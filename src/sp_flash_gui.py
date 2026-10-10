"""MediaTek SP Flash Tool GUI launcher for supported platforms (Windows + Linux x86/x86_64)."""

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple, Union

from . import paths

logger = logging.getLogger(__name__)

HISTORY_INI = "history.ini"
DA_FILENAME = "MTK_AllInOne_DA.bin"


def _user_sp_copy_dir() -> Path:
    """Per-user folder used when the installed SP Flash Tool cannot be written."""
    if paths.IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "Updater CE" / "SP_Flash_Tool"
    if paths.IS_MAC:
        return Path.home() / "Library" / "Application Support" / "Updater CE" / "SP_Flash_Tool"
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return Path(base) / "updater-ce" / "SP_Flash_Tool"


def _dir_is_writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / ".updater_write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _tool_binary_name() -> str:
    return "flash_tool.exe" if paths.IS_WINDOWS else "flash_tool"


def ensure_launch_dir(source: Path) -> Path:
    """Return a tool directory the current account can write ``history.ini`` into.

    A system-wide install keeps SP Flash Tool under Program Files, and an
    AppImage keeps it on a read-only mount. When that folder is not writable,
    the tree is copied once into the per-user data directory and that copy is
    launched, so ``history.ini`` still sits beside the binary.
    """
    source = Path(source)
    if _dir_is_writable(source):
        return source
    dest = _user_sp_copy_dir()
    try:
        dest.mkdir(parents=True, exist_ok=True)
        if not (dest / _tool_binary_name()).is_file():
            shutil.copytree(source, dest, dirs_exist_ok=True)
    except OSError:
        logger.warning("Could not copy SP Flash Tool to %s", dest, exc_info=True)
        return source
    if _dir_is_writable(dest):
        logger.info("Using writable SP Flash Tool copy at %s", dest)
        return dest
    return source


def is_sp_flash_gui_supported() -> bool:
    """Return True if the host platform supports running the SP Flash Tool GUI.

    SP Flash Tool is supported on Windows and Linux (x86/x86_64).
    It is not available on macOS (which uses MTKClient exclusively) or ARM Linux.
    Simulated macOS mode (--simulate-macos) reports False as well so the host
    behaves exactly like a Mac.
    """
    if paths.SIMULATE_MACOS:
        return False
    if paths.IS_MAC:
        return False
    if paths.IS_WINDOWS:
        return True
    if platform.system() == "Linux":
        from . import linux_sp_flash
        return linux_sp_flash.arch_supported()
    return False


def find_sp_flash_tool_dirs() -> List[Path]:
    """Return all directories containing an SP Flash Tool binary on this system."""
    if paths.IS_MAC:
        return []
    found: List[Path] = []
    seen = set()

    def add(p):
        if not p:
            return
        try:
            path = Path(p).resolve()
            if path.is_dir() and str(path) not in seen:
                if (path / "flash_tool").is_file() or (path / "flash_tool.exe").is_file():
                    found.append(path)
                    seen.add(str(path))
        except Exception:
            pass

    # Explicit environment variable
    if os.environ.get("SP_FLASH_TOOL_DIR"):
        add(Path(os.environ["SP_FLASH_TOOL_DIR"]))

    try:
        from . import tools_manager
        comp_id = "sp_flash_tool_win" if paths.IS_WINDOWS else "sp_flash_tool_linux"
        add(tools_manager.find_component(comp_id))
    except Exception:
        pass

    if platform.system() == "Linux":
        from . import linux_sp_flash
        add(linux_sp_flash.stage_dir())
        add(Path.home() / ".local" / "share" / "innioasis-updater")
        add(paths.INSTALL_DIR / "SP_Flash_Tool")
        add(paths.INSTALL_DIR)
        add(Path.cwd() / "SP_Flash_Tool")
        add(Path.cwd())
    elif paths.IS_WINDOWS:
        add(paths.find_sp_flash_tool())
        add(paths.INSTALL_DIR / "SP_Flash_Tool")
        add(paths.INSTALL_DIR)
        loc = os.environ.get("LOCALAPPDATA")
        if loc:
            add(Path(loc) / "Updater CE" / "SP_Flash_Tool")
            add(Path(loc) / "Updater CE")
            add(Path(loc) / "Innioasis Updater" / "SP_Flash_Tool")
            add(Path(loc) / "Innioasis Updater")
        add(Path.home() / "AppData" / "Local" / "Updater CE" / "SP_Flash_Tool")
        add(Path.home() / "AppData" / "Local" / "Updater CE")
        add(Path.home() / "AppData" / "Local" / "Innioasis Updater" / "SP_Flash_Tool")
        add(Path.home() / "AppData" / "Local" / "Innioasis Updater")
        add(Path.cwd() / "SP_Flash_Tool")
        add(Path.cwd())
        add(paths.REPO_ROOT / "SP_Flash_Tool")
        add(paths.REPO_ROOT)

    return found


