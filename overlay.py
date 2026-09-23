import sys, os, json, asyncio, threading
from datetime import datetime, timezone
from PyQt6.QtWidgets import *
from PyQt6.QtCore import Qt, QSettings, pyqtSignal, QObject
from PyQt6.QtGui import QAction
import websockets
import json
import asyncio

# Import your player row class
from player import PlayerRow
from prepull import (PrePullDetector, PrePullStore, PrePullPopup,
                     PrePullBoard, extract_log_line)

# Full JSON messages received from ACT MiniParse are appended here, one JSON
# object per line (JSON Lines). The file lives in the current working directory
# so it is easy to find and is never written into the read-only PyInstaller
# bundle.
LOG_FILEPATH = "websocket_logs.jsonl"
# Default when config.json is missing or has no "logging" section yet.
DEFAULT_LOGGING_ENABLED = True
# Defaults for the optional "prepull" section of config.json.
DEFAULT_PREPULL = {
    "tolerance_s": 0.3,
    "popup_s": 3.0,
    # Enmity comes from OverlayPlugin's /ws endpoint (the MiniParse endpoint
    # above only broadcasts CombatData/log lines; enmity must be subscribed).
    "enmity": True,
    "enmity_ws": "ws://127.0.0.1:10501/ws",
}
ENMITY_EVENTS = ["EnmityTargetData", "EnmityAggroList"]

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# Force X11 for stability. This only makes sense on Linux/X11 systems; on
# Windows (and other platforms) forcing the "xcb" platform plugin crashes
# Qt because that plugin is not available there. Let Qt fall back to its
# native platform on non-Windows-incompatible hosts instead.
if sys.platform != "win32":
    os.environ["QT_QPA_PLATFORM"] = "xcb"


class WebSocketLogger(QObject):
    """Appends the full ACT MiniParse WebSocket payload to a JSONL log file.

    The logger lives in the WebSocket thread, so doing file I/O here never
    blocks or interferes with the main (UI) thread. Every time a message is
    written it emits ``count_changed`` so the UI can show a small indicator.
    """

    count_changed = pyqtSignal(int)

    def __init__(self, filepath=LOG_FILEPATH, enabled=True):
        super().__init__()
        self.filepath = filepath
        self.enabled = enabled
        self.count = 0
        self._handle = None

    def log(self, raw_message):
        """Append one raw JSON message plus a UTC ISO-8601 timestamp.

        ``raw_message`` is the exact JSON string that arrived over the wire,
        which is the most faithful record of what ACT sent.
        """
        if not self.enabled or raw_message is None:
            return

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "raw": raw_message,
        }
        line = json.dumps(entry, ensure_ascii=False)

        # Append (don't truncate) so an existing log is preserved mid-session.
        if self._handle is None:
            try:
                self._handle = open(self.filepath, "a", encoding="utf-8")
            except OSError as e:
                print(f"Could not open log file {self.filepath}: {e}")
                return

        self._handle.write(line + "\n")
        self._handle.flush()
        self.count += 1
        self.count_changed.emit(self.count)

    def close(self):
        """Flush and close the open file handle, if any."""
        if self._handle is not None:
            try:
                self._handle.close()
            finally:
                self._handle = None


