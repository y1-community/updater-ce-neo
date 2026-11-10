#!/usr/bin/env python3
"""Verify that a frozen Linux bundle can start on the distros we promise.

Two failures took down the 2.5.1-2.5.4 Linux releases:

* The bundle was frozen on a host with glibc 2.44 (Arch/Fedora 43) and Python
  3.14, so every Debian/Ubuntu/Mint/openSUSE user hit
  ``GLIBC_2.44 not found`` before the window existed. glibc only works
  forwards, so the floor has to be checked at build time, not discovered by
  users.
* Qt 6.5+ loads ``libqxcb.so`` only when ``libxcb-cursor.so.0`` is present. A
  bundle without it dies with "could not load the Qt platform plugin xcb".

Run this against the PyInstaller onedir output. It exits non-zero, with a
message naming the offending file and the fix, when either check fails.

    python3 scripts/verify_linux_runtime.py --dist dist/linux/InnioasisUpdater
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX_GLIBC = (2, 35)  # Ubuntu 22.04: Debian 12+, Mint 21+, Fedora 36+, SteamOS 3
GLIBC_RE = re.compile(r"GLIBC_(\d+)\.(\d+)")
PYTHON_DIR_RE = re.compile(r"(?:^|/)lib?python(\d)\.(\d+)(?:\.so|$)|(?:^|/)python(\d)\.(\d+)$")

# Libraries Qt needs before it can create a window. They ship inside the
# bundle, so a missing one is a packaging bug rather than a user problem.
XCB_REQUIRED = (
    "libxcb-cursor.so.0",
    "libxcb-image.so.0",
    "libxcb-render-util.so.0",
    "libxcb-util.so.1",
    "libxkbcommon-x11.so.0",
    "libxkbcommon.so.0",
)

# Instructions that only exist on x86-64-v2 (SSE4.2/POPCNT) or v3 (AVX/AVX2).
# A Core 2 Duo (SSSE3) or an Athlon 64 (SSE2) aborts on the first POPCNT, so a
# bundle that contains one cannot claim "any x86-64 Linux".
X86_V2_INSN = re.compile(
    r"\s(popcnt|pcmpistri|pcmpistrm|pcmpestri|pcmpestrm|crc32[bwlq]?)\s", re.I
)
X86_V3_INSN = re.compile(r"\s(vzeroupper|vzeroall|vmovdqu|vmovdqa|vfmadd\w*|vpermd)\s", re.I)


def is_x86_64(path: Path) -> bool:
    try:
        out = subprocess.run(
            ["readelf", "-h", str(path)], capture_output=True, text=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "X86-64" in out


def cpu_requirement(path: Path) -> str:
    """'v1', 'v2' or 'v3' from the instructions actually present in the file."""
    try:
        disassembly = subprocess.run(
            ["objdump", "-d", "--no-show-raw-insn", str(path)],
            capture_output=True,
            text=True,
            timeout=600,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if X86_V3_INSN.search(disassembly):
        return "v3"
    if X86_V2_INSN.search(disassembly):
        return "v2"
    return "v1"


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink() and is_elf(path):
            yield path


def required_glibc(path: Path) -> tuple[int, int] | None:
    """Highest GLIBC_x.y symbol version this file requires."""
    for command in (["objdump", "-T"], ["readelf", "--dyn-syms", "--wide"]):
        try:
            out = subprocess.run(
                command + [str(path)], capture_output=True, text=True, timeout=120
            ).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        versions = [(int(a), int(b)) for a, b in GLIBC_RE.findall(out)]
        if versions:
            return max(versions)
    return None


def bundled_python(root: Path) -> tuple[int, int] | None:
    for path in root.rglob("*"):
        match = PYTHON_DIR_RE.search(path.as_posix())
        if not match:
            continue
        groups = [g for g in match.groups() if g]
        if len(groups) >= 2:
            try:
                return int(groups[0]), int(groups[1])
            except ValueError:
                continue
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", required=True, help="PyInstaller onedir folder")
    parser.add_argument(
        "--max-glibc",
        default=".".join(str(part) for part in DEFAULT_MAX_GLIBC),
        help="highest GLIBC symbol version the bundle may require (default 2.35)",
    )
    parser.add_argument("--max-python", default="3.12", help="highest Python minor allowed")
    parser.add_argument(
        "--allow-cpu-v2",
        action="store_true",
        help="accept a bundle that needs SSE4.2/POPCNT (breaks Core 2 Duo/Athlon 64)",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    root = Path(args.dist)
    if not (root / "_internal").is_dir() and not (root / "base_library.zip").is_file():
        print(f"verify_linux_runtime: {root} does not look like a PyInstaller onedir bundle")
        return 2

    ceiling = tuple(int(part) for part in args.max_glibc.split("."))
    problems: list[str] = []
    worst: tuple[int, int] | None = None
    worst_file: Path | None = None

    for path in elf_files(root):
        needed = required_glibc(path)
        if needed and (worst is None or needed > worst):
            worst, worst_file = needed, path
        if needed and needed > ceiling:
            problems.append(
                f"{path.relative_to(root)} requires GLIBC_{needed[0]}.{needed[1]}"
                f" (ceiling {ceiling[0]}.{ceiling[1]})"
            )

    if worst and worst > ceiling:
        problems.append(
            "The bundle was frozen on a host newer than the oldest distro it must run on. "
            "Rebuild inside Dockerfile.linux_build (Debian/Ubuntu 22.04 base) instead of the host."
        )

    python_minor = bundled_python(root)
    if python_minor:
        limit = tuple(int(part) for part in args.max_python.split("."))
        if python_minor > limit:
            problems.append(
                f"the bundle contains Python {python_minor[0]}.{python_minor[1]} "
                f"(maximum {args.max_python}); no distribution with glibc <= {args.max_glibc} "
                "ships that interpreter, and its wheels are linked against a newer libc"
            )

    # Qt is the part that decides the CPU floor: PySide6 6.10+ ships Qt built
    # for x86-64-v2, while the app's own code and CPython stay on the baseline.
    cpu_notes: list[str] = []
    if any(is_x86_64(path) for path in (root / "_internal").glob("libQt6Core.so*")):
        for candidate in sorted((root / "_internal").glob("libQt6Core.so*")) + sorted(
            (root / "_internal").glob("libpython3*.so*")
        ):
            if not is_x86_64(candidate):
                continue
            level = cpu_requirement(candidate)
            if level in ("v2", "v3"):
                if not args.allow_cpu_v2:
                    problems.append(
                        f"{candidate.name} uses x86-64-{level} instructions (SSE4.2/POPCNT or AVX); "
                        "Core 2 Duo, Athlon 64 and other pre-2008 x86-64 CPUs abort at startup. "
                        "Pin PySide6<6.10 (requirements.txt) and rebuild in the container."
                    )
                cpu_notes.append(f"{candidate.name}: {level} (allowed by --allow-cpu-v2)")
            elif level == "v1":
                cpu_notes.append(f"{candidate.name}: v1 (baseline)")

    internal = root / "_internal"
    for name in XCB_REQUIRED:
        if not (internal / name).exists():
            problems.append(
                f"_internal/{name} is missing; Qt 6.5+ cannot load the xcb platform plugin without it"
            )

    if not args.quiet:
        if worst and worst_file:
            print(
                f"  glibc floor: {worst[0]}.{worst[1]} (from {worst_file.relative_to(root)})"
            )
        if python_minor:
            print(f"  bundled Python: {python_minor[0]}.{python_minor[1]}")
        if cpu_notes:
            print(f"  CPU baseline: {', '.join(cpu_notes)}")
        print(f"  bundled Qt xcb libraries: {len(XCB_REQUIRED)} checked")

    if problems:
        print("", file=sys.stderr)
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1

    if not args.quiet:
        print("  Linux runtime floor OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
