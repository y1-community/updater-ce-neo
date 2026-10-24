"""Dark / light mode theming — Innioasis Lumen design system.

Two responsibilities:
1.  Detect the OS palette and expose a ``theme()`` singleton that owns the
    application's QPalette and colour tokens.
2.  Generate a QSS stylesheet from those tokens so the entire app is themed
    from a single source of truth.

Call ``apply_theme(app)`` once at startup.  Every widget should use the
colour helpers from this module instead of hard-coding hex literals.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import sys

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleFactory

logger = logging.getLogger(__name__)

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32" or platform.system() == "Windows"
IS_LINUX = sys.platform.startswith("linux") or platform.system() == "Linux"


def _factory_style(*names: str) -> str | None:
    """Return the installed QStyle key matching any of *names*, ignoring case."""
    by_lower = {key.lower(): key for key in QStyleFactory.keys()}
    for name in names:
        found = by_lower.get((name or "").lower())
        if found:
            return found
    return None


def native_style_candidates(
    *,
    system: str,
    offscreen: bool = False,
    desktop: str = "",
    style_override: str = "",
) -> tuple[str, ...]:
    """Style keys to try, most native first.

    Windows prefers the WinUI style (``windows11``). KDE and LXQt prefer
    Breeze. GNOME and the other free desktops prefer Adwaita. ``style_override``
    is ``QT_STYLE_OVERRIDE``, which Linux users set to pick their Qt style.
    """
    system = (system or "").lower()
    if system in ("darwin", "macos"):
        if offscreen:
            return ("fusion",)
        return ("macOS", "macintosh")
    if system in ("windows", "win32"):
        return ("windows11", "windowsvista", "windows")

    names: list[str] = []
    override = style_override.strip()
    if override:
        names.append(override)
    blob = desktop.lower()
    if any(token in blob for token in ("kde", "plasma", "lxqt")):
        names.extend(("breeze", "oxygen", "adwaita", "fusion"))
    else:
        names.extend(("adwaita", "adwaita-dark", "breeze", "fusion"))
    ordered: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(name)
    return tuple(ordered)


class _ThinWindowsScrollStyle(QProxyStyle):
    """Thin scrollbar for classic Windows styles that cannot draw WinUI bars.

    Only the scrollbar is replaced. Buttons, combos, and the rest of the
    layout stay on the native style. Colors come from the live palette.
    """

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: ANN001
        if metric == QStyle.PixelMetric.PM_ScrollBarExtent:
            return 8
        return super().pixelMetric(metric, option, widget)

    def styleHint(self, hint, option=None, widget=None, returnData=None):  # noqa: ANN001
        if hint == QStyle.StyleHint.SH_ScrollBar_Transient:
            return 1
        return super().styleHint(hint, option, widget, returnData)

    def subControlRect(self, control, option, subControl, widget=None):  # noqa: ANN001
        if control == QStyle.ComplexControl.CC_ScrollBar and subControl in (
            QStyle.SubControl.SC_ScrollBarAddLine,
            QStyle.SubControl.SC_ScrollBarSubLine,
        ):
            return QRect()
        return super().subControlRect(control, option, subControl, widget)

    def drawComplexControl(self, control, option, painter, widget=None):  # noqa: ANN001
        if control != QStyle.ComplexControl.CC_ScrollBar:
            super().drawComplexControl(control, option, painter, widget)
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        palette = option.palette
        painter.fillRect(option.rect, palette.color(QPalette.ColorRole.Window))
        slider = self.subControlRect(
            control, option, QStyle.SubControl.SC_ScrollBarSlider, widget,
        )
        if slider.isValid() and slider.width() > 1 and slider.height() > 1:
            color = palette.color(QPalette.ColorRole.Mid)
            if option.state & QStyle.StateFlag.State_MouseOver:
                color = palette.color(QPalette.ColorRole.ButtonText)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            thumb = slider.adjusted(1, 1, -1, -1)
            radius = max(1, min(thumb.width(), thumb.height()) / 2)
            painter.drawRoundedRect(thumb, radius, radius)
        painter.restore()


class _ClassicWindowsHoverStyle(QProxyStyle):
    """Hover wash drawn after the native Windows button.

    The classic ``windows`` style only changes when a button is pressed, so
    those buttons get a translucent accent wash. WinUI (``windows11``) paints
    a hot state, but a fully replaced dark palette often leaves that hot state
    the same color as the idle button. WinUI buttons therefore get a neutral
    veil. The default button is left to the WinUI accent fill. Pressed and
    checked buttons keep the base style's own look. Sidebar rows paint
    themselves and never reach here.
    """

    def drawControl(self, element, option, painter, widget=None):  # noqa: ANN001
        super().drawControl(element, option, painter, widget)
        if element != QStyle.ControlElement.CE_PushButton:
            return
        base = self.baseStyle()
        name = ((base.objectName() if base is not None else "") or "").lower()
        from PySide6.QtWidgets import QStyleOptionButton

        from .sidebar import classic_windows_style_needs_hover

        features = getattr(option, "features", QStyleOptionButton.ButtonFeature(0))
        is_default = bool(features & QStyleOptionButton.ButtonFeature.DefaultButton)
        is_winui = name == "windows11"
        # The WinUI style already fills the default button with the accent.
        # A veil on top of that fill hides it.
        if is_winui and is_default:
            return
        if name and not is_winui and not classic_windows_style_needs_hover(name):
            return
        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        hovered = bool(state & QStyle.StateFlag.State_MouseOver)
        pressed = bool(state & (QStyle.StateFlag.State_Sunken | QStyle.StateFlag.State_On))
        if not (enabled and hovered) or pressed:
            return
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if is_winui:
            window = option.palette.color(QPalette.ColorRole.Window)
            wash = QColor(255, 255, 255, 32) if window.lightness() < 140 else QColor(0, 0, 0, 22)
        else:
            wash = QColor(option.palette.color(QPalette.ColorRole.Highlight))
            wash.setAlpha(40)
        painter.setBrush(wash)
        painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 4, 4)
        painter.restore()


def setup_native_app_style(app: QApplication) -> str:
    """Apply the host platform's native QStyle.

    - macOS: ``macOS`` / ``macintosh`` (Aqua)
    - Windows: ``windows11`` (WinUI), then ``windowsvista`` / ``windows``
    - Linux: the desktop's style (Breeze, Adwaita) or Fusion

    The offscreen plugin used by headless tests cannot draw Aqua, so it keeps
    Fusion. A real desktop session still gets the platform style.
    """
    offscreen = os.environ.get("QT_QPA_PLATFORM") == "offscreen"
    if IS_MACOS:
        system = "darwin"
    elif IS_WINDOWS:
        system = "windows"
    else:
        system = "linux"
    desktop = " ".join(
        os.environ.get(name, "")
        for name in (
            "XDG_CURRENT_DESKTOP",
            "DESKTOP_SESSION",
            "XDG_SESSION_DESKTOP",
        )
    )
    if os.environ.get("KDE_FULL_SESSION"):
        desktop = f"{desktop} kde"
    # Aqua cannot be forced under the offscreen plugin. On Linux, honor the
    # desktop's own QT_STYLE_OVERRIDE so Kvantum and similar styles still win.
    override = ""
    if system == "linux" and not getattr(sys, "frozen", False) and "APPDIR" not in os.environ:
        from PySide6.QtCore import QCoreApplication
        existing = set(QCoreApplication.libraryPaths())
        for path in (
            "/usr/lib/qt6/plugins",
            "/usr/lib64/qt6/plugins",
            "/usr/lib/x86_64-linux-gnu/qt6/plugins",
            "/usr/lib/aarch64-linux-gnu/qt6/plugins",
            "/usr/local/lib/qt6/plugins",
        ):
            if path not in existing and os.path.isdir(path):
                QCoreApplication.addLibraryPath(path)
        override = os.environ.get("QT_STYLE_OVERRIDE", "")
        # The desktop's platform theme (Plasma, GNOME, Kvantum via qt6ct) has
        # already chosen the user's Qt style. Keep it, so its buttons and combos
        # keep their own depth. Only fall back to Breeze/Adwaita when the host
        # left Qt on the generic Fusion style.
        current = app.style().objectName()
        if current and current.lower() != "fusion":
            return current
    candidates = native_style_candidates(
        system=system,
        offscreen=offscreen and IS_MACOS,
        desktop=desktop,
        style_override=override,
    )
    key = _factory_style(*candidates)
    if key:
        if IS_WINDOWS:
            # windows11 is WinUI (thin bars, fluent combos). Older Windows keep
            # the same layout on the classic style, with a thin palette-colored
            # scrollbar because windowsvista cannot draw WinUI bars.
            style = _ClassicWindowsHoverStyle(key)
            if key.lower() != "windows11":
                style = _ThinWindowsScrollStyle(style)
            app.setStyle(style)
        else:
            # Leave checkboxes and any native switch indicators to the host
            # style; do not replace them with a hand-painted approximation.
            app.setStyle(key)
        return key
    return app.style().objectName() or "fusion"


_RGBA_RE = re.compile(
    r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([0-9.]+)\s*\)",
    re.IGNORECASE,
)


def palette_color(value: str, *, over: str | None = None) -> QColor:
    """A ``QColor`` for a token.

    Stylesheets accept CSS ``rgba()``. ``QColor`` does not, and an invalid
    color becomes black, which painted light-theme buttons as black plates.
    When a base color is given, the translucent token is flattened onto it.
    """
    direct = QColor(value)
    if direct.isValid():
        return direct
    match = _RGBA_RE.fullmatch((value or "").strip())
    if match is None:
        return QColor("#000000")
    red, green, blue = (int(match.group(index)) for index in (1, 2, 3))
    alpha = float(match.group(4))
    if alpha > 1:
        alpha /= 255.0
    alpha = min(1.0, max(0.0, alpha))
    if over is None or alpha >= 1:
        color = QColor(red, green, blue)
        color.setAlphaF(alpha)
        return color
    base = QColor(over)
    if not base.isValid():
        color = QColor(red, green, blue)
        color.setAlphaF(alpha)
        return color
    return QColor(
        int(red * alpha + base.red() * (1.0 - alpha)),
        int(green * alpha + base.green() * (1.0 - alpha)),
        int(blue * alpha + base.blue() * (1.0 - alpha)),
    )


def _hex_rgba(value: str, alpha: float) -> str:
    """CSS rgba() for a stylesheet. ``QColor`` cannot parse this string."""
    color = QColor(value)
    if not color.isValid():
        return value
    amount = min(1.0, max(0.0, float(alpha)))
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {amount:.3f})"


# ---------------------------------------------------------------------------
# Theme tokens
# ---------------------------------------------------------------------------

def get_native_accent_color(palette: QPalette | None = None) -> tuple[str, str]:
    """Return (accent_hex, accent_text_hex) matching host OS / desktop environment.

    Queries AppKit controlAccentColor on macOS, QPalette.Accent (Qt 6.6+)
    on Windows (DWM accent) and Linux (KDE/GNOME accent), strictly maintaining
    WCAG AA text contrast.
    """
    if sys.platform == "darwin":
        try:
            from AppKit import NSColor
            c = NSColor.controlAccentColor().colorUsingColorSpaceName_("NSCalibratedRGBColorSpace")
            if c:
                r = int(c.redComponent() * 255)
                g = int(c.greenComponent() * 255)
                b = int(c.blueComponent() * 255)
                accent = f"#{r:02x}{g:02x}{b:02x}"
                luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
                text = "#000000" if luminance > 0.6 else "#ffffff"
                return accent, text
        except Exception:
            pass

    if IS_WINDOWS:
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\DWM",
            )
            val, _ = winreg.QueryValueEx(key, "AccentColor")
            winreg.CloseKey(key)
            r = val & 0xFF
            g = (val >> 8) & 0xFF
            b = (val >> 16) & 0xFF
            accent = f"#{r:02x}{g:02x}{b:02x}"
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            text = "#000000" if luminance > 0.6 else "#ffffff"
            return accent, text
        except Exception:
            pass

    if palette is not None and hasattr(QPalette.ColorRole, "Accent"):
        acc = palette.color(QPalette.ColorRole.Accent)
        if acc.isValid() and acc.name().lower() not in ("#000000", "#ffffff", "#323232", "#23272e"):
            r, g, b = acc.red(), acc.green(), acc.blue()
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            text = "#000000" if luminance > 0.6 else "#ffffff"
            return acc.name(), text

    if palette is not None:
        hl = palette.color(QPalette.ColorRole.Highlight)
        if hl.isValid() and hl.name().lower() not in ("#000000", "#ffffff"):
            r, g, b = hl.red(), hl.green(), hl.blue()
            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            text = "#000000" if luminance > 0.6 else "#ffffff"
            return hl.name(), text

    return "#2563eb", "#ffffff"


def is_system_dark_mode() -> bool:
    """Accurately determine whether the host OS is currently in dark mode.

    Never relies on window surface colors (which can report #000000 when translucent).
    Queries the host platform first, Qt styleHints second, and standard palette last.
    """
    # 1. macOS: AppleInterfaceStyle defaults / AppKit effectiveAppearance
    if sys.platform == "darwin":
        try:
            import subprocess
            res = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                timeout=1,
            )
            if res.returncode == 0 and "dark" in res.stdout.strip().lower():
                return True
            if res.returncode != 0:
                # Key does not exist in light mode
                return False
        except Exception:
            pass

        try:
            from AppKit import NSApp
            app_inst = NSApp()
            if app_inst:
                appr = app_inst.effectiveAppearance()
                if appr:
                    name = str(appr.name()).lower()
                    if "dark" in name:
                        return True
                    if "light" in name or "aqua" in name:
                        return False
        except Exception:
            pass

    # 2. Windows: Registry check for AppsUseLightTheme
    if sys.platform == "win32" or platform.system() == "Windows":
        try:
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            winreg.CloseKey(key)
            return val == 0
        except Exception:
            pass

    # 3. Linux: FreeDesktop portal / GNOME / KDE color-scheme
    if sys.platform.startswith("linux") or platform.system() == "Linux":
        try:
            import subprocess
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                capture_output=True,
                text=True,
                timeout=1,
            ).stdout.strip().strip("'\"")
            if "dark" in out.lower():
                return True
            if "light" in out.lower() or "default" in out.lower():
                return False
        except Exception:
            pass

    # 4. Qt StyleHints (Qt 6.5+)
    try:
        app = QApplication.instance()
        if app:
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                scheme = hints.colorScheme()
                if scheme == Qt.ColorScheme.Dark:
                    return True
                elif scheme == Qt.ColorScheme.Light:
                    return False
    except Exception:
        pass

    # 5. Fallback: inspect standard application palette (NOT window surface)
    try:
        app = QApplication.instance()
        if app:
            pal = app.palette()
            txt = pal.color(QPalette.ColorRole.WindowText)
            if txt.isValid():
                return txt.lightness() > 160
    except Exception:
        pass

    return False


def _palette_is_dark(palette: QPalette) -> bool:
    """True when the desktop's window colour is dark.

    Plasma and other desktops theme any material colour, so the window
    colour decides the token set, not a light or dark setting.
    """
    window = palette.color(QPalette.ColorRole.Window)
    return window.isValid() and window.lightness() < 128


# Styles that expose the desktop's own colour scheme through
# ``standardPalette()`` rather than only through their palette instance.
_LIVE_PALETTE_STYLES = ("breeze", "oxygen", "kvantum", "adwaita")


def _linux_system_palette(app: QApplication) -> QPalette:
    """Read the Linux desktop palette without trusting an app-owned palette.

    The palette the platform theme installed before the app set one of its own
    is what the window is drawn on, so it outranks ``QStyle.standardPalette()``.
    Breeze's standard palette is built from its own scheme, which is not always
    the scheme the platform theme applied to this app (Plasma hands over the
    current scheme, including per-app overrides): when the two disagreed the
    app painted Breeze's dark tokens on a white window, with dark cards, dark
    sidebar rows and white combo text in white fields.
    """
    style_name = (app.style().objectName() or "").lower()
    if getattr(_state, "linux_palette_from_event", False) and _state.native_palette is not None:
        return QPalette(_state.native_palette)
    if _state.platform_palette is not None:
        candidate = QPalette(_state.platform_palette)
        if candidate.color(QPalette.ColorRole.Window).isValid():
            return candidate
    if style_name in _LIVE_PALETTE_STYLES:
        try:
            palette = QPalette(app.style().standardPalette())
            if palette.color(QPalette.ColorRole.Window).isValid():
                return palette
        except Exception:
            pass
    if _state.native_palette is not None:
        return QPalette(_state.native_palette)
    try:
        from PySide6.QtGui import QGuiApplication

        palette = QPalette(QGuiApplication.palette())
        if palette.color(QPalette.ColorRole.Window).isValid():
            return palette
    except Exception:
        pass
    try:
        return QPalette(app.style().standardPalette())
    except Exception:
        return QPalette(app.palette())





class _Tokens:
    """Flat namespace of colour strings for a single theme (light or dark).

    Enforces WCAG AAA compliance:
    - In Light Mode: Very black fonts (#000000, #111827) for crisp legibility
      against white surfaces and light frosted Liquid Glass.
    - In Dark Mode: Very white fonts (#ffffff, #f9fafb) for crisp legibility
      against dark surfaces and dark smoked Liquid Glass.
    """

    def __init__(
        self,
        dark: bool,
        pure_black: bool = False,
        palette: QPalette | None = None,
        *,
        use_native_linux_colors: bool = False,
    ):
        d = dark
        b = dark and pure_black
        os_accent, os_accent_text = get_native_accent_color(palette)

        if d:
            # Surfaces — Dark mode
            if b:
                self.bg         = "#000000"
                self.bg_card    = "#0b0f19"
                self.bg_elev    = "#111827"
                self.bg_input   = "#000000"
                self.bg_hover   = "#1e293b"
                self.bg_nav     = "#000000"
                self.bg_status  = "#0b0f19"
                self.bg_tooltip = "#111827"
            else:
                self.bg         = "#1e2227"
                self.bg_card    = "#252932"
                self.bg_elev    = "#2b303c"
                self.bg_input   = "#181b20"
                self.bg_hover   = "rgba(255, 255, 255, 0.08)"
                self.bg_nav     = "#1e2227"
                self.bg_status  = "#181b20"
                self.bg_tooltip = "#252932"

            # Text — Dark mode: Very white fonts for maximum readability
            self.fg         = "#ffffff"
            self.fg_dim     = "#f9fafb"
            self.fg_muted   = "#e5e7eb"
            self.subtext    = self.fg_muted
            self.fg_primary = os_accent

            # Borders
            self.border        = "#374151" if b else "#3e4451"
            self.border_strong = "#4b5563" if b else "#525860"
            self.border_focus  = os_accent

            # Accent
            self.accent       = os_accent
            self.accent_hover = QColor(os_accent).lighter(115).name()
            self.accent_active = QColor(os_accent).darker(115).name()
            self.accent_bg    = QColor(os_accent).darker(300).name()
            acc_txt = QColor(os_accent).lighter(200).name()
            def _rl(c):
                c = c.lstrip('#')
                r, g, b = [int(c[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
                r = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
                g = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
                b = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4
                return 0.2126 * r + 0.7152 * g + 0.0722 * b
            def _cr(c1, c2):
                l1, l2 = _rl(c1), _rl(c2)
                return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)
            if _cr(acc_txt, self.accent_bg) < 4.5:
                acc_txt = "#ffffff"
            self.accent_text  = acc_txt

            self.nav_active      = os_accent
            self.nav_active_text = os_accent_text

            # Status Badges
            self.status_idle       = ("#ffffff", "#334155")
            self.status_idle_fg    = self.status_idle
            self.status_selected   = ("#ffffff", os_accent)
            self.status_sel_fg     = self.status_selected
            self.status_connected  = ("#6ee7b7", "#064e3b")
            self.status_conn_fg    = self.status_connected
            self.status_disconn    = ("#fca5a5", "#7f1d1d")
            self.status_disconn_fg = self.status_disconn
            self.status_flashing   = ("#fde68a", "#78350f")
            self.status_flash_fg   = self.status_flashing
            self.status_complete   = ("#6ee7b7", "#064e3b")
            self.status_comp_fg    = self.status_complete
            self.status_failed     = ("#fca5a5", "#7f1d1d")
            self.status_fail_fg    = self.status_failed
            self.status_retry      = ("#fde68a", "#78350f")
            self.status_retry_fg   = self.status_retry

            # Banners
            self.info_bg    = "#1e3a5f"
            self.info_fg    = "#bfdbfe"
            self.ok_bg      = "#064e3b"
            self.ok_fg      = "#6ee7b7"
            self.warn_bg    = "#451a03"
            self.warn_fg    = "#fde68a"
            self.danger_bg  = "#7f1d1d"
            self.danger_fg  = "#fca5a5"

            # Progress
            self.progress_track = "#374151"
            self.progress_fill  = os_accent
            self.progress_ok    = "#10b981"
            self.progress_err   = "#ef4444"

            # Misc
            self.log_bg = "#000000" if b else "#0f172a"
            self.log_fg = "#a3e635"
            self.disabled = "#334155"
            self.disabled_fg = "#94a3b8"
            self.btn_primary_border = "rgba(255, 255, 255, 0.25)"
            self.btn_primary_border_bottom = "rgba(0, 0, 0, 0.35)"
            self.btn_disabled_border = "rgba(255, 255, 255, 0.15)"

        else:
            # Surfaces — Light mode
            self.bg         = "#f8fafc"
            self.bg_card    = "#ffffff"
            self.bg_elev    = "#f1f5f9"
            self.bg_input   = "#ffffff"
            self.bg_hover   = "rgba(0, 0, 0, 0.06)"
            self.bg_nav     = "#f8fafc"
            self.bg_status  = "#f1f5f9"
            self.bg_tooltip = "#ffffff"

            # Text — Light mode: Very black fonts for high-contrast legibility
            self.fg         = "#000000"
            self.fg_dim     = "#111827"
            self.fg_muted   = "#374151"
            self.subtext    = self.fg_muted
            self.fg_primary = os_accent

            # Borders
            self.border        = "#cbd5e1"
            self.border_strong = "#94a3b8"
            self.border_focus  = os_accent

            # Accent
            self.accent       = os_accent
            self.accent_hover = QColor(os_accent).darker(115).name()
            self.accent_active = QColor(os_accent).darker(130).name()
            self.accent_bg    = "#eff6ff"
            self.accent_text  = QColor(os_accent).darker(170).name()

            self.nav_active      = os_accent
            self.nav_active_text = os_accent_text

            # Status Badges
            self.status_idle       = ("#000000", "#e2e8f0")
            self.status_idle_fg    = self.status_idle
            self.status_selected   = ("#ffffff", os_accent)
            self.status_sel_fg     = self.status_selected
            self.status_connected  = ("#065f46", "#d1fae5")
            self.status_conn_fg    = self.status_connected
            self.status_disconn    = ("#991b1b", "#fee2e2")
            self.status_disconn_fg = self.status_disconn
            self.status_flashing   = ("#92400e", "#fef3c7")
            self.status_flash_fg   = self.status_flashing
            self.status_complete   = ("#065f46", "#d1fae5")
            self.status_comp_fg    = self.status_complete
            self.status_failed     = ("#991b1b", "#fee2e2")
            self.status_fail_fg    = self.status_failed
            self.status_retry      = ("#92400e", "#fef3c7")
            self.status_retry_fg   = self.status_retry

            # Banners
            self.info_bg    = "#eff6ff"
            self.info_fg    = "#1e40af"
            self.ok_bg      = "#d1fae5"
            self.ok_fg      = "#065f46"
            self.warn_bg    = "#fef3c7"
            self.warn_fg    = "#92400e"
            self.danger_bg  = "#fee2e2"
            self.danger_fg  = "#991b1b"

            # Progress
            self.progress_track = "#e2e8f0"
            self.progress_fill  = os_accent
            self.progress_ok    = "#059669"
            self.progress_err   = "#dc2626"

            # Misc
            self.log_bg = "#ffffff"
            self.log_fg = "#15803d"
            self.disabled = "#e2e8f0"
            self.disabled_fg = "#64748b"
            self.btn_primary_border = "rgba(0, 0, 0, 0.15)"
            self.btn_primary_border_bottom = "rgba(0, 0, 0, 0.30)"
            self.btn_disabled_border = "rgba(0, 0, 0, 0.15)"

        if IS_LINUX and use_native_linux_colors and palette is not None:
            self._apply_linux_palette(palette, dark=d)

    def _apply_linux_palette(self, palette: QPalette, *, dark: bool) -> None:
        """Use the desktop's palette roles for app surfaces and foregrounds."""
        def color(role: QPalette.ColorRole, fallback: str) -> QColor:
            candidate = palette.color(role)
            if not candidate.isValid() or candidate.alpha() == 0:
                candidate = QColor(fallback)
            candidate = QColor(candidate)
            candidate.setAlpha(255)
            return candidate

        window = color(QPalette.ColorRole.Window, self.bg)
        window_text = color(QPalette.ColorRole.WindowText, self.fg)
        base = color(QPalette.ColorRole.Base, self.bg_input)
        alternate = color(QPalette.ColorRole.AlternateBase, self.bg_elev)
        # Cards sit on the window as a translucent tint of its own text colour,
        # so the desktop's Button/AlternateBase shades never show as plates.
        # Cards are a shade of the desktop window colour, so a green desktop
        # gives green cards rather than a neutral grey overlay.
        card_tint = QColor(window).lighter(112 if dark else 104)
        elev_tint = QColor(window).lighter(124 if dark else 110)
        button = QColor(card_tint)
        highlight = QColor(self.accent)
        highlighted_text = QColor(self.accent_text)

        self.bg = window.name()
        self.bg_card = button.name()
        self.bg_elev = elev_tint.name()
        self.bg_input = base.name()
        self.bg_nav = window.name()
        self.bg_status = button.name()
        self.bg_tooltip = color(QPalette.ColorRole.ToolTipBase, self.bg_card).name()
        # Keep the desktop's text roles exactly: Plasma, GNOME and other
        # Linux desktops deliberately choose their foregrounds for their own
        # surfaces, and replacing them with generic white/black breaks custom
        # theme colors in labels, controls, combo boxes and icons.
        text_col = color(QPalette.ColorRole.Text, window_text.name())
        button_text = color(QPalette.ColorRole.ButtonText, window_text.name())
        placeholder = color(QPalette.ColorRole.PlaceholderText, text_col.name())

        self.fg = window_text.name()
        self.fg_dim = text_col.name()
        self.fg_muted = placeholder.name()
        self.subtext = self.fg_muted
        self.border = color(QPalette.ColorRole.Mid, self.border).name()
        self.border_strong = color(QPalette.ColorRole.Dark, self.border_strong).name()
        self.border_focus = highlight.name()
        self.accent = highlight.name()
        self.accent_hover = highlight.lighter(115).name() if dark else highlight.darker(115).name()
        self.accent_active = highlight.darker(130 if not dark else 115).name()
        self.accent_bg = palette_color(_hex_rgba(highlight.name(), 0.18), over=window.name()).name()
        self.accent_text = highlighted_text.name()
        self.nav_active = highlight.name()
        self.nav_active_text = highlighted_text.name()
        self.progress_track = elev_tint.name()
        self.progress_fill = highlight.name()
        self.log_bg = base.name()
        self.log_fg = color(QPalette.ColorRole.Text, self.fg).name()
        disabled_text = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        self.disabled_fg = disabled_text.name() if disabled_text.isValid() else self.fg_muted
        self.disabled = color(QPalette.ColorRole.Midlight, alternate.name()).name()
        self.status_idle = (window_text.name(), alternate.name())
        self.status_idle_fg = self.status_idle
        self.status_selected = (highlighted_text.name(), highlight.name())
        self.status_sel_fg = self.status_selected
        self.btn_primary_border = _hex_rgba(window_text.name(), 0.25)
        self.btn_primary_border_bottom = _hex_rgba(window_text.name(), 0.35)
        self.btn_disabled_border = _hex_rgba(window_text.name(), 0.15)

        # Status, banner and progress hues are fixed in the token sets. Mix each
        # hue into the desktop's window colour so it fits any theme colour.
        def mix(hue: str, amount: float) -> str:
            h = QColor(hue)
            return QColor(
                round(h.red() * amount + window.red() * (1 - amount)),
                round(h.green() * amount + window.green() * (1 - amount)),
                round(h.blue() * amount + window.blue() * (1 - amount)),
            ).name()

        def readable(hue: str) -> str:
            color_obj = QColor(hue)
            return color_obj.lighter(150).name() if dark else color_obj.darker(160).name()

        ok, warn, danger = "#10b981", "#f59e0b", "#ef4444"
        ok_bg, warn_bg, danger_bg = mix(ok, 0.22), mix(warn, 0.22), mix(danger, 0.22)
        self.ok_bg, self.ok_fg = ok_bg, readable(ok)
        self.warn_bg, self.warn_fg = warn_bg, readable(warn)
        self.danger_bg, self.danger_fg = danger_bg, readable(danger)
        self.info_bg = mix(highlight.name(), 0.22)
        self.info_fg = readable(highlight.name())
        self.status_connected = self.status_complete = (self.ok_fg, ok_bg)
        self.status_conn_fg = self.status_comp_fg = self.status_connected
        self.status_disconn = self.status_failed = (self.danger_fg, danger_bg)
        self.status_disconn_fg = self.status_fail_fg = self.status_disconn
        self.status_flashing = self.status_retry = (self.warn_fg, warn_bg)
        self.status_flash_fg = self.status_retry_fg = self.status_flashing
        self.progress_ok = ok
        self.progress_err = danger

        # Combo boxes on Linux read most of their painting colours from the
        # combo widget's own palette (text, window text, base, button) rather
        # than from the desktop's own palette directly. Pin those roles to the
        # desktop palette here so the combo matches the rest of the host app
        # instead of carrying a hard-coded dark-on-dark or light-on-light pair.
        self._linux_combo_window_text = window_text.name()
        self._linux_combo_text = text_col.name()
        self._linux_combo_button_text = button_text.name()
        self._linux_combo_base = base.name()
        self._linux_combo_window = window.name()
        self._linux_combo_highlight = highlight.name()
        self._linux_combo_highlight_text = highlighted_text.name()
        # The popup rows use the desktop's Base, not a separately styled card.
        self._linux_combo_view_base = base.name()


class _ThemeState:
    """Singleton holding the current palette and tokens."""

    def __init__(self):
        self._dark: bool | None = None
        self._pure_black: bool = False
        self.native_palette: QPalette | None = None
        # The host palette as the platform theme handed it over, before the app
        # replaced it with a derived one. Refreshed by host palette events.
        self.platform_palette: QPalette | None = None
        self.linux_palette_from_event = False
        self.tokens: _Tokens = _Tokens(False)

    def detect(self, native_palette: QPalette | None = None) -> bool:
        self._dark = is_system_dark_mode()
        self._pure_black = False
        if IS_LINUX and native_palette is not None:
            self.native_palette = QPalette(native_palette)
            # The palette decides, never the colour-scheme hint. Qt reports the
            # hint from a different source than the palette (Plasma's
            # integration, the Qt app style and the theme's own colours can each
            # disagree), and when the hint said "dark" over a white palette the
            # app painted black sidebar rows, dark cards and dark-on-dark titles
            # on a white window, with white combo text in white fields. There is
            # one decision here so tokens, the app palette and every painted
            # surface cannot disagree; a Linux desktop announces a scheme switch
            # by handing the app a new palette, which arrives through this path.
            window = self.native_palette.color(QPalette.ColorRole.Window)
            if window.isValid() and window.alpha() > 0:
                self._dark = _palette_is_dark(self.native_palette)
        use_native = (
            IS_LINUX
            and self.native_palette is not None
            and _palette_is_dark(self.native_palette) == self._dark
        )
        self.tokens = _Tokens(
            self._dark,
            pure_black=self._pure_black,
            palette=self.native_palette,
            use_native_linux_colors=use_native,
        )
        return self._dark

    def set_dark(self, value: bool, pure_black: bool = False, palette: QPalette | None = None) -> None:
        self._dark = value
        self._pure_black = pure_black
        if IS_LINUX and palette is not None:
            self.native_palette = QPalette(palette)
        use_native = (
            IS_LINUX
            and self.native_palette is not None
            and _palette_is_dark(self.native_palette) == value
        )
        self.tokens = _Tokens(
            value,
            pure_black=pure_black,
            palette=self.native_palette,
            use_native_linux_colors=use_native,
        )

    @property
    def is_dark(self) -> bool:
        return self._dark or False

    @property
    def is_pure_black(self) -> bool:
        return self._pure_black


_state = _ThemeState()


def is_dark() -> bool:
    """True when the current theme is dark."""
    return _state.is_dark


def is_pure_black() -> bool:
    """True when running in pure black / OLED high contrast mode."""
    return _state.is_pure_black


def T() -> _Tokens:
    """Return the active token set."""
    return _state.tokens


def is_pure_black() -> bool:
    """True when running in pure black / OLED high contrast mode."""
    return _state.is_pure_black


def T() -> _Tokens:
    """Return the active token set."""
    return _state.tokens


# ---------------------------------------------------------------------------
# QPalette builder
# ---------------------------------------------------------------------------

def _pre_liquid_glass_macos() -> bool:
    """macOS before Tahoe / Golden Gate, where Aqua still paints the controls."""
    if sys.platform != "darwin":
        return False
    try:
        from .glass import is_golden_gate_or_newer
        return not is_golden_gate_or_newer()
    except Exception:
        return False


def _is_opaque_white(color: QColor) -> bool:
    return (
        color.isValid()
        and color.alpha() >= 250
        and color.red() >= 250
        and color.green() >= 250
        and color.blue() >= 250
    )


# Aqua's light control and field fills from before Liquid Glass. Used only
# when the style's own color is missing or pure white, which is what made
# the combo boxes sit on the glass as bright plates.
_AQUA_LIGHT_CONTROL = QColor(236, 236, 236)
_AQUA_LIGHT_FIELD = QColor(246, 246, 246)


def legacy_light_role_color(role: QPalette.ColorRole, standard: QColor | None) -> QColor:
    """Light-mode field color for macOS older than Liquid Glass.

    Prefer the style's own light color so Aqua draws the control the way the
    desktop does. A pure white role is the unthemed plate. A dark role would
    force a dark popup while the window is light, so that is not used either.
    """
    if standard is not None and standard.isValid() and not _is_opaque_white(standard):
        if standard.lightness() > 180:
            return QColor(standard)
    if role == QPalette.ColorRole.Button:
        return QColor(_AQUA_LIGHT_CONTROL)
    return QColor(_AQUA_LIGHT_FIELD)


def _legacy_light_palette_color(role: QPalette.ColorRole) -> QColor | None:
    if not _pre_liquid_glass_macos():
        return None
    standard = None
    app = QApplication.instance()
    if app is not None:
        try:
            standard = app.style().standardPalette().color(role)
        except Exception:
            standard = None
    return legacy_light_role_color(role, standard)


def _make_palette(
    dark: bool,
    pure_black: bool = False,
    native_palette: QPalette | None = None,
) -> QPalette:
    if IS_LINUX and native_palette is not None and _palette_is_dark(native_palette) == dark:
        # Preserve the whole Linux palette: native controls, selection colors,
        # disabled colors and desktop-specific foregrounds are all deliberate.
        return QPalette(native_palette)
    p = QPalette()
    t = _Tokens(dark, pure_black=pure_black)
    if IS_LINUX and native_palette is not None:
        pal = QPalette(native_palette)
        # Take a palette snapshot before our app palette is replaced. Some
        # Qt styles expose their full desktop palette only through this object.
        t = _Tokens(dark, pure_black=pure_black, palette=pal, use_native_linux_colors=True)
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive, QPalette.ColorGroup.Disabled):
            for role, value in (
                (QPalette.ColorRole.Window, t.bg),
                (QPalette.ColorRole.WindowText, t._linux_combo_window_text),
                (QPalette.ColorRole.Base, t.bg_input),
                (QPalette.ColorRole.AlternateBase, t.bg_elev),
                (QPalette.ColorRole.ToolTipBase, t.bg_tooltip),
                (QPalette.ColorRole.ToolTipText, t._linux_combo_window_text),
                (QPalette.ColorRole.Text, t._linux_combo_text),
                (QPalette.ColorRole.Button, t.bg_card),
                (QPalette.ColorRole.ButtonText, t._linux_combo_button_text),
                (QPalette.ColorRole.Highlight, t.accent),
                (QPalette.ColorRole.HighlightedText, t._linux_combo_highlight_text),
                (QPalette.ColorRole.PlaceholderText, t.fg_muted),
                (QPalette.ColorRole.Link, t._linux_combo_text),
                (QPalette.ColorRole.LinkVisited, t._linux_combo_text),
                (QPalette.ColorRole.Midlight, t.disabled),
                (QPalette.ColorRole.Mid, t.border),
                (QPalette.ColorRole.Dark, t.border_strong),
            ):
                pal.setColor(group, role, QColor(value))
            if group == QPalette.ColorGroup.Disabled:
                disabled_window = native_palette.color(group, QPalette.ColorRole.WindowText)
                disabled_input = native_palette.color(group, QPalette.ColorRole.Text)
                disabled_button = native_palette.color(group, QPalette.ColorRole.ButtonText)
                if disabled_window.isValid():
                    pal.setColor(group, QPalette.ColorRole.WindowText, disabled_window)
                if disabled_input.isValid():
                    pal.setColor(group, QPalette.ColorRole.Text, disabled_input)
                if disabled_button.isValid():
                    pal.setColor(group, QPalette.ColorRole.ButtonText, disabled_button)
        return pal
    if dark:
        p.setColor(QPalette.Window, QColor(t.bg))
        p.setColor(QPalette.WindowText, QColor(t.fg))
        p.setColor(QPalette.Base, QColor(t.bg_input))
        p.setColor(QPalette.AlternateBase, QColor(t.bg_elev))
        p.setColor(QPalette.ToolTipBase, QColor(t.bg_tooltip))
        p.setColor(QPalette.ToolTipText, QColor(t.fg))
        p.setColor(QPalette.Text, QColor(t.fg))
        p.setColor(QPalette.Button, QColor(t.bg_card))
        p.setColor(QPalette.ButtonText, QColor(t.fg))
        p.setColor(QPalette.BrightText, QColor("#fca5a5"))
        p.setColor(QPalette.Link, QColor(t.fg))
        p.setColor(QPalette.LinkVisited, QColor(t.fg))
        p.setColor(QPalette.Highlight, QColor(t.accent))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        if hasattr(QPalette.ColorRole, "Accent"):
            p.setColor(QPalette.ColorRole.Accent, QColor(t.accent))
        p.setColor(QPalette.Midlight, palette_color(t.bg_hover, over=t.bg))
        p.setColor(QPalette.Mid, QColor(t.border))
        p.setColor(QPalette.Dark, QColor(t.border_strong))
        p.setColor(QPalette.Shadow, QColor("#000000"))
        p.setColor(QPalette.PlaceholderText, QColor(t.fg_muted))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(t.disabled))
    else:
        p.setColor(QPalette.Window, QColor(t.bg))
        p.setColor(QPalette.WindowText, QColor(t.fg))
        # On macOS before Liquid Glass, a forced #ffffff Button/Base makes
        # Aqua paint opaque white plates. Use the style's light control
        # colors instead. Tahoe and Golden Gate keep the token colors.
        base = _legacy_light_palette_color(QPalette.Base)
        button = _legacy_light_palette_color(QPalette.Button)
        alternate = _legacy_light_palette_color(QPalette.AlternateBase)
        p.setColor(QPalette.Base, base if base is not None else QColor(t.bg_input))
        p.setColor(QPalette.AlternateBase, alternate if alternate is not None else QColor(t.bg_elev))
        p.setColor(QPalette.ToolTipBase, QColor(t.bg_tooltip))
        p.setColor(QPalette.ToolTipText, QColor(t.fg))
        p.setColor(QPalette.Text, QColor(t.fg))
        p.setColor(QPalette.Button, button if button is not None else palette_color(t.bg_card))
        p.setColor(QPalette.ButtonText, QColor(t.fg))
        p.setColor(QPalette.BrightText, QColor("#dc2626"))
        p.setColor(QPalette.Link, QColor(t.fg))
        p.setColor(QPalette.LinkVisited, QColor(t.fg))
        p.setColor(QPalette.Highlight, QColor(t.accent))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        if hasattr(QPalette.ColorRole, "Accent"):
            p.setColor(QPalette.ColorRole.Accent, QColor(t.accent))
        p.setColor(QPalette.Midlight, palette_color(t.bg_hover, over=t.bg))
        p.setColor(QPalette.Mid, QColor(t.border))
        p.setColor(QPalette.Dark, QColor(t.border_strong))
        p.setColor(QPalette.Shadow, QColor("#000000"))
        p.setColor(QPalette.PlaceholderText, QColor(t.fg_muted))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(t.disabled))
    return p


