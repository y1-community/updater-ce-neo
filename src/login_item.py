"""Start-at-login item for the background (tray) mode.

The entry exists exactly while the user has turned on "Run in Background for
Notifications". It launches the app with ``--tray`` so it opens straight into
the menu bar / system tray: no splash, no window, just the icon the user can
click when they want the app. The entry is reconciled on every launch, so a
moved install or a preference saved before the entry existed heals itself.
"""

from __future__ import annotations

import logging
import os
import plistlib
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

TRAY_ARG = "--tray"
LABEL = "com.innioasis.updater"
WINDOWS_VALUE_NAME = "Innioasis Updater"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

#: Characters the desktop entry spec reserves inside an ``Exec`` argument.
_DESKTOP_RESERVED = set(' \t\n"\'\\<>~|&;$*?#()`')


def _macos_bundle_executable() -> Path | None:
    """The launcher of the .app bundle wrapping this process, when there is one."""
    for parent in Path(sys.executable).parents:
        if parent.suffix != ".app":
            continue
        contents = parent / "Contents"
        name = parent.stem
        try:
            with open(contents / "Info.plist", "rb") as handle:
                name = plistlib.load(handle).get("CFBundleExecutable") or name
        except Exception:
            pass
        launcher = contents / "MacOS" / name
        if launcher.is_file():
            return launcher
    return None


def _launch_command() -> list[str]:
    if sys.platform == "darwin":
        launcher = _macos_bundle_executable()
        if launcher is not None:
            # Launch the app, not the interpreter inside it: macOS names a login
            # item after the program it launched, so the bare interpreter
            # announced itself as "python3.11 is running in the background".
            return [str(launcher), TRAY_ARG]
    if sys.platform.startswith("linux"):
        appimage = os.environ.get("APPIMAGE")
        if appimage:
            # An AppImage runs from a temporary mount, so sys.executable is a
            # path that disappears; APPIMAGE is the file the user installed.
            return [appimage, TRAY_ARG]
    if getattr(sys, "frozen", False):
        return [sys.executable, TRAY_ARG]
    main = Path(__file__).resolve().parent / "app.py"
    return [sys.executable, str(main), TRAY_ARG]


def _windows_command() -> str:
    """The Run key value with every argument quoted for the Windows parser.

    An installed app lives under a path with spaces ("...\\Innioasis Updater\\
    InnioasisUpdater.exe"), and the unquoted form was split at the first space,
    so the login entry launched something that did not exist.
    """
    return subprocess.list2cmdline(_launch_command())


def _desktop_exec_arg(arg: str) -> str:
    """One ``Exec`` argument, quoted and escaped by the desktop entry rules."""
    text = str(arg).replace("%", "%%")
    if text and not (set(text) & _DESKTOP_RESERVED):
        return text
    for character, replacement in (
        ("\\", "\\\\"),
        ('"', '\\"'),
        ("`", "\\`"),
        ("$", "\\$"),
    ):
        text = text.replace(character, replacement)
    return f'"{text}"'


def _linux_exec_line() -> str:
    return " ".join(_desktop_exec_arg(part) for part in _launch_command())


def _macos_payload() -> dict:
    """The LaunchAgent property list, built as data so paths are escaped."""
    return {
        "Label": LABEL,
        "ProgramArguments": _launch_command(),
        "RunAtLoad": True,
        "ProcessType": "Background",
        # A menu bar app belongs to the logged-in GUI session. Without this a
        # background agent can start in a session with no window server and
        # the app then has no menu bar item to reach it by.
        "LimitLoadToSessionType": "Aqua",
    }


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
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(plistlib.dumps(_macos_payload()))


def _set_linux(enabled: bool) -> None:
    path = _linux_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Innioasis Updater\n"
        f"Exec={_linux_exec_line()}\nX-GNOME-Autostart-enabled=true\nNoDisplay=true\n",
        encoding="utf-8",
    )


def _set_windows(enabled: bool) -> None:
    import winreg

    key = winreg.CreateKeyEx(
        winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
    )
    try:
        if enabled:
            winreg.SetValueEx(
                key, WINDOWS_VALUE_NAME, 0, winreg.REG_SZ, _windows_command()
            )
        else:
            try:
                winreg.DeleteValue(key, WINDOWS_VALUE_NAME)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)
