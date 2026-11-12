# Packaging and publishing a community firmware

## Folder layout the app detects

| Host | Layout |
|---|---|
| Windows / Linux portable | ROM files + `fw_package.ini` inside a copy of `SP_Flash_Tool` (or folder beside the binary) |
| macOS portable | `Firmware/` (or `SP_Flash_Tool/`) folder beside `Updater CE.app` |
| Windows / Linux install-first portable | Target in `SP_Flash_Tool`; base in a sibling `pre_stage/` |
| macOS install-first portable | Target in `Firmware/` (or `firmware/` in a DMG pack); base in its `pre_stage/` |
| Legacy Windows / 32-bit Linux | Two numbered flash-tool folders, base first, each with its own scatter, images, DA and filename-only `history.ini` |
| Browsed/dropped | any folder or `.zip`/`.rar` with exactly one scatter |

Only the scatter, the images it lists, `fw_package.ini` and the icon are
required. When a zip is extracted from a portable package, the app prunes
everything else (SP Flash Tool binaries, readmes, archives, logs) before
flashing, and stages what remains into an isolated cache directory so the app's
own files can never be overwritten.

## `fw_package.ini`

```ini
[fw_package]
enabled = true
software_name = Rockbox
version = 3.0.7
target_model = Y1
target_type = Type A
description = Community Rockbox build with 360p theme support.
post_install = https://example.org/first-start
post_install2 = https://example.org/themes
derivative = y1-community/y1-stock-rom
repo = owner/rockbox
install_first = y1-community/y1-stock-rom
install_first_version = 3.2.0=>
```

Keys are case-insensitive; `name`, `model`, `type`, `post_install_2` and
`compatible` are accepted aliases. `enabled = false` makes the app ignore the
package. Without an ini the app still installs — it infers the model from the
scatter and leaves the software name generic.

## Install-first portable packages

When the manifest row has `install_first`, copy it and
`install_first_version` into the target ini together with the row's `repo`. The
portable pack carries the base firmware in a neighbouring `pre_stage` folder:

```
SP_Flash_Tool/        # target scatter, listed images, fw_package.ini
pre_stage/            # base scatter, listed images, fw_package.ini
```

The base ini names its own `repo`, `version` and `target_model`; that is what
lets the app match the pair even when the player owner browses the base folder
or its scatter directly. The model must equal the target's model and come from
the base repository's `slidia_manifest.xml` row, never from the target's
defaults. The app refuses a mismatched or unlabelled base, then falls back to
the ordinary install-first download from the catalogue.

For legacy Windows XP–8.1 / 32-bit Windows and 32-bit Linux, ship two numbered
folders instead, each with its own copy of the flash tool, scatter, listed
images, DA and filename-only `history.ini`. Number the base and target 2 and 3
when a driver folder is present, otherwise 1 and 2, and put the order in the
README. See `docs/portable_install_first.md` in `updater-ce-neo` for the full
tree and packager checklist.

## Release naming and checksums

- Tag every release by version; the title and notes are shown to users.
- Firmware asset: `rom.zip` (Y1) / `rom_<device>.zip`, matching
  `package_name` in the manifest when you override it.
- Ship a `.sha256` beside anything the app downloads directly.
- Do not replace an existing release's assets; publish a new tag.

## Mirroring (`portable_zips`)

A mirror repo (`y1-community/<name>-mirror`) copies the upstream releases and
adds one ready-to-run package per platform:

```
rom.zip -> rom-windows.zip, rom-windows-arm64.zip, rom-linux.zip,
           rom-linux-arm64.zip, rom-macos.zip
```

It commits an `index.json` (schema 1) describing every mirrored tag with, per
asset: `source_asset`, `platform`, size, checksum and download URL. The app
looks up `index.json` on `main` and falls back to the plain asset when anything
is unresolved (no mirror, tag not mirrored yet, no build for this platform).

Mirroring is what gives users the pre-selected, one-click portable experience —
and what text links to a release's zip resolve to for their machine. These
packages are never listed among the release's own ROM files.

## Publishing checklist

- [ ] Scatter is the release's own (not a preserve-data variant), platform and
      storage lines present.
- [ ] Every `is_download` image exists, names match `file_name` exactly.
- [ ] `fw_package.ini` present; model and variant correct.
- [ ] Install-first row: target ini carries `repo`, `install_first` and
      `install_first_version`; base is in `pre_stage` with its own ini, or the
      target omits the base so Updater CE can download it.
- [ ] `post_install` page exists for anything that needs first-boot setup.
- [ ] `flash.py --package ... --check --json` reports no problems.
- [ ] Manifest entry added for each target model, with `derivative` where the
      firmware shares the stock data partition.
- [ ] Mirror (optional): `index.json` covers the new tag on every platform you
      built.
- [ ] Install verified on the real hardware for the model you claim — the
      project's contribution rules require hands-on SP Flash Tool and MTKClient
      verification across multiple firmwares before a pull release.

## Archiving ethics

The archive exists so a player owner can always restore what they own.

- Never delete or re-tag upstream assets; mirrors follow, they do not replace.
- Keep release notes with the archived builds — they are the only record of what
  changed, and the app displays them.
- Record provenance: base firmware, `derivative`, and any vendor blobs you did
  not author.
- If a maintainer withdraws a release, mark it in the manifest rather than
  silently republishing a modified copy.
