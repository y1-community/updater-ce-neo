import pytest
from PySide6.QtCore import QSettings
from src import catalog, device_tracking, install_first, keep_data, player_update_check
from src.ui.select_page import SelectPackagePage
from src.ui.settings_page import SettingsPage


def _retire_page_workers(page) -> None:
    """Stop the listing threads before the page dies with one still running."""
    workers = [getattr(page, "_releases_worker", None)]
    workers.extend(getattr(page, "_active_workers", ()) or ())
    for worker in workers:
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
            worker.wait(1000)


@pytest.fixture
def clean_settings():
    s = QSettings("innioasis_test", "updater_test")
    s.clear()
    yield s
    s.clear()


def test_software_combo_has_no_dot(qtbot, clean_settings, monkeypatch):
    """The software dropdown must never show a dot prefix; the dot is only on the version in the list."""
    page = SelectPackagePage()
    qtbot.addWidget(page)
    page.settings = clean_settings
    monkeypatch.setattr(page, "current_model", lambda: "Y1")
    monkeypatch.setattr(page, "current_hardware_type", lambda: "A")

    names = ["Original Software", "Rockbox", "Better-Y"]

    # Even with a tracked install, software dropdown stays clean without a dot
    player_update_check.record_device_install(
        "Y1", "Original Software", "3.1.2", hardware_type="A", settings=clean_settings, manual=False
    )
    page._populate_software_combo(names)
    assert page._software_combo.count() == 3
    for i in range(page._software_combo.count()):
        assert not page._software_combo.itemText(i).startswith("● ")
    assert page.current_software() == "Original Software"
    _retire_page_workers(page)


def test_completed_installs_update_completion_slot_leaving_manual_alone(clean_settings):
    """Completed installs update _GROUP_INSTALLS and leave manual entries in _GROUP_SOFTWARES alone."""
    player_update_check.set_track_multiple_software_enabled(True, clean_settings)

    # 1. Manual entry added by user for Rockbox
    player_update_check.record_device_install(
        "Y1", "Rockbox", "1.0", hardware_type="A", settings=clean_settings, manual=True
    )
    # Since _GROUP_INSTALLS was empty, it went to _GROUP_INSTALLS
    installs = player_update_check.get_tracked_installs("Y1", hardware_type="A", settings=clean_settings)
    assert len(installs) == 1
    assert installs[0]["software_name"] == "Rockbox"

    # 2. User manually adds Original Software
    player_update_check.record_device_install(
        "Y1", "Original Software", "3.1.2", hardware_type="A", settings=clean_settings, manual=True
    )
    # Now Rockbox remains in _GROUP_INSTALLS, Original Software in _GROUP_SOFTWARES
    installs = player_update_check.get_tracked_installs("Y1", hardware_type="A", settings=clean_settings)
    assert len(installs) == 2
    sw_names = {rec["software_name"] for rec in installs}
    assert sw_names == {"Rockbox", "Original Software"}

    # 3. An install completes for Better-Y (manual=False)
    player_update_check.record_device_install(
        "Y1", "Better-Y", "1.5", hardware_type="A", settings=clean_settings, manual=False
    )
    # The install slot was updated to Better-Y, while manual entry (Original Software) was left alone
    installs = player_update_check.get_tracked_installs("Y1", hardware_type="A", settings=clean_settings)
    assert len(installs) == 2
    sw_names = {rec["software_name"] for rec in installs}
    assert sw_names == {"Better-Y", "Original Software"}

    # 4. Turn off multi-tracking -> Pruned back to only the latest install (Better-Y)
    player_update_check.set_track_multiple_software_enabled(False, clean_settings)
    player_update_check.prune_tracked_softwares(clean_settings)
    installs = player_update_check.get_tracked_installs("Y1", hardware_type="A", settings=clean_settings)
    assert len(installs) == 1
    assert installs[0]["software_name"] == "Better-Y"


