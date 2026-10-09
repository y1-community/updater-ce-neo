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
    except (ImportError, SyntaxError, Exception):
        logger.debug("pyqt-liquidglass not available, using native PyObjC fallback")


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
        if _pyobjc_prepare_window(window):
            return True
    except Exception as e:
        logger.debug("PyObjC window preparation fallback skipped: %s", e)

    # Zero-dependency ctypes preparation fallback
    try:
        return _ctypes_prepare_window(window)
    except Exception as e:
        logger.debug("ctypes window preparation fallback skipped: %s", e)
        return False


def _ensure_seamless_titlebar(window: QMainWindow | QWidget) -> None:
    """Ensure NSWindow titlebar has no separator line and matches window glass material."""
    if not is_glass_supported():
        return
    view = _get_nsview(window)
    if view:
        try:
            ns_win = view.window()
            if ns_win:
                from AppKit import NSColor, NSWindowTitleHidden
                current_mask = ns_win.styleMask()
                # NSWindowStyleMaskFullSizeContentView = 1 << 15 (0x8000), NSWindowStyleMaskResizable = 1 << 3 (0x8)
                target_mask = current_mask | 0x8000 | 0x0008
                if (current_mask & 0x8008) != 0x8008:
                    ns_win.setStyleMask_(target_mask)
                ns_win.setTitlebarAppearsTransparent_(True)
                ns_win.setTitleVisibility_(NSWindowTitleHidden)
                ns_win.setOpaque_(False)
                ns_win.setBackgroundColor_(NSColor.clearColor())
                ns_win.setMovableByWindowBackground_(True)
                if hasattr(ns_win, "setTitlebarSeparatorStyle_"):
                    ns_win.setTitlebarSeparatorStyle_(1)  # NSTitlebarSeparatorStyleNone

                # On macOS 14+, suppress _NSTitlebarDecorationView which draws a solid opaque titlebar background
                content_view = ns_win.contentView()
                tf = content_view.superview() if content_view else None
                if tf:
                    for sub in tf.subviews():
                        if "TitlebarContainerView" in str(type(sub)):
                            for s in sub.subviews():
                                if "DecorationView" in str(type(s)) or "BackgroundView" in str(type(s)):
                                    s.setHidden_(True)
                                    if hasattr(s, "setAlphaValue_"):
                                        s.setAlphaValue_(0.0)
                enable_macos_zoom_button(window)
                apply_monochrome_window_buttons(window)
                return
        except Exception:
            pass

    # ctypes fallback if PyObjC is unavailable
    try:
        _ctypes_ensure_seamless_titlebar(window)
    except Exception:
        pass


def native_chrome_intact(window: QMainWindow | QWidget, dark: bool = True) -> bool:
    """True when the platform's seamless window chrome is still in place.

    Qt re-applies its own window flags whenever it reconfigures a window (a page
    switch that changes the window's size hint is enough), and that discards
    what was set natively: on macOS the full-size content view, on Windows the
    DWM attributes. Nothing in Qt signals it, so the state is read back here —
    these are native property reads, not subprocesses.

    ``True`` is also returned when the check cannot be made (unsupported
    platform or API): a false negative would only cost a redundant re-apply,
    which the caller avoids when this reports intact.
    """
    if sys.platform == "darwin":
        if not is_glass_supported():
            return True
        try:
            view = _get_nsview(window)
            ns_win = view.window() if view else None
            if ns_win:
                # NSWindowStyleMaskFullSizeContentView = 1 << 15
                if not (ns_win.styleMask() & 0x8000):
                    return False
                if not ns_win.titlebarAppearsTransparent():
                    return False
                if ns_win.titleVisibility() != 1:  # NSWindowTitleHidden = 1
                    return False
                return True
        except Exception:
            pass
        try:
            ns_win_ptr = _ctypes_get_nswindow(window)
            if not ns_win_ptr:
                return True
            objc = _get_ctypes_objc()
            if not objc:
                return True
            import ctypes
            ns_win = ctypes.c_void_p(ns_win_ptr)
            cur_mask = _ctypes_msg(objc, ns_win, "styleMask", restype=ctypes.c_ulong)
            if not (cur_mask & 0x8000):
                return False
            transparent = _ctypes_msg(objc, ns_win, "titlebarAppearsTransparent", restype=ctypes.c_bool)
            if not transparent:
                return False
            vis = _ctypes_msg(objc, ns_win, "titleVisibility", restype=ctypes.c_long)
            if vis != 1:
                return False
            return True
        except Exception:
            return True

    if sys.platform == "win32" or platform.system() == "Windows":
        try:
            import ctypes
            from ctypes import byref, c_int, sizeof

            hwnd = int(window.winId())
            dwm = ctypes.windll.dwmapi
            # DWMWA_CAPTION_COLOR (35) is DWMWA_COLOR_NONE (0xFFFFFFFE) when the
            # title bar is transparent; DWMWA_USE_IMMERSIVE_DARK_MODE (20, 19 on
            # older builds) carries the theme. Newer attributes are not readable
            # everywhere, so an unreadable one counts as intact.
            caption = c_int(0)
            if dwm.DwmGetWindowAttribute(hwnd, 35, byref(caption), sizeof(caption)) == 0:
                if caption.value != 0xFFFFFFFE:
                    return False
            dark_flag = c_int(0)
            for attr in (20, 19):
                if dwm.DwmGetWindowAttribute(hwnd, attr, byref(dark_flag), sizeof(dark_flag)) == 0:
                    if bool(dark_flag.value) != bool(dark):
                        return False
                    break
            return True
        except Exception:
            return True

    return True


