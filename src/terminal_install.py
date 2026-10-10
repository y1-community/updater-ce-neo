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


def _bundle_path(exe: str) -> str:
    """The ``*.app`` bundle containing *exe*, or "" when there is none."""
    marker = "/Contents/MacOS/"
    index = str(exe).find(marker)
    if index < 0:
        return ""
    return str(exe)[:index]


def _bundle_facts(app) -> tuple:
    """(bundle identifier, version) read from an app bundle's Info.plist."""
    import plistlib

    plist = Path(app) / "Contents" / "Info.plist"
    if not plist.is_file():
        return "", ""
    try:
        with plist.open("rb") as handle:
            data = plistlib.load(handle)
    except Exception as e:  # pragma: no cover - unreadable plist
        logger.debug("Could not read %s: %s", plist, e)
        return "", ""
    version = data.get("CFBundleShortVersionString") or data.get("CFBundleVersion") or ""
    return str(data.get("CFBundleIdentifier") or ""), str(version)


def launcher_candidates(exe: Optional[str] = None) -> list:
    """Paths a generated launcher may use to run this app's console entry point.

    The recorded path comes first: it is the build the user is running, so the
    hand-run command matches the guided flow byte for byte. The rest exist
    because that path is a *build output* — rebuilding the app replaces or
    renames the bundle in place, and a launcher written before the rebuild
    then points at a binary that is gone (bash reports "No such file or
    directory" and the install cannot start).

    A candidate is only accepted when it is an app bundle whose identifier and
    version match this one, so a renamed rebuild is found while a different
    product in the same folder, or an older installed copy of this app, is
    never used to flash firmware.
    """
    from . import paths

    exe = str(exe if exe is not None else sys.executable)
    candidates = [exe]
    if not paths.IS_MAC:
        return candidates
    app = _bundle_path(exe)
    if not app:
        return candidates
    bundle_id, version = _bundle_facts(app)
    app_path = Path(app)
    sees = [app_path.parent, Path.home() / "Applications", Path("/Applications")]
    for folder in sees:
        try:
            siblings = sorted(folder.glob("*.app"))
        except OSError:
            continue
        for sibling in siblings:
            if sibling == app_path:
                continue
            if bundle_id or version:
                other_id, other_version = _bundle_facts(sibling)
                if bundle_id and other_id != bundle_id:
                    continue
                if version and other_version != version:
                    continue
            binaries = sorted((sibling / "Contents" / "MacOS").glob("*"))
            for binary in binaries:
                text = str(binary)
                if text not in candidates:
                    candidates.append(text)
    return candidates


def launcher_identity(exe: Optional[str] = None) -> tuple:
    """(bundle identifier, version) the generated script checks candidates against."""
    app = _bundle_path(str(exe if exe is not None else sys.executable))
    if not app:
        return "", ""
    return _bundle_facts(app)


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
    model: str = "",
    da_file: str = "",
    auth_file: str = "",
) -> list:
    """The app's own MTKClient console entry point (``--flash-cli``).

    A Download Agent and/or auth file chosen by the user travel as the CLI's
    trailing optional positions. They are only appended when one is set, so
    the plain hand-run command is unchanged (and a hand-edited script stays
    readable); the positions match what ``--flash-cli`` parses.
    """
    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        cmd.extend(["-m", "src.app"])
    args = [
        "--flash-cli",
        str(extract_dir),
        str(scatter),
        str(platform_name or ""),
        str(package_path or ""),
    ]
    if model or da_file or auth_file:
        args.extend([str(model or ""), str(da_file or ""), str(auth_file or "")])
    cmd.extend(args)
    return cmd


def build_install_command(
    method,
    extract_dir,
    scatter,
    package_path: str = "",
    platform_name: str = "",
    auth_file: str = "",
    da_file: str = "",
    model: str = "",
) -> Optional[list]:
    """Console command for ``method``.

    SP Flash Tool is used when a build exists for this machine; otherwise the
    command falls back to MTKClient's CLI (the only backend on macOS).

    ``auth_file`` and ``da_file`` are the same optional inputs the guided flow
    uses: an auth file reaches SP Flash Tool through its console configuration
    file and MTKClient through ``--auth``; a Download Agent overrides SP Flash
    Tool's ``-d`` and MTKClient's ``--loader``.
    """
    if normalise_method(method) == METHOD_SP:
        cmd = sp_flash_tool_command(scatter, da_file=da_file or None, auth_file=auth_file)
        if cmd:
            return cmd
    return mtkclient_command(
        extract_dir, scatter, platform_name, package_path, model, da_file, auth_file
    )


