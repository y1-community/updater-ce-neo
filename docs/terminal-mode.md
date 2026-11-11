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

For Windows on ARM and other WSL2 hosts, USB passthrough with `usbipd-win` is
covered in [Flashing through WSL2](#flashing-through-wsl2-on-windows). The
terminal session itself needs no Qt and no WSLg.

## What the lite install contains

`UpdaterCE-Lite.tar.gz` is one download for every machine below. The Python
is the same source as the window. The archive also carries the vendored
MTKclient tree, adb for the platforms that have a binary, and the Linux
x86-64 SP Flash Tool. A 32-bit board uses MTKclient; it does not run those
x86-64 binaries.

The archive is about 220 MB because it carries the flashing tools themselves;
the Python-only part is still the same source as the window. If you already
have MTKclient, adb and the flash payload, use `--file` with a smaller archive.

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

To install and open the interactive menu in one line, without saving the
installer first:

```sh
INNIOASIS_CHOICE=terminal sh -c "$(curl -fsSL https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/run_linux_lite.sh)"
```

The `sh -c` form is deliberate: it leaves the terminal on stdin, so curses can
run. `curl ... | sh` also installs, but the pipe has no input left for the menu
and the numbered fallback opens instead.

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
release that carries the archive, then to the direct 2.5.3 download URL. Both
`run_linux.sh` and `run_linux_lite.sh` ask whether to install the full app
(recommended) or run once in the terminal; choosing the full app hands over to
`run_linux.sh`, which installs the AppImage/tarball or lays the app out from the
release source when no Linux package matches the architecture.

Do not put the archive in the repository. `dist/` is build output; the release
asset is the distribution point.

## Terminal navigation

The text session mirrors the window's sidebar: `?` opens Menu, `n` opens
Notices (fetched from `https://innioasis.app/notice.xml`), `v` checks the app
release feed, and `g`, `u`, `l`, `c` open Settings, Support Us, Language and
Credits. The update page shows the installed and published versions, the
platform asset, the release notes and the download/release URLs; it never
installs an app update silently. Notices, update results and completion URLs
are read-only text pages, so the session works without a browser.

## Flashing through WSL2 on Windows

The terminal session has no native Windows requirement. On Windows on ARM,
where there is no native SP Flash Tool and MTKclient's Windows USB path is not
always usable, a WSL2 distro can run the same Python flasher against a player
passed through with [usbipd-win](https://github.com/dorssel/usbipd-win).

This is an advanced route. It adds a USB forwarding layer between MTKclient
and the player, so a lost attachment is a lost flash. Test it with a player
you can recover before relying on it.

### What WSLg does and does not do

WSLg runs Linux GUI applications on Windows. It does not pass USB devices to
Linux. The GUI, with or without WSLg, still cannot see the player until
`usbipd` attaches it. The terminal route is the one to use here: it does not
need Qt, X11, Wayland or WSLg.

Windows and macOS keep their normal, supported flashing paths. This section is
only for the WSL fallback.

### Requirements

* Windows 10 21H2 or newer, or Windows 11, with WSL2.
* A WSL2 distro, for example Ubuntu 22.04 or Debian 12. On Windows on ARM use
  the arm64 distro; do not install an x86-64 distro under emulation.
* `usbipd-win` installed on Windows.
* The player's MediaTek USB device visible to Windows before it is attached.
* MTKclient available inside the distro.

Install the terminal payload inside WSL:

```sh
curl -fsSLO https://innioasis.app/run_linux_lite.sh
sh run_linux_lite.sh --tui
```

The lite payload includes the vendored MTKclient tree. Check the host first:

```sh
innioasis-updater-lite doctor
```

`doctor` prints a **Windows Subsystem for Linux** block when it detects WSL,
including whether `usbipd.exe` is on the interop `PATH`. The TUI also prints a
short attach reminder when it starts inside WSL.

### Attach the player

Run these in an elevated **Windows** PowerShell or Command Prompt, not inside
WSL:

```powershell
winget install --exact dorssel.usbipd-win
usbipd list
usbipd bind --busid <BUSID>
```

`bind` is persistent for that USB port and normally only needs to be done
once. Put the player into download/BROM mode, confirm it still appears in
`usbipd list`, then attach it:

```powershell
usbipd attach --wsl --busid <BUSID>
```

In WSL, check that Linux can see a MediaTek device:

```sh
lsusb
```

Then open the terminal interface and install:

```sh
innioasis-updater-lite tui
```

### The reset problem

MTKclient's handshake makes the player reset its USB connection when it moves
from BROM to the preloader/DA stage. A normal desktop flash keeps the same
physical USB connection through that reset. `usbipd` does not: the attachment
normally disappears and has to be re-established.

Keep the Windows terminal ready and re-attach as soon as the player
re-enumerates:

```powershell
usbipd list
usbipd attach --wsl --busid <BUSID>
```

Some `usbipd-win` versions offer `--auto-attach`; if yours does, it can reduce
the gap but does not make a dropped attachment safe. Watch the WSL `dmesg` and
the TUI install log. If the log stops advancing immediately after the player
resets, re-attach first; do not unplug and restart the whole flash.

If the player is already in BROM mode before WSL starts, attach after WSL is
running. If Windows shows the device but WSL does not, detach and attach again
rather than binding a second time.

The terminal session deliberately selects MTKclient inside WSL, even on an
x86-64 distro. The Linux SP Flash Tool payload is x86-64 only, and a forwarded
USB device is already a best-effort path; MTKclient is the engine with a chance
of surviving the BROM reset this way.

### Permissions

A USB device forwarded into WSL is owned by `root` unless a udev rule says
otherwise. Either install a rule for the MediaTek vendor ID (`0e8d`) or run
the terminal session with the permissions your distro requires. Do not add a
broad `0666` rule for every USB device.

The Windows-side `usbipd bind` needs administrator rights. `usbipd attach`
may also prompt unless the service is configured for the current user.

### Troubleshooting

| Symptom | Check |
| --- | --- |
| `usbipd: command not found` in WSL | Windows interop is disabled, or the Windows `PATH` was not inherited. Run `usbipd.exe` from PowerShell instead. |
| `usbipd list` shows the player, `lsusb` does not | The device is bound but not attached to this distro. Run `usbipd attach --wsl --busid <BUSID>`. |
| The attach succeeds, then disappears at the start of the flash | Expected reset. Re-attach while the flash is still running; the TUI keeps the attempt open. |
| MTKclient says no device | Check `lsusb` for `MediaTek Inc.`; confirm the player is in BROM/download mode, then re-attach. |
| `doctor` reports `usbipd.exe not on PATH` | WSL interop is off or Windows `PATH` is restricted. The flash can still work; run the Windows commands in PowerShell. |
| The GUI does not start under WSLg | Use the TUI. WSLg and Qt are not part of the flashing path. |
