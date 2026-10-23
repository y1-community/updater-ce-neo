"""Sidebar Help row, right-to-left mirroring, and the reminder card copy.

The rail carries one Help row whose three marks are icon-only buttons at the
same 16px size as every other sidebar glyph. A right-to-left language mirrors
the whole interface (rail on the right, rows mirrored) without flipping any
artwork. The reminder card names the software tracked per model.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

DISCUSSIONS = "https://github.com/y1-community/updater-ce-neo/discussions"


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


@pytest.fixture()
def window():
    from src.i18n import translator
    from src.ui.main_window import MainWindow, apply_interface_direction

    app = _app()
    translator().set_language("en")
    apply_interface_direction(app)
    w = MainWindow()
    # The window restores the stored language itself; put this test back on
    # English whatever the developer's settings say.
    translator().set_language("en")
    w._retranslate_all()
    apply_interface_direction(app)
    w.show()
    app.processEvents()
    try:
        yield w
    finally:
        translator().set_language("en")
        apply_interface_direction(app)
        w.close()
        app.processEvents()


def test_the_help_row_is_three_16px_marks_above_the_language_picker(window):
    from PySide6.QtCore import Qt

    row = window._help_row
    assert row is not None

    marks = window._help_icon_buttons
    assert set(marks) == {"discord", "reddit", "github"}
    for name, button in marks.items():
        assert button.text() == "", name
        assert button.iconSize().width() == 16, name
        assert button.iconSize().width() == window._support_btn.iconSize().width()
        assert not button.icon().isNull(), name
        assert button.toolTip(), name
        assert button.cursor().shape() == Qt.CursorShape.ArrowCursor

    tips = {name: button.toolTip() for name, button in marks.items()}
    assert tips["discord"] == "Join our Discord"
    assert tips["github"] == "Get help on GitHub"
    # No word beside them: the row is marks only, so it reads as the quiet end
    # of a rail that is otherwise a list of words.
    assert not hasattr(window, "_help_label")

    # Under the last rail row, above the language picker.
    layout = window._nav_panel.layout()
    row_index = layout.indexOf(row)
    assert row_index > layout.indexOf(window._support_btn)
    assert row_index > layout.indexOf(window._check_updates_btn)
    assert row_index < layout.indexOf(window._lang_label)
    assert layout.indexOf(window._lang_label) < layout.indexOf(window._lang_combo)


def test_the_github_mark_opens_the_project_discussions(window, monkeypatch):
    opened = []
    monkeypatch.setattr("src.browser.open_browser", lambda url: opened.append(url))
    window._github_btn.click()
    assert opened == [DISCUSSIONS]


def test_right_to_left_mirrors_the_rail_and_restores_it(window):
    from PySide6.QtCore import Qt

    from src.i18n import translator
    from src.ui.main_window import apply_interface_direction, page_title_alignment

    app = _app()

    def rail_left_of_content() -> bool:
        nav = window._nav_panel.mapTo(window, window._nav_panel.rect().topLeft()).x()
        stack = window._stack.mapTo(window, window._stack.rect().topLeft()).x()
        return nav < stack

    assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
    assert rail_left_of_content()

    translator().set_language("ar")
    apply_interface_direction(app)
    app.processEvents()
    assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
    assert not rail_left_of_content(), "the rail belongs on the right in RTL"
    assert page_title_alignment() & Qt.AlignmentFlag.AlignRight
    # The help marks keep their order while the row mirrors: Discord, which
    # leads left-to-right, sits at the right end of the mirrored row.
    def mark_x(button) -> int:
        return button.mapTo(window._help_row, button.rect().topLeft()).x()

    assert mark_x(window._discord_btn) > mark_x(window._github_btn)

    translator().set_language("en")
    apply_interface_direction(app)
    app.processEvents()
    assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
    assert rail_left_of_content()


def _tracking(monkeypatch, installs, enabled):
    from src.ui import settings_page as settings_module

    tracking = settings_module.device_tracking
    monkeypatch.setattr(
        tracking,
        "get_device_install",
        lambda model, settings=None: installs.get(model),
    )
    monkeypatch.setattr(
        tracking,
        "is_device_reminder_enabled",
        lambda model, settings=None: enabled.get(model, False),
    )
    monkeypatch.setattr(
        tracking,
        "set_device_reminder_enabled",
        lambda model, value, settings=None: enabled.__setitem__(model, bool(value)),
    )
    return tracking


def test_reminders_are_one_switch_per_tracked_model(monkeypatch):
    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = _app()
    translator().set_language("en")
    installs = {
        "Y1": {"software_name": "Rockbox"},
        "Y2": {"software_name": "Original Software"},
    }
    enabled = {"Y1": True, "Y2": False}
    _tracking(monkeypatch, installs, enabled)
    page = SettingsPage()
    try:
        assert page._reminders_card.title_label().text() == "Player Update Reminders"
        assert page._reminders_list.isVisible() or not page.isVisible()
        switches = page._reminder_switches
        assert set(switches) == {"Y1", "Y2"}
        assert page._switch_rows[switches["Y1"]][1].text() == "Rockbox on Y1"
        assert page._switch_rows[switches["Y2"]][1].text() == "Original Software on Y2"
        assert switches["Y1"].isChecked() is True
        assert switches["Y2"].isChecked() is False

        switches["Y2"].setChecked(True)
        assert enabled["Y2"] is True
        assert page._cb_reminders.isChecked() is True
    finally:
        page.deleteLater()
        app.processEvents()


def test_a_single_tracked_install_names_its_software_in_the_copy(monkeypatch):
    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = _app()
    translator().set_language("en")
    installs = {"Y1": {"software_name": "Rockbox"}}
    enabled = {"Y1": True}
    _tracking(monkeypatch, installs, enabled)
    page = SettingsPage()
    try:
        assert page._reminders_desc.text() == (
            "We'll let you know when a new version of Rockbox is released for your Y1."
        )
        # One tracked player keeps the single title-line switch.
        assert page._reminder_switches == {}
        assert page._reminders_list.isHidden()
    finally:
        page.deleteLater()
        app.processEvents()

    installs["Y1"] = {"software_name": "Original Software"}
    page = SettingsPage()
    try:
        assert page._reminders_desc.text() == (
            "We'll let you know when a new version of the software for your Y1 is released."
        )
    finally:
        page.deleteLater()
        app.processEvents()


def test_no_tracked_install_keeps_the_generic_sentence(monkeypatch):
    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = _app()
    translator().set_language("en")
    _tracking(monkeypatch, {}, {})
    page = SettingsPage()
    try:
        assert page._reminders_desc.text() == tr("settings_reminders_desc")
        assert page._reminder_switches == {}
        assert page._reminders_list.isHidden()
    finally:
        page.deleteLater()
        app.processEvents()


def test_release_notes_follow_a_live_theme_change():
    """The notes carry no baked-in colour; every word follows the tokens."""
    from src.ui import dark
    from src.ui.select_page import SelectPackagePage

    app = _app()
    page = SelectPackagePage()
    rel = {"body": "## What's Changed\n* Fixed audio playback\n\nSee [here](https://example.com)."}
    try:
        dark.apply_theme(app, force_dark=True)
        dark_html = page._render_release_notes(rel, as_html=True)
        assert dark.T().fg in dark_html
        dark.apply_theme(app, force_dark=False)
        light_html = page._render_release_notes(rel, as_html=True)
        assert dark.T().fg in light_html
        assert dark_html != light_html
    finally:
        page.deleteLater()
        app.processEvents()
