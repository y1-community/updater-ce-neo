"""Firmware catalogue for the terminal front ends, standard library only.

The graphical app reads ``slidia_manifest.xml`` and asks GitHub for releases
with Qt's network stack. A Raspberry Pi Zero cannot install that stack, so this
module does the same two jobs with ``urllib`` and ``xml.etree``:

* read the manifest (24 h disk cache, so ``list`` works offline and a slow link
  is not hit for every command),
* resolve the newest release asset for a package (a short in-process cache, so
  ``list`` followed by ``download`` costs one request per repository).

Nothing here knows about Qt, and nothing writes outside the user's cache
directory.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

CATALOG_URLS = (
    "https://raw.githubusercontent.com/y1-community/innioasis-updater/refs/heads/main/slidia_manifest.xml",
    "https://innioasis.app/slidia_manifest.xml",
    "https://raw.githubusercontent.com/ryan-specter/innioasis-updater/refs/heads/main/slidia_manifest.xml",
)
CACHE_TTL_SECONDS = 24 * 60 * 60
RELEASE_TTL_SECONDS = 15 * 60
USER_AGENT = "UpdaterCE-lite"
BLOCKLIST_URL = "https://innioasis.app/release_blocklist.csv"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "innioasis-updater" / "catalog"


def _bundled_path(name: str) -> Path | None:
    """A copy of a catalogue asset shipped beside the app or in the payload."""
    roots = [
        Path(getattr(sys, "_MEIPASS", "")) / "assets" / "online",
        Path(__file__).resolve().parents[2] / "assets" / "online",
        Path(getattr(sys, "_MEIPASS", "")) / "online",
    ]
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


def bundled_asset(name: str) -> bytes | None:
    """Read a shipped catalogue CSV/XML, or ``None`` when it is not present."""
    path = _bundled_path(name)
    try:
        return path.read_bytes() if path is not None else None
    except OSError:
        return None


def _get(url: str, timeout: float = 20.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        request.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _read_cache(path: Path, ttl: int) -> bytes | None:
    try:
        if path.exists() and time.time() - path.stat().st_mtime < ttl:
            return path.read_bytes()
    except OSError:
        pass
    return None


def _write_cache(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        pass


def fetch_manifest(force: bool = False, timeout: float = 20.0) -> bytes:
    """The catalogue XML, from cache when it is fresh, else from the network."""
    cache = cache_dir() / "slidia_manifest.xml"
    if not force:
        cached = _read_cache(cache, CACHE_TTL_SECONDS)
        if cached is not None:
            return cached

    errors: list[str] = []
    for url in CATALOG_URLS:
        try:
            data = _get(url, timeout=timeout)
        except (urllib.error.URLError, OSError) as exc:
            errors.append(f"{url}: {exc}")
            continue
        if b"<slidia" not in data:
            errors.append(f"{url}: not a catalogue document")
            continue
        _write_cache(cache, data)
        return data

    stale = None
    try:
        stale = cache.read_bytes()
    except OSError:
        pass
    if stale is not None:
        return stale
    bundled = bundled_asset("slidia_manifest.xml")
    if bundled is not None and b"<slidia" in bundled:
        return bundled
    raise RuntimeError(
        "could not fetch the firmware catalogue (offline, and no cached copy): "
        + "; ".join(errors)
    )


def fetch_blocklist(force: bool = False, timeout: float = 20.0) -> str:
    """The release blocklist, from cache, the network, or the shipped copy."""
    cache = cache_dir() / "release_blocklist.csv"
    if not force:
        cached = _read_cache(cache, CACHE_TTL_SECONDS)
        if cached is not None:
            return cached.decode("utf-8", "replace")
    try:
        data = _get(BLOCKLIST_URL, timeout=timeout)
    except (urllib.error.URLError, OSError):
        data = None
    if data and b"software" in data.lower():
        _write_cache(cache, data)
        return data.decode("utf-8", "replace")
    try:
        stale = cache.read_text(encoding="utf-8")
    except OSError:
        stale = ""
    if stale.strip():
        return stale
    bundled = bundled_asset("release_blocklist.csv")
    return bundled.decode("utf-8", "replace") if bundled else ""


_blocklist_cache: tuple[float, list] | None = None


def blocklist_rules(force: bool = False) -> list:
    """Parsed ``release_blocklist.csv`` rules, cached for this process."""
    global _blocklist_cache
    now = time.time()
    if not force and _blocklist_cache and now - _blocklist_cache[0] < RELEASE_TTL_SECONDS:
        return _blocklist_cache[1]
    from .blocklist import parse_blocklist_csv

    rules = parse_blocklist_csv(fetch_blocklist(force=force))
    _blocklist_cache = (now, rules)
    return rules


@dataclass(frozen=True)
class Package:
    """One firmware entry as the terminal menus need it."""

    name: str
    brand: str
    model: str
    repo: str
    package_name: str
    kind: str = "img"
    handler: str = "Custom Firmware"
    slug: str = ""
    post_install: str = ""
    post_install_2: str = ""
    do_not_keep_data: bool = False
    #: The firmware must use SP Flash Tool's Format All + Download mode
    #: (``PMT_format_needed``). Formatting storage makes Keep Data pointless,
    #: so it is refused with the same veto as ``do_not_keep_data``.
    pmt_format_needed: bool = False
    #: The repository of the firmware this one was built from, when the
    #: catalogue names it. Equal or related firmwares share a data partition,
    #: which is what Keep Data depends on (see ``src/keep_data.py``).
    derivative: str = ""
    #: A firmware that must already be on the player before this one can be
    #: installed (``install_first`` in ``slidia_manifest.xml``).
    install_first: str = ""
    #: The base version the player must be running (``install_first_version``).
    install_first_version: str = ""

    @property
    def title(self) -> str:
        return f"{self.name} ({self.brand})" if self.brand else self.name


def _attr(element: ET.Element, *names: str, default: str = "") -> str:
    for name in names:
        value = (element.get(name) or "").strip()
        if value:
            return value
    return default


def _model_package_name(element: ET.Element, model: str) -> str:
    """The archive this entry names for one player (``package_name_<model>``).

    The terminal menus pick no hardware revision, so only the model-level and
    brand-qualified names apply here; the GUI resolves the type-specific ones
    too. The value may be a wildcard, as in ``pick_asset``.
    """
    if not model:
        return ""
    attributes = {
        str(key).lower(): str(value).strip() for key, value in element.attrib.items()
    }
    brand = _attr(element, "brand").lower()
    keys = []
    if brand:
        keys.append(f"package_name_{brand}_{model.lower()}")
    keys.append(f"package_name_{model.lower()}")
    for key in keys:
        if attributes.get(key):
            return attributes[key]
    return ""


def parse_manifest(data: bytes) -> list[Package]:
    root = ET.fromstring(data)
    packages: list[Package] = []
    seen: set[tuple[str, str, str]] = set()
    for element in root.iter("package"):
        repo = _attr(element, "repo", "url").rstrip("/")
        if not repo or "/" not in repo:
            continue
        # One entry may serve several models (``device="Y1,Y2,G5"``); the old
        # single-model form is the same loop with one item in it.
        raw_devices = _attr(element, "device", "model")
        devices = [
            part.strip().upper() for part in raw_devices.split(",") if part.strip()
        ] or [""]
        name = _attr(element, "name", default="Firmware")
        for model in devices:
            package_name = _model_package_name(element, model) or _attr(
                element, "package_name", "package"
            )
            if not package_name:
                package_name = "rom_MODEL.zip" if model and model != "Y1" else "rom.zip"
            package_name = package_name.replace("MODEL", model)
            slug = _attr(element, "slug")
            if not slug:
                slug = f"{repo.split('/')[-1]}-{model}".lower().replace(" ", "-")
            key = (name, model, repo)
            if key in seen:
                continue
            seen.add(key)
            packages.append(
                Package(
                    name=name,
                    brand=_attr(element, "brand"),
                    model=model,
                    repo=repo,
                    package_name=package_name,
                    kind=_attr(element, "type", default="img"),
                    handler=_attr(element, "handler", default="Custom Firmware"),
                    slug=slug,
                    post_install=_attr(element, "post_install"),
                    post_install_2=_attr(element, "post_install_2", "post_install2"),
                    do_not_keep_data=_attr(element, "do_not_keep_data", "no_keep_data").lower() in {"1", "true", "yes"},
                    pmt_format_needed=_attr(element, "PMT_format_needed", "pmt_format_needed").lower() in {"1", "true", "yes"},
                    derivative=_attr(element, "derivative"),
                    install_first=_attr(element, "install_first"),
                    install_first_version=_attr(element, "install_first_version"),
                )
            )
    return packages


def packages(force: bool = False) -> list[Package]:
    return parse_manifest(fetch_manifest(force=force))


def models(items: list[Package] | None = None) -> list[str]:
    items = items if items is not None else packages()
    return sorted({item.model for item in items if item.model})


def find(items: list[Package], query: str) -> list[Package]:
    needle = (query or "").strip().lower()
    if not needle:
        return list(items)
    return [
        item
        for item in items
        if needle in item.name.lower()
        or needle in item.model.lower()
        or needle == item.model.lower()
    ]


_release_cache: dict[str, tuple[float, dict]] = {}


def latest_release(repo: str, timeout: float = 20.0) -> dict:
    now = time.time()
    hit = _release_cache.get(repo)
    if hit and now - hit[0] < RELEASE_TTL_SECONDS:
        return hit[1]
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        release = json.loads(_get(url, timeout=timeout))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"{repo}: {exc}") from exc
    if not isinstance(release, dict):
        raise RuntimeError(f"{repo}: unexpected release payload")
    _release_cache[repo] = (now, release)
    return release


def pick_asset(release: dict, package_name: str) -> dict | None:
    """The ROM zip for this package, preferring the manifest's own filename.

    The declared name may be a wildcard (``Y2Linux-*-first-install.zip``), so
    it is matched as a glob rather than compared literally.
    """
    assets = [a for a in (release.get("assets") or []) if a.get("name")]
    wanted = (package_name or "").lower()
    for asset in assets:
        if wanted and fnmatch(asset["name"].lower(), wanted):
            return asset
    for asset in assets:
        name = asset["name"].lower()
        if name.endswith(".zip") and (name.startswith("rom") or name.startswith("firmware")):
            return asset
    for asset in assets:
        if asset["name"].lower().endswith(".zip"):
            return asset
    return None


def resolve_download(item: Package, timeout: float = 20.0) -> tuple[str, str]:
    """Return ``(url, asset_name)`` for this package's newest ROM."""
    release = latest_release(item.repo, timeout=timeout)
    asset = pick_asset(release, item.package_name)
    if not asset:
        raise RuntimeError(f"{item.repo}: the newest release has no firmware zip")
    return asset["browser_download_url"], asset["name"]
