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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

# ---------------------------------------------------------------------------
# Theme tokens
# ---------------------------------------------------------------------------

class _Tokens:
    """Flat namespace of colour strings for a single theme (light or dark)."""

    def __init__(self, dark: bool):
        d = dark

        # Surfaces
        self.bg        = "#111827" if d else "#f9fafb"
        self.bg_card   = "#1f2937" if d else "#ffffff"
        self.bg_elev   = "#1f2937" if d else "#f9fafb"
        self.bg_input  = "#111827" if d else "#ffffff"
        self.bg_hover  = "#374151" if d else "#f3f4f6"
        self.bg_nav    = "#0b1120"
        self.bg_status = "#1f2937" if d else "#ffffff"
        self.bg_tooltip= "#1f2937" if d else "#f9fafb"

        # Text
        self.fg         = "#f9fafb" if d else "#111827"
        self.fg_dim     = "#9ca3af" if d else "#6b7280"
        self.fg_muted   = "#6b7280" if d else "#9ca3af"
        self.fg_primary = "#818cf8" if d else "#3b5bdb"

        # Borders
        self.border      = "#374151" if d else "#e5e7eb"
        self.border_strong = "#4b5563" if d else "#d1d5db"
        self.border_focus = "#818cf8" if d else "#3b5bdb"

        # Accent
        self.accent      = "#818cf8" if d else "#3b5bdb"
        self.accent_hover= "#6366f1" if d else "#3251c4"
        self.accent_bg   = "#1e3a5f" if d else "#dbeafe"
        self.accent_text = "#93c5fd" if d else "#1d4ed8"

        # Status
        self.status_idle       = ("#9ca3af", "#1f2937" if d else "#f3f4f6")
        self.status_idle_fg    = ("#d1d5db", "#374151" if d else "#374151")
        self.status_selected   = ("#60a5fa", "#1e3a5f")
        self.status_sel_fg     = ("#bfdbfe", "#93c5fd")
        self.status_connected  = ("#34d399", "#064e3b")
        self.status_conn_fg    = ("#6ee7b7", "#34d399")
        self.status_disconn    = ("#f87171", "#7f1d1d")
        self.status_disconn_fg = ("#fecaca", "#f87171")
        self.status_flashing   = ("#fbbf24", "#78350f")
        self.status_flash_fg   = ("#fde68a", "#fbbf24")
        self.status_complete   = ("#34d399", "#064e3b")
        self.status_comp_fg    = ("#6ee7b7", "#34d399")
        self.status_failed     = ("#f87171", "#7f1d1d")
        self.status_fail_fg    = ("#fecaca", "#f87171")
        self.status_retry      = ("#fbbf24", "#78350f")
        self.status_retry_fg   = ("#fde68a", "#fbbf24")

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
        self.progress_track = "#374151" if d else "#e5e7eb"
        self.progress_fill  = "#60a5fa" if d else "#2563eb"
        self.progress_ok    = "#34d399" if d else "#10b981"
        self.progress_err   = "#f87171" if d else "#ef4444"

        # Misc
        self.log_bg = "#0a0e1a"
        self.log_fg = "#a3e635"
        self.nav_active = "#2563eb"
        self.disabled = "#4b5563" if d else "#d1d5db"
        self.disabled_fg = "#6b7280" if d else "#9ca3af"


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
    if dark:
        p.setColor(QPalette.Window, QColor("#111827"))
        p.setColor(QPalette.WindowText, QColor("#f9fafb"))
        p.setColor(QPalette.Base, QColor("#1f2937"))
        p.setColor(QPalette.AlternateBase, QColor("#111827"))
        p.setColor(QPalette.ToolTipBase, QColor("#1f2937"))
        p.setColor(QPalette.ToolTipText, QColor("#f9fafb"))
        p.setColor(QPalette.Text, QColor("#f9fafb"))
        p.setColor(QPalette.Button, QColor("#1f2937"))
        p.setColor(QPalette.ButtonText, QColor("#f9fafb"))
        p.setColor(QPalette.BrightText, QColor("#fca5a5"))
        p.setColor(QPalette.Link, QColor("#818cf8"))
        p.setColor(QPalette.Highlight, QColor("#3b82f6"))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor("#6b7280"))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6b7280"))
    else:
        p.setColor(QPalette.Window, QColor("#f9fafb"))
        p.setColor(QPalette.WindowText, QColor("#111827"))
        p.setColor(QPalette.Base, QColor("#ffffff"))
        p.setColor(QPalette.AlternateBase, QColor("#f3f4f6"))
        p.setColor(QPalette.ToolTipBase, QColor("#f9fafb"))
        p.setColor(QPalette.ToolTipText, QColor("#111827"))
        p.setColor(QPalette.Text, QColor("#111827"))
        p.setColor(QPalette.Button, QColor("#f3f4f6"))
        p.setColor(QPalette.ButtonText, QColor("#111827"))
        p.setColor(QPalette.BrightText, QColor("#dc2626"))
        p.setColor(QPalette.Link, QColor("#3b5bdb"))
        p.setColor(QPalette.Highlight, QColor("#3b5bdb"))
        p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        p.setColor(QPalette.Disabled, QPalette.Text, QColor("#9ca3af"))
        p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#9ca3af"))
    return p


