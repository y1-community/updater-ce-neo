"""Desktop accent lookups that do not open a permission dialog."""

from src.ui.dark import accent_from_desktop_config


def test_plasma_accent_is_a_hex_colour():
    text = "[General]\nAccentColor=61,174,233\n"
    assert accent_from_desktop_config(text) == "#3daee9"


def test_gnome_accent_name_maps_to_hex():
    text = "[Settings]\naccent-color=purple\n"
    assert accent_from_desktop_config(text) == "#9141ac"


def test_a_config_without_an_accent_is_left_alone():
    assert accent_from_desktop_config("[Settings]\ngtk-theme-name=Adwaita\n") is None
