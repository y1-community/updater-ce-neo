"""The progress bar must show its track, its chunk and its percentage.

The platform styles stopped drawing the chunk on the hosts we ship for (Aqua
under the app palette, acrylic on Windows) and the install looked stalled at
every percentage, so the bar paints itself. These tests drive the paint
directly: ``QWidget.render()`` fills an opaque background first, so a rendered
widget never proves what the bar itself drew.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from src.ui.widgets import BUSY_SEGMENT, paint_progress_bar

# One application for the module: the paint needs a font database, and a
# painter drawing text without one aborts the process.
app = QApplication.instance() or QApplication(sys.argv)

TRACK = QColor("#2b303c")
FILL = QColor("#3ecf6a")
LABEL = QColor("#ffffff")
CANVAS = QColor("#ff00ff")
BAR_WIDTH, BAR_HEIGHT = 200, 20
BAR = QRectF(0, 0, BAR_WIDTH, BAR_HEIGHT)


def _paint(**kwargs):
    image = QImage(BAR.width(), BAR.height(), QImage.Format_ARGB32)
    image.fill(CANVAS)
    painter = QPainter(image)
    options = dict(track=TRACK, fill=FILL, label_color=LABEL)
    options.update(kwargs)
    paint_progress_bar(painter, BAR, **options)
    painter.end()
    return image


def _row(image, y=None):
    """Colour of every pixel across one row of the painted bar."""
    y = image.height() // 2 if y is None else y
    return [image.pixelColor(x, y).name() for x in range(image.width())]


def _chunk_width(image, y=None):
    """How many pixels of the row carry the accent chunk."""
    fill = FILL.name()
    return sum(1 for name in _row(image, y) if name == fill)


def _similar(color: QColor, name: str, tolerance: int = 24) -> bool:
    other = QColor(name)
    return (
        abs(color.red() - other.red()) <= tolerance
        and abs(color.green() - other.green()) <= tolerance
        and abs(color.blue() - other.blue()) <= tolerance
    )


def _count(image, color: QColor, tolerance: int = 24) -> int:
    total = 0
    for y in range(image.height()):
        for x in range(image.width()):
            if _similar(image.pixelColor(x, y), color.name(), tolerance):
                total += 1
    return total


def test_chunk_tracks_the_value_the_bar_was_given():
    for percent in (10, 32, 64):
        image = _paint(fraction=percent / 100)
        expected = BAR_WIDTH * percent / 100
        painted = _chunk_width(image)
        assert abs(painted - expected) <= 3, (percent, painted, expected)


def test_track_stays_visible_ahead_of_the_chunk():
    image = _paint(fraction=0.4)
    row = _row(image)
    ahead = row[int(BAR_WIDTH * 0.7)]
    assert _similar(QColor(ahead), TRACK.name()), ahead
    behind = row[int(BAR_WIDTH * 0.2)]
    assert _similar(QColor(behind), FILL.name()), behind


def test_empty_bar_paints_the_track_only():
    image = _paint(fraction=0.0)
    assert _chunk_width(image) == 0
    assert _count(image, TRACK) > BAR_WIDTH * BAR_HEIGHT // 2


def test_full_bar_leaves_no_track_behind_the_chunk():
    image = _paint(fraction=1.0)
    assert abs(_chunk_width(image) - BAR_WIDTH) <= 2
    assert _count(image, TRACK, tolerance=10) == 0


def test_percent_is_painted_inside_the_bar_and_can_be_switched_off():
    plain = _paint(fraction=0.32)
    percent = _paint(fraction=0.32, text="32%")
    assert percent != plain, "the percent was never drawn"

    # The lab colour lands on the bar, and the copy is centred: the middle of
    # the bar carries glyph pixels the untouched paint did not have.
    middle = BAR_WIDTH // 2
    glyphs = [
        (x, y)
        for y in range(BAR_HEIGHT)
        for x in range(middle - 30, middle + 30)
        if _similar(percent.pixelColor(x, y), LABEL.name(), tolerance=90)
        and not _similar(plain.pixelColor(x, y), LABEL.name(), tolerance=90)
    ]
    assert glyphs, "no glyph pixels around the centre of the bar"


def test_busy_bar_sweeps_instead_of_filling():
    start = _paint(busy=True, offset=0.0)
    middle = _paint(busy=True, offset=0.5)
    finish = _paint(busy=True, offset=0.95)

    for image in (start, middle, finish):
        assert _chunk_width(image) <= BAR_WIDTH * BUSY_SEGMENT + 2

    def centre(image):
        row = _row(image)
        xs = [x for x, name in enumerate(row) if name == FILL.name()]
        return sum(xs) / len(xs) if xs else -1

    assert centre(start) < centre(middle) < centre(finish)


def test_busy_bar_never_shows_a_percent():
    """A sweeping bar has no percentage to name; the copy stays off."""
    image = _paint(busy=True, offset=0.5, text="0%")
    plain = _paint(busy=True, offset=0.5)
    assert image == plain


def test_percent_colour_clears_both_the_track_and_the_chunk():
    from src.ui.dark import apply_theme, contrast_ratio, progress_bar_colors
    from src.ui.widgets import InstallProgress

    app = QApplication.instance() or QApplication(sys.argv)
    for dark in (True, False):
        apply_theme(app, force_dark=dark)
        bar = InstallProgress()
        track, fill, label = progress_bar_colors(bar)
        assert contrast_ratio(label, track) >= 4.5, (dark, label.name(), track.name())
        assert contrast_ratio(label, fill) >= 3.0, (dark, label.name(), fill.name())


def test_flash_page_bars_are_themed_and_name_their_percentage():
    from src.ui.dark import apply_theme
    from src.ui.flash_page import FlashPage
    from src.ui.widgets import InstallProgress

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    page = FlashPage()
    try:
        for name in ("_prep_progress", "_wait_progress_bar", "_progress_bar", "_download_progress"):
            bar = getattr(page, name)
            assert isinstance(bar, InstallProgress), name

        page.show_flashing()
        page.update_progress(48)
        assert page._progress_bar.isTextVisible()
        assert page._progress_bar.text() == "48%"
        assert abs(page._progress_bar.fraction() - 0.48) < 0.01
        assert not page._progress_bar.is_busy()

        page.show_please_wait()
        assert page._wait_progress_bar.is_busy()
        # Nothing is filling yet, so there is no percentage to show.
        assert page._wait_progress_bar.text() == ""

        page.show_connect_search()
        assert not page._wait_progress_bar.is_busy()
        assert page._wait_progress_bar.value() == 0
    finally:
        page.close()
        app.processEvents()


def test_busy_sweep_only_runs_while_the_bar_is_on_screen():
    """No timer on a hidden bar: an install left open must not burn CPU."""
    from src import ui

    app = QApplication.instance() or QApplication(sys.argv)
    original = ui.widgets.prefers_reduced_motion
    ui.widgets.prefers_reduced_motion = lambda: False
    bar = ui.widgets.InstallProgress()
    try:
        bar.show()
        app.processEvents()
        bar.set_busy(True)
        assert bar._timer.isActive(), "a visible busy bar sweeps"

        bar.hide()
        app.processEvents()
        assert not bar._timer.isActive(), "hiding the bar stops the sweep"

        bar.show()
        app.processEvents()
        assert bar._timer.isActive()
        bar.set_busy(False)
        assert not bar._timer.isActive(), "a determinate bar does not sweep"
    finally:
        ui.widgets.prefers_reduced_motion = original
        bar.close()
        app.processEvents()


def test_download_screen_lines_up_with_the_install_card():
    """Both progress screens come from one wrapper: same width, same padding."""
    from src.ui.dark import apply_theme
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)
    page = FlashPage()
    try:
        page.set_package_name("Inniclassic Y2 October 8 2026 for Y1")
        page.resize(1180, 620)
        page.show()
        app.processEvents()

        page.show_downloading()
        page.update_download_progress(32, "45.9 MB / 434.3 MB (10%)")
        app.processEvents()
        download = page._download_card.geometry()
        download_column = page._download_pkg_label.geometry()

        page.show_flashing()
        page.update_progress(0)
        app.processEvents()
        install = page._progress_card.geometry()
        install_column = page._flash_pkg_label.geometry()

        assert download.x() == install.x()
        assert download.width() == install.width()
        assert download_column.x() == install_column.x()
        assert download_column.width() == install_column.width()
        # The chips sit in the same place on both cards.
        assert page._download_cancel_btn.x() == page._cancel_btn.x()
    finally:
        page.close()
        app.processEvents()
