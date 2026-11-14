import os
import re

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QPushButton

from src.donation_dialog import DonationStatusBar, DonationDialog, get_appeal_messages
from src.ui.main_window import MainWindow, _PAGE_SELECT, _PAGE_SUPPORT
from src.ui.support_page import SupportPage
from src.ui.flash_page import FlashPage


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def english_default_ui():
    """Keep this file's language state from leaking into the next test."""
    from src.i18n import translator

    old_language = translator().lang
    translator().set_language("en")
    try:
        yield
    finally:
        translator().set_language(old_language)


@pytest.fixture
def russian_ui():
    """Run one test with the app's chosen language set to Russian."""
    from src.i18n import translator

    old_language = translator().lang
    translator().set_language("ru")
    try:
        yield
    finally:
        translator().set_language(old_language)


def test_get_appeal_messages():
    msgs = get_appeal_messages()
    assert len(msgs) >= 3
    # Verify personal human & family appeals are included
    full_text = " ".join(msgs)
    assert "family" in full_text.lower() or "free" in full_text.lower()


def test_donation_status_bar_goal_suppression(qapp, monkeypatch):
    from datetime import datetime

    import src.donation_dialog as dialog
    from src.donors import get_goal_stats

    now = datetime(2026, 11, 5, 12)
    zero = get_goal_stats([], now=now)
    mid = get_goal_stats(
        [{"amount": 50.0, "name": "Alice", "dt": now}],
        now=now,
    )
    high = get_goal_stats(
        [{"amount": 250.0, "name": "Bob", "dt": now}], now=now
    )

    def stats_for(donations):
        if not donations:
            return zero
        if float(donations[0].get("amount") or 0) >= 200:
            return high
        return mid

    monkeypatch.setattr(dialog, "get_goal_stats", stats_for)
    monkeypatch.setattr(dialog, "_resolve_bar_state", lambda widget: (0, 0, 1, 1, True))

    # A quiet day names today's smallest ask instead of hiding the goal.
    bar = DonationStatusBar(donations=[])
    bar._refresh_goal()
    assert bar._goal_suppressed is False
    assert not bar._goal_bar.isHidden()
    assert not bar._goal_label.isHidden()
    assert "0" in bar._goal_label.text()
    assert "today" in bar._goal_label.text()

    # When raised is 50 (< 200), goal bar is visible.
    bar2 = DonationStatusBar(donations=[{"amount": 50.0, "name": "Alice", "dt": now}])
    bar2._refresh_goal()
    assert bar2._goal_suppressed is False
    assert not bar2._goal_bar.isHidden()
    assert not bar2._goal_label.isHidden()

    # When raised is 250 (>= 200), the month and the last seven days are both
    # covered: no bar, and the running-costs sentence takes its place.
    bar3 = DonationStatusBar(donations=[{"amount": 250.0, "name": "Bob", "dt": now}])
    bar3._refresh_goal()
    assert bar3._goal_suppressed is False
    assert bar3._goal_bar.isHidden()
    assert not bar3._goal_label.isHidden()
    assert "costs" in bar3._goal_label.text()


def test_support_page_narratives_and_methods(qapp, monkeypatch):
    from src.i18n import translator, tr

    # The method row depends on the host's region, so pin it: this asserts the
    # worldwide row even on a Russian machine.
    monkeypatch.setenv("UPDATER_REGION", "US")
    page = SupportPage()
    assert hasattr(page, "_appeal_body")
    assert "family" in page._appeal_body.text().lower()
    old_language = translator().lang
    try:
        for language in ("zh-CN", "fr", "es", "de", "ja", "it"):
            translator().set_language(language)
            page.retranslate()
            # The first sentence is wrapped in <b>, so compare the text without
            # its markup.
            body = re.sub(r"<[^>]+>", "", page._appeal_body.text())
            assert tr("donate_support_appeal_full") in body
    finally:
        translator().set_language(old_language)
        page.retranslate()

    # Test primary payment methods are created
    # (Ko-fi, Buy Me a Coffee, PayPal, Revolut, Patreon)
    assert len(page._method_buttons) == 5
    assert hasattr(page, "_honeygain_link")
    assert page._crypto_drawer.isHidden()
    page._toggle_crypto_drawer()
    assert not page._crypto_drawer.isHidden()

    btn = page._crypto_drawer.findChildren(QPushButton)[0]
    page._copy_crypto("test_addr", btn)
    assert QApplication.clipboard().text() == "test_addr"


