"""Console flashing and adb wrappers.

The GUI drives mtkclient through an in-process library and SP Flash Tool
through a Qt window. Neither exists on a headless Pi or a 32-bit machine, so the
terminal front ends shell out to the tools' own command lines and stream their
output. That also means the same code drives a phone attached to a Raspberry Pi
as drives it from an SSH session on a server.

Nothing here pretends a tool is present: every entry point reports exactly what
is missing and the command that installs it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Where the lite payload may unpack the console tools. The full AppImage keeps
# them beside the executable; a lite install keeps them in its own folder.
TOOL_DIRS = (
    Path(os.environ.get("INNIOASIS_TOOL_DIR", "")) if os.environ.get("INNIOASIS_TOOL_DIR") else None,
    Path.home() / ".local" / "share" / "innioasis-updater" / "tools",
)


def _candidate_mtkclient() -> list[list[str]]:
    candidates: list[list[str]] = []
    env = os.environ.get("MTKCLIENT")
    if env:
        candidates.append([env])
    for name in ("mtkclient", "mtk", "mtk.py"):
        found = shutil.which(name)
        if found:
            candidates.append([found])
    for base in TOOL_DIRS:
        if not base:
            continue
        for name in ("mtk.py", "mtkclient"):
            path = base / name
            if path.exists():
                candidates.append([sys.executable, str(path)])
        for name in ("mtkclient", "mtk"):
            path = base / name
            if path.exists():
                candidates.append([str(path)])
    # A source checkout: the vendored fork's own entry point.
    repo = Path(__file__).resolve().parents[2]
    vendored = repo / "vendor" / "mtkclient" / "mtk.py"
    if vendored.exists():
        candidates.append([sys.executable, str(vendored)])
    return candidates


def mtkclient_command() -> list[str] | None:
    for candidate in _candidate_mtkclient():
        if candidate and (candidate[0] == sys.executable or Path(candidate[0]).exists()):
            return candidate
    return None


def adb_command() -> str | None:
    for base in TOOL_DIRS:
        if not base:
            continue
        for name in ("adb", "platform-tools/adb"):
            path = base / name
            if path.exists():
                return str(path)
    return shutil.which("adb")


def rar_command() -> str | None:
    for name in ("unrar", "unar", "7z", "7za"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _run(command: list[str], dry_run: bool = False) -> int:
    printable = " ".join(command)
    print(f"$ {printable}", flush=True)
    if dry_run:
        return 0
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        print(f"could not start {command[0]}: {exc}", file=sys.stderr)
        return 127
    assert process.stdout is not None
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    return process.wait()


def flash_directory(
    folder: Path,
    preloader: Path | None = None,
    da: Path | None = None,
    auth: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Write every partition in ``folder`` with mtkclient.

    ``mtk wl <folder>`` is mtkclient's own "write all partitions from this
    directory" command, the console equivalent of the GUI's MTK flow.
    """
    base = mtkclient_command()
    if not base:
        print(
            "MTKClient is not installed. On the lite install:\n"
            "  pip install --user mtkclient\n"
            "or put mtk.py in ~/.local/share/innioasis-updater/tools/.",
            file=sys.stderr,
        )
        return 2
    command = list(base) + ["wl", str(folder)]
    if preloader:
        command += ["--preloader", str(preloader)]
    if da:
        command += ["--da", str(da)]
    if auth:
        command += ["--auth", str(auth)]
    return _run(command, dry_run=dry_run)


def reset_device(dry_run: bool = False) -> int:
    base = mtkclient_command()
    if not base:
        print("MTKClient is not installed; cannot send a reset.", file=sys.stderr)
        return 2
    return _run(list(base) + ["reset"], dry_run=dry_run)


def adb(args: Iterable[str], dry_run: bool = False) -> int:
    tool = adb_command()
    if not tool:
        # Fetch platform-tools only when an adb command is actually asked for.
        # An AppImage/tarball that already carries adb lands here with a path.
        try:
            from .components import ensure as ensure_component

            tool = str(ensure_component("adb"))
        except (RuntimeError, OSError) as exc:
            print(
                "adb is not installed and could not be fetched.\n"
                f"{exc}\n"
                "Install it with your package manager (android-tools on Arch, "
                "adb on Debian/Ubuntu) or place it in "
                "~/.local/share/innioasis-updater/tools/.",
                file=sys.stderr,
            )
            return 2
    return _run([tool] + [str(arg) for arg in args], dry_run=dry_run)


def adb_devices() -> list[str]:
    tool = adb_command()
    if not tool:
        return []
    try:
        output = subprocess.run([tool, "devices"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    devices = []
    for line in output.splitlines()[1:]:
        line = line.strip()
        if line and "\t" in line:
            devices.append(line.split("\t", 1)[0])
    return devices


def mount_point(path: Path) -> str:
    """Where a player's Rockbox partition appears, for the TUI's status line."""
    for base in (Path("/media"), Path("/run/media"), Path("/mnt"), Path("/Volumes")):
        if not base.exists():
            continue
        for child in base.glob("*"):
            if child.is_dir() and (child.name.upper().startswith("Y1") or child.name.upper().startswith("Y2")):
                return str(child)
    return ""


def doctor() -> dict:
    """Everything a support reply needs, in one plain dictionary."""
    mtk = mtkclient_command()
    try:
        from .components import status as component_status

        extra = {row["key"]: row["path"] for row in component_status() if row["available"]}
    except Exception:  # the report must never fail because a probe did
        extra = {}
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "tty": bool(sys.stdin.isatty()),
        "mtkclient": " ".join(mtk) if mtk else "",
        "adb": adb_command() or "",
        "unrar": rar_command() or "",
        "mount": mount_point(Path("/")),
        "components": extra,
        "env": {key: os.environ[key] for key in ("XDG_DATA_HOME", "APPIMAGE", "INNIOASIS_LITE") if os.environ.get(key)},
    }
