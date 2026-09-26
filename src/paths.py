"""Unified path resolution: development mode vs PyInstaller-packaged mode.

Faithful port of InniUpdaterChin's ``app.runtime_paths``. After packaging
there are two important directories:

- ``BUNDLE_DIR`` (``sys._MEIPASS``): internal resources unpacked by
  PyInstaller (``style.qss``, ``mtkclient``, ``donors.csv``).
- ``INSTALL_DIR`` (the executable's folder): files installed beside the app
  (``SP_Flash_Tool``, ``tools/UnRAR.exe``).
"""

import os
import sys
from pathlib import Path


def _get_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _get_install_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable))
    return Path(__file__).resolve().parent.parent


BUNDLE_DIR = _get_bundle_dir()
INSTALL_DIR = _get_install_dir()

# Where the project ships its own files in a dev checkout:
REPO_ROOT = Path(__file__).resolve().parent.parent

BASE_DIR = BUNDLE_DIR
MTKCLIENT_DIR = BUNDLE_DIR / "mtkclient"
RESOURCES_DIR = BUNDLE_DIR / "assets"
SP_FLASH_TOOL_DIR = INSTALL_DIR / "SP_Flash_Tool"
TOOLS_DIR = INSTALL_DIR / "tools"

# In a dev checkout, vendor/ lives next to the repo root (BUNDLE_DIR is the
# repo root in dev mode), so BUNDLE_DIR / "mtkclient" does not resolve.
if not (BUNDLE_DIR / "mtkclient").exists() and (REPO_ROOT / "vendor" / "mtkclient").exists():
    MTKCLIENT_DIR = REPO_ROOT / "vendor" / "mtkclient"
if not (BUNDLE_DIR / "assets").exists() and (REPO_ROOT / "assets").exists():
    RESOURCES_DIR = REPO_ROOT / "assets"
COMPAT_DIR = RESOURCES_DIR / "compat"


def ensure_mtkclient_importable():
    """Make the bundled mtkclient importable (idempotent).

    Called by every module that imports mtkclient so the import works both in
    a dev checkout (vendor/) and in a PyInstaller bundle (BUNDLE_DIR/mtkclient,
    Contents/Resources/mtkclient, etc.).
    """
    candidates = [
        MTKCLIENT_DIR,
        BUNDLE_DIR / "mtkclient",
        BUNDLE_DIR.parent / "Resources" / "mtkclient" if getattr(sys, "frozen", False) else None,
        REPO_ROOT / "vendor" / "mtkclient",
    ]
    for cand in candidates:
        if not cand or not cand.exists():
            continue
        if (cand / "mtkclient" / "__init__.py").exists():
            p = str(cand)
            if p not in sys.path:
                sys.path.insert(0, p)
            return
        if (cand / "__init__.py").exists():
            p = str(cand.parent)
            if p not in sys.path:
                sys.path.insert(0, p)
            return

    if str(MTKCLIENT_DIR) not in sys.path:
        sys.path.insert(0, str(MTKCLIENT_DIR))


def find_libusb_dylib() -> str | None:
    """Find the path to the libusb-1.0 dynamic library on macOS.

    Checks:
    1. Vendored universal dylib in mtkclient/Darwin/libusb-1.0.dylib
    2. PyInstaller bundle locations (sys._MEIPASS, Frameworks, Resources)
    3. python package `libusb_package`
    4. Standard Homebrew / MacPorts locations
    5. Returns None if not found on disk (falling back to standard dlopen name)
    """
    candidates = [
        MTKCLIENT_DIR / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
        MTKCLIENT_DIR / "Darwin" / "libusb-1.0.dylib",
        BUNDLE_DIR / "libusb-1.0.dylib",
        BUNDLE_DIR / "Frameworks" / "libusb-1.0.dylib",
        BUNDLE_DIR / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
        BUNDLE_DIR / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
    ]
    app_root = os.environ.get("INNIOASIS_APP_ROOT")
    if app_root:
        root_p = Path(app_root)
        candidates.extend([
            root_p / "Frameworks" / "libusb-1.0.dylib",
            root_p / "MacOS" / "libusb-1.0.dylib",
            root_p / "Resources" / "app" / "vendor" / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
        ])
    if getattr(sys, "frozen", False):
        res_dir = BUNDLE_DIR.parent / "Resources"
        frameworks_dir = BUNDLE_DIR.parent / "Frameworks"
        candidates.extend([
            res_dir / "libusb-1.0.dylib",
            res_dir / "Frameworks" / "libusb-1.0.dylib",
            res_dir / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
            res_dir / "mtkclient" / "Darwin" / "libusb-1.0.dylib",
            frameworks_dir / "libusb-1.0.dylib",
            BUNDLE_DIR.parent / "MacOS" / "libusb-1.0.dylib",
        ])

    for c in candidates:
        if c and c.is_file():
            return str(c)

    try:
        import libusb_package
        lp = libusb_package.get_library_path()
        if lp and os.path.isfile(lp):
            return lp
    except Exception:
        pass

    for sys_path in [
        "/opt/homebrew/lib/libusb-1.0.dylib",
        "/usr/local/lib/libusb-1.0.dylib",
        "/opt/local/lib/libusb-1.0.dylib",
    ]:
        if os.path.isfile(sys_path):
            return sys_path

    return None

