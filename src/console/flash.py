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

import importlib.util
import os
import platform
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

# Where the lite payload may unpack the console tools. The full AppImage keeps
# them beside the executable; a lite install keeps them in its own folder.
TOOL_DIRS = (
    Path(os.environ.get("INNIOASIS_TOOL_DIR", "")) if os.environ.get("INNIOASIS_TOOL_DIR") else None,
    Path.home() / ".local" / "share" / "innioasis-updater" / "tools",
)


CRYPTO_MODULES = ("Crypto", "Cryptodome")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def cryptodome_status() -> dict:
    """Whether the interpreter MTKclient runs under can import its crypto."""
    found = [name for name in CRYPTO_MODULES if _module_available(name)]
    return {"available": bool(found), "modules": found}


def ensure_cryptodome(verbose: bool = True) -> tuple[bool, str]:
    """Install pycryptodome for the current user when MTKclient needs it.

    The window and the AppImage already carry it. A lite session runs on a
    system Python and may not, which is the ``No module named 'Crypto'``
    failure. Installing into the user site keeps the system untouched and is
    visible to the same interpreter that runs ``mtk.py``.
    """
    if cryptodome_status()["available"]:
        return True, ""
    if os.environ.get("INNIOASIS_NO_AUTO_INSTALL") == "1":
        return False, "pycryptodome is missing and automatic installation is disabled (INNIOASIS_NO_AUTO_INSTALL=1)."
    if getattr(sys, "frozen", False):
        return False, (
            "This frozen build is missing pycryptodome, so MTKclient cannot start. "
            "Use the AppImage/tarball/zip, which bundle it, or install the distro package."
        )

    attempts = [
        [sys.executable, "-m", "pip", "install", "--user", "pycryptodome", "pycryptodomex"],
        [sys.executable, "-m", "pip", "install", "--user", "--break-system-packages", "pycryptodome", "pycryptodomex"],
    ]
    if verbose:
        print("MTKclient needs pycryptodome; installing it for this user…", flush=True)
    for command in attempts:
        try:
            process = subprocess.run(command, capture_output=True, text=True)
        except OSError:
            continue
        if process.returncode != 0:
            for line in (process.stderr or process.stdout or "").strip().splitlines()[-3:]:
                print(f"  {line}", file=sys.stderr)
            continue
        importlib.invalidate_caches()
        if site.USER_SITE and site.USER_SITE not in sys.path:
            sys.path.append(site.USER_SITE)
        if cryptodome_status()["available"]:
            return True, ""
    return False, (
        "pycryptodome could not be installed automatically.\n"
        "  Debian/Ubuntu/Mint: sudo apt install python3-pycryptodome\n"
        "  Fedora: sudo dnf install python3-pycryptodomex\n"
        "  Arch/Manjaro: sudo pacman -S python-pycryptodome\n"
        "  Any pip: python3 -m pip install --user pycryptodome pycryptodomex\n"
        "Falling back to SP Flash Tool where it is available."
    )


def _spflash_path() -> str:
    """A resolved SP Flash Tool, without downloading one just to report it."""
    try:
        from .components import COMPONENTS, resolve

        found = resolve(COMPONENTS["spflash"])
        return str(found) if found else ""
    except Exception:
        return ""


def _preserve_scatter(folder: Path) -> Path | None:
    """The package's own keep-data scatter, when it ships one."""
    for path in sorted(folder.rglob("*.txt")):
        name = path.name.lower()
        if any(tag in name for tag in ("preserve", "keep_data", "keepdata", "nodata")):
            return path
    return None


