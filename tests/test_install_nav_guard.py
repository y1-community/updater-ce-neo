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
    MAXIMUM_AUTO_FIT_HEIGHT,
    MAXIMUM_WINDOW_WIDTH,
    MainWindow,
    _PAGE_DIAGNOSTICS,
    _PAGE_FLASH,
    _PAGE_NOTICE,
    _PAGE_NOTICES,
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


def _settle(app, win, rounds: int = 8) -> None:
    """Let the deferred fits and the short size animations they start finish."""
    for _ in range(rounds):
        app.processEvents()
        anim = getattr(win, "_size_anim", None)
        if anim is not None:
            try:
                anim.setCurrentTime(10_000)
            except RuntimeError:
                pass
        app.processEvents()


def _start_flash(win):
    win._set_state(FlashState.S4_FLASHING)
    win._flash_page.show_flashing()
    win._sync_install_nav_entry()
    win._nav_to_page(_PAGE_FLASH)
    assert win._install_run_active()


def test_the_notices_row_keeps_the_page_that_opened_it():
    """Clicking a notice must light the Notices row, not the previous tab."""
    app, win = _window()
    try:
        win._notices_available = True
        # Opening the page refreshes from cache; the fetch is not this test's.
        win._notices_page.refresh = lambda: None

        win._nav_to_page(_PAGE_SELECT)
        select = win._nav_buttons["nav_select_package"][0]
        assert select.isChecked()

        win._nav_to_page(_PAGE_NOTICES)
        app.processEvents()
        assert win._notices_btn.isCheckable()
        assert win._notices_btn.isChecked()
        assert not select.isChecked()

        # One notice's detail is a child view of the same page.
        win._highlight_nav(_PAGE_NOTICE, False)
        assert win._notices_btn.isChecked()
        assert not select.isChecked()
    finally:
        _close(win)


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
        win.themes.begin("Y1")
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


def _frame_and_expectation(win):
    """A frame inside the test display, and the height the restore clamps to."""
    avail = win.screen().availableGeometry()
    max_w = min(MAXIMUM_WINDOW_WIDTH, avail.width())
    max_h = min(MAXIMUM_AUTO_FIT_HEIGHT, avail.height())
    frame = (max_w - 20, max_h - 20)
    expected_h = min(max(frame[1], win._content_floor_height()), max_h)
    return frame, expected_h


def test_cancelling_returns_the_catalogue_at_its_own_height():
    """A cancelled run gives back the catalogue, not the card or a tall frame.

    Measuring the catalogue after the install card has hugged the window
    describes the squeezed page, so the window came back short with the release
    rows cut off. Restoring the pre-run frame fixed that but also brought back
    a frame taller than the page needs, leaving an empty band under the cards.
    The page's own measurement is the height, whatever frame the run saved.
    """
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()
        assert catalogue_h >= win._content_floor_height()

        win._on_download_started("Rockbox 0.5 for Y1", "Y1")
        _settle(app, win)
        assert win.height() < catalogue_h, "the card should hug a compact frame"

        frame, _expected_h = _frame_and_expectation(win)
        assert frame[1] > catalogue_h, "the saved frame must be the taller one"
        win._pre_install_frame = frame
        win._return_to_select_software()
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win.height() == catalogue_h, (catalogue_h, win.height())
    finally:
        _close(win)


def test_cancelling_while_a_keep_data_question_is_pending_restores_the_catalogue():
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()

        win._on_download_started("Original Software 3.1.2 for Y1", "Y1")
        _settle(app, win)
        win._keep_data_confirm_pending = True
        win._flash_page.show_keep_data_confirmation("Keep Data?")
        _settle(app, win)

        frame, _expected_h = _frame_and_expectation(win)
        assert frame[1] > catalogue_h
        win._pre_install_frame = frame
        win._return_to_select_software()
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert not win._keep_data_confirm_pending
        assert win.height() == catalogue_h, (catalogue_h, win.height())
    finally:
        _close(win)


