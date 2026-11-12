"""Install Firmware First on the window side, without downloading firmware.

The card is the Keep Data renderer, and the Keep Data overrides are the part
that decides whether a Reborn-over-Reborn run can carry its data. Both are
checked here against the real catalogue entries, with no worker or player.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src import catalog, install_first as first, keep_data, manifest, player_update_check
from src.i18n import tr
from src.ui.flash_page import FlashPage
from src.ui.main_window import MainWindow

# The prerequisite declarations live in the shipped manifest, not in the
# static fallback table, so the GUI tests use the real catalogue entries.
ITEMS = manifest.parse_manifest_xml(
    Path("assets/online/slidia_manifest.xml").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def catalogue(monkeypatch):
    monkeypatch.setattr(
        catalog,
        "packages_for_model",
        lambda model, brand="": [p for p in ITEMS if p.model == model],
    )
    return ITEMS


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Keep every tracking read/write in a throwaway store.

    The window passes ``settings=None`` in these tests, which would otherwise
    read the developer's real install history and change which question the
    first prompt asks.
    """
    from PySide6.QtCore import QSettings

    from src import player_update_check

    store = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    monkeypatch.setattr(
        player_update_check,
        "_get_settings",
        lambda settings=None: settings if settings is not None else store,
    )
    return store


@pytest.fixture()
def page(qapp):
    widget = FlashPage()
    try:
        yield widget
    finally:
        widget.deleteLater()
        qapp.processEvents()


class FakeSelect:
    def __init__(self, base):
        self.base = base
        self.started = []
        self.fetched = []
        self.local_started = []

    def current_hardware_type(self):
        return "A"

    @property
    def client(self):
        return self

    def releases_for_package(self, package, model, **kwargs):
        self.fetched.append((package.name, model))
        return [
            {
                "tag_name": "stable-v3.2.0",
                "name": "Original Software 3.2.0",
                "asset_name": "rom_y2.zip",
                "download_url": "https://example.invalid/rom_y2.zip",
                "published_at": "2026-01-01T00:00:00Z",
            }
        ]

    def start_release_install(self, rel, package, prompt=False):
        self.started.append((rel, package, prompt))

    def start_local_install(self, package, *, prompt=False, install_path=None):
        self.local_started.append((package, install_path, prompt))


class Harness:
    _install_first_model = MainWindow._install_first_model
    _install_first_label = MainWindow._install_first_label
    _install_first_selected_answer = MainWindow._install_first_selected_answer
    _install_first_already_on_target = MainWindow._install_first_already_on_target
    _install_first_forces_no_keep_data = MainWindow._install_first_forces_no_keep_data
    _keep_data_default_for_install = MainWindow._keep_data_default_for_install
    _guard_install_first = MainWindow._guard_install_first
    _guard_local_install_first = MainWindow._guard_local_install_first
    _local_fw_package_for_path = MainWindow._local_fw_package_for_path
    _ask_install_first_software = MainWindow._ask_install_first_software
    _install_first_hardware_type = MainWindow._install_first_hardware_type
    _install_first_known_answer = MainWindow._install_first_known_answer
    _same_software_name = staticmethod(MainWindow._same_software_name)
    _on_install_first_running_no = MainWindow._on_install_first_running_no
    _answer_install_first_running = MainWindow._answer_install_first_running
    _remember_install_first_answer = MainWindow._remember_install_first_answer
    _begin_keep_data_confirmation = MainWindow._begin_keep_data_confirmation
    _on_keep_data_not_compatible = MainWindow._on_keep_data_not_compatible
    _keep_data_tracking_context = MainWindow._keep_data_tracking_context
    _remember_keep_data_answer = MainWindow._remember_keep_data_answer
    _forget_keep_data_software = MainWindow._forget_keep_data_software
    _record_completed_install = MainWindow._record_completed_install
    _on_install_first_continue = MainWindow._on_install_first_continue
    _ask_install_first_version = MainWindow._ask_install_first_version
    _on_install_first_version_no = MainWindow._on_install_first_version_no
    _confirm_install_first_prerequisite = MainWindow._confirm_install_first_prerequisite
    _proceed_with_install_first_target = MainWindow._proceed_with_install_first_target
    _install_first_filter_rows = MainWindow._install_first_filter_rows
    _start_install_first_prerequisite = MainWindow._start_install_first_prerequisite
    _finish_install_first_prerequisite = MainWindow._finish_install_first_prerequisite
    _cancel_install_first = MainWindow._cancel_install_first
    _install_first_record_version = lambda self: ""
    # This harness drives the ordinary non-MTK hand-off; the MTKClient
    # power-on prompt is a separate path and not part of these assertions.
    _install_first_used_mtk = lambda self: False

    def _nav_to_page(self, page):
        self.navigated.append(page)

    def _sync_install_nav_entry(self):
        pass

    def _adjust_window_geometry(self):
        pass

    def _show_status(self, text, timeout=0):
        self.statuses.append(text)

    def _append_log(self, text):
        self.logs.append(text)

    def _return_to_select_software(self):
        self.returned = True


