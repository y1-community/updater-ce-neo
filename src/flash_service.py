"""Flash service — dual-backend strategy (port of InniUpdaterChin ``app.flash_service``).

Platform matrix (uniform across Windows, macOS, Linux):
- Auto: SP Flash Tool on Windows/Linux, MTKClient on macOS.
- SP Flash Tool: available on Windows and Linux only; not built for macOS.
- MTKClient: available on all platforms; the only macOS backend.
- macOS parity: the macOS code path can be simulated on Linux/Windows with
  ``--simulate-macos`` (paths.SIMULATE_MACOS); the effective backend matrix
  is then identical to a real macOS build.
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
import sys
import threading
import time
import traceback
import zipfile
from pathlib import Path
from struct import pack, unpack
from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal

from . import linux_sp_flash, paths
from .i18n import tr

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

# --- backend log classification ----------------------------------------------
# mtkclient and the USB layer under it report target-side faults as bare errno
# strings. Two of them always mean the same physical thing — the player is
# wedged mid-handshake and will answer every further attempt with the same
# error — so they drive the retry-guidance dialog instead of piling up unseen
# in Diagnostics (a single stalled connection produced 18 identical lines).
RETRY_ERRNOS = (2, 5)
_ERRNO_RE = re.compile(r"errno[\s:=\[]*(\d+)", re.IGNORECASE)

# SP Flash Tool writes the same events to its console stream and to its own
# QT_FLASH_TOOL.log; the internal log carries more detail (DA versions, image
# checks, error traces) and is the only place some failures appear. Lines are
# compared with timestamps and spacing stripped so the internal log can be
# forwarded without repeating what the console already showed.
_TIMESTAMP_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ t]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*")
_WS_RE = re.compile(r"\s+")

# Upper bound on internal-log lines forwarded per install: the file can grow to
# thousands of lines and only the tail is interesting for a bug report.
_SP_QT_LOG_MAX_LINES = 2000


def _normalise_tool_line(line) -> str:
    """Compare tool lines across two logs (timestamp and spacing insensitive)."""
    text = str(line or "").strip()
    if not text:
        return ""
    return _WS_RE.sub(" ", _TIMESTAMP_PREFIX_RE.sub("", text)).lower()


# mtkclient's Port.py prints this Hint block once per failed handshake loop.
# It is not an error: it restates step 1 of the connection guide ("power the
# device off and reconnect"), so it re-arms the waiting stage silently.
_HINT_MARKERS = (
    "power off the phone before connecting",
    "power off the device before connecting",
)

LINE_RETRY = "retry"
LINE_CONNECT_HINT = "connect_hint"

# --- install methods ----------------------------------------------------------
# METHOD_MTK_MAC is the simulated-macOS flow: it runs the Mac code path
# (MTKClient as the only backend, mac-centric prompts) on Linux/Windows so the
# Mac experience can be exercised without Mac hardware. It maps to the same
# backend as METHOD_MTK; the difference is the platform behaviour the rest of
# the app shows for paths.SIMULATE_MACOS.
METHOD_SP = "sp"
METHOD_MTK = "mtk"
METHOD_MTK_MAC = "mtk_mac"


def default_flash_method():
    """Method used until the user picks one.

    Windows/Linux users are steered to SP Flash Tool's console-mode XML flow,
    the most reliable path on those platforms. macOS has no SP Flash Tool
    build, so MTKClient is the only option there.

    ``paths.IS_MAC`` is read live rather than the module-level ``IS_MAC``
    snapshot so simulated macOS mode (and the tests that toggle it) is
    honoured.
    """
    return METHOD_MTK if paths.IS_MAC else METHOD_SP


def normalise_method(method):
    """Coerce a persisted (possibly legacy) setting into a usable method id.

    Legacy ``"auto"`` resolves to the platform default so users land on SP
    Flash Tool rather than an ambiguous automatic choice; on macOS every
    value collapses to MTKClient, the only backend that exists there.
    """
    value = str(method or "").strip().lower()
    if value not in (METHOD_SP, METHOD_MTK, METHOD_MTK_MAC, "auto"):
        return default_flash_method()
    if paths.IS_MAC:
        return METHOD_MTK
    return default_flash_method() if value == "auto" else value


def backend_method(method):
    """Map an install method onto the backend id FlashWorker dispatches on."""
    return METHOD_MTK if normalise_method(method) == METHOD_MTK_MAC else normalise_method(method)


def classify_backend_line(line):
    """Classify a backend log line for user guidance.

    Returns ``LINE_RETRY`` when the line carries an errno from
    :data:`RETRY_ERRNOS` (the target must be reset by hand),
    ``LINE_CONNECT_HINT`` when it is mtkclient's "power off and reconnect"
    hint, or ``""`` when the line needs no special handling.
    """
    text = str(line or "")
    if not text:
        return ""
    low = text.lower()
    match = _ERRNO_RE.search(text)
    if match and int(match.group(1)) in RETRY_ERRNOS:
        return LINE_RETRY
    if "hint:" in low or any(marker in low for marker in _HINT_MARKERS):
        return LINE_CONNECT_HINT
    return ""

# Seconds of silence from the target before the MTKClient backend tells the
# user the device stopped answering. DA configuration runs inside a blocking
# in-process call that cannot be interrupted, so without this the UI would sit
# on "configuring DA..." forever (see _StallWatcher).
MTK_STALL_WARN_SECONDS = 90

# --- MTKClient session bring-up ---------------------------------------------
# A player that enumerates in preloader mode accepts the stage-2 DA upload and
# then never answers the download agent, so nothing is ever written. BROM mode
# is the mode the official tooling flashes in, so preloader-mode targets are
# restarted into BROM before the DA is uploaded.
MTK_DEVICE_VID = 0x0E8D
MTK_BROM_PID = 0x0003
MTK_PRELOADER_PID = 0x2000
# The Chinese vendor build connects directly in Preloader mode without forcing
# a BROM reboot; legacy SoCs (MT6572/MT6582) fail to reboot into BROM via watchdog.
MTK_RESTART_IN_BROM = False
MTK_BROM_WAIT_SECONDS = 30.0
MTK_RECOVERY_WAIT_SECONDS = 60.0
# One automatic retry after a DA failure (the target usually needs a power
# cycle to come back), then the retry dialog takes over.
MTK_DA_ATTEMPTS = 2
# The target re-enumerates while being restarted into BROM; the USB monitor
# must not read that as the player being unplugged mid-flash.
MTK_DEVICE_LOSS_GRACE_SECONDS = 90.0


def _wait_for_mtk_mode(pids, timeout):
    """Poll the USB bus until an MTK target with one of ``pids`` shows up.

    An int is accepted as a single PID. Returns True as soon as the device is
    seen, False on timeout. Uses the same libusb backend as the bundled
    mtkclient so it works on Windows without a system libusb install.
    """
    wanted = (pids,) if isinstance(pids, int) else tuple(pids)
    usb_core, backend = _load_libusb_backend()
    if usb_core is None:
        return False
    deadline = time.time() + float(timeout)
    while True:
        try:
            for dev in usb_core.find(find_all=True, backend=backend):
                try:
                    if dev.idVendor == MTK_DEVICE_VID and dev.idProduct in wanted:
                        return True
                except Exception:
                    continue
        except Exception as e:
            logger.debug("MTK USB poll failed: %s", e)
        if time.time() >= deadline:
            return False
        time.sleep(0.5)

_SP_LOG_ROOT = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "SP_FT_Logs"

# --- MediaTek preloader boot header constants (from vendor macOS build) -------
_BOOT_HEADER_SIZE = 2048
_BRLYT_OFFSET = 512
_PRELOADER_DATA_OFFSET = 2048
_PRELOADER_MAGIC = b"MMM\x01"
_BRLYT_MAGIC = 0x42524C59  # 'BRLY'
_BRLYT_TYPE_EMMC = 0x00010005
_BRLYT_TYPE_SDMMC = 0x00010008

# --- MediaTek flashing engine constants (Chinese release parity) ---------------
_ANDROID_SPARSE_MAGIC = 0xED26FF3A
_ANDROID_SPARSE_MAGIC_BYTES = b":\xff&\xed"
_MTK_SIGN_HEAD_MAGIC = b"SSSS"
_MTK_SIGN_TAIL_MAGIC = b"EEEE"
SIGNED_IMAGE_HEADER_SIZE = 64
SIGNED_IMAGE_FOOTER_SIZE = 236
SIGNED_IMAGE_OVERHEAD_SIZE = 300
_LEGACY_MBR_BIAS_PLATFORMS = frozenset({"MT8382", "MT6572", "MT6574", "MT6582", "MT6580"})
LEGACY_MBR_USER_ADDR_BIAS = 8388608  # 8MB (0x800000)
LEGACY_SEGMENTED_WRITE_BYTES = 52428800  # 50MB per USB transfer chunk


def _is_android_sparse(file_path) -> bool:
    """Check whether a file is an Android sparse image."""
    try:
        p = Path(file_path)
        if not p.is_file() or p.stat().st_size < 28:
            return False
        with p.open("rb") as f:
            magic = f.read(4)
        return magic == _ANDROID_SPARSE_MAGIC_BYTES
    except Exception:
        return False


def _convert_sparse_to_raw(sparse_path, out_dir):
    """Convert an Android sparse image to raw image in out_dir in-process.

    Returns (raw_path, regions) where regions is a list of [(offset, length), ...].
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / (Path(sparse_path).stem + ".raw.img")
    regions = []
    out_offset = 0

    with open(sparse_path, "rb") as rf, open(raw_path, "wb") as wf:
        hdr = rf.read(28)
        if len(hdr) != 28:
            raise RuntimeError(f"{Path(sparse_path).name} sparse header too short")
        magic, major, _minor, file_hdr_sz, chunk_hdr_sz, blk_sz, _total_blks, total_chunks, _checksum = unpack("<I4H4I", hdr)
        if magic != _ANDROID_SPARSE_MAGIC:
            raise RuntimeError(f"{Path(sparse_path).name} is not a valid sparse image")
        if major != 1:
            raise RuntimeError(f"{Path(sparse_path).name} unsupported sparse major version {major}")
        if file_hdr_sz > 28:
            rf.seek(file_hdr_sz - 28, os.SEEK_CUR)

        zero_buf = b"\x00" * 1048576

        for _ in range(total_chunks):
            chdr = rf.read(12)
            if len(chdr) != 12:
                raise RuntimeError(f"{Path(sparse_path).name} chunk header truncated")
            chunk_type, _reserved, chunk_sz, total_sz = unpack("<2H2I", chdr)
            if chunk_hdr_sz > 12:
                rf.seek(chunk_hdr_sz - 12, os.SEEK_CUR)
            data_sz = total_sz - chunk_hdr_sz
            out_sz = chunk_sz * blk_sz

            if chunk_type == 0xCAC1:  # RAW
                if data_sz != out_sz:
                    raise RuntimeError(f"{Path(sparse_path).name} RAW chunk size mismatch data={data_sz} out={out_sz}")
                if regions and (regions[-1][0] + regions[-1][1] == out_offset):
                    regions[-1] = (regions[-1][0], regions[-1][1] + out_sz)
                else:
                    regions.append((out_offset, out_sz))
                remaining = data_sz
                while remaining > 0:
                    chunk = rf.read(min(1048576, remaining))
                    if not chunk:
                        raise RuntimeError(f"{Path(sparse_path).name} RAW chunk truncated")
                    wf.write(chunk)
                    remaining -= len(chunk)
            elif chunk_type == 0xCAC2:  # FILL
                if data_sz != 4:
                    raise RuntimeError(f"{Path(sparse_path).name} FILL chunk invalid size={data_sz}")
                fill = rf.read(4)
                if len(fill) != 4:
                    raise RuntimeError(f"{Path(sparse_path).name} FILL value missing")
                if regions and (regions[-1][0] + regions[-1][1] == out_offset):
                    regions[-1] = (regions[-1][0], regions[-1][1] + out_sz)
                else:
                    regions.append((out_offset, out_sz))
                fill_buf = fill * 262144
                remaining = out_sz
                while remaining > 0:
                    n = min(len(fill_buf), remaining)
                    wf.write(fill_buf[:n])
                    remaining -= n
            elif chunk_type == 0xCAC3:  # DONT_CARE
                remaining = out_sz
                while remaining > 0:
                    n = min(len(zero_buf), remaining)
                    wf.write(zero_buf[:n])
                    remaining -= n
            elif chunk_type == 0xCAC4:  # CRC32
                if data_sz != 4:
                    raise RuntimeError(f"{Path(sparse_path).name} CRC32 chunk invalid size={data_sz}")
                crc = rf.read(4)
                if len(crc) != 4:
                    raise RuntimeError(f"{Path(sparse_path).name} CRC32 data missing")
            else:
                raise RuntimeError(f"unknown sparse chunk type=0x{chunk_type:04X}")

            out_offset += out_sz

    return raw_path, regions


