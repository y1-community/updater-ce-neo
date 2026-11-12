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
#   - macOS 12+ (Monterey through Golden Gate compatible, Intel & Apple Silicon)
#   - Xcode Command Line Tools for --arch universal2 (compiles the launcher):
#       xcode-select --install
#   - Nothing else: a fresh clone has no virtualenv and no packages, so the first
#     run creates .venv-build and pip-installs requirements.txt +
#     requirements-build.txt (scripts/bootstrap_build_env.py). Set PYTHON=... to
#     reuse an interpreter instead, or pass --check-deps to only verify it.
#
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="Innioasis Updater"
APP_ID="com.innioasis.updater"
AUTHOR="Ryan Specter"
# A run builds Updater CE only. BUILD_BRAND=mediatek_installer (or --brand
# mediatek_installer) packages the generic cross-platform MediaTek Installer
# instead: its own name and bundle id, and an app that knows it is offline-only.
# --brand all builds both front ends.
BRAND="$(printf '%s' "${BUILD_BRAND:-updater_ce}" | tr '[:upper:]-' '[:lower:]_')"
# The brand the caller asked for through the environment, before the default.
# The brand plan below distinguishes "nothing asked for" from "updater_ce".
BRAND_ENV="${BUILD_BRAND:-}"
VERSION=$(sed -n 's/.*APP_VERSION[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' src/config.py 2>/dev/null || echo "2.5.5")
[ -z "$VERSION" ] && VERSION="2.5.5"
DIST_DIR="dist/macos"
BUILD_DIR="build/macos"
SPEC_FILE="macos.spec"
BUNDLE_ICON="assets/icon.icns"
CREATE_DMG=0
CHECK_DEPS_ONLY=0
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
        --check-deps)
            CHECK_DEPS_ONLY=1
            shift
            ;;
        *)
            shift
            ;;
    esac
done

# --- Target architecture ----------------------------------------------------
# Default is the machine you are on: PyInstaller can only emit the architecture
# of the Python it runs under, so silently labelling host output "universal2"
# produced an Intel-incompatible bundle in the past. `universal2` is the one
# cross-architecture target and it builds through scripts/build_universal_app.py.
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
    arm64|aarch64) HOST_ARCH="arm64" ;;
    x86_64|amd64) HOST_ARCH="x86_64" ;;
esac
case "${TARGET_ARCH:-}" in
    ""|host|native|auto) TARGET_ARCH="$HOST_ARCH" ;;
    universal|universal2) TARGET_ARCH="universal2" ;;
    arm64|aarch64) TARGET_ARCH="arm64" ;;
    x86_64|amd64|intel) TARGET_ARCH="x86_64" ;;
    *)
        echo "ERROR: unknown --arch '$TARGET_ARCH' (expected arm64, x86_64 or universal2)"
        exit 1
        ;;
esac
if [ "$(uname -s)" != "Darwin" ] && [ "$TARGET_ARCH" != "universal2" ]; then
    echo "  -> Cross-building on $(uname -s): only the Universal 2 bundle is possible here."
    TARGET_ARCH="universal2"
fi

# Reject a bad brand before anything is deleted: a typo must not wipe a good
# build sitting in dist/.
for _candidate in "${BRAND_REQUESTED:-}" "$BRAND"; do
    case "$_candidate" in
        ""|updater_ce|mediatek_installer|all|both) ;;
        *)
            echo "ERROR: unknown brand '$_candidate' (expected updater_ce, mediatek_installer or all)"
            exit 1
            ;;
    esac
done

# --- Brand plan -----------------------------------------------------------
# A build produces the Updater CE app. The generic MediaTek Installer app (same
# internals, different identity) is opt-in: --brand mediatek_installer builds
# just it, --brand all builds both. Keeping it out of the default run makes a
# release build shorter and skips a product that may never ship.
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
            # The current bundle name plus the stale "Updater CE.app" name
            # from earlier builds, so a rebuild never leaves two apps behind.
            rm -rf "$DIST_DIR/Innioasis Updater.app" "$DIST_DIR/Updater CE.app" \
                "$DIST_DIR"/InnioasisUpdater-*.dmg "$DIST_DIR"/UpdaterCE-*.dmg
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
else
    case "${BRAND_REQUESTED:-$BRAND_ENV}" in
        "") BRANDS=(updater_ce) ;;
        all|both) BRANDS=(updater_ce mediatek_installer) ;;
        *) BRANDS=("${BRAND_REQUESTED:-$BRAND_ENV}") ;;
    esac
    rm -rf "$BUILD_DIR"
    for brand in "${BRANDS[@]}"; do
        clean_brand_artifacts "$brand"
    done
    for brand in "${BRANDS[@]}"; do
        INNOASIS_BRAND_LOCK=1 BUILD_BRAND="$brand" "$0" ${ORIGINAL_ARGS[@]+"${ORIGINAL_ARGS[@]}"} || exit $?
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
        echo "ERROR: unknown --brand '$BRAND' (expected updater_ce, mediatek_installer or all)"
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

