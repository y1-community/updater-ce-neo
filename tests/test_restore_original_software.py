"""Tests for "Device not starting? Restore the original software" and restore trigger."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication
from src.i18n import tr


def _get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_troubleshoot_copy_reads_device_not_starting():
    assert tr("rockbox_recover_troubleshoot") == "Device not starting? Restore the original software"


def test_flash_page_labels_and_recovery_trigger():
    _get_app()
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    assert "Device not starting? Restore the original software" in page._theme_recover_link.text()
    assert page._theme_trouble_recover_btn.text() == "Device not starting? Restore the original software"

    recovered = []
    page.start_rockbox_themes_stage(model="Y1", on_recover=lambda: recovered.append("Y1"))
    page._on_theme_recover_clicked()
    assert recovered == ["Y1"]


def test_main_window_restore_original_software_triggers_install():
    _get_app()
    from src.ui.main_window import MainWindow

    with patch("src.theme_pack.AdbWatchWorker"):
        win = MainWindow()
        try:
            # Mock select_page start_install_for_release
            win._select_page.start_install_for_release = MagicMock()
            win._package_model = "Y1"
            win._rockbox_power_on_pending = True
            win._rockbox_themes_flow_active = True
            win._install_failed = True

            win.restore_original_software("Y1")

            assert not win._rockbox_power_on_pending
            assert not win._rockbox_themes_flow_active
            assert not win._install_failed
            assert not win._fallback_attempted
            assert win._flash_method == win._preferred_flash_method()
            win._select_page.start_install_for_release.assert_called_once_with("Y1", "Original Software", tag_name="")
        finally:
            win._force_quit = True
            win.close()


def test_select_page_start_install_for_release_auto_selects_latest():
    _get_app()
    from src.ui.select_page import SelectPackagePage

    sel = SelectPackagePage()
    try:
        rel = {
            "tag_name": "3.1.2",
            "name": "3.1.2",
            "download_url": "https://example.com/rom.zip",
            "asset_name": "rom.zip",
        }
        triggered = []
        sel._trigger_release_install = lambda r, package=None: triggered.append(r)
        sel.start_install_for_release("Y1", "Original Software", release=rel)
        assert len(triggered) == 1
        assert triggered[0]["tag_name"] == "3.1.2"
    finally:
        sel.close()
