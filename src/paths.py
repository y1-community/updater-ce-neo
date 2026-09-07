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
    a dev checkout (vendor/) and in a PyInstaller bundle (BUNDLE_DIR/mtkclient).
    """
    if str(MTKCLIENT_DIR) not in sys.path:
        sys.path.insert(0, str(MTKCLIENT_DIR))

# --- Backend discovery (ported from flash_service) --------------------------

IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


def find_sp_flash_tool():
    """Locate the SP Flash Tool directory (Windows backend).

    Search order: ``$SP_FLASH_TOOL_DIR`` → Inno-installed ``SP_Flash_Tool``
    folder → a hard-coded developer fallback. The tool counts as found only
    when ``flash_tool.exe`` exists inside.
    """
    candidates = [
        Path(os.environ["SP_FLASH_TOOL_DIR"]) if os.environ.get("SP_FLASH_TOOL_DIR") else None,
        SP_FLASH_TOOL_DIR,
        Path(r"D:\work\data\tools\SP_Flash_Tool_v5.2016_Windows"),  # dev fallback
    ]
    for p in candidates:
        if p and p.exists() and (p / "flash_tool.exe").exists():
            return p
    return None


def find_unrar():
    """Locate an UnRAR executable, in the same order as InniUpdaterChin."""
    candidates = [
        TOOLS_DIR / "UnRAR.exe",
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    on_path = _which("unrar") or _which("unar")
    if on_path:
        return on_path
    return ""


def _which(name: str):
    import shutil

    return shutil.which(name)
