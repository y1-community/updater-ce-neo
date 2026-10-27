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

import pytest
from PySide6.QtCore import QObject, QSettings, QTimer, Signal


@pytest.fixture(autouse=True)
def _default_user_level(monkeypatch):
    """Each test starts with Advanced Settings off and Linux platform flags."""
    import platform
    import src.paths as paths_mod
    import src.linux_sp_flash as linux_mod

    monkeypatch.setattr(paths_mod, "IS_WINDOWS", False)
    monkeypatch.setattr(paths_mod, "IS_MAC", False)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(linux_mod, "arch_supported", lambda: True)
    QSettings("innioasis", "updater").remove("advanced_settings")
    yield
    QSettings("innioasis", "updater").remove("advanced_settings")


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

    from src.i18n import tr, translator
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
        assert page._checker_status_lbl.text() in {
            tr("linux_prep_ready_friendly"),
            tr("linux_prep_ready"),
        }
        assert page._sp_prep_worker is None
        # Already ready: nothing to fix, so the checker is not run a second time.
        assert calls["fix"] == 0 and calls["checked"] == 0
    finally:
        page.deleteLater()
        app.processEvents()


def test_root_step_is_asked_on_the_card(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
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

    from src.i18n import tr, translator
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
        assert page._checker_status_lbl.text() in {
            tr("linux_prep_ready_friendly"),
            tr("linux_prep_ready"),
        }
    finally:
        page.deleteLater()
        app.processEvents()


def test_missing_device_is_informational_not_a_warning():
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    report = _ready_report()
    report["items"].append({
        "key": "connected_device",
        "status": "info",
        "badge": "Not Detected",
        "detail": "No MediaTek device is currently connected.",
        "can_fix": False,
    })
    page = SettingsPage()
    try:
        page._render_sp_report(report)
        app.processEvents()
        assert page._checker_status_lbl.text() in {
            tr("linux_prep_ready_friendly"),
            tr("linux_prep_ready"),
        }
        assert page._checker_status_lbl.property("cssClass") != "warning-banner"
    finally:
        page.deleteLater()
        app.processEvents()


def test_friendly_copy_hides_tool_names_until_advanced():
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    from PySide6.QtCore import QSettings

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    # The Advanced flag is persisted to the real user config; start from the default.
    QSettings("innioasis", "updater").remove("advanced_settings")
    page = SettingsPage()
    try:
        assert page._checker_card.title() == "Prepare your computer"
        assert page._checker_desc.text() == (
            "Gets your computer ready to connect to your player and install software updates reliably."
        )
        page.reveal_advanced_methods()
        assert "SP Flash Tool" in page._checker_card.title()
        assert "serial permissions" in page._checker_desc.text()
    finally:
        QSettings("innioasis", "updater").remove("advanced_settings")
        page.deleteLater()
        app.processEvents()


def test_failed_download_stays_on_the_card(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
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


def test_successful_prep_emits_completion_but_failure_does_not(monkeypatch):
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    _install_fake_stack(monkeypatch, _ready_report())
    page = SettingsPage()
    completed = []
    page.linux_prep_completed.connect(lambda: completed.append(True))
    try:
        page._on_sp_prep_finished(True, "staged", _ready_report())
        assert completed == [True]
        # The root offer appears on the card; the user declines it.
        QTimer.singleShot(0, lambda: page._checker_card._confirm_reject.click())
        page._on_sp_prep_finished(True, "staged", _needs_root_report())
        assert completed == [True], "unfixed permission issues should keep setup available"
        page._on_sp_prep_finished(False, "download failed", {})
        assert completed == [True], "failed staging must leave the setup shortcut available"
    finally:
        page.deleteLater()
        app.processEvents()


def test_setup_flow_hides_gui_launcher_only_while_checker_is_focused(monkeypatch):
    from PySide6.QtWidgets import QApplication

    import src.sp_flash_gui as sp_gui
    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    monkeypatch.setattr(sp_gui, "is_sp_flash_gui_supported", lambda: True)
    page = SettingsPage()
    try:
        page.reveal_advanced_methods()
        page.set_sp_gui_available(True)
        assert not page._btn_launch_sp.isHidden()
        page.show_card_alone(page._checker_card)
        assert page._btn_launch_sp.isHidden()
        page.clear_card_focus()
        assert not page._btn_launch_sp.isHidden()
    finally:
        page.deleteLater()
        app.processEvents()


def test_linux_setup_shortcut_has_distinct_focus_from_full_settings(monkeypatch, tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    import src.paths as paths_mod
    import src.sp_flash_gui as sp_gui
    import src.ui.main_window as main_window_module
    from src.i18n import translator
    from src.ui.main_window import MainWindow, _PAGE_SETTINGS

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    monkeypatch.setattr(main_window_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(paths_mod, "IS_MAC", False)
    monkeypatch.setattr(sp_gui, "is_sp_flash_gui_supported", lambda: False)
    window = MainWindow()
    window.settings = QSettings(str(tmp_path / "linux-focus.ini"), QSettings.Format.IniFormat)
    page = window._settings_page
    original_start = page.start_sp_flash_prep
    page.start_sp_flash_prep = lambda: True
    try:
        window._show_linux_setup()
        assert window._stack.currentIndex() == _PAGE_SETTINGS
        assert window._settings_btn.isChecked()
        assert window._settings_btn.text() == "Linux Setup"
        assert window._linux_setup_btn.isHidden()
        assert window._nav_buttons["nav_select_package"][0].isHidden()
        assert not window._linux_setup_btn.isCheckable()
        assert page.focused_card() is page._checker_card
        assert page._btn_launch_sp.isHidden()

        window._show_full_settings()
        assert window._stack.currentIndex() == _PAGE_SETTINGS
        assert window._settings_btn.isChecked()
        assert window._settings_btn.text() == "Settings"
        assert not window._linux_setup_btn.isCheckable()
        assert page.focused_card() is None
    finally:
        page.start_sp_flash_prep = original_start
        window.close()
        app.processEvents()


def test_linux_setup_completion_returns_to_select_and_hides_shortcut(monkeypatch, tmp_path):
    from PySide6.QtCore import QObject, QSettings, Signal
    from PySide6.QtWidgets import QApplication

    import src.linux_sp_flash as linux
    import src.paths as paths_mod
    import src.sp_flash_gui as sp_gui
    import src.ui.main_window as main_window_module
    from src.i18n import translator
    from src.ui.main_window import MainWindow, _PAGE_SELECT, _PAGE_SETTINGS

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    monkeypatch.setattr(main_window_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(paths_mod, "IS_MAC", False)
    monkeypatch.setattr(sp_gui, "is_sp_flash_gui_supported", lambda: True)
    main_window_ref = []
    observed = []

    class FakeWorker(QObject):
        progress = Signal(int, str)
        finished = Signal(bool, str, dict)

        def __init__(self, parent=None, force_download=False):
            super().__init__(parent)

        def start(self):
            window = main_window_ref[0]
            observed.append((
                window._stack.currentIndex(),
                window._settings_btn.isChecked(),
                window._sp_flash_tool_btn.isHidden(),
                window._settings_page._btn_launch_sp.isHidden(),
                window._settings_page._checker_card.isHidden(),
            ))
            self.finished.emit(True, "staged", _ready_report())

    monkeypatch.setattr(linux, "LinuxStagingWorker", FakeWorker)
    window = MainWindow()
    main_window_ref.append(window)
    window.settings = QSettings(str(tmp_path / "linux-first-run.ini"), QSettings.Format.IniFormat)
    window._select_page.gui_firmware_target = lambda: {"kind": "cache"}
    window._settings_page.reveal_advanced_methods()
    window._linux_setup_btn.setVisible(True)
    window._linux_setup_btn.setEnabled(True)
    window._sync_sp_gui_button_visibility()
    try:
        assert not window._sp_flash_tool_btn.isHidden()
        window._linux_setup_btn.click()
        app.processEvents()

        assert observed == [(_PAGE_SETTINGS, True, True, True, False)]
        assert window._stack.currentIndex() == _PAGE_SELECT
        assert window._nav_buttons["nav_select_package"][0].isChecked()
        assert not window._settings_btn.isChecked()
        assert window._linux_setup_btn.isHidden()
        window._show_full_settings()
        assert window._settings_btn.isChecked(), "full Settings should highlight its own row"
        assert window._settings_page.focused_card() is None
        assert window.settings.value("linux_setup_completed", False, type=bool)
    finally:
        window.close()
        app.processEvents()


def test_prep_entry_point_shows_the_card_on_its_own(monkeypatch):
    """Linux Setup owns the page, so the card is the only one on it.

    The preparation is not a pop-out window: the other cards step aside so the
    window can be sized to this one.
    """
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    import src.paths as paths_mod
    import src.ui.settings_page as settings_mod

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    _install_fake_stack(monkeypatch, _ready_report())
    monkeypatch.setattr(paths_mod, "IS_MAC", False)
    monkeypatch.setattr(settings_mod, "is_mediatek_installer", lambda: False)
    page = SettingsPage()
    try:
        assert page.start_sp_flash_prep() is True
        page.show_card_alone(page._checker_card)
        app.processEvents()

        def others():
            return [c for c in page._cards_in_column() if c is not page._checker_card]

        assert page.focused_card() is page._checker_card
        assert page._checker_card.isHidden() is False
        page.set_sp_gui_available(True)
        assert page._btn_launch_sp.isHidden(), "the GUI launcher is not part of setup prep"
        assert [c.isHidden() for c in others()] == [True] * len(others())

        # The ordinary per-card rules run again on refresh; none may undo the
        # focus and bring the rest of the column back.
        page.refresh_settings()
        app.processEvents()
        assert page._checker_card.isHidden() is False
        assert [c.isHidden() for c in others()] == [True] * len(others())

        page.clear_card_focus()
        app.processEvents()
        assert page.focused_card() is None
    finally:
        page.deleteLater()
        app.processEvents()


if __name__ == "__main__":
    test_preparation_reports_on_the_card()
    test_root_step_is_asked_on_the_card()
    test_prep_entry_point_uses_the_card()
    test_failed_download_stays_on_the_card()
    test_missing_device_is_informational_not_a_warning()
    test_friendly_copy_hides_tool_names_until_advanced()
    test_successful_prep_emits_completion_but_failure_does_not()
    test_setup_flow_hides_gui_launcher_only_while_checker_is_focused()
    test_linux_setup_shortcut_has_distinct_focus_from_full_settings()
    test_linux_setup_completion_returns_to_select_and_hides_shortcut()
    print("All Linux prep card tests passed!")


def test_linux_access_is_a_wizard_stage_before_the_password_and_connect_prompt(monkeypatch):
    """The Linux access step waits for Next; the password prompt and the connect guidance follow it."""
    from PySide6.QtWidgets import QApplication

    from src.i18n import translator
    import src.linux_sp_flash as linux
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = {"fix": 0}

    def fake_fix():
        calls["fix"] += 1
        return True, "fixed"

    monkeypatch.setattr(linux, "silent_system_prep", lambda: None)
    monkeypatch.setattr(
        linux,
        "run_system_checker",
        lambda *a, **k: {"items": [{"key": "udev_rules", "status": "fail"}]},
    )
    monkeypatch.setattr(linux, "auto_fix_permissions", fake_fix)
    monkeypatch.setattr(MainWindow, "_needs_linux_device_prep", lambda self: True)
    monkeypatch.setattr(MainWindow, "_needs_device_prep", lambda self: True)

    window = MainWindow()
    window.show()
    started = []
    window.service.start_flash = lambda *a, **k: started.append(True)
    window.service.start_device_monitor = lambda *a, **k: None
    window._package_path = "/fake/rom.zip"
    window._package_name = "Original Software 3.2.1"
    window._package_model = "Y2"
    # The access step belongs to SP Flash Tool. Linux defaults to MTKClient
    # (no password), so this test pins the SP backend explicitly.
    window.settings.setValue("flash_method", "sp")
    window.settings.sync()
    window._flash_method = "sp"

    window._begin_flash_flow()
    assert window._linux_permission_pending
    assert window._flash_page._permission_prompt
    assert "permission" in window._flash_page._wait_prompt_label.text().lower()
    assert calls["fix"] == 0, "no password prompt before Next"
    assert not started

    window._on_linux_permission_next()
    assert calls["fix"] == 1
    assert not window._linux_permission_pending
    assert window._power_off_prompt is True, "connect guidance follows the access step"
    assert not started, "flash waits for the connect guidance Next"


def test_mtkclient_run_never_asks_for_a_system_password(monkeypatch):
    """Linux defaults to MTKClient, which needs no root rights and so must not
    show the Linux access/wizard stage or open a password prompt."""
    from PySide6.QtWidgets import QApplication

    import src.linux_sp_flash as linux
    from src.i18n import translator
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = {"fix": 0, "checked": 0}

    monkeypatch.setattr(linux, "silent_system_prep", lambda: None)
    monkeypatch.setattr(
        linux,
        "run_system_checker",
        lambda *a, **k: calls.__setitem__("checked", calls["checked"] + 1) or {"items": []},
    )

    def fake_fix():
        calls["fix"] += 1
        return True, "fixed"

    monkeypatch.setattr(linux, "auto_fix_permissions", fake_fix)
    monkeypatch.setattr(MainWindow, "_needs_linux_device_prep", lambda self: True)
    monkeypatch.setattr(MainWindow, "_needs_device_prep", lambda self: False)

    window = MainWindow()
    window.show()
    started = []
    window.service.start_flash = lambda *a, **k: started.append(True)
    window.service.start_device_monitor = lambda *a, **k: None
    window._package_path = "/fake/rom.zip"
    window._package_name = "Original Software 3.2.1"
    window._package_model = "Y2"
    window.settings.setValue("flash_method", "mtk")
    window.settings.sync()
    window._flash_method = "mtk"

    try:
        window._begin_flash_flow()
        assert window._flash_method == "mtk"
        assert not window._linux_permission_pending
        assert not getattr(window._flash_page, "_permission_prompt", False)
        assert calls["fix"] == 0, "MTKClient never opens the system password dialog"
        assert calls["checked"] == 0, "the SP Flash Tool checker is not part of an MTKClient run"
        assert started, "the MTKClient install starts without an access step"
    finally:
        window.settings.setValue("flash_method", "auto")
        window.close()
        app.processEvents()


def test_linux_setup_wizard_asks_to_reboot_after_group_fix_and_quits_on_decline(monkeypatch):
    """In the locked Linux Setup wizard state:
    - declining the superuser confirmation closes the app;
    - accepting runs auto_fix_permissions, then shows a single checkmark button
      asking the user to restart their computer, which closes the app when clicked.
    """
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    translator().set_language("en")
    calls = _install_fake_stack(monkeypatch, _needs_root_report())
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: quit_calls.append(True)))

    page = SettingsPage()
    try:
        page.show_card_alone(page._checker_card)
        # 1. Declining the initial confirmation in setup_flow closes the app.
        QTimer.singleShot(0, lambda: page._checker_card._confirm_reject.click())
        page._btn_run_checker.click()
        app.processEvents()
        assert calls["fix"] == 0
        assert quit_calls == [True]

        # 2. Accepting runs auto_fix_permissions and presents the reboot checkmark step.
        quit_calls.clear()
        page._sp_prep_worker = None
        real_confirm = page._checker_card.run_confirmation
        confirm_steps = []

        def _auto_accept_confirm(body, accept, reject, *, note="", symbol="next"):
            confirm_steps.append((body, accept, reject, symbol))
            QTimer.singleShot(0, lambda: page._checker_card._confirm_accept.click())
            return real_confirm(body, accept, reject, note=note, symbol=symbol)

        monkeypatch.setattr(page._checker_card, "run_confirmation", _auto_accept_confirm)
        page._btn_run_checker.click()
        app.processEvents()
        assert calls["fix"] == 1
        assert len(confirm_steps) == 2
        assert confirm_steps[0][0] == tr("linux_prep_fix_body_wizard")
        assert confirm_steps[0][3] == "next"
        assert confirm_steps[1][0] == tr("linux_prep_reboot_body")
        assert confirm_steps[1][2] == ""
        assert confirm_steps[1][3] == "check"
        assert quit_calls == [True]
    finally:
        page.deleteLater()
        app.processEvents()
