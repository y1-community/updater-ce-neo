"""Markdown documents a project publishes, and the site pages that show them.

Two things a reader needs after (or beside) an install are written by the
project that made the firmware, not by us:

* **Post-install steps.** A firmware's ``post_install`` in
  ``slidia_manifest.xml`` often points at a Markdown file in its own
  repository — Rondo's ``docs/install.md#first-start``, for example. The
  website renders those in ``post_install.html``; the app links to that page
  from its completion screen, so finishing an install never sends anyone to a
  bare GitHub blob page.
* **Release notes.** A release's notes and title live on its GitHub release.
  The website renders them in ``release_notes.html``, which is where the
  "See More" link goes instead of GitHub.

This module is the app's half of that contract:

``markdown_source(url)``
    the raw URL to read for a link we can parse (GitHub blob, GitHub raw, or
    any ``.md`` file), or ``""`` when the link is some other kind of page.
``markdown_anchor(url)`` / ``heading_text_for_anchor``
    the section a link points at, so a guide opens at Rondo's "First start"
    rather than at the top of a long file.
``prepare_markdown``
    a document's text with front matter removed and its relative images and
    links resolved against the file's own folder.
``fetch_markdown``
    a small disk cache around the download: a guide read once is read again
    without a connection.
``post_install_page_url`` / ``release_notes_url``
    the innioasis.app page for the same content, for "open in browser".

Nothing here raises. An unreadable or unparseable link is an empty string and
the caller keeps its ordinary web link.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

#: Where the site's own reader pages live. The query contract is shared with
#: the website: ``release_notes.html?repo=<owner/repo>&tag=<tag>`` and
#: ``post_install.html?url=<raw markdown>[&section=][&repo=][&software=][&model=]``.
SITE = "https://innioasis.app"
RAW_HOST = "https://raw.githubusercontent.com"

#: How long a downloaded document is trusted before it is looked up again.
#: Project guides change rarely, so a stale copy is better than a failed read
#: offline.
CACHE_TTL_SECONDS = 6 * 60 * 60
REQUEST_TIMEOUT = 10
USER_AGENT = "InnioasisUpdater/3.0 (+https://innioasis.app)"

_MARKDOWN_SUFFIXES = (".md", ".markdown", ".mdown", ".mkd")
_GITHUB_BLOB_ACTIONS = ("blob", "raw")

#: ``](target)`` and ``<img src="target">`` / ``<a href="target">`` in Markdown.
_MD_TARGET = re.compile(r"(\]\(\s*)(<?[^)\s>]+>?)")
_HTML_TARGET = re.compile(r"((?:src|href)\s*=\s*[\"'])([^\"']+)([\"'])", re.IGNORECASE)
_ATX_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


# ---------------------------------------------------------------------------
# Reading a link
# ---------------------------------------------------------------------------

def _parts(url: str):
    return urlsplit(str(url or "").strip())


def _segments(parts) -> list[str]:
    return [segment for segment in parts.path.split("/") if segment]


def is_github_host(host: str) -> bool:
    host = (host or "").lower()
    return host in ("github.com", "www.github.com", "raw.githubusercontent.com")


def markdown_source(url: str) -> str:
    """The raw URL holding the Markdown behind ``url`` (``""`` when none).

    GitHub blob links are rewritten to ``raw.githubusercontent.com``; GitHub's
    own ``/raw/`` links and plain ``.md`` URLs are used as they are. Anything
    else — a rendered guide page, a release page, a PDF — is not a document we
    can parse, and the caller should open it in the browser.
    """
    parts = _parts(url)
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    segments = _segments(parts)

    if host in ("github.com", "www.github.com"):
        if len(segments) >= 5 and segments[2] in _GITHUB_BLOB_ACTIONS:
            repo = f"{segments[0]}/{segments[1]}"
            ref = segments[3]
            path = "/".join(segments[4:])
            return f"{RAW_HOST}/{repo}/{ref}/{path}"
        return ""

    if host == "raw.githubusercontent.com":
        # A raw link without a file behind it reads as the repository listing.
        return str(url).strip() if len(segments) >= 4 else ""

    if parts.path.lower().rstrip("/").endswith(_MARKDOWN_SUFFIXES):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    return ""


def is_markdown(url: str) -> bool:
    """True when :func:`markdown_source` can read ``url``."""
    return bool(markdown_source(url))


def markdown_anchor(url: str) -> str:
    """The section name a link points at (``#first-start`` -> ``first-start``)."""
    return unquote(_parts(url).fragment or "").strip()


def repo_from_url(url: str) -> str:
    """``owner/repo`` for a GitHub blob/raw link (``""`` otherwise)."""
    parts = _parts(url)
    host = (parts.hostname or "").lower()
    if not is_github_host(host):
        return ""
    segments = _segments(parts)
    if len(segments) < 2:
        return ""
    return f"{segments[0]}/{segments[1]}"


def base_url_for(raw_url: str) -> str:
    """The folder a document lives in, used to resolve its relative links."""
    parts = _parts(raw_url)
    if not parts.scheme:
        return ""
    path = parts.path
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _repo_root(base_url: str) -> str:
    """``https://raw.githubusercontent.com/owner/repo/ref/`` for a GitHub raw base."""
    parts = _parts(base_url)
    segments = _segments(parts)
    if (parts.hostname or "").lower() == "raw.githubusercontent.com" and len(segments) >= 4:
        return (
            f"{RAW_HOST}/{segments[0]}/{segments[1]}/{segments[2]}/"
        )
    return ""


def _resolve_target(target: str, base_url: str) -> str:
    """Turn one Markdown link target into an absolute one where it is relative."""
    original = target
    wrapped = ""
    if original.startswith("<") and original.endswith(">"):
        wrapped = "<>"
        original = original[1:-1]
    text = original.strip()
    if not text:
        return target
    lowered = text.lower()
    if lowered.startswith(("#", "data:", "mailto:", "tel:", "javascript:", "ftp:")):
        return target
    if lowered.startswith(("http://", "https://", "//")):
        return target
    if text.startswith("/"):
        root = _repo_root(base_url)
        if root:
            resolved = root + text.lstrip("/")
            return f"<{resolved}>" if wrapped else resolved
        return target
    if not base_url:
        return target
    resolved = urljoin(base_url, text)
    return f"<{resolved}>" if wrapped else resolved


def rewrite_relative_urls(text: str, base_url: str) -> str:
    """Resolve a document's relative images and links against its own folder.

    A guide writes ``![](images/step-1.png)`` next to itself. Rendered in the
    app, that path has to become the raw file's real URL or the image never
    loads.
    """
    if not base_url or not text:
        return text

    def md(match):
        return match.group(1) + _resolve_target(match.group(2), base_url)

    def html(match):
        return match.group(1) + _resolve_target(match.group(2), base_url) + match.group(3)

    text = _MD_TARGET.sub(md, text)
    return _HTML_TARGET.sub(html, text)


def prepare_markdown(text: str, base_url: str = "") -> str:
    """A fetched document ready to render: no front matter, absolute links."""
    from .markdown_render import rewrite_legacy_discord_invite, strip_front_matter

    prepared = strip_front_matter(str(text or ""))
    prepared = rewrite_legacy_discord_invite(prepared)
    return rewrite_relative_urls(prepared, base_url)


def github_slug(text: str) -> str:
    """The anchor GitHub gives a heading (``First start!`` -> ``first-start``)."""
    lowered = str(text or "").strip().lower()
    lowered = re.sub(r"[^\w\s-]", "", lowered, flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", lowered).strip("-")


def heading_text_for_anchor(text: str, anchor: str) -> str:
    """The heading in ``text`` that ``anchor`` names (``""`` when none).

    The completion screen scrolls to this heading so a link to Rondo's
    ``#first-start`` opens at its final install steps. An anchor that names no
    heading is not an error: the document simply opens at the top.
    """
    wanted = str(anchor or "").strip().lower()
    if not wanted:
        return ""
    for line in str(text or "").splitlines():
        match = _ATX_HEADING.match(line)
        if not match:
            continue
        title = re.sub(r"[*_`]", "", match.group(1)).strip()
        if not title:
            continue
        if title.lower() == wanted or github_slug(title) == github_slug(wanted):
            return title
    return ""


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def cache_dir() -> Path:
    """Per-user folder for downloaded documents (created on first use)."""
    from .paths import app_cache_dir

    folder = app_cache_dir() / "docs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(str(url).encode("utf-8")).hexdigest()[:32]
    return cache_dir() / f"{digest}.md"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").lstrip("\ufeff")
    except OSError:
        return ""


def _write(path: Path, text: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        logger.debug("Could not cache %s", path, exc_info=True)


def cached_markdown(url: str) -> str:
    """The last copy of ``url`` this install downloaded (``""`` when none)."""
    try:
        return _read(_cache_path(url))
    except OSError:
        return ""


def clear_cache() -> None:
    """Forget every downloaded document (tests, and a refresh after an edit)."""
    try:
        folder = cache_dir()
    except OSError:
        return
    for path in folder.glob("*.md"):
        try:
            path.unlink()
        except OSError:
            logger.debug("Could not remove %s", path, exc_info=True)


def _is_fresh(path: Path, now: Optional[float] = None) -> bool:
    try:
        fetched = path.stat().st_mtime
    except OSError:
        return False
    current = time.time() if now is None else now
    return current - fetched < CACHE_TTL_SECONDS


def _looks_like_html(text: str) -> bool:
    """True for a login/404 page served in place of the document."""
    head = text.lstrip()[:400].lower()
    return head.startswith("<!doctype html") or head.startswith("<html")


def _clean(text) -> str:
    cleaned = str(text or "").lstrip("\ufeff")
    if not cleaned.strip() or _looks_like_html(cleaned):
        return ""
    return cleaned


def _fetch_text(url: str, timeout: float = REQUEST_TIMEOUT) -> str:
    from .network import requests_module

    response = requests_module().get(
        url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    response.raise_for_status()
    return response.text


def fetch_markdown(
    raw_url: str,
    *,
    timeout: float = REQUEST_TIMEOUT,
    fetch=None,
    force: bool = False,
) -> str:
    """Download ``raw_url`` (cached, with a stale copy as the offline answer).

    ``fetch`` takes ``(url, timeout)`` and returns the response text; it exists
    so tests and callers that already have the text never touch the network.
    """
    url = markdown_source(raw_url) or str(raw_url or "").strip()
    if not url:
        return ""
    try:
        path = _cache_path(url)
    except OSError:
        path = None
    cached = _read(path) if path is not None else ""
    if cached and not force and path is not None and _is_fresh(path):
        return cached

    fetcher = fetch or _fetch_text
    try:
        text = _clean(fetcher(url, timeout))
    except Exception as error:
        logger.debug("Could not read %s: %s", url, error)
        return cached
    if not text:
        return cached
    if path is not None:
        _write(path, text)
    return text


# ---------------------------------------------------------------------------
# The site pages
# ---------------------------------------------------------------------------

def normalize_repo(value) -> str:
    """``https://github.com/owner/repo.git`` -> ``owner/repo`` (``""`` when invalid)."""
    text = str(value or "").strip()
    text = re.sub(r"^https?://(?:www\.)?github\.com/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.git$", "", text, flags=re.IGNORECASE).strip("/")
    if text.count("/") != 1:
        return ""
    owner, name = text.split("/")
    return f"{owner}/{name}" if owner and name else ""


def release_notes_url(
    repo, tag, *, name: str = "", model: str = "", software: str = ""
) -> str:
    """The site's release-notes page for one release of ``repo``.

    The page reads the release's title and notes from GitHub and renders them
    with our styling and our own Get it in Updater / Download Portable Zip
    buttons — which is why the app sends "See More" here rather than to
    github.com. ``software`` is the catalogue slug the page uses for its own
    download buttons; the website's reader accepts it alongside ``repo``.
    """
    repo_name = normalize_repo(repo)
    release_tag = str(tag or "").strip()
    if not repo_name or not release_tag:
        return ""
    params = [("repo", repo_name), ("tag", release_tag)]
    if str(name or "").strip():
        params.append(("name", str(name).strip()))
    if str(model or "").strip():
        params.append(("model", str(model).strip().upper()))
    if str(software or "").strip():
        params.append(("software", str(software).strip()))
    return f"{SITE}/release_notes.html?{urlencode(params)}"


def post_install_page_url(
    url,
    *,
    repo: str = "",
    software: str = "",
    model: str = "",
) -> str:
    """The site's post-install page for a Markdown guide (``""`` when not Markdown).

    Only a document we can parse has a page there; a firmware whose
    ``post_install`` is an ordinary guide page keeps that page as its target.
    The site's reader calls the section ``anchor``; ``section`` is kept for the
    older app-generated links the site still accepts.
    """
    source = markdown_source(url)
    if not source:
        return ""
    params = [("url", source)]
    section = markdown_anchor(url)
    if section:
        params.append(("section", section))
        params.append(("anchor", section))
    repo_name = normalize_repo(repo) or repo_from_url(source)
    if repo_name:
        params.append(("repo", repo_name))
    if str(software or "").strip():
        params.append(("software", str(software).strip()))
    if str(model or "").strip():
        params.append(("model", str(model).strip().upper()))
    return f"{SITE}/post_install.html?{urlencode(params)}"


def _catalog_packages() -> list:
    """Catalogue entries from ``slidia_manifest.xml`` (live copy first)."""
    from . import catalog

    return list(catalog.LIVE_CATALOG or catalog.CATALOG or [])


def catalog_package_for_repo(repo):
    """The catalogue entry whose repository is ``repo`` (``None`` when none).

    ``slidia_manifest.xml`` is the shared source of truth: a repository that
    lists a package there has a firmware page, a release-notes page and a guide
    page on innioasis.app, so a link to GitHub can be handed to the parser
    pages instead.
    """
    wanted = normalize_repo(repo)
    if not wanted:
        return None
    for package in _catalog_packages():
        if normalize_repo(getattr(package, "repo", "")) == wanted:
            return package
    return None


def firmware_page_url(repo, *, model: str = "", software: str = "") -> str:
    """The site's firmware page for a project repository (``""`` when unnamed)."""
    package = catalog_package_for_repo(repo)
    slug = str(software or getattr(package, "slug", "") or "").strip()
    target_model = str(model or getattr(package, "model", "") or "").strip().upper()
    if not slug and not target_model:
        return ""
    params = [(key, value) for key, value in (("model", target_model), ("software", slug)) if value]
    return f"{SITE}/firmware.html?{urlencode(params)}"


def _github_release_tag(url: str) -> str:
    """The tag of a ``github.com/<owner>/<repo>/releases/tag/<tag>`` link."""
    parts = _parts(url)
    if not is_github_host((parts.hostname or "").lower()):
        return ""
    segments = _segments(parts)
    if len(segments) >= 5 and segments[2] == "releases" and segments[3] == "tag":
        return "/".join(segments[4:])
    return ""


def site_page_for_link(
    url,
    *,
    model: str = "",
    software: str = "",
    name: str = "",
) -> str:
    """The innioasis.app page that renders ``url`` (``""`` when it has none).

    Every Markdown document, repository and release the catalogue knows is
    shown by one of the site's parser pages. Sending a reader there means the
    document keeps our styling and download buttons, and the site applies the
    visitor's language and donation preferences — a bare GitHub page has no way
    to do either. Links the catalogue does not know, and every non-repository
    GitHub page (an issue, a pull request, a commit), keep their own target.
    """
    raw = url.toString() if hasattr(url, "toString") else str(url or "")
    raw = raw.strip()
    parts = _parts(raw)
    if parts.scheme not in ("http", "https"):
        return ""
    host = (parts.hostname or "").lower()
    # Already on the site: the page is the destination.
    if host == "innioasis.app" or host.endswith(".innioasis.app"):
        return ""

    source = markdown_source(raw)
    package = catalog_package_for_repo(repo_from_url(source or raw))
    if source:
        page = post_install_page_url(
            raw,
            repo=repo_from_url(source),
            software=software or getattr(package, "name", ""),
            model=model or getattr(package, "model", ""),
        )
        if page:
            return page

    if not is_github_host(host):
        return ""
    segments = _segments(parts)
    repo = repo_from_url(raw)
    if not repo:
        return ""
    tag = _github_release_tag(raw)
    if tag or (len(segments) >= 3 and segments[2] == "releases"):
        page = release_notes_url(
            repo,
            tag,
            name=name or getattr(package, "name", ""),
            model=model or getattr(package, "model", ""),
            software=getattr(package, "slug", "") or software,
        )
        if page:
            return page
    # A repository page (root, tree, README, a non-Markdown blob) is the
    # project's firmware page. Issue/PR/commit links stay specific to GitHub.
    if len(segments) >= 3 and segments[2] in (
        "pull",
        "issues",
        "commit",
        "compare",
        "actions",
        "wiki",
        "discussions",
        "security",
        "pulse",
        "graphs",
    ):
        return ""
    page = firmware_page_url(
        repo, model=model or getattr(package, "model", ""), software=software
    )
    return page


def open_site_link(url, *, model: str = "", software: str = "", name: str = "") -> bool:
    """Open ``url`` in the browser, through the site's parser page when it has one.

    This is the single entry point for a link clicked inside the app's own
    Markdown views (notices, release notes, guides), so those views cannot
    bypass the site's reader or its language/donation preferences.
    """
    from .browser import open_browser

    raw = url.toString() if hasattr(url, "toString") else str(url or "")
    if not raw.strip():
        return False
    return open_browser(
        site_page_for_link(raw, model=model, software=software, name=name) or raw
    )
