"""The Keep Data sanity check never turns a No into a cancelled install.

When the player's software is unknown, the install asks which firmware is
running. One compatible item is a yes/no question; answering No means the
player runs something else, so the install continues and explains that the
settings cannot be carried over. It is not a request to stop.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.i18n import tr
from src.ui.flash_page import FlashPage


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def page(qapp):
    widget = FlashPage()
    try:
        yield widget
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_the_no_chip_reports_incompatible_software_instead_of_stopping(page, qapp):
    answered = []
    page.show_keep_data_confirmation(
        "Is Original Software installed?",
        "Only compatible software can keep settings.",
        (),
        selected="",
        on_no=lambda: answered.append(True),
    )

    assert page._keep_data_confirm_active
    page._wait_cancel_btn.click()
    qapp.processEvents()

    assert answered == [True]
    # The card is still the question: the install was not cancelled.
    assert page._wait_copy == "keep_data_confirm"


def test_without_a_no_handler_the_cancel_chip_still_stops_the_install(page, qapp, monkeypatch):
    stopped = []
    monkeypatch.setattr(page, "_nothing_written_yet", lambda: True)
    page._cancel_wait_callback = lambda: stopped.append(True)

    page.show_keep_data_confirmation("Is Original Software installed?", "", ())
    page._wait_cancel_btn.click()
    qapp.processEvents()

    assert stopped == [True]


def test_one_compatible_item_offsets_the_checkbox_with_the_incoming_software(page, qapp):
    page.show_keep_data_confirmation(
        "Is Better-Y installed?",
        "",
        (),
        selected="Better-Y",
        on_no=lambda: None,
    )
    # A yes/no question has no drop-down to choose from; the chips read
    # Yes/No rather than Next/Cancel.
    assert page._keep_data_choice.isHidden()
    assert page._wait_continue_btn.toolTip() == tr("btn_yes")
    assert page._wait_cancel_btn.toolTip() == tr("btn_no")


def test_several_items_show_a_dropdown_with_the_incoming_software_selected(page, qapp):
    page.show_keep_data_confirmation(
        "Which software is running?",
        "",
        ("Original Software", "Better-Y", "Koensayr", "Other software"),
        selected="Better-Y",
        on_no=lambda: None,
    )
    assert not page._keep_data_choice.isHidden()
    assert page.keep_data_confirm_choice() == "Better-Y"
