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
    """Coffee is the only donation action. Crypto and Honeygain are not offered."""
    from PySide6.QtWidgets import QWidget

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = DonationDialog(context="install_success", model="Y1")
    assert dlg.width() == 710
    assert dlg.height() == 460
    assert hasattr(dlg, "_close_btn")
    assert hasattr(dlg, "_pay_buttons") and len(dlg._pay_buttons) == 1
    coffee = dlg._pay_buttons[0]
    assert coffee.text() == "Buy us a coffee"
    assert not coffee.styleSheet()
    assert coffee.cursor().shape() == Qt.ArrowCursor
    assert not hasattr(dlg, "_crypto_toggle")
    joined = " ".join(
        child.text() for child in dlg.findChildren(QWidget) if hasattr(child, "text")
    )
    for banned in ("Honeygain", "PayPal", "Revolut", "Patreon", "Bitcoin", "Crypto"):
        assert banned not in joined, banned
    dlg.close()


def test_model_and_type_label_strings():
    """Model and Type labels are the short names in every shipped language."""
    from src import i18n

    assert i18n._STRINGS["sel_model"] == {
        "zh-CN": "型号",
        "en": "Model",
        "fr": "Modèle",
        "es": "Modelo",
        "de": "Modell",
        "ja": "モデル",
    }
    assert i18n._STRINGS["sel_type"] == {
        "zh-CN": "类型",
        "en": "Type",
        "fr": "Type",
        "es": "Tipo",
        "de": "Typ",
        "ja": "タイプ",
    }


def test_type_info_click_shows_existing_tip():
    """A click on the information mark shows the same Type A / Type B tip as hover."""
    from unittest.mock import patch

    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest

    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()
    icon = page._type_help_icon
    page._type_field.setVisible(True)
    icon.setVisible(True)
    page._views.setCurrentWidget(page._online_tab)
    page.resize(720, 520)
    page.show()
    app.processEvents()

    tip = icon.toolTip()
    assert tip == page._type_combo.toolTip()
    assert "Type A" in tip
    assert "Type B" in tip
    assert icon.cursor().shape() == Qt.ArrowCursor
    assert icon.width() == 16
    assert not hasattr(page, "_type_help_btn")
    assert not hasattr(page, "_show_device_type_help")
    _assert_type_information_icon(icon)

    with patch("src.ui.select_page.QToolTip.showText") as show:
        QTest.mouseClick(icon, Qt.LeftButton)
        app.processEvents()
        assert show.called
        args = show.call_args.args
        assert args[1] == tip
        assert args[2] is icon
        assert args[0] == icon.mapToGlobal(QPoint(0, icon.height()))
    page.close()
    app.processEvents()


def _assert_type_information_icon(icon):
    """The Type mark is a platform information glyph, never a question-mark button."""
    from src.ui import icons

    assert icon.text() != "?"
    assert "?" not in (icon.text() or "")
    assert "Type?" not in (icon.text() or "")
    source = icon.property("helpSource") or ""
    assert "questionmark" not in source
    assert "U+E897" not in source
    assert source != "text:?"
    # The filled badge macOS itself draws comes first; the outline is the
    # fallback for a system whose SF Symbols has no filled info glyph.
    assert icons._SF_HELP_SYMBOLS == ("info.circle.fill", "info.circle")
    assert icons._SEGOE_HELP_CODEPOINTS == (0xE946,)
    assert icons._FREEDESKTOP_MAP["help"] == [
        "dialog-information",
        "help-about",
        "dialog-question",
    ]
    if sys.platform == "win32":
        assert source.startswith("segoe:") or source == "style:SP_MessageBoxInformation"
        if source.startswith("segoe:"):
            assert "U+E946" in source
            assert "Segoe Fluent Icons" in source or "Segoe MDL2 Assets" in source
        assert icon.pixmap() is not None and not icon.pixmap().isNull()
    elif sys.platform == "darwin":
        assert source == "sf:info.circle.fill"
        assert icon.pixmap() is not None and not icon.pixmap().isNull()
    else:
        assert source.startswith("theme:") or source == ""
        if source.startswith("theme:"):
            assert source.split(":", 1)[1] in (
                "dialog-information",
                "help-about",
                "dialog-question",
            )
            assert icon.pixmap() is not None and not icon.pixmap().isNull()


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
    assert not hasattr(page, "_type_help_btn")
    assert page._model_label.text() == "Model:"
    assert page._type_label.text() == "Type:"
    assert page._type_help_icon.parent() is page._type_field
    assert page._type_help_icon.cursor().shape() == Qt.ArrowCursor
    assert page._type_help_icon.text() != "Type?"
    assert "Type?" not in page._type_help_icon.text()
    assert page._type_help_icon.toolTip() == page._type_combo.toolTip()
    assert page._type_label.toolTip() == page._type_combo.toolTip()
    assert "Type A" in page._type_help_icon.toolTip()
    assert "Type B" in page._type_help_icon.toolTip()
    assert page._type_help_icon.width() == 16
    assert page._type_help_icon.text() != "?"
    assert "?" not in (page._type_help_icon.text() or "")
    _assert_type_information_icon(page._type_help_icon)

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

            # Newer than the installed release is bold. The installed row is a
            # circle only, with no "(Installed)" label and no extra bold.
            item0 = page._release_list.item(0)  # newer
            item1 = page._release_list.item(1)  # installed
            assert "Installed" not in item0.text()
            assert item0.font().bold()
            assert "Installed" not in item1.text()
            assert not item1.font().bold()
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

    for lang in ("fr", "es", "zh-CN", "en"):
        translator().set_language(lang)
        sp.retranslate()
        # One switch, one caption, one tooltip: the caption is the card title's
        # own words, and the tooltip explains what the switch shows and how to
        # hide it again. The legacy "skip post-install donations" names now
        # alias that single switch and label, so they read exactly the same.
        show_tip = tr("settings_hide_donations_tip")
        assert sp.switch_caption(sp._cb_hide_donations) == tr("settings_show_donations")
        assert sp._lbl_hide_tip.text() == show_tip, f"Tip failed for {lang}: {sp._lbl_hide_tip.text()}"
        assert sp._cb_hide_donations.toolTip() == show_tip, f"Tooltip failed for {lang}"
        assert sp._lbl_skip_tip is sp._lbl_hide_tip
        assert sp._cb_skip_install_donations is sp._cb_hide_donations
        assert sp._lbl_skip_tip.text() == show_tip
        assert sp._cb_skip_install_donations.toolTip() == show_tip

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
    """Verify window shrinks to compact mode and hides Settings during an install."""
    from src.state import FlashState
    from src.ui.main_window import (
        MINIMUM_WINDOW_HEIGHT,
        MINIMUM_WINDOW_WIDTH,
        _PAGE_SETTINGS,
        _PAGE_FLASH,
        _PAGE_SELECT,
        _PAGE_DIAGNOSTICS,
        default_window_size,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()

    # Initial state. The window opens at the catalogue size, clamped to the
    # screen it is on, and Diagnostics stays hidden until D is pressed.
    expected_w, expected_h = default_window_size(w.screen())
    assert w.size().width() == expected_w
    assert w.size().height() <= expected_h
    assert w._settings_btn.isVisible()
    assert w._log_btn.isVisible() is False
    assert w._diagnostics_page is None
    w._unlock_diagnostics()
    app.processEvents()
    assert w._log_btn.isVisible()
    assert w._check_updates_btn.isVisible()
    assert w._log_btn.y() > w._settings_btn.y()
    assert w._check_updates_btn.y() > w._log_btn.y()
    assert w._lang_combo.y() > w._check_updates_btn.y()
    assert not w._log_btn.styleSheet()
    assert not w._settings_btn.styleSheet()

    # Simulate transition to active install
    w._set_state(FlashState.S2_WAIT_CONNECTION)
    assert w._install_run_active() is True
    assert w._settings_btn.isVisible() is False
    assert w._support_btn.isVisible() is False
    assert w._sp_flash_tool_btn.isVisible() is False
    assert w._install_output_btn.isVisible() is True
    assert w._install_output_btn.isEnabled() is True
    assert w._log_btn.isVisible() is True
    assert w._log_btn.isEnabled() is True
    assert w._check_updates_btn.isVisible() is False
    assert w._lang_combo.isVisible() is False
    assert w._nav_buttons["nav_select_package"][0].isVisible() is True
    assert w._nav_buttons["nav_select_package"][0].isChecked() is True
    # Only the height hugs the progress card. The install window keeps the
    # width the app already had, so the card does not jump sideways.
    assert w.size().width() == expected_w
    assert w.size().height() <= 350

    # Diagnostics is part of the main window and can be opened mid-install.
    w._show_diagnostics()
    app.processEvents()
    if w._size_anim is not None:
        w._size_anim.setCurrentTime(200)
    assert w._stack.currentIndex() == _PAGE_DIAGNOSTICS
    assert w._log_btn.isChecked() is True
    assert w._nav_buttons["nav_select_package"][0].isChecked() is False
    assert w.size().height() >= 420
    w._nav_buttons["nav_select_package"][0].click()
    app.processEvents()
    if w._size_anim is not None:
        w._size_anim.setCurrentTime(200)
    assert w._stack.currentIndex() == _PAGE_FLASH
    assert w._log_btn.isChecked() is False
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
    assert w._settings_btn.isEnabled() is True
    assert w._log_btn.isVisible() is True
    assert w._check_updates_btn.isVisible() is True
    assert w._lang_combo.isVisible() is True
    assert w.minimumSize().width() == MINIMUM_WINDOW_WIDTH
    assert w.minimumSize().height() == MINIMUM_WINDOW_HEIGHT


def test_window_maximization_enabled():
    """Verify window maximization is suppressed everywhere: the app is laid out for a
    compact window, and throwing it full-screen would waste the sidebar, dropdown,
    button and icon copy that is sized for that compact footprint.

    The maximize affordance is removed on every platform: no maximize button hint
    in the window flags, no Aero Snap / snap layouts on Windows, and no in-app
    maximise glyph on Linux. The close and minimize hints are still present so the
    user can exit or minimise without losing work."""
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    flags = w.windowFlags()
    assert not bool(flags & Qt.WindowMaximizeButtonHint), (
        "Maximize button hint must not be reported on any platform."
    )
    assert bool(flags & Qt.WindowMinimizeButtonHint), "Minimize button hint must be enabled"
    assert bool(flags & Qt.WindowCloseButtonHint), "Close button hint must be enabled"
    assert not w._window_controls or not w._window_controls.max_button.isVisible(), (
        "The in-app maximise glyph must not be shown; maximising wastes screen real estate."
    )


def test_close_interception_during_install(monkeypatch=None):
    """Closing during an install asks inside the progress card, not a modal."""
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QCloseEvent
    from src.state import FlashState
    from src.config import get_app_name

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()

    w._set_state(FlashState.S4_FLASHING)
    w._flash_page.show_flashing()
    assert w._install_run_active() is True
    w._test_close_prompt = True

    def click_back():
        assert w._flash_page._stop_open
        assert get_app_name() in w._flash_page._stop_message
        w._flash_page._stop_back.click()

    QTimer.singleShot(0, click_back)
    ev1 = QCloseEvent()
    w.closeEvent(ev1)
    assert not ev1.isAccepted(), "Close event should be ignored when the user goes back"
    assert not w._flash_page._stop_open

    def click_continue():
        w._flash_page._stop_continue.click()

    QTimer.singleShot(0, click_continue)
    ev2 = QCloseEvent()
    w.closeEvent(ev2)
    assert ev2.isAccepted(), "Close event should be accepted when the user continues"


def test_flash_page_compact_card_and_heading():
    """The progress card fills the content width instead of a fixed 470px strip."""
    from PySide6.QtWidgets import QSizePolicy
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    fp = FlashPage()
    for card in (fp._progress_card, fp._wait_card, fp._prep_card, fp._download_card):
        assert card.maximumWidth() > 480
        assert card.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    fp.show()
    fp.resize(720, 220)
    app.processEvents()
    assert fp._prep_card.width() >= fp.width() - 80, fp._prep_card.width()
    # Every progress screen is wrapped the same way, one heading each, and the
    # download screen keeps its own wording.
    assert len(fp._headings) == 4
    for heading, key in fp._headings:
        assert heading.text() == tr(key)
    assert [key for _h, key in fp._headings].count("flash_install_in_progress") == 3
    assert fp._headings[-1][1] == "flash_download_in_progress"
    assert fp._download_card.width() == fp._progress_card.width()
    # Cancel button: the disc stays a 30px chip. The widget is a few pixels
    # larger on each side because that is where the raised shadow goes.
    disc = fp._cancel_btn._disc_rect()
    assert disc.width() == 30, disc
    assert disc.height() == 30, disc
    assert fp._cancel_btn.width() <= 36
    assert fp._cancel_btn.height() <= 36


def test_os_standard_iconography():
    """Verify OS-standard iconography for install, cancel, settings, translate, diagnostics, etc."""
    from src.ui.icons import get_symbol_icon, get_symbol_pixmap
    from src.ui.main_window import MainWindow
    from src.ui.select_page import SelectPackagePage
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)

    symbols = [
        "install",
        "cancel",
        "settings",
        "translate",
        "diagnostics",
        "update",
        "support",
        "tools",
        "file",
        "folder",
    ]
    for sym in symbols:
        icon = get_symbol_icon(sym, 16)
        pix = get_symbol_pixmap(sym, 16)
        assert not icon.isNull(), f"Icon for {sym} must not be null"
        assert not pix.isNull(), f"Pixmap for {sym} must not be null"

    w = MainWindow()
    assert not w._settings_btn.icon().isNull(), "Settings button must have an icon"
    assert not w._support_btn.icon().isNull(), "Support button must have an icon"
    assert not w._discord_btn.icon().isNull(), "Discord button must have an icon"
    assert not w._reddit_btn.icon().isNull(), "Reddit button must have an icon"
    assert not w._log_btn.icon().isNull(), "Diagnostics button must have an icon"
    assert not w._check_updates_btn.icon().isNull(), "Update button must have an icon"
    assert not w._lang_combo.itemIcon(0).isNull(), "Language combo items must have translate icon"

    sp = SelectPackagePage()
    assert not sp._install_btn.icon().isNull(), "Install button must have an icon"
    assert not sp._start_btn.icon().isNull(), "Start button must have an icon"
    from PySide6.QtGui import QColor
    for button in (sp._install_btn, sp._start_btn):
        pixmap = button.icon().pixmap(16, 16)
        image = pixmap.toImage()
        visible = [
            image.pixelColor(x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        ]
        assert visible
        foreground = QColor(T().fg)
        assert all(color.rgb() == foreground.rgb() for color in visible)
    assert not sp._browse_btn.icon().isNull(), "Browse file button must have an icon"
    assert not sp._browse_folder_btn.icon().isNull(), "Browse folder button must have an icon"

    fp = FlashPage()
    fp.show()
    # Cancel chips are circular glyph discs, not QIcons: the painted arrow is
    # the icon contract, with the wording in the tooltip and accessible name.
    assert fp._cancel_btn.toolTip(), "Cancel button must name its action"
    fp._cancel_btn.rebuild_arrow()
    assert not fp._cancel_btn._arrow.isNull(), "Cancel button must paint its glyph"


def test_symbol_color_adaptation_and_states():
    """Verify SF Symbols / Segoe / SVG icons adapt to dark/light mode and focus states."""
    from src.ui.icons import get_symbol_icon, get_symbol_data_uri, resolve_symbol_colors
    from src.ui.dark import T, apply_theme
    from PySide6.QtGui import QIcon

    app = QApplication.instance() or QApplication(sys.argv)

    # 1. Dark mode. The accent follows this desktop, so compare with the theme
    # token rather than one machine's green.
    apply_theme(app, force_dark=True)
    norm, foc = resolve_symbol_colors(match_accent=True)
    assert norm.lower() == str(T().accent).lower()
    assert foc.upper() == "#FFFFFF"

    norm_no_acc, foc_no_acc = resolve_symbol_colors(match_accent=False)
    assert norm_no_acc.upper() == "#FFFFFF"
    assert foc_no_acc.upper() == "#FFFFFF"

    # Multi-state QIcon pixmaps
    icon = get_symbol_icon("install", 16)
    pix_norm = icon.pixmap(16, 16, QIcon.Mode.Normal, QIcon.State.Off)
    pix_active = icon.pixmap(16, 16, QIcon.Mode.Active, QIcon.State.Off)
    pix_on = icon.pixmap(16, 16, QIcon.Mode.Normal, QIcon.State.On)
    assert not pix_norm.isNull()
    assert not pix_active.isNull()
    assert not pix_on.isNull()

    # Data URI generation for HTML labels
    uri = get_symbol_data_uri("translate", 12)
    assert uri.startswith("data:image/png;base64,")
    assert len(uri) > 50

    # 2. Light mode
    apply_theme(app, force_dark=False)
    norm_light, foc_light = resolve_symbol_colors(match_accent=False)
    assert norm_light.startswith("#")
    assert norm_light != "#FFFFFF"  # Must be dark in light mode


def test_traffic_lights_center_on_header():
    """Window controls line up with the vertical center of the icon and title."""
    from src.ui.glass import (
        HEADER_CONTENT_HEIGHT,
        traffic_light_center_from_top,
        traffic_light_origin_y,
    )

    button = 14.0
    header = HEADER_CONTENT_HEIGHT
    assert abs(traffic_light_center_from_top(header) - (header / 2.0)) < 0.01
    # A titlebar taller than the header can place the control on the header center.
    superview = 52.0
    origin = traffic_light_origin_y(button, superview, header)
    center_from_top = superview - origin - (button / 2.0)
    assert abs(center_from_top - (header / 2.0)) < 0.01

    # A short titlebar view still aims at the header center, even when that
    # origin sits below the view.
    short = traffic_light_origin_y(button, 28.0, header)
    short_center = 28.0 - short - (button / 2.0)
    assert abs(short_center - (header / 2.0)) < 0.01


def _image_diff(left, right) -> int:
    count = 0
    for y in range(left.height()):
        for x in range(left.width()):
            if left.pixel(x, y) != right.pixel(x, y):
                count += 1
    return count


def test_platform_sidebar_row_hover_and_selection():
    """Windows and Linux sidebar rows light up on hover and fill on selection.

    An idle row is not its own filled plate: on acrylic or glass the corner is
    clear, and on a solid desktop it matches the pane color. Hover and the
    selected row still paint differently, with no control stylesheet.
    """
    from PySide6.QtGui import QImage

    from src.ui.icons import get_symbol_icon
    from src.ui.sidebar import (
        SidebarButton,
        sidebar_base_color,
        sidebar_corner_radius,
        sidebar_row_spacing,
    )
    from src.ui.surfaces import glass_surfaces_enabled

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)

    assert sidebar_row_spacing("darwin") == 16
    assert sidebar_row_spacing("win32") == 4
    assert sidebar_row_spacing("linux") == 4
    assert sidebar_corner_radius("win32") == 4
    assert sidebar_corner_radius("linux") == 6

    btn = SidebarButton("Settings")
    btn.setIcon(get_symbol_icon("settings", 16))
    btn.setIconSize(btn.iconSize())
    btn.setCheckable(True)
    btn._force_platform_row = True
    btn.resize(180, 36)
    assert not btn.styleSheet()

    def render():
        image = QImage(btn.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        btn.render(image)
        return image

    idle = render()
    corner = idle.pixelColor(8, 8)
    if glass_surfaces_enabled():
        assert corner.alpha() == 0
    else:
        base = sidebar_base_color()
        assert corner.alpha() == 255
        assert (corner.red(), corner.green(), corner.blue()) == (
            base.red(),
            base.green(),
            base.blue(),
        )

    btn._preview_hover = True
    hover = render()
    btn._preview_hover = False
    btn.setChecked(True)
    selected = render()

    assert _image_diff(idle, hover) > 50
    assert _image_diff(idle, selected) > 50
    assert _image_diff(hover, selected) > 50
    assert not btn.styleSheet()


def test_native_style_candidates_follow_the_desktop():
    """WinUI, Breeze, and Adwaita are the first styles offered on each desktop."""
    from src.ui.dark import native_style_candidates, palette_color
    from PySide6.QtGui import QColor, QPalette

    assert native_style_candidates(system="darwin", offscreen=True) == ("fusion",)
    assert native_style_candidates(system="darwin")[0] == "macOS"
    assert native_style_candidates(system="windows")[0] == "windows11"
    kde = native_style_candidates(system="linux", desktop="KDE")
    assert kde[0] == "breeze"
    gnome = native_style_candidates(system="linux", desktop="GNOME")
    assert gnome[0] == "adwaita"
    assert "breeze" in gnome
    overridden = native_style_candidates(system="linux", desktop="GNOME", style_override="kvantum")
    assert overridden[0] == "kvantum"

    from src.ui.dark import _make_palette

    dark_pal = _make_palette(True)
    light_pal = _make_palette(False)
    assert dark_pal.color(QPalette.Midlight) != QColor("#000000")
    assert light_pal.color(QPalette.Button).lightness() > 200
    hover = palette_color("rgba(255, 255, 255, 0.08)", over="#1e2227")
    assert hover.isValid() and hover.alpha() == 255
    assert hover != QColor("#000000")


def test_linux_palette_preserves_desktop_colors():
    """Linux surfaces and controls retain the palette supplied by the desktop."""
    from PySide6.QtGui import QColor, QPalette

    from src.ui import dark as dark_mod

    app = QApplication.instance() or QApplication(sys.argv)
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#333b2d",
        QPalette.ColorRole.WindowText: "#e2e7d8",
        QPalette.ColorRole.Base: "#252b22",
        QPalette.ColorRole.AlternateBase: "#3c4434",
        QPalette.ColorRole.Text: "#e2e7d8",
        QPalette.ColorRole.Button: "#414a37",
        QPalette.ColorRole.ButtonText: "#e2e7d8",
        QPalette.ColorRole.Highlight: "#9aab62",
        QPalette.ColorRole.HighlightedText: "#1b2116",
        QPalette.ColorRole.ToolTipBase: "#414a37",
        QPalette.ColorRole.Mid: "#657052",
        QPalette.ColorRole.Dark: "#20261d",
        QPalette.ColorRole.Midlight: "#4b553e",
        QPalette.ColorRole.PlaceholderText: "#b2b99f",
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))

    was_linux = dark_mod.IS_LINUX
    dark_mod.IS_LINUX = True
    try:
        tokens = dark_mod._Tokens(
            True,
            palette=palette,
            use_native_linux_colors=True,
        )
        assert tokens.bg == "#333b2d"
        assert tokens.bg_card == "#414a37"
        assert tokens.bg_input == "#252b22"
        assert tokens.fg == "#e2e7d8"
        assert tokens.accent == "#9aab62"
        assert tokens.progress_track == "#3c4434"
        assert tokens.bg_tooltip == "#414a37"
        assert dark_mod._make_palette(True, native_palette=palette) == palette
    finally:
        dark_mod.IS_LINUX = was_linux


