import os
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.flash_service import (
    FlashWorker,
    is_disconnection_error,
    is_mtk_device_connected,
)
from src.state import FlashState, StateMachine
from src.ui.flash_page import FlashPage
from src.ui.main_window import MainWindow, _PAGE_FLASH


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if not app:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        app = QApplication([])
    return app


def test_is_disconnection_error_patterns():
    assert is_disconnection_error("Device disconnected") is True
    assert is_disconnection_error("LIBUSB_ERROR_NO_DEVICE") is True
    assert is_disconnection_error("usb.core.USBError: [Errno 19] No such device (it may have been disconnected)") is True
    assert is_disconnection_error("STATUS_DEVICE_DISCONNECTED") is True
    assert is_disconnection_error("BrokenPipeError: [Errno 32] Broken pipe") is True
    assert is_disconnection_error("Something unrelated: syntax error") is False


def test_flash_page_usb_disconnected_recovery_prompt(qapp):
    fp = FlashPage()
    fp.set_model("Y1")

    retry_called = []
    cancel_called = []

    def on_retry():
        retry_called.append(True)

    def on_cancel():
        cancel_called.append(True)

    fp.show_usb_disconnected(on_retry=on_retry, on_cancel=on_cancel)

    # Must display the paperclip / pin reset instruction and ask to press next
    prompt_text = fp._wait_prompt_label.text()
    assert "Your Y1 got disconnected" in prompt_text
    assert "disconnect the USB" in prompt_text
    assert "paperclip / pin" in prompt_text
    assert "between headphone jack and USB Port" in prompt_text
    assert "press next" in prompt_text

    # Action buttons: Next button and Cancel button must be visible
    assert not fp._wait_continue_btn.isHidden()
    assert not fp._wait_cancel_btn.isHidden()
    assert fp._wait_continue_btn.toolTip().lower() == "next"

    # Paperclip hint under the card must be visible
    assert not fp._paperclip_hint_host.isHidden()

    # Pressing Next fires the recovery retry callback
    fp._wait_continue_btn.click()
    assert retry_called == [True]


def test_flash_page_usb_disconnected_generic_device(qapp):
    fp = FlashPage()
    fp.set_model("")  # No model specified

    fp.show_usb_disconnected()
    prompt_text = fp._wait_prompt_label.text()
    assert "Your device got disconnected" in prompt_text
    assert "press next" in prompt_text
    assert not fp._wait_continue_btn.isHidden()


def test_main_window_handles_disconnection_and_kills_task(qapp):
    win = MainWindow()
    try:
        win._package_name = "Firmware Test"
        win._package_model = "Y1"
        win._past_usb_search = True

        # Simulate flash failure with disconnection
        win.sm.force_state(FlashState.S4_FLASHING)
        assert win.sm.state is FlashState.S4_FLASHING

        win._handle_flash_failure("LIBUSB_ERROR_NO_DEVICE: Device disconnected")

        # State machine should have transitioned to S5_USB_DISCONNECTED
        assert win.sm.state is FlashState.S5_USB_DISCONNECTED
        assert win._install_failed is True

        # Prompt must be showing the recovery instructions
        prompt_text = win._flash_page._wait_prompt_label.text()
        assert "Your Y1 got disconnected" in prompt_text
        assert "press next" in prompt_text
        assert not win._flash_page._wait_continue_btn.isHidden()

        # Clicking Next should recover: reset the state and rearm flash flow
        win._on_power_off_continue()
        assert win._install_failed is False
        assert win.sm.state in (FlashState.S6_RETRYING, FlashState.S2_WAIT_CONNECTION)
    finally:
        win.close()
        win.deleteLater()
        qapp.processEvents()
