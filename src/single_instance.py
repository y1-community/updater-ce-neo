"""One running copy of the app on every platform — and never a false refusal.

Two copies would fight over the same USB device, SP Flash Tool's own settings
files and the shared cache directory. Worse, every start-up sweeps stray flash
tools, so a second copy starting while a flash run is in progress can kill the
very tool that run is using. A second launch is therefore refused — it asks the
copy already running to show its window and exits. It only ever ends that copy
on request: when the running copy does not answer the focus request, the second
launch offers to force quit it and take over, and the user has to say yes. That
is the recovery path for a hung copy, not a routine takeover. The same dialog,
and a takeover that could not end the other copy, also offer Open App Anyway:
the guard must never be the reason a user cannot open the app at all.

The guard is deliberately biased towards *starting*. Refusing needs positive
evidence of a live copy; every ambiguous case fails open:

* the lock file lives in the launching user's own temp directory, named per user,
  so another account (or a shared ``/tmp``) can never be mistaken for this one;
* a lock whose holder pid is gone, or whose pid now belongs to a clearly
  unrelated program (a recycled pid), is stale and is taken over;
* a lock file that cannot be read or removed — read-only temp, permissions,
  another sandbox — is ignored, and the app starts;
* any unexpected error inside the guard starts the app.

Positive evidence is either the session-wide Windows mutex (kernel-owned, so it
cannot go stale and cannot be confused with a file) or a lock file whose holder
pid is alive and is our own program. The ``QLockFile`` also closes the race when
two copies are started at the same instant; the mutex and the focus server make
the answer authoritative once a copy is up.
"""

from __future__ import annotations

import ctypes
import logging
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QCoreApplication, QLockFile, QObject, QStandardPaths, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)

# Session-wide, not machine-wide: the player is a USB device, so copies in the
# same login session are the ones that can collide. ``Local\`` needs no
# privilege where ``Global\`` can.
WINDOWS_MUTEX_NAME = "Local\\innioasis-updater-ce-single-instance"
_ERROR_ALREADY_EXISTS = 183

# The elevated restart starts the new copy before the old one has quit, so
# the copy that inherits the guard is told to wait for it rather than to
# treat it as somebody else's launch.
TAKE_OVER_ARG = "--take-over"
# A package dropped on the running copy's icon travels over the focus socket.
# The NUL separator keeps paths with spaces (and other odd characters) intact.
DROP_MESSAGE_PREFIX = b"drop\x00"

_focus_server = None
_windows_mutex = None
_windows_kernel32 = None


def _user_token() -> str:
    """A stable, filename-safe token for the launching user.

    The lock file and the focus channel are per user: two accounts on one
    machine must not be able to block each other, and a shared temp directory
    must not hand one account's lock file to another.
    """
    try:
        raw = str(os.getuid()) if os.name != "nt" else (
            os.environ.get("USERNAME") or os.environ.get("USER") or ""
        )
    except Exception:
        raw = ""
    token = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip("._")
    return token or "user"


def lock_name() -> str:
    return f"updater-ce-{_user_token()}.lock"


def focus_server_name() -> str:
    return f"updater-ce-focus-{_user_token()}"


# Names a copy can run under, wherever its folder is: the packaged front ends
# (including the portable aliases the Windows build stages) and the
# development launcher. Windows reports image names; POSIX reports a command
# line, so both are matched.
_COPY_IMAGE_NAMES = (
    "innioasisupdater",
    "innioasis updater",
    "updater ce",
    "updater-ce",
    "2. install firmware",
    "mediatek installer",
    "mediatekinstaller",
)
_COPY_COMMAND_HINTS = (
    "src/app.py",
    "src\\app.py",
    "innioasis updater",
    "updater-ce",
    "2. install firmware",
    "mediatek installer",
)
_COPY_TITLE_HINTS = (
    "innioasis updater",
    "updater ce",
    "install firmware",
    "mediatek installer",
)


