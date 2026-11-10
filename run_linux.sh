#!/usr/bin/env bash
# ==============================================================================
# run_linux.sh — Install Innioasis Updater CE (updater-ce-neo) on Linux
# Repository: https://github.com/y1-community/updater-ce-neo
#
#   curl -fsSL https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/run_linux.sh | bash
#
# What it does:
#   1. Removes an install made by the old https://innioasis.app/run_linux.sh
#      (its launcher, desktop entry / menu shortcut and the whole
#      innioasis-updater folder), plus a copy installed by an older
#      install_linux_release.sh, so no stale entry survives the upgrade.
#   2. Downloads the AppImage (default) or accepts UpdaterCE.tar.gz with
#      --tarball / --file, verifying the published SHA-256.
#   3. Installs it to ${XDG_DATA_HOME:-~/.local/share}/innioasis-updater with a
#      per-user launcher and XDG desktop/menu shortcut. Re-running with
#      --update replaces the file in place and keeps settings.
#
# No root password is needed: everything is per-user. The app asks for a
# system password itself only if an SP Flash Tool fallback needs setup.
#
# Options:
#   --file <path>   Install a local AppImage or UpdaterCE.tar.gz
#   --appimage      Install the AppImage (default)
#   --tarball       Install the portable tar.gz instead of the AppImage
#   --update        Reinstall the newest release over the current copy
#   --check         Report missing host libraries and the commands that fix them
#   --uninstall     Remove the installed copy, launcher and shortcut
#   --no-shortcut   Do not create a desktop/menu shortcut
#   -h, --help      Show this help
# ==============================================================================

set -euo pipefail

REPO="y1-community/updater-ce-neo"
# Newest first. The first release that has a package for this architecture is
# installed, so a staggered release that is not yet for Linux is skipped.
RELEASES_API="https://api.github.com/repos/${REPO}/releases?per_page=30"

APP_FOLDER="InnioasisUpdater"
APP_EXE="InnioasisUpdater"
DISPLAY_NAME="Updater CE"
AUTHOR="Ryan Specter"
LAUNCHER_NAME="innioasis-updater"

# XDG user directories are the cross-distro convention (Debian/Ubuntu,
# Fedora/RHEL, openSUSE, Arch/Manjaro, Alpine, Void, NixOS and derivatives).
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
BIN_DIR="$HOME/.local/bin"
APPLICATIONS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
ICON_PATH="$ICON_DIR/$LAUNCHER_NAME.png"
DESKTOP_FILE="$APPLICATIONS_DIR/$LAUNCHER_NAME.desktop"

INSTALL_DIR="$DATA_HOME/innioasis-updater"
APP_DIR="$INSTALL_DIR/app"
# Earlier revisions used ~/.local/opt when XDG_DATA_HOME was unset.
LEGACY_INSTALL_DIR="$HOME/.local/opt/innioasis-updater"
if [ "$LEGACY_INSTALL_DIR" = "$INSTALL_DIR" ]; then
    LEGACY_INSTALL_DIR=""
fi
LAUNCHER="$BIN_DIR/$LAUNCHER_NAME"

ARCH="$(uname -m)"
FILE=""
DOWNLOAD=1
MAKE_SHORTCUT=1
UNINSTALL=0
# The AppImage is the route we recommend: one file, no unpacked tree, and the
# in-app updater can replace it atomically. The tarball stays for machines
# without FUSE.
PACKAGE_KIND="appimage"
CHECK_HOST=0

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { printf '%b\n' "${BLUE}==>${NC} $1"; }
success() { printf '%b\n' "${GREEN}[ok]${NC} $1"; }
warn() { printf '%b\n' "${YELLOW}[!]${NC} $1"; }
die() { printf '%b\n' "${RED}[x]${NC} $1" >&2; exit 1; }

