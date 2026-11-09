#!/usr/bin/env python3
"""Updater CE entry point — InniUpdaterChin interface + online catalogue +
donations modal (see ASSESSMENT.md for the port map)."""

import logging
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    import os
    from pathlib import Path

    _repo_root = str(Path(__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    import src
    __package__ = "src"

from PySide6.QtCore import QEvent, QObject, Qt, QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from . import paths

logger = logging.getLogger(__name__)


def _reset_usb_after_startup(sweep=None):
    """Rebuild libusb's context once the leftover-helper sweep has finished.

    Called on every launch, before any install can start: a helper that was
    still holding the player when the last session ended leaves a handle this
    process inherited only by name, and libusb's context is the one piece of
    USB state an app can hand back to the host. Off the GUI thread, silent, and
    only after nothing else is using the bus.
    """
    import threading

    def _run():
        try:
            if sweep is not None:
                sweep.join(10)
            from .usb_recovery import recover_usb_connection

            recover_usb_connection(reap_helpers=False, reset_context=True)
        except Exception:
            logger.debug("Startup USB recovery failed", exc_info=True)

    thread = threading.Thread(target=_run, name="startup-usb-recovery", daemon=True)
    thread.start()
    return thread


def _startup_step(label, fn, /, *args, **kwargs):
    """Run one host-integration step, keeping a failure out of the launch.

    Window glass, the dark title bar and the host theme all reach into the OS
    (pyobjc on macOS, the DWM on Windows) and are already no-ops where the
    host lacks them. A step that still raises -- an OS build that changed, a
    half-installed bridge, a sandbox that denies the call -- must not cost the
    user the window it was decorating, so each is isolated and logged.
    """
    try:
        return fn(*args, **kwargs)
    except Exception:
        logger.warning("Host integration step failed: %s", label, exc_info=True)
        return None


class _FileOpenFilter(QObject):
    """Route macOS Dock/Finder open-file events to the local-package handler.

    A Dock drop on a running app never becomes a command-line argument: AppKit
    delivers a ``QFileOpenEvent`` to the application object instead. The filter
    is kept on the application for the process lifetime.
    """

    def __init__(self, on_path):
        super().__init__()
        self._on_path = on_path

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.FileOpen:
            path = event.url().toLocalFile()
            if path:
                self._on_path(path)
            return True
        return False


def _apply_native_chrome(window, *, dark=None):
    """Apply the post-show host chrome (glass, traffic lights, title bar)."""
    from .ui import glass
    from .ui.dark import is_dark as _is_dark

    if dark is None:
        dark = _is_dark()
    _startup_step("apply_glass", glass.apply_glass, window, dark=dark)
    _startup_step("configure_traffic_lights", glass.configure_traffic_lights, window, x_offset=18)
    _startup_step("apply_windows_dark_titlebar", glass.apply_windows_dark_titlebar, window, dark)


def _configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Also write to a per-user log file so frozen GUI builds (no console) keep
    # a diagnosable trail (e.g. the real exception behind an INTERNAL_ERROR).
    try:
        from .downloads import downloads_dir

        log_path = downloads_dir().parent / "updater.log"
        fh = logging.FileHandler(str(log_path), encoding="utf-8", delay=True)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(fh)
    except Exception:
        pass
    # Mirror logging records (backend traces, mtkclient's [LIB] lines, errors)
    # into the diagnostics buffers so the in-app log viewer holds them too.
    try:
        from .diagnostics import install_log_capture

        install_log_capture()
    except Exception:
        pass
    try:
        from .diagnostics import guard_broken_stream_handlers

        guard_broken_stream_handlers()
    except Exception:
        pass


_fault_log = None


def _enable_native_crash_log():
    """Write Python tracebacks for hard crashes (SIGSEGV/SIGABRT) to a file.

    A fatal interpreter error bypasses ``logging`` and ``sys.excepthook``; on a
    windowed Windows/macOS build there is no console either, so without this a
    native abort leaves no trace at all. The file sits next to updater.log.
    """
    global _fault_log
    try:
        import faulthandler

        from .downloads import downloads_dir

        path = downloads_dir().parent / "native_crash.log"
        # Keep the log bounded: start fresh once it passes 1 MiB.
        mode = "w" if path.exists() and path.stat().st_size > 1 << 20 else "a"
        _fault_log = open(path, mode, encoding="utf-8")
        import time as _time

        _fault_log.write(f"\n--- session {_time.strftime('%Y-%m-%d %H:%M:%S')} pid {__import__('os').getpid()} ---\n")
        _fault_log.flush()
        faulthandler.enable(file=_fault_log, all_threads=True)
    except Exception:
        logger.debug("Native crash log unavailable", exc_info=True)


def _mark_startup_complete() -> None:
    """Create the launcher's ready marker once Qt is running the app.

    The AppImage and tarball launchers retry a startup crash (SIGSEGV/SIGABRT)
    once with the conservative Qt stack. That retry must never run after the
    window is up: by then a flash or a themes transfer may be in progress.
    """
    import os

    marker = os.environ.get("INNIOASIS_STARTUP_MARKER")
    if not marker:
        return
    try:
        Path(marker).write_text(f"{os.getpid()}\n", encoding="utf-8")
    except OSError:
        logger.debug("Startup marker could not be written", exc_info=True)


def _running_threads() -> list:
    """Every live Python QThread, whichever module created it."""
    import gc

    from PySide6.QtCore import QThread

    found = []
    for obj in gc.get_objects():
        if isinstance(obj, type):
            continue
        try:
            if isinstance(obj, QThread) and obj.isRunning():
                found.append(obj)
        except RuntimeError:
            # Wrapper already deleted; nothing left to report.
            continue
    return found


def _leave_cleanly(window, exit_code) -> None:
    """Retire workers, then leave without waking interpreter finalisation.

    Closing the window cancels and waits for the workers it knows about, but a
    worker blocked in a socket read can outlive that wait. Python finalisation
    then pulls the interpreter out from under its ``run()`` — PySide reports
    ``Error calling Python override of QThread::run()`` — and Qt aborts the
    process for a QThread destroyed while its thread is still running, which is
    the SIGABRT (\"Abort trap: 6\") in this app's crash reports. When something is
    still running, sync the user's settings first and leave the rest of the
    teardown to the OS.
    """
    try:
        settings = getattr(window, "settings", None)
        if settings is not None:
            settings.sync()
    except Exception:
        pass

    try:
        if not _running_threads():
            return
        import os

        logging.shutdown()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(exit_code if isinstance(exit_code, int) else 0)
    except Exception:
        pass


def _portable_package_ini():
    """The bundled ``fw_package.ini`` this launch runs from, if any.

    A portable package is recognised before the window exists: the launch card
    and the start-at-login reconciliation both need to know. Only the three
    locations a packaged run puts the manifest in are checked.
    """
    for candidate in (
        paths.SP_FLASH_TOOL_DIR / "fw_package.ini",
        paths.BUNDLE_DIR / "fw_package.ini",
        paths.INSTALL_DIR / "fw_package.ini",
    ):
        if candidate.is_file():
            return candidate
    return None


def _reconcile_login_item(portable_ini) -> None:
    """Keep the start-at-login entry in step with the saved background mode.

    The entry is a projection of the preference, so every launch rewrites it:
    a moved install, a restored preference or a manually deleted entry all heal
    without the user visiting Settings again. A portable run must not replace
    the installed app's entry with its own temporary executable.
    """
    if portable_ini is not None:
        return
    try:
        from . import login_item, tray

        login_item.set_login_item(tray.run_in_background_setting())
    except Exception:
        logger.debug("Could not reconcile the start-at-login item", exc_info=True)


def _startup_presentation(start_in_tray: bool, background: bool) -> str:
    """What a launch puts on screen: ``window``, only the ``tray``, or nothing.

    A ``--tray`` launch is the start-at-login entry. It never shows the splash
    or the window; it only keeps running when the background opt-in is on. A
    stale entry whose opt-in was turned off exits instead of flashing a window
    at every login.
    """
    if not start_in_tray:
        return "window"
    return "tray" if background else "exit"


def _conceal_window_under_splash(window) -> bool:
    """Show the window for layout without putting a single pixel on screen.

    The main window has to be shown for Qt to lay it out, fit the first page
    and apply the native chrome, but a launch may put exactly one thing on
    screen: the startup card. A transparent window is still shown to Qt and to
    the host, so the fit settles, while the user only sees the card. ``False``
    means the host refused the opacity change, and the caller shows the window
    the ordinary way.
    """
    try:
        window.setWindowOpacity(0.0)
    except Exception:
        logger.debug("Window opacity unavailable during launch", exc_info=True)
        return False
    window.show()
    return True


def _reveal_launched_window(window, splash) -> None:
    """Bring the app window in and retire the launch card in one frame.

    The window is already laid out and fitted by now, so the reveal is a
    single settled window rather than the starting geometry flashing up.
    """
    try:
        window.setWindowOpacity(1.0)
    except Exception:
        logger.debug("Could not restore window opacity", exc_info=True)
    try:
        splash.finish(window)
        splash.deleteLater()
    except Exception:
        logger.debug("Splash could not close", exc_info=True)


def _maybe_shed_redundant_payload():
    """On x86/x64 Windows with SP Flash Tool ready, MTKClient is redundant.

    Shedding mtkclient saves disk footprint and simplifies the install while
    leaving SP Flash Tool completely functional. On Windows ARM64, MTKClient
    is preserved as the primary engine. All file ops are wrapped to gracefully
    no-op on read-only/system installations.
    """
    if paths.IS_MAC or not paths.IS_WINDOWS:
        return
    import platform
    import shutil
    machine = (platform.machine() or "").lower()
    intel_machines = ("x86_64", "amd64", "x64", "i386", "i486", "i586", "i686", "x86")
    if machine not in intel_machines:
        return
    sp_exe = paths.SP_FLASH_TOOL_DIR / "flash_tool.exe"
    if not sp_exe.is_file():
        return
    candidates = [
        paths.INSTALL_DIR / "mtkclient",
        paths.BUNDLE_DIR / "mtkclient",
        paths.INSTALL_DIR / "_internal" / "mtkclient",
    ]
    for c in candidates:
        if c.is_dir() and getattr(sys, "frozen", False):
            try:
                shutil.rmtree(c, ignore_errors=True)
                logging.getLogger("innioasis.startup").info("Shed unneeded mtkclient payload: %s", c)
            except Exception:
                pass


def _configure_qt_plugins() -> None:
    """Keep a packaged build on its own Qt plugins, never the host's.

    The AppImage carries its own Qt. A distro's Qt 6 plugin directory is built
    against a different Qt version, and putting it on the search path made
    Fedora load the host ``libqxcb.so`` instead of the bundled one: Qt then
    aborted with "Could not load the Qt platform plugin" before any window
    existed. Only a source checkout may borrow the host's plugin directory,
    where the distro is the installation that provides Qt at all.

    Once the plugin directory is known, :mod:`linux_desktop` aligns the
    session's platform, theme and input-method variables with what this build
    actually ships, so an inherited value cannot abort the launch on another
    desktop environment.
    """
    if not sys.platform.startswith("linux"):
        return
    import os

    from PySide6.QtCore import QCoreApplication

    from . import linux_desktop

    plugin_dir = None
    if getattr(sys, "frozen", False) or os.environ.get("APPIMAGE") or os.environ.get("APPDIR"):
        for candidate in (
            paths.BUNDLE_DIR / "PySide6" / "Qt" / "plugins",
            paths.BUNDLE_DIR / "PySide6" / "plugins",
            paths.INSTALL_DIR / "_internal" / "PySide6" / "Qt" / "plugins",
            paths.INSTALL_DIR / "_internal" / "PySide6" / "plugins",
        ):
            if (candidate / "platforms").is_dir():
                plugin_dir = candidate
                os.environ["QT_PLUGIN_PATH"] = str(candidate)
                os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(candidate / "platforms")
                break
        else:
            # No bundled directory (a build that relies on PySide6's compiled-in
            # path): drop a host value so it cannot outrank that path.
            os.environ.pop("QT_PLUGIN_PATH", None)
            os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    else:
        for host_plugin_dir in (
            "/usr/lib/qt6/plugins",
            "/usr/lib64/qt6/plugins",
            "/usr/lib/x86_64-linux-gnu/qt6/plugins",
            "/usr/lib/aarch64-linux-gnu/qt6/plugins",
            "/usr/local/lib/qt6/plugins",
        ):
            if os.path.isdir(host_plugin_dir):
                QCoreApplication.addLibraryPath(host_plugin_dir)
        plugin_dir = linux_desktop.installed_plugin_dir()

    linux_desktop.prepare_qt_environment(plugin_dir)


def main():
    # Windows: no helper process (adb, taskkill, powershell ...) may open a
    # black console window over the launch card. Install the guard before any
    # import below can spawn one; it is a no-op on other platforms.
    from . import quiet_subprocess

    quiet_subprocess.install()

    # Next, before any Qt call: PySide6 6.12.0 on Python < 3.12 leaks a None
    # reference per QPainter pen/font change, which aborted the app within a
    # minute of painting ("none_dealloc: deallocating None"). See runtime_guard.
    from . import runtime_guard

    runtime_guard.pin_singletons()
    _configure_logging()
    _enable_native_crash_log()

    # Say which front end this is, once: a frozen build carries its brand
    # inside, so this is the one line that tells support which app a log came
    # from (and confirms the packaging baked the right one).
    from . import config

    is_cli = len(sys.argv) >= 2 and sys.argv[1] in ("--flash-cli", "--mtk-reset", "--reset")
    if not is_cli:
        logging.getLogger("innioasis.startup").info(
            "Starting %s v%s (brand: %s)",
            config.get_app_name(),
            config.APP_VERSION,
            config.build_brand() or "updater_ce",
        )

    # The start-at-login entry is a projection of the saved background
    # preference: reconcile it on every launch so a moved install, a restored
    # preference or a manually deleted entry heals without another visit to
    # Settings. A portable run must not replace the installed app's entry with
    # its own temporary executable.
    portable_ini = _portable_package_ini()
    if not is_cli:
        _reconcile_login_item(portable_ini)

    if len(sys.argv) >= 2 and sys.argv[1] in ("--reset", "--mtk-reset"):
        from . import mtk_api
        mtk_api._ensure_imports()
        from mtkclient.Library.mtk_main import Main
        from argparse import ArgumentParser, Namespace
        args = Namespace(
            cmd="reset",
            debugmode=False,
            loglevel=logging.INFO,
            loader=None,
            preloader=None,
            auth=None,
            cert=None,
            is_brom=False,
            ptype=None,
            socid=None,
            crash=None,
            gpt_num_part_entries=None,
            gpt_part_entry_size=None,
            gpt_part_entry_start_lba=None,
            offset=None,
            length=None,
            step=None,
            sectors=None,
            filename=None,
            parttype=None,
            subcmd=None,
            metamode=None,
        )
        try:
            m = Main(args)
            p = ArgumentParser()
            m.run(p)
            sys.exit(0)
        except SystemExit as se:
            sys.exit(se.code)
        except Exception as e:
            logging.getLogger(__name__).debug("mtk reset error: %s", e)
            sys.exit(1)

    if len(sys.argv) >= 4 and sys.argv[1] == "--flash-cli":
        import os
        import signal
        import time
        from pathlib import Path
        from PySide6.QtCore import QCoreApplication
        from .flash_service import FlashWorker

        cli_app = QCoreApplication(sys.argv)
        # Say whether the child actually loaded libusb before it opens USB.
        # This line reaches the install log through the parent's relay and
        # turns "MTKClient did not work" into a concrete path or load failure.
        from . import mtk_api
        _libusb = mtk_api.libusb_status()
        print(
            f"[LIBUSB] path={_libusb.get('path') or 'not found'} "
            f"loaded={int(bool(_libusb.get('loaded')))}",
            flush=True,
        )
        # Positional values are read from the arguments that are not switches, so
        # a trailing ``--keep-data`` can never be mistaken for the model (the
        # last position is omitted when the caller has nothing to put there).
        cli_pos = [a for a in sys.argv[2:] if not a.startswith("--")]
        extract_dir = cli_pos[0]
        scatter_file = cli_pos[1]
        scatter_platform = cli_pos[2] if len(cli_pos) >= 3 else ""
        package_path = cli_pos[3] if len(cli_pos) >= 4 else ""
        model = cli_pos[4] if len(cli_pos) >= 5 else ""
        # Optional trailing facts, forwarded by the app (and by the terminal
        # install script): a user-chosen Download Agent and auth file.
        da_file = cli_pos[5] if len(cli_pos) >= 6 else ""
        auth_file = cli_pos[6] if len(cli_pos) >= 7 else ""

        worker = FlashWorker(
            package_path=package_path or "dummy.zip",
            method="mtk",
            pre_extracted_dir=extract_dir,
            model=model,
            da_file=da_file,
            auth_file=auth_file,
            keep_data="--keep-data" in sys.argv,
            is_rockbox="--rockbox" in sys.argv,
        )
        worker.progress.connect(lambda x: print(f"[PROGRESS] {x}", flush=True))
        worker.step_changed.connect(lambda x: print(f"[STEP] {x}", flush=True))
        worker.log_message.connect(lambda x: print(f"[LOG] {x}", flush=True))
        worker.action_changed.connect(lambda x: print(f"[ACTION] {x}", flush=True))
        worker.partition_progress_changed.connect(lambda x: print(f"[PARTITION_PROGRESS] {x}", flush=True))
        worker.eta_changed.connect(lambda x: print(f"[ETA] {x}", flush=True))

        from . import process_guard

        # The headless installer owns the same window as the guided one: no
        # tool may be left searching for a device after it exits.
        process_guard.sweep_stray_tools(reason="before headless install")
        process_guard.begin_install_session(reason="headless install")

        result_emitted = False

        def on_finished(ok, msg):
            nonlocal result_emitted
            result_emitted = True
            print(f"[RESULT] {int(ok)} {msg}", flush=True)
            process_guard.end_install_session(reason="headless install finished")
            os._exit(0 if ok else 1)

        worker.finished.connect(on_finished, Qt.ConnectionType.DirectConnection)

        def handle_sigterm(signum=None, frame=None):
            worker.cancel()
            process_guard.end_install_session(reason="headless install interrupted")
            time.sleep(0.5)
            os._exit(1)

        if os.name != "nt":
            signal.signal(signal.SIGTERM, handle_sigterm)
            signal.signal(signal.SIGINT, handle_sigterm)

        try:
            worker._flash_via_mtkclient_core(
                Path(extract_dir),
                Path(scatter_file),
                scatter_platform,
                da_file=da_file,
                auth_file=auth_file,
            )
        except SystemExit:
            raise
        except BaseException as e:
            if not result_emitted:
                print(f"[RESULT] 0 {e}", flush=True)
                process_guard.end_install_session(reason="headless install exception")
            os._exit(1)

        if not result_emitted:
            print("[RESULT] 0 MTK_FLASH_UNEXPECTED_EXIT", flush=True)
            process_guard.end_install_session(reason="headless install unexpected exit")
            os._exit(1)
        return

    _configure_qt_plugins()

    app = QApplication(sys.argv)
    runtime_guard.report_binding_health()
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)
    # Names the app to the desktop (menu entry matching, WM_CLASS, macOS Dock):
    # the packaged front end tells the environment which one it is. Read before
    # the language is restored, so the name is the stable English one.
    app.setApplicationName(config.get_app_name())
    app.setOrganizationName("innioasis")

    # A Dock/Finder open arrives as an event, not as an argument. Keep the
    # early paths queued until the window and its drop handler exist.
    external_drops: list[Path] = []

    def _queue_external_drop(path: str) -> None:
        from .drop_install import classify_drop

        candidate = Path(path)
        if classify_drop(candidate) is None:
            return
        external_drops.append(candidate)
        flush = getattr(app, "_flush_external_drops", None)
        if flush is not None:
            flush()

    _file_open_filter = _FileOpenFilter(_queue_external_drop)
    app.installEventFilter(_file_open_filter)
    app._file_open_filter = _file_open_filter

    import sys as _sys
    if _sys.platform != "darwin":
        # On Windows and Linux, set the window/taskbar icon explicitly.
        # On macOS, omit app.setWindowIcon: setting an application icon through
        # Python/Qt overrides NSApplication's bundle icon and breaks macOS's
        # native dock icon decoration (automatic coloration, dark mode tinting,
        # and squircle dynamic lighting).
        icon = paths.RESOURCES_DIR / "icon.ico"
        if not icon.exists():
            icon = paths.RESOURCES_DIR / "icon.png"
        if icon.exists():
            app.setWindowIcon(QIcon(str(icon)))

    from .i18n import initial_language, translator

    # macOS asks once, at launch, whether the app may post notifications. Asking
    # here means the first notice of the session can appear as a banner instead
    # of being filed silently while the permission question is still pending.
    try:
        from .notifications import prepare_system_notifications

        _startup_step("prepare_system_notifications", prepare_system_notifications)
    except Exception:
        logging.getLogger(__name__).debug(
            "System notification setup unavailable", exc_info=True
        )

    # Default language: the reader's saved choice, else the system's own
    # language when the app ships a catalog for it, else English. A menu bar
    # notice is localized by this too, so it is restored before the tray exists.
    try:
        settings = QSettings("innioasis", "updater")
        stored = str(settings.value("language", "") or "")
    except Exception:
        settings = None
        stored = ""
    if settings is not None:
        try:
            from .install_language import consume_installer_language

            # A fresh Windows install leaves the language chosen in Inno Setup
            # behind once. It outranks both a stored value and the host's
            # detected language; store it as the reader's choice, then delete
            # the marker so later launches keep whatever language they select.
            stored = consume_installer_language(settings) or stored
        except Exception:
            logging.getLogger(__name__).debug(
                "Installer language handoff failed", exc_info=True
            )
    translator().set_language(initial_language(stored))

    # Dropping a package on the app icon runs the app with the package as an
    # argument. Windows taskbar/desktop shortcuts pass a plain path; Linux
    # desktop entries use %U, which may be a file URL. The same parser feeds a
    # cold launch below.
    from .drop_install import external_package_from_arguments

    startup_drop = external_package_from_arguments(sys.argv[1:])

    # One copy at a time. Two windows would compete for the same USB device, SP
    # Flash Tool's settings files and the shared cache, so a second launch stops
    # here -- after the theme is applied, so the explanation matches the app --
    # and offers to end a copy left behind by a crash or hidden in the tray.
    from .single_instance import (
        clear_show_request,
        ensure_single_instance,
        show_request_stamp,
    )

    # A start-at-login launch must never raise a window: if another copy is
    # already running, this one leaves quietly instead of asking for the window.
    start_in_tray = "--tray" in sys.argv[1:] and startup_drop is None

    # Capture the marker before the guard: one left by the launch that started
    # this one is stale, while one written during startup belongs to a second
    # launch and must still bring this window forward.
    startup_show_stamp = show_request_stamp()

    # Decide what this launch is before any UI exists. A --tray launch must not
    # build the window it is about to hide -- that was the whole menu bar
    # footprint -- and a stale login entry whose opt-in was turned off leaves
    # before the splash.
    from .tray import run_in_background_enabled

    background = run_in_background_enabled()
    presentation = _startup_presentation(start_in_tray, background)
    if presentation == "exit":
        logging.getLogger("innioasis.startup").info(
            "Start-at-login launch skipped: background mode is off"
        )
        sys.exit(0)

    # A taskbar/desktop drop can start a second process while the app is
    # already running. Hand the package to that copy and leave; if no copy
    # answers, this launch continues and installs the package itself.
    if startup_drop is not None:
        from .single_instance import request_drop

        if request_drop(startup_drop, timeout_ms=300):
            sys.exit(0)

    # The window's theme is a QSS stylesheet plus a palette: roughly 10 MB of Qt
    # style machinery the menu bar item never reads. A tray-only launch applies
    # it when the window is actually created instead.
    if presentation == "window":
        from .ui.dark import apply_theme

        _startup_step("apply_theme", apply_theme, app, force_dark=None)
        app._theme_applied = True

    if not ensure_single_instance(app, quiet=start_in_tray):
        sys.exit(0)

    # This copy is alive: publish what it is doing, so a launch that gets past
    # the guard (a takeover, an older build) can see the install before it
    # sweeps a tool or offers to end this copy.
    try:
        from . import instance_marker

        instance_marker.start()
        app.aboutToQuit.connect(instance_marker.clear)
    except Exception:
        logging.getLogger(__name__).debug(
            "Could not publish the running-copy marker", exc_info=True
        )

    from PySide6.QtCore import QTimer

    # The window lives here, not in a local: a tray launch creates it on demand
    # and an idle stay in the tray destroys it again, so nothing may keep a
    # reference to it after the shed. The agent clears this before deleting.
    holder = {"window": None}
    agent = None
    if startup_drop is not None:
        external_drops.append(startup_drop)

    def _forget_window() -> None:
        holder["window"] = None

    from . import process_guard

    def _sweep_on_startup() -> None:
        # A background-only copy has no window to report into, and does not
        # touch the USB bus until the user asks for an install. The first
        # window built runs this instead.
        window = holder["window"]
        if window is None:
            return
        app._startup_sweep_started = True

        def _report(killed):
            live = holder["window"]
            if live is not None:
                live.sweep_report.emit(killed)
            else:
                logging.getLogger(__name__).debug(
                    "Startup sweep finished without a window: %s",
                    process_guard.describe(killed),
                )

        sweep = process_guard.sweep_async(reason="startup", on_done=_report)
        _reset_usb_after_startup(sweep)

    def _create_window():
        """Build the one window, with the host chrome it needs."""
        from .ui.dark import apply_theme, is_dark
        from .ui.glass import apply_windows_dark_titlebar, prepare_window_for_glass
        from .ui.main_window import MainWindow, apply_interface_direction

        apply_interface_direction(app)
        if not getattr(app, "_theme_applied", False):
            _startup_step("apply_theme", apply_theme, app, force_dark=None)
            app._theme_applied = True
        window = MainWindow()
        holder["window"] = window
        if agent is not None:
            window._background_agent = agent
            agent.adopt_window(window)
        _startup_step("prepare_window_for_glass", prepare_window_for_glass, window)
        _startup_step(
            "apply_windows_dark_titlebar",
            apply_windows_dark_titlebar,
            window,
            is_dark(),
        )
        if presentation == "tray" and not getattr(app, "_startup_sweep_started", False):
            QTimer.singleShot(0, _sweep_on_startup)
        return window

    if background:
        from .background_agent import BackgroundAgent

        agent = BackgroundAgent(app, _create_window, _forget_window)
        try:
            app.aboutToQuit.connect(agent.shutdown)
        except Exception:
            pass

    def _show_main_window(route=None) -> None:
        """A second launch, the Dock icon or a notification asks for the window."""
        if agent is not None:
            agent.show_window(route)
            return
        window = holder["window"]
        if window is not None:
            window._show_from_tray()

    external_drop_ready = {"value": False}
    external_drop_attempts = {"count": 0}

    def _flush_external_drops() -> None:
        """Show the window if needed, then hand queued packages to it."""
        if not external_drop_ready["value"] or not external_drops:
            return
        window = holder["window"]
        if window is None:
            _show_main_window()
            window = holder["window"]
            if window is None:
                if external_drop_attempts["count"] < 25:
                    external_drop_attempts["count"] += 1
                    QTimer.singleShot(120, _flush_external_drops)
                return
        external_drop_attempts["count"] = 0
        while external_drops:
            window.accept_local_package(external_drops.pop(0))

    app._flush_external_drops = _flush_external_drops

    def _deliver_external_drop(path: str) -> None:
        """Accept a package forwarded by a second launch's focus socket."""
        from .drop_install import classify_drop

        candidate = Path(path)
        if classify_drop(candidate) is None:
            return
        external_drops.append(candidate)
        _flush_external_drops()

    try:
        from .single_instance import start_focus_server

        _focus_server = start_focus_server()
        if _focus_server is not None:
            _focus_server.focus_requested.connect(lambda: _show_main_window())
            _focus_server.drop_requested.connect(_deliver_external_drop)
    except Exception:
        logging.getLogger(__name__).debug(
            "Single-instance focus channel could not start", exc_info=True
        )

    # The file channel is the backstop: a copy started from another folder, an
    # older build, or a host that refuses local sockets still gets its window
    # back when the user opens the app from its shortcut. It does not depend on
    # the socket above, so it starts even when that one could not.
    try:
        from .single_instance import watch_show_requests

        app._show_request_watcher = watch_show_requests(
            _show_main_window, baseline=startup_show_stamp
        )
    except Exception:
        logging.getLogger(__name__).debug(
            "Single-instance show-request watcher could not start", exc_info=True
        )

    # Reopen window when clicking Dock icon on macOS while hidden in the menu bar / tray
    if _sys.platform == "darwin":
        try:
            from AppKit import NSApplication
            import objc

            ns_app = NSApplication.sharedApplication()
            delegate = ns_app.delegate()
            if delegate is not None:
                orig_reopen = getattr(
                    delegate, "applicationShouldHandleReopen_hasVisibleWindows_", None
                )

                class QCocoaApplicationDelegate(objc.Category(delegate.__class__)):
                    def applicationShouldHandleReopen_hasVisibleWindows_(self, sender, has_visible):
                        window = holder["window"]
                        if not has_visible or (
                            window is not None and not window.isVisible()
                        ):
                            _show_main_window()
                        if orig_reopen is not None:
                            try:
                                return orig_reopen(sender, has_visible)
                            except Exception:
                                pass
                        return True
        except Exception:
            logging.getLogger(__name__).debug(
                "Could not hook macOS applicationShouldHandleReopen", exc_info=True
            )

    if background:
        app.setQuitOnLastWindowClosed(False)
    elif _sys.platform == "darwin":
        # macOS builds temporarily have no background mode: the window is the
        # app, so closing it ends the process instead of leaving an invisible
        # copy behind with no menu bar item to bring it back.
        app.setQuitOnLastWindowClosed(True)

    startup_splash = None
    if _sys.platform.startswith("win") and presentation == "window":
        try:
            from .ui.splash import show_startup_splash

            # The card names the software the run will install, so read the
            # package manifest before the window exists. A manifest that cannot
            # be read still makes this a portable run; it just has no name.
            firmware_name = ""
            firmware_version = ""
            if portable_ini is not None:
                try:
                    from .fw_package import read_fw_package_ini

                    meta = read_fw_package_ini(portable_ini)
                    if getattr(meta, "enabled", True):
                        firmware_name = str(meta.name or "")
                        firmware_version = str(meta.version or "")
                except Exception:
                    logging.getLogger(__name__).debug(
                        "Portable package manifest unreadable", exc_info=True
                    )

            startup_splash = show_startup_splash(
                portable_fw=portable_ini is not None,
                firmware_name=firmware_name,
                firmware_version=firmware_version,
            )
        except Exception:
            logging.getLogger(__name__).debug("Startup splash unavailable", exc_info=True)

    if presentation == "window":
        window = _create_window()
        # The launch card owns the screen until the app is ready. With a card,
        # show the window beneath it with no pixels of its own, so the first
        # layout, fit and native chrome settle unseen; the reveal retires the
        # card. Without one -- macOS and Linux -- the window is the launch.
        concealed = startup_splash is not None and _conceal_window_under_splash(window)
        if not concealed:
            window.show()
        # The window is up: acknowledge a marker that was waiting at launch.
        # If a new request arrived meanwhile its stamp differs, so it stays for
        # the watcher to handle.
        clear_show_request(startup_show_stamp)
        from .ui.dark import is_dark

        _apply_native_chrome(window, dark=is_dark())
        if startup_splash is not None:
            # Keep the card up over the first layout and fit, then reveal the
            # window as it retires: a settled window, never the starting
            # geometry flashing up behind the card. The card still retires
            # even if the main window never exposes.
            splash = startup_splash
            startup_splash = None
            QTimer.singleShot(220, lambda: _reveal_launched_window(window, splash))
        QTimer.singleShot(1500, _sweep_on_startup)
    else:
        # Start-at-login: the menu bar item is the whole app until it is asked
        # for a window.
        if agent is not None:
            agent.start(show_tray=True)
        clear_show_request(startup_show_stamp)

    # A package that arrived before the window existed (Dock open, taskbar
    # argument, forwarded second launch) now has somewhere to go.
    external_drop_ready["value"] = True
    if external_drops:
        QTimer.singleShot(0, _flush_external_drops)

    # Work the first screen does not need: the catalogue refresh, the donation
    # marker and shedding the redundant payload. None of it paints anything, and
    # none of it may hold the window back, so it runs once the window is already
    # up (or immediately in a background-only session). Linux needs no startup
    # system preparation: MTKClient is the default there, and the optional SP
    # Flash Tool is staged only when that backend is actually used.
    def _deferred_startup() -> None:
        _maybe_shed_redundant_payload()
        # A windowless session has no donation bar or release feed on screen:
        # the background agent keeps the manifest and notices current, and
        # nothing else is fetched until a window is shown. A window launch
        # still refreshes everything for the donations bar and feeds.
        if presentation != "tray":
            try:
                from . import online_data

                online_data.refresh_all_async()
            except Exception:
                logging.getLogger(__name__).debug(
                    "Online data refresh could not start", exc_info=True
                )
        try:
            from . import player_update_check

            player_update_check.sync_sp_donation_marker()
        except Exception:
            logging.getLogger(__name__).debug("Donation marker check failed", exc_info=True)
        try:
            from . import theme_pack

            # The 360p themes archive is no longer bundled: warm the cache once
            # a week so the first themes install has a current pack to merge.
            theme_pack.refresh_theme_pack_if_due_async()
        except Exception:
            logging.getLogger(__name__).debug(
                "Weekly theme pack refresh could not start", exc_info=True
            )

    QTimer.singleShot(0, _deferred_startup)

    # Match host appearance live: light/dark switches, desktop accent colours,
    # system font changes -- the same way native apps follow the OS. The window
    # normally brings the watcher with it (and registers it on the app); one is
    # installed here only when it did not, so the host is never watched twice.
    # A tray-built window installs its own when it is created.
    if presentation == "window":
        from .ui.dark import ThemeWatcher, is_dark
        from .ui.glass import (
            apply_glass,
            apply_windows_dark_titlebar,
            configure_traffic_lights,
        )

        theme_watcher = getattr(app, "_theme_watcher", None)
        if theme_watcher is None:

            def _on_theme_applied():
                live = holder["window"]
                if live is None:
                    return
                apply_windows_dark_titlebar(live, is_dark())
                apply_glass(live)
                configure_traffic_lights(live)

            theme_watcher = ThemeWatcher(app, on_apply=_on_theme_applied)
            theme_watcher.install()
            # Keep a reference for the lifetime of the app; the watcher owns
            # timers and an event filter on the QApplication itself.
            app._theme_watcher = theme_watcher

    # One event-loop turn is not enough: let the first page paint before the
    # launcher's startup-crash window closes.
    QTimer.singleShot(1500, _mark_startup_complete)

    exit_code = app.exec()
    _leave_cleanly(holder["window"], exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()