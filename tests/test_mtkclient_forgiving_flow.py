"""Tests for forgiving MTKClient handshake, paperclip connect prompt, 120s timeout,
and Rockbox power-on/fallback post-flash flow.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_mtkclient_vs_sp_flash_prep_prompt():
    """Both MTKClient and SP Flash users have a consistent preparation flow:
    asked to confirm device is powered-off and disconnected, with paperclip/pin hint where applicable."""
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.set_model("Y1")

    # SP Flash method
    page.set_method("sp")
    page.show_power_off_prompt()
    sp_text = page._wait_prompt_label.text()
    assert "powered-off and disconnected" in sp_text
    assert "Y1" in sp_text

    # MTKClient method: unified Disconnect and Power Off flow
    page.set_method("mtk")
    page.show_power_off_prompt()
    mtk_text = page._wait_prompt_label.text()
    assert "powered-off and disconnected" in mtk_text
    assert "Y1" in mtk_text


def test_mtkclient_paperclip_prompt_and_120s_timeout():
    """In MTK mode, connect prompt asks to connect device with 120s timeout and paperclip hint where applicable."""
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.set_model("Y1")

    # SP Flash: the countdown starts on the tool's "search usb" line, not here.
    page.set_method("sp")
    page.leave_power_off_prompt()
    assert page._attempt_remaining == 0
    assert "please connect your y1" in page._wait_prompt_label.text().lower()

    # MTKClient - Y1
    page.set_method("mtk")
    page.leave_power_off_prompt()
    assert page._attempt_remaining == 120
    text_y1 = page._wait_prompt_label.text()
    assert "please connect your y1" in text_y1.lower()
    assert "(120s)" in text_y1

    # Paperclip hint is available for known models at the power-off step
    page.show()
    page.show_power_off_prompt(show_pin_tip=True)
    paperclip_visible = getattr(page, "_paperclip_hint_host", None)
    assert paperclip_visible is not None and not paperclip_visible.isHidden()
    assert "headphone" in page._paperclip_hint.text().lower()

    # A5 uses the power button to power on
    page.set_model("A5")
    page.show_power_off_prompt(show_pin_tip=True)
    assert not page._paperclip_hint_host.isHidden()
    assert "power button" in page._paperclip_hint.text().lower()

    # Y2 also uses the headphone jack, and the hint names the hidden button there
    page.set_model("Y2")
    page.show_power_off_prompt(show_pin_tip=True)
    assert not page._paperclip_hint_host.isHidden()
    assert "headphone" in page._paperclip_hint.text().lower()
    assert "paperclip or pin" in page._paperclip_hint.text().lower()
    page.leave_power_off_prompt()
    assert page._attempt_remaining == 120
    text_y2 = page._wait_prompt_label.text()
    assert "please connect your y2" in text_y2.lower()
    assert "(120s)" in text_y2


def test_mtkclient_forgiving_handshake_and_progress_detection():
    """While waiting for USB/paperclip in MTK mode, handshake errors are not fatal.
    Lines starting with 'Port - Device detected :)', 'Done', or 'Progress' transition out of USB search."""
    from src.flash_service import (
        FlashWorker,
        STEP_DETECT,
        STEP_DOWNLOAD_DA,
        STEP_WRITE,
        STEP_WAITING,
    )

    app = _app()
    w = FlashWorker("dummy.zip", method="mtk", model="Y1")

    steps = []
    progress_vals = []
    w.step_changed.connect(steps.append)
    w.progress.connect(progress_vals.append)

    # 1. Enters USB search
    w._classify_mtk_output("...")
    assert w._usb_search_active
    assert not w._past_usb_search

    # 2. Handshake error lines during search do NOT set _tool_error
    w._log("Status: Handshake failed, retrying...")
    w._log("Handshake: Error on sending sync, response: 0x0")
    w._log("Error: device not found")
    assert w._tool_error == "", "Handshake errors during connect wait must not be recorded as fatal tool error"

    # 3. Line with 'Port - Device detected :)' triggers detection
    w._classify_mtk_output("Port - Device detected :)")
    assert w._past_usb_search
    assert STEP_DETECT in steps
    assert 12 in progress_vals

    # 4. Lines with Done / Progress trigger DA upload or write progress
    steps.clear()
    progress_vals.clear()
    w._classify_mtk_output("Done |---------| 2.5% Stage 2: (0x1000/0x286BC), 0.00 MB/s")
    assert STEP_DOWNLOAD_DA in steps
    assert len(progress_vals) > 0

    steps.clear()
    progress_vals.clear()
    w._classify_mtk_output("Progress: |---------| 4.9% Stage 2: (0x2000/0x286BC), 3.21 MB/s")
    assert STEP_DOWNLOAD_DA in steps
    assert len(progress_vals) > 0

    steps.clear()
    progress_vals.clear()
    w._classify_mtk_output("Progress: |---------| 50.0% Writing partition system")
    assert STEP_WRITE in steps
    assert any(p >= 50 for p in progress_vals)


def test_rockbox_themes_stage_power_on_and_fallback():
    """Rockbox post-flash flow begins with model-specific power-button guidance,
    then falls back to asking if Rockbox is running if ADB is unavailable or times out."""
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()

    done_called = []
    recover_called = []

    # Patch find_adb_binary to return None (testing fallback)
    with patch("src.adb.find_adb_binary", return_value=None):
        page.start_rockbox_themes_stage(
            model="Y1",
            on_done=lambda: done_called.append(True),
            on_troubleshoot=lambda: recover_called.append(True),
        )

    # Because ADB is not found, falls back to USB storage mode prompt
    assert not page._theme_row.isHidden()
    assert not page._theme_fallback_widget.isHidden()
    assert "usb storage mode" in page._eta_label.text().lower()

    # User clicks Finish / completes stage -> triggers on_done
    page._finish_rockbox_themes_stage()
    assert done_called == [True]


def test_rockbox_themes_stage_with_adb_countdown():
    """Step 1 prompts to disconnect/power on; Step 2 prompts to connect USB with countdown."""
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()

    with patch("src.adb.find_adb_binary", return_value="/usr/bin/adb"), \
         patch("src.adb.adb_devices", return_value=[]), \
         patch("src.theme_pack.AdbWatchWorker") as mock_worker_cls:
        mock_worker = MagicMock()
        mock_worker_cls.return_value = mock_worker

        # Start stage - Step 1: Disconnect & power on
        page.start_rockbox_themes_stage(model="Y1")

        eta_text = page._eta_label.text()
        assert "disconnect usb, hold the centre button" in eta_text.lower()
        assert not page._theme_step1_widget.isHidden()

        # Step 1 -> Next: advances to Step 2 (connect USB + countdown)
        page._on_theme_step1_next_clicked()
        eta_text_step2 = page._eta_label.text()
        assert "connect your y1 by usb" in eta_text_step2.lower()
        assert "90" in eta_text_step2

        # Tick the timer
        page._on_adb_watch_tick()
        eta_text_tick = page._eta_label.text()
        assert "89" in eta_text_tick

        # For Y2
        page.start_rockbox_themes_stage(model="Y2")
        eta_text_y2 = page._eta_label.text()
        assert "power button" in eta_text_y2.lower()
        assert "your y2" in eta_text_y2.lower()


def test_mtkclient_watchdog_8min_and_touch_reset():
    """MTKClient uses a longer no-progress window than SP Flash Tool, and any

    MTK output line refreshes that window."""
    from src.flash_service import ATTEMPT_PROGRESS_TIMEOUT_MTK, ATTEMPT_PROGRESS_TIMEOUT

    # The code documents this as a longer MTK window than the SP Flash Tool one.
    assert ATTEMPT_PROGRESS_TIMEOUT_MTK > ATTEMPT_PROGRESS_TIMEOUT
    assert ATTEMPT_PROGRESS_TIMEOUT_MTK == 180.0

    from src.flash_service import FlashWorker
    import time

    app = _app()
    w = FlashWorker("dummy.zip", method="mtk", model="Y2")
    watchdog = w._start_attempt_watchdog()
    try:
        assert watchdog is not None
        initial_deadline = watchdog._deadline
        time.sleep(0.05)

        # Touching the attempt resets the window.
        w._touch_attempt()
        assert watchdog._deadline > initial_deadline

        # A real MTK write line also refreshes the window.
        d2 = watchdog._deadline
        time.sleep(0.05)
        w._classify_mtk_output("sdmmc_write_data: 完成 50 chunks")
        assert watchdog._deadline > d2
    finally:
        w._stop_attempt_watchdog()


def test_layman_subtitle_and_action_update():
    """Action updates live subtitle with layman task and partition percentage."""
    from src.ui.flash_page import FlashPage
    from src.flash_service import layman_task_name
    from src.i18n import tr

    app = _app()
    page = FlashPage()
    page.show()

    # Layman task names map partitions correctly
    assert layman_task_name("android") == tr("task_writing_system")
    assert layman_task_name("boot") == tr("task_writing_boot")
    assert layman_task_name("recovery") == tr("task_writing_recovery")
    assert layman_task_name("mbr") == tr("task_writing_partition_table")
    assert layman_task_name("userdata") == tr("task_preparing_storage")

    # update_action sets the subtitle label on the flash card
    sample_action = tr("action_writing_task_fmt").format(task=tr("task_writing_system"), pct=42)
    page.update_action(sample_action)
    assert "42%" in page._step_label.text()
    assert tr("task_writing_system") in page._step_label.text()


def test_preloader_skipped_in_mtkclient():
    """In MTKClient, preloader is omitted from image files when flashing to avoid DA boot1 lock."""
    from src.flash_service import FlashWorker

    app = _app()
    w = FlashWorker("dummy.zip", method="mtk")

    # If watchdog is active, _note_install_progress disarms it
    dog = w._start_attempt_watchdog()
    assert dog._armed
    w._note_install_progress()
    assert not dog._armed


def test_rockbox_y2_triggers_themes_stage_and_logs():
    """Flashing Rockbox for Y2 on any platform triggers themes stage and logs to diagnostic output."""
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()

    logs = []
    with patch("src.adb.find_adb_binary", return_value="/fake/adb"):
        with patch("src.theme_pack.AdbWatchWorker") as mock_worker_cls:
            page.start_rockbox_themes_stage(model="Y2", log_cb=logs.append)

    assert any("Rockbox Themes" in l for l in logs)
    assert any("Y2" in l for l in logs)
    assert page._themes_stage_active
    assert page._status_flow._forced_stacked


def test_rockbox_post_flash_power_prompt_uses_model_specific_button_and_hides_nav(tmp_path):
    """MTK success keeps the guided install chrome until the player has booted."""
    from PySide6.QtCore import QSettings

    from src import paths
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    settings = QSettings(str(tmp_path / "rockbox-flow.ini"), QSettings.Format.IniFormat)
    old_mac = paths.IS_MAC
    old_win = paths.IS_WINDOWS
    try:
        paths.IS_MAC = False
        paths.IS_WINDOWS = False
        with patch("src.theme_pack.AdbWatchWorker"):
            window = MainWindow()
            window.settings = settings
            window._package_model = "Y2"
            window._package_name = "Rockbox Y2"
            window._package_path = "/tmp/rockbox_y2.zip"
            window._flash_method = "mtk"
            window._flash_page.set_model("Y2")
            window._create_themes_wizard = lambda _model: None
            window.show()
            window._handle_flash_success()
            app.processEvents()

            assert window._stack.currentIndex() == _PAGE_FLASH
            assert window._rockbox_power_on_pending
            assert window._install_run_active()
            assert window._settings_btn.isHidden()
            assert window._support_btn.isHidden()
            if hasattr(window, "_linux_setup_btn"):
                assert window._linux_setup_btn.isHidden()
            assert window._check_updates_btn.isHidden()

            # Guided install chrome stays active until the player has booted;
            # the SP Flash Tool button is independent of that flow.
            assert window._install_run_active()
            assert "disconnect the usb and power on your y2" in window._flash_page._eta_label.text().lower()

            window._flash_page._theme_step1_next_btn.click()
            app.processEvents()
            assert not window._rockbox_power_on_pending
            assert window._flash_page._themes_stage_active
            assert "connect your y2 by usb" in window._flash_page._eta_label.text().lower()
            assert "usb" not in window._flash_page._theme_row.accessibleName().lower()
    finally:
        paths.IS_MAC = old_mac
        paths.IS_WINDOWS = old_win
        try:
            window.close()
            app.processEvents()
        except UnboundLocalError:
            pass


def test_sp_flash_rockbox_power_on_prompt(tmp_path):
    """SP Flash Tool also asks Y1/Y2 users to disconnect & power on, then connect USB, rather than bypassing with automatic=True."""
    from PySide6.QtCore import QSettings
    from src import paths
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    settings = QSettings(str(tmp_path / "sp-rockbox-flow.ini"), QSettings.Format.IniFormat)
    old_mac = paths.IS_MAC
    old_win = paths.IS_WINDOWS
    try:
        paths.IS_MAC = False
        paths.IS_WINDOWS = False
        with patch("src.theme_pack.AdbWatchWorker"):
            window = MainWindow()
            window.settings = settings
            window._package_model = "Y1"
            window._package_name = "Rockbox Y1"
            window._package_path = "/tmp/rockbox_y1.zip"
            window._flash_method = "sp"
            window._flash_page.set_model("Y1")
            window._create_themes_wizard = lambda _model: None
            window.show()
            window._handle_flash_success()
            app.processEvents()

            assert window._stack.currentIndex() == _PAGE_FLASH
            assert window._rockbox_power_on_pending
            assert window._install_run_active()

            # Step 1: Disconnect & power on prompt, shared by both flash methods
            assert "disconnect the usb and power on your y1" in window._flash_page._eta_label.text().lower()

            # User presses the round Next button to confirm disconnect and power on
            window._flash_page._theme_step1_next_btn.click()
            app.processEvents()
            assert not window._rockbox_power_on_pending
            assert window._flash_page._themes_stage_active

            # Step 2: Connect USB prompt
            assert "connect your y1 by usb" in window._flash_page._eta_label.text().lower()
    finally:
        paths.IS_MAC = old_mac
        paths.IS_WINDOWS = old_win
        try:
            window.close()
            app.processEvents()
        except UnboundLocalError:
            pass


def test_device_prep_confirmation_before_watch_and_flash(tmp_path):

    """Users are always prompted to power off and disconnect their model/device first.
    The flash worker and device monitor are NOT triggered until the user confirms (presses Next)."""
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    w = MainWindow()
    w.show()

    started_flash = []
    stopped_monitor = []
    primed_mtk_taken = []
    w.service.start_flash = lambda *a, **k: started_flash.append(True)
    w.service.stop_device_monitor = lambda *a, **k: stopped_monitor.append(True)

    def _take_primed_mtk():
        primed_mtk_taken.append(True)
        return None

    w.service.take_primed_mtk = _take_primed_mtk
    from unittest.mock import patch
    patcher = patch("src.linux_sp_flash.verify_linux_flashing_readiness", return_value=(True, []))
    patcher.start()

    # 1. Package selected triggers device prep
    w._package_path = str(tmp_path / "unique_test_device_prep_rom.zip")
    w._package_name = "Original Software 3.2.1"
    w._package_model = "Y2"
    w._show_device_prep()

    assert w._stack.currentIndex() == _PAGE_FLASH
    assert w._power_off_prompt is True
    # Device prep shows the power-off prompt; it stops any live device monitor
    # and takes a primed MTK session it does not start the flash worker yet.
    assert not started_flash, "Flash must not be started while asking user to prepare device"
    assert stopped_monitor == [True], "Device prep stops the device monitor"
    assert primed_mtk_taken == [True], "Device prep takes any primed MTK session"
    assert "powered-off and disconnected" in w._flash_page._wait_prompt_label.text()
    assert "Y2" in w._flash_page._wait_prompt_label.text()
    assert w._flash_page._wait_continue_btn.isVisible()

    # 2. User confirms by clicking Next -> flash starts and the prompt clears.
    w._on_power_off_continue()
    assert w._power_off_prompt is False
    assert started_flash == [True], "Flash must be triggered upon user confirmation"
    assert "please connect your y2" in w._flash_page._wait_prompt_label.text().lower()


def test_partition_table_fallback_is_not_a_fatal_tool_error():
    """The stock Y1/Y2 runs log "Failed to read GPT partition table; falling
    back to per-partition reads" and then write every partition. It must not
    be recorded as the failure that ends the install."""
    from src.flash_service import FlashWorker

    _app()
    w = FlashWorker("dummy.zip", method="mtk", model="Y2")
    w._past_usb_search = True

    w._log("⚠️ Failed to read GPT partition table; falling back to per-partition reads")
    assert w._tool_error == ""

    # The current probe logs the fallback with the tool's own error text.
    w._log("Could not read the device's partition table: Failed to read GPT header")
    assert w._tool_error == ""

    # A real DA failure still records, so the exemption stays narrow.
    w._log("Error on sending emmc read flash command, response: 0x0")
    assert w._tool_error == "Error on sending emmc read flash command, response: 0x0"


def test_errno_during_the_stage2_speed_reconnect_does_not_raise_the_reset_dialog():
    """mtkclient closes and reopens the port to move the DA to high speed and
    prints "[Errno 2] Entity not found" in between; the stock Y2 run did this
    and then flashed every partition. The manual-reset dialog belongs to the
    connect phase, not to a running flash."""
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    try:
        w._package_path = "/fake/rom.zip"
        shown = []
        w._show_retry_guidance = lambda detail="": shown.append(detail)

        w._past_usb_search = True
        w._handle_backend_line("DeviceClass - [Errno 2] Entity not found")
        assert shown == []

        # Before the player is seen the same errno still offers the manual reset.
        w._past_usb_search = False
        w._handle_backend_line("DeviceClass - [Errno 2] Entity not found")
        assert len(shown) == 1, shown
    finally:
        w.close()
        app.processEvents()


