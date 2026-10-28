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
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
import zlib
from pathlib import Path
from struct import pack, unpack
from typing import Optional, Union, Tuple

from .downloads import downloads_dir

# Combined ETA used by the install card and the Device Prep stage. The install
# card already shows a measured write ETA, and the Device Prep stage is a
# second, independent clock on the same card, so a single ``int`` needs a
# fixed, unambiguous shape:
#
#     0                ... no honest answer yet (the card shows elapsed time)
#     N > 0            ... remaining seconds, whole seconds, for the install
#
# The Device Prep stage keeps its own directly measured clock and reports it to
# the card through the same signal, so the card can render both halves itself.
# Keeping them as separate numbers avoids inventing a two-part seconds value
# that would go stale the moment either half changes its rate.
COMBINED_ETA_INITIAL = 0


class _InstallWindowRecord:
    """One pass through a flash's write phase.

    Holds the measurement window the app uses to turn the bytes already
    written into a *remaining* number. ``FlashWorker`` owns one of these for
    the SP Flash Tool branch and one for the MTKClient branch: the two engines
    are separate processes with separate USB stacks, and combining their
    windows would cross the fault boundary of the run.
    """

    def __init__(self):
        self._done = 0
        self._total = 0
        self._eta = ThroughputEta()
        self._remaining = 0
        self._last_eta_note = 0.0

    @property
    def done(self) -> int:
        return self._done

    @property
    def total(self) -> int:
        return self._total

    @property
    def remaining(self) -> int:
        """Seconds left, or 0 while the rate is not yet honest."""
        return self._remaining

    def note(self, done: int, total: int) -> int:
        self._done = max(0, int(done))
        self._total = max(0, int(total))
        now = time.monotonic()
        if self._total > 0 and (
            (self._done, self._total) != getattr(self, "_last_position", None)
            or now - self._last_eta_note >= 1.0
        ):
            estimate = self._eta.note(self._done, self._total)
            self._remaining = int(estimate) if estimate is not None else 0
            self._last_eta_note = now
            self._last_position = (self._done, self._total)
        elif self._total <= 0:
            self._remaining = 0
        return self._remaining


class _CombinableEta:
    """Install ETA plus Device Prep ETA, as the install card consumes them.

    The flash worker reports *install remaining* through the normal
    ``eta_changed`` signal, and the Device Prep stage reports *prep remaining*
    the same way. Both halves are turned into seconds at the card, which owns
    the layout that wants two lines, so this class only answers "how many
    seconds of install are left right now, measured from this run's own write
    rate":

    * 0 while nothing has been written or the rate is not yet honest;
    * > 0 the whole number of seconds left, the card adds the prep half on top.
    """

    def __init__(self):
        self._window = _InstallWindowRecord()

    def reset(self) -> None:
        self._window = _InstallWindowRecord()

    def note(self, done: int, total: int) -> int:
        """Record the tool's position and return install seconds left, or 0."""
        return self._window.note(done, total)

    @property
    def rate(self) -> float:
        return self._window._eta.rate

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

import platform

from . import linux_sp_flash, paths
from .fw_package import prune_to_firmware_package
from .i18n import tr
from .progress_model import ThroughputEta, percent_of

paths.ensure_mtkclient_importable()

logger = logging.getLogger(__name__)

# --- platform constants ------------------------------------------------------
IS_WINDOWS = paths.IS_WINDOWS
IS_MAC = paths.IS_MAC
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
_SUBPROCESS_NO_WINDOW = CREATE_NO_WINDOW

# --- pipeline steps ----------------------------------------------------------
STEP_EXTRACTING = "EXTRACTING"
STEP_PLEASE_WAIT = "PLEASE_WAIT"
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
_SP_QT_LOG_MAX_LINES = 80000


_TOOL_LINE_PREFIXES = ("[LOG] ", "[SP] ", "[MTK] ", "[QT] ", "[TOOL] ")


def tool_line_body(line) -> str:
    """Drop log tokens so a search check sees the tool's own text."""
    text = str(line or "").strip()
    while True:
        stripped = text.lstrip()
        for prefix in _TOOL_LINE_PREFIXES:
            if stripped.startswith(prefix):
                text = stripped[len(prefix):]
                break
        else:
            return stripped


def _strip_qt_trace_prefix(text: str) -> str:
    """The message part of an SP Flash Tool internal Qt trace line.

    ``10/11/2026 17:07:43.796 FlashTool[2232][7880][D]: APCore::ConnMedia::
    Search(): Search usb, timeout set as 120000 ms(..\\..\\Conn.cpp,21)``
    becomes ``Search usb, timeout set as 120000 ms``. Text that is not a trace
    line is returned untouched, so the console's own lines keep working.
    """
    if "FlashTool[" not in text:
        return text
    _head, _sep, tail = text.partition("]: ")
    tail = tail.strip()
    if tail.endswith(")"):
        open_paren = tail.rfind("(")
        if open_paren != -1:
            tail = tail[:open_paren].rstrip()
    if ": " in tail:
        tail = tail.rsplit(": ", 1)[1].strip()
    return tail


def is_sp_flash_complete_line(line) -> bool:
    """True for SP Flash Tool's own completion lines.

    Two lines mark a finished command: "Flash complete! Disconnect USB..." and
    "All command exec done!", which the tool logs after the flash command
    returns without an exception. Matched on those phrases: any line containing
    "all command exec", "all command" and "exec", or "flash complete" marks the flash successful.
    """
    text = _TIMESTAMP_PREFIX_RE.sub("", tool_line_body(line)).lstrip()
    low = _strip_qt_trace_prefix(text).lower()
    if "incomplete" in low:
        return False
    return (
        "flash complete" in low
        or "all command exec" in low
        or ("all command" in low and "exec" in low)
    )


def sp_log_result_is_success(log_success, error_detail, console_complete) -> bool:
    """Decide an SP Flash Tool run from its internal log, then its console.

    The internal log is the source of truth (the shipped build reads it after
    the tool exits). If the console emitted "All command exec" or "Flash complete",
    disconnection or post-flash reboot artifacts (e.g. SP_EXIT_... or USB disconnect)
    must not turn a completed flash into a failure.
    """
    if log_success:
        return True
    if not console_complete:
        return False
    detail = str(error_detail or "").strip()
    if not detail or is_disconnection_error(detail) or detail.startswith("SP_EXIT_"):
        return True
    return False


def is_sp_usb_search_line(line) -> bool:
    """True when flash_tool is searching USB.

    The connect sentence follows this prefix: case-insensitive, with
    leading whitespace (and a log timestamp) ignored, and recognised inside
    the tool's own internal Qt trace as well as on its console stream. Matches
    "search usb", "searchusb", and "scanning usb port".
    """
    text = _TIMESTAMP_PREFIX_RE.sub("", tool_line_body(line)).lstrip()
    low = _strip_qt_trace_prefix(text).lower()
    return (
        low.startswith("search usb")
        or "search usb" in low
        or "searchusb" in low
        or "scanning usb" in low
    )


def is_mtk_usb_search_line(line) -> bool:
    """MTKClient search state: a hint line, ``Port - Hint``, or a line of dots.

    After the log token and surrounding whitespace are removed, a search line
    starts with ``...`` or, case-insensitively, ``hint``. ``Port - Hint:``
    does not start with ``hint``, so that prefix is a search line too.
    """
    text = tool_line_body(line)
    if not text:
        return False
    if text.startswith("..."):
        return True
    if text.startswith("Hint:"):
        return True
    low = text.lower()
    if low.startswith("port - hint"):
        return True
    return "power off the phone" in low


# Tool-reported failures. mtkclient catches its own DA errors and still exits
# 0, so these lines — not the exit code — are what tells a real install from a
# wedged one: "Error on sending emmc read flash command, response: 0x0" left
# the player half-written while the app announced "Install complete".
_TOOL_FATAL_ERROR_RE = re.compile(
    r"error on (sending|writing|reading|erasing)|"
    r"^error\b|"
    r"error:\s|"
    r"failed to (write|send|erase|read)|"
    r"response:\s*0x0\b|"
    r"\bbrom error\b|"
    r"\bda error:",
    re.IGNORECASE,
)

# A failed read of the partition table is a fallback, not a failure: the
# legacy MBR/EBR package is written from the scatter addresses and the run
# continues. The shipped build logs this exact line before a successful Y1/Y2
# install, and ``failed to read`` otherwise matches the fatal-tool pattern.
_RECOVERABLE_TOOL_LINE_RE = re.compile(
    r"(?:could not|failed to) read (?:the device's |the )?(?:gpt )?partition table|"
    r"falling back to per-partition reads|"
    r"no mbr/ebr table found",
    re.IGNORECASE,
)


def is_fatal_tool_line(line) -> bool:
    """True when the tool reported a failure that must not read as success.

    The app's own notices are exempt: "Notice: userdata format non-fatal: ..."
    is a decision this side of the pipe made, not the tool reporting a failure.
    A partition-table read that fell back to the package's addresses is exempt
    for the same reason: the line says the run is continuing, not failing.
    """
    text = tool_line_body(line)
    if not text or text.lower().startswith("notice:"):
        return False
    if _RECOVERABLE_TOOL_LINE_RE.search(text):
        return False
    return bool(_TOOL_FATAL_ERROR_RE.search(text))


def is_logging_noise_line(line) -> bool:
    """Python logging failures that must not look like install progress.

    A ``StreamHandler`` whose stream is ``None`` (pythonw, or a closed
    console) raises ``AttributeError`` inside ``emit``. That traceback used
    to land in the tool log and was then read as forward progress.
    """
    text = tool_line_body(line)
    low = text.lower()
    if not low:
        return True
    if "attributeerror" in low:
        return True
    if "nonetype" in low and "write" in low:
        return True
    if "has no attribute 'write'" in low or 'has no attribute "write"' in low:
        return True
    if "streamhandler" in low:
        return True
    if low.startswith("traceback (most recent call last)"):
        return True
    if low.startswith("during handling of the above exception"):
        return True
    if low.startswith("the above exception"):
        return True
    if "history.ini" in low and "updated" in low:
        return True
    if low.startswith("file ") and "logging" in low:
        return True
    return False


# Install status while the tool is running, before a numbered stage.
PHASE_BEFORE = "before"
PHASE_CONNECT = "connect"
PHASE_PLEASE_WAIT = "please_wait"


def is_tool_trace_line(line) -> bool:
    """True for SP Flash Tool's own internal Qt trace lines.

    The tool mirrors its trace into the stream the app reads, shaped
    ``10/11/2026 17:07:43.796 FlashTool[2232][7880][D]: ...`` (the app then
    forwards it with a ``[QT] `` tag). Those lines are diagnostics, not device
    progress: reading them as forward progress pushed the install card off
    "please connect your player" and onto "please wait" while the tool was
    still searching for the device.
    """
    return "FlashTool[" in tool_line_body(line)


def classify_tool_wait_line(line) -> str:
    """``connect``, ``forward``, or ``""`` when the line must not move the UI.

    Search / hint / dots lines are the connect phase. A later line that is
    real tool output is forward progress. Logging-error spam and the tool's
    own internal trace are ignored.
    """
    if is_logging_noise_line(line):
        return ""
    if is_tool_trace_line(line):
        return ""
    if is_sp_usb_search_line(line) or is_mtk_usb_search_line(line):
        return "connect"
    body = tool_line_body(line)
    if not body:
        return ""
    low = body.lower()
    if any(marker in low for marker in _HINT_MARKERS):
        return "connect"
    return "forward"


# Lines that mean the player answered and the tool is working on it. Only
# these end the connect sentence: everything else a backend prints while it
# searches (its startup banner, the internal Qt trace, the timeout lines of a
# search that has just expired, the app's own "searching again" notice) must
# leave the connect prompt exactly where it is, because that prompt *is* the
# moment the user is being asked to connect the player.
_DEVICE_SEEN_MARKERS = (
    "usb port detected",
    "port - device detected",
    "port - device detected :)",
    "brom connected",
    "da connected",
    "download da now",
    "of da has been sent",
    "of bootloader has been sent",
    "of image data has been sent",
    "formatting flash",
    "of flash has been formatted",
    "format succeeded",
    "downloading images",
    "download ok",
    "done",
    "progress",
)


def is_tool_device_seen_line(line) -> bool:
    """True when the backend reports the player and starts working on it."""
    low = tool_line_body(line).lower()
    return any(marker in low for marker in _DEVICE_SEEN_MARKERS)


def advance_tool_wait_phase(phase: str, line: str) -> str:
    """Move the connect / please-wait phase from one tool line.

    The connect sentence belongs to the search: it goes up on a search or hint
    line and comes down only when the tool reports the player and starts
    working (see :func:`is_tool_device_seen_line`). Other chatter is ignored,
    so the prompt cannot be replaced by "please wait" while the user is still
    being asked to connect the player — which is what the tool's own Qt banner,
    its trace lines and a failed search's timeout lines used to do.
    """
    kind = classify_tool_wait_line(line)
    current = phase or PHASE_BEFORE
    if kind == "connect":
        return PHASE_CONNECT
    if kind == "forward" and current == PHASE_CONNECT and is_tool_device_seen_line(line):
        return PHASE_PLEASE_WAIT
    return current


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
# Flash engines are platform-routed. ``mtk_mac`` is retained only as a
# legacy persisted alias; it is never presented as a distinct install method.
METHOD_SP = "sp"
METHOD_MTK = "mtk"
METHOD_MTK_MAC = "mtk_mac"


_WINDOWS_INTEL_MACHINES = ("x86_64", "amd64", "x64", "i386", "i486", "i586", "i686", "x86")


def windows_only_method():
    """The one install method Windows may use, decided by the CPU.

    SP Flash Tool ships only as an x86/x86-64 Windows build, so Intel/AMD
    Windows gets SP Flash Tool and no route to MTKClient. Any other Windows CPU
    (ARM64) gets MTKClient only. Returns ``None`` off Windows, where the
    per-platform rules below apply instead.
    """
    if paths.IS_MAC or not paths.IS_WINDOWS:
        return None
    machine = (platform.machine() or "").lower()
    return METHOD_SP if machine in _WINDOWS_INTEL_MACHINES else METHOD_MTK


def default_flash_method():
    """Method used until the user picks one.

    SP Flash Tool is the default method on Linux (when the host architecture is
    supported) and Windows x86/x64, with MTKClient as the fallback / second tool
    attempted if an installation cannot proceed via SP Flash Tool. macOS and
    ARM64 Linux/Windows use MTKClient.

    ``paths.IS_MAC`` is read live rather than the module-level ``IS_MAC``
    snapshot so simulated macOS mode (and the tests that toggle it) is
    honoured.
    """
    locked = windows_only_method()
    if locked:
        return locked
    if paths.IS_MAC:
        return METHOD_MTK
    if paths.IS_LINUX:
        return METHOD_SP if linux_sp_flash.arch_supported() else METHOD_MTK
    return METHOD_SP


def normalise_method(method):
    """Coerce a persisted (possibly legacy) setting into a usable method id.

    Legacy ``"auto"`` resolves to the platform default (SP Flash Tool on Linux
    and Windows, MTKClient on macOS) rather than an ambiguous automatic
    choice; on macOS every value collapses to MTKClient, the only backend that
    exists there.

    The legacy ``mtk_mac`` setting is normalized to the platform default. The
    simulated macOS environment remains available to developers through the
    explicit ``--simulate-macos`` / ``INNIOASIS_SIMULATE_MACOS`` launch option,
    not as a second MTKClient selector.
    """
    value = str(method or "").strip().lower()
    if value not in (METHOD_SP, METHOD_MTK, METHOD_MTK_MAC, "auto"):
        return default_flash_method()
    if paths.IS_MAC:
        return METHOD_MTK
    if value in ("auto", METHOD_MTK_MAC):
        return default_flash_method()
    return value


def backend_method(method):
    """Map an install method onto the backend id FlashWorker dispatches on.

    ``normalise_method`` has already folded the legacy simulated-macOS setting
    into the platform's supported user-selectable method.
    """
    return normalise_method(method)


def is_mtk_method(method) -> bool:
    """True when the method uses the MTKClient engine."""
    if paths.IS_MAC:
        return True
    return backend_method(method) == METHOD_MTK


# --- MediaTek / SP Flash Tool error knowledge --------------------------------
# The console tool reports failures as ``S_NAME (code)`` on stdout and in its
# own QT_FLASH_TOOL.log. The raw name and code stay visible for support; the
# handful below are the ones a normal user can actually hit, and each gets an
# i18n key so the error page can say what happened and what to try.
SP_ERROR_GUIDANCE = {
    "S_TIMEOUT": "err_sp_timeout",
    "S_BROM_CMD_STARTCMD_FAIL": "err_sp_brom_startcmd",
    "S_CHIP_TYPE_NOT_MATCH": "err_sp_wrong_model",
    "S_FT_DOWNLOAD_FAIL": "err_sp_download_fail",
    "S_DA_INIT_SYNC_ERROR": "err_sp_da_init_sync",
    "S_BROM_DOWNLOAD_DA_FAIL": "err_sp_brom_da_fail",
    "S_COM_PORT_OPEN_FAIL": "err_sp_com_port",
    "S_FT_ENABLE_DRAM_FAIL": "err_sp_enable_dram",
    "S_DA_SDMMC_READ_FAILED": "err_sp_storage_read",
    "S_DA_UPDATE_BOOTLOADER_EXIST_MAGIC_NOT_MATCHED": "err_sp_download_fail",
    "S_DA_UPDATE_BOOTLOADER_EXIST_TYPE_NOT_MATCHED": "err_sp_download_fail",
    "S_DA_UPDATE_BOOTLOADER_FILE_SIZE_EXCEEDS_BOUNDARY_ADDR": "err_sp_download_fail",
    "S_DA_UPDATE_BOOTLOADER_BEGIN_ADDR_NOT_MATCHED": "err_sp_download_fail",
}

# Numeric-only fallback for the tail of a run that only carries
# ``Exception: err_code[4008]``.
SP_ERROR_CODE_NAMES = {
    "1013": "S_COM_PORT_OPEN_FAIL",
    "1042": "S_TIMEOUT",
    "2005": "S_BROM_CMD_STARTCMD_FAIL",
    "3145": "S_DA_UPDATE_BOOTLOADER_EXIST_MAGIC_NOT_MATCHED",
    "3146": "S_DA_UPDATE_BOOTLOADER_EXIST_TYPE_NOT_MATCHED",
    "3147": "S_DA_UPDATE_BOOTLOADER_FILE_SIZE_EXCEEDS_BOUNDARY_ADDR",
    "3148": "S_DA_UPDATE_BOOTLOADER_BEGIN_ADDR_NOT_MATCHED",
    "3184": "S_CHIP_TYPE_NOT_MATCH",
    "4008": "S_FT_DOWNLOAD_FAIL",
    "1093": "S_DA_INIT_SYNC_ERROR",
    "4032": "S_FT_ENABLE_DRAM_FAIL",
}

# Connecting the wrong player cannot be fixed by retrying or by switching
# backends, so the flow stops and says so instead of looping.
WRONG_MODEL_ERRORS = frozenset(
    {"S_CHIP_TYPE_NOT_MATCH", "DEVICE_PACKAGE_MISMATCH", "SP_WRONG_MODEL"}
)

# The app's own code for "the command ran but never showed any progress": the
# no-progress watchdog ended it and the flow may offer the other backend.
ERR_ATTEMPT_TIMEOUT = "ERR_ATTEMPT_TIMEOUT"

_SP_ERROR_NAME_RE = re.compile(r"(S_[A-Z0-9_]+)")
_SP_EXIT_ERROR_RE = re.compile(r"\bSP_EXIT_-?\d+\b", re.IGNORECASE)


def is_sp_driver_error(value) -> bool:
    """True when an SP Flash Tool exit or driver error indicates missing MediaTek USB drivers.

    Only non-zero ``SP_EXIT_*`` codes are treated as driver-missing signals:
    exit code 0 means the tool stopped cleanly (no "download ok" in the log)
    rather than that the driver stack was absent, so ``SP_EXIT_0`` must not
    route the user to the driver error card.
    """
    text = str(value or "").strip()
    if not text:
        return False
    upper = text.upper()
    m = _SP_EXIT_ERROR_RE.search(upper)
    if m:
        try:
            n = int(m.group(0).split("SP_EXIT_")[-1])
        except ValueError:
            return False
        if n == 0:
            return False
        return True
    return any(
        token in upper
        for token in (
            "DRIVER_NOT_INSTALLED",
            "USB_DRIVER_MISSING",
            "DRIVER_REQUIRED",
            "MEDIATEK_DRIVER",
        )
    )


def sp_error_name(value) -> str:
    """``S_*`` name carried by a finished error code or tool message (or "")."""
    match = _SP_ERROR_NAME_RE.search(str(value or "").upper())
    return match.group(1) if match else ""


def sp_error_code(value) -> str:
    """Numeric MediaTek error code carried by a finished code (or "")."""
    match = re.search(r"\((\d{3,5})\)", str(value or ""))
    return match.group(1) if match else ""


def is_wrong_model_error(value) -> bool:
    """True when the failure means the wrong player is connected."""
    text = str(value or "").upper()
    if any(token in text for token in WRONG_MODEL_ERRORS):
        return True
    return sp_error_name(text) in WRONG_MODEL_ERRORS


# "It never got as far as talking to the player": the tool could not open its
# port (SP Flash Tool reports error 1013 / ``S_COM_PORT_OPEN_FAIL``) or the
# engine could not claim the device at all. Nothing was written, so trying the
# other engine is a real option — and the step that follows should say the
# tool could not *connect*, not that the player dropped out of a running flash.
CONNECT_FAILURE_ERRORS = frozenset(
    {"S_COM_PORT_OPEN_FAIL", "CONNECTION_FAILED", "MTK_INIT_FAILED", "CLI_START_FAILED"}
)

# The same 1013 reported by number alone (``SP_ERR_1013``, ``err_code[1013]``).
_BARE_CONNECT_CODE_RE = re.compile(r"(?<!\d)1013(?!\d)")


def is_connect_failure(value) -> bool:
    """True when a failure means the tool could not reach the player.

    Accepts the console tool's ``S_COM_PORT_OPEN_FAIL (1013)``, that code
    carried by number alone, and the equivalent engine-side connect failures.
    """
    text = str(value or "").upper()
    if not text:
        return False
    if sp_error_name(text) in CONNECT_FAILURE_ERRORS:
        return True
    if any(
        token in text
        for token in ("CONNECTION_FAILED", "MTK_INIT_FAILED", "CLI_START_FAILED")
    ):
        return True
    return bool(_BARE_CONNECT_CODE_RE.search(text))


def is_com_port_error(value) -> bool:
    """True when the failure means SP Flash Tool failed to open the COM port (1013)."""
    text = str(value or "").upper()
    if not text:
        return False
    if sp_error_name(text) == "S_COM_PORT_OPEN_FAIL":
        return True
    if any(
        m in text
        for m in (
            "S_COM_PORT_OPEN_FAIL",
            "COM_PORT_OPEN_FAIL",
            "FAILED TO OPEN COM PORT",
        )
    ):
        return True
    if sp_error_code(text) == "1013":
        return True
    return bool(_BARE_CONNECT_CODE_RE.search(text))


def describe_backend_error(value) -> str:
    """Translated explanation of a backend failure, or "" when unknown.

    Keeps a raw ``S_FT_DOWNLOAD_FAIL (4008)`` from being the only thing the
    user ever sees.
    """
    if not value:
        return ""
    if is_wrong_model_error(value):
        return tr("err_sp_wrong_model")
    if is_sp_driver_error(value):
        return tr("err_driver_missing_desc")
    name = sp_error_name(value) or SP_ERROR_CODE_NAMES.get(sp_error_code(value), "")
    key = SP_ERROR_GUIDANCE.get(name)
    return tr(key) if key else ""


def _format_error_token(raw: str, default: str = "") -> str:
    name = sp_error_name(raw)
    code = sp_error_code(raw)
    if name and code:
        return f"{name} {code}"
    return name or code or default


