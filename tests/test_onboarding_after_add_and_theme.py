"""The journey after the first player, live macOS appearance, and named copy.

Once a player is on record the onboarding card says what the reminders cover
for those models and how to finish. macOS appearance changes are picked up
live, not only at launch. The install-first confirmation names the firmware
that is being replaced whenever the app knows it.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QThread, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

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


class _NoReleaseWorker(QThread):
    loaded = Signal(object)

    def __init__(self, package, model, selected_type):
        super().__init__()

    def run(self):
        return


def test_the_journey_switches_its_copy_once_a_player_exists(monkeypatch):
    from src.i18n import tr

    app, win = _window()
    page = win._settings_page
    installs = {}
    try:
        monkeypatch.setattr(settings_mod, "_AddDeviceReleasesWorker", _NoReleaseWorker)
        monkeypatch.setattr(
            settings_mod.device_tracking,
            "get_all_device_installs",
            lambda *a, **k: installs,
        )
        assert page.begin_onboarding()
        app.processEvents()
        # Nothing added yet: the introduction is what the card says.
        assert page._onboarding_body.text() == tr("onboarding_body")
        assert page._onboarding_footer.isHidden()

        installs["Y1"] = {"model": "Y1", "software_name": "Original Software"}
        page.refresh_settings()
        app.processEvents()

        # A player on record: the copy describes the reminders for the list
        # above and tells the user how to finish.
        assert page._onboarding_body.text() == tr("onboarding_after_add_body")
        assert page._onboarding_body.text().startswith("We'll notify you")
        assert not page._onboarding_footer.isHidden()
        assert "carried out correctly" in page._onboarding_footer.text()
        assert "simply click Done" in page._onboarding_footer.text()
    finally:
        page.end_onboarding()
        _close(win)


def test_the_journey_window_cannot_be_shrunk_under_its_form(monkeypatch):
    app, win = _window()
    page = win._settings_page
    installs = {}
    try:
        monkeypatch.setattr(settings_mod, "_AddDeviceReleasesWorker", _NoReleaseWorker)
        monkeypatch.setattr(
            settings_mod.device_tracking,
            "get_all_device_installs",
            lambda *a, **k: installs,
        )
        assert page.begin_onboarding()
        win._onboarding_active = True
        app.processEvents()

        floor = win._settings_form_floor_height()
        # The welcome form is taller than the empty card's strip, so the window
        # is not free to shrink under the rows the user still has to fill in.
        assert floor > 168
        win._adjust_window_geometry()
        assert win.minimumHeight() > 168
    finally:
        win._onboarding_active = False
        page.end_onboarding()
        _close(win)


def test_the_add_player_form_keeps_the_window_tall_outside_the_journey(monkeypatch):
    app, win = _window()
    page = win._settings_page
    try:
        monkeypatch.setattr(settings_mod, "_AddDeviceReleasesWorker", _NoReleaseWorker)
        page.end_onboarding()
        page._open_add_device_panel()
        app.processEvents()

        # No journey and no focused card: the ordinary Settings column with
        # the open form. Its rows still win over the empty card's strip.
        assert page.focused_card() is None
        floor = win._settings_form_floor_height()
        assert floor > 168
        win._adjust_window_geometry()
        assert win.minimumHeight() > 168
    finally:
        page._hide_add_device_panel()
        _close(win)


def test_macos_appearance_prefers_appkit_over_the_defaults_key(monkeypatch):
    from src.ui import dark

    monkeypatch.setattr(dark, "_macos_appkit_dark", lambda: True)
    monkeypatch.setattr(dark.sys, "platform", "darwin")
    assert dark.is_system_dark_mode() is True


def test_the_macos_appearance_notification_restyles(monkeypatch):
    from src.ui.dark import ThemeWatcher

    calls = []
    watcher = SimpleNamespace(schedule=lambda *a: calls.append(a))
    ThemeWatcher._macos_appearance_changed(watcher)
    assert calls


def test_the_install_first_copy_names_the_firmware_being_replaced():
    from src.ui.main_window import install_first_prerequisite_body

    class _Requirement:
        when_repos = ("classy/reborn",)

        def label(self):
            return "Original Software"

    journey = SimpleNamespace(
        requirement=_Requirement(),
        target_name="Reborn",
        running_name="",
    )
    body = install_first_prerequisite_body(journey, "Y2", "Rockbox")
    assert "straight over Rockbox" in body
    assert "software already on" not in body

    # The journey's own answer wins over the tracked record.
    journey.running_name = "Original Software"
    assert "straight over Original Software" in install_first_prerequisite_body(
        journey, "Y2", "Rockbox"
    )
