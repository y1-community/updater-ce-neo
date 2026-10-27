"""Release icons for the install card and the software details pane.

The icon URL is built from the release the app already has (owner, repo, tag).
Nothing here calls the GitHub releases API.

Preferred release image names are ``updater.jpg`` and ``updater.png``,
followed by the legacy ``updater.jpeg``. Dark mode tries ``updater_dark`` first.
If none are present, square PNG/JPG release assets are considered in release
asset order. ``icon`` and then ``image`` manifest URLs are tried only after
release images fail.

Dark mode prefers ``updater_dark.jpg`` and ``updater_dark.png``, then the
legacy ``updater_dark.jpeg`` before trying the light names above.

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

# Preserve conventional .jpg/.png precedence and retain the legacy .jpeg alias.
LIGHT_ICON_NAMES = ("updater.jpg", "updater.png", "updater.jpeg")
DARK_ICON_NAMES = ("updater_dark.jpg", "updater_dark.png", "updater_dark.jpeg")
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
        filename=quote(str(filename), safe=""),
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


def _generic_release_image_assets(release, repo: str, tag: str) -> list[tuple[str, str]]:
    """Return (filename, URL) for arbitrary PNG/JPG assets, preserving API order."""
    preferred = {name.lower() for name in (*DARK_ICON_NAMES, *LIGHT_ICON_NAMES)}
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for asset in (release or {}).get("assets") or []:
        if not isinstance(asset, dict):
            continue
        filename = str(asset.get("name") or "").strip()
        lowered = filename.lower()
        if not lowered.endswith((".png", ".jpg")) or lowered in preferred:
            continue
        url = str(asset.get("browser_download_url") or "").strip()
        if not url:
            url = release_download_icon_url(repo, tag, filename)
        if url and url not in seen:
            seen.add(url)
            result.append((filename, url))
    return result


def release_asset_urls(release, package=None, *, dark: bool | None = None) -> list[str]:
    """Preferred updater names, then arbitrary release image assets. No API call."""
    dark = _dark_flag(dark)
    repo, tag = _repo_and_tag(release, package)
    if not repo or not tag or "/" not in repo:
        return []
    names = list(DARK_ICON_NAMES) + list(LIGHT_ICON_NAMES) if dark else list(LIGHT_ICON_NAMES)
    preferred_urls = [release_download_icon_url(repo, tag, filename) for filename in names]
    has_release_metadata = bool(
        release
        and (
            release.get("source_repo")
            or (release.get("html_url") or "").startswith("https://github.com/")
        )
    )
    generic_urls = (
        [url for _filename, url in _generic_release_image_assets(release, repo, tag)]
        if has_release_metadata
        else []
    )
    return _deduped(preferred_urls + generic_urls)


def release_asset_requires_square(url: str, release, package=None) -> bool:
    """Only non-conventional PNG/JPG release artwork requires square dimensions."""
    repo, tag = _repo_and_tag(release, package)
    if not repo or not tag:
        return False
    has_release_metadata = bool(
        release
        and (
            release.get("source_repo")
            or (release.get("html_url") or "").startswith("https://github.com/")
        )
    )
    if not has_release_metadata:
        return False
    return str(url) in {
        candidate for _filename, candidate in _generic_release_image_assets(release, repo, tag)
    }


def software_icon_urls(package, *, dark: bool | None = None) -> list[str]:
    """Manifest logo URLs: prefer ``icon`` over legacy ``image`` fields.

    Dark mode tries ``icon_dark`` (which may contain ``image_dark``) first.
    """
    dark = _dark_flag(dark)
    urls = []
    if dark:
        urls.append(getattr(package, "icon_dark", "") or "")
    urls.append(getattr(package, "icon", "") or "")
    urls.append(getattr(package, "image", "") or "")
    return _deduped(urls)


def icon_candidate_urls(release, package=None, *, dark: bool | None = None) -> list[str]:
    """Release artwork candidates; no GitHub API call is made here."""
    return release_asset_urls(release, package, dark=dark)


def release_image_fallback_urls(release, package=None, *, dark: bool | None = None) -> list[str]:
    """Manifest candidates used after the release's preferred and generic images."""
    if not package:
        return []
    return software_icon_urls(package, dark=dark)


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
    """First cached or downloaded image; manifest icon/image follows release art."""
    directory = cache_dir or default_icon_cache_dir()
    release_urls = icon_candidate_urls(release, package, dark=dark)
    candidates = release_urls + release_image_fallback_urls(release, package, dark=dark)
    for url in candidates:
        square_only = (
            url in release_urls
            and release_asset_requires_square(url, release, package)
        )
        data = read_cached_icon(url, directory, square_only=square_only)
        if data is None and allow_network:
            data = fetch_icon_bytes(url, square_only=square_only)
            if data:
                write_cached_icon(url, data, directory, square_only=square_only)
        if data:
            return data
    return None


