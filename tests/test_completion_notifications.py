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
