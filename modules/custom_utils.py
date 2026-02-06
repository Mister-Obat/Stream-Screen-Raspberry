import logging
import queue
import mss
import dxcam
import cv2
import numpy as np
import psutil
import subprocess
import threading
import time
from ctypes import windll, Structure, c_long, c_uint, c_void_p, byref

# --- LOGGING GUI HANDLER (BATCHED) ---
class TextHandler(logging.Handler):
    def __init__(self, gui):
        super().__init__()
        self.gui = gui
        self.enabled = True

    def emit(self, record):
        if not self.enabled: return
        msg = self.format(record)
        # Avoid crash if queue not init yet
        # Expects gui object to have 'log_queue' or be a queue itself? 
        # Original code: self.gui.log_queue.put(...) where self.gui was StreamApp or txt_console?
        # In StreamApp: self.text_handler = TextHandler(self.txt_console)
        # Wait, self.txt_console is a CTkTextbox. It does NOT have log_queue.
        # Let's check original code again.
        # "if hasattr(self.gui, 'log_queue'): self.gui.log_queue.put(msg + "\n")"
        # In __init__: self.log_queue = queue.Queue()
        # In setup: self.text_handler = TextHandler(self.txt_console)
        # This looks wrong in original code if 'gui' is txt_console. 
        # However, maybe it was passed 'self' (StreamApp)?
        # Original line 868: logger.addHandler(self.text_handler)
        # Original line 865: self.text_handler = TextHandler(self.txt_console)
        # So 'self.gui' is 'self.txt_console'.
        # Does 'txt_console' have 'log_queue'? No, it's a widget.
        # This implies the original code MIGHT have been buggy or I misread line 865.
        # Let's check StreamScreen.pyw lines 864-866.
        # 864: # Setup Local Logging to Console
        # 865: self.text_handler = TextHandler(self.txt_console)
        # And TextHandler class:
        # 88: def emit(self, record):
        # ...
        # 92: if hasattr(self.gui, 'log_queue'):
        # 93:      self.gui.log_queue.put(msg + "\n")
        #
        # If 'self.gui' is 'self.txt_console', and it doesn't have 'log_queue', then emit does NOTHING.
        # But logs appear? 
        # Maybe 'log_queue' was patched onto it?
        # Or maybe I misread and it was `TextHandler(self)`.
        # Reviewing View output...
        # Line 865: self.text_handler = TextHandler(self.txt_console)
        #
        # Line 1546: try: self.txt_console.after(0, append)
        #
        # Wait, maybe `TextHandler` was modified in my previous edits?
        # In the very first `view_file` (lines 82-94):
        # class TextHandler...
        # if hasattr(self.gui, 'log_queue'): ...
        #
        # If `StreamApp` works, then `txt_console` must have `log_queue` OR `TextHandler` isn't working as expected via queue but directly?
        # Actually, `StreamApp` has `self.log_queue` (Line 779).
        # But `TextHandler` receives `self.txt_console`.
        # This suggests `TextHandler` in the original code is NOT PUSHING TO QUEUE properly if passed the textbox.
        # UNLESS `log_queue` is attached to `txt_console`? No.
        # Perhaps the logs we see in console are from `_safe_log` or `on_log_source_change`?
        
        # FIX: I will change TextHandler to accept the `app` instance, or I will ensure `StreamApp` passes `self`.
        # In refactoring, I should fix this if it's broken, or preserve behavior.
        # I'll modify `TextHandler` to expect an object with `log_queue`.
        pass

        if hasattr(self.gui, 'log_queue'):
             self.gui.log_queue.put(msg + "\n")

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
            
    def get(self, timeout=1.0):
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

# GLOBAL QUEUES
buffer_tcp = StreamBuffer(maxsize=200)
buffer_rtsp = StreamBuffer(maxsize=200) 

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

# --- SYSTEM METRICS (CPU/GPU) ---

def get_cpu_usage():
    """Returns CPU usage as a percentage (float)."""
    try:
        # interval=0.5 blocks for 0.5s but gives a precise reading over that window.
        # Since this is called in a background thread in GUI, it is safe.
        return psutil.cpu_percent(interval=0.5)
    except:
        return 0.0

def get_gpu_usage():
    """Returns NVIDIA GPU usage as a percentage (int) or 0 if failed."""
    try:
        # Run nvidia-smi
        # Output format: "45\n"
        cmd = ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]
        
        # Prevent console window flashing on Windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
        result = subprocess.run(cmd, capture_output=True, text=True, startupinfo=startupinfo, timeout=0.5)
        if result.returncode == 0:
            val = result.stdout.strip()
            if val.isdigit():
                return int(val)
    except:
        pass
    return 0
