from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel
from PyQt6.QtCore import Qt

JOB_COLORS = {
    "mnk": "#d69138", "whm": "#fff0dc", "war": "#b12323", "pld": "#a8d2e6",
    "sch": "#8657ff", "drg": "#4164cd", "brd": "#91ba5e", "blm": "#a579d6",
    "smn": "#2d9b78", "nin": "#af1964", "mch": "#6ee1d6", "drk": "#d126cc",
    "ast": "#ffe74a", "sam": "#e46d2e", "rdm": "#e87b7b", "gnb": "#796d30",
    "dnc": "#e2b0af", "rpr": "#965a90", "sge": "#80a0f0", "vpr": "#103506",
    "pct": "#f19ed1", "default": "#555555"
}

class PlayerRow(QFrame):
    def __init__(self, name, dps, percentage_str, job, relative_fill, deaths):
        super().__init__()
        
        # 1. Basics & Defaults
        self.setFixedHeight(26)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        # Normalize data
        job_key = str(job).lower().strip()
        color = JOB_COLORS.get(job_key, JOB_COLORS["default"])
        
        try:
            fill = float(relative_fill)
        except (ValueError, TypeError):
            fill = 0.0
        fill = max(0.0, min(1.0, fill))

        # 2. Logic for "YOU" and Deaths
        is_me = (name.upper() == "YOU")
        
        # Styles for the text and border
        name_style = "color: #ffd700; font-weight: 800;" if is_me else "color: white;"
        border_style = "border: 1px solid rgba(255, 215, 0, 80);" if is_me else "border: none;"
        
        # 3. Apply the bar styling (Gradient)
        self.setStyleSheet(f"""
            PlayerRow {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, 
                            stop:{fill} {color}, 
                            stop:{fill + 0.001} rgba(40, 40, 40, 180));
                border-radius: 3px;
                margin-bottom: 1px;
                {border_style}
            }}
            QLabel {{
                background: transparent;
                {name_style}
                font-family: 'Ubuntu', 'Segoe UI', sans-serif;
                font-size: 11px;
            }}
        """)

        # 4. Layout & Widgets
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(6)

        # Death Indicator (Skull)
        try:
            d_num = int(deaths)
        except:
            d_num = 0

        if d_num > 0:
            death_icon = "💀" + (str(d_num) if d_num > 1 else "")
            self.death_label = QLabel(death_icon)
            self.death_label.setStyleSheet("color: #ff4444; font-weight: bold; font-size: 10px;")
            layout.addWidget(self.death_label)

        # Name Label
        self.name_label = QLabel(name)
        layout.addWidget(self.name_label)
        
        # Spacer to push stats to the right
        layout.addStretch()

        # Stats Label (DPS + %)
        stats_text = f"{dps} ({percentage_str})"
        self.stats_label = QLabel(stats_text)
        self.stats_label.setStyleSheet("color: rgba(255, 255, 255, 0.7); font-weight: 400;")
        layout.addWidget(self.stats_label)
