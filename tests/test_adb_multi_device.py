"""Two players share one serial: the journey prompts, background code uses transports.

Every Innioasis player reports the same ADB serial (``0123456789ABCDEF``), so
with two attached ``adb -s <serial>`` refuses every command. The guided
journey must therefore ask the user to disconnect down to one player, while
the passive sync — which cannot prompt — addresses each player by the
transport id from ``adb devices -l``.
"""

import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])

TWO_DEVICES = (
    "List of devices attached\n"
    "0123456789ABCDEF       device usb:17956864X product:Y1 model:Y1 device:Y1 transport_id:1\n"
    "0123456789ABCDEF       device usb:17891328X product:Y2 model:Y2 device:Y2 transport_id:2\n"
    "\n"
)
ONE_DEVICE = (
    "List of devices attached\n"
    "0123456789ABCDEF       device usb:17956864X product:Y1 model:Y1 device:Y1 transport_id:1\n"
    "\n"
)
NO_DEVICES = "List of devices attached\n\n"


def _proc(stdout: str):
    p = Mock()
    p.returncode = 0
    p.stdout = stdout
    p.stderr = ""
    return p


def test_device_list_separates_two_players_sharing_one_serial():
    from src import adb

    with patch.object(adb, "_run_adb", return_value=_proc(TWO_DEVICES)):
        devs = adb.adb_device_list("/fake/adb")
        assert [d.transport_id for d in devs] == ["1", "2"]
        assert [d.model for d in devs] == ["Y1", "Y2"]
        assert {d.serial for d in devs} == {"0123456789ABCDEF"}
        # One entry per attached player: this count is what the prompt keys on.
        assert adb.adb_devices("/fake/adb") == ["0123456789ABCDEF"] * 2
        # Labels keep showing the serial the user recognises.
        assert str(devs[0]) == "0123456789ABCDEF"


def test_selector_prefers_transport_and_falls_back_to_serial():
    from src import adb

    assert adb._target(adb.AdbDevice("S", "7")) == ["-t", "7"]
    assert adb._target(adb.AdbDevice("S")) == ["-s", "S"]
    assert adb._target("S") == ["-s", "S"]


def test_wait_reports_multiple_once_then_accepts_the_single_player():
    from src import adb

    outputs = iter([TWO_DEVICES, TWO_DEVICES, ONE_DEVICE])
    with patch.object(adb, "_run_adb", side_effect=lambda *a, **k: _proc(next(outputs))):
        seen: list[int] = []
        serial = adb.wait_for_adb_device(
            "/fake/adb", timeout=5, poll_interval=0.01, multi_cb=seen.append
        )
    assert serial == "0123456789ABCDEF"
    # Reported on entering the state — not once per poll, and not skipped.
    assert seen == [2]


def test_wait_single_device_and_empty_input_stay_silent():
    from src import adb

    with patch.object(adb, "_run_adb", return_value=_proc(ONE_DEVICE)):
        seen: list[int] = []
        serial = adb.wait_for_adb_device(
            "/fake/adb", timeout=5, poll_interval=0.01, multi_cb=seen.append
        )
    assert serial == "0123456789ABCDEF"
    assert seen == []

    # Empty enumeration and a crashing binary both keep watching until timeout.
    with patch.object(adb, "_run_adb", return_value=_proc(NO_DEVICES)):
        assert adb.wait_for_adb_device("/fake/adb", timeout=0.01, poll_interval=0.01) is None
    with patch.object(adb, "_run_adb", side_effect=RuntimeError("adb missing")):
        assert adb.wait_for_adb_device("/fake/adb", timeout=0.01, poll_interval=0.01) is None
    # No callback at all must be safe as well.
    with patch.object(adb, "_run_adb", return_value=_proc(TWO_DEVICES)):
        assert adb.wait_for_adb_device("/fake/adb", timeout=0.01, poll_interval=0.01) is None


