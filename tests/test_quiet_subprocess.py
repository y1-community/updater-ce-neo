"""Windows helper processes must not open console windows."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import adb, quiet_subprocess

NO_WINDOW = 0x08000000


def test_hidden_window_kwargs_hide_console_on_windows(monkeypatch):
    monkeypatch.setattr(quiet_subprocess.os, "name", "nt")
    monkeypatch.setattr(subprocess, "STARTUPINFO", lambda: type("SI", (), {"dwFlags": 0})(), raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    kwargs = quiet_subprocess.hidden_window_kwargs()
    assert kwargs["creationflags"] & NO_WINDOW
    assert kwargs["startupinfo"].wShowWindow == 0


def test_hidden_window_kwargs_empty_off_windows(monkeypatch):
    monkeypatch.setattr(quiet_subprocess.os, "name", "posix")
    assert quiet_subprocess.hidden_window_kwargs() == {}


def test_adb_runs_silently():
    captured = {}

    def fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with patch("src.adb.subprocess.run", fake_run), patch(
        "src.quiet_subprocess.hidden_window_kwargs",
        return_value={"creationflags": NO_WINDOW},
    ):
        adb._run_adb("adb", "devices")
    assert captured["creationflags"] & NO_WINDOW
    assert captured["capture_output"] is True
    assert captured["stdin"] is subprocess.DEVNULL


def test_popen_patch_adds_flag_but_respects_explicit_console(monkeypatch):
    if sys.platform != "win32":
        return
    seen = []
    monkeypatch.setattr(quiet_subprocess, "_installed", False)
    original = subprocess.Popen.__init__
    try:
        monkeypatch.setattr(
            subprocess.Popen, "__init__",
            lambda self, *a, **k: seen.append(k.get("creationflags")),
        )
        quiet_subprocess.install()
        subprocess.Popen(["x"])
        subprocess.Popen(["x"], creationflags=0x10)  # CREATE_NEW_CONSOLE
        subprocess.Popen(["x"], creationflags=0x8)   # DETACHED_PROCESS
    finally:
        subprocess.Popen.__init__ = original
        quiet_subprocess._installed = False
    assert seen[0] & NO_WINDOW
    assert seen[1] == 0x10
    assert seen[2] == 0x8
