# AGENTS.md

## Local dev environment
- Dependencies live in **`.venv-build/`** (not `.venv/`, despite `.gitignore` listing it); the system
  `python3` has no `requests`/`PySide6`, so tests silently fail on import if you use it.
- Run one test without the whole suite: `/tmp/run_one.py <tests/file.py> <test_name>`
  (offscreen Qt, sets `QT_QPA_PLATFORM=offscreen`, prints `PASS <name>`). Recreate it if `/tmp` was cleared.
- `scripts/build_universal_app.py` **deletes and rebuilds `dist/*.app` in place**: a rebuild wipes any
  already-running app's bundle, so never build while the user is testing from `dist/`.
- Build entry points are `./build_macos.sh [--arch arm64|x86_64|universal2] [--dmg] [--brand …]`,
  `build_windows.{bat,ps1}` and `./build_appimage.sh --arch …`; a run builds **both** front ends
  unless `--brand` names one. Per-arch commands are in `BUILD_TARGETS.md`.
- `/tmp/run_one.py` is not executable — run `.venv-build/bin/python /tmp/run_one.py <file> <test>`.
  A whole file is `.venv-build/bin/python -m pytest tests/<file>.py`.
- `tests/test_native_ux_enhancements.py` **cannot be run whole**: network icon preloads hang it and
  `test_caption_icon_follows_the_macos_icon_style` aborts ("Abort trap: 6") at teardown. Select with `-k`.
  Some of its failures are stale tests (`_sp_flash_tool_btn`) or host-appearance/order artifacts
  (`'#fff' not in styleSheet`, `640 == 680` window width) rather than regressions — check individually
  with `pytest tests/test_native_ux_enhancements.py::<name>` before blaming a change.

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
- `vendor/mtkclient` is **the mtkclient exactly as the Chinese 1.1.7 app ships it** (Copied from
  `/Volumes/InnioasisUpdater/InnioasisUpdater.app/Contents/Resources/mtkclient`, the real tree behind the
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
