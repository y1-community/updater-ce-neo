"""The app must never reset a USB device on macOS, and must clean up after a loss.

libusb_reset_device() re-enumerates through the host controller and on macOS it
takes every other device behind the same Apple USB hub down with it (the
built-in keyboard and trackpad included); the ports then stay dead until the
controller is power cycled. mtkclient asks for that reset in close(reset=True),
so the guard has to sit below mtkclient as well as in its close path.

The recovery tests drive the bus report that decides whether the user is told
to replug the player or to restart the Mac.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _StubDevice:
    """The parts of a pyusb Device that usblib.close touches."""

    def __init__(self):
        self.reset_calls = 0
        self._ctx = _StubContext()

    def reset(self):
        self.reset_calls += 1

    def is_kernel_driver_active(self, interface):
        return True

    def attach_kernel_driver(self, interface):
        pass


class _StubContext:
    def __init__(self):
        self.disposed = 0

    def dispose(self, device):
        self.disposed += 1


@pytest.mark.skipif(
    not sys.platform.startswith("darwin"), reason="the reset guard is macOS-only"
)
def test_pyusb_reset_is_neutralised_once_the_guard_is_installed():
    from src import mtk_api

    import usb.core

    assert mtk_api.install_macos_usb_reset_guard() is True
    assert getattr(usb.core.Device.reset, "_mtk_guarded", False) is True

    device = _StubDevice()
    # The guard replaces the class method, so call it the way pyusb would.
    assert usb.core.Device.reset(device) is None
    assert device.reset_calls == 0, "a guarded device must not reach libusb"


def test_mtkclient_close_never_resets_the_device(monkeypatch):
    """close(reset=True) is the call site that took the host's ports down."""
    monkeypatch.setattr(sys, "platform", "darwin")
    from src import mtk_api

    mtk_api._ensure_imports()
    from mtkclient.Library.Connection import usblib

    device = _StubDevice()
    port = usblib.UsbClass.__new__(usblib.UsbClass)
    port.connected = True
    port.interface = 1
    port.device = device

    port.close(reset=True)

    assert device.reset_calls == 0
    assert device._ctx.disposed == 1, "the interface must still be released"


def test_the_backend_is_one_shared_context(monkeypatch):
    """A backend per call churned libusb contexts mid-session."""
    from src import mtk_api

    made = []

    class _Backend:
        def __init__(self):
            self.exits = 0

        def exit(self):
            self.exits += 1

    def _make():
        backend = _Backend()
        made.append(backend)
        return backend

    monkeypatch.setattr(mtk_api, "_shared_backend", None)
    monkeypatch.setattr(mtk_api, "_create_libusb_backend", _make)

    first = mtk_api.get_cross_platform_libusb_backend()
    second = mtk_api.get_cross_platform_libusb_backend()
    assert first is second
    assert len(made) == 1

    assert mtk_api.dispose_libusb_backend() is True
    assert made[0].exits == 1
    third = mtk_api.get_cross_platform_libusb_backend()
    assert third is not first
    assert len(made) == 2
    mtk_api.dispose_libusb_backend()


class _StubDev:
    def __init__(self, vid, pid=0x2000):
        self.idVendor = vid
        self.idProduct = pid


def _run_recovery(monkeypatch, devices, backend=object()):
    from src import usb_recovery

    import usb.core

    monkeypatch.setattr(usb_recovery, "_reap_helpers", lambda: ())
    monkeypatch.setattr(usb_recovery.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        "src.mtk_api.get_cross_platform_libusb_backend", lambda: backend
    )
    monkeypatch.setattr(
        usb.core, "find", lambda find_all=False, backend=None: iter(devices)
    )
    return usb_recovery.recover_usb_connection(reap_helpers=False)


def test_an_empty_first_enumeration_is_not_a_dead_bus(monkeypatch):
    """macOS answers the first lookup on a fresh context with nothing."""
    from src import usb_recovery

    answers = [[], [_StubDev(0x0E8D)]]
    monkeypatch.setattr(usb_recovery, "_reap_helpers", lambda: ())
    monkeypatch.setattr(usb_recovery.time, "sleep", lambda _s: None)
    monkeypatch.setattr("src.mtk_api.get_cross_platform_libusb_backend", lambda: object())

    import usb.core

    monkeypatch.setattr(
        usb.core, "find", lambda find_all=False, backend=None: iter(answers.pop(0))
    )
    report = usb_recovery.recover_usb_connection(reap_helpers=False)
    assert report.state == usb_recovery.READY
    assert report.mtk_devices == 1


