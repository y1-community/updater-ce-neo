"""Release rows for the text session, using the window's catalogue rules.

The window reads ``slidia_manifest.xml`` and GitHub releases through Qt. This
module does the same filtering with the standard library: brand, model,
hardware type and software pick one package, then the release list keeps or
drops a row for the same reasons (minimum Rockbox version, 240p, nightly,
Type A/B asset names).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

from .. import versioning
from .catalog import Package, USER_AGENT, cache_dir

_TYPE_B = ("_type_b", "-type-b", "type-b", " type b", "type_b", "typeb", "_b.zip", "-b.zip")
_PORTABLE = ("-windows.zip", "-windows-arm64.zip", "-linux.zip", "-linux-arm64.zip", "-macos.zip")
ROCKBOX_MIN = (0, 5)


@dataclass(frozen=True)
class ReleaseRow:
    label: str
    tag: str
    name: str
    body: str
    asset_name: str
    asset_url: str
    version: tuple[int, ...]
    resolution: str
    html_url: str
    hardware_type: str


def effective_brand(item: Package) -> str:
    return (item.brand or "").strip() or "Innioasis"


def brand_names(items: list[Package]) -> list[str]:
    names: list[str] = []
    for item in items:
        brand = effective_brand(item)
        if brand not in names:
            names.append(brand)
    names.sort(key=lambda name: (name.casefold() != "innioasis", name.casefold()))
    return names or ["Innioasis"]


def models_for(items: list[Package], brand: str) -> list[str]:
    want = (brand or "").casefold()
    seen: list[str] = []
    for item in items:
        if effective_brand(item).casefold() != want:
            continue
        if item.model and item.model not in seen:
            seen.append(item.model)
    return seen


def software_names(items: list[Package], brand: str, model: str) -> list[str]:
    want_brand = (brand or "").casefold()
    want_model = (model or "").upper()
    names: list[str] = []
    folded: set[str] = set()
    for item in items:
        if item.model.upper() != want_model:
            continue
        if effective_brand(item).casefold() != want_brand:
            continue
        key = item.name.strip().lower()
        if key and key not in folded:
            folded.add(key)
            names.append(item.name)
    return names


def package_for(items: list[Package], brand: str, model: str, software: str) -> Package | None:
    target = (software or "").strip().lower()
    for item in items:
        if item.model.upper() != (model or "").upper():
            continue
        if effective_brand(item).casefold() != (brand or "").casefold():
            continue
        if item.name.strip().lower() == target:
            return item
    return None


def hardware_types(model: str) -> list[str]:
    return ["A", "B"] if (model or "").upper() == "Y1" else []


def version_tuple(text: str, release_name: str = "") -> tuple[int, ...] | None:
    """The version the window reads from a tag and its release title.

    Y2Linux titles (``Y2Linux 1.0 Beta 9.3.20``) carry the product version
    while their tags are build identifiers (``baseline-229``). The shared
    parser is the one the window and the website use, so the TUI cannot pick
    the leading ``1.0`` from the title instead.
    """
    return versioning.release_version_tuple(text, release_name=release_name or None)


def _below_minimum(version: tuple[int, ...] | None, minimum: tuple[int, ...] = ROCKBOX_MIN) -> bool:
    if version is None:
        return True
    width = max(len(version), len(minimum))
    left = version + (0,) * (width - len(version))
    right = minimum + (0,) * (width - len(minimum))
    return left < right


def _is_base_tag(tag: str) -> bool:
    words = [word for word in re.split(r"[^a-z0-9]+", (tag or "").lower()) if word]
    return "base" in words


def _asset_type(name: str) -> str:
    lower = name.lower()
    return "B" if any(token in lower for token in _TYPE_B) else "A"


def _asset_resolution(name: str) -> str:
    lower = name.lower()
    if "_240p" in lower or "240p" in lower:
        return "240p"
    if "_360p" in lower:
        return "360p"
    return "native"


def _usable_assets(assets: list[dict]) -> list[dict]:
    files = []
    for asset in assets or []:
        name = str(asset.get("name") or "")
        lower = name.lower()
        if not lower.endswith(".zip"):
            continue
        if "source" in lower or "legal-info" in lower:
            continue
        files.append(asset)
    if any(str(asset.get("name") or "").lower().endswith(suffix) for asset in files for suffix in _PORTABLE):
        plain = [asset for asset in files if not str(asset.get("name") or "").lower().endswith(_PORTABLE)]
        if plain:
            files = plain
    roms = [asset for asset in files if str(asset.get("name") or "").lower().startswith("rom")]
    if roms:
        return roms
    # A release that only ships side files (voices, libs) is not a firmware row.
    side = ("libs", "voices", "source", "legal")
    firmware = [
        asset for asset in files
        if not any(token in str(asset.get("name") or "").lower() for token in side)
    ]
    return firmware[:1] if len(firmware) == 1 else []


def _label(tag: str, name: str, resolution: str, asset_name: str, prerelease: bool) -> str:
    """Use the window's release-label rules for the text session too."""
    rel = {
        "tag_name": tag,
        "name": name,
        "prerelease": prerelease,
        "asset_name": asset_name,
        "selected_resolution": resolution,
    }
    return versioning.parse_clean_release_tag_label(rel) or (name or tag or asset_name).strip()


