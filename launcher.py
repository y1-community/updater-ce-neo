#!/usr/bin/env python3
"""PyInstaller entry point.

``src/app.py`` is a package module (it uses relative imports), so we launch it
through this tiny script that puts ``src`` on ``sys.path`` and calls
``src.app.main()``. Kept outside ``src/`` so PyInstaller freezes it cleanly.
"""

import os
import sys
from pathlib import Path

# Never write __pycache__ beside the shipped bytecode: the app bundle is
# code-signed, and a writable .app would otherwise change a sealed resource on
# its first run. The launcher sets this for the packaged app; this covers a
# source checkout started straight from launcher.py too.
sys.dont_write_bytecode = True

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Simulated macOS mode: put the Linux/Windows client into the exact code path
# a real macOS build uses (MTKClient as the only flash backend, no SP Flash
# Tool GUI), so mtkclient functionality can be tested for macOS/Linux parity
# without macOS hardware. Must be applied before any src.* import.
if "--simulate-macos" in sys.argv:
    os.environ["INNIOASIS_SIMULATE_MACOS"] = "1"
    sys.argv.remove("--simulate-macos")

# The simulated-macOS flow is deliberately session-scoped. Choosing
# "MTKClient (Mac)" restarts the app with INNIOASIS_SIMULATE_MACOS=1 in the
# child's environment, so the flow applies for that run. It is NOT re-applied
# here from the saved install method: doing that made a value left behind by
# the M/D debug keys permanent, which turned SP Flash Tool into an unreachable
# backend on Linux and pinned every later launch to MTKClient.

# Pre-flight for a packaged bundle, run by the AppRun wrapper before it hands
# over (see assets/linux-run.sh and scripts/verify_linux_bundle.py). The report
# is printed and the exit status is the verdict; the app itself is not started,
# so a bundle missing Qt or shiboken6 explains itself instead of dying in a
# terminal the user never sees.
# The text session never imports Qt. Opening the AppImage, the tarball or the
# zip with no flag still starts the window. --tui is the explicit text session,
# and run_linux_lite.sh is the streamed way to reach the same entry.
if "--tui" in sys.argv:
    sys.argv = [arg for arg in sys.argv if arg != "--tui"]
    from src.tui_session import main as _tui_main

    sys.exit(_tui_main(sys.argv))


# The console commands (catalogue, resumable download, MTKClient/adb) live in
# src.console and import no Qt, so they work in the same AppImage on a machine
# whose display stack is broken, and in the lite payload with no PySide6 at all.
if "--cli" in sys.argv or "--lite" in sys.argv:
    from src.console.cli import main as _console_main

    _argv = [arg for arg in sys.argv[1:] if arg not in ("--cli", "--lite")]
    sys.exit(_console_main(_argv or ["tui"]))

if "--self-check" in sys.argv:
    from src.bundle_selfcheck import main as _self_check

    sys.exit(_self_check(sys.argv))

if "--check-environment" in sys.argv or "--diagnostics" in sys.argv:
    from src import paths, flash_service, mtk_api
    print("=== Innioasis Updater Environment Diagnostics ===")
    print(f"Platform: {sys.platform}")
    if paths.SIMULATE_MACOS:
        print("Simulated macOS: YES (--simulate-macos; flash backend = MTKClient only)")
    print(f"IS_MAC (effective): {paths.IS_MAC}")
    print(f"Frozen: {getattr(sys, 'frozen', False)}")
    print(f"Bundle dir: {paths.BUNDLE_DIR}")
    print(f"MTKClient dir: {paths.MTKCLIENT_DIR}")

    paths.ensure_mtkclient_importable()
    try:
        import mtkclient
        print(f"mtkclient: OK ({mtkclient.__file__})")
    except Exception as e:
        print(f"mtkclient: FAILED ({e})")
        sys.exit(1)

    dylib = paths.find_libusb_dylib()
    print(f"libusb-1.0.dylib: {dylib or 'NOT FOUND'}")

    usb_core, backend = flash_service._load_libusb_backend()
    if backend:
        print(f"libusb backend: OK ({backend})")
        try:
            devs = list(usb_core.find(find_all=True, backend=backend))
            print(f"USB devices found: {len(devs)}")
        except Exception as e:
            print(f"USB find warning: {e}")
    else:
        print("libusb backend: FAILED")
        sys.exit(2)

    try:
        mtk = mtk_api.init(None, None)
        print(f"mtk_api.init: OK ({mtk})")
    except Exception as e:
        print(f"mtk_api.init: FAILED ({e})")
        sys.exit(3)

    print("=== All checks passed successfully ===")
    sys.exit(0)

# macOS LaunchServices passes -psn_0_... when launching an .app bundle from Finder
sys.argv = [arg for arg in sys.argv if not arg.startswith("-psn")]

if __name__ == "__main__":
    try:
        # Before anything else spawns a helper: a window that flashes open
        # during launch makes the app look broken, and imports below can start
        # processes.
        from src import quiet_subprocess

        quiet_subprocess.install()
        from src.app import main
        main()
    except ModuleNotFoundError as exc:
        # A graphical bundle without Qt is still useful as a terminal build:
        # do not dump a traceback, hand over to the console front end instead.
        if (exc.name or "").split(".")[0] not in ("PySide6", "shiboken6"):
            raise
        sys.stderr.write(
            "Updater CE: PySide6 is not available in this build; starting the terminal interface.\n"
        )
        from src.console.cli import main as _console_main

        sys.exit(_console_main(["tui"] if sys.stdin.isatty() else ["--help"]))
    except Exception as exc:
        import traceback
        err_msg = traceback.format_exc()
        sys.stderr.write(f"Innioasis Updater CE Fatal Error:\n{err_msg}\n")
        try:
            if sys.platform == "darwin":
                log_dir = Path.home() / "Library" / "Logs" / "InnioasisUpdater"
            else:
                log_dir = Path.home() / ".innioasis"
            log_dir.mkdir(parents=True, exist_ok=True)
            (log_dir / "crash.log").write_text(err_msg, encoding="utf-8")
        except Exception:
            pass

        if sys.platform == "darwin":
            # The alert belongs to this process. Routing it through a script
            # host would put Script Editor in front of the user instead: an app
            # they never opened, asking them to trust the message. Without
            # AppKit the crash log written above is the report.
            try:
                from AppKit import NSAlert, NSApplication

                NSApplication.sharedApplication()
                alert = NSAlert.alloc().init()
                alert.setMessageText_("Innioasis Updater Error")
                alert.setInformativeText_(
                    str(exc) or "The application stopped unexpectedly."
                )
                alert.runModal()
            except Exception:
                pass
        sys.exit(1)
