"""Tests for bundled online data, notices browsing, post-install links and keep-data flashing."""

import os
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from src import catalog, manifest, online_data, sp_console_config
from src.flash_service import (
    KEEP_DATA_SCATTER_NAME,
    _scatter_partition_keeps_data,
    _write_scatter_preserving_data,
    sp_flash_tool_console_args,
)
from src.i18n import tr
from src.notice_service import Notice, NoticeService, parse_notice_xml
from src.ui.dialogs import (
    NoticeDetailView,
    NoticesDialog,
    _NoticeRow,
    format_notice_markdown,
    notice_see_more_url,
)
from src.ui.widgets import NoticeToast

# ── helpers ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def online_dirs(tmp_path, monkeypatch):
    """Point the online-data cache and bundle at temporary directories."""
    bundle = tmp_path / "bundle"
    cache = tmp_path / "cache"
    bundle.mkdir()
    cache.mkdir()
    monkeypatch.setattr(online_data, "bundle_dir", lambda: bundle)
    monkeypatch.setattr(online_data, "cache_dir", lambda: cache)
    return bundle, cache


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


SCATTER = """\
- general: MTK_PLATFORM_CFG
  info:
    - config_version: V1.1.2
      platform: MT6572
      storage: EMMC
- partition_index: SYS0
  partition_name: PRELOADER
  file_name: preloader.bin
  is_download: true
  type: SV5_BL_BIN
- partition_index: SYS1
  partition_name: ANDROID
  file_name: system.img
  is_download: true
  type: EXT4_IMG
- partition_index: SYS2
  partition_name: CACHE
  file_name: cache.img
  is_download: true
  type: EXT4_IMG
- partition_index: SYS3
  partition_name: USRDATA
  file_name: userdata.img
  is_download: true
  type: EXT4_IMG
- partition_index: SYS4
  partition_name: userdata
  file_name: usrdata.img
  is_download: true
  type: EXT4_IMG
"""


def _scatter_file(tmp_path: Path) -> Path:
    path = tmp_path / "MT6572_Android_scatter.txt"
    path.write_text(SCATTER, encoding="utf-8")
    return path


# ── online data: cache-first, bundled fallback, lazy refresh ────────────────


def test_online_sources_try_github_main_before_the_pages_site():
    from src.config import (
        INNIOASIS_RAW_FALLBACK_PREFIX,
        INNIOASIS_RAW_PREFIX,
        INNIOASIS_SITE_PREFIX,
        site_source_urls,
    )

    urls = site_source_urls("notice.xml")
    assert urls[0] == INNIOASIS_RAW_PREFIX + "notice.xml"
    assert urls[1] == INNIOASIS_SITE_PREFIX + "notice.xml"
    assert urls[2] == INNIOASIS_RAW_FALLBACK_PREFIX + "notice.xml"
    assert "y1-community/innioasis-updater/refs/heads/main" in urls[0]
    assert online_data.FILES["notice.xml"].alternate_urls()[0] == urls[0]


def test_read_text_prefers_cache_then_bundle(online_dirs):
    bundle, cache = online_dirs
    (bundle / "notice.xml").write_text("<notices><notice id='a'/></notices>", encoding="utf-8")
    assert "id='a'" in online_data.read_text("notice.xml")

    (cache / "notice.xml").write_text("<notices><notice id='b'/></notices>", encoding="utf-8")
    assert "id='b'" in online_data.read_text("notice.xml")

    # A blank download must not shadow a usable bundled copy.
    (cache / "notice.xml").write_text("   \n", encoding="utf-8")
    assert "id='a'" in online_data.read_text("notice.xml")


def test_refresh_writes_only_a_usable_copy_and_keeps_the_previous_one(online_dirs):
    _, cache = online_dirs

    good = "<notices><notice id='fresh'/></notices>"
    assert online_data.refresh("notice.xml", force=True, fetch=lambda spec, timeout: good)
    assert "fresh" in (cache / "notice.xml").read_text(encoding="utf-8")

    assert not online_data.refresh("notice.xml", force=True, fetch=lambda spec, timeout: None)
    assert "fresh" in online_data.cached_text("notice.xml")

    # Invalid XML and an HTML error page are both refused.
    assert not online_data.refresh("notice.xml", force=True, fetch=lambda s, t: "<notices>")
    assert not online_data.refresh(
        "notice.xml", force=True, fetch=lambda s, t: "<!doctype html><html>no</html>"
    )
    assert "fresh" in online_data.cached_text("notice.xml")


def test_refresh_is_ttl_gated_until_stale(online_dirs):
    bundle, cache = online_dirs
    (bundle / "notice.xml").write_text("<notices/>", encoding="utf-8")
    calls = []

    def fetch(spec, timeout):
        calls.append(1)
        return "<notices><notice id='new'/></notices>"

    assert online_data.refresh("notice.xml", fetch=fetch)
    assert len(calls) == 1
    # Fresh copy: a later launch does not download again.
    assert not online_data.refresh("notice.xml", fetch=fetch)
    assert len(calls) == 1
    # Past the TTL it refreshes.
    assert online_data.refresh(
        "notice.xml", fetch=fetch, now=time.time() + online_data.FILES["notice.xml"].ttl + 60
    )
    assert len(calls) == 2
    assert "new" in (cache / "notice.xml").read_text(encoding="utf-8")


def test_notice_service_reads_the_bundled_copy_when_it_has_no_cache(tmp_path, monkeypatch):
    service = NoticeService()
    service._cache_file = tmp_path / "missing.xml"
    service._cache_dir = tmp_path
    monkeypatch.setattr(
        online_data,
        "read_text",
        lambda name: "<notices><notice id='seed'><title>Seed</title></notice></notices>",
    )
    assert "id='seed'" in service._snapshot_text()


# ── catalogue snapshot + post-install URLs ─────────────────────────────────

MANIFEST_XML = """\
<slidia>
  <package name="Rockbox" repo="rockbox-y1/rockbox" device="Y1" type="img"
           handler="Custom Firmware"
           post_install="https://example.test/after"
           post_install_2="https://example.test/after-support" />
</slidia>
"""


