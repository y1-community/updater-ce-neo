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
    if paths.IS_MAC:
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
