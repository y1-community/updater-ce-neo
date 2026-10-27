# Multi-Platform Build & Packaging Guide

This guide documents the build architecture, prerequisites, tooling pipelines, and distribution procedures for **Updater CE** and **MediaTek Installer** across Windows, macOS, and Linux.

---

## 1. Architecture & Platform Matrix

The codebase ships two brand targets from a single shared engine:
- **`updater_ce` (Innioasis Updater CE)**: Tailored for Innioasis digital audio players (Y1, G1, etc.), featuring curated firmware downloads, scatter discovery, wizard navigation, and Rockbox themes installation.
- **`mediatek_installer` (MediaTek Installer)**: Generic MediaTek firmware installation tool supporting arbitrary scatter-based firmware packages.

### Backend Strategy by Platform

| Target Platform | Primary Flash Engine | Fallback / Secondary Engine | Archive Unpacking | Drivers / USB Access |
| :--- | :--- | :--- | :--- | :--- |
| **Windows x86 / x64** | **SP Flash Tool v5.1904** (Console Mode) | MTKClient (optional, shed on verified installs) | `zipfile` (ZIP) / `tools\UnRAR.exe` (RAR) | Bundled `DriverInstall.exe` (MediaTek VCOM) |
| **Windows ARM64** | **MTKClient** | SP Flash Tool (experimental via Prism emulation) | `zipfile` (ZIP) / `tools\UnRAR.exe` (RAR) | WinUSB / PyUSB via bundled `libusb-1.0.dll` |
| **macOS (Universal 2)** | **MTKClient** | None (SP Flash Tool does not ship for Darwin) | `zipfile` (ZIP) / `tools/unar` (RAR) | PyUSB via bundled Universal 2 `libusb-1.0.dylib` |
| **Linux (x86_64)** | **SP Flash Tool** | MTKClient | `zipfile` (ZIP) / `tools/unar` (RAR) | `udev` rules + host `libusb-1.0.so.0` |
| **Linux (AArch64)** | **MTKClient** | None | `zipfile` (ZIP) / `tools/unar` (RAR) | `udev` rules + host `libusb-1.0.so.0` |

---

## 2. Prerequisites & Development Environment

### Common Prerequisites
- **Python 3.10 – 3.12** (Python 3.11 recommended).
- Virtual environment in `.venv-build/` (or activated host environment).
- Required Python modules:
  ```bash
  pip install -r requirements.txt
  pip install pyinstaller pytest pytest-qt
  ```

---

## 3. Windows Build Process

