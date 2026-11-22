"""Cross-platform browser URL launcher ensuring new windows and foreground focus."""

import contextlib
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
    "firefox-esr",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "brave-browser",
    "brave",
    "microsoft-edge",
    "microsoft-edge-stable",
    "vivaldi",
    "vivaldi-stable",
    "opera",
    "librewolf",
    "zen-browser",
    "zen",
    "waterfox",
    "floorp",
    "epiphany",
    "falkon",
    "konqueror",
    "midori",
)

_LINUX_DESKTOP_OPENERS = (
    "xdg-open",
    "gio",
    "kioclient",
    "kioclient5",
    "kioclient6",
    "kde-open5",
    "kde-open",
    "exo-open",
    "gnome-open",
    "sensible-browser",
    "x-www-browser",
)

_NEW_WINDOW_LINUX_BROWSERS = (
    "firefox",
    "chrome",
    "chromium",
    "brave",
    "edge",
    "vivaldi",
    "opera",
    "librewolf",
    "zen",
    "waterfox",
    "floorp",
)

_LINUX_BUNDLE_ENV_DROP = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONEXECUTABLE",
    "PYINSTALLER_RESET_ENVIRONMENT",
    "APPDIR",
    "APPIMAGE",
    "ARGV0",
    "OWD",
    "GDK_PIXBUF_MODULE_FILE",
    "GDK_PIXBUF_MODULEDIR",
    "GTK_PATH",
    "GIO_EXTRA_MODULES",
    "GIO_MODULE_DIR",
    "GIO_LAUNCHED_DESKTOP_FILE",
    "GIO_LAUNCHED_DESKTOP_FILE_PID",
    "GSETTINGS_SCHEMA_DIR",
    "QT_DEBUG_PLUGINS",
    "LD_LIBRARY_PATH_ORIG",
    "INNIOASIS_HOST_ENV_SAVED",
    "INNIOASIS_APP_BUNDLE_ROOT",
    "INNIOASIS_ORIG_LD_LIBRARY_PATH",
    "INNIOASIS_ORIG_PATH",
    "INNIOASIS_ORIG_XDG_DATA_DIRS",
    "INNIOASIS_ORIG_QT_PLUGIN_PATH",
    "INNIOASIS_ORIG_QT_QPA_PLATFORM_PLUGIN_PATH",
    "INNIOASIS_ORIG_QT_QPA_PLATFORM",
    "INNIOASIS_ORIG_QT_QPA_PLATFORMTHEME",
    "INNIOASIS_ORIG_QT_OPENGL",
    "INNIOASIS_ORIG_LIBGL_ALWAYS_SOFTWARE",
    "INNIOASIS_ORIG_QT_IM_MODULE",
    "INNIOASIS_SAFE_MODE",
    "INNIOASIS_STARTUP_MARKER",
    "INNIOASIS_REPAIR_TRIED",
    "INNIOASIS_TUI",
    "INNIOASIS_GUI_PROBE",
)


def _linux_bundle_roots(env: dict[str, str]) -> tuple[str, ...]:
    """Bundle/AppImage/PyInstaller directories that must never leak to host apps."""
    roots: list[str] = []

    def _add(candidate: Optional[str]) -> None:
        if not candidate:
            return
        try:
            norm = os.path.normpath(os.path.abspath(str(candidate)))
        except Exception:
            norm = str(candidate).rstrip("/")
        if norm and norm != "/" and norm not in roots:
            roots.append(norm)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        _add(meipass)
        parent = os.path.dirname(os.path.normpath(str(meipass)))
        if os.path.basename(os.path.normpath(str(meipass))) == "_internal":
            _add(parent)
    if getattr(sys, "frozen", False) and sys.executable:
        exe_dir = os.path.dirname(os.path.realpath(sys.executable))
        _add(exe_dir)
        _add(os.path.join(exe_dir, "_internal"))
        if exe_dir.endswith(os.sep + os.path.join("usr", "bin")):
            _add(os.path.dirname(os.path.dirname(exe_dir)))
    for key in ("APPDIR", "INNIOASIS_APP_BUNDLE_ROOT"):
        _add(env.get(key))
    return tuple(roots)


