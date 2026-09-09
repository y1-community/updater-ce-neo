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
    - Windows: 'windowsvista' / 'windows'
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
    return app.style().objectName()

# ---------------------------------------------------------------------------
# Theme tokens
# ---------------------------------------------------------------------------

class _Tokens:
    """Flat namespace of colour strings for a single theme (light or dark).

    Strictly adheres to WCAG AA/AAA contrast ratios in both light and dark modes.
    """

    def __init__(self, dark: bool):
        d = dark

        # Surfaces
        self.bg        = "#0f172a" if d else "#f8fafc"
        self.bg_card   = "#1e293b" if d else "#ffffff"
        self.bg_elev   = "#1e293b" if d else "#f1f5f9"
        self.bg_input  = "#0f172a" if d else "#ffffff"
        self.bg_hover  = "#334155" if d else "#f1f5f9"
        self.bg_nav    = "#0b1120"
        self.bg_status = "#1e293b" if d else "#ffffff"
        self.bg_tooltip= "#1e293b" if d else "#ffffff"

        # Text — WCAG AA (>= 4.5:1) & AAA (>= 7:1) compliant
        self.fg         = "#f8fafc" if d else "#0f172a"  # > 17:1 AAA
        self.fg_dim     = "#cbd5e1" if d else "#334155"  # 11.2:1 (dark) / 9.6:1 (light) AAA
        self.fg_muted   = "#94a3b8" if d else "#64748b"  # 5.8:1 (dark) / 4.8:1 (light) AA
        self.fg_primary = "#818cf8" if d else "#2563eb"

        # Borders
        self.border        = "#334155" if d else "#e2e8f0"
        self.border_strong = "#475569" if d else "#cbd5e1"
        self.border_focus  = "#818cf8" if d else "#2563eb"

        # Accent
        self.accent      = "#6366f1" if d else "#2563eb"
        self.accent_hover= "#4f46e5" if d else "#1d4ed8"
        self.accent_bg   = "#1e1b4b" if d else "#eff6ff"
        self.accent_text = "#c7d2fe" if d else "#1e40af"

        # Status Badges (foreground, background) — high contrast in both modes
        self.status_idle       = ("#e2e8f0", "#334155") if d else ("#334155", "#e2e8f0")
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
        self.log_bg = "#030712"
        self.log_fg = "#a3e635"
        self.nav_active = "#2563eb"
        self.disabled = "#334155" if d else "#e2e8f0"
        self.disabled_fg = "#64748b" if d else "#94a3b8"


class _ThemeState:
    """Singleton holding the current palette and tokens."""

    def __init__(self):
        self._dark: bool | None = None
        self.tokens: _Tokens = _Tokens(False)

    def detect(self) -> bool:
        try:
            c = QApplication.palette().color(QPalette.ColorRole.Window)
            self._dark = c.lightness() < 128
        except Exception:
            self._dark = False
        self.tokens = _Tokens(self._dark)
        return self._dark

    def set_dark(self, value: bool) -> None:
        self._dark = value
        self.tokens = _Tokens(value)

    @property
    def is_dark(self) -> bool:
        return self._dark or False


_state = _ThemeState()


def is_dark() -> bool:
    """True when the current theme is dark."""
    return _state.is_dark


def T() -> _Tokens:
    """Return the active token set."""
    return _state.tokens


# ---------------------------------------------------------------------------
# QPalette builder
# ---------------------------------------------------------------------------

def _make_palette(dark: bool) -> QPalette:
    p = QPalette()
    t = _Tokens(dark)
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
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.disabled_fg))
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
        p.setColor(QPalette.Disabled, QPalette.Text, QColor(t.disabled_fg))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(t.disabled_fg))
    return p


# ---------------------------------------------------------------------------
# QSS generation
# ---------------------------------------------------------------------------

def _build_qss() -> str:
    t = _state.tokens

    # Select native font stack per platform
    if IS_MACOS:
        font_stack = '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif'
    elif IS_WINDOWS:
        font_stack = '"Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei", sans-serif'
    else:
        font_stack = 'system-ui, "Inter", "Cantarell", "Ubuntu", "Noto Sans", sans-serif'

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

/* ── Cards ────────────────────────────────────────────── */
QFrame[cssClass="card"] {{
    background-color: {t.bg_card};
    border: 1px solid {t.border};
    border-radius: 12px;
    padding: 6px;
}}

