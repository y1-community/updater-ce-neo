"""Tests for SP Flash Tool USB search lines, BROM connection, and detect timing.

Verifies:
- "Connecting to BROM..." does NOT trigger STEP_DETECT (12%) or leave USB search.
- "Search usb, timeout set as 120000 ms" and "Scanning USB port..." trigger STEP_WAITING.
- "BROM connected" triggers STEP_DETECT (12%).
"""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

from PySide6.QtWidgets import QApplication
from src.flash_service import (
    FlashWorker,
    STEP_WAITING,
    STEP_DETECT,
    STEP_WRITE,
    is_sp_usb_search_line,
)


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_is_sp_usb_search_line_matches():
    assert is_sp_usb_search_line("Search usb, timeout set as 120000 ms")
    assert is_sp_usb_search_line("[SP] Search usb, timeout set as 120000 ms")
    assert is_sp_usb_search_line("[SP] Scanning USB port...")
    assert is_sp_usb_search_line("Scanning USB port...")
    assert is_sp_usb_search_line("Search USB Port ...")
    assert not is_sp_usb_search_line("Connecting to BROM...")
    assert not is_sp_usb_search_line("BROM connected")


def test_connecting_to_brom_does_not_declare_detect():
    """SP Flash Tool's 'Connecting to BROM...' must not declare 12% detect."""
    _app()
    worker = FlashWorker("dummy.zip", method="sp", model="Y1")

    steps = []
    progress_values = []
    worker.step_changed.connect(steps.append)
    worker.progress.connect(progress_values.append)

    # 1. SP Flash Tool startup output before device is connected:
    worker._classify_sp_stdout("[SP] Connecting to BROM...")

    assert STEP_DETECT not in steps, f"Connecting to BROM prematurely triggered STEP_DETECT: {steps}"
    assert 12 not in progress_values, f"Premature 12% progress emitted: {progress_values}"
    assert not getattr(worker, "_device_seen", False), "_device_seen was prematurely set to True"
    assert not getattr(worker, "_past_usb_search", False), "_past_usb_search was prematurely set to True"

    # 2. Tool begins searching USB:
    worker._classify_sp_stdout("[SP] Search usb, timeout set as 120000 ms")
    assert STEP_WAITING in steps, f"Expected STEP_WAITING on search line, got {steps}"
    assert getattr(worker, "_usb_search_active", False) is True

    # 3. User plugs in player: BROM connects
    worker._classify_sp_stdout("[SP] BROM connected")
    assert STEP_DETECT in steps, f"Expected STEP_DETECT on 'BROM connected', got {steps}"
    assert 12 in progress_values, f"Expected 12% progress on 'BROM connected', got {progress_values}"
    assert getattr(worker, "_device_seen", False) is True
    assert getattr(worker, "_past_usb_search", False) is True
