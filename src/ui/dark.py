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

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
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


class _ClassicWindowsHoverStyle(QProxyStyle):
    """Hover wash drawn after the native Windows button.

    The classic ``windows`` style only changes when a button is pressed. WinUI
    (``windows11``) and ``windowsvista`` do paint a hot state, but a fully
    replaced dark palette often leaves that hot state the same color as the
    idle button, so the control looks inert. This draws one translucent
    accent wash after the native bevel. Pressed and checked buttons keep the
    base style's own look. Sidebar rows paint themselves and never reach here.
    """

    def drawControl(self, element, option, painter, widget=None):  # noqa: ANN001
        super().drawControl(element, option, painter, widget)
        if element != QStyle.ControlElement.CE_PushButton:
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
    if system == "linux":
        override = os.environ.get("QT_STYLE_OVERRIDE", "")
    candidates = native_style_candidates(
        system=system,
        offscreen=offscreen and IS_MACOS,
        desktop=desktop,
        style_override=override,
    )
    key = _factory_style(*candidates)
    if key:
        if IS_WINDOWS:
            app.setStyle(_ClassicWindowsHoverStyle(key))
        else:
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


class _Tokens:
    """Flat namespace of colour strings for a single theme (light or dark).

    Enforces WCAG AAA compliance:
    - In Light Mode: Very black fonts (#000000, #111827) for crisp legibility
      against white surfaces and light frosted Liquid Glass.
    - In Dark Mode: Very white fonts (#ffffff, #f9fafb) for crisp legibility
      against dark surfaces and dark smoked Liquid Glass.
    """

    def __init__(self, dark: bool, pure_black: bool = False, palette: QPalette | None = None):
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
                self.bg_nav     = "#181b20"
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


class _ThemeState:
    """Singleton holding the current palette and tokens."""

    def __init__(self):
        self._dark: bool | None = None
        self._pure_black: bool = False
        self.tokens: _Tokens = _Tokens(False)

    def detect(self) -> bool:
        self._dark = is_system_dark_mode()
        self._pure_black = False
        self.tokens = _Tokens(self._dark, pure_black=self._pure_black)
        return self._dark

    def set_dark(self, value: bool, pure_black: bool = False, palette: QPalette | None = None) -> None:
        self._dark = value
        self._pure_black = pure_black
        self.tokens = _Tokens(value, pure_black=pure_black, palette=palette)

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


def _make_palette(dark: bool, pure_black: bool = False) -> QPalette:
    p = QPalette()
    t = _Tokens(dark, pure_black=pure_black)
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
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.AlternateBase,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
        QPalette.ColorRole.Highlight,
        QPalette.ColorRole.HighlightedText,
        QPalette.ColorRole.PlaceholderText,
    ):
        pal.setColor(role, src.color(role))
    if progress:
        # One text color has to clear both the unfilled groove and the filled
        # chunk. Stylesheet ``palette()`` colors do not: a parent card replaces
        # the palette and the percent is painted black on the dark bar.
        groove = src.color(QPalette.ColorRole.AlternateBase)
        chunk = src.color(QPalette.ColorRole.Highlight)
        text = src.color(QPalette.ColorRole.WindowText)
        if contrast_ratio(text, groove) < 4.5:
            text = _readable_on(groove)
        chunk = _shift_until_contrast(text, chunk)
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

    # Window background & sidebar transparency for Liquid Glass (macOS) and Acrylic/Mica (Windows)
    use_glass = False
    try:
        from .glass import is_glass_supported, is_windows_acrylic_supported
        use_glass = is_glass_supported() or is_windows_acrylic_supported()
    except ImportError:
        use_glass = False

    # The window itself is transparent so Liquid Glass / acrylic shows in the
    # margins, sidebar gaps, and title bar. Cards are a light frost. Labels
    # that change text are not left transparent: they erase their own rect.
    window_bg = t.bg
    dialog_bg = t.bg_card
    dialog_border = f"1px solid {t.border}"
    card_bg = t.bg_card
    card_border = t.border
    title_bg = "transparent"
    title_color = "palette(window-text)"
    text_color = "palette(window-text)"
    if use_glass:
        window_bg = "transparent"
        dialog_border = "1px solid rgba(255, 255, 255, 0.12)" if _state.is_dark else "1px solid rgba(0, 0, 0, 0.10)"
        # The card's own paint supplies a faint frost. A stylesheet fill here
        # would stack into a solid plate.
        card_bg = "transparent"
        card_border = "transparent"
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
    background: transparent;
}}

/* ── Native Cards & Section Panels ───────────────────── */
QFrame[cssClass="card"] {{
    background-color: {card_bg};
    border: 1px solid {card_border};
    border-radius: 8px;
}}

/* ── Navigation sidebar ─────────────────────────────────
   The rail itself is not styled here. A stylesheet that matches the
   sidebar also restyles every button inside it, and those buttons then
   paint as blank plates. The window sets the rail color from the palette
   and leaves the buttons to the platform style. */

