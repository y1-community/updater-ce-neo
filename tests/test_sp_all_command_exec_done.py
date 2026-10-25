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


def test_a_failure_after_a_per_image_download_ok_is_not_success(tmp_path):
    """A later failure must win over an earlier image's "Download Ok"."""
    launch = _write_run(
        tmp_path,
        "Downloading images...\n"
        "1% of image data has been sent (7.1M of 711.4M)\n"
        "Download Ok\n"
        "Download failed: S_FT_DOWNLOAD_FAIL (4008)\n",
    )
    worker = fs.FlashWorker("pkg.zip", method="sp")
    ok, detail = worker._read_sp_log_result(launch, tmp_path)
    assert ok is False, detail
    assert "S_FT_DOWNLOAD_FAIL" in detail


def test_the_console_completion_line_cannot_override_a_logged_failure():
    """The log is the source of truth; the console line only fills a missing log.

    An SP run whose internal log ends in a failure code is the tool reporting a
    failed command. An earlier console "Flash complete!" must not turn that
    into a completed install.
    """
    assert fs.sp_log_result_is_success(True, "", False) is True
    assert fs.sp_log_result_is_success(False, "", True) is True
    assert fs.sp_log_result_is_success(False, "S_FT_DOWNLOAD_FAIL (4008)", True) is False
    assert fs.sp_log_result_is_success(False, "", False) is False


def test_a_final_success_after_an_earlier_error_is_success(tmp_path):
    """A retried command that ends in "Download Ok" is still a success."""
    launch = _write_run(
        tmp_path,
        "S_FT_FORMAT_FAIL (4010)\n"
        "Retrying...\n"
        "Download Ok\n"
        "All command exec done!\n",
    )
    worker = fs.FlashWorker("pkg.zip", method="sp")
    ok, detail = worker._read_sp_log_result(launch, tmp_path)
    assert ok is True, detail
    assert detail == ""
