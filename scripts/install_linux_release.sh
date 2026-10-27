#!/usr/bin/env bash
# ==============================================================================
# install_linux_release.sh — Install Innioasis Updater CE from a Linux release
# Repository: https://github.com/y1-community/updater-ce-neo
#
# Installs for the current user only (no root needed). Works on any distro:
#   - *.AppImage  : copied to ~/.local/opt/innioasis-updater/ and launched directly
#                   (falls back to --appimage-extract-and-run when FUSE is missing)
#   - *.tar.gz    : the PyInstaller folder, unpacked to ~/.local/opt/innioasis-updater/app/
#
# By default the newest local build is used (AppImage preferred over tar.gz),
# searched in the current directory, ./dist and the repository's dist/.
# Use --download to fetch the latest published release instead.
#
# Usage:
#   ./scripts/install_linux_release.sh                    # newest local build
#   ./scripts/install_linux_release.sh --file path/to/InnioasisUpdater-x86_64.tar.gz
#   ./scripts/install_linux_release.sh --download         # latest GitHub release
#   ./scripts/install_linux_release.sh --uninstall
# ==============================================================================

set -euo pipefail

REPO="y1-community/updater-ce-neo"
LATEST_API="https://api.github.com/repos/${REPO}/releases/latest"

APP_NAME="InnioasisUpdater"
DISPLAY_NAME="Updater CE"
LAUNCHER_NAME="innioasis-updater"
INSTALL_ROOT="${INNIOASIS_INSTALL_DIR:-$HOME/.local/opt/innioasis-updater}"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/256x256/apps"
ICON_PATH="$ICON_DIR/$LAUNCHER_NAME.png"
DESKTOP_FILE="$DESKTOP_DIR/$LAUNCHER_NAME.desktop"
LAUNCHER="$BIN_DIR/$LAUNCHER_NAME"

ARCH="$(uname -m)"
FILE=""
DOWNLOAD=0
UNINSTALL=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${BLUE}▶${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠${NC} $1"; }
die() { echo -e "${RED}✗${NC} $1" >&2; exit 1; }

usage() {
    cat <<EOF
Innioasis Updater CE — Linux release installer

Usage: $0 [OPTIONS]

Options:
  --file <path>     Install this *.AppImage or *.tar.gz
  --download        Download the latest GitHub release instead of a local build
  --uninstall       Remove the launcher, desktop entry, icon and installed files
  -h, --help        Show this help

Installs to ${INSTALL_ROOT} (set INNIOASIS_INSTALL_DIR to change it),
with a launcher at ${LAUNCHER}.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --file)
            [ -n "${2:-}" ] || die "--file requires a path"
            FILE="$2"
            shift 2
            ;;
        --download)
            DOWNLOAD=1
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1 (see --help)"
            ;;
    esac
done

refresh_desktop_caches() {
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
}

uninstall() {
    log "Removing ${DISPLAY_NAME} from ${INSTALL_ROOT}"
    rm -f "$LAUNCHER" "$DESKTOP_FILE" "$ICON_PATH"
    rm -rf "$INSTALL_ROOT"
    refresh_desktop_caches
    success "${DISPLAY_NAME} removed. Your settings in ~/.config are kept."
}

# Newest file in DIR matching PATTERN for this machine's architecture.
latest_local() {
    local dir="$1" pattern="$2"
    local -a hits=()
    shopt -s nullglob
    hits=("$dir"/$pattern)
    shopt -u nullglob
    [ "${#hits[@]}" -gt 0 ] || return 1
    ls -t -- "${hits[@]}" | head -n 1
}

find_local_build() {
    local dir found
    for dir in "$PWD" "$PWD/dist/linux" "$SCRIPT_DIR/../dist/linux" "$PWD/dist" "$SCRIPT_DIR/../dist"; do
        [ -d "$dir" ] || continue
        found="$(latest_local "$dir" "${APP_NAME}*${ARCH}*.AppImage" || true)"
        [ -n "$found" ] && { echo "$found"; return 0; }
        found="$(latest_local "$dir" "${APP_NAME}*${ARCH}*.tar.gz" || true)"
        [ -n "$found" ] && { echo "$found"; return 0; }
    done
    return 1
}

