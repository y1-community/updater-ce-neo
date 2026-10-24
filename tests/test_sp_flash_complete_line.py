"""SP Flash Tool's completion line must not read as a USB disconnect."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

from src.flash_service import (
    is_disconnection_error,
    is_sp_flash_complete_line,
    is_sp_usb_search_line,
)

COMPLETE_LINE = "Flash complete! Disconnect USB and reboot the device."


def test_completion_line_is_recognised():
    assert is_sp_flash_complete_line(COMPLETE_LINE)


def test_completion_line_is_not_a_disconnect():
    assert not is_disconnection_error(COMPLETE_LINE)


def test_completion_line_is_not_a_usb_search():
    assert not is_sp_usb_search_line(COMPLETE_LINE)


def test_ordinary_progress_lines_are_not_completion():
    assert not is_sp_flash_complete_line("Download Ok")
    assert not is_sp_flash_complete_line("Search USB Port ...")


def test_real_disconnect_still_detected():
    assert is_disconnection_error("device disconnected")
