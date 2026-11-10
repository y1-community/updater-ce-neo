"""Select Software screen model shared by curses and the numbered fallback.

Mouse hit-testing and keyboard movement return the same row. Neither path
imports Qt. The filters are the ones on the window: Brand, Model, Type,
Software, and Refresh.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import catalog
from .catalog import Package
from .controls import segment
from .releases import (
    ReleaseRow,
    brand_names,
    hardware_types,
    models_for,
    notes_text,
    package_for,
    software_names,
)

LIST_TOP = 4
APP_VERSION = "2.5.4"


@dataclass
class BrowseState:
    brand: str = "Innioasis"
    model: str = "Y1"
    hardware_type: str = "A"
    software: str = ""
    brands: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    software_choices: list[str] = field(default_factory=list)
    releases: list[ReleaseRow] = field(default_factory=list)
    index: int = 0
    scroll: int = 0
    show_old: bool = False
    show_240p: bool = False
    show_nightly: bool = False
    language: str = "en"
    focus: str = "list"
    status: str = ""
    page: str = "software"
    keep_data: bool = False
    notes_scroll: int = 0
    page_scroll: int = 0
    package: object = None
    install_percent: float = 0.0
    install_label: str = ""
    install_log: list[str] = field(default_factory=list)
    result_lines: list[str] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)
    notices_status: str = ""
    update_result: dict = field(default_factory=dict)
    update_downloaded: str = ""
    quit: bool = False


def read_gui_filters() -> dict:
    """The window's saved Rockbox filters, when this account has them.

    On macOS that is the Qt preferences file for ``innioasis`` / ``updater``.
    A text-session override in the cache wins after the reader changes a
    switch here, so the window's file is left alone.
    """
    override = _filter_path()
    if override.is_file():
        try:
            data = json.loads(override.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError):
            pass
    candidates = [
        Path.home() / "Library" / "Preferences" / "com.innioasis.updater.plist",
        Path.home() / ".config" / "innioasis" / "updater.conf",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        if path.suffix == ".plist":
            try:
                loaded = plistlib.loads(path.read_bytes())
            except (OSError, plistlib.InvalidFileException):
                continue
            if isinstance(loaded, dict):
                return loaded
        else:
            return _read_ini(path)
    return {}


def _filter_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "innioasis-updater" / "tui-filters.json"


def _read_ini(path: Path) -> dict:
    data: dict = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or line.strip().startswith("["):
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip()
    except OSError:
        return {}
    return data


def _flag(data: dict, key: str) -> bool:
    value = data.get(key)
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def apply_saved_filters(state: BrowseState, data: dict | None = None) -> None:
    data = read_gui_filters() if data is None else data
    state.show_240p = _flag(data, "show_240p_rockbox")
    state.show_old = _flag(data, "show_old_rockbox_builds") or state.show_240p
    state.show_nightly = _flag(data, "show_nightly_dev_releases")
    language = shared_language() or str(data.get("language") or "").strip()
    if language:
        state.language = language


def save_filters(state: BrowseState) -> None:
    path = _filter_path()
    payload = {
        "show_240p_rockbox": state.show_240p,
        "show_old_rockbox_builds": state.show_old,
        "show_nightly_dev_releases": state.show_nightly,
        "language": state.language,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        remember_language(state.language)
    except OSError:
        pass


def cycle(values: list[str], current: str, step: int) -> str:
    if not values:
        return current
    try:
        index = next(i for i, value in enumerate(values) if value.casefold() == current.casefold())
    except StopIteration:
        index = 0
    return values[(index + step) % len(values)]


def fill_choices(state: BrowseState, items: list[Package]) -> None:
    state.brands = brand_names(items)
    if state.brand not in state.brands:
        state.brand = "Innioasis" if "Innioasis" in state.brands else state.brands[0]
    state.models = models_for(items, state.brand)
    if state.model not in state.models and state.models:
        state.model = "Y1" if "Y1" in state.models else state.models[0]
    state.types = hardware_types(state.model)
    if state.types:
        if state.hardware_type not in state.types:
            state.hardware_type = state.types[0]
    else:
        state.hardware_type = ""
    state.software_choices = software_names(items, state.brand, state.model)
    if not any(name.casefold() == state.software.casefold() for name in state.software_choices):
        rockbox = next((name for name in state.software_choices if name.casefold() == "rockbox"), "")
        state.software = rockbox or (state.software_choices[0] if state.software_choices else "")


def current_package(state: BrowseState, items: list[Package]) -> Package | None:
    return package_for(items, state.brand, state.model, state.software)


def release_index_at(y: int, *, list_top: int = LIST_TOP, scroll: int = 0, count: int = 0, list_height: int = 1) -> int | None:
    """Map a pointer row to a release index. ``None`` is not a release row."""
    if count <= 0 or y < list_top:
        return None
    offset = y - list_top
    visible = min(list_height, max(0, count - scroll))
    if offset < 0 or offset >= visible:
        return None
    index = scroll + offset
    if index < 0 or index >= count:
        return None
    return index


def move_index(index: int, key: str, count: int) -> int:
    if count <= 0:
        return 0
    if key in ("up", "k"):
        return (index - 1) % count
    if key in ("down", "j"):
        return (index + 1) % count
    return index


def activate(rows: list[ReleaseRow], index: int | None) -> ReleaseRow | None:
    if index is None or index < 0 or index >= len(rows):
        return None
    return rows[index]


def keep_visible(state: BrowseState, height: int) -> None:
    if not state.releases:
        state.index = 0
        state.scroll = 0
        return
    state.index = max(0, min(state.index, len(state.releases) - 1))
    if state.index < state.scroll:
        state.scroll = state.index
    last = state.scroll + max(1, height) - 1
    if state.index > last:
        state.scroll = state.index - max(1, height) + 1


def interface_kind(*, stdin_tty: bool, stdout_tty: bool, term: str, curses_ok: bool) -> str:
    """``curses`` when a real terminal can host it, otherwise numbered prompts.

    Windows without the curses module, ``TERM=dumb``, and piped output all
    take the plain path. The plain path still lists the same releases.
    """
    if not stdin_tty or not stdout_tty:
        return "plain"
    if (term or "").strip().lower() in {"", "dumb"}:
        return "plain"
    if not curses_ok:
        return "plain"
    return "curses"


def parse_mouse_report(text: str) -> tuple[int, int] | None:
    """SGR mouse report (``CSI < button ; x ; y M``) as 0-based coordinates.

    Terminals that are not curses, including Windows terminals that emit this
    sequence, use the same hit test as a curses click.
    """
    match = re.search(r"\x1b\[<(\d+);(\d+);(\d+)[Mm]", text or "")
    if not match:
        return None
    return int(match.group(2)) - 1, int(match.group(3)) - 1


def failover_text(kind: str, detail: str = "") -> str:
    messages = {
        "no-display": "No display is available. The window is not opened; this text session is the interface.",
        "term-dumb": "TERM=dumb cannot draw a full-screen menu. Numbered prompts are used instead.",
        "piped": "Output is not a terminal. Numbered prompts are used instead.",
        "curses": "curses is not available. Numbered prompts are used instead.",
        "missing-component": "A needed component is missing. " + detail,
        "unsupported-arch": "This architecture cannot use that tool. " + detail,
        "download-failure": "The download failed. " + detail + " A partial file is kept so the next try can resume.",
    }
    return messages.get(kind, detail or kind).strip()


def screen_lines(state: BrowseState, version: str = APP_VERSION) -> list[str]:
    """Select Software as buttons. The focused control is wrapped in « »."""
    focused = state.focus
    type_label = state.hardware_type or "—"
    filters = "  ".join(
        (
            segment(focused == "brand", f"Brand: {state.brand}"),
            segment(focused == "model", f"Model: {state.model}"),
            segment(focused == "type", f"Type: {type_label}"),
            segment(focused == "software", f"Software: {state.software or '—'}"),
            segment(focused == "refresh", "Refresh"),
        )
    )
    lines = [
        f"Updater CE {version}    Select Software",
        filters,
        "Available System Software",
    ]
    if not state.releases:
        lines.append("  (no releases for this filter)")
    else:
        for offset, row in enumerate(state.releases):
            mark = ">" if offset == state.index else " "
            lines.append(f"{mark} {row.label}")
    lines.append(segment(focused == "keep", "Keep my data: " + ("on" if state.keep_data else "off")))
    actions = [
        segment(focused == "install", "Install"),
        segment(focused == "file", "Install from a file"),
    ]
    if state.software.casefold() == "rockbox":
        actions.append(segment(focused == "themes", "Themes"))
    lines.append("  ".join(actions))
    lines.append(
        " ".join(
            (
                segment(focused == "settings", "Settings"),
                segment(focused == "support", "Support Us"),
                segment(focused == "language", "Language"),
                segment(focused == "notices", "Notices"),
                segment(focused == "updates", "Updates"),
                segment(focused == "credits", "Credits"),
                segment(focused == "menu", "Menu"),
            )
        )
    )
    if state.status:
        lines.append(state.status)
    return lines


def notes_for(state: BrowseState) -> str:
    row = activate(state.releases, state.index) if state.releases else None
    return notes_text(row, state.software)


def package_links(package) -> list[str]:
    """Post-install pages the catalogue names for this package.

    The window opens these when an install finishes (``post_install`` and
    ``post_install_2`` in ``slidia_manifest.xml``). The terminal session shows
    the same URLs instead of hiding them behind a browser it may not have.
    """
    links: list[str] = []
    for attribute in ("post_install", "post_install_2"):
        value = str(getattr(package, attribute, "") or "").strip()
        if value and value not in links:
            links.append(value)
    return links


def keep_data_offered(package) -> bool:
    """The catalogue-level veto on Keep Data, without importing Qt."""
    return not bool(getattr(package, "do_not_keep_data", False))


#: The same destinations ``config.DONATION_LINKS`` gives the window. Copied
#: here so the lite payload does not import the GUI config module. A test
#: checks the URLs still match.
DONATION_LINKS = {
    "kofi": "https://innioasis.app/donate.html?method=kofi",
    "bmc": "https://innioasis.app/donate.html?method=bmc",
    "boosty": "https://innioasis.app/donate.html?method=boosty",
    "paypal": "https://paypal.me/ryanspecter282",
    "revolut": "https://revolut.me/rspecter",
    "patreon": "https://www.patreon.com/ryanspecter",
    "honeygain": "https://join.honeygain.com/ITSRY2B5D7",
}
DONATION_CRYPTO = (
    ("Bitcoin (BTC)", "bc1qv4gjkqczqy4wdl297k7ak5swtkusgaz5au6c6g"),
    ("Ethereum / Arbitrum / Optimism", "0x5E902083ee1B3A05dd39d824012B39cB10FB80D3"),
    ("SHIBA INU (ERC-20)", "0x5E902083ee1B3A05dd39d824012B39cB10FB80D3"),
)
DONATION_METHODS = (
    ("Ko-fi", DONATION_LINKS["kofi"]),
    ("Buy Me a Coffee", DONATION_LINKS["bmc"]),
    ("PayPal", DONATION_LINKS["paypal"]),
    ("Revolut", DONATION_LINKS["revolut"]),
    ("Patreon", DONATION_LINKS["patreon"]),
    ("Honeygain", DONATION_LINKS["honeygain"]),
)


def donation_methods(language: str = "") -> tuple[tuple[str, str], ...]:
    """The method row the window shows for this language.

    A Russian interface gets Boosty alone, which is the only page that takes
    a Russian card. Everywhere else gets the worldwide row.
    """
    if (language or "").strip().lower().split("-")[0] == "ru":
        return (("Boosty", DONATION_LINKS["boosty"]),)
    return DONATION_METHODS


def settings_lines(state: BrowseState) -> list[str]:
    def on(flag: bool) -> str:
        return "on" if flag else "off"

    return [
        "Settings",
        f"1  [{'x' if state.show_old else ' '}] Old Rockbox builds",
        f"2  [{'x' if state.show_240p else ' '}] 240p Rockbox releases",
        f"3  [{'x' if state.show_nightly else ' '}] Nightly releases",
        f"4  [{'x' if state.keep_data else ' '}] Keep my data during install",
        "5  Back",
        "Keep my data keeps photos, downloads and installed apps in internal storage.",
        "It is offered again before the write; a package without its preserve-data",
        "scatter is installed normally rather than erased by guesswork.",
        "The window still opens when the app is started with no flag.",
    ]


def support_lines(language: str = "") -> list[str]:
    lines = [
        "Support Us",
        "Donations keep the catalogue and the builds available.",
        "",
        "Donate with:",
    ]
    lines += [f"  {name:<22} {url}" for name, url in donation_methods(language)]
    if (language or "").strip().lower().split("-")[0] != "ru":
        lines.append("")
        lines.append("Crypto:")
        lines += [f"  {name:<22} {address}" for name, address in DONATION_CRYPTO]
    lines += [
        "",
        "Support page   https://innioasis.app/support_devs.html",
        "Credits        https://innioasis.app/credits.html",
        "Press Enter or q to go back.",
    ]
    return lines


_LANGUAGE_ROWS: list[tuple[str, str]] | None = None


def language_rows() -> list[tuple[str, str]]:
    """``(code, native name)`` in the same order as the window's language menu.

    Reads ``assets/i18n/languages.json`` directly. The translation module is
    not imported: it is large, and on Python older than 3.10 it cannot even
    be loaded. The window reads the same file.
    """
    global _LANGUAGE_ROWS
    if _LANGUAGE_ROWS is not None:
        return _LANGUAGE_ROWS
    rows: list[tuple[str, str]] = []
    candidates = [
        Path(__file__).resolve().parents[2] / "assets" / "i18n" / "languages.json",
    ]
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        candidates.insert(0, Path(meipass) / "assets" / "i18n" / "languages.json")
        candidates.insert(0, Path(meipass) / "i18n" / "languages.json")
    for path in candidates:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, dict):
            continue
        shipped = {
            path.stem
            for path in path.parent.glob("*.json")
            if path.stem != "languages"
        }
        for code, entry in raw.items():
            if shipped and str(code) not in shipped:
                continue
            if not isinstance(entry, dict):
                continue
            native = entry.get("native") or entry.get("english") or code
            rows.append((str(code), str(native)))
        if rows:
            break
    if not rows:
        rows = [(code, code) for code in ("en", "en-GB", "en-US", "de", "fr", "es", "ja", "zh-CN")]
    rows.sort(key=lambda row: row[1].casefold())
    _LANGUAGE_ROWS = rows
    return rows


def language_lines(state: BrowseState) -> list[str]:
    lines = ["Language", f"Current: {state.language}", ""]
    for index, (code, native) in enumerate(language_rows(), 1):
        mark = ">" if code == state.language else " "
        lines.append(f"{mark} {index:>3}  {native}  ({code})")
    lines.append("")
    lines.append("Same list as the window. Enter or a click selects. q goes back.")
    return lines


def remember_language(code: str) -> None:
    """Store a language where the window reads it, and in the text-session cache."""
    code = (code or "").strip()
    if not code:
        return
    _write_shared_language(code)


def shared_language() -> str:
    """The language the window last saved, if this account has one."""
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\innioasis\updater") as key:
                value, _kind = winreg.QueryValueEx(key, "language")
            return str(value or "").strip()
        except OSError:
            return ""
    data = _read_gui_store()
    return str(data.get("language") or "").strip()


def _gui_store_path() -> Path | None:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Preferences" / "com.innioasis.updater.plist"
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / "innioasis" / "updater.conf"
    return None


def _read_gui_store() -> dict:
    path = _gui_store_path()
    if path is None or not path.is_file():
        return {}
    if path.suffix == ".plist":
        try:
            loaded = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return _read_ini(path)


def _write_shared_language(code: str) -> None:
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\innioasis\updater") as key:
                winreg.SetValueEx(key, "language", 0, winreg.REG_SZ, code)
        except OSError:
            pass
        return
    path = _gui_store_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".plist":
            raw = path.read_bytes() if path.is_file() else b""
            data = plistlib.loads(raw) if raw else {}
            if not isinstance(data, dict):
                data = {}
            data["language"] = code
            fmt = plistlib.FMT_BINARY if raw.startswith(b"bplist") else plistlib.FMT_XML
            path.write_bytes(plistlib.dumps(data, fmt=fmt))
            return
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else ["[General]"]
        replaced = False
        out = []
        for line in lines:
            if line.startswith("language="):
                out.append(f"language={code}")
                replaced = True
            else:
                out.append(line)
        if not replaced:
            if out and not out[0].startswith("["):
                out.insert(0, "[General]")
            out.append(f"language={code}")
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    except (OSError, plistlib.InvalidFileException, ValueError):
        pass


def credits_lines(version: str = APP_VERSION) -> list[str]:
    return [
        f"Updater CE {version}",
        "by Ryan Specter and the Innioasis community",
        "Community catalogue and installer for Innioasis players.",
        "",
        "Thanks to: MTKclient, Rockbox, Slidia, the theme authors,",
        "the translators, the testers and everyone who donated.",
        "",
        "Credits and thanks  https://innioasis.app/credits.html?thank-you=1",
        "Support Us         https://innioasis.app/support_devs.html",
        "Press Enter or q to go back.",
    ]


def toggle_setting(state: BrowseState, which: str) -> None:
    if which == "old":
        state.show_old = not state.show_old
        if not state.show_old:
            state.show_240p = False
    elif which == "240p":
        state.show_240p = not state.show_240p
        if state.show_240p:
            state.show_old = True
    elif which == "nightly":
        state.show_nightly = not state.show_nightly
    elif which == "keep_data":
        state.keep_data = not state.keep_data
    save_filters(state)


APP_UPDATE_REPOS = ("y1-community/updater-ce-neo", "ryan-specter/updater-ce-neo")
NOTICE_URL = "https://innioasis.app/notice.xml"


def parse_version(tag: str):
    """Normalise a release tag to ``(major, minor, patch)``."""
    match = re.match(r"^\D*(\d+)\.(\d+)(?:\.(\d+))?", (tag or "").strip())
    if not match:
        return None
    return tuple(int(part or 0) for part in match.groups())


def is_newer(tag: str, current: str) -> bool:
    new, cur = parse_version(tag), parse_version(current)
    return bool(new and cur and new > cur)


def _platform_name() -> str:
    return sys.platform


def app_update_asset(release: dict, system: str | None = None) -> dict | None:
    """The release asset that updates this system, without the lite archive."""
    system = system or _platform_name()
    assets = [
        asset for asset in (release.get("assets") or [])
        if asset.get("name") and "lite" not in asset["name"].lower()
        and "terminal" not in asset["name"].lower()
    ]
    if system == "darwin":
        targets = ("innioasis.updater.for.mac.dmg",)
        allowed = lambda low: low.endswith(".dmg")
    elif system == "win32":
        arm = platform.machine().lower() in ("aarch64", "arm64")
        targets = (("installer-arm64.exe",) if arm else ()) + ("installer.exe", "updaterce-setup.exe")
        allowed = lambda low: low.endswith(".exe")
    else:
        arm = platform.machine().lower() in ("aarch64", "arm64")
        if arm:
            targets = (
                "updaterce-linux-portable-arm64.tar.gz",
                "updaterce-arm64.appimage",
                "updaterce-linux-portable-arm64.zip",
            )
        else:
            targets = (
                "updaterce-linux-portable.tar.gz",
                "updaterce.appimage",
                "updaterce-linux-portable.zip",
            )
        allowed = lambda low: (
            low.endswith((".appimage", ".tar.gz", ".zip"))
            and not any(tok in low for tok in ("darwin", "mac", "win32", "windows"))
        )
    for target in targets:
        for asset in assets:
            if (asset.get("name") or "").lower() == target and allowed(target):
                return asset
    for asset in assets:
        low = (asset.get("name") or "").lower()
        if allowed(low):
            return asset
    return None


def check_app_update(current: str = APP_VERSION, timeout: float = 8.0) -> dict:
    """Check the CE release feed with the same client the catalogue uses."""
    last_error = ""
    for repo in APP_UPDATE_REPOS:
        try:
            release = catalog.latest_release(repo, timeout=timeout)
        except RuntimeError as exc:
            last_error = str(exc)
            continue
        tag = str(release.get("tag_name") or "")
        result = {
            "current": current,
            "latest": tag.lstrip("vV"),
            "tag": tag,
            "name": release.get("name") or "",
            "body": release.get("body") or "",
            "release_url": release.get("html_url") or "",
            "newer": False,
            "failed": False,
        }
        if not is_newer(tag, current):
            return result
        asset = app_update_asset(release)
        if asset is None:
            result["reason"] = "the newest release has no package for this system"
            return result
        result.update(
            newer=True,
            asset=asset.get("name") or "",
            download=asset.get("browser_download_url") or "",
        )
        return result
    return {
        "current": current,
        "latest": "",
        "newer": False,
        "failed": True,
        "error": last_error or "the release feed could not be read",
    }


def update_lines(result: dict, downloaded: str = "") -> list[str]:
    if not result:
        return ["App update", "Press r to check the release feed.", "q goes back."]
    if result.get("failed"):
        return [
            "App update",
            f"Installed: {result.get('current', APP_VERSION)}",
            "Could not check for updates.",
            str(result.get("error") or ""),
            "The catalogue still works; check again later.",
            "r retries   q goes back.",
        ]
    lines = [
        "App update",
        f"Installed: {result.get('current', APP_VERSION)}",
        f"Published: {result.get('latest') or 'unknown'}",
    ]
    if result.get("newer"):
        lines += [
            "",
            "A newer version is available for this system.",
            f"Package: {result.get('asset') or 'release page'}",
        ]
        if result.get("download"):
            lines.append(f"Download: {result['download']}")
        if result.get("release_url"):
            lines.append(f"Release:  {result['release_url']}")
        body = [line.strip() for line in str(result.get("body") or "").splitlines() if line.strip()]
        if body:
            lines += ["", "Release notes:", *body[:12]]
        lines += [
            "",
            "d downloads the package to ~/Downloads (no silent install).",
            "o opens the release page. q goes back.",
        ]
        if downloaded:
            lines += ["", downloaded]
        return lines
    lines.append("")
    lines.append("This is the newest published version for this system.")
    if result.get("reason"):
        lines.append(str(result["reason"]).capitalize() + ".")
    lines.append("r checks again   q goes back.")
    if downloaded:
        lines += ["", downloaded]
    return lines


def _normalise_platform(value: str) -> str:
    low = (value or "").strip().lower()
    if low in {"mac", "macos", "osx", "darwin"}:
        return "darwin"
    if low in {"win", "windows", "win32"}:
        return "win32"
    if low.startswith("linux"):
        return "linux"
    return low


def _normalise_arch(value: str) -> str:
    low = (value or "").strip().lower()
    if low in {"aarch64", "arm64"}:
        return "arm64"
    if low in {"amd64", "x64", "x86_64"}:
        return "x86_64"
    if low in {"i386", "i486", "i586", "i686", "x86"}:
        return "x86"
    return low


def _notice_date(value: str):
    text = (value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def fetch_notices(model: str = "", timeout: float = 8.0) -> list[dict]:
    """Fetch the same notice.xml the window reads, with the standard library."""
    request = urllib.request.Request(
        NOTICE_URL,
        headers={"User-Agent": "UpdaterCE-terminal"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f"the notice feed could not be read: {exc}") from exc
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"the notice feed was not valid XML: {exc}") from exc

    elements = [root] if root.tag == "notice" else root.findall(".//notice")
    rows: list[dict] = []
    today = date.today()
    host_platform = _normalise_platform(sys.platform)
    host_arch = _normalise_arch(platform.machine())
    for element in elements:
        title = (element.get("title") or "").strip()
        if not title:
            title_element = element.find("title")
            title = (title_element.text or "").strip() if title_element is not None else ""
        content = (element.get("content") or "").strip()
        for tag in ("content", "body"):
            if content:
                break
            child = element.find(tag)
            content = (child.text or "").strip() if child is not None else ""
        if not title and not content:
            continue
        until = _notice_date(element.get("until") or "")
        if until and until < today:
            continue
        start = _notice_date(element.get("from") or "")
        if start and start > today:
            continue
        platforms = {
            _normalise_platform(part)
            for part in f"{element.get('platform') or ''},{element.get('os') or ''}".split(",")
            if part.strip()
        }
        if platforms and host_platform not in platforms:
            continue
        arches = {
            _normalise_arch(part)
            for part in (element.get("arch") or "").replace(";", ",").split(",")
            if part.strip()
        }
        if arches and host_arch not in arches and "universal" not in arches:
            continue
        models = {part.strip().upper() for part in (element.get("models") or "").split(",") if part.strip()}
        if models and model and model.upper() not in models and "ALL" not in models:
            continue
        url = (element.get("url") or "").strip()
        if not url:
            link = element.find("url")
            if link is None:
                link = element.find("link")
            url = (link.text or "").strip() if link is not None else ""
        rows.append({
            "id": (element.get("id") or "").strip(),
            "title": title or "Notice",
            "content": content,
            "url": url,
            "from": element.get("from") or "",
            "severity": (element.get("severity") or "info").lower(),
            "persistent": (element.get("persistent") or "").lower() in {"true", "1", "yes"},
        })
    rows.sort(key=lambda row: row.get("from") or "", reverse=True)
    return rows


def notices_lines(state: BrowseState) -> list[str]:
    lines = ["Notices", state.notices_status or ""]
    if not state.notices:
        lines += ["", "No active notices for this player and system.", "r checks again   q goes back."]
        return lines
    for index, notice in enumerate(state.notices, 1):
        lines.append("")
        lines.append(f"{index}. {notice.get('title') or 'Notice'}")
        if notice.get("from"):
            lines.append(f"   {notice['from']}")
        for body_line in str(notice.get("content") or "").splitlines():
            lines.append("   " + body_line.strip())
        if notice.get("url"):
            lines.append(f"   {notice['url']}")
    lines += ["", "o opens the first notice   r refreshes   q goes back."]
    return lines


def menu_lines(state: BrowseState) -> list[str]:
    return [
        "Menu",
        f"1  Select Software ({len(state.releases)} releases)",
        "2  Notices",
        "3  App update",
        "4  Settings",
        "5  Support Us",
        "6  Language",
        "7  Credits / Thanks",
        "8  Quit",
        "",
        "The same destinations as the window's sidebar.",
        "Number or click chooses. q goes back.",
    ]
