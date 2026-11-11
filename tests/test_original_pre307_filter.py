"""Y1 Type A release gates, and the Type B exemption from both.

Original Software releases older than 3.0.7 are hidden for Type A. Packages
built from the live manifest carry the slug ``original-software-y1``, not the
static catalogue's ``original-y1``; the hide must apply to both. Rockbox
releases older than 0.5 are hidden for Type A too, while Type B units -- which
only ever shipped pre-3.0.7 software -- see both gates lifted.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.catalog import (
    FirmwarePackage,
    ReleasesClient,
    original_y1_typea_pre_307_hidden,
    release_version_ok,
)


def _pkg(slug):
    return FirmwarePackage(
        slug=slug, name="Original Software", model="Y1",
        repo="y1-community/y1-stock-rom", package_name="rom.zip",
    )


def test_manifest_slug_is_hidden_below_307():
    assert original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "A", "3.0.2")
    assert original_y1_typea_pre_307_hidden(_pkg("original-y1"), "Y1", "A", "v3.0.6")


def test_307_and_newer_are_not_hidden():
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "A", "3.0.7")
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "A", "3.1.0")


def test_opt_in_lifts_the_hide():
    assert not original_y1_typea_pre_307_hidden(
        _pkg("original-software-y1"), "Y1", "A", "3.0.2", show_pre_307=True
    )


def test_type_b_and_other_packages_are_not_hidden():
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y1"), "Y1", "B", "3.0.2")
    assert not original_y1_typea_pre_307_hidden(_pkg("original-software-y2"), "Y2", "A", "3.0.2")


def test_listing_gate_drops_only_pre307_tags_for_manifest_package():
    for tag, expect_hidden in (("3.0.2", True), ("3.0.7", False)):
        assert original_y1_typea_pre_307_hidden(
            _pkg("original-software-y1"), "Y1", "A", tag
        ) is expect_hidden


def test_type_b_is_exempt_from_the_rockbox_pre_05_gate():
    assert release_version_ok(
        "rockbox-y1/rockbox", "stable-v0.4", model="Y1", selected_type="B"
    )
    assert release_version_ok(
        "rockbox-y1/rockbox", "stable-v0.4.9", model="Y1", selected_type="b"
    )
    assert not release_version_ok(
        "rockbox-y1/rockbox", "stable-v0.4", model="Y1", selected_type="A"
    )
    assert not release_version_ok("rockbox-y1/rockbox", "stable-v0.4", model="Y1")

    # The exemption only lifts the < 0.5 cutoff: a nightly stays hidden. A
    # stable unversioned tag is not a pre-0.5 build, so the cutoff cannot hide
    # it; the listing still drops it when it has no firmware asset.
    assert not release_version_ok(
        "rockbox-y1/rockbox",
        "nightly-35e926bd6ea663ab0aac2f138c7f71ae744971f",
        model="Y1",
        selected_type="B",
    )
    assert release_version_ok(
        "rockbox-y1/rockbox", "stock-menu", model="Y1", selected_type="B"
    )


def test_type_b_rockbox_listing_keeps_pre_05_release(tmp_path):
    client = ReleasesClient(cache_root=tmp_path)
    variants = [
        {
            "asset": {
                "name": "rom_360p.zip",
                "browser_download_url": "https://example.com/rom_360p.zip",
                "size": 1,
            },
            "model": "Y1",
            "type": "A",
            "resolution": "360p",
        },
        {
            "asset": {
                "name": "rom_360p_type_b.zip",
                "browser_download_url": "https://example.com/rom_360p_type_b.zip",
                "size": 2,
            },
            "model": "Y1",
            "type": "B",
            "resolution": "360p",
        },
    ]
    client.cache_releases(
        "rockbox-y1/rockbox",
        [
            {
                "tag_name": "stable-v0.4",
                "name": "Rockbox-Y1 0.4",
                "published_at": "2026-01-01T00:00:00Z",
                "prerelease": False,
                "assets": [],
                "rom_variants": variants,
            }
        ],
    )
    package = FirmwarePackage(
        slug="rockbox-y1",
        name="Rockbox",
        model="Y1",
        repo="rockbox-y1/rockbox",
        package_name="rom.zip",
    )

    type_b_rows = client.releases_for_package(package, "Y1", selected_type="B")
    assert [row["tag_name"] for row in type_b_rows] == ["stable-v0.4"]
    assert type_b_rows[0]["asset_name"] == "rom_360p_type_b.zip"

    # The same release stays hidden for Type A, where the gate still protects
    # units that shipped with Innioasis OS 3.0.7.
    assert client.releases_for_package(package, "Y1", selected_type="A") == []


if __name__ == "__main__":
    test_manifest_slug_is_hidden_below_307()
    test_307_and_newer_are_not_hidden()
    test_opt_in_lifts_the_hide()
    test_type_b_and_other_packages_are_not_hidden()
    test_listing_gate_drops_only_pre307_tags_for_manifest_package()
    test_type_b_is_exempt_from_the_rockbox_pre_05_gate()
    print("Type B release-gate tests passed")
