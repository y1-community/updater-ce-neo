"""Leaving an install must hand back a frame the catalogue and rail fully fit.

The install card hugs a compact frame. Returning to Select Software from it
used to leave the window at that card height, so the release rows, the
Original Software card and the sidebar's lower rows were cut off.
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
    CONTENT_FIT_SLACK,
    MainWindow,
    _PAGE_FLASH,
    _PAGE_SELECT,
)


def _window():
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    win.show()
    app.processEvents()
    return app, win


def _close(win):
    win._force_quit = True
    win.close()
    loader = getattr(win, "_icon_loader", None)
    try:
        running = loader is not None and loader.isRunning()
    except RuntimeError:
        running = False
    if running:
        loader.wait(2000)


def _settle(app, win, rounds: int = 10) -> None:
    for _ in range(rounds):
        app.processEvents()
        anim = getattr(win, "_size_anim", None)
        if anim is not None:
            try:
                anim.setCurrentTime(10_000)
            except RuntimeError:
                pass
        app.processEvents()


def _catalogue_need(win) -> int:
    return int(win._page_content_height(win._select_page))


def _rail_need(win) -> int:
    nav = getattr(win, "_nav_panel", None)
    if nav is None:
        return 0
    _, chrome_h = win._window_chrome()
    return int(nav.minimumSizeHint().height()) + int(chrome_h)


def test_the_catalogue_never_pins_a_frame_shorter_than_its_content():
    """A short frame is re-measured, not frozen with the lower cards cut off."""
    app, win = _window()
    try:
        _settle(app, win)
        floor_h = win._content_floor_height()
        _, chrome_h = win._window_chrome()
        need_h = min(
            win._auto_fit_height_ceiling(),
            int(win._page_content_height(win._select_page))
            + int(chrome_h)
            + CONTENT_FIT_SLACK,
        )

        # A frame handed back by a card: at the floor, with the catalogue still
        # needing more than that.
        win._release_select_window_size()
        win.resize(win.width(), max(floor_h, min(need_h - 40, need_h)))
        win._hold_select_window_size()
        _settle(app, win)

        assert win.height() >= need_h, "the catalogue pinned a clipped frame"
        assert win.height() >= floor_h
    finally:
        _close(win)


def test_the_sidebar_rail_contributes_to_the_window_floor():
    """The rail's own rows must fit, not just the page column."""
    app, win = _window()
    try:
        _settle(app, win)
        nav = win._nav_panel
        _, chrome_h = win._window_chrome()
        assert win._content_floor_height() >= nav.minimumSizeHint().height() + chrome_h
        assert win.height() >= nav.minimumSizeHint().height() + chrome_h
    finally:
        _close(win)


def test_leaving_a_cancelled_run_restores_a_frame_that_fits_the_catalogue():
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()
        floor_h = win._content_floor_height()
        assert catalogue_h >= floor_h

        win._on_download_started("Rockbox 0.5 for Y1", "Y1")
        _settle(app, win)
        assert win.height() < catalogue_h, "the card should hug a compact frame"

        win._return_to_select_software()
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win.height() >= floor_h, "the catalogue came back as a strip"
        assert win.height() >= _catalogue_need(win), "catalogue content is clipped"
        assert win.height() >= _rail_need(win), "the sidebar rail is clipped"
    finally:
        _close(win)


def test_leaving_a_completed_install_restores_a_frame_that_fits_the_catalogue():
    app, win = _window()
    try:
        _settle(app, win)
        catalogue_h = win.height()
        floor_h = win._content_floor_height()

        win._set_state(FlashState.S4_FLASHING)
        win._flash_page.show_flashing()
        win._sync_install_nav_entry()
        win._nav_to_page(_PAGE_FLASH)
        _settle(app, win)
        assert win.height() < catalogue_h, "the card should hug a compact frame"
        win._install_complete = True
        win._sync_install_nav_entry()

        win._on_install_done()
        _settle(app, win)

        assert win._stack.currentIndex() == _PAGE_SELECT
        assert win.height() >= floor_h, "the catalogue came back as a strip"
        assert win.height() >= _catalogue_need(win), "catalogue content is clipped"
        assert win.height() >= _rail_need(win), "the sidebar rail is clipped"
    finally:
        _close(win)
