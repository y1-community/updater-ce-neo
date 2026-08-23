# Innioasis Updater — Neo Prototype

A clean, compilable **Windows / macOS / Linux** PySide6 application that merges:

1. **The InniUpdaterChin flash engine** — extract a firmware package (ZIP/RAR),
   locate its MediaTek scatter file, and flash via **SP Flash Tool** (Windows
   and Linux) or the bundled **mtkclient** (macOS, plus the fallback backend
   everywhere). Device-agnostic and fully **offline**: any package with a
   scatter file is supported (Y1 MT6572, Y2 MT6582, and the rest of the MTK
   family covered by the bundled DA loaders) — i.e. every Innioasis and
   Timmkoo audio player that ships a scatter-based firmware.
2. **The online firmware catalogue** — Device Model → Software → Release
   listing fetched from GitHub, with the same `rom*.zip` variant rules as
   Updater CE (Type A/B, 360p/240p/native, Y1/Y2/dual), a persistent 24-hour
   cache for offline use, and a streaming download into the flash pipeline.
   The software list itself is **live**: `slidia_manifest.xml` is fetched from
   innioasis.app (24 h per-user cache) with the built-in catalog table as the
   offline fallback (see `src/manifest.py`).
4. **App update checking** — a silent check shortly after launch (plus a
   "Check for Updates" sidebar button) compares the latest GitHub release of
   `UPDATE_REPO` against the installed version and, when newer, shows an
   update dialog with release notes, per-OS install guidance, and a
   direct download of the right asset (Windows `.exe` / macOS `.dmg` /
   Linux `.AppImage`/`.sh`). See `src/updates.py` and
   **[UPDATES.md](UPDATES.md)** for the 3-OS delivery plan.
3. **The donations modal** — monthly-goal bar + rotating donor ticker, Ko-fi /
   PayPal / Revolut / Patreon / Honeygain / crypto buttons, live `donors.csv`
   refresh from innioasis.app, and an opt-out install-success prompt.

See **[ASSESSMENT.md](ASSESSMENT.md)** for the full port map, platform notes,
and risk analysis.

## Layout

```
src/                    application package
  app.py                entry point
  paths.py              port of app.runtime_paths (BUNDLE_DIR/INSTALL_DIR, backends)
  state.py              port of app.state (FlashState S1–S6, FlashContext, StateMachine)
  i18n.py               translator (en + zh-CN; 6-language table in the original pyc)
  flash_service.py      port of app.flash_service (extraction, scatter parser,
                        SP Flash Tool + mtkclient backends, device monitor)
  mtk_api.py            port of mtk_api (thin mtkclient wrapper)
  catalog.py            online catalogue + GitHub releases client (ported from CE)
  downloads.py          streaming release download worker
  donors.py             donors.csv parsing, monthly-goal stats, remote fetch
  donation_dialog.py    donations modal (ported from CE show_donation_dialog)
  manifest.py           live slidia_manifest.xml fetch/parse/cache + catalog merge
  updates.py            app update checker (version compare, per-OS asset pick)
  ui/                   main window, select/flash/error/retry pages, dialogs, widgets
vendor/mtkclient/       vendored mtkclient 2.1.4 (GPLv3, keep its LICENSE)
assets/                 style.qss, donors.csv, icon.ico
build/                  PyInstaller spec + build script
```

## Run from source

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.app
```

`vendor/mtkclient` is found automatically in a dev checkout; on macOS/Linux the
flash path needs `unrar` on PATH (`brew install unrar` / `apt install unrar`),
and Linux needs the MediaTek udev rule (`SUBSYSTEM=="usb",
ATTR{idVendor}=="0e8d", MODE="0666", GROUP="plugdev"`).

### SP Flash Tool payload (Windows)

The Windows backend is located at runtime in this order:
`$SP_FLASH_TOOL_DIR` → the project root's `SP_Flash_Tool/` (dev) or the
installed app's folder (frozen) → a hard-coded dev fallback. To make Windows
flashing work, place SP Flash Tool v5.2016 (from the original InniUpdaterChin
install, `flash_tool.exe` + Qt4 DLLs + `MTK_AllInOne_DA.bin` + `Driver/`) at:

```
NeoPrototype/SP_Flash_Tool/     # dev mode  (INSTALL_DIR/SP_Flash_Tool)
NeoPrototype/tools/UnRAR.exe    # RAR extraction
```

These payloads are third-party/proprietary (MediaTek) and are git-ignored; the
same layout is produced next to the frozen exe by the build.

### Flash flow

After a package is selected (online download or local file), the app shows a
**"Please connect your {model}"** prompt. Power the device fully off, connect
it over USB, and flashing **starts automatically** once the device is detected
in download mode — via SP Flash Tool on Windows and Linux, via mtkclient on
macOS.

### Flash method (backend) selection

The flash page lets the user pick the backend manually while the tool is
searching (the search restarts with the same package):

- **Auto (recommended)** — SP Flash Tool on Windows/Linux, MTKClient on macOS.
- **SP Flash Tool** — MediaTek's official flasher. On Windows it uses the
  bundled payload; on Linux the community `flash_tool_linux.zip` release is
  downloaded and staged per-user on first use (x86/x86_64 only).
- **MTKClient** — open-source flasher; the only option on macOS (SP Flash
  Tool was never released for macOS). Available everywhere, including Windows.

The choice is persisted (`flash_method` setting); the macOS selector simply
omits the SP Flash Tool option.

## Build (Windows / macOS / Linux)

```bash
bash build/build.sh
# or directly:
pip install -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm build/InnioasisUpdater.spec
```

On Windows, put SP Flash Tool v5.2016 at `dist/InnioasisUpdater/SP_Flash_Tool/`
(or set `SP_FLASH_TOOL_DIR`) so the Windows backend is available (the build
script copies it from `InniUpdaterChin/SP_Flash_Tool` automatically). On Linux
the SP Flash Tool backend is staged at first launch (downloads
`flash_tool_linux.zip`, ~67 MB) with MTKClient as the automatic fallback; on
macOS only the bundled mtkclient backend is used. No extra payload is needed
beyond the app bundle on either platform.

## Notes & decisions

- **No embedded GitHub tokens.** The original CE ships PATs in `config.ini`;
  this port uses unauthenticated requests + the cache and honors an optional
  `GITHUB_TOKEN` environment variable.
- **Donation endpoints/addresses are public** (published on innioasis.app), so
  they live as constants in `src/config.py`.
- **mtkclient is GPLv3** — if you distribute this app, comply with GPLv3
  (source offer + license notices) and treat the MediaTek SP Flash Tool
  binaries as a separate, non-redistributable install-time payload.
- The app ships four languages (中文, English, Français, Español). The full
  six-language i18n table exists in `InniUpdaterChin/_pyz/app/i18n.pyc`; mine
  it to extend `src/i18n.py` with 日本語 (ja) and Deutsch (de).