def _copy_pid_from_identity(pid: int, identity: str) -> bool:
    """True when an image name or command line is a copy of this app."""
    text = str(identity or "").replace("\\", "/").lower()
    image = text.rsplit("/", 1)[-1]
    if image.endswith(".exe"):
        image = image[:-4]
    if image.strip() in _COPY_IMAGE_NAMES:
        return True
    return any(hint.replace("\\", "/") in text for hint in _COPY_COMMAND_HINTS)


def _copy_pid_from_command(pid: int, command: str) -> bool:
    """True only when a POSIX command line is a copy of this app itself.

    A command line is not evidence: a shell, editor or file manager opened in
    a folder called ``updater-ce``, or any program whose arguments mention the
    app, would otherwise match and refuse a launch when no copy is running.
    The guarded front ends and the development launcher are recognised by the
    executable or the script they run, never by a folder name in an argument.
    """
    argv = str(command or "").split()
    if not argv:
        return False
    image = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if image.endswith(".exe"):
        image = image[:-4]
    if image in _COPY_IMAGE_NAMES:
        return True
    # A development copy runs as its interpreter; only the app's own script
    # path among its first arguments makes it this program.
    return any(
        part.replace("\\", "/").endswith("src/app.py") for part in argv[1:4]
    )


def _windows_process_image_names() -> dict:
    """PID -> image name from a Toolhelp snapshot, without spawning a helper."""
    try:
        from ctypes import wintypes

        TH32CS_SNAPPROCESS = 0x00000002
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snapshot in (None, 0, INVALID_HANDLE_VALUE):
            return {}
        names = {}
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            more = kernel32.Process32FirstW(
                ctypes.c_void_p(snapshot), ctypes.byref(entry)
            )
            while more:
                names[int(entry.th32ProcessID)] = str(entry.szExeFile)
                more = kernel32.Process32NextW(
                    ctypes.c_void_p(snapshot), ctypes.byref(entry)
                )
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(snapshot))
        return names
    except Exception:
        return {}


def find_running_copy_pids() -> list[int]:
    """PIDs of other copies of this app, whatever folder they started from.

    The mutex and the lock file answer for builds that carry this guard. An
    older portable copy, or one started from another extraction, may hold
    neither, so the process table is the backstop.
    """
    me = os.getpid()
    # A portable build can start a child copy and stay alive as its launcher;
    # that parent is this program, not another copy competing for the player.
    parent = os.getppid()
    found: list[int] = []
    try:
        if sys.platform.startswith("win"):
            names = _windows_process_image_names()
            for pid, name in names.items():
                if pid and pid not in (me, parent) and _copy_pid_from_identity(pid, name):
                    found.append(int(pid))
            # The snapshot succeeded: "no match" is the answer, so do not spawn
            # tasklist on every normal start. Only a failed snapshot falls back.
            if names:
                return found
            out = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=6,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout or ""
            for line in out.splitlines():
                parts = [part.strip().strip('"') for part in line.split('","')]
                if len(parts) < 2:
                    continue
                try:
                    pid = int(parts[1])
                except ValueError:
                    continue
                if pid and pid not in (me, parent) and _copy_pid_from_identity(pid, parts[0]):
                    found.append(pid)
            return found
        out = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            timeout=6,
        ).stdout or ""
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_text, _, command = line.partition(" ")
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if pid and pid not in (me, parent) and _copy_pid_from_command(pid, command):
                found.append(pid)
    except Exception:
        logger.debug("Could not list running copies", exc_info=True)
    return found


def _focus_running_window(pid: int = 0) -> bool:
    """Raise another copy's top-level window on Windows, by pid or by title.

    A copy built before the focus socket existed cannot answer it, but its
    window is still there to be shown. This is the same operation the user gets
    by clicking the taskbar entry.
    """
    if os.name != "nt":
        return False
    try:
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _visit(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.strip().lower()
                if any(hint in title for hint in _COPY_TITLE_HINTS):
                    owner = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                    if not pid or int(owner.value) == int(pid):
                        found.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(_visit), 0)
        for hwnd in found:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
        return bool(found)
    except Exception:
        logger.debug("Could not raise the running copy's window", exc_info=True)
        return False


