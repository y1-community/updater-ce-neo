"""Interpreter-level guards that keep a C-extension refcount bug from aborting the app.

Why this exists
---------------
PySide6 6.12.0 on CPython 3.10/3.11 drops one reference to ``None`` on every
``QPainter.setPen()`` / ``QPainter.setFont()`` call. Before Python 3.12 ``None``
is a normal refcounted object, so a UI that paints continuously (sidebar,
segmented progress bar, install page) drives its count to zero within a minute
and CPython aborts with::

    Fatal Python error: none_dealloc: deallocating None

The abort lands on whatever line happens to drop the last reference (a paint
event, a layout pass, even ``urllib3`` on a worker thread), which is why the
crash reports pointed somewhere different every time.

Python 3.12+ makes ``None``/``True``/``False`` immortal (PEP 683), so the leak is
harmless there. On older interpreters :func:`pin_singletons` gives those
singletons the same property by raising their reference count far beyond
anything a leak can drain in a session. The builds pin a non-leaking PySide6 as
well (``requirements.txt``, ``scripts/check_pyside_refcount.py``); this is the
safety net for an interpreter/binding pairing that slips past them.
"""

from __future__ import annotations

import logging
import platform
import sys

logger = logging.getLogger(__name__)

# 2**48 references: at a million leaked decrefs per second that is ~9 years of
# runtime, while staying far below the Py_ssize_t ceiling on 64-bit builds.
_PIN_AMOUNT_64 = 1 << 48
# 32-bit builds (very old Windows/ARM hosts): 2**30 is still ~18 minutes at
# 1M/s and days at the app's real paint rate, without risking overflow.
_PIN_AMOUNT_32 = 1 << 30

_pinned = False


def _singletons_need_pinning() -> bool:
    if sys.version_info >= (3, 12):
        return False  # PEP 683 immortal objects.
    if platform.python_implementation() != "CPython":
        return False
    if hasattr(sys, "gettotalrefcount"):
        # Debug builds (Py_TRACE_REFS) lay the object header out differently,
        # and developers running one want to *see* the refcount error.
        return False
    return True


def pin_singletons() -> bool:
    """Make ``None``, ``True`` and ``False`` effectively immortal on CPython < 3.12.

    Returns True when the pin was applied (or already in place). Never raises.
    """
    global _pinned
    if _pinned:
        return True
    if not _singletons_need_pinning():
        return False
    try:
        import ctypes

        amount = _PIN_AMOUNT_64 if sys.maxsize > 2**32 else _PIN_AMOUNT_32
        for obj in (None, True, False):
            # ob_refcnt is the first Py_ssize_t of every non-debug CPython object.
            cell = ctypes.c_ssize_t.from_address(id(obj))
            before = sys.getrefcount(obj)
            cell.value += amount
            # Verify we really hit the refcount field; undo if the layout is
            # not what we expect (a build we do not recognise).
            if sys.getrefcount(obj) - before != amount:
                cell.value -= amount
                logger.warning("Refcount pin skipped: unexpected object layout")
                return False
        _pinned = True
        logger.info(
            "Pinned None/True/False refcounts (Python %s) to guard against "
            "C-extension refcount leaks",
            platform.python_version(),
        )
        return True
    except Exception:
        logger.debug("Refcount pin failed", exc_info=True)
        return False


def measure_none_leak(iterations: int = 256) -> float:
    """Return the per-call change in ``None``'s refcount for ``QPainter.setPen``.

    0.0 means the Qt binding is sound; a negative value means every pen/font
    change drains a ``None`` reference (PySide6 6.12.0 on Python < 3.12).
    Requires a ``QGuiApplication``. Returns 0.0 when it cannot be measured.
    """
    try:
        from PySide6.QtGui import QColor, QFont, QImage, QPainter

        img = QImage(8, 8, QImage.Format.Format_ARGB32)
        colour = QColor(0, 0, 0)
        font = QFont()

        def _cycle():
            p = QPainter(img)
            try:
                p.setPen(colour)
                p.setFont(font)
            finally:
                p.end()

        _cycle()  # warm caches
        before = sys.getrefcount(None)
        for _ in range(iterations):
            _cycle()
        return (sys.getrefcount(None) - before) / float(iterations)
    except Exception:
        logger.debug("None leak probe failed", exc_info=True)
        return 0.0


def report_binding_health() -> None:
    """Log the Qt binding version and whether it leaks ``None`` references."""
    try:
        import PySide6

        version = getattr(PySide6, "__version__", "?")
    except Exception:
        version = "?"
    if sys.version_info >= (3, 12):
        logger.info("Qt binding: PySide6 %s on Python %s", version, platform.python_version())
        return
    drift = measure_none_leak()
    if drift < 0:
        logger.warning(
            "PySide6 %s on Python %s leaks None references (%.2f per paint call); "
            "%s. Rebuild with requirements.txt (PySide6<6.12 for Python<3.12).",
            version,
            platform.python_version(),
            drift,
            "guarded by refcount pin" if _pinned else "NOT GUARDED, the app may abort",
        )
    else:
        logger.info("Qt binding: PySide6 %s on Python %s (refcount sound)", version, platform.python_version())