def apply_native_window_buttons(window: QMainWindow | QWidget) -> bool:
    """Ensure native macOS window controls retain their original system/user colors (red, yellow, green / graphite)."""
    if not IS_MACOS:
        return False
    try:
        view = _get_nsview(window)
        if view:
            ns_window = view.window()
            if ns_window:
                for button_type in (0, 1, 2):
                    btn = ns_window.standardWindowButton_(button_type)
                    if btn:
                        if hasattr(btn, "layer") and btn.layer():
                            btn.layer().setFilters_(None)
                        btn.setEnabled_(True)
                        btn.setHidden_(False)
                enable_macos_zoom_button(window)
                return True
    except Exception as e:
        logger.debug("Could not restore native window buttons via PyObjC: %s", e)

    try:
        return _ctypes_apply_native_window_buttons(window)
    except Exception:
        return False


def apply_monochrome_window_buttons(window: QMainWindow | QWidget) -> bool:
    """Backwards compatibility alias for apply_native_window_buttons."""
    return apply_native_window_buttons(window)


def apply_glass(
    window: QMainWindow | QWidget,
    corner_radius: float = 20.0,
    padding: float = 0.0,
    sidebar_only: bool = False,
    dark: bool | None = None,
) -> bool:
    """Apply the Liquid Glass Quick Look effect to the window after window.show().

    On macOS 26+ (Golden Gate), utilizes NSGlassEffectView.
    On macOS 13–15 (Ventura through Sequoia), falls back to NSVisualEffectView (UnderWindowBackground).
    Safe no-op on Linux and Windows.
    """
    if not is_glass_supported():
        return False

    # Check if a native glass view is already installed on this window.
    # Re-applying glass unconditionally stacks multiple NSGlassEffectViews,
    # destroying translucency and turning the window into an opaque dark slab.
    try:
        view = _get_nsview(window)
        if view:
            ns_win = view.window()
            if ns_win:
                content_view = ns_win.contentView()
                tf = content_view.superview() if content_view else None
                glass_views = []
                if tf:
                    glass_views.extend([
                        s for s in tf.subviews()
                        if "Glass" in str(type(s)) or "VisualEffect" in str(type(s))
                    ])
                if not glass_views and content_view:
                    glass_views.extend([
                        s for s in content_view.subviews()
                        if "Glass" in str(type(s)) or "VisualEffect" in str(type(s))
                    ])
                if glass_views:
                    # Deduplicate: if multiple glass views were added, remove any extras
                    for extra in glass_views[1:]:
                        try:
                            extra.removeFromSuperview()
                        except Exception:
                            pass
                    primary_glass = glass_views[0]
                    target_rect = tf.bounds() if tf else content_view.bounds()
                    try:
                        primary_glass.setFrame_(target_rect)
                    except Exception:
                        pass
                    if not is_golden_gate_or_newer():
                        _sync_legacy_vibrancy(ns_win, primary_glass)
                    _ensure_seamless_titlebar(window)
                    apply_monochrome_window_buttons(window)
                    return True
    except Exception as e:
        logger.debug("Existing glass view check error: %s", e)

    if _has_pyqt_liquidglass and hasattr(_liquidglass_module, "apply_glass_to_window"):
        try:
            pad = (padding, padding, padding, padding) if isinstance(padding, (int, float)) else padding
            if hasattr(_liquidglass_module, "GlassOptions"):
                mat = getattr(_liquidglass_module.GlassMaterial, "UNDER_WINDOW_BACKGROUND", 21)
                bm = getattr(_liquidglass_module.BlendingMode, "BEHIND_WINDOW", 0)
                opts = _liquidglass_module.GlassOptions(
                    material=mat,
                    corner_radius=corner_radius,
                    blending_mode=bm,
                    padding=pad,
                )
                _liquidglass_module.apply_glass_to_window(window, opts)
            else:
                _liquidglass_module.apply_glass_to_window(
                    window,
                    corner_radius=corner_radius,
                    padding=padding,
                )
            _ensure_seamless_titlebar(window)
            apply_monochrome_window_buttons(window)
            return True
        except Exception as e:
            logger.warning("pyqt_liquidglass.apply_glass_to_window failed: %s", e)

    # Native PyObjC application fallback
    try:
        res = _pyobjc_apply_glass(window, corner_radius)
        if res:
            _ensure_seamless_titlebar(window)
            apply_monochrome_window_buttons(window)
            return True
    except Exception as e:
        logger.debug("PyObjC glass effect application failed: %s", e)

    # Native ctypes application fallback
    try:
        res = _ctypes_apply_glass(window, corner_radius)
        if res:
            _ensure_seamless_titlebar(window)
            apply_monochrome_window_buttons(window)
            return True
    except Exception as e:
        logger.debug("ctypes glass effect application failed: %s", e)

    return False


