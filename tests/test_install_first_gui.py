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

    def start_release_install(self, rel, package, prompt=False, guard=False):
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
    _install_first_gate_awaits_answer = MainWindow._install_first_gate_awaits_answer
    _manifest_fresh_package = MainWindow._manifest_fresh_package
    _apply_release_icon = MainWindow._apply_release_icon
    _install_first_hardware_type = MainWindow._install_first_hardware_type
    _same_software_name = staticmethod(MainWindow._same_software_name)
    _remember_install_first_answer = MainWindow._remember_install_first_answer
    _confirm_install_first_recommendation = (
        MainWindow._confirm_install_first_recommendation
    )
    _decline_install_first_prerequisite = (
        MainWindow._decline_install_first_prerequisite
    )
    _begin_keep_data_confirmation = MainWindow._begin_keep_data_confirmation
    _begin_keep_data_erase_confirmation = (
        MainWindow._begin_keep_data_erase_confirmation
    )
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
    _on_power_off_continue = MainWindow._on_power_off_continue
    _install_first_record_version = lambda self: ""
    # This harness drives the ordinary non-MTK hand-off; the MTKClient
    # power-on prompt is a separate path and not part of these assertions.
    _install_first_used_mtk = lambda self: False

    def _install_first_running_name(self) -> str:
        # The harness has no tracking store of its own, so in these tests only
        # the journey's answer can name the system the base replaces.
        journey = getattr(self, "_install_first_journey", None)
        return str(getattr(journey, "running_name", "") or "")

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
    window._install_first_choices = {}
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
    assert page._keep_data_choice.count() > 3
    assert select.started == []
    assert window.navigated

    page._keep_data_choice.setCurrentText(target.name)
    window._on_install_first_continue()
    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_journey.answer == first.ANSWER_TARGET


def test_the_firmware_menu_is_asked_before_any_keep_data_question(page, monkeypatch):
    """Continue asks which firmware runs, whatever Keep Data is set to."""
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    window._keep_data_pkg = target
    # The "Keep Data on any firmware" override must not reduce this question to
    # the one-firmware yes/no: the answer decides whether the base has to be
    # installed at all, so it is not a Keep Data question.
    monkeypatch.setattr(keep_data, "allow_keep_data_any", lambda settings=None: True)

    window._on_power_off_continue()

    assert window._install_first_stage == "software"
    assert page._keep_data_choice.count() > 3
    assert select.started == []


def test_an_answer_for_one_firmware_does_not_settle_the_next(page):
    """A recorded answer belongs to its own firmware, not to every install."""
    target, _stock, requirement, _items = reborn_and_stock()
    rockbox = next(p for p in catalog.packages_for_model("Y2") if p.name == "Rockbox")
    window = make_window(page, FakeSelect(base=None))
    window._install_first_journey = journey_for(rockbox, requirement, first.ANSWER_FIRMWARE)

    assert window._install_first_gate_awaits_answer(target) is True
    assert window._install_first_stage == "software"
    assert page._keep_data_choice.count() > 3


def test_the_window_initiated_install_asks_the_menu(qapp):
    """Install Firmware First guards; the journey's continuations do not."""
    from src.ui.select_page import SelectPackagePage

    calls = []

    class Recorder:
        def _trigger_release_install(self, rel, package=None, *, prompt=True, guard=True):
            calls.append((rel, package, prompt, guard))

    rel = {"download_url": "https://example.invalid/rom_y2.zip"}
    SelectPackagePage.start_release_install(Recorder(), rel, None)
    SelectPackagePage.start_release_install(Recorder(), rel, None, guard=False)
    assert [entry[3] for entry in calls] == [True, False]


def test_install_first_once_still_asks_a_player_running_the_base(page, monkeypatch):
    """install_first_once spares the base install, never the question."""
    from dataclasses import replace

    from src import keep_data

    target, stock, _requirement, items = reborn_and_stock()
    monkeypatch.setattr(
        keep_data,
        "known_running_software",
        lambda model, settings=None, hardware_type="": {"software_name": stock.name},
    )
    # The attribute lives in the manifest, so the catalogue row carries it: the
    # gate reads that row rather than a modified copy handed to it.
    once_items = [
        replace(p, install_first_once=True) if p.repo == target.repo else p
        for p in items
    ]
    monkeypatch.setattr(
        catalog,
        "packages_for_model",
        lambda model, brand="": [p for p in once_items if p.model == model],
    )
    select = FakeSelect(base=None)
    window = make_window(page, select)

    # Original Software is not named by install_first_when, and the tracked
    # software answers what runs, so the question goes straight to checking
    # whether that base version is new enough.
    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "version"
    assert select.started == []


