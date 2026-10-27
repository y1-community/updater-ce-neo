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

ADB_WATCH_TIMEOUT_SECONDS = 180
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
    # Never let adb (or the daemon it forks on first use) open a console window,
    # and keep its stderr (e.g. "no devices", "daemon not running") out of any
    # visible stream: callers read the captured output themselves.
    from .quiet_subprocess import hidden_window_kwargs

    return subprocess.run(
        [adb, *args],
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        env=env,
        **hidden_window_kwargs(),
    )


def _cancelled(cancel_cb: Optional[Callable[[], bool]]) -> bool:
    if cancel_cb is None:
        return False
    try:
        return bool(cancel_cb())
    except Exception:
        # Preserve the worker if its interruption state cannot be read.
        logger.debug("ADB cancellation callback failed", exc_info=True)
        return False


def _progress(progress_cb: Optional[Callable[[int, int], None]], curr: int, total: int) -> None:
    if progress_cb is None:
        return
    try:
        progress_cb(curr, total)
    except Exception:
        logger.debug("ADB transfer progress callback failed", exc_info=True)


def _shell_quote(value: str) -> str:
    """Quote one path for the remote POSIX shell used by Android."""
    return "'" + value.replace("'", "'\\''") + "'"


def _absolute_rockbox_path(relative_path: str) -> bool:
    """Reject archive paths that can escape the device's .rockbox directory."""
    path = relative_path.replace("\\", "/")
    return bool(path) and not path.startswith("/") and all(part not in ("", ".", "..") for part in path.split("/"))


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
        if _cancelled(cancel_cb):
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
    """True when ``device_path`` exists on the device.

    The host adb client does not forward the device shell's exit status, so the
    result is read from a marker printed only when the path exists.
    """
    try:
        proc = _run_adb(
            adb, "-s", serial, "shell",
            f"if [ -e {_shell_quote(device_path)} ]; then echo __ADB_EXISTS__; fi",
            timeout=15,
        )
    except Exception:
        return False
    return proc.returncode == 0 and "__ADB_EXISTS__" in (proc.stdout or "")


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
            '[ -d "$p" ] && (cd "$p" 2>/dev/null && pwd -P || readlink -f "$p" 2>/dev/null || echo "$p"); done',
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
                canon = None
                try:
                    cproc = _run_adb(
                        adb, "-s", serial, "shell", f'cd "{cand}" 2>/dev/null && pwd -P', timeout=5
                    )
                    if cproc.returncode == 0 and cproc.stdout.strip().startswith("/"):
                        canon = cproc.stdout.strip()
                except Exception:
                    pass
                target = canon or cand
                if target not in found:
                    found.append(target)
        except Exception:
            continue
    return found



def _run_adb_push_with_progress(
    adb: str,
    serial: str,
    local_path: str | Path,
    remote_path: str,
    *,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
    timeout: float = 600,
) -> subprocess.CompletedProcess:
    """Run one adb push while forwarding adb's percentage output to the UI."""
    import re
    import threading

    env = os.environ.copy()
    if os.name == "nt":
        env["PATH"] = f"{Path(adb).parent};{env.get('PATH', '')}"
    local_args = [str(p) for p in local_path] if isinstance(local_path, (list, tuple)) else [str(local_path)]
    # adb push's remote path is an argv operand (adb escapes it for its own
    # device-shell command); shell quote marks here become literal filename
    # characters. Quote only paths passed as adb shell command strings.
    args = [adb, "-s", serial, "push", *local_args, remote_path]

    from .quiet_subprocess import hidden_window_kwargs

    process = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        env=env,
        **hidden_window_kwargs(),
    )
    output = bytearray()
    reader_error: list[BaseException] = []
    last_percent = -1
    tail = b""

    def read_output() -> None:
        nonlocal last_percent, tail
        assert process.stdout is not None

        try:
            while True:
                chunk = os.read(process.stdout.fileno(), 4096)
                if not chunk:
                    break
                output.extend(chunk)
                data = tail + chunk
                for match in re.finditer(rb"(?<!\d)(\d{1,3})%", data):
                    percent = min(100, int(match.group(1)))
                    if percent <= last_percent:
                        continue
                    last_percent = percent
                    _progress(progress_cb, 5 + int(percent * 0.60), 100)
                tail = data[-32:]
        except BaseException as exc:
            reader_error.append(exc)

    reader = threading.Thread(target=read_output, name="adb-push-output", daemon=True)
    reader.start()
    deadline = time.monotonic() + max(1.0, timeout)
    was_cancelled = False
    while process.poll() is None:
        if _cancelled(cancel_cb):
            was_cancelled = True
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            break
        if time.monotonic() >= deadline:
            process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=2)
            raise subprocess.TimeoutExpired(args, timeout, output=bytes(output).decode("utf-8", "replace"))
        time.sleep(0.1)
    returncode = process.wait()
    reader.join(timeout=5)
    if reader.is_alive():
        if process.stdout is not None:
            process.stdout.close()
        reader.join(timeout=1)
    if reader_error:
        logger.debug("Could not read adb push progress output", exc_info=reader_error[0])
    if process.stdout is not None:
        process.stdout.close()
    stdout = bytes(output).decode("utf-8", "replace")
    if was_cancelled:
        return subprocess.CompletedProcess(args, 130, stdout=stdout, stderr="Theme transfer cancelled")
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


