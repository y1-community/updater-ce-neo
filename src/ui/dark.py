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

    Strictly adheres to WCAG AA/AAA contrast ratios in both light and dark modes,
    including pure black / OLED high-contrast modes.
    """

    def __init__(self, dark: bool, pure_black: bool = False):
        d = dark
        b = dark and pure_black

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
            # Neutral desktop dark slate/gray (matching KDE Breeze Dark / Fusion / firmware_downloader.py)
            self.bg        = "#23272e" if d else "#f8fafc"
            self.bg_card   = "#2b303c" if d else "#ffffff"
            self.bg_elev   = "#282c34" if d else "#f1f5f9"
            self.bg_input  = "#1e2227" if d else "#ffffff"
            self.bg_hover  = "#353b48" if d else "#f1f5f9"
            self.bg_nav    = "#1e2227" if d else "#ebeff3"
            self.bg_status = "#2b303c" if d else "#ffffff"
            self.bg_tooltip= "#2b303c" if d else "#ffffff"

        # Text — WCAG AA (>= 4.5:1) & AAA (>= 7:1) compliant
        self.fg         = "#ffffff" if b else ("#f0f3f6" if d else "#0f172a")  # > 13:1 AAA
        self.fg_dim     = "#e2e8f0" if b else ("#cbd5e1" if d else "#334155")  # > 8.8:1 AAA
        self.fg_muted   = "#94a3b8" if d else "#64748b"  # > 4.8:1 AA
        self.fg_primary = "#58a6ff" if d else "#2563eb"

        # Borders
        self.border        = "#374151" if b else ("#3e4451" if d else "#cbd5e1")
        self.border_strong = "#4b5563" if b else ("#4f5666" if d else "#94a3b8")
        self.border_focus  = "#58a6ff" if d else "#2563eb"

        # Accent
        self.accent      = "#388bfd" if d else "#2563eb"
        self.accent_hover= "#1f6feb" if d else "#1d4ed8"
        self.accent_bg   = "#1a3352" if d else "#eff6ff"
        self.accent_text = "#93c5fd" if d else "#1e40af"

        # Status Badges (foreground, background) — high contrast in both modes
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

        # Banners (info / ok / warn / danger)
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
        self.nav_active = "#2563eb"
        self.disabled = "#334155" if d else "#e2e8f0"
        self.disabled_fg = "#94a3b8" if d else "#64748b"  # High contrast in both modes (>= 4.5:1 AA)


class _ThemeState:
    """Singleton holding the current palette and tokens."""

    def __init__(self):
        self._dark: bool | None = None
        self._pure_black: bool = False
        self.tokens: _Tokens = _Tokens(False)

    def detect(self) -> bool:
        try:
            c = QApplication.palette().color(QPalette.ColorRole.Window)
            self._dark = c.lightness() < 128
            self._pure_black = c.lightness() < 24 or c.name().lower() == "#000000"
        except Exception:
            self._dark = False
            self._pure_black = False
        self.tokens = _Tokens(self._dark, pure_black=self._pure_black)
        return self._dark

    def set_dark(self, value: bool, pure_black: bool = False) -> None:
        self._dark = value
        self._pure_black = pure_black
        self.tokens = _Tokens(value, pure_black=pure_black)

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
        font_stack = 'system-ui, -apple-system, BlinkMacSystemFont, "SF Pro Text", "SF Pro Display", "Helvetica Neue", sans-serif'
    elif IS_WINDOWS:
        font_stack = '"Segoe UI Variable Text", "Segoe UI Variable Display", "Segoe UI", -apple-system, "Microsoft YaHei UI", "Microsoft YaHei", sans-serif'
    else:
        font_stack = 'system-ui, -apple-system, "Cantarell", "Ubuntu", "Inter", "Noto Sans", "Liberation Sans", sans-serif'

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
        nav_border = "1px solid #1a2538"

    return f"""
/* ── Global ────────────────────────────────────────────── */
* {{
    font-family: {font_stack};
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {window_bg};
    color: {t.fg};
}}

/* ── Navigation sidebar (always dark, translucent on glass) ── */
#navPanel {{
    background-color: {nav_bg};
    border-right: {nav_border};
    border-radius: 12px 0 0 12px;
}}
#navPanel QLabel {{
    color: #f1f5f9;
}}
#navPanel QPushButton {{
    background: transparent;
    color: #cbd5e1;
    text-align: left;
    padding: 10px 14px;
    border-radius: 8px;
    border: none;
    font-size: 13px;
    min-height: 34px;
}}
#navPanel QPushButton:hover {{
    background-color: #1e293b;
    color: #f8fafc;
}}
#navPanel QPushButton:checked {{
    background-color: {t.nav_active};
    color: #ffffff;
    font-weight: 600;
}}
#navPanel .nav-bottom {{
    color: #94a3b8;
    font-size: 12px;
}}

/* ── Semantic Labels & Titles (Dual Theme High Contrast) ─ */
QLabel[cssClass="pageTitle"] {{
    font-size: 22px;
    font-weight: 800;
    color: {t.fg};
    letter-spacing: -0.02em;
}}
QLabel[cssClass="sectionTitle"] {{
    font-size: 16px;
    font-weight: 700;
    color: {t.fg};
}}
QLabel[cssClass="cardTitle"] {{
    font-size: 14px;
    font-weight: 700;
    color: {t.fg};
    letter-spacing: -0.01em;
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
    border-radius: 8px;
    padding: 6px 12px;
}}