download_latest() {
    local tmp="$1" json url name
    command -v curl >/dev/null 2>&1 || die "curl is required for --download"
    log "Looking up the latest release of ${REPO}"
    json="$(curl -fsSL "$LATEST_API")" || die "Could not reach the GitHub releases API"
    url="$(printf '%s\n' "$json" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(https[^"]*\)"/\1/' \
        | grep -E "/${APP_NAME}[^/]*${ARCH}[^/]*\.AppImage$" | head -n 1 || true)"
    if [ -z "$url" ]; then
        url="$(printf '%s\n' "$json" | grep -o '"browser_download_url": *"[^"]*"' | sed 's/.*"\(https[^"]*\)"/\1/' \
            | grep -E "/${APP_NAME}[^/]*${ARCH}[^/]*\.tar\.gz$" | head -n 1 || true)"
    fi
    [ -n "$url" ] || die "The latest release has no ${ARCH} AppImage or tar.gz for ${APP_NAME}"
    name="$(basename "$url")"
    log "Downloading ${name}"
    curl -fL --progress-bar -o "$tmp/$name" "$url" || die "Download failed: $url"
    if curl -fsSL -o "$tmp/$name.sha256" "$url.sha256" 2>/dev/null; then
        (cd "$tmp" && sha256sum -c "$name.sha256" >/dev/null) || die "Checksum mismatch for $name"
        success "Checksum verified"
    else
        warn "No checksum published for $name; skipping verification"
    fi
    FILE="$tmp/$name"
}

check_arch() {
    local name="$1"
    case "$name" in
        *x86_64*|*amd64*) [ "$ARCH" = "x86_64" ] || die "$name is for x86_64 but this machine is $ARCH" ;;
        *aarch64*|*arm64*) [ "$ARCH" = "aarch64" ] || die "$name is for arm64 but this machine is $ARCH" ;;
    esac
}

write_launcher_app_image() {
    local app="$1"
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches Innioasis Updater CE (installed by install_linux_release.sh).
# Without FUSE, AppImages cannot mount themselves; extract-and-run works everywhere.
if [ -z "\${APPIMAGE_EXTRACT_AND_RUN:-}" ] && ! ldconfig -p 2>/dev/null | grep -q 'libfuse.so.2'; then
    APPIMAGE_EXTRACT_AND_RUN=1
    export APPIMAGE_EXTRACT_AND_RUN
fi
exec "$app" "\$@"
EOF
}

install_appimage() {
    local src="$1" tmp="$2"
    local dest="$INSTALL_ROOT/$APP_NAME.AppImage"
    rm -rf "$INSTALL_ROOT/app"
    mkdir -p "$INSTALL_ROOT"
    install -m 0755 "$src" "$dest.part"
    mv -f "$dest.part" "$dest"
    # Icon ships inside the AppImage; extracting needs no FUSE.
    rm -rf "$tmp/icon" && mkdir -p "$tmp/icon"
    (cd "$tmp/icon" && "$dest" --appimage-extract innioasis-updater.png >/dev/null 2>&1) || true
    if [ -f "$tmp/icon/squashfs-root/innioasis-updater.png" ]; then
        mkdir -p "$ICON_DIR"
        install -m 0644 "$tmp/icon/squashfs-root/innioasis-updater.png" "$ICON_PATH"
    fi
    write_launcher_app_image "$dest"
}