# Height of the in-window header that holds the app icon and title. Traffic
# lights are centered on this band so they line up with that row.
HEADER_CONTENT_HEIGHT = 44.0


def traffic_light_center_from_top(
    header_height: float = HEADER_CONTENT_HEIGHT,
    nudge_down: float = 0.0,
) -> float:
    """Distance from the top of the content view to the control's center.

    The 28px app icon is vertically centered in the header row, so this is
    ``header_height / 2``. ``nudge_down`` moves the control further down.
    """
    return (float(header_height) / 2.0) + float(nudge_down)


def traffic_light_origin_y(
    button_height: float,
    superview_height: float,
    header_height: float = HEADER_CONTENT_HEIGHT,
    nudge_down: float = 0.0,
) -> float:
    """Cocoa bottom-left y that centers a traffic light on the header row.

    The icon and title occupy the top ``header_height`` points of the window.
    A traffic-light superview uses a bottom-left origin and its top edge is the
    top of the window, so the button is placed by measuring down from that edge
    to the header's vertical center. ``nudge_down`` moves the button further
    down, in points. A short titlebar view may need a negative origin so the
    control can sit on the header row below that view.
    """
    height = max(float(button_height), 1.0)
    super_h = max(float(superview_height), height)
    center_from_top = traffic_light_center_from_top(header_height, nudge_down)
    return super_h - center_from_top - (height / 2.0)


def configure_traffic_lights(
    window: QMainWindow | QWidget,
    x_offset: int = 18,
    y_offset: int = 0,
) -> bool:
    """Place native macOS window controls on the header row.

    ``y_offset`` is an extra downward nudge in points. The default centers the
    controls on the icon and title. Safe no-op on non-macOS.
    """
    if not is_glass_supported():
        return False

    # The liquid-glass helper pins the buttons to the titlebar view's own
    # center, which sits above the app icon. Placement is done here instead.
    success = False
    try:
        success = _pyobjc_configure_traffic_lights(window, x_offset, y_offset)
    except Exception as e:
        logger.debug("Traffic lights inset fallback skipped: %s", e)
        success = False

    if not success:
        try:
            success = _ctypes_configure_traffic_lights(window, x_offset, y_offset)
        except Exception as e:
            logger.debug("ctypes traffic lights inset fallback skipped: %s", e)
            success = False

    enable_macos_zoom_button(window)
    apply_monochrome_window_buttons(window)
    return success


def ensure_window_maximize_enabled(window: QMainWindow | QWidget) -> bool:
    """Ensure window maximize button is enabled across macOS and Windows/Linux."""
    try:
        from PySide6.QtCore import Qt
        if not window.isVisible():
            flags = window.windowFlags() | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
            window.setWindowFlags(flags)
    except Exception:
        pass

    if IS_MACOS:
        enable_macos_zoom_button(window)
    return True


disable_window_maximize = ensure_window_maximize_enabled


