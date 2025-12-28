import socket
import struct
import time

def run():
    print("Connecting to sender...")
    try:
        s = socket.socket()
        s.connect(('127.0.0.1', 5555))
        print("Connected.")
        
        while True:
            # Read header
            header = b''
            while len(header) < 8:
                chunk = s.recv(8 - len(header))
                if not chunk: raise Exception("Conn closed")
                header += chunk
                
            seq, size = struct.unpack(">LL", header)
            
            # Read payload (discard)
            remaining = size
            while remaining > 0:
                chunk = s.recv(min(4096, remaining))
                if not chunk: raise Exception("Conn closed")
                remaining -= len(chunk)
                
    except Exception as e:
        print(f"Client Error: {e}")

if __name__ == "__main__":
    run()
