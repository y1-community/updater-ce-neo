"""Legacy pre-3.0 Innioasis Updater detection and cleanup service.

Pre-3.0 versions of Innioasis Updater were installed via shell scripts (run_mac.sh
and run_linux.sh), which created full Git clones, Python virtual environments, and
various platform-specific artifacts. These installations consumed significant disk
space (typically 500 MB - 1 GB+).

Updater CE 3.0 is a complete rewrite and standalone replacement packaged as compact,
optimized native binaries.

SAFETY INVARIANTS:
1. NEVER detect, touch, or remove 'InnioasisUpdater.app' (no space). That is the
   standalone Chinese offline tool and must be completely preserved.
2. Only detect 'Innioasis Updater.app' (with space) created by pre-3.0 run_mac.sh.
3. Updater CE stores its own data in 'Updater CE' application support / cache.
"""

import os
import sys
import shutil
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROTECTED_APP_NAMES = {
    "innioasisupdater.app",
    "updater ce.app",
    "updaterce.app",
    "mediatek installer.app",
}


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


def is_protected_app(path: Path) -> bool:
    """Return True if path represents a protected application that must never be removed."""
    name_lower = path.name.lower().strip()
    return name_lower in PROTECTED_APP_NAMES


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
        "has_platform_tools": False,
        "platform_tools_type": None,
        "platform_tools_paths": [],
        "estimated_bytes": 0,
        "items_summary": [],
    }

    # 1. macOS Detection
    if sys.platform == "darwin" or os.environ.get("INNIOASIS_SIMULATE_MACOS"):
        candidates = [
            Path("/Applications/Innioasis Updater.app"),
            Path.home() / "Applications" / "Innioasis Updater.app",
        ]
        for c in candidates:
            if c.exists() and not is_protected_app(c):
                # Ensure it specifically is 'Innioasis Updater.app'
                if c.name == "Innioasis Updater.app":
                    result["macos_apps"].append(c)
                    sz = _calc_dir_size(c)
                    result["estimated_bytes"] += sz
                    result["items_summary"].append(f"{c.name} ({format_bytes(sz)})")

        legacy_support = Path.home() / "Library" / "Application Support" / "Innioasis Updater"
        if legacy_support.is_dir():
            result["macos_app_support"] = legacy_support
            sz = _calc_dir_size(legacy_support)
            result["estimated_bytes"] += sz
            result["items_summary"].append(f"Application Support/Innioasis Updater ({format_bytes(sz)})")

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
        linux_install = Path.home() / ".local" / "share" / "innioasis-updater"
        if linux_install.is_dir():
            result["linux_install_dir"] = linux_install
            sz = _calc_dir_size(linux_install)
            result["estimated_bytes"] += sz
            result["items_summary"].append(f"~/.local/share/innioasis-updater ({format_bytes(sz)})")

        desktop_file = Path.home() / ".local" / "share" / "applications" / "innioasis-updater.desktop"
        if desktop_file.is_file():
            result["linux_desktop_file"] = desktop_file
            result["items_summary"].append("innioasis-updater.desktop")

        bin_launcher = Path.home() / ".local" / "bin" / "innioasis-updater"
        if bin_launcher.is_file():
            result["linux_bin_launcher"] = bin_launcher
            result["items_summary"].append("~/.local/bin/innioasis-updater")

        linux_cache = Path.home() / ".cache" / "innioasis-updater"
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

    result["has_legacy"] = bool(
        result["macos_apps"]
        or result["macos_app_support"]
        or result["linux_install_dir"]
        or result["linux_desktop_file"]
        or result["linux_bin_launcher"]
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
            if is_protected_app(app_path) or app_path.name != "Innioasis Updater.app":
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
        supp_path = info.get("macos_app_support")
        if supp_path and supp_path.is_dir():
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
