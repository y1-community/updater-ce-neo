import os
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from src import paths
from src.ui.main_window import _PAGE_FLASH, _PAGE_SELECT, _PAGE_SETTINGS, _PAGE_SUPPORT

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_r_key_resets_config_and_clears_caches(monkeypatch, tmp_path):
    app = _app()
    from src import device_tracking
    from src.ui.main_window import MainWindow

    # Keep the reset isolated: preserve the developer's settings and redirect
    # cache cleanup away from the real downloads/cache directories.
    settings_groups = (
        QSettings("innioasis", "updater"),
        QSettings("Innioasis", "UpdaterCE"),
        QSettings("Innioasis", "Innioasis Updater"),
    )
    saved_settings = [
        {key: settings.value(key) for key in settings.allKeys()}
        for settings in settings_groups
    ]
    from src import catalog, downloads, manifest
    monkeypatch.setattr(downloads, "clear_firmware_cache", lambda: None)
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path / "downloads")
    monkeypatch.setattr(catalog, "_app_cache_dir", lambda: tmp_path / "app_cache")
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path / "manifest_cache")

    s = settings_groups[0]
    s.clear()
    s.setValue("advanced_settings", True)
    device_tracking.set_hidden_mtk_options(True, s)
    s.setValue("flash_method", "mtk")
    device_tracking.set_donation_ui_disabled(True, s)
    device_tracking.set_settings_donations_section_dismissed(True, s)

    w = None
    try:
        w = MainWindow()
        assert device_tracking.is_donation_ui_disabled(w.settings) is True

        # Toggle D off and on, then use M while diagnostics are unlocked.
        event = QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.NoModifier)
        w.keyPressEvent(event)
        assert not w._diagnostics_unlocked()
        assert not w._settings_page.advanced_methods_revealed()
        w.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.NoModifier))
        assert w._diagnostics_unlocked()
        w.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_M, Qt.NoModifier))
        assert w._settings_page.advanced_methods_revealed()
        assert device_tracking.hidden_mtk_options_enabled(s)
        w._show_diagnostics()

        # Diagnostics is not one of the surfaces that offers the reset: R
        # there must be inert, leaving the unlocked state alone.
        assert w._stack.currentIndex() == 5
        w.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.NoModifier))
        assert device_tracking.is_donation_ui_disabled(w.settings) is True
        assert w._settings_page._advanced_revealed is True

        # Settings is one of the three surfaces that does offer it.
        w._nav_to_page(_PAGE_SETTINGS)
        assert w._stack.currentIndex() == _PAGE_SETTINGS
        w.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.NoModifier))

        # The reset re-hides the UI, clears the diagnostics preference, and
        # restores the platform's default backend in both controllers.
        assert device_tracking.is_donation_ui_disabled(w.settings) is False
        assert device_tracking.is_donation_install_prompt_disabled(w.settings) is False
        assert device_tracking.is_settings_donations_section_dismissed(w.settings) is False
        assert w._settings_page._advanced_revealed is False
        assert not hasattr(w._settings_page, "_dev_mac_revealed")
        assert not w._diagnostics_unlocked()
        assert not device_tracking.hidden_mtk_options_enabled(s)
        assert w._flash_method == ("mtk" if paths.IS_MAC else "sp")
        assert w._flash_page.current_method() == w._flash_method
        assert s.value("advanced_settings", False, type=bool) is False
        # Not absent: the reset puts the platform's own method back, and the
        # settings page persists it on purpose so the default sticks.
        from src.flash_service import default_flash_method

        assert s.value("flash_method") == default_flash_method()
    finally:
        if w is not None:
            w.close()
        for settings, saved in zip(settings_groups, saved_settings):
            settings.clear()
            for key, value in saved.items():
                settings.setValue(key, value)


def test_r_key_is_scoped_to_support_settings_and_select(monkeypatch, tmp_path):
    """R clears config only on the surfaces that offer it, and never mid-task."""
    _app()
    from src import catalog, downloads, manifest
    from src.ui.main_window import MainWindow

    settings_groups = (
        QSettings("innioasis", "updater"),
        QSettings("Innioasis", "UpdaterCE"),
        QSettings("Innioasis", "Innioasis Updater"),
    )
    saved_settings = [
        {key: settings.value(key) for key in settings.allKeys()}
        for settings in settings_groups
    ]
    monkeypatch.setattr(downloads, "clear_firmware_cache", lambda: None)
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path / "downloads")
    monkeypatch.setattr(catalog, "_app_cache_dir", lambda: tmp_path / "app_cache")
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path / "manifest_cache")

    s = settings_groups[0]
    s.clear()
    s.setValue("advanced_settings", True)

    def press():
        return QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.NoModifier)

    def still_set():
        return s.value("advanced_settings", False, type=bool) is True

    w = None
    try:
        w = MainWindow()
        # The install card writes to the player: R there is inert.
        w._nav_to_page(_PAGE_FLASH)
        w.keyPressEvent(press())
        assert still_set()

        # A running install or theme stage outranks an allowed page, and the
        # settings page's own handler follows the same rule.
        w._nav_to_page(_PAGE_SUPPORT)
        w._prep_active = True
        w.keyPressEvent(press())
        assert still_set()
        w._settings_page.keyPressEvent(press())
        assert still_set()
        w._prep_active = False

        # A theme pack refresh is preparing themes even when idle on a page
        # that otherwise offers the reset.
        w._theme_update_worker = SimpleNamespace(isRunning=lambda: True)
        w.keyPressEvent(press())
        assert still_set()
        w._theme_update_worker = None

        # Select Software offers it; so does Support us, and Settings through
        # its own key handler.
        w._nav_to_page(_PAGE_SELECT)
        w.keyPressEvent(press())
        assert not still_set()

        s.setValue("advanced_settings", True)
        w._nav_to_page(_PAGE_SETTINGS)
        w._settings_page.keyPressEvent(press())
        assert not still_set()
    finally:
        if w is not None:
            w.close()
        for settings, saved in zip(settings_groups, saved_settings):
            settings.clear()
            for key, value in saved.items():
                settings.setValue(key, value)
