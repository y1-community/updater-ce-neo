#!/bin/sh
# Updater CE — lite installer for headless, old and 32-bit Linux.
#
#   curl -fsSLO https://innioasis.app/run_linux_lite.sh && sh run_linux_lite.sh
#
# The lite install is the terminal front end only: no Qt, no PySide6, no
# compiled wheels. It runs on any machine with Python 3.8 or newer, which
# covers Raspberry Pi OS on a Pi Zero, 32-bit i686 netbooks, ARM NAS boxes and
# servers. Firmware is downloaded with resume support so a slow or interrupted
# connection does not start over, and flashing is handed to the console tools
# (mtkclient / adb) instead of a GUI.
#
# Options:
#   --tui          install, then open the terminal interface (this is the default)
#   --no-tui       install the launcher and stop, without opening the session
#   --file PATH    install from a local UpdaterCE-Lite.tar.gz instead of the network
#   --dir PATH     install location (default: ~/.local/share/innioasis-updater/lite)
#   --bin PATH     where to write the launcher (default: ~/.local/bin)
#   --check        report what this machine has and exit
#   --update       reinstall over the existing copy
#   --uninstall    remove the lite install and its launcher
#   --no-verify    skip the SHA-256 check (not recommended)
#   -q, --quiet    only errors
#
# Exit codes: 0 ok, 2 unsupported machine, 3 missing Python, 4 download failed.

set -u

SITE="https://innioasis.app"
RELEASE_API="https://api.github.com/repos/y1-community/updater-ce-neo/releases/latest"
ASSET_NAME="UpdaterCE-Lite.tar.gz"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
DEFAULT_DIR="$DATA_HOME/innioasis-updater/lite"
BIN_DIR="$HOME/.local/bin"
TARGET_DIR="$DEFAULT_DIR"
LOCAL_FILE=""
OPEN_TUI=1
VERIFY=1
QUIET=0
MODE="install"

say() { [ "$QUIET" -eq 1 ] || printf '%s\n' "$*"; }
err() { printf 'Updater CE: %s\n' "$*" >&2; }
die() { err "$1"; exit "${2:-1}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --tui) OPEN_TUI=1 ;;
        --no-tui) OPEN_TUI=0 ;;
        --file) shift; LOCAL_FILE="${1:-}" ;;
        --dir) shift; TARGET_DIR="${1:-$TARGET_DIR}" ;;
        --bin) shift; BIN_DIR="${1:-$BIN_DIR}" ;;
        --check) MODE="check" ;;
        --update) MODE="update" ;;
        --uninstall) MODE="uninstall" ;;
        --no-verify) VERIFY=0 ;;
        -q|--quiet) QUIET=1 ;;
        -h|--help) sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $1 (try --help)" 1 ;;
    esac
    shift
done

ARCH="$(uname -m 2>/dev/null || echo unknown)"
case "$ARCH" in
    x86_64|amd64) ARCH_NOTE="64-bit Intel/AMD" ;;
    i386|i486|i586|i686|x86) ARCH_NOTE="32-bit Intel" ;;
    aarch64|arm64) ARCH_NOTE="64-bit ARM" ;;
    armv6l|armv7l|armhf|arm) ARCH_NOTE="32-bit ARM" ;;
    *) die "unsupported machine: $ARCH (the lite payload needs Linux on x86 or ARM)" 2 ;;
esac

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done

check_report() {
    say "Updater CE lite check"
    say "  machine     $ARCH ($ARCH_NOTE)"
    if [ -n "$PYTHON" ]; then
        say "  python      $("$PYTHON" -c 'import platform; print(platform.python_version())') ($PYTHON)"
    else
        say "  python      MISSING (3.8 or newer is required)"
    fi
    for tool in curl wget adb mtkclient unrar 7z; do
        if command -v "$tool" >/dev/null 2>&1; then say "  $tool$(printf '%*s' $((12 - ${#tool})) '')$(command -v "$tool")"; fi
    done
    if [ -d "$TARGET_DIR" ]; then say "  installed   $TARGET_DIR"; else say "  installed   no"; fi
    [ -n "$PYTHON" ] || return 1
    return 0
}

if [ "$MODE" = "check" ]; then
    check_report
    exit $?
fi

if [ "$MODE" = "uninstall" ]; then
    rm -rf "$TARGET_DIR"
    rm -f "$BIN_DIR/innioasis-updater-lite"
    say "Removed $TARGET_DIR and the innioasis-updater-lite launcher."
    exit 0
fi

[ -n "$PYTHON" ] || die "Python 3.8+ is required. Install it first:
  Debian/Ubuntu/Mint: sudo apt install python3
  Fedora:             sudo dnf install python3
  openSUSE:           sudo zypper install python3
  Arch/CachyOS:       sudo pacman -S python
  Alpine:             sudo apk add python3" 3

if [ "$MODE" = "update" ] && [ ! -d "$TARGET_DIR" ]; then
    say "Nothing installed yet; doing a fresh install."
fi

TMP="${TMPDIR:-/tmp}/innioasis-lite-$$"
mkdir -p "$TMP" || die "cannot create a temporary folder" 1
trap 'rm -rf "$TMP"' EXIT HUP INT TERM

