"""Cross-platform browser URL launcher ensuring new windows and foreground focus."""

import logging
import os
import platform
import shutil
import subprocess
import sys
from typing import Optional

from . import paths

logger = logging.getLogger(__name__)


def _with_donation_preference(url: str) -> str:
    """Tell innioasis.app not to show donation asks when the user opted out.

    The site reads the query flag on every page it serves. GitHub and other
    destinations are untouched.
    When donations are disabled: sets ?donations=false.
    When donations are enabled: ensures no donations parameter is present in the URL.
    """
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        from . import player_update_check

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host != "innioasis.app" and not host.endswith(".innioasis.app"):
            return url
        # Parse existing query params as a list of pairs so we can cleanly filter
        query_pairs = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k != "donations"
        ]

        if player_update_check.is_donation_ui_disabled():
            query_pairs.append(("donations", "false"))

        new_query = urlencode(query_pairs)
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except Exception:
        logger.debug("Could not add the donations preference to %s", url, exc_info=True)
        return url

def _native_accent_hex() -> str | None:
    """The accent the desktop declares for this user, or None.

    AppKit's control accent on macOS, the DWM accent on Windows, the desktop
    palette's accent through Qt. ``None`` means the platform answered nothing,
    which the site treats as "keep your own fallback".
    """
    try:
        from PySide6.QtWidgets import QApplication

        from .ui.dark import get_declared_accent_color

        app = QApplication.instance()
        return get_declared_accent_color(app.palette() if app is not None else None)
    except Exception:
        logger.debug("No desktop accent colour is available", exc_info=True)
        return None


def _with_site_preferences(url: str, lang: str | None = None) -> str:
    """Pass privacy, donations, theme, accent, accessibility, and language
    preferences to innioasis.app and themes.innioasis.app as URL variables.

    ``lang`` overrides the app's current locale for the pages that show a
    translated copy (a notice's Google Translate view, for instance), so the
    site and the translation agree on the language.
    """
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        from . import player_update_check

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        # Google's proxy of the site is still the site: a translated reader
        # page must apply the same preferences as the original.
        on_site = (
            host == "innioasis.app"
            or host.endswith(".innioasis.app")
            or host == "innioasis-app.translate.goog"
        )
        if not on_site:
            return url

        query_dict = dict(parse_qsl(parts.query, keep_blank_values=True))

        # 1. Donations preference. Always explicit: the site stores it for the
        # visitor on every page, so a reader who turned donation asks back on
        # after turning them off must not keep the stored "off" state.
        query_dict["donations"] = (
            "false" if player_update_check.is_donation_ui_disabled() else "true"
        )

        # 2. Privacy consent is deliberately not asserted. The app has no
        # privacy screen, so it has nothing to declare; sending `privacy=all`
        # here re-enabled feedback prompts and nightly-build storage on every
        # link the app opened, even for a reader who had turned both off on
        # the site itself. Leaving the parameter out lets the page keep the
        # answer it already stores.

        # 3. Language preference. Send the app's own locale code (``zh-CN``,
        # ``pt-BR``): the site normalises it, while a lowercased tag can lose
        # the region the reader actually chose.
        try:
            if lang is None:
                from .i18n import translator
                current_lang = (translator().lang or "").strip()
            else:
                current_lang = str(lang).strip()
            if current_lang and "lang" not in query_dict:
                query_dict["lang"] = current_lang
        except Exception:
            pass

        # 4. Theme preference. The gallery already gives `theme` another
        # meaning (a theme page's own name), and its shell reads `site_theme`
        # first for exactly that reason, so the appearance travels under the
        # name that cannot collide.
        try:
            from .ui.dark import is_dark
            is_high_contrast = False
            if sys.platform == "darwin":
                try:
                    from AppKit import NSWorkspace
                    is_high_contrast = bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldIncreaseContrast())
                except Exception:
                    pass
            theme = "high-contrast" if is_high_contrast else ("dark" if is_dark() else "light")
            theme_key = "site_theme" if host == "themes.innioasis.app" else "theme"
            if theme_key not in query_dict and "theme" not in query_dict and "site_theme" not in query_dict:
                query_dict[theme_key] = theme
        except Exception:
            pass

        # 5. Accessibility preference
        try:
            from .ui.widgets import reduce_motion
            if reduce_motion() and "accessibility" not in query_dict:
                query_dict["accessibility"] = "reduce-motion"
        except Exception:
            pass

        # 6. Accent colour, and the CE handoff. The site paints its accent
        # tokens with this, so a page opened from the app arrives already
        # matching the desktop. `ce=true` marks the handoff even when the
        # desktop declares no accent, which is what lets the site offer to
        # keep matching (falling back to the browser's own accent).
        accent = _native_accent_hex()
        if accent and "accent" not in query_dict:
            query_dict["accent"] = accent
        if "ce" not in query_dict:
            query_dict["ce"] = "true"

        new_query = urlencode(list(query_dict.items()))
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
        )
    except Exception:
        logger.debug("Could not add site preferences to %s", url, exc_info=True)
        return url


