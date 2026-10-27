# Build targets — every platform and CPU architecture

One page of copy-paste build commands for each target this project ships.
[BUILDING.md](BUILDING.md) explains *why* each platform uses the backend it
does; this file is the command reference.

A normal build produces **both** front ends side by side (Updater CE and the
generic MediaTek Installer). Add a brand to build just one:
`--brand updater_ce` / `--brand mediatek_installer` (macOS, Linux) or
`updater_ce` / `mediatek_installer` (Windows).

| Platform | Architecture | Command | Output |
| :--- | :--- | :--- | :--- |
| macOS | Apple Silicon (`arm64`) | `./build_macos.sh --arch arm64` | `dist/macos/Updater CE.app` |
| macOS | Intel (`x86_64`) | `./build_macos.sh --arch x86_64` | `dist/macos/Updater CE.app` |
| macOS | **Universal 2** (`arm64` + `x86_64`) | `./build_macos.sh --arch universal2 --dmg` | `dist/macos/Updater CE.app`, `dist/macos/UpdaterCE-*-universal2-macOS.dmg` |
| Windows | x64 (`x86_64`) | `build_windows.bat` | `dist\windows\InnioasisUpdater\`, `dist\windows\UpdaterCE-Portable-*.zip`, `dist\windows\UpdaterCE-Setup-*.exe` |
| Windows | ARM64 (`aarch64`) | `build_windows.ps1` in an **arm64** Python | `dist\windows\InnioasisUpdater\`, portable `.zip`, setup `.exe` |
| Linux | x86-64 | `./build_appimage.sh --arch x86_64` | `dist/linux/UpdaterCE-*-x86_64.AppImage` |
| Linux | ARM64 (`aarch64`) | `./build_appimage.sh --arch aarch64` | `dist/linux/UpdaterCE-*-aarch64.AppImage` |
| Linux | ARM32 (`armhf`/`armv7`) | `./build_appimage.sh --arch armhf` | best-effort: needs an `armhf` appimagetool and an armhf Python |

Not supported: 32-bit Windows (`x86`/`arm32`) — PySide6 and the vendored
mtkclient loaders are 64-bit only.

---

## macOS — `.app` and `.dmg`

```bash
chmod +x build_macos.sh

./build_macos.sh                       # host architecture (arm64 on Apple Silicon)
./build_macos.sh --arch arm64          # Apple Silicon only
./build_macos.sh --arch x86_64         # Intel only
./build_macos.sh --arch universal2     # Intel + Apple Silicon in one bundle
./build_macos.sh --arch universal2 --dmg   # …plus a drag-and-drop DMG
```

- On a Mac the bundle is built and ad-hoc signed by PyInstaller. On a
  non-MacOS host the same command builds the Universal 2 Mach-O bundle through
  `scripts/build_universal_app.py` (LLVM/clang + `rcodesign`), which merges two
  per-arch PyInstaller trees with `lipo`.
- Both macOS front ends are written to `dist/macos/`; a single-brand build leaves the
  other app's bundle alone.

Verify a Universal 2 bundle really carries both slices before shipping:

```bash
python3 scripts/audit_universal2.py "dist/macos/Updater CE.app"
```

The audit exits non-zero if any Mach-O is missing an `x86_64` or `arm64` slice
or asks for a newer minimum macOS than 12.0 (Monterey). Override with
`--max-minos 12.0`.

The macOS flash backend is MTKClient, which needs
`vendor/mtkclient/mtkclient/Darwin/libusb-1.0.dylib`; `build_macos.sh` creates a
universal `libusb-1.0.dylib` if it is missing.

---

## Windows — portable folder and Inno Setup wizard

```cmd
build_windows.bat                       :: both front ends at x64
build_windows.bat updater_ce            :: Updater CE only
build_windows.bat mediatek_installer    :: MediaTek Installer only
```

```powershell
.\build_windows.ps1                     # both front ends
.\build_windows.ps1 -Clean              # wipe build/windows + dist/windows first
.\build_windows.ps1 -NoInstaller        # portable folder + zip only, skip Inno Setup
.\build_windows.ps1 -Brand mediatek_installer
```

- Output: `dist\windows\InnioasisUpdater\InnioasisUpdater.exe` (portable, with the
  bundled SP Flash Tool), `dist\windows\UpdaterCE-Portable-<version>.zip`, and
  `dist\windows\UpdaterCE-Setup-<version>.exe` when `ISCC.exe` (Inno Setup 6)
  is installed.
- Architecture follows the interpreter that runs PyInstaller: run the scripts
  with an **arm64** Python to produce a Windows ARM64 build, or with an x64
  Python for x64. There is no cross-compilation flag.
- Both `build_windows.bat` and `build_windows.ps1` accept the brand and `-Clean` / `-NoInstaller`.

---

## Linux — AppImage

```bash
chmod +x build_appimage.sh

./build_appimage.sh                     # x86_64 (default)
./build_appimage.sh --arch aarch64      # ARM64
./build_appimage.sh --arch armhf        # ARM32, needs an armhf Python + appimagetool
./build_appimage.sh --clean             # rebuild from scratch
./build_appimage.sh --no-pyinstaller    # repackage an existing dist/linux/ tree
./build_appimage.sh --brand updater_ce  # Updater CE only
```

- Output: `dist/linux/InnioasisUpdater-<version>-<arch>.AppImage` (alias `UpdaterCE.appimage` / `UpdaterCE.AppImage`) and `dist/linux/MediaTekInstaller-<version>-<arch>.AppImage` (alias `MediaTek Installer.appimage`), each carrying the full stack:
  - Customized SP Flash Tool (`tools/linux/SP_Flash_Tool_v5.1904_Linux`, on x86_64)
  - Precompiled MTKClient with bundled `libusb-1.0`
  - Vendored ADB (`tools/adb`, on `PATH` for both x86_64 and aarch64)
  - Automated udev rules & serial group setup helpers
- Rebuild customized SP Flash Tool for Linux at any time with `./tools/build_linux_sp_flash.sh`.
- The arch also selects `appimagetool-<arch>.AppImage`; both `x86_64` and `aarch64` are fully supported.
- Linux builds on x86-64 use SP Flash Tool by default (with MTKClient as alternate backend); ARM64 builds use native MTKClient.

---

## Building everything from one machine

Only macOS and Linux can produce a fat (Universal 2) binary; Windows and
Linux produce single-architecture artifacts, so a full release is three runs:

```bash
./build_macos.sh --arch universal2 --dmg      # macOS Intel + Apple Silicon
./build_appimage.sh --arch x86_64             # Linux x86-64
./build_appimage.sh --arch aarch64            # Linux ARM64
```

```cmd
build_windows.bat                             :: Windows x64
```

Each script cleans only the artifacts it is about to replace, so running them
in sequence (on their own platforms) leaves a complete `dist/`.
