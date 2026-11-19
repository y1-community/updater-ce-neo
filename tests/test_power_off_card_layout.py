"""The power-off card lays its rows out, it does not stack them on top."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

import src.ui.flash_page as flash_page
from src.ui.flash_page import FlashPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _settle(app, times=8):
    for _ in range(times):
        app.processEvents()


def _rect(page, widget):
    return QRect(widget.mapTo(page, widget.rect().topLeft()), widget.size())


def test_the_card_is_at_least_as_tall_as_its_rows_need(qapp, monkeypatch):
    """The tip grows the card after its first layout; the card must follow."""
    monkeypatch.setattr(flash_page, "_hint_seen", lambda key: False)
    monkeypatch.setattr(flash_page, "_hint_dismissed", lambda key: False)
    monkeypatch.setattr(flash_page, "_set_hint_seen", lambda key: None)

    page = FlashPage()
    try:
        page.resize(1400, 780)
        page.show()
        _settle(qapp)
        page.set_model("Y2")
        page.show_power_off_prompt(show_pin_tip=True)
        _settle(qapp)

        card = page._wait_card
        host = page._paperclip_hint_host
        assert host.isVisible(), "the first power-off card shows the tip"
        assert card.height() >= card.minimumSizeHint().height(), (
            f"card {card.height()}px is shorter than its {card.minimumSizeHint().height()}px minimum"
        )

        # The tip sits above the tick, not on it.
        tick = page._keep_data_cb
        assert tick.isVisible()
        overlap = _rect(page, host).intersected(_rect(page, tick))
        assert overlap.height() <= 2, f"tip overlaps the tick by {overlap.height()}px"
    finally:
        page.deleteLater()
        qapp.processEvents()
