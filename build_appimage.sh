#!/usr/bin/env bash
# ==============================================================================
# build_appimage.sh — Build Innioasis Updater CE Neo as a standalone Linux AppImage
# Repository: https://github.com/y1-community/updater-ce-neo
#
# Usage:
#   chmod +x build_appimage.sh
#   ./build_appimage.sh                    # Builds AppImage using InnioasisUpdater.spec
#   ./build_appimage.sh --no-pyinstaller   # Packages existing dist/InnioasisUpdater
#   ./build_appimage.sh --clean            # Clean previous builds and rebuild
#
# Requirements:
#   - Python 3.10+ (a fresh clone gets .venv-build + requirements installed
#     automatically by scripts/bootstrap_build_env.py)
#   - curl, file, desktop-file-utils (appimagetool is auto-bootstrapped if missing)
# ==============================================================================

set -euo pipefail
cd "$(dirname "$0")"

export BUILD_BRAND="${BUILD_BRAND:-}"
VERSION=$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' src/config.py 2>/dev/null || echo "2.5.3")
AUTHOR="Ryan Specter"
# Default to the host architecture; appimagetool and the release names use aarch64, not arm64.
case "$(uname -m)" in
    aarch64|arm64) HOST_ARCH="aarch64" ;;
    *) HOST_ARCH="$(uname -m)" ;;
esac
ARCH="${ARCH:-$HOST_ARCH}"

DIST_DIR="${DIST_DIR:-dist/linux}"
BUILD_DIR="build/linux"
APPDIR="$BUILD_DIR/AppDir"
SPEC_FILE="InnioasisUpdater.spec"

CLEAN_BUILD=0
SKIP_PYINSTALLER=0
BRAND_REQUESTED=""
SPEC_FILE="InnioasisUpdater.spec"
# The frozen bundle inherits the build host's glibc and Python. Freezing on a
# rolling distro (glibc 2.44, Python 3.14) produced AppImages that every
# Debian, Ubuntu, Mint and openSUSE user saw fail with "GLIBC_2.44 not found"
# before a window appeared. The container is therefore the default: a host
# build has to be asked for explicitly and is reported as unsupported.
USE_CONTAINER="${USE_CONTAINER:-1}"

FORWARD_ARGS=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN_BUILD=1
            FORWARD_ARGS+=("$1")
            shift
            ;;
        --no-pyinstaller)
            SKIP_PYINSTALLER=1
            FORWARD_ARGS+=("$1")
            shift
            ;;
        --container)
            USE_CONTAINER="1"
            FORWARD_ARGS+=("$1")
            shift
            ;;
        --no-container)
            USE_CONTAINER="0"
            FORWARD_ARGS+=("$1")
            shift
            ;;
        --arch)
            ARCH="$2"
            FORWARD_ARGS+=("$1" "$2")
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  --clean           Remove previous build and dist artifacts before building"
            echo "  --no-pyinstaller  Skip PyInstaller build and package existing dist/InnioasisUpdater"
            echo "  --container       Build PyInstaller payload inside Debian Bookworm container (ensures x86-64-baseline)"
            echo "  --no-container    Force building PyInstaller directly on the host system"
            echo "  --arch <arch>     Target architecture (default: x86_64)"
            echo "  --brand <name>    Build this brand: updater_ce (default), mediatek_installer,"
            echo "                    or all for both front ends"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
        --brand)
            BRAND_REQUESTED="$(printf '%s' "$2" | tr '[:upper:]-' '[:lower:]_')"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

export ARCH

