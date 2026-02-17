import json
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QCheckBox, QPushButton, QLabel, QScrollArea
from PyQt6.QtCore import Qt

class GeneralSettings(QWidget):
    def __init__(self, current_config, save_callback):
        super().__init__()
        self.setWindowTitle("Stat Visibility Settings")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(300, 400)
        self.save_callback = save_callback
        self.checks = {}

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("<b>Toggle Visible Stats</b>"))

        # Scroll area in case you add 20+ different stats
        scroll = QScrollArea()
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        for key, enabled in current_config.items():
            # Formatting the key name for the label (e.g., "damage%" -> "Damage %")
            display_name = key.replace('%', ' %').replace('_', ' ').title()
            cb = QCheckBox(display_name)
            cb.setChecked(enabled)
            scroll_layout.addWidget(cb)
            self.checks[key] = cb

        scroll.setWidget(scroll_content)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)

        save_btn = QPushButton("Apply Visibility")
        save_btn.setFixedHeight(35)
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)

    def save_settings(self):
        new_config = {key: cb.isChecked() for key, cb in self.checks.items()}
        with open('config.json', 'w') as f:
            json.dump(new_config, f, indent=4)
        self.save_callback(new_config)
        self.hide()
