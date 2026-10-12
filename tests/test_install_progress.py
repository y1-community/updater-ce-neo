"""The install card's phase, its progress and its ETA.

Covers the two things users actually see during a run:

* the connect prompt must stay up for as long as the tool is still searching
  for the player. It used to be replaced by "please wait" milliseconds after it
  appeared, because SP Flash Tool's own banner, its internal Qt trace, a search
  that had just timed out, and the app's own "searching again" notice all
  counted as forward progress. The sequence below is the real transcript of a
  successful install (search, timeout, restart, connect);
* the bar must follow the bytes the backend reports, and the time remaining
  must be measured from those bytes rather than extrapolated from the clock —
  the big images (userdata 40 MB-3 GB, system 400 MB-1 GB) are most of the
  wall-clock time and least of the count of partitions.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src import flash_service as fs  # noqa: E402
from src.progress_model import ThroughputEta, percent_of  # noqa: E402

# --------------------------------------------------------------------------
# The transcript of a real install, as the console printed it
# --------------------------------------------------------------------------
SP_BANNER = "[QT] =================================================================="
SP_TRACE_ON = "[QT] MediaTek SP Flash Tool v5.1904.00 runtime trace is ON."
SP_SEARCH = "[SP] Search usb, timeout set as 120000 ms"
SP_TRACE_SEARCH = (
    "[QT] 10/11/2026 18:31:28.069 FlashTool[12152][8516][D]: "
    "APCore::ConnMediaUSB::Search(): Search usb, timeout set as 120000 ms"
    "(..\\..\\flashtool\\Conn\\ConnMedia.cpp,21)"
)
SP_TIMEOUT_LINES = [
    "[SP] SearchUSBPortPool failed!",
    "[SP] Failed to find USB port",
    "[SP] Connect BROM failed: S_TIMEOUT(1042)",
    "[SP] Disconnect!",
    "[SP] Exception: err_code[1042], err_msg[ ERROR : S_TIMEOUT (1042)",
    "[SP] [HINT]:",
    "[SP] Logger deinited.",
]
RESTART_NOTICE = (
    "No device yet — searching again (attempt 1). Keep the player powered off "
    "and connect it when prompted."
)
SP_DEVICE_LINES = [
    "[SP] USB port is obtained. path name(\\\\?\\usb#vid_0e8d&pid_2000#...), "
    "port name(MediaTek PreLoader USB VCOM (Android) (COM7))",
    "[SP] USB port detected: \\\\?\\usb#vid_0e8d&pid_2000#5&2e1b880b&0&1#...",
    "[SP] BROM connected",
    "[SP] Downloading & Connecting to DA...",
    "[SP] 100% of DA has been sent.",
]


def _phase_after(lines):
    """The wait phase after feeding ``lines`` to the helper, one at a time."""
    phase = fs.PHASE_BEFORE
    for line in lines:
        phase = fs.advance_tool_wait_phase(phase, line)
    return phase


def test_the_connect_prompt_survives_the_tool_banner_and_trace():
    """The bug: the prompt vanished within milliseconds of appearing."""
    assert _phase_after([SP_SEARCH]) == fs.PHASE_CONNECT
    assert _phase_after([SP_SEARCH, SP_BANNER]) == fs.PHASE_CONNECT
    assert _phase_after([SP_SEARCH, SP_TRACE_ON]) == fs.PHASE_CONNECT
    assert _phase_after([SP_SEARCH, SP_TRACE_SEARCH]) == fs.PHASE_CONNECT


def test_the_connect_prompt_survives_a_timeout_and_its_restart():
    """A search that expires is not progress: the user is still connecting."""
    assert _phase_after([SP_SEARCH, *SP_TIMEOUT_LINES]) == fs.PHASE_CONNECT
    assert _phase_after(
        [SP_SEARCH, *SP_TIMEOUT_LINES, RESTART_NOTICE, SP_BANNER, SP_SEARCH]
    ) == fs.PHASE_CONNECT


def test_the_connect_prompt_ends_when_the_player_answers():
    assert _phase_after([SP_SEARCH, SP_DEVICE_LINES[2]]) == fs.PHASE_PLEASE_WAIT
    assert _phase_after([SP_SEARCH, SP_DEVICE_LINES[1]]) == fs.PHASE_PLEASE_WAIT
    # Uploading the DA itself is work on the player, not a search.
    assert _phase_after([SP_SEARCH, SP_DEVICE_LINES[4]]) == fs.PHASE_PLEASE_WAIT
    # The line that merely says a port was *obtained* is not the player being
    # ready for work: the console prints "USB port detected:" right after it.
    assert _phase_after([SP_SEARCH, SP_DEVICE_LINES[0]]) == fs.PHASE_CONNECT


def test_worker_keeps_the_connect_stage_through_the_real_transcript():
    """The same transcript through the worker's own classification."""
    worker = fs.FlashWorker("pkg.zip", method="sp")
    worker._log = lambda *a, **k: None
    steps = []
    worker.step_changed.connect(steps.append)
    for line in [SP_SEARCH, SP_BANNER, SP_TRACE_SEARCH, *SP_TIMEOUT_LINES,
                 RESTART_NOTICE, SP_BANNER, SP_TRACE_SEARCH]:
        worker._classify_sp_stdout(line)
    assert steps == [fs.STEP_WAITING], steps
    assert worker._usb_search_active is True

    # The port appearing is not yet the stage; BROM connecting is.
    worker._classify_sp_stdout(SP_DEVICE_LINES[1])
    assert steps == [fs.STEP_WAITING], steps
    worker._classify_sp_stdout(SP_DEVICE_LINES[2])
    assert steps[-1] == fs.STEP_DETECT
    assert worker._tool_wait_phase == fs.PHASE_PLEASE_WAIT


