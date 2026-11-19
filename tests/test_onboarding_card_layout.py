"""The welcome card keeps its width, and Skip is a quiet word beside Save.

Fitting the window to the focused card measured its height by calling
``adjustSize()``, which also snaps the card's width to its own size hint — on a
wide window that is about half the column, so the journey card shrank after it
had opened. Skip sat under the form in a second button row instead of being the
subtle way out beside Save.
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

from src.ui.main_window import MainWindow, _PAGE_SETTINGS


# Keep the application alive between tests: closing this file's window is the
# last window in the process, and without a live app the next test module's
# widgets cannot be constructed.
_APP = None


def _window():
    global _APP
    app = QApplication.instance() or QApplication(sys.argv)
    _APP = app
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    for _ in range(8):
        app.processEvents()
    win._nav_to_page(_PAGE_SETTINGS)
    for _ in range(8):
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


def test_measuring_the_focused_card_does_not_shrink_its_width():
    app, win = _window()
    page = win._settings_page
    try:
        assert page.begin_onboarding()
        _app_events(app)
        card = page._reminders_card
        width = card.width()
        assert width >= 640, "the journey card starts at its column width"

        # Every window fit asks the page for its content height. That
        # measurement must not resize the card.
        for _ in range(3):
            page.content_height_hint()
            _app_events(app)

        assert card.width() == width, (width, card.width())
    finally:
        page.end_onboarding()
        _close(win)


def _app_events(app):
    for _ in range(6):
        app.processEvents()


def test_skip_is_a_flat_text_button_beside_save():
    app, win = _window()
    page = win._settings_page
    try:
        assert page.begin_onboarding()
        _app_events(app)

        skip = page._onboarding_skip
        assert skip.isFlat(), "Skip must read as text, not a second button"
        assert skip.isVisible()
        # Skip belongs to the form's own button row, beside Save and Cancel —
        # not to the Done / Add another row under the form.
        assert skip.parentWidget() is page._add_device_panel
        assert page._add_device_save.parentWidget() is page._add_device_panel
        assert page._onboarding_actions.isAncestorOf(skip) is False

        skip.click()
        _app_events(app)
        assert not page._onboarding
        assert not skip.isVisible()
    finally:
        _close(win)
