# Innioasis Updater CE & MediaTek Installer

Cross-platform flashing suite for Innioasis (Y1, Y2), Timmkoo, and MediaTek devices (MT6572, MT6582, MT6580, MT8382). Supports SP Flash Tool console mode (Windows, Linux) and native in-process MTKClient (macOS, Windows, Linux).

---

## 1. Built Artifacts (`/dist`)

The build pipeline generates two distribution forms per brand:
1. **Self-Contained Installer Wizard**: Single setup executable created with Inno Setup 6.
2. **Portable Application Directory**: Ready-to-run folder containing the compiled executable, bundled SP Flash Tool payload, and runtime dependencies.

| Branding | Setup Installer | Portable Application Folder & Binary |
| :--- | :--- | :--- |
| **Updater CE** | `dist/UpdaterCE-Setup-2.5.0.exe` | `dist/InnioasisUpdater/InnioasisUpdater.exe` |
| **MediaTek Installer** | `dist/MediaTekInstaller-Setup-2.5.0.exe` | `dist/MediaTekInstaller/MediaTekInstaller.exe` |

---

## 2. Cross-Platform Build Commands

Every platform build script supports generating **both** front ends by default, or targeting a specific brand using the `--brand` / `-Brand` parameter:
- `updater_ce` (default community updater with online catalog + local firmware)
- `mediatek_installer` (offline generic MediaTek installer with isolated application identity)

### Windows (.exe & Inno Setup Installer)

Prerequisites: Python 3.10+ (64-bit), Inno Setup 6 (optional, required for setup `.exe` wizard).

#### Build in PowerShell:
```powershell
# Build Updater CE only (Executable + Installer)
.\build_windows.ps1 -Brand updater_ce

# Build MediaTek Installer only (Executable + Installer)
.\build_windows.ps1 -Brand mediatek_installer

# Build BOTH brandings side-by-side
.\build_windows.ps1

# Clean previous build artifacts before compiling
.\build_windows.ps1 -Brand updater_ce -Clean

# Skip Inno Setup (generate portable directory only)
.\build_windows.ps1 -Brand updater_ce -NoInstaller
```

#### Build in Command Prompt (`cmd.exe`):
```cmd
:: Build Updater CE only
build_windows.bat updater_ce

:: Build MediaTek Installer only
build_windows.bat mediatek_installer

:: Build BOTH brandings side-by-side
build_windows.bat
```

---

### macOS (.app Bundle & .dmg Disk Image)

Prerequisites: macOS 13+ (Ventura through Golden Gate), Python 3.11+ (Universal2 recommended), Xcode Command Line Tools (`xcode-select --install`).

```bash
# Make script executable
chmod +x build_macos.sh

# Build Updater CE only (.app bundle)
./build_macos.sh --brand updater_ce

# Build Updater CE as Universal2 .app and create drag-and-drop .dmg
./build_macos.sh --brand updater_ce --arch universal2 --dmg

# Build MediaTek Installer only (.app bundle)
./build_macos.sh --brand mediatek_installer

# Build MediaTek Installer as Universal2 .app and create drag-and-drop .dmg
./build_macos.sh --brand mediatek_installer --arch universal2 --dmg

# Build BOTH brandings (.app bundles for both)
./build_macos.sh

# Build BOTH brandings as Universal2 with .dmg installers
./build_macos.sh --arch universal2 --dmg
```

**macOS Outputs (`dist/`):**
- `dist/Updater CE.app` and `dist/UpdaterCE-3.0-universal2-macOS.dmg`
- `dist/MediaTek Installer.app` and `dist/MediaTekInstaller-3.0-universal2-macOS.dmg`

---

### Linux (Standalone .AppImage)

Prerequisites: Python 3.10+, `curl`, `file`, `desktop-file-utils` (`appimagetool` is automatically downloaded if missing).

```bash
# Make script executable
chmod +x build_appimage.sh

# Build Updater CE only (x86_64 AppImage)
./build_appimage.sh --brand updater_ce

# Build MediaTek Installer only (x86_64 AppImage)
./build_appimage.sh --brand mediatek_installer

# Build BOTH brandings side-by-side
./build_appimage.sh

# Clean previous build cache and rebuild
./build_appimage.sh --clean --brand updater_ce
```

**Linux Outputs (`dist/`):**
- `dist/UpdaterCE-2.5.0-x86_64.AppImage`
- `dist/MediaTekInstaller-2.5.0-x86_64.AppImage`

---

## 3. Brand Switching & Development Flags

When iterating in development without rebuilding executables:

```bash
# Run locally with generic MediaTek Installer branding
python -m src.app --mediatek-installer

# Run locally in Updater CE Offline Mode (hides online catalog)
python -m src.app --offline

# Verify environment diagnostics and frozen dependencies
python launcher.py --check-environment

# Set baked branding for manual PyInstaller runs
python scripts/set_build_brand.py updater_ce
python scripts/set_build_brand.py mediatek_installer
python scripts/set_build_brand.py --reset
```

---

## 4. Flash Engine Architecture

| Platform | Primary Backend | Secondary Backend | Bundled Tool / Payload |
| :--- | :--- | :--- | :--- |
| **Windows** | **SP Flash Tool** | **MTKClient** (press `M` to unlock) | `tools/windows/SP_Flash_Tool_v5.1904_Win` |
| **macOS** | **MTKClient** (in-process) | None | `vendor/mtkclient` + `libusb-1.0.dylib` |
| **Linux** | **SP Flash Tool** | **MTKClient** | `tools/linux/SP_Flash_Tool_v5.1904_Linux` |

---

## 5. Combined Windows / Linux Portable Distribution (`fw_package.ini`)

Firmware developers and community creators can distribute a single, combined portable package that boots natively on both Windows and Linux without requiring installation.

### Folder Structure
Place the Windows portable build, the Linux `.AppImage`, the MediaTek firmware files, and `fw_package.ini` in the same directory:

```text
MyCustomFirmware_Portable/
├── fw_package.ini             # Portable package manifest
├── updater.png                # Header branding icon (64x64 or 128x128 PNG)
├── MT6572_Android_scatter.txt # MediaTek scatter file
├── preloader.bin              # Declared partition images...
├── boot.img
├── recovery.img
├── system.img
├── UpdaterCE.exe              # Windows portable binary
├── _internal/                 # Windows dependencies
└── UpdaterCE-x86_64.AppImage  # Linux standalone executable (chmod +x)
```

### Manifest Format (`fw_package.ini`)
```ini
[fw_package]
enabled = true
name = Rockbox
version = 3.0.7
model = Y1
type = Type A
```

### Self-Installing Behavior & Working Directory Isolation
1. **Automatic Folder Pre-Selection**: When launched from this folder, Updater CE automatically detects `fw_package.ini`, switches to the folder install flow, and fills in the software name, version, model, hardware type, and header icon. The software name is automatically translated into the user's desktop language via Google Translate.
2. **Directory Isolation Staging**: Before SP Flash Tool or MTKClient executes, Updater CE stages the scatter file and only the declared partition images into an isolated cache working directory (`stage_firmware_for_flashing`). This protects the application's executable, `_internal/` runtime libraries, and scripts from being overwritten, polluted with temporary logs, or modified during flashing.
3. **Archive Pruning**: When extracting a portable zip archive, Updater CE automatically retains only the scatter file, the partition images declared in the scatter, `fw_package.ini`, any `*.auth` file, and `updater.png`, purging unnecessary archive overhead.

