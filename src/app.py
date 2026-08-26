#!/usr/bin/env python3
"""Neo updater entry point — InniUpdaterChin interface + online catalogue +
donations modal (see ASSESSMENT.md for the port map)."""

import logging
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import paths


def _configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Also write to a per-user log file so frozen GUI builds (no console) keep
    # a diagnosable trail (e.g. the real exception behind an INTERNAL_ERROR).
    try:
        from .downloads import downloads_dir

        log_path = downloads_dir().parent / "updater.log"
        fh = logging.FileHandler(str(log_path), encoding="utf-8", delay=True)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass


def main():
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Innioasis Updater")
    app.setOrganizationName("innioasis")

    import sys as _sys
    if _sys.platform == "darwin":
        icon = paths.RESOURCES_DIR / "icon.icns"
    else:
        icon = paths.RESOURCES_DIR / "icon.ico"
    if not icon.exists():
        icon = paths.RESOURCES_DIR / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    from .i18n import translator
    from .ui.dark import apply_theme

    # Apply Innioasis Lumen theme (detects OS dark/light mode automatically).
    apply_theme(app)

    # Default language: follow the system, fall back to English.
    import locale

    try:
        lang = locale.getlocale()[0] or ""
        if lang.lower().startswith("zh"):
            translator().set_language("zh-CN")
        else:
            translator().set_language("en")
    except Exception:
        translator().set_language("en")

    from .ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