def test_russia_is_offered_boosty_and_nothing_else(qapp, monkeypatch, russian_ui):
    """Russian copy in Russia swaps the coffee pages for Boosty everywhere."""
    from src import fx
    from src.config import DONATION_LINKS
    from src.i18n import tr
    from src.ui.settings_page import SettingsPage

    monkeypatch.setenv("UPDATER_REGION", "RU")
    assert fx.territory() == "RU"
    assert fx.donation_route() == "boosty"
    assert fx.routed() == "boosty"
    assert DONATION_LINKS["boosty"].endswith("boosty.to/ryanspecter")

    page = SupportPage()
    try:
        assert [key for _btn, key in page._method_buttons] == ["donate_boosty"]
        assert page._method_buttons[0][0].text() == tr("donate_boosty")
        assert page._honeygain_link.isHidden()
        assert page._crypto_toggle_link.isHidden()
    finally:
        page.close()
        page.deleteLater()

    settings = SettingsPage()
    try:
        assert settings._settings_coffee_btn.text() == tr("donate_boosty")
        assert settings._settings_bmc_btn.isHidden()
    finally:
        settings.close()
        settings.deleteLater()

    dialog = DonationDialog(donations=[])
    try:
        assert [btn.text() for btn in dialog._pay_buttons] == [tr("donate_boosty")]
    finally:
        dialog.close()
        dialog.deleteLater()

    fp = FlashPage()
    try:
        fp.set_post_install_urls("https://example.com/guide", "https://example.com/donate")
        # The manifest's donation page is not offered, and the coffee button
        # opens Boosty instead of it.
        assert fp._post_install_url_2 == ""
        assert fp._coffee_btn.text() == tr("donate_boosty")
        assert fp._bmc_btn.isHidden()
    finally:
        fp.close()
        fp.deleteLater()

    monkeypatch.delenv("UPDATER_REGION")
    assert fx.routed() == "boosty"
    qapp.processEvents()


def test_russian_region_gets_boosty_and_rubles(qapp, monkeypatch):
    """The Russian market gets Boosty alone and a rouble goal, even in English."""
    from src import fx
    from src.i18n import tr

    monkeypatch.setenv("UPDATER_REGION", "RU")
    monkeypatch.delenv("UPDATER_CURRENCY", raising=False)
    assert fx.territory() == "RU"
    assert fx.donation_route() == "boosty"
    assert fx.pair()[1] == "RUB"

    page = SupportPage()
    try:
        assert [key for _btn, key in page._method_buttons] == ["donate_boosty"]
        assert page._method_buttons[0][0].text() == tr("donate_boosty")
    finally:
        page.close()
        page.deleteLater()

    dialog = DonationDialog(donations=[])
    try:
        assert [btn.text() for btn in dialog._pay_buttons] == [tr("donate_boosty")]
    finally:
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


def test_explicit_currency_wins_over_a_russian_ui(monkeypatch, russian_ui):
    from src import fx

    monkeypatch.setenv("UPDATER_REGION", "GB")
    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")
    assert fx.pair()[1] == "GBP"


