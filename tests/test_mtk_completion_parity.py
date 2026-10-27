"""Tests for MTKClient flashing parity with the Chinese market release (InnioasisUpdater.app 1.1.7).

Verifies:
- Flash success does not spawn external mtk.py reset (which sends BROM handshake and bricks rebooting devices).
- Completion signaling in _flash_via_mtkclient emits finished.emit(True, "") directly on returncode 0 or [RESULT] 1.
- Preloader match check (_legacy_preloader_mismatch_targets) skips boot1 write when preloader is already identical.
- Partition writes proceed directly to _end_mtk_run_successfully without watchdog sleep delays.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QCoreApplication

from src.flash_service import FlashWorker, STEP_DONE
from src import mtk_api


def _app():
    return QCoreApplication.instance() or QCoreApplication(["--platform", "offscreen"])


def test_mtk_flash_success_does_not_fire_mtk_reset(tmp_path):
    """Outer _flash_via_mtkclient must not fire mtk reset upon completion."""
    _app()
    worker = FlashWorker("dummy.zip", method="mtk", model="Y1")
    worker._force_subprocess_cli = True

    finished_events = []
    worker.finished.connect(lambda ok, msg: finished_events.append((ok, msg)))

    fake_proc = MagicMock()
    fake_proc.stdout.readline.side_effect = [
        "[PROGRESS] 100\n",
        "[RESULT] 1 \n",
        "",
    ]
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    fake_proc.poll.return_value = 0

    with patch("subprocess.Popen", return_value=fake_proc), \
         patch("src.mtk_api.fire_mtk_reset") as mock_reset:
        worker._flash_via_mtkclient(str(tmp_path), "MT6572_Android_scatter.txt")

        mock_reset.assert_not_called()
        assert finished_events == [(True, "")]


def test_mtk_result_success_wins_over_expected_usb_reenumeration(tmp_path):
    """The player may reboot before the child exits after a successful flash."""
    _app()
    worker = FlashWorker("dummy.zip", method="mtk", model="Y1")
    worker._force_subprocess_cli = True
    finished_events = []
    worker.finished.connect(lambda ok, msg: finished_events.append((ok, msg)))

    fake_proc = MagicMock()
    fake_proc.stdout.readline.side_effect = [
        "[STEP] Write\n",
        "[RESULT] 1 \n",
        "",
    ]
    fake_proc.wait.return_value = 1
    fake_proc.returncode = 1
    fake_proc.poll.return_value = 1

    with patch("subprocess.Popen", return_value=fake_proc), \
         patch("src.flash_service.is_mtk_device_connected", return_value=False):
        worker._flash_via_mtkclient(str(tmp_path), "MT6572_Android_scatter.txt")

    assert finished_events == [(True, "")]


def test_mtk_flash_clean_exit_no_result_line(tmp_path):
    """Clean exit 0 with no [RESULT] line treats as success without mtk.py reset."""
    _app()
    worker = FlashWorker("dummy.zip", method="mtk", model="Y1")
    worker._force_subprocess_cli = True

    finished_events = []
    worker.finished.connect(lambda ok, msg: finished_events.append((ok, msg)))

    fake_proc = MagicMock()
    fake_proc.stdout.readline.side_effect = [""]
    fake_proc.wait.return_value = 0
    fake_proc.returncode = 0
    fake_proc.poll.return_value = 0

    with patch("subprocess.Popen", return_value=fake_proc), \
         patch("src.mtk_api.fire_mtk_reset") as mock_reset:
        worker._flash_via_mtkclient(str(tmp_path), "MT6572_Android_scatter.txt")

        mock_reset.assert_not_called()
        assert finished_events == [(True, "")]


def test_preloader_mismatch_check_skips_when_matching(tmp_path):
    """When boot1 on device matches package image, preloader write is skipped."""
    _app()
    worker = FlashWorker("dummy.zip", method="mtk", model="Y1")

    preloader_bin = tmp_path / "preloader.bin"
    preloader_data = b"MMM\x01" + b"\x00" * 2044
    preloader_bin.write_bytes(preloader_data)

    mock_da = MagicMock()
    mock_mtk = MagicMock(daloader=mock_da)
    mock_mtk.config.last_error = None

    # Test direct _legacy_preloader_mismatch_targets
    with patch.object(worker, "_read_flash_blob", return_value=preloader_data):
        mismatches = worker._legacy_preloader_mismatch_targets(mock_mtk, preloader_bin, ("boot1",))
        assert mismatches == [], "Expected empty mismatch list when boot1 matches"

    # Now verify _write_mtk_preloader skips writing when _legacy_preloader_mismatch_targets returns []
    with patch.object(worker, "_legacy_preloader_mismatch_targets", return_value=[]):
        worker._write_mtk_preloader(MagicMock(), mock_mtk, str(preloader_bin))
        mock_da.sdmmc_write_image.assert_not_called()


def test_preloader_mismatch_check_writes_when_different(tmp_path):
    """When boot1 on device differs or cannot be read, preloader write proceeds."""
    _app()
    worker = FlashWorker("dummy.zip", method="mtk", model="Y1")

    preloader_bin = tmp_path / "preloader.bin"
    preloader_data = b"MMM\x01" + b"\x00" * 2044
    preloader_bin.write_bytes(preloader_data)

    mock_da = MagicMock()
    mock_da.readflash.return_value = b"\x00" * 2048
    mock_da.sdmmc_write_image.return_value = True
    mock_mtk = MagicMock(daloader=mock_da)
    mock_mtk.config.last_error = None

    mismatches = worker._legacy_preloader_mismatch_targets(mock_mtk, preloader_bin, ("boot1",))
    assert mismatches == ["boot1"], "Expected boot1 in mismatch list when content differs"

    with patch.object(worker, "_legacy_preloader_mismatch_targets", return_value=["boot1"]):
        worker._write_mtk_preloader(MagicMock(), mock_mtk, str(preloader_bin))
        mock_da.sdmmc_write_image.assert_called_once()


def test_end_mtk_run_successfully_emits_completion_directly():
    """_end_mtk_run_successfully emits STEP_DONE, 100%, and finished.emit(True, "")."""
    _app()
    worker = FlashWorker("dummy.zip", method="mtk", model="Y1")

    steps = []
    progress_vals = []
    finished = []
    worker.step_changed.connect(steps.append)
    worker.progress.connect(progress_vals.append)
    worker.finished.connect(lambda ok, msg: finished.append((ok, msg)))

    mock_mtk = MagicMock()
    worker._end_mtk_run_successfully(mock_mtk)

    assert STEP_DONE in steps
    assert 100.0 in progress_vals
    assert finished == [(True, "")]
    # Port close called with reset=not IS_WINDOWS
    mock_mtk.port.close.assert_called_once()
