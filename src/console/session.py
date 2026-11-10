"""Select Software screen model shared by curses and the numbered fallback.

Mouse hit-testing and keyboard movement return the same row. Neither path
imports Qt. The filters are the ones on the window: Brand, Model, Type,
Software, and Refresh.
"""

from __future__ import annotations

import json
import os
import plistlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Package
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
    language = str(data.get("language") or "").strip()
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
    type_label = state.hardware_type or "—"
    lines = [
        f"Updater CE {version}    Select Software",
        (
            f"Brand: {state.brand}    Model: {state.model}    "
            f"Type: {type_label}    Software: {state.software or '—'}    Refresh"
        ),
        "Available System Software",
    ]
    if not state.releases:
        lines.append("  (no releases for this filter)")
    else:
        for offset, row in enumerate(state.releases):
            mark = ">" if offset == state.index else " "
            lines.append(f"{mark} {row.label}")
    actions = "Install    Install from a file"
    if state.software.casefold() == "rockbox":
        actions += "    Themes"
    lines.append(actions)
    lines.append("Settings    Support Us    Language    Credits / Thanks")
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


#: The same destinations the window's donation dialog offers. Kept here as
#: plain URLs so the text session needs no Qt, network or browser module.
DONATION_METHODS = (
    ("Ko-fi", "https://innioasis.app/donate.html?method=kofi"),
    ("Buy Me a Coffee", "https://innioasis.app/donate.html?method=bmc"),
    ("Boosty", "https://innioasis.app/donate.html?method=boosty"),
    ("PayPal", "https://paypal.me/ryanspecter282"),
    ("Revolut", "https://revolut.me/rspecter"),
    ("Patreon", "https://www.patreon.com/ryanspecter"),
)


def settings_lines(state: BrowseState) -> list[str]:
    def on(flag: bool) -> str:
        return "on" if flag else "off"

    return [
        "Settings",
        f"1  Old Rockbox builds: {on(state.show_old)}",
        f"2  240p Rockbox releases: {on(state.show_240p)}",
        f"3  Nightly releases: {on(state.show_nightly)}",
        f"4  Keep my data during install: {on(state.keep_data)}",
        "5  Back",
        "Keep my data writes the firmware without erasing internal storage.",
        "The window still opens when the app is started with no flag.",
    ]


def support_lines() -> list[str]:
    lines = [
        "Support Us",
        "Donations keep the catalogue and the builds available.",
        "",
        "Donate with:",
    ]
    lines += [f"  {name:<16} {url}" for name, url in DONATION_METHODS]
    lines += [
        "",
        "Support page   https://innioasis.app/support_devs.html",
        "Credits        https://innioasis.app/credits.html",
        "Press Enter or q to go back.",
    ]
    return lines


def language_lines(state: BrowseState) -> list[str]:
    choices = ("en-GB", "en", "de", "fr", "es", "ja", "zh-CN")
    lines = ["Language", f"Current: {state.language}"]
    for index, code in enumerate(choices, 1):
        mark = ">" if code == state.language else " "
        lines.append(f"{mark} {index}  {code}")
    lines.append("The window stores the same language key. This screen remembers the choice.")
    return lines


LANGUAGES = ("en-GB", "en", "de", "fr", "es", "ja", "zh-CN")


def credits_lines(version: str = APP_VERSION) -> list[str]:
    return [
        f"Updater CE {version}",
        "by Ryan Specter",
        "Community catalogue and installer for Innioasis players.",
        "Credits / Thanks",
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