def test_install_first_once_asks_an_untracked_player(page, monkeypatch):
    """The published Reborn row carries install_first_once: still ask."""
    from dataclasses import replace

    target, _stock, requirement, items = reborn_and_stock()
    once_items = [
        replace(p, install_first_once=True) if p.repo == target.repo else p
        for p in items
    ]
    monkeypatch.setattr(
        catalog,
        "packages_for_model",
        lambda model, brand="": [p for p in once_items if p.model == model],
    )
    select = FakeSelect(base=None)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "software"
    # Nothing is tracked, so the menu opens on the firmware that comes first:
    # the run must enforce the base rather than assume it is already there.
    assert page._keep_data_choice.currentText() == requirement.label()
    assert select.started == []


def test_the_same_player_is_still_asked_without_install_first_once(page, monkeypatch):
    """The gate keeps its 2.5.5 behaviour for an entry that does not opt out."""
    from src import keep_data

    target, stock, _requirement, _items = reborn_and_stock()
    monkeypatch.setattr(
        keep_data,
        "known_running_software",
        lambda model, settings=None, hardware_type="": {"software_name": stock.name},
    )
    select = FakeSelect(base=None)
    window = make_window(page, select)

    # Without the attribute the tracked player's running software answers the
    # system; the question checks whether the base version is new enough.
    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "version"


def test_untracked_player_opens_on_the_firmware_that_comes_first(page):
    """Nothing tracked must not preselect the overlay: the tick installs the base."""
    target, _stock, requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "software"
    # The app knows nothing about this player, so "Reborn" is an answer it
    # cannot support: the menu opens on the firmware the catalogue requires.
    assert page._keep_data_choice.currentText() == requirement.label()
    assert window._install_first_selected_answer() == first.ANSWER_PREREQUISITE

    # Pressing the tick asks about the base's version rather than flashing the
    # overlay over a player nobody has looked at.
    window._on_install_first_continue()
    assert window._install_first_stage == "version"
    assert select.started == []


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
    # No install_first_when on this package: the base, the target and "Other"
    # are the whole menu, and the local pre_stage base is the first row.
    assert page._keep_data_choice.count() == 3
    assert page._keep_data_choice.itemText(0).startswith(base.name)
    assert page._keep_data_choice.itemText(1) == target.name
    assert page._keep_data_choice.itemText(2) == tr("keep_data_choice_other")

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


def test_the_menu_opens_on_the_tracked_software(page, isolated_settings):
    from src import player_update_check

    target, _stock, requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    player_update_check.set_track_multiple_software_enabled(True, window.settings)
    player_update_check.record_device_install(
        "Y2", "Original Software", "3.2.4", settings=window.settings, manual=True
    )
    player_update_check.record_device_install(
        "Y2", "Rockbox", "1.0", settings=window.settings, manual=True
    )
    keep_data.note_running_software("Y2", "Original Software", window.settings, "A")

    assert window._guard_install_first(target, TARGET_REL) is False
    # With multiple firmwares tracked, it is a menu of firmwares: the tracked
    # software is the row it opens on.
    assert window._install_first_stage == "software"
    assert not page._keep_data_choice.isHidden()
    assert page._keep_data_choice.currentText() == requirement.label()
    assert window._install_first_selected_answer() == first.ANSWER_PREREQUISITE
    assert select.started == []
    assert window.navigated


def test_a_tracked_base_only_asks_about_its_version(page, isolated_settings):
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    keep_data.note_running_software("Y2", "Original Software", window.settings, "")

    assert window._guard_install_first(target, TARGET_REL) is False
    assert window._install_first_stage == "version"
    assert select.started == []

    window._on_install_first_continue()
    # Straight to Reborn: no base download, and nothing to keep for it.
    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_prerequisite_run is False
    assert window._install_first_journey.answer == first.ANSWER_PREREQUISITE
    assert window._install_first_journey.keep_data_allowed() is False
    assert window._install_first_stage == ""


def test_a_player_running_the_target_is_not_asked_again(page, isolated_settings):
    """The tracked software answers the question, so it is never put on screen."""
    target, _stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=None)
    window = make_window(page, select)
    keep_data.note_running_software("Y2", target.name, window.settings, "A")

    # The app already knows this model runs the firmware being installed: the
    # menu would only invite the user to contradict its own record.
    assert window._guard_install_first(target, TARGET_REL) is True
    assert window._install_first_stage == ""
    assert page._keep_data_choice.isHidden()
    assert window._install_first_journey.answer == first.ANSWER_TARGET
    assert window._install_first_journey.keep_data_allowed() is True
    # The guard does not start the run itself: it lets the install the user
    # asked for go ahead.
    assert select.started == []


