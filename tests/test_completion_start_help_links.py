"""Regression tests for the device-not-starting advice links on the completion screen."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem


@pytest.fixture
def completion_window(qapp, monkeypatch):
    """Build a MainWindow with network workers stubbed out and clean teardown."""
    from src import catalog, manifest
    from src.ui.main_window import MainWindow
    from src.ui.select_page import SelectPackagePage

    catalog.LIVE_CATALOG = manifest.load_snapshot_manifest()
    refreshed: list[bool] = []
    monkeypatch.setattr(
        SelectPackagePage,
        "_refresh_releases",
        lambda self, *a, **kw: refreshed.append(True),
    )
    monkeypatch.setattr(SelectPackagePage, "_refresh_manifest", lambda self, *a, **kw: None)

    win = MainWindow()
    win._test_refreshed = refreshed
    try:
        yield win
    finally:
        for worker in list(getattr(win._select_page, "_active_workers", ())):
            try:
                worker.requestInterruption()
                worker.wait(500)
            except Exception:
                pass
        win.close()
        qapp.processEvents()


def test_start_help_advice_label_link_activated_emits_signals(qapp):
    """Both completion appeal and simple completion advice labels emit on link click."""
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        emitted: list[str] = []
        page.install_original_requested.connect(lambda: emitted.append("original"))
        page.browse_software_requested.connect(lambda: emitted.append("browse"))

        # 1. Donations / completion appeal screen advice label
        page.set_model("Y1")
        page.set_device_done(donations_shown=True)
        page.show_completion_appeal(True)
        page._appeal_power_on.linkActivated.emit("start-help")
        assert not page._start_help_body.isHidden()

        assert 'href="install-original"' in page._start_help_advice.text()
        assert 'href="other-software"' in page._start_help_advice.text()
        page._start_help_advice.linkActivated.emit("install-original")
        page._start_help_advice.linkActivated.emit("other-software")
        assert emitted == ["original", "browse"]

        # 2. Simple completion note screen (donations off) advice label
        emitted.clear()
        page.set_device_done(donations_shown=False)
        page.show_completion_note(False, software="Rockbox", model="Y2")
        assert page._completion_advice_label is not None
        page._completion_advice_label.linkActivated.emit("install-original")
        page._completion_advice_label.linkActivated.emit("other-software")
        assert emitted == ["original", "browse"]
    finally:
        page.close()


def test_completion_install_original_triggers_latest_for_model_and_type(completion_window, monkeypatch):
    """Clicking 'Install Original Software' selects the model+type and installs latest stock."""
    win = completion_window
    sp = win._select_page

    triggered: list[tuple[dict, object]] = []
    monkeypatch.setattr(
        sp,
        "_trigger_release_install",
        lambda rel, package=None, **kw: triggered.append((rel, package)),
    )

    # Simulate user just finished installing a Y1 Type B package while SelectPage is on Y2/local
    sp.select_device("Y2")
    sp._show_local_view()
    win._package_model = "Y1"
    win._package_name = "Classy for Y1"
    win._package_path = "/tmp/rom_y1_type_b.zip"
    sp._current_installed_release_info = {
        "model": "Y1",
        "hardware_type": "B",
        "software_name": "Classy",
    }

    # Click "Install Original Software" directly on the completion appeal advice label
    win._flash_page._start_help_advice.linkActivated.emit("install-original")

    assert sp.current_brand() == "Innioasis"
    assert sp.current_model() == "Y1"
    assert sp.current_hardware_type() == "B"
    assert sp.current_software() == "Original Software"
    assert sp._auto_install_tag == "latest"
    assert sp._keep_settings_default is True

    # Simulate ReleasesWorker returning unsorted releases -> newest downloadable installs!
    older = {
        "tag_name": "type-b-1.5.0",
        "published_at": "2025-01-01T00:00:00Z",
        "download_url": "https://example.test/old_type_b.zip",
        "asset_name": "rom_type_b.zip",
    }
    newest = {
        "tag_name": "type-b-1.7.6",
        "published_at": "2026-01-01T00:00:00Z",
        "download_url": "https://example.test/new_type_b.zip",
        "asset_name": "rom_type_b.zip",
    }
    sp._on_releases_loaded([older, newest], None)

    assert len(triggered) == 1
    assert triggered[0][0]["tag_name"] == "type-b-1.7.6"


def test_completion_install_original_immediately_installs_when_already_loaded(completion_window, monkeypatch):
    """When Original Software releases for that model are already loaded, install starts at once."""
    win = completion_window
    sp = win._select_page

    triggered: list[dict] = []
    monkeypatch.setattr(
        sp,
        "_trigger_release_install",
        lambda rel, package=None, **kw: triggered.append(rel),
    )

    # Already viewing Y2 -> Original Software with releases loaded
    sp.navigate_to_package("Y2", "Original Software")
    latest_y2 = {
        "tag_name": "3.2.4-fm",
        "published_at": "2026-03-01T00:00:00Z",
        "download_url": "https://example.test/rom_y2.zip",
        "asset_name": "rom_y2.zip",
    }
    sp._on_releases_loaded([latest_y2], None)

    win._package_model = "Y2"
    win._package_name = "Reborn for Y2"
    sp._current_installed_release_info = {"model": "Y2", "software_name": "Reborn"}

    win._flash_page._start_help_advice.linkActivated.emit("install-original")

    assert len(triggered) == 1
    assert triggered[0]["tag_name"] == "3.2.4-fm"


def test_completion_install_original_refreshes_if_list_only_has_loading_placeholder(completion_window):
    """A stale 'Loading...' placeholder in _release_list must not block refreshing & installing."""
    win = completion_window
    sp = win._select_page

    sp.navigate_to_package("Y2", "Original Software")
    sp._release_list.clear()
    placeholder = QListWidgetItem("Loading…")
    placeholder.setFlags(placeholder.flags() & ~Qt.ItemIsEnabled)
    sp._release_list.addItem(placeholder)
    sp._loaded_selection_key = None
    win._test_refreshed.clear()

    win._package_model = "Y2"
    sp._current_installed_release_info = {"model": "Y2", "software_name": "Original Software"}

    win._flash_page._start_help_advice.linkActivated.emit("install-original")

    assert win._test_refreshed == [True]
    assert sp._auto_install_tag == "latest"


def test_completion_another_software_selects_model_and_type(completion_window):
    """Clicking 'another software' opens Select Software with brand, model, and type selected."""
    win = completion_window
    sp = win._select_page

    # Start from a completely different selection, local tab, and collapsed filters
    sp.select_device("Y2")
    sp._show_local_view()
    sp._filters_collapsed = True
    sp._apply_filter_collapsed_state()

    win._package_model = "Y1"
    win._package_name = "Classy for Y1"
    win._package_path = "/tmp/rom_y1_type_b.zip"
    sp._current_installed_release_info = {
        "model": "Y1",
        "hardware_type": "B",
        "software_name": "Classy",
    }

    win._flash_page._start_help_advice.linkActivated.emit("other-software")

    assert sp.current_brand() == "Innioasis"
    assert sp.current_model() == "Y1"
    assert sp.current_hardware_type() == "B"
    assert sp._filters_collapsed is False
    assert sp._views.currentWidget() is sp._online_tab
    assert sp._manual_local is False

    # Also verify cross-brand (Timmkoo Q5)
    win._package_model = "Q5"
    win._package_name = "Lithium for Q5"
    win._package_path = ""
    sp._current_installed_release_info = {"model": "Q5", "software_name": "Lithium (Android 7)"}

    win._flash_page._start_help_advice.linkActivated.emit("other-software")

    assert sp.current_brand() == "Timmkoo"
    assert sp.current_model() == "Q5"
    assert sp.current_hardware_type() == ""
