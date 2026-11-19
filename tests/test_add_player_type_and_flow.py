"""Adding a player: Original Software names its revision, other software asks.

On Original Software the Y1's Type A and Type B releases share one Version
list and the version the player reports decides the revision (older builds are
Type B). Other software keeps the Type dropdown, and only on models with more
than one revision. Cancel exists only for a second player, and Done / Add
another player appear once the first player is on record.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QThread, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui import settings_page as settings_mod  # noqa: E402
from src.ui.main_window import MainWindow, _PAGE_SETTINGS  # noqa: E402

_APP = None


def _window():
    global _APP
    app = QApplication.instance() or QApplication(sys.argv)
    _APP = app
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    for _ in range(8):
        app.processEvents()
    win._nav_to_page(_PAGE_SETTINGS)
    for _ in range(8):
        app.processEvents()
    return app, win


def _close(win):
    win._force_quit = True
    win.close()
    loader = getattr(win, "_icon_loader", None)
    try:
        running = loader is not None and loader.isRunning()
    except RuntimeError:
        running = False
    if running:
        loader.wait(2000)


def _events(app):
    for _ in range(6):
        app.processEvents()


class _RecordingWorker(QThread):
    """Never fetches: records which revision the form asked for."""

    loaded = Signal(object)

    def __init__(self, package, model, selected_type):
        super().__init__()
        self.selected_type = selected_type
        workers.append(self)

    def run(self):
        return


workers = []


def test_original_software_merges_versions_and_hides_type(monkeypatch):
    app, win = _window()
    page = win._settings_page
    workers.clear()
    monkeypatch.setattr(settings_mod, "_AddDeviceReleasesWorker", _RecordingWorker)
    try:
        page._open_add_device_panel()
        page._add_device_model.setCurrentIndex(page._add_device_model.findText("Y1"))
        index = page._add_device_software.findText("Original Software")
        assert index >= 0
        page._add_device_software.setCurrentIndex(index)
        _events(app)

        assert page._add_device_type.isHidden()
        assert page._add_device_type_label.isHidden()
        # One request for both revisions, so their versions share the list.
        assert workers and any(w.selected_type is None for w in workers)
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_other_software_keeps_the_type_question(monkeypatch):
    app, win = _window()
    page = win._settings_page
    workers.clear()
    monkeypatch.setattr(settings_mod, "_AddDeviceReleasesWorker", _RecordingWorker)
    try:
        page._open_add_device_panel()
        page._add_device_model.setCurrentIndex(page._add_device_model.findText("Y1"))
        page._add_device_software.clear()
        page._add_device_software.addItem("Rockbox")
        page._add_device_software.setCurrentIndex(0)
        _events(app)

        assert not page._add_device_type.isHidden()
        assert workers and workers[-1].selected_type == "A"
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_the_original_software_version_names_the_revision():
    from src.ui.settings_page import original_software_hardware_type

    assert original_software_hardware_type("1.8.7") == "B"
    assert original_software_hardware_type("1.9.0") == "B"
    assert original_software_hardware_type("1.9.7") == "A"
    assert original_software_hardware_type("3.1.2") == "A"
    assert original_software_hardware_type("") == ""
    assert original_software_hardware_type("not known") == ""


def test_saving_original_software_records_the_revision_the_version_names(monkeypatch):
    app, win = _window()
    page = win._settings_page
    recorded = {}
    try:
        monkeypatch.setattr(
            settings_mod.device_tracking,
            "record_device_install",
            lambda **kwargs: recorded.update(kwargs),
        )
        page._open_add_device_panel()
        page._add_device_model.setCurrentIndex(page._add_device_model.findText("Y1"))
        page._add_device_software.clear()
        page._add_device_software.addItem("Original Software")
        page._add_device_software.setCurrentIndex(0)
        page._add_device_package = lambda: SimpleNamespace(
            name="Original Software", slug="original-y1"
        )
        page._add_device_version.clear()
        page._add_device_version.addItem("1.8.7", {"tag_name": "1.8.7"})
        page._save_add_device()
        _events(app)

        assert recorded.get("hardware_type") == "B"
        assert recorded.get("model") == "Y1"
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_cancel_appears_only_for_a_second_player(monkeypatch):
    app, win = _window()
    page = win._settings_page
    installs = {}
    try:
        monkeypatch.setattr(
            settings_mod.device_tracking,
            "get_all_device_installs",
            lambda *a, **k: installs,
        )
        page._open_add_device_panel()
        _events(app)
        assert page._add_device_cancel.isHidden()

        page._hide_add_device_panel()
        installs["Y2"] = {"model": "Y2", "software_name": "Original Software"}
        page._open_add_device_panel()
        _events(app)
        assert not page._add_device_cancel.isHidden()
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_done_and_add_another_appear_after_the_first_player(monkeypatch):
    app, win = _window()
    page = win._settings_page
    installs = {}
    try:
        monkeypatch.setattr(
            settings_mod.device_tracking,
            "get_all_device_installs",
            lambda *a, **k: installs,
        )
        assert page.begin_onboarding()
        _events(app)
        assert page._onboarding_actions.isHidden()
        assert page._onboarding_more.isHidden()

        installs["Y1"] = {"model": "Y1", "software_name": "Original Software"}
        page.refresh_settings()
        _events(app)
        assert not page._onboarding_actions.isHidden()
        assert not page._onboarding_done.isHidden()
        assert not page._onboarding_more.isHidden()
    finally:
        page.end_onboarding()
        _close(win)
