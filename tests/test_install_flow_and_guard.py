"""Wrong-model handling, Keep Data presentation, and the background hand-over.

These are regression pins for three things a user can see:
* both flash backends describe a wrong player with the same sentence, and the
  failure card offers Cancel (the ×) instead of Retry;
* the Keep Data tick never carries a firmware name in brackets, and the
  compatible software appears as its own caption;
* another copy of the app, from any folder, is found and asked to show itself,
  including through the show-request file when the socket cannot reach it.
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication

from src import single_instance
from src.flash_service import describe_backend_error, is_wrong_model_error
from src.i18n import tr


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


# -- wrong model ------------------------------------------------------------


def test_both_backends_say_the_same_wrong_model_sentence():
    for code in (
        "S_CHIP_TYPE_NOT_MATCH (3184)",
        "DEVICE_PACKAGE_MISMATCH",
        "SP_WRONG_MODEL",
    ):
        assert is_wrong_model_error(code)
        assert describe_backend_error(code) == tr("err_sp_wrong_model")
    assert "wrong model" in tr("err_sp_wrong_model").lower()
    assert "×" in tr("err_sp_wrong_model")


def test_the_power_off_card_is_not_replaced_by_a_backend_step(app):
    """Nothing may take the power-off card until the user has answered it."""
    from src.flash_service import STEP_WAITING
    from src.ui.main_window import MainWindow

    window = MainWindow()
    try:
        page = window._flash_page
        page.set_model("Y2")
        page.show_power_off_prompt()
        window._power_off_prompt = True

        window._on_step_changed(STEP_WAITING)

        assert page._wait_copy == "power_off"

        # Once the user confirms, the same line is allowed to move the card on.
        window._power_off_prompt = False
        window._on_step_changed(STEP_WAITING)
        assert page._wait_copy == "connect"
    finally:
        window._force_quit = True
        window.close()
        app.processEvents()


def test_cancel_returns_to_select_and_ignores_the_abandoned_run(app):
    """Cancelling leaves for the catalogue; the killed run's tail must not re-arm it."""
    from src.ui.main_window import MainWindow, _PAGE_SELECT

    window = MainWindow()
    try:
        window.show()
        window._package_path = "/tmp/cancelled_run.zip"
        window._package_name = "Cancelled Run"
        window._package_model = "Y2"
        window._show_device_prep()
        app.processEvents()
        assert window._power_off_prompt is True

        window._on_cancel_wait()
        app.processEvents()
        assert window._power_off_prompt is False
        assert window._stack.currentIndex() == _PAGE_SELECT

        # The stopped worker still reports a connect timeout as it unwinds.
        window._return_to_power_off_stage("sp")
        window._on_step_changed(0)
        app.processEvents()
        assert window._power_off_prompt is False
        assert window._stack.currentIndex() == _PAGE_SELECT
    finally:
        window._force_quit = True
        window.close()
        app.processEvents()


def test_a_wrong_model_failure_is_never_routed_to_a_reconnect_step(app):
    """A chip mismatch is final: no reconnect card, no backend switch."""
    from src.ui.main_window import MainWindow

    window = MainWindow()
    calls = []
    try:
        window._show_install_failure = lambda message: calls.append(message)
        window._on_flash_disconnected = lambda: calls.append("reconnect")
        window._offer_backend_failover = lambda code: calls.append(
            f"failover:{code}"
        ) or True
        window._recover_missing_firmware = lambda code: calls.append("cache") or True
        window._cache_recovery_needed = False

        window._handle_flash_failure("DEVICE_PACKAGE_MISMATCH")

        assert calls == ["DEVICE_PACKAGE_MISMATCH"]
    finally:
        window._force_quit = True
        window.close()
        app.processEvents()


def test_wrong_model_failure_offers_cancel_and_not_retry(app):
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_model("Y1")
        page.show_install_failure(
            "DEVICE_PACKAGE_MISMATCH",
            tr("err_sp_wrong_model"),
            wrong_model=True,
        )
        assert page._flash_retry_btn.isHidden()
        assert not page._cancel_btn.isHidden()
        assert page._cancel_btn.toolTip() == tr("btn_cancel")
        assert "×" in page._eta_label.text()
    finally:
        page.close()


def test_an_ordinary_failure_still_offers_retry(app):
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_model("Y1")
        page.show_install_failure("S_TIMEOUT (1042)")
        assert not page._flash_retry_btn.isHidden()
        assert not page._cancel_btn.isHidden()
    finally:
        page.close()


