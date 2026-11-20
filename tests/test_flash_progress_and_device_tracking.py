"""Tests for flash write progress display capping, inactive stage suppression,
and tracked software updates on user answers.
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src import player_update_check as puc
from src.ui.flash_page import FlashPage
from src.themes_journey import ThemesJourney


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_flash_progress_over_100_clamps_value_and_hides_percentage(qapp):
    """Progress over 100% clamps to 100 and stops displaying percentage."""
    journey = ThemesJourney()
    page = FlashPage(journey=journey)

    page.update_progress(50)
    assert page._progress_bar.value() == 50
    assert page._progress_bar.isTextVisible() is True
    assert page._progress_bar.format() == "%p%"

    # Progress over 100% (e.g. 105%)
    page.update_progress(105)
    assert page._progress_bar.value() == 100
    assert page._progress_bar.isTextVisible() is False
    assert page._progress_bar.format() == ""

    # Once exceeded, subsequent progress calls stay hidden for that write activity
    page.update_progress(100)
    assert page._progress_bar.value() == 100
    assert page._progress_bar.isTextVisible() is False


def test_flash_progress_100_for_two_seconds_hides_percentage(qapp, monkeypatch):
    """When progress sits at 100% for 2+ seconds, percentage text is hidden."""
    journey = ThemesJourney()
    page = FlashPage(journey=journey)

    sim_time = 1000.0
    monkeypatch.setattr(time, "monotonic", lambda: sim_time)

    page.update_progress(100)
    assert page._progress_bar.value() == 100
    assert page._progress_bar.isTextVisible() is True

    # 1.5 seconds later (under 2 seconds) -> still visible
    sim_time = 1001.5
    page.update_progress(100)
    assert page._progress_bar.isTextVisible() is True

    # 2.1 seconds later (>= 2 seconds) -> hidden
    sim_time = 1002.1
    page.update_progress(100)
    assert page._progress_bar.isTextVisible() is False
    assert page._progress_bar.format() == ""

    # Resetting write restores visibility
    page._reset_write_progress()
    assert page._progress_bar.isTextVisible() is True
    assert page._progress_bar.format() == "%p%"


def test_write_progress_suppressed_during_cleanup_and_themes(qapp):
    """Write progress must not update when cleanup stage or themes is active."""
    journey = ThemesJourney()
    page = FlashPage(journey=journey)

    page.update_progress(40)
    assert page._progress_bar.value() == 40

    # Activate themes journey
    journey.begin("Y1", automatic=True, purpose="themes")
    assert journey.active is True

    # Attempt to send write progress while themes is active
    page.update_progress(80)
    assert page._progress_bar.value() == 40  # Must not change

    # Also check update_time does not overwrite eta when themes is active
    page.update_time("01:00", "00:30")
    # Verify eta was not updated to 00:30
    assert page._elapsed_row.value() != "01:00"

    journey.finish()
    assert journey.active is False

    # Now write progress works again
    page.update_progress(60)
    assert page._progress_bar.value() == 60


def test_device_tracking_record_and_chronological_update(tmp_path):
    """Tracked software with Unknown version compares chronologically for updates."""
    ini_path = tmp_path / "test_settings.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)

    # Record software with Unknown version (as when user answers questions)
    puc.record_device_install(
        model="Y1",
        software_name="Rockbox",
        tag_name="Unknown",
        settings=settings,
        hardware_type="type_a",
        manual=True,
    )

    rec = puc.get_device_install("Y1", hardware_type="type_a", settings=settings)
    assert rec is not None
    assert rec["software_name"] == "Rockbox"
    assert rec["tag_name"] == "Unknown"

    # update_notification_due allows Unknown tag through
    assert puc.update_notification_due("Unknown", "v1.2.0") is True

    # Set published_at in newer_rel to be in the future (after installed_at)
    older_rel = {"tag_name": "v1.0.0", "published_at": "2020-01-01T12:00:00"}
    newer_rel = {"tag_name": "v1.1.0", "published_at": "2099-01-01T12:00:00"}

    # Mock catalog packages and releases client
    class MockPkg:
        name = "Rockbox"
        slug = "rockbox"

    class MockClient:
        def releases_for_package(self, pkg, model, show_nightly=False, force_refresh=False):
            return [newer_rel, older_rel]

    from unittest.mock import patch
    with patch("src.catalog.packages_for_model", return_value=[MockPkg()]):
        updates = puc.check_device_updates(
            releases_client=MockClient(),
            settings=settings,
            ignore_last_notified=True,
        )
        assert len(updates) == 1
        assert updates[0]["latest_tag"] == "v1.1.0"


def test_question_answer_records_tracked_software_when_untracked(qapp, tmp_path):
    """Answering a question about running software records it if not tracked before."""
    from unittest.mock import MagicMock
    from src.ui.main_window import MainWindow

    ini_path = tmp_path / "test_main_settings.ini"
    settings = QSettings(str(ini_path), QSettings.Format.IniFormat)

    # Use a mock window
    win = MagicMock(spec=MainWindow)
    win.settings = settings
    win._keep_data_tracking_context = lambda: ("Y1", "type_a")
    win._keep_data_confirm_known_name = ""

    # Call MainWindow._remember_keep_data_answer with a chosen firmware
    MainWindow._remember_keep_data_answer(win, "Rockbox")

    rec = puc.get_device_install("Y1", hardware_type="type_a", settings=settings)
    assert rec is not None
    assert rec["software_name"] == "Rockbox"
    assert rec["tag_name"] == "Unknown"

    # Next, if another firmware is already tracked, answering should NOT overwrite if multiple tracking is on
    puc.set_track_multiple_software_enabled(True, settings=settings)
    win._keep_data_tracking_context = lambda: ("Y1", "type_a")
    MainWindow._remember_keep_data_answer(win, "Original Software")
    # Should not have overwritten the install record to Original Software
    rec_after = puc.get_device_install("Y1", hardware_type="type_a", settings=settings)
    assert rec_after["software_name"] == "Rockbox"

