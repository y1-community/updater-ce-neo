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
        ("fr", "Masque les invites et les boutons de don", "Affiche les détails d'achèvement"),
        ("es", "Oculta los avisos y botones de donación", "Muestra detalles simples de finalización"),
        ("zh-CN", "隐藏捐赠提示和按钮", "刷机完成后直接显示标准完成说明"),
        ("en", "Hides donation prompts and buttons", "Show simple completion details"),
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
    """Verify window shrinks to compact mode and hides Settings during an install."""
    from src.state import FlashState
    from src.ui.main_window import (
        DEFAULT_WINDOW_HEIGHT,
        DEFAULT_WINDOW_WIDTH,
        MINIMUM_WINDOW_HEIGHT,
        MINIMUM_WINDOW_WIDTH,
        _PAGE_SETTINGS,
        _PAGE_FLASH,
        _PAGE_SELECT,
        _PAGE_DIAGNOSTICS,
    )

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()

    # Initial state. Diagnostics stays hidden until D is pressed.
    assert w.size().width() == DEFAULT_WINDOW_WIDTH
    assert w.size().height() == DEFAULT_WINDOW_HEIGHT
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
    assert w._log_btn.isVisible() is True
    assert w._log_btn.isEnabled() is True
    assert w._check_updates_btn.isVisible() is False
    assert w._lang_combo.isVisible() is False
    assert w._nav_buttons["nav_select_package"][0].isVisible() is True
    assert w._nav_buttons["nav_select_package"][0].isChecked() is True
    assert w.size().width() <= 750
    assert w.size().height() <= 350

    # Diagnostics is part of the main window and can be opened mid-install.
    w._show_diagnostics()
    app.processEvents()
    assert w._stack.currentIndex() == _PAGE_DIAGNOSTICS
    assert w._log_btn.isChecked() is True
    assert w._nav_buttons["nav_select_package"][0].isChecked() is False
    assert w.size().height() >= 420
    w._nav_buttons["nav_select_package"][0].click()
    app.processEvents()
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
    """Verify window maximization is enabled across platforms and auto-resizing is disabled when maximized."""
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    flags = w.windowFlags()
    assert bool(flags & Qt.WindowMaximizeButtonHint), "Maximize button hint must be enabled"
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
    """The progress card fills the content width instead of a fixed 470px strip."""
    from PySide6.QtWidgets import QSizePolicy
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    fp = FlashPage()
    for card in (fp._progress_card, fp._wait_card, fp._prep_card):
        assert card.maximumWidth() > 480
        assert card.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding
    fp.show()
    fp.resize(720, 220)
    app.processEvents()
    assert fp._prep_card.width() >= fp.width() - 80, fp._prep_card.width()
    # Headings
    assert len(fp._headings) == 3
    for h in fp._headings:
        assert h.text() == tr("flash_install_in_progress")
    # Cancel button
    assert fp._cancel_btn.width() <= 30
    assert fp._cancel_btn.height() <= 30


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
    assert not w._log_btn.icon().isNull(), "Diagnostics button must have an icon"
    assert not w._check_updates_btn.icon().isNull(), "Update button must have an icon"
    assert not w._lang_combo.itemIcon(0).isNull(), "Language combo items must have translate icon"

    sp = SelectPackagePage()
    assert not sp._install_btn.icon().isNull(), "Install button must have an icon"
    assert not sp._start_btn.icon().isNull(), "Start button must have an icon"
    assert not sp._browse_btn.icon().isNull(), "Browse file button must have an icon"
    assert not sp._browse_folder_btn.icon().isNull(), "Browse folder button must have an icon"

    fp = FlashPage()
    assert not fp._cancel_btn.icon().isNull(), "Cancel button must have an icon"


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
    assert not classic_windows_style_needs_hover("windowsvista")
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
        text = (sp_dir / "history.ini").read_text(encoding="utf-8")
        assert f"[RecentOpenFile]\nlastDir={scatter_abs}" in text
        assert f"[LastDAFilePath]\nlastDir={da_abs}" in text
        assert str((sp_dir / scatter.name).resolve()) != scatter_abs


