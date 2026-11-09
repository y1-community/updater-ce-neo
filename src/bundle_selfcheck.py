"""In-process checks that a frozen Linux bundle can start by itself.

A PyInstaller onedir build is only "self-contained" if every shared object the
interpreter, Qt, PySide6 and shiboken need is actually inside ``_internal``.
PyInstaller silently leaves a few of them to the host: ``shiboken6`` when it is
only named as a hidden import, and the C++ runtime Qt is linked against. The
result is an AppImage that works on the build machine and fails on Debian,
Fedora or SteamOS with ``ModuleNotFoundError: shiboken6`` or
``GLIBCXX_... not found``.

This module is the one description of "complete enough to start", used by
``launcher.py --self-check`` (and therefore by the AppRun wrapper) and by
``scripts/verify_linux_bundle.py`` in CI. It must not import Qt at module level:
when Qt is the missing piece the report has to be printed anyway.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

#: Libraries the bundle must carry itself. Names are matched with globs because
#: distributions and PyInstaller versions differ in the exact soname.
_REQUIRED_GLOBS = {
    "Qt 6 core": ("_internal/libQt6Core.so.6", "libQt6Core.so.6"),
    "Qt xcb platform plugin": (
        "_internal/PySide6/Qt/plugins/platforms/libqxcb.so",
        "PySide6/Qt/plugins/platforms/libqxcb.so",
    ),
    "shiboken6": (
        "_internal/shiboken6/Shiboken.abi3.so",
        "_internal/shiboken6/libshiboken6*.so*",
        "_internal/libshiboken6*.so*",
    ),
    "libusb": ("_internal/libusb-1.0.so.0", "libusb-1.0.so.0"),
    "C++ runtime": ("_internal/libstdc++.so.6", "libstdc++.so.6"),
    "GCC runtime": ("_internal/libgcc_s.so.1", "libgcc_s.so.1"),
}


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str = ""


def bundle_root() -> Path:
    """The folder holding the executable: the onedir root, or the checkout."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_in(root: Path, patterns) -> Path | None:
    for pattern in patterns:
        if any(ch in pattern for ch in "*?["):
            for match in sorted(root.glob(pattern)):
                if match.exists():
                    return match
        else:
            candidate = root / pattern
            if candidate.exists():
                return candidate
    return None


def payload_checks(root: Path | None = None) -> list[Check]:
    """Every file-level requirement, in the order the app needs them."""
    root = Path(root) if root is not None else bundle_root()
    checks = [
        Check(
            "application executable",
            (root / "InnioasisUpdater").exists()
            or (root / "MediaTekInstaller").exists(),
            str(root),
        )
    ]
    for name, patterns in _REQUIRED_GLOBS.items():
        found = _find_in(root, patterns)
        checks.append(
            Check(
                name,
                found is not None,
                str(found) if found is not None else f"missing ({patterns[0]})",
            )
        )
    return checks


def import_checks() -> list[Check]:
    """The modules that made frozen Linux builds fail at import time."""
    checks: list[Check] = []
    try:
        import shiboken6  # noqa: F401

        checks.append(Check("import shiboken6", True))
    except Exception as exc:  # noqa: BLE001 - reported, never raised
        checks.append(Check("import shiboken6", False, f"{type(exc).__name__}: {exc}"))
    try:
        from PySide6 import QtCore

        version = getattr(QtCore, "__version__", "") or "unknown"
        checks.append(Check("import PySide6.QtCore", True, version))
    except Exception as exc:  # noqa: BLE001
        checks.append(Check("import PySide6.QtCore", False, f"{type(exc).__name__}: {exc}"))
    return checks


def qt_start_check() -> Check:
    """Build an offscreen QGuiApplication: proves the plugin and bindings load."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtGui import QGuiApplication

        app = QGuiApplication.instance() or QGuiApplication([])
        platform = QGuiApplication.platformName()
        del app
        return Check("Qt starts headless", True, f"platform={platform}")
    except Exception as exc:  # noqa: BLE001
        return Check("Qt starts headless", False, f"{type(exc).__name__}: {exc}")


def run_checks(*, start_qt: bool = False, root: Path | None = None) -> list[Check]:
    checks = payload_checks(root)
    checks.extend(import_checks())
    if start_qt:
        checks.append(qt_start_check())
    return checks


def format_report(checks) -> str:
    lines = []
    for check in checks:
        lines.append(f"[{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    failures = [c for c in checks if not c.ok]
    if failures:
        lines.append("")
        lines.append(
            f"{len(failures)} check(s) failed. Repair with `./build_appimage.sh --clean` "
            "and republish; never ship a bundle that failed this report."
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    """``--self-check`` entry point: print the report, return an exit status."""
    checks = run_checks(start_qt="--no-qt" not in (argv or sys.argv[1:]))
    print(format_report(checks))
    return 0 if all(check.ok for check in checks) else 1
