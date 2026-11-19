"""A notice banner arrives after the catalogue has already been fitted.

The banner is a row in the page's own column, so showing it pushes the release
card — the Install button and the "See more" link — further down. The window
kept the height it measured before the banner existed, so those actions were
clipped below the frame and users had to guess they were there.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.notice_service import Notice  # noqa: E402


def _page():
    from PySide6.QtWidgets import QApplication
    from src.ui.select_page import SelectPackagePage

    QApplication.instance() or QApplication(["--platform", "offscreen"])
    return SelectPackagePage()


def _notice():
    return Notice(
        id="reborn-first-install",
        title="Installing Reborn for the First Time?",
        content=(
            "Install Original Software (Y2) for the best results, it may not "
            "boot if coming from Classy or another custom firmware."
        ),
        severity="warning",
    )


def test_the_banner_asks_the_window_to_measure_the_page_again():
    page = _page()
    measurements = []
    page.content_size_changed.connect(lambda: measurements.append(1))

    page._on_notice_ready(_notice())
    assert page._notice_prompt.isHidden() is False
    assert measurements, "showing the banner must re-measure the page"

    # Closing it gives the height back.
    measurements.clear()
    page._notice_prompt.dismissed.emit()
    assert measurements, "closing the banner must re-measure the page"


def test_the_page_grows_by_the_banner_it_shows():
    from PySide6.QtWidgets import QApplication

    page = _page()
    page.resize(900, 480)
    page.show()
    QApplication.processEvents()

    before = page.content_height_hint()
    page._on_notice_ready(_notice())
    QApplication.processEvents()
    after = page.content_height_hint()

    banner = page._notice_prompt.sizeHint().height()
    assert banner > 0
    # The banner has to be room the window can grow into, not height the page
    # absorbed while staying the same size.
    assert after - before >= banner - 8, (before, after, banner)


def test_the_catalogue_says_it_scrolls_when_its_actions_do_not_fit():
    """A short window must show the page's own scrollbar, not hide the bar.

    Install and "Install from File" sit under the release card, so a window too
    short for both actions leaves them below the edge. The page still scrolls,
    and the platform's bar is the only thing that says so.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    page = _page()
    page.resize(900, 360)
    page.show()
    QApplication.processEvents()

    area = page._scroll
    assert area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    # The page can only be shown whole when the window is tall enough; force
    # the overflow a short display produces and the bar has to appear.
    content = area.widget()
    assert content is not None
    content.setMinimumHeight(area.viewport().height() + 400)
    QApplication.processEvents()

    bar = area.verticalScrollBar()
    assert bar.maximum() > bar.minimum(), "the overflow must be scrollable"
    assert not bar.isHidden(), "the scrollbar must be revealed, not forced off"


def test_a_short_fit_grows_the_window_until_the_bar_is_gone():
    """A visible bar is height the measurement missed: grow, do not show it."""
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from src.ui.main_window import MainWindow

    from PySide6.QtCore import QSettings

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    # The first screen has to be Select Software: a launch with no tracked
    # player opens Welcome instead, and this test measures the catalogue page.
    settings = QSettings("innioasis", "updater")
    saved = settings.value("onboarding_completed")
    settings.setValue("onboarding_completed", True)
    window = MainWindow()
    window.show()
    QTest.qWait(300)
    try:
        page = window._select_page
        area = page._scroll
        before = window.height()

        # What a wrapped hint line or a notice that arrived late does: a few
        # pixels the fitted height did not account for.
        content = area.widget()
        content.setMinimumHeight(area.viewport().height() + 40)
        app.processEvents()
        bar = area.verticalScrollBar()

        # The page may already have been refitted by the size change itself;
        # either way the window must end up taller than it was with the bar
        # gone, rather than leaving the extra height to the scrollbar.
        window._clear_scrollbar_after_fit()
        QTest.qWait(400)
        app.processEvents()

        assert window.height() > before
        assert bar.maximum() == bar.minimum(), (
            "the window should have grown out of the overflow"
        )
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
        if saved is None:
            settings.remove("onboarding_completed")
        else:
            settings.setValue("onboarding_completed", saved)


def test_scroll_areas_keep_the_policy_their_page_asked_for():
    """Windows used to force every bar off, so overflow had no visible signal."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QScrollArea

    from src.ui.scrollbars import configure_scroll_area

    QApplication.instance() or QApplication(["--platform", "offscreen"])
    area = QScrollArea()
    configure_scroll_area(area, vertical=Qt.ScrollBarAsNeeded)
    assert area.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded
    assert area.horizontalScrollBarPolicy() == Qt.ScrollBarAsNeeded

    hidden = QScrollArea()
    configure_scroll_area(hidden, vertical=Qt.ScrollBarAlwaysOff)
    assert hidden.verticalScrollBarPolicy() == Qt.ScrollBarAlwaysOff, (
        "a page that asks for no bar still gets none"
    )


def test_a_tall_page_grows_the_window_instead_of_being_clipped(monkeypatch):
    """The fit ceiling is the display, not a fixed 800 px that cut the card."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QApplication

    from src.ui.main_window import (
        MAXIMUM_AUTO_FIT_HEIGHT,
        SCREEN_EDGE_MARGIN,
        MainWindow,
    )

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])

    class _TallDisplay:
        def availableGeometry(self):
            return QRect(0, 0, 1920, 1200)

    class _ShortDisplay:
        def availableGeometry(self):
            return QRect(0, 0, 1280, 700)

    window = MainWindow()
    try:
        monkeypatch.setattr(window, "screen", lambda: _TallDisplay())
        assert window._auto_fit_height_ceiling() == 1200 - SCREEN_EDGE_MARGIN
        # A page that needs 1000 px may now have them: the old ceiling stopped
        # at 800 and painted the actions below the window's edge.
        window._apply_window_size_limits(1000)
        assert window.maximumHeight() > MAXIMUM_AUTO_FIT_HEIGHT

        # A display shorter than the old ceiling keeps its own height; there
        # the page scrolls (and shows its bar) instead of leaving the screens.
        monkeypatch.setattr(window, "screen", lambda: _ShortDisplay())
        assert window._auto_fit_height_ceiling() == 700
        window._apply_window_size_limits(1000)
        assert window.maximumHeight() <= 700
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_notes_scrolling_inside_their_card_does_not_grow_the_window():
    """A long note scrolls in its own five-line box; the frame stays put.

    The release-notes view is a scroll area inside a card, so its bar never
    goes away however tall the window gets. Counting that bar as page overflow
    grew the frame by its range on every pass: selecting a release with long
    notes ballooned the window to most of the display.
    """
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest

    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    settings = QSettings("innioasis", "updater")
    saved = settings.value("onboarding_completed")
    settings.setValue("onboarding_completed", True)
    window = MainWindow()
    window.show()
    QTest.qWait(300)
    try:
        page = window._select_page
        note = (
            "<p>"
            + "</p><p>".join(
                "General performance and stability improvements, with a sentence "
                "long enough to wrap over several lines in the notes card."
                for _ in range(40)
            )
            + "</p>"
        )
        page._notes.setHtml(note)
        QTest.qWait(200)
        app.processEvents()
        assert page._notes.verticalScrollBar().maximum() > page._notes.verticalScrollBar().minimum()

        before = window.height()
        window._clear_scrollbar_after_fit()
        QTest.qWait(500)
        app.processEvents()

        assert window.height() <= before + 4, (before, window.height())
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
        if saved is None:
            settings.remove("onboarding_completed")
        else:
            settings.setValue("onboarding_completed", saved)
