#!/usr/bin/env bash
# Runs inside one distribution container as part of
# scripts/test_linux_distro_matrix.sh. The portable tarball is already
# unpacked at /opt/updater and the display/Xvfb packages are installed.
#
# This checks behaviour, not just imports:
#   * the frozen app's own self-check
#   * the TUI in a terminal (plain mode, no curses dependency)
#   * the TUI fallback when no display exists
#   * the real Qt window under Xvfb
#   * the bundled SP Flash Tool window on x86-64
set -uo pipefail

ARCH="${ARCH:?ARCH is required}"
APP_DIR="${APP_DIR:-/opt/updater}"
APP="$APP_DIR/InnioasisUpdater"
export HOME="${HOME:-/tmp/updater-home}"
export XDG_CONFIG_HOME="$HOME/.config"
export XDG_CACHE_HOME="$HOME/.cache"
export XDG_DATA_HOME="$HOME/.local/share"
export XDG_STATE_HOME="$HOME/.local/state"
export INNIOASIS_NO_PREFETCH=1
export INNIOASIS_SKIP_PREP=1
mkdir -p "$HOME" "$XDG_CONFIG_HOME" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_STATE_HOME"

fail=0
step() { printf '\n==> %s\n' "$*"; }
bad() { printf 'FAIL: %s\n' "$*" >&2; fail=1; }
show_log() {
    [ -s "$1" ] || return 0
    printf -- '--- %s (tail) ---\n' "$1" >&2
    tail -n 30 "$1" >&2 || true
}

step "Distribution"
. /etc/os-release
printf '%s\n' "${PRETTY_NAME:-unknown}" "kernel=$(uname -srm)" "arch=$ARCH"
[ -x "$APP" ] || { echo "No executable at $APP" >&2; exit 2; }

step "Frozen bundle self-check (offscreen)"
if QT_QPA_PLATFORM=offscreen timeout 180 "$APP" --self-check >/tmp/selfcheck.log 2>&1; then
    echo "self-check passed"
else
    bad "self-check failed"
    show_log /tmp/selfcheck.log
fi

step "Environment diagnostics (MTKClient, Python and libusb)"
if QT_QPA_PLATFORM=offscreen timeout 300 "$APP" --diagnostics >/tmp/diagnostics.log 2>&1; then
    echo "diagnostics passed"
    grep -E 'libusb backend|MTKClient|mtkclient|libusb-1.0' /tmp/diagnostics.log | head -n 8 || true
else
    bad "diagnostics failed"
    show_log /tmp/diagnostics.log
fi

step "Terminal interface entry points"
if "$APP" --tui --help >/tmp/tui-help.log 2>&1; then
    echo "TUI help passed"
else
    bad "TUI help failed"
    show_log /tmp/tui-help.log
fi
if "$APP" --cli --version >/tmp/cli-version.log 2>&1; then
    echo "CLI version passed: $(cat /tmp/cli-version.log)"
else
    bad "CLI version failed"
    show_log /tmp/cli-version.log
fi

step "Terminal interface renders Select Software"
printf 'q\n' | timeout 60 "$APP" --tui >/tmp/tui.log 2>&1
if grep -q 'Select Software' /tmp/tui.log; then
    echo "TUI menu rendered"
else
    bad "TUI did not render Select Software"
    show_log /tmp/tui.log
fi

step "No-display launch falls back to the terminal interface"
timeout 20 env -u DISPLAY -u WAYLAND_DISPLAY "$APP" </dev/null >/tmp/no-display.log 2>&1
if grep -qE 'Select Software|terminal interface' /tmp/no-display.log; then
    echo "no-display fallback reached the terminal interface"
else
    bad "no-display launch did not reach the terminal interface"
    show_log /tmp/no-display.log
fi

step "Window under Xvfb"
rm -rf "$XDG_CONFIG_HOME/innioasis"
# Minimal containers (notably Fedora) do not create the X11 socket directory,
# and Xvfb exits immediately when it is missing.
mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
xvfb_pid=$!
export DISPLAY=:99
# Probe with xdotool: every image installs it. xdpyinfo lives in x11-utils on
# Debian but in differently named packages on Fedora/Arch/openSUSE, so using it
# made a perfectly healthy Xvfb look broken there.
display_ready() { xdotool getdisplaygeometry >/dev/null 2>&1; }
for _ in $(seq 1 40); do
    display_ready && break
    sleep 0.25
