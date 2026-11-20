"""Tests for detecting semantic versions at the start or end of release notes titles."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import catalog


def test_detect_title_start_or_end_semver_start():
    # Semantic version at start of title
    assert catalog.detect_title_start_or_end_semver("1.0.4 - Initial Release") == "1.0.4"
    assert catalog.detect_title_start_or_end_semver("v2.1.0: Bug fixes") == "2.1.0"
    assert catalog.detect_title_start_or_end_semver("0.6 Rockbox port") == "0.6"
    assert catalog.detect_title_start_or_end_semver("[3.1.2] Update") == "3.1.2"
    assert catalog.detect_title_start_or_end_semver("(v1.5) Stable") == "1.5"
    assert catalog.detect_title_start_or_end_semver("Release 2.0.4 for OS 3.0.2") == "2.0.4"
    assert catalog.detect_title_start_or_end_semver("Version 1.8.2-fastboot") == "1.8.2"


def test_detect_title_start_or_end_semver_end():
    # Semantic version at end of title
    assert catalog.detect_title_start_or_end_semver("Y1 Rockbox 0.6") == "0.6"
    assert catalog.detect_title_start_or_end_semver("Innioasis Y1 Update 3.1.2") == "3.1.2"
    assert catalog.detect_title_start_or_end_semver("Rockbox Port - v0.5.1") == "0.5.1"
    assert catalog.detect_title_start_or_end_semver("Timmkoo Firmware - 2.0.1") == "2.0.1"
    assert catalog.detect_title_start_or_end_semver("Custom OS [1.2.3]") == "1.2.3"
    assert catalog.detect_title_start_or_end_semver("Y2Linux 1.0 Beta 9.2.13") == "9.2.13"


def test_detect_title_start_or_end_semver_with_trailing_qualifiers():
    # Trailing designations should be stripped before detecting end version
    assert catalog.detect_title_start_or_end_semver("Y1 Rockbox 0.6 (Beta)") == "0.6"
    assert catalog.detect_title_start_or_end_semver("Y1 Rockbox 0.6 (240p)") == "0.6"
    assert catalog.detect_title_start_or_end_semver("Y1 Rockbox 0.6 [Preview]") == "0.6"
    assert catalog.detect_title_start_or_end_semver("Rockbox Y1 0.6 (ADB) (Wi-Fi)") == "0.6"


def test_detect_title_start_or_end_semver_none():
    assert catalog.detect_title_start_or_end_semver("") is None
    assert catalog.detect_title_start_or_end_semver(None) is None
    assert catalog.detect_title_start_or_end_semver("Just a title with no version") is None
    assert catalog.detect_title_start_or_end_semver("Fix in middle of 123 string") is None


def test_parse_clean_release_tag_label_uses_title_semver():
    # When title has semantic version at start or end, it replaces tag on firmware list
    rel_start = {
        "tag_name": "build-20241010",
        "name": "1.0.4 - Initial Release",
    }
    assert catalog.parse_clean_release_tag_label(rel_start) == "1.0.4"
    assert catalog.format_release_display_label(rel_start) == "1.0.4"
    assert catalog.release_effective_tag(rel_start) == "1.0.4"

    rel_end = {
        "tag_name": "y1-rockbox-tag",
        "name": "Y1 Rockbox 0.6",
    }
    assert catalog.parse_clean_release_tag_label(rel_end) == "0.6 (360p Y1 Themes Only)"
    assert catalog.format_release_display_label(rel_end) == "0.6"
    assert catalog.release_effective_tag(rel_end) == "0.6"

    # Verbs are still preserved
    rel_verbs = {
        "tag_name": "git-tag-98",
        "name": "0.6 Rockbox (ADB) (Wi-Fi)",
    }
    label = catalog.parse_clean_release_tag_label(rel_verbs)
    assert label.startswith("0.6")
    assert "(ADB)" in label
    assert "(Wi-Fi)" in label


def test_release_sort_key_prioritizes_title_semver():
    rel_old = {"tag_name": "build-99", "name": "Firmware 0.5"}
    rel_new = {"tag_name": "build-01", "name": "Firmware 0.6"}

    ordered = sorted([rel_old, rel_new], key=catalog.release_sort_key, reverse=True)
    assert ordered[0] == rel_new
    assert ordered[1] == rel_old


def test_a_tag_with_a_version_and_a_date_shows_both():
    # The date follows the version in parentheses; verbs still come after it.
    assert (
        catalog.parse_clean_release_tag_label({"tag_name": "v2.0.4-20260103"})
        == "2.0.4 (January 3 2026)"
    )
    assert (
        catalog.parse_clean_release_tag_label({"tag_name": "2.0.4_2026-06-07-adb"})
        == "2.0.4 (June 7 2026) (ADB)"
    )
    assert (
        catalog.parse_clean_release_tag_label({"tag_name": "3.1.2-240126-wifi"})
        == "3.1.2 (January 26 2024) (Wi-Fi)"
    )
    # A release title's version still replaces a build-identifier tag, and a
    # date-only tag keeps the date as its version.
    assert (
        catalog.parse_clean_release_tag_label(
            {"tag_name": "2.0.4-20260103", "name": "Rockbox 0.6"}
        )
        == "0.6 (January 3 2026)"
    )
    assert catalog.parse_clean_release_tag_label({"tag_name": "2026-02-01"}) == (
        "February 1 2026"
    )
    # The version-only helper that Settings uses never gains the date.
    assert (
        catalog.parse_clean_release_tag_label(
            {"tag_name": "v2.0.4-20260103"}, version_only=True
        )
        == "2.0.4"
    )