install_tarball() {
    local src="$1" tmp="$2"
    local unpack="$tmp/unpack" top
    rm -rf "$unpack" && mkdir -p "$unpack"
    tar -xzf "$src" -C "$unpack" || die "Could not unpack $(basename "$src")"
    top="$(find "$unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
    [ -n "$top" ] || die "The archive has no top-level folder"
    local bin="$top/$(basename "$top")"
    [ -f "$bin" ] || die "The archive does not contain $(basename "$top")/$(basename "$top")"
    chmod 0755 "$bin"

    rm -f "$INSTALL_ROOT/$APP_NAME.AppImage"
    rm -rf "$INSTALL_ROOT/app"
    mkdir -p "$INSTALL_ROOT"
    mv "$top" "$INSTALL_ROOT/app"
    mkdir -p "$BIN_DIR"

    if [ -f "$INSTALL_ROOT/app/innioasis-updater.png" ]; then
        mkdir -p "$ICON_DIR"
        install -m 0644 "$INSTALL_ROOT/app/innioasis-updater.png" "$ICON_PATH"
    fi
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches Innioasis Updater CE (installed by install_linux_release.sh).
exec "$INSTALL_ROOT/app/$(basename "$top")" "\$@"
EOF
}

write_desktop_entry() {
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$DISPLAY_NAME
GenericName=Firmware Flasher
GenericName[de]=Firmware-Installationsprogramm
GenericName[es]=Instalador de firmware
GenericName[fr]=Installateur de micrologiciel
GenericName[it]=Programma di installazione firmware
GenericName[ja]=ファームウェア書き込みツール
GenericName[ko]=펌웨어 설치 도구
GenericName[pt_BR]=Instalador de Firmware
GenericName[ru]=Установщик прошивок
GenericName[zh_CN]=固件刷机与更新工具
GenericName[zh_TW]=韌體刷機與更新工具
Comment=Install and manage Innioasis Y1 system software
Comment[de]=Systemsoftware für den Innioasis Y1 installieren und verwalten
Comment[es]=Instala y administra el software del sistema Innioasis Y1
Comment[fr]=Installer et gérer le logiciel système Innioasis Y1
Comment[it]=Installa e gestisci il software di sistema Innioasis Y1
Comment[ja]=Innioasis Y1 システムソフトウェアのインストールと管理
Comment[ko]=Innioasis Y1 시스템 소프트웨어 설치 및 관리
Comment[pt_BR]=Instale e gerencie o software do sistema Innioasis Y1
Comment[ru]=Установка и управление системным ПО Innioasis Y1
Comment[zh_CN]=安装并管理 Innioasis Y1 系统固件
Comment[zh_TW]=安裝並管理 Innioasis Y1 系統韌體
Exec=$LAUNCHER
Icon=$ICON_PATH
Terminal=false
Categories=Utility;
StartupWMClass=$DISPLAY_NAME
EOF
}

main() {
    if [ "$UNINSTALL" -eq 1 ]; then
        uninstall
        exit 0
    fi

    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT

    if [ "$DOWNLOAD" -eq 1 ]; then
        download_latest "$tmp"
    elif [ -z "$FILE" ]; then
        FILE="$(find_local_build || true)"
        if [ -z "$FILE" ]; then
            die "No ${APP_NAME} *.AppImage or *.tar.gz found in ./, ./dist or the repo dist/. Use --file <path> or --download."
        fi
        log "Using the newest local build: $FILE"
    fi

    [ -f "$FILE" ] || die "No such file: $FILE"
    FILE="$(readlink -f "$FILE")"
    check_arch "$(basename "$FILE")"

    case "$FILE" in
        *.AppImage)
            log "Installing AppImage to ${INSTALL_ROOT}"
            chmod 0755 "$FILE" 2>/dev/null || true
            install_appimage "$FILE" "$tmp"
            ;;
        *.tar.gz)
            log "Installing portable folder to ${INSTALL_ROOT}/app"
            install_tarball "$FILE" "$tmp"
            ;;
        *)
            die "Unsupported file type: $(basename "$FILE") (expected .AppImage or .tar.gz)"
            ;;
    esac

    chmod 0755 "$LAUNCHER"
    write_desktop_entry
    refresh_desktop_caches
    success "${DISPLAY_NAME} installed. Launch it from your app menu or run: ${LAUNCHER_NAME}"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR is not on your PATH; the app menu entry still works." ;;
    esac
}

main "$@"
