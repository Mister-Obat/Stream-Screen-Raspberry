# Stream Screen

<p align="center">
  <img src="screenshot1.png" width="45%" />
  <img src="screenshot2.png" width="45%" />
</p>

### Haute Performance. Latence Zéro.
**Solution de streaming de bureau Windows vers Raspberry Pi.**

---

## Vue d'ensemble

**Stream Screen** est une solution de streaming hybride haute performance. Elle transforme votre bureau en flux vidéo **H.264** fluide et réactif, accessible de deux manières simultanées :
1.  **Vers Raspberry Pi** (via application native TCP optimisée).
2.  **Vers Navigateur Web** (via WebRTC ultra-rapide) pour mobiles, tablettes et autres PC sans installation.

Elle utilise l'accélération matérielle **NVENC/AMF** et le moteur **MediaMTX** embarqué pour garantir une expérience proche du zéro latence.

### Écran Virtuel (Optionnel)

Pour créer un véritable second moniteur sans matériel supplémentaire, je recommande l'excellent [**Virtual Display Driver**](https://github.com/VirtualDrivers/Virtual-Display-Driver).
Il permet d'ajouter un écran virtuel à votre PC, idéal pour étendre votre bureau vers le Raspberry Pi.

## Fonctionnalités Clés

*   **Architecture "Zero-Copy"** : Capture DXCam directe vers Encodeur GPU.
*   **WebRTC Natif** : Partagez votre écran sur n'importe quel navigateur (Chrome, Safari, Edge) via un simple lien.
*   **Auto-Kill (Pi)** : Le récepteur Raspberry Pi se coupe automatiquement après 20s d'inactivité (plus d'écran figé).
*   **Console de Supervision** : Visualisez les logs du PC, du Raspberry Pi (SSH) et du Serveur de Diffusion en un seul endroit.
*   **Contrôle Total SSH** : Lancez, arrêtez ou mettez à jour le script sur le Pi d'un simple clic depuis Windows.
*   **Encodage H.264 Hardware** : Compatible NVIDIA (NVENC) et AMD (AMF).
*   **Smart Refresh** : Déduplication d'images (0% bande passante sur écrans statiques).

## Options & Réglages

L'application offre un contrôle total sur le flux :

*   **Résolution** : De 360p à 4K.
*   **Architecture** :
    *   **GPU (NVIDIA)** : Utilise NVENC. Ultra-rapide. Zéro charge CPU.
    *   **CPU (Compatibility)** : Utilise x264. Compatible tout PC.
*   **FPS (5 - 120)** : Ajustez la fluidité selon votre réseau.
*   **Bitrate (0.1 - 25 Mbps)** : Contrôle de qualité.
*   **Latence (Slider)** : Compromis réactivité vs fluidité.
*   **Modes de Diffusion** :
    *   **LIVE** : Dashboard principal.
    *   **RASPBERRY** : Client lourd optimisé (TCP).
    *   **WEBRTC** : Client léger universel (Navigateur).
*   **Smart Refresh** : Si l'image est statique, le débit tombe à 0 (Heartbeat actif).
*   **Mode Console** :
    *   **Local** : Logs PC.
    *   **Pi (SSH)** : Logs Raspberry Pi.
    *   **Serveur** : Logs MediaMTX (WebRTC).

## Mise en Route

### Prérequis
*   **Émetteur** : Windows 10/11 avec GPU dédié (recommandé).
*   **Récepteur** : Raspberry Pi 3/4/5 (ou tout système Linux avec Python 3).

### Installation sur Raspberry Pi (Récepteur)

Exécutez les commandes suivantes dans votre terminal pour préparer l'environnement nécessaire au décodage H.264 :

```bash
# 1. Mise à jour des paquets
sudo apt update

# 2. Installation des dépendances système (requises pour PyAV)
sudo apt install -y python3-dev python3-pip libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev libswscale-dev libswresample-dev libavfilter-dev

# 3. Installation des librairies Python
pip3 install av pygame
```

Une fois terminé, copiez simplement le fichier `stream_receiver.py` sur le bureau du Raspberry Pi.

### Lancement Manuel (Raspberry Pi)

Si vous ne souhaitez pas utiliser le lancement SSH automatique, vous pouvez lancer le récepteur manuellement :

```bash
# Lancer le récepteur
python3 stream_receiver.py
```

### Utilisation Rapide
1.  Ouvrez l'application sur Windows (`start.bat`).
2.  Renseignez l'IP de votre Raspberry Pi dans l'onglet dédié.
3.  Cliquez sur **"Lancer Receiver sur Pi"**. Le flux démarre instantanément.

---

## Stack Technique

*   **Core** : Python 3.11
*   **Capture** : DXCam (DirectX Mirror Driver) / MSS
*   **Encodage** : PyAV (FFmpeg Wrapper) -> NVENC H.264
*   **Réseau** : Sockets Raw (TCP/UDP Hybride)
*   **UI** : CustomTkinter

---

### Auteurs & Licence

**Mister Obat** — Conception & Développement
*Avec l'assistance technique de l'IA.*

Licence **AGPL-3.0**. Usage personnel libre.