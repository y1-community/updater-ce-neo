import os
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


def test_get_appeal_messages():
    msgs = get_appeal_messages()
    assert len(msgs) >= 3
    # Verify personal human & family appeals are included
    full_text = " ".join(msgs)
    assert "family" in full_text.lower() or "free" in full_text.lower()


def test_donation_status_bar_goal_suppression(qapp):
    # When raised == 0, goal bar should be suppressed (hidden)
    bar = DonationStatusBar(donations=[])
    bar._refresh_goal()
    assert bar._goal_suppressed is True
    assert not bar._goal_bar.isVisible()
    assert not bar._goal_label.isVisible()
    assert not bar._donor_label.isHidden()

    # When raised is 50 (< 200), goal bar is visible
    donations_mid = [{"amount": 50.0, "name": "Alice", "dt": None}]
    from datetime import datetime
    donations_mid[0]["dt"] = datetime.now()
    bar2 = DonationStatusBar(donations=donations_mid)
    bar2._refresh_goal()
    assert bar2._goal_suppressed is False
    assert not bar2._goal_bar.isHidden()
    assert not bar2._goal_label.isHidden()

    # When raised is 250 (>= 200), goal bar is suppressed
    donations_high = [{"amount": 250.0, "name": "Bob", "dt": datetime.now()}]
    bar3 = DonationStatusBar(donations=donations_high)
    bar3._refresh_goal()
    assert bar3._goal_suppressed is True
    assert not bar3._goal_bar.isVisible()


def test_support_page_narratives_and_methods(qapp):
    from src.i18n import translator, tr

    page = SupportPage()
    assert hasattr(page, "_appeal_body")
    assert "family" in page._appeal_body.text().lower()
    old_language = translator().lang
    try:
        for language in ("zh-CN", "fr", "es", "de", "ja", "it"):
            translator().set_language(language)
            page.retranslate()
            assert tr("donate_support_appeal_lead") in page._appeal_body.text()
            assert tr("donate_support_appeal_followup") in page._appeal_body.text()
    finally:
        translator().set_language(old_language)
        page.retranslate()

    # Test primary payment methods are created (Ko-fi, PayPal, Revolut, Patreon)
    assert len(page._method_buttons) == 4
    assert hasattr(page, "_honeygain_link")
    assert page._crypto_drawer.isHidden()
    page._toggle_crypto_drawer()
    assert not page._crypto_drawer.isHidden()

    btn = page._crypto_drawer.findChildren(QPushButton)[0]
    page._copy_crypto("test_addr", btn)
    assert QApplication.clipboard().text() == "test_addr"


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
            "https://reddit.com/r/innioasismodders",
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

        window._apply_install_ui_state(True)
        assert window._discord_btn.isHidden()
        assert window._reddit_btn.isHidden()
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
    assert not fp._paperclip_hint_host.isHidden()


def test_reset_on_r_restores_battery_reminder(qapp):
    s = QSettings("innioasis", "updater")
    s.setValue("battery_hint_dismissed", True)
    assert s.value("battery_hint_dismissed", False, type=bool) is True

    win = MainWindow()
    try:
        win._select_page._check_battery_toast_visibility()
        assert win._select_page._battery_toast.isHidden()

        # Trigger reset
        win.reset_all_configuration_and_caches()

        # After reset, battery_hint_dismissed must be False/cleared and toast visible
        assert s.value("battery_hint_dismissed", False, type=bool) is False
        assert not win._select_page._battery_toast.isHidden()
        assert not win._select_page._toast_container.isHidden()
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

