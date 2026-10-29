# Release file names

Every GitHub release is named by version tag. The app and the Linux installer
read these exact file names to decide what to offer each machine.

## Staggered releases

A newer release does not have to ship files for every platform. The update
check and `run_linux.sh` walk the releases newest first and use the first
release that has a file for the machine's platform and architecture. A machine
with no matching file in the newest release stays on the previous one.

## Approved names

| Platform | Architecture | File |
|---|---|---|
| Windows | x64 | `installer.exe` (also `UpdaterCE-Windows-Portable.zip`) |
| macOS | Universal 2 | `Innioasis.Updater.for.Mac.dmg` (also `Innioasis.Updater.for.Mac.zip`, the app copy used by the firmware packer) |
| Linux | x86-64 | `UpdaterCE.appimage`, `UpdaterCE-Linux-Portable.tar.gz`, `UpdaterCE-Linux-Portable.zip` |
| Linux | arm64 | `UpdaterCE-arm64.appimage`, `UpdaterCE-Linux-Portable-arm64.tar.gz`, `UpdaterCE-Linux-Portable-arm64.zip` |

x86-64 names carry no architecture tag. arm64 names carry `-arm64`.
Each file needs a matching `.sha256`.

The Linux installer script `run_linux.sh` is published alongside these files.

## Rules

- An arm64 build is offered only to arm64 machines. An x86-64 build is offered
  only to x86-64 machines. Untagged Linux names are x86-64.
- The AppImage is the preferred Linux download. The portable `.tar.gz` is used
  when AppImage is not wanted (no FUSE).
- The x86-64 build must run on older CPUs such as the Core 2 Duo. This has not
  been tested yet.