def test_single_question_flow_for_install_first_and_keep_data(monkeypatch, clean_settings):
    """When both install_first and keep_data are active, user is asked ONCE at install start.

    At Keep Data stage, it must not ask a second time.
    """
    from src.ui.main_window import MainWindow

    class FakeFlashPage:
        def __init__(self):
            self._keep_data = True
            self.locked = None
        def keep_data_checked(self):
            return self._keep_data
        def lock_keep_data(self, val):
            self.locked = val
        def keep_data_confirm_choice(self):
            return "Other"

    class FakeJourney:
        def __init__(self, answer="other", allowed=False):
            self.answer = answer
            self._allowed = allowed
            self.target = None
        def keep_data_allowed(self, rows=None):
            return self._allowed

    # Create dummy main window with needed attributes
    window = MainWindow.__new__(MainWindow)
    window.settings = clean_settings
    window._flash_page = FakeFlashPage()
    window._install_first_model = lambda: "Y1"
    window._select_page = None
    window._package_model = "Y1"
    window._keep_data_pkg = None
    window._package_name = "Target"
    window._install_first_already_on_target = lambda: False
    window._install_first_forces_no_keep_data = lambda: False
    window._keep_data_default_for_install = lambda: False

    # Scenario 1: journey has answer, and keep_data is NOT allowed by the answer
    window._install_first_journey = FakeJourney(answer="other", allowed=False)
    window._keep_data = True
    assert window._keep_data_needs_confirmation() is False
    # keep_data was set to False and locked
    assert window._keep_data is False
    assert window._keep_data_choice_locked is True
    assert window._flash_page.locked is False

    # Scenario 2: journey has answer, and keep_data IS allowed by the answer
    window._install_first_journey = FakeJourney(answer="family", allowed=True)
    window._keep_data = True
    window._keep_data_choice_locked = False
    assert window._keep_data_needs_confirmation() is False
    assert window._keep_data is True


def test_add_device_unknown_version_sets_ceiling(qtbot, clean_settings, monkeypatch):
    """Selecting 'I don't know' for firmware version sets notify_ceiling to catalogue latest."""
    card = SettingsPage()
    qtbot.addWidget(card)
    card.settings = clean_settings

    releases = [
        {"tag_name": "v3.2.0", "published_at": "2026-01-01T00:00:00Z"},
        {"tag_name": "v3.1.0", "published_at": "2025-01-01T00:00:00Z"},
    ]
    card._on_add_device_releases(getattr(card, "_add_device_request", None), releases)

    # Verify "I don't know" is in the version combo
    found_unknown = False
    for i in range(card._add_device_version.count()):
        if card._add_device_version.itemData(i) == "UNKNOWN":
            found_unknown = True
            card._add_device_version.setCurrentIndex(i)
            break
    assert found_unknown is True
    assert card._add_device_version_manual.isHidden()

    # Save device with UNKNOWN
    import types
    monkeypatch.setattr(card._add_device_model, "currentText", lambda: "Y2")
    pkg = types.SimpleNamespace(name="Original Software", model="Y2", repo="y2-community/stock", slug="y2-stock")
    monkeypatch.setattr(card, "_add_device_package", lambda: pkg)
    monkeypatch.setattr(card._add_device_software, "currentText", lambda: "Original Software")

    card._save_add_device()

    rec = player_update_check.get_device_install("Y2", settings=clean_settings)
    assert rec is not None
    assert rec["tag_name"] == "Unknown"
    assert rec["notify_ceiling"] == "v3.2.0"


def test_getting_ready_before_connect_search_and_not_after(qtbot):
    """'Getting ready...' is displayed before the connect prompt, not after it.

    When the flasher actively searches, Connect prompt is shown immediately and
    Getting Ready does NOT replace it.
    """
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    qtbot.addWidget(page)
    page.set_model("Y2")
    page.set_package_name("Y2-Stock")

    # 1. On power-off prompt
    page.show_power_off_prompt()

    # 2. Leaving power-off prompt: flasher hasn't searched yet, so show "Getting ready..."
    page.leave_power_off_prompt()
    assert page._wait_prompt_label.text() == tr("flash_getting_ready")
    assert tr("flash_connect_device_prompt").split("{")[0].strip() not in page._wait_prompt_label.text()

    # 3. Flasher outputs search line: Connect prompt is shown immediately
    page.show_connect_search()
    assert tr("flash_connect_device_prompt").split("{")[0].strip() in page._wait_prompt_label.text()
    assert "Y2" in page._wait_prompt_label.text()

    # Getting ready timer must NOT be running and must NOT replace connect prompt
    assert page._connect_get_ready_timer.isActive() is False
    assert page._wait_prompt_label.text() != tr("flash_getting_ready")


