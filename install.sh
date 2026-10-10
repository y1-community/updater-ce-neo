#!/usr/bin/env bash
# ==============================================================================
# Innioasis Updater CE & MediaTek Installer — Linux Installation & Setup Script
# Repository: https://github.com/y1-community/updater-ce-neo
#
# Supports: Ubuntu, Debian, Fedora, Arch Linux, Raspberry Pi OS, Arm64 distros,
#           CachyOS, Omarchy, openSUSE, Linux Mint, Pop!_OS, Manjaro, Alpine, Void
#
# Usage:
#   Direct from web (Updater CE):
#     curl -fsSL https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/install.sh | bash
#
#   MediaTek Installer branding:
#     ./install.sh --brand mediatek_installer
#
#   Update existing installation:
#     ./install.sh --update
#
#   Uninstall:
#     ./install.sh --uninstall
# ==============================================================================

set -euo pipefail

# Visual formatting
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

REPO_URL="https://github.com/y1-community/updater-ce-neo.git"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
UDEV_RULE_FILE="/etc/udev/rules.d/99-innioasis-mediatek.rules"

# Options
BRAND="updater_ce"
SKIP_UDEV=0
UNINSTALL=0
FORCE_UPDATE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --brand)
            if [ -n "${2:-}" ]; then
                BRAND="$2"
                shift 2
            else
                echo "Error: --brand requires an argument (updater_ce or mediatek_installer)" >&2
                exit 1
            fi
            ;;
        --mediatek-installer)
            BRAND="mediatek_installer"
            shift
            ;;
        --updater-ce)
            BRAND="updater_ce"
            shift
            ;;
        --update)
            FORCE_UPDATE=1
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        --skip-udev)
            SKIP_UDEV=1
            shift
            ;;
        -h|--help)
            echo "Updater CE & MediaTek Installer — Linux Setup Script"
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --brand <brand>        Brand to install: 'updater_ce' (default) or 'mediatek_installer'"
            echo "  --mediatek-installer   Shortcut for --brand mediatek_installer"
            echo "  --updater-ce           Shortcut for --brand updater_ce"
            echo "  --update               Update existing installation without prompt"
            echo "  --uninstall            Remove application and its desktop integration"
            echo "  --skip-udev            Skip installing MediaTek udev rules (requires sudo/root)"
            echo "  -h, --help             Show this help message"
            exit 0
            ;;
        *)
            shift
            ;;
    esac
done

# Normalize brand
case "$BRAND" in
    mediatek*|mtk*)
        BRAND="mediatek_installer"
        APP_NAME="MediaTek Installer"
        PKG_SLUG="mediatek-installer"
        INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mediatek-installer"
        BIN_NAME="mediatek-installer"
        DESKTOP_FILE="$DESKTOP_DIR/mediatek-installer.desktop"
        ICON_NAME="mediatek-installer"
        ;;
    *)
        BRAND="updater_ce"
        APP_NAME="Updater CE"
        PKG_SLUG="updater-ce"
        # Reuses ~/.local/share/innioasis-updater for backward compatibility with pre-3.0
        INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/innioasis-updater"
        BIN_NAME="updater-ce"
        DESKTOP_FILE="$DESKTOP_DIR/innioasis-updater.desktop"
        ICON_NAME="innioasis-updater"
        ;;
esac

WRAPPER_SCRIPT="$BIN_DIR/$BIN_NAME"

banner() {
    echo -e "${CYAN}${BOLD}"
    if [ "$BRAND" = "mediatek_installer" ]; then
        cat << 'EOF'
  __  __          _ _     _____    _      ___           _        _ _           
 |  \/  |___   __| (_)___|_   _|__| |__  |_ _|_ _  ___| |_ __ _| | |___ _ _  
 | |\/| / -_) / _` | / _ \ | |/ -_) / /   | || ' \(_-<  _/ _` | | / -_) '_| 
 |_|  |_\___| \__,_|_\___/ |_|\___|_\_\  |___|_||_/__/\__\__,_|_|_\___|_|   
