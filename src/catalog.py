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

import dataclasses
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path

from .config import (
    GITHUB_API,
    GITHUB_RELEASES_PER_PAGE,
    RELEASE_CACHE_TTL_SECONDS,
    REPO_MIN_RELEASE_VERSION,
    UPDATE_CACHE_TTL_SECONDS,
    resolve_firmware_repo,
)

from .network import requests_module  # noqa: E402  (kept below the config import)

from . import portable
from . import versioning

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
    icon: str = ""
    icon_dark: str = ""
    image: str = ""
    post_install: str = ""
    post_install_2: str = ""
    # Repository handle of the firmware this release was built from, e.g.
    # ``y1-community/y1-stock-rom``. Equal or related firmwares share a data
    # partition, which is what the Keep Data feature depends on.
    derivative: str = ""
    # Application repository whose APK releases describe what this firmware
    # deploys (``info_repo``): its tags and notes are listed, while the
    # firmware itself still comes from ``repo``.
    info_repo: str = ""
    # Application repository whose APK the app downloads and installs after the
    # firmware (``apk_repo``).
    apk_repo: str = ""
    # Whether Keep Data is completely blocked/disallowed for this firmware
    do_not_keep_data: bool = False
    # Whether this firmware needs SP Flash Tool's Format All + Download mode
    # because its partition table differs from the installed one
    # (``PMT_format_needed="true"`` in slidia_manifest.xml). That mode
    # reformats internal storage, so Keep Data is never offered for it.
    pmt_format_needed: bool = False
    # Explicit hardware type target: 'A' or 'B' (or empty for both/auto)
    hw_type: str = ""
    # Asset filename/glob pattern for Type A variant
    type_a_asset: str = ""
    # Asset filename/glob pattern for Type B variant
    type_b_asset: str = ""
    # Custom asset filename/glob pattern for firmware zips (e.g. '*.zip')
    asset_pattern: str = ""
    # Portable Flashing Zip Packager mirror repositories for this release
    # (``portable_zips`` in slidia_manifest.xml, comma separated). The row's
    # download is then the package a mirror built for this platform.
    portable_zips: str = ""
    # Brand the release belongs to (``brand`` in slidia_manifest.xml). Empty
    # means the device registry's brand for the model applies; a model the
    # registry does not know yet is :data:`DEFAULT_BRAND`.
    brand: str = ""
    # Repository of a firmware the player must already run before this one can
    # be installed (``install_first`` in slidia_manifest.xml). Overlay builds
    # rely on the base build's preloader, partition table and bootloader.
    install_first: str = ""
    # Minimum (or range) of that base firmware (``install_first_version``).
    # A bare version is a minimum; prefix ``=`` pins it exactly. Empty means
    # any version; see ``install_first.parse_version_constraint``.
    install_first_version: str = ""
    # Archive names declared per player and hardware revision
    # (``package_name_y2``, ``package_name_y1_type_b``,
    # ``package_name_timmkoo_a5``), keyed by the attribute's own suffix after
    # ``package_name_``. Empty means ``package_name`` covers every model the
    # entry lists. Any suffix a manifest writes is kept as written, so a model
    # added to the catalogue later needs no change here.
    package_names: dict = dataclasses.field(
        default_factory=dict, compare=False, hash=False, repr=False
    )


def post_install_urls_for(*packages) -> tuple[str, str]:
    """The first non-empty post-install URL pair among ``packages``.

    ``slidia_manifest.xml`` names the page(s) to open once a software install
    finishes (``post_install`` / ``post_install_2``), and a local package's
    ``fw_package.ini`` can name them too. Callers pass the candidates in
    precedence order; an absent entry simply falls through.
    """
    for pkg in packages:
        if pkg is None:
            continue
        post = str(getattr(pkg, "post_install", "") or "").strip()
        post_2 = str(getattr(pkg, "post_install_2", "") or "").strip()
        if post or post_2:
            return post, post_2
    return "", ""


# Live manifest entries (see ``manifest.py``) replace the static table once
# fetched; the static table remains the offline first-run fallback.
LIVE_CATALOG: list = []

#: ``(model, name)`` / ``(model, repo)`` pairs the live manifest used to list
#: but which publish no installable release. ``_catalog_packages`` merges the
#: static table for models the live catalogue does not cover, so it must also
#: skip a static row whose live twin was filtered out: a dead firmware must
#: not return through the offline table.
_UNAVAILABLE_NAMES: set = set()
_UNAVAILABLE_REPOS: set = set()


def _remember_unavailable(entries) -> None:
    global _UNAVAILABLE_NAMES, _UNAVAILABLE_REPOS
    names, repos = set(), set()
    for package in entries or []:
        model = str(getattr(package, "model", "") or "").strip().upper()
        name = str(getattr(package, "name", "") or "").strip().lower()
        repo = str(getattr(package, "repo", "") or "").strip().lower()
        if model and name:
            names.add((model, name))
        if model and repo:
            repos.add((model, repo))
    _UNAVAILABLE_NAMES, _UNAVAILABLE_REPOS = names, repos


def _unavailable_static_entry(package) -> bool:
    model = str(getattr(package, "model", "") or "").strip().upper()
    name = str(getattr(package, "name", "") or "").strip().lower()
    repo = str(getattr(package, "repo", "") or "").strip().lower()
    return (model and name and (model, name) in _UNAVAILABLE_NAMES) or (
        model and repo and (model, repo) in _UNAVAILABLE_REPOS
    )


def set_live_catalog(entries: list):
    """Install firmware entries parsed from the live ``slidia_manifest.xml``.
    They override the static table for every (model, software) they cover."""
    global LIVE_CATALOG
    LIVE_CATALOG = [e for e in entries if e]


def _catalog_packages():
    if not LIVE_CATALOG:
        return list(CATALOG)
    covered_models_and_names = {(p.model.upper(), p.name.strip().lower()) for p in LIVE_CATALOG}
    covered_models_and_repos = {(p.model.upper(), p.repo.strip().lower()) for p in LIVE_CATALOG}
    fallback = [
        p for p in CATALOG
        if (p.model.upper(), p.name.strip().lower()) not in covered_models_and_names
        and (p.model.upper(), p.repo.strip().lower()) not in covered_models_and_repos
        and not _unavailable_static_entry(p)
    ]
    return list(LIVE_CATALOG) + fallback


CATALOG = [
    FirmwarePackage("original-y1", "Original Software", "Y1", "y1-community/y1-stock-rom", "rom.zip",
                    "The stock software for restoring an Innioasis Y1."),
    FirmwarePackage("original-y2", "Original Software", "Y2", "y1-community/y1-stock-rom", "rom_y2.zip",
                    "The stock software for restoring an Innioasis Y2."),
    FirmwarePackage("rockbox-y1", "Rockbox", "Y1", "rockbox-y1/rockbox", "rom.zip",
                    "A music-first custom software with deep playback and theme options."),
    FirmwarePackage("rockbox-y2", "Rockbox", "Y2", "spycemagic/y2-rockbox", "rom_y2.zip",
                    "The Y2 Rockbox build."),
    FirmwarePackage("solar-y1", "Solar", "Y1", "thesolarproject/solar", "rom.zip",
                    "A Y1 custom software with network features, including YouTube support."),
    FirmwarePackage("solar-y2", "Solar", "Y2", "thesolarproject/solar", "rom_y2.zip",
                    "Listed for Y2 in the current Updater CE catalogue."),
    FirmwarePackage("jj-launcher-y1", "JJ Launcher", "Y1", "ismileblue/y1_launcher", "rom.zip",
                    "A launcher software for Y1."),
    FirmwarePackage("jj-launcher-y2", "JJ Launcher", "Y2", "ismileblue/y1_launcher", "rom_y2.zip",
                    "Listed for Y2 in the current Updater CE catalogue."),
    FirmwarePackage("original-a5", "Original Software", "A5", "y1-community/y1-stock-rom", "rom_a5.zip",
                    "The stock software for restoring an Innioasis A5."),
    FirmwarePackage("rockbox-a5", "Rockbox", "A5", "y1-community/rockbox-y2-rom", "rom_a5.zip",
                    "The A5 Rockbox build."),
    FirmwarePackage("inniclassic-y1", "Inniclassic", "Y1", "FabianZettl/inniclassic", "rom.zip",
                    "A classic-style Y1 software."),
    FirmwarePackage("y2player-y2", "Y2Player", "Y2", "schulzcode/Y2Player", "rom_y2.zip",
                    "A Y2 custom software."),
    FirmwarePackage("android-4-2-y1", "Android 4.2", "Y1", "y1-community/y1-ata-rom", "rom.zip",
                    "Android 4.2 Jelly Bean ATA firmware for Innioasis Y1."),
    FirmwarePackage("android-4-4-y2", "Android 4.4", "Y2", "y1-community/y2-ata-rom", "rom_y2.zip",
                    "Android 4.4 KitKat engineering firmware for Innioasis Y2."),
    FirmwarePackage("koensayr-y1", "Koensayr", "Y1", "ryan-specter/koensayr-auto", "rom.zip",
                    "A Y1 custom software maintained by the community."),
]


#: Brand an entry without an explicit ``brand`` belongs to.
DEFAULT_BRAND = "Innioasis"


def package_brand(package) -> str:
    """The brand a catalogue entry belongs to.

    A ``brand`` attribute in ``slidia_manifest.xml`` wins. Otherwise the device
    registry's brand for that model applies — that is where a newly discovered
    player is tagged — and an unknown model counts as the vendor's own.
    """
    explicit = str(getattr(package, "brand", "") or "").strip()
    if explicit:
        return explicit
    from . import device_models

    return device_models.brand_for(getattr(package, "model", "")) or DEFAULT_BRAND


def _brand_matches(package, brand: str) -> bool:
    wanted = str(brand or "").strip()
    if not wanted:
        return True
    return package_brand(package).casefold() == wanted.casefold()


def brand_names() -> list:
    """Brands the catalogue offers, Innioasis first.

    Empty when no package carries a ``brand`` attribute: the Select Software
    brand drop-down only appears when the manifest actually tags releases.
    """
    packages = LIVE_CATALOG or list(CATALOG)
    if not any(str(getattr(p, "brand", "") or "").strip() for p in packages):
        return []
    names = []
    for package in packages:
        brand = package_brand(package)
        if not any(brand.casefold() == known.casefold() for known in names):
            names.append(brand)
    # A brand whose every model is unlisted (no release to install) is not a
    # useful filter row: picking it would leave the model drop-down empty.
    names = [name for name in names if available_models(brand=name)]
    # Innioasis is the default pick, so it leads regardless of document order.
    names.sort(key=lambda name: name.casefold() != DEFAULT_BRAND.casefold())
    return names


def packages_for_model(model: str, brand: str = ""):
    # An info_repo package without a post-install page has no instructions for
    # its APK, so it is not offered at all.
    return [
        p for p in _catalog_packages()
        if p.model == model
        and _brand_matches(p, brand)
        and (not p.info_repo or p.post_install or p.post_install_2)
    ]


