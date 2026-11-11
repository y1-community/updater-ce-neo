"""Backend line and failure parsing shared by the terminal flash journey.

The window's flash worker knows an install failed from the tool's own output,
not from its exit code: the bundled MTKClient fork catches its DA errors and
still exits 0. This module gives the terminal path the same evidence without
importing Qt, so a text install cannot report "complete" after a fatal write.

``parse_flash_error`` mirrors ``flash_service.parse_flash_error`` in wording and
ordering. It accepts a translator callback so the TUI can use the same saved
language as the window; no English is invented for the terminal.
"""

from __future__ import annotations

import errno
import re
import sys
from pathlib import Path

from ..i18n import tr

# Tool-reported fatals. Notice/recoverable cases are exempt because the run
# continues after them; this is the same distinction the virtual window makes.
_FATAL_RE = re.compile(
    r"error on (sending|writing|reading|erasing)|\b"
    r"error\b|"
    r"error:\s|"
    r"failed to (write|send|erase|read)|"
    r"response:\s*0x0\b|"
    r"\bbrom error\b|"
    r"\bda error:",
    re.IGNORECASE,
)
_RECOVERABLE_RE = re.compile(
    r"(?:could not|failed to) read (?:the device's |the )?(?:gpt )?partition table|"
    r"falling back to per-partition reads|"
    r"no mbr/ebr table found|"
    r"notice:",
    re.IGNORECASE,
)
_LOGGING_NOISE = (
    "attributeerror",
    "streamhandler",
    "traceback (most recent call last)",
    "during handling of the above exception",
    "the above exception",
    "history.ini",
)
_SP_COMPLETE = re.compile(r"flash complete|all command exec|all command.*exec", re.IGNORECASE)
_WRONG_MODEL = re.compile(
    r"S_CHIP_TYPE_NOT_MATCH|DEVICE_PACKAGE_MISMATCH|SP_WRONG_MODEL|"
    r"SP_ERR_(?:3184|2005)|err_code\[(3184|2005)\]",
    re.IGNORECASE,
)
_CONNECT_FAIL = re.compile(r"S_COM_PORT_OPEN_FAIL|CONNECTION_FAILED|MTK_INIT_FAILED|CLI_START_FAILED|(?<!\d)1013(?!\d)", re.IGNORECASE)
_TIMEOUT = re.compile(r"S_TIMEOUT|(?<!\d)1042(?!\d)|TIMEOUT", re.IGNORECASE)
_DA_FAIL = re.compile(r"S_DA_INIT_SYNC_ERROR|S_BROM_DOWNLOAD_DA_FAIL|DA_SYNC|(?<!\d)(1093|2004)(?!\d)", re.IGNORECASE)
_DRAM_FAIL = re.compile(r"S_FT_ENABLE_DRAM_FAIL|DRAM_FAIL|(?<!\d)4032(?!\d)", re.IGNORECASE)
_WRITE_FAIL = re.compile(
    r"WRITE_FAILED|S_FT_DOWNLOAD_FAIL|WRITEFLASH|SEGMENTED WRITE FAILED|DA_WO|"
    r"(?<!\d)4008(?!\d)|STORAGE_READ",
    re.IGNORECASE,
)
_DISCONNECT = re.compile(
    r"device disconnected|device lost|usb disconnected|disconnection|"
    r"libusb_error_(?:no_device|io|pipe)|no such device|broken pipe|"
    r"connection reset|connection aborted|status_device_disconnected|"
    r"device_disconnected",
    re.IGNORECASE,
)
_STORAGE = re.compile(
    r"out_of_storage|enospc|no space left on device|not enough space|disk full|"
    r"storage full|there is not enough space on the disk|space left on device",
    re.IGNORECASE,
)
_MISSING = re.compile(r"NO_SCATTER_FILE|MISSING_IMAGES|NO_IMAGE_FILES", re.IGNORECASE)
_TOOL_MISSING = re.compile(r"SP_FLASH_TOOL_NOT_FOUND|MTK_IMPORT_FAILED", re.IGNORECASE)
_AUTH_UNUSABLE = re.compile(r"SP_AUTH_FILE_UNUSABLE", re.IGNORECASE)
_PMT = re.compile(r"(?<!\d)8502(?!\d)|PMT CHANGED FOR THE ROM", re.IGNORECASE)
_SP_NAME = re.compile(r"(S_[A-Z0-9_]+)")
_SP_CODE = re.compile(r"\((\d{3,5})\)")