usage() {
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --file)
            [ -n "${2:-}" ] || die "--file needs a path"
            FILE="$2"
            DOWNLOAD=0
            shift 2
            ;;
        --appimage)
            PACKAGE_KIND="appimage"
            shift
            ;;
        --tarball)
            PACKAGE_KIND="tarball"
            shift
            ;;
        --lite)
            # Hand over to the terminal-only install: no Qt, no wheels, any
            # architecture with Python 3.8+, resumable download.
            _lite_dir=$(dirname "$(readlink -f "$0")")
            if [ -x "$_lite_dir/run_linux_lite.sh" ]; then
                exec "$_lite_dir/run_linux_lite.sh" "$@"
            fi
            echo "Fetching the lite installer..." >&2
            _lite_tmp="${TMPDIR:-/tmp}/run_linux_lite.$$.sh"
            if command -v curl >/dev/null 2>&1; then
                curl -fsSL -o "$_lite_tmp" https://innioasis.app/run_linux_lite.sh || die "could not download the lite installer"
            else
                wget -qO "$_lite_tmp" https://innioasis.app/run_linux_lite.sh || die "could not download the lite installer"
            fi
            exec sh "$_lite_tmp" "$@"
            ;;
        --update)
            DOWNLOAD=1
            shift
            ;;
        --check)
            CHECK_HOST=1
            shift
            ;;
        --uninstall)
            UNINSTALL=1
            shift
            ;;
        --no-shortcut)
            MAKE_SHORTCUT=0
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

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 &&
        update-desktop-database "$APPLICATIONS_DIR" >/dev/null 2>&1 || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 &&
        gtk-update-icon-cache -q -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Remove what the previous installers left behind
# ---------------------------------------------------------------------------
desktop_dirs() {
    # Every place a .desktop shortcut can live for this user on a desktop
    # install. Missing folders are simply skipped.
    printf '%s\n' \
        "$APPLICATIONS_DIR" \
        "$HOME/Desktop" \
        "$HOME/桌面" \
        "$HOME/Schreibtisch" \
        "$HOME/Bureau" \
        "$HOME/Scrivania" \
        "$HOME/Escritorio" \
        "$(xdg-user-dir DESKTOP 2>/dev/null || true)"
}