def _resolve_lines(command: Sequence[str], fallbacks: Sequence[str], identity: tuple) -> list:
    """Shell that runs the recorded executable, or an equivalent one.

    A hand-run install must survive the app being rebuilt, renamed or moved
    after this script was written. Every candidate is checked against the
    bundle identifier and version recorded here, so the flash always runs the
    build the user was looking at when they asked for a terminal install.
    When nothing matches, the failure names the path that is missing instead
    of leaving bash's own "No such file or directory" behind.
    """
    exe = str(command[0])
    rest = " ".join(shlex.quote(str(part)) for part in command[1:])
    bundle_id, version = identity
    lines = [f"APP_EXE={shlex.quote(exe)}"]
    apps: list = []
    for candidate in fallbacks:
        app = _bundle_path(candidate)
        if app and app not in apps:
            apps.append(app)
    if apps:
        quoted = " ".join(shlex.quote(app) for app in apps)
        lines.extend(
            [
                "if [ ! -x \"$APP_EXE\" ]; then",
                "  RECORDED=\"$APP_EXE\"",
                "  APP_EXE=\"\"",
                f"  APP_ID={shlex.quote(bundle_id)}",
                f"  APP_VERSION={shlex.quote(version)}",
                # The app folder first (a rebuild or a rename), then the places
                # an installed copy lives. `plutil` reads the identity, so a
                # sibling that is a different product is skipped, not run.
                f"  for APP in {quoted}; do",
                "    [ -d \"$APP\" ] || continue",
                "    if [ -n \"$APP_ID\" ]; then",
                "      [ \"$(plutil -extract CFBundleIdentifier raw -o - \"$APP/Contents/Info.plist\" 2>/dev/null)\" = \"$APP_ID\" ] || continue",
                "    fi",
                "    if [ -n \"$APP_VERSION\" ]; then",
                "      [ \"$(plutil -extract CFBundleShortVersionString raw -o - \"$APP/Contents/Info.plist\" 2>/dev/null)\" = \"$APP_VERSION\" ] || continue",
                "    fi",
                "    for CANDIDATE in \"$APP\"/Contents/MacOS/*; do",
                "      [ -x \"$CANDIDATE\" ] || continue",
                "      APP_EXE=\"$CANDIDATE\"",
                "      break",
                "    done",
                "    [ -z \"$APP_EXE\" ] || break",
                "  done",
                "  if [ -z \"$APP_EXE\" ]; then",
                "    echo \"Could not find the app this installer was created by:\" >&2",
                "    echo \"  $RECORDED\" >&2",
                "    echo \"Reopen the app and start a terminal install again.\" >&2",
                "    echo",
                f"    echo {shlex.quote(POSIX_DONE_MSG)}",
                "    read -r _",
                "    exit 1",
                "  fi",
                "fi",
            ]
        )
    else:
        lines.append(
            "if [ ! -x \"$APP_EXE\" ]; then"
        )
        lines.append("  echo \"Could not find the app this installer was created by:\" >&2")
        lines.append("  echo \"  $APP_EXE\" >&2")
        lines.append("  echo \"Reopen the app and start a terminal install again.\" >&2")
        lines.append("  echo")
        lines.append(f"  echo {shlex.quote(POSIX_DONE_MSG)}")
        lines.append("  read -r _")
        lines.append("  exit 1")
        lines.append("fi")
    call = "\"$APP_EXE\""
    lines.append(f"{call} {rest}".rstrip())
    return lines


def build_script(
    command: Sequence[str],
    title: str = "Innioasis Updater CE",
    cwd=None,
    env: Optional[dict] = None,
    is_windows: Optional[bool] = None,
    fallbacks: Optional[Sequence[str]] = None,
    identity: Optional[tuple] = None,
) -> Path:
    """Write the launcher script that runs ``command`` in a terminal window.

    ``fallbacks`` (with ``identity``) turn the script self-healing: it finds
    the app again when the recorded executable has been rebuilt away. See
    ``launcher_candidates``.
    """
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
        if fallbacks:
            lines.extend(_resolve_lines(command, fallbacks, identity or ("", "")))
        else:
            lines.append(" ".join(shlex.quote(str(part)) for part in command))
        # Hand the install's own status back to the shell: a terminal install
        # that failed must not look like a success in a script or in a test.
        # ``read`` returns non-zero at end of input, which is not a failure.
        lines.append("STATUS=$?")
        lines.append("echo")
        lines.append('if [ "$STATUS" -ne 0 ]; then')
        lines.append('  echo "The install exited with status $STATUS." >&2')
        lines.append("fi")
        lines.append(f"echo {shlex.quote(POSIX_DONE_MSG)}")
        lines.append("read -r _ || true")
        lines.append('exit "$STATUS"')
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
        kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True
        subprocess.Popen(argv, **kwargs)
    except OSError as e:
        logger.warning("Could not open a terminal window: %s", e)
        return False
    return True
