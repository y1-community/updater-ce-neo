"""Live firmware catalogue manifest (``slidia_manifest.xml``).

Updater CE consumes ``https://innioasis.app/slidia_manifest.xml`` at runtime as
its authoritative catalogue; the static table in ``catalog.py`` is only the
offline first-run fallback. This module fetches, parses, and caches that live
manifest (24 h TTL, per-user) and hands the resulting ``FirmwarePackage`` list
to ``catalog.set_live_catalog``.

Manifest format (attributes on ``<package/>`` elements):

    <package name="Original Software" repo="y1-community/y1-stock-rom"
             device="Y1" url="..." type="img" handler="Custom Firmware" />

``brand`` (optional) tags a package with the brand it belongs to and wins
over the device registry. Entries without it take the registry's brand for
their model; an unknown model counts as Innioasis (see
``catalog.package_brand``).

``portable_zips`` (or the legacy ``portable_zip``) names one or more
Portable Flashing Zip Packager mirrors for the release, comma separated. The
catalogue then downloads the portable package a mirror built for the current
platform instead of the plain firmware zip (see ``portable.py``).

Only ``type="img"`` / ``handler="Custom Firmware"`` packages for Y1/Y2 are
turned into firmware entries; the app-update placeholder package is skipped
(our own update checker handles app updates).
"""

import logging
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .network import requests_module

from .catalog import FirmwarePackage
from .portable import parse_mirror_repos
from .config import (
    CATALOG_CACHE_DIR_NAME,
    CATALOG_MANIFEST_URLS,
    resolve_firmware_repo,
)

logger = logging.getLogger(__name__)

MANIFEST_CACHE_TTL_SECONDS = 5 * 60  # 5 min, same cadence as online_data
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


_START_TAG_RE = re.compile(r"<([A-Za-z_][\w:.-]*)([^<>]*?)(/?)>", re.DOTALL)
_ATTRIBUTE_RE = re.compile(r"""([A-Za-z_][\w:.-]*)\s*=\s*("[^"]*"|'[^']*')""", re.DOTALL)


def _dedupe_duplicate_attributes(xml_text: str) -> str:
    """Drop repeated attributes from start tags, keeping the first value.

    ``slidia_manifest.xml`` has shipped with ``derivative`` written twice on a
    package. XML rejects that outright, which used to discard the whole live
    catalogue and its Keep Data family information. The repaired copy is only
    used after the normal parse fails.
    """

    def repair_tag(match) -> str:
        tag, body, close = match.group(1), match.group(2) or "", match.group(3) or ""
        seen: set[str] = set()

        def keep_attribute(attribute) -> str:
            name = attribute.group(1).casefold()
            if name in seen:
                return ""
            seen.add(name)
            return attribute.group(0)

        body = _ATTRIBUTE_RE.sub(keep_attribute, body)
        return f"<{tag}{body}{close}>"

    return _START_TAG_RE.sub(repair_tag, xml_text)


def parse_manifest_xml(xml_text: str):
    """Parse ``<package/>`` entries into ``FirmwarePackage`` objects.

    Returns a list; invalid/irrelevant entries are skipped (never raises).
    A manifest with repeated attributes is retried with the duplicate values
    removed, so one typo cannot hide the whole catalogue.
    """
    entries = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        repaired = _dedupe_duplicate_attributes(xml_text)
        if repaired == xml_text:
            logger.error("slidia_manifest.xml parse failed: %s", e)
            return entries
        try:
            root = ET.fromstring(repaired)
            logger.warning(
                "slidia_manifest.xml had duplicate attributes; parsed repaired copy: %s",
                e,
            )
        except ET.ParseError as repaired_error:
            logger.error("slidia_manifest.xml parse failed: %s", repaired_error)
            return entries
    for node in root.iter():
        local_name = node.tag.split("}", 1)[-1].split(":", 1)[-1] if node.tag else ""
        if local_name != "package":
            continue
        entries.extend(_packages_from_mapping(node.attrib, description=(node.text or "").strip()))
    return entries


