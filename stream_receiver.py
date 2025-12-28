import socket
import struct
import sys
import io
import os
import time
import threading
import json
import collections

import av
import numpy as np

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
DEFAULT_PORT = 5555
UDP_PORT = 5555

# Global State
# Global State
latest_frame = None # (bytes, w, h)
frame_lock = threading.Lock()
running = True

# Stats for Nerd Graph
ping_history = collections.deque(maxlen=50)

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

def send_command(sock, cmd_dict):
    try:
        p = json.dumps(cmd_dict) + "\n"
        sock.sendall(p.encode())
    except: pass

def network_thread_func(sock):
    global latest_frame, running
    
    print("[NET] Thread démarré (H.264 Decoder).")
    
    codec_ctx = av.codec.CodecContext.create("h264", "r")
    
    def recv_n(n):
        buf = b''
        while len(buf) < n:
            try:
                c = sock.recv(n - len(buf))
                if not c: return None
                buf += c
            except: return None
        return buf

    while running:
        try:
            # Header: [Size (4)] => 4 bytes
            h = recv_n(4)
            if not h: break
            
            size = struct.unpack(">L", h)[0]
            
            # Body
            data = recv_n(size)
            if not data: break
            
            # Decode
            try:
                packet = av.Packet(data)
                frames = codec_ctx.decode(packet)
                
                if not frames:
                    print(f"Packet recu ({len(data)} octets) -> Aucun frame décodée (Buffering ou Manque Keyframe)")
                    
                for frame in frames:
                    print(f"Image décodée: {frame.width}x{frame.height} | Keyframe: {frame.key_frame}")
                    # Convert to RGB (numpy)
                    # format='rgb24' gives HxWx3 array
                    img_array = frame.to_ndarray(format='rgb24')
                    h, w = img_array.shape[:2]
                    
                    # Convert to bytes for Pygame
                    # Note: We could blit directly from array if we used "pygame.surfarray"
                    # But sticking to bytes is safe.
                    raw_bytes = img_array.tobytes()
                    
                    with frame_lock:
                        latest_frame = (raw_bytes, w, h)
                        
            except Exception as e:
                print(f"Decode Error: {e}")
                pass
                    
        except Exception as e:
            print(f"[NET ERR] {e}")
            break

def main():
    global running, latest_frame
    
    # 1. Discovery
    target_ip = None
    if len(sys.argv) > 1:
        target_ip = sys.argv[1]
    else:
        target_ip = discover_server()
        
    if not target_ip:
        print("Serveur non trouvé. Essayez de spécifier l'IP: python stream_receiver.py IP")
        return

    # 2. Connection
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print(f"[Connect] Tentative de connexion vers {target_ip}...")
    connected = False
    for i in range(10): # Try 10 times
        try:
            sock.connect((target_ip, DEFAULT_PORT))
            connected = True
            print("[Connect] Succès!")
            break
        except Exception as e:
            print(f"[Connect] Echec ({i+1}/10): {e}")
            time.sleep(1.0)
    
    if not connected:
        print("Abandon.")
        return

    # 3. Pygame
    pygame.init()
    info = pygame.display.Info()
    # screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN | pygame.DOUBLEBUF | pygame.HWSURFACE)
    # Start with a safe default, will resize on first frame
    screen = pygame.display.set_mode((1280, 720), pygame.FULLSCREEN | pygame.SCALED)
    pygame.mouse.set_visible(False)
    clock = pygame.time.Clock()
    
    font = pygame.font.SysFont(None, 40)
    
    # Start Net Thread
    t = threading.Thread(target=network_thread_func, args=(sock,))
    t.daemon = True
    t.start()
    
    # Params
    config = {"quality": 50, "fps": 60, "res": "720p"}
    show_menu = False
    
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT: running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_q or event.key == pygame.K_ESCAPE: running = False
                elif event.key == pygame.K_m: show_menu = not show_menu
                
                if show_menu:
                    # Inputs
                    update = False
                    if event.key == pygame.K_UP:
                        config['quality'] = min(100, config['quality'] + 5)
                        update = True
                    elif event.key == pygame.K_DOWN:
                        config['quality'] = max(5, config['quality'] - 5)
                        update = True
                    elif event.key == pygame.K_RIGHT:
                        config['fps'] = min(120, config['fps'] + 10)
                        update = True
                    elif event.key == pygame.K_LEFT:
                        config['fps'] = max(10, config['fps'] - 10)
                        update = True
                    elif event.key == pygame.K_r:
                        res_list = ["480p", "720p", "1080p"]
                        curr_idx = res_list.index(config['res'])
                        config['res'] = res_list[(curr_idx + 1) % 3]
                        update = True
                        
                    if update: send_command(sock, config)

        # Render
        frame_info = None
        with frame_lock:
            frame_info = latest_frame
        
        if frame_info:
            raw_bytes, w, h = frame_info
            try:
                # Create surface from raw bytes
                img_surf = pygame.image.frombuffer(raw_bytes, (w, h), "RGB")
                
                # Hardware Scaling Logic (Pygame 2+)
                screen_w, screen_h = screen.get_size()
                
                # If resolution changed, re-init display (Hardware Scaler)
                if w != screen_w or h != screen_h:
                    # print(f"Resizing Display to: {w}x{h}")
                    screen = pygame.display.set_mode((w, h), pygame.FULLSCREEN | pygame.SCALED)
                
                screen.blit(img_surf, (0,0))
            except Exception as e:
                # print(e)
                pass
            
        # Menu
        if show_menu:
            # Overlay
            panel = pygame.Surface((600, 300))
            panel.set_alpha(180)
            panel.fill((0,0,0))
            screen.blit(panel, (50, 50))
            
            lines = [
                f"--- ULTIMATE STREAM SETTINGS ---",
                f"Qualité: {config['quality']}% (Haut/Bas)",
                f"FPS Cible: {config['fps']} (Gauche/Droite)",
                f"Résolution: {config['res']} (R)",
                f"Résolution: {config['res']} (R)",
            ]
            for i, l in enumerate(lines):
                txt = font.render(l, True, (0, 255, 255))
                screen.blit(txt, (70, 70 + i*50))

        pygame.display.flip()
        clock.tick(120)

    sock.close()
    pygame.quit()

if __name__ == "__main__":
    main()
