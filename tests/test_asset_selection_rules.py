"""Asset-selection rules for repositories that serve more than one model.

Inniclassic's Y2 repository republishes the legacy Y1 zips (rom.zip,
rom_type_b.zip, rom_y1_*.zip) beside its own rom_y2.zip. The classifier reads
the repository name as Y2, and that used to hand the unqualified rom.zip to Y2
users. A device selection must always end on the model's own archive.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog, manifest  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402
from src.config import detect_model_and_type_from_name  # noqa: E402

REPO_Y2 = "JonaszPotoniec/inniclassic-y2"
TAG = "v2.0.0"
Y2_ASSETS = ("rom.zip", "rom_type_b.zip", "rom_y1_1.0.zip", "rom_y2.zip")


def _asset(name):
    return {
        "name": name,
        "browser_download_url": f"https://example.invalid/{name}",
        "size": 100,
    }


def _release(*names):
    return {
        "tag_name": TAG,
        "name": "Inniclassic 2.0",
        "body": "",
        "published_at": "2026-11-04T00:00:00Z",
        "prerelease": False,
        "assets": [_asset(name) for name in names],
    }


def _package(model="Y2", repo=REPO_Y2, asset_pattern=""):
    return FirmwarePackage(
        f"inniclassic-{model.lower()}",
        "Inniclassic",
        model,
        repo,
        "rom_y2.zip" if model == "Y2" else "rom.zip",
        asset_pattern=asset_pattern,
    )


def _rows(*names, package=None):
    client = ReleasesClient.__new__(ReleasesClient)
    raw = [_release(*names)]
    client.get_all_releases = lambda repo, force_refresh=False: client._build_releases(
        raw, repo
    )
    return client.releases_for_package(package or _package(), "Y2")


def test_a_model_suffixed_zip_beats_an_unqualified_zip_from_the_same_repo():
    variants = catalog._release_rom_variants(
        [_asset(name) for name in Y2_ASSETS], TAG, REPO_Y2
    )
    preferred = catalog.select_preferred_rom_asset(variants, model="Y2")
    assert preferred["asset"]["name"] == "rom_y2.zip"


def test_the_manifest_asset_pattern_pins_the_y2_zip():
    variants = catalog._release_rom_variants(
        [_asset(name) for name in Y2_ASSETS],
        TAG,
        REPO_Y2,
        package=_package(asset_pattern="rom_y2*.zip"),
    )
    assert [v["asset"]["name"] for v in variants] == ["rom_y2.zip"]

    rows = _rows(*Y2_ASSETS, package=_package(asset_pattern="rom_y2*.zip"))
    assert [row["asset_name"] for row in rows] == ["rom_y2.zip"]
    assert rows[0]["download_url"].endswith("/rom_y2.zip")


def test_y2_rows_pick_rom_y2_without_the_manifest_shim_too():
    rows = _rows(*Y2_ASSETS, package=_package())
    assert [row["asset_name"] for row in rows] == ["rom_y2.zip"]
    assert rows[0]["download_url"].endswith("/rom_y2.zip")


def test_a_cached_row_is_re_evaluated_with_the_manifest_shim():
    """A release cache written before the shim must not keep the wrong URL."""
    assets = [_asset(name) for name in Y2_ASSETS]
    cached = [
        {
            "tag_name": TAG,
            "name": "Inniclassic 2.0",
            "body": "",
            "published_at": "2026-11-04T00:00:00Z",
            "prerelease": False,
            "assets": assets,
            # Old cache shape: no model_explicit field, and the unqualified
            # rom.zip was chosen when the row was built.
            "rom_variants": [
                catalog._parse_rom_asset_variant(a, TAG, REPO_Y2) for a in assets
            ],
            "download_url": "https://example.invalid/rom.zip",
            "asset_name": "rom.zip",
            "asset_size": 100,
            "source_repo": REPO_Y2,
        }
    ]
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: cached
    rows = client.releases_for_package(
        _package(asset_pattern="rom_y2*.zip"), "Y2"
    )
    assert [row["asset_name"] for row in rows] == ["rom_y2.zip"]


def test_multiple_non_rom_zips_are_not_guessed():
    variants = catalog._release_rom_variants(
        [_asset("InniclassicY1.zip"), _asset("InniclassicY2.zip")], TAG, REPO_Y2
    )
    assert variants == []


def test_a_lone_non_rom_zip_is_still_a_firmware_candidatein_a_repo_without_rom_zips():
    variants = catalog._release_rom_variants(
        [_asset("Y2Linux-1.0-first-install.zip")], TAG, "schulzcode/y2linux"
    )
    assert [v["asset"]["name"] for v in variants] == [
        "Y2Linux-1.0-first-install.zip"
    ]


# ---------------------------------------------------------------------------
# A release that names its model / revision in each zip
# ---------------------------------------------------------------------------

CLASSY = "ClassyPlayer/Classy"
CLASSY_ASSETS = (
    "classy-y1-exp.2-type-a.zip",
    "classy-y1-exp.2-type-b.zip",
)


def test_each_zip_naming_its_revision_is_readable_in_one_release():
    """No rom*.zip at all: the names still say Y1 Type A and Y1 Type B."""
    variants = catalog._release_rom_variants(
        [_asset(name) for name in CLASSY_ASSETS], "exp.2-y1", CLASSY
    )
    assert [
        (v["asset"]["name"], v["model"], v["type"], v["model_explicit"])
        for v in variants
    ] == [
        ("classy-y1-exp.2-type-a.zip", "Y1", "A", True),
        ("classy-y1-exp.2-type-b.zip", "Y1", "B", True),
    ]


def test_the_named_zip_rule_lands_on_the_chosen_revision():
    """A Y1 Type B pick downloads the Type B archive from such a release."""
    client = ReleasesClient.__new__(ReleasesClient)
    release = _raw_release("exp.2-y1", CLASSY_ASSETS, "Y1 experimental 2")
    client.get_all_releases = lambda repo, force_refresh=False: client._build_releases(
        [release], repo
    )
    package = FirmwarePackage("classy-y1", "Classy", "Y1", CLASSY, "")
    rows = client.releases_for_package(package, "Y1", selected_type="B")
    assert [row["asset_name"] for row in rows] == ["classy-y1-exp.2-type-b.zip"]


def test_one_unnamed_zip_leaves_the_release_unreadable():
    """Guessing is still refused when an archive does not say what it is."""
    variants = catalog._release_rom_variants(
        [_asset("classy-y1-exp.2-type-b.zip"), _asset("aa11bb22cc33.zip")],
        TAG,
        CLASSY,
    )
    assert variants == []


def test_a_type_revision_spelled_with_a_space_is_still_read():
    assert detect_model_and_type_from_name("Classy Y1 Type B.zip") == ("Y1", "B")
    assert detect_model_and_type_from_name("Classy-Y1-TypeB.zip") == ("Y1", "B")
    # A word that merely contains "typeb" is not a revision marker.
    assert detect_model_and_type_from_name("prototypebase.zip") == ("", None)


# ---------------------------------------------------------------------------
# Only a package that declares a non-rom archive may list one
# ---------------------------------------------------------------------------

ROCKBOX = "rockbox-y1/rockbox"


def _rockbox_package():
    return FirmwarePackage("rockbox-y1", "Rockbox", "Y1", ROCKBOX, "rom.zip")


def _y2linux_package():
    return FirmwarePackage(
        "y2reborn-y2", "Reborn (Y2Linux)", "Y2", "schulzcode/y2linux",
        "Y2Linux-first-install.zip",
    )


def _raw_release(tag, names, title=""):
    return {
        "tag_name": tag,
        "name": title or tag,
        "body": "",
        "published_at": "2026-11-01T00:00:00Z",
        "prerelease": False,
        "assets": [_asset(name) for name in names],
    }


def test_a_voice_release_whose_zip_has_no_name_hint_is_not_a_row():
    """A voice pack's zip is hashed, so only the rom*.zip convention catches it."""
    client = ReleasesClient.__new__(ReleasesClient)
    rows = client._build_releases(
        [
            _raw_release(
                "stable-v0.6.2", ["453d02bb2a27590aa11.zip"], "Voice Files (latest)"
            ),
            _raw_release("stable-v0.6.1", ["rom.zip", "voices.zip"]),
        ],
        ROCKBOX,
    )
    assert [row["tag_name"] for row in rows] == ["stable-v0.6.1"]


