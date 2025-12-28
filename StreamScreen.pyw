
import customtkinter as ctk
import tkinter as tk
import base64
from tkinter import messagebox
import socket
import dxcam
import cv2
import numpy as np
import struct
import threading
import time
import json
import logging
import mss
import paramiko
import os
import ctypes
from ctypes import windll, Structure, c_long, c_uint, c_void_p, byref, sizeof
from PIL import Image, ImageTk
import queue
from stream_encoder import VideoEncoder

# --- TASKBAR ICON PERSISTENCE (Windows) ---
# Création d'un ID unique basé sur le nom du fichier du script
# Cela permet à chaque app (ex: 'StreamScreen.pyw', 'Autre.pyw') d'avoir sa propre icône dans la barre des tâches
try:
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    myappid = f'obat.{script_name}.v1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("sender_debug.log", mode='w'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SenderGUI")

# --- CONFIGURATION (Default) ---
DEFAULT_PORT = 5555
UDP_PORT = 5555
CONFIG_FILE = "stream_config.json"

# --- GLOBAL STATE ---
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

# --- LOGGING GUI HANDLER ---
class TextHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self.enabled = True # Control Visibility

    def emit(self, record):
        if not self.enabled: return
        
        msg = self.format(record)
        def append():
            self.text_widget.configure(state="normal")
            self.text_widget.insert("end", msg + "\n")
            self.text_widget.see("end")
            self.text_widget.configure(state="disabled")
        try:
             # Schedule GUI update on main thread
             self.text_widget.after(0, append)
        except: pass

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
        self.loss_percent = 0.0
        self.fps = 15
        self.quality = 50
        self.bitrate_mbps = 5.0 # Default 5 Mbps
        self.resolution = "480p" 
        self.target_w = 854
        self.target_h = 480
        self.dxcam_mapping = {}
        
        # Pi Config
        self.pi_ip = "192.168.1.XX"
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

state = StreamState()
state.load()

# --- HELPER: STREAM BUFFER ---
# --- HELPER: STREAM BUFFER (FIFO Queue for H.264) ---
class StreamBuffer:
    def __init__(self, maxsize=200): # Reduced from 500
        self.q = queue.Queue(maxsize=maxsize)
        self.running = True

    def put(self, packet):
        if not self.running: return
        try:
            self.q.put_nowait(packet)
            return True
        except queue.Full:
            return False

    def clear(self):
        with self.q.mutex:
            self.q.queue.clear()
            
    def get(self):
        try:
            return self.q.get(timeout=1.0)
        except queue.Empty:
            return None

buffer_obj = StreamBuffer()

# --- HELPER: MONITOR DISCOVERY & MAPPING ---
def get_monitors():
    try:
        with mss.mss() as sct:
            mons = []
            # mss monitors: 0=All, 1=1st, 2=2nd...
            # We want to list individual monitors
            for i, m in enumerate(sct.monitors[1:]):
                mons.append(f"Ecran {i} ({m['width']}x{m['height']})")
            return mons
    except:
        return ["Ecran 0 (Defaut)"]

def map_dxcam_monitors():
    mapping = {}
    mss_geometries = []
    try:
        with mss.mss() as sct:
             for m in sct.monitors[1:]:
                 mss_geometries.append((m['width'], m['height']))
    except: return {}

    dxcam_geometries = []
    for i in range(10):
        try:
            cam = dxcam.create(output_idx=i, output_color="RGB")
            dxcam_geometries.append({'id': i, 'w': cam.width, 'h': cam.height})
            del cam
        except: break

    used_dxcam_ids = set()
    for gui_idx, (mw, mh) in enumerate(mss_geometries):
        match_id = -1
        # Exact match
        for d in dxcam_geometries:
            if d['id'] not in used_dxcam_ids and d['w'] == mw and d['h'] == mh:
                match_id = d['id']
                break
        # Fallback
        if match_id == -1:
            if gui_idx < len(dxcam_geometries) and gui_idx not in used_dxcam_ids:
                 match_id = dxcam_geometries[gui_idx]['id']
            elif len(dxcam_geometries) > len(used_dxcam_ids):
                 for d in dxcam_geometries:
                     if d['id'] not in used_dxcam_ids:
                         match_id = d['id']
                         break
        
        if match_id != -1:
            mapping[gui_idx] = match_id
            used_dxcam_ids.add(match_id)
        else:
            mapping[gui_idx] = gui_idx 
    return mapping

# --- CURSOR LOGIC ---
class CURSORINFO(Structure):
    _fields_ = [("cbSize", c_uint), ("flags", c_uint), ("hCursor", c_void_p), ("ptScreenPos", type('POINT', (Structure,), {'_fields_': [("x", c_long), ("y", c_long)]}))]

class POINT(Structure):
    _fields_ = [("x", c_long), ("y", c_long)]

def get_cursor_pos_fast():
    pt = POINT()
    windll.user32.GetCursorPos(byref(pt))
    return pt.x, pt.y

IDC_ARROW, IDC_HAND, IDC_IBEAM = 32512, 32649, 32513
def draw_cursor_arrow(img, x, y, scale=1.0):
    # Base coords for 720p/1080p roughly
    # Points: Tip(0,0), BottomLeft(0,16), Inner(4,13), TailBott(7,20), TailTop(10,19), InnerRight(6,12), Right(11,11)
    base_pts = [[0, 0], [0, 16], [4, 13], [7, 20], [10, 19], [6, 12], [11, 11]]
    
    # Scale points
    scaled_pts = []
    for px, py in base_pts:
        scaled_pts.append([x + int(px * scale), y + int(py * scale)])
        
    pts = np.array(scaled_pts, np.int32)
    pts = pts.reshape((-1, 1, 2))
    cv2.fillPoly(img, [pts], (255, 255, 255))
    cv2.polylines(img, [pts], True, (0, 0, 0), max(1, int(1 * scale)))

