# AGENTS.md

## AI skills (all agents and assistants)
- Reusable, agent-neutral procedures live in **`.agents/skills/`** (open Agent Skills layout):
  `flash-innioasis-device` (automate a firmware install end to end), `guide-updater-ce-install`
  (walk a user through the app or a portable ROM.zip pack) and `develop-y1-y2-firmware`
  (target existing firmware images, `slidia_manifest.xml`, packaging, mirrors, archive rules).
  `updater-ce-workflow` stays the developer/verification runbook for this repo.
- `scripts/sync_agent_skills.py` mirrors `.agents/skills/` into `.claude/skills/`, `.cline/skills/`
  and `.github/skills/` for tools that only read their own directory. **Edit the canonical copy
  only**, then run it; `--check` validates frontmatter and fails on a stale mirror.
- `docs/AI_SKILLS.md` indexes the skills, lists where each agent looks, and records the facts all
  Innioasis repositories must agree on (catalogue URL, archive, donation rule, Download Only).
  Keep sibling repos' copies identical rather than writing a second procedure.

## Local dev environment
- Dependencies live in **`.venv-build/`** (not `.venv/`, despite `.gitignore` listing it); the system
  `python3` has no `requests`/`PySide6`, so tests silently fail on import if you use it.
- Run one test without the whole suite: `/tmp/run_one.py <tests/file.py> <test_name>`
  (offscreen Qt, sets `QT_QPA_PLATFORM=offscreen`, prints `PASS <name>`). Recreate it if `/tmp` was cleared.
- **Lint with ruff**: `.venv-build/bin/python -m ruff check` (`…\Scripts\python.exe` on Windows;
  `requirements-dev.txt`). `pyproject.toml` gates `E9,F63,F7,F82` — syntax errors, undefined/misused
  names, bad `assert`/`format` — so the tree is green. Widen the rule set one family at a time;
  `vendor/mtkclient` is excluded because it mirrors the shipped build and must never be "fixed".
- `scripts/build_universal_app.py` **deletes and rebuilds `dist/*.app` in place**: a rebuild wipes any
  already-running app's bundle, so never build while the user is testing from `dist/`.
- Build entry points are `./build_macos.sh [--arch arm64|x86_64|universal2] [--dmg] [--brand …]`,
  `build_windows.{bat,ps1}` and `./build_appimage.sh --arch …`; a run builds **Updater CE only**,
  and `--brand mediatek_installer` (or `--brand all`, `-Brand both` on Windows) adds the MediaTek
  Installer. Per-arch commands are in `BUILD_TARGETS.md`.
- `/tmp/run_one.py` is not executable — run `.venv-build/bin/python /tmp/run_one.py <file> <test>`.
  A whole file is `.venv-build/bin/python -m pytest tests/<file>.py`.
- `tests/test_native_ux_enhancements.py` **cannot be run whole**: network icon preloads hang it and
  `test_caption_icon_follows_the_macos_icon_style` aborts ("Abort trap: 6") at teardown. Select with `-k`.
  Some of its failures are stale tests (`_sp_flash_tool_btn`) or host-appearance/order artifacts
  (`'#fff' not in styleSheet`, `640 == 680` window width) rather than regressions — check individually
  with `pytest tests/test_native_ux_enhancements.py::<name>` before blaming a change.

## Native-first UI (all platforms)
- The app must feel like a native desktop app, not a web page wrapped in Qt. Standard controls, menus,
  focus rings, tooltips, scrollbars and window chrome come from the host `QStyle`/palette; do not
  restyle them into app-drawn equivalents.
- Never bake a black or white text colour into a label, card, toast or prompt. Use the live host text
  role (`QPalette.WindowText` via `theme_text_color()`/`apply_explicit_foreground`) or a QSS
  `palette(window-text)` rule, and re-apply it on `PaletteChange`/`ApplicationPaletteChange` and in
  `refresh_theme()`. This is what carries a KDE theme's accent-coloured text and macOS light/dark
  switches into action lines such as connect, power-off and Keep Data questions.
