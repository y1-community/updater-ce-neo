"""Flash service — dual-backend strategy (port of InniUpdaterChin ``app.flash_service``).

Platform matrix (uniform across Windows, macOS, Linux):
- Auto: SP Flash Tool on Windows/Linux, MTKClient on macOS.
- SP Flash Tool: available on Windows and Linux only; not built for macOS.
- MTKClient: available on all three platforms.  On Windows it requires the
  MediaTek USB driver to be installed.

The user can switch backends at any time while waiting for a device; the
previous backend's subprocess is terminated hard and its signals detached so
the new search starts cleanly.

Provides ``ExtractWorker`` (background extraction), ``FlashWorker`` (the full
extract → validate → flash pipeline for both backends) and ``FlashService``
(orchestration, cancellation, zombie-process reaping, device monitor).
"""

import logging
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import zipfile
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal

from . import linux_sp_flash, paths

paths.ensure_mtkclient_importable()

logger = logging.getLogger(__name__)

# --- platform constants ------------------------------------------------------
IS_WINDOWS = paths.IS_WINDOWS
IS_MAC = paths.IS_MAC
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_SUBPROCESS_NO_WINDOW = CREATE_NO_WINDOW

# --- pipeline steps ----------------------------------------------------------
STEP_EXTRACTING = "EXTRACTING"
STEP_WAITING = "WAITING"
STEP_DETECT = "DETECT"
STEP_DOWNLOAD_DA = "DOWNLOAD_DA"
STEP_DOWNLOAD_BL = "DOWNLOAD_BL"
STEP_WRITE = "WRITE"
STEP_DONE = "DONE"

EXTRACT_COMPLETE_MARKER = ".extract_complete"

_SP_LOG_ROOT = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "SP_FT_Logs"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _to_short_path(path):
    """Convert a path to a Windows 8.3 short name (SP Flash Tool v5.2016 is an
    ANSI/Qt4 app that can fail on non-ASCII paths). No-op on non-Windows."""
    s = str(path)
    if not IS_WINDOWS:
        return s
    try:
        import ctypes
        from ctypes import wintypes

        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        GetShortPathNameW.restype = wintypes.DWORD
        buf = ctypes.create_unicode_buffer(261)
        n = GetShortPathNameW(s, buf, 261)
        if n == 0:
            return s
        if n >= 261:
            buf = ctypes.create_unicode_buffer(n + 1)
            if GetShortPathNameW(s, buf, n + 1) == 0:
                return s
        return buf.value or s
    except Exception:
        return s


def compute_extract_dir(package_path):
    package = Path(package_path)
    return package.parent / f".{package.stem}_extracted"


def completed_extract_dir(package_path):
    """Return the extract dir for ``package_path`` when a previous run already
    finished extracting it, else ``""`` (so callers can skip re-extraction —
    the firmware is already on disk before a backend switch / MTKClient run)."""
    if not package_path:
        return ""
    d = compute_extract_dir(package_path)
    return str(d) if _is_extract_complete(d) else ""


def _is_extract_complete(extract_dir):
    return extract_dir.exists() and (extract_dir / EXTRACT_COMPLETE_MARKER).exists()