def _is_linux_bundle_path(entry: str, bundle_roots: tuple[str, ...]) -> bool:
    """True when ``entry`` points inside the AppImage / PyInstaller bundle."""
    if not entry:
        return False
    norm = os.path.normpath(entry)
    for root in bundle_roots:
        if norm == root or norm.startswith(root + os.sep):
            return True
    parts = norm.split("/")
    for part in parts:
        if part.startswith("_MEI") or part.startswith(".mount_") or part == "_internal":
            return True
    if "/PySide6/" in norm or norm.endswith("/PySide6"):
        return True
    return False


def _filter_colon_path(value: str, bundle_roots: tuple[str, ...]) -> str:
    kept = [
        item for item in value.split(":")
        if item and not _is_linux_bundle_path(item, bundle_roots)
    ]
    return ":".join(kept)


def clean_linux_subprocess_env(
    base_env: Optional[dict[str, str]] = None,
) -> dict[str, str]:
    """Return a host-clean environment for spawning Linux browsers & helpers.

    The Linux AppImage and portable tarball prepend the bundle's private
    libraries (``_internal``, ``usr/lib``) and Qt 6 plugins to
    ``LD_LIBRARY_PATH``, ``QT_PLUGIN_PATH``, ``QT_QPA_PLATFORM_PLUGIN_PATH``,
    ``PATH`` and ``XDG_DATA_DIRS``. Child host processes such as Firefox,
    Chrome, ``xdg-open``, ``gio`` or ``kioclient`` crash with library/ABI
    mismatches if they inherit those paths.
    """
    src = dict(os.environ if base_env is None else base_env)
    bundle_roots = _linux_bundle_roots(src)
    env = dict(src)
    host_saved = bool(src.get("INNIOASIS_HOST_ENV_SAVED"))

    # 1. LD_LIBRARY_PATH: restore host value captured by linux-run.sh or
    # PyInstaller's LD_LIBRARY_PATH_ORIG, then scrub any leftover bundle dirs.
    if host_saved:
        raw_ld = src.get("INNIOASIS_ORIG_LD_LIBRARY_PATH", "")
    elif "LD_LIBRARY_PATH_ORIG" in src:
        raw_ld = src.get("LD_LIBRARY_PATH_ORIG", "")
    else:
        raw_ld = src.get("LD_LIBRARY_PATH", "")
    clean_ld = _filter_colon_path(raw_ld, bundle_roots)
    if clean_ld:
        env["LD_LIBRARY_PATH"] = clean_ld
    else:
        env.pop("LD_LIBRARY_PATH", None)

    # 2. PATH: drop bundle bin dir so host tools win.
    raw_path = src.get("INNIOASIS_ORIG_PATH", "") if host_saved else src.get("PATH", "")
    clean_path = _filter_colon_path(raw_path or src.get("PATH", ""), bundle_roots)
    env["PATH"] = clean_path or "/usr/local/bin:/usr/bin:/bin"

    # 3. XDG_DATA_DIRS: restore host share dirs so desktop MIME associations work.
    if host_saved:
        raw_xdg = src.get("INNIOASIS_ORIG_XDG_DATA_DIRS", "")
    else:
        raw_xdg = src.get("XDG_DATA_DIRS", "")
    clean_xdg = _filter_colon_path(raw_xdg, bundle_roots)
    if clean_xdg:
        env["XDG_DATA_DIRS"] = clean_xdg
    elif host_saved or _is_linux_bundle_path(src.get("XDG_DATA_DIRS", ""), bundle_roots):
        env.pop("XDG_DATA_DIRS", None)

    # 4. Qt plugin paths: never expose PySide6's private plugins to KDE/Qt tools.
    for var, orig_var in (
        ("QT_PLUGIN_PATH", "INNIOASIS_ORIG_QT_PLUGIN_PATH"),
        ("QT_QPA_PLATFORM_PLUGIN_PATH", "INNIOASIS_ORIG_QT_QPA_PLATFORM_PLUGIN_PATH"),
    ):
        raw_qt = src.get(orig_var, "") if host_saved else src.get(var, "")
        clean_qt = _filter_colon_path(raw_qt, bundle_roots)
        if clean_qt:
            env[var] = clean_qt
        else:
            env.pop(var, None)

    for var in ("QML2_IMPORT_PATH", "QML_IMPORT_PATH", "LD_PRELOAD"):
        if var in env:
            cleaned = _filter_colon_path(env[var], bundle_roots)
            if cleaned:
                env[var] = cleaned
            else:
                env.pop(var, None)

    # Cert/bundle paths pointing inside _MEIPASS break host curl/python/browsers.
    for var in ("SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"):
        if var in env and _is_linux_bundle_path(env[var], bundle_roots):
            env.pop(var, None)

    # 5. Restore host Qt/GL session overrides touched by linux_desktop.py
    # (for example QT_QPA_PLATFORM="wayland;xcb" crashes Qt 5's kioclient5,
    # and safe-mode LIBGL_ALWAYS_SOFTWARE=1 strips GPU acceleration from browsers).
    if host_saved:
        for var, orig_var in (
            ("QT_QPA_PLATFORM", "INNIOASIS_ORIG_QT_QPA_PLATFORM"),
            ("QT_QPA_PLATFORMTHEME", "INNIOASIS_ORIG_QT_QPA_PLATFORMTHEME"),
            ("QT_OPENGL", "INNIOASIS_ORIG_QT_OPENGL"),
            ("LIBGL_ALWAYS_SOFTWARE", "INNIOASIS_ORIG_LIBGL_ALWAYS_SOFTWARE"),
            ("QT_IM_MODULE", "INNIOASIS_ORIG_QT_IM_MODULE"),
        ):
            orig_val = src.get(orig_var, "")
            if orig_val:
                env[var] = orig_val
            else:
                env.pop(var, None)
    else:
        qpa = env.get("QT_QPA_PLATFORM", "")
        if qpa in ("offscreen", "minimal") or ";" in qpa:
            env.pop("QT_QPA_PLATFORM", None)
        if str(src.get("INNIOASIS_SAFE_MODE", "")).strip().lower() in ("1", "true", "yes", "on"):
            if env.get("QT_OPENGL") == "software":
                env.pop("QT_OPENGL", None)
            if env.get("LIBGL_ALWAYS_SOFTWARE") == "1":
                env.pop("LIBGL_ALWAYS_SOFTWARE", None)
            if env.get("QT_IM_MODULE") == "compose":
                env.pop("QT_IM_MODULE", None)

    for key in _LINUX_BUNDLE_ENV_DROP:
        env.pop(key, None)

    return env


