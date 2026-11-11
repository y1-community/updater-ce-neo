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
| **Windows x86 / x64** | **SP Flash Tool v5.1904** (Console Mode) | MTKClient (optional, shed on verified installs) | `zipfile` (ZIP) / `tools\UnRAR.exe` (RAR) | MediaTek VCOM driver installed separately |
| **Windows ARM64** | **MTKClient** | SP Flash Tool (experimental via Prism emulation) | `zipfile` (ZIP) / `tools\UnRAR.exe` (RAR) | WinUSB / PyUSB via bundled `libusb-1.0.dll` |
| **macOS (Universal 2)** | **MTKClient** | None (SP Flash Tool does not ship for Darwin) | `zipfile` (ZIP) / `tools/unar` (RAR) | PyUSB via bundled Universal 2 `libusb-1.0.dylib` |
| **Linux (x86_64)** | **SP Flash Tool** | MTKClient | `zipfile` (ZIP) / `tools/unar` (RAR) | `udev` rules + host `libusb-1.0.so.0` |
| **Linux (AArch64)** | **MTKClient** | None | `zipfile` (ZIP) / `tools/unar` (RAR) | `udev` rules + host `libusb-1.0.so.0` |

---

## 2. Prerequisites & Development Environment

### Common Prerequisites
- **Python 3.10 – 3.14**; 3.12 recommended (PySide6 6.12's `none_dealloc` fix needs 3.12+).
- Nothing else: each build script runs `scripts/bootstrap_build_env.py`, which finds a suitable
  interpreter, creates `.venv-build/`, installs `requirements.txt` + `requirements-build.txt` and
  verifies the modules the PyInstaller specs import. A clone needs no manual `pip install`:
  ```bash
  python3 scripts/bootstrap_build_env.py           # set up / verify, prints PYTHON=<path>
  python3 scripts/bootstrap_build_env.py --check   # verify only
  python3 scripts/bootstrap_build_env.py --force   # reinstall the requirement sets
  ```
- Set `PYTHON=/path/to/python3.12` to build with an interpreter of your own; it is verified but
  never modified.

---

## 3. Windows Build Process

The Windows packaging pipeline produces, under the folder for the target
architecture (`dist\windows` for x64, `dist\windows-arm64` for ARM64, so one
never overwrites the other):
1. **Portable Application Directory** (`dist\windows[-arm64]\<BrandSlug>\`)
2. **Portable ZIP Archive** (`dist\windows[-arm64]\<BrandSlug>-Portable-<Version>[-arm64].zip`)
3. **Inno Setup Standard Windows Installer** (`dist\windows[-arm64]\<BrandSlug>-Setup-<Version>[-arm64].exe`)
4. **Microsoft Store MSIX Package** (optional via `-Msix`)

ARM64 packages must be built on Windows on ARM: PyInstaller cannot
cross-compile, and `build_windows.ps1` refuses to label an x64 bundle as ARM64.

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
   All child processes (`adb.exe`, `flash_tool.exe`, `powershell.exe`) run with `STARTF_USESHOWWINDOW` (`wShowWindow = SW_HIDE`) and `CREATE_NO_WINDOW (0x08000000)` via `src/quiet_subprocess.py` to prevent CMD console windows from flashing on screen.
2. **MediaTek SP Flash Tool Payload**:
   The x86 SP Flash Tool binary (`SP_Flash_Tool\flash_tool.exe`) and download agent binaries (`MTK_AllInOne_DA.bin`, `DA_PL.bin`, `DA_SWSEC.bin`) are automatically verified and placed beside the main executable.
3. **UnRAR Packaging**:
   `tools\windows\UnRAR.exe` is bundled into `tools\UnRAR.exe` to allow extraction of `.rar` firmware packages matching the Chinese market release behavior.
4. **MediaTek Driver**:
   Setup does not install or execute a bundled MediaTek VCOM driver. Install the driver separately before flashing. This also keeps Setup from running an unsigned helper from `{tmp}`. Portable builds still contain a manual `1. Install Driver First and Reboot PC.exe` entry for users who need it.
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

### Linting (ruff)

`ruff` is the only linter. Its configuration lives in `pyproject.toml` (`[tool.ruff]` /
`[tool.ruff.lint]`) and gates syntax errors, undefined or misused names and broken `assert` /
`format` usage — the mistakes that would otherwise only surface on an error path. Install it with
`pip install -r requirements-dev.txt` (it is already present in `.venv-build`), then:

```bash
python -m ruff check          # whole tree
python -m ruff check src/ui   # one area while working
```

The rule set is deliberately narrow so the existing tree is green. Widen `select` in `pyproject.toml`
one rule family at a time — `I` (import order) and `E501` (line length) would currently flag most of
the legacy app, and reformatting it in one sweep is not a change anyone can review. `vendor/mtkclient`
is excluded because it mirrors the shipped Chinese build (see `AGENTS.md`).

---

## 7. macOS Universal 2 and Intel verification

`./build_macos.sh --arch universal2` produces one bundle carrying an `arm64` and
an `x86_64` slice of every Mach-O it ships. The Apple Silicon slice is exercised
by the normal development flow; the Intel slice is not, because the project does
not run an Intel Mac day to day. A release that claims Intel support should be
checked on Intel hardware before it ships.

### 7.1 Checks that need no Intel hardware

```bash
APP="dist/macos/Innioasis Updater.app"
python3 scripts/audit_universal2.py "$APP"
codesign --verify --arch arm64  "$APP"
codesign --verify --arch x86_64 "$APP"
```

Expected: `Scanned <n> Mach-O files; highest minos = 12.0`, then
`RESULT: OK (all universal2, Monterey-compatible)`, then silence from both
`codesign` calls. The audit names the offending file when a slice or the
deployment target is wrong, but it does not look at what each slice links
against. That link check decides whether the **arm64** slice can start on a Mac
with no Rosetta, so run it when a release is cut:

```bash
python3 - "$APP" <<'PY'
import subprocess, sys
from pathlib import Path

app = Path(sys.argv[1])
magics = (b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe")

def run(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout

def is_macho(p):
    with open(p, "rb") as fh:
        return fh.read(4) in magics

machos = [p for p in app.rglob("*") if p.is_file() and not p.is_symlink() and is_macho(p)]
archs = {p: run("lipo", "-archs", str(p)).split() for p in machos}
problems = []
for p in machos:
    if "arm64" not in archs[p]:
        problems.append(f"{p.relative_to(app)}: no arm64 slice")
        continue
    own = {line.strip() for line in run("otool", "-arch", "arm64", "-D", str(p)).splitlines()[1:]}
    for line in run("otool", "-arch", "arm64", "-L", str(p)).splitlines()[1:]:
        dep = line.strip().split(" (compat")[0].strip()
        if not dep or dep in own or dep.endswith("):"):
            continue
        if dep.startswith(("/usr/lib/", "/System/", "/Library/Apple/")):
            continue
        if dep.startswith("@"):
            inside = [q for q in machos if q.name == Path(dep).name]
            if any("arm64" in archs[q] for q in inside):
                continue
            problems.append(f"{p.relative_to(app)}: {dep} (no arm64 inside bundle)")
        else:
            problems.append(f"{p.relative_to(app)}: {dep} (outside the bundle)")
print("\n".join(problems) if problems else "OK: every arm64 dependency resolves inside the bundle")
PY
```

It prints `OK: every arm64 dependency resolves inside the bundle`, or the file
and the dependency that is wrong.

Do not substitute a raw `strings` grep for that check. Qt, shiboken and `unar`
legitimately embed their own build paths (`/Users/qt/work/...`, `/Users/dag/...`),
and the launcher embeds `/opt/homebrew/bin/python3` as a last-resort interpreter
fallback; none of those can be loaded. A `strings` hit is not a defect, a hit in
`otool -L` is.

### 7.2 What has been exercised without Intel hardware

On an Apple Silicon Mac the bundle's Intel slices were run end to end through
Rosetta:

```bash
arch -x86_64 "$APP/Contents/MacOS/Innioasis Updater"
```

The process reported `Code Type: X86-64 (translated)` and started normally:
`Qt binding: PySide6 6.9.3 on Python 3.11.16`, cached catalogue loaded,
`notice.xml` refreshed from the network, no dyld errors. Importing
`PySide6.QtWidgets`, `usb`, `Crypto` and `capstone` under `arch -x86_64`
succeeded as well.

That proves the Intel slices and their bundled dependencies resolve and start
under emulation. It does not prove native execution on Intel silicon, and says
nothing about Intel GPUs, Intel USB/IOKit behaviour, or Gatekeeper on the Intel
macOS versions.

### 7.3 What an Intel Mac must confirm

1. Record the machine: `sw_vers`, `sysctl -n hw.model`, and
   `system_profiler SPUSBDataType | grep -i -A3 mediatek` with the player attached.
2. Launch through Gatekeeper from `/Applications`; for a downloaded copy run
   `xattr -dr com.apple.quarantine "/Applications/Innioasis Updater.app"` first.
   It must open with no "damaged", "cannot be opened" or "requires a newer
   version of macOS" dialog.
3. Confirm the slice. The launcher `execv`s the bundled interpreter, so the live
   process is not `Contents/MacOS/Innioasis Updater`:

   ```bash
   pid=$(ps -Ao pid=,comm= | grep "Resources/python/bin/Innioasis Updater" | grep -v grep | awk '{print $1}' | head -1)
   vmmap -summary "$pid" | grep "Code Type"
   ```

   On Intel this must print `X86-64`. An `ARM64` process means the wrong bundle
   was shipped.
4. Version and identity: `2.5.4` and `com.innioasis.updater`
   (`/usr/libexec/PlistBuddy -c "Print :CFBundleIdentifier"` on the bundle's
   `Info.plist`).
5. UI pass on Intel Qt: native menus and window chrome, host tooltips,
   scrollbars, light/dark switch without a relaunch, and the Software dropdown
   selecting firmware by name in every supported language.
6. Network: the release list populates (cached list plus a refresh from
   `innioasis.app`), and the notice area renders.
7. Flash: macOS has no SP Flash Tool, so MTKClient is the only backend here.
   Detect a player, install one firmware package to completion, then a second
   one. Watch for USB stalls or a disconnect mid-write.
8. Themes and fonts: install a Rockbox theme or font pack. That flow spawns the
   bundled `adb` and `unar`, which must run as `x86_64` from
   `tools/adb/macos-arm64/adb` and `tools/macos/unar`.
9. Notifications and login item: a finished install posts a banner under
   "Innioasis Updater" (not Script Editor), and start-at-login launches the
   bundle rather than `python3.11`.
10. Quit and relaunch, including the single-instance hand-off when a second copy
    is opened while the first is running.

### 7.4 Reporting an Intel-only failure

Attach `~/Library/Application Support/Innioasis Updater/logs/updater.log` and
`mtkclient.log` (same content as the in-app log viewer), the
`vmmap ... | grep "Code Type"` line, `sw_vers`, `sysctl -n hw.model`, and
`otool -L` of the binary named in the error. Record the failure against the
release rather than relabelling the bundle Apple Silicon only.
