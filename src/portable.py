"""Portable flashing packages from a mirror repository.

`Portable Flashing Zip Packager` mirrors another repository's firmware
releases: every release keeps its tag, title and notes and gains one
ready-to-run zip per platform next to the original asset::

    rom.zip  ->  rom-windows.zip, rom-windows-arm64.zip,
                 rom-linux.zip, rom-linux-arm64.zip, rom-macos.zip

Each mirror commits an ``index.json`` describing every mirrored tag, with each
portable asset's ``source_asset``, ``platform``, size, checksum and download
URL. A catalogue entry opts in with ``portable_zips`` in
``slidia_manifest.xml`` -- the same attribute the website's download buttons
read -- and this module answers the one question the app has: for this release
tag, this source asset and this machine, which package should be downloaded?

Anything unresolved (no mirror, tag not mirrored yet, no build for this
platform, index unreachable) returns ``None``, and the caller keeps the
original asset. Resolution never raises.

Text links to a release's zip use the same answer through
:func:`resolve_download_link`, so a link on a release-notes page hands the
reader the portable package for their machine without the package ever being
listed as a release file.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

logger = logging.getLogger(__name__)

INDEX_SCHEMA = 1
DEFAULT_BRANCH = "main"
INDEX_TTL_SECONDS = 5 * 60
REQUEST_TIMEOUT = 10
USER_AGENT = "InnioasisUpdater/3.0 (+https://innioasis.app)"

INDEX_URL = "https://raw.githubusercontent.com/{repo}/{branch}/index.json"
DOWNLOAD_URL = "https://github.com/{repo}/releases/download/{tag}/{name}"

#: Platform key -> (os, architecture). These are the keys a mirror index
#: publishes, matching ``mirror.config.json`` in the packager.
PLATFORM_SPECS = {
    "windows": ("windows", "x86-64"),
    "windows-arm64": ("windows", "arm64"),
    "linux": ("linux", "x86-64"),
    "linux-arm64": ("linux", "arm64"),
    "macos": ("macos", "universal"),
}

#: Asset-name suffix per platform key, used when an index entry predates the
#: ``source_asset`` field and only the naming rule is available.
KNOWN_SUFFIXES = {
    "windows": "-windows.zip",
    "windows-arm64": "-windows-arm64.zip",
    "linux": "-linux.zip",
    "linux-arm64": "-linux-arm64.zip",
    "macos": "-macos.zip",
}

_ARM64_MACHINES = ("arm64", "aarch64", "armv8", "armv8l")

#: Platform keys a mirror publishes for machines that cannot run this app at
#: all (32-bit Windows, Windows 7/8, 32-bit Intel Linux). Those packs exist for
#: the website's legacy download buttons, so a host that is running the app must
#: never be resolved onto one of them.
LEGACY_PLATFORM_MARKER = "legacy"

#: macOS packages are universal only across Apple silicon and 64-bit Intel.
#: PowerPC and 32-bit Intel Macs have no build and keep the plain ROM zip.
_MACOS_64_BIT_MACHINES = ("arm64", "aarch64", "x86_64", "amd64", "x64")

#: A link resolver runs when the reader clicks, so it must not stall the
#: browser launch waiting for a mirror's index.
LINK_TIMEOUT = 3.0

#: ``repo -> (fetched_at, index)``. Per-process only: an index changes when a
#: mirror syncs, and the raw CDN trails a commit by a few minutes anyway.
_INDEX_CACHE: dict = {}


def normalize_repo(value) -> str:
    """``https://github.com/owner/repo.git`` -> ``owner/repo`` (``""`` when invalid)."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://(www\.)?github\.com/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.git$", "", text, flags=re.IGNORECASE)
    text = text.strip("/")
    if text.count("/") != 1:
        return ""
    owner, name = text.split("/")
    if not owner or not name:
        return ""
    return f"{owner}/{name}"