def available_models(brand: str = ""):
    """Model ids the user may pick in the device drop-down.

    Driven by ``slidia_manifest.xml`` (``LIVE_CATALOG``): a model is offered
    only when the manifest actually lists a release for it. When no manifest
    has loaded — first run offline, or a failed fetch — the static table is
    used instead so the app never presents an empty model list. ``brand``
    narrows the list to the releases of one brand (the Select Software filter).

    Note this is deliberately *not* :func:`_catalog_packages`: that helper
    merges the static fallback in, which is right for looking up a package but
    wrong for deciding what to offer.
    """
    packages = LIVE_CATALOG or list(CATALOG)
    seen = []
    for pkg in packages:
        if pkg.model and _brand_matches(pkg, brand) and pkg.model not in seen:
            seen.append(pkg.model)
    return seen


def software_names_for_model(model: str, brand: str = ""):
    """Software names that model can actually list a release for.

    While a live manifest is loaded it is the whole list. Merging the static
    table in would offer a hand-written name no repository publishes any more
    beside the manifest's current name for the same software ("Android 4.2"
    next to "Android 4.2 (Dev)"), which is a drop-down row that can never
    produce a release. The static table remains the offline fallback.
    """
    if LIVE_CATALOG:
        packages = [
            p for p in LIVE_CATALOG
            if p.model == model
            and _brand_matches(p, brand)
            and (not p.info_repo or p.post_install or p.post_install_2)
        ]
    else:
        packages = packages_for_model(model, brand=brand)
    seen_lower = set()
    seen = []
    for p in packages:
        norm = p.name.strip().lower()
        if norm not in seen_lower:
            seen_lower.add(norm)
            seen.append(p.name)
    return seen


def packages_for_model_software(model: str, software: str, brand: str = ""):
    target = (software or "").strip().lower()
    return [
        p
        for p in packages_for_model(model, brand=brand)
        if p.name.strip().lower() == target
    ]


# ---------------------------------------------------------------------------
# rom*.zip asset variant parsing (ported from firmware_downloader.py)
# ---------------------------------------------------------------------------
def _strip_repo_names(name, repo=""):
    """An asset name with the repository's own name taken out of it.

    Releases have long carried the repository name inside the zip name (a
    ``y1-community`` prefix on a Y2 firmware, for instance), and reading that
    text as a device marker handed the archive to the wrong model. The
    repository is already known from the release itself, so its own name is
    not evidence about which player the firmware is for.
    """
    lower = str(name or "").lower()
    repo_lower = str(repo or "").strip().lower()
    fragments = set()
    if repo_lower:
        fragments.add(repo_lower)
        if "/" in repo_lower:
            owner, _, leaf = repo_lower.partition("/")
            fragments.update((owner, leaf))
    for fragment in sorted((f for f in fragments if f), key=len, reverse=True):
        lower = lower.replace(fragment, "-")
    # "<owner>-community" is a GitHub organisation, not a player: a zip named
    # "inniclassic-y1-community-2.0.zip" says where it was published, not that
    # it is Y1 firmware.
    lower = re.sub(r"[a-z0-9]+-community", "-", lower)
    return lower


def _registered_model_in(text):
    """A model id from the device registry named in ``text``, else ``""``.

    The wider lineup (G1, G5, Q3, Q5 ...) is recognised the same way the
    local-file importer recognises it, so a firmware zip named after its player
    keeps its model even when it is not a rom*.zip. Y1/Y2/A5 stay with the
    rules below, which also read the release tag.
    """
    try:
        from . import device_models

        model, _variant = device_models.model_from_filename(
            text, exclude=("Y1", "Y2", "A5")
        )
    except Exception:
        logger.debug("Device registry lookup failed for %r", text, exc_info=True)
        return ""
    return model or ""


def _classify_variant_model(name, tag_name="", repo=""):
    """Classify a rom*.zip asset as Y1, Y2, A5, another model, or dual."""
    lower = _strip_repo_names(name, repo)
    tag_lower = (tag_name or "").lower()
    repo_lower = (repo or "").lower()
    repo_name = repo_lower.split("/")[-1] if "/" in repo_lower else repo_lower

    if "_a5" in lower or lower.startswith("rom_a5") or " a5" in lower or "-a5" in lower:
        return "A5"
    if "a5" in tag_lower and "y1" not in tag_lower and "y2" not in tag_lower:
        return "A5"
    if "a5" in repo_name and "y1" not in repo_name and "y2" not in repo_name:
        return "A5"

    # Explicit asset name checks for Y1 before repo fallback
    if any(tok in lower for tok in ("_y1", "rom_y1", "-y1", " y1", "mt6572")):
        return "Y1"

    if "_y2" in lower or lower.startswith("rom_y2") or "-y2" in lower or " y2" in lower:
        return "Y2"
    if any(token in lower for token in ("6582", "eastaeon", "mt6582")):
        return "Y2"
    if any(token in tag_lower for token in ("6582", "eastaeon", "mt6582")):
        return "Y2"
    # Any other registered model named in the file name itself. A hardware
    # revision is not a model: "type_b" / "_b" marks Type B below, never a
    # device called B.
    registered = _registered_model_in(lower)
    if registered:
        return registered
    if "y2" in tag_lower and "y1" not in tag_lower:
        return "Y2"
    if "y1" in tag_lower and "y2" not in tag_lower:
        return "Y1"
    if "y2" in repo_name and "y1" not in repo_name.replace("y2", "", 1):
        return "Y2"
    if "y1" in repo_name and "y2" not in repo_name:
        return "Y1"
    return "dual"


def _parse_rom_asset_variant(asset, tag_name="", repo="", has_explicit_type_assets=False, package=None):
    """Classify a rom*.zip, *install*.zip or general firmware zip asset into hardware type (A/B), resolution, model."""
    try:
        name = asset.get("name", "")
        lower = name.lower()
        if not lower.endswith(".zip"):
            return None
        if _is_source_archive_name(name):
            return None

        # Check explicit manifest asset shims if package is provided
        hw_type = None
        if package is not None:
            type_a_pat = (getattr(package, "type_a_asset", "") or "").lower()
            type_b_pat = (getattr(package, "type_b_asset", "") or "").lower()
            if type_b_pat and fnmatch(lower, type_b_pat):
                hw_type = "B"
            elif type_a_pat and fnmatch(lower, type_a_pat):
                hw_type = "A"
            elif getattr(package, "hw_type", "") in ("A", "B"):
                hw_type = package.hw_type

        tag_lower = (tag_name or "").lower()
        if hw_type is None:
            is_explicit_b = any(
                tok in lower for tok in (
                    "_type_b", "-type-b", "type-b", " type b", "type_b", "typeb",
                    "_b.zip", "-b.zip",
                )
            )
            is_type_b = is_explicit_b
            if not is_type_b and not has_explicit_type_assets and any(
                tok in tag_lower for tok in ("type-b", "type_b", "type b", "typeb")
            ):
                is_type_b = True
            hw_type = "B" if is_type_b else "A"

        if "_360p" in lower:
            resolution = "360p"
        elif "_240p" in lower:
            resolution = "240p"
        else:
            resolution = "native"

        model = _classify_variant_model(name, tag_name, repo)
        # Whether the asset name itself names the model. A name such as
        # rom_y2.zip is stronger evidence than a repository called "...-y2":
        # a Y2 repo can still republish the legacy Y1 rom.zip beside its own
        # rom_y2.zip, and that unqualified zip must not win a tie just because
        # it comes first in the release.
        explicit_model = _classify_variant_model(name) in ("Y1", "Y2", "A5")
        # An unqualified archive belongs to the model the package is listed
        # for: a rom.zip in a G5 repository is G5 firmware, not "dual".
        if model == "dual" and package is not None and getattr(package, "model", ""):
            model = package.model

        return {
            "asset": asset,
            "type": hw_type,
            "resolution": resolution,
            "model": model,
            "model_explicit": explicit_model,
        }
    except Exception:
        return None


def _is_rom_zip_name(name):
    return fnmatch((name or "").lower(), "rom*.zip")


def package_names_for(package) -> dict:
    """Per-model/type archive names a package declares, keyed by suffix.

    ``package_name_y1_type_b="rom_type_b.zip"`` is stored as
    ``{"y1_type_b": "rom_type_b.zip"}``. The suffix is not interpreted here,
    so a manifest can name a model or brand this build has never heard of.
    """
    names = getattr(package, "package_names", None)
    return dict(names) if isinstance(names, dict) else {}


def package_name_for(package, model="", hw_type="") -> str:
    """The archive name this package declares for ``model`` and ``hw_type``.

    The most specific declaration wins: ``<brand>_<model>_type_<x>``, then
    ``<model>_type_<x>``, then ``<brand>_<model>``, then ``<model>``, then the
    plain ``package_name`` (rom.zip / rom_<model>.zip). The brand only takes
    part when the entry declares one, which is how the same model number for
    two brands stays two different archives.
    """
    fallback = (
        str(getattr(package, "package_name", "") or "").strip()
        if package is not None
        else ""
    )
    names = package_names_for(package)
    if not names:
        return fallback
    brand = str(getattr(package, "brand", "") or "").strip().lower()
    model_key = (
        str(model or "").strip()
        or str(getattr(package, "model", "") or "").strip()
    ).lower()
    type_key = str(hw_type or "").strip().lower()
    if model_key:
        candidates = []
        if type_key:
            if brand:
                candidates.append(f"{brand}_{model_key}_type_{type_key}")
            candidates.append(f"{model_key}_type_{type_key}")
        if brand:
            candidates.append(f"{brand}_{model_key}")
        candidates.append(model_key)
        for key in candidates:
            value = str(names.get(key) or "").strip()
            if value:
                return value
    return fallback


def _package_declares_non_rom_archive(package, model="", hw_type="") -> bool:
    """Whether the archive this package declares is not a rom*.zip.

    ``package_name`` (or a per-model/type ``package_name_*``) in
    slidia_manifest.xml names the firmware archive its releases carry.
    rom.zip / rom_<model>.zip is the standard, so a package that declares
    another name is one whose releases never follow it (y2linux publishes
    ``...-first-install.zip``), and it is the only kind that may list a
    differently named zip as firmware. The name may be a literal filename or
    a wildcard pattern.
    """
    if package is None:
        return False
    name = package_name_for(package, model, hw_type)
    return bool(name) and not _is_rom_zip_name(name)


def repo_is_single_model(repo, model="") -> bool:
    """Whether the catalogue lists ``repo`` for exactly one player.

    A repository that publishes firmware zips under its own names rather than
    rom*.zip can only be read safely when it serves one model (y2linux serves
    the Y2 and nothing else). A repository the manifest lists for two models
    (solar is Y1 and Y2) could be handing over either one, so its non-rom zips
    are not guessed at.
    """
    repo_lower = str(repo or "").strip().lower()
    if not repo_lower:
        return False
    # The package being evaluated is itself a catalogue entry, so its model
    # counts even when the live manifest has not loaded yet (a first offline
    # run): what matters is whether another model is listed for the same repo.
    models = set()
    if str(model or "").strip():
        models.add(str(model).strip().upper())
    try:
        entries = _catalog_packages()
    except Exception:
        logger.debug("Catalogue lookup failed for %s", repo, exc_info=True)
        return False
    for entry in entries or []:
        if str(getattr(entry, "repo", "") or "").strip().lower() != repo_lower:
            continue
        entry_model = str(getattr(entry, "model", "") or "").strip().upper()
        if entry_model:
            models.add(entry_model)
    return len(models) == 1