def enable_macos_zoom_button(window: QMainWindow | QWidget) -> None:
    """Ensure zoom/maximize button on macOS is enabled, visible, and supports fullscreen."""
    if not IS_MACOS:
        return
    try:
        view = _get_nsview(window)
        if not view:
            return
        ns_win = view.window()
        if not ns_win:
            return
        zoom_btn = ns_win.standardWindowButton_(2)
        if zoom_btn:
            zoom_btn.setEnabled_(True)
            zoom_btn.setHidden_(False)
        if hasattr(ns_win, "setCollectionBehavior_"):
            behavior = ns_win.collectionBehavior()
            # Enable NSWindowCollectionBehaviorFullScreenPrimary (1 << 7)
            # Remove NSWindowCollectionBehaviorFullScreenNone (1 << 9)
            ns_win.setCollectionBehavior_((behavior & ~(1 << 9)) | (1 << 7))
    except Exception as e:
        logger.debug("Enabling macOS zoom button skipped: %s", e)


_disable_macos_zoom_button = enable_macos_zoom_button


def set_window_close_button_enabled(window: QMainWindow | QWidget, enabled: bool) -> bool:
    """Enable or disable the native window close button (e.g. during flashing)."""
    if IS_MACOS:
        try:
            view = _get_nsview(window)
            if not view:
                return False
            ns_win = view.window()
            if not ns_win:
                return False
            close_btn = ns_win.standardWindowButton_(0)
            if close_btn:
                close_btn.setEnabled_(enabled)
            return True
        except Exception as e:
            logger.debug("Could not toggle macOS close button: %s", e)
            return False
    elif sys.platform == "win32" or platform.system() == "Windows":
        try:
            import ctypes
            hwnd = int(window.winId())
            SC_CLOSE = 0xF060
            MF_BYCOMMAND = 0x00000000
            MF_GRAYED = 0x00000001
            MF_DISABLED = 0x00000002
            MF_ENABLED = 0x00000000
            hmenu = ctypes.windll.user32.GetSystemMenu(hwnd, False)
            if hmenu:
                flags = (MF_GRAYED | MF_DISABLED) if not enabled else MF_ENABLED
                ctypes.windll.user32.EnableMenuItem(hmenu, SC_CLOSE, MF_BYCOMMAND | flags)
            return True
        except Exception as e:
            logger.debug("Could not toggle Windows close button: %s", e)
            return False
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


def _sync_legacy_vibrancy(ns_window, glass_view=None) -> None:
    """Match pre-Liquid Glass vibrancy to the current light or dark appearance.

    Golden Gate is left alone: this returns immediately on macOS 26+.
    """
    if is_golden_gate_or_newer() or ns_window is None:
        return
    try:
        from .dark import is_dark
        dark_mode = bool(is_dark())
    except Exception:
        dark_mode = True
    try:
        import AppKit
        name = "NSAppearanceNameVibrantDark" if dark_mode else "NSAppearanceNameVibrantLight"
        appr = AppKit.NSAppearance.appearanceNamed_(getattr(AppKit, name, name))
        if not appr:
            return
        ns_window.setAppearance_(appr)
        if glass_view is not None:
            glass_view.setAppearance_(appr)
    except Exception as exc:
        logger.debug("Could not sync legacy vibrancy: %s", exc)


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

        # NSWindowStyleMaskFullSizeContentView = 1 << 15 (0x8000), NSWindowStyleMaskResizable = 1 << 3 (0x8)
        style_mask = ns_window.styleMask() | (1 << 15) | (1 << 3)
        ns_window.setStyleMask_(style_mask)
        ns_window.setTitlebarAppearsTransparent_(True)
        ns_window.setTitleVisibility_(NSWindowTitleHidden)
        ns_window.setOpaque_(False)
        ns_window.setMovableByWindowBackground_(True)
        import AppKit
        if is_golden_gate_or_newer():
            # Liquid Glass keeps the appearance this path already used.
            vibrant_dark = getattr(AppKit, "NSAppearanceNameVibrantDark", "NSAppearanceNameVibrantDark")
            appr = AppKit.NSAppearance.appearanceNamed_(vibrant_dark)
            if appr:
                ns_window.setAppearance_(appr)
        else:
            _sync_legacy_vibrancy(ns_window)
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
        superview = content_view.superview() if content_view else None
        if not superview:
            superview = content_view
        frame = superview.bounds()

        existing_glass = getattr(window, "_pyobjc_glass_view", None)
        if existing_glass is not None:
            try:
                if existing_glass.superview() != superview:
                    existing_glass.removeFromSuperview()
                    superview.addSubview_positioned_relativeTo_(existing_glass, -1, content_view)
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
            import AppKit
            glass_view = NSVisualEffectView.alloc().initWithFrame_(frame)
            # Use UnderWindowBackground material (21) for authentic translucent glass backdrop
            mat = getattr(AppKit, "NSVisualEffectMaterialUnderWindowBackground", 21)
            glass_view.setMaterial_(mat)
            glass_view.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            glass_view.setState_(NSVisualEffectStateActive)
            try:
                from .dark import is_dark
                dark_mode = is_dark()
            except Exception:
                dark_mode = True
            appr_name = "NSAppearanceNameVibrantDark" if dark_mode else "NSAppearanceNameVibrantLight"
            appr = AppKit.NSAppearance.appearanceNamed_(appr_name)
            if appr:
                glass_view.setAppearance_(appr)
            _sync_legacy_vibrancy(ns_window, glass_view)
            logger.info("Activated NSVisualEffectView vibrancy for macOS %s", ".".join(map(str, MACOS_VERSION)))

        # NSViewWidthSizable (2) | NSViewHeightSizable (16) = 18
        glass_view.setAutoresizingMask_(18)

        # Insert behind content_view in the root theme frame so Qt controls render crisp and sharp ON TOP of the glass
        if superview is not content_view:
            superview.addSubview_positioned_relativeTo_(glass_view, -1, content_view)
        else:
            content_view.addSubview_positioned_relativeTo_(glass_view, -1, None)
        window._pyobjc_glass_view = glass_view
        return True
    except Exception as e:
        logger.warning("Failed to apply PyObjC glass view: %s", e)
        return False


