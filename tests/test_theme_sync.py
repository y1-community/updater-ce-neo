"""Themes stage chaining + passive 360p theme sync gating and behavior."""

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

    return QSettings(str(tmp_path / "sync.ini"), QSettings.Format.IniFormat)


def test_gate_only_tracks_360p_rockbox(tmp_path):
    from src import player_update_check as tracking
    from src.theme_sync import is_rockbox_360p_record, tracked_360p_rockbox_models

    _app()
    qs = _settings(tmp_path)
    assert tracked_360p_rockbox_models(qs) == []

    tracking.record_device_install(
        "Y1", "Rockbox", tag_name="0.6", release_label="0.6",
        package_slug="rockbox-y1", settings=qs,
    )
    assert tracked_360p_rockbox_models(qs) == ["Y1"]

    # A 240p build on Y2 does not qualify, and Y1 still does.
    tracking.record_device_install(
        "Y2", "Rockbox", tag_name="0.6", release_label="0.6 (240p)",
        package_slug="rockbox-y2", settings=qs,
    )
    assert tracked_360p_rockbox_models(qs) == ["Y1"]

    # Non-rockbox tracking stops the sync.
    tracking.record_device_install(
        "Y1", "Original Software", tag_name="3.1.2", release_label="3.1.2",
        package_slug="original-y1", settings=qs,
    )
    assert tracked_360p_rockbox_models(qs) == []
    assert not is_rockbox_360p_record(None)
    assert not is_rockbox_360p_record({"software_name": "Rockbox", "tag_name": "x", "release_label": "1.0 (240p)"})


def test_remember_and_last_sync_roundtrip(tmp_path):
    from src.theme_sync import (
        last_theme_sync,
        record_theme_sync,
        remember_theme_target,
        remembered_theme_targets,
    )

    _app()
    qs = _settings(tmp_path)
    assert remembered_theme_targets("Y1", qs) == []
    assert last_theme_sync("Y1", qs) == ""
    remember_theme_target("Y1", "/Volumes/PLAYER/.rockbox", qs)
    remember_theme_target("Y1", "/Volumes/PLAYER/.rockbox", qs)
    remember_theme_target("Y1", "/media/u/Y2/.rockbox", qs)
    assert remembered_theme_targets("Y1", qs) == [
        "/media/u/Y2/.rockbox",
        "/Volumes/PLAYER/.rockbox",
    ]
    record_theme_sync("Y1", qs)
    assert last_theme_sync("Y1", qs) != ""


def test_360p_success_opens_themes_stage_before_appeal():
    """Install in Progress -> themes wizard -> completion/donation appeal."""
    from PySide6.QtCore import QObject, Signal

    from src import player_update_check as tracking
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    w.show()
    try:
        tracking.set_donation_install_prompt_disabled(False, w.settings)
        tracking.set_donation_ui_disabled(False, w.settings)

        shown = []

        class StubWizard(QObject):
            installed_success = Signal()
            finished = Signal(int)

            def show(self):
                shown.append(True)

            def raise_(self):
                pass

            def activateWindow(self):
                pass

        stub = StubWizard()
        w._create_themes_wizard = lambda model: stub
        w._package_model = "Y1"
        w._package_name = "Rockbox-0.6 rom.zip"
        w._package_path = "/tmp/rockbox_rom.zip"
        w._handle_flash_success()
        app.processEvents()
        assert shown == [True]
        # Appeal waits for the wizard: install completed, themes stage first.
        assert not w._flash_page._appeal.isVisible()
        stub.installed_success.emit()
        app.processEvents()
        assert w._flash_page._appeal.isVisible()
        # Finished (close) is idempotent: no second appeal growth.
        stub.finished.emit(0)
        app.processEvents()
        assert w._flash_page._appeal.isVisible()
    finally:
        w.close()
        app.processEvents()


