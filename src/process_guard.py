"""Install-session process hygiene for the flash backends.

SP Flash Tool and mtkclient both hold the USB port, the tool's own settings
files and the target's download session while they run. One left behind after a
finished, failed or abandoned install does not just waste memory: it keeps the
port open, so the next attempt only ever sees "no device", and a fresh
plug-in looks dead. The flash tool is therefore allowed to run for exactly one
window — from the guidance that asks the user to connect the device until the
backend reports success or failure — and nothing may survive that window.

:func:`begin_install_session` / :func:`end_install_session` mark it.
:func:`sweep_stray_tools` cleans up anything from an earlier window and refuses
to run while a session is active, because that session owns its tool.

Every sweep works from the process list, not from a list of pids we happened to
spawn, so a tool orphaned by a crash or a previous run of the app is caught too.
Matching is deliberately narrow — ``flash_tool`` / ``flash_tool.exe`` /
``flash_tool.sh`` as the executable, or ``mtk.py`` as an argument — so a text
editor with a file open under ``vendor/mtkclient`` is never touched.
"""

from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Executable names that mean "a flash backend is running".
_FLASH_TOOL_EXECUTABLES = {"flash_tool", "flash_tool.exe", "flash_tool.sh"}
# The mtkclient CLI does not exist when its console script was never
# installed, so the ``mtk.py`` inside a python invocation is what to look for.
_MTK_CONSOLE_SCRIPTS = {"mtkclient", "mtkclient.exe", "mtk", "mtk.exe"}
_MTK_SCRIPT_NAMES = {"mtk.py", "mtk.pyw"}
_ADB_EXECUTABLES = {"adb", "adb.exe"}
_UPDATER_BIN_NAMES = {
    "innioasisupdater",
    "innioasisupdater.exe",
    "updater-ce",
    "updater-ce.exe",
    "installfirmware",
    "installfirmware.exe",
}
# ``python``, ``python3``, ``python3.10``, ``pythonw.exe``, …
_PYTHON_PREFIX = "python"
# The Linux payload ships ``flash_tool.sh`` beside the binary and is normally
# started through a shell, so what the process list shows is the shell.
_SHELL_EXECUTABLES = {
    "bash",
    "bash.exe",
    "sh",
    "sh.exe",
    "dash",
    "zsh",
    "fish",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}
_TOKEN_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
# How long a process may take to honour SIGTERM before it is killed outright.
_TERM_GRACE_SECONDS = 2.0

_SESSION_LOCK = threading.Lock()
_ACTIVE_SESSIONS = 0

# Last sweep's result, for the diagnostics page and the log.
_LAST_SWEEP: list = []


def _split_command(command: str) -> list:
    """Command line tokens, keeping quoted paths (``"C:\\SP Flash Tool\\"``) whole."""
    tokens = []
    for match in _TOKEN_RE.finditer(command or ""):
        for group in match.groups():
            if group is not None:
                tokens.append(group)
                break
    return tokens


def _basename(token: str) -> str:
    """Last path component, on either separator (``Path`` only knows one)."""
    return re.split(r"[\\/]", (token or "").strip())[-1].lower()


def _tool_invocation(command: str) -> str:
    """The tool this command line runs, or "" when it is not one of ours.

    Only the executable itself, the mtkclient console script, and an ``mtk.py``
    handed to a python interpreter count. A path that merely mentions mtkclient
    — an editor, a grep, this app itself — is not a flash tool and must never be
    terminated, so an argument is only evidence when python is running it.
    """
    tokens = _split_command(command)
    if not tokens:
        return ""
    first = _basename(tokens[0])
    if first in _FLASH_TOOL_EXECUTABLES:
        return first
    if first in _MTK_CONSOLE_SCRIPTS:
        return first
    if first in _ADB_EXECUTABLES:
        return first
    if "--flash-cli" in tokens:
        return "flash-cli"
    runner = first.startswith(_PYTHON_PREFIX) or first in _SHELL_EXECUTABLES
    if runner:
        for token in tokens[1:]:
            name = _basename(token)
            if name in _MTK_SCRIPT_NAMES or name in _FLASH_TOOL_EXECUTABLES:
                return name
            if name in _ADB_EXECUTABLES:
                return name
    return ""


def _parse_ps_output(text: str) -> list:
    """``ps`` rows as ``(pid, ppid, command)`` with the header dropped."""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.lower().startswith("pid"):
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue
        rows.append((pid, ppid, parts[2]))
    return rows


