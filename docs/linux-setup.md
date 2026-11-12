# Linux setup

The window and the terminal session are one Python tree. `launcher.py` opens the window. `src/console` and `src/tui_session.py` open the text session from that same source. `scripts/build_lite_payload.py` packs the text session, without Qt, into `UpdaterCE-Lite.tar.gz`.

## The question both scripts ask

`run_linux.sh` and `run_linux_lite.sh` ask:

1. Install Innioasis Updater (full app) — this is the recommended choice
2. Run once in Terminal

Choosing the full app from `run_linux_lite.sh` runs `run_linux.sh`. Choosing the terminal from `run_linux.sh` runs `run_linux_lite.sh`. A piped install with no terminal chooses the full app. `INNIOASIS_CHOICE=full` or `INNIOASIS_CHOICE=terminal` skips the question, so the hand-off does not ask twice. `--check`, `--repair`, `--uninstall`, and `--file` do not ask.

## What the full installer tries

People should be able to download a portable zip, tarball, or AppImage and run it without a terminal. The script is the fallback for a machine that does not already have one of those files.

It tries, in order:

1. The AppImage published for this architecture.
2. The portable `.tar.gz`, then the portable `.zip`, published for this architecture. A name containing `lite` or `terminal` is the text session, not this package.
3. The source zip of the newest release that contains `launcher.py` or `firmware_downloader.py`. The repos, in order, are `y1-community/updater-ce-neo`, `y1-community/innioasis-updater`, `ryan-specter/updater-ce-neo`, and `ryan-specter/innioasis-updater`. The installer makes a per-user virtualenv and installs `requirements.txt` into it. If the Qt front end has no wheel for that Python, it installs the rest of `requirements.txt` so the terminal interface still works. `y1-community/innioasis-updater` is the website; its zip is used only when the tree is actually the app.
4. The terminal interface of whatever install exists, with a printed explanation of what is missing.

Before the download it offers to install the libraries the window needs (xcb, FUSE, libusb, and a Python venv) plus python3/curl when they are absent. It reads the package manager this machine actually has:

| System | Manager used |
|---|---|
| Debian, Ubuntu, Mint, Pop!_OS, Zorin, Deepin, Kali | `apt-get` |
| Fedora, RHEL, Rocky, AlmaLinux, Nobara | `dnf` (or `yum`) |
| openSUSE, SLES | `zypper` |
| Arch, CachyOS, Omarchy, Manjaro, EndeavourOS, SteamOS | `pacman` |
| Alpine, postmarketOS | `apk` |
| Void | `xbps-install` |
| Gentoo, Funtoo | `emerge` |
| Fedora Silverblue/Kinoite, Bazzite and other immutable images | `rpm-ostree` (reports the reboot it needs) |
| NixOS and Guix | guidance for `appimage-run` / a `guix shell`, not a silent host change |
| Any Linux with Homebrew, and macOS | `brew` |
| macOS with MacPorts | `port` |

The default answer is yes. A system with no recognised manager, an immutable image that cannot take the packages without a reboot, and a rootless account without sudo/pkexec/doas all get the exact commands printed, then the terminal interface — the installer no longer stops at `No supported package manager found`.

## One script on Linux and macOS

`run_linux.sh` and `run_mac.sh` accept the same options and hand over to each other: `run_linux.sh` on macOS runs `run_mac.sh`, and `run_mac.sh` on Linux runs `run_linux.sh`. One download link therefore works on either system, and a machine that gets the wrong script corrects itself instead of looking for apt on a Mac. `INNIOASIS_OPSYS=linux` or `INNIOASIS_OPSYS=darwin` chooses one explicitly; a second hand-off is refused rather than looping. See [macos-setup.md](macos-setup.md).

## Where files are published

The archive is not stored in the git repository. `dist/` is build output.

The Linux 2.5.5 window packages are published on the **2.5.5** release in `y1-community/updater-ce-neo` (updates) and on the existing **2.5.3** release in `y1-community/innioasis-updater` (first installs). These files on those releases stay as they are:

- `Innioasis.Updater.for.Mac.dmg`
- `installer.exe`
- `UpdaterCE-Windows-Portable.zip`

The Linux files beside them are replaced with the tested 2.5.5 build:
`UpdaterCE.appimage`, `UpdaterCE-Linux-Portable.tar.gz`, `UpdaterCE-Linux-Portable.zip`,
their arm64 equivalents, and matching `.sha256` files. The Windows and macOS
files are never replaced by a Linux upload.

The source tree's version is 2.5.5, and that is what the terminal session reports. The 2.5.5 tag is the Linux update release in `updater-ce-neo`; the older 2.5.3 tag remains the first-install location in `innioasis-updater`.

`run_linux_lite.sh` chooses its source before downloading: a first install reads
`y1-community/innioasis-updater`, while an existing terminal install or
`--update` reads `y1-community/updater-ce-neo`. Each lookup falls back to the
other repository. The update checker ignores asset names containing `lite` or
`terminal`, so that archive cannot be selected as the Linux app update.

## Linux build guarantees

The Linux installer understands Homebrew on Linux too (`~/.linuxbrew`, `/home/linuxbrew/.linuxbrew`, `/opt/homebrew`): it is used only when no native manager exists, because the distribution's own packages are the safer fix.

Both Linux architectures are built in Ubuntu 22.04 with Python 3.10. The bundle
verifier rejects a glibc floor above 2.35, a Python newer than 3.10, a declared
x86-64-v2/v3 requirement, or a missing Qt XCB client library. The AppImage,
portable tar.gz and zip carry the same `_internal` tree and `run_linux.sh`, so
the AppImage's repair path works without a separate download. When no package
matches the machine, the installer still lays the app out from source.

See `docs/RELEASE_FILES.md` and `docs/terminal-mode.md`.
