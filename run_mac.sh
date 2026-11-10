#!/usr/bin/env bash
# ==============================================================================
# run_mac.sh — Install Innioasis Updater CE (updater-ce-neo) on macOS
# Repository: https://github.com/y1-community/updater-ce-neo
#
#   curl -fsSL https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/run_mac.sh | bash
#
# What it does:
#   1. Downloads the newest macOS package (.dmg first, then .zip) and verifies
#      the published SHA-256 when one exists.
#   2. Installs the .app into ~/Applications with a launcher in ~/.local/bin.
#      Everything is per-user; macOS needs no administrator step and no udev
#      rule for MediaTek USB access.
#   3. When no macOS package is published, lays the app out from the newest
#      release's source archive in a per-user virtualenv — the same Python the
#      .app is built from — and uses the terminal interface if the window
#      cannot open.
#
# This script also runs on Linux: it hands over to run_linux.sh. run_linux.sh
# hands over here on macOS, so one download link works on either system, and
# both scripts accept the same options.
#
# Options:
#   --file <path>   Install a local .dmg, .zip or .app
#   --dmg           Prefer the .dmg when downloading (default)
#   --zip           Prefer the .zip when downloading
#   --update        Reinstall the newest release over the current copy
#   --check         Report macOS version, Python, package managers and install
#   --repair        Install Python (and libusb) when missing, after asking
#   --yes           Do not ask before --repair installs packages
#   --dequarantine  Clear com.apple.quarantine on the installed app
#   --skip-udev     Accepted for parity with run_linux.sh; nothing to do here
#   --skip-host-check  Skip the window probe (automated tests)
#   --tui | --cli   Start the terminal interface / command line after installing
#   --uninstall     Remove the installed app and launcher
#   --no-shortcut   Accepted for parity; macOS has no desktop entry
#   -h, --help      Show this help
# ==============================================================================

set -euo pipefail

FIRST_INSTALL_REPO="y1-community/innioasis-updater"
UPDATE_REPO="y1-community/updater-ce-neo"
REPO="$FIRST_INSTALL_REPO"
FALLBACK_REPO="$UPDATE_REPO"
RELEASE_APIS=()

DISPLAY_NAME="Updater CE"
LAUNCHER_NAME="innioasis-updater"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
INSTALL_DIR="$DATA_HOME/innioasis-updater"
APPLICATIONS_DIR="$HOME/Applications"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/$LAUNCHER_NAME"
APP_RECORD="$INSTALL_DIR/app-path"
SOURCE_DIR="$INSTALL_DIR/source"

ARCH="$(uname -m)"
HOST_OS="$(uname -s 2>/dev/null || echo unknown)"

FILE=""
DOWNLOAD=1
UNINSTALL=0
FORCE_UPDATE=0
CHECK_HOST=0
REPAIR=0
ASSUME_YES=0
SKIP_PROBE=0
DEQUARANTINE=0
RUN_FLAG=""
PACKAGE_KIND="dmg"

TMP_DIR=""
DMG_MOUNT=""

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
# Host helpers shared with run_linux.sh
# ---------------------------------------------------------------------------
abspath() {
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

cleanup() {
    if [ -n "$DMG_MOUNT" ]; then
        hdiutil detach -quiet "$DMG_MOUNT" >/dev/null 2>&1 || true
        DMG_MOUNT=""
    fi
    if [ -n "$TMP_DIR" ]; then
        rm -rf "$TMP_DIR"
    fi
}
trap cleanup EXIT HUP INT TERM

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
    log "This is not ${HOST_OS}; fetching the ${want} installer that matches it."
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
            shift 2
            ;;
        --dmg)
            PACKAGE_KIND="dmg"
            shift
            ;;
        --zip)
            PACKAGE_KIND="zip"
            shift
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
        --dequarantine)
            DEQUARANTINE=1
            shift
            ;;
        --skip-host-check)
            SKIP_PROBE=1
            shift
            ;;
        --skip-udev|--no-shortcut)
            # Accepted so the Linux options can be repeated verbatim here.
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
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1 (see --help)"
            ;;
    esac
