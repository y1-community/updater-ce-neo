"""Base ROM tags never reach a release list.

A tag carrying the word ``base`` (``type-a-base``, ``v0.1.0-base``,
``y2-base``) names the firmware tree the next builds are made from. It is not
an install and not an update, so it must stay out of the catalogue rows, the
latest-release lookups, the app update check, and caches written before this
rule existed.

Only the whole word counts: ``database-3.1`` and ``baseband-2.0`` are ordinary
tags.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402


def _asset(name):
    return {"name": name, "browser_download_url": f"https://example.invalid/{name}", "size": 100}


def _raw(tag, names=("rom_360p.zip",), repo="rockbox-y1/rockbox", prerelease=False):
    return {
        "tag_name": tag,
        "name": tag,
        "body": "",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [_asset(n) for n in names],
        "prerelease": prerelease,
    }


def _row(tag, names=("rom_360p.zip",), repo="rockbox-y1/rockbox"):
    release = catalog.ReleasesClient.__new__(catalog.ReleasesClient)._build_releases(
        [_raw(tag, names, repo)], repo
    )
    if release:
        return release[0]
    # The builder drops base tags, but a cache written before that rule holds
    # the same shape, so build that row by hand.
    variant = catalog._parse_rom_asset_variant(_asset(names[0]), tag, repo)
    return {**_raw(tag, names, repo), "rom_variants": [variant], "source_repo": repo}


def _client(releases):
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: releases
    return client


PKG = FirmwarePackage("rockbox-y1", "Rockbox", "Y1", "rockbox-y1/rockbox", "rom.zip")


# --------------------------------------------------------------------------
# The predicate
# --------------------------------------------------------------------------

def test_the_word_base_marks_a_base_rom():
    for tag in (
        "type-a-base",
        "type-b-base",
        "y2-base",
        "v0.1.0-base",
        "BASE",
        "base",
        "base-2026",
        "type_a_base",
    ):
        assert catalog.is_base_release_tag(tag) is True, tag


def test_base_inside_another_word_is_not_a_base_rom():
    for tag in (
        "3.2.1",
        "v3.0.7",
        "database-3.1",
        "baseband-2.0",
        "rockbox-0.6",
        "stable-v0.5",
        "20260819-0956",
        "",
        None,
    ):
        assert catalog.is_base_release_tag(tag) is False, tag


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------

def test_catalogue_listing_hides_base_rows_and_keeps_the_rest():
    releases = [
        _row("stable-v0.6"),
        _row("type-a-base"),
        _row("stable-v0.5"),
    ]
    client = _client(releases)
    rows = client.releases_for_package(PKG, "Y1")
    tags = [row["tag_name"] for row in rows]
    assert tags == ["stable-v0.6", "stable-v0.5"], tags


def test_a_base_release_carrying_a_rom_asset_is_still_hidden():
    """The base tree can carry ``rom*.zip`` files; its tag is what decides."""
    release = catalog.ReleasesClient.__new__(catalog.ReleasesClient)._build_releases(
        [_raw("type-a-base"), _raw("stable-v0.6")], "rockbox-y1/rockbox"
    )
    tags = [row["tag_name"] for row in release]
    assert tags == ["stable-v0.6"], tags


def test_the_single_release_lookup_refuses_a_base_tag():
    client = ReleasesClient.__new__(ReleasesClient)
    assert client._normalize_release(_raw("type-a-base"), "rockbox-y1/rockbox") is None
    assert client._normalize_release(_raw("stable-v0.6"), "rockbox-y1/rockbox") is not None


# --------------------------------------------------------------------------
# Caches written before the rule existed
# --------------------------------------------------------------------------

def test_a_cached_listing_drops_base_rows_already_on_disk(tmp_path):
    client = ReleasesClient.__new__(ReleasesClient)
    client.cache_dir = tmp_path
    cached = [_row("stable-v0.6"), _row("type-a-base")]
    (tmp_path / "rockbox-y1_rockbox.json").write_text(
        json.dumps(cached), encoding="utf-8"
    )

    rows = client.get_cached_releases("rockbox-y1/rockbox")
    assert rows is not None
    assert [row["tag_name"] for row in rows] == ["stable-v0.6"]


def test_a_cached_latest_base_is_not_used_for_updates(tmp_path):
    client = ReleasesClient.__new__(ReleasesClient)
    client.cache_dir = tmp_path
    updates = tmp_path.parent / "updates"
    updates.mkdir(exist_ok=True)
    (updates / "y1-community_updater-ce-neo.json").write_text(
        json.dumps({"tag_name": "v3.1-base", "name": "", "body": ""}), encoding="utf-8"
    )

    assert client.get_cached_update("y1-community/updater-ce-neo") is None


# --------------------------------------------------------------------------
# App update checks
# --------------------------------------------------------------------------

def test_app_update_checks_skip_base_tags(monkeypatch, tmp_path):
    client = ReleasesClient.__new__(ReleasesClient)
    client.cache_dir = tmp_path
    payload = [_raw("v3.1-base"), _raw("v3.1.0"), _raw("v3.0.9")]
    monkeypatch.setattr(client, "_get_json", lambda url: payload)

    releases = client.get_all_update_releases("y1-community/updater-ce-neo")
    assert [rel["tag_name"] for rel in releases] == ["v3.1.0", "v3.0.9"]

    latest = client.get_latest_release_info("y1-community/updater-ce-neo", force=True)
    assert latest["tag_name"] == "v3.1.0"
