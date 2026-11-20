"""Tests for Add Player, player update reminders with model+type permutations,
keep data integration, Rockbox 360p themes default gating, and remote debug/rescue flags.
"""

import os
import sys
from unittest.mock import patch, MagicMock
from pathlib import Path
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

from src import player_update_check as device_tracking
from src import keep_data
from src import theme_sync
from src import remote_flags
from src.catalog import FirmwarePackage
from src.i18n import tr, translator


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture
def temp_settings(tmp_path):
    ini_path = tmp_path / "test_settings.ini"
    return QSettings(str(ini_path), QSettings.Format.IniFormat)


def test_add_player_renaming():
    """Verify that settings_add_device translates to 'Add Player' across languages."""
    previous = translator().lang
    try:
        translator().set_language("en")
        assert tr("settings_add_device") == "Add Player"

        translator().set_language("zh-CN")
        assert tr("settings_add_device") == "添加播放器"

        translator().set_language("de")
        assert tr("settings_add_device") == "Player hinzufügen"
    finally:
        translator().set_language(previous or "en")


def test_add_device_type_field_only_for_multi_type_models(app):
    """Y1 needs a type choice; models with one revision must not show one."""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QComboBox, QLabel

    from src.ui.settings_page import SettingsPage

    holder = SimpleNamespace(
        _add_device_model=QComboBox(),
        _add_device_type=QComboBox(),
        _add_device_type_label=QLabel(),
        _add_device_software=QComboBox(),
        _on_add_device_software_changed=lambda *_: None,
    )
    holder._add_device_model.addItems(["Y1", "Y2"])

    holder._add_device_model.setCurrentText("Y1")
    SettingsPage._on_add_device_model_changed(holder)
    assert not holder._add_device_type.isHidden()
    assert not holder._add_device_type_label.isHidden()
    assert holder._add_device_type.count() == 2
    assert holder._add_device_type.currentData() == "A"
    assert holder._add_device_type.itemText(0) == (
        f"{tr('settings_add_device_type')} {tr('sel_type_a')}"
    )
    assert holder._add_device_type.itemText(1) == (
        f"{tr('settings_add_device_type')} {tr('sel_type_b')}"
    )

    holder._add_device_model.setCurrentText("Y2")
    SettingsPage._on_add_device_model_changed(holder)
    assert holder._add_device_type.isHidden()
    assert holder._add_device_type_label.isHidden()
    assert holder._add_device_type.count() == 0


def test_model_and_type_permutations_tracking(temp_settings):
    """Verify that multiple hardware types of the same model (e.g. Y1 Type A and Type B) can be tracked simultaneously."""
    device_tracking.record_device_install(
        model="Y1",
        software_name="Original Software",
        tag_name="3.1.2",
        release_label="3.1.2",
        hardware_type="A",
        settings=temp_settings,
    )
    device_tracking.record_device_install(
        model="Y1",
        software_name="Rockbox",
        tag_name="0.2.5",
        release_label="0.2.5",
        hardware_type="B",
        settings=temp_settings,
    )
    device_tracking.record_device_install(
        model="Y2",
        software_name="Original Software",
        tag_name="3.1.7",
        release_label="3.1.7",
        settings=temp_settings,
    )

    all_installs = device_tracking.get_all_device_installs(settings=temp_settings)
    assert "Y1_A" in all_installs
    assert "Y1_B" in all_installs
    assert "Y2" in all_installs

    rec_a = all_installs["Y1_A"]
    assert rec_a["software_name"] == "Original Software"
    assert rec_a["hardware_type"] == "A"

    rec_b = all_installs["Y1_B"]
    assert rec_b["software_name"] == "Rockbox"
    assert rec_b["hardware_type"] == "B"

    # Permutation labels name only a non-standard hardware revision.
    label_a = device_tracking.tracked_permutation_label(rec_a)
    label_b = device_tracking.tracked_permutation_label(rec_b)
    type_a = f"{tr('settings_add_device_type')} {tr('sel_type_a')}"
    type_b = f"{tr('settings_add_device_type')} {tr('sel_type_b')}"
    assert type_a not in label_a
    assert "Y1" in label_a
    assert type_b in label_b

    # Unchecking / clearing Y1_A leaves Y1_B and Y2 intact
    device_tracking.clear_device_install("Y1", hardware_type="A", settings=temp_settings)
    remaining = device_tracking.get_all_device_installs(settings=temp_settings)
    assert "Y1_A" not in remaining
    assert "Y1_B" in remaining
    assert "Y2" in remaining


