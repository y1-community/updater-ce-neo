"""Dated release tags sort chronologically, including rebuild suffixes.

Classy publishes ``20261107`` and ``20261107-v2``.  The ``v2`` is a rebuild
suffix, not semantic version 2: it must keep the date as the version, display
as ``November 7 2026 (v2)``, and sort directly after that date's first build.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog  # noqa: E402


def _dated_tags():
    return [
        "20261112",
        "20261111",
        "20261110",
        "20261107-v2",
        "20261107",
        "20261106",
        "20261105",
        "20260928-build.6",
        "20260819-0956",
    ]


def test_a_dated_rebuild_suffix_is_not_a_semantic_version():
    assert catalog.release_version_tuple("20261107-v2") is None
    assert catalog.release_version_tuple("20260928-build.6") is None


def test_dated_rebuild_suffix_is_shown_after_the_date():
    assert (
        catalog.parse_clean_release_tag_label({"tag_name": "20261107-v2"})
        == "November 7 2026 (v2)"
    )
    assert (
        catalog.parse_clean_release_tag_label({"tag_name": "20260928-build.6"})
        == "September 28 2026 (build.6)"
    )
    assert (
        catalog.format_release_display_label({"tag_name": "20261107-v2"})
        == "November 7 2026 (v2)"
    )
    assert (
        catalog.parse_clean_release_tag_label(
            {"tag_name": "20261107-v2"}, version_only=True
        )
        == "November 7 2026 (v2)"
    )
    assert (
        catalog.parse_clean_release_tag_label({"tag_name": "20261107-0956-v2"})
        == "November 7 2026 (v2)"
    )


def test_dated_releases_sort_chronologically_with_rebuilds_after_their_date():
    releases = [
        {
            "tag_name": tag,
            "name": f"Classy {tag}",
            # Deliberately misleading publish metadata: the tag date wins.
            "published_at": "2026-11-12T23:59:59Z" if tag == "20261107-v2" else "2026-01-01T00:00:00Z",
        }
        for tag in _dated_tags()
    ]

    ordered = sorted(releases, key=catalog.release_sort_key, reverse=True)

    assert [release["tag_name"] for release in ordered] == [
        "20261112",
        "20261111",
        "20261110",
        "20261107-v2",
        "20261107",
        "20261106",
        "20261105",
        "20260928-build.6",
        "20260819-0956",
    ]
    labels = [catalog.parse_clean_release_tag_label(release) for release in ordered]
    assert labels[3] == "November 7 2026 (v2)"
    assert labels[4] == "November 7 2026"


def test_a_version_with_a_build_date_keeps_its_semantic_version():
    rel = {"tag_name": "v2.0.4-20260103", "name": "Classy 2.0.4"}
    assert catalog.release_version_tuple("v2.0.4-20260103") == (2, 0, 4)
    assert catalog.parse_clean_release_tag_label(rel) == "2.0.4 (January 3 2026)"
    key = catalog.release_sort_key(rel)
    assert key[2] == (2, 0, 4)
    assert key[-1] == "v2.0.4-20260103"


def test_undated_releases_fall_back_to_publish_metadata():
    older = {"tag_name": "build-a", "name": "build-a", "published_at": "2026-11-01T00:00:00Z"}
    newer = {"tag_name": "build-b", "name": "build-b", "published_at": "2026-11-02T00:00:00Z"}
    assert sorted([older, newer], key=catalog.release_sort_key, reverse=True) == [
        newer,
        older,
    ]


def test_terminal_rows_share_the_dated_order():
    from src.console.releases import rows_from_releases

    releases = [
        {
            "tag_name": tag,
            "name": f"Classy {tag}",
            "prerelease": False,
            "draft": False,
            "published_at": "2026-01-01T00:00:00Z",
            "assets": [
                {
                    "name": "rom_y2.zip",
                    "browser_download_url": "https://example.invalid/rom_y2.zip",
                    "size": 100,
                }
            ],
        }
        for tag in _dated_tags()
    ]

    rows = rows_from_releases(
        releases,
        model="Y2",
        hardware_type="",
        repo="ClassyPlayer/Classy",
    )

    assert [row.tag for row in rows] == [
        "20261112",
        "20261111",
        "20261110",
        "20261107-v2",
        "20261107",
        "20261106",
        "20261105",
        "20260928-build.6",
        "20260819-0956",
    ]
    assert rows[3].label == "November 7 2026 (v2)"