def test_release_list_dot_indicator_single_vs_multiple(qtbot, clean_settings, monkeypatch):
    """The release list only displays '● <version>' when not ambiguous and software matches."""
    page = SelectPackagePage()
    qtbot.addWidget(page)
    page.settings = clean_settings
    monkeypatch.setattr(page, "current_model", lambda: "Y1")
    page._selected_type = "A"
    monkeypatch.setattr(page, "current_software", lambda: "Original Software")

    releases = [
        {"tag_name": "v3.1.2", "published_at": "2026-01-01T00:00:00Z"},
        {"tag_name": "v3.1.0", "published_at": "2025-01-01T00:00:00Z"},
    ]

    # 1. Untracked: no dot in release list
    page._on_releases_loaded(releases, None)
    assert page._release_list.count() == 2
    assert not page._release_list.item(0).text().startswith("● ")
    assert not page._release_list.item(1).text().startswith("● ")

    # 2. Single tracked: v3.1.2 has dot!
    player_update_check.record_device_install(
        "Y1", "Original Software", "v3.1.2", hardware_type="A", settings=clean_settings, manual=False
    )
    page._on_releases_loaded(releases, None)
    assert page._release_list.item(0).text().startswith("● ")
    assert not page._release_list.item(1).text().startswith("● ")

    # 3. Multiple tracked: no dot in release list!
    player_update_check.set_track_multiple_software_enabled(True, clean_settings)
    player_update_check.record_device_install(
        "Y1", "Rockbox", "1.0", hardware_type="A", settings=clean_settings, manual=True
    )
    page._on_releases_loaded(releases, None)
    assert not page._release_list.item(0).text().startswith("● ")
    assert not page._release_list.item(1).text().startswith("● ")
    _retire_page_workers(page)


def test_keep_data_needs_confirmation_for_untracked_and_multiple(clean_settings):
    """keep_data.needs_confirmation returns True for 0 tracked or multiple tracked, False for 1 compatible."""
    import types
    pkg = types.SimpleNamespace(
        name="Original Software",
        model="Y1",
        repo="y1-community/stock",
        slug="y1-stock",
        do_not_keep_data=False,
    )

    # 0 tracked: needs confirmation
    assert keep_data.needs_confirmation("Y1", pkg, settings=clean_settings, hardware_type="A") is True

    # 1 tracked and compatible: does NOT need confirmation
    player_update_check.record_device_install(
        "Y1", "Original Software", "3.1.2", hardware_type="A", settings=clean_settings, manual=False
    )
    assert keep_data.needs_confirmation("Y1", pkg, settings=clean_settings, hardware_type="A") is False

    # 2 tracked: needs confirmation
    player_update_check.set_track_multiple_software_enabled(True, clean_settings)
    player_update_check.record_device_install(
        "Y1", "Rockbox", "1.0", hardware_type="A", settings=clean_settings, manual=True
    )
    assert keep_data.needs_confirmation("Y1", pkg, settings=clean_settings, hardware_type="A") is True


