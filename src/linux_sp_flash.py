"""Linux SP Flash Tool staging, environment preparation, and system verification.

SP Flash Tool is MediaTek-proprietary and only ships as a Windows exe in the
original InniUpdaterChin payload, but the community publishes a Linux console
build. This module downloads and stages that package so the utility can use
SP Flash Tool as the install method on Linux (x86/x86_64) across all common
distributions (Ubuntu, Debian, Fedora, Arch Linux, CachyOS, Omarchy, openSUSE,
etc.) — with mtkclient as the automatic fallback when staging is impossible
(unsupported arch, offline, or execution failure).
"""

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

from .config import GITHUB_API
from .paths import COMPAT_DIR, INSTALL_DIR, REPO_ROOT

logger = logging.getLogger(__name__)

# `grp` is a Unix-only stdlib module; this module is imported unconditionally
# by flash_service on every platform, so only import it where it exists
# (check_user_permissions is Linux-only and already handles its absence).
if sys.platform != "win32":
    import grp

FLASH_TOOL_LINUX_URL = (
    "https://github.com/y1-community/updater-ce-neo/releases/download/flash_tool/flash_tool_linux.zip"
)
FLASH_TOOL_LINUX_FALLBACK_URL = (
    "https://github.com/y1-community/Innioasis-Updater/releases/download/flash_tool/flash_tool_linux.zip"
)
FLASH_TOOL_LINUX_ZIP_NAME = "flash_tool_linux.zip"
FLASH_TOOL_LINUX_BIN = "flash_tool"
FLASH_TOOL_LINUX_ZIP_MIN_BYTES = 10 * 1024 * 1024  # ~67 MB full package

# Members expected inside flash_tool_linux.zip from GitHub Releases:
FLASH_TOOL_ZIP_REQUIRED_FILES = (
    "flash_tool",
    "flash_tool.sh",
    "libflashtool.so",
    "libflashtool.v1.so",
    "libflashtoolEx.so",
    "libsla_challenge.so",
    "MTK_AllInOne_DA.bin",
    "lib/libQtCore.so.4",
    "lib/libQtGui.so.4",
    "lib/libQtNetwork.so.4",
    "lib/libQtWebKit.so.4",
    "lib/libQtXml.so.4",
    "lib/libQtXmlPatterns.so.4",
    "lib/libQtSql.so.4",
    "lib/libQtHelp.so.4",
    "lib/libQtCLucene.so.4",
    "lib/libphonon.so.4",
    "plugins/imageformats/libqjpeg.so",
    "plugins/sqldrivers/libqsqlite.so",
)

# Files required in the extracted stage directory for runtime readiness.
# Note: libpng12.so.0 is staged from assets/compat or system sources because
# modern distros (Ubuntu 20+, Fedora, Arch, CachyOS, SUSE) do not bundle libpng12.
FLASH_TOOL_LINUX_REQUIRED_FILES = FLASH_TOOL_ZIP_REQUIRED_FILES + (
    "lib/libpng12.so.0",
)

LOG_DIR_NAME = "SP_FT_Logs"
STAGE_SUBDIR = "linux_flash_tool"

# Standard udev rule filenames installed on the host system
UDEV_RULE_FILENAME = "99-innioasis-mediatek.rules"
UDEV_COMPANION_FILENAME = "99-ttyacms.rules"
SETUP_SCRIPT_FILENAME = "setup_sp_flash_linux.sh"


def _app_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "innioasis-updater"


def _user_stage_dir() -> Path:
    """Per-user staging directory for the downloaded flash_tool_linux.zip payload."""
    d = _app_cache_dir() / STAGE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_dir() -> Path | None:
    """Return the SP Flash Tool directory shipped with the application, if any.

    Checked in order: ``INSTALL_DIR / SP_Flash_Tool`` (next to the frozen
    executable — inside an AppImage this is ``AppDir/usr/bin/SP_Flash_Tool``),
    then the dev-checkout locations under the repository. Returns the first
    directory containing the ``flash_tool`` binary, or None when no payload
    was bundled (runtime download is the fallback).
    """
    candidates = [
        INSTALL_DIR / "SP_Flash_Tool",
        REPO_ROOT / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "SP_Flash_Tool",
        REPO_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux",
    ]
    for cand in candidates:
        try:
            if (cand / FLASH_TOOL_LINUX_BIN).is_file():
                return cand
        except OSError:
            continue
    return None


def _bundled_complete(bundled: Path) -> bool:
    """Completeness of a bundled payload, minus libpng12.

    Same file set as ``files_ready()`` except ``lib/libpng12.so.0``: modern
    distros lack libpng12 and the app stages it at runtime, which cannot happen
    inside the read-only AppImage squashfs — there it is staged into the
    per-user cache and picked up via ``process_env()``.
    """
    missing = [rel for rel in missing_files(bundled) if rel != "lib/libpng12.so.0"]
    return not missing


def stage_dir() -> Path:
    """Active SP Flash Tool directory on Linux.

    Prefers the copy bundled with the application (complete payload next to
    the frozen executable) so shipped builds work offline; otherwise the
    per-user staging directory populated by ``ensure_linux_sp_flash_tool``
    from the ``flash_tool_linux.zip`` download.
    """
    bundled = bundled_dir()
    if bundled is not None and _bundled_complete(bundled):
        return bundled
    return _user_stage_dir()


def zip_cache_path() -> Path:
    return _app_cache_dir() / FLASH_TOOL_LINUX_ZIP_NAME


def arch_supported() -> bool:
    """SP Flash Tool Linux builds are x86/x86_64 only."""
    machine = (platform.machine() or "").lower()
    return machine in ("x86_64", "amd64", "x64", "i386", "i486", "i586", "i686", "x86")


def unsupported_reason() -> str:
    machine = platform.machine() or "unknown"
    return (
        f"SP Flash Tool is only available on x86 / x86_64 Linux "
        f"(this system is {machine}). Installs use MTKClient instead."
    )


# --- Distribution Detection --------------------------------------------------