def make_window(page, select):
    window = Harness()
    window._flash_page = page
    window._select_page = select
    window._package_model = "Y2"
    window._package_name = ""
    window._flash_method = "mtk"
    window.settings = None
    window._install_first_journey = None
    window._install_first_stage = ""
    window._install_first_after = None
    window._install_first_prerequisite_run = False
    window._install_first_power_off_only = False
    window._bundled_fw_package = None
    window._bundled_package_path = ""
    window._keep_data = False
    window.navigated = []
    window.statuses = []
    window.logs = []
    window.returned = False
    return window


TARGET_REL = {
    "tag_name": "stable-v1.0",
    "name": "Reborn 1.0",
    "asset_name": "rom_y2.zip",
    "download_url": "https://example.invalid/reborn.zip",
}


def reborn_and_stock():
    items = catalog.packages_for_model("Y2")
    target = next(p for p in items if p.name.startswith("Reborn"))
    stock = next(p for p in items if p.name == "Original Software")
    requirement = first.requirement_for(target, "Y2", packages=items)
    assert requirement is not None and requirement.package is stock
    return target, stock, requirement, items


def journey_for(target, requirement, answer):
    journey = first.InstallFirstJourney(target=target, requirement=requirement)
    journey.choose(answer, "")
    return journey


def test_prompt_offers_the_target_the_base_and_other(page, qapp):
    answered = []
    page.show_install_first_prompt(
        "Which software is currently running on your Y2?",
        "This firmware is built on Original Software.",
        ("Reborn (Y2Linux)", "Original Software 3.2.0+", "Other"),
        selected="Reborn (Y2Linux)",
        on_no=lambda: answered.append(True),
    )

    assert page._keep_data_confirm_active
    assert page._wait_copy == "keep_data_confirm"
    assert not page._keep_data_choice.isHidden()
    assert page._keep_data_choice.count() == 3
    assert page._keep_data_choice.currentText() == "Reborn (Y2Linux)"
    assert page._wait_continue_btn.toolTip() == tr("btn_next")
    assert page._wait_cancel_btn.toolTip() == tr("btn_cancel")

    page._wait_cancel_btn.click()
    qapp.processEvents()
    assert answered == [True]


def test_selected_answer_maps_back_to_the_journey(page):
    target, _stock, requirement, _items = reborn_and_stock()
    window = Harness()
    window._flash_page = page
    window._install_first_journey = journey_for(target, requirement, first.ANSWER_PREREQUISITE)
    page.show_install_first_prompt(
        "Which software is currently running?",
        "",
        (target.name, requirement.label(), tr("keep_data_choice_other")),
        selected=requirement.label(),
    )
    assert window._install_first_selected_answer() == first.ANSWER_PREREQUISITE

    page._keep_data_choice.setCurrentText(target.name)
    assert window._install_first_selected_answer() == first.ANSWER_TARGET

    page._keep_data_choice.setCurrentText(tr("keep_data_choice_other"))
    assert window._install_first_selected_answer() == first.ANSWER_OTHER


def test_already_on_target_allows_keep_data_despite_the_manifest(page):
    target, _stock, requirement, _items = reborn_and_stock()
    window = Harness()
    window._flash_page = page
    window._install_first_journey = journey_for(target, requirement, first.ANSWER_TARGET)
    window._install_first_prerequisite_run = False
    window._package_model = "Y2"
    assert window._keep_data_default_for_install() is True
    assert window._install_first_forces_no_keep_data() is False


