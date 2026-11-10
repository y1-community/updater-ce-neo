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
#   2. Downloads the AppImage (default) or accepts UpdaterCE.tar.gz / .zip with
#      --tarball / --file, verifying the published SHA-256.
#   3. Installs it to ${XDG_DATA_HOME:-~/.local/share}/innioasis-updater with a
#      per-user launcher and XDG desktop/menu shortcut. Re-running with
#      --update replaces the file in place and keeps settings.
#
# Everything is per-user. One optional administrator step installs the MediaTek
# udev rule, so the app does not have to ask for a password later.
#
# This script also runs on macOS: it hands over to run_mac.sh, and run_mac.sh
# hands over here on Linux. One download link therefore works on either
# system, and both scripts accept the same options. Set INNIOASIS_OPSYS=linux
# or INNIOASIS_OPSYS=darwin to choose one explicitly.
#
# Packages are tried in this order: AppImage, portable .tar.gz, portable .zip,
# then the newest release's source archive in a per-user virtualenv, then the
# terminal interface. Homebrew, MacPorts (ports), apt, dnf/yum, pacman,
# zypper, apk, xbps, emerge, rpm-ostree and Nix are all understood when
# offering the host packages the window needs; an unknown or immutable system
# is told exactly what to install and offered the terminal interface instead
# of being stopped.
#
# Options:
#   --file <path>   Install a local AppImage, .tar.gz or .zip
#   --appimage      Install the AppImage (default)
#   --tarball       Install the portable tar.gz instead of the AppImage
#   --update        Reinstall the newest release over the current copy
#   --check         Report missing host libraries and the commands that fix them
#   --repair        Install the host libraries the Qt window needs (asks first)
#   --yes           Do not ask before --repair installs packages
#   --skip-udev     Do not offer the one-time MediaTek USB access rule
#   --skip-host-check  Skip the host X11 library check (automated tests)
#   --lite          Install the terminal-only payload instead (no Qt)
#   --tui | --cli   Start the terminal interface / command line after installing
#   --uninstall     Remove the installed copy, launcher and shortcut
#   --no-shortcut   Do not create a desktop/menu shortcut
#   -h, --help      Show this help
# ==============================================================================

set -euo pipefail

# First installs are counted against the website repository; updates against
# the app repository. Both publish the same Linux files.
FIRST_INSTALL_REPO="y1-community/innioasis-updater"
UPDATE_REPO="y1-community/updater-ce-neo"
REPO="$FIRST_INSTALL_REPO"
FALLBACK_REPO="$UPDATE_REPO"
RELEASE_APIS=()

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
REPAIR=0
ASSUME_YES=0
RUN_FLAG=""
FORCE_UPDATE=0
SKIP_UDEV=0
SKIP_HOST=0

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
    awk 'NR > 2 { if (/^# =====/) exit; print }' "$0" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
# Host helpers shared with run_mac.sh
# ---------------------------------------------------------------------------
HOST_OS="$(uname -s 2>/dev/null || echo unknown)"

abspath() {
    # BSD readlink has no -f; cd + pwd is the portable equivalent.
    [ -n "${1:-}" ] || return 1
    if [ -d "$1" ]; then
        (cd "$1" && pwd -P)
    else
        (cd "$(dirname "$1")" && printf '%s/%s\n' "$(pwd -P)" "$(basename "$1")")
    fi
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    fi
}

with_timeout() {
    # macOS has no coreutils timeout unless GNU coreutils is installed.
    local seconds="$1"
    shift
    if command -v timeout >/dev/null 2>&1; then
        timeout "$seconds" "$@"
    elif command -v gtimeout >/dev/null 2>&1; then
        gtimeout "$seconds" "$@"
    else
        "$@"
    fi
}

handoff_other_os() {
    # One download link works on either system. Refuse a second hand-off
    # rather than looping between the two scripts.
    local want="$1" here script url tmp
    if [ "${INNIOASIS_OPSYS_HANDOFF:-0}" = "1" ]; then
        die "The installers handed back to each other. Set INNIOASIS_OPSYS=linux or INNIOASIS_OPSYS=darwin to choose one."
    fi
    INNIOASIS_OPSYS_HANDOFF=1
    export INNIOASIS_OPSYS_HANDOFF

    here="$(cd "$(dirname "$0")" 2>/dev/null && pwd -P)" || here=""
    script="$here/run_$want.sh"
    if [ -n "$here" ] && [ -f "$script" ]; then
        exec bash "$script" "${ORIGINAL_ARGS[@]}"
    fi

    tmp="${TMPDIR:-/tmp}/run_$want.$$.sh"
    log "This is not Linux; fetching the $want installer that matches it."
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$tmp" "https://raw.githubusercontent.com/${UPDATE_REPO}/main/run_$want.sh" ||
            curl -fsSL -o "$tmp" "https://innioasis.app/run_$want.sh" ||
            die "Could not download run_$want.sh. Run it directly: https://innioasis.app/run_$want.sh"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$tmp" "https://raw.githubusercontent.com/${UPDATE_REPO}/main/run_$want.sh" ||
            wget -qO "$tmp" "https://innioasis.app/run_$want.sh" ||
            die "Could not download run_$want.sh. Run it directly: https://innioasis.app/run_$want.sh"
    else
        die "curl or wget is required to fetch run_$want.sh."
    fi
    exec bash "$tmp" "${ORIGINAL_ARGS[@]}"
}

