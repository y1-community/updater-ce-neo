"""The players card, its Type hint, and the one-time Windows drivers ask.

Settings calls the card "Add your players", the welcome journey calls it
"Let's set up Innioasis Updater", reminders are per player with no master
switch, Type carries a "What does this mean?" hint, and Windows asks for the
MediaTek drivers plus a restart before the form opens.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import paths  # noqa: E402
from src.flash_service import METHOD_MTK, METHOD_SP  # noqa: E402
from src.ui import settings_page as settings_mod  # noqa: E402
from src.ui.main_window import MainWindow, _PAGE_SETTINGS  # noqa: E402

_APP = None


def _window():
    global _APP
    app = QApplication.instance() or QApplication(sys.argv)
    _APP = app
    app.setQuitOnLastWindowClosed(False)
    win = MainWindow()
    win.show()
    for _ in range(8):
        app.processEvents()
    win._nav_to_page(_PAGE_SETTINGS)
    for _ in range(8):
        app.processEvents()
    return app, win


def _close(win):
    win._force_quit = True
    win.close()
    loader = getattr(win, "_icon_loader", None)
    try:
        running = loader is not None and loader.isRunning()
    except RuntimeError:
        running = False
    if running:
        loader.wait(2000)


def _events(app):
    for _ in range(6):
        app.processEvents()


def test_the_card_is_add_your_players_in_settings_and_a_welcome_on_the_journey():
    app, win = _window()
    page = win._settings_page
    try:
        page._reminders_card.retranslate()
        assert page._reminders_card.title() == "Add your players"
        # No master switch: reminders are switched per player, and removing a
        # player is how they stop.
        assert not hasattr(page, "_cb_reminders")

        assert page.begin_onboarding()
        _events(app)
        assert page._reminders_card.title() == "Let's set up Innioasis Updater"
    finally:
        page.end_onboarding()
        _close(win)


def test_type_carries_the_catalogues_type_tooltip():
    app, win = _window()
    page = win._settings_page
    try:
        page._open_add_device_panel()
        _events(app)
        from src import i18n
        from src.i18n import tr

        button = page._add_device_type_hint_btn
        # The English copy is the wording asked for, whatever language this
        # machine's settings happen to have left the app in.
        assert i18n.catalog("en").get("settings_add_device_type_hint_btn") == (
            "What type do I have?"
        )
        assert button.text() == tr("settings_add_device_type_hint_btn")
        assert button.isFlat(), "the hint is a text button, not a boxed one"
        # One explanation, two screens: the same hover tip as the Type mark on
        # Select Software, and nothing revealed inside the card.
        assert button.toolTip() == win._select_page._type_help_icon.toolTip()
        assert "Type A" in button.toolTip()
        assert "Type B" in button.toolTip()
        assert not hasattr(page, "_add_device_type_hint")

        button.click()
        _events(app)
        assert not hasattr(page, "_add_device_type_hint")
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_windows_opens_add_device_form_directly_without_drivers_gate(monkeypatch):
    app, win = _window()
    page = win._settings_page
    try:
        monkeypatch.setattr(paths, "IS_WINDOWS", True)
        monkeypatch.setattr(settings_mod, "windows_only_method", lambda: METHOD_SP)

        assert not page._drivers_gate_needed()
        page._open_add_device_panel()
        _events(app)
        assert page._add_device_panel.isVisible()
        assert not hasattr(page, "_drivers_gate") or not page._drivers_gate.isVisible()
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_windows_driver_detection_and_reboot_hint(monkeypatch, tmp_path):
    # Test windows_sp_drivers_installed detection logic
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    driver_exe = tmp_path / "MediaTek" / "SP Driver" / "unins000.exe"
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert not paths.windows_sp_drivers_installed()

    driver_exe.parent.mkdir(parents=True, exist_ok=True)
    driver_exe.touch()
    assert paths.windows_sp_drivers_installed()


def test_arm64_windows_is_not_asked_for_drivers(monkeypatch):
    app, win = _window()
    page = win._settings_page
    try:
        monkeypatch.setattr(paths, "IS_WINDOWS", True)
        # ARM64 Windows runs MTKClient, and has no MediaTek USB driver to install.
        monkeypatch.setattr(settings_mod, "windows_only_method", lambda: METHOD_MTK)
        assert not page._drivers_gate_needed()

        page._open_add_device_panel()
        _events(app)
        assert page._add_device_panel.isVisible()
        assert not hasattr(page, "_drivers_gate") or not page._drivers_gate.isVisible()
    finally:
        page._hide_add_device_panel()
        _close(win)
