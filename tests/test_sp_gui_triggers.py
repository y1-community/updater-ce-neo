"""Every path that opens SP Flash Tool, exercised end to end.

The tool itself is stood in for by a stub ``flash_tool`` in a temporary tool
directory: it records the argv/cwd/environment it was started with and stays
alive, so the hand-off can be observed without root, a display, or the real
payload. A stub ``pkexec`` first on ``PATH`` stands in for the accepted-password
privilege agent, so the real launcher (Linux staging, ``history.ini``, the
detached process, the quit poll) is what runs.

The triggers covered are the ones a user can reach:
settings install mode on package selection, the sidebar launcher, the
diagnostics "Launch GUI" button, the failure page's launcher, the settings page
button, firmware prepared by the download worker, and the bundled firmware that
auto-starts at launch.
"""

import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the SP Flash Tool hand-off differs per platform; this suite drives Linux",
)

STUB_TOOL = '''#!/usr/bin/env python3
import json, os, sys, time

json.dump(
    {
        "argv": sys.argv[1:],
        "cwd": os.getcwd(),
        "pid": os.getpid(),
        "env": {
            key: value
            for key, value in os.environ.items()
            if key in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY", "LD_LIBRARY_PATH", "QT_PLUGIN_PATH")
        },
    },
    open(os.environ["SP_STUB_MARKER"], "w"),
)
time.sleep(10)
'''

SCATTER_BODY = "file_name: fat_sparse.img\n"


