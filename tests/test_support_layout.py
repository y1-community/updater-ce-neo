"""The Support Us card must show all of its copy at any window width.

The appeal is a wrapped paragraph and the donation methods are four labelled
buttons: both used to be squeezed into the height and width the card happened
to have, cutting the last lines off the paragraph and the first letters off
every button label.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from src.ui.support_page import SupportPage


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _page(width: int, height: int = 700):
    app = _app()
    page = SupportPage()
    page.resize(width, height)
    page.show()
    for _ in range(3):
        app.processEvents()
    return page


def _rows(page) -> int:
    return len({button.y() for button, _ in page._method_buttons})


def test_the_appeal_is_never_taller_than_the_space_it_gets():
    for width in (1200, 900, 760, 640, 560):
        page = _page(width)
        label = page._appeal_body
        needed = label.heightForWidth(label.width())
        assert label.height() >= needed, (width, label.height(), needed)
        page.close()


def test_rewrapping_narrower_keeps_the_copy_whole():
    page = _page(1000)
    label = page._appeal_body
    assert label.height() >= label.heightForWidth(label.width())
    page.resize(600, 700)
    for _ in range(3):
        _app().processEvents()
    assert label.height() >= label.heightForWidth(label.width())
    page.close()


def test_every_method_button_keeps_its_full_label():
    for width in (1100, 760, 560):
        page = _page(width)
        for button, key in page._method_buttons:
            assert button.width() >= button.sizeHint().width(), (width, key, button.width())
        page.close()


def test_the_method_row_is_one_line_when_it_fits():
    page = _page(1100)
    assert _rows(page) == 1, "four buttons fit on one row in a wide window"
    page.close()


def test_the_flow_layout_starts_a_new_row_instead_of_squeezing():
    """A translated label longer than the card is what used to be cut off."""
    from PySide6.QtWidgets import QPushButton, QWidget

    from src.ui.widgets import FlowLayout

    app = _app()
    host = QWidget()
    host.resize(320, 200)
    flow = FlowLayout(host, spacing=8)
    buttons = []
    for label in ("Unterstützen Sie uns auf Ko-fi", "Spenden Sie über PayPal", "Spenden Sie über Revolut"):
        button = QPushButton(label, host)
        buttons.append(button)
        flow.addWidget(button)
    host.show()
    for _ in range(3):
        app.processEvents()
    rows = {button.y() for button in buttons}
    assert len(rows) > 1, "three long buttons cannot share one 320 px row"
    for button in buttons:
        assert button.width() >= button.sizeHint().width(), button.text()
    host.close()


def test_the_height_hint_follows_the_width_it_is_given():
    page = _page(1000)
    narrow = page.content_height_hint(620)
    wide = page.content_height_hint(1200)
    assert narrow > wide, (narrow, wide)
    assert page.content_height_hint() == page.content_height_hint(page.width())
    page.close()


def _card_overflow(page) -> int:
    """How far the card's own controls reach past its bottom edge (<= 0 is good)."""
    card = page._card
    bottoms = [
        widget.mapTo(card, QPoint(0, widget.height())).y()
        for widget in [b for b, _ in page._method_buttons]
        + [page._honeygain_link, page._crypto_toggle_link]
        if widget.isVisible()
    ]
    return max(bottoms) - card.height()


def test_the_card_grows_when_the_button_row_wraps():
    """A fixed-height card drew the wrapped row past its own bottom edge.

    Translated labels are longer than the English ones, which is what wraps
    the row on a window the app already allows.
    """
    page = _page(1000)
    rows = _rows(page)
    height = page._card.height()
    for button, key in page._method_buttons:
        button.setText(
            {
                "donate_kofi": "Unterstützen Sie uns auf Ko-fi",
                "donate_paypal": "Spenden Sie über PayPal",
                "donate_revolut": "Spenden Sie über Revolut",
                "donate_patreon": "Auf Patreon unterstützen",
            }.get(key, button.text())
        )
    page.resize(630, 700)
    for _ in range(4):
        _app().processEvents()
    assert _rows(page) > rows, (_rows(page), rows, page._card.width())
    assert page._card.height() > height, (_rows(page), page._card.height(), height)
    assert _card_overflow(page) <= 0
    page.close()


def test_no_control_is_drawn_past_the_card_bottom():
    for width in (1100, 900, 760, 640, 560):
        page = _page(width)
        assert _card_overflow(page) <= 0, (width, _card_overflow(page))
        page.close()


def test_the_crypto_drawer_adds_height_and_reports_it():
    app = _app()
    page = _page(900)
    before = page.content_height_hint()
    changed = []
    page.content_size_changed.connect(lambda: changed.append(True))
    page._toggle_crypto_drawer()
    for _ in range(3):
        app.processEvents()
    after = page.content_height_hint()
    assert after > before, (before, after)
    assert changed, "the window must be told the card grew"
    page.close()


def test_longer_copy_tells_the_window_the_card_changed():
    """A longer translation wraps onto more lines; the window has to grow."""
    app = _app()
    page = _page(1000)
    changed = []
    page.content_size_changed.connect(lambda: changed.append(True))
    page._appeal_body.setText("<b>Long appeal.</b> " + ("Donations keep the test players coming. " * 12))
    for _ in range(3):
        app.processEvents()
    label = page._appeal_body
    assert label.height() >= label.heightForWidth(label.width())
    assert changed, "longer copy must refit the window"
    page.close()
