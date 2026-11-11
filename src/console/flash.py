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
import re
import shutil
import site
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

from . import wsl

# Where the lite payload may unpack the console tools. The full AppImage keeps
# them beside the executable; a lite install keeps them in its own folder.
TOOL_DIRS = (
    Path(os.environ.get("INNIOASIS_TOOL_DIR", "")) if os.environ.get("INNIOASIS_TOOL_DIR") else None,
    Path.home() / ".local" / "share" / "innioasis-updater" / "tools",
)


# Import name -> pip distribution. MTKclient loads these while it is still
# starting, before it talks to a device. Cryptodome is the one the vendored
# fork imports; Crypto is the name older copies use. One missing import aborts
# the whole flash, which is the "No module named colorama" failure.
MTK_PY_DEPS = (
    ("colorama", "colorama"),
    ("serial", "pyserial"),
    ("usb", "pyusb"),
    ("capstone", "capstone"),
    ("Cryptodome", "pycryptodomex"),
    ("Crypto", "pycryptodome"),
)
CRYPTO_MODULES = ("Crypto", "Cryptodome")
_MISSING_MODULE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def cryptodome_status() -> dict:
    """Whether the interpreter MTKclient runs under can import its crypto."""
    found = [name for name in CRYPTO_MODULES if _module_available(name)]
    return {"available": bool(found), "modules": found}


def package_for_import(name: str) -> str | None:
    """The pip distribution that provides an import MTKclient failed on."""
    root = (name or "").split(".", 1)[0]
    for module, package in MTK_PY_DEPS:
        if module == root:
            return package
    if root and root.isidentifier() and root not in {"mtkclient", "__main__"}:
        return root
    return None


def _python_for(command: list[str]) -> str:
    """The interpreter that will actually import mtkclient."""
    if not command:
        return sys.executable
    head = command[0]
    if Path(head).name.lower().startswith("python"):
        return head
    try:
        first = Path(head).read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except OSError:
        return sys.executable
    if first.startswith("#!") and "python" in first.lower():
        return first[2:].strip().split()[0] or sys.executable
    return sys.executable


