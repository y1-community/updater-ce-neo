"""SP Flash Tool's "All command exec done!" line is a success marker.

The tool logs it (mixed case) once the flash command returns without an
exception. The internal-log check used to match only lowercase text, so a
real successful run was reported as failed.
"""

import threading
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


def test_main_window_finishes_the_install_on_all_command_exec():
    """flash_tool's completion line ends a run whose console process never exits.

    Some SP Flash Tool builds keep the process alive after the line, so the
    worker never reports a result and the card stayed at the last write
    percentage (95%) with no completion screen. With no themes/APK stage to
    follow, the window must finish the journey itself: 100% and the ordinary
    completion screen.
    """
    from src.state import FlashState
    from src.ui.main_window import _PAGE_FLASH

    app, win = _make_test_window()
    try:
        win.settings.setValue("donation_ui_disabled", False)
        win.settings.setValue("donation_install_prompt_disabled", False)
        win._package_path = "dummy.zip"
        win._package_model = "Y1"
        win._package_name = "Stock"
        win._used_sp_backend = True
        win._on_progress(95)

        # Simulate MainWindow having also declared a failure too early.
        win._install_failed = True
        win.sm.force_state(FlashState.S5_USB_DISCONNECTED)

        win._handle_backend_line("[SP] All command exec done!")
        app.processEvents()

        assert win._saw_all_command_exec is True
        assert win._sp_console_completion_done is True
        assert win._install_failed is False
        assert win._install_complete is True
        assert win.sm.state is FlashState.S5_COMPLETE
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert win._flash_page._progress_bar.value() == 100
        assert win._flash_page._flash_banner.text() == "Install complete"
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_a_follow_up_themes_stage_owns_the_end_of_the_run(monkeypatch):
    """A configured Rockbox themes stage still starts instead of completing."""
    app, win = _make_test_window()
    try:
        win._package_path = "rockbox-y1.zip"
        win._package_model = "Y1"
        win._package_name = "Rockbox 0.5"
        win._used_sp_backend = True
        win._install_complete = False
        win._rockbox_themes_required = lambda: (True, "Y1")
        monkeypatch.setattr(
            "src.theme_sync.themes_auto_install_enabled", lambda *a, **k: True
        )

        win._handle_backend_line("[SP] All command exec done!")
        app.processEvents()

        assert win._sp_console_completion_done is False
        assert win._install_complete is False
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_a_late_failure_cannot_replace_a_finished_install():
    """After the completion line, the run's own exit result is not a failure."""
    app, win = _make_test_window()
    try:
        win.settings.setValue("donation_ui_disabled", False)
        win.settings.setValue("donation_install_prompt_disabled", False)
        win._package_path = "dummy.zip"
        win._package_model = "Y1"
        win._package_name = "Stock"
        win._used_sp_backend = True
        win._handle_backend_line("[SP] All command exec done!")
        app.processEvents()

        shown = []
        win._handle_flash_failure = lambda code: shown.append(code)
        win._on_flash_finished(False, "SP_EXIT_1")
        app.processEvents()

        assert shown == []
        assert win._install_complete is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_a_log_tail_after_success_only_feeds_diagnostics(tmp_path):
    """The log monitor must not move the card once the run has a result.

    It reads QT_FLASH_TOOL.log a batch at a time, so a large package leaves it
    one pass behind and it replays the final "100% of image data" line after
    the worker has reported success. That replay used to emit STEP_WRITE and
    put the install card back up at 95%, hiding the completion screen.
    """
    launch = _write_run(
        tmp_path,
        "99% of image data has been sent (1.58G of 1.59G)\n"
        "100% of image data has been sent (1.59G of 1.59G)\n"
        "All command exec done!\n",
    )
    worker = fs.FlashWorker("pkg.zip", method="sp")
    worker._sp_outcome_decided = True
    steps = []
    forwarded = []
    worker.step_changed.connect(steps.append)
    # Spy on the sink the monitor forwards tool lines to; log_message itself
    # needs a Qt event loop to reach a receiver.
    worker._log = forwarded.append

    stop = threading.Event()
    monitor = threading.Thread(
        target=worker._monitor_sp_log_file,
        args=(stop, tmp_path, launch),
        daemon=True,
    )
    monitor.start()
    time.sleep(0.8)
    stop.set()
    monitor.join(timeout=3.0)

    assert not monitor.is_alive()
    assert steps == []
    # The lines themselves still reach Diagnostics.
    assert any("of image data has been sent" in line for line in forwarded)


def test_a_late_sp_tail_cannot_reopen_the_completion_screen():
    """A replayed tick after success must leave the finished card alone."""
    from src.state import FlashState

    app, win = _make_test_window()
    try:
        page = win._flash_page
        win._install_complete = True
        win.sm.force_state(FlashState.S5_COMPLETE)
        for donations_shown in (False, True):
            page.set_device_done(donations_shown=donations_shown)
            assert page._progress_bar.value() == 100

            win._on_step_changed(fs.STEP_WRITE)
            win._on_progress(95.0)

            assert page._complete is True
            assert page._progress_bar.value() == 100
            assert page._step_key == "step_done"
            # Donations on hides the card behind the appeal; donations off
            # keeps it. Neither may be replaced by the write view.
            assert page._progress_card.isHidden() is donations_shown
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_repeated_write_steps_do_not_rebuild_the_card():
    """SP reports a write step per percent; the card is built once.

    Each show_flashing() bumps the page generation and asks the window to
    refit, so rebuilding it for every percent of a multi-gigabyte write churned
    the frame and left the page surface under the card unpainted on Windows.
    """
    app, win = _make_test_window()
    try:
        page = win._flash_page
        win._install_complete = False
        win._step_now = fs.STEP_DETECT
        shown = []
        page.show_flashing = lambda: shown.append(1)
        page.set_device_flashing = lambda: None
        win._enter_flashing_state = lambda: None
        win._settings_page.set_method_enabled = lambda *_a, **_k: None

        for _ in range(4):
            win._on_step_changed(fs.STEP_WRITE)

        assert len(shown) == 1
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
        # This test is about the themes stage, not the keep-data cleanup
        # that now runs first for a Rockbox package.
        win._start_rockbox_cleanup_if_needed = lambda: False

        started = []
        monkeypatch.setattr(
            win,
            "_start_themes_stage",
            lambda model, donation_disabled, already_powered_on=False, automatic=False, after_firmware=False: started.append(
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
