"""The background mode owns a start-at-login entry that opens only the tray."""

import os
import plistlib
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from src import login_item, single_instance, tray


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def test_windows_run_value_quotes_an_install_path_with_spaces(monkeypatch):
    monkeypatch.setattr(
        login_item,
        "_launch_command",
        lambda: [r"C:\Program Files\Innioasis Updater\InnioasisUpdater.exe", "--tray"],
    )
    assert login_item._windows_command() == (
        r'"C:\Program Files\Innioasis Updater\InnioasisUpdater.exe" --tray'
    )


def test_linux_exec_quotes_a_path_with_spaces(monkeypatch):
    monkeypatch.setattr(
        login_item,
        "_launch_command",
        lambda: ["/home/me/Updater CE/app", "--tray", "/home/me/100%"],
    )
    assert login_item._linux_exec_line() == (
        '"/home/me/Updater CE/app" --tray /home/me/100%%'
    )


def test_linux_appimage_uses_the_stable_file_path(tmp_path, monkeypatch):
    """An AppImage runs from a temporary mount; its login entry must not."""
    image = tmp_path / "Updater CE.AppImage"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("APPIMAGE", str(image))
    monkeypatch.setattr(sys, "executable", "/tmp/.mount_abc/updater", raising=False)

    assert login_item._launch_command() == [str(image), login_item.TRAY_ARG]


def test_macos_agent_round_trips_a_path_with_an_ampersand(tmp_path, monkeypatch):
    target = tmp_path / "com.innioasis.updater.plist"
    launcher = str(tmp_path / "A & B.app" / "Contents" / "MacOS" / "Updater")
    monkeypatch.setattr(login_item, "_macos_path", lambda: target)
    monkeypatch.setattr(login_item, "_launch_command", lambda: [launcher, "--tray"])
    monkeypatch.setattr(sys, "platform", "darwin")

    login_item.set_login_item(True)
    payload = plistlib.loads(target.read_bytes())
    assert payload["ProgramArguments"] == [launcher, "--tray"]
    assert payload["RunAtLoad"] is True
    assert payload["ProcessType"] == "Background"

    login_item.set_login_item(False)
    assert not target.exists()


def test_the_macos_agent_runs_in_the_gui_session(tmp_path, monkeypatch):
    target = tmp_path / "com.innioasis.updater.plist"
    monkeypatch.setattr(login_item, "_macos_path", lambda: target)
    monkeypatch.setattr(
        login_item,
        "_launch_command",
        lambda: [
            "/Applications/Innioasis Updater.app/Contents/MacOS/Innioasis Updater",
            "--tray",
        ],
    )
    monkeypatch.setattr(sys, "platform", "darwin")

    login_item.set_login_item(True)
    payload = plistlib.loads(target.read_bytes())
    # A menu bar app belongs to the logged-in GUI session; starting it in any
    # other session leaves it with no status item to reach it by.
    assert payload["LimitLoadToSessionType"] == "Aqua"
    login_item.set_login_item(False)
    assert not target.exists()


def test_every_host_ships_the_background_mode():
    """macOS keeps the menu bar mode; no host hides the switches any more."""
    for host in ("darwin", "win32", "linux"):
        assert tray.background_mode_supported(host) is True


def test_menu_bar_only_is_a_no_op_off_macos(monkeypatch):
    monkeypatch.setattr(tray.sys, "platform", "linux")
    tray.set_menu_bar_only(True)
    tray.set_menu_bar_only(False)


def test_linux_entry_exists_only_while_enabled(tmp_path, monkeypatch):
    target = tmp_path / "autostart" / "innioasis-updater.desktop"
    launcher = str(tmp_path / "Updater CE.AppImage")
    monkeypatch.setattr(login_item, "_linux_path", lambda: target)
    monkeypatch.setattr(login_item, "_launch_command", lambda: [launcher, "--tray"])
    monkeypatch.setattr(sys, "platform", "linux")

    login_item.set_login_item(True)
    text = target.read_text(encoding="utf-8")
    assert "Exec=" in text and "--tray" in text
    assert 'Updater CE.AppImage"' in text  # the space forces quoting
    assert "X-GNOME-Autostart-enabled=true" in text

    login_item.set_login_item(False)
    assert not target.exists()


def test_a_login_launch_shows_only_the_tray_or_nothing():
    from src.app import _startup_presentation

    assert _startup_presentation(False, True) == "window"
    assert _startup_presentation(False, False) == "window"
    assert _startup_presentation(True, True) == "tray"
    assert _startup_presentation(True, False) == "exit"


def test_a_start_at_login_launch_never_raises_another_copy(monkeypatch):
    app = _app()
    monkeypatch.setattr(single_instance, "acquire_windows_mutex", lambda: False)
    asked = []
    monkeypatch.setattr(
        single_instance,
        "_ask_running_copy_to_show",
        lambda *a, **k: asked.append(True) or True,
    )
    assert single_instance.ensure_single_instance(app, quiet=True) is False
    assert asked == []


def test_every_launch_reconciles_the_entry_with_the_saved_setting(monkeypatch):
    from src import app as app_module

    calls = []
    monkeypatch.setattr(
        "src.login_item.set_login_item", lambda enabled: calls.append(enabled)
    )
    monkeypatch.setattr("src.tray.run_in_background_setting", lambda: True)

    app_module._reconcile_login_item(None)
    assert calls == [True]

    # A portable run leaves the installed app's entry alone.
    calls.clear()
    app_module._reconcile_login_item(Path("fw_package.ini"))
    assert calls == []


def test_the_saved_background_setting_owns_the_entry(monkeypatch):
    # The remote-flag precedence still has to work on the hosts that ship the
    # mode; macOS parks it, so lift the gate for this check.
    monkeypatch.setattr(tray, "background_mode_supported", lambda: True)
    settings = QSettings("innioasis", "updater")
    settings.setValue(tray.RUN_IN_BACKGROUND_KEY, False)
    try:
        from src import remote_flags

        monkeypatch.setattr(
            remote_flags,
            "is_remote_flag_active",
            lambda name: name in ("background_on", "force_background"),
        )
        assert tray.run_in_background_enabled() is True
        assert tray.run_in_background_setting() is False
    finally:
        settings.remove(tray.RUN_IN_BACKGROUND_KEY)
        settings.sync()