def test_russian_ui_outside_russia_still_gets_boosty(
    qapp, monkeypatch, russian_ui
):
    """A Russian interface is the Russian market even on a non-Russian machine."""
    from src import fx
    from src.config import DONATION_LINKS
    from src.i18n import tr
    from src.ui.settings_page import SettingsPage

    monkeypatch.setenv("UPDATER_REGION", "GB")
    monkeypatch.delenv("UPDATER_CURRENCY", raising=False)
    assert fx.donation_route() == "boosty"
    assert fx.pair()[1] == "RUB"
    assert DONATION_LINKS["boosty"].endswith("boosty.to/ryanspecter")

    page = SupportPage()
    try:
        assert [key for _btn, key in page._method_buttons] == ["donate_boosty"]
        assert page._method_buttons[0][0].text() == tr("donate_boosty")
        assert page._honeygain_link.isHidden()
        assert page._crypto_toggle_link.isHidden()
    finally:
        page.close()
        page.deleteLater()

    settings = SettingsPage()
    try:
        settings.retranslate()
        assert settings._settings_coffee_btn.text() == tr("donate_boosty")
        assert settings._settings_bmc_btn.isHidden()
    finally:
        settings.close()
        settings.deleteLater()

    dialog = DonationDialog(donations=[])
    try:
        assert [btn.text() for btn in dialog._pay_buttons] == [tr("donate_boosty")]
    finally:
        dialog.close()
        dialog.deleteLater()

    fp = FlashPage()
    try:
        fp.set_post_install_urls("https://example.com/guide", "https://example.com/donate")
        assert fp._post_install_url_2 == ""
        assert fp._coffee_btn.text() == tr("donate_boosty")
        assert fp._bmc_btn.isHidden()
    finally:
        fp.close()
        fp.deleteLater()
    qapp.processEvents()


def test_settings_card_restores_the_second_coffee_page_when_english_returns(
    qapp, monkeypatch
):
    """Leaving Russian must undo the Boosty/BMC substitution in Settings."""
    from src.i18n import translator, tr
    from src.ui.settings_page import SettingsPage

    monkeypatch.setenv("UPDATER_REGION", "GB")
    translator().set_language("en")
    page = SettingsPage()
    try:
        assert page._settings_coffee_btn.text() == tr("donate_coffee_btn")
        assert not page._settings_bmc_btn.isHidden()

        translator().set_language("ru")
        page.retranslate()
        qapp.processEvents()
        assert page._settings_coffee_btn.text() == tr("donate_boosty")
        assert page._settings_bmc_btn.isHidden()

        translator().set_language("en")
        page.retranslate()
        qapp.processEvents()
        assert page._settings_coffee_btn.text() == tr("donate_coffee_btn")
        assert not page._settings_bmc_btn.isHidden()
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_completion_screen_follows_a_language_switch_mid_run(qapp, monkeypatch):
    """Choosing Russian during a download must re-route the completion card."""
    from src.i18n import translator, tr
    from src.ui.flash_page import FlashPage

    monkeypatch.setenv("UPDATER_REGION", "GB")
    page = FlashPage()
    try:
        page.set_post_install_urls(
            "https://example.com/guide", "https://example.com/donate"
        )
        assert page._post_install_url_2 == "https://example.com/donate"

        translator().set_language("ru")
        page.retranslate()
        qapp.processEvents()
        assert page._post_install_url_2 == ""
        assert page._coffee_btn.text() == tr("donate_boosty")
        assert page._bmc_btn.isHidden()

        translator().set_language("en")
        page.retranslate()
        qapp.processEvents()
        assert page._post_install_url_2 == "https://example.com/donate"
        assert page._coffee_btn.text() == tr("donate_completion_coffee_guide")
        assert not page._bmc_btn.isHidden()
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_support_page_switches_route_when_the_language_changes(qapp, monkeypatch):
    """The method row follows a language chosen after the page was built."""
    from src.i18n import translator

    monkeypatch.setenv("UPDATER_REGION", "GB")
    page = SupportPage()
    try:
        assert len(page._method_buttons) == 5
        translator().set_language("ru")
        page.retranslate()
        qapp.processEvents()
        assert [key for _btn, key in page._method_buttons] == ["donate_boosty"]
        translator().set_language("en")
        page.retranslate()
        qapp.processEvents()
        assert len(page._method_buttons) == 5
    finally:
        page.close()
        page.deleteLater()
        qapp.processEvents()


