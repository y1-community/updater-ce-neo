"""MediaTek SP Flash Tool GUI launcher for supported platforms (Windows + Linux x86/x86_64)."""

import logging
import os
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Tuple

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


def update_sp_history_ini(
    sp_dir: Path,
    scatter_path: Optional[Path] = None,
    model: str = "",
) -> bool:
    """Update or create history.ini in the SP Flash Tool directory.

    Pins scatterHistory to the active model or package scatter file so that
    the SP Flash Tool GUI opens with the scatter file pre-loaded.
    """
    try:
        sp_dir = Path(sp_dir)
        if not sp_dir.is_dir():
            return False

        scatter_name = ""
        if scatter_path and Path(scatter_path).is_file():
            scatter_name = Path(scatter_path).name
        elif "Y2" in (model or "").upper():
            scatter_name = "MT6582_Android_scatter.txt"
        else:
            scatter_name = "MT6572_Android_scatter.txt"

        history_ini_path = sp_dir / HISTORY_INI
        if history_ini_path.is_file():
            text = history_ini_path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"(?m)^\s*scatterHistory\s*=", text):
                text2, n = re.subn(
                    r"(?m)^\s*scatterHistory\s*=\s*.*$",
                    f"scatterHistory={scatter_name}",
                    text,
                    count=1,
                )
                if n:
                    history_ini_path.write_text(text2, encoding="utf-8")
                    return True
            if "[RecentOpenFile]" in text:
                text2 = re.sub(
                    r"(?m)(\[RecentOpenFile\][^\[]*)",
                    lambda m: (
                        m.group(1).rstrip() + f"\nscatterHistory={scatter_name}\n"
                        if "scatterHistory=" not in m.group(1)
                        else m.group(1)
                    ),
                    text,
                    count=1,
                )
                history_ini_path.write_text(text2, encoding="utf-8")
                return True

        content = (
            "[LastDAFilePath]\n"
            "lastDir=MTK_AllInOne_DA.bin\n\n"
            "[RecentOpenFile]\n"
            "lastDir=\n"
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
) -> Tuple[bool, str]:
    """Launch MediaTek SP Flash Tool in GUI mode on Windows or Linux.

    Returns:
        (success: bool, message: str)
    """
    if paths.IS_MAC:
        return False, "SP Flash Tool is not available on macOS. macOS uses MTKClient only."

    if platform.system() == "Linux":
        from . import linux_sp_flash

        if not linux_sp_flash.arch_supported():
            return False, linux_sp_flash.unsupported_reason()

        ok, msg = linux_sp_flash.ensure_linux_sp_flash_tool()
        if not ok:
            return False, f"Could not prepare SP Flash Tool: {msg}"

        stage = linux_sp_flash.stage_dir()
        update_sp_history_ini(stage, scatter_path=scatter_path, model=model)

        launcher_sh = stage / "flash_tool.sh"
        if launcher_sh.is_file():
            cmd = ["bash", str(launcher_sh)]
        else:
            bin_path = stage / linux_sp_flash.FLASH_TOOL_LINUX_BIN
            if not bin_path.is_file():
                return False, f"SP Flash Tool binary not found at {bin_path}"
            cmd = [str(bin_path)]

        env = linux_sp_flash.process_env(stage)
        try:
            subprocess.Popen(
                cmd,
                cwd=str(stage),
                env=env,
                start_new_session=True,
            )
            return True, "SP Flash Tool GUI launched successfully."
        except Exception as e:
            logger.exception("Failed to launch SP Flash Tool GUI on Linux")
            return False, str(e)

    elif paths.IS_WINDOWS:
        sp_dir = paths.find_sp_flash_tool()
        if not sp_dir:
            return False, "SP Flash Tool directory not found."

        flash_tool_exe = sp_dir / "flash_tool.exe"
        if not flash_tool_exe.is_file():
            return False, f"flash_tool.exe not found at {flash_tool_exe}"

        update_sp_history_ini(sp_dir, scatter_path=scatter_path, model=model)

        try:
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(
                [str(flash_tool_exe)],
                cwd=str(sp_dir),
                env=os.environ.copy(),
                creationflags=creationflags,
            )
            return True, "SP Flash Tool GUI launched successfully."
        except Exception as e:
            logger.exception("Failed to launch flash_tool.exe on Windows")
            return False, str(e)

    return False, f"SP Flash Tool is not supported on {platform.system()}."
