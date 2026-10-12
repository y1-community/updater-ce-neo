"""Verify the power-off prompt wording, Next button tooltips, and worker retention."""

import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.i18n import tr
from src.ui.flash_page import FlashPage, _RoundNextButton
from src.flash_service import FlashService, FlashWorker, DeviceMonitor


def test_next_button_tooltips_and_power_off_prompt():
    app = QApplication.instance() or QApplication(sys.argv)
    page = FlashPage()
    page.set_model("Y1")

    # Tooltip must be "Next", never "Continue"
    assert page._wait_continue_btn.toolTip() == "Next"
    assert page._wait_continue_btn.accessibleName() == "Next"
    assert page._wait_continue_btn.toolTip() != "Continue"

    # Any _RoundNextButton with symbol="next" defaults to "Next" tooltip
    generic_next_btn = _RoundNextButton(symbol="next")
    assert generic_next_btn.toolTip() == "Next"
    assert generic_next_btn.accessibleName() == "Next"

    # Power-off prompt wording
    page.show_power_off_prompt()
    expected_prompt_y1 = "Please make sure your Y1 is powered-off and disconnected - then press Next."
    assert page._wait_prompt_label.text() == expected_prompt_y1

    # Fallback to device when no model is set
    page.set_model("")
    page.show_power_off_prompt()
    expected_prompt_fallback = "Please make sure your device is powered-off and disconnected - then press Next."
    assert page._wait_prompt_label.text() == expected_prompt_fallback


def test_flash_service_worker_and_monitor_retention():
    app = QApplication.instance() or QApplication(sys.argv)
    service = FlashService()

    # Worker cancellation retains the worker until finished
    worker = FlashWorker(package_path="test.zip", method="auto")
    service._flash_worker = worker
    cancelled = service.cancel_flash()
    assert cancelled is worker
    assert worker in service._retired_workers
    assert service._flash_worker is None

    # Monitor stopping retains the monitor and does not block GUI thread
    service.start_device_monitor()
    monitor = service._device_monitor
    assert monitor is not None
    service.stop_device_monitor()
    assert monitor in service._retired_monitors
    assert service._device_monitor is None
