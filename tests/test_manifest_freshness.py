"""The manifest is the only source of install behaviour, and it stays current.

Two halves: the install gate reads the catalogue row that exists now (never the
copy a page happened to build its list from), and choosing a release refreshes
the manifest when the downloaded copy is past its freshness window, so a
corrected attribute applies to the install that follows rather than the next
launch. Nothing here names a firmware.
"""

import dataclasses
import os
import threading
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from src import catalog, install_first as first, manifest, online_data
from src.ui.main_window import MainWindow
from src.ui.select_page import SelectPackagePage

ITEMS = manifest.parse_manifest_xml(
    Path("assets/online/slidia_manifest.xml").read_text(encoding="utf-8")
)


def reborn():
    return next(p for p in ITEMS if p.name.startswith("Reborn") and p.model == "Y2")


class Gate:
    _manifest_fresh_package = MainWindow._manifest_fresh_package


def test_the_gate_reads_the_catalogues_current_row(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "packages_for_model",
        lambda model, brand="": [p for p in ITEMS if p.model == model],
    )
    target = reborn()
    # The page's copy predates a manifest correction.
    stale = dataclasses.replace(target, install_first="", install_first_when="")

    fresh = Gate()._manifest_fresh_package(stale)

    assert first.normalize_repo(fresh.repo) == first.normalize_repo(target.repo)
    assert fresh.install_first == target.install_first
    assert fresh.install_first_when == target.install_first_when


def test_a_package_without_a_catalogue_row_is_kept(monkeypatch):
    monkeypatch.setattr(
        catalog, "packages_for_model", lambda model, brand="": [p for p in ITEMS if p.model == model]
    )
    local = dataclasses.replace(reborn(), repo="")

    # A local zip is its own declaration: there is nothing to look up.
    assert Gate()._manifest_fresh_package(local) is local


class StubPage:
    _refresh_manifest_for_selection = SelectPackagePage._refresh_manifest_for_selection

    def __init__(self):
        self.window_hits = 0

    def window(self):
        self.window_hits += 1
        return object()


@pytest.fixture()
def refresh_recorder(monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, kwargs=None, name="", daemon=False):
            started.append((target, dict(kwargs or {}), name, daemon))

        def start(self):
            pass

    monkeypatch.setattr(threading, "Thread", FakeThread)
    return started


def test_a_stale_manifest_is_refreshed_when_a_release_is_chosen(
    monkeypatch, refresh_recorder
):
    monkeypatch.setattr(
        online_data, "age_seconds", lambda name, now=None: 24 * 60 * 60.0
    )

    StubPage()._refresh_manifest_for_selection()

    assert len(refresh_recorder) == 1
    target, kwargs, name, daemon = refresh_recorder[0]
    assert target is manifest.refresh_catalog
    assert kwargs == {"force_refresh": True}
    assert daemon is True


def test_a_fresh_manifest_costs_no_request(monkeypatch, refresh_recorder):
    monkeypatch.setattr(online_data, "age_seconds", lambda name, now=None: 5.0)

    StubPage()._refresh_manifest_for_selection()

    assert refresh_recorder == []


def test_no_downloaded_manifest_leaves_the_fetch_to_startup(
    monkeypatch, refresh_recorder
):
    monkeypatch.setattr(online_data, "age_seconds", lambda name, now=None: None)

    StubPage()._refresh_manifest_for_selection()

    # A cold cache belongs to the startup and connectivity refreshes: a click on
    # a row must never be the first request the app makes.
    assert refresh_recorder == []


def test_choosing_releases_in_a_row_asks_at_most_once(
    monkeypatch, refresh_recorder
):
    monkeypatch.setattr(
        online_data, "age_seconds", lambda name, now=None: 24 * 60 * 60.0
    )
    page = StubPage()

    page._refresh_manifest_for_selection()
    page._refresh_manifest_for_selection()

    assert len(refresh_recorder) == 1