def image_bytes_ok(data: bytes, *, square_only: bool = False) -> bool:
    """Validate supported image bytes, optionally requiring square dimensions."""
    if not data or len(data) < 16:
        return False
    if data.startswith(b"RIFF") and b"WEBP" not in data[:16]:
        return False
    if not data.startswith(_IMAGE_MAGIC):
        return False
    if not square_only:
        return True
    try:
        from PySide6.QtGui import QImage

        image = QImage.fromData(data)
        return not image.isNull() and image.width() == image.height()
    except Exception:
        return False


def icon_cache_file(url: str, cache_dir: Path) -> Path:
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.img"


def read_cached_icon(url: str, cache_dir: Path, *, square_only: bool = False) -> bytes | None:
    path = icon_cache_file(url, cache_dir)
    try:
        if not path.is_file() or path.stat().st_size < 16:
            return None
        data = path.read_bytes()
    except Exception:
        return None
    if not image_bytes_ok(data, square_only=square_only):
        return None
    return data


def write_cached_icon(url: str, data: bytes, cache_dir: Path, *, square_only: bool = False) -> Path | None:
    if not image_bytes_ok(data, square_only=square_only):
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


def fetch_icon_bytes(url: str, timeout: float = 8, *, square_only: bool = False) -> bytes | None:
    """Download an icon. Failures (404, offline, or invalid image) return None."""
    data, _code = fetch_icon_status(url, timeout=timeout, square_only=square_only)
    return data


def fetch_icon_status(
    url: str,
    timeout: float = 8,
    *,
    square_only: bool = False,
) -> tuple[bytes | None, int]:
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
    if not image_bytes_ok(data, square_only=square_only):
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


def load_local_package_icon_bytes(package_path: str | Path | None, *, dark: bool | None = None) -> bytes | None:
    """Inspect a local package (zip file or folder) for an embedded updater image.

    Checks updater_dark.* first when dark is True, then updater.jpg, updater.png, updater.jpeg.

    Returns the raw image bytes if found and valid, otherwise None.
    """
    if not package_path:
        return None
    try:
        p = Path(package_path)
        if not p.exists():
            return None
        dark = _dark_flag(dark)
        names = list(DARK_ICON_NAMES) + list(LIGHT_ICON_NAMES) if dark else list(LIGHT_ICON_NAMES)
        if p.is_file() and p.suffix.lower() != ".zip":
            # A scatter file: its updater image sits in the same folder.
            p = p.parent
        if p.is_dir():
            for name in names:
                cand = p / name
                if cand.is_file():
                    data = cand.read_bytes()
                    if image_bytes_ok(data):
                        return data
        elif p.is_file():
            if p.suffix.lower() == ".zip":
                import zipfile

                with zipfile.ZipFile(p, "r") as zf:
                    entry_names = {
                        Path(info.filename).name.lower(): info.filename
                        for info in zf.infolist()
                        if not info.is_dir()
                    }
                    for name in names:
                        entry = entry_names.get(name.lower())
                        if entry:
                            data = zf.read(entry)
                            if image_bytes_ok(data):
                                return data
    except Exception as exc:
        logger.debug("Could not inspect local package icon for %s: %s", package_path, exc)
    return None

