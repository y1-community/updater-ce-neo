#!/usr/bin/env python3
"""Fail a build whose Qt binding leaks ``None`` references.

PySide6 6.12.0 on CPython < 3.12 drops a ``None`` reference on every
``QPainter.setPen``/``setFont`` call; a frozen app built on it aborts with
``Fatal Python error: none_dealloc`` within a minute of use. Python 3.12+ is
immune (immortal ``None``). Run with the interpreter that PyInstaller will use:

    python scripts/check_pyside_refcount.py

Exit 0 = sound, 1 = leaking (do not ship), 2 = could not check.
"""

import os
import platform
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def main() -> int:
    try:
        import PySide6
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter
    except Exception as exc:  # pragma: no cover - environment problem
        print(f"  -> PySide6 refcount check: could not import PySide6 ({exc})")
        return 2

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])  # noqa: F841
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    colour, font = QColor(0, 0, 0), QFont()

    def cycle():
        p = QPainter(img)
        p.setPen(colour)
        p.setFont(font)
        p.end()

    cycle()
    n = 512
    before = sys.getrefcount(None)
    for _ in range(n):
        cycle()
    drift = (sys.getrefcount(None) - before) / n
    tag = f"PySide6 {PySide6.__version__} on Python {platform.python_version()}"
    if drift < 0:
        print(f"ERROR: {tag} leaks a None reference per paint call ({drift:+.2f}).")
        print("       The built app would abort with 'none_dealloc: deallocating None'.")
        print("       Fix: pip install -r requirements.txt   (PySide6<6.12 on Python<3.12)")
        print("            or build with Python 3.12+.")
        return 1
    print(f"  -> {tag}: refcount sound")
    return 0


if __name__ == "__main__":
    sys.exit(main())