# -- Keep Data copy ---------------------------------------------------------


def test_keep_data_option_never_names_compatible_software(app):
    """The prep card offers the tick; it does not list releases it works from."""
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_model("Y2")
        page.set_has_userdata(True)
        page.show_power_off_prompt()
        page.set_keep_data_available(True, tr("flash_keep_data_option"))
        assert page._keep_data_cb.text() == tr("flash_keep_data_option")
        assert "(" not in page._keep_data_cb.text()
        # No caption carrying release names was built at all.
        assert not hasattr(page, "_keep_data_software")
        captions = [
            label.text()
            for label in page.findChildren(type(page._keep_data_microsd_note))
            if label.text()
        ]
        assert not any("Keep Data works from" in text for text in captions)
        assert not any("inniclassic" in text for text in captions)
    finally:
        page.close()


# -- another copy, wherever it lives ---------------------------------------


@pytest.mark.parametrize(
    "identity",
    [
        "InnioasisUpdater.exe",
        r"C:\Users\me\Desktop\2. Install Firmware.exe",
        r"C:\Temp\Updater CE.exe",
        "mediatek installer.exe",
        "/home/me/updater-ce-neo/.venv/bin/python src/app.py",
        "python3 /opt/updater/src/app.py --tray",
    ],
)
def test_a_copy_is_recognised_from_its_image_name_or_command_line(identity):
    assert single_instance._copy_pid_from_identity(4242, identity)


@pytest.mark.parametrize(
    "identity",
    [
        "explorer.exe",
        "python -c import pytest",
        "firefox",
        "UpdateAssistant.exe",
    ],
)
def test_unrelated_processes_are_not_mistaken_for_a_copy(identity):
    assert not single_instance._copy_pid_from_identity(4242, identity)


def test_the_show_request_file_wakes_the_running_copy(tmp_path, monkeypatch):
    request = tmp_path / "show.request"
    monkeypatch.setattr(single_instance, "show_request_path", lambda: request)

    single_instance.clear_show_request()
    assert single_instance.show_request_stamp() == 0.0
    single_instance.request_show()
    first = single_instance.show_request_stamp()
    assert first > 0.0

    import time

    time.sleep(0.01)
    single_instance.request_show()
    assert single_instance.show_request_stamp() != first


def test_a_copy_found_outside_the_guard_is_never_started_twice(tmp_path, monkeypatch):
    """An older portable copy holds neither mutex nor lock: the scan still wins."""
    calls = []
    monkeypatch.setattr(single_instance, "find_running_copy_pids", lambda: [777])
    monkeypatch.setattr(single_instance, "request_show", lambda: calls.append("file"))
    monkeypatch.setattr(single_instance, "_focus_running_copy", lambda *a, **k: True)
    monkeypatch.setattr(single_instance, "acquire_windows_mutex", lambda: True)

    lock = single_instance.InstanceLock(tmp_path / "guard.lock")
    assert single_instance._guarded(object(), lock) is False
    assert calls == ["file"]


def test_a_hidden_older_copy_offers_the_takeover(tmp_path, monkeypatch):
    """Silently exiting would leave a tray-only older copy with no way in."""
    ended = []
    monkeypatch.setattr(single_instance, "find_running_copy_pids", lambda: [777])
    monkeypatch.setattr(single_instance, "request_show", lambda: None)
    monkeypatch.setattr(single_instance, "_focus_running_copy", lambda *a, **k: False)
    monkeypatch.setattr(single_instance, "_focus_running_window", lambda *a, **k: False)
    monkeypatch.setattr(single_instance, "_explain_already_running", lambda **k: "")
    monkeypatch.setattr(
        single_instance, "end_running_copy", lambda pid: ended.append(pid) or True
    )
    monkeypatch.setattr(single_instance, "acquire_windows_mutex", lambda: True)

    lock = single_instance.InstanceLock(tmp_path / "guard.lock")
    assert single_instance._guarded(object(), lock) is False
    assert ended == []


def test_a_hidden_older_copy_is_replaced_after_consent(tmp_path, monkeypatch):
    import types

    ended = []
    monkeypatch.setattr(single_instance, "find_running_copy_pids", lambda: [777])
    monkeypatch.setattr(single_instance, "request_show", lambda: None)
    monkeypatch.setattr(single_instance, "_focus_running_copy", lambda *a, **k: False)
    monkeypatch.setattr(single_instance, "_focus_running_window", lambda *a, **k: False)
    monkeypatch.setattr(single_instance, "_explain_already_running", lambda **k: "force")
    monkeypatch.setattr(
        single_instance, "end_running_copy", lambda pid: ended.append(pid) or True
    )
    monkeypatch.setattr(single_instance, "acquire_windows_mutex", lambda: True)

    app = types.SimpleNamespace(
        aboutToQuit=types.SimpleNamespace(connect=lambda fn: None)
    )
    lock = single_instance.InstanceLock(tmp_path / "guard.lock")
    assert single_instance._guarded(app, lock) is True
    assert ended == [777]
    assert app._instance_lock is lock


