#!/usr/bin/env python3
"""Make an extracted python.org universal2 Python.framework relocatable.

The python.org macOS installer hard-codes absolute load commands such as
``/Library/Frameworks/Python.framework/Versions/3.13/Python``. This script
rewrites every such reference inside the framework to an ``@loader_path``
relative path and re-applies an ad-hoc code signature, so the interpreter can
run from any directory without ``sudo`` or a system-wide install.

Usage:
    python3 scripts/relocate_python_framework.py <path/to/Python.framework> <X.Y>
"""

import os
import subprocess
import sys
from pathlib import Path

MACHO_MAGICS = {
    b"\xca\xfe\xba\xbe",  # FAT
    b"\xcf\xfa\xed\xfe",  # MH_MAGIC_64 (LE)
    b"\xce\xfa\xed\xfe",  # MH_MAGIC (LE)
}


def is_macho(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            return fh.read(4) in MACHO_MAGICS
    except OSError:
        return False


def deps(path: Path) -> list[str]:
    out = subprocess.run(["otool", "-L", str(path)], capture_output=True, text=True).stdout
    result = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if line and not line.endswith(":"):
            result.append(line.split(" (compatibility")[0].strip())
    return result


def main() -> int:
    fw = Path(sys.argv[1]).resolve()
    ver = sys.argv[2]
    old_prefix = f"/Library/Frameworks/Python.framework/Versions/{ver}/"
    ver_root = fw / "Versions" / ver

    for path in sorted(ver_root.rglob("*")):
        if path.is_symlink() or not path.is_file() or not is_macho(path):
            continue
        changes = []
        for dep in set(deps(path)):
            if dep.startswith(old_prefix):
                target = ver_root / dep[len(old_prefix):]
                rel = os.path.relpath(target, path.parent)
                changes += ["-change", dep, f"@loader_path/{rel}"]
        # Give dylibs a relocatable install id.
        if path.suffix == ".dylib" or path == ver_root / "Python":
            out = subprocess.run(["otool", "-D", str(path)], capture_output=True, text=True).stdout
            if old_prefix in out:
                changes += ["-id", f"@rpath/{path.relative_to(ver_root)}"]
        if changes:
            subprocess.run(
                ["install_name_tool", *changes, str(path)],
                check=True,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(["codesign", "--force", "-s", "-", str(path)], check=True,
                           stderr=subprocess.DEVNULL)
            print(f"relocated: {path.relative_to(fw)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