def tool_body(line: str) -> str:
    """Drop the app's log tags before matching a tool's own text."""
    text = str(line or "").strip()
    for prefix in ("[LOG] ", "[SP] ", "[MTK] ", "[QT] ", "[TOOL] "):
        if text.startswith(prefix):
            text = text[len(prefix):].lstrip()
    return text


def is_logging_noise(line: str) -> bool:
    low = tool_body(line).lower()
    if not low:
        return True
    return any(token in low for token in _LOGGING_NOISE)


def is_tool_trace(line: str) -> bool:
    """SP Flash Tool's internal Qt trace is diagnostics, not progress."""
    return "FlashTool[" in tool_body(line)


def is_fatal_line(line: str) -> bool:
    """True when the backend reported a failure that must fail the install."""
    text = tool_body(line)
    if not text or is_logging_noise(text) or is_tool_trace(text):
        return False
    if _RECOVERABLE_RE.search(text):
        return False
    if _SP_COMPLETE.search(text) or "download ok" in text.lower():
        return False
    return bool(_FATAL_RE.search(text))


def first_fatal_line(lines) -> str:
    """The first actionable fatal line in a retained backend log."""
    for line in lines or ():
        if is_fatal_line(line):
            return tool_body(line)
    return ""


def is_sp_complete_line(line: str) -> bool:
    text = tool_body(line)
    if not text or "incomplete" in text.lower():
        return False
    return bool(_SP_COMPLETE.search(text))


def is_sp_search_line(line: str) -> bool:
    low = tool_body(line).lower()
    return (
        "search usb" in low
        or "searchusb" in low
        or "scanning usb" in low
    )


def is_wrong_model_error(value) -> bool:
    return bool(_WRONG_MODEL.search(str(value or "")))


def _token(value) -> str:
    text = str(value or "")
    name = _SP_NAME.search(text.upper())
    code = _SP_CODE.search(text)
    if name and code:
        return f"{name.group(1)} {code.group(1)}"
    return (name.group(1) if name else "") or (code.group(1) if code else "")


def _default_sp_supported() -> bool:
    if sys.platform.startswith("linux"):
        return (__import__("platform").machine() or "").lower() in ("x86_64", "amd64")
    if sys.platform == "win32":
        return (__import__("platform").machine() or "").lower() in ("x86_64", "amd64", "x86")
    return False


