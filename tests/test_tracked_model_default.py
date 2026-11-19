"""One tracked player is a default, not a choice.

The catalogue already knows which model the app is tracking, so opening on the
first row instead made the user pick their own device again for no reason.
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
  <package name="Original Software" repo="y1-community/y1-stock-rom" device="Y1"
           type="img" handler="Custom Firmware" />
  <package name="Rockbox" repo="y1-community/y1-rockbox" device="Y1"
           type="img" handler="Custom Firmware" />
  <package name="Original Software" repo="y2-community/y2-stock-rom" device="Y2"
           type="img" handler="Custom Firmware" />
  <package name="Rockbox" repo="y2-community/y2-rockbox" device="Y2"
           type="img" handler="Custom Firmware" />
</slidia>
"""


class _StubReleasesWorker(QThread):
    """The model change asks for a release list; this test needs no network."""

    finished = Signal(list, str)

    def __init__(self, client, package, model, **kwargs):
        super().__init__(kwargs.get("parent"))
        self.package = package
        self.model = model

    def run(self):
        return


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _page(monkeypatch, installs):
    from src import device_tracking

    monkeypatch.setattr(
        device_tracking, "get_all_device_installs", lambda *a, **k: installs
    )
    monkeypatch.setattr(select_page, "ReleasesWorker", _StubReleasesWorker)
    page = select_page.SelectPackagePage()
    page._populate_model_combo()
    return page


def test_a_single_tracked_player_is_the_model_the_catalogue_opens_on(monkeypatch):
    app = _app()
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(MODEL_XML))
        page = _page(
            monkeypatch,
            {"Y2": {"model": "Y2", "software_name": "Original Software"}},
        )
        try:
            assert page.current_model() == "Y2"
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)


def test_two_tracked_players_leave_the_choice_alone(monkeypatch):
    app = _app()
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(MODEL_XML))
        page = _page(
            monkeypatch,
            {
                "Y1": {"model": "Y1", "software_name": "Original Software"},
                "Y2": {"model": "Y2", "software_name": "Original Software"},
            },
        )
        try:
            # Two players are a real choice: the first row stays the default.
            assert page.current_model() == "Y1"
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)


def test_a_player_added_later_is_selected_after_the_journey(monkeypatch):
    app = _app()
    saved = list(catalog.LIVE_CATALOG)
    try:
        catalog.set_live_catalog(manifest.parse_manifest_xml(MODEL_XML))
        page = _page(monkeypatch, {})
        try:
            assert page.current_model() == "Y1"
            # The journey just logged a Y2: the catalogue follows it.
            from src import device_tracking

            monkeypatch.setattr(
                device_tracking,
                "get_all_device_installs",
                lambda *a, **k: {"Y2": {"model": "Y2", "software_name": "Rockbox"}},
            )
            assert page.select_only_tracked_model() is True
            assert page.current_model() == "Y2"
        finally:
            page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)
