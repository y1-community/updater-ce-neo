#!/bin/sh
# Launcher for the portable Linux build and the AppImage (this file is AppRun).
#
# The bundle is meant to be self-contained: it carries Qt, PySide6, shiboken6,
# libusb and the C++ runtime. A distribution that ships none of them must still
# get a working window, so this script sets the bundle's own library and plugin
# paths, checks the payload before handing over, and explains itself in a
# dialog when something is missing instead of dying silently in a terminal the
# user never sees.
set -u

HERE=$(dirname "$(readlink -f "$0")")

# AppImage layout: AppRun at the root, payload under usr/bin. Portable tarball
# and zip layout: this script sits beside the executable.
if [ -x "$HERE/usr/bin/InnioasisUpdater" ]; then
    APP_HOME="$HERE/usr/bin"
    APP_BIN="$APP_HOME/InnioasisUpdater"
    LIB_DIRS="$APP_HOME/_internal:$HERE/usr/lib"
elif [ -x "$HERE/usr/bin/MediaTekInstaller" ]; then
    APP_HOME="$HERE/usr/bin"
    APP_BIN="$APP_HOME/MediaTekInstaller"
    LIB_DIRS="$APP_HOME/_internal:$HERE/usr/lib"
elif [ -x "$HERE/InnioasisUpdater" ]; then
    APP_HOME="$HERE"
    APP_BIN="$HERE/InnioasisUpdater"
    LIB_DIRS="$HERE/_internal"
elif [ -x "$HERE/MediaTekInstaller" ]; then
    APP_HOME="$HERE"
    APP_BIN="$HERE/MediaTekInstaller"
    LIB_DIRS="$HERE/_internal"
else
    echo "This folder does not contain the Updater CE executable." >&2
    exit 1
fi

export PATH="$APP_HOME:${PATH:-/usr/local/bin:/usr/bin:/bin}"
export LD_LIBRARY_PATH="$LIB_DIRS${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
case "${XDG_DATA_DIRS:-}" in
    "$HERE/usr/share"*) ;;
    *) export XDG_DATA_DIRS="$HERE/usr/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" ;;
esac

# Qt must load this build's plugins. A distro's Qt 6 plugin directory is built
# against another Qt version, and when it wins the search Qt aborts with
# "Could not load the Qt platform plugin xcb" before any window exists.
QT_PLUGIN_DIR=""
for _cand in "$APP_HOME/_internal/PySide6/Qt/plugins" \
             "$APP_HOME/_internal/PySide6/plugins" \
             "$APP_HOME/PySide6/Qt/plugins"; do
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

LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/innioasis-updater"
mkdir -p "$LOG_DIR" 2>/dev/null || LOG_DIR="${TMPDIR:-/tmp}"
SELF_CHECK_LOG="$LOG_DIR/bundle-selfcheck.log"

# Qt 6.5+ cannot load libqxcb.so without these. The bundle ships them; a build
# made before that rule, or a host that strips one, still gets the exact
# package names for its distribution instead of a bare abort.
_missing=""
for _so in libxcb-cursor.so.0 libxcb-image.so.0 libxcb-render-util.so.0 libxcb-util.so.1 libxkbcommon-x11.so.0; do
    if [ ! -e "$APP_HOME/_internal/$_so" ] && [ ! -e "$HERE/usr/lib/$_so" ]; then
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
fi

notify_problem() {
    # One short sentence for the user, the technical report on disk. Every
    # dialog tool here is optional and absent from minimal systems; then the
    # message goes to the terminal (if there is one) and the log.
    _title="Updater CE could not start normally"
    _text="$1

Details: $SELF_CHECK_LOG"
    echo "Updater CE: $1" >&2
    [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || return 0
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --no-markup --title="$_title" --text="$_text" >/dev/null 2>&1 && return 0
    fi
    if command -v kdialog >/dev/null 2>&1; then
        kdialog --title "$_title" --error "$_text" >/dev/null 2>&1 && return 0
    fi
    if command -v xmessage >/dev/null 2>&1; then
        xmessage -center "$_text" >/dev/null 2>&1 && return 0
    fi
}

# Pre-flight the bundle. This is a self-check, not a gate: the app still gets
# its chance to start (some failures, such as a missing tray helper, are not
# fatal), but a user whose window never appears now has an explanation instead
# of a silent exit.
QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}" "$APP_BIN" --self-check >"$SELF_CHECK_LOG" 2>&1
_self_check_status=$?
unset QT_QPA_PLATFORM 2>/dev/null || true
if [ "$_self_check_status" -ne 0 ]; then
    notify_problem "Some files the app needs are missing from this download. The app will try to start anyway; if no window appears, re-download the package (or use the .tar.gz) and see the log for the missing pieces."