def _release_accepts_other_zip_names(
    package, repo_has_rom=False, model="", hw_type=""
) -> bool:
    """Whether a zip without ``rom`` in its name may be this package's firmware.

    Three ways to qualify: the package's ``package_name`` declares an archive
    that is not a rom*.zip name (a plain name or a wildcard such as
    ``Y2Linux-*-first-install.zip``), or the repository publishes no rom*.zip
    in any release and the manifest lists it for a single model, or no package
    is known and the repository has no rom*.zip naming at all. Every firmware
    normally published as rom.zip / rom_<model>.zip is filtered strictly: in
    one of its releases a non-rom zip is a voice pack, an on-device updater or
    another extra, never installable firmware.
    """
    if repo_has_rom:
        return _package_declares_non_rom_archive(package, model, hw_type)
    if package is None:
        return True
    return repo_is_single_model(package.repo, getattr(package, "model", ""))


def _is_source_archive_name(name):
    """Source-code bundles are never installable firmware assets.

    Some projects publish both the install zip and a source zip/tar in the
    same release. The install zip is the one the catalogue must use, even when
    the source archive is the only other zip in the release.
    """
    lower = (name or "").lower()
    return "source" in lower or "legal-info" in lower


#: Asset names a Portable Flashing Zip Packager mirror adds next to the
#: original firmware zip: the source name plus one suffix per platform.
PORTABLE_BUNDLE_SUFFIXES = (
    "-windows.zip",
    "-windows-arm64.zip",
    "-linux.zip",
    "-linux-arm64.zip",
    "-macos.zip",
)


def _is_portable_bundle_name(name):
    """True for ``rom-windows.zip``-style portable mirror packages."""
    lower = (name or "").lower()
    return any(lower.endswith(suffix) for suffix in PORTABLE_BUNDLE_SUFFIXES)


def _is_nonfirmware_zip_name(name):
    """True for a zip a release publishes beside its firmware.

    ``rockbox-y1/rockbox`` ships ``voices.zip`` (spoken menus) and
    ``update.zip`` (Rockbox's on-device updater) next to ``rom.zip``, and also
    publishes voice-file releases that carry nothing else. Neither file is a
    flasher package, so a release whose only zips are these has no firmware to
    install and must not become a catalogue row.
    """
    lower = str(name or "").strip().lower()
    if not lower.endswith(".zip") or _is_rom_zip_name(lower):
        return False
    words = [word for word in re.split(r"[^a-z0-9]+", lower[:-4]) if word]
    if any(word in ("voice", "voices", "lib", "libs") for word in words):
        return True
    return "update" in words


def _without_portable_bundles(assets):
    """Drop portable mirror packages when the release also carries the plain zip.

    A mirror release republishes the original asset next to one ready-to-run
    zip per platform. Only the original is a firmware archive for this app, so
    listing ``rom-windows.zip`` as a ROM variant would hand a Windows bundle to
    a Linux or macOS user. A mirror configured without the original asset
    (``copy_original_assets: false``) still works: its bundles are kept when
    they are all it has.
    """
    assets = list(assets or [])
    if not any(_is_portable_bundle_name(a.get("name")) for a in assets):
        return assets
    plain = [a for a in assets if not _is_portable_bundle_name(a.get("name"))]
    return plain or assets


def _release_rom_variants(
    assets, tag_name, repo, repo_has_rom=False, package=None, model="", selected_type=None
):
    """Variants a release offers: its rom*.zip assets, else its candidate firmware zips.

    A release that ships any rom*.zip follows the rom*.zip standard, so only
    those assets are offered and the other zips are ignored. A zip without
    ``rom`` in its name is a firmware candidate only for a repository whose
    releases never use that name, or for a package whose ``package_name``
    declares a different archive (see :func:`_release_accepts_other_zip_names`),
    or when an explicit asset_pattern is declared in slidia_manifest.xml.

    A portable mirror release also carries ``rom-windows.zip``-style bundles
    beside the original asset; those are not firmware archives here (see
    :func:`_without_portable_bundles`).
    """
    assets = [
        a for a in (assets or []) if not _is_source_archive_name(a.get("name"))
    ]
    assets = _without_portable_bundles(assets)
    # A package_name that is not a rom*.zip name pins the archive itself, and
    # may be a wildcard (y2linux publishes a differently numbered
    # ...-first-install.zip per release). A per-model/type declaration
    # (package_name_y1_type_b) answers for its own model and revision.
    declared = (
        package_name_for(package, model, str(selected_type or "")).strip().lower()
        if package
        else ""
    )
    if declared and not _is_rom_zip_name(declared):
        matched = [a for a in assets if fnmatch((a.get("name") or "").lower(), declared)]
        if matched:
            pinned = [
                _parse_rom_asset_variant(a, tag_name, repo, package=package)
                for a in matched
            ]
            return [v for v in pinned if v]

    # Check if package specifies an asset_pattern shim
    pat = (getattr(package, "asset_pattern", "") or "").lower() if package else ""
    if pat:
        matched = [a for a in assets if fnmatch((a.get("name") or "").lower(), pat)]
        if matched:
            has_b = any(
                any(tok in (a.get("name") or "").lower() for tok in ("_type_b", "-type-b", "type-b", " type b", "type_b", "typeb", "_b.zip", "-b.zip"))
                for a in matched
            )
            variants = [_parse_rom_asset_variant(a, tag_name, repo, has_explicit_type_assets=has_b, package=package) for a in matched]
            return [v for v in variants if v]

    # Voice files and Rockbox's ``update.zip`` are never a firmware candidate.
    firmware_assets = [
        a for a in assets if not _is_nonfirmware_zip_name(a.get("name"))
    ]
    rom_assets = [a for a in firmware_assets if _is_rom_zip_name(a.get("name"))]
    target_assets = rom_assets if rom_assets else firmware_assets
    has_explicit = any(
        any(
            tok in (a.get("name") or "").lower()
            for tok in (
                "_type_b", "-type-b", "type-b", " type b", "type_b", "typeb",
                "_b.zip", "-b.zip",
            )
        )
        for a in target_assets
    )
    if rom_assets:
        variants = [_parse_rom_asset_variant(a, tag_name, repo, has_explicit_type_assets=has_explicit, package=package) for a in rom_assets]
        return [v for v in variants if v]

    # A zip that is not named rom*.zip is a firmware candidate only for a
    # package that may use one (see _release_accepts_other_zip_names), and only
    # when it is the sole zip the release offers. A release with several
    # differently named zips gives no reliable way to tell the models apart;
    # the manifest's explicit asset_pattern (checked above) is how one is
    # named. Portable mirror bundles are build artifacts, not firmware, but a
    # mirror release that carries nothing else keeps them (see
    # _without_portable_bundles).
    if not _release_accepts_other_zip_names(
        package, repo_has_rom, model, str(selected_type or "")
    ):
        return []
    zips = [a for a in firmware_assets if (a.get("name") or "").lower().endswith(".zip")]
    portable_only = bool(zips) and all(
        _is_portable_bundle_name(a.get("name")) for a in zips
    )
    if len(zips) == 1 or portable_only:
        variants = [_parse_rom_asset_variant(a, tag_name, repo, has_explicit_type_assets=has_explicit, package=package) for a in zips]
        variants = [v for v in variants if v]
        if variants:
            return variants

    return []


def _resolution_priority(resolution, prefer_240p=False):
    """Priority for choosing a default ROM.

    When prefer_240p is True: 240p > 360p > native.
    When prefer_240p is False: 360p (native Y1) > native > 240p.
    """
    if prefer_240p:
        if resolution == "240p":
            return 0
        if resolution == "360p":
            return 1
        if resolution == "native":
            return 2
        return 3
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


def select_preferred_rom_asset(variants, selected_type=None, model=None, prefer_240p=False):
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
        if str(model).upper() == "Y2":
            model_matched = [v for v in variants if (v.get("model") or "").upper() == "Y2"]
        else:
            model_matched = [
                v for v in variants
                if v.get("model") == model or v.get("model") == "dual"
            ]
        if model_matched:
            variants = model_matched
    if selected_type:
        type_matched = [v for v in variants if v.get("type") == selected_type]
        if type_matched:
            variants = type_matched
        else:
            return None
    # Sort order (least to most significant): model exactness, whether the
    # asset name itself names the model, then resolution (honoring
    # prefer_240p). The last sort is the primary key, so a 360p build still
    # wins over a native one; within the same resolution an explicit
    # rom_y2.zip beats an unqualified rom.zip that only looked like Y2 because
    # its repository is called "...-y2". Without a selected model the original
    # resolution order is unchanged.
    if model:
        variants = sorted(variants, key=lambda v: 0 if v.get("model_explicit") else 1)
        variants = sorted(variants, key=lambda v: _model_priority(v.get("model"), model))
    variants = sorted(
        variants,
        key=lambda v: _resolution_priority(
            v.get("resolution"), prefer_240p=prefer_240p
        ),
    )
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
    # A variant named after the selected player is that player's firmware,
    # whatever the package is: rom_g5.zip is G5 firmware for a G5 selection.
    if variant_model and variant_model == ed_model:
        return True
    selecting_a5 = "A5" in ed_model
    selecting_y2 = "Y2" in ed_model
    repo_name = (repo or "").lower().split("/")[-1]

    if selecting_a5:
        if variant_model == "A5":
            return True
        if pd in ("Y1", "Y2"):
            return False
        if variant_model == "DUAL":
            if pd == "A5" or "a5" in repo_name:
                return True
            return False
        return False

    if selecting_y2:
        if variant_model == "Y2":
            return True
        if variant_model == "DUAL" and pd == "Y2":
            return True
        return False

    # Y1 / generic selection
    if pd in ("Y2", "A5"):
        return False
    if variant_model in ("Y2", "A5"):
        return False
    return variant_model in ("Y1", "DUAL")


def filter_rom_variants_for_model(variants, model, package_model=None, repo=""):
    """Return rom*.zip variants usable by ``model`` for a catalogue package.

    Matches the upstream ``filter_rom_variants_for_model`` behaviour: explicit
    model variants (e.g. rom_y2.zip, rom_a5.zip) always win out, and dual rom.zip assets
    are gated on the package/repo context so Y2/A5 never inherit Y1-only legacy
    releases.
    """
    if not variants:
        return []
    enriched = []
    for v in variants:
        vm = (v.get("model") or "dual").upper()
        if not vm:
            continue
        enriched.append(v)
    selecting_y2 = "Y2" in (model or "").upper()
    kept = [
        v for v in enriched
        if _model_ok_for_package(v.get("model"), package_model, repo, model)
    ]
    if not kept:
        return []
    # When Y2 is selected, strictly require explicit Y2 variants;
    # when Y1 is selected, exclude anything Y2-specific.
    if selecting_y2:
        return [v for v in kept if (v.get("model") or "").upper() == "Y2"]
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