def test_other_blocks_keep_data_when_the_base_is_not_in_the_family(page):
    target, _stock, requirement, _items = reborn_and_stock()
    window = Harness()
    window._flash_page = page
    window._install_first_journey = journey_for(target, requirement, first.ANSWER_OTHER)
    window._install_first_prerequisite_run = False
    window._package_model = "Y2"
    assert window._install_first_forces_no_keep_data() is True
    assert window._keep_data_default_for_install() is False


def test_guard_pauses_before_the_download_and_target_proceeds(page):
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "software"
    assert page._wait_copy == "keep_data_confirm"
    assert page._keep_data_choice.count() == 3
    assert select.started == []
    assert window.navigated

    page._keep_data_choice.setCurrentText(target.name)
    window._on_install_first_continue()
    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_journey.answer == first.ANSWER_TARGET


def test_guard_prerequisite_with_unknown_version_asks_then_target(page):
    target, _stock, requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    page._keep_data_choice.setCurrentText(requirement.label())
    window._on_install_first_continue()
    assert window._install_first_stage == "version"
    assert select.started == []

    window._on_install_first_continue()  # yes, the base is new enough
    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_journey.answer == first.ANSWER_PREREQUISITE


def test_guard_other_confirms_and_starts_the_base_then_the_target(page):
    target, stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=stock)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    page._keep_data_choice.setCurrentText(tr("keep_data_choice_other"))
    window._on_install_first_continue()
    assert window._install_first_stage == "confirm"
    assert select.started == []

    window._on_install_first_continue()
    assert select.started and select.started[0][1] is stock
    assert select.started[0][2] is False
    assert window._install_first_prerequisite_run is True
    assert window._install_first_after == (target, TARGET_REL)
    assert window._install_first_journey.answer == first.ANSWER_OTHER

    # The base finished: the target starts by itself, with the same answer.
    window._finish_install_first_prerequisite()
    assert window._install_first_prerequisite_run is False
    assert select.started[-1] == (TARGET_REL, target, False)
    assert window._install_first_journey.answer == first.ANSWER_OTHER


def test_derivative_family_keeps_the_ordinary_rules(page):
    items = catalog.packages_for_model("Y1")
    target = next(p for p in items if p.name == "Koensayr")
    stock = next(p for p in items if p.name == "Original Software")
    requirement = first.Requirement(
        repo=stock.repo,
        version_text="",
        version_range=None,
        package=stock,
        model="Y1",
    )
    window = Harness()
    window._flash_page = page
    window._install_first_journey = journey_for(target, requirement, first.ANSWER_PREREQUISITE)
    window._install_first_prerequisite_run = False
    window._package_model = "Y1"
    assert window._install_first_forces_no_keep_data() is False


def test_prerequisite_filter_does_not_fall_back_for_an_exact_version():
    requirement = first.Requirement(
        repo="y1-community/y1-stock-rom",
        version_text="=3.2.0",
        version_range=first.parse_version_constraint("=3.2.0"),
        package=None,
        model="Y2",
    )
    rows = [
        {
            "tag_name": "stable-v3.2.1",
            "name": "Original Software 3.2.1",
            "published_at": "2026-01-01T00:00:00Z",
        },
        {
            "tag_name": "stable-v3.2.0",
            "name": "Original Software 3.2.0",
            "published_at": "2025-12-01T00:00:00Z",
        },
    ]
    filtered = Harness()._install_first_filter_rows(rows, requirement)
    assert [row["tag_name"] for row in filtered] == ["stable-v3.2.0"]


def test_prerequisite_with_releases_but_no_matching_version_reports_it(page):
    target, stock, _requirement, _items = reborn_and_stock()
    requirement = first.Requirement(
        repo=stock.repo,
        version_text="=3.2.0",
        version_range=first.parse_version_constraint("=3.2.0"),
        package=stock,
        model="Y2",
    )
    select = FakeSelect(base=stock)

    def newer_only(package, model, **kwargs):
        return [
            {
                "tag_name": "stable-v3.2.1",
                "name": "Original Software 3.2.1",
                "asset_name": "rom_y2.zip",
                "download_url": "https://example.invalid/rom_y2.zip",
                "published_at": "2026-01-01T00:00:00Z",
            }
        ]

    select.releases_for_package = newer_only
    window = make_window(page, select)
    window._install_first_journey = first.InstallFirstJourney(
        target=target, target_rel=TARGET_REL, requirement=requirement
    )

    window._start_install_first_prerequisite()

    assert window.returned is True
    assert select.started == []
    assert window.statuses[-1] == tr("install_first_version_unavailable").format(
        software="Original Software", version="=3.2.0"
    )