fi

RUN_MARKER="${TMPDIR:-/tmp}/innioasis-updater-start-$$"
rm -f "$RUN_MARKER"
trap 'rm -f "$RUN_MARKER"' EXIT HUP INT TERM
INNIOASIS_STARTUP_MARKER="$RUN_MARKER"
export INNIOASIS_STARTUP_MARKER

# A server, an SSH session or a broken desktop has no display at all. Qt cannot
# open a window there, but the app can still list firmware and flash a player
# in its terminal interface, so start that instead of aborting.
if [ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] && [ -z "${INNIOASIS_TUI:-}" ]; then
    case "${1:-}" in
        --tui|--cli|--self-check|--flash-cli|--help|-h|--version) ;;
        *)
            echo "Updater CE: no graphical session found; starting the terminal interface." >&2
            echo "  Run with --cli for commands, or --tui for the menu. See --help." >&2
            INNIOASIS_TUI=1
            export INNIOASIS_TUI
            exec "$APP_BIN" --tui
            ;;
    esac
fi

# Keep the window's own output: the Qt platform diagnostics are the only
# evidence of why a window did not appear, and they are what tells us whether
# the terminal interface is a usable fallback for this machine.
RUNTIME_LOG="$LOG_DIR/runtime.log"
: > "$RUNTIME_LOG" 2>/dev/null || RUNTIME_LOG="${TMPDIR:-/tmp}/innioasis-updater-runtime.log"

# POSIX sh has no PIPESTATUS: the app's status is written by the subshell that
# ran it, while tee keeps the output visible and on disk.
_STATUS_FILE="${TMPDIR:-/tmp}/innioasis-updater-status-$$"
rm -f "$STATUS_FILE"
set +e
{ "$APP_BIN" "$@" 2>&1; echo "$?" > "$STATUS_FILE"; } | tee -a "$RUNTIME_LOG"
_status=$(cat "$STATUS_FILE" 2>/dev/null || echo 0)
rm -f "$STATUS_FILE"
set -e

# Qt 6.5+ refuses to load libqxcb without libxcb-cursor0, and a distro Qt can
# also be picked up by mistake. Either way the window cannot open, but the
# terminal interface can: hand over rather than exiting with a core dump.
if [ ! -e "$RUN_MARKER" ] && [ "_$status" -ne 0 ] && \
   grep -qE 'xcb-cursor0|Could not load the Qt platform plugin|no Qt platform plugin could be initialized' "$RUNTIME_LOG" 2>/dev/null; then
    echo "" >&2
    echo "Updater CE: Qt could not open a window on this system." >&2
    echo "  Missing: libxcb-cursor0 (Qt 6.5+ needs it to load the xcb plugin)." >&2
    echo "  Debian/Ubuntu/Mint: sudo apt install libxcb-cursor0" >&2
    echo "  Fedora:             sudo dnf install xcb-util-cursor" >&2
    echo "  openSUSE:           sudo zypper install libxcb-cursor0" >&2
    echo "  Arch/CachyOS:       sudo pacman -S xcb-util-cursor" >&2
    echo "  Starting the terminal interface instead; nothing is lost, and" >&2
    echo "  https://innioasis.app/run_linux.sh --repair fixes the window." >&2
    exec "$APP_BIN" --tui
fi

# A native crash before the window reported itself is usually the Qt platform
# stack (compositor, GPU driver, input method), not the app. Retry once with
# the conservative stack instead of showing the same abort twice.
if [ -z "${INNIOASIS_SAFE_MODE:-}" ] && [ ! -e "$RUN_MARKER" ] && \
   { [ "$_status" -eq 134 ] || [ "$_status" -eq 139 ] || [ "$_status" -eq 132 ] || [ "$_status" -eq 135 ]; }; then
    echo "The Qt front end stopped during startup; retrying once in safe mode." >&2
    INNIOASIS_SAFE_MODE=1
    export INNIOASIS_SAFE_MODE
    exec "$APP_BIN" "$@"
fi
exit "$_status"
