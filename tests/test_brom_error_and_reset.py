"""The BROM handshake failure, and the reboot that ends an MTKClient install.

Two gaps this file pins down:

* ``S_BROM_CMD_STARTCMD_FAIL (2005)`` is the failure users hit most often with
  SP Flash Tool. The tool found the USB port and then could not start the boot
  ROM handshake; its own log says only "Possibly target power up too early",
  which names the symptom and not the fix. The fix — the player must be
  unplugged while the tool is searching, and plugged back in only once the
  connect prompt is up — now travels on the failure the progress card shows,
  with the raw code still in front of it so the error page keeps explaining it.
* A successful in-process MTKClient run ends like the Chinese 1.1.7 reference:
  it logs "Flash complete! Disconnect USB and reboot the device." and reports
  success. It sends no reset, so the user power-cycles the player.
"""

import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import flash_service as fs  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def _worker(method="sp", model="Y1"):
    """A worker that never starts a real backend (the methods are driven directly)."""
    worker = fs.FlashWorker(str(Path("C:/fw/firmware.zip")), method=method, model=model)
    codes = []
    worker.finished.connect(lambda ok, code: codes.append((ok, code)))
    worker.codes = codes
    return worker


#: The exact failure from a real console run: port found, handshake refused.
BROM_FAILURE = "S_BROM_CMD_STARTCMD_FAIL (2005)"


class _Loader:
    """Stand-in for mtkclient's DALoader, recording what it was asked to do."""

    def __init__(self, raises=False):
        self.calls = []
        self._raises = raises

    def shutdown(self, bootmode=None, **kwargs):
        self.calls.append(bootmode)
        if self._raises:
            raise SystemExit("device gone")


# --------------------------------------------------------------------------
# The BROM handshake failure carries the instruction that resolves it
# --------------------------------------------------------------------------
def test_brom_handshake_failures_are_recognised():
    _app()
    for value in (
        BROM_FAILURE,
        "S_BROM_CMD_STARTCMD_FAIL (2005)",
        "err_code[2005]",  # the tool's raw exception line
        "SP_ERR_2005",  # what the worker emits when a code has no name
        "2005",
        "S_BROM_CMD_START_FAIL",
        "S_BROM_CMD_JUMP_DA_FAIL",
    ):
        assert fs.is_brom_handshake_error(value), value
        hint = fs.sp_brom_handshake_hint(value)
        assert hint, value
        low = hint.lower()
        assert "unplug" in low and "connect" in low, hint
        # One sentence: it is appended to an error string, not a paragraph.
        assert hint.count(".") == 1, hint


def test_unrelated_failures_carry_no_brom_hint():
    _app()
    for value in (
        "S_TIMEOUT (1042)",
        "S_FT_DOWNLOAD_FAIL (4008)",
        "S_CHIP_TYPE_NOT_MATCH (3184)",
        "DEVICE_PACKAGE_MISMATCH",
        "SP_EXIT_1",
        "SP_ERR_9999",
        "MTK_FLASH_FAILED (1)",
        "",
        None,
    ):
        assert not fs.is_brom_handshake_error(value), value
        assert fs.sp_brom_handshake_hint(value) == "", value


def test_the_hint_travels_on_the_failure_the_card_shows():
    _app()
    worker = _worker()
    worker._finish_sp_failure(BROM_FAILURE)

    assert worker.codes == [(False, f"{BROM_FAILURE} — {worker.error_hint}")]
    shown = worker.codes[0][1]
    assert worker.error_hint and "unplug" in worker.error_hint.lower()
    # The code stays at the front of the message and stays machine-readable,
    # so the error page can still name and explain the failure.
    assert fs.sp_error_name(shown) == "S_BROM_CMD_STARTCMD_FAIL"
    assert fs.sp_error_code(shown) == "2005"
    assert fs.describe_backend_error(shown)
    assert "S_BROM_CMD_STARTCMD_FAIL" in shown and "2005" in shown


def test_a_failure_without_a_hint_is_reported_exactly_as_before():
    _app()
    worker = _worker()
    worker._finish_sp_failure("S_TIMEOUT (1042)")

    assert worker.error_hint == ""
    assert worker.codes == [(False, "S_TIMEOUT (1042)")]


# --------------------------------------------------------------------------
# A successful MTKClient run restarts the player
# --------------------------------------------------------------------------
def test_mtk_reset_uses_mtkclients_own_reset_command():
    loader = _Loader()
    assert fs.mtk_reset_to_normal(SimpleNamespace(daloader=loader)) is True
    # bootmode 0 == NORMAL: reboot into the installed firmware, not fastboot.
    assert loader.calls == [0]

    # The loader also lives on the DA handler in some sessions.
    handler_loader = _Loader()
    assert fs.mtk_reset_to_normal(SimpleNamespace(), SimpleNamespace(daloader=handler_loader)) is True
    assert handler_loader.calls == [0]

    # No loader to ask: nothing to send, and no exception either way.
    assert fs.mtk_reset_to_normal(SimpleNamespace()) is False
    assert fs.mtk_reset_to_normal(None) is False


def test_a_successful_mtk_run_ends_without_a_reset_like_the_reference():
    _app()
    worker = _worker(method="mtk")
    loader = _Loader()
    lines = []
    worker.log_message.connect(lines.append)

    worker._end_mtk_run_successfully(SimpleNamespace(daloader=loader), None)

    assert loader.calls == [], "the reference sends no reset after the last write"
    assert worker.codes == [(True, "")]
    assert any("reboot the device" in line for line in lines)


def test_a_successful_mtk_run_without_a_loader_still_succeeds():
    _app()
    worker = _worker(method="mtk")

    worker._end_mtk_run_successfully(SimpleNamespace(), None)

    assert worker.codes == [(True, "")]


def test_the_reboot_can_be_switched_off_for_a_session_with_no_player_left():
    _app()
    worker = _worker(method="mtk")
    worker.reboot_after_success = False
    loader = _Loader()

    assert worker._reboot_after_mtk_success(SimpleNamespace(daloader=loader), None) is False
    assert loader.calls == []


def test_a_failed_mtk_run_never_reaches_the_reboot():
    _app()
    # The reboot lives in one method, and the run loop's tool-error guard
    # returns before it: a failure must not restart the player into firmware
    # that is only half written. Asserted structurally because the guard and
    # the call are both inside the (device-owning) run loop itself.
    source = inspect.getsource(fs.FlashWorker._flash_via_mtkclient_core)
    guard = source.rindex("if self._tool_error:")
    call = source.index("self._end_mtk_run_successfully(mtk, da_handler)")
    assert call > guard, "the reboot must come after the failure guard"
    assert source.count("self._end_mtk_run_successfully(") == 1
    assert source.count("_end_mtk_run_successfully(") == 1, "no other caller"


if __name__ == "__main__":  # pragma: no cover - smoke_test style entry
    raise SystemExit(pytest.main([__file__, "-q"]))