def _with_portable_packages(package, rows, platform=None):
    """Attach optional portable-mirror metadata while keeping developer download URLs.

    Updater CE always downloads firmware packages directly from the community
    firmware developers themselves (``row["download_url"]`` is never replaced
    with a portable mirror archive URL). When ``portable_zips`` is configured in
    ``slidia_manifest.xml``, the matching standalone mirror metadata is recorded
    on ``portable_url`` / ``portable_repo`` / ``portable_platform`` /
    ``portable_asset`` without altering ``download_url`` or ``asset_size``.
    """
    mirrors = portable.parse_mirror_repos(getattr(package, "portable_zips", ""))
    if not mirrors or not rows:
        return rows
    key = platform or portable.platform_key()
    if not key:
        return rows
    out = []
    for row in rows:
        match = portable.resolve(
            mirrors,
            str(row.get("tag_name") or ""),
            str(row.get("asset_name") or ""),
            platform=key,
        )
        if match:
            row = dict(row)
            row["portable_url"] = match["url"]
            row["portable_size"] = int(match.get("size") or 0)
            row["portable_repo"] = match["repo"]
            row["portable_platform"] = match["platform"]
            row["portable_asset"] = match["name"]
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Version Parsing, Display Formatting & Chronological Sorting
# (Ported from firmware_downloader.py)
# ---------------------------------------------------------------------------
def _extract_tag_timestamp(tag_name: str) -> int:
    """Extract a YYYYMMDD-HHMM timestamp from a tag name as a sortable int."""
    return versioning.extract_tag_timestamp(tag_name)


def _parse_semver(version: str):
    """Convert a dotted numeric version string into a tuple of ints."""
    return versioning.parse_semver(version)


def detect_title_start_or_end_semver(title: str) -> str | None:
    """Detect a semantic version at the start or end of a release title."""
    return versioning.detect_title_start_or_end_semver(title)


def release_effective_tag(rel: dict) -> str:
    """Return the effective release tag for display or tracking."""
    return versioning.effective_tag(rel)


def _semver_from_release_title(title: str):
    """Read the product version from a release title when its tag has none."""
    return versioning.semver_from_release_title(title)


def parse_version_designations(version_name: str) -> dict:
    """Parse version names and extract designations with flexible adjectives."""
    from .i18n import tr

    return versioning.parse_version_designations(
        version_name, label_for_os=tr("label_for_os")
    )


def is_base_release_tag(tag_name) -> bool:
    """True for base ROM tags, such as ``type-a-base``."""
    return versioning.is_base_release_tag(tag_name)


def release_version_tuple(tag_name, release_name=None):
    """Dotted version a release carries (``stable-v0.5`` → ``(0, 5)``)."""
    return versioning.release_version_tuple(tag_name, release_name=release_name)


def release_version_ok(
    repo,
    tag_name,
    model=None,
    show_old_rockbox=False,
    show_nightly=False,
    release_name=None,
    selected_type=None,
):
    """Whether a release from ``repo`` may be listed for ``model``.

    The Rockbox < 0.5 gate, the Type B exemption and the model exemptions are
    shared with the terminal session in ``src/versioning.py``.
    """
    return versioning.release_version_ok(
        repo,
        tag_name,
        model=model,
        show_old_rockbox=show_old_rockbox,
        show_nightly=show_nightly,
        release_name=release_name,
        selected_type=selected_type,
        minimums=REPO_MIN_RELEASE_VERSION,
        resolve_repo=resolve_firmware_repo,
    )


def _release_safety_blocked(package, model, selected_type, rel):
    """True when the remote release blocklist hides this release."""
    from . import release_blocklist

    tag = rel.get("tag_name", "") or ""
    is_nightly = release_version_tuple(tag) is None or bool(rel.get("prerelease"))
    return release_blocklist.is_blocked(
        release_blocklist.current_rules(),
        getattr(package, "name", ""),
        model,
        selected_type or "",
        tag,
        is_nightly,
    )


# Y1 units sold with Innioasis OS 3.0.7 pre-installed will not boot older
# Original Software builds, so those are hidden for Y1 Type A by default.
ORIGINAL_Y1_TYPE_A_MIN_VERSION = (3, 0, 7)
ORIGINAL_Y1_SLUGS = ("original-y1", "original-software-y1")


def original_y1_typea_pre_307_hidden(package, model, selected_type, tag_name, show_pre_307=False):
    """True when an Original Software release must be hidden for Y1 Type A.

    Only the Y1 Original Software package, Type A, and a tag whose dotted
    version is below 3.0.7 are affected. Untagged/nightly releases are left to
    the existing rules, and ``show_pre_307`` (the Settings opt-in) lifts it.
    """
    if show_pre_307 or package is None:
        return False
    # Manifest-built packages use ``original-software-y1``; the static catalogue
    # uses ``original-y1``. Both are the same Y1 Original Software package.
    if getattr(package, "slug", "") not in ORIGINAL_Y1_SLUGS:
        return False
    if str(model or "").upper() != "Y1" or selected_type != "A":
        return False
    version = release_version_tuple(tag_name)
    return version is not None and version < ORIGINAL_Y1_TYPE_A_MIN_VERSION


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


def classify_release_list(releases, installed_tag, installed_published="", notify_ceiling=""):
    """Mark which listed releases are the installed one and which are newer.

    Ordering is ``release_sort_key`` (semver, then datestamp, then publish
    time), not the order the rows were clicked. ``prompt`` is set only when
    something in the list is newer than the installed tag.
    """
    rows = {}
    releases = list(releases or [])
    installed_tag = installed_tag or ""
    installed_rel = None
    for rel in releases:
        if (rel.get("tag_name") or "") == installed_tag:
            installed_rel = rel
            break
    if installed_tag and installed_rel is not None:
        installed_key = release_sort_key(installed_rel)
    elif installed_tag:
        installed_key = release_sort_key({
            "tag_name": installed_tag,
            "published_at": installed_published or "",
        })
    else:
        installed_key = None
    newer = []
    for rel in releases:
        tag = rel.get("tag_name") or ""
        is_installed = bool(installed_tag) and tag == installed_tag
        is_newer = installed_key is not None and not is_installed and release_sort_key(rel) > installed_key
        rows[tag] = {"installed": is_installed, "newer": is_newer}
        if is_newer:
            newer.append(rel)
    prompt = None
    if newer:
        newest = max(newer, key=release_sort_key)
        ceiling = (notify_ceiling or "").strip()
        blocked = False
        if ceiling:
            # Compare the version, not the publish timestamp. A ceiling stored
            # as a tag has no date, and that must still hide that same release.
            blocked = release_sort_key(newest)[:3] <= release_sort_key({"tag_name": ceiling})[:3]
        if not blocked:
            prompt = {
                "newer_tag": newest.get("tag_name") or "",
                "newer_release": newest,
                "installed_tag": installed_tag,
                "installed_release": installed_rel,
            }
    return {"rows": rows, "prompt": prompt}


def release_sort_key(release):
    """Build a sortable 5-tier key so newer releases appear first."""
    return versioning.release_sort_key(release)


def _release_datestamp_label(raw: str) -> str | None:
    """Human date for a tag, or None when the tag has no datestamp."""
    return versioning.release_datestamp_label(raw)


def parse_manufacturer_rom_info(name_or_path: str) -> dict | None:
    """Identify manufacturer ROM naming patterns and extract model, version, and date.

    Returns a dict with keys: 'model', 'version', 'date', 'software_name', 'title'
    or None if the name is not a recognized manufacturer ROM pattern.
    """
    if not name_or_path:
        return None
    clean = Path(str(name_or_path).replace("\\", "/")).name.lower()

    model = ""
    if "mx3-g3" in clean or "mx3-9_g3" in clean or "mx3-9_en_g3" in clean:
        model = "G3"
    elif "mx3-g1" in clean or "mx3-9_g1" in clean or "mx3-9_en" in clean:
        model = "G1"
    elif "mx4" in clean:
        model = "G5"
    elif "r2-v" in clean or "r2_v" in clean or clean.startswith("r2"):
        model = "PR1"
    elif "r1p" in clean:
        model = "SR1"
    elif "r1_v" in clean or "r1-v" in clean or clean.startswith("r1_") or clean.startswith("r1-"):
        model = "R1"
    elif "q5" in clean or "mx2-wvga_tm" in clean:
        model = "Q5"
    elif "mx2d-wvga" in clean or "mx2e-wvga" in clean or "mx2-es-wv" in clean:
        model = "Q3"
    elif "y1_hvga" in clean or "y1-" in clean or "y1_" in clean or clean.startswith("y1"):
        model = "Y1"
    elif "y2_v" in clean or "y2-" in clean or "y2_" in clean or "rom_y2" in clean or clean.startswith("y2"):
        model = "Y2"

    if not model:
        return None

    version = ""
    date_str = ""
    matches = re.findall(r"(?:[-_]|wv|v)(\d+(?:\.\d+)+)(?:[.-](\d{8}))?", clean)
    if matches:
        version = matches[-1][0]
        date_str = matches[-1][1] or ""
    else:
        m_simple = re.search(r"[-_](?:w?v)?(\d+(?:\.\d+)*)", clean)
        if m_simple:
            version = m_simple.group(1)

    if not date_str:
        m_date = re.search(r"(\d{8})", clean)
        if m_date:
            date_str = m_date.group(1)

    title = format_install_card_title("Original Software", version, model)
    return {
        "model": model,
        "version": version,
        "date": date_str,
        "software_name": f"Original Software {version}".strip(),
        "title": title,
    }


def parse_scatter_header(scatter_path: str | Path) -> tuple[str, str]:
    """Parse (platform, project) from a MediaTek scatter file."""
    plat, proj = "", ""
    try:
        content = Path(scatter_path).read_text(encoding="utf-8", errors="ignore")
        m_plat = re.search(r"^\s*-?\s*platform\s*:\s*(\S+)", content, re.MULTILINE | re.IGNORECASE)
        if m_plat:
            plat = m_plat.group(1).strip().upper()
        m_proj = re.search(r"^\s*-?\s*project\s*:\s*(\S+)", content, re.MULTILINE | re.IGNORECASE)
        if m_proj:
            proj = m_proj.group(1).strip()
    except Exception:
        pass
    return plat, proj


def infer_model_from_scatter(platform: str, project: str) -> str:
    """Infer the hardware model from scatter header facts when the package name is unknown."""
    plat = (platform or "").strip().upper()
    proj = (project or "").strip().lower()

    if proj == "k53v1_64_bsp":
        return "G1"
    if proj == "k62v1_64_bsp":
        return "PR1"
    if proj == "eastaeon80_wet_kk":
        return "Q5"
    if proj == "j5052":
        return "SR1"
    if proj == "g368_nyx":
        return "R1"
    if proj == "eastaeon82_wet_kk":
        return "Y2"
    if proj == "eastaeon72_wet_jb3":
        return "Y1"

    if plat == "MT6753":
        return "G1"
    if plat == "MT6765":
        return "PR1"
    if plat == "MT6572":
        return "Y1"
    if plat == "MT6582" or plat == "MT6580":
        return "Y2"
    return ""


