"""Linux SP Flash Tool staging, environment preparation, and system verification.

SP Flash Tool is MediaTek-proprietary and only ships as a Windows exe in the
original InniUpdaterChin payload, but the community publishes a Linux console
build. This module downloads and stages that package so the utility can use
SP Flash Tool as the install method on Linux (x86/x86_64) across all common
distributions (Ubuntu, Debian, Fedora, Arch Linux, CachyOS, Omarchy, openSUSE,
etc.) — with mtkclient as the automatic fallback when staging is impossible
(unsupported arch, offline, or execution failure).
"""

import grp
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

from .config import GITHUB_API
from .paths import COMPAT_DIR

logger = logging.getLogger(__name__)

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

# Standard udev rule filename installed on the host system
UDEV_RULE_FILENAME = "99-innioasis-mediatek.rules"
SETUP_SCRIPT_FILENAME = "setup_sp_flash_linux.sh"


def _app_cache_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "innioasis-updater"


def stage_dir() -> Path:
    d = _app_cache_dir() / STAGE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    if platform.system() != "Linux":
        return info

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

    distro_id = raw_data.get("ID", "").lower()
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
    (0e8d:2000, /dev/ttyACM*), while preventing ModemManager and brltty from
    interrupting BROM handshakes.
    """
    return (
        "# Innioasis Updater CE - MediaTek Flashing Rules\n"
        "# Ensures full permissions for Boot ROM (BROM) and Preloader serial interfaces.\n"
        "\n"
        "# MediaTek Boot ROM (BROM) - USB devices\n"
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", ENV{MTP_NO_PROBE}="1", '
        'ENV{BRLTTY_DEVICE_IGNORE}="1"\n'
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="0003", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1", ENV{MTP_NO_PROBE}="1", '
        'ENV{BRLTTY_DEVICE_IGNORE}="1"\n'
        "\n"
        "# MediaTek Preloader and DA - Serial TTY devices (/dev/ttyACM*)\n"
        'SUBSYSTEM=="usb", ATTRS{idVendor}=="0e8d", ATTRS{idProduct}=="2000", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"\n'
        'SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"\n'
        'KERNEL=="ttyACM[0-9]*", ATTRS{idVendor}=="0e8d", MODE="0666", TAG+="uaccess", '
        'ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"\n'
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


def write_setup_script(cache_dir: Path = None) -> Path:
    """Generate an executable shell script to install udev rules and configure the system."""
    if cache_dir is None:
        cache_dir = _app_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    script_path = cache_dir / SETUP_SCRIPT_FILENAME

    distro = detect_linux_distro()
    group = distro.get("serial_group", "dialout")
    rules_content = generate_udev_rule_content()

    script_content = f"""#!/bin/bash
# Innioasis Updater CE - Linux System Preparation Script
# Configures udev rules, permissions, and dependencies for MediaTek SP Flash Tool.

set -e

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root (or via sudo)."
    echo "Usage: sudo bash $0"
    exit 1
fi

echo "=== Innioasis Updater CE: Staging Linux System ==="
echo "Target Distribution: {distro.get('pretty_name', 'Linux')}"

# 1. Install udev rules
RULE_FILE="/etc/udev/rules.d/{UDEV_RULE_FILENAME}"
echo "Installing MediaTek udev rules to $RULE_FILE..."
cat << 'EOF' > "$RULE_FILE"
{rules_content}EOF

chmod 644 "$RULE_FILE"

# 2. Reload udev
echo "Reloading udev rules..."
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules
    udevadm trigger
    echo "udev reloaded successfully."
fi

# 3. User group
TARGET_USER="${{SUDO_USER:-$USER}}"
if [ -n "$TARGET_USER" ] && [ "$TARGET_USER" != "root" ]; then
    if getent group "{group}" >/dev/null 2>&1; then
        echo "Adding $TARGET_USER to group {group}..."
        usermod -aG "{group}" "$TARGET_USER" || true
    fi
fi

# 4. Mitigation for brltty if active
if systemctl is-active --quiet brltty 2>/dev/null; then
    echo "Notice: brltty is active. Stopping brltty to prevent MediaTek BROM hijacking..."
    systemctl stop brltty || true
fi

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
        target = Path("/etc/udev/rules.d") / UDEV_RULE_FILENAME
        try:
            target.write_text(generate_udev_rule_content(), encoding="utf-8")
            target.chmod(0o644)
            subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
            subprocess.run(["udevadm", "trigger"], check=False)
            return True, "Udev rules installed and reloaded."
        except OSError as e:
            return False, f"Failed to write udev rules: {e}"

    # Generate script and run with pkexec
    script_path = write_setup_script()
    if shutil.which("pkexec"):
        try:
            res = subprocess.run(
                ["pkexec", "bash", str(script_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode == 0:
                return True, "System rules configured successfully via pkexec."
            return False, f"Setup script exited with code {res.returncode}: {res.stderr.strip() or res.stdout.strip()}"
        except subprocess.TimeoutExpired:
            return False, "Authentication prompt timed out."
        except Exception as e:
            return False, f"pkexec failed: {e}"

    return False, f"pkexec not available. Run manually: sudo bash {script_path}"


# --- Comprehensive Verification -----------------------------------------------

def verify_linux_flashing_readiness(stage: Path = None) -> dict:
    """Run all system checks to verify if SP Flash Tool is ready to flash a device.

    Returns a detailed readiness dictionary.
    """
    if stage is None:
        stage = stage_dir()

    distro = detect_linux_distro()
    arch_ok = arch_supported()
    arch_msg = "" if arch_ok else unsupported_reason()

    # Ensure libpng12 is staged if stage directory exists
    if stage.is_dir():
        stage_libpng12(stage)
        make_executable(stage)

    missing = missing_files(stage)
    pkg_ok = (len(missing) == 0)

    libpng_staged = (stage / "lib" / "libpng12.so.0").is_file()

    ldd_ok, missing_libs = check_ldd_dependencies(stage)
    exec_ok, exec_msg = test_sp_flash_tool_execution(stage)
    udev_ok, udev_msg = check_udev_rules()
    conflicts = check_service_conflicts()
    user_perm_ok, user_perm_msg = check_user_permissions(distro.get("serial_group", "dialout"))
    script_path = write_setup_script()

    # Overall readiness evaluation:
    # Requires arch, binaries, execution test, and udev rules
    overall_ready = arch_ok and pkg_ok and exec_ok and udev_ok

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
        "setup_script_path": str(script_path),
        "overall_ready": overall_ready,
    }


# --- Primary Bootstrap Function ----------------------------------------------

def ensure_linux_sp_flash_tool(progress_cb=None, force_download=False) -> tuple:
    """Ensure the Linux SP Flash Tool package is staged, configured, and verified.

    Returns ``(ok, message)``.
    """
    if os.name == "nt" or platform.system() != "Linux":
        return True, "SP Flash Tool bootstrap is only required on Linux"
    if not arch_supported():
        return False, unsupported_reason()

    stage = stage_dir()
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