done
if ! display_ready; then
    bad "Xvfb did not start"
    show_log /tmp/xvfb.log
else
    "$APP" >/tmp/gui.log 2>&1 &
    gui_pid=$!
    window=""
    for _ in $(seq 1 40); do
        window="$(xdotool search --onlyvisible --name 'Updater CE' 2>/dev/null | head -n 1 || true)"
        [ -n "$window" ] && break
        kill -0 "$gui_pid" 2>/dev/null || break
        sleep 0.5
    done
    if [ -n "$window" ]; then
        title="$(xdotool getwindowname "$window" 2>/dev/null || echo '?')"
        echo "window opened: $title"
        sleep 2
        if ! kill -0 "$gui_pid" 2>/dev/null; then
            bad "window exited immediately after opening"
            show_log /tmp/gui.log
        fi
    else
        bad "no Updater CE window appeared"
        kill -0 "$gui_pid" 2>/dev/null || echo "process exited before a window appeared" >&2
        show_log /tmp/gui.log
    fi
    kill "$gui_pid" 2>/dev/null || true
    wait "$gui_pid" 2>/dev/null || true
fi

if [ "$ARCH" = "x86_64" ]; then
    step "Bundled SP Flash Tool window"
    sp_dir="$APP_DIR/SP_Flash_Tool"
    if [ ! -x "$sp_dir/flash_tool" ]; then
        bad "SP Flash Tool is missing from the x86-64 portable bundle"
    elif ! display_ready; then
        bad "cannot test SP Flash Tool without a display"
    else
        (
            cd "$sp_dir" || exit 2
            export LD_LIBRARY_PATH="$sp_dir:$sp_dir/lib"
            export QT_PLUGIN_PATH="$sp_dir/plugins"
            export QT_QPA_PLATFORM=xcb
            export DISPLAY=:99
            exec ./flash_tool
        ) >/tmp/spflash.log 2>&1 &
        sp_pid=$!
        sp_window=""
        for _ in $(seq 1 40); do
            # The Qt4 UI titles itself "Smart Phone Flash Tool(Runtime Trace Mode)";
            # match the window the user actually sees, not the binary name.
            sp_window="$(xdotool search --onlyvisible --name 'Flash Tool' 2>/dev/null | head -n 1 || true)"
            [ -n "$sp_window" ] && break
            kill -0 "$sp_pid" 2>/dev/null || break
            sleep 0.5
        done
        if [ -n "$sp_window" ]; then
            echo "SP Flash Tool window opened: $(xdotool getwindowname "$sp_window" 2>/dev/null || echo '?')"
            sleep 3
            if ! kill -0 "$sp_pid" 2>/dev/null; then
                bad "SP Flash Tool exited after its window opened"
                show_log /tmp/spflash.log
            else
                echo "SP Flash Tool is still running and waiting for a device"
            fi
        else
            bad "SP Flash Tool did not open a window"
            kill -0 "$sp_pid" 2>/dev/null || echo "SP Flash Tool exited before a window appeared" >&2
            show_log /tmp/spflash.log
        fi
        kill "$sp_pid" 2>/dev/null || true
        wait "$sp_pid" 2>/dev/null || true
    fi
else
    printf '\n==> SP Flash Tool\n'
    echo "omitted on $ARCH (x86-64-only payload; MTKClient is the native engine here)"
fi

kill "$xvfb_pid" 2>/dev/null || true
wait "$xvfb_pid" 2>/dev/null || true

if [ "$fail" -ne 0 ]; then
    printf '\nRESULT: FAILED on %s (%s)\n' "${PRETTY_NAME:-unknown}" "$ARCH" >&2
    exit 1
fi
printf '\nRESULT: PASSED on %s (%s)\n' "${PRETTY_NAME:-unknown}" "$ARCH"