def _packages_from_mapping(mapping: dict, description: str = "") -> list[FirmwarePackage]:
    """Convert a single manifest ``<package/>`` attribute dict or JSON feed
    package object into one or more ``FirmwarePackage`` entries."""
    out: list[FirmwarePackage] = []
    try:
        name = str(mapping.get("name") or "").strip()
        repo = str(mapping.get("repo") or "").strip()
        raw_device = str(mapping.get("device") or mapping.get("model") or "").strip()
        handler = str(mapping.get("handler") or "").strip()
        ptype = str(mapping.get("type") or "").strip()
        brand = str(mapping.get("brand") or "").strip()
        icon = str(mapping.get("icon") or "").strip()
        image = str(mapping.get("image") or "").strip()
        icon_dark = str(mapping.get("icon_dark") or mapping.get("image_dark") or "").strip()
        min_version = str(mapping.get("min_version") or mapping.get("min_app_version") or "").strip()
        raw_pkg_name = str(mapping.get("package_name") or mapping.get("package") or "").strip()
        post_install = str(mapping.get("post_install") or "").strip()
        post_install_2 = str(
            mapping.get("post_install2") or mapping.get("post_install_2") or ""
        ).strip()
        portable_zips = ", ".join(
            parse_mirror_repos(
                str(mapping.get("portable_zips") or mapping.get("portable_zip") or "")
            )
        )
        raw_deriv = mapping.get("derivative")
        if isinstance(raw_deriv, bool):
            derivative = ""
        else:
            derivative = str(raw_deriv or "").strip()
            if derivative.lower() in ("false", "0", "none"):
                derivative = ""
        info_repo = str(mapping.get("info_repo") or "").strip()
        apk_repo = str(mapping.get("apk_repo") or "").strip()
        install_first = str(mapping.get("install_first") or "").strip()
        install_first_version = str(
            mapping.get("install_first_version") or ""
        ).strip()
        raw_dnkd = mapping.get("do_not_keep_data")
        if raw_dnkd is None:
            raw_dnkd = mapping.get("no_keep_data")
        if isinstance(raw_dnkd, bool):
            do_not_keep_data = raw_dnkd
        else:
            do_not_keep_data = str(raw_dnkd or "").strip().lower() in ("true", "1", "yes")
        raw_pmt = mapping.get("PMT_format_needed", mapping.get("pmt_format_needed"))
        if isinstance(raw_pmt, bool):
            pmt_format_needed = raw_pmt
        else:
            pmt_format_needed = str(raw_pmt or "").strip().lower() in ("true", "1", "yes")
        desc = str(mapping.get("description") or description or "").strip()
        type_a_asset = str(mapping.get("type_a_asset") or "").strip()
        type_b_asset = str(mapping.get("type_b_asset") or "").strip()
        asset_pattern = str(mapping.get("asset_pattern") or "").strip()
        raw_slug = str(mapping.get("slug") or "").strip()
    except Exception:
        return out

    if not name or not repo or not raw_device:
        return out
    if repo.lower() == SKIPPED_UPDATE_REPO.lower():
        return out
    if ptype and ptype.lower() not in ("img", "firmware", "rom"):
        return out
    if handler and "firmware" not in handler.lower():
        return out
    if min_version:
        from .config import APP_VERSION
        from .updates import parse_version
        cur_v = parse_version(APP_VERSION)
        req_v = parse_version(min_version)
        if cur_v and req_v and cur_v < req_v:
            return out

    devices = [d.strip().upper() for d in raw_device.split(",") if d.strip()]
    for device in devices:
        pkg_name = raw_pkg_name
        if not pkg_name:
            pkg_name = "rom.zip" if device == "Y1" else f"rom_{device.lower()}.zip"
        slug = raw_slug if (raw_slug and len(devices) == 1) else _slug_for(name, device)
        out.append(
            FirmwarePackage(
                slug=slug,
                name=name,
                model=device,
                repo=resolve_firmware_repo(repo),
                package_name=pkg_name,
                description=desc,
                icon=icon,
                icon_dark=icon_dark,
                image=image,
                post_install=post_install,
                post_install_2=post_install_2,
                derivative=resolve_firmware_repo(derivative) if derivative else "",
                info_repo=resolve_firmware_repo(info_repo) if info_repo else "",
                apk_repo=apk_repo,
                do_not_keep_data=do_not_keep_data,
                pmt_format_needed=pmt_format_needed,
                type_a_asset=type_a_asset,
                type_b_asset=type_b_asset,
                asset_pattern=asset_pattern,
                portable_zips=portable_zips,
                brand=brand,
                install_first=(
                    resolve_firmware_repo(install_first) if install_first else ""
                ),
                install_first_version=install_first_version,
            )
        )
    return out