# -- the show-request marker -------------------------------------------------
#
# The local socket is the fast path, but a copy started from another folder,
# an older build, or a host that blocks local sockets still needs a way in.
# A launch writes a marker into the app's own per-user profile; every copy of
# this version, portable or installed, watches the same file. The running copy
# deletes it only after it has brought its window up, which also tells a second
# launch whether the first one actually answered.

SHOW_REQUEST_TIMEOUT_S = 2.5


def show_request_path() -> Path:
    """The show marker shared by installed and portable copies of this app.

    A process temp directory is not enough: portable builds can run from
    different folders and still share the same per-user profile. Keep the
    marker there so one of them can bring the other forward.
    """
    try:
        from .paths import app_cache_dir

        base = app_cache_dir()
    except Exception:
        base = None
    if base is None:
        fallback = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.TempLocation
        )
        base = Path(fallback or tempfile.gettempdir())
    return Path(base) / f"updater-ce-{_user_token()}.show"


def request_show() -> bool:
    """Ask the running copy to show itself. False when the marker cannot be written."""
    try:
        path = show_request_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(time.time()), encoding="utf-8")
        return True
    except Exception:
        logger.debug("Could not write the show request", exc_info=True)
        return False


def show_request_stamp() -> float:
    try:
        return show_request_path().stat().st_mtime
    except Exception:
        return 0.0


def clear_show_request(expected_stamp: Optional[float] = None) -> bool:
    """Remove the marker, or only the request *expected_stamp* identifies.

    The acknowledgement must not delete a newer request that arrived while the
    window was being raised; that launch still needs its own show.
    """
    try:
        path = show_request_path()
        if expected_stamp is not None and show_request_stamp() != expected_stamp:
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def wait_for_show_request(stamp: float, timeout_s: float = SHOW_REQUEST_TIMEOUT_S) -> bool:
    """Wait for the running copy to delete the marker it was asked to handle."""
    if not stamp:
        return False
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if show_request_stamp() != stamp:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.1, remaining))


def watch_show_requests(callback, interval_ms: int = 1500, baseline: Optional[float] = None):
    """Call *callback* whenever another launch asks this copy to show itself.

    The callback's successful return acknowledges the request by removing the
    marker. A request that arrived while the callback ran keeps its marker.
    """
    from PySide6.QtCore import QTimer

    # A request written while the window was still being built is real: start
    # from the launch-time stamp so it is not mistaken for a stale file. The
    # caller clears a marker that was already there before this copy started.
    last = [float(baseline) if baseline is not None else 0.0]
    timer = QTimer()
    timer.setInterval(max(250, int(interval_ms)))

    def _poll() -> None:
        stamp = show_request_stamp()
        if stamp and stamp != last[0]:
            last[0] = stamp
            try:
                callback()
            except Exception:
                logger.debug("Show request callback failed", exc_info=True)
            else:
                clear_show_request(stamp)

    timer.timeout.connect(_poll)
    timer.start()
    return timer


class FocusServer(QObject):
    """Lets a second launch ask the running copy to show its window or install a file."""

    focus_requested = Signal()
    drop_requested = Signal(str)

    def __init__(self, name: Optional[str] = None):
        super().__init__()
        self._name = name or focus_server_name()
        self._server = QLocalServer(self)
        self._buffers: dict[QLocalSocket, bytearray] = {}
        # A socket left behind by a crash is not a live copy; Qt's cleanup only
        # matters on desktops that use a real socket file.
        QLocalServer.removeServer(self._name)
        self._server.newConnection.connect(self._accept)
        self._server.listen(self._name)

    def is_listening(self) -> bool:
        return bool(self._server.isListening())

    def _accept(self) -> None:
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                continue
            # Keep the payload until the peer closes. A local socket can
            # deliver the write after newConnection(), so reading only once
            # here lost the path and turned the request into a bare focus.
            self._buffers[conn] = bytearray()
            conn.readyRead.connect(lambda c=conn: self._read_available(c))
            conn.disconnected.connect(lambda c=conn: self._finish(c))
            self._read_available(conn)

    def _read_available(self, conn: QLocalSocket) -> None:
        buffer = self._buffers.get(conn)
        if buffer is None:
            return
        try:
            buffer.extend(bytes(conn.readAll()))
        except Exception:
            logger.debug("Could not read the focus channel payload", exc_info=True)

    def _finish(self, conn: QLocalSocket) -> None:
        buffer = self._buffers.get(conn)
        if buffer is None:
            return
        self._read_available(conn)
        data = bytes(self._buffers.pop(conn, buffer))
        if data.startswith(DROP_MESSAGE_PREFIX):
            path = os.fsdecode(data[len(DROP_MESSAGE_PREFIX):])
            if path:
                self.drop_requested.emit(path)
        else:
            self.focus_requested.emit()
        try:
            conn.deleteLater()
        except Exception:
            pass


