"""Linux SP Flash Tool preparation lives in the Settings card, not a dialog.

The card keeps the app's screen real estate: the same staging worker runs
behind it, its progress and verdict land on one line on the card, and the
root step is asked for in place.
"""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QTimer, Signal


def _ready_report():
    return {
        "items": [
            {"key": "engine_files", "status": "ok", "badge": "Installed", "can_fix": True},
        ],
        "overall_ready": True,
        "can_auto_fix": False,
    }


def _needs_root_report():
    return {
        "items": [
            {"key": "engine_files", "status": "ok", "badge": "Installed", "can_fix": True},
            {
                "key": "udev_rules",
                "status": "warn",
                "badge": "Missing rules",
                "detail": "MediaTek USB rules are not installed.",
                "can_fix": True,
            },
        ],
        "overall_ready": False,
        "can_auto_fix": True,
    }


def _install_fake_stack(monkeypatch, report, *, ok=True, message="staged"):
    """Replace the staging worker and the dialog with recording stand-ins."""
    import src.linux_sp_flash as linux
    import src.ui.dialogs as dialogs

    calls = {"fix": 0, "checked": 0, "dialogs": 0}

    class FakeWorker(QObject):
        progress = Signal(int, str)
        finished = Signal(bool, str, dict)

        def __init__(self, parent=None, force_download=False):
            super().__init__(parent)

        def start(self):
            self.progress.emit(35, "downloading engine")
            self.finished.emit(ok, message, report)

    class NoDialog:
        def __init__(self, *a, **kw):
            calls["dialogs"] += 1
            raise AssertionError("preparation must not open the setup dialog")

    def fake_fix():
        calls["fix"] += 1
        return True, "fixed"

    def fake_checker(stage=None):
        calls["checked"] += 1
        return _ready_report()

    monkeypatch.setattr(linux, "LinuxStagingWorker", FakeWorker)
    monkeypatch.setattr(linux, "auto_fix_permissions", fake_fix)
    monkeypatch.setattr(linux, "run_system_checker", fake_checker)
    monkeypatch.setattr(dialogs, "LinuxSetupDialog", NoDialog)
    return calls


def test_preparation_reports_on_the_card(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = _install_fake_stack(monkeypatch, _ready_report())
    page = SettingsPage()
    try:
        page._checker_card.setVisible(True)
        assert page._checker_status_lbl.isHidden(), "the line is empty until there is news"

        page._btn_run_checker.click()
        app.processEvents()

        assert calls["dialogs"] == 0
        assert page._btn_run_checker.isEnabled(), "the button comes back when the run ends"
        assert page._checker_status_lbl.isHidden() is False
        assert page._checker_status_lbl.text() == "Ready — SP Flash Tool is prepared."
        assert page._sp_prep_worker is None
        # Already ready: nothing to fix, so the checker is not run a second time.
        assert calls["fix"] == 0 and calls["checked"] == 0
    finally:
        page.deleteLater()
        app.processEvents()


def test_root_step_is_asked_on_the_card(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = _install_fake_stack(monkeypatch, _needs_root_report())
    page = SettingsPage()
    try:
        page._checker_card.setVisible(True)
        QTimer.singleShot(0, lambda: page._checker_card._confirm_accept.click())
        page._btn_run_checker.click()
        app.processEvents()
        assert calls["fix"] == 1
        assert calls["checked"] == 1
        assert page._checker_status_lbl.text() == "Access granted — SP Flash Tool is ready."

        # Declining leaves the offer on the card instead of removing anything.
        calls["fix"] = 0
        page._sp_prep_worker = None
        QTimer.singleShot(0, lambda: page._checker_card._confirm_reject.click())
        page._btn_run_checker.click()
        app.processEvents()
        assert calls["fix"] == 0
        assert page._checker_status_lbl.text() == "Access was not granted — flashing needs it."
        assert calls["dialogs"] == 0
    finally:
        page.deleteLater()
        app.processEvents()


def test_prep_entry_point_uses_the_card(monkeypatch):
    """The Linux entry point reveals the card and starts the same preparation."""
    from PySide6.QtWidgets import QApplication

    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    import src.paths as paths_mod
    import src.ui.settings_page as settings_mod

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = _install_fake_stack(monkeypatch, _ready_report())
    # The card is the Linux (and Windows) entry point; on macOS the page hides
    # it because there is no SP Flash Tool to prepare.
    monkeypatch.setattr(paths_mod, "IS_MAC", False)
    monkeypatch.setattr(settings_mod, "is_mediatek_installer", lambda: False)
    page = SettingsPage()
    try:
        page.start_sp_flash_prep()
        app.processEvents()
        assert calls["dialogs"] == 0
        assert page._checker_card.isHidden() is False
        assert page._checker_status_lbl.isHidden() is False
        assert page._checker_status_lbl.text() == "Ready — SP Flash Tool is prepared."
    finally:
        page.deleteLater()
        app.processEvents()


def test_failed_download_stays_on_the_card(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = _install_fake_stack(monkeypatch, {}, ok=False, message="github unreachable")
    page = SettingsPage()
    try:
        page._checker_card.setVisible(True)
        page._btn_run_checker.click()
        app.processEvents()
        assert calls["dialogs"] == 0
        assert page._btn_run_checker.isEnabled()
        assert page._checker_status_lbl.text() == "Setup issue: github unreachable"
        assert page._checker_status_lbl.property("cssClass") == "warning-banner"
    finally:
        page.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    test_preparation_reports_on_the_card()
    test_root_step_is_asked_on_the_card()
    test_prep_entry_point_uses_the_card()
    test_failed_download_stays_on_the_card()
    print("All Linux prep card tests passed!")
