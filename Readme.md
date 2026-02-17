FFXIV Linux Overlay
A high-performance, lightweight combat overlay for FFXIV on Linux. This tool connects to Advanced Combat Tracker (ACT) via WebSockets to provide real-time Raid DPS, individual performance bars, and death tracking.

✨ Features
Real-time RDPS: Header shows total party damage per second.

Dynamic Scaling: Player bars scale relative to the top performer.

Job Awareness: Automatic coloring based on FFXIV job roles.

Death Tracker: Visual skull icons 💀 appear for players who have died.

UI Controls: Integrated transparency slider and window resizing grip.

🧩 Required Game Dependencies
To use this overlay on Linux, your game must be configured to send data to ACT. You will need:

Dalamud: The plugin framework for FFXIV (included with XIVLauncher).

IINACT (Dalamud Plugin): * Install via the Dalamud plugin installer.

This plugin acts as the bridge that sends combat data to ACT without needing specialized network drivers.

🖱️ Overlay Controls (Right-Click Menu)
As shown in the preview image, right-clicking anywhere on the overlay opens a context menu with the following essential tools:

Lock Position: Disables moving and resizing. Use this once you have placed the overlay in your preferred spot to prevent accidental clicks.

Enable/Disable Click-Through: * Enabled: The overlay ignores your mouse, allowing you to click "through" it to interact with the game world.

Disabled: Allows you to interact with the transparency slider and window grip.

Quit: Gracefully closes the overlay and saves your current window position and size.

📥 Installation
1. Download the Repository
Bash

git clone https://github.com/drubisy-diabetic/ffxiv-overlay.git
cd ffxiv-overlay
2. Configure ACT
Ensure ACT is running via Wine/Bottles with OverlayPlugin installed.

In the OverlayPlugin WSServer tab, click Start on port 10501.

🚀 How to Run (Using the Executable)
You do not need to install Python to run the pre-compiled binary located in the dist folder.

On CachyOS / Arch:
Bash

sudo pacman -S qt6-base
cd dist
chmod +x FFXIV_Overlay
./FFXIV_Overlay
On Ubuntu / Debian:
Bash

sudo apt install libxcb-cursor0
cd dist
chmod +x FFXIV_Overlay
./FFXIV_Overlay
🛠️ Development & Manual Installation
If you wish to run the source code:

Bash

python3 -m venv venv
source venv/bin/activate
pip install PyQt6 websockets
python3 overlay.py
📦 How to Rebuild the Binary
Bash

pip install pyinstaller
pyinstaller --noconsole --onefile --name "FFXIV_Overlay" overlay.py
Final Check
You're all set! Just copy that into your file and push it.
