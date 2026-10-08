# Building Updater CE

This guide contains everything required for anyone to clone this repository and build the complete updater stack on **macOS**, **Windows**, or **Linux**.

---

## 1. Prerequisites by Platform

### macOS (13.0 Ventura through 26.0+ Golden Gate / Tahoe)
- **Python**: 3.11 or newer (Universal2 recommended)
- **Xcode Command Line Tools**: `xcode-select --install`
- **Architecture**: Supports Intel (`x86_64`), Apple Silicon (`arm64`), and Universal2 (`universal2`).

### Windows (10 / 11, x64 / ARM64)
- **Python**: 3.10 or newer (ensure "Add python.exe to PATH" is checked during setup)
- **Inno Setup 6** (optional, for building the `.exe` setup wizard): [Inno Setup Downloads](https://jrsoftware.org/isdl.php)

### Linux (Ubuntu, Debian, Fedora, Arch, etc.)
- **Python**: 3.10 or newer
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

# Or build Universal2 fat binary and create a drag-and-drop DMG:
./build_macos.sh --arch universal2 --dmg
```

**Output** (both front ends — see section 5):
- `dist/Updater CE.app` and `dist/MediaTek Installer.app` (100% self-contained application bundles)
- `dist/UpdaterCE-3.0-universal2-macOS.dmg` and `dist/MediaTekInstaller-3.0-universal2-macOS.dmg` (if `--dmg` is specified)

---

### Windows (.exe standalone and setup installer)

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

**Output** (both front ends — see section 5):
- `dist\InnioasisUpdater\InnioasisUpdater.exe` and `dist\MediaTekInstaller\MediaTekInstaller.exe` (standalone portable directories with bundled SP Flash Tool)
- `dist\UpdaterCE-Setup-3.0.0.exe` and `dist\MediaTekInstaller-Setup-3.0.0.exe` (full Windows installer wizards, each with its own identity so both can be installed side by side)

> **Note**: On Windows, the app runs 100% SP Flash Tool by default. Press the `M` key on the keyboard to unlock the MTKClient advanced backend option.

---

### Linux (AppImage)

Clone the repository and run:
```bash
git clone https://github.com/y1-community/updater-ce-neo.git
cd updater-ce-neo

chmod +x build_appimage.sh
./build_appimage.sh
```

**Output** (both front ends — see section 5):
- `dist/UpdaterCE-3.0-x86_64.AppImage` and `dist/MediaTekInstaller-3.0-x86_64.AppImage` (standalone portable AppImages with bundled SP Flash Tool and udev setup tools)

---

## 3. Flash Engine Architecture

| Platform | Default Backend | Alternate Backend | Bundled Payload |
| :--- | :--- | :--- | :--- |
| **macOS** | **MTKClient** | None (SP Flash Tool has no macOS build) | `vendor/mtkclient` + `libusb-1.0.dylib` + `MTK_AllInOne_DA_mt6590.bin` |
| **Windows** | **SP Flash Tool** | **MTKClient** (unlocked via `M` key) | `tools/windows/SP_Flash_Tool_v5.1904_Win` |
| **Linux** | **SP Flash Tool** | **MTKClient** | `tools/linux/SP_Flash_Tool_v5.1904_Linux` |

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

- the app is named `MediaTek Installer` and shown in-app as `Installer 3.0`
- the offline switch is not shown: this build is offline-only by definition
- donations remain (buttons, goal, donor info), worded for MediaTek Installer
  rather than Updater CE, the Community Firmware Archive and the Themes Gallery
- "install from a terminal window" remains available, so the console tools can
  still be watched and diagnosed

### Every build ships both apps

A normal build of any platform produces **both** front ends side by side — the
Updater CE app and the MediaTek Installer app — because they are the same
engine with different identities:

```bash
./build_macos.sh            # dist/Updater CE.app + dist/MediaTek Installer.app
./build_appimage.sh         # dist/UpdaterCE-*.AppImage + dist/MediaTekInstaller-*.AppImage
build_windows.bat           # dist\InnioasisUpdater\ + dist\MediaTekInstaller\ (+ both installers)
```

To build just one (the quick path while iterating on a single front end):

```bash
./build_macos.sh --brand mediatek_installer     # dist/MediaTek Installer.app only
./build_appimage.sh --brand updater_ce          # Updater CE AppImage only
build_windows.bat mediatek_installer            # MediaTek Installer only
powershell -File build_windows.ps1 -Brand mediatek_installer
BUILD_BRAND=mediatek_installer ./build_macos.sh # same, via the environment
```

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
