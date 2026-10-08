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

# --- Packaging brand --------------------------------------------------------
# BUILD_BRAND=mediatek_installer builds the same engine as the generic
# cross-platform MediaTek Installer: its own bundle name and identifier.
# Anything else (default) builds Updater CE.
_BUILD_BRAND = (os.environ.get("BUILD_BRAND") or "").strip().lower().replace("-", "_")
IS_MEDIATEK_INSTALLER_BUILD = _BUILD_BRAND in ("mediatek_installer", "generic_mtk")
APP_DISPLAY_NAME = "MediaTek Installer" if IS_MEDIATEK_INSTALLER_BUILD else "Updater CE"
BUNDLE_ID = (
    "com.innioasis.mediatekinstaller" if IS_MEDIATEK_INSTALLER_BUILD else "com.innioasis.updater"
)

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


def _mtkclient_runtime_imports():
    """Return importable modules referenced by the vendored mtkclient sources.

    mtkclient is shipped as plain source (``excludes=["mtkclient"]`` below) and
    imported at runtime via ``paths.ensure_mtkclient_importable()``, so
    PyInstaller never sees its imports. Without this scan, stdlib modules only
    mtkclient uses (``unittest``, ``termios``, ``hmac``, ``socket`` ...) and
    third-party submodules (``Cryptodome.Cipher.AES``, ``usb.util`` ...) would
    be missing from the frozen interpreter.
    """
    import ast
    import importlib.util

    names = set()
    pkg_root = VENDOR_MTK / "mtkclient"
    for py in pkg_root.rglob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module)
                names.update(f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*")

    found = set()
    for name in names:
        if name.split(".")[0] in ("mtkclient", "__future__"):
            continue
        try:
            if importlib.util.find_spec(name) is not None:
                found.add(name)
        except (ImportError, ValueError, AttributeError):
            pass
    return sorted(found)


mtkclient_hidden = _mtkclient_runtime_imports()
src_hidden = collect_submodules("src")

binaries = []
if LIBUSB_DYLIB.exists():
    binaries.append((str(LIBUSB_DYLIB), "."))
    binaries.append((str(LIBUSB_DYLIB), "Frameworks"))

hiddenimports = (
    crypto_hidden
    + usb_hidden
    + serial_hidden
    + libusb_package_hidden
    + mtkclient_hidden
    + src_hidden
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

target_arch = os.environ.get("TARGET_ARCH") or "universal2"

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_DISPLAY_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=str(ASSETS / "entitlements.plist") if (ASSETS / "entitlements.plist").exists() else None,
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
    name=APP_DISPLAY_NAME,
)

app = BUNDLE(
    coll,
    name=f"{APP_DISPLAY_NAME}.app",
    icon="assets/icon.icns",
    bundle_identifier=BUNDLE_ID,
    info_plist={
        "CFBundleDisplayName": APP_DISPLAY_NAME,
        "CFBundleShortVersionString": "3.0.0",
        "CFBundleVersion": "3.0.0",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHumanReadableCopyright": "Copyright © 2024-2026 Innioasis Community. All rights reserved.",
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

# Ensure the .app is created even when running in non-Darwin environments
try:
    import shutil
    import plistlib
    dist_dir = PROJECT_ROOT / "dist"
    app_dir = dist_dir / f"{APP_DISPLAY_NAME}.app"
    coll_dir = dist_dir / APP_DISPLAY_NAME
    if not app_dir.exists() and coll_dir.exists():
        contents_dir = app_dir / "Contents"
        macos_dir = contents_dir / "MacOS"
        resources_dir = contents_dir / "Resources"
        frameworks_dir = contents_dir / "Frameworks"
        macos_dir.mkdir(parents=True, exist_ok=True)
        resources_dir.mkdir(parents=True, exist_ok=True)
        frameworks_dir.mkdir(parents=True, exist_ok=True)

        if (coll_dir / APP_DISPLAY_NAME).exists():
            shutil.copy2(coll_dir / APP_DISPLAY_NAME, macos_dir / APP_DISPLAY_NAME)
            os.chmod(macos_dir / APP_DISPLAY_NAME, 0o755)
        if (coll_dir / "_internal").exists():
            shutil.copytree(coll_dir / "_internal", macos_dir / "_internal", dirs_exist_ok=True)

        if LIBUSB_DYLIB.exists():
            shutil.copy2(LIBUSB_DYLIB, frameworks_dir / "libusb-1.0.dylib")
            shutil.copy2(LIBUSB_DYLIB, macos_dir / "libusb-1.0.dylib")

        if (ASSETS / "icon.icns").exists():
            shutil.copy2(ASSETS / "icon.icns", resources_dir / "icon.icns")

        (contents_dir / "PkgInfo").write_bytes(b"APPL????")

        plist_data = {
            "CFBundleDisplayName": APP_DISPLAY_NAME,
            "CFBundleExecutable": APP_DISPLAY_NAME,
            "CFBundleIconFile": "icon.icns",
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": APP_DISPLAY_NAME,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "3.0.0",
            "CFBundleVersion": "3.0.0",
            "LSApplicationCategoryType": "public.app-category.utilities",
            "LSMinimumSystemVersion": "13.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSPrincipalClass": "NSApplication",
            "NSHumanReadableCopyright": "Copyright © 2024-2026 Innioasis Community. All rights reserved.",
        }
        with open(contents_dir / "Info.plist", "wb") as f:
            plistlib.dump(plist_data, f)
except Exception:
    pass