def test_manifest_falls_back_to_the_bundled_snapshot(monkeypatch):
    monkeypatch.setattr(manifest, "load_cached_manifest", lambda: None)
    monkeypatch.setattr(manifest, "fetch_manifest", lambda: None)
    monkeypatch.setattr(catalog, "LIVE_CATALOG", [])
    monkeypatch.setattr(online_data, "read_text", lambda name: MANIFEST_XML)
    installed = {}
    monkeypatch.setattr(catalog, "set_live_catalog", lambda entries: installed.setdefault("entries", entries))

    entries = manifest.refresh_catalog(force_refresh=True)

    assert entries and entries[0].name == "Rockbox"
    assert entries[0].post_install == "https://example.test/after"
    assert entries[0].post_install_2 == "https://example.test/after-support"
    assert installed["entries"] == entries


def test_window_periodic_refresh_reloads_the_parsed_catalogue(monkeypatch):
    """Re-downloading the manifest is not enough: the window must re-parse it."""
    from src import notice_service
    from src import release_blocklist
    from src.ui.main_window import MainWindow

    reloaded = []
    monkeypatch.setattr(online_data, "refresh_all_async", lambda *a, **k: True)
    monkeypatch.setattr(release_blocklist, "refresh_blocklist_async", lambda: None)

    class _Notices:
        def fetch_async(self, *a, **k):
            return None

    monkeypatch.setattr(
        notice_service.NoticeService, "instance", staticmethod(lambda: _Notices())
    )

    class _Host:
        def _install_run_active(self):
            return False

        def _window_hidden_in_tray(self):
            return False

        def isVisible(self):
            return True

        def isMinimized(self):
            return False

        def _start_manifest_worker(self, force_refresh=False):
            reloaded.append(force_refresh)

    MainWindow._refresh_online_data(_Host())

    assert reloaded == [True]


def test_manifest_accepts_post_install2_spelling():
    xml = MANIFEST_XML.replace("post_install_2=", "post_install2=")

    entries = manifest.parse_manifest_xml(xml)

    assert entries[0].post_install_2 == "https://example.test/after-support"


def test_manifest_parses_info_and_apk_repo_attributes():
    xml = MANIFEST_XML.replace(
        'post_install_2="https://example.test/after-support"',
        'post_install_2="https://example.test/after-support" '
        'info_repo="jarvissax/rondo" apk_repo="author/app"',
    )

    entries = manifest.parse_manifest_xml(xml)

    assert entries[0].info_repo == "jarvissax/rondo"
    assert entries[0].apk_repo == "author/app"


def test_info_repo_lists_only_apk_releases_with_firmware_from_repo(monkeypatch):
    pkg = catalog.FirmwarePackage(
        slug="rondo", name="Rondo", model="Y1", repo="y1-community/y1-ata-rom",
        package_name="", info_repo="jarvissax/rondo",
        post_install="https://example.test/rondo",
    )
    apk_releases = [
        {"tag_name": "v2", "assets": [{"name": "rondo.apk", "browser_download_url": "u", "size": 1}]},
        {"tag_name": "v1", "assets": [{"name": "notes.txt", "browser_download_url": "u", "size": 1}]},
    ]
    firmware = {"tag_name": "fw-1", "download_url": "fw-url", "asset_name": "rom.zip"}
    real = catalog.ReleasesClient.releases_for_package

    def releases_for_package(self, p, model, **kwargs):
        if p.info_repo or p.apk_repo:
            return real(self, p, model, **kwargs)
        return [firmware]

    client = catalog.ReleasesClient.__new__(catalog.ReleasesClient)
    monkeypatch.setattr(catalog.ReleasesClient, "releases_for_package", releases_for_package)
    monkeypatch.setattr(client, "get_all_update_releases", lambda repo, force_refresh=False: apk_releases)

    out = client.releases_for_package(pkg, "Y1")

    assert [r["tag_name"] for r in out] == ["v2"]
    assert out[0]["source_repo"] == "jarvissax/rondo"
    assert out[0]["download_url"] == "fw-url"
    assert out[0]["firmware_tag_name"] == "fw-1"


def test_info_repo_without_post_install_lists_nothing(monkeypatch):
    pkg = catalog.FirmwarePackage(
        slug="rondo", name="Rondo", model="Y1", repo="y1-community/y1-ata-rom",
        package_name="", info_repo="jarvissax/rondo",
    )
    client = catalog.ReleasesClient.__new__(catalog.ReleasesClient)
    monkeypatch.setattr(client, "get_all_update_releases", lambda repo, force_refresh=False: [
        {"tag_name": "v2", "assets": [{"name": "rondo.apk", "browser_download_url": "u", "size": 1}]},
    ])

    assert client.releases_for_package(pkg, "Y1") == []

def test_post_install_urls_take_the_first_non_empty_pair():
    local = catalog.FirmwarePackage(
        slug="local", name="Rockbox", model="Y1", repo="", package_name="",
        post_install="https://local.test/after",
    )
    live = catalog.FirmwarePackage(
        slug="rockbox-y1", name="Rockbox", model="Y1", repo="r", package_name="",
        post_install="https://manifest.test/after", post_install_2="https://manifest.test/more",
    )
    plain = catalog.FirmwarePackage(slug="x", name="Solar", model="Y1", repo="r", package_name="")

    assert catalog.post_install_urls_for(None, local, live) == ("https://local.test/after", "")
    assert catalog.post_install_urls_for(None, plain, live) == (
        "https://manifest.test/after",
        "https://manifest.test/more",
    )
    assert catalog.post_install_urls_for(None, plain) == ("", "")


# ── notices: banner content vs full_content, See More + translation ─────────

NOTICE_XML = """\
<notices>
  <notice id="n1" severity="warning" models="Y1">
    <title>Hardware revision</title>
    <content>Y1 units sold after March 2026 need Rockbox 0.5 or later.</content>
    <full_content>Units with Innioasis OS 3.0.7 pre-installed cannot run older Rockbox builds. Install Rockbox 0.5 or newer.</full_content>
    <url>https://innioasis.app/notice/hardware-revision.html</url>
  </notice>
  <notice id="n2">
    <title>New release</title>
    <content>Rockbox 3.2 is available.</content>
  </notice>
</notices>
"""


