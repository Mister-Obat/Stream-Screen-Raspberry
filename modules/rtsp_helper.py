import os
import requests
import zipfile
import subprocess
import threading
import time
import shutil
import platform
import logging

logger = logging.getLogger("SenderGUI")

class RTSPServer:
    def __init__(self):
        self.process = None
        self.exe_name = "mediamtx.exe" if os.name == 'nt' else "mediamtx"
        self.url = "https://github.com/bluenviron/mediamtx/releases/download/v1.9.1/mediamtx_v1.9.1_windows_amd64.zip"
        self.is_running = False
        self.log_file = None

    def is_installed(self):
        return os.path.exists(self.exe_name)

    def download(self, progress_callback=None):
        try:
            if self.is_installed(): return True
            
            if progress_callback: progress_callback("Téléchargement de MediaMTX...")
            r = requests.get(self.url, stream=True)
            with open("mediamtx.zip", 'wb') as f:
                shutil.copyfileobj(r.raw, f)
            
            if progress_callback: progress_callback("Extraction...")
            with zipfile.ZipFile("mediamtx.zip", 'r') as zip_ref:
                zip_ref.extractall(".")
            
            os.remove("mediamtx.zip")
            if progress_callback: progress_callback("Prêt!")
            return True
        except Exception as e:
            logger.error(f"RTSP Download Error: {e}")
            return False

    def kill_existing(self):
        """Aggressively kill all instances of mediamtx"""
        logger.info("RTSP: Killing existing instances...")
        try:
            # Hide the console window for taskkill
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            # Retry loop for killing
            for i in range(3):
                if os.name == 'nt':
                    subprocess.run(["taskkill", "/F", "/IM", self.exe_name], 
                                   stdout=subprocess.DEVNULL, 
                                   stderr=subprocess.DEVNULL,
                                   startupinfo=startupinfo,
                                   check=False)
                else:
                     subprocess.run(["pkill", "-f", self.exe_name],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                time.sleep(1.0) # Wait for OS to release resources
                
        except Exception as e:
            logger.error(f"Kill Error: {e}")
            
    def start(self):
        if self.process: self.stop()
        
        # Kill any zombies
        self.kill_existing()
        
        if not self.is_installed(): 
            logger.error("RTSP Server not installed.")
            return (False, "Non installé")
        
        try:
            # Check if log file is locked by a zombie
            # Retry opening log file 3 times
            self.log_file = None
            for i in range(3):
                try:
                    self.log_file = open("mediamtx.log", "w")
                    break
                except PermissionError:
                    logger.warning(f"MediaMTX Log Locked (Attempt {i+1}/3). Retrying...")
                    self.kill_existing() # Kill again!
                    time.sleep(1.0)
            
            if self.log_file is None:
                logger.error("Could not open mediamtx.log (File Locked).")
                return (False, "Fichier log verrouillé (Zombie process?)")

            # Define startupinfo to hide console on Windows
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            self.process = subprocess.Popen([f"./{self.exe_name}"], 
                                            startupinfo=startupinfo,
                                            stdout=self.log_file,
                                            stderr=self.log_file)
            
            # Check if it crashed immediately
            try:
                # Wait a bit to see if it stays up
                time.sleep(0.5)
                code = self.process.poll()
                
                if code is not None:
                    # Capture logs to tell user WHY it crashed
                    try:
                        self.log_file.flush()
                        self.log_file.close() # Close to flush to disk
                        
                        with open("mediamtx.log", "r") as f:
                            logs = f.readlines()[-5:] # Last 5 lines
                            log_str = "".join(logs)
                    except:
                        log_str = "Impossible de lire les logs."
                        
                    logger.error(f"RTSP Server exited code {code}. Logs:\n{log_str}")
                    self.is_running = False
                    return (False, f"Code Sortie: {code}\nLogs:\n{log_str}")
            except Exception as e:
                logger.error(f"Process check error: {e}")
                
            self.is_running = True
            logger.info(f"RTSP Server Started (PID: {self.process.pid})")
            return (True, "")
            
        except Exception as e:
            logger.error(f"RTSP Start Error (Exception): {e}")
            self.is_running = False
            return (False, str(e))
            
        return (False, "Erreur inconnue (Fallthrough)")

    def stop(self):
        # Kill object process
        if self.process:
            logger.info("Stopping RTSP Server...")
            try:
                self.process.terminate()
                self.process.wait(timeout=1.0)
            except:
                try: self.process.kill()
                except: pass
            
            self.process = None
            try: self.log_file.close()
            except: pass
            
        # Kill zombies just in case
        self.kill_existing()
        
        self.is_running = False
        logger.info("RTSP Server Stopped")