/* ── Cards & Separators ───────────────────────────────── */
QFrame[cssClass="card"] {{
    background-color: {t.bg_card};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 6px;
}}
QLabel[cssClass="separator"], QFrame[cssClass="separator"] {{
    background-color: {t.border};
    min-height: 1px;
    max-height: 1px;
    border: none;
}}

/* ── Buttons (Preserve Native OS QStyle / UxTheme / Cocoa / Breeze) ──── */
/* Accessibility targets: min-height: 36px; min-height: 34px; */

/* ── Sidebar Language Selector ────────────────────────── */
#navPanel QComboBox, QComboBox#langCombo {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 500;
    min-height: 28px;
}}
#navPanel QComboBox:hover, QComboBox#langCombo:hover {{
    background-color: {t.bg_hover};
    border-color: {t.border_strong};
}}
#navPanel QComboBox::drop-down, QComboBox#langCombo::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 20px;
}}
#navPanel QComboBox::down-arrow, QComboBox#langCombo::down-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {t.fg_dim};
    margin-right: 6px;
}}
#navPanel QComboBox QAbstractItemView, QComboBox#langCombo QAbstractItemView {{
    background-color: {t.bg_card};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 4px;
    selection-background-color: {t.accent_bg};
    selection-color: {t.accent_text};
    font-size: 12px;
    outline: none;
}}

/* ── Content area Combo boxes & Buttons: 100% Native QStyle delegates ── */

/* ── Native Checkboxes & Radio Buttons ────────────────── */
QCheckBox, QRadioButton {{
    color: {t.fg};
    font-size: 13px;
    spacing: 8px;
    min-height: 22px;
    background: transparent;
}}
QCheckBox:disabled, QRadioButton:disabled {{
    color: {t.disabled_fg};
}}

/* ── Tabs (Native Clean Style) ────────────────────────── */
QTabWidget::pane {{
    border: 1px solid {t.border};
    border-radius: 8px;
    background-color: {t.bg_card};
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background-color: transparent;
    color: {t.fg_dim};
    border: none;
    padding: 8px 18px;
    min-height: 32px;
    font-size: 13px;
    font-weight: 600;
    border-bottom: 2px solid transparent;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    color: {t.fg_primary};
    font-weight: 700;
    border-bottom: 2px solid {t.accent};
}}
QTabBar::tab:hover:!selected {{
    color: {t.fg};
}}

/* ── Native List widget ───────────────────────────────── */
QListWidget {{
    background-color: {t.bg_card};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 2px;
    font-size: 13px;
    outline: none;
}}
QListWidget::item {{
    padding: 6px 10px;
    min-height: 28px;
}}

/* ── Progress bar (Smooth & High Contrast) ────────────── */
QProgressBar {{
    border: none;
    border-radius: 7px;
    background-color: {t.progress_track};
    text-align: center;
    min-height: 14px;
    max-height: 14px;
    color: transparent;
}}
QProgressBar::chunk {{
    background-color: {t.progress_fill};
    border-radius: 7px;
}}
QProgressBar[status="error"]::chunk {{
    background-color: {t.progress_err};
}}
QProgressBar[status="success"]::chunk {{
    background-color: {t.progress_ok};
}}

/* ── Text edit ────────────────────────────────────────── */
QTextEdit {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 10px;
    font-size: 13px;
    selection-background-color: {t.accent_bg};
    selection-color: {t.accent_text};
}}
QTextEdit[readOnly="true"] {{
    background-color: {t.bg_elev};
}}
QTextEdit#logView {{
    background-color: {t.log_bg};
    color: {t.log_fg};
    font-family: "Cascadia Code", "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: none;
    border-radius: 8px;
    padding: 10px;
}}

/* ── Line edit ────────────────────────────────────────── */
QLineEdit {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border_strong};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 13px;
    min-height: 32px;
}}
QLineEdit:focus {{
    border-color: {t.border_focus};
}}
QLineEdit::placeholder {{
    color: {t.fg_muted};
}}

/* ── Tooltips ─────────────────────────────────────────── */
QToolTip {{
    background-color: {t.bg_tooltip};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 6px 10px;
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
    If *force_dark* is ``None`` the system palette is inspected.
    Call ``apply_theme(app, force_dark=True)`` to lock dark mode.
    """
    setup_native_app_style(app)
    if force_dark is not None:
        _state.set_dark(force_dark, pure_black=bool(pure_black))
    else:
        _state.detect()
    app.setPalette(_make_palette(_state.is_dark, pure_black=_state.is_pure_black))
    app.setStyleSheet(_build_qss())


def refresh_theme(app: QApplication) -> None:
    """Re-read the palette and regenerate the QSS (e.g. after a system theme change)."""
    _state.detect()
    app.setPalette(_make_palette(_state.is_dark, pure_black=_state.is_pure_black))
    app.setStyleSheet(_build_qss())
