"""Application-wide constants for the Neo updater.

Values mirror the InniUpdaterChin app (APP_VERSION 1.1.2, contact email) and
the online catalogue / donation endpoints used by Updater CE and innioasis.app.
"""

APP_VERSION = "3.0"
APP_NAME = "Updater CE"
CONTACT_EMAIL = "updater-feedback@innioasis.com"

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
    """Short brand shown beside the version badge ("Installer 3.0")."""
    from .i18n import tr
    if is_mediatek_installer():
        return tr("app_name_mediatek_installer_short")
    return tr("app_name")

# --- Online firmware catalogue ---------------------------------------------
# The live manifest is fetched at runtime (like Updater CE does); the static
# table below is the built-in fallback for offline first-run and matches
# firmware-catalog.js / slidia_manifest.xml.
CATALOG_MANIFEST_URL = "https://innioasis.app/slidia_manifest.xml"
CATALOG_FALLBACK_URL = "https://raw.githubusercontent.com/y1-community/Innioasis-Updater/refs/heads/main/slidia_manifest.xml"
CATALOG_CACHE_DIR_NAME = "catalog"

GITHUB_API = "https://api.github.com"
GITHUB_RELEASES_PER_PAGE = 100
RELEASE_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 h, same as CE

# --- App update checking ----------------------------------------------------
# Where this app publishes its releases — Updater Neo lives in this repo, so
# Check for Updates reads its release channel here.
UPDATE_REPO = "y1-community/updater-ce-neo"
UPDATE_CACHE_TTL_SECONDS = 24 * 60 * 60
UPDATE_CHECK_STARTUP_DELAY_MS = 3000  # silent auto-check shortly after launch

# Asset-extension preference per OS, used to pick the right download from a
# release's assets (order = preference). Matches the asset names the CE repo
# publishes today (installer.exe, *.dmg, run_linux.sh).
UPDATE_ASSET_EXTENSIONS = {
    "win32": (".exe", ".msi", ".zip"),
    "darwin": (".dmg", ".pkg", ".zip"),
    "linux": (".AppImage", ".tar.gz", ".sh", ".deb", ".rpm"),
}
# Architecture tokens that rank an asset above an unmarked one (best first).
UPDATE_ASSET_ARCH_PRIORITY = ("universal", "x86_64", "amd64", "arm64", "aarch64")

# Legacy/unpublished manifest repo names that resolve to a fetchable repo.
FIRMWARE_REPO_FALLBACKS = {
    "y1-community/stock-rom": "y1-community/y1-stock-rom",
    "y1-community/y2-stock-rom": "y1-community/y1-stock-rom",
}

# Minimum visible release per (model, repo): anything older is hidden by default.
# Only Y1 has hardware revisions (units sold after March 2026 with Innioasis OS 3.0.7
# pre-installed) incompatible with Rockbox-Y1 pre-0.5. Y2 and non-Y1 models are
# unrestricted. Users can also tick "Show Old Rockbox Builds" to bypass this for Y1.
MODEL_REPO_MIN_RELEASE_VERSION = {
    ("Y1", "rockbox-y1/rockbox"): "0.5",
}
REPO_MIN_RELEASE_VERSION = {
    "rockbox-y1/rockbox": "0.5",
}

# --- Donations (public endpoints/addresses from innioasis.app / CE) ---------
DONORS_CSV_URL = "https://innioasis.app/donors.csv"
MONTHLY_GOAL_USD = 200.0

DONATION_LINKS = {
    "kofi": "https://ko-fi.com/teamslide",
    "paypal": "https://paypal.me/respectyarn",
    "revolut": "https://revolut.me/rspecter",
    "patreon": "https://www.patreon.com/ryanspecter",
    "honeygain": "https://join.honeygain.com/ITSRY2B5D7",
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
    if "type_b" in base or "type-b" in base or "rom_type_b" in base or "rom-type-b" in base:
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

    if is_y2_model(model):
        return tr("btn_power_lock")
    if is_a5_model(model):
        return tr("btn_power")
    if is_y1_model(model):
        return tr("btn_centre")
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

