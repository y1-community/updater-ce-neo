"""Dead catalogue entries stay out of the Select Software drop-downs.

``slidia_manifest.xml`` is maintained by hand. It can name a repository that
has published no releases yet, or whose releases carry no firmware zip for the
model it lists. Such an entry only ever produces an empty listing, so the
manifest worker drops it before installing the live catalogue. When the answer
cannot be known locally or online, the entry is kept rather than hidden.
"""

import os
import sys
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import catalog, manifest  # noqa: E402


def _release(tag, *assets):
    return {
        "tag_name": tag,
        "name": tag,
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "assets": [
            {
                "name": name,
                "browser_download_url": f"https://example.test/{name}",
                "size": 1,
            }
            for name in assets
        ],
    }


def _package(model, repo, name="Original Software"):
    return catalog.FirmwarePackage(
        slug=f"{name.lower().replace(' ', '-')}-{model.lower()}",
        name=name,
        model=model,
        repo=repo,
        package_name="rom.zip",
    )


class _FakeClient:
    """Release client that answers only what a test tells it to."""

    def __init__(self, live_rows, cached_rows=None):
        self.live_rows = live_rows
        self.cached_rows = cached_rows or {}
        self.asked = []

    def get_cached_releases(self, repo, ignore_ttl=False):
        return self.cached_rows.get(repo.lower())

    def available_release_rows(self, repo):
        self.asked.append(repo.lower())
        return self.live_rows.get(repo.lower())


@pytest.fixture(autouse=True)
def _isolated_catalogue():
    saved = list(catalog.LIVE_CATALOG)
    catalog._remember_unavailable([])
    catalog._AVAILABILITY_CACHE.clear()
    yield
    catalog.set_live_catalog(saved)
    catalog._remember_unavailable([])
    catalog._AVAILABILITY_CACHE.clear()


@pytest.fixture()
def fresh_snapshot(monkeypatch):
    """A release export young enough to hide a dead source on its own."""
    monkeypatch.setattr(catalog, "_release_snapshot_age", lambda: 60.0)


def test_snapshot_release_zip_keeps_an_entry(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "_release_snapshot_rows",
        lambda: {"acme/g1-stock": [_release("MX3-9_EN-V1.04", "rom_g1.zip")]},
    )
    client = _FakeClient({})

    kept = catalog.filter_unavailable_packages(
        [_package("G1", "acme/g1-stock")], allow_network=True, client=client
    )

    assert [p.model for p in kept] == ["G1"]
    assert client.asked == []
    catalog.set_live_catalog(kept)
    assert catalog.available_models() == ["G1"]


def test_an_empty_release_list_is_a_definitive_answer(monkeypatch, fresh_snapshot):
    """A repository the snapshot knows with no releases needs no request."""
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {"acme/g3-stock": []})
    client = _FakeClient({"acme/g3-stock": []})

    kept = catalog.filter_unavailable_packages(
        [_package("G3", "acme/g3-stock")], allow_network=True, client=client
    )

    assert kept == []
    assert client.asked == []


def test_releases_without_a_firmware_zip_are_hidden(monkeypatch, fresh_snapshot):
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {})
    client = _FakeClient({"acme/images": [_release("v1", "system.img", "boot.img")]})

    kept = catalog.filter_unavailable_packages(
        [_package("R1", "acme/images")], allow_network=True, client=client
    )

    assert kept == []


def test_a_new_repository_with_a_zip_is_kept(monkeypatch):
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {})
    client = _FakeClient({"acme/g5-stock": [_release("MX4_EN-V1.11", "rom_g5.zip")]})

    kept = catalog.filter_unavailable_packages(
        [_package("G5", "acme/g5-stock")], allow_network=True, client=client
    )

    assert [p.model for p in kept] == ["G5"]


def test_unverifiable_entries_are_kept(monkeypatch):
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {})

    # Network off: the snapshot knows nothing, so nothing may be hidden.
    offline = _FakeClient({})
    kept = catalog.filter_unavailable_packages(
        [_package("PR1", "acme/pr1-stock")], allow_network=False, client=offline
    )
    assert [p.model for p in kept] == ["PR1"]
    assert offline.asked == []

    # Network on but the request could not be answered: same, fail open.
    unknown = _FakeClient({})
    kept = catalog.filter_unavailable_packages(
        [_package("PR1", "acme/pr1-stock")], allow_network=True, client=unknown
    )
    assert [p.model for p in kept] == ["PR1"]
    assert unknown.asked == ["acme/pr1-stock"]


def test_a_repository_cached_by_a_listing_avoids_the_network(monkeypatch):
    monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: {})
    client = _FakeClient(
        {}, cached_rows={"acme/g5-stock": [_release("v1", "rom_g5.zip")]}
    )

    kept = catalog.filter_unavailable_packages(
        [_package("G5", "acme/g5-stock")], allow_network=True, client=client
    )

    assert [p.model for p in kept] == ["G5"]
    assert client.asked == []


def test_static_fallback_does_not_resurrect_a_hidden_entry(monkeypatch, fresh_snapshot):
    """The offline table is merged per model; a hidden live row must stay gone."""
    monkeypatch.setattr(
        catalog, "_release_snapshot_rows", lambda: {"rockbox-y1/rockbox": []}
    )
    client = _FakeClient({"rockbox-y1/rockbox": []})
    live = _package("Y1", "rockbox-y1/rockbox", name="Rockbox")

    kept = catalog.filter_unavailable_packages(
        [live], allow_network=True, client=client
    )
    assert kept == []

    catalog.set_live_catalog(
        [_package("Y2", "spycemagic/y2-rockbox", name="Rockbox")]
    )
    names = [p.name for p in catalog.packages_for_model("Y1")]
    assert "Rockbox" not in names


def test_refresh_catalog_installs_only_listable_entries(monkeypatch, fresh_snapshot):
    entries = [
        _package("G1", "acme/g1-stock"),
        _package("G3", "acme/g3-stock"),
    ]
    monkeypatch.setattr(manifest, "fetch_manifest", lambda: entries)
    monkeypatch.setattr(manifest, "cache_manifest", lambda _entries: None)
    # G1 is newer than the snapshot (so it is checked live); G3 is in the
    # snapshot with no releases at all.
    monkeypatch.setattr(
        catalog,
        "_release_snapshot_rows",
        lambda: {"acme/g3-stock": []},
    )
    client = _FakeClient(
        {
            "acme/g1-stock": [_release("v1", "rom_g1.zip")],
            "acme/g3-stock": [],
        }
    )
    monkeypatch.setattr(catalog, "ReleasesClient", lambda: client)

    installed = manifest.refresh_catalog(allow_release_network=True)

    assert [p.model for p in installed] == ["G1"]
    assert [p.model for p in catalog.LIVE_CATALOG] == ["G1"]


def test_a_failed_manifest_fetch_keeps_the_installed_catalogue(monkeypatch):
    """A transient fetch failure must not shrink the catalogue to the snapshot."""
    catalog.set_live_catalog([_package("G1", "acme/g1-stock")])
    stale = [_package("G3", "acme/g3-stock")]
    monkeypatch.setattr(manifest, "fetch_manifest", lambda: None)
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: stale)
    monkeypatch.setattr(manifest, "load_snapshot_manifest", lambda: stale)

    entries = manifest.refresh_catalog(force_refresh=True)

    assert [p.model for p in entries] == ["G1"]
    assert [p.model for p in catalog.LIVE_CATALOG] == ["G1"]
