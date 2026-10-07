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
import platform
import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

logger = logging.getLogger(__name__)

IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform == "win32" or platform.system() == "Windows"
IS_LINUX = sys.platform.startswith("linux") or platform.system() == "Linux"


def setup_native_app_style(app: QApplication) -> str:
    """Apply the host platform's native QStyle.

    - macOS: 'macintosh' (Aqua/Cocoa native controls)
    - Windows: 'windowsvista' / 'windows' (Native Windows controls)
    - Linux: system desktop default (e.g. Breeze on KDE, Adwaita/Fusion on GNOME)
    """
    keys = [k.lower() for k in QStyleFactory.keys()]
    if IS_MACOS:
        if "macintosh" in keys:
            app.setStyle("macintosh")
            return "macintosh"
    elif IS_WINDOWS:
        if "windowsvista" in keys:
            app.setStyle("windowsvista")
            return "windowsvista"
        elif "windows" in keys:
            app.setStyle("windows")
            return "windows"
    else:  # Linux
        import os
        de = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if ("kde" in de or "plasma" in de) and "breeze" in keys:
            app.setStyle("breeze")
            return "breeze"
        elif "adwaita" in keys:
            app.setStyle("adwaita")
            return "adwaita"
        elif "fusion" in keys:
            app.setStyle("fusion")
            return "fusion"
    return app.style().objectName()


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
            self.fg_primary = os_accent

            # Borders
            self.border        = "#374151" if b else "#3e4451"
            self.border_strong = "#4b5563" if b else "#525860"
            self.border_focus  = os_accent

            # Accent
            self.accent       = os_accent
            self.accent_hover = QColor(os_accent).lighter(115).name()
            self.accent_bg    = "#1a3352"
            self.accent_text  = QColor(os_accent).lighter(170).name()

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
            self.fg_primary = os_accent

            # Borders
            self.border        = "#cbd5e1"
            self.border_strong = "#94a3b8"
            self.border_focus  = os_accent

            # Accent
            self.accent       = os_accent
            self.accent_hover = QColor(os_accent).darker(115).name()
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
        p.setColor(QPalette.Link, QColor(t.accent))
        p.setColor(QPalette.Highlight, QColor(t.accent))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.Midlight, QColor(t.bg_hover))
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
        p.setColor(QPalette.Base, QColor(t.bg_input))
        p.setColor(QPalette.AlternateBase, QColor(t.bg_elev))
        p.setColor(QPalette.ToolTipBase, QColor(t.bg_tooltip))
        p.setColor(QPalette.ToolTipText, QColor(t.fg))
        p.setColor(QPalette.Text, QColor(t.fg))
        p.setColor(QPalette.Button, QColor(t.bg_hover))
        p.setColor(QPalette.ButtonText, QColor(t.fg))
        p.setColor(QPalette.BrightText, QColor("#dc2626"))
        p.setColor(QPalette.Link, QColor(t.accent))
        p.setColor(QPalette.Highlight, QColor(t.accent))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.Midlight, QColor(t.bg_card))
        p.setColor(QPalette.Mid, QColor(t.border))
        p.setColor(QPalette.Dark, QColor(t.border_strong))
        p.setColor(QPalette.Shadow, QColor("#000000"))
        p.setColor(QPalette.PlaceholderText, QColor(t.fg_muted))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.WindowText, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(t.disabled))
    return p


# ---------------------------------------------------------------------------
# QSS generation
# ---------------------------------------------------------------------------