def theme_foreground() -> QColor:
    """Foreground for the active theme: dark on light, light on dark."""
    app = QApplication.instance()
    if app is not None:
        color = app.palette().color(QPalette.ColorRole.WindowText)
        if color.isValid() and color.alpha() > 0:
            return QColor(color)
    return QColor(T().fg)


def apply_explicit_foreground(widget, extra: str = "") -> None:
    """Keep text on the theme foreground.

    An empty stylesheet inherits ``color`` from the window rule. The next
    application stylesheet polish then writes white into the label palette,
    which is the unreadable progress line on a light card. An explicit color
    with no background survives that polish and does not paint a plate.
    """
    if widget is None:
        return
    fg = theme_foreground()
    widget.setAutoFillBackground(False)
    widget.setStyleSheet(f"color: {fg.name()};{extra}")
    pal = widget.palette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            pal.setColor(group, role, fg)
    widget.setPalette(pal)
    widget.setForegroundRole(QPalette.ColorRole.WindowText)


class _ProgressTextStyle(QProxyStyle):
    """Native groove and chunk, with the percent in the theme foreground.

    WinUI draws that percent from HighlightedText, which the palette keeps
    white for selections. White on the light groove cannot be read. The
    color is read when painting, so a live theme change does not need a
    new progress bar.
    """

    def drawControl(self, element, option, painter, widget=None):  # noqa: N802
        label = element == QStyle.ControlElement.CE_ProgressBarLabel
        if label:
            text = getattr(option, "text", "") or ""
            visible = getattr(option, "textVisible", True)
            if not text or not visible:
                return
            painter.save()
            painter.setPen(theme_foreground())
            if widget is not None:
                painter.setFont(widget.font())
            painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter), text)
            painter.restore()
            return
        if element == QStyle.ControlElement.CE_ProgressBar:
            super().drawControl(element, option, painter, widget)
            # Aqua never asks for CE_ProgressBarLabel, so a native bar on macOS
            # showed the groove and no percentage at all. Paint the percent over
            # the groove there; the native styles that do draw their own label
            # are left alone.
            if sys.platform == "darwin":
                text = getattr(option, "text", "") or ""
                if text and getattr(option, "textVisible", True):
                    painter.save()
                    painter.setPen(theme_foreground())
                    if widget is not None:
                        painter.setFont(widget.font())
                    painter.drawText(option.rect, int(Qt.AlignmentFlag.AlignCenter), text)
                    painter.restore()
            return
        super().drawControl(element, option, painter, widget)


