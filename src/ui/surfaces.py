"""Glass-friendly surfaces that still erase text.

Liquid Glass and WinUI acrylic sit behind the Qt window. The top-level window
is the only widget with ``WA_TranslucentBackground``. A child that changes
its text replaces only its own rect with the backdrop before drawing the new
glyphs. That is ``CompositionMode_Source`` of a transparent color, which lets
the system material show through. It is not ``CompositionMode_Clear``, and it
is not an opaque theme fill.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPalette
from PySide6.QtWidgets import QTextEdit, QWidget


def glass_surfaces_enabled() -> bool:
    """True when the host window has a real glass or acrylic backdrop."""
    try:
        from .glass import is_glass_supported, is_windows_acrylic_supported
        return bool(is_glass_supported() or is_windows_acrylic_supported())
    except Exception:
        return False


class _BackdropErase(QObject):
    """Replace a widget's dirty rect with the backdrop before it draws."""

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        if event.type() != QEvent.Type.Paint or not isinstance(obj, QWidget):
            return False
        if not glass_surfaces_enabled():
            return False
        dirty = event.rect() if hasattr(event, "rect") else obj.rect()
        painter = QPainter(obj)
        if hasattr(event, "region"):
            painter.setClipRegion(event.region())
        else:
            painter.setClipRect(dirty)
        # Source replaces the previous glyphs in this rect. A transparent
        # source is the glass / acrylic already behind the window, not a
        # theme-colored plate.
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(dirty, QColor(0, 0, 0, 0))
        painter.end()
        return False


def _install_erase(widget: QWidget) -> None:
    if widget is None or getattr(widget, "_backdrop_erase", None) is not None:
        return
    filt = _BackdropErase(widget)
    widget._backdrop_erase = filt
    widget.installEventFilter(filt)


def show_glass_backdrop(widget: QWidget) -> None:
    """Leave this surface unfilled so the system material shows through."""
    if widget is None:
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
    widget.setAutoFillBackground(False)
    if glass_surfaces_enabled():
        _install_erase(widget)


def clear_glass_plate(widget: QWidget) -> None:
    """Drop a solid Base/Window fill so this region shows the system material.

    On Linux the palette is left alone: those desktops have no glass backdrop,
    and the solid window color is the background.
    """
    if widget is None or not glass_surfaces_enabled():
        return
    show_glass_backdrop(widget)
    none = QColor(0, 0, 0, 0)
    palette = widget.palette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        palette.setColor(group, QPalette.ColorRole.Base, none)
        palette.setColor(group, QPalette.ColorRole.Window, none)
    widget.setPalette(palette)
    widget.setAutoFillBackground(False)
    if isinstance(widget, QTextEdit):
        frame = widget.document().rootFrame()
        fmt = frame.frameFormat()
        fmt.setBackground(QBrush(Qt.BrushStyle.NoBrush))
        frame.setFrameFormat(fmt)
        view = widget.viewport()
        if view is not None:
            clear_glass_plate(view)


def seal_updating_text(widget: QWidget, **_unused) -> None:
    """Erase a changing label from the backdrop, then let it draw the new text.

    The width hint keeps a shorter string from shrinking the label below the
    widest one already painted in it. It is capped at the width the layout has
    already granted, because the erase only ever covers the widget's own rect:
    a larger minimum could not have contained an earlier glyph either, and it
    would stop the window from shrinking on every desktop. No opaque fill is
    painted.
    """
    if widget is None:
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAutoFillBackground(False)
    if not glass_surfaces_enabled():
        return
    _install_erase(widget)
    text = widget.text() if hasattr(widget, "text") else ""
    if not text or not hasattr(widget, "fontMetrics"):
        return
    # Nothing has been painted yet when the widget has no width, so there is
    # nothing to seal and no reason to constrain the layout.
    current = widget.width()
    if current <= 0:
        return
    # Rich text still carries the visible words; width is only a hint.
    width = min(widget.fontMetrics().horizontalAdvance(str(text)) + 12, current)
    if width > widget.minimumWidth():
        widget.setMinimumWidth(width)
