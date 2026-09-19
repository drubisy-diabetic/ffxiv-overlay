import sys


def test_import_modules():
    import player
    import editor
    import settings_gui
    import overlay
    assert hasattr(overlay, "Overlay")
    assert hasattr(overlay, "Bridge")
    assert hasattr(player, "PlayerRow")
    assert hasattr(player, "JOB_NAME_MAP")
    assert hasattr(player, "JOB_COLORS")


def test_qt_headless():
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication(sys.argv)
    w = QWidget()
    w.resize(10, 10)
    assert w.width() == 10
