"""Select Software, as a text session.

The window is still what opens when no flag is passed. ``--tui`` draws the
same filters and the same release list: Brand, Model, Type, Software, the
Available System Software rows, notes, Install, Install from a file, Settings,
Support, Language and Credits.

curses is used when the terminal has it. Windows, ``TERM=dumb`` and a pipe
use numbered prompts. Both accept a mouse click when the terminal reports one,
and both accept the keyboard on their own. Nothing here imports PySide.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import catalog, components, download as dl, flash
from .releases import fetch_releases, rows_from_releases
from .session import (
    LANGUAGES,
    LIST_TOP,
    BrowseState,
    activate,
    apply_saved_filters,
    credits_lines,
    current_package,
    failover_text,
    fill_choices,
    interface_kind,
    keep_data_offered,
    keep_visible,
    package_links,
    language_lines,
    move_index,
    notes_for,
    parse_mouse_report,
    release_index_at,
    save_filters,
    screen_lines,
    settings_lines,
    support_lines,
    toggle_setting,
)
from .simulate import install_progress, rockbox_themes

TITLE_VERSION = "2.5.4"


def _version() -> str:
    try:
        repo = Path(__file__).resolve().parents[2]
        for line in (repo / "src" / "config.py").read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return TITLE_VERSION


def _headless() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    return not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")


def _load_items(refresh: bool) -> tuple[list, str]:
    try:
        return catalog.packages(force=refresh), ""
    except RuntimeError as exc:
        return [], str(exc)


def _load_rows(state: BrowseState, items: list, refresh: bool) -> str:
    package = current_package(state, items)
    if package is None:
        state.releases = []
        state.index = 0
        return "No software matches this filter."
    try:
        payload = fetch_releases(package.repo, force=refresh)
    except RuntimeError as exc:
        state.releases = []
        return failover_text("download-failure", str(exc))
    state.releases = rows_from_releases(
        payload,
        model=state.model,
        hardware_type=state.hardware_type,
        repo=package.repo,
        show_old=state.show_old,
        show_240p=state.show_240p,
        show_nightly=state.show_nightly,
    )
    state.package = package
    state.index = 0
    state.scroll = 0
    state.notes_scroll = 0
    if not state.releases:
        return "The catalogue has no installable release for this filter."
    return ""


def _prepare(refresh: bool) -> tuple[BrowseState, list, str]:
    print("Updater CE — loading the catalogue…", flush=True)
    state = BrowseState()
    apply_saved_filters(state)
    items, error = _load_items(refresh)
    if error:
        state.status = failover_text("download-failure", error)
        return state, items, error
    fill_choices(state, items)
    state.status = _load_rows(state, items, refresh)
    return state, items, ""


def _maybe_prefetch() -> None:
    if os.environ.get("INNIOASIS_NO_PREFETCH") or os.environ.get("INNIOASIS_SIMULATE") == "1":
        return
    try:
        needed = components.session_component_keys(headless=_headless())
        present = [key for key in needed if components.resolve(components.COMPONENTS[key])]
        speed = components.measure_mbps(timeout=3.0, sample_bytes=524288)
        plan = components.prefetch_plan(speed, needed, present)
        if not plan:
            return
        print("Fast link: fetching " + ", ".join(plan), file=sys.stderr, flush=True)
        for key in plan:
            components.ensure(key, verbose=True)
    except (RuntimeError, OSError):
        pass


def _progress_printer(label: str):
    def render(done: int, total: int | None) -> None:
        if total:
            line = f"\r{label}: {done * 100 // max(total, 1):3d}%  {dl.human_bytes(done)} / {dl.human_bytes(total)}"
        else:
            line = f"\r{label}: {dl.human_bytes(done)}"
        sys.stderr.write(line)
        sys.stderr.flush()
        if total and done >= total:
            sys.stderr.write("\n")
    return render


def _run_install(state: BrowseState, output: Path) -> str:
    row = activate(state.releases, state.index)
    if row is None:
        return "Select a release first."
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        events = install_progress(sleep=0.04)
        for event in events:
            print(event["message"], flush=True)
        return events[-1]["message"]
    if not row.asset_url:
        return failover_text("download-failure", "this release has no firmware zip.")
    target = output / row.asset_name
    try:
        if not target.exists():
            dl.download(row.asset_url, target, progress=_progress_printer(row.asset_name))
        folder = output / Path(row.asset_name).stem
        dl.extract(target, folder)
    except (RuntimeError, OSError) as exc:
        return failover_text("download-failure", str(exc))
    keep = bool(state.keep_data) and keep_data_offered(state.package)
    if state.keep_data and not keep:
        print("Keep my data is not offered for this release; installing without it.", flush=True)
    system = {"linux": "linux", "darwin": "darwin", "win32": "windows"}.get(sys.platform, sys.platform)
    arch = components.platform_key().split("-", 1)[-1]
    engine, reason = flash.install_engine(system, arch, _headless())
    if engine != "sp-console":
        print(reason, flush=True)
    else:
        try:
            tool = components.ensure("spflash", progress=_progress_printer("SP Flash Tool"))
        except RuntimeError as exc:
            print(failover_text("missing-component", str(exc)), flush=True)
            print(reason or "Using MTKclient instead: mtk wl <folder>.", flush=True)
            tool = None
        else:
            scatter = dl.find_scatter(folder)
            da = tool.parent / "MTK_AllInOne_DA.bin"
            if scatter and da.is_file():
                args = flash.sp_flash_tool_console_args(str(scatter), str(da))
                code = flash._run([str(tool), *args])
                return "Flash complete! Disconnect USB and reboot." if code == 0 else f"SP Flash Tool exited {code}."
    if "rockbox" in state.software.casefold():
        try:
            components.ensure("adb", progress=_progress_printer("adb"))
        except RuntimeError as exc:
            print(failover_text("missing-component", str(exc)), flush=True)
    code = flash.flash_directory(folder, keep_data=keep)
    if code == 0:
        message = "Flash complete! Disconnect USB and reboot."
        links = package_links(state.package)
        if links:
            message += "  Next: " + "  ".join(links)
        return message
    return f"MTKclient exited {code}."


def _run_themes(state: BrowseState) -> str:
    if state.software.casefold() != "rockbox":
        return "Themes are part of a Rockbox install."
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        events = rockbox_themes(sleep=0.04)
        for event in events:
            print(event["message"], flush=True)
        return events[-1]["message"]
    try:
        components.ensure("adb", progress=_progress_printer("adb"))
    except RuntimeError as exc:
        return failover_text("missing-component", str(exc))
    code = flash.adb(["devices"])
    if code != 0:
        return "adb could not list devices."
    return "adb is ready. Connect the player in USB debugging to push the theme pack."


def _run_file(path_text: str) -> str:
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        events = install_progress(sleep=0.02)
        for event in events:
            print(event["message"], flush=True)
        return events[-1]["message"]
    path = Path(path_text).expanduser()
    if not path.exists():
        return f"No such file: {path}"
    if path.is_file():
        folder = path.with_suffix("") if path.suffix else path.parent / (path.name + "-unpacked")
        try:
            dl.extract(path, folder)
        except (RuntimeError, OSError) as exc:
            return failover_text("download-failure", str(exc))
    else:
        folder = path
    code = flash.flash_directory(folder)
    return "Flash complete! Disconnect USB and reboot." if code == 0 else f"MTKclient exited {code}."


def _plain(state: BrowseState, items: list, refresh: bool, output: Path) -> int:
    print(failover_text("piped") if not sys.stdin.isatty() or not sys.stdout.isatty() else "", end="")
    if (os.environ.get("TERM") or "").lower() == "dumb":
        print(failover_text("term-dumb"))
    if not _curses_ok():
        print(failover_text("curses"))
    while True:
        if state.page == "software":
            print("\n".join(screen_lines(state, _version())))
            print()
            print(notes_for(state))
            print()
            print("Number selects a row. i install, f file, h themes, r refresh,")
            print("b brand, m model, t type, s software, g settings, u support, l language, c credits, q quit")
        elif state.page == "settings":
            print("\n".join(settings_lines(state)))
        elif state.page == "support":
            print("\n".join(support_lines()))
        elif state.page == "language":
            print("\n".join(language_lines(state)))
        elif state.page == "credits":
            print("\n".join(credits_lines(_version())))
        try:
            choice = input("> ").strip()
        except EOFError:
            print()
            return 0
        pointer = parse_mouse_report(choice)
        if pointer is not None and state.page == "software":
            index = release_index_at(pointer[1], scroll=state.scroll, count=len(state.releases), list_height=len(state.releases) or 1)
            row = activate(state.releases, index)
            if row is not None:
                state.index = index
            continue
        lowered = choice.lower()
        if lowered in {"q", "quit", "exit"}:
            if state.page != "software":
                state.page = "software"
                continue
            return 0
        if state.page == "settings":
            if choice == "1":
                toggle_setting(state, "old")
                state.status = _load_rows(state, items, False)
            elif choice == "2":
                toggle_setting(state, "240p")
                state.status = _load_rows(state, items, False)
            elif choice == "3":
                toggle_setting(state, "nightly")
                state.status = _load_rows(state, items, False)
            elif choice == "4":
                toggle_setting(state, "keep_data")
                state.status = "Keep my data: " + ("on" if state.keep_data else "off")
            else:
                state.page = "software"
            continue
        if state.page == "language":
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(LANGUAGES):
                    state.language = LANGUAGES[index]
                    save_filters(state)
            state.page = "software"
            continue
        if state.page != "software":
            state.page = "software"
            continue
        if choice.isdigit():
            index = int(choice) - 1
            row = activate(state.releases, index)
            if row is not None:
                state.index = index
            else:
                print("That number is not a release.")
            continue
        if lowered in {"i", "install"}:
            state.status = _run_install(state, output)
            print(state.status)
            continue
        if lowered in {"f", "file"}:
            try:
                path_text = input("File or folder: ").strip()
            except EOFError:
                return 0
            state.status = _run_file(path_text)
            print(state.status)
            continue
        if lowered in {"h", "themes"}:
            state.status = _run_themes(state)
            print(state.status)
            continue
        if lowered in {"d", "keep"}:
            state.keep_data = not state.keep_data
            print("Keep my data: " + ("on" if state.keep_data else "off"))
            continue
        if lowered in {"r", "refresh"}:
            state.status = _load_rows(state, items, True)
            continue
        if lowered in {"g", "settings"}:
            state.page = "settings"
            continue
        if lowered in {"u", "support"}:
            state.page = "support"
            continue
        if lowered in {"l", "language"}:
            state.page = "language"
            continue
        if lowered in {"c", "credits"}:
            state.page = "credits"
            continue
        changed = False
        if lowered in {"b", "brand"}:
            state.brand = _cycle_brand(state)
            changed = True
        elif lowered in {"m", "model"}:
            state.model = _cycle_model(state)
            changed = True
        elif lowered in {"t", "type"} and state.types:
            state.hardware_type = "B" if state.hardware_type == "A" else "A"
            changed = True
        elif lowered in {"s", "software"}:
            state.software = _cycle_software(state)
            changed = True
        elif lowered in {"j", "down"}:
            state.index = move_index(state.index, "down", len(state.releases))
            continue
        elif lowered in {"k", "up"}:
            state.index = move_index(state.index, "up", len(state.releases))
            continue
        else:
            print("Choose a release number, or one of the letters above.")
            continue
        if changed:
            fill_choices(state, items)
            state.status = _load_rows(state, items, False)


def _cycle_brand(state: BrowseState) -> str:
    from .session import cycle
    return cycle(state.brands, state.brand, 1)


def _cycle_model(state: BrowseState) -> str:
    from .session import cycle
    return cycle(state.models, state.model, 1)


def _cycle_software(state: BrowseState) -> str:
    from .session import cycle
    return cycle(state.software_choices, state.software, 1)


def _curses_ok() -> bool:
    try:
        import curses  # noqa: F401
    except ImportError:
        return False
    return True


def _curses_run(state: BrowseState, items: list, output: Path) -> int:
    import curses

    def draw(screen) -> None:
        height, width = screen.getmaxyx()
        screen.erase()
        version = _version()
        screen.addnstr(0, 0, f"Updater CE {version}    Select Software", width - 1, curses.A_BOLD)
        type_label = state.hardware_type or "—"
        filters = (
            f"Brand: {state.brand}  Model: {state.model}  Type: {type_label}  "
            f"Software: {state.software or '—'}  Refresh"
        )
        screen.addnstr(1, 0, filters, width - 1)
        list_width = max(24, min(36, width // 3))
        screen.addnstr(3, 0, "Available System Software", list_width - 1, curses.A_BOLD)
        screen.addnstr(3, list_width + 1, state.software or "Release", width - list_width - 2, curses.A_BOLD)
        list_height = max(1, height - 8)
        keep_visible(state, list_height)
        for offset in range(list_height):
            index = state.scroll + offset
            if index >= len(state.releases):
                break
            row = state.releases[index]
            attr = curses.A_REVERSE if index == state.index else curses.A_NORMAL
            screen.addnstr(LIST_TOP + offset, 0, row.label.ljust(list_width), list_width, attr)
        notes = notes_for(state).splitlines()
        max_notes = max(0, len(notes) - list_height)
        state.notes_scroll = max(0, min(state.notes_scroll, max_notes))
        for offset, line in enumerate(notes[state.notes_scroll:state.notes_scroll + list_height]):
            screen.addnstr(LIST_TOP + offset, list_width + 1, line[: width - list_width - 2], width - list_width - 2)
        actions = "Install   Install from a file"
        if state.software.casefold() == "rockbox":
            actions += "   Themes"
        screen.addnstr(height - 3, 0, actions, width - 1, curses.A_BOLD)
        screen.addnstr(height - 2, 0, "Settings   Support Us   Language   Credits / Thanks", width - 1)
        help_line = "↑↓ or click a row   wheel scrolls notes   i install   f file   h themes   q quit"
        screen.addnstr(height - 1, 0, (state.status or help_line)[: width - 1], width - 1, curses.A_DIM)
        screen.refresh()
        return list_height, list_width

    def body(screen) -> int:
        curses.curs_set(0)
        screen.keypad(True)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass
        while True:
            if state.page != "software":
                _curses_page(screen, state, items)
                continue
            list_height, _list_width = draw(screen)
            key = screen.getch()
            if key == curses.KEY_MOUSE:
                try:
                    _id, x, y, _z, bstate = curses.getmouse()
                except curses.error:
                    continue
                wheel_up = getattr(curses, "BUTTON4_PRESSED", 0)
                wheel_down = getattr(curses, "BUTTON5_PRESSED", 0)
                if bstate & (wheel_up | wheel_down):
                    step = -3 if bstate & wheel_up else 3
                    if x > list_width:
                        state.notes_scroll = max(0, state.notes_scroll + step)
                    elif state.releases:
                        state.index = max(0, min(len(state.releases) - 1, state.index + step))
                    continue
                clicked = getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)
                if bstate and clicked and not (bstate & clicked):
                    continue
                index = release_index_at(y, scroll=state.scroll, count=len(state.releases), list_height=list_height)
                row = activate(state.releases, index)
                if row is not None:
                    state.index = index
                    continue
                height, _width = screen.getmaxyx()
                if y == height - 3:
                    state.status = _run_install(state, output) if x < 16 else (
                        _run_themes(state) if state.software.casefold() == "rockbox" and x > 40 else _ask_file(screen)
                    )
                elif y == height - 2:
                    if x < 12:
                        state.page = "settings"
                    elif x < 26:
                        state.page = "support"
                    elif x < 40:
                        state.page = "language"
                    else:
                        state.page = "credits"
                elif y == 1:
                    _click_filter(state, items, x)
                continue
            if key in (ord("q"), 27):
                return 0
            if key in (curses.KEY_UP, ord("k")):
                state.index = move_index(state.index, "up", len(state.releases))
            elif key in (curses.KEY_DOWN, ord("j")):
                state.index = move_index(state.index, "down", len(state.releases))
            elif key in (ord("i"),):
                state.status = _run_install(state, output)
            elif key in (ord("f"),):
                state.status = _ask_file(screen)
            elif key in (ord("h"),):
                state.status = _run_themes(state)
            elif key in (ord("r"),):
                state.status = _load_rows(state, items, True)
            elif key in (ord("g"),):
                state.page = "settings"
            elif key in (ord("u"),):
                state.page = "support"
            elif key in (ord("l"),):
                state.page = "language"
            elif key in (ord("c"),):
                state.page = "credits"
            elif key in (ord("b"),):
                state.brand = _cycle_brand(state)
                fill_choices(state, items)
                state.status = _load_rows(state, items, False)
            elif key in (ord("m"),):
                state.model = _cycle_model(state)
                fill_choices(state, items)
                state.status = _load_rows(state, items, False)
            elif key in (ord("t"),) and state.types:
                state.hardware_type = "B" if state.hardware_type == "A" else "A"
                state.status = _load_rows(state, items, False)
            elif key in (ord("s"),):
                state.software = _cycle_software(state)
                fill_choices(state, items)
                state.status = _load_rows(state, items, False)
            elif ord("1") <= key <= ord("9"):
                index = key - ord("1")
                row = activate(state.releases, index)
                if row is not None:
                    state.index = index

    return curses.wrapper(body)


def _click_filter(state: BrowseState, items: list, x: int) -> None:
    if x < 22:
        state.brand = _cycle_brand(state)
    elif x < 38:
        state.model = _cycle_model(state)
    elif x < 50 and state.types:
        state.hardware_type = "B" if state.hardware_type == "A" else "A"
    elif x < 78:
        state.software = _cycle_software(state)
    fill_choices(state, items)
    state.status = _load_rows(state, items, x >= 78)


def _ask_file(screen) -> str:
    import curses
    curses.echo()
    curses.curs_set(1)
    height, width = screen.getmaxyx()
    screen.addnstr(height - 1, 0, "File or folder: ".ljust(width - 1), width - 1)
    screen.refresh()
    try:
        raw = screen.getstr(height - 1, 16, max(1, width - 18))
    except curses.error:
        raw = b""
    curses.noecho()
    curses.curs_set(0)
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return "Install from a file was cancelled."
    return _run_file(text)


def _curses_page(screen, state: BrowseState, items: list) -> None:
    import curses
    screen.erase()
    if state.page == "settings":
        lines = settings_lines(state)
    elif state.page == "support":
        lines = support_lines()
    elif state.page == "language":
        lines = language_lines(state)
    else:
        lines = credits_lines(_version())
    height, width = screen.getmaxyx()
    max_scroll = max(0, len(lines) - (height - 1))
    state.page_scroll = max(0, min(state.page_scroll, max_scroll))
    for index, line in enumerate(lines[state.page_scroll:state.page_scroll + height - 1]):
        screen.addnstr(index, 0, line[: width - 1], width - 1)
    if max_scroll:
        screen.addnstr(height - 1, 0, f"wheel or ↑↓ scrolls   q back   ({state.page_scroll + 1}/{max_scroll + 1})"[: width - 1], width - 1, curses.A_DIM)
    screen.refresh()
    key = screen.getch()
    if key == curses.KEY_MOUSE:
        try:
            _id, _x, _y, _z, bstate = curses.getmouse()
        except curses.error:
            bstate = 0
        up = getattr(curses, "BUTTON4_PRESSED", 0)
        down = getattr(curses, "BUTTON5_PRESSED", 0)
        if bstate & (up | down):
            state.page_scroll = max(0, min(max_scroll, state.page_scroll + (-3 if bstate & up else 3)))
        return
    if state.page == "settings" and key in (ord("1"), ord("2"), ord("3"), ord("4")):
        toggle_setting(state, {ord("1"): "old", ord("2"): "240p", ord("3"): "nightly", ord("4"): "keep_data"}[key])
        state.status = _load_rows(state, items, False)
        return
    if state.page == "language" and ord("1") <= key <= ord("7"):
        index = key - ord("1")
        if index < len(LANGUAGES):
            state.language = LANGUAGES[index]
            save_filters(state)
    state.page = "software"


def run(refresh: bool = False, simulate: bool = False) -> int:
    if simulate or os.environ.get("INNIOASIS_SIMULATE") == "1":
        os.environ["INNIOASIS_SIMULATE"] = "1"
    if _headless():
        print(failover_text("no-display"), file=sys.stderr)
    state, items, _error = _prepare(refresh)
    _maybe_prefetch()
    output = Path.cwd() / "firmware"
    try:
        output.mkdir(parents=True, exist_ok=True)
    except OSError:
        output = Path.cwd()
    kind = interface_kind(
        stdin_tty=sys.stdin.isatty(),
        stdout_tty=sys.stdout.isatty(),
        term=os.environ.get("TERM") or "",
        curses_ok=_curses_ok() and sys.platform != "win32" or _curses_ok(),
    )
    # Windows ships no curses. A third-party curses module may exist; use it
    # when it imports, and the numbered UI when it does not.
    if sys.platform == "win32" and not _curses_ok():
        kind = "plain"
    try:
        if kind == "plain":
            return _plain(state, items, refresh, output)
        return _curses_run(state, items, output)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        if exc.__class__.__module__.startswith("curses") or isinstance(exc, ImportError):
            print(failover_text("curses"), file=sys.stderr)
            return _plain(state, items, refresh, output)
        print(failover_text("download-failure", str(exc)), file=sys.stderr)
        return 1
