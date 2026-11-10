"""Timed stand-in for a flash and a Rockbox theme install.

``--simulate`` and ``INNIOASIS_SIMULATE=1`` are explicit. Nothing here calls
mtkclient, adb or SP Flash Tool. The lines are the same status text the window
shows while a real install or a 360p theme install is in progress.
"""

from __future__ import annotations

import time
from typing import Callable

Event = dict

INSTALL_EVENTS: tuple[tuple[int, str, bool], ...] = (
    (0, "Initializing mtkclient...", False),
    (15, "Waiting for MTK device... Power off device and connect USB.", False),
    (45, "Writing firmware image... (45%)", False),
    (80, "Writing firmware image... (80%)", False),
    (100, "Flash complete! Disconnect USB and reboot.", True),
)

INSTALL_FAILURE: tuple[tuple[int, str, bool], ...] = (
    (0, "Initializing mtkclient...", False),
    (15, "Waiting for MTK device... Power off device and connect USB.", False),
    (45, "Device stopped responding while configuring the download agent. Power it off completely, reconnect, and retry.", False),
    (45, "Install failed.", False),
)

ROCKBOX_EVENTS: tuple[tuple[int, str, bool], ...] = (
    (0, "Downloading 360p Theme Pack...", False),
    (40, "Installing themes into .rockbox...", False),
    (70, "Installing Rockbox Themes & Fonts...", False),
    (100, "Theme pack successfully installed! You can now safely disconnect your player and select a theme in Rockbox (Settings > Theme Settings).", True),
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


def install_progress(sleep: float = 0.0, emit: Callable[[Event], None] | None = None, fail: bool = False) -> list[Event]:
    return _play(INSTALL_FAILURE if fail else INSTALL_EVENTS, sleep=sleep, emit=emit)


def rockbox_themes(sleep: float = 0.0, emit: Callable[[Event], None] | None = None, fail: bool = False) -> list[Event]:
    return _play(ROCKBOX_FAILURE if fail else ROCKBOX_EVENTS, sleep=sleep, emit=emit)
