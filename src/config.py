"""Application-wide constants for Updater CE.

Values mirror the InniUpdaterChin app (APP_VERSION 1.1.2, contact email) and
the online catalogue / donation endpoints used by Updater CE and innioasis.app.
"""

APP_VERSION = "2.5.4"
APP_NAME = "Updater CE"
CONTACT_EMAIL = "updater-feedback@innioasis.com"

# --- Staggered releases -----------------------------------------------------
# The Updater CE rewrite goes out one platform at a time. 2.5 is the Windows
# run. The update checker decides by asset name alone (see UPDATE_TARGET_ASSETS):
# a release is offered on a platform only when it carries that platform's asset,
# so a Windows-only 2.5 is never offered to macOS or Linux. This tuple records
# which platforms the current release targets; nothing gates on it.
RELEASE_TARGETS = ("windows",)


def release_target_for(platform: str = "") -> str:
    """The release-target name for a ``sys.platform`` value (``""`` when unknown)."""
    import sys

    platform = platform or sys.platform
    if platform.startswith("win"):
        return "windows"
    if platform.startswith("darwin"):
        return "macos"
    if platform.startswith("linux"):
        return "linux"
    return ""


def is_release_platform(platform: str = "") -> bool:
    """True when this platform is part of the release currently going out."""
    return release_target_for(platform) in RELEASE_TARGETS

# --- Offline mode / MediaTek Installer branding -----------------------------
# Two independent switches, deliberately kept apart:
#   * Offline mode — Updater CE with the online catalogue hidden. The CE brand
#     and everything else stays as it is.
#   * MediaTek Installer — the same engine built as a generic cross-platform
#     MediaTek firmware installer: its own name ("Installer x.x" in-app), no
#     online firmware at all, but donations and the terminal install remain.
IS_OFFLINE_MODE = False
IS_MEDIATEK_INSTALLER = False

OFFLINE_FLAGS = ("--offline",)
MEDIATEK_INSTALLER_FLAGS = ("--mediatek-installer", "--generic-mtk")
MEDIATEK_INSTALLER_BRANDS = ("mediatek_installer", "mediatek-installer", "generic_mtk")


def build_brand() -> str:
    """Brand baked into a frozen build by ``scripts/set_build_brand.py``.

    A built app is launched by double-click, so the choice cannot live in the
    environment: the packaging step writes it into ``src/_build_brand.py``
    before PyInstaller runs. Absent (dev runs, normal builds) = Updater CE.
    """
    try:
        from ._build_brand import BUILD_BRAND
    except Exception:
        return ""
    return str(BUILD_BRAND or "").strip().lower()


def is_mediatek_installer() -> bool:
    """True when running as the generic MediaTek Installer build."""
    if IS_MEDIATEK_INSTALLER:
        return True
    import os
    import sys
    if any(flag in sys.argv for flag in MEDIATEK_INSTALLER_FLAGS):
        return True
    brand = (os.environ.get("BUILD_BRAND") or "").strip().lower() or build_brand()
    return brand in MEDIATEK_INSTALLER_BRANDS


def is_offline_mode() -> bool:
    """True when the online firmware catalogue is switched off."""
    if is_mediatek_installer() or IS_OFFLINE_MODE:
        return True
    import os
    import sys
    if any(flag in sys.argv for flag in OFFLINE_FLAGS):
        return True
    if os.environ.get("UPDATER_OFFLINE") == "1":
        return True
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("Innioasis", "UpdaterCE")
        return bool(settings.value("offline_mode", False, type=bool))
    except Exception:
        return False


def is_generic_mtk() -> bool:
    """True when no online catalogue is available (offline, or generic build)."""
    return is_offline_mode()


def get_app_name() -> str:
    """Full application name for window titles and About text."""
    from .i18n import tr
    if is_mediatek_installer():
        return tr("app_name_mediatek_installer")
    return tr("app_name")


def get_brand_name() -> str:
    """Short brand shown beside the version badge ("Installer 2.5")."""
    from .i18n import tr
    if is_mediatek_installer():
        return tr("app_name_mediatek_installer_short")
    return tr("app_name")

