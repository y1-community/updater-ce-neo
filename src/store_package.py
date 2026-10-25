"""Microsoft Store build awareness.

A Store build is an MSIX package, and an MSIX package is updated by the Store
itself — an app that downloaded a newer installer over itself would leave two
copies behind. So a packaged build offers "Open Microsoft Store" on the
updates page instead of an installer download, and the release is only offered
that way when its ``msstore.txt`` asset says the release is on the Store.

The Store's own updates page is reached with the documented
``ms-windows-store://`` protocol; it lists the user's library and the updates
waiting in it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

#: The Store's "Downloads and updates" page (Windows 11: Library → Updates).
STORE_UPDATES_URI = "ms-windows-store://downloadsandupdates"

#: Returned by GetCurrentPackageFullName for a build that is not packaged.
APPMODEL_ERROR_NO_PACKAGE = 15700


def is_packaged() -> bool:
    """True when this process runs from an MSIX/AppX package.

    ``GetCurrentPackageFullName`` is the documented probe: it answers
    ``APPMODEL_ERROR_NO_PACKAGE`` for an unpackaged (portable or Inno Setup)
    build and success inside a package, so the same executable behaves
    correctly in both.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_full_name = kernel32.GetCurrentPackageFullName
        get_full_name.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        get_full_name.restype = wintypes.LONG
        length = wintypes.UINT(0)
        result = get_full_name(ctypes.byref(length), None)
        return result != APPMODEL_ERROR_NO_PACKAGE
    except Exception:
        logger.debug("package identity probe failed", exc_info=True)
        return False


def open_store_updates() -> bool:
    """Send the user to the Store's updates page. False when it cannot open."""
    if sys.platform != "win32":
        return False
    try:
        # ShellExecute through os.startfile resolves registered protocols,
        # which is exactly what a ms-windows-store:// URI is.
        os.startfile(STORE_UPDATES_URI)  # noqa: S606 (documented URI, no file)
        return True
    except Exception:
        logger.debug("os.startfile could not open the Store", exc_info=True)
    try:
        subprocess.Popen(
            ["cmd", "/c", "start", "", STORE_UPDATES_URI],
            close_fds=True,
            shell=False,
        )
        return True
    except Exception:
        logger.debug("could not open the Store updates page", exc_info=True)
        return False
