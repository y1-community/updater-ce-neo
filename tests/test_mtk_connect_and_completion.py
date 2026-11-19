"""MTKclient connect timing and partition-based completion.

The connect ask belongs to the moment mtkclient prints its handshake wait
line, and an install is complete when the last partition is written — not when
the backend's exit code or log tail happens to say so.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_mtkclient_wait_lines_are_what_reveal_the_connect_prompt():
    from src.flash_service import is_mtk_usb_search_line

    # The exact sentences Port.py / meta.py print while the handshake window
    # is open. Missing these is why nobody was asked to plug the player in.
    assert is_mtk_usb_search_line(
        "Status: Waiting for PreLoader VCOM, please connect mobile"
    )
    assert is_mtk_usb_search_line(
        "Status: Waiting for PreLoader VCOM, please reconnect mobile to preloader mode"
    )
    assert is_mtk_usb_search_line(
        "Status: Waiting for PreLoader VCOM, please reconnect mobile/iot device to brom mode"
    )
    # Starting the backend, or holding the system awake, is not an ask.
    assert not is_mtk_usb_search_line("Initializing mtkclient...")
    assert not is_mtk_usb_search_line("Holding the system awake (install session)")


def test_the_connect_window_restarts_when_the_tool_asks_for_the_player():
    from src.flash_service import PHASE_CONNECT, STEP_WAITING, FlashWorker

    _app()
    w = FlashWorker("dummy.zip", method="mtk", model="Y2")
    steps = []
    w.step_changed.connect(steps.append)
    try:
        # The spawned backend arms its no-progress window first.
        w._start_attempt_watchdog()
        spawned = w._attempt_watchdog
        assert spawned is not None

        # Once it is genuinely waiting, the ask goes up and the minute belongs
        # to the ask instead of to the import that happened before it.
        w._classify_mtk_output(
            "Status: Waiting for PreLoader VCOM, please connect mobile"
        )
        assert w._tool_wait_phase == PHASE_CONNECT
        assert w._attempt_watchdog is not None
        assert w._attempt_watchdog is not spawned
        assert STEP_WAITING in steps, "the connect ask must reach the window"
    finally:
        w._stop_attempt_watchdog()


def test_the_last_partition_write_is_the_completion_signal():
    from src.flash_service import partition_write_complete

    assert partition_write_complete("Written ANDROID (12/12)")
    assert partition_write_complete("[LOG] Written MBR (1/1)")
    assert not partition_write_complete("Written ANDROID (11/12)")
    # Starting a partition is not finishing it.
    assert not partition_write_complete("Writing ANDROID (9/12): android.img")


def test_the_app_records_completion_from_the_partition_lines():
    from src.flash_service import FlashWorker

    _app()
    w = FlashWorker("dummy.zip", method="mtk", model="Y2")
    assert not getattr(w, "_all_partitions_written", False)
    w._handle_mtk_subprocess_log("Written ANDROID (11/12)")
    assert not getattr(w, "_all_partitions_written", False)
    w._handle_mtk_subprocess_log("Written ANDROID (12/12)")
    assert getattr(w, "_all_partitions_written", False)


def test_mtkclient_failures_do_not_ask_the_user_to_power_cycle():
    source = (ROOT / "src" / "ui" / "main_window.py").read_text(encoding="utf-8")

    assert "asking for a power cycle" not in source
    assert 'prompt_key="install_disconnect_guidance_fmt"' not in source
