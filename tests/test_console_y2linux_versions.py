"""The TUI must read Y2Linux versions the same way as the window and site."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src import catalog
from src.console.releases import rows_from_releases


def _asset(name: str) -> dict:
    return {
        "name": name,
        "browser_download_url": "https://example.invalid/" + name,
        "size": 100,
    }


def _release(tag: str, title: str) -> dict:
    version = title.rsplit(" ", 1)[-1]
    return {
        "tag_name": tag,
        "name": title,
        "body": "",
        "prerelease": False,
        "draft": False,
        "assets": [
            _asset(f"Y2Linux-1.0.0-beta.{version}-first-install.zip"),
            _asset(f"Y2Linux-1.0.0-beta.{version}-sources-buildroot-legal-info.tar"),
            _asset("Y2Linux-sources-third-party-16db0acea92d.tar"),
        ],
    }


def _rows():
    releases = [
        _release("baseline-229", "Y2Linux 1.0 Beta 9.3.20"),
        _release("baseline-228", "Y2Linux 1.0 Beta 9.3.19"),
        _release("baseline-227", "Y2Linux 1.0 Beta 9.3.18"),
    ]
    return rows_from_releases(
        releases,
        model="Y2",
        hardware_type="",
        repo="schulzcode/y2linux",
    )


class Y2LinuxVersionTests(unittest.TestCase):
    def test_rows_use_the_product_version_not_the_title_prefix(self):
        rows = _rows()
        self.assertEqual([row.label for row in rows], ["9.3.20", "9.3.19", "9.3.18"])
        self.assertEqual(
            [row.version for row in rows],
            [(9, 3, 20), (9, 3, 19), (9, 3, 18)],
        )
        self.assertEqual([row.tag for row in rows], ["baseline-229", "baseline-228", "baseline-227"])

    def test_tui_labels_match_the_window_and_archive_parser(self):
        for row in _rows():
            rel = {
                "tag_name": row.tag,
                "name": row.name,
                "prerelease": False,
                "asset_name": row.asset_name,
                "selected_resolution": row.resolution,
            }
            self.assertEqual(row.label, catalog.parse_clean_release_tag_label(rel))
            self.assertEqual(row.label, catalog.format_release_display_label(rel))


if __name__ == "__main__":
    unittest.main()
