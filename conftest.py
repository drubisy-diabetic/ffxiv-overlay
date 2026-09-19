# Ensure the project root (this file's directory) is importable so the
# overlay/player/editor/settings_gui modules can be imported in tests.
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