done

case "${INNIOASIS_OPSYS:-auto}" in
    darwin) ;;
    linux)
        handoff_other_os linux
        ;;
    auto)
        if [ "$HOST_OS" != "Darwin" ]; then
            handoff_other_os linux
        fi
        ;;
    *)
        die "INNIOASIS_OPSYS must be linux or darwin (got: ${INNIOASIS_OPSYS})"
        ;;
esac

# A first install comes from the website repository; an update from the app
# repository. Both publish the same macOS files.
if [ -n "$(cat "$APP_RECORD" 2>/dev/null || true)" ] || [ -x "$LAUNCHER" ] || [ -d "$SOURCE_DIR" ]; then
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

# ---------------------------------------------------------------------------
# Package managers: Homebrew, MacPorts, and the Apple command line tools
# ---------------------------------------------------------------------------
find_brew() {
    local candidate
    if command -v brew >/dev/null 2>&1; then
        command -v brew
        return 0
    fi
    for candidate in "$HOME/.homebrew/bin/brew" /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [ -x "$candidate" ]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

usable_python() {
    # The Apple-shipped /usr/bin/python3 is a stub that opens the Command Line
    # Tools installer when they are absent, so it is only tried once
    # xcode-select confirms the tools exist.
    local candidate
    for candidate in \
        python3 \
        /opt/homebrew/bin/python3 /usr/local/bin/python3 \
        /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 \
        /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 \
        /opt/local/bin/python3.13 /opt/local/bin/python3.12 /opt/local/bin/python3.11; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        if "$candidate" -c 'import sys; raise SystemExit(0 if (3, 9) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null; then
            command -v "$candidate" 2>/dev/null || printf '%s\n' "$candidate"
            return 0
        fi
    done
    if [ -x /usr/bin/python3 ] && xcode-select -p >/dev/null 2>&1 &&
       /usr/bin/python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 9) else 1)' 2>/dev/null; then
        printf '/usr/bin/python3\n'
        return 0
    fi
    return 1
}

detect_package_manager() {
    PM_KIND="none"
    PM_LABEL="none"
    PM_CMDS=""
    PM_PRIV=1
    PM_NOTE=""
    if BREW_BIN="$(find_brew)"; then
        PM_KIND="brew"
        PM_LABEL="Homebrew ($BREW_BIN)"
        PM_CMDS="$BREW_BIN install python libusb"
        PM_PRIV=0
        PM_NOTE="Homebrew installs into your own account, so no password is needed."
    elif command -v port >/dev/null 2>&1; then
        PM_KIND="port"
        PM_LABEL="MacPorts"
        PM_CMDS="port install python312 libusb"
        PM_PRIV=1
    else
        PM_NOTE="Install Homebrew (https://brew.sh) or MacPorts (https://www.macports.org), or run 'xcode-select --install' for Apple's Python 3."
    fi
}

macos_version() {
    sw_vers -productVersion 2>/dev/null || echo "unknown"
}

# ---------------------------------------------------------------------------
# Release lookup and download
# ---------------------------------------------------------------------------
pick_asset() {
    local json="$1" kind="$2" urls
    urls="$(printf '%s' "$json" \
        | grep -o '"browser_download_url"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | sed 's/.*"\(https[^"]*\)"/\1/' \
        | grep -Ev '\.sha256$' \
        | grep -Ei '\.dmg$|/(Innioasis[._ -]?Updater|UpdaterCE)[^/]*(mac|osx)[^/]*\.zip$' || true)"
    [ -n "$urls" ] || return 1
    case "$kind" in
        dmg) printf '%s\n' "$urls" | grep -Ei '\.dmg$' | head -n 1 ;;
        *) printf '%s\n' "$urls" | grep -Ei '\.zip$' | head -n 1 ;;
    esac
}

