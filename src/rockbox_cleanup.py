"""Remove a player's old ``.rockbox`` folders as an install step.

Keeping settings means two different things on a Rockbox install. The flash
step decides whether the user data partition is written, and this step decides
whether the old ``.rockbox`` folder survives: themes, fonts, equalizer presets,
bookmarks and Rockbox's own configuration live there, on the storage partition,
so formatting the data partition alone would leave a stale setup behind.

The step is part of a full install journey, next to the themes stage: when
Keep my settings is ticked it is skipped, and when it is not the user is guided
through the same power-on / ADB / USB-storage fallback sequence the themes
stage uses. After an SP Flash install the player reboots into the software that
was just written, so the ADB watch usually finds it with no user action at all.

The rules are plain functions so they can be tested without a window or a
player; the workers only carry them to a thread.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

ROCKBOX_DIR_NAME = ".rockbox"


def rockbox_install_needs_cleanup(*, is_rockbox: bool, keep_data: bool) -> bool:
    """Whether this install should remove the player's old ``.rockbox`` folder.

    Ticking Keep my settings is the user asking for their setup to come across,
    so nothing is deleted. Unticked means a fresh start, and for a Rockbox
    install that includes the folder holding the old themes and settings. A
    non-Rockbox install has no ``.rockbox`` payload of its own to clean.
    """
    return bool(is_rockbox) and not bool(keep_data)


def is_rockbox_dir(path) -> bool:
    """True when ``path`` names a ``.rockbox`` folder (never a parent)."""
    try:
        return Path(str(path)).name.casefold() == ROCKBOX_DIR_NAME
    except (TypeError, ValueError):
        return False


def find_rockbox_dirs(
    selected: str | Path,
    *,
    max_children: int = 32,
) -> list[Path]:
    """Every ``.rockbox`` folder a picked player folder can resolve to.

    The picker shows a hidden ``.rockbox`` only when the OS allows it, so the
    user commonly picks the drive root, the ``sdcard`` folder, or a folder
    inside it. Only the pick, its parents and a small number of immediate
    children are inspected; nothing is created.
    """
    try:
        base = Path(selected).expanduser()
    except (TypeError, ValueError):
        return []
    if not base.exists():
        return []
    if not base.is_dir():
        base = base.parent

    found: list[Path] = []

    def add(path: Path) -> None:
        # A pick inside the folder and its parent both resolve to the same
        # path; deleting it twice would report a folder that was already gone.
        if path not in found:
            found.append(path)

    for candidate in [base, *base.parents]:
        if is_rockbox_dir(candidate) and candidate.is_dir():
            # Picked the folder itself: its parent is also inspected below, and
            # that lookup resolves to this same path.
            add(candidate)
            continue
        child = candidate / ROCKBOX_DIR_NAME
        if child.is_dir():
            add(child)
        if len(found) > 2:
            break

    try:
        children = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        children = []
    for child in children[:max_children]:
        if is_rockbox_dir(child):
            add(child)
            continue
        nested = child / ROCKBOX_DIR_NAME
        if nested.is_dir():
            add(nested)
    return found


def delete_local_rockbox_dirs(
    dirs,
    *,
    log_cb: Optional[Callable[[str], None]] = None,
    cancel_cb: Optional[Callable[[], bool]] = None,
) -> tuple[bool, str, int]:
    """Delete local ``.rockbox`` folders and report how many were removed."""
    removed = 0
    failed: list[str] = []
    for path in dirs or ():
        if cancel_cb is not None and cancel_cb():
            return False, "Removing the old Rockbox folders was cancelled", removed
        target = Path(path)
        if not is_rockbox_dir(target):
            failed.append(f"{target}: not a .rockbox folder")
            continue
        try:
            shutil.rmtree(target)
        except FileNotFoundError:
            removed += 1
            continue
        except OSError as e:
            failed.append(f"{target}: {e}")
            continue
        removed += 1
        if log_cb is not None:
            try:
                log_cb(f"Removed {target}")
            except Exception:
                logger.debug("Rockbox cleanup log callback failed", exc_info=True)
    if failed:
        return False, "; ".join(failed[:3]), removed
    return True, f"Removed {removed} .rockbox folder(s)", removed


class AdbRockboxCleanupWorker(QThread):
    """Delete the player's ``.rockbox`` folders over ADB."""

    progress = Signal(int, int)
    log = Signal(str)
    finished = Signal(bool, str)  # success, message

    def __init__(self, adb: str, serial: str, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._serial = serial

    def run(self):
        try:
            from . import adb as _adb

            if self.isInterruptionRequested():
                self.finished.emit(False, "Removing the old Rockbox folders was cancelled")
                return
            self.progress.emit(5, 100)
            dirs = _adb.adb_find_all_rockbox_dirs(self._adb, self._serial)
            if not dirs:
                self.progress.emit(100, 100)
                self.finished.emit(True, "No .rockbox folder was found on the player")
                return
            self.log.emit(
                f"[Rockbox Cleanup] Removing {len(dirs)} .rockbox folder(s): {', '.join(dirs)}"
            )
            self.progress.emit(30, 100)
            ok, message = _adb.adb_delete_rockbox_dirs(
                self._adb,
                self._serial,
                dirs,
                cancel_cb=self.isInterruptionRequested,
                log_cb=self.log.emit,
            )
            self.progress.emit(100, 100)
            self.finished.emit(ok, message)
        except Exception as e:
            logger.exception("ADB Rockbox cleanup failed: %s", e)
            self.finished.emit(False, str(e))


class LocalRockboxCleanupWorker(QThread):
    """Delete ``.rockbox`` folders on a player mounted as USB storage."""

    progress = Signal(int, int)
    log = Signal(str)
    finished = Signal(bool, str, int)  # success, message, count

    def __init__(self, selected: str | Path, parent=None):
        super().__init__(parent)
        self._selected = Path(selected)

    def run(self):
        try:
            if self.isInterruptionRequested():
                self.finished.emit(False, "Removing the old Rockbox folders was cancelled", 0)
                return
            self.progress.emit(10, 100)
            dirs = find_rockbox_dirs(self._selected)
            if not dirs:
                self.progress.emit(100, 100)
                self.finished.emit(True, "No .rockbox folder was found in the selected folder", 0)
                return
            self.log.emit(
                f"[Rockbox Cleanup] Removing {len(dirs)} .rockbox folder(s): "
                + ", ".join(str(p) for p in dirs)
            )
            ok, message, count = delete_local_rockbox_dirs(
                dirs,
                log_cb=self.log.emit,
                cancel_cb=self.isInterruptionRequested,
            )
            self.progress.emit(100, 100)
            self.finished.emit(ok, message, count)
        except Exception as e:
            logger.exception("Local Rockbox cleanup failed: %s", e)
            self.finished.emit(False, str(e), 0)
