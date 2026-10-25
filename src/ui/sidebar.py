"""Sidebar rows for each desktop.

macOS keeps the Aqua push button, which already lights up under the pointer.
Windows and Linux draw a navigation row instead of a beveled button: icon and
text on the left, a rounded hover wash, and a quiet selection. Windows adds a
short accent bar. The row is not filled with the accent color. Those shapes
match WinUI NavigationView and Adwaita/Breeze sidebars.
The row stays a real ``QPushButton`` with an empty stylesheet, so the platform
font, palette, and icon states still do the work. An idle row does not paint
its own plate: on acrylic the system material shows through, and on a solid
sidebar the row uses the pane color so it does not read as a second button.
Hover and selection paint the rounded wash. Text changes erase that rect
first, which is what keeps the previous label from staying on screen.
"""

from __future__ import annotations

import logging
import sys

import weakref

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QPushButton, QWidget


from .tab_focus import TabFocusManager


class _FocusDismissFilter:
    """Compatibility shim delegating to TabFocusManager."""

    @classmethod
    def ensure_installed(cls) -> None:
        TabFocusManager.ensure_installed()

    @classmethod
    def mark_keyboard_focused(cls, btn: SidebarButton) -> None:
        TabFocusManager.register_focused_widget(btn)

    @classmethod
    def unmark_keyboard_focused(cls, btn: SidebarButton) -> None:
        TabFocusManager.unregister_focused_widget(btn)

    @classmethod
    def dismiss_all(cls) -> None:
        TabFocusManager.dismiss_all()



logger = logging.getLogger(__name__)

#: Widest the rail grows to show a translated row whole. The rail is sized
#: from its own labels, because a language's copy is often half again as long
#: as English ("Unterstützen Sie uns" against "Support Us"). Past this the
#: rail stops growing and the row elides: an ellipsis in a pane that already
#: made room for itself is the sign that the string is too long, not the pane
#: being too narrow.
SIDEBAR_MAX_WIDTH = 320

#: The language picker is a sidebar row too, but its copy is the name of a
#: language rather than one of ours: "Português (Brasil)" has to fit, an exotic
#: endonym both script and romanisation may not. Its own ceiling keeps the rail
#: from being set by one such entry while still showing the selection whole.
SIDEBAR_PICKER_MAX_WIDTH = 240

#: Narrowest the rail degrades to on a display that cannot afford the page
#: column's minimum plus a full rail. Rows elide at this width, which is the
#: honest signal: the screen, not the copy, is what ran out.
SIDEBAR_MIN_WIDTH = 120


def sidebar_base_width(platform_name: str | None = None) -> int:
    """The rail's width when its labels fit: Aqua rows need a little more."""
    name = sys.platform if platform_name is None else platform_name
    return 185 if name == "darwin" else 175


