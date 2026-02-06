import socket
import struct
import sys
import io
import os
import time
import threading


import av
import numpy as np
import os
import time

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = "hide"
import pygame

import ctypes
# Création d'un ID unique basé sur le nom du fichier du script
# Cela permet à chaque app (ex: 'StreamScreen.pyw', 'stream_receiver.py') d'avoir sa propre icône dans la barre des tâches
try:
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    myappid = f'obat.{script_name}.v1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass

# --- CONFIG ---
DEFAULT_PORT = 5000
UDP_PORT = 5000

# Global State
# Global State
latest_frame = None # (bytes, w, h)
frame_lock = threading.Lock()
running = True
last_packet_time = 0.0



def discover_server():
    """Listens for UDP beacon to find Server IP auto-magically."""
    print("[AUTO-DISCOVERY] Ecoute du réseau...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', UDP_PORT))
    sock.settimeout(10.0) # 10s timeout
    
    try:
        while True:
            data, addr = sock.recvfrom(1024)
            msg = data.decode()
            if msg.startswith("STREAM_SERVER"):
                print(f"[DECOUVERT] Serveur trouvé à {addr[0]}")
                return addr[0]
    except socket.timeout:
        print("[AUTO-DISCOVERY] Timeout. Echec.")
        return None
    finally:
        sock.close()



import queue

# --- OPTIMIZATION: JITTER BUFFER ---
packet_queue = queue.Queue(maxsize=4) # STRICT: Only 4 frames buffer (~60ms @ 60fps)

def receive_thread_func(sock):
    """Producer: Reads socket, pushes raw packets to queue."""
    global running, last_packet_time # [FIX] Ensure proper scope
    print("[NET] Reception Thread Started.")
    
    # [FIX] Reduce OS TCP Buffer to minimize "ghost" latency
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 32 * 1024) # 32KB
    except: pass
    
    def recv_n(n):
        buf = b''
        while len(buf) < n:
            try:
                c = sock.recv(n - len(buf))
                if not c: return None
                buf += c
            except socket.timeout:
                if not running: raise
                continue 
            except Exception: 
                return None
        return buf

    try:
        while running:
            # 1. Read Header [Size (4)]
            try:
                h = recv_n(4)
            except Exception: 
                break
                
            if not h: break
            size = struct.unpack(">L", h)[0]
            
            # Sanity
            if size > 10_000_000:
                print(f"[NET] Error: Frame too large ({size}). corrupted?")
                break
                
            # 2. Read Body
            data = recv_n(size)
            if not data: break
            
            # Handle PING (Heartbeat)
            if data == b'PING':
                last_packet_time = time.time()
                # print("[PING] Heartbeat ack") 
                continue
            
            # Update Timeout Timer (Video Data)
            last_packet_time = time.time()
            
            # 3. Push to Queue
            # If queue is full, we block execution of this thread (TCP Flow Control kick in)
            # OR we could drop? But for now, blocking is safer preventing artifact corruption.
            try:
                packet_queue.put(data, timeout=1.0)
            except queue.Full:
                print("[NET] Warning: Decoder too slow, Buffer Full! (TCP Backpressure)")
                # Retry once to avoid dropping immediately?
                # Actually, effectively blocking is better for quality, but adds latency.
                # Let's drop if really stuck to catch up? 
                # No, dropping raw bytes breaks H.264 stream usually.
                pass 
                
    except Exception as e:
        print(f"[NET] Receive Loop Error: {e}")
        pass
        
    print("[NET] Receive Thread Ended.")
    # Signal main loop to restart immediately
    running = False


