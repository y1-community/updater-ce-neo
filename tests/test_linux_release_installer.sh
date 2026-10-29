#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
HOME="$TMP/home with spaces"
export HOME
export XDG_DATA_HOME="$TMP/data with spaces"
export XDG_CONFIG_HOME="$TMP/config"
export XDG_CACHE_HOME="$TMP/cache"
mkdir -p "$HOME" "$TMP/stage/InnioasisUpdater/_internal"
printf '#!/bin/sh\necho updater-test\n' > "$TMP/stage/InnioasisUpdater/InnioasisUpdater"
chmod 0755 "$TMP/stage/InnioasisUpdater/InnioasisUpdater"
printf 'icon' > "$TMP/stage/InnioasisUpdater/innioasis-updater.png"
mkdir -p "$TMP/stage/InnioasisUpdater/SP_Flash_Tool"
printf 'runtime' > "$TMP/stage/InnioasisUpdater/SP_Flash_Tool/payload"

tar -C "$TMP/stage" -czf "$TMP/UpdaterCE.tar.gz" InnioasisUpdater
bash "$ROOT/run_linux.sh" --file "$TMP/UpdaterCE.tar.gz"

INSTALL="$XDG_DATA_HOME/innioasis-updater/app"
LAUNCHER="$HOME/.local/bin/innioasis-updater"
DESKTOP="$XDG_DATA_HOME/applications/innioasis-updater.desktop"
test -x "$INSTALL/InnioasisUpdater"
test -f "$INSTALL/SP_Flash_Tool/payload"
test -x "$LAUNCHER"
test -f "$XDG_DATA_HOME/icons/hicolor/256x256/apps/innioasis-updater.png"
grep -F 'Exec="' "$DESKTOP" | grep -F "$HOME/.local/bin/innioasis-updater" | grep -F ' %U'
grep -F 'Icon=innioasis-updater' "$DESKTOP"
test "$("$LAUNCHER")" = updater-test

# Settings are QSettings("innioasis", "updater") in ~/.config and must survive
# an upgrade over an existing install.
mkdir -p "$XDG_CONFIG_HOME/innioasis"
printf '[General]\nlanguage=fr\n' > "$XDG_CONFIG_HOME/innioasis/updater.conf"
bash "$ROOT/run_linux.sh" --file "$TMP/UpdaterCE.tar.gz"
test "$(cat "$XDG_CONFIG_HOME/innioasis/updater.conf")" = "$(printf '[General]\nlanguage=fr')"

# With no XDG_DATA_HOME, use the freedesktop default under ~/.local/share.
HOME="$TMP/default-home"
mkdir -p "$HOME"
unset XDG_DATA_HOME
export HOME
bash "$ROOT/run_linux.sh" --file "$TMP/UpdaterCE.tar.gz" --no-shortcut
test -x "$HOME/.local/share/innioasis-updater/app/InnioasisUpdater"
test -x "$HOME/.local/bin/innioasis-updater"
test ! -e "$HOME/.local/share/applications/innioasis-updater.desktop"

# A broken archive is rejected before the existing working tree is touched.
printf 'not a tar archive\n' > "$TMP/broken.tar.gz"
if bash "$ROOT/run_linux.sh" --file "$TMP/broken.tar.gz"; then
    echo 'broken archive unexpectedly installed' >&2
    exit 1
fi
test -x "$HOME/.local/share/innioasis-updater/app/InnioasisUpdater"

echo 'PASS run_linux per-user XDG install'
