"""Legacy pre-3.0 Innioasis Updater detection and cleanup service.

Pre-3.0 versions of Innioasis Updater were installed via shell scripts (run_mac.sh
and run_linux.sh), which created full Git clones, Python virtual environments, and
various platform-specific artifacts. These installations consumed significant disk
space (typically 500 MB - 1 GB+).

Updater CE 3.0 is a complete rewrite and standalone replacement packaged as compact,
optimized native binaries.

SAFETY INVARIANTS:
1. Both pre-3.0 bundles are old apps and are offered for removal:
   'Innioasis Updater.app' (with space, created by pre-3.0 run_mac.sh) and
   'InnioasisUpdater.app' (no space). Either or both may be installed.
2. NEVER detect, touch, or remove the bundle this process is running from,
   whatever it is called: `_running_app_bundle()` is resolved from
   ``sys.executable`` and both detection and removal refuse it. The current
   product names ('Updater CE.app', 'UpdaterCE.app', 'MediaTek Installer.app')
   are refused by name as well.
3. Updater CE stores data under 'Updater CE'; legacy application-support folders are copied only after app-owned markers are found, and the source is retained.
"""

import os
import sys
import shutil
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Current product bundles. Their *contents* are the app, never a leftover, so
# they are refused even when an old copy sits in another Applications folder.
PROTECTED_APP_NAMES = {
    "updater ce.app",
    "updaterce.app",
    "mediatek installer.app",
}

# The old apps this service offers to remove. The no-space bundle is the
# standalone offline tool; the spaced one is what run_mac.sh installed.
LEGACY_APP_NAMES = (
    "Innioasis Updater.app",
    "InnioasisUpdater.app",
)
LEGACY_APP_DIRS = (
    Path("/Applications"),
    Path.home() / "Applications",
)

LEGACY_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Innioasis Updater"
LEGACY_SUPPORT_DIRS = (
    Path.home() / "Library" / "Application Support" / "Innioasis Updater",
    Path.home() / "Library" / "Application Support" / "InnioasisUpdater",
    Path.home() / "Library" / "Application Support" / "innioasis-updater",
)
LEGACY_DOWNLOADS_DIR = Path.home() / "Library" / "Application Support" / "innioasis-updater" / "downloads"
CURRENT_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Updater CE"
LEGACY_DATA_MARKERS = ("firmware_downloader.py", "run_mac.sh", "run_linux.sh")
PRE_3_0_APP_SUPPORT_MARKER = "firmware_downloader.py"
MIGRATION_MARKER = ".updater-ce-legacy-migration-v1"


def has_legacy_firmware_downloader(path: Path) -> bool:
    """True when path or its contents hold the legacy firmware_downloader.py marker."""
    folder = Path(path)
    try:
        if not folder.is_dir():
            return False
        if (folder / PRE_3_0_APP_SUPPORT_MARKER).is_file():
            return True
        for entry in folder.iterdir():
            if entry.is_file() and entry.name == PRE_3_0_APP_SUPPORT_MARKER:
                return True
            if entry.is_dir() and not entry.name.startswith("."):
                try:
                    if (entry / PRE_3_0_APP_SUPPORT_MARKER).is_file():
                        return True
                except OSError:
                    pass
    except OSError:
        pass
    return False

LEGACY_LINUX_INSTALL_DIR = Path.home() / ".local" / "share" / "innioasis-updater"
# These three are shared with the running app (see install.sh): they belong to
# whatever is installed, so on their own they never raise the removal offer.
LEGACY_LINUX_DESKTOP_FILE = (
    Path.home() / ".local" / "share" / "applications" / "innioasis-updater.desktop"
)
LEGACY_LINUX_BIN_LAUNCHER = Path.home() / ".local" / "bin" / "innioasis-updater"
LEGACY_LINUX_CACHE_DIR = Path.home() / ".cache" / "innioasis-updater"
# The only thing that makes a folder an uninstallable pre-3.0 install. The
# current release installs into the same directory (see install.sh), so
# something inside it has to still belong to the old app.
PRE_3_0_LINUX_MARKER = "firmware_downloader.py"