def install_progress_readability(bar) -> None:
    """Theme a progress bar's roles and percent. No progress stylesheet.

    A bar that paints itself (:class:`ui.widgets.InstallProgress`) needs the
    roles and nothing else — an extra text proxy over it would draw the
    percent twice. Bars left on the platform style keep the proxy, because
    Aqua never asks for the label and WinUI takes it from HighlightedText.
    """
    if bar is None:
        return
    bar.setStyleSheet("")
    bar.setAutoFillBackground(False)
    apply_readable_palette(bar, progress=True)
    from .widgets import InstallProgress

    if isinstance(bar, InstallProgress):
        return
    if not isinstance(bar.style(), _ProgressTextStyle):
        style = _ProgressTextStyle()
        style.setParent(bar)
        bar.setStyle(style)


def progress_bar_colors(bar=None) -> tuple[QColor, QColor, QColor]:
    """Track, fill and percent colours for a progress bar, from the live theme.

    Straight from the theme tokens, not the bar's palette: a QProgressBar's
    resolved palette reports the style's own Highlight, which is a grey on the
    platforms that made the chunk invisible in the first place. The label is
    whichever candidate clears both the track and the chunk, because the
    percent sits over both as the bar fills.
    """
    del bar  # kept for callers that pass their widget
    t = T()

    def opaque(value) -> QColor:
        color = QColor(value)
        if not color.isValid():
            return QColor("#808080")
        color.setAlpha(255)
        return color

    track = opaque(t.progress_track)
    fill = opaque(t.progress_fill)
    label = _progress_label_color(theme_foreground(), track, fill)
    if contrast_ratio(label, fill) < 3.0:
        fill = _shift_until_contrast(label, fill, 3.0)
    return track, fill, label


