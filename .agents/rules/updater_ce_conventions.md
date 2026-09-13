# Innioasis Updater CE — Development Guidelines & Invariants

## 1. Branding & Naming Standards
- **User-Facing Title**: The application name must ALWAYS be **"Innioasis Updater CE"** across all user-facing surfaces:
  - Window title: `Innioasis Updater CE v{APP_VERSION}`
  - Application launcher: `updater-ce` (with legacy symlinks `innioasis-updater` and `updater-ce-neo`)
  - Desktop entry: `Name=Innioasis Updater CE`
  - Installer: `Innioasis Updater CE Installer`
  - All dialogs and translated strings (`src/i18n.py`)
- **Internal / Repo Identifiers**: While the remote GitHub repository is named `updater-ce-neo`, user-facing code, UI elements, and documentation must strictly omit the word "Neo".

## 2. Business Logic & Interfacing Invariant
- The business logic must faithfully mirror `InniUpdaterChin` (the Chinese vendor reference build):
  - **Scatter-Driven**: Flashing must be driven dynamically by the package's scatter file (`MT6572_Android_scatter.txt` / `MT6582_Android_scatter.txt`) rather than hard-coding device SoCs.
  - **State Machine**: Adhere strictly to the `S1_READY` -> `S2_PACKAGE_READY` -> `S3_DEVICE_DETECTED` -> `S4_FLASHING` -> `S5_USB_DISCONNECTED` -> `S6_RETRY` lifecycle.
  - **Dual Backend**:
    - Windows: SP Flash Tool console mode (`format-download`) with MTKClient fallback.
    - Linux: SP Flash Tool console mode (when staged) with MTKClient fallback.
    - macOS: In-process MTKClient backend.

## 3. Network & Download Reliability
- Large firmware packages (>300 MB) must use the resilient download engine in `src/downloads.py`:
  - Stream into `.part` temporary files, atomically renaming on verified completion.
  - Support resumable downloads via HTTP `Range: bytes={downloaded}-` headers.
  - Implement read timeouts (25s) and automatic reconnection with backoff (up to 5 retries).
  - Use 128 KB buffer chunks and throttle progress emissions to integer percentage changes (100 signals max) to prevent Qt event loop starvation.

## 4. Linux Environment Staging
- Maintain universal Linux compatibility across Arch, Debian/Ubuntu, Fedora, openSUSE, and derived distros:
  - Automatic staging of `flash_tool_linux.zip` to `~/.cache/innioasis-updater/linux_flash_tool/`.
  - Bundled `libpng12.so.0` in `assets/compat/` copied to the runtime library directory.
  - MediaTek udev rules (`99-innioasis-mediatek.rules`) configuring `0e8d:0003` and `0e8d:2000` with `TAG+="uaccess"`.
  - Distro-aware serial group assignment (`uucp` on Arch-based systems, `dialout` on Debian/Fedora/SUSE).

## 5. macOS MTKClient & Universal MediaTek Invariants
To maintain reliable flashing across all models and SoCs (legacy MT6572/MT6582 and modern 64-bit MT67xx/MT68xx/MT81xx) on macOS via MTKClient:

### A. macOS LibUSB Transport (`usblib.py` & `Port.py`)
- **No Fast Mode on Darwin**: Apple's `IOUSBHostFamily` kernel driver throws `LIBUSB_ERROR_OVERFLOW` on transfer requests smaller than `wMaxPacketSize`. Fast mode must be forced `False` on macOS, and all reads must request at least `wMaxPacketSize` (or 4096 on overflow), using a queue buffer for excess bytes.
- **Handshake Packet Sizing**: `run_handshake()` must read echoes with `ep_in(maxinsize)`, NEVER `ep_in(1)` which triggers an immediate overflow crash on macOS.
- **Non-BROM Preloader PIDs**: Devices in preloader mode (PID `0x2000`) require a wake byte (`0xa0`) and response flush before handshake.
- **VID-Only Device Discovery**: Devices must be discovered by MediaTek VIDs (`0x0E8D`, etc.) without filtering on `bDeviceClass=devclass` (many MTK devices enumerate with class `0`).
- **Endpoint Stall Recovery**: Pipe errors must attempt endpoint recovery via `clear_halt(EP_IN)`.

### B. Download Agent (DA) Payloads
- The bundled `vendor/mtkclient/mtkclient/payloads/` directory MUST retain all vendor DA binaries:
  - `da_x.bin` (64-bit XFlash DA for modern Helio/Dimensity SoCs)
  - `da_xml_64.bin` & `da_xml.bin` (XML DAs)
  - `DA_BR.bin`
- Modern SoCs using XFlash must use `_SEND_PARAM_CHUNK = 0x100000` (1 MB) to prevent 8x throughput degradation and timeouts caused by 512-byte per-syscall overhead.

### C. Partition & Preloader Flashing Engine
- **Preloader Target Routing**: Preloader writes must route to `boot1`, `boot2`, or composite `boot1_boot2`, never to the generic `user` partition.
- **BRLYT Boot Header Synthesis**: Raw preloader binaries without headers must be automatically wrapped with `EMMC_BOOT`/`UFS_BOOT` and `BRLYT` headers so the device ROM bootloader can execute them.
- **Address Bias Calibration**: For legacy MBR/EBR scatters, calibrate linear scatter addresses against physical device MBR partition offsets before flashing to prevent writing at incorrect offsets.
- **GPT Validation & Sync**: For modern devices, validate GPT table geometry against scatter partitions prior to writing image files.
- **Process & Handle Isolation**: On macOS/Linux, ensure in-process MTKClient execution handles all `BaseException` instances and detaches USB handles cleanly on cancellation to protect the Qt GUI event loop.

