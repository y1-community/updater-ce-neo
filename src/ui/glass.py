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

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QDialog, QMainWindow, QWidget

logger = logging.getLogger(__name__)

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32" or platform.system() == "Windows"
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


def is_windows_acrylic_supported() -> bool:
    """Return True if running on Windows 10 (1803+) or Windows 11 where Acrylic/Mica is supported."""
    if sys.platform != "win32" and platform.system() != "Windows":
        return False
    try:
        ver = sys.getwindowsversion()
        return ver.major >= 10 and ver.build >= 17134
    except Exception:
        return False


def is_glass_supported() -> bool:
    """Return True if running on supported macOS (Ventura through Golden Gate+)."""
    if not IS_MACOS:
        return False
    if not is_ventura_or_newer():
        return False
    try:
        from PySide6.QtGui import QGuiApplication
        app = QGuiApplication.instance()
        if app is not None and app.platformName() != "cocoa":
            return False
    except Exception:
        pass
    return True


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
    Safe no-op on Linux and Windows.
    """
    if not is_glass_supported():
        return False

    # Configure Qt translucent background
    window.setAttribute(Qt.WA_TranslucentBackground, True)

    if _has_pyqt_liquidglass and hasattr(_liquidglass_module, "prepare_window_for_glass"):
        try:
            _liquidglass_module.prepare_window_for_glass(window)
            _ensure_seamless_titlebar(window)
            return True
        except Exception as e:
            logger.warning("pyqt_liquidglass.prepare_window_for_glass failed: %s", e)

    # Native PyObjC preparation fallback
    try:
        return _pyobjc_prepare_window(window)
    except Exception as e:
        logger.debug("PyObjC window preparation fallback skipped: %s", e)
        return False


def _ensure_seamless_titlebar(window: QMainWindow | QWidget) -> None:
    """Ensure NSWindow titlebar has no separator line and matches window glass material."""
    if not is_glass_supported():
        return
    view = _get_nsview(window)
    if not view:
        return
    try:
        ns_win = view.window()
        if not ns_win:
            return
        from AppKit import NSColor, NSWindowTitleHidden
        current_mask = ns_win.styleMask()
        # NSWindowStyleMaskFullSizeContentView = 1 << 15 (0x8000)
        # Qt's QTabBar or widget visibility changes reset styleMask to default flags;
        # re-asserting 0x8000 preserves the full-window content extension under the titlebar.
        if not (current_mask & 0x8000):
            ns_win.setStyleMask_(current_mask | 0x8000)
        ns_win.setTitlebarAppearsTransparent_(True)
        ns_win.setTitleVisibility_(NSWindowTitleHidden)
        ns_win.setOpaque_(False)
        ns_win.setBackgroundColor_(NSColor.clearColor())
        if hasattr(ns_win, "setTitlebarSeparatorStyle_"):
            ns_win.setTitlebarSeparatorStyle_(1)  # NSTitlebarSeparatorStyleNone

        # On macOS 14+, suppress _NSTitlebarDecorationView which draws a solid opaque
        # titlebar background/line when windows are resized or views switch.
        content_view = ns_win.contentView()
        tf = content_view.superview() if content_view else None
        if tf:
            for sub in tf.subviews():
                if "TitlebarContainerView" in str(type(sub)):
                    for s in sub.subviews():
                        if "DecorationView" in str(type(s)):
                            s.setHidden_(True)
                            if hasattr(s, "setAlphaValue_"):
                                s.setAlphaValue_(0.0)
    except Exception:
        pass


def apply_glass(
    window: QMainWindow | QWidget,
    corner_radius: float = 16.0,
    padding: float = 0.0,
    sidebar_only: bool = False,
    dark: bool | None = None,
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
            if hasattr(_liquidglass_module, "GlassOptions"):
                pad = (padding, padding, padding, padding) if isinstance(padding, (int, float)) else padding
                opts = _liquidglass_module.GlassOptions(corner_radius=corner_radius, padding=pad)
                _liquidglass_module.apply_glass_to_window(window, opts)
            else:
                _liquidglass_module.apply_glass_to_window(
                    window,
                    corner_radius=corner_radius,
                    padding=padding,
                )
            _ensure_seamless_titlebar(window)
            return True
        except Exception as e:
            logger.warning("pyqt_liquidglass.apply_glass_to_window failed: %s", e)

    # Native PyObjC application fallback
    try:
        res = _pyobjc_apply_glass(window, corner_radius)
        _ensure_seamless_titlebar(window)
        return res
    except Exception as e:
        logger.warning("Native glass effect application failed: %s", e)
        return False


def configure_traffic_lights(
    window: QMainWindow | QWidget,
    x_offset: int = 18,
    y_offset: int = 0,
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
    if not is_glass_supported():
        return None
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

        existing_glass = getattr(window, "_pyobjc_glass_view", None)
        if existing_glass is not None:
            try:
                existing_glass.setFrame_(frame)
                return True
            except Exception:
                pass

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
        window._pyobjc_glass_view = glass_view
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


def apply_windows_dark_titlebar(window: QMainWindow | QWidget, dark: bool) -> bool:
    """Enable immersive dark mode and seamless transparent titlebar for Windows.

    Uses DwmSetWindowAttribute with DWMWA_USE_IMMERSIVE_DARK_MODE (attribute 20/19),
    DWMWA_CAPTION_COLOR (35) = DWMWA_COLOR_NONE (0xFFFFFFFE) to suppress the OS accent
    color and blend seamlessly with the Acrylic backdrop, and DWMWA_BORDER_COLOR (34).
    Safe no-op on macOS and Linux.
    """
    if sys.platform != "win32" and platform.system() != "Windows":
        return False
    try:
        import ctypes
        from ctypes import byref, c_int, sizeof

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        val = c_int(1 if dark else 0)
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(hwnd, attr, byref(val), sizeof(val))

        # Windows 11 (Build 22000+): Make title bar seamless and suppress accent color
        # DWMWA_CAPTION_COLOR = 35, DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE = 0xFFFFFFFE
        color_none = c_int(0xFFFFFFFE)
        dwm.DwmSetWindowAttribute(hwnd, 35, byref(color_none), sizeof(color_none))
        dwm.DwmSetWindowAttribute(hwnd, 34, byref(color_none), sizeof(color_none))

        # Title text color (DWMWA_TEXT_COLOR = 36): white for dark mode (0x00FFFFFF), dark for light mode (0x00000000)
        text_color = c_int(0x00FFFFFF if dark else 0x00000000)
        dwm.DwmSetWindowAttribute(hwnd, 36, byref(text_color), sizeof(text_color))

        # Also apply pywinstyles transparent titlebar if installed
        try:
            import pywinstyles
            pywinstyles.change_header_color(window, "transparent")
            pywinstyles.change_border_color(window, "transparent")
        except Exception:
            pass

        return True
    except Exception as e:
        logger.debug("Could not set Windows seamless titlebar: %s", e)
    return False


def apply_windows_acrylic(window: QMainWindow | QWidget, dark: bool = True) -> bool:
    """Apply native Windows 11 Acrylic material (or Win10 / Win7 Aero fallback).

    - Windows 11 22H2+ (Build 22621+): DwmExtendFrameIntoClientArea (-1, -1, -1, -1)
      and DwmSetWindowAttribute with DWMWA_SYSTEMBACKDROP_TYPE = 3 (DWMSBT_TRANSIENTWINDOW = Acrylic).
    - Windows 11 21H2: DWMWA_MICA_EFFECT = 1029.
    - Windows 10: SetWindowCompositionAttribute with ACCENT_ENABLE_ACRYLICBLURBEHIND (accent state 4).
    - Fallback: pywinstyles / Aero glass DwmExtendFrameIntoClientArea (-1, -1, -1, -1).
    Safe no-op on macOS and Linux.
    """
    if sys.platform != "win32" and platform.system() != "Windows":
        return False

    try:
        import ctypes
        from ctypes import byref, c_int, sizeof, Structure, pointer
        from ctypes.wintypes import DWORD, ULONG

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi

        # 1. Synchronize immersive dark mode titlebar attribute
        apply_windows_dark_titlebar(window, dark)

        # 2. Always extend frame into client area so DWM backdrop covers full window
        class MARGINS(Structure):
            _fields_ = [
                ("cxLeftWidth", c_int),
                ("cxRightWidth", c_int),
                ("cyTopHeight", c_int),
                ("cyBottomHeight", c_int),
            ]

        margins = MARGINS(-1, -1, -1, -1)
        dwm.DwmExtendFrameIntoClientArea(hwnd, byref(margins))

        class ACCENT_POLICY(Structure):
            _fields_ = [
                ("AccentState", DWORD),
                ("AccentFlags", DWORD),
                ("GradientColor", DWORD),
                ("AnimationId", DWORD),
            ]

        class WINDOW_COMPOSITION_ATTRIBUTES(Structure):
            _fields_ = [
                ("Attribute", DWORD),
                ("Data", ctypes.POINTER(ACCENT_POLICY)),
                ("SizeOfData", ULONG),
            ]

        build = sys.getwindowsversion().build if hasattr(sys, "getwindowsversion") else 0

        # Try pywinstyles if available
        try:
            import pywinstyles
            pywinstyles.apply_style(window, "acrylic")
        except Exception:
            pass

        # 3. Windows 11 22H2+ (Build 22621+): Official DWMWA_SYSTEMBACKDROP_TYPE
        if build >= 22621:
            try:
                # DWMSBT_TRANSIENTWINDOW = 3 (Acrylic material)
                backdrop_type = c_int(3)
                hr = dwm.DwmSetWindowAttribute(hwnd, 38, byref(backdrop_type), sizeof(backdrop_type))

                # Host backdrop accent policy
                accent = ACCENT_POLICY(5, 0, 0, 0)
                data = WINDOW_COMPOSITION_ATTRIBUTES(19, pointer(accent), sizeof(accent))
                ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, pointer(data))
                if dark:
                    data_dark = WINDOW_COMPOSITION_ATTRIBUTES(26, pointer(accent), sizeof(accent))
                    ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, pointer(data_dark))

                if hr == 0:
                    logger.info("Applied Windows 11 Acrylic backdrop (DWMWA_SYSTEMBACKDROP_TYPE=3)")
                    return True
            except Exception as e:
                logger.debug("Win11 DWMWA_SYSTEMBACKDROP_TYPE failed: %s", e)

        # 4. Windows 11 21H2 (Build 22000): DWMWA_MICA_EFFECT = 1029
        elif build >= 22000:
            try:
                mica_val = c_int(1)
                hr = dwm.DwmSetWindowAttribute(hwnd, 1029, byref(mica_val), sizeof(mica_val))
                if hr == 0:
                    logger.info("Applied Windows 11 21H2 Mica effect (DWMWA_MICA_EFFECT=1029)")
                    return True
            except Exception as e:
                logger.debug("Win11 21H2 Mica effect failed: %s", e)

        # 5. Fallback: Windows 10 SetWindowCompositionAttribute (Acrylic blur)
        try:
            gradient_color = 0x99202020 if dark else 0x99F0F0F0
            accent = ACCENT_POLICY(
                AccentState=4,  # ACCENT_ENABLE_ACRYLICBLURBEHIND
                AccentFlags=2,
                GradientColor=gradient_color,
                AnimationId=0,
            )
            data = WINDOW_COMPOSITION_ATTRIBUTES(
                Attribute=19,  # WCA_ACCENT_POLICY
                Data=pointer(accent),
                SizeOfData=sizeof(accent),
            )
            res = ctypes.windll.user32.SetWindowCompositionAttribute(hwnd, pointer(data))
            if res != 0:
                logger.info("Applied Windows 10 Acrylic blur via SetWindowCompositionAttribute")
                return True
        except Exception as e:
            logger.debug("Win10 SetWindowCompositionAttribute fallback failed: %s", e)

        return True

    except Exception as e:
        logger.debug("Could not apply Windows Acrylic backdrop: %s", e)

    return False


class _DialogThemeWatcher(QObject):
    """Event filter ensuring Windows DWM attributes persist when a dialog is shown."""

    _instance = None

    @classmethod
    def instance(cls) -> _DialogThemeWatcher:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            try:
                from .dark import is_dark
                apply_windows_dark_titlebar(watched, is_dark())
            except Exception:
                pass
        return super().eventFilter(watched, event)


def apply_dialog_theme(dialog: QDialog) -> None:
    """Apply host OS native window decoration, titlebar styling, and backdrop to a dialog.

    - Windows: applies immersive dark/light mode titlebar, suppresses default accent color
      on caption bar, and installs event filter so styling persists on Show.
    - macOS: configures Liquid Glass / vibrancy when supported.
    """
    try:
        from .dark import is_dark
        dark = is_dark()
    except Exception:
        dark = False

    if IS_WINDOWS:
        apply_windows_dark_titlebar(dialog, dark)
        try:
            dialog.installEventFilter(_DialogThemeWatcher.instance())
        except Exception:
            pass
    elif IS_MACOS and is_glass_supported():
        try:
            prepare_window_for_glass(dialog)
            apply_glass(dialog, corner_radius=12.0, dark=dark)
        except Exception as e:
            logger.debug("apply_dialog_theme glass skipped: %s", e)