def _progress_label_color(foreground: QColor, track: QColor, fill: QColor) -> QColor:
    """Percent colour the eye can read over both the track and the chunk."""
    candidates = (QColor(foreground), QColor("#ffffff"), QColor("#0f172a"))

    def worst(color: QColor) -> float:
        return min(contrast_ratio(color, track), contrast_ratio(color, fill))

    best = max(candidates, key=worst)
    if worst(best) < 1.8:
        return QColor(foreground)
    return best


def apply_readable_palette(widget, *, progress: bool = False) -> None:
    """Copy theme roles onto a widget so text and bars follow light and dark mode.

    A stylesheet ``color: palette(...)`` resolves against a palette Qt replaced
    when a parent card set a background, which painted black text on the dark
    card. Roles are taken from the application palette instead.
    """
    app = QApplication.instance()
    if app is None or widget is None:
        return
    src = app.palette()
    pal = widget.palette()
    roles = (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.HighlightedText,
        QPalette.ColorRole.PlaceholderText,
    )
    foreground = T()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role in roles:
            if IS_LINUX and role in (
                QPalette.ColorRole.WindowText,
                QPalette.ColorRole.Text,
                QPalette.ColorRole.ButtonText,
            ):
                source_role = {
                    QPalette.ColorRole.WindowText: "_linux_combo_window_text",
                    QPalette.ColorRole.Text: "_linux_combo_text",
                    QPalette.ColorRole.ButtonText: "_linux_combo_button_text",
                }[role]
                value = getattr(foreground, source_role, "")
                if value:
                    pal.setColor(group, role, QColor(value))
                    continue
            pal.setColor(group, role, src.color(group, role))
    if progress:
        # The percent uses the theme foreground: dark on light, light on dark.
        # HighlightedText on the application palette is white so selections
        # stay readable, and WinUI was painting the percent with that white.
        groove = src.color(QPalette.ColorRole.AlternateBase)
        chunk = src.color(QPalette.ColorRole.Highlight)
        text = QColor(src.color(QPalette.ColorRole.WindowText))
        # Keep the accent fill. Shifting it until the text clears both the
        # groove and the chunk washed the bar out into the card.
        if contrast_ratio(chunk, groove) < 1.5:
            chunk = _shift_until_contrast(groove, chunk)
        pal.setColor(QPalette.ColorRole.Base, groove)
        pal.setColor(QPalette.ColorRole.Window, groove)
        pal.setColor(QPalette.ColorRole.Highlight, chunk)
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.HighlightedText,
        ):
            pal.setColor(role, text)
    widget.setPalette(pal)
    if progress:
        widget.setForegroundRole(QPalette.ColorRole.WindowText)


