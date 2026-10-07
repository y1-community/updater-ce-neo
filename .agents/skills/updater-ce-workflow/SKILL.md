---
name: updater-ce-workflow
description: >-
  Workflows, testing runbooks, and troubleshooting procedures for Innioasis Updater CE
  (updater-ce-neo), covering SP Flash Tool console mode, MTKClient, headless tests, and Linux staging.
---

# Innioasis Updater CE — Developer & Verification Workflows

This skill provides operational procedures for testing, running, and maintaining the Innioasis Updater CE (`updater-ce-neo`) codebase.

## 1. Headless Test Suite Execution

Run the complete smoke test suite headlessly without requiring an active display server (X11 / Wayland):

```bash
cd /home/deck/Documents/Workspaces/UpdaterNeo/updater-ce-neo
QT_QPA_PLATFORM=offscreen python3 tests/smoke_test.py
```

### Key Test Coverage:
- `test_catalog`: Verifies software catalog and device model mappings (Y1 vs Y2).
- `test_rom_variant_parsing`: Validates Type A/B, 360p/240p/native resolutions, and model-specific asset prioritization (`rom_y2.zip` vs `rom.zip`).
- `test_i18n_languages`: Validates string coverage across all supported locales (`en`, `zh-CN`, `fr`, `es`, `de`, `ja`).
- `test_donors`: Verifies CSV parsing, negative amount accounting, monthly goal calculations, and top supporter aggregations.
- `test_scatter_parsing`: Tests line-based scatter parser against MT6572 and MT6582 scatters.

## 2. SP Flash Tool Console Mode Testing

When testing SP Flash Tool console mode:
- **Command shape**:
  `flash_tool -c format-download -s <scatter_path> -d MTK_AllInOne_DA.bin -t without -r`
- **Linux staging directory**:
  `~/.cache/innioasis-updater/linux_flash_tool/`
- **history.ini verification**: Ensure entries contain valid absolute paths:
  `grep -E "lastDir|scatterHistory" ~/.cache/innioasis-updater/linux_flash_tool/history.ini`
- **Log inspection**:
  `cat ~/.cache/innioasis-updater/linux_flash_tool/SP_FT_Logs/*/QT_FLASH_TOOL.log`

## 3. Linux Environment & Serial Port Diagnostics

To verify a Linux host for MediaTek flashing:
1. **Verify udev rules**:
   `ls -l /etc/udev/rules.d/99-innioasis-mediatek.rules /etc/udev/rules.d/99-ttyacms.rules`
2. **Check user group membership**:
   `groups` (must contain `dialout` on Debian/Fedora/SUSE or `uucp` on Arch/SteamOS/CachyOS)
3. **Check ModemManager conflict status**:
   `systemctl is-active ModemManager`
4. **Run Readiness Check in Python**:
   ```python
   from src.linux_sp_flash import verify_linux_flashing_readiness
   print(verify_linux_flashing_readiness())
   ```

## 4. Frozen Build & In-Process MTKClient Verification

When modifying `src/mtk_api.py` or `src/flash_service.py`:
- Ensure `sys.stdout` and `sys.stderr` are wrapped before importing `mtkclient`:
  ```python
  if sys.stdout is None:
      sys.stdout = open(os.devnull, "w", encoding="utf-8")
  if sys.stderr is None:
      sys.stderr = open(os.devnull, "w", encoding="utf-8")
  ```
- Always wrap in-process MTKClient execution with `except BaseException as e:` to capture `SystemExit` from DA loaders without crashing the Qt application.

## 5. MediaTek Flashing Front-End Interfacing Standards

Learned and adapted from `firmware_downloader.py` and the reference Chinese build (`decompiled_app`):

### 1. Connection Phasing
- **Waiting Phase (`STEP_WAITING`)**: Device is unplugged or waiting for BROM handshake. Keep status as `idle`.
- **Detection (`STEP_DETECT`)**: Triggered immediately upon USB port acquisition (`USB port detected`, `BROM connected`, or MTKClient `Port - Device detected`). Set `device_status = "connected"`.
- **DA Download (`STEP_DOWNLOAD_DA`)**: Triggered upon `Download DA now`, `Downloading & Connecting to DA`, `connect DA end stage`, or `% of DA has been sent`. Map DA progress (12% -> 18%) to prevent static UI.
- **Partition Writing (`STEP_WRITE`)**: Triggered upon `of image data has been sent` or MTKClient partition write loops. Map progress smoothly across 20% -> 95%.

### 2. Output Parser Harmonization
- All stdout lines and `QT_FLASH_TOOL.log` tail lines must pass through the unified classifier (`_classify_sp_stdout`).
- `config.guiprogress` in MTKClient must provide both `.emit(pos)` and `.__call__(pos)` to satisfy both `gui_utils.py` and direct callers.

## 6. Desktop Native Widget Styling & Verification Runbook

To maintain the native look across macOS 13+, Windows 10/11+, and Linux desktop environments (Breeze/Adwaita):

### 1. Platform-Specific Native Styling Architecture
- **macOS (`darwin`)**:
  - `QApplication.setStyle("macintosh")`
  - Retain Aqua/Cocoa control delegates for buttons, popup buttons, segmented controls, tabs, and progress indicators.
  - Native vibrancy and window buttons handled by `src.ui.glass`.
