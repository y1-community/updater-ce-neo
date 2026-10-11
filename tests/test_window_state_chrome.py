"""Full screen owns the window: the chrome repair and the rows come back after."""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


class _FullScreenStub:
    """A window that reports the state macOS full screen sets."""

    def __init__(self):
        from PySide6.QtCore import Qt

        self._state = Qt.WindowState.WindowFullScreen

    def windowState(self):
        return self._state


def test_fullscreen_windows_are_not_repaired_as_windowed_chrome():
    from src.ui.glass import in_fullscreen, native_chrome_intact

    assert in_fullscreen(_FullScreenStub()) is True
    # A full-screen window has no seamless titlebar to verify, and the repair
    # run against a window macOS owns is what leaves it mis-framed on restore.
    assert native_chrome_intact(_FullScreenStub()) is True
    assert in_fullscreen(None) is False


def test_state_change_resync_reseats_the_header_and_the_rows():
    from PySide6.QtCore import Qt

    app = _app()
    from src.ui.main_window import HEADER_CONTENT_HEIGHT, MainWindow
    from src.ui.sidebar import SidebarButton

    w = MainWindow()
    w.show()
    app.processEvents()
    try:
        # The header is a manually placed child: it follows the window size.
        rows = w._nav_panel.findChildren(SidebarButton)
        shown = [btn for btn in rows if btn.isVisible()]
        bar = getattr(w, "_title_bar", None)
        if bar is not None:
            bar.move(0, 40)
        w.resize(w.width() + 60, w.height() + 20)
        w._schedule_window_state_resync()
        assert w._state_resync_pending is True
        w._resync_window_state()
        assert w._state_resync_pending is False
        if bar is not None:
            assert bar.geometry().top() == 0
            assert bar.width() == w.width()
            assert bar.height() == int(HEADER_CONTENT_HEIGHT)
        # Every row that was on the rail is still on the rail.
        assert shown
        for btn in shown:
            assert btn.isVisible() is True
    finally:
        w.close()
        app.processEvents()


def test_change_event_schedules_the_resync_only_for_window_state():
    from PySide6.QtCore import QEvent

    app = _app()
    from src.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()
    try:
        w._state_resync_pending = False
        w.changeEvent(QEvent(QEvent.Type.WindowStateChange))
        assert w._state_resync_pending is True
        w._state_resync_pending = False
        w.changeEvent(QEvent(QEvent.Type.ActivationChange))
        assert w._state_resync_pending is False
    finally:
        w.close()
        app.processEvents()