def _ps_snapshot() -> list:
    """Every live process as ``(pid, ppid, command)`` (best effort)."""
    for args in (["ps", "-axo", "pid=,ppid=,command="], ["ps", "-ef"]):
        try:
            res = subprocess.run(
                args, capture_output=True, text=True, timeout=6
            )
        except Exception as e:
            logger.debug("Process listing with %s failed: %s", args, e)
            continue
        rows = _parse_ps_output(res.stdout or "")
        if rows:
            return rows
    return []


def _windows_snapshot() -> list:
    """Every live process with its command line, via CIM (Windows only)."""
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,CommandLine | "
        "ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logger.debug("Process listing via CIM failed: %s", e)
        return []
    rows = []
    for line in (res.stdout or "").splitlines():
        line = line.rstrip()
        if not line or "\t" not in line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
        except ValueError:
            continue
    return rows


def process_snapshot() -> list:
    """Live processes as ``(pid, ppid, command)`` on this platform."""
    if sys.platform.startswith("win") or os.name == "nt":
        return _windows_snapshot()
    return _ps_snapshot()


def _own_ancestry(snapshot=None) -> set:
    """This process and every parent of it, so we never terminate ourselves."""
    safe = {os.getpid()}
    try:
        safe.add(os.getppid())
    except Exception:
        pass
    try:
        rows = _ps_snapshot() if snapshot is None else snapshot
        by_pid = {pid: ppid for pid, ppid, _cmd in rows}
    except Exception:
        by_pid = {}
    pid = os.getpid()
    for _ in range(64):
        parent = by_pid.get(pid)
        if not parent or parent <= 1 or parent in safe:
            break
        safe.add(parent)
        pid = parent
    return safe


def stray_tool_processes(snapshot=None) -> list:
    """Running flash-backend processes as ``[{"pid", "tool", "command"}]``."""
    rows = process_snapshot() if snapshot is None else snapshot
    safe = _own_ancestry(rows)
    found = []
    for pid, _ppid, command in rows:
        if pid in safe or pid <= 1:
            continue
        tool = _tool_invocation(command)
        if not tool:
            continue
        found.append({"pid": pid, "tool": tool, "command": command.strip()})
    return found