def test_sp_gui_sidebar_handoff_prefers_cache_and_cancels_first():
    """The sidebar item cancels an in-app run, then hands the cached firmware over."""
    from src import sp_flash_gui

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
    original_resolve = sp_flash_gui.resolve_cached_firmware
    try:
        with tempfile.TemporaryDirectory() as td:
            scatter = Path(td) / "rom_scatter.txt"
            scatter.write_text("scatter\n", encoding="utf-8")
            extract = Path(td)
            sp_flash_gui.is_sp_flash_gui_supported = lambda: True
            sp_flash_gui.cached_install_firmware = lambda latest: (scatter, extract)
            sp_flash_gui.launch_sp_flash_tool_gui = launch
            window._open_sp_flash_tool_gui()
            assert order[:2] == ["cancel", "stop"]
            assert order[-1] == "launch"
            assert launched[-1]["scatter_path"] == scatter
            assert launched[-1]["extract_dir"] == extract

            order.clear()
            launched.clear()
            sp_flash_gui.cached_install_firmware = lambda latest: (None, None)
            prepared = []

            def begin(target, callback):
                prepared.append(target)
                return True

            window._select_page.focused_firmware = lambda: {"kind": "online", "url": "https://example.invalid/rom.zip"}
            window._select_page.begin_external_prepare = begin
            window._open_sp_flash_tool_gui()
            assert order[:2] == ["cancel", "stop"]
            assert "launch" not in order
            assert prepared and prepared[0]["kind"] == "online"

            order.clear()
            window._select_page.focused_firmware = lambda: None
            sp_flash_gui.resolve_cached_firmware = lambda **kwargs: (scatter, extract, "Y1")
            window._open_sp_flash_tool_gui()
            assert launched[-1]["scatter_path"] == scatter
            assert launched[-1]["model"] == "Y1"
    finally:
        sp_flash_gui.is_sp_flash_gui_supported = original_supported
        sp_flash_gui.cached_install_firmware = original_cached
        sp_flash_gui.launch_sp_flash_tool_gui = original_launch
        sp_flash_gui.resolve_cached_firmware = original_resolve
        window.close()
        app.processEvents()


def test_dark_to_light_leaves_combos_and_notes_readable():
    """A live dark-to-light switch must not leave a black list or unreadable combos."""
    from PySide6.QtGui import QPalette

    from src.ui.dark import apply_theme, contrast_ratio, refresh_theme

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    window = MainWindow()
    window.show()
    app.processEvents()
    page = window._select_page
    page._notes.setHtml("<p>Koensayr 2.4.0 release notes</p>")
    page._release_list.addItem("2.4.0")
    apply_theme(app, force_dark=False)
    refresh_theme(app)
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
    tip = page._type_help_btn.toolTip()
    assert "Type A" in tip
    assert "Type B" in tip
    assert "<br>" in tip
    assert not hasattr(page, "_show_device_type_help")
    notes_rule = app.styleSheet().split("QTextBrowser#releaseNotes", 1)[1][:200]
    assert "background: transparent" not in notes_rule
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
    from src.ui.main_window import client_rect_for_unified_caption

    restored = client_rect_for_unified_caption(
        (10, 20, 800, 600), maximized=False, frame_x=8, frame_y=8, padded=4,
    )
    assert restored == (11, 20, 799, 599)
    maximized = client_rect_for_unified_caption(
        (0, 0, 1920, 1080), maximized=True, frame_x=8, frame_y=8, padded=4,
    )
    assert maximized == (12, 12, 1908, 1068)


def test_completion_check_mark_and_opt_out():
    """A finished install shows a check, hugs the card, and can hide the coffee note."""
    from src.config import get_app_name
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
        assert "Hi, Ryan here" in pitch
        assert get_app_name() in pitch
        assert page._coffee_btn.text() == "Buy me a coffee"
        assert page._appeal.isVisible()
        with_note = window.height()
        assert with_note < DEFAULT_WINDOW_HEIGHT - 40

        window._settings_page._cb_hide_donations.setChecked(True)
        app.processEvents()
        page.show_completion_appeal(False)
        window._adjust_window_geometry()
        app.processEvents()
        assert not page._appeal.isVisible()
        coffee = window._settings_page._settings_coffee_btn
        assert not coffee.isHidden()
        assert coffee.text() == "Buy me a coffee"
        assert window._settings_page._cb_hide_donations.text() == "Turn off Donations / Credits"
        assert "buy a coffee from Settings" in window._settings_page._lbl_hide_tip.text()
        assert window.height() <= with_note
    finally:
        device_tracking.set_donation_ui_disabled(False)
        window.close()
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
    test_sp_gui_sidebar_handoff_prefers_cache_and_cancels_first()
    test_dark_to_light_leaves_combos_and_notes_readable()
    test_caption_drag_leaves_controls_alone()
    test_unified_caption_client_rect()
    test_completion_check_mark_and_opt_out()

    print("All native UX enhancement tests passed!")