- When two round chips form one segmented control, lay them out in a zero-spacing container and call
  `set_segmented_pair` on both. A surrounding row's general spacing must not separate the halves.
- Verify appearance with a non-default host palette (for example accent-coloured Plasma text) and a
  live light/dark switch, not only the default theme.

## Native macOS UI (PyObjC is bundled — AppKit is fair game)
- Prefer real OS controls: PyObjC/Cocoa is available and already used (`src/ui/glass.py` NSView interop).
  The user asks for OS-native tooltips, switches and iconography over hand-painted ones.
- **Never style `QToolTip` via QSS or force its palette** — either one puts the tip on `QStyleSheetStyle`
  and replaces the host's own tip (macOS help tag / WinUI), and rich-text tips then need hand-set colours.
  Keep tip text plain so the platform draws it.
- macOS draws app icons in the user's *Icon & widget style*, and it is reachable:
  `NSWorkspace.currentIconAppearanceConfiguration()` gives the theme + `resolvedIconTintColor()`, and
  `NSWorkspace.icon(forFile:)` returns an appearance-sensitive image (`NSISIconImageRep`). Verified: on a
  Tinted system the app's icon renders green-tinted rather than as the bundled PNG.
- `NSWorkspace.accessibilityDisplayShouldShowWindowTitlebarIcons()` is the "Show window title icons"
  setting, and there is no document-proxy-icon slot in this app (content extends under the traffic lights).