def _missing_imports(python: str, names: list[str]) -> list[str]:
    if not names:
        return []
    if os.path.realpath(python) == os.path.realpath(sys.executable):
        return [name for name in names if not _module_available(name)]
    probe = (
        "import importlib.util, sys\n"
        "print('\\n'.join(name for name in sys.argv[1:] "
        "if importlib.util.find_spec(name) is None))"
    )
    try:
        process = subprocess.run(
            [python, "-c", probe, *names],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return list(names)
    if process.returncode != 0:
        return list(names)
    return [line.strip() for line in process.stdout.splitlines() if line.strip()]


def _pip_install(python: str, packages: list[str]) -> bool:
    if not packages:
        return True
    attempts = [
        [python, "-m", "pip", "install", "--user", *packages],
        [python, "-m", "pip", "install", "--user", "--break-system-packages", *packages],
    ]
    installed = False
    for command in attempts:
        try:
            process = subprocess.run(command, capture_output=True, text=True)
        except OSError:
            continue
        if process.returncode == 0:
            installed = True
            break
        for line in (process.stderr or process.stdout or "").strip().splitlines()[-3:]:
            print(f"  {line}", file=sys.stderr)
    if not installed:
        try:
            subprocess.run([python, "-m", "ensurepip", "--user"], capture_output=True, text=True)
            process = subprocess.run(attempts[0], capture_output=True, text=True)
            installed = process.returncode == 0
        except OSError:
            installed = False
    importlib.invalidate_caches()
    if site.USER_SITE and site.USER_SITE not in sys.path:
        sys.path.append(site.USER_SITE)
    return installed


def ensure_mtk_python(python: str | None = None, verbose: bool = True) -> tuple[bool, str]:
    """Install the Python modules MTKclient imports, into the user's site.

    The window and the AppImage already carry them. A text session uses
    whatever Python launched it, which often has none of them. Installing
    with ``--user`` leaves the system Python untouched and is visible to the
    same interpreter that runs ``mtk.py``.
    """
    python = python or sys.executable
    names = [module for module, _package in MTK_PY_DEPS]
    missing = _missing_imports(python, names)
    if not missing:
        return True, ""
    packages: list[str] = []
    for module in missing:
        package = package_for_import(module)
        if package and package not in packages:
            packages.append(package)
    if os.environ.get("INNIOASIS_NO_AUTO_INSTALL") == "1":
        return False, (
            "MTKclient is missing " + ", ".join(packages)
            + " and automatic installation is disabled (INNIOASIS_NO_AUTO_INSTALL=1)."
        )
    if getattr(sys, "frozen", False) and os.path.realpath(python) == os.path.realpath(sys.executable):
        return False, (
            "This build is missing " + ", ".join(missing)
            + ", so MTKclient cannot start. Re-download the AppImage, tarball or zip."
        )
    if verbose:
        print(
            "MTKclient needs " + ", ".join(packages) + "; installing them for this user…",
            flush=True,
        )
    _pip_install(python, packages)
    still = _missing_imports(python, names)
    if still:
        still_packages = []
        for module in still:
            package = package_for_import(module)
            if package and package not in still_packages:
                still_packages.append(package)
        return False, (
            "Still missing after install: " + ", ".join(still_packages) + ".\n"
            "  Debian/Ubuntu/Mint: sudo apt install python3-colorama python3-pycryptodome python3-serial python3-usb python3-capstone\n"
            "  Fedora: sudo dnf install python3-colorama python3-pycryptodomex python3-pyserial python3-pyusb python3-capstone\n"
            "  Arch/Manjaro: sudo pacman -S python-colorama python-pycryptodome python-pyserial python-pyusb python-capstone\n"
            f"  Any pip: {python} -m pip install --user " + " ".join(still_packages) + "\n"
            "Falling back to SP Flash Tool where it is available."
        )
    return True, ""


def ensure_cryptodome(verbose: bool = True) -> tuple[bool, str]:
    """Install pycryptodome, and the rest of the modules MTKclient imports."""
    return ensure_mtk_python(verbose=verbose)


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
    runner = repo / "src" / "console" / "mtk_runner.py"
    if runner.exists():
        candidates.append([sys.executable, str(runner)])
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


def _scatter_blocks(text: str) -> list[dict[str, str]]:
    """Partition blocks in a MediaTek scatter, in file order."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("- partition_index:") or stripped.startswith("partition_index:"):
            if current:
                blocks.append(current)
            current = {}
            continue
        if current is None or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lstrip("- ").lower()
        current[key] = value.strip()
    if current:
        blocks.append(current)
    return blocks


def _downloaded_names(blocks: list[dict[str, str]]) -> list[str]:
    names = []
    for block in blocks:
        if str(block.get("is_download") or "").lower() != "true":
            continue
        file_name = str(block.get("file_name") or "").strip()
        if file_name.upper() in {"", "NONE"}:
            continue
        name = str(block.get("partition_name") or "").strip()
        if name:
            names.append(name)
    return names


def _partition_keeps_data(name: str, file_name: str = "") -> bool:
    """The window's rule: a user/usr partition is what Keep my data leaves."""
    for value in (name, file_name):
        low = str(value or "").strip().lower()
        if "user" in low or "usr" in low:
            return True
    return False


def _find_preserve_scatter(scatter: Path, selected: set[str]) -> Path | None:
    """The package scatter whose download set is a strict subset of ``selected``."""
    if not selected or not scatter.is_file():
        return None
    best: tuple[Path, set[str]] | None = None
    try:
        siblings = [path for path in scatter.parent.iterdir() if path.is_file()]
    except OSError:
        return None
    for candidate in siblings:
        if candidate.resolve() == scatter.resolve():
            continue
        if candidate.suffix.lower() != ".txt" or "scatter" not in candidate.name.lower():
            continue
        stem = candidate.stem.lower()
        if not any(tag in stem for tag in ("preserve", "keep_data", "keepdata", "nodata")):
            continue
        try:
            names = set(_downloaded_names(_scatter_blocks(candidate.read_text(encoding="utf-8", errors="ignore"))))
        except OSError:
            continue
        if not names or names == selected or not names.issubset(selected):
            continue
        if best is None or len(names) > len(best[1]):
            best = (candidate, names)
    return best[0] if best else None


def rewrite_scatter_keeping_data(text: str) -> tuple[str, tuple[str, ...]]:
    """Scatter text with user-data partitions set ``is_download: false``.

    The original string is not the return value. An empty excluded tuple means
    nothing needed masking, which is the window's "keep using the original" case.
    """
    excluded: list[str] = []
    out: list[str] = []
    mask = False
    pending_name = ""
    pending_file = ""
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("- partition_index:") or stripped.startswith("partition_index:"):
            mask = False
            pending_name = ""
            pending_file = ""
        else:
            key, _, value = stripped.partition(":")
            key = key.strip().lstrip("- ").lower()
            value = value.strip()
            if key == "partition_name" and not pending_name:
                pending_name = value
            elif key == "file_name" and not pending_file:
                pending_file = value
            if key in {"partition_name", "file_name"}:
                mask = _partition_keeps_data(pending_name, pending_file)
                if mask and pending_name and pending_name not in excluded:
                    excluded.append(pending_name)
        if mask and stripped.lower().startswith("is_download:"):
            idx = line.lower().find("true")
            if idx != -1:
                ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
                line = f"{line[:idx]}false{ending}"
                if pending_name and pending_name not in excluded:
                    excluded.append(pending_name)
        out.append(line)
    return "".join(out), tuple(excluded)


class KeepPlan:
    """How this install treats internal storage.

    ``write-all`` writes every downloadable partition. ``preserve-scatter``
    uses the package's own keep-data scatter. ``exclude-userdata`` is the
    window's fallback: a copy of the scatter with user partitions not downloaded.
    """

    def __init__(self, enabled: bool, mode: str, scatter: str, downloaded: tuple[str, ...], excluded: tuple[str, ...], messages: tuple[str, ...]):
        self.enabled = enabled
        self.mode = mode
        self.scatter = scatter
        self.downloaded = downloaded
        self.excluded = excluded
        self.messages = messages

    @property
    def message(self) -> str:
        return "\n".join(self.messages)


def describe_keep_data(folder: Path, enabled: bool) -> KeepPlan:
    """The window's keep-data decision for ``folder``, without writing anything."""
    from .download import find_scatter
    from .progress import KEEP_LEAVING, KEEP_USING

    scatter = find_scatter(Path(folder))
    if scatter is None or not scatter.is_file():
        return KeepPlan(bool(enabled), "write-all", "", (), (), ())
    try:
        blocks = _scatter_blocks(scatter.read_text(encoding="utf-8", errors="ignore"))
    except OSError:
        return KeepPlan(bool(enabled), "write-all", str(scatter), (), (), ())
    downloaded = tuple(_downloaded_names(blocks))
    if not enabled:
        return KeepPlan(False, "write-all", str(scatter), downloaded, (), ())
    selected = set(downloaded)
    preserve = _find_preserve_scatter(scatter, selected)
    if preserve is not None:
        kept_names = tuple(_downloaded_names(_scatter_blocks(preserve.read_text(encoding="utf-8", errors="ignore"))))
        excluded = tuple(sorted(selected - set(kept_names)))
        messages = [KEEP_USING.format(name=preserve.name)]
        if excluded:
            messages.append(KEEP_LEAVING.format(names=", ".join(excluded)))
        return KeepPlan(True, "preserve-scatter", str(preserve), kept_names, excluded, tuple(messages))
    _rewritten, excluded = rewrite_scatter_keeping_data(scatter.read_text(encoding="utf-8", errors="ignore"))
    if not excluded:
        return KeepPlan(True, "write-all", str(scatter), downloaded, (), ())
    remaining = tuple(name for name in downloaded if name not in excluded)
    return KeepPlan(
        True,
        "exclude-userdata",
        str(scatter.parent / ".updater_keepdata.txt"),
        remaining,
        excluded,
        (KEEP_LEAVING.format(names=", ".join(excluded)),),
    )


def stage_keep_data(folder: Path, enabled: bool) -> tuple[Path, KeepPlan]:
    """A folder whose scatter matches :func:`describe_keep_data`.

    The caller's folder is never modified. ``write-all`` returns it unchanged.
    """
    plan = describe_keep_data(folder, enabled)
    if not enabled or plan.mode == "write-all":
        return Path(folder), plan
    staging = Path(tempfile.mkdtemp(prefix=".innioasis-keep-", dir=str(Path(folder).parent)))
    try:
        for item in Path(folder).iterdir():
            target = staging / item.name
            if item.is_dir():
                shutil.copytree(item, target, copy_function=os.link)
            elif item.is_file():
                try:
                    os.link(item, target)
                except OSError:
                    shutil.copy2(item, target)
        from .download import find_scatter

        staged_scatter = find_scatter(staging)
        if staged_scatter is None:
            raise OSError("the staged package has no scatter")
        if plan.mode == "preserve-scatter":
            shutil.copy2(plan.scatter, staged_scatter)
            plan.scatter = str(staged_scatter)
        else:
            original = Path(folder) / staged_scatter.relative_to(staging)
            rewritten, _excluded = rewrite_scatter_keeping_data(
                original.read_text(encoding="utf-8", errors="ignore")
            )
            staged_scatter.write_text(rewritten, encoding="utf-8")
            plan.scatter = str(staged_scatter)
        return staging, plan
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parse_progress(line: str) -> tuple[float | None, str]:
    """The window's install progress, read from a tool's output line.

    Returns ``(percent, label)``. SP Flash Tool reports byte-weighted image
    progress; MTKclient reports a percentage per partition. Lines that only
    describe a stage (BROM connected, DA sent, partition written) return no
    percentage but a label, which is the same guidance the window shows.
    """
    text = str(line or "").strip()
    if not text:
        return None, ""
    from .progress import status_from_tool_line

    guided = status_from_tool_line(text)
    low = text.lower()
    image = re.search(
        r"(\d+(?:\.\d+)?)%\s+of image data has been sent\s+\(([^)]+)\s+of\s+([^)]+)\)",
        text,
        re.IGNORECASE,
    )
    if image:
        pct = max(0.0, min(100.0, float(image.group(1))))
        return pct, guided or f"Writing images — {image.group(2).strip()} of {image.group(3).strip()}"
    if "of image data has been sent" in low:
        simple = re.search(r"(\d+(?:\.\d+)?)%", text)
        return (float(simple.group(1)) if simple else None), guided or "Writing firmware image... (0%)"
    if "of da has been sent" in low:
        pct = re.search(r"(\d+(?:\.\d+)?)%", text)
        return (float(pct.group(1)) if pct else None), guided or "Downloading DA to device..."
    if "of bootloader has been sent" in low:
        pct = re.search(r"(\d+(?:\.\d+)?)%", text)
        return (float(pct.group(1)) if pct else None), guided or "Downloading Bootloader..."
    if "brom connected" in low or "usb port detected" in low or "usb port is obtained" in low:
        return 0.0, guided or "Device detected in BROM mode..."
    if "format succeeded" in low:
        return 20.0, guided or "Flash format succeeded..."
    if "download ok" in low:
        return None, "Partition written"
    if "erasing" in low:
        pct = re.search(r"(\d+(?:\.\d+)?)%", text)
        return (float(pct.group(1)) if pct else None), "Erasing"
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if pct:
        value = max(0.0, min(100.0, float(pct.group(1))))
        if "preloader" in low:
            return value, "Writing the preloader"
        if "boot" in low:
            return value, "Writing the boot image"
        if "system" in low:
            return value, "Writing the system image"
        if "userdata" in low:
            return value, "Writing internal storage"
        return value, guided or "Writing firmware image... ({pct}%)".format(pct=int(value))
    if guided:
        return None, guided
    if "writing" in low or "write" in low:
        return None, "Writing firmware image... (0%)"
    return None, ""


def _run(command: list[str], dry_run: bool = False, on_line=None) -> int:
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        print("simulation: not running " + " ".join(command), flush=True)
        return 0
    printable = " ".join(command)
    if on_line is None:
        print(f"$ {printable}", flush=True)
    else:
        on_line(f"$ {printable}")
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
    missing: list[str] = []
    for line in process.stdout:
        if on_line is None:
            sys.stdout.write(line)
            sys.stdout.flush()
        else:
            on_line(line.rstrip("\n"))
        found = _MISSING_MODULE.search(line)
        if found:
            missing.append(found.group(1))
    _run.last_missing = missing  # type: ignore[attr-defined]
    return process.wait()


def flash_directory(
    folder: Path,
    preloader: Path | None = None,
    da: Path | None = None,
    auth: Path | None = None,
    dry_run: bool = False,
    keep_data: bool = False,
    on_line=None,
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

    cleanup: Path | None = None
    if keep_data:
        try:
            staged, plan = stage_keep_data(folder, True)
        except OSError as exc:
            print(f'Could not prepare "Keep my data": {exc}', file=sys.stderr)
            print("No firmware images were written.", file=sys.stderr)
            return 2
        for message in plan.messages:
            print(message, flush=True)
            if on_line:
                on_line(message)
        if staged.resolve() != Path(folder).resolve():
            cleanup = staged
            folder = staged

    try:
        base = mtkclient_command()
        if base:
            python = _python_for(base)
            ready, detail = ensure_mtk_python(python)
            if ready:
                found = discover_mtk_files(folder)
                command = mtk_write_command(
                    base,
                    folder,
                    preloader=preloader or found["preloader"],
                    da=da or found["da"],
                    auth=auth or found["auth"],
                )
                code = _run(command, dry_run=dry_run, on_line=on_line)
                missed = [package_for_import(name) for name in getattr(_run, "last_missing", [])]
                missed = [name for name in missed if name]
                if code != 0 and missed and not dry_run:
                    print(
                        "MTKclient stopped because " + ", ".join(missed) + " was not installed. Installing it and trying again…",
                        flush=True,
                    )
                    if _pip_install(python, missed):
                        code = _run(command, dry_run=dry_run, on_line=on_line)
                return code
            print(detail, file=sys.stderr)
        else:
            print(
                "MTKClient is not installed. On the lite install:\n"
                "  pip install --user mtkclient\n"
                "or put mtk.py in ~/.local/share/innioasis-updater/tools/.",
                file=sys.stderr,
            )
            if wsl.is_wsl():
                print(wsl.no_backend_hint(), file=sys.stderr)

        fallback = sp_flash_directory(folder, dry_run=dry_run, on_line=on_line)
        if fallback is not None:
            return fallback
        return 2
    finally:
        if cleanup is not None:
            shutil.rmtree(cleanup, ignore_errors=True)


def sp_flash_directory(folder: Path, dry_run: bool = False, on_line=None) -> int | None:
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
    return _run(
        [tool, *sp_flash_tool_console_args(str(scatter), str(da))],
        dry_run=dry_run,
        on_line=on_line,
    )


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
    wsl_report = wsl.report()
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
        "wsl": wsl_report["wsl"],
        "wsl_distro": wsl_report["wsl_distro"],
        "usbipd": wsl_report["usbipd"],
        "wsl_steps": wsl_report["wsl_steps"],
        "components": extra,
        "env": {key: os.environ[key] for key in ("XDG_DATA_HOME", "APPIMAGE", "INNIOASIS_LITE") if os.environ.get(key)},
    }
