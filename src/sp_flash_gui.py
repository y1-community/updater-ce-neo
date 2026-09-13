"""MediaTek SP Flash Tool GUI launcher for supported platforms (Windows + Linux x86/x86_64)."""

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

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


def update_sp_history_ini(
    sp_dir: Optional[Path] = None,
    scatter_path: Optional[Path] = None,
    extract_dir: Optional[Path] = None,
    model: str = "",
) -> bool:
    """Update or create history.ini in SP Flash Tool directories.

    Prepopulates scatterHistory and lastDir with the exact paths of the
    scatter file and partition images for the most recently downloaded or
    attempted software package in Updater Neo.

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
        sp_dir = Path(sp_dir)
        if not sp_dir.is_dir():
            return False

        # If scatter_path not explicitly provided, query device_tracking latest package
        if not scatter_path or not Path(scatter_path).is_file():
            latest = device_tracking.get_latest_package()
            if latest:
                if latest.get("scatter_path") and Path(latest["scatter_path"]).is_file():
                    scatter_path = Path(latest["scatter_path"])
                if not extract_dir and latest.get("extract_dir") and Path(latest["extract_dir"]).is_dir():
                    extract_dir = Path(latest["extract_dir"])
                if not model and latest.get("model"):
                    model = latest["model"]

        scatter_name = ""
        scatter_dir = ""
        if scatter_path and Path(scatter_path).is_file():
            scatter_file = Path(scatter_path).resolve()
            scatter_name = str(scatter_file)
            scatter_dir = (
                str(Path(extract_dir).resolve())
                if extract_dir and Path(extract_dir).is_dir()
                else str(scatter_file.parent)
            )
            # Also copy the scatter file into sp_dir so local relative lookups succeed
            if sp_dir.resolve() != scatter_file.parent.resolve():
                try:
                    shutil.copy2(scatter_file, sp_dir / scatter_file.name)
                except Exception as e:
                    logger.debug("Could not copy scatter file to %s: %s", sp_dir, e)
        elif "Y2" in (model or "").upper():
            scatter_name = "MT6582_Android_scatter.txt"
        else:
            scatter_name = "MT6572_Android_scatter.txt"

        history_ini_path = sp_dir / HISTORY_INI
        if history_ini_path.is_file():
            text = history_ini_path.read_text(encoding="utf-8", errors="replace")
            # Update scatterHistory
            if re.search(r"(?m)^\s*scatterHistory\s*=", text):
                text, _ = re.subn(
                    r"(?m)^\s*scatterHistory\s*=\s*.*$",
                    f"scatterHistory={scatter_name}",
                    text,
                    count=1,
                )
            elif "[RecentOpenFile]" in text:
                text = re.sub(
                    r"(?m)(\[RecentOpenFile\][^\[]*)",
                    lambda m: m.group(1).rstrip() + f"\nscatterHistory={scatter_name}\n",
                    text,
                    count=1,
                )
            else:
                text = (
                    text.rstrip()
                    + f"\n\n[RecentOpenFile]\nlastDir={scatter_dir}\nscatterHistory={scatter_name}\nauthHistory=\n"
                )

            # Update lastDir under [RecentOpenFile]
            if scatter_dir and "[RecentOpenFile]" in text:
                parts = text.split("[RecentOpenFile]")
                if len(parts) == 2:
                    recent_part = parts[1]
                    if re.search(r"(?m)^\s*lastDir\s*=", recent_part):
                        recent_part, _ = re.subn(
                            r"(?m)^\s*lastDir\s*=\s*.*$",
                            f"lastDir={scatter_dir}",
                            recent_part,
                            count=1,
                        )
                    else:
                        recent_part = f"\nlastDir={scatter_dir}" + recent_part
                    text = parts[0] + "[RecentOpenFile]" + recent_part

            history_ini_path.write_text(text, encoding="utf-8")
            return True

        content = (
            "[LastDAFilePath]\n"
            "lastDir=MTK_AllInOne_DA.bin\n\n"
            "[RecentOpenFile]\n"
            f"lastDir={scatter_dir}\n"
            f"scatterHistory={scatter_name}\n"
            "authHistory=\n"
        )
        history_ini_path.write_text(content, encoding="utf-8")
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