def _portable_download(url: str) -> str:
    """Point a text link to a ROM zip at the package built for this machine.

    Every in-app text link that leaves for a release asset passes through
    ``open_browser``. A link to the original ``rom.zip`` becomes the mirror's
    ``rom-macos.zip`` / ``rom-linux.zip`` / etc. where one exists; anything
    else, and every PowerPC or 32-bit Intel Mac, keeps the original URL.
    """
    if "/releases/download/" not in url:
        return url
    try:
        from . import portable

        return portable.resolve_download_link(url) or url
    except Exception:
        logger.debug("Could not resolve a portable download for %s", url, exc_info=True)
        return url


_KNOWN_LINUX_BROWSERS = (
    "firefox",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "vivaldi",
    "opera",
)


def _find_linux_browser() -> Optional[str]:
    """Identify the default or available graphical web browser binary on Linux."""
    # 1. Respect explicit BROWSER environment variable if set and valid
    browser_env = os.environ.get("BROWSER")
    if browser_env:
        first = browser_env.split(":")[0].strip()
        if shutil.which(first):
            return first

    # 2. Query xdg-settings or xdg-mime for user's configured default browser
    for cmd in (
        ["xdg-settings", "get", "default-web-browser"],
        ["xdg-mime", "query", "default", "x-scheme-handler/https"],
        ["xdg-mime", "query", "default", "x-scheme-handler/http"],
    ):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            out = res.stdout.strip().lower()
            for name in _KNOWN_LINUX_BROWSERS:
                if name in out:
                    path = shutil.which(name)
                    if path:
                        return path
        except Exception:
            pass

    # 3. Fall back to the first known browser binary found in PATH
    for name in _KNOWN_LINUX_BROWSERS:
        path = shutil.which(name)
        if path:
            return path

    return None


_DOWNLOAD_SUFFIXES = (
    ".zip", ".7z", ".tar", ".gz", ".tgz", ".dmg", ".exe", ".msi",
    ".apk", ".img", ".bin", ".iso", ".pkg", ".theme", ".rbtheme",
)
_DONATION_HOSTS = (
    "ko-fi.com",
    "buymeacoffee.com",
    "boosty.to",
    "paypal.me",
    "patreon.com",
    "revolut.me",
)


_BLOCKED_REDIRECT_WORDS = ("payment", "login", "auth")


def refuses_interstitial(url: str) -> bool:
    """True when this address must not be placed on our redirect page.

    The words appear in real login and checkout links, and also in harmless
    ones. Either way the interstitial would be a trusted innioasis.app address
    that forwards the reader, which is the shape a phishing link wants.
    """
    haystack = str(url or "").lower()
    return any(word in haystack for word in _BLOCKED_REDIRECT_WORDS)