def test_a_rom_zip_package_ignores_a_differently_named_zip_even_when_alone():
    variants = catalog._release_rom_variants(
        [_asset("453d02bb2a27590aa11.zip")],
        TAG,
        ROCKBOX,
        repo_has_rom=True,
        package=_rockbox_package(),
    )
    assert variants == []


def test_a_cached_voice_row_does_not_come_back_through_the_package():
    voice = _asset("453d02bb2a27590aa11.zip")
    rom = _asset("rom.zip")
    cached = [
        {
            "tag_name": "stable-v0.6.2",
            "name": "Voice Files (latest)",
            "body": "",
            "published_at": "2026-11-02T00:00:00Z",
            "prerelease": False,
            "assets": [voice],
            # A cache row built before the rom*.zip rule applied.
            "rom_variants": [
                catalog._parse_rom_asset_variant(voice, "stable-v0.6.2", ROCKBOX)
            ],
            "download_url": voice["browser_download_url"],
            "asset_name": voice["name"],
            "asset_size": 100,
            "source_repo": ROCKBOX,
        },
        {
            "tag_name": "stable-v0.6.1",
            "name": "Rockbox-Y1 v0.6.1",
            "body": "",
            "published_at": "2026-11-01T00:00:00Z",
            "prerelease": False,
            "assets": [rom],
            "rom_variants": [
                catalog._parse_rom_asset_variant(rom, "stable-v0.6.1", ROCKBOX)
            ],
            "download_url": rom["browser_download_url"],
            "asset_name": rom["name"],
            "asset_size": 100,
            "source_repo": ROCKBOX,
        },
    ]
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: cached
    rows = client.releases_for_package(_rockbox_package(), "Y1")
    assert [row["tag_name"] for row in rows] == ["stable-v0.6.1"]


