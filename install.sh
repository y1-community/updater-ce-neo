#!/usr/bin/env bash
# ==============================================================================
# Innioasis Updater Neo — Linux Installation & Setup Script
# Repository: https://github.com/y1-community/updater-ce-neo
#
# Supports: Ubuntu, Debian, Fedora, Arch Linux, CachyOS, Omarchy, openSUSE,
#           Linux Mint, Pop!_OS, Manjaro, EndeavourOS, Gentoo, Void, Alpine, etc.
#
# Usage:
#   Direct from web:
#     curl -fsSL https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/install.sh | bash
#
#   From local repository clone:
#     ./install.sh
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
INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/updater-ce-neo"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor/256x256/apps"
UDEV_RULE_FILE="/etc/udev/rules.d/99-innioasis-mediatek.rules"

SKIP_UDEV=0
UNINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --uninstall) UNINSTALL=1 ;;
        --skip-udev) SKIP_UDEV=1 ;;
        -h|--help)
            echo "Innioasis Updater Neo Installer"
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  --uninstall    Remove Innioasis Updater Neo and its desktop integration"
            echo "  --skip-udev    Skip installing MediaTek udev rules (requires root)"
            echo "  -h, --help     Show this help message"
            exit 0
            ;;
    esac
done

banner() {
    echo -e "${CYAN}${BOLD}"
    cat << 'EOF'
   ___                 _           _        _   _            
  |_ _|_ __  _ __ (_) ___   __ _ ___(_)___ | \ | | ___  ___  
   | || '_ \| '_ \| |/ _ \ / _` / __| / __||  \| |/ _ \/ _ \ 
   | || | | | | | | | (_) | (_| \__ \ \__ \| |\  |  __/ (_) |
  |___|_| |_|_| |_|_|\___/ \__,_|___/_|___/|_| \_|\___|\___/ 
                  Innioasis Updater Neo for Linux
EOF
    echo -e "${NC}"
}

log_info() { echo -e " ${BLUE}●${NC} $1"; }
log_success() { echo -e " ${GREEN}✓${NC} $1"; }
log_warn() { echo -e " ${YELLOW}⚠${NC} $1"; }
log_error() { echo -e " ${RED}✗${NC} $1"; }
log_step() { echo -e "\n${BOLD}${CYAN}==>${NC} ${BOLD}$1${NC}"; }

# --- Uninstallation -----------------------------------------------------------
if [ "$UNINSTALL" -eq 1 ]; then
    banner
    log_step "Uninstalling Innioasis Updater Neo"

    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        log_success "Removed application directory: $INSTALL_DIR"
    fi

    for b in "$BIN_DIR/updater-ce-neo" "$BIN_DIR/innioasis-updater"; do
        if [ -f "$b" ] || [ -L "$b" ]; then
            rm -f "$b"
            log_success "Removed launcher: $b"
        fi
    done

    if [ -f "$DESKTOP_DIR/innioasis-updater.desktop" ]; then
        rm -f "$DESKTOP_DIR/innioasis-updater.desktop"
        log_success "Removed desktop menu entry"
    fi

    if [ -f "$ICON_DIR/innioasis-updater.png" ]; then
        rm -f "$ICON_DIR/innioasis-updater.png"
        log_success "Removed application icon"
    fi

    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    fi

    echo -e "\n${GREEN}${BOLD}Uninstallation complete.${NC}\n"
    exit 0
fi

# --- Installation -------------------------------------------------------------
banner
log_info "Initializing Linux installation for Innioasis Updater Neo..."

# 1. Detect Linux distribution
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

# Check architecture
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "amd64" ]; then
    log_warn "Detected architecture: $ARCH. SP Flash Tool is built for x86_64; fallback to MTKClient backend will be used."
else
    log_success "Architecture: $ARCH (x86_64 compatible with SP Flash Tool)"
fi

# 2. Check & install system dependencies
log_step "Checking System Dependencies"

MISSING_PACKAGES=()

check_cmd() {
    command -v "$1" >/dev/null 2>&1
}

# Determine package manager & required packages
PM=""
PKG_INSTALL_CMD=""
SERIAL_GROUP="dialout"

case "$DISTRO_ID" in
    arch|cachyos|omarchy|manjaro|endeavouros|garuda|steamos)
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
    *)
        # Check by ID_LIKE
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
    log_info "Missing system dependencies: ${MISSING_PACKAGES[*]}"
    if [ -n "$PKG_INSTALL_CMD" ]; then
        log_info "Attempting to install dependencies via $PM..."
        $PKG_INSTALL_CMD "${MISSING_PACKAGES[@]}"
    else
        log_error "Please install missing packages manually: ${MISSING_PACKAGES[*]}"
        exit 1
    fi
else
    log_success "Core system dependencies are present (python3, venv, git)."
fi

# 3. Clone or update repository
log_step "Staging Application Files"

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
        log_info "Updating existing installation from GitHub..."
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
    python3 -m venv "$VENV_DIR"
fi

log_info "Installing required Python packages (PySide6, pyusb, pyserial, pycryptodomex)..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$INSTALL_DIR/requirements.txt" --quiet
"$VENV_DIR/bin/pip" install Pillow --quiet
log_success "Python virtual environment configured."

# 5. Generate CLI wrappers
log_step "Installing Command-Line Launchers"

mkdir -p "$BIN_DIR"
WRAPPER_SCRIPT="$BIN_DIR/updater-ce-neo"

cat << EOF > "$WRAPPER_SCRIPT"
#!/bin/sh
exec "$VENV_DIR/bin/python" "$INSTALL_DIR/launcher.py" "\$@"
EOF

chmod +x "$WRAPPER_SCRIPT"
ln -sf "$WRAPPER_SCRIPT" "$BIN_DIR/innioasis-updater"
log_success "Installed CLI launcher: $WRAPPER_SCRIPT"

# 6. Install Desktop Entry & Icon
log_step "Creating Desktop Application Entry"

mkdir -p "$DESKTOP_DIR" "$ICON_DIR"

# Generate or copy icon.png
if [ -f "$INSTALL_DIR/assets/icon.png" ]; then
    cp "$INSTALL_DIR/assets/icon.png" "$ICON_DIR/innioasis-updater.png"
elif [ -f "$INSTALL_DIR/assets/icon.ico" ]; then
    "$VENV_DIR/bin/python" -c "
from PIL import Image
try:
    img = Image.open('$INSTALL_DIR/assets/icon.ico')
    img.save('$ICON_DIR/innioasis-updater.png')
except Exception:
    pass
" 2>/dev/null || true
fi

DESKTOP_FILE="$DESKTOP_DIR/innioasis-updater.desktop"
cat << EOF > "$DESKTOP_FILE"
[Desktop Entry]
Version=1.0
Name=Innioasis Updater Neo
GenericName=Firmware Flasher
Comment=Flash, update, and restore Innioasis Y1 & Y2 digital audio players
Exec=$WRAPPER_SCRIPT
Icon=innioasis-updater
Terminal=false
Type=Application
Categories=AudioVideo;Utility;Development;
Keywords=innioasis;y1;y2;mtk;flash;firmware;rockbox;solar;
StartupWMClass=innioasis-updater
EOF

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
# Innioasis Updater Neo - MediaTek Flashing Rules
SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", ENV{MTP_NO_PROBE}=\"1\", ENV{BRLTTY_DEVICE_IGNORE}=\"1\"
SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"0e8d\", ATTRS{idProduct}==\"0003\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\", ENV{MTP_NO_PROBE}=\"1\", ENV{BRLTTY_DEVICE_IGNORE}=\"1\"
SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"0e8d\", ATTRS{idProduct}==\"2000\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\"
SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\"
KERNEL==\"ttyACM[0-9]*\", ATTRS{idVendor}==\"0e8d\", MODE=\"0666\", TAG+=\"uaccess\", ENV{ID_MM_DEVICE_IGNORE}=\"1\", ENV{ID_MM_PORT_IGNORE}=\"1\"
EOF
chmod 644 '$UDEV_RULE_FILE'
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules && udevadm trigger
fi
if getent group '$SERIAL_GROUP' >/dev/null 2>&1; then
    usermod -aG '$SERIAL_GROUP' '${SUDO_USER:-$USER}' || true
fi
" || log_warn "Could not install udev rules automatically. You can install them later in the app."
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
echo -e "${GREEN}${BOLD}   Innioasis Updater Neo is successfully installed and ready!        ${NC}"
echo -e "${GREEN}${BOLD}══════════════════════════════════════════════════════════════════════${NC}"
echo -e "You can launch the app:"
echo -e "  ${BOLD}1. From your Application Menu:${NC} Search for ${CYAN}${BOLD}Innioasis Updater Neo${NC}"
echo -e "  ${BOLD}2. From your Terminal:${NC}         Run ${CYAN}${BOLD}updater-ce-neo${NC}\n"
