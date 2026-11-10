"""Timed stand-in for a flash and a Rockbox theme install.

``--simulate`` and ``INNIOASIS_SIMULATE=1`` are explicit. Nothing here calls
mtkclient, adb or SP Flash Tool. The lines are the same status text the window
shows while a real install or a 360p theme install is in progress.
"""

from __future__ import annotations

import time
from typing import Callable

from .progress import (
    BROM,
    COMPLETE,
    DOWNLOADING,
    DOWNLOADING_DA,
    EXTRACTING,
    INIT_MTK,
    INSTALL_ORDER,
    KEEP_LEAVING,
    SEARCHING,
    THEME_DONE,
    THEME_DOWNLOAD,
    THEME_INSTALL,
    THEME_MERGE,
    THEME_ORDER,
    THEME_PUSH,
    UNPLUG,
    WAIT_MTK,
    WRITING_IMAGE,
    WRITING_PART,
)

Event = dict

INSTALL_EVENTS: tuple[tuple[int, str, bool], ...] = (
    (5, DOWNLOADING, False),
    (12, EXTRACTING, False),
    (18, UNPLUG, False),
    (25, SEARCHING, False),
    (30, INIT_MTK, False),
    (40, WAIT_MTK, False),
    (48, BROM, False),
    (55, DOWNLOADING_DA, False),
    (70, WRITING_IMAGE.format(pct=45), False),
    (80, WRITING_PART.format(part="boot", i=1, total=2, file="boot.img"), False),
    (92, WRITING_PART.format(part="system", i=2, total=2, file="system.img"), False),
    (100, COMPLETE, True),
)

INSTALL_FAILURE: tuple[tuple[int, str, bool], ...] = (
    (0, "Initializing mtkclient...", False),
    (15, "Waiting for MTK device... Power off device and connect USB.", False),
    (45, "Device stopped responding while configuring the download agent. Power it off completely, reconnect, and retry.", False),
    (45, "Install failed.", False),
)

ROCKBOX_EVENTS: tuple[tuple[int, str, bool], ...] = (
    (0, THEME_DOWNLOAD, False),
    (25, THEME_PUSH, False),
    (50, THEME_MERGE, False),
    (75, THEME_INSTALL, False),
    (100, THEME_DONE, True),
)

ROCKBOX_FAILURE: tuple[tuple[int, str, bool], ...] = (
    (0, "Downloading 360p Theme Pack...", False),
    (40, "Installing themes into .rockbox...", False),
    (40, "Rockbox theme install failed.", False),
)


def _play(
    script: tuple[tuple[int, str, bool], ...],
    sleep: float = 0.0,
    emit: Callable[[Event], None] | None = None,
) -> list[Event]:
    events: list[Event] = []
    for pct, message, ok in script:
        if sleep:
            time.sleep(sleep)
        event: Event = {"pct": pct, "message": message, "ok": ok}
        events.append(event)
        if emit:
            emit(event)
    return events


def install_progress(
    sleep: float = 0.0,
    emit: Callable[[Event], None] | None = None,
    fail: bool = False,
    keep: bool = False,
    post_install: str = "",
) -> list[Event]:
    """Replay an install.

    ``keep`` inserts the window's keep-data sentence. ``post_install`` is the
    catalogue URL the window opens after a successful install; it is attached
    to the completion event and shown as its own line.
    """
    if fail:
        events = _play(INSTALL_FAILURE, sleep=sleep, emit=emit)
    else:
        script = list(INSTALL_EVENTS)
        if keep:
            script.insert(2, (14, KEEP_LEAVING.format(names="USRDATA"), False))
        events = _play(tuple(script), sleep=sleep, emit=emit)
    url = (post_install or "").strip()
    for event in events:
        event.setdefault("url", "")
    if url and events and events[-1].get("ok"):
        events[-1]["url"] = url
        shown = {"pct": 100, "message": url, "ok": True, "url": url}
        events.append(shown)
        if emit:
            emit(shown)
    return events


def rockbox_themes(sleep: float = 0.0, emit: Callable[[Event], None] | None = None, fail: bool = False) -> list[Event]:
    return _play(ROCKBOX_FAILURE if fail else ROCKBOX_EVENTS, sleep=sleep, emit=emit)


def install_order() -> tuple[str, ...]:
    return INSTALL_ORDER


def theme_order() -> tuple[str, ...]:
    return THEME_ORDER