def test_a_package_named_after_its_own_archive_may_use_it():
    """``package_name`` is the opt-in: y2linux's archive is not a rom*.zip."""
    variants = catalog._release_rom_variants(
        [_asset("Y2Linux-1.0-first-install.zip")],
        TAG,
        "schulzcode/y2linux",
        repo_has_rom=True,
        package=_y2linux_package(),
    )
    assert [v["asset"]["name"] for v in variants] == [
        "Y2Linux-1.0-first-install.zip"
    ]


def test_the_bundled_manifest_names_the_y2linux_archive():
    text = (ROOT / "assets" / "online" / "slidia_manifest.xml").read_text(
        encoding="utf-8"
    )
    entries = manifest.parse_manifest_xml(text)
    y2linux = [entry for entry in entries if entry.repo == "schulzcode/y2linux"]
    assert len(y2linux) == 1
    assert y2linux[0].package_name == "Y2Linux-*-first-install.zip"
    # The declaration is the whole opt-in: a rom*.zip name would keep the
    # firmware on the strict rule instead.
    assert not catalog._is_rom_zip_name(y2linux[0].package_name)


# ---------------------------------------------------------------------------
# Per-model and per-type archive names, and multi-device entries
# ---------------------------------------------------------------------------


def test_one_entry_can_list_several_devices():
    xml = (
        '<slidia><package name="Multi" repo="acme/multi" device="Y1,Y2,G5" '
        'type="img" handler="Custom Firmware" /></slidia>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert {entry.model for entry in entries} == {"Y1", "Y2", "G5"}
    # The old one-entry-per-model form is untouched.
    single = manifest.parse_manifest_xml(
        '<slidia><package name="Solo" repo="acme/solo" device="Y1" '
        'type="img" handler="Custom Firmware" /></slidia>'
    )
    assert [entry.model for entry in single] == ["Y1"]


def test_a_package_can_name_its_archive_per_model_and_type():
    xml = (
        '<slidia><package name="Multi" repo="acme/multi" device="Y1,Y2" '
        'package_name_y2="rom_y2.zip" package_name_y1_type_b="rom_type_b.zip" '
        'type="img" handler="Custom Firmware" /></slidia>'
    )
    entries = manifest.parse_manifest_xml(xml)
    y1 = next(entry for entry in entries if entry.model == "Y1")
    y2 = next(entry for entry in entries if entry.model == "Y2")
    assert catalog.package_name_for(y1, "Y1", "B") == "rom_type_b.zip"
    # Type A keeps the standard name; only the declared revision is renamed.
    assert catalog.package_name_for(y1, "Y1", "A") == "rom.zip"
    assert catalog.package_name_for(y2, "Y2") == "rom_y2.zip"


def test_a_brand_qualified_package_name_stays_with_its_brand():
    xml = (
        '<slidia><package name="A5" repo="acme/a5" device="A5" brand="Timmkoo" '
        'package_name_timmkoo_a5="rom_timmkoo.zip" '
        'package_name_otherbrand_a5="rom_other.zip" '
        'type="img" handler="Custom Firmware" /></slidia>'
    )
    [entry] = manifest.parse_manifest_xml(xml)
    assert catalog.package_name_for(entry, "A5") == "rom_timmkoo.zip"


def test_a_type_specific_package_name_pins_the_archive():
    """A non-standard archive named only for Type B is what Type B offers."""
    package = FirmwarePackage(
        "acme-y1", "Acme", "Y1", "acme/acme", "rom.zip",
        package_names={"y1_type_b": "Acme-*-type-b.zip"},
    )
    variants = catalog._release_rom_variants(
        [_asset("Acme-1.0-type-b.zip"), _asset("Acme-1.0-type-a.zip")],
        TAG,
        "acme/acme",
        package=package,
        model="Y1",
        selected_type="B",
    )
    assert [v["asset"]["name"] for v in variants] == ["Acme-1.0-type-b.zip"]


def test_the_terminal_view_reads_multi_device_entries_and_model_names():
    from src.console import catalog as cli_catalog

    parsed = cli_catalog.parse_manifest(
        b'<slidia><package name="Multi" repo="acme/multi" device="Y1,Y2" '
        b'package_name_y2="rom_y2.zip" type="img" handler="Custom Firmware" /></slidia>'
    )
    assert {(item.model, item.package_name) for item in parsed} == {
        ("Y1", "rom.zip"),
        ("Y2", "rom_y2.zip"),
    }


def test_the_terminal_view_matches_a_wildcard_package_name():
    from src.console import catalog as cli_catalog

    release = {
        "assets": [
            _asset("Y2Linux-1.0-first-install.zip"),
            _asset("Y2Linux-notes.zip"),
        ]
    }
    picked = cli_catalog.pick_asset(release, "Y2Linux-*-first-install.zip")
    assert picked["name"] == "Y2Linux-1.0-first-install.zip"


def test_the_manifest_cache_keeps_per_model_names(tmp_path, monkeypatch):
    entries = manifest.parse_manifest_xml(
        '<slidia><package name="Multi" repo="acme/multi" device="Y1,Y2" '
        'package_name_y2="rom_y2.zip" type="img" handler="Custom Firmware" /></slidia>'
    )
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(entries)
    restored = manifest.load_cached_manifest()
    assert {
        entry.model: catalog.package_name_for(entry, entry.model)
        for entry in restored
    } == {"Y1": "rom.zip", "Y2": "rom_y2.zip"}


def test_a_wildcard_package_name_pins_the_archive():
    package = FirmwarePackage(
        "y2reborn-y2", "Reborn (Y2Linux)", "Y2", "schulzcode/y2linux",
        "Y2Linux-*-first-install.zip",
    )
    variants = catalog._release_rom_variants(
        [
            _asset("Y2Linux-1.0.0-beta.9.3.24-first-install.zip"),
            _asset("Y2Linux-notes-9.3.24.zip"),
        ],
        TAG,
        "schulzcode/y2linux",
        repo_has_rom=True,
        package=package,
    )
    assert [v["asset"]["name"] for v in variants] == [
        "Y2Linux-1.0.0-beta.9.3.24-first-install.zip"
    ]


def test_a_repo_the_manifest_lists_for_one_model_keeps_its_own_naming(monkeypatch):
    package = FirmwarePackage(
        "y2reborn-y2", "Reborn (Y2Linux)", "Y2", "schulzcode/y2linux", "rom_y2.zip"
    )
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: [package])
    assert catalog._release_rom_variants(
        [_asset("Y2Linux-1.0-first-install.zip")],
        TAG,
        "schulzcode/y2linux",
        package=package,
    )



