# `slidia_manifest.xml` — the community firmware catalogue

Live copy: `https://innioasis.app/slidia_manifest.xml` (fallbacks: the
`innioasis.app` raw GitHub copy, then the maintainer fork). The app ships a
snapshot at `assets/online/slidia_manifest.xml` for first-run/offline use, and
caches the parsed result for 5 minutes.

It is **data**. A model appears in the app because the manifest lists a release
for it — nothing in the code is per-model beyond display names.

## Structure

```xml
<slidia>
  <package name="..." repo="owner/repo" device="Y1" url="https://github.com/owner/repo"
           type="img" handler="Custom Firmware" ... />
</slidia>
```

## Attributes the app reads

| Attribute | Meaning |
|---|---|
| `name` | Software name shown in the list and on install cards |
| `repo` | `owner/repo` whose GitHub Releases carry the firmware zip |
| `device` | Target model (`Y1`, `Y2`, `A5`, `Q5`, `G1`, ...) |
| `url` | Human-facing project URL (release notes / repo) |
| `type` | `img` (also accepts `firmware` / `rom`). Anything else is skipped — APK entries belong elsewhere |
| `handler` | Must contain `firmware` (conventionally `Custom Firmware`) |
| `brand` | Catalogue brand (`Innioasis`, `Timmkoo`). Empty uses the model registry's brand |
| `portable_zips` | Comma-separated `*-mirror` repos that publish ready-to-run packages |
| `derivative` | Repo this firmware is built from; drives Keep Data compatibility |
| `info_repo` | App repo whose releases describe what the firmware deploys (needs a `post_install` page) |
| `apk_repo` | App repo whose APK is installed after the firmware |
| `post_install`, `post_install2` | Page(s) opened after a successful install |
| `icon`, `icon_dark`, `image`, `image_dark` | Logo URLs; the app prefers the release's `updater.png` / `updater.jpg`, then `icon` (and `icon_dark` in dark mode) |
| `min_version` / `min_app_version` | Hide the entry from older app builds |
| `package_name` | Release asset name; defaults to `rom.zip` for Y1 and `rom_<device>.zip` otherwise (accepts `package` as a legacy alias) |
| `asset_pattern` | Glob for releases whose zips are not named `rom*.zip` (e.g. `my-firmware-*.zip`) |
| `type_a_asset` / `type_b_asset` | Globs naming the Y1 Type A / Type B package when its filename does not say so |
| `slug` | Stable identifier for links and saved installs; generated from name + model when empty |
| `description` | Catalogue description (attribute value or element text) |
| `do_not_keep_data` | `true` (also `1` / `yes`) marks a firmware that must not inherit user data |
| `install_first` | Repo of the base firmware that must be installed before this entry (enables the Install Firmware First gate) |
| `install_first_version` | Version constraint on that base; see the syntax table below |

Aliases still accepted: `model` (`device`), `package` (`package_name`),
`portable_zip` (`portable_zips`), `post_install_2` (`post_install2`),
`min_app_version` (`min_version`) and `image_dark` (`icon_dark`). New entries
should use the names above; `https://innioasis.app/developers.html` generates
entries for every attribute and previews the result against the live file.

### `install_first_version` syntax

`install_first` names the base firmware's repo; `install_first_version`
constrains the version already on the player. A bare version is a **minimum**,
not an exact match. Omit the attribute to accept any version of that base.

| Value | Required base version |
|---|---|
| `3.2.0` | 3.2.0 or newer |
| `=3.2.0` | exactly 3.2.0 |
| `&lt;3.2.0` | older than 3.2.0 (escape `<` in XML) |
| `3.2.0>` | newer than 3.2.0 |
| `3.2.0=>` | 3.2.0 or newer (`=>` includes 3.2.0) |
| `2.3.0-3.0.0` | inclusive range (`2.3.0..3.0.0` also works) |
| `>=3.2.0,&lt;4.0.0` | combined lower and upper bounds |

Operators may lead or trail the version (`>=3.2.0`, `3.2.0>=`, `<=3.2.0`,
`3.2.0<=`, `=<3.2.0`, `==3.2.0`), and `+` or “or later” are accepted minimums.
`>` and `<` are strict; `=>`, `=<`, `>=` and `<=` are inclusive. Every `<`
inside the XML attribute must be written as `&lt;`.

When this constraint is present, the app installs a base release only if one
satisfies it; otherwise it reports that the catalogue has releases but none
matches the required version.

Skipped automatically: `repo="Please-Click/Updates-Available"` (the app's own
update placeholder), non-`img` types, handlers that are not firmware, and
entries whose `min_version` is newer than the running app.

Duplicate attributes (a real typo in the shipped file) are repaired by keeping
the first value, so one mistake cannot hide the whole catalogue.

## How the app resolves a download

1. Entry → `repo` → newest release that contains `package_name`.
2. If `portable_zips` names a mirror and that mirror's `index.json` has a build
   for this OS/arch, download the portable package instead of the plain zip.
   Platform keys: `windows`, `windows-arm64`, `linux`, `linux-arm64`, `macos`.
3. Otherwise take the original asset.

`*_mirror` packages exist because the app can then pre-select the firmware,
stage only the declared images, and offer the portable completion flow.

## Keep Data rules

`derivative` is the whole answer: an install may keep user data when the
software already on the device is the same firmware or a relative of the
incoming one (same `derivative` root). A firmware with no `derivative` is only
compatible with itself. There is an advanced override
(`allow_keep_data_any_firmware`) but it is not the default and should not be
presented as one.

## Changing the catalogue

- Add or edit one `<package>` per model — the same software usually needs a Y1
  and a Y2 entry.
- Keep the XML valid; `xml.etree` rejects duplicate attributes.
- A release must exist before the entry is useful: the app lists the entry, but
  the download 404s otherwise.
- The website reads the same file for its download buttons, so an entry is both
  "in the app" and "on the site".