def test_pre_liquid_glass_light_fields_are_not_white_plates():
    """Older macOS light mode uses Aqua's light control colors, not #ffffff plates."""
    from PySide6.QtGui import QColor, QPalette

    from src.ui import dark as dark_mod

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    kept = dark_mod.legacy_light_role_color(QPalette.ColorRole.Button, QColor(232, 232, 232))
    assert kept.red() == 232
    plate = dark_mod.legacy_light_role_color(QPalette.ColorRole.Button, QColor("#ffffff"))
    assert plate.name().lower() != "#ffffff"
    assert plate.lightness() > 200
    # A dark standard color must not become the light-mode popup.
    field = dark_mod.legacy_light_role_color(QPalette.ColorRole.Base, QColor(28, 28, 30))
    assert field.lightness() > 200
    assert dark_mod.palette_color("rgba(255, 255, 255, 0.08)", over="#1e2227") != QColor("#000000")

    # The Aqua fill is a macOS-before-Liquid-Glass path. Force it on so the
    # assertion does not depend on this host being Darwin.
    real_pre = dark_mod._pre_liquid_glass_macos
    dark_mod._pre_liquid_glass_macos = lambda: True
    try:
        pal = dark_mod._make_palette(False)
    finally:
        dark_mod._pre_liquid_glass_macos = real_pre
    button = pal.color(QPalette.Button)
    assert button.lightness() > 200
    assert button.name().lower() != "#ffffff"
    assert pal.color(QPalette.Base).lightness() > 200
    assert pal.color(QPalette.Text).lightness() < 80
    # Dark mode stays on the blended dark control color.
    assert dark_mod._make_palette(True).color(QPalette.Button).lightness() < 80


def test_diagnostics_count_matches_lines_from_each_source():
    """The line count is the number of lines actually retained, from every source."""
    from src.diagnostics import DiagnosticsManager
    from src.ui.dialogs import DiagnosticsView

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    mgr = DiagnosticsManager.instance()
    mgr.clear()
    view = DiagnosticsView()
    view.set_lines(None)
    token = "retained-count-9f3a"
    sources = [
        f"{token} app ready",
        f"[SP] {token} download da",
        f"[MTK] {token} handshake",
    ]
    try:
        for line in sources:
            view.append_line(line)
        retained = view.raw_lines()
        text = "\n".join(retained)
        for line in sources:
            assert line in text
        assert view._count_label.text() == tr("log_lines_total").format(total=len(retained))
        before = len(retained)
        view.append_line(f"[SP] {token} write")
        after = view.raw_lines()
        assert len(after) == before + 1
        assert view._count_label.text() == tr("log_lines_total").format(total=len(after))
    finally:
        mgr.clear()
        view.deleteLater()


def test_live_theme_switch_updates_title_card_and_donation_text():
    """A live theme change must restyle titles, card labels, and the donation line."""
    from PySide6.QtGui import QPalette

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.processEvents()

    def text_color(widget):
        return widget.palette().color(QPalette.ColorRole.WindowText)

    apply_theme(app, force_dark=True)
    app.processEvents()
    title = window._select_page._title
    card = window._select_page._details_name
    donation = window.statusBar()._goal_label
    dark = {name: text_color(widget) for name, widget in (
        ("title", title), ("card", card), ("donation", donation),
    )}
    for name, color in dark.items():
        assert color.lightness() > 140, (name, color.name())

    apply_theme(app, force_dark=False)
    app.processEvents()
    light = {name: text_color(widget) for name, widget in (
        ("title", title), ("card", card), ("donation", donation),
    )}
    for name, color in light.items():
        assert color.name() != dark[name].name(), (name, color.name(), dark[name].name())
        assert color.lightness() < 120, (name, color.name())

    assert window._select_page._notes_group.title() == ""
    qss = _build_qss()
    assert "0.96" not in qss
    assert "background-color: transparent" in qss.split('cssClass="pageTitle"')[1][:240]
    apply_theme(app, force_dark=None)