def start_focus_server() -> Optional[FocusServer]:
    """Start the primary copy's focus channel (kept alive by the app)."""
    global _focus_server
    if _focus_server is not None and _focus_server.is_listening():
        return _focus_server
    server = FocusServer()
    if not server.is_listening():
        logger.debug("Could not start the single-instance focus server")
        return None
    _focus_server = server
    return server


def _send_focus_payload(payload: bytes, timeout_ms: int) -> bool:
    """Deliver one message to the running copy's focus channel.

    ``disconnectFromServer`` can discard a payload that has not reached the
    event loop yet, and this launch has no event loop of its own. Pump the
    pending write for a moment before closing; the server keeps the buffer
    until the peer disconnects and then dispatches it.
    """
    try:
        socket = QLocalSocket()
        socket.connectToServer(focus_server_name())
        if not socket.waitForConnected(timeout_ms):
            return False
        socket.write(payload)
        socket.flush()
        app = QCoreApplication.instance()
        deadline = time.monotonic() + max(0.05, timeout_ms / 1000.0)
        while socket.bytesToWrite() and time.monotonic() < deadline:
            if app is not None:
                app.processEvents()
            socket.waitForBytesWritten(30)
        socket.disconnectFromServer()
        if app is not None:
            app.processEvents()
        return True
    except Exception:
        logger.debug("Focus channel message could not be sent", exc_info=True)
        return False


def request_focus(timeout_ms: int = 500) -> bool:
    """Ask an already-running copy to come to the front.

    A successful connection is itself proof that a copy is alive: the OS owns
    the name, so it cannot be left behind by a crash.
    """
    return _send_focus_payload(b"show", timeout_ms)


def request_drop(path, timeout_ms: int = 500) -> bool:
    """Hand a package to the running copy and ask it to show its window.

    A taskbar or desktop drop can start a second process with the file as an
    argument. That launch must not run beside the copy already holding the USB
    device; it forwards the path over the same per-user local socket used for
    focus requests, then exits. A successful connection is proof of a live copy.
    """
    return _send_focus_payload(
        DROP_MESSAGE_PREFIX + os.fsencode(str(path)), timeout_ms
    )


def _win_kernel32():
    """kernel32 loaded with the ctypes error shadow, so last-error is real.

    ``ctypes.get_last_error()`` only reports what the DLL captured when it was
    loaded with ``use_last_error=True``. Loaded the plain way it returns 0, which
    made every duplicate copy look like the first one.
    """
    global _windows_kernel32
    if _windows_kernel32 is None:
        _windows_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _windows_kernel32


def _create_named_mutex(name: str):
    """Create a named mutex; returns ``(handle, already_existed)``.

    A zero handle means the host refused to create it (a locked-down machine),
    which callers treat as "no guard available" rather than "another copy".
    """
    create = _win_kernel32().CreateMutexW
    create.restype = ctypes.c_void_p
    # A stale thread error from an earlier call would read as "exists".
    ctypes.set_last_error(0)
    handle = create(None, False, name)
    return handle, ctypes.get_last_error() == _ERROR_ALREADY_EXISTS