# --- Backend discovery (ported from flash_service) --------------------------

# Simulated macOS mode: run the macOS code path (MTKClient-only flash
# backend, no SP Flash Tool GUI) on Linux/Windows for parity testing.
# Set via the ``--simulate-macos`` launch flag (see launcher.py) or the
# INNIOASIS_SIMULATE_MACOS=1 environment variable.
SIMULATE_MACOS = os.environ.get("INNIOASIS_SIMULATE_MACOS", "") in ("1", "true", "yes", "on")

IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
# Every macOS conditional in the app must read IS_MAC (not raw sys.platform)
# so the simulated mode is indistinguishable from the real thing.
IS_MAC = sys.platform == "darwin" or SIMULATE_MACOS


def find_sp_flash_tool():
    """Locate the SP Flash Tool directory (Windows & Linux).

    Search order:
    1. ``$SP_FLASH_TOOL_DIR`` env var
    2. Staged or self-healed user tools directory
    3. Inno-installed / AppImage bundled ``SP_Flash_Tool`` folder
    4. Local development / repository fallback.
    """
    candidates = [
        Path(os.environ["SP_FLASH_TOOL_DIR"]) if os.environ.get("SP_FLASH_TOOL_DIR") else None,
        SP_FLASH_TOOL_DIR,
        INSTALL_DIR / "SP_Flash_Tool",
        INSTALL_DIR,
        REPO_ROOT / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "SP_Flash_Tool",
        Path.cwd() / "SP_Flash_Tool",
        Path.cwd(),
    ]
    if IS_WINDOWS:
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Innioasis Updater" / "tools" / "sp_flash_tool_win")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Innioasis Updater" / "SP_Flash_Tool")
        candidates.append(Path.home() / "AppData" / "Local" / "Innioasis Updater" / "tools" / "sp_flash_tool_win")
        candidates.append(Path.home() / "AppData" / "Local" / "Innioasis Updater" / "SP_Flash_Tool")
        candidates.append(Path(r"D:\work\data\tools\SP_Flash_Tool_v5.2016_Windows"))
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")
        candidates.append(Path(xdg_data) / "innioasis-updater" / "tools" / "sp_flash_tool_linux")
        xdg_cache = os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")
        candidates.append(Path(xdg_cache) / "innioasis-updater" / "linux_flash_tool")

    bin_names = ["flash_tool.exe"] if IS_WINDOWS else ["flash_tool", "flash_tool.sh"]
    for p in candidates:
        if not p or not p.exists():
            continue
        for b in bin_names:
            if (p / b).is_file():
                return p
    return None


def find_unrar():
    """Locate an UnRAR executable, in the same order as InniUpdaterChin."""
    candidates = [
        TOOLS_DIR / "UnRAR.exe",
        Path.home() / "AppData" / "Local" / "Innioasis Updater" / "tools" / "unrar_win" / "UnRAR.exe",
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
    ]
    for p in candidates:
        if p and p.exists():
            return str(p)
    on_path = _which("unrar") or _which("unar")
    if on_path:
        return on_path
    return ""


def _which(name: str):
    import shutil

    return shutil.which(name)
