"""Release titles carry dates, and pre-releases follow one classification.

``rockbox-y1/rockbox`` tags each nightly with only a commit hash and names the
build day in the title (``Rockbox-Y1 Nightly-10-11-2026``), so the date in the
title has to be read the way a semantic version in a title already is. Those
nightlies, and any alpha/dev build, are pre-releases: hidden while the show-dev
filter is off, marked with ``(Pre-release)`` beside their date or version, and
still listed when the product publishes nothing but pre-releases. A beta build
is not a pre-release. Voice files and Rockbox's ``update.zip`` are not firmware.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog, versioning  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402

NIGHTLY_TAG = "nightly-a8c63c04f0030dca6bc193a437767e1838067b67"


def _asset(name):
    return {
        "name": name,
        "browser_download_url": f"https://example.invalid/{name}",
        "size": 10,
    }


def _release(tag, name, *assets, prerelease=False):
    return {
        "tag_name": tag,
        "name": name,
        "published_at": "2026-11-10T00:00:00Z",
        "prerelease": prerelease,
        "assets": [_asset(asset) for asset in assets],
    }


def _package(model="Y1", repo="rockbox-y1/rockbox", name="Rockbox"):
    return FirmwarePackage(
        slug=f"{name.lower()}-{model.lower()}",
        name=name,
        model=model,
        repo=repo,
        package_name="rom_y2.zip" if model == "Y2" else "rom.zip",
    )


def _rows(*releases, repo="rockbox-y1/rockbox"):
    return ReleasesClient.__new__(ReleasesClient)._build_releases(list(releases), repo)


# ---------------------------------------------------------------------------
# A date in the title is the release's version, like a semantic version
# ---------------------------------------------------------------------------

def test_a_nightly_title_date_is_read_as_its_version():
    assert versioning.release_title_datestamp_label("Rockbox-Y1 Nightly-10-11-2026") == (
        "November 10 2026"
    )
    assert versioning.release_title_datestamp_label("Rockbox-Y1 Nightly-05-10-2026") == (
        "October 5 2026"
    )
    assert versioning.release_title_datestamp_label("Nightly 2026-11-10") == (
        "November 10 2026"
    )


def test_a_dotted_version_or_model_name_is_never_read_as_a_date():
    for title in (
        "Rockbox-Y1 v0.6.1",
        "Y2Linux 1.0 Beta 9.2.13",
        "Rockbox-Y1 Nightly",
        "",
        None,
    ):
        assert versioning.release_title_datestamp_label(title) is None, title


def test_a_nightly_lists_its_date_instead_of_its_hash_tag():
    rel = {"tag_name": NIGHTLY_TAG, "name": "Rockbox-Y1 Nightly-10-11-2026"}
    assert versioning.parse_clean_release_tag_label(rel) == (
        "November 10 2026 (Pre-release)"
    )
    assert catalog.format_release_display_label(rel) == "November 10 2026 (Pre-release)"
    assert versioning.effective_tag(rel) == "November 10 2026"
    # The date is not a semantic version: it must not drive the 0.5 gate.
    assert versioning.release_version_tuple(NIGHTLY_TAG, rel["name"]) is None


def test_a_mixed_list_shows_dates_and_versions_side_by_side():
    rows = _rows(
        _release(NIGHTLY_TAG, "Rockbox-Y1 Nightly-10-11-2026", "rom.zip"),
        _release("stable-v0.6.1", "Rockbox-Y1 v0.6.1", "rom.zip"),
    )
    labels = {
        row["tag_name"]: catalog.parse_clean_release_tag_label(row) for row in rows
    }
    assert labels[NIGHTLY_TAG] == "November 10 2026 (Pre-release)"
    assert labels["stable-v0.6.1"] == "0.6.1"


# ---------------------------------------------------------------------------
# One pre-release classification
# ---------------------------------------------------------------------------

def test_nightly_alpha_and_dev_are_pre_releases_and_beta_is_not():
    assert versioning.is_pre_release({"tag_name": NIGHTLY_TAG}) is True
    assert versioning.is_pre_release({"tag_name": "v1.0.0-alpha1"}) is True
    assert versioning.is_pre_release({"tag_name": "v1.0.0-dev"}) is True
    assert versioning.is_pre_release({"tag_name": "v1.0.0-beta"}) is False
    assert versioning.is_pre_release(
        {"tag_name": "v1.0.0-beta", "prerelease": True}
    ) is False
    assert versioning.is_pre_release({"tag_name": "stable-v0.6"}) is False
    # GitHub's flag still classifies a token-less release.
    assert versioning.is_pre_release(
        {"tag_name": "build-12", "prerelease": True}
    ) is True


def test_dev_releases_are_listed_only_when_the_show_dev_filter_is_on():
    rows = _rows(
        _release("v1.0.0", "Stable build", "rom.zip"),
        _release("v1.1.0-alpha1", "Alpha build", "rom.zip"),
        _release("v1.1.0-dev", "Dev build", "rom.zip"),
    )
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: rows

    package = _package()
    hidden = [row["tag_name"] for row in client.releases_for_package(package, "Y1")]
    shown = [
        row["tag_name"]
        for row in client.releases_for_package(package, "Y1", show_nightly=True)
    ]

    assert hidden == ["v1.0.0"]
    assert set(shown) == {"v1.0.0", "v1.1.0-alpha1", "v1.1.0-dev"}


def test_a_beta_is_listed_without_a_pre_release_marker():
    rel = {"tag_name": "v1.0.0-beta", "name": "v1.0.0 Beta", "prerelease": True}
    label = versioning.parse_clean_release_tag_label(rel)
    assert "Pre-release" not in label
    assert "Beta" in label


def test_a_product_with_only_pre_releases_stays_listed_with_the_filter_off():
    rows = _rows(
        _release("nightly-a1", "Nightly 2026-11-10", "rom_y2.zip"),
        _release("nightly-a2", "Nightly 2026-11-09", "rom_y2.zip"),
        repo="newco/nightly",
    )
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: rows

    listed = client.releases_for_package(_package("Y2", "newco/nightly", "Nightly"), "Y2")

    assert {row["tag_name"] for row in listed} == {"nightly-a1", "nightly-a2"}


# ---------------------------------------------------------------------------
# Assets that are not firmware
# ---------------------------------------------------------------------------

def test_a_voice_files_release_is_not_a_firmware_row():
    rows = _rows(
        _release("voices-22511894870", "Voice Files (latest) - 453d02bb", "voices.zip"),
        _release("stable-v0.6.1", "Rockbox-Y1 v0.6.1", "rom.zip", "update.zip"),
    )
    assert [row["tag_name"] for row in rows] == ["stable-v0.6.1"]


def test_update_zip_is_never_picked_as_the_firmware():
    assert catalog._release_rom_variants(
        [_asset("update.zip")], "stable-v0.6", "rockbox-y1/rockbox"
    ) == []
    names = [
        variant["asset"]["name"]
        for variant in catalog._release_rom_variants(
            [_asset("rom.zip"), _asset("update.zip"), _asset("voices.zip")],
            "stable-v0.6",
            "rockbox-y1/rockbox",
        )
    ]
    assert names == ["rom.zip"]


# ---------------------------------------------------------------------------
# Feature aliases in a tag
# ---------------------------------------------------------------------------

def test_feature_aliases_read_as_short_markers():
    assert (
        versioning.parse_clean_release_tag_label({"tag_name": "3.2.0-fm"})
        == "3.2.0 (FM)"
    )
    assert (
        versioning.parse_clean_release_tag_label({"tag_name": "2.1.9-adb"})
        == "2.1.9 (ADB)"
    )