def _ini_escape(value: str) -> str:
    """Escape a path the way Qt's INI reader expects it.

    SP Flash Tool reads ``history.ini`` with QSettings. A single backslash
    starts an escape, so ``C:\\Users`` is read as ``C:Users`` and the scatter
    file "cannot be found". Doubling the backslash keeps the real path.
    """
    return (value or "").replace("\\", "\\\\")


def _ini_unescape(value: str) -> str:
    """Undo one level of INI backslash escaping. Single-backslash files stay intact."""
    text = value or ""
    out: List[str] = []
    index = 0
    while index < len(text):
        if text[index] == "\\" and index + 1 < len(text) and text[index + 1] == "\\":
            out.append("\\")
            index += 2
            continue
        out.append(text[index])
        index += 1
    return "".join(out)


def read_history_paths(history_file: Union[Path, str]) -> Tuple[str, str, str]:
    """Paths SP Flash Tool will actually load from ``history.ini``."""
    from PySide6.QtCore import QSettings

    settings = QSettings(str(history_file), QSettings.Format.IniFormat)
    settings.sync()
    da = str(settings.value("LastDAFilePath/lastDir") or "")
    scatter = str(settings.value("RecentOpenFile/lastDir") or "")
    history = settings.value("RecentOpenFile/scatterHistory")
    if isinstance(history, (list, tuple)):
        history_text = ",".join(str(item) for item in history)
    else:
        history_text = str(history or "")
    return da, scatter, history_text


def format_sp_history_ini(
    existing_text: str,
    da_path: str,
    scatter_path: str,
    sp_dir: Optional[Path] = None,
) -> str:
    """Generate or update history.ini ensuring valid absolute paths and correct INI syntax.

    Both LastDAFilePath.lastDir and RecentOpenFile.lastDir as well as scatterHistory
    entries are guaranteed to be absolute paths.
    """
    scatter_abs = str(Path(scatter_path).resolve())
    da_abs = str(Path(da_path).resolve())

    history_entries = [scatter_abs]
    other_sections = []
    auth_history = ""

    if existing_text:
        # Pre-clean any concatenated section headers like '[RecentOpenFile]lastDir=...'
        cleaned_text = re.sub(
            r"\[RecentOpenFile\]\s*(lastDir\s*=)", r"[RecentOpenFile]\n\1", existing_text
        )
        cleaned_text = re.sub(
            r"\[LastDAFilePath\]\s*(lastDir\s*=)", r"[LastDAFilePath]\n\1", cleaned_text
        )

        m = re.search(r"(?m)^\s*scatterHistory\s*=\s*(.*)$", cleaned_text)
        if m:
            for raw in m.group(1).split(","):
                entry = _ini_unescape(raw.strip())
                if not entry or entry.startswith("@Invalid"):
                    continue
                if os.path.isabs(entry):
                    p_entry = str(Path(entry).resolve())
                elif sp_dir:
                    p_entry = str((Path(sp_dir) / entry).resolve())
                else:
                    continue
                # A scatter copied into the tool folder has no package images
                # beside it. Keep only files that are still on disk.
                if not Path(p_entry).is_file():
                    continue
                # Drop a scatter that was copied into the tool folder once the
                # package extract itself is the file being opened.
                if (
                    sp_dir
                    and Path(scatter_abs).parent.resolve() != Path(sp_dir).resolve()
                    and Path(p_entry).parent.resolve() == Path(sp_dir).resolve()
                ):
                    continue
                if p_entry not in history_entries:
                    history_entries.append(p_entry)

        m_auth = re.search(r"(?m)^\s*authHistory\s*=\s*(.*)$", cleaned_text)
        if m_auth:
            auth_history = m_auth.group(1).strip()

        # Check for any extra sections outside LastDAFilePath and RecentOpenFile
        sections = re.findall(r"(?m)^(\[[^\]]+\])", cleaned_text)
        for s in sections:
            s_name = s.strip("[] \t\r\n")
            if s_name not in ("LastDAFilePath", "RecentOpenFile"):
                pat = rf"(?m)^\[{re.escape(s_name)}\].*?(?=(?:^\[|\Z))"
                sec_match = re.search(pat, cleaned_text, re.DOTALL)
                if sec_match:
                    other_sections.append(sec_match.group(0).strip())

    history_entries = history_entries[:10]
    scatter_history_line = ",".join(_ini_escape(entry) for entry in history_entries)

    base = (
        f"[LastDAFilePath]\n"
        f"lastDir={_ini_escape(da_abs)}\n\n"
        f"[RecentOpenFile]\n"
        f"lastDir={_ini_escape(scatter_abs)}\n"
        f"scatterHistory={scatter_history_line}\n"
        f"authHistory={auth_history}\n"
    )
    if other_sections:
        base += "\n" + "\n\n".join(other_sections) + "\n"
    return base


