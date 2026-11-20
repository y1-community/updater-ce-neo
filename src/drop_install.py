"""Drag-and-drop firmware packages onto the main window or the app icon.

A drop is a local file, not an online release. It is never written into the
installed-version record. The check picks the package's own scatter and then
requires every image it lists before any flash starts. Desktop and Dock drops
arrive as launch arguments or file-open events; they use the same check.

Scatter acceptance is the same as a browsed folder or a downloaded release:
``*_Android_scatter.txt`` outranks its ``preserve_data`` variant and any other
``*scatter*.txt``, a lone scatter is accepted whatever it is called, and only a
tie between two models stops the install.
"""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse


def is_scatter_filename(name: str) -> bool:
    lower = (name or "").lower()
    return "scatter" in lower and lower.endswith(".txt")


def is_rockbox_theme_path(path: Path) -> bool:
    """True if path points to a Rockbox theme archive, folder, or config file."""
    path = Path(path)
    try:
        if not path.exists():
            return False
    except OSError:
        return False

    if is_scatter_filename(path.name):
        return False
    if path.is_dir() and folder_has_scatter(path):
        return False

    if path.is_dir():
        if path.name.lower() == ".rockbox":
            return True
        if (path / ".rockbox").is_dir():
            return True
        try:
            subdirs = {p.name.lower() for p in path.iterdir() if p.is_dir()}
            if subdirs.intersection({"themes", "wps", "fonts", "backdrops", "icons"}):
                return True
            files = {p.name.lower() for p in path.iterdir() if p.is_file()}
            if any(f.endswith(".cfg") or f == "config.ini" for f in files):
                return True
        except OSError:
            pass
        return False

    if path.is_file():
        suffix = path.suffix.lower()
        if suffix in {".zip", ".rar"}:
            if suffix == ".zip" and zipfile.is_zipfile(path):
                try:
                    with zipfile.ZipFile(path, "r") as zf:
                        names = [info.filename.lower() for info in zf.infolist() if not info.is_dir()]
                        if any("scatter" in n and n.endswith(".txt") for n in names):
                            return False
                        if any(".rockbox" in n for n in names):
                            return True
                        for n in names:
                            for folder in ("themes/", "wps/", "fonts/", "backdrops/", "icons/"):
                                if folder in n or n.startswith(folder):
                                    return True
                            if n.endswith(".cfg") or n.endswith("config.ini"):
                                return True
                except Exception:
                    return False
            return False
        if suffix == ".cfg" or path.name.lower() in ("config.ini", "theme.cfg"):
            return True

    return False


def classify_drop(path: Path) -> Optional[str]:
    """Return ``archive``, ``scatter``, ``folder``, ``rockbox_theme``, or None when the drop is ignored."""
    path = Path(path)
    try:
        if path.is_dir():
            if folder_has_scatter(path):
                return "folder"
            if is_rockbox_theme_path(path):
                return "rockbox_theme"
            return None
        if not path.is_file():
            return None
    except OSError:
        return None
    suffix = path.suffix.lower()
    if is_scatter_filename(path.name):
        return "scatter"
    if suffix in {".zip", ".rar"}:
        if is_rockbox_theme_path(path):
            return "rockbox_theme"
        return "archive"
    if is_rockbox_theme_path(path):
        return "rockbox_theme"
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


def argument_path(raw) -> Optional[Path]:
    """A filesystem path from one command-line argument.

    Linux desktop entries pass ``%U`` file URLs, while Windows taskbar and
    shortcut drops pass a plain path. Switches (``--tray``, ``-psn_...``) are
    not paths and are skipped.
    """
    text = str(raw or "").strip()
    if not text or text.startswith("-"):
        return None
    text = text.strip('"')
    if text.lower().startswith("file:"):
        parsed = urlparse(text)
        if parsed.scheme.lower() != "file":
            return None
        text = unquote(parsed.path or "")
        if parsed.netloc and parsed.netloc.lower() not in {"", "localhost"}:
            text = f"//{parsed.netloc}{text}"
        # A Windows file URL is /C:/…; Path needs C:/. A POSIX path keeps
        # its leading slash because only a drive letter may lose it.
        if re.match(r"^/[A-Za-z]:", text):
            text = text[1:]
    return Path(text) if text else None


def external_package_from_arguments(args: Iterable[str]) -> Optional[Path]:
    """The firmware package the app was launched to open, if any.

    Dropping a file on a taskbar/Dock/desktop icon runs the app with that file
    as an argument. Only a package the drop flow already understands is
    returned, so an unrelated file in the command line cannot start an install.
    """
    for raw in args:
        path = argument_path(raw)
        if path is not None and classify_drop(path):
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
    # Ranked exactly as a browsed folder or a downloaded release: the package's
    # own ``*_Android_scatter.txt`` wins over its preserve-data copy and any
    # other ``*scatter*.txt``, and the images checked are that scatter's own.
    from .fw_package import ranked_scatters

    best = ranked_scatters(scatters)
    if len(best) != 1:
        return "missing_scatter"
    scatter = best[0]
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