def looks_like_pre_3_0_linux_install(path: Path = None) -> bool:
    """True when ``path`` is a pre-3.0 install the app may offer to remove.

    The install directory alone proves nothing: the current release reuses
    ``~/.local/share/innioasis-updater``, so its mere existence is not an old
    install. Only a folder that still holds the old ``firmware_downloader.py``
    is one -- and with no such file there is nothing to help remove, so the
    offer is never made.
    """
    folder = Path(path) if path is not None else LEGACY_LINUX_INSTALL_DIR
    try:
        return (folder / PRE_3_0_LINUX_MARKER).is_file()
    except OSError:
        return False


def legacy_folder_is_app_owned(path: Path) -> bool:
    """Require a recognizable legacy app marker before offering folder cleanup."""
    folder = Path(path)
    if folder == LEGACY_DOWNLOADS_DIR:
        return folder.is_dir()
    return folder.is_dir() and (
        any((folder / marker).is_file() for marker in LEGACY_DATA_MARKERS)
        or any((folder / name).exists() for name in ("downloads", ".cache", "SP_Flash_Tool"))
    )


def _copy_missing_tree(source: Path, destination: Path, copied: list[str], prefix: str = "") -> None:
    """Copy regular files/directories that are absent; preserve target data and skip symlinks."""
    destination.mkdir(parents=True, exist_ok=True)
    with os.scandir(source) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            src = Path(entry.path)
            dst = destination / entry.name
            relative = f"{prefix}{entry.name}"
            if entry.is_dir(follow_symlinks=False):
                if dst.exists() and not dst.is_dir():
                    continue
                _copy_missing_tree(src, dst, copied, relative + "/")
            elif entry.is_file(follow_symlinks=False):
                if dst.exists():
                    continue
                shutil.copy2(src, dst)
                copied.append(relative)


def migrate_legacy_download_cache(source: Path = None, destination: Path = None) -> tuple[bool, str]:
    """Copy the app's old downloads directory once; retain it and preserve existing target files."""
    import json

    source = Path(source or LEGACY_DOWNLOADS_DIR)
    destination = Path(destination or (CURRENT_SUPPORT_DIR / "downloads"))
    marker = destination / MIGRATION_MARKER
    if marker.is_file():
        return True, "already migrated"
    if not source.is_dir():
        return False, "legacy downloads not found"
    copied = []
    try:
        _copy_missing_tree(source, destination, copied)
        marker.write_text(json.dumps({"source": str(source), "copied": copied}), encoding="utf-8")
    except OSError as exc:
        return False, f"could not copy legacy downloads: {exc}"
    return True, ", ".join(copied) if copied else "no files to copy"


def migrate_legacy_application_support(source: Path = None, destination: Path = None) -> tuple[bool, str]:
    """One-time, non-destructive migration of known app-owned legacy data.

    Data is copied first, existing destination entries win, and the legacy tree
    is retained for the user/explicit cleanup prompt. Unknown adjacent user
    files are never moved or removed.
    """
    import json

    source = Path(source or LEGACY_SUPPORT_DIR)
    destination = Path(destination or CURRENT_SUPPORT_DIR)
    marker = destination / MIGRATION_MARKER
    if marker.is_file():
        return True, "already migrated"
    owns_support = legacy_folder_is_app_owned(source)
    owns_downloads = LEGACY_DOWNLOADS_DIR.is_dir()
    if not owns_support and not owns_downloads:
        return False, "legacy app marker not found"

    destination.mkdir(parents=True, exist_ok=True)
    copied = []
    # Only known app subdirectories are migrated; the old tree is never removed.
    if owns_support:
        for name in ("downloads", ".cache", "SP_Flash_Tool", "tools"):
            src = source / name
            if not src.is_dir():
                continue
            try:
                _copy_missing_tree(src, destination / name, copied, name + "/")
            except OSError as exc:
                return False, f"could not copy {name}: {exc}"
    download_note = ""
    if owns_downloads:
        ok, download_note = migrate_legacy_download_cache(
            LEGACY_DOWNLOADS_DIR, destination / "downloads"
        )
        if not ok:
            return False, download_note
    try:
        marker.write_text(json.dumps({"source": str(source), "copied": copied}), encoding="utf-8")
    except OSError as exc:
        return False, f"could not write migration marker: {exc}"
    details = [note for note in (", ".join(copied), download_note) if note]
    return True, "; ".join(details) if details else "no known data to copy"


