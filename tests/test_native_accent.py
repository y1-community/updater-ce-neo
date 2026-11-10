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


def test_a_forced_probe_reads_the_users_default(monkeypatch):
    """After the macOS notification, the accent comes from the user's default.

    AppKit caches controlAccentColor per process, so reading it again in the
    same process can still answer the old colour. The notification handler
    asks for one fresh read of the stored default.
    """
    from src.ui import dark

    monkeypatch.setattr(dark.sys, "platform", "darwin")
    monkeypatch.setattr(
        dark, "_macos_accent_from_defaults", lambda: ("#ff375f", "#ffffff")
    )
    dark.note_macos_accent_changed()

    assert dark._probe_native_accent(None) == ("#ff375f", "#ffffff")
    assert dark._force_plist_accent is False
