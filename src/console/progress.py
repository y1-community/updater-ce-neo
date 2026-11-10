"""Install status lines, in the window's words.

``flash_service`` decides these phrases and then imports PySide, so the text
session cannot call it. The match order and the English are copied from that
parser and from the ``action_*`` strings the install card shows.
"""

from __future__ import annotations

import re

DOWNLOADING = "Downloading firmware"
EXTRACTING = "Extracting"
UNPLUG = "Keep the device unplugged until the connect prompt appears."
SEARCHING = "Searching for device (keep unplugged)..."
INIT_MTK = "Initializing mtkclient..."
WAIT_MTK = "Waiting for MTK device... Power off device and connect USB."
BROM = "Device detected in BROM mode..."
DOWNLOADING_DA = "Downloading DA to device..."
DOWNLOADING_BOOTLOADER = "Downloading Bootloader..."
FORMAT_OK = "Flash format succeeded..."
WRITING_IMAGE = "Writing firmware image... ({pct}%)"
WRITING_PART = "Writing {part} ({i}/{total}): {file}"
COMPLETE = "Flash complete! Disconnect USB and reboot."
KEEP_USING = "Keep my data: using the package's {name}"
KEEP_LEAVING = "Keep my data: leaving {names} untouched"
THEME_DOWNLOAD = "Downloading 360p Theme Pack..."
THEME_PUSH = "Pushing themes and fonts over ADB..."
THEME_MERGE = "Installing themes into .rockbox..."
THEME_INSTALL = "Installing Rockbox Themes & Fonts..."
THEME_DONE = (
    "Theme pack successfully installed! You can now safely disconnect your "
    "player and select a theme in Rockbox (Settings > Theme Settings)."
)

# A real install emits these, in this order, once each phase has happened.
INSTALL_ORDER = (
    DOWNLOADING,
    EXTRACTING,
    UNPLUG,
    SEARCHING,
    INIT_MTK,
    WAIT_MTK,
    BROM,
    DOWNLOADING_DA,
    WRITING_IMAGE.format(pct=45),
    WRITING_PART.format(part="boot", i=1, total=2, file="boot.img"),
    WRITING_PART.format(part="system", i=2, total=2, file="system.img"),
    COMPLETE,
)

THEME_ORDER = (
    THEME_DOWNLOAD,
    THEME_PUSH,
    THEME_MERGE,
    THEME_INSTALL,
    THEME_DONE,
)


def status_from_tool_line(line: str) -> str:
    """The window's action sentence for one tool line, or ``""``."""
    text = str(line or "").strip()
    if not text:
        return ""
    low = text.lower()
    if "incomplete" not in low and (
        "flash complete" in low
        or "all command exec" in low
        or ("all command" in low and "exec" in low)
    ):
        return COMPLETE
    if (
        "search usb" in low
        or "searchusb" in low
        or "scanning usb" in low
        or low.startswith("...")
        or low.startswith("hint:")
        or low.startswith("port - hint")
        or "power off the phone" in low
        or "power off the device before connecting" in low
    ):
        return SEARCHING
    if any(
        marker in low
        for marker in (
            "brom connected",
            "da high speed usb port detected",
            "usb port is obtained",
            "usb port detected",
            "com port is open",
            "executing dadownloadall",
            "start downloading via da",
        )
    ):
        return BROM
    if "of da has been sent" in low:
        return DOWNLOADING_DA
    if "of bootloader has been sent" in low:
        return DOWNLOADING_BOOTLOADER
    if "format succeeded" in low:
        return FORMAT_OK
    if "of image data has been sent" in low:
        match = re.search(r"(\d+)%", text)
        return WRITING_IMAGE.format(pct=match.group(1) if match else "0")
    if "initializing mtkclient" in low:
        return INIT_MTK
    if "waiting for mtk" in low or "power off device and connect usb" in low:
        return WAIT_MTK
    part = re.search(
        r"writing\s+(\S+)\s+\((\d+)\s*/\s*(\d+)\)\s*:\s*(\S+)",
        text,
        re.IGNORECASE,
    )
    if part:
        return WRITING_PART.format(
            part=part.group(1),
            i=part.group(2),
            total=part.group(3),
            file=part.group(4),
        )
    if "keep the device unplugged" in low:
        return UNPLUG
    return ""
