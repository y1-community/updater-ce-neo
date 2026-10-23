"""The Settings column is never clipped: the window is wide enough for it.

Settings is a column of cards inside a widget-resizable ``QScrollArea``, and
that scroll area answers the layout with its own tiny minimum instead of the
cards' width. The window therefore stayed at its narrow size and the cards ran
off the right edge — the "Clear Firmware Cache" line that appears after a
clear is one state that widens the column at runtime.

``SettingsPage.content_width_hint()`` reports what the cards really need and
the window sizes itself to it, including when the column grows while the page
is already on screen.
"""

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


@pytest.fixture()
def window(monkeypatch):
    import src.catalog as catalog

    # The catalogue and update checks reach the network; this test is about
    # the width the Settings column asks for.
    monkeypatch.setattr(
        catalog.ReleasesClient, "get_all_releases", lambda self, repo, force_refresh=False: []
    )
    monkeypatch.setattr(catalog.ReleasesClient, "get_cached_releases", lambda self, repo: [])
    monkeypatch.setattr(
        catalog.ReleasesClient, "get_all_update_releases", lambda self, *a, **k: []
    )
    monkeypatch.setattr(
        catalog.ReleasesClient, "get_latest_release_info", lambda self, *a, **k: None
    )

    app = _app()
    from src.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    _settle(app)
    try:
        yield w
    finally:
        w._force_quit = True
        w.close()
        app.processEvents()


def _settle(app, turns=40):
    for _ in range(turns):
        app.processEvents()
        time.sleep(0.01)


def _show_settings(window, app):
    from src.ui import main_window as mw

    window._nav_to_page(mw._PAGE_SETTINGS)
    _settle(app)
    assert window._stack.currentIndex() == mw._PAGE_SETTINGS


def _chrome_width(window):
    """Everything outside the page stack: the rail and the page margins."""
    return max(0, window.width() - window._stack.width())


def test_the_width_hint_covers_the_cards_inside_the_scroll_area(window):
    page = window._settings_page
    scroll = page._settings_scroll
    inner = scroll.widget()
    margins = page.layout().contentsMargins()
    card_column = (
        inner.minimumSizeHint().width()
        + 2 * scroll.frameWidth()
        + margins.left()
        + margins.right()
    )

    assert page.content_width_hint() >= card_column
    # The scroll area on its own reports almost nothing, which is why the
    # window had no way to know how wide this page has to be.
    assert scroll.minimumSizeHint().width() < card_column


def test_a_runtime_line_widens_the_window_instead_of_being_clipped(window):
    app = _app()
    _show_settings(window, app)
    page = window._settings_page
    chrome = _chrome_width(window)
    available = window.screen().availableGeometry().width() if window.screen() else 1920

    page._cache_status_lbl.setText(
        "Cleared 12 item(s) (2048.5 MB freed), old packages removed and the space "
        "returned to the disk the app runs from"
    )
    page.content_size_changed.emit()
    _settle(app)

    required = page.content_width_hint() + chrome
    assert window.width() >= min(required, available)
    # And the user cannot drag it back below what the cards need.
    assert window.minimumWidth() >= min(required, available)

    inner = page._settings_scroll.widget()
    viewport = page._settings_scroll.viewport().width()
    if window.width() >= required:
        assert viewport >= inner.minimumSizeHint().width(), "the column must fit, not scroll"
        assert page._settings_scroll.horizontalScrollBar().maximum() == 0
    else:
        # The display is narrower than the column: the overflow is reachable.
        assert page._settings_scroll.horizontalScrollBar().maximum() > 0


def test_switching_to_settings_widens_a_narrow_window(window):
    app = _app()
    page = window._settings_page
    chrome = _chrome_width(window)
    required = page.content_width_hint() + chrome
    available = window.screen().availableGeometry().width() if window.screen() else 1920

    # Narrow the window as far as it will go, then open Settings.
    window.resize(window.minimumWidth(), window.height())
    _settle(app)
    _show_settings(window, app)

    assert window.width() >= min(required, available)
    if window.width() >= required:
        assert page._settings_scroll.horizontalScrollBar().maximum() == 0
