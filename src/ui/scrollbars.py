"""Scroll areas that keep the platform's own scrollbars.

macOS and Linux keep the policy the caller asked for, drawn by the platform
style. Windows hides scrollbars outright (``ScrollBarAlwaysOff``): a matching
application stylesheet forces classic arrow bars, and those are not repainted
here. Nothing in this module adds a ``QScrollBar`` stylesheet rule.

The one thing that breaks it: giving a scroll area its own stylesheet. A widget
with a local stylesheet is drawn by ``QStyleSheetStyle`` instead of the platform
style, so the host's floating bar silently becomes a classic always-visible one
that also reserves layout space. That is why transparency is applied through the
palette and widget attributes here — the same result, drawn by the OS.

So: use these helpers instead of ``setStyleSheet`` on a scroll area (or on a
scrollbar), and the platform's scrollbars are simply what you get.
"""

from __future__ import annotations

import sys

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
    """Leave the scroll area on its native style and erase to the parent card.

    No stylesheet is used, so host scrollbars stay native. On solid desktops
    the viewport Base is made equal to the surrounding card fill; on glass it
    is left transparent for the system material.
    """
    area.setFrameShape(QFrame.NoFrame)
    area.setFrameShadow(QFrame.Plain)
    area.setBackgroundRole(QPalette.ColorRole.Base)
    area.setAutoFillBackground(False)
    area.setAttribute(Qt.WA_TranslucentBackground, False)
    area.setAttribute(Qt.WA_NoSystemBackground, False)
    viewport = area.viewport()
    if viewport is not None:
        viewport.setBackgroundRole(QPalette.ColorRole.Base)
        viewport.setAttribute(Qt.WA_TranslucentBackground, False)
        viewport.setAttribute(Qt.WA_NoSystemBackground, False)
    try:
        from .surfaces import glass_surfaces_enabled, show_glass_backdrop

        if glass_surfaces_enabled():
            show_glass_backdrop(area)
            if viewport is not None:
                show_glass_backdrop(viewport)
            area.setAutoFillBackground(False)
            if viewport is not None:
                viewport.setAutoFillBackground(False)
        else:
            from .dark import T

            # Window surface, not a card plate: scroll content blends with the window.
            card_surface = QColor(T().bg)
            card_surface.setAlpha(255)
            for widget in (area, viewport):
                if widget is None:
                    continue
                palette = widget.palette()
                for group in (
                    QPalette.ColorGroup.Active,
                    QPalette.ColorGroup.Inactive,
                    QPalette.ColorGroup.Disabled,
                ):
                    palette.setColor(group, QPalette.ColorRole.Base, card_surface)
                    palette.setColor(group, QPalette.ColorRole.Window, card_surface)
                widget.setPalette(palette)
            if viewport is not None:
                viewport.setAutoFillBackground(True)
            area.setAutoFillBackground(False)
    except Exception:
        pass
    inner = area.widget() if hasattr(area, "widget") else None
    if isinstance(inner, QWidget):
        inner.setAutoFillBackground(False)
        inner.setAttribute(Qt.WA_TranslucentBackground, False)


def _windows_hides_scrollbars() -> bool:
    return sys.platform == "win32"


def _scroll_policy(requested: Qt.ScrollBarPolicy) -> Qt.ScrollBarPolicy:
    """Windows scroll areas show no bar. Other desktops keep the caller's policy."""
    if _windows_hides_scrollbars():
        return Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    return requested


def configure_scroll_area(
    area: QAbstractScrollArea,
    *,
    horizontal: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    vertical: Qt.ScrollBarPolicy = Qt.ScrollBarPolicy.ScrollBarAsNeeded,
    transparent: bool = False,
) -> QAbstractScrollArea:
    """Let the platform widget decide: as-needed bars, no stylesheet of ours."""
    area.setHorizontalScrollBarPolicy(_scroll_policy(horizontal))
    area.setVerticalScrollBarPolicy(_scroll_policy(vertical))
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
    if _windows_hides_scrollbars():
        widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return
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
