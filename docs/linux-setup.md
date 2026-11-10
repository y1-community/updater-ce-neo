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
2. The portable `.tar.gz` or `.zip` published for this architecture. A name containing `lite` or `terminal` is the text session, not this package.
3. The source zip of the newest release that contains `launcher.py` or `firmware_downloader.py`. The repos, in order, are `y1-community/updater-ce-neo`, `y1-community/innioasis-updater`, and `ryan-specter/innioasis-updater`. The installer makes a per-user virtualenv and installs `requirements.txt` into it. `y1-community/innioasis-updater` is the website; its zip is used only when the tree is actually the app.

Before the download it offers to install the libraries the window needs (xcb, FUSE, libusb, and a Python venv) with apt, dnf, zypper, pacman, or apk. The default answer is yes. Immutable images that cannot take those packages still get the prebuilt file when one is published.

## Where files are published

The archive is not stored in the git repository. `dist/` is build output.

`UpdaterCE-Lite.tar.gz` and `UpdaterCE-Lite.tar.gz.sha256` are extra assets on the existing GitHub release **2.5.3** of `y1-community/updater-ce-neo`. These files on that release stay as they are:

- `Innioasis.Updater.for.Mac.dmg`
- `installer.exe`
- `UpdaterCE-Windows-Portable.zip`
- `UpdaterCE.appimage`

There is no 2.5.4 release tag. Installed Windows and Mac copies compare release tags, and a higher tag that also contains their package would tell them an update exists. The source tree's version is 2.5.4, and that is what the terminal session reports. It is not a GitHub release.

`run_linux_lite.sh` reads `/releases/tags/2.5.3` first, then any release that carries `UpdaterCE-Lite.tar.gz`, then the copy on innioasis.app. The update checker ignores asset names containing `lite` or `terminal`, so that archive cannot be selected as the Linux app update.

## Later Linux builds

A newer Linux AppImage, portable zip, and portable tar.gz can be added to the **2.5.3** tag after they have been tested. They are added beside the files already there. The Windows and Mac files are not replaced, and the release is not retagged. Until that test, those packages are not uploaded. When none of them match the machine, the installer lays the app out from source.

See `docs/RELEASE_FILES.md` and `docs/terminal-mode.md`.