def test_the_menu_lists_every_firmware_that_can_be_replaced(page, isolated_settings):
    target, _stock, requirement, items = reborn_and_stock()
    window = make_window(page, FakeSelect(base=None))

    assert window._guard_install_first(target, TARGET_REL) is False
    offers = [
        page._keep_data_choice.itemText(index)
        for index in range(page._keep_data_choice.count())
    ]
    # The base first, the firmware being installed second.
    assert offers[0] == requirement.label()
    assert offers[1] == target.name
    # Every system install_first_when names is covered by "Other" instead of
    # getting a row of its own: the base is required from it, so offering it
    # would promise an install the catalogue refuses.
    named = [
        p.name
        for p in items
        if first.normalize_repo(p.repo) in requirement.when_repos
    ]
    assert named
    for name in named:
        assert name not in offers
    assert offers[-1] == tr("keep_data_choice_other")
    for name in ("Rockbox", "Y2Player", "Solar", "Android 4.4 (Dev)"):
        assert name in offers
    assert "Classy" not in offers


def test_a_gated_system_requires_the_base(page, isolated_settings):
    target, stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=stock)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    page._keep_data_choice.setCurrentText(tr("keep_data_choice_other"))
    window._on_install_first_continue()

    # install_first_when names the system "Other" stands for: the base is
    # required, not suggested.
    assert window._install_first_stage == "confirm"
    assert window._install_first_journey.answer == first.ANSWER_OTHER
    assert select.started == []

    window._on_install_first_continue()
    assert select.started and select.started[0][1] is stock


def test_a_system_the_manifest_does_not_name_is_advised_to_use_the_base(
    page, isolated_settings
):
    target, stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=stock)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    page._keep_data_choice.setCurrentText("Rockbox")
    window._on_install_first_continue()

    assert window._install_first_journey.answer == first.ANSWER_FIRMWARE
    assert window._install_first_stage == "recommend"
    assert "Rockbox" in page._keep_data_hint.text()
    assert select.started == []

    # Yes installs the base first; the target follows by itself.
    window._on_install_first_continue()
    assert select.started and select.started[0][1] is stock
    assert window._install_first_after == (target, TARGET_REL)


def test_declining_the_base_installs_the_target_without_keep_data(
    page, isolated_settings
):
    target, stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=stock)
    window = make_window(page, select)

    assert window._guard_install_first(target, TARGET_REL) is False
    page._keep_data_choice.setCurrentText("Rockbox")
    window._on_install_first_continue()
    assert window._install_first_stage == "recommend"

    window._decline_install_first_prerequisite()

    assert select.started == [(TARGET_REL, target, False)]
    assert window._install_first_prerequisite_run is False
    assert window._install_first_forces_no_keep_data() is True
    assert window._keep_data is False


def test_the_answer_is_stored_without_a_version(isolated_settings):
    keep_data.note_running_software("Y2", "Reborn (Y2Linux)", isolated_settings, "A")
    known = keep_data.known_running_software("Y2", isolated_settings, "A")
    assert known["software_name"] == "Reborn (Y2Linux)"
    assert known["tag_name"] == ""
    assert known["confirmed"] is True

    keep_data.forget_running_software("Y2", isolated_settings, "A")
    assert keep_data.known_running_software("Y2", isolated_settings, "A") == {}


def test_keep_data_sanity_check_asks_about_the_compatible_software(page, isolated_settings):
    _target, _stock, _requirement, items = reborn_and_stock()
    # A firmware with no ``install_first`` declaration is the one whose Keep
    # Data check is the only question: Reborn asks the menu of firmwares first.
    plain = next(p for p in items if p.name == "Rockbox")
    names = keep_data.compatible_software_names(plain, "Y2")
    assert names
    window = make_window(page, FakeSelect(base=None))
    window._keep_data_pkg = plain
    keep_data.note_running_software("Y2", names[-1], window.settings, "A")

    window._begin_keep_data_confirmation()

    # 2.5.5's question, restored: one compatible name is a yes/no naming that
    # software, not a confirmation of what the app happens to have recorded.
    assert page._keep_data_choice.isHidden()
    assert names[-1] in page._wait_question_label.text()
    assert window._keep_data_confirm_known_name == ""
    assert window._keep_data_confirm_pending is True