# --- Online firmware catalogue ---------------------------------------------
# The live manifest is fetched at runtime (like Updater CE does); the static
# table below is the built-in fallback for offline first-run and matches
# firmware-catalog.js / slidia_manifest.xml.
CATALOG_MANIFEST_URL = "https://innioasis.app/slidia_manifest.xml"
# Data files are read from the website's repository first: GitHub serves the
# committed copy immediately, while the GitHub Pages CDN behind innioasis.app
# can lag behind it. The website remains the next source, and the maintainer's
# fork is the last fallback if the organisation repository is unavailable.
INNIOASIS_RAW_PREFIX = "https://raw.githubusercontent.com/y1-community/innioasis-updater/refs/heads/main/"
INNIOASIS_RAW_FALLBACK_PREFIX = "https://raw.githubusercontent.com/ryan-specter/innioasis-updater/refs/heads/main/"
CATALOG_RAW_URL = INNIOASIS_RAW_PREFIX + "slidia_manifest.xml"
CATALOG_FALLBACK_URL = INNIOASIS_RAW_FALLBACK_PREFIX + "slidia_manifest.xml"
CATALOG_MANIFEST_URLS = (CATALOG_RAW_URL, CATALOG_MANIFEST_URL, CATALOG_FALLBACK_URL)
CATALOG_CACHE_DIR_NAME = "catalog"

GITHUB_API = "https://api.github.com"
GITHUB_RELEASES_PER_PAGE = 100
RELEASE_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 h, same as CE

# --- App update checking ----------------------------------------------------
# Where this app publishes its releases — Updater CE lives in this repo, so
# Check for Updates reads its release channel here.
# Application updates always come from the CE repository. If that repository
# is temporarily unavailable (for example removed from the organisation), the
# maintainer's fork is tried as the last fallback.
UPDATE_REPO = "y1-community/updater-ce-neo"
UPDATE_REPOS = (UPDATE_REPO, "ryan-specter/updater-ce-neo")
UPDATE_CACHE_TTL_SECONDS = 24 * 60 * 60
UPDATE_CHECK_STARTUP_DELAY_MS = 3000  # silent auto-check shortly after launch

# Specific target release asset filenames per operating system for updater-ce-neo releases.
# If these files don't exist in a newer release, it is assumed the user is running the latest build.
UPDATE_TARGET_ASSETS = {
    "win32": ("installer.exe", "installer-arm64.exe", "updaterce-setup.exe"),
    "darwin": ("innioasis.updater.for.mac.dmg",),
    "linux": (
        "UpdaterCE.appimage",
        "UpdaterCE-arm64.appimage",
        "UpdaterCE-Linux-Portable.tar.gz",
        "UpdaterCE-Linux-Portable-arm64.tar.gz",
        "Updater-CE-Linux-Portable-arm64.tar.gz",
        "UpdaterCE-Linux-Portable.zip",
        "UpdaterCE-Linux-Portable-arm64.zip",
        "Updater-CE-Linux-Portable-arm64.zip",
        "mediatek installer.appimage",
        "mediatek installer.tar.gz",
    ),
}

# Asset-extension preference per OS fallback
UPDATE_ASSET_EXTENSIONS = {
    "win32": (".exe", ".msi", ".zip"),
    "darwin": (".dmg", ".pkg", ".zip"),
    "linux": (".AppImage", ".appimage", ".tar.gz", ".sh", ".deb", ".rpm"),
}
# Architecture tokens that rank an asset above an unmarked one (best first).
UPDATE_ASSET_ARCH_PRIORITY = ("universal", "x86_64", "amd64", "arm64", "aarch64")

# Legacy/unpublished manifest repo names that resolve to a fetchable repo.
FIRMWARE_REPO_FALLBACKS = {
    "y1-community/stock-rom": "y1-community/y1-stock-rom",
    "y1-community/y2-stock-rom": "y1-community/y1-stock-rom",
    "y1-community/rockbox-y2-rom": "spycemagic/y2-rockbox",
}

# Minimum visible release per (model, repo): anything older is hidden by default.
# Only Y1 Type A has the hardware revision (units sold after March 2026 with
# Innioasis OS 3.0.7 pre-installed) incompatible with Rockbox-Y1 pre-0.5. Y1
# Type B, Y2, and non-Y1 models are unrestricted: Type B units only ever shipped
# pre-3.0.7 software. Users can also tick "Show Old Rockbox Builds" to bypass
# this for Y1 Type A.
MODEL_REPO_MIN_RELEASE_VERSION = {
    ("Y1", "rockbox-y1/rockbox"): "0.5",
}
REPO_MIN_RELEASE_VERSION = {
    "rockbox-y1/rockbox": "0.5",
}

# --- Donations (public endpoints/addresses from innioasis.app / CE) ---------
DONORS_CSV_URL = "https://innioasis.app/donors.csv"
MONTHLY_GOAL_USD = 200.0