@contextlib.contextmanager
def _clean_linux_environ_context(clean_env: dict[str, str]):
    """Temporarily apply ``clean_env`` to ``os.environ`` for Qt/webbrowser fallbacks."""
    saved = dict(os.environ)
    try:
        for key in list(os.environ.keys()):
            if key not in clean_env:
                os.environ.pop(key, None)
        for key, value in clean_env.items():
            if os.environ.get(key) != value:
                os.environ[key] = value
        yield
    finally:
        for key in list(os.environ.keys()):
            if key not in saved:
                os.environ.pop(key, None)
        for key, value in saved.items():
            if os.environ.get(key) != value:
                os.environ[key] = value


def _linux_desktop_user_prefix(env: dict[str, str]) -> list[str]:
    """If running as root via sudo/pkexec, run the browser as the desktop user."""
    geteuid = getattr(os, "geteuid", None)
    if not callable(geteuid) or geteuid() != 0:
        return []
    try:
        import pwd

        pw = None
        sudo_user = (env.get("SUDO_USER") or "").strip()
        if sudo_user and sudo_user != "root":
            pw = pwd.getpwnam(sudo_user)
        else:
            pk_uid = (env.get("PKEXEC_UID") or "").strip()
            if pk_uid.isdigit() and int(pk_uid) > 0:
                pw = pwd.getpwuid(int(pk_uid))
        if pw is None or pw.pw_uid == 0:
            return []
        env["HOME"] = pw.pw_dir
        env["USER"] = pw.pw_name
        env["LOGNAME"] = pw.pw_name
        runtime_dir = f"/run/user/{pw.pw_uid}"
        if os.path.isdir(runtime_dir):
            env.setdefault("XDG_RUNTIME_DIR", runtime_dir)
            bus_sock = os.path.join(runtime_dir, "bus")
            if os.path.exists(bus_sock):
                env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={bus_sock}")
        sudo_bin = shutil.which("sudo", path=env.get("PATH"))
        if sudo_bin:
            return [sudo_bin, "-u", pw.pw_name, "-E", "--"]
        runuser_bin = shutil.which("runuser", path=env.get("PATH"))
        if runuser_bin:
            return [runuser_bin, "-u", pw.pw_name, "--"]
    except Exception:
        logger.debug("Could not resolve desktop user for root browser launch", exc_info=True)
    return []