ORIGINAL_ARGS=("$@")

while [ $# -gt 0 ]; do
    case "$1" in
        --file)
            [ -n "${2:-}" ] || die "--file needs a path"
            FILE="$2"
            DOWNLOAD=0
            case "$FILE" in
                *.tar.gz|*.tgz|*.zip) PACKAGE_KIND="tarball" ;;
                *.AppImage|*.appimage) PACKAGE_KIND="appimage" ;;
            esac
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
            _lite_dir=$(dirname "$(abspath "$0")")
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
            FORCE_UPDATE=1
            shift
            ;;
        --check)
            CHECK_HOST=1
            shift
            ;;
        --repair)
            REPAIR=1
            shift
            ;;
        --yes|-y)
            ASSUME_YES=1
            shift
            ;;
        --skip-udev)
            SKIP_UDEV=1
            shift
            ;;
        --skip-host-check)
            SKIP_HOST=1
            shift
            ;;
        --tui|--cli)
            RUN_FLAG="$1"
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

# The same link serves Linux and macOS. Choosing the matching script here is
# what keeps run_linux.sh from trying apt/pacman on a Mac (where none of them
# exist) and ending with a package-manager error.
case "${INNIOASIS_OPSYS:-auto}" in
    linux) ;;
    darwin) handoff_other_os mac ;;
    auto)
        case "$HOST_OS" in
            Linux) ;;
            Darwin) handoff_other_os mac ;;
            *)
                die "Unsupported operating system: $HOST_OS (this installer covers Linux and macOS). The terminal interface is described at https://innioasis.app"
                ;;
        esac
        ;;
    *)
        die "INNIOASIS_OPSYS must be linux or darwin (got: ${INNIOASIS_OPSYS})"
        ;;
esac

# Pick the release source before anything is cleaned up: the presence of a
# working install is what distinguishes an update from a first install.
if [ -x "$LAUNCHER" ] || [ -e "$INSTALL_DIR/UpdaterCE.appimage" ] || [ -e "$APP_DIR" ]; then
    REPO="$UPDATE_REPO"
    FALLBACK_REPO="$FIRST_INSTALL_REPO"
elif [ "$FORCE_UPDATE" -eq 1 ]; then
    REPO="$UPDATE_REPO"
    FALLBACK_REPO="$FIRST_INSTALL_REPO"
fi
RELEASE_APIS=(
    "https://api.github.com/repos/${REPO}/releases?per_page=30"
    "https://api.github.com/repos/${FALLBACK_REPO}/releases?per_page=30"
)
log "Package source: ${REPO} ($([ "$REPO" = "$UPDATE_REPO" ] && echo update || echo first install))"

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
verify_download() {
    # The published .sha256 is the contract; read its first field instead of
    # shelling out to sha256sum so the same check works on macOS (shasum).
    local file="$1" sum_url="$2" sumfile expected actual
    sumfile="$file.sha256"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$sumfile" "$sum_url" 2>/dev/null || return 0
    else
        wget -qO "$sumfile" "$sum_url" 2>/dev/null || return 0
    fi
    [ -s "$sumfile" ] || return 0
    expected="$(awk 'NF {print $1; exit}' "$sumfile")"
    actual="$(sha256_of "$file")"
    [ "$expected" = "$actual" ] || die "Checksum mismatch for $(basename "$file")"
    success "Checksum verified"
}

unpack_archive() {
    # unpack_archive <archive> <destination>
    local src="$1" dest="$2"
    case "$src" in
        *.zip)
            if command -v unzip >/dev/null 2>&1; then
                unzip -q "$src" -d "$dest"
            elif command -v python3 >/dev/null 2>&1; then
                python3 -m zipfile -e "$src" "$dest"
            else
                return 1
            fi
            ;;
        *)
            tar -xzf "$src" -C "$dest"
            ;;
    esac
}