def format_install_heading(software: str, model: str = "") -> str:
    """Install-screen heading, or the generic title when no release name is known.

    A detected release keeps the pre-install ``Get your <model> ready to
    install <release>`` wording. Without one, the card names the device it is
    installing onto instead of showing a file name or an empty title.
    """
    software = str(software or "").strip()
    model = str(model or "").strip()
    if software:
        return f"Get your {model} ready to install {software}" if model else f"Get ready to install {software}"
    from .i18n import tr

    device = model or tr("flash_player_fallback")
    return tr("flash_installing_software_on").format(model=device)


_READY_HEADING_RE = re.compile(
    r"^get\s+your\s+(?P<model>.+?)\s+ready\s+to\s+install\s+(?P<software>.+)$"
    r"|^get\s+ready\s+to\s+install\s+(?P<software_alt>.+)$",
    re.IGNORECASE,
)


def software_from_install_heading(text, model: str = "") -> str:
    """The release name inside an install heading (``""`` when there is none).

    The install card's title is a heading -- ``Get your Y2 ready to install
    Rockbox 2.5`` -- and a completion screen that reads it as a release name
    either says the heading back or falls back to "The software has been
    installed on your Y2" while the name it needs is already known. This
    unwraps the heading and drops a trailing model token the sentence names
    separately.
    """
    heading = _READY_HEADING_RE.match(str(text or "").strip())
    if heading is None:
        return ""
    software = (heading.group("software") or heading.group("software_alt") or "").strip()
    if not software or software.casefold() == "software":
        return ""
    token = str(model or heading.group("model") or "").strip()
    if token:
        word = rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])"
        stripped = re.sub(rf"\bfor\s+{word}", " ", software, flags=re.IGNORECASE)
        stripped = re.sub(
            rf"\s*[-\u2013,\u00b7:/]\s*{word}", " ", stripped, flags=re.IGNORECASE
        ).strip(" -\u2013,\u00b7:/")
        if stripped:
            software = stripped
    return software


def format_install_card_title(software: str, version_label: str = "", model: str = "") -> str:
    """``{software} {version or date} for {model}``, omitting empty pieces.

    ``version_label`` is the Choose Software list string (semantic version or
    datestamp). The word "firmware" is not added; MediaTek branding uses the
    software title it already has.

    The connector between software and player is UI copy ("for" is "für" in
    German, "用" in Japanese), so it comes from the catalog rather than being
    glued on in English.
    """
    from .i18n import tr

    name = re.sub(r"\s*\([^)]*\)\s*$", "", str(software or "")).strip()
    version = str(version_label or "").strip()
    device = str(model or "").strip()
    if not name:
        return format_install_heading("", device)
    if version and version.lower() == name.lower():
        version = ""
    title = " ".join(part for part in (name, version) if part)
    if device:
        title = (
            tr("install_card_title_fmt").format(software=title, model=device)
            if title
            else device
        )
    return title


def parse_clean_release_tag_label(rel, prefer_240p=False, version_only=False) -> str:
    """The label beside a release row, matching the terminal session."""
    from .i18n import tr

    return versioning.parse_clean_release_tag_label(
        rel,
        prefer_240p=prefer_240p,
        version_only=version_only,
        label_for_os=tr("label_for_os"),
    )