def _which_clean(name: str, env: dict[str, str]) -> Optional[str]:
    return shutil.which(name, path=env.get("PATH"))


def _first_linux_desktop_opener(env: dict[str, str]) -> Optional[str]:
    for opener in _LINUX_DESKTOP_OPENERS:
        path = _which_clean(opener, env)
        if path:
            return path
    return None


def _is_flatpak_desktop_id(desktop_id: str) -> bool:
    stem = desktop_id.strip().lower()
    if stem.endswith(".desktop"):
        stem = stem[:-8]
    # Reverse-DNS IDs like org.mozilla.firefox or app.zen_browser.zen.
    return stem.count(".") >= 2


def _find_linux_browser(env: Optional[dict[str, str]] = None) -> Optional[str]:
    """Identify the default or available graphical web browser / URL opener on Linux."""
    clean_env = clean_linux_subprocess_env(env)

    # 1. Respect explicit BROWSER environment variable if set and valid
    browser_env = clean_env.get("BROWSER")
    if browser_env:
        for candidate in browser_env.split(":"):
            first = candidate.strip()
            if not first:
                continue
            resolved = _which_clean(first, clean_env)
            if resolved:
                return resolved

    # 2. Query xdg-settings or xdg-mime for user's configured default browser
    #    using the clean host environment so XDG/MIME lookups work inside AppImage.
    desktop_default = ""
    for cmd in (
        ["xdg-settings", "get", "default-web-browser"],
        ["xdg-mime", "query", "default", "x-scheme-handler/https"],
        ["xdg-mime", "query", "default", "x-scheme-handler/http"],
    ):
        try:
            exe = _which_clean(cmd[0], clean_env)
            if not exe:
                continue
            res = subprocess.run(
                [exe, *cmd[1:]],
                capture_output=True,
                text=True,
                timeout=2,
                env=clean_env,
            )
            out = (res.stdout or "").strip()
            if res.returncode == 0 and out:
                desktop_default = out.splitlines()[0].strip()
                break
        except Exception:
            pass

    if desktop_default:
        low = desktop_default.lower()
        # Flatpak / reverse-DNS default browsers (e.g. org.mozilla.firefox.desktop,
        # app.zen_browser.zen.desktop) should go through the desktop opener
        # (xdg-open / gio / kioclient) rather than launching a different native binary.
        if _is_flatpak_desktop_id(low):
            opener = _first_linux_desktop_opener(clean_env)
            if opener:
                return opener
        for name in sorted(_KNOWN_LINUX_BROWSERS, key=len, reverse=True):
            if name in low:
                path = _which_clean(name, clean_env)
                if path:
                    return path
        # Configured default isn't a known native binary name: let desktop opener handle it.
        opener = _first_linux_desktop_opener(clean_env)
        if opener:
            return opener

    # 3. Prefer standard desktop opener (xdg-open / gio / kioclient / kde-open)
    #    with clean host env so Snap, Flatpak, and DE-configured browsers work.
    opener = _first_linux_desktop_opener(clean_env)
    if opener:
        return opener

    # 4. Fall back to the first known browser binary found in clean PATH
    for name in _KNOWN_LINUX_BROWSERS:
        path = _which_clean(name, clean_env)
        if path:
            return path

    return None


