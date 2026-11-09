"""Links inside the app's Markdown views open the site's parser pages.

A release's notes, a project README and a guide's own links are Markdown written
by someone else. Clicking a GitHub document, repository or release there used to
send the reader to a bare GitHub page: no site styling, no download buttons and
no language or donation preference. The website already renders all three from
``slidia_manifest.xml`` entries, so the app hands it the link instead. Links the
catalogue does not know, and specific GitHub pages (an issue, a pull request),
keep their own target.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from src import docs_source, player_update_check
from src.browser import _with_site_preferences

BETTER_Y = SimpleNamespace(
    slug="better-y-y1", name="Better-Y", model="Y1", repo="Leska1337/better-Y"
)
RONDO_MD = "https://github.com/jarvissax/rondo/blob/main/docs/install.md#first-start"


def _catalog(monkeypatch, packages=(BETTER_Y,)):
    monkeypatch.setattr(docs_source, "_catalog_packages", lambda: list(packages))


def test_a_catalogue_repository_opens_its_firmware_page(monkeypatch):
    _catalog(monkeypatch)
    url = docs_source.site_page_for_link("https://github.com/Leska1337/better-Y")
    parts = urlsplit(url)
    assert parts.scheme + "://" + parts.netloc + parts.path == (
        "https://innioasis.app/firmware.html"
    )
    query = parse_qs(parts.query)
    assert query["model"] == ["Y1"]
    assert query["software"] == ["better-y-y1"]


def test_a_catalogue_release_opens_the_release_notes_reader(monkeypatch):
    _catalog(monkeypatch)
    url = docs_source.site_page_for_link(
        "https://github.com/Leska1337/better-Y/releases/tag/v3.4.0"
    )
    parts = urlsplit(url)
    assert parts.path == "/release_notes.html"
    query = parse_qs(parts.query)
    assert query["repo"] == ["Leska1337/better-Y"]
    assert query["tag"] == ["v3.4.0"]
    assert query["model"] == ["Y1"]
    assert query["software"] == ["better-y-y1"]


def test_a_markdown_file_opens_the_post_install_reader(monkeypatch):
    _catalog(monkeypatch)
    url = docs_source.site_page_for_link(RONDO_MD)
    parts = urlsplit(url)
    assert parts.path == "/post_install.html"
    query = parse_qs(parts.query)
    assert query["url"] == [
        "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/install.md"
    ]
    # The site's own reader calls the section ``anchor``.
    assert query["section"] == ["first-start"]
    assert query["anchor"] == ["first-start"]
    assert query["repo"] == ["jarvissax/rondo"]


def test_an_unknown_project_keeps_its_github_target(monkeypatch):
    _catalog(monkeypatch)
    assert docs_source.site_page_for_link("https://github.com/other/project") == ""
    assert (
        docs_source.site_page_for_link("https://example.test/guide.html") == ""
    )


def test_specific_github_pages_are_not_replaced(monkeypatch):
    _catalog(monkeypatch)
    for url in (
        "https://github.com/Leska1337/better-Y/pull/12",
        "https://github.com/Leska1337/better-Y/issues/4",
        "https://github.com/Leska1337/better-Y/commit/abc123",
    ):
        assert docs_source.site_page_for_link(url) == ""


def test_site_links_are_already_the_destination(monkeypatch):
    _catalog(monkeypatch)
    assert (
        docs_source.site_page_for_link(
            "https://innioasis.app/firmware.html?model=Y1&software=better-y-y1"
        )
        == ""
    )


def test_open_site_link_goes_through_the_browser_helper(monkeypatch):
    _catalog(monkeypatch)
    opened = []
    from src import browser

    monkeypatch.setattr(
        browser, "open_browser", lambda url, *a, **k: opened.append(url) or True
    )
    assert docs_source.open_site_link("https://github.com/Leska1337/better-Y")
    assert opened[-1].startswith("https://innioasis.app/firmware.html?")
    # Something with no parser page still opens, on its own address.
    assert docs_source.open_site_link("https://example.test/notes")
    assert opened[-1] == "https://example.test/notes"


def test_site_links_always_carry_both_preferences(monkeypatch):
    from src.i18n import translator

    monkeypatch.setattr(player_update_check, "is_donation_ui_disabled", lambda: False)
    translator().set_language("zh-CN")
    enabled = parse_qs(
        urlsplit(
            _with_site_preferences("https://innioasis.app/firmware.html?model=Y1")
        ).query
    )
    assert enabled["donations"] == ["true"]
    assert enabled["lang"] == ["zh-CN"]

    monkeypatch.setattr(player_update_check, "is_donation_ui_disabled", lambda: True)
    disabled = parse_qs(
        urlsplit(
            _with_site_preferences("https://innioasis.app/firmware.html?model=Y1")
        ).query
    )
    assert disabled["donations"] == ["false"]