def _mark_extract_complete(extract_dir):
    try:
        (extract_dir / EXTRACT_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    except Exception:
        pass


def _parse_sp_size(text):
    """Parse '12.5 MB' style sizes into bytes."""
    m = re.search(r"([0-9.]+)\s*([KMGT]?)", text)
    if not m:
        return 0
    value = float(m.group(1))
    mult = {"": 1, "K": 1024, "M": 1048576, "G": 1073741824, "T": 1099511627776}
    return int(value * mult.get(m.group(2).upper(), 1))


# ---------------------------------------------------------------------------
# Extraction worker
# ---------------------------------------------------------------------------
class ExtractWorker(QThread):
    """Extract a firmware package in the background (used by the select page
    to validate/peek a package before the flash flow starts)."""

    progress = Signal(int)
    finished = Signal(bool, str, str)  # ok, extract_dir, error

    def __init__(self, package_path, parent=None):
        super().__init__(parent)
        self.package_path = package_path
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            extract_dir = self._extract(self.package_path)
            if self._cancelled:
                self.finished.emit(False, "", "USER_CANCELLED")
                return
            self.finished.emit(True, str(extract_dir), "")
        except Exception as e:
            logger.error("Extraction failed: %s", e, exc_info=True)
            self.finished.emit(False, "", str(e))

    def _extract(self, package_path):
        extract_dir = compute_extract_dir(package_path)
        if _is_extract_complete(extract_dir):
            return extract_dir
        shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(package_path).suffix.lower()
        if suffix == ".zip":
            self._extract_zip(package_path, extract_dir)
        else:
            self._extract_rar(package_path, extract_dir)
        _mark_extract_complete(extract_dir)
        return extract_dir

    def _extract_zip(self, package, extract_dir):
        with zipfile.ZipFile(package, "r") as zf:
            infos = zf.infolist()
            total_bytes = sum(info.file_size for info in infos) or 1
            extracted = 0
            last_pct = -1
            for info in infos:
                if self._cancelled:
                    return
                target = extract_dir / info.filename
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    while True:
                        if self._cancelled:
                            return
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        dst.write(chunk)
                        extracted += len(chunk)
                        pct = min(99, int(extracted * 99 / total_bytes))
                        if pct != last_pct:
                            last_pct = pct
                            self.progress.emit(pct)
            self.progress.emit(99)

    def _extract_rar(self, package, extract_dir):
        unrar = paths.find_unrar()
        if not unrar:
            raise RuntimeError(
                "UnRAR tool not found. On Windows install WinRAR; on macOS run 'brew install unrar'"
            )
        cmd = [unrar, "x", "-o+", "-y", str(package), str(extract_dir) + os.sep]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_SUBPROCESS_NO_WINDOW,
        )
        buf = b""
        last_pct = -1
        while not self._cancelled:
            raw = proc.stdout.read1(1024)  # type: ignore[union-attr]
            if not raw:
                break
            buf += raw
            for m in re.finditer(rb"(\d+)%", buf):
                pct = min(99, int(m.group(1)))
                if pct != last_pct:
                    last_pct = pct
                    self.progress.emit(pct)
            if len(buf) > 4096:
                buf = buf[-256:]
        if self._cancelled:
            proc.terminate()
            return
        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"UnRAR exit code {proc.returncode}")
        self.progress.emit(99)


# ---------------------------------------------------------------------------
# Scatter parsing (the app's own line-based parser — port of app.flash_service)
# ---------------------------------------------------------------------------
def _find_scatter(directory):
    d = Path(directory)
    if not d.exists():
        return None
    for f in d.rglob("*scatter*.txt"):
        return f
    for f in d.rglob("*Scatter*.txt"):
        return f
    return None


def _parse_scatter_platform(scatter_path):
    try:
        for line in Path(scatter_path).read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*-?\s*platform\s*:\s*(MT\w+)", line, re.IGNORECASE)
            if m:
                return m.group(1).upper()
    except Exception:
        pass
    return ""


def _resolve_image_file(scatter_dir, fname):
    exact = scatter_dir / fname
    if exact.exists():
        return exact
    stem, ext = os.path.splitext(fname)
    for suffix in ("-verified", "-sign"):
        variant = scatter_dir / f"{stem}{suffix}{ext}"
        if variant.exists():
            return variant
    return exact


