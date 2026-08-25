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

import sys
from pathlib import Path

# SPECPATH is the directory containing this spec file (the project's build/).
PROJECT_ROOT = Path(SPECPATH)  # noqa: F821  (SPECPATH injected by PyInstaller)
VENDOR_MTK = PROJECT_ROOT / "vendor" / "mtkclient"
ASSETS = PROJECT_ROOT / "assets"

# Make mtkclient importable at build time so collect_submodules can see it.
if str(VENDOR_MTK) not in sys.path:
    sys.path.insert(0, str(VENDOR_MTK))

from PyInstaller.utils.hooks import collect_submodules  # noqa: E402

mtk_hidden = []
# Cryptodome (pycryptodomex): the hook only bundles the C-extension binaries;
# the pure-Python submodules must be declared explicitly because mtkclient is
# bundled as a data directory (invisible to PyInstaller's static analysis).
crypto_hidden = collect_submodules("Cryptodome")

datas = [
    (str(ASSETS / "style.qss"), "assets"),
    (str(ASSETS / "donors.csv"), "assets"),
    (str(ASSETS / "icon.ico"), "assets"),
    # CE-style guidance / device illustrations used by the flash page.
    (str(ASSETS / "guide_img1.png"), "assets"),
    (str(ASSETS / "guide_img2.png"), "assets"),
    (str(ASSETS / "guide_img3.png"), "assets"),
    (str(ASSETS / "guide_img4.png"), "assets"),
    (str(ASSETS / "y1_illustration.png"), "assets"),
    (str(ASSETS / "start_here.png"), "assets"),
    (str(ASSETS / "ready.png"), "assets"),
    (str(ASSETS / "sleeping.png"), "assets"),
    (str(ASSETS / "installing.png"), "assets"),
    (str(ASSETS / "installed.png"), "assets"),
    # firmware_downloader.py-style guided install images (presteps →
    # initsteps → please_wait → installing) with platform variants.
    (str(ASSETS / "presteps.png"), "assets"),
    (str(ASSETS / "initsteps.png"), "assets"),
    (str(ASSETS / "initsteps_win.png"), "assets"),
    (str(ASSETS / "initsteps_sp.png"), "assets"),
    (str(ASSETS / "please_wait.png"), "assets"),
    (str(ASSETS / "reconnect.png"), "assets"),
]
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
    name="InnioasisUpdater",
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
    name="InnioasisUpdater",
)

# ---------------------------------------------------------------------------
# Post-build payload placement: the app resolves SP Flash Tool at
# INSTALL_DIR / "SP_Flash_Tool" (next to the exe), which PyInstaller datas
# cannot target (datas always land under _internal). Without this a --clean
# rebuild silently drops the payload and Auto mode fails with
# "SP Flash Tool not found". Copy the whole tree next to the exe after COLLECT.
# ---------------------------------------------------------------------------
import shutil  # noqa: E402

SP_FLASH_SRC = PROJECT_ROOT / "SP_Flash_Tool"
try:
    _dist_root = Path(DISTPATH)  # noqa: F821  (injected by PyInstaller)
    _app_dir = _dist_root / "InnioasisUpdater"
    _sp_target = _app_dir / "SP_Flash_Tool"
    if SP_FLASH_SRC.exists():
        if _sp_target.exists():
            shutil.rmtree(_sp_target, ignore_errors=True)
        shutil.copytree(SP_FLASH_SRC, _sp_target)
        print(f"[spec] copied SP_Flash_Tool payload -> {_sp_target}")
except Exception as exc:  # noqa: BLE001  (never fail a build over payload)
    print(f"[spec] WARNING: could not place SP_Flash_Tool payload: {exc}")