# --- THREADS (Updated for Real-time Config) ---
def sender_loop(sock):
    buffer_obj.running = True
    try:
        while state.streaming:
            packet = buffer_obj.get()
            if packet is None: continue 
            # Packet structure: [Size (4 bytes)] + [Data]
            # logger.info(f"Sending packet size: {len(packet)}") # DEBUG-FLOOD
            header = struct.pack(">L", len(packet))
            sock.sendall(header + packet)
    except Exception as e:
        logger.error(f"Sender Loop Error: {e}")

def stream_thread_func():
    logger.info("Stream Thread Started")
    
    # Init Backend Mapping
    state.dxcam_mapping = map_dxcam_monitors()
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Important for Latency: Disable Nagle
    server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        server.bind(('0.0.0.0', DEFAULT_PORT))
        server.listen(1)
        server.settimeout(15.0) # 15s Auto-Stop Timeout
    except Exception as e:
        logger.error(f"Bind Error: {e}")
        return

    # Beacon
    def beacon():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = f"STREAM_SERVER|{DEFAULT_PORT}".encode()
        while state.streaming:
            try: s.sendto(msg, ('<broadcast>', UDP_PORT))
            except: pass
            time.sleep(1.0)
    threading.Thread(target=beacon, daemon=True).start()

    conn = None
    dxcam_camera = None
    sct = None
    encoder = None
    
    current_backend = None
    current_codec_choice = None
    current_preset = None
    current_resolution = None
    current_mon_idx = -1
    current_fps = -1
    current_bitrate_mbps = -1.0
    
    # Monitor Geometry
    mon_left, mon_top, mon_width, mon_height = 0, 0, 1920, 1080
    
    # Stats Counter
    byte_count = 0
    frame_count = 0
    last_stat_time = time.time()
    
    # Deduplication
    last_frame_data = None
    last_cursor_pos = (-1, -1)
    
    # 1s Window Stats
    frames_total_sec = 0
    frames_dropped_sec = 0
    state.loss_percent = 0.0
    
    last_send_time = time.time()
    
    while state.streaming:
        # A. Connection
        if conn is None:
            try:
                conn, addr = server.accept()
                conn.settimeout(None)
                logger.info(f"Client connected: {addr}")
                buffer_obj.running = True
                threading.Thread(target=sender_loop, args=(conn,), daemon=True).start()
            except socket.timeout:
                # TIMEOUT: No client for 15s -> Auto Stop
                logger.warning("Auto-Stop: No client connected for 15s.")
                state.streaming = False
                # Note: The main thread loop will now exit
                break
            except:
                continue

        # B. Init/Re-init Capture & Encoder
        # Check if backend, monitor, fps, or bitrate changed
        if (current_backend != state.backend) or \
           (current_codec_choice != state.codec_choice) or \
           (current_preset != state.encoder_preset) or \
           (current_resolution != state.resolution) or \
           (current_mon_idx != state.monitor_idx) or \
           (current_fps != state.fps) or \
           (abs(current_bitrate_mbps - state.bitrate_mbps) > 0.1) or \
           (encoder is None):
            
            # Cleanup
            if dxcam_camera: dxcam_camera.stop(); dxcam_camera = None
            if sct: sct.close(); sct = None
            if encoder: encoder.close(); encoder = None
            
            current_backend = state.backend
            current_codec_choice = state.codec_choice
            current_preset = state.encoder_preset
            current_resolution = state.resolution
            current_mon_idx = state.monitor_idx
            current_fps = state.fps
            current_bitrate_mbps = state.bitrate_mbps
            
            # Clear Buffer to prevent lag/sync issues
            buffer_obj.clear()
            
            logger.info(f"Re-initializing Stream: {current_backend} | {current_fps} FPS | {current_bitrate_mbps:.1f} Mbps")
            
            # Geometry
            try:
                with mss.mss() as tmp_sct:
                    idx = state.monitor_idx + 1
                    if idx < len(tmp_sct.monitors):
                        m = tmp_sct.monitors[idx]
                        mon_left, mon_top = m["left"], m["top"]
                        mon_width, mon_height = m["width"], m["height"]
            except: pass
            
            # Init Encoder
            enc_w, enc_h = state.target_w, state.target_h
            if state.resolution == "Native" or enc_w <= 0:
                 enc_w, enc_h = mon_width, mon_height
            
            # Ensure even dimensions (required for some codecs)
            if enc_w % 2 != 0: enc_w -= 1
            if enc_h % 2 != 0: enc_h -= 1
            
            # Bitrate configuration (User controlled)
            # Convert Mbps to bps
            bitrate_bps = int(state.bitrate_mbps * 1000 * 1000)
            
            logger.info(f"[ENCODER INIT] Res: {enc_w}x{enc_h} | FPS: {state.fps} | Bitrate: {state.bitrate_mbps}M | Codec: {state.codec_choice} | Preset: {state.encoder_preset}")
            encoder = VideoEncoder(enc_w, enc_h, state.fps, bitrate_bps, codec_choice=state.codec_choice, preset_choice=state.encoder_preset)
            
            # Init Capture
            if current_backend == "DXCam":
                try:
                    t_idx = state.dxcam_mapping.get(state.monitor_idx, state.monitor_idx)
                    dxcam_camera = dxcam.create(output_idx=t_idx, output_color="RGB")
                    dxcam_camera.start(target_fps=state.fps, video_mode=True)
                except: current_backend = "MSS" # Fallback

            if current_backend == "MSS":
                sct = mss.mss()

        # C. Capture & Process
        t_start = time.time()
        frame_ready = False
        frame_bgr = None
        
        try:
            # T1: Capture
            t1 = time.time()
            if current_backend == "DXCam" and dxcam_camera:
                raw = dxcam_camera.get_latest_frame()
                if raw is not None:
                    frame_bgr = cv2.cvtColor(raw, cv2.COLOR_RGB2BGR)
                    frame_ready = True
            elif current_backend == "MSS" and sct:
                mon_id = state.monitor_idx + 1
                if mon_id < len(sct.monitors):
                    img = sct.grab(sct.monitors[mon_id])
                    frame_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_BGRA2BGR)
                    frame_ready = True
            t2 = time.time()

            if frame_ready and frame_bgr is not None:
                # Resize
                h, w = frame_bgr.shape[:2]
                tw, th = encoder.width, encoder.height
                
                if (w != tw or h != th):
                    frame_bgr = cv2.resize(frame_bgr, (tw, th))
                
                # Cursor
                mx, my = get_cursor_pos_fast()
                rx = int((mx - mon_left) * tw / w) if w else -100
                ry = int((my - mon_top) * th / h) if h else -100
                
                # Scale cursor relative to 720p (User preference)
                # If res is 360p (h=360) -> scale = 0.5
                # If res is 1080p (h=1080) -> scale = 1.5
                cursor_scale = th / 720.0 
                # Clamp min size so it doesn't disappear
                cursor_scale = max(0.5, cursor_scale)

                # DEDUPLICATION CHECK
                # Need to check if FRAME changed OR CURSOR changed
                # Fast check on cursor first
                cursor_changed = (mx != last_cursor_pos[0] or my != last_cursor_pos[1])
                
                # We can't easily check 'frame changed' BEFORE drawing cursor if we draw ON the frame.
                # So verify frame content (without cursor) first.
                frame_changed = False
                if last_frame_data is None:
                    frame_changed = True
                else:
                    # np.array_equal is robust but can be slow. 
                    # Optimization: Check pure bytes if possible or just rely on numpy C-speed.
                    if not np.array_equal(frame_bgr, last_frame_data):
                        frame_changed = True
                
                if not frame_changed and not cursor_changed:
                    # Nothing moved! 
                    # prevent connection timeout (Heartbeat) - Send at least every 0.5s
                    if time.time() - last_send_time < 0.5:
                        # Sleep slightly to prevent CPU spin
                        time.sleep(0.01)
                        continue

                # Update Last State
                last_frame_data = frame_bgr.copy() # Copy is needed as frame_bgr is mutable
                last_cursor_pos = (mx, my)
                last_send_time = time.time()
                
                if 0 <= rx < tw and 0 <= ry < th:
                    draw_cursor_arrow(frame_bgr, rx, ry, scale=cursor_scale)
                
                t3 = time.time()

                # ENCODE (H.264)
                packets = encoder.encode(frame_bgr)
                t4 = time.time()
                
                if not packets:
                   # logger.warning("Encoder returned no packets!")
                   pass

                # Smart Buffer Management
                # Latency Formula: Queue Size / Packets_Per_frame / FPS
                # User Latency Setting: 1...100
                # Min threshold (Realtime): 5 packets
                # Max threshold (Stability): 500 packets (was old max)
                user_threshold = max(5, int(state.latency_value * 5))
                
                frames_total_sec += 1
                q_size = buffer_obj.q.qsize()
                if q_size > user_threshold: 
                    # Drop frame
                    state.dropped_frames += 1
                    frames_dropped_sec += 1
                    # RATE LIMIT LOGGING (Prevent Spam)
                    # Use dropped count or time to limit to ~1 log/sec
                    if state.dropped_frames % state.fps == 0: 
                         logger.warning(f"Latency Prevented! Dropping frame (Q:{q_size} > {user_threshold})")
                else:
                    # Send Packets
                    for pkt in packets:
                        if not buffer_obj.put(pkt):
                            logger.warning("Buffer FULL! Packet lost.")
                            
                    byte_count += sum(len(p) for p in packets)
                
                frame_count += 1
                
                # Update Stats
                t_now = time.time()
                if t_now - last_stat_time >= 1.0:
                    state.current_fps = frame_count
                    state.current_mbps = (byte_count * 8) / (1000 * 1000)
                    
                    # Calc Loss %
                    if frames_total_sec > 0:
                         state.loss_percent = (frames_dropped_sec / frames_total_sec) * 100.0
                    else:
                         state.loss_percent = 0.0
                         
                    # Reset Window
                    frames_total_sec = 0
                    frames_dropped_sec = 0

                    # logger.info(f"Stats: {state.current_fps} FPS, {state.current_mbps:.2f} Mbps, Queue Size: {buffer_obj.q.qsize()}")
                    # Profiling Log
                    cap_ms = (t2 - t1) * 1000
                    proc_ms = (t3 - t2) * 1000
                    enc_ms = (t4 - t3) * 1000
                    total_ms = (t4 - t1) * 1000
                    logger.info(f"FPS:{state.current_fps} | Mbps:{state.current_mbps:.1f} | Q:{buffer_obj.q.qsize()} | Loss:{state.loss_percent:.1f}% | Times(ms) Cap:{cap_ms:.1f} Proc:{proc_ms:.1f} Enc:{enc_ms:.1f} Tot:{total_ms:.1f}")
                    
                    byte_count = 0
                    frame_count = 0
                    last_stat_time = t_now
                    
        except Exception as e:
            logger.error(f"Stream Loop Error: {e}")
            pass
        
        # FPS Cap
        dt = time.time() - t_start
        target_dt = 1.0 / state.fps
        if dt < target_dt: time.sleep(target_dt - dt)

    # Cleanup when loop ends
    buffer_obj.running = False
    if conn: conn.close()
    if dxcam_camera: dxcam_camera.stop()
    if encoder: encoder.close()
    server.close()
    logger.info("Stream Thread Stopped")

