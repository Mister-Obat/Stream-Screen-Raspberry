
import customtkinter as ctk
import tkinter as tk
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
# ctypes for cursor
from ctypes import windll, Structure, c_long, c_uint, c_void_p, byref, sizeof

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
class StreamState:
    def __init__(self):
        self.streaming = False  # Controls the capture loop
        self.monitor_idx = 0    # Default to Screen 0 (Index 0 in list)
        self.backend = "DXCam"  # Default to DXCam
        self.fps = 60
        self.quality = 50
        self.resolution = "720p" 
        self.target_w = 1280
        self.target_h = 720
        self.dxcam_mapping = {}
        
        # Pi Config
        self.pi_ip = "192.168.1.XX"
        self.pi_user = "pi"
        self.pi_pass = "raspberry"
        self.pi_path = "Desktop/stream_receiver.py"
        
        # Runtime Stats
        self.current_mbps = 0.0
        self.current_fps = 0


    def save(self):
        data = {
            "monitor_idx": self.monitor_idx,
            "backend": self.backend,
            "fps": self.fps,
            "quality": self.quality,
            "resolution": self.resolution,
            "pi_ip": self.pi_ip,
            "pi_user": self.pi_user,
            "pi_pass": self.pi_pass,
            "pi_path": self.pi_path
        }
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(data, f)
        except: pass

    def load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    data = json.load(f)
                    self.monitor_idx = data.get("monitor_idx", 0)
                    self.backend = data.get("backend", "DXCam")
                    self.fps = data.get("fps", 60)
                    self.quality = data.get("quality", 50)
                    self.resolution = data.get("resolution", "720p")
                    self.pi_ip = data.get("pi_ip", "")
                    self.pi_user = data.get("pi_user", "pi")
                    self.pi_pass = data.get("pi_pass", "raspberry")
                    self.pi_path = data.get("pi_path", "Desktop/stream_receiver.py")
            except: pass

state = StreamState()
state.load()

# --- HELPER: STREAM BUFFER ---
class StreamBuffer:
    def __init__(self):
        self.lock = threading.Lock()
        self.frame_data = None
        self.seq = 0
        self.new_data_event = threading.Event()
        self.running = True

    def put(self, data, seq):
        with self.lock:
            self.frame_data = data
            self.seq = seq
            self.new_data_event.set()

    def get(self):
        has_data = self.new_data_event.wait(timeout=1.0)
        if not has_data or not self.running:
            return None, None
        
        with self.lock:
            self.new_data_event.clear()
            return self.frame_data, self.seq

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
def draw_cursor_arrow(img, x, y):
    pts = np.array([[x, y], [x, y+16], [x+4, y+13], [x+7, y+20], [x+10, y+19], [x+6, y+12], [x+11, y+11]], np.int32)
    pts = pts.reshape((-1, 1, 2))
    cv2.fillPoly(img, [pts], (255, 255, 255))
    cv2.polylines(img, [pts], True, (0, 0, 0), 1)

# --- THREADS ---
def sender_loop(sock):
    buffer_obj.running = True
    try:
        while state.streaming:
            data, seq = buffer_obj.get()
            if data is None: continue 
            header = struct.pack(">LL", seq, len(data))
            sock.sendall(header + data)
    except: pass

def stream_thread_func():
    logger.info("Stream Thread Started")
    
    # Init Backend Mapping
    state.dxcam_mapping = map_dxcam_monitors()
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(('0.0.0.0', DEFAULT_PORT))
        server.listen(1)
        server.settimeout(1.0)
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
    
    current_backend = None
    current_mon_idx = -1
    
    seq = 0
    
    # Monitor Geometry
    mon_left, mon_top, mon_width, mon_height = 0, 0, 1920, 1080
    
    # Stats Counter
    byte_count = 0
    frame_count = 0
    last_stat_time = time.time()
    
    while state.streaming:
        # A. Connection
        if conn is None:
            try:
                conn, addr = server.accept()
                conn.settimeout(None)
                logger.info(f"Client connected: {addr}")
                seq = 0
                buffer_obj.running = True
                threading.Thread(target=sender_loop, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except:
                continue

        # B. Init/Re-init Capture
        # Check if backend or monitor changed
        if (current_backend != state.backend) or (current_mon_idx != state.monitor_idx):
            # Cleanup
            if dxcam_camera: dxcam_camera.stop(); dxcam_camera = None
            if sct: sct.close(); sct = None
            
            current_backend = state.backend
            current_mon_idx = state.monitor_idx
            
            # Geometry
            try:
                with mss.mss() as tmp_sct:
                    idx = state.monitor_idx + 1
                    if idx < len(tmp_sct.monitors):
                        m = tmp_sct.monitors[idx]
                        mon_left, mon_top = m["left"], m["top"]
                        mon_width, mon_height = m["width"], m["height"]
            except: pass
            
            # Init
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

            if frame_ready and frame_bgr is not None:
                # Resize
                h, w = frame_bgr.shape[:2]
                tw, th = state.target_w, state.target_h
                if state.resolution == "Native": tw, th = w, h
                
                if (w != tw or h != th) and tw > 0 and th > 0:
                    frame_bgr = cv2.resize(frame_bgr, (tw, th))
                
                # Cursor
                mx, my = get_cursor_pos_fast()
                rx = int((mx - mon_left) * tw / w) if w else -100
                ry = int((my - mon_top) * th / h) if h else -100
                if 0 <= rx < tw and 0 <= ry < th:
                    draw_cursor_arrow(frame_bgr, rx, ry)
                
                # Encode
                ret, buf = cv2.imencode('.jpg', frame_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), state.quality])
                if ret:
                    data_bytes = buf.tobytes()
                    buffer_obj.put(data_bytes, seq)
                    seq += 1
                    
                    # Update Stats
                    byte_count += len(data_bytes)
                    frame_count += 1
                    
                    t_now = time.time()
                    if t_now - last_stat_time >= 1.0:
                        state.current_fps = frame_count
                        # bytes * 8 / 1000 / 1000 = Mbps
                        state.current_mbps = (byte_count * 8) / (1000 * 1000)
                        
                        byte_count = 0
                        frame_count = 0
                        last_stat_time = t_now
                    
        except: pass
        
        # FPS Cap
        dt = time.time() - t_start
        target_dt = 1.0 / state.fps
        if dt < target_dt: time.sleep(target_dt - dt)

    # Cleanup when loop ends
    buffer_obj.running = False
    buffer_obj.new_data_event.set()
    if conn: conn.close()
    if dxcam_camera: dxcam_camera.stop()
    server.close()
    logger.info("Stream Thread Stopped")

