"""Install card hints: the first power-off card opens, the connect card offers.

The paperclip tip *is* the instruction at power-off and dropped-USB, so it is
open the first time an install reaches that card; afterwards it waits behind its
How? link. The connect card is the opposite: a reader whose player is not being
found always gets the advice link, and the diagram opens only when it is
clicked. None of this is firmware-specific.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src.i18n import tr
from src.ui import flash_page as flash_page_module
from src.ui.flash_page import FlashPage


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def page(qapp):
    widget = FlashPage()
    try:
        yield widget
    finally:
        widget.deleteLater()
        qapp.processEvents()


@pytest.fixture(autouse=True)
def isolated_hints(tmp_path, monkeypatch):
    """Keep the seen/dismissed flags in a throwaway store.

    The page reads them through the module's helpers, whose default store is the
    real app's settings: a developer who has already seen the tip would
    otherwise change what these tests observe.
    """
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(
        flash_page_module,
        "_hint_seen",
        lambda name, settings=None: bool(
            store.value(f"seen_install_hints/{name}", False, type=bool)
        ),
    )
    monkeypatch.setattr(
        flash_page_module,
        "_set_hint_seen",
        lambda name, seen=True, settings=None: store.setValue(
            f"seen_install_hints/{name}", bool(seen)
        ),
    )
    monkeypatch.setattr(
        flash_page_module,
        "_hint_dismissed",
        lambda name, settings=None: bool(
            store.value(f"dismissed_install_hints/{name}", False, type=bool)
        ),
    )
    monkeypatch.setattr(
        flash_page_module,
        "_set_hint_dismissed",
        lambda name, dismissed=True, settings=None: store.setValue(
            f"dismissed_install_hints/{name}", bool(dismissed)
        ),
    )
    return store


def test_the_first_power_off_card_opens_the_tip(page):
    page.set_model("Y1")

    page.show_power_off_prompt()

    # Its diagram is the instruction, so the first install shows it straight
    # away rather than hiding it behind a link the reader has no reason to try.
    assert page._paperclip_revealed is True
    assert not page._paperclip_hint_host.isHidden()
    assert "hidden button" in page._paperclip_hint.text()


def test_the_next_power_off_card_keeps_it_behind_the_how_link(page):
    page.set_model("Y1")
    page.show_power_off_prompt()
    page._on_paperclip_got_it()

    page.show_power_off_prompt()
    assert page._paperclip_revealed is False
    assert page._paperclip_hint_host.isHidden()
    assert page._paperclip_help_wanted() is True
    page._sync_wait_band_page()
    assert tr("flash_paperclip_help") in page._wait_question_label.text()

    # The link is the door: following it opens the same tip exactly once.
    page._on_paperclip_help()
    assert page._paperclip_revealed is True
    assert not page._paperclip_hint_host.isHidden()
    assert page._paperclip_help_wanted() is False


def test_the_connect_card_offers_its_advice_without_opening_it(page):
    page.set_model("Y1")

    page.show_connect_search()

    # Never automatic on the connect card: the sentence says what to do, and
    # the link below it is always on offer while the handshake is running.
    assert page._paperclip_revealed is False
    assert page._paperclip_hint_host.isHidden()
    assert page._paperclip_help_wanted() is True
    link = tr("flash_not_connecting_help_fmt").format(model="Y1")
    page._sync_wait_band_page()
    assert link in page._wait_question_label.text()

    page._on_paperclip_help()
    assert page._paperclip_revealed is True
    assert not page._paperclip_hint_host.isHidden()


def test_each_new_handshake_starts_with_the_tip_closed(page):
    page.set_model("Y1")
    page.show_connect_search()
    page._on_paperclip_help()
    assert page._paperclip_revealed is True

    page.show_connect_search()

    assert page._paperclip_revealed is False
    assert page._paperclip_hint_host.isHidden()