def _keep_data_copy(folder: Path) -> tuple[Path | None, str]:
    """A cheap copy of the firmware whose scatter preserves user data.

    Hard links keep the copy instantaneous and space-free on one filesystem.
    The original package is never modified; the caller removes the copy.
    """
    from .download import find_scatter

    preserve = _preserve_scatter(folder)
    scatter = find_scatter(folder)
    if preserve is None or scatter is None:
        return None, ""
    try:
        staging = Path(tempfile.mkdtemp(prefix=".innioasis-keep-", dir=str(folder.parent)))
        for item in folder.iterdir():
            target = staging / item.name
            if item.is_dir():
                shutil.copytree(item, target, copy_function=os.link)
            elif item.is_file():
                os.link(item, target)
        target_scatter = staging / scatter.relative_to(folder)
        target_scatter.unlink(missing_ok=True)
        shutil.copy2(preserve, target_scatter)
        return staging, str(preserve)
    except (OSError, ValueError) as exc:
        shutil.rmtree(staging, ignore_errors=True) if "staging" in locals() else None
        print(f"Could not prepare Keep my data: {exc}", file=sys.stderr)
        return None, ""


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


def mtk_write_command(
    base: list[str],
    folder: Path,
    preloader: Path | None = None,
    da: Path | None = None,
    auth: Path | None = None,
) -> list[str]:
    """``mtk wl <folder>`` plus the same optional files the GUI forwards.

    mtkclient names the Download Agent ``--loader`` and the authentication
    file ``--auth``. ``--preloader`` is the preloader image. The scatter file
    stays in the folder: ``wl`` writes the images that sit beside it.
    """
    command = list(base) + ["wl", str(folder)]
    if preloader:
        command += ["--preloader", str(preloader)]
    if da:
        command += ["--loader", str(da)]
    if auth:
        command += ["--auth", str(auth)]
    return command


def sp_flash_tool_console_args(scatter_arg, da_arg, auth_file="", format_all=False):
    """Console arguments for flash_tool, duplicated from the window.

    A no-auth download is ``-c download -s <scatter> -d <DA> -t without -r``.
    ``format_all`` is the one opt-in change, ``-c format-download``. An
    authentication file is applied by the window through a generated config;
    the text session does not import that Qt-adjacent writer and says so.
    """
    if str(auth_file or "").strip():
        raise RuntimeError(
            "An authentication file is applied by the window through SP Flash Tool's "
            "console config. The text session does not start the Qt window for that case; "
            "use MTKclient (mtk wl <folder> --auth <file>) instead."
        )
    return [
        "-c", "format-download" if format_all else "download",
        "-s", str(scatter_arg),
        "-d", str(da_arg),
        "-t", "without",
        "-r",
    ]


def install_engine(system: str, arch: str, headless: bool) -> tuple[str, str]:
    """``sp-console`` only on Linux x86_64 with a display. Otherwise MTKclient.

    Headless and every other architecture must not launch the Qt SP Flash Tool
    window. The reason is returned for the status line.
    """
    if system == "linux" and arch == "x86_64" and not headless:
        return "sp-console", ""
    if headless:
        reason = "There is no display, so the Qt SP Flash Tool window is not started."
    else:
        reason = (
            f"This is {system} {arch}, not the Linux x86_64 SP Flash Tool build, "
            "so the Qt SP Flash Tool window is not started."
        )
    return "mtk", reason + " Using MTKclient instead: mtk wl <folder>."


def discover_mtk_files(folder: Path) -> dict[str, Path | None]:
    """Preloader, Download Agent and auth file sitting beside a firmware folder."""
    preloader = da = auth = None
    folder = Path(folder)
    if not folder.is_dir():
        return {"preloader": None, "da": None, "auth": None}
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        name = path.name.lower()
        if preloader is None and "preloader" in name and path.suffix.lower() in {".bin", ".img"}:
            preloader = path
        if da is None and (name in {"mtk_allinone_da.bin", "download_agent.bin"} or name.endswith("_da.bin")):
            da = path
        if auth is None and path.suffix.lower() == ".auth":
            auth = path
    return {"preloader": preloader, "da": da, "auth": auth}


