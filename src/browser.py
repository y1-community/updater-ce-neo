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

def _with_site_preferences(url: str) -> str:
    """Pass privacy, donations, theme, accessibility, and language preferences
    to innioasis.app and themes.innioasis.app as URL variables."""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        from . import player_update_check

        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host != "innioasis.app" and not host.endswith(".innioasis.app"):
            return url

        query_dict = dict(parse_qsl(parts.query, keep_blank_values=True))

        # 1. Donations preference
        if player_update_check.is_donation_ui_disabled():
            query_dict["donations"] = "false"

        # 2. Privacy preference (defaults to all enabled for optimal experience)
        if "privacy" not in query_dict:
            query_dict["privacy"] = "all"

        # 3. Language preference
        try:
            from .i18n import translator
            current_lang = translator().lang
            if current_lang and "lang" not in query_dict:
                query_dict["lang"] = current_lang.lower()
        except Exception:
            pass

        # 4. Theme preference
        try:
            from .ui.dark import is_dark
            is_high_contrast = False
            if sys.platform == "darwin":
                try:
                    from AppKit import NSWorkspace
                    is_high_contrast = bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldIncreaseContrast())
                except Exception:
                    pass
            theme = "high-contrast" if is_high_contrast else ("dark" if is_dark() else "dark")
            if "theme" not in query_dict:
                query_dict["theme"] = theme
        except Exception:
            pass

        # 5. Accessibility preference
        try:
            from .ui.widgets import reduce_motion
            if reduce_motion() and "accessibility" not in query_dict:
                query_dict["accessibility"] = "reduce-motion"
        except Exception:
            pass

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


def open_browser(url: str, new_window: bool = True) -> bool:
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
