"""Firmware packages that ship their own ``fw_package.ini`` or portable scatter files.

A firmware folder (or zip) can carry the portable Updater next to its files.
When a single ``*scatter*.txt`` sits in the ``sp_flash_tool`` directory of the
application (or in the same folder as the ``.app`` bundle on macOS), the application
automatically detects the firmware release, parses the scatter, stages the firmware
files to the normal working directory, and primes the installation flow directly
into the "Install Software" screen without requiring manual firmware selection.

If the folder contains a ``*rockbox*.txt`` file, Rockbox themes and fontpacks are
automatically marked for installation with the firmware.

``fw_package.ini`` is optional:
When present, it describes the Software Name, Version, Target model, target type,
and description shown to the user in install prompts. If omitted, the app infers
the model from the scatter file header and proceeds with default configurations.

Example sections: ``[Firmware]``, ``[Package]``, or ``[fw_package]``.
Keys supported (case-insensitive):
- ``software_name`` / ``name``: software name shown in header (e.g. ``Rockbox``)
- ``version``: version string (e.g. ``2.5``)
- ``target_model`` / ``model``: device model (e.g. ``Y1``, ``Y2``)
- ``target_type`` / ``type``: hardware type (e.g. ``Type A``, ``Stock``)
- ``description``: optional description of the release
- ``enabled``: ``true`` / ``false`` (default true)
"""

from __future__ import annotations

import configparser
import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .paths import INSTALL_DIR, IS_MAC, IS_WINDOWS, REPO_ROOT, find_sp_flash_tool

FW_PACKAGE_INI = "fw_package.ini"
PACKAGE_ICON_NAMES = ("updater.png", "updater.jpg", "updater.jpeg")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FwIni:
    enabled: bool = True
    name: str = ""
    version: str = ""
    model: str = ""
    type: str = ""
    description: str = ""


@dataclass(frozen=True)
class FwPackage:
    folder: Path
    scatter: Path
    name: str = ""
    version: str = ""
    model: str = ""
    type: str = ""
    description: str = ""
    image: Optional[Path] = None
    has_rockbox: bool = False
    has_ini: bool = False

    @property
    def display_name(self) -> str:
        if self.name and self.version:
            return f"{self.name} {self.version}".strip()
        return self.name or ""


def has_rockbox_marker(folder: Union[str, Path]) -> bool:
    """Check if folder contains a *rockbox*.txt marker file."""
    try:
        f = Path(folder)
        if not f.is_dir():
            return False
        return any(
            p.is_file() and p.suffix.lower() == ".txt" and "rockbox" in p.name.lower()
            for p in f.iterdir()
        )
    except OSError:
        return False


