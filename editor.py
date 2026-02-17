import json
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QScrollArea, QColorDialog)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

class ColorEditor(QWidget):
    def __init__(self, current_colors, save_callback):
        super().__init__()
        self.setWindowTitle("Job Color Definitions")
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(350, 500)
        
        self.save_callback = save_callback
        self.inputs = {}
        self.color_buttons = {}

        main_layout = QVBoxLayout(self)
        
        # Scroll area for the long list of jobs
        scroll = QScrollArea()
        scroll_content = QWidget()
        self.grid_layout = QVBoxLayout(scroll_content)
        
        for job, color in current_colors.items():
            row = QHBoxLayout()
            
            # Color Preview Square (Clickable)
            color_btn = QPushButton()
            color_btn.setFixedSize(24, 24)
            color_btn.setStyleSheet(f"background-color: {color}; border: 1px solid white;")
            color_btn.clicked.connect(lambda checked, j=job: self.pick_color(j))
            
            label = QLabel(job.upper())
            label.setFixedWidth(50)
            
            edit = QLineEdit(color)
            
            row.addWidget(color_btn)
            row.addWidget(label)
            row.addWidget(edit)
            self.grid_layout.addLayout(row)
            
            self.inputs[job] = edit
            self.color_buttons[job] = color_btn

        scroll.setWidget(scroll_content)
        scroll.setWidgetResizable(True)
        main_layout.addWidget(scroll)

        save_btn = QPushButton("Save & Apply Changes")
        save_btn.setFixedHeight(40)
        save_btn.clicked.connect(self.handle_save)
        main_layout.addWidget(save_btn)

    def pick_color(self, job):
        current_hex = self.inputs[job].text()
        color = QColorDialog.getColor(QColor(current_hex), self, f"Select color for {job}")
        if color.isValid():
            new_hex = color.name()
            self.inputs[job].setText(new_hex)
            self.color_buttons[job].setStyleSheet(f"background-color: {new_hex}; border: 1px solid white;")

    def handle_save(self):
        new_colors = {job: edit.text() for job, edit in self.inputs.items()}
        try:
            with open('colors.json', 'w') as f:
                json.dump(new_colors, f, indent=4)
            self.save_callback(new_colors)
            # Use hide() to stop the window from being visible 
            # without killing the process
            self.hide() 
        except Exception as e:
            print(f"Error saving: {e}")
