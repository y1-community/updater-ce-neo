# Terminal mode on old, small and headless Linux machines

The AppImage, the portable tarball and the portable zip all carry Qt. That is
the right default, but it is not installable everywhere: PySide6 has no wheel
for 32-bit ARM or 32-bit x86, a Pi Zero has no desktop to draw into, and a
server you reach over SSH has no display at all. In each of those cases you
still want to download firmware and flash a player.

There are two ways in, and both use the same code:

* the normal download started with `--tui` or `--cli` (Qt is imported only
  after those flags have been handled, so it never loads), and
* `run_linux_lite.sh`, which installs a small pure-Python payload with no Qt,
  no PySide6 and no wheels at all.

## What the lite install contains

`UpdaterCE-Lite.tar.gz` is architecture independent: Python 3.8+ source. One
file serves every machine below.

| Machine | lite | GUI |
| --- | --- | --- |
| x86-64 | yes | yes |
| i686 / 32-bit Intel | yes | no wheel |
| aarch64 (Pi 3/4/5, server) | yes | yes, needs a desktop |
| armv7l (Pi 2, Pi 3 in 32-bit OS) | yes | no wheel |
| armv6l (Pi Zero, Pi 1) | yes | no wheel |

## Install

```sh
curl -fsSLO https://innioasis.app/run_linux_lite.sh
sh run_linux_lite.sh --check          # report python, tools, install state
sh run_linux_lite.sh                  # install to ~/.local/share/innioasis-updater/lite
sh run_linux_lite.sh --tui            # install, then open the terminal interface
```

The download resumes. If the connection drops, run the same command again: the
partial file continues from where it stopped instead of starting over. The
archive's SHA-256 is checked against the release asset before anything is
unpacked.

No root is used. The launcher is written to `~/.local/bin/innioasis-updater-lite`.

## Commands

```sh
innioasis-updater-lite list --model Y1
innioasis-updater-lite download Y1 --output ~/firmware --extract
innioasis-updater-lite flash ~/firmware/rom_Y1 --dry-run
innioasis-updater-lite flash ~/firmware/rom_Y1
innioasis-updater-lite adb devices
innioasis-updater-lite doctor
innioasis-updater-lite tui
```

Every command accepts `--json` for scripts. `doctor` prints the Python version,
architecture and which of `mtkclient`, `adb` and `unrar` were found, with the
install command for the distribution it can infer.

## Flashing

The terminal path does not reimplement a flasher. It calls the tools' own
command lines, which is what makes it usable on machines where Qt cannot run:

* **MTKClient** — `mtk wl <folder>`, the console equivalent of the GUI's MTK
  flow. Install with `pip install --user mtkclient`, or drop `mtk.py` into
  `~/.local/share/innioasis-updater/tools/`.
* **adb** — `adb devices`, `adb sideload <zip>` for Rockbox/stock packages.

SP Flash Tool remains a GUI tool; its Linux build needs X. On a headless
machine use MTKClient, which does the same write over BROM/Preloader.

`flash --dry-run` prints the exact command without touching the player. That is
the safest first run on a new distribution.

## The TUI

`updater-ce --tui` (or `innioasis-updater-lite tui`) opens Select Software,
the same screen as the window: Brand, Model, Type, Software, the release
list, notes, Install, Install from a file, Settings, Support Us, Language
and Credits. A mouse click and the keyboard select the same row. On a fast
link (above 60 Mbps) missing tools are fetched before the menu; otherwise
adb and, on Linux x86_64, SP Flash Tool are fetched at the install step, and
a copy already in the bundle is not downloaded again. Headless machines and
32-bit Linux stay on MTKclient (`mtk wl <folder>`) and do not start the Qt
SP Flash Tool window.

`--simulate` (or `INNIOASIS_SIMULATE=1`) prints the install and Rockbox theme
progress without calling mtk, adb or SP Flash Tool. It is not the default.

When the terminal cannot host curses — Windows without a curses module,
piped output, `TERM=dumb` — the same list is offered as numbered prompts,
and a terminal that reports mouse clicks still selects a row.

## Updating and removing

```sh
sh run_linux_lite.sh --update
sh run_linux_lite.sh --uninstall
```

## Building the payload

```sh
python3 scripts/build_lite_payload.py --out dist/linux-lite \
    --site-dir ../Innioasis-Updater
```

That writes `UpdaterCE-Lite.tar.gz`, its `.sha256`, and copies the installer
into the website checkout so `https://innioasis.app/run_linux_lite.sh` serves
the same script. `build_appimage.sh` runs it as part of a release, so the lite
payload is never a separate hand-made artifact.

## Why this exists

Every failure it removes is one that has already been reported: a missing
`libxcb-cursor` on a fresh install, a 32-bit netbook with no PySide6 wheel, a
headless server where the window cannot open, and a slow SD-card connection
where one interrupted download meant starting over.

## Self-healing and the install flow

The terminal session offers the same **Keep my data** choice as the window. It is passed to the
flashing engine, which uses the firmware package's own preserve-data scatter from a hard-linked
copy so the downloaded package is never modified. If a package does not ship that scatter the
session stops before writing anything rather than erasing internal storage.

MTKclient needs `pycryptodome`. If the interpreter running `mtk.py` cannot import it, the session
installs `pycryptodome`/`pycryptodomex` into the user site (never the system), then retries. If that
is impossible, it falls back to SP Flash Tool's console mode on Linux x86_64 before reporting a
failure. Set `INNIOASIS_NO_AUTO_INSTALL=1` to opt out of the automatic install.

After a successful install the post-install pages named by the catalogue are printed with the
completion line. The Support Us screen lists the same donation methods as the window.

## Publishing the lite archive

The terminal payload is built from the same Python tree as the window:

```sh
python3 scripts/build_lite_payload.py --out dist/linux-lite
```

That writes `UpdaterCE-Lite.tar.gz` and `UpdaterCE-Lite.tar.gz.sha256`. The
archive is architecture independent (Python 3.8+ source, no Qt or wheels), so
one file covers x86-64, i686, aarch64, armv7l and armv6l.

The archive is attached to the **existing 2.5.3 GitHub release** in
`y1-community/updater-ce-neo`, beside the Windows and macOS packages. Nothing
about those packages is replaced, and the release is not retagged or promoted,
so existing 2.5.3 window users are not told an update exists. The payload's
`VERSION` file still records 2.5.4, which is what the terminal session reports.

`run_linux_lite.sh` reads `/releases/tags/2.5.3` first, then falls back to any
release that carries the archive, then to the copy on innioasis.app. Both
`run_linux.sh` and `run_linux_lite.sh` ask whether to install the full app
(recommended) or run once in the terminal; choosing the full app hands over to
`run_linux.sh`, which installs the AppImage/tarball or lays the app out from the
release source when no Linux package matches the architecture.

Do not put the archive in the repository. `dist/` is build output; the release
asset is the distribution point.
