# Porting Assessment — Online Firmware Lookup, Listing & Donations Modal into the InniUpdaterChin Interface

**Scope of this document.** Assess how to port three features that exist in the
*online* Innioasis Updater ("Updater CE", `firmware_downloader.py` v2.0.4 and the
`innioasis.app` website) into the *InniUpdaterChin* desktop application
(`InnioasisUpdater.exe` v1.1.2, PySide6, Chinese-first), producing a **clean,
compilable Windows / macOS / Linux application** in `NeoPrototype/` that can
**flash all firmwares to all devices InniUpdaterChin supports**.

---

## 1. What each side is today

### 1.1 InniUpdaterChin (the target interface) — v1.1.2

A PySide6 (Qt 6) desktop app, frozen with PyInstaller 6.0.0 / Python 3.11,
reconstructed from bytecode in `InniUpdaterChin/` and documented in
`InnioasisUpdater-Documentation/`.

| Layer | What it is |
|---|---|
| `app.runtime_paths` | Resolves `BUNDLE_DIR` (`sys._MEIPASS`) vs `INSTALL_DIR` (exe folder) |
| `app.state` | `FlashState` S1–S6 enum, `FlashContext` dataclass, `StateMachine` with a transition table |
| `app.i18n` | Translator, 6 languages (zh-CN, en, ja, de, fr, es) |
| `app.flash_service` | `ExtractWorker` / `FlashWorker` / `FlashService`: ZIP+RAR extraction, **own** line-based scatter parser, dual flash backend |
| `mtk_api` | ~30-line wrapper over the bundled mtkclient (`init`, `connect`) |
| `app.ui.*` | `MainWindow` (left nav + pages), `SelectPackagePage`, `FlashPage`, `RetryPage`, `ErrorPage`, dialogs, custom widgets |
| Bundled engines | `_internal/mtkclient` (full mtkclient 2.1.4 source, 60 MB, GPLv3) + `SP_Flash_Tool/` (SP Flash Tool v5.2016, Windows-only) |

The flash pipeline (`FlashWorker._do_flash`) is:

```
extract package (ZIP/RAR) → find scatter → validate images →
    Windows: SP Flash Tool console mode (format-download) 
    macOS/Linux: mtkclient in-process, one write per partition
```

Crucially, the Chin app is **local-only**: `SelectPackagePage` opens a file
dialog; there is no download, no online listing, no donation UI. The app is
also device-agnostic: it flashes **any** ZIP/RAR containing a MediaTek scatter
file. It does not hard-code Y1/Y2; the scatter's `platform` and partition table
drive everything. Y1 (MT6572) and Y2 (MT6582) work because their packages
contain `MT6572_Android_scatter.txt` / `MT6582_Android_scatter.txt`, and the
bundled mtkclient loaders (`MTK_DA_V5/V6`, `MTK_AllInOne_DA_*`) cover the whole
MTK range.

### 1.2 The online side (features to port)

**Online firmware lookup + listing** — "Updater CE" (`firmware_downloader.py`):

1. A **catalog** of software per device model. Two sources exist and agree:
   - `firmware-catalog.js` (website): 11 entries `{slug, name, model, repo,
     packageName, guide, description}` covering Y1/Y2 × Original Software,
     Rockbox, Solar, JJ Launcher, Inniclassic, Y2Player, Koensayr.
   - `slidia_manifest.xml` (fetched by the app): `<package name=... repo=...
     device=Y1|Y2 .../>` — the live manifest the app actually consumes.
2. A **GitHub releases client** (`GithubApi` class, ~line 6150):
   - `GET https://api.github.com/repos/{repo}/releases?per_page=100` and
     `/releases/latest`, with token rotation (`config.ini` PATs), unauthenticated
     fallback, exponential-backoff retry, and a **persistent 24 h disk cache**
     (`~/.innioasis-updater/.cache/releases/`) so listings work offline.
   - `rom*.zip` asset parsing: hardware **Type A/B**, **resolution**
     (360p/240p/native), **model family** (Y1/Y2/dual) — `rom_y2.zip`,
     `rom_360p.zip`, `rom_360p_type_b.zip`, legacy tags like `360p-type-b-v0.3`.
   - Release filtering: `is_download=true`-style rules, "stable" override,
     nightly filter, latest-release merge.