def decode_thread_func():
    """Consumer: Pulls packets and decodes them."""
    global latest_frame, running
    
    print("[DEC] Decode Thread Started.")
    codec_ctx = av.codec.CodecContext.create("h264", "r")
    
    while running:
        try:
            # Get packet with timeout to allow checking 'running'
            try:
                data = packet_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            
            # PING Check (Just in case it slipped through)
            if data == b'PING': continue
            
            # Decode
            try:
                packet = av.Packet(data)
                frames = codec_ctx.decode(packet)
                
                # Check for latency (Queue size)
                q_sz = packet_queue.qsize()
                if q_sz > 10:
                    # We are lagging.
                    # We can't skip 'decode' (need to update ref frames), but we can skip 'rendering' (conversion)
                    # BUT 'decode' is the heavy part.
                    # FFMPEG multithreading?
                    # For now just logging.
                    # print(f"[DEC] Lag detected: {q_sz} frames buffered.")
                    pass
                
                if not frames: continue

                # Logging Res changes
                if not hasattr(decode_thread_func, "last_res"):
                     decode_thread_func.last_res = (0, 0)

                for frame in frames:
                    if (frame.width, frame.height) != decode_thread_func.last_res:
                        print(f"[VIDEO] Resolution: {frame.width}x{frame.height}")
                        decode_thread_func.last_res = (frame.width, frame.height)
                    
                    # Convert to RGB
                    img_array = frame.to_ndarray(format='rgb24')
                    h, w = img_array.shape[:2]
                    raw_bytes = img_array.tobytes()
                    
                    with frame_lock:
                        latest_frame = (raw_bytes, w, h)
                        
            except Exception as e:
                # Ignore harmless ffmpeg errors
                if "avcodec_send_packet()" not in str(e):
                    print(f"[DEC] Error: {e}")
                    
        except Exception as e:
            print(f"[DEC] Loop Error: {e}")
            break
            
    print("[DEC] Decode Thread Ended.")


def network_start(sock):
    """Launcher helper"""
    # Disable timeout on socket for the blocking read (or use long timeout)
    sock.settimeout(5.0) 
    
    t_recv = threading.Thread(target=receive_thread_func, args=(sock,), daemon=True)
    t_dec = threading.Thread(target=decode_thread_func, daemon=True)
    
    t_recv.start()
    t_dec.start()
    return t_recv, t_dec


