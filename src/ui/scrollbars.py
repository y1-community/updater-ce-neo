"""Scroll areas that keep the platform's own scrollbars.

Qt draws scrollbars with the active platform style (Cocoa, WinUI/Win32,
Adwaita/Breeze), and that style also decides how they look and when they show:
macOS fades overlay scrollers in while scrolling, Windows 11 shows its thin
bars on hover/scroll, a Linux desktop does whatever it is configured to do.
Nothing here re-implements that, and nothing here asks the system about it.

The one thing that breaks it: giving a scroll area its own stylesheet. A widget
with a local stylesheet is drawn by ``QStyleSheetStyle`` instead of the platform
style, so the host's floating bar silently becomes a classic always-visible one
that also reserves layout space. That is why transparency is applied through the
palette and widget attributes here — the same result, drawn by the OS.

So: use these helpers instead of ``setStyleSheet`` on a scroll area (or on a
scrollbar), and the platform's scrollbars are simply what you get.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractScrollArea, QApplication, QFrame, QWidget

__all__ = [
    "configure_scroll_area",
    "ensure_native_scrolling",
    "make_transparent",
]


def _theme_role(role: QPalette.ColorRole, fallback: str) -> QColor:
    """Opaque theme color. A zero-alpha black is painted as a solid black plate."""
    app = QApplication.instance()
    color = app.palette().color(role) if app is not None else QColor(fallback)
    if not color.isValid() or color.alpha() == 0:
        color = QColor(fallback)
    color = QColor(color)
    color.setAlpha(255)
    return color


def _paint_theme_surface(widget: QWidget) -> None:
    if widget is None:
        return
    base = _theme_role(QPalette.ColorRole.Base, "#ffffff")
    window = _theme_role(QPalette.ColorRole.Window, "#ffffff")
    text = _theme_role(QPalette.ColorRole.Text, "#1a1a1a")
    window_text = _theme_role(QPalette.ColorRole.WindowText, "#1a1a1a")
    palette = widget.palette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QPalette.ColorRole.Base, base)
        palette.setColor(group, QPalette.ColorRole.Window, window)
        palette.setColor(group, QPalette.ColorRole.Text, text)
        palette.setColor(group, QPalette.ColorRole.WindowText, window_text)
    widget.setPalette(palette)


def make_transparent(area: QAbstractScrollArea) -> None:
    """Blend a scroll area with the current theme without a stylesheet.

    The palette/attribute equivalent of ``setStyleSheet("background:
    transparent;")``, which would cost the platform's scrollbars. The fill is
    the live theme color, never ``QColor(0, 0, 0, 0)``: Windows paints that
    stored black as an opaque slab after a dark-to-light switch.
    """
    area.setFrameShape(QFrame.NoFrame)
    area.setFrameShadow(QFrame.Plain)
    area.setBackgroundRole(QPalette.ColorRole.Base)
    area.setAutoFillBackground(False)
    # The window is the only translucent surface. A translucent child skips
    # its erase and the next paint leaves the previous text behind.
    area.setAttribute(Qt.WA_TranslucentBackground, False)
    area.setAttribute(Qt.WA_NoSystemBackground, False)
    _paint_theme_surface(area)
    viewport = area.viewport()
    if viewport is not None:
        viewport.setAutoFillBackground(True)
        viewport.setBackgroundRole(QPalette.ColorRole.Base)
        viewport.setAttribute(Qt.WA_TranslucentBackground, False)
        viewport.setAttribute(Qt.WA_NoSystemBackground, False)
        _paint_theme_surface(viewport)
    inner = area.widget() if hasattr(area, "widget") else None
    if isinstance(inner, QWidget):
        inner.setAutoFillBackground(False)
        inner.setAttribute(Qt.WA_TranslucentBackground, False)
    try:
        from .surfaces import glass_surfaces_enabled, show_glass_backdrop
        if glass_surfaces_enabled():
            show_glass_backdrop(area)
            if viewport is not None:
                show_glass_backdrop(viewport)
    except Exception:
        pass


def configure_scroll_area(
    area: QAbstractScrollArea,
    *,
    horizontal: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    vertical: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    transparent: bool = False,
) -> QAbstractScrollArea:
    """Let the platform widget decide: as-needed bars, no stylesheet of ours."""
    area.setHorizontalScrollBarPolicy(horizontal)
    area.setVerticalScrollBarPolicy(vertical)
    if transparent:
        make_transparent(area)
    return area


def ensure_native_scrolling(widget) -> None:
    """Put scroll areas back on the platform's widgets, whatever they were.

    Only clears a local stylesheet (the one thing that replaces the platform's
    scrollbars) and restores as-needed policies; called for every scroll area on
    a theme refresh, so a stray ``setStyleSheet`` cannot quietly undo this.
    """
    if not isinstance(widget, QAbstractScrollArea):
        return
    if widget.styleSheet():
        widget.setStyleSheet("")
        make_transparent(widget)
    if widget.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn:
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    if widget.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOn:
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)


def apply_native_scrolling(root) -> None:
    """Walk a window (or the whole application) and let its scroll areas be."""
    if isinstance(root, QApplication):
        widgets = root.allWidgets()
    elif isinstance(root, QWidget):
        widgets = [root] + root.findChildren(QAbstractScrollArea)
    else:
        return
    for widget in widgets:
        ensure_native_scrolling(widget)