def test_classic_windows_buttons_show_hover():
    """The classic Windows style gets a hover wash. WinUI already has one."""
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleFactory, QStyleOptionButton

    from src.ui.dark import _ClassicWindowsHoverStyle
    from src.ui.sidebar import classic_windows_style_needs_hover

    assert classic_windows_style_needs_hover("windows")
    assert not classic_windows_style_needs_hover("windows11")
    assert classic_windows_style_needs_hover("windowsvista")
    if "windows" not in {key.lower() for key in QStyleFactory.keys()}:
        return

    style = _ClassicWindowsHoverStyle("Windows")

    def render(hovered: bool):
        image = QImage(120, 32, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#1e2227"))
        painter = QPainter(image)
        option = QStyleOptionButton()
        option.rect = image.rect().adjusted(1, 1, -1, -1)
        option.text = "Install"
        option.palette = QApplication.instance().palette()
        option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Active
        if hovered:
            option.state |= QStyle.StateFlag.State_MouseOver
        style.drawControl(QStyle.ControlElement.CE_PushButton, option, painter, None)
        painter.end()
        return image

    assert _image_diff(render(False), render(True)) > 20


def test_compact_install_height_counts_the_header_on_every_desktop():
    """The in-app header is 44px of the window on Windows, Linux and macOS.

    It used to be counted on macOS only, which left the compact install window
    exactly one header too short on the other desktops and clipped the status
    line under the progress card.
    """
    from src.ui.glass import HEADER_CONTENT_HEIGHT
    from src.ui.main_window import (
        COMPACT_INSTALL_MIN_HEIGHT,
        COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS,
        compact_install_height,
    )

    header = int(HEADER_CONTENT_HEIGHT)
    # Tall enough that the floor does not mask the header contribution.
    base = dict(page_h=200, prompt_h=30, status_h=22, donations_disabled=False)

    with_header = compact_install_height(inline_header=True, **base)
    without_header = compact_install_height(inline_header=False, **base)
    assert with_header - without_header == header
    assert with_header == base["page_h"] + base["prompt_h"] + base["status_h"] + header

    # A desktop whose window manager owns the title bar still gets the floor.
    assert compact_install_height(
        page_h=40, prompt_h=0, status_h=0, inline_header=False, donations_disabled=False
    ) == COMPACT_INSTALL_MIN_HEIGHT
    # Donations disabled drops the status row and keeps its own floor.
    assert compact_install_height(inline_header=True, **{**base, "donations_disabled": True}) == (
        with_header - base["status_h"]
    )
    assert compact_install_height(
        page_h=40, prompt_h=0, status_h=40, inline_header=True, donations_disabled=True
    ) == COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS


def test_da_payload_is_staged_from_this_desktops_tool_tree(tmp_path, monkeypatch):
    """The DA written into history.ini comes from our own tree for this desktop.

    SP Flash Tool takes the download agent path from history.ini, so the app
    stages the copy it ships when the tool directory has none. The Windows and
    Linux trees must not be borrowed from each other, and macOS (no SP Flash
    Tool GUI) still resolves mtkclient's loader agents instead of failing.
    """
    from src import paths as paths_mod
    from src import sp_flash_gui

    def make_tree(repo, windows: bool, content: bytes):
        sub = (
            repo / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win"
            if windows
            else repo / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux"
        )
        sub.mkdir(parents=True)
        (sub / sp_flash_gui.DA_FILENAME).write_bytes(content)
        return sub

    sp_dir = tmp_path / "sp_tool"
    sp_dir.mkdir()
    absent = tmp_path / "absent"

    for windows in (True, False):
        repo = tmp_path / ("win_repo" if windows else "linux_repo")
        content = b"WIN" if windows else b"LINUX"
        sub = make_tree(repo, windows, content)
        monkeypatch.setattr(paths_mod, "IS_WINDOWS", windows)
        monkeypatch.setattr(paths_mod, "IS_MAC", False)
        monkeypatch.setattr(paths_mod, "REPO_ROOT", repo)
        monkeypatch.setattr(paths_mod, "SP_FLASH_TOOL_DIR", absent / "SP_Flash_Tool")
        monkeypatch.setattr(paths_mod, "COMPAT_DIR", absent / "compat")
        monkeypatch.setattr(paths_mod, "MTKCLIENT_DIR", absent / "mtkclient")

        staged = sp_flash_gui._ensure_da_payload(sp_dir)
        assert staged == sp_dir / sp_flash_gui.DA_FILENAME
        assert staged.read_bytes() == (sub / sp_flash_gui.DA_FILENAME).read_bytes()
        staged.unlink()

    loader = tmp_path / "mtk" / "mtkclient" / "Loader"
    loader.mkdir(parents=True)
    (loader / "MTK_AllInOne_DA_2625.bin").write_bytes(b"OLD")
    (loader / "MTK_AllInOne_DA_7687.bin").write_bytes(b"NEW")
    monkeypatch.setattr(paths_mod, "IS_WINDOWS", False)
    monkeypatch.setattr(paths_mod, "IS_MAC", True)
    monkeypatch.setattr(paths_mod, "REPO_ROOT", tmp_path / "empty_repo")
    monkeypatch.setattr(paths_mod, "MTKCLIENT_DIR", tmp_path / "mtk")
    staged = sp_flash_gui._ensure_da_payload(sp_dir)
    assert staged.read_bytes() == b"NEW", "the newest mtkclient loader agent wins"
    staged.unlink()

    # A tool directory that already carries an agent is left exactly as it is.
    own = sp_dir / sp_flash_gui.DA_FILENAME
    own.write_bytes(b"KEEP")
    assert sp_flash_gui._ensure_da_payload(sp_dir) == own
    assert own.read_bytes() == b"KEEP"


def test_seal_hint_never_widens_the_layout(monkeypatch=None):
    """Sealing a label must not widen the window past what the layout granted.

    The backdrop erase covers the label's own rect, so a width the layout never
    gave it could not have held the earlier text either; growing the minimum
    instead would fight every resize, on acrylic and on macOS glass alike.
    """
    from PySide6.QtWidgets import QLabel

    from src.ui import surfaces

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    previous = surfaces.glass_surfaces_enabled
    if monkeypatch is not None:
        monkeypatch.setattr(surfaces, "glass_surfaces_enabled", lambda: True)
    else:
        surfaces.glass_surfaces_enabled = lambda: True

    long_text = "Downloading DA \u2014 about 2 minutes remaining, please keep the device plugged in"
    label = QLabel(long_text)
    label.resize(90, 20)
    surfaces.seal_updating_text(label)
    # The hint never asks for more room than this label already owns.
    assert 0 < label.minimumWidth() <= label.width() == 90

    label.setText("DA")
    surfaces.seal_updating_text(label)
    # A shorter string cannot lower the seal, and cannot raise it either.
    assert label.minimumWidth() == 90

    # A label the layout has given more room than the text needs only keeps
    # room for the text, not for the whole granted width.
    roomy = QLabel("DA")
    roomy.resize(1000, 20)
    surfaces.seal_updating_text(roomy)
    assert roomy.minimumWidth() <= roomy.fontMetrics().horizontalAdvance("DA") + 12
    assert roomy.minimumWidth() < 1000

    # A label with no width yet has painted nothing to seal, so no minimum is
    # forced onto it and the layout stays free.
    unbuilt = QLabel(long_text)
    unbuilt.setFixedWidth(0)
    surfaces.seal_updating_text(unbuilt)
    assert unbuilt.minimumWidth() == 0
    for widget in (label, roomy, unbuilt):
        widget.deleteLater()
    if monkeypatch is None:
        surfaces.glass_surfaces_enabled = previous


def test_symbol_pixmaps_are_marked_hi_dpi_and_never_oversized():
    """Every icon source must hand back a hi-DPI pixmap at the requested size.

    A 2x pixmap without a device pixel ratio (or a ratio without the extra
    pixels) is what rendered half-clipped and blurry on scaled desktops.
    """
    from src.ui.icons import get_symbol_pixmap

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    for sym in ("install", "cancel", "settings", "diagnostics", "tools", "support"):
        for size in (16, 20, 24):
            pix = get_symbol_pixmap(sym, size)
            assert not pix.isNull(), (sym, size)
            dpr = pix.devicePixelRatio()
            assert dpr > 1.0, (sym, size, dpr)
            assert pix.width() / dpr <= size + 1, (sym, size, pix.width(), dpr)
            assert pix.height() / dpr <= size + 1, (sym, size, pix.height(), dpr)


def test_install_failure_stays_on_progress_and_retry_does_not_reflash():
    """A failed install stays on the progress card. Retry returns to the prompt."""
    from src.state import FlashState
    from src.ui.main_window import _PAGE_ERROR, _PAGE_FLASH

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.processEvents()

    window._last_progress = 10
    window._set_state(FlashState.S4_FLASHING)
    app.processEvents()
    assert not window._sp_flash_tool_btn.isVisible()
    assert window._install_output_btn.isVisible()

    window._handle_flash_failure("S_BROM_CMD_STARTCMD_FAIL (2005)")
    app.processEvents()

    assert window._stack.currentIndex() == _PAGE_FLASH
    assert window._stack.currentIndex() != _PAGE_ERROR
    assert not window._error_page.isVisible()
    page = window._flash_page
    assert page._flash_retry_btn.isVisible()
    assert page._flash_retry_btn.text() == tr("flash_btn_retry")
    assert page._flash_retry_btn.text() != tr("err_btn_retry")
    assert not page._cancel_btn.isVisible()
    assert page._progress_bar.value() == 10
    assert "2005" in page._step_label.text()
    for button in (
        window._error_page._retry_btn,
        window._error_page._reconnect_btn,
        window._error_page._reselect_btn,
        window._error_page._log_btn,
    ):
        assert not button.isVisible()

    started = []
    window.service.start_flash = lambda *args, **kwargs: started.append("start")
    window.service.start_device_monitor = lambda: started.append("monitor")
    page._flash_retry_btn.click()
    app.processEvents()

    assert started == []
    assert window._power_off_prompt is True
    assert page._wait_continue_btn.isVisible()
    assert not page._flash_retry_btn.isVisible()
    assert tr("flash_connect_prompt").split("{")[0].strip() in page._wait_prompt_label.text()

    window._package_path = "firmware.zip"
    window._package_name = "firmware"
    page._wait_continue_btn.click()
    app.processEvents()
    assert "start" in started
    window.close()
    app.processEvents()


def test_pin_sp_flash_history_uses_extract_scatter_and_tool_da():
    """history.ini points at the extracted scatter and the tool's DA file."""
    import os

    from src import sp_flash_gui

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        extract = root / "cached_firmware"
        extract.mkdir()
        scatter = extract / "MT6582_Android_scatter.txt"
        scatter.write_text("partition_index: SYS\n", encoding="utf-8")
        sp_dir = root / "SP_Flash_Tool"
        sp_dir.mkdir()
        da = sp_dir / sp_flash_gui.DA_FILENAME
        da.write_bytes(b"DA")

        ok, da_abs, scatter_abs = sp_flash_gui.pin_sp_flash_history(sp_dir, scatter)
        assert ok is True
        assert os.path.isabs(scatter_abs)
        assert os.path.isabs(da_abs)
        assert Path(scatter_abs) == scatter.resolve()
        assert Path(da_abs) == da.resolve()
        da_read, scatter_read, _history = sp_flash_gui.read_history_paths(sp_dir / "history.ini")
        assert os.path.normcase(scatter_read) == os.path.normcase(scatter_abs)
        assert os.path.normcase(da_read) == os.path.normcase(da_abs)
        assert not (sp_dir / scatter.name).is_file()


def test_sp_gui_handoff_choice_continue_and_sidebar():
    """Clicked release, else cache, else the focused release. Continue and the sidebar both open the tool from Select Software."""
    from src import sp_flash_gui
    from src.config import download_mode_hint
    from src.i18n import tr
    from src.ui.main_window import _PAGE_FLASH, _PAGE_SELECT

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    order = []
    real_cancel = window.service.cancel_flash
    real_stop = window.service.stop_device_monitor

    def cancel():
        order.append("cancel")
        return real_cancel()

    def stop():
        order.append("stop")
        return real_stop()

    launched = []

    def launch(**kwargs):
        launched.append(kwargs)
        order.append("launch")
        return True, "ok"

    window.service.cancel_flash = cancel
    window.service.stop_device_monitor = stop
    original_supported = sp_flash_gui.is_sp_flash_gui_supported
    original_cached = sp_flash_gui.cached_install_firmware
    original_launch = sp_flash_gui.launch_sp_flash_tool_gui
    try:
        with tempfile.TemporaryDirectory() as td:
            # A cache only counts as firmware when the images beside the scatter
            # are really there: an interrupted extract is treated as no cache
            # and downloaded again, so this fixture is a complete one.
            scatter = Path(td) / "rom_scatter.txt"
            scatter.write_text("file_name: fat_sparse.img\n", encoding="utf-8")
            (Path(td) / "fat_sparse.img").write_bytes(b"img")
            extract = Path(td)
            clicked = {"kind": "online", "url": "https://example.invalid/clicked.zip", "model": "Y1"}
            focused = {"kind": "online", "url": "https://example.invalid/original.zip", "model": "Y1"}
            prepared = []

            def begin(target, callback):
                prepared.append(target)
                return True

            sp_flash_gui.is_sp_flash_gui_supported = lambda: True
            sp_flash_gui.cached_install_firmware = lambda latest: (scatter, extract)
            sp_flash_gui.launch_sp_flash_tool_gui = launch
            window._select_page.explicit_firmware = lambda: clicked
            window._select_page.focused_firmware = lambda: focused
            window._select_page.begin_external_prepare = begin
            window._open_sp_flash_tool_gui()
            assert order[:2] == ["cancel", "stop"]
            assert "launch" not in order
            assert prepared == [clicked]

            order.clear()
            prepared.clear()
            window._gui_handoff_prepare = False
            window._gui_launch_when_ready = False
            window._select_page.explicit_firmware = lambda: None
            window._open_sp_flash_tool_gui()
            assert order[:2] == ["cancel", "stop"]
            assert order[-1] == "launch"
            assert prepared == []
            assert launched[-1]["scatter_path"] == scatter
            assert launched[-1]["extract_dir"] == extract
            assert window._stack.currentIndex() == _PAGE_SELECT

            order.clear()
            launched.clear()
            sp_flash_gui.cached_install_firmware = lambda latest: (None, None)
            window._open_sp_flash_tool_gui()
            assert "launch" not in order
            assert prepared == [focused]

            window._gui_ready = (scatter, extract, "Y2")
            window._gui_handoff_ready = True
            window._stack.setCurrentIndex(_PAGE_FLASH)
            window._on_power_off_continue()
            assert window._stack.currentIndex() == _PAGE_SELECT
            assert launched[-1]["model"] == "Y2"
            assert launched[-1]["scatter_path"] == scatter

            window._gui_ready = (scatter, extract, "G5")
            window._gui_handoff_ready = True
            window._power_off_prompt = True
            window._stack.setCurrentIndex(_PAGE_FLASH)
            before = len(order)
            window._open_sp_flash_tool_gui()
            assert window._stack.currentIndex() == _PAGE_SELECT
            assert launched[-1]["model"] == "G5"
            # The hand-over stops the session first: a console-mode flash_tool
            # still searching (or an MTKClient run unwinding) would fight the
            # desktop tool for the port and for history.ini.
            assert order[before:] == ["cancel", "stop", "launch"], order[before:]
            assert window._nav_buttons["nav_select_package"][0].text() == tr("nav_select_package")

            window.show()
            window._nav_to_page(_PAGE_FLASH)
            window._flash_page.set_model("Y1")
            window._flash_page.show_gui_handoff_prompt(download_mode_hint("Y1"))
            app.processEvents()
            hint = window._flash_page._wait_mode_hint.text()
            assert hint == download_mode_hint("Y1")
            assert window._flash_page._wait_mode_hint.isVisible()
            assert "SP Flash Tool" not in hint
            assert "MTKClient" not in hint
            assert "Y1" in window._flash_page._wait_prompt_label.text()
    finally:
        sp_flash_gui.is_sp_flash_gui_supported = original_supported
        sp_flash_gui.cached_install_firmware = original_cached
        sp_flash_gui.launch_sp_flash_tool_gui = original_launch
        window.close()
        app.processEvents()


def test_release_click_downloads_for_handoff_not_in_app_install():
    """A list click only selects that release. The sidebar tool downloads it."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem

    from src import sp_flash_gui

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    original = sp_flash_gui.is_sp_flash_gui_supported
    installs = []
    try:
        sp_flash_gui.is_sp_flash_gui_supported = lambda: True
        prepared = []

        def begin(target, callback):
            prepared.append(target)
            return True

        window._select_page.begin_external_prepare = begin
        item = QListWidgetItem("1.0")
        item.setData(
            Qt.UserRole,
            {
                "download_url": "https://example.invalid/rom_y1.zip",
                "asset_name": "rom_y1.zip",
                "tag_name": "v1",
            },
        )
        window._select_page._release_list.addItem(item)
        window._select_page._release_list.itemClicked.emit(item)
        assert prepared == []
        assert window._gui_handoff_prepare is False
        assert window._select_page.explicit_firmware()["url"].endswith("rom_y1.zip")
        window._open_sp_flash_tool_gui()
        assert prepared and prepared[0]["kind"] == "online"
        assert prepared[0]["model"] == "Y1"
        # The click is the whole request: the download is followed by the
        # desktop tool opening, not by a preparation prompt.
        assert window._gui_after_prepare == "launch"

        window._select_page._release_list.setCurrentItem(item)
        window._select_page._trigger_release_install = lambda rel: installs.append(rel)
        window._select_page._on_install()
        assert installs and installs[0]["tag_name"] == "v1"
        assert window._gui_handoff_prepare is False
        assert window._gui_after_prepare == ""
    finally:
        sp_flash_gui.is_sp_flash_gui_supported = original
        window.close()
        app.processEvents()


def test_gui_handoff_package_rules_and_hint_copy():
    """Local packages qualify, hints stay tool-agnostic, and every language has the new lines."""
    from src import config, i18n
    from src.i18n import translator
    from src.sp_flash_gui import choose_sp_gui_package, is_gui_local_package

    clicked = {"kind": "online", "name": "clicked"}
    cached = {"kind": "cache", "name": "cached"}
    focused = {"kind": "online", "name": "original"}
    assert choose_sp_gui_package(clicked, cached, focused) is clicked
    assert choose_sp_gui_package(None, cached, focused) is cached
    assert choose_sp_gui_package(None, None, focused) is focused
    assert choose_sp_gui_package(None, None, None) is None

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        zip_path = root / "rom.zip"
        zip_path.write_bytes(b"PK")
        rar_path = root / "rom.rar"
        rar_path.write_bytes(b"Rar")
        scatter = root / "MT6572_Android_scatter.txt"
        scatter.write_text("scatter\n", encoding="utf-8")
        notes = root / "readme.txt"
        notes.write_text("notes\n", encoding="utf-8")
        folder = root / "pkg"
        folder.mkdir()
        (folder / "MT6582_Android_scatter.txt").write_text("scatter\n", encoding="utf-8")
        assert is_gui_local_package(zip_path)
        assert is_gui_local_package(rar_path)
        assert is_gui_local_package(scatter)
        assert is_gui_local_package(folder)
        assert not is_gui_local_package(notes)

    previous = translator().lang
    translator().set_language("en")
    try:
        for model in ("Y1", "Y2", "G3", "G1", "G5", "Q5", "Q3e"):
            assert "headphone" in config.download_mode_hint(model).lower()
        assert "power button" in config.download_mode_hint("A5").lower()
        assert "player" in config.download_mode_hint("Q8").lower()
        original = config.is_mediatek_installer
        config.is_mediatek_installer = lambda: True
        try:
            generic = config.download_mode_hint("Q8")
            assert "firmware" in generic.lower()
            assert "player" not in generic.lower()
        finally:
            config.is_mediatek_installer = original
    finally:
        translator().set_language(previous)

    keys = (
        "gui_handoff_hint_headphone",
        "gui_handoff_hint_a5",
        "gui_handoff_hint_generic",
        "gui_handoff_hint_generic_mediatek",
        "gui_handoff_failed_title",
        "gui_handoff_failed",
        "gui_handoff_no_package",
        "gui_handoff_no_package_mediatek",
    )
    for key in keys:
        for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
            text = i18n._STRINGS[key][lang]
            assert text.strip()
            assert "SP Flash Tool" not in text
            assert "MTKClient" not in text


def test_history_ini_absolute_paths_before_gui_launch():
    """history.ini lists the extracted scatter and the tool DA before the GUI process starts."""
    import os

    from src import paths, sp_flash_gui

    if paths.IS_MAC:
        ok, _msg = sp_flash_gui.launch_sp_flash_tool_gui()
        assert ok is False
        return

    original_popen = sp_flash_gui.subprocess.Popen
    original_find = sp_flash_gui.find_sp_flash_tool_dirs
    launched = []
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            extract = root / "cached_firmware"
            extract.mkdir()
            scatter = extract / "MT6572_Android_scatter.txt"
            scatter.write_text("file_name: system.img\n", encoding="utf-8")
            (extract / "system.img").write_bytes(b"img")
            sp_dir = root / "SP_Flash_Tool"
            sp_dir.mkdir()
            exe_name = "flash_tool.exe" if paths.IS_WINDOWS else "flash_tool"
            (sp_dir / exe_name).write_bytes(b"tool")
            da = sp_dir / sp_flash_gui.DA_FILENAME
            da.write_bytes(b"DA")
            scatter_abs = str(scatter.resolve())
            da_abs = str(da.resolve())

            captured = {}

            def fake_popen(*args, **kwargs):
                captured["text"] = (sp_dir / "history.ini").read_text(encoding="utf-8")
                captured["scatter_exists"] = Path(scatter_abs).is_file()
                captured["da_exists"] = Path(da_abs).is_file()
                launched.append(args[0])
                return object()

            sp_flash_gui.find_sp_flash_tool_dirs = lambda: [sp_dir]
            sp_flash_gui.subprocess.Popen = fake_popen
            if not paths.IS_WINDOWS:
                from src import linux_sp_flash

                linux_sp_flash.arch_supported = lambda: True
                linux_sp_flash.ensure_linux_sp_flash_tool = lambda: (True, "")
                linux_sp_flash.bundled_dir = lambda: None
                linux_sp_flash.stage_dir = lambda: sp_dir
                linux_sp_flash.process_env = lambda _directory: os.environ.copy()

            missing = root / "missing_scatter.txt"
            ok, _msg = sp_flash_gui.launch_sp_flash_tool_gui(
                scatter_path=missing,
                extract_dir=extract,
            )
            assert ok is False
            assert launched == []

            ok, _msg = sp_flash_gui.launch_sp_flash_tool_gui(
                model="Y1",
                scatter_path=scatter,
                extract_dir=extract,
            )
            assert ok is True, _msg
            assert launched
            assert Path(launched[-1][0]).name == exe_name
            da_read, scatter_read, _history = sp_flash_gui.read_history_paths(sp_dir / "history.ini")
            assert os.path.normcase(scatter_read) == os.path.normcase(scatter_abs)
            assert os.path.normcase(da_read) == os.path.normcase(da_abs)
            assert captured["scatter_exists"] is True
            assert captured["da_exists"] is True
    finally:
        sp_flash_gui.subprocess.Popen = original_popen
        sp_flash_gui.find_sp_flash_tool_dirs = original_find


def _assert_scroll_widgets_stay_off_the_stylesheet(sheet: str) -> None:
    """A matching rule forces QStyleSheetStyle and its classic arrow bars."""
    for token in (
        "QScrollBar",
        "QScrollArea",
        "QListWidget",
        "QListView",
        "QTextBrowser",
        "QTextEdit",
        "QPlainTextEdit",
        "releaseList",
        "releaseNotes",
        "logView",
        "statusView",
        "updateNotes",
    ):
        assert token not in sheet, token


def test_windows_scrollbar_uses_winui_when_it_exists():
    """windows11 draws the fluent bar. The thin proxy is only the fallback."""
    from src.ui.dark import IS_WINDOWS, _ThinWindowsScrollStyle, setup_native_app_style

    app = QApplication.instance() or QApplication(sys.argv)
    _assert_scroll_widgets_stay_off_the_stylesheet(_build_qss())
    key = setup_native_app_style(app)
    style = app.style()
    names = []
    seen = set()
    while style is not None and id(style) not in seen:
        seen.add(id(style))
        names.append(type(style).__name__)
        style = style.baseStyle() if hasattr(style, "baseStyle") else None
    if str(key).lower() == "windows11":
        assert "_ThinWindowsScrollStyle" not in names
        assert not isinstance(app.style(), _ThinWindowsScrollStyle)
    elif IS_WINDOWS:
        assert "_ThinWindowsScrollStyle" in names
    apply_theme(app)


def test_advanced_install_methods_offer_one_shared_mtkclient():
    """Advanced methods expose MTKClient once, not a separate Mac alias."""
    from src import paths
    from src.flash_service import METHOD_MTK, METHOD_SP
    from src.ui.main_window import _PAGE_SETTINGS

    app = QApplication.instance() or QApplication(sys.argv)
    real_mac = paths.IS_MAC
    real_windows = paths.IS_WINDOWS
    paths.IS_MAC = False
    paths.IS_WINDOWS = False
    window = MainWindow()
    try:
        window.show()
        window._nav_to_page(_PAGE_SETTINGS)
        app.processEvents()
        page = window._settings_page
        page.reveal_advanced_methods()
        page.set_method(METHOD_SP)
        methods = page._available_methods()
        assert methods == (METHOD_SP, METHOD_MTK)
        assert page._method_combo.findData("mtk_mac") == -1
        assert page.current_method() == METHOD_SP

        paths.IS_MAC = True
        page.refresh_settings()
        assert page._available_methods() == (METHOD_MTK,)
        assert page.current_method() == METHOD_MTK
    finally:
        paths.IS_MAC = real_mac
        paths.IS_WINDOWS = real_windows
        window.close()
        app.processEvents()


def test_settings_checkboxes_keep_the_host_native_style():
    """Settings toggles are native QCheckBoxes, not custom-painted switches."""
    from PySide6.QtWidgets import QCheckBox

    from src.ui.dark import _ClassicWindowsHoverStyle
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(sys.argv)
    page = SettingsPage()
    try:
        switches = page.findChildren(QCheckBox)
        assert switches
        assert all(not switch.styleSheet() for switch in switches)
        # Windows may wrap its native delegate for the button hover wash, but
        # it no longer overrides checkbox primitives with a custom switch.
        if isinstance(app.style(), _ClassicWindowsHoverStyle):
            assert app.style().baseStyle() is not None
    finally:
        page.deleteLater()
        app.processEvents()


def test_title_row_switches_when_the_title_says_it_all():
    """No duplicated wording: the toggle rides on the title line instead."""
    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(sys.argv)
    original = translator().lang
    translator().set_language("en")
    page = SettingsPage()
    try:
        for card, switch, caption_key in (
            (page._reminders_card, page._cb_reminders, "settings_reminders_enable"),
            (page._offline_mode_card, page._cb_offline_mode, "settings_offline_mode"),
        ):
            row = card._title_row
            assert switch.parentWidget() is row, caption_key
            assert card.title_label().parentWidget() is row, caption_key
            # The caption still holds the wording (a11y + switch_caption) but
            # is never drawn: the title above it already says the same thing.
            caption = page._switch_rows[switch][1]
            assert caption.isHidden(), caption_key
            assert page.switch_caption(switch) == tr(caption_key)

        # A card whose title is a group name keeps its caption row.
        other = page._switch_rows[page._cb_guided_install][1]
        assert not other.isHidden()
        assert page.switch_caption(page._cb_guided_install) == tr("settings_guided_install")
    finally:
        translator().set_language(original)
        page.deleteLater()
        app.processEvents()


def test_header_bar_follows_an_animated_resize():
    """The right-anchored page heading must not slide during a resize animation."""
    import sys as _sys

    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    bar = getattr(window, "_title_bar", None)
    if bar is None or not _sys.platform.startswith("darwin"):
        window.deleteLater()
        app.processEvents()
        return
    try:
        # An unshown widget only stores its new geometry, so the resize has to
        # land on a visible window for the header to be re-placed at all.
        window.show()
        app.processEvents()
        window._size_animating = False
        window.resize(700, 520)
        app.processEvents()
        assert bar.width() == window.width(), bar.width()

        # The hug/animate path resizes with the flag set; the inline header is
        # a manually placed child, so it still has to take the new width rather
        # than the one the animation started from.
        window._size_animating = True
        window.resize(980, 640)
        app.processEvents()
        assert bar.width() == window.width(), bar.width()

        window._size_animating = False
        window.resize(640, 480)
        app.processEvents()
        assert bar.width() == window.width(), bar.width()
    finally:
        window._size_animating = False
        window.close()
        window.deleteLater()
        app.processEvents()


def test_progress_cancel_stays_in_the_card():
    """The stop prompt fits the card, and either button leaves Select Software selected."""
    from PySide6.QtGui import QPalette

    from src import i18n
    from src.i18n import translator
    from src.state import FlashState
    from src.ui.main_window import _PAGE_FLASH, _PAGE_SELECT

    for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
        cancel = i18n._STRINGS["install_interrupt_cancel"][lang]
        back = i18n._STRINGS["install_interrupt_back"][lang]
        assert cancel and back
        assert len(cancel) <= 16
        assert len(back) <= 8
        for banned in ("SP Flash", "MTKClient", "MTK", "firmware", "Firmware"):
            assert banned not in cancel and banned not in back
    assert i18n._STRINGS["install_interrupt_cancel"]["en"] == "Cancel Install"
    assert i18n._STRINGS["install_interrupt_back"]["en"] == "Back"

    translator().set_language("en")
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.processEvents()
    long_name = "Original Software 2.1.9 (ADB) for Innioasis Y1 Community Archive release"
    try:
        window._set_state(FlashState.S4_FLASHING)
        page = window._flash_page
        page.set_package_name(long_name)
        page.show_flashing()
        window._nav_to_page(_PAGE_FLASH)
        app.processEvents()

        calls = []
        window.service.cancel_flash = lambda: calls.append("flash") or None
        window.service.cancel_extract = lambda: calls.append("extract") or None
        window.service.stop_device_monitor = lambda: calls.append("monitor") or None

        page._on_cancel()
        app.processEvents()
        prompt = page._step_label
        package = page._flash_pkg_label
        assert page._stop_open
        assert page._stop_continue.isVisible()
        assert page._stop_back.isVisible()
        assert not page._cancel_btn.isVisible()
        # Round glyph buttons: the wording is the hover tip and the accessible
        # name, never a label on the button.
        assert page._stop_continue.text() == ""
        assert page._stop_back.text() == ""
        assert page._stop_continue.toolTip() == "Cancel Install"
        assert page._stop_back.toolTip() == "Back"
        assert page._stop_continue.accessibleName() == "Cancel Install"
        assert page._stop_back.accessibleName() == "Back"
        # The interruption card asks the question the round buttons answer, and
        # names the install it is about rather than the firmware.
        assert "cancel" in page._stop_message.lower()
        assert "install" in page._stop_message.lower()
        assert "firmware" not in page._stop_message.lower()
        assert prompt.wordWrap() and package.wordWrap()
        assert prompt.foregroundRole() == QPalette.ColorRole.WindowText
        assert "#fff" not in prompt.styleSheet().lower()

        wrap_w = max(prompt.sizeHint().width(), prompt.minimumWidth(), 160)
        line = prompt.fontMetrics().height()
        prompt_h = prompt.heightForWidth(wrap_w)
        assert prompt.minimumSizeHint().height() >= prompt_h - 2
        if prompt.fontMetrics().horizontalAdvance(prompt.text()) > wrap_w + 8:
            assert prompt_h > line + 2
        assert package.fontMetrics().horizontalAdvance(long_name) > package.sizeHint().width()
        assert package.minimumSizeHint().height() > line + 2

        card = page._progress_card
        card_h = max(card.sizeHint().height(), card.minimumSizeHint().height())
        buttons_h = max(page._stop_continue.height(), page._stop_back.height())
        # Round glyph buttons: a square, fixed-size chip per action.
        from src.ui.flash_page import ROUND_NEXT_BUTTON_PX

        assert page._stop_continue.width() == ROUND_NEXT_BUTTON_PX
        assert page._stop_continue.height() == ROUND_NEXT_BUTTON_PX
        # The accent chip leads: "back to the install" on the left, "stop it"
        # on the right, so the two arrows point away from each other.
        assert list(page._stop_actions._buttons) == [page._stop_back, page._stop_continue]
        assert page._stop_back._accent is True
        assert page._stop_continue._accent is False
        # They sit beside the copy, so they no longer add a row to the card:
        # the height is the wrapped copy plus the card's own padding.
        assert card_h >= prompt.minimumSizeHint().height() + package.minimumSizeHint().height() - 8
        assert card_h > buttons_h + line
        assert window.minimumHeight() >= page.sizeHint().height()
        assert window.minimumWidth() >= 600

        # The back arrow only puts the check away: the install keeps running.
        page._stop_back.click()
        app.processEvents()
        assert calls == []
        assert window._install_run_active()
        assert window._stack.currentIndex() == _PAGE_FLASH
        assert not page._stop_open
        assert not page._stop_actions.isVisible()
        assert page._stop_label.text() != page._stop_message

        # ...and the cross is the one that confirms the stop.
        page._on_cancel()
        app.processEvents()
        assert page._stop_open
        page._stop_continue.click()
        app.processEvents()
        assert "flash" in calls
        assert "extract" in calls
        assert not window._install_run_active()
        assert not window._download_active
        assert window._stack.currentIndex() == _PAGE_SELECT
        select = window._nav_buttons["nav_select_package"][0]
        assert select.isChecked()
        assert select.text() == "Select Software"

        calls.clear()
        window._download_active = True

        class _Worker:
            def cancel(self):
                calls.append("download")

        window._select_page._download_worker = _Worker()
        window._set_state(FlashState.S2_WAIT_CONNECTION)
        page.show_downloading()
        page.set_package_name(long_name)
        window._nav_to_page(_PAGE_FLASH)
        app.processEvents()
        page._on_cancel_download()
        app.processEvents()
        status = page._download_status_label
        status_h = status.heightForWidth(max(status.sizeHint().width(), 160))
        assert status.minimumSizeHint().height() >= status_h - 2
        # Round glyph buttons: the wording is the hover tip and the accessible
        # name, never a label on the button.
        assert page._stop_continue.text() == ""
        assert page._stop_back.text() == ""
        assert page._stop_continue.toolTip() == "Cancel Install"
        assert page._stop_back.toolTip() == "Back"
        assert page._stop_continue.accessibleName() == "Cancel Install"
        assert page._stop_back.accessibleName() == "Back"
        # On the download card the cross confirms the stop too, and the back
        # arrow would have left the download alone.
        page._stop_back.click()
        app.processEvents()
        assert "download" not in calls
        assert window._download_active
        page._on_cancel_download()
        app.processEvents()
        page._stop_continue.click()
        app.processEvents()
        assert "download" in calls
        assert window._select_page._download_worker is None
        assert not window._download_active
        assert not window._install_run_active()
        assert window._stack.currentIndex() == _PAGE_SELECT
        assert select.isChecked()
        assert select.text() == "Select Software"
    finally:
        window.close()
        app.processEvents()


def test_dark_to_light_leaves_combos_and_notes_readable():
    """A live dark-to-light switch must not leave a black list or unreadable combos."""
    from PySide6.QtGui import QPalette

    from src.ui.dark import apply_theme, contrast_ratio

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    window = MainWindow()
    window.show()
    app.processEvents()
    page = window._select_page
    page._notes.setHtml("<p>Koensayr 2.4.0 release notes</p>")
    page._release_list.addItem("2.4.0")
    apply_theme(app, force_dark=False)
    app.processEvents()

    def readable(widget, label):
        palette = widget.palette()
        fg = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
        bg = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base)
        assert bg.alpha() > 200, (label, bg.name(), bg.alpha())
        assert bg.lightness() > 40, (label, bg.name())
        assert contrast_ratio(fg, bg) >= 3.0, (label, fg.name(), bg.name())

    for combo in (page._model_combo, page._type_combo, page._software_combo):
        readable(combo, "combo")
        readable(combo.view().viewport(), "combo-popup")
    readable(page._notes.viewport(), "notes")
    readable(page._release_list.viewport(), "releases")
    tip = page._type_combo.toolTip()
    assert "Type A" in tip
    assert "Type B" in tip
    # Plain text with real newlines: the platform draws this tip, and a
    # rich-text document would need its own colours written into it.
    assert "\n" in tip and "<qt>" not in tip
    assert page._type_help_icon.toolTip() == tip
    assert page._type_label.toolTip() == tip
    assert page._type_help_icon.cursor().shape() == Qt.ArrowCursor
    assert not hasattr(page, "_type_help_btn")
    assert not hasattr(page, "_show_device_type_help")
    sheet = app.styleSheet()
    _assert_scroll_widgets_stay_off_the_stylesheet(sheet)
    window.close()
    app.processEvents()


def test_caption_drag_leaves_controls_alone():
    """The title band moves the window. Buttons, combos, and links do not."""
    from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

    from src.ui.main_window import caption_drag_blocked

    assert not caption_drag_blocked(QLabel("Select Software"))
    link = QLabel('<a href="https://example.com">Ryan Specter</a>')
    link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
    assert caption_drag_blocked(link)
    assert caption_drag_blocked(QPushButton("Close"))
    assert caption_drag_blocked(QComboBox())


def test_unified_caption_client_rect():
    """The client Qt paints is the client Windows reports, on every side.

    Qt sizes a window as ``client + (SM_CXSIZEFRAME + SM_CXPADDEDBORDER)`` per
    side. Returning a thinner edge than that hands the app a client *wider*
    than the width Qt asked for and painted, and the leftover columns stay
    unpainted: an opaque black bar down the right edge of the window. The
    caption row is still the window's own (no extra top inset) when restored.
    """
    from src.ui.main_window import client_rect_for_unified_caption

    frame_x = frame_y = 8
    padded = 4
    inset = frame_x + padded
    left, top, right, bottom = (10, 20, 800, 600)

    restored = client_rect_for_unified_caption(
        (left, top, right, bottom), maximized=False,
        frame_x=frame_x, frame_y=frame_y, padded=padded,
    )
    assert restored == (left + inset, top, right - inset, bottom - inset)
    # Qt's arithmetic, expressed as the invariant the black bar violated.
    assert (right - left) - (restored[2] - restored[0]) == 2 * inset
    assert (bottom - top) - (restored[3] - restored[1]) == inset

    maximized = client_rect_for_unified_caption(
        (0, 0, 1920, 1080), maximized=True, frame_x=8, frame_y=8, padded=4,
    )
    assert maximized == (12, 12, 1908, 1068)


def test_completion_check_mark_and_opt_out():
    """A finished install shows a check, hugs the card, and can hide the coffee note."""
    from src.ui.main_window import DEFAULT_WINDOW_HEIGHT, _PAGE_FLASH

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window._install_complete = True
        window._flash_page.show_flashing()
        window._flash_page.set_device_done()
        window._flash_page.show_completion_appeal(True)
        window._nav_to_page(_PAGE_FLASH)
        app.processEvents()
        page = window._flash_page
        assert page._done_mark.isVisible()
        assert not page._cancel_btn.isVisible()
        assert not page._cancel_btn.isEnabled()
        pitch = page._appeal_label.text()
        assert "All done" in pitch
        assert "thank" in pitch
        assert page._coffee_btn.text() == "Buy me a coffee"
        assert page._appeal.isVisible()
        with_note = window.height()
        assert with_note < DEFAULT_WINDOW_HEIGHT - 40

        # Off means the supporter credits and donation prompts are hidden.
        window._settings_page._cb_hide_donations.setChecked(False)
        app.processEvents()
        page.show_completion_appeal(False)
        window._adjust_window_geometry()
        app.processEvents()
        assert not page._appeal.isVisible()
        coffee = window._settings_page._settings_coffee_btn
        assert not coffee.isHidden()
        assert coffee.text() == "Buy me a coffee"
        assert window._settings_page.switch_caption(
            window._settings_page._cb_hide_donations
        ) == tr("settings_show_donations")
        assert "buy a coffee from Settings" in window._settings_page._lbl_hide_tip.text()
        assert window.height() <= with_note
    finally:
        device_tracking.set_donation_ui_disabled(False)
        window.close()
        app.processEvents()


def test_read_only_sp_flash_tool_is_copied_before_launch():
    """Program Files and an AppImage cannot store history.ini. Launch uses a copy."""
    from src import sp_flash_gui

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "Program Files" / "SP_Flash_Tool"
        source.mkdir(parents=True)
        binary = "flash_tool.exe" if sys.platform.startswith("win") else "flash_tool"
        (source / binary).write_bytes(b"tool")
        dest = root / "user" / "SP_Flash_Tool"

        def writable(directory):
            return Path(directory) == dest

        original_user = sp_flash_gui._user_sp_copy_dir
        original_writable = sp_flash_gui._dir_is_writable
        sp_flash_gui._user_sp_copy_dir = lambda: dest
        sp_flash_gui._dir_is_writable = writable
        try:
            launched = sp_flash_gui.ensure_launch_dir(source)
        finally:
            sp_flash_gui._user_sp_copy_dir = original_user
            sp_flash_gui._dir_is_writable = original_writable

        assert launched == dest
        assert (dest / binary).is_file()


def test_sidebar_selection_is_not_an_accent_plate():
    """The selected row is a neutral veil. The accent stays on the Windows bar."""
    from PySide6.QtGui import QColor

    from src.ui.sidebar import sidebar_selected_color

    dark = sidebar_selected_color(QColor("#181b20"))
    accent = QColor("#e11d48")
    assert dark.alpha() < 80
    assert (dark.red(), dark.green(), dark.blue()) != (accent.red(), accent.green(), accent.blue())


def test_combo_popup_uses_translucent_material():
    """An open combo list is a frost, not a solid plate."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QComboBox, QWidget

    from src.ui.glass import POPUP_FROST_ALPHA, apply_popup_material

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    popup = QWidget()
    popup.setWindowFlag(Qt.WindowType.Popup, True)
    combo = QComboBox(popup)
    combo.addItems(["Original Software", "Rockbox"])
    apply_popup_material(popup)
    base = popup.palette().color(QPalette.ColorRole.Base)
    assert base.alpha() == POPUP_FROST_ALPHA
    assert base.alpha() < 200
    view = combo.view()
    assert view.autoFillBackground() is False


def test_light_menus_and_sidebar_selection_follow_the_theme():
    """Light windows keep a light combo menu. Selection contrasts in both themes."""
    import re

    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QComboBox, QWidget

    from src.ui.dark import contrast_ratio
    from src.ui.glass import POPUP_FROST_ALPHA, apply_popup_material
    from src.ui.sidebar import (
        sidebar_selected_color,
        sidebar_selected_fill,
        sidebar_selected_text_color,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=False)
    try:
        window = app.palette().color(QPalette.ColorRole.Window)
        assert window.lightness() > 140

        popup = QWidget()
        popup.setWindowFlag(Qt.WindowType.Popup, True)
        combo = QComboBox(popup)
        combo.addItems(["English", "Français", "Español"])
        apply_popup_material(popup)

        def assert_light_menu(widget, label):
            palette = widget.palette()
            base = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Base)
            text = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Text)
            assert base.lightness() > 160, (label, base.name(), base.alpha())
            assert base.alpha() > 200, (label, base.alpha())
            assert text.lightness() < 80, (label, text.name())
            assert contrast_ratio(text, base) >= 4.5, (label, text.name(), base.name())
            highlight = palette.color(QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight)
            highlighted = palette.color(
                QPalette.ColorGroup.Active, QPalette.ColorRole.HighlightedText
            )
            assert contrast_ratio(highlighted, highlight) >= 4.5, (
                label,
                highlighted.name(),
                highlight.name(),
            )

        assert_light_menu(popup, "popup")
        assert_light_menu(combo, "combo")
        view = combo.view()
        assert_light_menu(view, "view")
        assert_light_menu(view.viewport(), "viewport")
        sheet = app.styleSheet()
        assert re.search(r"(^|\n)\s*QComboBox\b", sheet) is None
        assert re.search(r"(^|\n)\s*QScrollBar\b", sheet) is None

        apply_theme(app, force_dark=True)
        dark_popup = QWidget()
        dark_popup.setWindowFlag(Qt.WindowType.Popup, True)
        dark_combo = QComboBox(dark_popup)
        dark_combo.addItems(["English"])
        apply_popup_material(dark_popup)
        dark_base = dark_popup.palette().color(QPalette.ColorRole.Base)
        dark_text = dark_popup.palette().color(QPalette.ColorRole.Text)
        assert dark_base.lightness() < 80
        assert dark_base.alpha() == POPUP_FROST_ALPHA
        assert dark_text.lightness() > 160
        assert app.palette().color(QPalette.ColorRole.Window).lightness() < 80

        light_bg = QColor("#f8fafc")
        light_fill = sidebar_selected_fill(light_bg)
        assert light_fill.lightness() < light_bg.lightness()
        assert contrast_ratio(sidebar_selected_text_color(light_bg), light_fill) >= 4.5

        dark_bg = QColor("#181b20")
        dark_fill = sidebar_selected_fill(dark_bg)
        dark_raw = sidebar_selected_color(dark_bg)
        assert dark_fill.lightness() < 80
        assert not (dark_raw.alpha() > 200 and dark_raw.lightness() > 180)
        assert contrast_ratio(sidebar_selected_text_color(dark_bg), dark_fill) >= 4.5
        accent = QColor("#e11d48")
        assert (light_fill.red(), light_fill.green(), light_fill.blue()) != (
            accent.red(),
            accent.green(),
            accent.blue(),
        )
    finally:
        apply_theme(app, force_dark=None)


