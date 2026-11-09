"""Post-install guides and release notes, opened on the site, not on GitHub.

A firmware's ``post_install`` is often a Markdown file in the project's own
repository (Rondo: ``docs/install.md#first-start``). The completion screen links
to the site's reader page for that file, so the reader gets the same guide with
our styling instead of a bare GitHub blob page; any other kind of page keeps
its own link. "See More" for a release goes to the site's own
``release_notes.html``, which renders the release title and notes with our
styling and our own download buttons.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from urllib.parse import parse_qs, urlsplit

import pytest
from PySide6.QtWidgets import QApplication

from src import docs_source, player_update_check
from src.browser import _with_donation_preference
from src.markdown_render import (
    render_markdown_html,
    rewrite_legacy_discord_invite,
    strip_front_matter,
)
from src.ui.flash_page import FlashPage

RONDO = "https://github.com/jarvissax/rondo/blob/main/docs/install.md#first-start"
GUIDE = """\
---
title: Install
---

# Rondo

Some introduction.

## First start

1. Copy the themes onto the player.
2. Reboot.

![Step one](images/step-1.png)

| Setting | Value |
| --- | --- |
| Theme | Rondo |
"""


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def doc_cache(tmp_path, monkeypatch):
    """Point the document cache at a temporary folder."""
    monkeypatch.setattr(docs_source, "cache_dir", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------
# Which links we can read
# --------------------------------------------------------------------------

def test_github_blob_links_resolve_to_raw_markdown():
    assert docs_source.markdown_source(RONDO) == (
        "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/install.md"
    )
    assert docs_source.markdown_anchor(RONDO) == "first-start"
    assert docs_source.repo_from_url(RONDO) == "jarvissax/rondo"
    assert docs_source.base_url_for(docs_source.markdown_source(RONDO)) == (
        "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/"
    )


def test_raw_and_plain_markdown_links_are_sources():
    raw = "https://raw.githubusercontent.com/o/r/main/docs/install.md"
    assert docs_source.markdown_source(raw) == raw
    plain = "https://example.test/guide/install.md?raw=1"
    assert docs_source.markdown_source(plain) == plain
    assert docs_source.markdown_source("https://example.test/guide/install.markdown")


def test_other_web_pages_are_not_markdown_sources():
    # The Reborn post-install page is a rendered guide, not a file we can parse.
    assert docs_source.markdown_source("https://innioasis.app/y2reborn_wifibt.html") == ""
    # A release page is not a document either.
    assert docs_source.markdown_source("https://github.com/o/r/releases/tag/v1.0.0") == ""
    assert docs_source.markdown_source("https://github.com/o/r") == ""
    assert docs_source.markdown_source("") == ""


def test_markdown_links_ignore_the_fragment_when_fetching():
    assert docs_source.markdown_source(RONDO).endswith("install.md")


# --------------------------------------------------------------------------
# Preparing a document
# --------------------------------------------------------------------------

def test_relative_images_and_links_are_resolved_against_the_file():
    base = docs_source.base_url_for(docs_source.markdown_source(RONDO))
    text = docs_source.rewrite_relative_urls(
        "![a](images/x.png)\n"
        "[b](other.md)\n"
        "[c](../README.md)\n"
        "[d](https://example.test/keep)\n"
        "[e](#local)\n"
        '[f](/assets/root.png)\n'
        '<img src="images/y.png">',
        base,
    )
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/images/x.png" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/other.md" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/README.md" in text
    assert "https://example.test/keep" in text
    assert "(#local)" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/assets/root.png" in text
    assert "https://raw.githubusercontent.com/jarvissax/rondo/main/docs/images/y.png" in text


def test_front_matter_is_not_shown_to_the_reader():
    assert strip_front_matter(GUIDE).lstrip().startswith("# Rondo")
    prepared = docs_source.prepare_markdown(GUIDE)
    assert "title: Install" not in prepared


def test_a_link_section_finds_its_heading():
    assert docs_source.heading_text_for_anchor(GUIDE, "first-start") == "First start"
    assert docs_source.heading_text_for_anchor(GUIDE, "#first-start") == "First start"
    # An anchor that names no heading does not stop the document opening.
    assert docs_source.heading_text_for_anchor(GUIDE, "nowhere") == ""


# --------------------------------------------------------------------------
# Fetching and caching
# --------------------------------------------------------------------------

def test_a_downloaded_guide_is_cached_for_offline_reading(doc_cache):
    calls = []

    def fetch(url, timeout=0):
        calls.append(url)
        return GUIDE

    assert docs_source.fetch_markdown(RONDO, fetch=fetch).startswith("---")
    assert docs_source.cached_markdown(docs_source.markdown_source(RONDO))

    def offline(url, timeout=0):
        raise RuntimeError("no connection")

    # Still fresh: no second request is made.
    assert docs_source.fetch_markdown(RONDO, fetch=offline).startswith("---")
    assert len(calls) == 1
    # Forced refresh that fails keeps the copy we already have.
    assert docs_source.fetch_markdown(RONDO, fetch=offline, force=True).startswith("---")


def test_an_html_error_page_is_not_treated_as_a_guide(doc_cache):
    html = docs_source.fetch_markdown(
        RONDO, fetch=lambda url, timeout=0: "<!DOCTYPE html><html>Not found</html>"
    )
    assert html == ""
    assert docs_source.cached_markdown(docs_source.markdown_source(RONDO)) == ""


def test_a_failed_first_fetch_is_empty(doc_cache):
    def offline(url, timeout=0):
        raise RuntimeError("no connection")

    assert docs_source.fetch_markdown(RONDO, fetch=offline) == ""


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def test_markdown_keeps_tables_and_images_and_drops_colours():
    html = render_markdown_html(
        '<span style="color:#ff0000">red</span>\n\n| a | b |\n| --- | --- |\n| 1 | 2 |\n\n'
        "![shot](https://example.test/x.png)\n",
        text_color="#123456",
    )
    assert "<table" in html
    assert "x.png" in html
    assert "#ff0000" not in html
    assert "#123456" in html


OLD_DISCORD = "https://discord.gg/3zbfaTNN7V"
NEW_DISCORD = "https://discord.gg/zHrT2zrcek"


def test_legacy_discord_invites_point_at_the_current_server():
    body = (
        f"[Join the Discord]({OLD_DISCORD})\n\n"
        f"[![Community](images/community.png)]({OLD_DISCORD})\n\n"
        f'<a href="{OLD_DISCORD}">HTML invite</a>\n'
    )
    rewritten = rewrite_legacy_discord_invite(body)
    assert NEW_DISCORD in rewritten
    assert OLD_DISCORD not in rewritten
    # Labels and image markup are the document's; only the target moved.
    assert "[Join the Discord](" in rewritten
    assert "![Community](images/community.png)" in rewritten
    assert "HTML invite" in rewritten
    # Preparing a document twice must not double-rewrite anything.
    assert rewrite_legacy_discord_invite(rewritten) == rewritten
    prepared = docs_source.prepare_markdown(body)
    assert NEW_DISCORD in prepared
    assert OLD_DISCORD not in prepared


def test_rendered_markdown_uses_the_current_discord_invite():
    body = (
        f"[Join the Discord]({OLD_DISCORD})\n\n"
        f"[![Community](images/community.png)]({OLD_DISCORD})\n"
    )
    html = render_markdown_html(body)
    assert NEW_DISCORD in html
    assert OLD_DISCORD not in html
    assert "Join the Discord" in html
    assert "Community" in html
    assert "images/community.png" in html


# --------------------------------------------------------------------------
# The site's own pages
# --------------------------------------------------------------------------

def test_release_notes_link_names_the_site_page():
    url = docs_source.release_notes_url(
        "https://github.com/y1-community/y1-stock-rom.git", "v3.1.2",
        name="Original Software", model="y1",
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://innioasis.app/release_notes.html"
    query = parse_qs(parts.query)
    assert query["repo"] == ["y1-community/y1-stock-rom"]
    assert query["tag"] == ["v3.1.2"]
    assert query["name"] == ["Original Software"]
    assert query["model"] == ["Y1"]
    # A release the site cannot name keeps github.com as its page.
    assert docs_source.release_notes_url("", "v1") == ""


def test_translated_release_notes_open_the_site_page():
    """The notes open on our page, with the reader's language, not Google's proxy."""
    from src.translate import get_google_translate_release_url

    url = get_google_translate_release_url(
        {"source_repo": "y1-community/y1-stock-rom", "tag_name": "3.2.1", "name": "3.2.1"},
        "fi",
    )
    parts = urlsplit(url)
    assert parts.netloc == "innioasis.app"
    assert parts.path == "/release_notes.html"
    assert "translate.google" not in url and "translate.goog" not in url
    query = parse_qs(parts.query)
    assert query["repo"] == ["y1-community/y1-stock-rom"]
    assert query["tag"] == ["3.2.1"]
    assert query["lang"] == ["fi"]

    # Without ``source_repo`` the release page still names its repository, and
    # the tag comes from the release URL itself.
    from_page = get_google_translate_release_url(
        {"html_url": "https://github.com/jarvissax/rondo/releases/tag/v1.2.0"}, "de"
    )
    query = parse_qs(urlsplit(from_page).query)
    assert query["repo"] == ["jarvissax/rondo"]
    assert query["tag"] == ["v1.2.0"]
    assert query["lang"] == ["de"]


def test_post_install_page_carries_the_document_and_its_section():
    url = docs_source.post_install_page_url(
        RONDO, software="Rondo", model="Y1"
    )
    parts = urlsplit(url)
    assert parts.path == "/post_install.html"
    query = parse_qs(parts.query)
    assert query["url"] == ["https://raw.githubusercontent.com/jarvissax/rondo/main/docs/install.md"]
    assert query["section"] == ["first-start"]
    assert query["repo"] == ["jarvissax/rondo"]
    assert query["software"] == ["Rondo"]
    assert query["model"] == ["Y1"]
    # A non-Markdown post_install keeps its own page.
    assert docs_source.post_install_page_url("https://innioasis.app/y2reborn_wifibt.html") == ""


def test_site_links_carry_the_donations_preference(monkeypatch):
    monkeypatch.setattr(
        player_update_check, "is_donation_ui_disabled", lambda: True
    )
    url = _with_donation_preference(docs_source.post_install_page_url(RONDO))
    assert parse_qs(urlsplit(url).query)["donations"] == ["false"]
    notes = _with_donation_preference(docs_source.release_notes_url("a/b", "v1"))
    assert parse_qs(urlsplit(notes).query)["donations"] == ["false"]


# --------------------------------------------------------------------------
# The completion screen
# --------------------------------------------------------------------------

def test_completion_screen_links_the_guide_instead_of_embedding_it(qapp):
    """The completion screen sends the reader to the guide, not a pane of it."""
    for url in (RONDO, "https://innioasis.app/y2reborn_wifibt.html"):
        page = FlashPage()
        try:
            page.set_post_install_urls(url, "")
            page.set_device_done(donations_shown=False)

            assert not page._post_install_hint.isHidden()
            assert page._done_mark._symbol == "www"
            assert not hasattr(page, "_post_install_doc")
        finally:
            page.deleteLater()


def test_the_globe_opens_the_site_reader_for_a_markdown_guide(qapp, monkeypatch):
    opened = []
    from src import browser

    monkeypatch.setattr(browser, "open_browser", lambda url, *a, **k: opened.append(url) or True)
    monkeypatch.setattr(player_update_check, "is_donation_ui_disabled", lambda: True)

    page = FlashPage()
    page.set_post_install_urls(RONDO, "")
    page._open_post_install_guide()

    assert opened and opened[-1].startswith("https://innioasis.app/post_install.html?")
    assert "first-start" in opened[-1]
    assert "donations=false" in opened[-1]


def test_site_preferences_url_variables(monkeypatch):
    from src.browser import _with_site_preferences
    from src.i18n import translator

    monkeypatch.setattr(player_update_check, "is_donation_ui_disabled", lambda: True)
    translator().set_language("fr")

    url = _with_site_preferences("https://innioasis.app/firmware-list.html?model=Y1")
    parsed = parse_qs(urlsplit(url).query)
    assert parsed["model"] == ["Y1"]
    assert parsed["donations"] == ["false"]
    # The app has no privacy screen, so it declares no consent: asserting one
    # re-enabled feedback and nightly storage for readers who had turned them
    # off on the site. The page keeps the answer it already stores.
    assert "privacy" not in parsed
    assert parsed["lang"] == ["fr"]
    assert "theme" in parsed
