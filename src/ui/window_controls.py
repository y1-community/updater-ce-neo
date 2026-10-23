"""Minimize / maximize / close controls for the app's own title bar.

The window keeps its caption height out of the non-client area
(``client_rect_for_unified_caption``), so the whole window is client area. On
the Windows builds this app ships to, DWM reports the caption-button rectangle
(``DWMWA_CAPTION_BUTTON_BOUNDS``) but paints no system glyphs over a full-client
custom title bar — the header was reserving room for buttons that never appear.
The header therefore draws the three controls itself, with the 46x32 metrics
Windows 11 uses, so the caption still looks and behaves like the desktop's.

macOS keeps its native traffic lights (Cocoa draws them over the content view).
On Linux the header keeps the app's own title and icon, but the *symbols* are
the desktop's: the freedesktop icon names every decoration theme ships
(``window-minimize``, ``window-maximize``, ``window-restore``, ``window-close``,
including the ``-symbolic`` variants) are read from the session's icon theme
and painted in its place, so the controls match KDE, GNOME, XFCE or whatever
theme is active — not a Windows imitation of them. The hand-painted glyphs are
only the last resort, for a session with no theme icons and no style glyphs.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QPushButton, QStyle, QWidget

# Windows 11 caption metrics: three 46x32 buttons, flush with the window edge.
CONTROL_WIDTH = 46
CONTROL_HEIGHT = 32
GLYPH_SIZE = 10
# The close button's hot state is the system red, with a white glyph.
CLOSE_HOT = QColor("#c42b1c")

# Size the host's own caption symbol is drawn at. Decoration themes draw a
# 16px icon in a wider button on every desktop that ships one.
HOST_SYMBOL_SIZE = 16

ROLE_MINIMIZE = "minimize"
ROLE_MAXIMIZE = "maximize"
ROLE_CLOSE = "close"

# Freedesktop Icon Naming Specification entries for a window's decoration
# buttons. KDE, GNOME, XFCE, Cinnamon and the icon themes they install all
# provide them, under the plain name for a colour theme and ``-symbolic`` for
# a monochrome one that follows the interface's own foreground colour.
CAPTION_ICON_NAMES = {
    ROLE_MINIMIZE: ("window-minimize", "window-minimize-symbolic"),
    ROLE_MAXIMIZE: ("window-maximize", "window-maximize-symbolic"),
    ROLE_CLOSE: ("window-close", "window-close-symbolic"),
}
RESTORE_ICON_NAMES = ("window-restore", "window-restore-symbolic")

# Searched when the session names no icon theme of its own (a bare Qt platform
# theme, a minimal session), so the desktop's symbols are still found. The
# session's own theme is always tried first.
FALLBACK_ICON_THEMES = (
    "breeze-dark",
    "breeze",
    "Adwaita",
    "Adwaita-dark",
    "Yaru",
    "Yaru-dark",
    "Papirus",
    "Papirus-Dark",
    "elementary",
    "hicolor",
)


def host_caption_symbols_apply() -> bool:
    """True where the header borrows the desktop's own decoration symbols."""
    return sys.platform.startswith("linux")


def host_caption_icon(role: str, *, maximized: bool = False) -> tuple[QIcon, bool]:
    """The symbol the host's own title bar shows for ``role``.

    Returns ``(icon, symbolic)``; an empty icon when the desktop offers none.
    A symbolic icon is one flat colour that the caller tints with the live
    palette, exactly as the toolkit would.
    """
    names = RESTORE_ICON_NAMES if (role == ROLE_MAXIMIZE and maximized) else CAPTION_ICON_NAMES.get(role, ())
    if not names:
        return QIcon(), False

    previous = QIcon.themeName()
    themes = [previous] if previous else []
    themes.extend(theme for theme in FALLBACK_ICON_THEMES if theme not in themes)
    try:
        for theme in themes:
            QIcon.setThemeName(theme)
            for name in names:
                icon = QIcon.fromTheme(name)
                if not icon.isNull():
                    return icon, name.endswith("-symbolic")
    finally:
        # The icon theme name is process-wide state.
        QIcon.setThemeName(previous)

    # Nothing in the icon themes: the active Qt style still draws the host
    # toolkit's caption glyphs, which beats an imitation of them.
    standard = {
        ROLE_MINIMIZE: QStyle.StandardPixmap.SP_TitleBarMinButton,
        ROLE_MAXIMIZE: (
            QStyle.StandardPixmap.SP_TitleBarNormalButton
            if maximized
            else QStyle.StandardPixmap.SP_TitleBarMaxButton
        ),
        ROLE_CLOSE: QStyle.StandardPixmap.SP_TitleBarCloseButton,
    }.get(role)
    app = QApplication.instance()
    if standard is not None and app is not None:
        try:
            icon = app.style().standardIcon(standard)
        except Exception:
            icon = QIcon()
        if not icon.isNull():
            return icon, False
    return QIcon(), False


def tinted_pixmap(pixmap: QPixmap, colour: QColor) -> QPixmap:
    """Recolour a symbolic icon to one flat colour, as the toolkit does."""
    tinted = QPixmap(pixmap.size())
    tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
    tinted.fill(Qt.GlobalColor.transparent)
    painter = QPainter(tinted)
    try:
        painter.drawPixmap(0, 0, pixmap)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        painter.fillRect(tinted.rect(), colour)
    finally:
        painter.end()
    return tinted


