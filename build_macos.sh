#!/bin/bash
#
# build_macos.sh — Build Innioasis Updater as a standalone macOS .app
#
# Usage:
#   chmod +x build_macos.sh
#   ./build_macos.sh                         # builds .app using macos.spec
#   ./build_macos.sh --arch universal2       # builds Universal 2 (Intel + Apple Silicon)
#   ./build_macos.sh --arch arm64 --dmg      # builds Apple Silicon .app and .dmg
#
# Requirements:
#   - Python 3.11+ in a virtualenv (.venv)
#   - pip install pyinstaller pyside6 requests pyqt-liquidglass pyobjc-framework-cocoa pyobjc-framework-quartz
#   - macOS 13+ (Ventura through Golden Gate compatible, Intel & Apple Silicon)
#
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Innioasis Updater CE"
APP_ID="com.innioasis.updater"
VERSION=$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' src/config.py 2>/dev/null || echo "3.0")
DIST_DIR="dist"
BUILD_DIR="build"
SPEC_FILE="macos.spec"
BUNDLE_ICON="assets/icon.icns"
CREATE_DMG=0
TARGET_ARCH="${TARGET_ARCH:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dmg)
            CREATE_DMG=1
            shift
            ;;
        --arch)
            TARGET_ARCH="$2"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

export TARGET_ARCH

PYTHON="${PYTHON:-.venv/bin/python3}"

# --- Preflight -----------------------------------------------------------
if [ ! -f "$PYTHON" ]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON="$(command -v python3)"
    else
        echo "ERROR: Python not found at $PYTHON"
        echo "       Set PYTHON env var or create a .venv first."
        exit 1
    fi
fi

if ! "$PYTHON" -c "import PyInstaller" 2>/dev/null; then
    echo "ERROR: PyInstaller not installed in this Python."
    echo "       Run: pip install pyinstaller"
    exit 1
fi

if ! "$PYTHON" -c "import PySide6" 2>/dev/null; then
    echo "ERROR: PySide6 not installed."
    echo "       Run: pip install pyside6"
    exit 1
fi

# Convert .ico to .icns if needed (macOS uses .icns, not .ico)
if [ ! -f "$BUNDLE_ICON" ]; then
    echo ">>> Generating macOS .icns from icon.png ..."
    if [ -f "assets/icon.png" ]; then
        TMP_ICNS_DIR=$(mktemp -d)
        ICONSET="$TMP_ICNS_DIR/icon.iconset"
        mkdir -p "$ICONSET"
        for size in 16 32 64 128 256 512; do
            sips -z $size $size assets/icon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1
            double=$((size * 2))
            if [ "$double" -le 1024 ]; then
                sips -z $double $double assets/icon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1
            fi
        done
        iconutil -c icns "$ICONSET" -o "$BUNDLE_ICON" 2>/dev/null || true
        rm -rf "$TMP_ICNS_DIR"
    fi
fi

echo "=== Building Innioasis Updater CE for macOS (v$VERSION) ==="
echo "Target: macOS 13 (Ventura) through macOS 26 (Golden Gate)"
[ -n "$TARGET_ARCH" ] && echo "Architecture: $TARGET_ARCH" || echo "Architecture: Host default (Intel/Apple Silicon)"

# --- Clean ----------------------------------------------------------------
rm -rf "$BUILD_DIR" "$DIST_DIR"/*.app "$DIST_DIR"/*.dmg

# --- PyInstaller build using macos.spec ------------------------------------
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --clean \
    "$SPEC_FILE"

echo "=== Build complete: $DIST_DIR/$APP_NAME.app ==="

# --- Optional DMG creation -----------------------------------------------
if [ "$CREATE_DMG" -eq 1 ]; then
    ARCH_SUFFIX="${TARGET_ARCH:+-$TARGET_ARCH}"
    DMG_NAME="InnioasisUpdater-${VERSION}${ARCH_SUFFIX}-macOS.dmg"
    echo ">>> Creating DMG: $DIST_DIR/$DMG_NAME"

    DMG_TEMP=$(mktemp -d)
    cp -R "$DIST_DIR/$APP_NAME.app" "$DMG_TEMP/"
    ln -s /Applications "$DMG_TEMP/Applications"

    hdiutil create \
        -volname "Innioasis Updater" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$DIST_DIR/$DMG_NAME"

    rm -rf "$DMG_TEMP"
    echo "=== DMG ready: $DIST_DIR/$DMG_NAME ==="
fi

echo ""
echo "Done. Drag '$APP_NAME.app' from $DIST_DIR to your Applications folder."