def parse_flash_error(value, model: str = "") -> tuple[str, str]:
    """Parse a flash failure message or error code into a (title, explanation) pair.

    Returns localized, human-friendly guidance explaining *why* the flash failed
    and what action to take, while preserving technical error codes for diagnostics.
    """
    raw = str(value or "").strip()
    if not raw:
        return tr("install_failed_title"), tr("err_generic_flash_failed_desc").format(detail="")

    if raw == "CACHE_FILE_MISSING":
        return tr("install_failed_title"), tr("err_cache_file_missing_desc")

    # 1. USB Disconnected / lost connection
    if is_disconnection_error(raw) or "USB_DISCONNECTED" in raw.upper() or "DEVICE_DISCONNECTED" in raw.upper():
        model_name = model or tr("device_default_name")
        return tr("err_usb_lost_title"), tr("err_usb_disconnected_recovery").format(model=model_name)

    # 2. Wrong Model / Chip mismatch
    if is_wrong_model_error(raw):
        code_token = _format_error_token(raw, "MISMATCH")
        title = f"{tr('err_sp_wrong_model_title')} ({code_token})" if code_token else tr("err_sp_wrong_model_title")
        return title, tr("err_sp_wrong_model")

    # 2b. SP Flash Tool exit / missing MediaTek USB Driver
    if is_sp_driver_error(raw):
        match = _SP_EXIT_ERROR_RE.search(raw.upper())
        code_token = match.group(0) if match else _format_error_token(raw, raw[:32])
        title = (
            f"{tr('err_driver_missing_title')} ({code_token})"
            if code_token
            else tr("err_driver_missing_title")
        )
        return title, tr("err_driver_missing_desc")

    # 3. Timeout / device not responding
    if (
        raw == ERR_ATTEMPT_TIMEOUT
        or "TIMEOUT" in raw.upper()
        or sp_error_name(raw) == "S_TIMEOUT"
        or sp_error_code(raw) == "1042"
    ):
        code_token = _format_error_token(raw, "TIMEOUT")
        return f"{tr('err_attempt_timeout_title')} ({code_token})", tr("err_sp_timeout")

    # 4. Device powered on too early / BROM handshake
    if is_brom_handshake_error(raw):
        code_token = _format_error_token(raw, "2005")
        return f"{tr('err_connection_failed_title')} ({code_token})", tr("err_sp_brom_startcmd")

    # 5. COM port failure (error 1013)
    if is_com_port_error(raw):
        code_token = _format_error_token(raw, "1013")
        return f"{tr('err_connection_failed_title')} ({code_token})", tr("err_com_port_reboot_suggestion")

    # 6. Connection / Handshake failure
    if any(k in raw.upper() for k in ("CONNECTION_FAILED", "MTK_INIT_FAILED", "CLI_START_FAILED", "HANDSHAKE")):
        code_token = _format_error_token(raw, raw.split()[0] if raw else "CONNECTION_FAILED")
        return f"{tr('err_connection_failed_title')} ({code_token})", tr("err_connection_failed_desc")

    # 6. Download Agent sync / transmission failure
    if any(k in raw.upper() for k in ("S_DA_INIT_SYNC_ERROR", "S_BROM_DOWNLOAD_DA_FAIL", "DA_SYNC", "1093", "2004")):
        code_token = _format_error_token(raw, "DA_ERROR")
        return f"{tr('install_failed_title')} ({code_token})", tr("err_sp_da_init_sync")

    # 7. Memory / DRAM initialization failure
    if any(k in raw.upper() for k in ("S_FT_ENABLE_DRAM_FAIL", "DRAM_FAIL", "4032")):
        code_token = _format_error_token(raw, "4032")
        return f"{tr('install_failed_title')} ({code_token})", tr("err_sp_enable_dram")

    # 8. Write Interrupted / Flash write failed
    if any(k in raw.upper() for k in ("WRITE_FAILED", "S_FT_DOWNLOAD_FAIL", "WRITEFLASH", "SEGMENTED WRITE FAILED", "DA_WO", "4008", "STORAGE_READ")):
        code_token = _format_error_token(raw, "WRITE_FAIL")
        return f"{tr('err_flash_write_failed_title')} ({code_token})", tr("err_flash_write_failed_desc")

    # 9. Package files missing
    if any(k in raw.upper() for k in ("NO_SCATTER_FILE", "MISSING_IMAGES", "NO_IMAGE_FILES")):
        if ":" in raw:
            parts = raw.split(":", 1)[1].strip()
            if parts:
                return tr("err_missing_package_files_title"), f"{tr('err_missing_package_files_desc')}\n\n{parts}"
        return tr("err_missing_package_files_title"), tr("err_missing_package_files_desc")

    # 10. Flashing tool / import failure
    if any(k in raw.upper() for k in ("SP_FLASH_TOOL_NOT_FOUND", "MTK_IMPORT_FAILED")):
        return tr("err_tool_unavailable_title"), tr("err_tool_unavailable_desc")

    # 11. Known SP error guidance fallback
    name = sp_error_name(raw) or SP_ERROR_CODE_NAMES.get(sp_error_code(raw), "")
    if name and name in SP_ERROR_GUIDANCE:
        guidance = tr(SP_ERROR_GUIDANCE[name])
        code_token = _format_error_token(raw, name)
        return f"{tr('install_failed_title')} ({code_token})", guidance

    # 12. Generic fallback
    detail = raw
    if len(detail) > 40:
        detail = detail[:37] + "..."
    return f"{tr('install_failed_title')} ({detail})" if detail else tr("install_failed_title"), tr("err_generic_flash_failed_desc").format(detail=raw)


# The BROM handshake failures. The console tool found the USB port but could
# not start the boot ROM handshake; its own log says "Possibly target power up
# too early", which is the real cause: the player was already powered/plugged
# when the tool began scanning. The tool's message names the symptom, not the
# fix, so these failures carry the one instruction that actually resolves them.
BROM_HANDSHAKE_ERRORS = frozenset(
    {
        "S_BROM_CMD_STARTCMD_FAIL",
        "S_BROM_CMD_START_FAIL",
        "S_BROM_CMD_JUMP_DA_FAIL",
    }
)

_BARE_CODE_RE = re.compile(r"(\d{3,5})")


def is_brom_handshake_error(value) -> bool:
    """True when a failure means the BROM handshake started too early.

    Accepts anything the app passes around as an error: ``S_NAME (code)`` from
    the console tool's log, a bare code (``SP_ERR_2005``), or the raw
    ``err_code[2005]`` line.
    """
    text = str(value or "")
    if not text:
        return False
    if sp_error_name(text) in BROM_HANDSHAKE_ERRORS:
        return True
    code = sp_error_code(text)
    if not code:
        # ``SP_ERR_2005`` / ``err_code[2005]`` carry the number with no
        # parentheses; the table still names it.
        match = _BARE_CODE_RE.search(text)
        code = match.group(1) if match else ""
    return bool(code) and SP_ERROR_CODE_NAMES.get(code, "") in BROM_HANDSHAKE_ERRORS


def sp_brom_handshake_hint(value) -> str:
    """Plain-English fix for a BROM handshake failure, else "".

    The player has to be *powered off and unplugged* while the tool is
    searching: plugging it in before the search starts is what makes the
    handshake fail, and no amount of retrying or switching backends helps
    until the order is right. It is plain English here; the caller decides how
    to carry it (today it is appended to the failure the progress card shows).
    """
    if not is_brom_handshake_error(value):
        return ""
    return (
        "Unplug the player, wait a few seconds, and plug it back in only once "
        "the app is asking you to connect it — the tool has to be searching "
        "before the player is powered on."
    )


def mtk_reset_to_normal(mtk, da_handler=None) -> bool:
    """Reboot the player out of download mode after an MTKClient write.

    This is mtkclient's own ``reset`` command (``daloader.shutdown(bootmode=0)``,
    bootmode 0 == NORMAL): it tells the running DA to restart the target so the
    freshly flashed firmware boots on its own, the way an SP Flash Tool run
    already does, instead of leaving the player sitting in BROM/download mode
    until the user power-cycles it.

    Best effort by design: a player that fell off the bus or was already
    power-cycled returns False and the (already successful) install stands.
    """
    rebooted = False
    loader = getattr(mtk, "daloader", None)
    if loader is None or not hasattr(loader, "shutdown"):
        loader = getattr(da_handler, "daloader", None)
    if loader is not None and hasattr(loader, "shutdown"):
        try:
            loader.shutdown(bootmode=0)
            rebooted = True
        except BaseException as e:  # mtkclient may sys.exit() on a dead session
            logger.debug("Post-install reboot could not be sent: %s", e)

    # If loader.shutdown wasn't reachable, try direct DA finish/shutdown
    if not rebooted:
        da = getattr(loader, "da", None) or getattr(da_handler, "da", None)
        if da is not None:
            try:
                if hasattr(da, "finish"):
                    da.finish(0)
                    rebooted = True
                elif hasattr(da, "shutdown"):
                    da.shutdown(bootmode=0)
                    rebooted = True
            except BaseException as e:
                logger.debug("Direct DA reboot failed: %s", e)

    try:
        if mtk is not None and hasattr(mtk, "port") and mtk.port is not None:
            mtk.port.close(reset=True)
            rebooted = True
    except Exception as e:
        logger.debug("Port reset failed: %s", e)
    return rebooted


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
    if is_mtk_usb_search_line(text) or any(marker in low for marker in _HINT_MARKERS):
        return LINE_CONNECT_HINT
    return ""

# Seconds of silence from the target before the MTKClient backend tells the
# user the device stopped answering. DA configuration runs inside a blocking
# in-process call that cannot be interrupted, so without this the UI would sit
# on "configuring DA..." forever (see _StallWatcher).
MTK_STALL_WARN_SECONDS = 90

# Seconds of silence from the MTKClient backend's live console output before
# the app treats this as an install failure. This is separate from the device
# stall watcher (which reports when the target stops answering). It covers a
# different failure: the backend keeps printing, but the same BROM bring-up
# stall line has been on screen for too long with no change.
MTK_OUTPUT_STALL_WINDOW = 20.0
# USB enumeration can briefly disappear while the legacy DA is writing. Keep
# the device-loss UI from cancelling an active MTK transfer; the worker's own
# bounded USB checks will still report a real disconnect after this window.
MTK_WRITE_PROGRESS_USB_GRACE_SECONDS = 30.0

# Line prefixes read from the MTKClient console that indicate a BROM bring-up
# or DRAM bring-up stall rather than normal write progress.
MTK_BROM_STALL_PREFIXES = (
    "DRAM config needed for",
    "DRAM config",
    "Failed to read stage-2 config checksum",
)

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
# The app's own no-progress window, counted from the moment a backend command
# starts (SP Flash Tool: from its "search usb" line). A command that has shown
# nothing by the time it elapses is ended by the app (the vendor tools wait far longer), the countdown is refreshed when
# the device is seen, and it is switched off as soon as the download agent or
# the images start moving. SP Flash Tool's own connection window is set to the
# same value through its console configuration file.
ATTEMPT_PROGRESS_TIMEOUT = 60.0
ATTEMPT_PROGRESS_TIMEOUT_MTK = 180.0
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

# Console-mode SP Flash Tool runs this app started and has not reaped yet.
# A cancel normally reaches the live process through the worker, but the
# re-armed USB search spawns a fresh tool every couple of minutes; the pids are
# kept here as well so switching to the desktop tool cannot leave a searching
# flash_tool holding the USB port (or the tool's own settings files) behind.
_SP_SESSION_PIDS: set = set()
_SP_SESSION_LOCK = threading.Lock()


def register_sp_session(pid) -> None:
    """Remember a console-mode tool this app just started."""
    try:
        with _SP_SESSION_LOCK:
            _SP_SESSION_PIDS.add(int(pid))
    except Exception:
        pass


def unregister_sp_session(pid) -> None:
    """Forget a console-mode tool that has exited."""
    try:
        with _SP_SESSION_LOCK:
            _SP_SESSION_PIDS.discard(int(pid))
    except Exception:
        pass


def active_sp_sessions() -> list:
    """Pids of console-mode tool runs this app started and has not seen exit."""
    with _SP_SESSION_LOCK:
        return sorted(_SP_SESSION_PIDS)


def terminate_sp_sessions() -> list:
    """Stop any console-mode tool this app started. Returns the pids it killed.

    Only pids this app spawned are touched, so a desktop tool the user opened
    themselves is never killed; the process tree is taken down as well, because
    flash_tool keeps a child for the device search.
    """
    killed = []
    for pid in active_sp_sessions():
        try:
            if IS_WINDOWS:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except Exception as e:
            logger.debug("Could not stop console tool pid %s: %s", pid, e)
        finally:
            unregister_sp_session(pid)
    if killed:
        logger.info("Stopped leftover SP Flash Tool console runs: %s", killed)
    return killed

# --- MediaTek preloader boot header constants (from vendor macOS build) -------
_BOOT_HEADER_SIZE = 2048
_BRLYT_OFFSET = 512
_PRELOADER_DATA_OFFSET = 2048
_PRELOADER_MAGIC = b"MMM\x01"
# MediaTek BL_Descriptor.m_bl_exist_magic at BRLYT+0x14 (boot1 offset 0x214).
# Must be 0x42424242 ('BBBB'); writing 0x42524C59 ('BRLY') here causes SP Flash
# Tool's DA (EMMC_CheckAndUpdateBLDescriptor) to abort with error 3145
# (S_DA_UPDATE_BOOTLOADER_EXIST_MAGIC_NOT_MATCHED) on subsequent downloads.
_BRLYT_MAGIC = 0x42424242  # 'BBBB' (BL_EXIST_MAGIC)
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
# The bundled legacy DA accepts a WRITE_IMAGE data phase and then stops
# waiting for the final ACK: it returns True and records this note. The Y1 and
# Y2 runs both take 109-111s to reach that point and keep the install, so this
# note is not a failure; any other ``last_error`` next to a True result is.
_BENIGN_WRITE_IMAGE_NOTE = "WRITE_IMAGE final ack timeout after data accepted"
# Per-transfer chunk size handed to the bundled (Chinese-fork) legacy DA. Its
# ``sdmmc_write_data`` reads ``config.write_chunk_size`` for every USB transfer
# and defaults to 1 MiB when unset. The shipped 1.1.7 build's legacy strategy
# drives the same DA with 512 KiB, and 256 KiB when an image is 1.5 GB or larger
# so a big transfer still leaves room for the device to answer between chunks.
# Never raise these: a larger chunk is exactly what makes a slow preloader-mode
# target miss its ACK window mid-partition.
LEGACY_WRITE_CHUNK_BYTES = 524288  # 512 KiB
LEGACY_WRITE_CHUNK_BYTES_LARGE = 262144  # 256 KiB, images >= 1536 MB
LEGACY_WRITE_CHUNK_LARGE_IMAGE_BYTES = 1536 * 1024 * 1024


def legacy_write_chunk_size(image_paths) -> int:
    """Chunk size the reference build writes legacy-DA images with."""
    largest = 0
    for path in image_paths:
        try:
            largest = max(largest, Path(path).stat().st_size)
        except OSError:
            continue
    if largest >= LEGACY_WRITE_CHUNK_LARGE_IMAGE_BYTES:
        return LEGACY_WRITE_CHUNK_BYTES_LARGE
    return LEGACY_WRITE_CHUNK_BYTES


def layman_task_name(part_name: str) -> str:
    """Return user-friendly layman description for a partition or task."""
    low = (part_name or "").lower().strip()
    if low == "usr" or "usr" in low or "user_data" in low or "userdata" in low:
        return tr("task_writing_user_storage")
    if any(k in low for k in ("system", "android", "innios")):
        return tr("task_writing_system")
    if any(k in low for k in ("boot", "bootimg")):
        return tr("task_writing_boot")
    if "recovery" in low:
        return tr("task_writing_recovery")
    if any(k in low for k in ("preloader", "uboot", "lk")):
        return tr("task_writing_bootloader")
    if "logo" in low:
        return tr("task_writing_logo")
    if any(k in low for k in ("userdata", "cache")):
        return tr("task_preparing_storage")
    if any(k in low for k in ("mbr", "ebr", "gpt", "pgpt")):
        return tr("task_writing_partition_table")
    return tr("task_writing_partition_generic")


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


def _scatter_part_label(part) -> str:
    """Partition name from a parsed scatter block or a write-plan entry."""
    return str(part.get("name") or part.get("partition_name") or "")


def _detect_legacy_user_addr_bias(scatter_path, parts=None):
    """Detect whether a scatter file on a legacy platform requires MBR user address biasing."""
    platform_name = _parse_scatter_platform(scatter_path)
    if platform_name not in _LEGACY_MBR_BIAS_PLATFORMS:
        return 0
    if not parts or not isinstance(parts[0], dict):
        parts = _parse_scatter_entries(scatter_path)
    has_boot_preloader = any(
        _scatter_part_label(part).strip().lower() == "preloader"
        and str(part.get("region", "")).strip().upper().startswith("EMMC_BOOT")
        for part in parts
    )
    user_names = {
        _scatter_part_label(part).strip().lower()
        for part in parts
        if str(part.get("region", "")).strip().upper() == "EMMC_USER"
    }
    if has_boot_preloader and {"mbr", "ebr1"}.issubset(user_names):
        return LEGACY_MBR_USER_ADDR_BIAS
    return 0



def _fill_boot_magic(header: bytearray, storage_type: str = "emmc"):
    from struct import pack

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
    header[16:20] = pack("<I", _BRLYT_OFFSET)


def _fill_brlyt_header(header: bytearray, raw_size: int):
    from struct import pack

    image_size = _PRELOADER_DATA_OFFSET + raw_size
    # MediaTek's DA (EMMC_SetupHBDescriptor / EMMC_CheckAndUpdateBLDescriptor)
    # rounds m_bl_boundary_dev_addr up to a 512-byte sector boundary
    # ((begin_dev_addr + bl_size + 0x1FF) & ~0x1FF). Leaving it unaligned causes
    # error 3147 (S_DA_UPDATE_BOOTLOADER_FILE_SIZE_EXCEEDS_BOUNDARY_ADDR) when
    # SP Flash Tool updates a preloader whose size is not a multiple of 512.
    boundary_addr = (image_size + 0x1FF) & ~0x1FF
    header[_BRLYT_OFFSET : _BRLYT_OFFSET + 8] = b"BRLYT\x00\x00\x00"
    header[_BRLYT_OFFSET + 8 : _BRLYT_OFFSET + 12] = pack("<I", 1)
    header[_BRLYT_OFFSET + 12 : _BRLYT_OFFSET + 16] = pack("<I", _PRELOADER_DATA_OFFSET)
    header[_BRLYT_OFFSET + 16 : _BRLYT_OFFSET + 20] = pack("<I", boundary_addr)
    header[_BRLYT_OFFSET + 20 : _BRLYT_OFFSET + 24] = pack("<I", _BRLYT_MAGIC)
    header[_BRLYT_OFFSET + 24 : _BRLYT_OFFSET + 28] = pack("<I", _BRLYT_TYPE_EMMC)
    header[_BRLYT_OFFSET + 28 : _BRLYT_OFFSET + 32] = pack("<I", _PRELOADER_DATA_OFFSET)
    header[_BRLYT_OFFSET + 32 : _BRLYT_OFFSET + 36] = pack("<I", boundary_addr)
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


class PackageLayoutError(RuntimeError):
    """The package cannot be written to this device's layout.

    Raised before a write starts: an image (or a sparse image's data extents)
    reaches past the partition the scatter describes. Handing that to the DA is
    what produces a NACK near the tail of the partition, with the player left
    half written and no useful diagnosis.
    """


def _sparse_data_end(regions) -> int:
    """End offset (in the raw image) of the last sparse data extent."""
    return max((int(off) + int(length) for off, length in regions or ()), default=0)


def _clip_sparse_regions(regions, bound: int):
    """Regions that fit inside ``bound``, plus whether any tail was dropped.

    Fill extents are DONT_CARE, so a sparse image whose data reaches past the
    device's partition can stop at the partition edge instead of NACKing the
    DA mid-transfer. Returns ``(regions, clipped)``.
    """
    if not bound:
        return list(regions or ()), False
    kept = []
    clipped = False
    for region_off, region_len in regions or ():
        if region_off >= bound:
            clipped = True
            continue
        inside = min(int(region_len), bound - int(region_off))
        if inside != region_len:
            clipped = True
        if inside > 0:
            kept.append((int(region_off), inside))
    return kept, clipped


def _parse_mbr_partitions(data: bytes, sector: int = 512) -> list:
    """Absolute ``(start, size)`` of every partition an MBR/EBR chain describes.

    The Timmkoo/Innioasis players are MBR-partitioned, so this is the only way
    to learn their live layout: the scatter describes the layout a package was
    built for, not the one the player currently has. Entries are returned in
    address order; a chain that cannot be parsed yields an empty list.

    In an EBR the logical entry is relative to the extended partition's base
    (the MBR's own extended entry), which is what the players' tables use.
    """
    data = data or b""
    if len(data) < sector or data[510:512] != b"\x55\xaa":
        return []

    def _entries(buf):
        out = []
        for index in range(4):
            entry = buf[446 + 16 * index : 462 + 16 * index]
            if len(entry) < 16:
                break
            ptype = entry[4]
            lba, count = unpack("<II", entry[8:16])
            out.append((ptype, lba, count))
        return out

    partitions = []
    extended_base = 0
    for ptype, lba, count in _entries(data[:sector]):
        if ptype in (0x05, 0x0F, 0x85):
            extended_base = extended_base or lba * sector
            continue
        if ptype and count and count < 0xFFFFFFF0:
            partitions.append((lba * sector, count * sector))

    ebr = extended_base
    seen = set()
    while ebr and ebr not in seen and len(data) >= ebr + sector:
        seen.add(ebr)
        entries = _entries(data[ebr : ebr + sector])
        if not entries:
            break
        # MTK puts every logical partition in EBR1 (all four slots are
        # partitions, no chaining); a classic chain, when present, uses one
        # slot as the link to the next EBR.
        next_ebr = 0
        for ptype, lba, count in entries:
            if ptype in (0x05, 0x0F, 0x85):
                next_ebr = extended_base + lba * sector
                continue
            if ptype and count and count < 0xFFFFFFF0:
                partitions.append(
                    (extended_base + lba * sector, count * sector)
                )
        ebr = next_ebr if next_ebr and next_ebr not in seen else 0
    return sorted(partitions)


def _normalize_platform(name) -> str:
    """Chip-number form of a platform name: ``MT6572``/``MT6572G`` -> ``6572``.

    The device reports its own name in whatever form its DA uses
    (``MT6572``, ``MT6572/MT6582 Modem``, ...), so comparing the raw strings
    would flag a matching package as a mismatch.
    """
    text = str(name or "").strip().upper()
    if not text:
        return ""
    if text.startswith("MT"):
        text = text[2:]
    text = text.split("/")[0]
    match = re.search(r"\d+", text)
    return match.group(0) if match else text


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
    from .downloads import downloads_dir
    dd = downloads_dir()
    if package.is_file() and (package.suffix.lower() == ".txt" or "scatter" in package.name.lower()):
        return package.parent
    if package.is_dir():
        sc = _find_scatter(package, allow_raise=False)
        if sc:
            return sc.parent
        return package
    return dd / f".{package.stem}_extracted"


def completed_extract_dir(package_path):
    """Return the extract dir for ``package_path`` when a previous run already
    finished extracting it, else ``""`` (so callers can skip re-extraction —
    the firmware is already on disk before a backend switch / MTKClient run)."""
    if not package_path:
        return ""
    p = Path(package_path)
    if p.exists() and p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower()):
        return str(p.parent)
    if p.exists() and p.is_dir():
        sc = _find_scatter(p, allow_raise=False)
        return str(sc.parent if sc else p)
    # The archive may be gone once it has been extracted; the extracted folder is still the source.
    d = compute_extract_dir(package_path)
    return str(d) if _is_extract_complete(d) else ""


def _is_extract_complete(extract_dir):
    d = Path(extract_dir)
    if not d.exists() or not d.is_dir():
        return False
    if not d.name.endswith("_extracted"):
        return True
    if (d / EXTRACT_COMPLETE_MARKER).exists():
        return True
    return _find_scatter(d, allow_raise=False) is not None