def test_non_rockbox_success_keeps_immediate_appeal():
    from src import player_update_check as tracking
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    w.show()
    try:
        tracking.set_donation_install_prompt_disabled(False, w.settings)
        tracking.set_donation_ui_disabled(False, w.settings)
        opened = []
        w._create_themes_wizard = lambda model: opened.append(model) or None
        w._package_model = "Y1"
        w._package_name = "Original Software"
        w._package_path = "/tmp/orig.zip"
        w._handle_flash_success()
        app.processEvents()
        assert opened == []
        assert w._flash_page._appeal.isVisible()
    finally:
        w.close()
        app.processEvents()


def test_passive_worker_merges_silently(tmp_path):
    from src import theme_sync as sync

    _app()
    qs = _settings(tmp_path)
    payload = tmp_path / "payload" / ".rockbox"
    (payload / "Themes").mkdir(parents=True)
    (payload / "Themes" / "a.cfg").write_text("x")
    target = tmp_path / "player" / ".rockbox"
    target.mkdir(parents=True)
    sync.remember_theme_target("Y1", str(target), qs)

    pushed = []
    started = []
    finished = []
    worker = sync.PassiveThemeSyncWorker(["Y1"], qs)
    worker.device_push_started.connect(lambda m, s: started.append((m, s)))
    worker.done.connect(lambda m, ok: finished.append((m, ok)))
    with patch.object(sync, "ensure_combined_payload", return_value=payload):
        with patch("src.adb.find_adb_binary", return_value="/fake/adb"):
            with patch("src.adb.adb_devices", return_value=["SER1"]):
                with patch("src.adb.adb_find_all_rockbox_dirs", return_value=["/sdcard/.rockbox"]):
                    with patch("src.adb.adb_push_merge", side_effect=lambda *a, **k: (pushed.append(a), (True, ""))[1]):
                        with patch.object(sync, "find_removable_rockbox_dirs", return_value=[]):
                            worker.run()
    assert started == [("Y1", "SER1")]
    assert pushed and pushed[0][1] == "SER1"
    assert (target / "Themes" / "a.cfg").is_file()
    assert finished == [("Y1", True)]
    assert sync.last_theme_sync("Y1", qs) != ""


def test_passive_worker_silent_without_targets(tmp_path):
    from src import theme_sync as sync

    _app()
    qs = _settings(tmp_path)
    finished = []
    worker = sync.PassiveThemeSyncWorker(["Y1"], qs)
    worker.done.connect(lambda m, ok: finished.append((m, ok)))
    with patch("src.adb.find_adb_binary", return_value=None):
        with patch.object(sync, "find_removable_rockbox_dirs", return_value=[]):
            worker.run()
    # ensure_combined_payload may download; failure or no targets -> False, silent.
    assert finished and finished[0][0] == "Y1"
    assert sync.last_theme_sync("Y1", qs) == ""


def test_service_runs_only_for_tracked_360p(tmp_path):
    from src import player_update_check as tracking
    from src.theme_sync import ThemeSyncService

    app = _app()
    qs = _settings(tmp_path)
    service = ThemeSyncService(qs, parent=None)
    try:
        made = []
        with patch("src.theme_sync.PassiveThemeSyncWorker") as worker_cls:
            service.run_once()
            assert worker_cls.call_count == 0
            tracking.record_device_install(
                "Y2", "Rockbox", tag_name="1.0", release_label="1.0",
                package_slug="rockbox-y2", settings=qs,
            )
            service.run_once()
            assert worker_cls.call_count == 1
            assert worker_cls.call_args[0][0] == ["Y2"]
    finally:
        service.stop()
        app.processEvents()


def test_wizard_adopts_passive_push_and_registers(tmp_path):
    from src.theme_pack import Rockbox360pSetupWizard, open_wizards

    app = _app()
    payload = tmp_path / ".rockbox"
    payload.mkdir()
    wiz = Rockbox360pSetupWizard(model="Y1")
    wiz._combined_dir = payload  # avoid network fetch in tests
    try:
        assert wiz in open_wizards()
        wiz._stack.setCurrentIndex(1)
        assert wiz.maybe_adopt_passive_push("SER9") is True
        assert wiz._adb_serial == "SER9"
        wiz.note_passive_progress(3, 10, "adb:SER9")
    finally:
        wiz.close()
        app.processEvents()
    assert wiz not in open_wizards()
