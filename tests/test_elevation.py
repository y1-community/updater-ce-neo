"""Installs can be asked to run with superuser rights (Settings switch).

Updater CE spawns every flash tool as a child process, so the preference is
applied by restarting the app itself once: the children then inherit the rights
with no second prompt and with stdout still attached. These tests cover the
preference, the platform probes, and the Settings switch that drives them —
never an actual elevation, which would ask the real user.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from src import elevation  # noqa: E402


class _FakeSettings:
    """A QSettings stand-in: honours the ``type`` keyword the real one takes."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, type=None):  # noqa: A002 - Qt's own name
        raw = self.values.get(key, default)
        if type is bool and not isinstance(raw, bool):
            if isinstance(raw, str):
                return raw.strip().lower() in ("1", "true", "yes", "on")
            return bool(raw)
        return raw

    def setValue(self, key, value):  # noqa: N802 - Qt's own name
        self.values[key] = value


class _RawSettings:
    """A settings object whose ``value`` has no ``type`` keyword at all."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's own name
        self.values[key] = value


class _NoSettings:
    """Not a settings object in any way."""


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


@pytest.fixture()
def stored_flag():
    """The real key, snapshotted and put back so the suite stays clean."""
    from PySide6.QtCore import QSettings

    settings = QSettings("innioasis", "updater")
    previous = settings.value(elevation.RUN_AS_ADMIN_KEY, None)
    try:
        yield settings
    finally:
        if previous is None:
            settings.remove(elevation.RUN_AS_ADMIN_KEY)
        else:
            settings.setValue(elevation.RUN_AS_ADMIN_KEY, previous)
        settings.sync()


def test_flag_round_trips_through_a_settings_object():
    fake = _FakeSettings()
    assert elevation.run_as_admin_enabled(fake) is False
    elevation.set_run_as_admin(fake, True)
    assert elevation.run_as_admin_enabled(fake) is True
    elevation.set_run_as_admin(fake, False)
    assert elevation.run_as_admin_enabled(fake) is False


def test_flag_round_trips_through_real_qsettings(stored_flag):
    elevation.set_run_as_admin(stored_flag, True)
    stored_flag.sync()
    from PySide6.QtCore import QSettings

    assert elevation.run_as_admin_enabled(QSettings("innioasis", "updater")) is True


@pytest.mark.parametrize("text", ["true", "TRUE", "1", "yes", "on"])
def test_truthy_strings_count_as_on(text):
    assert elevation.run_as_admin_enabled(_FakeSettings({elevation.RUN_AS_ADMIN_KEY: text})) is True


@pytest.mark.parametrize("text", ["", "0", "false", "no", "off"])
def test_falsey_strings_count_as_off(text):
    assert elevation.run_as_admin_enabled(_FakeSettings({elevation.RUN_AS_ADMIN_KEY: text})) is False


def test_a_settings_object_without_type_keyword_still_answers():
    assert elevation.run_as_admin_enabled(_RawSettings()) is False
    assert elevation.run_as_admin_enabled(_RawSettings({elevation.RUN_AS_ADMIN_KEY: "true"})) is True
    # A raw store hands the string through, so the tolerant reading applies
    # (the type=bool form above is QSettings' own conversion, which refuses it).
    assert (
        elevation.run_as_admin_enabled(
            _RawSettings({elevation.RUN_AS_ADMIN_KEY: " checked "})
        )
        is True
    )


def test_a_foreign_object_reads_as_off_and_never_raises():
    assert elevation.run_as_admin_enabled(_NoSettings()) is False
    assert elevation.run_as_admin_enabled(None) is False
    elevation.set_run_as_admin(_NoSettings(), True)  # must not raise
    elevation.set_run_as_admin(None, True)


def test_elevation_probes_are_booleans_on_this_machine():
    assert isinstance(elevation.is_elevated(), bool)
    assert elevation.is_elevated() is False, "the test process is not elevated"
    assert isinstance(elevation.can_elevate(), bool)
    if sys.platform == "win32":
        assert elevation.can_elevate() is True, "Windows can always ask through UAC"


def test_switch_words_are_platform_specific_and_present():
    label = elevation.label_text()
    description = elevation.description_text()
    assert label.strip() and description.strip()
    assert label != description
    if sys.platform == "win32":
        assert "administrator" in label.lower()
    else:
        assert "superuser" in label.lower()


def test_relaunch_argv_is_this_machine_s_own_command(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["launcher.py", "--simulate-macos"], raising=False)
    argv = elevation._relaunch_argv()
    assert argv[0] == sys.executable
    assert argv[1].endswith("launcher.py")
    assert argv[2:] == ["--simulate-macos", elevation.TAKE_OVER_ARG]


def test_relaunch_argv_of_a_frozen_build_is_the_executable(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "argv", ["UpdaterCE.exe", "--flag"], raising=False)
    assert elevation._relaunch_argv() == [sys.executable, "--flag", elevation.TAKE_OVER_ARG]


def test_settings_page_offers_the_switch_off_by_default(stored_flag):
    from src.ui.settings_page import SettingsPage

    stored_flag.remove(elevation.RUN_AS_ADMIN_KEY)
    app = _app()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(elevation, "can_elevate", lambda: True)
    monkeypatch.setattr(elevation, "is_elevated", lambda: False)
    try:
        page = SettingsPage()
        switch = page._cb_run_as_admin
        assert switch.isChecked() is False
        assert page._run_as_admin_status.isHidden() is True
        assert page._run_as_admin_restart_btn.isHidden() is True

        switch.setChecked(True)
        app.processEvents()
        assert elevation.run_as_admin_enabled(stored_flag) is True
        # Not elevated yet: the card says so and offers the restart.
        assert page._run_as_admin_status.isHidden() is False
        assert page._run_as_admin_restart_btn.isHidden() is False
        assert page.run_as_admin() is True

        switch.setChecked(False)
        app.processEvents()
        assert elevation.run_as_admin_enabled(stored_flag) is False
        assert page._run_as_admin_status.isHidden() is True
        assert page._run_as_admin_restart_btn.isHidden() is True
        page.deleteLater()
    finally:
        monkeypatch.undo()


def test_the_restart_button_reports_a_refused_request(stored_flag):
    from src.ui.settings_page import SettingsPage

    app = _app()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(elevation, "can_elevate", lambda: True)
    monkeypatch.setattr(elevation, "is_elevated", lambda: False)
    monkeypatch.setattr(
        elevation, "restart_as_admin", lambda: (False, "Administrator rights were declined.")
    )
    try:
        page = SettingsPage()
        page._cb_run_as_admin.setChecked(True)
        page._on_run_as_admin_restart()
        app.processEvents()
        assert "declined" in page._run_as_admin_status.text()
        page.deleteLater()
    finally:
        monkeypatch.undo()


def test_no_elevation_helper_disables_the_switch(stored_flag):
    from src.ui.settings_page import SettingsPage

    stored_flag.remove(elevation.RUN_AS_ADMIN_KEY)
    app = _app()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(elevation, "can_elevate", lambda: False)
    try:
        page = SettingsPage()
        assert page._cb_run_as_admin.isEnabled() is False
        assert page._run_as_admin_status.isHidden() is False
        assert page._run_as_admin_restart_btn.isHidden() is True
        page.deleteLater()
    finally:
        monkeypatch.undo()


def test_description_text_does_not_mention_linux_pkexec_on_windows_or_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    win_desc = elevation.description_text()
    assert "windows" in win_desc.lower()
    assert "pkexec" not in win_desc.lower()
    assert "linux" not in win_desc.lower()

    monkeypatch.setattr(sys, "platform", "darwin")
    mac_desc = elevation.description_text()
    assert "macos" in mac_desc.lower()
    assert "pkexec" not in mac_desc.lower()
    assert "linux" not in mac_desc.lower()

    monkeypatch.setattr(sys, "platform", "linux")
    linux_desc = elevation.description_text()
    assert "pkexec" in linux_desc.lower()


def test_windows_install_admin_prompt_never_shows_linux_copy(monkeypatch):
    """_ensure_elevated_for_install only runs off Linux; its prompt must not mention Linux."""
    import json
    import platform

    from PySide6.QtWidgets import QMessageBox

    from src import i18n
    from src.ui import main_window

    old_lang = i18n.translator().lang
    try:
        for code in ("en", "en-GB", "en-US"):
            i18n.translator().set_language(code)
            for key in (
                "install_needs_admin_title",
                "install_needs_admin_body",
                "install_needs_admin_body_mediatek",
            ):
                text = i18n.tr(key)
                assert "linux" not in text.lower(), (code, key, text)
                assert "continue without a password" not in text.lower(), (code, key, text)
    finally:
        i18n.translator().set_language(old_lang)

    # Every shipped language catalog must also be free of the overwritten Linux copy.
    catalog_dir = ROOT / "assets" / "i18n"
    for path in sorted(catalog_dir.glob("*.json")):
        if path.stem == "languages":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in (
            "install_needs_admin_title",
            "install_needs_admin_body",
            "install_needs_admin_body_mediatek",
        ):
            assert "linux" not in str(data.get(key, "")).lower(), (path.stem, key)

    captured = []

    def fake_warning(parent, title, body, buttons, default_button):
        captured.append((title, body))
        return QMessageBox.No

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(elevation, "run_as_admin_enabled", lambda settings: True)
    monkeypatch.setattr(elevation, "is_elevated", lambda: False)
    monkeypatch.delenv("QT_QPA_PLATFORM", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(QMessageBox, "warning", fake_warning)

    class _DummyWindow:
        def __init__(self, settings):
            self.settings = settings
            self._elevation_prompt_settled = False
            self.logs = []

        def _append_log(self, msg):
            self.logs.append(msg)

    # 1. With Show Advanced Settings OFF, a stale persisted run_as_admin=True
    #    must never trigger an elevation prompt in Updater CE.
    hidden = _DummyWindow(
        _FakeSettings({elevation.RUN_AS_ADMIN_KEY: True, "advanced_settings": False})
    )
    assert main_window.MainWindow._ensure_elevated_for_install(hidden) is True
    assert captured == []

    # 2. With Show Advanced Settings ON, the Windows prompt asks once using
    #    administrator-rights copy (never Linux copy), and declining settles it
    #    for the rest of the install run (device-prep Next, PMT 8502 retry, etc.).
    dummy = _DummyWindow(
        _FakeSettings({elevation.RUN_AS_ADMIN_KEY: True, "advanced_settings": True})
    )
    result = main_window.MainWindow._ensure_elevated_for_install(dummy)
    assert result is True
    assert len(captured) == 1
    title, body = captured[0]
    assert "administrator" in title.lower()
    assert "restart" in body.lower()
    assert "linux" not in body.lower()
    assert "password" not in body.lower()

    # Re-entering _begin_flash_flow (e.g. after PMT 8502 Format All confirm)
    # must not pop up the dialog a second time.
    assert main_window.MainWindow._ensure_elevated_for_install(dummy) is True
    assert len(captured) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
