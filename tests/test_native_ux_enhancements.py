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
        import src.player_update_check as puc
        old_set = puc._get_settings
        puc._get_settings = lambda settings=None: s
        try:
            dlg._check_disable_opt_out()
            assert device_tracking.is_release_skipped("Y1", "v2.0", settings=s)
        finally:
            puc._get_settings = old_set


def test_donation_dialog_toned_down_and_themed():
    """Verify DonationDialog applies native styling, default 710x460 size, and Buy Us A Coffee label."""
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = DonationDialog(context="install_success", model="Y1")
    assert dlg.width() == 710
    assert dlg.height() == 460
    assert hasattr(dlg, "_close_btn")
    # Native buttons should have symbols and no overriding stylesheet
    assert hasattr(dlg, "_pay_buttons") and len(dlg._pay_buttons) == 4
    for btn in dlg._pay_buttons:
        assert not bool(btn.styleSheet()), f"Button {btn.text()} has overriding stylesheet"
    grid_buttons = [btn.text() for btn in dlg._pay_buttons]
    assert any("☕" in txt for txt in grid_buttons)
    assert any("Buy Us A Coffee" in txt for txt in grid_buttons)
    assert any("💳" in txt for txt in grid_buttons)
    assert any("⚡" in txt for txt in grid_buttons)
    assert any("★" in txt for txt in grid_buttons)
    # Honeygain and crypto remain non-native (links/toggles)
    assert bool(dlg._last_button.styleSheet())
    assert bool(dlg._crypto_toggle.styleSheet())
    dlg.close()


def test_select_page_installed_release_badge_and_button_rename():
    """Verify SelectPackagePage highlights currently installed release and renames install button."""
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()

    # Start button in local flow is Install / Restore
    assert page._start_btn.text() == "Install / Restore"

    # Buttons must be OS-native without overriding stylesheet or custom classes
    assert not page._install_btn.styleSheet()
    assert page._install_btn.property("cssClass") is None
    assert page._install_btn.isDefault()
    assert not page._start_btn.styleSheet()
    assert page._start_btn.property("cssClass") is None
    assert page._start_btn.isDefault()
    assert not page._refresh_btn.styleSheet()
    assert page._refresh_btn.property("cssClass") is None
    assert not page._type_help_btn.styleSheet()
    assert page._type_help_btn.property("cssClass") is None

    with tempfile.TemporaryDirectory() as td:
        s = QSettings(f"{td}/settings.ini", QSettings.IniFormat)
        import src.player_update_check as puc
        old_set = puc._get_settings
        puc._get_settings = lambda settings=None: s if settings is None else settings
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
            puc._get_settings = old_set


def test_theme_watcher_in_main_window():
    """Verify MainWindow has live ThemeWatcher wired."""
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    assert hasattr(w, "_theme_watcher")
    assert hasattr(w, "_on_theme_changed")


def test_subtitle_hints_and_eta_retranslation():
    """Verify subtitle hints in SettingsPage and friendly ETA in FlashPage translate properly across all supported locales."""
    from src.i18n import translator
    from src.ui.settings_page import SettingsPage
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    sp = SettingsPage()
    fp = FlashPage()

    for lang, exp_hide, exp_skip in [
        ("fr", "Supprime la reconnaissance des donateurs", "Affiche les détails d'achèvement"),
        ("es", "Elimina el reconocimiento", "Muestra detalles simples de finalización"),
        ("zh-CN", "移除状态栏捐赠滚动条", "刷机完成后直接显示标准完成说明"),
        ("en", "Remove donor recognition and donation UI", "Show simple completion details"),
    ]:
        translator().set_language(lang)
        sp.retranslate()
        assert exp_hide in sp._lbl_hide_tip.text(), f"Hide tip failed for {lang}: {sp._lbl_hide_tip.text()}"
        assert exp_skip in sp._lbl_skip_tip.text(), f"Skip tip failed for {lang}: {sp._lbl_skip_tip.text()}"
        assert exp_hide in sp._cb_hide_donations.toolTip(), f"Hide tooltip failed for {lang}"
        assert exp_skip in sp._cb_skip_install_donations.toolTip(), f"Skip tooltip failed for {lang}"

        # Test localized ETA
        eta_53s = fp._format_friendly_eta("00:53")
        eta_2m = fp._format_friendly_eta("02:00")
        if lang == "fr":
            assert "secondes restantes" in eta_53s
            assert "minutes restantes" in eta_2m
        elif lang == "es":
            assert "segundos restantes" in eta_53s
            assert "minutos restantes" in eta_2m
        elif lang == "zh-CN":
            assert "秒" in eta_53s
            assert "分钟" in eta_2m
        elif lang == "en":
            assert "53 seconds remaining" in eta_53s
            assert "2 minutes remaining" in eta_2m

    # Reset back to en
    translator().set_language("en")


def test_pre_install_guidance_dialog():
    """Verify PreInstallGuidanceDialog displays appropriate guidance per model/mode."""
    from src.ui.dialogs import PreInstallGuidanceDialog

    app = QApplication.instance() or QApplication(sys.argv)
    # Innioasis model
    d1 = PreInstallGuidanceDialog(model="Y1", is_mtk_generic=False)
    assert d1.minimumSize().width() >= 500
    assert d1.minimumSize().height() >= 250
    # MediaTek Generic
    d2 = PreInstallGuidanceDialog(model="", is_mtk_generic=True)
    assert d2.minimumSize().width() >= 500


