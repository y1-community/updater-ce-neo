#!/usr/bin/env python3
"""Audit Mach-O binaries for Universal2 slices and macOS deployment target.

Usage:
    python3 scripts/audit_universal2.py <dir> [--max-minos 13.0]

Exits non-zero if any Mach-O is missing an x86_64 or arm64 slice, or declares
a minimum macOS version newer than --max-minos (default 13.0 = Ventura).
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

MAGICS = {b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe"}


def is_macho(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            return fh.read(4) in MAGICS
    except OSError:
        return False


def minos_for(p: Path, arch: str) -> str | None:
    out = subprocess.run(["otool", "-arch", arch, "-l", str(p)], capture_output=True, text=True).stdout
    m = re.search(r"cmd LC_BUILD_VERSION.*?minos ([0-9.]+)", out, re.S)
    if m:
        return m.group(1)
    m = re.search(r"cmd LC_VERSION_MIN_MACOSX.*?version ([0-9.]+)", out, re.S)
    return m.group(1) if m else None


def vtuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--max-minos", default="13.0")
    args = ap.parse_args()
    limit = vtuple(args.max_minos)

    bad, total, highest = [], 0, (0,)
    for p in sorted(Path(args.root).rglob("*")):
        if p.is_symlink() or not p.is_file() or not is_macho(p):
            continue
        total += 1
        archs = subprocess.run(["lipo", "-archs", str(p)], capture_output=True, text=True).stdout.split()
        missing = {"x86_64", "arm64"} - set(archs)
        if missing:
            bad.append(f"MISSING {sorted(missing)}: {p}")
            continue
        for arch in ("x86_64", "arm64"):
            mo = minos_for(p, arch)
            if mo:
                highest = max(highest, vtuple(mo))
                if vtuple(mo) > limit:
                    bad.append(f"MINOS {mo} ({arch}) > {args.max_minos}: {p}")

    print(f"Scanned {total} Mach-O files; highest minos = {'.'.join(map(str, highest))}")
    for line in bad:
        print(line)
    print("RESULT:", "FAIL" if bad else "OK (all universal2, Ventura-compatible)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