def test_keep_data_with_tracked_permutations(temp_settings):
    """Verify that keep_data checks match logged permutations and derivatives."""
    # Track an original software install on Y1 Type A
    device_tracking.record_device_install(
        model="Y1",
        software_name="Original Software",
        tag_name="3.1.2",
        release_label="3.1.2",
        hardware_type="A",
        settings=temp_settings,
    )

    orig_pkg = FirmwarePackage(
        slug="stock-y1",
        name="Original Software",
        model="Y1",
        repo="y1-community/y1-stock-rom",
        package_name="Original Software",
    )
    rockbox_pkg = FirmwarePackage(
        slug="rockbox-y1",
        name="Rockbox",
        model="Y1",
        repo="y1-community/rockbox-y1",
        package_name="Rockbox",
        derivative="y1-community/y1-stock-rom",
    )

    # With hardware_type="A" the tracked package is compatible, so 2.5.5's
    # sanity check has nothing to ask: an update carries the data forward.
    with patch("src.keep_data._packages", return_value=[orig_pkg, rockbox_pkg]):
        assert keep_data.tracked_is_compatible("Y1", orig_pkg, settings=temp_settings, hardware_type="A") is True
        assert keep_data.needs_confirmation("Y1", orig_pkg, settings=temp_settings, hardware_type="A") is False

        # On Type B nothing is tracked, so the question is asked.
        assert keep_data.tracked_is_compatible("Y1", orig_pkg, settings=temp_settings, hardware_type="B") is None
        assert keep_data.needs_confirmation("Y1", orig_pkg, settings=temp_settings, hardware_type="B") is True


def test_manifest_duplicate_derivatives_list_the_keep_data_family():
    """A malformed duplicate attribute must not hide the whole derivative family."""
    from src.manifest import parse_manifest_xml

    xml = """<slidia>
      <package name="Original Software" repo="y1-community/y1-stock-rom"
               device="Y1" type="img" handler="Custom Firmware" />
      <package name="Better-Y" repo="Leska1337/better-Y" device="Y1"
               type="img" handler="Custom Firmware"
               derivative="y1-community/y1-stock-rom"
               derivative="y1-community/y1-stock-rom" />
      <package name="Koensayr" repo="ryan-specter/koensayr-auto" device="Y1"
               type="img" handler="Custom Firmware"
               derivative="y1-community/y1-stock-rom"
               derivative="y1-community/y1-stock-rom" />
    </slidia>"""

    entries = parse_manifest_xml(xml)
    assert [entry.name for entry in entries] == [
        "Original Software",
        "Better-Y",
        "Koensayr",
    ]
    with patch("src.keep_data._packages", return_value=entries):
        for entry in entries:
            assert keep_data.compatible_software_names(entry, "Y1") == [
                "Original Software",
                "Better-Y",
                "Koensayr",
            ]


def test_remote_flags_candidate_filenames():
    """Verify that candidate filenames correctly generate compound platform, arch, and region patterns."""
    names = remote_flags.candidate_flag_filenames("no-term-install")
    assert "no-term-install.txt" in names

    # Should contain platform or arch variants
    has_platform_or_arch = any(
        "macos" in fn or "windows" in fn or "linux" in fn or "arm64" in fn or "x86_64" in fn
        for fn in names
    )
    assert has_platform_or_arch


def test_remote_rescue_flags_evaluations(temp_settings):
    """Verify that remote flags alter app behaviours appropriately."""
    remote_flags.clear_remote_flags_cache()

    # 1. null_rbtf / rockbox themes auto-install gating
    with patch("src.remote_flags._probe_remote_file", side_effect=lambda fn: "null_rbtf" in fn):
        remote_flags.clear_remote_flags_cache()
        assert theme_sync.themes_auto_install_enabled(temp_settings) is False

    # 2. When null_rbtf is absent, default is True
    with patch("src.remote_flags._probe_remote_file", return_value=False):
        remote_flags.clear_remote_flags_cache()
        # Clean settings key so default applies
        temp_settings.remove(f"{theme_sync._GROUP_THEME_SYNC}/{theme_sync._KEY_ENABLED}")
        assert theme_sync.themes_auto_install_enabled(temp_settings) is True

    # 3. no-term-install flag disables terminal install
    with patch("src.remote_flags._probe_remote_file", side_effect=lambda fn: "no-term-install" in fn):
        remote_flags.clear_remote_flags_cache()
        assert device_tracking.terminal_install_enabled(temp_settings) is False

    # 4. donations_off flag disables donation UI
    with patch("src.remote_flags._probe_remote_file", side_effect=lambda fn: "donations_off" in fn):
        remote_flags.clear_remote_flags_cache()
        assert device_tracking.is_donation_ui_disabled(temp_settings) is True

    # 5. disable_device_reminders flag disables reminders
    with patch("src.remote_flags._probe_remote_file", side_effect=lambda fn: "disable_device_reminders" in fn):
        remote_flags.clear_remote_flags_cache()
        assert device_tracking.is_device_reminder_enabled("Y1", temp_settings) is False

    # 6. background_off flag disables background running
    with patch("src.remote_flags._probe_remote_file", side_effect=lambda fn: "background_off" in fn):
        remote_flags.clear_remote_flags_cache()
        from src.tray import run_in_background_enabled
        assert run_in_background_enabled(temp_settings) is False

    # 7. background_on flag enables background running
    with patch("src.remote_flags._probe_remote_file", side_effect=lambda fn: "background_on" in fn):
        remote_flags.clear_remote_flags_cache()
        from src.tray import run_in_background_enabled
        # macOS parks the mode behind the platform gate; the flag precedence is
        # still what the shipping hosts run.
        with patch("src.tray.background_mode_supported", return_value=True):
            assert run_in_background_enabled(temp_settings) is True
    remote_flags.clear_remote_flags_cache()


