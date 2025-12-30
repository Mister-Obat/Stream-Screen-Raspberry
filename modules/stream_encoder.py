import av
import numpy as np
import logging
import time
from fractions import Fraction

logger = logging.getLogger("VideoEncoder")

class VideoEncoder:

    
    def __init__(self, width=1280, height=720, fps=60, bitrate=4000000, codec_choice="auto", preset_choice="fast", initial_pts=0):
        """
        Initialize the Video Encoder.
        :param width: Video width
        :param height: Video height
        :param fps: Target FPS
        :param bitrate: Target bitrate in bits/s (e.g. 4000000 for 4Mbps)
        :param codec_choice: 'auto', 'nvenc', 'x264'
        :param preset_choice: 'fast', 'balanced', 'quality'
        :param initial_pts: Starting value for monotonic PTS (prevents timeline reset on restart)
        """
        self.width = width
        self.height = height
        self.fps = fps
        self.bitrate = bitrate
        self.preset_choice = preset_choice or "fast"
        
        self.ctx = None
        self.codec_name = "libx264" # Default fallback
        self._force_keyframe = True # [FIX] ALWAYS start with a Keyframe (IDR)
        self.frame_count = initial_pts 
        self.start_time = None # [NEW] For Wall-Clock PTS
        
        # 1. Select Codec
        if codec_choice == "auto" or codec_choice == "nvenc":
            if self._is_codec_available("h264_nvenc"):
                self.codec_name = "h264_nvenc"
            elif self._is_codec_available("h264_amf"):
                self.codec_name = "h264_amf"
            else:
                logger.warning("NVENC/AMF not found, falling back to libx264.")
                self.codec_name = "libx264"
        elif codec_choice == "x264":
            self.codec_name = "libx264"
            
        print(f"[VideoEncoder] Selected Codec: {self.codec_name}")
        
        # 2. Init Context
        try:
            self.codec = av.codec.Codec(self.codec_name, "w")
            self.ctx = av.codec.CodecContext.create(self.codec)
            
            self.ctx.width = self.width
            self.ctx.height = self.height
            self.ctx.pix_fmt = 'yuv420p'
            self.ctx.time_base = Fraction(1, self.fps)
            self.ctx.framerate = Fraction(self.fps, 1)
            self.ctx.bit_rate = self.bitrate
            # self.ctx.rc_max_rate = self.bitrate  # Removed: Not supported in this PyAV version
            # self.ctx.rc_buffer_size = self.bitrate # Removed: Not supported in this PyAV version
            
            # LATENCY KILLER: Threading
            # Frame threading adds latency = number of threads.
            # We must use SLICE threading or 1 thread.
            self.ctx.thread_count = 1 
            self.ctx.thread_type = "SLICE"
            
            self.ctx.gop_size = self.fps # Keyframe every 1 second (Better for Low Latency)
            
            # 3. Apply Low Latency Optimizations
            self.ctx.max_b_frames = 0 # STRICTLY 0 B-frames for low latency
            
            if "nvenc" in self.codec_name:
                p_val = "p1" # fast
                if self.preset_choice == "balanced": p_val = "p3"
                elif self.preset_choice == "quality": p_val = "p4"
                
                self.ctx.options = {
                    "preset": p_val,      # Fastest
                    "tune": "ll",        # Low Latency
                    "zerolatency": "1",
                    "delay": "0",
                    "rc": "cbr",
                    "rc-lookahead": "0",
                    "maxrate": str(self.bitrate),
                    "bufsize": str(self.bitrate), 
                }
            else:
                # x264 options
                p_val = "ultrafast" # fast
                if self.preset_choice == "balanced": p_val = "superfast"
                elif self.preset_choice == "quality": p_val = "veryfast"
                
                self.ctx.options = {
                    "preset": p_val,
                    "tune": "zerolatency",
                    "profile": "baseline", # [FIX] Baseline is required for broad player stability (WebRTC/RTSP)
                    "bframes": "0", # Force 0 B-frames
                    # "nal-hrd": "cbr", # DISABLED: Causes buffering pauses if bitrate dips
                    "maxrate": str(self.bitrate),
                    "bufsize": str(self.bitrate * 2), # Allow 2s burst buffer (prevents underrun)
                }
            
            # [FIX] Critical for RTSP/MP4 containers: Global Header (SPS/PPS in extradata)
            # REVERTED: This caused Mbps:0.0 (no packets) on Windows/x264.
            # self.ctx.flags |= av.codec.CodecContext.FLAG_GLOBAL_HEADER
            
            self.ctx.open()
            logger.info(f"VideoEncoder initialized with {self.codec_name} @ {width}x{height}")
            
        except Exception as e:
            logger.error(f"Failed to init encoder: {e}")
            self.ctx = None

    def _is_codec_available(self, name):
        try:
            av.codec.Codec(name, "w")
            return True
        except:
            return False

    def encode(self, frame_bgr):
        """
        Encodes a BGR (OpenCV/Numpy) frame.
        Returns a list of bytes (packets).
        """
        if self.ctx is None: return []
        
        try:
            # Wrap numpy frame
            # Note: The color conversion BGR -> YUV420p is done by PyAV/FFmpeg here.
            # It runs on CPU.
            frame = av.VideoFrame.from_ndarray(frame_bgr, format='bgr24')
            
            # [NEW] Force Keyframe if requested
            if self._force_keyframe:
                frame.pict_type = av.video.frame.PictureType.I
                self._force_keyframe = False # Reset flag
                # logger.info("Forcing Keyframe (IDR)")
            
            # [FIX] Strict Monotonic PTS (Counter-based) for RTSP Stability
            # Wall-Clock skipping can cause gaps that confuse players/muxers.
            # We rely on the core loop's timing to keep real-time.
            
            self.frame_count += 1
            
            frame.pts = self.frame_count
            frame.time_base = self.ctx.time_base # 1/fps
            
            # Force Keyframe (IDR) strictly every GOP (e.g. 60 frames)
            # This ensures HLS always has a cut point every second.
            if self.frame_count % self.fps == 0:
                frame.pict_type = av.video.frame.PictureType.I
                self._force_keyframe = False # Clear manual flag if any
            
            # Manual Keyframe Request
            if self._force_keyframe:
                frame.pict_type = av.video.frame.PictureType.I
                self._force_keyframe = False
            
            packets = self.ctx.encode(frame)
            
            # Return raw PACKETS (needed for Muxing/RTSP)
            return packets
            
        except Exception as e:
            logger.error(f"Encode Error: {e}")
            return []
            
    def close(self):
        if self.ctx:
            try:
                # Flush
                packets = self.ctx.encode(None)
                packets = self.ctx.encode(None)
                return packets
            except: pass
        return []

    def force_next_keyframe(self):
        """Request the next frame to be an IDR Keyframe"""
        self._force_keyframe = True