# -- notification click targets -------------------------------------------


def test_a_notice_click_opens_the_named_notice(monkeypatch):
    import types

    from src import notifications
    from src.ui.main_window import MainWindow, _PAGE_NOTICES

    monkeypatch.setattr(notifications, "bring_window_to_front", lambda window: None)
    calls = []

    class _NoticesPage:
        def refresh_from_service(self):
            calls.append("refresh")

        def open_notice_by_id(self, notice_id):
            calls.append(("open", notice_id))
            return True

        def show_list(self):
            calls.append("list")

    class _Window:
        _notices_page = _NoticesPage()
        _open_notified_notice = MainWindow._open_notified_notice

        def _bring_notification_window_forward(self):
            calls.append("front")

        def _nav_to_page(self, page):
            calls.append(("nav", page))

    MainWindow._on_notification_clicked(_Window(), "notices:notice-42")
    assert ("nav", _PAGE_NOTICES) in calls
    assert ("open", "notice-42") in calls


def test_a_player_update_click_selects_the_named_release(monkeypatch):
    import json

    from src import notifications
    from src.ui.main_window import MainWindow, _PAGE_SELECT

    monkeypatch.setattr(notifications, "bring_window_to_front", lambda window: None)
    calls = []

    class _SelectPage:
        def navigate_to_package(
            self, model, software, tag_name=None, refresh_if_missing=False
        ):
            calls.append((model, software, tag_name))

    class _Window:
        _select_page = _SelectPage()
        _focus_notified_release = MainWindow._focus_notified_release

        def _bring_notification_window_forward(self):
            calls.append("front")

        def _nav_to_page(self, page):
            calls.append(("nav", page))

    payload = {"model": "Y2", "software": "Reborn (Y2Linux)", "tag": "v3.1.0"}
    MainWindow._on_notification_clicked(
        _Window(), "player_update:" + json.dumps(payload)
    )
    assert ("nav", _PAGE_SELECT) in calls
    assert ("Y2", "Reborn (Y2Linux)", "v3.1.0") in calls


def test_player_update_notification_carries_its_release(monkeypatch):
    import json

    from src import notice_alerts, player_update_check
    from src.ui.main_window import MainWindow

    sent = []
    monkeypatch.setattr(notice_alerts, "player_updates_enabled", lambda: True)
    monkeypatch.setattr(player_update_check, "get_last_notified_tag", lambda model: "")
    monkeypatch.setattr(player_update_check, "set_last_notified_tag", lambda model, tag: None)
    monkeypatch.setattr(
        notice_alerts,
        "deliver",
        lambda title, message, tray=None, route="": sent.append(route) or True,
    )

    class _Window:
        def _notification_tray(self):
            return None

    MainWindow._notify_device_updates(
        _Window(),
        [{"software_name": "Original Software", "model": "Y1", "latest_tag": "v1.2.3"}],
    )
    assert sent and sent[0].startswith("player_update:")
    assert json.loads(sent[0].split(":", 1)[1]) == {
        "model": "Y1",
        "software": "Original Software",
        "tag": "v1.2.3",
    }


def test_a_notice_can_be_opened_by_id(monkeypatch):
    from types import SimpleNamespace

    from src import notice_service
    from src.ui.dialogs import NoticesPage

    notice = SimpleNamespace(id="notice-42", title="A notice")

    class _Service:
        active_notices = [notice]

    from types import SimpleNamespace as NS

    monkeypatch.setattr(
        notice_service.NoticeService,
        "instance",
        classmethod(lambda cls: _Service()),
    )
    opened = []
    page = NS(open_notice=opened.append)
    assert NoticesPage.open_notice_by_id(page, "notice-42") is True
    assert opened == [notice]
    assert NoticesPage.open_notice_by_id(page, "missing") is False


