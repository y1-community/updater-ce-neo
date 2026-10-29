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
    assert fs.is_sp_flash_complete_line("all command exec")
    assert fs.is_sp_flash_complete_line("all commands exec")
    assert fs.is_sp_flash_complete_line("all command was exec successfully")
    assert not fs.is_sp_flash_complete_line("General command exec done!")
    assert fs.is_sp_flash_complete_line("Flash complete! Disconnect USB and reboot the device.")
    assert not fs.is_sp_flash_complete_line("Flash incomplete, retrying")


def test_diagnostics_classifies_all_command_exec():
    from src.diagnostics import DiagnosticsManager, CAT_SP
    dm = DiagnosticsManager()
    assert dm.classify_line("All command exec done!") == CAT_SP
    assert dm.classify_line("all command exec") == CAT_SP
    assert dm.classify_line("all commands exec") == CAT_SP
    assert dm.classify_line("Flash complete! Disconnect USB") == CAT_SP


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


def test_all_command_exec_followed_by_disconnect_line_is_success(tmp_path):
    """Diagnosed from real Windows run: 'All command exec done!' followed by 'Disconnect!'."""
    worker = fs.FlashWorker("pkg.zip", method="sp")
    worker._classify_sp_stdout("All command exec done!")
    assert worker._sp_flash_complete is True
    # The subsequent Disconnect! line emitted upon device reboot must be ignored
    worker._classify_sp_stdout("Disconnect!")
    assert worker._is_disconnected is False

    # And finish_sp_failure must override any error detail if _sp_flash_complete is set
    completed = []
    worker.finished.connect(lambda ok, detail: completed.append((ok, detail)))
    worker._finish_sp_failure("SP_EXIT_1")
    assert completed == [(True, "")]


def _make_test_window():
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    win = MainWindow()
    win.start_device_monitor = lambda: None
    win.service.start_device_monitor = lambda: None
    win.service.cancel_flash = lambda: None
    win.service.stop_device_monitor = lambda: None
    win._ensure_theme_sync = lambda: None
    return app, win


def test_main_window_catches_and_corrects_failure_on_all_command_exec():
    from src.state import FlashState

    app, win = _make_test_window()
    try:
        win._package_path = "dummy.zip"
        win._package_model = "Y1"
        win._package_name = "Stock"

        # Simulate MainWindow having declared a failure
        win._install_failed = True
        win.sm.force_state(FlashState.S5_USB_DISCONNECTED)

        # The console line only records completion; the worker's final result
        # decides success, so the window must not jump to the success state.
        win._handle_backend_line("[SP] All command exec done!")
        assert win._saw_all_command_exec is True
        assert win._install_failed is False
        assert win.sm.state is not FlashState.S5_COMPLETE
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_rockbox_flow_on_windows_sp_starts_automatically(monkeypatch):
    monkeypatch.setattr("src.paths.IS_WINDOWS", True)
    app, win = _make_test_window()
    try:
        win._package_path = "rockbox.zip"
        win._package_model = "Y1"
        win._package_name = "Rockbox 0.5"
        win._used_sp_backend = True

        started = []
        monkeypatch.setattr(
            win,
            "_start_themes_stage",
            lambda model, donation_disabled, already_powered_on=False, automatic=False: started.append(
                (model, already_powered_on, automatic)
            ),
        )

        win._handle_flash_success()
        assert len(started) == 1
        model, powered_on, automatic = started[0]
        assert model == "Y1"
        assert powered_on is True
        assert automatic is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()
