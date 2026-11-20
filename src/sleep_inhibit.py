"""Hold the host awake while the app is writing to a player.

A firmware flash or a Rockbox themes transfer writes to the player over USB.
If the host suspends in the middle of that write the player is left with a
half-written flash, and MTK hardware does not come back without a BROM
recovery — that is a brick, not a failed attempt. The app therefore holds a
power assertion for exactly as long as a task that touches the device is
running, and lets go the moment the task ends: idle, backgrounded and finished
runs must not keep the machine awake.

Holds are named. Each activity (an install session, one themes transfer, …)
holds its own name and releases only that one, so a themes stage finishing can
never let the machine sleep while a flash is still writing, and a duplicate
``begin`` for the same activity cannot strand a hold. :func:`hold_sleep` /
:func:`release_sleep` are the API; the older
``acquire/release_sleep_inhibition`` pair remains as a thin wrapper.

Per platform:

* **Windows** — a process-wide ``PowerCreateRequest`` /
  ``PowerSetRequest(PowerRequestSystemRequired)`` request, plus
  ``PowerRequestExecutionRequired`` so Modern Standby cannot suspend the app.
  The old ``ES_AWAYMODE_REQUIRED`` call was wrong twice over: away mode is not
  supported on battery-powered laptops (the whole call then fails and *no*
  assertion is held, which is how an install can sleep on a battery PC), and
  ``SetThreadExecutionState`` state dies with the thread that set it. That call
  is kept only as a fallback, held by a dedicated thread.
* **macOS** — ``IOPMAssertionCreateWithName`` with
  ``PreventUserIdleSystemSleep``, falling back to ``caffeinate -w <pid>`` so the
  helper exits with the app instead of pinning the Mac awake forever.
* **Linux** — ``systemd-inhibit`` (idle, sleep and lid switch), falling back to
  the GNOME/MATE/Cinnamon session inhibitors. The child gets
  ``PR_SET_PDEATHSIG`` so a crash cannot leave the inhibitor behind.

A watchdog re-asserts the assertion every few seconds in case the helper
process is killed or the OS drops it. ``INNIOASIS_NO_SLEEP_INHIBIT=1`` disables
all of it (tests, and a support escape hatch).
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Default name used by the legacy acquire/release pair.
_DEFAULT_REASON = "Software installation in progress"
# How often the watchdog checks that the assertion is still in place.
_WATCHDOG_SECONDS = 20.0

# Windows SetThreadExecutionState flags (fallback path only).
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

# POWER_REQUEST_TYPE values.
_POWER_REQUEST_SYSTEM_REQUIRED = 1
_POWER_REQUEST_EXECUTION_REQUIRED = 3
# REASON_CONTEXT flags / version.
_POWER_REQUEST_CONTEXT_VERSION = 0
_POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x00000001

if sys.platform == "win32":  # pragma: no cover - Windows-only structures
    from ctypes import wintypes

    class _REASON_DETAILED(ctypes.Structure):
        _fields_ = [
            ("LocalizedReasonModule", wintypes.HMODULE),
            ("LocalizedReasonId", wintypes.DWORD),
            ("ReasonStringCount", wintypes.DWORD),
            ("ReasonStrings", ctypes.POINTER(wintypes.LPWSTR)),
        ]

    class _REASON_UNION(ctypes.Union):
        _fields_ = [
            ("Detailed", _REASON_DETAILED),
            ("SimpleReasonString", wintypes.LPWSTR),
        ]

    class _REASON_CONTEXT(ctypes.Structure):
        _anonymous_ = ("Reason",)
        _fields_ = [
            ("Version", wintypes.DWORD),
            ("Flags", wintypes.DWORD),
            ("Reason", _REASON_UNION),
        ]

else:  # pragma: no cover - only the names matter off Windows
    wintypes = None
    _REASON_CONTEXT = None


def _disabled() -> bool:
    """True when inhibition is switched off by the environment."""
    return bool(os.environ.get("INNIOASIS_NO_SLEEP_INHIBIT"))


# ---------------------------------------------------------------------------
# Platform backends
# ---------------------------------------------------------------------------
class _Backend:
    """One platform's way of asking the OS to stay awake."""

    name = "none"

    def engage(self, reason: str) -> bool:
        raise NotImplementedError

    def disengage(self) -> None:
        raise NotImplementedError

    def ensure(self) -> None:
        """Re-assert the request if the helper behind it died."""

    @staticmethod
    def _spawn(argv, *, posix_pdeathsig: bool = False) -> Optional[subprocess.Popen]:
        kwargs = {}
        if posix_pdeathsig and os.name == "posix":
            # Kill the inhibitor with us: a killed app must not leave the
            # machine unable to sleep for the rest of the session.
            def _die_with_parent() -> None:
                try:
                    libc = ctypes.CDLL(None, use_errno=True)
                    PR_SET_PDEATHSIG = 1
                    libc.prctl(PR_SET_PDEATHSIG, 15, 0, 0, 0)
                except Exception:
                    pass

            kwargs["preexec_fn"] = _die_with_parent
        try:
            return subprocess.Popen(
                argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
        except Exception as e:
            logger.debug("Could not start %s: %s", argv[0], e)
            return None


class _WindowsBackend(_Backend):
    """Process-wide power request, with a dedicated-thread fallback."""

    name = "windows"

    def __init__(self) -> None:
        self._handle = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._reason = _DEFAULT_REASON

    def engage(self, reason: str) -> bool:
        self._reason = reason or _DEFAULT_REASON
        if self._handle is not None:
            return True
        if self._create_power_request():
            return True
        return self._engage_thread()

    def _create_power_request(self) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.PowerCreateRequest.argtypes = [ctypes.POINTER(_REASON_CONTEXT)]
            kernel32.PowerCreateRequest.restype = wintypes.HANDLE
            kernel32.PowerSetRequest.argtypes = [wintypes.HANDLE, ctypes.c_int]
            kernel32.PowerSetRequest.restype = wintypes.BOOL
            context = _REASON_CONTEXT()
            context.Version = _POWER_REQUEST_CONTEXT_VERSION
            context.Flags = _POWER_REQUEST_CONTEXT_SIMPLE_STRING
            context.SimpleReasonString = self._reason
            handle = kernel32.PowerCreateRequest(ctypes.byref(context))
            if not handle:
                logger.debug("PowerCreateRequest failed (%s)", ctypes.get_last_error())
                return False
            system_ok = bool(kernel32.PowerSetRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED))
            if not system_ok:
                kernel32.CloseHandle(handle)
                logger.debug("PowerSetRequest(SystemRequired) failed")
                return False
            # Keeps Modern Standby from suspending the process; older Windows
            # has no such type, and system-required alone is enough there.
            try:
                kernel32.PowerSetRequest(handle, _POWER_REQUEST_EXECUTION_REQUIRED)
            except Exception:
                pass
            self._handle = handle
            return True
        except Exception as e:
            logger.debug("Windows power request failed: %s", e)
            return False

    def _engage_thread(self) -> bool:
        """Hold ``ES_CONTINUOUS`` from a thread that outlives the caller."""
        try:
            kernel32 = ctypes.windll.kernel32
        except Exception as e:
            logger.debug("Windows kernel32 unavailable: %s", e)
            return False
        if self._thread is not None and self._thread.is_alive():
            return True
        self._stop.clear()

        def _hold() -> None:
            try:
                kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
            except Exception as e:
                logger.debug("SetThreadExecutionState failed: %s", e)
                return
            self._stop.wait()  # the request lives as long as this thread
            try:
                kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception:
                pass

        self._thread = threading.Thread(target=_hold, name="keep-awake", daemon=True)
        self._thread.start()
        return True

    def disengage(self) -> None:
        handle, self._handle = self._handle, None
        if handle:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.PowerClearRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED)
                kernel32.PowerClearRequest(handle, _POWER_REQUEST_EXECUTION_REQUIRED)
                kernel32.CloseHandle(handle)
            except Exception as e:
                logger.debug("Windows power request release failed: %s", e)
        self._stop.set()
        self._thread = None

    def ensure(self) -> None:
        if self._handle is not None:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        if self._create_power_request():
            return
        self._engage_thread()