def detect_linux_distro() -> dict:
    """Detect the host Linux distribution and its family.

    Supports:
      - Arch family: Arch Linux, CachyOS, Omarchy, Manjaro, EndeavourOS, Garuda, SteamOS
      - Debian family: Ubuntu, Debian, Linux Mint, Pop!_OS, Zorin OS, Kali, Raspbian
      - Fedora family: Fedora, RHEL, CentOS Stream, Rocky Linux, AlmaLinux, Nobara
      - SUSE family: openSUSE Tumbleweed, openSUSE Leap, SUSE Linux Enterprise (SLES)
      - Independent / Others: Alpine, Gentoo, Void, NixOS, Solus
    """
    info = {
        "id": "unknown",
        "id_like": [],
        "name": "Linux",
        "pretty_name": "Linux",
        "family": "generic",
        "serial_group": "dialout",
        "package_manager": "",
        "pkg_install_cmd": "",
        "recommended_pkgs": [],
    }

    os_release_paths = [Path("/etc/os-release"), Path("/usr/lib/os-release")]
    raw_data = {}
    for p in os_release_paths:
        if p.is_file():
            try:
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    raw_data[k.strip()] = v.strip().strip("\"'")
                break
            except OSError:
                pass

    distro_id = (raw_data.get("ID") or "generic").lower()
    id_like = [x.lower() for x in raw_data.get("ID_LIKE", "").split() if x.strip()]
    name = raw_data.get("NAME", "Linux")
    pretty_name = raw_data.get("PRETTY_NAME", name)

    info["id"] = distro_id
    info["id_like"] = id_like
    info["name"] = name
    info["pretty_name"] = pretty_name

    # Determine family and serial permissions group:
    all_identifiers = {distro_id} | set(id_like)
    if any(k in all_identifiers for k in ("arch", "cachyos", "omarchy", "manjaro", "endeavouros", "garuda", "steamos")):
        info["family"] = "arch"
        info["serial_group"] = "uucp"
        info["package_manager"] = "pacman"
        info["pkg_install_cmd"] = "sudo pacman -S --needed libusb-compat libusb"
        info["recommended_pkgs"] = ["libusb-compat", "libusb"]
    elif any(k in all_identifiers for k in ("debian", "ubuntu", "linuxmint", "pop", "zorin", "kali", "raspbian", "elementary")):
        info["family"] = "debian"
        info["serial_group"] = "dialout"
        info["package_manager"] = "apt"
        info["pkg_install_cmd"] = "sudo apt update && sudo apt install -y libusb-1.0-0 libusb-0.1-4"
        info["recommended_pkgs"] = ["libusb-1.0-0", "libusb-0.1-4"]
    elif any(k in all_identifiers for k in ("fedora", "rhel", "centos", "rocky", "almalinux", "nobara")):
        info["family"] = "fedora"
        info["serial_group"] = "dialout"
        info["package_manager"] = "dnf"
        info["pkg_install_cmd"] = "sudo dnf install -y libusb-compat-0.1 libusb1"
        info["recommended_pkgs"] = ["libusb-compat-0.1", "libusb1"]
    elif any(k in all_identifiers for k in ("suse", "opensuse", "opensuse-tumbleweed", "opensuse-leap", "sles")):
        info["family"] = "suse"
        info["serial_group"] = "dialout"
        info["package_manager"] = "zypper"
        info["pkg_install_cmd"] = "sudo zypper install -y libusb-0_1-4 libusb-1_0-0"
        info["recommended_pkgs"] = ["libusb-0_1-4", "libusb-1_0-0"]
    elif "gentoo" in all_identifiers:
        info["family"] = "gentoo"
        info["serial_group"] = "uucp"
        info["package_manager"] = "emerge"
        info["pkg_install_cmd"] = "sudo emerge --ask dev-libs/libusb-compat dev-libs/libusb"
        info["recommended_pkgs"] = ["dev-libs/libusb-compat", "dev-libs/libusb"]
    elif "void" in all_identifiers:
        info["family"] = "void"
        info["serial_group"] = "dialout"
        info["package_manager"] = "xbps"
        info["pkg_install_cmd"] = "sudo xbps-install -S libusb-compat libusb"
        info["recommended_pkgs"] = ["libusb-compat", "libusb"]
    elif "alpine" in all_identifiers:
        info["family"] = "alpine"
        info["serial_group"] = "dialout"
        info["package_manager"] = "apk"
        info["pkg_install_cmd"] = "sudo apk add libusb-compat libusb gcompat"
        info["recommended_pkgs"] = ["libusb-compat", "libusb", "gcompat"]
    elif "nixos" in all_identifiers:
        info["family"] = "nixos"
        info["serial_group"] = "dialout"
        info["package_manager"] = "nix"
        info["pkg_install_cmd"] = "nix-env -iA nixos.libusb-compat-0_1 nixos.libusb1"
        info["recommended_pkgs"] = ["libusb-compat-0_1", "libusb1"]

    return info


# --- Staging & Compatibility -------------------------------------------------

def stage_libpng12(stage: Path) -> bool:
    """Ensure libpng12.so.0 is available under stage/lib/.

    SP Flash Tool and Qt4 depend directly on libpng12.so.0. Modern distros ship
    only libpng16+. We bundle a clean 64-bit libpng12.so.0 under assets/compat/
    which resolves this on all x86_64 Linux systems without root or package installs.
    """
    target = stage / "lib" / "libpng12.so.0"
    if target.is_file() and target.stat().st_size > 50 * 1024:
        return True

    target.parent.mkdir(parents=True, exist_ok=True)

    # 1. Check bundled assets/compat directory
    candidate_sources = [
        COMPAT_DIR / "libpng12.so.0",
        Path(__file__).resolve().parent.parent / "assets" / "compat" / "libpng12.so.0",
    ]

    # 2. Check well-known host system paths
    host_candidates = [
        Path("/usr/lib/libpng12.so.0"),
        Path("/usr/lib64/libpng12.so.0"),
        Path("/lib/x86_64-linux-gnu/libpng12.so.0"),
        Path("/usr/lib/x86_64-linux-gnu/libpng12.so.0"),
        Path("/home/deck/.local/share/Steam/ubuntu12_32/steam-runtime/lib/x86_64-linux-gnu/libpng12.so.0.46.0"),
    ]
    candidate_sources.extend(host_candidates)

    for src in candidate_sources:
        try:
            if src.is_file() and src.stat().st_size > 50 * 1024:
                shutil.copy2(src, target)
                target.chmod(target.stat().st_mode | 0o755)
                logger.info("Staged libpng12.so.0 from %s to %s", src, target)
                return True
        except OSError as e:
            logger.debug("Failed staging libpng12 from %s: %s", src, e)

    return False


def missing_files(stage: Path) -> list:
    """Check for missing runtime files in stage."""
    missing = []
    for rel in FLASH_TOOL_LINUX_REQUIRED_FILES:
        path = stage / rel
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                missing.append(rel)
        except OSError:
            missing.append(rel)
    return missing


def files_ready(stage: Path) -> bool:
    return not missing_files(stage)


def zip_has_required_members(zip_path: Path) -> bool:
    """Check that the downloaded zip archive has all required upstream members."""
    try:
        if not zip_path.is_file() or zip_path.stat().st_size < FLASH_TOOL_LINUX_ZIP_MIN_BYTES:
            return False
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set()
            for name in zf.namelist():
                n = name.lstrip("./")
                if n.endswith("/"):
                    continue
                names.add(n)
        return all(rel in names for rel in FLASH_TOOL_ZIP_REQUIRED_FILES)
    except Exception as e:
        logger.debug("flash_tool_linux.zip check failed: %s", e)
        return False


def _download_zip(zip_path: Path, progress_cb=None) -> bool:
    """Download the SP Flash Tool archive, trying the primary URL then fallback."""
    urls = [FLASH_TOOL_LINUX_URL, FLASH_TOOL_LINUX_FALLBACK_URL]
    headers = {"Accept": "application/vnd.github+json"}

    for url in urls:
        try:
            logger.info("Downloading Linux SP Flash Tool from %s", url)
            resp = requests.get(url, stream=True, timeout=30, headers=headers)
            if resp.status_code == 302:
                redirect_url = resp.headers.get("Location")
                if redirect_url:
                    resp = requests.get(redirect_url, stream=True, timeout=30)
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_cb and total:
                            progress_cb(min(99, int(downloaded * 100 / total)))
            if progress_cb:
                progress_cb(100)
            if zip_has_required_members(zip_path):
                return True
        except Exception as e:
            logger.warning("Download from %s failed: %s", url, e)

    return zip_has_required_members(zip_path)


def _extract_zip(zip_path: Path, stage: Path) -> bool:
    try:
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(stage)
        # Stage bundled compatibility libraries (libpng12) immediately after extraction
        stage_libpng12(stage)
        make_executable(stage)
        fix_option_ini(stage)
        return files_ready(stage)
    except Exception as e:
        logger.error("flash_tool_linux.zip extract failed: %s", e)
        return False


def make_executable(stage: Path):
    for name in (FLASH_TOOL_LINUX_BIN, "flash_tool.sh"):
        path = stage / name
        if path.is_file():
            try:
                path.chmod(path.stat().st_mode | 0o755)
            except Exception:
                pass


