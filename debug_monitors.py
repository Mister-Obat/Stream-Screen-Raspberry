import mss
import dxcam
import sys

log = open("debug_log.txt", "w")

def log_print(msg):
    print(msg)
    log.write(str(msg) + "\n")

log_print("=== MSS MONITORS ===")
try:
    with mss.mss() as sct:
        for i, m in enumerate(sct.monitors):
            log_print(f"MSS Index {i}: {m}")
except Exception as e:
    log_print(f"MSS Error: {e}")

log_print("\n=== DXCAM MONITORS ===")
for i in range(10): 
    try:
        log_print(f"--- Checking DXCam Index {i} ---")
        camera = dxcam.create(output_idx=i, output_color="RGB")
        log_print(f"DXCam Index {i} Created.")
        log_print(f"  Width: {camera.width}")
        log_print(f"  Height: {camera.height}")
        log_print(f"  Rotation: {camera.rotation}")
        
        # Inspection
        if hasattr(camera, '_output'):
             log_print(f"  _output: {camera._output}")
             # check for resolution or rect in output
             if hasattr(camera._output, 'Resolution'):
                 log_print(f"  _output.Resolution: {camera._output.Resolution}")
        
        del camera
    except Exception as e:
        if "list index out of range" in str(e) or "monitor" in str(e).lower():
            log_print(f"DXCam Index {i}: Stopped ({e})")
            break
        log_print(f"DXCam Index {i}: Error ({e})")

log.close()