def _drop_button_constraints(view, buttons: tuple) -> None:
    """Remove Auto Layout rules that mention the traffic lights.

    The chrome watchdog runs about once a second. Leaving the previous
    constraints in place would stack a new set on every pass.
    """
    if view is None:
        return
    try:
        current = list(view.constraints())
    except Exception:
        return
    for constraint in current:
        try:
            first = constraint.firstItem()
            second = constraint.secondItem()
        except Exception:
            continue
        if first in buttons or second in buttons:
            try:
                view.removeConstraint_(constraint)
            except Exception:
                pass


def _pyobjc_configure_traffic_lights(
    window: QMainWindow | QWidget,
    x_offset: int,
    y_offset: int,
) -> bool:
    """Center the traffic lights on the header icon.

    A positive Auto Layout constant moves the first item down, so
    ``button.centerY = contentView.top + header/2`` sits the control on the
    same line as the app icon. The titlebar view's own center is higher than
    that row, which is why the buttons used to look too high.
    """
    view = _get_nsview(window)
    if not view:
        return False

    try:
        from AppKit import NSLayoutConstraint

        ns_window = view.window()
        if not ns_window:
            return False
        content = ns_window.contentView()
        if content is None:
            return False
        ancestor = content.superview() or content

        buttons = []
        for button_type in (0, 1, 2):
            btn = ns_window.standardWindowButton_(button_type)
            if btn is not None:
                btn.setEnabled_(True)
                btn.setHidden_(False)
                buttons.append(btn)
        if not buttons:
            return False

        for host in {ancestor, content, *(btn.superview() for btn in buttons)}:
            _drop_button_constraints(host, tuple(buttons))

        for btn in buttons:
            btn.setTranslatesAutoresizingMaskIntoConstraints_(False)
            host = btn.superview()
            if host is not None and hasattr(host, "setClipsToBounds_"):
                host.setClipsToBounds_(False)

        # NSLayoutAttributeLeading = 5, Trailing = 6, Top = 3, CenterY = 10.
        # NSLayoutRelationEqual = 0. A positive constant moves the item down.
        center = traffic_light_center_from_top(nudge_down=y_offset)
        gap = 6.0

        def pin(item, attr, to_item, to_attr, constant):
            constraint = (
                NSLayoutConstraint
                .constraintWithItem_attribute_relatedBy_toItem_attribute_multiplier_constant_(
                    item, attr, 0, to_item, to_attr, 1.0, float(constant),
                )
            )
            ancestor.addConstraint_(constraint)

        close_btn = buttons[0]
        pin(close_btn, 5, content, 5, x_offset)
        pin(close_btn, 10, content, 3, center)
        previous = close_btn
        for btn in buttons[1:]:
            pin(btn, 5, previous, 6, gap)
            pin(btn, 10, content, 3, center)
            previous = btn

        enable_macos_zoom_button(window)
        return True
    except Exception as e:
        logger.debug("Could not reposition traffic lights via PyObjC: %s", e)
        return False


# ---------------------------------------------------------------------------
# Zero-dependency ctypes fallback (macOS Ventura through Golden Gate+)
# ---------------------------------------------------------------------------

_ctypes_objc_lib = None