# Online files served from innioasis.app. When that site cannot be reached the
# same file is fetched from the repo that hosts the website (GitHub names are
# case-insensitive, so this is the same repository as CATALOG_FALLBACK_URL).
INNIOASIS_SITE_PREFIX = "https://innioasis.app/"
INNIOASIS_GITHUB_FALLBACK_PREFIX = INNIOASIS_RAW_PREFIX


def github_fallback_url(url: str) -> str:
    """Map an innioasis.app file URL to its copy in the website's GitHub repo."""
    if url.startswith(INNIOASIS_SITE_PREFIX):
        return INNIOASIS_GITHUB_FALLBACK_PREFIX + url[len(INNIOASIS_SITE_PREFIX):].split("?", 1)[0]
    return url


def site_source_urls(path: str) -> tuple[str, ...]:
    """Sources for one website data file, freshest copy first.

    The GitHub-hosted copy updates as soon as a commit lands; the Pages site
    can trail it by a CDN cache interval. A fork fallback keeps the app working
    if the organisation repository is removed.
    """
    clean = str(path or "").lstrip("/")
    return (
        INNIOASIS_RAW_PREFIX + clean,
        INNIOASIS_SITE_PREFIX + clean,
        INNIOASIS_RAW_FALLBACK_PREFIX + clean,
    )

DONATION_LINKS = {
    "kofi": "https://innioasis.app/donate.html?method=kofi",
    "bmc": "https://innioasis.app/donate.html?method=bmc",
    # The only donation page that accepts a Russian card, so the Russian market
    # is offered this alone (see fx.donation_route()).
    "boosty": "https://innioasis.app/donate.html?method=boosty",
    "paypal": "https://paypal.me/ryanspecter282",
    "revolut": "https://revolut.me/rspecter",
    "patreon": "https://www.patreon.com/ryanspecter",
    "honeygain": "https://join.honeygain.com/ITSRY2B5D7",
    "discord": "https://discord.gg/zHrT2zrcek",
    "reddit": "https://reddit.com/r/innioasis",
    # Help questions go to the project's discussions, not an issue tracker.
    "github": "https://github.com/y1-community/updater-ce-neo/discussions",
}
DONATION_CRYPTO = {
    "Bitcoin (BTC)": "bc1qv4gjkqczqy4wdl297k7ak5swtkusgaz5au6c6g",
    "Ethereum / Arbitrum / Optimism": "0x5E902083ee1B3A05dd39d824012B39cB10FB80D3",
    "SHIBA INU (ERC-20)": "0x5E902083ee1B3A05dd39d824012B39cB10FB80D3",
}

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# --- Device model helpers ---------------------------------------------------
DEVICE_MODELS = ("Y1", "Y2", "A5")

# Hardware revisions each model was sold in, standard revision first. Models
# absent from this map have no selectable revision, so their UI never asks for
# one. Keeping the metadata here means the manual Add Player flow and the
# reminder labels can share the same rule.
DEVICE_MODEL_TYPES = {
    "Y1": ("A", "B"),
}


def hardware_types_for_model(model: str) -> tuple[str, ...]:
    """Hardware revisions for ``model``, standard revision first."""
    return DEVICE_MODEL_TYPES.get((model or "").strip().upper(), ())


def default_hardware_type(model: str) -> str:
    """The standard revision for ``model``, or ``""`` when it has none."""
    types = hardware_types_for_model(model)
    return types[0] if types else ""


def resolve_firmware_repo(repo: str) -> str:
    """Map legacy/unpublished manifest repo names to a fetchable GitHub repo."""
    return FIRMWARE_REPO_FALLBACKS.get(repo, repo)


def is_a5_model(model: str) -> bool:
    return "A5" in (model or "").upper()


def is_y2_model(model: str) -> bool:
    return "Y2" in (model or "").upper()


def is_y1_model(model: str) -> bool:
    return "Y1" in (model or "").upper()