def rows_from_releases(
    releases: list[dict],
    *,
    model: str,
    hardware_type: str,
    repo: str,
    show_old: bool = False,
    show_240p: bool = False,
    show_nightly: bool = False,
) -> list[ReleaseRow]:
    """The Available System Software rows for one package."""
    model = (model or "").upper()
    hardware_type = (hardware_type or "").upper()
    rockbox = "rockbox" in (repo or "").lower()
    gate = rockbox and model == "Y1" and hardware_type != "B"
    y2_rockbox = model == "Y2" and rockbox
    rows: list[ReleaseRow] = []
    for release in releases or []:
        if not isinstance(release, dict) or release.get("draft"):
            continue
        tag = str(release.get("tag_name") or "")
        if _is_base_tag(tag):
            continue
        if "nightly" in tag.lower() and not show_nightly:
            continue
        if release.get("prerelease") and not show_nightly and not y2_rockbox:
            continue
        release_name = str(release.get("name") or "")
        version = version_tuple(tag, release_name)
        if gate and not show_old and _below_minimum(version):
            continue
        variants = []
        for asset in _usable_assets(release.get("assets") or []):
            name = str(asset.get("name") or "")
            kind = _asset_type(name)
            if hardware_type in ("A", "B") and kind != hardware_type:
                continue
            variants.append((asset, _asset_resolution(name), kind))
        if not variants:
            continue
        if not show_240p and all(resolution == "240p" for _asset, resolution, _kind in variants):
            continue
        chosen: list[tuple[dict, str, str]] = []
        if show_240p:
            standard = [item for item in variants if item[1] != "240p"]
            low = [item for item in variants if item[1] == "240p"]
            if standard:
                chosen.append(standard[0])
            if low:
                chosen.append(low[0])
        else:
            standard = [item for item in variants if item[1] != "240p"] or variants
            chosen.append(standard[0])
        for asset, resolution, kind in chosen:
            rows.append(
                ReleaseRow(
                    label=_label(tag, release_name, resolution, str(asset.get("name") or ""), bool(release.get("prerelease"))),
                    tag=tag,
                    name=release_name or tag,
                    body=str(release.get("body") or ""),
                    asset_name=str(asset.get("name") or ""),
                    asset_url=str(asset.get("browser_download_url") or ""),
                    version=version or (),
                    resolution=resolution,
                    html_url=str(release.get("html_url") or ""),
                    hardware_type=kind,
                )
            )
    rows.sort(key=lambda row: (row.version, 0 if row.resolution != "240p" else -1), reverse=True)
    return rows


def notes_text(row: ReleaseRow | None, software: str) -> str:
    if row is None:
        return "Select a release to read its notes."
    body = row.body or ""
    body = re.sub(r"(?m)^#{1,6}\s*", "", body)
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", body)
    body = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    body = re.sub(r"[*_`]", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    lines = [software or row.name, row.label, ""]
    if body:
        lines.append(body)
    else:
        lines.append("No release notes.")
    if row.html_url:
        lines.extend(["", "See more", row.html_url])
    return "\n".join(lines)


def _get_json(url: str, timeout: float = 25.0):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_releases(repo: str, force: bool = False, timeout: float = 25.0) -> list[dict]:
    """Every release for ``owner/name``, from a short disk cache when it is fresh."""
    cache = cache_dir() / "releases" / (repo.replace("/", "_") + ".json")
    if not force:
        try:
            if cache.exists() and cache.stat().st_size > 2:
                import time
                if time.time() - cache.stat().st_mtime < 15 * 60:
                    cached = json.loads(cache.read_text(encoding="utf-8"))
                    if isinstance(cached, list):
                        return cached
        except (OSError, ValueError):
            pass
    url = f"https://api.github.com/repos/{repo}/releases?per_page=100"
    try:
        payload = _get_json(url, timeout=timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise RuntimeError(f"{repo}: {exc}") from exc
    if isinstance(payload, dict):
        raise RuntimeError(f"{repo}: {payload.get('message') or 'unexpected release payload'}")
    if not isinstance(payload, list):
        raise RuntimeError(f"{repo}: unexpected release payload")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass
    return payload
