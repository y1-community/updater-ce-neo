"""macOS Liquid Glass & Vibrancy integration.

Targets macOS Ventura (13.0) through Golden Gate (26.0+) on both Intel (x86_64)
and Apple Silicon (arm64). Integrates ``pyqt-liquidglass`` with an in-process
PyObjC fallback and safe no-ops on Linux and Windows.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMainWindow, QWidget

logger = logging.getLogger(__name__)

IS_MACOS = sys.platform == "darwin"
ARCH = platform.machine().lower()  # 'arm64' or 'x86_64'


def get_macos_version() -> tuple[int, int, int]:
    """Return the detected macOS version as a (major, minor, patch) tuple.

    Returns (0, 0, 0) on non-macOS systems.
    """
    if not IS_MACOS:
        return (0, 0, 0)
    try:
        ver_str = platform.mac_ver()[0]
        if not ver_str:
            return (0, 0, 0)
        parts = [int(p) for p in ver_str.split(".") if p.isdigit()]
        while len(parts) < 3:
            parts.append(0)
        return (parts[0], parts[1], parts[2])
    except Exception:
        return (0, 0, 0)


MACOS_VERSION = get_macos_version()

# Compatibility checks:
# macOS 13.0 = Ventura (baseline target)
# macOS 14.0 = Sonoma
# macOS 15.0 = Sequoia
# macOS 26.0+ = Tahoe / "Golden Gate" (Liquid Glass introduction)
VENTURA_VERSION = (13, 0, 0)
GOLDEN_GATE_VERSION = (26, 0, 0)


def is_ventura_or_newer() -> bool:
    return IS_MACOS and MACOS_VERSION >= VENTURA_VERSION


def is_golden_gate_or_newer() -> bool:
    return IS_MACOS and MACOS_VERSION >= GOLDEN_GATE_VERSION


def is_glass_supported() -> bool:
    """Return True if running on supported macOS (Ventura through Golden Gate+)."""
    return is_ventura_or_newer()


# ---------------------------------------------------------------------------
# Liquid Glass / Vibrancy Implementation
# ---------------------------------------------------------------------------

_has_pyqt_liquidglass = False
_liquidglass_module: Any = None

if IS_MACOS:
    try:
        import pyqt_liquidglass as _lg
        _liquidglass_module = _lg
        _has_pyqt_liquidglass = True
        logger.info("Found pyqt-liquidglass library")
    except ImportError:
        logger.debug("pyqt-liquidglass not installed, using native PyObjC fallback if available")


def prepare_window_for_glass(window: QMainWindow | QWidget) -> bool:
    """Prepare window flags and translucent attributes before window.show().

    Must be called before the window is mapped/shown on macOS.
    Safe no-op on non-macOS.
    """
    if not is_glass_supported():
        return False

    # Configure Qt translucent background
    window.setAttribute(Qt.WA_TranslucentBackground, True)

    if _has_pyqt_liquidglass and hasattr(_liquidglass_module, "prepare_window_for_glass"):
        try:
            _liquidglass_module.prepare_window_for_glass(window)
            return True
        except Exception as e:
            logger.warning("pyqt_liquidglass.prepare_window_for_glass failed: %s", e)

    # Native PyObjC preparation fallback
    try:
        return _pyobjc_prepare_window(window)
    except Exception as e:
        logger.debug("PyObjC window preparation fallback skipped: %s", e)
        return False


def apply_glass(
    window: QMainWindow | QWidget,
    corner_radius: float = 12.0,
    padding: float = 0.0,
    sidebar_only: bool = False,
) -> bool:
    """Apply the Liquid Glass effect to the window after window.show().

    On macOS 26+ (Golden Gate), utilizes NSGlassEffectView.
    On macOS 13–15 (Ventura through Sequoia), falls back to NSVisualEffectView.
    Safe no-op on Linux and Windows.
    """
    if not is_glass_supported():
        return False

    if _has_pyqt_liquidglass and hasattr(_liquidglass_module, "apply_glass_to_window"):
        try:
            _liquidglass_module.apply_glass_to_window(
                window,
                corner_radius=corner_radius,
                padding=padding,
            )
            return True
        except Exception as e:
            logger.warning("pyqt_liquidglass.apply_glass_to_window failed: %s", e)

    # Native PyObjC application fallback
    try:
        return _pyobjc_apply_glass(window, corner_radius)
    except Exception as e:
        logger.warning("Native glass effect application failed: %s", e)
        return False


def configure_traffic_lights(
    window: QMainWindow | QWidget,
    x_offset: int = 18,
    y_offset: int = 18,
) -> bool:
    """Inset native macOS window traffic lights (close, minimize, zoom).

    Safe no-op on non-macOS.
    """
    if not is_glass_supported():
        return False

    if _has_pyqt_liquidglass and hasattr(_liquidglass_module, "setup_traffic_lights_inset"):
        try:
            _liquidglass_module.setup_traffic_lights_inset(
                window,
                x_offset=x_offset,
                y_offset=y_offset,
            )
            return True
        except Exception as e:
            logger.debug("pyqt_liquidglass.setup_traffic_lights_inset failed: %s", e)

    try:
        return _pyobjc_configure_traffic_lights(window, x_offset, y_offset)
    except Exception as e:
        logger.debug("Traffic lights inset fallback skipped: %s", e)
        return False


# ---------------------------------------------------------------------------
# In-process PyObjC Fallback Implementation (macOS 13 to macOS 26+)
# ---------------------------------------------------------------------------

def _get_nsview(widget: QWidget) -> Any | None:
    """Get the Cocoa NSView for a Qt widget."""
    try:
        import objc
        ptr = widget.winId()
        return objc.objc_object(c_void_p=int(ptr))
    except Exception:
        return None


def _pyobjc_prepare_window(window: QMainWindow | QWidget) -> bool:
    """Configure NSWindow style masks and titlebar transparency."""
    view = _get_nsview(window)
    if not view:
        return False

    try:
        ns_window = view.window()
        if not ns_window:
            return False

        from AppKit import (
            NSColor,
            NSWindowTitleHidden,
        )

        # NSWindowStyleMaskFullSizeContentView = 1 << 15
        style_mask = ns_window.styleMask() | (1 << 15)
        ns_window.setStyleMask_(style_mask)
        ns_window.setTitlebarAppearsTransparent_(True)
        ns_window.setTitleVisibility_(NSWindowTitleHidden)
        ns_window.setOpaque_(False)
        ns_window.setBackgroundColor_(NSColor.clearColor())
        return True
    except Exception as e:
        logger.debug("PyObjC window prepare error: %s", e)
        return False


def _pyobjc_apply_glass(window: QMainWindow | QWidget, corner_radius: float) -> bool:
    """Attach an NSGlassEffectView (macOS 26+) or NSVisualEffectView (macOS 13-15)."""
    view = _get_nsview(window)
    if not view:
        return False

    try:
        import objc
        from AppKit import (
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialSidebar,
            NSVisualEffectStateActive,
            NSVisualEffectView,
        )

        ns_window = view.window()
        if not ns_window:
            return False

        content_view = ns_window.contentView()
        frame = content_view.bounds()

        glass_view = None

        # Try NSGlassEffectView on macOS 26+ (Golden Gate / Tahoe)
        if is_golden_gate_or_newer():
            try:
                glass_cls = objc.lookUpClass("NSGlassEffectView")
                glass_view = glass_cls.alloc().initWithFrame_(frame)
                if corner_radius > 0 and hasattr(glass_view, "setCornerRadius_"):
                    glass_view.setCornerRadius_(corner_radius)
                logger.info("Activated NSGlassEffectView (Liquid Glass) on macOS Golden Gate")
            except (objc.nosuchclass_error, Exception) as err:
                logger.debug("NSGlassEffectView not found, falling back to NSVisualEffectView: %s", err)

        # Fallback to NSVisualEffectView for Ventura (13.x) through Sequoia (15.x)
        if glass_view is None:
            glass_view = NSVisualEffectView.alloc().initWithFrame_(frame)
            glass_view.setMaterial_(NSVisualEffectMaterialSidebar)
            glass_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            glass_view.setState_(NSVisualEffectStateActive)
            logger.info("Activated NSVisualEffectView vibrancy for macOS %s", ".".join(map(str, MACOS_VERSION)))

        # NSViewWidthSizable (2) | NSViewHeightSizable (16) = 18
        glass_view.setAutoresizingMask_(18)

        # Insert at the back: NSWindowBelow (-1)
        content_view.addSubview_positioned_relativeTo_(glass_view, -1, None)
        return True
    except Exception as e:
        logger.warning("Failed to apply PyObjC glass view: %s", e)
        return False


def _pyobjc_configure_traffic_lights(
    window: QMainWindow | QWidget,
    x_offset: int,
    y_offset: int,
) -> bool:
    """Position macOS standard traffic light buttons."""
    view = _get_nsview(window)
    if not view:
        return False

    try:
        ns_window = view.window()
        if not ns_window:
            return False

        # 0: close, 1: miniaturize, 2: zoom
        spacing = 20
        for i, button_type in enumerate((0, 1, 2)):
            btn = ns_window.standardWindowButton_(button_type)
            if btn:
                frame = btn.frame()
                origin_x = x_offset + (i * spacing)
                # In AppKit, (0,0) is bottom-left, so calculate from top
                origin_y = ns_window.frame().size.height - y_offset - frame.size.height
                btn.setFrameOrigin_((origin_x, origin_y))
        return True
    except Exception as e:
        logger.debug("Could not reposition traffic lights via PyObjC: %s", e)
        return False