def test_flash_stage_pushes_through_when_one_player_and_watches_when_two():
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = _app()
    logs: list[str] = []

    # Exactly one player: found immediately, no watch, no prompt.
    page = FlashPage()
    page.show()
    app.processEvents()
    try:
        with patch("src.adb.find_adb_binary", return_value="/fake/adb"), \
                patch("src.adb.adb_devices", return_value=["0123456789ABCDEF"]), \
                patch.object(page, "_on_adb_device_found") as found:
            page.start_rockbox_themes_stage(model="Y1", automatic=True, log_cb=logs.append)
            found.assert_called_once_with("0123456789ABCDEF")
            assert getattr(page, "_adb_watch_worker", None) is None
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()

    # Two players sharing the serial: no immediate push — the watch starts
    # and the disconnect prompt owns the copy until one player remains.
    page = FlashPage()
    page.show()
    app.processEvents()
    try:
        with patch("src.adb.find_adb_binary", return_value="/fake/adb"), \
                patch("src.adb.adb_devices", return_value=["0123456789ABCDEF"] * 2), \
                patch("src.theme_pack.AdbWatchWorker") as worker_cls, \
                patch.object(page, "_on_adb_device_found") as found:
            page.start_rockbox_themes_stage(model="Y1", automatic=True, log_cb=logs.append)
            found.assert_not_called()
            worker_cls.assert_called_once()
            worker = worker_cls.return_value
            worker.multiple.connect.assert_called_once_with(page._on_adb_multiple_devices)
            worker.found.connect.assert_called_once_with(page._on_adb_device_found)
            assert any("asking the user to disconnect" in line for line in logs)

            # The worker reports the state; the card asks the user, and the
            # countdown ticks keep that copy on screen.
            page._on_adb_multiple_devices(2)
            prompt = tr("rockbox_stage_disconnect_extra")
            assert page.themes.multiple_players is True
            assert page._eta_label.text() == prompt
            page._on_adb_watch_tick()
            assert page._eta_label.text() == prompt
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


def test_found_after_multi_prompt_clears_the_state():
    from PySide6.QtCore import QTimer

    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    app.processEvents()
    try:
        page.themes.note_multiple()
        page._adb_watch_seconds = 30
        with patch.object(QTimer, "singleShot") as single_shot:
            page._on_adb_device_found("0123456789ABCDEF")
            single_shot.assert_called_once()
        assert page.themes.multiple_players is False
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


def test_wizard_refuses_to_adopt_a_passive_push_while_prompted():
    from src.theme_pack import Rockbox360pSetupWizard

    app = _app()
    wiz = Rockbox360pSetupWizard(model="Y1")
    try:
        wiz._stack.setCurrentIndex(1)
        wiz._adb_multi = True
        # The disconnect prompt owns the journey: adoption would push with
        # the ambiguous shared serial.
        assert wiz.maybe_adopt_passive_push("0123456789ABCDEF") is False
        assert wiz._adb_serial is None
    finally:
        wiz.close()
        app.processEvents()


def test_passive_worker_pushes_to_each_transport(tmp_path):
    """Background sync cannot prompt: it addresses every player by transport."""
    from src import adb
    from src import theme_sync as sync

    from PySide6.QtCore import QSettings

    app = _app()

    qs = QSettings(str(tmp_path / "sync.ini"), QSettings.Format.IniFormat)
    payload = tmp_path / "payload" / ".rockbox"
    (payload / "themes").mkdir(parents=True)
    (payload / "themes" / "a.cfg").write_text("x")

    devices = [
        adb.AdbDevice("0123456789ABCDEF", "1", "Y1"),
        adb.AdbDevice("0123456789ABCDEF", "2", "Y2"),
    ]
    pushed: list[tuple] = []
    started: list[tuple] = []
    finished: list[tuple] = []
    worker = sync.PassiveThemeSyncWorker(["Y1"], qs)
    worker.device_push_started.connect(lambda m, s: started.append((m, s)))
    worker.done.connect(lambda m, ok: finished.append((m, ok)))
    with patch.object(sync, "ensure_combined_payload", return_value=payload), \
            patch("src.adb.find_adb_binary", return_value="/fake/adb"), \
            patch("src.adb.adb_device_list", return_value=devices), \
            patch("src.adb.adb_find_all_rockbox_dirs", return_value=["/sdcard/.rockbox"]), \
            patch("src.adb.adb_push_merge",
                  side_effect=lambda *a, **k: (pushed.append(a), (True, ""))[1]), \
            patch.object(sync, "find_removable_rockbox_dirs", return_value=[]):
        worker.run()

    # Both players reached despite sharing one serial.
    assert len(pushed) == 2
    assert [d.transport_id for _, d, *_ in pushed] == ["1", "2"]
    assert all(str(d) == "0123456789ABCDEF" for _, d, *_ in pushed)
    assert finished and finished[0] == ("Y1", True)
    # Progress/adoption labels still show the familiar serial.
    assert started and all(s == "0123456789ABCDEF" for _, s in started)
