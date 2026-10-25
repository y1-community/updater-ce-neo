"""Bring the USB side of the app back after a run loses the player.

Nothing here can restart macOS's USB controller: a wedged IOUSBHost stack lives
in kernel space, and only unplugging the offending device or a power cycle
brings the ports back. What the app *can* do is make sure it is not what is
holding the bus in that state — release every handle it owns, stop a helper
that may still have the player claimed, rebuild libusb's context — and then say
which of the three situations the user is actually in, because the right next
step is different for each:

* :data:`READY`    — a MediaTek device is enumerating again; a retry can work.
* :data:`NO_TARGET` — the host still sees USB devices, just not the player.
* :data:`BUS_DOWN` — nothing enumerates at all: the controller needs a power
  cycle, and retrying the install cannot help.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

READY = "ready"
NO_TARGET = "no_target"
BUS_DOWN = "bus_down"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class UsbRecoveryReport:
    """Outcome of :func:`recover_usb_connection`."""

    state: str
    total_devices: int = 0
    mtk_devices: int = 0
    reaped: tuple = field(default_factory=tuple)
    detail: str = ""

    @property
    def bus_down(self) -> bool:
        return self.state == BUS_DOWN

    def guidance_key(self) -> str:
        """The recovery line the install card should show for this state."""
        if self.bus_down:
            return "err_usb_bus_down_recovery"
        return "err_usb_disconnected_recovery"


def _reap_helpers() -> tuple:
    """Stop flash helpers this app left behind, which may still hold the player."""
    try:
        from . import process_guard

        killed = process_guard.sweep_stray_tools(reason="usb recovery")
        return tuple(killed or ())
    except Exception as e:  # pragma: no cover - a failed sweep never blocks recovery
        logger.debug("USB recovery could not sweep leftover tools: %s", e)
        return ()


def _enumerate():
    """Count every USB device and every MediaTek one on a fresh context.

    Returns ``(total, mtk)``, or ``None`` when libusb itself is unavailable.
    """
    from . import mtk_api

    try:
        import usb.core  # type: ignore

        backend = mtk_api.get_cross_platform_libusb_backend()
        if backend is None:
            return None
        from .flash_service import _MTK_VENDOR_IDS

        # A freshly initialised libusb context answers its first enumeration
        # with an empty list on macOS before its IOKit notification port is
        # ready. Reporting "no USB devices at all" from that answer would tell
        # the user to restart their Mac over a healthy bus, so look twice.
        for attempt in range(3):
            total = 0
            mtk = 0
            for dev in usb.core.find(find_all=True, backend=backend):
                total += 1
                try:
                    vid = int(dev.idVendor)
                except Exception:
                    continue
                if vid in _MTK_VENDOR_IDS:
                    mtk += 1
            if total or attempt == 2:
                return total, mtk
            time.sleep(0.25)
        return 0, 0
    except Exception as e:
        logger.debug("USB recovery could not enumerate the bus: %s", e)
        return None


def recover_usb_connection(
    *, reap_helpers: bool = True, reset_context: bool = False
) -> UsbRecoveryReport:
    """Stop leftover helpers and report what the host can see.

    ``reset_context`` drops libusb's context and builds a new one. Only a
    caller that knows no device monitor is running may ask for that: the
    monitor caches the backend it was started with, and exiting a context out
    from under an in-flight enumeration is worse than the stale handle it
    would clear. The GUI enumerates only — the handle belongs to the flash
    helper process, which the sweep above has already stopped.
    """
    from . import mtk_api

    reaped = _reap_helpers() if reap_helpers else ()
    if reset_context:
        mtk_api.dispose_libusb_backend()
    seen = _enumerate()
    if seen is None:
        return UsbRecoveryReport(UNKNOWN, reaped=reaped, detail="libusb unavailable")
    total, mtk = seen
    if total == 0:
        state = BUS_DOWN
    elif mtk == 0:
        state = NO_TARGET
    else:
        state = READY
    report = UsbRecoveryReport(
        state, total_devices=total, mtk_devices=mtk, reaped=reaped
    )
    logger.info(
        "USB recovery: %s (%d device(s) enumerated, %d MediaTek)%s",
        state,
        total,
        mtk,
        f", stopped {len(reaped)} leftover helper(s)" if reaped else "",
    )
    return report
