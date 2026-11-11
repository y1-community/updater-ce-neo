# Build targets — every platform and CPU architecture

One page of copy-paste build commands for each target this project ships.
[BUILDING.md](BUILDING.md) explains *why* each platform uses the backend it
does; this file is the command reference.

A normal build produces **Updater CE** only. The generic MediaTek Installer is
opt-in: `--brand mediatek_installer` (or `--brand all` for both) on macOS and
Linux, `mediatek_installer` / `both` on Windows.

Every build script first runs `scripts/bootstrap_build_env.py`. On a fresh clone
that creates `.venv-build/` and installs `requirements.txt` +
`requirements-build.txt`; afterwards it reuses that environment, so no manual
virtualenv or `pip install` step is needed on any platform. `./build_macos.sh
--check-deps`, `python3 scripts/bootstrap_build_env.py --check` (macOS/Linux) and
`scripts\bootstrap_build_env.py --check` (Windows) verify without installing.

| Platform | Architecture | Command | Output |
| :--- | :--- | :--- | :--- |
| macOS | Apple Silicon (`arm64`) | `./build_macos.sh --arch arm64` | `dist/macos/Innioasis Updater.app` |
| macOS | Intel (`x86_64`) | `./build_macos.sh --arch x86_64` | `dist/macos/Innioasis Updater.app` |
| macOS | **Universal 2** (`arm64` + `x86_64`) | `./build_macos.sh --arch universal2 --dmg` | `dist/macos/Innioasis Updater.app`, `dist/macos/InnioasisUpdater-*-universal2-macOS.dmg` |
| Windows | x64 (`x86_64`) | `build_windows.bat` or `build_windows.ps1` | `dist\windows\InnioasisUpdater\`, `dist\windows\UpdaterCE-Setup-*.exe` |
| Windows | ARM64 (`aarch64`) | `build_windows.bat --arch arm64` or `.\build_windows.ps1 -Arch arm64` | `dist\windows-arm64\InnioasisUpdater\`, setup `*-arm64.exe` |
| Linux | x86-64 | `./build_appimage.sh --arch x86_64` | `dist/linux/UpdaterCE-*-x86_64.AppImage` |
| Linux | ARM64 (`aarch64`) | `./build_appimage.sh --arch aarch64` | `dist/linux/UpdaterCE-*-aarch64.AppImage` |
| Linux | ARM32 (`armhf`/`armv7`) | `./build_appimage.sh --arch armhf` | best-effort: needs an `armhf` appimagetool and an armhf Python |

Not supported: 32-bit Windows (`x86`/`arm32`) — PySide6 and the vendored
mtkclient loaders are 64-bit only.

The two Windows architectures build into separate folders — `dist\windows` for
x64 and `dist\windows-arm64` for ARM64 — so building one never overwrites the
other. Each needs a native interpreter: PyInstaller freezes for the Python that
runs it and **cannot cross-compile**, so an ARM64 package is produced on
Windows on ARM (a Snapdragon PC/VM, or a `windows-11-arm` CI runner), not on an
Intel/AMD machine. The scripts check the interpreter's architecture and refuse
with an explanation rather than mislabelling an x64 bundle.

---

## macOS — `.app` and `.dmg`

```bash
chmod +x build_macos.sh

