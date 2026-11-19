"""Remote rescue and debug flags evaluation.

Enables silent runtime overrides and feature gating via lightweight remote text files
hosted across innioasis.app and the community/maintainer repositories.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
import threading
from typing import Optional
import xml.etree.ElementTree as ET

from PySide6.QtCore import QLocale, QThread, Signal

from .network import requests_module

logger = logging.getLogger(__name__)

FLAG_SOURCES = (
    # Repository copies first: GitHub serves a committed flag file immediately,
    # while the Pages site behind innioasis.app can trail it by a CDN interval.
    "https://raw.githubusercontent.com/y1-community/innioasis-updater/refs/heads/main/",
    "https://innioasis.app/",
    "https://raw.githubusercontent.com/y1-community/updater-ce-neo/refs/heads/main/",
    "https://raw.githubusercontent.com/ryan-specter/innioasis-updater/refs/heads/main/",
    "https://raw.githubusercontent.com/ryan-specter/updater-ce-neo/refs/heads/main/",
)

_CACHE_LOCK = threading.Lock()
_ACTIVE_FLAGS: dict[str, bool] = {}
_RFLAG_PRESENT: Optional[bool] = None
_SITEMAP_URLS: set[str] = set()
_LAST_CHECK_TIME = 0.0
_CACHE_TTL = 300.0


def _system_platform_tokens() -> list[str]:
    tokens = []
    if sys.platform == "darwin":
        tokens.extend(["macos", "darwin", "osx"])
    elif sys.platform.startswith("win"):
        tokens.extend(["windows", "win", "win32"])
    elif sys.platform.startswith("linux"):
        tokens.extend(["linux"])
    return tokens


def _system_arch_tokens() -> list[str]:
    m = (platform.machine() or "").lower()
    if m in ("arm64", "aarch64"):
        return ["arm64", "aarch64"]
    if m in ("x86_64", "amd64", "x64"):
        return ["x86_64", "amd64", "x64"]
    if m in ("i386", "i686", "x86"):
        return ["x86", "i386"]
    return [m] if m else []


def _system_locale_tokens() -> list[str]:
    tokens: list[str] = []
    try:
        from .i18n import translator

        app_lang = (translator().language or "").lower()
        if app_lang:
            tokens.append(app_lang)
            if "-" in app_lang:
                tokens.append(app_lang.replace("-", "_"))
                parts = app_lang.split("-")
                tokens.extend(parts)
            elif "_" in app_lang:
                tokens.append(app_lang.replace("_", "-"))
                parts = app_lang.split("_")
                tokens.extend(parts)
    except Exception:
        pass

    try:
        loc = QLocale.system()
        bcp47 = (loc.bcp47Name() or "").lower()
        if bcp47 and bcp47 not in tokens:
            tokens.append(bcp47)
            if "-" in bcp47:
                tokens.append(bcp47.replace("-", "_"))
                tokens.extend(bcp47.split("-"))
        territory_func = getattr(loc, "territory", None) or getattr(loc, "country", None)
        if territory_func is not None:
            c_code = (territory_func().name or "").lower()
            if c_code and c_code not in tokens:
                tokens.append(c_code)
    except Exception:
        pass

    return list(dict.fromkeys(tokens))


def candidate_flag_filenames(base_name: str) -> list[str]:
    """Generate candidate text filenames for a flag with platform, arch, and region suffixes."""
    stem = base_name.removesuffix(".txt")
    candidates: list[str] = []

    platforms = _system_platform_tokens()
    arches = _system_arch_tokens()
    regions = _system_locale_tokens()

    for p in platforms:
        for a in arches:
            for r in regions:
                candidates.append(f"{stem}-{p}-{a}-{r}.txt")
                candidates.append(f"{stem}_{p}_{a}_{r}.txt")

    for p in platforms:
        for a in arches:
            candidates.append(f"{stem}-{p}-{a}.txt")
            candidates.append(f"{stem}_{p}_{a}.txt")

    for p in platforms:
        for r in regions:
            candidates.append(f"{stem}-{p}-{r}.txt")
            candidates.append(f"{stem}_{p}_{r}.txt")

    for p in platforms:
        candidates.append(f"{stem}-{p}.txt")
        candidates.append(f"{stem}_{p}.txt")

    for a in arches:
        candidates.append(f"{stem}-{a}.txt")
        candidates.append(f"{stem}_{a}.txt")

    for r in regions:
        candidates.append(f"{stem}-{r}.txt")
        candidates.append(f"{stem}_{r}.txt")

    candidates.append(f"{stem}.txt")

    return list(dict.fromkeys(candidates))


def _probe_remote_file(filename: str) -> bool:
    """Test whether any source serves filename with HTTP 200."""
    if not os.environ.get("UPDATER_ALLOW_REMOTE_FLAGS") == "1":
        if (
            os.environ.get("INNIOASIS_HEADLESS") == "1"
            or os.environ.get("UPDATER_OFFLINE") == "1"
            or "PYTEST_CURRENT_TEST" in os.environ
            or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
        ):
            return False

    with _CACHE_LOCK:
        if _SITEMAP_URLS:
            for u in _SITEMAP_URLS:
                if u.endswith("/" + filename) or u == filename:
                    return True

    req = requests_module()
    for base_url in FLAG_SOURCES:
        url = base_url.rstrip("/") + "/" + filename
        try:
            resp = req.get(url, timeout=0.5, allow_redirects=True)
            if resp.status_code == 200 and resp.text.strip() != "404: Not Found":
                return True
        except Exception:
            continue
    return False


def _check_rflag_gate() -> bool:
    """Master gate check: remote flags are only checked if rflag.txt exists."""
    global _RFLAG_PRESENT
    with _CACHE_LOCK:
        if _RFLAG_PRESENT is not None:
            return _RFLAG_PRESENT

    # In test environments without network or explicit mocks, evaluate probe
    if "PYTEST_CURRENT_TEST" in os.environ and os.environ.get("UPDATER_ALLOW_REMOTE_FLAGS") != "1":
        return True

    # Try sitemap first for fast batch discovery
    _load_sitemap_urls()

    present = _probe_remote_file("rflag.txt")
    with _CACHE_LOCK:
        _RFLAG_PRESENT = present
    return present


def _load_sitemap_urls() -> None:
    """Fast passive sitemap check to speed up file discovery."""
    global _SITEMAP_URLS
    if (
        os.environ.get("INNIOASIS_HEADLESS") == "1"
        or os.environ.get("UPDATER_OFFLINE") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    ):
        return

    req = requests_module()
    try:
        resp = req.get("https://innioasis.app/sitemap.xml", timeout=0.6)
        if resp.status_code == 200 and resp.text:
            root = ET.fromstring(resp.text)
            urls = set()
            for loc in root.iter():
                if loc.tag.endswith("loc") and loc.text:
                    urls.add(loc.text.strip())
            with _CACHE_LOCK:
                _SITEMAP_URLS = urls
    except Exception:
        pass


def is_remote_flag_active(base_name: str) -> bool:
    """Non-blocking query for flag status from in-memory cache."""
    clean_base = base_name.removesuffix(".txt")
    with _CACHE_LOCK:
        if clean_base in _ACTIVE_FLAGS:
            return _ACTIVE_FLAGS[clean_base]

    # In unit tests with mock probes, resolve synchronously
    if (
        os.environ.get("UPDATER_ALLOW_REMOTE_FLAGS") == "1"
        or "PYTEST_CURRENT_TEST" in os.environ
    ):
        filenames = candidate_flag_filenames(clean_base)
        active = any(_probe_remote_file(fn) for fn in filenames)
        with _CACHE_LOCK:
            _ACTIVE_FLAGS[clean_base] = active
        return active

    return False


def poll_known_remote_flags() -> dict[str, bool]:
    """Background poll for the standard suite of remote debug / rescue flags."""
    if not _check_rflag_gate():
        return {}

    known_bases = (
        "null_rbtf",
        "null-rbtf",
        "mtkclient-win-x64",
        "no-term-install",
        "null-translate",
        "reset_all",
        "notices_off",
        "no_donations",
        "donations_off",
        "rockbox_themes_off",
        "disable_device_reminders",
        "force_sp_flash",
        "force_mtkclient",
        "no_firmware_cache",
        "no_preflight_checks",
        "disable_app_updates",
        "background_off",
        "background_on",
        "latest_update_required",
        "update_required",
    )
    results = {}
    for base in known_bases:
        filenames = candidate_flag_filenames(base)
        active = any(_probe_remote_file(fn) for fn in filenames)
        results[base] = active
        with _CACHE_LOCK:
            _ACTIVE_FLAGS[base] = active

    return results


class RemoteFlagsWorker(QThread):
    """Silent background worker to poll remote flags."""

    flags_updated = Signal(dict)

    def run(self):
        try:
            results = poll_known_remote_flags()
            self.flags_updated.emit(results)
        except Exception:
            self.flags_updated.emit({})


def clear_remote_flags_cache() -> None:
    global _RFLAG_PRESENT
    with _CACHE_LOCK:
        _ACTIVE_FLAGS.clear()
        _RFLAG_PRESENT = None
        _SITEMAP_URLS.clear()
