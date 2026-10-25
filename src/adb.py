"""Minimal ADB helper for the Rockbox 360p post-install flow.

The app ships (or locates) a bare ``adb`` binary — without the rest of the
Android platform tools — so a freshly flashed player booted into Rockbox can
receive the 360p theme/fonts merge over ADB, then be rebooted, with no USB
Storage Mode round-trip.

When no binary or no device is found within the wizard's watch window, the
flow falls back to the USB Storage Mode folder picker (see ``theme_pack``).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

ADB_WATCH_TIMEOUT_SECONDS = 30
STANDARD_Y_ADB_SERIAL = "0123456789ABCDEF"

# Candidate on-device locations for the Rockbox data directory, probed in
# order via ``adb shell ls``.
DEVICE_ROCKBOX_CANDIDATES = (
    "/storage/sdcard0/.rockbox",
    "/storage/sdcard1/.rockbox",
    "/sdcard/.rockbox",
    "/storage/emulated/0/.rockbox",
    "/mnt/sdcard/.rockbox",
    "/mnt/sdcard2/.rockbox",
)


def _platform_adb_dir() -> str:
    if sys.platform == "darwin":
        import platform as _platform

        arch = (_platform.machine() or "").lower()
        return "macos-arm64" if "arm" in arch else "macos-x64"
    if os.name == "nt":
        return "windows-x64"
    import platform as _platform

    arch = (_platform.machine() or "").lower()
    if arch in ("aarch64", "arm64"):
        return "linux-arm64"
    return "linux-x64"


def candidate_adb_paths() -> list[Path]:
    """Ordered locations to look for a bare ``adb`` binary."""
    from . import paths as _paths

    exe = "adb.exe" if os.name == "nt" else "adb"
    plat = _platform_adb_dir()
    candidates: list[Path] = []
    bases = [
        getattr(_paths, "BUNDLE_DIR", None),
        getattr(_paths, "INSTALL_DIR", None),
        getattr(_paths, "REPO_ROOT", None),
    ]
    install_d = getattr(_paths, "INSTALL_DIR", None)
    if install_d is not None:
        bases.append(Path(install_d) / "_internal")

    for base in bases:
        if base is None:
            continue
        p_base = Path(base)
        candidates.append(p_base / "tools" / "adb" / plat / exe)
        candidates.append(p_base / "tools" / "adb" / exe)
        candidates.append(p_base / "_internal" / "tools" / "adb" / plat / exe)
        candidates.append(p_base / "_internal" / "tools" / "adb" / exe)
        candidates.append(p_base / plat / exe)
        candidates.append(p_base / exe)
    env = os.environ.get("ADB_BINARY", "")
    if env:
        candidates.insert(0, Path(env))
    return candidates


def find_adb_binary() -> Optional[str]:
    """Return a usable ``adb`` path, or None when the app has none.

    Search order: explicit ``ADB_BINARY`` env, vendored ``tools/adb``
    per-platform binaries (bundle, install dir, repo checkout), then PATH.
    Vendored binaries are made executable on POSIX when needed.
    """
    for cand in candidate_adb_paths():
        try:
            if cand and cand.is_file():
                if os.name != "nt":
                    try:
                        mode = cand.stat().st_mode
                        if not (mode & 0o111):
                            cand.chmod(mode | 0o755)
                    except OSError:
                        pass
                return str(cand)
        except OSError:
            continue
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    return None


def _run_adb(adb: str, *args: str, timeout: float = 15) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if os.name == "nt":
        adb_dir = str(Path(adb).parent)
        env["PATH"] = f"{adb_dir};{env.get('PATH', '')}"
    return subprocess.run(
        [adb, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def adb_devices(adb: str) -> list[str]:
    """Serials of ADB-attached devices in ``device`` state."""
    try:
        proc = _run_adb(adb, "devices", timeout=10)
    except Exception as e:
        logger.debug("adb devices failed: %s", e)
        return []
    if proc.returncode != 0:
        return []
    serials: list[str] = []
    for line in (proc.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    if STANDARD_Y_ADB_SERIAL in serials:
        serials.remove(STANDARD_Y_ADB_SERIAL)
        serials.insert(0, STANDARD_Y_ADB_SERIAL)
    return serials


def wait_for_adb_device(
    adb: str,
    timeout: float = ADB_WATCH_TIMEOUT_SECONDS,
    poll_interval: float = 1.0,
    cancel_cb: Optional[Callable[[], bool]] = None,
    progress_cb: Optional[Callable[[float], None]] = None,
) -> Optional[str]:
    """Poll ``adb devices`` until a device appears, the timeout lapses, or
    ``cancel_cb`` returns True. Returns the first serial, or None."""
    deadline = time.monotonic() + max(1.0, timeout)
    while True:
        if cancel_cb is not None:
            try:
                if cancel_cb():
                    return None
            except Exception:
                return None
        serials = adb_devices(adb)
        if serials:
            return serials[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if progress_cb is not None:
            try:
                progress_cb(1.0 - remaining / max(1.0, timeout))
            except Exception:
                pass
        time.sleep(min(poll_interval, remaining))


def adb_shell_ls(adb: str, serial: str, device_path: str) -> bool:
    """True when ``device_path`` exists on the device."""
    try:
        proc = _run_adb(adb, "-s", serial, "shell", "ls", device_path, timeout=15)
    except Exception:
        return False
    return proc.returncode == 0


def adb_find_rockbox_dir(adb: str, serial: str) -> Optional[str]:
    """Locate the player's ``.rockbox`` directory over ADB."""
    found = adb_find_all_rockbox_dirs(adb, serial)
    return found[0] if found else None