def parse_manifest_packages(packages_data: list) -> list[FirmwarePackage]:
    """Parse package dicts from ``firmware-releases.json`` / ``firmware-feed.xml``."""
    entries: list[FirmwarePackage] = []
    for item in packages_data or []:
        if isinstance(item, dict):
            entries.extend(_packages_from_mapping(item))
    return entries


def catalogue_repos() -> list[str]:
    """Firmware repositories the catalogue lists, for a reachability check.

    ``slidia_manifest.xml`` is authoritative; the static table in ``catalog.py``
    covers a first run that has never fetched it.
    """
    repos: list[str] = []
    for entry in (load_cached_manifest() or []):
        repo = (getattr(entry, "repo", "") or "").strip()
        if repo and repo not in repos:
            repos.append(repo)
    if not repos:
        try:
            from .catalog import _catalog_packages

            for entry in _catalog_packages():
                repo = (getattr(entry, "repo", "") or "").strip()
                if repo and repo not in repos:
                    repos.append(repo)
        except Exception:
            pass
    return repos


def _fetch_xml(url: str) -> str:
    resp = requests_module().get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def fetch_manifest():
    """Fetch + parse the live manifest (primary URL, then fallback, then
    ``innioasis.app`` static feeds).

    Returns a list of ``FirmwarePackage`` or ``None`` when all sources fail.
    """
    for url in CATALOG_MANIFEST_URLS:
        try:
            text = _fetch_xml(url)
            entries = parse_manifest_xml(text)
            if entries:
                logger.info("Live catalog: %d packages from %s", len(entries), url)
                return entries
        except Exception as e:
            logger.debug("Manifest fetch failed for %s: %s", url, e)

    try:
        from . import feed_fallback

        feed_pkgs = feed_fallback.get_packages_from_feed(force_refresh=True)
        entries = parse_manifest_packages(feed_pkgs)
        if entries:
            logger.info("Live catalog: %d packages from innioasis.app fallback feed", len(entries))
            return entries
    except Exception as e:
        logger.debug("Fallback feed manifest lookup failed: %s", e)

    return None


def cache_manifest(entries):
    """Persist the parsed manifest so offline launches reuse it."""
    try:
        import json

        data = [
            {"name": e.name, "repo": e.repo, "device": e.model,
             "package_name": e.package_name, "slug": e.slug,
             "portable_zips": getattr(e, "portable_zips", "") or "",
             "asset_pattern": getattr(e, "asset_pattern", "") or "",
             "type_a_asset": getattr(e, "type_a_asset", "") or "",
             "type_b_asset": getattr(e, "type_b_asset", "") or "",
             "icon": getattr(e, "icon", "") or "",
             "icon_dark": getattr(e, "icon_dark", "") or "",
             "image": getattr(e, "image", "") or "",
             "post_install": getattr(e, "post_install", "") or "",
             "post_install_2": getattr(e, "post_install_2", "") or "",
             "derivative": getattr(e, "derivative", "") or "",
             "info_repo": getattr(e, "info_repo", "") or "",
             "apk_repo": getattr(e, "apk_repo", "") or "",
             "brand": getattr(e, "brand", "") or "",
             "install_first": getattr(e, "install_first", "") or "",
             "install_first_version": getattr(e, "install_first_version", "") or "",
             "do_not_keep_data": bool(getattr(e, "do_not_keep_data", False)),
             "PMT_format_needed": bool(getattr(e, "pmt_format_needed", False))}
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
                portable_zips=e.get("portable_zips", "") or "",
                asset_pattern=e.get("asset_pattern", "") or "",
                type_a_asset=e.get("type_a_asset", "") or "",
                type_b_asset=e.get("type_b_asset", "") or "",
                icon=e.get("icon", "") or "",
                icon_dark=e.get("icon_dark", "") or "",
                image=e.get("image", "") or "",
                post_install=e.get("post_install", "") or "",
                post_install_2=e.get("post_install_2", "") or "",
                derivative=e.get("derivative", "") or "",
                info_repo=e.get("info_repo", "") or "",
                apk_repo=e.get("apk_repo", "") or "",
                brand=e.get("brand", "") or "",
                install_first=e.get("install_first", "") or "",
                install_first_version=e.get("install_first_version", "") or "",
                do_not_keep_data=bool(e.get("do_not_keep_data", False)),
                pmt_format_needed=bool(
                    e.get("PMT_format_needed", e.get("pmt_format_needed", False))
                ),
            )
            for e in data
            if e.get("name") and e.get("repo")
        ]
    except Exception as e:
        logger.debug("Manifest cache read failed: %s", e)
        return None