def test_settings_switches_are_on_the_right_and_install_modes_exclude_each_other():
    from PySide6.QtWidgets import QAbstractScrollArea, QScrollArea

    from src import config, device_tracking
    from src.donation_dialog import DonationStatusBar
    from src.i18n import tr
    from src.sp_flash_gui import is_sp_flash_gui_supported
    from src.ui.dialogs import DiagnosticsView
    from src.ui.main_window import MainWindow
    from src.ui.scrollbars import configure_scroll_area
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(sys.argv)
    device_tracking.set_terminal_install_enabled(False)
    device_tracking.set_sp_gui_install_enabled(False)
    page = SettingsPage()
    try:
        row, caption = page._switch_rows[page._cb_guided_install]
        layout = row.layout()
        assert layout.itemAt(0).widget() is caption
        assert layout.itemAt(1).widget() is page._cb_guided_install
        assert caption.cursor().shape() == Qt.ArrowCursor
        assert page._cb_guided_install.cursor().shape() == Qt.ArrowCursor
        assert page.switch_caption(page._cb_guided_install) == tr("settings_guided_install")
        assert page._cb_guided_install.isChecked()
        assert not page._cb_terminal_install.isChecked()
        assert not page._cb_sp_gui_install.isChecked()
        assert not device_tracking.terminal_install_enabled()
        assert not device_tracking.sp_gui_install_enabled()

        page._cb_terminal_install.setChecked(True)
        app.processEvents()
        assert page._cb_terminal_install.isChecked()
        assert not page._cb_guided_install.isChecked()
        assert not page._cb_sp_gui_install.isChecked()
        assert device_tracking.terminal_install_enabled()
        assert not device_tracking.sp_gui_install_enabled()

        if is_sp_flash_gui_supported():
            page._cb_sp_gui_install.setChecked(True)
            app.processEvents()
            assert page._cb_sp_gui_install.isChecked()
            assert not page._cb_terminal_install.isChecked()
            assert not page._cb_guided_install.isChecked()
            assert device_tracking.sp_gui_install_enabled()
            assert not device_tracking.terminal_install_enabled()
            device_tracking.set_terminal_install_enabled(True)
            page.refresh_settings()
            assert page._cb_sp_gui_install.isChecked()
            assert not page._cb_terminal_install.isChecked()
            assert not device_tracking.terminal_install_enabled()

        page._cb_guided_install.setChecked(True)
        app.processEvents()
        assert page._cb_guided_install.isChecked()
        assert not page._cb_terminal_install.isChecked()
        assert not page._cb_sp_gui_install.isChecked()
        assert not device_tracking.terminal_install_enabled()
        assert not device_tracking.sp_gui_install_enabled()

        was_mtk = config.IS_MEDIATEK_INSTALLER
        config.IS_MEDIATEK_INSTALLER = True
        try:
            mediatek = SettingsPage()
            mediatek.apply_brand_mode(True)
            assert mediatek._cb_guided_install.isChecked()
            assert mediatek.switch_caption(mediatek._cb_guided_install) == tr(
                "settings_guided_install"
            )
            assert not mediatek._switch_rows[mediatek._cb_guided_install][0].isHidden()
            assert not mediatek._terminal_card.isHidden()
            mediatek.deleteLater()
        finally:
            config.IS_MEDIATEK_INSTALLER = was_mtk

        if sys.platform == "win32":
            area = QScrollArea()
            configure_scroll_area(area, vertical=Qt.ScrollBarAsNeeded)
            assert area.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
            assert area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
            window = MainWindow()
            window.show()
            app.processEvents()
            try:
                areas = window.findChildren(QAbstractScrollArea)
                assert areas
                for area in areas:
                    assert area.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff, type(area)
                    assert area.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff, type(area)
                window._unlock_diagnostics()
                diag = window._ensure_diagnostics_page()
                assert isinstance(diag, DiagnosticsView)
                assert diag._view.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
                assert "QScrollBar" not in app.styleSheet()
                bar = window.statusBar()
                assert bar.autoFillBackground() is False
                assert not bar.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                assert "background: transparent" in bar.styleSheet()
                assert "background-color:" not in bar.styleSheet()
            finally:
                window.close()
                app.processEvents()

        loose = DonationStatusBar()
        assert loose.autoFillBackground() is False
        assert "background: transparent" in loose.styleSheet()
        loose.deleteLater()
    finally:
        device_tracking.set_terminal_install_enabled(False)
        device_tracking.set_sp_gui_install_enabled(False)
        page.deleteLater()
        app.processEvents()