def _signed_image_payload_range(file_path, file_size: int):
    """Detect if an image has MediaTek signed wrapper (64B head, 236B tail).
    Returns (offset, payload_length) or None.
    """
    if file_size <= SIGNED_IMAGE_OVERHEAD_SIZE:
        return None
    try:
        with open(file_path, "rb") as f:
            head = f.read(SIGNED_IMAGE_HEADER_SIZE)
            f.seek(max(0, file_size - SIGNED_IMAGE_FOOTER_SIZE))
            tail = f.read(SIGNED_IMAGE_FOOTER_SIZE)
    except OSError:
        return None
    if not head.startswith(_MTK_SIGN_HEAD_MAGIC):
        return None
    if _MTK_SIGN_TAIL_MAGIC not in tail:
        return None
    return (SIGNED_IMAGE_HEADER_SIZE, file_size - SIGNED_IMAGE_OVERHEAD_SIZE)


def _detect_legacy_user_addr_bias(scatter_path, parts=None):
    """Detect whether a scatter file on a legacy platform requires MBR user address biasing."""
    platform_name = _parse_scatter_platform(scatter_path)
    if platform_name not in _LEGACY_MBR_BIAS_PLATFORMS:
        return 0
    if not parts or not isinstance(parts[0], dict):
        parts = _parse_scatter_entries(scatter_path)
    has_boot_preloader = any(
        part.get("name", "").strip().lower() == "preloader"
        and part.get("region", "").strip().upper().startswith("EMMC_BOOT")
        for part in parts
    )
    user_names = {
        part.get("name", "").strip().lower()
        for part in parts
        if part.get("region", "").strip().upper() == "EMMC_USER"
    }
    if has_boot_preloader and {"mbr", "ebr1"}.issubset(user_names):
        return LEGACY_MBR_USER_ADDR_BIAS
    return 0



def _fill_boot_magic(header: bytearray, storage_type: str = "emmc"):
    storage = storage_type.lower()
    version_tail = b"\x01\x00\x00\x00"
    if storage == "ufs":
        header[0:8] = b"UFS_BOOT"
        header[8:12] = b"\x00\x00\x00\x00"
        header[12:16] = version_tail
    elif storage == "sdmmc":
        header[0:10] = b"SDMMC_BOOT"
        header[10:12] = b"\x00\x00"
        header[12:16] = version_tail
    else:
        header[0:9] = b"EMMC_BOOT"
        header[9:12] = b"\x00\x00\x00"
        header[12:16] = version_tail


def _fill_brlyt_header(header: bytearray, raw_size: int):
    from struct import pack

    image_size = _PRELOADER_DATA_OFFSET + raw_size
    header[_BRLYT_OFFSET : _BRLYT_OFFSET + 8] = b"BRLYT\x00\x00\x00"
    header[_BRLYT_OFFSET + 8 : _BRLYT_OFFSET + 12] = pack("<I", 1)
    header[_BRLYT_OFFSET + 12 : _BRLYT_OFFSET + 16] = pack("<I", _PRELOADER_DATA_OFFSET)
    header[_BRLYT_OFFSET + 16 : _BRLYT_OFFSET + 20] = pack("<I", image_size)
    header[_BRLYT_OFFSET + 20 : _BRLYT_OFFSET + 24] = pack("<I", _BRLYT_MAGIC)
    header[_BRLYT_OFFSET + 24 : _BRLYT_OFFSET + 28] = pack("<I", _BRLYT_TYPE_EMMC)
    header[_BRLYT_OFFSET + 28 : _BRLYT_OFFSET + 32] = pack("<I", _PRELOADER_DATA_OFFSET)
    header[_BRLYT_OFFSET + 32 : _BRLYT_OFFSET + 36] = pack("<I", image_size)
    header[_BRLYT_OFFSET + 36 : _BRLYT_OFFSET + 40] = pack("<I", 1)