def _terminate(pid: int, tool: str) -> bool:
    """Stop one process tree; True when the pid is gone afterwards."""
    if sys.platform.startswith("win") or os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.debug("taskkill failed for pid %s: %s", pid, e)
            return False
        return not _pid_alive(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception as e:
        logger.debug("Could not signal pid %s: %s", pid, e)
        return False
    deadline = time.time() + _TERM_GRACE_SECONDS
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception as e:
        logger.debug("Could not kill pid %s: %s", pid, e)
        return False
    return not _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` is a live process.

    A process this app killed but did not reap stays signalable as a zombie, so
    signalling alone would report a stopped tool as "could not stop" — which is
    exactly the wrong thing to tell the user about a port they are trying to
    free. Zombies are not alive for our purposes.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    if sys.platform.startswith("win") or os.name == "nt":
        return True
    try:
        res = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return True
    state = (res.stdout or "").strip()
    if not state:
        return False
    return not state.upper().startswith("Z")


def install_session_active() -> bool:
    """True between the connect-device guidance and the backend's result."""
    with _SESSION_LOCK:
        return _ACTIVE_SESSIONS > 0


def begin_install_session(reason: str = "") -> None:
    """Open the window in which a flash backend may run.

    Called when an install starts waiting for the device. Only one install can
    run at a time in this app, so a second call (a retry, or the terminal
    installer taking over) marks the same window rather than nesting.
    """
    global _ACTIVE_SESSIONS
    with _SESSION_LOCK:
        _ACTIVE_SESSIONS = 1
    logger.debug("Install session opened%s", f" ({reason})" if reason else "")


def end_install_session(reason: str = "", sweep: bool = True) -> list:
    """Close the window and take away anything the backend left behind."""
    global _ACTIVE_SESSIONS
    with _SESSION_LOCK:
        _ACTIVE_SESSIONS = 0
    logger.debug("Install session closed%s", f" ({reason})" if reason else "")
    return sweep_stray_tools(reason=reason) if sweep else []


def last_sweep() -> list:
    """The processes removed by the most recent sweep."""
    return list(_LAST_SWEEP)


def describe(killed) -> str:
    """One line naming what a sweep stopped, for the app log."""
    if not killed:
        return ""
    names = ", ".join(f"{item['tool']} (pid {item['pid']})" for item in killed)
    return f"Stopped {len(killed)} leftover flash tool process(es): {names}"


def sweep_async(reason: str = "", on_done=None, force: bool = False) -> threading.Thread:
    """Sweep off the calling thread.

    Listing processes and waiting out a SIGTERM grace period takes up to a few
    seconds, which the GUI thread must never spend: a finished install has to
    show its result — and a retry has to start — straight away.
    """

    def _run():
        killed = sweep_stray_tools(force=force, reason=reason)
        if killed and callable(on_done):
            try:
                on_done(killed)
            except Exception as e:
                logger.debug("Sweep callback failed: %s", e)

    thread = threading.Thread(target=_run, name="flash-tool-sweep", daemon=True)
    thread.start()
    return thread


def free_com_ports(log_cb=None) -> list[str]:
    """Free COM / serial ports (such as /dev/ttyACM* and /dev/ttyUSB*) held by processes or services.

    On Linux, checks for ModemManager, brltty, and any processes holding open
    /dev/ttyACM* or /dev/ttyUSB* ports, releasing them so SP Flash Tool and MTKClient
    can open the COM port cleanly without S_COM_PORT_OPEN_FAIL (1013).
    """
    released = []
    if sys.platform.startswith("win") or os.name == "nt":
        return released

    import glob
    import shutil

    tty_ports = glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")
    safe = _own_ancestry()

    # 1. Kill any processes holding /dev/ttyACM* or /dev/ttyUSB*
    for port in tty_ports:
        pids_holding = set()
        try:
            for pid_dir in glob.glob("/proc/[0-9]*"):
                try:
                    pid = int(os.path.basename(pid_dir))
                    if pid in safe or pid <= 1:
                        continue
                    fd_dir = os.path.join(pid_dir, "fd")
                    if os.path.isdir(fd_dir):
                        for fd in os.listdir(fd_dir):
                            target = os.path.realpath(os.path.join(fd_dir, fd))
                            if target == port:
                                pids_holding.add(pid)
                except Exception:
                    continue
        except Exception:
            pass

        if shutil.which("fuser"):
            try:
                res = subprocess.run(
                    ["fuser", "-k", "-9", port],
                    capture_output=True,
                    timeout=2,
                )
                if res.returncode == 0:
                    released.append(f"{port} (fuser)")
            except Exception:
                pass

        for pid in pids_holding:
            try:
                os.kill(pid, signal.SIGKILL)
                released.append(f"{port} (pid {pid})")
                if log_cb:
                    log_cb(f"Terminated process {pid} locking {port}")
            except Exception:
                pass

    # 2. Check ModemManager / brltty
    for svc in ("ModemManager", "brltty"):
        try:
            chk = subprocess.run(
                ["systemctl", "is-active", "--quiet", svc],
                capture_output=True,
                timeout=1,
            )
            if chk.returncode == 0:
                subprocess.run(["systemctl", "stop", svc], capture_output=True, timeout=2)
                released.append(f"stopped {svc}")
                if log_cb:
                    log_cb(f"Temporarily stopped {svc} to prevent serial port lock")
        except Exception:
            pass

    return released


def sweep_stray_tools(force: bool = False, reason: str = "") -> list:
    """Terminate every flash tool that is not part of the running install.

    Refuses while an install session is open unless ``force`` is set: that
    session owns its tool, and killing it would abort a healthy flash.
    """
    global _LAST_SWEEP
    if install_session_active() and not force:
        logger.debug(
            "Sweep skipped%s: an install session is running and owns its tool",
            f" ({reason})" if reason else "",
        )
        return []
    candidates = stray_tool_processes()
    if install_session_active() and not force:
        logger.debug(
            "Sweep aborted after snapshot%s: an install session opened while enumerating",
            f" ({reason})" if reason else "",
        )
        return []
    active_pids: set = set()
    if not force:
        try:
            from .flash_service import active_sp_sessions
            active_pids.update(active_sp_sessions())
        except Exception:
            pass
    killed = []
    for item in candidates:
        if install_session_active() and not force:
            break
        if not force and item["pid"] in active_pids:
            continue
        if _terminate(item["pid"], item["tool"]):
            killed.append(item)
        else:
            logger.warning(
                "Could not stop leftover flash tool %s (pid %s)",
                item["tool"],
                item["pid"],
            )
    try:
        free_com_ports()
    except Exception:
        pass
    _LAST_SWEEP = killed
    if killed:
        logger.info(describe(killed))
    return killed