# --- Python build environment ------------------------------------------------
# A clone has no .venv-build and macOS still ships Python 3.9, which cannot run
# PySide6/PyInstaller. scripts/bootstrap_build_env.py finds a 3.10+ interpreter,
# creates .venv-build, installs the pinned requirements, verifies the imports the
# spec needs, and prints the interpreter as PYTHON=<path>. Re-runs are offline:
# a hash marker makes an unchanged environment a no-op.
BOOTSTRAP_SCRIPT="scripts/bootstrap_build_env.py"
BOOTSTRAP_ARGS=()
[ "$CHECK_DEPS_ONLY" -eq 1 ] && BOOTSTRAP_ARGS+=(--check)
if [ ! -f "$BOOTSTRAP_SCRIPT" ]; then
    echo "ERROR: $BOOTSTRAP_SCRIPT is missing; this checkout is incomplete."
    exit 1
fi
if [ -z "${PYTHON:-}" ]; then
    BOOTSTRAP_PYTHON=""
    for _cand in python3 /usr/bin/python3 python; do
        if command -v "$_cand" >/dev/null 2>&1; then
            BOOTSTRAP_PYTHON="$_cand"
            break
        fi
    done
    if [ -z "$BOOTSTRAP_PYTHON" ]; then
        echo "ERROR: no python3 on PATH to start the build environment bootstrap."
        echo "       Install Python: brew install python@3.12"
        echo "       (or download it from https://www.python.org/downloads/macos/)"
        exit 1
    fi
    if ! BOOTSTRAP_OUTPUT="$("$BOOTSTRAP_PYTHON" "$BOOTSTRAP_SCRIPT" ${BOOTSTRAP_ARGS[@]+"${BOOTSTRAP_ARGS[@]}"})"; then
        echo ""
        echo "ERROR: could not prepare the Python build environment (see above)."
        echo "       Fix that, then run ./build_macos.sh again."
        exit 1
    fi
    PYTHON="$(printf '%s\n' "$BOOTSTRAP_OUTPUT" | sed -n 's/^PYTHON=//p' | tail -1)"
    if [ -z "$PYTHON" ] || [ ! -x "$PYTHON" ]; then
        echo "ERROR: the build environment bootstrap did not report an interpreter."
        exit 1
    fi
else
    # An explicitly supplied interpreter is verified, never modified.
    if ! "$PYTHON" "$BOOTSTRAP_SCRIPT" ${BOOTSTRAP_ARGS[@]+"${BOOTSTRAP_ARGS[@]}"} >/dev/null; then
        exit 1
    fi
fi

TOTAL_STEPS=$([ "$CREATE_DMG" -eq 1 ] && echo 5 || echo 4)

log_step 1 "$TOTAL_STEPS" "Validating Environment & Dependencies" "Checking Python runtime, PySide6, and MediaTek communication libraries..."
echo "  -> Environment verified: $("$PYTHON" --version 2>&1) ($("$PYTHON" -c 'import platform; print(platform.machine())')) at $PYTHON"

# PyInstaller can only emit the architecture of the interpreter it runs under.
if [ "$(uname -s)" = "Darwin" ] && [ "$TARGET_ARCH" != "universal2" ]; then
    PY_ARCH="$("$PYTHON" -c 'import platform; print(platform.machine().lower())')"
    case "$PY_ARCH" in
        arm64|aarch64) PY_ARCH="arm64" ;;
        x86_64|amd64) PY_ARCH="x86_64" ;;
    esac
    if [ "$PY_ARCH" != "$TARGET_ARCH" ]; then
        echo "ERROR: --arch $TARGET_ARCH needs a $TARGET_ARCH Python, but $PYTHON is $PY_ARCH."
        echo "       Build on a $TARGET_ARCH Mac, point PYTHON at a $TARGET_ARCH python3,"
        echo "       or use --arch universal2 to produce one bundle for both architectures."
        exit 1
    fi
