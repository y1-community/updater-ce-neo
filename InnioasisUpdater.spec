# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the Neo updater (Windows / macOS / Linux).

Build (from the project root):

    pyinstaller --clean --noconfirm build/InnioasisUpdater.spec

The spec bundles:
  - the application package (src/) via the launcher.py entry point,
  - assets/ (style.qss, donors.csv, icon.ico, guidance + device images),
  - the vendored mtkclient (vendor/mtkclient) as data + collected submodules,
    so the macOS/Linux flash backend keeps all DA loaders and payloads.
"""

import os
import sys
from pathlib import Path

# SPECPATH is the directory containing this spec file (the project's build/).
PROJECT_ROOT = Path(SPECPATH)  # noqa: F821  (SPECPATH injected by PyInstaller)
VENDOR_MTK = PROJECT_ROOT / "vendor" / "mtkclient"
ASSETS = PROJECT_ROOT / "assets"

# --- Packaging brand --------------------------------------------------------
# BUILD_BRAND=mediatek_installer packages the same engine as the generic
# cross-platform MediaTek Installer (its own executable and dist folder);
# anything else builds Updater CE, exactly as before.
_BUILD_BRAND = (os.environ.get("BUILD_BRAND") or "").strip().lower().replace("-", "_")
if not _BUILD_BRAND:
    _brand_file = PROJECT_ROOT / "src" / "_build_brand.py"
    if _brand_file.exists():
        for _line in _brand_file.read_text(encoding="utf-8").splitlines():
            if _line.startswith("BUILD_BRAND"):
                _BUILD_BRAND = _line.split("=")[-1].strip().strip('"\'')
                break
IS_MEDIATEK_INSTALLER_BUILD = (_BUILD_BRAND == "mediatek_installer")
# The dist folder / executable name stays a slug (like Updater CE's does): the
# display name is the job of the installer and the desktop entry, and spaces in
# an executable path are needless trouble for cmd, Inno Setup and AppRun.
APP_DISPLAY_NAME = "MediaTek Installer" if IS_MEDIATEK_INSTALLER_BUILD else "Updater CE"
APP_SLUG = "MediaTekInstaller" if IS_MEDIATEK_INSTALLER_BUILD else "InnioasisUpdater"
print(f"[spec] packaging brand: {APP_DISPLAY_NAME} -> dist/{APP_SLUG}")

# Make mtkclient importable at build time so collect_submodules can see it.
if str(VENDOR_MTK) not in sys.path:
    sys.path.insert(0, str(VENDOR_MTK))

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

mtk_hidden = []
# Cryptodome (pycryptodomex): the hook only bundles the C-extension binaries;
# the pure-Python submodules must be declared explicitly because mtkclient is
# bundled as a data directory (invisible to PyInstaller's static analysis).
crypto_hidden = collect_submodules("Cryptodome")

datas = []
if ASSETS.exists():
    datas.append((str(ASSETS), "assets"))
if VENDOR_MTK.exists():
    datas.append((str(VENDOR_MTK), "mtkclient"))

# NOTE: SP Flash Tool is NOT bundled through datas — the app resolves it at
# INSTALL_DIR / "SP_Flash_Tool" (next to the exe), which datas cannot target
# (datas always land under _internal). A post-build copy step places it next
# to the exe; see tools/build.sh / build.bat.

hiddenimports = mtk_hidden + crypto_hidden + [
    # mtkclient loads some backends lazily; keep them explicit. Since
    # mtkclient is bundled as a data directory (not analysed as Python), its
    # third-party imports are invisible to PyInstaller's static analysis —
    # they must be declared here or the frozen import fails (MTK_IMPORT_FAILED).
    "usb",
    "usb.backend.libusb1",
    "serial",
    "serial.tools.list_ports",  # pyserial submodule imported by mtkclient
    "Cryptodome",  # pycryptodomex: mtkclient's mtk_crypto imports it directly
    "colorama",  # mtkclient.gui_utils imports it at module level
    "logging.config",  # stdlib module PyInstaller strips from base_library.zip
    # UI fallback libraries
    "pywinstyles",
    "qtwin11",
    "wx",
    "win32gui",
    "win32con",
    "win32api",
    "tkinter",
    "ttk",
]

a = Analysis(
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mtkclient"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_SLUG,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ASSETS / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_SLUG,
)

# ---------------------------------------------------------------------------
# Post-build payload placement: the app resolves SP Flash Tool at
# INSTALL_DIR / "SP_Flash_Tool" (next to the exe), which PyInstaller datas
# cannot target (datas always land under _internal). Without this a --clean
# rebuild silently drops the payload and Auto mode fails with
# "SP Flash Tool not found". Copy the whole tree next to the exe after COLLECT.
# ---------------------------------------------------------------------------
import shutil  # noqa: E402

sp_candidates = [
    PROJECT_ROOT / "SP_Flash_Tool",
    PROJECT_ROOT / "tools" / "SP_Flash_Tool",
    PROJECT_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win" if sys.platform.startswith("win") else None,
    PROJECT_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux" if sys.platform.startswith("linux") else None,
]
SP_FLASH_SRC = next((p for p in sp_candidates if p and p.exists()), None)
try:
    _dist_root = Path(DISTPATH)  # noqa: F821  (injected by PyInstaller)
    _app_dir = _dist_root / APP_SLUG
    _sp_target = _app_dir / "SP_Flash_Tool"
    if SP_FLASH_SRC and SP_FLASH_SRC.exists():
        if _sp_target.exists():
            shutil.rmtree(_sp_target, ignore_errors=True)
        shutil.copytree(SP_FLASH_SRC, _sp_target)
        print(f"[spec] copied SP_Flash_Tool payload from {SP_FLASH_SRC} -> {_sp_target}")
except Exception as exc:  # noqa: BLE001  (never fail a build over payload)
    print(f"[spec] WARNING: could not place SP_Flash_Tool payload: {exc}")
