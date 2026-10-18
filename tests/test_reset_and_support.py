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
    page = SupportPage()
    assert hasattr(page, "_appeal_body")
    assert "family" in page._appeal_body.text().lower()

    # Test primary payment methods are created (Ko-fi, PayPal, Revolut, Patreon)
    assert len(page._method_buttons) == 4

    # Test discrete honeygain button exists
    assert hasattr(page, "_honeygain_link")

    # Test crypto drawer toggle and copy function
    assert page._crypto_drawer.isHidden()
    page._toggle_crypto_drawer()
    assert not page._crypto_drawer.isHidden()

    btn = page._crypto_drawer.findChildren(QPushButton)[0]
    page._copy_crypto("test_addr", btn)
    clip = QApplication.clipboard()
    assert clip.text() == "test_addr"


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