def _linux_cmd_for(tool_path: str, url: str, new_window: bool) -> list[str]:
    base_name = os.path.basename(tool_path).lower()
    if base_name == "gio":
        return [tool_path, "open", url]
    if base_name in ("kioclient", "kioclient5", "kioclient6"):
        return [tool_path, "exec", url]
    if base_name in _LINUX_DESKTOP_OPENERS:
        return [tool_path, url]
    if new_window and any(b in base_name for b in _NEW_WINDOW_LINUX_BROWSERS):
        return [tool_path, "--new-window", url]
    return [tool_path, url]


def _spawn_linux_command(cmd: list[str], env: dict[str, str], wait_s: float = 0.25) -> bool:
    """Spawn a detached Linux browser/opener command and detect immediate failures."""
    try:
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        logger.debug("Linux browser launch (%s) failed to start: %s", cmd[0], e)
        return False
    try:
        rc = proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        # Still running -> launched successfully in the background.
        return True
    except Exception:
        return True
    if isinstance(rc, int) and rc != 0:
        logger.warning("Linux browser launcher %s exited with status %s", cmd[0], rc)
        return False
    return True


def _linux_fallback_tools(primary: str, env: dict[str, str]) -> list[str]:
    """Ordered fallback openers/browsers to try if ``primary`` exits non-zero."""
    candidates: list[str] = [primary]
    seen = {os.path.realpath(primary) if os.path.exists(primary) else primary}
    for name in (*_LINUX_DESKTOP_OPENERS, *_KNOWN_LINUX_BROWSERS):
        resolved = _which_clean(name, env)
        if not resolved:
            continue
        key = os.path.realpath(resolved) if os.path.exists(resolved) else resolved
        if key not in seen:
            seen.add(key)
            candidates.append(resolved)
    return candidates


def open_linux_path(target: str) -> bool:
    """Open a local folder or file in the Linux host desktop handler cleanly."""
    clean_env = clean_linux_subprocess_env()
    prefix = _linux_desktop_user_prefix(clean_env)
    seen: set[str] = set()
    for name in _LINUX_DESKTOP_OPENERS:
        tool = _which_clean(name, clean_env)
        if not tool:
            continue
        key = os.path.realpath(tool) if os.path.exists(tool) else tool
        if key in seen:
            continue
        seen.add(key)
        cmd = prefix + _linux_cmd_for(tool, str(target), new_window=False)
        if _spawn_linux_command(cmd, clean_env):
            return True
    return False


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
    if not url:
        return False

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

    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    url = interstitial_url(url)
    url = _with_donation_preference(url)
    url = _with_site_preferences(url)
    url = _portable_download(url)

    is_linux = platform.system() == "Linux"
    linux_env = clean_linux_subprocess_env() if is_linux else None

    # 1. Linux: launch with a clean host environment (free of AppImage /
    # PyInstaller LD_LIBRARY_PATH and Qt plugin overrides) and verify the
    # launcher did not abort immediately before giving up on fallbacks.
    if is_linux and linux_env is not None:
        browser_bin = _find_linux_browser()
        if browser_bin:
            prefix = _linux_desktop_user_prefix(linux_env)
            for tool in _linux_fallback_tools(browser_bin, linux_env):
                cmd = prefix + _linux_cmd_for(tool, url, new_window)
                if _spawn_linux_command(cmd, linux_env):
                    return True

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
    env_ctx = (
        _clean_linux_environ_context(linux_env)
        if is_linux and linux_env is not None
        else contextlib.nullcontext()
    )
    with env_ctx:
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
