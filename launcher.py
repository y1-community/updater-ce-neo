#!/usr/bin/env python3
"""PyInstaller entry point.

``src/app.py`` is a package module (it uses relative imports), so we launch it
through this tiny script that puts ``src`` on ``sys.path`` and calls
``src.app.main()``. Kept outside ``src/`` so PyInstaller freezes it cleanly.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "--check-environment" in sys.argv or "--diagnostics" in sys.argv:
    from src import paths, flash_service, mtk_api
    print("=== Innioasis Updater Environment Diagnostics ===")
    print(f"Platform: {sys.platform}")
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

from src.app import main  # noqa: E402

if __name__ == "__main__":
    main()
