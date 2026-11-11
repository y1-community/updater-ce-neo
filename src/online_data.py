"""Bundled snapshots of every file Updater CE reads from the internet.

The catalogue manifest, notices, supporters feed and release blocklist all live
at ``innioasis.app`` (with the website's GitHub repository as the fallback).
Waiting for those on launch made a slow or filtered connection look like a slow
app, and a first run with no connection at all had nothing to show.

This module gives each of them three copies, in the order they are read:

1. **Cache** -- the last copy this install downloaded
   (``<app cache>/online/<name>``).
2. **Bundle** -- a copy baked into the app at packaging time
   (``assets/online/<name>``, refreshed by ``scripts/fetch_online_data.py``
   before every build).
3. **Nothing** -- the caller falls back to its own built-in data (the static
   catalogue table, no notices, a header-only donors file, no blocklist).

Reading is always local and instant: the network is never on the launch path.
:func:`refresh_all_async` runs after the first window is up, replaces a stale
cache with a fresh download, and does nothing on failure -- so a launch is never
held up, and a connection that comes and goes simply keeps the last good copy.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .config import (
    INNIOASIS_RAW_FALLBACK_PREFIX,
    INNIOASIS_RAW_PREFIX,
    INNIOASIS_SITE_PREFIX,
    RELEASE_CACHE_TTL_SECONDS,
    github_fallback_url,
    site_source_urls,
)
from .paths import REPO_ROOT, RESOURCES_DIR, app_cache_dir

logger = logging.getLogger(__name__)

#: Folder under ``assets/`` and under the per-user cache that holds the files.
BUNDLE_SUBDIR = "online"
CACHE_SUBDIR = "online"
#: A day old is stale, matching the catalogue's own 24 h cache.
#: How long a downloaded copy is trusted before the next refresh. Short on
#: purpose: a new firmware or notice reaches users within minutes. Each file is
#: one small request, so this stays cheap.
ONLINE_TTL_SECONDS = 5 * 60
DEFAULT_TTL_SECONDS = ONLINE_TTL_SECONDS
USER_AGENT = "InnioasisUpdater/3.0 (+https://innioasis.app)"
REQUEST_TIMEOUT = 8.0

NOTICE_URL = "https://innioasis.app/notice.xml"
BLOCKLIST_URL = "https://innioasis.app/release_blocklist.csv"


@dataclass(frozen=True)
class OnlineFile:
    """One remote file: its name, sources, freshness window and validation."""

    name: str
    urls: tuple[str, ...]
    ttl: int = DEFAULT_TTL_SECONDS
    kind: str = "text"  # "xml" | "csv" | "json" | "text"

    def alternate_urls(self) -> tuple[str, ...]:
        """Every URL to try, including each one's GitHub-repository fallback."""
        out: list[str] = []
        for url in self.urls:
            rel_path = url.rsplit("/", 1)[-1]
            for prefix in (INNIOASIS_RAW_PREFIX, INNIOASIS_SITE_PREFIX, INNIOASIS_RAW_FALLBACK_PREFIX):
                if url.startswith(prefix):
                    rel_path = url[len(prefix):]
                    break
            for candidate in (*site_source_urls(rel_path), github_fallback_url(url)):
                if candidate and candidate not in out:
                    out.append(candidate)
        return tuple(out)


#: The files this module owns. ``notice.xml`` is also read by NoticeService and
#: ``slidia_manifest.xml`` by the catalogue; both go through here so there is
#: one cache and one refresh policy rather than one per feature.
FILES: dict[str, OnlineFile] = {
    "slidia_manifest.xml": OnlineFile(
        name="slidia_manifest.xml",
        urls=site_source_urls("slidia_manifest.xml"),
        kind="xml",
    ),
    "notice.xml": OnlineFile(
        name="notice.xml",
        urls=site_source_urls("notice.xml"),
        kind="xml",
    ),
    "donors.csv": OnlineFile(
        name="donors.csv", urls=site_source_urls("donors.csv"), kind="csv"
    ),
    "release_blocklist.csv": OnlineFile(
        name="release_blocklist.csv", urls=site_source_urls("release_blocklist.csv"), kind="csv"
    ),
    "releases_backup.json": OnlineFile(
        name="releases_backup.json",
        urls=site_source_urls("feeds/releases.json"),
        kind="json",
    ),
    "releases.xml": OnlineFile(
        name="releases.xml",
        urls=site_source_urls("feeds/releases.xml"),
        kind="xml",
    ),
}


def bundle_dir() -> Path:
    """Folder holding the copies baked into this app.

    ``RESOURCES_DIR`` already resolves to the checkout's ``assets`` in a
    development run and to the unpacked bundle when frozen.
    """
    for candidate in (RESOURCES_DIR / BUNDLE_SUBDIR, REPO_ROOT / "assets" / BUNDLE_SUBDIR):
        if candidate.is_dir():
            return candidate
    return RESOURCES_DIR / BUNDLE_SUBDIR


