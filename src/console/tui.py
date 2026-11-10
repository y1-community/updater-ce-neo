"""A curses interface for Updater CE that runs on tiny machines.

The TUI is deliberately small: it lists models, lists firmware, downloads with
a visible progress bar and can hand an unpacked folder to MTKClient. When the
terminal cannot host curses (piped output, ``TERM=dumb``) it falls back to
numbered prompts, so the same entry point works over a serial console.

Selection, keys: arrows or j/k, Enter to open, ``d`` download, ``f`` flash,
``r`` refresh, ``q`` back/quit.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import catalog, download as dl, flash

TITLE = "Updater CE — terminal"


def _plain(refresh: bool = False) -> int:
    """Numbered prompts for terminals without curses."""
    try:
        items = catalog.packages(force=refresh)
    except RuntimeError as exc:
        print(f"catalogue unavailable: {exc}", file=sys.stderr)
        return 1
    models = catalog.models(items)
    if not models:
        print("The catalogue has no models.")
        return 1
    print(TITLE + " (plain mode)")
    for index, model in enumerate(models, 1):
        count = sum(1 for item in items if item.model == model)
        print(f"  {index:>2}. {model} ({count} firmwares)")
    choice = input("Model number (q to quit): ").strip()
    if choice.lower() in ("q", ""):
        return 0
    try:
        model = models[int(choice) - 1]
    except (ValueError, IndexError):
        print("Not a model number.")
        return 2
    entries = [item for item in items if item.model == model]
    for index, item in enumerate(entries, 1):
        print(f"  {index:>2}. {item.name} — {item.repo} ({item.package_name})")
    pick = input("Firmware number (q to quit): ").strip()
    if pick.lower() in ("q", ""):
        return 0
    try:
        item = entries[int(pick) - 1]
    except (ValueError, IndexError):
        print("Not a firmware number.")
        return 2
    output = Path(input("Download folder [.]: ").strip() or ".")
    try:
        url, asset = catalog.resolve_download(item)
    except RuntimeError as exc:
        print(f"no download: {exc}", file=sys.stderr)
        return 1
    target = dl.download(url, output / asset, progress=dl.report_progress(asset))
    print(f"saved {target}")
    if input("Extract now? [y/N]: ").strip().lower() == "y":
        folder = output / Path(asset).stem
        written = dl.extract(target, folder)
        print(f"extracted {len(written)} files into {folder}")
        if input("Flash this folder with MTKClient? [y/N]: ").strip().lower() == "y":
            return flash.flash_directory(folder)
    return 0


def _curses_run(refresh: bool = False) -> int:
    import curses

    items = catalog.packages(force=refresh)

    def picker(screen, title: str, rows: list[tuple[str, str]], subtitle: str = "") -> int:
        curses.curs_set(0)
        screen.keypad(True)
        try:
            # Mouse-aware terminals get click-to-select as well as the keys;
            # a serial console simply reports no mouse events.
            curses.mousemask(curses.ALL_MOUSE_EVENTS)
        except curses.error:
            pass
        position = 0
        while True:
            screen.erase()
            height, width = screen.getmaxyx()
            screen.addnstr(0, 0, title.ljust(width - 1), width - 1, curses.A_BOLD)
            if subtitle:
                screen.addnstr(1, 0, subtitle.ljust(width - 1), width - 1, curses.A_DIM)
            view = max(1, height - 4)
            start = max(0, min(position - view // 2, len(rows) - view))
            for offset, (label, _value) in enumerate(rows[start:start + view]):
                line = start + offset
                attr = curses.A_REVERSE if line == position else curses.A_NORMAL
                screen.addnstr(2 + offset, 0, label.ljust(width - 1), width - 1, attr)
            screen.addnstr(height - 2, 0, "↑↓ move   Enter select   q back".ljust(width - 1), width - 1, curses.A_DIM)
            screen.refresh()
            key = screen.getch()
            if key in (curses.KEY_UP, ord("k")):
                position = (position - 1) % len(rows)
            elif key in (curses.KEY_DOWN, ord("j")):
                position = (position + 1) % len(rows)
            elif key in (curses.KEY_ENTER, 10, 13):
                if rows[position][1] == "__quit__":
                    return -1
                return position
            elif key == curses.KEY_MOUSE:
                try:
                    _id, _x, my, _z, bstate = curses.getmouse()
                except curses.error:
                    continue
                if 2 <= my < 2 + len(rows[start:start + view]):
                    position = start + (my - 2)
                if bstate & (curses.BUTTON1_CLICKED | curses.BUTTON1_DOUBLE_CLICKED):
                    if rows[position][1] == "__quit__":
                        return -1
                    return position
            elif key in (ord("q"), 27):
                return -1

    def show_message(screen, text: str) -> None:
        height, width = screen.getmaxyx()
        screen.erase()
        for index, line in enumerate(text.splitlines()[: height - 2]):
            screen.addnstr(index, 0, line[: width - 1], width - 1)
        screen.addnstr(height - 2, 0, "press any key"[: width - 1], width - 1, curses.A_DIM)
        screen.refresh()
        screen.getch()

    def body(screen) -> int:
        models = catalog.models(items)
        model_rows = [(f"{model}  ({sum(1 for i in items if i.model == model)})", model) for model in models]
        model_rows.append(("Quit", "__quit__"))
        while True:
            picked = picker(screen, TITLE, model_rows, "Choose a player model")
            if picked < 0:
                return 0
            model = models[picked]
            entries = [item for item in items if item.model == model]
            entry_rows = [(f"{item.name}  ·  {item.brand or item.repo}  ·  {item.package_name}", str(i))
                          for i, item in enumerate(entries)]
            entry_rows.append(("Back", "__quit__"))
            index = picker(screen, f"{model} firmware", entry_rows, "Enter to download")
            if index < 0:
                continue
            item = entries[index]
            try:
                url, asset = catalog.resolve_download(item)
            except RuntimeError as exc:
                show_message(screen, f"No download available.\n\n{exc}")
                continue

            def draw(done: int, total: int | None) -> None:
                height, width = screen.getmaxyx()
                screen.erase()
                screen.addnstr(0, 0, f"Downloading {asset}", width - 1, curses.A_BOLD)
                if total:
                    ratio = min(1.0, done / total)
                    bar_width = max(10, width - 24)
                    filled = int(ratio * bar_width)
                    screen.addnstr(2, 0, "[" + "#" * filled + "-" * (bar_width - filled) + "]", width - 1)
                    screen.addnstr(3, 0, f"{int(ratio * 100):3d}%  {dl.human_bytes(done)} / {dl.human_bytes(total)}", width - 1)
                else:
                    screen.addnstr(2, 0, dl.human_bytes(done), width - 1)
                screen.addnstr(height - 2, 0, "Ctrl-C cancels; a partial file resumes next time"[: width - 1], width - 1, curses.A_DIM)
                screen.refresh()

            target = Path.cwd() / asset
            try:
                dl.download(url, target, progress=draw)
            except (RuntimeError, OSError) as exc:
                show_message(screen, f"Download failed.\n\n{exc}\n\nA partial file was kept, so trying again resumes it.")
                continue
            folder = target.with_suffix("")
            if folder.exists():
                show_message(screen, f"Already downloaded:\n{target}\n\nExtract into {folder} again? y/n")
            written = dl.extract(target, folder)
            scatter = dl.find_scatter(folder)
            show_message(
                screen,
                f"Saved {target}\n{len(written)} files in {folder}\n\n"
                + (f"Scatter: {scatter.name}" if scatter else "No scatter file found in this archive."),
            )
            if scatter:
                # The install is the first task that needs a tool the lite
                # session may not have. Fetch it here, between tasks, rather
                # than at menu time: an AppImage already carries it, and a
                # Pi Zero only pays for it if the user goes this far.
                try:
                    from .components import ensure as ensure_component

                    ensure_component("spflash")
                except RuntimeError:
                    pass  # MTKClient remains the console flasher
                answer = picker(screen, "Flash with MTKClient?", [("No, keep the files", "no"), ("Yes, write now", "yes")], str(folder))
                if answer == 1:
                    curses.endwin()
                    return flash.flash_directory(folder)

    return curses.wrapper(body)


def run(refresh: bool = False) -> int:
    try:
        # On a fast link, fetch the tools a session may need up front. On a slow
        # one nothing is downloaded until a task asks for it. Either way this is
        # best effort: a failure must not stop the menu from opening.
        if sys.stdin.isatty() and not os.environ.get("INNIOASIS_NO_PREFETCH"):
            try:
                from . import components

                if components.measure_mbps() >= components.PREFETCH_MBPS:
                    print("Fast link: prefetching session tools…", file=sys.stderr)
                    components.prefetch()
            except Exception:
                pass
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            return _plain(refresh)
        if (sys.platform != "win32") and (not sys.stdin.encoding):
            return _plain(refresh)
        return _curses_run(refresh)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
