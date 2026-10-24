"""SP Flash Tool's "All command exec done!" line is a success marker.

The tool logs it (mixed case) once the flash command returns without an
exception. The internal-log check used to match only lowercase text, so a
real successful run was reported as failed.
"""

import time
from pathlib import Path

import src.flash_service as fs


def _write_run(log_root: Path, text: str) -> float:
    run_dir = log_root / "20261024_SP_FT"
    run_dir.mkdir(parents=True)
    (run_dir / "QT_FLASH_TOOL.log").write_text(text, encoding="utf-8")
    return time.time() - 10


def test_all_command_exec_done_is_success(tmp_path):
    launch = _write_run(
        tmp_path,
        "DA version: 1.0.0.0\n"
        "All command exec done!\n",
    )
    worker = fs.FlashWorker("pkg.zip", method="sp")
    ok, detail = worker._read_sp_log_result(launch, tmp_path)
    assert ok is True, detail
    assert detail == ""


def test_console_all_command_exec_done_marks_complete():
    assert fs.is_sp_flash_complete_line("All command exec done!")
    assert fs.is_sp_flash_complete_line("  All command exec done!\n")
    assert not fs.is_sp_flash_complete_line("General command exec done!")
    assert fs.is_sp_flash_complete_line("Flash complete! Disconnect USB and reboot the device.")
    assert not fs.is_sp_flash_complete_line("Flash incomplete, retrying")


def test_error_without_success_line_still_fails(tmp_path):
    launch = _write_run(
        tmp_path,
        "S_DA_INIT_SYNC_ERROR (1093)\n",
    )
    worker = fs.FlashWorker("pkg.zip", method="sp")
    ok, detail = worker._read_sp_log_result(launch, tmp_path)
    assert ok is False
    assert "S_DA_INIT_SYNC_ERROR" in detail