def test_a_fit_after_the_restore_keeps_the_catalogue_hug():
    """A queued measurement after the restore must inherit the card's frame."""
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()

        win.themes.begin("Y1")
        win._install_complete = True
        win._sync_install_nav_entry()
        win._nav_to_page(_PAGE_FLASH)
        _settle(app, win)

        frame, _expected_h = _frame_and_expectation(win)
        win._pre_install_frame = frame
        win._return_to_select_software()
        _settle(app, win)
        assert win.height() == catalogue_h, (catalogue_h, win.height())

        # The guard stands for the fit that lands before the page has laid out
        # at the restored size: it re-measures the catalogue like any other fit
        # instead of holding on to whatever frame the install card left.
        win._select_frame_guard = True
        win._fit_window_to_page()
        _settle(app, win)

        assert win.height() == catalogue_h, (catalogue_h, win.height())
        assert win.height() >= win._content_floor_height()
    finally:
        _close(win)


def test_cancelling_before_the_flash_returns_the_catalogue_at_full_height():
    """Cancel on the wait card: Select Software comes back at its own height.

    The install card hugs a compact frame. Returning from a cancelled run used
    to leave the catalogue inside that frame, so the release rows and the
    Install button were cut off.
    """
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()
        floor_h = win._content_floor_height()
        assert catalogue_h >= floor_h

        win._on_download_started("Rockbox 0.5 for Y1", "Y1")
        _settle(app, win)
        assert win._install_run_active()
        assert win.height() < catalogue_h, "the card should hug a compact frame"

        win._return_to_select_software()
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win.height() >= floor_h, "the catalogue came back as a strip"
        assert win.height() >= catalogue_h
    finally:
        _close(win)


def test_cancelling_the_themes_stage_returns_the_catalogue_at_full_height():
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()
        floor_h = win._content_floor_height()

        # The Rockbox themes/fonts stage runs after the flash reported success.
        win.themes.begin("Y1")
        win._install_complete = True
        win._sync_install_nav_entry()
        win._nav_to_page(_PAGE_FLASH)
        _settle(app, win)
        assert win._install_run_active()
        assert win.height() < catalogue_h

        win._return_to_select_software()
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert not win.themes.active
        assert win.height() >= floor_h, "the catalogue came back as a strip"
        assert win.height() >= catalogue_h
    finally:
        _close(win)


def test_a_late_compact_install_frame_cannot_freeze_the_catalogue():
    """The card's animation may finish after the cancel; it must not pin short.

    A size animation was started for the progress card, and the user cancels
    while it is still running. Its end value is the card's compact height, so
    the catalogue must re-measure instead of adopting (and pinning) it.
    """
    app, win = _window()
    try:
        _settle(app, win)
        floor_h = win._content_floor_height()
        assert win.height() >= floor_h
        compact_h = floor_h - 60

        # Exactly what the card's animation would apply when it lands late: the
        # floor is lowered to the compact height, the window is resized to it,
        # and the animation finishes.
        win._release_select_window_size()
        win.setMinimumSize(win.width(), compact_h)
        win._size_anim_minimum = (win.width(), compact_h)
        win.resize(win.width(), compact_h)
        win._finish_size_anim()
        _settle(app, win)

        assert win.height() >= floor_h, "the catalogue was frozen at the card's height"
        assert win.minimumHeight() >= floor_h
        assert win._select_size_hold is None or win.height() >= floor_h
    finally:
        _close(win)


def test_coming_back_from_settings_gives_the_catalogue_its_own_height():
    """Settings is taller than the catalogue; leaving it must not keep the band."""
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()

        win._nav_to_page(_PAGE_SETTINGS)
        _settle(app, win)
        assert win.height() >= catalogue_h

        win._nav_to_page(_PAGE_SELECT)
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win.height() == catalogue_h, (catalogue_h, win.height())
        assert win.height() >= win._content_floor_height()
    finally:
        _close(win)
