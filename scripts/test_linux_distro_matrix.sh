#!/usr/bin/env bash
# ==============================================================================
# test_linux_distro_matrix.sh — prove the portable Linux bundle on real distros
#
# Runs the same tarball in several distribution containers. On each one it
# starts the real Qt window under Xvfb, the terminal interface, the no-display
# fallback, MTKClient's USB/lib loads, and — on x86-64 — the bundled SP Flash
# Tool window, which then waits for a device.
#
# Usage:
#   ./scripts/test_linux_distro_matrix.sh --tarball dist/linux/UpdaterCE-Linux-Portable.tar.gz
#   ./scripts/test_linux_distro_matrix.sh --tarball dist/linux/UpdaterCE-Linux-Portable-arm64.tar.gz
#   ./scripts/test_linux_distro_matrix.sh --tarball ... --arch x86_64 \
#       --image ubuntu:24.04 --image fedora:40
#
# The architecture is inferred from the tarball name (aarch64/arm64 vs
# x86_64/amd64). Docker Desktop on Apple silicon runs the x86-64 images through
# its emulation, so the same command covers both native and emulated runs.
# ==============================================================================

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSIDE="$ROOT/scripts/test_linux_distro_inside.sh"
TARBALL=""
ARCH=""
IMAGES=()
DOCKER="${DOCKER:-docker}"

usage() {
    sed -n '2,22p' "$0" | sed -e 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --tarball)
            [[ $# -ge 2 ]] || { echo "--tarball needs a path" >&2; exit 2; }
            TARBALL="$2"; shift 2 ;;
        --arch)
            [[ $# -ge 2 ]] || { echo "--arch needs x86_64 or aarch64" >&2; exit 2; }
            ARCH="$2"; shift 2 ;;
        --image)
            [[ $# -ge 2 ]] || { echo "--image needs a docker image" >&2; exit 2; }
            IMAGES+=("$2"); shift 2 ;;
        -h|--help)
            usage; exit 0 ;;
        *)
            echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
done

[[ -n "$TARBALL" ]] || { echo "Missing --tarball (see --help)" >&2; exit 2; }
[[ -f "$TARBALL" ]] || { echo "No such tarball: $TARBALL" >&2; exit 2; }
TARBALL="$(cd "$(dirname "$TARBALL")" && pwd)/$(basename "$TARBALL")"
[[ -x "$INSIDE" || -f "$INSIDE" ]] || { echo "Missing $INSIDE" >&2; exit 2; }

if [[ -z "$ARCH" ]]; then
    case "$(basename "$TARBALL")" in
        *aarch64*|*arm64*) ARCH="aarch64" ;;
        *) ARCH="x86_64" ;;
    esac
fi
case "$ARCH" in
    x86_64|amd64) ARCH="x86_64"; PLATFORM="linux/amd64" ;;
    aarch64|arm64) ARCH="aarch64"; PLATFORM="linux/arm64" ;;
    *) echo "Unsupported --arch '$ARCH' (x86_64 or aarch64)" >&2; exit 2 ;;
esac

if [[ ${#IMAGES[@]} -eq 0 ]]; then
    if [[ "$ARCH" = "x86_64" ]]; then
        IMAGES=(
            "ubuntu:22.04"
            "ubuntu:24.04"
            "debian:12-slim"
            "debian:13-slim"
            "fedora:40"
            "fedora:41"
            "archlinux:latest"
            "opensuse/leap:15.6"
        )
    else
        IMAGES=(
            "ubuntu:22.04"
            "ubuntu:24.04"
            "debian:12-slim"
            "fedora:40"
            "archlinux:latest"
        )
    fi
fi

if ! "$DOCKER" info >/dev/null 2>&1; then
    echo "Docker is not reachable." >&2
    exit 2
fi

# The dependency names differ, but every image here uses one of these package
# managers. Keep this list to what the window, Xvfb and the SP tool need; the
# app's Qt libraries are inside the tarball.
install_deps() {
    cat <<'SH'
set -e
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y --no-install-recommends \
        xvfb xauth x11-utils xdotool dbus-x11 \
        libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
        libxcb-image0 libxcb-keysyms1 libxcb-render-util0 libxcb-util1 \
        libxcb-xkb1 libxcb-shape0 libxcb-shm0 libxcb-xfixes0 libxcb-randr0 \
        libx11-xcb1 libfontconfig1 libfreetype6 libdbus-1-3 libglib2.0-0 \
        libusb-1.0-0 fontconfig
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y xorg-x11-server-Xvfb xorg-x11-xauth xdotool \
        libxkbcommon-x11 xcb-util-cursor xcb-util-image xcb-util-renderutil \
        xcb-util libxcb libX11 libXext libXrender libXi libXtst libXfixes \
        libXrandr libXcursor mesa-libGL mesa-libEGL fontconfig freetype \
        libusb1 glib2 dbus-libs
elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive refresh
    zypper --non-interactive install xvfb xauth xdotool \
        libxkbcommon-x11-0 libxcb-cursor0 xcb-util-image xcb-util-renderutil \
        libxcb-util1 libGL1 libEGL1 fontconfig libdbus-1-3 glib2 libusb-1_0-0
elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm --needed xorg-server-xvfb xorg-xauth xdotool \
        libxkbcommon-x11 xcb-util-cursor xcb-util-image xcb-util-renderutil \
        xcb-util libxcb libx11 libxext libxrender libxi libxtst libxfixes \
        libxrandr libxcursor mesa libglvnd fontconfig freetype2 libusb \
        dbus glib2
else
    echo "Unsupported package manager in this image" >&2
    exit 3
fi
SH
}

echo "======================================================================"
echo " Linux distro matrix: $ARCH ($PLATFORM)"
echo " tarball: $TARBALL"
echo " images: ${IMAGES[*]}"
echo "======================================================================"

failed=()
for image in "${IMAGES[@]}"; do
    echo ""
    echo "----------------------------------------------------------------------"
    echo " $image"
    echo "----------------------------------------------------------------------"
    if ! "$DOCKER" run --rm --platform "$PLATFORM" \
        -e ARCH="$ARCH" \
        -e APP_DIR=/opt/updater \
        -v "$TARBALL:/tmp/updater.tar.gz:ro" \
        -v "$INSIDE:/opt/test-linux-distro.sh:ro" \
        "$image" bash -lc "$(install_deps)
mkdir -p /opt/updater /tmp/updater-home
tar -xzf /tmp/updater.tar.gz -C /opt/updater --strip-components=1
bash /opt/test-linux-distro.sh"; then
        failed+=("$image")
    fi
done

echo ""
echo "======================================================================"
if [[ ${#failed[@]} -ne 0 ]]; then
    echo " FAILED: ${failed[*]}"
    exit 1
fi
echo " PASSED: all ${#IMAGES[@]} images"
