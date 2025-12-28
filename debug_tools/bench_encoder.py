import av
import time
import numpy as np
import cv2

def benchmark_encoder(width=1920, height=1080, codec_name='h264_nvenc'):
    print(f"--- BENCHMARK: {codec_name} ({width}x{height}) ---")
    
    try:
        # 1. Setup Container (Dummy memory file)
        # We use a memory format or just a null output to test raw encoding speed
        # But PyAV usually wants a container. We'll use 'null' format if possible or just raw stream
        # actually, easier to just use VideoCodecContext directly like a raw encoder
        
        ctx = av.codec.CodecContext.create(codec_name, "w")
        ctx.width = width
        ctx.height = height
        ctx.pix_fmt = 'yuv420p'
        ctx.time_base = "1/60"
        
        # preset: p1 (fastest) to p7 (slowest/quality). p4 is medium "hq".
        # tune: ll (low latency)
        options = {
            "preset": "p4", 
            "tune": "ll",
            "zerolatency": "1" # Important for streaming
        }
        
        # Open
        ctx.open(options=options)
        print(f"[SUCCESS] Codec {codec_name} opened!")
        
    except Exception as e:
        print(f"[FAIL] Could not open codec {codec_name}: {e}")
        return

    # 2. Generate Dummy Frame (RGB)
    print("Generating dummy frames...")
    frame_bgr = np.random.randint(0, 255, (height, width, 3), dtype=np.uint8)
    
    # 3. Loop
    frame_count = 600
    start_time = time.time()
    
    encoded_packets = 0
    total_bytes = 0
    
    print("Starting Encode Loop...")
    for i in range(frame_count):
        # Convert BGR to YUV420p (CPU Cost - inevitable without Shared Memory/CUDA)
        # PyAV can handle this via av.VideoFrame.from_ndarray
        
        # wrap numpy array
        frame = av.VideoFrame.from_ndarray(frame_bgr, format='bgr24')
        
        # Encode
        packets = ctx.encode(frame)
        for p in packets:
            encoded_packets += 1
            total_bytes += len(p)
            
    # Flush
    packets = ctx.encode(None)
    for p in packets:
        encoded_packets += 1
        total_bytes += len(p)
        
    end_time = time.time()
    duration = end_time - start_time
    fps = frame_count / duration
    
    print(f"--- RESULTS ---")
    print(f"Time: {duration:.3f}s")
    print(f"FPS:  {fps:.2f}")
    print(f"Data: {total_bytes / 1024 / 1024:.2f} MB")
    print(f"Avg Packet: {total_bytes/encoded_packets if encoded_packets else 0} bytes")
    print("----------------")

if __name__ == "__main__":
    # Check Codecs: Just list common ones
    print("Checking H.264 Codecs availability...")
    candidates = ['h264_nvenc', 'h264_amf', 'libx264', 'h264_qsv']
    for c_name in candidates:
        try:
            # Try to grab the codec wrapper
            c = av.codec.Codec(c_name, 'w')
            print(f" - {c.name}: Available")
        except:
            print(f" - {c_name}: Not Found")
            
    # Test NVENC
    print("\nTesting NVENC...")
    benchmark_encoder(1920, 1080, 'h264_nvenc')
    
    # Test x264 (Fallback)
    # print("\nTesting x264 (CPU)...")
    # benchmark_encoder(1920, 1080, 'libx264')
