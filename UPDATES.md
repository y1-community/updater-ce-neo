# Update Delivery Plan — Windows / macOS / Linux

How the Neo app checks for, downloads, and installs its own updates, and how we
will deliver updates on the three desktop platforms. The in-app **checker** is
implemented today; the **delivery mechanics** below are the roadmap.

---

## 1. How the checker works (implemented)

- **Source of truth:** GitHub Releases of `UPDATE_REPO`
  (`src/config.py`, default `y1-community/Innioasis-Updater`, overridable via
  the `GITHUB_UPDATE_REPO` env var / code when the app gets its own channel).
- **Versioning:** release tags are compared against `APP_VERSION` (semver-ish:
  `2.0.4`, `v1.2.0`). Tags that don't parse as versions are ignored.
- **Cadence:** a silent auto-check ~3 s after launch (once per session), plus a
  manual **"Check for Updates"** button in the sidebar. Fetches go through
  `ReleasesClient` — token-aware, rate-limit-aware, 24 h per-user cache
  (`<cache>/updates/{repo}.json`) — and any failure is silent on auto, surfaced
  on manual.
- **Prompts:** when a newer version exists, the `UpdateAvailableDialog` shows
  the new/current version, release notes, a per-OS install guide, and three
  actions: **Download Update** (opens the right asset for your OS, picked by
  extension + architecture ranking in `updates.pick_platform_asset`),
  **Remind Me Later**, and **Skip This Version** (remembered in settings).
- **Asset naming convention** the picker understands (extensions in priority
  order): Windows `.exe/.msi/.zip`; macOS `.dmg/.pkg/.zip`; Linux
  `.AppImage/.tar.gz/.sh/.deb/.rpm`, preferring `universal`/`x86_64`/`amd64`
  over `arm64`/`aarch64` over unmarked.

## 2. Delivery per OS

### Windows — installer + "download & run" (v1), silent update (v2)

- **Packaging:** PyInstaller onedir → Inno Setup (or NSIS) installer
  (`installer.exe`), which places `SP_Flash_Tool/` and `tools/UnRAR.exe`
  beside the exe and registers the app.
- **v1 (now):** the update dialog downloads/opens the new `.exe`; the user runs
  it. Inno replaces the old install; the per-user data (`%LOCALAPPDATA%`,
  settings, downloads, `.cache`) survives untouched.
- **v2:** silent/live update — run the installer with `/VERYSILENT`, or adopt
  WinSparkle (Sparkle port) for in-app auto-update. Caveats to solve: file
  locks while the app runs (Inno handles by restart), SmartScreen/AV trust
  (needs code signing), and SP Flash Tool's Qt DLLs must never be half-updated
  while a flash runs (guard: refuse update mid-flash).

### macOS — .dmg "download & drag" (v1), Sparkle (v2)

- **Packaging:** PyInstaller `.app` → `hdiutil` .dmg (`Innioasis.Updater.for.Mac.dmg`).
- **v1 (now):** dialog opens the `.dmg`; user mounts it and drags the app into
  `/Applications`. Data lives in `~/Library/Application Support` and survives.
- **v2:** Sparkle — the de-facto standard. Requires the app bundle be
  **code-signed and notarized** (Developer ID); Sparkle verifies EdDSA
  signatures on update feeds. Without signing, Gatekeeper makes self-replace
  updates painful, so v1's manual drag is the pragmatic start.
- **Note:** our flash backend on macOS is mtkclient (in-process), so an update
  only needs the app itself replaced — no driver payload to migrate.

### Linux — AppImage (v1 + natural fit), packages later

- **Packaging:** PyInstaller onedir → **AppImage** (`InnioasisUpdater-*.AppImage`).
  The AppImage is a single executable file: updating = downloading the new file
  and swapping it — the simplest possible story, and it matches the current
  `run_linux.sh` launch script (which can be repointed at the AppImage).
- **v1 (now):** dialog downloads the `.AppImage`; the guide says "make it
  executable and run it". Data lives in `~/.cache` / `~/.local/share`.
- **v2:** `appimageupdatetool` (delta updates) or AppImageUpdate-in-app; later,
  `.deb`/`.rpm` via fpm for distro-integrated installs (still keep AppImage as
  the portable default). The SP Flash Tool Linux payload is staged per-user
  separately (`src/linux_sp_flash.py`) and is untouched by app updates.
- **Caveat:** the Linux flash backend prefers the staged SP Flash Tool; never
  delete or refresh `<cache>/linux_flash_tool/` during an app update.

## 3. Release process (applies to all OSes)

1. Bump `APP_VERSION` in `src/config.py` — the single source of truth.
2. Build per OS (`bash build/build.sh` on each; Windows also runs Inno).
3. Upload to a GitHub release tagged `v{APP_VERSION}` with assets named per the
   convention in §1 (keep the existing `installer.exe` / `.dmg` /
   `run_linux.sh` names so legacy installs keep matching).
4. Add release notes (shown in the update dialog) — keep them short and
   translated-friendly (the dialog shows them verbatim).
5. (v2) attach checksums / signatures (`.sha256`, Sparkle EdDSA, or
   `appimageupdatetool` signatures).

## 4. Guard rails

- **Never prompt mid-flash.** The update dialog and auto-check are disabled
  while `S4_FLASHING`; "Check for Updates" shows the dialog only when idle.
- **Skip is remembered** (`update_skipped_version`); a skipped version is never
  auto-reoffered (manual check still shows it).
- **Offline is quiet.** Auto-check failures are invisible; the manual check
  explains the failure.
- **Rate limits.** One cached `/releases/latest` call per day per install
  (24 h cache), reusing the same token/rate-limit machinery as the firmware
  catalogue.
