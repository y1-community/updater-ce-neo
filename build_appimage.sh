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
#   - Python 3.10+ (virtualenv recommended)
#   - pip install -r requirements.txt pyinstaller
#   - curl, file, desktop-file-utils (appimagetool is auto-bootstrapped if missing)
# ==============================================================================

set -euo pipefail
cd "$(dirname "$0")"

export BUILD_BRAND="${BUILD_BRAND:-}"
VERSION=$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' src/config.py 2>/dev/null || echo "2.5.0")
ARCH="${ARCH:-x86_64}"

DIST_DIR="${DIST_DIR:-dist/linux}"
BUILD_DIR="build/linux"
APPDIR="$BUILD_DIR/AppDir"
SPEC_FILE="InnioasisUpdater.spec"

CLEAN_BUILD=0
SKIP_PYINSTALLER=0
BRAND_REQUESTED=""
SPEC_FILE="InnioasisUpdater.spec"
USE_CONTAINER="${USE_CONTAINER:-auto}"

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
            echo "  --brand <name>    Build only this brand (updater_ce or mediatek_installer);"
            echo "                    by default BOTH apps are built"
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
        ""|updater_ce|mediatek_installer) ;;
        *)
            echo "ERROR: unknown brand '$_candidate' (expected updater_ce or mediatek_installer)"
            exit 1
            ;;
    esac
done

# --- Brand plan ---------------------------------------------------------------
# A build produces BOTH front ends: Updater CE and the generic MediaTek
# Installer (same internals, different identity and front end). --brand picks
# just one for a quick local iteration. Each brand is built by a child run of
# this script, so the single-brand path stays identical to what it always was.
if [ -z "${INNOASIS_BRAND_LOCK:-}" ]; then
    if [ -n "${BRAND_REQUESTED:-}" ]; then
        brands=("$BRAND_REQUESTED")
    else
        brands=(updater_ce mediatek_installer)
    fi

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
        INNOASIS_BRAND_LOCK=1 BUILD_BRAND="$brand" "$0" "${FORWARD_ARGS[@]}" || exit $?
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
        echo "ERROR: unknown BUILD_BRAND '$BRAND' (expected updater_ce or mediatek_installer)"
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
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
    for cand in .venv-build/bin/python3 .venv/bin/python3 "$(which python3 2>/dev/null || true)"; do
        if [ -n "$cand" ] && [ -x "$cand" ]; then
            PYTHON="$cand"
            break
        fi
    done
fi

log_step 1 "$TOTAL_STEPS" "Validating Environment & Dependencies" "Checking Python runtime, PySide6, PyInstaller, and build tools..."
if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
        if command -v python3 >/dev/null 2>&1; then
            PYTHON="$(command -v python3)"
        else
            echo "ERROR: Python 3 not found. Set PYTHON env var or active virtual environment."
            exit 1
        fi
    fi

    echo "  -> Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"

    if ! "$PYTHON" -c "import PyInstaller" 2>/dev/null; then
        echo "ERROR: PyInstaller is not installed in $PYTHON."
        echo "       Run: pip install pyinstaller"
        exit 1
    fi

    if ! "$PYTHON" -c "import PySide6" 2>/dev/null; then
        echo "ERROR: PySide6 is not installed in $PYTHON."
        echo "       Run: pip install -r requirements.txt"
        exit 1
    fi
    echo "  -> Required modules verified (PySide6, PyInstaller)"
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

# Decide whether to build inside Debian Bookworm container for pure x86-64-baseline
SHOULD_USE_CONTAINER=0
if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    if [ "$USE_CONTAINER" = "1" ]; then
        SHOULD_USE_CONTAINER=1
    elif [ "$USE_CONTAINER" = "auto" ]; then
        if command -v docker >/dev/null 2>&1; then
            # If host system has v4/v3 packages in /usr/lib (like CachyOS or optimized distros), containerize to avoid SIGILL on Skylake/Ryzen
            if readelf -n /usr/lib/libcrypto.so.3 2>/dev/null | grep -q "x86-64-v4" || \
               readelf -n /usr/lib/libc.so.6 2>/dev/null | grep -q "x86-64-v4"; then
                echo "  -> Notice: Host system uses x86-64-v4 optimized libraries."
                echo "  -> Using Debian Bookworm container to guarantee pure x86-64 baseline for all CPUs."
                SHOULD_USE_CONTAINER=1
            fi
        fi
    fi
