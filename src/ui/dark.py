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

import platform
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

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

class _Tokens:
    """Flat namespace of colour strings for a single theme (light or dark).

    Extracts values directly from the host system's QPalette when available
    (matching KDE Plasma, GNOME, Windows, macOS native color schemes),
    strictly adhering to WCAG contrast standards.
    """

    def __init__(self, dark: bool, pure_black: bool = False, palette: QPalette | None = None):
        d = dark
        b = dark and pure_black

        if palette is not None:
            w = palette.color(QPalette.ColorRole.Window)
            fg = palette.color(QPalette.ColorRole.WindowText)
            base = palette.color(QPalette.ColorRole.Base)
            alt = palette.color(QPalette.ColorRole.AlternateBase)
            btn = palette.color(QPalette.ColorRole.Button)
            hl = palette.color(QPalette.ColorRole.Highlight)
            hlt = palette.color(QPalette.ColorRole.HighlightedText)
            mid = palette.color(QPalette.ColorRole.Mid)
            dark_c = palette.color(QPalette.ColorRole.Dark)
            midlight = palette.color(QPalette.ColorRole.Midlight)
            placeholder = palette.color(QPalette.ColorRole.PlaceholderText)
            tooltip_base = palette.color(QPalette.ColorRole.ToolTipBase)
            disabled_w = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Window)
            disabled_t = palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)

            if b:
                self.bg        = "#000000"
                self.bg_card   = "#0b0f19"
                self.bg_elev   = "#111827"
                self.bg_input  = "#000000"
                self.bg_hover  = "#1e293b"
                self.bg_nav    = "#000000"
                self.bg_status = "#0b0f19"
                self.bg_tooltip= "#111827"
            else:
                self.bg        = w.name()
                self.bg_card   = btn.name() if d else alt.name()
                self.bg_elev   = alt.name() if alt.isValid() else (btn.name() if d else "#f1f5f9")
                self.bg_input  = base.name()
                self.bg_hover  = midlight.name() if (midlight.isValid() and midlight != w) else (btn.name() if d else alt.name())
                self.bg_nav    = base.name() if (IS_LINUX and not b) else (alt.name() if not b else "#000000")
                self.bg_status = alt.name()
                self.bg_tooltip= tooltip_base.name() if tooltip_base.isValid() else btn.name()

            self.fg         = fg.name()
            self.fg_dim     = placeholder.name() if placeholder.isValid() else ("#cbd5e1" if d else "#334155")
            self.fg_muted   = placeholder.name() if placeholder.isValid() else ("#94a3b8" if d else "#64748b")
            self.fg_primary = hl.name()

            self.border        = mid.name() if mid.isValid() else ("#373b40" if d else "#cbd5e1")
            self.border_strong = dark_c.name() if dark_c.isValid() else ("#525860" if d else "#94a3b8")
            self.border_focus  = hl.name()

            self.accent       = hl.name()
            self.accent_hover = hl.lighter(115).name() if d else hl.darker(115).name()
            self.accent_bg    = alt.name()
            self.accent_text  = hlt.name()

            self.nav_active      = hl.name()
            self.nav_active_text = hlt.name()

            self.status_idle       = (self.fg_dim, self.bg_elev)
            self.status_idle_fg    = self.status_idle
            self.status_selected   = (self.accent_text, self.accent)
            self.status_sel_fg     = self.status_selected
            self.status_connected  = ("#6ee7b7", "#064e3b") if d else ("#065f46", "#d1fae5")
            self.status_conn_fg    = self.status_connected
            self.status_disconn    = ("#fca5a5", "#7f1d1d") if d else ("#991b1b", "#fee2e2")
            self.status_disconn_fg = self.status_disconn
            self.status_flashing   = ("#fde68a", "#78350f") if d else ("#92400e", "#fef3c7")
            self.status_flash_fg   = self.status_flashing
            self.status_complete   = ("#6ee7b7", "#064e3b") if d else ("#065f46", "#d1fae5")
            self.status_comp_fg    = self.status_complete
            self.status_failed     = ("#fca5a5", "#7f1d1d") if d else ("#991b1b", "#fee2e2")
            self.status_fail_fg    = self.status_failed
            self.status_retry      = ("#fde68a", "#78350f") if d else ("#92400e", "#fef3c7")
            self.status_retry_fg   = self.status_retry

            self.info_bg    = self.bg_elev
            self.info_fg    = self.accent
            self.ok_bg      = "#064e3b" if d else "#d1fae5"
            self.ok_fg      = "#6ee7b7" if d else "#065f46"
            self.warn_bg    = "#451a03" if d else "#fef3c7"
            self.warn_fg    = "#fde68a" if d else "#92400e"
            self.danger_bg  = "#7f1d1d" if d else "#fee2e2"
            self.danger_fg  = "#fca5a5" if d else "#991b1b"

            self.progress_track = self.bg_elev
            self.progress_fill  = self.accent
            self.progress_ok    = "#10b981" if d else "#059669"
            self.progress_err   = "#ef4444" if d else "#dc2626"

            self.log_bg = "#000000" if b else self.bg_input
            self.log_fg = "#a3e635"
            self.disabled = disabled_w.name() if disabled_w.isValid() else ("#334155" if d else "#e2e8f0")
            self.disabled_fg = disabled_t.name() if disabled_t.isValid() else ("#94a3b8" if d else "#64748b")
        else:
            # Surfaces
            if b:
                self.bg        = "#000000"
                self.bg_card   = "#0b0f19"
                self.bg_elev   = "#111827"
                self.bg_input  = "#000000"
                self.bg_hover  = "#1e293b"
                self.bg_nav    = "#000000"
                self.bg_status = "#0b0f19"
                self.bg_tooltip= "#111827"
            else:
                self.bg        = "#23272e" if d else "#f8fafc"
                self.bg_card   = "#2b303c" if d else "#ffffff"
                self.bg_elev   = "#282c34" if d else "#f1f5f9"
                self.bg_input  = "#1e2227" if d else "#ffffff"
                self.bg_hover  = "#353b48" if d else "#f1f5f9"
                self.bg_nav    = "#1e2227" if d else "#ebeff3"
                self.bg_status = "#2b303c" if d else "#ffffff"
                self.bg_tooltip= "#2b303c" if d else "#ffffff"

            # Text — WCAG AA (>= 4.5:1) & AAA (>= 7:1) compliant
            self.fg         = "#ffffff" if b else ("#f0f3f6" if d else "#0f172a")
            self.fg_dim     = "#e2e8f0" if b else ("#cbd5e1" if d else "#334155")
            self.fg_muted   = "#94a3b8" if d else "#64748b"
            self.fg_primary = "#58a6ff" if d else "#2563eb"

            # Borders
            self.border        = "#374151" if b else ("#3e4451" if d else "#cbd5e1")
            self.border_strong = "#4b5563" if b else ("#4f5666" if d else "#94a3b8")
            self.border_focus  = "#58a6ff" if d else "#2563eb"

            # Accent
            self.accent       = "#388bfd" if d else "#2563eb"
            self.accent_hover = "#1f6feb" if d else "#1d4ed8"
            self.accent_bg    = "#1a3352" if d else "#eff6ff"
            self.accent_text  = "#93c5fd" if d else "#1e40af"

            self.nav_active      = "#2563eb"
            self.nav_active_text = "#ffffff"

            # Status Badges
            self.status_idle       = ("#e2e8f0", "#334155") if d else ("#1e293b", "#e2e8f0")
            self.status_idle_fg    = self.status_idle
            self.status_selected   = ("#bfdbfe", "#1e3a5f") if d else ("#1e40af", "#dbeafe")
            self.status_sel_fg     = self.status_selected
            self.status_connected  = ("#6ee7b7", "#064e3b") if d else ("#065f46", "#d1fae5")
            self.status_conn_fg    = self.status_connected
            self.status_disconn    = ("#fca5a5", "#7f1d1d") if d else ("#991b1b", "#fee2e2")
            self.status_disconn_fg = self.status_disconn
            self.status_flashing   = ("#fde68a", "#78350f") if d else ("#92400e", "#fef3c7")
            self.status_flash_fg   = self.status_flashing
            self.status_complete   = ("#6ee7b7", "#064e3b") if d else ("#065f46", "#d1fae5")
            self.status_comp_fg    = self.status_complete
            self.status_failed     = ("#fca5a5", "#7f1d1d") if d else ("#991b1b", "#fee2e2")
            self.status_fail_fg    = self.status_failed
            self.status_retry      = ("#fde68a", "#78350f") if d else ("#92400e", "#fef3c7")
            self.status_retry_fg   = self.status_retry

            # Banners
            self.info_bg    = "#1e3a5f" if d else "#eff6ff"
            self.info_fg    = "#93c5fd" if d else "#1e40af"
            self.ok_bg      = "#064e3b" if d else "#d1fae5"
            self.ok_fg      = "#6ee7b7" if d else "#065f46"
            self.warn_bg    = "#451a03" if d else "#fef3c7"
            self.warn_fg    = "#fde68a" if d else "#92400e"
            self.danger_bg  = "#7f1d1d" if d else "#fee2e2"
            self.danger_fg  = "#fca5a5" if d else "#991b1b"

            # Progress
            self.progress_track = "#334155" if d else "#e2e8f0"
            self.progress_fill  = "#6366f1" if d else "#2563eb"
            self.progress_ok    = "#10b981" if d else "#059669"
            self.progress_err   = "#ef4444" if d else "#dc2626"

            # Misc
            self.log_bg = "#000000" if b else "#030712"
            self.log_fg = "#a3e635"
            self.disabled = "#334155" if d else "#e2e8f0"
            self.disabled_fg = "#94a3b8" if d else "#64748b"


