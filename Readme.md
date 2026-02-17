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
git clone [https://github.com/drubisy-diabetic/ffxiv-overlay.git](https://github.com/drubisy-diabetic/ffxiv-overlay.git)
cd ffxiv-overlay


🚀 How to Run (Using the Executable)
You do not need to install Python to run the pre-compiled binary located in the dist folder.

CachyOS / Arch:

# Install required system libraries
sudo pacman -S qt6-base

# Execute the binary
cd dist
chmod +x FFXIV_Overlay
./FFXIV_Overlay


Ubuntu / Debian

# Install required system libraries
sudo apt install libxcb-cursor0

# Execute the binary
cd dist
chmod +x FFXIV_Overlay
./FFXIV_Overlay



🛠️ Development & Manual Installation
If you wish to run the source code or modify the logic:

# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install PyQt6 websockets

# Launch
python3 overlay.py



📦 How to Rebuild the Binary
If you make changes to the Python script and want to update the executable:

pip install pyinstaller
pyinstaller --noconsole --onefile --name "FFXIV_Overlay" overlay.py




Created by drubisy-diabetic
