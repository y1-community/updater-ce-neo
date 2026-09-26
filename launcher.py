#!/usr/bin/env python3
"""PyInstaller entry point.

``src/app.py`` is a package module (it uses relative imports), so we launch it
through this tiny script that puts ``src`` on ``sys.path`` and calls
``src.app.main()``. Kept outside ``src/`` so PyInstaller freezes it cleanly.
"""

import os
import sys
from pathlib import Path

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
        from src.app import main
        main()
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
            try:
                import subprocess
                clean_msg = str(exc).replace('"', '\\"')
                subprocess.run([
                    "osascript", "-e",
                    f'display alert "Innioasis Updater Error" message "{clean_msg}"'
                ], timeout=5)
            except Exception:
                pass
        sys.exit(1)
