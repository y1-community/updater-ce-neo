"""Reusable widgets — port of InniUpdaterChin's ``app.ui.widgets``

Widgets use CSS class properties for static styling (handled by the global
QSS) and ``dark.T()`` colour tokens only for dynamically computed styles
(status tag colours, banner variants, etc.).
"""

import os
import sys

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QEventLoop,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette, QPen, QRegion
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

# Qt's QWIDGETSIZE_MAX. PySide6 does not re-export the macro.
_WIDGET_SIZE_MAX = (1 << 24) - 1

from ..i18n import tr
from .dark import T, progress_bar_colors


class StatusTag(QLabel):
    """Coloured pill indicating the current flash-phase status."""

    _COLORS = {
        "idle":         "status_idle",
        "selected":     "status_selected",
        "connected":    "status_connected",
        "disconnected": "status_disconnected",
        "flashing":     "status_flashing",
        "complete":     "status_complete",
        "failed":       "status_failed",
        "retrying":     "status_retrying",
    }

    def __init__(self, status="idle", parent=None):
        super().__init__(parent)
        self._status = status
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(28)
        self.setMinimumWidth(84)
        self._apply()

    def set_status(self, status):
        self._status = status
        self._apply()

    def retranslate(self):
        self._apply()

    def _apply(self):
        t = T()
        key = self._COLORS.get(self._status, "status_idle")
        fg, bg = getattr(t, key)
        self.setText(tr(f"status_{self._status}"))
        self.setStyleSheet(
            f"background-color: {bg}; color: {fg};"
            f" border-radius: 4px; font-size: 11px; font-weight: 700;"
            f" padding: 3px 10px; letter-spacing: 0.01em;"
        )


class StepIndicator(QWidget):
    """Horizontal step dots/labels for the S1-S6 flow."""

    _STEP_KEYS = [
        "home_step_1", "home_step_2", "home_step_3",
        "home_step_4", "home_step_5", "home_step_6",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = []
        self._build_ui()
        self.set_active_step(0)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for _ in range(6):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumWidth(68)
            layout.addWidget(lbl, 1)
            self._labels.append(lbl)
        self.setFixedHeight(34)

    def set_active_step(self, index):
        self._active = index
        t = T()
        for i, lbl in enumerate(self._labels):
            text = self._STEP_KEYS[i] if i < len(self._STEP_KEYS) else ""
            if i < index:
                lbl.setText(f"&#10003; {tr(text)}")
                lbl.setStyleSheet(f"color: {t.ok_fg}; font-size: 11px; font-weight: 700;")
            elif i == index:
                lbl.setText(f"&#9679; {tr(text)}")
                lbl.setStyleSheet(f"color: {t.fg_primary}; font-size: 11px; font-weight: 700;")
            else:
                lbl.setText(f"&#9675; {tr(text)}")
                lbl.setStyleSheet(f"color: {t.fg}; font-size: 11px; font-weight: 500;")

    def retranslate(self):
        self.set_active_step(getattr(self, "_active", 0))


class InfoRow(QWidget):
    """Key-value row: label on the left, value on the right."""

    def __init__(self, label_key="", parent=None):
        super().__init__(parent)
        self._label_key = ""
        self._label = QLabel()
        self._label.setProperty("cssClass", "dimmed")
        self._value = QLabel()
        self._value.setProperty("cssClass", "infoValue")
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)
        if label_key:
            self.set_label(label_key)

    def set_label(self, key):
        self._label_key = key
        self._label.setText(tr(key))

    def retranslate(self):
        if self._label_key:
            self._label.setText(tr(self._label_key))

    def set_value(self, value):
        self._value.setText(str(value))

    def value(self):
        return self._value.text()


# Short content cross-fade. Qt does not expose WinUI NavigationTransitionInfo
# or the macOS view transition, so every toolkit uses this same opacity fade.
# 180ms is a short fade, not a slide or a bounce.
PAGE_FADE_MS = 180

# Closing a settings card. Same constraint as the fade: one short geometry
# animation for Windows, macOS, and Linux. 200ms sits in the 180–220ms range.
COLLAPSE_MS = 200


