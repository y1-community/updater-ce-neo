"""Test that BatteryToast dismiss button updates accent colouring on system theme changes."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication
from src.ui.widgets import BatteryToast
from src.ui.dark import apply_theme, T


def _get_app():
    return QApplication.instance() or QApplication(sys.argv)


def test_battery_toast_dismiss_button_updates_accent_on_theme_refresh():
    app = _get_app()
    toast = BatteryToast()
    try:
        btn = toast._dismiss_btn
        assert btn._accent is True
        assert btn._symbol == "complete"

        # Apply dark theme
        apply_theme(app, force_dark=True)
        toast.refresh_theme()
        disc_dark, _ = btn._colors()
        expected_dark = QColor(getattr(T(), "accent", "") or "#52b036")
        assert disc_dark.red() == expected_dark.red()
        assert disc_dark.green() == expected_dark.green()
        assert disc_dark.blue() == expected_dark.blue()

        # Apply light theme
        apply_theme(app, force_dark=False)
        toast.refresh_theme()
        disc_light, _ = btn._colors()
        expected_light = QColor(getattr(T(), "accent", "") or "#52b036")
        assert disc_light.red() == expected_light.red()
        assert disc_light.green() == expected_light.green()
        assert disc_light.blue() == expected_light.blue()
    finally:
        toast.close()


def test_round_next_button_handles_palette_change_event():
    _get_app()
    from src.ui.flash_page import _RoundNextButton

    btn = _RoundNextButton(symbol="complete", accent=True)
    try:
        # Trigger changeEvent with PaletteChange
        event = QEvent(QEvent.Type.PaletteChange)
        btn.changeEvent(event)
        # Should not raise, arrow should be regenerated
        assert not btn._arrow.isNull()
    finally:
        btn.close()