def test_prerequisite_with_no_catalogue_releases_keeps_the_missing_message(page):
    target, stock, _requirement, _items = reborn_and_stock()
    requirement = first.Requirement(
        repo=stock.repo,
        version_text="=3.2.0",
        version_range=first.parse_version_constraint("=3.2.0"),
        package=stock,
        model="Y2",
    )
    select = FakeSelect(base=stock)
    select.releases_for_package = lambda package, model, **kwargs: []
    window = make_window(page, select)
    window._install_first_journey = first.InstallFirstJourney(
        target=target, target_rel=TARGET_REL, requirement=requirement
    )

    window._start_install_first_prerequisite()

    assert window.returned is True
    assert select.started == []
    assert window.statuses[-1] == tr("install_first_missing").format(
        software="Original Software"
    )


def _local_package(folder, name, version, repo, install_first="", version_text="", base=None):
    from src.fw_package import FwPackage

    return FwPackage(
        folder=Path(folder),
        scatter=Path(folder) / "MT6582_Android_scatter.txt",
        name=name,
        version=version,
        model="Y2",
        repo=repo,
        install_first=install_first,
        install_first_version=version_text,
        install_first_package=base,
    )


def test_local_portable_pre_stage_is_flashed_without_downloading(page, tmp_path):
    base = _local_package(
        tmp_path / "pre_stage",
        "Original Software",
        "3.2.0",
        "y1-community/y1-stock-rom",
    )
    target = _local_package(
        tmp_path / "SP_Flash_Tool",
        "Reborn",
        "1.0",
        "someone/reborn",
        install_first="y1-community/y1-stock-rom",
        version_text="3.2.0=>",
        base=base,
    )
    select = FakeSelect(base=None)
    select._fw_package = target
    select._current_installed_release_info = None
    window = make_window(page, select)

    assert window._guard_local_install_first(str(target.folder)) is False
    assert window._install_first_journey is not None
    assert window._install_first_journey.local_prerequisite is base
    assert page._keep_data_choice.count() == 3

    page._keep_data_choice.setCurrentText(tr("keep_data_choice_other"))
    window._on_install_first_continue()
    assert window._install_first_stage == "confirm"
    assert select.local_started == []

    window._on_install_first_continue()
    assert select.fetched == []
    assert select.local_started == [(base, None, False)]
    assert window._install_first_prerequisite_run is True

    window._finish_install_first_prerequisite()
    assert select.started[-1][1] is not None
    assert select.started[-1][0]["local_package"] is target
    assert select.started[-1][0]["local_path"] == str(target.folder)


def test_local_portable_without_pre_stage_downloads_the_base(page, tmp_path):
    target = _local_package(
        tmp_path / "SP_Flash_Tool",
        "Reborn",
        "1.0",
        "someone/reborn",
        install_first="y1-community/y1-stock-rom",
        version_text="3.2.0=>",
    )
    select = FakeSelect(base=None)
    select._fw_package = target
    select._current_installed_release_info = None
    window = make_window(page, select)

    assert window._guard_local_install_first(str(target.folder)) is False
    page._keep_data_choice.setCurrentText(tr("keep_data_choice_other"))
    window._on_install_first_continue()
    window._on_install_first_continue()

    assert select.fetched
    assert select.started and select.started[0][0]["download_url"].startswith("https://")
    assert window._install_first_prerequisite_run is True