def _run(command: list[str], dry_run: bool = False) -> int:
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        print("simulation: not running " + " ".join(command), flush=True)
        return 0
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
    keep_data: bool = False,
) -> int:
    """Write every partition in ``folder`` with mtkclient.

    ``mtk wl <folder>`` is mtkclient's own "write all partitions from this
    directory" command, the console equivalent of the GUI's MTK flow. When
    ``keep_data`` is set, the package's preserve-data scatter is used from a
    hard-linked copy, so internal storage is not written and the original
    package is untouched. A package without that scatter stops instead of
    silently erasing user data. If MTKclient cannot start, SP Flash Tool's
    console mode is tried before giving up.
    """
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        from .simulate import install_progress

        for event in install_progress(sleep=0):
            print(event["message"], flush=True)
        return 0 if event["ok"] else 1

    staging: Path | None = None
    if keep_data:
        staging, preserve = _keep_data_copy(folder)
        if staging is None:
            print(
                "Keep my data needs a preserve-data scatter shipped with this firmware "
                "package; this one has none. Nothing was written. Use the graphical "
                "installer for this package or install without Keep my data.",
                file=sys.stderr,
            )
            return 2
        print(f"Keep my data: using the package's {Path(preserve).name}", flush=True)
        folder = staging

    try:
        base = mtkclient_command()
        if base:
            ready, detail = ensure_cryptodome()
            if ready:
                found = discover_mtk_files(folder)
                command = mtk_write_command(
                    base,
                    folder,
                    preloader=preloader or found["preloader"],
                    da=da or found["da"],
                    auth=auth or found["auth"],
                )
                return _run(command, dry_run=dry_run)
            print(detail, file=sys.stderr)
        else:
            print(
                "MTKClient is not installed. On the lite install:\n"
                "  pip install --user mtkclient\n"
                "or put mtk.py in ~/.local/share/innioasis-updater/tools/.",
                file=sys.stderr,
            )

        fallback = sp_flash_directory(folder, dry_run=dry_run)
        if fallback is not None:
            return fallback
        return 2
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def sp_flash_directory(folder: Path, dry_run: bool = False) -> int | None:
    """Fall back to SP Flash Tool's console download on Linux x86_64.

    ``None`` means the fallback is not available on this system, so the caller
    can report the original MTKclient problem rather than a misleading exit.
    """
    if platform.system().lower() != "linux" or platform.machine().lower() not in ("x86_64", "amd64"):
        return None
    from .download import find_scatter

    scatter = find_scatter(folder)
    if scatter is None:
        print("SP Flash Tool needs the package's scatter file; none was found.", file=sys.stderr)
        return None
    tool = _spflash_path()
    if not tool:
        try:
            from .components import ensure as ensure_component

            tool = str(ensure_component("spflash", verbose=False))
        except (RuntimeError, OSError) as exc:
            print(f"SP Flash Tool is not available either: {exc}", file=sys.stderr)
            return None
    found = discover_mtk_files(folder)
    da = found["da"] or Path(tool).parent / "MTK_AllInOne_DA.bin"
    if not da.is_file():
        for candidate in Path(tool).parent.rglob("*DA*.bin"):
            da = candidate
            break
    if not da.is_file():
        print("SP Flash Tool's Download Agent was not found beside the tool.", file=sys.stderr)
        return None
    print("MTKclient could not start; using SP Flash Tool console mode.", flush=True)
    return _run([tool, *sp_flash_tool_console_args(str(scatter), str(da))], dry_run=dry_run)


def reset_device(dry_run: bool = False) -> int:
    base = mtkclient_command()
    if not base:
        print("MTKClient is not installed; cannot send a reset.", file=sys.stderr)
        return 2
    return _run(list(base) + ["reset"], dry_run=dry_run)


def adb(args: Iterable[str], dry_run: bool = False) -> int:
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        print("simulation: not running adb " + " ".join(str(arg) for arg in args), flush=True)
        return 0
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
        "cryptodome": cryptodome_status()["available"],
        "spflash": _spflash_path(),
        "adb": adb_command() or "",
        "unrar": rar_command() or "",
        "mount": mount_point(Path("/")),
        "components": extra,
        "env": {key: os.environ[key] for key in ("XDG_DATA_HOME", "APPIMAGE", "INNIOASIS_LITE") if os.environ.get(key)},
    }
