"""In-beta Settings toggle, its action wording, and the give-up transition.

Three asks, one file: the corner switch must default off and actually own the
passive sync's run state, the card must carry the requested copy (beta pill,
"When enabled…" subtitle, "Install Themes Now"), and a themes stage that can't
place the pack must land on the completion/donation screen exactly once with
the try-again-from-Settings note instead of looping the USB guidance.
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _settings(tmp_path):
    from PySide6.QtCore import QSettings

    return QSettings(str(tmp_path / "toggle.ini"), QSettings.Format.IniFormat)


def test_toggle_defaults_off_and_gates_every_sync_pass(tmp_path):
    from src import player_update_check as tracking
    from src import theme_sync as sync

    qs = _settings(tmp_path)
    # Fresh settings: the beta toggle is off unless the user turns it on.
    assert sync.themes_auto_install_enabled(qs) is False

    # A tracked 360p Rockbox Y1 model means the service *would* spawn a worker...
    tracking.record_device_install(
        "Y1", "Rockbox", tag_name="1.0", release_label="1.0",
        package_slug="rockbox-y1", settings=qs,
    )
    service = sync.ThemeSyncService(qs)
    try:
        with patch("src.theme_sync.PassiveThemeSyncWorker") as worker_cls:
            # ...but every pass is gated while the toggle is off.
            service.run_once()
            assert worker_cls.call_count == 0

            sync.set_themes_auto_install_enabled(True, qs)
            service.run_once()
            assert worker_cls.call_count == 1

            sync.set_themes_auto_install_enabled(False, qs)
            service.run_once()
            assert worker_cls.call_count == 1
    finally:
        service.stop()


def test_settings_card_copy_pill_and_toggle_roundtrip():
    from src import theme_sync as sync
    from src.i18n import tr
    from src.ui.settings_page import SettingsPage

    app = _app()
    old = sync.themes_auto_install_enabled()
    sync.set_themes_auto_install_enabled(False)
    page = SettingsPage()
    try:
        page.refresh_settings()
        page.show()
        app.processEvents()

        # The action wording the request named, and the requested subtitle.
        assert page._install_themes_btn.text() == "Install Themes Now"
        assert tr("settings_install_rockbox_themes_btn") == "Install Themes Now"
        assert page._install_themes_desc.text() == (
            "When enabled, the 360p Rockbox themes pack will be installed "
            "at the end of your Rockbox install"
        )
        assert page._install_themes_desc.text() == tr("settings_install_themes_desc")

        # Beta, one word, as a pill on the card's title row beside the title.
        assert page._themes_beta_pill.text() == "Beta"
        card = page._install_themes_card
        assert page._themes_beta_pill.parentWidget() is card._title_row
        assert page._cb_install_themes.parentWidget() is card._title_row
        assert card.title_label().parentWidget() is card._title_row

        # Corner switch: off by default, and flipping it persists + announces.
        assert page._cb_install_themes.isChecked() is False
        emitted = []
        page.themes_auto_install_changed.connect(emitted.append)

        page._cb_install_themes.setChecked(True)
        assert emitted == [True]
        assert sync.themes_auto_install_enabled() is True

        page._cb_install_themes.setChecked(False)
        assert emitted == [True, False]
        assert sync.themes_auto_install_enabled() is False
    finally:
        sync.set_themes_auto_install_enabled(old)
        page.close()
        app.processEvents()


def test_toggle_owns_the_sync_service_lifecycle():
    from src import theme_sync as sync
    from src.ui.main_window import MainWindow

    app = _app()
    old = sync.themes_auto_install_enabled()
    sync.set_themes_auto_install_enabled(False)
    w = MainWindow()
    try:
        # Off at boot: no background service exists at all.
        w._ensure_theme_sync()
        assert getattr(w, "_theme_sync_service", None) is None

        # The real wire: corner switch -> persist -> signal -> service starts.
        w._settings_page._cb_install_themes.setChecked(True)
        service = w._theme_sync_service
        assert service is not None
        assert service._timer.isActive()
        assert sync.themes_auto_install_enabled() is True

        # ...and off again, immediately.
        w._settings_page._cb_install_themes.setChecked(False)
        assert not service._timer.isActive()
        assert sync.themes_auto_install_enabled() is False

        # Re-enabling reuses the same service rather than leaking a second.
        w._settings_page._cb_install_themes.setChecked(True)
        assert w._theme_sync_service is service
        assert service._timer.isActive()
        w._settings_page._cb_install_themes.setChecked(False)
        assert not service._timer.isActive()
    finally:
        sync.set_themes_auto_install_enabled(old)
        w.close()
        app.processEvents()


def _wait_for(app, predicate, timeout_s: float = 4.0) -> bool:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def test_power_prompt_detects_a_player_and_continues_the_stage():
    """ADB answering during the power-on step presses Continue for the user."""
    from src import adb as adbmod
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    continued = []
    try:
        with patch.object(adbmod, "find_adb_binary", return_value="/fake/adb"), patch.object(
            adbmod, "adb_devices", return_value=["SERIAL1"]
        ):
            page.show_rockbox_power_on_prompt(
                "disconnect and power on your Y1",
                on_continue=lambda: continued.append(1),
            )
            assert _wait_for(app, lambda: bool(continued))
            assert continued == [1]  # exactly once, not once per poll
            assert page.themes.watch is None
            assert page.themes.prompt_pending is False
    finally:
        page.close()
        app.processEvents()


def test_manual_pick_yields_to_a_player_on_adb():
    """The folder the user picked must not be written when ADB can do the job."""
    from src import adb as adbmod
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    page.themes.begin("Y1")
    adopted = []
    page._on_adb_device_found = lambda serial: adopted.append(serial)
    chosen = Path("/tmp/ce_themes_manual_probe")
    try:
        page._on_theme_fallback_next_clicked()
        assert page.themes.folder_dialog is not None
        with patch.object(adbmod, "find_adb_binary", return_value="/fake/adb"), patch.object(
            adbmod, "adb_devices", return_value=["SERIAL2"]
        ):
            page._on_theme_folder_chosen(str(chosen))
        assert adopted == ["SERIAL2"]
        assert getattr(page, "_dir_merge_worker", None) is None
        assert not chosen.exists()
    finally:
        page.close()
        app.processEvents()


def test_cancel_keeps_watching_but_leaves_no_merge_behind():
    from src import adb as adbmod
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    page.themes.begin("Y1")
    try:
        with patch.object(adbmod, "find_adb_binary", return_value="/fake/adb"), patch.object(
            adbmod, "adb_devices", return_value=[]
        ):
            page._show_theme_fallback_usb_mode()
            assert page.themes.watch is not None
            page._on_theme_fallback_next_clicked()
            # Cancelling the picker: nothing was chosen, nothing is merged.
            page._close_theme_folder_dialog()
            app.processEvents()
        assert page.themes.folder_dialog is None
        assert getattr(page, "_dir_merge_worker", None) is None
        # The stage ending is what stops the watch, not the dialog closing.
        assert page.themes.watch is not None
        page._finish_rockbox_themes_stage()
        assert page.themes.watch is None
    finally:
        page.close()
        app.processEvents()


def test_confirm_step_shows_a_segmented_pair_with_back_disabled():
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    page.themes.model = "Y1"
    try:
        page.show_rockbox_power_on_prompt("disconnect and power on your Y1")
        app.processEvents()
        back, nxt = page._theme_back_btn, page._theme_step1_next_btn
        assert back.isVisible() and nxt.isVisible()
        assert not back.isEnabled()
        assert nxt.isEnabled()
        assert back._companion is nxt and nxt._companion is back
        assert back._is_leading is True and nxt._is_leading is False
    finally:
        page.close()
        app.processEvents()


def test_confirm_step_asks_for_the_window():
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    page.themes.model = "Y1"
    calls = []

    class _Win:
        def raise_(self):
            calls.append("raise")

        def activateWindow(self):
            calls.append("activate")

    page.window = lambda: _Win()
    try:
        page.show_rockbox_power_on_prompt("disconnect and power on your Y1")
        assert calls == ["raise", "activate"]
    finally:
        page.close()
        app.processEvents()


def test_auto_themes_stage_obeys_the_settings_toggle():
    """Off (the default) means an install finishes without a themes stage."""
    from src import theme_sync as sync
    from src.ui.main_window import MainWindow

    app = _app()
    old = sync.themes_auto_install_enabled()
    w = MainWindow()
    started = []
    w._flash_page.start_rockbox_themes_stage = lambda *a, **k: started.append(1)
    w._create_themes_wizard = lambda model: None
    try:
        sync.set_themes_auto_install_enabled(False)
        w._start_themes_stage("Y1", donation_disabled=True, automatic=True)
        assert started == []

        sync.set_themes_auto_install_enabled(True)
        w._start_themes_stage("Y1", donation_disabled=True, automatic=True)
        assert started == [1]
    finally:
        sync.set_themes_auto_install_enabled(old)
        w.close()
        app.processEvents()


def test_manual_themes_start_refuses_y2_and_logs_it():
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    try:
        combo = w._select_page._model_combo
        combo.setCurrentIndex(combo.findText("Y2"))
        assert w._manual_themes_model() == "Y2"
        assert w.start_rockbox_themes_manually() is False
        assert not w.themes.power_on_pending
        assert not w.themes.active
        assert any("not installed for Y2" in line for line in w._log_lines)
    finally:
        w.close()
        app.processEvents()


def test_manual_themes_start_still_starts_for_y1_rockbox():
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    try:
        w._package_model = "Y1"
        assert w.start_rockbox_themes_manually() is True
        assert w.themes.power_on_pending is True
    finally:
        w.themes.clear_power_on()
        w.close()
        app.processEvents()


def test_settings_button_still_starts_themes_with_the_toggle_off():
    """"Install Themes Now" is a user decision, not the automatic one."""
    from src import theme_sync as sync
    from src.ui.main_window import MainWindow

    app = _app()
    old = sync.themes_auto_install_enabled()
    sync.set_themes_auto_install_enabled(False)
    w = MainWindow()
    try:
        assert w.start_rockbox_themes_manually() is True
        assert w.themes.power_on_pending is True
    finally:
        w.themes.clear_power_on()
        sync.set_themes_auto_install_enabled(old)
        w.close()
        app.processEvents()


def test_manual_folder_merge_lands_the_real_payload(tmp_path):
    """The folder pick must copy the same payload the ADB path pushes."""
    import zipfile

    from src import theme_pack as tp
    from src.ui.flash_page import FlashPage

    app = _app()
    payload = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("fonts/12-Nimbus.fnt", "font")
        zf.writestr("wps/rockbox.wps", "wps")
    chosen = tmp_path / "PLAYER"
    chosen.mkdir()

    page = FlashPage()
    page.themes.begin("Y1")
    page.themes.model = "Y1"
    try:
        page._on_theme_fallback_next_clicked()
        with patch.object(tp, "ensure_normalized_theme_pack_zip", return_value=payload):
            page._on_theme_folder_chosen(str(chosen))
            worker = page._dir_merge_worker
            assert worker is not None
            assert worker.wait(20000)
            app.processEvents()

        landed = sorted(
            p.name for p in (chosen / ".rockbox").rglob("*") if p.is_file()
        )
        assert landed == ["12-Nimbus.fnt", "rockbox.wps"]
        assert page.themes.placement_failed is False
        # The payload is unpacked into a temp dir and then merged: it must not
        # leave a second .rockbox level behind.
        assert not (chosen / ".rockbox" / ".rockbox").exists()
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


def test_giveup_reaches_the_completion_screen_exactly_once():
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    app.processEvents()
    done = []
    logs = []
    try:
        # No ADB binary: the journey goes straight to USB-storage guidance.
        with patch("src.adb.find_adb_binary", return_value=None):
            page.start_rockbox_themes_stage(
                model="Y1", on_done=lambda: done.append(1), log_cb=logs.append,
            )
        assert page.themes.transfer_running is True
        assert page.themes.placement_failed is False
        assert not done

        # The folder merge fails: that is the end of the line — not another
        # round of guidance.
        page._on_dir_merge_finished(False, "read-only volume", 0, "/Volumes/X/.rockbox")
        assert done == [1]
        assert page.themes.placement_failed is True
        assert page.themes.transfer_running is False
        assert any("Unable to place the themes pack" in line for line in logs)

        # A stray worker finishing late never routes a second time.
        page._on_dir_merge_finished(False, "read-only volume", 0, "/Volumes/X/.rockbox")
        assert done == [1]

        # The completion note carries the try-again copy once flagged failed.
        page.show_completion_note(True, software="Rockbox", model="Y1")
        assert tr("themes_placement_failed_note") in page._complete_note.text()
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


def test_completion_screen_shows_the_giveup_copy_with_and_without_donations():
    from src.i18n import tr
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    w.show()
    app.processEvents()
    try:
        w.themes.mark_placement_failed()

        # Donations off: the note is the screen.
        w.themes.completion_shown = False
        w._finish_themes_stage(True)
        note = w._flash_page._complete_note
        assert note.isVisible()
        assert tr("themes_placement_failed_note") in note.text()

        # Donations on: the appeal shows *and* the note stays — the failure
        # copy must not be displaced by the donation pitch.
        w.themes.completion_shown = False
        w._finish_themes_stage(False)
        assert note.isVisible()
        assert tr("themes_placement_failed_note") in note.text()
        assert w._flash_page._appeal.isVisible()

        # Re-entry is idempotent: still one screen, still one note.
        w._finish_themes_stage(False)
        assert note.isVisible()
        assert tr("themes_placement_failed_note") in note.text()
    finally:
        w.themes.begin("Y1")
        w.close()
        app.processEvents()


def test_picker_stays_live_and_yields_to_a_player_that_appears():
    """The folder picker must not freeze detection out of the journey."""
    from src import adb as adbmod
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    page.themes.begin("Y1")
    page.themes.model = "Y1"
    started = []
    try:
        with patch.object(adbmod, "find_adb_binary", return_value="/fake/adb"), patch.object(
            adbmod, "adb_devices", return_value=[]
        ):
            page._show_theme_fallback_usb_mode()
            page._on_theme_fallback_next_clicked()
            dialog = page.themes.folder_dialog
            assert dialog is not None and dialog.isVisible()
            # A blocking dialog would have returned only after closing; this one
            # leaves the event loop running for the watcher.
            assert app.processEvents() is None

            with patch.object(page, "_start_adb_theme_push", lambda serial: started.append(serial)), patch(
                "src.ui.flash_page.ROCKBOX_BOOT_WAIT_MS", 0
            ):
                page._on_adb_device_found("SERIAL3")
                app.processEvents()

        assert page.themes.folder_dialog is None and not dialog.isVisible()
        assert page.themes.player_taken is True
        assert started == ["SERIAL3"]
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


def test_usb_guidance_step_asks_for_the_window():
    from src import adb as adbmod
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    calls = []

    class _Win:
        def raise_(self):
            calls.append("raise")

        def activateWindow(self):
            calls.append("activate")

    page.window = lambda: _Win()
    try:
        with patch.object(adbmod, "find_adb_binary", return_value=None):
            page._show_theme_fallback_usb_mode()
        assert calls == ["raise", "activate"]
    finally:
        page.close()
        app.processEvents()


def test_a_poller_stuck_in_adb_is_retired_not_abandoned():
    """Stopping the watch must never destroy a running QThread (Qt aborts)."""
    import time

    from src.ui.flash_page import FlashPage

    app = _app()

    def slow_devices(*_args, **_kwargs):
        time.sleep(2.0)
        return []

    page = FlashPage()
    page.show()
    try:
        with patch("src.adb.adb_devices", side_effect=slow_devices):
            page._ensure_adb_presence_watch()
            app.processEvents()
            worker = page.themes.watch
            assert worker is not None and worker.isRunning()
            page._stop_adb_presence_watch()
            assert page.themes.watch is None
            assert worker in page.themes.retired_watches
            assert _wait_for(app, lambda: not page.themes.retired_watches, timeout_s=8.0)
            assert not worker.isRunning()
    finally:
        page.close()
        app.processEvents()