def _list_expected_images(scatter_path):
    """Return [(partition_name, resolved_image_path)] for every
    ``is_download: true`` partition with a real image file."""
    expected = []
    scatter_dir = Path(scatter_path).parent
    try:
        content = Path(scatter_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    def flush(part):
        if part.get("is_download", "").lower() != "true":
            return
        fname = part.get("file_name", "NONE")
        pname = part.get("partition_name", "")
        if fname == "NONE" or not pname:
            return
        expected.append((pname, _resolve_image_file(scatter_dir, fname)))

    current = {}
    for raw in content.split("\n"):
        line = raw.strip()
        if line.startswith("- partition_index:"):
            if current:
                flush(current)
            current = {}
            continue
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            current[key.strip().lstrip("- ")] = value.strip()
    if current:
        flush(current)
    return expected


def _parse_scatter(scatter_path):
    results = []
    for pname, fpath in _list_expected_images(scatter_path):
        if fpath.exists():
            results.append((pname, fpath))
    return results


def _missing_images(scatter_path):
    return [name for (name, path) in _list_expected_images(scatter_path) if not path.exists()]


# ---------------------------------------------------------------------------
# Flash worker
# ---------------------------------------------------------------------------
class FlashWorker(QThread):
    """Background flash: extract → validate → dispatch to the platform backend."""

    step_changed = Signal(str)
    progress = Signal(int)
    log_message = Signal(str)
    finished = Signal(bool, str)  # ok, error_code

    def __init__(self, package_path, pre_extracted_dir="", method="auto", parent=None):
        super().__init__(parent)
        self.package_path = package_path
        self.pre_extracted_dir = pre_extracted_dir
        # Flash backend method: "auto" (platform default), "sp" (SP Flash
        # Tool), or "mtk" (MTKClient). The user can pick manually; on macOS
        # "sp" is unavailable and always falls back to MTKClient.
        self.method = (method or "auto").lower()
        self._cancelled = False
        self._process = None
        self._sp_progress_hwm = 0
        self._sp_mismatch = False
        self._sp_err_code = None
        self._start_time = time.time()

    # -- lifecycle ----------------------------------------------------------
    def cancel(self):
        """Ask the backend to stop (used for user cancel AND backend switches).

        The SP Flash Tool subprocess is terminated hard so a switch to the
        other backend really frees the port/device; the worker thread then
        unblocks and exits without forwarding signals (the service detaches
        them in ``FlashService.cancel_flash``).
        """
        self._cancelled = True
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            if IS_WINDOWS:
                # TerminateProcess is async; give it a moment then force-kill
                # the process tree so no child of flash_tool survives the switch.
                try:
                    self._process.wait(timeout=2.0)
                except Exception:
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                            capture_output=True,
                        )
                    except Exception:
                        pass

    def _log(self, msg):
        self.log_message.emit(str(msg))

    def _update_time(self):
        pass  # UI computes elapsed/ETA from its own timer

    # -- pipeline ------------------------------------------------------------
    def run(self):
        try:
            self._do_flash()
        except BaseException as e:
            # BaseException (not just Exception): the in-process mtkclient
            # calls sys.exit()/SystemExit on several DA failure paths; surface
            # them as a clean failure here, never crash the app.
            logger.error("Flash pipeline crashed: %s", e, exc_info=True)
            # Also surface the real cause in Diagnostics so frozen builds are
            # diagnosable instead of a bare INTERNAL_ERROR.
            try:
                self._log(f"Internal error: {type(e).__name__}: {e}")
            except Exception:
                pass
            self.finished.emit(False, "INTERNAL_ERROR")

    def _do_flash(self):
        pre = Path(self.pre_extracted_dir) if self.pre_extracted_dir else None
        reused = bool(pre) and _is_extract_complete(pre)
        if reused:
            # The package is already extracted (e.g. from the SP Flash Tool
            # phase before a backend switch); skip re-extraction entirely.
            self._log(f"Reusing extracted directory: {pre}")
            extract_dir = pre
            self.step_changed.emit(STEP_WAITING)
            self.progress.emit(8)
        else:
            self.step_changed.emit(STEP_EXTRACTING)
            self.progress.emit(5)
            self._log(f"Extracting firmware package: {self.package_path}")
            extract_dir = self._extract_package()

        if self._cancelled:
            self.finished.emit(False, "USER_CANCELLED")
            return

        scatter_file, missing = self._validate_extract(extract_dir)

        if (scatter_file is None or missing) and reused:
            self._log(
                "Scatter file missing from reused directory; re-extracting..."
                if scatter_file is None
                else f"Image files missing ({', '.join(missing)}); re-extracting..."
            )
            shutil.rmtree(extract_dir, ignore_errors=True)
            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            extract_dir = self._extract_package()
            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            scatter_file, missing = self._validate_extract(extract_dir)

        if scatter_file is None:
            self._log("Error: no scatter file found in package")
            self.finished.emit(False, "NO_SCATTER_FILE")
            return
        if missing:
            self._log(f"Error: missing image files: {', '.join(missing)}")
            self.finished.emit(False, "MISSING_IMAGES")
            return

        self._log(f"Scatter file: {scatter_file}")
        platform = _parse_scatter_platform(scatter_file)
        if platform:
            self._log(f"Package platform: {platform}")

        self._dispatch_backend(extract_dir, scatter_file)

    def _dispatch_backend(self, extract_dir, scatter_file):
        """Choose SP Flash Tool vs MTKClient for this run.

        ``method`` is the user's explicit choice ("sp" / "mtk") or "auto".
        Auto leans towards SP Flash Tool on Windows and Linux (the
        InniUpdaterChin default) with MTKClient as the fallback; macOS has no
        SP Flash Tool build, so MTKClient is the only backend there.
        """
        method = self.method
        if method == "mtk":
            self._log("Using MTKClient method (user selected).")
            self._flash_via_mtkclient(extract_dir, scatter_file)
            return
        if method == "sp" and IS_MAC:
            self._log("SP Flash Tool is not available on macOS; using MTKClient.")
            self._flash_via_mtkclient(extract_dir, scatter_file)
            return
        if method == "sp" or (method == "auto" and not IS_MAC):
            # Windows: bundled SP Flash Tool payload.
            # Linux: stage the community Linux build; fall back to MTKClient
            # when staging is impossible (offline / unsupported arch).
            if IS_MAC:
                pass  # unreachable (handled above)
            elif IS_WINDOWS:
                # Auto mode falls back to MTKClient when the bundled SP Flash
                # Tool payload is missing, instead of failing the flash.
                if method == "auto" and paths.find_sp_flash_tool() is None:
                    self._log(
                        "SP Flash Tool not found; using MTKClient method (auto fallback)."
                    )
                    self._flash_via_mtkclient(extract_dir, scatter_file)
                    return
                if method == "sp":
                    self._log("Using SP Flash Tool method (user selected).")
                self._flash_via_sp_flash_tool(scatter_file)
            else:
                self._log("Checking for SP Flash Tool (Linux)...")
                distro = linux_sp_flash.detect_linux_distro()
                self._log(
                    f"Detected Linux OS: {distro.get('pretty_name', 'Linux')} "
                    f"({distro.get('family', 'generic')} family)"
                )
                ok, msg = linux_sp_flash.ensure_linux_sp_flash_tool(
                    progress_cb=self._linux_stage_progress
                )
                if ok:
                    readiness = linux_sp_flash.verify_linux_flashing_readiness()
                    if not readiness.get("udev_ok"):
                        self._log(
                            f"Warning: {readiness.get('udev_msg')}. "
                            f"If device is not detected, run: sudo bash {readiness.get('setup_script_path')}"
                        )
                    for conf in readiness.get("service_conflicts", []):
                        self._log(f"Notice: {conf.get('message')}")
                    if method == "sp":
                        self._log("Using SP Flash Tool method (user selected).")
                    self._log(f"Linux SP Flash Tool ready: {msg}")
                    self._flash_via_sp_flash_tool(scatter_file)
                else:
                    self._log(
                        f"SP Flash Tool unavailable ({msg}); using MTKClient method."
                    )
                    self._flash_via_mtkclient(extract_dir, scatter_file)
            return
        # method == "auto" on macOS: MTKClient is the only backend.
        self._log("Using MTKClient method (only backend available on macOS).")
        self._flash_via_mtkclient(extract_dir, scatter_file)

    # -- extraction ----------------------------------------------------------
    def _extract_package(self):
        worker = ExtractWorker(self.package_path)
        # Reuse the extraction helpers synchronously inside the flash worker.
        extract_dir = compute_extract_dir(self.package_path)
        if _is_extract_complete(extract_dir):
            return extract_dir
        shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(self.package_path).suffix.lower()
        if suffix == ".zip":
            worker._extract_zip(self.package_path, extract_dir)
        else:
            worker._extract_rar(self.package_path, extract_dir)
        _mark_extract_complete(extract_dir)
        return extract_dir

    def _validate_extract(self, directory):
        scatter = _find_scatter(directory)
        if scatter is None:
            return (None, [])
        return (scatter, _missing_images(scatter))

    def _linux_stage_progress(self, pct, msg):
        if msg:
            self._log(msg)
        self.progress.emit(min(40, 5 + int(pct * 0.35)))

    # -- SP Flash Tool backend (Windows + staged Linux) ----------------------
    def _flash_via_sp_flash_tool(self, scatter_file):
        if IS_WINDOWS:
            from .paths import find_sp_flash_tool

            sp_dir = find_sp_flash_tool()
            if sp_dir is None:
                self._log("SP Flash Tool not found.")
                self.finished.emit(False, "SP_FLASH_TOOL_NOT_FOUND")
                return
            flash_tool_exe = sp_dir / "flash_tool.exe"
            da_file = sp_dir / "MTK_AllInOne_DA.bin"
            log_root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "SP_FT_Logs"
            env = None
            creationflags = _SUBPROCESS_NO_WINDOW
            scatter_arg = _to_short_path(scatter_file)
            da_arg = _to_short_path(da_file)
            if not str(scatter_arg).isascii():
                self._log(
                    "Warning: scatter path contains non-ASCII characters and 8.3 short "
                    "name is unavailable. SP Flash Tool may fail to load the package."
                )
        else:
            # Linux staged package (ensure_linux_sp_flash_tool already ran).
            sp_dir = linux_sp_flash.stage_dir()
            flash_tool_exe = sp_dir / linux_sp_flash.FLASH_TOOL_LINUX_BIN
            da_file = sp_dir / "MTK_AllInOne_DA.bin"
            log_root = sp_dir / linux_sp_flash.LOG_DIR_NAME
            log_root.mkdir(parents=True, exist_ok=True)
            env = linux_sp_flash.process_env(sp_dir)
            creationflags = 0
            scatter_arg = str(scatter_file)
            da_arg = str(da_file)

        cmd = [
            str(flash_tool_exe),
            "-c", "format-download",
            "-s", scatter_arg,
            "-d", da_arg,
            "-t", "without",
            "-r",
        ]
        self.step_changed.emit(STEP_WAITING)
        self.progress.emit(8)
        self._log("Launching SP Flash Tool (console mode, searching USB)...")
        self._log("Keep the device unplugged until the connect prompt appears.")
        self._process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(sp_dir),
            env=env,
            creationflags=creationflags,
        )

        # Background thread tailing the SP Flash Tool log file.
        launch_time = time.time()
        stop_monitor = threading.Event()
        monitor = threading.Thread(
            target=self._monitor_sp_log_file, args=(stop_monitor, log_root), daemon=True
        )
        monitor.start()

        try:
            for line in iter(self._process.stdout.readline, ""):  # type: ignore[union-attr]
                line = line.rstrip("\n")
                if line:
                    self._log(f"[SP] {line}")
                if self._cancelled:
                    try:
                        self._process.terminate()
                    except Exception:
                        pass
                    stop_monitor.set()
                    self.finished.emit(False, "USER_CANCELLED")
                    return
                self._classify_sp_stdout(line)
                if self._sp_mismatch:
                    self._log("Device/package mismatch detected. Aborting.")
                    try:
                        self._process.terminate()
                    except Exception:
                        pass
                    break
        finally:
            stop_monitor.set()

        self._process.wait()
        exit_code = self._process.returncode

        if self._sp_mismatch:
            self.finished.emit(False, "DEVICE_PACKAGE_MISMATCH")
            return
        if exit_code == 0:
            self.step_changed.emit(STEP_DONE)
            self.progress.emit(100)
            self._log("Flash complete! Disconnect USB and reboot the device.")
            self.finished.emit(True, "")
            return

        log_success, error_detail = self._read_sp_log_result(launch_time, log_root)
        if "CHIP_TYPE_NOT_MATCH" in str(error_detail).upper():
            self.finished.emit(False, "DEVICE_PACKAGE_MISMATCH")
        elif log_success:
            self.step_changed.emit(STEP_DONE)
            self.progress.emit(100)
            self.finished.emit(True, "")
        elif error_detail:
            self._log(f"SP Flash Tool error: {error_detail}")
            self.finished.emit(False, str(error_detail))
        elif self._sp_err_code:
            self.finished.emit(False, f"SP_ERR_{self._sp_err_code}")
        else:
            self.finished.emit(False, f"SP_EXIT_{exit_code}")

    def _classify_sp_stdout(self, line):
        low = line.lower()
        if "s_chip_type_not_match" in low:
            self._sp_mismatch = True
        elif "scanning usb" in low or "search usb" in low:
            self.step_changed.emit(STEP_WAITING)
            self.progress.emit(10)
        elif "brom connected" in low:
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
        elif "of da has been sent" in low:
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.progress.emit(15)
        elif "of bootloader has been sent" in low:
            self.step_changed.emit(STEP_DOWNLOAD_BL)
            self.progress.emit(18)
        elif "format succeeded" in low:
            self.progress.emit(20)
        elif "of image data has been sent" in low:
            self.step_changed.emit(STEP_WRITE)
            self._update_sp_image_progress(line)
        elif "download ok" in low:
            self.step_changed.emit(STEP_DONE)
            self.progress.emit(100)
        elif "download failed" in low or "s_ft_" in low:
            m = re.search(r"(S_\w+)\s*\((\d+)\)", line)
            self._log(f"Error: {m.group(1)} (code {m.group(2)})" if m else f"SP Flash Tool error: {line}")
        elif "exception" in low and "err_code" in low:
            m = re.search(r"err_code\[(\d+)\]", line)
            if m:
                self._sp_err_code = m.group(1)
            self._log(f"SP Flash Tool exception: {line}")
        elif "error" in low and "fail" in low:
            self._log(f"Error: {line}")

    def _update_sp_image_progress(self, line):
        m = re.search(r"(\d+)%\s+of image data has been sent\s+\(([^)]+)\s+of\s+([^)]+)\)", line)
        if not m:
            return
        pct = int(m.group(1))
        total = _parse_sp_size(m.group(3))
        if total < 104857600:  # ignore small/partial update lines
            return
        mapped = 20 + int(pct * 0.7)
        if mapped > self._sp_progress_hwm:
            self._sp_progress_hwm = mapped
            self.progress.emit(mapped)

    def _monitor_sp_log_file(self, stop_event, log_root=None):
        log_root = Path(log_root or _SP_LOG_ROOT)
        time.sleep(2)
        try:
            if not log_root.exists():
                return
            newest = max(
                (p for p in log_root.iterdir() if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                default=None,
            )
            if newest is None:
                return
            log_file = newest / "QT_FLASH_TOOL.log"
            last_pos = 0
            while not stop_event.is_set():
                time.sleep(0.5)
                try:
                    size = log_file.stat().st_size
                    if size < last_pos:
                        last_pos = 0
                    if size <= last_pos:
                        continue
                    with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                        f.seek(last_pos)
                        new_lines = f.readlines()
                        last_pos = f.tell()
                    for ln in new_lines:
                        low = ln.lower()
                        if "of image data has been sent" in low:
                            self.step_changed.emit(STEP_WRITE)
                            self._update_sp_image_progress(ln)
                        elif "download ok" in low:
                            self.step_changed.emit(STEP_DONE)
                            self.progress.emit(100)
                        elif "brom connected" in low:
                            self.step_changed.emit(STEP_DETECT)
                            self.progress.emit(12)
                except Exception:
                    continue
        except Exception as e:
            logger.debug("SP log monitor stopped: %s", e)

    def _read_sp_log_result(self, launch_time, log_root=None):
        log_root = Path(log_root or _SP_LOG_ROOT)
        try:
            if not log_root.exists():
                return (False, "")
            dirs = [
                p
                for p in log_root.iterdir()
                if p.is_dir() and p.stat().st_mtime >= launch_time - 5
            ]
            if not dirs:
                return (False, "")
            newest = max(dirs, key=lambda p: p.stat().st_mtime)
            log_file = newest / "QT_FLASH_TOOL.log"
            if not log_file.exists():
                return (False, "")
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            if "Download Ok" in content or "download ok" in content:
                return (True, "")
            errors = re.findall(r"(S_\w+)\s*\((\d+)\)", content)
            errors = [(n, c) for n, c in errors if n != "S_DONE"]
            if errors:
                name, code = errors[-1]
                return (False, f"{name} ({code})")
            for line in reversed(content.splitlines()):
                if "Exception" in line and "err_code" in line:
                    m = re.search(r"err_msg\[\s*(.+?)[\]\n]", line)
                    if m:
                        return (False, m.group(1).strip())
        except Exception as e:
            logger.debug("read_sp_log_result failed: %s", e)
        return (False, "")

    # -- macOS/Linux backend: mtkclient -------------------------------------
    def _flash_via_mtkclient(self, extract_dir, scatter_file):
        self.step_changed.emit(STEP_WAITING)
        self.progress.emit(8)
        self._log("Initializing mtkclient...")
        try:
            from . import mtk_api
        except BaseException as e:
            # In frozen builds a lazy mtkclient import failure must be visible,
            # not swallowed into a bare INTERNAL_ERROR at the EXTRACTING step.
            self._log(f"mtkclient import failed: {type(e).__name__}: {e}")
            # Also write the full traceback to the on-disk log (updater.log)
            # so frozen-only import failures are diagnosable without the UI.
            logger.error("mtkclient import failed", exc_info=True)
            self.finished.emit(False, "MTK_IMPORT_FAILED")
            return

        image_files = _parse_scatter(scatter_file)
        if not image_files:
            self._log("Error: no downloadable partitions in scatter file")
            self.finished.emit(False, "NO_IMAGE_FILES")
            return
        self._log(f"Found {len(image_files)} partition image(s)")

        try:
            mtk = mtk_api.init(None, None)
        except BaseException as e:
            self._log(f"mtkclient init failed: {type(e).__name__}: {e}")
            self.finished.emit(False, "MTK_INIT_FAILED")
            return
        self._log("Waiting for MTK device... Power off the device and connect USB.")
        try:
            mtk, da_handler = mtk_api.connect(mtk, directory=str(extract_dir))
        except BaseException as e:
            # BaseException: mtkclient raises SystemExit on DA upload failure.
            self._log(f"Device connection failed: {e}\n{traceback.format_exc()}")
            self.finished.emit(False, "CONNECTION_FAILED")
            return
        if mtk is None or da_handler is None:
            self._log("Device connection returned None")
            self.finished.emit(False, "CONNECTION_FAILED")
            return

        self.step_changed.emit(STEP_WRITE)
        self.progress.emit(20)
        self._log("Device connected, starting write...")

        total = len(image_files)
        for i, (part_name, file_path) in enumerate(image_files):
            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            self._log(f"Writing {part_name} ({i + 1}/{total}): {file_path.name}")
            pct = 25 + int(i / max(total, 1) * 70)
            self.progress.emit(pct)
            try:
                da_handler.handle_da_cmds(
                    mtk,
                    "w",
                    partitions=part_name,
                    filenames=str(file_path),
                    parttype="user",
                )
            except BaseException as e:
                # BaseException: mtkclient may sys.exit() on DA errors; keep it
                # a clean per-partition failure, never an app crash.
                self._log(f"Writing {part_name} failed: {e}\n{traceback.format_exc()}")
                self.finished.emit(False, "WRITE_FAILED")
                return

        self.step_changed.emit(STEP_DONE)
        self.progress.emit(100)
        self._log("Flash complete! Disconnect USB and reboot the device.")
        self.finished.emit(True, "")


# ---------------------------------------------------------------------------
# Device monitor (USB presence poller for S3/S5 transitions)
# ---------------------------------------------------------------------------
# Supported MTK-family USB vendors (same set mtkclient's usblib matches):
# MediaTek, LG, OPPO/realme/oneplus, Sony.
_MTK_VENDOR_IDS = (0x0E8D, 0x1004, 0x22D9, 0x0FCE)


def _load_libusb_backend():
    """Create a pyusb libusb-1 backend the same way mtkclient does.

    On Windows the bundled ``libusb-1.0.dll`` (vendor/mtkclient/mtkclient/
    Windows/) is added to the DLL search path and loaded explicitly, because
    plain ``usb.core.find()`` without a backend fails with "No backend
    available" on Windows. Returns ``(usb_core, backend)`` or ``(None, None)``.
    """
    try:
        import usb.backend.libusb1  # type: ignore
        import usb.core  # type: ignore
    except Exception:
        return None, None

    if IS_WINDOWS:
        try:
            windows_dir = str(paths.MTKCLIENT_DIR / "mtkclient" / "Windows")
            if os.path.isdir(windows_dir):
                try:
                    os.add_dll_directory(windows_dir)
                except Exception:
                    pass
                os.environ["PATH"] = windows_dir + os.pathsep + os.environ.get("PATH", "")
        except Exception:
            pass
        backend = usb.backend.libusb1.get_backend(
            find_library=lambda name: "libusb-1.0.dll"
        )
    else:
        backend = usb.backend.libusb1.get_backend(
            find_library=lambda name: "libusb-1.0.so"
        )
    if backend is None:
        return None, None
    return usb.core, backend


class DeviceMonitor(QThread):
    """Polls USB for MTK-family devices and emits connect/lost.

    Faithful in intent to the Chin app's ``start_device_monitor``: it drives
    ``S3_DEVICE_DETECTED`` and ``S5_USB_DISCONNECTED``. Uses the same libusb
    backend setup as the bundled mtkclient so it works on Windows without a
    system-wide libusb install.
    """

    device_found = Signal(str)
    device_lost = Signal()
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stop = threading.Event()
        self._present = False
        self._misses = 0
        self._failed = False

    def stop(self):
        self._stop.set()

    def run(self):
        usb_core, backend = _load_libusb_backend()
        if usb_core is None:
            self._failed = True
            self.error.emit(
                "USB monitor unavailable: libusb backend could not be loaded. "
                "Device detection is disabled; use the flash backend directly."
            )
            return
        while not self._stop.is_set():
            try:
                present = False
                port_label = ""
                devices = usb_core.find(find_all=True, backend=backend)
                for dev in devices:
                    try:
                        if dev.idVendor in _MTK_VENDOR_IDS:
                            present = True
                            port_label = f"USB {dev.idVendor:04X}:{dev.idProduct:04X}"
                            break
                    except Exception:
                        continue
                if present and not self._present:
                    self._present = True
                    self._misses = 0
                    self.device_found.emit(port_label)
                elif not present and self._present:
                    # Debounce: the device re-enumerates (BROM -> preloader ->
                    # DA) during flashing, so only report a loss after several
                    # consecutive misses (~4.5 s).
                    self._misses += 1
                    if self._misses >= 3:
                        self._present = False
                        self._misses = 0
                        self.device_lost.emit()
                else:
                    self._misses = 0
            except Exception as e:
                if not self._failed:
                    self._failed = True
                    self.error.emit(f"USB monitor error: {e}")
            self._stop.wait(1.5)


# ---------------------------------------------------------------------------
# Flash service — orchestration
# ---------------------------------------------------------------------------
class FlashService(QObject):
    """Owns workers and forwards their signals; mirrors the Chin FlashService."""

    # forwarded worker signals
    step_changed = Signal(str)
    progress = Signal(int)
    log_message = Signal(str)
    flash_finished = Signal(bool, str)
    extract_finished = Signal(bool, str, str)  # ok, extract_dir, err
    extract_progress = Signal(int)
    device_found = Signal(str)
    device_lost = Signal()
    monitor_error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._extract_worker = None
        self._flash_worker = None
        self._device_monitor = None

    # -- extraction ---------------------------------------------------------
    def extract_async(self, package_path):
        self.cancel_extract()
        self._extract_worker = ExtractWorker(package_path)
        self._extract_worker.progress.connect(self.extract_progress)
        self._extract_worker.finished.connect(self.extract_finished)
        self._extract_worker.finished.connect(self._on_extract_done)
        self._extract_worker.start()

    def cancel_extract(self):
        if self._extract_worker is not None:
            self._extract_worker.cancel()

    def _on_extract_done(self, *args):
        self._extract_worker = None

    # -- flashing -----------------------------------------------------------
    def start_flash(self, package_path, pre_extracted_dir="", method="auto"):
        # Kill any backend still running (e.g. a searching flash_tool) first,
        # then start the new one. The cancelled worker's late terminal signals
        # are detached so they cannot reach the UI or clobber the new run.
        self.cancel_flash()
        worker = FlashWorker(package_path, pre_extracted_dir, method)
        self._flash_worker = worker
        worker.step_changed.connect(self.step_changed)
        worker.progress.connect(self.progress)
        worker.log_message.connect(self.log_message)
        worker.finished.connect(self.flash_finished)
        worker.finished.connect(lambda ok, err, w=worker: self._on_flash_done(w, ok, err))
        worker.start()

    def cancel_flash(self):
        worker = self._flash_worker
        if worker is None:
            return
        # Detach first: a run cancelled to switch backends must not emit
        # USER_CANCELLED into the UI or clear the reference of a newer worker.
        self._detach_worker(worker)
        worker.cancel()
        # Brief wait: the old thread (possibly mid-extraction) must die
        # before the new worker tries to rmtree the same extract dir.
        worker.wait(3000)
        self._flash_worker = None

    @staticmethod
    def _detach_worker(worker):
        for sig in (worker.step_changed, worker.progress, worker.log_message,
                    worker.finished):
            try:
                sig.disconnect()
            except Exception:
                pass

    def _on_flash_done(self, worker, ok, err):
        # Identity-guarded: only the current worker clears the slot, so a late
        # finish from a previous (cancelled) run cannot orphan a newer worker.
        if self._flash_worker is worker:
            self._flash_worker = None

    # -- device monitor -----------------------------------------------------
    def start_device_monitor(self):
        if self._device_monitor is not None and self._device_monitor.isRunning():
            return
        self._device_monitor = DeviceMonitor()
        self._device_monitor.device_found.connect(self.device_found)
        self._device_monitor.device_lost.connect(self.device_lost)
        self._device_monitor.error.connect(self.monitor_error)
        self._device_monitor.start()

    def stop_device_monitor(self):
        if self._device_monitor is not None:
            self._device_monitor.stop()

    def cleanup(self):
        self.cancel_flash()
        self.cancel_extract()
        self.stop_device_monitor()