# Allow packaging output to be redirected outside the repository (release staging).
if [[ "$DIST_DIR" = /* ]]; then
    DIST_DIR="$(realpath -m "$DIST_DIR")"
else
    DIST_DIR="$(realpath -m "$DIST_DIR")"
fi

# Reject a bad brand before anything is deleted: a typo must not wipe good
# AppImages sitting in dist/.
for _candidate in "${BRAND_REQUESTED:-}" "${BUILD_BRAND:-}"; do
    case "$_candidate" in
        ""|updater_ce|mediatek_installer|all|both) ;;
        *)
            echo "ERROR: unknown brand '$_candidate' (expected updater_ce, mediatek_installer or all)"
            exit 1
            ;;
    esac
done

# --- Brand plan ---------------------------------------------------------------
# A build produces Updater CE. The generic MediaTek Installer (same internals,
# different identity and front end) is opt-in, because building it doubles a
# release run for a product that may never ship. Each brand is built by a child
# run of this script, so the single-brand path stays identical to what it
# always was.
if [ -z "${INNOASIS_BRAND_LOCK:-}" ]; then
    case "${BRAND_REQUESTED:-${BUILD_BRAND:-}}" in
        "") brands=(updater_ce) ;;
        all|both) brands=(updater_ce mediatek_installer) ;;
        *) brands=("${BRAND_REQUESTED:-${BUILD_BRAND:-}}") ;;
    esac

    if [ "$CLEAN_BUILD" -eq 1 ]; then
        rm -rf "$BUILD_DIR"
        # Each AppImage is named after its brand: a clean build replaces the
        # ones it is about to rebuild and leaves the other app in dist/.
        for brand in "${brands[@]}"; do
            case "$brand" in
                updater_ce) rm -rf "$DIST_DIR"/UpdaterCE-*.AppImage* ;;
                mediatek_installer) rm -rf "$DIST_DIR"/MediaTekInstaller-*.AppImage* ;;
            esac
        done
    fi

    for brand in "${brands[@]}"; do
        INNOASIS_BRAND_LOCK=1 BUILD_BRAND="$brand" "$0" ${FORWARD_ARGS[@]+"${FORWARD_ARGS[@]}"} || exit $?
    done
    echo ""
    echo "=== dist/ ==="
    ls "$DIST_DIR"/*.AppImage 2>/dev/null || echo "  (no AppImages yet)"
    exit 0
fi

BRAND="$(printf '%s' "${BUILD_BRAND:-updater_ce}" | tr '[:upper:]-' '[:lower:]_')"
APP_NAME="InnioasisUpdater"
DISPLAY_NAME="Updater CE"
APP_ID="io.github.y1_community.InnioasisUpdater"
BRAND_SLUG="InnioasisUpdater"
case "$BRAND" in
    updater_ce) ;;
    mediatek_installer)
        # The dist folder / executable stay slugs (as Updater CE's does): the
        # display name is the job of the desktop entry.
        APP_NAME="MediaTekInstaller"
        DISPLAY_NAME="MediaTek Installer"
        APP_ID="io.github.y1_community.MediaTekInstaller"
        BRAND_SLUG="MediaTekInstaller"
        ;;
    *)
        echo "ERROR: unknown BUILD_BRAND '$BRAND' (expected updater_ce, mediatek_installer or all)"
        exit 1
        ;;
esac
export BUILD_BRAND="$BRAND"

echo "======================================================================"
echo " Building $DISPLAY_NAME AppImage (v$VERSION - $ARCH)"
echo "======================================================================"

log_step() {
    local num="$1"
    local total="$2"
    local title="$3"
    local desc="${4:-}"
    echo ""
    echo "================================================================================"
    echo "  [Step ${num}/${total}] ${title}"
    if [ -n "$desc" ]; then
        echo "  ${desc}"
    fi
    echo "================================================================================"
}

TOTAL_STEPS=8

# --- Preflight Python & Dependencies ------------------------------------------
# Same bootstrap as the macOS/Windows scripts: a fresh clone has no virtualenv,
# so create .venv-build and install requirements.txt + requirements-build.txt
# rather than failing on the first missing import.
BOOTSTRAP_SCRIPT="scripts/bootstrap_build_env.py"
PYTHON="${PYTHON:-}"

log_step 1 "$TOTAL_STEPS" "Validating Environment & Dependencies" "Checking Python runtime, PySide6, PyInstaller, and build tools..."
if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    if [ ! -f "$BOOTSTRAP_SCRIPT" ]; then
        echo "ERROR: $BOOTSTRAP_SCRIPT is missing; this checkout is incomplete."
        exit 1
    fi
    if [ -z "$PYTHON" ]; then
        BOOTSTRAP_PYTHON=""
        for _cand in python3 /usr/bin/python3 python; do
            if command -v "$_cand" >/dev/null 2>&1; then
                BOOTSTRAP_PYTHON="$_cand"
                break
            fi
        done
        if [ -z "$BOOTSTRAP_PYTHON" ]; then
            echo "ERROR: no python3 on PATH to start the build environment bootstrap."
            echo "       Debian/Ubuntu: sudo apt install python3 python3-venv"
            echo "       Fedora:        sudo dnf install python3"
            exit 1
        fi
        if ! BOOTSTRAP_OUTPUT="$("$BOOTSTRAP_PYTHON" "$BOOTSTRAP_SCRIPT")"; then
            echo ""
            echo "ERROR: could not prepare the Python build environment (see above)."
            exit 1
        fi
        PYTHON="$(printf '%s\n' "$BOOTSTRAP_OUTPUT" | sed -n 's/^PYTHON=//p' | tail -1)"
        if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
            echo "ERROR: the build environment bootstrap did not report an interpreter."
            exit 1
        fi
    else
        # An explicitly supplied interpreter is verified, never modified.
        if ! "$PYTHON" "$BOOTSTRAP_SCRIPT" >/dev/null; then
            exit 1
        fi
    fi

    echo "  -> Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"
    echo "  -> Required modules verified (PySide6, PyInstaller)"
fi

# Snapshot innioasis.app's catalogue/notices/donors/blocklist into the bundle so
# a first launch works offline. Best-effort: existing copies are kept on failure.
if [ -n "$PYTHON" ] && [ -x "$PYTHON" ]; then
    "$PYTHON" scripts/fetch_online_data.py || echo "  ! Online snapshot refresh skipped (using the copies already in assets/online)"
fi

# --- Clean if requested -------------------------------------------------------
if [ "$CLEAN_BUILD" -eq 1 ]; then
    echo "  -> Cleaning build artifacts in $BUILD_DIR and $DIST_DIR/$APP_NAME..."
    rm -rf "$BUILD_DIR" "$DIST_DIR/$APP_NAME"
fi

# --- Step 2: PyInstaller build ------------------------------------------------
log_step 2 "$TOTAL_STEPS" "Compiling Standalone Binary with PyInstaller" "Freezing Python code, Qt libraries, and application resources using $SPEC_FILE..."
mkdir -p "$DIST_DIR" "$BUILD_DIR"

if [ -n "$PYTHON" ] && [ -x "$PYTHON" ]; then
    "$PYTHON" scripts/set_build_brand.py "$BRAND"
    trap 'rm -f src/_build_brand.py' EXIT
fi

# The container is the only supported shape of a release build: it pins the
# glibc floor (Ubuntu 22.04, 2.35) and the interpreter, and it keeps
# x86-64-v3/v4 host packages out of the bundle. --no-container stays available
# for a local experiment, but says so and still runs the floor check.
SHOULD_USE_CONTAINER=0
if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    if [ "$USE_CONTAINER" = "1" ]; then
        if ! command -v docker >/dev/null 2>&1; then
            echo "ERROR: release builds must run in Dockerfile.linux_build (glibc 2.35 baseline)."
            echo "       Install Docker (or colima on macOS) and run ./build_appimage_docker.sh."
            echo "       For a throwaway local build only, pass --no-container."
            exit 1
        fi
        SHOULD_USE_CONTAINER=1
    else
        echo "  ! --no-container: the bundle inherits this host's glibc and Python."
        echo "  ! Not suitable for a release; run ./build_appimage_docker.sh instead."
    fi
fi

if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    if [ "$SHOULD_USE_CONTAINER" -eq 1 ]; then
        ARCH_CONTAINER="linux/amd64"
        if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then
            ARCH_CONTAINER="linux/arm64"
        fi
        IMAGE_NAME="updater-linux-builder-${ARCH}"
        echo "  -> Building standalone binary in ${IMAGE_NAME} container (${ARCH_CONTAINER})..."
        if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
            echo "  -> Creating ${IMAGE_NAME} container image..."
            docker build --platform "$ARCH_CONTAINER" -t "$IMAGE_NAME" -f Dockerfile.linux_build .
        fi

        mkdir -p "$DIST_DIR" "$BUILD_DIR"
        docker run --rm \
            --platform "$ARCH_CONTAINER" \
            -v "$(pwd):/workspace" \
            -v "$DIST_DIR:$DIST_DIR" \
            "$IMAGE_NAME" -c "
set -e
if [ -d /opt/venv ]; then
    VENV_CMD=\"/opt/venv/bin\"
    # A cached builder image may predate the requirements pins (it shipped
    # PySide6 6.12.0, which crashes on this Python 3.11): re-sync every run.
    /opt/venv/bin/pip install -q -r requirements.txt pyinstaller
else
    python3 -m venv /tmp/venv
    /tmp/venv/bin/pip install -q --upgrade pip
    /tmp/venv/bin/pip install -q -r requirements.txt pyinstaller
    VENV_CMD=\"/tmp/venv/bin\"
fi
\$VENV_CMD/python3 scripts/check_pyside_refcount.py
\$VENV_CMD/python3 scripts/set_build_brand.py '$BRAND'
\$VENV_CMD/pyinstaller --clean --noconfirm --distpath '$DIST_DIR' --workpath '$BUILD_DIR' '$SPEC_FILE'
\$VENV_CMD/python3 scripts/verify_linux_runtime.py --dist '$DIST_DIR/$APP_NAME' --max-glibc 2.35
cp /usr/lib/*-linux-gnu/libusb-1.0.so* '$DIST_DIR/$APP_NAME/_internal/' 2>/dev/null || true
chown -R $(id -u):$(id -g) '$DIST_DIR' '$BUILD_DIR' .cache 2>/dev/null || true
rm -f src/_build_brand.py
"
        echo "  -> Container PyInstaller compilation completed."
    else
        echo "  -> Running PyInstaller on host..."
        if ! "$PYTHON" scripts/check_pyside_refcount.py; then
            exit 1
        fi
        "$PYTHON" -m PyInstaller \
            --clean \
            --noconfirm \
            --distpath "$DIST_DIR" \
            --workpath "$BUILD_DIR" \
            "$SPEC_FILE"
        echo "  -> PyInstaller compilation completed."
        if command -v objdump >/dev/null 2>&1 || command -v readelf >/dev/null 2>&1; then
            "$PYTHON" scripts/verify_linux_runtime.py --dist "$DIST_DIR/$APP_NAME" --max-glibc 2.35 \
                || echo "  ! This bundle is above the glibc 2.35 release floor; do not ship it."
        else
            echo "  -> Note: no readelf/objdump here, so the glibc floor was not verified."
        fi
    fi
fi

if [ ! -d "$DIST_DIR/$APP_NAME" ] || [ ! -f "$DIST_DIR/$APP_NAME/$APP_NAME" ]; then
    echo "ERROR: PyInstaller build directory not found at $DIST_DIR/$APP_NAME/$APP_NAME"
    exit 1
fi

# MTKClient is the default Linux flash engine, so its libusb must be a real
# file inside every artifact. A copy of a build-host symlink would be dangling
# on the user's machine, and the fork asks pyusb for the unversioned soname
# that distributions do not ship. Normalise both names here, before the
# AppImage and tarball are assembled.
stage_libusb_payload() {
    _internal="$DIST_DIR/$APP_NAME/_internal"
    [ -d "$_internal" ] || return 0
    _dest="$_internal/libusb-1.0.so.0"

    if [ -e "$_dest" ]; then
        # Dereference whatever PyInstaller or the container left behind.
        cp -L "$_dest" "$_dest.tmp"
        mv -f "$_dest.tmp" "$_dest"
    else
        for _cand in /usr/lib/*-linux-gnu/libusb-1.0.so.0 /usr/lib64/libusb-1.0.so.0 /usr/lib/libusb-1.0.so.0; do
            if [ -f "$_cand" ]; then
                cp -L "$_cand" "$_dest"
                break
            fi
        done
    fi

    if [ ! -s "$_dest" ]; then
        echo "ERROR: libusb-1.0.so.0 is required for MTKClient (the default Linux flash engine) but was not found."
        echo "       Install the libusb runtime package (libusb-1.0-0 on Debian/Ubuntu) and rebuild."
        exit 1
    fi

    chmod 0644 "$_dest"
    ln -sfn libusb-1.0.so.0 "$_internal/libusb-1.0.so"
    echo "  -> Bundled libusb for MTKClient: $_dest"
}
stage_libusb_payload

# The Rockbox 360p themes archive is ~62 MB and is now fetched on demand and
# refreshed weekly by the app; only the small fonts pack ships. Prune a dist/
# produced before this rule too (the --no-pyinstaller path packages an existing
# build as-is).
rm -f "$DIST_DIR/$APP_NAME/assets/360p/themes-themepack.zip"
rm -rf "$DIST_DIR/$APP_NAME/assets/360p/themes-themepack"

# --- Step 2b: Bundle Qt's XCB client dependencies -----------------------------
# Qt 6.5+ loads libqxcb.so only when libxcb-cursor0 and its helper libraries
# are present. Minimal desktops and some derivatives do not install them, and
# Qt then aborts with "Could not load the Qt platform plugin xcb" before a
# window exists. The build host (the Debian container or the CI runner) has
# them; ship the client-side copies beside the other bundled libraries. The
# core libxcb/X11 libraries stay the host's, since they must match the running
# X server.
bundle_xcb_client_libs() {
    _internal="$DIST_DIR/$APP_NAME/_internal"
    [ -d "$_internal" ] || return 0
    for _so in \
        libxcb-cursor.so.0 \
        libxcb-image.so.0 \
        libxcb-render-util.so.0 \
        libxcb-util.so.1 \
        libxkbcommon-x11.so.0 \
        libxkbcommon.so.0; do
        _src=""
        for _libdir in /usr/lib/*-linux-gnu /usr/lib64 /usr/lib; do
            if [ -e "$_libdir/$_so" ]; then
                _src="$_libdir/$_so"
                break
            fi
        done
        if [ -z "$_src" ]; then
            echo "  -> Note: $_so is not installed on this build host; the target distro must provide it."
        elif [ ! -e "$_internal/$_so" ]; then
            cp -L "$_src" "$_internal/$_so"
            echo "  -> Bundled Qt XCB dependency: $_so"
        fi
    done

    # Qt 6.5+ cannot load libqxcb.so without libxcb-cursor0. Shipping a bundle
    # that merely warns about it is how 2.5.1-2.5.4 reached Debian and Mint
    # users with "could not load the Qt platform plugin xcb".
    if [ ! -e "$_internal/libxcb-cursor.so.0" ] && [ ! -e "$_internal/libxcb-cursor.so" ]; then
        echo "ERROR: libxcb-cursor.so.0 is missing; Qt 6.5+ cannot start without it."
        echo "       Install libxcb-cursor0 in the build container (apt) and rebuild."
        exit 1
    fi
}
bundle_xcb_client_libs

# --- Step 3: Stage the bundled SP Flash Tool Linux payload --------------------
log_step 3 "$TOTAL_STEPS" "Bundling SP Flash Tool Linux Payload" "Copying flash_tool executable, helper scripts, and libraries into package..."
SP_TOOL_SRC="tools/linux/SP_Flash_Tool_v5.1904_Linux"
SP_TOOL_DIST="$DIST_DIR/$APP_NAME/SP_Flash_Tool"
if [ "$ARCH" = "x86_64" ]; then
    if [ -f "$SP_TOOL_SRC/flash_tool" ]; then
        echo "  -> Staging bundled SP Flash Tool payload: $SP_TOOL_SRC -> $SP_TOOL_DIST"
        rm -rf "$SP_TOOL_DIST"
        cp -r "$SP_TOOL_SRC" "$SP_TOOL_DIST"
        if [ -f "$SP_TOOL_DIST/history.ini" ]; then
            printf "[RecentOpenFile]\nscatterHistory=@Invalid()\nauthHistory=@Invalid()\n" > "$SP_TOOL_DIST/history.ini"
        fi
        rm -rf "$SP_TOOL_DIST/SP_FT_Logs"
    fi
    if [ -f "$SP_TOOL_DIST/flash_tool" ]; then
        chmod 0755 "$SP_TOOL_DIST/flash_tool" "$SP_TOOL_DIST/flash_tool.sh" "$SP_TOOL_DIST/run_flash_tool.sh" 2>/dev/null || true
        find "$SP_TOOL_DIST" -name "*.so*" -exec chmod a+r {} + 2>/dev/null || true
        echo "  -> SP Flash Tool payload bundled at $SP_TOOL_DIST"
    else
        echo "  -> Note: no local SP Flash Tool payload found; app will download at first run if needed."
    fi
else
    echo "  -> Note: SP Flash Tool is x86_64 only; on $ARCH MTKClient serves as the primary native flasher engine."
fi

# --- Step 3b: Stage ADB binary ------------------------------------------------
ADB_BIN=""
case "$ARCH" in
    x86_64)
        if [ -f "tools/adb/linux-x64/adb" ]; then
            ADB_BIN="tools/adb/linux-x64/adb"
        fi
        ;;
    aarch64|arm64)
        if [ -f "tools/adb/linux-arm64/adb" ]; then
            ADB_BIN="tools/adb/linux-arm64/adb"
        fi
        ;;
esac

if [ -n "$ADB_BIN" ] && [ -f "$ADB_BIN" ]; then
    chmod 0755 "$ADB_BIN"
    # The spec already stages tools/adb/<platform>/adb. Add the flat copy only
    # for a --no-pyinstaller dist that predates that layout, and never a second
    # root copy: the resolver checks the per-platform path first.
    mkdir -p "$DIST_DIR/$APP_NAME/tools/adb"
    if [ ! -f "$DIST_DIR/$APP_NAME/tools/adb/adb" ]; then
        cp "$ADB_BIN" "$DIST_DIR/$APP_NAME/tools/adb/adb"
    fi
    echo "  -> ADB ($ARCH) staged under tools/adb"
fi

# Stage portable unrar / unar for RAR unpacking compatibility across Linux architectures
UNRAR_BIN=""
case "$ARCH" in
    x86_64)
        if [ -f "tools/linux/x86_64/unrar" ]; then
            UNRAR_BIN="tools/linux/x86_64/unrar"
        elif [ -f "tools/linux/unrar" ]; then
            UNRAR_BIN="tools/linux/unrar"
        fi
        ;;
    aarch64|arm64)
        if [ -f "tools/linux/aarch64/unrar" ]; then
            UNRAR_BIN="tools/linux/aarch64/unrar"
        elif [ -f "tools/linux/arm64/unrar" ]; then
            UNRAR_BIN="tools/linux/arm64/unrar"
        fi
        ;;
    armhf|armv7l|arm*)
        if [ -f "tools/linux/armhf/unrar" ]; then
            UNRAR_BIN="tools/linux/armhf/unrar"
        fi
        ;;
esac
if [ -n "$UNRAR_BIN" ] && [ -f "$UNRAR_BIN" ]; then
    mkdir -p "$DIST_DIR/$APP_NAME/tools"
    cp "$UNRAR_BIN" "$DIST_DIR/$APP_NAME/tools/unrar"
    chmod 0755 "$DIST_DIR/$APP_NAME/tools/unrar"
    echo "  -> Bundled unrar ($ARCH) at $DIST_DIR/$APP_NAME/tools/unrar"
fi
if [ -f "tools/linux/unar" ]; then
    mkdir -p "$DIST_DIR/$APP_NAME/tools"
    cp "tools/linux/unar" "$DIST_DIR/$APP_NAME/tools/unar"
    chmod 0755 "$DIST_DIR/$APP_NAME/tools/unar"
    echo "  -> Bundled unar at $DIST_DIR/$APP_NAME/tools/unar"
fi

# --- Step 4: Prepare AppDir structure -----------------------------------------
log_step 4 "$TOTAL_STEPS" "Assembling AppDir Directory Hierarchy" "Setting up standardized Linux desktop integration (desktop entry, icons, AppRun)..."
echo "  -> Populating $APPDIR..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/metainfo"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy binary payload & _internal
cp -r "$DIST_DIR/$APP_NAME"/* "$APPDIR/usr/bin/"

if [ -d "$APPDIR/usr/bin/SP_Flash_Tool" ]; then
    chmod 0755 "$APPDIR/usr/bin/SP_Flash_Tool/flash_tool" \
               "$APPDIR/usr/bin/SP_Flash_Tool/flash_tool.sh" \
               "$APPDIR/usr/bin/SP_Flash_Tool/run_flash_tool.sh" 2>/dev/null || true
    find "$APPDIR/usr/bin/SP_Flash_Tool" -name "*.so*" -exec chmod a+r {} + 2>/dev/null || true
fi

if [ -n "$ADB_BIN" ] && [ -f "$ADB_BIN" ]; then
    cp "$ADB_BIN" "$APPDIR/usr/bin/adb"
    chmod 0755 "$APPDIR/usr/bin/adb"
    echo "  -> Bundled ADB executable placed on PATH at $APPDIR/usr/bin/adb"
fi
if [ -n "$UNRAR_BIN" ] && [ -f "$UNRAR_BIN" ]; then
    cp "$UNRAR_BIN" "$APPDIR/usr/bin/unrar"
    chmod 0755 "$APPDIR/usr/bin/unrar"
    echo "  -> Bundled unrar executable placed on PATH at $APPDIR/usr/bin/unrar"
fi

# MTKClient is the default Linux engine and loads libusb from the AppImage.
# Keep a real file (not a build-host symlink) and the unversioned name the
# bundled mtkclient fork asks pyusb for.
mkdir -p "$APPDIR/usr/lib"
LIBUSB_PAYLOAD="$APPDIR/usr/bin/_internal/libusb-1.0.so.0"
if [ ! -s "$LIBUSB_PAYLOAD" ]; then
    echo "ERROR: libusb-1.0.so.0 missing from the PyInstaller payload; MTKClient cannot work."
    exit 1
fi
cp -L "$LIBUSB_PAYLOAD" "$APPDIR/usr/lib/libusb-1.0.so.0"
ln -sfn libusb-1.0.so.0 "$APPDIR/usr/lib/libusb-1.0.so"
echo "  -> AppImage libusb payload: $APPDIR/usr/lib/libusb-1.0.so.0"

# Copy / generate desktop integration
DESKTOP_SRC="assets/innioasis-updater.desktop"
if [ -f "$DESKTOP_SRC" ]; then
    cp "$DESKTOP_SRC" "$APPDIR/innioasis-updater.desktop"
    cp "$DESKTOP_SRC" "$APPDIR/usr/share/applications/innioasis-updater.desktop"
else
    cat << 'EOF' > "$APPDIR/innioasis-updater.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Updater CE
GenericName=Firmware Flasher
Comment=Flash, update, and restore Innioasis and Timmkoo digital audio players
Exec=InnioasisUpdater %U
Icon=innioasis-updater
Terminal=false
Categories=Utility;AudioVideo;
Keywords=innioasis;timmkoo;y1;y2;a5;mtk;flash;firmware;rockbox;solar;
StartupWMClass=Updater CE
X-AppImage-Author=Ryan Specter
EOF
    cp "$APPDIR/innioasis-updater.desktop" "$APPDIR/usr/share/applications/innioasis-updater.desktop"
fi

# The MediaTek Installer is the same build with a different identity: give it
# the name the user sees and the slugged executable it actually launches.
if [ "$BRAND" = "mediatek_installer" ]; then
    for entry in "$APPDIR/innioasis-updater.desktop" \
                 "$APPDIR/usr/share/applications/innioasis-updater.desktop"; do
        sed -i \
            -e "s/^Name=Updater CE/Name=$DISPLAY_NAME/" \
            -e "s/^Exec=InnioasisUpdater/Exec=$APP_NAME/" \
            -e "s/^StartupWMClass=Updater CE/StartupWMClass=$DISPLAY_NAME/" \
            "$entry"
    done
fi

# Copy AppStream metadata
APPDATA_SRC="assets/io.github.y1_community.InnioasisUpdater.metainfo.xml"
if [ -f "$APPDATA_SRC" ]; then
    cp "$APPDATA_SRC" "$APPDIR/usr/share/metainfo/io.github.y1_community.InnioasisUpdater.metainfo.xml"
    cp "$APPDATA_SRC" "$APPDIR/usr/share/metainfo/innioasis-updater.appdata.xml"
    cp "$APPDATA_SRC" "$APPDIR/usr/share/metainfo/innioasis-updater.metainfo.xml"
fi

# Copy icons
ICON_SRC="assets/icon.png"
if [ -f "$ICON_SRC" ]; then
    cp "$ICON_SRC" "$APPDIR/innioasis-updater.png"
    cp "$ICON_SRC" "$APPDIR/.DirIcon"
    cp "$ICON_SRC" "$APPDIR/usr/share/icons/hicolor/256x256/apps/innioasis-updater.png"
else
    echo "WARNING: assets/icon.png not found! Using fallback icon."
    touch "$APPDIR/innioasis-updater.png"
fi

# Create AppRun entry point
cat << 'EOF' > "$APPDIR/AppRun"
#!/bin/sh
set -e

# Resolve AppDir location
HERE="$(dirname "$(readlink -f "${0}")")"

export PATH="${HERE}/usr/bin:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/bin/_internal:${HERE}/usr/lib:${LD_LIBRARY_PATH}"
export XDG_DATA_DIRS="${HERE}/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"

# Qt must use the plugins inside this build. A distro's Qt 6 plugin directory
# is built against a different Qt and, when it is on the search path, Qt loads
# the host libqxcb.so first and aborts with "Could not load the Qt platform
# plugin ..." before any window appears (Fedora reports this on both Wayland
# and X11). Pin the bundled copy and drop any inherited value when absent.
QT_PLUGIN_DIR=""
for _cand in "$HERE/usr/bin/_internal/PySide6/Qt/plugins" \
             "$HERE/usr/bin/_internal/PySide6/plugins" \
             "$HERE/usr/bin/PySide6/Qt/plugins"; do
    if [ -d "$_cand/platforms" ]; then
        QT_PLUGIN_DIR="$_cand"
        break
    fi
done
if [ -n "$QT_PLUGIN_DIR" ]; then
    export QT_PLUGIN_PATH="$QT_PLUGIN_DIR"
    export QT_QPA_PLATFORM_PLUGIN_PATH="$QT_PLUGIN_DIR/platforms"
else
    unset QT_PLUGIN_PATH QT_QPA_PLATFORM_PLUGIN_PATH
fi

# Hand off execution to the brand's binary (InnioasisUpdater or
# MediaTekInstaller — both come from the same spec). If it dies with a native
# crash before reporting the window up, retry once with the conservative Qt
# stack (see src/linux_desktop.py): another compositor, GPU driver or input
# method setup then gets a working launch instead of the same abort.
APP_BIN="${HERE}/usr/bin/InnioasisUpdater"
RUN_MARKER="${TMPDIR:-/tmp}/innioasis-updater-start-$$"
rm -f "$RUN_MARKER"
trap 'rm -f "$RUN_MARKER"' EXIT HUP INT TERM
INNIOASIS_STARTUP_MARKER="$RUN_MARKER"
export INNIOASIS_STARTUP_MARKER

# --- Self-heal the desktop stack ------------------------------------------
# Qt 6.5+ loads libqxcb.so only when libxcb-cursor0 and its helpers are
# present. The bundle ships them, but a tarball built before that rule (or an
# AppImage on a machine with an old glibc) can still be missing a core one.
# Name the packages instead of dying with "could not load the Qt platform
# plugin xcb" and no way forward.
_missing=""
for _so in libxcb-cursor.so.0 libxcb-image.so.0 libxcb-render-util.so.0 libxcb-util.so.1 libxkbcommon-x11.so.0; do
    if [ ! -e "$HERE/usr/bin/_internal/$_so" ] && [ ! -e "$HERE/usr/lib/$_so" ]; then
        _missing="$_missing $_so"
    fi
done
if [ -n "$_missing" ]; then
    printf 'Updater CE: this bundle is missing:%s\n' "$_missing" >&2
    printf 'Install the Qt XCB helpers, then run it again:\n' >&2
    printf '  Debian/Ubuntu/Mint: sudo apt install libxcb-cursor0 libxcb-image0 libxcb-render-util0 libxcb-util1 libxkbcommon-x11-0\n' >&2
    printf '  Fedora:             sudo dnf install xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11\n' >&2
    printf '  Arch/Manjaro/CachyOS/SteamOS: sudo pacman -S xcb-util-cursor xcb-util-image xcb-util-renderutil xcb-util libxkbcommon-x11\n' >&2
    printf '  openSUSE:           sudo zypper install libxcb-cursor0 xcb-util-image xcb-util-renderutil libxkbcommon-x11\n' >&2
    printf 'The app also runs headless: %s --flash-cli --help\n' "$APP_BIN" >&2
fi

set +e
"$APP_BIN" "$@"
_status=$?
set -e
if [ -z "${INNIOASIS_SAFE_MODE:-}" ] && [ ! -e "$RUN_MARKER" ] && \
   { [ "$_status" -eq 134 ] || [ "$_status" -eq 139 ] || [ "$_status" -eq 132 ] || [ "$_status" -eq 135 ]; }; then
    echo "The Qt front end stopped during startup; retrying once in safe mode." >&2
    INNIOASIS_SAFE_MODE=1
    QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-xcb}"
    export INNIOASIS_SAFE_MODE QT_QPA_PLATFORM
    exec "$APP_BIN" "$@"
fi
if [ "$_status" -ne 0 ] && [ ! -e "$RUN_MARKER" ]; then
    printf 'Updater CE did not open a window on this desktop. It still runs from a terminal:\n' >&2
    printf '  %s --flash-cli --help\n' "$APP_BIN" >&2
fi
exit "$_status"
EOF
chmod +x "$APPDIR/AppRun"
# ...and point it at this brand's executable.
if [ "$APP_NAME" != "InnioasisUpdater" ]; then
    sed -i "s|/usr/bin/InnioasisUpdater|/usr/bin/$APP_NAME|" "$APPDIR/AppRun"
fi

# --- Step 5: Obtain appimagetool ----------------------------------------------
log_step 5 "$TOTAL_STEPS" "Preparing appimagetool" "Obtaining and bootstrapping AppImage packaging binary..."
APPIMAGETOOL_BIN=""
if command -v appimagetool >/dev/null 2>&1; then
    APPIMAGETOOL_BIN="$(command -v appimagetool)"
    echo "  -> Found system appimagetool: $APPIMAGETOOL_BIN"
fi

# Bootstrap appimagetool if not found or in environment without FUSE
TOOLS_DIR="$BUILD_DIR/tools"
EXTRACTED_TOOL="$TOOLS_DIR/appimagetool.extracted/AppRun"

if [ -z "$APPIMAGETOOL_BIN" ]; then
    if [ -x "$EXTRACTED_TOOL" ]; then
        APPIMAGETOOL_BIN="$EXTRACTED_TOOL"
        echo "  -> Reusing cached extracted appimagetool at $APPIMAGETOOL_BIN"
    else
        echo "  -> Downloading appimagetool ($ARCH)..."
        mkdir -p "$TOOLS_DIR"
        TOOL_APPIMAGE="$TOOLS_DIR/appimagetool-$ARCH.AppImage"
        TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
        
        if ! curl -fsSL -o "$TOOL_APPIMAGE" "$TOOL_URL"; then
            echo "  -> Fallback: downloading from AppImageKit release 13..."
            TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/13/appimagetool-${ARCH}.AppImage"
            curl -fsSL -o "$TOOL_APPIMAGE" "$TOOL_URL"
        fi
        chmod +x "$TOOL_APPIMAGE"

        echo "  -> Extracting appimagetool for reliable execution across environments..."
        rm -rf "$TOOLS_DIR/appimagetool.extracted"
        (
            cd "$TOOLS_DIR"
            ./"appimagetool-$ARCH.AppImage" --appimage-extract >/dev/null 2>&1 || true
            if [ -d "squashfs-root" ]; then
                mv squashfs-root appimagetool.extracted
            fi
        )

        if [ -x "$EXTRACTED_TOOL" ]; then
            APPIMAGETOOL_BIN="$EXTRACTED_TOOL"
        else
            APPIMAGETOOL_BIN="$TOOL_APPIMAGE"
        fi
    fi
fi
echo "  -> appimagetool ready."

# --- Step 6: Build AppImage ---------------------------------------------------
OUTPUT_VERSIONED="$DIST_DIR/${BRAND_SLUG}-${VERSION}-${ARCH}.AppImage"
OUTPUT_LATEST="$DIST_DIR/${BRAND_SLUG}-${ARCH}.AppImage"
log_step 6 "$TOTAL_STEPS" "Generating Standalone Linux AppImage" "Compressing AppDir into squashfs runtime ($OUTPUT_VERSIONED)..."

echo "  -> Creating AppImage with appimagetool..."
# appimagetool mis-resolves a relative AppDir path ("no such file or directory"), so pass an absolute one.
APPDIR_ABS="$(realpath "$APPDIR")"
if ! ARCH="$ARCH" "$APPIMAGETOOL_BIN" "$APPDIR_ABS" "$OUTPUT_VERSIONED" >/dev/null 2>&1; then
    echo "  -> Notice: retrying with --no-appstream flag..."
    ARCH="$ARCH" "$APPIMAGETOOL_BIN" --no-appstream "$APPDIR_ABS" "$OUTPUT_VERSIONED" >/dev/null 2>&1
fi

# Create standard unversioned link/copy for auto-update and release naming
cp -f "$OUTPUT_VERSIONED" "$OUTPUT_LATEST"
chmod +x "$OUTPUT_VERSIONED" "$OUTPUT_LATEST"

echo "  -> Generating SHA256 checksums..."
(
    cd "$DIST_DIR"
    sha256sum "$(basename "$OUTPUT_VERSIONED")" > "$(basename "$OUTPUT_VERSIONED").sha256"
    sha256sum "$(basename "$OUTPUT_LATEST")" > "$(basename "$OUTPUT_LATEST").sha256"
)

# --- Step 7: Portable tar.gz of the PyInstaller onedir folder -----------------
log_step 7 "$TOTAL_STEPS" "Packaging Portable Distribution Tarball (.tar.gz)" "Compressing directory for distros without FUSE or AppImage support..."
TARBALL_VERSIONED="$DIST_DIR/${BRAND_SLUG}-${VERSION}-${ARCH}.tar.gz"
TARBALL_LATEST="$DIST_DIR/${BRAND_SLUG}-${ARCH}.tar.gz"
TAR_STAGE="$BUILD_DIR/tarball/$APP_NAME"

rm -rf "$BUILD_DIR/tarball"
mkdir -p "$TAR_STAGE"
cp -a "$DIST_DIR/$APP_NAME"/. "$TAR_STAGE/"
cp "$APPDIR/innioasis-updater.png" "$TAR_STAGE/innioasis-updater.png"
cp "$APPDIR/innioasis-updater.desktop" "$TAR_STAGE/innioasis-updater.desktop"
if [ ! -x "$TAR_STAGE/$APP_NAME" ]; then
    echo "ERROR: $TAR_STAGE/$APP_NAME is not executable"
    exit 1
fi
tar -C "$BUILD_DIR/tarball" -czf "$TARBALL_VERSIONED" "$APP_NAME"
cp -f "$TARBALL_VERSIONED" "$TARBALL_LATEST"
(
    cd "$DIST_DIR"
    sha256sum "$(basename "$TARBALL_VERSIONED")" > "$(basename "$TARBALL_VERSIONED").sha256"
    sha256sum "$(basename "$TARBALL_LATEST")" > "$(basename "$TARBALL_LATEST").sha256"
)
# Stable aliases required by the end-user download page and run_linux.sh.
if [ "$BRAND_SLUG" = "InnioasisUpdater" ]; then
    # Stable names from docs/RELEASE_FILES.md: x86-64 is untagged, arm64 carries -arm64.
    case "$ARCH" in
        aarch64|arm64) ARM_SUFFIX="-arm64" ;;
        *) ARM_SUFFIX="" ;;
    esac
    APPIMAGE_STABLE="$DIST_DIR/UpdaterCE${ARM_SUFFIX}.appimage"
    TARBALL_STABLE="$DIST_DIR/UpdaterCE-Linux-Portable${ARM_SUFFIX}.tar.gz"
    ZIP_STABLE="$DIST_DIR/UpdaterCE-Linux-Portable${ARM_SUFFIX}.zip"
    cp -f "$OUTPUT_VERSIONED" "$APPIMAGE_STABLE"
    cp -f "$TARBALL_VERSIONED" "$TARBALL_STABLE"
    install -m 0755 run_linux.sh "$DIST_DIR/run_linux.sh"
    sha256sum "$APPIMAGE_STABLE" > "$APPIMAGE_STABLE.sha256"
    sha256sum "$TARBALL_STABLE" > "$TARBALL_STABLE.sha256"
    sha256sum "$DIST_DIR/run_linux.sh" > "$DIST_DIR/run_linux.sh.sha256"
elif [ "$BRAND_SLUG" = "MediaTekInstaller" ]; then
    cp -f "$OUTPUT_VERSIONED" "$DIST_DIR/MediaTek Installer.appimage"
    cp -f "$OUTPUT_VERSIONED" "$DIST_DIR/MediaTekInstaller.AppImage"
    sha256sum "$DIST_DIR/MediaTek Installer.appimage" > "$DIST_DIR/MediaTek Installer.appimage.sha256"
    sha256sum "$DIST_DIR/MediaTekInstaller.AppImage" > "$DIST_DIR/MediaTekInstaller.AppImage.sha256"
fi

# --- Step 8: Portable ZIP of the same folder ------------------------
if [ "$BRAND_SLUG" = "MediaTekInstaller" ]; then
    cp -f "$TARBALL_VERSIONED" "$DIST_DIR/MediaTek Installer.tar.gz"
fi
log_step 8 "$TOTAL_STEPS" "Packaging Portable ZIP" "Creating the .zip portable archive (Windows-friendly extraction)..."
ZIP_VERSIONED="$DIST_DIR/${BRAND_SLUG}-${VERSION}-${ARCH}.zip"
if command -v zip >/dev/null 2>&1; then
    rm -f "$ZIP_VERSIONED"
    (cd "$BUILD_DIR/tarball" && zip -qry "$ZIP_VERSIONED" "$APP_NAME")
else
    echo "  -> 'zip' not found; writing the archive with Python instead"
    rm -f "$ZIP_VERSIONED"
    ARCHIVE_DIR="$BUILD_DIR/tarball" python3 - "$ZIP_VERSIONED" "$APP_NAME" <<'PY'
import os
import sys
import zipfile

out, top = sys.argv[1], sys.argv[2]
root = os.environ["ARCHIVE_DIR"]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
    for base, _dirs, files in os.walk(os.path.join(root, top)):
        for name in files:
            full = os.path.join(base, name)
            zf.write(full, os.path.relpath(full, root))
PY
fi
(
    cd "$DIST_DIR"
    sha256sum "$(basename "$ZIP_VERSIONED")" > "$(basename "$ZIP_VERSIONED").sha256"
)
if [ "$BRAND_SLUG" = "InnioasisUpdater" ]; then
    cp -f "$ZIP_VERSIONED" "$ZIP_STABLE"
    sha256sum "$ZIP_STABLE" > "$ZIP_STABLE.sha256"
fi

APPIMAGE_SIZE=$(du -sh "$OUTPUT_VERSIONED" 2>/dev/null | cut -f1 || echo "unknown")
TARBALL_SIZE=$(du -sh "$TARBALL_VERSIONED" 2>/dev/null | cut -f1 || echo "unknown")
ZIP_SIZE=$(du -sh "$ZIP_VERSIONED" 2>/dev/null | cut -f1 || echo "unknown")

echo ""
echo "================================================================================"
echo "  LINUX BUILD COMPLETE - Output Summary"
echo "================================================================================"
echo "  Brand:            $DISPLAY_NAME ($BRAND_SLUG)"
echo "  Author:           $AUTHOR"
echo "  Version:          $VERSION ($ARCH)"
echo "  AppImage Package: $OUTPUT_VERSIONED ($APPIMAGE_SIZE)"
echo "  Tarball Archive:  $TARBALL_VERSIONED ($TARBALL_SIZE)"
echo "  Portable ZIP:     $ZIP_VERSIONED ($ZIP_SIZE)"
echo "  Checksum File:    $OUTPUT_VERSIONED.sha256"
if [ "$BRAND_SLUG" = "InnioasisUpdater" ]; then
    echo "  Stable downloads: $APPIMAGE_STABLE"
    echo "                    $TARBALL_STABLE"
    echo "                    $ZIP_STABLE"
else
    echo "  Stable downloads: $DIST_DIR/MediaTek Installer.appimage"
    echo "                    $DIST_DIR/MediaTek Installer.tar.gz"
fi
echo "================================================================================"