def test_try_anyway_only_appears_with_the_keep_data_override(
    page, isolated_settings, monkeypatch
):
    from src import keep_data

    _target, _stock, _requirement, items = reborn_and_stock()
    # A firmware without do_not_keep_data, so only the override decides.
    plain = next(p for p in items if p.name == "Rockbox")
    window = make_window(page, FakeSelect(base=None))
    window._keep_data_pkg = plain

    monkeypatch.setattr(keep_data, "allow_keep_data_any", lambda settings=None: False)
    window._begin_keep_data_erase_confirmation()
    assert "try-anyway" not in page._keep_data_hint.text()

    monkeypatch.setattr(keep_data, "allow_keep_data_any", lambda settings=None: True)
    window._begin_keep_data_erase_confirmation()
    assert "try-anyway" in page._keep_data_hint.text()


def test_the_gate_reads_the_manifests_current_row(page, isolated_settings):
    import dataclasses

    target, _stock, _requirement, _items = reborn_and_stock()
    # The page built its list from an older manifest: this copy declares no
    # prerequisite at all. The live catalogue's row is what must be read, or a
    # corrected install_first would only take effect on the next launch.
    stale = dataclasses.replace(target, install_first="", install_first_when="")
    assert first.requirement_for(stale, "Y2") is None

    window = make_window(page, FakeSelect(base=None))
    assert window._guard_install_first(stale, TARGET_REL) is False
    assert window._install_first_stage == "software"
    assert window._install_first_journey.requirement is not None
    assert window._install_first_journey.requirement.repo


def test_the_base_stage_shows_the_base_firmwares_own_icon(
    page, isolated_settings, monkeypatch
):
    from PySide6.QtGui import QPixmap

    from src import config, release_icons
    from src.ui import release_icon as release_icon_module

    target, stock, requirement, _items = reborn_and_stock()
    select = FakeSelect(base=stock)
    base_rel = {"tag_name": "stable-v3.2.0", "asset_name": "rom_y2.zip"}
    select._current_selected_rel = base_rel
    select._details_package = stock
    window = make_window(page, select)
    window._install_first_journey = journey_for(target, requirement, first.ANSWER_OTHER)
    window._install_first_prerequisite_run = True
    window._package_path = ""

    # An SP Flash Tool / portable run would otherwise fall back to the generic
    # squircle for exactly the stage that has to be recognisable.
    monkeypatch.setattr(config, "is_mediatek_installer", lambda: True)
    monkeypatch.setattr(release_icons, "icon_candidate_urls", lambda *a, **k: [])
    monkeypatch.setattr(release_icons, "release_image_fallback_urls", lambda *a, **k: [])
    calls = []

    def fake_load(release, package=None, **kwargs):
        calls.append((release, package))
        return QPixmap()

    monkeypatch.setattr(release_icon_module, "load_release_pixmap", fake_load)

    window._apply_release_icon()
    # The base's own release row and package, driven by install_first alone.
    assert calls == [(base_rel, stock)]

    # The target's run keeps the ordinary local/generic behaviour.
    window._install_first_prerequisite_run = False
    window._apply_release_icon()
    assert calls == [(base_rel, stock)]


def test_the_firmware_menu_comes_before_the_reborn_yes_no(page, isolated_settings):
    target, stock, _requirement, _items = reborn_and_stock()
    select = FakeSelect(base=stock)
    window = make_window(page, select)
    window._keep_data_pkg = target
    window._current_selected_rel = TARGET_REL

    window._begin_keep_data_confirmation()

    # A run that reaches the power-off card without the Select page's guard
    # must still be asked what the player runs, not whether Reborn is already
    # on it: that answer is what decides the base and the Keep Data tick.
    assert not getattr(window, "_keep_data_confirm_pending", False)
    assert window._install_first_stage == "software"
    assert not page._keep_data_choice.isHidden()
    assert page._keep_data_choice.count() > 3
    assert "Which software" in page._wait_question_label.text()
    assert select.started == []


def test_an_answered_menu_leaves_the_keep_data_question_alone(page, isolated_settings):
    target, _stock, requirement, _items = reborn_and_stock()
    window = make_window(page, FakeSelect(base=None))
    window._keep_data_pkg = target

    window._install_first_journey = journey_for(target, requirement, first.ANSWER_TARGET)
    assert window._install_first_gate_awaits_answer() is False

    window._install_first_journey = None
    assert window._install_first_gate_awaits_answer() is True


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