def format_release_display_label(rel, prefer_240p=False):
    """Format release display label adhering to legacy firmware_downloader.py rules."""
    tag_name = (rel.get("tag_name") or "").strip()
    name = (rel.get("name") or "").strip()
    if (
        versioning.release_datestamp_is_primary(tag_name)
        and not detect_title_start_or_end_semver(name)
        and not _semver_from_release_title(name)
    ):
        # A dated build uses the date as its version; render that same shared
        # label so a suffix such as ``20261107-v2`` becomes
        # ``November 7 2026 (v2)`` here and in every other surface.
        return parse_clean_release_tag_label(rel, prefer_240p=prefer_240p)
    # A nightly title names its build day while the tag is a commit hash, so
    # the same label that reads a title's semantic version reads its date.
    if (
        name
        and versioning.release_title_datestamp(name) is not None
        and release_version_tuple(tag_name, release_name=name) is None
        and not _release_datestamp_label(tag_name)
    ):
        return parse_clean_release_tag_label(rel, prefer_240p=prefer_240p)
    version_info = parse_version_designations(tag_name)
    published_date = rel.get("published_at", "")
    is_prerelease = versioning.is_pre_release(rel)
    display_version = get_display_version(version_info, published_date, is_prerelease)

    designations = list(version_info.get("designations") or [])
    # Append (240p) only when this row's own asset is the 240p variant.
    # Such rows only exist when the Settings toggle allows them.
    is_240p = (
        rel.get("selected_resolution") == "240p"
        or "240p" in str(rel.get("asset_name", "")).lower()
    )
    if is_240p:
        if not any("240p" in d.lower() for d in designations):
            designations.append("240p")

    # If name is present and not just the raw tag, clean designations already in name
    if name and name != tag_name:
        filtered_designations = [d for d in designations if d.lower() not in name.lower()]
    else:
        filtered_designations = designations

    designation_suffix = f" ({' | '.join(filtered_designations)})" if filtered_designations else ""

    clean_v = version_info.get("clean_version")
    title_version = detect_title_start_or_end_semver(name) or (_semver_from_release_title(name) if not clean_v else None)
    if title_version:
        # The tag may be a build identifier such as ``baseline-204``; its
        # "Baseline" word is not a version designation users need to see.
        compound_os = ""
        compound_m = re.search(
            r"[-_](?:[a-zA-Z0-9]+[-_]|os[-_])?v?(\d+\.\d+(?:\.\d+)?)(?:[^\d.]|$)",
            tag_name,
            re.IGNORECASE,
        )
        if compound_m and "os" in tag_name.lower():
            from .i18n import tr
            compound_os = f" ({tr('label_for_os').format(version=compound_m.group(1))})"
        res_suffix = " (240p)" if is_240p else ""
        label = f"{title_version}{compound_os}{res_suffix}"
    elif name and name != tag_name:
        label = f"{name}{designation_suffix}"
    elif clean_v and any(d.lower() in tag_name.lower() for d in designations):
        # Tag name has embedded verbs (e.g. v0.5.1-ADB); format with clean version and designations
        v_prefix = "v" if tag_name.lower().startswith("v") else ""
        label = f"{v_prefix}{clean_v}{designation_suffix}"
    elif display_version and display_version != tag_name and not tag_name.startswith("v"):
        label = f"{display_version}{designation_suffix}"
    else:
        label = f"{tag_name}{designation_suffix}"

    if is_prerelease and "pre-release" not in label.lower():
        label += " (Pre-release)"
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
        self.session = requests_module().Session()
        self.session.headers.update({"Accept": "application/vnd.github+json"})
        self.unauth_calls = []
        self.unauth_hourly_limit = 55

    # -- cache --------------------------------------------------------------
    def _cache_path(self, repo):
        return self.cache_dir / f"{repo.replace('/', '_')}.json"

    def get_cached_releases(self, repo, ignore_ttl=False, show_nightly=False):
        """Cached releases for ``repo``, with the repo's minimum-version gate
        applied so a cache written before the gate existed cannot resurface a
        release users are no longer allowed to install.

        ``show_nightly`` keeps dated/nightly rows in the cache for a listing;
        the listing itself applies the show-dev filter. Cache files always hold
        every row a fetch produced, so filtering them out here made nightlies
        disappear as soon as a listing was served from disk.
        """
        path = self._cache_path(repo)
        try:
            if path.exists() and (ignore_ttl or (time.time() - path.stat().st_mtime) < RELEASE_CACHE_TTL_SECONDS):
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return [
                        r for r in data
                        if isinstance(r, dict)
                        and release_version_ok(
                            repo,
                            r.get("tag_name", ""),
                            show_old_rockbox=True,
                            show_nightly=show_nightly,
                            release_name=r.get("name"),
                        )
                        # A cache written before base tags were filtered out
                        # still holds them; they must never reach a list.
                        and not is_base_release_tag(r.get("tag_name", ""))
                        # A listing read keeps pre-releases and lets the
                        # listing's filter decide; other readers (the latest
                        # release, app updates) keep normal builds only.
                        and (show_nightly or not versioning.is_pre_release(r))
                    ]
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
        from .pat_pool import get_random_pats, report_pat_status

        # 1. Try explicit user token first if configured
        candidate_tokens = []
        if self.token:
            candidate_tokens.append(self.token)

        # 2. Add 3 random PATs from the config.ini PAT pool
        pats = get_random_pats(count=3)
        for p in pats:
            if p not in candidate_tokens:
                candidate_tokens.append(p)

        last_err = None
        for tok in candidate_tokens:
            headers = {"Authorization": f"Bearer {tok}"}
            try:
                resp = self.session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (401, 403, 429):
                    report_pat_status(tok, resp.status_code)
                    logger.debug("GitHub token %s... returned HTTP %d; trying next candidate", tok[:15], resp.status_code)
                    last_err = resp.status_code
                    continue
                last_err = resp.status_code
            except requests_module().RequestException as e:
                last_err = e
                continue

        # 3. Fallback to unauthenticated request if quota allows
        if self._can_unauth():
            self._record_unauth()
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    return resp.json()
                last_err = resp.status_code
            except requests_module().RequestException as e:
                last_err = e

        logger.debug("GitHub request failed for %s: %s", url, last_err)
        return None

    def _fetch_expanded_assets(self, repo, tag):
        """Parse downloadable assets from https://github.com/{repo}/releases/expanded_assets/{tag}."""
        url = f"https://github.com/{repo}/releases/expanded_assets/{tag}"
        assets = []
        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                pattern = re.compile(r'href="([^"]+/releases/download/[^"]+/([^"]+))"')
                seen = set()
                for match in pattern.finditer(resp.text):
                    path, name = match.group(1), match.group(2)
                    if name in seen:
                        continue
                    seen.add(name)
                    download_url = f"https://github.com{path}" if path.startswith("/") else path
                    assets.append({
                        "name": name,
                        "browser_download_url": download_url,
                        "size": 0,
                    })
        except Exception as e:
            logger.debug("Expanded assets fetch failed for %s/%s: %s", repo, tag, e)
        from .config import UPDATE_REPO
        if not assets and repo != UPDATE_REPO:
            for name in ("rom.zip", "rom_y2.zip", "rom_a5.zip"):
                assets.append({
                    "name": name,
                    "browser_download_url": f"https://github.com/{repo}/releases/download/{tag}/{name}",
                    "size": 0,
                })
        return assets

    def _fetch_backup_releases(self, repo):
        """Load 1:1 releases for ``repo`` from the innioasis.app backup feeds or bundled snapshot."""
        lower_repo = str(repo or "").strip().lower()
        if "/" not in lower_repo:
            return []

        # 1. Try per-repo 1:1 JSON backup feed from innioasis.app / website repo
        try:
            from .config import site_source_urls

            sess = getattr(self, "_session", None)
            if sess is not None or hasattr(self, "session"):
                for feed_url in site_source_urls(f"feeds/{lower_repo}/releases.json"):
                    try:
                        resp = self.session.get(feed_url, timeout=min(4.0, REQUEST_TIMEOUT))
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list) and data:
                                return data
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("Online 1:1 backup feed fetch failed for %s: %s", repo, e)

        # 2. Try bundled/cached master 1:1 JSON backup store (releases_backup.json)
        try:
            from . import online_data

            backup_text = online_data.read_text("releases_backup.json")
            if backup_text:
                parsed = json.loads(backup_text)
                repos_map = parsed.get("repos", {}) if isinstance(parsed, dict) else {}
                for k, v in repos_map.items():
                    if k.lower() == lower_repo and isinstance(v, dict):
                        rels = v.get("releases")
                        if isinstance(rels, list) and rels:
                            return rels
        except Exception as e:
            logger.debug("Local 1:1 JSON backup store read failed for %s: %s", repo, e)

        # 3. Try bundled/cached combined XML backup store (releases.xml)
        try:
            import xml.etree.ElementTree as ET
            from . import online_data

            xml_text = online_data.read_text("releases.xml")
            if xml_text:
                root = ET.fromstring(xml_text)
                for repo_el in root.findall("repository"):
                    if (repo_el.attrib.get("name") or "").lower() != lower_repo:
                        continue
                    out = []
                    for rel_el in repo_el.findall("release"):
                        body_el = rel_el.find("body")
                        assets_el = rel_el.find("assets")
                        assets = []
                        if assets_el is not None:
                            for a_el in assets_el.findall("asset"):
                                assets.append({
                                    "id": int(a_el.attrib.get("id") or 0),
                                    "name": a_el.attrib.get("name", ""),
                                    "size": int(a_el.attrib.get("size") or 0),
                                    "download_count": int(a_el.attrib.get("download_count") or 0),
                                    "content_type": a_el.attrib.get("content_type", ""),
                                    "browser_download_url": a_el.attrib.get("browser_download_url", ""),
                                })
                        out.append({
                            "id": int(rel_el.attrib.get("id") or 0),
                            "tag_name": rel_el.attrib.get("tag_name", ""),
                            "name": rel_el.attrib.get("name", ""),
                            "body": body_el.text if body_el is not None and body_el.text else "",
                            "published_at": rel_el.attrib.get("published_at", ""),
                            "prerelease": (rel_el.attrib.get("prerelease") or "").lower() == "true",
                            "html_url": rel_el.attrib.get("html_url", ""),
                            "icon_url": rel_el.attrib.get("icon_url", ""),
                            "icon_dark_url": rel_el.attrib.get("icon_dark_url", ""),
                            "assets": assets,
                        })
                    if out:
                        return out
        except Exception as e:
            logger.debug("Local 1:1 XML backup store read failed for %s: %s", repo, e)

        return []

    def _parse_atom_feed_xml(self, xml_text, repo):
        """Parse an Atom 1.0 release feed (supporting both innioasis:enclosure and GitHub Atom)."""
        import xml.etree.ElementTree as ET

        root = ET.fromstring(xml_text)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "innioasis": "https://innioasis.app/ns/feeds/1.0",
        }
        raw_releases = []
        for entry in root.findall("atom:entry", ns):
            title_el = entry.find("atom:title", ns)
            title = title_el.text if title_el is not None and title_el.text else ""
            content_el = entry.find("atom:content", ns)
            body = content_el.text if content_el is not None and content_el.text else ""
            updated_el = entry.find("atom:updated", ns)
            pub_date = updated_el.text if updated_el is not None and updated_el.text else ""

            tag_el = entry.find("innioasis:tag_name", ns)
            tag = (tag_el.text or "").strip() if tag_el is not None else ""
            html_url = ""
            enclosure_assets = []
            for link in entry.findall("atom:link", ns):
                rel_attr = link.attrib.get("rel", "")
                href = link.attrib.get("href", "")
                if rel_attr == "enclosure" and href:
                    enclosure_assets.append({
                        "id": int(link.attrib.get("{https://innioasis.app/ns/feeds/1.0}asset_id") or 0),
                        "name": link.attrib.get("title") or href.rsplit("/", 1)[-1],
                        "browser_download_url": href,
                        "size": int(link.attrib.get("length") or 0),
                        "download_count": int(link.attrib.get("{https://innioasis.app/ns/feeds/1.0}download_count") or 0),
                        "content_type": link.attrib.get("type", "application/octet-stream"),
                    })
                elif "/releases/tag/" in href:
                    html_url = href
                    if not tag:
                        tag = href.split("/releases/tag/")[-1]
            if not tag:
                id_el = entry.find("atom:id", ns)
                if id_el is not None and id_el.text and "Release/" in id_el.text:
                    tag = id_el.text.split("Release/")[-1]
            if not tag:
                continue

            pre_el = entry.find("innioasis:prerelease", ns)
            if pre_el is not None and pre_el.text is not None:
                prerelease = pre_el.text.strip().lower() == "true"
            else:
                prerelease = "nightly" in tag.lower() or "preview" in tag.lower()

            icon_el = entry.find("innioasis:icon_url", ns)
            icon_dark_el = entry.find("innioasis:icon_dark_url", ns)
            assets = enclosure_assets if enclosure_assets else self._fetch_expanded_assets(repo, tag)
            raw_releases.append({
                "tag_name": tag,
                "name": title,
                "body": body,
                "published_at": pub_date,
                "html_url": html_url or f"https://github.com/{repo}/releases/tag/{tag}",
                "prerelease": prerelease,
                "icon_url": (icon_el.text or "").strip() if icon_el is not None else "",
                "icon_dark_url": (icon_dark_el.text or "").strip() if icon_dark_el is not None else "",
                "assets": assets,
            })
        return raw_releases

    def _fetch_releases_atom(self, repo):
        """Fetch releases via public Atom feed (innioasis.app 1:1 Atom feed first, then GitHub Atom)."""
        lower_repo = str(repo or "").strip().lower()
        urls = [
            f"https://innioasis.app/feeds/{lower_repo}/releases.atom",
            f"https://github.com/{repo}/releases.atom",
        ]
        for url in urls:
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code != 200:
                    continue
                parsed = self._parse_atom_feed_xml(resp.text, repo)
                if parsed:
                    return parsed
            except Exception as e:
                logger.debug("Atom release fallback failed for %s (%s): %s", repo, url, e)
        return []

    # -- fetching -----------------------------------------------------------
    def get_latest_release(self, repo, force_refresh=False):
        repo = resolve_firmware_repo(repo)
        self.last_used_backup = False
        self.last_fetch_failed = False
        if not force_refresh:
            cached = self.get_cached_releases(repo)
            if cached:
                return cached[0]
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
        data = None
        if self.token:
            data = self._get_json(url)
        elif self._can_unauth():
            self._record_unauth()
            data = self._get_json(url)

        if data:
            result = self._normalize_release(data, repo)
            if result:
                self.cache_releases(repo, [result])
                return result

        # Fallback to cache
        cached = self.get_cached_releases(repo, ignore_ttl=True)
        if cached:
            self.last_used_backup = True
            return cached[0]

        # Fallback to 1:1 backup store (JSON / XML) or public Atom feed
        for fallback_raw in (self._fetch_backup_releases(repo), self._fetch_releases_atom(repo)):
            if fallback_raw:
                releases = self._build_releases(fallback_raw, repo)
                if releases:
                    self.last_used_backup = True
                    releases = sorted(releases, key=release_sort_key, reverse=True)
                    self.cache_releases(repo, releases)
                    return releases[0]
        self.last_fetch_failed = True
        return None

    def get_all_releases(self, repo, force_refresh=False):
        repo = resolve_firmware_repo(repo)
        self.last_used_backup = False
        self.last_fetch_failed = False
        if not force_refresh:
            cached = self.get_cached_releases(repo, show_nightly=True)
            if cached:
                logger.info("Using %d cached releases for %s", len(cached), repo)
                return sorted(cached, key=release_sort_key, reverse=True)[:100]

        url = f"{GITHUB_API}/repos/{repo}/releases?per_page={GITHUB_RELEASES_PER_PAGE}"
        data = None
        if self.token:
            data = self._get_json(url)
        elif self._can_unauth():
            self._record_unauth()
            data = self._get_json(url)

        if data is not None:
            releases = self._build_releases(data, repo)
            if releases:
                releases = sorted(releases, key=release_sort_key, reverse=True)
                self.cache_releases(repo, releases)
                return releases

        # Fallback to cached releases if available
        cached = self.get_cached_releases(repo, ignore_ttl=True, show_nightly=True)
        if cached:
            self.last_used_backup = True
            logger.info("Using %d cached releases for %s after API fallback", len(cached), repo)
            return sorted(cached, key=release_sort_key, reverse=True)

        # Fallback to 1:1 backup store (JSON / XML) or public Atom feed (never rate-limited)
        for source_label, fallback_raw in (
            ("1:1 backup store", self._fetch_backup_releases(repo)),
            ("Atom feed", self._fetch_releases_atom(repo)),
        ):
            if fallback_raw:
                logger.info("Fetched %d releases via %s for %s", len(fallback_raw), source_label, repo)
                releases = self._build_releases(fallback_raw, repo)
                if releases:
                    self.last_used_backup = True
                    releases = sorted(releases, key=release_sort_key, reverse=True)
                    self.cache_releases(repo, releases)
                    return releases

        self.last_fetch_failed = True
        return []

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
                # A cached "latest" written before base tags were filtered
                # must not be handed to the update check either.
                if isinstance(data, dict) and not is_base_release_tag(data.get("tag_name", "")):
                    return data
        except Exception as e:
            logger.debug("Update cache read failed for %s: %s", repo, e)
        return None

    def get_all_update_releases(self, repo, force_refresh: bool = False) -> list:
        """Fetch all releases for an application update repo (not rom*.zip firmware).

        Returns raw releases with normalized assets list:
        [{'tag_name': ..., 'name': ..., 'body': ..., 'published_at': ..., 'html_url': ..., 'assets': [...]}, ...]
        Ordered from newest to oldest. Falls back to Atom feed when API is unavailable.
        """
        repo = resolve_firmware_repo(repo)
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page=30"
        data = self._get_json(url)
        releases = []
        if isinstance(data, list) and data:
            for r in data:
                if is_base_release_tag(r.get("tag_name", "")):
                    continue
                releases.append({
                    "tag_name": r.get("tag_name", ""),
                    "name": r.get("name", ""),
                    "body": r.get("body", ""),
                    "published_at": r.get("published_at", ""),
                    "html_url": r.get("html_url", ""),
                    "assets": [
                        {
                            "name": a.get("name", ""),
                            "browser_download_url": a.get("browser_download_url", ""),
                            "size": a.get("size", 0),
                        }
                        for a in (r.get("assets") or [])
                    ],
                })
            return releases

        # Fallback to 1:1 backup store or public Atom feed
        for fallback_raw in (self._fetch_backup_releases(repo), self._fetch_releases_atom(repo)):
            if fallback_raw:
                for item in fallback_raw:
                    if is_base_release_tag(item.get("tag_name", "")):
                        continue
                    releases.append({
                        "tag_name": item.get("tag_name", ""),
                        "name": item.get("name", ""),
                        "body": item.get("body", ""),
                        "published_at": item.get("published_at", ""),
                        "html_url": item.get("html_url", ""),
                        "assets": item.get("assets", []),
                    })
                if releases:
                    return releases
        return releases

    def get_latest_release_info(self, repo, force: bool = False):
        """Latest release of an app repo (not a firmware repo) as a plain dict:
        tag_name, name, body, published_at, html_url, assets[]. Falls back to
        the cache and never raises; returns None when nothing is available."""
        repo = resolve_firmware_repo(repo)
        cached = self.get_cached_update(repo)
        if cached and not force:
            return cached

        all_rels = self.get_all_update_releases(repo, force_refresh=force)
        if all_rels:
            result = all_rels[0]
            try:
                self._updates_cache_path(repo).write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8"
                )
            except Exception as e:
                logger.debug("Update cache write failed for %s: %s", repo, e)
            return result

        return cached

    # -- normalization ------------------------------------------------------
    def _normalize_release(self, release, repo):
        tag_name = release.get("tag_name", "")
        if is_base_release_tag(tag_name):
            return None
        if not release_version_ok(repo, tag_name, show_nightly=True, show_old_rockbox=True, release_name=release.get("name")):
            return None
        assets = release.get("assets", [])
        rom_variants = _release_rom_variants(assets, tag_name, repo)
        preferred = select_preferred_rom_asset(rom_variants)
        if not preferred:
            return None
        out = {
            "tag_name": tag_name,
            "name": release.get("name", ""),
            "body": release.get("body", ""),
            "published_at": release.get("published_at", ""),
            "download_url": preferred["asset"]["browser_download_url"],
            "asset_name": preferred["asset"]["name"],
            "asset_size": preferred["asset"].get("size", 0),
            "assets": assets,
            "rom_variants": rom_variants,
            "prerelease": versioning.is_pre_release(release),
            "source_repo": repo,
        }
        if release.get("icon_url"):
            out["icon_url"] = release["icon_url"]
        if release.get("icon_dark_url"):
            out["icon_dark_url"] = release["icon_dark_url"]
        return out

    def _build_releases(self, releases_data, repo):
        releases = []
        # Non-rom zips are only offered when no release of this repo has a rom*.zip.
        repo_has_rom = any(
            _is_rom_zip_name(a.get("name"))
            for release in releases_data or []
            for a in release.get("assets", [])
        )
        for release in releases_data or []:
            tag_name = release.get("tag_name", "")
            if is_base_release_tag(tag_name):
                continue
            if not release_version_ok(repo, tag_name, show_nightly=True, show_old_rockbox=True, release_name=release.get("name")):
                continue
            is_prerelease = versioning.is_pre_release(release)
            assets = release.get("assets", [])
            rom_variants = _release_rom_variants(assets, tag_name, repo, repo_has_rom)
            preferred = select_preferred_rom_asset(rom_variants)
            if not preferred:
                continue
            row = {
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
                "html_url": release.get("html_url", ""),
            }
            if release.get("icon_url"):
                row["icon_url"] = release["icon_url"]
            if release.get("icon_dark_url"):
                row["icon_dark_url"] = release["icon_dark_url"]
            releases.append(row)
        return releases

    def releases_for_package(
        self,
        package: FirmwarePackage,
        model: str,
        show_nightly=False,
        selected_type=None,
        show_old_rockbox=False,
        prefer_240p=False,
        force_refresh=False,
        show_original_pre_307=False,
        show_safety_blocked=False,
    ):
        """Fetch releases for a catalogue package, filtered for the device model.

        Only releases that carry a ``rom*.zip`` asset compatible with ``model``
        (exact match or ``dual``) are included. When ``selected_type`` is
        given (e.g. 'A' or 'B'), only releases with a matching hardware type
        variant are included.
        """
        info_repo = getattr(package, "info_repo", "") or ""
        apk_repo = getattr(package, "apk_repo", "") or ""
        if info_repo or apk_repo:
            return self._apk_repo_releases(
                package, model, info_repo or apk_repo, force_refresh=force_refresh,
                selected_type=selected_type, show_nightly=show_nightly,
                show_old_rockbox=show_old_rockbox, prefer_240p=prefer_240p,
                show_original_pre_307=show_original_pre_307,
                show_safety_blocked=show_safety_blocked,
            )
        releases = self.get_all_releases(package.repo, force_refresh=force_refresh)
        # Whether this repository publishes rom*.zip at all decides whether a
        # differently named zip may stand in for firmware (see
        # _release_accepts_other_zip_names). Read from every release, so one
        # voice-file release cannot present a repo as a non-rom publisher.
        repo_has_rom = any(
            _is_rom_zip_name(asset.get("name"))
            for rel in releases or []
            if isinstance(rel, dict)
            for asset in (rel.get("assets") or [])
            if isinstance(asset, dict)
        )
        # A software product that publishes only pre-releases (a nightly-only
        # tree, or a port still in development) has nothing else to offer, so
        # hiding them would show an empty list. The show-dev filter only hides
        # a pre-release when a normal release exists beside it.
        has_stable = any(
            isinstance(rel, dict)
            and not versioning.is_pre_release(rel)
            and _release_matches_model(model, rel, package, selected_type)
            for rel in releases
        )
        out = []
        for rel in releases:
            tag_name = rel.get("tag_name", "")
            if is_base_release_tag(tag_name):
                # Base ROMs are build inputs, never installable firmware.
                continue
            if not release_version_ok(
                package.repo,
                tag_name,
                model=model,
                show_old_rockbox=show_old_rockbox,
                show_nightly=show_nightly,
                release_name=rel.get("name"),
                selected_type=selected_type,
            ):
                continue
            if original_y1_typea_pre_307_hidden(
                package, model, selected_type, tag_name, show_original_pre_307
            ):
                continue
            if not _release_matches_model(model, rel, package, selected_type):
                continue
            if not show_safety_blocked and _release_safety_blocked(package, model, selected_type, rel):
                continue
            if versioning.is_pre_release(rel) and not show_nightly and has_stable:
                if not (package.slug == "rockbox-y2" or package.repo == "spycemagic/y2-rockbox" or (str(model or "").upper() == "Y2" and "rockbox" in package.slug)):
                    continue
            # Re-pick the preferred asset with the model filter so the
            # download URL points to the correct rom*.zip for this model.
            # Rebuild the variants from the raw assets when they are present:
            # a release cache written before an asset_pattern shim existed
            # still contains the old rom_variants, which would keep pointing
            # at the wrong model's zip until the cache expired.
            raw_assets = rel.get("assets") or []
            source_repo = rel.get("source_repo", "") or package.repo
            if raw_assets:
                rom_variants = _release_rom_variants(
                    raw_assets,
                    tag_name,
                    source_repo,
                    repo_has_rom=repo_has_rom,
                    package=package,
                    model=model,
                    selected_type=selected_type,
                )
                if not rom_variants and not getattr(package, "asset_pattern", ""):
                    # Rows built by the APK path carry application assets, not
                    # firmware zips; their rom_variants were prepared already.
                    rom_variants = list(rel.get("rom_variants") or [])
            else:
                rom_variants = list(rel.get("rom_variants") or [])
            if not _release_accepts_other_zip_names(package, repo_has_rom):
                # A row cached before its release stopped shipping rom*.zip
                # must not keep offering the voice pack it was built from.
                rom_variants = [
                    v
                    for v in rom_variants
                    if _is_rom_zip_name(str((v.get("asset") or {}).get("name") or ""))
                ]
            rom_variants = filter_rom_variants_for_model(
                rom_variants,
                model,
                package.model,
                rel.get("source_repo", "") or "",
            )
            if selected_type:
                rom_variants = [v for v in rom_variants if v.get("type") == selected_type]
            if not rom_variants:
                continue
            if not prefer_240p and rom_variants and all(
                v.get("resolution") == "240p" for v in rom_variants
            ):
                # 240p builds (rom*_240p.zip) drive the 240p screen mod and are
                # incompatible with Y1 units older than OS 3.0.7, so they stay
                # out of the list until the Settings toggle asks for them.
                continue
            if prefer_240p:
                # The toggle is on: list the 240p variant as its own row with
                # a "(240p)" suffix, next to the standard (360p) row, so the
                # two ROMs for the same tag are distinguishable. Each row
                # carries its own download asset, honouring the Type A/B pick.
                std_variants = [v for v in rom_variants if v.get("resolution") != "240p"]
                low_variants = [v for v in rom_variants if v.get("resolution") == "240p"]
                std_pick = (
                    select_preferred_rom_asset(
                        std_variants, selected_type=selected_type, model=model,
                    )
                    if std_variants
                    else None
                )
                low_pick = (
                    select_preferred_rom_asset(
                        low_variants, selected_type=selected_type, model=model,
                        prefer_240p=True,
                    )
                    if low_variants
                    else None
                )
                for pick in (std_pick, low_pick):
                    if not pick:
                        continue
                    row = dict(rel)  # shallow copy, don't mutate cache
                    row["download_url"] = pick["asset"]["browser_download_url"]
                    row["asset_name"] = pick["asset"]["name"]
                    row["asset_size"] = pick["asset"].get("size", 0)
                    row["selected_resolution"] = pick.get("resolution")
                    row["prefer_240p"] = True
                    out.append(row)
                continue
            preferred = select_preferred_rom_asset(
                rom_variants,
                selected_type=selected_type,
                model=model,
                prefer_240p=prefer_240p,
            )
            if preferred:
                rel = dict(rel)  # shallow copy, don't mutate cache
                rel["download_url"] = preferred["asset"]["browser_download_url"]
                rel["asset_name"] = preferred["asset"]["name"]
                rel["asset_size"] = preferred["asset"].get("size", 0)
                rel["selected_resolution"] = preferred.get("resolution")
                rel["prefer_240p"] = prefer_240p
            out.append(rel)
        return sorted(
            _with_portable_packages(package, out), key=release_sort_key, reverse=True
        )

    def _apk_repo_releases(self, package, model, apk_source, force_refresh=False, **filters):
        """Version rows for a package whose ``info_repo`` or ``apk_repo`` names
        the application repository.

        The rows are that repository's releases carrying an ``*.apk`` asset, so
        their versions and release notes come from the application. Each row
        installs the latest firmware from the package's ``repo`` (the firmware
        that is actually flashed). Where ``info_repo`` or ``apk_repo`` now contains
        zips matching firmware criteria, it is treated as ``repo`` directly. An
        ``info_repo`` package with no post-install page has no instructions for its
        APK, so it lists nothing.
        """
        apk_releases = self.get_all_update_releases(apk_source, force_refresh=force_refresh)
        # If the target repository now publishes zips matching firmware criteria,
        # treat info_repo / apk_repo as repo directly.
        has_firmware_zips = any(
            _release_rom_variants(
                rel.get("assets") or [],
                str(rel.get("tag_name", "") or ""),
                apk_source,
            )
            for rel in apk_releases
        )
        if has_firmware_zips:
            direct_firmware = self.releases_for_package(
                dataclasses.replace(package, repo=apk_source, info_repo="", apk_repo=""),
                model,
                force_refresh=force_refresh,
                **filters,
            )
            if direct_firmware:
                return direct_firmware

        if package.info_repo and not (package.post_install or package.post_install_2):
            return []
        firmware = self.releases_for_package(
            dataclasses.replace(package, info_repo="", apk_repo=""), model, force_refresh=force_refresh, **filters,
        )
        if not firmware:
            return []
        latest = firmware[0]
        out = []
        for rel in apk_releases:
            assets = rel.get("assets") or []
            apk_assets = [a for a in assets if str(a.get("name", "")).lower().endswith(".apk")]
            if not apk_assets:
                continue
            row = dict(rel)  # shallow copy, don't mutate cache
            row["source_repo"] = apk_source
            row["info_only"] = True
            row["apk_repo"] = str(getattr(package, "apk_repo", "") or "")
            apk_asset = apk_assets[0]
            row["apk_url"] = str(apk_asset.get("browser_download_url") or "")
            row["apk_name"] = str(apk_asset.get("name") or "")
            row["apk_size"] = int(apk_asset.get("size") or 0)
            for key in (
                "download_url",
                "asset_name",
                "asset_size",
                "selected_resolution",
                "prefer_240p",
                "portable_repo",
                "portable_platform",
                "portable_asset",
            ):
                if key in latest:
                    row[key] = latest[key]
            row["firmware_tag_name"] = latest.get("tag_name", "")
            out.append(row)
        return sorted(out, key=release_sort_key, reverse=True)

    def available_release_rows(self, repo):
        """Raw release rows for the catalogue's availability gate, or ``None``.

        Deliberately one request, without :meth:`get_all_releases`'s backup
        feed and Atom fallbacks: proving that a repository has *nothing*
        published must not cost several more requests each time the manifest
        loads. ``None`` means the answer could not be obtained (offline,
        rate-limited, repository missing); an empty list is a real answer.
        """
        repo = resolve_firmware_repo(repo)
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page={GITHUB_RELEASES_PER_PAGE}"
        data = self._get_json(url)
        if not isinstance(data, list):
            return None
        if data:
            releases = self._build_releases(data, repo)
            if releases:
                # The same rows serve the listing when the user picks this
                # software, so the check is not extra work for it.
                self.cache_releases(
                    repo, sorted(releases, key=release_sort_key, reverse=True)
                )
        return data


