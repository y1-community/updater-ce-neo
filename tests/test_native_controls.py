"""The AppKit round-chip and combo bridge, exercised offscreen.

The AppKit path itself is macOS-only and needs a cocoa session, so what is
checked here is that every headless run falls back to what the app painted
before, and that the bridge's surface is safe to call when it is not live.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QWidget

from src.ui import native_controls as nc
from src.ui.flash_page import _RoundNextButton


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_headless_runs_keep_the_painted_chip(monkeypatch):
    _app()
    assert nc.native_round_buttons_available() is False
    assert nc.native_combo_menus_available() is False
    assert nc.install_native_combo_menus(QApplication.instance()) is False


def test_the_env_switch_turns_the_bridge_off_even_on_a_cocoa_session(monkeypatch):
    monkeypatch.setenv(nc.DISABLE_ENV, "0")
    assert nc.native_round_buttons_available() is False


def test_the_headless_plugin_is_refused_by_name(monkeypatch):
    """A cocoa app instance created before the plugin name was read must not
    hand a widget to AppKit when the session is offscreen."""
    monkeypatch.delenv(nc.DISABLE_ENV, raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    assert nc.native_round_buttons_available() is False


def test_a_chip_that_cannot_attach_is_a_safe_no_op():
    _app()
    host = QWidget()
    host.resize(nc.CHIP_INSET_PX * 2 + 30, nc.CHIP_INSET_PX * 2 + 30)
    chip = nc.NativeRoundChip(host)
    assert chip.attach() is False
    assert chip.live is False
    # Everything the chip forwards has to survive not being live.
    chip.set_symbol("back")
    chip.set_style(accent=True, disc=None, content=None)
    chip.set_enabled(False)
    chip.set_tooltip("Back")
    chip.sync_frame()
    chip.detach()
    chip._emit_click()


def test_the_qt_chip_stays_painted_when_the_bridge_is_off():
    _app()
    btn = _RoundNextButton(symbol="next")
    btn.show()
    QApplication.instance().processEvents()
    assert btn._native is None
    assert btn._arrow is not None
    btn.close()


def test_the_combo_filter_ignores_events_it_does_not_use():
    _app()
    combo = QComboBox()
    combo.addItems(["A", "B"])
    filt = nc._NativeComboFilter.instance()
    assert filt.eventFilter(combo, QEvent(QEvent.Type.Polish)) is False
    assert filt.eventFilter(combo, QEvent(QEvent.Type.FocusIn)) is False
    assert filt.eventFilter(QObject(), QEvent(QEvent.Type.MouseButtonPress)) is False


def test_the_combo_filter_only_takes_a_plain_left_press():
    """A right-click belongs to the widget, not to the menu."""
    _app()
    combo = QComboBox()
    combo.addItems(["A", "B"])
    filt = nc._NativeComboFilter.instance()
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF

    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(1, 1),
        QPointF(1, 1),
        Qt.MouseButton.RightButton,
        Qt.MouseButton.RightButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert filt.eventFilter(combo, event) is False
