"""Application-wide constants for the Neo updater.

Values mirror the InniUpdaterChin app (APP_VERSION 1.1.2, contact email) and
the online catalogue / donation endpoints used by Updater CE and innioasis.app.
"""

APP_VERSION = "1.1.2"
APP_NAME = "Innioasis Updater"
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

# --- Device model helpers ---------------------------------------------------
DEVICE_MODELS = ("Y1", "Y2")


def resolve_firmware_repo(repo: str) -> str:
    """Map legacy/unpublished manifest repo names to a fetchable GitHub repo."""
    return FIRMWARE_REPO_FALLBACKS.get(repo, repo)


def is_y2_model(model: str) -> bool:
    return "Y2" in (model or "").upper()


def is_y1_model(model: str) -> bool:
    return "Y1" in (model or "").upper()


def device_label_for_model(model: str) -> str:
    if is_y2_model(model):
        return "Y2"
    if is_y1_model(model):
        return "Y1"
    return (model or "").strip() or "Y1"


def power_on_button_for_model(model: str) -> str:
    """Hardware button used to power the player on after an install."""
    return "power/lock button" if is_y2_model(model) else "centre button"
