"""Hand a console-mode install to the user's own terminal window.

This is the minimal alternative to the guided flow: the very command the app
would run is written to a small launcher script and opened in the platform
terminal, so the user can watch SP Flash Tool or MTKClient work, and diagnose
failures with those tools (or with the updater) directly. The app itself stays
on the Select Software screen and does not drive the run.
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

from .flash_service import METHOD_SP, normalise_method

logger = logging.getLogger(__name__)

SCRIPT_BASENAME = "install_from_terminal"
POSIX_DONE_MSG = "[done] Press Enter to close this window."


def script_dir() -> Path:
    """Directory holding the generated launcher scripts."""
    from .downloads import downloads_dir

    d = downloads_dir().parent / "terminal"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sp_flash_tool_command(
    scatter,
    sp_dir: Optional[Path] = None,
    da_file: Optional[Path] = None,
    auth_file: str = "",
) -> Optional[list]:
    """Console-mode SP Flash Tool command, matching the guided flow's arguments.

    ``auth_file`` is optional: when set, the command points SP Flash Tool at a
    generated console configuration file, because that is the only console-mode
    way to hand it an authentication file.

    Returns ``None`` when no SP Flash Tool build is available for this machine.
    """
    from . import paths

    if paths.IS_MAC:
        return None
    if sp_dir is None:
        if paths.IS_WINDOWS:
            sp_dir = paths.find_sp_flash_tool()
        else:
            from . import linux_sp_flash

            sp_dir = linux_sp_flash.stage_dir()
    if sp_dir is None:
        return None
    sp_dir = Path(sp_dir)
    scatter = Path(scatter)
    if paths.IS_WINDOWS:
        exe = sp_dir / "flash_tool.exe"
    else:
        from . import linux_sp_flash

        # The Linux bundle ships an option.ini pointing at C:\ProgramData; fix
        # it before the user runs flash_tool by hand.
        try:
            linux_sp_flash.fix_option_ini(sp_dir)
        except Exception as e:  # pragma: no cover - best effort
            logger.debug("Could not fix option.ini for %s: %s", sp_dir, e)
        exe = sp_dir / linux_sp_flash.FLASH_TOOL_LINUX_BIN
    if not Path(exe).is_file():
        return None
    da = Path(da_file) if da_file else sp_dir / "MTK_AllInOne_DA.bin"
    if paths.IS_WINDOWS:
        # flash_tool is an ANSI/Qt4 app: non-ASCII paths or spaces need 8.3 short names.
        from .flash_service import _to_short_path

        if not str(scatter).isascii() or " " in str(scatter):
            scatter = _to_short_path(scatter)
        if not str(da).isascii() or " " in str(da):
            da = _to_short_path(da)
    # Same argument construction as the guided flow, so a hand-run command and
    # the wizard stay in step — including the authentication file, which is only
    # reachable through SP Flash Tool's console configuration file.
    from .flash_service import sp_flash_tool_console_args

    return [str(exe), *sp_flash_tool_console_args(str(scatter), str(da), auth_file)]


def script_env(command: Sequence[str]) -> dict:
    """Environment the generated command needs to work when run by hand.

    The Linux SP Flash Tool bundle is a Qt4 app that only finds its own
    libraries and plugins through LD_LIBRARY_PATH / QT_PLUGIN_PATH; the guided
    flow sets those from ``linux_sp_flash.process_env``. Only the differences
    from this machine's environment are written into the script. Other tools
    (MTKClient) run with the user's own environment untouched.
    """
    from . import paths

    if not command or paths.IS_MAC or paths.IS_WINDOWS:
        return {}
    from . import linux_sp_flash

    if Path(str(command[0])).name != linux_sp_flash.FLASH_TOOL_LINUX_BIN:
        return {}
    stage = Path(str(command[0])).parent
    try:
        full = linux_sp_flash.process_env(stage)
    except Exception as e:  # pragma: no cover - best effort
        logger.debug("Could not build an SP Flash Tool environment: %s", e)
        return {}
    return {k: v for k, v in full.items() if os.environ.get(k) != v}


def mtkclient_command(
    extract_dir,
    scatter,
    platform_name: str = "",
    package_path: str = "",
) -> list:
    """The app's own MTKClient console entry point (``--flash-cli``)."""
    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        from . import app as app_main

        main_file = getattr(app_main, "__file__", None)
        if main_file:
            cmd.append(str(Path(main_file).resolve()))
        else:
            cmd.extend(["-m", "src.app"])
    cmd.extend([
        "--flash-cli",
        str(extract_dir),
        str(scatter),
        str(platform_name or ""),
        str(package_path or ""),
    ])
    return cmd


