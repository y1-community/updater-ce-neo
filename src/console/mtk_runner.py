"""Run vendored MTKclient with the same libusb backend as the window.

``mtk.py`` imports pyusb itself and, on macOS, pyusb's default search does not
find the fork's bundled ``libusb-1.0.dylib``. The window patches that lookup in
``src.mtk_api``; a console flash runs in a child process, so this runner applies
the same patch before executing the vendored entry point. Keeping the runner in
the console payload means a source checkout and a lite install both get it.
"""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _prepare_library_paths(root: Path) -> None:
    """Help pyusb find the bundled library even if mtk_api cannot be imported."""
    vendor = root / "vendor" / "mtkclient"
    candidates = [
        vendor / "mtkclient" / "Darwin",
        vendor / "mtkclient" / "Windows",
        vendor / "mtkclient" / "Linux",
    ]
    for folder in candidates:
        if not folder.is_dir():
            continue
        if sys.platform.startswith("darwin"):
            current = os.environ.get("DYLD_LIBRARY_PATH", "")
            os.environ["DYLD_LIBRARY_PATH"] = str(folder) + ((":" + current) if current else "")
        elif os.name == "nt":
            current = os.environ.get("PATH", "")
            os.environ["PATH"] = str(folder) + os.pathsep + current
        else:
            current = os.environ.get("LD_LIBRARY_PATH", "")
            os.environ["LD_LIBRARY_PATH"] = str(folder) + ((":" + current) if current else "")
        return


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    vendor = root / "vendor" / "mtkclient"
    if not (vendor / "mtk.py").is_file():
        raise SystemExit("MTKclient is missing from this install: " + str(vendor))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    _prepare_library_paths(root)
    try:
        from src import mtk_api

        mtk_api.install_pyusb_default_backend()
    except Exception as exc:  # keep the runner usable in a minimal payload
        print(f"MTKclient libusb setup warning: {exc}", file=sys.stderr)
    sys.argv = [str(vendor / "mtk.py"), *sys.argv[1:]]
    runpy.run_path(str(vendor / "mtk.py"), run_name="__main__")


if __name__ == "__main__":
    main()