EOF
        echo -e "                   MediaTek Installer for Linux${NC}"
    else
        cat << 'EOF'
   ___                 _           _        _   _            
  |_ _|_ __  _ __ (_) ___   __ _ ___(_)___ | \ | | ___  ___  
   | || '_ \| '_ \| |/ _ \ / _` / __| / __||  \| |/ _ \/ _ \ 
   | || | | | | | | | (_) | (_| \__ \ \__ \| |\  |  __/ (_) |
  |___|_| |_|_| |_|_|\___/ \__,_|___/_|___/|_| \_|\___|\___/ 
EOF
        echo -e "                      Updater CE for Linux${NC}"
    fi
    echo ""
}

log_info() { echo -e " ${BLUE}●${NC} $1"; }
log_success() { echo -e " ${GREEN}✓${NC} $1"; }
log_warn() { echo -e " ${YELLOW}⚠${NC} $1"; }
log_error() { echo -e " ${RED}✗${NC} $1"; }
log_step() { echo -e "\n${BOLD}${CYAN}==>${NC} ${BOLD}$1${NC}"; }

# --- Interactive check for existing installation ------------------------------
if [ "$UNINSTALL" -eq 0 ] && [ "$FORCE_UPDATE" -eq 0 ]; then
    if [ -d "$INSTALL_DIR" ] && [ -f "$INSTALL_DIR/launcher.py" ]; then
        banner
        echo -e "${YELLOW}${BOLD}Existing ${APP_NAME} installation detected at:${NC} $INSTALL_DIR\n"
        if [ -t 0 ]; then
            echo -e "What would you like to do?"
            echo -e "  ${BOLD}[U]${NC} Update existing installation to latest version (default)"
            echo -e "  ${BOLD}[R]${NC} Remove / Uninstall existing installation"
            echo -e "  ${BOLD}[C]${NC} Cancel"
            echo ""
            read -r -p "Select option [U/r/c]: " user_choice
            case "${user_choice,,}" in
                r|remove|uninstall)
                    UNINSTALL=1
                    ;;
                c|cancel|q|quit)
                    echo -e "\nInstallation cancelled.\n"
                    exit 0
                    ;;
                *)
                    log_info "Proceeding with update..."
                    ;;
            esac
        else
            log_info "Non-interactive environment detected. Proceeding to update $APP_NAME..."
        fi
    fi
fi

# --- Uninstallation -----------------------------------------------------------
if [ "$UNINSTALL" -eq 1 ]; then
    banner
    log_step "Uninstalling $APP_NAME"

    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        log_success "Removed application directory: $INSTALL_DIR"
    fi

    for b in "$WRAPPER_SCRIPT" "$BIN_DIR/updater-ce" "$BIN_DIR/innioasis-updater" "$BIN_DIR/updater-ce-neo" "$BIN_DIR/mediatek-installer"; do
        if [ -f "$b" ] || [ -L "$b" ]; then
            if [ "$BRAND" = "mediatek_installer" ] && [ "$b" != "$WRAPPER_SCRIPT" ]; then
                continue
            fi
            rm -f "$b"
            log_success "Removed launcher: $b"
        fi
    done

    if [ -f "$DESKTOP_FILE" ]; then
        rm -f "$DESKTOP_FILE"
        log_success "Removed desktop entry: $DESKTOP_FILE"
    fi

    if [ -f "$ICON_DIR/${ICON_NAME}.png" ]; then
        rm -f "$ICON_DIR/${ICON_NAME}.png"
        log_success "Removed application icon"
    fi

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    fi

    echo -e "\n${GREEN}${BOLD}Uninstallation of $APP_NAME complete.${NC}\n"
    exit 0
fi

# --- Legacy Pre-3.0 Cleanup ---------------------------------------------------
cleanup_legacy() {
    log_step "Checking for Legacy Pre-3.0 Innioasis Installations"
    local found_legacy=0

    # 1. Clean temporary build folders from pre-3.0 run_linux.sh
    if [ -d "$HOME/innioasis-updater-temp" ]; then
        rm -rf "$HOME/innioasis-updater-temp"
        log_success "Removed legacy temporary folder: $HOME/innioasis-updater-temp"
        found_legacy=1
    fi

    for tmp_d in /tmp/innioasis-updater-*; do
        if [ -d "$tmp_d" ]; then
            rm -rf "$tmp_d"
            log_success "Removed legacy temporary folder: $tmp_d"
            found_legacy=1
        fi
    done

    # 2. Clean old cache directory
    if [ -d "$HOME/.cache/innioasis-updater" ]; then
        rm -rf "$HOME/.cache/innioasis-updater"
        log_success "Cleaned legacy cache: $HOME/.cache/innioasis-updater"
        found_legacy=1
    fi

    # 3. Clean deprecated preview directory if present
    local old_preview="${XDG_DATA_HOME:-$HOME/.local/share}/updater-ce-neo"
    if [ "$INSTALL_DIR" != "$old_preview" ] && [ -d "$old_preview" ]; then
        rm -rf "$old_preview"
        log_success "Removed deprecated preview folder: $old_preview"
        found_legacy=1
    fi

    # 4. Check if ~/.local/share/innioasis-updater has pre-3.0 legacy files (e.g. run_linux.sh)
    if [ -d "$INSTALL_DIR" ] && [ "$BRAND" = "updater_ce" ]; then
        if [ -f "$INSTALL_DIR/run_linux.sh" ] || { [ -f "$INSTALL_DIR/main.py" ] && [ ! -f "$INSTALL_DIR/launcher.py" ]; }; then
            log_info "Detected pre-3.0 codebase in $INSTALL_DIR. Purging legacy files..."
            rm -rf "$INSTALL_DIR"
            log_success "Cleaned legacy installation files from $INSTALL_DIR"
            found_legacy=1
        fi
    fi

    if [ "$found_legacy" -eq 0 ]; then
        log_info "No legacy pre-3.0 artifacts detected."
    else
        log_success "Legacy pre-3.0 cleanup completed successfully."
    fi
}

# --- Installation -------------------------------------------------------------
banner
log_info "Initializing Linux setup for ${BOLD}${APP_NAME}${NC}..."

cleanup_legacy

# 1. Detect Linux distribution & architecture
DISTRO_ID="unknown"
DISTRO_NAME="Linux"
DISTRO_LIKE=""
if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_NAME="${PRETTY_NAME:-$NAME}"
    DISTRO_LIKE="${ID_LIKE:-}"
fi

log_info "Detected operating system: ${BOLD}${DISTRO_NAME}${NC}"

ARCH=$(uname -m)
case "$ARCH" in
    x86_64|amd64)
        log_success "Architecture: $ARCH (x86_64 compatible with SP Flash Tool & MTKClient)"
        ;;
    aarch64|arm64)
        log_warn "Detected architecture: $ARCH (ARM64). MediaTek SP Flash Tool binaries are x86_64 only; MTKClient backend will be used for all flashing operations."
        ;;
    armv7l|armhf|armv6l)
        log_warn "Detected architecture: $ARCH (32-bit ARM / Raspberry Pi OS). SP Flash Tool is x86_64 only; MTKClient backend will be used for all operations."
        ;;
    riscv64)
        log_warn "Detected architecture: $ARCH (RISC-V). MTKClient backend will be used."
        ;;
    *)
        log_warn "Detected architecture: $ARCH. MTKClient backend will be used."
        ;;
esac

# 2. Check & install system dependencies
log_step "Checking System Dependencies"

MISSING_PACKAGES=()

check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

PM=""
PKG_INSTALL_CMD=""
SERIAL_GROUP="dialout"

case "$DISTRO_ID" in
    arch|cachyos|omarchy|manjaro|endeavouros|garuda|steamos|alarm)
        PM="pacman"
        SERIAL_GROUP="uucp"
        ! check_cmd git && MISSING_PACKAGES+=("git")
        ! check_cmd python3 && MISSING_PACKAGES+=("python")
        ! check_cmd pip && MISSING_PACKAGES+=("python-pip")
        PKG_INSTALL_CMD="sudo pacman -S --needed --noconfirm"
        ;;
    ubuntu|debian|linuxmint|pop|zorin|kali|raspbian)
        PM="apt"
        SERIAL_GROUP="dialout"
        ! check_cmd git && MISSING_PACKAGES+=("git")
        ! check_cmd python3 && MISSING_PACKAGES+=("python3")
        ! python3 -m venv --help >/dev/null 2>&1 && MISSING_PACKAGES+=("python3-venv")
        ! check_cmd pip3 && MISSING_PACKAGES+=("python3-pip")
        PKG_INSTALL_CMD="sudo apt-get update && sudo apt-get install -y"
        ;;
    fedora|rhel|centos|rocky|almalinux|nobara)
        PM="dnf"
        SERIAL_GROUP="dialout"
        ! check_cmd git && MISSING_PACKAGES+=("git")
        ! check_cmd python3 && MISSING_PACKAGES+=("python3")
        ! check_cmd pip3 && MISSING_PACKAGES+=("python3-pip")
        PKG_INSTALL_CMD="sudo dnf install -y"
        ;;
    opensuse*|suse|sles)
        PM="zypper"
        SERIAL_GROUP="dialout"
        ! check_cmd git && MISSING_PACKAGES+=("git")
        ! check_cmd python3 && MISSING_PACKAGES+=("python3")
        ! check_cmd pip3 && MISSING_PACKAGES+=("python3-pip")
        PKG_INSTALL_CMD="sudo zypper install -y"
        ;;
    alpine)
        PM="apk"
        SERIAL_GROUP="dialout"
        ! check_cmd git && MISSING_PACKAGES+=("git")
        ! check_cmd python3 && MISSING_PACKAGES+=("python3")
        ! check_cmd pip3 && MISSING_PACKAGES+=("py3-pip")
        PKG_INSTALL_CMD="sudo apk add"
        ;;
    void)
        PM="xbps"
        SERIAL_GROUP="dialout"
        ! check_cmd git && MISSING_PACKAGES+=("git")
        ! check_cmd python3 && MISSING_PACKAGES+=("python3")
        ! check_cmd pip3 && MISSING_PACKAGES+=("python3-pip")
        PKG_INSTALL_CMD="sudo xbps-install -Sy"
        ;;
    *)
        if echo "$DISTRO_LIKE" | grep -q "arch"; then
            PM="pacman"
            SERIAL_GROUP="uucp"
            PKG_INSTALL_CMD="sudo pacman -S --needed --noconfirm"
        elif echo "$DISTRO_LIKE" | grep -q -E "debian|ubuntu"; then
            PM="apt"
            SERIAL_GROUP="dialout"
            PKG_INSTALL_CMD="sudo apt-get update && sudo apt-get install -y"
        elif echo "$DISTRO_LIKE" | grep -q -E "fedora|rhel"; then
            PM="dnf"
            SERIAL_GROUP="dialout"
            PKG_INSTALL_CMD="sudo dnf install -y"
        fi
        ;;
esac

if [ ${#MISSING_PACKAGES[@]} -gt 0 ]; then
    log_info "Missing system packages: ${MISSING_PACKAGES[*]}"
    if [ -n "$PKG_INSTALL_CMD" ]; then
        log_info "Installing dependencies via $PM..."
        $PKG_INSTALL_CMD "${MISSING_PACKAGES[@]}"
    else
        log_warn "Please install missing packages manually: ${MISSING_PACKAGES[*]}"
    fi
else
    log_success "Core system packages are present (python3, venv, git)."
fi

# 3. Clone or stage application repository
log_step "Staging Application Files for $APP_NAME"

mkdir -p "$(dirname "$INSTALL_DIR")"
IS_LOCAL=0
LOCAL_SRC=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then
    CANDIDATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [ -f "$CANDIDATE_DIR/launcher.py" ] && [ -d "$CANDIDATE_DIR/src" ]; then
        IS_LOCAL=1
        LOCAL_SRC="$CANDIDATE_DIR"
    fi
fi

if [ "$IS_LOCAL" -eq 1 ]; then
    log_info "Installing from local directory: $LOCAL_SRC"
    mkdir -p "$INSTALL_DIR"
    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude='.git' --exclude='.venv' --exclude='build' --exclude='dist' --exclude='__pycache__' "$LOCAL_SRC/" "$INSTALL_DIR/"
    else
        cp -r "$LOCAL_SRC"/* "$INSTALL_DIR/"
    fi
else
    if [ -d "$INSTALL_DIR/.git" ]; then
        log_info "Updating existing repository from GitHub..."
        git -C "$INSTALL_DIR" fetch origin main
        git -C "$INSTALL_DIR" reset --hard origin/main
    else
        log_info "Cloning latest repository from $REPO_URL..."
        rm -rf "$INSTALL_DIR"
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    fi
fi
log_success "Application files staged at $INSTALL_DIR"

# 4. Configure Python virtual environment
log_step "Configuring Python Virtual Environment"

VENV_DIR="$INSTALL_DIR/.venv"
if [ ! -f "$VENV_DIR/bin/python3" ]; then
    log_info "Creating virtual environment at $VENV_DIR..."
    if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
        log_warn "python3 -m venv failed. Trying with --system-site-packages..."
        python3 -m venv --system-site-packages "$VENV_DIR"
    fi
fi

log_info "Installing required Python packages (PySide6, pyusb, pyserial, pycryptodomex)..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet || true
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    "$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet || {
        log_warn "Standard pip install had warnings. Checking PySide6 availability..."
    }
fi
"$VENV_DIR/bin/pip" install Pillow --quiet || true
log_success "Python virtual environment configured."

# 5. Generate CLI wrappers
log_step "Installing Command-Line Launchers"

mkdir -p "$BIN_DIR"

cat << EOF > "$WRAPPER_SCRIPT"
#!/bin/sh
export UPDATER_CE_BRAND="$BRAND"
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/launcher.py" "\$@"
EOF

chmod +x "$WRAPPER_SCRIPT"

if [ "$BRAND" = "updater_ce" ]; then
    ln -sf "$WRAPPER_SCRIPT" "$BIN_DIR/innioasis-updater"
    ln -sf "$WRAPPER_SCRIPT" "$BIN_DIR/updater-ce-neo"
    log_success "Installed CLI launcher: $WRAPPER_SCRIPT (aliases: innioasis-updater, updater-ce-neo)"
else
    log_success "Installed CLI launcher: $WRAPPER_SCRIPT"
fi

# 6. Install Desktop Entry & Icon
log_step "Creating Desktop Application Entry"

mkdir -p "$DESKTOP_DIR" "$ICON_DIR"

# Determine application icon
if [ "$BRAND" = "mediatek_installer" ]; then
    ICON_SRC="$INSTALL_DIR/assets/icon.png"
    if [ ! -f "$ICON_SRC" ]; then
        ICON_SRC="$INSTALL_DIR/assets/icon.ico"
    fi
else
    ICON_SRC="$INSTALL_DIR/assets/icon.png"
    if [ ! -f "$ICON_SRC" ]; then
        ICON_SRC="$INSTALL_DIR/assets/icon.ico"
    fi
fi

if [ -f "$ICON_SRC" ]; then
    if [[ "$ICON_SRC" == *.png ]]; then
        cp "$ICON_SRC" "$ICON_DIR/${ICON_NAME}.png"
    elif [[ "$ICON_SRC" == *.ico ]]; then
        "$VENV_DIR/bin/python" -c "
from PIL import Image
try:
    img = Image.open('$ICON_SRC')
    img.save('$ICON_DIR/${ICON_NAME}.png')
except Exception:
    pass
" 2>/dev/null || true
    fi
fi

if [ "$BRAND" = "mediatek_installer" ]; then
    cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Name=MediaTek Installer
GenericName=MediaTek Device Flasher
Comment=Universal MediaTek BootROM and Preloader firmware flasher
Exec=$WRAPPER_SCRIPT
Icon=$ICON_NAME
Terminal=false
Type=Application
Categories=Development;Utility;HardwareSettings;
Keywords=mediatek;mtk;flash;firmware;spflashtool;mtkclient;bootrom;preloader;
StartupWMClass=MediaTek Installer
EOF
else
    cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Name=Updater CE
GenericName=Firmware Flasher
Comment=Flash, update, and restore Innioasis and Timmkoo digital audio players
Exec=$WRAPPER_SCRIPT
Icon=$ICON_NAME
Terminal=false
Type=Application
Categories=AudioVideo;Utility;Development;
Keywords=innioasis;timmkoo;y1;y2;a5;mtk;flash;firmware;rockbox;solar;
StartupWMClass=Updater CE
EOF
fi

chmod +x "$DESKTOP_FILE"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
log_success "Installed desktop entry: $DESKTOP_FILE"

# 7. MediaTek USB & Serial Udev Rules
log_step "MediaTek Device Permissions (udev rules)"

if [ "$SKIP_UDEV" -eq 1 ]; then
    log_info "Skipping udev rules installation (--skip-udev specified)."
else
    if [ -f "$UDEV_RULE_FILE" ] && grep -q "0e8d" "$UDEV_RULE_FILE" 2>/dev/null; then
        log_success "MediaTek udev rules already configured in $UDEV_RULE_FILE"
    else
        log_info "Installing udev rules for MediaTek Boot ROM & Preloader USB access..."
        SUDO_CMD=""
        if [ "$EUID" -ne 0 ]; then
            if command -v sudo >/dev/null 2>&1; then
                SUDO_CMD="sudo"
            elif command -v pkexec >/dev/null 2>&1; then
                SUDO_CMD="pkexec"
            fi
        fi

        if [ -n "$SUDO_CMD" ] || [ "$EUID" -eq 0 ]; then
            $SUDO_CMD bash -c "cat << 'EOF' > '$UDEV_RULE_FILE'
# MediaTek Device Flashing Rules - BootROM & Preloader
SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", ENV{MTP_NO_PROBE}=\"1\", ENV{BRLTTY_DEVICE_IGNORE}=\"1\", TEST==\"power/control\", ATTR{power/control}=\"on\"
SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"0e8d\", ATTRS{idProduct}==\"0003\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", ENV{MTP_NO_PROBE}=\"1\", ENV{BRLTTY_DEVICE_IGNORE}=\"1\", TEST==\"power/control\", ATTR{power/control}=\"on\"
SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"0e8d\", ATTRS{idProduct}==\"2000\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\"
SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", RUN+=\"/bin/chmod 0666 /dev/%k\"
KERNEL==\"ttyACM[0-9]*\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", RUN+=\"/bin/chmod 0666 /dev/%k\"
KERNEL==\"ttyUSB[0-9]*\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", RUN+=\"/bin/chmod 0666 /dev/%k\"
EOF
chmod 644 '$UDEV_RULE_FILE'
cat << 'EOF' > '/etc/udev/rules.d/99-ttyacms.rules'
# Unprivileged Serial Port Access for MediaTek Devices
ACTION==\"add|change\", SUBSYSTEM==\"tty\", KERNEL==\"ttyACM[0-9]*\", MODE=\"0666\", TAG+=\"uaccess\", RUN+=\"/bin/chmod 0666 /dev/%k\"
ACTION==\"add|change\", SUBSYSTEM==\"tty\", KERNEL==\"ttyUSB[0-9]*\", MODE=\"0666\", TAG+=\"uaccess\", RUN+=\"/bin/chmod 0666 /dev/%k\"
EOF
chmod 644 '/etc/udev/rules.d/99-ttyacms.rules'
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules && udevadm trigger
fi
for grp in '$SERIAL_GROUP' plugdev dialout uucp lock; do
    if getent group \"\$grp\" >/dev/null 2>&1; then
        usermod -aG \"\$grp\" '${SUDO_USER:-$USER}' 2>/dev/null || true
    fi
done
modprobe cdc_acm 2>/dev/null || true
if systemctl is-active --quiet brltty 2>/dev/null; then
    systemctl stop brltty 2>/dev/null || true
    systemctl mask brltty 2>/dev/null || true
fi
" || log_warn "Could not install udev rules automatically. You can install them later in the app settings."
            log_success "MediaTek udev rules installed and reloaded."
        else
            log_warn "Root or sudo required for udev rules. Run manually later if needed."
        fi
    fi
fi

# 8. Check PATH
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        log_warn "$BIN_DIR is not in your PATH."
        log_info "To launch from any terminal, add this to your ~/.bashrc or ~/.zshrc:"
        echo -e "     ${BOLD}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
        ;;
esac

# 9. Completion summary
echo -e "\n${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}${BOLD}   ${APP_NAME} is successfully installed and ready!                ${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
echo -e "You can launch the app:"
echo -e "  ${BOLD}1. From your Application Menu:${NC} Search for ${CYAN}${BOLD}${APP_NAME}${NC}"
echo -e "  ${BOLD}2. From your Terminal:${NC}         Run ${CYAN}${BOLD}${BIN_NAME}${NC}\n"
