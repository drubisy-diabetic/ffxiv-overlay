import sys, os, json, asyncio, threading
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, QSettings, pyqtSignal, QObject
from PyQt6.QtGui import QAction
import websockets
import json
import asyncio

# Import your player row class
from player import PlayerRow

# Force X11 for stability
os.environ["QT_QPA_PLATFORM"] = "xcb"

class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        # 1. Settings & State
        self.settings = QSettings("MyFFXIVApp", "OverlayConfig")
        self.locked = self.settings.value("locked", "false") == "true"
        self.click_through = self.settings.value("click_through", "false") == "true"
        
        # 2. Window Setup
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        # 3. UI Layout
        self.init_ui()
        
        # 4. Apply Utilities
        self.apply_util_settings()

        # 5. WebSocket Bridge
        self.bridge = Bridge()
        self.bridge.data_received.connect(self.update_ui)
        self.bridge.connection_status.connect(self.update_status_light)
        threading.Thread(target=self.start_ws_loop, daemon=True).start()
        
        self.show()

    def init_ui(self):
        # 1. Outer layout
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # 2. Container
        self.container = QFrame()
        self.container.setObjectName("MainContainer")
        self.container.setStyleSheet("background: rgba(20, 20, 20, 220); border: 1px solid #444; border-radius: 6px;")

        self.rows_layout = QVBoxLayout(self.container)
        self.rows_layout.setContentsMargins(4, 4, 4, 4)
        self.rows_layout.setSpacing(2)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.main_layout.addWidget(self.container)

        # 3. Header Layout (Label + Status Dot)
        header_container = QHBoxLayout()
        
        self.header_label = QLabel("Waiting for Combat...")
        self.header_label.setStyleSheet("color: #888; font-size: 10px; font-weight: bold; background: transparent; border: none;")
        
        # --- THE STATUS DOT ---
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(6, 6)
        self.status_dot.setStyleSheet("background-color: #ff4444; border-radius: 3px; border: none;") # Start Red
        self.status_dot.setToolTip("Searching for ACT...")

        header_container.addWidget(self.header_label)
        header_container.addStretch()
        header_container.addWidget(self.status_dot)
        self.rows_layout.addLayout(header_container)

        # 4. Separator Line
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,20);")
        self.rows_layout.addWidget(line)

        # 4.5 Transparency Slider
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(20, 255)
        self.opacity_slider.setValue(220)
        self.opacity_slider.setFixedHeight(10)
        self.opacity_slider.setStyleSheet("""
            QSlider::handle:horizontal { background: #888; width: 10px; border-radius: 5px; }
            QSlider::groove:horizontal { background: rgba(255, 255, 255, 20); height: 4px; }
        """)
        self.opacity_slider.valueChanged.connect(self.update_opacity)
        self.rows_layout.addWidget(self.opacity_slider)

        self.grip = QSizeGrip(self)

        geom = self.settings.value("geometry")
        if geom: 
            self.restoreGeometry(geom)
        else: 
            self.resize(400, 250)

    def apply_util_settings(self):
        """Update UI based on Lock and Click-Through states"""
        self.grip.setVisible(not self.locked)
        
        # WA_TransparentForMouseEvents is more stable than WindowFlags for click-through
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.click_through)
        
        # Small visual hint for locking
        self.container.setFrameShape(QFrame.Shape.Panel if not self.locked else QFrame.Shape.NoFrame)

    def update_ui(self, data):
        """Processes encounter and combatant data to refresh the UI"""
        # 1. Update the Header (Zone, Timer, and TOTAL DPS)
        encounter = data.get('Encounter', data.get('encounter', {}))
        e_title = encounter.get('title', 'Current Encounter')
        e_time = encounter.get('duration', '00:00')

        # FIX: Try 'ENCDPS' (Caps) or 'dps' for the actual party total
        # 'encdps' in Encounter often just mirrors the top player
        total_dps = encounter.get('ENCDPS', encounter.get('dps', '0'))

        self.header_label.setText(f"{e_title} — {e_time}  |  RDPS: {total_dps}")

        # 2. Get Combatants
        combatants = data.get('Combatant', data.get('combatant', {}))
        if not combatants:
            return

        # 3. Clear existing player rows
        # Skip Index 0 (Header), 1 (Slider), and 2 (Separator Line)
        # Your previous code had 'while count > 3', ensuring 3 items stay.
        while self.rows_layout.count() > 3:
            item = self.rows_layout.takeAt(3)
            if item.widget():
                item.widget().deleteLater()

        # 4. Helper to get numeric DPS
        def get_dps_num(p):
            val = p.get('encdps', p.get('ENCDPS', 0))
            try:
                return float(str(val).replace(',', ''))
            except (ValueError, TypeError):
                return 0.0

        # 5. Sort players by DPS
        sorted_players = sorted(combatants.values(), key=get_dps_num, reverse=True)

        # 6. Find Max DPS for scaling
        max_dps = 0
        for p in sorted_players:
            if p.get('name') != "Limit Break":
                max_dps = get_dps_num(p)
                break

        # 7. Add new player rows
        for p in sorted_players:
            name = p.get('name', 'Unknown')
            if name == "Limit Break":
                continue

            dps_str = p.get('encdps', '0')
            pct_str = p.get('damage%', '0%')
            job = p.get('Job', p.get('job', 'default'))
            death_count = p.get('deaths', '0')

            dps_val = get_dps_num(p)
            relative_fill = (dps_val / max_dps) if max_dps > 0 else 0

            row = PlayerRow(name, dps_str, pct_str, job, relative_fill, death_count)
            self.rows_layout.addWidget(row)

        # 8. Spacer
        self.rows_layout.addStretch()

    def contextMenuEvent(self, event):
        """Right-click menu for utilities"""
        menu = QMenu(self)
        menu.setStyleSheet("background-color: #222; color: white; border: 1px solid #555;")

        lock_act = QAction("Unlock" if self.locked else "Lock Position", self)
        lock_act.triggered.connect(self.toggle_lock)
        menu.addAction(lock_act)

        ct_act = QAction("Disable Click-Through" if self.click_through else "Enable Click-Through", self)
        ct_act.triggered.connect(self.toggle_click_through)
        menu.addAction(ct_act)

        menu.addSeparator()
        menu.addAction("Quit", QApplication.instance().quit)
        menu.exec(event.globalPos())

    def toggle_lock(self):
        self.locked = not self.locked
        self.settings.setValue("locked", "true" if self.locked else "false")
        self.apply_util_settings()
        self.opacity_slider.setVisible(not self.locked)

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self.settings.setValue("click_through", "true" if self.click_through else "false")
        self.apply_util_settings()

    def start_ws_loop(self):
        asyncio.run(self.listen())

    async def listen(self):
        uri = "ws://127.0.0.1:10501/MiniParse"
    
        while True:  # Outer loop: Keeps trying to connect/reconnect
            try:
                print(f"CONNECTING TO: {uri}")
                async with websockets.connect(uri) as ws:
                    self.bridge.connection_status.emit(True)
                    print("CONNECTED to ACT via MiniParse!")
                    # Optional: self.bridge.connection_status.emit(True)

                    while True:  # Inner loop: Processes incoming messages
                        try:
                            msg = await ws.recv()
                            raw = json.loads(msg)

                            if not isinstance(raw, dict):
                                continue

                            data = raw.get('msg', raw)
                            if not isinstance(data, dict):
                                continue

                            if 'Combatant' in data or 'combatant' in data:
                                self.bridge.data_received.emit(data)
                            else:
                                msg_type = data.get('type', 'unknown')
                                if msg_type != 'unknown':
                                    print(f"Ignoring non-combat packet: {msg_type}")
                                
                        except websockets.ConnectionClosed:
                            self.bridge.connection_status.emit(False)
                            print("Connection lost. Retrying in 5 seconds...")
                            break  # Break inner loop to reconnect in outer loop

            except Exception as e:
                # Catches "Connection Refused" if ACT isn't open
                print(f"ACT not found or error: {e}. Retrying in 5 seconds...")
                # Optional: self.bridge.connection_status.emit(False)
                await asyncio.sleep(5)

    def mousePressEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            self.windowHandle().startSystemMove()

    def resizeEvent(self, event):
        self.grip.move(self.width() - 16, self.height() - 16)
        if not self.locked:
            self.settings.setValue("geometry", self.saveGeometry())
        super().resizeEvent(event)

    def update_opacity(self, value):
        """Updates the background alpha of the container"""
        self.container.setStyleSheet(f"""
            background: rgba(20, 20, 20, {value}); 
            border: 1px solid rgba(255, 255, 255, 30); 
            border-radius: 6px;
        """)

    def update_status_light(self, connected):
        """Updates the small dot color based on connection state"""
        if connected:
            self.status_dot.setStyleSheet("background-color: #44ff44; border-radius: 3px; border: none;")
            self.status_dot.setToolTip("Connected to ACT")
        else:
            self.status_dot.setStyleSheet("background-color: #ff4444; border-radius: 3px; border: none;")
            self.status_dot.setToolTip("Searching for ACT...")

    def update_status_light(self, connected):
        """Updates the small dot color based on connection state"""
        if connected:
            # IT IS ACTIVE: Change to Green
            self.status_dot.setStyleSheet("background-color: #44ff44; border-radius: 3px; border: none;")
            self.status_dot.setToolTip("Connected to ACT")
        else:
            # IT IS SEARCHING: Change to Red
            self.status_dot.setStyleSheet("background-color: #ff4444; border-radius: 3px; border: none;")
            self.status_dot.setToolTip("Searching for ACT...")

class Bridge(QObject):
    data_received = pyqtSignal(dict)
    connection_status = pyqtSignal(bool) # Add this signal

if __name__ == '__main__':
    app = QApplication(sys.argv)
    gui = Overlay()
    sys.exit(app.exec())
