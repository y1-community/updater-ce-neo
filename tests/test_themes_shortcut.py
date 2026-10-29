"""Ctrl+T / Cmd+T starts the Rockbox themes & fonts stage without a flash."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication


def _window():
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    with patch("src.theme_pack.AdbWatchWorker"):
        return app, MainWindow()


def test_ctrl_t_shortcut_registered_and_starts_prompt():
    from src.ui.main_window import _PAGE_FLASH

    app, win = _window()
    try:
        assert win._themes_shortcut.key() == QKeySequence("Ctrl+T")
        assert not win._install_run_active()
        win._package_model = ""
        win._themes_shortcut.activated.emit()
        app.processEvents()
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert win.themes.power_on_pending
        assert win.themes.active
        assert win._install_run_active()
        assert win.themes.power_on_model in ("Y1", "Y2")

        # Confirming power-on enters the themes stage proper.
        win._flash_page._theme_step1_next_btn.click()
        app.processEvents()
        assert not win.themes.power_on_pending
        assert win._flash_page.themes.transfer_running
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_ctrl_t_ignored_while_install_running():
    app, win = _window()
    try:
        win._download_active = True
        assert win.start_rockbox_themes_manually() is False
        assert not win.themes.power_on_pending
    finally:
        win._download_active = False
        win._force_quit = True
        win.close()
        app.processEvents()