class _MacBackend(_Backend):
    """IOKit power assertion, with ``caffeinate -w <pid>`` as the fallback."""

    name = "macos"

    def __init__(self) -> None:
        self._assertion_id: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._reason = _DEFAULT_REASON

    @staticmethod
    def _libraries():
        iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        cf = ctypes.cdll.LoadLibrary(
            "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation"
        )
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_uint32,
        ]
        cf.CFRelease.restype = None
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        # Without these prototypes ctypes truncates the 64-bit CFStringRef to
        # an int and IOKit dereferences garbage: a hard SIGSEGV, not an error.
        iokit.IOPMAssertionCreateWithName.restype = ctypes.c_int32
        iokit.IOPMAssertionCreateWithName.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        iokit.IOPMAssertionRelease.restype = ctypes.c_int32
        iokit.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
        return iokit, cf

    def engage(self, reason: str) -> bool:
        self._reason = reason or _DEFAULT_REASON
        if self._assertion_id is not None:
            return True
        try:
            iokit, cf = self._libraries()
            kCFStringEncodingUTF8 = 0x08000100
            cf_type = cf.CFStringCreateWithCString(
                None, b"PreventUserIdleSystemSleep", kCFStringEncodingUTF8
            )
            cf_name = cf.CFStringCreateWithCString(
                None, self._reason.encode("utf-8", "replace"), kCFStringEncodingUTF8
            )
            assertion_id = ctypes.c_uint32(0)
            ok = False
            if cf_type and cf_name:
                # 255 == kIOPMAssertionLevelOn
                ok = iokit.IOPMAssertionCreateWithName(
                    cf_type, 255, cf_name, ctypes.byref(assertion_id)
                ) == 0
            if cf_type:
                cf.CFRelease(cf_type)
            if cf_name:
                cf.CFRelease(cf_name)
            if ok:
                self._assertion_id = assertion_id.value
                return True
        except Exception as e:
            logger.debug("macOS IOPMAssertion failed (%s), using caffeinate", e)
        return self._spawn_caffeinate()

    def _spawn_caffeinate(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        # -w <pid>: caffeinate exits with the app, so a killed updater cannot
        # pin the Mac awake afterwards.
        self._proc = self._spawn(
            ["caffeinate", "-d", "-i", "-m", "-s", "-w", str(os.getpid())]
        )
        return self._proc is not None

    def disengage(self) -> None:
        assertion_id, self._assertion_id = self._assertion_id, None
        if assertion_id is not None:
            try:
                iokit, _cf = self._libraries()
                iokit.IOPMAssertionRelease(assertion_id)
            except Exception as e:
                logger.debug("IOPMAssertionRelease failed: %s", e)
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def ensure(self) -> None:
        if self._assertion_id is not None:
            return
        if self._proc is not None and self._proc.poll() is None:
            return
        if not self._spawn_caffeinate():
            # Try the native assertion again; a transient IOKit failure should
            # not cost the whole install its protection.
            self.engage(self._reason)


class _LinuxBackend(_Backend):
    """``systemd-inhibit`` or one of the desktop session inhibitors."""

    name = "linux"

    def __init__(self) -> None:
        self._proc: Optional[subprocess.Popen] = None
        self._argv: list = []
        self._reason = _DEFAULT_REASON

    def _candidates(self, reason: str) -> list:
        return [
            [
                "systemd-inhibit",
                "--what=idle:sleep:handle-lid-switch",
                "--who=Innioasis Updater",
                f"--why={reason}",
                "sleep",
                "infinity",
            ],
            ["gnome-session-inhibit", "--inhibit", "idle:suspend", "--reason", reason,
             "sleep", "infinity"],
            ["mate-session-inhibit", "--inhibit", "idle:suspend", "--reason", reason,
             "sleep", "infinity"],
            ["cinnamon-session-inhibit", "--inhibit", "idle:suspend", "--reason", reason,
             "sleep", "infinity"],
        ]

    def engage(self, reason: str) -> bool:
        self._reason = reason or _DEFAULT_REASON
        if self._proc is not None and self._proc.poll() is None:
            return True
        for argv in self._candidates(self._reason):
            if not shutil.which(argv[0]):
                continue
            proc = self._spawn(argv, posix_pdeathsig=True)
            if proc is not None:
                self._argv = argv
                self._proc = proc
                return True
        logger.warning("No sleep inhibitor available on this system")
        return False

    def disengage(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def ensure(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        argv = self._argv
        if not argv:
            self.engage(self._reason)
            return
        proc = self._spawn(argv, posix_pdeathsig=True)
        if proc is not None:
            self._proc = proc


def _make_backend() -> _Backend:
    if sys.platform == "win32" or os.name == "nt":
        return _WindowsBackend()
    if sys.platform == "darwin":
        return _MacBackend()
    return _LinuxBackend()


# ---------------------------------------------------------------------------
# Inhibitor
# ---------------------------------------------------------------------------
class SleepInhibitor:
    """Named holds on the host staying awake.

    A reason is either held or not, so a duplicate ``begin`` for the same
    activity cannot strand a hold, and releasing one activity leaves every
    other hold in place.
    """

    def __init__(self, backend: Optional[_Backend] = None) -> None:
        self._lock = threading.RLock()
        self._holds: Dict[str, bool] = {}
        self._backend = backend
        self._engaged = False
        self._watchdog: Optional[threading.Thread] = None
        self._wake = threading.Event()
        self._last_error: str = ""

    @property
    def is_active(self) -> bool:
        return bool(self._holds)

    @property
    def reasons(self) -> tuple:
        return tuple(self._holds)

    def hold(self, reason: str = _DEFAULT_REASON) -> bool:
        key = (reason or _DEFAULT_REASON).strip() or _DEFAULT_REASON
        if _disabled():
            logger.debug("Sleep inhibition disabled by environment; ignoring %s", key)
            return False
        with self._lock:
            was_active = bool(self._holds)
            self._holds[key] = True
            if was_active:
                return True
            try:
                backend = self._backend or _make_backend()
                self._backend = backend
                ok = backend.engage(key)
            except Exception as e:  # a backend bug must never break an install
                logger.warning("Sleep inhibition failed: %s", e)
                backend = self._backend
                ok = False
            self._engaged = True
            if not ok:
                self._last_error = f"{backend.name} backend could not engage"
                logger.warning("Could not inhibit system sleep (%s)", key)
            else:
                logger.info("Holding the system awake (%s)", key)
            self._start_watchdog()
            return ok

    def release(self, reason: Optional[str] = None) -> None:
        with self._lock:
            if reason is None:
                self._holds.clear()
            else:
                self._holds.pop((reason or "").strip(), None)
            if self._holds:
                return
            backend = self._backend
            if backend is not None and self._engaged:
                self._engaged = False
                backend.disengage()
                logger.info("Restoring normal system sleep")
            self._wake.set()

    # Backwards-compatible aliases -----------------------------------------
    def acquire(self, reason: str = _DEFAULT_REASON) -> bool:
        return self.hold(reason)

    def release_one(self, reason: str = _DEFAULT_REASON) -> None:
        self.release(reason)

    # ------------------------------------------------------------------
    def _start_watchdog(self) -> None:
        if self._watchdog is not None and self._watchdog.is_alive():
            return
        self._wake.clear()

        def _loop() -> None:
            while True:
                self._wake.wait(_WATCHDOG_SECONDS)
                with self._lock:
                    # A release followed by a fresh hold while this thread was
                    # waking must not end the watch: only "no holds" does.
                    if not self._holds:
                        self._wake.clear()
                        return
                    backend = self._backend
                    self._wake.clear()
                try:
                    if backend is not None:
                        backend.ensure()
                except Exception as e:
                    logger.debug("Sleep-inhibition watchdog failed: %s", e)

        self._watchdog = threading.Thread(
            target=_loop, name="sleep-inhibit-watchdog", daemon=True
        )
        self._watchdog.start()


_GLOBAL = SleepInhibitor()


def hold_sleep(reason: str = _DEFAULT_REASON) -> bool:
    """Hold the system awake for ``reason`` (idempotent)."""
    return _GLOBAL.hold(reason)


def release_sleep(reason: Optional[str] = None) -> None:
    """Release one ``reason`` (or every hold when ``reason`` is None)."""
    _GLOBAL.release(reason)


def release_all_sleep_holds() -> None:
    _GLOBAL.release(None)


def is_sleep_inhibited() -> bool:
    """True while at least one hold is active."""
    return _GLOBAL.is_active


def active_sleep_holds() -> tuple:
    return _GLOBAL.reasons


# Legacy names used by older code: the release side lets go of everything.
def acquire_sleep_inhibition(reason: str = _DEFAULT_REASON) -> bool:
    return hold_sleep(reason)


def release_sleep_inhibition() -> None:
    release_all_sleep_holds()


atexit.register(release_all_sleep_holds)
