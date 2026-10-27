"""The SP Flash Tool GUI route prepares firmware instead of failing.

A run that was interrupted leaves an extract with its scatter and some of its
images, which is exactly what "images referenced in scatter file were not
found" is reported from. That used to end in a dialog; these tests cover what
happens instead: the cache is treated as absent, the release is downloaded (or
the archive extracted again) with the usual progress screen, every session this
app started is stopped, and only then is the tool's own window opened.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

SCATTER_BODY = "file_name: fat_sparse.img\n"


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


@pytest.fixture()
def window():
    """A real window, on the offscreen platform.

    The paths under test reach no dialog of their own: the point of every test
    here is that the firmware is prepared *instead of* being reported, so a
    dialog would leave the state the assertions look for unset.
    """
    from src.ui import main_window as mw

    _app()  # Qt aborts the process if a widget is built without one
    w = mw.MainWindow()
    try:
        yield w
    finally:
        w.close()
        _app().processEvents()


def _incomplete_extract() -> tempfile.TemporaryDirectory:
    """A temp extract whose scatter names an image that is not beside it."""
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / "MT6582_Android_scatter.txt").write_text(SCATTER_BODY, encoding="utf-8")
    return td


def test_a_half_written_cache_is_not_offered(window, monkeypatch):
    """The incomplete extract resolves as "no cache", so a fresh one is used."""
    from src import device_tracking

    with _incomplete_extract() as td:
        root = Path(td)
        scatter = root / "MT6582_Android_scatter.txt"
        monkeypatch.setattr(
            device_tracking, "get_latest_package", lambda settings: {"model": "Y2"}
        )
        monkeypatch.setattr(
            "src.sp_flash_gui.cached_install_firmware",
            lambda latest: (scatter, root),
        )
        assert window._cached_gui_package() is None
        assert "fat_sparse.img" in window._gui_cache_reason

        # With the image present the same extract is a cache again.
        (root / "fat_sparse.img").write_bytes(b"img")
        cached = window._cached_gui_package()
        assert cached is not None and cached["kind"] == "cache"
        assert Path(cached["scatter"]).is_file()


def test_a_selected_unready_online_release_is_offered_for_preparation(window, monkeypatch, tmp_path):
    """The focused release remains eligible before its download is cached."""
    page = window._select_page
    rel = {
        "tag_name": "v2.0",
        "download_url": "https://example.invalid/release.zip",
        "asset_name": "release.zip",
    }
    page._current_selected_rel = rel
    page._current_package_path = ""
    page._gui_explicit = page._firmware_target_for_release(rel)
    monkeypatch.setattr(page, "_gui_target_if_ready", lambda target: None)
    monkeypatch.setattr(page, "focused_firmware", lambda: page._gui_explicit)
    target = page.gui_firmware_target()
    assert target is not None
    assert target["kind"] == "online"
    assert target["release"]["tag_name"] == "v2.0"


def test_an_incomplete_local_package_is_refused_not_prepared(window, monkeypatch, tmp_path):
    """A local package with missing scatter images is never flashed or repaired."""
    from src.ui import main_window as mw_mod
    from src import sp_flash_gui

    package = tmp_path / "rom_y2.zip"
    package.write_bytes(b"PK\x03\x04")
    prepared = []

    monkeypatch.setattr(sp_flash_gui, "is_sp_flash_gui_supported", lambda: True)
    monkeypatch.setattr(
        sp_flash_gui,
        "check_cached_firmware_readiness",
        lambda **kwargs: (
            False,
            None,
            None,
            "Images referenced in scatter file were not found: fat_sparse.img",
        ),
    )
    monkeypatch.setattr(
        window._select_page,
        "begin_external_prepare",
        lambda target, callback: prepared.append(target) or True,
    )
    window._package_path = str(package)
    window._package_model = "Y2"
    window._package_name = "Original Software 3.2.1 for Y2"
    window._select_page._gui_explicit = {
        "kind": "local",
        "path": str(package),
        "model": "Y2",
        "name": window._package_name,
    }

    warnings = []
    monkeypatch.setattr(mw_mod.QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    monkeypatch.setattr(window, "_ensure_elevated_for_install", lambda: True)
    window._begin_sp_gui_install()

    assert prepared == []
    assert warnings, "the user must be told the package cannot be used"
    assert window._gui_handoff_prepare is False
    assert window._gui_launch_when_ready is False


def test_settings_shortcut_routes_selected_firmware_through_main_handoff(window, monkeypatch):
    """Settings uses the same release validation and history path as the rail."""
    invoked = []
    monkeypatch.setattr(window, "_open_sp_flash_tool_gui", lambda: invoked.append(True))
    window._settings_page._on_launch_sp()
    assert invoked == [True]


def test_the_desktop_tool_is_opened_only_after_the_sessions_stop(window, monkeypatch, tmp_path):
    """Console/MTKClient sessions stop before history.ini is staged and the GUI opens."""
    from src import sp_flash_gui
    from src.ui.main_window import _PAGE_SELECT

    scatter = tmp_path / "scatter.txt"
    scatter.write_text("scatter\n", encoding="utf-8")
    order = []
    monkeypatch.setattr(
        window.service,
        "terminate_install_sessions",
        lambda *_a, **_k: order.append("sessions") or "stopped",
    )
    monkeypatch.setattr(
        sp_flash_gui,
        "launch_sp_flash_tool_gui",
        lambda **kwargs: order.append("launch") or (True, "ok"),
    )
    window._gui_ready = (scatter, tmp_path, "Y2")
    window._gui_handoff_ready = True
    window._gui_handoff_prepare = True

    window._complete_gui_handoff()

    assert order == ["sessions", "launch"], order
    assert window._stack.currentIndex() == _PAGE_SELECT


def test_sessions_are_reported_to_the_log(window, monkeypatch):
    """terminate_install_sessions()' report lands in the run log."""
    logged = []
    monkeypatch.setattr(
        window.service, "terminate_install_sessions", lambda *_a, **_k: "Stopped 2 console runs."
    )
    monkeypatch.setattr(window, "_append_log", logged.append)
    window._terminate_install_sessions()
    assert logged == ["Stopped 2 console runs."]