def test_post_install_support_appeal():
    """A finished install grows into a coffee appeal. Install Software stays selected."""
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QCheckBox, QDialog, QWidget

    from src.ui.main_window import _PAGE_FLASH, _PAGE_SELECT, _PAGE_SUPPORT
    from src.ui.support_appeal import PORTRAIT_URL, bundled_portrait_path, circle_pixmap

    app = QApplication.instance() or QApplication(sys.argv)
    device_tracking.set_donation_ui_disabled(False)
    device_tracking.set_donation_install_prompt_disabled(False)
    assert bundled_portrait_path().is_file()
    assert PORTRAIT_URL.endswith("developer.png")
    sample = circle_pixmap(QPixmap(str(bundled_portrait_path())), 56)
    assert not sample.isNull()
    corner = sample.toImage().pixelColor(0, 0)
    center = sample.toImage().pixelColor(28, 28)
    assert corner.alpha() < 20
    assert center.alpha() > 200

    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        heights = []
        original = window._animate_window_size

        def _spy(width, height, minimum=None):
            heights.append(int(height))
            return original(width, height, minimum)

        window._animate_window_size = _spy
        window._package_name = "Rockbox"
        window._handle_flash_success()
        app.processEvents()

        page = window._flash_page
        install = window._nav_buttons["nav_select_package"][0]
        assert window._stack.currentIndex() == _PAGE_FLASH
        assert install.isChecked()
        assert install.text() == "Install Software"
        assert not window._support_btn.isChecked()
        assert window._select_page._title.text() == "Install complete"
        assert page._appeal.isVisible()
        assert page._done_mark.isVisible()
        assert page._coffee_btn.text() == "Buy me a coffee"
        assert page._coffee_btn.cursor().shape() == Qt.ArrowCursor
        assert page._not_now_btn.text() == "Not now"
        assert page._not_now_btn.cursor().shape() == Qt.ArrowCursor
        assert "Don't ask me again" in page._appeal_dont.text()
        assert page._appeal_dont.cursor().shape() == Qt.PointingHandCursor
        assert not isinstance(page._appeal_dont, QCheckBox)
        assert "All done" in page._intro.headline.text()
        assert "underline" in page._intro.headline.text()
        portrait = page._intro.portrait.pixmap()
        assert portrait is not None and not portrait.isNull()
        assert portrait.toImage().pixelColor(0, 0).alpha() < 20
        blob = " ".join(
            widget.text()
            for widget in page._appeal.findChildren(QWidget)
            if hasattr(widget, "text") and callable(widget.text)
        )
        for banned in ("Honeygain", "PayPal", "Revolut", "Patreon", "Bitcoin", "Crypto"):
            assert banned not in blob, banned
        assert heights and max(heights) > min(heights)
        import inspect
        source = inspect.getsource(type(window)._animate_window_size)
        assert "200" in source and "OutCubic" in source

        window._flash_page._appeal_dont.linkActivated.emit("dont-ask")
        app.processEvents()
        assert device_tracking.is_donation_ui_disabled(window.settings)
        # "Show …" is off once the user asked never to see the appeal again.
        assert not window._settings_page._cb_hide_donations.isChecked()
        assert not page._appeal.isVisible()
        assert window._install_complete
        assert window._stack.currentIndex() == _PAGE_FLASH
        assert install.isChecked()
        assert window._select_page._title.text() == "Install complete"

        page._done_mark.click()
        app.processEvents()
        assert window._stack.currentIndex() == _PAGE_SELECT
        assert not window._install_complete

        device_tracking.set_donation_ui_disabled(False, window.settings)
        window._settings_page.refresh_settings()
        window._package_name = "Rockbox"
        window._handle_flash_success()
        app.processEvents()
        assert window._flash_page._appeal.isVisible()
        window._flash_page._not_now_btn.click()
        app.processEvents()
        assert not device_tracking.is_donation_ui_disabled(window.settings)
        assert window._stack.currentIndex() == _PAGE_SELECT

        device_tracking.set_donation_ui_disabled(True, window.settings)
        window._package_name = "Rockbox"
        window._handle_flash_success()
        app.processEvents()
        assert not window._flash_page._appeal.isVisible()
        assert window._stack.currentIndex() == _PAGE_FLASH
        assert install.isChecked()

        device_tracking.set_donation_ui_disabled(False, window.settings)
        window._settings_page.refresh_settings()
        window._on_install_done()
        window._support_btn.click()
        app.processEvents()
        support = window._support_page
        assert window._stack.currentIndex() == _PAGE_SUPPORT
        assert window._support_btn.isChecked()
        assert not isinstance(support, QDialog)
        assert support.styleSheet() == ""
        labels = " ".join(
            widget.text()
            for widget in support.findChildren(QWidget)
            if hasattr(widget, "text") and callable(widget.text)
        )
        assert support._coffee.text() == "Buy us a coffee"
        assert "Honeygain" not in labels
        assert "PayPal" not in labels
        assert "Revolut" not in labels
        assert "Patreon" not in labels
        assert not support._intro.portrait.pixmap().isNull()
    finally:
        device_tracking.set_donation_ui_disabled(False)
        window.close()
        app.processEvents()