def detect_model_and_type_from_name(name_or_url: str) -> tuple[str, str | None]:
    """Detect device model (Y1, Y2, A5, etc.) and type variant (A or B)
    from the original file name, asset name, or download URL.
    
    Examples:
      rom_y2.zip, *_y2*.zip -> ("Y2", None)
      rom_a5.zip, *_a5*.zip -> ("A5", None)
      rom_type_b.zip, *_type_b*.zip -> ("Y1", "B")
      rom.zip, rom_type_a.zip -> ("Y1", "A")
    """
    if not name_or_url:
        return "", None

    clean = str(name_or_url).lower().replace("\\", "/")
    base = Path(clean).name
    stem = Path(base).stem.lower()
    parts = re.split(r"[-_.\s]+", stem)

    # 1. A5
    if (
        "a5" in parts
        or "rom_a5" in base
        or "rom-a5" in base
        or base.startswith("a5")
        or "/a5" in clean
    ):
        return "A5", None

    # 2. Y2
    if (
        "y2" in parts
        or "_y2" in base
        or "-y2" in base
        or "rom_y2" in base
        or "rom-y2" in base
        or "y2-stock" in base
        or "y2_stock" in base
        or base.startswith("y2")
        or "/y2" in clean
    ):
        return "Y2", None

    # 3. Type B (Y1 variant)
    if (
        "type_b" in base
        or "type-b" in base
        or "typeb" in base
        or "rom_type_b" in base
        or "rom-type-b" in base
        or base.endswith(("_b.zip", "-b.zip"))
    ):
        return "Y1", "B"

    # 4. Type A (Y1 variant)
    if "type_a" in base or "type-a" in base or "rom_type_a" in base or "rom-type-a" in base:
        return "Y1", "A"

    # 5. Non-legacy registered models (G1/G3/G5, Q3E/Q5/Q8...). These have no
    #    catalogue entry yet, but firmware for them can still be imported by
    #    hand, and the connection prompts must name the right player.
    try:
        from . import device_models

        registered, _variant = device_models.model_from_filename(
            base, exclude=("Y1", "Y2", "A5")
        )
        if registered:
            return registered, None
    except Exception:
        logger.debug("Device registry detection failed for %s", name_or_url, exc_info=True)

    # 6. Y1 / generic rom.zip
    if (
        "y1" in parts
        or "_y1" in base
        or "-y1" in base
        or "rom_y1" in base
        or "rom-y1" in base
        or "y1-stock" in base
        or "y1_stock" in base
        or base.startswith("y1")
        or "y1-community" in base
        or "/y1" in clean
        or base == "rom.zip"
    ):
        return "Y1", "A"

    return "", None


def device_label_for_model(model: str = "", type_variant: str | None = None) -> str:
    m = (model or "").strip()
    if not m or m.lower() in ("device", "generic", "unknown"):
        from .i18n import tr
        return tr("flash_model_generic")

    if is_a5_model(m):
        return "A5"
    if is_y2_model(m):
        return "Y2"
    if is_y1_model(m):
        return "Y1"

    # Anything outside the legacy Y1/Y2/A5 trio is resolved through the device
    # registry, so a hand-imported package still produces guidance that names
    # the real product ("Innioasis G5") instead of a bare id. Imported lazily:
    # config is loaded very early, before the registry may be readable.
    try:
        from . import device_models

        registered = device_models.guidance_name(m)
        if registered:
            return registered
    except Exception:
        logger.debug("Device registry lookup failed for %s", m, exc_info=True)

    return m


def power_on_button_for_model(model: str = "") -> str:
    """Hardware button used to power the player on after an install."""
    from .i18n import tr

    if is_y1_model(model):
        return tr("btn_centre")
    # Y2 and every other model power on with the power button.
    return tr("btn_power")


def install_power_on_steps(model: str = "") -> str:
    """Short post-install power-on steps for the active model."""
    from .i18n import tr

    label = device_label_for_model(model)
    button = power_on_button_for_model(model)
    return tr("install_power_on_steps_fmt").format(label=label, button=button)


def install_disconnect_guidance(model: str = "", type_variant: str | None = None) -> str:
    """Guidance text for ensuring device is disconnected and powered off before install."""
    from .i18n import tr

    label = device_label_for_model(model, type_variant)
    return tr("install_disconnect_guidance_fmt").format(label=label)


_HEADPHONE_PORT_MODELS = ("Y1", "Y2", "G1", "G3", "G5", "Q5", "Q3E")


def download_mode_model(model: str) -> str:
    """Model id used to pick the download-mode hint, or "" when none matches."""
    text = re.sub(r"[^A-Z0-9]", "", (model or "").upper())
    if "Q3E" in text:
        return "Q3E"
    for mid in ("Y1", "Y2", "G1", "G3", "G5", "Q5", "A5"):
        if mid in text:
            return mid
    return ""


def download_mode_hint(model: str = "") -> str:
    """Small in-window hint for how to power this model into download mode.

    Y1, Y2, G1, G3, G5, Q5 and Q3e use the hole beside the headphone port.
    A5 uses the hole under the power button. Anything else gets one short
    generic line. Copy does not name the desktop tool.
    """
    from .i18n import tr, tr_brand

    mid = download_mode_model(model)
    if mid in _HEADPHONE_PORT_MODELS:
        return tr("gui_handoff_hint_headphone")
    if mid == "A5":
        return tr("gui_handoff_hint_a5")
    return tr_brand("gui_handoff_hint_generic")

