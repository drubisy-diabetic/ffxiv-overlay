import sys


def test_import_overlay_then_qt():
    # overlay.py force-sets QT_QPA_PLATFORM=xcb at import time.
    import overlay  # noqa: F401
    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    w = overlay.Bridge()
    assert w is not None
