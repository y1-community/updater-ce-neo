"""Turning Show Advanced Settings off returns every advanced choice to default.

The advanced cards expose the flash method, the installation mode (terminal /
SP Flash Tool GUI handoff, superuser restart) and the Rockbox release filters.
Once the switch goes off those choices must stop applying: the app is back on
the guided install with the platform's own flash method, and old builds,
nightlies, 240p and pre-3.0.7 releases are hidden again.
"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import device_tracking
from src.flash_service import default_flash_method
from src.ui.settings_page import SettingsPage


@pytest.fixture()
def settings_page():
    app = QApplication.instance() or QApplication(sys.argv)
    settings = QSettings("innioasis", "updater")
    saved = {
        key: settings.value(key)
        for key in (
            "flash_method",
            "advanced_settings",
            "run_as_admin",
        )
    }
    page = SettingsPage()
    page.show()
    app.processEvents()
    yield page
    page.close()
    app.processEvents()
    for key, value in saved.items():
        if value is None:
            settings.remove(key)
        else:
            settings.setValue(key, value)
    device_tracking.set_terminal_install_enabled(False)
    device_tracking.set_sp_gui_install_enabled(False)
    for flag in (
        device_tracking.FILTER_OLD_ROCKBOX,
        device_tracking.FILTER_NIGHTLY,
        device_tracking.FILTER_240P,
        device_tracking.FILTER_ORIGINAL_PRE_307,
    ):
        device_tracking.set_rockbox_release_filter(flag, False)


def test_switching_advanced_off_reverts_every_advanced_choice(settings_page):
    page = settings_page
    settings = QSettings("innioasis", "updater")

    # Choose the advanced options the revert has to undo.
    page.reveal_advanced_methods()
    device_tracking.set_terminal_install_enabled(True)
    device_tracking.set_sp_gui_install_enabled(False)
    page._sync_install_mode_switches()
    device_tracking.set_rockbox_release_filter(device_tracking.FILTER_NIGHTLY, True)
    device_tracking.set_rockbox_release_filter(device_tracking.FILTER_240P, True)
    page._apply_filter_flags()
    assert device_tracking.terminal_install_enabled()
    assert device_tracking.rockbox_release_filters().nightly

    # Advanced off: guided install, platform default method, no filters.
    page._cb_advanced_settings.setChecked(False)

    assert not device_tracking.terminal_install_enabled()
    assert not device_tracking.sp_gui_install_enabled()
    assert page._cb_guided_install.isChecked()

    flags = device_tracking.rockbox_release_filters()
    assert not flags.old_rockbox
    assert not flags.nightly
    assert not flags.rockbox_240p
    assert not flags.original_pre_307

    assert str(settings.value("flash_method", "")) == default_flash_method()

    from src import elevation

    assert not elevation.run_as_admin_enabled(settings)


def test_hiding_advanced_with_the_d_shortcut_also_reverts(settings_page):
    page = settings_page
    page.reveal_advanced_methods()
    device_tracking.set_rockbox_release_filter(device_tracking.FILTER_OLD_ROCKBOX, True)
    page._apply_filter_flags()

    page.hide_advanced_methods()

    assert not device_tracking.rockbox_release_filters().old_rockbox
    assert not device_tracking.advanced_settings_enabled()


def test_release_filters_are_an_advanced_choice(settings_page, monkeypatch):
    page = settings_page
    # Offline mode has no catalogue to filter, so it hides the card either
    # way; the gating under test is the advanced switch.
    monkeypatch.setattr("src.ui.settings_page.is_offline_mode", lambda: False)

    page.hide_advanced_methods()
    page._update_rockbox_visibility()
    assert page._rockbox_card.isHidden()

    page.reveal_advanced_methods()
    page._update_rockbox_visibility()
    assert not page._rockbox_card.isHidden()

    page.hide_advanced_methods()
    page._update_rockbox_visibility()
    assert page._rockbox_card.isHidden()
