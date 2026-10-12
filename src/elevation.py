"""Running the install with superuser rights.

Updater CE drives its flash tools (SP Flash Tool in console mode, the SP Flash
Tool GUI, MTKClient, and the terminal installer) as child processes. A child of
an elevated process is elevated too — with no second consent prompt, and with
stdout still attached — so asking for administrator rights once, for this app,
is what makes every install method run as a superuser. Raising a single tool
instead would lose the console stream the install state machine reads, and this
process could no longer stop the tool it started.

Nothing here grants rights on its own: it is the persisted preference, the
platform's own question (ShellExecuteW's ``runas`` verb, ``osascript``,
``pkexec``), and a restart of the app so the choice takes effect.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# QSettings key for the Settings switch.
RUN_AS_ADMIN_KEY = "run_as_admin"

_TRUTHY = frozenset({"1", "true", "yes", "on", "checked", "enabled"})


def _read_value(settings, key: str, default: bool = False) -> bool:
    """The stored flag, tolerating any QSettings-like object.

    A real ``QSettings`` (and most of the objects tests hand in) take the
    ``type=bool`` keyword; one that does not is asked for the raw value and the
    answer is coerced here. An object with no ``value`` at all reads as off.
    """
    raw = default
    try:
        raw = settings.value(key, default, type=bool)
    except TypeError:
        try:
            raw = settings.value(key, default)
        except Exception:
            return bool(default)
    except Exception:
        return bool(default)
    if isinstance(raw, str):
        return raw.strip().lower() in _TRUTHY
    return bool(raw)


def run_as_admin_enabled(settings) -> bool:
    """True when the user asked for installs to run elevated."""
    if settings is None:
        return False
    return _read_value(settings, RUN_AS_ADMIN_KEY, False)


def set_run_as_admin(settings, enabled: bool) -> None:
    """Persist the choice. A settings object that cannot write is not fatal."""
    if settings is None:
        return
    try:
        settings.setValue(RUN_AS_ADMIN_KEY, bool(enabled))
    except Exception:
        logger.debug("Could not persist %s", RUN_AS_ADMIN_KEY, exc_info=True)


def is_elevated() -> bool:
    """True when this process already runs with elevated rights."""
    try:
        if sys.platform == "win32":
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        if hasattr(os, "geteuid"):
            return os.geteuid() == 0
    except Exception:
        return False
    return False


def can_elevate() -> bool:
    """True when this desktop has a way to ask for the rights at all."""
    try:
        if sys.platform in ("win32", "darwin"):
            # UAC on Windows; osascript's "with administrator privileges" on macOS.
            return True
        return shutil.which("pkexec") is not None
    except Exception:
        return False


def label_text() -> str:
    """The switch's words for this platform."""
    from .i18n import tr

    key = "settings_run_as_admin" if sys.platform == "win32" else "settings_run_as_superuser"
    return tr(key)


def description_text() -> str:
    """The one line under the switch."""
    from .i18n import tr

    key = (
        "settings_run_as_admin_desc"
        if sys.platform == "win32"
        else "settings_run_as_superuser_desc"
    )
    return tr(key)


def _relaunch_argv() -> list:
    """The command that starts this app again, for the current packaging."""
    if getattr(sys, "frozen", False):
        # A packaged build: the executable is the app.
        return [sys.executable, *sys.argv[1:]]
    script = str(sys.argv[0] or "").strip()
    if script and not script.startswith("-"):
        return [sys.executable, str(Path(script).resolve()), *sys.argv[1:]]
    # Started with -c or -m: the package entry point is the honest answer.
    return [sys.executable, "-m", "src.app", *sys.argv[1:]]


def _quote_windows(part) -> str:
    part = str(part)
    if not part or " " in part or "\t" in part:
        return f'"{part}"'
    return part


def _restart_windows(argv) -> tuple:
    import ctypes

    exe, args = str(argv[0]), argv[1:]
    params = " ".join(_quote_windows(a) for a in args)
    try:
        result = int(
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", exe, params, str(Path(exe).parent), 1
            )
        )
    except Exception as exc:  # pragma: no cover - platform specific
        logger.debug("ShellExecuteW runas failed", exc_info=True)
        return False, str(exc)
    if result > 32:
        return True, ""
    if result == 5:
        # ERROR_ACCESS_DENIED here is the user saying no to the UAC prompt.
        return False, "Administrator rights were declined, so the install stays as it is."
    return False, f"Could not restart with administrator rights (code {result})."


def _restart_macos(argv) -> tuple:
    command = " ".join(shlex.quote(str(a)) for a in argv)
    script = 'do shell script "{}" with administrator privileges'.format(
        command.replace("\\", "\\\\").replace('"', '\\"')
    )
    try:
        subprocess.Popen(["osascript", "-e", script])
    except Exception as exc:  # pragma: no cover - platform specific
        logger.debug("osascript elevation failed", exc_info=True)
        return False, str(exc)
    return True, ""


def _restart_posix(argv) -> tuple:
    pkexec = shutil.which("pkexec")
    if not pkexec:
        manual = " ".join(shlex.quote(str(a)) for a in argv)
        return False, f"Run it from a terminal with: sudo {manual}"
    try:
        subprocess.Popen([pkexec, *[str(a) for a in argv]])
    except Exception as exc:
        logger.debug("pkexec elevation failed", exc_info=True)
        return False, str(exc)
    return True, ""


def restart_as_admin() -> tuple:
    """Ask the platform to start a second, elevated copy of this app.

    Returns ``(ok, message)``. It deliberately does not quit the running copy:
    the caller decides when this process should exit, so the answer can still
    be shown on screen when the request is refused.
    """
    argv = _relaunch_argv()
    if not argv:
        return False, "The application path could not be determined."
    if sys.platform == "win32":
        return _restart_windows(argv)
    if sys.platform == "darwin":
        return _restart_macos(argv)
    return _restart_posix(argv)
