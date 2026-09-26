"""Application-wide constants for the Neo updater.

Values mirror the InniUpdaterChin app (APP_VERSION 1.1.2, contact email) and
the online catalogue / donation endpoints used by Updater CE and innioasis.app.
"""

APP_VERSION = "3.0"
APP_NAME = "Innioasis Updater CE"
CONTACT_EMAIL = "updater-feedback@innioasis.com"

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

import re
from pathlib import Path

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

    # 5. Y1 / generic rom.zip
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


def device_label_for_model(model: str, type_variant: str | None = None) -> str:
    m = (model or "").strip()
    if is_a5_model(m):
        base = "A5"
    elif is_y2_model(m):
        base = "Y2"
    elif is_y1_model(m):
        base = "Y1"
    else:
        base = m or "Y1"

    if base == "Y1" and type_variant:
        return f"Y1 (Type {type_variant})"
    return base


def power_on_button_for_model(model: str) -> str:
    """Hardware button used to power the player on after an install."""
    if is_y2_model(model):
        return "power/lock button"
    if is_a5_model(model):
        return "power button"
    return "centre button"


def install_power_on_steps(model: str) -> str:
    """Short post-install power-on steps for the active model."""
    label = device_label_for_model(model)
    button = power_on_button_for_model(model)
    return f"Unplug your {label}, then hold the {button} until it turns on."


def install_disconnect_guidance(model: str = "Y1", type_variant: str | None = None) -> str:
    """Guidance text for ensuring device is disconnected and powered off before install."""
    label = device_label_for_model(model, type_variant)
    return (
        f"Before continuing, please:\n\n"
        f"1. If it isn't already off, power off your {label}\n"
        f"   (You can use a pin or paperclip to press the reset button if needed)\n\n"
        f"2. If it is connected, disconnect the USB cable from your {label}\n\n"
        f"Then click OK and follow the on-screen instructions."
    )

