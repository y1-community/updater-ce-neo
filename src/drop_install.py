"""Drag-and-drop firmware packages onto the main window.

A drop is a local file, not an online release. It is never written into the
installed-version record. The check looks for one scatter file and every
image that scatter lists before any flash starts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable, Optional


def is_scatter_filename(name: str) -> bool:
    lower = (name or "").lower()
    return "scatter" in lower and lower.endswith(".txt")


def classify_drop(path: Path) -> Optional[str]:
    """Return ``archive``, ``scatter``, ``folder``, or None when the drop is ignored."""
    path = Path(path)
    try:
        if path.is_dir():
            return "folder" if folder_has_scatter(path) else None
        if not path.is_file():
            return None
    except OSError:
        return None
    suffix = path.suffix.lower()
    if suffix in {".zip", ".rar"}:
        return "archive"
    if is_scatter_filename(path.name):
        return "scatter"
    return None


def folder_has_scatter(path: Path) -> bool:
    """True when a folder contains a ``*scatter*.txt`` file."""
    try:
        for root, dirs, files in os.walk(path):
            dirs[:] = [name for name in dirs if not name.startswith(".") and name not in {"__MACOSX", "__pycache__"}]
            for name in files:
                if is_scatter_filename(name):
                    return True
    except OSError:
        return False
    return False


def first_accepted_drop(paths: Iterable[Path]) -> Optional[Path]:
    """The first zip, rar, scatter file, or scatter folder. Anything else is ignored."""
    for raw in paths:
        path = Path(raw)
        if classify_drop(path):
            return path
    return None


def inspect_dropped_path(path: Path) -> tuple[bool, str, str]:
    """Extract if needed, then require a scatter and every listed image.

    Returns ``(ok, flash_path, error_code)``. ``error_code`` is empty on
    success, otherwise ``missing_scatter`` or ``missing_images``. ``flash_path``
    is what the existing install flow already accepts: the archive, or the
    folder that holds the scatter.
    """
    path = Path(path)
    kind = classify_drop(path)
    if kind is None:
        return False, "", "rejected"

    if kind == "scatter":
        directory = path.parent
        flash_path = str(directory)
    elif kind == "folder":
        directory = path
        flash_path = str(path)
    else:
        from .flash_service import ExtractWorker

        worker = ExtractWorker(str(path))
        try:
            directory = Path(worker._extract(str(path)))
        except Exception:
            return False, str(path), "missing_scatter"
        flash_path = str(path)

    error = package_check_error(Path(directory))
    if error:
        return False, flash_path, error
    return True, flash_path, ""


_FILE_NAME = re.compile(r"(?im)^\s*file_name:\s*(\S+)")


def _scatter_files(directory: Path) -> list[Path]:
    found = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [name for name in dirs if not name.startswith(".") and name not in {"__MACOSX", "__pycache__"}]
        for name in files:
            if is_scatter_filename(name):
                found.append(Path(root) / name)
    return found


def package_check_error(directory: Path) -> str:
    """``missing_scatter``, ``missing_images``, or ``""`` when the package can be installed."""
    directory = Path(directory)
    if not directory.is_dir():
        return "missing_scatter"
    scatters = _scatter_files(directory)
    if len(scatters) != 1:
        return "missing_scatter"
    scatter = scatters[0]
    try:
        text = scatter.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "missing_scatter"
    names = [name.strip() for name in _FILE_NAME.findall(text) if name.strip() and name.strip().upper() != "NONE"]
    if not names:
        images = [
            p for p in scatter.parent.iterdir()
            if p.is_file() and p.suffix.lower() in {".img", ".bin"} and p.name != "MTK_AllInOne_DA.bin"
        ]
        return "" if images else "missing_images"
    missing = [name for name in names if not (scatter.parent / name).is_file()]
    return "missing_images" if missing else ""