def acquire_windows_mutex() -> bool:
    """Hold the session-wide guard for this process. False means a copy exists.

    Windows only; every other platform returns True and relies on the lock file.
    The handle lives as long as the process, so the kernel releases the guard
    however the app exits. A failure to create the mutex is *not* treated as a
    conflict — a locked-down host may forbid named objects, and blocking the app
    there would be a false refusal.
    """
    global _windows_mutex
    if os.name != "nt":
        return True
    if _windows_mutex is not None:
        return True
    handle = 0
    try:
        handle, existed = _create_named_mutex(WINDOWS_MUTEX_NAME)
    except Exception as e:
        logger.debug("Could not create the single-instance mutex: %s", e)
        return True
    if not handle:
        logger.debug("CreateMutexW failed; starting without the mutex")
        return True
    if existed:
        try:
            _win_kernel32().CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass
        return False
    _windows_mutex = handle
    return True


def _take_over_requested() -> bool:
    """True when this launch is replacing a copy that is on its way out."""
    try:
        return TAKE_OVER_ARG in sys.argv[1:]
    except Exception:
        return False


def _wait_for_previous_copy(deadline_s: float = 8.0) -> None:
    """Give the copy this launch replaces time to finish exiting.

    The elevated restart starts the new copy before the old one quits, so the
    lock is held for a moment. Waiting for it to fall free keeps that hand-over
    from looking like a second person's launch — and from offering to force quit
    the copy that is already leaving.
    """
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        probe = InstanceLock()
        if probe.acquire():
            probe.release()
            return
        time.sleep(0.2)


def lock_path() -> Path:
    """The launching user's own lock file."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    return Path(base or tempfile.gettempdir()) / lock_name()


def _pid_alive(pid: int) -> bool:
    """True when *pid* is still a live process on this machine."""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
        except Exception:
            return False
        return str(pid) in (out or "")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Alive, but owned by someone else: still a holder.
        return True
    except OSError:
        return False
    # A zombie is not a running copy: it can never answer, so a lock left by a
    # copy that crashed and was never reaped must not refuse launches forever.
    try:
        state = (
            subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=3,
            ).stdout
            or ""
        ).strip().upper()
    except Exception:
        return True
    if not state:
        return True
    return not state.startswith("Z")


def _pid_identity(pid: int) -> str:
    """Best-effort image name / command line of *pid*, or "" when unreadable."""
    if pid <= 0:
        return ""
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout or ""
            for line in out.splitlines():
                if line.startswith('"'):
                    return line.split('","')[0].strip('"').lower()
            return ""
        proc = Path(f"/proc/{pid}/cmdline")
        if proc.exists():
            raw = proc.read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
            return raw.strip().lower()
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout or ""
        return out.strip().lower()
    except Exception:
        return ""


# Anything a copy of this project runs as: the packaged front ends, the
# development launcher, and the frozen app's own bootloader. Deliberately not
# generic words: a recycled pid that now belongs to some other updater or to a
# plain Python process must read as foreign, or a stale lock refuses every
# launch on that machine (the Linux reports: no window, still "already running").
_OUR_APP_HINTS = (
    "innioasis",
    "updater-ce",
    "updater ce",
    "updaterce",
    "src/app.py",
    "launcher.py",
    "mediatek",
)


def _is_clearly_foreign(pid: int) -> bool:
    """True only when *pid* is provably some other program (a recycled pid).

    Unreadable identities count as "not foreign" so a permission boundary can
    never be turned into a refusal.
    """
    identity = _pid_identity(pid)
    if not identity:
        return False
    return not any(hint in identity for hint in _OUR_APP_HINTS)


class InstanceLock:
    """The app's single-instance lock file."""

    def __init__(self, path: Optional[Path] = None):
        self._lock = QLockFile(str(path or lock_path()))
        # No time-based guesswork: a dead holder is detected by its pid.
        self._lock.setStaleLockTime(0)
        self._held = False

    def acquire(self, timeout_ms: int = 200) -> bool:
        self._held = bool(self._lock.tryLock(timeout_ms))
        if not self._held and not self.holder_pid():
            # The lock file survived without a readable owner: remove it once
            # and try again rather than failing a launch for nothing.
            self._lock.removeStaleLockFile()
            self._held = bool(self._lock.tryLock(timeout_ms))
        return self._held

    def release(self) -> None:
        if self._held:
            self._lock.unlock()
            self._held = False

    def holder_pid(self) -> int:
        try:
            info = self._lock.getLockInfo()
        except Exception:
            return 0
        if not info:
            return 0
        try:
            return int(info[0])
        except (TypeError, ValueError, IndexError):
            return 0

    def unopenable(self) -> bool:
        """True when the lock file cannot be used here (read-only temp, rights).

        Nothing about permissions may stop the app from starting, so the caller
        treats this as "no guard available" rather than "another copy".
        """
        try:
            error = self._lock.error()
        except Exception:
            return False
        permission_error = getattr(QLockFile.LockError, "PermissionError", None)
        return permission_error is not None and error == permission_error

    def drop(self) -> bool:
        """Try to clear a lock we have proven stale. False when it cannot go."""
        try:
            return bool(self._lock.removeStaleLockFile())
        except Exception:
            return False


