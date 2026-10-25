"""Disconnect detection belongs to a run in progress.

Once the app has declared a flash successful the player is *expected* to
vanish — it reboots into what was just written, and it comes and goes again
while the guided themes/fonts install talks to it over ADB. A device-lost
event after that point must not reach the disconnect/failure path, or it
interrupts the completion screen, the donation prompt and the guided themes
install. Detection must still work while a run is flashing.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from src.state import FlashState
from src.ui.main_window import MainWindow


def _window():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.start_device_monitor = lambda: None
    win.service.start_device_monitor = lambda: None
    win.service.cancel_flash = lambda: None
    win.service.stop_device_monitor = lambda: None
    return app, win


def test_device_lost_after_success_is_ignored():
    app, win = _window()
    try:
        called = []
        win._on_flash_disconnected = lambda *a, **k: called.append(True)

        # A finished install, with the USB-search flag left over from the run
        # that just succeeded (the state the app is really in at that point).
        win.sm.force_state(FlashState.S5_COMPLETE)
        win._install_complete = True
        win._past_usb_search = True

        win._on_device_lost()

        assert called == [], "a post-success disconnect must not interrupt the completion flow"
    finally:
        win.close()
        app.processEvents()


def test_device_lost_still_lands_while_flashing():
    app, win = _window()
    try:
        called = []
        win._on_flash_disconnected = lambda *a, **k: called.append(True)

        win.sm.force_state(FlashState.S4_FLASHING)
        win._install_complete = False

        win._on_device_lost()

        assert called == [True], "an in-run disconnect must still be reported"
    finally:
        win.close()
        app.processEvents()
