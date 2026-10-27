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

_dist_root = Path(DISTPATH) if "DISTPATH" in globals() else (PROJECT_ROOT / "dist")  # noqa: F821
print(f"[spec] packaging brand: {APP_DISPLAY_NAME} -> {_dist_root / APP_SLUG}")

datas = []
if ASSETS.exists():
    datas.append((str(ASSETS), "assets"))
if VENDOR_MTK.exists():
    compile_tree_if_stale([VENDOR_MTK], MTK_PRECOMPILED, strict=False)
    datas.append((str(MTK_PRECOMPILED), "mtkclient"))
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
    PROJECT_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux"
    if sys.platform.startswith("linux")
    else None,
]
SP_FLASH_SRC = next((p for p in sp_candidates if p and p.exists()), None)


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
    for dll_name in ("libusb-1.0.dll", "libusb32-1.0.dll"):
        dll_path = VENDOR_MTK / "mtkclient" / "Windows" / dll_name
        if dll_path.exists():
            binaries.append((str(dll_path), "."))

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
    _app_dir = _dist_root / APP_SLUG
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
    _unrar_src = PROJECT_ROOT / "tools" / "windows" / "UnRAR.exe"
    if _unrar_src.exists():
        _tools_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_unrar_src, _tools_dir / "UnRAR.exe")
        print(f"[spec] copied UnRAR payload from {_unrar_src} -> {_tools_dir / 'UnRAR.exe'}")
except Exception as exc:  # noqa: BLE001
    print(f"[spec] WARNING: could not place SP_Flash_Tool payload: {exc}")
