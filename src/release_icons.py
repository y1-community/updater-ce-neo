"""Release icons for the install card and the software details pane.

Priority, first match that actually loads:
1. ``updater.png`` attached to the GitHub release.
2. The optional ``icon`` attribute on the catalogue package (slidia_manifest.xml).
3. No image. The install card then draws a theme squircle and a settings glyph.
   The application icon is not a release placeholder.

A missing file or a failed download falls through. Nothing here is required
for older clients: ``icon`` is optional and ``updater.png`` is not required.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

UPDATER_ASSET_NAME = "updater.png"
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
)


def icon_candidate_urls(release, package=None) -> list[str]:
    """Ordered icon URLs. Empty when neither a release asset nor a manifest icon exists."""
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        text = str(url or "").strip()
        if text and text not in seen:
            seen.add(text)
            urls.append(text)

    for asset in (release or {}).get("assets") or []:
        name = str(asset.get("name") or "")
        if name.lower() == UPDATER_ASSET_NAME:
            add(asset.get("browser_download_url") or "")
            break
    add(getattr(package, "icon", "") or "")
    return urls


def image_bytes_ok(data: bytes) -> bool:
    if not data or len(data) < 16:
        return False
    if data.startswith(b"RIFF") and b"WEBP" not in data[:16]:
        return False
    return data.startswith(_IMAGE_MAGIC)


def icon_cache_file(url: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.img"


def read_cached_icon(url: str, cache_dir: Path) -> bytes | None:
    path = icon_cache_file(url, cache_dir)
    try:
        if not path.is_file() or path.stat().st_size < 16:
            return None
        data = path.read_bytes()
    except Exception:
        return None
    if not image_bytes_ok(data):
        return None
    return data


def write_cached_icon(url: str, data: bytes, cache_dir: Path) -> Path | None:
    if not image_bytes_ok(data):
        return None
    path = icon_cache_file(url, cache_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except Exception as exc:
        logger.debug("Could not cache icon %s: %s", url, exc)
        return None
    return path


def default_icon_cache_dir() -> Path:
    from .manifest import _cache_root

    directory = _cache_root() / "icons"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def fetch_icon_bytes(url: str, timeout: float = 8) -> bytes | None:
    """Download an icon. Failures (404, offline, not an image) return None."""
    try:
        import requests

        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.content or b""
    except Exception as exc:
        logger.debug("Icon fetch failed for %s: %s", url, exc)
        return None
    if not image_bytes_ok(data):
        return None
    return data