def _mark_extract_complete(extract_dir):
    try:
        (extract_dir / EXTRACT_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    except Exception:
        pass


def prune_extracted_cache(keep_package_path: Optional[str] = None) -> list:
    """Keep only the set of firmware files for ``keep_package_path``.

    Called only after a different firmware package has finished extracting, so
    its files are on disk before older extracted folders are replaced. Selecting
    a package, installs, failures, retries and device prep never call this.

    Returns a list of paths that were removed.
    """
    removed = []
    try:
        from .downloads import downloads_dir
        dd = downloads_dir()
        if not dd.is_dir():
            return removed
        keep_stem = Path(keep_package_path).stem if keep_package_path else ""
        keep_dir_name = f".{keep_stem}_extracted" if keep_stem else ""
        keep_archive = Path(keep_package_path).name if keep_package_path else ""

        for item in list(dd.iterdir()):
            if item.is_file() and item.suffix.lower() in (".zip", ".rar") and item.name != keep_archive:
                logger.info("Removing leftover firmware archive: %s", item.name)
                try:
                    item.unlink()
                    removed.append(str(item))
                except Exception as e:
                    logger.warning("Could not remove archive %s: %s", item, e)
            elif item.is_dir() and item.name.startswith(".") and item.name.endswith("_extracted"):
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


def _calc_dir_size(d: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(str(d)):
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += _calc_dir_size(Path(entry.path))
    except (OSError, PermissionError):
        pass
    return total


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

    def _extract(self, package_path, force=False):
        p = Path(package_path)
        is_scatter_file = p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower())
        if is_scatter_file or p.is_dir():
            source_dir = p.parent if is_scatter_file else p
            from .sp_flash_gui import discard_firmware_history
            sc = _find_scatter(source_dir, allow_raise=True)
            work_dir = sc.parent if sc else source_dir
            discard_firmware_history(work_dir)
            self.progress.emit(100)
            return work_dir

        extract_dir = compute_extract_dir(package_path)
        from .sp_flash_gui import discard_firmware_history
        if not force and _is_extract_complete(extract_dir):
            self.progress.emit(100)
            discard_firmware_history(extract_dir)
            return extract_dir
        # Cached files are never deleted here: a forced re-extract overwrites them in place.
        extract_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(package_path).suffix.lower()
        if suffix == ".zip":
            self._extract_zip(package_path, extract_dir)
        else:
            self._extract_rar(package_path, extract_dir)
        # A package that ships fw_package.ini keeps only its scatter and listed files.
        prune_to_firmware_package(extract_dir)
        discard_firmware_history(extract_dir)
        _mark_extract_complete(extract_dir)
        # The archive is no longer needed once its files are extracted.
        try:
            Path(package_path).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove extracted archive %s: %s", package_path, e)
        # Only now, with the new firmware on disk, may older extractions be replaced.
        prune_extracted_cache(keep_package_path=package_path)
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
                            # Let the GUI thread take the GIL and paint the bar.
                            QThread.msleep(1)
            self.progress.emit(99)
            # Do not leave the bar pinned at 99% after the last file.
            # The user saw a real stall every time the final archive entry was
            # large (the last chunk writes finish long after the 99 tick), which
            # read as a hang even though extraction was only still flushing the
            # biggest file. Emit a 100 only after every entry is on disk, then
            # report the real total so the next run prepopulates history.ini from
            # the finished extract rather than the cached zip path.
            self.progress.emit(100)

    def _extract_rar(self, package, extract_dir):
        unrar = paths.find_unrar()
        if not unrar:
            raise RuntimeError(
                "UnRAR / unar tool not found. On Windows place UnRAR.exe in tools/; on macOS run 'brew install unar'; on Linux run 'sudo apt install unar'"
            )
        is_unar = "unar" in Path(unrar).stem.lower()
        if is_unar:
            cmd = [unrar, "-f", "-o", str(extract_dir), str(package)]
        else:
            cmd = [unrar, "x", "-o+", "-y", str(package), str(extract_dir) + os.sep]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_SUBPROCESS_NO_WINDOW,
        )
        buf = b""
        last_pct = -1
        stop_event = threading.Event()
        pkg_size = max(1, os.path.getsize(package))

        def _dir_monitor():
            nonlocal last_pct
            while not stop_event.wait(0.2) and not self._cancelled:
                try:
                    cur = _calc_dir_size(Path(extract_dir))
                    pct = min(95, max(1, int(cur * 95 / pkg_size)))
                    if pct > last_pct:
                        last_pct = pct
                        self.progress.emit(pct)
                except Exception:
                    pass

        monitor_thread = threading.Thread(target=_dir_monitor, daemon=True, name="RarExtractMonitor")
        monitor_thread.start()

        try:
            while not self._cancelled:
                raw = proc.stdout.read1(1024) if hasattr(proc.stdout, "read1") else proc.stdout.read(1024)  # type: ignore[union-attr]
                if not raw:
                    break
                buf += raw
                for m in re.finditer(rb"(\d+)%", buf):
                    pct = min(99, int(m.group(1)))
                    if pct > last_pct:
                        last_pct = pct
                        self.progress.emit(pct)
                        QThread.msleep(1)
                if len(buf) > 4096:
                    buf = buf[-256:]
        finally:
            stop_event.set()
            monitor_thread.join(timeout=1.0)

        if self._cancelled:
            proc.terminate()
            return
        proc.wait()
        if proc.returncode != 0:
            tool_name = "unar" if is_unar else "UnRAR"
            raise RuntimeError(f"{tool_name} exit code {proc.returncode}")
        self.progress.emit(100)


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
        from . import catalog
        plat, _ = catalog.parse_scatter_header(scatter_path)
        if plat:
            return plat
    except Exception:
        pass
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
    # ``-c format-download`` is Format All + Download: the reference Chinese
    # 1.1.7 build runs this unconditionally (see analysis_cn/_flash_via_sp_flash_tool.dis.txt).
    # Full erase before download avoids S_DA_UPDATE_BOOTLOADER_EXIST_MAGIC_NOT_MATCHED (3145)
    # when target NAND/eMMC does not have matching DSP_BL headers.
    return [
        "-c", "format-download",
        "-s", scatter_arg,
        "-d", da_arg,
        "-t", "without",
        "-r",
    ]


_SP_BOOTLOADER_HEADER_ERROR_CODES = frozenset({"3145", "3146", "3147", "3148"})
_SP_BOOTLOADER_HEADER_ERROR_MARKERS = (
    "EXIST_MAGIC_NOT_MATCHED",
    "EXIST_TYPE_NOT_MATCHED",
    "FILE_SIZE_EXCEEDS_BOUNDARY_ADDR",
    "BEGIN_ADDR_NOT_MATCHED",
    "(3145)",
    "(3146)",
    "(3147)",
    "(3148)",
    "err_code[3145]",
    "err_code[3146]",
    "err_code[3147]",
    "err_code[3148]",
)


def _is_sp_bootloader_header_mismatch(error_detail="", err_code=None) -> bool:
    """True when SP Flash Tool's DA aborted CMD_DownloadBootLoader because the
    existing boot1 BRLYT descriptor had a legacy magic/boundary (3145-3148)."""
    code_str = str(err_code or "").strip()
    if code_str in _SP_BOOTLOADER_HEADER_ERROR_CODES:
        return True
    detail_up = str(error_detail or "").upper()
    return any(marker.upper() in detail_up for marker in _SP_BOOTLOADER_HEADER_ERROR_MARKERS)


def _write_scatter_without_preloader(scatter_path) -> Optional[Path]:
    """Create a staged scatter file in the same directory with PRELOADER
    ``is_download: false`` so SP Flash Tool skips CMD_DownloadBootLoader when
    boot1 already has a working preloader with a legacy BRLYT descriptor."""
    scatter_p = Path(scatter_path).resolve()
    try:
        lines = scatter_p.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
    except OSError:
        return None

    in_preloader = False
    changed = False
    out_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- partition_index:") or stripped.startswith("partition_index:"):
            in_preloader = False
        elif stripped.startswith("partition_name:"):
            part_name = stripped.split(":", 1)[1].strip().upper()
            in_preloader = part_name in ("PRELOADER", "DSP_BL")
        elif in_preloader and stripped.startswith("is_download:"):
            val = stripped.split(":", 1)[1].strip().lower()
            if val == "true":
                prefix = line[: line.lower().find("true")]
                ending = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
                line = f"{prefix}false{ending}"
                changed = True
                in_preloader = False
        out_lines.append(line)

    if not changed:
        return None

    out_path = scatter_p.parent / f"{scatter_p.stem}_no_preloader{scatter_p.suffix}"
    try:
        out_path.write_text("".join(out_lines), encoding="utf-8")
        return out_path
    except OSError:
        return None


def _resolve_image_file(scatter_dir, fname):
    scatter_dir = Path(scatter_dir).resolve()
    clean_fname = Path(str(fname).replace("\\", "/")).name
    exact = (scatter_dir / fname).resolve()
    if exact.is_file():
        return exact
    exact_clean = (scatter_dir / clean_fname).resolve()
    if exact_clean.is_file():
        return exact_clean

    stem, ext = os.path.splitext(clean_fname)
    base_stem = re.sub(r"-(?:verified|sign)$", "", stem, flags=re.IGNORECASE)
    candidate_stems = [stem, base_stem, f"{base_stem}-verified", f"{base_stem}-sign"]
    for c_stem in candidate_stems:
        variant = (scatter_dir / f"{c_stem}{ext}").resolve()
        if variant.is_file():
            return variant

    fname_lower = clean_fname.lower()
    candidate_stems_lower = {s.lower() for s in candidate_stems}
    ext_lower = ext.lower()

    # Direct scan in scatter_dir (case-insensitive)
    try:
        for f in scatter_dir.iterdir():
            if f.is_file():
                if f.name.lower() == fname_lower:
                    return f.resolve()
                if f.stem.lower() in candidate_stems_lower and f.suffix.lower() == ext_lower:
                    return f.resolve()
    except Exception:
        pass

    # Recursive scan in scatter_dir and parent directory (arbitrary firmware layouts)
    search_dirs = [scatter_dir]
    if scatter_dir.parent.is_dir() and scatter_dir.parent != scatter_dir:
        search_dirs.append(scatter_dir.parent)
    for s_dir in search_dirs:
        try:
            for f in s_dir.rglob("*"):
                if f.is_file():
                    if f.name.lower() == fname_lower:
                        return f.resolve()
                    if f.stem.lower() in candidate_stems_lower and f.suffix.lower() == ext_lower:
                        return f.resolve()
        except Exception:
            pass

    return exact


def align_scatter_disk_images(scatter_path, log=None):
    """Ensure all declared partition image files in the scatter exist on disk.

    If an image is declared in the scatter as e.g. 'lk.bin', but only 'lk-sign.bin'
    exists on disk (or vice versa), creates a link/symlink or copy so SP Flash Tool
    and disk writers can resolve the declared filename without S_FILE_NOT_FOUND.
    """
    scatter_path = Path(scatter_path).resolve()
    scatter_dir = scatter_path.parent
    try:
        content = scatter_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        if log:
            log(f"Warning: could not read scatter file {scatter_path.name}: {e}")
        return

    declared_files = set()
    current = {}
    for raw in content.split("\n"):
        line = raw.strip()
        if line.startswith("- partition_index:"):
            if current.get("is_download", "").lower() == "true":
                fname = current.get("file_name", "NONE")
                if fname and fname != "NONE":
                    declared_files.add(fname)
            current = {}
            continue
        if ":" in line and not line.startswith("#"):
            k, _, v = line.partition(":")
            current[k.strip().lstrip("- ")] = v.strip()
    if current.get("is_download", "").lower() == "true":
        fname = current.get("file_name", "NONE")
        if fname and fname != "NONE":
            declared_files.add(fname)

    for fname in declared_files:
        clean_fname = Path(str(fname).replace("\\", "/")).name
        target_path = scatter_dir / clean_fname
        if target_path.exists():
            continue
        resolved = _resolve_image_file(scatter_dir, fname)
        if resolved and resolved.exists() and resolved != target_path:
            try:
                target_path.symlink_to(resolved)
                if log:
                    log(f"Aliased {resolved.name} -> {clean_fname}")
            except OSError:
                try:
                    os.link(str(resolved), str(target_path))
                    if log:
                        log(f"Linked {resolved.name} -> {clean_fname}")
                except OSError:
                    try:
                        shutil.copy2(str(resolved), str(target_path))
                        if log:
                            log(f"Copied {resolved.name} -> {clean_fname}")
                    except Exception as e:
                        if log:
                            log(f"Warning: could not alias {resolved.name} -> {clean_fname}: {e}")


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