def _calc_dir_size(path: Path) -> int:
    """Calculate directory size in bytes safely."""
    total = 0
    try:
        if path.is_file() or path.is_symlink():
            return path.stat().st_size
        for root, dirs, files in os.walk(path):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total += os.path.getsize(fp)
                except (OSError, IOError):
                    pass
    except (OSError, IOError):
        pass
    return total


def format_bytes(n: int) -> str:
    """Format bytes into human-readable string (e.g. 750 MB)."""
    if n < 1024:
        return f"{n} B"
    kb = n / 1024.0
    if kb < 1024:
        return f"{kb:.1f} KB"
    mb = kb / 1024.0
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024.0
    return f"{gb:.2f} GB"


def _running_app_bundle() -> Optional[Path]:
    """The .app bundle this process runs from, when there is one.

    A frozen build lives at ``<bundle>.app/Contents/MacOS/<exe>``, so the bundle
    is the first ``.app`` ancestor of the executable. Running from a source
    checkout finds nothing, which is correct: there is no bundle to protect.
    """
    try:
        exe = Path(sys.executable).resolve()
    except Exception:
        return None
    for parent in exe.parents:
        if parent.suffix == ".app":
            return parent
    return None


def is_protected_app(path: Path) -> bool:
    """Return True if path must never be removed.

    Two ways to be protected: the name is a current product bundle, or the path
    *is* the bundle this process is running from. The second rule is what keeps
    a build named 'InnioasisUpdater.app' from offering to delete itself while it
    still treats that name, elsewhere on disk, as an old app.
    """
    name_lower = path.name.lower().strip()
    if name_lower in PROTECTED_APP_NAMES:
        return True
    running = _running_app_bundle()
    if running is None:
        return False
    try:
        return path.resolve() == running
    except OSError:
        return False


def is_legacy_app(path: Path) -> bool:
    """Return True when ``path`` is one of the old app bundles this offers."""
    name_lower = path.name.lower().strip()
    return name_lower in {name.lower() for name in LEGACY_APP_NAMES}


