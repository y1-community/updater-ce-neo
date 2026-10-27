"""Keep helper processes from opening console windows on Windows.

The app is a GUI program, so every console tool it launches (``adb``, ``taskkill``,
``powershell``, ``wmic`` ...) would otherwise get a fresh black console window
for as long as it runs. ``install()`` makes ``subprocess.Popen`` (and so ``run``,
``check_output`` and friends) hide that window by default:

* ``CREATE_NO_WINDOW`` is added to ``creationflags``, and
* a ``STARTUPINFO`` with ``SW_HIDE`` is supplied, which also covers tools that
  create their own console.

Callers that deliberately ask for a visible or detached console (``DETACHED_PROCESS``,
``CREATE_NEW_CONSOLE``, ``CREATE_NO_WINDOW`` already set) are left untouched, so the
terminal-install hand-off keeps working. This is a no-op on macOS and Linux.
"""

from __future__ import annotations

import os
import subprocess

_installed = False

_CREATE_NO_WINDOW = 0x08000000
_DETACHED_PROCESS = 0x00000008
_CREATE_NEW_CONSOLE = 0x00000010


def hidden_window_kwargs() -> dict:
    """``creationflags``/``startupinfo`` kwargs that hide a console child (Windows only)."""
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0  # SW_HIDE
    return {"creationflags": _CREATE_NO_WINDOW, "startupinfo": startupinfo}


def install() -> bool:
    """Patch ``subprocess.Popen`` once so child consoles stay hidden. Returns True if patched."""
    global _installed
    if os.name != "nt" or _installed:
        return False
    original_init = subprocess.Popen.__init__

    def _init(self, *args, **kwargs):
        flags = int(kwargs.get("creationflags") or 0)
        if not flags & (_DETACHED_PROCESS | _CREATE_NEW_CONSOLE | _CREATE_NO_WINDOW):
            kwargs["creationflags"] = flags | _CREATE_NO_WINDOW
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _init
    _installed = True
    return True