def interstitial_url(url: str, *, thank: bool = False) -> str:
    """Send an off-site page through the site's interstitial.

    Donation hosts, and a post_install2 page opened as a thank-you, use
    donate.html. Everything else uses leaving.html. Pages already on
    innioasis.app, and theme or firmware file downloads, are left alone.
    """
    from urllib.parse import quote, urlsplit

    raw = str(url or "").strip()
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        return raw
    if refuses_interstitial(raw):
        return raw
    host = (parts.hostname or "").lower()
    if host in ("innioasis.app", "localhost", "127.0.0.1") or host.endswith(".innioasis.app"):
        return raw
    path = (parts.path or "").lower()
    if any(path.endswith(suffix) for suffix in _DOWNLOAD_SUFFIXES):
        return raw
    donation = thank or any(host == item or host.endswith("." + item) for item in _DONATION_HOSTS)
    page = "donate.html" if donation else "leaving.html"
    return "https://innioasis.app/" + page + "?from=updater-ce&to=" + quote(raw, safe="")


def open_browser(url: str, new_window: bool = True) -> bool:

    # The donation interstitial paints in the visitor's accent before it
    # forwards to Ko-fi/BMAC/Boosty. Carry the desktop accent and the donation
    # choice so the site stores them, the same way the site-to-site handoff
    # works; the page strips them from the address bar afterwards.
    if "innioasis.app/donate.html" in url:
        try:
            from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
            parts = urlsplit(url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            accent = _native_accent_hex()
            if accent:
                query.setdefault("accent", accent)
            query.setdefault("donations", "true")
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        except Exception:
            pass
    """Open a URL in an external browser, prioritizing a new focused window.

    Ensures that when users click links (e.g. Credits / Thanks, donation links,
    or release notes), a browser window opens in the foreground rather than
    silently adding a background tab to an existing browser session.

    Args:
        url: The web URL to open.
        new_window: Whether to request a new browser window (default: True).

    Returns:
        True if the browser was successfully invoked, False otherwise.
    """
    if not url:
        return False

    url = str(url).strip()
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    url = interstitial_url(url)
    url = _with_donation_preference(url)
    url = _with_site_preferences(url)
    url = _portable_download(url)

    # 1. Linux: prefer direct browser invocation with --new-window to guarantee
    # a new window is raised and focused by the window manager
    if platform.system() == "Linux" and new_window:
        browser_bin = _find_linux_browser()
        if browser_bin:
            try:
                cmd = [browser_bin]
                # Firefox, Chrome, Chromium, Brave, Edge all accept --new-window
                base_name = os.path.basename(browser_bin).lower()
                if any(b in base_name for b in ("firefox", "chrome", "chromium", "brave", "edge", "vivaldi", "opera")):
                    cmd.extend(["--new-window", url])
                else:
                    cmd.append(url)

                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return True
            except Exception as e:
                logger.debug("Direct Linux browser launch (%s) failed: %s", browser_bin, e)

    # 2. macOS: /usr/bin/open brings the application to the foreground
    if platform.system() == "Darwin":
        try:
            subprocess.Popen(
                ["open", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception as e:
            logger.debug("macOS open command failed: %s", e)

    # 3. Qt QDesktopServices (carries Qt's window activation token on X11/Wayland/Windows)
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        qurl = QUrl.fromUserInput(url)
        if QDesktopServices.openUrl(qurl):
            return True
    except Exception as e:
        logger.debug("QDesktopServices.openUrl failed: %s", e)

    # 4. Standard library webbrowser fallback with new=1 (new window) and autoraise=True
    try:
        import webbrowser

        # new=1 requests new window, autoraise=True raises window
        if webbrowser.open(url, new=1 if new_window else 2, autoraise=True):
            return True
    except Exception as e:
        logger.debug("webbrowser.open failed: %s", e)

    # 5. Windows shell startfile fallback
    if paths.IS_WINDOWS:
        try:
            os.startfile(url)
            return True
        except Exception as e:
            logger.debug("os.startfile failed: %s", e)

    return False