class SidebarWidth:
    """How wide the navigation rail should be, for this language on this display.

    One owner for the whole question. It holds the numbers (base, floor and the
    two ceilings), measures the rows and the language picker, decides how much
    the page column's own minimum leaves it on this display, and reports a
    label that still does not fit — as a tooltip plus one log line, never as
    silent truncation.

    The window feeds it facts and nothing else: :meth:`apply` whenever labels,
    theme or display may have changed, then :attr:`width` or
    :meth:`window_floor` for the result.
    """

    def __init__(
        self,
        *,
        base: int | None = None,
        maximum: int = SIDEBAR_MAX_WIDTH,
        picker_max: int = SIDEBAR_PICKER_MAX_WIDTH,
        minimum: int = SIDEBAR_MIN_WIDTH,
    ) -> None:
        self.base = sidebar_base_width() if base is None else int(base)
        self.maximum = max(int(maximum), self.base)
        self.picker_max = max(int(picker_max), self.base)
        self.minimum = max(1, min(int(minimum), self.base))
        self._width = self.base
        self._warned: set[tuple[str, str]] = set()

    # ── results ──

    @property
    def width(self) -> int:
        """The width the last :meth:`apply` gave the rail."""
        return self._width

    def window_floor(self, content_min: int, display_width: int, minimum: int) -> int:
        """Least window width that keeps the rail and the page column whole."""
        floor = max(int(minimum), self._width + max(0, int(content_min)))
        if int(display_width) > 0:
            floor = min(floor, int(display_width))
        return max(1, int(floor))

    def affordable(self, display_width: int, content_min: int) -> int:
        """Most the rail may take while the page column keeps its minimum.

        On a display narrower than a full rail plus the page column the column
        wins: the rail gives the width back and its rows elide, with their
        labels in tooltips. ``minimum`` is where the rail stops being a rail.
        """
        if int(display_width) <= 0:
            return self.maximum
        return max(self.minimum, int(display_width) - max(0, int(content_min)))

    def needed(self, panel: QWidget, picker=None) -> int:
        """Width the rail needs for this language's labels, within its ceilings.

        Measured from the rows themselves, so the icon, the row padding, the
        themed font and a translated label are all counted — a fixed 185 px
        elided the Spanish, Italian, French and Hungarian rows. The language
        picker is measured too, but against ``picker_max``: it is sized for the
        selection on screen, not for the longest name in its list.
        """
        margins = self._margins(panel)
        widest = 0
        for child in panel.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            if picker is not None and child is picker:
                continue
            if not child.isVisibleTo(panel):
                continue
            widest = max(widest, self.row_width(child))
        needed = widest + margins
        if picker is not None and picker.isVisibleTo(panel):
            needed = max(needed, min(self.picker_width(picker) + margins, self.picker_max))
        return max(self.base, min(needed, self.maximum))

    # ── the one call ──

    def apply(
        self,
        panel: QWidget,
        picker=None,
        *,
        display_width: int = 0,
        content_min: int = 0,
        language: str = "",
        picker_tooltip: str = "",
    ) -> bool:
        """Size ``panel`` for this language and display; True when it changed."""
        if panel is None:
            return False
        width = max(self.minimum, min(self.needed(panel, picker), self.affordable(display_width, content_min)))
        self._flag_overlong(panel, picker, width - self._margins(panel), language, picker_tooltip)
        changed = width != panel.width()
        if changed:
            panel.setFixedWidth(width)
        self._width = width
        return changed

    # ── measurement ──

    @staticmethod
    def row_width(row) -> int:
        """Width one rail row needs to draw its whole label."""
        measure = getattr(row, "required_width", None)
        if callable(measure):
            return int(measure())
        return int(row.sizeHint().width())

    @staticmethod
    def picker_width(picker) -> int:
        """Width the picker needs for the selection on screen.

        ``QComboBox.sizeHint`` is sized for the widest item in the list, so one
        long language name would otherwise set the width of the rail and the
        selected one would still clip. This asks the style for the size of the
        text that is actually shown, icon, padding and arrow included.
        """
        if picker is None:
            return 0
        try:
            from PySide6.QtWidgets import QStyle, QStyleOptionComboBox

            index = picker.currentIndex()
            text = picker.currentText()
            icon = picker.itemIcon(index) if index >= 0 else QIcon()
            width = QFontMetrics(picker.font()).horizontalAdvance(text)
            if not icon.isNull():
                width += picker.iconSize().width() + 4
            option = QStyleOptionComboBox()
            option.initFrom(picker)
            option.currentText = text
            option.icon = icon
            option.editable = picker.isEditable()
            size = picker.style().sizeFromContents(
                QStyle.ContentsType.CT_ComboBox, option, QSize(width, picker.height()), picker
            )
            # Two pixels of slack so the text is not elided on a rounding edge.
            return int(size.width()) + 2
        except Exception:
            return int(picker.sizeHint().width())

    @staticmethod
    def _margins(panel: QWidget) -> int:
        """Pixels the rail's layout spends either side of its rows."""
        layout = panel.layout()
        if layout is None:
            return 20
        box = layout.contentsMargins()
        return int(box.left() + box.right())

    # ── reporting ──

    def _flag_overlong(
        self,
        panel: QWidget,
        picker,
        available: int,
        language: str,
        picker_tooltip: str,
    ) -> None:
        """Make a label that cannot fit the rail findable, never just clipped.

        ``SidebarButton`` elides past the row width, so an ellipsis in a rail
        that already sized itself is the visible sign a string is unwieldy. The
        full label rides in the tooltip so it stays readable, and the string is
        named once per language in the log so it can be shortened. The picker
        gets the same treatment: its selection is a label in the rail too.
        """

        def flag(text: str, needed: int, set_tooltip) -> None:
            too_wide = int(needed) > int(available)
            set_tooltip(text if too_wide else "")
            if not too_wide or (language, text) in self._warned:
                return
            self._warned.add((language, text))
            logger.warning(
                "Sidebar label needs %d px, the rail gives %d px: %r (%s)",
                int(needed),
                int(available),
                text,
                language,
            )

        for row in panel.findChildren(SidebarButton):
            # Only rows that are actually drawn: an update row waiting off
            # screen is not a label the reader can see elided.
            if not row.isVisibleTo(panel) or not row.text():
                continue
            flag(row.text(), self.row_width(row), row.setToolTip)
        if picker is not None and picker.isVisibleTo(panel):
            flag(
                picker.currentText(),
                self.picker_width(picker),
                lambda tip: picker.setToolTip(tip or picker_tooltip),
            )


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


