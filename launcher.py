#!/usr/bin/env python3
"""PyInstaller entry point.

``src/app.py`` is a package module (it uses relative imports), so we launch it
through this tiny script that puts ``src`` on ``sys.path`` and calls
``src.app.main()``. Kept outside ``src/`` so PyInstaller freezes it cleanly.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src.app import main  # noqa: E402

if __name__ == "__main__":
    main()
