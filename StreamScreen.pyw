
import customtkinter as ctk
import logging
import os
import ctypes
from modules.gui import StreamApp

# --- TASKBAR ICON PERSISTENCE (Windows) ---
# Création d'un ID unique basé sur le nom du fichier du script
# Cela permet à chaque app (ex: 'StreamScreen.pyw', 'Autre.pyw') d'avoir sa propre icône dans la barre des tâches
try:
    script_name = os.path.splitext(os.path.basename(__file__))[0]
    myappid = f'obat.{script_name}.v1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("sender_debug.log", mode='w'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SenderGUI")

if __name__ == "__main__":
    app = StreamApp()
    app.mainloop()
