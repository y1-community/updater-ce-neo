"""Release icons for the install card and the software details pane.

The icon URL is built from the release the app already has (owner, repo, tag).
Nothing here calls the GitHub releases API.

Light image, first name that actually downloads:
1. ``updater.jpg``
2. ``updater.jpeg``
3. ``updater.png``

Dark mode tries ``updater_dark.jpg``, ``updater_dark.jpeg``, ``updater_dark.png``
first, then the light names above.

The software logo from ``slidia_manifest.xml`` is attribute ``icon`` (jpg, jpeg,
or png). Dark mode also reads ``icon_dark``, then ``image_dark``, before that
light logo. That logo is the stand-in for every release. A release image
cross-fades over it once the release file has loaded.

A missing file (404) or a failed download falls through. When nothing loads,
the card draws an accent squircle and a settings glyph. The application icon
is not a placeholder.

Preloads stay off the UI thread. One download runs at a time, and each later
request waits a random gap so image hosts do not see a burst.
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)

# Compressed names first. ``updater.png`` remains a valid light icon.
LIGHT_ICON_NAMES = ("updater.jpg", "updater.jpeg", "updater.png")
DARK_ICON_NAMES = ("updater_dark.jpg", "updater_dark.jpeg", "updater_dark.png")
UPDATER_ASSET_NAME = "updater.png"
ICON_URL = "https://github.com/{owner}/{repo}/releases/download/{tag}/{filename}"
# One download in flight. A second overlapping fetch is the most this pacing allows.
PRELOAD_CONCURRENCY = 1
JITTER_MIN_SECONDS = 0.25
JITTER_MAX_SECONDS = 0.85
_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"RIFF",
)


def _repo_and_tag(release, package=None) -> tuple[str, str]:
    """Owner/repo and tag from the release dict the catalogue already holds."""
    repo = str((release or {}).get("source_repo") or "").strip()
    tag = str((release or {}).get("tag_name") or "").strip()
    if not repo and package is not None:
        repo = str(getattr(package, "repo", "") or "").strip()
    if repo and tag:
        return repo, tag
    html = str((release or {}).get("html_url") or "")
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/releases/tag/([^?#]+)", html)
    if match:
        repo = repo or f"{match.group(1)}/{match.group(2)}"
        tag = tag or match.group(3)
    return repo, tag


def release_download_icon_url(repo: str, tag: str, filename: str) -> str:
    """``https://github.com/{owner}/{repo}/releases/download/{tag}/{filename}``."""
    owner, name = str(repo).split("/", 1)
    return ICON_URL.format(
        owner=quote(owner, safe=""),
        repo=quote(name, safe=""),
        tag=quote(str(tag), safe=""),
        filename=filename,
    )


def _dark_flag(dark: bool | None) -> bool:
    if dark is None:
        try:
            from .ui.dark import is_dark

            return bool(is_dark())
        except Exception:
            return False
    return bool(dark)


def _deduped(urls) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for url in urls:
        text = str(url or "").strip()
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered


def release_asset_urls(release, package=None, *, dark: bool | None = None) -> list[str]:
    """Release-file URLs only. Built from owner, repo, and tag. No GitHub API."""
    dark = _dark_flag(dark)
    repo, tag = _repo_and_tag(release, package)
    if not repo or not tag or "/" not in repo:
        return []
    names = list(DARK_ICON_NAMES) + list(LIGHT_ICON_NAMES) if dark else list(LIGHT_ICON_NAMES)
    return [release_download_icon_url(repo, tag, filename) for filename in names]


def software_icon_urls(package, *, dark: bool | None = None) -> list[str]:
    """Software logo from ``slidia_manifest.xml``. ``icon`` is the existing key.

    Dark mode tries ``icon_dark`` (which also accepts ``image_dark``) first.
    """
    dark = _dark_flag(dark)
    urls = []
    if dark:
        urls.append(getattr(package, "icon_dark", "") or "")
    urls.append(getattr(package, "icon", "") or "")
    return _deduped(urls)


