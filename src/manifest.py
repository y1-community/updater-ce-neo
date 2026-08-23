"""Live firmware catalogue manifest (``slidia_manifest.xml``).

Updater CE consumes ``https://innioasis.app/slidia_manifest.xml`` at runtime as
its authoritative catalogue; the static table in ``catalog.py`` is only the
offline first-run fallback. This module fetches, parses, and caches that live
manifest (24 h TTL, per-user) and hands the resulting ``FirmwarePackage`` list
to ``catalog.set_live_catalog``.

Manifest format (attributes on ``<package/>`` elements):

    <package name="Original Software" repo="y1-community/y1-stock-rom"
             device="Y1" url="..." type="img" handler="Custom Firmware" />

Only ``type="img"`` / ``handler="Custom Firmware"`` packages for Y1/Y2 are
turned into firmware entries; the app-update placeholder package is skipped
(our own update checker handles app updates).
"""

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

from .catalog import FirmwarePackage
from .config import (
    CATALOG_CACHE_DIR_NAME,
    CATALOG_FALLBACK_URL,
    CATALOG_MANIFEST_URL,
    RELEASE_CACHE_TTL_SECONDS,
    resolve_firmware_repo,
)

logger = logging.getLogger(__name__)

MANIFEST_CACHE_TTL_SECONDS = RELEASE_CACHE_TTL_SECONDS  # 24 h
SKIPPED_UPDATE_REPO = "Please-Click/Updates-Available"
REQUEST_TIMEOUT = 12

_CACHE_DIR_NAME = CATALOG_CACHE_DIR_NAME  # "catalog"


def _cache_root() -> Path:
    import os
    import sys

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    d = base / "innioasis-updater" / ".cache" / _CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_file() -> Path:
    return _cache_root() / "slidia_manifest.xml"


def _slug_for(name: str, device: str) -> str:
    import re

    base = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return f"{base}-{device.lower()}" if base else f"pkg-{device.lower()}"


def parse_manifest_xml(xml_text: str):
    """Parse ``<package/>`` entries into ``FirmwarePackage`` objects.

    Returns a list; invalid/irrelevant entries are skipped (never raises).
    """
    entries = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.error("slidia_manifest.xml parse failed: %s", e)
        return entries
    for node in root.iter("package"):
        try:
            name = (node.get("name") or "").strip()
            repo = (node.get("repo") or "").strip()
            device = (node.get("device") or "").strip().upper()
            handler = (node.get("handler") or "").strip()
            ptype = (node.get("type") or "").strip()
        except Exception:
            continue
        if not name or not repo or device not in ("Y1", "Y2"):
            continue
        if repo.lower() == SKIPPED_UPDATE_REPO.lower():
            continue  # CE's in-app "please update" placeholder
        if ptype and ptype.lower() not in ("img", "firmware"):
            continue
        if handler and "firmware" not in handler.lower():
            continue
        entries.append(
            FirmwarePackage(
                slug=_slug_for(name, device),
                name=name,
                model=device,
                repo=resolve_firmware_repo(repo),
                package_name="rom_y2.zip" if device == "Y2" else "rom.zip",
                description="",
            )
        )
    return entries


def _fetch_xml(url: str) -> str:
    resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def fetch_manifest():
    """Fetch + parse the live manifest (primary URL, then fallback).

    Returns a list of ``FirmwarePackage`` or ``None`` when both sources fail.
    """
    for url in (CATALOG_MANIFEST_URL, CATALOG_FALLBACK_URL):
        try:
            text = _fetch_xml(url)
            entries = parse_manifest_xml(text)
            if entries:
                logger.info("Live catalog: %d packages from %s", len(entries), url)
                return entries
        except Exception as e:
            logger.debug("Manifest fetch failed for %s: %s", url, e)
    return None


def cache_manifest(entries):
    """Persist the parsed manifest so offline launches reuse it."""
    try:
        import json

        data = [
            {"name": e.name, "repo": e.repo, "device": e.model,
             "package_name": e.package_name, "slug": e.slug}
            for e in entries
        ]
        _cache_root().joinpath("packages.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        logger.debug("Manifest cache write failed: %s", e)


def load_cached_manifest():
    """Restore the last parsed manifest from disk (None when stale/missing)."""
    try:
        import json

        path = _cache_root() / "packages.json"
        if not path.exists() or (time.time() - path.stat().st_mtime) > MANIFEST_CACHE_TTL_SECONDS:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return [
            FirmwarePackage(
                slug=e.get("slug", ""), name=e.get("name", ""),
                model=e.get("device", ""), repo=e.get("repo", ""),
                package_name=e.get("package_name", "rom.zip"),
            )
            for e in data
            if e.get("name") and e.get("repo")
        ]
    except Exception as e:
        logger.debug("Manifest cache read failed: %s", e)
        return None


def refresh_catalog():
    """Best-effort live manifest refresh: cache-first, then network.

    Installs entries into ``catalog.LIVE_CATALOG`` (static table remains the
    fallback when this yields nothing). Returns the entry list (possibly []).
    """
    from . import catalog

    entries = load_cached_manifest()
    if not entries:
        entries = fetch_manifest()
        if entries:
            cache_manifest(entries)
    catalog.set_live_catalog(entries or [])
    return entries or []


class ManifestWorker(QThread):
    """Fetches the live catalog off the UI thread (cache-first)."""

    finished = Signal(list)  # entries

    def run(self):
        try:
            self.finished.emit(refresh_catalog())
        except Exception as e:
            logger.exception("Manifest refresh failed")
            self.finished.emit([])