def _readable_on(background: QColor) -> QColor:
    """Near-white or near-black, whichever clears the background more."""
    light = QColor("#f8fafc")
    dark = QColor("#0f172a")
    if contrast_ratio(light, background) >= contrast_ratio(dark, background):
        return light
    return dark


def _shift_until_contrast(foreground: QColor, background: QColor, minimum: float = 4.5) -> QColor:
    """Keep the background hue and move its lightness until the text clears it."""
    if contrast_ratio(foreground, background) >= minimum:
        return QColor(background)
    hue = background.hslHue()
    if hue < 0:
        hue = 210
    saturation = background.hslSaturation()
    start = background.lightness()
    best = QColor(background)
    best_ratio = contrast_ratio(foreground, background)
    for delta in range(8, 256, 8):
        for light in (start - delta, start + delta):
            if light < 0 or light > 255:
                continue
            trial = QColor.fromHsl(hue, saturation, int(light))
            ratio = contrast_ratio(foreground, trial)
            if ratio > best_ratio:
                best = trial
                best_ratio = ratio
            if ratio >= minimum:
                return trial
    return best


def contrast_ratio(foreground: QColor, background: QColor) -> float:
    """WCAG contrast of two opaque colors."""

    def channel(value: int) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    def luminance(color: QColor) -> float:
        return (
            0.2126 * channel(color.red())
            + 0.7152 * channel(color.green())
            + 0.0722 * channel(color.blue())
        )

    lighter = max(luminance(foreground), luminance(background))
    darker = min(luminance(foreground), luminance(background))
    return (lighter + 0.05) / (darker + 0.05)


# ---------------------------------------------------------------------------
# QSS generation
# ---------------------------------------------------------------------------