def test_navigate_to_package_selects_and_scrolls_to_the_tag():
    from src.ui.select_page import SelectPackagePage

    class _Combo:
        def __init__(self, names):
            self._names = list(names)
            self._index = 0

        def findText(self, text):
            return self._names.index(text) if text in self._names else -1

        def currentIndex(self):
            return self._index

        def setCurrentIndex(self, index):
            self._index = index

        def count(self):
            return len(self._names)

        def itemData(self, index):
            return self._names[index]

        def itemText(self, index):
            return self._names[index]

        def currentText(self):
            return self._names[self._index] if self._names else ""

    class _Item:
        def __init__(self, release):
            self.release = release

        def data(self, _role):
            return self.release

    class _List:
        def __init__(self, releases):
            self.items = [_Item(release) for release in releases]
            self.row = None
            self.scrolled = None

        def count(self):
            return len(self.items)

        def item(self, row):
            return self.items[row]

        def setCurrentRow(self, row):
            self.row = row

        def scrollToItem(self, item):
            self.scrolled = item

    page = SelectPackagePage.__new__(SelectPackagePage)
    page._model_combo = _Combo(["Y1"])
    page._software_combo = _Combo(["Original Software"])
    page._release_list = _List(
        [
            {"tag_name": "v2.0.0"},
            {"tag_name": "v1.0.0"},
        ]
    )
    page._show_online_view = lambda: None
    page.current_package = lambda: object()
    refreshed = []
    page._refresh_releases = lambda *a, **k: refreshed.append(True)
    page._target_tag_to_select = None

    SelectPackagePage.navigate_to_package(
        page, "Y1", "Original Software", "v1.0.0"
    )
    assert page._release_list.row == 1
    assert page._release_list.scrolled is page._release_list.items[1]
    assert page._target_tag_to_select is None
    assert refreshed == []


def test_navigate_to_package_refreshes_only_for_a_notification_target():
    from src.ui.select_page import SelectPackagePage

    class _Combo:
        def __init__(self, names):
            self._names = list(names)
            self._index = 0

        def findText(self, text):
            return self._names.index(text) if text in self._names else -1

        def currentIndex(self):
            return self._index

        def setCurrentIndex(self, index):
            self._index = index

        def count(self):
            return len(self._names)

        def itemData(self, index):
            return self._names[index]

        def itemText(self, index):
            return self._names[index]

        def currentText(self):
            return self._names[self._index]

    class _List:
        def count(self):
            return 0

    page = SelectPackagePage.__new__(SelectPackagePage)
    page._model_combo = _Combo(["Y1"])
    page._software_combo = _Combo(["Original Software"])
    page._release_list = _List()
    page._show_online_view = lambda: None
    page.current_package = lambda: object()
    page._releases_worker = None
    refreshed = []
    page._refresh_releases = lambda *a, **k: refreshed.append(True)
    page._target_tag_to_select = None

    SelectPackagePage.navigate_to_package(
        page, "Y1", "Original Software", "v9.9", refresh_if_missing=False
    )
    assert refreshed == []

    SelectPackagePage.navigate_to_package(
        page, "Y1", "Original Software", "v9.9", refresh_if_missing=True
    )
    assert refreshed == [True]


def test_show_request_lives_in_the_shared_app_profile(monkeypatch, tmp_path):
    from src import paths

    monkeypatch.setattr(paths, "app_cache_dir", lambda: tmp_path)
    assert single_instance.show_request_path().parent == tmp_path


def test_a_newer_show_request_survives_an_old_acknowledgement(monkeypatch, tmp_path):
    import time

    request = tmp_path / "show.request"
    monkeypatch.setattr(single_instance, "show_request_path", lambda: request)
    single_instance.clear_show_request()

    assert single_instance.request_show() is True
    old_stamp = single_instance.show_request_stamp()
    time.sleep(0.01)
    assert single_instance.request_show() is True
    new_stamp = single_instance.show_request_stamp()
    assert new_stamp != old_stamp

    assert single_instance.clear_show_request(old_stamp) is False
    assert single_instance.show_request_stamp() == new_stamp
    assert single_instance.clear_show_request(new_stamp) is True
    assert single_instance.show_request_stamp() == 0.0


def test_wait_for_show_request_spots_the_acknowledgement(monkeypatch, tmp_path):
    request = tmp_path / "show.request"
    monkeypatch.setattr(single_instance, "show_request_path", lambda: request)
    single_instance.clear_show_request()

    assert single_instance.request_show() is True
    stamp = single_instance.show_request_stamp()
    assert single_instance.wait_for_show_request(stamp, timeout_s=0.05) is False

    assert single_instance.clear_show_request(stamp) is True
    assert single_instance.wait_for_show_request(stamp, timeout_s=0.2) is True