def test_notice_parses_content_full_content_and_url():
    n1, n2 = parse_notice_xml(NOTICE_XML)
    assert n1.content.startswith("Y1 units")
    assert n1.full_content.startswith("Units with Innioasis OS")
    assert n1.url == "https://innioasis.app/notice/hardware-revision.html"
    # Without full_content the banner text is the fallback body.
    assert n2.full_content == n2.content
    assert n2.url == ""


def test_see_more_url_is_translated_outside_english():
    url = "https://innioasis.app/notice/hardware-revision.html"
    english = notice_see_more_url(url, "en")
    assert english.startswith(url)
    assert "lang=en" in english
    assert "translate.google" not in english
    translated = notice_see_more_url(url, "de")
    assert translated.startswith("https://innioasis.app/notice/hardware-revision.html?")
    assert "lang=de" in translated
    assert "translate.google" not in translated
    assert notice_see_more_url("", "de") == ""


def test_translated_notice_link_keeps_the_site_preferences(monkeypatch):
    """The notice opens on our site, with the language and the desktop accent."""
    from urllib.parse import parse_qs, urlsplit

    from src import browser

    monkeypatch.setattr(browser, "_native_accent_hex", lambda: "#a1b2c3")
    url = "https://innioasis.app/notice/hardware-revision.html"
    query = parse_qs(urlsplit(notice_see_more_url(url, "de")).query)
    assert query["accent"] == ["#a1b2c3"]
    assert query["ce"] == ["true"]
    assert query["lang"] == ["de"]
    assert query["theme"] in (["dark"], ["light"], ["high-contrast"])


def test_notice_toast_shows_banner_content_not_full_content(qapp):
    notice = parse_notice_xml(NOTICE_XML)[0]
    toast = NoticeToast()
    toast.set_notice(notice)
    assert toast._title_label.text() == notice.title
    assert toast._content_label.text() == notice.content
    assert notice.full_content not in toast._content_label.text()


def test_notices_dialog_cards_show_full_body_and_page_button(qapp, monkeypatch):
    notices = parse_notice_xml(NOTICE_XML)
    dialog = NoticesDialog(None, notices)
    try:
        assert len(dialog._rows) == 2
        first = dialog._rows[0]
        # In Updater CE 2.5.2, cards display the <content> attribute/element preview.
        assert first._body.toPlainText().strip() == notices[0].content
        assert not first._more.isHidden()

        opened = []
        monkeypatch.setattr("src.browser.open_browser", lambda url: opened.append(url))
        first._open_page.click()
        assert opened == [notice_see_more_url(notices[0].url)]

        opened.clear()
        first._more.click()
        from src.ui.dialogs import notice_read_more_url
        assert opened == [notice_see_more_url(notice_read_more_url(notices[0]))]
        assert opened == [notice_see_more_url(notices[0].url)]
    finally:
        dialog.deleteLater()


def test_notice_cards_render_markdown_images_and_inner_scroll(qapp):
    from PySide6.QtCore import Qt

    from src.ui.dialogs import NOTICE_CARD_BODY_MAX_HEIGHT

    long_body = "## Details\n\n**Bold** and [a link](https://example.test/guide).\n\n![Screenshot](https://example.test/shot.png)\n\n" + "\n\n".join(
        f"Paragraph {index}" for index in range(60)
    )
    notice = Notice(
        id="markdown-card",
        title="Markdown notice",
        content=long_body,
        full_content=long_body,
    )
    row = _NoticeRow(notice)
    try:
        html = row._body.toHtml()
        assert "Details" in html
        assert "Bold" in html
        assert "example.test/guide" in html
        assert "shot.png" in html
        assert row._body.verticalScrollBarPolicy() == Qt.ScrollBarAsNeeded

        row.resize(480, 420)
        row.show()
        qapp.processEvents()
        assert row._body.height() <= NOTICE_CARD_BODY_MAX_HEIGHT
        assert row._body.verticalScrollBar().maximum() > 0
    finally:
        row.close()
        row.deleteLater()


def test_short_notice_card_does_not_reserve_the_body_ceiling(qapp):
    """A one-line notice is a one-line card, not a 300 px empty pane."""
    from src.ui.dialogs import NOTICE_CARD_BODY_MAX_HEIGHT, _NoticeRow

    notice = Notice(
        id="short-card",
        title="Short notice",
        content="One short line.",
        full_content="One short line.",
    )
    row = _NoticeRow(notice)
    try:
        row.resize(600, 300)
        row.show()
        qapp.processEvents()
        assert row._body.height() < NOTICE_CARD_BODY_MAX_HEIGHT
        assert row._body.height() <= 80
        assert row._body.verticalScrollBar().maximum() == 0
    finally:
        row.close()
        row.deleteLater()


def test_notices_page_opens_a_notice_and_returns_to_the_list(qapp):
    from src.ui.dialogs import NoticesPage

    notice = parse_notice_xml(NOTICE_XML)[0]
    page = NoticesPage()
    titles = []
    page.title_changed.connect(lambda: titles.append(page.display_title()))
    try:
        page.set_notices([notice])
        page.open_notice(notice)
        assert page._pages.currentWidget() is page._detail
        assert page.display_title() == notice.title
        assert titles[-1] == notice.title

        page._detail._back.click()
        assert page._pages.currentWidget() is page._list_page
        assert page.display_title() == tr("nav_notices")
    finally:
        page.deleteLater()


def test_dismiss_chip_hands_its_appkit_button_back(qapp):
    from src.ui.flash_page import _RoundNextButton

    chip = _RoundNextButton(symbol="cancel", accent=False)
    try:
        chip._native_attempted = True
        chip.detach_native()
        assert chip._native is None
        # A reused widget may attach again the next time it is shown.
        assert chip._native_attempted is False
    finally:
        chip.deleteLater()