def _build_qss() -> str:
    t = _state.tokens

    # Window background & sidebar transparency for Liquid Glass (macOS)
    use_glass = False
    try:
        from .glass import is_glass_supported
        use_glass = is_glass_supported()
    except ImportError:
        use_glass = False

    # The window itself is transparent so Liquid Glass / acrylic shows in the
    # margins, sidebar gaps, and title bar. Cards are a light frost. Labels
    # that change text are not left transparent: they erase their own rect.
    window_bg = t.bg
    dialog_bg = t.bg_card
    dialog_border = f"1px solid {t.border}"
    title_bg = "transparent"
    title_color = "palette(window-text)"
    text_color = "palette(window-text)"
    if use_glass:
        window_bg = "transparent"
        dialog_border = "1px solid rgba(255, 255, 255, 0.12)" if _state.is_dark else "1px solid rgba(0, 0, 0, 0.10)"
        # The card's own paint supplies a faint frost. A stylesheet fill here
        # would stack into a solid plate.
        title_bg = "transparent"

    tab_bar_bg = "rgba(255, 255, 255, 0.08)" if _state.is_dark else "rgba(0, 0, 0, 0.06)"

    return f"""
/* ── Base Window ────────────────────────────────────────
   Only the window surface and the app's own opt-in elements
   (sidebar, semantic labels, log view) are styled here. Standard
   controls — tabs, buttons, combo boxes, text fields, group boxes —
   are deliberately left to the native QStyle so they paint and behave
   like every other app on the host desktop. */
/* Surface: {t.bg} */
/* Accent: {t.accent} */
QMainWindow {{
    background-color: {window_bg};
    color: {text_color};
}}

QDialog {{
    background-color: {dialog_bg};
    border: {dialog_border};
    border-radius: 12px;
    color: {text_color};
}}

QMessageBox {{
    background-color: {dialog_bg};
    border: {dialog_border};
    border-radius: 12px;
    color: {text_color};
}}
QMessageBox QLabel {{
    color: {text_color};
}}

/* Cards paint their rounded surface in Card.paintEvent. Do not give the
   frame a stylesheet background: it fills the entire rectangle behind the
   rounded frost and produces the square plates seen at the card edges. */

/* ── Navigation sidebar ─────────────────────────────────
   The rail itself is not styled here. A stylesheet that matches the
   sidebar also restyles every button inside it, and those buttons then
   paint as blank plates. The window sets the rail color from the palette
   and leaves the buttons to the platform style. */

/* ── Semantic Labels & Titles (OS-Native Hierarchy) ──── */
QLabel[cssClass="pageTitle"] {{
    font-size: 20px;
    font-weight: 700;
    color: {title_color};
    letter-spacing: -0.01em;
    background: transparent;
    background-color: {title_bg};
}}
QLabel[cssClass="sectionTitle"] {{
    font-size: 15px;
    font-weight: 600;
    color: {text_color};
}}
QLabel[cssClass="cardTitle"] {{
    font-size: 14px;
    font-weight: 600;
    color: {text_color};
    background: transparent;
    border: none;
    padding: 0;
    margin-bottom: 2px;
}}
QLabel[cssClass="subtitle"] {{
    font-size: 13px;
    color: {text_color};
    background: transparent;
    border: none;
}}
QLabel[cssClass="dimmed"] {{
    font-size: 12px;
    color: {text_color};
    background: transparent;
    border: none;
}}
QLabel[cssClass="hint"] {{
    font-size: 13px;
    color: {text_color};
    background: transparent;
    border: none;
}}
QLabel[cssClass="field-label"] {{
    font-size: 13px;
    font-weight: 600;
    color: {text_color};
    background: transparent;
    border: none;
}}
QLabel[cssClass="infoValue"] {{
    font-size: 12px;
    font-weight: 600;
    color: {text_color};
    background: transparent;
    border: none;
}}
QLabel[cssClass="warning-banner"] {{
    font-size: 12px;
    font-weight: 500;
    color: {t.warn_fg};
    background-color: {t.warn_bg};
    border-radius: 6px;
    padding: 6px 12px;
}}
QLabel[cssClass="separator"], QFrame[cssClass="separator"] {{
    background-color: {t.border};
    min-height: 1px;
    max-height: 1px;
    border: none;
}}

/* Scroll areas, lists, and text views are not styled here. A matching
   rule puts that widget on QStyleSheetStyle, which draws classic arrow
   scrollbars even when the app style is windows11. Selection and hover
   stay on the platform style and the live palette. The diagnostics
   console sets a monospace QFont on the widget itself. */

/* ── Tooltips ───────────────────────────────────────────
   Deliberately not styled. A QToolTip rule puts the tip on
   QStyleSheetStyle, which throws away the host's own tip frame — the
   rounded help tag on macOS, the WinUI tip on Windows — and leaves a
   flat plate with our border and padding. The platform draws tooltips
   here, and tooltip text is plain, so nothing needs forcing a colour
   into a rich-text document either. */

/* ── Buttons are the platform's, not ours ─────────────────
   Every action button (primary, secondary, navigation) is drawn by the host
   QStyle: Aqua / WinUI / Adwaita / Breeze own the fill, the bevel, the focus
   ring, the disabled state and the keyboard cues, exactly as they do in the
   platform's own apps. The cssClass properties remain as semantic labels for
   wiring and tests; nothing here paints them, so a statically styled button
   can never disagree with the desktop's accessibility settings (high
   contrast, accent colour, "reduce motion" pressed states). */

QPushButton#roundNextButton, QPushButton#roundGlyphButton, QPushButton#softwareUpdateCancelBtn, QPushButton#flashRetryBtn, QPushButton#installDoneButton {{
    outline: none;
}}
QPushButton#roundNextButton:focus, QPushButton#roundGlyphButton:focus, QPushButton#softwareUpdateCancelBtn:focus, QPushButton#flashRetryBtn:focus, QPushButton#installDoneButton:focus {{
    outline: none;
}}
"""


def apply_native_scrollbar_policy(widget) -> None:
    """Leave scroll areas to the platform's own widgets (see ui.scrollbars)."""
    from .scrollbars import ensure_native_scrolling

    ensure_native_scrolling(widget)


def link_html(text: str, *, bold: bool = True) -> str:
    """Markup for an in-app link.

    Links use the same colour as every other piece of text — the system's
    foreground on the window surface, so they contrast with the background
    whether the desktop is in light or dark mode — and are bold so they are
    recognisable as links without relying on a colour the OS may not give us.
    """
    weight = " font-weight: 700;" if bold else ""
    return (
        f'<a href="#" style="color: {_state.tokens.fg}; text-decoration: none;'
        f' border: none; background: transparent;{weight}">{text}</a>'
    )



# Vertical space the window chrome occupies at the top of the client area.
# macOS draws the traffic lights *over* the window when the content view is
# extended (Liquid Glass), so content has to clear them; Windows and Linux
# hand Qt a client area that already starts below the native title bar, where
# any extra inset is pure wasted space.
MACOS_TRAFFIC_LIGHT_CLEARANCE = 12
NATIVE_TITLEBAR_MARGIN = 6
# A plain boundary under the traffic lights still reads as a gap: the first
# sidebar entry is pulled back up into that band so it lines up with the window
# chrome, leaving the button's own padding as the only breathing room.
MACOS_SIDEBAR_TITLEBAR_TRIM = 12
# Page content sits inside the client area regardless of platform.
PAGE_TOP_MARGIN = 8


def content_top_margin() -> int:
    """Inset for the sidebar so its first row lines up with the title bar."""
    if not IS_MACOS:
        return NATIVE_TITLEBAR_MARGIN
    try:
        from .glass import is_glass_supported

        # Only an extended content view puts the traffic lights over the app;
        # there the entry rises into the chrome band rather than sitting under it.
        if is_glass_supported():
            return max(0, MACOS_TRAFFIC_LIGHT_CLEARANCE - MACOS_SIDEBAR_TITLEBAR_TRIM)
        return NATIVE_TITLEBAR_MARGIN
    except Exception:
        return NATIVE_TITLEBAR_MARGIN


# One set of page margins for every page, so the content column is identical
# from screen to screen regardless of which page is showing (and regardless of
# whether a scrollbar is currently visible on the host).
PAGE_SIDE_MARGIN = 24
PAGE_BOTTOM_MARGIN = 20


def page_top_margin() -> int:
    """Inset for page content: the top of the page area, never a title-bar gap."""
    return PAGE_TOP_MARGIN


def page_margins() -> tuple[int, int, int, int]:
    """``(left, top, right, bottom)`` for a page's root layout."""
    return PAGE_SIDE_MARGIN, page_top_margin(), PAGE_SIDE_MARGIN, PAGE_BOTTOM_MARGIN