The Windows packaging pipeline produces:
1. **Portable Application Directory** (`dist\windows\<BrandSlug>\`)
2. **Portable ZIP Archive** (`dist\windows\<BrandSlug>-Portable-<Version>.zip`)
3. **Inno Setup Standard Windows Installer** (`dist\windows\<BrandSlug>-Setup-<Version>.exe`)
4. **Microsoft Store MSIX Package** (optional via `-Msix`)

### Build Scripts
- **PowerShell (Primary)**: `build_windows.ps1`
- **Batch Wrapper**: `build_windows.bat`
- **Installer Script**: `scripts\build_windows_installer.ps1` / `scripts\build_windows_installer.iss`
- **MSIX Packager**: `scripts\build_windows_msix.ps1`

### Running the Windows Build

#### A. Build Updater CE Only (Default & Recommended to Save Time)
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1
```
*Note: In `build_windows.ps1`, `-Brand` defaults to `updater_ce`. This builds only the primary Updater CE application and installer, avoiding duplicate build time.*

#### B. Build MediaTek Installer Only
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -Brand mediatek_installer
```

#### C. Build Both Brands (Full Release Suite)
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -Brand both
```

#### D. Fast Iteration Options
- **Skip PyInstaller re-compilation** (repackage installer/zip from existing staged files):
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -SkipPyInstaller
  ```
- **Portable ZIP Only (Skip Inno Setup Installer)**:
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -NoInstaller
  ```
- **Inno Setup Installer Only**:
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -InstallerOnly
  ```
- **Clean Build (Wipe output directories first)**:
  ```powershell
  powershell -NoProfile -ExecutionPolicy Bypass -File .\build_windows.ps1 -Clean
  ```

### Windows Packaging Specifics

1. **Subprocess Silence**:
   All child processes (`adb.exe`, `flash_tool.exe`, `DriverInstall.exe`, `powershell.exe`) run with `STARTF_USESHOWWINDOW` (`wShowWindow = SW_HIDE`) and `CREATE_NO_WINDOW (0x08000000)` via `src/quiet_subprocess.py` to prevent CMD console windows from flashing on screen.
2. **MediaTek SP Flash Tool Payload**:
   The x86 SP Flash Tool binary (`SP_Flash_Tool\flash_tool.exe`) and download agent binaries (`MTK_AllInOne_DA.bin`, `DA_PL.bin`, `DA_SWSEC.bin`) are automatically verified and placed beside the main executable.
3. **UnRAR Packaging**:
   `tools\windows\UnRAR.exe` is bundled into `tools\UnRAR.exe` to allow extraction of `.rar` firmware packages matching the Chinese market release behavior.
4. **Driver Check & Inno Setup Finish Page**:
   Inno Setup verifies if MediaTek drivers are already installed by searching:
   - `{pf}\MediaTek\SP Flash\unins000.exe`
   - `{pf}\MediaTek\SP Driver\unins000.exe`
   - `{pf32}\MediaTek\SP Flash\unins000.exe`
   - `{pf64}\MediaTek\SP Flash\unins000.exe`
   If the driver was installed during setup, Inno Setup automatically suppresses "Launch Updater CE" and offers a "Restart PC to finish setup" option instead.
5. **Conditional MTKClient Delivery**:
   On x86/x64 Windows systems, `mtkclient` is excluded during Inno Setup installation to reduce install size. On ARM64 Windows systems (`ProcessorArchitecture = paARM64`), `mtkclient` is installed. When running on an x86/x64 machine, the application will also shed redundant `mtkclient` folders on startup.
6. **Cache & Settings Cleanup on Upgrade & Uninstall**:
   - **Upgrade Lifecycle (`ssInstall`)**: During installation or upgrade, Inno Setup purges older versions' cached files (`.cache`, `downloads`, `logs`, `tools`, `SP_Flash_Tool`) across all local user profiles (`AppData\Local\Updater CE`, `AppData\Local\MediaTek Installer`, `AppData\Local\Innioasis Updater`, `innioasis`), clears system-wide `C:\ProgramData\SP_FT_Logs` and `SP_FT_Dump`, and resets legacy registry entries (`HKCU\Software\innioasis`, `HKCU\Software\Innioasis`) to ensure an upgrade never inherits corrupted cache or incompatible settings.
   - **Uninstall Lifecycle (`usUninstall` & `usPostUninstall`)**: Stops all active processes (`InnioasisUpdater.exe`, `MediaTekInstaller.exe`, `flash_tool.exe`, `adb.exe`), deletes the entire `{app}` directory tree, deletes all per-user cache/data folders (`Updater CE`, `MediaTek Installer`, etc.) and system trace dumps, and purges all registry keys so the system is left completely clean.

---

## 4. macOS Build Process

The macOS pipeline produces a self-contained, signed **Universal 2 App Bundle** (`dist/macOS/<AppName>.app`) and an optional `.dmg` disk image.

### Build Scripts
- **Main Shell Script**: `./build_macos.sh`
- **Universal Python Builder**: `scripts/build_universal_app.py`

### Running the macOS Build

#### A. Build Universal 2 App Bundle
```bash
./build_macos.sh --arch universal2
```

#### B. Build and Create DMG Installer
```bash
./build_macos.sh --arch universal2 --dmg
```

#### C. Build Specific Architecture or Brand
```bash
./build_macos.sh --arch arm64 --brand updater_ce
./build_macos.sh --arch x86_64 --brand mediatek_installer
```

### macOS Packaging Specifics

1. **Universal 2 Architecture**:
   `scripts/build_universal_app.py` assembles a standalone Universal 2 CPython runtime by extracting both `arm64` and `x86_64` CPython distributions and merging all Mach-O binaries with `llvm-lipo` / `lipo`.
2. **Libusb Universal Library**:
   `vendor/mtkclient/mtkclient/Darwin/libusb-1.0.dylib` is bundled into `Contents/Frameworks/libusb-1.0.dylib` and verified as Universal 2 (`x86_64` + `arm64`).
3. **RAR Support (`unar`)**:
   `tools/macos/unar` is copied into `Contents/Frameworks/tools/unar` with a symlink at `Contents/Resources/tools/unar`, ensuring parity with the Chinese market release for RAR decompression.
4. **Native PyObjC & Icon Tinting**:
   Window titlebars and appearance integrate natively with macOS Cocoa APIs (`NSWorkspace`, `NSAppearance`). SP Flash Tool is omitted since MediaTek does not provide a Darwin build.
5. **Code Signing**:
   The bundle is automatically signed with hardened runtime and entitlements (`assets/entitlements.plist`) using `rcodesign` or Apple's native `codesign`.

---

## 5. Linux Build Process

The Linux pipeline creates a standalone, desktop-integrated **AppImage** runnable across glibc-based distributions.

### Build Script
- **Main Shell Script**: `./build_appimage.sh`

### Running the Linux Build

#### A. Build x86_64 AppImage
```bash
./build_appimage.sh --arch x86_64
```

#### B. Build AArch64 / ARM64 AppImage
```bash
./build_appimage.sh --arch aarch64
```

#### C. Target Specific Brand
```bash
./build_appimage.sh --arch x86_64 --brand mediatek_installer
```

### Linux Packaging Specifics

1. **SP Flash Tool Console Runtime**:
   On `x86_64`, SP Flash Tool v5.1904 console mode is bundled alongside required runtime libraries (`tools/linux/SP_Flash_Tool_v5.1904_Linux`).
2. **PyUSB / libusb-1.0 Resolution**:
   Linux builds search for `libusb-1.0.so.0` first, which is standard on modern distributions, rather than hardcoding unversioned `.so`.
3. **Bundled ADB**:
   Architecture-matched ADB executables from `tools/adb/linux-x64/adb` or `tools/adb/linux-arm64/adb` are staged into `tools/adb/` and `usr/bin/`.
4. **Udev Rules**:
   Device access without root permissions is configured via udev rules. The app prompts users to install `51-innioasis.rules` if BROM / Preloader USB access is restricted.

---

## 6. Verification & Automated Testing

### Headless Test Suite
To verify backend logic, scatter parsing, USB search timing, and theme updates without opening GUI windows:

```bash
# Set headless and offscreen environment flags
export QT_QPA_PLATFORM=offscreen
export INNIOASIS_HEADLESS=1

# Run core verification suite
pytest tests/test_sp_flash_usb_search.py \
       tests/test_rar_extraction.py \
       tests/test_shed_mtkclient.py \
       tests/test_donations_and_release_labels.py \
       tests/test_theme_segmented_and_settings.py \
       tests/test_battery_toast_theme.py \
       tests/test_quiet_subprocess.py
```

### On Windows PowerShell:
```powershell
$env:QT_QPA_PLATFORM="offscreen"
$env:INNIOASIS_HEADLESS="1"
python -m pytest tests/test_sp_flash_usb_search.py tests/test_rar_extraction.py tests/test_shed_mtkclient.py tests/test_donations_and_release_labels.py tests/test_theme_segmented_and_settings.py tests/test_battery_toast_theme.py tests/test_quiet_subprocess.py
```
