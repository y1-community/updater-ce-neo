# Release file names

These names are read by three things:

- the in-app updater (`src/updates.py`, `src/config.py`),
- the first-time Linux installer (`run_linux.sh`),
- the download page on innioasis.app.

Match them exactly, or updates and first-time installs will not find the file.

Asset matching is case-insensitive. Where a release has several candidates, the
updater ranks arch-tagged names by token: `universal`, `x86_64`, `amd64`,
`arm64`, `aarch64`, in that order. An untagged name ranks below all of them.

## Linux (`y1-community/updater-ce-neo`)

| Purpose | File name | Architecture |
| --- | --- | --- |
| AppImage, first install and auto-update | `UpdaterCE.appimage` | x86-64 (Intel/AMD) |
| AppImage, first install and auto-update | `UpdaterCE-arm64.appimage` | arm64 (aarch64) |
| Portable archive, first install (no FUSE needed) | `UpdaterCE.tar.gz` | x86-64 |
| Portable archive | `UpdaterCE-arm64.tar.gz` | arm64 (aarch64) |
| Installer script | `run_linux.sh` | any |

Also upload a `.sha256` file next to each download.

### Known problems

These are not fixed yet. Fix them before the next Linux release.

1. **Exact-name match ignores arch (updater bug).** `updates.py` step 1 returns
   an exact match for `updaterce.appimage` before it looks at arch tags. If a
   release carries both `UpdaterCE.appimage` (x86-64) and
   `UpdaterCE-arm64.appimage`, an arm64 install is offered the x86-64 file.
   Until the code is fixed, do not publish an untagged `UpdaterCE.appimage`
   together with an arm64 AppImage in the same release.
2. **arm64 tar.gz name mismatch (installer script).** `run_linux.sh` hard-codes
   `UpdaterCE-aarch64.tar.gz` as the arm64 fallback URL. This document uses
   `UpdaterCE-arm64.tar.gz`. The script's search regex accepts both, but the
   fallback URL only works with `-aarch64`. Pick one name and make the script
   match it.
3. **Build script overwrites the stable aliases.** `build_appimage.sh` copies
   `UpdaterCE.appimage` and `UpdaterCE.tar.gz` for every `--arch`, so an arm64
   build run replaces the x86-64 aliases. Build the arm64 files on their own
   and rename them to `UpdaterCE-arm64.*` before upload.

### Compatibility

- The x86-64 AppImage must run on older CPUs such as Core 2 Duo under modern
  distributions. Build it with an x86-64 baseline, with no newer-CPU-only
  flags, and test it on a Core 2 Duo-class machine before release. This is not
  yet verified.

## Windows

The updater accepts `installer.exe`, `updaterce-setup.exe`, or any `.exe`
whose name starts with `installer` or contains `setup`. Use
`UpdaterCE-Setup.exe` or `updaterce-setup.exe`.

## macOS

| Purpose | File name |
| --- | --- |
| Disk image | `innioasis.updater.for.mac.dmg` (case-insensitive) |

The updater only offers a `.dmg` on macOS. Use the same name for Apple Silicon
and Intel, or ship a universal2 build under this one name.
