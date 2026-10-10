"""Tests for 240p release rows, 360p theme merge helpers, ADB parsing, and wizard."""

import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import catalog
from src.catalog import FirmwarePackage, ReleasesClient


def _asset(name):
    return {"name": name, "browser_download_url": f"https://x/{name}", "size": 100}


def _rel(tag, names, repo="rockbox-y1/rockbox"):
    variants = [catalog._parse_rom_asset_variant(_asset(n), tag, repo) for n in names]
    return {
        "tag_name": tag, "name": tag, "body": "",
        "published_at": "2026-01-01T00:00:00Z",
        "assets": [_asset(n) for n in names],
        "rom_variants": [v for v in variants if v],
        "prerelease": False, "source_repo": repo,
    }


def _client(releases):
    c = ReleasesClient.__new__(ReleasesClient)
    c.get_all_releases = lambda repo, force_refresh=False: releases
    return c


PKG = FirmwarePackage("rockbox-y1", "Rockbox", "Y1", "rockbox-y1/rockbox", "rom.zip")


def test_240p_rows_split_with_suffix_only_on_240p():
    releases = [_rel("stable-v0.6", ["rom_360p.zip", "rom_240p.zip"])]
    c = _client(releases)
    rows = c.releases_for_package(PKG, "Y1", prefer_240p=True)
    assert len(rows) == 2, [(r["tag_name"], r["asset_name"]) for r in rows]
    std, low = rows[0], rows[1]
    assert std["asset_name"] == "rom_360p.zip"
    assert std["selected_resolution"] == "360p"
    assert low["asset_name"] == "rom_240p.zip"
    assert low["selected_resolution"] == "240p"
    std_label = catalog.parse_clean_release_tag_label(std, prefer_240p=True)
    low_label = catalog.parse_clean_release_tag_label(low, prefer_240p=True)
    assert "(240p)" not in std_label, std_label
    assert "(240p)" in low_label, low_label


def test_240p_only_release_single_suffixed_row():
    releases = [_rel("stable-v0.5", ["rom_240p.zip"])]
    c = _client(releases)
    assert c.releases_for_package(PKG, "Y1") == []
    rows = c.releases_for_package(PKG, "Y1", prefer_240p=True)
    assert len(rows) == 1 and rows[0]["asset_name"] == "rom_240p.zip"
    assert "(240p)" in catalog.parse_clean_release_tag_label(rows[0], prefer_240p=True)


def test_240p_rows_respect_type_b():
    releases = [_rel("stable-v0.6", [
        "rom_360p.zip", "rom_240p.zip",
        "rom_360p_type_b.zip", "rom_240p_type_b.zip",
    ])]
    c = _client(releases)
    rows = c.releases_for_package(PKG, "Y1", prefer_240p=True, selected_type="B")
    names = sorted(r["asset_name"] for r in rows)
    assert names == ["rom_240p_type_b.zip", "rom_360p_type_b.zip"], names


def test_360p_label_has_no_suffix_when_filter_on():
    rel = _rel("stable-v0.6", ["rom_360p.zip"])
    rel["selected_resolution"] = "360p"
    rel["asset_name"] = "rom_360p.zip"
    label = catalog.parse_clean_release_tag_label(rel, prefer_240p=True)
    assert "(240p)" not in label, label


def test_merge_dirs_overwrites():
    from src.theme_pack import merge_dirs

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src" / ".rockbox"
        (src / "themes").mkdir(parents=True)
        (src / "themes" / "a.cfg").write_text("new")
        (src / "fonts").mkdir(parents=True)
        (src / "fonts" / "f.fnt").write_text("font")
        dst = Path(tmp) / "dst" / ".rockbox"
        (dst / "themes").mkdir(parents=True)
        (dst / "themes" / "a.cfg").write_text("old")
        (dst / "themes" / "keep.cfg").write_text("keep")
        count = merge_dirs(src, dst)
        assert count == 2
        assert (dst / "themes" / "a.cfg").read_text() == "new"
        assert (dst / "themes" / "keep.cfg").read_text() == "keep"
        assert (dst / "fonts" / "f.fnt").read_text() == "font"


def test_build_combined_keeps_bundled_fonts_and_overlays_remote():
    from src import theme_pack

    with tempfile.TemporaryDirectory() as tmp:
        bundled = Path(tmp) / "bundled"
        (bundled / "themes").mkdir(parents=True)
        (bundled / "themes" / "shared.cfg").write_text("bundled")
        (bundled / "fonts").mkdir(parents=True)
        (bundled / "fonts" / "pack.fnt").write_text("fonts-pack")
        remote_zip = Path(tmp) / "remote.zip"
        with zipfile.ZipFile(remote_zip, "w") as zf:
            zf.writestr("themes-themepack/.rockbox/themes/shared.cfg", "remote")
            zf.writestr("themes-themepack/.rockbox/themes/extra.cfg", "extra")
        dest = Path(tmp) / "combined"
        with mock.patch.object(theme_pack, "bundled_rockbox_dir", return_value=bundled):
            n = theme_pack.build_combined_rockbox_dir(remote_zip, dest)
        assert n == 2
        assert (dest / "themes" / "shared.cfg").read_text() == "remote"
        assert (dest / "themes" / "extra.cfg").read_text() == "extra"
        assert (dest / "fonts" / "pack.fnt").read_text() == "fonts-pack"


def test_adb_devices_parsing():
    from src import adb as adb_mod

    proc = mock.Mock()
    proc.returncode = 0
    proc.stdout = "List of devices attached\nABC123\tdevice\nXYZ999\tunauthorized\n\n"
    with mock.patch.object(adb_mod.subprocess, "run", return_value=proc):
        assert adb_mod.adb_devices("/fake/adb") == ["ABC123"]


def test_find_adb_binary_env_override():
    from src import adb as adb_mod

    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "adb"
        fake.write_bytes(b"#!/bin/sh\n")
        with mock.patch.dict(os.environ, {"ADB_BINARY": str(fake)}):
            assert adb_mod.find_adb_binary() == str(fake)


def test_wizard_instantiates_with_four_pages():
    from PySide6.QtWidgets import QApplication
    from src.theme_pack import Rockbox360pSetupWizard

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    wiz = Rockbox360pSetupWizard(model="Y1")
    assert wiz._stack.count() == 4
    assert wiz._stack.currentIndex() == 0
    # Deterministic regardless of host ADB: no binary -> USB fallback page.
    with mock.patch("src.adb.find_adb_binary", return_value=None):
        wiz._on_next()
        app.processEvents()
        assert wiz._stack.currentIndex() == 2
    # With a binary present the watch page is shown.
    wiz._stack.setCurrentIndex(0)
    with mock.patch("src.adb.find_adb_binary", return_value="/fake/adb"):
        with mock.patch.object(wiz, "_start_watch", wraps=wiz._start_watch) as spied:
            wiz._stop_watch = lambda: None  # no worker started in test
            wiz._on_next()
            app.processEvents()
            assert wiz._stack.currentIndex() == 1
            assert spied.called
    del wiz._stop_watch  # restore the real stopper
    wiz._stop_watch()
    wiz.close()
    app.processEvents()
