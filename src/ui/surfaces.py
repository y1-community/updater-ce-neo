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
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLineEdit,
    QTextEdit,
    QWidget,
)


def glass_surfaces_enabled() -> bool:
    """True when the host window has a real glass backdrop (macOS Liquid Glass)."""
    try:
        from .glass import is_glass_supported
        return bool(is_glass_supported())
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


def _clear_document_background(view: QTextEdit) -> None:
    """Let the widget's own surface show through a text view's document."""
    frame = view.document().rootFrame()
    fmt = frame.frameFormat()
    fmt.setBackground(QBrush(Qt.BrushStyle.NoBrush))
    frame.setFrameFormat(fmt)


def paint_window_surface(widget: QWidget) -> None:
    """Make a widget *be* the window's surface, with no plate of its own.

    Windows and Linux have no glass backdrop: the window is painted in the
    theme's Window role, so a child that fills itself with Base — a shade
    apart from Window — reads as a rectangle sitting behind the notes or the
    release list. Copying Window onto Base as well removes the box and still
    erases the widget on every repaint, so nothing is left half-painted.
    """
    if widget is None:
        return
    app = QApplication.instance()
    surface = QColor()
    if app is not None:
        surface = app.palette().color(QPalette.ColorRole.Window)
    if not surface.isValid() or surface.alpha() == 0:
        try:
            from .dark import T

            surface = QColor(T().bg)
        except Exception:
            surface = QColor("#f8fafc")
    surface = QColor(surface)
    surface.setAlpha(255)
    text = QColor()
    if app is not None:
        text = app.palette().color(QPalette.ColorRole.WindowText)
    if not text.isValid():
        text = QColor("#1a1a1a")
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    widget.setAutoFillBackground(True)
    pal = widget.palette()
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        pal.setColor(group, QPalette.ColorRole.Window, surface)
        # Base is the plate colour on every desktop that has no glass: keeping
        # it here is the visible box this function exists to remove.
        pal.setColor(group, QPalette.ColorRole.Base, surface)
        pal.setColor(group, QPalette.ColorRole.WindowText, text)
    widget.setPalette(pal)


def harmonise_window_plates(root: QWidget) -> None:
    """On solid desktops, give every self-filling child the window surface.

    Pages and bars that never received the app palette keep Qt's native
    default Window role, which paints as a grey plate on the themed window.
    Input widgets keep their own Base role; only the fill colour is moved.
    """
    if root is None or glass_surfaces_enabled():
        return
    # The window paints the application palette's Window role; match that,
    # not the card token, or the page plates would be skipped as already equal.
    app = QApplication.instance()
    surface = QColor(app.palette().color(QPalette.ColorRole.Window)) if app is not None else QColor()
    if not surface.isValid():
        from .dark import T

        surface = QColor(T().bg)
    surface.setAlpha(255)
    # Scroll views take the window surface on their Base too, so their
    # contents blend in; real text entry widgets keep their own Base.
    input_like = (QLineEdit, QComboBox, QAbstractSpinBox, QTextEdit)
    for widget in [root, *root.findChildren(QWidget)]:
        scroll = isinstance(widget, QAbstractScrollArea)
        if not widget.autoFillBackground() and not scroll:
            continue
        pal = widget.palette()
        if pal.color(QPalette.ColorRole.Window) == surface and (
            not scroll or pal.color(QPalette.ColorRole.Base) == surface
        ):
            continue
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pal.setColor(group, QPalette.ColorRole.Window, surface)
            if scroll or not isinstance(widget, input_like):
                pal.setColor(group, QPalette.ColorRole.Base, surface)
        widget.setPalette(pal)


def clear_glass_plate(widget: QWidget) -> None:
    """Remove a child plate while keeping scroll views on their card surface.

    Plain labels inherit the card/window surface and should not paint their
    own rectangle. A scroll area's viewport is the exception: native item/text
    painting uses its Base role to erase old content, so match that role to the
    surrounding bento card rather than Window (which creates a visible box).
    """
    if widget is None:
        return
    if glass_surfaces_enabled():
        show_glass_backdrop(widget)
    else:
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
        widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        widget.setAutoFillBackground(False)
        if isinstance(widget, QAbstractScrollArea):
            # A local stylesheet changes QStyleSheetStyle for the whole view,
            # replacing the desktop's native scrollbar. The palette and widget
            # attributes below provide the same transparent surface instead.
            widget.setStyleSheet("")
        else:
            widget.setStyleSheet("background: transparent; border: none;")
    if isinstance(widget, QAbstractScrollArea):
        view = widget.viewport()
        if view is not None:
            if glass_surfaces_enabled():
                show_glass_backdrop(view)
            else:
                from .dark import T

                surface = QColor(T().bg_card)
                surface.setAlpha(255)
                palette = view.palette()
                for group in (
                    QPalette.ColorGroup.Active,
                    QPalette.ColorGroup.Inactive,
                    QPalette.ColorGroup.Disabled,
                ):
                    palette.setColor(group, QPalette.ColorRole.Base, surface)
                    palette.setColor(group, QPalette.ColorRole.Window, surface)
                view.setPalette(palette)
                view.setBackgroundRole(QPalette.ColorRole.Base)
                view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
                view.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
                view.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
                view.setAutoFillBackground(True)
                view.setStyleSheet("")
    if isinstance(widget, QTextEdit):
        _clear_document_background(widget)


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