# ---------------------------------------------------------------------------
# Catalogue availability: a listed firmware must lead to a release
# ---------------------------------------------------------------------------
#: How long a live answer about a repository is reused. The bundled release
#: snapshot answers most entries without touching the network; this only
#: bounds re-checks for repositories the snapshot does not cover yet.
RELEASE_AVAILABILITY_TTL_SECONDS = 30 * 60

#: A release snapshot older than this is advisory: it may keep a source but
#: never hide one by itself. ``releases_backup.json`` is a periodic export, so a
#: repository added to the live manifest since the export has no row in it, and
#: its first stable release may already be on GitHub.
RELEASE_SNAPSHOT_HIDE_MAX_AGE_SECONDS = RELEASE_AVAILABILITY_TTL_SECONDS

_SNAPSHOT_CACHE: tuple | None = None
_AVAILABILITY_CACHE: dict = {}


def _release_snapshot_path():
    """The release backup to read: downloaded copy first, then bundled copy."""
    from . import online_data

    for getter in (online_data.cached_path, online_data.bundled_path):
        try:
            path = getter("releases_backup.json")
        except Exception:
            continue
        if path is None:
            continue
        try:
            if path.is_file() and path.stat().st_size > 0:
                return path
        except OSError:
            continue
    return None


def _release_snapshot_rows():
    """Raw release rows from ``releases_backup.json`` keyed by repository.

    ``None`` means the snapshot itself is unreadable, in which case a dead
    entry and a brand-new one look the same and nothing may be hidden. A
    repository mapped to ``[]`` is known to publish no releases.
    """
    global _SNAPSHOT_CACHE
    path = _release_snapshot_path()
    if path is None:
        return None
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    if _SNAPSHOT_CACHE is not None and _SNAPSHOT_CACHE[0] == key:
        return _SNAPSHOT_CACHE[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception as e:
        logger.debug("Release snapshot read failed: %s", e)
        return None
    raw_repos = data.get("repos") if isinstance(data, dict) else None
    rows_by_repo: dict = {}
    if isinstance(raw_repos, dict):
        for repo, value in raw_repos.items():
            releases = value.get("releases") if isinstance(value, dict) else value
            if isinstance(releases, list):
                rows_by_repo[str(repo).strip().lower()] = releases
    _SNAPSHOT_CACHE = (key, rows_by_repo)
    return rows_by_repo


def _rows_have_installable_release(package, rows) -> bool:
    """True when ``rows`` hold a release with a zip usable by ``package``."""
    repo = str(getattr(package, "repo", "") or "").strip()
    model = str(getattr(package, "model", "") or "").strip()
    releases = [row for row in (rows or []) if isinstance(row, dict)]
    if not releases:
        return False
    repo_has_rom = any(
        _is_rom_zip_name(asset.get("name"))
        for release in releases
        for asset in (release.get("assets") or [])
        if isinstance(asset, dict)
    )
    for release in releases:
        tag = str(release.get("tag_name") or "").strip()
        if not tag or is_base_release_tag(tag):
            continue
        if not release_version_ok(
            repo,
            tag,
            model=model,
            show_nightly=True,
            show_old_rockbox=True,
            release_name=release.get("name"),
        ):
            continue
        variants = _release_rom_variants(
            release.get("assets") or [],
            tag,
            repo,
            repo_has_rom=repo_has_rom,
            package=package,
            model=model,
        )
        if filter_rom_variants_for_model(variants, model, model, repo):
            return True
    return False


def _availability_key(package) -> tuple:
    return (
        str(getattr(package, "repo", "") or "").strip().lower(),
        str(getattr(package, "model", "") or "").strip().upper(),
    )


def _cached_availability(package):
    hit = _AVAILABILITY_CACHE.get(_availability_key(package))
    if hit is None:
        return None
    checked_at, usable = hit
    if (time.time() - checked_at) >= RELEASE_AVAILABILITY_TTL_SECONDS:
        return None
    return usable


def _remember_availability(package, usable) -> None:
    _AVAILABILITY_CACHE[_availability_key(package)] = (time.time(), bool(usable))


def _release_snapshot_age():
    """Age in seconds of the release snapshot used by the availability gate.

    ``None`` means there is no snapshot (the gate then keeps every entry).
    """
    path = _release_snapshot_path()
    if path is None:
        return None
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def filter_unavailable_packages(entries, *, allow_network: bool = False, client=None):
    """Keep only catalogue entries that can list an installable release.

    ``slidia_manifest.xml`` is maintained by hand, so it can name a repository
    that has published no releases yet, or whose releases carry no firmware zip
    for the model it lists. Such an entry only ever produces an empty Select
    Software listing, so it is not offered at all.

    The answer comes from the bundled/downloaded release snapshot, then the
    per-repo release cache, then (with ``allow_network``) one GitHub request per
    still-unknown repository. When no source can answer — no snapshot, offline,
    rate-limited — the entry is kept: an unverifiable firmware must not vanish
    from the catalogue.
    """
    entries = list(entries or [])
    if not entries:
        _remember_unavailable([])
        return entries
    snapshot = _release_snapshot_rows()
    if snapshot is None:
        return entries
    # A repository added to the live manifest since the export has no row in
    # it, and a newly published stable release may not be in it either. Only a
    # fresh export may hide a source on its own.
    snapshot_age = _release_snapshot_age()
    snapshot_may_hide = (
        snapshot_age is not None
        and snapshot_age <= RELEASE_SNAPSHOT_HIDE_MAX_AGE_SECONDS
    )

    network_client = client
    client_ready = client is not None

    def _client():
        nonlocal network_client, client_ready
        if not client_ready:
            client_ready = True
            try:
                network_client = ReleasesClient()
            except Exception:
                logger.debug("Release availability client unavailable", exc_info=True)
                network_client = None
        return network_client

    kept, dropped = [], []
    for package in entries:
        repo = str(getattr(package, "repo", "") or "").strip()
        repo_key = repo.lower()
        # A fresh snapshot answers without a request; its "no" is confirmed
        # against the release cache and, when allowed, the live API below.
        snapshot_rows = snapshot.get(repo_key)
        snapshot_verdict = None
        if snapshot_rows is not None:
            snapshot_verdict = _rows_have_installable_release(package, snapshot_rows)
            usable = snapshot_verdict
        else:
            usable = _cached_availability(package)

        # The per-repository release cache is written by a live fetch, so a
        # usable row in it is newer evidence than the periodic export.
        cached_rows = None
        if usable is not True:
            cache_client = _client()
            if cache_client is not None:
                try:
                    cached_rows = cache_client.get_cached_releases(
                        repo, show_nightly=True
                    )
                except Exception:
                    cached_rows = None
        if cached_rows:
            cached_usable = _rows_have_installable_release(package, cached_rows)
            if cached_usable:
                usable = True
            elif usable is None:
                usable = False
        if usable is False and snapshot_verdict is False and not snapshot_may_hide:
            # An old export must never hide a source by itself. Ask live below
            # when that is allowed; otherwise fail open and keep the entry.
            usable = None
        if usable is None and allow_network and repo:
            live_client = _client()
            if live_client is not None:
                try:
                    live_rows = live_client.available_release_rows(repo)
                except Exception:
                    live_rows = None
                if live_rows is not None:
                    usable = _rows_have_installable_release(package, live_rows)
                    _remember_availability(package, usable)
        if usable is False:
            dropped.append(package)
        else:
            kept.append(package)

    _remember_unavailable(dropped)
    if dropped:
        logger.info(
            "Catalogue: %d entr%s publish no installable release; hidden: %s",
            len(dropped),
            "y" if len(dropped) == 1 else "ies",
            ", ".join(f"{p.model} {p.name}" for p in dropped),
        )
    return kept


def _app_cache_dir():
    """Per-user cache dir: 'Updater CE' on Windows, the shared
    'Innioasis Updater' Application Support folder on macOS."""
    import sys

    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Updater CE" / ".cache"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Innioasis Updater" / ".cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        target = base / "updater-ce"
        legacy = base / "innioasis-updater" / ".cache"
        if not target.exists() and legacy.exists():
            return legacy
        return target
