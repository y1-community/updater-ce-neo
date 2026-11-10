#!/usr/bin/env bash
# The Linux and macOS installers share their options and hand over to each
# other, so one download link works on either machine. This checks that the
# handoff is in place, that both scripts parse, and that the macOS check runs
# without downloading anything or asking for a password.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

bash -n "$ROOT/run_linux.sh"
bash -n "$ROOT/run_mac.sh"
echo 'PASS both installers parse'

# The package-manager table must cover the managers users actually have,
# including the two macOS ones, and must not stop at "no supported manager".
for manager in apt-get dnf zypper pacman apk xbps-install emerge rpm-ostree brew port; do
    grep -q "$manager" "$ROOT/run_linux.sh" || {
        echo "run_linux.sh does not handle $manager" >&2
        exit 1
    }
done
grep -q 'xattr -dr com.apple.quarantine' "$ROOT/run_mac.sh"
grep -q 'handoff_other_os' "$ROOT/run_linux.sh"
grep -q 'handoff_other_os' "$ROOT/run_mac.sh"
echo 'PASS package managers and cross-platform handoff are present'

# An unknown platform name is refused instead of silently pretending to be
# Linux on a Mac.
if INNIOASIS_OPSYS=plan9 bash "$ROOT/run_linux.sh" --check >/dev/null 2>&1; then
    echo 'run_linux.sh accepted an unknown INNIOASIS_OPSYS' >&2
    exit 1
fi

# --check is local-only: no network, no prompt, no install.
HOME="$TMP/home"
export HOME
mkdir -p "$HOME"
out="$(bash "$ROOT/run_mac.sh" --check 2>&1)" || {
    printf '%s\n' "$out" >&2
    echo 'run_mac.sh --check reported a problem' >&2
    exit 1
}
case "$out" in
    *macOS*) ;;
    *)
        printf '%s\n' "$out" >&2
        echo 'run_mac.sh --check did not describe the macOS host' >&2
        exit 1
        ;;
esac

# On a Mac, the Linux script must hand over rather than looking for apt/dnf.
if [ "$(uname -s)" = "Darwin" ]; then
    out="$(bash "$ROOT/run_linux.sh" --check 2>&1)" || {
        printf '%s\n' "$out" >&2
        echo 'run_linux.sh did not hand over to run_mac.sh' >&2
        exit 1
    }
    case "$out" in
        *macOS*) ;;
        *)
            printf '%s\n' "$out" >&2
            echo 'run_linux.sh --check did not reach the macOS check' >&2
            exit 1
            ;;
    esac
fi

echo 'PASS installer platform handoff'
