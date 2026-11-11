"""Firmware packages that ship their own ``fw_package.ini`` or portable scatter files.

A firmware folder (or zip) can carry the portable Updater next to its files.
When a single ``*scatter*.txt`` sits in the ``sp_flash_tool`` directory of the
application (or in a ``Firmware`` folder beside the ``.app`` bundle on macOS), the
application automatically detects the firmware release, parses the scatter, stages
the firmware files to the normal working directory, and primes the installation flow
directly into the "Install Software" screen without requiring manual firmware
selection.

Such a folder only needs the optional ``fw_package.ini``, the optional
``updater.png`` / ``updater.jpg`` / ``updater.jpeg`` icon, the scatter and every file
the scatter lists. Anything else a firmware release carries (the SP Flash Tool
executables a Windows/Linux pack puts around the ROM files, readmes, archives) is
ignored and does not need to be packed. A scatter whose name contains ``preserve`` is
a data-preserving variant of the release, not the package's own scatter: the
package's ``*_Android_scatter.txt`` outranks both it and any other
``*scatter*.txt`` naming, and a lone scatter is accepted whatever it is called.

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
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from .paths import INSTALL_DIR, IS_MAC, IS_WINDOWS, REPO_ROOT, find_sp_flash_tool, get_appimage_dir

FW_PACKAGE_INI = "fw_package.ini"
PACKAGE_ICON_NAMES = ("updater.png", "updater.jpg", "updater.jpeg")
#: Folder beside the macOS ``.app`` that holds a portable firmware release.
MACOS_FIRMWARE_FOLDER = "Firmware"
FIRMWARE_FOLDER_NAMES = ("Firmware", "firmware", "_Firmware", "_firmware")
SP_TOOL_FOLDER_NAMES = ("SP_Flash_Tool", "sp_flash_tool", "_SP_flash_tool", "_sp_flash_tool")
PACKAGE_SUBFOLDER_NAMES = FIRMWARE_FOLDER_NAMES + SP_TOOL_FOLDER_NAMES
#: A portable pack may rename its flash-tool folder for reading order
#: ("2. Open and run flash_tool"), so any immediate subfolder that names the
#: tool counts as the package folder too.
FLASH_TOOL_NAME_HINTS = ("flash_tool", "flash tool", "flashtool", "flash-tool")
logger = logging.getLogger(__name__)


def package_subfolders(root: Union[str, Path]) -> list:
    """Immediate subfolders of ``root`` that may hold a portable package.

    The canonical ``Firmware`` / ``SP_Flash_Tool`` names come first, then any
    other subfolder whose name says it is the flash tool. A pack that numbers
    its folders ("1. Install Drivers and Reboot PC", "2. Open and run
    flash_tool") therefore still opens on the install screen, and the numbered
    driver folder never counts as firmware because its name does not name the
    tool.
    """
    root_p = Path(root)
    found = [root_p / name for name in PACKAGE_SUBFOLDER_NAMES]
    try:
        children = sorted(p for p in root_p.iterdir() if p.is_dir())
    except OSError:
        return found
    for child in children:
        if child in found or child.name.startswith((".", "__")):
            continue
        lowered = child.name.lower()
        if any(hint in lowered for hint in FLASH_TOOL_NAME_HINTS):
            found.append(child)
    return found


@dataclass(frozen=True)
class FwIni:
    enabled: bool = True
    name: str = ""
    version: str = ""
    model: str = ""
    type: str = ""
    description: str = ""
    post_install: str = ""
    post_install_2: str = ""
    # The catalogue's own reaction to this release, so an offline copy of it is
    # treated exactly like the online entry: no Keep Data offer at all, and
    # Format All + Download because the partition table has to be replaced
    # (``no_keep_data`` / ``PMT_format_needed`` in ``slidia_manifest.xml``).
    do_not_keep_data: bool = False
    pmt_format_needed: bool = False
    # Firmware names this rom is a derivative of / compatible with ("Original
    # Software, Rockbox"), so the user can be asked whether that is what they
    # are running before a data-wiping install.
    derivatives: tuple = ()


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
    has_rockbox_240p: bool = False
    has_ini: bool = False
    has_preserve_data_scatter: bool = False
    post_install: str = ""
    post_install_2: str = ""
    do_not_keep_data: bool = False
    pmt_format_needed: bool = False
    #: Firmware this release was built from, as its ini names it ("Original
    #: Software, Rockbox"). Keep Data reads this exactly like the catalogue's
    #: ``derivative``: a release that names its parent shares that data
    #: partition, so an offline copy is offered the same choice as the online
    #: entry instead of being treated as an unknown firmware.
    derivative: str = ""

    @property
    def display_name(self) -> str:
        if self.name and self.version:
            return f"{self.name} {self.version}".strip()
        return self.name or ""

    @property
    def has_release_name(self) -> bool:
        """True when a real release name was found, not a generic install heading."""
        if not self.name:
            return False
        generic_prefixes = ("Installing software", "Get ready to install", "Get your")
        return not self.name.startswith(generic_prefixes)


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


def rockbox_package_name(*names: Optional[str]) -> bool:
    """True when a package name marks a Rockbox install (not its 240p build).

    The name contains ``rockbox``; a 240p zip is excluded by its ``_240`` or ``240p`` naming.
    """
    texts = [n.lower() for n in names if n]
    if any("_240" in t or "240p" in t for t in texts):
        return False
    return any("rockbox" in t for t in texts)


def rockbox_240p_package_name(*names: Optional[str]) -> bool:
    """True when the names mark a 240p Rockbox ROM: ``rockbox`` plus a ``240p``/``_240`` tag.

    A release name containing ``240p`` (with Rockbox in the release or ini name and the tag
    in the zip name) is a Rockbox ROM for the small-screen player. It takes the theme-site
    completion note instead of the bundled 360p theme push.
    """
    texts = [n.lower() for n in names if n]
    if not any("rockbox" in t for t in texts):
        return False
    return any("240p" in t or "_240" in t for t in texts)


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
    post_install = get_first("post_install", "post install")
    post_install_2 = get_first("post_install2", "post_install_2", "post install 2")
    do_not_keep_data = get_first("do_not_keep_data", "no_keep_data").lower() in (
        "true", "1", "yes"
    )
    pmt_format_needed = get_first("pmt_format_needed", "format_all").lower() in (
        "true", "1", "yes"
    )
    derivatives_raw = get_first(
        "derivative",
        "derivatives",
        "rom_derivative",
        "rom_derivatives",
        "compatible",
        "compatible_software",
    )
    derivatives = tuple(
        name.strip() for name in re.split(r"[,;|]", derivatives_raw) if name.strip()
    )

    return FwIni(
        enabled=enabled,
        name=name,
        version=version,
        model=model,
        type=sw_type,
        description=desc,
        post_install=post_install,
        post_install_2=post_install_2,
        do_not_keep_data=do_not_keep_data,
        pmt_format_needed=pmt_format_needed,
        derivatives=derivatives,
    )


def package_derivatives(pkg) -> tuple:
    """Firmware names ``pkg``'s ``fw_package.ini`` lists as rom derivatives.

    A derivative entry (``derivative = Original Software, Rockbox``) says which
    firmware shares this package's data partition, so the install can ask the
    user whether that is what they are running before offering Keep Data.
    """
    folder = Path(str(getattr(pkg, "folder", "") or ""))
    ini = folder / FW_PACKAGE_INI
    if not ini.is_file():
        return ()
    return read_fw_package_ini(ini).derivatives


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


def scatter_sort_key(path: Union[str, Path]) -> tuple:
    """Order scatter files by how closely they match the package's own scatter.

    A stock MediaTek release ships exactly one ``*_Android_scatter.txt``. Any
    other scatter beside it is a variant: a
    ``*_Android_scatter_preserve_data.txt`` keeps user data instead of writing
    it, and a bare ``*scatter*.txt`` names no known layout at all. Lower sorts
    first, so the original Android scatter always wins and the rest are
    ignored while it is there.
    """
    stem = Path(str(path)).name.lower()
    if stem.endswith(".txt"):
        stem = stem[:-4]
    variant = any(tag in stem for tag in ("preserve", "keep_data", "keepdata", "nodata"))
    if variant:
        return (1, stem)
    if stem.endswith("android_scatter"):
        return (0, stem)
    return (2, stem)


_IGNORED_SCATTER_DIRS = frozenset({
    "__macosx",
    ".git",
    ".idea",
    ".venv",
    ".venv-build",
    ".cache",
    "__pycache__",
    "_internal",
    "vendor",
    "mtkclient",
    "site-packages",
    "tools",
})


def is_ignored_scatter_path(path: Union[str, Path], root: Optional[Union[str, Path]] = None) -> bool:
    """True when ``path`` sits inside an app bundle or runtime asset folder rather than a firmware package."""
    p = Path(path)
    try:
        rel_parts = p.relative_to(Path(root)).parts[:-1] if root is not None else p.parts[:-1]
    except ValueError:
        rel_parts = p.parts[:-1]
    lowered = [part.lower() for part in rel_parts]
    for idx, part in enumerate(lowered):
        if part in _IGNORED_SCATTER_DIRS or part.endswith(".app"):
            return True
        if part == "compat" and idx > 0 and lowered[idx - 1] == "assets":
            return True
    return False


def ranked_scatters(paths) -> list:
    """The best-ranked scatter file(s) among ``paths``.

    Every lower-ranked scatter is ignored while a better one is present, which
    is how a package's own ``*_Android_scatter.txt`` displaces its
    preserve-data copy and any stray ``*scatter*.txt`` for the same model/chipset.
    When candidate scatters belong to two different hardware models or chipsets,
    no auto-selection takes place and all candidates are returned so the ambiguity
    is surfaced to the user.
    """
    items = [Path(p) for p in paths]
    if not items:
        return []

    non_ignored = [p for p in items if not is_ignored_scatter_path(p)]
    if non_ignored:
        items = non_ignored

    if len(items) > 1:
        try:
            from .flash_service import _list_expected_images, _missing_images

            complete = [
                p
                for p in items
                if p.is_file() and _list_expected_images(p) and not _missing_images(p)
            ]
            if complete and len(complete) < len(items):
                items = complete
        except Exception:
            pass

    # Check whether candidate scatters target different hardware models or chipsets
    try:
        from . import catalog
        models = set()
        chipsets = set()
        for p in items:
            if p.is_file():
                plat, proj = catalog.parse_scatter_header(p)
                m = catalog.infer_model_from_scatter(plat, proj)
                if m:
                    models.add(m)
                if plat:
                    chipsets.add(plat.upper())
        if len(models) > 1 or len(chipsets) > 1:
            return items
    except Exception:
        pass

    # Compared on the rank alone: the name breaks a tie for ordering, never
    # for choosing, so two stock scatters from two chips stay ambiguous.
    best = min(scatter_sort_key(p)[0] for p in items)
    return [p for p in items if scatter_sort_key(p)[0] == best]


def pick_scatter(folder: Union[str, Path]) -> Optional[Path]:
    """Return the scatter naming this folder's firmware layout, else ``None``.

    Selection rules:
    1. Only ``*scatter*.txt`` files count, and a lone one is accepted whatever
       it is called: some releases are not strictly named.
    2. With several, the best-ranked name wins and the rest are ignored:
       ``*_Android_scatter.txt``, then its ``preserve_data`` variant, then any
       other scatter naming (see :func:`scatter_sort_key`).
    3. Two files at the same rank are two different packages, so no single
       scatter is chosen and the caller reports the ambiguity.
    """
    try:
        folder_p = Path(folder)
        matches = [
            p for p in folder_p.iterdir()
            if p.is_file() and p.suffix.lower() == ".txt" and "scatter" in p.name.lower()
        ]
    except OSError:
        return None

    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]

    best = ranked_scatters(matches)
    return best[0] if len(best) == 1 else None


def scatter_has_preserve_data_variant(folder: Union[str, Path]) -> bool:
    """True when folder or its subfolders contain a *preserve_data* scatter file.

    Indicates the firmware shipped with a preserve-data variant, and the
    Keep My Data checkbox should default to checked.
    """
    try:
        f = Path(folder)
        if not f.exists():
            return False
        if f.is_file():
            f = f.parent
        return any(
            p.is_file() and p.suffix.lower() == ".txt"
            and "scatter" in p.name.lower()
            and "preserve_data" in p.name.lower()
            for p in f.rglob("*.txt")
        )
    except OSError:
        return False


def scatter_has_userdata(scatter_path: Union[str, Path]) -> bool:
    """True if the scatter file lists a downloadable userdata partition.

    Reads the scatter file and returns True if any partition whose name
    or file name contains "user" or "usr" (case-insensitive) has ``is_download: true``.
    """
    try:
        p = Path(scatter_path)
        if not p.is_file():
            return False
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    lines = text.splitlines()
    blocks: list[list[str]] = []
    current: Optional[list[str]] = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- partition_index:") or stripped.startswith("partition_index:"):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)

    for block in blocks:
        name = ""
        file_name = ""
        is_dl = False
        for line in block:
            stripped = line.strip()
            key, _, value = stripped.partition(":")
            key = key.strip().lstrip("- ").lower()
            value = value.strip().strip('"\'')
            if key == "partition_name" and not name:
                name = value
            elif key == "file_name" and not file_name:
                file_name = value
            elif key == "is_download":
                is_dl = (value.lower() == "true")

        if is_dl and file_name.upper() != "NONE":
            for val in (name, file_name):
                low = val.lower()
                if "user" in low or "usr" in low:
                    return True

    return False


def _package_image(folder: Path, scope=()) -> Optional[Path]:
    """The release's ``updater.png``/``updater.jpg`` in ``folder`` or ``scope``.

    A package that was repacked or browsed as a folder keeps its image
    wherever the publisher left it, so ``scope`` carries the browsed
    package's other candidate folders (its own folder first).
    """
    for candidate_dir in (folder, *scope):
        try:
            by_name = {p.name.lower(): p for p in Path(candidate_dir).iterdir() if p.is_file()}
        except OSError:
            continue
        for name in PACKAGE_ICON_NAMES:
            if name in by_name:
                return by_name[name]
    return None


def _browse_scope(folder: Path, browse_root: Optional[Path]) -> list[Path]:
    """Folders around a scatter folder, bounded by the browsed package root.

    ``fw_package.ini`` and the ``updater`` image may sit beside the scatter, in
    a neighbouring folder of the same package, or one or more levels above it
    (a repacked portable archive keeps them at the top). Everything stays
    inside ``browse_root`` so a browse never reads unrelated files on the
    computer.
    """
    folder_p = Path(folder)
    if browse_root is None:
        return []
    try:
        root = Path(browse_root).resolve()
        current = folder_p.resolve()
    except OSError:
        return []
    if root != current and root not in current.parents:
        return []
    nodes: list[Path] = []
    node = current.parent
    while node != node.parent:
        if node != current and (node == root or root in node.parents):
            nodes.append(node)
        if node == root:
            break
        node = node.parent
    expanded: list[Path] = []
    for node in nodes:
        if node not in expanded:
            expanded.append(node)
        try:
            for child in sorted(node.iterdir()):
                if not child.is_dir() or child == current:
                    continue
                if child.name.startswith((".", "__")) or child.suffix.lower() == ".app":
                    continue
                if child not in expanded:
                    expanded.append(child)
        except OSError:
            continue
    return expanded


def _release_ini(
    folder: Path,
    app_dir: Optional[Path] = None,
    browse_root: Optional[Path] = None,
) -> Optional[Path]:
    """Return the ``fw_package.ini`` describing the release in ``folder``.

    On Windows and Linux the file beside the application executable or AppImage is preferred,
    so a portable app zipped with its firmware can describe the release from its own folder.
    Then the package folder (the SP Flash Tool or Firmware directory), then the folders around
    it inside the browsed package: a sibling folder, or a parent folder that holds it (a
    repacked portable archive keeps ``fw_package.ini`` at its top level). macOS keeps only
    the package-folder lookups when running bundled.
    """
    candidates: list[Path] = []
    if app_dir is not None:
        candidates.append(Path(app_dir) / FW_PACKAGE_INI)
    elif not IS_MAC:
        candidates.append(Path(INSTALL_DIR) / FW_PACKAGE_INI)
    appimage_dir = get_appimage_dir()
    if appimage_dir is not None:
        candidates.append(appimage_dir / FW_PACKAGE_INI)
    candidates.append(Path(folder) / FW_PACKAGE_INI)
    for scope_dir in _browse_scope(folder, browse_root):
        candidates.append(scope_dir / FW_PACKAGE_INI)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


_VERSION_RE = re.compile(r"(?<![\d.])v?(\d+(?:\.\d+)+)(?![\d.])", re.IGNORECASE)
_NAME_VERSION_RE = re.compile(r"^\s*(?P<name>[A-Za-z][A-Za-z ]*?)[\s_.-]+v?(?P<ver>\d+(?:\.\d+)+)")
_ARCHIVE_SUFFIX_RE = re.compile(r"\.(zip|rar|7z|txt|ini)$", re.IGNORECASE)
_GENERIC_NAME_KEYS = {
    "rom", "fw", "firmware", "update", "package", "original", "software", "release",
    "build", "full", "stock", "scatter", "image", "installer", "bin", "zip",
}


def _name_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def infer_release_name(model: str, hints) -> tuple[str, str]:
    """Infer ``(software name, version)`` from file names when ``fw_package.ini`` has no name.

    Release names the catalogue lists for ``model`` (the live ``slidia_manifest.xml`` list,
    or the static table offline) are matched against the hints first. Otherwise a leading
    ``Name 1.2.3`` pattern in a hint is used. Returns ``("", "")`` when nothing is recognised,
    so the caller keeps its generic label.
    """
    from . import catalog

    texts = [_ARCHIVE_SUFFIX_RE.sub("", str(h)) for h in hints if h]
    keys = [_name_key(t) for t in texts]

    name = ""
    version = ""
    if model:
        for cand in catalog.software_names_for_model(model):
            ck = _name_key(cand)
            if len(ck) < 4 or ck in _GENERIC_NAME_KEYS:
                continue
            if any(ck in k for k in keys) and len(ck) > len(_name_key(name)):
                name = cand
        if name:
            for text in texts:
                m = _VERSION_RE.search(text)
                if m:
                    version = m.group(1)
                    break

    if not name:
        for text in texts:
            m = _NAME_VERSION_RE.match(text)
            if not m:
                continue
            cand = m.group("name").strip()
            ck = _name_key(cand)
            if len(ck) >= 3 and ck not in _GENERIC_NAME_KEYS:
                name = cand
                version = m.group("ver")
                break

    return (name, version) if name else ("", "")


def release_install_heading(model: str, hints=()) -> str:
    """Install heading from a release name found in file-name ``hints``.

    Gives ``Get your Y2 ready to install Rockbox 2.5`` when a release name can be inferred, and
    the generic ready-to-install label otherwise.
    """
    from . import catalog

    name, version = infer_release_name(model, tuple(hints))
    software = f"{name} {version}".strip() if name else ""
    return catalog.format_install_heading(software, model)


def package_install_heading(pkg: "FwPackage") -> str:
    """Install heading for a parsed package: its release name when known, else the generic label."""
    from . import catalog

    software = pkg.display_name if pkg.has_release_name else ""
    return catalog.format_install_heading(software, pkg.model)


def read_package(
    folder: Union[str, Path],
    require_ini: bool = True,
    app_dir: Optional[Union[str, Path]] = None,
    source_names: tuple[str, ...] = (),
    browse_root: Optional[Union[str, Path]] = None,
) -> Optional[FwPackage]:
    """Return the package in ``folder`` when it has exactly one scatter and all listed images exist.

    ``fw_package.ini`` is looked up beside the app executable (``app_dir``) first, then in
    ``folder``. It is required when ``require_ini=True`` and optional when ``require_ini=False``.
    """
    from .flash_service import _missing_images

    folder_p = Path(folder)
    if not folder_p.is_dir():
        return None

    scope = _browse_scope(folder_p, Path(browse_root) if browse_root is not None else None)
    ini = _release_ini(
        folder_p,
        Path(app_dir) if app_dir is not None else None,
        browse_root=Path(browse_root) if browse_root is not None else None,
    )
    has_ini = ini is not None
    if has_ini:
        meta = read_fw_package_ini(ini)
        if not meta.enabled:
            return None
    else:
        if require_ini:
            return None
        meta = FwIni(enabled=True)

    scatter = pick_scatter(folder_p)
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
    eff_version = meta.version
    if not eff_name:
        inferred_name, inferred_version = infer_release_name(
            eff_model, (folder_p.name, scatter.name, *source_names)
        )
        eff_name = inferred_name
        eff_version = eff_version or inferred_version
    if not eff_name:
        # Leave the name empty. The caller renders a generic *heading* from the
        # model when it needs one; storing that heading here made completion
        # copy read "Get your Y2 ready to install software has been installed".
        eff_name = ""

    has_rockbox = has_rockbox_marker(folder_p) or rockbox_package_name(
        eff_name, folder_p.name, scatter.name, *source_names
    )
    has_rockbox_240p = rockbox_240p_package_name(
        eff_name, folder_p.name, scatter.name, *source_names
    )
    image = _package_image(folder_p, scope)
    if image is None and ini is not None and ini.parent != folder_p:
        # fw_package.ini beside the app: its updater image may sit there too.
        image = _package_image(ini.parent)
    if image is None:
        appimage_dir = get_appimage_dir()
        if appimage_dir is not None:
            image = _package_image(appimage_dir)

    return FwPackage(
        folder=folder_p.resolve(),
        scatter=scatter.resolve(),
        name=eff_name,
        version=eff_version,
        model=eff_model,
        type=meta.type,
        description=meta.description,
        image=image.resolve() if image else None,
        has_rockbox=has_rockbox,
        has_rockbox_240p=has_rockbox_240p,
        has_ini=has_ini,
        has_preserve_data_scatter=scatter_has_preserve_data_variant(folder_p),
        post_install=meta.post_install,
        post_install_2=meta.post_install_2,
        do_not_keep_data=meta.do_not_keep_data,
        pmt_format_needed=meta.pmt_format_needed,
        derivative=", ".join(meta.derivatives),
    )


def locate_package(
    root: Union[str, Path],
    require_ini: bool = False,
    app_dir: Optional[Union[str, Path]] = None,
    source_names: tuple[str, ...] = (),
) -> Optional[FwPackage]:
    """Find a firmware package under ``root`` (checking root, then subfolders).

    ``app_dir`` and ``source_names`` are passed to :func:`read_package` so the release name
    can come from the app's ``fw_package.ini`` or from the browsed file names.
    """
    root_p = Path(root)
    if not root_p.is_dir():
        return None

    # Check root directly
    pkg = read_package(
        root_p, require_ini=require_ini, app_dir=app_dir,
        source_names=source_names, browse_root=root_p,
    )
    if pkg is not None:
        return pkg

    # Search subfolders with fw_package.ini
    for ini in sorted(root_p.rglob(FW_PACKAGE_INI)):
        if is_ignored_scatter_path(ini, root_p):
            continue
        pkg = read_package(
            ini.parent, require_ini=require_ini, app_dir=app_dir,
            source_names=source_names, browse_root=root_p,
        )
        if pkg is not None:
            return pkg

    if not require_ini:
        # Search subfolders with a single scatter
        for scatter in sorted(root_p.rglob("*.txt")):
            if "scatter" in scatter.name.lower() and not is_ignored_scatter_path(scatter, root_p):
                pkg = read_package(
                    scatter.parent, require_ini=False, app_dir=app_dir,
                    source_names=source_names, browse_root=root_p,
                )
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
    - macOS: A ``Firmware`` or ``SP_Flash_Tool`` folder beside the .app bundle, then the bundle's folder.
    - Linux AppImage: A ``Firmware`` or ``SP_Flash_Tool`` folder in the same folder as the .AppImage,
      or the neighbouring directory itself.
    - Windows & Linux: A ``Firmware`` or ``SP_Flash_Tool`` folder in the same folder as the binary
      (e.g. INSTALL_DIR/Firmware, INSTALL_DIR/SP_Flash_Tool, or next to binary).
      Release details come from ``fw_package.ini`` beside the application executable or AppImage,
      falling back to the one in the package folder.

    Returns an FwPackage if exactly one *scatter*.txt file is found and all declared
    images exist, or None otherwise.
    """
    candidates: list[Path] = []
    base = Path(app_dir).resolve() if app_dir is not None else Path(INSTALL_DIR).resolve()
    appimage_dir = get_appimage_dir()

    if IS_MAC:
        # macOS: a Firmware or SP_Flash_Tool folder beside the .app is the portable layout (the
        # macOS equivalent of Windows/Linux putting ROM files in a copy of the SP
        # Flash Tool directory). Older packs put the firmware beside the bundle
        # directly, and a dev checkout runs from the repo root.
        app_parent = _macos_app_parent(Path(sys.executable))
        if app_parent and app_parent.is_dir():
            candidates.extend(package_subfolders(app_parent))
            candidates.append(app_parent)
        base_app_parent = _macos_app_parent(base)
        if base_app_parent and base_app_parent.is_dir():
            candidates.extend(package_subfolders(base_app_parent))
            candidates.append(base_app_parent)
        candidates.extend(package_subfolders(base))
    else:
        # Linux AppImage: check neighbouring Firmware or SP_Flash_Tool folder beside the AppImage
        if appimage_dir and appimage_dir.is_dir():
            candidates.extend(p.resolve() for p in package_subfolders(appimage_dir))
            candidates.append(appimage_dir.resolve())

        # Windows & Linux: check neighbouring Firmware or SP_Flash_Tool folder beside the binary
        candidates.extend(p.resolve() for p in package_subfolders(base))
        candidates.append(base.resolve())

        # Check SP Flash Tool directory candidates
        sp_found = find_sp_flash_tool()
        if sp_found:
            candidates.append(Path(sp_found).resolve())

        # Dev checkouts and repo tools
        for sub in PACKAGE_SUBFOLDER_NAMES:
            candidates.append((REPO_ROOT / sub).resolve())
            candidates.append((REPO_ROOT / "tools" / sub).resolve())

        candidates.append((REPO_ROOT / "tools" / "windows" / "SP_Flash_Tool_v5.1904_Win").resolve())
        candidates.append((REPO_ROOT / "tools" / "linux" / "SP_Flash_Tool_v5.1904_Linux").resolve())

    seen = set()
    for cand in candidates:
        if not cand or not cand.is_dir():
            continue
        cand_resolved = cand.resolve()
        if cand_resolved in seen:
            continue
        seen.add(cand_resolved)

        pkg = read_package(cand_resolved, require_ini=False, app_dir=base)
        if pkg is not None:
            logger.info("Found bundled firmware package at %s (scatter=%s)", cand_resolved, pkg.scatter.name)
            return pkg

    return None


def find_installed_package(app_dir=None) -> Optional[FwPackage]:
    """Look for a package beside the app executable, then in its parent folder."""
    base = Path(app_dir) if app_dir is not None else Path(INSTALL_DIR)
    folders = [base, base.parent]
    appimage_dir = get_appimage_dir()
    if appimage_dir and appimage_dir.is_dir():
        folders.insert(0, appimage_dir)
    for folder in folders:
        pkg = read_package(folder, require_ini=True, app_dir=base)
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

    # Copy the updater image so the staged scatter still shows the release icon
    if pkg.image is not None and pkg.image.is_file():
        try:
            shutil.copy2(pkg.image, staged_dir / pkg.image.name)
        except Exception as e:
            logger.debug("Could not copy release icon: %s", e)

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
    # Keep fw_package.ini in the package folder and beside the app executable (portable zips)
    for ini in root.rglob(FW_PACKAGE_INI):
        if ini.is_file():
            keep.add(ini.resolve())
            # A portable pack keeps the release's own icon beside its ini, one
            # folder above the scatter; pruning it left the install card blank.
            for name in PACKAGE_ICON_NAMES:
                icon_p = ini.parent / name
                if icon_p.is_file():
                    keep.add(icon_p.resolve())
    if (folder / FW_PACKAGE_INI).is_file():
        keep.add((folder / FW_PACKAGE_INI).resolve())
    if (folder / "manifest.json").is_file():
        keep.add((folder / "manifest.json").resolve())
    from .flash_service import find_preserve_data_scatter
    preserve_scatter = find_preserve_data_scatter(scatter)
    if preserve_scatter is not None and preserve_scatter.is_file():
        keep.add(preserve_scatter.resolve())
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

    # A scatter may name a file in a different case than the package stores it
    # (the player's storage is case-insensitive; a Linux host is not). Deleting
    # that file here is what later surfaced as CACHE_FILE_MISSING mid-flash.
    # Only match keep_names inside the firmware package folder itself so
    # duplicate reference scatters elsewhere (e.g. inside a bundled .app's
    # assets/compat/) are pruned rather than spared.
    keep_names = {path.name.lower() for _name, path in _list_expected_images(scatter)}
    keep_names.add(scatter.name.lower())
    folder_resolved = folder.resolve()

    removed: list[str] = []
    for f in root.rglob("*"):
        if (
            f.is_file()
            and f.resolve() not in keep
            and not (f.parent.resolve() == folder_resolved and f.name.lower() in keep_names)
        ):
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