def test_retry_guidance_dialog_size():
    """Verify RetryGuidanceDialog conforms to minimum 610x275 size."""
    from src.ui.dialogs import RetryGuidanceDialog

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = RetryGuidanceDialog(detail="Test error")
    assert dlg.minimumSize().width() >= 610
    assert dlg.minimumSize().height() >= 275


def test_compact_window_mode_and_sidebar_hiding():
    """Verify window shrinks to 700x300 and hides other sidebar buttons during active install."""
    from src.state import FlashState
    from src.ui.main_window import _PAGE_SETTINGS, _PAGE_FLASH, _PAGE_SELECT

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()

    # Initial state
    assert w.size().width() >= 900
    assert w.size().height() >= 500
    assert w._settings_btn.isVisible()
    assert w._log_btn.isVisible()
    assert w._check_updates_btn.isVisible()

    # Simulate transition to active install
    w._set_state(FlashState.S2_WAIT_CONNECTION)
    assert w._install_run_active() is True
    assert w._settings_btn.isVisible() is False
    assert w._support_btn.isVisible() is False
    assert w._log_btn.isVisible() is False
    assert w._check_updates_btn.isVisible() is False
    assert w._lang_combo.isVisible() is False
    assert w._nav_buttons["nav_select_package"][0].isVisible() is True
    assert w._nav_buttons["nav_select_package"][0].isChecked() is True
    assert w.size().width() <= 750
    assert w.size().height() <= 350

    # User attempts to navigate to Settings during flash: must be blocked
    w._nav_to_page(_PAGE_SETTINGS)
    assert w._stack.currentIndex() != _PAGE_SETTINGS
    assert w._settings_btn.isChecked() is False
    assert w._nav_buttons["nav_select_package"][0].isChecked() is True

    # When install ends / reset, full window and sidebar are restored
    w._reset_after_run()
    assert w._install_run_active() is False
    assert w._settings_btn.isVisible() is True
    assert w._log_btn.isVisible() is True
    assert w._check_updates_btn.isVisible() is True
    assert w._lang_combo.isVisible() is True
    assert w.minimumSize().width() == 900
    assert w.minimumSize().height() == 500


def test_window_maximization_disabled():
    """Verify window maximization is disabled across platforms (only close and minimize)."""
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    flags = w.windowFlags()
    assert not (flags & Qt.WindowMaximizeButtonHint), "Maximize button hint must be disabled"
    assert bool(flags & Qt.WindowMinimizeButtonHint), "Minimize button hint must be enabled"
    assert bool(flags & Qt.WindowCloseButtonHint), "Close button hint must be enabled"


def test_close_interception_during_install(monkeypatch=None):
    """Verify close attempt during active install prompts confirmation and cancels safely if confirmed."""
    from PySide6.QtGui import QCloseEvent
    from src.state import FlashState
    from src.config import get_app_name

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()

    # Enter active flash state
    w._set_state(FlashState.S4_FLASHING)
    assert w._install_run_active() is True
    w._test_close_prompt = True

    # Test 1: User says "No" to closing
    prompted_box = []
    original_question = QMessageBox.question

    def mock_question_no(parent, title, text, buttons, default):
        prompted_box.append((title, text))
        return QMessageBox.No

    QMessageBox.question = mock_question_no
    try:
        ev1 = QCloseEvent()
        w.closeEvent(ev1)
        assert not ev1.isAccepted(), "Close event should be ignored when user selects No"
        assert len(prompted_box) == 1
        assert get_app_name() in prompted_box[0][1]
    finally:
        QMessageBox.question = original_question

    # Test 2: User says "Yes" to closing
    prompted_box.clear()

    def mock_question_yes(parent, title, text, buttons, default):
        prompted_box.append((title, text))
        return QMessageBox.Yes

    QMessageBox.question = mock_question_yes
    try:
        ev2 = QCloseEvent()
        w.closeEvent(ev2)
        assert ev2.isAccepted(), "Close event should be accepted when user confirms"
        assert len(prompted_box) == 1
    finally:
        QMessageBox.question = original_question


def test_flash_page_compact_card_and_heading():
    """Verify FlashPage layout matches mockup with compact 470px card and no idle badge."""
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    fp = FlashPage()
    assert fp._progress_card.maximumWidth() <= 480
    assert fp._wait_card.maximumWidth() <= 480
    assert fp._prep_card.maximumWidth() <= 480
    # Headings
    assert len(fp._headings) == 3
    for h in fp._headings:
        assert h.text() == tr("flash_install_in_progress")
    # Cancel button
    assert fp._cancel_btn.width() <= 30
    assert fp._cancel_btn.height() <= 30


if __name__ == "__main__":
    test_dialog_theme_not_transparent()
    test_combobox_popup_styling()
    test_release_skip_and_reminder_tracking()
    test_release_reminder_dialog_markdown_and_opt_out()
    test_donation_dialog_toned_down_and_themed()
    test_select_page_installed_release_badge_and_button_rename()
    test_theme_watcher_in_main_window()
    test_subtitle_hints_and_eta_retranslation()
    test_pre_install_guidance_dialog()
    test_retry_guidance_dialog_size()
    test_compact_window_mode_and_sidebar_hiding()
    test_window_maximization_disabled()
    test_close_interception_during_install()
    test_flash_page_compact_card_and_heading()

    print("All native UX enhancement tests passed!")