def native_font_families() -> list[str]:
    """Native UI font stack per platform, with CJK fallbacks for i18n.

    Applied through ``QApplication.setFont`` — never as a ``* { font-family }``
    QSS rule, which defeats CoreText/fontconfig cascading and renders CJK text
    as tofu boxes. Qt resolves these families in order, per glyph.
    """
    sys_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    sys_family = (sys_font.family() if sys_font else "").strip()
    families = []
    if sys_family and sys_family not in families:
        families.append(sys_family)
    if IS_MACOS:
        for f in [
            ".AppleSystemUIFont", "SF Pro Text", "SF Pro Display",
            "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        ]:
            if f not in families:
                families.append(f)
        return [f for f in families if f and f.strip()]
    if IS_WINDOWS:
        for f in ["Segoe UI", "Segoe UI Variable Text", "Microsoft YaHei"]:
            if f not in families:
                families.append(f)
        return [f for f in families if f and f.strip()]
    for f in ["Noto Sans", "Cantarell", "Ubuntu", "WenQuanYi Micro Hei"]:
        if f not in families:
            families.append(f)
    return [f for f in families if f and f.strip()]


_windows_font_subs_applied = False


def _ensure_windows_font_substitutions() -> None:
    """Register Windows 10 font substitutions when Windows 11 fonts are absent.

    Windows 10 ships ``Segoe UI``, ``Segoe MDL2 Assets``, and ``Consolas`` rather
    than ``Segoe UI Variable*``, ``Segoe Fluent Icons``, and ``Cascadia Mono``.
    Without substitutions, direct ``QFont`` lookups for the Windows 11 family
    names fall back to ``Tahoma`` on Windows 10.
    """
    global _windows_font_subs_applied
    if _windows_font_subs_applied or not IS_WINDOWS:
        return
    try:
        installed = {f.lower() for f in QFontDatabase.families()}
        substitutions = (
            ("Segoe Fluent Icons", "Segoe MDL2 Assets"),
            ("Segoe UI Variable Text", "Segoe UI"),
            ("Segoe UI Variable Display", "Segoe UI"),
            ("Segoe UI Variable Small", "Segoe UI"),
            ("Segoe UI Variable", "Segoe UI"),
            ("Cascadia Mono", "Consolas"),
            ("Cascadia Code", "Consolas"),
        )
        for target, fallback in substitutions:
            if target.lower() not in installed and fallback.lower() in installed:
                existing = [s.lower() for s in QFont.substitutes(target)]
                if fallback.lower() not in existing:
                    QFont.insertSubstitution(target, fallback)
        _windows_font_subs_applied = True
    except Exception:
        pass


def _apply_native_font(app: QApplication) -> None:
    """Set the platform UI font (and CJK fallbacks) on the application."""
    _ensure_windows_font_substitutions()
    sys_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    font = QFont(sys_font) if sys_font and sys_font.family() else QFont()
    fams = [f for f in native_font_families() if f and f.strip()]
    if fams:
        font.setFamilies(fams)
    sys_size = sys_font.pointSizeF() if sys_font else 0.0
    if sys_size > 0:
        font.setPointSizeF(sys_size)
    elif IS_MACOS:
        font.setPointSize(13)
    elif IS_WINDOWS:
        font.setPointSize(10)
    else:
        # Keep whatever size the desktop environment selected.
        size = app.font().pointSizeF()
        font.setPointSizeF(size if size > 0 else 10.0)
    app.setFont(font)


def tooltip_surface_colors() -> tuple[QColor, QColor]:
    """Background and text for a tooltip on the active theme.

    Light appearance: light surface, dark text. Dark appearance: the inverse.
    """
    tokens = _state.tokens
    background = QColor(tokens.bg_tooltip)
    foreground = QColor(tokens.fg)
    if not background.isValid():
        background = QColor("#ffffff" if not _state.is_dark else "#252932")
    if not foreground.isValid() or contrast_ratio(foreground, background) < 4.5:
        foreground = _readable_on(background)
    return background, foreground


def _capture_platform_palette(app: QApplication) -> None:
    """Remember the host palette the first time, before the app sets its own.

    After the first ``apply_theme`` the application palette is ours, so the
    desktop's own colours are only readable here — and it is those colours the
    platform theme paints the window frame, native controls and scrollbars with.
    """
    if not IS_LINUX or _state.platform_palette is not None:
        return
    try:
        from PySide6.QtGui import QGuiApplication

        candidate = QPalette(QGuiApplication.palette())
        if candidate.color(QPalette.ColorRole.Window).isValid():
            _state.platform_palette = candidate
    except Exception:
        logger.debug("could not snapshot the platform palette", exc_info=True)


def apply_theme(
    app: QApplication,
    force_dark: bool | None = None,
    pure_black: bool | None = None,
) -> None:
    """Apply the native platform theme to *app*.

    Ensures native platform QStyle is initialized.
    If *force_dark* is ``None`` the host system's native dark/light appearance is detected.
    Call ``apply_theme(app, force_dark=True)`` to lock dark mode.
    """
    watcher = getattr(app, "_theme_watcher", None) if app is not None else None
    prev_applying = getattr(watcher, "_applying", False) if watcher is not None else False
    if watcher is not None:
        watcher._applying = True
    try:
        native_palette = None
        _capture_platform_palette(app)
        setup_native_app_style(app)
        if IS_LINUX:
            native_palette = _linux_system_palette(app)
            _state.native_palette = QPalette(native_palette)
        _apply_native_font(app)
        if force_dark is not None:
            _state.set_dark(force_dark, pure_black=bool(pure_black), palette=native_palette)
        else:
            _state.detect(native_palette=native_palette)
        app.setPalette(
            _make_palette(
                _state.is_dark,
                pure_black=_state.is_pure_black,
                native_palette=_state.native_palette,
            )
        )
        app.setStyleSheet(_build_qss())
        # QToolTip keeps the host theme's palette: tooltips are the platform's.
        _restyle_existing_widgets(app)
        if watcher is not None:
            try:
                watcher._debounce.stop()
                if IS_LINUX:
                    _state.linux_palette_from_event = False
                watcher._last = theme_fingerprint(app, platform_dark=False)
                watcher._last_full = theme_fingerprint(app)
                watcher._cooldown.start()
            except Exception:
                pass
    finally:
        if watcher is not None:
            watcher._applying = prev_applying


def theme_fingerprint(app: QApplication | None = None, *, platform_dark: bool = True) -> tuple:
    """Snapshot of the host appearance, used to notice changes Qt never signals.

    Reads the *style's* standard palette rather than the application palette,
    because the app palette is ours (it already holds the previously derived
    accent) and would therefore never report a change.

    ``platform_dark=False`` skips the host dark-mode probe (a ``defaults`` /
    ``gsettings`` subprocess on macOS and Linux) for the cheap polling path;
    the Qt-driven signals cover appearance switches on their own.
    """
    app = app or QApplication.instance()
    if app is None:
        return ()
    scheme = ""
    try:
        hints = app.styleHints()
        if hasattr(hints, "colorScheme"):
            scheme = str(hints.colorScheme())
    except Exception:
        scheme = ""
    try:
        system_palette = _linux_system_palette(app) if IS_LINUX else app.style().standardPalette()
    except Exception:
        system_palette = app.palette()
    accent, accent_text = get_native_accent_color(system_palette)
    try:
        from .icons import macos_icon_style

        icon_style = macos_icon_style()
    except Exception:
        icon_style = ("", "")
    palette_roles = (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.Button,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.HighlightedText,
        QPalette.ColorRole.ToolTipBase,
        QPalette.ColorRole.ToolTipText,
        QPalette.ColorRole.Light,
        QPalette.ColorRole.Midlight,
        QPalette.ColorRole.Mid,
        QPalette.ColorRole.Dark,
        QPalette.ColorRole.Shadow,
        QPalette.ColorRole.Link,
        QPalette.ColorRole.LinkVisited,
        QPalette.ColorRole.PlaceholderText,
    )
    if hasattr(QPalette.ColorRole, "Accent"):
        palette_roles += (QPalette.ColorRole.Accent,)
    palette_fingerprint = tuple(
        system_palette.color(group, role).rgba()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        )
        for role in palette_roles
    )
    style_name = ""
    try:
        if app.style() is not None:
            st = app.style()
            style_name = st.objectName() or (st.metaObject().className() if st.metaObject() else str(type(st).__name__))
    except Exception:
        style_name = ""
    return (
        scheme,
        is_system_dark_mode() if platform_dark else None,
        accent,
        accent_text,
        palette_fingerprint,
        icon_style,
        style_name,
    )


def configure_combo_palette(combo):
    """Refresh a QComboBox's own palette from the live theme tokens.

    A combo that only keeps its desktop palette will repaint its dropdown arrow
    and text using hard-coded host colours (white on dark, black on light),
    which is what leaves white text on a dark combo and black text on a light
    combo on Linux. The roles below are the ones Qt asks the combo widget's
    palette for when it paints the editable portion and the arrow. On Linux,
    where the desktop palette drove token selection, those desktop-derived roles
    are preferred so the combo matches the rest of the host app instead of any
    static light-on-dark or dark-on-light fallback pair.
    """
    app = QApplication.instance()
    if app is None or combo is None:
        return
    t = T()
    pal = combo.palette()
    # Combo-box text and glyphs are painted from its own palette. Preserve the
    # desktop's separate WindowText/Text/ButtonText roles instead of applying
    # a fixed generic foreground to the control.
    if IS_LINUX and getattr(t, "_linux_combo_text", None):
        role_colors = {
            QPalette.ColorRole.WindowText: QColor(t._linux_combo_window_text),
            QPalette.ColorRole.Text: QColor(t._linux_combo_text),
            QPalette.ColorRole.ButtonText: QColor(t._linux_combo_button_text),
        }
    else:
        fg = QColor(getattr(t, "fg", "#000000"))
        role_colors = {
            QPalette.ColorRole.WindowText: fg,
            QPalette.ColorRole.Text: fg,
            QPalette.ColorRole.ButtonText: fg,
        }
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, role_color in role_colors.items():
            pal.setColor(group, role, role_color)
    combo.setPalette(pal)


def configure_combo_view_palette(view):
    """Refresh a QComboBox pop-up list's palette from the live theme tokens.

    The drop-down view paints list rows from the view's palette. When that view
    kept a stale dark Base, the menu becomes a black slab on a light desktop and
    the reverse on a dark one. The roles below are the ones Qt asks the view for
    when it paints rows. On Linux, where the desktop palette drove token
    selection, the desktop-derived base/selection roles are preferred so the
    menu matches the desktop's own lists.
    """
    app = QApplication.instance()
    if app is None or view is None:
        return
    t = T()
    vpal = view.palette()
    # Prefer the host desktop base / text / selection roles on Linux.
    if IS_LINUX and getattr(t, "_linux_combo_base", None):
        base = QColor(t._linux_combo_view_base)
        # Rows read the same colour as the closed combo and every label. Some
        # desktop schemes give QPalette::Text (the colour for text inside Base
        # surfaces) a plain white/black value while WindowText/ButtonText carry
        # the theme's font colour, which left the open menu at odds with the
        # combo it dropped out of.
        text = QColor(t._linux_combo_window_text)
        highlight = QColor(t._linux_combo_highlight)
        highlighted = QColor(t._linux_combo_highlight_text)
    else:
        base = QColor(t.bg_input)
        text = QColor(getattr(t, "fg", "#000000"))
        highlight = QColor(t.accent)
        highlighted = QColor("#ffffff")
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, value in (
            # The list surface itself: a view that falls back to its Window
            # brush must get the list background, never the text colour.
            (QPalette.ColorRole.Window, base),
            (QPalette.ColorRole.Base, base),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.ButtonText, text),
            (QPalette.ColorRole.Highlight, highlight),
            (QPalette.ColorRole.HighlightedText, highlighted),
        ):
            vpal.setColor(group, role, value)
    view.setPalette(vpal)