def _get_ctypes_objc():
    global _ctypes_objc_lib
    if _ctypes_objc_lib is not None:
        return _ctypes_objc_lib
    if not IS_MACOS:
        return None
    try:
        import ctypes
        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.objc_msgSend.restype = ctypes.c_void_p
        _ctypes_objc_lib = objc
        return objc
    except Exception:
        return None


def _ctypes_msg(objc, obj, sel_name: str, *args, restype=None, argtypes=None):
    import ctypes
    if not obj:
        return 0
    fn = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p)
    sel = objc.sel_registerName(sel_name.encode("ascii"))
    actual_res = restype or ctypes.c_void_p
    if argtypes is None:
        actual_args = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_void_p] * len(args)
    else:
        actual_args = [ctypes.c_void_p, ctypes.c_void_p] + argtypes
    func_type = ctypes.CFUNCTYPE(actual_res, *actual_args)
    return func_type(fn.value)(obj, sel, *args)


def _ctypes_get_nswindow(widget: QWidget) -> int | None:
    objc = _get_ctypes_objc()
    if not objc:
        return None
    try:
        import ctypes
        ptr = int(widget.winId())
        ns_win = _ctypes_msg(objc, ctypes.c_void_p(ptr), "window")
        return int(ns_win) if ns_win else None
    except Exception:
        return None


def _ctypes_prepare_window(window: QMainWindow | QWidget) -> bool:
    objc = _get_ctypes_objc()
    if not objc:
        return False
    try:
        import ctypes
        ns_win_ptr = _ctypes_get_nswindow(window)
        if not ns_win_ptr:
            return False
        ns_win = ctypes.c_void_p(ns_win_ptr)
        cur_mask = _ctypes_msg(objc, ns_win, "styleMask", restype=ctypes.c_ulong)
        target_mask = cur_mask | 0x8000 | 0x0008
        _ctypes_msg(objc, ns_win, "setStyleMask:", target_mask, argtypes=[ctypes.c_ulong])
        _ctypes_msg(objc, ns_win, "setTitlebarAppearsTransparent:", 1, argtypes=[ctypes.c_bool])
        _ctypes_msg(objc, ns_win, "setTitleVisibility:", 1, argtypes=[ctypes.c_long])
        _ctypes_msg(objc, ns_win, "setOpaque:", 0, argtypes=[ctypes.c_bool])
        nscolor = objc.objc_getClass(b"NSColor")
        clear_color = _ctypes_msg(objc, nscolor, "clearColor")
        _ctypes_msg(objc, ns_win, "setBackgroundColor:", clear_color, argtypes=[ctypes.c_void_p])
        _ctypes_msg(objc, ns_win, "setMovableByWindowBackground:", 1, argtypes=[ctypes.c_bool])
        return True
    except Exception as e:
        logger.debug("_ctypes_prepare_window skipped: %s", e)
        return False


def _ctypes_ensure_seamless_titlebar(window: QMainWindow | QWidget) -> None:
    objc = _get_ctypes_objc()
    if not objc:
        return
    try:
        import ctypes
        ns_win_ptr = _ctypes_get_nswindow(window)
        if not ns_win_ptr:
            return
        ns_win = ctypes.c_void_p(ns_win_ptr)
        cur_mask = _ctypes_msg(objc, ns_win, "styleMask", restype=ctypes.c_ulong)
        target_mask = cur_mask | 0x8000 | 0x0008
        if (cur_mask & 0x8008) != 0x8008:
            _ctypes_msg(objc, ns_win, "setStyleMask:", target_mask, argtypes=[ctypes.c_ulong])
        _ctypes_msg(objc, ns_win, "setTitlebarAppearsTransparent:", 1, argtypes=[ctypes.c_bool])
        _ctypes_msg(objc, ns_win, "setTitleVisibility:", 1, argtypes=[ctypes.c_long])
        _ctypes_msg(objc, ns_win, "setOpaque:", 0, argtypes=[ctypes.c_bool])
        nscolor = objc.objc_getClass(b"NSColor")
        clear_color = _ctypes_msg(objc, nscolor, "clearColor")
        _ctypes_msg(objc, ns_win, "setBackgroundColor:", clear_color, argtypes=[ctypes.c_void_p])
        _ctypes_msg(objc, ns_win, "setMovableByWindowBackground:", 1, argtypes=[ctypes.c_bool])
        _ctypes_enable_zoom_button(window)
        _ctypes_apply_native_window_buttons(window)
    except Exception as e:
        logger.debug("_ctypes_ensure_seamless_titlebar skipped: %s", e)


