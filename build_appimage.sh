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

APP_NAME="InnioasisUpdater"
DISPLAY_NAME="Innioasis Updater CE"
APP_ID="io.github.y1_community.InnioasisUpdater"
VERSION=$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' src/config.py 2>/dev/null || echo "3.0.0")
ARCH="${ARCH:-x86_64}"

DIST_DIR="dist"
BUILD_DIR="build"
APPDIR="$BUILD_DIR/AppDir"
SPEC_FILE="InnioasisUpdater.spec"

CLEAN_BUILD=0
SKIP_PYINSTALLER=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --clean)
            CLEAN_BUILD=1
            shift
            ;;
        --no-pyinstaller)
            SKIP_PYINSTALLER=1
            shift
            ;;
        --arch)
            ARCH="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo "Options:"
            echo "  --clean           Remove previous build and dist artifacts before building"
            echo "  --no-pyinstaller  Skip PyInstaller build and package existing dist/InnioasisUpdater"
            echo "  --arch <arch>     Target architecture (default: x86_64)"
            echo "  -h, --help        Show this help message"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

export ARCH

echo "======================================================================"
echo " Building $DISPLAY_NAME AppImage (v$VERSION - $ARCH)"
echo "======================================================================"

# --- Preflight Python & Dependencies ------------------------------------------
if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    PYTHON="${PYTHON:-}"
    if [ -z "$PYTHON" ]; then
        for cand in .venv-build/bin/python3 .venv/bin/python3 "$(which python3 2>/dev/null || true)"; do
            if [ -n "$cand" ] && [ -x "$cand" ]; then
                if "$cand" -c "import PySide6, PyInstaller" 2>/dev/null; then
                    PYTHON="$cand"
                    break
                fi
            fi
        done
    fi

    if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
        if command -v python3 >/dev/null 2>&1; then
            PYTHON="$(command -v python3)"
        else
            echo "ERROR: Python 3 not found. Set PYTHON env var or active virtual environment."
            exit 1
        fi
    fi

    echo ">>> Using Python: $PYTHON ($("$PYTHON" --version 2>&1))"

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
fi

# --- Clean if requested -------------------------------------------------------
if [ "$CLEAN_BUILD" -eq 1 ]; then
    echo ">>> Cleaning build artifacts..."
    rm -rf "$BUILD_DIR" "$DIST_DIR/$APP_NAME" "$DIST_DIR"/*.AppImage*
fi

# --- Step 1: PyInstaller build ------------------------------------------------
mkdir -p "$DIST_DIR" "$BUILD_DIR"

if [ "$SKIP_PYINSTALLER" -eq 0 ]; then
    echo ">>> Running PyInstaller with $SPEC_FILE..."
    "$PYTHON" -m PyInstaller \
        --clean \
        --noconfirm \
        "$SPEC_FILE"
fi

if [ ! -d "$DIST_DIR/$APP_NAME" ] || [ ! -f "$DIST_DIR/$APP_NAME/$APP_NAME" ]; then
    echo "ERROR: PyInstaller build directory not found at $DIST_DIR/$APP_NAME/$APP_NAME"
    exit 1
fi

# --- Step 2: Prepare AppDir structure -----------------------------------------
echo ">>> Assembling AppDir structure in $APPDIR..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/metainfo"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy binary payload & _internal
cp -r "$DIST_DIR/$APP_NAME"/* "$APPDIR/usr/bin/"

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
Name=Innioasis Updater CE
GenericName=Firmware Flasher
Comment=Flash, update, and restore Innioasis Y1 & Y2 digital audio players
Exec=InnioasisUpdater %U
Icon=innioasis-updater
Terminal=false
Categories=Utility;AudioVideo;
Keywords=innioasis;y1;y2;mtk;flash;firmware;rockbox;solar;
StartupWMClass=Innioasis Updater
EOF
    cp "$APPDIR/innioasis-updater.desktop" "$APPDIR/usr/share/applications/innioasis-updater.desktop"
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

# Hand off execution to InnioasisUpdater binary
exec "${HERE}/usr/bin/InnioasisUpdater" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# --- Step 3: Obtain appimagetool ----------------------------------------------
APPIMAGETOOL_BIN=""
if command -v appimagetool >/dev/null 2>&1; then
    APPIMAGETOOL_BIN="$(command -v appimagetool)"
    echo ">>> Found system appimagetool: $APPIMAGETOOL_BIN"
fi

# Bootstrap appimagetool if not found or in environment without FUSE
TOOLS_DIR="$BUILD_DIR/tools"
EXTRACTED_TOOL="$TOOLS_DIR/appimagetool.extracted/AppRun"

if [ -z "$APPIMAGETOOL_BIN" ]; then
    if [ -x "$EXTRACTED_TOOL" ]; then
        APPIMAGETOOL_BIN="$EXTRACTED_TOOL"
        echo ">>> Reusing cached extracted appimagetool at $APPIMAGETOOL_BIN"
    else
        echo ">>> Downloading appimagetool ($ARCH)..."
        mkdir -p "$TOOLS_DIR"
        TOOL_APPIMAGE="$TOOLS_DIR/appimagetool-$ARCH.AppImage"
        TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage"
        
        if ! curl -fsSL -o "$TOOL_APPIMAGE" "$TOOL_URL"; then
            echo ">>> Fallback: downloading from AppImageKit release 13..."
            TOOL_URL="https://github.com/AppImage/AppImageKit/releases/download/13/appimagetool-${ARCH}.AppImage"
            curl -fsSL -o "$TOOL_APPIMAGE" "$TOOL_URL"
        fi
        chmod +x "$TOOL_APPIMAGE"

        echo ">>> Extracting appimagetool for reliable execution in all environments..."
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

# --- Step 4: Build AppImage ---------------------------------------------------
OUTPUT_VERSIONED="$DIST_DIR/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
OUTPUT_LATEST="$DIST_DIR/${APP_NAME}-${ARCH}.AppImage"

echo ">>> Generating AppImage: $OUTPUT_VERSIONED..."
if ! ARCH="$ARCH" "$APPIMAGETOOL_BIN" "$APPDIR" "$OUTPUT_VERSIONED"; then
    echo ">>> AppStream validation warning detected; falling back to appimagetool --no-appstream..."
    ARCH="$ARCH" "$APPIMAGETOOL_BIN" --no-appstream "$APPDIR" "$OUTPUT_VERSIONED"
fi

# Create standard unversioned link/copy for auto-update and release naming
cp -f "$OUTPUT_VERSIONED" "$OUTPUT_LATEST"
chmod +x "$OUTPUT_VERSIONED" "$OUTPUT_LATEST"

# Generate SHA256 checksums
echo ">>> Generating checksums..."
(
    cd "$DIST_DIR"
    sha256sum "$(basename "$OUTPUT_VERSIONED")" > "$(basename "$OUTPUT_VERSIONED").sha256"
    sha256sum "$(basename "$OUTPUT_LATEST")" > "$(basename "$OUTPUT_LATEST").sha256"
)

echo "======================================================================"
echo " Build successful!"
echo " AppImage created: $OUTPUT_VERSIONED ($(du -h "$OUTPUT_VERSIONED" | cut -f1))"
echo " Latest symlink:   $OUTPUT_LATEST"
echo " Checksum:         $OUTPUT_VERSIONED.sha256"
echo "======================================================================"
