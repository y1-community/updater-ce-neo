#!/usr/bin/env python3
"""Fail a Linux build whose bundle cannot start on a machine without Qt.

Run after PyInstaller:

    python3 scripts/verify_linux_bundle.py dist/linux/InnioasisUpdater
    python3 scripts/verify_linux_bundle.py dist/linux/InnioasisUpdater --run

``--run`` also starts the frozen executable with ``--self-check`` under
``QT_QPA_PLATFORM=offscreen`` and fails when it exits non-zero. That is the only
check that catches a bundle whose files are present but whose imports explode.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.bundle_selfcheck import format_report, payload_checks  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="PyInstaller onedir output folder")
    parser.add_argument(
        "--run",
        action="store_true",
        help="execute the frozen app's --self-check under QT_QPA_PLATFORM=offscreen",
    )
    args = parser.parse_args(argv)

    root = args.bundle.resolve()
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory", file=sys.stderr)
        return 2

    checks = payload_checks(root)
    report = format_report(checks)
    print(report)
    if not all(check.ok for check in checks):
        return 1
    if not args.run:
        return 0

    exe = next(
        (p for p in (root / "InnioasisUpdater", root / "MediaTekInstaller") if p.exists()),
        None,
    )
    if exe is None:
        print("ERROR: no executable to run", file=sys.stderr)
        return 2
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("LD_LIBRARY_PATH", None)
    try:
        proc = subprocess.run(
            [str(exe), "--self-check"],
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("ERROR: --self-check did not finish within 180s", file=sys.stderr)
        return 1
    output = (proc.stdout or "").strip()
    errors = (proc.stderr or "").strip()
    if output:
        print(output)
    if errors:
        print(errors, file=sys.stderr)
    if proc.returncode != 0:
        print(
            f"ERROR: the frozen bundle failed its self-check (exit {proc.returncode})",
            file=sys.stderr,
        )
        return 1
    print("Bundle self-check passed: the frozen app starts without host Qt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