def _dismiss_label() -> str:
    """The app's own acknowledgement label, whichever key this build carries."""
    from .i18n import tr

    for key in ("btn_ok", "btn_close", "dialog_ok"):
        text = tr(key)
        if text and text != key:
            return text
    return "OK"


def _focus_running_copy(
    attempts: int = 4, timeout_ms: int = 350, pause_s: float = 0.3
) -> bool:
    """Ask the running copy to come forward, giving a slow start a moment."

    A copy that is still starting up has not opened its focus channel yet;
    retrying briefly keeps a fast second launch from calling it unresponsive.
    Each attempt is also a liveness probe: the OS owns the socket name.
    """
    for attempt in range(max(1, attempts)):
        if request_focus(timeout_ms=timeout_ms):
            return True
        if attempt + 1 < attempts:
            time.sleep(pause_s)
    return False


def end_running_copy(pid: int, wait_s: float = 5.0) -> bool:
    """End the copy the user just agreed to force quit. True once it is gone.

    Only called from the explicit force-quit answer. Termination is immediate,
    so the running copy gets no shut-down sweep: that is exactly what makes it
    usable on a hung copy, and why the dialog warns about an install in flight.
    """
    if pid <= 0:
        return False
    try:
        from . import instance_marker

        if instance_marker.busy_for_pid(pid):
            logger.info("Refusing to end pid %s: it is running an install or owns a tool", pid)
            return False
    except Exception:
        logger.debug("Could not read the running-copy marker", exc_info=True)
    try:
        if sys.platform.startswith("win"):
            kernel32 = getattr(ctypes, "WinDLL", ctypes.windll).kernel32
            process_terminate = 0x0001
            handle = kernel32.OpenProcess(process_terminate, False, int(pid))
            if not handle:
                return False
            try:
                kernel32.TerminateProcess(handle, 1)
            finally:
                kernel32.CloseHandle(handle)
        else:
            os.kill(int(pid), signal.SIGTERM)
    except Exception as e:
        logger.info("Could not end the running copy (pid %s): %s", pid, e)
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def _explain_force_failed(pid: int = 0) -> bool:
    """Say the takeover did not work, and offer to open the app anyway.

    The copy that answered nothing could not be ended (another account, a
    protected process, a wedged kernel). Refusing the launch then leaves the
    user with no way in at all, so the notice offers Open App Anyway; True
    means the user chose it.
    """
    from PySide6.QtWidgets import QMessageBox

    from .i18n import tr

    box = QMessageBox()
    box.setWindowTitle(tr("single_instance_title"))
    detail = tr("single_instance_force_failed")
    if pid:
        # The user has to be able to find it in Task Manager / Activity Monitor.
        detail = f"{detail}\n\nPID: {pid}"
    box.setText(detail)
    box.setIcon(QMessageBox.Icon.Warning)
    anyway = box.addButton(
        tr("single_instance_open_anyway"), QMessageBox.ButtonRole.AcceptRole
    )
    dismiss = box.addButton(_dismiss_label(), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(dismiss)
    box.exec()
    return box.clickedButton() is anyway


def _explain_already_running(focus_failed: bool = False, pid: int = 0) -> str:
    """Say why this launch stops; on an unanswered focus, offer the ways in.

    Returns ``"force"`` when the user chose to end a copy that did not answer,
    and ``"anyway"`` when they chose to open this copy beside it instead. The
    copy that is running can own the USB device and its own flash tool, so the
    destructive answer is never the default and its copy says the install in
    progress would be interrupted. Open App Anyway is the fallback for a copy
    that refuses to be ended: a guard must never lock the user out.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

    from .i18n import tr

    dialog = QDialog()
    dialog.setWindowTitle(tr("single_instance_title"))
    dialog.setModal(True)
    dialog.setMinimumWidth(420)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 18, 20, 16)
    layout.setSpacing(10)

    title = QLabel(tr("single_instance_title"))
    font = title.font()
    font.setPixelSize(14)
    font.setBold(True)
    title.setFont(font)
    layout.addWidget(title)

    body = QLabel(
        tr("single_instance_focus_failed" if focus_failed else "single_instance_body")
    )
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    layout.addWidget(body)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    # Distinct results: both ways forward accept the dialog, but only one may
    # end the other copy.
    force_code, anyway_code = 1, 2
    if focus_failed and pid > 0:
        force = QPushButton(tr("single_instance_force_quit"))
        force.setAutoDefault(False)
        force.clicked.connect(lambda: dialog.done(force_code))
        buttons.addWidget(force)
        anyway = QPushButton(tr("single_instance_open_anyway"))
        anyway.setAutoDefault(False)
        anyway.clicked.connect(lambda: dialog.done(anyway_code))
        buttons.addWidget(anyway)
    dismiss = QPushButton(tr("btn_cancel") if focus_failed else _dismiss_label())
    dismiss.setDefault(True)
    dismiss.setAutoDefault(True)
    dismiss.clicked.connect(dialog.reject)
    buttons.addWidget(dismiss)
    layout.addLayout(buttons)

    result = dialog.exec()
    if result == force_code:
        return "force"
    if result == anyway_code:
        return "anyway"
    return ""


def _busy_copy_running() -> bool:
    """True when the live copy published work another launch must not kill."""
    try:
        from . import instance_marker

        return bool(instance_marker.foreign_busy())
    except Exception:
        return False


def _explain_busy_copy() -> None:
    """Refuse the takeover while the running copy owns the USB device."""
    from PySide6.QtWidgets import QMessageBox

    from .i18n import tr

    box = QMessageBox()
    box.setWindowTitle(tr("single_instance_title"))
    box.setText(tr("single_instance_busy_body"))
    box.setIcon(QMessageBox.Icon.Information)
    box.exec()


def _hold_lock(app, lock: InstanceLock) -> None:
    app._instance_lock = lock
    try:
        app.aboutToQuit.connect(lock.release)
    except Exception:
        pass


def ensure_single_instance(
    app, lock: Optional["InstanceLock"] = None, quiet: bool = False
) -> bool:
    """Take the guard, or send this launch to the copy already running.

    True means "carry on". A copy that answers the focus request is never
    terminated, so an ordinary second launch cannot disturb a flash run in
    progress; only a copy that fails to answer may be ended, and only after the
    user chooses that. Any failure inside the guard itself starts the app: the
    user is never locked out by the guard's own machinery.

    ``quiet`` marks a start-at-login launch: if another copy is already running
    it leaves without asking for the window, because a login must never raise
    one by itself.
    """
    try:
        return _guarded(app, lock, quiet=quiet)
    except Exception:
        logger.warning("Single-instance guard failed; starting normally", exc_info=True)
        return True


def _ask_running_copy_to_show(pid: int = 0, wait_s: Optional[float] = None) -> bool:
    """Ask the live copy to show, treating its marker deletion as acknowledgement.

    A visible window is immediate proof. Otherwise the focus socket is tried;
    if it cannot deliver, wait for the running copy to delete the marker. Only
    a copy that neither raised a window nor acknowledged the marker is called
    unresponsive.
    """
    request_show()
    stamp = show_request_stamp()
    if _focus_running_window(pid):
        if stamp:
            clear_show_request(stamp)
        return True
    if _focus_running_copy():
        return True
    timeout = SHOW_REQUEST_TIMEOUT_S if wait_s is None else wait_s
    return bool(stamp) and wait_for_show_request(stamp, timeout_s=timeout)


def _guarded(app, lock: Optional["InstanceLock"], quiet: bool = False) -> bool:
    if _take_over_requested():
        _wait_for_previous_copy()
    if not acquire_windows_mutex():
        if quiet:
            logger.info("Another copy holds the mutex; start-at-login launch leaves")
            return False
        logger.info("Another copy holds the single-instance mutex; asking it to show")
        diagnosis = InstanceLock()
        pid = diagnosis.holder_pid()
        if _ask_running_copy_to_show(pid):
            return False
        pids = find_running_copy_pids()
        pid = pid or (pids[0] if pids else 0)
        if _busy_copy_running():
            _explain_busy_copy()
            return False
        choice = _explain_already_running(focus_failed=True, pid=pid)
        if choice == "anyway":
            logger.warning("Opening beside the running copy at the user's request")
            return True
        if choice != "force":
            return False
        if not end_running_copy(pid):
            return _explain_force_failed(pid)
        lock = lock or diagnosis
        lock.drop()
        if lock.acquire() and acquire_windows_mutex():
            _hold_lock(app, lock)
            return True
        return False

    lock = lock or InstanceLock()
    if lock.acquire():
        # Builds without this guard hold neither the mutex nor the lock file.
        # The process table still knows such a copy is running, so a second
        # launch must ask it to show rather than open a second window beside it.
        pids = find_running_copy_pids()
        if pids:
            if quiet:
                logger.info(
                    "Another copy is running (pid %s); start-at-login launch leaves",
                    pids[0],
                )
                lock.release()
                return False
            logger.info("Another copy is running (pid %s); asking it to show", pids[0])
            if _ask_running_copy_to_show(pids[0]):
                lock.release()
                return False
            logger.info("The running copy did not answer the show request")
            # A copy built before this guard has no socket and may be hidden in
            # the tray, so there is no window to raise. Offer the same explicit
            # takeover as a guarded copy rather than exiting with no way in.
            if _busy_copy_running():
                _explain_busy_copy()
                lock.release()
                return False
            choice = _explain_already_running(focus_failed=True, pid=pids[0])
            if choice == "anyway":
                logger.warning("Opening beside pid %s at the user's request", pids[0])
                lock.release()
                return True
            if choice != "force":
                lock.release()
                return False
            for pid in pids:
                if not end_running_copy(pid):
                    open_anyway = _explain_force_failed()
                    lock.release()
                    return open_anyway
        _hold_lock(app, lock)
        return True

    if lock.unopenable():
        logger.warning(
            "The single-instance lock cannot be used here; starting without it"
        )
        return True

    pid = lock.holder_pid()
    if not pid or not _pid_alive(pid) or _is_clearly_foreign(pid):
        # Dead holder, or the pid now belongs to an unrelated program: the lock
        # is stale. Take it if we can; otherwise start rather than block.
        logger.info("Clearing a stale single-instance lock (pid %s)", pid or "unknown")
        if lock.drop() and lock.acquire():
            _hold_lock(app, lock)
            return True
        logger.warning("Could not clear the stale lock; starting anyway")
        return True

    # A live copy of this app holds the lock. Show its window; only a copy that
    # does not answer may be ended, and only if the user says so. A start-at-
    # login launch leaves instead: it must never raise a window by itself.
    if quiet:
        logger.info(
            "Another copy is running (pid %s); start-at-login launch leaves", pid
        )
        return False
    logger.info("Another copy is running (pid %s); asking it to show", pid)
    if _ask_running_copy_to_show(pid):
        return False
    if _busy_copy_running():
        _explain_busy_copy()
        return False
    choice = _explain_already_running(focus_failed=True, pid=pid)
    if choice == "anyway":
        logger.warning("Opening beside pid %s at the user's request", pid)
        return True
    if choice != "force":
        return False
    if end_running_copy(pid) and lock.drop() and lock.acquire():
        _hold_lock(app, lock)
        return True
    return _explain_force_failed(pid)
