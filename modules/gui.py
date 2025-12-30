import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import queue
import threading
import logging
import os
import socket
import time
import paramiko
import webbrowser
from modules.config import state
from modules.core import stream_thread_func
from modules.custom_utils import TextHandler, get_monitors

logger = logging.getLogger("SenderGUI")

class StreamApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # Log Queue init
        self.log_queue = queue.Queue()
        self.process_log_queue()
        
        # Security: Kill any zombie RTSP servers from previous runs
        state.rtsp_server.kill_existing()
        
        # Setup
        # Setup
        self.title("Stream Screen")
        self.geometry("500x800")
        
        # --- DESIGN SYSTEM "MIDNIGHT INDIGO" ---
        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("dark-blue")
        
        # Color Palette
        self.C_BG = "#0B0E14"       # Deep Void
        self.C_CARD = "#151B26"     # Soft Charcoal
        self.C_CARD_HOVER = "#1E2532"
        self.C_PRIMARY = "#5865F2"  # Blurple (Modern Tech)
        self.C_ACCENT = "#00B0FF"   # Electric Blue
        self.C_SUCCESS = "#00D26A"  # Vibrant Green
        self.C_DANGER = "#F23F42"   # Soft Red
        self.C_WARN = "#FFD600"     # Amber
        self.C_TEXT = "#F8F9FA"     # Almost White

        self.C_TEXT_DIM = "#8A939B" # Muted Text
        self.C_BORDER = "#2B3240"
        
        self.configure(fg_color=self.C_BG)
        
        # Fonts
        self.F_HERO = ("Segoe UI Display", 28, "bold")
        self.F_H1 = ("Segoe UI", 16, "bold")
        self.F_H2 = ("Segoe UI Semibold", 13)
        self.F_BODY = ("Segoe UI", 12)
        self.F_SMALL = ("Segoe UI", 10)

        # Icon
        try:
            if os.path.exists("stream4.ico"): self.iconbitmap("stream4.ico")
        except: pass
        
        # --- HEADER HERO ---
        # Removed as requested



        # --- NAVIGATION (Pill Style) ---
        self.tabs = ctk.CTkTabview(self, 
                                   fg_color="transparent", 
                                   text_color="#FFFFFF",
                                   segmented_button_fg_color="#1F2937",
                                   segmented_button_selected_color=self.C_PRIMARY,
                                   segmented_button_selected_hover_color=self.C_PRIMARY,
                                   segmented_button_unselected_color="#1F2937",
                                   segmented_button_unselected_hover_color="#374151",
                                   corner_radius=20,
                                   height=60)
                                   
        self.tabs.pack(fill="both", expand=True, padx=20, pady=(20, 0))
        self.tabs._segmented_button.configure(font=("Segoe UI", 12, "bold"), height=35)
        
        self.tab_stream = self.tabs.add("LIVE")
        self.tab_pi = self.tabs.add("RASPBERRY")
        self.tab_other = self.tabs.add("WEBRTC")
        self.tab_console = self.tabs.add("LOGS")
        self.tab_info = self.tabs.add("INFOS")
        
        # Enable Scrollable Frame for Stream Tab to handle height
        
        # === TAB: STREAM (Dashboard) ===
        
        # 1. STATUS CARD (The "Big Button" Area)
        self.card_status = ctk.CTkFrame(self.tab_stream, fg_color=self.C_CARD, corner_radius=16, border_width=1, border_color=self.C_BORDER)
        self.card_status.pack(fill="x", pady=10)
        
        # Mini Title Removed

        
        self.btn_start = ctk.CTkButton(self.card_status, 
                                       text="LANCER LA DIFFUSION", 
                                       font=("Segoe UI", 16, "bold"), 
                                       height=50, 
                                       fg_color="#0D2616", 
                                       border_width=1,
                                       border_color=self.C_SUCCESS,
                                       text_color=self.C_SUCCESS,
                                       hover_color="#183622", 
                                       corner_radius=12,
                                       command=self.toggle_stream)

        self.btn_start.pack(fill="x", padx=20, pady=20)


        # 2. VIDEO SETTINGS CARD (Compact)
        self.card_video = ctk.CTkFrame(self.tab_stream, fg_color=self.C_CARD, corner_radius=16, border_width=1, border_color=self.C_BORDER)
        self.card_video.pack(fill="x", pady=10)
        
        ctk.CTkLabel(self.card_video, text="SIGNAL VIDÉO", font=self.F_SMALL, text_color=self.C_TEXT_DIM).pack(anchor="w", padx=20, pady=(15, 10))
        
        # Unified Grid for Video Settings (2x2)
        frm_grid = ctk.CTkFrame(self.card_video, fg_color="transparent")
        frm_grid.pack(fill="x", padx=15, pady=(2, 10))
        frm_grid.columnconfigure(0, weight=1)
        frm_grid.columnconfigure(1, weight=1)
        
        # Row 0: Monitor | Engine
        self.mons = get_monitors()
        self.opt_mon = ctk.CTkOptionMenu(frm_grid, values=self.mons, command=self.on_mon_change, fg_color="#2B3240", button_color="#3B4252", button_hover_color="#434C5E", text_color="white")
        self.opt_mon.grid(row=0, column=0, sticky="ew", padx=(0, 5), pady=(0, 10))
        if state.monitor_idx < len(self.opt_mon._values): self.opt_mon.set(self.opt_mon._values[state.monitor_idx])

        self.opt_engine = ctk.CTkOptionMenu(frm_grid, values=["GPU NVIDIA", "CPU"], command=self.on_engine_change, fg_color="#2B3240", button_color="#3B4252")
        self.opt_engine.grid(row=0, column=1, sticky="ew", padx=(5, 0), pady=(0, 10))
        # Init logic
        if state.backend == "DXCam": self.opt_engine.set("GPU NVIDIA")
        else: self.opt_engine.set("CPU")
        
        # Row 1: Preset | Resolution
        self.opt_preset = ctk.CTkOptionMenu(frm_grid, command=self.on_preset_change, fg_color="#2B3240", button_color="#3B4252")
        self.opt_preset.grid(row=1, column=0, sticky="ew", padx=(0, 5))
        self.update_preset_options()
        
        self.opt_res = ctk.CTkOptionMenu(frm_grid, values=[
            "Native", "360p", "480p", "540p", "720p", "900p", "1080p", "1440p", "4K"
        ], command=self.on_res_change, fg_color="#2B3240", button_color="#3B4252")
        self.opt_res.grid(row=1, column=1, sticky="ew", padx=(5, 0))
        self.opt_res.set(state.resolution)




        # Sliders Group
        self.frm_slds = ctk.CTkFrame(self.card_video, fg_color="transparent")
        self.frm_slds.pack(fill="x", padx=10, pady=10)
        
        # FPS
        self.lbl_fps_val = ctk.CTkLabel(self.frm_slds, text=f"{state.fps} FPS", font=self.F_H2, text_color=self.C_ACCENT)
        self.lbl_fps_val.grid(row=0, column=1, sticky="e", padx=10)
        ctk.CTkLabel(self.frm_slds, text="Images par seconde", font=self.F_BODY).grid(row=0, column=0, sticky="w", padx=10)
        self.sld_fps = ctk.CTkSlider(self.frm_slds, from_=5, to=120, number_of_steps=115, command=self.on_fps_change, progress_color=self.C_ACCENT, button_color=self.C_ACCENT, button_hover_color=self.C_ACCENT)
        self.sld_fps.set(state.fps)
        self.sld_fps.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 15))


        # Bitrate
        self.lbl_bit_val = ctk.CTkLabel(self.frm_slds, text=f"{state.bitrate_mbps:.1f} Mbps", font=self.F_H2, text_color=self.C_PRIMARY)
        self.lbl_bit_val.grid(row=2, column=1, sticky="e", padx=10)
        ctk.CTkLabel(self.frm_slds, text="Débit", font=self.F_BODY).grid(row=2, column=0, sticky="w", padx=10)
        self.sld_bit = ctk.CTkSlider(self.frm_slds, from_=0.1, to=25.0, number_of_steps=249, command=self.on_bitrate_change, progress_color=self.C_PRIMARY, button_color=self.C_PRIMARY, button_hover_color=self.C_PRIMARY)
        self.sld_bit.set(state.bitrate_mbps)
        self.sld_bit.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 15))


        # Latency (Restored)
        self.lbl_lat = ctk.CTkLabel(self.frm_slds, text=f"Buffering: {state.latency_value}%", font=self.F_H2, text_color=self.C_WARN)
        self.lbl_lat.grid(row=4, column=1, sticky="e", padx=10)
        ctk.CTkLabel(self.frm_slds, text="Latence", font=self.F_BODY).grid(row=4, column=0, sticky="w", padx=10)
        
        self.sld_lat = ctk.CTkSlider(self.frm_slds, from_=1, to=100, number_of_steps=99, command=self.on_latency_change, progress_color=self.C_WARN, button_color=self.C_WARN, button_hover_color=self.C_WARN)
        self.sld_lat.set(state.latency_value)
        self.sld_lat.grid(row=5, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 15))

        
        # 3. AUDIO CARD
        self.card_audio = ctk.CTkFrame(self.tab_stream, fg_color=self.C_CARD, corner_radius=16, border_width=1, border_color=self.C_BORDER)
        self.card_audio.pack(fill="x", pady=10)
        
        self.sw_audio = ctk.CTkSwitch(self.card_audio, text="FLUX AUDIO (BÊTA)", font=("Segoe UI", 12, "bold"), command=self.on_audio_toggle, progress_color=self.C_SUCCESS)
        self.sw_audio.pack(anchor="w", padx=20, pady=15)
        
        self.frm_audio_opts = ctk.CTkFrame(self.card_audio, fg_color="transparent")
        
        # Audio Content
        self.opt_audio_src = ctk.CTkOptionMenu(self.frm_audio_opts, values=["Loopback (Défaut)", "Microphone", "Mixage Stéréo"], command=self.on_audio_src_change, fg_color="#2B3240", button_color="#3B4252")
        self.opt_audio_src.pack(fill="x", padx=20, pady=(0, 10))
        self.opt_audio_src.set(state.audio_source)

        self.seg_audio_bit = ctk.CTkSegmentedButton(self.frm_audio_opts, values=["64k", "96k", "128k", "192k"], command=self.on_audio_bit_change, selected_color=self.C_PRIMARY)
        self.seg_audio_bit.pack(fill="x", padx=20, pady=(0, 10))
        self.seg_audio_bit.set(state.audio_bitrate)
        
        self.lbl_vol = ctk.CTkLabel(self.frm_audio_opts, text=f"Volume {int(state.audio_volume*100)}%", font=self.F_SMALL, text_color=self.C_TEXT_DIM)
        self.lbl_vol.pack(anchor="w", padx=25)
        self.sld_vol = ctk.CTkSlider(self.frm_audio_opts, from_=0, to=2.0, number_of_steps=100, command=self.on_audio_vol_change, progress_color="white")
        self.sld_vol.set(state.audio_volume)
        self.sld_vol.pack(fill="x", padx=20, pady=(0, 15))
        
        if state.audio_enabled:
            self.sw_audio.select()
            self.frm_audio_opts.pack(fill="x")
        else:
             self.sw_audio.deselect()

        # Dummy Preset Removed (Initialized above)
 
        
        



        
        # === TAB: PI ===
        ctk.CTkLabel(self.tab_pi, text="Configuration SSH", font=("Arial", 14, "bold")).pack(pady=10)
        
        # IP
        frm_ip = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_ip.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frm_ip, text="Adresse ip du Raspberry").pack(side="left")
        self.chk_ip = ctk.CTkCheckBox(frm_ip, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_ip.pack(side="right")
        if state.remember_ip: self.chk_ip.select()
        else: self.chk_ip.deselect()
        
        self.ent_ip = ctk.CTkEntry(self.tab_pi, placeholder_text="IP du Raspberry Pi")
        self.ent_ip.pack(fill="x", padx=10, pady=5)
        if state.pi_ip: self.ent_ip.insert(0, state.pi_ip)
        
        # User
        frm_user = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_user.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frm_user, text="id de connexion").pack(side="left")
        self.chk_user = ctk.CTkCheckBox(frm_user, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_user.pack(side="right")
        if state.remember_user: self.chk_user.select()
        else: self.chk_user.deselect()
        
        self.ent_user = ctk.CTkEntry(self.tab_pi, placeholder_text="Utilisateur (ex: pi)")
        self.ent_user.pack(fill="x", padx=10, pady=5)
        self.ent_user.insert(0, state.pi_user)

        # Pass
        frm_pass = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_pass.pack(fill="x", padx=10, pady=(5,0))
        ctk.CTkLabel(frm_pass, text="mot de passe").pack(side="left")
        self.chk_pass = ctk.CTkCheckBox(frm_pass, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_pass.pack(side="right")
        if state.remember_pass: self.chk_pass.select()
        else: self.chk_pass.deselect()

        self.ent_pass = ctk.CTkEntry(self.tab_pi, placeholder_text="Mot de passe", show="*")
        self.ent_pass.pack(fill="x", padx=10, pady=5)
        self.ent_pass.insert(0, state.pi_pass)
        
        # Path
        frm_path = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        frm_path.pack(fill="x", padx=10, pady=(10,0))
        ctk.CTkLabel(frm_path, text="Chemin fichier python :").pack(side="left")
        self.chk_path = ctk.CTkCheckBox(frm_path, text="Mémoriser", width=20, height=20, font=("Arial", 10))
        self.chk_path.pack(side="right")
        if state.remember_path: self.chk_path.select()
        else: self.chk_path.deselect()

        self.ent_path = ctk.CTkEntry(self.tab_pi, placeholder_text="ex: Desktop/stream_receiver.py")
        self.ent_path.pack(fill="x", padx=10, pady=5)
        self.ent_path.insert(0, state.pi_path)
        
        # Force IP Checkbox (New)
        self.chk_force_ip = ctk.CTkCheckBox(self.tab_pi, text="Forcer IP Source (Avancé)", font=("Arial", 10))
        self.chk_force_ip.pack(anchor="w", padx=10, pady=(5,0))
        
        self.ent_force_ip = ctk.CTkEntry(self.tab_pi, placeholder_text="IP de ce PC (ex: 192.168.0.15)")
        self.ent_force_ip.pack(fill="x", padx=10, pady=5)
        # Default to nothing or detected?
        # Let's pre-fill with detected for convenience
        local_ip_guess = "192.168.x.x"
        try: local_ip_guess = socket.gethostbyname(socket.gethostname())
        except: pass
        self.ent_force_ip.insert(0, local_ip_guess)

        # Buttons Frame
        self.frm_pi_btns = ctk.CTkFrame(self.tab_pi, fg_color="transparent")
        self.frm_pi_btns.pack(fill="x", padx=5, pady=20)
        
        # Grid Layout for cleaner alignment
        self.frm_pi_btns.columnconfigure(0, weight=1)
        self.frm_pi_btns.columnconfigure(1, weight=1)
        
        # Row 0: Launch and Stop (Side by Side)
        self.btn_launch_pi = ctk.CTkButton(self.frm_pi_btns, text="LANCER", font=("Segoe UI", 14, "bold"), height=45, fg_color="#0D2616", border_width=1, border_color=self.C_SUCCESS, text_color=self.C_SUCCESS, hover_color="#183622", command=self.launch_pi)
        self.btn_launch_pi.grid(row=0, column=0, sticky="ew", padx=5, pady=5)

        
        self.btn_stop_pi = ctk.CTkButton(self.frm_pi_btns, text="ARRÊTER", font=("Segoe UI", 14, "bold"), height=45, fg_color="#271818", border_width=1, border_color=self.C_DANGER, text_color=self.C_DANGER, hover_color="#421C1C", command=self.stop_pi_manual)
        self.btn_stop_pi.grid(row=0, column=1, sticky="ew", padx=5, pady=5)
        
        # Row 1: Update (Full Width)
        self.btn_update_pi = ctk.CTkButton(self.frm_pi_btns, text="METTRE À JOUR (SSH)", font=("Segoe UI", 12, "bold"), height=35, fg_color="transparent", border_width=1, border_color=self.C_ACCENT, text_color=self.C_ACCENT, hover_color="#18202A", command=self.update_pi)
        self.btn_update_pi.grid(row=1, column=0, columnspan=2, sticky="ew", padx=5, pady=(10, 0))


        
        # Checkbox for Pi Auto-Restart (Renamed)
        self.chk_auto_pi = ctk.CTkCheckBox(self.tab_pi, text="Tentative de reconnexion automatique (Max 2h)", font=("Segoe UI", 12))
        self.chk_auto_pi.pack(anchor="w", padx=15, pady=10)
        
        # === TAB: DIFFUSION (BROADCAST) ===
        self.frm_links = ctk.CTkFrame(self.tab_other, fg_color="transparent")
        self.frm_links.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Switch RTSP
        self.card_rtsp = ctk.CTkFrame(self.frm_links, fg_color=self.C_CARD, corner_radius=16, border_width=1, border_color=self.C_BORDER)
        self.card_rtsp.pack(fill="x", pady=(0, 20))
        
        self.sw_rtsp = ctk.CTkSwitch(self.card_rtsp, text="DIFFUSION WEB (WEBRTC)", font=("Segoe UI", 12, "bold"), command=self.toggle_rtsp_mode, progress_color=self.C_ACCENT)
        self.sw_rtsp.pack(anchor="w", padx=20, pady=15)
        self.lbl_rtsp_status = ctk.CTkLabel(self.card_rtsp, text="Statut: Éteint", text_color="gray", font=self.F_SMALL)
        self.lbl_rtsp_status.pack(anchor="w", padx=20, pady=(0, 15))

        # Links Area
        local_ip = "127.0.0.1"
        try: local_ip = socket.gethostbyname(socket.gethostname())
        except: pass
        
        def add_copy_link(title, url, color):
            f = ctk.CTkFrame(self.frm_links, fg_color=self.C_CARD, corner_radius=12)
            f.pack(fill="x", pady=5)
            ctk.CTkLabel(f, text=title, font=("Segoe UI", 11, "bold"), text_color=color).pack(anchor="w", padx=15, pady=(10, 0))
            e = ctk.CTkEntry(f, font=("Consolas", 11), fg_color="#0B0E14", border_color="#2B3240")
            e.pack(fill="x", padx=15, pady=10)
            e.insert(0, url)
            return e
            
        self.ent_webrtc_url = add_copy_link("LIEN DE PARTAGE (NAVIGATEUR)", f"http://{local_ip}:8889/stream", self.C_ACCENT)
        # HLS and RTSP/VLC links removed as requested
        
        
        # === TAB: CONSOLE (Logs) ===
        self.frm_cons_ctrl = ctk.CTkFrame(self.tab_console, fg_color="transparent")
        self.frm_cons_ctrl.pack(fill="x", padx=10, pady=(20, 10))
        
        # Redesigned Segmented Button (Pill Style - Matching Main Menu)
        self.seg_console = ctk.CTkSegmentedButton(self.frm_cons_ctrl, 
                                                  values=["Local (PC)", "Raspberry Pi (SSH)", "WEBRTC"], 
                                                  command=self.on_log_source_change, 
                                                  fg_color="#1F2937",
                                                  selected_color=self.C_PRIMARY,
                                                  selected_hover_color=self.C_PRIMARY,
                                                  unselected_color="#1F2937",
                                                  unselected_hover_color="#374151",
                                                  font=("Segoe UI", 12, "bold"),
                                                  height=32,
                                                  corner_radius=20)
        self.seg_console.pack(fill="x", padx=10)

        self.seg_console.set("Local (PC)")
        
        self.txt_console = ctk.CTkTextbox(self.tab_console, font=("Consolas", 11), fg_color="#0B0E14", text_color="#D1D5DB", activate_scrollbars=True, border_color=self.C_BORDER, border_width=1)
        self.txt_console.pack(fill="both", expand=True, padx=10, pady=(0, 20))
        self.txt_console.configure(state="disabled")
        
        self.text_handler = TextHandler(self) 
        self.text_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(self.text_handler)


        # === TAB: INFO ===
        self.frm_info_c = ctk.CTkFrame(self.tab_info, fg_color="transparent")
        self.frm_info_c.place(relx=0.5, rely=0.5, anchor="center")
        
        # Header Info
        ctk.CTkLabel(self.frm_info_c, text="Stream Screen", font=self.F_HERO).pack(pady=(5, 2))
        ctk.CTkLabel(self.frm_info_c, text="v4.0.0 | Midnight Edition", font=self.F_SMALL, text_color=self.C_ACCENT).pack()
        
        # Credits
        ctk.CTkLabel(self.frm_info_c, text="Développé par Mister Obat", font=self.F_H1).pack(pady=(20, 2))
        ctk.CTkLabel(self.frm_info_c, text="Avec l'assistance de l'Intelligence Artificielle", font=("Segoe UI", 11, "italic"), text_color="gray").pack()

        # Donate
        self.btn_donate = ctk.CTkButton(self.frm_info_c, 
                                        text="Soutenir le projet (PayPal)", 
                                        font=("Segoe UI", 14, "bold"),
                                        fg_color="#FFC439", 
                                        text_color="black",
                                        hover_color="#F4B400",
                                        height=50,
                                        width=280,
                                        corner_radius=25,
                                        command=lambda: webbrowser.open("https://www.paypal.com/paypalme/creaprisme"))
        self.btn_donate.pack(pady=(30, 10))
        
        ctk.CTkLabel(self.frm_info_c, text="Licence Open Source (AGPL-3.0)", font=("Consolas", 10), text_color="gray").pack()


        



        
        # Footer
        self.frm_footer = ctk.CTkFrame(self, fg_color="transparent")
        self.frm_footer.pack(side="bottom", fill="x", padx=10, pady=5)
        
        self.lbl_status = ctk.CTkLabel(self.frm_footer, text="Status: Prêt", text_color="gray")
        self.lbl_status.pack(side="left")
        
        self.lbl_drop = ctk.CTkLabel(self.frm_footer, text="Perte: 0%", text_color="gray")
        self.lbl_drop.pack(side="right")
        
        # Start Monitor Loop
        # Start Monitor Loop
        self.watchdog_loss_counter = 0
        self.watchdog_cooldown_until = 0
        self.monitor_stats()
        
        # Shutdown Protocol
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        try:
            # 1. Stop RTSP Server (Critical)
            if state.rtsp_server.is_running:
                logger.info("Shutdown: Killing MediaMTX...")
                state.rtsp_server.stop()
            
            # 2. Stop Streams
            state.streaming = False
            state.remote_log_running = False # Stop SSH Log threads
            
            # 3. Kill Dxcam/Internal threads (Best effort)
            # Dxcam needs explicit stop sometimes
            # We rely on daemon threads usually, but let's be safe.
            
            logger.info("Shutdown: Closing App...")
        except Exception as e:
            print(f"Shutdown Error: {e}")
            
        self.destroy()
        # Force kill to ensure no zombie python processes (sometimes threads hang)
        import os
        os._exit(0)

    def process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.txt_console.configure(state="normal")
                self.txt_console.insert("end", msg)
                self.txt_console.see("end")
                self.txt_console.configure(state="disabled")
        except queue.Empty:
            pass
        self.after(100, self.process_log_queue)

    def monitor_stats(self):
        if state.streaming:
            client_status = "Connecté" if state.client_connected else "En attente..."
            msg = f"Status: En Ligne ({client_status}) | {state.current_fps} FPS | {state.current_mbps:.2f} Mbps"
            color = "green" if state.client_connected else "orange"
        else:
            msg = "Status: Arrêté"
            color = "gray"
            
        if self.btn_start.cget("text") == "STOPPER LE FLUX": # Only update if running to avoid overriding connection messages
             self.lbl_status.configure(text=msg, text_color=color)
        
        self.after(500, self.monitor_stats)
    
    def on_mon_change(self, choice):
        idx = self.mons.index(choice)
        state.monitor_idx = idx
        self.save_config()

    def on_engine_change(self, choice):
        if "NVIDIA" in choice:
            state.backend = "DXCam"
            state.codec_choice = "auto" # Auto prefers NVENC
        else:
            state.backend = "MSS"
            state.codec_choice = "x264"
        self.update_preset_options()
        self.save_config()
        
    def update_preset_options(self):
        if state.codec_choice == "x264" or state.backend == "MSS":
            # X264 Options
            self.opt_preset.configure(values=["Performance (Ultrafast)", "Equilibré (Superfast)", "Qualité (Veryfast)"])
            # Map internal to display
            val = "Performance (Ultrafast)"
            if state.encoder_preset == "balanced": val = "Equilibré (Superfast)"
            elif state.encoder_preset == "quality": val = "Qualité (Veryfast)"
            self.opt_preset.set(val)
        else:
            # NVENC Options
            self.opt_preset.configure(values=["Performance (P1 - Latence Min)", "Equilibré (P3)", "Qualité (P4)"])
             # Map internal to display
            val = "Performance (P1 - Latence Min)"
            if state.encoder_preset == "balanced": val = "Equilibré (P3)"
            elif state.encoder_preset == "quality": val = "Qualité (P4)"
            self.opt_preset.set(val)
            
    def on_preset_change(self, choice):
        if "Performance" in choice: state.encoder_preset = "fast"
        elif "Equilibré" in choice: state.encoder_preset = "balanced"
        elif "Qualité" in choice: state.encoder_preset = "quality"
        self.save_config()

    def on_fps_change(self, val):
        state.fps = int(val)
        self.lbl_fps_val.configure(text=f"FPS Cible: {state.fps}")
        # Note: stream_thread will pick this up automatically now
        self.save_config()

    def on_bitrate_change(self, val):
        state.bitrate_mbps = float(val)
        self.lbl_bit_val.configure(text=f"{state.bitrate_mbps:.1f} Mbps")
        self.save_config()

    def on_latency_change(self, val):
        state.latency_value = int(val)
        self.lbl_lat.configure(text=f"Buffering: {state.latency_value}%")
        self.save_config()

    def on_res_change(self, choice):
        state.resolution = choice
        if choice == "360p": state.target_w, state.target_h = 640, 360
        elif choice == "480p": state.target_w, state.target_h = 854, 480
        elif choice == "540p": state.target_w, state.target_h = 960, 540
        elif choice == "720p": state.target_w, state.target_h = 1280, 720
        elif choice == "900p": state.target_w, state.target_h = 1600, 900
        elif choice == "1080p": state.target_w, state.target_h = 1920, 1080
        elif choice == "1440p": state.target_w, state.target_h = 2560, 1440
        elif choice == "4K": state.target_w, state.target_h = 3840, 2160
        elif choice == "Native": state.target_w, state.target_h = 0, 0
        logger.info(f"Resolution Changed to: {choice} ({state.target_w}x{state.target_h})")
        self.save_config()

    def on_audio_toggle(self):
        state.audio_enabled = bool(self.sw_audio.get())
        if state.audio_enabled:
            self.frm_audio_opts.pack(fill="x", pady=(0,5))
        else:
            self.frm_audio_opts.pack_forget()
        self.save_config()

    def on_audio_src_change(self, val):
        state.audio_source = val
        self.save_config()

    def on_audio_bit_change(self, val):
        state.audio_bitrate = val
        self.save_config()

    def on_audio_vol_change(self, val):
        state.audio_volume = float(val)
        self.lbl_vol.configure(text=f"Volume: {int(state.audio_volume*100)}%")
        self.save_config()

    def save_config(self):
        state.audio_enabled = bool(self.sw_audio.get())
        state.audio_source = self.opt_audio_src.get()
        state.audio_bitrate = self.seg_audio_bit.get()
        state.audio_volume = self.sld_vol.get()
        
        state.pi_ip = self.ent_ip.get()
        state.pi_user = self.ent_user.get()
        state.pi_pass = self.ent_pass.get()
        state.pi_path = self.ent_path.get()
        
        state.remember_ip = bool(self.chk_ip.get())
        state.remember_user = bool(self.chk_user.get())
        state.remember_pass = bool(self.chk_pass.get())
        state.remember_path = bool(self.chk_path.get())
        
        state.save()

    def toggle_stream(self):
        if not state.streaming:
            # START
            state.streaming = True
            self.th = threading.Thread(target=stream_thread_func, daemon=True)
            self.th.start()
            self.btn_start.configure(text="STOPPER LE FLUX", fg_color="#271818", border_color=self.C_DANGER, text_color=self.C_DANGER, hover_color="#421C1C")
            self.lbl_status.configure(text="Status: Streaming en cours...", text_color=self.C_SUCCESS)
        
        else:
            # STOP
            state.streaming = False
            self.btn_start.configure(text="LANCER LA DIFFUSION", fg_color="#0D2616", border_color=self.C_SUCCESS, text_color=self.C_SUCCESS, hover_color="#183622")
            self.lbl_status.configure(text="Status: Arrêté", text_color="gray")


        

        # Start Metric Update Loop
        self.update_metrics()

    def toggle_rtsp_mode(self):
        # Logic: If turning ON
        if self.sw_rtsp.get():
            state.rtsp_mode = True
            
            # Create Modal
            self.loading_win = ctk.CTkToplevel(self)
            self.loading_win.title("Démarrage")
            self.loading_win.geometry("300x150")
            self.loading_win.attributes("-topmost", True)
            self.loading_win.transient(self) # Lock to main window
            self.loading_win.grab_set() # Modal interaction lock
            
            # Center it
            x = self.winfo_x() + (self.winfo_width() // 2) - 150
            y = self.winfo_y() + (self.winfo_height() // 2) - 75
            self.loading_win.geometry(f"+{x}+{y}")
            
            self.lbl_loading = ctk.CTkLabel(self.loading_win, text="Initialisation...", font=("Segoe UI", 12))
            self.lbl_loading.pack(expand=True)
            self.progress_bar = ctk.CTkProgressBar(self.loading_win, mode="indeterminate", width=200)
            self.progress_bar.pack(pady=20)
            self.progress_bar.start()
            
            # Start Thread
            threading.Thread(target=self._async_rtsp_startup, daemon=True).start()
            
        else:
            # TURNING OFF
            state.rtsp_mode = False
            if state.rtsp_server.is_running:
                state.rtsp_server.stop()
            self.lbl_rtsp_status.configure(text="Statut: Éteint", text_color="gray")

    def _update_loading_text(self, text):
        if hasattr(self, 'lbl_loading') and self.lbl_loading.winfo_exists():
            self.lbl_loading.configure(text=text)

    def _async_rtsp_startup(self):
        try:
            # STEP 1: Check Install
            self.after(0, lambda: self._update_loading_text("Vérification du serveur..."))
            
            if not state.rtsp_server.is_installed():
                self.after(0, lambda: self._update_loading_text("Téléchargement de MediaMTX..."))
                ok = state.rtsp_server.download()
                if not ok:
                    raise Exception("Échec du téléchargement.")
            
            # STEP 2: Start Server
            self.after(0, lambda: self._update_loading_text("Démarrage du serveur..."))
            time.sleep(0.5) # UI Refresh
            
            res = state.rtsp_server.start()
            logger.info(f"DEBUG: start() returned type {type(res)}: {res}")
            
            if res is None:
                raise Exception("Erreur Interne: start() a renvoyé None.")
                
            ok, err_msg = res
            if not ok:
                raise Exception(f"Echec Démarrage Serveur:\n{err_msg}")
                
            # STEP 3: Start Stream
            self.after(0, lambda: self._update_loading_text("Lancement du flux vidéo..."))
            time.sleep(0.5)
            
            if not state.streaming:
                self.after(0, self.toggle_stream)
                
            time.sleep(1.0) # Let it connect
            
            # SUCCESS
            self.after(0, self._rtsp_startup_success)
            
        except Exception as e:
            msg = str(e)
            self.after(0, lambda: self._rtsp_startup_fail(msg))

    def _rtsp_startup_success(self):
        if hasattr(self, 'loading_win'): self.loading_win.destroy()
        self.lbl_rtsp_status.configure(text="Statut: Actif (Serveur en cours)", text_color="green")
        messagebox.showinfo("Succès", "Le serveur de diffusion est en ligne !")

    def _rtsp_startup_fail(self, error_msg):
        if hasattr(self, 'loading_win'): self.loading_win.destroy()
        self.sw_rtsp.deselect()
        state.rtsp_mode = False
        messagebox.showerror("Erreur de Démarrage", f"Impossible de lancer la diffusion :\n\n{error_msg}")
        
    def update_metrics(self):
        if state.streaming:
            # Update Drop Count (Percent)
            l_tcp = state.loss_tcp
            l_rtsp = state.loss_rtsp
            
            col = "green"
            if l_tcp > 1.0 or l_rtsp > 1.0: col = "orange" 
            if l_tcp > 10.0 or l_rtsp > 10.0: col = "red"
            
            txt = f"Perte: RASP {l_tcp:.0f}%"
            if state.rtsp_mode:
                 txt += f" | WEBRTC {l_rtsp:.0f}%"
            
            self.lbl_drop.configure(text=txt, text_color=col)
            
            # --- WATCHDOG LOGIC ---
            pct = state.loss_percent # Uses MAX of both
            # 0. Check Cooldown
            if time.time() < self.watchdog_cooldown_until:
                remaining = int(self.watchdog_cooldown_until - time.time())
                if remaining % 5 == 0: logger.info(f"Watchdog: Cooldown ({remaining}s)...")
                # Reset counter during cooldown to be safe
                self.watchdog_loss_counter = 0
            
            # 1. Check for high loss (indicating dead receiver)
            elif pct >= 99.0:
                self.watchdog_loss_counter += 1
                
                # Global Failure Logic (Persists across re-launches via self)
                if not hasattr(self, "global_failure_timer"): self.global_failure_timer = 0
                self.global_failure_timer += 1
                
                # Limits
                TIME_LIMIT = 120 # 2 minutes by default
                if self.chk_auto_pi.get():
                    TIME_LIMIT = 7200 # 2 Hours Max Safety
                    
                # Have we exceeded total time?
                if self.global_failure_timer > TIME_LIMIT:
                    logger.error("Watchdog: Abandon (Limite de temps de 2h atteinte).")
                    self.toggle_stream() # Stop Local
                    
                    # Safety Kill for Pi
                    if self.chk_auto_pi.get():
                        logger.info("Watchdog: Killing Pi process (Safety Timeout)...")
                        self.stop_pi(silent=True)

                    self.watchdog_loss_counter = 0
                    self.global_failure_timer = 0
                    return

                # Retry Interval (Every 10s)
                # Logic: If we are in "Loss" state, we trigger every 10s.
                RETRY_INTERVAL = 10
                
                if self.watchdog_loss_counter >= RETRY_INTERVAL:
                    logger.warning(f"Watchdog: Tentative de reconnexion... (Global: {self.global_failure_timer}s)")
                    
                    if state.rtsp_mode:
                        logger.info("Watchdog: RTSP mode, disabling watchdog trigger.")
                        self.watchdog_loss_counter = 0 
                    else:
                        state.watchdog_triggered = True
                        
                        # Trigger action based on current state
                        if state.streaming and not state.pi_streaming: 
                            logger.info("Watchdog Action: Restarting Local...")
                            self.restart_local()
                        elif state.streaming and state.pi_streaming: 
                            logger.info("Watchdog Action: Restarting Pi...")
                            self.restart_pi()
                        else: 
                            logger.info("Watchdog Action: Starting Receiver...")
                            self.launch_pi()
                        
                        # Reset Interval Counter (but NOT global timer)
                        self.watchdog_loss_counter = 0 

            else:
                 # Success! Reset counters
                 self.watchdog_loss_counter = 0
                 self.global_failure_timer = 0

        else:
             self.watchdog_loss_counter = 0
             self.global_failure_timer = 0
             # Stream stopped (Auto-stop or other), reset UI
             if self.btn_start.cget("text") == "STOPPER LE FLUX":
                 self.toggle_stream() # Logic handles UI reset
            
        self.after(1000, self.update_metrics)


    # --- SSH HELPER ---
    def _create_ssh_client(self, ip, user, pwd):
        """
        Creates and connects an SSH Client with robust settings.
        Forces password auth to avoid key/agent issues.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        # INCREASED TIMEOUTS & FORCE PASSWORD
        # banner_timeout=30: Helps if server is slow to greet
        # timeout=10: Connection timeout
        # look_for_keys=False: Don't try local keys (avoids auth failure if server wants pwd)
        # allow_agent=False: Don't use SSH agent
        client.connect(ip, username=user, password=pwd if pwd else None, 
                       timeout=10, banner_timeout=30, 
                       look_for_keys=False, allow_agent=False)
        return client

    def launch_pi(self):
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        path = self.ent_path.get()
        
        if not ip or not user:
            messagebox.showerror("Erreur", "IP et Utilisateur requis!")
            print("ERROR: Missing IP or User")
            return
        
        self.save_config()

        # AUTO-START STREAM IF NEEDED
        async_start = False
        
        # Force Protocol for Pi (Always Header+Data)
        if state.compatibility_mode:
             logger.info("Launch Pi: Forcing Compatibility Mode OFF (Need Header+Data)")
             state.compatibility_mode = False
        
        if not state.streaming:
            logger.info("Auto-starting Stream for Pi... (Forcing TCP Mode)")
 
            # state.compatibility_mode = False # Already handled above
            self.toggle_stream()
            async_start = True
        
        # Wait a moment for server to bind if just started
        if async_start: time.sleep(2.0)
        
        
        def ssh_task():
            try:
                # 1. Get Local IP to help Receiver
                local_ip = ""
                
                # CHECK FORCE IP
                if self.chk_force_ip.get():
                     local_ip = self.ent_force_ip.get()
                     logger.info(f"Using Forced Local IP: {local_ip}")
                else:
                    # AUTO-DETECT
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.settimeout(2.0)
                    try:
                        # Method A: Connect to Pi (Best)
                        logger.info(f"Method A: Connecting to Pi IP {ip}...")
                        s.connect((ip, 22)) 
                        local_ip = s.getsockname()[0]
                        logger.info(f"Method A Success: {local_ip}")
                    except Exception as eA: 
                        logger.warning(f"Method A Failed: {eA}")
                        # Method B: Connect to Public DNS (Google)
                        try:
                            logger.info("Method B: Connecting to 8.8.8.8...")
                            s.connect(("8.8.8.8", 80))
                            local_ip = s.getsockname()[0]
                            logger.info(f"Method B Success: {local_ip}")
                        except Exception as eB:
                            logger.warning(f"Method B Failed: {eB}")
                            # Method C: Hostname
                            try: 
                                local_ip = socket.gethostbyname(socket.gethostname())
                                logger.info(f"Method C (Hostname): {local_ip}")
                            except: pass
                    finally: s.close()

                if not local_ip:
                     logger.error("CRITICAL: Failed to detect ANY local IP. Receiver will likely timeout.")

                # 2. Kill previous instances explicitly first
                # OPTIMIZATION: Only pkill 1 out of 3 times to spare Pi CPU/SSH time
                if not hasattr(self, "pi_launch_count"): self.pi_launch_count = 0
                self.pi_launch_count += 1
                
                do_kill = True
                # "1 fois sur 3" -> 1st time (1), 4th time (4), etc.
                if self.pi_launch_count > 1 and (self.pi_launch_count - 1) % 3 != 0:
                     do_kill = False
                     
                if do_kill:
                    try:
                        logger.info(f"Killing previous remote instances... (Launch #{self.pi_launch_count}: KILL)")
                        client.exec_command("pkill -9 -f stream_receiver.py")
                        time.sleep(1.0) # Wait for cleanup
                    except: pass
                else:
                    logger.info(f"Skipping kill to speed up retry... (Launch #{self.pi_launch_count}: SKIP)")

                # 3. Prepare Command
                # Use >> (append) to keep file alive.
                # Added 'echo' to verify command execution in logs
                # Removed pkill from chain as it's done above
                base_cmd = f"echo '--- LAUNCHING PYTHON SCRIPT ---' >> stream_receiver.log; export DISPLAY=:0 && export XAUTHORITY=~/.Xauthority && python3 -u {path} >> stream_receiver.log 2>&1 &"
                
                final_cmd = base_cmd
                
                # 4. Add Arguments
                # If script ends with .py, pass local_ip for reverse connection
                pass_arg = path.endswith(".py")
                if not pass_arg:
                    pass_arg = self.check_file_is_python(path)
                    if not pass_arg:
                        logger.warning(f"Not passing IP arg: Path '{path}' does not end with .py")

                # Prepare flags
                retry_flag = ""
                # SMART CONFIG: Enable ZOMBIE MODE if Pi Auto-Restart is checked.
                is_zombie_requested = self.chk_auto_pi.get()
                
                if is_zombie_requested:
                     retry_flag = " --retry"
                     logger.info("Auto-Retry: ENABLED (Zombie Mode Active via UI)")
                else:
                     logger.info("Auto-Retry: DISABLED (No Auto-Restart checked)")

                if pass_arg:
                     final_cmd = f"echo '--- LAUNCHING PYTHON SCRIPT (ARG) ---' >> stream_receiver.log; export DISPLAY=:0 && export XAUTHORITY=~/.Xauthority && python3 -u {path} {local_ip}{retry_flag} >> stream_receiver.log 2>&1 &"
                else:
                     logger.warning("Using Base Cmd (No IP arg) -> Receiver will use Auto-Discovery.")
                
                logger.info(f"FINAL CMD: || {final_cmd} ||")
                
                logger.info(f"SSH Connecting to {ip}...")
                self.lbl_status.configure(text="SSH: Connexion...", text_color="orange")
                
                # USE HELPER
                client = self._create_ssh_client(ip, user, pwd)
                
                logger.info(f"SSH Executing: {final_cmd}")
                self.lbl_status.configure(text="SSH: Exécution...", text_color="blue")
                
                stdin, stdout, stderr = client.exec_command(final_cmd, get_pty=False) # No PTY for background task
                
                # Check for immediate errors (waiting a bit)
                time.sleep(1.0)
                
                if stdout.channel.recv_ready():
                    out = stdout.channel.recv(1024).decode().strip()
                    if out: logger.info(f"SSH Check: {out}")
                    if "Error" in out or "found" in out or "denied" in out:
                         # Safe UI Update
                         self.after(0, lambda: messagebox.showerror("Erreur SSH", f"Retour: {out}"))
                         self.lbl_status.configure(text="SSH: Erreur (voir logs)", text_color="red")
                         client.close()
                         return

                self.lbl_status.configure(text="SSH: Lancé avec succès!", text_color="green")
                # Warning: Closing client kills the process if not persistent. 
                # Keeping it open or letting it detach is tricky. 
                # For now, we keep the object but the thread ends. The GC might close it.
                # Let's detach properly or keep log open?
                # User wants 'simple'.
                # Use nohup trick if we want to close connection
                # But 'exec_command' blocks until channel closed? No, it returns streams.
                
            except Exception as e:
                logger.error(f"SSH Fail: {e}")
                self.lbl_status.configure(text="SSH Erreur!", text_color="red")
                self.after(0, lambda: messagebox.showerror("Erreur Connexion", str(e)))
        
        threading.Thread(target=ssh_task, daemon=True).start()



    def stop_pi_manual(self):
         # Always silent for success, errors still show if silent=False (default behavior kept for errors but success silenced manually in function)
         # Actually, user said "totalement supprimer toutes les popups". 
         # So we pass silent=True to be sure? 
         # Wait, if I pass silent=True, errors are also hidden. User likely wants to see errors.
         # But wants "Processus arrêté" popup gone.
         # I commented out the success popup above, so silent=False is fine for errors.
         self.stop_pi(silent=False)

    def stop_pi(self, silent=False):
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        
        if not ip or not user:
             if not silent: messagebox.showerror("Erreur", "IP et Utilisateur requis!")
             return

        def task():
            try:
                self.lbl_status.configure(text="SSH: Arrêt en cours...", text_color="orange")
                client = self._create_ssh_client(ip, user, pwd)
                
                # Kill all python instances of stream_receiver
                # Using pkill -f to match full command line
                cmd = "pkill -f stream_receiver.py"
                logger.info(f"SSH Stop: {cmd}")
                
                client.exec_command(cmd)
                time.sleep(0.5)
                client.close()
                
                self.lbl_status.configure(text="SSH: Processus stoppé.", text_color="green")
                # if not silent: messagebox.showinfo("Succès", "Processus arrêté sur le Raspberry Pi.")
            except Exception as e:
                logger.error(f"SSH Stop Error: {e}")
                self.lbl_status.configure(text="SSH: Erreur Stop", text_color="red")
                if not silent: 
                    self.after(0, lambda: messagebox.showerror("Erreur", str(e)))

        threading.Thread(target=task, daemon=True).start()

    def update_pi(self):
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        remote_path = self.ent_path.get() # e.g. Desktop/stream_receiver.py
        
        if not ip or not user: return messagebox.showerror("Erreur", "IP et User requis")
        
        # Local File
        local_file = "stream_receiver.py" 
        # Assume it's in the same dir as the main script (StreamScreen.pyw)
        # Since we run from root, os.path.abspath("stream_receiver.py") should be fine.
        # But wait, modules/gui.py is deeper.
        # But we run from root.
        if not os.path.exists(local_file):
            return messagebox.showerror("Erreur", f"Fichier local introuvable:\n{local_file}")
            
        def task():
            try:
                self.lbl_status.configure(text="SFTP: Connexion...", text_color="orange")
                client = self._create_ssh_client(ip, user, pwd)
                
                sftp = client.open_sftp()
                
                # Resolve Home dir if path is relative
                # Paramiko SFTP starts at user home usually, ensuring it.
                # If remote path is absolute, it's fine. If relative, it's relative to home.
                
                target_path = remote_path
                # Basic check for empty path
                if not target_path or target_path == ".": target_path = "stream_receiver.py"
                
                # Resolve Home dir verification
                # Remove old file first to be sure
                self._safe_log(f"[UPDATE] Suppression ancienne version: {target_path}")
                try: client.exec_command(f"rm {target_path}")
                except: pass
                
                self.lbl_status.configure(text="SFTP: Upload...", text_color="blue")
                logger.info(f"Uploading {local_file} -> {target_path}")
                
                sftp.put(local_file, target_path)
                sftp.close()
                
                self.lbl_status.configure(text="SFTP: Succès!", text_color="green")
                self.after(0, lambda: messagebox.showinfo("Mise à jour", "Le fichier a été mis à jour avec succès sur le Raspberry Pi."))
                
            except Exception as e:
                logger.error(f"SFTP Error: {e}")
                self.lbl_status.configure(text="SFTP: Echec", text_color="red")
                # Fix: Capture 'e' immediately as string because 'e' is deleted after except block
                err_msg = str(e)
                self.after(0, lambda: messagebox.showerror("Erreur Upload", err_msg))
                
        threading.Thread(target=task, daemon=True).start()

    def restart_local(self):
        def task():
            if state.streaming:
                logger.info("Restart Local: Stopping...")
                self.toggle_stream() # Disables streaming
                time.sleep(1.0)
            
            logger.info("Restart Local: Starting...")
            self.toggle_stream() # Enables streaming
        
        threading.Thread(target=task, daemon=True).start()

    def restart_pi(self):
        # Optimized Restart: process kill is handled by launch_pi now.
        # We just need to reset local stream to clear buffers and call launch.
        
        def task():
            # Reset Local Stream (STOP)
            if state.streaming:
                logger.info("Watchdog Action: Resetting Local Stream for clean Start...")
                # UI Safe toggle
                self.toggle_stream() 
            
            self.lbl_status.configure(text="Relance Pi: En cours...", text_color="orange")
            time.sleep(1.0) # Short wait for socket cleanup
            
            # call launch_pi (on main thread to be safe with UI getters)
            self.after(0, self.launch_pi)
            
        threading.Thread(target=task, daemon=True).start()

    def on_log_source_change(self, value):
        # Stop any remote/file log threads
        self.remote_log_running = False
        self.text_handler.enabled = False
        
        # Clear Console
        self.txt_console.configure(state="normal")
        self.txt_console.delete("1.0", "end")
        self.txt_console.configure(state="disabled")
        
        if value == "Local (PC)":
            self.text_handler.enabled = True
            logger.info("Console: Mode Local Active")
            
        elif value == "Raspberry Pi (SSH)":
            self.start_remote_log()
            
        elif value == "Serveur Diffusion":
            self.start_file_tail("mediamtx.log")

    def start_file_tail(self, filename):
        self.remote_log_running = True
        
        def task():
            if not os.path.exists(filename):
                self._safe_log(f"Fichier log introuvable: {filename}\nLe serveur n'a peut-être pas démarré.")
                return

            try:
                self._safe_log(f"Lecture du log: {filename}...\n")
                f = open(filename, "r")
                # Go to end? Or read all? User wants to see recent logs.
                # Let's read last 2000 bytes approx?
                f.seek(0, 2) # End
                size = f.tell()
                f.seek(max(0, size - 4000), 0) # Rewind a bit
                
                while self.remote_log_running and self.seg_console.get() == "Serveur Diffusion":
                    line = f.readline()
                    if line:
                        self._safe_log(line)
                    else:
                        time.sleep(0.5)
                f.close()
            except Exception as e:
                self._safe_log(f"Erreur Lecture Fichier: {e}")
        
        threading.Thread(target=task, daemon=True).start()

    def _safe_log(self, txt):
        def append():
             self.txt_console.configure(state="normal")
             self.txt_console.insert("end", txt)
             self.txt_console.see("end")
             self.txt_console.configure(state="disabled")
        try: self.txt_console.after(0, append)
        except: pass

    def start_remote_log(self):
        self.remote_log_running = True
        
        ip = self.ent_ip.get()
        user = self.ent_user.get()
        pwd = self.ent_pass.get()
        
        if not ip or not user:
             self._safe_log("\n[GUI] ERREUR: Configurer IP/User dans l'onglet Raspberry Pi d'abord.\n")
             self.seg_console.set("Local (PC)")
             self.on_log_source_change("Local (PC)")
             return
             
        def task():
            client = None
            try:
                # Clear previous logs to avoid confusion
                self.after(0, lambda: self.txt_console.delete("1.0", "end"))
                self._safe_log(f"--- FETCHING REMOTE LOGS ({ip}) ---\n")
                
                client = self._create_ssh_client(ip, user, pwd)
                # Use -n 100 to see history, -f to follow. 
                # Note: 'tail -f' might lose file if recreated (log rotation/overwrite).
                # ideally 'tail -F' but busybox/pi might not support it fully or it behaves differently via paramiko.
                # simpler: just read.
                stdin, stdout, stderr = client.exec_command("tail -n 100 -f stream_receiver.log")
                
                logger.info(f"SSH Log: Monitoring stream_receiver.log on {ip}...")

                while self.remote_log_running and self.seg_console.get() == "Raspberry Pi (SSH)":
                    # Check if channel is active
                    if stdout.channel.recv_ready():
                        line = stdout.channel.recv(4096).decode(errors='replace')
                        if line: self._safe_log(line)
                    
                    if stdout.channel.exit_status_ready():
                        # Command finished?
                        if not stdout.channel.recv_ready():
                             logger.warning("SSH Log: Remote command finished.")
                             break
                    
                    time.sleep(0.1)
                
                client.close()
            except Exception as e:
                self._safe_log(f"\n[GUI] Erreur SSH Logs: {e}")
                
        threading.Thread(target=task, daemon=True).start()

    def detect_local_ip(self):
        try:
           s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
           s.connect(("8.8.8.8", 80))
           ip = s.getsockname()[0]
           s.close()
           return ip
        except: return "127.0.0.1"