def test_donations_screen_view_guide_stays_donation_buttons_dismiss(qtbot, monkeypatch):
    """View Guide button does NOT dismiss Donations+Completion screen; donation buttons do dismiss it."""
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    qtbot.addWidget(page)
    page.show()
    page.show_flashing()
    page.set_model("Y2")
    page.set_post_install_urls("https://example.com/guide")
    page.set_device_done(donations_shown=True)

    dismissed = []
    page.show_completion_note(True, software="TestFW", model="Y2")
    page.show_completion_appeal(True, on_not_now=lambda: dismissed.append("not_now"))

    # Mock open_browser
    opened_urls = []
    monkeypatch.setattr("src.browser.open_browser", lambda url: opened_urls.append(url))

    # 1. Click View Guide: Opens guide, but DOES NOT dismiss screen!
    assert page._view_guide_btn.isVisible()
    page._view_guide_btn.click()
    assert len(opened_urls) == 1
    assert "example.com/guide" in opened_urls[0]
    assert len(dismissed) == 0

    # 2. Click Coffee button: Opens URL AND dismisses screen!
    page._coffee_btn.click()
    assert len(opened_urls) == 2
    assert "not_now" in dismissed

    # 3. Click BMC button: Opens URL AND dismisses screen!
    dismissed.clear()
    page._bmc_btn.click()
    assert len(opened_urls) == 3
    assert "not_now" in dismissed


def test_settings_track_multiple_toggle_confirmation(qtbot, clean_settings, monkeypatch):
    """Toggling 'Remind me about multiple releases on a single model' runs confirmation."""
    page = SettingsPage()
    qtbot.addWidget(page)
    page.settings = clean_settings
    device_tracking.set_track_multiple_software_enabled(False, clean_settings)
    page.refresh_settings()
    assert page._cb_track_multiple.isChecked() is False

    # Mock run_confirmation to reject first
    monkeypatch.setattr(page._reminders_card, "run_confirmation", lambda *args, **kwargs: False)
    page._cb_track_multiple.setChecked(True)
    assert page._cb_track_multiple.isChecked() is False
    assert device_tracking.is_track_multiple_software_enabled(clean_settings) is False

    # Mock run_confirmation to accept
    monkeypatch.setattr(page._reminders_card, "run_confirmation", lambda *args, **kwargs: True)
    page._cb_track_multiple.setChecked(True)
    assert page._cb_track_multiple.isChecked() is True
    assert device_tracking.is_track_multiple_software_enabled(clean_settings) is True


def test_allow_keep_data_any_card_visibility(qtbot, clean_settings):
    """The 'Allow Keep Settings and Media on any firmware install' card only shows if Show Advanced Settings is on."""
    page = SettingsPage()
    qtbot.addWidget(page)
    page.settings = clean_settings
    page.show()

    # By default, Show Advanced Settings is off -> _keep_data_card is hidden
    device_tracking.set_advanced_settings_enabled(False, clean_settings)
    page.refresh_settings()
    assert page._keep_data_card.isHidden() is True

    # When Show Advanced Settings is enabled -> _keep_data_card is visible
    page._cb_advanced_settings.setChecked(True)
    assert page._keep_data_card.isHidden() is False

    # When toggled back off -> _keep_data_card is hidden again
    page._cb_advanced_settings.setChecked(False)
    assert page._keep_data_card.isHidden() is True


def test_unknown_not_in_software_dropdown_or_catalog():
    """'Unknown' / software_unknown is a placeholder for unrecognised versions, never a software package."""
    names_y1 = catalog.software_names_for_model("Y1")
    assert "Unknown" not in names_y1
    assert "software_unknown" not in names_y1
    names_y2 = catalog.software_names_for_model("Y2")
    assert "Unknown" not in names_y2
    assert "software_unknown" not in names_y2


def test_track_multiple_hidden_during_onboarding_visible_in_settings(qtbot, clean_settings):
    """The multi-tracking toggle and caption must NOT appear on onboarding, only on Add your players in Settings."""
    page = SettingsPage()
    qtbot.addWidget(page)
    page.settings = clean_settings
    page.show()

    row_widget, caption_label = page._switch_rows[page._cb_track_multiple]

    # In normal Settings mode: row and caption are visible on the Add your players card
    assert not row_widget.isHidden()
    assert not caption_label.isHidden()
    assert not page._cb_track_multiple.isHidden()

    # During onboarding: entire row (checkbox + caption) is hidden
    page.begin_onboarding()
    assert row_widget.isHidden()

    # After onboarding ends: entire row is visible again
    page.end_onboarding()
    assert not row_widget.isHidden()
    assert not caption_label.isHidden()
    assert not page._cb_track_multiple.isHidden()