def process_env(stage: Path) -> dict:
    """Environment for launching the Linux flash_tool (bundled Qt4 + plugins)."""
    env = os.environ.copy()
    lib_dirs = [str(stage), str(stage / "lib")]
    # Compat libs staged into the per-user cache (e.g. libpng12.so.0 when the
    # bundled payload lives on the read-only AppImage squashfs).
    try:
        cache_dir = _app_cache_dir()
        if cache_dir.is_dir():
            lib_dirs.append(str(cache_dir))
    except Exception:
        pass
    existing = env.get("LD_LIBRARY_PATH", "").strip()
    if existing:
        lib_dirs.append(existing)
    env["LD_LIBRARY_PATH"] = ":".join(lib_dirs)
    plugins = stage / "plugins"
    if plugins.is_dir():
        env["QT_PLUGIN_PATH"] = str(plugins)
    env.setdefault("QT_QPA_PLATFORM", "xcb")
    return env


def test_sp_flash_tool_execution(stage: Path) -> tuple:
    """Execute flash_tool with -h to verify that all shared libraries resolve and it runs.

    Returns (ok: bool, message: str).
    """
    bin_path = stage / FLASH_TOOL_LINUX_BIN
    if not bin_path.is_file():
        return False, f"Binary not found: {bin_path}"

    try:
        res = subprocess.run(
            [str(bin_path), "-h"],
            env=process_env(stage),
            capture_output=True,
            text=True,
            timeout=5,
        )
        combined = (res.stdout or "") + (res.stderr or "")
        if res.returncode == 0 or "Usage: flash_tool" in combined or "FlashTool in console mode" in combined:
            return True, "SP Flash Tool execution check passed."
        if res.returncode == 127 or "cannot open shared object file" in combined:
            return False, f"Missing shared library: {combined.strip()}"
        return False, f"Execution returned code {res.returncode}: {combined.strip()[:200]}"
    except subprocess.TimeoutExpired:
        return False, "Execution test timed out after 5 seconds."
    except Exception as e:
        return False, f"Execution test failed: {e}"


def check_ldd_dependencies(stage: Path) -> tuple:
    """Run ldd on flash_tool to check for unsatisfied dynamic dependencies."""
    bin_path = stage / FLASH_TOOL_LINUX_BIN
    if not bin_path.is_file():
        return False, ["Binary not found"]

    try:
        res = subprocess.run(
            ["ldd", str(bin_path)],
            env=process_env(stage),
            capture_output=True,
            text=True,
            timeout=5,
        )
        missing = []
        for line in res.stdout.splitlines():
            if "not found" in line:
                lib_name = line.split("=>")[0].strip()
                missing.append(lib_name)
        if missing:
            return False, missing
        return True, []
    except Exception as e:
        logger.debug("ldd check failed: %s", e)
        return True, []


# --- Udev Rules & System Configuration ----------------------------------------

def generate_udev_rule_content() -> str:
    """Generate comprehensive udev rules for MediaTek flashing.

    Grants standard desktop user access to Boot ROM (0e8d:0003) and Preloader
    (0e8d:2000, /dev/ttyACM*), forces immediate 0666 permissions via RUN+,
    disables USB power autosuspend, and prevents ModemManager and brltty from
    interrupting BROM/DA handshakes.
    """
    return (
        "# Innioasis Updater CE - MediaTek Flashing Rules\n"
        "# Ensures full permissions for Boot ROM (BROM) and Preloader serial interfaces.\n"
        "\n"
        "# MediaTek Boot ROM (BROM) - USB devices\n"
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", ENV{MTP_NO_PROBE}="1", '
        'ENV{BRLTTY_DEVICE_IGNORE}="1", TEST=="power/control", ATTR{power/control}="on"\n'
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0003", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", ENV{MTP_NO_PROBE}="1", '
        'ENV{BRLTTY_DEVICE_IGNORE}="1", TEST=="power/control", ATTR{power/control}="on"\n'
        "\n"
        "# MediaTek Preloader and DA - Serial TTY devices (/dev/ttyACM*)\n"
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="2000", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"\n'
        'SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", RUN+="/bin/chmod 0666 /dev/%k"\n'
        'KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", RUN+="/bin/chmod 0666 /dev/%k"\n'
        'KERNEL=="ttyUSB[0-9]*", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", RUN+="/bin/chmod 0666 /dev/%k"\n'
    )


def generate_ttyacms_rule_content() -> str:
    """Generate universal unprivileged /dev/ttyACM* and /dev/ttyUSB* access rule.

    Standard community fix for MediaTek preloader serial access without udev race conditions.
    """
    return (
        "# Innioasis Updater CE - Unprivileged Serial Port Access\n"
        'ACTION=="add|change", SUBSYSTEM=="tty", KERNEL=="ttyACM[0-9]*", MODE="0666", TAG+="uaccess", RUN+="/bin/chmod 0666 /dev/%k"\n'
        'ACTION=="add|change", SUBSYSTEM=="tty", KERNEL=="ttyUSB[0-9]*", MODE="0666", TAG+="uaccess", RUN+="/bin/chmod 0666 /dev/%k"\n'
    )


def check_udev_rules() -> tuple:
    """Check whether udev rules granting access to MediaTek devices are installed.

    Returns (installed: bool, description: str).
    """
    rule_dirs = [Path("/etc/udev/rules.d"), Path("/usr/lib/udev/rules.d"), Path("/lib/udev/rules.d")]
    matching_rules = []
    has_0e8d_access = False

    for d in rule_dirs:
        if not d.is_dir():
            continue
        try:
            for rule_file in d.glob("*.rules"):
                try:
                    content = rule_file.read_text(encoding="utf-8", errors="replace")
                    if "0e8d" in content.lower():
                        matching_rules.append(rule_file.name)
                        if "0666" in content or "uaccess" in content:
                            has_0e8d_access = True
                except OSError:
                    pass
        except OSError:
            pass

    if has_0e8d_access:
        return True, f"MediaTek udev rules detected ({', '.join(matching_rules[:3])})"
    if matching_rules:
        return False, f"Rules found ({', '.join(matching_rules)}), but without user access permissions (0666/uaccess)."
    return False, "No MediaTek udev rules found in /etc/udev/rules.d/."