release_json() {
    local api="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$api" 2>/dev/null
    else
        wget -qO- "$api" 2>/dev/null
    fi
}

fetch_url() {
    local url="$1" dest="$2"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --progress-bar -o "$dest" "$url"
    else
        wget -q --show-progress -O "$dest" "$url"
    fi
}

verify_download() {
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

download_mac_package() {
    local tmp="$1" api json url name attempt
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "curl or wget is required to download $DISPLAY_NAME"

    log "Looking up the newest $DISPLAY_NAME package for macOS ($ARCH)"
    for api in "${RELEASE_APIS[@]}"; do
        json="$(release_json "$api" || true)"
        [ -n "$json" ] || continue
        for attempt in "$PACKAGE_KIND" dmg zip; do
            url="$(pick_asset "$json" "$attempt" || true)"
            [ -n "$url" ] || continue
            name="$(basename "$url")"
            log "Downloading $name"
            fetch_url "$url" "$tmp/$name" || die "Download failed: $url"
            verify_download "$tmp/$name" "$url.sha256"
            FILE="$tmp/$name"
            return 0
        done
    done
    return 1
}

# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------
remove_old_apps() {
    local app name
    for app in "$APPLICATIONS_DIR"/*.app; do
        [ -d "$app" ] || continue
        name="$(basename "$app")"
        case "$name" in
            Innioasis*|"Updater CE.app"|UpdaterCE*.app)
                rm -rf -- "$app"
                ;;
        esac
    done
}

install_bundle() {
    local src_app="$1" name dest staging
    name="$(basename "$src_app")"
    dest="$APPLICATIONS_DIR/$name"
    staging="$dest.new.$$"
    mkdir -p "$APPLICATIONS_DIR" "$INSTALL_DIR"
    rm -rf "$staging"
    ditto "$src_app" "$staging" || die "Could not copy $name into $APPLICATIONS_DIR"
    remove_old_apps
    rm -rf "$dest"
    mv "$staging" "$dest"
    printf '%s\n' "$dest" > "$APP_RECORD"

    local exe_name
    exe_name="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$dest/Contents/Info.plist" 2>/dev/null || true)"
    [ -n "$exe_name" ] || exe_name="$(basename "$dest" .app)"

    write_app_launcher "$dest/Contents/MacOS/$exe_name"
    success "$DISPLAY_NAME installed at $dest"
}

write_app_launcher() {
    local target="$1"
    [ -x "$target" ] || die "The package does not contain an executable at $target"
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches $DISPLAY_NAME (installed by run_mac.sh).
exec "$target" "\$@"
EOF
    chmod 0755 "$LAUNCHER"
}

install_from_archive() {
    local src="$1" tmp="$2" src_app
    case "$src" in
        *.dmg)
            log "Mounting $(basename "$src")"
            mkdir -p "$tmp/mnt"
            hdiutil attach -nobrowse -readonly -quiet -mountpoint "$tmp/mnt" "$src" ||
                die "Could not mount $(basename "$src")"
            DMG_MOUNT="$tmp/mnt"
            src_app="$(find "$tmp/mnt" -maxdepth 1 -name '*.app' -print -quit)"
            [ -n "$src_app" ] || die "The disk image does not contain an .app bundle"
            install_bundle "$src_app"
            hdiutil detach -quiet "$DMG_MOUNT" >/dev/null 2>&1 || true
            DMG_MOUNT=""
            ;;
        *.zip)
            log "Unpacking $(basename "$src")"
            mkdir -p "$tmp/unpack"
            ditto -x -k "$src" "$tmp/unpack" || die "Could not unpack $(basename "$src")"
            src_app="$(find "$tmp/unpack" -maxdepth 3 -name '*.app' -print -quit)"
            [ -n "$src_app" ] || die "The archive does not contain an .app bundle"
            install_bundle "$src_app"
            ;;
        *.app)
            install_bundle "$src"
            ;;
        *)
            die "Unsupported package: expected a .dmg, .zip or .app"
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Host check, repair and the window probe
# ---------------------------------------------------------------------------
check_host() {
    local py brew port installed
    py="$(usable_python || true)"
    brew="$(find_brew || true)"
    port="$(command -v port 2>/dev/null || true)"
    installed="$(cat "$APP_RECORD" 2>/dev/null || true)"

    printf 'Updater CE — macOS check\n'
    printf '  system      macOS %s (%s)\n' "$(macos_version)" "$ARCH"
    if [ -n "$py" ]; then
        printf '  python      %s (%s)\n' "$py" "$("$py" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo '?')"
    else
        printf '  python      MISSING (needed only for the source fallback)\n'
    fi
    if [ -n "$brew" ]; then
        printf '  brew        %s\n' "$brew"
    else
        printf '  brew        not installed\n'
    fi
    if [ -n "$port" ]; then
        printf '  ports       %s\n' "$port"
    else
        printf '  ports       not installed\n'
    fi
    if [ -n "$installed" ] && [ -d "$installed" ]; then
        printf '  installed   %s\n' "$installed"
    elif [ -d "$SOURCE_DIR" ]; then
        printf '  installed   %s (from source)\n' "$SOURCE_DIR"
    else
        printf '  installed   no\n'
    fi
    if [ -x "$LAUNCHER" ]; then
        printf '  launcher    %s\n' "$LAUNCHER"
    fi

    # The .app carries its own Python, Qt and libusb, so a normal install
    # needs nothing from the host. Only the source fallback needs Python.
    if [ -n "$installed" ] && [ -d "$installed" ]; then
        success "The installed app carries its own runtime; no host packages are required"
        return 0
    fi
    if [ -n "$py" ]; then
        success "Python is available for the source fallback"
        return 0
    fi
    warn "Python 3.9–3.13 is missing; only the prebuilt .app can be installed until it is present."
    detect_package_manager
    if [ -n "$PM_CMDS" ]; then
        printf '  Run: %s\n' "$PM_CMDS"
    fi
    [ -n "$PM_NOTE" ] && printf '  %s\n' "$PM_NOTE"
    return 1
}

run_privileged() {
    local cmd="$1"
    if [ "$PM_PRIV" -eq 0 ] || [ "$(id -u)" -eq 0 ]; then
        sh -c "$cmd"
    elif command -v sudo >/dev/null 2>&1; then
        sudo sh -c "$cmd"
    else
        return 127
    fi
}

repair_host() {
    local py answer
    py="$(usable_python || true)"
    # The .app carries its own Python, Qt and libusb, so it needs no host
    # package. Python is only the source fallback's requirement.
    if [ -n "$py" ]; then
        success "Python $("$py" -c 'import platform; print(platform.python_version())' 2>/dev/null || echo '?') is present"
        return 0
    fi
    detect_package_manager
    warn "Python 3.9–3.13 is not available"
    [ -n "$PM_CMDS" ] && printf '  Command: %s\n' "$PM_CMDS"
    [ -n "$PM_NOTE" ] && printf '  %s\n' "$PM_NOTE"
    [ -n "$PM_CMDS" ] || return 1

    if [ "$ASSUME_YES" -ne 1 ]; then
        printf 'Install Python now? [Y/n] '
        if [ -r /dev/tty ]; then
            read -r answer < /dev/tty || answer="Y"
        else
            read -r answer || answer="Y"
        fi
        case "$answer" in
            n|N|no|NO) return 1 ;;
        esac
    fi
    run_privileged "$PM_CMDS" || { warn "The package manager did not finish; install Python manually."; return 1; }
    success "Python installed. The source fallback can now run."
    return 0
}

probe_installed_window() {
    [ "$SKIP_PROBE" -eq 0 ] || return 0
    [ -n "$RUN_FLAG" ] && return 0
    [ -x "$LAUNCHER" ] || return 0
    [ -n "${SSH_TTY:-}" ] && return 0
    command -v sw_vers >/dev/null 2>&1 || return 0

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
    warn "The terminal interface will be used instead."
    printf '  Full diagnostics: %s\n' "$log_file"
    return 1
}

fallback_to_tui() {
    printf '\n  Start the terminal interface with: %s --tui\n' "$LAUNCHER"
    if [ -r /dev/tty ] && [ -x "$LAUNCHER" ]; then
        exec "$LAUNCHER" --tui
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Source fallback: newest release archive, per-user virtualenv
# ---------------------------------------------------------------------------
pick_source_zip() {
    local json="$1" py
    py="$(usable_python || true)"
    if [ -n "$py" ]; then
        printf '%s' "$json" | "$py" -c '
import json, sys
try:
    releases = json.load(sys.stdin)
except Exception:
    sys.exit(1)
if isinstance(releases, dict):
    releases = [releases]
for release in releases:
    if release.get("draft") or release.get("prerelease"):
        continue
    url = release.get("zipball_url") or ""
    if url:
        print(url)
        sys.exit(0)
sys.exit(1)
'
        return $?
    fi
    printf '%s' "$json" \
        | grep -o '"zipball_url"[[:space:]]*:[[:space:]]*"[^"]*"' \
        | sed 's/.*"\(https[^"]*\)"/\1/' \
        | head -n 1
}

install_from_source() {
    local py tmp json url api top entry venv reqs
    py="$(usable_python || true)"
    if [ -z "$py" ]; then
        warn "No usable Python 3 was found, so the source fallback cannot run yet."
        detect_package_manager
        [ -n "$PM_CMDS" ] && printf '  Install it with: %s\n' "$PM_CMDS"
        [ -n "$PM_NOTE" ] && printf '  %s\n' "$PM_NOTE"
        return 1
    fi
    command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 ||
        die "curl or wget is required to download the source archive"

    [ -n "$TMP_DIR" ] || TMP_DIR="$(mktemp -d)"
    tmp="$TMP_DIR"
    log "No macOS package is published for this release; using the source archive."
    url=""
    for api in "${RELEASE_APIS[@]}"; do
        json="$(release_json "$api" || true)"
        [ -n "$json" ] || continue
        url="$(pick_source_zip "$json" || true)"
        [ -n "$url" ] && break
    done
    [ -n "$url" ] || die "Could not find a source archive for the app"

    log "Downloading the source archive"
    fetch_url "$url" "$tmp/source.zip" || die "Source download failed"
    mkdir -p "$tmp/src"
    ditto -x -k "$tmp/source.zip" "$tmp/src" || die "Could not unpack the source archive"

    top="$(find "$tmp/src" -mindepth 1 -maxdepth 1 -type d -print -quit)"
    [ -n "$top" ] || die "The source archive is empty"
    [ -f "$top/launcher.py" ] || die "That source archive is not the Updater app"

    mkdir -p "$INSTALL_DIR"
    rm -rf "$SOURCE_DIR"
    mv "$top" "$SOURCE_DIR"
    chmod 0755 "$SOURCE_DIR" 2>/dev/null || true

    venv="$SOURCE_DIR/venv"
    if [ ! -x "$venv/bin/python" ]; then
        "$py" -m venv "$venv" ||
            "$py" -m venv --system-site-packages "$venv" ||
            die "python3 -m venv failed. Install Python with Homebrew or MacPorts and run this again."
    fi
    reqs="$SOURCE_DIR/requirements.txt"
    if [ -f "$reqs" ]; then
        "$venv/bin/python" -m pip install --upgrade pip --quiet --disable-pip-version-check || true
        if ! "$venv/bin/python" -m pip install -r "$reqs" --quiet --disable-pip-version-check; then
            warn "The Qt front end could not be installed (often a Python with no matching PySide6 wheel)."
            warn "Installing the non-Qt requirements so the terminal interface still works."
            grep -viE '^(PySide6|shiboken6)' "$reqs" > "$tmp/requirements-tui.txt" || true
            "$venv/bin/python" -m pip install -r "$tmp/requirements-tui.txt" --quiet --disable-pip-version-check ||
                die "Could not install the app's Python requirements."
        fi
    fi

    entry="launcher.py"
    mkdir -p "$BIN_DIR"
    cat > "$LAUNCHER" <<EOF
#!/bin/sh
# Launches $DISPLAY_NAME from its source checkout (installed by run_mac.sh).
cd "$SOURCE_DIR" || exit 1
exec "$SOURCE_DIR/venv/bin/python" "$entry" "\$@"
EOF
    chmod 0755 "$LAUNCHER"
    : > "$APP_RECORD"
    success "$DISPLAY_NAME is installed from source in $SOURCE_DIR"
    success "Launch: $LAUNCHER"
}

uninstall() {
    local app
    log "Uninstalling $DISPLAY_NAME"
    app="$(cat "$APP_RECORD" 2>/dev/null || true)"
    if [ -n "$app" ] && [ -d "$app" ]; then
        rm -rf -- "$app"
        success "Removed $app"
    fi
    remove_old_apps
    rm -f "$LAUNCHER"
    rm -rf "$INSTALL_DIR"
    success "$DISPLAY_NAME removed. Settings in ~/Library and ~/.config are kept."
}

main() {
    if [ "$UNINSTALL" -eq 1 ]; then
        uninstall
        return 0
    fi
    if [ "$CHECK_HOST" -eq 1 ]; then
        if check_host; then
            exit 0
        fi
        exit 1
    fi
    if [ "$REPAIR" -eq 1 ]; then
        if repair_host; then
            exit 0
        fi
        printf '  %s --tui still installs firmware without the window.\n' "$DISPLAY_NAME"
        exit 1
    fi

    if [ "$DOWNLOAD" -eq 1 ]; then
        TMP_DIR="$(mktemp -d)"
        download_mac_package "$TMP_DIR" || {
            warn "No macOS .dmg or .zip was found in the newest releases."
            if install_from_source; then
                :
            else
                printf '  Install Python, then run: %s --tui\n' "$LAUNCHER"
                return 1
            fi
        }
        if [ -n "$FILE" ]; then
            FILE="$(abspath "$FILE")"
            install_from_archive "$FILE" "$TMP_DIR"
        fi
    else
        [ -n "$FILE" ] || die "No package to install"
        [ -f "$FILE" ] || [ -d "$FILE" ] || die "No such file: $FILE"
        FILE="$(abspath "$FILE")"
        TMP_DIR="$(mktemp -d)"
        install_from_archive "$FILE" "$TMP_DIR"
    fi

    if [ "$DEQUARANTINE" -eq 1 ]; then
        xattr -dr com.apple.quarantine "$(cat "$APP_RECORD" 2>/dev/null || true)" 2>/dev/null ||
            warn "Could not clear com.apple.quarantine"
    elif xattr -p com.apple.quarantine "$(cat "$APP_RECORD" 2>/dev/null || true)" >/dev/null 2>&1; then
        warn "This copy came from a browser download, so macOS quarantined it."
        warn "If it refuses to open, run: xattr -dr com.apple.quarantine \"$(cat "$APP_RECORD")\""
    fi

    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *) warn "$BIN_DIR is not on your PATH; launch from ~/Applications or run $LAUNCHER" ;;
    esac

    if ! probe_installed_window; then
        fallback_to_tui
    fi
    printf '\n  Launch: %s\n  Update: %s --update\n\n' "$LAUNCHER" "$LAUNCHER"
    return 0
}

main "$@"

if [ -n "$RUN_FLAG" ] && [ -x "$LAUNCHER" ]; then
    exec "$LAUNCHER" "$RUN_FLAG"
fi
