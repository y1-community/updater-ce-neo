"""The keep-settings-and-media switch is its own card, behind advanced settings.

Skipping the compatibility question changes every install, so it is not a row
inside the Advanced Settings card: it has a card of its own, styled like the
rest of the column, and it only appears once Show Advanced Settings is on.
Hiding it again must also stop it applying.
"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import device_tracking, keep_data
from src.ui.settings_page import SettingsPage


@pytest.fixture()
def settings_page():
    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("innioasis", "updater")
    saved = {
        key: settings.value(key)
        for key in ("advanced_settings", keep_data.ALLOW_ANY_KEY)
    }
    device_tracking.set_advanced_settings_enabled(False)
    keep_data.set_allow_keep_data_any(False)
    page = SettingsPage()
    page.show()
    app.processEvents()
    try:
        yield page
    finally:
        page.close()
        app.processEvents()
        for key, value in saved.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
        device_tracking.set_advanced_settings_enabled(False)
        keep_data.set_allow_keep_data_any(False)


def test_switch_has_its_own_card_not_the_advanced_card(settings_page):
    page = settings_page
    assert page._cb_allow_keep_data_any is not page._cb_advanced_settings
    assert page._keep_data_card is not page._advanced_card
    # Its own card, sitting directly under the Advanced Settings card.
    assert page._keep_data_card.findChild(type(page._cb_allow_keep_data_any)) is not None
    index = page._card_layout.indexOf(page._keep_data_card)
    assert index > page._card_layout.indexOf(page._advanced_card)
    # Styled like the other cards in the column.
    assert page._keep_data_card.property("cssClass") == "card"
    # The title is the option's own words, with the switch on that line.
    # Updater CE says software; the MediaTek Installer keeps "firmware".
    assert page._keep_data_card.title() == (
        "Allow Keep Settings and Media on any software install"
    )
    assert page._keep_data_card.isAncestorOf(page._cb_allow_keep_data_any)
    assert page._switch_rows[page._cb_allow_keep_data_any][1].isHidden()


def test_card_is_hidden_until_advanced_settings_is_on(settings_page):
    app = QApplication.instance()
    page = settings_page

    assert page._keep_data_card.isHidden()
    assert not page._cb_allow_keep_data_any.isVisible()

    page._cb_advanced_settings.setChecked(True)
    app.processEvents()
    assert not page._keep_data_card.isHidden()
    assert page._cb_allow_keep_data_any.isVisible()

    page._cb_advanced_settings.setChecked(False)
    app.processEvents()
    assert page._keep_data_card.isHidden()


def test_hiding_the_card_stops_the_option_applying(settings_page):
    page = settings_page
    page.reveal_advanced_methods()
    page._cb_allow_keep_data_any.setChecked(True)
    assert keep_data.allow_keep_data_any()

    page.hide_advanced_methods()

    assert not keep_data.allow_keep_data_any()
    assert not page._cb_allow_keep_data_any.isChecked()