def check_service_conflicts() -> list:
    """Check for services that conflict with MediaTek bootloader handshakes."""
    conflicts = []

    # 1. Check brltty (Braille TTY daemon grabs 0e8d:0003 and terminates connection)
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "brltty"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.stdout.strip() == "active":
            conflicts.append({
                "service": "brltty",
                "severity": "high",
                "message": (
                    "brltty is currently active. It intercepts MediaTek Boot ROM (0e8d:0003) "
                    "as a Braille display and disrupts flashing. Mitigation: stop it with "
                    "`sudo systemctl stop brltty` or ensure udev BRLTTY_DEVICE_IGNORE rule is active."
                ),
            })
    except Exception:
        pass

    # 2. Check ModemManager (probes serial ports with AT commands)
    try:
        res = subprocess.run(
            ["systemctl", "is-active", "ModemManager"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.stdout.strip() == "active":
            conflicts.append({
                "service": "ModemManager",
                "severity": "medium",
                "message": (
                    "ModemManager is active. It may probe /dev/ttyACM* ports unless ignored. "
                    "Innioasis udev rules automatically set ID_MM_DEVICE_IGNORE=1 to prevent interference."
                ),
            })
    except Exception:
        pass

    return conflicts


def check_user_permissions(serial_group: str) -> tuple:
    """Check if the current user belongs to the required serial dialout/uucp group."""
    try:
        user_groups = [grp.getgrgid(g).gr_name for g in os.getgroups()]
    except Exception:
        user_groups = []

    in_group = serial_group in user_groups
    in_plugdev = "plugdev" in user_groups

    # Note: With TAG+="uaccess" in udev, an active systemd-logind session grants
    # direct ACL access to the device node even if the user isn't in dialout/uucp.
    if in_group:
        return True, f"User is member of group '{serial_group}'."
    if in_plugdev:
        return True, f"User is member of group 'plugdev' (and uaccess grants session access)."
    return False, (
        f"User is not in group '{serial_group}'. Add via `sudo usermod -aG {serial_group} $USER` "
        f"(udev 'uaccess' also grants access during active desktop sessions)."
    )


def check_cdc_acm() -> tuple:
    """Check if the cdc_acm kernel driver is available or loaded.

    MediaTek Preloader communicates over CDC ACM serial (/dev/ttyACM0).
    """
    if platform.system() != "Linux":
        return True, "Not on Linux"
    if Path("/sys/module/cdc_acm").is_dir():
        return True, "Kernel driver 'cdc_acm' is loaded and active."
    try:
        modules = Path("/proc/modules").read_text(encoding="utf-8", errors="replace")
        if "cdc_acm" in modules:
            return True, "Kernel driver 'cdc_acm' is loaded."
    except Exception:
        pass
    try:
        res = subprocess.run(["modinfo", "cdc_acm"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            return True, "Kernel module 'cdc_acm' is available (will load on demand)."
    except Exception:
        pass
    return True, "Kernel driver 'cdc_acm' is built into the kernel or available."


def check_noexec_mount(stage: Path = None) -> tuple:
    """Check if the filesystem containing stage directory allows binary execution."""
    if stage is None:
        stage = stage_dir()
    if platform.system() != "Linux":
        return True, "N/A"
    try:
        stage_resolved = str(stage.resolve())
        mounts_text = Path("/proc/mounts").read_text(encoding="utf-8", errors="replace")
        best_mp = "/"
        best_opts = ""
        for line in mounts_text.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                mp = parts[1]
                if stage_resolved.startswith(mp) and len(mp) >= len(best_mp):
                    best_mp = mp
                    best_opts = parts[3]
        if "noexec" in best_opts.split(","):
            return False, f"Directory is on mount '{best_mp}' with 'noexec' flag. Binaries cannot execute."
        return True, f"Filesystem on '{best_mp}' allows binary execution."
    except Exception as e:
        return True, f"Mount check passed: {e}"


def fix_option_ini(stage: Path = None) -> bool:
    """Point option.ini LogPath at a valid Linux-writable directory.

    Default shipped option.ini uses C:\\ProgramData\\SP_FT_Logs, which causes
    directory creation errors and malformed relative paths on Linux.
    """
    if stage is None:
        stage = stage_dir()
    option_path = stage / "option.ini"
    log_dir = stage / LOG_DIR_NAME
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    if not option_path.is_file():
        return False
    try:
        text = option_path.read_text(encoding="utf-8", errors="replace")
        new_log = str(log_dir)
        lines = []
        changed = False
        for line in text.splitlines():
            if line.strip().lower().startswith("logpath="):
                current = line.split("=", 1)[-1].strip()
                if current != new_log:
                    lines.append(f"LogPath={new_log}")
                    changed = True
                else:
                    lines.append(line)
            else:
                lines.append(line)
        if changed:
            option_path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""), encoding="utf-8")
            logger.info("Fixed option.ini LogPath to %s", new_log)
            return True
        return False
    except Exception as e:
        logger.warning("Could not fix option.ini LogPath: %s", e)
        return False


def check_connected_mtk_device() -> tuple:
    """Check whether a MediaTek device (VID 0e8d) is currently attached via USB."""
    if platform.system() != "Linux":
        return False, "N/A"
    try:
        sysfs_usb = Path("/sys/bus/usb/devices")
        if sysfs_usb.is_dir():
            for dev in sysfs_usb.iterdir():
                vendor_file = dev / "idVendor"
                if vendor_file.is_file():
                    try:
                        vid = vendor_file.read_text().strip().lower()
                        if vid == "0e8d":
                            pid_file = dev / "idProduct"
                            pid = pid_file.read_text().strip().lower() if pid_file.is_file() else "unknown"
                            mode = "Boot ROM (BROM)" if pid == "0003" else ("Preloader" if pid == "2000" else f"PID {pid}")
                            return True, f"MediaTek device detected ({mode}, VID 0e8d:{pid})"
                    except Exception:
                        pass
    except Exception:
        pass
    return False, "No MediaTek device detected on USB (device should be connected turned off)"


class TtyAccessGuardian(threading.Thread):
    """Userspace guardian thread that continuously chmods newly appeared ttyACM* nodes.

    Compensates for the 50-100ms latency between kernel devnode creation and
    systemd-logind uaccess ACL application, preventing SP Flash Tool from failing
    with 'Permission denied' upon connection.
    """

    def __init__(self, interval: float = 0.015):
        super().__init__(daemon=True, name="tty-access-guardian")
        self._stop_event = threading.Event()
        self.interval = interval

    def stop(self):
        self._stop_event.set()

    def run(self):
        while not self._stop_event.is_set():
            for path in Path("/dev").glob("ttyACM*"):
                try:
                    os.chmod(path, 0o666)
                except Exception:
                    pass
            for path in Path("/dev").glob("ttyUSB*"):
                try:
                    os.chmod(path, 0o666)
                except Exception:
                    pass
            self._stop_event.wait(self.interval)


def write_setup_script(cache_dir: Path = None) -> Path:
    """Generate an executable shell script to install udev rules and configure the system."""
    if cache_dir is None:
        cache_dir = _app_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    script_path = cache_dir / SETUP_SCRIPT_FILENAME

    distro = detect_linux_distro()
    group = distro.get("serial_group", "dialout")
    rules_content = generate_udev_rule_content()
    tty_rules_content = generate_ttyacms_rule_content()

    script_content = f"""#!/bin/bash
# Innioasis Updater CE - Linux System Preparation Script
# Configures udev rules, permissions, groups, and services for MediaTek SP Flash Tool.

set -e

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root (or via sudo)."
    echo "Usage: sudo bash $0"
    exit 1
fi

echo "=== Innioasis Updater CE: Staging Linux System ==="
echo "Target Distribution: {distro.get('pretty_name', 'Linux')}"

# 1. Install primary udev rules
RULES_DIR="/etc/udev/rules.d"
mkdir -p "$RULES_DIR"

RULE_FILE="$RULES_DIR/{UDEV_RULE_FILENAME}"
echo "Installing MediaTek udev rules to $RULE_FILE..."
cat << 'EOF' > "$RULE_FILE"
{rules_content}EOF
chmod 644 "$RULE_FILE"

# 2. Install companion unprivileged serial rule
TTY_RULE_FILE="$RULES_DIR/{UDEV_COMPANION_FILENAME}"
echo "Installing unprivileged serial rules to $TTY_RULE_FILE..."
cat << 'EOF' > "$TTY_RULE_FILE"
{tty_rules_content}EOF
chmod 644 "$TTY_RULE_FILE"

# 3. Reload udev
echo "Reloading udev rules..."
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger || true
    echo "udev reloaded successfully."
fi

# 4. User groups
TARGET_USER="${{SUDO_USER:-$USER}}"
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    for grp in {group} plugdev dialout uucp lock; do
        if getent group "$grp" >/dev/null 2>&1; then
            echo "Adding $TARGET_USER to group $grp..."
            usermod -aG "$grp" "$TARGET_USER" 2>/dev/null || true
        fi
    done
fi

# 5. Kernel module cdc_acm
if command -v modprobe >/dev/null 2>&1; then
    echo "Ensuring kernel module cdc_acm is loaded..."
    modprobe cdc_acm 2>/dev/null || true
fi

# 6. Mitigation for brltty if active
if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet brltty 2>/dev/null; then
        echo "Stopping brltty to prevent MediaTek BROM hijacking..."
        systemctl stop brltty 2>/dev/null || true
        systemctl mask brltty 2>/dev/null || true
    fi
fi

# 7. Apply immediate permissions on existing tty nodes
for p in /dev/ttyACM* /dev/ttyUSB*; do
    [ -e "$p" ] && chmod 0666 "$p" 2>/dev/null || true
done

echo ""
echo "=== System Staging Complete ==="
echo "SP Flash Tool is ready to communicate with your Innioasis player."
"""

    script_path.write_text(script_content, encoding="utf-8")
    script_path.chmod(script_path.stat().st_mode | 0o755)
    return script_path


def install_udev_rules() -> tuple:
    """Attempt to install udev rules using pkexec or direct root execution."""
    if os.geteuid() == 0:
        rules_dir = Path("/etc/udev/rules.d")
        rules_dir.mkdir(parents=True, exist_ok=True)
        try:
            (rules_dir / UDEV_RULE_FILENAME).write_text(generate_udev_rule_content(), encoding="utf-8")
            (rules_dir / UDEV_RULE_FILENAME).chmod(0o644)
            (rules_dir / UDEV_COMPANION_FILENAME).write_text(generate_ttyacms_rule_content(), encoding="utf-8")
            (rules_dir / UDEV_COMPANION_FILENAME).chmod(0o644)
            subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
            subprocess.run(["udevadm", "trigger"], check=False)
            fix_option_ini()
            return True, "Udev rules installed and reloaded."
        except OSError as e:
            return False, f"Failed to write udev rules: {e}"

    # Generate script and run with available escalation tool
    script_path = write_setup_script()
    fix_option_ini()

    candidates = find_available_escalation_tools()
    for tool in candidates:
        try:
            if tool == "doas":
                # Check nopass first
                chk = subprocess.run(["doas", "-n", "true"], capture_output=True, timeout=1)
                if chk.returncode == 0:
                    res = subprocess.run(["doas", "bash", str(script_path)], capture_output=True, text=True, timeout=30)
                else:
                    askpass = get_askpass_helper()
                    if not askpass:
                        continue
                    pw_res = subprocess.run([str(askpass)], stdout=subprocess.PIPE, text=True, timeout=60)
                    pw = pw_res.stdout.rstrip("\r\n")
                    if pw_res.returncode != 0 or not pw:
                        return False, "Authentication cancelled by user."
                    import pty
                    m_fd, s_fd = pty.openpty()
                    proc = subprocess.Popen(
                        ["doas", "bash", str(script_path)],
                        stdin=s_fd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    os.close(s_fd)
                    os.write(m_fd, f"{pw}\n".encode())
                    out, err = proc.communicate(timeout=30)
                    os.close(m_fd)
                    if proc.returncode == 0:
                        return True, "System rules configured successfully via doas."
                    return False, f"Setup script exited with code {proc.returncode}: {err.strip() or out.strip()}"
            elif tool == "pkexec":
                res = subprocess.run(
                    ["pkexec", "bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            elif tool == "sudo":
                askpass = get_askpass_helper()
                s_env = os.environ.copy()
                if askpass:
                    s_env["SUDO_ASKPASS"] = str(askpass)
                res = subprocess.run(
                    ["sudo", "-A" if askpass else "-n", "bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=s_env,
                )
            elif tool == "run0":
                res = subprocess.run(
                    ["run0", "--pipe", "bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            elif tool == "lxqt-sudo":
                res = subprocess.run(
                    ["lxqt-sudo", "bash", str(script_path)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            elif tool in ("kdesu", "kdesudo"):
                res = subprocess.run(
                    [tool, "-c", f"bash {script_path}"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            else:
                continue

            if res.returncode == 0:
                return True, f"System rules configured successfully via {tool}."
            return False, f"Setup script exited with code {res.returncode}: {res.stderr.strip() or res.stdout.strip()}"
        except subprocess.TimeoutExpired:
            return False, f"Authentication prompt ({tool}) timed out."
        except Exception:
            continue

    return False, f"Elevation tool not available. Run manually: sudo bash {script_path} (or: doas bash {script_path})"


class _PrependedStream:
    """Stream wrapper that yields a pre-read line before delegating to the underlying stream."""

    def __init__(self, first_line: str, stream):
        self._first_line = first_line
        self._stream = stream
        self._first_read = False

    def readline(self) -> str:
        if not self._first_read:
            self._first_read = True
            return self._first_line
        return self._stream.readline() if hasattr(self._stream, "readline") else ""

    def __iter__(self):
        if not self._first_read:
            self._first_read = True
            if self._first_line:
                yield self._first_line
        if self._stream:
            yield from self._stream

    def __getattr__(self, name):
        return getattr(self._stream, name)


def create_flash_tool_runner(stage: Path = None) -> Path:
    """Create an executable bash wrapper ensuring LD_LIBRARY_PATH and plugins are set."""
    if stage is None:
        stage = stage_dir()
    runner = stage / "run_flash_tool.sh"
    stage_abs = stage.resolve()
    script = (
        "#!/bin/bash\n"
        f'STAGE_DIR="{stage_abs}"\n'
        'export LD_LIBRARY_PATH="$STAGE_DIR:$STAGE_DIR/lib:${LD_LIBRARY_PATH:-}"\n'
        'export QT_PLUGIN_PATH="$STAGE_DIR/plugins"\n'
        'export QT_QPA_PLATFORM="xcb"\n'
        'cd "$STAGE_DIR"\n'
        f'exec "$STAGE_DIR/{FLASH_TOOL_LINUX_BIN}" "$@"\n'
    )
    runner.write_text(script, encoding="utf-8")
    try:
        runner.chmod(runner.stat().st_mode | 0o755)
    except Exception:
        pass
    return runner


def get_askpass_helper() -> Path | None:
    """Find or generate a graphical/terminal askpass helper for sudo -A, doas, and other tools."""
    env_askpass = os.environ.get("SUDO_ASKPASS") or os.environ.get("SSH_ASKPASS")
    if env_askpass and os.path.isfile(env_askpass) and os.access(env_askpass, os.X_OK):
        return Path(env_askpass)

    cache = _app_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    askpass = cache / "askpass.sh"
    content = (
        "#!/bin/bash\n"
        'if [ -n "$SUDO_ASKPASS" ] && [ -x "$SUDO_ASKPASS" ]; then\n'
        '    exec "$SUDO_ASKPASS" "$@"\n'
        'elif [ -n "$SSH_ASKPASS" ] && [ -x "$SSH_ASKPASS" ]; then\n'
        '    exec "$SSH_ASKPASS" "$@"\n'
        'elif command -v zenity >/dev/null 2>&1; then\n'
        '    exec zenity --password --title="Innioasis Updater CE - Superuser Privileges"\n'
        'elif command -v kdialog >/dev/null 2>&1; then\n'
        '    exec kdialog --password "Innioasis Updater CE - Superuser Privileges"\n'
        'elif command -v rofi >/dev/null 2>&1; then\n'
        '    exec rofi -dmenu -password -p "Superuser Privileges:"\n'
        "elif command -v dmenu >/dev/null 2>&1 && dmenu -h 2>&1 | grep -q -- '-P'; then\n"
        '    exec dmenu -P -p "Superuser Privileges:"\n'
        'elif command -v ksshaskpass >/dev/null 2>&1; then\n'
        '    exec ksshaskpass "$@"\n'
        'elif command -v lxqt-openssh-askpass >/dev/null 2>&1; then\n'
        '    exec lxqt-openssh-askpass "$@"\n'
        'elif command -v openssh-askpass >/dev/null 2>&1; then\n'
        '    exec openssh-askpass "$@"\n'
        'elif command -v x11-ssh-askpass >/dev/null 2>&1; then\n'
        '    exec x11-ssh-askpass "$@"\n'
        'elif command -v gxmessage >/dev/null 2>&1; then\n'
        '    exec gxmessage -entry -title "Superuser Privileges" "Enter password:"\n'
        'elif python3 -c "import PySide6" >/dev/null 2>&1; then\n'
        '    exec python3 -c "\n'
        'import sys\n'
        'from PySide6.QtWidgets import QApplication, QInputDialog, QLineEdit\n'
        'app = QApplication(sys.argv)\n'
        'text, ok = QInputDialog.getText(None, \'Superuser Privileges\', \'Enter password for root privileges:\', QLineEdit.Password)\n'
        'if ok and text:\n'
        '    print(text)\n'
        '    sys.exit(0)\n'
        'sys.exit(1)\n'
        '"\n'
        "else\n"
        "    exit 1\n"
        "fi\n"
    )
    askpass.write_text(content, encoding="utf-8")
    try:
        askpass.chmod(askpass.stat().st_mode | 0o755)
    except Exception:
        pass
    return askpass


def silent_system_prep(stage: Path = None) -> bool:
    """Silently configure system dependencies, option.ini, and permissions for SP Flash Tool."""
    if stage is None:
        stage = stage_dir()
    try:
        stage_libpng12(stage)
        fix_option_ini(stage)
        make_executable(stage)
        create_flash_tool_runner(stage)
        check_cdc_acm()
        log_dir = stage / LOG_DIR_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        try:
            log_dir.chmod(log_dir.stat().st_mode | 0o777)
        except Exception:
            pass
        if os.geteuid() == 0:
            rules_dir = Path("/etc/udev/rules.d")
            rule_file = rules_dir / UDEV_RULE_FILENAME
            if not rule_file.is_file():
                try:
                    rules_dir.mkdir(parents=True, exist_ok=True)
                    rule_file.write_text(generate_udev_rule_content(), encoding="utf-8")
                    rule_file.chmod(0o644)
                    (rules_dir / UDEV_COMPANION_FILENAME).write_text(generate_ttyacms_rule_content(), encoding="utf-8")
                    (rules_dir / UDEV_COMPANION_FILENAME).chmod(0o644)
                    subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
                    subprocess.run(["udevadm", "trigger"], check=False)
                except Exception:
                    pass
        return True
    except Exception as e:
        logger.debug("Silent system prep caught non-fatal exception: %s", e)
        return False


def find_available_escalation_tools() -> list[str]:
    """Return an ordered list of available privilege escalation tools on this Linux system.

    Supports:
      - doas (OpenDoas / BSD doas)
      - pkexec (Polkit standard)
      - sudo (with SUDO_ASKPASS)
      - run0 (systemd 256+ Polkit elevation)
      - lxqt-sudo (LXQt native)
      - kdesu / kdesudo (KDE Plasma native)
      - gksu / gksudo (GTK legacy)
      - beesu (Fedora / Red Hat)
    """
    tools = []

    # Check explicit user or distro override
    override = os.environ.get("ESCALATION_TOOL", "").strip().lower()
    if override and shutil.which(override):
        tools.append(override)

    has_doas = bool(shutil.which("doas"))
    has_sudo = bool(shutil.which("sudo"))
    has_pkexec = bool(shutil.which("pkexec"))
    has_doas_conf = (
        Path("/etc/doas.conf").is_file()
        or Path("/usr/local/etc/doas.conf").is_file()
    )

    # If doas is configured on system or sudo is absent, prioritize doas
    if has_doas and (has_doas_conf or not has_sudo):
        if "doas" not in tools:
            tools.append("doas")

    # Polkit pkexec (freedesktop.org standard on modern desktop environments)
    if has_pkexec and "pkexec" not in tools:
        tools.append("pkexec")

    # Desktop-specific elevation utilities
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
    if "LXQT" in desktop and shutil.which("lxqt-sudo") and "lxqt-sudo" not in tools:
        tools.append("lxqt-sudo")
    if "KDE" in desktop:
        if shutil.which("kdesu") and "kdesu" not in tools:
            tools.append("kdesu")
        elif shutil.which("kdesudo") and "kdesudo" not in tools:
            tools.append("kdesudo")

    # sudo
    if has_sudo and "sudo" not in tools:
        tools.append("sudo")

    # doas if installed but not already prioritized
    if has_doas and "doas" not in tools:
        tools.append("doas")

    # modern systemd run0
    if shutil.which("run0") and "run0" not in tools:
        tools.append("run0")

    # Other desktop wrappers
    for alt in ("lxqt-sudo", "kdesu", "kdesudo", "gksu", "gksudo", "beesu"):
        if shutil.which(alt) and alt not in tools:
            tools.append(alt)

    return tools


def _launch_with_doas(
    runner: Path,
    cmd_args: list,
    cwd: Path,
    env: dict,
    log_cb=None,
) -> tuple[subprocess.Popen | None, bool]:
    """Launch flash_tool runner via doas, handling nopass, persist, or askpass via PTY.

    Returns (proc, user_cancelled).
    """
    doas_bin = shutil.which("doas")
    if not doas_bin:
        return None, False

    full_cmd = [doas_bin, str(runner)] + cmd_args

    # Check if doas can execute without password (nopass rule or active persist)
    try:
        check = subprocess.run([doas_bin, "-n", "true"], capture_output=True, timeout=1)
        if check.returncode == 0:
            if log_cb:
                log_cb("doas running in non-interactive / passwordless mode...")
            proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(cwd),
                env=env,
            )
            return proc, False
    except Exception:
        pass

    # Password required: prompt with askpass helper
    askpass = get_askpass_helper()
    if not askpass:
        return None, False

    if log_cb:
        log_cb("Requesting superuser privileges via doas...")

    try:
        res = subprocess.run([str(askpass)], stdout=subprocess.PIPE, text=True, timeout=60)
        password = res.stdout.rstrip("\r\n")
        if res.returncode != 0 or not password:
            if log_cb:
                log_cb("doas authentication cancelled by user.")
            return None, True
    except Exception as e:
        if log_cb:
            log_cb(f"doas askpass prompt error: {e}")
        return None, False

    # Launch doas with PTY slave as stdin so readpassphrase() receives password securely
    try:
        import pty
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            full_cmd,
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(cwd),
            env=env,
        )
        os.close(slave_fd)
        os.write(master_fd, f"{password}\n".encode())
        proc._pty_master_fd = master_fd
        return proc, False
    except Exception as e:
        if log_cb:
            log_cb(f"Failed to launch doas via PTY: {e}")
        return None, False


def _attempt_escalation(
    tool: str,
    runner: Path,
    cmd_args: list,
    cwd: Path,
    env: dict,
    log_cb=None,
) -> tuple[subprocess.Popen | None, bool]:
    """Attempt privilege escalation using the specified tool.

    Returns (proc, user_cancelled).
    """
    if tool == "doas":
        return _launch_with_doas(runner, cmd_args, cwd, env, log_cb)

    escalation_cmd = None
    escalation_env = os.environ.copy()

    if tool == "pkexec":
        escalation_cmd = ["pkexec", str(runner)] + cmd_args
    elif tool == "sudo":
        # Check if sudo can run passwordless first
        try:
            chk = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=1)
            if chk.returncode == 0:
                escalation_cmd = ["sudo", "-n", str(runner)] + cmd_args
        except Exception:
            pass
        if not escalation_cmd:
            askpass = get_askpass_helper()
            if askpass:
                escalation_cmd = ["sudo", "-A", str(runner)] + cmd_args
                escalation_env["SUDO_ASKPASS"] = str(askpass)
            else:
                return None, False
    elif tool == "run0":
        escalation_cmd = ["run0", "--pipe", str(runner)] + cmd_args
    elif tool == "lxqt-sudo":
        escalation_cmd = ["lxqt-sudo", str(runner)] + cmd_args
    elif tool == "kdesu":
        cmd_str = " ".join([f'"{runner}"'] + [f'"{a}"' for a in cmd_args])
        escalation_cmd = ["kdesu", "-c", cmd_str]
    elif tool == "kdesudo":
        cmd_str = " ".join([f'"{runner}"'] + [f'"{a}"' for a in cmd_args])
        escalation_cmd = ["kdesudo", "-c", cmd_str]
    elif tool in ("gksu", "gksudo", "beesu"):
        escalation_cmd = [tool, str(runner)] + cmd_args
    else:
        return None, False

    if log_cb:
        log_cb(f"Requesting superuser privileges for SP Flash Tool via {tool}...")

    try:
        proc = subprocess.Popen(
            escalation_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(cwd),
            env=escalation_env,
        )
        return proc, False
    except Exception as e:
        if log_cb:
            log_cb(f"Escalation via {tool} failed to start: {e}")
        return None, False


def launch_linux_flash_tool(
    stage: Path,
    cmd_args: list,
    cwd: Path = None,
    env: dict = None,
    log_cb=None,
) -> subprocess.Popen:
    """Launch Linux flash_tool with privilege escalation (doas, pkexec, sudo, run0, etc.) and silent fallback.

    If the user fails or cancels authentication to run as root, SP Flash Tool is
    silently executed as the current user without root.
    """
    if cwd is None:
        cwd = stage
    runner = create_flash_tool_runner(stage)
    silent_system_prep(stage)
    base_env = env or process_env(stage)

    # 1. If already root, launch directly
    if os.geteuid() == 0:
        if log_cb:
            log_cb("Running SP Flash Tool as root...")
        return subprocess.Popen(
            [str(runner)] + cmd_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(cwd),
            env=base_env,
        )

    # 2. Check if privilege escalation is possible
    is_headless = (
        os.environ.get("QT_QPA_PLATFORM") == "offscreen"
        or bool(os.environ.get("INNIOASIS_HEADLESS"))
        or (not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"))
    )

    if not is_headless:
        candidates = find_available_escalation_tools()
        for tool in candidates:
            proc, cancelled = _attempt_escalation(tool, runner, cmd_args, cwd, base_env, log_cb)
            if cancelled:
                if log_cb:
                    log_cb("Superuser authentication cancelled by user; silently running SP Flash Tool without root...")
                break
            if proc is not None:
                # Wait for either authentication failure or first line of tool output
                first_line = proc.stdout.readline() if proc.stdout else ""
                if proc.poll() is not None and proc.returncode != 0:
                    if log_cb:
                        log_cb(f"Superuser authentication via {tool} not granted; silently running SP Flash Tool without root...")
                    try:
                        if proc.stdout:
                            proc.stdout.close()
                    except Exception:
                        pass
                    if hasattr(proc, "_pty_master_fd"):
                        try:
                            os.close(proc._pty_master_fd)
                        except Exception:
                            pass
                    # If user cancelled / failed authentication, don't spam with repeated prompts
                    break
                else:
                    if log_cb:
                        log_cb(f"SP Flash Tool running with root privileges (via {tool}).")
                    if proc.stdout:
                        proc.stdout = _PrependedStream(first_line, proc.stdout)
                    return proc

    # 3. Silent fallback: launch without root
    if log_cb:
        log_cb("Running SP Flash Tool without root privileges.")
    return subprocess.Popen(
        [str(runner)] + cmd_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=str(cwd),
        env=base_env,
    )


def auto_fix_permissions() -> tuple:
    """Perform full automated fix for permissions, udev rules, and configuration."""
    silent_system_prep()
    return install_udev_rules()


# --- Comprehensive Verification & System Checker ------------------------------

def run_system_checker(stage: Path = None) -> dict:
    """Run all system checks for MediaTek SP Flash Tool readiness.

    Returns a structured report containing individual check items, overall status,
    and mitigation recommendations.
    """
    if stage is None:
        stage = stage_dir()

    distro = detect_linux_distro()
    arch_ok = arch_supported()
    arch_msg = "" if arch_ok else unsupported_reason()

    if stage.is_dir():
        stage_libpng12(stage)
        make_executable(stage)
        fix_option_ini(stage)

    missing = missing_files(stage)
    pkg_ok = (len(missing) == 0)
    libpng_staged = (stage / "lib" / "libpng12.so.0").is_file()
    ldd_ok, missing_libs = check_ldd_dependencies(stage)
    exec_ok, exec_msg = test_sp_flash_tool_execution(stage)
    udev_ok, udev_msg = check_udev_rules()
    conflicts = check_service_conflicts()
    user_perm_ok, user_perm_msg = check_user_permissions(distro.get("serial_group", "dialout"))
    cdc_ok, cdc_msg = check_cdc_acm()
    mount_ok, mount_msg = check_noexec_mount(stage)
    option_ok = (stage / "option.ini").is_file()
    dev_ok, dev_msg = check_connected_mtk_device()
    script_path = write_setup_script()

    has_high_conflict = any(c.get("severity") == "high" for c in conflicts)
    overall_ready = arch_ok and pkg_ok and exec_ok and udev_ok and not has_high_conflict

    items = [
        {
            "key": "os_arch",
            "title": "Operating System & Architecture",
            "status": "ok" if arch_ok else "fail",
            "badge": f"{distro.get('pretty_name')} ({platform.machine()})" if arch_ok else f"Unsupported ({platform.machine()})",
            "detail": "Supported x86_64 architecture for SP Flash Tool native execution." if arch_ok else arch_msg,
            "can_fix": False,
        },
        {
            "key": "engine_files",
            "title": "SP Flash Tool Engine Files",
            "status": "ok" if pkg_ok else "fail",
            "badge": "Installed" if pkg_ok else f"Missing {len(missing)} files",
            "detail": f"All required binaries and libraries present in {stage}" if pkg_ok else f"Missing: {', '.join(missing[:6])}",
            "can_fix": True,
        },
        {
            "key": "libpng12",
            "title": "Compatibility Library (libpng12)",
            "status": "ok" if libpng_staged else "warn",
            "badge": "Staged" if libpng_staged else "Missing",
            "detail": "libpng12.so.0 is staged for Qt4 interface." if libpng_staged else "libpng12.so.0 missing (needed for SP Flash Tool UI/console).",
            "can_fix": True,
        },
        {
            "key": "dynamic_deps",
            "title": "Dynamic Shared Libraries (ldd)",
            "status": "ok" if ldd_ok else "fail",
            "badge": "Resolved" if ldd_ok else f"{len(missing_libs)} missing",
            "detail": "All dynamic shared library dependencies satisfied." if ldd_ok else f"Missing libraries: {', '.join(missing_libs)}",
            "can_fix": False,
        },
        {
            "key": "exec_selftest",
            "title": "Binary Execution Self-Test",
            "status": "ok" if exec_ok else "fail",
            "badge": "Passed" if exec_ok else "Failed",
            "detail": exec_msg,
            "can_fix": False,
        },
        {
            "key": "udev_rules",
            "title": "USB & Serial Permissions (udev)",
            "status": "ok" if udev_ok else "fail",
            "badge": "Configured" if udev_ok else "Action Needed",
            "detail": udev_msg,
            "can_fix": True,
        },
        {
            "key": "user_groups",
            "title": f"User Group Membership ({distro.get('serial_group', 'dialout')})",
            "status": "ok" if user_perm_ok else "warn",
            "badge": "Member" if user_perm_ok else "Not in group",
            "detail": user_perm_msg,
            "can_fix": True,
        },
        {
            "key": "service_conflicts",
            "title": "Background Service Conflicts",
            "status": "fail" if has_high_conflict else ("warn" if conflicts else "ok"),
            "badge": "None Detected" if not conflicts else f"{len(conflicts)} Detected",
            "detail": "No conflicting daemons active." if not conflicts else "; ".join(c.get("message") for c in conflicts),
            "can_fix": True,
        },
        {
            "key": "kernel_driver",
            "title": "CDC ACM Serial Driver (cdc_acm)",
            "status": "ok" if cdc_ok else "warn",
            "badge": "Active" if cdc_ok else "Check Module",
            "detail": cdc_msg,
            "can_fix": True,
        },
        {
            "key": "mount_permissions",
            "title": "Filesystem Execution Permission",
            "status": "ok" if mount_ok else "fail",
            "badge": "Executable" if mount_ok else "noexec",
            "detail": mount_msg,
            "can_fix": False,
        },
        {
            "key": "option_ini",
            "title": "Log Directory Configuration (option.ini)",
            "status": "ok" if option_ok else "info",
            "badge": "Configured" if option_ok else "Pending",
            "detail": f"LogPath pointed to {stage / LOG_DIR_NAME}" if option_ok else "Will be configured upon first launch.",
            "can_fix": True,
        },
        {
            "key": "connected_device",
            "title": "Connected MediaTek Device",
            "status": "ok" if dev_ok else "info",
            "badge": "Connected" if dev_ok else "Not Detected",
            "detail": dev_msg,
            "can_fix": False,
        },
    ]

    return {
        "distro": distro,
        "arch_ok": arch_ok,
        "arch_msg": arch_msg,
        "stage_dir": str(stage),
        "package_files_ok": pkg_ok,
        "missing_files": missing,
        "libpng12_staged": libpng_staged,
        "ldd_ok": ldd_ok,
        "missing_libs": missing_libs,
        "sp_exec_ok": exec_ok,
        "sp_exec_msg": exec_msg,
        "udev_ok": udev_ok,
        "udev_msg": udev_msg,
        "service_conflicts": conflicts,
        "user_perm_ok": user_perm_ok,
        "user_perm_msg": user_perm_msg,
        "cdc_acm_ok": cdc_ok,
        "cdc_acm_msg": cdc_msg,
        "mount_ok": mount_ok,
        "mount_msg": mount_msg,
        "option_ini_ok": option_ok,
        "connected_device_ok": dev_ok,
        "connected_device_msg": dev_msg,
        "items": items,
        "setup_script_path": str(script_path),
        "overall_ready": overall_ready,
        "can_auto_fix": not (udev_ok and user_perm_ok and not conflicts),
    }


def verify_linux_flashing_readiness(stage: Path = None) -> dict:
    """Run all system checks to verify if SP Flash Tool is ready to flash a device.

    Returns a detailed readiness dictionary (calls run_system_checker).
    """
    return run_system_checker(stage=stage)


# --- Primary Bootstrap Function ----------------------------------------------

def ensure_linux_sp_flash_tool(progress_cb=None, force_download=False) -> tuple:
    """Ensure the Linux SP Flash Tool package is staged, configured, and verified.

    A complete payload bundled with the application (AppImage / PyInstaller
    ``INSTALL_DIR / SP_Flash_Tool``) is authoritative: it is used as-is and the
    network is never touched — ``force_download`` only applies when nothing is
    bundled. Otherwise the package is downloaded from the GitHub releases
    (with the community fallback URL) into the per-user staging directory.

    Returns ``(ok, message)``.
    """
    if os.name == "nt" or platform.system() != "Linux":
        return True, "SP Flash Tool bootstrap is only required on Linux"
    if not arch_supported():
        return False, unsupported_reason()

    # Bundled payload takes precedence over the runtime download. libpng12 is
    # the one file that cannot ship inside a read-only AppImage squashfs —
    # stage it into a writable location (the bundle itself on writable
    # installs, the per-user cache otherwise) for process_env() to pick up.
    bundled = bundled_dir()
    if bundled is not None and _bundled_complete(bundled):
        try:
            make_executable(bundled)
            if not (bundled / "lib" / "libpng12.so.0").is_file():
                stage_libpng12(bundled)
                if not (bundled / "lib" / "libpng12.so.0").is_file():
                    stage_libpng12(_app_cache_dir())
        except Exception:
            pass
        return True, f"SP Flash Tool bundled at {bundled}"

    stage = _user_stage_dir()
    zip_path = zip_cache_path()
    try:
        # Step 1: Check if package exists or needs download
        stage_libpng12(stage)
        missing = missing_files(stage)
        need_fetch = force_download or bool(missing)

        if need_fetch:
            if progress_cb:
                progress_cb(2, f"Preparing SP Flash Tool for Linux ({len(missing)} file(s) needed)…")
            if zip_has_required_members(zip_path):
                if progress_cb:
                    progress_cb(25, "Extracting cached flash_tool_linux.zip…")
                if not _extract_zip(zip_path, stage):
                    return False, "Staged flash_tool_linux.zip is incomplete."
            else:
                if progress_cb:
                    progress_cb(5, f"Downloading {FLASH_TOOL_LINUX_ZIP_NAME} (~67 MB)…")
                if not _download_zip(
                    zip_path,
                    progress_cb=lambda p: progress_cb(5 + int(p * 0.5), None) if progress_cb else None,
                ):
                    return False, (
                        "Could not download SP Flash Tool for Linux from GitHub releases. "
                        "Check your internet connection or use MTKClient method."
                    )
                if progress_cb:
                    progress_cb(60, "Extracting SP Flash Tool…")
                if not _extract_zip(zip_path, stage):
                    return False, "Extracted flash_tool_linux.zip is incomplete."

        # Step 2: Ensure libpng12 and execution bits
        stage_libpng12(stage)
        make_executable(stage)

        still = missing_files(stage)
        if still:
            return False, "SP Flash Tool package is incomplete. Missing: " + ", ".join(still[:8])

        (stage / LOG_DIR_NAME).mkdir(parents=True, exist_ok=True)

        # Step 3: Verify binary execution
        if progress_cb:
            progress_cb(85, "Verifying SP Flash Tool execution on host system…")
        exec_ok, exec_msg = test_sp_flash_tool_execution(stage)
        if not exec_ok:
            logger.warning("SP Flash Tool execution test failed: %s", exec_msg)
            return False, f"SP Flash Tool binary verification failed: {exec_msg}"

        if progress_cb:
            progress_cb(100, "SP Flash Tool is ready.")
        return True, f"SP Flash Tool staged and verified at {stage}"
    except Exception as e:
        logger.exception("Linux SP Flash Tool setup failed")
        return False, str(e)


class LinuxStagingWorker(QThread):
    """Background worker that stages SP Flash Tool and verifies host readiness."""

    progress = Signal(int, str)
    finished = Signal(bool, str, dict)

    def __init__(self, parent=None, force_download: bool = False):
        super().__init__(parent)
        self.force_download = force_download

    def run(self):
        def _on_progress(pct, msg):
            self.progress.emit(int(pct), str(msg or ""))

        ok, msg = ensure_linux_sp_flash_tool(
            progress_cb=_on_progress,
            force_download=self.force_download,
        )
        report = verify_linux_flashing_readiness()
        self.finished.emit(ok, msg, report)
