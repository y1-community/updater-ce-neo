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

set +e
"$APP_BIN" "$@"
_status=$?
set -e

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