def test_the_real_window_asks_before_any_download_or_power_off(qapp, isolated_settings, monkeypatch):
    """Clicking Install on a gated firmware shows the drop-down first."""
    from src.ui.main_window import MainWindow

    window = MainWindow()
    try:
        window._package_model = "Y2"
        # Nothing is tracked in this test's store: the question must be asked,
        # not answered from whatever this machine happens to have on record.
        window.settings = isolated_settings
        page, card = window._select_page, window._flash_page
        events = []
        page._should_prompt_pre_install = lambda: True
        page._prompt_pre_install = lambda *a, **k: events.append("power_off") or False
        page._stop_transfer_workers = lambda: None
        for name in dir(page):
            if name.startswith("_start") and "download" in name:
                monkeypatch.setattr(page, name, lambda *a, **k: events.append("download"))
        target = next(p for p in ITEMS if p.model == "Y2" and "reborn" in p.name.lower())
        rel = {
            "tag_name": "stable-v3.2.0",
            "name": "Reborn 3.2.0",
            "asset_name": "rom_y2.zip",
            "download_url": "https://example.invalid/rom_y2.zip",
            "published_at": "2026-01-01T00:00:00Z",
        }

        page._trigger_release_install(rel, target)
        qapp.processEvents()

        # Nothing left the machine and the player was not asked to power off:
        # the question owns the flow from here.
        assert events == []
        assert window._power_off_prompt is False
        assert window._install_first_stage == "software"
        assert card._keep_data_choice.count() > 1
        assert card._keep_data_choice.itemText(0).startswith("Original Software")
        assert card._keep_data_choice.currentText().startswith("Original Software")
        # Reborn names the systems its required firmware has to go over
        # (``install_first_when``), so the menu is not the three-row base /
        # target / Other set and the hint cannot enumerate it: it just asks
        # what the player is running now.
        assert card._keep_data_hint.text() == tr("install_first_which_firmware_hint")
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_the_real_window_does_not_ask_a_tracked_player(qapp, isolated_settings, monkeypatch):
    """A tracked Reborn on Y2 is the answer: no "which software" card at all.

    The Settings screen already says this player runs Reborn, so clicking
    Install on Reborn used to show a drop-down whose answer was on record. The
    tracked software has to answer for the player and the install go ahead.
    """
    window = MainWindow()
    try:
        window._package_model = "Y2"
        page, card = window._select_page, window._flash_page
        asked = []
        monkeypatch.setattr(
            card, "show_install_first_prompt", lambda *a, **k: asked.append(a)
        )
        page._should_prompt_pre_install = lambda: True
        page._prompt_pre_install = lambda *a, **k: False
        page._stop_transfer_workers = lambda: None
        # The real window reads tracking from its own store; point it at the
        # throwaway one so the answer this test writes is the answer it reads.
        window.settings = isolated_settings
        target = next(p for p in ITEMS if p.model == "Y2" and "reborn" in p.name.lower())
        keep_data.note_running_software("Y2", target.name, isolated_settings, "A")
        rel = {
            "tag_name": "stable-v3.2.0",
            "name": "Reborn 3.2.0",
            "asset_name": "rom_y2.zip",
            "download_url": "https://example.invalid/rom_y2.zip",
            "published_at": "2026-01-01T00:00:00Z",
        }

        page._trigger_release_install(rel, target)
        qapp.processEvents()

        assert asked == [], "the tracked software must not be asked about"
        assert window._install_first_stage == ""
        assert window._install_first_journey.answer == first.ANSWER_TARGET
        assert card._keep_data_choice.isHidden()
    finally:
        window.deleteLater()
        qapp.processEvents()


def test_a_stale_catalogue_row_cannot_strip_the_gate(monkeypatch):
    """A row read before the manifest landed must not hide install_first."""
    from types import SimpleNamespace

    target = next(p for p in ITEMS if p.model == "Y2" and "reborn" in p.name.lower())
    bare = SimpleNamespace(
        **{**target.__dict__, "install_first": "", "install_first_when": "", "install_first_once": False}
    )
    monkeypatch.setattr(catalog, "packages_for_model", lambda model, brand="": [bare])
    # No live manifest: this is the launch that only had the stale row.
    monkeypatch.setattr(catalog, "LIVE_CATALOG", [])
    resolved = MainWindow._manifest_fresh_package(SimpleNamespace(), target)
    assert resolved.install_first == target.install_first
    assert resolved.install_first_when == target.install_first_when