def test_donation_appeals_and_community_labels_have_all_catalog_translations():
    import json
    from pathlib import Path

    from src.i18n import _STRINGS

    keys = (
        "donate_appeal_costs_for_year",
        "donate_appeal_family_and_time",
        "donate_appeal_human_touch",
        "donate_appeal_keep_going",
        "donate_appeal_always_free",
        "donate_appeal_community_archive",
        "donate_appeal_what_innioasis_dont",
        "donate_support_appeal_lead",
        "donate_support_appeal_followup",
        "community_discord",
        "community_reddit",
    )
    catalog_dir = Path(__file__).resolve().parent.parent / "assets" / "i18n"
    catalogs = [path for path in catalog_dir.glob("*.json") if path.name != "languages.json"]
    assert catalogs
    for path in catalogs:
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in keys:
            assert data.get(key, "").strip(), f"{path.stem} missing {key}"
            assert data[key] != key, f"{path.stem} has untranslated {key}"
    for key in keys:
        assert all(_STRINGS[key].get(code) for code in ("en", "zh-CN", "fr", "es", "de", "ja"))


def test_dismissing_donations_does_not_make_settings_window_wider(qapp):
    from src import device_tracking

    settings = QSettings("innioasis", "updater")
    old_dismissed = device_tracking.is_settings_donations_section_dismissed(settings)
    old_disabled = device_tracking.is_donation_ui_disabled(settings)
    old_prompt_disabled = device_tracking.is_donation_install_prompt_disabled(settings)
    device_tracking.set_settings_donations_section_dismissed(False, settings)
    device_tracking.set_donation_ui_disabled(False, settings)
    device_tracking.set_donation_install_prompt_disabled(False, settings)
    window = MainWindow()
    try:
        window._nav_to_page(4)
        window.show()
        qapp.processEvents()
        page = window._settings_page
        assert page._donations_card.isVisible()
        available_width = window.screen().availableGeometry().width()
        window.resize(min(790, available_width), window.height())
        qapp.processEvents()
        width_before = window.width()
        assert width_before > 640
        page._cb_hide_donations.setChecked(False)
        qapp.processEvents()

        assert not page._dismiss_donations_link.isHidden()
        assert page._dismiss_donations_link.wordWrap()
        assert page._donations_actions.minimumSizeHint().width() < width_before
        assert window.width() == width_before
        assert window._settings_btn.isVisible()

        page._on_dismiss_donations_section()
        qapp.processEvents()
        assert page._donations_card.isHidden()
        assert window.width() == width_before
        assert window._settings_btn.isVisible()
    finally:
        device_tracking.set_settings_donations_section_dismissed(old_dismissed, settings)
        device_tracking.set_donation_ui_disabled(old_disabled, settings)
        device_tracking.set_donation_install_prompt_disabled(old_prompt_disabled, settings)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_community_buttons_open_their_links_and_follow_visibility(qapp, monkeypatch):
    from src import device_tracking
    from src.config import DONATION_LINKS
    from src.ui.main_window import MainWindow

    old_disabled = device_tracking.is_donation_ui_disabled()
    old_prompt_disabled = device_tracking.is_donation_install_prompt_disabled()
    device_tracking.set_donation_ui_disabled(False)
    device_tracking.set_donation_install_prompt_disabled(False)
    window = MainWindow()
    opened = []
    monkeypatch.setattr("src.browser.open_browser", lambda url: opened.append(url))
    try:
        window._discord_btn.click()
        window._reddit_btn.click()
        assert opened == [
            "https://discord.gg/zHrT2zrcek",
            "https://reddit.com/r/innioasis",
        ]
        assert DONATION_LINKS["discord"] == opened[0]
        assert DONATION_LINKS["reddit"] == opened[1]
        assert not window._discord_btn.icon().isNull()
        assert not window._reddit_btn.icon().isNull()
        # The community marks match the sidebar's own 16px glyphs.
        assert window._discord_btn.iconSize().width() == 16
        assert window._reddit_btn.iconSize().width() == 16
        assert window._github_btn.iconSize().width() == 16

        window._apply_donation_visibility(True)
        assert window._discord_btn.isHidden()
        assert window._reddit_btn.isHidden()
        window._apply_donation_visibility(False)
        assert not window._discord_btn.isHidden()
        assert not window._reddit_btn.isHidden()

        # Mid-install the marks follow the Donations setting, so help stays
        # reachable while a run is in progress.
        window._apply_install_ui_state(True)
        assert not window._discord_btn.isHidden()
        assert not window._reddit_btn.isHidden()
        assert not window._github_btn.isHidden()
        window._apply_donation_visibility(True)
        assert window._discord_btn.isHidden()
        assert window._github_btn.isHidden()
        window._apply_donation_visibility(False)
        window._apply_install_ui_state(False)
        assert not window._discord_btn.isHidden()
        assert not window._reddit_btn.isHidden()
    finally:
        device_tracking.set_donation_ui_disabled(old_disabled)
        device_tracking.set_donation_install_prompt_disabled(old_prompt_disabled)
        window.close()
        qapp.processEvents()