def test_a_repo_listed_for_two_models_gets_no_guess(monkeypatch):
    solar_y2 = FirmwarePackage(
        "solar-y2", "Solar", "Y2", "thesolarproject/solar", "rom_y2.zip"
    )
    solar_y1 = FirmwarePackage(
        "solar-y1", "Solar", "Y1", "thesolarproject/solar", "rom.zip"
    )
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: [solar_y2, solar_y1])
    assert catalog._release_rom_variants(
        [_asset("Solar-install.zip")],
        TAG,
        "thesolarproject/solar",
        package=solar_y2,
    ) == []


# ---------------------------------------------------------------------------
# The model comes from the archive, not from a repository name inside it
# ---------------------------------------------------------------------------


def test_a_repository_name_inside_a_zip_name_does_not_pick_the_model():
    variant = catalog._parse_rom_asset_variant(
        _asset("inniclassic-y1-community-2.0.zip"), "v2.0.0", REPO_Y2
    )
    assert variant["model"] == "Y2"


def test_a_bare_b_is_a_hardware_revision_not_a_model():
    variant = catalog._parse_rom_asset_variant(
        _asset("firmware-b.zip"), TAG, "someone/player-firmware"
    )
    assert variant["model"] == "dual"


def test_a_firmware_named_for_its_own_player_keeps_that_model():
    variant = catalog._parse_rom_asset_variant(
        _asset("rom_g5.zip"), TAG, "y1-community/g5-stock-rom"
    )
    assert variant["model"] == "G5"
    kept = catalog.filter_rom_variants_for_model(
        [variant], "G5", "G5", "y1-community/g5-stock-rom"
    )
    assert kept, "a G5 archive must survive the model filter for a G5 row"