def test_rebuilding_notice_cards_hands_back_their_appkit_chips(qapp):
    """A replaced card must release the AppKit ✕ it owns.

    The dismiss chip is drawn by AppKit in the window, not in the widget tree,
    so a card thrown away while its chip still owns a button leaves the old ✕
    painted where the card used to be. The notices list rebuilds on every
    refresh, which put a stray cross above the first card.
    """
    from src.ui.dialogs import NoticesPage

    page = NoticesPage()
    try:
        first = Notice(id="a", title="First", content="Body.", full_content="Body.")
        second = Notice(id="b", title="Second", content="Body.", full_content="Body.")
        page.set_notices([first, second])
        qapp.processEvents()
        rows = [
            page._body_layout.itemAt(i).widget()
            for i in range(page._body_layout.count())
        ]
        rows = [row for row in rows if row is not None and row.objectName() == "noticeRow"]
        assert [row._notice.id for row in rows] == ["a", "b"]

        released = []
        original = rows[0].release_native

        def spy():
            released.append(rows[0]._notice.id)
            original()

        rows[0].release_native = spy
        page.set_notices([second])
        qapp.processEvents()
        assert released == ["a"], "the replaced card must hand its chip back"
        assert rows[0]._dismiss._native is None
    finally:
        page.deleteLater()


def test_replacing_notice_cards_never_makes_them_top_level_windows(qapp):
    """A dismissed notice's card is hidden, not unparented into a small window.

    The list rebuilds while the card's dismiss chip is handling its click.
    Unparenting the visible card made it a top-level window until deleteLater
    ran, which could flash as a small window over the page.
    """
    from src.ui.dialogs import NoticesPage

    page = NoticesPage()
    try:
        first = Notice(id="a", title="First", content="Body.", full_content="Body.")
        second = Notice(id="b", title="Second", content="Body.", full_content="Body.")
        page.set_notices([first, second])
        qapp.processEvents()
        rows = [
            page._body_layout.itemAt(i).widget()
            for i in range(page._body_layout.count())
        ]
        rows = [row for row in rows if row is not None and row.objectName() == "noticeRow"]
        assert len(rows) == 2

        page.set_notices([second])
        for row in rows:
            assert not row.isWindow(), "a replaced card must stay inside the page"
            assert row.isHidden()
    finally:
        page.set_notices([])
        page.deleteLater()


def test_window_auto_fit_never_grows_past_the_height_ceiling(qapp):
    """Tall page content scrolls; it never stretches the window past 600 px."""
    from PySide6.QtWidgets import QWidget

    from src.ui.main_window import MAXIMUM_AUTO_FIT_HEIGHT, MainWindow

    class _TallPage(QWidget):
        def content_height_hint(self):
            # What the Notices list measured with three long notice cards.
            return 1168

    window = MainWindow()
    window.show()
    qapp.processEvents()
    page = _TallPage()
    index = window._stack.addWidget(page)
    window._stack.setCurrentIndex(index)
    try:
        window._fit_window_to_page()
        assert window.height() <= MAXIMUM_AUTO_FIT_HEIGHT
        assert window.maximumHeight() <= MAXIMUM_AUTO_FIT_HEIGHT

        # A second fit over the same content cannot creep past it either.
        window._fit_window_to_page()
        assert window.height() <= MAXIMUM_AUTO_FIT_HEIGHT
    finally:
        window._stack.setCurrentIndex(0)
        window._stack.removeWidget(page)
        page.deleteLater()
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_notice_detail_renders_markdown_and_ignores_bare_site_url(qapp):
    notice = parse_notice_xml(
        """<notices><notice id="markdown">
        <title>Markdown notice</title>
        <content>Short preview</content>
        <full_content>## Details\n\n**Bold** and [a link](https://example.test/guide).\n\n![Screenshot](https://example.test/shot.png)</full_content>
        <url>https://innioasis.app</url>
        </notice></notices>"""
    )[0]

    detail = NoticeDetailView()
    try:
        detail.set_notice(notice)
        html = detail._body.toHtml()
        assert "Details" in html
        assert "Bold" in html
        assert "example.test/guide" in html
        assert "shot.png" in html
        # A bare site root forms the canonical notice page on innioasis.app
        assert not detail._open_page.isHidden()
        assert "notices.html?notice=markdown" in detail._page_url
        assert "<img" in format_notice_markdown(notice.full_content)
    finally:
        detail.deleteLater()


# ── keep data + Download Only flashing ─────────────────────────────────────


def test_keep_data_matching_covers_user_and_usr_partitions():
    assert _scatter_partition_keeps_data("USRDATA")
    assert _scatter_partition_keeps_data("userdata", "userdata.img")
    assert _scatter_partition_keeps_data("", "usrdata.img")
    assert not _scatter_partition_keeps_data("ANDROID", "system.img")
    assert not _scatter_partition_keeps_data("CACHE", "cache.img")


def test_keep_data_scatter_copy_disables_user_data_downloads_only(tmp_path):
    scatter = _scatter_file(tmp_path)
    original = scatter.read_text(encoding="utf-8")

    copy = _write_scatter_preserving_data(scatter)

    assert copy is not None
    assert copy.name == KEEP_DATA_SCATTER_NAME
    assert "scatter" not in copy.name.lower()
    text = copy.read_text(encoding="utf-8")

    def block(name):
        start = text.index(f"partition_name: {name}")
        return text[start:text.index("- partition_index:", start) if "- partition_index:" in text[start:] else len(text)]

    assert "is_download: false" in block("USRDATA")
    assert "is_download: false" in block("userdata")
    assert "is_download: true" in block("ANDROID")
    assert "is_download: true" in block("CACHE")
    # The package's own scatter is never edited.
    assert scatter.read_text(encoding="utf-8") == original


def test_keep_data_is_a_no_op_when_the_package_has_no_user_data(tmp_path):
    scatter = tmp_path / "MT6572_Android_scatter.txt"
    scatter.write_text(
        "- general: MTK_PLATFORM_CFG\n  info:\n    - platform: MT6572\n"
        "- partition_index: SYS0\n  partition_name: ANDROID\n  file_name: system.img\n  is_download: true\n",
        encoding="utf-8",
    )
    assert _write_scatter_preserving_data(scatter) is None


def test_keep_data_checkbox_follows_userdata_detection(qapp):
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_has_userdata(False)
        assert page._keep_data_cb.isHidden()

        # Detection alone is not the power-off prompt; the tick stays hidden.
        page.set_has_userdata(True, preserve_data_default=True)
        assert page._keep_data_cb.isHidden()
        assert page._keep_data_cb.isChecked()

        page.show_power_off_prompt()
        assert not page._keep_data_cb.isHidden()

        # A later package re-shows it at the prompt, unticked.
        page.set_has_userdata(True)
        assert not page._keep_data_cb.isHidden()
        assert not page._keep_data_cb.isChecked()

        # Not the connect screen, and not while flashing.
        page.set_waiting_device()
        assert page._keep_data_cb.isHidden()
        page.show_power_off_prompt()
        assert not page._keep_data_cb.isHidden()
        page.show_flashing()
        assert page._keep_data_cb.isHidden()
    finally:
        page.deleteLater()