- **Windows (`win32` / Windows 10/11+)**:
  - `QApplication.setStyle("windowsvista")` (or `"windows"`)
  - Enable DWM immersive dark title bar (`DWMWA_USE_IMMERSIVE_DARK_MODE`).
  - Rely on Windows native control rendering and system fonts (`Segoe UI Variable Text` / `Segoe UI`).
- **Linux (`linux` / KDE Plasma, GNOME, XFCE)**:
  - Detect and respect system DE style (`breeze`, `adwaita`, or system default).
  - Use system `QPalette` lightness to adapt seamlessly to desktop dark/light themes.

### 2. Styling Rules of Thumb
- **No Global Control QSS**: Never set global CSS properties (`border-radius`, `background`, `::drop-down`, `::indicator`) on `QPushButton`, `QComboBox`, `QCheckBox`, `QRadioButton`, `QScrollBar`, or `QProgressBar`.
- **Theme via `QPalette`**: Set colors on `QPalette` (Window, WindowText, Base, Button, Highlight, etc.) so native OS delegates paint their bevels, states, and focus rings using the active theme.
- **Cursor Discipline**: Standard desktop buttons MUST use `Qt.ArrowCursor`. Reserve `Qt.PointingHandCursor` strictly for hyperlinks.
- **Card Replacement**: Avoid flat Tailwind-style web cards (`border-radius: 12px; background: #1e293b`); use `QGroupBox` or subtle native pane borders.
## 7. MediaTek Universal Flashing Engine (Chinese Parity Architecture)

When flashing MediaTek devices (Innioasis Y1/Y2, Timmkoo, MT6572, MT6582, MT6580, MT8382) via in-process MTKClient:

### 1. Scatter Addressing & MBR/EBR Bias
- Legacy platforms (`MT8382`, `MT6572`, `MT6574`, `MT6582`, `MT6580`) use MBR/EBR partition tables.
- Scatter files for MT6582 offset `EMMC_USER` linear addresses by `0x1400000` (20MB) relative to physical start address `0x0`.
- Always resolve physical offsets: for MBR devices, write by exact physical address, never named GPT writes.
- Never skip MBR or EBR1; they contain essential partition tables for device boot.

### 2. In-Process Android Sparse & Signed Image Handling
- **Android Sparse**: Check first 4 bytes for magic `0xed26ff3a` (`_ANDROID_SPARSE_MAGIC`). Always unsparse in-process before writing or write sparse chunks by region. Never write raw sparse files directly to flash.
- **Signed Image Headers**: MediaTek signed images have a 64-byte header (`_MTK_SIGN_HEAD_MAGIC` = `b"SSSS"`) and 236-byte footer (`_MTK_SIGN_TAIL_MAGIC` = `b"EEEE"`). Strip this wrapper when writing to raw partition offsets.

### 3. USB Segmentation on macOS/Linux
- MTK DA bulk USB transfers will time out on transfers exceeding 50MB.
- Set `LEGACY_SEGMENTED_WRITE_BYTES = 52428800` (50MB) and chunk packets to 256KB (or 512KB).
- On Darwin (`macOS`), disable fast USB mode (`usblib.set_fast_mode: self.fast = False`) to prevent `LIBUSB_ERROR_OVERFLOW`.

## 8. MTKClient Reference Parity & CLI Subprocess Verification

### 1. Direct Preloader Handshake Workflow (Timmkoo A5 & Y1)
When validating MT6572 / MT6582 devices:
1. Connect device in standard powered-off mode (Preloader VCOM `0x2000`).
2. Handshake directly via `DaHandler.connect()` -> `DaHandler.configure_da()`.
3. Confirm DALegacy uploads stage 1 (`MTK_AllInOne_DA_mt6590.bin`), verifies NAND/eMMC, and uploads stage 2 without initiating a BROM reboot.
4. Verify stage 2 connects with message: `Connected to stage2`.

### 2. Standalone Subprocess CLI Invocation
Test flashing headlessly using the `--flash-cli` entry point:
```bash
python3 src/app.py --flash-cli <extract_dir> <scatter_file> <platform> [package_path]
```
Ensure output streams clean IPC tokens:
- `[PROGRESS] <0-100>`
- `[STEP] <STEP_NAME>`
- `[LOG] <message>`
- `[RESULT] <1|0> <STATUS_CODE>`

### 3. macOS Native Liquid Glass & Universal2 Invariants
- `pyqt_liquidglass` provides the native macOS 15+ Liquid Glass effect (`NSGlassEffectView`).
- On macOS 13–14 (Ventura / Sonoma), automatically fall back to `NSVisualEffectView` with `NSVisualEffectMaterialBehindWindow`.
- Ensure Universal2 binaries retain arm64 and x86_64 slices for Python, PySide6, and libusb.

## 9. Inline Translation & Network Verification Runbook

### 1. Verification of Google Translate In-App Translation
To verify that release notes translation functions correctly:
```bash
.venv-build/bin/python -c "from src.translate import fetch_google_translation; print(fetch_google_translation('Initial release with Rockbox support', 'zh-CN'))"
```
Expected output:
```text
具有 Rockbox 支持的初始版本
```

### 2. Multi-Paragraph & Long Markdown Changelog Verification
Verify that large release notes with markdown formatting are translated via HTTP POST:
```bash
.venv-build/bin/python -c "
from src.translate import fetch_google_translation
text = '''## What's Changed\n* Fixed audio playback\n* Improved battery meter'''
print(fetch_google_translation(text, 'zh-CN'))
"
```
Expected output contains translated Markdown headers and bullet lists without HTTP 414 URI length errors.