def adb_find_all_rockbox_dirs(adb: str, serial: str) -> list[str]:
    """Every probed ``.rockbox`` directory on the device (passive merges hit all)."""
    found: list[str] = []
    try:
        proc = _run_adb(
            adb,
            "-s",
            serial,
            "shell",
            'for p in /storage/*/.rockbox /sdcard/.rockbox /mnt/*/.rockbox; do '
            '[ -d "$p" ] && (readlink -f "$p" 2>/dev/null || echo "$p"); done',
            timeout=15,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                line = line.strip()
                if (
                    line
                    and line.startswith("/")
                    and line not in found
                    and "No such" not in line
                    and "not found" not in line
                ):
                    found.append(line)
            if found:
                return found
    except Exception as e:
        logger.debug("ADB shell rockbox discovery failed: %s", e)

    for cand in DEVICE_ROCKBOX_CANDIDATES:
        try:
            if adb_shell_ls(adb, serial, cand):
                if cand not in found:
                    found.append(cand)
        except Exception:
            continue
    return found


def adb_push_merge(
    adb: str,
    serial: str,
    local_dir: str | Path,
    device_dir: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> tuple[bool, str]:
    """Push ``local_dir`` contents into ``device_dir``, overwriting same-name
    files. Tries a single recursive push first, then per-file pushes."""
    local = Path(local_dir)
    if not local.is_dir():
        return False, f"Local theme directory not found: {local}"
    files = [p for p in local.rglob("*") if p.is_file()]
    if progress_cb is not None:
        try:
            progress_cb(0, len(files))
        except Exception:
            pass
    try:
        proc = _run_adb(adb, "-s", serial, "push", f"{local}{os.sep}.", device_dir, timeout=300)
    except Exception as e:
        return False, str(e)
    if proc.returncode == 0:
        if progress_cb is not None:
            try:
                progress_cb(len(files), len(files))
            except Exception:
                pass
        return True, ""
    # Fallback: push file by file so one bad file cannot sink the batch.
    errors: list[str] = []
    done = 0
    for src in files:
        rel = src.relative_to(local).as_posix()
        try:
            proc = _run_adb(adb, "-s", serial, "push", str(src), f"{device_dir}/{rel}", timeout=120)
        except Exception as e:
            errors.append(f"{rel}: {e}")
            continue
        done += 1
        if progress_cb is not None:
            try:
                progress_cb(done, len(files))
            except Exception:
                pass
        if proc.returncode != 0:
            errors.append(f"{rel}: {(proc.stderr or '').strip()}")
    if errors and done == 0:
        return False, "; ".join(errors[:5])
    return True, "" if not errors else f"{done}/{len(files)} pushed; issues: {'; '.join(errors[:3])}"


def adb_reboot(adb: str, serial: str) -> bool:
    """Reboot the player over ADB. Returns True when the command was sent."""
    try:
        proc = _run_adb(adb, "-s", serial, "reboot", timeout=15)
        return proc.returncode == 0
    except Exception as e:
        logger.debug("adb reboot failed: %s", e)
        return False


def adb_enable_mass_storage(adb: str, serial: str) -> bool:
    """Attempt to enable USB mass storage mode via ADB property/service commands."""
    try:
        proc = _run_adb(
            adb,
            "-s",
            serial,
            "shell",
            "setprop sys.usb.config mass_storage,adb 2>/dev/null || setprop sys.usb.config mass_storage 2>/dev/null || svc usb setFunctions mass_storage 2>/dev/null",
            timeout=10,
        )
        return proc.returncode == 0
    except Exception as e:
        logger.debug("adb enable mass storage skipped/failed: %s", e)
        return False