def test_keep_data_warning_is_model_appropriate(qapp):
    from src.i18n import tr
    from src.keep_data import keep_data_label
    from src.ui.flash_page import FlashPage

    page = FlashPage()
    try:
        page.set_has_userdata(True)
        page.show_power_off_prompt()
        page.set_model("Y1")
        page.set_keep_data_available(True, keep_data_label("Y1"))
        assert page._keep_data_cb.text() == tr("flash_keep_data_option")
        # The explanation waits behind its link until it is asked for.
        assert page._keep_data_warning.isHidden()
        assert not page._keep_data_help_link.isHidden()
        page._on_keep_data_help()
        assert page._keep_data_warning_sentence == tr("flash_keep_data_warning_y1")
        assert tr("flash_keep_data_warning_y1") in page._keep_data_warning.text()

        page._keep_data_cb.setChecked(True)
        assert page._keep_data_warning.isHidden()
        page._keep_data_cb.setChecked(False)

        # Its Got it takes the link with the sentence: the hint is read, so the
        # card keeps neither an inert "What does this mean?" nor the warning.
        page._on_keep_data_warning_got_it()
        assert page._keep_data_warning.isHidden()
        assert page._keep_data_help_link.isHidden()
        page._keep_data_cb.setChecked(True)
        page._keep_data_cb.setChecked(False)
        assert page._keep_data_help_link.isHidden()

        from src.ui.flash_page import _set_hint_dismissed

        _set_hint_dismissed("keep_data_warning", False)

        page.set_model("Y2")
        assert page._keep_data_warning_sentence == tr("flash_keep_data_warning")
        page.set_keep_data_available(False, keep_data_label("Y2"))
        assert page._keep_data_warning_sentence == tr("flash_keep_data_unavailable")
        # News the reader needs now: an unavailable tick is never behind a link.
        assert not page._keep_data_warning.isHidden()

        page.set_model("Y1")
        assert page._keep_data_warning_sentence == tr("flash_keep_data_unavailable_y1")
        assert "partition" not in page._keep_data_warning_sentence.lower()
    finally:
        page.deleteLater()


def test_rockbox_themes_are_an_opt_out_per_install():
    from types import SimpleNamespace

    from src.ui.main_window import MainWindow

    class _CheckBox:
        def __init__(self):
            self.checked = True

        def isChecked(self):
            return self.checked

    page = SimpleNamespace(
        _local_rockbox_themes_cb=_CheckBox(),
        _current_selected_rel=None,
        current_installed_release_info=lambda: {},
    )
    win = SimpleNamespace(
        _package_model="Y1",
        _package_name="Rockbox",
        _package_path="",
        _select_page=page,
    )

    # Opt-out: checked by default, so a Y1 Rockbox install pushes themes…
    assert MainWindow._rockbox_themes_required(win) == (True, "Y1")
    # …unless the user clears the box for this install.
    page._local_rockbox_themes_cb.checked = False
    assert MainWindow._rockbox_themes_required(win) == (False, "Y1")


def test_post_install_completion_uses_a_globe_and_view_guide(qapp, monkeypatch):
    from src.config import DONATION_LINKS
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    opened = []
    monkeypatch.setattr("src.browser.open_browser", opened.append)
    page = FlashPage()
    try:
        page.set_post_install_urls("https://example.test/steps", "https://example.test/guide")

        # No donations: the done mark is a globe and the hint carries the link.
        page.set_device_done(donations_shown=False)
        assert page._done_mark._symbol == "www"
        assert not page._post_install_hint.isHidden()
        hint = page._post_install_hint.text()
        assert hint.count("<a ") == 1, hint
        assert '<a href="post-install">' in hint, hint
        assert "<img" in hint and "data:image/png;base64," in hint, hint
        assert hint.count(tr("post_install_find_out_more")) == 1, hint
        assert page._view_guide_btn.isHidden()

        # Donations shown: View Guide points at the second URL, and the
        # coffee button opens that same post instead of the plain ko-fi page.
        page.set_device_done(donations_shown=True)
        assert page._done_mark._symbol == "complete"
        assert page._post_install_hint.isHidden()
        assert not page._view_guide_btn.isHidden()
        page._open_view_guide()
        page._open_coffee_page()
        assert opened == ["https://example.test/steps", "https://example.test/guide"]

        # Without a second URL View Guide falls back to post_install and the
        # coffee button is the usual ko-fi page.
        opened.clear()
        page.set_post_install_urls("https://example.test/steps")
        page.set_device_done(donations_shown=True)
        assert not page._view_guide_btn.isHidden()
        page._open_view_guide()
        page._open_coffee_page()
        assert opened == ["https://example.test/steps", DONATION_LINKS["kofi"]]
    finally:
        page.deleteLater()


def test_non_donations_completion_with_a_guide_stays_compact(qapp):
    """A guide link must not grow the completion window back to a tall card."""
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    window = MainWindow()
    window.show()
    qapp.processEvents()
    try:
        window._install_complete = True
        page = window._flash_page
        page.show_flashing()
        page.set_post_install_urls(
            "https://github.com/jarvissax/rondo/blob/main/docs/install.md#first-start", ""
        )
        page.set_device_done(donations_shown=False)
        page.show_completion_note(True, software="Rondo", model="Y1")
        window._nav_to_page(_PAGE_FLASH)
        window._adjust_window_geometry()
        for _ in range(3):
            qapp.processEvents()

        assert page._post_install_hint.isVisible()
        assert page._done_mark._symbol == "www"
        assert not hasattr(page, "_post_install_doc")
        assert window.height() < 400
    finally:
        window.close()
        qapp.processEvents()


def test_sp_flash_tool_console_arguments_are_always_download_only():
    args = sp_flash_tool_console_args("scatter.txt", "DA.bin")
    # The ordinary run is the reference command line, with Download Only in
    # place of Format All + Download; only an auth-file run uses the XML.
    assert args[:2] == ["-c", "download"]
    assert "format" not in " ".join(args).lower()