/* ── Release notes and the version list ─────────────────
   No background here. A stylesheet fill, including a zero-alpha rgba that
   Qt stores as black, paints a rectangle over the glass. The palette leaves
   these views clear, and the platform style draws the selection. */
QTextBrowser#releaseNotes, QTextEdit#releaseNotes,
QTextBrowser#releaseNotes::viewport, QTextEdit#releaseNotes::viewport {{
    background: transparent;
    border: none;
    color: {text_color};
}}

QListWidget#releaseList,
QListView#releaseList,
QListWidget#releaseList::viewport,
QListView#releaseList::viewport {{
    background: transparent;
    border: none;
    color: {text_color};
    outline: none;
}}
QListWidget#releaseList::item, QListView#releaseList::item {{
    padding: 3px 6px;
    border-radius: 4px;
    color: {text_color};
}}
QListWidget#releaseList::item:hover, QListView#releaseList::item:hover {{
    background-color: {t.bg_hover};
}}
QListWidget#releaseList::item:selected, QListView#releaseList::item:selected {{
    background-color: {t.accent};
    color: {t.nav_active_text};
}}
QListWidget#releaseList::item:selected:hover, QListView#releaseList::item:selected:hover {{
    background-color: {t.accent_hover};
    color: {t.nav_active_text};
}}


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

/* ── Monospace Diagnostic Console ──────────────────────
   Styled from here, never from the widget: a stylesheet set *on* a scroll
   area makes Qt answer SH_ScrollBar_Transient with 0 and the host's floating
   bars silently become classic ones (see src/ui/scrollbars.py). */
QTextEdit#logView, QPlainTextEdit#logView {{
    background-color: {t.log_bg};
    color: {t.log_fg};
    font-family: "SF Mono", "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid {t.border};
    border-radius: 4px;
    padding: 8px;
}}
QTextEdit#statusView, QPlainTextEdit#statusView {{
    background-color: {t.bg};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    font-family: "SF Mono", "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 11px;
    padding: 8px;
}}
QTextBrowser#updateNotes, QTextEdit#updateNotes {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 6px;
}}

/* ── Tooltips ─────────────────────────────────────────── */
QToolTip {{
    background-color: {t.bg_tooltip};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 4px;
    padding: 4px 8px;
    font-size: 12px;
}}

/* ── Buttons are the platform's, not ours ─────────────────
   Every action button (primary, secondary, navigation) is drawn by the host
   QStyle: Aqua / WinUI / Adwaita / Breeze own the fill, the bevel, the focus
   ring, the disabled state and the keyboard cues, exactly as they do in the
   platform's own apps. The cssClass properties remain as semantic labels for
   wiring and tests; nothing here paints them, so a statically styled button
   can never disagree with the desktop's accessibility settings (high
   contrast, accent colour, "reduce motion" pressed states). */
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
    if IS_MACOS:
        return [
            ".AppleSystemUIFont", "SF Pro Text", "SF Pro Display",
            "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei",
        ]
    if IS_WINDOWS:
        return ["Segoe UI", "Segoe UI Variable Text", "Microsoft YaHei"]
    return ["Noto Sans", "Cantarell", "Ubuntu", "WenQuanYi Micro Hei"]


def _apply_native_font(app: QApplication) -> None:
    """Set the platform UI font (and CJK fallbacks) on the application."""
    font = QFont()
    font.setFamilies(native_font_families())
    if IS_MACOS:
        font.setPointSize(13)
    elif IS_WINDOWS:
        font.setPointSize(10)
    else:
        # Keep whatever size the desktop environment selected.
        size = app.font().pointSizeF()
        font.setPointSizeF(size if size > 0 else 10.0)
    app.setFont(font)


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
    setup_native_app_style(app)
    _apply_native_font(app)
    if force_dark is not None:
        _state.set_dark(force_dark, pure_black=bool(pure_black))
    else:
        _state.detect()
    app.setPalette(_make_palette(_state.is_dark, pure_black=_state.is_pure_black))
    app.setStyleSheet(_build_qss())


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
        system_palette = app.style().standardPalette()
    except Exception:
        system_palette = app.palette()
    accent, accent_text = get_native_accent_color(system_palette)
    return (
        scheme,
        is_system_dark_mode() if platform_dark else None,
        accent,
        accent_text,
        system_palette.color(QPalette.ColorRole.Window).name(),
        system_palette.color(QPalette.ColorRole.WindowText).name(),
    )


def refresh_theme(app: QApplication | None = None) -> None:
    """Re-read the host appearance and repaint the whole app with the new tokens.

    Re-applies the font (desktop environments can change it together with the
    theme), the palette, the stylesheet, and then asks every widget that owns
    inline styling to restyle itself.
    """
    app = app or QApplication.instance()
    _state.detect()
    if not app:
        return
    _apply_native_font(app)
    app.setPalette(_make_palette(_state.is_dark, pure_black=_state.is_pure_black))
    app.setStyleSheet(_build_qss())
    # Descend the whole tree, not just top-level windows: pages and custom
    # widgets keep their own token-derived styles.
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
                self._app.paletteChanged.connect(self.schedule)
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

