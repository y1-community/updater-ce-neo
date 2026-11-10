# macOS setup

`run_mac.sh` installs the same app as `run_linux.sh`, using the macOS package
on the newest release. It needs no administrator rights: the `.app` goes into
`~/Applications`, the launcher into `~/.local/bin`, and the app carries its own
Python, Qt and libusb.

```sh
curl -fsSLO https://innioasis.app/run_mac.sh
bash run_mac.sh
```

`run_mac.sh` is a bash script, not a `.command`; run it with `bash`, or make it
executable once (`chmod +x run_mac.sh`) and run `./run_mac.sh`.

## What it tries

1. `Innioasis.Updater.for.Mac.dmg` from the newest release, verified against
   its published `.sha256` when one exists. The disk image is mounted read-only,
   the `.app` is copied with `ditto`, and the image is detached.
2. `Innioasis.Updater.for.Mac.zip` (or another macOS-named zip) on the same
   release, unpacked with `ditto`.
3. The newest release's source archive in a per-user virtualenv under
   `~/.local/share/innioasis-updater/source`, installing `requirements.txt`.
   This is the same Python the `.app` is built from.
4. If the Qt front end cannot be installed (for example a Python version with
   no PySide6 wheel), the rest of `requirements.txt` is installed and the
   terminal interface is used.
5. If there is no usable Python, the installer prints the Homebrew or MacPorts
   command that provides one and leaves the terminal instructions in place
   instead of failing silently.

The macOS package names, and the rule that a newer release without a macOS
file is skipped, are in [RELEASE_FILES.md](RELEASE_FILES.md).

## Package managers

The installer recognises both package managers people actually use on a Mac:

| Manager | Detection | Install command |
|---|---|---|
| Homebrew | `brew` on `PATH`, `/opt/homebrew/bin/brew`, `/usr/local/bin/brew` | `brew install python libusb` (no password) |
| MacPorts | `port` on `PATH` | `sudo port install python312 libusb` |
| Apple Command Line Tools | `xcode-select -p` and `/usr/bin/python3` | `xcode-select --install` |

Python is only needed for the source fallback. A normal `.app` install needs no
host packages at all, and `--check` says so. `--repair` installs Python through
Homebrew or MacPorts after asking, or prints the `xcode-select --install`
command when neither is present.

## Options

```sh
bash run_mac.sh --check          # macOS version, Python, brew, ports, install state
bash run_mac.sh --file path.dmg  # install a local .dmg, .zip or .app
bash run_mac.sh --zip            # prefer the .zip when downloading
bash run_mac.sh --update         # newest release over the current copy
bash run_mac.sh --tui            # install, then open the terminal interface
bash run_mac.sh --repair --yes   # install a usable Python without asking
bash run_mac.sh --uninstall      # remove the app and launcher, keep settings
```

`--dmg` (the default), `--dequarantine` and `--skip-host-check` are also
accepted. `--skip-udev` and `--no-shortcut` exist only so the Linux options can
be repeated verbatim; macOS needs no udev rule and has no desktop entry.

## The window check

After installing, the installer runs the app with `--gui-probe` for up to 45
seconds. A missing window is reported with the beginning of the runtime log;
the terminal interface is then started instead. `--skip-host-check` skips the
probe, which is what automated tests use.

If the copy came from a browser download, macOS sets `com.apple.quarantine` and
may refuse to open the app. The installer warns and prints the exact command:

```sh
xattr -dr com.apple.quarantine "$HOME/Applications/Innioasis Updater.app"
```

`--dequarantine` does it during the install. A download performed by the script
itself is not quarantined.

## Linux and macOS share one link

`run_mac.sh` on Linux runs `run_linux.sh`, and `run_linux.sh` on macOS runs
`run_mac.sh`. Both accept the same options, so a single download URL can be
published for either machine. `INNIOASIS_OPSYS=linux` or
`INNIOASIS_OPSYS=darwin` selects one explicitly; a second hand-off is refused
rather than looping between the two. The Linux side is described in
[linux-setup.md](linux-setup.md).

## Packaging the app

The `.app` is built by `./build_macos.sh` (see `BUILDING.md`). The terminal
interface does not need Qt and is started with `--tui` from the same install.