fi

if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    if [ "$SHOULD_USE_CONTAINER" -eq 1 ]; then
        echo "  -> Building standalone binary in updater-linux-builder container..."
        if ! docker image inspect updater-linux-builder >/dev/null 2>&1; then
            echo "  -> Creating updater-linux-builder container image..."
            docker build -t updater-linux-builder -f Dockerfile.linux_build .
        fi

        mkdir -p "$DIST_DIR" "$BUILD_DIR"
        docker run --rm \
            -v "$(pwd):/workspace" \
            -v "$DIST_DIR:$DIST_DIR" \
            updater-linux-builder -c "
set -e
python3 -m venv /tmp/venv
/tmp/venv/bin/pip install -q --upgrade pip
/tmp/venv/bin/pip install -q -r requirements.txt pyinstaller
/tmp/venv/bin/python3 scripts/set_build_brand.py '$BRAND'
/tmp/venv/bin/pyinstaller --clean --noconfirm --distpath '$DIST_DIR' --workpath '$BUILD_DIR' '$SPEC_FILE'
cp /usr/lib/x86_64-linux-gnu/libusb-1.0.so* '$DIST_DIR/$APP_NAME/_internal/' 2>/dev/null || true
chown -R $(id -u):$(id -g) '$DIST_DIR' '$BUILD_DIR' .cache 2>/dev/null || true
rm -f src/_build_brand.py
"
        echo "  -> Container PyInstaller compilation completed."
    else
        echo "  -> Running PyInstaller on host..."
        "$PYTHON" -m PyInstaller \
            --clean \
            --noconfirm \
            --distpath "$DIST_DIR" \
            --workpath "$BUILD_DIR" \
            "$SPEC_FILE"
        echo "  -> PyInstaller compilation completed."
    fi
fi

if [ ! -d "$DIST_DIR/$APP_NAME" ] || [ ! -f "$DIST_DIR/$APP_NAME/$APP_NAME" ]; then
    echo "ERROR: PyInstaller build directory not found at $DIST_DIR/$APP_NAME/$APP_NAME"
    exit 1
fi

# --- Step 3: Stage the bundled SP Flash Tool Linux payload --------------------
log_step 3 "$TOTAL_STEPS" "Bundling SP Flash Tool Linux Payload" "Copying flash_tool executable, helper scripts, and libraries into package..."
SP_TOOL_SRC="tools/linux/SP_Flash_Tool_v5.1904_Linux"
SP_TOOL_DIST="$DIST_DIR/$APP_NAME/SP_Flash_Tool"
if [ ! -f "$SP_TOOL_DIST/flash_tool" ] && [ -f "$SP_TOOL_SRC/flash_tool" ]; then
    echo "  -> Staging bundled SP Flash Tool payload: $SP_TOOL_SRC -> $SP_TOOL_DIST"
    rm -rf "$SP_TOOL_DIST"
    cp -r "$SP_TOOL_SRC" "$SP_TOOL_DIST"
fi
if [ -f "$SP_TOOL_DIST/flash_tool" ]; then
    chmod 0755 "$SP_TOOL_DIST/flash_tool" "$SP_TOOL_DIST/flash_tool.sh" 2>/dev/null || true
    find "$SP_TOOL_DIST" -name "*.so*" -exec chmod a+r {} + 2>/dev/null || true
    echo "  -> SP Flash Tool payload bundled at $SP_TOOL_DIST"
else
    echo "  -> Note: no local SP Flash Tool payload found; app will download at first run if needed."
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
               "$APPDIR/usr/bin/SP_Flash_Tool/flash_tool.sh" 2>/dev/null || true
fi

# Bundle libusb-1.0 into AppDir/usr/lib:
# Prefer the baseline libusb-1.0 bundled in _internal, otherwise look on the build system
mkdir -p "$APPDIR/usr/lib"
if compgen -G "$APPDIR/usr/bin/_internal/libusb-1.0.so*" > /dev/null; then
    cp -d "$APPDIR/usr/bin/_internal"/libusb-1.0.so* "$APPDIR/usr/lib/" 2>/dev/null || true
