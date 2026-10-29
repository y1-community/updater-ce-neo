"""Start-at-login item for the background (tray) mode.

Only written when the user has turned on "Run in Background for Notifications".
The entry launches the app with ``--tray`` so it opens straight into the menu
bar / system tray and loads the window lazily, on click.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TRAY_ARG = "--tray"
LABEL = "com.innioasis.updater"


def _launch_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, TRAY_ARG]
    main = Path(__file__).resolve().parent / "app.py"
    return [sys.executable, str(main), TRAY_ARG]


def _macos_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _linux_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "autostart" / "innioasis-updater.desktop"


def set_login_item(enabled: bool) -> None:
    """Add or remove the start-at-login entry for this platform."""
    try:
        if sys.platform == "darwin":
            _set_macos(enabled)
        elif sys.platform.startswith("linux"):
            _set_linux(enabled)
        elif os.name == "nt":
            _set_windows(enabled)
    except Exception:
        logger.warning("Could not update the start-at-login item", exc_info=True)


def _set_macos(enabled: bool) -> None:
    path = _macos_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    args = "".join(f"<string>{a}</string>" for a in _launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>'
        f"<key>Label</key><string>{LABEL}</string>"
        f"<key>ProgramArguments</key><array>{args}</array>"
        "<key>RunAtLoad</key><true/>"
        "<key>ProcessType</key><string>Background</string>"
        "</dict></plist>\n",
        encoding="utf-8",
    )


def _set_linux(enabled: bool) -> None:
    path = _linux_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    exec_line = " ".join(_launch_command())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Innioasis Updater\n"
        f"Exec={exec_line}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
        encoding="utf-8",
    )


def _set_windows(enabled: bool) -> None:
    import winreg

    key = winreg.OpenKey(
        winreg.HKEY_CURRENT_USER,
        r"Software\Microsoft\Windows\CurrentVersion\Run",
        0,
        winreg.KEY_SET_VALUE,
    )
    try:
        if enabled:
            winreg.SetValueEx(key, "Innioasis Updater", 0, winreg.REG_SZ, " ".join(_launch_command()))
        else:
            try:
                winreg.DeleteValue(key, "Innioasis Updater")
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)