# --------------------------------------------------------------------------
# Byte-weighted progress
# --------------------------------------------------------------------------
def test_percent_is_the_byte_fraction_inside_its_slice():
    assert percent_of(0, 100, 20, 75) == 20
    assert percent_of(50, 100, 20, 75) == 58
    assert percent_of(100, 100, 20, 75) == 95
    # A missing or nonsensical total leaves the bar where the slice starts.
    assert percent_of(10, 0, 20, 75) == 20
    assert percent_of("x", "y", 20, 75) == 20


def test_the_tool_own_total_is_what_the_bar_follows():
    """SP reports its totals; the app must not outrun them.

    The tool auto-loads every image the scatter enables, so its total can be
    larger than the package's own file list. With the app's smaller total the
    bar read "half done" while the big images were still to come.
    """
    worker = fs.FlashWorker("pkg.zip", method="sp")
    worker._log = lambda *a, **k: None
    progress = []
    etas = []
    worker.progress.connect(progress.append)
    worker.eta_changed.connect(etas.append)

    worker._classify_sp_stdout("[SP] 1% of image data has been sent (7.37M of 711.46M)")
    worker._classify_sp_stdout("[SP] 50% of image data has been sent (356.62M of 711.46M)")
    assert progress[0] == percent_of(7.37 * 1024**2, 711.46 * 1024**2, 20, 75)
    assert progress[-1] == percent_of(356.62 * 1024**2, 711.46 * 1024**2, 20, 75)
    # 50% of the bytes is around the middle of the write slice, not "63%".
    assert 55 <= progress[-1] <= 60, progress
    assert etas, "the card is told the ETA, even while it is still unknown"
    assert etas[-1] == -1, "one sample is not a rate"


def test_progress_never_goes_backwards_mid_write():
    worker = fs.FlashWorker("pkg.zip", method="sp")
    worker._log = lambda *a, **k: None
    progress = []
    worker.progress.connect(progress.append)
    worker._sp_progress_hwm = 60
    worker._classify_sp_stdout("[SP] 10% of image data has been sent (71.0M of 711.46M)")
    assert progress == [], "a lower reading after a higher one is ignored"


# --------------------------------------------------------------------------
# Measured ETA
# --------------------------------------------------------------------------
class _Clock:
    """A clock the test moves by hand."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_eta_is_measured_from_the_write_rate():
    clock = _Clock()
    eta = ThroughputEta(clock=clock)
    total = 100 * 1024**2
    # 1 MiB/s: 10 MiB every 10 seconds.
    for i in range(1, 6):
        clock.now += 10.0
        eta.note(i * 10 * 1024**2, total)
    remaining = eta.note(50 * 1024**2, total)
    assert remaining is not None
    assert 45 <= remaining <= 55, remaining
    assert eta.rate > 0


def test_eta_is_unknown_until_it_has_watched_enough():
    clock = _Clock()
    eta = ThroughputEta(clock=clock)
    assert eta.note(10, 100) is None
    clock.now += 1.0
    assert eta.note(20, 100) is None, "two samples are not yet a rate"
    clock.now += 1.0
    assert eta.note(30, 100) is not None


def test_eta_ignores_a_position_that_went_backwards():
    """A new image stream restarts the tool's own counter."""
    clock = _Clock()
    eta = ThroughputEta(clock=clock)
    total = 1024**3
    for i in range(1, 5):
        clock.now += 1.0
        eta.note(i * 50 * 1024**2, total)
    assert eta.note(60 * 1024**2, total) is None, "the old rate no longer applies"
    for i in range(1, 5):
        clock.now += 1.0
        last = eta.note(60 * 1024**2 + i * 50 * 1024**2, total)
    assert last is not None


def test_a_pause_lengthens_the_estimate_then_makes_it_unknown():
    clock = _Clock()
    eta = ThroughputEta(clock=clock)
    total = 100 * 1024**2
    for i in range(1, 5):
        clock.now += 1.0
        eta.note(i * 1024**2, total)
    steady = eta.note(4 * 1024**2, total)
    assert steady is not None
    # Five quiet seconds: the bytes have not moved, so the estimate grows —
    # that is what the pause means.
    for _ in range(5):
        clock.now += 1.0
        stalled = eta.note(4 * 1024**2, total)
    assert stalled is not None and stalled > steady
    # Long quiet: nothing recent to measure, so stop guessing.
    clock.now += 120.0
    assert eta.note(4 * 1024**2, total) is None


def test_eta_reports_a_finished_write_as_zero():
    clock = _Clock()
    eta = ThroughputEta(clock=clock)
    for i in range(1, 5):
        clock.now += 1.0
        eta.note(i * 10, 100)
    assert eta.note(100, 100) == 0


def test_resetting_forgets_the_previous_run():
    clock = _Clock()
    eta = ThroughputEta(clock=clock)
    for i in range(1, 5):
        clock.now += 1.0
        eta.note(i * 10, 100)
    eta.reset()
    assert eta.rate == 0.0
    assert eta.note(90, 100) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