def fade_in(widget) -> None:
    """Short opacity fade used when a page or a card swaps its contents.

    The same duration as ``CurrentPageStack``. Reduced motion, and the
    offscreen test harness, show the new contents immediately.
    """
    if widget is None or prefers_reduced_motion():
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", widget)
    anim.setDuration(PAGE_FADE_MS)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def finish():
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    anim.finished.connect(finish)
    widget._fade_anim = anim
    anim.start()


def prefers_reduced_motion() -> bool:
    """True when the OS, the toolkit, or the test harness wants an instant swap."""
    if os.environ.get("UPDATER_REDUCE_MOTION") == "1":
        return True
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return True
    if os.environ.get("GTK_ENABLE_ANIMATIONS") == "0":
        return True
    if sys.platform == "win32":
        try:
            import ctypes

            enabled = ctypes.c_int(1)
            # SPI_GETCLIENTAREAANIMATION
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return enabled.value == 0
        except Exception:
            return False
    if sys.platform == "darwin":
        try:
            from AppKit import NSWorkspace

            return bool(NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
        except Exception:
            return False
    return False


def layout_gap_closing(widget) -> bool:
    """True while *widget* is still animating closed."""
    anim = getattr(widget, "_collapse_anim", None)
    if anim is None:
        return False
    return anim.state() == QAbstractAnimation.State.Running


def _vertical_layout_spacing(layout, parent) -> int:
    spacing = layout.spacing()
    if spacing >= 0:
        return spacing
    if parent is None:
        return 0
    metric = parent.style().pixelMetric(QStyle.PixelMetric.PM_LayoutVerticalSpacing, None, parent)
    return max(0, int(metric))


def close_layout_gap(widget) -> None:
    """Remove *widget* and let the cards under it move up to close the gap.

    The form stays top-aligned, so the cards above hold still and the cards
    below travel up by the removed height plus the layout spacing. That is
    the two sides of the gap meeting. Qt does not expose WinUI
    NavigationTransitionInfo or macOS view transitions, so this short
    geometry animation is the one shared by Windows, macOS, and Linux.
    Reduced motion, ``UPDATER_REDUCE_MOTION=1``, and
    ``QT_QPA_PLATFORM=offscreen`` hide the widget immediately.
    """
    if widget is None:
        return
    parent = widget.parentWidget()
    layout = parent.layout() if parent is not None else None
    vertical = (
        isinstance(layout, QBoxLayout)
        and layout.direction() == QBoxLayout.Direction.TopToBottom
        and layout.indexOf(widget) >= 0
    )
    if (
        prefers_reduced_motion()
        or not widget.isVisible()
        or widget.height() <= 0
        or not vertical
    ):
        widget.hide()
        return

    layout.activate()
    index = layout.indexOf(widget)
    below = []
    for i in range(index + 1, layout.count()):
        item = layout.itemAt(i)
        child = item.widget() if item is not None else None
        if child is not None and child.isVisible():
            below.append(child)
    start_card = widget.geometry()
    start_below = [child.geometry() for child in below]
    # The next card has to land where this one starts, so the shift is the
    # real distance between those tops (the card plus the gap under it).
    if start_below:
        shift = start_below[0].y() - start_card.y()
    else:
        shift = start_card.height() + _vertical_layout_spacing(layout, parent)
    if shift <= 0:
        widget.hide()
        return
    # The layout would fight setGeometry on every tick. It is turned back on
    # when the cards are already in the positions it would have chosen.
    layout.setEnabled(False)
    # An explicit minimum lets the card shrink. A size hint would otherwise
    # hold the old height and the gap would stay open.
    widget.setMinimumHeight(0)

    anim = QVariantAnimation(widget)
    anim.setDuration(COLLAPSE_MS)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def on_value(progress):
        t = max(0.0, min(1.0, float(progress)))
        new_h = max(0, int(round(start_card.height() * (1.0 - t))))
        widget.setGeometry(start_card.x(), start_card.y(), start_card.width(), new_h)
        if new_h > 0 and start_card.width() > 0:
            widget.setMask(QRegion(0, 0, start_card.width(), new_h))
        else:
            widget.clearMask()
        dy = int(round(shift * t))
        for child, geom in zip(below, start_below):
            child.setGeometry(geom.x(), geom.y() - dy, geom.width(), geom.height())
        if parent is not None:
            parent.update()

    def finish():
        try:
            widget.clearMask()
            widget.hide()
            widget.setMinimumHeight(0)
            widget.setMaximumHeight(_WIDGET_SIZE_MAX)
        finally:
            layout.setEnabled(True)
            layout.invalidate()
            layout.activate()
            widget._collapse_anim = None

    anim.valueChanged.connect(on_value)
    anim.finished.connect(finish)
    widget._collapse_anim = anim
    anim.start()


class CurrentPageStack(QStackedWidget):
    """Size to the visible page, not the tallest one in the stack.

    Changing pages fades only this stack. Sidebar rows and window chrome stay put.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_fade = None

    def sizeHint(self):
        current = self.currentWidget()
        if current is None:
            return super().sizeHint()
        return current.sizeHint()

    def minimumSizeHint(self):
        current = self.currentWidget()
        if current is None:
            return super().minimumSizeHint()
        return current.minimumSizeHint()

    def setCurrentWidget(self, widget) -> None:  # noqa: N802 (Qt naming)
        self.setCurrentIndex(self.indexOf(widget))

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 (Qt naming)
        index = int(index)
        if index == self.currentIndex() or index < 0 or index >= self.count():
            return
        if self._skip_page_fade():
            self._clear_page_fade()
            super().setCurrentIndex(index)
            return
        outgoing = self.currentWidget()
        shot = None
        if outgoing is not None and outgoing.isVisible() and self.width() > 1 and self.height() > 1:
            shot = outgoing.grab()
        self._clear_page_fade()
        super().setCurrentIndex(index)
        page = self.currentWidget()
        if page is None:
            return
        # The live page stays on the platform style. A grabbed frame of the
        # page that just left fades out over it. Qt has no WinUI or AppKit
        # view transition, so this short fade is the shared one, and it is
        # skipped when the OS asks for reduced motion.
        if shot is not None and not shot.isNull():
            overlay = QLabel(self)
            overlay.setPixmap(shot)
            overlay.setGeometry(self.rect())
            overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            effect = QGraphicsOpacityEffect(overlay)
            effect.setOpacity(1.0)
            overlay.setGraphicsEffect(effect)
            overlay.show()
            overlay.raise_()
            self._fade_overlay = overlay
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
        else:
            effect = QGraphicsOpacityEffect(page)
            effect.setOpacity(0.0)
            page.setGraphicsEffect(effect)
            anim = QPropertyAnimation(effect, b"opacity", self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
        anim.setDuration(PAGE_FADE_MS)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._finish_page_fade)
        self._page_fade = anim
        anim.start()

    def _skip_page_fade(self) -> bool:
        if prefers_reduced_motion():
            return True
        if not self.isVisible():
            return True
        return False

    def _finish_page_fade(self) -> None:
        self._clear_page_fade()

    def _clear_page_fade(self) -> None:
        anim = self._page_fade
        self._page_fade = None
        if anim is not None:
            anim.stop()
        overlay = getattr(self, "_fade_overlay", None)
        self._fade_overlay = None
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()
        page = self.currentWidget()
        if page is not None and page.graphicsEffect() is not None:
            page.setGraphicsEffect(None)


def _layout_widgets(layout):
    """Widgets owned by *layout*, including those nested in child layouts."""
    found = []
    if layout is None:
        return found
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            found.append(widget)
        child = item.layout()
        if child is not None:
            found.extend(_layout_widgets(child))
    return found


class Card(QFrame):
    """Native desktop card panel matching human interface guidelines."""

    def __init__(self, title_key="", parent=None):
        super().__init__(parent)
        self.setProperty("cssClass", "card")
        self._title_key = title_key
        self._title = None
        self._glass_clear = False
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(14, 12, 14, 12)
        self._outer.setSpacing(10)
        if title_key:
            self.setTitle(tr(title_key))

    def setTitle(self, text: str) -> None:
        """Set or update the card title header label."""
        if self._title is None:
            self._title = QLabel(text)
            self._title.setProperty("cssClass", "cardTitle")
            self._outer.insertWidget(0, self._title)
        else:
            self._title.setText(text)

    def title(self) -> str:
        """Return the current title text."""
        return self._title.text() if self._title is not None else ""

    def title_label(self):
        """The title QLabel itself, for a row that shares the header line."""
        return self._title

    def put_widget_on_title_row(self, widget) -> None:
        """Move ``widget`` onto the title line, right-aligned.

        For a card whose title already names the setting ("Offline Mode"), the
        switch belongs on that line: repeating the same words underneath as a
        caption is a row the eye skips over and a row of vertical space.
        """
        if widget is None or self._title is None:
            return
        row = getattr(self, "_title_row", None)
        if row is None:
            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(12)
            # The title label itself moves into the row, so retranslate() and
            # title() keep addressing the same widget.
            self._outer.removeWidget(self._title)
            box.addWidget(self._title, 1)
            self._outer.insertWidget(0, row)
            self._title_row = row
        row.layout().addWidget(widget, 0, Qt.AlignRight | Qt.AlignVCenter)

    def retranslate(self):
        if self._title_key:
            self.setTitle(tr(self._title_key))

    def set_glass_clear(self, clear: bool = True) -> None:
        """Show the system material through this card.

        A frosted fill on an opaque widget buffer becomes a dark rectangle.
        The version list and the notes sit on the same texture as the window.
        """
        self._glass_clear = bool(clear)
        if self._glass_clear:
            from .surfaces import clear_glass_plate
            clear_glass_plate(self)
        self.update()

    def set_layout(self, layout):
        self._outer.addLayout(layout)

    def add_widget(self, widget):
        self._outer.addWidget(widget)

    def run_confirmation(self, body: str, accept: str, reject: str, *, note: str = "") -> bool:
        """Replace this card's contents with a short confirmation, then restore them.

        The swap uses the same short fade as a page change. Reduced motion
        skips it. ``True`` means the accept button was chosen.
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if note:
            note_lbl = QLabel(note)
            note_lbl.setWordWrap(True)
            note_lbl.setProperty("cssClass", "subtitle")
            layout.addWidget(note_lbl)
        if body:
            body_lbl = QLabel(body)
            body_lbl.setWordWrap(True)
            body_lbl.setProperty("cssClass", "subtitle")
            layout.addWidget(body_lbl)
        row = QHBoxLayout()
        row.setSpacing(8)
        accept_btn = QPushButton(accept)
        reject_btn = QPushButton(reject)
        accept_btn.setCursor(Qt.ArrowCursor)
        reject_btn.setCursor(Qt.ArrowCursor)
        accept_btn.setMinimumHeight(28)
        reject_btn.setMinimumHeight(28)
        row.addWidget(accept_btn)
        row.addWidget(reject_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._confirm_accept = accept_btn
        self._confirm_reject = reject_btn

        hidden = [widget for widget in _layout_widgets(self._outer) if widget.isVisible()]
        for widget in hidden:
            widget.hide()
        self._outer.addWidget(panel)
        panel.show()
        fade_in(panel)

        loop = QEventLoop(self)
        result = {"ok": False}

        def accept_clicked():
            result["ok"] = True
            loop.quit()

        def reject_clicked():
            result["ok"] = False
            loop.quit()

        accept_btn.clicked.connect(accept_clicked)
        reject_btn.clicked.connect(reject_clicked)
        try:
            loop.exec()
        finally:
            self._outer.removeWidget(panel)
            panel.hide()
            panel.setParent(None)
            panel.deleteLater()
            self._confirm_accept = None
            self._confirm_reject = None
            for widget in hidden:
                widget.show()
            fade_in(self)
        return bool(result["ok"])

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        """Frost the card in place. Source replaces the rect, so it does not
        stack on the previous frame and does not clear the rest of the window."""
        from .surfaces import glass_surfaces_enabled

        if not glass_surfaces_enabled():
            super().paintEvent(event)
            return
        if self._glass_clear:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(event.rect())
        from .dark import is_dark
        # Clear first. A Source fill of a pale color on an opaque child buffer
        # composites against black and leaves a blotch behind the card text.
        rect = self.rect().adjusted(0, 0, -1, -1)
        path = self._rounded(rect)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.fillPath(path, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        fill = QColor(255, 255, 255, 18 if is_dark() else 32)
        painter.setBrush(fill)
        painter.fillPath(path, fill)
        edge = QColor(255, 255, 255, 40) if is_dark() else QColor(0, 0, 0, 36)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(edge))
        painter.drawPath(path)
        painter.end()

    @staticmethod
    def _rounded(rect):
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        return path


def banner_material_color(kind: str = "info") -> QColor:
    """Translucent pane for an in-window toast or banner.

    Windows and macOS use a thin frost so acrylic or the window vibrancy
    stays visible. Linux tints the desktop window color. A status kind adds
    only a faint tint. The result is never an opaque blue, green, or red bar.
    """
    kind = kind or "info"
    app = QApplication.instance()
    window = QColor("#1e2227")
    if app is not None:
        window = app.palette().color(QPalette.ColorRole.Window)
    dark = window.lightness() < 140
    if sys.platform.startswith("linux"):
        # A Breeze/Adwaita pane: the window color, lifted just enough to read
        # as a card, still see-through.
        veil = QColor(window)
        if dark:
            veil = veil.lighter(150)
            veil.setAlpha(56)
        else:
            veil = veil.darker(110)
            veil.setAlpha(40)
    elif sys.platform == "darwin":
        veil = QColor(255, 255, 255, 32 if dark else 64)
    else:
        # WinUI acrylic card: a light frost, not a flat accent rectangle.
        veil = QColor(255, 255, 255, 28 if dark else 72)
    if kind == "success":
        veil = QColor(veil)
        veil.setGreen(min(255, veil.green() + 24))
    elif kind == "warning":
        veil = QColor(veil)
        veil.setRed(min(255, veil.red() + 28))
    elif kind == "danger":
        veil = QColor(veil)
        veil.setRed(min(255, veil.red() + 36))
    if veil.alpha() > 80:
        veil.setAlpha(80)
    return veil


def _apply_banner_text(label: QLabel) -> None:
    """Window text on the frost. No stylesheet background, which Qt paints as a slab."""
    label.setAutoFillBackground(False)
    label.setStyleSheet("")
    app = QApplication.instance()
    fg = QColor("#ffffff")
    if app is not None:
        fg = app.palette().color(QPalette.ColorRole.WindowText)
    pal = label.palette()
    pal.setColor(QPalette.ColorRole.WindowText, fg)
    pal.setColor(QPalette.ColorRole.Text, fg)
    label.setPalette(pal)
    label.setForegroundRole(QPalette.ColorRole.WindowText)


class Banner(QLabel):
    """In-window status line. The fill is a translucent pane, not a solid color."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(42)
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._key = ""
        self._fmt = {}
        self._kind = "info"
        font = self.font()
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        self.setFont(font)
        self.setContentsMargins(14, 8, 14, 8)
        self.set_type("info")

    def set_key(self, key, **fmt):
        self._key = key
        self._fmt = fmt
        self.setText(tr(key).format(**fmt) if fmt else tr(key))

    def retranslate(self):
        if self._key:
            self.setText(tr(self._key).format(**self._fmt) if self._fmt else tr(self._key))

    def set_type(self, banner_type):
        self._kind = banner_type or "info"
        self.setStyleSheet("")
        _apply_banner_text(self)
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(event.rect())
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setPen(Qt.NoPen)
        painter.setBrush(banner_material_color(self._kind))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.end()
        super().paintEvent(event)


class UpdateToast(QWidget):
    """One-line update note on Select Software.

    One device shows Install and a session-only close. Several devices show a
    button per model name. Close hides the note until the next launch.
    """

    install_clicked = Signal()
    close_clicked = Signal()
    model_clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("releaseUpdatePrompt")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setMinimumHeight(44)
        self._offers = []
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 8, 6)
        row.setSpacing(8)
        self._label = QLabel("")
        self._label.setWordWrap(False)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        font = self._label.font()
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.Medium)
        self._label.setFont(font)
        _apply_banner_text(self._label)
        row.addWidget(self._label, 1)

        self._models = QHBoxLayout()
        self._models.setSpacing(6)
        self._models.setContentsMargins(0, 0, 0, 0)
        row.addLayout(self._models, 0)
        self._model_buttons = []

        self._install = QPushButton(tr("toast_install"))
        self._install.setObjectName("updateToastInstall")
        self._install.setCursor(Qt.CursorShape.ArrowCursor)
        self._install.setStyleSheet("")
        self._install.clicked.connect(self.install_clicked.emit)
        row.addWidget(self._install, 0)

        self._close = QPushButton("\u00d7")
        self._close.setObjectName("updateToastClose")
        self._close.setFlat(True)
        self._close.setCursor(Qt.CursorShape.ArrowCursor)
        self._close.setStyleSheet("")
        self._close.setFixedWidth(28)
        self._close.setToolTip(tr("toast_dismiss"))
        self._close.clicked.connect(self.close_clicked.emit)
        row.addWidget(self._close, 0)
        self.setVisible(False)

    def set_offers(self, offers) -> None:
        self._offers = [offer for offer in (offers or []) if (offer or {}).get("model")]
        self._rebuild_models()
        self._apply_copy()
        self.setVisible(bool(self._offers))

    def set_message(self, software: str, model: str) -> None:
        if software and model:
            self.set_offers([{"software": software, "model": model, "tag": ""}])
        else:
            self.set_offers([])

    def offers(self) -> list:
        return list(self._offers)

    def text(self) -> str:
        return self._label.text()

    def _rebuild_models(self) -> None:
        while self._models.count():
            item = self._models.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._model_buttons = []
        several = len(self._offers) > 1
        self._install.setVisible(len(self._offers) == 1)
        if not several:
            return
        for offer in self._offers:
            button = QPushButton(str(offer.get("model") or ""))
            button.setObjectName("updateToastModel")
            button.setCursor(Qt.CursorShape.ArrowCursor)
            button.setStyleSheet("")
            button.clicked.connect(lambda _checked=False, chosen=offer: self.model_clicked.emit(chosen))
            self._models.addWidget(button)
            self._model_buttons.append(button)

    def _apply_copy(self) -> None:
        if len(self._offers) == 1:
            offer = self._offers[0]
            self._label.setText(tr("sel_update_available").format(
                software=offer.get("software") or "",
                model=offer.get("model") or "",
            ))
        elif len(self._offers) > 1:
            self._label.setText(tr("sel_updates_available"))
        else:
            self._label.setText("")
        _apply_banner_text(self._label)
        self._close.setToolTip(tr("toast_dismiss"))
        self._install.setText(tr("toast_install"))

    def retranslate(self) -> None:
        self._apply_copy()
        self._install.setText(tr("toast_install"))

    def refresh_theme(self) -> None:
        _apply_banner_text(self._label)
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(event.rect())
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setPen(Qt.NoPen)
        painter.setBrush(banner_material_color("info"))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 8, 8)
        painter.end()


class BatteryToast(QFrame):
    """Toast hint shown on first run reminding user to charge player/device battery before use.

    Features a card panel texture matching settings cards and firmware filters, with a circular checkmark (dismiss) button.
    """

    dismissed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("batteryToastHint")
        self.setProperty("cssClass", "card")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumHeight(38)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 12, 8)
        row.setSpacing(10)

        self._icon = QLabel("🔋", self)
        self._icon.setStyleSheet("font-size: 14px; background: transparent; border: none;")
        row.addWidget(self._icon, 0, Qt.AlignVCenter)

        self._label = QLabel(self._get_battery_text(), self)
        self._label.setWordWrap(True)
        self._label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        font = self._label.font()
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.Medium)
        self._label.setFont(font)
        _apply_banner_text(self._label)
        row.addWidget(self._label, 1, Qt.AlignVCenter)

        from .icons import get_symbol_icon

        self._dismiss_btn = QPushButton(self)
        self._dismiss_btn.setObjectName("batteryToastDismissBtn")
        self._dismiss_btn.setFixedSize(24, 24)
        self._dismiss_btn.setCursor(Qt.PointingHandCursor)
        self._dismiss_btn.setIcon(get_symbol_icon("check", 12))
        self._dismiss_btn.setIconSize(QSize(12, 12))
        self._dismiss_btn.setToolTip(tr("toast_dismiss"))
        self._dismiss_btn.setStyleSheet(
            "QPushButton#batteryToastDismissBtn {"
            "  border-radius: 12px;"
            "  background: rgba(37, 99, 235, 0.18);"
            "  border: none;"
            "}"
            "QPushButton#batteryToastDismissBtn:hover {"
            "  background: rgba(37, 99, 235, 0.32);"
            "}"
            "QPushButton#batteryToastDismissBtn:pressed {"
            "  background: rgba(37, 99, 235, 0.45);"
            "}"
        )
        self._dismiss_btn.clicked.connect(self.dismissed.emit)
        row.addWidget(self._dismiss_btn, 0, Qt.AlignVCenter)

    def _get_battery_text(self) -> str:
        from ..branding import is_generic_mtk_brand

        key = "toast_battery_charged_device" if is_generic_mtk_brand() else "toast_battery_charged"
        return tr(key)

    def retranslate(self) -> None:
        from .icons import get_symbol_icon

        self._label.setText(self._get_battery_text())
        _apply_banner_text(self._label)
        self._dismiss_btn.setToolTip(tr("toast_dismiss"))
        self._dismiss_btn.setIcon(get_symbol_icon("check", 12))

    def refresh_theme(self) -> None:
        _apply_banner_text(self._label)
        self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        """Frost the toast card consistent with settings cards and filter group."""
        from .surfaces import glass_surfaces_enabled

        if not glass_surfaces_enabled():
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipRect(event.rect())
        from .dark import is_dark

        rect = self.rect().adjusted(0, 0, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 8, 8)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.fillPath(path, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        fill = QColor(255, 255, 255, 18 if is_dark() else 32)
        painter.setBrush(fill)
        painter.fillPath(path, fill)
        edge = QColor(255, 255, 255, 40) if is_dark() else QColor(0, 0, 0, 36)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(edge))
        painter.drawPath(path)
        painter.end()


# ---------------------------------------------------------------------------
# Progress bars
# ---------------------------------------------------------------------------

BUSY_SEGMENT = 0.28
"""Width of the sweeping segment in the busy state, as a fraction of the bar."""


def paint_progress_bar(
    painter,
    rect,
    *,
    track: QColor,
    fill: QColor,
    label_color: QColor,
    fraction: float = 0.0,
    text: str = "",
    busy: bool = False,
    offset: float = 0.0,
) -> None:
    """Paint a rounded progress bar: track, accent chunk and centred percent.

    A free function on purpose so the paint can be checked on its own canvas:
    ``QWidget.render()`` fills an opaque background first, so a rendered
    widget never proves what the bar itself drew.
    """
    rect = QRectF(rect)
    if rect.width() <= 1 or rect.height() <= 1:
        return
    radius = min(8.0, rect.height() / 2.0)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(track)
    painter.drawRoundedRect(rect, radius, radius)

    if busy:
        span = rect.width() * BUSY_SEGMENT
        travel = rect.width() + span
        left = rect.left() - span + travel * (offset % 1.0)
        chunk = QRectF(left, rect.top(), span, rect.height()).intersected(rect)
    else:
        width = rect.width() * max(0.0, min(1.0, float(fraction)))
        chunk = QRectF(rect.left(), rect.top(), width, rect.height())
    if chunk.width() > 1:
        chunk_radius = min(radius, chunk.width() / 2.0, chunk.height() / 2.0)
        painter.setBrush(fill)
        painter.drawRoundedRect(chunk, chunk_radius, chunk_radius)

    if text and not busy:
        # A sweeping bar has no percentage to name.
        painter.setPen(label_color)
        painter.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), text)
    painter.restore()


class InstallProgress(QProgressBar):
    """Install and download progress bar, painted from the live theme.

    The platform styles stopped drawing the chunk on some hosts — Aqua under
    the app palette and acrylic on Windows both showed a groove with no fill,
    so an install looked stalled at every percentage. The bar is painted here
    instead: rounded track, accent chunk, and the percent centred on the bar
    the way a system updater shows progress. A range of ``(0, 0)`` is the busy
    state, drawn as a segment sweeping across an empty track.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # The sweep state comes first: property setters below ask for it.
        self._sweep = 0.0
        self._precise_value: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)
        self.setObjectName("softwareUpdateProgress")
        self.setRange(0, 100)
        self.setValue(0)
        self.setTextVisible(True)
        self.setFormat("%p%")
        self.setMinimumHeight(18)
        self.setMaximumHeight(24)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- state ---------------------------------------------------------
    def is_busy(self) -> bool:
        """A bar with nothing left to fill sweeps instead of filling."""
        return self.maximum() <= self.minimum()

    def set_busy(self, busy: bool = True) -> None:
        """``(0, 0)`` for the sweeping state, ``(0, 100)`` at zero otherwise."""
        self._precise_value = None
        if busy:
            self.setRange(0, 0)
            return
        self.setRange(0, 100)
        self.setFormat("%p%")
        self.setTextVisible(True)
        self.setValue(0)

    def set_precise_value(self, value: float) -> None:
        """Set a decimal percentage (e.g. 18.4) for 1-decimal display."""
        try:
            val = float(value)
        except (TypeError, ValueError):
            val = 0.0
        val = max(0.0, min(100.0, val))
        self._precise_value = val
        self.setRange(0, 100)
        self.setTextVisible(True)
        super().setValue(int(round(val)))
        self.update()

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        self._timer.stop()
        super().hideEvent(event)

    def setRange(self, minimum, maximum):  # noqa: N802 (Qt naming)
        super().setRange(minimum, maximum)
        self._sync_animation()

    def setValue(self, value):  # noqa: N802 (Qt naming)
        if isinstance(value, float):
            self.set_precise_value(value)
            return
        self._precise_value = None
        super().setValue(int(value) if value is not None else 0)
        if self.is_busy():
            self._sync_animation()

    def set_failed(self, failed: bool = True, text: str = "") -> None:
        """Switch the progress bar to the failed state: percent replaced by 'Install Failed'."""
        self._failed = bool(failed)
        self._failed_text = text or tr("install_failed")
        self._sync_animation()
        self.update()

    def is_failed(self) -> bool:
        return getattr(self, "_failed", False)

    def _sync_animation(self) -> None:
        """Sweep only while a busy bar is on screen and motion is allowed."""
        timer = getattr(self, "_timer", None)
        if timer is None:
            return
        wanted = self.is_busy() and not self.is_failed() and self.isVisible() and not prefers_reduced_motion()
        if wanted and not timer.isActive():
            self._sweep = 0.0
            timer.start()
        elif not wanted and timer.isActive():
            timer.stop()
        self.update()

    def _tick(self) -> None:
        self._sweep = (self._sweep + 0.02) % 1.0
        self.update()

    def text(self) -> str:
        if self.is_failed():
            return getattr(self, "_failed_text", "") or tr("install_failed")
        pv = getattr(self, "_precise_value", None)
        if pv is not None and not self.is_busy():
            return f"{pv:.1f}%"
        return super().text()

    def fraction(self) -> float:
        """How much of the track the chunk covers, 0.0 to 1.0."""
        if self.is_failed():
            return 0.0
        pv = getattr(self, "_precise_value", None)
        if pv is not None and not self.is_busy():
            return max(0.0, min(1.0, pv / 100.0))
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.value() - self.minimum()) / span))

    # -- paint ---------------------------------------------------------
    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        busy = self.is_busy() and not self.is_failed()
        track, fill, label_color = progress_bar_colors(self)
        if self.is_failed():
            from .dark import T
            t = T()
            label_color = QColor(t.danger_fg)
        text = "" if busy or not self.isTextVisible() else (self.text() or "")
        painter = QPainter(self)
        paint_progress_bar(
            painter,
            self.rect(),
            track=track,
            fill=fill,
            label_color=label_color,
            fraction=self.fraction(),
            text=text,
            busy=busy,
            offset=self._sweep,
        )
        painter.end()