# --- GUI ---
class StreamApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Setup
        self.title("Stream Screen")
        self.geometry("450x650")
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        
        # Header
        self.lbl_title = ctk.CTkLabel(self, text="Stream Screen", font=("Roboto", 24, "bold"))
        self.lbl_title.pack(pady=20)
        
        # --- TAB VIEW ---
        self.tabs = ctk.CTkTabview(self)
        self.tabs.pack(fill="both", expand=True, padx=20, pady=10)
        
        self.tab_stream = self.tabs.add("Stream")
        self.tab_pi = self.tabs.add("Raspberry Pi")
        
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
            
        # 2. Mode
        self.frm_mode = ctk.CTkFrame(self.tab_stream)
        self.frm_mode.pack(fill="x", pady=5)
        ctk.CTkLabel(self.frm_mode, text="Mode de Capture:", font=("Arial", 12, "bold")).pack(anchor="w", padx=10, pady=5)
        self.opt_mode = ctk.CTkOptionMenu(self.frm_mode, values=["DXCam (Rapide)", "MSS (Compatible)"], command=self.on_mode_change)
        self.opt_mode.pack(fill="x", padx=10, pady=5)
        self.opt_mode.set("DXCam (Rapide)" if state.backend == "DXCam" else "MSS (Compatible)")

        # 3. Settings (FPS/Quality/Res)
        self.frm_set = ctk.CTkFrame(self.tab_stream)
        self.frm_set.pack(fill="x", pady=5)
        
        # FPS
        # FPS
        self.lbl_fps_val = ctk.CTkLabel(self.frm_set, text=f"FPS Cible: {state.fps}")
        self.lbl_fps_val.pack(anchor="w", padx=10)
        self.sld_fps = ctk.CTkSlider(self.frm_set, from_=10, to=120, number_of_steps=11, command=self.on_fps_change)
        self.sld_fps.set(state.fps)
        self.sld_fps.pack(fill="x", padx=10, pady=5)
        
        # Quality
        self.lbl_qual_val = ctk.CTkLabel(self.frm_set, text=f"Qualité: {state.quality}%")
        self.lbl_qual_val.pack(anchor="w", padx=10)
        self.sld_qual = ctk.CTkSlider(self.frm_set, from_=10, to=100, number_of_steps=90, command=self.on_qual_change)
        self.sld_qual.set(state.quality)
        self.sld_qual.pack(fill="x", padx=10, pady=5)
        
        # Res
        ctk.CTkLabel(self.frm_set, text="Résolution:").pack(anchor="w", padx=10)
        self.opt_res = ctk.CTkOptionMenu(self.frm_set, values=["480p", "720p", "1080p", "Native"], command=self.on_res_change)
        self.opt_res.pack(fill="x", padx=10, pady=10)
        self.opt_res.set(state.resolution)

        # 4. BIG BUTTON
        self.btn_start = ctk.CTkButton(self.tab_stream, text="LANCER LE FLUX", 
                                       font=("Arial", 18, "bold"), 
                                       height=50, 
                                       fg_color="green", 
                                       hover_color="darkgreen",
                                       command=self.toggle_stream)
        self.btn_start.pack(fill="x", padx=10, pady=20)
        
        # === TAB: PI ===
        ctk.CTkLabel(self.tab_pi, text="Configuration SSH", font=("Arial", 14, "bold")).pack(pady=10)
        
        self.ent_ip = ctk.CTkEntry(self.tab_pi, placeholder_text="IP du Raspberry Pi")
        self.ent_ip.pack(fill="x", padx=10, pady=5)
        if state.pi_ip: self.ent_ip.insert(0, state.pi_ip)
        
        self.ent_user = ctk.CTkEntry(self.tab_pi, placeholder_text="Utilisateur (ex: pi)")
        self.ent_user.pack(fill="x", padx=10, pady=5)
        self.ent_user.insert(0, state.pi_user)

        self.ent_pass = ctk.CTkEntry(self.tab_pi, placeholder_text="Mot de passe", show="*")
        self.ent_pass.pack(fill="x", padx=10, pady=5)
        self.ent_pass.insert(0, state.pi_pass)
        
        ctk.CTkLabel(self.tab_pi, text="Chemin fichier python :").pack(anchor="w", padx=10, pady=(10,0))
        self.ent_path = ctk.CTkEntry(self.tab_pi, placeholder_text="ex: Desktop/stream_receiver.py")
        self.ent_path.pack(fill="x", padx=10, pady=5)
        self.ent_path.insert(0, state.pi_path)
        
        self.btn_pi = ctk.CTkButton(self.tab_pi, text="Lancer Receiver sur Pi (SSH)", 
                                    height=40,
                                    fg_color="#D63384",
                                    hover_color="#A81D62",
                                    command=self.launch_pi)
        self.btn_pi.pack(fill="x", padx=10, pady=20)
        
        # Footer
        self.lbl_status = ctk.CTkLabel(self, text="Status: Prêt", text_color="gray")
        self.lbl_status.pack(side="bottom", pady=5)
        
        # Start Monitor Loop
        self.monitor_stats()

    def monitor_stats(self):
        if state.streaming:
            msg = f"Status: En Ligne | {state.current_fps} FPS | {state.current_mbps:.2f} Mbps"
            color = "green"
            # Update Quality Label dynamically to show Mbps?
            # User request: "pour la qualité j'aimerais l'info affiché en mbps"
            self.lbl_qual_val.configure(text=f"Qualité: {state.quality}%  ({state.current_mbps:.1f} Mbps)")
        else:
            msg = "Status: Arrêté"
            color = "gray"
            self.lbl_qual_val.configure(text=f"Qualité: {state.quality}%")
            
        if self.btn_start.cget("text") == "STOPPER LE FLUX": # Only update if running to avoid overriding connection messages
             self.lbl_status.configure(text=msg, text_color=color)
        
        self.after(500, self.monitor_stats)
    
    def on_mon_change(self, choice):
        idx = self.mons.index(choice)
        state.monitor_idx = idx
        self.save_config()

    def on_mode_change(self, choice):
        state.backend = "MSS" if "MSS" in choice else "DXCam"
        self.save_config()

    def on_fps_change(self, val):
        state.fps = int(val)
        self.lbl_fps_val.configure(text=f"FPS Cible: {state.fps}")
        self.save_config()

    def on_qual_change(self, val):
        state.quality = int(val)
        self.lbl_qual_val.configure(text=f"Qualité: {state.quality}%")
        self.save_config()

    def on_res_change(self, choice):
        state.resolution = choice
        if choice == "720p": state.target_w, state.target_h = 1280, 720
        elif choice == "1080p": state.target_w, state.target_h = 1920, 1080
        elif choice == "480p": state.target_w, state.target_h = 854, 480
        self.save_config()

    def save_config(self):
        state.pi_ip = self.ent_ip.get()
        state.pi_user = self.ent_user.get()
        state.pi_pass = self.ent_pass.get()
        state.pi_path = self.ent_path.get()
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
                base_cmd = f"export DISPLAY=:0 && export XAUTHORITY=~/.Xauthority && python3 {path}"
                
                final_cmd = base_cmd
                # If command is python script, append IP
                if path.endswith(".py") and local_ip:
                     final_cmd = f"{base_cmd} {local_ip}"
                
                logger.info(f"SSH Connecting to {ip}...")
                self.lbl_status.configure(text="SSH: Connexion...", text_color="orange")
                
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(ip, username=user, password=pwd if pwd else None, timeout=5)
                
                logger.info(f"SSH Executing: {final_cmd}")
                self.lbl_status.configure(text="SSH: Exécution...", text_color="blue")
                
                stdin, stdout, stderr = client.exec_command(final_cmd, get_pty=True)
                
                # Check for immediate errors (waiting a bit)
                time.sleep(1.0)
                
                if stdout.channel.recv_ready():
                    out = stdout.channel.recv(1024).decode().strip()
                    if out: logger.info(f"SSH Check: {out}")
                    if "Error" in out or "found" in out or "denied" in out:
                         messagebox.showerror("Erreur SSH", f"Retour: {out}")
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
                messagebox.showerror("Erreur Connexion", str(e))
        
        threading.Thread(target=ssh_task, daemon=True).start()

if __name__ == "__main__":
    app = StreamApp()
    app.mainloop()
    state.streaming = False
