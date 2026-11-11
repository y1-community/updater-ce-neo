"""Published stable releases from manifest sources stay visible.

``releases_backup.json`` is a periodic export, so a repository added to the
remote ``slidia_manifest.xml`` can be missing from the local copy. The
catalogue's availability gate must fail open in that case instead of hiding
the source, and a release cached by a live fetch must outrank an older export.
``rockbox-y1/rockbox`` publishing ``stable-v0.6`` is the shape this guards.
"""

import os
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import catalog, versioning  # noqa: E402
from src.catalog import FirmwarePackage, ReleasesClient  # noqa: E402
from src.console import releases as console_releases  # noqa: E402


def _release(tag, *assets, name=None, prerelease=False):
    return {
        "tag_name": tag,
        "name": name or tag,
        "published_at": "2026-10-29T00:51:15Z",
        "prerelease": prerelease,
        "assets": [
            {
                "name": asset,
                "browser_download_url": f"https://example.test/{asset}",
                "size": 1,
            }
            for asset in assets
        ],
    }


def _package(model="Y1", repo="rockbox-y1/rockbox", name="Rockbox"):
    return FirmwarePackage(
        slug=f"{name.lower().replace(' ', '-')}-{model.lower()}",
        name=name,
        model=model,
        repo=repo,
        package_name="rom.zip",
    )


class _Client:
    """Release client that answers only what a test tells it to."""

    def __init__(self, cached=None, live=None):
        self.cached = cached
        self.live = live
        self.asked = []

    def get_cached_releases(self, repo, ignore_ttl=False):
        return self.cached

    def available_release_rows(self, repo):
        self.asked.append(repo)
        return self.live


@pytest.fixture(autouse=True)
def _isolated_catalogue():
    saved = list(catalog.LIVE_CATALOG)
    catalog._remember_unavailable([])
    catalog._AVAILABILITY_CACHE.clear()
    yield
    catalog.set_live_catalog(saved)
    catalog._remember_unavailable([])
    catalog._AVAILABILITY_CACHE.clear()


def _rows(*releases, repo="rockbox-y1/rockbox"):
    return ReleasesClient.__new__(ReleasesClient)._build_releases(list(releases), repo)


def test_a_source_added_to_the_manifest_is_not_hidden_by_an_old_snapshot(monkeypatch):
    """The source has no row in the build-time export yet; it must stay."""
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {"newco/rockbox": []})
    monkeypatch.setattr(catalog, "_release_snapshot_age", lambda: 3 * 24 * 3600)

    kept = catalog.filter_unavailable_packages(
        [_package(repo="newco/rockbox")],
        allow_network=False,
        client=_Client(),
    )

    assert [p.repo for p in kept] == ["newco/rockbox"]


def test_a_fresh_snapshot_still_hides_a_repository_with_no_releases(monkeypatch):
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {"newco/rockbox": []})
    monkeypatch.setattr(catalog, "_release_snapshot_age", lambda: 60.0)

    kept = catalog.filter_unavailable_packages(
        [_package(repo="newco/rockbox")],
        allow_network=False,
        client=_Client(),
    )

    assert kept == []


def test_a_live_cached_stable_release_outranks_an_old_snapshot(monkeypatch):
    """A release cached after the export proves the source now has firmware."""
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {"newco/rockbox": []})
    monkeypatch.setattr(catalog, "_release_snapshot_age", lambda: 3 * 24 * 3600)
    client = _Client(
        cached=_rows(
            _release(
                "stable-v0.6",
                "rom.zip",
                "rom_type_b.zip",
                name="Rockbox-Y1 v0.6",
            )
        )
    )

    kept = catalog.filter_unavailable_packages(
        [_package(repo="newco/rockbox")],
        allow_network=False,
        client=client,
    )

    assert [p.repo for p in kept] == ["newco/rockbox"]


def test_a_live_check_restores_a_source_hidden_by_an_old_snapshot(monkeypatch):
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {"newco/rockbox": []})
    monkeypatch.setattr(catalog, "_release_snapshot_age", lambda: 3 * 24 * 3600)
    client = _Client(
        live=[
            _release(
                "stable-v0.6",
                "rom.zip",
                "rom_type_b.zip",
                name="Rockbox-Y1 v0.6",
            )
        ]
    )

    kept = catalog.filter_unavailable_packages(
        [_package(repo="newco/rockbox")],
        allow_network=True,
        client=client,
    )

    assert [p.repo for p in kept] == ["newco/rockbox"]
    assert client.asked == ["newco/rockbox"]


def test_stable_v06_is_listed_for_y1_with_type_a_and_b_assets():
    package = _package()
    client = ReleasesClient.__new__(ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: _rows(
        _release("stable-v0.6", "rom.zip", "rom_type_b.zip", name="Rockbox-Y1 v0.6")
    )

    rows = client.releases_for_package(package, "Y1", selected_type="A")

    assert [row["tag_name"] for row in rows] == ["stable-v0.6"]
    assert rows[0]["asset_name"] == "rom.zip"


def test_a_stable_tag_without_a_readable_version_survives_the_rockbox_gate():
    minimums = {"rockbox-y1/rockbox": "0.5"}

    assert (
        versioning.release_version_ok(
            "rockbox-y1/rockbox",
            "stable-rockbox",
            model="Y1",
            release_name="Rockbox-Y1 stable",
            minimums=minimums,
        )
        is True
    )
    # Historical pre-0.5 builds carry a readable version and stay hidden.
    assert (
        versioning.release_version_ok(
            "rockbox-y1/rockbox",
            "stable-v0.4",
            model="Y1",
            release_name="Rockbox-Y1 v0.4",
            minimums=minimums,
        )
        is False
    )


def test_the_terminal_hides_only_old_or_unmarked_rockbox_builds():
    old = _release("stable-v0.4", "rom.zip", name="Rockbox-Y1 v0.4")
    stable = _release("stable-rockbox", "rom.zip", name="Rockbox-Y1 stable")

    rows = console_releases.rows_from_releases(
        [old, stable],
        model="Y1",
        hardware_type="A",
        repo="rockbox-y1/rockbox",
    )

    assert [row.tag for row in rows] == ["stable-rockbox"]
