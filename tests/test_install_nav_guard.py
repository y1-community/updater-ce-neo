"""The install card owns the window while a run is in progress.

Clicking the first sidebar entry ("Install Software") during a flash, device
prep or the Rockbox themes stage used to cancel the run and drop the user on
Select Software without the stop confirmation. Settings was reachable through
the same gap. Both screens stay closed until the run finishes, fails, or is
cancelled from the install card's own stop control.
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
from src.ui.main_window import (
    MainWindow,
    _PAGE_DIAGNOSTICS,
    _PAGE_FLASH,
    _PAGE_SELECT,
    _PAGE_SETTINGS,
)


def _window():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    app.processEvents()
    return app, win


def _close(win):
    # Never leave an "install in progress" window asking about closing.
    win._force_quit = True
    win.close()
    loader = getattr(win, "_icon_loader", None)
    try:
        running = loader is not None and loader.isRunning()
    except RuntimeError:
        # The loader finished and Qt deleted its C++ half before teardown.
        running = False
    if running:
        loader.wait(2000)


def _spy_cancels(win):
    calls = []
    win._stop_running_install = lambda: calls.append("stop")
    win._return_to_select_software = lambda: calls.append("select")
    win._on_cancel_flash = lambda: calls.append("cancel-flash")
    win._on_cancel_wait = lambda: calls.append("cancel-wait")
    win._on_cancel_download = lambda: calls.append("cancel-download")
    return calls


def _start_flash(win):
    win._set_state(FlashState.S4_FLASHING)
    win._flash_page.show_flashing()
    win._sync_install_nav_entry()
    win._nav_to_page(_PAGE_FLASH)
    assert win._install_run_active()


def test_the_install_entry_never_cancels_or_opens_select():
    app, win = _window()
    calls = _spy_cancels(win)
    try:
        _start_flash(win)
        btn = win._nav_buttons["nav_select_package"][0]
        assert btn.text() == "Install Software"

        btn.click()
        app.processEvents()

        assert calls == [], calls
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert win._install_run_active(), "the run must still be going"
    finally:
        _close(win)


def test_the_install_entry_returns_from_an_allowed_popout():
    app, win = _window()
    calls = _spy_cancels(win)
    try:
        _start_flash(win)
        win._unlock_diagnostics()
        win._show_diagnostics()
        app.processEvents()
        assert win._stack.currentIndex() == _PAGE_DIAGNOSTICS

        win._nav_buttons["nav_select_package"][0].click()
        app.processEvents()

        assert win._stack.currentIndex() == _PAGE_FLASH
        assert calls == []
        assert win._install_run_active()
    finally:
        _close(win)


def test_settings_cannot_be_opened_during_a_run():
    app, win = _window()
    try:
        _start_flash(win)
        assert not win._settings_btn.isVisible()

        win._show_full_settings()
        app.processEvents()
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert not win._settings_btn.isChecked()

        win._nav_to_page(_PAGE_SETTINGS)
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert win._settings_btn.isVisible() is False
    finally:
        _close(win)


def test_device_prep_keeps_the_install_card():
    app, win = _window()
    try:
        win._package_path = ""
        win._show_device_prep()
        app.processEvents()
        assert win._power_off_prompt is True
        assert win._install_run_active()
        assert win._stack.currentIndex() == _PAGE_FLASH
        calls = _spy_cancels(win)

        win._nav_buttons["nav_select_package"][0].click()
        win._nav_to_page(_PAGE_SELECT)
        app.processEvents()

        assert calls == [], calls
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert win._install_run_active()
    finally:
        _close(win)


def test_the_themes_stage_keeps_settings_and_select_closed():
    app, win = _window()
    try:
        # The themes/fonts stage runs after the flash itself reported success,
        # so a completed flash must not unlock the window underneath it.
        win._rockbox_themes_flow_active = True
        win._install_complete = True
        win._sync_install_nav_entry()
        win._nav_to_page(_PAGE_FLASH)
        assert win._install_run_active()
        calls = _spy_cancels(win)

        win._nav_buttons["nav_select_package"][0].click()
        win._nav_to_page(_PAGE_SELECT)
        win._show_full_settings()
        app.processEvents()

        assert calls == [], calls
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert win._install_run_active()
    finally:
        _close(win)


def test_select_reopens_once_the_run_is_over():
    app, win = _window()
    try:
        _start_flash(win)
        win.sm.force_state(FlashState.S5_COMPLETE)
        win._install_complete = True
        win._compact_install_screen = False
        win._power_off_prompt = False
        win._prep_active = False
        win._download_active = False
        win._sync_install_nav_entry()
        assert not win._install_run_active()

        win._nav_buttons["nav_select_package"][0].click()
        app.processEvents()

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win._nav_buttons["nav_select_package"][0].text() == "Select Software"
    finally:
        _close(win)


def test_a_failed_run_can_leave_the_install_card():
    app, win = _window()
    try:
        _start_flash(win)
        win._show_install_failure("CONNECTION_FAILED")
        app.processEvents()
        assert not win._install_run_active()

        win._nav_buttons["nav_select_package"][0].click()
        app.processEvents()
        assert win._stack.currentIndex() == _PAGE_SELECT
    finally:
        _close(win)