archive_has_unsafe_paths() {
    # 0 when the archive contains an absolute path or a parent traversal.
    local src="$1"
    case "$src" in
        *.zip)
            if command -v python3 >/dev/null 2>&1; then
                python3 - "$src" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as bundle:
    for name in bundle.namelist():
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit(0)
raise SystemExit(1)
PY
                return $?
            fi
            if command -v unzip >/dev/null 2>&1; then
                unzip -Z1 "$src" | awk 'BEGIN { bad=0 } /^\// || /(^|\/)\.\.($|\/)/ { bad=1 } END { exit bad ? 0 : 1 }'
                return $?
            fi
            # No Python and no unzip: refuse rather than extract an archive
            # whose names could escape the destination.
            return 0
            ;;
        *)
            tar -tzf "$src" | awk 'BEGIN { bad=0 } /^\// || /(^|\/)\.\.($|\/)/ { bad=1 } END { exit bad ? 0 : 1 }'
            ;;
    esac
}

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
# Newest release first. Within a release, prefer the portable tar.gz, then the
# portable zip, for this architecture. Releases without a matching package are
# skipped, so a staggered release never reaches the wrong machine.
for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue
    candidates = []
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        low = name.lower()
        # The terminal session archive is a different download. Treating it
        # as the window package would install a text-only tree.
        if "lite" in low or "terminal" in low:
            continue
        if not low.startswith(("updaterce", "innioasisupdater")) or not pattern.search(name):
            continue
        if low.endswith(".tar.gz") or low.endswith(".tgz"):
            rank = 0
        elif low.endswith(".zip"):
            rank = 1
        else:
            continue
        candidates.append((rank, name, asset.get("browser_download_url") or ""))
    if candidates:
        candidates.sort()
        print(candidates[0][2])
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

    if ! command -v python3 >/dev/null 2>&1; then
        # The AppImage carries its own Python, so a machine with no python3
        # can still install it. Pick the asset with tools every host has.
        printf '%s' "$json" \
            | grep -o '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]*"' \
            | sed 's/.*"\(https[^"]*\)"/\1/' \
            | grep -Ei '\.appimage$' \
            | { case "$ARCH" in
                    aarch64|arm64) grep -Ei 'aarch64|arm64' ;;
                    *) grep -Eiv 'aarch64|arm64' ;;
                esac; } \
            | head -n 1 || true
        return 0
    fi

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
    local tmp="$1" json url name api

    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "curl or wget is required to download $DISPLAY_NAME"

    log "Looking up the newest $DISPLAY_NAME AppImage for $ARCH"
    url=""
    for api in "${RELEASE_APIS[@]}"; do
        if command -v curl >/dev/null 2>&1; then
            json="$(curl -fsSL "$api" 2>/dev/null)" || continue
        else
            json="$(wget -qO- "$api" 2>/dev/null)" || continue
        fi
        url="$(fetch_appimage_url "$json")"
        [ -n "$url" ] && break
    done
    [ -n "$url" ] || return 1

    name="$(basename "$url")"
    log "Downloading $name"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$tmp/$name" "$url" || die "Download failed: $url"
        verify_download "$tmp/$name" "$url.sha256"
    else
        wget -q --show-progress -O "$tmp/$name" "$url" || die "Download failed: $url"
    fi
    FILE="$tmp/$name"
}