def test_the_menu_becomes_a_confirmation_when_the_base_is_tracked(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    keep_data.note_running_software("Y2", "Original Software", window.settings, "A")

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "running"
    assert page._keep_data_choice.isHidden()
    assert window._install_first_running_answer == first.ANSWER_PREREQUISITE
    assert select.started == []
    assert window.navigated


def test_confirming_the_tracked_base_skips_its_install(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    keep_data.note_running_software("Y2", "Original Software", window.settings, "A")

    assert window._guard_install_first(target, TARGET_REL) is False
    window._on_install_first_continue()

    # Straight to Reborn: no base download, and nothing to keep for it.
    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_prerequisite_run is False
    assert window._install_first_journey.answer == first.ANSWER_PREREQUISITE
    assert window._install_first_journey.keep_data_allowed() is False
    assert window._install_first_stage == ""


def test_confirming_the_tracked_target_offers_keep_data(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    keep_data.note_running_software("Y2", target.name, window.settings, "A")

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_running_answer == first.ANSWER_TARGET
    window._on_install_first_continue()

    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_journey.answer == first.ANSWER_TARGET
    assert window._install_first_journey.keep_data_allowed() is True


def test_denying_the_confirmation_returns_to_the_full_menu(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    window = make_window(page, FakeSelect(base=None))
    keep_data.note_running_software("Y2", "Original Software", window.settings, "A")

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "running"
    window._on_install_first_running_no()

    assert window._install_first_stage == "software"
    assert page._keep_data_choice.count() == 3
    assert keep_data.known_running_software("Y2", window.settings, "A") == {}


def test_the_answer_is_stored_without_a_version(isolated_settings):
    keep_data.note_running_software("Y2", "Reborn (Y2Linux)", isolated_settings, "A")
    known = keep_data.known_running_software("Y2", isolated_settings, "A")
    assert known["software_name"] == "Reborn (Y2Linux)"
    assert known["tag_name"] == ""
    assert known["confirmed"] is True

    keep_data.forget_running_software("Y2", isolated_settings, "A")
    assert keep_data.known_running_software("Y2", isolated_settings, "A") == {}


def test_keep_data_sanity_check_confirms_the_tracked_software(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    names = keep_data.compatible_software_names(target, "Y2")
    assert names
    window = make_window(page, FakeSelect(base=None))
    window._keep_data_pkg = target
    keep_data.note_running_software("Y2", names[-1], window.settings, "A")

    window._begin_keep_data_confirmation()

    # One name the player already runs: a yes/no, not a drop-down.
    assert page._keep_data_choice.isHidden()
    assert window._keep_data_confirm_known_name == names[-1]
    assert window._keep_data_confirm_pending is True


def test_answering_keep_data_tracks_the_chosen_software(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    window = make_window(page, FakeSelect(base=None))
    window._keep_data_pkg = target

    window._remember_keep_data_answer("Original Software 3.2.0+")

    known = keep_data.known_running_software("Y2", window.settings, "A")
    assert known["software_name"] == "Original Software 3.2.0+"
    assert known["tag_name"] == ""


def test_the_base_install_is_logged_as_the_models_firmware(page, isolated_settings, monkeypatch):
    from src import notice_service

    class _NoNotices:
        @classmethod
        def instance(cls):
            raise RuntimeError("no notice service in tests")

    monkeypatch.setattr(notice_service, "NoticeService", _NoNotices)
    target, _stock, _requirement, _items = reborn_and_stock()

    def base_release():
        return {
            "model": "Y2",
            "software_name": "Original Software",
            "tag_name": "stable-v3.2.0",
            "release_label": "Original Software 3.2.0",
            "package_slug": "y1-community/y1-stock-rom",
            "hardware_type": "A",
        }

    select = FakeSelect(base=None)
    select.current_installed_release_info = base_release
    window = make_window(page, select)
    window._install_first_prerequisite_run = True
    window._install_first_after = (target, TARGET_REL)
    window._install_first_journey = journey_for(target, _requirement, first.ANSWER_OTHER)

    # Logged before the run continues, so stopping here still resumes later.
    recorded = []
    window._finish_install_first_prerequisite = lambda: recorded.append(True)
    window._record_completed_install()

    assert recorded == []

    record = player_update_check.get_device_install("Y2", hardware_type="A", settings=window.settings)
    assert record is not None
    assert record["software_name"] == "Original Software"
    assert record["tag_name"] == "stable-v3.2.0"
    known = keep_data.known_running_software("Y2", window.settings, "A")
    assert known["software_name"] == "Original Software"