def icon_candidate_urls(release, package=None, *, dark: bool | None = None) -> list[str]:
    """Release files, then the software logo. Built without the GitHub API."""
    return _deduped(
        list(release_asset_urls(release, package, dark=dark))
        + list(software_icon_urls(package, dark=dark))
    )


def next_preload_gap(rng: random.Random | None = None) -> float:
    """Random pause before the next icon request. Not a fixed interval."""
    rng = rng or random.Random()
    return float(rng.uniform(JITTER_MIN_SECONDS, JITTER_MAX_SECONDS))


def preload_start_times(count: int, rng: random.Random | None = None) -> list[float]:
    """When each preload may start, in seconds.

    The first request may start at 0 (the release the user just selected).
    Every later start waits a fresh random gap, so the list is not fired at once.
    ``PRELOAD_CONCURRENCY`` is how many of those requests may be in flight.
    """
    count = max(int(count), 0)
    if count == 0:
        return []
    rng = rng or random.Random()
    times = [0.0]
    for _ in range(count - 1):
        times.append(times[-1] + next_preload_gap(rng))
    return times


def resolve_icon_bytes(release, package=None, *, dark: bool = False, cache_dir: Path | None = None, allow_network: bool = True) -> bytes | None:
    """First cached or downloaded icon. 404 and bad files fall through. None keeps the squircle."""
    directory = cache_dir or default_icon_cache_dir()
    for url in icon_candidate_urls(release, package, dark=dark):
        data = read_cached_icon(url, directory)
        if data is None and allow_network:
            data = fetch_icon_bytes(url)
            if data:
                write_cached_icon(url, data, directory)
        if data:
            return data
    return None


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
        miss = icon_miss_path(url, cache_dir)
        if miss.is_file():
            miss.unlink()
    except Exception as exc:
        logger.debug("Could not cache icon %s: %s", url, exc)
        return None
    return path


def icon_miss_path(url: str, cache_dir: Path) -> Path:
    return icon_cache_file(url, cache_dir).with_suffix(".miss")


def icon_known_miss(url: str, cache_dir: Path) -> bool:
    """True when this exact name already 404'd. It is not requested again."""
    try:
        return icon_miss_path(url, cache_dir).is_file()
    except Exception:
        return False


def remember_icon_miss(url: str, cache_dir: Path) -> None:
    path = icon_miss_path(url, cache_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"404")
    except Exception as exc:
        logger.debug("Could not remember icon miss %s: %s", url, exc)


def default_icon_cache_dir() -> Path:
    from .manifest import _cache_root

    directory = _cache_root() / "icons"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def fetch_icon_bytes(url: str, timeout: float = 8) -> bytes | None:
    """Download an icon. Failures (404, offline, not an image) return None."""
    data, _code = fetch_icon_status(url, timeout=timeout)
    return data


def fetch_icon_status(url: str, timeout: float = 8) -> tuple[bytes | None, int]:
    """Download an icon. The status is 404 when that name is absent, 0 when the request failed."""
    code = 0
    try:
        import requests

        resp = requests.get(url, timeout=timeout)
        code = int(resp.status_code)
        if code != 200:
            return None, code
        data = resp.content or b""
    except Exception as exc:
        logger.debug("Icon fetch failed for %s: %s", url, exc)
        return None, 0
    if not image_bytes_ok(data):
        return None, code
    return data, 200


def load_first_icon_bytes(urls, cache_dir: Path, *, allow_network: bool = True) -> bytes | None:
    """First cached or downloaded image. Skips names already cached as a 404."""
    for url in urls:
        cached = read_cached_icon(url, cache_dir)
        if cached:
            return cached
        if icon_known_miss(url, cache_dir) or not allow_network:
            continue
        fetched, code = fetch_icon_status(url)
        if fetched:
            write_cached_icon(url, fetched, cache_dir)
            return fetched
        if code == 404:
            remember_icon_miss(url, cache_dir)
    return None
