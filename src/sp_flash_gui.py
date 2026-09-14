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


def is_sp_flash_gui_supported() -> bool:
    """Return True if the host platform supports running the SP Flash Tool GUI.

    SP Flash Tool is supported on Windows and Linux (x86/x86_64).
    It is not available on macOS (which uses MTKClient exclusively) or ARM Linux.
    """
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
            add(Path(loc) / "Innioasis Updater" / "SP_Flash_Tool")
            add(Path(loc) / "Innioasis Updater")
        add(Path.home() / "AppData" / "Local" / "Innioasis Updater" / "SP_Flash_Tool")
        add(Path.home() / "AppData" / "Local" / "Innioasis Updater")
        add(Path.cwd() / "SP_Flash_Tool")
        add(Path.cwd())
        add(paths.REPO_ROOT / "SP_Flash_Tool")
        add(paths.REPO_ROOT)

    return found


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
                entry = raw.strip()
                if not entry:
                    continue
                if os.path.isabs(entry):
                    p_entry = str(Path(entry).resolve())
                elif sp_dir:
                    p_entry = str((Path(sp_dir) / entry).resolve())
                else:
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
    scatter_history_line = ",".join(history_entries)

    base = (
        f"[LastDAFilePath]\n"
        f"lastDir={da_abs}\n\n"
        f"[RecentOpenFile]\n"
        f"lastDir={scatter_abs}\n"
        f"scatterHistory={scatter_history_line}\n"
        f"authHistory={auth_history}\n"
    )
    if other_sections:
        base += "\n" + "\n\n".join(other_sections) + "\n"
    return base


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

        # 1. Resolve DA file path to absolute path
        da_file = sp_dir / "MTK_AllInOne_DA.bin"
        da_abs = str(da_file.resolve())

        # 2. Resolve scatter_path if explicitly provided
        scatter_file: Optional[Path] = None
        if scatter_path:
            sc_p = Path(scatter_path)
            if sc_p.is_file():
                scatter_file = sc_p.resolve()
            elif not sc_p.is_absolute() and extract_dir and (Path(extract_dir) / sc_p).is_file():
                scatter_file = (Path(extract_dir) / sc_p).resolve()
            elif not sc_p.is_absolute() and (sp_dir / sc_p).is_file():
                scatter_file = (sp_dir / sc_p).resolve()

        # 3. If scatter_file not resolved, check latest package from device_tracking
        if scatter_file is None:
            latest = device_tracking.get_latest_package()
            if latest:
                if latest.get("scatter_path") and Path(latest["scatter_path"]).is_file():
                    scatter_file = Path(latest["scatter_path"]).resolve()
                if not extract_dir and latest.get("extract_dir") and Path(latest["extract_dir"]).is_dir():
                    extract_dir = Path(latest["extract_dir"]).resolve()
                if not model and latest.get("model"):
                    model = latest["model"]

        # 4. If scatter_file still not resolved, check extract_dir if provided
        if scatter_file is None and extract_dir and Path(extract_dir).is_dir():
            from .flash_service import _find_scatter
            found_sc = _find_scatter(Path(extract_dir))
            if found_sc and Path(found_sc).is_file():
                scatter_file = Path(found_sc).resolve()

        # 5. If scatter_file still not resolved, scan downloads_dir() for cached extractions
        if scatter_file is None:
            try:
                from .downloads import downloads_dir
                from .flash_service import _find_scatter
                dd = downloads_dir()
                if dd.is_dir():
                    for item in sorted(dd.iterdir(), key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True):
                        if item.is_dir() and item.name.startswith(".") and item.name.endswith("_extracted"):
                            found_sc = _find_scatter(item)
                            if found_sc and Path(found_sc).is_file():
                                scatter_file = Path(found_sc).resolve()
                                if not extract_dir:
                                    extract_dir = item.resolve()
                                break
            except Exception:
                pass

        # 6. Fallback to model-specific scatter file: MT6582 for Y2, MT6572 for Y1
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

        scatter_abs = str(scatter_file.resolve())

        # Copy scatter file to sp_dir so local relative lookups by SP Flash Tool also succeed
        if scatter_file.is_file() and sp_dir.resolve() != scatter_file.parent.resolve():
            try:
                shutil.copy2(scatter_file, sp_dir / scatter_file.name)
            except Exception as e:
                logger.debug("Could not copy scatter file to %s: %s", sp_dir, e)

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
        history_ini_path.write_text(new_content, encoding="utf-8")
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
        return False, "SP Flash Tool is not available on macOS. macOS uses MTKClient only."

    from . import device_tracking

    # Resolve latest package if not passed explicitly
    if not scatter_path or not Path(scatter_path).is_file():
        latest = device_tracking.get_latest_package()
        if latest:
            if latest.get("scatter_path") and Path(latest["scatter_path"]).is_file():
                scatter_path = Path(latest["scatter_path"])
            if not extract_dir and latest.get("extract_dir") and Path(latest["extract_dir"]).is_dir():
                extract_dir = Path(latest["extract_dir"])
            if not model and latest.get("model"):
                model = latest["model"]

    if platform.system() == "Linux":
        from . import linux_sp_flash

        if not linux_sp_flash.arch_supported():
            return False, linux_sp_flash.unsupported_reason()

        ok, msg = linux_sp_flash.ensure_linux_sp_flash_tool()
        if not ok:
            return False, f"Could not prepare SP Flash Tool: {msg}"

        dirs = find_sp_flash_tool_dirs()
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

        # Update history.ini in all candidate directories
        update_sp_history_ini(
            sp_dir=None,
            scatter_path=scatter_path,
            extract_dir=extract_dir,
            model=model,
        )

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