class StubWorld:
    """A temporary tool install, firmware package and privilege agent."""

    def __init__(self, root: Path):
        self.root = root
        self.tool_dir = root / "SP_Flash_Tool_stub"
        self.tool_dir.mkdir()
        self.marker = root / "launch.json"
        self.downloads = root / "downloads"
        self.downloads.mkdir()
        self.extract = self.downloads / "rom_extracted"
        self.extract.mkdir()
        self.scatter = self.extract / "MT6582_Android_scatter.txt"
        self.scatter.write_text(SCATTER_BODY, encoding="utf-8")
        (self.extract / "fat_sparse.img").write_bytes(b"img")
        self.package = self.downloads / "rom.zip"
        self.package.write_bytes(b"PK\x03\x04")

        tool = self.tool_dir / "flash_tool"
        tool.write_text(STUB_TOOL, encoding="utf-8")
        tool.chmod(tool.stat().st_mode | stat.S_IXUSR)
        (self.tool_dir / "MTK_AllInOne_DA.bin").write_bytes(b"DA")

        bin_dir = root / "bin"
        bin_dir.mkdir()
        pkexec = bin_dir / "pkexec"
        # What an accepted password prompt does: run the requested command.
        pkexec.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        pkexec.chmod(0o755)
        self.bin_dir = bin_dir

    def launched(self):
        """The recorded launch, or None. The marker is consumed per launch."""
        if not self.marker.is_file():
            return None
        data = json.loads(self.marker.read_text(encoding="utf-8"))
        self.marker.unlink()
        return data

    def wait_for_launch(self, app, deadline=8.0):
        end = time.time() + deadline
        while time.time() < end:
            app.processEvents()
            got = self.launched()
            if got:
                return got
            time.sleep(0.05)
        return None

    def clear(self):
        self.marker.unlink(missing_ok=True)

    def kill(self):
        pid = None
        data = self.launched()
        if data:
            pid = data.get("pid")
        if pid:
            try:
                os.kill(int(pid), 9)
            except OSError:
                pass


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    """Temp tool/firmware tree with the launcher pointed at it."""
    import src.downloads as downloads
    import src.device_tracking as device_tracking
    import src.flash_service as flash_service
    import src.linux_sp_flash as linux_sp_flash
    import src.sp_flash_gui as sp_flash_gui

    stub = StubWorld(tmp_path_factory.mktemp("sp-gui"))
    saved_env = {key: os.environ.get(key) for key in ("PATH", "SP_FLASH_TOOL_DIR", "SP_STUB_MARKER", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")}
    saved = {
        (module, name): getattr(module, name)
        for module, names in (
            (linux_sp_flash, ("bundled_dir", "stage_dir", "ensure_linux_sp_flash_tool")),
            (flash_service, ("prune_extracted_cache",)),
            (downloads, ("downloads_dir",)),
        )
        for name in names
    }

    thread_patches = _silence_startup_workers()
    os.environ["PATH"] = f"{stub.bin_dir}:{os.environ.get('PATH', '')}"
    os.environ["SP_FLASH_TOOL_DIR"] = str(stub.tool_dir)
    os.environ["SP_STUB_MARKER"] = str(stub.marker)
    os.environ["XDG_CONFIG_HOME"] = str(stub.root / "config")
    os.environ["XDG_CACHE_HOME"] = str(stub.root / "cache")

    # Never touch the checkout's own payload, and never reach the network.
    linux_sp_flash.bundled_dir = lambda: None
    linux_sp_flash.stage_dir = lambda: stub.tool_dir
    linux_sp_flash.ensure_linux_sp_flash_tool = lambda *a, **k: (True, "stub")
    flash_service.prune_extracted_cache = lambda *a, **k: None
    downloads.downloads_dir = lambda: stub.downloads

    try:
        yield stub
    finally:
        stub.kill()
        _stop_qt_threads()
        for cls, start in thread_patches:
            cls.start = start
        for (module, name), value in saved.items():
            setattr(module, name, value)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture()
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


@pytest.fixture()
def window(app, world):
    from src.ui import main_window as mw

    w = mw.MainWindow()
    w.show()
    app.processEvents()
    try:
        yield w
    finally:
        timer = getattr(w, "_tool_launch_timer", None)
        if timer is not None:
            timer.stop()
        # A leaked one-shot check on a closed window would quit the next test's
        # application as soon as it saw the previous stub still running.
        w._gui_quit_started = True
        w.close()
        app.processEvents()
        _stop_qt_threads()
        world.kill()


def _silence_startup_workers():
    """Keep the window's catalogue/update fetches from starting threads.

    Those fetches reach the network, are irrelevant to the hand-off, and a
    thread still running at interpreter shutdown aborts the process ("QThread:
    Destroyed while thread is still running"). Returns the patches to undo.
    """
    from PySide6.QtCore import QThread

    for name in (
        "src.updates",
        "src.network",
        "src.manifest",
        "src.translate",
        "src.theme_sync",
        "src.ui.main_window",
        "src.ui.select_page",
        "src.ui.release_icon",
    ):
        try:
            __import__(name)
        except Exception:
            continue

    patches = []
    seen = set()
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if not name.startswith("src"):
            continue
        for value in list(vars(module).values()):
            if not isinstance(value, type) or value is QThread:
                continue
            if not issubclass(value, QThread) or value in seen:
                continue
            seen.add(value)
            patches.append((value, value.start))
            value.start = lambda self, *args, **kwargs: None  # noqa: B023 - per class
    return patches


def _stop_qt_threads() -> None:
    """Finish background workers, so interpreter shutdown has no live QThread.

    The window starts a catalogue/update fetch on construction. Destruction of a
    ``QThread`` that is still running aborts the process ("QThread: Destroyed
    while thread is still running") at the end of the session.
    """
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for thread in app.findChildren(QThread):
        try:
            thread.requestInterruption()
            thread.quit()
            thread.wait(3000)
        except Exception:
            pass
    app.processEvents()


def _seed_cache(world, settings, *, scatter=None, extract=None):
    from src import device_tracking

    device_tracking.record_latest_package(
        model="Y2",
        software_name="Original Software 3.1.2 for Y2",
        tag_name="v3.1.2",
        package_path=str(world.package),
        extract_dir=str(extract or world.extract),
        scatter_path=str(scatter or world.scatter),
        settings=settings,
    )


# --------------------------------------------------------------------------
# Settings install mode: the tool opens on its own when a package is selected
# --------------------------------------------------------------------------

def test_settings_install_mode_opens_the_tool_on_package_selection(window, world, app):
    from src import device_tracking

    world.clear()
    device_tracking.set_sp_gui_install_enabled(True, window.settings)
    _seed_cache(world, window.settings)

    window._select_page.package_selected.emit(
        str(world.package), world.package.name, "Y2"
    )

    got = world.wait_for_launch(app)
    assert got is not None, "selecting a package in SP-GUI mode must open the tool"
    assert Path(got["cwd"]).resolve() == world.tool_dir.resolve()
    history = (world.tool_dir / "history.ini").read_text(encoding="utf-8")
    assert str(world.scatter.resolve()) in history, history
    assert "DISPLAY" in got["env"] or "WAYLAND_DISPLAY" in got["env"]


# --------------------------------------------------------------------------
# The sidebar launcher
# --------------------------------------------------------------------------

def test_the_sidebar_launcher_opens_the_cached_firmware(window, world, app):
    from src import device_tracking

    world.clear()
    device_tracking.set_sp_gui_install_enabled(False, window.settings)
    _seed_cache(world, window.settings)
    window._select_page._gui_explicit = None
    window._select_page._current_selected_rel = None

    assert window._sync_sp_gui_button_visibility() is True
    window._open_sp_flash_tool_gui()

    assert world.wait_for_launch(app) is not None


def test_an_incomplete_cache_is_never_launched(window, world, app):
    from src import device_tracking

    world.clear()
    broken = world.downloads / "broken_extract"
    broken.mkdir(exist_ok=True)
    broken_scatter = broken / "MT6582_Android_scatter.txt"
    broken_scatter.write_text("file_name: missing.img\n", encoding="utf-8")
    device_tracking.set_sp_gui_install_enabled(False, window.settings)
    _seed_cache(world, window.settings, scatter=broken_scatter, extract=broken)
    window._select_page._gui_explicit = None
    window._select_page._current_selected_rel = None

    window._open_sp_flash_tool_gui()

    assert world.wait_for_launch(app, deadline=1.5) is None


def test_nothing_is_offered_when_no_firmware_is_available(window, world, app):
    from src import device_tracking

    world.clear()
    device_tracking.clear_latest_package(window.settings)
    device_tracking.set_sp_gui_install_enabled(False, window.settings)
    window._select_page._gui_explicit = None
    window._select_page._current_selected_rel = None
    window._select_page._current_package_path = ""

    assert window._sync_sp_gui_button_visibility() is False
    window._open_sp_flash_tool_gui()

    assert world.wait_for_launch(app, deadline=1.5) is None


def test_a_second_request_does_not_open_a_second_tool(window, world, app, monkeypatch):
    """The privilege prompt stays up for minutes; a second click must not race it."""
    import src.sp_flash_gui as sp_flash_gui

    world.clear()
    real_launch = sp_flash_gui.launch_sp_flash_tool_gui
    calls = []

    def recorder(**kwargs):
        calls.append(kwargs)
        return True, "requested"

    monkeypatch.setattr(sp_flash_gui, "launch_sp_flash_tool_gui", recorder)
    try:
        _seed_cache(world, window.settings)
        window._select_page._gui_explicit = None
        window._select_page._current_selected_rel = None
        # The tool has not started yet: the password prompt is still up.
        from src import process_guard

        monkeypatch.setattr(process_guard, "process_snapshot", lambda: [])

        window._open_sp_flash_tool_gui()
        assert len(calls) == 1
        assert window._gui_ready is None, "the ready tuple is consumed by the launch"

        window._open_sp_flash_tool_gui()
        assert len(calls) == 1, "a second request while the prompt is open launched again"
    finally:
        monkeypatch.setattr(sp_flash_gui, "launch_sp_flash_tool_gui", real_launch)


# --------------------------------------------------------------------------
# The other launchers
# --------------------------------------------------------------------------

def test_diagnostics_launch_gui_without_arguments(world, app):
    import src.sp_flash_gui as sp_flash_gui
    from src import device_tracking

    world.clear()
    settings = device_tracking._get_settings(None)
    _seed_cache(world, settings)

    ok, msg = sp_flash_gui.open_sp_flash_tool_gui()

    assert ok, msg
    assert world.wait_for_launch(app) is not None


def test_the_failure_page_button_opens_the_tool_in_advanced_mode(window, world, app):
    world.clear()
    _seed_cache(world, window.settings)
    window._select_page._gui_explicit = None
    window._select_page._current_selected_rel = None

    window._error_page.set_advanced_mode(False)
    assert not window._error_page._sp_gui_btn.isVisibleTo(window._error_page)
    window._error_page.set_advanced_mode(True)
    assert window._error_page._sp_gui_btn.isVisibleTo(window._error_page)

    window._error_page._sp_gui_cb()
    assert world.wait_for_launch(app) is not None


def test_the_settings_page_button_opens_the_tool(window, world, app):
    from src import device_tracking

    world.clear()
    device_tracking.set_sp_gui_install_enabled(False, window.settings)
    _seed_cache(world, window.settings)
    window._select_page._gui_explicit = None
    window._select_page._current_selected_rel = None

    window._settings_page._on_launch_sp()

    assert world.wait_for_launch(app) is not None


def test_firmware_prepared_by_the_worker_opens_when_it_finishes(window, world, app):
    world.clear()
    _seed_cache(world, window.settings)
    window._gui_launch_when_ready = True
    window._gui_after_prepare = "launch"
    window._download_active = True

    window._on_gui_prepare_done(True, str(world.extract), "")

    got = world.wait_for_launch(app)
    assert got is not None
    assert Path(got["cwd"]).resolve() == world.tool_dir.resolve()


def test_bundled_firmware_opens_at_startup(window, world, app, monkeypatch):
    import src.device_tracking as device_tracking
    import src.fw_package as fw

    world.clear()
    device_tracking.set_sp_gui_install_enabled(True, window.settings)
    _seed_cache(world, window.settings)

    class _Package:
        folder = world.extract
        model = "Y2"
        type = ""
        has_rockbox = False

    monkeypatch.setattr(fw, "find_bundled_firmware", lambda: _Package())
    monkeypatch.setattr(fw, "stage_bundled_firmware", lambda package: world.scatter)
    monkeypatch.setattr(
        fw, "package_install_heading", lambda package: "Bundled Original Software for Y2"
    )

    window._prefill_fw_package()

    assert world.wait_for_launch(app) is not None


# --------------------------------------------------------------------------
# Refusals: unsupported hosts, missing tool, missing scatter
# --------------------------------------------------------------------------

def test_an_unsupported_host_warns_instead_of_launching(window, world, app, monkeypatch):
    import src.linux_sp_flash as linux_sp_flash
    import src.paths as paths
    import src.sp_flash_gui as sp_flash_gui
    from src.ui import main_window as mw

    world.clear()
    warnings = []
    monkeypatch.setattr(
        mw.QMessageBox, "warning", lambda *a, **k: warnings.append(a[2] if len(a) > 2 else a)
    )
    monkeypatch.setattr(sp_flash_gui, "is_sp_flash_gui_supported", lambda: False)
    window._begin_sp_gui_install()
    assert warnings, "the install entry point must say why it cannot open the tool"

    monkeypatch.setattr(linux_sp_flash, "arch_supported", lambda: False)
    arch_ok, arch_msg = sp_flash_gui.launch_sp_flash_tool_gui()
    assert not arch_ok and "x86" in arch_msg

    monkeypatch.setattr(paths, "IS_MAC", True)
    mac_ok, mac_msg = sp_flash_gui.launch_sp_flash_tool_gui()
    assert not mac_ok and "macOS" in mac_msg
    assert world.launched() is None


def test_a_missing_tool_install_reports_instead_of_raising(world, monkeypatch):
    import src.linux_sp_flash as linux_sp_flash
    import src.sp_flash_gui as sp_flash_gui

    world.clear()
    monkeypatch.setattr(sp_flash_gui, "find_sp_flash_tool_dirs", lambda: [])
    monkeypatch.setattr(linux_sp_flash, "stage_dir", lambda: world.root / "nope")
    monkeypatch.setattr(linux_sp_flash, "bundled_dir", lambda: None)

    ok, msg = sp_flash_gui.launch_sp_flash_tool_gui()

    assert not ok and "not found" in msg.lower()
    assert world.launched() is None


def test_a_missing_scatter_refuses_to_open(world):
    import src.sp_flash_gui as sp_flash_gui

    world.clear()
    ok, msg = sp_flash_gui.launch_sp_flash_tool_gui(
        scatter_path=world.root / "does-not-exist.txt"
    )

    assert not ok
    assert world.launched() is None


# --------------------------------------------------------------------------
# Leaving: the updater waits for the tool's own process
# --------------------------------------------------------------------------

def test_the_updater_waits_for_the_tools_process_before_leaving(window, world, app, monkeypatch):
    from src import process_guard
    from src.ui import main_window as mw

    world.clear()
    quits = []

    class _App:
        def quit(self):
            quits.append(True)

        def __getattr__(self, name):
            return getattr(mw.QApplication, name)

    monkeypatch.setattr(mw, "QApplication", _App())
    window._launch_sp_gui(world.scatter, world.extract, "Y2")

    timer = window._tool_launch_timer
    assert timer is not None and timer.isActive()
    assert quits == [], "the updater left before the tool was confirmed running"

    assert world.wait_for_launch(app) is not None
    deadline = time.time() + 5
    saw_tool = False
    while time.time() < deadline and not saw_tool:
        saw_tool = any(
            "flash_tool" in (row[2] or "") for row in process_guard.process_snapshot()
        )
        if not saw_tool:
            time.sleep(0.1)
    assert saw_tool

    timer.timeout.emit()
    assert quits == [True], "the updater leaves once the tool is running"
    assert window._gui_quit_started is True


def test_a_refused_prompt_offers_the_launch_again(window, world, app, monkeypatch):
    import src.process_guard as process_guard
    import src.sp_flash_gui as sp_flash_gui
    from src.ui import main_window as mw

    world.clear()
    monkeypatch.setattr(
        sp_flash_gui, "launch_sp_flash_tool_gui", lambda **kwargs: (True, "waiting")
    )
    monkeypatch.setattr(mw.QTimer, "singleShot", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(process_guard, "process_snapshot", lambda: [])
    synced = []
    monkeypatch.setattr(
        window, "_sync_sp_gui_button_visibility", lambda *a, **k: synced.append(True) or True
    )

    window._gui_ready = (world.scatter, world.extract, "Y2")
    window._complete_gui_handoff()
    assert window._gui_launch_in_flight is True

    window._tool_launch_timer.stop()
    window._quit_when_tool_runs(timeout_seconds=0.0)
    window._tool_launch_timer.timeout.emit()

    assert window._gui_launch_in_flight is False, "the launch stays offered"
    assert synced
    assert not getattr(window, "_gui_quit_started", False)


# --------------------------------------------------------------------------
# A read-only install (AppImage / system-wide) must still be usable
# --------------------------------------------------------------------------

def _read_only_tool_tree(root: Path) -> Path:
    root.mkdir()
    tool = root / "flash_tool"
    tool.write_text(STUB_TOOL, encoding="utf-8")
    tool.chmod(0o555)
    (root / "MTK_AllInOne_DA.bin").write_bytes(b"DA")
    root.chmod(0o555)
    return root


def test_a_read_only_install_is_copied_to_the_user_cache_and_launched(
    world, app, monkeypatch
):
    """copytree copies directory modes too, so the read-only copy stayed read-only.

    Without fixing the copy the launch refused with "Could not write history.ini
    with the extracted scatter" — exactly the AppImage / system-wide install this
    copy exists for.
    """
    import src.linux_sp_flash as linux_sp_flash
    import src.sp_flash_gui as sp_flash_gui
    from src import device_tracking

    world.clear()
    read_only = _read_only_tool_tree(world.root / "readonly_tool")
    try:
        monkeypatch.setattr(sp_flash_gui, "find_sp_flash_tool_dirs", lambda: [read_only])
        monkeypatch.setattr(linux_sp_flash, "stage_dir", lambda: read_only)
        _seed_cache(world, device_tracking._get_settings(None))

        ok, msg = sp_flash_gui.launch_sp_flash_tool_gui()
        got = world.wait_for_launch(app)
        user_copy = Path(os.environ["XDG_CACHE_HOME"]) / "updater-ce" / "SP_Flash_Tool"
        assert ok, msg
        assert got is not None and Path(got["cwd"]).resolve() == user_copy.resolve()
        history = (user_copy / "history.ini").read_text(encoding="utf-8")
        assert str(world.scatter.resolve()) in history
    finally:
        read_only.chmod(0o755)


def test_a_staged_copy_of_a_complete_bundled_payload_is_writable(
    world, monkeypatch
):
    import src.linux_sp_flash as linux_sp_flash

    bundled = world.root / "bundled_tool"
    bundled.mkdir()
    for relative in linux_sp_flash.FLASH_TOOL_LINUX_REQUIRED_FILES:
        target = bundled / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
    (bundled / "flash_tool").chmod(0o555)
    bundled.chmod(0o555)
    monkeypatch.setattr(linux_sp_flash, "bundled_dir", lambda: bundled)
    monkeypatch.setenv("XDG_CACHE_HOME", str(world.root / "stage-cache"))
    try:
        staged = linux_sp_flash.stage_dir()
        assert staged != bundled, "a read-only payload must be staged first"
        probe = staged / "history.ini"
        probe.write_text("[RecentOpenFile]\nlastDir=/tmp/scatter.txt\n", encoding="utf-8")
        assert probe.is_file()
    finally:
        bundled.chmod(0o755)
        (bundled / "flash_tool").chmod(0o755)