# The X11 client libraries the distro supplies, not the bundle. Qt 6.5+ loads
# libxcb-cursor while opening the xcb plugin; the others are checked because a
# distro can ship some of the family and not the rest.
host_libs_missing() {
    local missing="" lib
    # macOS needs none of these: the .app carries its own Qt and uses Cocoa.
    [ "$HOST_OS" = "Darwin" ] && return 0
    for lib in libxcb-cursor.so.0 libxcb-image.so.0 libxcb-render-util.so.0 \
               libxcb-util.so.1 libx11-xcb.so.1 libxkbcommon-x11.so.0; do
        if ! (command -v ldconfig >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q "$lib") \
           && ! find /usr/lib /usr/lib64 /usr/lib/*-linux-gnu /usr/lib64/*-linux-gnu -maxdepth 1 -name "$lib" -print -quit 2>/dev/null | grep -q .; then
            missing="$missing $lib"
        fi
    done
    printf '%s' "$missing"
}

missing_tools() {
    local missing=""
    command -v python3 >/dev/null 2>&1 || missing="$missing python3"
    if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
        missing="$missing curl-or-wget"
    fi
    printf '%s' "$missing"
}

find_brew() {
    local candidate
    if command -v brew >/dev/null 2>&1; then
        command -v brew
        return 0
    fi
    for candidate in "$HOME/.linuxbrew/bin/brew" /home/linuxbrew/.linuxbrew/bin/brew \
                     "$HOME/.homebrew/bin/brew" /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

# Sets PM_KIND, PM_LABEL, PM_PRIV (1 = needs administrator rights),
# PM_CMDS (full install), PM_REQUIRED (window libraries only, used when the
# full set is not packaged for this release) and PM_NOTE (guidance).
detect_package_manager() {
    PM_KIND="unknown"
    PM_LABEL="this system"
    PM_PRIV=1
    PM_CMDS=""
    PM_REQUIRED=""
    PM_NOTE=""

    if [ -e /run/ostree-booted ] && command -v rpm-ostree >/dev/null 2>&1; then
        PM_KIND="rpm-ostree"
        PM_LABEL="rpm-ostree (immutable image)"
        PM_CMDS="rpm-ostree install xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11 libusb1 python3 python3-pip"
        PM_REQUIRED="$PM_CMDS"
        PM_NOTE="Layering packages on an immutable image needs a reboot, so the window will not open until then. The terminal interface works now; running the AppImage inside toolbox or distrobox is the other route."
    elif command -v apt-get >/dev/null 2>&1; then
        PM_KIND="apt"
        PM_LABEL="apt (Debian, Ubuntu, Mint, Pop!_OS, Zorin, Deepin, Kali)"
        PM_CMDS="apt-get update && apt-get install -y libxcb-cursor0 libxcb-image0 libxcb-render-util0 libxcb-util1 libxkbcommon-x11-0 libgl1 libusb-1.0-0 python3 python3-venv python3-pip"
        PM_REQUIRED="apt-get update && apt-get install -y libxcb-cursor0 libxcb-image0 libxcb-render-util0 libxcb-util1 libxkbcommon-x11-0"
        PM_NOTE="libfuse2 (or libfuse2t64) is only needed to loop-mount the AppImage; without FUSE it extracts and runs."
    elif command -v dnf >/dev/null 2>&1 || command -v yum >/dev/null 2>&1; then
        if command -v dnf >/dev/null 2>&1; then DNF=dnf; else DNF=yum; fi
        PM_KIND="dnf"
        PM_LABEL="dnf (Fedora, RHEL, Rocky, AlmaLinux, Nobara, Bazzite)"
        PM_CMDS="$DNF install -y xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11 mesa-libGL libusb1 python3 python3-pip"
        PM_REQUIRED="$DNF install -y xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11"
        PM_NOTE="fuse and fuse-libs are only needed to loop-mount the AppImage."
    elif command -v zypper >/dev/null 2>&1; then
        PM_KIND="zypper"
        PM_LABEL="zypper (openSUSE, SLES)"
        PM_CMDS="zypper --non-interactive install libxcb-cursor0 xcb-util-image xcb-util-renderutil libxcb-util1 libxkbcommon-x11-0 libGL1 libusb-1_0-0 python3 python3-pip"
        PM_REQUIRED="zypper --non-interactive install libxcb-cursor0 xcb-util-image xcb-util-renderutil libxcb-util1 libxkbcommon-x11-0"
        PM_NOTE="libfuse2 is only needed to loop-mount the AppImage."
    elif command -v pacman >/dev/null 2>&1; then
        PM_KIND="pacman"
        PM_LABEL="pacman (Arch, CachyOS, Omarchy, Manjaro, EndeavourOS, SteamOS)"
        PM_CMDS="pacman -S --needed --noconfirm xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11 mesa fuse2 libusb python python-pip"
        PM_REQUIRED="pacman -S --needed --noconfirm xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11"
        PM_NOTE="fuse2 is only needed to loop-mount the AppImage."
    elif command -v apk >/dev/null 2>&1; then
        PM_KIND="apk"
        PM_LABEL="apk (Alpine and postmarketOS)"
        PM_CMDS="apk add xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11 mesa-gl fuse libusb python3 py3-pip"
        PM_REQUIRED="apk add xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11"
    elif command -v xbps-install >/dev/null 2>&1; then
        PM_KIND="xbps"
        PM_LABEL="xbps (Void Linux)"
        PM_CMDS="xbps-install -Sy xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11 libGL libusb python3 python3-pip"
        PM_REQUIRED="xbps-install -Sy xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11"
    elif command -v emerge >/dev/null 2>&1; then
        PM_KIND="emerge"
        PM_LABEL="Portage (Gentoo, Funtoo)"
        PM_CMDS="emerge --noreplace x11-libs/xcb-util-cursor x11-libs/xcb-util-image x11-libs/xcb-util-renderutil x11-libs/xcb-util x11-libs/libxkbcommon media-libs/mesa dev-libs/libusb dev-lang/python"
        PM_REQUIRED="emerge --noreplace x11-libs/xcb-util-cursor x11-libs/xcb-util-image x11-libs/xcb-util-renderutil x11-libs/xcb-util x11-libs/libxkbcommon"
        PM_NOTE="Portage may need accepting a keyword or licence for one of these; it will say which."
    elif command -v nix >/dev/null 2>&1; then
        PM_KIND="nix"
        PM_LABEL="Nix"
        PM_NOTE="Nix cannot put X11 client libraries where a non-Nix AppImage looks for them. Run the AppImage through appimage-run, for example: nix-shell -p appimage-run --run 'appimage-run ./UpdaterCE.appimage'. The terminal interface works from this installer on any of those setups."
    elif command -v guix >/dev/null 2>&1; then
        PM_KIND="guix"
        PM_LABEL="Guix"
        PM_NOTE="Guix keeps its libraries outside the loader path an AppImage uses. Run the portable tarball inside a guix shell that provides the xcb libraries, or use the terminal interface."
    elif BREW_BIN="$(find_brew)"; then
        PM_KIND="brew"
        PM_LABEL="Homebrew ($BREW_BIN)"
        PM_CMDS="$BREW_BIN install libxcb libxkbcommon libusb python"
        PM_REQUIRED="$PM_CMDS"
        PM_PRIV=0
        PM_NOTE="Homebrew installs into your own account, so no password is needed. The distro's own packages are the better fix when they exist."
    elif command -v port >/dev/null 2>&1; then
        PM_KIND="port"
        PM_LABEL="MacPorts"
        PM_CMDS="port install xorg-libxcb libxkbcommon libusb python312"
        PM_REQUIRED="$PM_CMDS"
    else
        PM_NOTE="No package manager this script recognizes is available. Install the Qt XCB libraries (libxcb-cursor, libxcb-image, libxcb-render-util, libxcb-util, libxkbcommon-x11) with your distribution's own tool, or keep using the terminal interface."
    fi
}

print_package_hint() {
    local first
    if [ "$PM_KIND" != "unknown" ] && [ -n "$PM_LABEL" ]; then
        printf '  Package manager: %s\n' "$PM_LABEL"
    fi
    if [ -n "$PM_CMDS" ]; then
        first="$(printf '%s\n' "$PM_CMDS" | sed -n '1p')"
        printf '  Command: %s\n' "$first"
    fi
    [ -n "$PM_NOTE" ] && printf '  %s\n' "$PM_NOTE"
    return 0
}

check_host() {
    local missing tools
    missing="$(host_libs_missing)"
    tools="$(missing_tools)"
    if [ -z "$missing$tools" ]; then
        success "Host libraries for the Qt window are present"
        return 0
    fi
    [ -n "$missing" ] && warn "Missing host libraries:$missing"
    [ -n "$tools" ] && warn "Missing download/install tools:$tools"
    detect_package_manager
    print_package_hint
    printf '  Or use the terminal interface: %s --tui\n' "$DISPLAY_NAME"
    return 1
}

download_latest() {
    local tmp="$1" json url name api

    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "curl or wget is required to download $DISPLAY_NAME"

    log "Looking up the newest $DISPLAY_NAME release for $ARCH"
    url=""
    for api in "${RELEASE_APIS[@]}"; do
        if command -v curl >/dev/null 2>&1; then
            json="$(curl -fsSL "$api" 2>/dev/null)" || continue
        else
            json="$(wget -qO- "$api" 2>/dev/null)" || continue
        fi
        url="$(fetch_asset_url "$json")"
        [ -n "$url" ] && break
    done
    [ -n "$url" ] || return 1

    name="$(basename "$url")"
    log "Downloading $name"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$tmp/$name" "$url" || die "Download failed: $url"
        verify_download "$tmp/$name" "$url.sha256"
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

    # Reject absolute paths and parent traversals before extracting anything.
    if archive_has_unsafe_paths "$src"; then
        die "Unsafe paths in package archive"
    fi
    mkdir -p "$unpack"
    unpack_archive "$src" "$unpack" || die "Could not unpack $(basename "$src")"

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

clean_old_shortcuts_only() {
    rm -f "$DESKTOP_FILE"
    remove_old_shortcuts
}

# Install or update the AppImage. One file in the per-user data directory, so
# an update is a download plus an atomic rename: the running copy keeps working
# until the swap, and nothing is half-extracted if the download dies.
install_appimage_flow() {
    local tmp src dest
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp:-}"' EXIT

    if [ "$DOWNLOAD" -eq 1 ]; then
        download_appimage "$tmp" || return 1
    fi
    [ -n "$FILE" ] || die "No AppImage to install"
    [ -f "$FILE" ] || die "No such file: $FILE"
    FILE="$(abspath "$FILE")"
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
    install_udev_rules || true
    if ! probe_installed_window; then
        fallback_to_tui
    fi
    printf '\n  Launch: %s\n  Update: %s --update\n\n' "$LAUNCHER" "$LAUNCHER"
}

run_privileged() {
    # Homebrew runs as the user; rpm-ostree and the distro managers need root.
    # pkexec is preferred because it uses the desktop's own authentication
    # window; run0 is the systemd replacement; sudo and doas stay supported.
    local cmd="$1"
    if [ "$PM_PRIV" -eq 0 ] || [ "$(id -u)" -eq 0 ]; then
        sh -c "$cmd"
    elif command -v pkexec >/dev/null 2>&1; then
        pkexec sh -c "$cmd"
    elif command -v run0 >/dev/null 2>&1; then
        run0 --pipe sh -c "$cmd"
    elif command -v sudo >/dev/null 2>&1; then
        sudo sh -c "$cmd"
    elif command -v doas >/dev/null 2>&1; then
        doas sh -c "$cmd"
    else
        return 127
    fi
}

repair_host() {
    # Install only the client-side XCB stack, and only after saying what will
    # run. This is the fix for "Could not load the Qt platform plugin xcb" that
    # does not require the user to know which of the library names their
    # distribution uses.
    #
    # A machine with no recognized package manager is not a failure: it is
    # told the exact package names and pointed at the terminal interface,
    # which needs none of them. That is what keeps atomic, rootless and
    # Nix/Guix systems usable instead of stopping at "no supported package
    # manager".
    local missing tools first answer
    missing="$(host_libs_missing)"
    tools="$(missing_tools)"
    if [ -z "$missing" ] && [ -z "$tools" ]; then
        success "Host libraries for the Qt window are present"
        return 0
    fi
    [ -n "$missing" ] && warn "Missing host libraries:$missing"
    [ -n "$tools" ] && warn "Missing download/install tools:$tools"

    detect_package_manager
    print_package_hint

    if [ -z "$PM_CMDS" ]; then
        printf '  The terminal interface installs firmware without any of these libraries:\n    %s --tui\n' "$DISPLAY_NAME"
        return 1
    fi

    first="$(printf '%s\n' "$PM_CMDS" | sed -n '1p')"
    if [ "$ASSUME_YES" -ne 1 ]; then
        printf 'Install these packages now? [y/N] '
        if [ -r /dev/tty ]; then
            read -r answer < /dev/tty || answer=""
        else
            read -r answer || answer=""
        fi
        case "$answer" in
            y|Y|yes|YES) ;;
            *)
                echo "Not installed. The AppImage will fall back to the terminal interface."
                return 1
                ;;
        esac
    fi

    if ! run_privileged "$first"; then
        if [ -n "$PM_REQUIRED" ] && [ "$PM_REQUIRED" != "$first" ]; then
            warn "The full package set did not install; trying only the window libraries."
            if ! run_privileged "$PM_REQUIRED"; then
                warn "The package manager did not finish; install the packages above manually."
                return 1
            fi
        else
            warn "The package manager did not finish; install the packages above manually."
            return 1
        fi
    fi

    if [ -z "$(host_libs_missing)" ]; then
        success "Host libraries installed. Reopen Updater CE for the window."
        return 0
    fi
    warn "Some libraries are still missing. Reopen Updater CE, or use the terminal interface:"
    printf '  %s --tui\n' "$DISPLAY_NAME"
    return 1
}

offer_choice() {
    if [ -n "${INNIOASIS_CHOICE:-}" ]; then
        return 0
    fi
    if [ "$CHECK_HOST" -eq 1 ] || [ "$REPAIR" -eq 1 ] || [ "$UNINSTALL" -eq 1 ] || [ -n "$FILE" ] || [ -n "$RUN_FLAG" ]; then
        return 0
    fi
    if [ -r /dev/tty ]; then
        printf '%s\n' "What would you like to do?"
        printf '%s\n' "  1) Install Innioasis Updater (full app)  [recommended]"
        printf '%s\n' "  2) Run once in Terminal"
        printf '%s' "Choice [1]: "
        read -r _choice < /dev/tty || _choice="1"
    else
        _choice="1"
    fi
    case "$_choice" in
        2|[Tt]|terminal|Terminal)
            export INNIOASIS_CHOICE=terminal
            log "Starting a one-time terminal session."
            _here=$(dirname "$(abspath "$0")")
            if [ -f "$_here/run_linux_lite.sh" ]; then
                exec sh "$_here/run_linux_lite.sh"
            fi
            _tmp="${TMPDIR:-/tmp}/run_linux_lite.$$.sh"
            if command -v curl >/dev/null 2>&1; then
                curl -fsSL -o "$_tmp" https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/run_linux_lite.sh \
                    || curl -fsSL -o "$_tmp" https://innioasis.app/run_linux_lite.sh \
                    || die "could not download the terminal installer"
            else
                wget -qO "$_tmp" https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/run_linux_lite.sh \
                    || die "could not download the terminal installer"
            fi
            exec sh "$_tmp"
            ;;
        *)
            export INNIOASIS_CHOICE=full
            log "Installing the full app. This is the recommended choice."
            ;;
    esac
}

prepare_for_window() {
    [ "$SKIP_HOST" -eq 0 ] || return 0
    check_host && return 0
    if [ -r /dev/tty ]; then
        printf '%s' "Install the missing system packages now? [Y/n] "
        read -r answer < /dev/tty || answer="Y"
    else
        answer="Y"
    fi
    case "$answer" in
        n|N|no|NO)
            warn "Continuing without them. If the window cannot open, the app starts in the terminal."
            ;;
        *)
            ASSUME_YES=1
            repair_host || warn "The system packages were not installed; the app will fall back to the terminal if needed."
            ;;
    esac
}

install_udev_rules() {
    # The app can ask for this later, but doing it in the installer means the
    # user sees one clear prompt and the first flash does not fail with an
    # access error. The rule is scoped to MediaTek vendor IDs and session ACLs;
    # it never grants world access to USB or serial nodes.
    [ "$SKIP_UDEV" -eq 0 ] || return 0
    if grep -qs '0e8d' /etc/udev/rules.d/*.rules 2>/dev/null; then
        success "MediaTek USB access rule already present"
        return 0
    fi
    if [ "$ASSUME_YES" -ne 1 ]; then
        if [ -r /dev/tty ]; then
            printf 'Install the MediaTek USB access rule now? This needs your password once. [Y/n] '
            read -r answer < /dev/tty || answer="Y"
        else
            warn "Skipping the MediaTek USB rule (no terminal for the password prompt)."
            printf '  Run later: %s --repair --yes\n' "$0"
            return 0
        fi
        case "$answer" in
            n|N|no|NO) warn "Skipping MediaTek USB access setup."; return 0 ;;
        esac
    fi

    local tmp="" group="dialout" runner=""
    if grep -qE '^(ID=arch|ID_LIKE=.*arch)' /etc/os-release 2>/dev/null; then
        group="uucp"
    fi
    tmp="$(mktemp -d)" || return 1
    cat > "$tmp/99-innioasis-mediatek.rules" <<EOF
# Updater CE - MediaTek flash-mode access
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="0003", MODE="0660", GROUP="$group", TAG+="uaccess", ENV{MTP_NO_PROBE}="1", ENV{BRLTTY_DEVICE_IGNORE}="1"
SUBSYSTEM=="usb", ATTR{idVendor}=="0e8d", ATTR{idProduct}=="2000", MODE="0660", GROUP="$group", TAG+="uaccess", ENV{MTP_NO_PROBE}="1", ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="0e8d", MODE="0660", GROUP="$group", TAG+="uaccess", ENV{ID_MM_DEVICE_IGNORE}="1", ENV{ID_MM_PORT_IGNORE}="1"
EOF
    cat > "$tmp/install.sh" <<'EOF'
#!/bin/sh
set -e
install -m 0644 "$1" /etc/udev/rules.d/99-innioasis-mediatek.rules
install -m 0644 "$1" /etc/udev/rules.d/99-ttyacms.rules
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --action=change --subsystem-match=usb --attr-match=idVendor=0e8d 2>/dev/null || true
    udevadm settle --timeout=5 2>/dev/null || true
fi
EOF
    chmod 0700 "$tmp/install.sh"
    if command -v pkexec >/dev/null 2>&1; then
        set +e
        pkexec sh "$tmp/install.sh" "$tmp/99-innioasis-mediatek.rules"
        _udev_status=$?
        set -e
    elif command -v run0 >/dev/null 2>&1; then
        set +e
        run0 --pipe sh "$tmp/install.sh" "$tmp/99-innioasis-mediatek.rules"
        _udev_status=$?
        set -e
    elif command -v sudo >/dev/null 2>&1; then
        set +e
        sudo sh "$tmp/install.sh" "$tmp/99-innioasis-mediatek.rules"
        _udev_status=$?
        set -e
    else
        warn "No administrator prompt tool found; the app will offer USB access setup when needed."
        rm -rf "$tmp"
        return 0
    fi
    rm -rf "$tmp"
    if [ "${_udev_status:-1}" -eq 0 ]; then
        success "MediaTek USB access rule installed"
    else
        warn "MediaTek USB access was not installed; the app can offer it again."
    fi
    return 0
}

probe_installed_window() {
    # A completed download is not a working window. This opens the real Qt
    # window minimized through --gui-probe, so a missing host library or an
    # unusable Wayland/X11 session is caught while the installer is still here
    # to explain it and repair what it can.
    [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 0
    [ -z "$RUN_FLAG" ] || return 0
    command -v "$LAUNCHER" >/dev/null 2>&1 || return 0

    local log_file="${TMPDIR:-/tmp}/innioasis-gui-probe-$$.log" rc=0
    log "Checking that the installed window can open…"
    set +e
    with_timeout 45 "$LAUNCHER" --gui-probe >"$log_file" 2>&1
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
        success "Window check passed"
        rm -f "$log_file"
        return 0
    fi
    warn "The window did not open cleanly (exit $rc)."
    sed -n '1,12p' "$log_file" 2>/dev/null || true
    if repair_host; then
        set +e
        with_timeout 45 "$LAUNCHER" --gui-probe >>"$log_file" 2>&1
        rc=$?
        set -e
        if [ "$rc" -eq 0 ]; then
            success "Window check passed after repairing host libraries"
            rm -f "$log_file"
            return 0
        fi
    fi
    warn "The window still cannot open. The terminal interface will be used."
    printf '  Full diagnostics: %s\n' "$log_file"
    return 1
}

fallback_to_tui() {
    printf '\n  Start the terminal interface with: %s --tui\n' "$LAUNCHER"
    if [ -r /dev/tty ]; then
        exec "$LAUNCHER" --tui
    fi
    return 0
}

install_from_source() {
    local tmp url top entry
    log "No prebuilt package is published for $ARCH."
    log "Laying the app out from the release source. It is the same Python the window is built from."
    command -v python3 >/dev/null 2>&1 || die "python3 is required to lay the app out from source"
    command -v curl >/dev/null 2>&1 || die "curl is required to download the source archive"
    tmp="$(mktemp -d)"
    url="$(INNIOASIS_SOURCE_REPOS="$REPO $FALLBACK_REPO y1-community/updater-ce-neo y1-community/innioasis-updater ryan-specter/updater-ce-neo ryan-specter/innioasis-updater" python3 - <<'PY'
import json, os, urllib.request
headers = {"User-Agent": "UpdaterCE-installer", "Accept": "application/vnd.github+json"}
repos = tuple(dict.fromkeys(os.environ.get("INNIOASIS_SOURCE_REPOS", "").split()))
for repo in repos:
    api = "https://api.github.com/repos/%s/releases?per_page=10" % repo
    try:
        req = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as response:
            releases = json.loads(response.read())
    except Exception:
        continue
    for release in releases:
        if release.get("draft") or release.get("prerelease"):
            continue
        zipball = release.get("zipball_url") or ""
        if zipball:
            print(zipball)
            raise SystemExit(0)
raise SystemExit(1)
PY
)" || die "Could not find a source archive for the app"
    [ -n "$url" ] || die "Could not find a source archive for the app"
    log "Downloading the source archive"
    curl -fL --progress-bar -o "$tmp/source.zip" "$url" || die "Source download failed"
    mkdir -p "$tmp/src"
    python3 - "$tmp/source.zip" "$tmp/src" <<'PY' || die "Could not unpack the source archive"
import sys, zipfile
from pathlib import Path
archive, dest = sys.argv[1], Path(sys.argv[2])
dest.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(archive) as bundle:
    for info in bundle.infolist():
        name = info.filename
        if name.startswith("/") or ".." in name.split("/"):
            raise SystemExit("unsafe path in source archive")
    bundle.extractall(dest)
PY
    top="$(find "$tmp/src" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
    if [ ! -f "$top/launcher.py" ] && [ ! -f "$top/firmware_downloader.py" ]; then
        die "That source archive is not the Updater app"
    fi
    mkdir -p "$INSTALL_DIR"
    rm -rf -- "$APP_DIR"
    mv "$top" "$APP_DIR"
    if [ ! -x "$APP_DIR/venv/bin/python" ]; then
        python3 -m venv "$APP_DIR/venv" ||
            python3 -m venv --system-site-packages "$APP_DIR/venv" ||
            die "python3-venv is required. Re-run and accept the system package install."
    fi
    if [ -f "$APP_DIR/requirements.txt" ]; then
        "$APP_DIR/venv/bin/python" -m pip install --upgrade pip --quiet --disable-pip-version-check || true
        if ! "$APP_DIR/venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt" --disable-pip-version-check; then
            warn "The Qt front end could not be installed (often a Python without a matching PySide6 wheel)."
            warn "Installing the non-Qt requirements so the terminal interface still works."
            grep -viE '^(PySide6|shiboken6)' "$APP_DIR/requirements.txt" > "$tmp/requirements-tui.txt" || true
            "$APP_DIR/venv/bin/python" -m pip install -r "$tmp/requirements-tui.txt" --disable-pip-version-check \
                || die "Could not install the app's Python requirements into its own environment"
        fi
    fi
    entry="launcher.py"
    [ -f "$APP_DIR/launcher.py" ] || entry="firmware_downloader.py"
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
cd "$APP_DIR" || exit 1
exec "$APP_DIR/venv/bin/python" "$entry" "\$@"
EOF
    chmod 0755 "$LAUNCHER"
    write_shortcut
    success "$DISPLAY_NAME is installed from source in $APP_DIR"
    success "Launch: $LAUNCHER"
    rm -rf -- "$tmp"
}

main() {
    if [ "$REPAIR" -eq 1 ]; then
        # A machine with no package manager is not a crash: report what is
        # missing, point at the terminal interface and return a status.
        if repair_host; then
            exit 0
        fi
        printf '  %s --tui still installs firmware without the window.\n' "$DISPLAY_NAME"
        exit 1
    fi
    if [ "$UNINSTALL" -eq 1 ]; then
        uninstall
        return
    fi

    if [ "$CHECK_HOST" -eq 1 ]; then
        if check_host; then
            exit 0
        fi
        exit 1
    fi

    offer_choice
    prepare_for_window

    if [ "$PACKAGE_KIND" = "appimage" ]; then
        if install_appimage_flow; then
            return
        fi
        warn "No AppImage is published for $ARCH. Trying the portable archive."
        PACKAGE_KIND="tarball"
        DOWNLOAD=1
    fi

    TMP_DIR="$(mktemp -d)"
    tmp="$TMP_DIR"
    trap 'rm -rf "${TMP_DIR:-}"' EXIT

    # Clean only known legacy paths and shortcut names; keep the current copy
    # untouched until a verified package is ready to atomically replace it.
    if [ "$DOWNLOAD" -eq 1 ]; then
        if ! download_latest "$tmp"; then
            install_from_source
            return
        fi
    fi
    [ -n "$FILE" ] || die "No package to install"
    [ -f "$FILE" ] || die "No such file: $FILE"
    FILE="$(abspath "$FILE")"

    # Extract and validate before replacing the existing working installation.
    mkdir -p "$tmp/validate"
    if archive_has_unsafe_paths "$FILE"; then
        die "Unsafe paths in package archive"
    fi
    unpack_archive "$FILE" "$tmp/validate" || die "Could not unpack $(basename "$FILE")"
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
    install_udev_rules || true
    if ! probe_installed_window; then
        fallback_to_tui
    fi
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

# --tui / --cli ask for the terminal front end, using whatever install just
# happened (or the existing one). This is how a desktop user can reach the same
# journeys when the window cannot open.
if [ -n "$RUN_FLAG" ] && [ -x "$LAUNCHER" ]; then
    exec "$LAUNCHER" "$RUN_FLAG" "${@}"
fi