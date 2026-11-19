"""The connect card offers its reset guidance behind the link, never open."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

import src.ui.flash_page as flash_page
from src.ui.flash_page import FlashPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def test_the_connect_card_shows_the_reset_tip_every_time(qapp, monkeypatch):
    # The tip was already shown once on an earlier power-off card, and a "Got
    # it." from an earlier install is remembered. The connect card is the one a
    # player that did not answer lands on, so it must still offer the guidance
    # every time - behind its link, because the link is the only instruction on
    # that card until the reader asks for the diagram.
    monkeypatch.setattr(flash_page, "_hint_seen", lambda key: True)
    monkeypatch.setattr(flash_page, "_hint_dismissed", lambda key: False)

    page = FlashPage()
    try:
        page.resize(1400, 780)
        page.show()
        for _ in range(6):
            qapp.processEvents()
        page.set_model("Y1")
        page.show_connect_search()
        page._reveal_connect_prompt()
        for _ in range(8):
            qapp.processEvents()

        assert page._wait_copy == "connect"
        assert page._paperclip_help_wanted(), "the connect tip has no link"
        assert page._paperclip_hint_host.isHidden(), "the connect tip opened itself"

        page._on_paperclip_help()
        for _ in range(8):
            qapp.processEvents()
        assert page._paperclip_hint_host.isVisible(), "the link did not open the tip"
        copy = (page._paperclip_hint.text() + " " + page._wait_prompt_label.text()).lower()
        assert "paperclip" in copy or "reset" in copy or "pin" in copy
    finally:
        page.deleteLater()
        qapp.processEvents()