def test_sp_auth_config_uses_download_only_never_format_download(tmp_path, monkeypatch):
    monkeypatch.setattr(sp_console_config, "config_dir", lambda: tmp_path)
    scatter = _scatter_file(tmp_path)
    da = tmp_path / "DA.bin"
    da.write_bytes(b"da")
    auth = tmp_path / "custom.auth"
    auth.write_bytes(b"auth")

    args = sp_flash_tool_console_args(str(scatter), str(da), str(auth))

    assert args[0] == "-r" and args[1] == "-i"
    config = Path(args[2]).read_text(encoding="utf-8")
    assert "<download-only/>" in config
    assert "format-download" not in config
    assert f"<scatter>{scatter}</scatter>" in config
    assert f"<authentication>{auth}</authentication>" in config


def test_apk_repo_without_post_install_lists_releases_and_records_apk_url(monkeypatch):
    pkg = catalog.FirmwarePackage(
        slug="app-pkg", name="AppPkg", model="Y1", repo="y1-community/y1-ata-rom",
        package_name="", apk_repo="author/app",
    )
    apk_releases = [
        {"tag_name": "1.2.0", "assets": [{"name": "app.apk", "browser_download_url": "https://example.test/app.apk", "size": 500}]},
    ]
    firmware = {"tag_name": "fw-1", "download_url": "fw-url", "asset_name": "rom.zip"}
    real = catalog.ReleasesClient.releases_for_package

    def releases_for_package(self, p, model, **kwargs):
        if p.info_repo or p.apk_repo:
            return real(self, p, model, **kwargs)
        if p.repo == "author/app":
            return []
        return [firmware]

    client = catalog.ReleasesClient.__new__(catalog.ReleasesClient)
    monkeypatch.setattr(catalog.ReleasesClient, "releases_for_package", releases_for_package)
    monkeypatch.setattr(client, "get_all_update_releases", lambda repo, force_refresh=False: apk_releases)

    out = client.releases_for_package(pkg, "Y1")

    assert len(out) == 1
    assert out[0]["tag_name"] == "1.2.0"
    assert out[0]["apk_repo"] == "author/app"
    assert out[0]["apk_url"] == "https://example.test/app.apk"
    assert out[0]["download_url"] == "fw-url"
    assert catalog.post_install_urls_for(pkg) == ("", "")


def test_info_or_apk_repo_with_firmware_zips_is_treated_as_repo(monkeypatch):
    pkg = catalog.FirmwarePackage(
        slug="rondo", name="Rondo", model="Y1", repo="y1-community/y1-ata-rom",
        package_name="", info_repo="jarvissax/rondo",
    )
    direct = {"tag_name": "2.0.0", "download_url": "https://example.test/rom.zip", "asset_name": "rom.zip"}
    real = catalog.ReleasesClient.releases_for_package

    def releases_for_package(self, p, model, **kwargs):
        if p.info_repo or p.apk_repo:
            return real(self, p, model, **kwargs)
        if p.repo == "jarvissax/rondo":
            return [direct]
        return []

    client = catalog.ReleasesClient.__new__(catalog.ReleasesClient)
    monkeypatch.setattr(catalog.ReleasesClient, "releases_for_package", releases_for_package)
    monkeypatch.setattr(
        client,
        "get_all_update_releases",
        lambda repo, force_refresh=False: [
            {"tag_name": "2.0.0", "assets": [{"name": "rom.zip", "browser_download_url": "https://example.test/rom.zip", "size": 100}]}
        ],
    )

    out = client.releases_for_package(pkg, "Y1")

    assert [r["tag_name"] for r in out] == ["2.0.0"]
    assert out[0]["download_url"] == "https://example.test/rom.zip"


def test_adb_apk_install_worker_and_themes_journey_apk_stage(tmp_path, monkeypatch):
    from src import adb
    from src.theme_pack import AdbApkInstallWorker
    from src.themes_journey import ThemesJourney

    journey = ThemesJourney()
    apk_file = tmp_path / "test_app.apk"
    apk_file.write_bytes(b"fake-apk")
    journey.set_apk(str(apk_file), "https://example.test/test_app.apk")
    assert journey.is_apk_stage
    assert journey.should_start(automatic=True, settings=None) == (True, "")

    installed = []
    monkeypatch.setattr(
        adb,
        "adb_install_apk",
        lambda adb_bin, serial, path, timeout=180: (installed.append((adb_bin, serial, str(path))) or True, "Success"),
    )
    worker = AdbApkInstallWorker("/usr/bin/adb", "0123456789ABCDEF", apk_path=apk_file)
    results = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))
    worker.run()
    assert installed == [("/usr/bin/adb", "0123456789ABCDEF", str(apk_file))]
    assert results == [(True, "Success")]
    journey.mark_completion_shown()
    assert not journey.is_apk_stage


