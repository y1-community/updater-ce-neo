"""Minimize / maximize / close controls for the app's own title bar.

The window keeps its caption height out of the non-client area
(``client_rect_for_unified_caption``), so the whole window is client area. On
the Windows builds this app ships to, DWM reports the caption-button rectangle
(``DWMWA_CAPTION_BUTTON_BOUNDS``) but paints no system glyphs over a full-client
custom title bar — the header was reserving room for buttons that never appear.
The header therefore draws the three controls itself, with the 46x32 metrics
Windows 11 uses, so the caption still looks and behaves like the desktop's.

macOS keeps its native traffic lights (Cocoa draws them over the content view)
and Linux keeps whatever its window manager draws, so this widget is only built
on Windows.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

# Windows 11 caption metrics: three 46x32 buttons, flush with the window edge.
CONTROL_WIDTH = 46
CONTROL_HEIGHT = 32
GLYPH_SIZE = 10
# The close button's hot state is the system red, with a white glyph.
CLOSE_HOT = QColor("#c42b1c")

ROLE_MINIMIZE = "minimize"
ROLE_MAXIMIZE = "maximize"
ROLE_CLOSE = "close"


def _dark_surface(widget) -> bool:
    """True when this widget sits on a dark surface.

    Uses the live palette so the hover wash follows a theme switch without the
    widget having to own any colour. Falls back to the palette lightness when
    the theme module cannot answer.
    """
    try:
        from .dark import is_dark

        return bool(is_dark())
    except Exception:
        window = getattr(widget, "palette", lambda: None)()
        if window is None:
            return False
        return window.color(QPalette.ColorRole.Window).lightness() < 140


class CaptionControlButton(QPushButton):
    """One caption control. Flat, and painted here rather than by the style."""

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self._maximized = False
        self.setObjectName(f"captionControl{role.capitalize()}")
        self.setProperty("cssClass", f"caption{role.capitalize()}")
        self.setFixedSize(CONTROL_WIDTH, CONTROL_HEIGHT)
        self.setFlat(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setToolTip("")

    # -- state the header mirrors ------------------------------------------
    @property
    def maximized(self) -> bool:
        """True while the middle button shows the restore glyph."""
        return self._maximized

    def set_maximized(self, maximized: bool) -> None:
        maximized = bool(maximized)
        if maximized == self._maximized:
            return
        self._maximized = maximized
        self.update()

    def refresh_theme(self) -> None:
        """Live theme switch: the wash and the glyph are palette colours."""
        self.update()

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        try:
            hot = self.isEnabled() and (self.underMouse() or self.isDown())
            if hot:
                if self.role == ROLE_CLOSE:
                    fill = QColor(CLOSE_HOT)
                    if self.isDown():
                        fill = fill.darker(112)
                else:
                    dark = _dark_surface(self)
                    alpha = 34 if self.isDown() else (26 if dark else 20)
                    fill = QColor(255, 255, 255, alpha) if dark else QColor(0, 0, 0, alpha)
                painter.fillRect(self.rect(), fill)
            colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
            if self.role == ROLE_CLOSE and hot:
                colour = QColor("#ffffff")
            if not self.isEnabled():
                colour.setAlpha(110)
            self._paint_glyph(painter, colour)
        finally:
            painter.end()

    def _paint_glyph(self, painter: QPainter, colour: QColor) -> None:
        """Draw a 10x10 caption glyph with 1px strokes, centred in the button.

        Antialiasing stays off so every stroke lands on a whole pixel, which is
        what makes a hand-drawn caption glyph match the platform's own.
        """
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(colour)
        pen.setWidth(1)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = self.rect()
        size = GLYPH_SIZE
        x0 = rect.x() + (rect.width() - size) // 2
        y0 = rect.y() + (rect.height() - size) // 2
        x1 = x0 + size - 1
        y1 = y0 + size - 1

        if self.role == ROLE_MINIMIZE:
            mid = y0 + size // 2
            painter.drawLine(x0, mid, x1, mid)
        elif self.role == ROLE_MAXIMIZE:
            if self._maximized:
                # Restore: the front window offset down-left by a corner.
                inset = 3
                painter.drawRect(x0 + inset, y0, size - 1 - inset, size - 1 - inset)
                painter.drawRect(x0, y0 + inset, size - 1 - inset, size - 1 - inset)
            else:
                painter.drawRect(x0, y0, size - 1, size - 1)
        else:
            painter.drawLine(x0, y0, x1, y1)
            painter.drawLine(x1, y0, x0, y1)


class WindowControls(QWidget):
    """The three caption controls, laid out for the header's trailing edge."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.setObjectName("windowControls")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)
        self.setFixedSize(CONTROL_WIDTH * 3, CONTROL_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.min_button = CaptionControlButton(ROLE_MINIMIZE, self)
        self.max_button = CaptionControlButton(ROLE_MAXIMIZE, self)
        self.close_button = CaptionControlButton(ROLE_CLOSE, self)
        for button in (self.min_button, self.max_button, self.close_button):
            layout.addWidget(button)

        self.min_button.clicked.connect(self._minimize)
        self.max_button.clicked.connect(self._toggle_maximize)
        self.close_button.clicked.connect(self._close)
        self.set_state(self._is_maximized())

    # -- public interface --------------------------------------------------
    def set_state(self, maximized: bool) -> None:
        """Show the restore glyph while the window is maximized."""
        self.max_button.set_maximized(maximized)

    def set_close_enabled(self, enabled: bool) -> None:
        """Mirror the greyed-out close button an install puts on the caption."""
        self.close_button.setEnabled(bool(enabled))

    def refresh_theme(self) -> None:
        for button in (self.min_button, self.max_button, self.close_button):
            button.update()

    # -- actions -----------------------------------------------------------
    def _window(self):
        return self.window_ref if self.window_ref is not None else self.window()

    def _is_maximized(self) -> bool:
        window = self._window()
        try:
            return bool(window.isMaximized()) if window is not None else False
        except Exception:
            return False

    def _minimize(self) -> None:
        window = self._window()
        if window is not None:
            window.showMinimized()

    def _toggle_maximize(self) -> None:
        window = self._window()
        if window is None:
            return
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()
        self.set_state(bool(window.isMaximized()))

    def _close(self) -> None:
        window = self._window()
        if window is not None:
            # close(), not a hard exit: MainWindow's closeEvent asks inside the
            # progress card while an install is running.
            window.close()