def test_settings_donations_dismiss_link():
    """The donations card link removes that Settings section and keeps it removed."""
    from PySide6.QtCore import QEasingCurve, QSettings
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

    from src import i18n
    from src.i18n import tr, tr_brand, translator
    from src.ui import widgets as widgets_mod
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(sys.argv)
    translator().set_language("en")
    for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
        assert i18n._STRINGS["settings_donations_dismiss"].get(lang)
    assert "settings_donations_dismiss_mediatek" not in i18n._STRINGS
    assert tr_brand("settings_donations_dismiss") == "Never show me this again"
    assert 180 <= widgets_mod.COLLAPSE_MS <= 220

    store = QSettings("innioasis", "updater")
    key = "settings_donations_section_dismissed"
    grouped = f"preferences/{key}"
    had_key = key in store.allKeys() or grouped in store.allKeys()
    previous = device_tracking.is_settings_donations_section_dismissed()
    donations_were_off = device_tracking.is_donation_ui_disabled()
    install_prompts_were_off = device_tracking.is_donation_install_prompt_disabled()
    # This test covers a permanent section dismissal rather than the global UI
    # switch; pin that switch off so the dismissal cannot change its state.
    device_tracking.set_donation_ui_disabled(True)
    device_tracking.set_donation_install_prompt_disabled(True)
    device_tracking.set_settings_donations_section_dismissed(False)

    page = SettingsPage()
    page2 = None
    host = None
    original_motion = None
    try:
        page.resize(720, 960)
        page.show()
        app.processEvents()
        coffee = page._settings_coffee_btn
        discord = page._settings_discord_btn
        reddit = page._settings_reddit_btn
        link = page._dismiss_donations_link
        assert isinstance(link, QLabel)
        assert coffee.parentWidget() is link.parentWidget()
        row = coffee.parentWidget().layout()
        assert row.indexOf(discord) > row.indexOf(coffee)
        assert row.indexOf(reddit) > row.indexOf(discord)
        assert row.indexOf(link) > row.indexOf(reddit)
        assert discord.text() == "Discord"
        assert reddit.text() == "Reddit"
        assert not discord.icon().isNull()
        assert not reddit.icon().isNull()
        assert discord.iconSize().width() == 10
        assert reddit.iconSize().width() == 10
        assert link.cursor().shape() == Qt.PointingHandCursor
        assert coffee.cursor().shape() == Qt.ArrowCursor
        assert "Never show me this again" in link.text()
        assert 'href="dismiss"' in link.text()
        assert coffee.width() > 0 and link.width() > 0
        assert page._settings_discord_btn.isVisible()
        assert page._settings_reddit_btn.isVisible()
        assert page._donations_card.isVisible()
        assert page._card_layout.isEnabled()

        link.linkActivated.emit("dismiss")
        app.processEvents()
        assert device_tracking.is_settings_donations_section_dismissed()
        assert device_tracking.is_donation_ui_disabled() is True
        if widgets_mod.prefers_reduced_motion():
            assert page._donations_card.isHidden()
            assert page._card_layout.isEnabled()
        else:
            QTest.qWait(widgets_mod.COLLAPSE_MS + 80)
            assert page._donations_card.isHidden()
        assert not page._settings_coffee_btn.isVisible()
        assert not page._settings_discord_btn.isVisible()
        assert not page._settings_reddit_btn.isVisible()
        assert not page._cb_hide_donations.isVisible()
        assert not page._lbl_hide_tip.isVisible()

        page2 = SettingsPage()
        page2.resize(720, 960)
        page2.show()
        app.processEvents()
        page2._card_layout.activate()
        app.processEvents()
        assert page2._donations_card.isHidden()
        assert not page2._donations_card.isVisible()
        assert not page2._settings_coffee_btn.isVisible()
        assert not page2._settings_discord_btn.isVisible()
        assert not page2._settings_reddit_btn.isVisible()
        assert not page2._dismiss_donations_link.isVisible()
        assert "Never show me this again" not in _visible_text(page2)
        _assert_no_hole_where_the_card_was(page2)

        original_motion = widgets_mod.prefers_reduced_motion
        widgets_mod.prefers_reduced_motion = lambda: False
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(18)
        above = QWidget()
        closing = QWidget()
        below = QWidget()
        for card, height in ((above, 40), (closing, 80), (below, 50)):
            card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            card.setFixedHeight(height)
            lay.addWidget(card)
        lay.addStretch(1)
        host.resize(240, 320)
        host.show()
        app.processEvents()
        start_below = below.y()
        start_above = above.y()
        widgets_mod.close_layout_gap(closing)
        anim = closing._collapse_anim
        assert anim is not None
        assert anim.duration() == widgets_mod.COLLAPSE_MS
        assert anim.easingCurve().type() == QEasingCurve.Type.OutCubic
        # setCurrentTime is synchronous. Pumping events here would let the
        # timer run past the sample we want.
        anim.setCurrentTime(widgets_mod.COLLAPSE_MS // 2)
        assert closing.isVisible()
        # OutCubic is front-loaded: halfway through the 200ms, most of the gap is gone.
        assert closing.height() == 10
        assert below.y() < start_below
        assert above.y() == start_above
        anim.setCurrentTime(widgets_mod.COLLAPSE_MS)
        assert closing.isHidden()
        assert host.layout().isEnabled()
        assert below.y() == above.y() + above.height() + lay.spacing()
        assert above.y() == start_above
    finally:
        if original_motion is not None:
            widgets_mod.prefers_reduced_motion = original_motion
        if host is not None and host.layout() is not None:
            host.layout().setEnabled(True)
            host.close()
            host.deleteLater()
        translator().set_language("en")
        store = QSettings("innioasis", "updater")
        if had_key:
            device_tracking.set_settings_donations_section_dismissed(previous)
        else:
            store.remove(key)
            store.remove(grouped)
        device_tracking.set_donation_ui_disabled(donations_were_off)
        device_tracking.set_donation_install_prompt_disabled(install_prompts_were_off)
        store.sync()
        page.close()
        page.deleteLater()
        if page2 is not None:
            page2.close()
            page2.deleteLater()
        app.processEvents()
    print("settings donations dismiss: pass")


def _visible_text(root) -> str:
    from PySide6.QtWidgets import QWidget

    parts = []
    for widget in root.findChildren(QWidget):
        if not widget.isVisible() or not hasattr(widget, "text"):
            continue
        text = widget.text
        if callable(text):
            parts.append(str(text()))
    return " ".join(parts)


def _assert_no_hole_where_the_card_was(page):
    layout = page._card_layout
    index = layout.indexOf(page._donations_card)

    def nearest(step):
        i = index + step
        while 0 <= i < layout.count():
            item = layout.itemAt(i)
            widget = item.widget() if item is not None else None
            if widget is not None and widget.isVisible():
                return widget
            i += step
        return None

    above = nearest(-1)
    below = nearest(1)
    assert above is not None and below is not None
    assert above.height() > 0 and below.height() > 0
    gap = below.y() - (above.y() + above.height())
    assert 0 <= gap <= layout.spacing() + 6, gap


def test_caption_icon_follows_the_macos_icon_style():
    """The caption icon is the system's own art for this app, style included.

    System Settings → Appearance → Icon & widget style (Default / Dark / Clear /
    Tinted) is published by AppKit as the icon appearance configuration, and
    IconServices draws the bundle icon in it — so the caption matches the Dock
    and Finder, including a tint the bundled PNG cannot imitate. The bundled
    artwork stays as the fallback, and the system is only asked again when the
    style actually changes.
    """
    from unittest.mock import patch

    from PySide6.QtGui import QPixmap

    from src.ui import icons

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    label = window._icon_label
    assert not label.pixmap().isNull()
    assert label.pixmap().width() == window._icon_size

    marker = QPixmap(window._icon_size, window._icon_size)
    marker.fill(Qt.red)
    with patch.object(icons, "macos_icon_style", return_value=("7", "tinted")):
        with patch.object(icons, "macos_app_icon_pixmap", return_value=marker) as draw:
            window._apply_caption_icon()
            assert draw.call_count == 1
            assert label.pixmap().toImage() == marker.toImage()
            window._apply_caption_icon()
            assert draw.call_count == 1, "an unchanged style must not redraw the icon"

    # No system icon (other platforms, or a failed draw): bundled artwork, and
    # never a blank caption.
    with patch.object(icons, "macos_icon_style", return_value=("", "")):
        with patch.object(icons, "macos_app_icon_pixmap", return_value=None):
            window._apply_caption_icon()
            assert not label.pixmap().isNull()
            assert label.pixmap().width() == window._icon_size

    if sys.platform == "darwin":
        theme, tint = icons.macos_icon_style()
        assert theme, "this Mac must publish its icon appearance configuration"
        assert isinstance(tint, str)
    window.close()
    app.processEvents()


def test_tooltips_stay_native_and_readable():
    """Tooltips are the host's own, drawn by the platform style.

    No QToolTip stylesheet rule and no forced tip palette: a rule puts the tip
    on QStyleSheetStyle, which replaces the desktop's own tip frame (the macOS
    help tag, the WinUI tip) with a flat plate — the ugly tips this replaced.
    The theme pairs still exist for anything that paints a surface itself.
    """
    import re

    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QToolTip, QWidget

    from src.ui.dark import T, apply_theme, contrast_ratio, tooltip_surface_colors

    # Comments may name the widget; a *rule* for it is what must not exist.
    rules = re.sub(r"/\*.*?\*/", "", _build_qss(), flags=re.S)
    assert not re.search(r"(?m)^\s*QToolTip\b", rules), "tooltips must not be styled"
    assert not re.search(r"(?m)QToolTip[^\n]*\{", rules), "tooltips must not be styled"

    app = QApplication.instance() or QApplication(sys.argv)

    def _assert_theme(dark: bool) -> None:
        # A palette the platform set is left exactly as it is.
        poisoned = QPalette()
        poisoned.setColor(
            QPalette.ColorGroup.All, QPalette.ColorRole.ToolTipBase, QColor("#123456")
        )
        poisoned.setColor(
            QPalette.ColorGroup.All, QPalette.ColorRole.ToolTipText, QColor("#abcdef")
        )
        QToolTip.setPalette(poisoned)

        apply_theme(app, force_dark=dark)
        kept = QToolTip.palette()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            assert kept.color(group, QPalette.ColorRole.ToolTipBase).name() == "#123456"
            assert kept.color(group, QPalette.ColorRole.ToolTipText).name() == "#abcdef"

        # And what the platform then draws stays legible in both appearances:
        # the tip is a surface with text on it, never one flat block.
        background, foreground = tooltip_surface_colors()
        assert contrast_ratio(foreground, background) >= 4.5
        if dark:
            assert background.lightness() < 80
            assert foreground.lightness() > 180
        else:
            assert background.lightness() > 180
            assert foreground.lightness() < 80
        tokens = T()
        assert background.name() == QColor(tokens.bg_tooltip).name()

        host = QWidget()
        host.resize(120, 40)
        host.show()
        QToolTip.showText(QPoint(8, 8), "Type A\nType B", host)
        app.processEvents()
        label = next(
            (w for w in app.allWidgets() if w.metaObject().className() == "QTipLabel"),
            None,
        )
        assert label is not None
        image = label.grab().toImage()
        light_pixels = dark_pixels = 0
        for y in range(0, image.height(), 2):
            for x in range(0, image.width(), 2):
                lightness = image.pixelColor(x, y).lightness()
                if lightness > 180:
                    light_pixels += 1
                elif lightness < 80:
                    dark_pixels += 1
        assert light_pixels > 0 and dark_pixels > 0, (
            dark,
            light_pixels,
            dark_pixels,
            image.width(),
            image.height(),
        )
        QToolTip.hideText()
        host.close()
        app.processEvents()

    _assert_theme(False)
    _assert_theme(True)


def test_type_help_tip_is_plain_text():
    """The Type tip is plain text so the platform draws it, rich text does not."""
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    page = SelectPackagePage()
    tip = page._type_combo.toolTip()
    assert tip
    assert "<qt>" not in tip and "<span" not in tip and "style=" not in tip
    assert "Type A" in tip and "Type B" in tip
    assert tip == page._type_help_icon.toolTip()
    page.close()
    app.processEvents()


def _near(color, reference, tolerance: int = 24) -> bool:
    """True when *color* is the painted *reference* up to anti-aliasing."""
    return (
        abs(color.red() - reference.red()) <= tolerance
        and abs(color.green() - reference.green()) <= tolerance
        and abs(color.blue() - reference.blue()) <= tolerance
    )


def test_progress_and_status_follow_the_live_theme():
    """Light text is dark, dark text is light, and the bars stay transparent.

    Applying the other theme updates the existing window. A stylesheet polish
    must not put the previous foreground back.
    """
    from PySide6.QtGui import QPalette

    from src.ui.dark import _build_qss

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=False)
    window = MainWindow()
    window.show()
    app.processEvents()
    page = window._flash_page
    page.show_downloading()
    page.update_download_progress(87, "200.1 MB / 2300 MB (87%) — 17.9 MB/s")
    window._stack.setCurrentWidget(page)
    bar = window.statusBar()
    bar.showMessage("Package ready")
    app.processEvents()
    same = window

    def foreground(widget):
        return widget.palette().color(widget.foregroundRole())

    def assert_text(dark_words: bool):
        for widget in (
            page._download_status_label,
            page._download_pkg_label,
            page._download_progress,
            bar._status_label,
            bar._goal_label,
        ):
            lightness = foreground(widget).lightness()
            if dark_words:
                assert lightness < 80, (type(widget).__name__, foreground(widget).name())
            else:
                assert lightness > 180, (type(widget).__name__, foreground(widget).name())
        from src.ui.dark import contrast_ratio, progress_bar_colors

        track, fill, label = progress_bar_colors(page._download_progress)
        image = page._download_progress.grab().toImage()
        dark_pixels = light_pixels = accent_pixels = track_pixels = 0
        for y in range(image.height()):
            for x in range(image.width()):
                color = image.pixelColor(x, y)
                if color.alpha() < 20:
                    continue
                if color.lightness() < 60:
                    dark_pixels += 1
                elif color.lightness() > 200:
                    light_pixels += 1
                if _near(color, fill):
                    accent_pixels += 1
                elif _near(color, track):
                    track_pixels += 1
        assert accent_pixels > 20, "progress bar fill disappeared"
        assert track_pixels > 20, "progress bar track disappeared"
        assert contrast_ratio(label, fill) >= 3.0
        if dark_words:
            assert dark_pixels > 10, "progress percent is not dark on the light bar"
        else:
            assert light_pixels > 10, "progress percent is not light on the dark bar"

    def assert_transparent_surface():
        for widget in (bar, bar._donation_container, bar._status_container):
            assert widget.autoFillBackground() is False
            assert not widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            fill = widget.palette().color(QPalette.ColorRole.Window)
            assert fill.alpha() == 0, (type(widget).__name__, fill.name(), fill.alpha())
        assert "background: transparent" in bar.styleSheet()
        assert "background-color:" not in bar.styleSheet()
        image = bar.grab().toImage()
        corner = image.pixelColor(max(0, image.width() - 4), 2)
        assert corner.alpha() < 16, (corner.name(), corner.alpha())

    try:
        assert_text(True)
        assert_transparent_surface()
        apply_theme(app, force_dark=True)
        app.processEvents()
        assert window is same
        assert_text(False)
        assert_transparent_surface()
        apply_theme(app, force_dark=False)
        app.processEvents()
        app.setStyleSheet(_build_qss())
        app.processEvents()
        assert window is same
        assert_text(True)
        assert_transparent_surface()
    finally:
        window.close()
        app.processEvents()