def test_no_devices_at_all_means_the_host_controller_is_down(monkeypatch):
    from src import usb_recovery

    report = _run_recovery(monkeypatch, [])
    assert report.state == usb_recovery.BUS_DOWN
    assert report.bus_down is True
    assert report.guidance_key() == "err_usb_bus_down_recovery"


def test_devices_without_the_player_ask_for_a_replug(monkeypatch):
    from src import usb_recovery

    report = _run_recovery(monkeypatch, [_StubDev(0x05AC), _StubDev(0x046D)])
    assert report.state == usb_recovery.NO_TARGET
    assert report.total_devices == 2
    assert report.guidance_key() == "err_usb_disconnected_recovery"


def test_a_mediaTek_device_means_a_retry_can_work(monkeypatch):
    from src import usb_recovery

    report = _run_recovery(monkeypatch, [_StubDev(0x05AC), _StubDev(0x0E8D)])
    assert report.state == usb_recovery.READY
    assert report.mtk_devices == 1


def test_startup_recovery_is_silent_and_rebuilds_the_context(monkeypatch):
    """It runs on every launch, with no UI and no user action."""
    from src import app as app_module, usb_recovery

    calls = []
    monkeypatch.setattr(
        usb_recovery, "recover_usb_connection", lambda **kwargs: calls.append(kwargs)
    )
    thread = app_module._reset_usb_after_startup()
    thread.join(5)
    assert calls == [{"reap_helpers": False, "reset_context": True}]


def test_recovery_leaves_the_context_alone_unless_asked(monkeypatch):
    """The device monitor caches its backend, so a dispose would pull it away."""
    from src import mtk_api, usb_recovery

    calls = []
    monkeypatch.setattr(usb_recovery, "_reap_helpers", lambda: ())
    monkeypatch.setattr(mtk_api, "dispose_libusb_backend", lambda: calls.append("dispose"))
    monkeypatch.setattr(
        "src.mtk_api.get_cross_platform_libusb_backend", lambda: calls.append("backend")
    )
    usb_recovery.recover_usb_connection(reap_helpers=False)
    assert calls == ["backend"]

    calls.clear()
    usb_recovery.recover_usb_connection(reap_helpers=False, reset_context=True)
    assert calls == ["dispose", "backend"]


def test_the_bus_down_line_renders_with_the_model_name():
    from src.i18n import tr

    text = tr("err_usb_bus_down_recovery").format(model="Y1")
    assert "Y1" in text
    assert "{model}" not in text
    assert "restart" in text.lower()


def test_a_dead_bus_switches_the_install_card_to_the_power_cycle_line(monkeypatch):
    """The disconnect screen must not promise a replug will fix a dead host."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["INNIOASIS_HEADLESS"] = "1"
    from PySide6.QtWidgets import QApplication

    from src import usb_recovery
    from src.state import FlashState
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()
    try:
        win.sm.force_state(FlashState.S5_USB_DISCONNECTED)
        win._flash_page.show_usb_disconnected(
            on_retry=lambda: None, on_cancel=lambda: None
        )
        win._on_usb_recovery_report(
            usb_recovery.UsbRecoveryReport(usb_recovery.BUS_DOWN)
        )
        app.processEvents()
        assert win._flash_page._power_off_recovery_key == "err_usb_bus_down_recovery"
        assert "restart" in win._flash_page._wait_prompt_label.text().lower()
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_a_live_bus_leaves_the_replug_guidance_alone():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["INNIOASIS_HEADLESS"] = "1"
    from PySide6.QtWidgets import QApplication

    from src import usb_recovery
    from src.state import FlashState
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    win = MainWindow()
    win.show()
    app.processEvents()
    try:
        win.sm.force_state(FlashState.S5_USB_DISCONNECTED)
        win._flash_page.show_usb_disconnected(
            on_retry=lambda: None, on_cancel=lambda: None
        )
        win._on_usb_recovery_report(
            usb_recovery.UsbRecoveryReport(usb_recovery.NO_TARGET, 3, 0)
        )
        app.processEvents()
        assert win._flash_page._power_off_recovery_key != "err_usb_bus_down_recovery"
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


if __name__ == "__main__":  # pragma: no cover - smoke_test style entry
    raise SystemExit(pytest.main([__file__, "-q"]))