class _ThemeState:
    """Singleton holding the current palette and tokens."""

    def __init__(self):
        self._dark: bool | None = None
        self._pure_black: bool = False
        self.tokens: _Tokens = _Tokens(False)

    def detect(self) -> bool:
        try:
            pal = QApplication.palette()
            c = pal.color(QPalette.ColorRole.Window)
            self._dark = c.lightness() < 128
            self._pure_black = c.lightness() < 24 or c.name().lower() == "#000000"
            self.tokens = _Tokens(self._dark, pure_black=self._pure_black, palette=pal)
        except Exception:
            self._dark = False
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

    # Select native font stack per platform
    if IS_MACOS:
        font_stack = '".AppleSystemUIFont", "SF Pro Text", "SF Pro Display", -apple-system, sans-serif'
    elif IS_WINDOWS:
        font_stack = '"Segoe UI", "Segoe UI Variable Text", sans-serif'
    else:
        font_stack = 'system-ui, "Noto Sans", "Cantarell", "Ubuntu", sans-serif'

    # Window background & sidebar transparency for Liquid Glass on macOS
    use_glass = False
    if IS_MACOS:
        try:
            from .glass import is_glass_supported
            use_glass = is_glass_supported()
        except ImportError:
            use_glass = False

    if use_glass:
        window_bg = "transparent"
        nav_bg = "rgba(11, 17, 32, 0.75)"
        nav_border = "1px solid rgba(255, 255, 255, 0.12)"
    else:
        window_bg = t.bg
        nav_bg = t.bg_nav
        nav_border = f"1px solid {t.border}"

    return f"""
/* ── Global Typography & Base Window ─────────────────── */
* {{
    font-family: {font_stack};
}}
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
    color: {t.fg_dim};
    text-align: left;
    padding: 8px 12px;
    border-radius: 5px;
    border: none;
    font-size: 13px;
    min-height: 34px;
}}
#navPanel QPushButton:hover {{
    background-color: {t.bg_hover};
    color: {t.fg};
}}
#navPanel QPushButton:checked {{
    background-color: {t.nav_active};
    color: #ffffff;
    font-weight: 600;
}}
#navPanel QPushButton[primary="true"],
#navPanel QPushButton[cssClass="primary"] {{
    min-height: 36px;
}}
#navPanel .nav-bottom {{
    color: {t.fg_muted};
    font-size: 12px;
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
    color: {t.fg_dim};
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
QTextEdit#logView {{
    background-color: {t.log_bg};
    color: {t.log_fg};
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
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


def apply_theme(
    app: QApplication,
    force_dark: bool | None = None,
    pure_black: bool | None = None,
) -> None:
    """Apply the native platform theme to *app*.

    Ensures native platform QStyle is initialized.
    If *force_dark* is ``None`` the host system's native QPalette is preserved
    and token colors are extracted directly from the system theme.
    Call ``apply_theme(app, force_dark=True)`` to lock dark mode.
    """
    setup_native_app_style(app)
    if force_dark is not None:
        _state.set_dark(force_dark, pure_black=bool(pure_black))
        app.setPalette(_make_palette(_state.is_dark, pure_black=_state.is_pure_black))
    else:
        _state.detect()
    app.setStyleSheet(_build_qss())


def refresh_theme(app: QApplication) -> None:
    """Re-read the palette and regenerate the QSS (e.g. after a system theme change)."""
    _state.detect()
    app.setStyleSheet(_build_qss())