def test_the_handoff_shows_the_download_it_is_actually_doing(window, monkeypatch):
    """A not-yet-downloaded release shows the download card, not "Extracting".

    The preparation used to switch straight to the extraction view, so a
    multi-hundred megabyte download ran behind a 0% "Extracting" card with no
    progress at all. The card follows the worker: the download signals own the
    download view, and only the extractor's own prep_started shows extraction.
    """
    from src.i18n import tr
    from src.ui import main_window as mw_mod

    page = window._select_page
    rel = {
        "tag_name": "v9.9",
        "download_url": "https://example.invalid/release.zip",
        "asset_name": "release.zip",
    }
    page._current_selected_rel = rel
    page._current_package_path = ""
    page._gui_explicit = page._firmware_target_for_release(rel)
    monkeypatch.setattr(page, "_gui_target_if_ready", lambda target: None)
    monkeypatch.setattr(page, "focused_firmware", lambda: page._gui_explicit)
    monkeypatch.setattr(
        "src.sp_flash_gui.is_sp_flash_gui_supported", lambda: True
    )

    started = []

    def fake_begin(target, callback):
        # What the real begin_external_prepare does when it starts a download.
        started.append(target)
        page.download_started.emit("Original Software 3.9.9 for Y1", "Y1")
        return True

    monkeypatch.setattr(page, "begin_external_prepare", fake_begin)
    monkeypatch.setattr(mw_mod.QMessageBox, "warning", lambda *a, **k: None)

    window._open_sp_flash_tool_gui()

    assert started and started[0]["kind"] == "online"
    flash = window._flash_page
    assert window._download_active is True
    assert flash._stack.currentWidget() is flash._downloading_view
    assert page._title.text() == tr("flash_download_in_progress")


def test_a_focused_release_survives_the_session_stop_before_preparation(window, monkeypatch):
    """Clicking the tool with a not-yet-cached release must start the download.

    Stopping the install service resets the flash state, and the page drops its
    explicit release choice when that happens. The choice is read before the
    stop, so the button still prepares the focused release instead of silently
    doing nothing.
    """
    from src import sp_flash_gui

    page = window._select_page
    rel = {
        "tag_name": "v4.4",
        "download_url": "https://example.invalid/release.zip",
        "asset_name": "release.zip",
    }
    page._current_selected_rel = rel
    page._current_package_path = ""
    page._gui_explicit = page._firmware_target_for_release(rel)
    monkeypatch.setattr(page, "_gui_target_if_ready", lambda target: None)
    monkeypatch.setattr(sp_flash_gui, "is_sp_flash_gui_supported", lambda: True)

    class _ResettingService:
        """Cancelling a session drops the page's release selection."""

        def cancel_flash(self):
            page._gui_explicit = None
            page._current_selected_rel = None

        def stop_device_monitor(self):
            page._gui_explicit = None
            page._current_selected_rel = None

    monkeypatch.setattr(window, "service", _ResettingService(), raising=False)

    started = []

    def fake_begin(target, callback):
        started.append(target)
        return True

    monkeypatch.setattr(page, "begin_external_prepare", fake_begin)
    monkeypatch.setattr(window, "_nav_to_page", lambda *a, **k: None)

    window._open_sp_flash_tool_gui()

    assert started, "the focused release must still be prepared"
    assert started[0]["kind"] == "online"
    assert started[0]["release"]["tag_name"] == "v4.4"


