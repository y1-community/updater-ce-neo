"""Backend selection, attempt timeout and failure guidance.

Covers the regressions that made SP Flash Tool look broken on Windows/Linux:

* a legacy ``mtk_mac`` setting must not pin the app to MTKClient (it used
  to make SP Flash Tool unreachable entirely);
* a backend command must not start until the user has confirmed the player is
  unplugged and powered off (SP Flash Tool's own search window expired
  otherwise and reported S_TIMEOUT while nothing was wrong);
* a command that shows no progress inside the app's window is ended by the
  app and hands over to the other engine — except a wrong-model result, which
  no retry can fix;
* SP Flash Tool's internal Qt trace must not push the card off the "please
  connect your player" sentence onto "please wait" while it searches.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import flash_service as fs  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


# --------------------------------------------------------------------------
# Legacy platform-specific method aliases
# --------------------------------------------------------------------------
def test_simulated_macos_entry_does_not_pin_mtkclient():
    from src import paths

    real_mac = paths.IS_MAC
    try:
        paths.IS_MAC = False
        assert fs.normalise_method("mtk_mac") == fs.METHOD_SP
        assert fs.backend_method("mtk_mac") == fs.METHOD_SP
        # The real choices are untouched.
        assert fs.normalise_method("sp") == fs.METHOD_SP
        assert fs.normalise_method("mtk") == fs.METHOD_MTK
        assert fs.normalise_method("auto") == fs.METHOD_SP

        # While the macOS flow is actually running, it is MTKClient.
        paths.IS_MAC = True
        assert fs.normalise_method("mtk_mac") == fs.METHOD_MTK
        assert fs.normalise_method("sp") == fs.METHOD_MTK
        assert fs.backend_method("mtk_mac") == fs.METHOD_MTK
    finally:
        paths.IS_MAC = real_mac


def test_settings_selector_has_one_platform_neutral_mtkclient_entry(monkeypatch):
    """Legacy aliases normalize away; only one MTKClient method is selectable."""
    from src import paths
    from src.ui.settings_page import SettingsPage

    app = _app()
    page = SettingsPage()
    monkeypatch.setattr(paths, "IS_MAC", False)
    try:
        page.reveal_advanced_methods()
        methods = page._available_methods()
        assert methods == (fs.METHOD_SP, fs.METHOD_MTK)
        assert page._method_combo.findData("mtk_mac") == -1
        page.set_method("mtk_mac")
        assert page.current_method() == fs.METHOD_SP

        monkeypatch.setattr(paths, "IS_MAC", True)
        assert page._available_methods() == (fs.METHOD_MTK,)
        page.set_method("mtk_mac")
        assert page.current_method() == fs.METHOD_MTK
    finally:
        page.deleteLater()
        app.processEvents()


# --------------------------------------------------------------------------
# Error guidance
# --------------------------------------------------------------------------
def test_sp_error_names_and_codes_are_extracted():
    assert fs.sp_error_name("S_FT_DOWNLOAD_FAIL (4008)") == "S_FT_DOWNLOAD_FAIL"
    assert fs.sp_error_code("S_FT_DOWNLOAD_FAIL (4008)") == "4008"
    assert fs.sp_error_name("no tool code here") == ""
    # Every code the numeric-only fallback can report has a name.
    for code, name in fs.SP_ERROR_CODE_NAMES.items():
        assert name in fs.SP_ERROR_GUIDANCE, code


def test_connect_failures_are_recognised_by_name_and_by_number():
    """"It never got as far as the player" is a distinct failure kind.

    SP Flash Tool reports error 1013 (``S_COM_PORT_OPEN_FAIL``) by name in its
    log and by number alone from ``err_code[1013]``, and the engines have their
    own connect-failed codes. None of them is a player that dropped out of a
    running flash.
    """
    for value in (
        "S_COM_PORT_OPEN_FAIL (1013)",
        "SP_ERR_1013",
        "Exception: err_code[1013]",
        "CONNECTION_FAILED",
        "MTK_INIT_FAILED",
        "CLI_START_FAILED",
    ):
        assert fs.is_connect_failure(value), value
    for value in (
        "",
        "S_FT_DOWNLOAD_FAIL (4008)",
        "S_TIMEOUT (1042)",
        "S_CHIP_TYPE_NOT_MATCH (3184)",
        "S_BROM_CMD_STARTCMD_FAIL (2005)",
        "ERR_ATTEMPT_TIMEOUT",
    ):
        assert not fs.is_connect_failure(value), value
    # The bare number is named, so the failure keeps its explanation.
    assert fs.SP_ERROR_CODE_NAMES["1013"] == "S_COM_PORT_OPEN_FAIL"
    assert fs.describe_backend_error("S_COM_PORT_OPEN_FAIL (1013)")


def test_connect_failover_copy_names_both_engines_and_breaks_lines():
    """The recovery line splits into failure / next engine / reset steps."""
    from src import i18n

    table = i18n._STRINGS["err_connect_failover_recovery"]
    assert set(table) >= {"zh-CN", "en", "fr", "es", "de", "ja"}
    lines = table["en"].split("\n")
    assert len(lines) == 3, lines
    assert lines[0] == "{failed} couldn't connect to your {model}."
    assert lines[1] == "Updater will now try {next} instead."
    assert lines[2].startswith("Please disconnect the USB and use a paperclip")
    assert lines[2].endswith("then press next.")
    # A real disconnect keeps its own wording.
    assert i18n._STRINGS["err_usb_disconnected_recovery"]["en"].startswith(
        "Your {model} got disconnected."
    )


def test_wrong_model_errors_are_recognised():
    assert fs.is_wrong_model_error("S_CHIP_TYPE_NOT_MATCH (3184)")
    assert fs.is_wrong_model_error("DEVICE_PACKAGE_MISMATCH")
    assert fs.is_wrong_model_error("SP_WRONG_MODEL")
    assert not fs.is_wrong_model_error("S_FT_DOWNLOAD_FAIL (4008)")
    assert not fs.is_wrong_model_error("S_TIMEOUT (1042)")
    assert not fs.is_wrong_model_error("")


def test_known_errors_get_an_explanation_and_unknown_ones_do_not():
    from src.i18n import tr

    assert fs.describe_backend_error("S_CHIP_TYPE_NOT_MATCH (3184)") == tr(
        "err_sp_wrong_model"
    )
    for value in (
        "S_TIMEOUT (1042)",
        "S_BROM_CMD_STARTCMD_FAIL (2005)",
        "S_FT_DOWNLOAD_FAIL (4008)",
        "DEVICE_PACKAGE_MISMATCH",
    ):
        assert fs.describe_backend_error(value), value
    assert fs.describe_backend_error("SP_ERR_9999") == ""
    assert fs.describe_backend_error("") == ""
    # The i18n key really exists, so the hint is not an empty label.
    assert tr("err_sp_wrong_model") != "err_sp_wrong_model"


def test_error_page_shows_the_guidance_for_a_failed_flash():
    from src.i18n import tr
    from src.ui.error_page import ErrorPage

    app = _app()
    page = ErrorPage()
    try:
        page.show_flash_failed(
            percent=42,
            step="WRITE",
            error_code="S_FT_DOWNLOAD_FAIL (4008)",
            package_name="rom.zip",
            retry_count=1,
        )
        label = page._cause
        assert label.isHidden() is False
        assert tr("err_sp_download_fail") in label.text()
        # The raw code is still shown for support.
        assert "4008" in page._error_row.value()

        page.show_flash_failed(
            percent=0,
            step="WAIT",
            error_code="S_CHIP_TYPE_NOT_MATCH (3184)",
            package_name="rom.zip",
            retry_count=0,
        )
        assert "Wrong Model Connected" in page._error_row.value()
        assert tr("err_sp_wrong_model") in page._cause.text()

        # An unknown failure leaves no stale hint behind.
        page.show_flash_failed(
            percent=0,
            step="WAIT",
            error_code="SP_ERR_9999",
            package_name="rom.zip",
            retry_count=0,
        )
        assert page._cause.isHidden() is True
    finally:
        page.deleteLater()
        app.processEvents()


# --------------------------------------------------------------------------
# The tool's own trace must not move the wait phase
# --------------------------------------------------------------------------
QT_TRACE = (
    "10/11/2026 17:07:43.796 FlashTool[2232][7880][D]: "
    "APCore::ConnMediaUSB::Search(): Search usb, timeout set as 120000 ms"
    "(..\\..\\flashtool\\Conn\\ConnMedia.cpp,21)"
)


def test_internal_tool_trace_is_not_progress():
    assert fs.is_tool_trace_line(QT_TRACE)
    assert fs.is_tool_trace_line("[QT] " + QT_TRACE)
    assert not fs.is_tool_trace_line("12% of image data has been sent")
    assert not fs.is_tool_trace_line("[SP] Search usb, timeout set as 120000 ms")

    # While the tool is searching, its trace lines must leave the connect
    # sentence up instead of flipping the card to "please wait".
    assert fs.advance_tool_wait_phase(fs.PHASE_CONNECT, QT_TRACE) == fs.PHASE_CONNECT
    assert fs.classify_tool_wait_line(QT_TRACE) == ""
    # Real output still advances the phase.
    assert (
        fs.advance_tool_wait_phase(fs.PHASE_CONNECT, "12% of DA has been sent")
        == fs.PHASE_PLEASE_WAIT
    )


def test_search_line_still_arms_the_connect_sentence_through_the_trace():
    """The console search line is recognised even wrapped in the trace shape."""
    worker = fs.FlashWorker("pkg.zip", method="sp")
    steps = []
    worker.step_changed.connect(steps.append)
    worker._classify_sp_stdout(QT_TRACE)
    assert steps == [fs.STEP_WAITING], steps
    assert worker._usb_search_active


# --------------------------------------------------------------------------
# The attempt no-progress watchdog
# --------------------------------------------------------------------------
def test_watchdog_ticks_then_expires():
    ticks = []
    expired = []
    watchdog = fs._AttemptWatchdog(
        ticks.append, lambda: expired.append(True), timeout=0.25, interval=0.05
    )
    watchdog.start()
    watchdog.join(timeout=3)
    assert expired == [True]
    assert ticks, "the countdown must reach the UI"


def test_watchdog_touch_refreshes_and_disarm_ends_it():
    expired = []
    watchdog = fs._AttemptWatchdog(lambda _s: None, lambda: expired.append(True),
                                   timeout=0.3, interval=0.05)
    watchdog.start()
    deadline = time.time() + 1.0
    while time.time() < deadline:
        watchdog.touch()
        time.sleep(0.05)
    assert expired == [], "a device being seen must keep the attempt alive"
    watchdog.disarm()
    time.sleep(0.4)
    assert expired == [], "progress must switch the window off"


def test_expiring_the_attempt_terminates_the_command_once():
    worker = fs.FlashWorker("pkg.zip", method="sp")
    worker._log = lambda *a, **k: None

    class _Process:
        def __init__(self):
            self.terminated = 0

        def poll(self):
            return None

        def terminate(self):
            self.terminated += 1

    worker._process = _Process()
    worker._expire_attempt()
    worker._expire_attempt()
    assert worker._attempt_expired is True
    assert worker._process.terminated == 1, "a silent backend is stopped once"


def test_starting_the_watchdog_expires_a_worker_that_never_reports():
    app = _app()
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None
    ticks = []
    worker.attempt_timeout.connect(ticks.append)
    worker._start_attempt_watchdog(timeout=0.2)
    deadline = time.time() + 3.0
    while time.time() < deadline and not worker._attempt_expired:
        app.processEvents()
        time.sleep(0.05)
    worker._stop_attempt_watchdog()
    assert worker._attempt_expired is True
    assert ticks and ticks[-1] == 0, f"countdown must reset when it ends: {ticks}"


# --------------------------------------------------------------------------
# Guided-install failover
# --------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has no SP Flash Tool")
def test_failed_attempt_goes_back_to_the_power_off_step():
    from src.ui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    calls = []
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(method)
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True
    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Rockbox (Y1)"
        window._package_model = "Y1"
        window._flash_method = "sp"
        window._flash_page.set_method("sp")

        # A backend that never got going hands over to the other engine.
        window._handle_flash_failure("S_FT_DOWNLOAD_FAIL (4008)")
        assert window._flash_method == "mtk"
        assert window._power_off_prompt is True
        assert calls == [], "the other backend must wait for the confirmation"
        window._on_power_off_continue()
        assert calls[-1] == "mtk"

        # One automatic switch per install: an exhausted pair is a failure.
        assert window._offer_backend_failover("S_TIMEOUT (1042)") is False
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has no SP Flash Tool")
def test_wrong_model_never_falls_back_to_the_other_backend():
    from src.ui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    try:
        window.show()
        window._flash_method = "sp"
        assert window._offer_backend_failover("S_CHIP_TYPE_NOT_MATCH (3184)") is False
        assert window._offer_backend_failover("DEVICE_PACKAGE_MISMATCH") is False
        assert window._flash_method == "sp"
        assert window._fallback_attempted is False
    finally:
        window.close()
        app.processEvents()


def test_no_failover_without_a_second_backend():
    from src.ui.main_window import MainWindow
    from src import paths

    app = _app()
    window = MainWindow()
    try:
        window.show()
        window._flash_method = "sp"
        real_mac = paths.IS_MAC
        paths.IS_MAC = True
        try:
            assert window._other_method("sp") == ""
            assert window._other_method("mtk") == ""
        finally:
            paths.IS_MAC = real_mac
        assert window._other_method("sp") == "mtk"
        assert window._other_method("mtk") == "sp"
    finally:
        window.close()
        app.processEvents()


def test_switching_method_is_silent_and_waits_for_the_confirmation():
    """The user log must not name backends; the power-off step gates the run."""
    from src.ui.main_window import MainWindow

    from PySide6.QtCore import QSettings

    from src.state import FlashState

    app = _app()
    settings = QSettings("innioasis", "updater")
    previous = settings.value("flash_method")
    window = MainWindow()
    calls = []
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(method)
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True
    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Rockbox (Y1)"
        window._package_model = "Y1"
        settings.setValue("flash_method", "sp")
        window._flash_method = "sp"
        window._flash_page.set_method("sp")
        window._settings_page.set_method("sp")
        window._settings_page.reveal_advanced_methods()
        # The gate applies while the run is waiting for the player.
        window.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        combo = window._settings_page._method_combo
        calls.clear()
        with patch.object(window, "_append_log") as log:
            combo.setCurrentIndex(combo.findData("mtk"))
            window._on_method_changed("mtk")
            assert log.call_args_list == [], log.call_args_list
        assert "mtk" not in calls, "the switch must not start a backend command"
        assert window._power_off_prompt is True
        window._on_power_off_continue()
        assert calls[-1] == "mtk"
    finally:
        if previous is None:
            settings.remove("flash_method")
        else:
            settings.setValue("flash_method", previous)
        window.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has no SP Flash Tool")
def test_power_off_copy_after_an_automatic_method_switch():
    """Only the automatic engine switch shows the recovery card.

    That attempt left the player mid-download, so the card keeps saying the
    install failed and the line spells out the hidden button that puts the
    player back into flash mode. Switching engine by hand in Settings is the
    user's own choice and keeps the plain power-off step.
    """
    from PySide6.QtCore import QSettings

    from src.i18n import tr
    from src.state import FlashState
    from src.ui.main_window import MainWindow

    app = _app()
    settings = QSettings("innioasis", "updater")
    previous = settings.value("flash_method")
    window = MainWindow()
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: None
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True
    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Rockbox (Y1)"
        window._package_model = "Y1"
        window._flash_method = "sp"
        window._flash_page.set_method("sp")
        window._settings_page.set_method("sp")

        # A backend that never got going hands over to the other engine, and
        # the step now says how to bring the player back into flash mode.
        window._handle_flash_failure("S_FT_DOWNLOAD_FAIL (4008)")
        assert window._flash_method == "mtk"
        assert window._flash_page._wait_prompt_label.text() == tr(
            "err_usb_disconnected_recovery"
        ).format(model="Y1")
        # The band still reads "Install Failed", and that line already names the
        # hidden button, so the separate pin tip stays off.
        assert window._flash_page._wait_progress_bar.is_failed() is True
        assert window._flash_page._show_pin_tip is False

        # Choosing the other engine in Settings is not a recovery: the user is
        # not mid-failure, so the plain power-off line is right.
        window.sm.force_state(FlashState.S2_WAIT_CONNECTION)
        window._on_method_changed("sp")
        assert window._flash_page._wait_prompt_label.text() == tr(
            "flash_connect_prompt"
        ).format(model="Y1")
        # The ordinary step is not a failure: the bar is live again and the pin
        # tip returns, which is the only place that copy appears.
        assert window._flash_page._wait_progress_bar.is_failed() is False
        assert window._flash_page._show_pin_tip is True
    finally:
        if previous is None:
            settings.remove("flash_method")
        else:
            settings.setValue("flash_method", previous)
        window.close()
        app.processEvents()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS has no SP Flash Tool")
def test_com_port_failure_fails_over_with_the_engine_names():
    """A 1013 COM-port error says the tool could not connect, not that the
    player dropped out, and names the engine taking over."""
    from src.i18n import tr
    from src.ui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: None
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True
    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Original Software 3.1.2 for Y1"
        window._package_model = "Y1"
        window._flash_method = "sp"
        window._flash_page.set_method("sp")
        # SP Flash Tool can print its USB-search phase before discovering the
        # port cannot be opened (e.g. kernel TIOCCBRK not supported). A device
        # absent from USB afterward must not turn this pre-connection failure
        # into a disconnect and bypass the safe MTKClient fallback.
        window._past_usb_search = True
        with patch("src.flash_service.is_mtk_device_connected", return_value=False):
            window._handle_flash_failure("S_COM_PORT_OPEN_FAIL (1013)")

        assert window._flash_method == "mtk"
        text = window._flash_page._wait_prompt_label.text()
        assert text == tr("err_connect_failover_recovery").format(
            model="Y1", failed="SP Flash Tool", next="MTKClient"
        )
        # Failure, engine taking over, reset steps: one line each.
        assert len(text.splitlines()) == 3
        # The band keeps saying the install failed.
        assert window._flash_page._wait_progress_bar.is_failed() is True
        assert window._power_off_prompt is True

        # A failure mid-download is a different story: the player really did
        # drop out, and that line is still the one shown.
        window._fallback_attempted = False
        window._flash_method = "sp"
        window._flash_page.set_method("sp")
        window._past_usb_search = False
        window._handle_flash_failure("S_FT_DOWNLOAD_FAIL (4008)")
        assert window._flash_page._wait_prompt_label.text() == tr(
            "err_usb_disconnected_recovery"
        ).format(model="Y1")
    finally:
        window.close()
        app.processEvents()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