def sidebar_nav_kind(platform_name: str | None = None) -> str:
    """Which settings-app sidebar to paint.

    Windows 11 Settings uses a quiet highlight and a short accent bar.
    macOS System Settings uses a translucent selection and no bar.
    Linux follows the desktop row (Breeze / Adwaita) and does not copy the
    Windows bar.
    """
    name = sys.platform if platform_name is None else platform_name
    if name == "darwin":
        return "macos"
    if name == "win32":
        return "windows"
    return "linux"


def sidebar_selected_color(base: QColor) -> QColor:
    """Selected row fill. Neutral, never a solid accent plate.

    A dark pane keeps a faint light veil so the row is not a light plate.
    A light pane uses a subtle neutral wash so the row matches native WinUI/macOS sidebars.
    """
    if not base.isValid():
        base = QColor("#2b303c")
    if base.lightness() < 140:
        return QColor(255, 255, 255, 32)
    return QColor(0, 0, 0, 22)


def sidebar_selected_fill(base: QColor) -> QColor:
    """Opaque color of the selected row after the veil is drawn on ``base``."""
    veil = sidebar_selected_color(base)
    if not base.isValid():
        base = QColor("#2b303c")
    if veil.alpha() >= 250:
        return QColor(veil.red(), veil.green(), veil.blue())
    alpha = veil.alpha() / 255.0
    return QColor(
        int(round(veil.red() * alpha + base.red() * (1.0 - alpha))),
        int(round(veil.green() * alpha + base.green() * (1.0 - alpha))),
        int(round(veil.blue() * alpha + base.blue() * (1.0 - alpha))),
    )


def sidebar_selected_text_color(base: QColor) -> QColor:
    """Matches the unselected item font color so text colors stay consistent."""
    try:
        from .dark import T
        return QColor(T().fg)
    except Exception:
        if not base.isValid():
            base = QColor("#2b303c")
        return QColor("#f8fafc") if base.lightness() < 140 else QColor("#111827")