def _wrap_preloader_for_raw_boot(
    file_path: Path, storage_type: str = "emmc", log_cb=None
) -> Path:
    """If preloader is in raw MMM\\x01 format, prepend EMMC_BOOT / BRLYT header and return temp file.

    If it already has a boot header or cannot identify magic, return the original file.
    """
    try:
        with open(file_path, "rb") as f:
            data = f.read()

        if (
            data[:9] == b"EMMC_BOOT"
            or data[:8] == b"UFS_BOOT"
            or data[:10] == b"SDMMC_BOOT"
            or data[:5] == b"BRLYT"
            or data[_BRLYT_OFFSET : _BRLYT_OFFSET + 5] == b"BRLYT"
        ):
            return file_path

        magic_pos = data.find(_PRELOADER_MAGIC)
        if magic_pos < 0:
            return file_path

        raw_data = data[magic_pos:]
        raw_size = len(raw_data)

        header = bytearray(_BOOT_HEADER_SIZE)
        _fill_boot_magic(header, storage_type)
        _fill_brlyt_header(header, raw_size)

        combined = bytes(header) + raw_data

        import tempfile

        fd, tmp_name = tempfile.mkstemp(prefix="preloader_wrapped_", suffix=".bin")
        with os.fdopen(fd, "wb") as out_f:
            out_f.write(combined)
        if log_cb:
            log_cb("Generated complete BRLYT boot header for raw preloader")
        return Path(tmp_name)
    except Exception as e:
        if log_cb:
            log_cb(f"Preloader wrap check note: {e}")
        return file_path


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
    if package.is_dir():
        return package
    return package.parent / f".{package.stem}_extracted"


def completed_extract_dir(package_path):
    """Return the extract dir for ``package_path`` when a previous run already
    finished extracting it, else ``""`` (so callers can skip re-extraction —
    the firmware is already on disk before a backend switch / MTKClient run)."""
    if not package_path:
        return ""
    p = Path(package_path)
    if p.is_dir():
        return str(p)
    d = compute_extract_dir(package_path)
    return str(d) if _is_extract_complete(d) else ""


def _is_extract_complete(extract_dir):
    d = Path(extract_dir)
    if d.is_dir() and not d.name.endswith("_extracted"):
        return True
    return d.exists() and (d / EXTRACT_COMPLETE_MARKER).exists()


