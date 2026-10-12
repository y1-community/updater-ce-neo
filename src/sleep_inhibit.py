"""Cross-platform sleep inhibition during active firmware and theme installs.

Prevents the operating system from going to sleep or idling down while a
firmware installation, backend tool execution, or Rockbox theme placement
is actively in progress on Windows, macOS, and Linux.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# Windows SetThreadExecutionState flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


class SleepInhibitor:
    """Manages system sleep inhibition across Windows, macOS, and Linux."""

    def __init__(self):
        self._active: bool = False
        self._proc: Optional[subprocess.Popen] = None
        self._assertion_id: Optional[int] = None
        self._ref_count: int = 0

    @property
    def is_active(self) -> bool:
        return self._active

    def acquire(self, reason: str = "Firmware installation in progress") -> bool:
        """Prevent system sleep. Reference-counted so nested calls are safe."""
        self._ref_count += 1
        if self._active:
            return True

        self._active = True
        logger.info("Pausing system sleep (%s)...", reason)

        try:
            if sys.platform == "win32":
                self._acquire_windows()
            elif sys.platform == "darwin":
                self._acquire_macos(reason)
            else:
                self._acquire_linux(reason)
            return True
        except Exception as e:
            logger.warning("Could not inhibit system sleep: %s", e)
            return False

    def release(self) -> None:
        """Release sleep inhibition when reference count hits zero."""
        if self._ref_count > 1:
            self._ref_count -= 1
            return

        self._ref_count = 0
        if not self._active:
            return

        self._active = False
        logger.info("Restoring normal system sleep...")

        try:
            if sys.platform == "win32":
                self._release_windows()
            elif sys.platform == "darwin":
                self._release_macos()
            else:
                self._release_linux()
        except Exception as e:
            logger.warning("Could not release system sleep inhibition: %s", e)

    # -----------------------------------------------------------------------
    # Windows: SetThreadExecutionState
    # -----------------------------------------------------------------------
    def _acquire_windows(self) -> None:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadExecutionState(
                ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
            )
        except Exception as e:
            logger.debug("Windows SetThreadExecutionState failed: %s", e)

    def _release_windows(self) -> None:
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        except Exception as e:
            logger.debug("Windows SetThreadExecutionState restore failed: %s", e)

    # -----------------------------------------------------------------------
    # macOS: IOKit power assertion or caffeinate
    # -----------------------------------------------------------------------
    def _acquire_macos(self, reason: str) -> None:
        # Prefer native IOPMAssertion via IOKit
        try:
            iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
            cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")

            # kIOPMAssertionTypePreventUserIdleSystemSleep
            cf_type = cf.CFStringCreateWithCString(None, b"PreventUserIdleSystemSleep", 0x08000100)
            cf_name = cf.CFStringCreateWithCString(None, reason.encode("utf-8"), 0x08000100)
            assertion_id = ctypes.c_uint32(0)

            ret = iokit.IOPMAssertionCreateWithName(
                cf_type,
                255,  # kIOPMAssertionLevelOn
                cf_name,
                ctypes.byref(assertion_id),
            )
            cf.CFRelease(cf_type)
            cf.CFRelease(cf_name)

            if ret == 0:
                self._assertion_id = assertion_id.value
                return
        except Exception as e:
            logger.debug("macOS IOPMAssertion failed (%s), falling back to caffeinate", e)

        # Fallback to caffeinate subprocess
        try:
            self._proc = subprocess.Popen(
                ["caffeinate", "-d", "-i", "-m", "-s"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.debug("macOS caffeinate spawn failed: %s", e)

    def _release_macos(self) -> None:
        if self._assertion_id is not None:
            try:
                iokit = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
                iokit.IOPMAssertionRelease(ctypes.c_uint32(self._assertion_id))
            except Exception as e:
                logger.debug("macOS IOPMAssertionRelease failed: %s", e)
            self._assertion_id = None

        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    # -----------------------------------------------------------------------
    # Linux: systemd-inhibit
    # -----------------------------------------------------------------------
    def _acquire_linux(self, reason: str) -> None:
        try:
            self._proc = subprocess.Popen(
                [
                    "systemd-inhibit",
                    "--what=idle:sleep",
                    f"--why={reason}",
                    "--who=InnioasisUpdater",
                    "sleep",
                    "86400",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            logger.debug("Linux systemd-inhibit spawn failed: %s", e)

    def _release_linux(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=1.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


_GLOBAL_INHIBITOR = SleepInhibitor()


def acquire_sleep_inhibition(reason: str = "Firmware installation in progress") -> bool:
    """Acquire global sleep inhibition lock."""
    return _GLOBAL_INHIBITOR.acquire(reason)


def release_sleep_inhibition() -> None:
    """Release global sleep inhibition lock."""
    _GLOBAL_INHIBITOR.release()


def is_sleep_inhibited() -> bool:
    """True if sleep is currently inhibited."""
    return _GLOBAL_INHIBITOR.is_active