def _tint_pixmap(pixmap: QPixmap, color: QColor) -> QPixmap:
    tinted = QPixmap(pixmap.size())
    tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(tinted.rect(), color)
    painter.end()
    return tinted


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

    Windows paints a Settings navigation row. macOS paints a System Settings
    selection. Linux paints the desktop's quiet row. ``_preview_hover`` and
    ``_force_platform_row`` exist so tests can render the Windows row on any host.
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._preview_hover = False
        self._force_platform_row = False
        self._keyboard_focused = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.setAutoDefault(False)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setStyleSheet("")
        TabFocusManager.ensure_installed()

    def dismiss_keyboard_focus(self) -> None:
        self._keyboard_focused = False
        self.update()

    def required_width(self) -> int:
        """Width this row needs to draw its whole label.

        ``_paint_label`` lays the text out inside its own insets (row inset,
        icon, the gaps either side of it, the label's own trailing gap) rather
        than in the style's content rect, so a platform size hint reports less
        room than the label actually needs. The rail is sized from this.
        """
        text = self.text()
        if not text:
            return int(super().sizeHint().width())
        icon_w = self.iconSize().width() if not self.icon().isNull() else 0
        # Row inset, the gaps around the glyph, and the label's own trailing
        # gap; two extra pixels so the elide and the paint agree on the edge.
        chrome = 4 + 8 + icon_w + (8 if icon_w else 0) + 6 + 4 + 2
        return int(QFontMetrics(self.font()).horizontalAdvance(text)) + chrome

    @classmethod
    def dismiss_focus_rings(cls) -> None:
        """Dismiss active keyboard focus outlines across all sidebar rows."""
        TabFocusManager.dismiss_all()

    def focusInEvent(self, event):
        reason = event.reason()
        if TabFocusManager.is_tab_active() and reason in (
            Qt.FocusReason.TabFocusReason,
            Qt.FocusReason.BacktabFocusReason,
            Qt.FocusReason.ShortcutFocusReason,
        ):
            self._keyboard_focused = True
            TabFocusManager.register_focused_widget(self)
        else:
            self._keyboard_focused = False
            TabFocusManager.unregister_focused_widget(self)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        self._keyboard_focused = False
        _FocusDismissFilter.unmark_keyboard_focused(self)
        self.update()
        super().focusOutEvent(event)

    def mousePressEvent(self, event):
        self._keyboard_focused = False
        _FocusDismissFilter.unmark_keyboard_focused(self)
        _FocusDismissFilter.dismiss_all()
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        self._keyboard_focused = False
        _FocusDismissFilter.unmark_keyboard_focused(self)
        self.update()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        if self._keyboard_focused:
            self._keyboard_focused = False
            _FocusDismissFilter.unmark_keyboard_focused(self)
        _FocusDismissFilter.dismiss_all()
        self.update()
        super().enterEvent(event)

    def mouseMoveEvent(self, event):
        if self._keyboard_focused:
            self._keyboard_focused = False
            _FocusDismissFilter.unmark_keyboard_focused(self)
            self.update()
        super().mouseMoveEvent(event)

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

    def _row_kind(self) -> str:
        if self._force_platform_row:
            return "windows"
        return sidebar_nav_kind()

    def paintEvent(self, event):
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
            kind = self._row_kind()
            row = rect.adjusted(4, 2, -4, -2)
            accent = self.palette().color(QPalette.ColorRole.Highlight)
            if selected or hovered:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                if selected:
                    painter.setBrush(sidebar_selected_color(sidebar_base_color()))
                else:
                    painter.setBrush(sidebar_hover_color(sidebar_base_color()))
                radius = 8 if kind == "macos" else sidebar_corner_radius(
                    "win32" if kind == "windows" else "linux"
                )
                painter.drawRoundedRect(row, radius, radius)
                if selected and kind == "windows":
                    bar_h = max(10, row.height() - 16)
                    bar = QRect(
                        row.left() + 2,
                        row.center().y() - bar_h // 2,
                        3,
                        bar_h,
                    )
                    painter.setBrush(accent)
                    painter.drawRoundedRect(bar, 1, 1)

            if self.hasFocus() and self._keyboard_focused and TabFocusManager.is_tab_active() and not selected:
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
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        if not self.text():
            # An icon-only row (the community marks) centres its glyph.
            icon_rect = QRect(row.left(), row.top(), row.width(), row.height())
        elif rtl:
            icon_rect = QRect(
                row.right() - 8 - size.width(), row.top(), size.width(), row.height()
            )
        else:
            icon_rect = QRect(row.left() + 8, row.top(), size.width(), row.height())
        if not self.isEnabled():
            mode = QIcon.Mode.Disabled
            color = self.palette().color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText)
        else:
            mode = QIcon.Mode.Normal
            from .dark import T
            color = QColor(T().fg)
        state = QIcon.State.On if selected else QIcon.State.Off
        pixmap = icon.pixmap(size, mode, state)
        if not pixmap.isNull():
            tinted = _tint_pixmap(pixmap, color)
            target = QRect(0, 0, size.width(), size.height())
            target.moveCenter(icon_rect.center())
            painter.drawPixmap(target, tinted)
            return
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
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        if rtl:
            left = row.left() + 6
            right = row.right() - (8 + icon_w + 8 if icon_w else 8)
        else:
            left = row.left() + (8 + icon_w + 8 if icon_w else 8)
            right = row.right() - 6
        text_rect = QRect(left, row.top(), max(0, right - left), row.height())
        pal = QPalette(self.palette())
        if self.isEnabled():
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
            int(
                (Qt.AlignmentFlag.AlignRight if rtl else Qt.AlignmentFlag.AlignLeft)
                | Qt.AlignmentFlag.AlignVCenter
            ),
            pal,
            self.isEnabled(),
            elided,
            QPalette.ColorRole.WindowText,
        )


def classic_windows_style_needs_hover(style_name: str) -> bool:
    """Classic and Vista Win32 styles do not paint a visible hot button.

    WinUI (``windows11``) does. Linux and macOS keep their own hover.
    """
    return (style_name or "").strip().lower() in {"windows", "windowsvista"}
