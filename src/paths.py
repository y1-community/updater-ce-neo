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


def get_appimage_path() -> Path | None:
    """Return the Path to the running AppImage file if running under an AppImage on Linux."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        try:
            return Path(appimage).resolve()
        except (OSError, ValueError):
            pass
    if sys.argv and sys.argv[0]:
        try:
            cand = Path(sys.argv[0]).resolve()
            if cand.suffix.lower() == ".appimage":
                return cand
        except (OSError, ValueError):
            pass
    return None


def get_appimage_dir() -> Path | None:
    """Return the host directory containing the AppImage if running under AppImage."""
    img = get_appimage_path()
    if img is not None:
        try:
            parent = img.parent
            if parent.is_dir():
                return parent
        except (OSError, ValueError):
            pass
    return None


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

# Locate assets directory across dev checkout, PyInstaller onedir (_internal/assets
# or assets beside exe), and packaged/installed layouts.
_asset_candidates = [
    BUNDLE_DIR / "assets",
    INSTALL_DIR / "assets",
    INSTALL_DIR / "_internal" / "assets",
    REPO_ROOT / "assets",
]
if getattr(sys, "frozen", False):
    try:
        _exe_parent = Path(sys.executable).resolve().parent
        _asset_candidates.extend([
            _exe_parent / "assets",
            _exe_parent / "_internal" / "assets",
        ])
    except Exception:
        pass

for _cand in _asset_candidates:
    if (_cand / "i18n").is_dir() or (_cand / "i18n" / "languages.json").is_file() or _cand.is_dir():
        RESOURCES_DIR = _cand
        if (_cand / "i18n" / "languages.json").is_file():
            break

COMPAT_DIR = RESOURCES_DIR / "compat"


def _is_package_dir(path: Path) -> bool:
    """True when ``path`` is an importable package directory.

    Both source and bytecode count: the bundled builds ship mtkclient
    precompiled (``__init__.pyc``, no ``.py`` — see
    ``scripts/precompile_tree.py``), and a bundle that only recognised
    ``__init__.py`` would stop finding its own mtkclient.
    """
    if (path / "__init__.py").is_file() or (path / "__init__.pyc").is_file():
        return True
    cache = path / "__pycache__"
    if cache.is_dir():
        try:
            return any(
                p.name.startswith("__init__.") and p.suffix == ".pyc" for p in cache.iterdir()
            )
        except OSError:
            return False
    return False


def _mtkclient_path_entry(cand: Path) -> str | None:
    """The ``sys.path`` entry that makes ``mtkclient`` importable from ``cand``.

    Two layouts exist in the wild: ``cand/mtkclient/`` (the vendored tree) and
    ``cand`` itself being the package (a PyInstaller bundle).
    """
    if _is_package_dir(cand / "mtkclient"):
        return str(cand)
    if _is_package_dir(cand):
        return str(cand.parent)
    return None


def ensure_mtkclient_importable():
    """Make the bundled mtkclient importable (idempotent).

    Called by every module that imports mtkclient so the import works both in
    a dev checkout (vendor/) and in a PyInstaller bundle (BUNDLE_DIR/mtkclient,
    Contents/Resources/mtkclient, etc.).
    """
    for stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
        if stream is not None and hasattr(stream, "detach"):
            try:
                import io
                def _no_detach():
                    raise io.UnsupportedOperation("detach disabled to protect stream buffer")
                stream.detach = _no_detach
            except Exception:
                pass

    candidates = [
        MTKCLIENT_DIR,
        BUNDLE_DIR / "mtkclient",
        BUNDLE_DIR.parent / "Resources" / "mtkclient" if getattr(sys, "frozen", False) else None,
        REPO_ROOT / "vendor" / "mtkclient",
    ]
    for cand in candidates:
        if not cand or not cand.exists():
            continue
        entry = _mtkclient_path_entry(Path(cand))
        if entry:
            if entry not in sys.path:
                sys.path.insert(0, entry)
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
IS_LINUX = sys.platform.startswith("linux") and not IS_MAC


def find_sp_flash_tool():
    """Locate the SP Flash Tool directory (Windows & Linux).

    Search order:
    1. ``$SP_FLASH_TOOL_DIR`` env var
    2. Staged or self-healed user tools directory
    3. Inno-installed / AppImage bundled ``SP_Flash_Tool`` folder
    4. Local development / repository fallback.
    """
    if IS_MAC:
        return None
    candidates = [
        Path(os.environ["SP_FLASH_TOOL_DIR"]) if os.environ.get("SP_FLASH_TOOL_DIR") else None,
        SP_FLASH_TOOL_DIR,
        INSTALL_DIR / "SP_Flash_Tool",
        INSTALL_DIR / "sp_flash_tool",
        INSTALL_DIR,
    ]
    appimage_dir = get_appimage_dir()
    if appimage_dir:
        candidates.extend([
            appimage_dir / "SP_Flash_Tool",
            appimage_dir / "sp_flash_tool",
            appimage_dir,
        ])
    candidates.extend([
        REPO_ROOT / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win",
        REPO_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux",
        Path.cwd() / "SP_Flash_Tool",
        Path.cwd() / "sp_flash_tool",
        Path.cwd(),
    ])
    if IS_WINDOWS:
        if os.environ.get("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Updater CE" / "tools" / "sp_flash_tool_win")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Updater CE" / "SP_Flash_Tool")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Innioasis Updater" / "tools" / "sp_flash_tool_win")
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Innioasis Updater" / "SP_Flash_Tool")
        candidates.append(Path.home() / "AppData" / "Local" / "Updater CE" / "tools" / "sp_flash_tool_win")
        candidates.append(Path.home() / "AppData" / "Local" / "Updater CE" / "SP_Flash_Tool")
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


#: Inno Setup's uninstall-key suffix for Updater CE (scripts/build_windows_installer.iss).
_WIN_CE_APP_ID = "{C78F982D-7B5F-48A1-8E23-DF3A0210A2B9}_is1"


def _windows_ce_installed() -> bool:
    """True when an Innioasis Updater CE install is registered on Windows."""
    try:
        from .store_package import is_packaged

        if is_packaged():
            return True
    except Exception:
        pass
    try:
        import winreg
    except Exception:
        return False
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    sub_keys = (
        r"Software\Microsoft\Windows\CurrentVersion\Uninstall",
        r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
    )
    for root in roots:
        for sub_key in sub_keys:
            try:
                with winreg.OpenKey(root, sub_key) as key:
                    count = winreg.QueryInfoKey(key)[0]
                    names = [winreg.EnumKey(key, index) for index in range(count)]
            except OSError:
                continue
            for name in names:
                if name == _WIN_CE_APP_ID:
                    return True
                try:
                    with winreg.OpenKey(root, sub_key + "\\" + name) as entry:
                        display, _kind = winreg.QueryValueEx(entry, "DisplayName")
                except OSError:
                    continue
                if "updater ce" in str(display or "").casefold():
                    return True
    return False


def _mac_ce_installed() -> bool:
    """True when an Innioasis Updater CE bundle sits in an Applications folder."""
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        try:
            bundles = list(folder.glob("*.app"))
        except OSError:
            continue
        for bundle in bundles:
            words = bundle.stem.casefold().split()
            if "updater" in words and "ce" in words:
                return True
    return False


def _linux_ce_installed() -> bool:
    """True when a desktop entry for Innioasis Updater CE is installed."""
    share = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    folders = (
        share / "applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
    )
    for folder in folders:
        try:
            entries = list(folder.glob("*.desktop"))
        except OSError:
            continue
        for entry in entries:
            try:
                content = entry.read_text(encoding="utf-8", errors="replace").casefold()
            except OSError:
                continue
            if "name=" in content and "updater ce" in content:
                return True
    return False


def installed_app_present() -> bool:
    """True when a real Innioasis Updater CE install exists on this computer.

    A portable (bundled firmware) run shares the app's settings store and its
    application-support/cache folders with an installed copy. When one is
    present those files belong to the installed app, so preferences saved
    during the run persist and the run's cleanup leaves them alone. The
    pre-3.0 Chinese updater does not count: it is a different app with its
    own install and its own settings.

    Detection never raises and answers ``False`` when it cannot tell. That is
    the safe answer: a run with no installed copy then removes only the
    working files it created itself.
    """
    try:
        if sys.platform == "win32":
            return _windows_ce_installed()
        if sys.platform == "darwin":
            return _mac_ce_installed()
        return _linux_ce_installed()
    except Exception:
        return False


def find_unrar() -> str:
    """Locate an UnRAR / unar executable across platforms."""
    candidates = [
        TOOLS_DIR / "UnRAR.exe",
        TOOLS_DIR / "unar.exe",
        TOOLS_DIR / "unar",
        TOOLS_DIR / "unrar",
        BUNDLE_DIR / "tools" / "UnRAR.exe",
        BUNDLE_DIR / "tools" / "unar.exe",
        BUNDLE_DIR / "tools" / "unar",
        BUNDLE_DIR / "tools" / "unrar",
        REPO_ROOT / "tools" / "windows" / "UnRAR.exe",
        REPO_ROOT / "tools" / "macos" / "unar",
        REPO_ROOT / "tools" / "linux" / "unar",
        REPO_ROOT / "tools" / "UnRAR.exe",
        REPO_ROOT / "tools" / "unar",
        Path.home() / "AppData" / "Local" / "Updater CE" / "tools" / "unrar_win" / "UnRAR.exe",
        Path.home() / "AppData" / "Local" / "Innioasis Updater" / "tools" / "unrar_win" / "UnRAR.exe",
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
    ]
    for p in candidates:
        if p and p.is_file():
            return str(p)
    on_path = _which("unar") or _which("unrar")
    if on_path:
        return on_path
    return ""


def _which(name: str):
    import shutil

    return shutil.which(name)


def app_cache_dir() -> Path:
    """Per-user cache directory for downloaded manifests, catalogs and PATs."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Updater CE" / ".cache"
    elif sys.platform == "darwin":
        # The app's own cache lives in the shared Application Support folder —
        # the pre-3.0 app's folder, which first launch clears of the old app's
        # files (see legacy_cleanup.clear_legacy_support_now).
        return Path.home() / "Library" / "Application Support" / "Innioasis Updater" / ".cache"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        target = base / "updater-ce"
        legacy = base / "innioasis-updater" / ".cache"
        if not target.exists() and legacy.exists():
            return legacy
        return target