def _ctypes_enable_zoom_button(window: QMainWindow | QWidget) -> None:
    objc = _get_ctypes_objc()
    if not objc:
        return
    try:
        import ctypes
        ns_win_ptr = _ctypes_get_nswindow(window)
        if not ns_win_ptr:
            return
        ns_win = ctypes.c_void_p(ns_win_ptr)
        zoom_btn = _ctypes_msg(objc, ns_win, "standardWindowButton:", 2, argtypes=[ctypes.c_ulong])
        if zoom_btn:
            _ctypes_msg(objc, ctypes.c_void_p(zoom_btn), "setEnabled:", 1, argtypes=[ctypes.c_bool])
            _ctypes_msg(objc, ctypes.c_void_p(zoom_btn), "setHidden:", 0, argtypes=[ctypes.c_bool])
        beh = _ctypes_msg(objc, ns_win, "collectionBehavior", restype=ctypes.c_ulong)
        # Enable FullScreenPrimary (1 << 7), Remove FullScreenNone (1 << 9)
        new_beh = (beh & ~(1 << 9)) | (1 << 7)
        _ctypes_msg(objc, ns_win, "setCollectionBehavior:", new_beh, argtypes=[ctypes.c_ulong])
    except Exception:
        pass


def _ctypes_apply_native_window_buttons(window: QMainWindow | QWidget) -> bool:
    objc = _get_ctypes_objc()
    if not objc:
        return False
    try:
        import ctypes
        ns_win_ptr = _ctypes_get_nswindow(window)
        if not ns_win_ptr:
            return False
        ns_win = ctypes.c_void_p(ns_win_ptr)
        for b_type in (0, 1, 2):
            btn = _ctypes_msg(objc, ns_win, "standardWindowButton:", b_type, argtypes=[ctypes.c_ulong])
            if btn:
                layer = _ctypes_msg(objc, ctypes.c_void_p(btn), "layer")
                if layer:
                    _ctypes_msg(objc, ctypes.c_void_p(layer), "setFilters:", None, argtypes=[ctypes.c_void_p])
                _ctypes_msg(objc, ctypes.c_void_p(btn), "setEnabled:", 1, argtypes=[ctypes.c_bool])
                _ctypes_msg(objc, ctypes.c_void_p(btn), "setHidden:", 0, argtypes=[ctypes.c_bool])
        _ctypes_enable_zoom_button(window)
        return True
    except Exception:
        return False


def _ctypes_apply_glass(window: QMainWindow | QWidget, corner_radius: float) -> bool:
    objc = _get_ctypes_objc()
    if not objc:
        return False
    try:
        import ctypes

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class CGSize(ctypes.Structure):
            _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [("origin", CGPoint), ("size", CGSize)]

        ns_win_ptr = _ctypes_get_nswindow(window)
        if not ns_win_ptr:
            return False
        ns_win = ctypes.c_void_p(ns_win_ptr)
        content_view = ctypes.c_void_p(_ctypes_msg(objc, ns_win, "contentView"))
        if not content_view:
            return False

        fn = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p)
        bounds_fn = ctypes.CFUNCTYPE(CGRect, ctypes.c_void_p, ctypes.c_void_p)(fn.value)
        rect = bounds_fn(content_view, objc.sel_registerName(b"bounds"))

        # Look up NSGlassEffectView or NSVisualEffectView
        glass_cls = objc.objc_getClass(b"NSGlassEffectView") if is_golden_gate_or_newer() else None
        if not glass_cls:
            glass_cls = objc.objc_getClass(b"NSVisualEffectView")
        if not glass_cls:
            return False

        alloc_v = _ctypes_msg(objc, glass_cls, "alloc")
        init_fn = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, CGRect)(fn.value)
        glass_view = ctypes.c_void_p(init_fn(alloc_v, objc.sel_registerName(b"initWithFrame:"), rect))
        if not glass_view:
            return False

        # Configure material: 21 (UnderWindowBackground), blendingMode: 0 (BehindWindow), state: 1 (Active)
        _ctypes_msg(objc, glass_view, "setMaterial:", 21, argtypes=[ctypes.c_long])
        _ctypes_msg(objc, glass_view, "setBlendingMode:", 0, argtypes=[ctypes.c_long])
        _ctypes_msg(objc, glass_view, "setState:", 1, argtypes=[ctypes.c_long])
        _ctypes_msg(objc, glass_view, "setAutoresizingMask:", 18, argtypes=[ctypes.c_ulong])

        superview_ptr = _ctypes_msg(objc, content_view, "superview")
        superview = ctypes.c_void_p(superview_ptr) if superview_ptr else content_view
        parent_view = superview if superview.value else content_view
        rel_view = content_view if superview.value else None

        _ctypes_msg(objc, parent_view, "addSubview:positioned:relativeTo:", glass_view, -1, rel_view, argtypes=[ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p])
        window._ctypes_glass_view = glass_view
        _ctypes_ensure_seamless_titlebar(window)
        return True
    except Exception as e:
        logger.debug("_ctypes_apply_glass failed: %s", e)
        return False


