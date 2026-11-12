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

from src import catalog, install_first as first, manifest
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
