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

APP_NAME="Updater CE"
APP_ID="com.innioasis.updater"
# BUILD_BRAND=mediatek_installer (or --brand mediatek_installer) packages the
# generic cross-platform MediaTek Installer instead: its own name and bundle
# id, and an app that knows it is offline-only.
BRAND="$(printf '%s' "${BUILD_BRAND:-updater_ce}" | tr '[:upper:]-' '[:lower:]_')"
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
        --brand)
            BRAND="$(printf '%s' "$2" | tr '[:upper:]-' '[:lower:]_')"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

case "$BRAND" in
    updater_ce) ;;
    mediatek_installer)
        APP_NAME="MediaTek Installer"
        APP_ID="com.innioasis.mediatekinstaller"
        ;;
    *)
        echo "ERROR: unknown --brand '$BRAND' (expected updater_ce or mediatek_installer)"
        exit 1
        ;;
esac

export TARGET_ARCH
export BUILD_BRAND="$BRAND"

if [ -z "${PYTHON:-}" ]; then
    if [ -x ".venv-build/bin/python3" ]; then
        PYTHON=".venv-build/bin/python3"
    elif [ -x ".venv/bin/python3" ]; then
        PYTHON=".venv/bin/python3"
    elif command -v python3 >/dev/null 2>&1; then
        PYTHON="$(command -v python3)"
    else
        echo "ERROR: Python not found."
        echo "       Set PYTHON env var or create a .venv first."
        exit 1
    fi
fi

# --- Preflight -----------------------------------------------------------
CHECK_MODS="usb serial Cryptodome colorama"
if [ "$(uname -s)" = "Darwin" ]; then
    CHECK_MODS="PyInstaller PySide6 $CHECK_MODS"
fi
for mod in $CHECK_MODS; do
    if ! "$PYTHON" -c "import $mod" 2>/dev/null; then
        echo "ERROR: Required module '$mod' not installed."
        echo "       Run: pip install -r requirements.txt"
        exit 1
    fi
done

DARWIN_LIBUSB="vendor/mtkclient/mtkclient/Darwin/libusb-1.0.dylib"
if [ ! -f "$DARWIN_LIBUSB" ]; then
    echo ">>> Ensuring universal libusb-1.0.dylib for macOS..."
    mkdir -p "vendor/mtkclient/mtkclient/Darwin"
    "$PYTHON" -c "
import urllib.request, zipfile, io, os, subprocess
url = 'https://files.pythonhosted.org/packages/52/6f/26de4e9f858ab50e87931f0be268f3c1bbfce33e8584add60da857632142/libusb_package-1.0.30.0-py3-none-macosx_11_0_arm64.whl'
try:
    data = urllib.request.urlopen(url, timeout=10).read()
    zf = zipfile.ZipFile(io.BytesIO(data))
    arm64_dylib = zf.read('libusb_package/libusb-1.0.dylib')
    with open('/tmp/libusb-arm64.dylib', 'wb') as f:
        f.write(arm64_dylib)
    import libusb_package
    x86_path = libusb_package.get_library_path()
    subprocess.run(['lipo', '-create', '-output', '$DARWIN_LIBUSB', '/tmp/libusb-arm64.dylib', x86_path], check=True)
    subprocess.run(['codesign', '-s', '-', '--force', '$DARWIN_LIBUSB'], check=True)
except Exception as e:
    print('Notice: Failed auto-downloading universal dylib:', e)
" 2>/dev/null || true
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

echo "=== Building $APP_NAME for macOS (v$VERSION) [$BRAND] ==="
echo "Target: macOS 13 (Ventura) through macOS 26 (Golden Gate)"
[n -n "$TARGET_ARCH" ] && echo "Architecture: $TARGET_ARCH" || echo "Architecture: Host default (Intel/Apple Silicon)"

# --- Clean ----------------------------------------------------------------
rm -rf "$BUILD_DIR" "$DIST_DIR"/*.app "$DIST_DIR"/*.dmg

# --- Bake the packaging brand -----------------------------------------
# The frozen app must know its own brand without an environment: this writes
# src/_build_brand.py (gitignored) for PyInstaller to freeze, and removes it
# again however the build ends.
"$PYTHON" scripts/set_build_brand.py "$BRAND"
trap 'rm -f src/_build_brand.py' EXIT

if [ "$(uname -s)" = "Darwin" ]; then
    # --- Native macOS build with PyInstaller ------------------------------
    "$PYTHON" -m PyInstaller \
        --noconfirm \
        --clean \
        "$SPEC_FILE"

    # Remove intermediate COLLECT directory so dist/ contains ONLY the self-contained .app
    rm -rf "$DIST_DIR/$APP_NAME"

    echo ">>> Signing bundle..."
    ENTITLEMENTS_FILE="assets/entitlements.plist"
    if [ -f "$ENTITLEMENTS_FILE" ]; then
        find "$DIST_DIR/$APP_NAME.app" -type f \( -name "*.dylib" -o -name "*.so" \) -exec codesign --force -s - {} + 2>/dev/null || true
        codesign --force --deep --entitlements "$ENTITLEMENTS_FILE" -s - "$DIST_DIR/$APP_NAME.app"
    else
        codesign --force --deep -s - "$DIST_DIR/$APP_NAME.app"
    fi
else
    # --- Universal 2 (Intel + Apple Silicon) Mach-O build via LLVM/clang/rcodesign ---
    echo ">>> Building Universal 2 Mach-O .app bundle..."
    "$PYTHON" scripts/build_universal_app.py
    rm -rf "$DIST_DIR/$APP_NAME"
fi

echo "=== Build complete: $DIST_DIR/$APP_NAME.app ==="

# --- Optional DMG creation -----------------------------------------------
if [ "$CREATE_DMG" -eq 1 ]; then
    ARCH_SUFFIX="${TARGET_ARCH:+-$TARGET_ARCH}"
    if [ "$BRAND" = "mediatek_installer" ]; then
        BRAND_SLUG="MediaTekInstaller"
    else
        BRAND_SLUG="UpdaterCE"
    fi
    DMG_NAME="${BRAND_SLUG}-${VERSION}${ARCH_SUFFIX}-macOS.dmg"
    echo ">>> Creating DMG: $DIST_DIR/$DMG_NAME"

    DMG_TEMP=$(mktemp -d)
    cp -R "$DIST_DIR/$APP_NAME.app" "$DMG_TEMP/"
    ln -s /Applications "$DMG_TEMP/Applications"

    hdiutil create \
        -volname "$APP_NAME" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$DIST_DIR/$DMG_NAME"

    rm -rf "$DMG_TEMP"
    echo "=== DMG ready: $DIST_DIR/$DMG_NAME ==="
fi

echo ""
echo "Done. Drag '$APP_NAME.app' from $DIST_DIR to your Applications folder."
