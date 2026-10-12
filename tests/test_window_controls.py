"""The header's own minimize / maximize / close controls (Windows).

The window removes the caption band from its non-client area, and DWM paints no
system buttons over a full-client custom title bar, so Windows used to show a
title bar with nothing on it. These tests cover the controls the header draws
instead: where they sit, what they call, and how an install greys the close one.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _window():
    from src.ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    _app().processEvents()
    return window


def test_controls_are_built_where_the_platform_puts_them():
    """Windows draws them itself; macOS and Linux keep the host's controls."""
    app = _app()
    window = _window()
    try:
        controls = getattr(window, "_window_controls", None)
        if sys.platform == "win32":
            assert controls is not None, "Windows needs its own caption controls"
            assert controls.min_button.role == "minimize"
            assert controls.max_button.role == "maximize"
            assert controls.close_button.role == "close"
        else:
            # macOS traffic lights and Linux decorations are the host's.
            assert controls is None
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="the header draws them on Windows only")
def test_controls_sit_at_the_trailing_edge_of_the_header():
    from src.ui.window_controls import WindowControls

    app = _app()
    window = _window()
    try:
        controls = window._window_controls
        assert isinstance(controls, WindowControls)
        bar = window._title_bar
        assert bar is not None
        layout = bar.layout()
        assert layout.itemAt(layout.count() - 1).widget() is controls
        # Flush with the trailing edge: no reserve margin for ghost buttons.
        assert layout.contentsMargins().right() == 0
        assert abs((controls.x() + controls.width()) - bar.width()) <= 2, (
            controls.geometry().getRect(),
            bar.width(),
        )
        assert controls.y() >= 0 and controls.y() + controls.height() <= bar.height()
        for button in (controls.min_button, controls.max_button, controls.close_button):
            assert button.isVisible()
    finally:
        window.close()
        app.processEvents()


def test_controls_drive_the_window_actions():
    from src.ui.window_controls import WindowControls

    app = _app()
    window = _window()
    calls = []
    patched = {
        "isMaximized": lambda: False,
        "showMinimized": lambda: calls.append("minimize"),
        "showMaximized": lambda: calls.append("maximize"),
        "showNormal": lambda: calls.append("normal"),
        "close": lambda: calls.append("close"),
    }
    originals = {name: getattr(window, name) for name in patched}
    try:
        for name, replacement in patched.items():
            setattr(window, name, replacement)

        controls = WindowControls(window)
        controls.min_button.click()
        controls.max_button.click()
        assert calls == ["minimize", "maximize"], calls
        # A maximized window toggles back down instead of maximizing again.
        window.isMaximized = lambda: True
        controls.max_button.click()
        assert calls == ["minimize", "maximize", "normal"], calls
        controls.close_button.click()
        assert calls[-1] == "close", calls
    finally:
        for name, original in originals.items():
            setattr(window, name, original)
        window.close()
        app.processEvents()


def test_close_can_be_greyed_out_on_its_own():
    from src.ui.window_controls import WindowControls

    app = _app()
    window = _window()
    try:
        controls = WindowControls(window)
        controls.set_close_enabled(False)
        assert not controls.close_button.isEnabled()
        assert controls.min_button.isEnabled()
        assert controls.max_button.isEnabled()
        controls.set_close_enabled(True)
        assert controls.close_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


def test_middle_glyph_switches_between_maximize_and_restore():
    from src.ui.window_controls import WindowControls

    app = _app()
    window = _window()
    try:
        controls = WindowControls(window)
        assert controls.max_button.maximized is False
        controls.set_state(True)
        assert controls.max_button.maximized is True
        controls.set_state(False)
        assert controls.max_button.maximized is False
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform != "win32", reason="no app-drawn controls elsewhere")
def test_install_state_greys_the_close_control():
    app = _app()
    window = _window()
    try:
        controls = window._window_controls
        assert controls is not None
        window._apply_install_ui_state(True)
        assert not controls.close_button.isEnabled()
        window._apply_install_ui_state(False)
        assert controls.close_button.isEnabled()
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    test_controls_are_built_where_the_platform_puts_them()
    test_controls_sit_at_the_trailing_edge_of_the_header()
    test_controls_drive_the_window_actions()
    test_close_can_be_greyed_out_on_its_own()
    test_middle_glyph_switches_between_maximize_and_restore()
    test_install_state_greys_the_close_control()

    print("All window control tests passed!")
