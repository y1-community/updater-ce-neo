# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Updater CE (Windows portable exe).

Build from the project root:

    pyinstaller --clean --noconfirm InnioasisUpdater.spec

The resulting portable folder is dist/InnioasisUpdater with
InnioasisUpdater.exe and the bundled SP Flash Tool payload.
"""

import os
import platform
import re
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

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from precompile_tree import compile_tree_if_stale  # noqa: E402  (build helper)

# mtkclient ships as *data*, never in the PYZ: it is imported at runtime from a
# directory added to sys.path (see excludes= below). Compile it to bytecode
# first, because a frozen app cannot write __pycache__ beside the data (Program
# Files, a read-only install dir) and would otherwise re-parse the whole tree on
# every launch. Bytecode-only trees import normally: see scripts/precompile_tree.py.
MTK_PRECOMPILED = PROJECT_ROOT / ".cache" / "precompiled-mtkclient"

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
APP_AUTHOR = "Ryan Specter"
APP_COPYRIGHT = f"Copyright (C) 2024-2026 {APP_AUTHOR}. All rights reserved."
APP_VERSION = "2.5.3"
try:
    for _config_line in (PROJECT_ROOT / "src" / "config.py").read_text(encoding="utf-8").splitlines():
        if _config_line.strip().startswith("APP_VERSION"):
            APP_VERSION = _config_line.split("=", 1)[1].strip().strip("\"'")
            break
except OSError:
    pass

_dist_root = Path(DISTPATH) if "DISTPATH" in globals() else (PROJECT_ROOT / "dist")  # noqa: F821
print(f"[spec] packaging brand: {APP_DISPLAY_NAME} -> {_dist_root / APP_SLUG}")

datas = []
if ASSETS.exists():
    datas.append((str(ASSETS), "assets"))
if VENDOR_MTK.exists():
    compile_tree_if_stale([VENDOR_MTK], MTK_PRECOMPILED, strict=False)
    datas.append((str(MTK_PRECOMPILED), "mtkclient"))


# The SP Flash Tool payload is placed next to the exe by a post-build copy
# step (below), not by PyInstaller datas, because datas always land under
# _internal and the runtime resolver looks next to the executable.
#
# SP Flash Tool exists as an x86/x86-64 build only. On an ARM64 host (Windows
# ARM64, Linux aarch64/armhf) it cannot run and the app uses MTKClient as its
# only engine, exactly as it does on macOS, so bundling the x86 payload would
# add a few hundred megabytes of unusable binaries that the runtime resolver
# would then have to reject. Match src/linux_sp_flash.arch_supported().
_X86_MACHINES = ("x86_64", "amd64", "x64", "i386", "i486", "i586", "i686", "x86")
# The post-build payload staging below also needs the machine name. It used to
# reference an undefined HOST_MACHINE, so the unrar copy was skipped by the
# surrounding try/except on every Linux build.
HOST_MACHINE = (os.environ.get("TARGET_ARCH") or platform.machine() or "").strip().lower()
_env_target_arch = (os.environ.get("TARGET_ARCH") or os.environ.get("BUILD_ARCH") or "").strip().lower()
if _env_target_arch:
    HOST_IS_X86 = _env_target_arch in _X86_MACHINES and _env_target_arch not in ("arm64", "aarch64")
else:
    HOST_IS_X86 = (platform.machine() or "").lower() in _X86_MACHINES

sp_candidates = [
    PROJECT_ROOT / "SP_Flash_Tool" if HOST_IS_X86 else None,
    PROJECT_ROOT / "tools" / "SP_Flash_Tool" if HOST_IS_X86 else None,
    PROJECT_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win"
    if sys.platform.startswith("win") and HOST_IS_X86
    else None,
    PROJECT_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux"
    if sys.platform.startswith("linux") and HOST_IS_X86
    else None,
]
SP_FLASH_SRC = next((p for p in sp_candidates if p and p.exists()), None)
if SP_FLASH_SRC is None and not HOST_IS_X86:
    print(
        f"[spec] {platform.machine()}: SP Flash Tool is x86-only; "
        "packaging MTKClient as the only flash engine"
    )

# ADB is only needed for the host platform this build runs on. Copying the
# whole tools/adb tree put roughly 49 MB of Windows, macOS and other-arch
# binaries into every artifact. macOS is the one exception because the app is
# universal2 and launches whichever binary matches the machine it runs on.
if ADB_TOOLS.exists():
    if sys.platform == "darwin":
        _adb_dirs = ["macos-x64", "macos-arm64"]
    elif sys.platform.startswith("win"):
        _adb_dirs = ["windows-x64"]
    else:
        _adb_dirs = ["linux-x64"] if HOST_IS_X86 else ["linux-arm64"]
    for _adb_dir in _adb_dirs:
        _adb_src = ADB_TOOLS / _adb_dir
        if _adb_src.is_dir():
            datas.append((str(_adb_src), f"tools/adb/{_adb_dir}"))
            print(f"[spec] bundling {_adb_dir} ADB payload")
        else:
            print(f"[spec] WARNING: ADB payload missing: {_adb_src}")


def _mtkclient_runtime_imports():
    """Return importable modules referenced by the vendored mtkclient sources."""
    import ast
    import importlib.util

    names = set()
    pkg_root = VENDOR_MTK / "mtkclient"
    if not pkg_root.exists():
        return []
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
                names.update(
                    f"{node.module}.{alias.name}" for alias in node.names if alias.name != "*"
                )

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


binaries = []
if sys.platform.startswith("win"):
    # libusb is loaded into this process, so it must match the interpreter's
    # architecture: an ARM64 build cannot use the x64 helper that mirrors the
    # shipped Chinese build. The arm64 copy is this repo's own addition
    # (vendor/mtkclient/mtkclient/Windows/arm64/README.md).
    libusb_dir = VENDOR_MTK / "mtkclient" / "Windows" / ("" if HOST_IS_X86 else "arm64")
    libusb_names = ("libusb-1.0.dll", "libusb32-1.0.dll") if HOST_IS_X86 else ("libusb-1.0.dll",)
    for dll_name in libusb_names:
        dll_path = libusb_dir / dll_name
        if not dll_path.exists() and not HOST_IS_X86:
            print(f"[spec] WARNING: ARM64 libusb payload missing: {dll_path}")
        if dll_path.exists():
            binaries.append((str(dll_path), "."))
            print(f"[spec] bundling libusb payload: {dll_path}")

hiddenimports = (
    collect_submodules("Cryptodome")
    + collect_submodules("usb")
    + collect_submodules("serial")
    + collect_submodules("src")
    + _mtkclient_runtime_imports()
    + [
        "PySide6.QtSvg",
        "PySide6.QtNetwork",
        "shiboken6",
        "capstone",
        "usb",
        "usb.core",
        "usb.backend.libusb1",
        "usb.backend.libusb0",
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
        "src.mtk_api",
        "src.flash_service",
    ]
)

a = Analysis(
    [str(PROJECT_ROOT / "launcher.py")],
    pathex=[str(PROJECT_ROOT), str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["mtkclient"],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Windows shows these fields in the executable's Properties > Details pane and
# in the uninstall entry. Generate the resource per brand so the portable EXE
# and the installed app carry the same author, product and copyright credit.
_version_parts = [int(part) for part in re.findall(r"\d+", APP_VERSION)[:4]]
_version_parts += [0] * (4 - len(_version_parts))
_version_tuple = tuple(_version_parts)
_version_info_path = PROJECT_ROOT / ".cache" / "windows-version" / f"{APP_SLUG}.txt"
_version_info_path.parent.mkdir(parents=True, exist_ok=True)
_version_info_path.write_text(
    "VSVersionInfo(\n"
    "  ffi=FixedFileInfo(\n"
    f"    filevers={_version_tuple},\n"
    f"    prodvers={_version_tuple},\n"
    "    mask=0x3f,\n"
    "    flags=0x0,\n"
    "    OS=0x40004,\n"
    "    fileType=0x1,\n"
    "    subtype=0x0,\n"
    "    date=(0, 0)\n"
    "  ),\n"
    "  kids=[\n"
    "    StringFileInfo([\n"
    "      StringTable(\n"
    "        u'040904B0',\n"
    "        [StringStruct(u'CompanyName', u'" + APP_AUTHOR + "'),\n"
    "         StringStruct(u'FileDescription', u'" + APP_DISPLAY_NAME + "'),\n"
    "         StringStruct(u'FileVersion', u'" + APP_VERSION + "'),\n"
    "         StringStruct(u'InternalName', u'" + APP_SLUG + "'),\n"
    "         StringStruct(u'LegalCopyright', u'" + APP_COPYRIGHT + "'),\n"
    "         StringStruct(u'OriginalFilename', u'" + APP_SLUG + ".exe'),\n"
    "         StringStruct(u'ProductName', u'" + APP_DISPLAY_NAME + "'),\n"
    "         StringStruct(u'ProductVersion', u'" + APP_VERSION + "'),\n"
    "         StringStruct(u'Comments', u'Author: " + APP_AUTHOR + "')]\n"
    "      )\n"
    "    ]),\n"
    "    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])\n"
    "  ]\n"
    ")\n",
    encoding="utf-8",
)

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
    version=str(_version_info_path) if sys.platform.startswith("win") else None,
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
    _app_dir = _dist_root / APP_SLUG
    # The 360p themes archive is ~62 MB and is refreshed weekly from the remote
    # pack; only the small fonts pack stays in the build. The macOS builder
    # already did this, and the Linux/Windows spec used to ship the archive.
    _themes_zip = _app_dir / "assets" / "360p" / "themes-themepack.zip"
    if _themes_zip.is_file():
        _themes_zip.unlink()
        print("[spec] removed the bundled 360p themes archive")
    _themes_dir = _app_dir / "assets" / "360p" / "themes-themepack"
    if _themes_dir.exists():
        shutil.rmtree(_themes_dir, ignore_errors=True)

    _sp_target = _app_dir / "SP_Flash_Tool"
    if SP_FLASH_SRC and SP_FLASH_SRC.exists():
        if _sp_target.exists():
            shutil.rmtree(_sp_target, ignore_errors=True)
        shutil.copytree(SP_FLASH_SRC, _sp_target)
        # Ensure packaged SP_Flash_Tool has clean history.ini and no developer logs
        _hist = _sp_target / "history.ini"
        if _hist.exists():
            _hist.write_text(
                "[RecentOpenFile]\nscatterHistory=@Invalid()\nauthHistory=@Invalid()\n",
                encoding="utf-8",
            )
        for _log_dir in ("SP_FT_Logs", "log", "logs"):
            if (_sp_target / _log_dir).is_dir():
                shutil.rmtree(_sp_target / _log_dir, ignore_errors=True)
        print(f"[spec] copied SP_Flash_Tool payload from {SP_FLASH_SRC} -> {_sp_target}")

    _tools_dir = _app_dir / "tools"
    if sys.platform.startswith("win"):
        _win_arch_dir = "x64" if HOST_IS_X86 else "arm64"
        _unrar_src = PROJECT_ROOT / "tools" / "windows" / _win_arch_dir / "UnRAR.exe"
        if not _unrar_src.exists():
            _unrar_src = PROJECT_ROOT / "tools" / "windows" / "UnRAR.exe"
        if _unrar_src.exists():
            _tools_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_unrar_src, _tools_dir / "UnRAR.exe")
            print(f"[spec] copied UnRAR payload from {_unrar_src} -> {_tools_dir / 'UnRAR.exe'}")
    elif sys.platform.startswith("linux"):
        if HOST_MACHINE in ("aarch64", "arm64"):
            _lin_arch_dir = "aarch64"
        elif HOST_MACHINE.startswith("arm"):
            _lin_arch_dir = "armhf"
        else:
            _lin_arch_dir = "x86_64"
        _unrar_src = PROJECT_ROOT / "tools" / "linux" / _lin_arch_dir / "unrar"
        if not _unrar_src.exists():
            _unrar_src = PROJECT_ROOT / "tools" / "linux" / "unrar"
        if _unrar_src.exists():
            _tools_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_unrar_src, _tools_dir / "unrar")
            os.chmod(_tools_dir / "unrar", 0o755)
            print(f"[spec] copied unrar payload from {_unrar_src} -> {_tools_dir / 'unrar'}")
except Exception as exc:  # noqa: BLE001
    print(f"[spec] WARNING: could not place SP_Flash_Tool payload: {exc}")