def main():
    global running, latest_frame, last_packet_time
    
    print("="*40)
    print(" STREAM RECEIVER v2.4 (Ultra Low Latency + 5min Timeout)")
    # 1. Discovery & Arg Parsing
    target_ip = None
    infinite_retry = False
    opt_windowed = False
    
    # Parse args manually
    args = sys.argv[1:]
    
    # Filter flags
    if "--retry" in args:
        infinite_retry = True
        args.remove("--retry")
        print("[CONFIG] Mode Relance Automatique: ACTIVÉ (Infini)")

    if "--windowed" in args:
        opt_windowed = True
        args.remove("--windowed")
        print("[CONFIG] Mode Fenêtré: ACTIVÉ")
    
    if args:
        target_ip = args[0]
    else:
        target_ip = discover_server()
        
    if not target_ip:
        print("Serveur non trouvé. Essayez de spécifier l'IP: python stream_receiver.py IP")
        return


    # State for toggle
    is_fullscreen = True
    last_click_time = 0.0

    # 2. Pygame Init
    pygame.init()
    
    # Get current resolution for explicit Fullscreen size (SCALED doesn't like 0,0)
    info = pygame.display.Info()
    monitor_w, monitor_h = info.current_w, info.current_h

    # Display Setup
    # Logic:
    # If opt_windowed: Start Windowed (1280x720), Mouse Visible, Toggle Enabled
    # Else: Start Fullscreen, Mouse Hidden, Toggle Disabled
    
    if opt_windowed:
        is_fullscreen = False # Start Windowed
        screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
        pygame.mouse.set_visible(True)
    else:
        is_fullscreen = True # Forced Fullscreen
        screen = pygame.display.set_mode((monitor_w, monitor_h), pygame.FULLSCREEN | pygame.SCALED)
        pygame.mouse.set_visible(False)
        
    pygame.display.set_caption("Stream Receiver")
    clock = pygame.time.Clock()
    font = pygame.font.SysFont(None, 40)

    # CONFIG
    user_quit = False

    # --- SUPER LOOP (Reconnect) ---
    disconnect_start_time = None
    
    while True:
        running = True # Reset run flag
        connected = False
        sock = None
        
        # Screen Feedback
        screen.fill((0, 0, 0))
        txt = font.render(f"Connexion vers {target_ip}...", True, (255, 165, 0))
        screen.blit(txt, (50, 50))
        
        # Display Timeout Countdown if applicable
        if disconnect_start_time and infinite_retry:
             elapsed = time.time() - disconnect_start_time
             remaining = 7200 - elapsed
             if remaining < 0: remaining = 0
             sub = font.render(f"Timeout Safety: {int(remaining)}s", True, (200, 50, 50))
             screen.blit(sub, (50, 100))
             
        pygame.display.flip()
        
        # 3. Connection Loop
        attempt = 0
        if not disconnect_start_time: disconnect_start_time = time.time()
        
        while True:
            attempt += 1
            
            # TIMEOUT CHECK (Only if infinite mode is on)
            # If we've been trying to connect for > 2 hours, we Kill the Process.
            if infinite_retry and (time.time() - disconnect_start_time > 7200):
                 print("[SAFETY] Timeout de 2h atteint. Arrêt définitif (Self-Kill).")
                 pygame.quit()
                 return # Exits the script
            
            # Stop condition for normal mode
            if not infinite_retry and attempt > 10:
                print("Abandon (10/10 tentatives).")
                connected = False
                break
            
            # Check for Quit during connection attempt
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    user_quit = True
                    break
            if user_quit: break

            print(f"[Connect] Tentative de connexion vers {target_ip} ({attempt})...")
            
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(3.0) 
                sock.connect((target_ip, DEFAULT_PORT))
                sock.settimeout(None) # Restore blocking
                connected = True
                print("[Connect] Succès!")
                disconnect_start_time = None # Reset safety timer
                last_packet_time = time.time() # Reset timeout timer
                break
            except Exception as e:
                try: sock.close()
                except: pass
                time.sleep(1.0 if infinite_retry else 1.0)
        
        if user_quit: break
        if not connected: return # Give up if not infinite or max retries reached

        # 4. Start Thread
        network_start(sock)
        
        # 5. Main Loop (Serving)
        print("[MAIN] Entering Stream Loop...")
        while running:
            dt = clock.tick(60)
            
            # CHECK TIMEOUT (Anti-Freeze)
            if time.time() - last_packet_time > 300.0:
                 print("[TIMEOUT] Pas de données depuis 5 minutes. Fin du stream.")
                 running = False
            
            for event in pygame.event.get():
                if event.type == pygame.QUIT: 
                    running = False
                    user_quit = True
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_q or event.key == pygame.K_ESCAPE: 
                        running = False
                        user_quit = True
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    # Only allow toggle if windowed mode is enabled
                    if opt_windowed and event.button == 1: # Left Click
                        curr_time = time.time()
                        if curr_time - last_click_time < 0.5:
                            # Double Click Detected -> Toggle
                            is_fullscreen = not is_fullscreen
                            try:
                                if is_fullscreen:
                                    # Fetch explicit res again (in case it changed or to be safe)
                                    info = pygame.display.Info()
                                    screen = pygame.display.set_mode((info.current_w, info.current_h), pygame.FULLSCREEN | pygame.SCALED)
                                    # User Request: If in windowed mode (which allows toggle), mouse must remain visible even in fullscreen
                                    pygame.mouse.set_visible(True) 
                                else:
                                    screen = pygame.display.set_mode((1280, 720), pygame.RESIZABLE)
                                    pygame.mouse.set_visible(True)
                            except Exception as e:
                                print(f"[DISPLAY] Toggle Error: {e}")
                                # Revert state if failed
                                is_fullscreen = not is_fullscreen
                                
                            last_click_time = 0.0 
                        else:
                            last_click_time = curr_time

            # Render
            frame_info = None
            with frame_lock:
                frame_info = latest_frame
            
            if frame_info:
                data, w, h = frame_info
                
                # --- Aspect Ratio Handling ---
                target_rect = screen.get_rect()
                screen_w, screen_h = target_rect.width, target_rect.height
                
                # Calculate scale to fit
                scale_w = screen_w / w
                scale_h = screen_h / h
                scale = min(scale_w, scale_h)
                
                new_w = int(w * scale)
                new_h = int(h * scale)
                
                # Center coords
                x_offset = (screen_w - new_w) // 2
                y_offset = (screen_h - new_h) // 2
                
                img = pygame.image.frombuffer(data, (w, h), "RGB")
                img = pygame.transform.scale(img, (new_w, new_h))
                
                # Fill black background first (clear previous frame garbage if aspect changed)
                screen.fill((0,0,0)) 
                screen.blit(img, (x_offset, y_offset))
            else:
                # No signal yet or lost
                screen.fill((20, 20, 20))
                txt = font.render(f"Prêt. Attente flux...", True, (100, 100, 100))
                screen.blit(txt, (50, 50))

            pygame.display.flip()
            
        # End of Stream Loop (Disconnected or Quit)
        print("[MAIN] Stream Loop Ended.")
        try: sock.close()
        except: pass
        
        if user_quit:
            print("[MAIN] User Quit via Keyboard/Event.")
            break
            
        if not infinite_retry:
            print("[MAIN] Infinite Retry OFF. Exiting.")
            break
            
        print("[MAIN] Connection Lost. Rebooting loop in 2s...")
        time.sleep(2.0)

    pygame.quit()

if __name__ == "__main__":
    main()