def test_install_stages_sidebar_visibility_and_compact_height():
    """Verify that during install, extraction, and prep stages, only Install Software
    and Diagnostics are shown in the sidebar, and the window stays compact.
    When cancelled, completed, or failed, the normal sidebar buttons are restored."""
    from src.state import FlashState
    from src.i18n import tr

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()

    nav_select_btn = w._nav_buttons["nav_select_package"][0]

    def assert_active_sidebar():
        assert nav_select_btn.text() == tr("nav_install_package")
        assert w._install_output_btn.isVisible() is True
        assert w._settings_btn.isVisible() is False
        assert w._support_btn.isVisible() is False
        # The community marks follow the Donations setting, install or not.
        assert w._discord_btn.isVisible() is True
        assert w._reddit_btn.isVisible() is True
        assert w._github_btn.isVisible() is True
        assert w._sp_flash_tool_btn.isVisible() is False
        assert w._check_updates_btn.isVisible() is False
        assert w._lang_combo.isVisible() is False
        assert w._lang_label.isVisible() is False

    def assert_idle_sidebar():
        assert nav_select_btn.text() == tr("nav_select_package")
        assert w._install_output_btn.isVisible() is False
        assert w._settings_btn.isVisible() is True
        assert w._support_btn.isVisible() is True
        assert w._discord_btn.isVisible() is True
        assert w._reddit_btn.isVisible() is True
        assert w._lang_combo.isVisible() is True
        assert w._lang_label.isVisible() is True

    try:
        # Initial idle state
        assert_idle_sidebar()

        # 1. PREP STAGE (Power-off prompt)
        w._show_device_prep()
        app.processEvents()
        if w._size_anim is not None:
            w._size_anim.setCurrentTime(200)
        assert w._power_off_prompt is True
        assert w._install_run_active() is True
        assert_active_sidebar()
        assert w.size().height() <= 330

        # 2. DOWNLOAD STAGE
        w._on_download_started("Rockbox 0.5 for Y1", "Y1")
        app.processEvents()
        if w._size_anim is not None:
            w._size_anim.setCurrentTime(200)
        assert w._download_active is True
        assert w._install_run_active() is True
        assert_active_sidebar()
        assert w.size().height() <= 260

        # 3. EXTRACTION STAGE
        w._on_download_finished(True, "/tmp/mock_pkg.zip")
        w._on_prep_started("Rockbox 0.5 for Y1", "Y1")
        app.processEvents()
        if w._size_anim is not None:
            w._size_anim.setCurrentTime(200)
        assert w._prep_active is True
        assert w._install_run_active() is True
        assert_active_sidebar()
        assert w.size().height() <= 260

        # 4. FLASHING STAGE
        w._set_state(FlashState.S4_FLASHING)
        app.processEvents()
        if w._size_anim is not None:
            w._size_anim.setCurrentTime(200)
        assert w._install_run_active() is True
        assert_active_sidebar()

        # 5. ON COMPLETION
        w._install_complete = True
        w._compact_install_screen = False
        w._power_off_prompt = False
        w._prep_active = False
        w._download_active = False
        w._sync_install_nav_entry()
        app.processEvents()
        assert w._install_run_active() is False
        assert_idle_sidebar()

        # 6. ON CANCEL
        w._on_download_started("Firmware", "Y1")
        app.processEvents()
        assert_active_sidebar()
        w._return_to_select_software()
        app.processEvents()
        assert w._install_run_active() is False
        assert_idle_sidebar()

        # 7. ON FAILURE
        w._set_state(FlashState.S4_FLASHING)
        app.processEvents()
        assert_active_sidebar()
        w._show_install_failure("CONNECTION_FAILED")
        app.processEvents()
        assert w._install_run_active() is False
        assert_idle_sidebar()

    finally:
        w.close()
        app.processEvents()


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
    test_window_maximization_enabled()
    test_close_interception_during_install()
    test_flash_page_compact_card_and_heading()
    test_os_standard_iconography()
    test_symbol_color_adaptation_and_states()
    test_platform_sidebar_row_hover_and_selection()
    test_native_style_candidates_follow_the_desktop()
    test_pre_liquid_glass_light_fields_are_not_white_plates()
    test_live_theme_switch_updates_title_card_and_donation_text()
    test_diagnostics_count_matches_lines_from_each_source()
    test_classic_windows_buttons_show_hover()
    test_compact_install_height_counts_the_header_on_every_desktop()
    test_seal_hint_never_widens_the_layout()
    test_symbol_pixmaps_are_marked_hi_dpi_and_never_oversized()
    test_install_failure_stays_on_progress_and_retry_does_not_reflash()
    test_pin_sp_flash_history_uses_extract_scatter_and_tool_da()
    test_sp_gui_handoff_choice_continue_and_sidebar()
    test_release_click_downloads_for_handoff_not_in_app_install()
    test_gui_handoff_package_rules_and_hint_copy()
    test_history_ini_absolute_paths_before_gui_launch()
    test_dark_to_light_leaves_combos_and_notes_readable()
    test_sidebar_selection_is_not_an_accent_plate()
    test_combo_popup_uses_translucent_material()
    test_caption_drag_leaves_controls_alone()
    test_unified_caption_client_rect()
    test_completion_check_mark_and_opt_out()
    test_post_install_support_appeal()
    test_settings_switches_are_on_the_right_and_install_modes_exclude_each_other()
    test_settings_donations_dismiss_link()
    test_caption_icon_follows_the_macos_icon_style()
    test_tooltips_stay_native_and_readable()
    test_type_help_tip_is_plain_text()
    test_progress_and_status_follow_the_live_theme()

    print("All native UX enhancement tests passed!")


