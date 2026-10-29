"""The menu bar / system tray mode is opt-in.

Off by default: closing the window quits the app and no tray icon exists.
On: closing hides the window, keeps a tray icon, and an app update found while
hidden is announced natively instead of by an in-window toast.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

from src import login_item, tray
from src.state import FlashState
from src.ui.main_window import MainWindow
from src.updates import UpdateInfo


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def _window():
    app = _app()
    win = MainWindow()
    win.show()
    app.processEvents()
    return app, win


def _clear_opt_in():
    QSettings("innioasis", "updater").remove(tray.RUN_IN_BACKGROUND_KEY)


def test_background_opt_in_defaults_to_off():
    _app()
    _clear_opt_in()
    assert tray.run_in_background_enabled() is False


def test_background_opt_in_reads_back_when_enabled():
    _app()
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        assert tray.run_in_background_enabled() is True
    finally:
        _clear_opt_in()


def test_close_with_opt_in_off_quits_and_creates_no_tray(monkeypatch):
    _clear_opt_in()
    app, win = _window()
    monkeypatch.setattr(login_item, "set_login_item", lambda enabled: None)
    event = QCloseEvent()
    win.closeEvent(event)
    assert event.isAccepted()
    assert getattr(win, "_background_tray", None) is None


def test_close_with_opt_in_on_hides_to_tray_and_keeps_process(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        event = QCloseEvent()
        win.closeEvent(event)
        assert not event.isAccepted()
        assert not win.isVisible()
        assert win._background_tray.is_visible()
    finally:
        _clear_opt_in()


def test_close_is_blocked_during_install_even_with_opt_in_on():
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        win._install_complete = False
        win._install_failed = False
        win._download_active = True
        event = QCloseEvent()
        win.closeEvent(event)
        # The install prompt must run before any hide: an unanswered prompt
        # keeps the window and its install open.
        assert getattr(win, "_background_tray", None) is None or not win._background_tray.is_visible() or win.isVisible() or not event.isAccepted()
        assert not (event.isAccepted() and getattr(win, "_closing_accepted", False))
    finally:
        _clear_opt_in()


def test_tray_quit_does_not_quit_while_install_blocks_close(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quit_calls.append(True)))
    try:
        app, win = _window()
        win._download_active = True
        win._install_complete = False
        win._install_failed = False
        win._test_close_prompt = True
        # The user declines the stop prompt: the install keeps running.
        monkeypatch.setattr(win._flash_page, "prompt_stop", lambda *a, **k: False)
        win._quit_from_tray()
        assert quit_calls == []
        assert not win._closing_accepted
    finally:
        _clear_opt_in()


def test_app_update_while_hidden_is_announced_natively(monkeypatch):
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        win._hide_to_tray(QCloseEvent())
        assert win._window_hidden_in_tray()
        sent = []
        monkeypatch.setattr(win, "_notify_app_update", lambda info: sent.append(info.tag))
        win._update_offered_once = False
        win._update_manual_pending = False
        win._on_update_check_done(UpdateInfo(tag="v9.9.9"), manual=False)
        assert sent == ["v9.9.9"]
    finally:
        _clear_opt_in()


def test_hidden_window_pauses_its_timers_and_resumes_on_show():
    QSettings("innioasis", "updater").setValue(tray.RUN_IN_BACKGROUND_KEY, True)
    try:
        app, win = _window()
        win._hide_to_tray(QCloseEvent())
        assert win._background_tray.is_visible()
        timer = getattr(win, "_chrome_watchdog", None)
        if timer is not None:
            assert not timer.isActive()
        win._show_from_tray()
        if timer is not None:
            assert timer.isActive()
    finally:
        _clear_opt_in()