def validate_scatter_images(
    scatter_file: Union[Path, str],
    extract_dir: Optional[Union[Path, str]] = None,
) -> Tuple[bool, List[str], List[str]]:
    """Validate that images referenced in a scatter file actually exist.

    Returns:
        (is_valid: bool, found_images: List[str], missing_images: List[str])
    """
    sc_path = Path(scatter_file).resolve()
    if not sc_path.is_file():
        return False, [], [str(sc_path)]

    base_dir = sc_path.parent
    ext_dir = Path(extract_dir).resolve() if extract_dir else base_dir

    try:
        content = sc_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        logger.warning("Could not read scatter file %s: %s", sc_path, e)
        return False, [], [str(sc_path)]

    found_images: List[str] = []
    missing_images: List[str] = []

    # Look for file_name: <filename> in MediaTek scatter files
    pattern = re.compile(r"(?i)^\s*file_name:\s*(\S+)", re.MULTILINE)
    matches = pattern.findall(content)

    for item in matches:
        item = item.strip()
        if not item or item.upper() == "NONE":
            continue
        p1 = base_dir / item
        p2 = ext_dir / item
        if p1.is_file() or p2.is_file():
            found_images.append(item)
        else:
            missing_images.append(item)

    if missing_images:
        return False, found_images, missing_images

    # If no partition image references were found in scatter, check if any .img or .bin files exist
    if not found_images:
        images_in_dir = [
            f.name
            for f in base_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".img", ".bin") and f.name != "MTK_AllInOne_DA.bin"
        ]
        if not images_in_dir and ext_dir != base_dir and ext_dir.is_dir():
            images_in_dir = [
                f.name
                for f in ext_dir.iterdir()
                if f.is_file() and f.suffix.lower() in (".img", ".bin") and f.name != "MTK_AllInOne_DA.bin"
            ]
        if not images_in_dir:
            return False, [], ["(no partition images found)"]
        return True, images_in_dir, []

    return True, found_images, []


