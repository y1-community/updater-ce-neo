"""Dark-mode helpers — single source of truth for color choices.

Every widget calls ``dc(light, dark)`` so that a single palette check drives
the entire UI.  The dark palette is derived from the Innioasis / Lumen
design system tokens (gray-800/900 backgrounds, gray-50 text, accent blue
#3B5BDB, green #10B981, etc.).
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPalette


def is_dark() -> bool:
    """Return True when the system palette indicates dark mode."""
    try:
        c = QApplication.palette().color(QPalette.ColorRole.Window)
        return c.lightness() < 128
    except Exception:
        return False


def dc(light: str, dark: str) -> str:
    """Return *dark* when the system is in dark mode, else *light*."""
    return dark if is_dark() else light


# ── Named colour tokens (Lumen / Innioasis) ─────────────────────────
# Keep these DRY so callers can write ``dc(BG, BG_DARK)`` instead of
# hex literals scattered everywhere.

# Surfaces
BG         = "#ffffff"
BG_DARK    = "#1f2937"     # gray-800
BG_ELEV    = "#f9fafb"     # gray-50  (light panels)
BG_ELEV_D  = "#111827"     # gray-900 (dark panels)

# Borders
BORDER     = "#e5e7eb"     # gray-200
BORDER_D   = "#374151"     # gray-700
BORDER_S   = "#d1d5db"     # gray-300
BORDER_S_D = "#4b5563"     # gray-600

# Text
FG         = "#111827"     # gray-900 (light text)
FG_D       = "#f9fafb"     # gray-50  (dark text)
FG_MID     = "#374151"     # gray-700
FG_MID_D   = "#d1d5db"     # gray-300
FG_SEC     = "#6b7280"     # gray-500
FG_SEC_D   = "#9ca3af"     # gray-400
FG_DIM     = "#9ca3af"     # gray-400
FG_DIM_D   = "#6b7280"     # gray-500

# Brand
PRIMARY    = "#3B5BDB"
PRIMARY_HV = "#3451C7"
PRIMARY_D  = "#818CF8"
PRIMARY_DH = "#6366F1"
SUCCESS    = "#10b981"
DANGER     = "#dc2626"
WARNING_BG = "#fef3c7"
WARNING_FG = "#92400e"
WARNING_BG_D = "#451a03"
WARNING_FG_D = "#fde68a"
INFO_BG    = "#eff6ff"
INFO_FG    = "#1e40af"
INFO_BG_D  = "#1e3a5f"
INFO_FG_D  = "#93c5fd"
SUCCESS_BG = "#d1fae5"
SUCCESS_BG_D = "#065f46"
SUCCESS_FG = "#065f46"
SUCCESS_FG_D = "#a7f3d0"
DANGER_BG  = "#fee2e2"
DANGER_BG_D= "#7f1d1d"
DANGER_FG  = "#991b1b"
DANGER_FG_D= "#fca5a5"

# Progress
PROGRESS_TRACK   = "#e8ecf2"
PROGRESS_TRACK_D = "#374151"
PROGRESS_CHUNK   = "#2563eb"
PROGRESS_CHUNK_D = "#60a5fa"
PROGRESS_CHUNK_OK   = "#2b8a3e"
PROGRESS_CHUNK_OK_D = "#34d399"
PROGRESS_CHUNK_ERR  = "#e03131"
PROGRESS_CHUNK_ERR_D = "#f87171"
PROGRESS_TRACK_LIGHT = "#f3f4f6"

# Status tags
STATUS_COLORS = {
    "idle":        (dc("#9ca3af", "#6b7280"), dc("#f3f4f6", "#1f2937")),
    "selected":    (dc("#2563eb", "#60a5fa"), dc("#dbeafe", "#1e3a5f")),
    "connected":   (dc("#059669", "#34d399"), dc("#d1fae5", "#064e3b")),
    "disconnected":(dc("#dc2626", "#f87171"), dc("#fee2e2", "#7f1d1d")),
    "flashing":    (dc("#d97706", "#fbbf24"), dc("#fef3c7", "#78350f")),
    "complete":    (dc("#059669", "#34d399"), dc("#d1fae5", "#064e3b")),
    "failed":      (dc("#dc2626", "#f87171"), dc("#fee2e2", "#7f1d1d")),
    "retrying":    (dc("#d97706", "#fbbf24"), dc("#fef3c7", "#78350f")),
}