def load_snapshot_manifest():
    """Parse the manifest snapshot bundled with the app (or last downloaded).

    Used only when neither the parsed cache nor the network produced entries:
    a fresh install with no connection still lists firmware from the copy
    baked in at packaging time (``assets/online/slidia_manifest.xml``).
    """
    try:
        from . import online_data

        text = online_data.read_text("slidia_manifest.xml")
    except Exception as e:
        logger.debug("Manifest snapshot unreadable: %s", e)
        return None
    if not text.strip():
        return None
    entries = parse_manifest_xml(text)
    return entries or None


def refresh_catalog(force_refresh=False, check_releases=True, allow_release_network=False):
    """Best-effort live manifest refresh: network-first, cache when offline.

    The live slidia_manifest.xml is always preferred. The cached copy is used
    only when the fetch fails, then the build-time snapshot. Installs entries
    into ``catalog.LIVE_CATALOG`` (static table remains the fallback when this
    yields nothing). Returns the entry list (possibly []).

    ``check_releases`` drops entries that cannot list an installable release
    (see ``catalog.filter_unavailable_packages``), so a hand-edited manifest
    cannot leave a model in the drop-downs with nothing to install.
    ``allow_release_network`` lets that check ask GitHub about repositories the
    local release snapshot does not know yet; only the off-UI manifest worker
    enables it, because the answer is not worth blocking a caller for.
    """
    from . import catalog

    fetched = fetch_manifest()
    if fetched:
        cache_manifest(fetched)
        entries = fetched
    else:
        # A failed fetch says nothing about the catalogue that is already
        # installed. Falling back over it is how a transient GitHub or CDN
        # failure shrank a live multi-model catalogue back to whatever stale
        # two-model snapshot the build happened to ship.
        existing = list(catalog.LIVE_CATALOG)
        if existing:
            logger.info(
                "Manifest fetch failed; keeping the %d-entry live catalogue",
                len(existing),
            )
            return existing
        entries = load_cached_manifest()
    if not entries:
        # Baked in at build time: no connection, no cache, still a catalogue.
        entries = load_snapshot_manifest()
    if entries and check_releases:
        # A failed manifest fetch means no connection: keep the entry rather
        # than spend minutes on release requests that cannot be answered.
        entries = catalog.filter_unavailable_packages(
            entries, allow_network=allow_release_network and bool(fetched)
        )
    catalog.set_live_catalog(entries or [])
    return entries or []


class ManifestWorker(QThread):
    """Fetches the live catalog off the UI thread (cache-first by default)."""

    finished = Signal(list)  # entries

    def __init__(self, parent=None, force_refresh=False):
        super().__init__(parent)
        self.force_refresh = force_refresh

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            entries = refresh_catalog(
                force_refresh=self.force_refresh,
                allow_release_network=True,
            )
            if self.isInterruptionRequested():
                return
            self.finished.emit(entries)
        except Exception as e:
            if not self.isInterruptionRequested():
                logger.exception("Manifest refresh failed")
                self.finished.emit([])