./build_macos.sh                       # host architecture (arm64 on Apple Silicon)
./build_macos.sh --arch arm64          # Apple Silicon only
./build_macos.sh --arch x86_64         # Intel only
./build_macos.sh --arch universal2     # Intel + Apple Silicon in one bundle
./build_macos.sh --arch universal2 --dmg   # …plus a drag-and-drop DMG
./build_macos.sh --check-deps          # verify .venv-build, install nothing
```

- The first run installs its own build environment (see the note at the top);
  `--arch` must match the architecture of the Python it runs under, because
  PyInstaller cannot cross-compile. The script says so up front instead of
  producing a mislabelled thin bundle.
- On a Mac the bundle is built and ad-hoc signed by PyInstaller. On a
  non-MacOS host the same command builds the Universal 2 Mach-O bundle through
  `scripts/build_universal_app.py` (LLVM/clang + `rcodesign`), which merges two
  per-arch PyInstaller trees with `lipo`.
- Both macOS front ends are written to `dist/macos/`; a single-brand build leaves the
  other app's bundle alone.

Verify a Universal 2 bundle really carries both slices before shipping:

```bash
python3 scripts/audit_universal2.py "dist/macos/Innioasis Updater.app"
```

The audit exits non-zero if any Mach-O is missing an `x86_64` or `arm64` slice
or asks for a newer minimum macOS than 12.0 (Monterey). Override with
`--max-minos 12.0`. It checks slices and minimum OS, not what each slice links
against; the arm64/Rosetta link check and the checklist for an actual Intel Mac
are in [BUILD_GUIDE.md](docs/BUILD_GUIDE.md) section 7.

The macOS flash backend is MTKClient, which needs
`vendor/mtkclient/mtkclient/Darwin/libusb-1.0.dylib`; `build_macos.sh` creates a
universal `libusb-1.0.dylib` if it is missing.

---

## Windows — portable folder and Inno Setup wizard

```cmd
build_windows.bat                       :: Updater CE at x64 (default) -> dist\windows
build_windows.bat --arch arm64          :: Updater CE for Windows on ARM64 -> dist\windows-arm64
build_windows.bat both                  :: both front ends at x64
build_windows.bat updater_ce            :: Updater CE only (x64)
build_windows.bat updater_ce --arch arm64 :: Updater CE only (ARM64)
build_windows.bat mediatek_installer    :: MediaTek Installer only
```

```powershell
.\build_windows.ps1                     # Updater CE (auto-detects architecture from Python)
.\build_windows.ps1 -Brand both         # both front ends
.\build_windows.ps1 -Arch arm64         # force ARM64 build (needs ARM64 Python)
.\build_windows.ps1 -Clean              # wipe this architecture's build/ and dist/ folder first
.\build_windows.ps1 -NoInstaller        # portable folder only, skip Inno Setup
.\build_windows.ps1 -Brand mediatek_installer
```

- Output, in the folder for the target architecture (`dist\windows` on x64,
  `dist\windows-arm64` on ARM64): `InnioasisUpdater\InnioasisUpdater.exe` (portable
  folder) and `UpdaterCE-Setup-<version>[-arm64].exe` when `ISCC.exe` (Inno Setup 6)
  is installed.
- The setup wizard is compiled immediately after the portable folder is staged.
  A portable ZIP is not produced; archive the finished folder yourself if needed.
- Every x64 run also stages **`dist\windows-legacy\SP_Flash_Tool\`**, a folder
  that contains SP Flash Tool and nothing else (no updater, no Python runtime),
  then zips it as `dist\windows-legacy\Innioasis-SP-Flash-Tool-windows-legacy.zip`
  with `donate.txt` and `README.txt` beside it. That package is the only route
  on Windows XP/Vista/7/8/8.1 and on 32-bit Windows, where Updater CE cannot
  run; the firmware mirrors repack it beside a rom as
  `<romzipname>-windows-legacy.zip` for the site's legacy download button.
  Pass `-NoLegacy` (or `-NoLegacy` to the batch file) to skip it. ARM64 builds
  skip it because SP Flash Tool is x86-only.
- Architecture: pass `--arch arm64` / `-Arch arm64` or run the scripts with an **arm64** Python.
  On ARM64, MTKClient is packaged as the native flashing engine (SP Flash Tool is x86-only and omitted).
- Both `build_windows.bat` and `build_windows.ps1` accept the brand, architecture, `-Clean`, `-NoInstaller`, and `-Msix`.

---

## Linux — AppImage

```bash
chmod +x build_appimage.sh

./build_appimage.sh                     # host architecture (x86_64 or aarch64)
./build_appimage.sh --arch aarch64      # ARM64
./build_appimage.sh --arch armhf        # ARM32, needs an armhf Python + appimagetool
./build_appimage.sh --clean             # rebuild from scratch
./build_appimage.sh --no-pyinstaller    # repackage an existing dist/linux/ tree
./build_appimage.sh --brand all         # Updater CE + MediaTek Installer
./build_appimage.sh --brand updater_ce  # Updater CE only (the default)
```

- Output: `dist/linux/InnioasisUpdater-<version>-<arch>.AppImage` (alias `UpdaterCE.appimage` / `UpdaterCE.AppImage`); `--brand all` also builds `MediaTekInstaller-<version>-<arch>.AppImage` (alias `MediaTek Installer.appimage`). Each carries the full stack:
  - Customized SP Flash Tool (`tools/linux/SP_Flash_Tool_v5.1904_Linux`, on x86_64)
  - Precompiled MTKClient with bundled `libusb-1.0`
  - Vendored ADB (`tools/adb`, on `PATH` for both x86_64 and aarch64)
  - Automated udev rules & serial group setup helpers
- Rebuild customized SP Flash Tool for Linux at any time with `./tools/build_linux_sp_flash.sh`.
- The arch also selects `appimagetool-<arch>.AppImage`; both `x86_64` and `aarch64` are fully supported.
- Linux builds on x86-64 use SP Flash Tool by default (with MTKClient as alternate backend); ARM64 builds use native MTKClient.

### Building both from macOS or Windows (Docker)

`build_appimage.sh` assembles the AppImage with Linux binaries (`appimagetool`,
`sha256sum`, `tar`), so its `--container` mode only containerises PyInstaller and
the packaging steps still need a Linux host. To build from a Mac or Windows PC,
run the whole pipeline in Docker instead:

```bash
./build_appimage_docker.sh                  # x86_64 and aarch64
./build_appimage_docker.sh --arch aarch64   # one architecture
./build_appimage_docker.sh --no-test        # build only
./build_appimage_docker.sh --clean          # drop the per-arch build trees first
./build_appimage_docker.sh --rebuild-image  # rebuild the builder image
```

It builds `updater-linux-builder:<arch>` from `Dockerfile.linux_build` (Debian
Bookworm, so the x86-64 payload keeps a plain x86-64 baseline), runs
`build_appimage.sh --no-container` inside that image, and writes the same
`dist/linux/` artifacts a native run does. Each architecture gets its own
`build/linux-<arch>/` tree, because the cached `appimagetool` in it belongs to
one CPU architecture.

After each build the result is tested inside the same container: the SHA256
file is verified, the AppDir is extracted and its ELF architecture, bundled
`adb`, `unrar`, `libusb-1.0` and (x86-64 only) SP Flash Tool payloads are
checked, `--diagnostics` must pass (mtkclient import, libusb backend,
`mtk_api.init`), and the AppImage must stay up for ten seconds headless. Pass
`--no-test` to skip this. A cross-architecture build relies on the host's
emulation, so it is correct but slow on an Apple Silicon Mac.

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