remove_old_shortcuts() {
    local dir f name
    while IFS= read -r dir; do
        [ -n "$dir" ] && [ -d "$dir" ] || continue
        for f in "$dir"/*; do
            [ -e "$f" ] || continue
            name="$(basename "$f")"
            case "$name" in
                # Anything named after the app, whichever installer wrote it.
                *[Ii]nnioasis*|*[Uu]pdater?CE*|*updater-ce*)
                    rm -rf -- "$f"
                    ;;
            esac
        done
    done <<EOF
$(desktop_dirs)
EOF
}

clean_old_install() {
    log "Removing the previous Innioasis Updater install and its shortcuts"

    # The old https://innioasis.app/run_linux.sh launcher and menu entry.
    rm -f "$LAUNCHER" "$DESKTOP_FILE"
    remove_old_shortcuts

    # Older .desktop ids used by earlier releases / third-party copies.
    rm -f \
        "$APPLICATIONS_DIR/Innioasis Updater.desktop" \
        "$APPLICATIONS_DIR/InnioasisUpdater.desktop" \
        "$APPLICATIONS_DIR/innioasis-updater.desktop"

    # Remove known old app directories, not arbitrary cache/config directories.
    if [ -n "$LEGACY_INSTALL_DIR" ]; then
        rm -rf -- "$LEGACY_INSTALL_DIR"
    fi
    rm -rf -- "$INSTALL_DIR" "$HOME/.local/opt/innioasis-updater"

    # Leftover shortcuts in the system menu from an older system-wide (root)
    # install of the same app; only touched when it is writable.
    if [ -w /usr/share/applications ]; then
        rm -f /usr/share/applications/innioasis-updater.desktop 2>/dev/null || true
    fi

    # Remove known shortcuts left on common localized desktop folders.
    while IFS= read -r dir; do
        [ -n "$dir" ] && [ -d "$dir" ] || continue
        rm -f -- "$dir/innioasis-updater.desktop" "$dir/Innioasis Updater.desktop" "$dir/Updater CE.desktop"
    done <<EOF
$(desktop_dirs)
EOF

    refresh_caches
    success "Previous install removed"
}

uninstall() {
    log "Uninstalling $DISPLAY_NAME (author: $AUTHOR)"
    rm -f "$LAUNCHER" "$DESKTOP_FILE" "$ICON_PATH"
    rm -rf -- "$INSTALL_DIR" "$HOME/.local/opt/innioasis-updater"
    if [ -n "$LEGACY_INSTALL_DIR" ]; then
        rm -rf -- "$LEGACY_INSTALL_DIR"
    fi
    remove_old_shortcuts
    refresh_caches
    success "$DISPLAY_NAME removed. Settings in ~/.config are kept."
}

# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
fetch_asset_url() {
    local json="$1" arch_re
    case "$ARCH" in
        x86_64|amd64) arch_re='^(?!.*(aarch64|arm64))' ;;
        aarch64|arm64) arch_re='(aarch64|arm64)' ;;
        *) arch_re="$(printf '%s' "$ARCH" | sed 's/[.[\*^$]/\\&/g')" ;;
    esac

    printf '%s' "$json" | python3 -c '
import json, re, sys
try:
    releases = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if isinstance(releases, dict):
    releases = [releases]
pattern = re.compile(sys.argv[1])
# Newest release first. Within a release, prefer the portable archive for this
# architecture, then the generic stable alias. Releases without a matching
# package are skipped, so a staggered release never reaches the wrong machine.
for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue
    assets = release.get("assets") or []
    for wanted in ("UpdaterCE", "InnioasisUpdater"):
        for asset in assets:
            name = asset.get("name") or ""
            if name.startswith(wanted) and name.lower().endswith(".tar.gz") and pattern.search(name):
                print(asset.get("browser_download_url") or "")
                sys.exit(0)
sys.exit(1)
' "$arch_re" 2>/dev/null || true
}

fetch_appimage_url() {
    local json="$1" arch_re
    case "$ARCH" in
        x86_64|amd64) arch_re='^(?!.*(aarch64|arm64))' ;;
        aarch64|arm64) arch_re='(aarch64|arm64)' ;;
        *) arch_re="$(printf '%s' "$ARCH" | sed 's/[.[\*^$]/\\&/g')" ;;
    esac

    printf '%s' "$json" | python3 -c '
import json, re, sys
try:
    releases = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if isinstance(releases, dict):
    releases = [releases]
pattern = re.compile(sys.argv[1])
for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue
    candidates = []
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        low = name.lower()
        if not low.endswith(".appimage") or low.endswith(".sha256"):
            continue
        if not pattern.search(name):
            continue
        rank = 0 if name.lower().startswith("updaterce") else 1
        candidates.append((rank, name, asset.get("browser_download_url") or ""))
    if candidates:
        candidates.sort()
        print(candidates[0][2])
        sys.exit(0)
sys.exit(1)
' "$arch_re" 2>/dev/null || true
}

download_appimage() {
    local tmp="$1" json url name

    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "curl or wget is required to download $DISPLAY_NAME"

    log "Looking up the newest $DISPLAY_NAME AppImage for $ARCH"
    if command -v curl >/dev/null 2>&1; then
        json="$(curl -fsSL "$RELEASES_API")" || die "Could not reach the GitHub releases API"
    else
        json="$(wget -qO- "$RELEASES_API")" || die "Could not reach the GitHub releases API"
    fi

    url="$(fetch_appimage_url "$json")"
    [ -n "$url" ] || die "No recent release publishes an AppImage for $ARCH; rerun with --tarball"

    name="$(basename "$url")"
    log "Downloading $name"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$tmp/$name" "$url" || die "Download failed: $url"
        if curl -fsSL -o "$tmp/$name.sha256" "$url.sha256" 2>/dev/null; then
            (cd "$tmp" && sha256sum -c "$name.sha256" >/dev/null) ||
                die "Checksum mismatch for $name"
            success "Checksum verified"
        fi
    else
        wget -q --show-progress -O "$tmp/$name" "$url" || die "Download failed: $url"
    fi
    FILE="$tmp/$name"
}

# Report the host libraries Qt needs that the AppImage cannot carry (they must
# match the running X server). Printing beats running a package manager on the
# user's behalf without them watching.
check_host() {
    local missing="" pkg_cmd=""
    for lib in libxcb-cursor.so.0 libxcb-image.so.0 libx11-xcb.so.1 libxkbcommon-x11.so.0; do
        if ! (command -v ldconfig >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q "$lib") \
           && ! find /usr/lib /usr/lib64 /usr/lib/*-linux-gnu -maxdepth 1 -name "$lib" -print -quit 2>/dev/null | grep -q .; then
            missing="$missing $lib"
        fi
    done
    if [ -z "$missing" ]; then
        success "Host libraries for the Qt window are present"
        return 0
    fi
    warn "Missing host libraries:$missing"
    if command -v apt-get >/dev/null 2>&1; then
        pkg_cmd="sudo apt install libxcb-cursor0 libxcb-image0 libx11-xcb1 libxkbcommon-x11-0"
    elif command -v dnf >/dev/null 2>&1; then
        pkg_cmd="sudo dnf install xcb-util-cursor xcb-util-image libxcb libxkbcommon-x11"
    elif command -v pacman >/dev/null 2>&1; then
        pkg_cmd="sudo pacman -S xcb-util-cursor xcb-util-image libxcb libxkbcommon-x11"
    elif command -v zypper >/dev/null 2>&1; then
        pkg_cmd="sudo zypper install libxcb-cursor0 xcb-util-image libxcb1 libxkbcommon-x11-0"
    fi
    if [ -n "$pkg_cmd" ]; then
        printf '  Run: %s\n' "$pkg_cmd"
    fi
    printf '  Or use the terminal interface: %s --flash-cli --help\n' "$DISPLAY_NAME"
    return 1
}

download_latest() {
    local tmp="$1" json url name

    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "curl or wget is required to download $DISPLAY_NAME"

    log "Looking up the newest $DISPLAY_NAME release for $ARCH"
    if command -v curl >/dev/null 2>&1; then
        json="$(curl -fsSL "$RELEASES_API")" || die "Could not reach the GitHub releases API"
    else
        json="$(wget -qO- "$RELEASES_API")" || die "Could not reach the GitHub releases API"
    fi

    url="$(fetch_asset_url "$json")"
    [ -n "$url" ] || die "No recent $DISPLAY_NAME release has a portable archive for $ARCH"

    name="$(basename "$url")"
    log "Downloading $name"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$tmp/$name" "$url" || die "Download failed: $url"
        if curl -fsSL -o "$tmp/$name.sha256" "$url.sha256" 2>/dev/null; then
            (cd "$tmp" && sha256sum -c "$name.sha256" >/dev/null) ||
                die "Checksum mismatch for $name"
            success "Checksum verified"
        fi
    else
        wget -q --show-progress -O "$tmp/$name" "$url" || die "Download failed: $url"
    fi
    FILE="$tmp/$name"
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
install_app() {
    local src="$1" unpack="$2" top bin relative_bin

    mkdir -p "$unpack"
    case "$src" in
        *.tar.gz|*.tgz)
            tar -xzf "$src" -C "$unpack" || die "Could not unpack $(basename "$src")"
            ;;
        *)
            die "Unsupported package: expected UpdaterCE.tar.gz"
            ;;
    esac
    # Reject absolute paths and parent traversals before using extracted files.
    if tar -tzf "$src" | awk 'BEGIN { bad=0 } /^\// || /(^|\/)\.\.($|\/)/ { bad=1 } END { exit bad ? 0 : 1 }'; then
        die "Unsafe paths in package archive"
    fi

    top="$(find "$unpack" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)"
    [ -n "$top" ] || die "The archive has no top-level folder"
    bin="$top/$APP_EXE"
    [ -f "$bin" ] || die "The archive does not contain $APP_EXE"
    chmod 0755 "$bin"

    relative_bin="${bin#"$top"/}"
    mkdir -p "$INSTALL_DIR"
    rm -rf "$APP_DIR"
    mv "$top" "$APP_DIR"
    bin="$APP_DIR/$relative_bin"
    chmod 0755 "$bin"

    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches $DISPLAY_NAME (installed by run_linux.sh).
exec "$bin" "\$@"
EOF
    chmod 0755 "$LAUNCHER"

    if [ -f "$APP_DIR/innioasis-updater.png" ]; then
        mkdir -p "$ICON_DIR"
        install -m 0644 "$APP_DIR/innioasis-updater.png" "$ICON_PATH"
    fi
}

write_shortcut() {
    [ "$MAKE_SHORTCUT" -eq 1 ] || return 0
    mkdir -p "$APPLICATIONS_DIR"
    desktop_exec="$(printf '%s' "$LAUNCHER" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$DISPLAY_NAME
Comment=Install and manage Innioasis player system software
Exec="$desktop_exec" %U
Icon=$LAUNCHER_NAME
Terminal=false
Categories=Utility;System;
StartupWMClass=$APP_EXE
X-AppImage-Author=$AUTHOR
EOF
    chmod 0644 "$DESKTOP_FILE"
    refresh_caches
}

# Install or update the AppImage. One file in the per-user data directory, so
# an update is a download plus an atomic rename: the running copy keeps working
# until the swap, and nothing is half-extracted if the download dies.
install_appimage_flow() {
    local tmp src dest
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp:-}"' EXIT

    if [ "$DOWNLOAD" -eq 1 ]; then
        download_appimage "$tmp"
    fi
    [ -n "$FILE" ] || die "No AppImage to install"
    [ -f "$FILE" ] || die "No such file: $FILE"
    FILE="$(readlink -f "$FILE")"
    src="$FILE"

    case "$(basename "$src")" in
        *.AppImage|*.appimage) ;;
        *) die "Not an AppImage: $(basename "$src") (use --tarball for the portable archive)" ;;
    esac
    head -c 4 "$src" | grep -q $'\x7fELF' || die "$(basename "$src") is not an ELF executable"

    mkdir -p "$INSTALL_DIR" "$BIN_DIR"
    dest="$INSTALL_DIR/UpdaterCE.appimage"
    cp -f "$src" "$dest.new.$$"
    chmod 0755 "$dest.new.$$"
    mv -f "$dest.new.$$" "$dest"

    # A previous tarball install would otherwise shadow the AppImage in the menu.
    rm -rf -- "$APP_DIR"
    if [ -n "$LEGACY_INSTALL_DIR" ]; then
        rm -rf -- "$LEGACY_INSTALL_DIR"
    fi
    rm -rf -- "$HOME/.local/opt/innioasis-updater"

    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches $DISPLAY_NAME from the installed AppImage (run_linux.sh).
exec "$dest" "\$@"
EOF
    chmod 0755 "$LAUNCHER"

    if [ -f "$INSTALL_DIR/innioasis-updater.png" ]; then
        mkdir -p "$ICON_DIR"
        install -m 0644 "$INSTALL_DIR/innioasis-updater.png" "$ICON_PATH"
    fi
    clean_old_shortcuts_only
    write_shortcut
    if [ "$MAKE_SHORTCUT" -eq 0 ]; then
        rm -f "$DESKTOP_FILE"
        refresh_caches
    fi
    if [ ! -e /dev/fuse ] && ! command -v fusermount >/dev/null 2>&1 && ! command -v fusermount3 >/dev/null 2>&1; then
        warn "FUSE is not available; the AppImage can still run with APPIMAGE_EXTRACT_AND_RUN=1"
    fi
    check_host || true
    success "$DISPLAY_NAME installed"
    printf '\n  Launch: %s\n  Update: %s --update\n\n' "$LAUNCHER" "$LAUNCHER"
}

main() {
    if [ "$UNINSTALL" -eq 1 ]; then
        uninstall
        return
    fi

    if [ "$CHECK_HOST" -eq 1 ]; then
        check_host
        return
    fi

    if [ "$PACKAGE_KIND" = "appimage" ]; then
        install_appimage_flow
        return
    fi

    TMP_DIR="$(mktemp -d)"
    tmp="$TMP_DIR"
    trap 'rm -rf "${TMP_DIR:-}"' EXIT

    # Clean only known legacy paths and shortcut names; keep the current copy
    # untouched until a verified package is ready to atomically replace it.
    clean_old_shortcuts_only() {
        rm -f "$DESKTOP_FILE"
        remove_old_shortcuts
    }

    if [ "$DOWNLOAD" -eq 1 ]; then
        download_latest "$tmp"
    fi
    [ -n "$FILE" ] || die "No package to install"
    [ -f "$FILE" ] || die "No such file: $FILE"
    FILE="$(readlink -f "$FILE")"

    # Extract and validate before replacing the existing working installation.
    mkdir -p "$tmp/validate"
    tar -tzf "$FILE" >/dev/null || die "Could not unpack $(basename "$FILE")"
    if tar -tzf "$FILE" | awk 'BEGIN { bad=0 } /^\// || /(^|\/)\.\.($|\/)/ { bad=1 } END { exit bad ? 0 : 1 }'; then
        die "Unsafe paths in package archive"
    fi
    tar -xzf "$FILE" -C "$tmp/validate" || die "Could not unpack $(basename "$FILE")"
    staged_top="$(find "$tmp/validate" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    [ -n "$staged_top" ] || die "The archive has no top-level folder"
    staged_exe="$staged_top/$APP_EXE"
    [ -x "$staged_exe" ] || [ -f "$staged_exe" ] || die "The archive does not contain $APP_EXE"

    log "Installing $DISPLAY_NAME to $INSTALL_DIR"
    mkdir -p "$INSTALL_DIR"
    install_stage="$INSTALL_DIR/.app.new.$$"
    install_backup="$INSTALL_DIR/.app.old.$$"
    rm -rf -- "$install_stage" "$install_backup"
    cp -a "$staged_top" "$install_stage"
    [ -x "$install_stage/$APP_EXE" ] || chmod 0755 "$install_stage/$APP_EXE"

    if [ -e "$APP_DIR" ] || [ -L "$APP_DIR" ]; then
        mv "$APP_DIR" "$install_backup" || die "Could not move the previous install aside"
    fi
    if ! mv "$install_stage" "$APP_DIR"; then
        if [ -e "$install_backup" ] || [ -L "$install_backup" ]; then
            mv "$install_backup" "$APP_DIR" || warn "Could not restore the previous install from $install_backup"
        fi
        die "Could not activate the new install"
    fi
    rm -rf -- "$install_backup"

    mkdir -p "$BIN_DIR"
    escaped_app="$(printf '%s' "$APP_DIR/$APP_EXE" | sed "s/'/'\\\\''/g")"
    quoted_app="'$escaped_app'"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches $DISPLAY_NAME (installed by run_linux.sh).
APP_BIN=$quoted_app
RUN_MARKER="\${TMPDIR:-/tmp}/innioasis-updater-start-\$\$"
rm -f "\$RUN_MARKER"
trap 'rm -f "\$RUN_MARKER"' EXIT HUP INT TERM
INNIOASIS_STARTUP_MARKER="\$RUN_MARKER"
export INNIOASIS_STARTUP_MARKER
"\$APP_BIN" "\$@"
_status=\$?
if [ -z "\${INNIOASIS_SAFE_MODE:-}" ] && [ ! -e "\$RUN_MARKER" ] && \\
   { [ "\$_status" -eq 134 ] || [ "\$_status" -eq 139 ] || [ "\$_status" -eq 132 ] || [ "\$_status" -eq 135 ]; }; then
    echo "The Qt front end stopped during startup; retrying once in safe mode." >&2
    INNIOASIS_SAFE_MODE=1
    export INNIOASIS_SAFE_MODE
    exec "\$APP_BIN" "\$@"
fi
exit "\$_status"
EOF
    chmod 0755 "$LAUNCHER"
    if [ -f "$APP_DIR/innioasis-updater.png" ]; then
        mkdir -p "$ICON_DIR"
        install -m 0644 "$APP_DIR/innioasis-updater.png" "$ICON_PATH"
    else
        rm -f "$ICON_PATH"
    fi
    if [ -n "$LEGACY_INSTALL_DIR" ]; then
        rm -rf -- "$LEGACY_INSTALL_DIR"
    fi
    rm -rf -- "$HOME/.local/opt/innioasis-updater"
    clean_old_shortcuts_only
    write_shortcut

    if [ "$MAKE_SHORTCUT" -eq 0 ]; then
        rm -f "$DESKTOP_FILE"
        refresh_caches
    fi
    success "$DISPLAY_NAME installed"
    if [ -n "$ICON_PATH" ] && [ ! -f "$ICON_PATH" ]; then
        warn "No application icon was found in the package; the menu entry uses a generic icon"
    fi
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR is not on your PATH; launch from the app menu or run $LAUNCHER" ;;
    esac
    printf '\n  Launch: %s\n\n' "$LAUNCHER"
}

main "$@"
