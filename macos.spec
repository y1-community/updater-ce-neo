# -*- mode: python ; coding: utf-8 -*-
# macos.spec — PyInstaller spec for macOS .app bundle
# Usage: pyinstaller macos.spec

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

PROJECT_ROOT = Path(SPECPATH)  # noqa: F821
VENDOR_MTK = PROJECT_ROOT / "vendor" / "mtkclient"
ASSETS = PROJECT_ROOT / "assets"
LIBUSB_DYLIB = VENDOR_MTK / "mtkclient" / "Darwin" / "libusb-1.0.dylib"

if str(VENDOR_MTK) not in sys.path:
    sys.path.insert(0, str(VENDOR_MTK))

block_cipher = None

# Collect pure-Python submodules for Cryptodome, pyusb, pyserial, and libusb_package
crypto_hidden = collect_submodules("Cryptodome")
usb_hidden = collect_submodules("usb")
serial_hidden = collect_submodules("serial")
libusb_package_hidden = collect_submodules("libusb_package")

datas = []
if ASSETS.exists():
    datas.append((str(ASSETS), "assets"))
if VENDOR_MTK.exists():
    datas.append((str(VENDOR_MTK), "mtkclient"))

try:
    datas.extend(collect_data_files("libusb_package"))
except Exception:
    pass

binaries = []
if LIBUSB_DYLIB.exists():
    binaries.append((str(LIBUSB_DYLIB), "."))

hiddenimports = (
    crypto_hidden
    + usb_hidden
    + serial_hidden
    + libusb_package_hidden
    + [
        # macOS UI & glass
        "PySide6.QtSvg",
        "PySide6.QtNetwork",
        "pyqt_liquidglass",
        "objc",
        "AppKit",
        "Foundation",
        "Quartz",
        "src.ui.glass",
        # MTK & USB dependencies
        "usb",
        "usb.core",
        "usb.backend.libusb1",
        "usb.backend.libusb0",
        "serial",
        "serial.tools.list_ports",
        "serial.tools.list_ports_osx",
        "serial.tools.list_ports_posix",
        "Cryptodome",
        "colorama",
        "logging.config",
        "capstone",
        "shiboken6",
        "src.mtk_api",
        "src.flash_service",
    ]
)

a = Analysis(
    ["launcher.py"],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mtkclient"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

target_arch = os.environ.get("TARGET_ARCH") or None

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Innioasis Updater CE",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Innioasis Updater",
)

app = BUNDLE(
    coll,
    name="Innioasis Updater CE.app",
    icon="assets/icon.icns",
    bundle_identifier="com.innioasis.updater",
    info_plist={
        "CFBundleDisplayName": "Innioasis Updater CE",
        "CFBundleShortVersionString": "3.0.0",
        "CFBundleVersion": "3.0.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "CFBundleDocumentTypes": [
            {
                "CFBundleTypeName": "Firmware Archive",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": [
                    "com.pkware.zip-archive",
                    "public.zip-archive",
                    "org.rarlab.rar-archive",
                ],
            }
        ],
    },
)