def resolve_cached_firmware(
    scatter_path: Optional[Union[Path, str]] = None,
    extract_dir: Optional[Union[Path, str]] = None,
    model: str = "",
) -> Tuple[Optional[Path], Optional[Path], str]:
    """Locate the scatter file and extract directory for a cached/downloaded firmware package."""
    from . import device_tracking

    resolved_scatter: Optional[Path] = None
    resolved_extract: Optional[Path] = None

    if scatter_path:
        sc_p = Path(scatter_path)
        if sc_p.is_file():
            resolved_scatter = sc_p.resolve()
        elif extract_dir and (Path(extract_dir) / sc_p).is_file():
            resolved_scatter = (Path(extract_dir) / sc_p).resolve()

    if extract_dir and Path(extract_dir).is_dir():
        resolved_extract = Path(extract_dir).resolve()

    if resolved_scatter is None:
        latest = device_tracking.get_latest_package()
        if latest:
            if latest.get("scatter_path") and Path(latest["scatter_path"]).is_file():
                resolved_scatter = Path(latest["scatter_path"]).resolve()
            if not resolved_extract and latest.get("extract_dir") and Path(latest["extract_dir"]).is_dir():
                resolved_extract = Path(latest["extract_dir"]).resolve()
            if not model and latest.get("model"):
                model = latest["model"]

    if resolved_scatter is None and resolved_extract and resolved_extract.is_dir():
        from .flash_service import _find_scatter
        found_sc = _find_scatter(resolved_extract)
        if found_sc and Path(found_sc).is_file():
            resolved_scatter = Path(found_sc).resolve()

    if resolved_scatter is None:
        try:
            from .downloads import downloads_dir
            from .flash_service import _find_scatter
            dd = downloads_dir()
            if dd.is_dir():
                for item in sorted(dd.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                    if item.is_dir() and item.name.startswith(".") and item.name.endswith("_extracted"):
                        found_sc = _find_scatter(item)
                        if found_sc and Path(found_sc).is_file():
                            resolved_scatter = Path(found_sc).resolve()
                            if not resolved_extract:
                                resolved_extract = item.resolve()
                            break
        except Exception:
            pass

    if resolved_scatter and not resolved_extract:
        resolved_extract = resolved_scatter.parent

    return resolved_scatter, resolved_extract, model


def choose_sp_gui_package(explicit, cached, focused):
    """Firmware to open in the desktop tool.

    Order: a release the user clicked, or a zip/rar/scatter they browsed to;
    otherwise the newest extract still in the working folder; otherwise the
    release focused on screen (usually the latest Original Software).
    """
    if explicit:
        return explicit
    if cached:
        return cached
    if focused:
        return focused
    return None


def is_gui_local_package(path) -> bool:
    """True for a zip, rar, scatter text file, or a folder that contains one."""
    if not path:
        return False
    candidate = Path(path)
    try:
        if candidate.is_file():
            name = candidate.name.lower()
            if candidate.suffix.lower() in (".zip", ".rar"):
                return True
            return "scatter" in name and name.endswith(".txt")
        if candidate.is_dir():
            from .flash_service import find_scatter_files

            return bool(find_scatter_files(candidate))
    except OSError:
        return False
    return False


def cached_install_firmware(latest: Optional[dict]) -> Tuple[Optional[Path], Optional[Path]]:
    """Extracted firmware still in the working folder, if those files are on disk.

    Used when nothing was clicked and nothing was browsed: the cache is opened
    as-is, without downloading it again. Failed and successful installs are
    both recorded the same way, so either can be opened again.
    """
    if not latest:
        return None, None
    scatter_raw = latest.get("scatter_path") or ""
    extract_raw = latest.get("extract_dir") or ""
    scatter = Path(scatter_raw) if scatter_raw else None
    extract = Path(extract_raw) if extract_raw else None
    if scatter is not None and scatter.is_file():
        if extract is None or not extract.is_dir():
            extract = scatter.parent
        return scatter.resolve(), extract.resolve()
    if extract is not None and extract.is_dir():
        from .flash_service import _find_scatter

        found = _find_scatter(extract, allow_raise=False)
        if found and Path(found).is_file():
            return Path(found).resolve(), extract.resolve()
    return None, None


def pin_sp_flash_history(
    sp_dir: Union[Path, str],
    scatter_path: Union[Path, str],
) -> Tuple[bool, str, str]:
    """Write history.ini beside the SP Flash Tool binary.

    ``RecentOpenFile.lastDir`` is the absolute path of the extracted scatter
    on the user's computer (images live next to it). ``LastDAFilePath.lastDir``
    is ``MTK_AllInOne_DA.bin`` inside ``sp_dir``. Returns ``(ok, da_abs, scatter_abs)``.
    """
    tool_dir = Path(sp_dir)
    scatter = Path(scatter_path)
    if not scatter.is_file() or not tool_dir.is_dir():
        return False, "", ""
    scatter_abs = str(scatter.resolve())
    da_file = _ensure_da_payload(tool_dir)
    if not da_file.is_file():
        return False, "", scatter_abs
    da_abs = str(da_file.resolve())
    # The scatter entry must stay the extracted file, not a copy dropped into
    # the tool directory. A copy has no partition images beside it, which is
    # the "scatter not found / images missing" open.
    wrote = update_sp_history_ini(
        sp_dir=tool_dir,
        scatter_path=scatter_abs,
        extract_dir=str(scatter.resolve().parent),
    )
    if not wrote:
        return False, da_abs, scatter_abs
    history = tool_dir / HISTORY_INI
    if not history.is_file():
        return False, da_abs, scatter_abs
    read_da, read_scatter, _history = read_history_paths(history)
    if os.path.normcase(read_da) != os.path.normcase(da_abs):
        return False, da_abs, scatter_abs
    if os.path.normcase(read_scatter) != os.path.normcase(scatter_abs):
        return False, da_abs, scatter_abs
    return True, da_abs, scatter_abs


def stage_history_before_launch(
    sp_dir: Union[Path, str],
    scatter_path: Union[Path, str],
) -> Tuple[bool, str]:
    """Write history.ini beside the tool binary, then confirm both files exist.

    The GUI is not started unless the extracted scatter and this folder's
    ``MTK_AllInOne_DA.bin`` are real files and both absolute paths are in
    ``history.ini``.
    """
    tool_dir = Path(sp_dir)
    scatter = Path(scatter_path) if scatter_path else None
    if scatter is None or not scatter.is_file() or not tool_dir.is_dir():
        return False, "The extracted scatter file is missing."
    ok, da_abs, scatter_abs = pin_sp_flash_history(tool_dir, scatter)
    if not ok or not da_abs or not scatter_abs:
        return False, (
            "Could not write history.ini with the extracted scatter and "
            "MTK_AllInOne_DA.bin."
        )
    if not os.path.isabs(da_abs) or not os.path.isabs(scatter_abs):
        return False, "history.ini paths must be absolute."
    da_file = Path(da_abs)
    scatter_file = Path(scatter_abs)
    if (
        not da_file.is_file()
        or not scatter_file.is_file()
        or da_file.name != DA_FILENAME
        or da_file.parent.resolve() != tool_dir.resolve()
    ):
        return False, "The scatter file or MTK_AllInOne_DA.bin is missing."
    if not (tool_dir / HISTORY_INI).is_file():
        return False, "history.ini could not be read back."
    read_da, read_scatter, _history = read_history_paths(tool_dir / HISTORY_INI)
    if os.path.normcase(read_da) != os.path.normcase(da_abs):
        return False, "history.ini does not list the download agent."
    if os.path.normcase(read_scatter) != os.path.normcase(scatter_abs):
        return False, "history.ini does not list the scatter file."
    return True, ""


def check_cached_firmware_readiness(
    scatter_path: Optional[Union[Path, str]] = None,
    extract_dir: Optional[Union[Path, str]] = None,
    model: str = "",
) -> Tuple[bool, Optional[Path], Optional[Path], str]:
    """Check whether a cached firmware package exists with a valid scatter file and all images.

    Returns:
        (ready: bool, scatter_file: Optional[Path], extract_dir: Optional[Path], reason: str)
    """
    sc_file, ext_dir, model = resolve_cached_firmware(
        scatter_path=scatter_path,
        extract_dir=extract_dir,
        model=model,
    )
    if not sc_file or not sc_file.is_file():
        return False, None, None, "No cached firmware package found from a previous firmware install attempt."

    is_valid, found_imgs, missing_imgs = validate_scatter_images(sc_file, ext_dir)
    if not is_valid:
        missing_str = ", ".join(missing_imgs[:5])
        return False, sc_file, ext_dir, f"Images referenced in scatter file were not found: {missing_str}"

    return True, sc_file, ext_dir, "Firmware package is ready."


def _da_source_dirs() -> List[Path]:
    """Directories that may hold a download agent we are allowed to copy in.

    Ordered most specific first and always platform first: each desktop stages
    the payload from its own tool tree, so a Windows build never borrows the
    Linux file (or the other way round). The trailing entries exist on every
    platform, including macOS where the SP Flash Tool GUI itself does not run.
    """
    dirs: List[Path] = [
        paths.SP_FLASH_TOOL_DIR,
        paths.COMPAT_DIR,
    ]
    tools = paths.REPO_ROOT / "tools"
    if paths.IS_WINDOWS:
        dirs.append(tools / "windows" / "SP_Flash_Tool_v5.1904_Win")
    elif not paths.IS_MAC:
        dirs.append(tools / "linux" / "SP_Flash_Tool_v5.1904_Linux")
    dirs.append(tools / "SP_Flash_Tool")
    # mtkclient ships with the app everywhere; its loader directory carries
    # the per-hardware-code agents as the platform-independent fallback.
    dirs.append(paths.MTKCLIENT_DIR / "mtkclient" / "Loader")
    return dirs


def _da_source_files() -> List[Path]:
    """Concrete DA files that could be staged, best candidate first."""
    files: List[Path] = []
    for directory in _da_source_dirs():
        try:
            exact = directory / DA_FILENAME
            if exact.is_file():
                files.append(exact)
                continue
            # mtkclient names its agents after the hardware code
            # (MTK_AllInOne_DA_7687.bin); its highest code is the newest.
            numbered = [
                p for p in directory.glob("MTK_AllInOne_DA_*.bin")
                if p.stem.rsplit("_", 1)[-1].isdigit()
            ]
            files.extend(sorted(numbered, key=lambda p: int(p.stem.rsplit("_", 1)[-1]), reverse=True))
        except OSError:
            continue
    return files


def _ensure_da_payload(sp_dir: Path) -> Path:
    """Return ``sp_dir/MTK_AllInOne_DA.bin``, staging our bundled copy if absent.

    SP Flash Tool takes the download-agent path from ``history.ini``. A staged
    or updated tool directory (or one supplied by the user) may not carry the
    DA yet, which would leave the GUI with an empty agent field; copy the copy
    the app ships so the GUI opens populated. macOS never reaches this (the
    SP Flash Tool GUI is unsupported there) but the same code keeps the
    Linux/Windows payloads separate.
    """
    target = sp_dir / DA_FILENAME
    if target.is_file():
        return target
    for src in _da_source_files():
        try:
            shutil.copy2(src, target)
            logger.info("Staged DA payload into %s from %s", target, src)
            return target
        except Exception as e:
            logger.debug("Could not stage DA payload from %s: %s", src, e)
    return target


def update_sp_history_ini(
    sp_dir: Optional[Union[Path, str]] = None,
    scatter_path: Optional[Union[Path, str]] = None,
    extract_dir: Optional[Union[Path, str]] = None,
    model: str = "",
) -> bool:
    """Update or create history.ini in SP Flash Tool directories.

    Prepopulates scatterHistory, lastDir, and LastDAFilePath with absolute paths
    of the scatter file and partition images for the most recently attempted or
    downloaded firmware package in Updater Neo.

    If sp_dir is None, updates all detected SP Flash Tool binary directories.
    """
    from . import device_tracking

    if sp_dir is None and paths.IS_MAC:
        return False

    if sp_dir is None:
        dirs = find_sp_flash_tool_dirs()
        if not dirs and platform.system() == "Linux":
            from . import linux_sp_flash
            dirs = [linux_sp_flash.stage_dir()]
        elif not dirs and paths.IS_WINDOWS:
            found = paths.find_sp_flash_tool()
            if found:
                dirs = [found]
        ok_any = False
        for d in dirs:
            if update_sp_history_ini(d, scatter_path=scatter_path, extract_dir=extract_dir, model=model):
                ok_any = True
        return ok_any

    try:
        sp_dir = Path(sp_dir).resolve()
        if not sp_dir.is_dir():
            return False

        # 1. Resolve DA file path to absolute path conforming to OS conventions.
        # The GUI reads this from history.ini, so make sure the file exists in
        # the tool directory even when the directory did not ship with it.
        da_file = _ensure_da_payload(sp_dir)
        da_abs = os.path.abspath(str(da_file))

        # 2. Resolve cached firmware scatter & extract
        scatter_file, resolved_ext, resolved_model = resolve_cached_firmware(
            scatter_path=scatter_path,
            extract_dir=extract_dir,
            model=model,
        )
        if resolved_model and not model:
            model = resolved_model
        if resolved_ext and not extract_dir:
            extract_dir = resolved_ext

        # 3. Keep a scatter the tool can already open. Do not replace it with
        # the bare file that sits in the tool folder.
        if scatter_file is None:
            history_ini_path = sp_dir / HISTORY_INI
            if history_ini_path.is_file():
                _da, existing_scatter, _hist = read_history_paths(history_ini_path)
                existing_path = Path(existing_scatter) if existing_scatter else None
                if (
                    existing_path is not None
                    and existing_path.is_file()
                    and existing_path.parent.resolve() != sp_dir.resolve()
                ):
                    scatter_file = existing_path.resolve()

        # 4. Fallback to model-specific scatter file: MT6582 for Y2, MT6572 for Y1
        if scatter_file is None:
            default_scatter_name = (
                "MT6582_Android_scatter.txt"
                if "Y2" in (model or "").upper()
                else "MT6572_Android_scatter.txt"
            )
            cand_sp = sp_dir / default_scatter_name
            if cand_sp.is_file():
                scatter_file = cand_sp.resolve()
            else:
                compat_sc = paths.COMPAT_DIR / default_scatter_name
                if compat_sc.is_file():
                    try:
                        shutil.copy2(compat_sc, cand_sp)
                        scatter_file = cand_sp.resolve()
                    except Exception:
                        scatter_file = compat_sc.resolve()
                else:
                    scatter_file = cand_sp.resolve()

        scatter_abs = os.path.abspath(str(scatter_file))
        if not Path(scatter_abs).is_file():
            return False
        # The scatter must stay in the extracted package. A copy inside the
        # tool folder has no partition images beside it, and that is the
        # "scatter file cannot find" dialog.

        # Write or update history.ini
        history_ini_path = sp_dir / HISTORY_INI
        existing_text = ""
        if history_ini_path.is_file():
            existing_text = history_ini_path.read_text(encoding="utf-8", errors="replace")

        new_content = format_sp_history_ini(
            existing_text=existing_text,
            da_path=da_abs,
            scatter_path=scatter_abs,
            sp_dir=sp_dir,
        )
        if existing_text.replace("\r\n", "\n").strip() == new_content.replace("\r\n", "\n").strip():
            return True
        history_ini_path.write_text(new_content, encoding="utf-8")
        try:
            from .diagnostics import guard_broken_stream_handlers

            guard_broken_stream_handlers()
        except Exception:
            pass
        logger.info("Updated %s with absolute scatter: %s", history_ini_path, scatter_abs)
        return True
    except Exception as e:
        logger.warning("Could not update %s in %s: %s", HISTORY_INI, sp_dir, e)
        return False


def launch_sp_flash_tool_gui(
    model: str = "",
    scatter_path: Optional[Path] = None,
    extract_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Launch MediaTek SP Flash Tool in GUI mode on Windows or Linux.

    Pre-populates history.ini with the scatter file and image paths for the
    most recently downloaded / attempted software package.

    Returns:
        (success: bool, message: str)
    """
    if paths.IS_MAC:
        if paths.SIMULATE_MACOS:
            return (
                False,
                "Simulated macOS mode (--simulate-macos): MTKClient is the only "
                "flash backend, matching a real macOS build.",
            )
        return False, "SP Flash Tool is not available on macOS. macOS uses MTKClient only."

    if scatter_path and not Path(scatter_path).is_file():
        return False, "The extracted scatter file is missing."

    ready, sc_file, ext_dir, reason = check_cached_firmware_readiness(
        scatter_path=scatter_path,
        extract_dir=extract_dir,
        model=model,
    )
    if not ready:
        return False, reason

    scatter_path = sc_file
    extract_dir = ext_dir

    if platform.system() == "Linux":
        from . import linux_sp_flash

        if not linux_sp_flash.arch_supported():
            return False, linux_sp_flash.unsupported_reason()

        ok, msg = linux_sp_flash.ensure_linux_sp_flash_tool()
        if not ok:
            return False, f"Could not prepare SP Flash Tool: {msg}"

        dirs = find_sp_flash_tool_dirs()
        # Bundled payload (shipped inside the AppImage / frozen bundle) wins
        # over any previously downloaded copy.
        bundled = linux_sp_flash.bundled_dir()
        if bundled is not None and (bundled / linux_sp_flash.FLASH_TOOL_LINUX_BIN).is_file():
            if bundled not in dirs:
                dirs.insert(0, bundled)
        stage = linux_sp_flash.stage_dir()
        if stage not in dirs and (stage / linux_sp_flash.FLASH_TOOL_LINUX_BIN).is_file():
            dirs.insert(0, stage)

        if not dirs:
            return False, "SP Flash Tool directory not found on Linux."

        chosen_dir = dirs[0]
        bin_path = chosen_dir / linux_sp_flash.FLASH_TOOL_LINUX_BIN
        if not bin_path.is_file():
            for d in dirs:
                candidate = d / linux_sp_flash.FLASH_TOOL_LINUX_BIN
                if candidate.is_file():
                    bin_path = candidate
                    chosen_dir = d
                    break

        if not bin_path.is_file():
            return False, f"SP Flash Tool binary not found at {bin_path}"

        chosen_dir = ensure_launch_dir(chosen_dir)
        bin_path = chosen_dir / linux_sp_flash.FLASH_TOOL_LINUX_BIN

        # Update history.ini in ALL candidate directories
        update_sp_history_ini(
            sp_dir=None,
            scatter_path=scatter_path,
            extract_dir=extract_dir,
            model=model,
        )

        try:
            bin_path.chmod(bin_path.stat().st_mode | 0o755)
        except Exception:
            pass

        # IMPORTANT: Run the binary directly with process_env(chosen_dir) instead of
        # flash_tool.sh. flash_tool.sh resets LD_LIBRARY_PATH and causes a segmentation fault.
        env = linux_sp_flash.process_env(chosen_dir)
        ready_history, history_msg = stage_history_before_launch(chosen_dir, scatter_path)
        if not ready_history:
            return False, history_msg
        try:
            subprocess.Popen(
                [str(bin_path)],
                cwd=str(chosen_dir),
                env=env,
                start_new_session=True,
            )
            return True, "SP Flash Tool GUI launched successfully."
        except Exception as e:
            logger.exception("Failed to launch SP Flash Tool GUI on Linux")
            return False, str(e)

    elif paths.IS_WINDOWS:
        dirs = find_sp_flash_tool_dirs()
        if not dirs:
            return False, "SP Flash Tool directory not found on Windows."

        chosen_dir = dirs[0]
        flash_tool_exe = chosen_dir / "flash_tool.exe"
        if not flash_tool_exe.is_file():
            for d in dirs:
                candidate = d / "flash_tool.exe"
                if candidate.is_file():
                    flash_tool_exe = candidate
                    chosen_dir = d
                    break

        if not flash_tool_exe.is_file():
            return False, f"flash_tool.exe not found at {flash_tool_exe}"

        chosen_dir = ensure_launch_dir(chosen_dir)
        flash_tool_exe = chosen_dir / "flash_tool.exe"

        # Update history.ini in all candidate directories
        update_sp_history_ini(
            sp_dir=None,
            scatter_path=scatter_path,
            extract_dir=extract_dir,
            model=model,
        )

        ready_history, history_msg = stage_history_before_launch(chosen_dir, scatter_path)
        if not ready_history:
            return False, history_msg
        try:
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(
                [str(flash_tool_exe)],
                cwd=str(chosen_dir),
                env=os.environ.copy(),
                creationflags=creationflags,
            )
            return True, "SP Flash Tool GUI launched successfully."
        except Exception as e:
            logger.exception("Failed to launch flash_tool.exe on Windows")
            return False, str(e)

    return False, f"SP Flash Tool is not supported on {platform.system()}."


def open_sp_flash_tool_gui(
    model: str = "",
    scatter_path: Optional[Path] = None,
    extract_dir: Optional[Path] = None,
) -> Tuple[bool, str]:
    """Open SP Flash Tool GUI with the current or cached firmware."""
    return launch_sp_flash_tool_gui(
        model=model,
        scatter_path=scatter_path,
        extract_dir=extract_dir,
    )