def caption_hot_fill(role: str, *, dark: bool, pressed: bool, host_symbols: bool) -> QColor:
    """The wash under a caption button on hover or press.

    The Windows red belongs to the Windows glyphs: a desktop's own symbols are
    highlighted the way its title bar highlights them, with the same neutral
    wash the other buttons use.
    """
    if role == ROLE_CLOSE and not host_symbols:
        fill = QColor(CLOSE_HOT)
        return fill.darker(112) if pressed else fill
    alpha = 34 if pressed else (26 if dark else 20)
    return QColor(255, 255, 255, alpha) if dark else QColor(0, 0, 0, alpha)


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

    # The maximise glyph is always wired so DWM/NSWindow caption hit-testing
    # stays aligned with the three-slot layout, even when the button is hidden
    # for host policy (e.g. maximising is suppressed).
    maximise_allowed = True

    def __init__(self, role: str, parent=None):
        super().__init__(parent)
        self.role = role
        self._maximized = False
        # The desktop's own symbol for this role, resolved from the icon theme
        # (and again after a theme switch). Empty when the session has none.
        self._host_icon = QIcon()
        self._host_icon_symbolic = False
        self._host_icon_resolved = False
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
        # Maximise and restore are different symbols in the desktop's theme.
        self._host_icon_resolved = False
        self.update()

    def refresh_theme(self) -> None:
        """Live theme switch: re-read the desktop's symbols and repaint.

        The palette carries the wash and the tint; the icon theme can have
        changed on its own (a user switching their decoration theme), so the
        symbol is resolved again.
        """
        self._host_icon_resolved = False
        self.update()

    def _ensure_host_icon(self) -> None:
        if self._host_icon_resolved:
            return
        self._host_icon_resolved = True
        if not host_caption_symbols_apply():
            self._host_icon, self._host_icon_symbolic = QIcon(), False
            return
        self._host_icon, self._host_icon_symbolic = host_caption_icon(
            self.role, maximized=self._maximized
        )

    def _paint_host_symbol(self, painter: QPainter, colour: QColor) -> bool:
        """Paint the desktop's own symbol if there is one; False when there is not."""
        self._ensure_host_icon()
        icon = self._host_icon
        if icon is None or icon.isNull():
            return False
        size = HOST_SYMBOL_SIZE
        mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
        dpr = float(self.devicePixelRatioF() or 1.0)
        try:
            pixmap = icon.pixmap(QSize(size, size), dpr, mode, QIcon.State.Off)
        except Exception:
            return False
        if pixmap.isNull():
            return False
        if self._host_icon_symbolic:
            # A symbolic icon is one colour: give it the live foreground, which
            # is what a themed title bar does with it.
            pixmap = tinted_pixmap(pixmap, colour)
        pixmap.setDevicePixelRatio(dpr)
        painter.drawPixmap(
            (self.width() - size) // 2,
            (self.height() - size) // 2,
            pixmap,
        )
        return True

    # -- painting ----------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        try:
            host_symbols = host_caption_symbols_apply()
            hot = self.isEnabled() and (self.underMouse() or self.isDown())
            if hot:
                painter.fillRect(
                    self.rect(),
                    caption_hot_fill(
                        self.role,
                        dark=_dark_surface(self),
                        pressed=self.isDown(),
                        host_symbols=host_symbols,
                    ),
                )
            colour = QColor(self.palette().color(QPalette.ColorRole.WindowText))
            if self.role == ROLE_CLOSE and hot and not host_symbols:
                colour = QColor("#ffffff")
            if not self.isEnabled():
                colour.setAlpha(110)
            if not (host_symbols and self._paint_host_symbol(painter, colour)):
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
        self._maximise_allowed = True
        self.setObjectName("windowControls")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setAutoFillBackground(False)
        self.setFixedSize(CONTROL_WIDTH * 2, CONTROL_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.min_button = CaptionControlButton(ROLE_MINIMIZE, self)
        self.max_button = CaptionControlButton(ROLE_MAXIMIZE, self)
        self.close_button = CaptionControlButton(ROLE_CLOSE, self)
        # The maximise glyph is always created so caption-hit testing stays in
        # step with the other two slots, but it is only shown when the host
        # allows maximising. On macOS there is no in-app strip, and elsewhere
        # we suppress it so the app cannot be thrown full-screen.
        self.max_button.setVisible(False)
        for button in (self.min_button, self.max_button, self.close_button):
            layout.addWidget(button, 0, Qt.AlignLeft)

        self.min_button.clicked.connect(self._minimize)
        self.max_button.clicked.connect(self._toggle_maximize)
        self.close_button.clicked.connect(self._close)
        self.set_state(self._is_maximized())

    def set_maximise_allowed(self, allowed: bool) -> None:
        """Show or hide the maximise button.

        The glyph is always painted and wired to caption hit-testing; only its
        visibility follows the host policy, so a forced-restore/window-manager
        maximise path still maps cleanly to the three-slot caption layout.
        """
        allowed = bool(allowed)
        if allowed == self._maximise_allowed:
            return
        self._maximise_allowed = allowed
        self.max_button.setVisible(allowed)
        self.updateGeometry()

    # -- public interface --------------------------------------------------
    def set_state(self, maximized: bool) -> None:
        """Show the restore glyph while the window is maximized."""
        self.max_button.set_maximized(maximized)

    def set_close_enabled(self, enabled: bool) -> None:
        """Mirror the greyed-out close button an install puts on the caption."""
        self.close_button.setEnabled(bool(enabled))

    def refresh_theme(self) -> None:
        for button in (self.min_button, self.max_button, self.close_button):
            button.refresh_theme()

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
        if not self._maximise_allowed:
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