def cache_dir() -> Path:
    """Per-user folder for downloaded copies (created on first use)."""
    d = app_cache_dir() / CACHE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_path(name: str) -> Optional[Path]:
    """The packaged copy of ``name``, or ``None`` when the build did not ship one."""
    try:
        path = bundle_dir() / Path(name).name
    except Exception:
        return None
    return path if path.is_file() else None


def cached_path(name: str) -> Path:
    """Where a downloaded copy of ``name`` is stored."""
    return cache_dir() / Path(name).name


def _read(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
    except OSError:
        return ""


def bundled_text(name: str) -> str:
    """Contents of the copy shipped inside the app ("" when there is none)."""
    return _read(bundled_path(name))


def cached_text(name: str) -> str:
    """Contents of the last downloaded copy ("" when there is none)."""
    try:
        path = cached_path(name)
    except OSError:
        return ""
    return _read(path if path.is_file() else None)


def read_text(name: str) -> str:
    """The best local copy of ``name``: downloaded first, then bundled.

    Never touches the network, so callers can use it on the first-paint path.
    """
    text = cached_text(name)
    if text.strip():
        return text
    return bundled_text(name)


def age_seconds(name: str, now: Optional[float] = None) -> Optional[float]:
    """How old the downloaded copy is, or ``None`` when there is no copy."""
    try:
        path = cached_path(name)
        if not path.is_file():
            return None
        return max(0.0, (now if now is not None else time.time()) - path.stat().st_mtime)
    except OSError:
        return None


def is_stale(name: str, now: Optional[float] = None) -> bool:
    """True when the downloaded copy is missing or past the file's TTL."""
    spec = FILES.get(name)
    ttl = spec.ttl if spec else DEFAULT_TTL_SECONDS
    age = age_seconds(name, now=now)
    return age is None or age >= ttl


def _is_usable(spec: OnlineFile, text: str) -> bool:
    """Reject captive portals, HTML error pages and truncated payloads."""
    text = (text or "").strip()
    if not text:
        return False
    if text[:200].lstrip().lower().startswith(("<!doctype html", "<html")):
        return False
    if spec.kind == "xml":
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.debug("%s is not valid XML: %s", spec.name, e)
            return False
        return bool(root.tag)
    if spec.kind == "csv":
        # A header-only file is legitimate (no donations yet, nothing blocked).
        return bool(text.splitlines())
    if spec.kind == "json":
        try:
            parsed = json.loads(text)
        except Exception as e:
            logger.debug("%s is not valid JSON: %s", spec.name, e)
            return False
        if isinstance(parsed, dict):
            return bool(parsed.get("repos") or parsed)
        return isinstance(parsed, list) and len(parsed) > 0
    return True


def _ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


def _download(spec: OnlineFile, timeout: float = REQUEST_TIMEOUT) -> Optional[str]:
    """Download ``spec`` from the first source that answers with a usable file."""
    request_headers = {"User-Agent": USER_AGENT, "Cache-Control": "no-cache"}
    for url in spec.alternate_urls():
        for verify in (True, False):
            try:
                ctx = _ssl_context() if verify else ssl._create_unverified_context()
                req = urllib.request.Request(url, headers=request_headers)
                with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                    if resp.status != 200:
                        continue
                    text = resp.read().decode("utf-8", errors="ignore")
                if _is_usable(spec, text):
                    return text
                logger.info("Ignoring unusable %s from %s", spec.name, url)
            except Exception as e:
                logger.debug("Could not fetch %s from %s: %s", spec.name, url, e)
                continue
    return None


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _worth_seeding(spec: OnlineFile, text: str) -> bool:
    """True when a bundled copy is real content rather than an empty stub.

    Seeding marks the cache fresh for the file's TTL, so promoting a stub (an
    XML feed with no entries, a CSV with only a header) would keep the live
    version from ever being fetched. A stub must leave the cache empty instead,
    so the next refresh downloads the real file.
    """
    text = (text or "").strip()
    if not text:
        return False
    if spec.kind == "xml":
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return False
        return len(root) > 0
    if spec.kind == "csv":
        rows = [line for line in text.splitlines() if line.strip()]
        return len(rows) > 1
    if spec.kind == "json":
        try:
            parsed = json.loads(text)
        except Exception:
            return False
        if isinstance(parsed, dict):
            return bool(parsed.get("repos"))
        return isinstance(parsed, list) and len(parsed) > 0
    return True


def seed_cache(names: Optional[Iterable[str]] = None) -> list[str]:
    """Promote the bundled copies into the download cache when it is empty.

    A fresh install (or one upgraded from the pre-2.5 layout) has no cache, so
    every online file would read as stale on first launch and be fetched again
    before the UI could rely on it. The copies baked into the build are the
    newest thing the build had, so they become the cache once; later launches
    keep using them until their TTL runs out.
    """
    seeded: list[str] = []
    for name in names if names is not None else tuple(FILES):
        if FILES.get(name) is None:
            continue
        try:
            target = cached_path(name)
            if target.is_file():
                continue
            spec = FILES[name]
            text = bundled_text(name)
            if not _worth_seeding(spec, text):
                continue
            _write_atomic(target, text)
            seeded.append(name)
            logger.info("Seeded %s from the bundled copy", name)
        except OSError as e:
            logger.debug("Could not seed %s: %s", name, e)
    return seeded


def refresh(
    name: str,
    force: bool = False,
    fetch: Optional[Callable[[OnlineFile, float], Optional[str]]] = None,
    timeout: float = REQUEST_TIMEOUT,
    now: Optional[float] = None,
) -> bool:
    """Replace the cached copy of ``name`` when it is stale (or ``force``).

    Returns True only when a new copy was actually written. Every failure is
    swallowed: the previous cache, the bundled copy and the caller's built-in
    fallback all keep working exactly as before.
    """
    spec = FILES.get(name)
    if spec is None:
        raise KeyError(f"unknown online file {name!r}")
    if not force and not is_stale(name, now=now):
        return False
    try:
        text = fetch(spec, timeout) if fetch else _download(spec, timeout)
    except Exception as e:
        logger.debug("Refreshing %s failed: %s", name, e)
        return False
    if not text or not _is_usable(spec, text):
        logger.info("Kept the previous %s copy; no usable remote version", spec.name)
        return False
    try:
        _write_atomic(cached_path(spec.name), text)
    except OSError as e:
        logger.warning("Could not cache %s: %s", spec.name, e)
        return False
    logger.info("Updated %s from the network (%d bytes)", spec.name, len(text))
    return True


_refresh_lock = threading.Lock()
_refresh_thread: Optional[threading.Thread] = None


#: Files a windowless background session keeps listening to. Donors, the
#: release feeds and the blocklist are only read for something on screen; a
#: tray-only copy has no donation bar to update and no feed snapshot to show.
#: ``notice.xml`` is refreshed by NoticeService (it has to parse the feed and
#: post the notification itself), so the manifest is the only file fetched
#: directly from this module by the background agent.
BACKGROUND_FILES = ("slidia_manifest.xml",)

#: NoticeService owns the notice feed: it fetches the same cache through
#: :func:`refresh` and then parses it. A generic refresh must leave the file
#: alone, or one launch downloads and logs the same notice.xml twice.
NOTICE_SERVICE_FILES = ("notice.xml",)


def refresh_files(
    names: Iterable[str],
    force: bool = False,
    timeout: float = REQUEST_TIMEOUT,
    now: Optional[float] = None,
) -> dict[str, bool]:
    """Refresh only ``names``, sequentially, off any UI thread.

    The background agent uses this to keep the firmware manifest current
    without pulling the donors feed, the release feeds or the blocklist that
    no window is showing.
    """
    wanted = tuple(dict.fromkeys(str(name) for name in (names or ())))
    if not wanted:
        return {}
    # Seeding first means a fresh install counts as fresh off the build's own
    # copies instead of downloading everything again on the first launch.
    seed_cache(wanted)
    updated: dict[str, bool] = {}
    for name in wanted:
        try:
            updated[name] = refresh(name, force=force, timeout=timeout, now=now)
        except Exception:
            logger.debug("Refreshing %s failed", name, exc_info=True)
            updated[name] = False
    return updated


def refresh_all(force: bool = False, timeout: float = REQUEST_TIMEOUT) -> dict[str, bool]:
    """Refresh every stale file except the notice feed, sequentially.

    ``notice.xml`` is deliberately left to NoticeService, which downloads it
    through :func:`refresh` and then parses the result. Including it here made
    the window refresh and the notice service fetch the same URL in the same
    launch, producing duplicate ``Updated notice.xml`` log lines.
    """
    names = tuple(name for name in FILES if name not in NOTICE_SERVICE_FILES)
    return refresh_files(names, force=force, timeout=timeout)


def refresh_files_async(
    names: Iterable[str],
    force: bool = False,
    timeout: float = REQUEST_TIMEOUT,
) -> bool:
    """Start a background refresh of just ``names`` unless one is running.

    There is one refresh worker per process, whatever its file set: a window
    and the background agent asking at the same moment must not download the
    same manifest twice.
    """
    global _refresh_thread
    wanted = tuple(dict.fromkeys(str(name) for name in (names or ())))
    if not wanted:
        return False
    with _refresh_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return False
        thread = threading.Thread(
            target=refresh_files,
            kwargs={"names": wanted, "force": force, "timeout": timeout},
            name="online-data-refresh",
            daemon=True,
        )
        _refresh_thread = thread
        thread.start()
        return True


def refresh_all_async(force: bool = False, timeout: float = REQUEST_TIMEOUT) -> bool:
    """Refresh every online file except ``notice.xml`` in the background.

    Deliberately lazy: called after the first window is shown (and again when
    connectivity returns), so launch never waits for a byte from the network.
    NoticeService is the single owner of the notice feed. Returns True when a
    new worker was started.
    """
    names = tuple(name for name in FILES if name not in NOTICE_SERVICE_FILES)
    return refresh_files_async(names, force=force, timeout=timeout)


def wait_for_refresh(timeout: float = 0.0) -> None:
    """Join a running refresh worker (tests and shutdown only)."""
    thread = _refresh_thread
    if thread is not None:
        thread.join(timeout)