# --- GUI ---
class StreamApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Setup
        self.title("Stream Screen")
        self.geometry("450x790")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        # Icon
        # Icon
        try:
            icon_path = "stream4.ico"
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception as e:
            logger.error(f"Failed to load icon: {e}")
        
        # Header
        self.lbl_title = ctk.CTkLabel(self, text="Stream Screen", font=("Roboto", 24, "bold"))
        self.lbl_title.pack(pady=20)
        
        # --- TAB VIEW ---
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.tab_stream = self.tabs.add("Stream")
        self.tab_pi = self.tabs.add("Raspberry Pi")
        self.tab_console = self.tabs.add("Console")
        
        # === TAB: CONSOLE ===
        self.frm_cons_ctrl = ctk.CTkFrame(self.tab_console, fg_color="transparent")
        self.frm_cons_ctrl.pack(fill="x", padx=10, pady=5)
        
        self.sw_console_mode = ctk.CTkSwitch(self.frm_cons_ctrl, text="Voir Console Pi (SSH)", command=self.toggle_console_mode)
        self.sw_console_mode.pack(side="left")
        
        # Console Output
        self.txt_console = ctk.CTkTextbox(self.tab_console, font=("Consolas", 10), activate_scrollbars=True)
        self.txt_console.pack(fill="both", expand=True, padx=10, pady=5)
        self.txt_console.configure(state="disabled")
        
        # Setup Local Logging to Console
        self.text_handler = TextHandler(self.txt_console)
        # Formatter
        self.text_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(self.text_handler)
        
        # === TAB: STREAM ===
        
        # 1. Source
        self.frm_src = ctk.CTkFrame(self.tab_stream)
        self.frm_src.pack(fill="x", pady=5)
        
        ctk.CTkLabel(self.frm_src, text="Écran Source:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        self.mons = get_monitors()
        self.opt_mon = ctk.CTkOptionMenu(self.frm_src, values=self.mons, command=self.on_mon_change)
        self.opt_mon.pack(fill="x", padx=10, pady=5)
        # Set default
        if state.monitor_idx < len(self.mons):
            self.opt_mon.set(self.mons[state.monitor_idx])
            
        # 2. Architecture (Engine)
        self.frm_mode = ctk.CTkFrame(self.tab_stream)
        self.frm_mode.pack(fill="x", pady=5)
        ctk.CTkLabel(self.frm_mode, text="Architecture:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        
        self.opt_engine = ctk.CTkOptionMenu(self.frm_mode, values=["NVIDIA / GPU (Rapide)", "Processeur / CPU (Compatible)"], command=self.on_engine_change)
        self.opt_engine.pack(fill="x", padx=10, pady=5)
        
        # Determine initial selection
        if state.backend == "DXCam" and state.codec_choice in ["auto", "nvenc"]:
             self.opt_engine.set("NVIDIA / GPU (Rapide)")
        else:
             self.opt_engine.set("Processeur / CPU (Compatible)")
             
        # Preset
        ctk.CTkLabel(self.frm_mode, text="Préréglage (Vitesse/Qualité):", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        self.opt_preset = ctk.CTkOptionMenu(self.frm_mode, command=self.on_preset_change)
        self.opt_preset.pack(fill="x", padx=10, pady=5)
        self.update_preset_options()

        # 3. Settings (FPS/Bitrate/Res)
        self.frm_set = ctk.CTkFrame(self.tab_stream)
        self.frm_set.pack(fill="x", pady=5)
        
        # FPS
        self.lbl_fps_val = ctk.CTkLabel(self.frm_set, text=f"FPS Cible: {state.fps}")
        self.lbl_fps_val.pack(anchor="w", padx=10)
        self.sld_fps = ctk.CTkSlider(self.frm_set, from_=5, to=120, number_of_steps=115, command=self.on_fps_change)
        self.sld_fps.set(state.fps)
        self.sld_fps.pack(fill="x", padx=10, pady=5)
        
        # Bitrate (Mbps)
        self.lbl_bit_val = ctk.CTkLabel(self.frm_set, text=f"Bitrate: {state.bitrate_mbps:.1f} Mbps")
        self.lbl_bit_val.pack(anchor="w", padx=10)
        
        # Logarithmic or linear? Linear 0.1 to 25.0
        self.sld_bit = ctk.CTkSlider(self.frm_set, from_=0.1, to=25.0, number_of_steps=249, command=self.on_bitrate_change)
        self.sld_bit.set(state.bitrate_mbps)
        self.sld_bit.pack(fill="x", padx=10, pady=5)

        # Latency Slider
        self.lbl_lat = ctk.CTkLabel(self.frm_set, text=f"Latence vs Stabilité: {state.latency_value}%")
        self.lbl_lat.pack(anchor="w", padx=10)
        self.sld_lat = ctk.CTkSlider(self.frm_set, from_=1, to=100, number_of_steps=99, command=self.on_latency_change)
        self.sld_lat.set(state.latency_value)
        self.sld_lat.pack(fill="x", padx=10, pady=5)
        
        # Res
        ctk.CTkLabel(self.frm_set, text="Résolution de Sortie:").pack(anchor="w", padx=10)
        self.opt_res = ctk.CTkOptionMenu(self.frm_set, values=[
            "Native", "360p", "480p", "540p", "720p", "900p", "1080p", "1440p", "4K"
        ], command=self.on_res_change)
        self.opt_res.pack(fill="x", padx=10, pady=10)
        self.opt_res.set(state.resolution)

        # 4. BIG BUTTON
        self.btn_start = ctk.CTkButton(self.tab_stream, text="LANCER LE FLUX", 
                                       font=("Arial", 18, "bold"), 
                                       height=50, 
                                       fg_color="green", 
                                       hover_color="darkgreen",
                                       command=self.toggle_stream)
        self.btn_start.pack(fill="x", padx=10, pady=(20, 5))

        # Checkbox for Local Auto-Restart
        self.chk_auto_local = ctk.CTkCheckBox(self.tab_stream, text="Relancer automatiquement si perte signal (>10s)")
        self.chk_auto_local.pack(anchor="w", padx=10, pady=(5, 20))

        
        # === TAB: PI ===
        ctk.CTkLabel(self.tab_pi, text="Configuration SSH", font=("Arial", 14, "bold")).pack(pady=10)
        
        # IP
        frm_ip = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_ip.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frm_ip, text="Adresse ipv4 eth0").pack(side="left")
        self.chk_ip = ctk.CTkCheckBox(frm_ip, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_ip.pack(side="right")
        if state.remember_ip: self.chk_ip.select()
        else: self.chk_ip.deselect()
        
        self.ent_ip = ctk.CTkEntry(self.tab_pi, placeholder_text="IP du Raspberry Pi")
        self.ent_ip.pack(fill="x", padx=10, pady=5)
        if state.pi_ip: self.ent_ip.insert(0, state.pi_ip)
        
        # User
        frm_user = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_user.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frm_user, text="id de connexion").pack(side="left")
        self.chk_user = ctk.CTkCheckBox(frm_user, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_user.pack(side="right")
        if state.remember_user: self.chk_user.select()
        else: self.chk_user.deselect()
        
        self.ent_user = ctk.CTkEntry(self.tab_pi, placeholder_text="Utilisateur (ex: pi)")
        self.ent_user.pack(fill="x", padx=10, pady=5)
        self.ent_user.insert(0, state.pi_user)

        # Pass
        frm_pass = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_pass.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frm_pass, text="mot de passe").pack(side="left")
        self.chk_pass = ctk.CTkCheckBox(frm_pass, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_pass.pack(side="right")
        if state.remember_pass: self.chk_pass.select()
        else: self.chk_pass.deselect()

        self.ent_pass = ctk.CTkEntry(self.tab_pi, placeholder_text="Mot de passe", show="*")
        self.ent_pass.pack(fill="x", padx=10, pady=5)
        self.ent_pass.insert(0, state.pi_pass)
        
        # Path
        frm_path = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_path.pack(fill="x", padx=10, pady=(10,0))
        ctk.CTkLabel(frm_path, text="Chemin fichier python :").pack(side="left")
        self.chk_path = ctk.CTkCheckBox(frm_path, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_path.pack(side="right")
        if state.remember_path: self.chk_path.select()
        else: self.chk_path.deselect()

        self.ent_path = ctk.CTkEntry(self.tab_pi, placeholder_text="ex: Desktop/stream_receiver.py")
        self.ent_path.pack(fill="x", padx=10, pady=5)
        self.ent_path.insert(0, state.pi_path)
        
        # Buttons Frame
        self.frm_pi_btns = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        self.frm_pi_btns.pack(fill="x", padx=5, pady=20)
        
        self.btn_pi = ctk.CTkButton(self.frm_pi_btns, text="Lancer", 
                                    height=40,
                                    fg_color="#D63384",
                                    hover_color="#A81D62",
                                    command=self.launch_pi)
        self.btn_pi.pack(side="left", fill="x", expand=True, padx=5)

        self.btn_stop_pi = ctk.CTkButton(self.frm_pi_btns, text="Stopper", 
                                    height=40,
                                    fg_color="#DC3545",
                                    hover_color="#A71D2A",
                                    command=self.stop_pi_manual)
        self.btn_stop_pi.pack(side="left", fill="x", expand=True, padx=5)
        
        # Checkbox for Pi Auto-Restart
        self.chk_auto_pi = ctk.CTkCheckBox(self.tab_pi, text="Relancer automatiquement si perte signal (>10s)")
        self.chk_auto_pi.pack(anchor="w", padx=10, pady=5)


        self.btn_update_pi = ctk.CTkButton(self.tab_pi, text="Mettre à jour le Receiver (SFTP)", 
                                           height=30,
                                           fg_color="#0D6EFD",
                                           hover_color="#0B5ED7",
                                           command=self.update_pi)
        self.btn_update_pi.pack(fill="x", padx=10, pady=(0, 20))
        
        # Footer
        self.frm_footer = ctk.CTkFrame(self, fg_color="transparent")
        self.frm_footer.pack(side="bottom", fill="x", padx=10, pady=5)
        
        self.lbl_status = ctk.CTkLabel(self.frm_footer, text="Status: Prêt", text_color="gray")
        self.lbl_status.pack(side="left")
        
        self.lbl_drop = ctk.CTkLabel(self.frm_footer, text="Perte: 0%", text_color="gray")
        self.lbl_drop.pack(side="right")
        
        # Start Monitor Loop
        # Start Monitor Loop
        self.watchdog_loss_counter = 0
        self.watchdog_cooldown_until = 0
        self.monitor_stats()

    def monitor_stats(self):
        if state.streaming:
            msg = f"Status: En Ligne | {state.current_fps} FPS | {state.current_mbps:.2f} Mbps"
            color = "green"
        else:
            msg = "Status: Arrêté"
            color = "gray"
            
        if self.btn_start.cget("text") == "STOPPER LE FLUX": # Only update if running to avoid overriding connection messages
             self.lbl_status.configure(text=msg, text_color=color)
        
        self.after(500, self.monitor_stats)
    
    def on_mon_change(self, choice):
        idx = self.mons.index(choice)
        state.monitor_idx = idx
        self.save_config()

    def on_engine_change(self, choice):
        if "NVIDIA" in choice:
            state.backend = "DXCam"
            state.codec_choice = "auto" # Auto prefers NVENC
        else:
            state.backend = "MSS"
            state.codec_choice = "x264"
        self.update_preset_options()
        self.save_config()
        
    def update_preset_options(self):
        if state.codec_choice == "x264" or state.backend == "MSS":
            # X264 Options
            self.opt_preset.configure(values=["Performance (Ultrafast)", "Equilibré (Superfast)", "Qualité (Veryfast)"])
            # Map internal to display
            val = "Performance (Ultrafast)"
            if state.encoder_preset == "balanced": val = "Equilibré (Superfast)"
            elif state.encoder_preset == "quality": val = "Qualité (Veryfast)"
            self.opt_preset.set(val)
        else:
            # NVENC Options
            self.opt_preset.configure(values=["Performance (P1 - Latence Min)", "Equilibré (P3)", "Qualité (P4)"])
             # Map internal to display
            val = "Performance (P1 - Latence Min)"
            if state.encoder_preset == "balanced": val = "Equilibré (P3)"
            elif state.encoder_preset == "quality": val = "Qualité (P4)"
            self.opt_preset.set(val)
            
    def on_preset_change(self, choice):
        if "Performance" in choice: state.encoder_preset = "fast"
        elif "Equilibré" in choice: state.encoder_preset = "balanced"
        elif "Qualité" in choice: state.encoder_preset = "quality"
        self.save_config()

    def on_fps_change(self, val):
        state.fps = int(val)
        self.lbl_fps_val.configure(text=f"FPS Cible: {state.fps}")
        # Note: stream_thread will pick this up automatically now
        self.save_config()

    def on_bitrate_change(self, val):
        state.bitrate_mbps = float(val)
        self.lbl_bit_val.configure(text=f"Bitrate: {state.bitrate_mbps:.1f} Mbps")
        self.save_config()

    def on_latency_change(self, val):
        state.latency_value = int(val)
        self.lbl_lat.configure(text=f"Latence vs Stabilité: {state.latency_value}%")
        self.save_config()

    def on_res_change(self, choice):
        state.resolution = choice
        if choice == "360p": state.target_w, state.target_h = 640, 360
        elif choice == "480p": state.target_w, state.target_h = 854, 480
        elif choice == "540p": state.target_w, state.target_h = 960, 540
        elif choice == "720p": state.target_w, state.target_h = 1280, 720
        elif choice == "900p": state.target_w, state.target_h = 1600, 900
        elif choice == "1080p": state.target_w, state.target_h = 1920, 1080
        elif choice == "1440p": state.target_w, state.target_h = 2560, 1440
        elif choice == "4K": state.target_w, state.target_h = 3840, 2160
        elif choice == "Native": state.target_w, state.target_h = 0, 0
        logger.info(f"Resolution Changed to: {choice} ({state.target_w}x{state.target_h})")
        self.save_config()

    def save_config(self):
        state.pi_ip = self.ent_ip.get()
        state.pi_user = self.ent_user.get()
        state.pi_pass = self.ent_pass.get()
        state.pi_path = self.ent_path.get()
        
        state.remember_ip = bool(self.chk_ip.get())
        state.remember_user = bool(self.chk_user.get())
        state.remember_pass = bool(self.chk_pass.get())
        state.remember_path = bool(self.chk_path.get())
        
        state.save()

    def toggle_stream(self):
        if not state.streaming:
            # START
            state.streaming = True
            self.th = threading.Thread(target=stream_thread_func, daemon=True)
            self.th.start()
            self.btn_start.configure(text="STOPPER LE FLUX", fg_color="red", hover_color="darkred")
            self.lbl_status.configure(text="Status: Streaming en cours...", text_color="green")
        else:
            # STOP
            state.streaming = False
            self.btn_start.configure(text="LANCER LE FLUX", fg_color="green", hover_color="darkgreen")
            self.lbl_status.configure(text="Status: Arrêté", text_color="gray")

        # Start Metric Update Loop
        self.update_metrics()
        
    def update_metrics(self):
        if state.streaming:
            # Update Drop Count (Percent)
            pct = state.loss_percent
            col = "green"
            if pct > 1.0: col = "orange" 
            if pct > 10.0: col = "red"
            self.lbl_drop.configure(text=f"Perte: {pct:.0f}%", text_color=col)
            
            # --- WATCHDOG LOGIC ---
            # 0. Check Cooldown
            if time.time() < self.watchdog_cooldown_until:
                remaining = int(self.watchdog_cooldown_until - time.time())
                if remaining % 5 == 0: logger.info(f"Watchdog: Cooldown ({remaining}s)...")
                # Reset counter during cooldown to be safe
                self.watchdog_loss_counter = 0
            
            # 1. Check for high loss (indicating dead receiver)
            # Only trigger if queue buffer is filling up (> 10 items) AND loss is high.
            # Q size check prevents triggering on simple network lag where packets are still sent but ack is slow (TCP)
            # Actually, loss_percent is calculated based on DROPPED bytes (buffer full).
            # So if loss is 100%, it means we are dropping everything aka buffer is full aka receiver is not reading.
            elif pct >= 99.0:
                self.watchdog_loss_counter += 1
                
                # Dynamic Threshold
                # If Auto-Restart is ON (Pi or Local): Trigger fast (10s) to retry.
                # If Auto-Restart is OFF: Wait longer (60s) before giving up and stopping.
                threshold = 60
                if self.chk_auto_pi.get() or self.chk_auto_local.get():
                    threshold = 10
                
                if self.watchdog_loss_counter % 5 == 0: 
                    logger.warning(f"Watchdog: Perte signal détectée ({self.watchdog_loss_counter}/{threshold}s)")
                
                if self.watchdog_loss_counter >= threshold:
                    logger.error(f"Watchdog: TRIGGERED ({threshold}s Loss)")
                    self.watchdog_loss_counter = 0 # Reset
                    
                    # ACTION PRIORITY:
                    # 1. Restart Pi (if checked) -> Cooldown 30s (Pi Reboot time)
                    # 2. Restart Local (if checked) -> Cooldown 10s (Fast Retry)
                    # 3. Just Stop -> No Cooldown
                    
                    if self.chk_auto_pi.get():
                        logger.info("Watchdog Action: Restarting Pi...")
                        self.watchdog_cooldown_until = time.time() + 30
                        self.restart_pi()
                    elif self.chk_auto_local.get():
                        logger.info("Watchdog Action: Restarting Local...")
                        self.watchdog_cooldown_until = time.time() + 10
                        self.restart_local()
                    else:
                        logger.info("Watchdog Action: Stopping Stream...")
                        self.toggle_stream()
            else:
                 self.watchdog_loss_counter = 0

        else:
             self.watchdog_loss_counter = 0
             # Stream stopped (Auto-stop or other), reset UI
             if self.btn_start.cget("text") == "STOPPER LE FLUX":
                 self.toggle_stream() # Logic handles UI reset
            
        self.after(1000, self.update_metrics)


    # --- SSH HELPER ---
    def _create_ssh_client(self, ip, user, pwd):
        """
        Creates and connects an SSH Client with robust settings.
        Forces password auth to avoid key/agent issues.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # INCREASED TIMEOUTS & FORCE PASSWORD
        # banner_timeout=30: Helps if server is slow to greet
        # timeout=10: Connection timeout
        # look_for_keys=False: Don't try local keys (avoids auth failure if server wants pwd)
        # allow_agent=False: Don't use SSH agent
        client.connect(ip, username=user, password=pwd if pwd else None, 
                       timeout=10, banner_timeout=30, 
                       look_for_keys=False, allow_agent=False)
        return client

    def launch_pi(self):
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        path = self.ent_path.get()
        
        if not ip or not user:
            messagebox.showerror("Erreur", "IP et Utilisateur requis!")
            print("ERROR: Missing IP or User")
            return
        
        self.save_config()

        # AUTO-START STREAM IF NEEDED
        if not state.streaming:
            logger.info("Auto-starting Stream for Pi...")
            self.toggle_stream()
            # Wait a moment for server to bind
            time.sleep(1.0)
        
        
        def ssh_task():
            try:
                # 1. Get Local IP to help Receiver
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.settimeout(0.1)
                try:
                    # Connect to the Pi IP to get the correct interface IP
                    s.connect((ip, 22)) 
                    local_ip = s.getsockname()[0]
                except: local_ip = ""
                finally: s.close()

                # 2. Prepare Command
                # Robust command construction
                # Use ~/.Xauthority to be user-agnostic
                # ADD LOG REDIRECTION for Remote Console
                base_cmd = f"export DISPLAY=:0 && export XAUTHORITY=~/.Xauthority && python3 -u {path} > stream_receiver.log 2>&1 &"
                # Note: 'python3 -u' forces unbuffered output so logs appear instantly
                
                final_cmd = base_cmd
                # If command is python script, append IP (Wait, args go before redirection)
                # Correct pattern: python3 -u script.py ARG > log 2>&1 &
                if path.endswith(".py") and local_ip:
                     final_cmd = f"export DISPLAY=:0 && export XAUTHORITY=~/.Xauthority && python3 -u {path} {local_ip} > stream_receiver.log 2>&1 &"
                
                logger.info(f"SSH Connecting to {ip}...")
                self.lbl_status.configure(text="SSH: Connexion...", text_color="orange")
                
                # USE HELPER
                client = self._create_ssh_client(ip, user, pwd)
                
                logger.info(f"SSH Executing: {final_cmd}")
                self.lbl_status.configure(text="SSH: Exécution...", text_color="blue")
                
                stdin, stdout, stderr = client.exec_command(final_cmd, get_pty=False) # No PTY for background task
                
                # Check for immediate errors (waiting a bit)
                time.sleep(1.0)
                
                if stdout.channel.recv_ready():
                    out = stdout.channel.recv(1024).decode().strip()
                    if out: logger.info(f"SSH Check: {out}")
                    if "Error" in out or "found" in out or "denied" in out:
                         # Safe UI Update
                         self.after(0, lambda: messagebox.showerror("Erreur SSH", f"Retour: {out}"))
                         self.lbl_status.configure(text="SSH: Erreur (voir logs)", text_color="red")
                         client.close()
                         return

                self.lbl_status.configure(text="SSH: Lancé avec succès!", text_color="green")
                # Warning: Closing client kills the process if not persistent. 
                # Keeping it open or letting it detach is tricky. 
                # For now, we keep the object but the thread ends. The GC might close it.
                # Let's detach properly or keep log open?
                # User wants 'simple'.
                # Use nohup trick if we want to close connection
                # But 'exec_command' blocks until channel closed? No, it returns streams.
                
            except Exception as e:
                logger.error(f"SSH Fail: {e}")
                self.lbl_status.configure(text="SSH Erreur!", text_color="red")
                self.after(0, lambda: messagebox.showerror("Erreur Connexion", str(e)))
        
        threading.Thread(target=ssh_task, daemon=True).start()

    def stop_pi_manual(self):
         # Always silent for success, errors still show if silent=False (default behavior kept for errors but success silenced manually in function)
         # Actually, user said "totalement supprimer toutes les popups". 
         # So we pass silent=True to be sure? 
         # Wait, if I pass silent=True, errors are also hidden. User likely wants to see errors.
         # But wants "Processus arrêté" popup gone.
         # I commented out the success popup above, so silent=False is fine for errors.
         self.stop_pi(silent=False)

    def stop_pi(self, silent=False):
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        
        if not ip or not user:
             if not silent: messagebox.showerror("Erreur", "IP et Utilisateur requis!")
             return

        def task():
            try:
                self.lbl_status.configure(text="SSH: Arrêt en cours...", text_color="orange")
                client = self._create_ssh_client(ip, user, pwd)
                
                # Kill all python instances of stream_receiver
                # Using pkill -f to match full command line
                cmd = "pkill -f stream_receiver.py"
                logger.info(f"SSH Stop: {cmd}")
                
                client.exec_command(cmd)
                time.sleep(0.5)
                client.close()
                
                self.lbl_status.configure(text="SSH: Processus stoppé.", text_color="green")
                # if not silent: messagebox.showinfo("Succès", "Processus arrêté sur le Raspberry Pi.")
            except Exception as e:
                logger.error(f"SSH Stop Error: {e}")
                self.lbl_status.configure(text="SSH: Erreur Stop", text_color="red")
                if not silent: 
                    self.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def update_pi(self):
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        remote_path = self.ent_path.get() # e.g. Desktop/stream_receiver.py
        
        if not ip or not user: return messagebox.showerror("Erreur", "IP et User requis")
        
        # Local File
        local_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stream_receiver.py")
        if not os.path.exists(local_file):
            return messagebox.showerror("Erreur", f"Fichier local introuvable:\n{local_file}")
            
        def task():
            try:
                self.lbl_status.configure(text="SFTP: Connexion...", text_color="orange")
                client = self._create_ssh_client(ip, user, pwd)
                
                sftp = client.open_sftp()
                
                # Resolve Home dir if path is relative
                # Paramiko SFTP starts at user home usually, ensuring it.
                # If remote path is absolute, it's fine. If relative, it's relative to home.
                
                target_path = remote_path
                # Basic check for empty path
                if not target_path or target_path == ".": target_path = "stream_receiver.py"
                
                self.lbl_status.configure(text="SFTP: Upload...", text_color="blue")
                logger.info(f"Uploading {local_file} -> {target_path}")
                
                sftp.put(local_file, target_path)
                sftp.close()
                client.close()
                
                self.lbl_status.configure(text="SFTP: Succès!", text_color="green")
                self.after(0, lambda: messagebox.showinfo("Mise à jour", f"Fichier envoyé avec succès vers:\n{target_path}"))
                
            except Exception as e:
                logger.error(f"SFTP Error: {e}")
                self.lbl_status.configure(text="SFTP: Echec", text_color="red")
                self.after(0, lambda: messagebox.showerror("Erreur Upload", str(e)))
                
        threading.Thread(target=task, daemon=True).start()

    def restart_local(self):
        def task():
            if state.streaming:
                logger.info("Restart Local: Stopping...")
                self.toggle_stream() # Disables streaming
                time.sleep(2.0)
            
            logger.info("Restart Local: Starting...")
            self.toggle_stream() # Enables streaming
        
        threading.Thread(target=task, daemon=True).start()

    def restart_pi(self):
        # 1. Stop (Silent)
        self.stop_pi(silent=True)
        
        # 2. Wait & Start
        def task():
            # Reset Local Stream to clear buffer!
            if state.streaming:
                logger.info("Watchdog Action: Resetting Local Stream for clean Start...")
                # Must call on main thread UI safe? 
                # toggle_stream modifies UI labels, so yes.
                # using .after is safer or just call it if we are confident (it uses .configure)
                # But we are in a thread here? No, restart_pi is called from watchdog (main thread callback in update_metrics)
                # Wait, update_metrics uses self.after, so it runs on main thread.
                # So we can call toggle_stream directly here.
                self.toggle_stream() # Stop
            
            self.lbl_status.configure(text="SSH: Redémarrage (Attente 5s)...", text_color="orange")
            time.sleep(5.0)
            
            # launch_pi will auto-start stream if needed
            # But better to be explicit or let launch_pi handle it?
            # launch_pi has: if not state.streaming: toggle_stream()
            # So stopping above is perfect.
            
            # We must use self.after to call launch_pi if we want to be 100% thread safe, 
            # but launch_pi starts a thread itself.
            # However, this task runs in a thread.
            # So calling launch_pi (which gets UI values) might be risky?
            # launch_pi > self.ent_ip.get() (Safe-ish on Tkinter?)
            # Usually 'get' is safe.
            
            # To be absolutely safe, let's schedule launch_pi on main thread
            self.after(0, self.launch_pi)
            
        threading.Thread(target=task, daemon=True).start()

    def toggle_console_mode(self):
        # Clear Console
        self.txt_console.configure(state="normal")
        self.txt_console.delete("1.0", "end")
        self.txt_console.configure(state="disabled")
        
        # Local or Remote?
        if self.sw_console_mode.get() == 1:
            # Remote Mode: Disable Local Logs
            self.text_handler.enabled = False
            self.start_remote_log()
        else:
            # Local Mode: Enable Local Logs
            self.stop_remote_log()
            self.text_handler.enabled = True
             # Re-print local status
            logger.info("Console Mode: LOCAL")
            
    def start_remote_log(self):
        self.remote_log_running = True
        
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        
        if not ip or not user:
             self.txt_console.configure(state="normal")
             self.txt_console.insert("end", "\n[GUI] ERREUR: Configurer IP/User dans l'onglet Raspberry Pi d'abord.\n")
             self.txt_console.configure(state="disabled")
             self.sw_console_mode.deselect()
             return

        def task():
            client = None
            try:
                self.txt_console.configure(state="normal")
                self.txt_console.insert("end", f"\n[GUI] Connexion SSH log ({ip})...\n")
                self.txt_console.configure(state="disabled")
                
                client = self._create_ssh_client(ip, user, pwd)
                
                stdin, stdout, stderr = client.exec_command("tail -f stream_receiver.log")
                
                # Stream logs
                while self.remote_log_running and self.sw_console_mode.get() == 1:
                    if stdout.channel.recv_ready():
                        line = stdout.channel.recv(1024).decode(errors='replace')
                        if line:
                             # Append to GUI safely
                             def append(txt):
                                 self.txt_console.configure(state="normal")
                                 self.txt_console.insert("end", txt) # Already has newlines usually
                                 self.txt_console.see("end")
                                 self.txt_console.configure(state="disabled")
                             self.txt_console.after(0, append, line)
                    else:
                        time.sleep(0.1)
                        if stdout.channel.exit_status_ready(): break
                        
            except Exception as e:
                def err_msg(e):
                     self.txt_console.configure(state="normal")
                     self.txt_console.insert("end", f"\n[GUI] Erreur SSH Log: {e}\n")
                     self.txt_console.configure(state="disabled")
                self.txt_console.after(0, err_msg, e)
            finally:
                if client: client.close()
                self.remote_log_running = False
        
        threading.Thread(target=task, daemon=True).start()

    def stop_remote_log(self):
        self.remote_log_running = False
        self.txt_console.configure(state="normal")
        self.txt_console.insert("end", "\n[GUI] Retour Local Log.\n")
        self.txt_console.configure(state="disabled")

if __name__ == "__main__":
    # Priority Boost
    try:
        pid = os.getpid()
        import psutil
        p = psutil.Process(pid)
        p.nice(psutil.HIGH_PRIORITY_CLASS)
        logger.info("Process Priority set to HIGH")
    except:
        logger.warning("Could not set Process Priority (psutil missing?)")

    app = StreamApp()
    app.mainloop()
    state.streaming = False