class Overlay(QWidget):
    def __init__(self):
        super().__init__()
        # 1. Settings & State
        self.settings = QSettings("MyFFXIVApp", "OverlayConfig")
        self.locked = self.settings.value("locked", "false") == "true"
        self.click_through = self.settings.value("click_through", "false") == "true"

        # WebSocket logging toggle (loaded from config.json, defaults to on).
        self.logging_enabled = self._load_settings_file().get("logging", {}).get(
            "websocket", DEFAULT_LOGGING_ENABLED
        )
        self.logger = WebSocketLogger(
            filepath=LOG_FILEPATH, enabled=self.logging_enabled
        )
        self.logger.count_changed.connect(self.update_log_indicator)

        # 1.5 Pre-pull tracking
        prepull_cfg = dict(DEFAULT_PREPULL)
        prepull_cfg.update(self._load_settings_file().get("prepull", {}))
        self.prepull_cfg = prepull_cfg
        self.prepull_detector = PrePullDetector(tolerance=prepull_cfg["tolerance_s"])
        self._last_enmity_log_key = {}
        self.prepull_store = PrePullStore()
        self.prepull_popup = PrePullPopup(seconds=prepull_cfg["popup_s"])
        self.prepull_board = PrePullBoard(self.prepull_store,
                                          on_hide=lambda: self.set_board_visible(False))
        self.board_visible = self.settings.value("prepull_board_visible", "true") == "true"

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
        self.bridge.prepull_detected.connect(self.on_prepull)
        self.bridge.enmity_status.connect(self.prepull_board.set_enmity_status)
        threading.Thread(target=self.start_ws_loop, daemon=True).start()

        self.show()
        self.prepull_board.attach(self)
        self.prepull_board.setVisible(self.board_visible)

    def _load_settings_file(self):
        """Best-effort read of config.json (used for the logging toggle)."""
        try:
            with open(resource_path("config.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

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

        # The WebSocket log indicator (small, only visible while unlocked).
        self.log_label = QLabel()
        self.log_label.setStyleSheet("color: #666; font-size: 9px; font-weight: bold; background: transparent; border: none;")
        self.log_label.setToolTip(f"WebSocket log entries saved to {LOG_FILEPATH}")
        if self.logging_enabled:
            self.log_label.setText("LOG 0")
        else:
            self.log_label.setText("LOG OFF")
            self.log_label.setToolTip("WebSocket logging is disabled")

        # --- THE STATUS DOT ---
        self.status_dot = QLabel()
        self.status_dot.setFixedSize(6, 6)
        self.status_dot.setStyleSheet("background-color: #ff4444; border-radius: 3px; border: none;") # Start Red
        self.status_dot.setToolTip("Searching for ACT...")

        header_container.addWidget(self.header_label)
        header_container.addStretch()
        header_container.addWidget(self.log_label)
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
        if hasattr(self, "prepull_board"):
            self.prepull_board.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.click_through)

        # Small visual hint for locking
        self.container.setFrameShape(QFrame.Shape.Panel if not self.locked else QFrame.Shape.NoFrame)

        # Keep the log indicator out of the way while locked (during combat).
        self.log_label.setVisible(not self.locked)

    def update_log_indicator(self, count):
        """Refresh the small log-counter label. Called on the main thread."""
        if self.log_label is None:
            return
        if self.logging_enabled:
            self.log_label.setText(f"LOG {count}")
            self.log_label.setToolTip(f"{count} WebSocket log entries -> {LOG_FILEPATH}")
        else:
            self.log_label.setText("LOG OFF")

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
        board_act = QAction("Hide Pre-pull Board" if self.board_visible
                            else "Show Pre-pull Board", self)
        board_act.triggered.connect(lambda: self.set_board_visible(not self.board_visible))
        menu.addAction(board_act)
        menu.addAction("Test Pre-pull Popup", self.test_prepull_popup)
        menu.addAction("Reset Pre-pull Board", self.reset_prepull_board)

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

    # --- Pre-pull -----------------------------------------------------------
    def on_prepull(self, event):
        """Main-thread slot: count it, refresh the board, flash the popup."""
        count = self.prepull_store.add(event)
        print(f"PRE-PULL: {event['name']} {event['early_by']}s early "
              f"({event.get('ability') or '-'}, via {event.get('evidence')})")
        self.prepull_board.refresh(highlight=event["name"])
        self.prepull_popup.show_event(event, count=count,
                                      colors=self.prepull_board.colors, anchor=self)

    def test_prepull_popup(self):
        """Show the popup with fake data (not counted) to check its position."""
        self.prepull_popup.show_event(
            {"name": "Test Player", "job": "war", "early_by": 2.4, "ability": "Tomahawk",
             "evidence": "action+enmity"},
            colors=self.prepull_board.colors, anchor=self)

    def set_board_visible(self, visible):
        self.board_visible = visible
        self.settings.setValue("prepull_board_visible", "true" if visible else "false")
        self.prepull_board.setVisible(visible)
        if visible:
            self.prepull_board.follow()

    def reset_prepull_board(self):
        reply = QMessageBox.question(self, "Reset Pre-pull Board",
                                     "Clear all pre-pull counts?")
        if reply == QMessageBox.StandardButton.Yes:
            self.prepull_store.reset()
            self.prepull_board.refresh()

    def start_ws_loop(self):
        asyncio.run(self.run_connections())

    async def run_connections(self):
        """Everything network-side runs on this one asyncio thread, so the
        pre-pull detector is only ever touched from here (no locking)."""
        tasks = [self.listen(), self.prepull_ticker()]
        if self.prepull_cfg.get("enmity", True):
            tasks.append(self.listen_enmity())
        await asyncio.gather(*tasks)

    async def prepull_ticker(self):
        """Settles a pending engagement once its confirmation window
        (time for the other evidence source to arrive) has passed."""
        while True:
            await asyncio.sleep(0.2)
            try:
                event = self.prepull_detector.tick()
            except Exception as e:
                print(f"pre-pull tick error: {e}")
                continue
            if event:
                self.bridge.prepull_detected.emit(event)

    def _log_enmity(self, msg, data):
        """Enmity arrives several times a second; only log it when WHO has
        enmity on WHAT changes, so websocket_logs.jsonl stays readable."""
        if self.logger is None:
            return
        kind = data.get("type")
        if kind == "EnmityTargetData":
            key = ((data.get("Target") or {}).get("ID"),
                   tuple(sorted((e.get("ID"), bool(e.get("Enmity"))) for e in (data.get("Entries") or [])
                                if isinstance(e, dict))))
        else:
            key = tuple(sorted((a.get("ID"), (a.get("Target") or {}).get("ID"))
                               for a in (data.get("AggroList") or []) if isinstance(a, dict)))
        if self._last_enmity_log_key.get(kind) != key:
            self._last_enmity_log_key[kind] = key
            self.logger.log(msg)

    async def listen_enmity(self):
        """Second connection, OverlayPlugin's /ws API: subscribe to the
        enmity events and feed them to the pre-pull detector. If this can't
        connect, pre-pull detection keeps working from log lines alone."""
        uri = self.prepull_cfg.get("enmity_ws", DEFAULT_PREPULL["enmity_ws"])
        while True:
            try:
                async with websockets.connect(uri) as ws:
                    await ws.send(json.dumps({"call": "subscribe", "events": ENMITY_EVENTS}))
                    print(f"CONNECTED to {uri} for enmity data")
                    self.bridge.enmity_status.emit(True)
                    async for msg in ws:
                        try:
                            data = json.loads(msg)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(data, dict):
                            continue
                        data = data.get("msg", data) if isinstance(data.get("msg"), dict) else data
                        if data.get("type") not in ENMITY_EVENTS:
                            continue
                        self._log_enmity(msg, data)
                        event = self.prepull_detector.feed_enmity(data)
                        if event:
                            self.bridge.prepull_detected.emit(event)
            except Exception as e:
                print(f"Enmity connection ({uri}) unavailable: {e}. Retrying in 5 seconds...")
            self.bridge.enmity_status.emit(False)
            await asyncio.sleep(5)

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

                            # 1. Log the full raw JSON message for diagnostics.
                            # This runs in the WS thread and never touches UI.
                            if self.logger is not None:
                                self.logger.log(msg)

                            # 2. Parse for the overlay itself.
                            try:
                                raw = json.loads(msg)
                            except json.JSONDecodeError:
                                continue

                            if not isinstance(raw, dict):
                                continue

                            # Log lines feed the pre-pull detector (runs here,
                            # in the WS thread; results go to the UI via signal).
                            log_line = extract_log_line(raw)
                            if log_line is not None:
                                event = self.prepull_detector.feed_line(log_line)
                                if event:
                                    self.bridge.prepull_detected.emit(event)
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

    def closeEvent(self, event):
        """Flush and close the log file when the overlay quits."""
        if self.logger is not None:
            self.logger.close()
        self.prepull_board.close()
        self.prepull_popup.close()
        super().closeEvent(event)

    def moveEvent(self, event):
        self.prepull_board.follow()
        super().moveEvent(event)

    def mousePressEvent(self, event):
        if not self.locked and event.button() == Qt.MouseButton.LeftButton:
            self.windowHandle().startSystemMove()

    def resizeEvent(self, event):
        self.grip.move(self.width() - 16, self.height() - 16)
        if not self.locked:
            self.settings.setValue("geometry", self.saveGeometry())
        if hasattr(self, "prepull_board"):
            self.prepull_board.follow()
        super().resizeEvent(event)

    def update_opacity(self, value):
        """Updates the background alpha of the container"""
        self.container.setStyleSheet(f"""
            background: rgba(20, 20, 20, {value}); 
            border: 1px solid rgba(255, 255, 255, 30); 
            border-radius: 6px;
        """)
        self.prepull_board.set_opacity(value)

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
    prepull_detected = pyqtSignal(dict)
    enmity_status = pyqtSignal(bool)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    gui = Overlay()
    sys.exit(app.exec())