def configure_native_combobox(combo, max_visible_items: int = 12) -> None:
    """Configure a QComboBox to match the desktop system font, use native popup widgets,
    and remain scrollable and constrained to an appropriate screen height when holding many items."""
    if combo is None:
        return

    # On every platform: keep the combo's own palette and the dropdown
    # view/viewport in sync with the host theme. A combo whose popup kept
    # a stale dark Base turns the menu into an unreadable black slab on a
    # light desktop, and the reverse on a dark one. Windows and macOS still
    # only cap the visible item count here — their styles paint text from
    # the live palette — but the palette surfaces are refreshed as well so a
    # theme change never leaves a mismatched popup behind.
    app = QApplication.instance()
    target_font = app.font() if app else QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if target_font and (not target_font.family() or target_font.pointSize() <= 0):
        target_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    if target_font:
        combo.setFont(target_font)

    configure_combo_palette(combo)

    combo.setMaxVisibleItems(max_visible_items)
    view = combo.view()
    if view is not None:
        if target_font:
            view.setFont(target_font)
        configure_combo_view_palette(view)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setMaximumHeight(360)


def sync_cached_palettes(app: QApplication) -> None:
    """Rewrite brushes that a widget cached from the previous theme.

    Combo popups and scroll viewports keep their own palette. A dark Base
    left on those widgets is the black slab, and dark text on that Base is
    why the dropdowns become unreadable after a live switch. The combo itself
    gets the lightweight combo palette (not the full readable one) so the
    dropdown arrow and any text the style reads from the combo stay on theme.
    """
    from PySide6.QtWidgets import QAbstractScrollArea, QComboBox

    from .scrollbars import refresh_scrollbar_palettes

    for widget in app.allWidgets():
        if isinstance(widget, QComboBox):
            configure_native_combobox(widget)
            configure_combo_palette(widget)
            view = widget.view()
            if view is not None:
                configure_combo_view_palette(view)
                viewport = view.viewport()
                if viewport is not None:
                    # The popup list paints rows from the viewport's palette, so
                    # it gets the same theme roles as the view. Left to the
                    # desktop's raw Text role, a scheme that reserves white for
                    # text-inside-Base would repaint the open menu in white.
                    configure_combo_view_palette(viewport)
                    viewport.setAutoFillBackground(True)
        elif isinstance(widget, QAbstractScrollArea):
            if widget.styleSheet():
                widget.setStyleSheet("")
            # The bars keep a copy of the theme taken when the area was built.
            refresh_scrollbar_palettes(widget)
            viewport = widget.viewport()
            if viewport is None:
                continue
            base = viewport.palette().color(QPalette.ColorRole.Base)
            if base.alpha() == 0 or widget.autoFillBackground():
                apply_readable_palette(viewport)
                viewport.setAutoFillBackground(True)
            elif base.alpha() > 200 and base.lightness() > 40:
                # Already a light surface, keep it native so the scrollbars
                # stay platform-drawn and the card's glass overlay does not turn
                # the area into a solid frame.
                pass
    try:
        from .glass import arm_combo_popups
        arm_combo_popups(app)
    except Exception:
        logger.debug("combo popup material was not armed", exc_info=True)


def refresh_theme(app: QApplication | None = None) -> None:
    """Re-read the host appearance and repaint the whole app with the new tokens.

    Re-applies the font (desktop environments can change it together with the
    theme), the palette, the stylesheet, and then asks every widget that owns
    inline styling to restyle itself.
    """
    app = app or QApplication.instance()
    if not app:
        return
    watcher = getattr(app, "_theme_watcher", None)
    prev_applying = getattr(watcher, "_applying", False) if watcher is not None else False
    if watcher is not None:
        watcher._applying = True
    try:
        native_palette = _linux_system_palette(app) if IS_LINUX else None
        _state.detect(native_palette=native_palette)
        if IS_LINUX:
            _state.linux_palette_from_event = False
        _apply_native_font(app)
        app.setPalette(
            _make_palette(
                _state.is_dark,
                pure_black=_state.is_pure_black,
                native_palette=_state.native_palette,
            )
        )
        app.setStyleSheet(_build_qss())
        # QToolTip keeps the host theme's palette: tooltips are the platform's.
        _restyle_existing_widgets(app)
    finally:
        if watcher is not None:
            watcher._applying = prev_applying


def _restyle_existing_widgets(app: QApplication) -> None:
    """Reapply colors that widgets cached before this palette.

    Status, donation, and progress text keep their own palette. Updating only
    the application palette leaves the previous foreground in place, which is
    white text on the light surface after a live theme change.
    """
    sync_cached_palettes(app)
    for widget in app.allWidgets():
        apply_native_scrollbar_policy(widget)
        hook = getattr(widget, "refresh_theme", None)
        if callable(hook):
            try:
                hook()
            except Exception:
                logger.debug("refresh_theme failed for %s", type(widget).__name__, exc_info=True)
        try:
            widget.update()
        except Exception:
            pass
    sync_cached_palettes(app)


class ThemeWatcher(QObject):
    """Applies host theme / accent changes live, on every platform.

    Qt reports light-dark switches through ``styleHints.colorSchemeChanged``
    (6.5+) and desktop-environment palette swaps as palette-change events, but
    an accent-colour-only change (KDE, GNOME, Windows personalisation) is often
    signalled by nothing at all. All three paths are therefore wired, with a
    cheap fingerprint poll as the catch-all.
    """

    # The poll is the catch-all for accent-colour changes that the host signals
    # through nothing at all; the appearance switches themselves arrive as Qt
    # events (see ``install``). A few seconds of latency on an accent change is
    # invisible, so the poll stays inexpensive for long-running sessions.
    POLL_INTERVAL_MS = 5000
    DEBOUNCE_MS = 150
    # Qt re-broadcasts a palette/theme change caused by *our own* setPalette, so
    # ignore platform events for a moment after each refresh or the app would
    # restyle itself twice for every host change.
    COOLDOWN_MS = 500
    # Every Nth poll also runs the slower cross-platform probes (``defaults``
    # / ``gsettings`` subprocesses), so an accent change that Qt never reports
    # is still caught — without spawning a process every couple of seconds.
    # Those probes fork a process (~29 ms each on macOS), so they are kept to
    # roughly one every two minutes.
    FULL_PROBE_EVERY = 24

    def __init__(self, app: QApplication, parent=None, on_apply=None):
        super().__init__(parent or app)
        self._app = app
        self._applying = False
        self._on_apply = on_apply
        self._polls = 0
        self._last = theme_fingerprint(app, platform_dark=False)
        self._last_full = theme_fingerprint(app)
        # A watcher that has been stopped stays inert even though Qt keeps the
        # host signal connections until the receiver is destroyed (see stop()).
        self._installed = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(self.DEBOUNCE_MS)
        self._debounce.timeout.connect(self.apply_now)
        self._cooldown = QTimer(self)
        self._cooldown.setSingleShot(True)
        self._cooldown.setInterval(self.COOLDOWN_MS)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.poll)

    # -- wiring -------------------------------------------------------------
    def install(self) -> None:
        """Start watching. Idempotent, and paired with :meth:`stop`."""
        if self._installed:
            return
        try:
            hints = self._app.styleHints()
            if hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(self.schedule)
        except Exception:
            pass
        try:
            if hasattr(self._app, "paletteChanged"):
                self._app.paletteChanged.connect(
                    self._on_palette_changed if IS_LINUX else self.schedule
                )
        except Exception:
            pass
        try:
            self._app.installEventFilter(self)
        except Exception:
            pass
        self._installed = True
        self._poll_timer.start()

    def stop(self) -> None:
        """Stop watching: timers off, event filter removed, watcher inert.

        Qt keeps signal connections to a receiver that is still alive, and a
        parented watcher outlives ``stop()``. ``_installed`` is what makes the
        stopped watcher ignore them, so it can never restyle the app behind a
        newer watcher's back.
        """
        self._installed = False
        self._poll_timer.stop()
        self._debounce.stop()
        self._cooldown.stop()
        try:
            self._app.removeEventFilter(self)
        except Exception:
            pass

    def _on_palette_changed(self, palette=None) -> None:
        if not self._installed or self._applying:
            return
        if IS_LINUX:
            try:
                candidate = QPalette(palette) if isinstance(palette, QPalette) else QPalette(self._app.palette())
                if candidate.color(QPalette.ColorRole.Window).isValid():
                    _state.native_palette = candidate
                    _state.platform_palette = QPalette(candidate)
                    _state.linux_palette_from_event = True
            except Exception:
                logger.debug("could not snapshot Linux desktop palette", exc_info=True)
        self.schedule()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if self._installed and event.type() in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.ThemeChange,
        ):
            self.schedule()
        return False

    # -- change handling ----------------------------------------------------
    def schedule(self, *_args) -> None:
        """Coalesce the burst of events a single theme change produces."""
        if not self._installed:
            return
        self._request()

    def _request(self, *, force: bool = False) -> None:
        if self._applying:
            return  # our own palette application, not the host's
        if not force and self._cooldown.isActive():
            return  # trailing events from the refresh we just performed
        self._debounce.start()

    def poll(self) -> None:
        if not self._installed:
            return
        self._polls += 1
        try:
            current = theme_fingerprint(self._app, platform_dark=False)
        except Exception:
            return
        if current and current != self._last:
            # A fingerprint mismatch is evidence of a real host change, so it
            # bypasses the post-refresh cooldown.
            self._request(force=True)
            return
        if self._polls % self.FULL_PROBE_EVERY == 0:
            try:
                full = theme_fingerprint(self._app)
            except Exception:
                return
            if full and full != self._last_full:
                self._request(force=True)

    def apply_now(self) -> None:
        if self._applying or not self._installed:
            return
        self._applying = True
        try:
            refresh_theme(self._app)
            if callable(self._on_apply):
                try:
                    # Window-level chrome (native title bar, glass appearance)
                    # is outside the widget tree and has to be re-applied too.
                    self._on_apply()
                except Exception:
                    logger.debug("theme on_apply callback failed", exc_info=True)
            self._last = theme_fingerprint(self._app, platform_dark=False)
            self._last_full = theme_fingerprint(self._app)
        finally:
            self._applying = False
            self._cooldown.start()