def _ctypes_configure_traffic_lights(window: QMainWindow | QWidget, x_offset: int, y_offset: int) -> bool:
    objc = _get_ctypes_objc()
    if not objc:
        return False
    try:
        import ctypes

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        class CGSize(ctypes.Structure):
            _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

        class CGRect(ctypes.Structure):
            _fields_ = [("origin", CGPoint), ("size", CGSize)]

        ns_win_ptr = _ctypes_get_nswindow(window)
        if not ns_win_ptr:
            return False
        ns_win = ctypes.c_void_p(ns_win_ptr)
        fn = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p)
        frame_fn = ctypes.CFUNCTYPE(CGRect, ctypes.c_void_p, ctypes.c_void_p)(fn.value)
        set_origin_fn = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, CGPoint)(fn.value)
        sel_origin = objc.sel_registerName(b"setFrameOrigin:")
        sel_frame = objc.sel_registerName(b"frame")

        spacing = 20
        origin_y = None
        for i, b_type in enumerate((0, 1, 2)):
            btn = _ctypes_msg(objc, ns_win, "standardWindowButton:", b_type, argtypes=[ctypes.c_ulong])
            if btn:
                _ctypes_msg(objc, ctypes.c_void_p(btn), "setEnabled:", 1, argtypes=[ctypes.c_bool])
                _ctypes_msg(objc, ctypes.c_void_p(btn), "setHidden:", 0, argtypes=[ctypes.c_bool])
                frame = frame_fn(ctypes.c_void_p(btn), sel_frame)
                if origin_y is None:
                    superview = _ctypes_msg(objc, ctypes.c_void_p(btn), "superview")
                    super_h = HEADER_CONTENT_HEIGHT
                    if superview:
                        super_frame = frame_fn(ctypes.c_void_p(superview), sel_frame)
                        if super_frame.size.height > 0:
                            super_h = float(super_frame.size.height)
                    origin_y = traffic_light_origin_y(
                        float(frame.size.height or 14.0),
                        super_h,
                        nudge_down=y_offset,
                    )
                origin_x = float(x_offset + (i * spacing))
                set_origin_fn(ctypes.c_void_p(btn), sel_origin, CGPoint(origin_x, origin_y))
        _ctypes_enable_zoom_button(window)
        return True
    except Exception as e:
        logger.debug("_ctypes_configure_traffic_lights failed: %s", e)
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
                dark = is_dark()
                if IS_WINDOWS:
                    apply_windows_dark_titlebar(watched, dark)
                    apply_windows_acrylic(watched, dark=dark)
                elif IS_MACOS and is_glass_supported() and isinstance(watched, QDialog):
                    prepare_window_for_glass(watched)
                    apply_glass(watched, corner_radius=16.0, dark=dark)
            except Exception:
                pass
        return super().eventFilter(watched, event)


def apply_dialog_theme(dialog: QDialog) -> None:
    """Apply host OS native window decoration, titlebar styling, and backdrop to a dialog.

    - Windows: applies immersive dark/light mode titlebar, suppresses default accent color
      on caption bar, enables Acrylic / Aero glass frosted backdrop, and installs event filter so styling persists on Show.
    - macOS: configures Liquid Glass / translucency matching DiagnosticsDialog across all dialogs.
    """
    try:
        from .dark import is_dark
        dark = is_dark()
    except Exception:
        dark = False

    disable_window_maximize(dialog)

    if IS_WINDOWS:
        try:
            dialog.setAttribute(Qt.WA_TranslucentBackground, True)
        except Exception:
            pass
        apply_windows_dark_titlebar(dialog, dark)
        apply_windows_acrylic(dialog, dark=dark)
        try:
            dialog.installEventFilter(_DialogThemeWatcher.instance())
        except Exception:
            pass
    elif IS_MACOS and is_glass_supported():
        try:
            prepare_window_for_glass(dialog)
            apply_glass(dialog, corner_radius=16.0, dark=dark)
            try:
                dialog.installEventFilter(_DialogThemeWatcher.instance())
            except Exception:
                pass
        except Exception as e:
            logger.debug("apply_dialog_theme glass skipped: %s", e)