def parse_flash_error(value, model: str = "", *, translate=None, method_label=None, sp_supported=None):
    """Return ``(title, explanation)`` for a backend failure.

    The branch order and conditions match ``flash_service.parse_flash_error``.
    ``translate`` lets the TUI use its saved language while still sharing one
    parser; ``method_label`` and ``sp_supported`` keep the checksum guidance
    identical to the window.
    """
    t = translate or tr
    raw = str(value or "").strip()
    method_label = method_label or (lambda name: {"mtk": "MTKClient", "sp": "SP Flash Tool"}.get(name, name))
    sp_supported = sp_supported or _default_sp_supported

    if not raw:
        return t("install_failed_title"), t("err_generic_flash_failed_desc").format(detail="")
    if raw == "CACHE_FILE_MISSING":
        return t("install_failed_title"), t("err_cache_file_missing_desc")
    if _PMT.search(raw):
        return t("install_failed_title"), t("err_pmt_format_required_desc")
    if _STORAGE.search(raw) or _storage_full_now(raw):
        return t("err_storage_full_title"), t("err_storage_full_desc")
    if _DISCONNECT.search(raw) or "USB_DISCONNECTED" in raw.upper() or "DEVICE_DISCONNECTED" in raw.upper():
        return (
            t("err_usb_lost_title"),
            t("err_usb_disconnected_recovery").format(model=model or t("device_default_name")),
        )
    if is_wrong_model_error(raw):
        token = _token(raw) or "MISMATCH"
        return f"{t('err_sp_wrong_model_title')} ({token})", t("err_sp_wrong_model")
    if re.search(r"SP_EXIT_-?\d+", raw, re.IGNORECASE):
        token = re.search(r"SP_EXIT_-?\d+", raw, re.IGNORECASE).group(0)
        return f"{t('err_driver_missing_title')} ({token})", t("err_driver_missing_desc")
    if raw.upper() == "ERR_ATTEMPT_TIMEOUT" or _TIMEOUT.search(raw):
        token = _token(raw) or "TIMEOUT"
        return f"{t('err_attempt_timeout_title')} ({token})", t("err_sp_timeout")
    if re.search(r"S_BROM_CMD_(?:STARTCMD|START|JUMP_DA)_FAIL", raw, re.IGNORECASE):
        token = _token(raw) or "2005"
        return f"{t('err_connection_failed_title')} ({token})", t("err_sp_brom_startcmd")
    if _CONNECT_FAIL.search(raw):
        token = _token(raw) or "1013"
        return f"{t('err_connection_failed_title')} ({token})", t("err_com_port_reboot_suggestion")
    if raw.upper() == "MTK_CHECKSUM_STALLED" or ("CHECKSUM" in raw.upper() and "STALL" in raw.upper()):
        if not sp_supported():
            return t("err_checksum_stall_title"), t("err_checksum_stall_no_sp_desc")
        return (
            t("err_checksum_stall_title"),
            t("err_checksum_stall_sp_failover").format(
                failed=method_label("mtk"), next=method_label("sp"), model=model or "player"
            ),
        )
    if any(k in raw.upper() for k in ("CONNECTION_FAILED", "MTK_INIT_FAILED", "CLI_START_FAILED", "HANDSHAKE")):
        token = _token(raw) or raw.split()[0]
        return f"{t('err_connection_failed_title')} ({token})", t("err_connection_failed_desc")
    if _DA_FAIL.search(raw):
        return f"{t('install_failed_title')} ({_token(raw) or 'DA_ERROR'})", t("err_sp_da_init_sync")
    if _DRAM_FAIL.search(raw):
        return f"{t('install_failed_title')} ({_token(raw) or '4032'})", t("err_sp_enable_dram")
    if _WRITE_FAIL.search(raw):
        return f"{t('err_flash_write_failed_title')} ({_token(raw) or 'WRITE_FAIL'})", t("err_flash_write_failed_desc")
    if _MISSING.search(raw):
        detail = raw.split(":", 1)[1].strip() if ":" in raw else ""
        desc = t("err_missing_package_files_desc")
        return t("err_missing_package_files_title"), (desc + ("\n\n" + detail if detail else ""))
    if _TOOL_MISSING.search(raw):
        return t("err_tool_unavailable_title"), t("err_tool_unavailable_desc")
    if _AUTH_UNUSABLE.search(raw):
        return t("err_auth_file_unusable_title"), t("err_auth_file_unusable_desc")
    detail = raw if len(raw) <= 40 else raw[:37] + "..."
    return (
        f"{t('install_failed_title')} ({detail})" if detail else t("install_failed_title"),
        t("err_generic_flash_failed_desc").format(detail=raw),
    )


def _storage_full_now(raw: str) -> bool:
    if not any(k in raw.lower() for k in ("write", "i/o", "ioerror", "oserror", "download failed", "prepare_failed")):
        return False
    try:
        import shutil

        from ..downloads import downloads_dir

        target = downloads_dir()
        return target.exists() and shutil.disk_usage(target).free < 50 * 1024 * 1024
    except Exception:
        return False


def package_model_from_extract(folder: Path, package_name: str = "", requested: str = "") -> str:
    """The model the window would use for this package.

    Same precedence as ``FlashWorker._do_flash``: explicit selection, then the
    manufacturer/package name, then the scatter header, then the platform.
    This is deliberately Qt-free; it reads the same catalogue helpers.
    """
    try:
        from .. import catalog
        from ..config import detect_model_and_type_from_name

        path = str(package_name or folder)
        info = catalog.parse_manufacturer_rom_info(path)
        detected = (info or {}).get("model") or ""
        if not detected:
            detected, _variant = detect_model_and_type_from_name(path)
        try:
            from ..download import find_scatter

            scatter = find_scatter(Path(folder))
        except Exception:
            scatter = None
        platform = ""
        if scatter:
            platform, project = catalog.parse_scatter_header(scatter)
            if not detected:
                detected = catalog.infer_model_from_scatter(platform, project)
        if not detected:
            low = path.lower()
            if "a5" in low:
                detected = "A5"
            elif "6582" in platform:
                detected = "Y2"
            elif "6572" in platform:
                detected = "Y1"
        return (requested or detected or "").strip().upper()
    except Exception:
        return (requested or "").strip().upper()