# ---------------------------------------------------------------------------
# QSS generation
# ---------------------------------------------------------------------------

def _build_qss() -> str:
    t = _state.tokens
    return f"""
/* ── Global ────────────────────────────────────────────── */
* {{
    font-family: "Segoe UI", "Microsoft YaHei", "Helvetica Neue", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background-color: {t.bg};
    color: {t.fg};
}}

/* ── Navigation sidebar (always dark) ─────────────────── */
#navPanel {{
    background-color: {t.bg_nav};
    border-right: 1px solid #1a2538;
    border-radius: 12px 0 0 12px;
}}
#navPanel QLabel {{
    color: #e2e8f0;
}}
#navPanel QPushButton {{
    background: transparent;
    color: #94a3b8;
    text-align: left;
    padding: 10px 14px;
    border-radius: 8px;
    border: none;
    font-size: 13px;
}}
#navPanel QPushButton:hover {{
    background-color: #1e293b;
    color: #f1f5f9;
}}
#navPanel QPushButton:checked {{
    background-color: {t.nav_active};
    color: white;
    font-weight: 600;
}}
#navPanel .nav-bottom {{
    color: #64748b;
    font-size: 12px;
}}

/* ── Page titles ──────────────────────────────────────── */
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
QLabel[cssClass="subtitle"] {{
    font-size: 13px;
    color: {t.fg_dim};
}}

/* ── Cards ────────────────────────────────────────────── */
QFrame[cssClass="card"] {{
    background-color: {t.bg_card};
    border: 1px solid {t.border};
    border-radius: 12px;
    padding: 4px;
}}

/* ── Buttons ──────────────────────────────────────────── */
QPushButton[cssClass="primary"] {{
    background-color: {t.accent};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 28px;
    font-size: 14px;
    font-weight: 700;
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
    padding: 10px 28px;
    font-size: 14px;
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
    color: white;
    border: none;
    border-radius: 8px;
    padding: 10px 28px;
    font-size: 14px;
    font-weight: 600;
}}
QPushButton[cssClass="danger"]:hover {{
    background-color: #b91c1c;
}}
QPushButton[cssClass="ghost"] {{
    background: transparent;
    color: {t.fg_dim};
    border: none;
    padding: 8px 12px;
    border-radius: 8px;
    font-size: 12px;
}}
QPushButton[cssClass="ghost"]:hover {{
    background-color: {t.bg_hover};
    color: {t.fg};
}}
QPushButton[cssClass="accent-pill"] {{
    background-color: {t.accent};
    color: white;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton[cssClass="accent-pill"]:hover {{
    background-color: {t.accent_hover};
}}

/* ── Combo boxes ──────────────────────────────────────── */
QComboBox {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 7px 14px;
    font-size: 13px;
    min-height: 18px;
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
    width: 24px;
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
    padding: 6px 10px;
    min-height: 22px;
    border-radius: 4px;
}}
QComboBox QAbstractItemView::item:hover {{
    background-color: {t.bg_hover};
}}

/* ── Tabs ─────────────────────────────────────────────── */
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
    padding: 8px 18px;
    font-size: 13px;
    font-weight: 500;
    border-bottom: 2px solid transparent;
    margin-right: 2px;
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
    padding: 8px 10px;
    border-radius: 6px;
    margin: 1px 2px;
}}
QListWidget::item:selected {{
    background-color: {t.accent_bg};
    color: {t.accent_text};
    font-weight: 600;
}}
QListWidget::item:hover:!selected {{
    background-color: {t.bg_hover};
}}

/* ── Progress bar ─────────────────────────────────────── */
QProgressBar {{
    border: none;
    border-radius: 6px;
    background-color: {t.progress_track};
    text-align: center;
    min-height: 12px;
    max-height: 12px;
    font-size: 0px;
}}
QProgressBar::chunk {{
    background-color: {t.progress_fill};
    border-radius: 6px;
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

/* ── Line edit ────────────────────────────────────────── */
QLineEdit {{
    background-color: {t.bg_input};
    color: {t.fg};
    border: 1px solid {t.border_strong};
    border-radius: 8px;
    padding: 7px 12px;
    font-size: 13px;
}}
QLineEdit:focus {{
    border-color: {t.border_focus};
}}
QLineEdit::placeholder {{
    color: {t.fg_muted};
}}

/* ── Scrollbars ───────────────────────────────────────── */
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

    If *force_dark* is ``None`` the system palette is inspected.
    Call ``apply_theme(app, force_dark=True)`` to lock dark mode.
    """
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
