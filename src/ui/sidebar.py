"""Sidebar rows for each desktop.

macOS keeps the Aqua push button, which already lights up under the pointer.
Windows and Linux draw a navigation row instead of a beveled button: icon and
text on the left, a rounded hover wash, and the accent fill when the row is
selected. Those shapes match WinUI NavigationView and Adwaita/Breeze sidebars.
The row stays a real ``QPushButton`` with an empty stylesheet, so the platform
font, palette, and icon states still do the work. An idle row does not paint
its own plate: on acrylic the system material shows through, and on a solid
sidebar the row uses the pane color so it does not read as a second button.
Hover and selection paint the rounded wash. Text changes erase that rect
first, which is what keeps the previous label from staying on screen.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QRect, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette
from PySide6.QtWidgets import QPushButton


def sidebar_row_spacing(platform_name: str | None = None) -> int:
    """Vertical gap between sidebar rows.

    Aqua draws about 6px outside the layout cell, so macOS rows need the
    extra room. Windows and Linux paint inside the widget.
    """
    name = sys.platform if platform_name is None else platform_name
    if name == "darwin":
        return 16
    return 4


def sidebar_row_min_height(platform_name: str | None = None) -> int:
    """WinUI and Adwaita rows are a little taller than an Aqua bezel."""
    name = sys.platform if platform_name is None else platform_name
    if name == "darwin":
        return 28
    return 32


def sidebar_corner_radius(platform_name: str | None = None) -> int:
    """WinUI NavigationView uses 4px; Adwaita rows use 6px."""
    name = sys.platform if platform_name is None else platform_name
    if name == "win32":
        return 4
    return 6


def sidebar_hover_color(base: QColor) -> QColor:
    """Hover veil for a navigation row.

    WinUI and Adwaita lighten the row under the pointer. The wash is
    translucent so acrylic (and a solid pane) stay visible through it. It is
    not a second opaque button.
    """
    if not base.isValid():
        base = QColor("#2b303c")
    if base.lightness() < 140:
        return QColor(255, 255, 255, 40)
    return QColor(0, 0, 0, 28)


def sidebar_base_color() -> QColor:
    """Opaque sidebar surface. Matches the nav pane so idle rows blend in."""
    try:
        from .dark import T

        color = QColor(T().bg_nav)
        if color.isValid():
            return color
    except Exception:
        pass
    return QColor("#181b20")


class SidebarButton(QPushButton):
    """One sidebar entry.

    On macOS this is an Aqua button. On Windows and Linux ``paintEvent`` draws
    the navigation row. ``_preview_hover`` and ``_force_platform_row`` exist so
    tests can render the Windows/Linux row on any host.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._preview_hover = False
        self._force_platform_row = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAutoDefault(False)
        self.setFlat(sys.platform != "darwin")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet("")

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)

    def changeEvent(self, event):
        if event.type() in (
            QEvent.Type.EnabledChange,
            QEvent.Type.PaletteChange,
            QEvent.Type.FontChange,
        ):
            self.update()
        super().changeEvent(event)

    def paintEvent(self, event):
        if sys.platform == "darwin" and not self._force_platform_row:
            super().paintEvent(event)
            return
        self._paint_platform_row(event)

    def _hovered(self) -> bool:
        return self.isEnabled() and (self.underMouse() or self._preview_hover)

    def _erase_row(self, painter: QPainter, rect: QRect) -> None:
        """Drop the previous glyphs without leaving an idle plate.

        On glass, a transparent source replaces this rect so acrylic shows
        through an idle row. On a solid desktop the pane color is the
        background, so the row does not look like its own filled button.
        """
        from .surfaces import glass_surfaces_enabled

        if glass_surfaces_enabled():
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(rect, QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            return
        painter.fillRect(rect, sidebar_base_color())

    def _paint_platform_row(self, event):
        painter = QPainter(self)
        painter.setClipRect(event.rect())
        try:
            rect = self.rect()
            self._erase_row(painter, rect)

            selected = self.isEnabled() and self.isChecked()
            hovered = self._hovered() and not selected
            row = rect.adjusted(4, 2, -4, -2)
            if selected or hovered:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                if selected:
                    painter.setBrush(self.palette().color(QPalette.ColorRole.Highlight))
                else:
                    painter.setBrush(sidebar_hover_color(sidebar_base_color()))
                radius = sidebar_corner_radius()
                painter.drawRoundedRect(row, radius, radius)

            if self.hasFocus() and not selected:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                pen_color = self.palette().color(QPalette.ColorRole.Highlight)
                painter.setPen(pen_color)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(row.adjusted(1, 1, -1, -1), sidebar_corner_radius(), sidebar_corner_radius())

            self._paint_icon(painter, row, selected)
            self._paint_label(painter, row, selected)
        finally:
            painter.end()

    def _paint_icon(self, painter: QPainter, row: QRect, selected: bool) -> None:
        icon = self.icon()
        if icon.isNull():
            return
        size = self.iconSize()
        if not size.isValid() or size.isEmpty():
            size = QSize(16, 16)
        icon_rect = QRect(row.left() + 8, row.top(), size.width(), row.height())
        if not self.isEnabled():
            mode = QIcon.Mode.Disabled
        elif selected:
            mode = QIcon.Mode.Selected
        else:
            mode = QIcon.Mode.Normal
        state = QIcon.State.On if selected else QIcon.State.Off
        icon.paint(
            painter,
            icon_rect,
            Qt.AlignmentFlag.AlignCenter,
            mode,
            state,
        )

    def _paint_label(self, painter: QPainter, row: QRect, selected: bool) -> None:
        from .dark import T

        icon_w = self.iconSize().width() if not self.icon().isNull() else 0
        left = row.left() + (8 + icon_w + 8 if icon_w else 8)
        text_rect = QRect(left, row.top(), max(0, row.right() - left - 6), row.height())
        pal = QPalette(self.palette())
        if selected:
            color = QColor(T().nav_active_text)
        elif self.isEnabled():
            color = QColor(T().fg)
        else:
            color = pal.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Normal,
        ):
            pal.setColor(group, QPalette.ColorRole.WindowText, color)
        painter.setFont(self.font())
        elided = painter.fontMetrics().elidedText(
            self.text(),
            Qt.TextElideMode.ElideRight,
            text_rect.width(),
        )
        self.style().drawItemText(
            painter,
            text_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            pal,
            self.isEnabled(),
            elided,
            QPalette.ColorRole.WindowText,
        )


def classic_windows_style_needs_hover(style_name: str) -> bool:
    """The classic Windows style draws a pressed button and no hover.

    ``windows11`` and ``windowsvista`` already light up under the pointer, so
    they must not get a second wash on top.
    """
    return (style_name or "").lower() == "windows"
