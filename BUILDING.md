# Building Updater CE

This guide contains everything required for anyone to clone this repository and build the complete updater stack on **macOS**, **Windows**, or **Linux**.

> Looking for the copy-paste command for a specific CPU architecture (arm64, x86_64, Universal 2, arm64 Windows, aarch64 Linux)? See **[BUILD_TARGETS.md](BUILD_TARGETS.md)**, the per-target command matrix.

---

## 1. Prerequisites by Platform

### macOS (12.0 Monterey through 26.0+ Golden Gate / Tahoe)
- **Python**: none needed up front — the first build finds or installs one (3.10+, preferring 3.12) and creates `.venv-build`
- **Xcode Command Line Tools**: needed for `--arch universal2` (clang): `xcode-select --install`
- **Architecture**: Supports Intel (`x86_64`), Apple Silicon (`arm64`), and Universal2 (`universal2`).

`./build_macos.sh` bootstraps its own environment through `scripts/bootstrap_build_env.py`:
no virtualenv, `pip install` or Homebrew package is a prerequisite for a fresh clone. It reuses
an existing `.venv-build` when its requirements are unchanged, installs `requirements.txt` +
`requirements-build.txt` when they are not, and never touches a `PYTHON` you set yourself.

### Windows (10 / 11, x64 / ARM64)
- **Python**: 3.10 or newer (ensure "Add python.exe to PATH" is checked during setup; use native ARM64 Python for ARM64 builds)
- **Inno Setup 6** (optional, for building the `.exe` setup wizard): [Inno Setup Downloads](https://jrsoftware.org/isdl.php)
- **Windows 10/11 SDK** (optional, only if packing Microsoft Store `.msix` packages with `MakeAppx.exe`)

### Linux (Ubuntu, Debian, Fedora, Arch, etc.)
- **Python**: installed by `./build_appimage.sh` if missing (3.10+, preferring 3.12); `python3-venv`/`python3-pip` must be available for the virtualenv
- **Packages**: `curl`, `file`, `desktop-file-utils` (used by AppImage tooling)

---

## 2. Build Instructions

### macOS (.app bundle and .dmg disk image)

Clone the repository and run:
```bash
git clone https://github.com/y1-community/updater-ce-neo.git
cd updater-ce-neo

# Make executable and build standalone macOS .app
chmod +x build_macos.sh
./build_macos.sh

# Or build a Universal2 fat binary and create a drag-and-drop DMG:
./build_macos.sh --arch universal2 --dmg
```

The first run creates `.venv-build/` and installs the requirements (a few minutes, ~200 MB);
later runs reuse it and go straight to PyInstaller. `./build_macos.sh --check-deps` only verifies
the environment. Without `--arch`, the bundle targets the machine's own architecture — PyInstaller
can only produce the architecture of the Python it runs under; use `--arch universal2` for one
bundle that runs on both Intel and Apple Silicon.

**Output** (Updater CE by default — `--brand all` adds the MediaTek Installer, see section 5):
- `dist/macos/Innioasis Updater.app` (100% self-contained application bundle)
- `dist/macos/InnioasisUpdater-2.5.2-<arch>-macOS.dmg` (if `--dmg` is specified)

---

### Windows (.exe standalone and setup installer)

#### 1. x64 Build (Intel / AMD 64-bit)

In Command Prompt (`cmd.exe`):
```cmd
git clone https://github.com/y1-community/updater-ce-neo.git
cd updater-ce-neo
build_windows.bat
```

Or in PowerShell:
```powershell
git clone https://github.com/y1-community/updater-ce-neo.git
cd updater-ce-neo
.\build_windows.ps1
```

**Output** (Updater CE by default — `--brand both` adds the MediaTek Installer, see section 5):
- `dist\windows\InnioasisUpdater\InnioasisUpdater.exe` (standalone portable directory with bundled SP Flash Tool)
- `dist\windows\UpdaterCE-Portable-2.5.2.zip` (portable zip archive)
- `dist\windows\UpdaterCE-Setup-2.5.2.exe` (Windows Inno Setup installer wizard)

> **Note**: On x64 Windows, the app runs 100% SP Flash Tool by default. Press the `M` key on the keyboard to unlock the MTKClient advanced backend option.

#### 2. ARM64 Build (Qualcomm Snapdragon X Elite / Plus, Surface Pro X, Windows on ARM)

To compile native ARM64 Windows binaries and installers:

In Command Prompt (`cmd.exe`):
```cmd
build_windows.bat --arch arm64
```

Or in PowerShell:
```powershell
.\build_windows.ps1 -Arch arm64
```

> **Auto-detection**: When running on an ARM64 Windows system (or VM) with native ARM64 Python, `build_windows.bat` and `build_windows.ps1` automatically detect the architecture without needing `--arch arm64`.
>
> **No cross-compiling**: PyInstaller freezes for the interpreter that runs it, so an Intel/AMD machine cannot produce an ARM64 package. The scripts check the interpreter and stop with an explanation instead of labelling an x64 build as ARM64. Build ARM64 on Windows on ARM — a Snapdragon PC/VM, or a `windows-11-arm` CI runner — where the bootstrap creates a native `.venv-build` (or point it at `.venv-arm64`).

**ARM64 Output** (into `dist\windows-arm64`, so an x64 build in `dist\windows` is left alone; `-Brand both` also produces the same three as MediaTek Installer):
- `dist\windows-arm64\InnioasisUpdater\InnioasisUpdater.exe` (native ARM64 portable folder)
- `dist\windows-arm64\UpdaterCE-Portable-2.5.2-arm64.zip` (portable archive)
- `dist\windows-arm64\UpdaterCE-Setup-2.5.2-arm64.exe` (Inno Setup 6 installer compiled with `ArchitecturesAllowed=arm64` and `ArchitecturesInstallIn64BitMode=arm64`)
- `dist\windows-arm64\InnioasisUpdater-2.5.2.0-arm64.msix` (when building with `-Msix`)

**Flashing Backend on Windows ARM64**:
On Windows ARM64, the app uses **MTKClient** as its flash engine (just like macOS and Linux ARM64). SP Flash Tool is an x86/x64-only binary and is deliberately not bundled on ARM64 builds, saving space and avoiding driver emulation issues. MTKClient communicates directly over USB with MediaTek BROM / Preloader devices without requiring legacy x86 VCOM kernel drivers.

### Windows (Microsoft Store / MSIX package)

The same PyInstaller folder is packed as a packaged desktop application for the
Microsoft Store:

```powershell
build_windows.ps1 -Brand updater_ce -Msix
# or
build_windows.bat updater_ce -Msix
```

That produces `dist\windows\InnioasisUpdater-<version>.msix` (and, for the other
front end, `MediaTekInstaller-<version>.msix`) next to the portable folder and
the Inno Setup wizard; an ARM64 build writes the same names into
`dist\windows-arm64` with an `-arm64` suffix. Requirements and one-time setup:

1. **Windows 10/11 SDK** with `MakeAppx.exe` (and `signtool.exe`, only needed to
   test-sign locally). The build script finds it under
   `%ProgramFiles(x86)%\Windows Kits\10\bin`.
2. **A reserved app name in Partner Center.** Copy
   `scripts\msix\msix_identity.example.json` to
   `scripts\msix\msix_identity_<brand>.json` (`updater_ce` or
   `mediatek_installer`) and replace every value with the ones Partner Center
   shows under **App management → App identity**. `Name` and `Publisher` must
   match the reservation exactly or the upload is rejected. These files are
   git-ignored because they are account-specific.
3. **Upload the unsigned `.msix`.** Microsoft signs Store packages during
   certification; `-MsixSignCert` is only for installing a test build locally.

What the package declares (see `scripts/msix/AppxManifest.xml`): a packaged
Win32 desktop app with `Windows.FullTrustApplication` and `runFullTrust`, so the
bundled Python runtime, Qt plugins and SP Flash Tool binaries run exactly as in
the portable build. The manifest also carries a commented-out `usb` device
capability block: declare the MediaTek `vidpid` ids if a submission asks for
them.

Once published, a newer GitHub release advertises the Store build with an
`msstore.txt` asset whose first line is `true`. The app reads that file for the
release it has found, and only when it is running as an MSIX package does it
offer **Open Microsoft Store** (which opens the Store's *Library → Updates*
page) instead of downloading an installer over itself.

---

### Linux (AppImage)

Clone the repository and run:
```bash
git clone https://github.com/y1-community/updater-ce-neo.git
cd updater-ce-neo

chmod +x build_appimage.sh
./build_appimage.sh                    # builds x86_64
./build_appimage.sh --arch aarch64     # builds ARM64
```

**Output** (Updater CE by default — `--brand all` adds the MediaTek Installer, see section 5):
- `dist/linux/InnioasisUpdater-2.5-x86_64.AppImage` (alias `UpdaterCE.appimage` / `UpdaterCE.AppImage`)
- Standalone portable `.tar.gz` and `.zip` archives next to each AppImage
- Bundled full stack:
  - **Customized SP Flash Tool for Linux** (`tools/linux/SP_Flash_Tool_v5.1904_Linux`, built from `tools/sp-flash-tool-src` via `tools/build_linux_sp_flash.sh` against Ubuntu 16.04 glibc 2.23 baseline with self-contained Qt 4.8.7 runtime libraries)
  - **In-process MTKClient** with bytecode precompilation and bundled `libusb-1.0`
  - **Vendored ADB** on `PATH` for both `x86_64` and `aarch64` (`tools/adb`)
  - **Automated udev rules & serial group setup** for all major Linux distributions

To recompile our customized SP Flash Tool for Linux from source at any time:
```bash
./tools/build_linux_sp_flash.sh
```

---

## 3. Flash Engine Architecture

| Platform | Default Backend | Alternate Backend | Bundled Payload |
| :--- | :--- | :--- | :--- |
| **macOS** | **MTKClient** | None (SP Flash Tool has no macOS build) | `vendor/mtkclient` + `libusb-1.0.dylib` + `MTK_AllInOne_DA_mt6590.bin` + `tools/adb` |
| **Windows (x64)** | **SP Flash Tool** | **MTKClient** (unlocked via `M` key) | `tools/windows/SP_Flash_Tool_v5.1904_Win` + `tools/adb` |
| **Windows (ARM64)** | **MTKClient** | None (SP Flash Tool is x86-only) | Precompiled `mtkclient` + `tools/adb` |
| **Linux (x86-64)** | **SP Flash Tool** | **MTKClient** | `tools/linux/SP_Flash_Tool_v5.1904_Linux` + `tools/adb` + `libusb-1.0` |
| **Linux (ARM64)** | **MTKClient** | None (SP Flash Tool is x86-64 only) | Precompiled `mtkclient` + `tools/adb/linux-arm64` + `libusb-1.0` |

---

## 4. Running Offline

Updater CE supports 100% offline usage:
- When no internet connection is detected, the **Online** tab is automatically hidden, leaving only the **Local File** tab.
- Users can choose any local `.zip`, `.rar`, scatter text file (`MT6572_Android_scatter.txt`), or firmware directory to flash completely offline.
- When an active network is detected, the window dynamically refreshes and restores the **Online** firmware tab.
- Settings also offers a manual **Offline Mode** switch for the same effect. Offline mode keeps the Updater CE name and branding; it only hides the online catalogue. (To build the tool as a generic MediaTek installer instead, see section 5.)

---

## 5. MediaTek Installer builds (generic cross-platform tool)

The same engine can be packaged as **MediaTek Installer** — a generic MediaTek
firmware installer with no Updater CE identity and no online firmware at all:

- the app is named `MediaTek Installer` and shown in-app as `Installer 2.5`
- the offline switch is not shown: this build is offline-only by definition
- donations remain (buttons, goal, donor info), worded for MediaTek Installer
  rather than Updater CE, the Community Firmware Archive and the Themes Gallery
- "install from a terminal window" remains available, so the console tools can
  still be watched and diagnosed

### MediaTek Installer is an opt-in brand

A normal build produces the **Updater CE** app only — it is the product that
ships, and building the MediaTek Installer as well doubles the packaging time
for a build that may never be released. Both front ends come from the same
engine with different identities, so the Installer is one flag away:

```bash
./build_macos.sh --brand all        # dist/Innioasis Updater.app + dist/MediaTek Installer.app
./build_appimage.sh --brand all     # Updater CE + MediaTek Installer AppImages
build_windows.bat both              # dist\InnioasisUpdater\ + dist\MediaTekInstaller\ (+ both installers)
```

To build only the Installer:

```bash
./build_macos.sh --brand mediatek_installer     # dist/MediaTek Installer.app only
./build_appimage.sh --brand mediatek_installer  # MediaTek Installer AppImage only
build_windows.bat mediatek_installer            # MediaTek Installer only
powershell -File build_windows.ps1 -Brand mediatek_installer
BUILD_BRAND=mediatek_installer ./build_macos.sh # same, via the environment
```

A build only replaces the artifacts of the brands it builds: whatever else is
already in `dist/` (the other app, its installer/DMG) stays exactly as it was.

The dist folder / executable name stays a slug per brand
(`InnioasisUpdater`, `MediaTekInstaller`); `MediaTek Installer` is the name the
user sees in the installer, Start Menu, window title and dock.

The brand is frozen into the bundle (`src/_build_brand.py`, generated and
gitignored), so it survives being launched from Finder. Other platforms can
bake it with the same helper before their packaging step:

```bash
BUILD_BRAND=mediatek_installer python scripts/set_build_brand.py mediatek_installer
# ... run that platform's build/packaging ...
python scripts/set_build_brand.py --reset          # back to Updater CE
```

For development no rebuild is needed — the mode can be chosen at launch:

```bash
python -m src.app --mediatek-installer   # generic MediaTek Installer
python -m src.app --offline              # Updater CE, online catalogue hidden
```
