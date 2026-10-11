# Vendored minimal ADB binaries

The Rockbox 360p post-install wizard (`src/theme_pack.py`
`Rockbox360pSetupWizard`, via `src/adb.py`) needs a bare `adb` binary — just
the executable, without the rest of the Android platform tools — so a freshly
flashed player booted into Rockbox can receive the 360p theme/fonts merge
automatically and be rebooted, with no USB Storage Mode round-trip.

Drop one `adb` executable per platform directory:

| Directory      | Platform / arch              | File         |
|----------------|------------------------------|--------------|
| `macos-arm64/` | macOS Apple Silicon          | `adb`        |
| `macos-x64/`   | macOS Intel                  | `adb`        |
| `linux-x64/`   | Linux x86_64                 | `adb`        |
| `linux-arm64/` | Linux ARM64                  | `adb`        |
| `windows-x64/` | Windows x86_64               | `adb.exe`    |

Sources: Google's platform-tools ZIPs
(<https://developer.android.com/tools/releases/platform-tools>) — extract
only `platform-tools/adb[.exe]` (plus `AdbWinApi.dll`/`AdbWinUsbApi.dll` next
to it on Windows). Binaries are Apache-2.0 licensed; keep
`NOTICE` attribution if you redistribute them.

Lookup order in `src/adb.py:find_adb_binary()`:

1. `ADB_BINARY` environment variable,
2. vendored `tools/adb/<platform>/adb[.exe]` (bundle, install dir, checkout),
3. `adb` on `PATH`.

When no binary (or no device within 30 s) is available, the wizard falls back
to the USB Storage Mode folder picker — ADB is an accelerator, never a
requirement.
