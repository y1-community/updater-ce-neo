# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Updater CE (Windows portable exe).

Build from the project root:

    pyinstaller --clean --noconfirm InnioasisUpdater.spec

The resulting portable folder is dist/InnioasisUpdater with
InnioasisUpdater.exe and the bundled SP Flash Tool payload.
"""

import os
import shutil
import sys
from pathlib import Path

# SPECPATH is injected by PyInstaller as the directory containing this spec.
PROJECT_ROOT = Path(SPECPATH)  # noqa: F821
VENDOR_MTK = PROJECT_ROOT / "vendor" / "mtkclient"
ASSETS = PROJECT_ROOT / "assets"
ADB_TOOLS = PROJECT_ROOT / "tools" / "adb"

if str(VENDOR_MTK) not in sys.path:
    sys.path.insert(0, str(VENDOR_MTK))

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

# ---------------------------------------------------------------------------
# Runtime brand selection (optional).
# Set BUILD_BRAND=mediatek_installer to package the generic MediaTek
# Installer instead of Updater CE. The spec shared by both brands uses
# the same logic; only the display/slug names change.
# ---------------------------------------------------------------------------
_BUILD_BRAND = (os.environ.get("BUILD_BRAND") or "").strip().lower().replace("-", "_")
if not _BUILD_BRAND and (PROJECT_ROOT / "src" / "_build_brand.py").exists():
    for line in (PROJECT_ROOT / "src" / "_build_brand.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("BUILD_BRAND"):
            _BUILD_BRAND = line.split("=")[-1].strip().strip('"\'')
            break

IS_MEDIATEK_INSTALLER_BUILD = (_BUILD_BRAND == "mediatek_installer")
APP_DISPLAY_NAME = "MediaTek Installer" if IS_MEDIATEK_INSTALLER_BUILD else "Updater CE"
APP_SLUG = "MediaTekInstaller" if IS_MEDIATEK_INSTALLER_BUILD else "InnioasisUpdater"

print(f"[spec] packaging brand: {APP_DISPLAY_NAME} -> dist/{APP_SLUG}")

datas = []
if ASSETS.exists():
    datas.append((str(ASSETS), "assets"))
if VENDOR_MTK.exists():
    datas.append((str(VENDOR_MTK), "mtkclient"))
if ADB_TOOLS.exists():
    datas.append((str(ADB_TOOLS), "tools/adb"))

# The SP Flash Tool payload is placed next to the exe by a post-build copy
# step (below), not by PyInstaller datas, because datas always land under
# _internal and the runtime resolver looks next to the executable.
sp_candidates = [
    PROJECT_ROOT / "SP_Flash_Tool",
    PROJECT_ROOT / "tools" / "SP_Flash_Tool",
    PROJECT_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win"
    if sys.platform.startswith("win")
    else None,
]
SP_FLASH_SRC = next((p for p in sp_candidates if p and p.exists()), None)

hiddenimports = (
    collect_submodules("Cryptodome")
    + [
        "usb",
        "usb.backend.libusb1",
        "serial",
        "serial.tools.list_ports",
        "Cryptodome",
        "colorama",
        "logging.config",
        "pywinstyles",
        "qtwin11",
        "wx",
        "win32gui",
        "win32con",
        "win32api",
        "tkinter",
        "ttk",
    ]
)

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
    icon=str(ASSETS / "icon.ico") if (ASSETS / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name=APP_SLUG,
)

try:
    _dist_root = Path(DISTPATH)  # noqa: F821
    _app_dir = _dist_root / APP_SLUG
    _sp_target = _app_dir / "SP_Flash_Tool"
    if SP_FLASH_SRC and SP_FLASH_SRC.exists():
        if _sp_target.exists():
            shutil.rmtree(_sp_target, ignore_errors=True)
        shutil.copytree(SP_FLASH_SRC, _sp_target)
        print(f"[spec] copied SP_Flash_Tool payload from {SP_FLASH_SRC} -> {_sp_target}")
except Exception as exc:  # noqa: BLE001
    print(f"[spec] WARNING: could not place SP_Flash_Tool payload: {exc}")