fetch() {
    # fetch URL DEST — resume-aware, quiet, and honest about failure.
    _url="$1"; _dest="$2"
    if command -v curl >/dev/null 2>&1; then
        if [ "$QUIET" -eq 1 ]; then
            curl -fL --retry 4 --retry-delay 3 -C - -o "$_dest" "$_url" 2>/dev/null
        else
            curl -fL --retry 4 --retry-delay 3 -C - --progress-bar -o "$_dest" "$_url"
        fi
    elif command -v wget >/dev/null 2>&1; then
        wget -c -O "$_dest" "$_url" 2>/dev/null || wget -O "$_dest" "$_url" 2>/dev/null
    else
        "$PYTHON" - "$_url" "$_dest" <<'PY'
import sys, urllib.request
url, dest = sys.argv[1], sys.argv[2]
req = urllib.request.Request(url, headers={"User-Agent": "UpdaterCE-lite"})
with urllib.request.urlopen(req, timeout=120) as src, open(dest, "wb") as out:
    while True:
        block = src.read(1 << 20)
        if not block:
            break
        out.write(block)
PY
    fi
}

ARCHIVE="$TMP/$ASSET_NAME"

if [ -n "$LOCAL_FILE" ]; then
    [ -f "$LOCAL_FILE" ] || die "no such file: $LOCAL_FILE" 4
    cp "$LOCAL_FILE" "$ARCHIVE" || die "could not read $LOCAL_FILE" 4
    say "Using local archive: $LOCAL_FILE"
else
    say "Updater CE 2.5.4 — temporary terminal session"
    say "1/4  Checking Python ($ARCH_NOTE)"
    say "2/4  Fetching the release index…"
    # The asset URL changes with every release, so ask the API. A hand-written
    # JSON scan keeps this working without jq or any Python module beyond the
    # standard library. If GitHub is unreachable, the copy on innioasis.app is
    # used instead.
    if ! "$PYTHON" - "$RELEASE_API" "$TMP/urls.txt" <<'PY'
import json, sys, urllib.request
api, out = sys.argv[1], sys.argv[2]
req = urllib.request.Request(api, headers={"User-Agent": "UpdaterCE-lite"})
with urllib.request.urlopen(req, timeout=30) as response:
    release = json.loads(response.read())
found = {}
for asset in release.get("assets", []):
    name = asset.get("name", "")
    if name == "UpdaterCE-Lite.tar.gz":
        found["archive"] = asset["browser_download_url"]
    elif name == "UpdaterCE-Lite.tar.gz.sha256":
        found["sha256"] = asset["browser_download_url"]
if "archive" not in found:
    raise SystemExit("the newest release has no UpdaterCE-Lite.tar.gz asset")
with open(out, "w", encoding="utf-8") as handle:
    handle.write(found["archive"] + "\n" + found.get("sha256", "") + "\n")
PY
    then
        URL_ARCHIVE="$(sed -n '1p' "$TMP/urls.txt")"
        URL_SHA="$(sed -n '2p' "$TMP/urls.txt")"
    else
        say "GitHub did not answer. Using the copy on innioasis.app."
        URL_ARCHIVE="$SITE/UpdaterCE-Lite.tar.gz"
        URL_SHA="$SITE/UpdaterCE-Lite.tar.gz.sha256"
    fi
    say "3/4  Downloading (resumable; rerun this command if the connection drops)…"
    fetch "$URL_ARCHIVE" "$ARCHIVE" || die "download failed; the partial file is kept in $TMP until this shell exits" 4
    if [ "$VERIFY" -eq 1 ] && [ -n "$URL_SHA" ]; then
        fetch "$URL_SHA" "$TMP/sha256" 2>/dev/null || true
        if [ -s "$TMP/sha256" ]; then
            EXPECTED="$(awk '{print $1}' "$TMP/sha256" | head -1)"
            ACTUAL="$("$PYTHON" - "$ARCHIVE" <<'PY'
import hashlib, sys
digest = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for block in iter(lambda: handle.read(1 << 20), b""):
        digest.update(block)
print(digest.hexdigest())
PY
)"
            [ "$EXPECTED" = "$ACTUAL" ] || die "checksum mismatch: expected $EXPECTED, got $ACTUAL" 4
            say "Checksum verified."
        fi
    fi
fi

mkdir -p "$TARGET_DIR" || die "cannot create $TARGET_DIR" 1
"$PYTHON" - "$ARCHIVE" "$TARGET_DIR" <<'PY' || die "could not unpack the lite payload" 4
import sys, tarfile
archive, dest = sys.argv[1], sys.argv[2]
with tarfile.open(archive) as bundle:
    for member in bundle.getmembers():
        if member.name.startswith("/") or ".." in member.name.split("/"):
            raise SystemExit("unsafe path in archive: " + member.name)
    bundle.extractall(dest)
PY

mkdir -p "$BIN_DIR" || die "cannot create $BIN_DIR" 1
cat > "$BIN_DIR/innioasis-updater-lite" <<LAUNCHER
#!/bin/sh
# Generated by run_linux_lite.sh — starts the Updater CE terminal front end.
exec "$(command -v "$PYTHON")" "$TARGET_DIR/lite.py" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/innioasis-updater-lite"

say ""
say "Installed to $TARGET_DIR"
say "Launcher:    $BIN_DIR/innioasis-updater-lite"
say ""
say "  innioasis-updater-lite list --model Y1"
say "  innioasis-updater-lite download Y1 --output ~/firmware --extract"
say "  innioasis-updater-lite flash ~/firmware/rom_Y1 --dry-run"
say "  innioasis-updater-lite tui"
say ""
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "Add $BIN_DIR to PATH to run it by name, or call the full path above." ;;
esac

if [ "$OPEN_TUI" -eq 1 ]; then
    say "4/4  Opening the terminal session."
    exec "$BIN_DIR/innioasis-updater-lite" tui
fi
exit 0
