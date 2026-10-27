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
#   - macOS 12+ (Monterey through Golden Gate compatible, Intel & Apple Silicon)
#
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Updater CE"
APP_ID="com.innioasis.updater"
# BUILD_BRAND=mediatek_installer (or --brand mediatek_installer) packages the
# generic cross-platform MediaTek Installer instead: its own name and bundle
# id, and an app that knows it is offline-only.
BRAND="$(printf '%s' "${BUILD_BRAND:-updater_ce}" | tr '[:upper:]-' '[:lower:]_')"
VERSION=$(grep -oP 'APP_VERSION\s*=\s*"\K[^"]+' src/config.py 2>/dev/null || echo "2.5.1")
DIST_DIR="dist/macos"
BUILD_DIR="build/macos"
SPEC_FILE="macos.spec"
BUNDLE_ICON="assets/icon.icns"
CREATE_DMG=0
TARGET_ARCH="${TARGET_ARCH:-}"
# The brand pass re-invokes this script after the option loop has consumed
# --arch/--dmg, so keep the original argv: the child must build the same
# architecture and DMG the caller asked for, not the host default.
ORIGINAL_ARGS=("$@")

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
            BRAND_REQUESTED="$(printf '%s' "$2" | tr '[:upper:]-' '[:lower:]_')"
            shift 2
            ;;
        *)
            shift
            ;;
    esac
done

# Reject a bad brand before anything is deleted: a typo must not wipe a good
# build sitting in dist/.
for _candidate in "${BRAND_REQUESTED:-}" "$BRAND"; do
    case "$_candidate" in
        ""|updater_ce|mediatek_installer) ;;
        *)
            echo "ERROR: unknown brand '$_candidate' (expected updater_ce or mediatek_installer)"
            exit 1
            ;;
    esac
done

# --- Brand plan -----------------------------------------------------------
# A build produces BOTH front ends: the Updater CE app and the generic
# MediaTek Installer app (same internals, different identity). Pass --brand to
# build just one, which is what a quick local iteration wants.
#
# Each brand is built by a child run of this script: that keeps the per-brand
# path identical to the single-brand flow above and below.
#
# Each app owns its own dist artifacts, so a build only removes the ones it is
# about to replace. Building one front end therefore leaves the other one in
# dist/ untouched, instead of deleting a good app that this run will not
# rebuild (which is how dist/ ended up with a single app in the past).
clean_brand_artifacts() {
    case "$1" in
        updater_ce)
            rm -rf "$DIST_DIR/Updater CE.app" "$DIST_DIR"/UpdaterCE-*.dmg
            ;;
        mediatek_installer)
            rm -rf "$DIST_DIR/MediaTek Installer.app" "$DIST_DIR"/MediaTekInstaller-*.dmg
            ;;
    esac
}

