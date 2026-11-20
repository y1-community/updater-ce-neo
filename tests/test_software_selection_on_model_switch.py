"""The Software dropdown follows a model switch without changing firmware.

Every model opens on its Original Software row. A software name that the
new model also publishes is carried across by name, and the release listing
is rebuilt from the *new* model's package rather than only changing the label.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, Signal  # noqa: E402

from src import catalog, manifest  # noqa: E402
from src.ui import select_page  # noqa: E402


MODEL_XML = """
<slidia>
  <package name="Rockbox" repo="y1-community/y1-rockbox" device="Y1"
           type="img" handler="Custom Firmware" />
  <package name="Original Software" repo="y1-community/y1-stock-rom" device="Y1"
           type="img" handler="Custom Firmware" />
  <package name="Inniclassic" repo="y1-community/y1-inny" device="Y1"
           type="img" handler="Custom Firmware" />
  <package name="Rockbox" repo="y2-community/y2-rockbox" device="Y2"
           type="img" handler="Custom Firmware" />
  <package name="Original Software" repo="y2-community/y2-stock-rom" device="Y2"
           type="img" handler="Custom Firmware" />
  <package name="Y2Player" repo="y2-community/y2-player" device="Y2"
           type="img" handler="Custom Firmware" />
</slidia>
"""


class _StubReleasesWorker(QThread):
    """Capture the package the page asks to list, without any network work."""

    finished = Signal(list, str)
    started_with = []

    def __init__(self, client, package, model, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.package = package
        self.model = model
        type(self).started_with.append((package, model))

    def run(self):
        return


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_the_keep_data_note_sits_under_the_software_name(monkeypatch):
    app = _app()
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(MODEL_XML))
        monkeypatch.setattr(select_page, "ReleasesWorker", _StubReleasesWorker)
        from src import keep_data

        monkeypatch.setattr(
            keep_data, "incompatible_with_current", lambda *a, **k: False
        )
        page = select_page.SelectPackagePage()
        try:
            assert page._keep_data_note.isHidden()
            monkeypatch.setattr(
                keep_data, "incompatible_with_current", lambda *a, **k: True
            )
            page._update_keep_data_note()
            assert not page._keep_data_note.isHidden()
            assert page._keep_data_note.text() == select_page.tr(
                "sel_keep_data_incompatible"
            )

            monkeypatch.setattr(
                keep_data, "incompatible_with_current", lambda *a, **k: False
            )
            page._update_keep_data_note()
            assert page._keep_data_note.isHidden()
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)


def test_model_switch_keeps_only_a_same_named_software(monkeypatch):
    app = _app()
    saved = list(catalog.LIVE_CATALOG)
    _StubReleasesWorker.started_with = []
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(MODEL_XML))
        monkeypatch.setattr(select_page, "ReleasesWorker", _StubReleasesWorker)
        page = select_page.SelectPackagePage()
        try:
            page._model_combo.setCurrentText("Y1")
            # Original is selected even though Rockbox is the first row.
            assert page.current_software() == "Original Software"
            assert page.current_package().repo == "y1-community/y1-stock-rom"

            page._software_combo.setCurrentIndex(
                page._find_software_index("Rockbox")
            )
            assert page.current_package().repo == "y1-community/y1-rockbox"

            page._model_combo.setCurrentText("Y2")
            assert page.current_software() == "Rockbox"
            assert page.current_package().repo == "y2-community/y2-rockbox"
            package, model = _StubReleasesWorker.started_with[-1]
            assert (package.repo, model) == ("y2-community/y2-rockbox", "Y2")

            # A name Y2 does not publish returns the switch to Original and
            # drops the old release selection before the new list loads.
            page._model_combo.setCurrentText("Y1")
            page._software_combo.setCurrentIndex(
                page._find_software_index("Inniclassic")
            )
            assert page.current_package().repo == "y1-community/y1-inny"
            page._current_selected_rel = {"tag_name": "stale"}
            page._details_package = page.current_package()
            page._listed_releases = [{"tag_name": "stale"}]

            page._model_combo.setCurrentText("Y2")
            assert page.current_software() == "Original Software"
            assert page.current_package().repo == "y2-community/y2-stock-rom"
            package, model = _StubReleasesWorker.started_with[-1]
            assert (package.repo, model) == ("y2-community/y2-stock-rom", "Y2")
            assert page._current_selected_rel is None
            assert page._details_package is None
            assert page._listed_releases == []
        finally:
            page.deleteLater()
            app.processEvents()
    finally:
        catalog.set_live_catalog(saved)
        _StubReleasesWorker.started_with = []


def test_original_is_the_default_even_when_not_the_first_row():
    assert select_page.SelectPackagePage._default_software_name(
        ["Rockbox", "Original Software", "Inniclassic"]
    ) == "Original Software"
    assert select_page.SelectPackagePage._default_software_name(
        ["Rockbox", "Original (Stock)"]
    ) == "Original (Stock)"
    assert select_page.SelectPackagePage._default_software_name(
        ["Rockbox", "Y2Player"]
    ) == "Rockbox"


def test_same_name_matching_ignores_case():
    assert select_page.SelectPackagePage._software_name_kept_across_models(
        "rockbox", ["Rockbox", "Original Software"]
    ) == "Rockbox"
    assert select_page.SelectPackagePage._software_name_kept_across_models(
        "Inniclassic", ["Rockbox", "Original Software"]
    ) == ""