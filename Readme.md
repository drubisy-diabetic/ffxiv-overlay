# ⚔️ FFXIV Linux Overlay

A high-performance, lightweight combat overlay for FFXIV on Linux. This tool connects to **Advanced Combat Tracker (ACT)** via WebSockets to provide real-time Raid DPS, individual performance bars, and death tracking.

![Overlay Preview](https://github.com/drubisy-diabetic/ffxiv-overlay/blob/main/preview.png?raw=true)

---

## ✨ Features

* **Real-time RDPS:** Header shows total party damage per second (Manual summation for accuracy).
* **Dynamic Scaling:** Player bars scale relative to the top performer.
* **Job Awareness:** Automatic coloring based on FFXIV job roles (including Viper/Picto).
* **Death Tracker:** Visual skull icons `💀` appear for players who have died.
* **UI Controls:** Integrated transparency slider and window resizing grip.

---

## 🧩 Required Game Dependencies

To use this overlay on Linux, your game must be configured to send data to ACT.

1.  **Dalamud:** The plugin framework for FFXIV (included with XIVLauncher).
2.  **IINACT (Dalamud Plugin):** * Install via the Dalamud plugin installer (`/xlplugins`).
    * This plugin acts as the bridge that sends combat data to ACT without needing specialized network drivers.

---

## 🖱️ Overlay Controls (Right-Click Menu)

Right-clicking anywhere on the overlay opens a context menu with essential tools:

| Feature | Description |
| :--- | :--- |
| **Lock Position** | Disables moving and resizing. Prevents accidental clicks during combat. |
| **Enable Click-Through** | The overlay ignores mouse clicks, letting you interact with the game behind it. |
| **Disable Click-Through** | Allows you to interact with the transparency slider and window grip. |
| **Quit** | Gracefully closes the overlay and saves your window position/size. |

---

## 📥 Installation

### 1. Download the Repository
```bash
git clone https://github.com/drubisy-diabetic/ffxiv-overlay.git && cd ffxiv-overlay
```

🚀 How to Run (Using the Executable)
You do not need to install Python to run the pre-compiled binary located in the dist folder.

CachyOS / Arch:

# Install required system libraries
```bash
sudo pacman -S qt6-base
```

# Execute the binary
```bash
cd dist
chmod +x FFXIV_Overlay
./FFXIV_Overlay
```

Ubuntu / Debian

# Install required system libraries
```bash
sudo apt install libxcb-cursor0
```

# Execute the binary
```bash
cd dist
chmod +x FFXIV_Overlay
./FFXIV_Overlay
```

🛠️ Development & Manual Installation
If you wish to run the source code or modify the logic:

# Create and activate virtual environment
```bash
python3 -m venv venv
source venv/bin/activate
```

# Install requirements
```bash
pip install PyQt6 websockets
```
# Launch
```bash
python3 overlay.py
```


📦 How to Rebuild the Binary
If you make changes to the Python script and want to update the executable:
```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name "FFXIV_Overlay" overlay.py
```

🐧 Linux Desktop Integration
If you want the overlay to appear in your application launcher (like GNOME or KDE) with a proper icon and name, follow these steps:

1. Build the Executable
Ensure you have bundled your icons into the binary using PyInstaller:

```bash
pyinstaller --noconsole --onefile --add-data "icons:icons" --name "FFXIV_Overlay" overlay.py
```

2. Create a Desktop Entry
Create a file named ffxiv-overlay.desktop in your local applications folder:

```bash
nano ~/.local/share/applications/ffxiv-overlay.desktop
```

3. Add the Configuration
Paste the following content into the file. Note: Make sure to replace /path/to/your/... with the actual absolute paths on your system.

```bash
[Desktop Entry]
Type=Application
Version=1.0
Name=FFXIV Overlay
Comment=ACT Combat Metrics Overlay
# Update these paths to your actual locations
Exec=/home/youruser/act/dist/FFXIV_Overlay
Icon=/home/youruser/act/app_icon.png
Terminal=false
Categories=Game;Utility;
Keywords=ffxiv;act;overlay;dps;
```bash

4. Register the App
Run these commands to make the file executable and refresh your application database:

```bash
chmod +x ~/.local/share/applications/ffxiv-overlay.desktop
update-desktop-database ~/.local/share/applications/
```

Created by drubisy-diabetic
