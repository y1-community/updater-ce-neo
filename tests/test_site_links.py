"""Links from innioasis.app and themes.innioasis.app that open the app.

A site starts a firmware or theme download with an ``innioasis-updater://``
link carrying what, version, model, brand and type. The app reads the same
names it already sends the sites, so both sides attribute and act on one
contract. 240p builds only appear after the reader agrees to the warning, and
a model with no released firmware is never offered through a link either.
"""

import os
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from src import catalog, docs_source, manifest, player_update_check, site_links  # noqa: E402
from src.browser import _with_site_preferences  # noqa: E402


def test_the_contract_reads_every_field():
    link = site_links.parse(
        "innioasis-updater://open?what=firmware&software=Rockbox&version=v0.5.1"
        "&model=y1&brand=Innioasis&type=b&resolution=240P&action=install"
        "&utm_source=innioasis.app&utm_campaign=release&ref=firmware-page"
    )
    assert link is not None
    assert link.kind == site_links.KIND_FIRMWARE
    assert link.action == site_links.ACTION_INSTALL
    assert (link.software, link.version, link.model, link.brand) == (
        "Rockbox", "v0.5.1", "Y1", "Innioasis"
    )
    assert link.hw_type == "B" and link.resolution == "240p" and link.wants_240p
    assert link.attribution == {
        "utm_source": "innioasis.app",
        "utm_campaign": "release",
        "ref": "firmware-page",
    }
    again = site_links.parse(link.describe())
    assert again is not None and again.query() == link.query()


def test_kind_and_action_may_be_the_link_host():
    theme = site_links.parse("innioasis-updater://theme?id=ipod-classic&model=Y1")
    assert theme.kind == site_links.KIND_THEME and theme.theme_id == "ipod-classic"
    install = site_links.parse("innioasis-updater://install?what=firmware&model=Y2")
    assert install.action == site_links.ACTION_INSTALL and install.wants_latest


def test_site_urls_are_read_with_the_same_query():
    themes = site_links.parse("https://themes.innioasis.app/theme.html?id=retro")
    assert themes.kind == site_links.KIND_THEME and themes.theme_id == "retro"
    page = site_links.parse(
        "https://innioasis.app/release_notes.html?what=firmware&software=solar&model=Y1"
    )
    assert page.kind == site_links.KIND_FIRMWARE and page.software == "solar"
    # A plain site page is not a request to the app.
    assert site_links.parse("https://innioasis.app/release_notes.html?repo=a/b") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/?what=firmware&model=Y1",
        "innioasis-updater://open?model=Y1",
        "innioasis-updater://open?what=firmware&software=%3Cscript%3E",
        "innioasis-updater://open?what=firmware&version=" + "v" * 200,
        "file:///tmp/rom.zip",
        "",
    ],
)
def test_anything_else_is_not_a_link(url):
    assert site_links.parse(url) is None


def test_launch_arguments_skip_switches_and_paths():
    link = site_links.from_arguments(
        ["--tray", "/tmp/rom.zip", "innioasis-updater://open?what=theme"]
    )
    assert link is not None and link.kind == site_links.KIND_THEME
    assert site_links.from_arguments(["--tray", "/tmp/rom.zip"]) is None


def test_site_visits_say_they_came_from_the_app(monkeypatch):
    from src import config

    monkeypatch.setattr(player_update_check, "is_donation_ui_disabled", lambda: False)
    query = parse_qs(
        urlsplit(_with_site_preferences("https://themes.innioasis.app/?model=Y1")).query
    )
    assert query["client"] == ["updater-ce"]
    assert query["client_version"] == [config.APP_VERSION]
    assert query["model"] == ["Y1"]
    # Other hosts are left alone.
    assert _with_site_preferences("https://github.com/x/y") == "https://github.com/x/y"


def test_release_notes_page_names_the_build_for_its_download_buttons():
    url = docs_source.release_notes_url(
        "rockbox-y1/rockbox",
        "v0.5",
        model="y1",
        software="rockbox-y1",
        brand="Innioasis",
        hw_type="b",
        resolution="240p",
    )
    query = parse_qs(urlsplit(url).query)
    assert query["type"] == ["B"] and query["resolution"] == ["240p"]
    assert query["brand"] == ["Innioasis"] and query["software"] == ["rockbox-y1"]
    plain = parse_qs(urlsplit(docs_source.release_notes_url("a/b", "v1")).query)
    assert "resolution" not in plain and "type" not in plain


