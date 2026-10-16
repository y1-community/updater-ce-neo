import os

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from src import paths

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

        # Send R while the unlocked Diagnostics page is open.
        assert w._stack.currentIndex() == 5
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
        assert s.value("flash_method") is None
    finally:
        if w is not None:
            w.close()
        for settings, saved in zip(settings_groups, saved_settings):
            settings.clear()
            for key, value in saved.items():
                settings.setValue(key, value)