def test_completion_screen_layout_and_post_install_buttons(app, monkeypatch):
    """Verify that when extra steps are needed:
    - Completion text, the guide hint and View Guide sit ABOVE the appeal
    - The appeal reads as one letter: message, picture and signature, then buttons
    - If post_install2 URL is present, button reads 'View Guide + Donate' and opens post_install2
    - If donations are disabled, 'donations=false' is appended to View Guide URLs
    """
    from src.ui.flash_page import FlashPage
    from src.i18n import tr, translator

    translator().set_language("en")
    opened = []
    monkeypatch.setattr("src.browser.open_browser", opened.append)

    fp = FlashPage()
    try:
        # Case A: post_install2 present -> Button reads "View Guide + Donate"
        monkeypatch.setattr("src.player_update_check.is_donation_ui_disabled", lambda *_: False)
        fp.set_post_install_urls(
            post_install="https://innioasis.app/guide/reborn",
            post_install_2="https://innioasis.app/guide/reborn-donate",
        )
        fp.set_device_done(donations_shown=True)
        fp.show_completion_appeal(True)

        assert fp._coffee_btn.text() == "View Guide + Donate"
        assert not fp._view_guide_btn.isHidden()
        assert not fp._completion_guide_hint.isHidden()
        # The sentence announces the extra steps; the button is the action.
        assert 'href="post-install"' not in fp._completion_guide_hint.text()
        assert tr("post_install_explain_completion") in fp._completion_guide_hint.text()
        opened.clear()
        fp._view_guide_btn.click()
        assert opened[-1] == "https://innioasis.app/guide/reborn"
        # One letter layout: the message stays above the signature, guide or not.
        assert not fp._appeal_body.isHidden()
        assert not fp._appeal_signature_row.isHidden()
        assert fp._appeal_signature.text() == "~ Ryan · Maintainer of Updater CE"
        # The heading carries the firmware icon (or its squircle stand-in).
        assert fp._intro.icon is not None
        assert not fp._intro.icon.pixmap().isNull()
        layout = fp._appeal.layout()
        # View Guide belongs to the extra-steps instruction, between its
        # sentence and the appeal copy, not in the donation button row.
        assert layout.indexOf(fp._completion_guide_hint) < layout.indexOf(fp._view_guide_btn)
        assert layout.indexOf(fp._view_guide_btn) < layout.indexOf(fp._appeal_body)
        assert layout.indexOf(fp._appeal_body) < layout.indexOf(fp._appeal_signature_row)
        assert layout.indexOf(fp._appeal_signature_row) < layout.indexOf(fp._appeal_dont)

        # Coffee button opens post_install2
        fp._open_coffee_page()
        assert opened[-1] == "https://innioasis.app/guide/reborn-donate"

        # View Guide button opens post_install
        fp._open_view_guide()
        assert opened[-1] == "https://innioasis.app/guide/reborn"

        # Case B: When donations are disabled, donations=false is appended to URL
        monkeypatch.setattr("src.player_update_check.is_donation_ui_disabled", lambda *_: True)
        fp._open_view_guide()
        assert "donations=false" in opened[-1]
        assert opened[-1] == "https://innioasis.app/guide/reborn?donations=false"

        # Case C: No extra steps (no post-install URLs)
        fp.set_post_install_urls("", "")
        fp.show_completion_appeal(True)
        assert fp._completion_guide_hint.isHidden()
        assert fp._view_guide_btn.isHidden()
        assert not fp._appeal_body.isHidden()
        assert not fp._appeal_signature_row.isHidden()
        assert fp._coffee_btn.text() == "Donate on Ko-Fi"
    finally:
        fp.deleteLater()
