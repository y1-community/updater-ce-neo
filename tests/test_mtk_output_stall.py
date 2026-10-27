"""The MTKClient output stall detector must only fire on a real bring-up stall.

A long partition write prints nothing but write progress. That used to look
like a stalled DRAM line and aborted a working install, so write progress must
keep the detector quiet, and any ordinary output must disarm it.
"""

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.flash_service import FlashWorker, _MtkOutputStallDetector  # noqa: E402

WINDOW = 0.2
INTERVAL = 0.02


def _run(events, settle=0.5):
    """Start a detector, feed it ``events(det)``, and report what it fired."""
    fired = []
    det = _MtkOutputStallDetector(
        lambda line, elapsed: fired.append(line), window=WINDOW, interval=INTERVAL
    )
    det.start()
    try:
        events(det)
        time.sleep(settle)
    finally:
        det.stop()
        det.join(timeout=2)
    return fired


def test_a_stalled_dram_line_fires_once_the_window_passes():
    fired = _run(lambda det: det.note("DRAM config needed for stage 2"))
    assert fired == ["DRAM config needed for stage 2"]


def test_write_progress_keeps_a_stalled_looking_line_from_firing():
    def events(det):
        det.note("DRAM config needed for stage 2")
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            det.progress()
            time.sleep(0.02)

    assert _run(events) == []


def test_ordinary_output_after_a_stall_line_disarms_it():
    def events(det):
        det.note("DRAM config needed for stage 2")
        det.note("sdmmc_write_data: addr=0x5180000 length=859832320")

    assert _run(events) == []


def test_a_stage2_checksum_failure_after_the_dram_line_is_a_stall():
    fired = _run(
        lambda det: det.note("Failed to read stage-2 config checksum (USB reply too short)")
    )
    assert len(fired) == 1


def test_subprocess_stall_terminates_backend_and_reports_stall_code():
    class _Proc:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    worker = FlashWorker("pkg.zip", method="mtk")
    proc = _Proc()
    worker._process = proc
    finalized = []
    worker._finalize_mtk_stall = lambda reason, **kw: finalized.append(reason)

    worker._on_mtk_output_stalled_subprocess("DRAM config needed for stage 2", 21.0)

    assert proc.terminated is True
    assert worker._stalled_out is True
    assert finalized == ["MTK_OUTPUT_STALLED"]


def test_write_progress_only_reads_after_the_last_line_is_quiet():
    def events(det):
        det.note("DRAM config")
        time.sleep(0.1)
        det.progress()

    assert _run(events, settle=0.1) == []


def test_child_progress_log_refreshes_liveness_without_becoming_failure():
    holds = []
    worker = FlashWorker("pkg.zip", method="mtk", device_loss_hold=holds.append)
    classified = []
    worker._classify_mtk_output = classified.append
    line = "Progress: 8.3% Write BOOTIMG (0.5MB/6.0MB), 5.59 MB/s (02m:05s left)"

    worker._handle_mtk_subprocess_log(line)
    worker._handle_mtk_subprocess_log(line)  # duplicate output must not extend grace

    assert worker._mtk_cli_partition_progress == {"BOOTIMG": 0.5}
    assert worker._last_install_progress_time > 0
    assert holds == [30.0]
    assert classified == []


def test_child_progress_log_tracks_advancing_partition_bytes():
    holds = []
    worker = FlashWorker("pkg.zip", method="mtk", device_loss_hold=holds.append)

    worker._handle_mtk_subprocess_log(
        "Progress: 8.3% Write BOOTIMG (0.5MB/6.0MB), 5.59 MB/s (02m:05s left)"
    )
    worker._handle_mtk_subprocess_log(
        "Progress: 16.7% Write BOOTIMG (1.0MB/6.0MB), 5.44 MB/s (02m:04s left)"
    )

    assert worker._mtk_cli_partition_progress == {"BOOTIMG": 1.0}
    assert holds == [30.0, 30.0]


def test_mtk_usb_loss_handler_ignores_reenumeration_while_write_grace_is_active():
    from src.state import FlashState
    from src.ui.main_window import MainWindow

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    window = MainWindow()
    worker = FlashWorker("pkg.zip", method="mtk")
    worker._tool_tag = "MTK"
    worker._past_usb_search = True
    worker._device_loss_held_until = time.time() + 30.0
    window.service._flash_worker = worker
    window.sm.force_state(FlashState.S4_FLASHING)
    try:
        window._on_device_lost()
        assert window.sm.state is FlashState.S4_FLASHING
        assert not hasattr(window, "_kill_process_timer")
        assert window._install_failed is False
    finally:
        window.service._flash_worker = None
        window.close()
        app.processEvents()


def test_mtk_usb_loss_handler_reports_loss_after_write_grace_expires():
    from src.state import FlashState
    from src.ui.main_window import MainWindow

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    window = MainWindow()
    worker = FlashWorker("pkg.zip", method="mtk")
    worker._tool_tag = "MTK"
    worker._past_usb_search = True
    worker._device_loss_held_until = time.time() - 1.0
    window.service._flash_worker = worker
    window.sm.force_state(FlashState.S4_FLASHING)
    try:
        window._on_device_lost()
        assert window.sm.state is FlashState.S5_USB_DISCONNECTED
        assert window._install_failed is True
        assert window._kill_process_timer.isActive()
    finally:
        if hasattr(window, "_kill_process_timer"):
            window._kill_process_timer.stop()
        window.service._flash_worker = None
        window.close()
        app.processEvents()
