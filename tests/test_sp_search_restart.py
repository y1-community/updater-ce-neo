"""An expired SP Flash Tool USB search is restarted, not reported as a failure.

The console tool bounds its own device search (``[Conn] Timeout=3`` in its
option.ini prints as "Search usb, timeout set as 120000 ms"), and its ``-t``
switch is the battery mode rather than a timeout, so a player connected after
two minutes used to end the install with ``S_TIMEOUT (1042)`` while the app was
still showing "Please connect your Y1/Y2". These tests drive the run loop with
a fake tool that speaks the real console lines.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.flash_service import (  # noqa: E402
    PHASE_BEFORE,
    STEP_DETECT,
    STEP_PLEASE_WAIT,
    STEP_WAITING,
    FlashWorker,
    is_sp_usb_search_line,
)

SEARCH = "  Search usb, timeout set as 120000 ms"
# The tail of a run that never saw the player, exactly as flash_tool prints it.
EXPIRED = [
    SEARCH + "\n",
    "SearchUSBPortPool failed!\n",
    "Failed to find USB port\n",
    "Connect BROM failed: S_TIMEOUT(1042)\n",
]


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class _FakeProcess:
    """Just enough of ``subprocess.Popen`` for the console run loop."""

    def __init__(self, lines, returncode):
        self.stdout = _FakeStdout(lines)
        self.returncode = returncode
        self.pid = 4242

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 1


def _worker():
    worker = FlashWorker("package.zip", method="sp", model="Y2")
    worker.SP_SEARCH_RESTART_PAUSE = 0
    return worker


def _spawner(runs, spawned):
    def spawn():
        process = runs[len(spawned)]
        spawned.append(process)
        return process

    return spawn


def test_search_line_is_the_one_the_tool_prints():
    assert is_sp_usb_search_line(SEARCH)
    assert is_sp_usb_search_line("[SP] " + SEARCH)


def test_expired_search_restarts_and_the_late_device_still_installs(tmp_path):
    runs = [
        _FakeProcess(EXPIRED, 1),
        _FakeProcess(EXPIRED, 1),
        _FakeProcess([SEARCH + "\n", "BROM connected\n", "12% of DA has been sent\n"], 0),
    ]
    spawned = []
    worker = _worker()
    steps = []
    worker.step_changed.connect(steps.append)
    logged = []
    real_log = worker._log
    worker._log = lambda message: (logged.append(str(message)), real_log(message))[1]

    exit_code, _launch_time = worker._sp_console_search(_spawner(runs, spawned), tmp_path)

    assert exit_code == 0
    assert len(spawned) == 3, "the two expired searches must have been re-armed"
    assert worker._device_seen is True
    assert steps[0] == STEP_WAITING and STEP_DETECT in steps
    assert worker._tool_wait_phase == "please_wait"
    assert any("searching again" in line.lower() for line in logged), logged


def test_the_connect_prompt_stays_up_while_it_searches_again(tmp_path):
    runs = [_FakeProcess(EXPIRED, 1) for _ in range(_worker().SP_SEARCH_RESTARTS + 1)]
    spawned = []
    worker = _worker()
    steps = []
    worker.step_changed.connect(steps.append)
    phases_at_launch = []

    def spawn():
        # State when each run starts: everything after the first must already
        # have the connect sentence back up. The tool's timeout lines read as
        # forward progress and would otherwise leave "please wait" showing.
        phases_at_launch.append(worker._tool_wait_phase)
        process = runs[len(spawned)]
        spawned.append(process)
        return process

    worker._sp_console_search(spawn, tmp_path)

    assert len(spawned) == worker.SP_SEARCH_RESTARTS + 1
    assert phases_at_launch[0] == PHASE_BEFORE
    assert set(phases_at_launch[1:]) == {"connect"}, phases_at_launch
    # The connect sentence goes up on the first search line and stays up for
    # every attempt: a search expiring is not progress, so nothing moves the
    # card to "please wait" and nothing has to be put back afterwards. One
    # emission, not one per attempt — a prompt that never came down does not
    # need re-arming, and the user never sees it flicker.
    assert steps == [STEP_WAITING], steps
    assert STEP_PLEASE_WAIT not in steps, "a timed-out search is not progress"
    assert worker._search_started and not worker._device_seen


def test_a_device_that_never_answers_still_fails_after_the_limit(tmp_path):
    runs = [_FakeProcess(EXPIRED, 1) for _ in range(_worker().SP_SEARCH_RESTARTS + 4)]
    spawned = []
    worker = _worker()

    exit_code, _launch_time = worker._sp_console_search(_spawner(runs, spawned), tmp_path)

    assert exit_code == 1
    assert len(spawned) == worker.SP_SEARCH_RESTARTS + 1, "the restart loop must be bounded"
    assert worker._device_seen is False


def test_a_failure_after_the_device_appeared_is_never_replayed(tmp_path):
    runs = [
        _FakeProcess(
            [SEARCH + "\n", "BROM connected\n", "Download failed: S_DA_INIT_SYNC_ERROR (1093)\n"],
            1,
        ),
        _FakeProcess(EXPIRED, 1),
    ]
    spawned = []
    worker = _worker()

    exit_code, _launch_time = worker._sp_console_search(_spawner(runs, spawned), tmp_path)

    assert exit_code == 1
    assert len(spawned) == 1, "a run that already had the device must not be repeated"
    assert worker._device_seen is True


def test_a_run_that_never_searched_is_not_restarted(tmp_path):
    runs = [_FakeProcess(["Loading scatter file failed\n"], 1), _FakeProcess(EXPIRED, 1)]
    spawned = []
    worker = _worker()

    worker._sp_console_search(_spawner(runs, spawned), tmp_path)

    assert len(spawned) == 1
    assert worker._tool_wait_phase == PHASE_BEFORE
    assert worker._search_started is False


def test_cancelling_during_a_search_ends_the_loop(tmp_path):
    runs = [_FakeProcess(EXPIRED, 1)]
    spawned = []
    worker = _worker()
    worker._cancelled = True
    finished = []
    worker.finished.connect(lambda ok, code: finished.append((ok, code)))

    exit_code, _launch_time = worker._sp_console_search(_spawner(runs, spawned), tmp_path)

    assert exit_code is None
    assert finished == [(False, "USER_CANCELLED")]
    assert len(spawned) == 1


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        test_search_line_is_the_one_the_tool_prints()
        test_expired_search_restarts_and_the_late_device_still_installs(tmp)
        test_the_connect_prompt_stays_up_while_it_searches_again(tmp)
        test_a_device_that_never_answers_still_fails_after_the_limit(tmp)
        test_a_failure_after_the_device_appeared_is_never_replayed(tmp)
        test_a_run_that_never_searched_is_not_restarted(tmp)
        test_cancelling_during_a_search_ends_the_loop(tmp)

    print("All SP search restart tests passed!")
