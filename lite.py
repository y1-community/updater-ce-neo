#!/usr/bin/env python3
"""Entry point of the lite payload.

The lite tarball is this file, ``src/console``, the vendored MTKclient tree,
adb, and the Linux SP Flash Tool. It has no Qt and no PySide6, which is what
lets the text session run on a Raspberry Pi Zero or an i686 netbook.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.console.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
