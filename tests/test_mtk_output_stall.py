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

from src.flash_service import _MtkOutputStallDetector  # noqa: E402

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


def test_write_progress_only_reads_after_the_last_line_is_quiet():
    def events(det):
        det.note("DRAM config")
        time.sleep(0.1)
        det.progress()

    assert _run(events, settle=0.1) == []
