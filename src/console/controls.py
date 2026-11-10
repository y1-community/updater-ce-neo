"""Segmented controls for Select Software.

The same control answers a mouse click and a keyboard activation. Tab and
the arrow keys move the focus; Enter activates whatever is focused. Nothing
here imports Qt.
"""

from __future__ import annotations

import re

# Longer labels first so "Install from a file" is not read as "Install".
_LABELS = (
    ("Install from a file", "file"),
    ("Keep my data:", "keep"),
    ("Support Us", "support"),
    ("Brand:", "brand"),
    ("Model:", "model"),
    ("Type:", "type"),
    ("Software:", "software"),
    ("Refresh", "refresh"),
    ("Settings", "settings"),
    ("Language", "language"),
    ("Notices", "notices"),
    ("Updates", "updates"),
    ("Credits", "credits"),
    ("Themes", "themes"),
    ("Install", "install"),
    ("Menu", "menu"),
)


def control_order(state) -> list[str]:
    """Every control on Select Software, left to right, top to bottom."""
    order = ["brand", "model", "type", "software", "refresh", "list", "keep", "install", "file"]
    if (state.software or "").casefold() == "rockbox":
        order.append("themes")
    order += ["settings", "support", "language", "notices", "updates", "credits", "menu"]
    return order


def cycle_focus(state, step: int) -> str:
    """Move focus by ``step`` controls and return the newly focused one."""
    order = control_order(state)
    if not order:
        return state.focus
    try:
        index = order.index(state.focus)
    except ValueError:
        index = 0
    state.focus = order[(index + step) % len(order)]
    return state.focus


def segment(focused: bool, label: str) -> str:
    """A button. The focused one is marked so a keyboard user can see it."""
    if focused:
        return f"« {label} »"
    return f"[ {label} ]"


def spans_on_line(line: str) -> list[tuple[str, int, int]]:
    """``(control, start, end)`` for every button drawn on ``line``."""
    found: list[tuple[str, int, int]] = []
    occupied: list[tuple[int, int]] = []
    for label, control in _LABELS:
        for match in re.finditer(re.escape(label), line):
            start = match.start()
            # Include the bracket or guillemet that wraps the label.
            while start > 0 and line[start - 1] in " [«":
                start -= 1
            end = match.end()
            while end < len(line) and line[end] in " ]»":
                end += 1
            if any(not (end <= left or start >= right) for left, right in occupied):
                continue
            occupied.append((start, end))
            found.append((control, start, end))
    found.sort(key=lambda item: item[1])
    return found


def hit(lines: list[str], x: int, y: int) -> str | None:
    """The control under a pointer, or ``None`` when the click missed."""
    if y < 0 or y >= len(lines) or x < 0:
        return None
    line = lines[y]
    for control, start, end in spans_on_line(line):
        if start <= x < end:
            return control
    if re.match(r"^[> ] \S", line):
        return "list"
    return None
