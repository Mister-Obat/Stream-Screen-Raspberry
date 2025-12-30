import os
import json
import base64
from modules import rtsp_helper

# --- CONFIGURATION (Default) ---
DEFAULT_PORT = 5000
UDP_PORT = 5560
CONFIG_FILE = "stream_config.json"

# --- SECURITY ---
class SimpleCrypto:
    # Clé simple pour l'obfuscation locale (évite le clair textuel)
    KEY = "ObatStreamSecureKey2025" 
    
    @staticmethod
    def _xor(data, key):
        return ''.join(chr(ord(c) ^ ord(key[i % len(key)])) for i, c in enumerate(data))

    @staticmethod
    def encrypt(text):
        if not text: return ""
        try:
            xor_result = SimpleCrypto._xor(text, SimpleCrypto.KEY)
            return base64.b64encode(xor_result.encode()).decode()
        except: return text

    @staticmethod
    def decrypt(encoded):
        if not encoded: return ""
        try:
            decoded_xor = base64.b64decode(encoded).decode()
            return SimpleCrypto._xor(decoded_xor, SimpleCrypto.KEY)
        except: return ""

# --- GLOBAL STATE ---
class StreamState:
    def __init__(self):
        self.streaming = False  # Controls the capture loop
        self.monitor_idx = 0    # Default to Screen 0 (Index 0 in list)
        self.backend = "MSS"  # Default to MSS (CPU)
        self.codec_choice = "x264" # Default CPU
        self.encoder_preset = "fast" # fast/balanced/quality (fast=ultrafast for x264)
        self.latency_value = 10 # 0-100 (Low to High buffering)
        self.dropped_frames = 0
        self.loss_percent = 0.0 # Legacy/Combined (For Watchdog)
        self.loss_tcp = 0.0
        self.loss_rtsp = 0.0
        self.fps = 15
        self.quality = 50
        self.bitrate_mbps = 5.0 # Default 5 Mbps
        self.resolution = "480p" 
        self.target_w = 854
        self.target_h = 480
        
        # Audio Config
        self.audio_enabled = False
        self.audio_source = "Default"
        self.audio_bitrate = "128k"
        self.audio_volume = 1.0
        
        self.dxcam_mapping = {}
        
        # Compatibility Modes
        self.compatibility_mode = False # Raw TCP
        self.rtsp_mode = False # RTSP Server
        
        # RTSP Server Instance
        self.rtsp_server = rtsp_helper.RTSPServer()
        
        self.encoder = None # Expose for RTSP Extradata copying

        # Pi Config
        self.pi_ip = "192.168.0.XX"
        self.pi_user = "pi"
        self.pi_pass = "raspberry"
        self.pi_path = "Desktop/stream_receiver.py"
        
        # Remember Flags
        self.remember_ip = True
        self.remember_user = True
        self.remember_pass = True
        self.remember_path = True
        
        # Runtime Stats
        self.current_mbps = 0.0
        self.current_fps = 0
        
        # Watchdog
        self.watchdog_triggered = False
        self.pi_streaming = False # Track if Pi is theoretically running
        self.slow_cap_count = 0
        self.client_connected = False # TCP Client Status

    def save(self):
        data = {
            "monitor_idx": self.monitor_idx,
            "backend": self.backend,
            "codec_choice": self.codec_choice,
            "encoder_preset": self.encoder_preset,
            "latency_value": self.latency_value,
            "fps": self.fps,
            "quality": self.quality,
            "bitrate_mbps": self.bitrate_mbps,
            "resolution": self.resolution,
            
            # Audio
            "audio_enabled": self.audio_enabled,
            "audio_source": self.audio_source,
            "audio_bitrate": self.audio_bitrate,
            "audio_volume": self.audio_volume,
            
            
            # Save flags
            "remember_ip": self.remember_ip,
            "remember_user": self.remember_user,
            "remember_pass": self.remember_pass,
            "remember_path": self.remember_path,
            
            # Save data if remembered
            "pi_ip": self.pi_ip if self.remember_ip else "",
            "pi_user": self.pi_user if self.remember_user else "",
            "pi_path": self.pi_path if self.remember_path else ""
        }
        
        # Encrypt password if remembered
        if self.remember_pass and self.pi_pass:
            data["pi_pass_enc"] = SimpleCrypto.encrypt(self.pi_pass)
        else:
            data["pi_pass_enc"] = ""

        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f, indent=4)
        except: pass

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.monitor_idx = data.get("monitor_idx", 0)
                    self.backend = data.get("backend", "DXCam")
                    self.codec_choice = data.get("codec_choice", "auto")
                    self.encoder_preset = data.get("encoder_preset", "fast")
                    self.latency_value = data.get("latency_value", 20)
                    self.fps = data.get("fps", 60)
                    self.quality = data.get("quality", 50)
                    self.bitrate_mbps = data.get("bitrate_mbps", 4.0)
                    self.resolution = data.get("resolution", "720p")
                    
                    # Apply resolution dims (Restore target_w/h)
                    r = self.resolution
                    if r == "360p": self.target_w, self.target_h = 640, 360
                    elif r == "480p": self.target_w, self.target_h = 854, 480
                    elif r == "540p": self.target_w, self.target_h = 960, 540
                    elif r == "720p": self.target_w, self.target_h = 1280, 720
                    elif r == "900p": self.target_w, self.target_h = 1600, 900
                    elif r == "1080p": self.target_w, self.target_h = 1920, 1080
                    elif r == "1440p": self.target_w, self.target_h = 2560, 1440
                    elif r == "4K": self.target_w, self.target_h = 3840, 2160
                    elif r == "Native": self.target_w, self.target_h = 0, 0
                    
                    self.audio_enabled = data.get("audio_enabled", False)
                    self.audio_source = data.get("audio_source", "Default")
                    self.audio_bitrate = data.get("audio_bitrate", "128k")
                    self.audio_volume = data.get("audio_volume", 1.0)
                    
                    
                    self.remember_ip = data.get("remember_ip", True)
                    self.remember_user = data.get("remember_user", True)
                    self.remember_pass = data.get("remember_pass", True)
                    self.remember_path = data.get("remember_path", True)
                    
                    if self.remember_ip: self.pi_ip = data.get("pi_ip", "")
                    if self.remember_user: self.pi_user = data.get("pi_user", "pi")
                    if self.remember_path: self.pi_path = data.get("pi_path", "Desktop/stream_receiver.py")
                    
                    # Decrypt pass
                    enc_pass = data.get("pi_pass_enc", "")
                    if self.remember_pass and enc_pass:
                        self.pi_pass = SimpleCrypto.decrypt(enc_pass)
                    elif not self.remember_pass:
                        self.pi_pass = ""
            except: pass

# Global Singleton
state = StreamState()
state.load()