- The app's code runs in the bundled interpreter (the launcher `execv`s `Contents/Resources/python/bin`), so
  `scripts/build_universal_app.py` gives that process the app's own identity: `Info.plist` beside the
  interpreter plus an interpreter symlink under the app's name. Without it macOS calls the process
  `python3.11` (Dock, login-item "… is running in the background" banner) and `NSUserNotificationCenter` is
  nil, which pushed notices onto `osascript` and made them appear under Script Editor. Keep notifications
  native (`src/notifications.py` never posts via `osascript` at all — bundled or not — and
  `housekeeping/core.py`'s scheduled check posts natively or not at all) and keep the macOS login item
  pointed at the bundle launcher instead of `sys.executable`.
- The Software dropdown must never key off item *text*: canonical English names are the lookup keys, so the
  preset localisation rides in `Qt.UserRole` item data (`select_page._populate_software_combo`), and
  `retranslate()` re-labels rows in place. `display_software_name()` localises the label only.
- `_FitLabel` wraps into a *fixed* column (`INSTALL_TEXT_WIDTH`), so copy never reflows when the window
  widens; `_FlowRow` (flash_page) joins step + ETA onto one row when they fit and stacks them otherwise,
  and `prompt_stop` calls `set_forced_stacked(True)` so the sanity-check sentence still wraps.
- `_RoundNextButton` chips paint translucent discs (alphas in flash_page) and are `AlignVCenter` in their
  cards. To test their translucency paint the brush with `QPainter` — `QWidget.render()` fills an opaque
  background first, so a rendered chip always reads alpha 255.
- `MainWindow._apply_release_icon` must decide "generic squircle" from *no catalogue release / local tab*
  only: a downloaded release's unpacked working copy also contains a scatter file, and treating that as a
  local browse replaced the release's own `updater.jpg` with the settings cog on the install card.

## Reverse-engineering the shipped Chinese build (1.1.7)
- The reference macOS app (`/Volumes/InnioasisUpdater/InnioasisUpdater.app`) is a PyInstaller
  onedir bundle running **Python 3.10**: unmarshal its PYZ with `/opt/homebrew/bin/python3.10`,
  not `.venv-build` (3.13 → `bad marshal data`). Dump raw entries first with 3.13
  (`CArchiveReader(...).open_embedded_archive('PYZ.pyz').extract(name, raw=True)`), then parse with 3.10.
- Its **running UI is the frozen `app.*` package (14 modules) inside `PYZ.pyz`**, not mtkclient's GUI; the
  bundled plaintext `mtkclient/` (v2.1.4) is only the library it calls. Don't read `mtk_gui.py` for app behaviour.
- `app.i18n` defaults to **`en`** (`QSettings('Innioasis', 'Innioasis Updater')['language']`), labels are
  `English/中文/Deutsch/…`; the Chinese UI comes from a stored setting, not from the code default.
- Quick constant/API map instead of a decompiler: `/tmp/analyze_cn.py` (names, args, constants per module) and
  `/tmp/dis_cn.py <qualname>` (dis one function). Filter strings with `len >= 2` or `'中文'`/`'en'` vanish.
- `Contents/Resources/*` is a tree of **symlinks** into `Contents/Frameworks/` (`tools`, `QtCore`, `Python.framework`…),
  so `find`/`ls` there under-report — `tools/unar` really lives in `Frameworks/tools/`.
- `pyi-archive_viewer` is interactive: drive it with piped input
  (e.g. `printf 'O PYZ.pyz\nl\nQ\nQ\n' | ...`), never run it bare (it hangs the tool call).
- `mental_model/` is gitignored scratch for per-model reverse-engineering notes;
  suffix files with your model name and reuse `/tmp/cn_raw/*.marshal` dumps instead of re-extracting.

## Bundled `mtkclient`: the Chinese build's fork
- `vendor/mtkclient` i`, the real tree behind the
  `Contents/Frameworks/mtkclient` symlink). Its legacy DA write path, DRAM bring-up, USB read loop, error
  strings and `config.last_error` reporting are what make an install behave like the Chinese build at the
  flash stage, so **do not “fix up” those files towards upstream `bkerler/mtkclient`**: re-diff against the
  mounted bundle and take its version (`diff -rq --exclude='__pycache__' --exclude='*.pyc' $CN vendor/mtkclient`).
- Two files are deliberately *not* the bundle's copy:
  `mtkclient/Library/Hardware/__init__.py` (the bundle ships it empty, which would break the
  `hwcrypto_ssr.py` we also ship) and anything only this repo has (`mtkclient/Darwin/libusb-1.0.dylib`,
  `mtk_tcpip_*.py`, `Setup/`, `Tools/`, the READMEs) — keep those.
- Everything else the app needs is **additive runtime wrapping in `src/mtk_api.py`**: the cross-platform
  libusb backend, the config fields mtkclient's own argparse would have created, the stdout/stderr detach
  shield (the fork's `Library/utils.py` calls `sys.stdout.detach()` unguarded at import time), and the
  USB-stall guard in `usbread`. Never replace fork behaviour we can use as-is.
- The fork reads `config.write_chunk_size` for every legacy-DA transfer (1 MiB when unset).
  `flash_service.legacy_write_chunk_size()` supplies the reference build's 512 KiB / 256 KiB table — keep the
  two in step, and keep `config.last_error` surfaced in the guided failure copy.
- On Linux, never hardcode `libusb-1.0.so` in PyUSB loaders; end-user distros only ship `libusb-1.0.so.0`. Always search `libusb-1.0.so.0` first.
- On macOS, `libusb-1.0.dylib` in `Contents/Frameworks/` must remain Universal 2 (`x86_64` + `arm64`).

## Flashing back-end: recorded as WORKING
- The SP Flash Tool and MTKclient flash paths, method selection and failover (`MainWindow._other_method`,
  `_offer_backend_failover`), and the USB close/reset handling in `src/flash_service.py`, `src/mtk_api.py`
  and `src/linux_sp_flash.py` are the user-confirmed working back-end: installs complete without bricking
  devices when the connection is not interrupted.
- Preserve this logic. Change it only when the user explicitly asks for a change to it.
- **The host must stay awake for exactly the device window.** A flash or a Rockbox themes transfer that the
  OS suspends mid-write bricks the player, so `src/sleep_inhibit.py` holds a power assertion: named holds
  (`"install session"` via `process_guard.begin/end_install_session`, `"themes:<id>"` via
  `ThemesJourney.begin/finish`) so one finishing never frees the other, and `MainWindow._stop_running_install`
  ends both when a run is abandoned. Do not add ad-hoc acquire/release pairs in the widgets, and do not hold
  anything while the app is idle or finished — the hold must disappear with the task.

## Release visibility (Updater CE and the firmware archive)
- A published stable release from a repository named in `slidia_manifest.xml` must be listed for its
  model. Only `release_blocklist.csv` or the documented Rockbox `< 0.5` / Original Software `< 3.0.7`
  gates may hide one; no tag-parsing, asset-name or snapshot rule may.
- `releases_backup.json` is a periodic export, not the source of truth. It may answer the catalogue
  availability check, but a stale copy must never hide a source by itself: a repository added to the
  live manifest can have no row yet, and its first `stable-vX.Y` release may already be on GitHub.
  Fail open, confirm with the per-repo release cache or a live check, and only hide on a fresh,
  corroborated "nothing installable" answer.
- A future release tag that cannot be read as a dotted version still counts as stable when GitHub
  does not mark it prerelease/nightly; keep it visible rather than dropping it at a minimum-version
  gate. `stable-v0.6` from `rockbox-y1/rockbox` is the regression shape (`tests/test_stable_release_visibility.py`).

## Rockbox themes/fonts stage: one owner for its state
- `src/themes_journey.py` (`ThemesJourney`) owns the stage's decisions and state: transfer running,
  placement failed, power-on prompt pending, ADB adoption, multiple-players, the presence watcher,
  the open folder picker, and the stage-start rule (automatic starts obey the Settings switch).
- `MainWindow.themes` is created before the install card and passed in as `FlashPage(journey=...)`, so
  both widgets read the same object; a `FlashPage()` built on its own gets its own.
- `themes.active` is the one answer to "is a themes run on?" for the window's install-run lock, its
  device-lost guard and its close prompt; `themes.transfer_running` is the narrower question the card
  asks about what it is drawing. Neither has a twin on `MainWindow`.
- Add journey behaviour there, not as new `_theme_*` attributes on the two widgets. Widgets keep
  their widgets, timers, Qt workers, callbacks and copy; they call in for the decisions.

## Translations
- Every English UI string in `src/i18n.py` must ship with copy for every catalog in
  `assets/i18n/`, in that language (never a copy of the English sentence). Brand and product
  names (`Buy Me a Coffee`, `Boosty`) are the only strings left in English on purpose.
- **Never pass an English literal to a user-facing Qt API.** That includes `QLabel`,
  `QPushButton`, `QCheckBox`, `QAction`, `setText`, `setTitle`, `setWindowTitle`, `setToolTip`,
  `setPlaceholderText`, file-dialog titles/filters, `QMessageBox` text, table/list/tree items and
  headers. Add a key to `src/i18n.py`, wrap the call in `tr()`, and regenerate the catalogs with
  `.venv-build/bin/python scripts/i18n_fill.py --lang <code>` (or `--all --workers 8`).
  `tests/test_ui_literals.py` rejects new literals; `tests/test_i18n_completeness.py` rejects a
  merge where any picker language is missing the new key.
- `tests/test_donation_i18n.py` enforces this for the donations and appeal copy. Fill a gap with
  `fetch_google_translation` only after protecting `{placeholders}` and `<b>` markup, then keep the
  translation only if both survive; the match check in `scripts/sync_i18n.py` shows the form.
- `src/runtime_i18n.py` is the fallback, not the plan: it translates a widget whose text still
  matches an English source (including each text run of a rich-text label) and stores the result
  in the user's catalog. Add the catalog copy instead of relying on it.

## AI Contribution, Pull Releases & Quality Standards
- **Prohibit low-quality, automated, non-human-reviewed submissions**: AI models assisting with this codebase must produce robust, carefully validated code with full documentation integrity, preserving existing tests, interfaces, and architecture.
- **Mandatory hardware verification before pull release**: Before an AI model assists a contributor in preparing or making a pull request / pull release from their fork, the contributor must explicitly confirm that they have hands-on tested **both SP Flash Tool and MTKclient flash methods** across multiple available firmware packages/methods on real hardware, and verified that a number of firmwares installed successfully without regressions or connection stalls (this verification requirement applies to contributors creating pull releases from their forks; it does not apply if changes are not originating from a fork maintainer release).

