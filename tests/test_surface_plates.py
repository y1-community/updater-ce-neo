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

from src.ui.widgets import BatteryToast, Card  # noqa: E402
from src.ui.dark import T  # noqa: E402

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


def test_card_does_not_request_a_translucent_child_window(no_glass):
    card = Card()
    assert card.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False


def test_card_frame_has_no_rectangular_global_stylesheet_fill():
    from src.ui.dark import _build_qss

    rules = [line.strip() for line in _build_qss().splitlines()]
    assert 'QFrame[cssClass="card"] {' not in rules


def test_cards_and_battery_hint_paint_the_bento_surface_without_glass(no_glass):
    from PySide6.QtGui import QImage

    for widget in (Card(), BatteryToast()):
        widget.resize(120, 56)
        widget.show()
        _app.processEvents()
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        assert image.pixelColor(60, 28).name() == T().bg_card
        window = widget.palette().color(QPalette.ColorRole.Window)
        assert image.pixelColor(0, 0).name() == window.name()
        widget.close()


def test_window_surface_matches_the_live_theme():
    widget = QWidget()
    surfaces.paint_window_surface(widget)
    app_window = _app.palette().color(QPalette.ColorRole.Window)
    assert widget.palette().color(QPalette.ColorRole.Window).name() == app_window.name()


def test_clear_glass_plate_removes_its_own_fill_without_glass(no_glass):
    """Text surfaces inherit their parent card instead of painting a plate."""
    for widget in (QLabel("notes"), QWidget()):
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        surfaces.clear_glass_plate(widget)
        assert widget.autoFillBackground() is False
        assert widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is False
        assert widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False


def test_clear_glass_plate_keeps_scroll_views_on_card_surface(no_glass):
    for view in (QListWidget(), QTextEdit()):
        surfaces.clear_glass_plate(view)
        viewport = view.viewport()
        assert view.autoFillBackground() is False
        assert viewport.autoFillBackground() is True
        assert viewport.palette().color(QPalette.ColorRole.Base).name() == T().bg_card
        assert viewport.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is False
        assert not view.styleSheet()
        assert not viewport.styleSheet()


def test_select_page_scroll_views_keep_native_styles_and_card_surfaces(no_glass):
    from src.ui.select_page import SelectPackagePage

    page = SelectPackagePage()
    for view in (page._release_list, page._notes):
        assert not view.styleSheet()
        assert not view.viewport().styleSheet()
        assert not view.verticalScrollBar().styleSheet()
        assert not view.horizontalScrollBar().styleSheet()
        assert view.frameShape() == view.Shape.NoFrame
    page.close()


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