def test_flash_page_paperclip_tip_inside_card_removed(qapp):
    fp = FlashPage()
    fp.set_model("Y1")
    fp.show_power_off_prompt()
    # Inside-card mode hint must be hidden in favour of the beneath-card tip
    assert fp._wait_mode_hint.isHidden()
    fp._on_paperclip_help()
    assert not fp._paperclip_hint_host.isHidden()


def test_reset_on_r_keeps_a_dismissed_battery_reminder_away(qapp):
    """Closing the toast is forever: resetting settings must not bring it back."""
    s = QSettings("innioasis", "updater")
    s.setValue("battery_hint_dismissed", True)
    assert s.value("battery_hint_dismissed", False, type=bool) is True

    win = MainWindow()
    try:
        win._select_page._check_battery_toast_visibility()
        assert win._select_page._battery_toast.isHidden()

        win.reset_all_configuration_and_caches()

        assert s.value("battery_hint_dismissed", False, type=bool) is True
        assert win._select_page._battery_toast.isHidden()
        assert win._select_page._toast_container.isHidden()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_reset_brings_dismissed_install_hints_back(qapp, monkeypatch, tmp_path):
    """R is how a user asks for the How? tip and the keep-data note again."""
    from src import catalog, downloads, manifest

    # Keep the reset away from the developer's real caches.
    monkeypatch.setattr(downloads, "clear_firmware_cache", lambda: None)
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path / "downloads")
    monkeypatch.setattr(catalog, "_app_cache_dir", lambda: tmp_path / "app_cache")
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path / "manifest_cache")

    s = QSettings("innioasis", "updater")
    for name in ("paperclip", "microsd", "keep_data_warning"):
        s.setValue(f"dismissed_install_hints/{name}", True)

    win = MainWindow()
    try:
        win._flash_page.set_model("Y1")
        win._flash_page.show_power_off_prompt()
        qapp.processEvents()
        assert not win._flash_page._paperclip_help_wanted()

        win.reset_all_configuration_and_caches()
        qapp.processEvents()

        for name in ("paperclip", "microsd", "keep_data_warning"):
            assert (
                s.value(f"dismissed_install_hints/{name}", False, type=bool) is False
            ), name
        assert win._flash_page._paperclip_help_wanted()
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()