def test_manifest_xml_and_feed_dicts_carry_the_asset_pattern(tmp_path, monkeypatch):
    xml = (
        '<slidia><package name="inniclassic" '
        f'repo="{REPO_Y2}" device="Y2" asset_pattern="rom_y2*.zip" '
        'type="img" handler="Custom Firmware" /></slidia>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert entries and entries[0].asset_pattern == "rom_y2*.zip"

    fed = manifest.parse_manifest_packages(
        [
            {
                "name": "inniclassic",
                "repo": REPO_Y2,
                "device": "Y2",
                "asset_pattern": "rom_y2*.zip",
            }
        ]
    )
    assert fed and fed[0].asset_pattern == "rom_y2*.zip"

    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(entries)
    restored = manifest.load_cached_manifest()
    assert restored and restored[0].asset_pattern == "rom_y2*.zip"


def test_the_bundled_manifest_pins_the_y2_inniclassic_entry():
    text = (ROOT / "assets" / "online" / "slidia_manifest.xml").read_text(
        encoding="utf-8"
    )
    entries = manifest.parse_manifest_xml(text)
    y2 = [
        entry
        for entry in entries
        if entry.name.lower() == "inniclassic" and entry.model.upper() == "Y2"
    ]
    assert len(y2) == 1
    assert y2[0].repo == REPO_Y2
    assert y2[0].asset_pattern == "rom_y2*.zip"