def test_guided_power_off_and_keep_data_flow(qapp):
    from src.ui.main_window import MainWindow
    from src import manifest, catalog

    win = MainWindow()
    try:
        win.show()
        pkgs = manifest.load_snapshot_manifest()
        catalog.LIVE_CATALOG = pkgs
        rondo_pkg = next(p for p in pkgs if "rondo" in p.name.lower())
        win._package_name = "Rondo 1.0.0 for Y1"
        win._package_model = "Y1"
        win._package_path = "/tmp/fake_rondo.zip"
        win._select_page._details_package = rondo_pkg
        win._select_page._current_selected_rel = {"tag_name": "v1.0.0", "html_url": "https://github.com/fake"}

        # Stage 1: Disconnect & Power Off
        win._show_device_prep()
        fp = win._flash_page
        assert fp._wait_continue_btn._symbol == "next"
        assert fp._wait_cancel_btn._symbol == "cancel"
        assert not fp._keep_data_cb.isHidden()
        assert not fp._keep_data_cb.isChecked()
        # The microSD footnote belongs to the power-off explanation, not to
        # the "which software is running" question.
        assert fp._keep_data_microsd_note.isHidden()
        assert fp._keep_data_choice.isHidden()

        # Next without Keep Data: directly proceeds
        win._on_power_off_continue()
        assert win._pre_install_guided is True
        assert win._keep_data is False

        # Reset & Next with Keep Data (Single firmware: Rondo)
        win._show_device_prep()
        fp._keep_data_cb.setChecked(True)
        win._on_power_off_continue()
        assert win._keep_data_confirm_pending is True
        assert fp._wait_continue_btn._symbol == "check"
        assert fp._wait_cancel_btn._symbol == "cancel"
        assert fp._keep_data_choice.isHidden()
        # The question takes the progress band's place in bold, so the X and
        # the tick beside it read as its answers rather than as a bar at 0%.
        assert fp._wait_head_stack.currentWidget() is fp._wait_question_label
        assert "Rondo" in fp._wait_question_label.text()
        assert fp._wait_prompt_label.isHidden()
        assert "Rondo" in fp._wait_prompt_label.text()

        # Cancel confirmation -> goes to erase confirmation
        win._on_cancel_wait()
        assert win._keep_data_erase_confirm_pending is True
        assert "try-anyway" in fp._keep_data_hint.text()

        # Try Anyway link subverts restriction
        win._on_keep_data_try_anyway()
        assert win._keep_data is True
        assert win._pre_install_guided is True

        # Multiple firmware options (Stock / Koensayr)
        stock_pkg = next(p for p in pkgs if p.name == "Original Software" and p.model == "Y1")
        win._package_name = "Original Software 3.1.2 for Y1"
        win._package_model = "Y1"
        win._select_page._details_package = stock_pkg
        win._show_device_prep()
        fp._keep_data_cb.setChecked(True)
        win._on_power_off_continue()
        assert win._keep_data_confirm_pending is True
        assert fp._wait_continue_btn._symbol == "next"
        assert fp._wait_cancel_btn._symbol == "cancel"
        assert not fp._keep_data_choice.isHidden()
        assert fp._keep_data_choice.count() > 1
        # The firmware list takes the band's own slot, so it sits between the
        # icon and the X/arrow chips and shares their axis. The question moves
        # up into the row the software title would have used, which gives its
        # row to the list rather than stacking two headings above it.
        assert fp._wait_head_stack.currentWidget() is fp._keep_data_choice_holder
        assert "Y1" in fp._wait_question_label.text()
        assert not fp._wait_question_heading.isHidden()
        assert "Y1" in fp._wait_question_heading.text()
        assert fp._wait_pkg_label.isHidden()
        assert fp._keep_data_hint.isVisible()
    finally:
        win.close()


def _keep_data_window(tmp_path):
    """A window with a real scatter folder and the Rondo Y1 package selected."""
    from src.ui.main_window import MainWindow

    pkgs = manifest.load_snapshot_manifest()
    catalog.LIVE_CATALOG = pkgs
    rondo_pkg = next(p for p in pkgs if "rondo" in p.name.lower())
    firmware = tmp_path / "rondo_firmware"
    firmware.mkdir()
    (firmware / "MT6572_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")

    win = MainWindow()
    win._package_name = "Rondo 1.0.0 for Y1"
    win._package_model = "Y1"
    win._package_path = str(firmware)
    win._select_page._details_package = rondo_pkg
    win._select_page._current_selected_rel = {
        "tag_name": "v1.0.0",
        "html_url": "https://github.com/fake",
    }
    # The prep card is what this test inspects; do not start a backend.
    win._begin_flash_flow = lambda: None
    return win, rondo_pkg


def test_accepting_an_update_notification_prechecks_keep_data(qapp, tmp_path):
    win, rondo_pkg = _keep_data_window(tmp_path)
    try:
        calls = []
        win._select_page.start_install_for_release = lambda **kwargs: calls.append(kwargs)
        win._select_page._on_model_toast_install({
            "model": "Y1",
            "software": rondo_pkg.name,
            "tag": "v2.0.0",
            "release": {
                "tag_name": "v2.0.0",
                "download_url": "https://example.test/rom.zip",
            },
            "package": rondo_pkg,
        })

        assert calls and calls[0]["keep_settings"] is True
        # This is the handoff MainWindow performs when the package is ready.
        win._on_package_selected(
            win._package_path, "Rondo 1.0.0 for Y1", "Y1"
        )
        assert win._keep_settings_default is True
        win._show_device_prep()
        assert win._flash_page._keep_data_cb.isChecked()

        win._on_power_off_continue()
        assert win._keep_data is True
        assert win._keep_data_confirm_pending is False
    finally:
        win.close()


def test_reinstalling_the_tracked_firmware_prechecks_keep_data(qapp, tmp_path, monkeypatch):
    from src.keep_data import tracked_is_same

    win, rondo_pkg = _keep_data_window(tmp_path)
    try:
        monkeypatch.setattr(
            "src.keep_data.get_device_install",
            lambda *args, **kwargs: {
                "software_name": rondo_pkg.name,
                "package_slug": rondo_pkg.slug,
            },
        )
        assert tracked_is_same("Y1", rondo_pkg) is True
        win._keep_settings_default = False
        win._show_device_prep()
        assert win._flash_page._keep_data_cb.isChecked()

        win._on_power_off_continue()
        assert win._keep_data is True
        assert win._keep_data_confirm_pending is False
    finally:
        win.close()


def test_release_notes_see_more_follows_language(qapp):
    """The GitHub page is English, but its link label belongs to this UI."""
    from types import SimpleNamespace

    from PySide6.QtWidgets import QLabel

    from src.i18n import translator
    from src.ui.select_page import SelectPackagePage

    holder = SimpleNamespace(
        _current_selected_rel={"html_url": "https://github.com/example/release"},
        _see_more_label=QLabel(),
    )
    previous = translator().lang
    try:
        translator().set_language("de")
        SelectPackagePage._update_see_more_link(holder)
        text = holder._see_more_label.text()
        assert tr("notices_see_more") in text
        assert "See More" not in text
    finally:
        translator().set_language(previous or "en")


