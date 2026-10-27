from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

from PySide6.QtWidgets import QApplication

from src.adb import adb_find_all_rockbox_dirs, adb_push_merge
from src.theme_pack import AdbPushWorker


def _ensure_app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_adb_push_merge_granular_progress(tmp_path):
    # Setup test directory with files across subdirectories
    theme_dir = tmp_path / "test_theme"
    theme_dir.mkdir()
    (theme_dir / "fonts").mkdir()
    (theme_dir / "wps").mkdir()

    # Create 60 files (30 in root, 20 in fonts, 10 in wps)
    for i in range(30):
        (theme_dir / f"root_{i}.cfg").write_text("cfg")
    for i in range(20):
        (theme_dir / "fonts" / f"font_{i}.fnt").write_text("fnt")
    for i in range(10):
        (theme_dir / "wps" / f"wps_{i}.wps").write_text("wps")

    progress_reports = []

    def progress_cb(curr, total):
        progress_reports.append((curr, total))

    # Mock the progress-aware adb push helper to simulate successful batch pushes.
    def fake_push(adb, serial, local_path, remote_path, **kwargs):
        return subprocess.CompletedProcess(args=[adb, "push"], returncode=0, stdout="", stderr="")

    with patch("src.adb._run_adb", return_value=subprocess.CompletedProcess([], 0, "__ADB_MKDIR_OK__", "")), patch(
        "src.adb._run_adb_push_with_progress", side_effect=fake_push
    ) as mock_push:
        ok, err = adb_push_merge(
            "adb",
            "TEST_SERIAL",
            theme_dir,
            "/storage/sdcard0/.rockbox",
            progress_cb=progress_cb,
        )

    assert ok is True
    assert err == ""
    assert len(progress_reports) > 2  # Not just (0, 60) and (60, 60)
    assert progress_reports[0] == (0, 60)
    assert progress_reports[-1] == (60, 60)
    # Check that progress strictly increases
    currs = [r[0] for r in progress_reports]
    assert sorted(currs) == currs
    assert mock_push.call_count == 4  # root has two batches; fonts and WPS one each


def test_adb_push_merge_cancellation(tmp_path):
    theme_dir = tmp_path / "test_theme"
    theme_dir.mkdir()
    for i in range(30):
        (theme_dir / f"file_{i}.txt").write_text("txt")

    cancelled = False

    def cancel_check():
        nonlocal cancelled
        return cancelled

    progress_reports = []

    def progress_cb(curr, total):
        nonlocal cancelled
        progress_reports.append(curr)
        if curr > 0:
            cancelled = True

    def fake_push(adb, serial, local_path, remote_path, **kwargs):
        return subprocess.CompletedProcess(args=[adb, "push"], returncode=130, stdout="", stderr="Theme transfer cancelled")

    with patch("src.adb._run_adb_push_with_progress", side_effect=fake_push):
        ok, err = adb_push_merge(
            "adb",
            "TEST_SERIAL",
            theme_dir,
            "/storage/sdcard0/.rockbox",
            progress_cb=progress_cb,
            cancel_cb=cancel_check,
        )

    assert ok is False
    assert "cancelled" in err.lower()
    assert progress_reports[-1] < 30


def test_adb_push_merge_batch_failure_fallback(tmp_path):
    theme_dir = tmp_path / "test_theme"
    theme_dir.mkdir()
    for i in range(5):
        (theme_dir / f"file_{i}.txt").write_text("txt")

    progress_reports = []

    # Simulate batch push failure, then individual push success.
    calls = []

    def fake_push(adb, serial, local_path, remote_path, **kwargs):
        calls.append(local_path)
        if isinstance(local_path, list):
            return subprocess.CompletedProcess(args=[adb, "push"], returncode=1, stdout="", stderr="batch error")
        return subprocess.CompletedProcess(args=[adb, "push"], returncode=0, stdout="", stderr="")

    with patch("src.adb._run_adb", return_value=subprocess.CompletedProcess([], 0, "__ADB_MKDIR_OK__", "")), patch(
        "src.adb._run_adb_push_with_progress", side_effect=fake_push
    ):
        ok, err = adb_push_merge(
            "adb",
            "TEST_SERIAL",
            theme_dir,
            "/storage/sdcard0/.rockbox",
            progress_cb=lambda c, t: progress_reports.append((c, t)),
        )

    assert ok is True
    assert progress_reports[-1] == (5, 5)
    assert len(calls) == 6  # one batch, followed by five individual retries


def test_adb_find_all_rockbox_dirs_deduplication():
    # If device outputs symlink aliases, pwd -P resolution deduplicates them
    shell_output = "/storage/sdcard0/.rockbox\n/storage/sdcard0/.rockbox\n"
    proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=shell_output, stderr="")

    with patch("src.adb._run_adb", return_value=proc):
        dirs = adb_find_all_rockbox_dirs("adb", "SERIAL1")

    assert dirs == ["/storage/sdcard0/.rockbox"]


def test_flash_page_theme_progress_granularity():
    app = _ensure_app()
    from src.ui.flash_page import FlashPage
    page = FlashPage()
    page.show()
    try:
        app.processEvents()
        page._theme_last_logged_curr = 0
        logged = []
        page._log_theme = lambda msg: logged.append(msg)

        # Simulate non-multiple-of-25 batch arrivals
        page._on_theme_push_progress(18, 100)
        assert "18%" in page._eta_label.text()

        page._on_theme_push_progress(36, 100)
        assert "36%" in page._eta_label.text()
        assert any("36%" in line for line in logged)

        page._on_theme_push_progress(72, 100)
        assert page._eta_label.text()
        assert any("72%" in line for line in logged)

        page._on_theme_push_progress(100, 100)
        assert page._eta_label.text()
        assert any("100%" in line for line in logged)
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()
