"""Firmware packages that ship their own ``fw_package.ini``.

A firmware folder (or zip) can carry the portable Updater next to its files.
When ``fw_package.ini`` sits in the same folder as exactly one ``*scatter*.txt``
and every image that scatter lists is present, the app opens the install-from-file
screen with that folder already loaded and filled in from the ini.

``fw_package.ini`` is either a bare ``true`` / ``false`` (the default content is
``true``) or an INI file with an optional ``[fw_package]`` section holding:

- ``enabled``: ``true`` / ``false`` (default true)
- ``name``: software name shown in the header, e.g. ``Rockbox``
- ``version``: version shown beside the name
- ``model``: device model, e.g. ``Y1`` or ``Y2``
- ``type``: software type, matched against the type list, e.g. ``Stock``

An optional ``updater.png`` / ``updater.jpg`` / ``updater.jpeg`` next to the ini
supplies the header icon.
"""

from __future__ import annotations

import configparser
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .paths import INSTALL_DIR

FW_PACKAGE_INI = "fw_package.ini"
PACKAGE_ICON_NAMES = ("updater.png", "updater.jpg", "updater.jpeg")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FwIni:
    enabled: bool = False
    name: str = ""
    version: str = ""
    model: str = ""
    type: str = ""


@dataclass(frozen=True)
class FwPackage:
    folder: Path
    scatter: Path
    name: str = ""
    version: str = ""
    model: str = ""
    type: str = ""
    image: Optional[Path] = None

    @property
    def display_name(self) -> str:
        return f"{self.name} {self.version}".strip()


def read_fw_package_ini(path) -> FwIni:
    """Parse an ``fw_package.ini``. An unreadable or malformed file counts as disabled."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return FwIni()
    bare = text.strip().lower()
    if bare in ("true", "false"):
        return FwIni(enabled=bare == "true")
    if not text.lstrip().startswith("["):
        text = "[fw_package]\n" + text
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as e:
        logger.warning("Ignoring malformed %s at %s: %s", FW_PACKAGE_INI, path, e)
        return FwIni()
    if not parser.sections():
        return FwIni(enabled=True)
    section = parser[parser.sections()[0]]

    def value(key: str) -> str:
        return section.get(key, "").strip()

    return FwIni(
        enabled=value("enabled").lower() in ("", "true", "1", "yes", "on"),
        name=value("name"),
        version=value("version"),
        model=value("model"),
        type=value("type"),
    )


def _single_scatter(folder: Path) -> Optional[Path]:
    matches = [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() == ".txt" and "scatter" in p.name.lower()
    ]
    return matches[0] if len(matches) == 1 else None


def _package_image(folder: Path) -> Optional[Path]:
    by_name = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    for name in PACKAGE_ICON_NAMES:
        if name in by_name:
            return by_name[name]
    return None


def read_package(folder) -> Optional[FwPackage]:
    """Return the package in ``folder`` when it is enabled, has one scatter, and all listed images exist."""
    from .flash_service import _missing_images

    folder = Path(folder)
    ini = folder / FW_PACKAGE_INI
    if not ini.is_file():
        return None
    meta = read_fw_package_ini(ini)
    if not meta.enabled:
        return None
    scatter = _single_scatter(folder)
    if scatter is None:
        return None
    missing = _missing_images(scatter)
    if missing:
        logger.warning("fw_package at %s is missing %d listed file(s)", folder, len(missing))
        return None
    image = _package_image(folder)
    return FwPackage(
        folder=folder.resolve(),
        scatter=scatter.resolve(),
        name=meta.name,
        version=meta.version,
        model=meta.model,
        type=meta.type,
        image=image.resolve() if image else None,
    )


def locate_package(root) -> Optional[FwPackage]:
    """Find the package anywhere under ``root`` (a zip extraction may nest it in a subfolder)."""
    root = Path(root)
    if not root.is_dir():
        return None
    for ini in sorted(root.rglob(FW_PACKAGE_INI)):
        pkg = read_package(ini.parent)
        if pkg is not None:
            return pkg
    return None


def find_installed_package(app_dir=None) -> Optional[FwPackage]:
    """Look for a package beside the app executable, then in its parent folder."""
    base = Path(app_dir) if app_dir is not None else Path(INSTALL_DIR)
    for folder in (base, base.parent):
        pkg = read_package(folder)
        if pkg is not None:
            return pkg
    return None


def prune_to_firmware_package(extract_dir) -> list[str]:
    """After extracting a zip that carries ``fw_package.ini``, keep only the scatter,
    the files it lists, the ini, the updater image and any ``*.auth`` file.
    Everything else is removed.

    Does nothing when no valid package is inside ``extract_dir``.
    """
    root = Path(extract_dir)
    pkg = locate_package(root)
    if pkg is None:
        return []

    from .flash_service import _list_expected_images

    keep = {pkg.scatter.resolve(), (pkg.folder / FW_PACKAGE_INI).resolve()}
    keep.update(img.resolve() for _name, img in _list_expected_images(pkg.scatter))
    keep.update(p.resolve() for p in pkg.folder.glob("*.auth"))
    if pkg.image is not None:
        keep.add(pkg.image)

    removed: list[str] = []
    for f in root.rglob("*"):
        if f.is_file() and f.resolve() not in keep:
            f.unlink()
            removed.append(str(f))
    for d in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
    logger.info("Pruned %d file(s) outside firmware package %s", len(removed), pkg.folder)
    return removed