3. A **download + install flow** (`_download_release_zip`, `ReleaseInstallWorker`):
   streaming download with progress → extract → copy into an install layout →
   hand off to the same SP Flash Tool / mtkclient backends the Chin app uses.
4. UI: Device Model + Software dropdowns, release list, release notes pane,
   "Install / Restore" button, offline/loading empty states, "Browse Files"
   fallback for a local rom.zip.

**Donations modal** (`show_donation_dialog`, ~line 24837; website
`credits.html` / `support_devs.html`):

- Modal `QDialog` "Support Innioasis Updater & Themes Gallery".
- Top card alternating between a **monthly-goal progress bar** ("You've helped
  us cover $X of our $200 costs for this month") and a rotating **donor
  ticker** (top-supporter groups + recent single donors), with crossfade
  animations.
- Developer header ("It takes *you*."), randomized intro copy (family /
  community variants), opt-out checkbox after install success
  ("Don't ask me again after successful firmware installs").
- Payment grid: **Ko-fi**, **PayPal**, **Revolut**, **Patreon**, **Honeygain**
  (free), **crypto** (BTC / ETH / SHIB with copy-to-clipboard addresses).
- Data: `donors.csv` (name, amount, date, url, method) bundled + live fetch
  from `https://innioasis.app/donors.csv` on dialog open; `$200/month` target.

---

## 2. Port strategy

The core insight: **the Chin flash engine already flashes everything
InniUpdaterChin supports — the port adds an *online source* in front of it and
a *donation surface* around it.** No change to SP Flash Tool / mtkclient
dispatch, scatter parsing, or the state machine is required.

```
┌─────────────────────────── InniUpdaterChin UI (unchanged spirit) ───────────────┐
│  Home ── Select Package ── Flash ── Result/Retry        (left nav, S1–S6)        │
│                                                                                  │
│  SelectPackagePage now has two sources:                                         │
│    [Online]  Device Model ▸ Software ▸ Release list ▸ Download ▸  (new)          │
│    [Local]   File picker (existing behavior)                                     │
│        │                                                                         │
│        ▼  package_path (ZIP/RAR)                                                 │
│  FlashWorker (existing): extract → scatter → SP Flash Tool / mtkclient           │
│        │                                                                         │
│        ▼  success                                                                │
│  DonationDialog (new, ported from CE)  +  "Support" buttons on Home/Nav          │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### 2.1 What to port, and how

| # | Feature | Source (CE/website) | Port target (new module in `NeoPrototype/src`) |
|---|---|---|---|
| 1 | Catalog | `slidia_manifest.xml` + `firmware-catalog.js` | `catalog.py` — static catalog table, model/software grouping |
| 2 | GitHub releases | `GithubApi` class | `catalog.py` — `ReleasesClient`: fetch, filter, cache |
| 3 | rom*.zip variant rules | `_parse_rom_asset_variant`, `_classify_variant_model` | `catalog.py` |
| 4 | Download | `_download_release_zip` | `downloads.py` — `DownloadWorker` (QThread, progress signals) |
| 5 | Flash | Chin `app.flash_service` | `flash_service.py` — **faithful port** |
| 6 | State machine | Chin `app.state` | `state.py` — **faithful port** |
| 7 | UI pages | Chin `app.ui.*` + CE select UI | `ui/*` — Chin layout, CE select content |
| 8 | Donations | CE `show_donation_dialog` + `donors.csv` | `donors.py` + `donation_dialog.py` |
| 9 | i18n | Chin `app.i18n` (6 langs) | `i18n.py` — en + zh-CN initially, table-extensible |

### 2.2 Devices & firmwares covered ("all firmwares, all devices")

Because the flash engine is scatter-driven, the port is automatically capable
of flashing **every** package the Chin app can flash today:

- All catalog firmwares for Y1 (MT6572) and Y2 (MT6582) — Original, Rockbox,
  Solar, JJ Launcher, Inniclassic, Y2Player, Koensayr (`rom*.zip` releases).
- Any other MTK package with a scatter file (the bundled mtkclient loaders and
  SP Flash Tool DA handle the SoC family generically).
- Type A/B and 360p/240p/native variants — surfaced in the release listing via
  the ported `rom*.zip` classification, exactly like CE.

The one porting decision that matters: **do not try to rewrite the two
backends.** Reuse the bundled engines verbatim:
- `vendor/mtkclient/` — copy of `InniUpdaterChin/_internal/mtkclient` (source
  form, GPLv3 — keep its LICENSE).
- `SP_Flash_Tool/` — not vendored into the repo; located at runtime via
  `SP_FLASH_TOOL_DIR` env var, `INSTALL_DIR/SP_Flash_Tool`, or a dev path
  (same search order as the Chin app). On Windows the installer ships it; on
  Linux the CE app's `flash_tool_linux.zip` package is the equivalent.

### 2.3 Security / hygiene decisions

- **Do NOT copy `config.ini` GitHub PATs.** The CE app embeds live tokens; the
  prototype uses unauthenticated requests (60 req/h/IP) + the 24 h cache, and
  honors an optional `GITHUB_TOKEN` env var.
- **Do NOT copy donor payment URLs as secrets** — they are public (Ko-fi,
  PayPal.me, Revolut.me, Patreon, Honeygain, BTC/ETH addresses published on the
  website), so they stay as constants.
- Vendor `mtkclient` only if its GPLv3 terms are acceptable for the target
  distribution; otherwise depend on the pip package `mtkclient` instead.

### 2.4 Platform notes

| | Windows | macOS | Linux |
|---|---|---|---|
| Flash backend | SP Flash Tool (subprocess) | mtkclient (in-process) | mtkclient (in-process) |
| RAR | bundled `UnRAR.exe` / WinRAR | `brew install unrar` | `apt install unrar` |
| USB | MTK driver (preloader DA) | libusb (native) | libusb + udev rule `0e8d` |
| GitHub API | same | same | same |
| Build | `pyinstaller --onefile` / onedir + Inno Setup | `.app`/`.dmg` | AppImage/tar |

The Chin app dispatches on `platform.system() == "Windows"` only; macOS and
Linux share the mtkclient path. Keep that.

### 2.5 Risks & mitigations

1. **GitHub rate limits** (unauth 60/h, auth 5k/h): 24 h persistent cache,
   graceful offline state with cached listings, optional token.
2. **Repo/asset drift**: catalog repos move (CE already has
   `FIRMWARE_REPO_FALLBACKS`); keep the fallback map and make the catalog
   refreshable from the live manifest.
3. **RAR dependency**: on macOS/Linux `unrar` must be on PATH; degrade
   gracefully with the same error message as the Chin app.
4. **License**: mtkclient GPLv3 — if the app is distributed, it must comply
   (provide source, license notice). SP Flash Tool is MediaTek-proprietary and
   Windows-only; keep it an install-time payload, not a repo blob.
5. **i18n completeness**: the full 6-language string table exists in
   `app/i18n.pyc`; the prototype ships en + zh-CN and documents how to mine the
   remaining tables.

### 2.6 Effort estimate

| Work item | Effort |
|---|---|
| Assessment + scaffolding | done (this doc + repo layout) |
| Flash engine + state machine port | ~1 day (mostly mechanical from disassembly/docs) |
| Online catalog + releases client | ~0.5 day |
| Download flow + select-page UI | ~0.5 day |
| Donation modal + donors data | ~0.5 day |
| i18n, styling, packaging for 3 OSes | ~1 day |
| Hardware verification (Y1/Y2) | 1–2 days on real devices |

---

## 3. Deliverables in `NeoPrototype/`

- `ASSESSMENT.md` — this document.
- `src/` — clean, commented PySide6 app implementing §2.1 (the port).
- `vendor/mtkclient/` — vendored mtkclient (flash capability).
- `assets/` — `style.qss`, `donors.csv`, icons.
- `requirements.txt`, `pyproject.toml` — reproducible deps.
- `build/` — PyInstaller spec + per-OS build scripts.
- `README.md` — run/build instructions and how the port maps to the sources.