def adb_push_merge(
    adb: str,
    serial: str,
    local_dir: str | Path,
    device_dir: str,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Push ``local_dir`` contents into ``device_dir``, overwriting same-name
    files with granular batch-level progress reporting and robust fallbacks."""
    local = Path(local_dir)
    if not local.is_dir():
        return False, f"Local theme directory not found: {local}"
    files = sorted(p for p in local.rglob("*") if p.is_file())
    total_files = len(files)
    if total_files == 0:
        if progress_cb is not None:
            try:
                progress_cb(0, 0)
            except Exception:
                pass
        return True, ""

    _progress(progress_cb, 0, total_files)

    # Ensure destination subdirectories exist on device up front
    rel_dirs = sorted({p.parent.relative_to(local).as_posix() for p in files if p.parent != local})
    if rel_dirs:
        chunk_size = 50
        for i in range(0, len(rel_dirs), chunk_size):
            if _cancelled(cancel_cb):
                return False, "Theme transfer cancelled"
            chunk = rel_dirs[i:i + chunk_size]
            targets_str = " ".join(_shell_quote(f"{device_dir}/{d}") for d in chunk)
            try:
                proc = _run_adb(
                    adb, "-s", serial, "shell",
                    f"mkdir -p {_shell_quote(device_dir)} {targets_str} && echo __ADB_MKDIR_OK__",
                    timeout=25,
                )
                if proc.returncode != 0 or (
                    "__ADB_MKDIR_OK__" not in (proc.stdout or "")
                    and not (proc.stdout == "" and proc.stderr == "")
                ):
                    return False, (
                        proc.stderr or proc.stdout or "Could not create theme directories on device"
                    ).strip()
            except Exception as e:
                logger.debug("Failed pre-creating device directories %s: %s", chunk, e)
                return False, str(e)

    # Group files by relative parent directory to keep destination commands clean
    files_by_dir: dict[str, list[Path]] = {}
    for p in files:
        rel_parent = p.parent.relative_to(local).as_posix()
        files_by_dir.setdefault(rel_parent, []).append(p)

    BATCH_SIZE = 25
    done = 0
    errors: list[str] = []

    for rel_dir, flist in files_by_dir.items():
        if _cancelled(cancel_cb):
            return False, "Theme transfer cancelled"

        target_dir = device_dir if rel_dir == "." else f"{device_dir}/{rel_dir}"
        for i in range(0, len(flist), BATCH_SIZE):
            if _cancelled(cancel_cb):
                return False, "Theme transfer cancelled"

            batch = flist[i:i + BATCH_SIZE]

            batch_args = [str(f) for f in batch]
            try:
                proc = _run_adb_push_with_progress(
                    adb,
                    serial,
                    batch_args,
                    f"{target_dir}/",
                    progress_cb=lambda p, base=done, batch_size=len(batch): _progress(
                        progress_cb,
                        min(total_files, base + batch_size * max(0, p - 5) / 60),
                        total_files,
                    ),
                    cancel_cb=cancel_cb,
                    timeout=600,
                )
            except Exception as e:
                proc = None
                logger.debug("ADB batch push failed for %s: %s; retrying per-file", target_dir, e)

            if proc is not None and proc.returncode == 0:
                done += len(batch)
                _progress(progress_cb, done, total_files)
            elif proc is not None and proc.returncode == 130:
                return False, "Theme transfer cancelled"
            else:
                # A batch failure falls back file-by-file; don't treat a
                # recovered batch failure as a final error.
                if proc is not None:
                    logger.debug(
                        "ADB batch push failed for %s (%s); retrying per-file",
                        target_dir,
                        (proc.stderr or proc.stdout or "unknown error").strip(),
                    )
                # Fallback for this specific batch: push file-by-file
                for src in batch:
                    if _cancelled(cancel_cb):
                        return False, "Theme transfer cancelled"
                    rel = src.relative_to(local).as_posix()
                    dest_path = f"{device_dir}/{rel}"
                    try:
                        single_proc = _run_adb_push_with_progress(
                            adb,
                            serial,
                            src,
                            dest_path,
                            progress_cb=lambda p, base=done: _progress(
                                progress_cb, min(total_files, base + p / 100), total_files
                            ),
                            cancel_cb=cancel_cb,
                            timeout=600,
                        )
                        if single_proc.returncode == 0:
                            done += 1
                        elif single_proc.returncode == 130:
                            return False, "Theme transfer cancelled"
                        else:
                            errors.append(f"{rel}: {(single_proc.stderr or single_proc.stdout or '').strip()}")
                    except Exception as e:
                        if _cancelled(cancel_cb):
                            return False, "Theme transfer cancelled"
                        errors.append(f"{rel}: {e}")
                    _progress(progress_cb, done, total_files)

    if errors:
        return False, f"{done}/{total_files} files pushed; issues: {'; '.join(errors[:5])}"
    return True, ""


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


def adb_push_and_unpack_zip(
    adb: str,
    serial: str,
    zip_path: str | Path,
    device_dirs: list[str],
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str]:
    """Transfer the themes/fonts zip to the device and unpack directly into target .rockbox locations.

    Pushes the archive as a single transfer to on-device temp storage, then uses
    adb shell to unpack directly into each candidate .rockbox folder using on-device
    unzip (unzip / busybox / toybox). Cleans up the temporary zip when done.
    Returns (True, message) on success, or (False, error) on failure.
    """
    local_zip = Path(zip_path)
    if not local_zip.is_file() or local_zip.stat().st_size < 1024:
        return False, f"Theme archive not found or invalid: {local_zip}"

    if not device_dirs:
        return False, "No Rockbox storage directory was detected on the player"
    if any(not _absolute_rockbox_path(path.removeprefix("/")) for path in device_dirs):
        return False, "An invalid Rockbox destination path was detected on the player"

    if _cancelled(cancel_cb):
        return False, "Theme transfer cancelled"

    _progress(progress_cb, 0, 100)

    # Store the archive with a unique name beside the selected .rockbox folder:
    # this works without root and never overwrites an existing user file.
    import uuid

    target_parent = device_dirs[0].rsplit("/.rockbox", 1)[0] or "/sdcard"
    remote_tmp_zip = f"{target_parent}/.rockbox_theme_payload_{uuid.uuid4().hex}.zip"

    def remove_remote_archive() -> None:
        try:
            _run_adb(adb, "-s", serial, "shell", f"rm -f {_shell_quote(remote_tmp_zip)}", timeout=10)
        except Exception:
            logger.warning("Could not clean up temporary Rockbox theme archive on player storage")

    logger.info("Pushing theme pack archive %s to device %s:%s...", local_zip, serial, remote_tmp_zip)
    try:
        proc = _run_adb_push_with_progress(
            adb,
            serial,
            local_zip,
            remote_tmp_zip,
            progress_cb=progress_cb,
            cancel_cb=cancel_cb,
            timeout=600,
        )
    except Exception as e:
        remove_remote_archive()
        return False, f"Could not push themes zip to device: {e}"
    if proc.returncode == 130:
        remove_remote_archive()
        return False, "Theme transfer cancelled"
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "push failed").strip()
        remove_remote_archive()
        return False, f"Could not push themes zip to device: {err}"

    if _cancelled(cancel_cb):
        remove_remote_archive()
        return False, "Theme transfer cancelled"

    unpacked_dirs: list[str] = []
    errors: list[str] = []

    for idx, d_dir in enumerate(device_dirs):
        if _cancelled(cancel_cb):
            remove_remote_archive()
            return False, "Theme transfer cancelled"

        # Prepare one target, then unpack the same normalized archive into it.
        try:
            mkdir_proc = _run_adb(
                adb, "-s", serial, "shell",
                f"mkdir -p {_shell_quote(d_dir)} && echo __ADB_MKDIR_OK__",
                timeout=20,
            )
        except Exception as e:
            errors.append(f"{d_dir}: could not prepare destination: {e}")
            continue
        if mkdir_proc.returncode != 0 or "__ADB_MKDIR_OK__" not in (mkdir_proc.stdout or ""):
            errors.append(f"{d_dir}: could not prepare destination: {(mkdir_proc.stderr or mkdir_proc.stdout or 'mkdir failed').strip()}")
            continue

        quoted_zip = _shell_quote(remote_tmp_zip)
        quoted_dir = _shell_quote(d_dir)
        cmd = (
            f"if command -v unzip >/dev/null 2>&1; then "
            f"  unzip -o {quoted_zip} -d {quoted_dir}; "
            f"elif command -v busybox >/dev/null 2>&1; then "
            f"  busybox unzip -o {quoted_zip} -d {quoted_dir}; "
            f"elif command -v toybox >/dev/null 2>&1; then "
            f"  toybox unzip -o {quoted_zip} -d {quoted_dir}; "
            f"elif [ -x /system/bin/unzip ]; then "
            f"  /system/bin/unzip -o {quoted_zip} -d {quoted_dir}; "
            f"elif [ -x /system/xbin/unzip ]; then "
            f"  /system/xbin/unzip -o {quoted_zip} -d {quoted_dir}; "
            f"elif [ -x /system/xbin/busybox ]; then "
            f"  /system/xbin/busybox unzip -o {quoted_zip} -d {quoted_dir}; "
            f"else echo __ADB_NO_UNZIP__; exit 127; fi"
        )

        try:
            unpack_proc = _run_adb(adb, "-s", serial, "shell", cmd, timeout=180)
        except Exception as e:
            errors.append(f"{d_dir}: {e}")
            continue
        out = (unpack_proc.stdout or "").strip()
        err = (unpack_proc.stderr or "").strip()

        # A successful shell exit only proves unzip ran. Verify expected
        # payload content exists in this destination before claiming success.
        has_fonts = adb_shell_ls(adb, serial, f"{d_dir}/fonts")
        has_wps = adb_shell_ls(adb, serial, f"{d_dir}/wps")
        has_themes = adb_shell_ls(adb, serial, f"{d_dir}/themes")

        if unpack_proc.returncode == 0 and (has_fonts or has_wps or has_themes):
            unpacked_dirs.append(d_dir)
            if progress_cb is not None:
                step = 65 + int((idx + 1) / len(device_dirs) * 30)
                try:
                    progress_cb(min(95, step), 100)
                except Exception:
                    pass
        else:
            errors.append(f"{d_dir}: {err or out or 'unzip failed or tools missing'}")

    # Remove the temporary archive even when a destination failed.
    try:
        cleanup_proc = _run_adb(
            adb, "-s", serial, "shell",
            f"rm -f {_shell_quote(remote_tmp_zip)} && echo __ADB_CLEANUP_OK__",
            timeout=15,
        )
        if cleanup_proc.returncode != 0 or "__ADB_CLEANUP_OK__" not in (cleanup_proc.stdout or ""):
            errors.append("Could not remove temporary theme archive from player storage")
    except Exception as e:
        errors.append(f"Could not remove temporary theme archive from player storage: {e}")

    if not unpacked_dirs:
        return False, "; ".join(errors[:5]) or "On-device unpack failed: no target directory was verified"

    if progress_cb is not None:
        try:
            progress_cb(100, 100)
        except Exception:
            pass

    return True, f"Unpacked into {', '.join(unpacked_dirs)}"
