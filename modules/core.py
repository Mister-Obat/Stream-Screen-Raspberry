import time
import socket
import threading
import logging
import cv2
import numpy as np
import mss
import dxcam
from modules.config import state, DEFAULT_PORT
from modules.custom_utils import buffer_tcp, buffer_rtsp, get_cursor_pos_fast, draw_cursor_arrow, map_dxcam_monitors
from modules.networking import sender_loop, rtsp_publisher_loop
from modules.stream_encoder import VideoEncoder

logger = logging.getLogger("SenderGUI")

def stream_thread_func():
    logger.info(f"Stream Thread Started | Mode: {'RTSP' if state.rtsp_mode else 'TCP SERVER'}")
    
    # Init Backend Mapping
    state.dxcam_mapping = map_dxcam_monitors()
    
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # Important for Latency: Disable Nagle
    server.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    
    # [FIX] Revert to Blocking with Timeout (More robust than non-blocking on Windows)
    server.settimeout(0.2) 
    
    try:
        server.bind(('0.0.0.0', DEFAULT_PORT))
        server.listen(5) # Increase backlog
    except Exception as e:
        logger.error(f"Bind Error: {e}")
        return
        
    # --- START BEACON (UDP Discovery) ---
    def beacon_loop():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while state.streaming:
            try:
                # Msg: STREAM_SERVER|IP|PORT
                msg = f"STREAM_SERVER|{DEFAULT_PORT}"
                sock.sendto(msg.encode(), ('<broadcast>', DEFAULT_PORT))
                time.sleep(1.0)
            except Exception:
                time.sleep(2.0)
        sock.close()

    threading.Thread(target=beacon_loop, daemon=True).start()
    logger.info("UDP Beacon Started for Auto-Discovery")
    
    conn = None
    rtsp_thread = None
    
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
    dropped_tcp_total = 0
    dropped_rtsp_total = 0
    state.loss_percent = 0.0
    
    state.loss_percent = 0.0
    
    last_send_time = time.time()
    last_log_time = time.time() 
    
    # FPS Limiter Vars
    loop_start_time = time.perf_counter()

    # Clear Buffers
    buffer_tcp.clear()
    buffer_rtsp.clear()

    while state.streaming:
        # A. HYBRID CONNECTION LOGIC
        
        # 1. Manage RTSP Publisher Thread
        if state.rtsp_mode:
             if rtsp_thread is None or not rtsp_thread.is_alive():
                 logger.info("Hybrid: Starting RTSP Publisher...")
                 rtsp_thread = threading.Thread(target=rtsp_publisher_loop, daemon=True)
                 rtsp_thread.start()
        
        # 2. Manage TCP Client (Pi/Pc)
        if conn is None:
            try:
                # Blocking accept with timeout (0.2s)
                c, addr = server.accept()
                # Crucial: Set a timeout (e.g. 10s) for operations (recv/send)
                # If set to None (blocking), sendall() can hang forever if the client vanishes (CLOSE_WAIT)
                c.settimeout(10.0) 
                c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                
                logger.info(f"Hybrid: Client connected (TCP): {addr}")
                state.client_connected = True
                
                buffer_tcp.running = True
                buffer_tcp.clear() # Start fresh
                threading.Thread(target=sender_loop, args=(c,), daemon=True).start()
                conn = c
            except socket.timeout: pass # No client, continue loop
            except BlockingIOError: pass 
            except Exception as e: 
                logger.error(f"Accept Error: {e}")
        
        # Check if client disconnected (flag set by sender_loop)
        if conn is not None and not state.client_connected:
            logger.info("Hybrid: Detected Client Disconnection. Resetting conn.")
            conn = None
        
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
            if encoder: encoder.close(); encoder = None # Close existing encoder if any
            
            current_backend = state.backend
            current_codec_choice = state.codec_choice
            current_preset = state.encoder_preset
            current_resolution = state.resolution
            current_mon_idx = state.monitor_idx
            current_fps = state.fps
            current_bitrate_mbps = state.bitrate_mbps
            
            # Clear Buffers to prevent lag/sync issues
            buffer_tcp.clear()
            buffer_rtsp.clear()
            
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
            
            if state.audio_enabled:
                 logger.info(f"[AUDIO] Enabled | Source: {state.audio_source} | Bitrate: {state.audio_bitrate} | Vol: {int(state.audio_volume*100)}%")
            else:
                 logger.info("[AUDIO] Disabled (Muted by Config)")

            # [OPTIM] Monotonic PTS: Repair Timeline
            # Resume frame count from previous encoder to prevent "time reset" errors in MediaMTX/WebRTC
            initial_pts = 0
            if encoder:
                 initial_pts = encoder.frame_count
            
            encoder = VideoEncoder(enc_w, enc_h, state.fps, bitrate_bps, codec_choice=state.codec_choice, preset_choice=state.encoder_preset, initial_pts=initial_pts)
            state.encoder = encoder # Expose for RTSP (extradata)
            
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
            t0_start = time.time()
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
                    
                    if state.rtsp_mode:
                        # RTSP requires CFR
                        pass 
                    else:
                        # TCP/Pi Mode: Use PING Heartbeat
                        time_since_last = time.time() - last_send_time
                        
                        if time_since_last < 0.5:
                            time.sleep(0.01)
                            continue
                        
                        # SEND HEARTBEAT (PING)
                        # We push directly to TCP buffer
                        if conn is not None:
                             buffer_tcp.put(b'PING')
                             last_send_time = time.time()
                             logger.info("Sent PING") # Verbose for Debug
                        
                        continue

                # Update Last State
                last_frame_data = frame_bgr.copy() # Copy is needed as frame_bgr is mutable
                last_cursor_pos = (mx, my)
                last_send_time = time.time()
                
                if 0 <= rx < tw and 0 <= ry < th:
                    draw_cursor_arrow(frame_bgr, rx, ry, scale=cursor_scale)
                
                t3 = time.time()

                t3 = time.time()

                # --- [OPTIM] PRE-ENCODING DROP (Congestion Control) ---
                # "LATENCY IS THE BOSS" Logic
                # 0-10%: REAL-TIME PRIORITY (Aggressive Drop / Snap-to-Live)
                # 11-90%: BALANCED (Smooth Drop)
                # 91-100%: QUALITY PRIORITY (Never Drop)

                # 1. QUALITY MODE (> 90%)
                if state.latency_value > 90:
                    # We REFUSE to drop frames proactively. We trust the network or allow buffer to grow.
                    # Only check for extreme OOM protection (e.g. > 5 seconds buffer)
                    oom_threshold = state.fps * 5 
                    
                    full_tcp = conn is not None and buffer_tcp.q.qsize() > oom_threshold
                    full_rtsp = state.rtsp_mode and buffer_rtsp.q.qsize() > oom_threshold
                    
                    if full_tcp or full_rtsp:
                        # EMERGENCY ONLY
                         if full_tcp: dropped_tcp_total += 1
                         if full_rtsp: dropped_rtsp_total += 1
                         frames_total_sec += 1
                         continue
                
                # 2. REAL-TIME MODE (< 10%)
                elif state.latency_value < 10:
                    # STRICT ZERO BUFFER POLICY
                    # If there is ANY packet in the queue, we are lagging.
                    # We FLUSH everything to snap back to live.
                    
                    # Threshold: 1 frame (basically 0 but allow 1 to be in transit)
                    strict_limit = 0 if state.latency_value < 5 else 1
                    
                    full_tcp = conn is not None and buffer_tcp.q.qsize() > strict_limit
                    full_rtsp = state.rtsp_mode and buffer_rtsp.q.qsize() > max(strict_limit, state.fps * 0.5) # RTSP needs ~0.5s grace
                    
                    if full_tcp:
                        with buffer_tcp.q.mutex:
                            q_len = len(buffer_tcp.q.queue)
                            buffer_tcp.q.queue.clear()
                            dropped_tcp_total += q_len
                        # Force Keyframe after flush
                        if encoder: encoder.force_next_keyframe()
                        # Do NOT continue (drop current), we want to encode THIS fresh frame as keyframe!
                    
                    if full_rtsp:
                         # [OPTIM] NO FLUSH for WebRTC (Localhost)
                         # As requested: "Je ne supprime plus les images pour le serveur local... le navigateur gère."
                         # We skip the flush logic for RTSP to prevent visual stutter ("trous").
                         pass
                
                # 3. BALANCED MODE (10-90%)
                else:
                    # Calculated acceptable buffer based on slider
                    # 10% -> 0.2s
                    # 90% -> 2.0s
                    # Linear mapping
                    target_sec = 0.2 + ((state.latency_value - 10) / 80.0) * 1.8 
                    allowed_frames = int(target_sec * state.fps)
                    
                    full_tcp = conn is not None and buffer_tcp.q.qsize() > allowed_frames
                    # [OPTIM] For WebRTC, we validly decided to NEVER drop frames proactively (except OOM)
                    # full_rtsp = state.rtsp_mode and buffer_rtsp.q.qsize() > allowed_frames 
                    full_rtsp = False 
                    
                    if full_tcp or full_rtsp:
                        # SMOOTH DROP: Skip this frame to let buffer drain
                        if full_tcp: dropped_tcp_total += 1
                        if full_rtsp: dropped_rtsp_total += 1
                        frames_total_sec += 1
                        continue

                # ENCODE (H.264)
                packets = encoder.encode(frame_bgr)
                t4 = time.time()
                
                if not packets:
                   # logger.warning("Encoder returned no packets!")
                   pass

                frames_total_sec += 1
                
                # Push to buffers (No more dropping here)
                
                # 1. RTSP Queue
                if state.rtsp_mode:
                    for pkt in packets: 
                        if not buffer_rtsp.put(pkt): dropped_rtsp_total += 1
                
                # 2. TCP Queue
                if conn is not None:
                    for pkt in packets: 
                        if not buffer_tcp.put(bytes(pkt)): dropped_tcp_total += 1
                
                # Only add byte count if at least one sent? 
                # Simplification: just add it, byte count is for source throughput estimation.
                byte_count += sum(len(bytes(p)) for p in packets)
                
                frame_count += 1
                
                # Update Stats
                t_now = time.time()
                if t_now - last_stat_time >= 0.5:
                    elapsed = t_now - last_stat_time
                    state.current_fps = int(frame_count / elapsed)
                    state.current_mbps = ((byte_count * 8) / (1000 * 1000)) / elapsed
                    
                    # Calc Loss % (Independent)
                    loss_tcp_pct = 0.0
                    loss_rtsp_pct = 0.0
                    
                    if frames_total_sec > 0:
                        loss_tcp_pct = (dropped_tcp_total / frames_total_sec) * 100.0
                        loss_rtsp_pct = (dropped_rtsp_total / frames_total_sec) * 100.0
                        
                    # State Update
                    state.loss_tcp = min(100.0, loss_tcp_pct)
                    state.loss_rtsp = min(100.0, loss_rtsp_pct)
                    state.loss_percent = min(100.0, max(loss_tcp_pct, loss_rtsp_pct))
                         
                    # Reset Window
                    frames_total_sec = 0
                    dropped_tcp_total = 0
                    dropped_rtsp_total = 0 # Need to init these variables before loop

                    # Profiling Log
                    cap_ms = (t2 - t1) * 1000
                    proc_ms = (t3 - t2) * 1000
                    if t_now - last_log_time >= 5.0:
                        capture_mode = "DXCAM" if state.backend == "DXCam" else "MSS"
                        
                        # [DEBUG] Show Target FPS vs Actual
                        logger.info(f"[{capture_mode}] Target:{state.fps} | FPS:{state.current_fps} | Mbps:{state.current_mbps:.1f} | " 
                                    f"Q_TCP:{buffer_tcp.q.qsize()} Q_RTSP:{buffer_rtsp.q.qsize()} | "
                                    f"Loss TCP:{state.loss_tcp:.1f}% RTSP:{state.loss_rtsp:.1f}% | "
                                    f"Times(ms) Cap:{t1-t0_start:.1f} Proc:{t2-t1:.1f} Enc:{t4-t3:.1f} Tot:{t4-t0_start:.1f}")
                        last_log_time = t_now
                    # Logic block was removed here.
                    
                    byte_count = 0
                    frame_count = 0
                    last_stat_time = t_now
                    
        except Exception as e:
            logger.error(f"Stream Loop Error: {e}")
            pass
            
        # [FIX] FPS Limiter at END of loop to minimize input latency
        proc_duration = time.perf_counter() - loop_start_time
        target_dt = 1.0 / max(1, state.fps)
        if proc_duration < target_dt:
             time.sleep(target_dt - proc_duration)
        
        loop_start_time = time.perf_counter()
        
        # [OPTIM] Sleep optimization (Input Lag)
        # Capture -> Send -> Sleep -> Capture
        # This was already handled by the logic above (lines 447-451).
        # We remove the redundant sleep block here to avoid double-waiting.

    # Cleanup when loop ends
    buffer_tcp.running = False
    buffer_rtsp.running = False
    buffer_rtsp.running = False
    if conn and hasattr(conn, 'close'): 
        try: conn.close()
        except: pass
    state.client_connected = False
    
    if dxcam_camera: dxcam_camera.stop()
    if encoder: encoder.close()
    server.close()
    logger.info("Stream Thread Stopped")
