"""Unit tests for double button dismissal on failure, subtitle persistence,

liquid glass scroll-lock / tracking, and legacy cleanup enhancements.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
from pathlib import Path
from PySide6.QtCore import QEvent, QPoint, QSettings, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QVBoxLayout, QWidget
import pytest

from src.ui.flash_page import FlashPage, _RoundNextButton
from src.ui.main_window import MainWindow
from src.ui.widgets import Card, BatteryToast, UpdateToast
import src.player_update_check as player_check
import src.legacy_cleanup as legacy_cleanup


@pytest.fixture(autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])
    yield app


def test_install_failure_dismisses_stop_prompt():
    """Cancellation flow buttons must be dismissed when install failure interrupts the run."""
    fp = FlashPage()
    fp.show_flashing()

    # Open the stop sanity check
    opened = fp.prompt_stop("Are you sure you want to cancel the installation?", wait=False)
    assert fp._stop_open is True
    assert fp._stop_actions is not None
    assert not fp._stop_actions.isHidden()

    # Now install fails while the prompt is open
    fp.show_install_failure("S_BROM_DOWNLOAD_FAIL", "Connection was interrupted")

    # Stop prompt must be gracefully dismissed
    assert fp._stop_open is False
    assert fp._stop_actions.isHidden() is True
    # Only retry and cancel buttons should be visible (2 buttons, not 4)
    assert not fp._flash_retry_btn.isHidden()
    assert not fp._cancel_btn.isHidden()
    fp.close()


def test_install_completion_dismisses_stop_prompt():
    """Cancellation flow buttons must be dismissed when install completes."""
    fp = FlashPage()
    fp.show_flashing()

    fp.prompt_stop("Stop install?", wait=False)
    assert fp._stop_open is True

    fp.set_device_done()
    assert fp._stop_open is False
    assert fp._stop_actions.isHidden() is True


def test_subtitle_never_reappears_after_dismissal(tmp_path):
    """'by Ryan Specter' subtitle stays hidden once donations/credits are disabled or dismissed."""
    settings_file = tmp_path / "test_settings.ini"
    settings = QSettings(str(settings_file), QSettings.Format.IniFormat)

    # 1. By default, not disabled
    assert player_check.is_donation_ui_disabled(settings) is False

    # 2. When settings donations section is dismissed
    player_check.set_settings_donations_section_dismissed(True, settings)
    assert player_check.is_donation_ui_disabled(settings) is True
    assert player_check.is_donation_install_prompt_disabled(settings) is True

    # Reset and test when donation install prompt is disabled (e.g. "Don't ask me again")
    settings.clear()
    assert player_check.is_donation_ui_disabled(settings) is False
    player_check.set_donation_install_prompt_disabled(True, settings)
    assert player_check.is_donation_ui_disabled(settings) is True

    # Test in MainWindow
    from unittest.mock import patch

    with patch("src.ui.select_page.ReleasesWorker"), patch("src.manifest.ManifestWorker"), patch("src.donors.fetch_remote_donors_async"):
        win = MainWindow()
        try:
            win.settings = settings
            win._update_author_label()
            assert win._version_label.isVisible() is False

            # Retranslate or theme change must keep it hidden
            win._retranslate_all()
            assert win._version_label.isVisible() is False
            win._apply_donation_visibility()
            assert win._version_label.isVisible() is False
        finally:
            win.close()
            app = QApplication.instance()
            if app:
                app.processEvents()


def test_card_confirmation_scroll_lock_and_trash_symbol():
    """Card confirmation locks scroll area and supports trash symbol."""
    scroll = QScrollArea()
    content = QWidget()
    layout = QVBoxLayout(content)
    card = Card("Test Card")
    layout.addWidget(card)
    scroll.setWidget(content)
    scroll.show()

    # Pre-condition: vertical scrollbar enabled
    assert scroll.verticalScrollBar().isEnabled() is True

    # When run_confirmation starts, scrollbar is disabled
    def on_prompt():
        assert scroll.verticalScrollBar().isEnabled() is False
        assert card._confirm_accept is not None
        assert card._confirm_accept._symbol == "trash"
        card._confirm_accept.click()

    from PySide6.QtCore import QTimer
    QTimer.singleShot(0, on_prompt)

    ok = card.run_confirmation("Delete old files?", "Delete", "Cancel", symbol="trash")
    assert ok is True
    # Post-condition: vertical scrollbar restored
    assert scroll.verticalScrollBar().isEnabled() is True


def test_battery_and_update_toast_buttons_are_round_chips():
    """Battery and Update toasts use _RoundNextButton without CSS override flattening."""
    bt = BatteryToast()
    assert isinstance(bt._dismiss_btn, _RoundNextButton)
    assert bt._dismiss_btn._symbol == "complete"

    ut = UpdateToast()
    assert isinstance(ut._install, _RoundNextButton)
    assert isinstance(ut._close, _RoundNextButton)
    assert ut._install._symbol == "download"
    assert ut._close._symbol == "cancel"


def test_windows_legacy_detection_and_removal(monkeypatch, tmp_path):
    """Windows %LocalAppData%/Innioasis Updater is detected and cleaned up."""
    local_app_data = tmp_path / "AppData" / "Local"
    legacy_dir = local_app_data / "Innioasis Updater"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "old_firmware.bin").write_bytes(b"\x00" * 2048)

    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("INNIOASIS_SIMULATE_WINDOWS", "1")
    monkeypatch.setattr(sys, "platform", "win32")

    info = legacy_cleanup.detect_legacy_installations()
    assert info["has_legacy"] is True
    assert legacy_dir in info["windows_legacy_dirs"]

    ok, removed, errors = legacy_cleanup.remove_windows_legacy()
    assert ok is True
    assert not legacy_dir.exists()
