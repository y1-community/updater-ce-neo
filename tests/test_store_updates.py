"""Microsoft Store update path: msstore.txt, packaged detection, Store action."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.updates import STORE_MARKER_ASSET, UpdateChecker, UpdateInfo, parse_store_marker


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


class _Response:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


class _Session:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.requested = []

    def get(self, url, timeout=None, **kwargs):
        self.requested.append(url)
        return _Response(self.text, self.status_code)


class _Client:
    def __init__(self, session):
        self.session = session


def test_the_marker_reads_its_first_line():
    assert parse_store_marker("true") is True
    assert parse_store_marker("TRUE\nbecause 2.6 is on the Store") is True
    assert parse_store_marker("\n\n  true  \n") is True
    assert parse_store_marker("false") is False
    assert parse_store_marker("") is False
    assert parse_store_marker("2.6") is False


def test_a_release_without_the_marker_is_not_a_store_release():
    checker = UpdateChecker(client=_Client(_Session("true")))
    assert checker._store_flag([{"name": "UpdaterCE-Setup-2.6.exe"}]) is False


def test_the_marker_file_is_read_only_on_windows(monkeypatch):
    session = _Session("true")
    checker = UpdateChecker(client=_Client(session))
    asset = {"name": STORE_MARKER_ASSET, "browser_download_url": "https://example.com/msstore.txt"}

    monkeypatch.setattr(sys, "platform", "darwin")
    assert checker._store_flag([asset]) is False
    assert session.requested == []

    monkeypatch.setattr(sys, "platform", "win32")
    assert checker._store_flag([asset]) is True
    assert session.requested == ["https://example.com/msstore.txt"]


def test_a_marker_that_says_false_or_cannot_be_read_is_not_a_store_release(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    asset = {"name": STORE_MARKER_ASSET, "browser_download_url": "https://example.com/msstore.txt"}
    assert UpdateChecker(client=_Client(_Session("false")))._store_flag([asset]) is False
    assert UpdateChecker(client=_Client(_Session("true", status_code=404)))._store_flag([asset]) is False

    class _Broken:
        session = None

    assert UpdateChecker(client=_Broken())._store_flag([asset]) is False


def test_store_status_starts_off():
    assert UpdateInfo().store_available is False


def test_the_store_action_only_appears_in_a_packaged_windows_build(monkeypatch):
    _app()
    from src.ui import dialogs
    import src.store_package as store_package

    info = UpdateInfo(tag="v2.6", name="2.6", body="Notes", store_available=True)

    monkeypatch.setattr(store_package, "is_packaged", lambda: False)
    plain = dialogs.UpdateAvailableDialog(None, info=info, current_version="2.5.0")
    assert plain._store is False
    assert plain._action_btn.toolTip() != "Open Microsoft Store"

    monkeypatch.setattr(store_package, "is_packaged", lambda: True)
    packaged = dialogs.UpdateAvailableDialog(None, info=info, current_version="2.5.0")
    assert packaged._store is True
    assert packaged._action_btn.toolTip() == "Open Microsoft Store"


def test_the_store_action_opens_the_store_and_closes(monkeypatch):
    _app()
    from src.ui import dialogs
    import src.store_package as store_package

    opened = []
    monkeypatch.setattr(store_package, "is_packaged", lambda: True)
    monkeypatch.setattr(store_package, "open_store_updates", lambda: opened.append(True) or True)
    info = UpdateInfo(tag="v2.6", name="2.6", body="Notes", store_available=True)
    dlg = dialogs.UpdateAvailableDialog(None, info=info, current_version="2.5.0")
    dlg._on_action_clicked()
    assert opened == [True]


def test_the_store_uri_is_the_store_updates_page():
    from src.store_package import STORE_UPDATES_URI

    assert STORE_UPDATES_URI == "ms-windows-store://downloadsandupdates"


def test_an_unpackaged_build_is_never_packaged():
    from src.store_package import is_packaged

    if sys.platform != "win32":
        assert is_packaged() is False
