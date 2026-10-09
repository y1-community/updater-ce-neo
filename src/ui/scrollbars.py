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


def make_transparent(area: QAbstractScrollArea) -> None:
    """Blend a scroll area with its parent without a stylesheet.

    The palette/attribute equivalent of ``setStyleSheet("background:
    transparent;")``, which would cost the platform's scrollbars.
    """
    area.setFrameShape(QFrame.NoFrame)
    area.setFrameShadow(QFrame.Plain)
    area.setBackgroundRole(QPalette.ColorRole.NoRole)
    area.setAutoFillBackground(False)
    # The window is the only translucent surface. A translucent child skips
    # its erase and the next paint leaves the previous text behind.
    area.setAttribute(Qt.WA_TranslucentBackground, False)
    area.setAttribute(Qt.WA_NoSystemBackground, True)
    viewport = area.viewport()
    if viewport is not None:
        viewport.setAutoFillBackground(False)
        viewport.setBackgroundRole(QPalette.ColorRole.NoRole)
        viewport.setAttribute(Qt.WA_TranslucentBackground, False)
        viewport.setAttribute(Qt.WA_NoSystemBackground, True)
        # A solid Base/Window is the gray plate. Zero alpha lets the glass
        # or the solid parent show through; the keyword "transparent" does not.
        clear = viewport.palette()
        none = QColor(0, 0, 0, 0)
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            clear.setColor(group, QPalette.ColorRole.Base, none)
            clear.setColor(group, QPalette.ColorRole.Window, none)
        viewport.setPalette(clear)
        area_palette = area.palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            area_palette.setColor(group, QPalette.ColorRole.Base, none)
            area_palette.setColor(group, QPalette.ColorRole.Window, none)
        area.setPalette(area_palette)
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
