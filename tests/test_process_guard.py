"""The flash tool is only allowed to run inside the install window.

SP Flash Tool's console search loop and an mtkclient session both hold the USB
port, so one left behind after a finished, failed or abandoned install makes the
next attempt fail with "no device". These tests cover the matching rules (which
must never touch an editor or this app itself), the window gating, and the
cleanup that runs when the window closes.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import process_guard  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_window():
    """Every test starts outside an install window and ends outside one."""
    process_guard.end_install_session(reason="test setup", sweep=False)
    yield
    process_guard.end_install_session(reason="test teardown", sweep=False)


def test_recognises_the_flash_tool_and_mtkclient_cli():
    assert process_guard._tool_invocation("/opt/sp/flash_tool -c download") == "flash_tool"
    assert process_guard._tool_invocation(r'"C:\SP Flash Tool\flash_tool.exe" -c download') == "flash_tool.exe"
    assert process_guard._tool_invocation("/opt/sp/flash_tool.sh -c download") == "flash_tool.sh"
    assert process_guard._tool_invocation(
        "python3 /repo/vendor/mtkclient/mtk.py wo 0x0 0x100 boot.img"
    ) == "mtk.py"
    assert process_guard._tool_invocation("/usr/local/bin/mtkclient printgpt") == "mtkclient"
    # The Linux payload is started through a shell, so the process list shows
    # the shell; the script it runs is the flash tool.
    assert process_guard._tool_invocation("/bin/bash ./flash_tool.sh -c download") == "flash_tool.sh"
    assert process_guard._tool_invocation('sh "C:\\SP Flash Tool\\flash_tool.exe" -c d') == "flash_tool.exe"
    assert process_guard._tool_invocation("python -m src.app --flash-cli /tmp/extracted /tmp/scatter.txt MT6572") == "flash-cli"
    assert process_guard._tool_invocation("/Applications/InnioasisUpdater.app/Contents/MacOS/InnioasisUpdater --flash-cli /tmp/extracted /tmp/scatter.txt MT6572") == "flash-cli"


def test_never_matches_a_path_that_merely_mentions_the_tool():
    """An editor, a grep or this app itself must survive every sweep."""
    assert process_guard._tool_invocation("vim vendor/mtkclient/mtkclient/Library/utils.py") == ""
    assert process_guard._tool_invocation("grep -rn mtkclient .") == ""
    assert process_guard._tool_invocation("/Applications/InnioasisUpdater.app/Contents/MacOS/InnioasisUpdater") == ""
    assert process_guard._tool_invocation("python -m pytest tests/test_process_guard.py") == ""
    assert process_guard._tool_invocation("") == ""
    # ``mtk_test.py`` is not ``mtk.py``.
    assert process_guard._tool_invocation("python debug/mtk_test.py readback") == ""


def test_snapshot_drops_our_own_process_and_its_ancestors():
    snapshot = [
        (os.getpid(), 1, "/usr/bin/flash_tool"),
        (4242, 1, "/opt/sp/flash_tool -c download"),
    ]
    found = process_guard.stray_tool_processes(snapshot=snapshot)
    assert [item["pid"] for item in found] == [4242]
    assert found[0]["tool"] == "flash_tool"


def test_sweep_refuses_while_an_install_is_running(monkeypatch):
    killed = []
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [(4242, 1, "flash_tool -c download")])
    monkeypatch.setattr(process_guard, "_terminate", lambda pid, tool: killed.append(pid) or True)

    process_guard.begin_install_session(reason="test")
    assert process_guard.install_session_active() is True
    # The running install owns its tool: sweeping now would abort a healthy flash.
    assert process_guard.sweep_stray_tools() == []
    assert killed == []

    removed = process_guard.end_install_session(reason="test")
    assert process_guard.install_session_active() is False
    assert [item["pid"] for item in removed] == [4242]
    assert killed == [4242]


def test_sweep_can_be_forced_for_a_hand_over(monkeypatch):
    killed = []
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [(7, 1, "flash_tool.sh -c download")])
    monkeypatch.setattr(process_guard, "_terminate", lambda pid, tool: killed.append(pid) or True)
    process_guard.begin_install_session(reason="test")
    assert [item["pid"] for item in process_guard.sweep_stray_tools(force=True)] == [7]
    assert killed == [7]


def test_report_names_what_was_stopped(monkeypatch):
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [(9, 1, "flash_tool -c download")])
    monkeypatch.setattr(process_guard, "_terminate", lambda pid, tool: True)
    removed = process_guard.sweep_stray_tools()
    text = process_guard.describe(removed)
    assert "flash_tool" in text and "9" in text
    assert process_guard.describe([]) == ""


def test_a_process_that_survives_is_reported_as_kept(monkeypatch, caplog):
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [(21, 1, "flash_tool")])
    monkeypatch.setattr(process_guard, "_terminate", lambda pid, tool: False)
    assert process_guard.sweep_stray_tools() == []


def test_sweep_aborts_when_install_session_begins_during_snapshot(monkeypatch):
    """Regression test for SP_EXIT_1: a slow process snapshot that finishes after
    a new install session has started must not kill the newly launched flash_tool.exe.
    """
    killed = []

    def slow_snapshot():
        # Simulate an install session starting while Get-CimInstance is running
        process_guard.begin_install_session(reason="install started mid-snapshot")
        return [(5940, 1, r'"C:\SP_Flash_Tool\flash_tool.exe" -i "C:\download.xml"')]

    monkeypatch.setattr(process_guard, "process_snapshot", slow_snapshot)
    monkeypatch.setattr(process_guard, "_terminate", lambda pid, tool: killed.append(pid) or True)

    assert process_guard.sweep_stray_tools() == []
    assert killed == []


def test_sweep_never_kills_registered_active_sp_session(monkeypatch):
    """Active SP Flash Tool Popen processes registered in flash_service are never killed by non-force sweeps."""
    from src import flash_service

    killed = []
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [(7788, 1, "flash_tool.exe -i download.xml")])
    monkeypatch.setattr(process_guard, "_terminate", lambda pid, tool: killed.append(pid) or True)

    flash_service.register_sp_session(7788)
    try:
        assert process_guard.sweep_stray_tools() == []
        assert killed == []
    finally:
        flash_service.unregister_sp_session(7788)