def _build_qss() -> str:
    t = _state.tokens

    # Window background & sidebar transparency for Liquid Glass on macOS
    use_glass = False
    if IS_MACOS:
        try:
            from .glass import is_glass_supported
            use_glass = is_glass_supported()
        except ImportError:
            use_glass = False

    if IS_MACOS or use_glass:
        window_bg = "transparent" if use_glass else t.bg
        nav_bg = "transparent"
        nav_border = "none"
    else:
        window_bg = t.bg
        nav_bg = t.bg_nav
        nav_border = f"1px solid {t.border}"

    return f"""
/* ── Base Window ────────────────────────────────────────
   Only the window surface and the app's own opt-in elements
   (sidebar, semantic labels, log view) are styled here. Standard
   controls — tabs, buttons, combo boxes, text fields, group boxes —
   are deliberately left to the native QStyle so they paint and behave
   like every other app on the host desktop. */
QMainWindow, QDialog {{
    background-color: {window_bg};
    color: {t.fg};
}}

/* ── Navigation sidebar (OS-native docked rail) ──────── */
#navPanel {{
    background-color: {nav_bg};
    border-right: {nav_border};
    border-radius: 0;
}}
#navPanel QLabel {{
    color: {t.fg};
}}
#navPanel QPushButton {{
    background: transparent;
    color: {t.fg};
    text-align: left;
    padding: 8px 12px;
    border-radius: 5px;
    border: 1px solid transparent;
    font-size: 13px;
    font-weight: 500;
    min-height: 34px;
}}
#navPanel QPushButton:hover {{
    background-color: {t.bg_hover};
    color: {t.fg};
}}
#navPanel QPushButton:focus {{
    outline: none;
    border: 1px solid {t.border_focus};
}}
#navPanel QPushButton:checked {{
    background-color: {t.nav_active};
    color: {t.nav_active_text};
    font-weight: 600;
    border: 1px solid transparent;
}}
#navPanel QPushButton:checked:focus {{
    background-color: {t.nav_active};
    color: {t.nav_active_text};
    font-weight: 600;
    outline: none;
    border: 1px solid {t.border_strong};
}}
#navPanel QPushButton[primary="true"],
#navPanel QPushButton[cssClass="primary"] {{
    min-height: 36px;
}}
#navPanel .nav-bottom {{
    color: {t.fg_dim};
    font-size: 12px;
}}

/* ── Release notes rich display (clean translucent view) ─ */
QTextBrowser#releaseNotes, QTextEdit#releaseNotes {{
    background: transparent;
    background-color: transparent;
    border: none;
    color: {t.fg};
}}

/* ── Semantic Labels & Titles (OS-Native Hierarchy) ──── */
QLabel[cssClass="pageTitle"] {{
    font-size: 20px;
    font-weight: 700;
    color: {t.fg};
    letter-spacing: -0.01em;
}}
QLabel[cssClass="sectionTitle"] {{
    font-size: 15px;
    font-weight: 600;
    color: {t.fg};
}}
QLabel[cssClass="cardTitle"] {{
    font-size: 13px;
    font-weight: 600;
    color: {t.fg};
    background: transparent;
    border: none;
}}
QLabel[cssClass="subtitle"] {{
    font-size: 13px;
    color: {t.fg_dim};
    background: transparent;
    border: none;
}}
QLabel[cssClass="dimmed"] {{
    font-size: 12px;
    color: {t.fg_dim};
    background: transparent;
    border: none;
}}
QLabel[cssClass="hint"] {{
    font-size: 13px;
    color: {t.fg_dim};
    background: transparent;
    border: none;
}}
QLabel[cssClass="field-label"] {{
    font-size: 13px;
    font-weight: 600;
    color: {t.fg};
    background: transparent;
    border: none;
}}
QLabel[cssClass="infoValue"] {{
    font-size: 12px;
    font-weight: 600;
    color: {t.fg};
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

/* ── Monospace Diagnostic Console ────────────────────── */
QTextEdit#logView, QPlainTextEdit#logView {{
    background-color: {t.log_bg};
    color: {t.log_fg};
    font-family: "SF Mono", "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid {t.border};
    border-radius: 4px;
    padding: 8px;
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
"""


# Vertical space the window chrome occupies at the top of the client area.
# macOS draws the traffic lights *over* the window when the content view is
# extended (Liquid Glass), so content has to clear them; Windows and Linux
# hand Qt a client area that already starts below the native title bar, where
# any extra inset is pure wasted space.
MACOS_TRAFFIC_LIGHT_CLEARANCE = 12
NATIVE_TITLEBAR_MARGIN = 6
# Page content sits inside the client area regardless of platform.
PAGE_TOP_MARGIN = 8


def content_top_margin() -> int:
    """Inset for the sidebar so its first row sits just below the title bar."""
    if not IS_MACOS:
        return NATIVE_TITLEBAR_MARGIN
    try:
        from .glass import is_glass_supported

        # Only an extended content view puts the traffic lights over the app.
        return MACOS_TRAFFIC_LIGHT_CLEARANCE if is_glass_supported() else NATIVE_TITLEBAR_MARGIN
    except Exception:
        return NATIVE_TITLEBAR_MARGIN


def page_top_margin() -> int:
    """Inset for page content: the top of the page area, never a title-bar gap."""
    return PAGE_TOP_MARGIN


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

    POLL_INTERVAL_MS = 2000
    DEBOUNCE_MS = 150
    # Qt re-broadcasts a palette/theme change caused by *our own* setPalette, so
    # ignore platform events for a moment after each refresh or the app would
    # restyle itself twice for every host change.
    COOLDOWN_MS = 500
    # Every Nth poll also runs the slower cross-platform probes (``defaults``
    # / ``gsettings`` subprocesses), so an accent change that Qt never reports
    # is still caught — without spawning a process every couple of seconds.
    FULL_PROBE_EVERY = 10

    def __init__(self, app: QApplication, parent=None, on_apply=None):
        super().__init__(parent or app)
        self._app = app
        self._applying = False
        self._on_apply = on_apply
        self._polls = 0
        self._last = theme_fingerprint(app, platform_dark=False)
        self._last_full = theme_fingerprint(app)
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
        self._poll_timer.start()

    def stop(self) -> None:
        self._poll_timer.stop()
        self._debounce.stop()
        self._cooldown.stop()
        try:
            self._app.removeEventFilter(self)
        except Exception:
            pass

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if event.type() in (QEvent.Type.ApplicationPaletteChange, QEvent.Type.ThemeChange):
            self.schedule()
        return False

    # -- change handling ----------------------------------------------------
    def schedule(self, *_args) -> None:
        """Coalesce the burst of events a single theme change produces."""
        self._request()

    def _request(self, *, force: bool = False) -> None:
        if self._applying:
            return  # our own palette application, not the host's
        if not force and self._cooldown.isActive():
            return  # trailing events from the refresh we just performed
        self._debounce.start()

    def poll(self) -> None:
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
        if self._applying:
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