def test_donation_status_bar_rotation_and_centering(qapp):
    from datetime import datetime
    from src.ui.main_window import compact_install_height, COMPACT_INSTALL_MIN_HEIGHT, COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS

    donations = [{"amount": 25.0, "name": "Charlie", "dt": datetime.now()}]
    bar = DonationStatusBar(donations=donations)
    bar.show()
    qapp.processEvents()

    # Initial state: showing goal
    assert bar._showing_goal is True
    assert bar._goal_bar.isVisible()
    # The goal line sits flush in the right corner (no mirror spacer on the right).
    assert bar._goal_group.x() + bar._goal_group.width() >= bar.width() - 16

    # Rotate to donor line: goal bar must be hidden so no space is wasted
    bar._rotate()
    assert bar._showing_goal is False
    assert not bar._goal_bar.isVisible()
    assert bar._donor_label.isVisible()

    # Rotate back to goal line
    bar._rotate()
    assert bar._showing_goal is True
    assert bar._goal_bar.isVisible()

    # Verify height expands and contracts
    base = dict(page_h=120, prompt_h=0, status_h=28, inline_header=True)
    h_with = compact_install_height(donations_disabled=False, **base)
    h_without = compact_install_height(donations_disabled=True, **base)
    assert h_with > h_without
    assert h_without == COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS
    bar.close()
    bar.deleteLater()
    qapp.processEvents()


def test_windows_legacy_shortcut_cleanup_never_deletes_user_files(tmp_path, monkeypatch):
    from src import legacy_cleanup

    fake_home = tmp_path / "User"
    desktop = fake_home / "Desktop"
    start_menu = fake_home / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    desktop.mkdir(parents=True)
    start_menu.mkdir(parents=True)

    # 1. Shortcuts that MUST be removed (.lnk / .url containing Innioasis or SP Flash)
    old_app_lnk = desktop / "Innioasis Updater.lnk"
    old_sp_lnk = desktop / "SP Flash Tool.lnk"
    old_app_lnk.write_text("shortcut")
    old_sp_lnk.write_text("shortcut")

    menu_folder = start_menu / "Innioasis Updater"
    menu_folder.mkdir()
    (menu_folder / "Innioasis Updater.lnk").write_text("shortcut")
    (menu_folder / "SP Flash Tool.lnk").write_text("shortcut")

    # 2. User files that MUST NEVER be deleted even if named Innioasis / SP Flash
    user_zip = desktop / "Innioasis Y1 Backup.zip"
    user_notes = desktop / "SP Flash notes.txt"
    user_folder = desktop / "My Innioasis Music"
    user_folder.mkdir()
    user_song = user_folder / "song.mp3"
    user_zip.write_text("user zip data")
    user_notes.write_text("user notes")
    user_song.write_text("audio")

    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("APPDATA", str(fake_home / "AppData" / "Roaming"))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "ProgramData"))
    monkeypatch.setenv("PUBLIC", str(tmp_path / "Public"))

    removed = legacy_cleanup._clean_windows_legacy_shortcuts()
    assert len(removed) >= 5
    assert not old_app_lnk.exists()
    assert not old_sp_lnk.exists()
    assert not menu_folder.exists()

    # Non-shortcut user files and folders with user files are completely untouched
    assert user_zip.exists()
    assert user_notes.exists()
    assert user_folder.exists()
    assert user_song.exists()


def test_install_donations_link_opens_support_as_a_modal(qapp):
    """The lower donations text during an install must not leave the install.

    It opens Support Us in a dialog. SupportPage does not take a donations
    argument; passing one used to raise TypeError and the click did nothing.
    """
    from PySide6.QtWidgets import QWidget

    host = QWidget()
    host._donations = {"shown": True}
    host._support_modal = None
    try:
        MainWindow._open_support_modal(host)
        qapp.processEvents()
        dialog = host._support_modal
        assert dialog is not None
        assert dialog.isVisible()
        assert dialog.windowTitle()
        page = dialog.findChild(SupportPage)
        assert page is not None
        assert page.content_height_hint(536) > 0
    finally:
        dialog = getattr(host, "_support_modal", None)
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
        host.deleteLater()
        qapp.processEvents()


