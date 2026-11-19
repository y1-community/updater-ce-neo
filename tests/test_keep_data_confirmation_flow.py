"""The Keep Data sanity check never turns a No into a cancelled install.

When the player's software is unknown, the install asks which firmware is
running. One compatible item is a yes/no question; answering No means the
player runs something else, so the install continues and explains that the
settings cannot be carried over. It is not a request to stop.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src.i18n import tr
from src.ui.flash_page import FlashPage


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


def test_the_no_chip_reports_incompatible_software_instead_of_stopping(page, qapp):
    answered = []
    page.show_keep_data_confirmation(
        "Is Original Software installed?",
        "Only compatible software can keep settings.",
        (),
        selected="",
        on_no=lambda: answered.append(True),
    )

    assert page._keep_data_confirm_active
    page._wait_cancel_btn.click()
    qapp.processEvents()

    assert answered == [True]
    # The card is still the question: the install was not cancelled.
    assert page._wait_copy == "keep_data_confirm"


def test_without_a_no_handler_the_cancel_chip_still_stops_the_install(page, qapp, monkeypatch):
    stopped = []
    monkeypatch.setattr(page, "_nothing_written_yet", lambda: True)
    page._cancel_wait_callback = lambda: stopped.append(True)

    page.show_keep_data_confirmation("Is Original Software installed?", "", ())
    page._wait_cancel_btn.click()
    qapp.processEvents()

    assert stopped == [True]


def test_one_compatible_item_offsets_the_checkbox_with_the_incoming_software(page, qapp):
    page.show_keep_data_confirmation(
        "Is Better-Y installed?",
        "",
        (),
        selected="Better-Y",
        on_no=lambda: None,
    )
    # A yes/no question has no drop-down to choose from; the chips read
    # Yes/No rather than Next/Cancel.
    assert page._keep_data_choice.isHidden()
    assert page._wait_continue_btn.toolTip() == tr("btn_yes")
    assert page._wait_cancel_btn.toolTip() == tr("btn_no")


def test_several_items_show_a_dropdown_with_the_incoming_software_selected(page, qapp):
    page.show_keep_data_confirmation(
        "Which software is running?",
        "",
        ("Original Software", "Better-Y", "Koensayr", "Other software"),
        selected="Better-Y",
        on_no=lambda: None,
    )
    assert not page._keep_data_choice.isHidden()
    assert page.keep_data_confirm_choice() == "Better-Y"


def test_the_question_card_hides_the_tick_and_the_software_title(page, qapp):
    """The tick was already answered, and the title repeats version + model."""
    page.set_model("Y2")
    page.set_package_name("Reborn 9.3.32 for Y2")
    page.set_has_userdata(True)

    page.show_keep_data_confirmation(
        "Is Reborn already installed on your Y2?",
        tr("keep_data_only_compatible_hint_other"),
        (),
        selected="Reborn",
    )

    assert page._keep_data_cb.isHidden()
    assert page._wait_pkg_label.isHidden()
    # The explanation and its link stay: they explain the question, not the tick.
    assert tr("keep_data_only_compatible_hint_other") in page._keep_data_hint.text()
    assert not page._keep_data_help_link.isHidden()


def test_a_settled_keep_data_answer_is_reused_without_the_tick(page, qapp):
    """A retry must not show the tick or change the first answer."""
    page.set_has_userdata(True)
    page.show_power_off_prompt()
    page._keep_data_cb.setChecked(True)

    page.lock_keep_data(True)
    page.show_power_off_prompt()

    assert page._keep_data_cb.isHidden()
    assert page._keep_data_warning.isHidden()
    assert page.keep_data_checked() is True
    # The retry re-reads the scatter and calls set_has_userdata again; that
    # must not resurrect the checkbox or turn the answer off.
    page.set_has_userdata(True, preserve_data_default=False)
    assert page._keep_data_cb.isHidden()
    assert page.keep_data_checked() is True


def test_a_new_install_unlocks_the_keep_data_tick(page, qapp):
    page.set_has_userdata(True)
    page.show_power_off_prompt()
    page.lock_keep_data(False)
    assert page._keep_data_cb.isHidden()

    page.reset_keep_data_preference()
    page.show_power_off_prompt()

    assert not page._keep_data_cb.isHidden()
    assert page.keep_data_checked() is False


def test_prep_marks_a_known_incompatible_firmware_for_the_warning(qapp, monkeypatch):
    """A known-incompatible install shows the erase warning and does not hide
    the fact behind a checkbox that silently disappeared."""
    from src import keep_data
    from src.ui.main_window import MainWindow

    window = MainWindow()
    try:
        monkeypatch.setattr(keep_data, "keep_data_available", lambda *a, **k: False)
        monkeypatch.setattr(
            keep_data, "incompatible_with_current", lambda *a, **k: True
        )
        monkeypatch.setattr(window, "_apply_pmt_format_state", lambda *a, **k: False)
        monkeypatch.setattr(window, "_apply_release_icon", lambda **k: None)
        monkeypatch.setattr(window, "_sync_install_nav_entry", lambda: None)
        window._package_name = "Test Firmware"
        window._package_model = "Y2"
        window._keep_data_pkg = None

        window._show_device_prep()

        assert window._keep_data_erase_required is True
        assert window._flash_page._keep_data_cb.isHidden()
        assert not window._flash_page._keep_data_warning.isHidden()
        assert "reset" in window._flash_page._keep_data_warning.text().lower()

        # The settled retry answer wins over re-detecting compatibility.
        window._keep_data_choice_locked = True
        window._show_device_prep()
        assert window._keep_data_erase_required is False
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_retries_reuse_the_answer_without_showing_the_tick(qapp):
    from src.ui.main_window import MainWindow

    window = MainWindow()
    try:
        page = window._flash_page
        page.set_has_userdata(True)
        page.show_power_off_prompt()
        page._keep_data_cb.setChecked(True)
        window._keep_data = True

        window._lock_keep_data_for_retries()
        # The next backend's power-off card is a new prompt, not a new install.
        page.show_power_off_prompt()

        assert window._keep_data_choice_locked is True
        assert page._keep_data_cb.isHidden()
        assert page.keep_data_checked() is True
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_choosing_other_running_firmware_shows_the_erase_warning(qapp):
    """The dropdown's "Other" answer is the incompatible-firmware warning."""
    from src.ui.main_window import MainWindow

    window = MainWindow()
    asked = []
    try:
        page = window._flash_page
        page.set_has_userdata(True)
        page.show_keep_data_confirmation(
            "Which software is running?",
            "",
            ("Original Software", tr("keep_data_choice_other")),
            selected="Original Software",
        )
        page._keep_data_choice.setCurrentText(tr("keep_data_choice_other"))
        window._keep_data = True
        window._keep_data_confirm_pending = True
        window._begin_keep_data_erase_confirmation = lambda: asked.append(True)

        window._on_power_off_continue()

        assert asked == [True]
        assert window._keep_data_confirm_pending is False
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_an_update_is_not_asked_and_an_unknown_player_is(qapp, monkeypatch):
    """2.5.5's rule: only the player the app cannot account for is asked.

    Reinstalling the tracked firmware is an update, so Keep Data carries the
    data forward without a question; a player whose software the catalogue
    cannot compare still gets the sanity check.
    """
    from src import keep_data
    from src.ui.main_window import MainWindow

    window = MainWindow()
    asked = []
    try:
        page = window._flash_page
        page.set_has_userdata(True)
        page.show_power_off_prompt()
        page._keep_data_cb.setChecked(True)
        monkeypatch.setattr(keep_data, "needs_confirmation", lambda *a, **k: True)
        window._begin_keep_data_confirmation = lambda: asked.append(True)

        # Tracked and same as the incoming firmware: an update, no question.
        window._keep_data_default_for_install = lambda: True
        window._on_power_off_continue()
        assert asked == []

        # Nothing tracked: the upgrade cannot be compared, so ask.
        window._keep_data_default_for_install = lambda: False
        window._keep_data = True
        window._on_power_off_continue()
        assert asked == [True]
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_keeping_settings_still_confirms_the_running_firmware(qapp, monkeypatch):
    """"Keep my settings" only pre-ticks the box; it does not say what runs.

    The update prompt's answer carries the data forward, but the question
    between the power-off card and the connect stage is 2.5.5's, and it stays.
    """
    from src import keep_data
    from src.ui.main_window import MainWindow

    window = MainWindow()
    asked = []
    try:
        page = window._flash_page
        page.set_has_userdata(True)
        page.show_power_off_prompt()
        page._keep_data_cb.setChecked(True)
        monkeypatch.setattr(keep_data, "needs_confirmation", lambda *a, **k: True)
        window._begin_keep_data_confirmation = lambda: asked.append(True)
        window._keep_data_default_for_install = lambda: True
        window._keep_settings_default = True

        window._on_power_off_continue()

        assert asked == [True]
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_a_known_incompatible_firmware_asks_before_erasing(qapp):
    """Knowing the tracked firmware differs must not erase settings silently."""
    from src.ui.main_window import MainWindow

    window = MainWindow()
    asked = []
    try:
        window._flash_page.set_has_userdata(True)
        window._flash_page.show_power_off_prompt()
        window._keep_data_erase_required = True
        window._begin_keep_data_erase_confirmation = lambda: asked.append(True)

        window._on_power_off_continue()

        assert asked == [True]
        assert window._keep_data is False
        assert window._keep_data_erase_required is True
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_cancel_on_the_questions_leaves_the_install(qapp):
    """X on the erase warning, or on a question with a list, ends the run.

    Both cards are asked before the first write, so their X is the user
    leaving the install rather than answering it or stepping back through it.
    """
    from src.ui.main_window import MainWindow, _PAGE_SELECT

    window = MainWindow()
    try:
        window.show()
        page = window._flash_page
        page.set_has_userdata(True)
        window._package_path = "/tmp/keep_data_cancel.zip"
        window._package_name = "Cancel Me"
        window._package_model = "Y1"
        window._show_device_prep()
        qapp.processEvents()

        # A question with a list of answers: X is the cancel chip, so it leaves.
        page.show_keep_data_confirmation(
            "Which software is running?",
            "",
            ("Original Software", "Rockbox", tr("keep_data_choice_other")),
            selected="Original Software",
        )
        window._keep_data_confirm_pending = True
        window._keep_data = True

        page._wait_cancel_btn.click()
        qapp.processEvents()

        assert window._keep_data_confirm_pending is False
        assert window._keep_data is False
        assert window._stack.currentIndex() == _PAGE_SELECT
        assert not window._install_run_active()

        # The erase warning answers the same gesture the same way.
        window._show_device_prep()
        qapp.processEvents()
        page.show_keep_data_erase_confirmation("Install and erase?", "explainer")
        window._keep_data_erase_confirm_pending = True

        page._wait_cancel_btn.click()
        qapp.processEvents()

        assert window._keep_data_erase_confirm_pending is False
        assert window._stack.currentIndex() == _PAGE_SELECT
        assert not window._install_run_active()
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()


def test_accepting_the_erase_confirmation_unlocks_the_retry(qapp):
    """After the warning is accepted, later retries reuse the erase answer."""
    from src.ui.main_window import MainWindow

    window = MainWindow()
    calls = []
    try:
        window._flash_page.set_has_userdata(True)
        window._flash_page.show_power_off_prompt()
        window._keep_data_erase_required = True
        window._keep_data_erase_confirm_pending = True
        window._begin_flash_flow = lambda: calls.append(True)
        window._flash_page.clear_keep_data_confirmation = lambda: None

        window._on_power_off_continue()

        assert calls == [True]
        assert window._keep_data is False
        assert window._keep_data_erase_required is False
    finally:
        window._force_quit = True
        window.close()
        qapp.processEvents()