def build_install_command(
    method,
    extract_dir,
    scatter,
    package_path: str = "",
    platform_name: str = "",
    auth_file: str = "",
) -> Optional[list]:
    """Console command for ``method``.

    SP Flash Tool is used when a build exists for this machine; otherwise the
    command falls back to MTKClient's CLI (the only backend on macOS).
    """
    if normalise_method(method) == METHOD_SP:
        cmd = sp_flash_tool_command(scatter, auth_file=auth_file)
        if cmd:
            return cmd
    return mtkclient_command(extract_dir, scatter, platform_name, package_path)


def build_script(
    command: Sequence[str],
    title: str = "Innioasis Updater CE",
    cwd=None,
    env: Optional[dict] = None,
    is_windows: Optional[bool] = None,
) -> Path:
    """Write the launcher script that runs ``command`` in a terminal window."""
    if is_windows is None:
        is_windows = os.name == "nt"
    out_dir = script_dir()
    if is_windows:
        path = out_dir / f"{SCRIPT_BASENAME}.bat"
        lines = ["@echo off", f"title {title}"]
        if cwd:
            lines.append(f'cd /d "{cwd}"')
        for key, value in (env or {}).items():
            lines.append(f'set "{key}={value}"')
        lines.append(" ".join(f'"{part}"' if " " in part else part for part in command))
        lines.append("echo.")
        lines.append(f"echo {POSIX_DONE_MSG}")
        lines.append("pause")
    else:
        suffix = ".command" if sys.platform == "darwin" else ".sh"
        path = out_dir / f"{SCRIPT_BASENAME}{suffix}"
        lines = ["#!/bin/bash", f"# {title}"]
        if cwd:
            lines.append(f"cd {shlex.quote(str(cwd))} || exit 1")
        for key, value in (env or {}).items():
            lines.append(f"export {key}={shlex.quote(str(value))}")
        lines.append(" ".join(shlex.quote(str(part)) for part in command))
        lines.append("echo")
        lines.append(f"echo {shlex.quote(POSIX_DONE_MSG)}")
        lines.append("read -r _")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if not is_windows:
        try:
            path.chmod(0o755)
        except OSError as e:  # pragma: no cover - exotic filesystems only
            logger.debug("Could not mark %s executable: %s", path, e)
    return path


def terminal_argv(script, platform: Optional[str] = None) -> Optional[list]:
    """Command that opens ``script`` in a terminal window, if one is available."""
    if platform is None:
        platform = sys.platform
    if platform == "darwin":
        return ["open", "-a", "Terminal", str(script)]
    if platform == "win32":
        return ["cmd", "/c", "start", "Terminal install", "cmd", "/k", str(script)]
    candidates = (
        ("x-terminal-emulator", ["-e"]),
        ("gnome-terminal", ["--"]),
        ("konsole", ["-e"]),
        ("xfce4-terminal", ["-e"]),
        ("xterm", ["-e"]),
    )
    for exe, prefix in candidates:
        found = shutil.which(exe)
        if found:
            return [found, *prefix, str(script)]
    return None


def open_in_terminal(script, platform: Optional[str] = None) -> bool:
    """Open the launcher script in the platform terminal window."""
    argv = terminal_argv(script, platform=platform)
    if not argv:
        logger.warning("No terminal emulator found for a terminal install")
        return False
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        logger.warning("Could not open a terminal window: %s", e)
        return False
    return True