def _mark_extract_complete(extract_dir):
    try:
        (extract_dir / EXTRACT_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    except Exception:
        pass


def prune_extracted_cache(keep_package_path: Optional[str] = None) -> list:
    """Retain only the most recently downloaded software package extracted directory,
    removing older extracted directories in downloads_dir() to save disk space.

    Returns a list of directory paths that were removed.
    """
    removed = []
    try:
        from .downloads import downloads_dir
        dd = downloads_dir()
        if not dd.is_dir():
            return removed
        keep_stem = Path(keep_package_path).stem if keep_package_path else ""
        keep_dir_name = f".{keep_stem}_extracted" if keep_stem else ""

        for item in list(dd.iterdir()):
            if item.is_dir() and item.name.startswith(".") and item.name.endswith("_extracted"):
                if item.name != keep_dir_name:
                    logger.info("Pruning old cached firmware extraction: %s", item.name)
                    try:
                        shutil.rmtree(item, ignore_errors=True)
                        removed.append(str(item))
                    except Exception as e:
                        logger.warning("Could not remove old extract dir %s: %s", item, e)
    except Exception as e:
        logger.warning("Error pruning extracted cache: %s", e)
    return removed


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
            # Validate scatter discovery; surfaces MULTIPLE_SCATTERS early
            sc = _find_scatter(extract_dir)
            if sc is None:
                self.finished.emit(False, "", "NO_SCATTER_FILE")
                return
            self.finished.emit(True, str(extract_dir), "")
        except ScatterDiscoveryError as sde:
            self.finished.emit(False, "", str(sde))
        except Exception as e:
            logger.error("Extraction failed: %s", e, exc_info=True)
            self.finished.emit(False, "", str(e))

    def _extract(self, package_path):
        p = Path(package_path)
        if p.is_dir():
            self.progress.emit(100)
            return p
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
class ScatterDiscoveryError(RuntimeError):
    """Raised when multiple scatter files/packages are found in a selected folder/archive."""
    def __init__(self, message, scatters=None):
        super().__init__(message)
        self.scatters = list(scatters or [])


def find_scatter_files(directory):
    """Find all scatter files in directory and nested subfolders, ignoring system/cache folders."""
    d = Path(directory)
    if not d.exists():
        return []
    ignored = {"__MACOSX", ".git", ".idea", ".venv", ".venv-build", ".cache", "__pycache__"}
    scatters = []
    for root, dirs, files in os.walk(str(d)):
        dirs[:] = [name for name in dirs if name not in ignored and not name.startswith(".")]
        for f in files:
            fl = f.lower()
            if "scatter" in fl and fl.endswith(".txt"):
                scatters.append(Path(root) / f)
    return scatters


def _find_scatter(directory, allow_raise=True):
    """Find the scatter file in a directory or its nested subfolder.
    If multiple scatter files are found across different packages/folders,
    raises ScatterDiscoveryError if allow_raise is True, else returns None.
    """
    scatters = find_scatter_files(directory)
    if not scatters:
        return None
    if len(scatters) == 1:
        return scatters[0]

    if allow_raise:
        raise ScatterDiscoveryError(
            "MULTIPLE_SCATTERS: Multiple firmware packages or scatter files were found. "
            "Please select only one software archive or folder at once.",
            scatters=scatters,
        )
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


def sp_flash_tool_console_args(scatter_arg, da_arg, auth_file="", log=None):
    """Console arguments for flash_tool, carrying an auth file when one is set.

    SP Flash Tool has no ``--auth`` switch, so an authentication file is only
    reachable through the console configuration file (``-i``). Without one the
    plain command line is used, exactly as before — auth files are optional and
    most targets are not secure-booted.
    """
    auth = str(auth_file or "").strip()
    if auth:
        from . import sp_console_config

        if not sp_console_config.auth_file_usable(auth):
            if log:
                log(tr("sp_auth_missing_file").format(path=auth))
        else:
            try:
                config = sp_console_config.write_console_config(
                    scatter_arg, da_arg, auth
                )
            except sp_console_config.SpConfigError as e:
                if log:
                    log(tr(f"sp_config_{e.reason}"))
            except OSError as e:
                logger.warning("Could not write the SP console configuration: %s", e)
                if log:
                    log(tr("sp_config_write_failed"))
            else:
                if log:
                    log(tr("sp_auth_loaded").format(path=auth))
                return ["-r", "-i", str(config)]
    return [
        "-c", "format-download",
        "-s", scatter_arg,
        "-d", da_arg,
        "-t", "without",
        "-r",
    ]


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


def _to_int_safe(val, default=0):
    try:
        return int(str(val).strip(), 0)
    except Exception:
        return default


def _parse_scatter_entries(scatter_path):
    """Parse all partition entries from a scatter file, returning a list of dicts with:
    - name: partition name (str)
    - file_name: scatter file_name field (str)
    - is_download: bool
    - region: storage region (str, e.g. EMMC_BOOT_1, EMMC_USER)
    - linear_start_addr: int
    - physical_start_addr: int
    - partition_size: int
    - offset: calculated byte offset within the target partition (int)
    """
    try:
        content = Path(scatter_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    raw_parts = []
    current = {}
    for raw in content.split("\n"):
        line = raw.strip()
        if line.startswith("- partition_index:"):
            if current:
                raw_parts.append(current)
            current = {}
            continue
        if ":" in line and not line.startswith("#"):
            key, _, value = line.partition(":")
            current[key.strip().lstrip("- ")] = value.strip()
    if current:
        raw_parts.append(current)

    # Determine base linear address for EMMC_USER (typically the linear address
    # of the first EMMC_USER partition, usually 0x1400000 on MT6572/MT6582)
    user_base = None
    has_non_zero_phys = False
    for p in raw_parts:
        region = p.get("region", "").upper()
        if region == "EMMC_USER":
            lin = _to_int_safe(p.get("linear_start_addr", 0))
            phys = _to_int_safe(p.get("physical_start_addr", 0))
            if user_base is None:
                user_base = lin
            if phys > 0:
                has_non_zero_phys = True
    if user_base is None:
        user_base = 0x1400000

    entries = []
    for p in raw_parts:
        pname = p.get("partition_name", "")
        if not pname:
            continue
        fname = p.get("file_name", "NONE")
        region = p.get("region", "EMMC_USER").upper()
        lin = _to_int_safe(p.get("linear_start_addr", 0))
        phys = _to_int_safe(p.get("physical_start_addr", 0))
        size = _to_int_safe(p.get("partition_size", 0))
        is_dl = p.get("is_download", "").lower() == "true"

        if region in ("EMMC_BOOT_1", "EMMC_BOOT_2"):
            offset = phys
        elif has_non_zero_phys:
            offset = phys
        else:
            offset = max(0, lin - user_base)

        entries.append({
            "name": pname,
            "file_name": fname,
            "is_download": is_dl,
            "region": region,
            "linear_start_addr": lin,
            "physical_start_addr": phys,
            "partition_size": size,
            "offset": offset,
        })
    return entries


# ---------------------------------------------------------------------------
# MTKClient session helpers
# ---------------------------------------------------------------------------
class _MtkMessageBridge:
    """Adapter for mtkclient's ``config.gui`` hook.

    mtkclient's ``logsetup`` routes every phase message to ``config.gui.emit``
    when a gui object is set, and logs to stdout otherwise. Feeding those
    messages into Diagnostics is what makes a slow/stalled DA stage visible
    ("Uploading legacy stage 1...", "Successfully uploaded stage 2", ...)
    instead of the app appearing frozen after its one handshake message.
    """

    def __init__(self, callback):
        self._cb = callback

    def emit(self, message):
        self._cb(str(message))


class _StallWatcher(threading.Thread):
    """Reports when the MTKClient backend stops hearing from the device.

    mtkclient's USB reads are blocking, so a target that goes silent mid-DA
    (the usual cause: the device was not fully powered off before connecting)
    leaves the worker parked with no way to cancel it. This watcher only
    *reports* the stall; the bounded USB timeout is what eventually unblocks
    and fails the run.
    """

    def __init__(self, on_stall, timeout=MTK_STALL_WARN_SECONDS, interval=2.0):
        super().__init__(daemon=True, name="MtkStallWatcher")
        self._on_stall = on_stall
        self._timeout = timeout
        self._interval = interval
        self._stop = threading.Event()
        self._last = time.time()
        self._warned = False

    def activity(self):
        """Register backend chatter; clears a pending stall warning."""
        self._last = time.time()
        self._warned = False

    def stop(self):
        self._stop.set()

    def run(self):
        while not self._stop.wait(self._interval):
            idle = time.time() - self._last
            if idle >= self._timeout and not self._warned:
                self._warned = True
                try:
                    self._on_stall(idle)
                except Exception:
                    logger.debug("MTK stall callback failed", exc_info=True)


# ---------------------------------------------------------------------------
# Flash worker
# ---------------------------------------------------------------------------
class FlashWorker(QThread):
    """Background flash: extract → validate → dispatch to the platform backend."""

    step_changed = Signal(str)
    progress = Signal(int)
    action_changed = Signal(str)
    log_message = Signal(str)
    finished = Signal(bool, str)  # ok, error_code

    def __init__(self, package_path, pre_extracted_dir="", method="auto", model="",
                 parent=None, device_loss_hold=None):
        super().__init__(parent)
        self.package_path = package_path
        self.pre_extracted_dir = pre_extracted_dir
        self.model = (model or "").strip()
        # Flash backend method: "auto" (platform default), "sp" (SP Flash
        # Tool), or "mtk" (MTKClient). The user can pick manually; on macOS
        # "sp" is unavailable and always falls back to MTKClient.
        self.method = (method or "auto").lower()
        # Suppresses the USB monitor's "player unplugged" handling while this
        # worker deliberately restarts the target (preloader -> BROM).
        self._device_loss_hold = device_loss_hold
        self._cancelled = False
        self._process = None
        self._sp_progress_hwm = 0
        self._sp_total_bytes = 0
        self._sp_completed_bytes = 0
        self._sp_last_part_total = 0
        self._sp_last_sent = 0
        self._sp_mismatch = False
        self._sp_err_code = None
        self._start_time = time.time()
        # Normalised SP Flash Tool console lines: used to skip the same line
        # when it is forwarded from the tool's internal QT_FLASH_TOOL.log.
        self._sp_stdout_seen: set[str] = set()
        # Tag for output the tooling prints itself ("SP"/"MTK"/"TOOL").
        self._tool_tag = "TOOL"

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
        from .diagnostics import capture_tool_output

        # Everything the backends print themselves (MTKClient runs in process,
        # so its USB/DA traces never reach the UI otherwise) is mirrored into
        # the diagnostics log for as long as the install runs.
        with capture_tool_output(self._log, tag=lambda: self._tool_tag):
            try:
                self._do_flash()
            except BaseException as e:
                # BaseException (not just Exception): the in-process mtkclient
                # calls sys.exit()/SystemExit on several DA failure paths;
                # surface them as a clean failure here, never crash the app.
                logger.error("Flash pipeline crashed: %s", e, exc_info=True)
                # Also surface the real cause in Diagnostics so frozen builds
                # are diagnosable instead of a bare INTERNAL_ERROR.
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

        try:
            scatter_file, missing = self._validate_extract(extract_dir)
        except ScatterDiscoveryError as e:
            self._log(f"Error: {e}")
            self.finished.emit(False, str(e))
            return

        if (scatter_file is None or missing) and reused and not Path(self.package_path).is_dir():
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
            try:
                scatter_file, missing = self._validate_extract(extract_dir)
            except ScatterDiscoveryError as e:
                self._log(f"Error: {e}")
                self.finished.emit(False, str(e))
                return

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

        try:
            from . import device_tracking
            from .config import detect_model_and_type_from_name
            from .sp_flash_gui import update_sp_history_ini

            det_m, _ = detect_model_and_type_from_name(self.package_path)
            detected_model = (
                self.model
                or det_m
                or ("A5" if "a5" in str(self.package_path).lower() else ("Y2" if "6582" in (platform or "") else ("Y1" if "6572" in (platform or "") else "")))
            )
            device_tracking.record_latest_package(
                model=detected_model,
                software_name=Path(self.package_path).stem if self.package_path else "",
                tag_name="",
                package_path=str(self.package_path),
                extract_dir=str(Path(extract_dir).resolve()),
                scatter_path=str(Path(scatter_file).resolve()),
            )
            update_sp_history_ini(
                scatter_path=Path(scatter_file).resolve(),
                extract_dir=Path(extract_dir).resolve(),
                model=detected_model,
            )
        except Exception as e:
            logger.debug("Could not update SP history.ini during flash: %s", e)

        self._dispatch_backend(extract_dir, scatter_file)

    def _dispatch_backend(self, extract_dir, scatter_file):
        """Choose SP Flash Tool vs MTKClient for this run.

        ``method`` is the user's explicit choice ("sp" / "mtk") or "auto".
        Auto leans towards SP Flash Tool on Windows and Linux (the
        InniUpdaterChin default) with MTKClient as the fallback; macOS has no
        SP Flash Tool build, so MTKClient is the only backend there.
        """
        # "mtk_mac" (simulated macOS) is the same backend as "mtk"; only the
        # platform-level behaviour shown to the user differs.
        method = backend_method(self.method)
        if method == "mtk":
            self._log("Using MTKClient method (user selected).")
            self._flash_via_mtkclient(extract_dir, scatter_file)
            return
        if method == "sp" and IS_MAC:
            reason = (
                "SP Flash Tool is not available on macOS; using MTKClient."
                if not paths.SIMULATE_MACOS
                else "Simulated macOS mode (--simulate-macos): using MTKClient."
            )
            self._log(reason)
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
                if (self.method == "auto" or method == "auto") and paths.find_sp_flash_tool() is None:
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
        # Raw output printed while this backend runs is flash_tool's.
        self._tool_tag = "SP"
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
            linux_sp_flash.fix_option_ini(sp_dir)
            flash_tool_exe = sp_dir / linux_sp_flash.FLASH_TOOL_LINUX_BIN
            da_file = sp_dir / "MTK_AllInOne_DA.bin"
            log_root = sp_dir / linux_sp_flash.LOG_DIR_NAME
            log_root.mkdir(parents=True, exist_ok=True)
            env = linux_sp_flash.process_env(sp_dir)
            creationflags = 0
            scatter_arg = str(scatter_file)
            da_arg = str(da_file)

        try:
            scatter_images = _parse_scatter(scatter_file)
            self._sp_total_bytes = sum(fp.stat().st_size for _, fp in scatter_images if fp.exists())
        except Exception:
            self._sp_total_bytes = 0
        self._sp_completed_bytes = 0
        self._sp_last_part_total = 0
        self._sp_last_sent = 0
        try:
            from .sp_flash_gui import update_sp_history_ini
            update_sp_history_ini(
                sp_dir=sp_dir,
                scatter_path=scatter_file,
                extract_dir=scatter_file.parent,
            )
        except Exception as e:
            logger.debug("Could not update SP history.ini before flash_tool: %s", e)

        from . import device_tracking

        auth_file = device_tracking.sp_auth_file()

        if IS_WINDOWS:
            cmd = [
                str(flash_tool_exe),
                *sp_flash_tool_console_args(
                    scatter_arg, da_arg, auth_file, self._log
                ),
            ]
            self.step_changed.emit(STEP_WAITING)
            self.progress.emit(10)
            self.action_changed.emit(tr("action_searching"))
            self._log("Launching SP Flash Tool (console mode, searching USB)...")
            self._log("Keep the device unplugged until the connect prompt appears.")
            guardian = None
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
        else:
            cmd_args = sp_flash_tool_console_args(
                scatter_arg, da_arg, auth_file, self._log
            )
            self._log("Preparing SP Flash Tool environment (Linux)...")
            guardian = None
            if os.name != "nt" and sys.platform.startswith("linux"):
                try:
                    guardian = linux_sp_flash.TtyAccessGuardian()
                    guardian.start()
                except Exception:
                    guardian = None

            self._process = linux_sp_flash.launch_linux_flash_tool(
                stage=sp_dir,
                cmd_args=cmd_args,
                cwd=sp_dir,
                env=env,
                log_cb=self._log,
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
                    normalised = _normalise_tool_line(line)
                    if normalised:
                        self._sp_stdout_seen.add(normalised)
                        if len(self._sp_stdout_seen) > 4000:
                            self._sp_stdout_seen.clear()
                if self._cancelled:
                    try:
                        self._process.terminate()
                    except Exception:
                        pass
                    stop_monitor.set()
                    if guardian:
                        guardian.stop()
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
            if guardian:
                guardian.stop()
            if hasattr(self._process, "_pty_master_fd"):
                try:
                    os.close(self._process._pty_master_fd)
                except Exception:
                    pass

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
            self.action_changed.emit(tr("action_searching"))
        elif "brom connected" in low:
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
            self.action_changed.emit(tr("action_brom_detected"))
        elif "of da has been sent" in low:
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.progress.emit(15)
            self.action_changed.emit(tr("action_downloading_da"))
        elif "of bootloader has been sent" in low:
            self.step_changed.emit(STEP_DOWNLOAD_BL)
            self.progress.emit(18)
            self.action_changed.emit(tr("action_downloading_bootloader"))
        elif "format succeeded" in low:
            self.progress.emit(20)
            self.action_changed.emit(tr("action_format_succeeded"))
        elif "of image data has been sent" in low:
            self.step_changed.emit(STEP_WRITE)
            self._update_sp_image_progress(line)
        elif "download ok" in low:
            self.step_changed.emit(STEP_DONE)
            self.progress.emit(100)
            self.action_changed.emit(tr("action_flash_complete"))
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
            m_simple = re.search(r"(\d+)%\s+of image data", line)
            if m_simple:
                pct = int(m_simple.group(1))
                mapped = 20 + int(pct * 0.75)
                if mapped > self._sp_progress_hwm:
                    self._sp_progress_hwm = mapped
                    self.progress.emit(min(95, mapped))
                self.action_changed.emit(tr("action_writing_image_fmt").format(pct=pct))
            return
        pct = int(m.group(1))
        sent = _parse_sp_size(m.group(2))
        total = _parse_sp_size(m.group(3))

        if total != self._sp_last_part_total or sent < self._sp_last_sent:
            if self._sp_last_part_total > 0:
                self._sp_completed_bytes += self._sp_last_part_total
            self._sp_last_part_total = total
        self._sp_last_sent = sent

        if self._sp_total_bytes > 0:
            current_done = self._sp_completed_bytes + sent
            ratio = min(1.0, max(0.0, current_done / self._sp_total_bytes))
            mapped = 20 + int(ratio * 75)
        else:
            mapped = 20 + int(pct * 0.75)

        if mapped > self._sp_progress_hwm:
            self._sp_progress_hwm = mapped
            self.progress.emit(min(95, mapped))
        self.action_changed.emit(tr("action_writing_image_fmt").format(pct=pct))

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
            forwarded = 0
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
                        # Forward the internal log itself, minus lines the
                        # console stream already reported, so the diagnostics
                        # window holds everything flash_tool emitted.
                        if forwarded >= _SP_QT_LOG_MAX_LINES:
                            continue
                        normalised = _normalise_tool_line(ln)
                        if not normalised or normalised in self._sp_stdout_seen:
                            continue
                        forwarded += 1
                        self._log(f"[QT] {ln.strip()}")
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

    # -- MTKClient session helpers ------------------------------------------
    def _restart_in_brom(self, mtk, preloader_path, directory, on_message, timeout=None):
        """Restart a preloader-mode target into BROM and re-handshake it.

        In preloader mode ``da_handler.configure_da()`` sets
        ``daloader.patch = False``, so the stage-2 DA is uploaded unpatched and
        never answers ``read_flash_info()``: the log stops at "Successfully
        uploaded stage 2" and no partition is written. Arming the USB-DL flag
        and resetting the target brings it up as BROM (0x0003), where the DA
        runs normally. Returns the new session, or ``None`` when the restart
        did not land — the caller then falls back to a plain preloader-mode
        session, never worse than the behaviour before the restart existed.
        """
        from . import mtk_api  # local: mtkclient is imported lazily (frozen builds)

        self._log(
            "Device answered in preloader mode; restarting it into BROM mode "
            "so the download agent can run..."
        )
        self.action_changed.emit(tr("action_restart_brom"))
        if self._device_loss_hold is not None:
            # The target has to re-enumerate (a few seconds); the monitor must
            # not report that as the player being unplugged mid-flash.
            try:
                self._device_loss_hold(MTK_DEVICE_LOSS_GRACE_SECONDS)
            except Exception:
                pass
        try:
            mtk_api.arm_brom_boot(mtk)
            mtk_api.trigger_reset(mtk)
        except BaseException as e:
            self._log(f"Could not restart the device into BROM mode: {type(e).__name__}: {e}")
            return None
        mtk_api.close_port(mtk)

        if not _wait_for_mtk_mode(MTK_BROM_PID, timeout or MTK_BROM_WAIT_SECONDS):
            self._log(
                f"The device did not come back in BROM mode (USB "
                f"{MTK_DEVICE_VID:04X}:{MTK_BROM_PID:04X}) within "
                f"{int(timeout or MTK_BROM_WAIT_SECONDS)}s; continuing in preloader mode."
            )
            return None
        self._log("Device is in BROM mode - reconnecting...")

        try:
            session = mtk_api.init(loader=None, preloader=preloader_path)
        except BaseException as e:
            self._log(f"mtkclient init failed: {type(e).__name__}: {e}")
            return None
        if hasattr(session, "config"):
            session.config.gui = _MtkMessageBridge(on_message)
        session, _da = mtk_api.handshake(session, directory=str(directory))
        if session is None:
            self._log("Handshake failed after the BROM restart; continuing in preloader mode.")
            return None
        return session

    def _wait_for_retry(self, attempt):
        """Auto-recovery: wait for the target, then let the caller retry.

        The DA stage-2 failure leaves the target wedged until it is reset by
        hand, so this waits for it to re-appear in flash mode first (the
        player may reboot itself). Returns True when another attempt should
        run; the caller falls back to the retry dialog when it returns False.
        """
        if self._cancelled:
            return False
        self.action_changed.emit(tr("action_recover_powercycle"))
        self._log(
            f"Attempt {attempt} failed. Recovering: power-cycle the player "
            "(hold power ~10s) and reconnect it in flash mode."
        )
        if _wait_for_mtk_mode(
            (MTK_PRELOADER_PID, MTK_BROM_PID), MTK_RECOVERY_WAIT_SECONDS
        ):
            return True
        self._log(
            "The player has not come back yet. Reconnect it in flash mode "
            "and start the flash again."
        )
        return False

    # -- macOS/Linux backend: mtkclient -------------------------------------
    def _flash_via_mtkclient(self, extract_dir, scatter_file):
        """Run MTKClient in an isolated subprocess (--flash-cli) for memory/libusb safety."""
        if (
            os.environ.get("UPDATER_MTK_INPROCESS") == "1"
            or not getattr(self, "_use_subprocess_cli", True)
            or (len(sys.argv) > 0 and "smoke_test" in sys.argv[0])
            or "pytest" in sys.modules
        ):
            self._flash_via_mtkclient_core(extract_dir, scatter_file)
            return

        self._tool_tag = "MTK"
        self.step_changed.emit(STEP_WAITING)
        self.progress.emit(8)
        self.action_changed.emit(tr("action_init_mtkclient"))
        self._log(tr("action_init_mtkclient"))

        if self._cancelled:
            self.finished.emit(False, "USER_CANCELLED")
            return

        cmd = [sys.executable]
        if not getattr(sys, "frozen", False):
            try:
                from . import app as app_main
                main_file = getattr(app_main, "__file__", None)
                if main_file:
                    cmd.append(str(Path(main_file).resolve()))
                else:
                    cmd.extend(["-m", "src.app"])
            except Exception:
                cmd.extend(["-m", "src.app"])
        platform_name = str(getattr(self, "package_platform", "") or "")
        cmd.extend([
            "--flash-cli",
            str(extract_dir),
            str(scatter_file),
            platform_name,
            str(self.package_path or ""),
            str(self.model or ""),
        ])

        final_ok = False
        final_msg = ""
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in iter(self._process.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                if line.startswith("[PROGRESS] "):
                    try:
                        self.progress.emit(int(line[len("[PROGRESS] "):]))
                    except ValueError:
                        pass
                elif line.startswith("[STEP] "):
                    self.step_changed.emit(line[len("[STEP] "):])
                elif line.startswith("[ACTION] "):
                    self.action_changed.emit(line[len("[ACTION] "):])
                elif line.startswith("[LOG] "):
                    self.log_message.emit(line[len("[LOG] "):])
                elif line.startswith("[RESULT] "):
                    parts = line[len("[RESULT] "):].split(" ", 1)
                    final_ok = bool(int(parts[0]))
                    final_msg = parts[1] if len(parts) > 1 else ""
                else:
                    self.log_message.emit(line)

            self._process.wait()
            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            if self._process.returncode == 0:
                self.finished.emit(True, "")
            else:
                self.finished.emit(final_ok, final_msg or f"MTK_FLASH_FAILED ({self._process.returncode})")
        except (subprocess.SubprocessError, OSError) as e:
            if getattr(self, "_process", None) and self._process.poll() is None:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=2)
                except (subprocess.TimeoutExpired, OSError):
                    try:
                        self._process.kill()
                    except OSError:
                        pass
            self._log(f"Failed to start flash CLI: {e}")
            self.finished.emit(False, "CLI_START_FAILED")

    def _flash_via_mtkclient_core(self, extract_dir, scatter_file, platform=""):
        # Raw output printed while this backend runs comes from MTKClient.
        self._tool_tag = "MTK"
        self.step_changed.emit(STEP_WAITING)
        self.progress.emit(10)
        self.action_changed.emit(tr("action_init_mtkclient"))
        self._log(tr("action_init_mtkclient"))
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

        # Locate preloader if present in package
        preloader_path = None
        for pname, fpath in image_files:
            if pname.upper() == "PRELOADER":
                p = Path(fpath)
                if p.exists():
                    preloader_path = str(p)
                break

        if not preloader_path and os.path.isdir(str(extract_dir)):
            for f in Path(extract_dir).glob("preloader*.bin"):
                if f.is_file():
                    preloader_path = str(f)
                    break

        if preloader_path:
            self._log(f"Preloader image: {os.path.basename(preloader_path)}")
        else:
            self._log("No preloader image found; using built-in EMI")

        self.step_changed.emit(STEP_WAITING)
        self.progress.emit(10)
        self.action_changed.emit(tr("action_wait_mtk"))
        self._log("Waiting for MTK device... Power off the device and connect USB.")

        # Surface mtkclient's own phase messages and flag a device that stops
        # answering mid-DA instead of letting the UI look frozen. Repeats are
        # collapsed: a silent target makes mtkclient emit the same retry line
        # once per USB timeout.
        repeated = {"text": None, "count": 0}

        def _on_mtk_message(message):
            watcher.activity()
            text = str(message).strip()
            if not text:
                return
            if text == repeated["text"]:
                repeated["count"] += 1
                return
            if repeated["count"] > 1:
                self._log(f"  (repeated {repeated['count']}x)")
            repeated["text"], repeated["count"] = text, 1
            self._log(text)

        def _on_mtk_stall(idle):
            seconds = int(idle)
            self._log(
                f"No response from the device for {seconds}s while configuring the DA."
            )
            self.action_changed.emit(tr("action_da_stall"))

        watcher = _StallWatcher(_on_mtk_stall)

        def _on_connected(mtk):
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
            # Report the mode mtkclient actually found: PID 0x2000 (and the
            # BL-version probe) is preloader mode, only 0x0003 is BROM, and
            # the difference is what the user has to act on.
            if mtk_api.is_brom(mtk):
                msg = tr("action_detected_brom")
            else:
                msg = tr("action_detected_preloader")
            self.action_changed.emit(msg)
            self._log(msg)

        def _clear_mtk_state(*dirs):
            for base in dirs:
                if not base:
                    continue
                try:
                    b_path = Path(base)
                    for name in (".state", "hwparam.json"):
                        p = b_path / name
                        if p.exists():
                            p.unlink()
                except Exception:
                    pass

        def _open_session():
            """One session bring-up: init, handshake, and BROM mode if needed.

            A target that answers in preloader mode will accept the stage-2 DA
            upload and then never answer it, so nothing is ever written (see
            ``_restart_in_brom``). Restarting it into BROM first is what makes
            the download agent actually run. Returns the session or ``None``.
            """
            _clear_mtk_state(extract_dir, Path.cwd())
            try:
                session = mtk_api.init(loader=None, preloader=preloader_path)
            except BaseException as e:
                self._log(f"mtkclient init failed: {type(e).__name__}: {e}")
                return None
            if hasattr(session, "config"):
                session.config.gui = _MtkMessageBridge(_on_mtk_message)
            session, _da = mtk_api.handshake(session, directory=str(extract_dir))
            if session is None:
                return None
            _on_connected(session)
            if MTK_RESTART_IN_BROM and not mtk_api.is_brom(session):
                restarted = self._restart_in_brom(
                    session, preloader_path, extract_dir, _on_mtk_message
                )
                if restarted is not None:
                    session = restarted
                    _on_connected(session)
                else:
                    # The restart released the port, so the session above is
                    # unusable: open a plain preloader-mode one and let the DA
                    # upload run exactly as it did before the restart existed.
                    self._log("Falling back to a preloader-mode session...")
                    try:
                        session = mtk_api.init(loader=None, preloader=preloader_path)
                    except BaseException as e:
                        self._log(f"mtkclient init failed: {type(e).__name__}: {e}")
                        return None
                    if hasattr(session, "config"):
                        session.config.gui = _MtkMessageBridge(_on_mtk_message)
                    session, _da = mtk_api.handshake(
                        session, directory=str(extract_dir)
                    )
                    if session is None:
                        return None
            return session

        class _ProgressEmitter:
            def __init__(self, callback):
                self._cb = callback

            def emit(self, pos):
                self._cb(pos)

            def __call__(self, pos):
                self._cb(pos)

        watcher.start()
        attempt = 0
        try:
            while True:
                attempt += 1
                mtk = _open_session()
                da_handler = None
                if mtk is not None:
                    try:
                        self.step_changed.emit(STEP_DOWNLOAD_DA)
                        self.action_changed.emit(tr("step_download_da"))
                        self.progress.emit(14)

                        def _on_da_progress(pos):
                            if self._cancelled:
                                return
                            val = pos if pos <= 1.0 else (pos / 100.0)
                            mapped = 14 + int(min(1.0, max(0.0, val)) * 4)
                            self.progress.emit(min(18, max(14, mapped)))

                        if hasattr(mtk, "config"):
                            mtk.config.guiprogress = _ProgressEmitter(_on_da_progress)
                        mtk, da_handler = mtk_api.attach(mtk, directory=str(extract_dir))
                    except BaseException as e:
                        # BaseException: mtkclient raises SystemExit on DA
                        # upload failure.
                        self._log(f"Device connection failed: {e}\n{traceback.format_exc()}")
                        mtk, da_handler = None, None
                if mtk is not None and da_handler is not None:
                    break
                if attempt >= MTK_DA_ATTEMPTS:
                    break
                self._log("The download agent did not come up.")
                self.action_changed.emit(tr("action_da_wait_back"))
                if not self._wait_for_retry(attempt):
                    break
        except BaseException as e:
            self._log(f"Device connection failed: {e}\n{traceback.format_exc()}")
            watcher.stop()
            self.finished.emit(False, "CONNECTION_FAILED")
            return
        if mtk is None or da_handler is None:
            self._log("Device connection returned None")
            self._log(
                "The download agent did not come up. Power the device off "
                "completely (hold power ~10s), reconnect in flash mode, and retry."
            )
            watcher.stop()
            self.finished.emit(False, "CONNECTION_FAILED")
            return

        self.step_changed.emit(STEP_DOWNLOAD_BL)
        self.progress.emit(18)
        self.action_changed.emit(tr("action_device_connected"))

        total = len(image_files)

        scatter_entries_map = {
            e["name"].lower(): e for e in _parse_scatter_entries(scatter_file)
        }

        # Sequence: partition table (MBR/EBR) first, systems next, preloader last for safety
        def _partition_sort_key(item):
            name = item[0].lower()
            if name in ("mbr", "pgpt"):
                return 0
            if name.startswith("ebr"):
                return 1
            if name in ("uboot", "lk"):
                return 10
            if name in ("boot", "bootimg"):
                return 20
            if name in ("recovery", "secro", "logo"):
                return 30
            if name in ("userdata", "cache"):
                return 80
            if name in ("system", "android", "innios"):
                return 90
            if name == "preloader":
                return 999  # Flashed last to keep target recoverable
            return 50

        ordered_images = sorted(image_files, key=_partition_sort_key)
        total_bytes = sum(file_path.stat().st_size for _, file_path in ordered_images if file_path.exists())
        completed_bytes = 0

        def on_mtk_write_progress(pos):
            if self._cancelled:
                return
            current_done = completed_bytes + pos
            if total_bytes > 0:
                ratio = min(1.0, max(0.0, current_done / total_bytes))
                mapped = 20 + int(ratio * 75)
                self.progress.emit(min(95, max(20, mapped)))

        if hasattr(mtk, "config"):
            mtk.config.guiprogress = _ProgressEmitter(on_mtk_write_progress)

        try:
            for i, (part_name, file_path) in enumerate(ordered_images):
                if self._cancelled:
                    self.finished.emit(False, "USER_CANCELLED")
                    return
                if i == 0:
                    # The install is considered started once the DA actually
                    # writes images (the reference updater derives install
                    # progress from image writes, not from the handshake).
                    self.step_changed.emit(STEP_WRITE)
                    self.progress.emit(20)
                    self.action_changed.emit(tr("action_install_in_progress"))
                    self._log(tr("log_install_writing"))
                self._log(tr("action_writing_part_fmt").format(
                    part=part_name, i=i + 1, total=total, file=file_path.name,
                ))
                self.action_changed.emit(tr("action_writing_part_fmt").format(
                    part=part_name, i=i + 1, total=total, file=file_path.name,
                ))

                entry = scatter_entries_map.get(part_name.lower())
                region = entry.get("region", "EMMC_USER") if entry else "EMMC_USER"
                offset = entry.get("offset") if entry else None

                is_preloader = part_name.lower() == "preloader"
                active_file = file_path
                temp_wrapped = None
                temp_unsparse = None

                # 1. Unpack Android sparse images in-process
                if _is_android_sparse(active_file):
                    self._log(tr("log_sparse_fmt").format(part=part_name))
                    self.action_changed.emit(tr("action_unpacking_fmt").format(part=part_name))
                    unsparse_dir = Path(extract_dir) / ".unsparse_cache"
                    temp_unsparse, _ = _convert_sparse_to_raw(active_file, unsparse_dir)
                    active_file = temp_unsparse

                # 2. Detect & strip signed image wrappers (head=64B, tail=236B)
                cur_fsize = active_file.stat().st_size if active_file.exists() else 0
                signed_range = _signed_image_payload_range(active_file, cur_fsize) if cur_fsize > 0 else None
                file_off = 0
                write_len = cur_fsize
                if signed_range is not None:
                    file_off, write_len = signed_range
                    self._log(
                        f"  Stripping signed wrapper: offset=0x{file_off:X}, payload_len=0x{write_len:X}"
                    )

                if is_preloader:
                    temp_wrapped = _wrap_preloader_for_raw_boot(
                        active_file, storage_type="emmc", log_cb=self._log
                    )
                    if temp_wrapped != active_file:
                        active_file = temp_wrapped
                        file_off = 0
                        write_len = active_file.stat().st_size if active_file.exists() else 0
                    target_parts = self._resolve_preloader_target_parts(mtk, normalized_region=region)
                else:
                    if region == "EMMC_BOOT_1":
                        target_parts = ("boot1",)
                    elif region == "EMMC_BOOT_2":
                        target_parts = ("boot2",)
                    else:
                        target_parts = ("user",)

                try:
                    for target_part in target_parts:
                        self._write_mtk_partition(
                            da_handler,
                            mtk,
                            part_name,
                            str(active_file),
                            target_part,
                            offset=offset,
                            file_offset=file_off,
                            write_length=write_len,
                        )
                except BaseException as e:
                    # BaseException: mtkclient may sys.exit() on DA errors; keep it
                    # a clean per-partition failure, never an app crash.
                    self._log(f"Writing {part_name} failed: {e}\n{traceback.format_exc()}")
                    self.finished.emit(False, "WRITE_FAILED")
                    return
                finally:
                    if temp_unsparse and temp_unsparse.exists():
                        try:
                            temp_unsparse.unlink(missing_ok=True)
                        except Exception:
                            pass
                    if temp_wrapped and temp_wrapped != file_path and temp_wrapped.exists():
                        try:
                            temp_wrapped.unlink(missing_ok=True)
                        except Exception:
                            pass
                    if file_path.exists():
                        completed_bytes += file_path.stat().st_size
                    if total_bytes > 0:
                        mapped = 20 + int(min(1.0, completed_bytes / total_bytes) * 75)
                        self.progress.emit(min(95, max(20, mapped)))

            # 3. Format/wipe userdata and cache to ensure clean boot without encryption/stale data bootloops
            self._log("Formatting userdata and cache...")
            self.action_changed.emit(tr("action_formatting"))
            try:
                if hasattr(da_handler, "da_erase"):
                    da_handler.da_erase(["userdata", "cache"], parttype="user")
                elif hasattr(mtk, "daloader") and hasattr(mtk.daloader, "formatflash"):
                    for clean_part in ("userdata", "cache"):
                        p_entry = scatter_entries_map.get(clean_part)
                        if p_entry and p_entry.get("offset") is not None and p_entry.get("partition_size"):
                            mtk.daloader.formatflash(
                                addr=p_entry["offset"],
                                length=p_entry["partition_size"],
                                partitionname=clean_part,
                                parttype="user",
                            )
            except Exception as e:
                self._log(f"Notice: userdata format non-fatal: {e}")

            self.step_changed.emit(STEP_DONE)
            self.progress.emit(100)
            self.action_changed.emit(tr("action_flash_complete"))
            self._log("Flash complete! Disconnect USB and reboot the device.")
            self.finished.emit(True, "")
        finally:
            watcher.stop()
            # Hand mtkclient's messages back to its own logging so nothing is
            # left holding the worker after the session ends.
            try:
                if hasattr(mtk, "config"):
                    mtk.config.gui = None
            except Exception as e:
                logger.debug("Failed to reset mtk gui hook: %s", e)
            # Clean up USB port / DA connection so libusb interface is released
            try:
                if mtk is not None and hasattr(mtk, "port") and mtk.port is not None:
                    mtk.port.close()
            except Exception as e:
                logger.debug("Failed to close mtk port cleanly: %s", e)

    def _is_legacy_mode(self, mtk):
        mode = getattr(getattr(mtk, "daloader", None), "flashmode", None)
        return mode == 3 or mode == "LEGACY"

    def _resolve_preloader_target_parts(self, mtk, normalized_region: str = ""):
        norm = normalized_region.upper() if normalized_region else ""
        if norm == "EMMC_BOOT_1":
            return ("boot1",)
        elif norm == "EMMC_BOOT_2":
            return ("boot2",)
        elif norm == "EMMC_BOOT1_BOOT2":
            if self._is_legacy_mode(mtk):
                return ("boot1",)
            return ("boot1", "boot2")
        else:
            if self._is_legacy_mode(mtk):
                return ("boot1",)
            return ("boot1", "boot2")

    def _write_partition_segmented(
        self,
        mtk,
        addr: int,
        file_path_str: str,
        segment_bytes: int = LEGACY_SEGMENTED_WRITE_BYTES,
        parttype: str = "user",
        file_offset: int = 0,
        write_length: Optional[int] = None,
        bytes_progress_cb=None,
    ):
        """Write large partitions in chunks (50MB by default) to keep USB transfers responsive."""
        file_path = Path(file_path_str)
        file_size = file_path.stat().st_size if file_path.exists() else 0
        fsize = write_length if write_length is not None else max(0, file_size - file_offset)
        offset = 0
        seg_idx = 0
        total_segments = max(1, (fsize + segment_bytes - 1) // segment_bytes)

        while offset < fsize:
            seg_idx += 1
            seg_len = min(segment_bytes, fsize - offset)
            seg_addr = addr + offset
            source_off = file_offset + offset

            self._log(
                f"  Segmented write {seg_idx}/{total_segments}: "
                f"offset=0x{seg_addr:X} size={seg_len / 1048576:.1f}MB"
            )
            daloader = getattr(mtk, "daloader", None)
            ok = False
            if daloader is not None and hasattr(daloader, "writeflash"):
                ok = daloader.writeflash(
                    addr=seg_addr,
                    length=seg_len,
                    filename=file_path_str,
                    offset=source_off,
                    parttype=parttype,
                )
            if not ok:
                err = getattr(getattr(mtk, "config", None), "last_error", None) or "writeflash returned failure"
                raise RuntimeError(
                    f"Segmented write failed segment={seg_idx}/{total_segments} "
                    f"offset=0x{seg_addr:X} ({err})"
                )

            offset += seg_len
            if bytes_progress_cb:
                bytes_progress_cb(seg_len)

    def _write_mtk_partition(
        self,
        da_handler,
        mtk,
        part_name,
        file_path_str,
        part_type,
        offset=None,
        file_offset=0,
        write_length=None,
        bytes_progress_cb=None,
    ):
        """Invoke da_handler to write a partition, supporting direct offset write, real DaHandler, and test mocks."""
        file_path = Path(file_path_str)
        file_size = file_path.stat().st_size if file_path.exists() else 0
        wlen = write_length if write_length is not None else max(0, file_size - file_offset)

        # Fast path: if offset is known and da_handler / mtk supports direct offset write,
        # write directly by offset. This bypasses detect_partition and on-device GPT probing,
        # ensuring reliable writes on MBR/EBR devices (e.g. MT6572, MT6582) and unformatted flash.
        if offset is not None:
            daloader = getattr(mtk, "daloader", None)
            if daloader is not None and hasattr(daloader, "writeflash") and (wlen > LEGACY_SEGMENTED_WRITE_BYTES or file_offset > 0):
                self._write_partition_segmented(
                    mtk,
                    addr=offset,
                    file_path_str=file_path_str,
                    segment_bytes=LEGACY_SEGMENTED_WRITE_BYTES,
                    parttype=part_type,
                    file_offset=file_offset,
                    write_length=wlen,
                    bytes_progress_cb=bytes_progress_cb,
                )
                return

            if hasattr(da_handler, "da_wo"):
                ok = da_handler.da_wo(
                    start=offset,
                    length=wlen,
                    filename=file_path_str,
                    parttype=part_type,
                )
                if not ok:
                    err = getattr(getattr(mtk, "config", None), "last_error", None) or "write failed"
                    raise RuntimeError(f"Writing {part_name} at offset {hex(offset)} failed: {err}")
                return
            if daloader is not None and hasattr(daloader, "writeflash"):
                ok = daloader.writeflash(
                    addr=offset,
                    length=wlen,
                    filename=file_path_str,
                    offset=file_offset,
                    parttype=part_type,
                )
                if not ok:
                    err = getattr(getattr(mtk, "config", None), "last_error", None) or "write failed"
                    raise RuntimeError(f"Writing {part_name} at offset {hex(offset)} failed: {err}")
                return

        # Fallback path: handle_da_cmds / da_write (used by test mocks or when offset is unknown)
        offset_hex = hex(offset) if offset is not None else "0x0"
        length_hex = hex(wlen)

        class _Args:
            partitionname = part_name
            filename = file_path_str
            parttype = part_type
            offset = offset_hex
            length = length_hex

        if hasattr(da_handler, "handle_da_cmds"):
            try:
                da_handler.handle_da_cmds(mtk, "w", _Args())
            except TypeError:
                da_handler.handle_da_cmds(
                    mtk,
                    "w",
                    partitions=part_name,
                    filenames=file_path_str,
                    parttype=part_type,
                )
        elif hasattr(da_handler, "da_write"):
            da_handler.da_write(
                parttype=part_type,
                filenames=[file_path_str],
                partitions=[part_name],
            )
        else:
            raise RuntimeError("DaHandler has no write method")


# ---------------------------------------------------------------------------
# Device monitor (USB presence poller for S3/S5 transitions)
# ---------------------------------------------------------------------------
# Supported MTK-family USB vendors (same set mtkclient's usblib matches):
# MediaTek, LG, OPPO/realme/oneplus, Sony.
_MTK_VENDOR_IDS = (0x0E8D, 0x1004, 0x22D9, 0x0FCE)

# Flash-mode VID/PID pairs supported by mtkclient (BROM / Preloader / DA modes).
# Used by DeviceMonitor to avoid falsely triggering on normal Android (MTP/ADB) mode.
_MTK_FLASH_IDS = {
    0x0E8D: {0x0003, 0x2000, 0x2001, 0x20FF, 0x3000, 0x6000},
    0x1004: {0x6000},
    0x22D9: {0x0006},
    0x0FCE: {0xF200, 0xD1E9, 0xD1E2, 0xD1EC, 0xD1DD},
}


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
    elif IS_MAC:
        # Real macOS: the vendored universal dylib; simulated macOS (Linux
        # host, paths.SIMULATE_MACOS): the host ELF .so — never the dylib.
        if paths.SIMULATE_MACOS:
            backend = usb.backend.libusb1.get_backend(
                find_library=lambda name: "libusb-1.0.so"
            )
        else:
            dylib_path = paths.find_libusb_dylib()
            if dylib_path:
                backend = usb.backend.libusb1.get_backend(
                    find_library=lambda name: dylib_path
                )
            else:
                backend = usb.backend.libusb1.get_backend(
                    find_library=lambda name: "libusb-1.0.dylib"
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
        self._lost_grace_until = 0.0
        self._grace_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def hold_lost(self, seconds):
        """Ignore device-lost reports for ``seconds``.

        A backend that deliberately resets the target (the MTKClient session
        restart into BROM) makes it re-enumerate; without this the monitor
        would debounce that into "USB disconnected" and abort the flash.
        """
        with self._grace_lock:
            self._lost_grace_until = max(
                self._lost_grace_until, time.time() + float(seconds)
            )

    def release_lost_hold(self):
        with self._grace_lock:
            self._lost_grace_until = 0.0

    def _lost_is_held(self):
        with self._grace_lock:
            return time.time() < self._lost_grace_until

    def run(self):
        usb_core, backend = _load_libusb_backend()
        if usb_core is None:
            self._failed = True
            self.error.emit(
                "USB monitor unavailable: libusb backend could not be loaded. "
                "Device detection is disabled; use the flash backend directly."
            )
            return
        android_mode_warned = False
        while not self._stop.is_set():
            try:
                present = False
                port_label = ""
                devices = usb_core.find(find_all=True, backend=backend)
                for dev in devices:
                    try:
                        vid = dev.idVendor
                        pid = dev.idProduct
                        if vid in _MTK_FLASH_IDS:
                            if pid in _MTK_FLASH_IDS[vid]:
                                present = True
                                port_label = f"USB {vid:04X}:{pid:04X}"
                                break
                            elif vid == 0x0E8D and not android_mode_warned:
                                android_mode_warned = True
                                self.error.emit(
                                    f"Device detected (USB {vid:04X}:{pid:04X}) in normal Android mode. "
                                    "Please power off the device completely and reconnect it to enter flash mode."
                                )
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
                        if not self._lost_is_held():
                            self.device_lost.emit()
                        # else: the flash worker is restarting the target on
                        # purpose (preloader -> BROM), so stay silent.
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
    action_changed = Signal(str)
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
    def start_flash(self, package_path, pre_extracted_dir="", method="auto", model=""):
        # Kill any backend still running (e.g. a searching flash_tool) first,
        # then start the new one. The cancelled worker's late terminal signals
        # are detached so they cannot reach the UI or clobber the new run.
        self.cancel_flash()
        worker = FlashWorker(
            package_path,
            pre_extracted_dir,
            method,
            model=model,
            device_loss_hold=self.hold_device_lost,
        )
        self._flash_worker = worker
        worker.step_changed.connect(self.step_changed)
        worker.progress.connect(self.progress)
        worker.action_changed.connect(self.action_changed)
        worker.log_message.connect(self.log_message)
        worker.finished.connect(self.flash_finished)
        worker.finished.connect(lambda ok, err, w=worker: self._on_flash_done(w, ok, err))
        worker.start()

    def hold_device_lost(self, seconds):
        """See :meth:`DeviceMonitor.hold_lost` (passed to the flash worker)."""
        monitor = self._device_monitor
        if monitor is not None:
            monitor.hold_lost(seconds)

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
        for sig in (worker.step_changed, worker.progress, worker.action_changed,
                    worker.log_message, worker.finished):
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
            if self._device_monitor.isRunning():
                self._device_monitor.wait(2000)

    def cleanup(self):
        self.cancel_flash()
        self.cancel_extract()
        self.stop_device_monitor()
