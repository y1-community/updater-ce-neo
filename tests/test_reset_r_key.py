import os
from PySide6.QtCore import Qt, QEvent, QSettings
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_r_key_resets_config_and_clears_caches():
    app = _app()
    from src import device_tracking
    from src.ui.main_window import MainWindow

    # Set some custom settings
    s = QSettings("innioasis", "updater")
    s.setValue("advanced_settings", True)
    device_tracking.set_donation_ui_disabled(True, s)
    device_tracking.set_settings_donations_section_dismissed(True, s)

    w = MainWindow()
    assert device_tracking.is_donation_ui_disabled(w.settings) is True

    # Send R key press
    event = QKeyEvent(QEvent.KeyPress, Qt.Key_R, Qt.NoModifier)
    w.keyPressEvent(event)

    # Verify everything was reset
    assert device_tracking.is_donation_ui_disabled(w.settings) is False
    assert device_tracking.is_donation_install_prompt_disabled(w.settings) is False
    assert device_tracking.is_settings_donations_section_dismissed(w.settings) is False
    assert w._settings_page._advanced_revealed is False
    w.close()
