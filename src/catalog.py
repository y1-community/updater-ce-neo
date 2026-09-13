"""Online firmware catalogue + GitHub releases client.

Ported from Updater CE's ``firmware_downloader.py`` (``GithubApi`` class,
``resolve_firmware_repo``, ``_parse_rom_asset_variant``,
``select_preferred_rom_asset``) and the website's ``firmware-catalog.js`` /
``slidia_manifest.xml``.

Design notes kept from the original:
- Persistent 24 h disk cache so listings work offline and GitHub rate limits
  are not exhausted.
- ``rom*.zip`` asset classification: hardware **Type A/B**, **resolution**
  (360p / 240p / native), **model family** (Y1 / Y2 / dual).
- Unauthenticated requests with an optional ``GITHUB_TOKEN`` env var
  (the original ships PATs in ``config.ini``; this port intentionally does not).
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path

import requests

from .config import (
    GITHUB_API,
    GITHUB_RELEASES_PER_PAGE,
    RELEASE_CACHE_TTL_SECONDS,
    UPDATE_CACHE_TTL_SECONDS,
    resolve_firmware_repo,
)

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 12


# ---------------------------------------------------------------------------
# Catalogue (static fallback — matches firmware-catalog.js / slidia_manifest.xml)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FirmwarePackage:
    slug: str
    name: str
    model: str
    repo: str
    package_name: str
    description: str = ""


# Live manifest entries (see ``manifest.py``) replace the static table once
# fetched; the static table remains the offline first-run fallback.
LIVE_CATALOG: list = []


def set_live_catalog(entries: list):
    """Install firmware entries parsed from the live ``slidia_manifest.xml``.
    They override the static table for every (model, software) they cover."""
    global LIVE_CATALOG
    LIVE_CATALOG = [e for e in entries if e]


def _catalog_packages():
    if LIVE_CATALOG:
        return list(LIVE_CATALOG)
    return list(CATALOG)


CATALOG = [
    FirmwarePackage("original-y1", "Original Software", "Y1", "y1-community/y1-stock-rom", "rom.zip",
                    "The stock software for restoring an Innioasis Y1."),
    FirmwarePackage("original-y2", "Original Software", "Y2", "y1-community/y1-stock-rom", "rom_y2.zip",
                    "The stock software for restoring an Innioasis Y2."),
    FirmwarePackage("rockbox-y1", "Rockbox", "Y1", "rockbox-y1/rockbox", "rom.zip",
                    "A music-first custom software with deep playback and theme options."),
    FirmwarePackage("rockbox-y2", "Rockbox", "Y2", "y1-community/rockbox-y2-rom", "rom_y2.zip",
                    "The Y2 Rockbox build."),
    FirmwarePackage("solar-y1", "Solar", "Y1", "thesolarproject/solar", "rom.zip",
                    "A Y1 custom software with network features, including YouTube support."),
    FirmwarePackage("solar-y2", "Solar", "Y2", "thesolarproject/solar", "rom_y2.zip",
                    "Listed for Y2 in the current Updater CE catalogue."),
    FirmwarePackage("jj-launcher-y1", "JJ Launcher", "Y1", "ismileblue/y1_launcher", "rom.zip",
                    "A launcher software for Y1."),
    FirmwarePackage("jj-launcher-y2", "JJ Launcher", "Y2", "ismileblue/y1_launcher", "rom_y2.zip",
                    "Listed for Y2 in the current Updater CE catalogue."),
    FirmwarePackage("inniclassic-y1", "Inniclassic", "Y1", "FabianZettl/inniclassic", "rom.zip",
                    "A classic-style Y1 software."),
    FirmwarePackage("y2player-y2", "Y2Player", "Y2", "schulzcode/Y2Player", "rom_y2.zip",
                    "A Y2 custom software."),
    FirmwarePackage("koensayr-y1", "Koensayr", "Y1", "ryan-specter/koensayr-auto", "rom.zip",
                    "A Y1 custom software maintained by the community."),
]


def packages_for_model(model: str):
    return [p for p in _catalog_packages() if p.model == model]


def software_names_for_model(model: str):
    seen = []
    for p in packages_for_model(model):
        if p.name not in seen:
            seen.append(p.name)
    return seen


def packages_for_model_software(model: str, software: str):
    return [p for p in packages_for_model(model) if p.name == software]


# ---------------------------------------------------------------------------
# rom*.zip asset variant parsing (ported from firmware_downloader.py)
# ---------------------------------------------------------------------------
def _classify_variant_model(name, tag_name="", repo=""):
    """Classify a rom*.zip asset as Y1, Y2, or dual (eligible for both)."""
    lower = (name or "").lower()
    tag_lower = (tag_name or "").lower()
    repo_lower = (repo or "").lower()
    repo_name = repo_lower.split("/")[-1] if "/" in repo_lower else repo_lower

    if "_y2" in lower or lower.startswith("rom_y2"):
        return "Y2"
    if any(token in lower for token in ("6582", "eastaeon", "mt6582")):
        return "Y2"
    if any(token in tag_lower for token in ("6582", "eastaeon", "mt6582")):
        return "Y2"
    if "y2" in tag_lower and "y1" not in tag_lower:
        return "Y2"
    if "y2" in repo_name and "y1" not in repo_name.replace("y2", "", 1):
        return "Y2"
    return "dual"


def _parse_rom_asset_variant(asset, tag_name="", repo=""):
    """Classify a rom*.zip asset into hardware type (A/B), resolution, model."""
    try:
        name = asset.get("name", "")
        lower = name.lower()
        if not fnmatch(lower, "rom*.zip"):
            return None
        tag_lower = (tag_name or "").lower()
        is_type_b = "_type_b" in lower or "type-b" in lower or " type b" in lower
        if not is_type_b and "type-b" in tag_lower:
            is_type_b = True
        hw_type = "B" if is_type_b else "A"
        if "_360p" in lower:
            resolution = "360p"
        elif "_240p" in lower:
            resolution = "240p"
        else:
            resolution = "native"
        return {
            "asset": asset,
            "type": hw_type,
            "resolution": resolution,
            "model": _classify_variant_model(name, tag_name, repo),
        }
    except Exception:
        return None


def _resolution_priority(resolution):
    """Priority for choosing a default ROM: 360p (native Y1) > native > 240p."""
    if resolution == "360p":
        return 0
    if resolution == "native":
        return 1
    if resolution == "240p":
        return 2
    return 3


def _model_priority(variant_model, model):
    """Best-matching variant for the selected device model."""
    if variant_model == model:
        return 0
    if variant_model == "dual":
        return 1
    return 2


def select_preferred_rom_asset(variants, selected_type=None, model=None):
    """Pick the default rom*.zip asset, honoring type/model/resolution order.

    When ``model`` is given, only variants whose ``model`` field matches
    (exact or ``dual``) are considered.  This prevents picking a Y1 ROM
    when the user selected Y2, or vice-versa.  Within the model-matched
    set, hardware type (if ``selected_type``) and resolution priority
    determine the winner.
    """
    if not variants:
        return None
    # First narrow by model so we never hand a Y1 ROM to a Y2 user.
    if model:
        model_matched = [
            v for v in variants
            if v.get("model") == model or v.get("model") == "dual"
        ]
        if model_matched:
            variants = model_matched
    if selected_type:
        variants = [v for v in variants if v.get("type") == selected_type] or variants
    # Sort: resolution first (lower is better), then model (exact > dual).
    # Model is the *last* key so it acts as a tiebreaker — a rom_y2.zip
    # always beats a dual rom.zip even if the latter has a nicer resolution.
    variants = sorted(variants, key=lambda v: _resolution_priority(v.get("resolution")))
    if model:
        variants = sorted(variants, key=lambda v: _model_priority(v.get("model"), model))
    return variants[0]


def _model_ok_for_package(variant_model, package_model, repo, model):
    """Whether a rom*.zip variant model fits the selected device ``model``
    given the catalogue package's target ``package_model``.

    Ported from firmware_downloader.py ``filter_rom_variants_for_model``.
    The crucial rule this captures: a *dual* rom.zip from a repo whose name
    has no Y2 marker (e.g. ``y1-stock-rom``) is a Y1-only legacy asset and
    must never appear for a Y2 selection.
    """
    variant_model = (variant_model or "dual").upper()
    ed_model = str(model or "").upper()
    pd = str(package_model or "").upper()
    selecting_y2 = "Y2" in ed_model
    repo_name = (repo or "").lower().split("/")[-1]

    if selecting_y2:
        if variant_model == "Y2":
            return True
        if pd == "Y1":
            return False
        if variant_model == "DUAL":
            # Legacy rom.zip on a Y1-named repo (no y2 marker) is not for a Y2 device.
            if pd == "Y2" and "y2" not in repo_name:
                return False
            return True
        return False

    # Y1 / generic selection
    if pd == "Y2":
        return False
    if variant_model == "Y2":
        return False
    return variant_model in ("Y1", "DUAL")


def filter_rom_variants_for_model(variants, model, package_model=None, repo=""):
    """Return rom*.zip variants usable by ``model`` for a catalogue package.

    Matches the upstream ``filter_rom_variants_for_model`` behaviour: explicit
    model variants (e.g. rom_y2.zip) always win out, and dual rom.zip assets
    are gated on the package/repo context so Y2 never inherits Y1-only legacy
    releases.
    """
    if not variants:
        return []
    enriched = []
    for v in variants:
        vm = (v.get("model") or "dual").upper()
        if vm not in ("Y1", "Y2", "DUAL"):
            continue
        enriched.append(v)
    selecting_y2 = "Y2" in (model or "").upper()
    kept = [
        v for v in enriched
        if _model_ok_for_package(v.get("model"), package_model, repo, model)
    ]
    if not kept:
        return []
    # When Y2 is selected, prefer explicit Y2 variants over dual rom.zip;
    # when Y1 is selected, exclude anything Y2-specific.
    if selecting_y2:
        y2_explicit = [v for v in kept if (v.get("model") or "").upper() == "Y2"]
        if y2_explicit:
            return y2_explicit
    else:
        non_y2 = [v for v in kept if (v.get("model") or "").upper() != "Y2"]
        if non_y2:
            return non_y2
    return kept


def _release_matches_model(model, release, package=None, selected_type=None):
    """True if the release carries a rom*.zip variant usable by ``model``.

    When ``selected_type`` (e.g. 'A' or 'B') is given, the release must
    contain at least one variant matching that hardware type.
    """
    variants = release.get("rom_variants") or []
    package_model = package.model if package is not None else None
    repo = release.get("source_repo", "") or ""
    filtered = filter_rom_variants_for_model(variants, model, package_model, repo)
    if selected_type:
        filtered = [v for v in filtered if v.get("type") == selected_type]
    return bool(filtered)


# ---------------------------------------------------------------------------
# Version Parsing, Display Formatting & Chronological Sorting
# (Ported from firmware_downloader.py)
# ---------------------------------------------------------------------------
def _extract_tag_timestamp(tag_name: str) -> int:
    """Extract a YYYYMMDD-HHMM timestamp from a tag name and return it as a sortable int.

    Returns 0 when no such timestamp is found.
    """
    m = re.search(r'(\d{8})-(\d{4})\b', tag_name or "")
    if m:
        try:
            return int(m.group(1)) * 10000 + int(m.group(2))
        except ValueError:
            pass
    return 0


def _parse_semver(version: str):
    """Convert a dotted numeric version string into a tuple of ints. Return None if invalid."""
    if version is None:
        return None
    version_str = str(version).strip()
    if not version_str or not re.fullmatch(r'\d+(?:\.\d+)*', version_str):
        return None
    try:
        return tuple(int(part) for part in version_str.split('.'))
    except ValueError:
        return None


def parse_version_designations(version_name: str) -> dict:
    """Parse version names and extract designations with flexible adjective handling."""
    designations = []
    adjectives = ['compatible', 'aware', 'supported', 'enabled', 'disabled', 'ready', 'optimized', 'enhanced']
    excluded_parts = ['type', 'b', 'base', 'stable']

    clean_version = re.sub(r'-[a-f0-9]{16,}!?$', '', version_name or '')
    clean_version = clean_version.rstrip('-!')
    extracted_version = None

    timestamp_match = re.search(r'(\d{8})-(\d{4})\b', clean_version)
    if timestamp_match:
        extracted_version = f"{timestamp_match.group(1)}-{timestamp_match.group(2)}"

    if not extracted_version:
        version_pattern = re.search(r'\bv([\d.]+)\b', clean_version, re.IGNORECASE)
        if version_pattern:
            extracted_version = version_pattern.group(1)

    if not extracted_version and '-' in clean_version:
        # Check if any dash-delimited segment looks like a dotted version (e.g. "3.2.0-fm", "type-b-1.7.6")
        for part in clean_version.split('-'):
            part_clean = part.lstrip('vV')
            if re.match(r'^\d+(?:\.\d+)+$', part_clean):
                extracted_version = part_clean
                break
        if not extracted_version:
            last_part = clean_version.split('-')[-1]
            if last_part.lower().startswith('v'):
                last_part = last_part[1:]
            if re.match(r'^[\d.]+$', last_part):
                extracted_version = last_part

    if extracted_version:
        clean_version = extracted_version

    parts = (version_name or '').split('-')
    for i, part in enumerate(parts):
        part_to_check = part[1:] if part.lower().startswith('v') and len(part) > 1 else part
        if re.match(r'^[\d.]+$', part_to_check):
            continue
        if re.match(r'^[a-f0-9]{16,}!?$', part):
            continue
        if part.lower() in excluded_parts:
            continue
        if part == 'nightly':
            continue
        elif part == '360p':
            designations.append('360p / Y1 Theme Compatible')
        elif part in ('wifi', 'wi-fi'):
            designations.append('Wi-Fi')
        elif part == 'rockbox':
            designations.append('with Rockbox')
        elif part.lower() in ('adb', 'usb', 'ethernet', 'hdmi', 'audio', 'video', 'camera', 'gps', 'nfc', 'lte', '5g', 'fm'):
            designations.append(part.upper() if len(part) <= 4 else part.title())
        elif part == 'ipod' and i + 1 < len(parts) and parts[i + 1] == 'theme':
            if i + 2 < len(parts) and parts[i + 2] in adjectives:
                adjective = parts[i + 2]
                designations.append(f'iPod Classic/Video Rockbox Theme {adjective.title()}')
            else:
                designations.append('iPod Classic/Video (240p) Rockbox Themes')
        elif part == 'theme' and i > 0 and parts[i - 1] == 'ipod':
            continue
        else:
            if i + 1 < len(parts) and parts[i + 1] in adjectives:
                adjective = parts[i + 1]
                main_part = part.replace('-', ' ').title()
                designations.append(f'{main_part} {adjective.title()}')
            else:
                if part not in adjectives:
                    designations.append(part.replace('-', ' ').title())

    return {
        'clean_version': clean_version.strip(),
        'designations': designations,
    }


def format_datestamp_version(match_or_str, now_dt=None):
    """Format a YYYYMMDD-HHMM datestamp as a human-readable date with time."""
    val = match_or_str.group(0) if hasattr(match_or_str, "group") else str(match_or_str)
    m = re.search(r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})', val)
    if not m:
        return val

    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    hour, minute = int(m.group(4)), int(m.group(5))
    try:
        release_dt = datetime(year, month, day, hour, minute)
    except ValueError:
        return val

    time_str = f"{hour:02d}:{minute:02d}"
    now = now_dt or datetime.now()
    today = now.date()
    yesterday = today - timedelta(days=1)
    release_date = release_dt.date()

    if release_date == today:
        return f"Today at {time_str}"
    elif release_date == yesterday:
        return f"Yesterday at {time_str}"
    elif 0 < (today - release_date).days <= 7:
        day_name = release_dt.strftime('%A')
        return f"{day_name} at {time_str}"
    elif release_date.year == today.year:
        return release_dt.strftime(f'%b %d at {time_str}')
    else:
        return release_dt.strftime(f'%b %d, %Y at {time_str}')


def format_fancy_date(date_obj, now_dt=None):
    """Format date in a simplified way without time."""
    now = now_dt or (datetime.now(date_obj.tzinfo) if date_obj.tzinfo else datetime.now())
    today = now.date()
    yesterday = today - timedelta(days=1)
    date_only = date_obj.date()

    if date_only == today:
        return "Today"
    elif date_only == yesterday:
        return "Yesterday"
    elif (today - date_only).days <= 7:
        return date_obj.strftime('%A')
    elif (today - date_only).days <= 30:
        return date_obj.strftime('%b %d')
    else:
        return date_obj.strftime('%b %Y')


def get_display_version(version_info, published_date="", is_prerelease=False, now_dt=None):
    """Get the display version - either version number or published date based on length or prerelease status."""
    version_text = (version_info or {}).get('clean_version', '')
    is_nightly = 'nightly' in version_text.lower()

    datestamp_match = re.match(r'^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})$', version_text)
    if datestamp_match:
        return format_datestamp_version(datestamp_match, now_dt=now_dt)

    if len(version_text) > 8 or is_prerelease or is_nightly:
        if published_date:
            try:
                date_obj = datetime.fromisoformat(published_date.replace('Z', '+00:00'))
                return f"Released: {format_fancy_date(date_obj, now_dt=now_dt)}"
            except Exception:
                return f"Released: {published_date}"
        return "Unknown Date"

    return version_text


def release_sort_key(release):
    """Build a sortable 5-tier key so newer releases appear first in reverse sort."""
    tag_name = (release or {}).get("tag_name", "")
    version_info = parse_version_designations(tag_name)
    clean_version = str(version_info.get("clean_version", "")).lstrip("vV")

    tag_timestamp = _extract_tag_timestamp(tag_name)
    numeric_version = None
    if not tag_timestamp:
        numeric_version = _parse_semver(clean_version)

    published_at = (release or {}).get("published_at", "")
    try:
        published_timestamp = datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp()
    except Exception:
        published_timestamp = 0.0

    return (
        1 if (tag_timestamp or numeric_version is not None) else 0,
        tag_timestamp,
        numeric_version or tuple(),
        published_timestamp,
        str(tag_name).lower(),
    )


def format_release_display_label(rel):
    """Format release display label adhering to legacy firmware_downloader.py rules."""
    tag_name = (rel.get("tag_name") or "").strip()
    name = (rel.get("name") or "").strip()
    version_info = parse_version_designations(tag_name)
    published_date = rel.get("published_at", "")
    is_prerelease = bool(rel.get("prerelease"))
    display_version = get_display_version(version_info, published_date, is_prerelease)

    designations = version_info.get("designations") or []
    if name and name != tag_name:
        designations = [d for d in designations if d.lower() not in name.lower()]
    designation_suffix = f" ({' | '.join(designations)})" if designations else ""

    if name and name != tag_name:
        label = f"{name}{designation_suffix}"
    elif display_version and display_version != tag_name and not tag_name.startswith("v"):
        label = f"{display_version}{designation_suffix}"
    else:
        label = f"{tag_name}{designation_suffix}"

    if is_prerelease and "[preview]" not in label.lower():
        label += "  [preview]"
    return label


# ---------------------------------------------------------------------------
# Releases client
# ---------------------------------------------------------------------------
class ReleasesClient:
    """GitHub releases fetcher with persistent cache + rate-limit awareness.

    Only the ``GITHUB_TOKEN`` env var is honored (no embedded PATs). Unauthenticated
    requests are rate-limited to ~55 calls/hour to stay under GitHub's 60/h cap.
    """

    def __init__(self, cache_root=None):
        if cache_root is None:
            cache_root = _app_cache_dir()
        self.cache_dir = Path(cache_root) / "releases"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.token = os.environ.get("GITHUB_TOKEN") or ""
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/vnd.github+json"})
        self.unauth_calls = []
        self.unauth_hourly_limit = 55

    # -- cache --------------------------------------------------------------
    def _cache_path(self, repo):
        return self.cache_dir / f"{repo.replace('/', '_')}.json"

    def get_cached_releases(self, repo, ignore_ttl=False):
        path = self._cache_path(repo)
        try:
            if path.exists() and (ignore_ttl or (time.time() - path.stat().st_mtime) < RELEASE_CACHE_TTL_SECONDS):
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.debug("Cache read failed for %s: %s", repo, e)
        return None

    def cache_releases(self, repo, releases):
        try:
            self._cache_path(repo).write_text(
                json.dumps(releases, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.debug("Cache write failed for %s: %s", repo, e)

    # -- rate limiting ------------------------------------------------------
    def _can_unauth(self):
        cutoff = time.time() - 3600
        self.unauth_calls = [t for t in self.unauth_calls if t > cutoff]
        return len(self.unauth_calls) < self.unauth_hourly_limit

    def _record_unauth(self):
        self.unauth_calls.append(time.time())

    def _get_json(self, url):
        headers = {}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        last_err = None
        for attempt in range(3):
            try:
                resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (403, 429):
                    logger.info("GitHub rate limited (%s); sleeping", resp.status_code)
                    time.sleep(2 * (attempt + 1))
                    continue
                last_err = resp.status_code
                break
            except requests.RequestException as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        logger.debug("GitHub request failed for %s: %s", url, last_err)
        return None

    # -- fetching -----------------------------------------------------------
    def get_latest_release(self, repo, force_refresh=False):
        repo = resolve_firmware_repo(repo)
        if not force_refresh:
            cached = self.get_cached_releases(repo)
            if cached:
                return cached[0]
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        if self.token:
            data = self._get_json(url)
            if data:
                result = self._normalize_release(data, repo)
                if result:
                    self.cache_releases(repo, [result])
                return result
        if not self._can_unauth():
            cached = self.get_cached_releases(repo, ignore_ttl=True)
            return cached[0] if cached else None
        self._record_unauth()
        data = self._get_json(url)
        if not data:
            cached = self.get_cached_releases(repo, ignore_ttl=True)
            return cached[0] if cached else None
        result = self._normalize_release(data, repo)
        if result:
            self.cache_releases(repo, [result])
        return result

    def get_all_releases(self, repo, force_refresh=False):
        repo = resolve_firmware_repo(repo)
        if not force_refresh:
            cached = self.get_cached_releases(repo)
            if cached:
                logger.info("Using %d cached releases for %s", len(cached), repo)
                return sorted(cached, key=release_sort_key, reverse=True)[:100]

        url = f"{GITHUB_API}/repos/{repo}/releases?per_page={GITHUB_RELEASES_PER_PAGE}"
        if self.token:
            data = self._get_json(url)
            if data is not None:
                releases = self._build_releases(data, repo)
                if releases:
                    releases = sorted(releases, key=release_sort_key, reverse=True)
                    self.cache_releases(repo, releases)
                return releases

        if not self._can_unauth():
            logger.warning("GitHub unauthenticated rate limit reached for %s; using cache if available", repo)
            cached = self.get_cached_releases(repo, ignore_ttl=True)
            return sorted(cached, key=release_sort_key, reverse=True) if cached else []
        self._record_unauth()
        data = self._get_json(url)
        if data is None:
            cached = self.get_cached_releases(repo, ignore_ttl=True)
            return sorted(cached, key=release_sort_key, reverse=True) if cached else []
        releases = self._build_releases(data, repo)
        if releases:
            releases = sorted(releases, key=release_sort_key, reverse=True)
            self.cache_releases(repo, releases)
        return releases

    # -- app-update checking --------------------------------------------------
    def _updates_cache_path(self, repo):
        d = self.cache_dir.parent / "updates"
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{repo.replace('/', '_')}.json"

    def get_cached_update(self, repo):
        """Cached ``/releases/latest`` payload (raw, no rom*.zip filter)."""
        path = self._updates_cache_path(repo)
        try:
            if path.exists() and (time.time() - path.stat().st_mtime) < UPDATE_CACHE_TTL_SECONDS:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.debug("Update cache read failed for %s: %s", repo, e)
        return None

    def get_latest_release_info(self, repo):
        """Latest release of an app repo (not a firmware repo) as a plain dict:
        tag_name, name, body, published_at, html_url, assets[]. Falls back to
        the cache and never raises; returns None when nothing is available."""
        repo = resolve_firmware_repo(repo)
        cached = self.get_cached_update(repo)
        if cached:
            return cached

        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        data = None
        if self.token:
            data = self._get_json(url)
        elif self._can_unauth():
            self._record_unauth()
            data = self._get_json(url)
        if not data:
            return cached
        result = {
            "tag_name": data.get("tag_name", ""),
            "name": data.get("name", ""),
            "body": data.get("body", ""),
            "published_at": data.get("published_at", ""),
            "html_url": data.get("html_url", ""),
            "assets": [
                {
                    "name": a.get("name", ""),
                    "browser_download_url": a.get("browser_download_url", ""),
                    "size": a.get("size", 0),
                }
                for a in (data.get("assets") or [])
            ],
        }
        try:
            self._updates_cache_path(repo).write_text(
                json.dumps(result, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.debug("Update cache write failed for %s: %s", repo, e)
        return result

    # -- normalization ------------------------------------------------------
    def _normalize_release(self, release, repo):
        tag_name = release.get("tag_name", "")
        assets = release.get("assets", [])
        rom_variants = [_parse_rom_asset_variant(a, tag_name, repo) for a in assets]
        rom_variants = [v for v in rom_variants if v]
        preferred = select_preferred_rom_asset(rom_variants)
        if not preferred:
            return None
        return {
            "tag_name": tag_name,
            "name": release.get("name", ""),
            "body": release.get("body", ""),
            "published_at": release.get("published_at", ""),
            "download_url": preferred["asset"]["browser_download_url"],
            "asset_name": preferred["asset"]["name"],
            "asset_size": preferred["asset"].get("size", 0),
            "assets": assets,
            "rom_variants": rom_variants,
            "prerelease": bool(release.get("prerelease")),
            "source_repo": repo,
        }

    def _build_releases(self, releases_data, repo):
        releases = []
        for release in releases_data or []:
            tag_name = release.get("tag_name", "")
            is_prerelease = bool(release.get("prerelease"))
            if "stable" in tag_name.lower():
                is_prerelease = False
            assets = release.get("assets", [])
            rom_variants = [_parse_rom_asset_variant(a, tag_name, repo) for a in assets]
            rom_variants = [v for v in rom_variants if v]
            preferred = select_preferred_rom_asset(rom_variants)
            if not preferred:
                continue
            releases.append({
                "tag_name": tag_name,
                "name": release.get("name", ""),
                "body": release.get("body", ""),
                "published_at": release.get("published_at", ""),
                "download_url": preferred["asset"]["browser_download_url"],
                "asset_name": preferred["asset"]["name"],
                "asset_size": preferred["asset"].get("size", 0),
                "assets": assets,
                "rom_variants": rom_variants,
                "prerelease": is_prerelease,
                "source_repo": repo,
            })
        return releases

    def releases_for_package(self, package: FirmwarePackage, model: str, show_nightly=False, selected_type=None, force_refresh=False):
        """Fetch releases for a catalogue package, filtered for the device model.

        Only releases that carry a ``rom*.zip`` asset compatible with ``model``
        (exact match or ``dual``) are included.  When ``selected_type`` is
        given (e.g. 'A' or 'B'), only releases with a matching hardware type
        variant are included.
        """
        releases = self.get_all_releases(package.repo, force_refresh=force_refresh)
        out = []
        for rel in releases:
            if not _release_matches_model(model, rel, package, selected_type):
                continue
            if rel.get("prerelease") and not show_nightly:
                continue
            # Re-pick the preferred asset with the model filter so the
            # download URL points to the correct rom*.zip for this model.
            rom_variants = filter_rom_variants_for_model(
                rel.get("rom_variants") or [],
                model,
                package.model,
                rel.get("source_repo", "") or "",
            )
            if selected_type:
                rom_variants = [v for v in rom_variants if v.get("type") == selected_type] or rom_variants
            preferred = select_preferred_rom_asset(rom_variants, selected_type=selected_type, model=model)
            if preferred:
                rel = dict(rel)  # shallow copy, don't mutate cache
                rel["download_url"] = preferred["asset"]["browser_download_url"]
                rel["asset_name"] = preferred["asset"]["name"]
                rel["asset_size"] = preferred["asset"].get("size", 0)
            out.append(rel)
        return sorted(out, key=release_sort_key, reverse=True)


def _app_cache_dir():
    """Per-user cache dir, mirroring CE's ``_FIRMWARE_APP_DIR/.cache``."""
    import sys

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "innioasis-updater" / ".cache"
