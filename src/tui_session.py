"""Text session entry used by ``launcher.py --tui``.

The AppImage, the tarball and the zip still open the window when no flag is
given. This module does not draw a menu of its own: it starts the Select
Software session in ``src.console.tui``, which is also what the lite payload
runs. PySide is not imported.
"""

from __future__ import annotations

import os
import sys


def _run():
    try:
        from src.console.tui import run
    except ImportError:
        from console.tui import run
    return run


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv[1:])
    if "--help" in args or "-h" in args:
        print("Updater CE text session")
        print("  updater-ce --tui              Select Software in the terminal")
        print("  updater-ce --tui --simulate   timed install and theme progress, no device")
        print("  updater-ce --cli              commands, no full-screen menu")
        print("The AppImage, tarball and zip still open the window when no flag is given.")
        return 0
    simulate = "--simulate" in args or os.environ.get("INNIOASIS_SIMULATE") == "1"
    refresh = "--refresh" in args
    return _run()(refresh=refresh, simulate=simulate)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
