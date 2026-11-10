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
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

CATALOG_URLS = (
    "https://raw.githubusercontent.com/y1-community/innioasis-updater/refs/heads/main/slidia_manifest.xml",
    "https://innioasis.app/slidia_manifest.xml",
    "https://raw.githubusercontent.com/ryan-specter/innioasis-updater/refs/heads/main/slidia_manifest.xml",
)
CACHE_TTL_SECONDS = 24 * 60 * 60
RELEASE_TTL_SECONDS = 15 * 60
USER_AGENT = "UpdaterCE-lite"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "innioasis-updater" / "catalog"


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
    raise RuntimeError(
        "could not fetch the firmware catalogue (offline, and no cached copy): "
        + "; ".join(errors)
    )


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

    @property
    def title(self) -> str:
        return f"{self.name} ({self.brand})" if self.brand else self.name


def _attr(element: ET.Element, *names: str, default: str = "") -> str:
    for name in names:
        value = (element.get(name) or "").strip()
        if value:
            return value
    return default


def parse_manifest(data: bytes) -> list[Package]:
    root = ET.fromstring(data)
    packages: list[Package] = []
    seen: set[tuple[str, str, str]] = set()
    for element in root.iter("package"):
        repo = _attr(element, "repo", "url").rstrip("/")
        if not repo or "/" not in repo:
            continue
        model = _attr(element, "device", "model").upper()
        name = _attr(element, "name", default="Firmware")
        package_name = _attr(element, "package_name", "package")
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
                do_not_keep_data=_attr(element, "do_not_keep_data").lower() in {"1", "true", "yes"},
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
    """The ROM zip for this package, preferring the manifest's own filename."""
    assets = [a for a in (release.get("assets") or []) if a.get("name")]
    wanted = (package_name or "").lower()
    for asset in assets:
        if asset["name"].lower() == wanted:
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
