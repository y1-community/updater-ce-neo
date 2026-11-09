"""Brand filtering for the Select Software release listing.

``slidia_manifest.xml`` may tag a ``<package/>`` with ``brand``. When it does,
the page offers a Brand drop-down before Model, defaulting to Innioasis; the
choice scopes which models and releases are listed and nothing else.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import catalog, manifest  # noqa: E402

BRANDED_XML = """
<slidia>
  <package name="Original Software" repo="y1-community/y1-stock-rom" device="Y1"
           type="img" handler="Custom Firmware" />
  <package name="Original Software" repo="timmkoo/q5-stock" device="Q5"
           brand="Timmkoo" type="img" handler="Custom Firmware" />
</slidia>
"""


def _page():
    from PySide6.QtWidgets import QApplication
    from src.ui.select_page import SelectPackagePage

    QApplication.instance() or QApplication(["--platform", "offscreen"])
    return SelectPackagePage()


def test_manifest_parses_and_caches_the_brand(tmp_path, monkeypatch):
    entries = manifest.parse_manifest_xml(BRANDED_XML)
    assert [(e.model, e.brand) for e in entries] == [("Y1", ""), ("Q5", "Timmkoo")]

    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(entries)
    restored = manifest.load_cached_manifest()
    assert [e.brand for e in restored] == ["", "Timmkoo"]


def test_untagged_releases_are_innioasis_and_brands_are_innioasis_first():
    saved = list(catalog.LIVE_CATALOG)
    try:
        entries = manifest.parse_manifest_xml(BRANDED_XML)
        # A branded entry first in the document must not become the default.
        catalog.set_live_catalog(list(reversed(entries)))
        assert catalog.brand_names() == ["Innioasis", "Timmkoo"]
        assert catalog.package_brand(entries[0]) == "Innioasis"
        assert catalog.available_models(brand="Timmkoo") == ["Q5"]
        assert catalog.available_models(brand="Innioasis") == ["Y1"]
        assert catalog.software_names_for_model("Y1", brand="Timmkoo") == []
        assert (
            catalog.packages_for_model_software(
                "Y1", "Original Software", brand="Timmkoo"
            )
            == []
        )
    finally:
        catalog.set_live_catalog(saved)


def test_unreleased_models_and_brands_are_not_offered():
    """Only released firmware decides what the drop-downs offer.

    A Timmkoo player with nothing published (the Q8 and A5 today) has no model
    row, and its brand has no row either, so the filter cannot lead to an empty
    list. The registry still knows them for prompts and imported packages.
    """
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog([p for p in catalog.CATALOG if p.model == "Y1"])
        page = _page()
        try:
            assert page._brand_combo.count() == 0
            assert not page._brand_combo.isVisibleTo(page)
            models = [
                page._model_combo.itemText(i) for i in range(page._model_combo.count())
            ]
            assert models == ["Y1"], models
            assert page.current_brand() == ""
            assert page.current_model() == "Y1"
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)


def test_brand_dropdown_defaults_to_innioasis_and_filters_the_listing():
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(BRANDED_XML))
        page = _page()
        try:
            assert page._brand_label.isVisibleTo(page)
            assert page._brand_combo.isVisibleTo(page)
            labels = [
                page._brand_combo.itemText(i) for i in range(page._brand_combo.count())
            ]
            assert labels == ["Innioasis", "Timmkoo"]
            assert page.current_brand() == "Innioasis"
            assert page.current_model() == "Y1"
            assert page.current_package().repo == "y1-community/y1-stock-rom"

            page._brand_combo.setCurrentIndex(page._brand_combo.findData("Timmkoo"))
            assert page.current_brand() == "Timmkoo"
            # Only the brand's released model is listed: no A5 or Q8 row.
            models = [
                page._model_combo.itemText(i) for i in range(page._model_combo.count())
            ]
            assert models == ["Q5"], models
            assert page.current_model() == "Q5"
            assert page.current_software() == "Original Software"
            assert page.current_package().repo == "timmkoo/q5-stock"

            page._brand_combo.setCurrentIndex(page._brand_combo.findData("Innioasis"))
            assert page.current_model() == "Y1"
            assert page.current_package().repo == "y1-community/y1-stock-rom"
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)


def test_a_model_without_a_tag_keeps_its_registry_brand():
    """Brands belong to models: a newly discovered player is tagged in
    device_models.xml, so its releases need no brand attribute of their own."""
    saved = list(catalog.LIVE_CATALOG)
    try:
        xml = (
            "<slidia>"
            '<package name="Original Software" repo="y1-community/y1-stock-rom"'
            ' device="Y1" type="img" handler="Custom Firmware" />'
            '<package name="Original Software" repo="y1-community/y1-stock-rom"'
            ' device="A5" type="img" handler="Custom Firmware" />'
            '<package name="Original Software" repo="timmkoo/q5-stock"'
            ' device="Q5" brand="Timmkoo" type="img" handler="Custom Firmware" />'
            "</slidia>"
        )
        catalog.set_live_catalog(manifest.parse_manifest_xml(xml))
        # The A5 carries no brand attribute; the registry says Timmkoo.
        assert catalog.available_models(brand="Timmkoo") == ["A5", "Q5"]
        assert catalog.available_models(brand="Innioasis") == ["Y1"]
        assert "Original Software" in catalog.software_names_for_model(
            "A5", brand="Timmkoo"
        )

        # A model the registry has never heard of is the vendor's own.
        unknown = manifest.parse_manifest_xml(
            '<slidia><package name="X" repo="acme/zz9" device="ZZ9"'
            ' type="img" handler="Custom Firmware" /></slidia>'
        )[0]
        assert catalog.package_brand(unknown) == "Innioasis"

        page = _page()
        try:
            page._brand_combo.setCurrentIndex(page._brand_combo.findData("Timmkoo"))
            models = [
                page._model_combo.itemText(i)
                for i in range(page._model_combo.count())
            ]
            assert models == ["A5", "Q5"]
            assert page.current_model() == "A5"
            page._brand_combo.setCurrentIndex(page._brand_combo.findData("Innioasis"))
            assert page.current_model() == "Y1"
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)


def test_a_brand_with_no_releases_is_not_offered():
    """A brand row that cannot lead to a model is noise in the filter.

    The manifest may keep a branded entry for a project whose releases have
    gone, but the point of the Brand drop-down is to narrow a listing that
    exists. A brand with no models left must disappear with its models.
    """
    saved = list(catalog.LIVE_CATALOG)
    try:
        xml = (
            "<slidia>"
            '<package name="Original Software" repo="y1-community/y1-stock-rom"'
            ' device="Y1" brand="Innioasis" type="img" handler="Custom Firmware" />'
            "</slidia>"
        )
        catalog.set_live_catalog(manifest.parse_manifest_xml(xml))
        assert catalog.brand_names() == ["Innioasis"]

        catalog.set_live_catalog([])
        # Without a manifest the static table is the offline fallback; it has
        # no brand attributes, so there is still no Brand row.
        assert catalog.brand_names() == []
    finally:
        catalog.set_live_catalog(saved)


def test_a_model_appears_once_a_release_is_listed():
    """Nothing hardcodes the Q8 away: listing a release brings its row back."""
    from src import device_models

    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(BRANDED_XML))
        offered = device_models.selectable_models(
            catalog.available_models(brand="Timmkoo"), brand="Timmkoo"
        )
        assert offered == ["Q5"]
        xml = BRANDED_XML.replace(
            "</slidia>",
            '<package name="Rockbox" repo="timmkoo/q8-rockbox" device="Q8"'
            ' brand="Timmkoo" type="img" handler="Custom Firmware" /></slidia>',
        )
        catalog.set_live_catalog(manifest.parse_manifest_xml(xml))
        offered = device_models.selectable_models(
            catalog.available_models(brand="Timmkoo"), brand="Timmkoo"
        )
        assert offered == ["Q5", "Q8"]
    finally:
        catalog.set_live_catalog(saved)


def test_offline_table_drops_models_the_snapshot_shows_unreleased(monkeypatch):
    """Offline, the static A5 rows go when the snapshot lists no A5 zip."""
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog([])
        a5_repos = {
            p.repo.lower(): [{"tag_name": "v1", "assets": [{"name": "rom.zip"}]}]
            for p in catalog.CATALOG
            if p.model == "A5"
        }
        monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: a5_repos)
        assert "A5" not in catalog.available_models()
        assert "Y1" in catalog.available_models()

        released = {
            repo: [{"tag_name": "v1", "assets": [{"name": "rom_a5.zip"}]}]
            for repo in a5_repos
        }
        monkeypatch.setattr(catalog, "_release_snapshot_rows", lambda: released)
        assert "A5" in catalog.available_models()
    finally:
        catalog.set_live_catalog(saved)
