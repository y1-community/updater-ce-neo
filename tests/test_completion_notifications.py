"""Completion notifications must not repeat the model the package name names."""

from src.ui.main_window import _completion_notification_message


def test_model_already_in_package_name_is_not_appended_again():
    assert (
        _completion_notification_message("Original Software 3.2.1 for Y2", "Y2")
        == "Original Software 3.2.1 for Y2"
    )


def test_model_is_appended_when_the_name_does_not_carry_it():
    assert (
        _completion_notification_message("Rockbox 3.15", "Y2")
        == "Rockbox 3.15 Y2"
    )


def test_model_inside_parentheses_still_counts_as_present():
    assert (
        _completion_notification_message("Original Software (Y1)", "Y1")
        == "Original Software (Y1)"
    )


def test_missing_software_name_falls_back_to_the_model():
    assert _completion_notification_message("", "A5") == "A5"


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_completion_note_names_the_model_once():
    """"Rockbox for Y2 is installed on your Y2" read as the model twice."""
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    try:
        page.show_completion_note(True, software="Rockbox for Y2", model="Y2")
        first = page._complete_note.text().splitlines()[0]
        assert first == tr("donate_completion_subtitle").format(
            software="Rockbox", model="Y2"
        )
        assert "for Y2" not in first
    finally:
        page.close()
        app.processEvents()


def test_completion_appeal_subtitle_names_the_model_once():
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    try:
        page._package_name = "Rockbox for Y2"
        page._model = "Y2"
        page.show_completion_appeal(True)
        assert page._intro.subtitle.text() == tr("donate_completion_subtitle").format(
            software="Rockbox", model="Y2"
        )
    finally:
        page.close()
        app.processEvents()


def test_names_that_only_look_like_the_model_are_left_alone():
    from src.ui.flash_page import _software_without_model

    assert _software_without_model("Y2Player", "Y2") == "Y2Player"
    assert _software_without_model("Rockbox 3.15", "Y2") == "Rockbox 3.15"
    # A name that is nothing but the model stays as it is rather than blanking.
    assert _software_without_model("Y2", "Y2") == "Y2"
    assert _software_without_model("Solar (A5)", "A5") == "Solar"
