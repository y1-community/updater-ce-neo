"""Tests for AppUpdateToast and its precedence over device update reminders."""

import sys
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.ui.widgets import AppUpdateToast
from src.ui.select_page import SelectPackagePage
from src.updates import UpdateInfo


def test_app_update_toast_widget():
    app = QApplication.instance() or QApplication(sys.argv)
    toast = AppUpdateToast()
    assert not toast.isVisible()

    info = UpdateInfo(tag="v3.1.0", body="Test notes")
    toast.set_update(info)
    assert not toast.isHidden()
    assert "v3.1.0 is available" in toast.text()

    action_btn = toast.findChild(QPushButton, "appUpdateToastAction")
    close_btn = toast.findChild(QPushButton, "appUpdateToastClose")
    assert action_btn is not None
    assert close_btn is not None

    action_clicked = []
    toast.action_clicked.connect(lambda: action_clicked.append(True))
    action_btn.click()
    assert action_clicked == [True]

    close_clicked = []
    toast.close_clicked.connect(lambda: close_clicked.append(True))
    close_btn.click()
    assert close_clicked == [True]


def test_app_update_toast_precedence_over_device_update():
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()
    page.show()

    # Initially neither toast is shown
    assert not page._app_update_prompt.isVisible()
    assert not any(t.isVisible() for t in page._model_toasts)

    # 1. Device update arrives
    device_updates = [{
        "model": "Y1",
        "software_name": "Rockbox",
        "latest_tag": "v1.2",
        "latest_release": {"name": "Rockbox v1.2"},
    }]
    page.show_update_offers(device_updates)
    app.processEvents()  # layout shows new child toasts on the next loop pass
    assert [t.isVisible() for t in page._model_toasts] == [True]
    assert not page._app_update_prompt.isVisible()

    # 2. App update arrives while device update is showing
    # App update takes precedence in the same toast spot!
    app_info = UpdateInfo(tag="v3.2.0")
    page.show_app_update(app_info)
    assert page._app_update_prompt.isVisible()
    assert not any(t.isVisible() for t in page._model_toasts)  # Hidden by precedence

    # 3. Dismiss app update toast
    # Device update is shown in that spot again!
    page._app_update_prompt.findChild(QPushButton, "appUpdateToastClose").click()
    app.processEvents()
    assert not page._app_update_prompt.isVisible()
    assert [t.isVisible() for t in page._model_toasts] == [True]
    page.close()


def test_app_update_toast_action_signal():
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()
    page.show()

    received = []
    page.app_update_clicked.connect(lambda info: received.append(info))

    app_info = UpdateInfo(tag="v3.2.0")
    page.show_app_update(app_info)
    assert page._app_update_prompt.isVisible()

    page._app_update_prompt.findChild(QPushButton, "appUpdateToastAction").click()
    assert len(received) == 1
    assert received[0].version == "3.2.0"
    page.close()


def test_main_window_update_during_install_shows_toast_instead_of_dialog(monkeypatch):
    import src.ui.main_window as mw
    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()

    dialog_shown = []
    class _StubDlg(QWidget):
        def __init__(self, *args, **kwargs):
            super().__init__()
            dialog_shown.append(True)
    monkeypatch.setattr(mw, "UpdateAvailableDialog", _StubDlg)

    # Simulate active download/install run
    w._download_active = True
    assert w._install_run_active()

    app_info = UpdateInfo(tag="v3.2.0")
    w._on_update_check_done(app_info, manual=False)

    # Verify no full dialog was presented
    assert not dialog_shown
    # Verify toast was presented in SelectPackagePage instead
    assert w._select_page._app_update_prompt.isVisible()
    w.close()