def stage_firmware_for_flashing(
    scatter_path: Union[str, Path],
    target_dir: Optional[Union[str, Path]] = None,
) -> Path:
    """Stage firmware files into an isolated working directory under cache/downloads
    before SP Flash Tool / MTKClient work on them, protecting the app's own directory
    (_internal, binaries) from being overwritten or modified when extracting/flashing.

    Only the scatter file, the declared partition image files, any *.auth file, and
    fw_package.ini / icon are staged. Returns the path to the staged scatter file.
    """
    scatter_p = Path(scatter_path).resolve()
    src_folder = scatter_p.parent
    if target_dir is None:
        import hashlib
        h = hashlib.sha256(str(src_folder).encode("utf-8")).hexdigest()[:12]
        dest_folder = downloads_dir() / "staged_firmware" / f"stage_{h}_{scatter_p.stem}"
    else:
        dest_folder = Path(target_dir).resolve()

    if src_folder == dest_folder:
        return scatter_p

    dest_folder.mkdir(parents=True, exist_ok=True)
    dest_scatter = dest_folder / scatter_p.name

    # Always stage the scatter file if not already present or if modified
    if not dest_scatter.exists() or dest_scatter.stat().st_size != scatter_p.stat().st_size:
        shutil.copy2(scatter_p, dest_scatter)

    # Collect declared images to stage
    for _name, img_path in _list_expected_images(scatter_p):
        if img_path.is_file():
            dest_img = dest_folder / img_path.name
            if not dest_img.exists() or dest_img.stat().st_size != img_path.stat().st_size:
                try:
                    shutil.copy2(img_path, dest_img)
                except Exception as e:
                    logger.warning("Could not stage image %s: %s", img_path.name, e)

    # Stage any .auth or DA files present in source folder
    for p in src_folder.glob("*.auth"):
        dest_auth = dest_folder / p.name
        if not dest_auth.exists() or dest_auth.stat().st_size != p.stat().st_size:
            shutil.copy2(p, dest_auth)

    for ini_name in ("fw_package.ini", "updater.png", "updater.jpg", "updater.jpeg"):
        f = src_folder / ini_name
        if f.is_file():
            dest_f = dest_folder / ini_name
            if not dest_f.exists() or dest_f.stat().st_size != f.stat().st_size:
                shutil.copy2(f, dest_f)

    return dest_scatter



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
    - order: position of the entry in the scatter file (the write order the
      reference build uses)
    """
    raw_parts = _read_scatter_parts(scatter_path)
    if not raw_parts:
        return []
    return _scatter_entries_from_parts(raw_parts, scatter_path)


def _read_scatter_parts(scatter_path):
    """Split a scatter file into one dict per ``- partition_index:`` block."""
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
    return raw_parts


def _legacy_scatter_has_mbr_ebr(parts) -> bool:
    """Return True if the parsed scatter contains legacy MBR/EBR partition definitions."""
    names = {
        str(part.get("partition_name", "")).strip().lower()
        for part in parts
        if str(part.get("region", "")).strip().upper() == "EMMC_USER"
        or not str(part.get("region", "")).strip()
    }
    return {"mbr", "ebr1"}.issubset(names)


def _scatter_hex_int(value):
    """Parse a scatter hex field, or None when it is absent/unparseable.

    The reference build's ``_parse_scatter_hex_int`` returns None rather than
    0: its address mode decision treats a missing physical address differently
    from a present one.
    """
    try:
        return int(str(value).strip(), 16)
    except (TypeError, ValueError):
        return None


def _is_scatter_tail_addr(value) -> bool:
    """True for an address relative to the *end* of the user area (0xFFFF0000+)."""
    return value is not None and 0xFFFF0000 <= value <= 0xFFFFFFFF


def _scatter_user_physical_addresses_are_usable(raw_parts) -> bool:
    """Whether the scatter's own ``physical_start_addr`` column can address writes.

    Ported from the shipped 1.1.7 build's
    ``FlashWorker._scatter_user_physical_addresses_are_usable``: over the
    non-reserved EMMC_USER entries the physical column must hold at least two
    distinct non-zero values, and they must ascend in scatter order
    (tail-relative addresses excluded). The Y1's stock scatter zeroes every
    physical address and fails this test, which is what sends the reference
    down its linear-address path.
    """
    values = []
    for part in raw_parts:
        if str(part.get("region", "")).strip().upper() != "EMMC_USER":
            continue
        if str(part.get("is_reserved", "")).strip().lower() == "true":
            continue
        values.append(_scatter_hex_int(part.get("physical_start_addr", "")))
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return False
    if len({v for v in values if v != 0}) < 2:
        return False
    ordered = [v for v in values if not _is_scatter_tail_addr(v)]
    return ordered == sorted(ordered)


def _scatter_entries_from_parts(raw_parts, scatter_path=None):
    """Turn parsed scatter blocks into write-plan entries (shared parser).

    The write address is the shipped 1.1.7 build's
    ``FlashWorker._resolve_scatter_user_addr`` verbatim:

    * physical column usable (real Y2-style scatter) → ``physical_start_addr
      + legacy bias`` — the 8 MB ``LEGACY_MBR_USER_ADDR_BIAS`` that maps the
      scatter's user-relative physical address onto the device's absolute
      address;
    * otherwise (the Y1's stock scatter zeroes every physical address) →
      ``linear_start_addr`` itself, the reference's "readback calibration not
      found; using original linear address" path. A readback calibration
      cannot exist before the device is connected, and the reference's own
      flash logs confirm it: ``start=0x1400000`` for MBR — the scatter's
      linear address, never linear minus a base.
    """
    physical_usable = _scatter_user_physical_addresses_are_usable(raw_parts)
    addr_bias = 0
    if physical_usable and scatter_path is not None:
        addr_bias = _detect_legacy_user_addr_bias(scatter_path, raw_parts)

    entries = []
    for order, p in enumerate(raw_parts):
        pname = p.get("partition_name", "")
        if not pname:
            continue
        fname = p.get("file_name", "NONE")
        region = p.get("region", "EMMC_USER").upper()
        lin = _to_int_safe(p.get("linear_start_addr", 0))
        phys = _to_int_safe(p.get("physical_start_addr", 0))
        size = _to_int_safe(p.get("partition_size", 0))
        is_dl = p.get("is_download", "").lower() == "true"

        # MTK scatters mark addresses at/above 0xFFFF0000 as relative to the
        # *end* of the user area (BMTPOOL uses 0xFFFF00a8). They can only be
        # resolved against the live device's user-area size, so they never get
        # a scatter address of their own in the plan.
        tail_relative = bool(
            region == "EMMC_USER"
            and (_is_scatter_tail_addr(phys) or (phys <= 0 and _is_scatter_tail_addr(lin)))
        )

        if tail_relative:
            offset = 0
        elif region in ("EMMC_BOOT_1", "EMMC_BOOT_2"):
            offset = phys
        elif physical_usable:
            offset = phys + addr_bias
        else:
            offset = max(0, lin)

        entries.append({
            "name": pname,
            "file_name": fname,
            "is_download": is_dl,
            "region": region,
            "linear_start_addr": lin,
            "physical_start_addr": phys,
            "partition_size": size,
            "offset": offset,
            "tail_relative": tail_relative,
            "order": order,
        })
    return entries


# --- reference write plan (parity with the shipped 1.1.7 build) --------------
# Updater 1.1.7 is the build that flashes reliably on macOS through the same
# legacy MTK DA we bundle. Its plan is the package scatter, in scatter order,
# with the preloader deferred to the very end:
#
#   Every downloadable image is written by scatter address, so the device's
#   own partition table is never consulted (mtkclient enumerates GPT only, and
#   the Timmkoo/Innioasis players are MBR-partitioned — a named lookup returns
#   nothing and the install dies with "Couldn't detect partition").
#
#   Y1-style scatters report physical_start_addr 0x0 for every EMMC_USER
#   entry, so the reference writes each image at the scatter's own
#   linear_start_addr (its flash logs: MBR start=0x1400000, UBOOT 0x3120000,
#   ANDROID 0x4B40000 — the linear column verbatim). Y2-style scatters carry a
#   usable physical column, written as physical + LEGACY_MBR_USER_ADDR_BIAS.
#
#   The preloader is written last, wrapped in the BootROM's EMMC_BOOT/BRLYT
#   header through the DA's WRITE_IMAGE command. Writing it first (or writing
#   the bare image) is what leaves a player the BootROM no longer recognises;
#   written last, a run that dies earlier still leaves a player that enumerates
#   for another try. Nothing formats a partition, rewrites the device's MBR/EBR
#   or duplicates the preloader into eMMC boot2.
#
# Partition-table scaffolding that belongs to the device, not to the package.
# The stock zips ship 512-byte all-zero MBR/EBR stubs; writing one over the real
# table leaves a player that cannot find a single partition.
MTK_DEVICE_TABLE_PARTS = frozenset(
    {"mbr", "ebr", "ebr1", "ebr2", "ebr3", "pgpt", "sgpt", "gpt", "pmt"}
)
MTK_DEVICE_TABLE_FILES = frozenset(
    {"mbr", "ebr", "ebr1", "ebr2", "ebr3"}
)


def _is_device_table_partition(partition_name, filename="") -> bool:
    """True for MBR/EBR/PMT-style regions that must stay device-local."""
    name = (partition_name or "").strip().lower()
    fname = Path(str(filename or "")).name.strip().lower()
    if name in MTK_DEVICE_TABLE_PARTS or fname in MTK_DEVICE_TABLE_FILES:
        return True
def _build_protective_mbr(user_size: int, pagesize: int = 512) -> bytes:
    """Build a protective MBR (LBA 0) with a single type 0xEE partition spanning the disk."""
    disk_lbas = max(1, user_size // pagesize)
    mbr = bytearray(pagesize)
    mbr[446] = 0        # boot indicator
    mbr[450] = 0xEE     # OS type: EFI GPT
    mbr[454:458] = pack("<I", 1)  # starting LBA
    mbr[458:462] = pack("<I", min(max(1, disk_lbas - 1), 0xFFFFFFFF))  # size in LBA
    mbr[510:512] = b"\x55\xAA"   # signature
    return bytes(mbr)


def _build_gpt_header(
    pagesize: int,
    current_lba: int,
    backup_lba: int,
    first_usable_lba: int,
    last_usable_lba: int,
    disk_guid: bytes,
    entry_lba: int,
    num_entries: int,
    entry_size: int,
    entry_crc: int,
) -> bytes:
    """Build a standard 92-byte GPT header aligned to pagesize."""
    header_size = 92
    header = bytearray(pagesize)
    header[0:8] = b"EFI PART"
    header[8:12] = pack("<I", 0x00010000)
    header[12:16] = pack("<I", header_size)
    header[16:20] = pack("<I", 0)  # CRC zeroed for calculation
    header[20:24] = pack("<I", 0)
    header[24:32] = pack("<Q", current_lba)
    header[32:40] = pack("<Q", backup_lba)
    header[40:48] = pack("<Q", first_usable_lba)
    header[48:56] = pack("<Q", last_usable_lba)
    header[56:72] = (disk_guid or b"\x00" * 16)[:16]
    header[72:80] = pack("<Q", entry_lba)
    header[80:84] = pack("<I", num_entries)
    header[84:88] = pack("<I", entry_size)
    header[88:92] = pack("<I", entry_crc)
    header_crc = zlib.crc32(bytes(header[:header_size])) & 0xFFFFFFFF
    header[16:20] = pack("<I", header_crc)
    return bytes(header)


def _stable_partition_guid(name: str) -> bytes:
    """Generate deterministic partition GUID from partition name."""
    namespace = uuid.UUID("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")
    return uuid.uuid5(namespace, (name or "").strip().lower()).bytes_le


def _parse_scatter_gpt_reserved_sizes(scatter_path) -> Tuple[int, int]:
    """Return (pgpt_size, sgpt_size) in bytes from scatter."""
    pgpt_size = 0
    sgpt_size = 0
    for part in _read_scatter_parts(scatter_path):
        if (part.get("region") or "").strip().upper() != "EMMC_USER":
            continue
        name = (part.get("partition_name") or "").strip().lower()
        size = _to_int_safe(part.get("partition_size", 0))
        if name == "pgpt":
            pgpt_size = size
        elif name == "sgpt":
            sgpt_size = size
    return pgpt_size, sgpt_size


def _parse_scatter_all_user_partitions(scatter_path, user_size: int = 0):
    """Parse all EMMC_USER partitions from scatter for GPT generation/sync.
    Returns {partition_name_lower: (byte_start_address, byte_size)}.
    """
    parts = _read_scatter_parts(scatter_path)
    if not parts:
        return {}
    entries = _scatter_entries_from_parts(parts)
    result = {}
    tail_reserved = []
    userdata_key = None

    for entry in entries:
        if (entry.get("region") or "").upper() != "EMMC_USER":
            continue
        pname = (entry.get("name") or "").strip().lower()
        if pname in ("gpt", "pgpt", "sgpt", "bmtpool"):
            continue

        raw_addr = _to_int_safe(entry.get("physical_start_addr", 0)) or _to_int_safe(entry.get("linear_start_addr", 0))
        size = _to_int_safe(entry.get("partition_size", 0))

        if raw_addr >= 0xFFFF0000:
            if user_size > 0:
                offset_from_end = 0x100000000 - raw_addr
                addr = max(0, user_size - offset_from_end)
                tail_reserved.append((pname, addr, size))
            continue

        addr = entry.get("offset", raw_addr)
        if pname in ("userdata", "usrdata"):
            userdata_key = pname
        result[pname] = (addr, size)

    tail_bound = user_size
    for pname, addr, size in sorted(tail_reserved, key=lambda x: x[1]):
        result[pname] = (addr, size)
        if addr < tail_bound:
            tail_bound = addr

    if userdata_key and userdata_key in result and user_size > 0:
        ud_addr, ud_size = result[userdata_key]
        if tail_bound > ud_addr:
            result[userdata_key] = (ud_addr, tail_bound - ud_addr)

    return result


def _get_emmc_user_size(mtk) -> int:
    """Read eMMC user-area capacity from DA configuration."""
    daloader = getattr(mtk, "daloader", None)
    daconfig = getattr(daloader, "daconfig", None)
    legacy = getattr(daconfig, "legacy_storage", None)
    if legacy is not None and getattr(legacy, "emmc", None) is not None:
        size = int(getattr(legacy.emmc, "m_emmc_ua_size", 0) or 0)
        if size > 0:
            return size
    storage = getattr(daconfig, "storage", None)
    emmc = getattr(storage, "emmc", None)
    if emmc is not None:
        for attr in ("user_size", "m_emmc_ua_size", "ua_size", "m_emmc_user_size"):
            val = int(getattr(emmc, attr, 0) or 0)
            if val > 0:
                return val
    return 0


def _infer_user_size_from_gpt_blob(raw: bytes, pagesize: int = 512) -> int:
    """Infer eMMC user disk capacity from backup LBA field in primary GPT header."""
    if raw is None or len(raw) < pagesize + 92:
        return 0
    if raw[pagesize : pagesize + 8] != b"EFI PART":
        return 0
    backup_lba = unpack("<Q", raw[pagesize + 32 : pagesize + 40])[0]
    if backup_lba <= 34:
        return 0
    return (backup_lba + 1) * pagesize


def _build_gpt_blobs_from_scatter(
    scatter_file,
    user_size: int,
    pagesize: int = 512,
    existing_gpt_blob: Optional[bytes] = None,
):
    """Build primary GPT, backup GPT, and protective MBR blobs directly from scatter layout."""
    scatter_parts = _parse_scatter_all_user_partitions(scatter_file, user_size)
    if not scatter_parts:
        raise RuntimeError("GPT generation failed: no valid EMMC_USER partition found in scatter")
    if user_size <= pagesize * 34:
        raise RuntimeError("GPT generation failed: failed to get valid EMMC_USER capacity")

    pgpt_size, sgpt_size = _parse_scatter_gpt_reserved_sizes(scatter_file)
    first_usable_lba = max(34, (pgpt_size // pagesize) if pgpt_size else 34)
    disk_last_lba = (user_size // pagesize) - 1
    last_usable_lba = disk_last_lba - max(1, (sgpt_size // pagesize) if sgpt_size else 33)

    if last_usable_lba <= first_usable_lba:
        raise RuntimeError("GPT generation failed: usable LBA range is invalid")

    scatter_entries = sorted(scatter_parts.items(), key=lambda item: item[1][0])
    entry_size = 128
    num_entries = max(128, len(scatter_entries))
    entries_len = num_entries * entry_size
    entries_sectors = (entries_len + pagesize - 1) // pagesize
    primary_entry_lba = 2
    backup_entry_lba = disk_last_lba - entries_sectors

    entry_array = bytearray(entries_sectors * pagesize)
    type_guid = uuid.UUID("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")

    new_cache = {}
    for index, (name, (addr, size)) in enumerate(scatter_entries):
        first_lba = addr // pagesize
        last_lba = (addr + size) // pagesize - 1
        if first_lba < first_usable_lba:
            first_lba = first_usable_lba
        if last_lba > last_usable_lba:
            last_lba = last_usable_lba
        if first_lba > last_lba:
            continue

        new_cache[name] = (first_lba * pagesize, (last_lba - first_lba + 1) * pagesize)

        entry_off = index * entry_size
        entry_array[entry_off : entry_off + 16] = type_guid.bytes_le
        entry_array[entry_off + 16 : entry_off + 32] = _stable_partition_guid(name)
        entry_array[entry_off + 32 : entry_off + 40] = pack("<Q", first_lba)
        entry_array[entry_off + 40 : entry_off + 48] = pack("<Q", last_lba)
        enc_name = name.encode("utf-16-le")[:72]
        entry_array[entry_off + 56 : entry_off + 56 + len(enc_name)] = enc_name

    entry_crc = zlib.crc32(bytes(entry_array[:entries_len])) & 0xFFFFFFFF
    disk_guid = b"\x00" * 16

    primary_header = _build_gpt_header(
        pagesize=pagesize,
        current_lba=1,
        backup_lba=disk_last_lba,
        first_usable_lba=first_usable_lba,
        last_usable_lba=last_usable_lba,
        disk_guid=disk_guid,
        entry_lba=primary_entry_lba,
        num_entries=num_entries,
        entry_size=entry_size,
        entry_crc=entry_crc,
    )

    backup_header = _build_gpt_header(
        pagesize=pagesize,
        current_lba=disk_last_lba,
        backup_lba=1,
        first_usable_lba=first_usable_lba,
        last_usable_lba=last_usable_lba,
        disk_guid=disk_guid,
        entry_lba=backup_entry_lba,
        num_entries=num_entries,
        entry_size=entry_size,
        entry_crc=entry_crc,
    )

    primary_len = (primary_entry_lba + entries_sectors) * pagesize
    primary_blob = bytearray(primary_len)
    primary_blob[:pagesize] = _build_protective_mbr(user_size, pagesize)
    primary_blob[pagesize : 2 * pagesize] = primary_header
    entries_start = primary_entry_lba * pagesize
    primary_blob[entries_start : entries_start + len(entry_array)] = entry_array

    backup_blob = bytes(entry_array) + backup_header
    backup_addr = backup_entry_lba * pagesize

    return bytes(primary_blob), backup_addr, backup_blob, new_cache


def should_probe_live_table(plan, daloader=None) -> bool:
    """Whether to read the device's own MBR/EBR chain before writing.

    The probe is a ``readflash`` issued before the first write. The bundled
    Chinese-fork legacy DA does not answer that request on these players: the
    device stops responding four seconds later and drops off the USB bus, which
    is exactly what was killing runs right after Stage 2 on macOS. The 1.1.7
    build writes the package's layout without reading the table back, so a
    legacy DA session skips the probe entirely.
    """
    if not plan:
        return False
    if daloader is not None and type(daloader).__name__ == "DALegacy":
        return False
    return True


def _is_empty_placeholder_image(path, sample_max: int = 65536) -> bool:
    """True for an all-zero image (a stock MBR/EBR stub, typically 512 bytes).

    Only small files are read: a real system/userdata image is hundreds of MB
    and can never be a table stub, so there is no reason to load it.
    """
    try:
        if Path(path).stat().st_size > sample_max:
            return False
        data = Path(path).read_bytes()
    except OSError:
        return False
    if not data:
        return True
    if len(data) >= 512 and data[510:512] == b"\x55\xaa":
        body = data[:510] + data[512:]
    elif len(data) <= 512 and data[-2:] == b"\x55\xaa":
        body = data[:-2]
    else:
        body = data
    return not any(body)


def _scatter_region_parttype(region: str, partition_name: str = "") -> str:
    """DA parttype for a scatter entry: ``boot1``/``boot2`` or ``user``."""
    region = (region or "").strip().upper()
    if region == "EMMC_BOOT_2":
        return "boot2"
    if region.startswith("EMMC_BOOT") or (partition_name or "").strip().upper() == "PRELOADER":
        return "boot1"
    return "user"


def build_mtk_write_plan(
    scatter_path,
    model="",
    *,
    preloader_first=True,
    skip_table_partitions=False,
):
    """Return the ordered write plan the shipped 1.1.7 build performs.

    Each step is ``{"name", "path", "parttype", "offset", "length",
    "partition_size", "region"}``, and every step is an absolute byte address
    for ``da_wo``: the device's own partition table is never consulted.
    mtkclient enumerates GPT, while the Timmkoo/Innioasis players
    (MT6572/MT6582/MT6580/MT8382) are MBR-partitioned, so a named lookup
    returns nothing and the install dies with "Couldn't detect partition".

    The order is every downloadable image in scatter order. When ``preloader_first``
    is True, the preloader is moved to the front. When ``preloader_first`` is False
    (default in actual flash sessions, matching the Chinese 1.1.7 build strategy
    ``legacy_preloader_last``), the preloader is moved to the very end so an interrupted
    write leaves the existing BROM/preloader boot mechanism intact.

    Non-empty partition tables (MBR, EBR1) shipped in the package are included
    so the device has its partition tables configured, while all-zero empty stubs
    are skipped via ``_is_empty_placeholder_image``.

    ``partition_size`` is the scatter's own size for the partition. It is not
    a write length — it is the bound an image (or a sparse image's data
    extents) must fit inside, so a package built for a different layout is
    caught before it can NACK the device mid-partition.
    """
    scatter_p = Path(scatter_path)
    entries = _parse_scatter_entries(scatter_p)
    if not entries:
        return []
    scatter_dir = scatter_p.parent

    plan = []
    seen = set()
    for entry in sorted(entries, key=lambda e: e.get("order", 0)):
        name = (entry.get("name") or "").strip()
        if not name or not entry.get("is_download"):
            continue
        file_name = (entry.get("file_name") or "").strip()
        if not file_name or file_name.upper() == "NONE":
            continue
        if skip_table_partitions and _is_device_table_partition(name, file_name):
            continue
        image = _resolve_image_file(scatter_dir, file_name)
        if not image.is_file() or _is_empty_placeholder_image(image):
            # Missing files and all-zero placeholder stubs (such as empty MBR/EBR
            # stubs) are skipped so only genuine package images are flashed.
            continue
        if entry.get("tail_relative"):
            # An end-of-user-area address (>= 0xFFFF0000) only resolves
            # against the live device's user-area size; guessing it would
            # write the image at a huge bogus offset.
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        length = image.stat().st_size
        if length <= 0:
            continue
        parttype = _scatter_region_parttype(entry.get("region", ""), name)
        # Every step is written by address, exactly as SP Flash Tool does: the
        # device's partition table is never consulted (mtkclient reads GPT
        # only, and these players are MBR-partitioned). Address 0 is real
        # inside eMMC boot1 (the preloader) and at the head of the user area
        # (which the MBR occupies).
        offset = int(entry.get("offset") or 0)
        plan.append(
            {
                "name": name,
                "path": image,
                "parttype": parttype,
                "offset": offset,
                "length": length,
                "partition_size": int(entry.get("partition_size") or 0),
                "region": (entry.get("region") or "").upper(),
            }
        )

    if preloader_first:
        # Stable: everything keeps scatter order, only the preloader moves to front.
        plan.sort(key=lambda step: step["name"].strip().lower() != "preloader")
    else:
        # Preloader last (Chinese build strategy: middle + system + preloader)
        plan.sort(key=lambda step: step["name"].strip().lower() == "preloader")
    return plan


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
        # NB: not ``_stop`` — that name is a method on threading.Thread and
        # shadowing it breaks join()/thread bookkeeping.
        self._stopped = threading.Event()
        self._last = time.time()
        self._warned = False

    def activity(self):
        """Register backend chatter; clears a pending stall warning."""
        self._last = time.time()
        self._warned = False

    def stop(self):
        self._stopped.set()

    def run(self):
        while not self._stopped.wait(self._interval):
            idle = time.time() - self._last
            if idle >= self._timeout and not self._warned:
                self._warned = True
                try:
                    self._on_stall(idle)
                except Exception:
                    logger.debug("MTK stall callback failed", exc_info=True)



class _MtkOutputStallDetector(threading.Thread):
    """Reports when the MTKClient backend has printed the same stalled line

    too long. The existing ``_StallWatcher`` only tracks idle time between
    backend chatter, which is not enough for a backend that keeps printing the
    same BROM/DRAM bring-up line. This detector remembers the last stalled
    line and raises it after :data:`MTK_OUTPUT_STALL_WINDOW`.
    """

    def __init__(self, on_stalled_line, window=MTK_OUTPUT_STALL_WINDOW, interval=0.5):
        super().__init__(daemon=True, name="MtkOutputStallDetector")
        self._on_stalled_line = on_stalled_line
        self._window = float(window)
        self._interval = float(interval)
        self._stopped = threading.Event()
        self._last_line = ""
        self._last_time = 0.0
        self._reported = False

    def note(self, line):
        """Called with each backend line. Only a BROM/DRAM bring-up line arms the
        stall; any other output means the backend has moved on and disarms it."""
        text = str(line or "").strip()
        if text.startswith(MTK_BROM_STALL_PREFIXES):
            self._last_line = text
            self._last_time = time.monotonic()
        else:
            self._last_line = ""
        self._reported = False

    def progress(self):
        """Write progress is activity too: a long partition write prints no text."""
        self._last_line = ""
        self._reported = False

    def maybe_stalled(self):
        """True when the stalled line is old enough to report."""
        if not self._last_line:
            return False
        return (time.monotonic() - self._last_time) >= self._window

    def stop(self):
        self._stopped.set()

    def run(self):
        while not self._stopped.wait(self._interval):
            if (
                self._last_line
                and not self._reported
                and (time.monotonic() - self._last_time) >= self._window
            ):
                # Report once per stall; new output or progress re-arms it.
                self._reported = True
                try:
                    self._on_stalled_line(self._last_line, time.monotonic() - self._last_time)
                except Exception:
                    logger.debug("MTK output stall callback failed", exc_info=True)


class _AttemptWatchdog(threading.Thread):
    """Counts down the app's own no-progress window for a backend command.

    ``on_tick(remaining)`` is called once a second with the seconds left, so
    the install card can show the countdown; ``on_expire()`` is called once
    when it reaches zero. ``touch()`` refreshes the window (the device was
    seen) and ``disarm()`` ends it for good (the transfer started), which
    keeps a slow but working flash from ever being cut off.
    """

    def __init__(self, on_tick, on_expire, timeout=ATTEMPT_PROGRESS_TIMEOUT, interval=1.0):
        super().__init__(daemon=True, name="AttemptWatchdog")
        self._on_tick = on_tick
        self._on_expire = on_expire
        self._timeout = float(timeout)
        self._interval = float(interval)
        # Not ``_stop``: that is a threading.Thread method.
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._deadline = time.time() + float(timeout)
        self._armed = True

    def touch(self):
        """The device was seen: start the window over."""
        with self._lock:
            if not self._armed:
                return
            self._deadline = time.time() + self._timeout
        try:
            self._on_tick(int(math.ceil(self._timeout)))
        except Exception:
            pass

    def disarm(self):
        """Real progress began; the window no longer applies."""
        with self._lock:
            self._armed = False
        self._stopped.set()

    def stop(self):
        self._stopped.set()

    def run(self):
        try:
            self._on_tick(int(math.ceil(self._timeout)))
        except Exception:
            pass
        while not self._stopped.wait(self._interval):
            with self._lock:
                if not self._armed:
                    return
                remaining = self._deadline - time.time()
            if remaining <= 0:
                try:
                    self._on_expire()
                except Exception:
                    logger.debug("Attempt timeout callback failed", exc_info=True)
                return
            try:
                # Ceil: a partly-elapsed second still has time left in it.
                self._on_tick(int(math.ceil(remaining)))
            except Exception:
                logger.debug("Attempt countdown tick failed", exc_info=True)


# ---------------------------------------------------------------------------
# Flash worker
# ---------------------------------------------------------------------------
class FlashWorker(QThread):
    """Background flash: extract → validate → dispatch to the platform backend."""

    step_changed = Signal(str)
    progress = Signal(object)
    action_changed = Signal(str)
    log_message = Signal(str)
    # Seconds left of the app's no-progress window while a backend command is
    # still silent; 0 once the countdown ends (progress began, or it expired).
    attempt_timeout = Signal(int)
    # Seconds left of the write, measured from this install's own write rate;
    # -1 while the rate is not yet known (the card shows elapsed time instead).
    eta_changed = Signal(int)
    finished = Signal(bool, str)  # ok, error_code
    usb_disconnected = Signal()

    # How many times an expired device search is restarted before the install
    # is reported as failed. Each run searches for the tool's own configured
    # timeout (two minutes as shipped), so four restarts keep the connect
    # prompt true for roughly ten minutes; a device that never answers still
    # ends as a failure rather than searching forever.
    SP_SEARCH_RESTARTS = 4
    SP_SEARCH_RESTART_PAUSE = 1.0
    # Opt-in reboot after a successful in-process MTKClient write. Off by
    # default: the reference 1.1.7 build sends no reset after its last write
    # (see ``_end_mtk_run_successfully``), and nothing on the success path
    # calls ``_reboot_after_mtk_success`` today.
    reboot_after_success = False

    def __init__(self, package_path, pre_extracted_dir="", method="auto", model="",
                 parent=None, device_loss_hold=None, da_file="", auth_file="", is_rockbox=False,
                 primed_mtk=None):
        super().__init__(parent)
        self.package_path = package_path
        self.pre_extracted_dir = pre_extracted_dir
        self.model = (model or "").strip()
        self._primed_mtk = primed_mtk
        pkg_low = (Path(package_path).name if package_path else "").lower()
        self._is_rockbox_flow = bool(is_rockbox or "rockbox" in pkg_low)
        # User-chosen Download Agent and authentication file. Updater CE leaves
        # both empty (the bundled MTK_AllInOne_DA.bin and the saved SP auth file
        # apply); the MediaTek Installer hands the three files the user picked.
        self.da_file = str(da_file or "").strip()
        self.auth_file = str(auth_file or "").strip()
        # Flash backend method: "auto" (platform default), "sp" (SP Flash
        # Tool), or "mtk" (MTKClient). The user can pick manually; on macOS
        # "sp" is unavailable and always falls back to MTKClient.
        self.method = (method or "auto").lower()
        # Suppresses the USB monitor's "player unplugged" handling while this
        # worker deliberately restarts the target (preloader -> BROM).
        self._device_loss_hold = device_loss_hold
        self._cancelled = False
        # The first tool line that reported a failure, if any: a finished run
        # with one of these is a failed install, whatever the exit code says.
        self._tool_error = ""
        self._process = None
        # Set by the no-progress watchdog when it ends a backend command.
        self._attempt_expired = False
        self._attempt_watchdog = None
        self._sp_progress_hwm = 0
        self._mtk_cli_progress_hwm = 0
        self._mtk_cli_partition_progress: dict[str, float] = {}
        # Measured write rate for the ETA. Reset when the write phase begins:
        # the handshake and the format are not the transfer being timed.
        self._eta = ThroughputEta()
        # Measured write window: the install card turns this into remaining
        # seconds, and the Device Prep stage adds its own half on top of it.
        self._total_eta = _CombinableEta()
        self._sp_total_bytes = 0
        self._sp_completed_bytes = 0
        self._sp_last_part_total = 0
        self._sp_last_sent = 0
        self._sp_mismatch = False
        self._sp_err_code = None
        # SP Flash Tool reported "Flash complete! Disconnect USB...": the write
        # is finished, so the trailing "Disconnect USB" is not a failure.
        self._sp_flash_complete = False
        # Actionable one-liner for the failure this run ended with (only the
        # BROM handshake failures have one today), so the UI can explain a
        # failure without re-parsing the tool's message itself.
        self.error_hint = ""
        self._start_time = time.time()
        # Normalised SP Flash Tool console lines: used to skip the same line
        # when it is forwarded from the tool's internal QT_FLASH_TOOL.log.
        self._sp_stdout_seen: set[str] = set()
        # Tag for output the tooling prints itself ("SP"/"MTK"/"TOOL").
        self._tool_tag = "TOOL"
        self._log_pending: list[str] = []
        self._log_flushed = 0.0
        self._log_lock = threading.Lock()
        self._log_timer = None
        # Previous worker this run must wait for, off the GUI thread.
        self._wait_for = None
        # USB search is a stdout state, not the moment the install starts.
        # The connect sentence is up only while the latest tool line is a
        # search or hint. A later search line shows it again.
        self._usb_search_active = False
        self._past_usb_search = False
        # "before" until a search line. Please-wait is a later forward line.
        self._tool_wait_phase = PHASE_BEFORE
        # A search that has begun and never seen the target: while that holds,
        # the console run can be restarted without risking a half-written
        # device, so the tool's own two-minute timeout does not end the install.
        self._search_started = False
        self._search_timed_out = False
        self._device_seen = False
        self._is_disconnected = False
        self._finished = False
        self._device_loss_held_until = 0.0

    # -- lifecycle ----------------------------------------------------------
    def cancel(self):
        """Ask the backend to stop (used for user cancel AND backend switches).

        The SP Flash Tool subprocess is terminated hard so a switch to the
        other backend really frees the port/device; the worker thread then
        unblocks and exits without forwarding signals (the service detaches
        them in ``FlashService.cancel_flash``).
        """
        self._cancelled = True
        if getattr(self, "_primed_mtk", None) is not None:
            try:
                self._primed_mtk.port.close(reset=True)
            except Exception:
                pass
            self._primed_mtk = None
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            try:
                self._process.kill()
            except Exception:
                pass
            if IS_WINDOWS:
                # TerminateProcess is async; give it a moment then force-kill
                # the process tree so no child of flash_tool survives the switch.
                try:
                    self._process.wait(timeout=1.0)
                except Exception:
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                            capture_output=True,
                        )
                    except Exception:
                        pass

    def _hold_device_loss(self, seconds: float):
        self._device_loss_held_until = max(self._device_loss_held_until, time.time() + float(seconds))
        if callable(self._device_loss_hold):
            try:
                self._device_loss_hold(seconds)
            except Exception:
                pass

    def _device_loss_is_held(self) -> bool:
        return time.time() < getattr(self, "_device_loss_held_until", 0.0)

    def _start_usb_watchdog(self):
        """Monitor USB presence in background while flashing once device is detected."""
        def _watch():
            misses = 0
            while not self._cancelled and not getattr(self, "_finished", False):
                time.sleep(1.0)
                if getattr(self, "_sp_flash_complete", False) or getattr(self, "_finished", False):
                    break
                if not getattr(self, "_device_seen", False):
                    continue
                if self._device_loss_is_held():
                    continue
                if getattr(self, "_past_usb_search", False):
                    progress_grace = (
                        MTK_WRITE_PROGRESS_USB_GRACE_SECONDS
                        if getattr(self, "_tool_tag", "") == "MTK"
                        else 6.0
                    )
                    if time.time() - getattr(self, "_last_install_progress_time", 0.0) < progress_grace:
                        misses = 0
                        continue
                    if not is_mtk_device_connected():
                        misses += 1
                        if misses >= 5:  # 5-second safety window before determining install failure
                            if getattr(self, "_sp_flash_complete", False) or getattr(self, "_finished", False):
                                break
                            self._log("Notice: Device disconnected from USB mid-flash.")
                            self._is_disconnected = True
                            self.cancel()
                            self.usb_disconnected.emit()
                            break
                    else:
                        misses = 0

        t = threading.Thread(target=_watch, daemon=True, name="FlashUsbWatchdog")
        t.start()

    def _announce_please_wait(self):
        """Before any search line: do not show the connect sentence."""
        if getattr(self, "_tool_wait_phase", PHASE_BEFORE) != PHASE_BEFORE:
            return
        if self._past_usb_search or self._usb_search_active:
            return
        self.step_changed.emit(STEP_PLEASE_WAIT)
        self.progress.emit(0)

    # -- attempt no-progress watchdog ---------------------------------------
    def _start_attempt_watchdog(self, timeout=None):
        """Arm the app's own no-progress window for a backend command.

        Counted from the moment the command starts: a backend that never
        reports the player, or goes quiet right after, is ended by the app
        rather than left on the install card until the tool's own (much
        longer) timeout. ``_touch_attempt`` refreshes it when the device is
        seen and ``_note_install_progress`` switches it off once the download
        agent or the images start moving.
        """
        self._stop_attempt_watchdog()
        self._attempt_expired = False
        if timeout is None:
            is_mtk = (
                is_mtk_method(getattr(self, "method", ""))
                or getattr(self, "_tool_tag", "") == "MTK"
            )
            timeout = ATTEMPT_PROGRESS_TIMEOUT_MTK if is_mtk else ATTEMPT_PROGRESS_TIMEOUT
        try:
            watchdog = _AttemptWatchdog(
                self.attempt_timeout.emit,
                self._expire_attempt,
                timeout=timeout,
            )
            watchdog.start()
        except Exception:
            logger.debug("Could not start the attempt watchdog", exc_info=True)
            return None
        self._attempt_watchdog = watchdog
        return watchdog

    def _stop_attempt_watchdog(self):
        watchdog = getattr(self, "_attempt_watchdog", None)
        self._attempt_watchdog = None
        if watchdog is not None:
            watchdog.stop()
        try:
            self.attempt_timeout.emit(0)
        except Exception:
            pass

    def _touch_attempt(self):
        """The backend reported the device: start the window over."""
        watchdog = getattr(self, "_attempt_watchdog", None)
        if watchdog is not None:
            watchdog.touch()

    def _note_install_progress(self):
        """Real transfer began: the no-progress window no longer applies."""
        self._last_install_progress_time = time.time()
        watchdog = getattr(self, "_attempt_watchdog", None)
        if watchdog is not None:
            watchdog.disarm()
        try:
            self.attempt_timeout.emit(0)
        except Exception:
            pass

    def _expire_attempt(self):
        """End a backend command that showed nothing inside the window."""
        if getattr(self, "_attempt_expired", False):
            return
        self._attempt_expired = True
        timeout_val = getattr(getattr(self, "_attempt_watchdog", None), "_timeout", ATTEMPT_PROGRESS_TIMEOUT)
        self._log(
            tr("err_attempt_timeout").format(seconds=int(timeout_val))
        )
        process = getattr(self, "_process", None)
        try:
            if process is not None and process.poll() is None:
                process.terminate()
        except Exception:
            logger.debug("Could not terminate a silent backend", exc_info=True)

    def _enter_usb_search(self):
        """The tool is on a search line. Show the connect sentence, still at 0%."""
        if getattr(self, "_past_usb_search", False):
            return
        already = getattr(self, "_tool_wait_phase", PHASE_BEFORE) == PHASE_CONNECT
        self._search_started = True
        self._usb_search_active = True
        self._tool_wait_phase = PHASE_CONNECT
        if not getattr(self, "_attempt_watchdog", None) and not getattr(self, "_attempt_expired", False):
            self._start_attempt_watchdog()
        if already:
            return
        self.step_changed.emit(STEP_WAITING)
        self.progress.emit(0)
        self.action_changed.emit(tr("action_searching"))

    def _note_tool_wait_line(self, line):
        """Connect only on a search line; please-wait on the next real line."""
        current = getattr(self, "_tool_wait_phase", PHASE_BEFORE)
        phase = advance_tool_wait_phase(current, line)
        if phase == current:
            return
        if phase == PHASE_CONNECT:
            # _enter_usb_search records the phase and emits STEP_WAITING.
            self._enter_usb_search()
            return
        self._tool_wait_phase = phase
        if phase == PHASE_PLEASE_WAIT:
            self._usb_search_active = False
            self.step_changed.emit(STEP_PLEASE_WAIT)
            self.progress.emit(0)

    def _leave_usb_search(self):
        """A numbered stage started. A later search line may show connect again."""
        self._usb_search_active = False
        self._past_usb_search = True
        self._device_seen = True
        # The player answered, so the connect sentence is finished: mark the
        # phase here rather than letting the next unrelated tool line decide.
        self._tool_wait_phase = PHASE_PLEASE_WAIT
        # The device answered: the app's window starts over instead of ending
        # the run the moment it was making progress.
        self._touch_attempt()

    def _classify_mtk_output(self, line):
        """Search and forward lines from MTKClient stdout or a ``[LOG]`` payload."""
        self._touch_attempt()
        if is_mtk_usb_search_line(line):
            self._enter_usb_search()
            return
        text = tool_line_body(line)
        low = text.lower()
        if not text:
            return
        if is_disconnection_error(text):
            self._log(f"Notice: Disconnection detected in MTKClient output: {text}")
            self._is_disconnected = True
            self.cancel()
            self.usb_disconnected.emit()
            return
        if (
            low.startswith("port - device detected")
            or "port - device detected" in low
            or "usb port detected" in low
            or "brom connected" in low
            or "detected" in low
        ):
            self._leave_usb_search()
            self._tool_error = ""
            self._touch_attempt()
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
            return
        if "sdmmc_write_data" in low or "segmented write" in low:
            self._leave_usb_search()
            self._tool_error = ""
            self._note_install_progress()
            self._touch_attempt()
            return
        if low.startswith("done") or low.startswith("progress"):
            self._leave_usb_search()
            self._note_install_progress()
            self._touch_attempt()
            if low.startswith("progress:"):
                self._update_mtk_partition_action(text)
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
            if m:
                try:
                    pct_val = float(m.group(1))
                except ValueError:
                    pct_val = None
                if pct_val is not None:
                    if "stage 2" in low or "stage2" in low:
                        scaled = 14 + int(min(1.0, max(0.0, pct_val / 100.0)) * 4)
                        self.step_changed.emit(STEP_DOWNLOAD_DA)
                        self.progress.emit(min(18, max(14, scaled)))
                    else:
                        scaled = 20 + int(min(1.0, max(0.0, pct_val / 100.0)) * 75)
                        self.step_changed.emit(STEP_WRITE)
                        self.progress.emit(min(95, max(20, scaled)))
                    return
            self.step_changed.emit(STEP_WRITE)
            return
        if (
            "download da" in low
            or "of da has been sent" in low
            or "connect da" in low
        ):
            self._leave_usb_search()
            self._tool_error = ""
            self._note_install_progress()
            self._touch_attempt()
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.progress.emit(12)
            return
        if "of image data has been sent" in low or "writing partition" in low:
            self._leave_usb_search()
            self._tool_error = ""
            self._note_install_progress()
            self._touch_attempt()
            self.step_changed.emit(STEP_WRITE)
            return
        if "didn't receive dram config status" in low:
            self._log(
                "DRAM configuration failed in BROM mode. Please reconnect in Preloader mode: "
                "power off the device completely, then connect USB without holding any buttons or pinhole reset."
            )
            return

    def _log(self, msg):
        for line in str(msg).splitlines() or [str(msg)]:
            self._note_tool_wait_line(line)
            is_mtk = (
                is_mtk_method(getattr(self, "method", ""))
                or getattr(self, "_tool_tag", "") == "MTK"
            )
            if is_mtk:
                self._touch_attempt()
                # In-process MTKClient prints transfer percentages through the
                # diagnostics tee rather than config.gui. Feed those same raw
                # lines to the subtitle parser here so activity stays in sync
                # with the DA output. The formatted action itself is never a
                # Progress: line, so it cannot recurse.
                progress_line = tool_line_body(line)
                if progress_line.lower().startswith("progress:"):
                    self._update_mtk_partition_action(progress_line)
            waiting_mtk = is_mtk and not self._past_usb_search
            if not self._tool_error and is_fatal_tool_line(line) and not waiting_mtk:
                self._tool_error = line.strip()
        flush_now = False
        with self._log_lock:
            self._log_pending.append(str(msg))
            now = time.monotonic()
            if len(self._log_pending) >= 24 or now - self._log_flushed >= 0.08:
                flush_now = True
            elif self._log_timer is None or not self._log_timer.is_alive():
                # A burst can end before the next line. Without this the last
                # lines stay invisible until the install finishes.
                self._log_timer = threading.Timer(0.09, self._flush_log)
                self._log_timer.daemon = True
                self._log_timer.start()
        if flush_now:
            self._flush_log()

    def _flush_log(self):
        """Hand the GUI a batch of lines so progress paints between them."""
        with self._log_lock:
            if not self._log_pending:
                return
            chunk = "\n".join(self._log_pending)
            self._log_pending.clear()
            self._log_flushed = time.monotonic()
        self.log_message.emit(chunk)

    def _update_time(self):
        pass  # UI computes elapsed/ETA from its own timer

    # -- pipeline ------------------------------------------------------------
    def run(self):
        from .diagnostics import capture_tool_output

        pred = getattr(self, "_wait_for", None)
        if pred is not None:
            try:
                if pred.isRunning():
                    pred.wait(3000)
            except Exception:
                pass

        # Everything the backends print themselves (MTKClient runs in process,
        # so its USB/DA traces never reach the UI otherwise) is mirrored into
        # the diagnostics log for as long as the install runs.
        with capture_tool_output(self._log, tag=lambda: self._tool_tag):
            try:
                self._start_usb_watchdog()
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
                if is_disconnection_error(str(e), e) or (
                    getattr(self, "_past_usb_search", False) and not is_mtk_device_connected()
                ):
                    self._is_disconnected = True
                    self.cancel()
                    self.usb_disconnected.emit()
                    self.finished.emit(False, "USB_DISCONNECTED")
                else:
                    self.finished.emit(False, "INTERNAL_ERROR")
            finally:
                self._finished = True
                self._flush_log()

    def _do_flash(self):
        p = Path(self.package_path) if self.package_path else None
        is_scatter = p and p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower())
        is_folder = p and p.is_dir()
        pre = Path(self.pre_extracted_dir) if self.pre_extracted_dir else None
        reused = (bool(pre) and _is_extract_complete(pre)) or is_scatter or is_folder
        if reused:
            # The package is already extracted (e.g. from the SP Flash Tool
            # phase before a backend switch, or a local scatter folder); skip re-extraction entirely.
            if pre and _is_extract_complete(pre):
                extract_dir = pre
            elif is_scatter:
                extract_dir = p.parent
            elif is_folder:
                sc = _find_scatter(p, allow_raise=False)
                extract_dir = sc.parent if sc else p
            self._log(f"Reusing extracted directory: {extract_dir}")
            # Ready on disk. The connect sentence waits for a real search line.
            self._announce_please_wait()
        else:
            self.step_changed.emit(STEP_EXTRACTING)
            self.progress.emit(5)
            self._log(f"Extracting firmware package: {self.package_path}")
            extract_dir = self._extract_package()

        if self._cancelled:
            self.finished.emit(False, "USER_CANCELLED")
            return

        self._announce_please_wait()

        try:
            scatter_file, missing = self._validate_extract(extract_dir)
        except ScatterDiscoveryError as e:
            self._log(f"Error: {e}")
            self.finished.emit(False, str(e))
            return

        can_reextract = bool(
            self.package_path
            and Path(self.package_path).is_file()
            and Path(self.package_path).suffix.lower() in (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz")
        )
        if (scatter_file is None or missing) and reused and can_reextract and not Path(self.package_path).is_dir():
            self._log(
                "Scatter file missing from reused directory; re-extracting..."
                if scatter_file is None
                else f"Image files missing ({', '.join(missing)}); re-extracting..."
            )
            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            extract_dir = self._extract_package(force=True)
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
            self.finished.emit(False, f"MISSING_IMAGES: {', '.join(missing)}")
            return

        scatter_parent = scatter_file.parent
        if (scatter_parent / "fw_package.ini").is_file() or (scatter_parent / "_internal").is_dir() or (scatter_parent / "tools").is_dir():
            self._log("Staging firmware into isolated working directory...")
            scatter_file = stage_firmware_for_flashing(scatter_file)
            extract_dir = scatter_file.parent
            self._log(f"Staged scatter file: {scatter_file}")

        self._log(f"Scatter file: {scatter_file}")
        platform = _parse_scatter_platform(scatter_file)
        if platform:
            self._log(f"Package platform: {platform}")

        try:
            from . import catalog, device_tracking
            from .config import detect_model_and_type_from_name
            from .sp_flash_gui import update_sp_history_ini

            mfr_info = catalog.parse_manufacturer_rom_info(self.package_path)
            det_m = mfr_info["model"] if mfr_info else None
            if not det_m:
                det_m, _ = detect_model_and_type_from_name(self.package_path)

            plat, proj = catalog.parse_scatter_header(scatter_file)
            scatter_inferred = catalog.infer_model_from_scatter(plat, proj)
            detected_model = (
                self.model
                or det_m
                or scatter_inferred
                or ("A5" if "a5" in str(self.package_path).lower() else ("Y2" if "6582" in (platform or "") else ("Y1" if "6572" in (platform or "") else "")))
            )
            self.package_model = detected_model
            if plat:
                self.package_platform = plat
            device_tracking.record_latest_package(
                model=detected_model,
                software_name=Path(self.package_path).stem if self.package_path else "",
                tag_name="",
                package_path=str(self.package_path),
                extract_dir=str(Path(extract_dir).resolve()),
                scatter_path=str(Path(scatter_file).resolve()),
            )
        except Exception as e:
            logger.debug("Could not record latest package during flash: %s", e)

        self._dispatch_backend(extract_dir, scatter_file)

    def _dispatch_backend(self, extract_dir, scatter_file):
        """Choose SP Flash Tool vs MTKClient for this run.

        ``method`` is the user's explicit choice ("sp" / "mtk") or "auto".
        Auto leans towards SP Flash Tool on Windows and Linux (the
        InniUpdaterChin default) with MTKClient as the fallback; macOS has no
        SP Flash Tool build, so MTKClient is the only backend there.
        """
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
    def _extract_package(self, force=False):
        pre = Path(self.pre_extracted_dir) if self.pre_extracted_dir else None
        if pre and pre.is_dir():
            sc = _find_scatter(pre, allow_raise=False)
            if sc:
                return sc.parent
            return pre
        p = Path(self.package_path) if self.package_path else None
        if p and p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower()):
            return p.parent
        if p and p.is_dir():
            sc = _find_scatter(p, allow_raise=False)
            return sc.parent if sc else p
        worker = ExtractWorker(self.package_path)
        # The helper emits on its own signal. Forward that onto this worker so
        # the progress card moves during unzip instead of sitting at 5%.
        worker.progress.connect(lambda p: self.progress.emit(p))
        # Same extraction as ExtractWorker: reuses a completed extract and marks
        # a new one complete. Cached files are never removed during an install.
        extract_dir = worker._extract(self.package_path, force=force)
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
    def _resolve_da_file(self, sp_dir):
        """Download agent for this run: the user's choice, else the bundled DA.

        Updater CE always flashes with the payload's own ``MTK_AllInOne_DA.bin``
        (the reference behaviour, and the only DA known to match our packages).
        The MediaTek Installer lets the user point at any vendor's DA, so that
        file wins when it is present; a path that vanished falls back to the
        bundled payload with a line saying so rather than flashing with the
        wrong agent silently.
        """
        chosen = self.da_file
        if chosen:
            if Path(chosen).is_file():
                return Path(chosen)
            self._log(f"Download agent not found, using the bundled DA: {chosen}")
        return Path(sp_dir) / "MTK_AllInOne_DA.bin"

    def _resolve_auth_file(self):
        """Authentication file for this run: the installer's choice, else saved."""
        if self.auth_file:
            return self.auth_file
        try:
            from . import device_tracking

            return device_tracking.sp_auth_file()
        except Exception:
            return ""

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
            da_file = self._resolve_da_file(sp_dir)
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
            da_file = self._resolve_da_file(sp_dir)
            log_root = sp_dir / linux_sp_flash.LOG_DIR_NAME
            log_root.mkdir(parents=True, exist_ok=True)
            env = linux_sp_flash.process_env(sp_dir)
            creationflags = 0
            scatter_arg = str(scatter_file)
            da_arg = str(da_file)

        align_scatter_disk_images(scatter_file, log=self._log)
        if IS_WINDOWS:
            scatter_arg = _to_short_path(scatter_file)
            if not str(scatter_arg).isascii():
                self._log(
                    "Warning: scatter path contains non-ASCII characters and 8.3 short "
                    "name is unavailable. SP Flash Tool may fail to load the package."
                )
        else:
            scatter_arg = str(scatter_file)

        try:
            scatter_images = _parse_scatter(scatter_file)
            self._sp_total_bytes = sum(fp.stat().st_size for _, fp in scatter_images if fp.exists())
        except Exception:
            self._sp_total_bytes = 0
        self._sp_completed_bytes = 0
        self._sp_last_part_total = 0
        self._sp_last_sent = 0

        auth_file = self._resolve_auth_file()
        if self.da_file and str(Path(da_file)) != str(Path(sp_dir) / "MTK_AllInOne_DA.bin"):
            self._log(f"Download agent: {Path(da_file).name}")

        self._announce_please_wait()
        try:
            if IS_WINDOWS:
                cmd = [
                    str(flash_tool_exe),
                    *sp_flash_tool_console_args(
                        scatter_arg, da_arg, auth_file, self._log
                    ),
                ]
                self._log("Launching SP Flash Tool (console mode, searching USB)...")
                self._log("Keep the device unplugged until the connect prompt appears.")

                def spawn():
                    return subprocess.Popen(
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

                def spawn():
                    return linux_sp_flash.launch_linux_flash_tool(
                        stage=sp_dir,
                        cmd_args=cmd_args,
                        cwd=sp_dir,
                        env=env,
                        log_cb=self._log,
                    )

            exit_code, launch_time = self._sp_console_search(spawn, log_root)
            if self._attempt_expired:
                # Nothing happened inside the app's own window: report it as such
                # so the flow can offer the other backend.
                self.finished.emit(False, ERR_ATTEMPT_TIMEOUT)
                return
            if exit_code is None:
                return

            if self._sp_mismatch:
                self.finished.emit(False, "DEVICE_PACKAGE_MISMATCH")
                return

            # The reference Chinese build decides success from the internal log,
            # not from the process exit code alone: the console tool can exit
            # non-zero while the flash actually succeeded. Read the log first so
            # the log result is the single source of truth for completion.
            log_success, error_detail = self._read_sp_log_result(launch_time, log_root)

            if "CHIP_TYPE_NOT_MATCH" in str(error_detail).upper():
                self.finished.emit(False, "DEVICE_PACKAGE_MISMATCH")
                return

            # The console's completion line ("All command exec" or "Flash complete!")
            # marks an unconditionally successful flash execution.
            if self._sp_flash_complete or sp_log_result_is_success(
                log_success, error_detail, self._sp_flash_complete
            ):
                log_success = True

            if log_success:
                self._finished = True
                self.step_changed.emit(STEP_DONE)
                self.progress.emit(100)
                self._log("Flash complete! Disconnect USB and reboot the device.")
                self.finished.emit(True, "")
                return

            if error_detail:
                self._log(f"SP Flash Tool error: {error_detail}")
                self._finish_sp_failure(str(error_detail))
            elif self._sp_err_code:
                # Name the number so the error page can explain it; the raw code
                # stays in the message for support.
                name = SP_ERROR_CODE_NAMES.get(str(self._sp_err_code), "")
                self._finish_sp_failure(
                    f"{name} ({self._sp_err_code})" if name else f"SP_ERR_{self._sp_err_code}"
                )
            elif exit_code == 0:
                # Exit code 0 but no "download ok" in the log: the tool exited
                # cleanly without completing the flash (for example, the user
                # closed it mid-run). Treat exit 0 as completion, not as a driver
                # error: exit codes are not part of the driver-missing signal, and
                # the log is the single source of truth for whether the flash
                # finished.
                self._finish_sp_failure(f"SP_EXIT_{exit_code}")
            else:
                self._finish_sp_failure(f"SP_EXIT_{exit_code}")
        finally:
            pass

    def _finish_sp_failure(self, detail):
        """Emit a failed SP run, carrying the hint that failure calls for.

        ``S_BROM_CMD_STARTCMD_FAIL (2005)`` is the failure users hit most often
        and the tool's own message ("Possibly target power up too early") does
        not say what to do about it, so its hint travels on the error string the
        progress card already shows. The raw code stays at the front of that
        string, so :func:`sp_error_name` / :func:`sp_error_code` and the error
        page's explanation keep working.
        """
        if self._sp_flash_complete:
            self._log(
                "Notice: SP Flash Tool 'all command exec' completion overrides failure/exit detail: "
                f"{detail}"
            )
            self.step_changed.emit(STEP_DONE)
            self.progress.emit(100)
            self.finished.emit(True, "")
            return
        if (
            self._is_disconnected
            or is_disconnection_error(detail)
        ):
            self._log(f"Notice: SP Flash Tool failed due to USB disconnection: {detail}")
            self._is_disconnected = True
            self.cancel()
            self.usb_disconnected.emit()
            self.finished.emit(False, "USB_DISCONNECTED")
            return
        hint = sp_brom_handshake_hint(detail)
        self.error_hint = hint
        if hint:
            self._log(f"Hint: {hint}")
            detail = f"{detail} — {hint}"
        self.finished.emit(False, str(detail))

    def _sp_console_search(self, spawn, log_root):
        """Run the console tool, re-arming its USB search while it expires unheard.

        The tool's device search is bounded by its own ``option.ini``
        (``[Conn] Timeout=3`` prints as "Search usb, timeout set as 120000 ms"),
        and its ``-t`` switch is the battery mode, not a timeout: after two
        minutes of nothing the run ends with ``S_TIMEOUT (1042)`` even though
        the user is still following the on-screen connect prompt. Restarting the
        search keeps that prompt true instead of failing the install under it.
        Bounded, and only while no device has been seen, so a cable, driver or
        device problem still ends as a failure rather than searching forever.

        Returns ``(exit_code, launch_time)``; the exit code is ``None`` when the
        user cancelled (the cancel signal has already been emitted).
        """
        attempts = 0
        launch_time = time.time()
        while True:
            attempts += 1
            launch_time = time.time()
            exit_code = self._sp_console_run_once(spawn, log_root, launch_time)
            if exit_code is None:
                return None, launch_time
            if (
                exit_code == 0
                or self._sp_mismatch
                or self._cancelled
                or self._attempt_expired
                or self._device_seen
                or not (self._search_started and self._search_timed_out)
                or attempts > self.SP_SEARCH_RESTARTS
            ):
                return exit_code, launch_time
            self._log(tr("sp_search_restart").format(count=attempts))
            # The run's own timeout lines read as forward progress and would
            # leave the card on "please wait" while the player is still
            # expected: put the connect sentence back before the next search.
            self._enter_usb_search()
            time.sleep(self.SP_SEARCH_RESTART_PAUSE)

    def _sp_console_run_once(self, spawn, log_root, launch_time=None):
        """Launch the console tool once, stream it to the log, return its code.

        ``None`` means the user cancelled: ``USER_CANCELLED`` has been emitted.
        """
        self._search_timed_out = False
        if launch_time is None:
            launch_time = time.time()

        self._process = spawn()
        register_sp_session(getattr(self._process, "pid", 0))
        # Reset the window for this launch, but do not start it: the countdown
        # starts on the tool's "search usb" line (see _enter_usb_search), not at
        # launch, when the tool is still initialising and the user has nothing
        # to connect yet.
        self._stop_attempt_watchdog()
        self._attempt_expired = False

        # Background thread tailing the SP Flash Tool log file.
        stop_monitor = threading.Event()
        monitor = threading.Thread(
            target=self._monitor_sp_log_file,
            args=(stop_monitor, log_root, launch_time),
            daemon=True,
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
                    if self._sp_flash_complete:
                        stop_monitor.set()
                        return 0
                    try:
                        self._process.terminate()
                    except Exception:
                        pass
                    stop_monitor.set()
                    self.finished.emit(False, "USER_CANCELLED")
                    return None
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
            if hasattr(self._process, "_pty_master_fd"):
                try:
                    os.close(self._process._pty_master_fd)
                except Exception:
                    pass

        self._stop_attempt_watchdog()
        self._process.wait()
        unregister_sp_session(getattr(self._process, "pid", 0))
        return self._process.returncode

    def _mapped_phase_percent(self, line, start, span):
        """Map a tool's own percent into a slice of the install bar."""
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", line or "")
        if not match:
            return int(start)
        frac = max(0.0, min(100.0, float(match.group(1)))) / 100.0
        return int(round(start + span * frac))

    def _classify_sp_stdout(self, line):
        if is_sp_usb_search_line(line):
            if not getattr(self, "_past_usb_search", False) and not getattr(self, "_device_seen", False):
                self._enter_usb_search()
            return
        if is_sp_flash_complete_line(line):
            self._sp_flash_complete = True
            return
        if is_disconnection_error(line):
            # The tool has already reported the command finished: the cable
            # going away now is the device rebooting, not a failed install.
            # Treating it as a disconnect cancels a completed flash and shows
            # the player-disconnected card over a good write.
            if self._sp_flash_complete:
                self._log(
                    "Notice: SP Flash Tool reported completion before the device "
                    f"left USB; ignoring: {line}"
                )
                return
            self._log(f"Notice: Disconnection detected in SP Flash Tool output: {line}")
            self._is_disconnected = True
            self.cancel()
            self.usb_disconnected.emit()
            return
        low = line.lower()
        if "searchusbportpool failed" in low or "failed to find usb port" in low:
            # The console search gave up ("Connect BROM failed: S_TIMEOUT
            # (1042)" follows). Only a restart can still catch a late player.
            self._search_timed_out = True
        if "s_chip_type_not_match" in low:
            self._sp_mismatch = True
        elif (
            "brom connected" in low
            or "da high speed usb port detected" in low
            or "usb port is obtained" in low
            or "usb port detected" in low
            or "com port is open" in low
            or "executing dadownloadall" in low
            or "start downloading via da" in low
        ):
            self._leave_usb_search()
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
            self.action_changed.emit(tr("action_brom_detected"))
        elif "of da has been sent" in low:
            self._leave_usb_search()
            self._note_install_progress()
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.progress.emit(self._mapped_phase_percent(line, 12, 6))
            self.action_changed.emit(tr("action_downloading_da"))
        elif "of bootloader has been sent" in low:
            self._leave_usb_search()
            self._note_install_progress()
            self.step_changed.emit(STEP_DOWNLOAD_BL)
            self.progress.emit(self._mapped_phase_percent(line, 16, 2))
            self.action_changed.emit(tr("action_downloading_bootloader"))
        elif "format succeeded" in low:
            self._leave_usb_search()
            self.step_changed.emit(STEP_WRITE)
            self.progress.emit(20)
            self.action_changed.emit(tr("action_format_succeeded"))
        elif "of image data has been sent" in low:
            self._leave_usb_search()
            self._note_install_progress()
            if not getattr(self, "_write_phase_started", False):
                # First image bytes: this is the transfer the ETA is about.
                self._write_phase_started = True
                self._eta.reset()
            self.step_changed.emit(STEP_WRITE)
            self._update_sp_image_progress(line)
        elif "download ok" in low:
            # SP Flash Tool reports "download ok" on each partition write
            # completion in its console output. The console may still be
            # running after this line (it can emit per-partition "download ok"
            # lines), so do not emit STEP_DONE / completion here: the log-file
            # result check at the end of the run is the single place that
            # decides success, matching the reference Chinese build.
            self._leave_usb_search()
            self._note_install_progress()
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

    def _emit_progress(self, mapped):
        """Monotonic write-phase progress: a bar never goes backwards."""
        from .progress_model import scale_flash_progress
        is_rockbox = getattr(self, "_is_rockbox_flow", False)
        scaled = scale_flash_progress(float(mapped), is_rockbox=is_rockbox)
        if scaled > self._sp_progress_hwm:
            self._sp_progress_hwm = scaled
            self.progress.emit(round(scaled, 1))

    def _emit_eta(self, done, total):
        """Publish the measured time remaining (``-1`` while it is unknown)."""
        eta = self._eta.note(done, total)
        self.eta_changed.emit(-1 if eta is None or eta <= 0 else int(eta))

    def _update_sp_image_progress(self, line):
        """Overall progress and ETA from the tool's own byte counts.

        The console reports ``62% of image data has been sent (441.62M of
        711.46M)``: a fraction of *its* total, then the byte accounting behind
        it. That parenthesised total is what the tool decided to write — it
        auto-loads every image the scatter enables — so it, and not a sum this
        app derives from the package's own file list, is the honest
        denominator. Using the smaller app-side total made the bar run ahead of
        the tool: it read "half way" while the much larger images were still to
        come, which is precisely the case the estimate must get right.
        """
        from .progress_model import float_percent_of, format_size_bytes
        m = re.search(r"(\d+)%\s+of image data has been sent\s+\(([^)]+)\s+of\s+([^)]+)\)", line)
        if not m:
            m_simple = re.search(r"(\d+)%\s+of image data", line)
            if m_simple:
                pct = min(100, max(0, int(m_simple.group(1))))
                self._emit_progress(float_percent_of(pct, 100, 20, 75))
                self.action_changed.emit(tr("action_writing_image_fmt").format(pct=pct))
            return

        pct = int(m.group(1))
        sent = _parse_sp_size(m.group(2))
        total = _parse_sp_size(m.group(3))
        if total <= 0:
            total = self._sp_total_bytes
        if total > 0:
            self._sp_total_bytes = total
            self._sp_completed_bytes = sent
            self._emit_progress(float_percent_of(sent, total, 20, 75))
            # The whole install left, measured from the write window the tool
            # has actually been flying through: sent is how many bytes of
            # the declared set have landed, and the remaining bytes are the
            # install portion the card scores before it hands the last few
            # seconds to the Device Prep stage.
            remaining = self._total_eta.note(self._sp_completed_bytes, self._sp_total_bytes)
            self.eta_changed.emit(remaining if remaining > 0 else -1)
        else:
            self._emit_progress(float_percent_of(pct, 100, 20, 75))

        sent_str = format_size_bytes(sent) if sent > 0 else m.group(2)
        total_str = format_size_bytes(total) if total > 0 else m.group(3)
        img_name = getattr(self, "_current_image_name", "") or "system"
        task_label = layman_task_name(img_name)
        self._note_install_progress()
        self._stop_attempt_watchdog()
        self.action_changed.emit(
            tr("action_writing_task_fmt").format(
                task=task_label, pct=pct
            )
        )

    def _monitor_sp_log_file(self, stop_event, log_root=None, launch_time=None):
        log_root = Path(log_root or _SP_LOG_ROOT)
        try:
            log_file = None
            last_pos = 0
            forwarded = 0

            def _find_newest_log():
                if not log_root.exists():
                    return None
                dirs = [
                    p
                    for p in log_root.iterdir()
                    if p.is_dir()
                    and (launch_time is None or p.stat().st_mtime >= launch_time - 5.0)
                ]
                if not dirs:
                    return None
                newest = max(dirs, key=lambda p: p.stat().st_mtime)
                candidate = newest / "QT_FLASH_TOOL.log"
                return candidate if candidate.exists() else None

            log_file = _find_newest_log()
            while log_file is None and not stop_event.is_set():
                time.sleep(0.5)
                log_file = _find_newest_log()

            if log_file is None:
                return

            while not stop_event.is_set():
                time.sleep(0.5)
                try:
                    if not log_file.exists():
                        continue
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
                            self._leave_usb_search()
                            self.step_changed.emit(STEP_WRITE)
                            self._update_sp_image_progress(ln)
                        elif "download ok" in low:
                            # Per-partition "download ok" lines are progress, not
                            # completion. The final success determination is made by
                            # _read_sp_log_result after the process exits, matching the
                            # reference Chinese build which reads the log file once at
                            # the end rather than reacting to each partition line.
                            self._leave_usb_search()
                            self._note_install_progress()
                        elif is_sp_flash_complete_line(ln):
                            self._sp_flash_complete = True
                        elif (
                            "brom connected" in low
                            or "da high speed usb port detected" in low
                            or "usb port is obtained" in low
                            or "usb port detected" in low
                            or "com port is open" in low
                            or "executing dadownloadall" in low
                            or "start downloading via da" in low
                        ):
                            self._leave_usb_search()
                            self.step_changed.emit(STEP_DETECT)
                            self.progress.emit(12)
                        elif "of da has been sent" in low or "of bootloader has been sent" in low:
                            self._classify_sp_stdout(ln)
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
        finally:
            try:
                self._flush_log()
            except Exception:
                pass

    def _read_sp_log_result(self, launch_time, log_root=None):
        """Read the SP Flash Tool internal log to determine install result.

        The reference Chinese build decides success from the internal log
        (QT_FLASH_TOOL.log), not from the process exit code alone: the console
        tool can exit non-zero while the flash actually succeeded, and can exit
        zero in some partial-write cases. This mirrors that behaviour — the
        log is the single source of truth for completion detection.

        Returns ``(success: bool, detail: str)``. On success, ``detail`` is
        empty; on failure, ``detail`` carries the error name and code.
        """
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
            # The reference build checks for "Download Ok" in the log as the
            # success signal. Both capitalisations appear depending on the tool
            # version. A success token and a failure code can both be present
            # in one log (the console reports "Download Ok" per completed
            # image), so the *last* marker of either kind decides: an earlier
            # success can never turn a later failure into a completed install,
            # and a retry that ends in "Download Ok" still counts as success.
            success_at = -1
            saw_all_command = False
            for match in re.finditer(
                r"download ok|download succeeded|all command(?:s)?\s+exec|flash complete",
                content,
                re.IGNORECASE,
            ):
                success_at = match.end()
                m_low = match.group(0).lower()
                if "all command" in m_low or "exec" in m_low or "flash complete" in m_low:
                    saw_all_command = True
            error_at = -1
            last_error = None
            # Error tokens exclude S_DONE, which is the success code, not an
            # error. The last error is the one the user needs to see.
            for match in re.finditer(r"(S_\w+)\s*\((\d+)\)", content):
                name, code = match.group(1), match.group(2)
                if name == "S_DONE":
                    continue
                error_at = match.end()
                last_error = (name, code)
            if saw_all_command or (success_at != -1 and success_at > error_at):
                return (True, "")
            if last_error is not None:
                name, code = last_error
                return (False, f"{name} ({code})")
            # Fall back to the last exception line if there is one.
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
            session = mtk_api.init(
                loader=getattr(self, "da_file", "") or None,
                preloader=preloader_path,
                auth=getattr(self, "auth_file", "") or None,
            )
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
        self._log(
            f"Attempt {attempt} failed. Waiting for the player to come back "
            "in flash mode."
        )
        self.action_changed.emit(f"{tr('action_da_wait_back')} (retrying)")
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
        if not getattr(self, "_force_subprocess_cli", False) and (
            os.environ.get("UPDATER_MTK_INPROCESS") == "1"
            or not getattr(self, "_use_subprocess_cli", True)
            or (len(sys.argv) > 0 and "smoke_test" in sys.argv[0])
            or "pytest" in sys.modules
        ):
            self._flash_via_mtkclient_core(
                extract_dir, scatter_file, da_file=self.da_file, auth_file=self._resolve_auth_file()
            )
            return

        self._tool_tag = "MTK"
        if sys.platform.startswith("linux") or (os.name == "posix" and not sys.platform.startswith("darwin")):
            try:
                from . import linux_sp_flash
                readiness = linux_sp_flash.verify_linux_flashing_readiness()
                if not readiness.get("udev_ok"):
                    self._log(
                        f"Warning: {readiness.get('udev_msg')}. "
                        f"If device is not detected, run: sudo bash {readiness.get('setup_script_path')}"
                    )
                for conf in readiness.get("service_conflicts", []):
                    self._log(f"Notice: {conf.get('message')}")
            except Exception as e:
                logger.debug("Linux readiness check skipped: %s", e)
        self._announce_please_wait()
        self.action_changed.emit(tr("action_init_mtkclient"))
        self._log(tr("action_init_mtkclient"))

        if self._cancelled:
            self.finished.emit(False, "USER_CANCELLED")
            return

        cmd = [sys.executable]
        repo_root = str(Path(__file__).resolve().parent.parent)
        env = dict(os.environ)
        if "PYTHONPATH" in env:
            env["PYTHONPATH"] = f"{repo_root}{os.pathsep}{env['PYTHONPATH']}"
        else:
            env["PYTHONPATH"] = repo_root

        if not getattr(sys, "frozen", False):
            cmd.extend(["-m", "src.app"])
        platform_name = str(getattr(self, "package_platform", "") or "")
        # Trailing optional facts the CLI forwards to mtkclient: a user-chosen
        # Download Agent (--loader) and an authentication file (--auth). Empty
        # strings keep the bundled DA / no-auth behaviour.
        cmd.extend([
            "--flash-cli",
            str(extract_dir),
            str(scatter_file),
            platform_name,
            str(self.package_path or ""),
            str(self.model or ""),
            self.da_file,
            self._resolve_auth_file(),
        ])

        final_ok = None
        final_msg = ""
        self._stalled_out = False
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=repo_root,
                env=env,
            )
            self._start_attempt_watchdog()
            stall_detector = _MtkOutputStallDetector(
                self._on_mtk_output_stalled_subprocess,
                window=MTK_OUTPUT_STALL_WINDOW,
                interval=0.5,
            )
            self._mtk_output_stall_detector = stall_detector
            stall_detector.start()
            for line in iter(self._process.stdout.readline, ""):
                line = line.rstrip()
                if not line:
                    continue
                self._touch_attempt()
                if line.startswith("[PROGRESS] "):
                    try:
                        pct = int(line[len("[PROGRESS] "):])
                    except ValueError:
                        pct = None
                    # Stay at 0 until the tool has moved past the USB search.
                    if pct is not None and (self._past_usb_search or pct == 0):
                        self.progress.emit(0 if not self._past_usb_search else pct)
                        if pct > self._mtk_cli_progress_hwm:
                            self._mtk_cli_progress_hwm = pct
                            self._note_mtk_subprocess_write_progress()
                elif line.startswith("[ETA] "):
                    try:
                        eta_val = int(line[len("[ETA] "):])
                        self.eta_changed.emit(eta_val)
                    except ValueError:
                        pass
                elif line.startswith("[STEP] "):
                    step = line[len("[STEP] "):]
                    if step not in (STEP_WAITING, STEP_PLEASE_WAIT):
                        self._leave_usb_search()
                    if step == STEP_WRITE:
                        self._note_mtk_subprocess_write_progress()
                    self.step_changed.emit(step)
                elif line.startswith("[ACTION] "):
                    self.action_changed.emit(line[len("[ACTION] "):])
                elif line.startswith("[LOG] "):
                    payload = line[len("[LOG] "):]
                    self._handle_mtk_subprocess_log(payload)
                elif line.startswith("[RESULT] "):
                    parts = line[len("[RESULT] "):].split(" ", 1)
                    final_ok = bool(int(parts[0]))
                    final_msg = parts[1] if len(parts) > 1 else ""
                else:
                    self._log(line)
                    # When running in subprocess mode, [PROGRESS] lines emitted from
                    # the child worker's byte-level tracking drive the overall progress bar.
                    # Raw partition progress lines must not overwrite it, but they do
                    # prove that the DA is still writing and refresh USB-loss liveness.
                    if line.lower().startswith("progress:"):
                        self._note_mtk_partition_progress(line)
                    else:
                        self._classify_mtk_output(line)
                    self._note_mtk_output_for_stall_check(line)

            self._stop_attempt_watchdog()
            stall_detector.stop()
            self._mtk_output_stall_detector = None
            self._process.wait()
            # The child emits [RESULT] only after its flash worker has reached a
            # terminal state. A successful write may reboot the player and make
            # USB disappear before the child exits; do not let that expected
            # re-enumeration override the explicit success record.
            if final_ok is True:
                self.finished.emit(True, "")
                return
            if self._is_disconnected or is_disconnection_error(self._tool_error) or is_disconnection_error(final_msg):
                self._log("MTKClient flash terminated due to device disconnection.")
                self.cancel()
                self.usb_disconnected.emit()
                self.finished.emit(False, "USB_DISCONNECTED")
                return
            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            if self._process.returncode != 0 and getattr(self, "_past_usb_search", False) and not is_mtk_device_connected():
                self._log("Install ended with error and device is no longer connected to USB.")
                self._is_disconnected = True
                self.cancel()
                self.usb_disconnected.emit()
                self.finished.emit(False, "USB_DISCONNECTED")
                return
            if getattr(self, "_stalled_out", False):
                self.finished.emit(False, "MTK_OUTPUT_STALLED")
                return
            if self._attempt_expired:
                self.finished.emit(False, ERR_ATTEMPT_TIMEOUT)
                return
            if self._tool_error:
                self._log(f"Install failed: {self._tool_error}")
                self.finished.emit(False, "TOOL_ERROR")
                return
            # The [RESULT] line is the authoritative completion signal from
            # MTKClient: it carries the real outcome even when the process exit
            # code disagrees (the CLI can exit non-zero while the flash succeeded,
            # mirroring the SP Flash Tool behaviour where the log is the source of
            # truth). Honour it first; fall back to the exit code only when no
            # result line was emitted.
            if final_ok is not None:
                self.finished.emit(False, final_msg or "MTK_FLASH_FAILED")
                return
            if self._process.returncode == 0:
                self.finished.emit(True, "")
                return
            self.finished.emit(False, final_msg or f"MTK_FLASH_FAILED ({self._process.returncode})")
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

    # -- MTKClient output-stall helpers --------------------------------------
    def _handle_mtk_subprocess_log(self, payload: str) -> None:
        """Forward child output and keep parent-side liveness watchdogs current."""
        is_partition_progress = str(payload).lstrip().lower().startswith("progress:")
        if is_partition_progress:
            self._note_mtk_partition_progress(payload)
        self._log(payload)
        # Per-partition byte progress must not be interpreted as global flash
        # progress; the child already sends the aggregate [PROGRESS] signal.
        if not is_partition_progress:
            self._classify_mtk_output(payload)
        self._note_mtk_output_for_stall_check(payload)

    def _note_mtk_subprocess_write_progress(self):
        """Record child-process write activity for the parent USB watchdogs."""
        self._note_install_progress()
        self._touch_attempt()
        self._hold_device_loss(MTK_WRITE_PROGRESS_USB_GRACE_SECONDS)

    def _update_mtk_partition_action(self, line: str) -> None:
        """Show the current partition's own percentage in the install subtitle."""
        match = re.search(
            r"Progress:\s*([\d.]+)%\s+Write\s+(.+?)\s+\(([\d.]+)MB/([\d.]+)MB\)",
            line,
            re.IGNORECASE,
        )
        if not match:
            return
        try:
            pct = int(float(match.group(1)))
        except ValueError:
            return
        task = layman_task_name(match.group(2).strip())
        self.action_changed.emit(
            tr("action_writing_task_fmt").format(task=task, pct=pct)
        )

    def _note_mtk_partition_progress(self, line: str) -> None:
        """Refresh liveness only when the child reports a larger byte position."""
        self._update_mtk_partition_action(line)
        match = re.search(
            r"Progress:\s*[\d.]+%\s+Write\s+(.+?)\s+\(([\d.]+)MB/([\d.]+)MB\)",
            line,
            re.IGNORECASE,
        )
        if not match:
            return
        partition = match.group(1).strip().upper()
        try:
            done_mb = float(match.group(2))
        except ValueError:
            return
        previous = self._mtk_cli_partition_progress.get(partition, -1.0)
        if done_mb <= previous:
            return
        self._mtk_cli_partition_progress[partition] = done_mb
        self._note_mtk_subprocess_write_progress()

    def _other_backend_available_for(self, method):
        resolved = backend_method(method)
        if is_mtk_method(method):
            return _sp_backend_available()
        return _mtk_backend_available()

    def _mtk_backend_available(self):
        return mtkclient_command() is not None

    def _sp_backend_available(self):
        if paths.IS_MAC:
            return False
        if paths.IS_WINDOWS:
            return find_sp_flash_tool() is not None
        try:
            from . import linux_sp_flash
            return linux_sp_flash.ensure_linux_sp_flash_tool(
                progress_cb=lambda pct, msg: None
            )[0]
        except Exception:
            return False

    def _mtk_stall_context(self):
        method = getattr(self, "method", "auto")
        resolved = backend_method(method)
        is_mtk = resolved == METHOD_MTK
        mtk_only = (
            paths.IS_MAC
            or resolved == METHOD_MTK
            or windows_only_method() == METHOD_MTK
            or (is_mtk and not self._other_backend_available_for(self.method))
        )
        can_fallback = (
            not paths.IS_MAC
            and not mtk_only
            and self._other_backend_available_for(self.method)
        )
        return {
            "is_mtk": is_mtk,
            "mtk_only": mtk_only,
            "can_fallback": can_fallback,
            "resolved_method": resolved,
        }

    def _finalize_mtk_stall(self, reason_code, title_key=None, desc_key=None, desc_fmt=None):
        context = self._mtk_stall_context()
        if context["mtk_only"]:
            title = tr("err_only_mtkclient_title")
            desc = tr("err_only_mtkclient_desc")
        elif context["can_fallback"]:
            title = tr("err_mtk_output_stall_title")
            desc = tr("err_mtk_output_stall_desc", seconds=int(MTK_OUTPUT_STALL_WINDOW))
            if context["is_mtk"]:
                desc += " " + tr("err_mtk_output_stall_fallback_available")
        else:
            title = title_key or tr("err_mtk_output_stall_title")
            if desc_key:
                desc = tr(desc_key)
            elif desc_fmt:
                desc = desc_fmt
            else:
                desc = tr("err_mtk_output_stall_desc", seconds=int(MTK_OUTPUT_STALL_WINDOW))
        self._log(f"MTKClient output stall: {reason_code}")
        self.finished.emit(False, reason_code)
        if not getattr(self, "_stall_prompt_handled", False):
            self._stall_prompt_handled = True
            self._deferred_restart_prompt = {
                "title": title,
                "desc": desc,
            }

    def _mtk_stall_prompt_text(self):
        """The restart-and-quit prompt text, if one was deferred after the run."""
        return getattr(self, "_deferred_restart_prompt", None)

    def _note_mtk_output_for_stall_check(self, text):
        detector = getattr(self, "_mtk_output_stall_detector", None)
        if detector is not None:
            detector.note(text)

    def _note_mtk_write_progress(self):
        detector = getattr(self, "_mtk_output_stall_detector", None)
        if detector is not None:
            detector.progress()

    def _on_mtk_output_stalled(self, line="", elapsed=0.0):
        self._log(f"MTKClient stalled on line: {line} ({int(elapsed)}s)")
        self._finalize_mtk_stall("MTK_OUTPUT_STALLED")

    def _on_mtk_output_stalled_subprocess(self, line="", elapsed=0.0):
        # Called from the detector thread while the --flash-cli subprocess is
        # still running. Stop the backend so it cannot keep the device half-
        # written, then report the stall through the same finalizer.
        self._log(f"MTKClient stalled on line: {line} ({int(elapsed)}s); stopping the backend")
        self._stalled_out = True
        try:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
        except Exception:
            pass
        self._finalize_mtk_stall("MTK_OUTPUT_STALLED")

    def _close_mtk_session(self, mtk) -> None:
        """Close the MTK session port cleanly matching the reference 1.1.7 build."""
        if mtk is None:
            return
        try:
            if hasattr(mtk, "port") and mtk.port is not None:
                mtk.port.close(reset=not IS_WINDOWS)
        except OSError as e:
            self._log(f"⚠️ Failed to close USB port: {e}")
        except Exception as e:
            logger.debug("Failed to close mtk session cleanly: %s", e)

    def _end_mtk_run_successfully(self, mtk, da_handler=None) -> None:
        """The last step of a successful in-process MTKClient run.

        Matches the reference 1.1.7 build: logs completion, signals success,
        and releases the MTKClient session cleanly without premature reset.
        """
        self._finished = True
        self._flush_log()
        self.step_changed.emit(STEP_DONE)
        if not getattr(self, "_is_rockbox_flow", False):
            self.progress.emit(100.0)
        else:
            self.progress.emit(90.0)
        self.action_changed.emit(tr("action_flash_complete"))
        self._log("Flash complete! Disconnect USB and reboot the device.")
        self._close_mtk_session(mtk)
        self.finished.emit(True, "")

    def _reboot_after_mtk_success(self, mtk, da_handler=None) -> bool:
        """Restart the player so the firmware just written boots by itself.

        Only ever called on a successful in-process MTKClient run: the DA
        session is still open at that point, and asking it to reboot leaves the
        player starting the new firmware on its own instead of parked in
        download mode waiting for a power cycle. Never raises — the install has
        already succeeded and a device that rebooted or dropped off USB early
        must not turn that into a failure.
        """
        if not self.reboot_after_success:
            self._log("Flash complete! Disconnect USB and reboot the device.")
            return False
        self._log("Flash complete! Firing mtk reset to boot the device...")
        from . import mtk_api
        rebooted = mtk_reset_to_normal(mtk, da_handler)
        if rebooted:
            self._log("Device reset command sent successfully. Rebooting into installed firmware...")
            return True
        self._log("Flash complete! Disconnect USB and reboot the device.")
        return False

    def _flash_via_mtkclient_core(self, extract_dir, scatter_file, platform="", da_file="", auth_file=""):
        # Raw output printed while this backend runs comes from MTKClient.
        self._tool_tag = "MTK"
        # A Download Agent / auth file chosen by the user (MediaTek Installer)
        # beats whatever this worker was constructed with; both are optional.
        if da_file:
            self.da_file = str(da_file).strip()
        if auth_file:
            self.auth_file = str(auth_file).strip()
        self._announce_please_wait()
        self.action_changed.emit(tr("action_init_mtkclient"))
        self._log(tr("action_init_mtkclient"))
        # Measured write window: the install card turns this into remaining
        # seconds, and the Device Prep stage adds its own half on top of it.
        self._total_eta = _CombinableEta()
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

        align_scatter_disk_images(scatter_file, log=self._log)
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
        # mtkclient accepts a Download Agent override and an auth file; report
        # which ones this run will actually use so a failure is diagnosable.
        if self.da_file and Path(self.da_file).is_file():
            self._log(f"Download agent: {os.path.basename(self.da_file)}")
        elif self.da_file:
            self._log(f"Download agent not found, using mtkclient's built-in DA: {self.da_file}")
            self.da_file = ""
        if self.auth_file and Path(self.auth_file).is_file():
            self._log(f"Auth file: {os.path.basename(self.auth_file)}")
        elif self.auth_file:
            self._log(f"Auth file not found, continuing without one: {self.auth_file}")
            self.auth_file = ""

        self._announce_please_wait()
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
            self._classify_mtk_output(text)
            self._note_mtk_output_for_stall_check(text)

        def _on_mtk_stall(idle):
            seconds = int(idle)
            self._log(
                f"No response from the device for {seconds}s while configuring the DA."
            )
            platform = str(getattr(self, "package_platform", "") or "").upper()
            model = str(getattr(self, "package_model", self.model) or "").upper()
            if "6572" in platform or model == "Y1":
                self._log(
                    "Stage-2 DA setup is waiting for a response after RAM initialization. "
                    "If this repeats on a Y1, power the player off and reconnect without "
                    "holding buttons or pressing the reset pinhole, to enter Preloader mode."
                )
            self.action_changed.emit(tr("action_da_stall"))

        watcher = _StallWatcher(_on_mtk_stall)

        def _on_connected(mtk):
            self._leave_usb_search()
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
            # Report the mode mtkclient actually found: PID 0x2000 (and the
            # BL-version probe) is preloader mode, only 0x0003 is BROM, and
            # the difference is what the user has to act on.
            if mtk_api.is_brom(mtk):
                msg = tr("action_detected_brom")
                if "6572" in str(getattr(self, "package_platform", "")) or getattr(self, "package_model", "") == "Y1":
                    self._log("Notice: Device connected in BROM mode. For MT6572 (Y1), if DRAM initialization stalls, reconnect in Preloader mode (power off and connect USB without holding buttons or pinhole reset).")
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
                # Reference sequence: init(None, None), then connect. The
                # package's preloader image is not pushed as an override; the
                # device's own preloader answers and the image is written with
                # the other partitions.
                session = mtk_api.init(
                    loader=self.da_file or None,
                    preloader=None,
                    auth=self.auth_file or None,
                )
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
                        session = mtk_api.init(
                            loader=self.da_file or None,
                            preloader=preloader_path,
                            auth=self.auth_file or None,
                        )
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

        def _attach_primed_mtk(primed_session):
            import logging as _logging
            from mtkclient.Library.DA.mtk_da_handler import DaHandler

            primed_session.config.hwparam_path = str(extract_dir)
            if getattr(primed_session.config, "target_config", None) is None:
                self._log("⚠️ Primed session target_config is missing; reconnect the device and try again.")
                try:
                    primed_session.port.close(reset=True)
                except Exception:
                    pass
                return None, None

            self._leave_usb_search()
            self.step_changed.emit(STEP_DETECT)
            self.progress.emit(12)
            self.step_changed.emit(STEP_DOWNLOAD_DA)
            self.action_changed.emit(tr("step_download_da"))
            self.progress.emit(14)

            def _on_da_progress(pos):
                if self._cancelled:
                    return
                val = pos if pos <= 1.0 else (pos / 100.0)
                mapped = 14 + int(min(1.0, max(0.0, val)) * 4)
                self.progress.emit(min(18, max(14, mapped)))

            if hasattr(primed_session, "config"):
                primed_session.config.guiprogress = _ProgressEmitter(_on_da_progress)
            da_handler = DaHandler(primed_session, _logging.INFO)
            res_mtk = da_handler.configure_da(primed_session)
            if res_mtk is None:
                self._log("DA upload failed")
                return None, None
            return res_mtk, da_handler

        watcher.start()
        mtk_output_stall_detector = _MtkOutputStallDetector(
            self._on_mtk_output_stalled,
            window=MTK_OUTPUT_STALL_WINDOW,
            interval=0.5,
        )
        self._mtk_output_stall_detector = mtk_output_stall_detector
        mtk_output_stall_detector.start()
        attempt = 0
        try:
            while True:
                attempt += 1
                da_handler = None
                if getattr(self, "_primed_mtk", None) is not None:
                    self._log("Reusing the device session primed during monitoring")
                    mtk, da_handler = _attach_primed_mtk(self._primed_mtk)
                    self._primed_mtk = None
                else:
                    mtk = _open_session()
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
                max_attempts = MTK_DA_ATTEMPTS
                if attempt >= max_attempts or getattr(self, "_attempt_expired", False) or self._cancelled:
                    break
                self._log("The download agent did not come up.")
                self.action_changed.emit(tr("action_da_wait_back"))
                if not self._wait_for_retry(attempt):
                    break
        except BaseException as e:
            self._log(f"Device connection failed: {e}\n{traceback.format_exc()}")
            watcher.stop()
            mtk_output_stall_detector.stop()
            self._mtk_output_stall_detector = None
            self._flush_log()
            self.finished.emit(False, "CONNECTION_FAILED")
            return
        if mtk is None or da_handler is None:
            self._log("Device connection returned None")
            watcher.stop()
            mtk_output_stall_detector.stop()
            self._mtk_output_stall_detector = None
            self._flush_log()
            self.finished.emit(False, "CONNECTION_FAILED")
            return

        self.step_changed.emit(STEP_DOWNLOAD_BL)
        self.progress.emit(18)
        self.action_changed.emit(tr("action_device_connected"))
        self._apply_mtk_write_strategy(mtk, image_files)

        # Refuse before writing anything when the package's chip and the chip
        # that answered are different. The 1.1.7 build does the same check
        # right after the DA comes up; without it a package for another player
        # writes at that player's addresses and the install "succeeds" into a
        # layout this device does not have.
        if not self._check_package_platform(mtk, platform):
            real = self._device_platform_name(mtk) or "unknown"
            self._log(
                f"Firmware package does not match device: package platform "
                f"{platform or 'unknown'}, device chip {real}"
            )
            self._log("Confirm that the selected firmware package matches this device model.")
            try:
                if hasattr(mtk, "port") and mtk.port is not None:
                    mtk.port.close()
            except Exception:
                pass
            self.finished.emit(False, "DEVICE_PACKAGE_MISMATCH")
            return

        # The write plan is the 1.1.7 build's, not a guess: every downloadable
        # image in scatter order, the preloader last, and genuine package
        # MBR/EBR tables written while empty stubs are skipped. Nothing is formatted
        # before the run, so an interrupted install can always be retried from a
        # player whose preloader still answers.
        plan = build_mtk_write_plan(
            scatter_file,
            getattr(self, "package_model", "") or self.model,
            preloader_first=False,
        )
        if not plan:
            self._log("Error: no writable partitions in scatter file")
            self.finished.emit(False, "NO_IMAGE_FILES")
            return
        self._log(f"Write plan ({len(plan)} partition(s), scatter addresses):")
        for step in plan:
            off_str = f"0x{step['offset']:X}" if step.get("offset") is not None else "N/A"
            self._log(
                f"  {step['name']:<12} {step['path'].name:<28} "
                f"{off_str} {step['parttype']} "
                f"(partition {int(step.get('partition_size') or 0) / 1048576:.1f}MB)"
            )

        # The Chinese build issues no GPT sync before its writes (the reference
        # sequence is init, connect, then one ``w`` per partition), so the
        # package's own partition images are written as they are.

        # The Chinese build reads nothing back before its writes: the reference
        # sequence is init, connect, then one ``w`` per partition. On this
        # legacy DA a pre-write ``readflash`` is not answered either (the DA
        # stops responding and the player drops off the USB bus right after
        # Stage 2), so the package's own layout is what gets written.
        live_parts = []
        live_by_start = dict(live_parts or ())
        for step in plan:
            if step["parttype"] != "user" or step.get("offset") is None:
                step["live_size"] = 0
                continue
            live_size = int(live_by_start.get(int(step["offset"])) or 0)
            step["live_size"] = live_size
            scatter_size = int(step.get("partition_size") or 0)
            if live_size:
                if scatter_size and live_size != scatter_size:
                    self._log(
                        f"  {step['name']}: device partition is "
                        f"{live_size / 1048576:.1f}MB, package says "
                        f"{scatter_size / 1048576:.1f}MB; using the device's size"
                    )
            elif live_parts:
                self._log(
                    f"  {step['name']}: no device partition starts at "
                    f"0x{step['offset']:X}; writing at the package's address"
                )

        total = len(plan)
        total_bytes = sum(int(step["length"]) for step in plan)
        completed_bytes = 0
        part_completed_bytes = 0
        cur_part_name = ""
        cur_part_len = 0
        last_log_time = 0.0
        last_log_pct = -1.0

        def on_segment_done(seg_len):
            nonlocal completed_bytes, part_completed_bytes
            completed_bytes += seg_len
            part_completed_bytes += seg_len
            self._touch_attempt()
            self._note_install_progress()
            self._note_mtk_write_progress()
            on_mtk_write_progress(0)

        def on_mtk_write_progress(pos):
            nonlocal last_log_time, last_log_pct
            if self._cancelled:
                return
            self._touch_attempt()
            self._note_install_progress()
            self._note_mtk_write_progress()
            current_done = completed_bytes + pos
            current_part_done = part_completed_bytes + pos
            if total_bytes > 0:
                self._emit_progress(percent_of(current_done, total_bytes, 20, 75))
                # Install remaining, measured from this run's own write rate:
                # the card scores it first and the Device Prep stage adds its
                # half on top of the same number.
                remaining = self._total_eta.note(current_done, total_bytes)
                self.eta_changed.emit(remaining if remaining > 0 else -1)

            now = time.monotonic()
            part_pct = (current_part_done / max(1, cur_part_len)) * 100.0 if cur_part_len > 0 else 0.0
            if now - last_log_time >= 0.5 or part_pct - last_log_pct >= 2.0 or current_part_done >= cur_part_len:
                last_log_time = now
                last_log_pct = part_pct
                rate = getattr(self._eta, "_rate", 0.0) or 0.0
                speed_mb = rate / (1024 * 1024)
                part_done_mb = current_part_done / (1024 * 1024)
                part_tot_mb = cur_part_len / (1024 * 1024)

                sec_left = self._eta.note(current_done, total_bytes) if total_bytes > 0 else None
                if sec_left is not None and sec_left > 0:
                    min_rem = sec_left // 60
                    sec_rem = sec_left % 60
                    if min_rem > 60:
                        h = min_rem // 60
                        min_rem = min_rem % 60
                        hinfo = f"{h:02d}h:{min_rem:02d}m:{sec_rem:02d}s left"
                    elif min_rem > 0:
                        hinfo = f"{min_rem:02d}m:{sec_rem:02d}s left"
                    else:
                        hinfo = f"{sec_rem:02d}s left"
                else:
                    hinfo = "-- left"

                line = (
                    f"Progress: {part_pct:.1f}% Write {cur_part_name} "
                    f"({part_done_mb:.1f}MB/{part_tot_mb:.1f}MB), {speed_mb:.2f} MB/s ({hinfo})"
                )
                self._log(line)
                task_label = layman_task_name(cur_part_name)
                self.action_changed.emit(
                    tr("action_writing_task_fmt").format(
                        task=task_label, pct=int(part_pct)
                    )
                )

        if hasattr(mtk, "config"):
            mtk.config.guiprogress = _ProgressEmitter(on_mtk_write_progress)

        try:
            for i, step in enumerate(plan):
                part_name = step["name"]
                file_path = step["path"]
                if self._cancelled:
                    self.finished.emit(False, "USER_CANCELLED")
                    return
                if i == 0:
                    # The install is considered started once the DA actually
                    # writes images (the reference updater derives install
                    # progress from image writes, not from the handshake).
                    self._leave_usb_search()
                    self._stop_attempt_watchdog()
                    self._note_install_progress()
                    watcher.stop()
                    self.step_changed.emit(STEP_WRITE)
                    self.progress.emit(20)
                if i == 0:
                    self._eta.reset()
                    self._total_eta.reset()
                self.action_changed.emit(tr("action_install_in_progress"))
                self._log(tr("log_install_writing"))
                self._log(tr("action_writing_part_fmt").format(
                    part=part_name, i=i + 1, total=total, file=file_path.name,
                ))
                task_label = layman_task_name(part_name)
                self.action_changed.emit(
                    tr("action_writing_task_fmt").format(
                        task=task_label, pct=0
                    )
                )

                offset = step["offset"]
                target_part = step["parttype"]
                part_size = int(step.get("partition_size") or 0)
                live_size = int(step.get("live_size") or 0)
                if live_size:
                    # The device's own partition is the real bound: a package
                    # built for a 2 GB userdata must not be written into the
                    # 34 MB userdata the player currently has.
                    part_size = min(part_size, live_size) if part_size else live_size
                is_preloader = part_name.lower() == "preloader"
                active_file = file_path
                temp_unsparse = None
                sparse_regions = None

                # Android sparse images must be raw before a DA write, exactly
                # as the shipped build desparses system/cache/userdata first.
                # The converter's region list matters: the 1.1.7 build writes
                # only the sparse image's data extents, never the empty fill
                # between them, so a 2 GB userdata container does not become a
                # 2 GB transfer. Nothing else is rewritten: no signed-wrapper
                # strip, no boot header (the preloader path owns that).
                if _is_android_sparse(active_file):
                    self._log(tr("log_sparse_fmt").format(part=part_name))
                    self.action_changed.emit(tr("action_unpacking_fmt").format(part=part_name))
                    unsparse_dir = Path(extract_dir) / ".unsparse_cache"
                    temp_unsparse, sparse_regions = _convert_sparse_to_raw(
                        active_file, unsparse_dir
                    )
                    active_file = temp_unsparse

                write_len = active_file.stat().st_size if active_file.exists() else 0
                file_off = 0
                # A desparsed image is bigger than the sparse file on disk, so
                # the bar's denominator has to grow with it or it reaches the
                # end of the write slice early. Region writes move only the
                # actual data extents (matching Chinese build sum(region_len)).
                if sparse_regions:
                    transfer_len = sum(int(seg_len) for _, seg_len in sparse_regions)
                else:
                    transfer_len = write_len
                # The denominator follows what is actually transferred (the
                # desparsed raw size, or the sparse data extents), not the
                # on-disk size of a sparse container or empty filler.
                effective_len = transfer_len
                target_len = int(step["length"])
                if target_len > 0:
                    total_bytes += effective_len - target_len

                cur_part_name = part_name
                cur_part_len = transfer_len
                part_completed_bytes = 0
                last_log_pct = -1.0

                try:
                    # The package must fit the partition the scatter describes.
                    # A mismatch here is what the 1.1.7 build refuses up front
                    # ("sparse data exceeds partition", "image is too large") —
                    # handed to the DA it becomes an unexplained NACK near the
                    # end of the partition.
                    # Android sparse images can contain data extents up to the total filesystem
                    # container boundary (e.g. 2 GB or 4 GB), while the physical partition or
                    # storage on device may be bounded. Fill extents are DONT_CARE, so clip
                    # sparse regions to part_size so out-of-bound sectors are never sent to the DA.
                    daloader = getattr(mtk, "daloader", None)
                    daconfig = getattr(daloader, "daconfig", None)
                    legacy_storage = getattr(daconfig, "legacy_storage", None)
                    emmc = getattr(legacy_storage, "emmc", None)
                    emmc_ua_size = getattr(emmc, "m_emmc_ua_size", 0) or 0
                    if target_part == "user" and emmc_ua_size > 0 and offset is not None:
                        max_user_space = max(0, emmc_ua_size - offset)
                        if part_size:
                            part_size = min(part_size, max_user_space)
                        else:
                            part_size = max_user_space

                    if sparse_regions and part_size:
                        sparse_regions, clipped = _clip_sparse_regions(sparse_regions, part_size)
                        if clipped:
                            self._log(
                                f"  Sparse data for {part_name} clipped to partition bound "
                                f"({part_size / 1048576:.1f}MB)"
                            )
                        transfer_len = sum(int(seg_len) for _, seg_len in sparse_regions)
                        cur_part_len = transfer_len
                    elif part_size and transfer_len > part_size:
                        raise PackageLayoutError(
                            f"{part_name} image is {transfer_len / 1048576:.1f}MB but the "
                            f"device's partition is {part_size / 1048576:.1f}MB "
                            "(possible package/scatter mismatch)"
                        )
                    if is_preloader:
                        self._write_mtk_preloader(da_handler, mtk, str(active_file))
                    elif sparse_regions:
                        self._write_mtk_sparse_regions(
                            mtk,
                            part_name,
                            str(active_file),
                            sparse_regions,
                            offset,
                            target_part,
                            bytes_progress_cb=on_segment_done,
                            segment_bytes=getattr(self, "_segmented_write_bytes", 0),
                        )
                    else:
                        self._write_mtk_partition(
                            da_handler,
                            mtk,
                            part_name,
                            str(active_file),
                            target_part,
                            offset=offset,
                            file_offset=file_off,
                            write_length=write_len,
                            bytes_progress_cb=on_segment_done,
                        )
                except BaseException as e:
                    if is_disconnection_error(str(e), e):
                        self._log(f"USB disconnection detected during partition write: {e}")
                        self._is_disconnected = True
                        self.cancel()
                        self.usb_disconnected.emit()
                        self.finished.emit(False, "USB_DISCONNECTED")
                        return
                    self._log(f"Writing {part_name} failed: {e}\n{traceback.format_exc()}")
                    if isinstance(e, PackageLayoutError):
                        self._log(
                            "The firmware package does not fit this device's layout; "
                            "nothing after this partition was written."
                        )
                        self.finished.emit(False, "DEVICE_PACKAGE_MISMATCH")
                        return
                    # The player can still be reflashed over USB even when it
                    # cannot boot: BROM mode answers whatever is in the flash.
                    # Whole-run retry is the supported recovery (Start Flash
                    # again), never a per-partition retry.
                    self._log(
                        "The player can still be reflashed over USB in BROM mode: keep the "
                        "cable connected and click Start Flash to try again. If that "
                        "fails, unplug USB, wait 3 seconds, reconnect, and flash again."
                    )
                    self.finished.emit(False, "WRITE_FAILED")
                    return
                finally:
                    if temp_unsparse and temp_unsparse.exists():
                        try:
                            temp_unsparse.unlink(missing_ok=True)
                        except Exception:
                            pass
                    if sparse_regions:
                        actual_len = sum(int(seg_len) for _, seg_len in sparse_regions)
                    elif active_file == temp_unsparse:
                        actual_len = write_len
                    else:
                        actual_len = file_path.stat().st_size if file_path.exists() else 0
                    if actual_len > part_completed_bytes:
                        completed_bytes += (actual_len - part_completed_bytes)
                    if total_bytes > 0:
                        self._emit_progress(percent_of(completed_bytes, total_bytes, 20, 75))
                        remaining = self._total_eta.note(completed_bytes, total_bytes)
                        self.eta_changed.emit(remaining if remaining > 0 else -1)

            if self._cancelled:
                self.finished.emit(False, "USER_CANCELLED")
                return
            if self._tool_error:
                # The tool said a command failed and then stopped; the player
                # is very likely half written. Say so instead of "complete".
                self._log(f"Install failed: {self._tool_error}")
                self.step_changed.emit(STEP_WRITE)
                self.finished.emit(False, "TOOL_ERROR")
                return

            self._end_mtk_run_successfully(mtk, da_handler)
        finally:
            self._flush_log()
            watcher.stop()
            if mtk_output_stall_detector is not None:
                mtk_output_stall_detector.stop()
            self._mtk_output_stall_detector = None
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

    def _await_usb_watchdog_verdict(self, window: float = 3.5) -> None:
        """Hold the success decision until the USB watchdog has had its window.

        Returns early once the watchdog has flagged a disconnect or the run was
        cancelled. A run that never passed USB search has no watchdog verdict.
        """
        if not getattr(self, "_past_usb_search", False):
            return
        deadline = time.monotonic() + window
        while time.monotonic() < deadline:
            if self._is_disconnected or self._cancelled:
                return
            time.sleep(0.1)

    def _is_legacy_mode(self, mtk):
        mode = getattr(getattr(mtk, "daloader", None), "flashmode", None)
        return mode == 3 or mode == "LEGACY"

    def _apply_mtk_write_strategy(self, mtk, image_files) -> int:
        """Drive the legacy DA with the same transfer size the reference build uses.

        The bundled DA takes the chunk size from ``config.write_chunk_size``; left
        unset it writes 1 MiB per USB transfer, which is not what the Chinese
        build does for MT6572/MT6582. Returns the size it applied (0 when the
        session is not a legacy DA, so nothing is claimed in the log).
        """
        if not self._is_legacy_mode(mtk):
            self._segmented_write_bytes = 0
            return 0
        chunk = legacy_write_chunk_size([path for _name, path in image_files])
        try:
            mtk.config.write_chunk_size = chunk
        except Exception as e:
            logger.debug("Could not set the legacy write chunk size: %s", e)
        self._segmented_write_bytes = LEGACY_SEGMENTED_WRITE_BYTES
        self._log(
            f"Legacy DA write chunk size: {chunk // 1024} KiB "
            "(matches the reference build)"
        )
        return chunk

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
            self._touch_attempt()

    def _read_live_partitions(self, mtk, extract_dir, length: int = 64 * 1024):
        """Read the device's own MBR/EBR chain before anything is written.

        Read-only and best-effort: the head of the user area holds the live
        table, and a chain that cannot be parsed leaves the plan untouched.
        """
        daloader = getattr(mtk, "daloader", None)
        if daloader is None or not hasattr(daloader, "readflash"):
            return []
        head = Path(extract_dir) / ".unsparse_cache" / "live_table_head.bin"
        try:
            head.parent.mkdir(parents=True, exist_ok=True)
            daloader.readflash(
                addr=0,
                length=length,
                filename=str(head),
                parttype="user",
                display=False,
            )
            data = head.read_bytes()
        except BaseException as e:
            self._log(f"Could not read the device's partition table: {e}")
            return []
        finally:
            try:
                head.unlink(missing_ok=True)
            except OSError:
                pass
        partitions = _parse_mbr_partitions(data)
        if not partitions:
            self._log(
                "No MBR/EBR table found at the head of the user area; "
                "writing at the package's own addresses."
            )
            return []
        self._log("Device partition table (read back before writing):")
        for start, size in partitions:
            self._log(f"  0x{start:X}  {size / 1048576:.1f}MB")
        return partitions

    def _read_flash_blob(self, mtk, parttype: str, addr: int, length: int) -> bytes:
        daloader = getattr(mtk, "daloader", None)
        if daloader is None or not hasattr(daloader, "readflash"):
            return b""
        try:
            data = daloader.readflash(
                addr=addr, length=length, filename="", parttype=parttype, display=False
            )
            if isinstance(data, (bytes, bytearray)):
                return bytes(data)
        except Exception as e:
            logger.debug("readflash failed: %s", e)
        return b""

    def _read_user_blob(self, mtk, addr: int, length: int) -> bytes:
        return self._read_flash_blob(mtk, "user", addr, length)

    def _write_gpt_blobs(
        self, mtk, primary_blob: bytes, backup_addr: int, backup_blob: bytes
    ) -> bool:
        writes = [
            (0, primary_blob, "primary GPT"),
            (backup_addr, backup_blob, "backup GPT"),
        ]
        for addr, blob, label in writes:
            ok = mtk.daloader.writeflash(
                addr=addr,
                length=len(blob),
                filename="",
                parttype="user",
                wdata=blob,
                display=False,
            )
            if not ok:
                err = getattr(getattr(mtk, "config", None), "last_error", "")
                detail = f" ({err})" if err else " (device did not confirm write)"
                raise RuntimeError(f"{label} write failed{detail}")
            # Readback verification
            actual = self._read_user_blob(mtk, addr, len(blob))
            if actual and actual != blob:
                raise RuntimeError(f"{label} readback verification failed")
            self._log(f"{label} write/readback verification passed")
        return True

    def _sync_gpt_with_scatter(
        self, mtk, gpt_cache: dict, scatter_file, create_when_missing: bool = True
    ) -> dict:
        """Align device partition table with scatter layout prior to flashing.

        For GPT layouts:
          1. Parses scatter partitions for EMMC_USER.
          2. Inspects device GPT header at LBA 0-33.
          3. Rebuilds or creates primary and backup GPT if missing or mismatched.
        For legacy MBR/EBR layouts:
          Preserves existing partition table images; skips GPT sync.
        """
        parts = _read_scatter_parts(scatter_file)
        if _legacy_scatter_has_mbr_ebr(parts):
            self._log(
                "GPT sync: legacy MBR/EBR package uses bundled partition-table images; skipping GPT sync"
            )
            return gpt_cache

        pagesize = max(512, int(getattr(getattr(mtk, "config", None), "pagesize", 512) or 512))
        gpt_read_len = 34 * pagesize
        user_size = _get_emmc_user_size(mtk)

        raw = self._read_user_blob(mtk, 0, gpt_read_len)
        if raw is None or len(raw) < pagesize:
            self._log("⚠️ GPT sync: could not read device table header from LBA 0; proceeding with scatter")
            return gpt_cache

        if user_size <= pagesize * 34:
            inferred_size = _infer_user_size_from_gpt_blob(raw, pagesize)
            if inferred_size > pagesize * 34:
                user_size = inferred_size
                self._log(f"GPT sync: USER capacity inferred from existing GPT: {user_size / 1048576:.1f}MB")

        scatter_parts = _parse_scatter_all_user_partitions(scatter_file, user_size)
        if not scatter_parts:
            self._log("⚠️ GPT sync: no EMMC_USER partition found in scatter; skipping sync")
            return gpt_cache

        # Check GPT signature
        has_gpt_sig = (
            len(raw) >= pagesize + 8
            and raw[pagesize : pagesize + 8] == b"EFI PART"
        )

        if not has_gpt_sig:
            if not create_when_missing:
                self._log("⚠️ GPT sync: standard GPT signature not detected on device; skipping sync")
                return gpt_cache
            if user_size <= pagesize * 34:
                self._log("⚠️ GPT sync: cannot create GPT without valid EMMC_USER capacity")
                return gpt_cache
            self._log("GPT sync: standard GPT signature not detected on device; creating GPT from scatter")
            try:
                primary_blob, backup_addr, backup_blob, new_cache = _build_gpt_blobs_from_scatter(
                    scatter_file, user_size, pagesize, raw
                )
                self._write_gpt_blobs(mtk, primary_blob, backup_addr, backup_blob)
                self._log(f"GPT created from scatter (primary=0x0, backup=0x{backup_addr:X})")
                return new_cache
            except Exception as e:
                self._log(f"⚠️ GPT creation from scatter encountered error: {e}")
                return gpt_cache

        # Existing GPT matches or needs update
        needs_rebuild = False
        for name, (s_addr, s_size) in scatter_parts.items():
            if name in gpt_cache:
                c_addr, c_size = gpt_cache[name]
                if c_addr != s_addr:
                    needs_rebuild = True
                    break

        if needs_rebuild and user_size > pagesize * 34:
            self._log("GPT partition entries do not match scatter; rebuilding primary/backup GPT from scatter")
            try:
                primary_blob, backup_addr, backup_blob, new_cache = _build_gpt_blobs_from_scatter(
                    scatter_file, user_size, pagesize, raw
                )
                self._write_gpt_blobs(mtk, primary_blob, backup_addr, backup_blob)
                self._log(f"GPT rebuilt from scatter (primary=0x0, backup=0x{backup_addr:X})")
                return new_cache
            except Exception as e:
                self._log(f"⚠️ GPT rebuild from scatter encountered error: {e}")

        self._log("GPT layout matches scatter; no sync needed")
        return gpt_cache

    def _device_platform_name(self, mtk) -> str:
        """Chip the connected device reports (``MT6572``, ``6572``, ...)."""
        cpu = getattr(getattr(mtk, "config", None), "cpu", None)
        if not cpu:
            chip = getattr(getattr(mtk, "config", None), "chipconfig", None)
            cpu = getattr(chip, "name", None)
        return str(cpu or "").strip()

    def _check_package_platform(self, mtk, package_platform) -> bool:
        """True unless the package's chip and the device's chip disagree.

        Either side being unknown means the run cannot be judged, so nothing is
        blocked (the same rule the 1.1.7 build applies).
        """
        pkg = _normalize_platform(package_platform)
        dev = _normalize_platform(self._device_platform_name(mtk))
        if not pkg or not dev or pkg.upper() == "UNKNOWN" or dev.upper() == "UNKNOWN":
            return True
        return pkg == dev

    def _read_flash_blob(self, mtk, parttype: str, addr: int, length: int) -> bytes | None:
        """Read a blob of flash data from device (matches Chinese build 1.1.7)."""
        try:
            daloader = getattr(mtk, "daloader", None)
            if daloader is None or not hasattr(daloader, "readflash"):
                return None
            data = daloader.readflash(
                addr=addr,
                length=length,
                filename="",
                parttype=parttype,
                display=False,
            )
            return bytes(data) if data else None
        except (SystemExit, Exception) as e:
            logger.debug("Failed to read flash blob (%s, addr=0x%X, len=%d): %s", parttype, addr, length, e)
            return None

    def _legacy_preloader_mismatch_targets(
        self, mtk, write_file: Path, target_parts: tuple[str, ...]
    ) -> list[str]:
        """Check if target boot partitions on device already match the package preloader.

        Matches Chinese build 1.1.7 FlashWorker._legacy_preloader_mismatch_targets:
        if the device's boot partition already matches the package preloader, skipping
        the write completely eliminates bricking risk on functioning devices.
        """
        try:
            expected = write_file.read_bytes()
        except OSError as e:
            logger.debug("Failed to read write_file for preloader match check: %s", e)
            return list(target_parts)
        mismatch_parts = []
        for part in target_parts:
            actual = self._read_flash_blob(mtk, part, 0, len(expected))
            if actual is not None and actual == expected:
                self._log(
                    f"  preloader match check: {part} already matches package image; skipping write"
                )
                continue
            if actual is None:
                self._log(
                    f"  preloader match check: could not read {part}; will write it"
                )
            else:
                self._log(
                    f"  preloader match check: {part} differs from package image; will write it"
                )
            mismatch_parts.append(part)
        return mismatch_parts

    def _write_mtk_preloader(self, da_handler, mtk, file_path_str) -> None:
        """Write the preloader the way the shipped 1.1.7 build does.

        A raw ``MMM\\x01`` preloader is wrapped in the BootROM's EMMC_BOOT /
        BRLYT header first, then written through the DA's WRITE_IMAGE command
        into eMMC boot1. eMMC boot1 holds a boot header: writing the bare image
        over it leaves a player the BootROM no longer recognises.

        This runs last, exactly as in 1.1.7. If the run dies anywhere earlier
        the player still enumerates with its old preloader and can be flashed
        again; that is the whole point of deferring it.
        """
        file_path = Path(file_path_str)
        try:
            storage = str(
                getattr(
                    getattr(getattr(mtk, "daloader", None), "daconfig", None),
                    "storage",
                    None,
                ).flashtype
            ).lower()
        except Exception:
            storage = "emmc"
        if "ufs" in storage:
            storage = "ufs"
        else:
            storage = "emmc"

        write_file = Path(
            _wrap_preloader_for_raw_boot(file_path, storage, log_cb=self._log)
        )
        if write_file != file_path:
            self._log(
                "  Legacy preloader uses BootROM header image "
                f"({write_file.stat().st_size} bytes)"
            )
        else:
            self._log("  Legacy preloader uses original image")
        try:
            mismatch_targets = self._legacy_preloader_mismatch_targets(mtk, write_file, ("boot1",))
            if not mismatch_targets:
                self._log("  Legacy preloader match check: boot1 already matches package image; preloader is intact")
                return

            write_size = write_file.stat().st_size
            daloader = getattr(mtk, "daloader", None)
            if daloader is not None and hasattr(daloader, "sdmmc_write_image"):
                da = getattr(daloader, "da", None)
                if da is not None and hasattr(da, "sdmmc_switch_part"):
                    try:
                        da.sdmmc_switch_part(1)
                    except Exception as e:
                        logger.debug("sdmmc_switch_part(1) failed: %s", e)
                ok = daloader.sdmmc_write_image(
                    addr=0,
                    length=write_size,
                    filename=str(write_file),
                    display=False,
                    parttype=1,  # MTK_DA_EMMC_PART_BOOT1
                )
                error = getattr(getattr(mtk, "config", None), "last_error", None)
                if not ok:
                    if error and str(error).startswith(_BENIGN_WRITE_IMAGE_NOTE):
                        self._log(
                            f"  Legacy WRITE_IMAGE note: {error}; data phase accepted, "
                            "treating as success for the old DA"
                        )
                    else:
                        raise RuntimeError(error or "WRITE_IMAGE returned failure")
                elif error:
                    # The legacy DA accepts the whole data phase and then stops
                    # waiting for the final ACK; it deliberately returns True
                    # there and records the timeout. The shipped build logs
                    # "final ACK missing, treating as success for old DA" and
                    # keeps the install. Only that one note is benign; a DA
                    # that returns True with any other recorded error is a
                    # failure, not a completed preloader write.
                    if not str(error).startswith(_BENIGN_WRITE_IMAGE_NOTE):
                        raise RuntimeError(error)
                    self._log(
                        f"  Legacy WRITE_IMAGE note: {error}; data phase accepted, "
                        "treating as success for the old DA"
                    )
            else:
                # A DA without WRITE_IMAGE falls back to an address write into
                # boot1; the wrapped image is what makes that safe.
                self._write_mtk_partition(
                    da_handler,
                    mtk,
                    "preloader",
                    str(write_file),
                    "boot1",
                    offset=0,
                    write_length=write_size,
                )
            self._log("  Legacy WRITE_IMAGE preloader write complete")
        finally:
            if write_file != file_path:
                try:
                    write_file.unlink(missing_ok=True)
                except OSError as e:
                    logger.debug("Could not remove wrapped preloader: %s", e)

    def _write_mtk_sparse_regions(
        self,
        mtk,
        part_name,
        raw_path,
        regions,
        offset,
        parttype,
        bytes_progress_cb=None,
        segment_bytes: int = 0,
    ) -> None:
        """Write only a sparse image's data extents (the 1.1.7 sparse path).

        Matches Chinese build 1.1.7 FlashWorker._write_partition_by_regions:
        iterates through all regions, segments large extents if segment_bytes is
        specified, and calls writeflash for each segment.
        """
        daloader = getattr(mtk, "daloader", None)
        if daloader is None or not hasattr(daloader, "writeflash"):
            raise RuntimeError("DA has no writeflash for sparse region writes")
        if not regions:
            self._log(f"  No sparse data extents to write for {part_name}")
            return
        total = len(regions)
        raw_size = os.path.getsize(raw_path)
        for index, (region_off, region_len) in enumerate(regions, 1):
            seg_start = 0
            while seg_start < region_len:
                if segment_bytes:
                    seg_len = min(region_len - seg_start, segment_bytes)
                else:
                    seg_len = region_len - seg_start
                file_off = region_off + seg_start
                dev_addr = offset + file_off
                self._log(
                    f"  Sparse region {index}/{total}: offset=0x{dev_addr:X} "
                    f"size={seg_len / 1048576:.2f}MB"
                )
                if file_off + seg_len > raw_size:
                    raise RuntimeError(
                        f"Sparse region {index}/{total} of {part_name} failed: "
                        f"the image holds {raw_size} bytes, the segment ends at "
                        f"{file_off + seg_len}"
                    )
                ok = daloader.writeflash(
                    addr=dev_addr,
                    length=seg_len,
                    filename=str(raw_path),
                    offset=file_off,
                    parttype=parttype,
                )
                if not ok:
                    error = getattr(getattr(mtk, "config", None), "last_error", None)
                    raise RuntimeError(
                        f"Sparse region {index}/{total} of {part_name} failed: "
                        f"{error or 'writeflash returned failure'}"
                    )
                seg_start += seg_len
                if bytes_progress_cb:
                    bytes_progress_cb(seg_len)
                self._touch_attempt()
        total_data = sum(int(seg_len) for _, seg_len in regions)
        skipped_mb = (raw_size - total_data) / 1048576
        data_mb = total_data / 1048576
        self._log(
            f"  Region write complete: data={data_mb:.1f}MB skipped_DONT_CARE={skipped_mb:.1f}MB regions={total}"
        )

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
        """Write one partition exactly the way the reference build does.

        Every plan step carries a scatter address, so this is one ``da_wo``
        per image (the same command ``mtk.py script`` issues). The named
        ``da_write`` below is kept only as a legacy fallback for callers
        without an address; it resolves the partition from the device table,
        which is empty on these MBR-partitioned players.
        Nothing is split into 50 MB segments and no image header is rewritten.
        """
        file_path = Path(file_path_str)
        file_size = file_path.stat().st_size if file_path.exists() else 0
        wlen = write_length if write_length is not None else max(0, file_size - file_offset)

        if offset is not None:
            segment_bytes = getattr(self, "_segmented_write_bytes", LEGACY_SEGMENTED_WRITE_BYTES) or LEGACY_SEGMENTED_WRITE_BYTES
            if segment_bytes and wlen > segment_bytes and part_type == "user":
                return self._write_partition_segmented(
                    mtk=mtk,
                    addr=offset,
                    file_path_str=file_path_str,
                    segment_bytes=segment_bytes,
                    parttype=part_type,
                    file_offset=file_offset,
                    write_length=wlen,
                    bytes_progress_cb=bytes_progress_cb,
                )
            daloader = getattr(mtk, "daloader", None)
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
                if bytes_progress_cb:
                    bytes_progress_cb(wlen)
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
                if bytes_progress_cb:
                    bytes_progress_cb(wlen)
                return

        # Named write: the device's own partition table supplies the address.
        if hasattr(da_handler, "da_write"):
            da_handler.da_write(
                parttype=part_type,
                filenames=[file_path_str],
                partitions=[part_name],
            )
            # The bundled CLI's ``w`` command treats da_write as a fire-and-
            # forget operation: failed writes are printed, but the method
            # returns None and the CLI still exits normally. Reject a named
            # write only when its own diagnostics report a real failure; a
            # successful write also clears last_error in the bundled legacy DA.
            error = getattr(getattr(mtk, "config", None), "last_error", None)
            if error:
                raise RuntimeError(f"Writing {part_name} failed: {error}")
            return

        offset_hex = hex(offset) if offset is not None else "0x0"

        class _Args:
            partitionname = part_name
            filename = file_path_str
            parttype = part_type
            offset = offset_hex
            length = hex(wlen)

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
            return
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
    """Create a pyusb libusb-1 backend with full cross-platform support.

    Uses mtk_api's unified cross-platform libusb resolution chain covering macOS
    (Universal 2 dylib), Windows (bundled dll), and Linux (AppImage & libusb-1.0.so.0).
    Returns (usb_core, backend) or (None, None).
    """
    try:
        import usb.core  # type: ignore
        from . import mtk_api

        backend = mtk_api.get_cross_platform_libusb_backend()
        if backend is not None:
            return usb.core, backend
    except Exception:
        pass

    try:
        import usb.backend.libusb1  # type: ignore
        import usb.core  # type: ignore

        backend = usb.backend.libusb1.get_backend()
        if backend is not None:
            return usb.core, backend
    except Exception:
        pass

    return None, None


def is_mtk_device_connected() -> bool:
    """Check if any MediaTek device is currently attached to USB."""
    try:
        usb_core, backend = _load_libusb_backend()
        if usb_core is None:
            return False
        devices = usb_core.find(find_all=True, backend=backend)
        for dev in devices:
            try:
                vid = dev.idVendor
                pid = dev.idProduct
                if vid in _MTK_FLASH_IDS and pid in _MTK_FLASH_IDS[vid]:
                    return True
                if vid in _MTK_VENDOR_IDS:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


_DISCONNECTION_PATTERNS = (
    "device disconnected",
    "device lost",
    "usb disconnected",
    "disconnection",
    "libusb_error_no_device",
    "libusb_error_io",
    "libusb_error_pipe",
    "no such device",
    "broken pipe",
    "connection reset",
    "connection aborted",
    "err_usb_lost",
    "status_device_disconnected",
    "device_disconnected",
)


def is_disconnection_error(error_code_or_msg: str = "", exc: BaseException | None = None) -> bool:
    """Detect whether a failure string or exception corresponds to an abrupt USB disconnect."""
    text = str(error_code_or_msg or "").lower()
    if exc is not None:
        text += f" {str(exc).lower()} {type(exc).__name__.lower()}"
    for pat in _DISCONNECTION_PATTERNS:
        if pat in text:
            return True
    return False


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
        self.primed_mtk = None
        self._primed_lock = threading.Lock()

    def take_primed_mtk(self):
        with self._primed_lock:
            mtk = self.primed_mtk
            self.primed_mtk = None
            return mtk

    def _close_primed_mtk(self):
        with self._primed_lock:
            mtk = self.primed_mtk
            self.primed_mtk = None
        if mtk is not None:
            try:
                mtk.port.close(reset=True)
            except Exception:
                pass

    def _try_prime_brom(self):
        """Immediately handshake with a newly detected device and disable watchdog.

        MediaTek Preloader on MT6572/MT6582 has a ~1-3s watchdog timer. Priming
        the session upon USB connection disables the watchdog timer, holding
        the device safely in flash mode until FlashWorker takes over.
        """
        try:
            from . import mtk_api
            from mtkclient.Library.Partitions.gpt import GptSettings
        except Exception as e:
            logger.warning("DeviceMonitor: mtkclient unavailable: %s", e)
            return None
        try:
            mtk = mtk_api.init(preloader=None, loader=None)
            if getattr(mtk.config, "gpt_settings", None) is None:
                mtk.config.gpt_settings = GptSettings(0, 0, 0)
        except Exception as e:
            logger.warning("DeviceMonitor: mtk_init failed: %s", e)
            return None
        try:
            ok = mtk.preloader.init(display=False)
        except Exception as e:
            logger.warning("DeviceMonitor: preloader.init exception: %s", e)
            ok = False
        if ok and getattr(mtk.config, "target_config", None) is not None:
            return mtk
        try:
            mtk.port.close(reset=True)
        except Exception:
            pass
        return None

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
                    primed = self._try_prime_brom()
                    if primed is not None:
                        with self._primed_lock:
                            self.primed_mtk = primed
                    self.device_found.emit(port_label)
                elif not present and self._present:
                    # Debounce: the device re-enumerates (BROM -> preloader ->
                    # DA) during flashing, so only report a loss after several
                    # consecutive misses (~4.5 s).
                    self._misses += 1
                    if self._misses >= 3:
                        self._present = False
                        self._misses = 0
                        self._close_primed_mtk()
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
    progress = Signal(object)
    action_changed = Signal(str)
    log_message = Signal(str)
    attempt_timeout = Signal(int)
    # Seconds left of the write, measured from this install's own write rate;
    # -1 while that rate is not yet known.
    eta_changed = Signal(int)
    flash_finished = Signal(bool, str)
    extract_finished = Signal(bool, str, str)  # ok, extract_dir, err
    extract_progress = Signal(int)
    device_found = Signal(str)
    device_lost = Signal()
    monitor_error = Signal(str)
    usb_disconnected = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._extract_worker = None
        self._flash_worker = None
        self._device_monitor = None
        self._retired_workers = set()
        self._retired_monitors = set()
        self._retired_extractors = set()

    def _retire_worker(self, worker):
        if worker is None:
            return
        self._retired_workers.add(worker)

        def _cleanup():
            if hasattr(worker, "isRunning") and worker.isRunning():
                QTimer.singleShot(500, _cleanup)
            else:
                self._retired_workers.discard(worker)

        try:
            worker.finished.connect(_cleanup)
        except Exception:
            _cleanup()

    def _retire_monitor(self, monitor):
        if monitor is None:
            return
        self._retired_monitors.add(monitor)

        def _cleanup():
            if hasattr(monitor, "isRunning") and monitor.isRunning():
                QTimer.singleShot(500, _cleanup)
            else:
                self._retired_monitors.discard(monitor)

        try:
            monitor.finished.connect(_cleanup)
        except Exception:
            _cleanup()

    def _retire_extractor(self, extractor):
        if extractor is None:
            return
        self._retired_extractors.add(extractor)

        def _cleanup():
            if hasattr(extractor, "isRunning") and extractor.isRunning():
                QTimer.singleShot(500, _cleanup)
            else:
                self._retired_extractors.discard(extractor)

        try:
            extractor.finished.connect(_cleanup)
        except Exception:
            _cleanup()

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
            worker = self._extract_worker
            self._retire_extractor(worker)
            worker.cancel()
            self._extract_worker = None

    def _on_extract_done(self, *args):
        if self._extract_worker is not None:
            self._retire_extractor(self._extract_worker)
            self._extract_worker = None

    def take_primed_mtk(self):
        """Retrieve the primed MTK session from device monitor, if any."""
        if self._device_monitor is not None and hasattr(self._device_monitor, "take_primed_mtk"):
            return self._device_monitor.take_primed_mtk()
        return None

    # -- flashing -----------------------------------------------------------
    def start_flash(self, package_path, pre_extracted_dir="", method="auto", model="",
                    da_file="", auth_file="", is_rockbox=False):
        # Kill any backend still running (e.g. a searching flash_tool) first,
        # then start the new one. The cancelled worker's late terminal signals
        # are detached so they cannot reach the UI or clobber the new run.
        retired = self.cancel_flash()
        # The previous window is over, so anything it left behind (including a
        # tool orphaned by a crash) goes now, and this run opens a fresh one:
        # the port has to be free before a new device search begins.
        from . import process_guard

        terminate_sp_sessions()
        process_guard.begin_install_session(reason="install")
        primed = self.take_primed_mtk()
        worker = FlashWorker(
            package_path,
            pre_extracted_dir,
            method,
            model=model,
            device_loss_hold=self.hold_device_lost,
            da_file=da_file,
            auth_file=auth_file,
            is_rockbox=is_rockbox,
            primed_mtk=primed,
        )
        worker._wait_for = retired
        self._flash_worker = worker
        worker.step_changed.connect(self.step_changed)
        worker.progress.connect(self.progress)
        worker.action_changed.connect(self.action_changed)
        worker.log_message.connect(self.log_message)
        worker.attempt_timeout.connect(self.attempt_timeout)
        worker.eta_changed.connect(self.eta_changed)
        worker.finished.connect(self.flash_finished)
        worker.finished.connect(lambda ok, err, w=worker: self._on_flash_done(w, ok, err))
        worker.usb_disconnected.connect(self.usb_disconnected)
        worker.start()

    def hold_device_lost(self, seconds):
        """See :meth:`DeviceMonitor.hold_lost` (passed to the flash worker)."""
        monitor = self._device_monitor
        if monitor is not None:
            monitor.hold_lost(seconds)

    def cancel_flash(self):
        """Stop the current backend without blocking the GUI thread.

        The next ``start_flash`` waits for this worker on its own thread, so
        a cancel or a backend switch cannot freeze the window for the old
        extract. Returns the retired worker, or None.
        """
        worker = self._flash_worker
        if worker is None:
            return None
        # Detach first: a run cancelled to switch backends must not emit
        # USER_CANCELLED into the UI or clear the reference of a newer worker.
        self._detach_worker(worker)
        self._retire_worker(worker)
        worker.cancel()
        self._flash_worker = None
        return worker

    @staticmethod
    def _detach_worker(worker):
        for sig in (worker.step_changed, worker.progress, worker.action_changed,
                    worker.log_message, worker.attempt_timeout, worker.eta_changed,
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
        self._retire_worker(worker)

    # -- device monitor -----------------------------------------------------
    def start_device_monitor(self):
        if self._device_monitor is not None and self._device_monitor.isRunning():
            return
        if self._device_monitor is not None:
            self._retire_monitor(self._device_monitor)
        self._device_monitor = DeviceMonitor()
        self._device_monitor.device_found.connect(self.device_found)
        self._device_monitor.device_lost.connect(self.device_lost)
        self._device_monitor.error.connect(self.monitor_error)
        self._device_monitor.start()

    def stop_device_monitor(self):
        monitor = self._device_monitor
        if monitor is not None:
            self._device_monitor = None
            try:
                monitor.device_found.disconnect()
            except Exception:
                pass
            try:
                monitor.device_lost.disconnect()
            except Exception:
                pass
            try:
                monitor.error.disconnect()
            except Exception:
                pass
            self._retire_monitor(monitor)
            monitor.stop()

    def terminate_install_sessions(self, timeout: float = 8.0) -> str:
        """Stop every session this app started, before another route takes over.

        The desktop tool opens the same USB port and reads the same history.ini
        the guided backends use, so the backends are stopped *and* waited for:
        ``cancel_flash`` only asks a worker to stop, and an MTKClient run
        unwinds on its own thread. Returns a short report for the log.
        """
        worker = self.cancel_flash()
        self.cancel_extract()
        self.stop_device_monitor()
        waited = None
        if worker is not None and hasattr(worker, "wait"):
            try:
                if worker.isRunning() if hasattr(worker, "isRunning") else True:
                    worker.wait(int(timeout * 1000))
                    waited = worker.isRunning() if hasattr(worker, "isRunning") else False
            except Exception:
                logger.debug("Waiting for the install worker failed", exc_info=True)
        killed = terminate_sp_sessions()
        # The hand-over (or shutdown) ends the install window, so every tool
        # process goes — including one this app did not start itself.
        from . import process_guard

        for item in process_guard.end_install_session(reason="session hand-over"):
            killed.append(item.get("pid"))
        if worker is None and not killed:
            return "No install session was running."
        if waited:
            return (
                "The previous install backend is still stopping; "
                f"stopped {len(killed)} leftover tool run(s)."
            )
        return f"Stopped the previous install session ({len(killed)} tool run(s))."

    def cleanup(self, sweep: bool = True):
        self.cancel_flash()
        self.cancel_extract()
        self.stop_device_monitor()
        from . import process_guard

        process_guard.end_install_session(reason="shutdown", sweep=sweep)
