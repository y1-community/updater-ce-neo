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
