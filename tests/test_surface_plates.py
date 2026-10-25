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
        # A padding point, clear of whatever the widget draws in its middle
        # (the battery toast's glyph sits on the centre line).
        assert image.pixelColor(20, 8).name() == T().bg_card
        # The rounded card sits on the window surface, which it also paints in
        # its own corners: no square plate of a different shade, and nothing
        # left stale around the radius.
        assert image.pixelColor(0, 0).name() == T().bg
        widget.close()


def test_window_surface_matches_the_live_theme():
    widget = QWidget()
    surfaces.paint_window_surface(widget)
    app_window = _app.palette().color(QPalette.ColorRole.Window)
    assert widget.palette().color(QPalette.ColorRole.Window).name() == app_window.name()


def test_a_child_inside_a_card_erases_to_the_card_not_the_window(monkeypatch):
    """A transparent erase inside a card left a boxy, clearer rectangle.

    Every scroll viewport and sealed label clears its dirty rect before it
    draws. Inside a painted card that clear has to reproduce the card's own
    translucent plate; clearing to transparent punched the plate out of that
    rectangle and let the window material show through it un-tinted.
    """
    from PySide6.QtGui import QImage

    monkeypatch.setattr(surfaces, "glass_surfaces_enabled", lambda: True)
    card = Card()
    card.resize(200, 100)
    child = QWidget(card)
    child.setGeometry(20, 20, 160, 60)
    surfaces.show_glass_backdrop(child)
    card.show()
    _app.processEvents()
    try:
        image = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        card.render(image)
        outside = image.pixelColor(8, 50)
        inside = image.pixelColor(100, 50)
        assert outside.alpha() > 0, "the card plate is missing"
        assert inside.alpha() == outside.alpha(), (
            f"the child cleared a hole in the card: {inside.alpha()} vs {outside.alpha()}"
        )
        assert (inside.red(), inside.green(), inside.blue()) == (
            outside.red(),
            outside.green(),
            outside.blue(),
        )
        assert surfaces.card_fill_under(child) == surfaces.card_surface_fill()
        assert surfaces.card_fill_under(card) is None
    finally:
        card.close()


def test_select_page_cards_paint_their_own_surface_on_glass(monkeypatch):
    """The filter, software list and notes sit on cards, not bare glass.

    Settings and the battery hint paint a rounded pane over the window
    material; the three Select Software groups are the same kind of card and
    must not erase themselves back to the bare backdrop.
    """
    from PySide6.QtGui import QImage

    from src.ui.select_page import SelectPackagePage

    monkeypatch.setattr(surfaces, "glass_surfaces_enabled", lambda: True)
    page = SelectPackagePage()
    page.resize(900, 600)
    page.show()
    _app.processEvents()
    try:
        for name in ("_filter_group", "_pkg_group", "_notes_group"):
            card = getattr(page, name)
            assert card._glass_clear is False, name
            image = QImage(card.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            card.render(image)
            # A point in the card's left padding: the rounded pane is there.
            assert image.pixelColor(6, card.height() // 2).alpha() > 0, name
            # The corner stays clear, so the pane reads as rounded.
            assert image.pixelColor(0, 0).alpha() == 0, name
    finally:
        page.close()


def test_clear_glass_plate_removes_its_own_fill_without_glass(no_glass):
    """Text surfaces inherit their parent card instead of painting a plate."""
    for widget in (QLabel("notes"), QWidget()):
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        surfaces.clear_glass_plate(widget)
        assert widget.autoFillBackground() is False
        assert widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is False
        assert widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False


def test_clear_glass_plate_keeps_scroll_views_clear_inside_cards(no_glass):
    # Both item views and rich text documents inherit the painted card surface;
    # neither viewport may add its own square background plate.
    card = Card()
    view = QListWidget(card)
    surfaces.clear_glass_plate(view)
    viewport = view.viewport()
    assert view.autoFillBackground() is False
    assert viewport.autoFillBackground() is False
    assert viewport.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is True
    assert not view.styleSheet()
    assert not viewport.styleSheet()
    card.close()

    document = QTextEdit()
    surfaces.clear_glass_plate(document)
    assert document.viewport().autoFillBackground() is False
    assert document.viewport().testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is True

    notes_card = Card()
    notes = QTextEdit(notes_card)
    surfaces.clear_glass_plate(notes)
    assert notes.viewport().autoFillBackground() is False
    assert notes.viewport().testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is True
    notes_card.close()


def test_select_page_scroll_views_keep_native_styles_and_card_surfaces(no_glass):
    from src.ui.select_page import SelectPackagePage

    page = SelectPackagePage()
    surfaces.clear_glass_plate(page._release_list)
    surfaces.clear_glass_plate(page._notes)
    for view in (page._release_list, page._notes):
        assert not view.styleSheet()
        assert not view.viewport().styleSheet()
        assert not view.verticalScrollBar().styleSheet()
        assert not view.horizontalScrollBar().styleSheet()
        assert view.frameShape() == view.Shape.NoFrame
    try:
        for view in (page._release_list, page._notes):
            assert view.viewport().autoFillBackground() is False
            assert view.viewport().testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is True
        # Theme refresh must preserve the clear card surface.
        page.refresh_theme()
        for view in (page._release_list, page._notes):
            assert view.viewport().autoFillBackground() is False
            assert view.viewport().testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is True
    finally:
        page.close()


def test_transparent_scroll_area_keeps_its_scrollbars_painted(no_glass):
    """A bar must not inherit the alpha-0 roles the viewport needs.

    The bar paints its track and groove from its own palette. Breeze draws that
    track from ``Window``/``Base``, so a bar inheriting the scroll area's
    transparent roles paints nothing at all: the strip became a hole in the
    window and the Plasma window behind the app showed through next to the
    settings list as a gradient band.
    """
    from PySide6.QtWidgets import QScrollArea

    from src.ui.scrollbars import configure_scroll_area

    area = QScrollArea()
    area.setWidget(QWidget())
    configure_scroll_area(area, transparent=True)
    try:
        # The viewport still erases to the card behind it...
        assert area.viewport().palette().color(QPalette.ColorRole.Window).alpha() == 0
        # ...while both bars keep colours they can actually draw with.
        for bar in (area.verticalScrollBar(), area.horizontalScrollBar()):
            for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base):
                assert bar.palette().color(role).alpha() == 255, role
    finally:
        area.deleteLater()


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