fi

# macOS tooling: the icon conversion, ad-hoc signing and DMG steps are part of
# the OS, but a universal build compiles its launcher with clang from Xcode.
if [ "$(uname -s)" = "Darwin" ]; then
    MISSING_TOOLS=""
    for _tool in sips iconutil codesign lipo; do
        command -v "$_tool" >/dev/null 2>&1 || MISSING_TOOLS="$MISSING_TOOLS $_tool"
    done
    if [ "$CREATE_DMG" -eq 1 ] && ! command -v hdiutil >/dev/null 2>&1; then
        MISSING_TOOLS="$MISSING_TOOLS hdiutil"
    fi
    if [ -n "$MISSING_TOOLS" ]; then
        echo "ERROR: missing macOS build tools:$MISSING_TOOLS"
        echo "       Install the Xcode Command Line Tools: xcode-select --install"
        exit 1
    fi
    if [ "$TARGET_ARCH" = "universal2" ] && ! xcrun --find clang >/dev/null 2>&1; then
        echo "ERROR: --arch universal2 compiles its launcher with clang."
        echo "       Install the Xcode Command Line Tools: xcode-select --install"
        exit 1
    fi
fi

# Snapshot innioasis.app's catalogue/notices/donors/blocklist into the bundle so
# a first launch works offline. Best-effort: existing copies are kept on failure.
"$PYTHON" scripts/fetch_online_data.py || echo "  ! Online snapshot refresh skipped (using the copies already in assets/online)"
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
if [ ! -f "$BUNDLE_ICON" ]; then
    # PyInstaller rejects a missing --icon path, so say what happened here
    # instead of letting the bundle step fail with a FileNotFoundError.
    echo "  ! Could not generate $BUNDLE_ICON from assets/icon.png; building without an app icon."
fi

echo "  -> Target OS: macOS 12 (Monterey) through macOS 26 (Golden Gate)"
[ -n "$TARGET_ARCH" ] && echo "  -> Architecture: $TARGET_ARCH" || echo "  -> Architecture: Host default (Intel/Apple Silicon)"

# --- Bake the packaging brand -----------------------------------------
"$PYTHON" scripts/set_build_brand.py "$BRAND"
trap 'rm -f src/_build_brand.py' EXIT

log_step 3 "$TOTAL_STEPS" "Building Standalone macOS Application Bundle" "Compiling $APP_NAME into self-contained .app bundle..."
if [ "$TARGET_ARCH" = "universal2" ] || [ "$(uname -s)" != "Darwin" ]; then
    # --- Universal 2 (Intel + Apple Silicon) Mach-O build ---
    echo "  -> Building Universal 2 Mach-O .app bundle..."
    INNIOASIS_APP_OUT="$DIST_DIR/$APP_NAME.app" APP_NAME="$APP_NAME" BUILD_BRAND="$BRAND" "$PYTHON" scripts/build_universal_app.py
    rm -rf "$DIST_DIR/$APP_NAME"

    log_step 4 "$TOTAL_STEPS" "Applying Mach-O Code Signatures" "Applying ad-hoc code signatures to universal application bundle..."
    echo "  -> Universal bundle signatures applied."
else
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
        BRAND_SLUG="InnioasisUpdater"
    fi
    DMG_NAME="${BRAND_SLUG}-${VERSION}${ARCH_SUFFIX}-macOS.dmg"
    echo "  -> Generating DMG: $DIST_DIR/$DMG_NAME..."

    DMG_TEMP=$(mktemp -d)
    cp -R "$DIST_DIR/$APP_NAME.app" "$DMG_TEMP/"
    ln -s /Applications "$DMG_TEMP/Applications"
    printf 'Author: %s\nCopyright (C) 2024-2026 %s. All rights reserved.\n' \
        "$AUTHOR" "$AUTHOR" > "$DMG_TEMP/AUTHOR.txt"

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
echo "  Author:      $AUTHOR"
echo "  App Bundle:  $DIST_DIR/$APP_NAME.app ($APP_SIZE)"
if [ "$CREATE_DMG" -eq 1 ]; then
    echo "  Disk Image:  $DIST_DIR/$DMG_NAME ($DMG_SIZE)"
fi
echo "================================================================================"
echo "  Ready to use: drag '$APP_NAME.app' to your Applications folder."
echo "================================================================================"
