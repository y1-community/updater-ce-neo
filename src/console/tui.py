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
import webbrowser
import zipfile
from pathlib import Path

from . import catalog, components, download as dl, flash, wsl
from .releases import fetch_releases, rows_from_releases
from .session import (
    LIST_TOP,
    BrowseState,
    activate,
    apply_saved_filters,
    check_app_update,
    credits_lines,
    current_package,
    failover_text,
    fetch_notices,
    fill_choices,
    interface_kind,
    keep_data_offered,
    keep_visible,
    tracked_install,
    package_links,
    language_lines,
    language_rows,
    remember_language,
    menu_lines,
    move_index,
    notices_lines,
    notes_for,
    parse_mouse_report,
    release_index_at,
    save_filters,
    screen_lines,
    settings_lines,
    support_lines,
    toggle_setting,
    update_lines,
)
from .simulate import install_progress, rockbox_themes

TITLE_VERSION = "2.5.5"

# The same payload the window installs. Keeping these URLs here means the
# text session does not import theme_pack.py, which imports PySide6 at module
# level and would make the lite install depend on Qt again.
THEME_PACK_URL = "https://github.com/rockbox-y1/themes/releases/download/for_the_gays_and_the_theys/themepack.zip"
FONTS_PACK_URL = (
    "https://raw.githubusercontent.com/y1-community/updater-ce-neo/main/"
    "assets/360p/fonts-pack-merge-into-themes-pack.zip"
)


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
    try:
        rules = catalog.blocklist_rules()
    except Exception:
        rules = ()
    state.releases = rows_from_releases(
        payload,
        model=state.model,
        hardware_type=state.hardware_type,
        repo=package.repo,
        system_software=package.name,
        show_original_pre_307=state.show_original_307,
        block_rules=rules,
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


def _install_line(state: BrowseState, line: str, report) -> None:
    """Feed one backend line through the window's progress parser."""
    percent, label = flash.parse_progress(line)
    if percent is not None and percent >= state.install_percent:
        state.install_percent = percent
    if label:
        state.install_label = label
    stripped = line.strip()
    if stripped:
        state.install_log = (state.install_log + [stripped])[-80:]
    if report:
        report(state.install_percent, state.install_label or "Installing firmware", stripped)


def _install_success(state: BrowseState) -> str:
    state.install_ok = True
    links = package_links(state.package)
    message = _tr("action_flash_complete")
    state.result_lines = [message]
    if links:
        state.result_lines += ["", "Open next:", *links]
        message += "  Next: " + "  ".join(links)
    state.result_lines += [
        "",
        "Support Us  https://innioasis.app/support_devs.html",
        "Press Enter or q to go back.",
    ]
    return message


def _install_failure(state: BrowseState, message: str) -> str:
    state.install_ok = False
    tail = [line for line in state.install_log[-8:] if line]
    state.result_lines = [line for line in message.splitlines() if line] or [message]
    if tail:
        state.result_lines += ["", "Last output:", *tail]
    state.result_lines += ["", "Press Enter or q to go back."]
    return message


def _install_first_pick_text(
    title: str, options: list[str], selected: int = 0
) -> int | None:
    """Numbered form of the Install Firmware First question."""
    print()
    print(title)
    for index, option in enumerate(options, 1):
        print(f"  {index}. {option}")
    try:
        raw = input(f"> [{selected + 1}] ").strip()
    except EOFError:
        return None
    if raw.lower() in {"q", "quit", "cancel"}:
        return None
    if not raw:
        return selected
    if raw.isdigit() and 1 <= int(raw) <= len(options):
        return int(raw) - 1
    return selected


def _install_first_confirm_text(title: str, detail: str = "") -> bool | None:
    print()
    print(title)
    if detail:
        print(detail)
    try:
        raw = input("> [Y/n]: ").strip().lower()
    except EOFError:
        return None
    if raw in {"q", "quit", "cancel"}:
        return None
    return raw not in {"n", "no"}


def _install_first_pick(
    screen, title: str, options: list[str], selected: int = 0
) -> int | None:
    """The same question as an arrow-key list on the curses screen."""
    import curses

    selected = max(0, min(int(selected), len(options) - 1))
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        _put(
            screen,
            0,
            0,
            f"Updater CE {_version()}    {_tr('install_first_title')}"[: width - 1],
            width - 1,
            curses.A_BOLD,
        )
        _put(screen, 2, 0, title[: width - 1], width - 1)
        for offset, option in enumerate(options):
            attr = curses.A_REVERSE | curses.A_BOLD if offset == selected else curses.A_NORMAL
            _put(screen, 4 + offset, 0, f"  {offset + 1}. {option}"[: width - 1], width - 1, attr)
        _put(
            screen,
            height - 1,
            0,
            "↑↓ choose   Enter select   1-9 pick   q cancel"[: width - 1],
            width - 1,
            curses.A_DIM,
        )
        screen.refresh()
        key = screen.getch()
        if key in (ord("q"), 27):
            return None
        if key in (curses.KEY_UP, ord("k")):
            selected = (selected - 1) % len(options)
        elif key in (curses.KEY_DOWN, ord("j")):
            selected = (selected + 1) % len(options)
        elif key in (ord("\n"), curses.KEY_ENTER, 10, 13):
            return selected
        elif ord("1") <= key <= ord("9") and key - ord("1") < len(options):
            return key - ord("1")


def _install_first_confirm(screen, title: str, detail: str = "") -> bool | None:
    """Yes / no / cancel on the curses screen."""
    import curses

    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        _put(
            screen,
            0,
            0,
            f"Updater CE {_version()}    {_tr('install_first_title')}"[: width - 1],
            width - 1,
            curses.A_BOLD,
        )
        lines = [title, "", *detail.splitlines()]
        for offset, line in enumerate(lines):
            _put(screen, 2 + offset, 0, line[: width - 1], width - 1)
        _put(
            screen,
            height - 1,
            0,
            "Enter / y continues   n says no   q cancels"[: width - 1],
            width - 1,
            curses.A_DIM,
        )
        screen.refresh()
        key = screen.getch()
        if key in (ord("q"), 27):
            return None
        if key in (ord("n"), ord("N")):
            return False
        if key in (ord("\n"), curses.KEY_ENTER, ord("y"), ord("Y"), 10, 13):
            return True


def _install_first_gate(
    state: BrowseState, items, pick, confirm, output: Path, report, ask_keep
) -> bool:
    """Ask the manifest's prerequisite question before downloading anything.

    Returns ``True`` when the requested install may continue as-is. ``False``
    means the answer needs the base firmware first (and it has now been
    installed), or the user cancelled, or the base itself failed. The same
    function serves the curses and numbered sessions, so both routes obey the
    catalogue and neither downloads the overlay before the check.
    """
    from .. import install_first

    if activate(state.releases, state.index) is None:
        return True
    try:
        requirement = install_first.requirement_for(
            state.package, state.model, packages=items or []
        )
    except Exception:
        return True
    if requirement is None:
        state.install_first_answer = ""
        state.install_first_keep = ""
        return True
    state.install_first_answer = ""
    state.install_first_keep = ""
    if pick is None or confirm is None:
        # A piped session cannot answer; flashing the overlay blind is what
        # the catalogue exists to prevent.
        print(_tr("install_first_needs_answer"), flush=True)
        return False
    model = state.model or "player"
    options = [state.package.name, requirement.label(), _tr("keep_data_choice_other")]
    stored = tracked_install(state.model, state.hardware_type) or {}
    stored_name = str(stored.get("software_name") or "").strip().casefold()
    wanted_name = requirement.software_name.strip().casefold()
    same_software = bool(stored_name) and (
        stored_name == wanted_name
        or stored_name in wanted_name
        or wanted_name in stored_name
    )
    selected = pick(
        _tr("keep_data_which_firmware_fmt").format(model=model),
        options,
        selected=1 if same_software else 0,
    )
    if selected is None:
        return False
    answer = (
        install_first.ANSWER_TARGET,
        install_first.ANSWER_PREREQUISITE,
        install_first.ANSWER_OTHER,
    )[selected]
    version = (
        str(stored.get("release_label") or stored.get("tag_name") or "")
        if same_software
        else ""
    )
    journey = install_first.InstallFirstJourney(
        target=state.package, target_rel={}, requirement=requirement
    )
    step = journey.choose(answer, version)
    if step == install_first.ASK_VERSION:
        version_text = (
            requirement.version_range.label()
            if requirement.version_range is not None
            else requirement.version_text
        )
        decided = confirm(
            _tr("install_first_version_fmt").format(
                model=model, software=requirement.software_name, version=version_text
            ),
            _tr("install_first_hint").format(
                software=requirement.label(),
                model=model,
                target=state.package.name,
            ),
        )
        if decided is None:
            return False
        step = journey.confirm_version(bool(decided))
    if step == install_first.PROCEED:
        state.install_first_answer = answer
        if answer == install_first.ANSWER_TARGET:
            state.install_first_keep = "allow"
        elif install_first.related_by_derivative(state.package, requirement, items or []):
            state.install_first_keep = ""
        else:
            state.install_first_keep = "block"
        return True
    if requirement.package is None:
        print(
            _tr("install_first_missing").format(software=requirement.software_name),
            flush=True,
        )
        return False
    target_keep = (
        ""
        if install_first.related_by_derivative(state.package, requirement, items or [])
        else "block"
    )
    decided = confirm(
        _tr("install_first_need_title").format(
            software=requirement.software_name, target=state.package.name
        ),
        _tr("install_first_need_body").format(
            software=requirement.label(), target=state.package.name, model=model
        ),
    )
    if not decided:
        return False
    try:
        payload = fetch_releases(requirement.package.repo)
        rules = catalog.blocklist_rules()
    except Exception as exc:
        print(failover_text("download-failure", str(exc)), flush=True)
        return False
    rows = rows_from_releases(
        payload,
        model=state.model,
        hardware_type=state.hardware_type,
        repo=requirement.package.repo,
        system_software=requirement.package.name,
        block_rules=rules,
    )
    usable = [
        row
        for row in rows
        if requirement.version_range is None or requirement.allows(row.version)
    ]
    if not usable:
        if rows:
            version = (
                requirement.version_range.label()
                if requirement.version_range is not None
                else requirement.version_text
            )
            message = _tr("install_first_version_unavailable").format(
                software=requirement.software_name, version=version
            )
        else:
            message = _tr("install_first_missing").format(
                software=requirement.software_name
            )
        print(message, flush=True)
        return False
    saved = (
        state.package,
        state.releases,
        state.index,
        state.software,
        state.install_first_answer,
        state.install_first_keep,
        state.keep_data,
    )
    state.package = requirement.package
    state.releases = usable
    state.index = 0
    state.software = requirement.package.name
    state.install_first_answer = ""
    # A base install never keeps the overlay's or the old system's data.
    state.install_first_keep = "block"
    state.keep_data = False
    try:
        _run_install(
            state,
            output,
            report=report,
            ask_keep=ask_keep,
            items=items,
            ask_first_pick=pick,
            ask_first_confirm=confirm,
            _skip_install_first=True,
        )
        base_ok = state.install_ok
    finally:
        (
            state.package,
            state.releases,
            state.index,
            state.software,
            state.install_first_answer,
            state.install_first_keep,
            state.keep_data,
        ) = saved
    # The answer belongs to the target, not to the temporary base run: restore
    # it so the outer install's Keep Data question follows the journey rules.
    state.install_first_answer = answer
    state.install_first_keep = target_keep
    if not base_ok:
        return False
    message = _tr("install_first_base_done").format(
        software=requirement.software_name, target=state.package.name
    )
    if report:
        state.status = message
    else:
        print(message, flush=True)
    return True


def _run_install(
    state: BrowseState,
    output: Path,
    report=None,
    ask_keep=None,
    items=None,
    ask_first_pick=None,
    ask_first_confirm=None,
    _skip_install_first: bool = False,
) -> str:
    state.install_ok = False
    if not _skip_install_first:
        if not _install_first_gate(
            state, items, ask_first_pick, ask_first_confirm, output, report, ask_keep
        ):
            return "Install cancelled."
    state.install_percent = 0.0
    state.install_label = "Preparing"
    state.install_log = []
    state.result_lines = []
    row = activate(state.releases, state.index)
    if row is None:
        return "Select a release first."
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        events = install_progress(sleep=0.04)
        for index, event in enumerate(events):
            if report:
                report(index * 100.0 / max(1, len(events)), event["message"], "")
            print(event["message"], flush=True)
        return _install_success(state) if events[-1]["ok"] else _install_failure(state, events[-1]["message"])
    if not row.asset_url:
        return failover_text("download-failure", "this release has no firmware zip.")
    target = output / row.asset_name

    def download_report(done: int, total: int | None) -> None:
        if not report:
            return
        percent = (done * 100.0 / total) if total else 0.0
        detail = f"{dl.human_bytes(done)} / {dl.human_bytes(total)}" if total else dl.human_bytes(done)
        report(percent, "Downloading firmware", detail)

    try:
        if not target.exists():
            if report:
                report(0.0, "Downloading firmware", row.asset_name)
            dl.download(row.asset_url, target, progress=download_report if report else _progress_printer(row.asset_name))
        folder = output / Path(row.asset_name).stem
        if report:
            report(0.0, "Unpacking firmware", target.name)
        dl.extract(target, folder)
    except (RuntimeError, OSError) as exc:
        return _install_failure(state, failover_text("download-failure", str(exc)))

    # The window only offers the tick when the catalogue allows it and the
    # package actually carries a preserve-data scatter. Ask at install time,
    # not only in Settings, exactly as the connect card does.
    allowed = keep_data_offered(
        state.package, state.allow_keep_data_any, state.model, state.hardware_type
    )
    if state.install_first_keep == "allow":
        allowed = True
    elif state.install_first_keep == "block":
        allowed = False
    offered = allowed and flash._preserve_scatter(folder) is not None
    if ask_keep:
        choice = ask_keep(offered, state.keep_data)
    else:
        choice = _ask_ready_text(state, offered, state.keep_data)
    if choice is None:
        return "Install cancelled."
    keep = bool(choice) if offered else False
    if not offered and state.keep_data:
        state.keep_data = False
        print(_tr("sel_keep_data_incompatible"), flush=True)
    model = (state.model or "player").strip() or "player"
    connect = _tr("flash_connect_device_prompt", model=model)
    state.install_label = connect
    if report:
        report(0.0, connect, "")

    system = {"linux": "linux", "darwin": "darwin", "win32": "windows"}.get(sys.platform, sys.platform)
    arch = components.platform_key().split("-", 1)[-1]
    if wsl.is_wsl():
        # The Linux SP Flash Tool is x86-64, and a forwarded USB device is
        # already a best-effort path. MTKclient is the engine with a chance of
        # surviving the BROM reset this way, so never auto-pick SP here.
        engine, reason = "mtk", "WSL2: using MTKclient over usbipd."
    else:
        engine, reason = flash.install_engine(system, arch, _headless())
    if engine != "sp-console":
        print(reason, flush=True)
    else:
        try:
            tool = components.ensure("spflash", progress=download_report if report else _progress_printer("SP Flash Tool"))
        except RuntimeError as exc:
            print(failover_text("missing-component", str(exc)), flush=True)
            print(reason or "Using MTKclient instead: mtk wl <folder>.", flush=True)
            tool = None
        else:
            scatter = dl.find_scatter(folder)
            da = tool.parent / "MTK_AllInOne_DA.bin"
            if scatter and da.is_file():
                args = flash.sp_flash_tool_console_args(str(scatter), str(da))
                code = flash._run(
                    [str(tool), *args],
                    on_line=lambda line: _install_line(state, line, report),
                )
                if code == 0:
                    if report:
                        report(100.0, _tr("action_flash_complete"), "")
                    return _install_success(state)
                return _install_failure(
                    state,
                    _tr("flash_failed") + "\n\n" + _tr("err_usb_disconnected_recovery", model=model),
                )
    if "rockbox" in state.software.casefold():
        try:
            components.ensure("adb", progress=download_report if report else _progress_printer("adb"))
        except RuntimeError as exc:
            print(failover_text("missing-component", str(exc)), flush=True)
    code = flash.flash_directory(
        folder,
        keep_data=keep,
        on_line=lambda line: _install_line(state, line, report),
    )
    if code == 0:
        if report:
            report(100.0, _tr("action_flash_complete"), "")
        return _install_success(state)
    if report:
        report(state.install_percent, _tr("flash_failed"), f"MTKclient exited {code}.")
    return _install_failure(
        state,
        _tr("flash_failed") + "\n\n" + _tr("err_usb_disconnected_recovery", model=model),
    )


def _draw_progress(screen, state: BrowseState, percent: float, label: str, detail: str) -> None:
    """The window's install card, as text: stage, bar, byte detail, log tail."""
    import curses

    height, width = screen.getmaxyx()
    screen.erase()
    row = activate(state.releases, state.index)
    title = row.label if row is not None else state.software or "Firmware"
    _put(screen, 0, 0, f"Updater CE {_version()}    Installing", width - 1, curses.A_BOLD)
    _put(screen, 1, 0, title[: width - 1], width - 1)
    bar_width = max(8, width - 12)
    filled = int(bar_width * max(0.0, min(100.0, percent)) / 100.0)
    _put(screen, 3, 0, "[" + "#" * filled + "-" * (bar_width - filled) + f"] {percent:3.0f}%", width - 1)
    _put(screen, 4, 0, label[: width - 1], width - 1, curses.A_BOLD)
    _put(screen, 5, 0, (detail or "")[: width - 1], width - 1, curses.A_DIM)
    for offset, line in enumerate(state.install_log[-6:]):
        _put(screen, 7 + offset, 0, line[: width - 1], width - 1, curses.A_DIM)
    _put(screen, height - 2, 0, "Keep the player connected. Ctrl+C stops the session.", width - 1, curses.A_DIM)
    screen.refresh()


def _tr(key: str, **fields) -> str:
    """The same sentence the window shows for this key, in the saved language."""
    try:
        from ..i18n import tr, translator
        from .session import shared_language

        code = shared_language()
        if code:
            translator().set_language(code)
        text = tr(key)
    except Exception:
        text = key
    if not fields:
        return text
    try:
        return text.format(**fields)
    except (KeyError, IndexError, ValueError):
        return text


def _install_card_lines(state: BrowseState, offered: bool, keep: bool) -> list[str]:
    """The window's power-off card: disconnect, the paperclip tip, Keep my settings."""
    model = (state.model or "player").strip() or "player"
    lines = [
        _tr("flash_connect_prompt", model=model),
        "",
        _tr("flash_connect_paperclip_prompt"),
    ]
    if offered:
        mark = "x" if keep else " "
        lines += [
            "",
            f"[{mark}]  {_tr('flash_keep_data_option')}",
            _tr("flash_keep_data_tooltip"),
        ]
    lines += ["", _tr("btn_next"), "q  Cancel"]
    return lines


def _card_action_at(lines: list[str], index: int) -> str | None:
    """The card control on one text row, or ``None`` for a plain row."""
    if index < 0 or index >= len(lines):
        return None
    text = lines[index].strip()
    if not text:
        return None
    if text == _tr("btn_next"):
        return "next"
    if text.casefold().startswith("q") and "cancel" in text.casefold():
        return "cancel"
    if text.startswith("[") and _tr("flash_keep_data_option") in text:
        return "keep"
    return None


def _ask_ready_text(state: BrowseState, offered: bool, current: bool) -> bool | None:
    """Numbered form of the power-off card. Enter continues, k toggles, q cancels."""
    keep = bool(current) if offered else False
    while True:
        print("\n".join(_install_card_lines(state, offered, keep)), flush=True)
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            return None
        if answer in {"q", "quit", "cancel"}:
            return None
        if offered and answer in {"k", "1", "y", "yes"}:
            keep = not keep
            continue
        if answer in {"", "next", "enter"}:
            return keep
    return keep


def _ask_keep_data(screen, state: BrowseState, current: bool, offered: bool = True) -> bool | None:
    """The window's power-off card. Enter or a click continues; k or a click toggles Keep my settings."""
    import curses

    keep = bool(current) if offered else False
    hover: str | None = None
    click = getattr(curses, "BUTTON1_CLICKED", 0) | getattr(curses, "BUTTON1_PRESSED", 0)
    while True:
        height, width = screen.getmaxyx()
        screen.erase()
        title = f"Updater CE {_version()}    {_tr('flash_guide_title')}"
        _put(screen, 0, 0, title[: width - 1], width - 1, curses.A_BOLD)
        _put(screen, 1, 0, (state.software or "Firmware")[: width - 1], width - 1)
        lines = _install_card_lines(state, offered, keep)
        visible = min(len(lines), max(0, height - 4))
        for offset, line in enumerate(lines[:visible]):
            action = _card_action_at(lines, offset)
            attr = curses.A_REVERSE | curses.A_BOLD if action == hover else curses.A_NORMAL
            _put(screen, 3 + offset, 0, line[: width - 1], width - 1, attr)
        hint = "Click or Enter continues   k toggles Keep my settings   q cancels" if offered else "Click or Enter continues   q cancels"
        _put(screen, height - 1, 0, hint[: width - 1], width - 1, curses.A_DIM)
        screen.refresh()
        key = screen.getch()
        if key == curses.KEY_MOUSE:
            try:
                _id, _x, y, _z, bstate = curses.getmouse()
            except curses.error:
                continue
            action = _card_action_at(lines, y - 3) if 3 <= y < 3 + visible else None
            if bstate & click:
                if action == "next":
                    return keep
                if action == "cancel":
                    return None
                if action == "keep":
                    keep = not keep
            hover = action
            continue
        if key in (ord("q"), 27):
            return None
        if offered and key in (ord("k"), ord("1"), ord("y"), ord("Y")):
            keep = not keep
            hover = None
            continue
        if key in (ord("\n"), curses.KEY_ENTER, 10, 13):
            return keep


def _merge_theme_zip(archive: Path, target: Path, progress=None) -> int:
    """Merge a theme/font pack into `.rockbox` using only the stdlib.

    Archives in the wild wrap entries in `.rockbox/` or `themes-themepack/`.
    Those wrappers are stripped so the files land where Rockbox reads them.
    """
    written = 0
    with zipfile.ZipFile(archive) as bundle:
        members = [info for info in bundle.infolist() if not info.is_dir()]
        for index, info in enumerate(members):
            parts = [part for part in info.filename.replace("\\", "/").split("/") if part not in ("", ".")]
            while parts and parts[0] in (".rockbox", "themes-themepack"):
                parts = parts[1:]
            if not parts or any(part == ".." for part in parts):
                continue
            destination = target.joinpath(*parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as source, destination.open("wb") as sink:
                while True:
                    block = source.read(1 << 20)
                    if not block:
                        break
                    sink.write(block)
            written += 1
            if progress and index % 25 == 0:
                progress(index, len(members))
    if progress:
        progress(len(members), len(members))
    return written


def _run_themes(state: BrowseState, screen=None, report=None, ask_path=None) -> str:
    if state.software.casefold() != "rockbox":
        return "Themes are part of a Rockbox install."
    if os.environ.get("INNIOASIS_SIMULATE") == "1":
        events = rockbox_themes(sleep=0.04)
        for event in events:
            print(event["message"], flush=True)
        return events[-1]["message"]

    if ask_path is None:
        if screen is not None:
            ask_path = lambda: _ask_line(
                screen, "Player folder (USB storage mode)", flash.mount_point(Path("/"))
            )
        else:
            ask_path = lambda: input("Player folder (USB storage mode): ").strip()
    folder_text = ask_path()
    if not folder_text:
        return "Themes were cancelled."
    folder = Path(folder_text).expanduser()
    if not folder.is_dir():
        return f"No such player folder: {folder}"
    target = folder if folder.name == ".rockbox" or (folder / ".rockbox").is_dir() else folder / ".rockbox"
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Could not open {target}: {exc}"

    cache = Path.home() / ".cache" / "innioasis-updater"
    sources = (
        (THEME_PACK_URL, cache / "rockbox_360p_themepack.zip", "theme pack"),
        (FONTS_PACK_URL, cache / "rockbox_360p_fonts.zip", "fonts"),
    )
    installed = 0
    errors: list[str] = []
    for url, archive, label in sources:
        try:
            def download_progress(done, total, _label=label):
                if report:
                    percent = (done * 100.0 / total) if total else 0.0
                    report(percent, f"Downloading Rockbox {_label}", dl.human_bytes(done))
            dl.download(url, archive, progress=download_progress if report else None)

            def merge_progress(done, total):
                if report:
                    report(100.0 * done / max(1, total), "Installing Rockbox themes & fonts", target.name)
            installed += _merge_theme_zip(archive, target, merge_progress if report else None)
        except (RuntimeError, OSError, zipfile.BadZipFile) as exc:
            errors.append(f"{label}: {exc}")
    if not installed:
        return "Theme install failed. " + "; ".join(errors)
    message = f"Installed {installed} theme/font files into {target}."
    if errors:
        message += " Some sources failed: " + "; ".join(errors)
    return message


def _local_install_first_gate(folder, pick, confirm, ready=None):
    """Ask the prerequisite question for a locally browsed package.

    Returns ``None`` when the folder is not an install-first package, a string
    when the install must stop, and ``True`` when the target may be flashed.
    The local ``pre_stage`` package is flashed directly; if the packager did
    not include it, the terminal reports the missing prerequisite and the GUI
    remains the route for the catalogue download fallback.
    """
    from .. import install_first

    try:
        from ..fw_package import locate_package

        pkg = locate_package(folder, require_ini=False)
    except Exception:
        return None
    if pkg is None or not str(pkg.install_first or "").strip():
        return None
    try:
        requirement = install_first.requirement_for(pkg, pkg.model, packages=[])
    except Exception:
        return None
    if requirement is None:
        return None
    if pick is None or confirm is None:
        return _tr("install_first_needs_answer")
    model = pkg.model or "player"
    target_name = pkg.name or pkg.folder.name
    base = pkg.install_first_package
    selected = 1 if base is not None else 0
    choice = pick(
        _tr("keep_data_which_firmware_fmt").format(model=model),
        [target_name, requirement.label(), _tr("keep_data_choice_other")],
        selected=selected,
    )
    if choice is None:
        return "Install cancelled."
    answer = (
        install_first.ANSWER_TARGET,
        install_first.ANSWER_PREREQUISITE,
        install_first.ANSWER_OTHER,
    )[choice]
    journey = install_first.InstallFirstJourney(
        target=pkg, requirement=requirement, local_prerequisite=base
    )
    reported = base.version if answer == install_first.ANSWER_PREREQUISITE and base else ""
    step = journey.choose(answer, reported)
    if step == install_first.ASK_VERSION:
        version = (
            requirement.version_range.label()
            if requirement.version_range is not None
            else requirement.version_text
        )
        decided = confirm(
            _tr("install_first_version_fmt").format(
                model=model, software=requirement.software_name, version=version
            ),
            _tr("install_first_hint").format(
                software=requirement.label(), model=model, target=target_name
            ),
        )
        if decided is None:
            return "Install cancelled."
        step = journey.confirm_version(bool(decided))
    if step == install_first.PROCEED:
        return True
    if base is None:
        return _tr("install_first_missing").format(software=requirement.software_name)
    if ready is not None:
        proceed = ready(False, False)
        if proceed is None:
            return "Install cancelled."
    print(
        _tr("install_first_starting").format(
            software=requirement.software_name, target=target_name
        ),
        flush=True,
    )
    code = flash.flash_directory(str(base.folder), keep_data=False)
    if code != 0:
        return _tr("flash_failed") + "\n\n" + _tr(
            "err_usb_disconnected_recovery", model=model
        )
    print(
        _tr("install_first_base_done").format(
            software=requirement.software_name, target=target_name
        ),
        flush=True,
    )
    return True


def _run_file(
    path_text: str, ask_keep=None, ask_first_pick=None, ask_first_confirm=None
) -> str:
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

    # A portable install-first package carries its target metadata and, when
    # the packager could include it, the prerequisite in its own folder. Ask
    # the same question as the catalogue before touching the player, and
    # flash the local prerequisite first when that is the answer.
    gate = _local_install_first_gate(
        folder, ask_first_pick, ask_first_confirm, ready=ask_keep
    )
    if isinstance(gate, str):
        return gate
    try:
        from ..fw_package import locate_package

        package = locate_package(folder, require_ini=False)
    except Exception:
        package = None
    if package is not None and str(package.install_first or "").strip():
        folder = str(package.folder)

    # A local package gets the same preserve-data question as a catalogue
    # package. Asking only when the package actually carries the scatter is
    # what prevents a clean-write guess from erasing a player's storage.
    offered = flash._preserve_scatter(folder) is not None
    file_state = BrowseState(model="player", software="Firmware")
    if ask_keep:
        choice = ask_keep(offered, False)
    else:
        choice = _ask_ready_text(file_state, offered, False)
    if choice is None:
        return "Install cancelled."
    keep = bool(choice) if offered else False
    code = flash.flash_directory(folder, keep_data=keep)
    if code == 0:
        return _tr("action_flash_complete")
    return _tr("flash_failed") + "\n\n" + _tr("err_usb_disconnected_recovery", model="player")


def _plain(state: BrowseState, items: list, refresh: bool, output: Path) -> int:
    def ask_keep(offered: bool, current: bool) -> bool | None:
        return _ask_ready_text(state, offered, current)

    print(failover_text("piped") if not sys.stdin.isatty() or not sys.stdout.isatty() else "", end="")
    if (os.environ.get("TERM") or "").lower() == "dumb":
        print(failover_text("term-dumb"))
    if not _curses_ok():
        print(failover_text("curses"))
    while True:
        if state.quit:
            return 0
        if state.page == "software":
            print("\n".join(screen_lines(state, _version())))
            print()
            print(notes_for(state))
            print()
            print("Number selects a row. i install, f file, h themes, n notices, v update, r refresh,")
            print("b brand, m model, t type, s software, g settings, u support, l language, c credits, ? menu, q quit")
        elif state.page == "settings":
            print("\n".join(settings_lines(state)))
        elif state.page == "support":
            print("\n".join(support_lines(state.language)))
        elif state.page == "language":
            print("\n".join(language_lines(state)))
        elif state.page == "credits":
            print("\n".join(credits_lines(_version())))
        elif state.page == "menu":
            print("\n".join(menu_lines(state)))
        elif state.page == "notices":
            print("\n".join(notices_lines(state)))
        elif state.page == "update":
            print("\n".join(update_lines(state.update_result, state.update_downloaded)))
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
        if state.page == "menu" and choice.isdigit():
            _menu_choice(state, int(choice) - 1, items)
            continue
        if state.page == "update" and lowered in {"d", "download"}:
            result = state.update_result or {}
            if result.get("download"):
                target = Path.home() / "Downloads" / Path(str(result.get("asset") or "update")).name
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dl.download(str(result["download"]), target, progress=_progress_printer(target.name))
                except (RuntimeError, OSError) as exc:
                    state.update_downloaded = f"Download failed: {exc}"
                else:
                    state.update_downloaded = f"Downloaded to {target}"
            else:
                state.update_downloaded = "There is no newer package to download."
            continue
        if state.page in ("notices", "update") and lowered in {"r", "refresh"}:
            if state.page == "notices":
                _open_notices(state)
            else:
                _open_update(state)
            continue
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
                toggle_setting(state, "original")
                state.status = _load_rows(state, items, False)
            elif choice == "5":
                toggle_setting(state, "keep_data")
                state.status = "Keep my data: " + ("on" if state.keep_data else "off")
            else:
                state.page = "software"
            continue
        if state.page == "language":
            rows = language_rows()
            if choice.isdigit():
                index = int(choice) - 1
                if 0 <= index < len(rows):
                    _choose_language(state, rows[index][0])
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
            state.status = _run_install(
                state,
                output,
                ask_keep=ask_keep,
                items=items,
                ask_first_pick=_install_first_pick_text,
                ask_first_confirm=_install_first_confirm_text,
            )
            print(state.status)
            continue
        if lowered in {"f", "file"}:
            try:
                path_text = input("File or folder: ").strip()
            except EOFError:
                return 0
            state.status = _run_file(
                path_text,
                ask_keep=ask_keep,
                ask_first_pick=_install_first_pick_text,
                ask_first_confirm=_install_first_confirm_text,
            )
            print(state.status)
            continue
        if lowered in {"h", "themes"}:
            state.status = _run_themes(state)
            print(state.status)
            continue
        if lowered in {"n", "notices"}:
            _open_notices(state)
            continue
        if lowered in {"v", "update"}:
            _open_update(state)
            continue
        if lowered in {"?", "menu"}:
            state.page = "menu"
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


def _put(screen, y: int, x: int, text, limit: int, attr: int = 0) -> None:
    """Write one terminal line without letting a resize or a wide glyph abort.

    curses raises ``addnwstr() returned ERR`` when a write reaches the last
    cell, when the terminal is resized between ``getmaxyx`` and the write, or
    when a translated line contains a control character. A flash card must
    never be replaced by a shell prompt because of a cosmetic write failure.
    """
    import curses

    try:
        height, width = screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= width or limit <= 0:
            return
        value = str(text or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
        available = min(int(limit), max(0, width - x - 1))
        if available <= 0:
            return
        screen.addnstr(y, x, value, available, attr)
    except curses.error:
        # A cosmetic line is never worth losing the session or the flash.
        pass


def _curses_run(state: BrowseState, items: list, output: Path) -> int:
    import curses

    def draw(screen) -> None:
        height, width = screen.getmaxyx()
        screen.erase()
        version = _version()
        _put(screen, 0, 0, f"Updater CE {version}    Select Software", width - 1, curses.A_BOLD)
        type_label = state.hardware_type or "—"
        filters = _filter_text(state)
        _put(screen, 1, 0, filters, width - 1)
        list_width = max(24, min(36, width // 3))
        _put(screen, 3, 0, "Available System Software", list_width - 1, curses.A_BOLD)
        _put(screen, 3, list_width + 1, state.software or "Release", width - list_width - 2, curses.A_BOLD)
        list_height = max(1, height - 8)
        keep_visible(state, list_height)
        for offset in range(list_height):
            index = state.scroll + offset
            if index >= len(state.releases):
                break
            row = state.releases[index]
            attr = curses.A_REVERSE if index == state.index else curses.A_NORMAL
            _put(screen, LIST_TOP + offset, 0, row.label.ljust(list_width), list_width, attr)
        notes = notes_for(state).splitlines()
        max_notes = max(0, len(notes) - list_height)
        state.notes_scroll = max(0, min(state.notes_scroll, max_notes))
        for offset, line in enumerate(notes[state.notes_scroll:state.notes_scroll + list_height]):
            _put(screen, LIST_TOP + offset, list_width + 1, line[: width - list_width - 2], width - list_width - 2)
        _put(screen, height - 3, 0, _action_text(state), width - 1, curses.A_BOLD)
        _put(screen, height - 2, 0, _secondary_text(), width - 1)
        help_line = "↑↓ row   i install   f file   n notices   v update   ? menu   q quit"
        _put(screen, height - 1, 0, (state.status or help_line)[: width - 1], width - 1, curses.A_DIM)
        screen.refresh()
        return list_height, list_width

    def body(screen) -> int:
        curses.curs_set(0)
        screen.keypad(True)
        try:
            curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        except curses.error:
            pass

        def report(percent, label, detail):
            _draw_progress(screen, state, percent, label, detail)

        def ask_keep(offered, current):
            return _ask_keep_data(screen, state, current, offered=offered)

        def ask_first_pick(title, options, selected=0):
            return _install_first_pick(screen, title, options, selected=selected)

        def ask_first_confirm(title, detail=""):
            return _install_first_confirm(screen, title, detail)

        while True:
            if state.quit:
                return 0
            if state.page != "software":
                _curses_page(screen, state, items)
                continue
            list_height, list_width = draw(screen)
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
                    which = _segment_at(_action_text(state), x, ["Install", "Install from a file", "Themes"])
                    if which == 0:
                        state.status = _run_install(
                            state,
                            output,
                            report=report,
                            ask_keep=ask_keep,
                            items=items,
                            ask_first_pick=ask_first_pick,
                            ask_first_confirm=ask_first_confirm,
                        )
                        if state.result_lines:
                            state.page = "done"
                            state.page_scroll = 0
                    elif which == 2 and state.software.casefold() == "rockbox":
                        state.status = _run_themes(state, screen=screen, report=report)
                    elif which == 1:
                        state.status = _ask_file(screen, state)
                elif y == height - 2:
                    which = _segment_at(
                        _secondary_text(), x,
                        ["Settings", "Support Us", "Language", "Notices", "Updates", "Credits", "Menu"],
                    )
                    if which == 0:
                        state.page = "settings"
                    elif which == 1:
                        state.page = "support"
                    elif which == 2:
                        state.page = "language"
                    elif which == 3:
                        _open_notices(state)
                    elif which == 4:
                        _open_update(state)
                    elif which == 5:
                        state.page = "credits"
                    elif which == 6:
                        state.page = "menu"
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
                state.status = _run_install(
                    state,
                    output,
                    report=report,
                    ask_keep=ask_keep,
                    items=items,
                    ask_first_pick=ask_first_pick,
                    ask_first_confirm=ask_first_confirm,
                )
                if state.result_lines:
                    state.page = "done"
                    state.page_scroll = 0
            elif key in (ord("f"),):
                state.status = _ask_file(screen, state)
            elif key in (ord("h"),):
                state.status = _run_themes(state, screen=screen, report=report)
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
            elif key in (ord("?"), curses.KEY_F1):
                state.page = "menu"
            elif key in (ord("n"),):
                _open_notices(state)
            elif key in (ord("v"),):
                _open_update(state)
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


def _filter_text(state: BrowseState) -> str:
    return (
        f"[ Brand: {state.brand} ]  [ Model: {state.model} ]  "
        f"[ Type: {state.hardware_type or '—'} ]  [ Software: {state.software or '—'} ]  [ Refresh ]"
    )


def _action_text(state: BrowseState) -> str:
    actions = "[ Install ]  [ Install from a file ]"
    if state.software.casefold() == "rockbox":
        actions += "  [ Themes ]"
    return actions


def _secondary_text() -> str:
    return "[ Settings ] [ Support Us ] [ Language ] [ Notices ] [ Updates ] [ Credits ] [ Menu ]"


def _segment_at(text: str, x: int, labels: list[str]) -> int | None:
    starts = [text.find(f"[ {label}") for label in labels]
    for index, start in enumerate(starts):
        if start < 0:
            continue
        end = starts[index + 1] if index + 1 < len(starts) and starts[index + 1] > start else len(text)
        if start <= x < end:
            return index
    return None


def _open_notices(state: BrowseState) -> None:
    state.notices_status = "Fetching notices…"
    try:
        state.notices = fetch_notices(model=state.model)
    except RuntimeError as exc:
        state.notices = []
        state.notices_status = str(exc)
    else:
        state.notices_status = "" if state.notices else "There are no active notices right now."
    state.page = "notices"
    state.page_scroll = 0


def _download_update(screen, state: BrowseState) -> None:
    """Download the offered package with the live progress screen."""
    result = state.update_result or {}
    url = str(result.get("download") or "")
    if not result.get("newer") or not url:
        state.update_downloaded = "There is no newer package to download."
        return
    name = Path(str(result.get("asset") or url.split("?", 1)[0])).name or "UpdaterCE-update"
    downloads = Path.home() / "Downloads"
    try:
        downloads.mkdir(parents=True, exist_ok=True)
    except OSError:
        downloads = Path.cwd()
    target = downloads / name

    def report(done, total):
        percent = (done * 100.0 / total) if total else 0.0
        detail = f"{dl.human_bytes(done)} / {dl.human_bytes(total)}" if total else dl.human_bytes(done)
        _draw_progress(screen, state, percent, "Downloading app update", detail)

    try:
        report(0, None)
        dl.download(url, target, progress=report)
    except (RuntimeError, OSError) as exc:
        state.update_downloaded = f"Download failed: {exc}"
    else:
        state.update_downloaded = f"Downloaded to {target}"


def _open_update(state: BrowseState) -> None:
    state.update_downloaded = ""
    try:
        state.update_result = check_app_update()
    except Exception as exc:  # the page must open even when the feed is odd
        state.update_result = {
            "current": _version(),
            "latest": "",
            "newer": False,
            "failed": True,
            "error": str(exc),
        }
    state.page = "update"
    state.page_scroll = 0


def _menu_choice(state: BrowseState, index: int, items: list) -> None:
    if index == 0:
        state.page = "software"
    elif index == 1:
        _open_notices(state)
    elif index == 2:
        _open_update(state)
    elif index == 3:
        state.page = "settings"
    elif index == 4:
        state.page = "support"
    elif index == 5:
        state.page = "language"
    elif index == 6:
        state.page = "credits"
    elif index == 7:
        state.quit = True


def _click_filter(state: BrowseState, items: list, x: int) -> None:
    which = _segment_at(_filter_text(state), x, ["Brand", "Model", "Type", "Software", "Refresh"])
    if which == 0:
        state.brand = _cycle_brand(state)
    elif which == 1:
        state.model = _cycle_model(state)
    elif which == 2 and state.types:
        state.hardware_type = "B" if state.hardware_type == "A" else "A"
    elif which == 3:
        state.software = _cycle_software(state)
    elif which != 4:
        return
    fill_choices(state, items)
    state.status = _load_rows(state, items, which == 4)


def _ask_line(screen, label: str, default: str = "") -> str:
    """One line of input at the bottom of a curses page."""
    import curses
    curses.echo()
    curses.curs_set(1)
    height, width = screen.getmaxyx()
    prompt = f"{label} [{default}]: " if default else f"{label}: "
    _put(screen, height - 1, 0, prompt.ljust(width - 1), width - 1)
    screen.refresh()
    try:
        raw = screen.getstr(height - 1, len(prompt), max(1, width - len(prompt) - 1))
    except curses.error:
        raw = b""
    curses.noecho()
    curses.curs_set(0)
    text = raw.decode("utf-8", "replace").strip()
    return text or default


def _ask_file(screen, state: BrowseState | None = None) -> str:
    text = _ask_line(screen, "File or folder")
    if not text:
        return "Install from a file was cancelled."
    ask_keep = None
    if state is not None:
        ask_keep = lambda offered, current: _ask_keep_data(screen, state, current, offered=offered)
    return _run_file(
        text,
        ask_keep=ask_keep,
        ask_first_pick=lambda title, options, selected=0: _install_first_pick(
            screen, title, options, selected
        ),
        ask_first_confirm=lambda title, detail="": _install_first_confirm(
            screen, title, detail
        ),
    )


def _curses_page(screen, state: BrowseState, items: list) -> None:
    import curses
    screen.erase()
    if state.page == "done":
        lines = state.result_lines or ["Nothing has been installed in this session."]
    elif state.page == "menu":
        lines = menu_lines(state)
    elif state.page == "notices":
        lines = notices_lines(state)
    elif state.page == "update":
        lines = update_lines(state.update_result, state.update_downloaded)
    elif state.page == "settings":
        lines = settings_lines(state)
    elif state.page == "support":
        lines = support_lines(state.language)
    elif state.page == "language":
        lines = language_lines(state)
    else:
        lines = credits_lines(_version())
    height, width = screen.getmaxyx()
    max_scroll = max(0, len(lines) - (height - 1))
    state.page_scroll = max(0, min(state.page_scroll, max_scroll))
    for index, line in enumerate(lines[state.page_scroll:state.page_scroll + height - 1]):
        _put(screen, index, 0, line[: width - 1], width - 1)
    if max_scroll:
        _put(screen, height - 1, 0, f"wheel or ↑↓ scrolls   q back   ({state.page_scroll + 1}/{max_scroll + 1})"[: width - 1], width - 1, curses.A_DIM)
    screen.refresh()
    key = screen.getch()
    if key == curses.KEY_MOUSE:
        try:
            _id, _x, y, _z, bstate = curses.getmouse()
        except curses.error:
            bstate = 0
            y = -1
        up = getattr(curses, "BUTTON4_PRESSED", 0)
        down = getattr(curses, "BUTTON5_PRESSED", 0)
        if bstate & (up | down):
            state.page_scroll = max(0, min(max_scroll, state.page_scroll + (-3 if bstate & up else 3)))
            return
        if state.page == "language" and y >= 3:
            rows = language_rows()
            index = y - 3 + state.page_scroll
            if 0 <= index < len(rows):
                _choose_language(state, rows[index][0])
        elif state.page == "menu" and 1 <= y <= 8:
            _menu_choice(state, y - 1, items)
        return
    if key in (curses.KEY_UP, ord("k")):
        state.page_scroll = max(0, state.page_scroll - 1)
        return
    if key in (curses.KEY_DOWN, ord("j")):
        state.page_scroll = min(max_scroll, state.page_scroll + 1)
        return
    if state.page == "menu" and ord("1") <= key <= ord("8"):
        _menu_choice(state, key - ord("1"), items)
        return
    if state.page == "update" and key in (ord("d"),):
        _download_update(screen, state)
        return
    if state.page in ("notices", "update") and key in (ord("r"),):
        if state.page == "notices":
            _open_notices(state)
        else:
            _open_update(state)
        return
    if state.page in ("notices", "update") and key in (ord("o"),):
        target = ""
        if state.page == "notices" and state.notices:
            target = str(state.notices[0].get("url") or "")
        elif state.page == "update":
            target = str(state.update_result.get("release_url") or state.update_result.get("download") or "")
        if target:
            webbrowser.open(target)
            state.status = f"Opened {target}"
        return
    if key in (ord("q"), 27, ord("\n"), curses.KEY_ENTER):
        state.page = "software"
        state.page_scroll = 0
        return
    if state.page == "settings" and key in (ord("1"), ord("2"), ord("3"), ord("4"), ord("5")):
        toggle_setting(state, {ord("1"): "old", ord("2"): "240p", ord("3"): "nightly", ord("4"): "original", ord("5"): "keep_data"}[key])
        state.status = _load_rows(state, items, False)
        return
    if state.page == "language" and ord("1") <= key <= ord("9"):
        rows = language_rows()
        index = state.page_scroll + (key - ord("1"))
        if 0 <= index < len(rows):
            _choose_language(state, rows[index][0])


def _choose_language(state: BrowseState, code: str) -> None:
    state.language = code
    save_filters(state)
    remember_language(code)
    # The window applies this the next time it opens. Importing the
    # translation module here would load the whole GUI string table, and on
    # Python 3.9 that module cannot be imported at all.
    state.status = f"Language: {code}. The window uses the same choice."


def _prepare_machine() -> None:
    """Stage what a flash needs before the menu accepts a key.

    A frozen AppImage, tarball or zip already carries these modules, so it
    does not download anything. A source checkout or a lite session does,
    and it says so line by line. Qt is not imported.
    """
    if os.environ.get("INNIOASIS_SIMULATE") == "1" or os.environ.get("INNIOASIS_SKIP_PREP") == "1":
        return
    from .wsl import is_wsl, startup_note

    if is_wsl():
        print(startup_note(), flush=True)
    if getattr(sys, "frozen", False):
        print("Updater CE — this build already includes its tools.", flush=True)
        return
    print("Updater CE — preparing this computer.", flush=True)
    print("The menu opens when this finishes. The window's Qt libraries are not loaded.", flush=True)
    from .flash import ensure_mtk_python

    ok, detail = ensure_mtk_python()
    if ok:
        print("Python modules for MTKclient are ready.", flush=True)
    else:
        print(detail, flush=True)
        print("The catalogue still opens. Install needs the modules named above.", flush=True)
    print("Opening Select Software.", flush=True)


def run(refresh: bool = False, simulate: bool = False) -> int:
    if simulate or os.environ.get("INNIOASIS_SIMULATE") == "1":
        os.environ["INNIOASIS_SIMULATE"] = "1"
    _prepare_machine()
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
        print(f"Updater CE stopped: {exc}", file=sys.stderr)
        return 1