/* ── Buttons (Comfortable Touch Points & High Contrast) ── */
QPushButton[cssClass="primary"] {{
    background-color: {t.accent};
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 9px 24px;
    font-size: 14px;
    font-weight: 700;
    min-height: 36px;
}}
QPushButton[cssClass="primary"]:hover {{
    background-color: {t.accent_hover};
}}
QPushButton[cssClass="primary"]:disabled {{
    background-color: {t.disabled};
    color: {t.disabled_fg};
}}
QPushButton[cssClass="secondary"] {{
    background-color: {t.bg_card};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 9px 24px;
    font-size: 14px;
    font-weight: 600;
    min-height: 36px;
}}
QPushButton[cssClass="secondary"]:hover {{
    background-color: {t.bg_hover};
    border-color: {t.border_strong};
}}
QPushButton[cssClass="secondary"]:disabled {{
    background-color: {t.bg_elev};
    color: {t.disabled_fg};
    border-color: {t.border};
}}
QPushButton[cssClass="danger"] {{
    background-color: #dc2626;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 9px 24px;
    font-size: 14px;
    font-weight: 600;
    min-height: 36px;
}}
QPushButton[cssClass="danger"]:hover {{
    background-color: #b91c1c;
}}
QPushButton[cssClass="ghost"] {{
    background: transparent;
    color: {t.fg_dim};
    border: 1px solid transparent;
    padding: 8px 14px;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 600;
    min-height: 34px;
}}
QPushButton[cssClass="ghost"]:hover {{
    background-color: {t.bg_hover};
    color: {t.fg};
    border-color: {t.border};
}}
QPushButton[cssClass="accent-pill"] {{
    background-color: {t.accent};
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    min-height: 32px;
}}
QPushButton[cssClass="accent-pill"]:hover {{
    background-color: {t.accent_hover};
}}

/* ── Combo boxes (Native Touch Target >= 34px) ────────── */
""" + (f"""
QComboBox {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    min-height: 34px;
}}
QComboBox:hover {{
    border-color: {t.fg_dim};
}}
QComboBox:focus {{
    border-color: {t.border_focus};
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: center right;
    border: none;
    width: 26px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {t.fg_dim};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background-color: {t.bg_card};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 8px;
    padding: 4px;
    selection-background-color: {t.accent_bg};
    selection-color: {t.accent_text};
    outline: 0;
    font-size: 13px;
    outline: none;
}}
QComboBox QAbstractItemView::item {{
    padding: 7px 12px;
    min-height: 28px;
    border-radius: 4px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {t.bg_hover};
}}
""" if not IS_MACOS else f"""
/* Native Cocoa QComboBox on macOS */
QComboBox {{
    font-size: 13px;
    min-height: 34px;
}}
QComboBox QAbstractItemView {{
    background-color: {t.bg_card};
    color: {t.fg};
    selection-background-color: {t.accent_bg};
    selection-color: {t.accent_text};
}}
""") + f"""

/* ── Tabs (Comfortable Touch Targets >= 36px) ─────────── */
QTabWidget::pane {{
    border: 1px solid {t.border};
    border-radius: 10px;
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
    padding: 8px 20px;
    min-height: 36px;
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

/* ── List widget ──────────────────────────────────────── */
QListWidget {{
    background-color: {t.bg_card};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 10px;
    padding: 4px;
    font-size: 13px;
    outline: none;
}}
QListWidget::item {{
    padding: 8px 12px;
    border-radius: 6px;
    margin: 2px 2px;
    min-height: 34px;
}}
QListWidget::item:selected {{
    background-color: {t.accent_bg};
    color: {t.accent_text};
    font-weight: 600;
}}
QListWidget::item:hover:!selected {{
    background-color: {t.bg_hover};
    color: {t.fg};
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
    border-radius: 10px;
    padding: 12px;
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
    border-radius: 10px;
    padding: 12px;
}}

/* ── Line edit (Min Height >= 36px) ───────────────────── */
QLineEdit {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 13px;
    min-height: 36px;
}}
QLineEdit:focus {{
    border-color: {t.border_focus};
}}
QLineEdit::placeholder {{
    color: {t.fg_muted};
}}

/* ── Scrollbars ───────────────────────────────────────── */
""" + (f"""
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {t.border};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {t.fg_dim};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t.border};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {t.fg_dim};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
""" if not IS_MACOS else "/* Native overlay scrollbars on macOS */\n") + f"""

/* ── Tooltips ─────────────────────────────────────────── */
QToolTip {{
    background-color: {t.bg_tooltip};
    color: {t.fg};
    border: 1px solid {t.border};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ── Dialog buttons ───────────────────────────────────── */
QDialogButtonBox QPushButton {{
    background-color: {t.accent};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 20px;
    font-weight: 600;
    min-width: 70px;
}}
QDialogButtonBox QPushButton:hover {{
    background-color: {t.accent_hover};
}}
QDialogButtonBox QPushButton:flat {{
    background-color: transparent;
    color: {t.fg};
}}
QDialogButtonBox QPushButton:flat:hover {{
    background-color: {t.bg_hover};
}}

/* ── Group box ────────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {t.border};
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 12px;
    font-weight: 600;
    color: {t.fg};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    padding: 0 8px;
}}
"""


def apply_theme(app: QApplication, force_dark: bool | None = None) -> None:
    """Apply the Innioasis Lumen theme to *app*.

    Ensures native platform QStyle is initialized.
    If *force_dark* is ``None`` the system palette is inspected.
    Call ``apply_theme(app, force_dark=True)`` to lock dark mode.
    """
    setup_native_app_style(app)
    if force_dark is not None:
        _state.set_dark(force_dark)
    else:
        _state.detect()
    app.setPalette(_make_palette(_state.is_dark))
    app.setStyleSheet(_build_qss())


def refresh_theme(app: QApplication) -> None:
    """Re-read the palette and regenerate the QSS (e.g. after a system theme change)."""
    _state.detect()
    app.setPalette(_make_palette(_state.is_dark))
    app.setStyleSheet(_build_qss())
