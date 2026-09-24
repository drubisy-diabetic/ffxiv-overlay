"""Integration / smoke test: build the Windows executable with PyInstaller
and make sure it at least starts without crashing.

This is the only place in the repo that invokes PyInstaller, and it is the
mechanism the sandbox uses to verify the build (its fixed runner is pytest).
The build is lazy: once dist\\FFXIV_Overlay.exe exists it is reused, so later
runs stay fast. Mirrors build.bat exactly.
"""

import os
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(REPO, "dist")
EXE = os.path.join(DIST, "FFXIV_Overlay.exe")


def _build_exe():
    os.makedirs(DIST, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconsole", "--onefile",
        "--collect-all", "PyQt6",
        "--add-data", "icons;icons",
        "--name", "FFXIV_Overlay",
        "overlay.py",
    ]
    proc = subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    # PyInstaller prints warnings but still succeeds; only fail on a real error.
    if not os.path.exists(EXE):
        sys.stdout.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise RuntimeError("PyInstaller did not produce FFXIV_Overlay.exe")


def test_build_windows_exe():
    if not os.path.exists(EXE):
        _build_exe()
    assert os.path.exists(EXE), "exe was not created in dist/"
    assert os.path.getsize(EXE) > 5 * 1024 * 1024, "exe looks empty/corrupt"


def test_exe_starts_without_errors():
    """Launch the built exe briefly; it must stay alive (i.e. not crash)."""
    if not os.path.exists(EXE):
        _build_exe()
    proc = subprocess.Popen(
        [str(EXE)],
        cwd=REPO,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(8)
        alive = proc.poll() is None
        output = ""
        if not alive and proc.stdout is not None:
            try:
                output = proc.stdout.read().decode("utf-8", "replace")
            except Exception:
                output = "<unreadable>"
        assert alive, (
            "FFXIV_Overlay.exe exited on its own (crashed on start):\n" + output
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
