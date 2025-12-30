import socket
import struct
import time
import logging
import threading
import av
from fractions import Fraction
from modules.config import state, DEFAULT_PORT
from modules.custom_utils import buffer_tcp, buffer_rtsp

logger = logging.getLogger("SenderGUI")

# --- THREADS (Updated for Real-time Config) ---
# --- THREADS (Updated for Real-time Config) ---
def sender_loop(sock):
    logger.info(f"Sender Loop: STARTING with socket {sock}")
    buffer_tcp.running = True
    import select # Import here to avoid circular or top-level issues if any
    
    # Counter for debug
    packet_count = 0
    
    try:
        while state.streaming:
            # 1. Check for Disconnection (Readability check)
            try:
                r, _, _ = select.select([sock], [], [], 0.0)
                if r:
                    d = sock.recv(1024)
                    if not d:
                        logger.info("Sender Loop: Client sent FIN (Disconnect).")
                        break # Exit loop
                    else:
                         # Client sent data (Key command?)
                         logger.info(f"Sender Loop: Rx Data: {d}")
            except Exception as e_sel:
                 logger.error(f"Sender Loop SELECT Error: {e_sel}")
                 break
            
            # 2. Get Data
            packet = buffer_tcp.get(timeout=0.1)
            
            # If timeout, loop back to check connection status again
            if packet is None: continue 
            
            # Packet structure: [Size (4 bytes)] + [Data]
            
            if state.compatibility_mode:
                # RAW Mode (VLC/MPEG-TS)
                sock.sendall(bytes(packet))
            else:
                # APP Mode (Custom Protocol)
                data = bytes(packet)
                header = struct.pack(">L", len(data))
                sock.sendall(header + data)
                
    except Exception as e:
        logger.error(f"Sender Loop CRASH: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        logger.info("Sender Loop Exiting. Cleaning up connection.")
        state.client_connected = False
        try: sock.shutdown(socket.SHUT_RDWR)
        except: pass
        try: sock.close()
        except: pass

def rtsp_publisher_loop():
    buffer_rtsp.running = True
    url = "rtsp://127.0.0.1:8554/stream"
    
    logger.info("RTSP Publisher Loop: Enter")
    
    # Persistent Connection Manager (Outer Loop)
    while state.streaming and state.rtsp_mode:
        container = None
        try:
             # ... (Full Connection Logic) ...
             # We use a nested try/except to catch connection failures without exiting the thread
             
             logger.info(f"RTSP: Connecting to {url}...")
             
             # 1. CONNECT
             try:
                # [FIX] Optimized options for Low Latency & High Performance
                # Removed 'pkt_size' which can cause fragmentation issues on localhost TCP
                container = av.open(url, mode='w', format='rtsp', options={
                    'fflags': 'nobuffer', 
                    'flush_packets': '1', 
                    'rtsp_transport': 'tcp', 
                    # 'stimeout': '5000000', # Warned as unused
                    # 'muxdelay': '0',       # Warned as unused
                })
             except Exception as e:
                logger.warning(f"RTSP Connect Fail: {e}. Retrying in 2s...")
                time.sleep(2.0)
                continue 
            
             # 2. SETUP STREAM
             stream = container.add_stream('h264', rate=state.fps)
             stream.time_base = Fraction(1, 90000) 
             stream.width = state.target_w
             stream.height = state.target_h
             stream.pix_fmt = 'yuv420p'

             # 3. EXTRADATA CHECK (Non-blocking)
             # If available (e.g. NVENC often has it), use it. 
             # If not (x264 default), proceed with inline headers.
             logger.info("RTSP: Checking for Extradata...")
             
             # Small delay to allow encoder to init if it was just created
             time.sleep(0.5) 
             
             if state.encoder and state.encoder.ctx and state.encoder.ctx.extradata:
                 logger.info(f"RTSP: Extradata found ({len(state.encoder.ctx.extradata)} bytes). Applying.")
                 stream.codec_context.extradata = state.encoder.ctx.extradata
             else:
                 logger.info("RTSP: No Global Extradata found. Relying on Inline Headers (Annex B).")

             logger.info("RTSP: Connected & Ready! Streaming loop start...")

             logger.info("RTSP: Connected & Ready! Streaming loop start...")
             
             # 4. STREAM LOOP
             buffer_rtsp.clear()
             # [FIX] Force immediate Keyframe (IDR) to prevent initial freeze
             # We just cleared the buffer, so the first packet sent MUST be an IDR.
             if state.encoder: state.encoder.force_next_keyframe()

             error_count = 0
             
             while state.streaming and state.rtsp_mode:
                packet = buffer_rtsp.get()
                if packet is None: continue
                
                try:
                    # [FIX] Manual Timestamp Rescaling (PyAV Compatibility)
                    # packet.rescale_timestamp() is not available in some versions.
                    # Formula: PTS_new = PTS_old * 90000 / FPS
                    
                    # Assign stream first so PyAV knows the context
                    packet.stream = stream
                    
                    if packet.pts is None: packet.pts = 0
                    
                    # Calculate strictly (avoid float drift if possible, but int casting is usually fine for this scale)
                    # Encoder Timebase: 1/FPS
                    # Stream Timebase: 1/90000
                    scale_factor = 90000 / state.fps
                    new_pts = int(packet.pts * scale_factor)
                    
                    packet.pts = new_pts
                    packet.dts = new_pts # Force DTS=PTS (No B-frames)
                    
                    container.mux(packet)
                    error_count = 0
                except Exception as e:
                    # Broken pipe or server disconnect
                    logger.warning(f"RTSP Mux Fail: {e}")
                    error_count += 1
                    if error_count > 10: raise e # Force reconnect
            
             if container: container.close()
            
        except Exception as e:
            logger.error(f"RTSP Loop Critical Error: {e}. Resetting in 2s...")
            if container: 
                try: container.close()
                except: pass
            time.sleep(2.0)
            
    logger.info(f"RTSP Publisher Thread Exit (Streaming: {state.streaming})")
