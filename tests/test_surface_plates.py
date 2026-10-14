"""Surfaces without a glass backdrop must paint the window, not a plate.

macOS gets a real backdrop behind the window, so a child there can simply leave
its background unset. Windows and Linux have no such backdrop: the window is the
flat theme colour, and a widget left with no background of its own either keeps
the plate it used to draw (the rectangle reported behind the release notes and
the release list) or never erases at all (the donation goal line vanishing from
the status bar). These tests pin the no-glass behaviour of both helpers.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QListWidget, QTextEdit, QWidget  # noqa: E402

pytest.importorskip("PySide6")

_app = QApplication.instance() or QApplication(sys.argv)

from src.ui import surfaces  # noqa: E402


@pytest.fixture
def no_glass(monkeypatch):
    """Windows / Linux: there is no glass backdrop to show through."""
    monkeypatch.setattr(surfaces, "glass_surfaces_enabled", lambda: False)


def _assert_no_plate(widget):
    pal = widget.palette()
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
        assert pal.color(group, QPalette.ColorRole.Base) == pal.color(group, QPalette.ColorRole.Window)
    assert widget.autoFillBackground() is True
    assert widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is False
    assert widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False


def test_paint_window_surface_removes_the_base_plate(no_glass):
    widget = QWidget()
    surfaces.paint_window_surface(widget)
    _assert_no_plate(widget)


def test_window_surface_matches_the_live_theme():
    widget = QWidget()
    surfaces.paint_window_surface(widget)
    app_window = _app.palette().color(QPalette.ColorRole.Window)
    assert widget.palette().color(QPalette.ColorRole.Window).name() == app_window.name()


def test_clear_glass_plate_still_clears_without_glass(no_glass):
    """This used to return early, which is what left the box on Linux."""
    for widget in (QListWidget(), QTextEdit(), QLabel("notes"), QWidget()):
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        surfaces.clear_glass_plate(widget)
        _assert_no_plate(widget)


def test_clear_glass_plate_clears_a_scroll_area_viewport(no_glass):
    view = QTextEdit()
    surfaces.clear_glass_plate(view)
    viewport = view.viewport()
    assert viewport is not None
    _assert_no_plate(viewport)


def test_donation_bar_hosts_can_erase_without_glass(no_glass):
    from src.donation_dialog import DonationStatusBar
    from src.donors import parse_donors_csv_text

    donations = parse_donors_csv_text(
        "name,amount,method,date,url\nAlice,25,kofi,2026-10-01,\n"
    )
    host_window = QWidget()
    bar = DonationStatusBar(parent=host_window, donations=donations)
    assert bar.parent() is host_window
    for name in (
        "_donation_container",
        "_status_container",
        "_goal_group",
        "_goal_ticker",
        "_donor_ticker",
        "_status_ticker",
    ):
        host = getattr(bar, name)
        _assert_no_plate(host)


def test_donation_bar_ticker_owns_its_row():
    """A zero-height host put the goal line in a row with no space."""
    from src.donation_dialog import _Ticker

    ticker = _Ticker()
    assert ticker.minimumHeight() >= ticker.label.sizeHint().height() > 0
    assert ticker.minimumWidth() == 0
