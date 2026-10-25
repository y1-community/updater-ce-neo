#!/usr/bin/env python3
"""Updater CE entry point — InniUpdaterChin interface + online catalogue +
donations modal (see ASSESSMENT.md for the port map)."""

import logging
import sys

if __package__ is None or __package__ == "":
    import os
    from pathlib import Path

    _repo_root = str(Path(__file__).resolve().parent.parent)
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    import src
    __package__ = "src"

from PySide6.QtCore import Qt, QSettings
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


def _prepare_linux_sp_flash_tool() -> None:
    """Prepare Linux system for SP Flash Tool USB access.

    This ensures the SP Flash Tool can communicate with MediaTek devices
    on Linux by configuring udev rules, group permissions, and killing
    conflicting services. This prevents the S_COM_PORT_OPEN_FAIL (1013)
    error that crashes the app before the GUI opens.
    """
    try:
        from . import linux_sp_flash
        from . import process_guard

        # Free any COM ports held by stale processes
        try:
            process_guard.free_com_ports()
            logger.debug("Freed COM ports on Linux startup")
        except Exception as e:
            logger.debug("Failed to free COM ports: %s", e)

        # Check for and report conflicting services
        try:
            conflicts = process_guard.check_service_conflicts()
            for conflict in conflicts:
                logger.warning(
                    "Conflicting service on Linux: %s - %s",
                    conflict["severity"],
                    conflict["message"],
                )
        except Exception as e:
            logger.debug("Could not check service conflicts: %s", e)

        # Check udev rules status and install if missing
        try:
            udev_ok, udev_msg = linux_sp_flash.check_udev_rules()
            logger.info("Udev rules status: %s", udev_msg)
            if not udev_ok:
                logger.info("Installing MediaTek udev rules...")
                install_result, install_msg = linux_sp_flash.install_udev_rules()
                logger.info("Udev rules installation: %s", install_msg)
        except Exception as e:
            logger.debug("Could not check/install udev rules: %s", e)

        # Check user permissions and add to group if needed
        try:
            serial_group = "dialout"  # default, will be detected per distro
            perm_ok, perm_msg = linux_sp_flash.check_user_permissions(serial_group)
            logger.info("User permissions: %s", perm_msg)
            if not perm_ok:
                logger.warning(
                    "User not in '%s' group. This may prevent SP Flash Tool from "
                    "accessing MediaTek USB devices. Ensure group membership "
                    "and restart the application.",
                    serial_group,
                )
        except Exception as e:
            logger.debug("Could not check user permissions: %s", e)

        # Check for conflicting services and attempt to stop them
        try:
            conflicts = process_guard.check_service_conflicts()
            for conflict in conflicts:
                service_name = conflict["service"]
                logger.warning(
                    "Stopping conflicting service: %s", service_name,
                )
                try:
                    subprocess.run(
                        ["systemctl", "stop", service_name],
                        capture_output=True,
                        timeout=3,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Could not check/stop service conflicts: %s", e)

        # Run silent system prep (stages libpng12, option.ini, etc.)
        try:
            ok = linux_sp_flash.silent_system_prep()
            if ok:
                logger.info("Linux system preparation completed successfully")
            else:
                logger.warning("Linux system preparation had non-fatal issues")
        except Exception as e:
            logger.debug("Linux system preparation exception: %s", e)

        # Check cdc_acm kernel module availability
        try:
            cdc_ok, cdc_msg = linux_sp_flash.check_cdc_acm()
            logger.info("cdc_acm kernel module: %s", cdc_msg)
        except Exception as e:
            logger.debug("Could not check cdc_acm: %s", e)

        logger.info("Linux SP Flash Tool preparation complete")

    except ImportError as e:
        logger.debug("Linux SP Flash Tool preparation skipped: %s", e)
    except Exception as e:
        logger.warning("Linux SP Flash Tool preparation failed: %s", e)


def main():
    _configure_logging()

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
        extract_dir = sys.argv[2]
        scatter_file = sys.argv[3]
        scatter_platform = sys.argv[4] if len(sys.argv) >= 5 else ""
        package_path = sys.argv[5] if len(sys.argv) >= 6 else ""
        model = sys.argv[6] if len(sys.argv) >= 7 else ""
        # Optional trailing facts, forwarded by the app (and by the terminal
        # install script): a user-chosen Download Agent and auth file.
        da_file = sys.argv[7] if len(sys.argv) >= 8 else ""
        auth_file = sys.argv[8] if len(sys.argv) >= 9 else ""

        worker = FlashWorker(
            package_path=package_path or "dummy.zip",
            method="mtk",
            pre_extracted_dir=extract_dir,
            model=model,
            da_file=da_file,
            auth_file=auth_file,
        )
        worker.progress.connect(lambda x: print(f"[PROGRESS] {x}", flush=True))
        worker.step_changed.connect(lambda x: print(f"[STEP] {x}", flush=True))
        worker.log_message.connect(lambda x: print(f"[LOG] {x}", flush=True))
        worker.action_changed.connect(lambda x: print(f"[ACTION] {x}", flush=True))
        worker.eta_changed.connect(lambda x: print(f"[ETA] {x}", flush=True))

        from . import process_guard

        # The headless installer owns the same window as the guided one: no
        # tool may be left searching for a device after it exits.
        process_guard.sweep_stray_tools(reason="before headless install")
        process_guard.begin_install_session(reason="headless install")

        def on_finished(ok, msg):
            print(f"[RESULT] {int(ok)} {msg}", flush=True)
            process_guard.end_install_session(reason="headless install finished")
            sys.exit(0 if ok else 1)

        worker.finished.connect(on_finished)

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
        except BaseException as e:
            print(f"[RESULT] 0 {e}", flush=True)
            sys.exit(1)
        return

    import sys as _sys
    if _sys.platform.startswith("linux"):
        import os as _os
        from PySide6.QtCore import QCoreApplication
        for _plugin_dir in (
            "/usr/lib/qt6/plugins",
            "/usr/lib64/qt6/plugins",
            "/usr/lib/x86_64-linux-gnu/qt6/plugins",
            "/usr/lib/aarch64-linux-gnu/qt6/plugins",
            "/usr/local/lib/qt6/plugins",
        ):
            if _os.path.isdir(_plugin_dir):
                QCoreApplication.addLibraryPath(_plugin_dir)

    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs, False)
    # Names the app to the desktop (menu entry matching, WM_CLASS, macOS Dock):
    # the packaged front end tells the environment which one it is. Read before
    # the language is restored, so the name is the stable English one.
    app.setApplicationName(config.get_app_name())
    app.setOrganizationName("innioasis")

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

    from .i18n import translator
    from .ui.dark import ThemeWatcher, apply_theme, is_dark
    from .ui.glass import (
        apply_glass,
        apply_windows_dark_titlebar,
        configure_traffic_lights,
        prepare_window_for_glass,
    )
    from .ui.main_window import MainWindow

    # Follow the host appearance. Older macOS uses vibrant light or vibrant
    # dark to match; Tahoe and Golden Gate stay on the Liquid Glass path.
    _startup_step("apply_theme", apply_theme, app, force_dark=None)

    # Default language: the reader's saved choice, else the system's own
    # language when the app ships a catalog for it, else English.
    from .i18n import initial_language

    try:
        stored = str(QSettings("innioasis", "updater").value("language", "") or "")
    except Exception:
        stored = ""
    translator().set_language(initial_language(stored))
    # The interface mirrors for a right-to-left language (sidebar on the right,
    # controls aligned right), before the first widget is built.
    from .ui.main_window import apply_interface_direction

    apply_interface_direction(app)

    # PREPARE LINUX SYSTEM FOR SP FLASH TOOL before showing the window
    if _sys.platform.startswith("linux"):
        _prepare_linux_sp_flash_tool()

    window = MainWindow()
    # Before show(): the window must exist to be prepared for glass. After it:
    # every other piece of host chrome, each one guarded.
    _startup_step("prepare_window_for_glass", prepare_window_for_glass, window)
    window.show()
    _apply_native_chrome(window, dark=is_dark())

    # A previous run that crashed (or a tool the user started by hand) may still
    # be holding the USB port and SP Flash Tool's own settings files. Nothing is
    # installing at this point, so clear it once, off the GUI thread.
    from PySide6.QtCore import QTimer
    from . import process_guard

    def _sweep_on_startup():
        # The report travels back on the window's own signal: the sweep runs on
        # a worker thread and the log is a widget.
        sweep = process_guard.sweep_async(reason="startup", on_done=window.sweep_report.emit)
        _reset_usb_after_startup(sweep)

    QTimer.singleShot(1500, _sweep_on_startup)

    # Match host appearance live: light/dark switches, desktop accent colours,
    # system font changes — the same way native apps follow the OS. The window
    # normally brings the watcher with it (and registers it on the app); one is
    # installed here only when it did not, so the host is never watched twice.
    theme_watcher = getattr(app, "_theme_watcher", None)
    if theme_watcher is None:
        def _on_theme_applied():
            apply_windows_dark_titlebar(window, is_dark())
            apply_glass(window)
            configure_traffic_lights(window)

        theme_watcher = ThemeWatcher(app, on_apply=_on_theme_applied)
        theme_watcher.install()
        # Keep a reference for the lifetime of the app; the watcher owns timers
        # and an event filter on the QApplication itself.
        app._theme_watcher = theme_watcher

    exit_code = app.exec()
    _leave_cleanly(window, exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()