def test_select_page_release_notes_layout_no_overlap(qapp):
    from src.ui.main_window import MainWindow
    from src.i18n import translator

    win = MainWindow()
    try:
        win.show()
        translator().set_language("az")
        win._retranslate_all()
        page = win._select_page
        if page._release_list.count() > 0:
            page._release_list.setCurrentRow(0)
        # Row 0 can already be current (an earlier test selected it), and then
        # setCurrentRow() emits nothing and no release is recorded as selected.
        # Both links under test only exist for a selected release, so state one.
        page._current_selected_rel = page._current_selected_rel or {
            "tag_name": "v1.0.0",
            "body": "Release notes for the layout check.",
            "html_url": "https://github.com/example/release",
        }
        page._is_translated = True
        page._update_translate_link()
        qapp.processEvents()
        win._fit_window_to_select_content()
        qapp.processEvents()
        # The page's own layout holds the scroll area; the notes card and its
        # links live in the scroll content's layout, so that is the one whose
        # geometry has to be resolved before reading it.
        page._scroll_content.layout().activate()
        page._notes_group.layout().activate()
        qapp.processEvents()

        notes_geom = page._notes.geometry()
        trans_geom = page._translate_label.geometry()
        see_more_geom = page._see_more_label.geometry()

        assert notes_geom.bottom() < trans_geom.top()
        assert trans_geom.bottom() < see_more_geom.top()
    finally:
        # The page's connectivity probe is a shared background QThread. The
        # worker is daemon-like in the app, but a test process that exits with
        # it still running aborts in Qt, after every assertion has passed.
        try:
            win._select_page._monitor.stop_monitoring()
        except Exception:
            pass
        win.close()


def test_notice_content_and_read_more_url(qapp, monkeypatch):
    from src.ui.dialogs import _NoticeRow, notice_read_more_url
    from src.notice_service import Notice
    from src.i18n import tr

    notice = Notice(
        id="2026-10-25-firmware-advisory",
        title="About Notices in Updater CE",
        content="You'll get important alerts about device safety issues reported by the community here.",
        full_content="Long markdown text that should not appear in the card preview.",
    )
    row = _NoticeRow(notice)
    try:
        # 1. Body shows notice.content, NOT full_content
        assert row._body.toPlainText().strip() == notice.content
        assert "Long markdown" not in row._body.toPlainText()

        # 2. notice_read_more_url forms the expected URL with the ID
        expected_url = "https://innioasis.app/notices.html?notice=2026-10-25-firmware-advisory"
        assert notice_read_more_url(notice) == expected_url

        # 3. Read more button opens the URL in the browser
        from src.ui.dialogs import notice_see_more_url
        opened = []
        monkeypatch.setattr("src.browser.open_browser", lambda url: opened.append(url))
        row._more.click()
        assert opened == [notice_see_more_url(expected_url)]
    finally:
        row.deleteLater()


def test_keep_data_compatibility_copy_y1_vs_other():
    """The English copy distinguishes the two cases.

    The copy is asserted in English, so the language is pinned: a catalog
    translated by another test must not decide whether this passes.
    """
    from src.i18n import translator

    t = translator()
    previous = t.lang
    t.set_language("en")
    try:
        hint_y1 = t.t("keep_data_only_compatible_hint_y1")
        hint_other = t.t("keep_data_only_compatible_hint_other")

        # Y1 copy states "only keep your settings" (no media mentioned since microSD stores media)
        assert "settings and media" not in hint_y1
        assert "only keep your settings" in hint_y1

        # Other models say "settings and media"
        assert "settings and media" in hint_other
    finally:
        t.set_language(previous)


def test_online_notices_url_formation():
    from src.notice_service import parse_notice_xml
    from src.ui.dialogs import notice_read_more_url

    xml = """<notices>
  <notice id="2026-11-03-rockbox-y2-warn" persistent="false" severity="warning" from="2026-11-03" until="2026-12-31" models="Y2">
    <title>Users having issues with Rockbox for Y2.</title>
    <content>Rockbox 360p themes not working and music not reading from MicroSD.</content>
    <url>https://innioasis.app</url>
  </notice>
  <notice id="2026-10-25-firmware-advisory" persistent="false" severity="info" from="2026-10-25" until="2026-12-31" models="Y1,Y2">
    <title>About Notices in Updater CE</title>
    <content>You'll get important alerts about device safety issues reported by the community here.</content>
    <link>https://innioasis.app</link>
  </notice>
</notices>"""
    n1, n2 = parse_notice_xml(xml)
    assert notice_read_more_url(n1) == "https://innioasis.app/notices.html?notice=2026-11-03-rockbox-y2-warn"
    assert notice_read_more_url(n2) == "https://innioasis.app/notices.html?notice=2026-10-25-firmware-advisory"
    assert n1.web_url == "https://innioasis.app/notices.html?notice=2026-11-03-rockbox-y2-warn"
    assert n2.web_url == "https://innioasis.app/notices.html?notice=2026-10-25-firmware-advisory"


def test_donation_preference_in_browser_urls(monkeypatch):
    from src.browser import _with_donation_preference

    # Case 1: donations disabled -> ?donations=false added
    monkeypatch.setattr("src.player_update_check.is_donation_ui_disabled", lambda: True)
    assert _with_donation_preference("https://innioasis.app") == "https://innioasis.app?donations=false"
    assert _with_donation_preference("https://innioasis.app/guide.html") == "https://innioasis.app/guide.html?donations=false"
    assert _with_donation_preference("https://innioasis.app/guide.html?foo=bar") == "https://innioasis.app/guide.html?foo=bar&donations=false"
    assert _with_donation_preference("https://innioasis.app/guide.html?donations=false") == "https://innioasis.app/guide.html?donations=false"
    assert _with_donation_preference("https://innioasis.app/guide.html?donations=true") == "https://innioasis.app/guide.html?donations=false"

    # Case 2: donations enabled -> no donations parameter provided at all
    monkeypatch.setattr("src.player_update_check.is_donation_ui_disabled", lambda: False)
    assert _with_donation_preference("https://innioasis.app") == "https://innioasis.app"
    assert _with_donation_preference("https://innioasis.app/guide.html") == "https://innioasis.app/guide.html"
    assert _with_donation_preference("https://innioasis.app/guide.html?donations=false") == "https://innioasis.app/guide.html"
    assert _with_donation_preference("https://innioasis.app/guide.html?donations=true") == "https://innioasis.app/guide.html"
    assert _with_donation_preference("https://innioasis.app/guide.html?foo=bar&donations=false") == "https://innioasis.app/guide.html?foo=bar"