def detect_legacy_installations() -> dict:
    """Scan for pre-3.0 legacy installations across macOS and Linux.

    Returns a dictionary detailing detected components and estimated reclaimable space.
    """
    result = {
        "has_legacy": False,
        "macos_apps": [],
        "macos_app_support": None,
        "linux_install_dir": None,
        "linux_desktop_file": None,
        "linux_bin_launcher": None,
        "linux_cache_dir": None,
        "linux_desktop_shortcuts": [],
        "windows_legacy_dirs": [],
        "has_platform_tools": False,
        "platform_tools_type": None,
        "platform_tools_paths": [],
        "estimated_bytes": 0,
        "items_summary": [],
    }

    # 1. macOS Detection
    if sys.platform == "darwin" or os.environ.get("INNIOASIS_SIMULATE_MACOS"):
        for folder in LEGACY_APP_DIRS:
            for name in LEGACY_APP_NAMES:
                c = folder / name
                if not c.exists() or is_protected_app(c):
                    continue
                result["macos_apps"].append(c)
                sz = _calc_dir_size(c)
                result["estimated_bytes"] += sz
                result["items_summary"].append(f"{c.name} ({format_bytes(sz)})")

        found_support_dirs = []
        check_dirs = [LEGACY_SUPPORT_DIR]
        for candidate in LEGACY_SUPPORT_DIRS:
            if candidate not in check_dirs:
                check_dirs.append(candidate)

        for supp_dir in check_dirs:
            if not supp_dir.is_dir():
                continue
            # A user can delete their .app without clearing the application support folder.
            # The presence of firmware_downloader.py or legacy app markers ensures those users
            # are covered by the remove app feature too.
            if has_legacy_firmware_downloader(supp_dir) or legacy_folder_is_app_owned(supp_dir):
                if supp_dir not in found_support_dirs:
                    found_support_dirs.append(supp_dir)
                    sz = _calc_dir_size(supp_dir)
                    result["estimated_bytes"] += sz
                    result["items_summary"].append(f"Application Support/{supp_dir.name} ({format_bytes(sz)})")

        if found_support_dirs:
            result["macos_app_support"] = found_support_dirs[0]
            result["macos_app_supports"] = found_support_dirs

        # Check Homebrew android-platform-tools
        brew_cmd = shutil.which("brew")
        if brew_cmd:
            try:
                proc = subprocess.run(
                    [brew_cmd, "list", "--formula"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0 and "android-platform-tools" in proc.stdout.splitlines():
                    result["has_platform_tools"] = True
                    result["platform_tools_type"] = "brew"
            except Exception:
                pass

    # 2. Linux Detection
    if sys.platform.startswith("linux") or not (sys.platform == "darwin" or os.environ.get("INNIOASIS_SIMULATE_MACOS")):
        linux_install = LEGACY_LINUX_INSTALL_DIR
        if looks_like_pre_3_0_linux_install(linux_install):
            result["linux_install_dir"] = linux_install
            sz = _calc_dir_size(linux_install)
            result["estimated_bytes"] += sz
            result["items_summary"].append(f"~/.local/share/innioasis-updater ({format_bytes(sz)})")

        desktop_file = LEGACY_LINUX_DESKTOP_FILE
        if desktop_file.is_file():
            result["linux_desktop_file"] = desktop_file
            result["items_summary"].append("innioasis-updater.desktop")

        desktop_shortcut = Path.home() / "Desktop" / "innioasis-updater.desktop"
        if desktop_shortcut.is_file():
            result["linux_desktop_shortcuts"].append(desktop_shortcut)
            result["items_summary"].append("~/Desktop/innioasis-updater.desktop")

        bin_launcher = LEGACY_LINUX_BIN_LAUNCHER
        if bin_launcher.is_file():
            result["linux_bin_launcher"] = bin_launcher
            result["items_summary"].append("~/.local/bin/innioasis-updater")

        linux_cache = LEGACY_LINUX_CACHE_DIR
        if linux_cache.is_dir():
            result["linux_cache_dir"] = linux_cache
            sz = _calc_dir_size(linux_cache)
            result["estimated_bytes"] += sz
            result["items_summary"].append(f"~/.cache/innioasis-updater ({format_bytes(sz)})")

        adb_bin = Path.home() / ".local" / "bin" / "adb"
        fastboot_bin = Path.home() / ".local" / "bin" / "fastboot"
        pt_paths = []
        if adb_bin.exists():
            pt_paths.append(adb_bin)
        if fastboot_bin.exists():
            pt_paths.append(fastboot_bin)
        if pt_paths:
            result["has_platform_tools"] = True
            result["platform_tools_type"] = "bin"
            result["platform_tools_paths"] = pt_paths

    # 3. Windows Detection
    if sys.platform == "win32" or os.environ.get("INNIOASIS_SIMULATE_WINDOWS"):
        win_candidates = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            win_candidates.extend([
                Path(local_app_data) / "Innioasis Updater",
                Path(local_app_data) / "InnioasisUpdater",
            ])
        app_data = os.environ.get("APPDATA")
        if app_data:
            win_candidates.extend([
                Path(app_data) / "Innioasis Updater",
                Path(app_data) / "InnioasisUpdater",
            ])
        for folder in win_candidates:
            if folder.is_dir() and folder not in result["windows_legacy_dirs"]:
                sz = _calc_dir_size(folder)
                result["windows_legacy_dirs"].append(folder)
                result["estimated_bytes"] += sz
                result["items_summary"].append(f"{folder.name} ({format_bytes(sz)})")

    result["has_legacy"] = bool(
        result["macos_apps"]
        or result["macos_app_support"]
        or result["linux_install_dir"]
        or result["linux_desktop_file"]
        or result["linux_bin_launcher"]
        or result["linux_desktop_shortcuts"]
        or result["windows_legacy_dirs"]
    )
    return result


def remove_macos_legacy(
    remove_apps: bool = True,
    remove_app_support: bool = True,
    uninstall_platform_tools: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """Execute removal of legacy macOS components.

    Returns:
        (success, list_of_removed_items, list_of_errors)
    """
    removed = []
    errors = []
    info = detect_legacy_installations()

    if remove_apps:
        for app_path in info.get("macos_apps", []):
            if is_protected_app(app_path) or not is_legacy_app(app_path):
                errors.append(f"Safety guard: refusing to remove protected app {app_path}")
                continue

            # If user has write permissions, remove directly
            can_direct_delete = os.access(app_path.parent, os.W_OK) and os.access(app_path, os.W_OK)
            if can_direct_delete:
                try:
                    shutil.rmtree(app_path)
                    removed.append(str(app_path))
                    continue
                except Exception as e:
                    pass

            # Otherwise, use osascript to prompt for system password
            prompt = "Updater CE requires your administrator password to remove the legacy Innioasis Updater app."
            script = f'do shell script "rm -rf {shlex.quote(str(app_path))}" with prompt {shlex.quote(prompt)} with administrator privileges'
            try:
                proc = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if proc.returncode == 0:
                    removed.append(str(app_path))
                else:
                    err = proc.stderr.strip()
                    if "User canceled" in err:
                        errors.append(f"User canceled password prompt for {app_path.name}")
                    else:
                        errors.append(f"Failed to remove {app_path.name}: {err}")
            except Exception as e:
                errors.append(f"Failed to execute authorization prompt: {e}")

    if remove_app_support:
        supp_paths = info.get("macos_app_supports") or []
        if not supp_paths and info.get("macos_app_support"):
            supp_paths = [info["macos_app_support"]]
        for supp_path in supp_paths:
            if supp_path and (has_legacy_firmware_downloader(supp_path) or legacy_folder_is_app_owned(supp_path)):
                try:
                    shutil.rmtree(supp_path)
                    removed.append(str(supp_path))
                except Exception as e:
                    errors.append(f"Failed to remove {supp_path}: {e}")

    if uninstall_platform_tools and info.get("has_platform_tools") and info.get("platform_tools_type") == "brew":
        brew_cmd = shutil.which("brew")
        if brew_cmd:
            try:
                proc = subprocess.run(
                    [brew_cmd, "uninstall", "android-platform-tools"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if proc.returncode == 0:
                    removed.append("Homebrew package android-platform-tools")
                else:
                    errors.append(f"Failed to uninstall android-platform-tools: {proc.stderr.strip()}")
            except Exception as e:
                errors.append(f"Error calling brew uninstall: {e}")

    success = len(errors) == 0
    return success, removed, errors


def remove_linux_legacy(
    remove_install_dir: bool = True,
    uninstall_platform_tools: bool = False,
) -> Tuple[bool, List[str], List[str]]:
    """Execute removal of legacy Linux components.

    Returns:
        (success, list_of_removed_items, list_of_errors)
    """
    removed = []
    errors = []
    info = detect_legacy_installations()

    if remove_install_dir:
        inst_dir = info.get("linux_install_dir")
        if inst_dir and inst_dir.is_dir():
            try:
                shutil.rmtree(inst_dir)
                removed.append(str(inst_dir))
            except Exception as e:
                errors.append(f"Failed to remove {inst_dir}: {e}")

        df = info.get("linux_desktop_file")
        if df and df.is_file():
            try:
                df.unlink()
                removed.append(str(df))
            except Exception as e:
                errors.append(f"Failed to remove {df}: {e}")

        bl = info.get("linux_bin_launcher")
        if bl and bl.is_file():
            try:
                bl.unlink()
                removed.append(str(bl))
            except Exception as e:
                errors.append(f"Failed to remove {bl}: {e}")

        cd = info.get("linux_cache_dir")
        if cd and cd.is_dir():
            try:
                shutil.rmtree(cd)
                removed.append(str(cd))
            except Exception as e:
                errors.append(f"Failed to remove {cd}: {e}")

        for ds in info.get("linux_desktop_shortcuts", []):
            if ds.is_file():
                try:
                    ds.unlink()
                    removed.append(str(ds))
                except Exception as e:
                    errors.append(f"Failed to remove {ds}: {e}")

    if uninstall_platform_tools:
        for pt in info.get("platform_tools_paths", []):
            try:
                if pt.is_file() or pt.is_symlink():
                    pt.unlink()
                    removed.append(str(pt))
            except Exception as e:
                errors.append(f"Failed to remove {pt}: {e}")

    success = len(errors) == 0
    return success, removed, errors


def _is_windows_legacy_shortcut_name(name: str) -> bool:
    """True when a shortcut or shortcut folder name belongs to legacy Innioasis / SP Flash installs."""
    low = (name or "").lower()
    return "innioasis" in low or "sp flash" in low


def _clean_windows_legacy_shortcuts() -> List[str]:
    """Safely remove legacy .lnk/.url shortcuts and shortcut-only folders from Start Menu and Desktop.

    Never touches user files or paths outside Desktop and Start Menu Programs.
    """
    removed: List[str] = []
    roots: List[Path] = []
    userprofile = os.environ.get("USERPROFILE")
    appdata = os.environ.get("APPDATA")
    programdata = os.environ.get("PROGRAMDATA")
    public = os.environ.get("PUBLIC")
    if userprofile:
        roots.append(Path(userprofile) / "Desktop")
    if appdata:
        roots.append(Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if programdata:
        roots.append(Path(programdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    if public:
        roots.append(Path(public) / "Desktop")

    for root in roots:
        if not root.is_dir():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_file():
                    if entry.suffix.lower() in (".lnk", ".url") and _is_windows_legacy_shortcut_name(entry.stem):
                        entry.unlink()
                        removed.append(str(entry))
                elif entry.is_dir() and _is_windows_legacy_shortcut_name(entry.name):
                    # Only remove .lnk and .url shortcut files inside the folder, then
                    # remove the folder only if it is empty so user files are never deleted.
                    for sub in list(entry.rglob("*")):
                        if sub.is_file() and not sub.is_symlink() and sub.suffix.lower() in (".lnk", ".url"):
                            try:
                                sub.unlink()
                                removed.append(str(sub))
                            except OSError:
                                pass
                    for sub_dir in sorted(
                        (d for d in entry.rglob("*") if d.is_dir() and not d.is_symlink()),
                        key=lambda p: len(p.parts),
                        reverse=True,
                    ):
                        try:
                            sub_dir.rmdir()
                        except OSError:
                            pass
                    try:
                        entry.rmdir()
                        removed.append(str(entry))
                    except OSError:
                        pass
            except OSError:
                continue
    return removed


def remove_windows_legacy() -> Tuple[bool, List[str], List[str]]:
    """Execute removal of legacy Windows components.

    Runs Uninstall.exe / uninstall.exe if found, clears leftover files from
    %LocalAppData%\\Innioasis Updater, and removes legacy shortcuts from Start Menu / Desktop.
    """
    removed = []
    errors = []
    info = detect_legacy_installations()
    dirs = info.get("windows_legacy_dirs", [])
    for folder in dirs:
        if not folder.is_dir():
            continue
        uninstaller = None
        for candidate in ("uninstall.exe", "Uninstall.exe", "unins000.exe"):
            unins_path = folder / candidate
            if unins_path.is_file():
                uninstaller = unins_path
                break
        if uninstaller is not None:
            try:
                proc = subprocess.run(
                    [str(uninstaller), "/S", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                removed.append(f"{uninstaller.name} executed")
            except Exception as e:
                errors.append(f"Uninstaller failed: {e}")
        # Clear up any leftover files in the folder once uninstaller completes
        if folder.is_dir():
            try:
                shutil.rmtree(folder)
                removed.append(str(folder))
            except Exception as e:
                errors.append(f"Failed to remove {folder}: {e}")
    try:
        removed.extend(_clean_windows_legacy_shortcuts())
    except Exception as e:
        errors.append(f"Failed to clean legacy shortcuts: {e}")
    success = len(errors) == 0
    return success, removed, errors