def parse_mirror_repos(value) -> list[str]:
    """Mirror repositories named by a manifest attribute (comma or space separated)."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        items = re.split(r"[\s,]+", str(value))
    repos: list[str] = []
    for item in items:
        repo = normalize_repo(item)
        if repo and repo not in repos:
            repos.append(repo)
    return repos


def _host_is_arm64(machine, system) -> bool:
    machine_lower = str(machine or "").lower()
    if machine_lower in _ARM64_MACHINES:
        return True
    if str(system or "").lower().startswith("win"):
        # An x86-64 build running under Prism reports AMD64 from
        # ``platform.machine()``; the OS still knows its native machine type.
        native = " ".join(
            os.environ.get(key, "")
            for key in ("PROCESSOR_ARCHITEW6432", "PROCESSOR_ARCHITECTURE")
        ).upper()
        if "ARM64" in native:
            return True
    return False


def platform_key(system=None, machine=None) -> str:
    """This machine's mirror platform key (``""`` when the mirror has none).

    macOS packages are universal across Apple silicon and 64-bit Intel, so
    those Macs report ``macos``; PowerPC and 32-bit Intel Macs have no build
    and report nothing. Windows and Linux report their architecture
    (``windows-arm64``, ``linux-arm64``).
    """
    import platform as platform_module

    system = sys.platform if system is None else system
    machine = platform_module.machine() if machine is None else machine
    lowered = str(system or "").lower()
    if lowered == "darwin":
        return "macos" if str(machine or "").lower() in _MACOS_64_BIT_MACHINES else ""
    if lowered.startswith("win"):
        os_key = "windows"
    elif lowered.startswith("linux"):
        os_key = "linux"
    else:
        return ""
    if _host_is_arm64(machine, lowered):
        return f"{os_key}-arm64"
    return os_key


def index_url(repo: str, branch: str = DEFAULT_BRANCH) -> str:
    repo = normalize_repo(repo)
    return INDEX_URL.format(
        repo=repo, branch=quote(str(branch or DEFAULT_BRANCH), safe="")
    )


def _download_url(repo: str, tag: str, name: str) -> str:
    return DOWNLOAD_URL.format(
        repo=repo, tag=quote(str(tag), safe=""), name=quote(str(name), safe="")
    )


def _fetch_text(url: str, timeout: float = REQUEST_TIMEOUT) -> str:
    from .network import requests_module

    response = requests_module().get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    return response.text


def clear_cache() -> None:
    """Forget every fetched index (tests, and a refresh after a new sync)."""
    _INDEX_CACHE.clear()


def strip_portable_suffix(name: str) -> str:
    """Strip a portable platform suffix from a zip filename if present.

    E.g. ``rom-macos.zip`` -> ``rom.zip``,
    ``Y2Linux-1.0-windows-arm64.zip`` -> ``Y2Linux-1.0.zip``.
    """
    raw = str(name or "").strip()
    lower = raw.lower()
    for suffix in sorted(KNOWN_SUFFIXES.values(), key=len, reverse=True):
        if lower.endswith(suffix) and len(raw) > len(suffix):
            return raw[: -len(suffix)] + ".zip"
    return raw


def _normalize_index_data(data, repo: str = "") -> Optional[dict]:
    """Normalize either a packager ``index.json`` (``schema: 1``, ``releases``
    list) or an ``innioasis.app`` feed mirror entry (``version: 1``,
    ``releases`` dict) into the standard ``load_index`` dictionary."""
    if not isinstance(data, dict):
        return None
    if data.get("schema") == INDEX_SCHEMA and isinstance(data.get("releases"), list):
        return data

    releases_map = data.get("releases")
    if not isinstance(releases_map, dict):
        return None

    m_repo = normalize_repo(str(data.get("mirror_repo") or repo or ""))
    platforms_seen: list[str] = []
    releases_list: list[dict] = []

    for tag_key, rel_info in releases_map.items():
        if not isinstance(rel_info, dict):
            continue
        orig_tag = str(rel_info.get("original_tag") or tag_key).strip()
        mirror_tag = str(rel_info.get("mirror_tag") or orig_tag).strip()
        if not orig_tag:
            continue
        assets_list: list[dict] = []
        mirror_originals: dict[str, dict] = {}
        assets_map = rel_info.get("assets")
        if isinstance(assets_map, dict):
            for orig_asset_name, asset_info in assets_map.items():
                if not isinstance(asset_info, dict):
                    continue
                src_name = str(orig_asset_name or "").strip()
                if not src_name:
                    continue
                mirror_orig_url = str(asset_info.get("mirror_original_url") or "").strip()
                if not mirror_orig_url and m_repo and mirror_tag:
                    mirror_orig_url = _download_url(m_repo, mirror_tag, src_name)
                mirror_originals[src_name] = {
                    "name": src_name,
                    "original_url": str(asset_info.get("original_url") or ""),
                    "size": int(asset_info.get("original_size") or 0),
                    "mirror_original_url": mirror_orig_url,
                }
                pkgs = asset_info.get("packages")
                if isinstance(pkgs, dict):
                    for plat_key, pkg_info in pkgs.items():
                        if not isinstance(pkg_info, dict):
                            continue
                        p_key = str(plat_key or "").strip()
                        p_name = str(pkg_info.get("name") or "").strip()
                        p_url = str(pkg_info.get("url") or "").strip()
                        if not p_name and p_url:
                            p_name = p_url.rsplit("/", 1)[-1]
                        if not p_url and m_repo and mirror_tag and p_name:
                            p_url = _download_url(m_repo, mirror_tag, p_name)
                        if not p_key or not p_name or not p_url:
                            continue
                        if p_key not in platforms_seen:
                            platforms_seen.append(p_key)
                        assets_list.append({
                            "name": p_name,
                            "source_asset": src_name,
                            "platform": p_key,
                            "size": int(pkg_info.get("size") or 0),
                            "sha256": str(pkg_info.get("sha256") or ""),
                            "url": p_url,
                        })
        releases_list.append({
            "tag": orig_tag,
            "mirror_tag": mirror_tag,
            "release_name": str(rel_info.get("release_name") or orig_tag),
            "published_at": str(rel_info.get("published_at") or ""),
            "assets": assets_list,
            "mirror_originals": mirror_originals,
        })

    platforms_table = []
    for p_key in platforms_seen:
        os_key, arch = PLATFORM_SPECS.get(p_key, (p_key.split("-")[0], "x86_64"))
        platforms_table.append({
            "key": p_key,
            "os": os_key,
            "arch": arch,
            "suffix": KNOWN_SUFFIXES.get(p_key, f"-{p_key}.zip"),
        })

    out = {
        "schema": INDEX_SCHEMA,
        "source_repo": str(data.get("source_repo") or ""),
        "mirror_repo": m_repo,
        "platforms": platforms_table,
        "releases": releases_list,
    }
    if isinstance(data.get("raw_releases"), list):
        out["raw_releases"] = data["raw_releases"]
    return out


def load_index(
    repo: str,
    fetch=None,
    now: Optional[float] = None,
    branch: str = DEFAULT_BRANCH,
    force_refresh: bool = False,
):
    """The mirror's ``index.json``, or ``None`` when it cannot be used.

    A failed fetch falls back to ``innioasis.app/feeds/firmware-releases.json``
    (or ``firmware-feed.xml``) and then keeps serving the last good copy, so a
    flaky connection does not silently downgrade users on every lookup.
    """
    repo = normalize_repo(repo)
    if not repo:
        return None
    current = time.time() if now is None else now
    cached = _INDEX_CACHE.get(repo)
    if not force_refresh and cached and current - cached[0] < INDEX_TTL_SECONDS:
        return cached[1]

    fetcher = fetch or _fetch_text
    stale = cached[1] if cached else None
    raw_data = None
    try:
        raw_data = json.loads(fetcher(index_url(repo, branch)))
    except Exception as error:
        logger.debug("Portable mirror index unavailable for %s: %s", repo, error)

    data = _normalize_index_data(raw_data, repo) if raw_data is not None else None

    if data is None and fetch is None:
        try:
            from . import feed_fallback

            feed_mirror = feed_fallback.get_mirror_from_feed(repo, force_refresh=force_refresh)
            if feed_mirror is not None:
                data = _normalize_index_data(feed_mirror, repo)
        except Exception as e:
            logger.debug("Fallback feed mirror index lookup failed for %s: %s", repo, e)

    if data is None:
        return stale

    _INDEX_CACHE[repo] = (current, data)
    return data


def fetch_mirror_index(
    repo: str,
    *,
    force_refresh: bool = False,
    fetch=None,
    now: Optional[float] = None,
    branch: str = DEFAULT_BRANCH,
):
    """Fetch a mirror repository's index, falling back to the ``innioasis.app``
    static feed when ``raw.githubusercontent.com`` or GitHub API is unreachable."""
    return load_index(repo, fetch=fetch, now=now, branch=branch, force_refresh=force_refresh)


def platform_candidates(index, platform: Optional[str] = None) -> list[str]:
    """Platform keys to try for this machine, best first.

    Mirrors publish one entry per platform they built. A machine only accepts
    its own architecture or a universal one: an x86-64 host never gets an
    arm64 bundle, and an arm64 host never gets the x86-64 build.
    """
    key = platform or platform_key()
    spec = PLATFORM_SPECS.get(key)
    if spec is None:
        return []
    os_key, arch = spec
    ordered: list[tuple[int, int, str]] = []
    for position, entry in enumerate(index.get("platforms") or []):
        if not isinstance(entry, dict):
            continue
        entry_key = str(entry.get("key") or "")
        if not entry_key:
            continue
        if LEGACY_PLATFORM_MARKER in entry_key.lower():
            continue
        if str(entry.get("os") or "") != os_key:
            continue
        entry_arch = str(entry.get("arch") or "")
        if not (entry_arch == arch or entry_arch == "universal" or arch == "universal"):
            continue
        ordered.append((0 if entry_arch == arch else 1, position, entry_key))
    if not ordered:
        # An index without a platforms table can still be matched by key.
        return [key]
    return [item[2] for item in sorted(ordered)]


def _platform_suffix(index, key: str) -> str:
    for entry in index.get("platforms") or []:
        if isinstance(entry, dict) and str(entry.get("key") or "") == key:
            suffix = str(entry.get("suffix") or "")
            if suffix:
                return suffix
    return KNOWN_SUFFIXES.get(key, "")


def find_asset(index, repo: str, tag: str, source_asset: str, platform: Optional[str] = None):
    """The mirror asset built from ``source_asset`` at ``tag`` for this machine."""
    if not isinstance(index, dict) or not tag or not source_asset:
        return None
    release = None
    for candidate in index.get("releases") or []:
        if isinstance(candidate, dict) and str(candidate.get("tag") or "") == str(tag):
            release = candidate
            break
    if release is None:
        return None

    assets = [a for a in (release.get("assets") or []) if isinstance(a, dict)]
    source_name = str(source_asset)
    base = source_name[:-4] if source_name.lower().endswith(".zip") else source_name
    for key in platform_candidates(index, platform):
        expected = base + _platform_suffix(index, key)
        for asset in assets:
            if str(asset.get("platform") or "") != key:
                continue
            name = str(asset.get("name") or "")
            if not name:
                continue
            declared = str(asset.get("source_asset") or "")
            if declared:
                if declared != source_name:
                    continue
            elif expected == base or name != expected:
                continue
            return {
                "repo": repo,
                "platform": key,
                "name": name,
                "size": int(asset.get("size") or 0),
                "sha256": str(asset.get("sha256") or ""),
                "url": str(asset.get("url") or "") or _download_url(repo, tag, name),
            }
    return None


def resolve(mirrors, tag: str, source_asset: str, platform: Optional[str] = None, fetch=None):
    """Resolve one release asset to its portable package (``None`` when absent).

    ``mirrors`` is the manifest attribute (string, comma separated) or a list;
    the first mirror that has the tag and a build for this platform wins.
    """
    repos = parse_mirror_repos(mirrors)
    key = platform or platform_key()
    if not repos or not tag or not source_asset or not key:
        return None
    for repo in repos:
        index = load_index(repo, fetch=fetch)
        if not index:
            continue
        match = find_asset(index, repo, str(tag), str(source_asset), key)
        if match:
            return match
    return None


resolve_portable_asset = resolve


def resolve_mirror_candidates(
    mirrors,
    tag: str,
    source_asset: str,
    platform: Optional[str] = None,
    fetch=None,
) -> Optional[dict]:
    """Resolve all mirror download options for ``(tag, source_asset)``:

    - ``exact_portable``: portable zip built for the current host ``platform`` (or ``None``)
    - ``any_portable``: ``exact_portable`` if available, else the first available OS portable zip
      (since any OS portable zip contains the same ``Firmware/`` or ``SP_Flash_Tool/`` files)
    - ``all_portable_urls``: ordered list of all portable package URLs across all OSes
    - ``mirror_original_url``: unmodified original zip URL on the mirror release
    - ``mirror_repo``: mirror repository name
    """
    repos = parse_mirror_repos(mirrors)
    if not repos or not tag or not source_asset:
        return None
    key = platform or platform_key()
    source_name = str(source_asset).strip()
    base = source_name[:-4] if source_name.lower().endswith(".zip") else source_name

    for repo in repos:
        index = load_index(repo, fetch=fetch)
        if not index:
            continue
        release = None
        for candidate in index.get("releases") or []:
            if isinstance(candidate, dict) and (
                str(candidate.get("tag") or "") == str(tag)
                or str(candidate.get("mirror_tag") or "") == str(tag)
            ):
                release = candidate
                break
        if release is None:
            continue

        mirror_tag = str(release.get("mirror_tag") or release.get("tag") or tag)
        mirror_originals = release.get("mirror_originals")
        mirror_original_url = ""
        if isinstance(mirror_originals, dict):
            orig_info = mirror_originals.get(source_name)
            if orig_info is None:
                for k, v in mirror_originals.items():
                    if str(k).lower() == source_name.lower():
                        orig_info = v
                        break
            if isinstance(orig_info, dict):
                mirror_original_url = str(orig_info.get("mirror_original_url") or "").strip()
        if not mirror_original_url:
            mirror_original_url = _download_url(repo, mirror_tag, source_name)

        exact = find_asset(index, repo, str(tag), source_name, key) if key else None
        all_assets: list[dict] = []
        for asset in release.get("assets") or []:
            if not isinstance(asset, dict):
                continue
            a_name = str(asset.get("name") or "").strip()
            a_plat = str(asset.get("platform") or "").strip()
            if not a_name or not a_plat:
                continue
            declared = str(asset.get("source_asset") or "").strip()
            if declared:
                if declared != source_name and declared.lower() != source_name.lower():
                    continue
            else:
                expected = base + _platform_suffix(index, a_plat)
                if expected == base or a_name != expected:
                    continue
            a_url = str(asset.get("url") or "").strip() or _download_url(repo, mirror_tag, a_name)
            all_assets.append({
                "repo": repo,
                "platform": a_plat,
                "name": a_name,
                "size": int(asset.get("size") or 0),
                "sha256": str(asset.get("sha256") or ""),
                "url": a_url,
            })

        any_portable = exact or (all_assets[0] if all_assets else None)
        all_urls: list[str] = []
        if exact and exact.get("url"):
            all_urls.append(exact["url"])
        for a in all_assets:
            if a["url"] and a["url"] not in all_urls:
                all_urls.append(a["url"])

        return {
            "mirror_repo": repo,
            "mirror_tag": mirror_tag,
            "mirror_original_url": mirror_original_url,
            "exact_portable": exact,
            "any_portable": any_portable,
            "all_portable_urls": all_urls,
        }
    return None


def mirrors_for_repo(repo: str) -> list[str]:
    """Mirror repositories a catalogue entry for ``repo`` names.

    A text link only carries the source repository, so the catalogue is the
    map from it to the mirrors configured in ``slidia_manifest.xml``. The
    parsed cache covers normal launches; the live/static catalogue covers a
    first run that has not written it yet.
    """
    source = normalize_repo(repo).lower()
    if not source:
        return []
    entries: list = []
    try:
        from . import manifest

        entries.extend(manifest.load_cached_manifest() or [])
    except Exception:
        logger.debug("Could not read the manifest for portable mirrors", exc_info=True)
    try:
        from . import catalog

        entries.extend(catalog._catalog_packages())
    except Exception:
        logger.debug("Could not read the catalogue for portable mirrors", exc_info=True)

    mirrors: list[str] = []
    for entry in entries:
        try:
            entry_repo = normalize_repo(getattr(entry, "repo", "")).lower()
        except Exception:
            continue
        if entry_repo != source:
            continue
        for mirror in parse_mirror_repos(getattr(entry, "portable_zips", "")):
            if mirror not in mirrors:
                mirrors.append(mirror)
    return mirrors


def _release_asset_from_url(url: str) -> tuple[str, str, str]:
    """``(repo, tag, asset)`` for a GitHub release download link, else empty."""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return "", "", ""
    if (parts.hostname or "").lower() not in ("github.com", "www.github.com"):
        return "", "", ""
    segments = [unquote(segment) for segment in parts.path.split("/") if segment]
    if (
        len(segments) < 6
        or segments[2].lower() != "releases"
        or segments[3].lower() != "download"
    ):
        return "", "", ""
    repo = normalize_repo(f"{segments[0]}/{segments[1]}")
    tag = segments[4].strip()
    asset = segments[5].strip()
    if not repo or not tag or not asset:
        return "", "", ""
    return repo, tag, asset


def _is_portable_asset(name: str) -> bool:
    lower = str(name or "").lower()
    return any(lower.endswith(suffix) for suffix in KNOWN_SUFFIXES.values())


def resolve_download_link(url: str, platform: Optional[str] = None, fetch=None) -> str:
    """Portable package URL for a text link to a ROM zip (``""`` when none).

    Release-notes pages and other in-app text link at the original
    ``rom_type_b.zip``. Where the mirror has a build for this machine, the
    click hands the reader ``rom_type_b-macos.zip`` instead. Mirror packages
    stay a background detail: no release list ever shows them. A PowerPC or
    32-bit Intel Mac has no macOS build and keeps the plain zip.
    """
    repo, tag, asset = _release_asset_from_url(url)
    if not repo or not asset.lower().endswith(".zip") or _is_portable_asset(asset):
        return ""
    key = platform_key() if platform is None else platform
    if not key:
        return ""
    mirrors = mirrors_for_repo(repo)
    if not mirrors:
        return ""
    fetcher = fetch or (lambda index_url: _fetch_text(index_url, LINK_TIMEOUT))
    match = resolve(mirrors, tag, asset, platform=key, fetch=fetcher)
    return str(match.get("url") or "") if match else ""


def fallback_urls_for(url: str, platform: Optional[str] = None, fetch=None) -> list[str]:
    """Mirror download URLs to try after a source release download fails.

    A publisher can delete a repository, or a network can block it, long after
    ``slidia_manifest.xml`` named it. The mirror keeps the same release tag and
    the same original zip, so a failed download continues from the mirror
    instead of ending the install. Order: the portable package built for this
    machine, any other portable package (they all carry the same
    ``Firmware/`` or ``SP_Flash_Tool/`` layout), then the mirror's unmodified
    copy of the original asset.

    Never raises; an unresolvable link returns ``[]`` and the caller keeps the
    original URL and its error.
    """
    try:
        repo, tag, asset = _release_asset_from_url(url)
        if not repo or not tag or not asset:
            return []
        mirrors = mirrors_for_repo(repo)
        if not mirrors:
            return []
        key = platform_key() if platform is None else platform
        candidates = resolve_mirror_candidates(mirrors, tag, asset, platform=key, fetch=fetch)
        if not candidates:
            return []
        ordered: list[str] = []
        exact = candidates.get("exact_portable") or {}
        for candidate in (
            str(exact.get("url") or ""),
            str((candidates.get("any_portable") or {}).get("url") or ""),
            str(candidates.get("mirror_original_url") or ""),
            *(candidates.get("all_portable_urls") or []),
        ):
            candidate = str(candidate or "").strip()
            if candidate and candidate != url and candidate not in ordered:
                ordered.append(candidate)
        return ordered
    except Exception:
        logger.debug("Could not resolve mirror fallbacks for %s", url, exc_info=True)
        return []
