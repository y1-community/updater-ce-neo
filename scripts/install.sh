#!/usr/bin/env bash
# ==============================================================================
# Updater CE 3.0 — Fast, Lightweight CLI Installer & Launcher
# Repository: https://github.com/y1-community/updater-ce-neo
# ==============================================================================
# This script replaces the legacy pre-3.0 install scripts (which downloaded full
# Git repos and bloated 1GB+ venvs). It installs compact, pre-compiled native
# binaries and cleanly offers to purge older legacy versions.
# ==============================================================================

set -euo pipefail

REPO="y1-community/updater-ce-neo"
LATEST_RELEASE_API="https://api.github.com/repos/${REPO}/releases/latest"

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${BLUE}▶${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }
warn() { echo -e "${YELLOW}⚠️${NC} $1"; }
error() { echo -e "${RED}✗${NC} $1" >&2; }

OS="$(uname -s)"
ARCH="$(uname -m)"

echo "=========================================="
echo "      Innioasis Updater CE 3.0 Setup      "
echo "=========================================="
echo "Detected Platform: ${OS} (${ARCH})"
echo

# --- 1. Offer Legacy Pre-3.0 Cleanup ---
cleanup_legacy() {
    if [ "${OS}" = "Darwin" ]; then
        LEGACY_APP="/Applications/Innioasis Updater.app"
        USER_LEGACY_APP="${HOME}/Applications/Innioasis Updater.app"
        LEGACY_DIR="${HOME}/Library/Application Support/Innioasis Updater"
        
        FOUND_LEGACY=0
        if [ -d "${LEGACY_APP}" ] || [ -d "${USER_LEGACY_APP}" ] || [ -d "${LEGACY_DIR}" ]; then
            FOUND_LEGACY=1
        fi
        
        if [ ${FOUND_LEGACY} -eq 1 ]; then
            warn "Found older pre-3.0 Innioasis Updater installation."
            echo "Updater CE 3.0 replaces the older version, freeing ~500MB-1GB of space."
            read -p "Would you like to uninstall and remove older pre-3.0 files? [Y/n] " -r REPLY
            REPLY=${REPLY:-Y}
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                log "Removing legacy application support folder..."
                rm -rf "${LEGACY_DIR}" 2>/dev/null || true
                
                if [ -d "${USER_LEGACY_APP}" ]; then
                    rm -rf "${USER_LEGACY_APP}" 2>/dev/null || true
                fi
                
                if [ -d "${LEGACY_APP}" ]; then
                    log "Removing ${LEGACY_APP} (requires admin privileges)..."
                    osascript -e 'do shell script "rm -rf \"/Applications/Innioasis Updater.app\"" with prompt "Updater CE requires permission to remove the legacy Innioasis Updater app." with administrator privileges' 2>/dev/null || true
                fi
                success "Legacy pre-3.0 cleanup completed."
            fi
        fi
    elif [ "${OS}" = "Linux" ]; then
        LEGACY_DIR="${HOME}/.local/share/innioasis-updater"
        LEGACY_DESKTOP="${HOME}/.local/share/applications/innioasis-updater.desktop"
        LEGACY_BIN="${HOME}/.local/bin/innioasis-updater"
        
        if [ -d "${LEGACY_DIR}" ] || [ -f "${LEGACY_DESKTOP}" ] || [ -f "${LEGACY_BIN}" ]; then
            warn "Found older pre-3.0 Innioasis Updater Linux installation."
            read -p "Would you like to remove older pre-3.0 files? [Y/n] " -r REPLY
            REPLY=${REPLY:-Y}
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                rm -rf "${LEGACY_DIR}" "${LEGACY_DESKTOP}" "${LEGACY_BIN}" "${HOME}/.cache/innioasis-updater" 2>/dev/null || true
                success "Legacy pre-3.0 Linux files removed."
            fi
        fi
    fi
}

cleanup_legacy

# --- 2. Installation / Launch ---
if [ "${OS}" = "Darwin" ]; then
    log "Preparing Updater CE 3.0 for macOS..."
    # If running from local clone and dist/Updater CE.app exists, offer to run it
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    LOCAL_APP="${SCRIPT_DIR}/../dist/Updater CE.app"
    
    if [ -d "${LOCAL_APP}" ]; then
        success "Found local built bundle: ${LOCAL_APP}"
        read -p "Launch local Updater CE.app now? [Y/n] " -r REPLY
        REPLY=${REPLY:-Y}
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            open "${LOCAL_APP}"
            exit 0
        fi
    else
        log "To run from source or build the .app bundle:"
        echo "  ./.venv-build/bin/python -m src.app"
        echo "  ./.venv-build/bin/python scripts/build_universal_app.py"
    fi
elif [ "${OS}" = "Linux" ]; then
    log "Preparing Updater CE 3.0 for Linux..."
    mkdir -p "${HOME}/.local/bin" "${HOME}/.local/share/applications"
    
    # Check for local AppImage in dist/
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    LOCAL_APPIMAGE=$(ls "${SCRIPT_DIR}/../dist/"*".AppImage" 2>/dev/null | head -n 1 || true)
    
    if [ -n "${LOCAL_APPIMAGE}" ] && [ -f "${LOCAL_APPIMAGE}" ]; then
        success "Found local AppImage: ${LOCAL_APPIMAGE}"
        chmod +x "${LOCAL_APPIMAGE}"
        cp "${LOCAL_APPIMAGE}" "${HOME}/.local/bin/updater-ce.AppImage"
        success "Installed to ${HOME}/.local/bin/updater-ce.AppImage"
    fi
fi

success "Setup complete! Thank you for using Updater CE 3.0."
