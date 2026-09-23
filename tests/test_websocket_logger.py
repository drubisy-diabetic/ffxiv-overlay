import json
import pytest


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def log_path(tmp_path):
    return str(tmp_path / "websocket_logs.jsonl")


def test_logger_writes_jsonl_with_timestamp_and_raw(qapp, log_path):
    """Each raw message is saved as one JSON object line with a timestamp."""
    from overlay import WebSocketLogger

    logger = WebSocketLogger(filepath=log_path, enabled=True)

    sample = '{"msg": {"Combatant": {"1": {"name": "You"}}}}'
    logger.log(sample)
    logger.log(sample)

    # Two entries, one per line (JSON Lines format).
    assert logger.count == 2

    with open(log_path, encoding="utf-8") as f:
        lines = [ln for ln in f.read().splitlines() if ln.strip()]
    assert len(lines) == 2

    for ln in lines:
        entry = json.loads(ln)  # each line is valid JSON
        assert "timestamp" in entry
        assert "raw" in entry
        # The full original JSON message is preserved verbatim.
        assert entry["raw"] == sample

    # Timestamp should be a parseable ISO-8601 string.
    from datetime import datetime
    datetime.fromisoformat(lines[0].split('"timestamp": "')[1].split('"')[0])


def test_logger_disabled_writes_nothing(qapp, log_path):
    from overlay import WebSocketLogger

    logger = WebSocketLogger(filepath=log_path, enabled=False)
    logger.log('{"msg": {}}')
    assert logger.count == 0
    # The file should not be created at all when logging is off.
    import os
    assert not os.path.exists(log_path)


def test_count_changed_signal(qapp, log_path):
    from overlay import WebSocketLogger

    logger = WebSocketLogger(filepath=log_path, enabled=True)
    received = []
    logger.count_changed.connect(received.append)

    logger.log('{"a": 1}')
    logger.log('{"b": 2}')

    assert received == [1, 2]


def test_config_logging_flag_loaded():
    """overlay.py reads the websocket logging toggle from config.json."""
    import overlay
    from pathlib import Path
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
    assert cfg.get("logging", {}).get("websocket") is True

    obj = overlay.Overlay.__new__(overlay.Overlay)
    assert obj._load_settings_file()["logging"]["websocket"] is True