# What dist/ holds after a build, so a single-brand run never looks like it
# lost the other app.
list_built_apps() {
    echo ""
    echo "=== dist/ ==="
    ls -d "$DIST_DIR"/*.app 2>/dev/null || echo "  (no .app bundles yet)"
    ls "$DIST_DIR"/*.dmg 2>/dev/null || true
}

if [ "${INNOASIS_BRAND_LOCK:-}" = "1" ]; then
    : # child pass: build the brand handed over in BUILD_BRAND
elif [ -n "${BRAND_REQUESTED:-}" ]; then
    rm -rf "$BUILD_DIR"
    clean_brand_artifacts "$BRAND_REQUESTED"
    INNOASIS_BRAND_LOCK=1 BUILD_BRAND="$BRAND_REQUESTED" "$0" "${ORIGINAL_ARGS[@]}" || exit $?
    list_built_apps
    exit 0
else
    rm -rf "$BUILD_DIR"
    for brand in updater_ce mediatek_installer; do
        clean_brand_artifacts "$brand"
    done
    for brand in updater_ce mediatek_installer; do
        INNOASIS_BRAND_LOCK=1 BUILD_BRAND="$brand" "$0" "${ORIGINAL_ARGS[@]}" || exit $?
    done
    echo ""
    echo "=== Built for macOS ==="
    list_built_apps
    exit 0
fi

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

TOTAL_STEPS=$([ "$CREATE_DMG" -eq 1 ] && echo 5 || echo 4)

log_step 1 "$TOTAL_STEPS" "Validating Environment & Dependencies" "Checking Python runtime, PySide6, and MediaTek communication libraries..."
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
echo "  -> Environment verified: $("$PYTHON" --version 2>&1)"
if [ "$(uname -s)" = "Darwin" ]; then
    # PySide6 6.12.0 on Python < 3.12 leaks a None reference per paint call and
    # the app aborts ("none_dealloc: deallocating None") within a minute.
    set +e
    "$PYTHON" scripts/check_pyside_refcount.py
    rc=$?
    set -e
    if [ "$rc" -eq 1 ]; then
        exit 1
    fi
fi

log_step 2 "$TOTAL_STEPS" "Preparing Native Assets & Dynamic Libraries" "Ensuring universal libusb-1.0.dylib and macOS application icon (.icns)..."
DARWIN_LIBUSB="vendor/mtkclient/mtkclient/Darwin/libusb-1.0.dylib"
if [ ! -f "$DARWIN_LIBUSB" ]; then
    echo "  -> Preparing universal libusb-1.0.dylib for macOS..."
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
else
    echo "  -> Universal libusb-1.0.dylib verified at $DARWIN_LIBUSB"
fi

# Convert .ico to .icns if needed (macOS uses .icns, not .ico)
if [ ! -f "$BUNDLE_ICON" ]; then
    echo "  -> Generating macOS .icns from icon.png..."
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

echo "  -> Target OS: macOS 12 (Monterey) through macOS 26 (Golden Gate)"
[ -n "$TARGET_ARCH" ] && echo "  -> Architecture: $TARGET_ARCH" || echo "  -> Architecture: Host default (Intel/Apple Silicon)"

# --- Bake the packaging brand -----------------------------------------
"$PYTHON" scripts/set_build_brand.py "$BRAND"
trap 'rm -f src/_build_brand.py' EXIT

log_step 3 "$TOTAL_STEPS" "Building Standalone macOS Application Bundle" "Compiling $APP_NAME into self-contained .app bundle..."
if [ "$(uname -s)" = "Darwin" ]; then
    "$PYTHON" -m PyInstaller \
        --noconfirm \
        --clean \
        --distpath "$DIST_DIR" \
        --workpath "$BUILD_DIR" \
        "$SPEC_FILE"

    rm -rf "$DIST_DIR/$APP_NAME"
    echo "  -> PyInstaller completed standalone application bundle."

    log_step 4 "$TOTAL_STEPS" "Applying Code Signatures & Entitlements" "Signing internal dynamic libraries, frameworks, and .app bundle..."
    ENTITLEMENTS_FILE="assets/entitlements.plist"
    if [ -f "$ENTITLEMENTS_FILE" ]; then
        find "$DIST_DIR/$APP_NAME.app" -type f \( -name "*.dylib" -o -name "*.so" \) -exec codesign --force -s - {} + 2>/dev/null || true
        codesign --force --deep --entitlements "$ENTITLEMENTS_FILE" -s - "$DIST_DIR/$APP_NAME.app"
    else
        codesign --force --deep -s - "$DIST_DIR/$APP_NAME.app"
    fi
    echo "  -> Code signing completed successfully."
else
    # --- Universal 2 (Intel + Apple Silicon) Mach-O build via LLVM/clang/rcodesign ---
    echo "  -> Building Universal 2 Mach-O .app bundle via LLVM cross-compiler..."
    INNIOASIS_APP_OUT="$DIST_DIR/$APP_NAME.app" BUILD_BRAND="$BRAND" "$PYTHON" scripts/build_universal_app.py
    rm -rf "$DIST_DIR/$APP_NAME"

    log_step 4 "$TOTAL_STEPS" "Applying Mach-O Code Signatures" "Applying ad-hoc code signatures to universal application bundle..."
    echo "  -> Universal bundle signatures applied."
fi

APP_SIZE=$(du -sh "$DIST_DIR/$APP_NAME.app" 2>/dev/null | cut -f1 || echo "unknown")
echo "  -> Bundle created: $DIST_DIR/$APP_NAME.app ($APP_SIZE)"

# --- Optional DMG creation -----------------------------------------------
if [ "$CREATE_DMG" -eq 1 ]; then
    log_step 5 "$TOTAL_STEPS" "Creating Disk Image (.dmg)" "Packaging application into mountable .dmg installer..."
    ARCH_SUFFIX="${TARGET_ARCH:+-$TARGET_ARCH}"
    if [ "$BRAND" = "mediatek_installer" ]; then
        BRAND_SLUG="MediaTekInstaller"
    else
        BRAND_SLUG="UpdaterCE"
    fi
    DMG_NAME="${BRAND_SLUG}-${VERSION}${ARCH_SUFFIX}-macOS.dmg"
    echo "  -> Generating DMG: $DIST_DIR/$DMG_NAME..."

    DMG_TEMP=$(mktemp -d)
    cp -R "$DIST_DIR/$APP_NAME.app" "$DMG_TEMP/"
    ln -s /Applications "$DMG_TEMP/Applications"

    hdiutil create \
        -volname "$APP_NAME" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$DIST_DIR/$DMG_NAME" >/dev/null 2>&1

    rm -rf "$DMG_TEMP"
    DMG_SIZE=$(du -sh "$DIST_DIR/$DMG_NAME" 2>/dev/null | cut -f1 || echo "unknown")
    echo "  -> DMG created: $DIST_DIR/$DMG_NAME ($DMG_SIZE)"
fi

echo ""
echo "================================================================================"
echo "  MACOS BUILD COMPLETE - Output Summary"
echo "================================================================================"
echo "  App Bundle:  $DIST_DIR/$APP_NAME.app ($APP_SIZE)"
if [ "$CREATE_DMG" -eq 1 ]; then
    echo "  Disk Image:  $DIST_DIR/$DMG_NAME ($DMG_SIZE)"
fi
echo "================================================================================"
echo "  Ready to use: drag '$APP_NAME.app' to your Applications folder."
echo "================================================================================"
