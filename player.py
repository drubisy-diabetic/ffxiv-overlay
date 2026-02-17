import os
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor

# Mapping ACT abbreviations to your lowercase filenames
JOB_NAME_MAP = {
    "mrd": "warrior", "war": "warrior",
    "pld": "paladin", "gla": "paladin",
    "drk": "dark_knight",
    "gnb": "gunbreaker",
    "whm": "white_mage", "cnj": "white_mage",
    "sch": "scholar",
    "ast": "astrologian",
    "sge": "sage",
    "mnk": "monk", "pgl": "monk",
    "drg": "dragoon", "lnc": "dragoon",
    "nin": "ninja", "rog": "ninja",
    "sam": "samurai",
    "rpr": "reaper",
    "vpr": "viper",
    "brd": "bard", "arc": "bard",
    "mch": "machinist",
    "dnc": "dancer",
    "blm": "black_mage", "thm": "black_mage",
    "smn": "summoner", "acn": "summoner",
    "rdm": "red_mage",
    "pct": "pictomancer",
    "blu": "blue_mage"
}

# Your existing JOB_COLORS dictionary (ensure keys match the map values)
JOB_COLORS = {
    "mnk": "#d69138", "whm": "#fff0dc", "war": "#b12323", "pld": "#a8d2e6",
    "sch": "#8657ff", "drg": "#4164cd", "brd": "#91ba5e", "blm": "#a579d6",
    "smn": "#2d9b78", "nin": "#af1964", "mch": "#6ee1d6", "drk": "#d126cc",
    "ast": "#ffe74a", "sam": "#e46d2e", "rdm": "#e87b7b", "gnb": "#796d30",
    "dnc": "#e2b0af", "rpr": "#965a90", "sge": "#80a0f0", "vpr": "#103506",
    "pct": "#f19ed1", "default": "#555555"
}

class PlayerRow(QFrame):
    def __init__(self, name, dps, percentage_str, job, relative_fill, deaths, opacity=1.0):
        super().__init__()
        self.setObjectName("PlayerRow")
        self.setFixedHeight(26)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        # 1. Normalize Job and Map to Filename
        raw_job = str(job).lower().strip()
        job_filename = JOB_NAME_MAP.get(raw_job, raw_job).replace(" ", "_")
        
        # 2. Transparency & Color Logic
        color_hex = JOB_COLORS.get(job_filename, JOB_COLORS["default"])
        q_color = QColor(color_hex)
        r, g, b = q_color.red(), q_color.green(), q_color.blue()
        
        # Convert 0.0-1.0 opacity to 0-255 alpha
        alpha = int(opacity * 255)
        bg_alpha = int(opacity * 150) # Slightly more transparent background
        
        job_key = str(job).lower().strip().replace(" ", "_")
        color = JOB_COLORS.get(job_key, JOB_COLORS["default"])

        # 3. Fill in logic
        try:
            fill = float(relative_fill)
        except:
            fill = 0.0

        fill = max(0.0, min(0.99, fill))

        # 4. Styling
        is_me = (name.upper() == "YOU")
        border_style = f"1px solid rgba(255, 215, 0, {alpha})" if is_me else "none"

        self.setStyleSheet(f"""
            #PlayerRow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                            stop:{fill} {color}, 
                            stop:{fill + 0.001} rgba(40, 40, 40, 180));
                            border-radius: 3px;
                border: {border_style};
            }}
            QLabel {{
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, {opacity});
                font-family: 'Ubuntu', sans-serif;
                font-size: 11px;
            }}
        """)

        # 5. Layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        # --- Icon Loading ---
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(18, 18)
        self.icon_label.setScaledContents(True)
        
        # Use absolute path to icons folder
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", f"{job_filename}.png")
        pixmap = QPixmap(icon_path)
        
        if not pixmap.isNull():
            self.icon_label.setPixmap(pixmap)
            layout.addWidget(self.icon_label)
        else:
            self.icon_label.hide()

        # --- Death / Name / Stats ---
        try:
            d_num = int(deaths)
        except:
            d_num = 0

        if d_num > 0:
            death_text = "💀" + (str(d_num) if d_num > 1 else "")
            death_label = QLabel(death_text)
            death_label.setStyleSheet(f"color: rgba(255, 68, 68, {opacity}); font-weight: bold;")
            layout.addWidget(death_label)

        self.name_label = QLabel(name)
        layout.addWidget(self.name_label)
        layout.addStretch()

        stats_label = QLabel(f"{dps} ({percentage_str})")
        stats_label.setStyleSheet(f"color: rgba(255, 255, 255, {opacity * 0.7});")
        layout.addWidget(stats_label)