def test_a_second_launch_forwards_its_link(monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from src import single_instance

    app = QApplication.instance() or QApplication([])
    name = f"updater-ce-test-{uuid.uuid4().hex[:8]}"
    monkeypatch.setattr(single_instance, "focus_server_name", lambda: name)
    server = single_instance.FocusServer(name)
    received, drops = [], []
    server.link_requested.connect(received.append)
    server.drop_requested.connect(drops.append)
    url = "innioasis-updater://open?what=firmware&model=Y1&software=Rockbox"
    assert single_instance.request_link(url, timeout_ms=500)
    for _ in range(50):
        app.processEvents()
        QCoreApplication.processEvents()
        if received:
            break
    assert received == [url] and drops == []


# --- the window acting on a link -------------------------------------------
class _Page:
    def __init__(self):
        self.calls = []
        self._model_combo = SimpleNamespace(findText=lambda _t: -1)

    def current_model(self):
        return "Y1"

    def navigate_to_package(self, *args, **kwargs):
        self.calls.append(("navigate", args, kwargs))

    def start_install_for_release(self, *args, **kwargs):
        self.calls.append(("install", args, kwargs))


def _window(confirm=True, busy=False):
    messages = []
    w = SimpleNamespace(
        _select_page=_Page(),
        _settings_page=SimpleNamespace(allowed=[], allow_240p_listing=None),
        messages=messages,
        themes_started=[],
    )
    w._settings_page.allow_240p_listing = lambda: w._settings_page.allowed.append(True)
    w._show_from_tray = lambda: None
    w._append_log = lambda *_a, **_k: None
    w._install_run_active = lambda: busy
    w._nav_to_page = lambda _i: None
    w.statusBar = lambda: SimpleNamespace(
        showMessage=lambda text, _ms=0: messages.append(text)
    )
    w._confirm_240p_link = lambda: confirm
    w.start_rockbox_themes_manually = lambda: w.themes_started.append(True) or True
    return w


MANIFEST = """
<slidia>
  <package name="Rockbox" repo="rockbox-y1/rockbox" device="Y1" slug="rockbox-y1"
           type="img" handler="Custom Firmware" />
  <package name="Original Software" repo="timmkoo/q5-stock" device="Q5"
           brand="Timmkoo" type="img" handler="Custom Firmware" />
</slidia>
"""


@pytest.fixture
def live_catalog():
    saved = list(catalog.LIVE_CATALOG)
    catalog.set_live_catalog(manifest.parse_manifest_xml(MANIFEST))
    yield
    catalog.set_live_catalog(saved)


def _open(window, url):
    from src.ui.main_window import MainWindow

    return MainWindow.open_site_link(window, site_links.parse(url))


def test_a_firmware_link_selects_the_release(live_catalog):
    w = _window()
    assert _open(w, "innioasis-updater://open?what=firmware&software=rockbox-y1&model=Y1&version=v0.5&type=B")
    kind, args, kwargs = w._select_page.calls[-1]
    assert kind == "navigate" and args == ("Y1", "Rockbox")
    assert kwargs["tag_name"] == "v0.5" and kwargs["hw_type"] == "B"
    assert kwargs["resolution"] == ""


def test_install_links_start_the_guided_install(live_catalog):
    w = _window()
    assert _open(w, "innioasis-updater://install?what=firmware&software=Original%20Software&model=Q5&brand=Timmkoo")
    kind, args, kwargs = w._select_page.calls[-1]
    assert kind == "install" and args == ("Q5", "Original Software")
    assert kwargs["tag_name"] == "latest" and kwargs["brand"] == "Timmkoo"


def test_a_model_with_no_release_is_not_offered(live_catalog):
    w = _window()
    assert not _open(w, "innioasis-updater://open?what=firmware&software=Rockbox&model=Q8")
    assert not _open(w, "innioasis-updater://open?what=firmware&software=Rockbox&model=A5")
    assert w._select_page.calls == []
    assert w.messages and all(m == w.messages[0] for m in w.messages)


def test_240p_needs_the_readers_agreement(live_catalog, monkeypatch):
    from src import device_tracking

    monkeypatch.setattr(device_tracking, "advanced_settings_enabled", lambda *a, **k: False)
    url = "innioasis-updater://open?what=firmware&software=Rockbox&model=Y1&resolution=240p"

    agreed = _window(confirm=True)
    _open(agreed, url)
    assert agreed._settings_page.allowed == [True]
    assert agreed._select_page.calls[-1][2]["resolution"] == "240p"

    declined = _window(confirm=False)
    _open(declined, url)
    assert declined._settings_page.allowed == []
    assert declined._select_page.calls[-1][2]["resolution"] == ""


def test_theme_links_open_the_themes_stage_and_busy_windows_wait(live_catalog):
    w = _window()
    assert _open(w, "innioasis-updater://open?what=theme&id=retro&model=Y1")
    assert w.themes_started == [True]

    busy = _window(busy=True)
    assert not _open(busy, "innioasis-updater://open?what=firmware&software=Rockbox&model=Y1")
    assert busy._select_page.calls == [] and busy.messages


# --- the release list ---------------------------------------------------------
def test_a_240p_row_reads_like_a_release_and_warns(live_catalog):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem

    from src.i18n import tr
    from src.ui.select_page import SelectPackagePage

    QApplication.instance() or QApplication([])
    standard = {"tag_name": "v0.5", "name": "v0.5", "asset_name": "rom.zip",
                "selected_resolution": "360p"}
    low = dict(standard, asset_name="rom_240p.zip", selected_resolution="240p")
    assert catalog.parse_clean_release_tag_label(low).endswith("(240p)")
    assert "240p" not in catalog.parse_clean_release_tag_label(standard)

    page = SelectPackagePage()
    try:
        page._release_list.blockSignals(True)
        page._release_list.clear()
        page._release_list.blockSignals(False)
        page._listed_releases = [standard, low]
        page._set_online_banner("releases", 2)
        for rel in (standard, low):
            item = QListWidgetItem(catalog.parse_clean_release_tag_label(rel))
            item.setData(Qt.UserRole, rel)
            page._release_list.addItem(item)

        page._target_resolution = ""
        assert page._row_for_tag("v0.5") == 0
        page._target_resolution = "240p"
        assert page._row_for_tag("v0.5") == 1
        assert page._row_for_tag("latest") == 1

        page._release_list.setCurrentRow(1)
        assert page._online_banner_key == "sel_240p_warning"
        assert page._online_banner.text() == tr("sel_240p_warning")
        page._release_list.setCurrentRow(0)
        assert page._online_banner_key == "releases"
    finally:
        page.deleteLater()
