import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src import catalog, device_tracking
from src.donation_dialog import DonationDialog, DonationStatusBar
from src.i18n import tr
from src.ui.dark import T, _build_qss, apply_theme, is_dark
from src.ui.dialogs import (
    DiagnosticsDialog,
    FlashCompleteDialog,
    FlashFailedDialog,
    ReleaseReminderDialog,
    UpdateAvailableDialog,
    format_markdown_release_notes,
)
from src.ui.glass import apply_dialog_theme
from src.ui.main_window import MainWindow
from src.ui.select_page import SelectPackagePage


def test_dialog_theme_not_transparent():
    """Verify QDialog and QMessageBox are styled with solid background (never transparent void)."""
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    qss = _build_qss()

    # QDialog must NOT have background-color: transparent
    assert "QDialog {\n    background-color: transparent" not in qss
    assert "QDialog {\n    background-color: " in qss
    assert "QMessageBox {\n    background-color: " in qss
    assert "QMessageBox QLabel {\n    color: " in qss


def test_combobox_popup_styling():
    """Verify QComboBox uses native QStyle and high-contrast palette tokens."""
    from PySide6.QtGui import QPalette
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    pal = app.palette()
    base_color = pal.color(QPalette.Base).name()
    text_color = pal.color(QPalette.Text).name()
    assert base_color != text_color


def test_release_skip_and_reminder_tracking():
    """Verify per-release skip opt out preference."""
    with tempfile.TemporaryDirectory() as td:
        s = QSettings(f"{td}/settings.ini", QSettings.IniFormat)
        assert not device_tracking.is_release_skipped("Y1", "v1.2.3", settings=s)
        device_tracking.set_release_skipped("Y1", "v1.2.3", True, settings=s)
        assert device_tracking.is_release_skipped("Y1", "v1.2.3", settings=s)

        # Record install
        device_tracking.record_device_install("Y1", "Inniclassic", "v1.0.0", settings=s)

        client = catalog.ReleasesClient(cache_root=td)
        client.releases_for_package = lambda pkg, model, show_nightly=False: [
            {"tag_name": "v1.2.3", "name": "v1.2.3", "published_at": "2026-05-01T00:00:00Z"},
            {"tag_name": "v1.0.0", "name": "v1.0.0", "published_at": "2026-01-01T00:00:00Z"},
        ]

        # Since v1.2.3 is skipped, check_device_updates should return empty
        updates = device_tracking.check_device_updates(releases_client=client, settings=s)
        assert len(updates) == 0

        # Un-skip
        device_tracking.set_release_skipped("Y1", "v1.2.3", False, settings=s)
        updates = device_tracking.check_device_updates(releases_client=client, settings=s)
        assert len(updates) == 1
        assert updates[0]["latest_tag"] == "v1.2.3"


def test_release_reminder_dialog_markdown_and_opt_out():
    """Verify ReleaseReminderDialog renders markdown changelog and handles release opt-out."""
    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as td:
        s = QSettings(f"{td}/settings.ini", QSettings.IniFormat)

        upd = {
            "model": "Y1",
            "software_name": "Inniclassic",
            "installed_tag": "v1.0",
            "latest_tag": "v2.0",
            "latest_release": {
                "name": "Inniclassic 2.0",
                "tag_name": "v2.0",
                "body": "## What's Changed\n* Added AAC support\n* Fixed crash\n![alt](https://example.com/img.png)",
            },
        }

        dlg = ReleaseReminderDialog(update_info=upd)
        assert hasattr(dlg, "_notes_view")
        html = dlg._notes_view.toHtml()
        assert "What's Changed" in html
        assert "Added AAC support" in html
        assert "img.png" not in html  # Images stripped for clean typography

        assert hasattr(dlg, "cb_dont_remind")
        assert "Do not remind me about this release" in dlg.cb_dont_remind.text()

        # Check opt-out logic
        dlg.cb_dont_remind.setChecked(True)
        # Manually patch settings for test
        old_set = device_tracking._get_settings
        device_tracking._get_settings = lambda settings=None: s
        try:
            dlg._check_disable_opt_out()
            assert device_tracking.is_release_skipped("Y1", "v2.0", settings=s)
        finally:
            device_tracking._get_settings = old_set


def test_donation_dialog_toned_down_and_themed():
    """Verify DonationDialog applies native styling and symbols."""
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = DonationDialog(context="general", model="Y1")
    assert hasattr(dlg, "_close_btn")
    # Native buttons should have symbols
    grid_buttons = [btn.text() for btn in dlg.findChildren(type(dlg._close_btn))]
    assert any("☕" in txt for txt in grid_buttons)
    assert any("💳" in txt for txt in grid_buttons)
    assert any("⚡" in txt for txt in grid_buttons)
    assert any("★" in txt for txt in grid_buttons)


def test_select_page_installed_release_badge_and_button_rename():
    """Verify SelectPackagePage highlights currently installed release and renames install button."""
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()

    # Start button in local flow is Install / Restore
    assert page._start_btn.text() == "Install / Restore"

    with tempfile.TemporaryDirectory() as td:
        s = QSettings(f"{td}/settings.ini", QSettings.IniFormat)
        old_set = device_tracking._get_settings
        device_tracking._get_settings = lambda settings=None: s if settings is None else settings
        try:
            device_tracking.record_device_install(
                "Y1", "Rockbox", "2026-01-01",
                release_label="Rockbox 2026-01-01",
            )
            page._software_combo.setCurrentText("Rockbox")
            mock_releases = [
                {"tag_name": "2026-02-01", "name": "Rockbox 2026-02-01", "published_at": "2026-02-01T00:00:00Z"},
                {"tag_name": "2026-01-01", "name": "Rockbox 2026-01-01", "published_at": "2026-01-01T00:00:00Z"},
            ]
            page._on_releases_loaded(mock_releases, None)

            # Check that row with 2026-01-01 is bold and badged
            item0 = page._release_list.item(0) # newer
            item1 = page._release_list.item(1) # installed
            assert "Installed" not in item0.text()
            assert not item0.font().bold()

            assert "Installed" in item1.text()
            assert item1.font().bold()
        finally:
            device_tracking._get_settings = old_set


def test_theme_watcher_in_main_window():
    """Verify MainWindow has live ThemeWatcher wired."""
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    assert hasattr(w, "_theme_watcher")
    assert hasattr(w, "_on_theme_changed")