else
    for libusb_cand in /usr/lib/*/libusb-1.0.so* /usr/lib64/libusb-1.0.so* /usr/lib/libusb-1.0.so*; do
        if [ -f "$libusb_cand" ]; then
            cp -d "$libusb_cand" "$APPDIR/usr/lib/" 2>/dev/null || true
        fi
    done
fi

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

# Hand off execution to the brand's binary (InnioasisUpdater or
# MediaTekInstaller — both come from the same spec).
exec "${HERE}/usr/bin/InnioasisUpdater" "$@"
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
if ! ARCH="$ARCH" "$APPIMAGETOOL_BIN" "$APPDIR" "$OUTPUT_VERSIONED" >/dev/null 2>&1; then
    echo "  -> Notice: retrying with --no-appstream flag..."
    ARCH="$ARCH" "$APPIMAGETOOL_BIN" --no-appstream "$APPDIR" "$OUTPUT_VERSIONED" >/dev/null 2>&1
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
    cp -f "$OUTPUT_VERSIONED" "$DIST_DIR/UpdaterCE.appimage"
    cp -f "$TARBALL_VERSIONED" "$DIST_DIR/UpdaterCE-${ARCH}.tar.gz"
    cp -f "$TARBALL_VERSIONED" "$DIST_DIR/UpdaterCE.tar.gz"
    install -m 0755 run_linux.sh "$DIST_DIR/run_linux.sh"
    sha256sum "$DIST_DIR/UpdaterCE.appimage" > "$DIST_DIR/UpdaterCE.appimage.sha256"
    sha256sum "$DIST_DIR/UpdaterCE.tar.gz" > "$DIST_DIR/UpdaterCE.tar.gz.sha256"
    sha256sum "$DIST_DIR/run_linux.sh" > "$DIST_DIR/run_linux.sh.sha256"
elif [ "$BRAND_SLUG" = "MediaTekInstaller" ]; then
    cp -f "$OUTPUT_VERSIONED" "$DIST_DIR/MediaTek Installer.appimage"
    sha256sum "$DIST_DIR/MediaTek Installer.appimage" > "$DIST_DIR/MediaTek Installer.appimage.sha256"
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
    cp -f "$ZIP_VERSIONED" "$DIST_DIR/UpdaterCE-Linux-Portable.zip"
    sha256sum "$DIST_DIR/UpdaterCE-Linux-Portable.zip" > "$DIST_DIR/UpdaterCE-Linux-Portable.zip.sha256"
fi

APPIMAGE_SIZE=$(du -sh "$OUTPUT_VERSIONED" 2>/dev/null | cut -f1 || echo "unknown")
TARBALL_SIZE=$(du -sh "$TARBALL_VERSIONED" 2>/dev/null | cut -f1 || echo "unknown")
ZIP_SIZE=$(du -sh "$ZIP_VERSIONED" 2>/dev/null | cut -f1 || echo "unknown")

echo ""
echo "================================================================================"
echo "  LINUX BUILD COMPLETE - Output Summary"
echo "================================================================================"
echo "  Brand:            $DISPLAY_NAME ($BRAND_SLUG)"
echo "  Version:          $VERSION ($ARCH)"
echo "  AppImage Package: $OUTPUT_VERSIONED ($APPIMAGE_SIZE)"
echo "  Tarball Archive:  $TARBALL_VERSIONED ($TARBALL_SIZE)"
echo "  Portable ZIP:     $ZIP_VERSIONED ($ZIP_SIZE)"
echo "  Checksum File:    $OUTPUT_VERSIONED.sha256"
if [ "$BRAND_SLUG" = "InnioasisUpdater" ]; then
    echo "  Stable downloads: $DIST_DIR/UpdaterCE.appimage"
    echo "                    $DIST_DIR/UpdaterCE.tar.gz"
    echo "                    $DIST_DIR/UpdaterCE-Linux-Portable.zip"
else
    echo "  Stable downloads: $DIST_DIR/MediaTek Installer.appimage"
    echo "                    $DIST_DIR/MediaTek Installer.tar.gz"
fi
echo "================================================================================"