def _ink_box(pixmap):
    """Bounding box of the drawn pixels of ``pixmap``, in physical pixels."""
    image = pixmap.toImage()
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 20:
                xs.append(x)
                ys.append(y)
    assert xs, "the glyph drew nothing"
    return min(xs), min(ys), max(xs), max(ys)


def test_vector_fallback_glyphs_fill_their_pixmap_box():
    """A bundled SVG glyph is drawn once, at scale, inside its own pixmap.

    ``QSvgRenderer.render()`` without bounds fills the paint device, and a
    device marked 2x has half the painter coordinates, so the fallback glyphs
    were drawn doubled and clipped into one quadrant: a magnified fragment in
    some sidebar rows and a full glyph in others. Every glyph must cover its
    own box, which is what makes the icons read as one set.
    """
    from src.ui.icons import get_symbol_pixmap

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    for sym in ("install", "cancel", "next", "back", "settings", "diagnostics", "support", "tools"):
        pix = get_symbol_pixmap(sym, 16)
        width, height = pix.width(), pix.height()
        left, top, right, bottom = _ink_box(pix)
        assert (right - left + 1) >= width * 0.6, (sym, (left, right), width)
        assert (bottom - top + 1) >= height * 0.6, (sym, (top, bottom), height)
        # Centred in the canvas, not pushed into a corner.
        assert abs((left + right) - (width - 1)) <= width * 0.25, (sym, (left, right), width)
        assert abs((top + bottom) - (height - 1)) <= height * 0.25, (sym, (top, bottom), height)


def test_a_theme_icon_that_ships_one_small_raster_keeps_the_row_size(monkeypatch):
    """Theme icons come back at the logical size the row asked for.

    Icon themes without a scalable or 32px version return their best raster
    (often 16px) even when 32 is requested. Marking that as 2x made it a
    logical 8px glyph, so one sidebar row's icon was half the size — and half
    the resolution — of its neighbours.
    """
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    from src.ui import icons

    class _SmallRasterIcon:
        def __init__(self):
            self._source = QPixmap(16, 16)
            self._source.fill(Qt.transparent)
            painter = QPainter(self._source)
            painter.fillRect(2, 2, 12, 12, QColor("#ffffff"))
            painter.end()

        def isNull(self):
            return False

        def pixmap(self, _size):
            # What Qt's icon loader engine does: never upscales a raster it
            # only has at a smaller size.
            return QPixmap(self._source)

    class _FakeIcon:
        @staticmethod
        def fromTheme(_name):
            return _SmallRasterIcon()

    app = QApplication.instance() or QApplication(sys.argv)
    assert app is not None
    monkeypatch.setattr(icons, "QIcon", _FakeIcon)

    pix = icons._get_freedesktop_pixmap("install", 16)
    assert pix is not None and not pix.isNull()
    assert pix.devicePixelRatio() > 1.0
    assert pix.deviceIndependentSize().width() == 16
    assert pix.deviceIndependentSize().height() == 16
    row_pixmap = QIcon(icons.get_symbol_pixmap("install", 16)).pixmap(16, 16)
    assert row_pixmap.deviceIndependentSize().width() == 16


def test_browse_and_clear_glyphs_follow_the_interface_text_colour():
    """Small button glyphs are the text colour, not the provider's white default.

    ``get_symbol_icon`` defaults to plain white in dark mode, which left the
    Browse Files / Browse Folder / Clear buttons pale against the theme's own
    foreground on every label around them.
    """
    from PySide6.QtGui import QColor

    from src.ui.dark import T, apply_theme
    from src.ui.select_page import SelectPackagePage
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    foreground = QColor(T().fg)

    def ink(button):
        pixmap = button.icon().pixmap(button.iconSize())
        image = pixmap.toImage()
        return [
            image.pixelColor(x, y)
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 200
        ]

    page = SelectPackagePage()
    try:
        for name in (
            "_browse_btn",
            "_browse_folder_btn",
            "_browse_da_btn",
            "_clear_da_btn",
            "_browse_scatter_btn",
            "_browse_auth_btn",
            "_clear_auth_btn",
        ):
            button = getattr(page, name)
            pixels = ink(button)
            assert pixels, name
            for colour in pixels:
                assert abs(colour.red() - foreground.red()) <= 12, (name, colour.name())
                assert abs(colour.green() - foreground.green()) <= 12, (name, colour.name())
                assert abs(colour.blue() - foreground.blue()) <= 12, (name, colour.name())
    finally:
        page.deleteLater()
        app.processEvents()

    settings = SettingsPage()
    try:
        pixels = ink(settings._clear_cache_btn)
        assert pixels
        assert all(
            abs(colour.red() - foreground.red()) <= 12
            and abs(colour.green() - foreground.green()) <= 12
            and abs(colour.blue() - foreground.blue()) <= 12
            for colour in pixels
        )
    finally:
        settings.deleteLater()
        app.processEvents()
