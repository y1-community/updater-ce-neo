#!/usr/bin/env python3
"""Entry point of the lite payload.

The lite tarball is only this file, ``src/console`` and the installer script.
It has no Qt, no PySide6 and no compiled wheels, which is what lets it run on a
Raspberry Pi Zero, an i686 netbook or any machine where pip has nothing to
offer.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.console.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