def test_a_successful_handoff_leaves_the_tool_running_and_quits_the_updater(
    window, monkeypatch, tmp_path
):
    """The tool keeps the port; this app leaves without sweeping it away.

    ``close()`` would end the install window and terminate stray flash tools,
    which is the tool this run just handed over to. The updater quits the event
    loop instead, so the desktop tool survives its hand-over.
    """
    from src import sp_flash_gui
    from src.ui import main_window as mw_mod

    scatter = tmp_path / "scatter.txt"
    scatter.write_text("scatter\n", encoding="utf-8")
    launched = []
    monkeypatch.setattr(
        sp_flash_gui,
        "launch_sp_flash_tool_gui",
        lambda **kwargs: launched.append(kwargs) or (True, "SP Flash Tool GUI launched"),
    )
    monkeypatch.setattr(window.service, "terminate_install_sessions", lambda *a, **k: "stopped")

    real_app = mw_mod.QApplication
    quits = []

    class _QuitRecorder:
        def quit(self):
            quits.append(True)

        def __getattr__(self, name):
            return getattr(real_app, name)

    monkeypatch.setattr(mw_mod, "QApplication", _QuitRecorder())
    # Not Linux: there the tool starts behind a password prompt, so the updater
    # waits for its process instead of leaving the moment it was asked.
    monkeypatch.setattr(mw_mod.paths, "IS_LINUX", False)
    closed = []
    monkeypatch.setattr(window, "close", lambda: closed.append(True))
    window._gui_ready = (scatter, tmp_path, "Y2")
    window._gui_handoff_ready = True
    window._gui_handoff_prepare = True

    window._complete_gui_handoff()

    assert launched, "the desktop tool must be launched"
    assert quits == [True], "the updater quits once the tool is up"
    assert window._gui_quit_started is True
    assert closed == [], "nothing closes the window: closing sweeps the tool away"
    # And only once, however many times the hand-over is re-entered.
    window._quit_after_gui_handoff()
    assert quits == [True]


def test_on_linux_the_updater_stays_up_while_the_password_prompt_is_open(
    window, monkeypatch, tmp_path
):
    """The prompt belongs to the desktop and dies with the app that spawned it.

    Quitting when the launch request returns closed the app under the running
    authorisation prompt, so the password was never accepted and SP Flash Tool
    never started. The updater now waits for the tool's own process.
    """
    from src import process_guard, sp_flash_gui
    from src.ui import main_window as mw_mod

    scatter = tmp_path / "scatter.txt"
    scatter.write_text("scatter\n", encoding="utf-8")
    launched = []
    monkeypatch.setattr(
        sp_flash_gui,
        "launch_sp_flash_tool_gui",
        lambda **kwargs: launched.append(kwargs) or (True, "waiting for the prompt"),
    )
    monkeypatch.setattr(window.service, "terminate_install_sessions", lambda *a, **k: "stopped")
    monkeypatch.setattr(mw_mod.paths, "IS_LINUX", True)
    monkeypatch.setattr(mw_mod.QTimer, "singleShot", staticmethod(lambda *a, **k: None))

    class _QuitRecorder:
        def quit(self):
            quits.append(True)

        def __getattr__(self, name):
            return getattr(real_app, name)

    real_app = mw_mod.QApplication
    quits = []
    monkeypatch.setattr(mw_mod, "QApplication", _QuitRecorder())

    # Pre-launch and prompt-up: no flash_tool process yet, then the tool appears.
    snapshots = [[], [], [(101, 1, "flash_tool")]]
    monkeypatch.setattr(
        process_guard,
        "process_snapshot",
        lambda: snapshots.pop(0) if snapshots else [(101, 1, "flash_tool")],
    )

    window._gui_ready = (scatter, tmp_path, "Y2")
    window._gui_handoff_ready = True
    window._gui_handoff_prepare = True
    window._complete_gui_handoff()

    assert launched, "the desktop tool must be asked to open"
    assert quits == [], "the updater must not leave the password prompt behind"
    timer = window._tool_launch_timer
    assert timer is not None and timer.isActive()

    timer.timeout.emit()  # polled while the prompt is still open
    assert quits == []

    timer.timeout.emit()  # the tool is running now
    assert quits == [True]
    assert window._gui_quit_started is True


def test_linux_keeps_the_updater_open_when_the_tool_never_starts(
    window, monkeypatch, tmp_path
):
    """A refused password leaves the launch offered, not a vanished window."""
    from src import process_guard, sp_flash_gui
    from src.ui import main_window as mw_mod

    scatter = tmp_path / "scatter.txt"
    scatter.write_text("scatter\n", encoding="utf-8")
    monkeypatch.setattr(
        sp_flash_gui, "launch_sp_flash_tool_gui", lambda **kwargs: (True, "waiting")
    )
    monkeypatch.setattr(window.service, "terminate_install_sessions", lambda *a, **k: "stopped")
    monkeypatch.setattr(mw_mod.paths, "IS_LINUX", True)
    monkeypatch.setattr(mw_mod.QTimer, "singleShot", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [])
    synced = []
    monkeypatch.setattr(
        window, "_sync_sp_gui_button_visibility", lambda *a, **k: synced.append(True)
    )

    window._gui_ready = (scatter, tmp_path, "Y2")
    window._gui_handoff_ready = True
    window._gui_handoff_prepare = True
    window._complete_gui_handoff()

    window._tool_launch_timer.stop()
    window._quit_when_tool_runs(timeout_seconds=0.0)
    window._tool_launch_timer.timeout.emit()

    assert not getattr(window, "_gui_quit_started", False), "the app stays open"
    assert synced, "the launch is offered again"