def read_fw_package_ini(path: Union[str, Path]) -> FwIni:
    """Parse an ``fw_package.ini``. An unreadable or malformed file counts as disabled."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return FwIni(enabled=False)
    bare = text.strip().lower()
    if bare in ("true", "false"):
        return FwIni(enabled=bare == "true")
    has_section = any(line.strip().startswith("[") and line.strip().endswith("]") for line in text.splitlines())
    if not has_section:
        text = "[fw_package]\n" + text
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as e:
        logger.warning("Ignoring malformed %s at %s: %s", FW_PACKAGE_INI, path, e)
        return FwIni(enabled=False)
    if not parser.sections():
        return FwIni(enabled=True)

    # Use [Firmware], [Package], [fw_package], or the first available section
    sec_name = parser.sections()[0]
    for s in parser.sections():
        if s.lower() in ("firmware", "package", "fw_package"):
            sec_name = s
            break
    section = parser[sec_name]

    def get_first(*keys: str) -> str:
        for k in keys:
            normalized = k.lower().replace(" ", "_")
            for sec_k in section.keys():
                if sec_k.lower().replace(" ", "_") == normalized:
                    val = section.get(sec_k, "").strip()
                    if val:
                        return val
        return ""

    enabled_raw = get_first("enabled")
    enabled = enabled_raw.lower() not in ("false", "0", "no", "off")

    name = get_first("software_name", "software name", "name", "software")
    version = get_first("version", "ver")
    model = get_first("target_model", "target model", "model", "target")
    sw_type = get_first("target_type", "target type", "type", "type_variant", "variant")
    desc = get_first("description", "desc")

    return FwIni(
        enabled=enabled,
        name=name,
        version=version,
        model=model,
        type=sw_type,
        description=desc,
    )


def _single_scatter(folder: Path) -> Optional[Path]:
    """Return single *scatter*.txt file if exactly one exists in folder, else None."""
    try:
        matches = [
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() == ".txt" and "scatter" in p.name.lower()
        ]
        return matches[0] if len(matches) == 1 else None
    except OSError:
        return None


def _package_image(folder: Path) -> Optional[Path]:
    try:
        by_name = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
        for name in PACKAGE_ICON_NAMES:
            if name in by_name:
                return by_name[name]
    except OSError:
        pass
    return None


def read_package(folder: Union[str, Path], require_ini: bool = True) -> Optional[FwPackage]:
    """Return the package in ``folder`` when it has exactly one scatter and all listed images exist.

    ``fw_package.ini`` is required when ``require_ini=True``, and optional when ``require_ini=False``.
    """
    from .flash_service import _missing_images

    folder_p = Path(folder)
    if not folder_p.is_dir():
        return None

    ini = folder_p / FW_PACKAGE_INI
    has_ini = ini.is_file()
    if has_ini:
        meta = read_fw_package_ini(ini)
        if not meta.enabled:
            return None
    else:
        if require_ini:
            return None
        meta = FwIni(enabled=True)

    scatter = _single_scatter(folder_p)
    if scatter is None:
        return None

    missing = _missing_images(scatter)
    if missing:
        logger.warning("fw_package at %s is missing %d listed file(s)", folder_p, len(missing))
        return None

    eff_model = meta.model
    if not eff_model:
        try:
            from . import catalog
            plat, proj = catalog.parse_scatter_header(scatter)
            eff_model = catalog.infer_model_from_scatter(plat, proj) or ""
        except Exception:
            eff_model = ""

    eff_name = meta.name
    if not eff_name:
        eff_name = f"Installing software on {eff_model}" if eff_model else "Installing software"

    has_rockbox = has_rockbox_marker(folder_p)
    image = _package_image(folder_p)

    return FwPackage(
        folder=folder_p.resolve(),
        scatter=scatter.resolve(),
        name=eff_name,
        version=meta.version,
        model=eff_model,
        type=meta.type,
        description=meta.description,
        image=image.resolve() if image else None,
        has_rockbox=has_rockbox,
        has_ini=has_ini,
    )


def locate_package(root: Union[str, Path], require_ini: bool = False) -> Optional[FwPackage]:
    """Find a firmware package under ``root`` (checking root, then subfolders)."""
    root_p = Path(root)
    if not root_p.is_dir():
        return None

    # Check root directly
    pkg = read_package(root_p, require_ini=require_ini)
    if pkg is not None:
        return pkg

    # Search subfolders with fw_package.ini
    for ini in sorted(root_p.rglob(FW_PACKAGE_INI)):
        pkg = read_package(ini.parent, require_ini=require_ini)
        if pkg is not None:
            return pkg

    if not require_ini:
        # Search subfolders with a single scatter
        for scatter in sorted(root_p.rglob("*.txt")):
            if "scatter" in scatter.name.lower():
                pkg = read_package(scatter.parent, require_ini=False)
                if pkg is not None:
                    return pkg
    return None


def _macos_app_parent(start_path: Path) -> Optional[Path]:
    """Find the directory containing the .app bundle on macOS."""
    p = start_path.resolve()
    for parent in [p] + list(p.parents):
        if parent.suffix.lower() == ".app":
            return parent.parent
    return None


def find_bundled_firmware(app_dir=None) -> Optional[FwPackage]:
    """Locate an automated portable firmware package packaged with the application.

    Locations searched:
    - macOS: Same folder as the .app bundle.
    - Windows & Linux: In the sp_flash_tool directory of the application
      (e.g. tools/SP_Flash_Tool, INSTALL_DIR/SP_Flash_Tool, or next to binary).

    Returns an FwPackage if exactly one *scatter*.txt file is found and all declared
    images exist, or None otherwise.
    """
    candidates: list[Path] = []
    base = Path(app_dir).resolve() if app_dir is not None else Path(INSTALL_DIR).resolve()

    if IS_MAC:
        # macOS: Search the folder containing the .app bundle
        app_parent = _macos_app_parent(Path(sys.executable))
        if app_parent and app_parent.is_dir():
            candidates.append(app_parent)
        base_app_parent = _macos_app_parent(base)
        if base_app_parent and base_app_parent.is_dir():
            candidates.append(base_app_parent)
        candidates.extend([base, base.parent, Path.cwd()])
    else:
        # Windows & Linux: Search sp_flash_tool directory candidates
        sp_found = find_sp_flash_tool()
        if sp_found:
            candidates.append(Path(sp_found).resolve())

        sp_subnames = ["SP_Flash_Tool", "sp_flash_tool", "_SP_flash_tool", "_sp_flash_tool"]
        for sub in sp_subnames:
            candidates.append((base / sub).resolve())
            candidates.append((REPO_ROOT / sub).resolve())
            candidates.append((REPO_ROOT / "tools" / sub).resolve())
            candidates.append((Path.cwd() / sub).resolve())

        candidates.append((REPO_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win").resolve())
        candidates.append((REPO_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux").resolve())
        candidates.extend([base, base.parent, Path.cwd()])

    seen = set()
    for cand in candidates:
        if not cand or not cand.is_dir():
            continue
        cand_resolved = cand.resolve()
        if cand_resolved in seen:
            continue
        seen.add(cand_resolved)

        pkg = read_package(cand_resolved, require_ini=False)
        if pkg is not None:
            logger.info("Found bundled firmware package at %s (scatter=%s)", cand_resolved, pkg.scatter.name)
            return pkg

    return None


def find_installed_package(app_dir=None) -> Optional[FwPackage]:
    """Look for a package beside the app executable, then in its parent folder."""
    base = Path(app_dir) if app_dir is not None else Path(INSTALL_DIR)
    for folder in (base, base.parent):
        pkg = read_package(folder, require_ini=True)
        if pkg is not None:
            return pkg
    return None


def prime_sp_tool_history_ini(
    sp_dir: Path,
    scatter_path: Path,
    da_name: str = "MTK_AllInOne_DA.bin",
) -> None:
    """Pre-populate history.ini in the SP Flash Tool directory with filename-only entries.

    Ensuring there are no paths and only the file name present allows SP Flash Tool
    to immediately locate MTKAllInOneDA.bin and the scatter file in its own directory
    portably across drive letters and host operating systems.
    """
    try:
        sp_dir = Path(sp_dir).resolve()
        if not sp_dir.is_dir():
            return
        scatter_name = Path(scatter_path).name
        history_ini = sp_dir / "history.ini"
        content = (
            "[RecentOpenFile]\n"
            "lastDir=\n"
            f"scatterHistory={scatter_name}\n\n"
            "[LastDAFilePath]\n"
            f"downloadAgentHistory={da_name}\n"
            "lastDir=\n"
        )
        history_ini.write_text(content, encoding="utf-8")
        logger.info("Primed portable history.ini at %s with scatter=%s, DA=%s", history_ini, scatter_name, da_name)
    except Exception as e:
        logger.warning("Could not prime history.ini at %s: %s", sp_dir, e)


def stage_bundled_firmware(pkg: FwPackage) -> Path:
    """Stage firmware files into the app's working directory under cache/downloads.

    Leaves the firmware files in the SP_Flash_Tool directory, copies the declared
    partition images to the app's working directory, copies any *rockbox*.txt marker
    and fw_package.ini, and primes history.ini beside SP Flash Tool.
    """
    from .flash_service import stage_firmware_for_flashing
    from .sp_flash_gui import update_sp_history_ini

    staged_scatter = stage_firmware_for_flashing(pkg.scatter)
    staged_dir = staged_scatter.parent

    # Copy any *rockbox*.txt marker file
    try:
        for r_file in pkg.folder.glob("*rockbox*.txt"):
            dest_r = staged_dir / r_file.name
            if not dest_r.exists():
                shutil.copy2(r_file, dest_r)
    except Exception as e:
        logger.debug("Could not copy rockbox marker: %s", e)

    # Prime SP Flash Tool's history.ini if the package is in an SP Flash Tool folder
    prime_sp_tool_history_ini(pkg.folder, pkg.scatter)

    # Also register with the global history.ini updater
    try:
        update_sp_history_ini(
            scatter_path=staged_scatter,
            extract_dir=staged_dir,
            model=pkg.model,
        )
    except Exception as e:
        logger.debug("Could not run update_sp_history_ini: %s", e)

    return staged_scatter


def prune_to_firmware_package(extract_dir: Union[str, Path]) -> list[str]:
    """After extracting a zip that carries a scatter file, keep only the scatter,
    the files it lists, fw_package.ini, rockbox markers, updater images, DA binaries,
    and any *.auth file. Everything else is removed.

    Ensures that zip archives and extraneous non-flashing files are cleaned up while
    retaining all components necessary for automated portable flashing.
    """
    root = Path(extract_dir)
    if not root.is_dir():
        return []

    from .flash_service import _list_expected_images

    # Look for a valid package (all declared images present)
    pkg = locate_package(root, require_ini=False)
    if pkg is None:
        return []

    scatter = pkg.scatter
    folder = scatter.parent
    keep = {scatter.resolve()}
    if (folder / FW_PACKAGE_INI).is_file():
        keep.add((folder / FW_PACKAGE_INI).resolve())

    for _name, img in _list_expected_images(scatter):
        if img.exists():
            keep.add(img.resolve())

    for p in folder.glob("*.auth"):
        keep.add(p.resolve())

    for p in folder.glob("*rockbox*.txt"):
        keep.add(p.resolve())

    for name in PACKAGE_ICON_NAMES:
        icon_p = folder / name
        if icon_p.is_file():
            keep.add(icon_p.resolve())

    for da_name in ("MTK_AllInOne_DA.bin", "DA_SWSEC.bin"):
        da_p = folder / da_name
        if da_p.is_file():
            keep.add(da_p.resolve())

    removed: list[str] = []
    for f in root.rglob("*"):
        if f.is_file() and f.resolve() not in keep:
            try:
                f.unlink()
                removed.append(str(f))
            except OSError as e:
                logger.debug("Could not remove non-firmware file %s: %s", f, e)

    for d in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass

    logger.info("Pruned %d non-firmware file(s) outside firmware package at %s", len(removed), folder)
    return removed
