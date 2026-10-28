"""Flash page — modern OS software update in-progress display (macOS / iOS / Windows Fluent style)."""

import logging
from pathlib import Path
import sys

logger = logging.getLogger(__name__)

from PySide6.QtCore import QEvent, QEventLoop, QRect, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from .tab_focus import TabFocusManager
from ..flash_service import (
    METHOD_MTK,
    METHOD_SP,
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_WAITING,
    STEP_WRITE,
    default_flash_method,
    normalise_method,
)
from ..i18n import tr, tr_brand
from ..progress_model import format_eta_seconds
from .support_appeal import COMPLETION_KEYS, SupportIntro
from .widgets import (
    Banner,
    Card,
    CurrentPageStack,
    InfoRow,
    InstallProgress,
    StatusTag,
    fade_in,
)
from .dark import T, page_top_margin
from .icons import get_symbol_icon, get_symbol_pixmap
from .native_controls import (
    NativeRoundChip,
    NativeSegmentedPair,
    native_round_buttons_available,
)

# Width the progress copy wraps into. A wrapped QLabel inside a horizontal
# card reports a one-line size hint unless it is given this width.
INSTALL_TEXT_WIDTH = 220

ROCKBOX_SITE_THEMES_URL = "https://themes.rockbox.org/index.php?target=ipod6g"

#: Settle time after ADB detection so org.rockbox can populate .rockbox.
ROCKBOX_BOOT_WAIT_MS = 20_000

#: Height of the paperclip diagram. Drawn 8% larger than the original 60 px so
#: the pin and the button it points at stay legible on a phone-sized photo.
_PAPERCLIP_HINT_PX = round(60 * 1.08)


def install_horizontal_chrome(sidebar: int) -> int:
    """Pixels beside the wrapping column: sidebar, pads, icon, and the chips.

    The head row is the widest line on a progress card — icon, the software
    title with its progress band, and the round chips — so it is what decides
    how wide the window has to be for the copy to keep wrapping at
    ``INSTALL_TEXT_WIDTH``. The chips sit on that row rather than centred on the
    card, so they are counted here beside the column, not inside it.
    """
    page_pad = 32
    wrap_pad = 8
    card_outer = 28
    card_inner = 32
    icon = _CARD_ICON_PX
    gaps = 2 * _CARD_HEAD_GAP
    chips = 2 * ROUND_NEXT_BUTTON_PX + 10
    slack = 16
    return (
        int(sidebar)
        + page_pad
        + wrap_pad
        + card_outer
        + card_inner
        + icon
        + gaps
        + chips
        + slack
    )


_STEP_KEY = {
    STEP_EXTRACTING: "step_extract",
    STEP_WAITING: "step_wait",
    STEP_DETECT: "step_detect",
    STEP_DOWNLOAD_DA: "step_download_da",
    STEP_DOWNLOAD_BL: "step_download_bl",
    STEP_WRITE: "step_write",
    STEP_DONE: "step_done",
}


class _StatusLabel(QLabel):
    """Software title or status line on the progress card.

    A text change asks the card to paint its frost again first, so the new
    glyphs replace the old ones. The label itself stays transparent.
    """

    def setText(self, text):  # noqa: N802 (Qt naming)
        super().setText(text)
        host = self.parentWidget()
        while host is not None and not isinstance(host, Card):
            host = host.parentWidget()
        if host is not None:
            host.update()


class _FitLabel(_StatusLabel):
    """Wrapped status line whose size hint is the wrapped height.

    A word-wrapped QLabel in a horizontal card reports one line and clips.
    Giving it a column width makes ``sizeHint`` / ``heightForWidth`` cover
    every line. A string that still cannot break uses a short scroll, or
    elided text plus a tooltip when motion is reduced.
    """

    def __init__(self, text: str = ""):
        self._wrap_width = INSTALL_TEXT_WIDTH
        self._elide = False
        self._marquee = False
        self._single_line = False
        self._offset = 0
        self._timer = None
        self._ready = False
        super().__init__(text)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self._ready = True
        self._refresh_mode()

    def set_column_width(self, width: int) -> None:
        target_w = max(160, int(width))
        if getattr(self, "_wrap_width", None) == target_w:
            return
        self._wrap_width = target_w
        if not self._single_line:
            self.setMinimumWidth(self._wrap_width)
        self._refresh_mode()
        self.updateGeometry()

    def set_single_line(self, single: bool) -> None:
        """Report the text's own width, for a status row the card can widen.

        Wrapped mode keeps the fixed column so the card has a predictable
        height; single-line mode lets the label sit beside another line and
        only measure one row tall.
        """
        single = bool(single)
        if single == self._single_line:
            return
        self._single_line = single
        self.setMinimumWidth(0 if single else self._wrap_width)
        self.setWordWrap(not single)
        self._refresh_mode()
        self.updateGeometry()

    def line_width(self) -> int:
        """Width of the text on one line, plus a pixel of slack."""
        return self.fontMetrics().horizontalAdvance(self.text() or "") + 2

    def setText(self, text):  # noqa: N802 (Qt naming)
        super().setText(text)
        if getattr(self, "_ready", False):
            self._refresh_mode()
            self.updateGeometry()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return not (self._single_line or self._elide or self._marquee)

    def _wrapped_height(self, width: int) -> int:
        """Height of this label's text wrapped into ``width`` pixels.

        Measured from the font metrics alone. QLabel's own ``heightForWidth``
        must not be folded in: it reflects the minimum height this method sets,
        so including it makes every relayout two pixels taller than the last —
        the window would creep downwards for as long as a backend keeps
        printing lines (mtkclient's connect hint arrives once per USB retry).
        """
        width = max(1, int(width))
        text = self.text() or " "
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap)
        rect = self.fontMetrics().boundingRect(0, 0, width, 10000, flags, text)
        return max(self.fontMetrics().height(), rect.height()) + 2

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        if self._single_line or self._elide or self._marquee:
            return max(18, self.fontMetrics().height() + 2)
        return self._wrapped_height(width)

    def sizeHint(self):  # noqa: N802 (Qt naming)
        if self._single_line:
            return QSize(self.line_width(), self.heightForWidth(self._wrap_width))
        if self._elide or self._marquee:
            return QSize(self._wrap_width, self.heightForWidth(self._wrap_width))
        return QSize(self._wrap_width, self._wrapped_height(self._wrap_width))

    def minimumSizeHint(self):  # noqa: N802 (Qt naming)
        if self._single_line:
            return QSize(0, self.heightForWidth(self._wrap_width))
        return QSize(
            min(self._wrap_width, max(40, self._unbroken_width())),
            self._wrapped_height(self._wrap_width),
        )

    def _unbroken_width(self) -> int:
        text = self.text() or ""
        parts = [part for part in text.replace("\n", " ").split(" ") if part]
        if not parts:
            return 0
        metrics = self.fontMetrics()
        return max(metrics.horizontalAdvance(part) for part in parts)

    def _refresh_mode(self) -> None:
        if not getattr(self, "_ready", False):
            return
        if self._single_line:
            # A shared status row stacks again instead of overflowing, so the
            # scroll / elide fallbacks stay out of the way here.
            self._stop_marquee()
            self.setToolTip("")
            self.setWordWrap(False)
            self.setMinimumHeight(max(18, self.fontMetrics().height() + 2))
            return
        overflows = self._unbroken_width() > self._wrap_width + 8
        self._elide = False
        self._marquee = False
        self.setToolTip("")
        if overflows:
            from .widgets import prefers_reduced_motion

            self.setToolTip(self.text())
            if prefers_reduced_motion():
                self._elide = True
                self._stop_marquee()
            else:
                self._marquee = True
                self._start_marquee()
        else:
            self._stop_marquee()
        self.setWordWrap(not self._elide and not self._marquee)
        if not self._elide and not self._marquee:
            self.setMinimumHeight(self._wrapped_height(self._wrap_width))
        else:
            self.setMinimumHeight(max(18, self.fontMetrics().height() + 2))

    def _start_marquee(self) -> None:
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.setInterval(40)
            self._timer.timeout.connect(self._tick)
        self._offset = 0
        if not self._timer.isActive():
            self._timer.start()

    def _stop_marquee(self) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._offset = 0

    def _tick(self) -> None:
        span = self.fontMetrics().horizontalAdvance(self.text() or "")
        limit = max(0, span - max(1, self.width()) + 16)
        self._offset += 2
        if self._offset > limit + 24:
            self._offset = 0
        self.update()

    def hideEvent(self, event):  # noqa: N802 (Qt naming)
        self._stop_marquee()
        super().hideEvent(event)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        if not self._elide and not self._marquee:
            super().paintEvent(event)
            return
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setClipRect(self.rect())
        text = self.text() or ""
        if self._elide:
            shown = self.fontMetrics().elidedText(
                text,
                Qt.TextElideMode.ElideRight,
                max(1, self.width() or self._wrap_width),
            )
            painter.drawText(
                self.rect(),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                shown,
            )
            painter.end()
            return
        baseline = self.fontMetrics().ascent() + max(0, (self.height() - self.fontMetrics().height()) // 2)
        painter.drawText(-self._offset, baseline, text)
        painter.end()


def _apply_card_text(label) -> None:
    """Light words on a dark card, dark words on a light card. No fill behind them.

    The color is the live theme foreground, written into the label's own
    stylesheet and palette. A stylesheet background is not used:
    ``background: transparent`` is stored as a slab and the erase filter left a
    blotchy backdrop under the status line.
    """
    if label is None:
        return
    from .dark import apply_explicit_foreground

    label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, False)
    label.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
    apply_explicit_foreground(label)


def _style_card_heading(label) -> None:
    if label is None:
        return
    font = label.font()
    font.setPixelSize(14)
    font.setWeight(QFont.Weight.Bold)
    label.setFont(font)
    _apply_card_text(label)


def _status_label(text: str, *, strong: bool = False) -> _FitLabel:
    label = _FitLabel(text)
    font = label.font()
    font.setPixelSize(13 if strong else 12)
    font.setWeight(QFont.Weight.DemiBold if strong else QFont.Weight.Normal)
    label.setFont(font)
    _apply_card_text(label)
    label.set_column_width(INSTALL_TEXT_WIDTH)
    return label


# One card shape, four screens: the software icon, the software title with its
# progress band, and the round action chips, on one row. The copy for the
# screen sits under that row, lined up with the title.
_CARD_ICON_PX = 40
_CARD_HEAD_GAP = 14
# The chip is a disc with a little room around it. What makes it read as raised
# is the drop shadow that room holds, not a heavier disc: the disc itself stays
# translucent so the card's material keeps showing through it.
_CHIP_DISC_PX = 30
_CHIP_SHADOW_PX = 3
ROUND_NEXT_BUTTON_PX = _CHIP_DISC_PX + _CHIP_SHADOW_PX * 2
_ROUND_NEXT_ARROW_PX = 15
_CHIP_SHADOW_ALPHA = 72
# Where a chip row's copy starts, so the icon reads as the card's leading edge.
_CARD_COPY_INDENT = _CARD_ICON_PX + _CARD_HEAD_GAP
# Round chips sit on a glass card, so their discs are translucent too. The
# accent (the step that moves the install on) stays nearly opaque; a neutral
# cancel/back chip reads as a wash over whatever is behind it.
_ACCENT_DISC_ALPHA = 235
_NEUTRAL_DISC_ALPHA = 150


class _CardHead(QWidget):
    """Icon, software title, progress band and the round chips, on one row.

    Every progress card — preparing, waiting, installing, downloading — is this
    row with a different middle. The chips belong here, to the right of and
    inline with the software title and its progress band, and they stay there
    on all four screens: a chip that drifts to the middle of a taller card (on
    the failure copy, say) stops reading as the next step of the title it
    belongs to.

    One widget for all four screens on purpose. Aqua, WinUI, Breeze or Adwaita
    paint the controls inside it, but the layout is decided here, so the same
    card is laid out the same way whichever host look the app is running under.
    """

    def __init__(self, icon: QWidget, title: QWidget, parent=None):
        super().__init__(parent)
        self._aligning = False
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(_CARD_HEAD_GAP)
        self.icon = icon
        self.box.addWidget(icon, 0, Qt.AlignVCenter)
        self.identity = QWidget()
        self.identity_box = QVBoxLayout(self.identity)
        self.identity_box.setContentsMargins(0, 2, 0, 9)
        self.identity_box.setSpacing(6)
        self.title = title
        self.title.setWordWrap(False)
        self.identity_box.addWidget(title)
        self.box.addWidget(self.identity, 1)
        self.band = None
        self._chips: list[_RoundNextButton] = []

    def set_band(self, band: QWidget) -> None:
        """The progress band, directly under the title it reports on."""
        self.band = band
        self.identity_box.addWidget(band)

    def add_chip(self, chip: QWidget) -> None:
        """A round action chip, right of the title and the band.

        Centred on the progress band so the release icon, progress bar and
        round action buttons share one horizontal axis.
        """
        self.box.addWidget(chip, 0, Qt.AlignVCenter)
        if isinstance(chip, _RoundNextButton):
            self._chips.append(chip)
            chip.installEventFilter(self)
        self._sync_chips_pairing()

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if event.type() in (
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.EnabledChange,
            QEvent.Type.Resize,
        ):
            self._sync_chips_pairing()
        return super().eventFilter(watched, event)

    def _sync_chips_pairing(self) -> None:
        visible = [
            c
            for c in self._chips
            if not c.isHidden()
        ]
        if len(visible) == 2:
            c0, c1 = visible[0], visible[1]
            c0.set_segmented_pair(companion=c1, is_leading=True)
            c1.set_segmented_pair(companion=c0, is_leading=False)
        else:
            for c in self._chips:
                c.set_segmented_pair(companion=None)

    def _sync_to_band(self) -> None:
        """Vertically centre the release icon and action chips on the progress band."""
        if getattr(self, "_aligning", False) or not hasattr(self, "identity_box") or not hasattr(self, "band"):
            return
        self._sync_chips_pairing()
        self._aligning = True
        try:
            band_visible = self.band is not None and not self.band.isHidden()
            target_top = 2 if band_visible else 0
            band_h = max(0, self.band.height() if band_visible else 0)
            target_bottom = max(10, (_CARD_ICON_PX // 2) - (band_h // 2) + 1) if band_visible else 0
            margins = self.identity_box.contentsMargins()
            if margins.top() != target_top or margins.bottom() != target_bottom:
                self.identity_box.setContentsMargins(0, target_top, 0, target_bottom)
            self.box.activate()
            self.identity_box.activate()
            if band_visible:
                band_rect = self.band.geometry()
                if band_rect.height() <= 0:
                    return
                centre_y = self.identity.y() + band_rect.center().y()
            else:
                if self.height() <= 0:
                    return
                centre_y = (self.height() - 1) // 2
            if self.icon is not None and not self.icon.isHidden():
                icon_y = max(0, centre_y - (self.icon.height() - 1) // 2)
                if self.icon.y() != icon_y:
                    self.icon.move(self.icon.x(), icon_y)
            visible_chips = [c for c in self._chips if c.isVisible() and not c.isHidden()]
            if len(visible_chips) == 2:
                c0, c1 = visible_chips[0], visible_chips[1]
                c0_y = max(0, centre_y - (c0.height() - 1) // 2)
                c1_y = max(0, centre_y - (c1.height() - 1) // 2)
                if c0.y() != c0_y:
                    c0.move(c0.x(), c0_y)
                c1_x = c0.x() + c0.width()
                if c1.x() != c1_x or c1.y() != c1_y:
                    c1.move(c1_x, c1_y)
            else:
                for i in range(2, self.box.count()):
                    item = self.box.itemAt(i)
                    chip = item.widget() if item is not None else None
                    if chip is not None and not chip.isHidden():
                        chip_y = max(0, centre_y - (chip.height() - 1) // 2)
                        if chip.y() != chip_y:
                            chip.move(chip.x(), chip_y)
        finally:
            self._aligning = False

    def event(self, ev):  # noqa: N802 (Qt naming)
        res = super().event(ev)
        if ev.type() in (
            QEvent.Type.LayoutRequest,
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Paint,
        ):
            self._sync_to_band()
        return res

    def band_span(self) -> tuple[int, int]:
        """Vertical extent of title + band in this row's coordinates."""
        self._sync_to_band()
        top = self.identity.y() + self.title.geometry().top()
        if self.band is not None and not self.band.isHidden():
            bottom = self.identity.y() + self.band.geometry().bottom()
        else:
            bottom = self.identity.y() + self.title.geometry().bottom()
        return top, bottom


class _GlyphActions(QWidget):
    """Up to three round glyph buttons: a row when there is room, else a column.

    The row is right-aligned beside the copy. When the card is too narrow for
    that, the same buttons stack in the order they were given, the way a
    traffic light does. No button ever carries a word: the wording lives in the
    hover tip and the accessible name.
    """

    def __init__(self, buttons, parent=None):
        super().__init__(parent)
        self._buttons = list(buttons)
        self._box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(10)
        for button in self._buttons:
            self._box.addWidget(button, 0, Qt.AlignCenter)
            if hasattr(button, "installEventFilter"):
                button.installEventFilter(self)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._sync_chips_pairing()

    def eventFilter(self, watched, event):  # noqa: N802 (Qt naming)
        if event.type() in (
            QEvent.Type.Show,
            QEvent.Type.Hide,
            QEvent.Type.EnabledChange,
            QEvent.Type.Resize,
        ):
            self._sync_chips_pairing()
        return super().eventFilter(watched, event)

    def _sync_chips_pairing(self) -> None:
        visible = [
            b
            for b in self._buttons
            if hasattr(b, "set_segmented_pair") and not b.isHidden()
        ]
        if len(visible) == 2:
            self._box.setSpacing(0)
            b0, b1 = visible[0], visible[1]
            b0.set_segmented_pair(companion=b1, is_leading=True)
            b1.set_segmented_pair(companion=b0, is_leading=False)
        else:
            self._box.setSpacing(10)
            for b in self._buttons:
                if hasattr(b, "set_segmented_pair"):
                    b.set_segmented_pair(companion=None)

    def set_stacked(self, stacked: bool) -> None:
        """Column instead of row, for a card with no horizontal room left."""
        direction = (
            QBoxLayout.Direction.TopToBottom
            if stacked
            else QBoxLayout.Direction.LeftToRight
        )
        if self._box.direction() != direction:
            self._box.setDirection(direction)
            self.updateGeometry()


class _FlowRow(QWidget):
    """Status lines that share one row when the card is wide enough.

    The install step and its ETA were two fixed rows, each wrapping into its
    own column: a wide window kept them stacked. On one row they read as a
    single sentence and give the copy band its height back; a card too narrow
    for both stacks them again, exactly as before.
    """

    def __init__(self, first, second, parent=None):
        super().__init__(parent)
        self._labels = (first, second)
        self._box = QBoxLayout(QBoxLayout.Direction.TopToBottom, self)
        self._box.setContentsMargins(0, 0, 0, 0)
        self._box.setSpacing(4)
        for label in self._labels:
            self._box.addWidget(label, 0, Qt.AlignVCenter)
        self._forced_stacked = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

    def row_width(self) -> int:
        """Width both lines need side by side."""
        width = 0
        visible_labels = [l for l in self._labels if l.isVisible()]
        for label in visible_labels:
            text = label.text() or ""
            if text:
                width += label.fontMetrics().horizontalAdvance(text)
        spacing = self._box.spacing() * max(0, len(visible_labels) - 1)
        return width + spacing + 4

    def _horizontal_for(self, width: int) -> bool:
        if self._forced_stacked:
            return False
        return int(width) >= self.row_width()

    def set_forced_stacked(self, stacked: bool) -> None:
        """Keep the lines stacked whatever the width, while the copy needs to wrap.

        The stop sanity check borrows the step line for a full sentence, and
        that sentence must wrap into the card rather than run off one row.
        """
        stacked = bool(stacked)
        if stacked == self._forced_stacked:
            return
        self._forced_stacked = stacked
        self.set_horizontal(self._horizontal_for(self.width()))

    def set_horizontal(self, horizontal: bool) -> None:
        direction = (
            QBoxLayout.Direction.LeftToRight if horizontal else QBoxLayout.Direction.TopToBottom
        )
        if self._box.direction() == direction:
            return
        self._box.setDirection(direction)
        for label in self._labels:
            setter = getattr(label, "set_single_line", None)
            if callable(setter):
                setter(horizontal)
        self.updateGeometry()

    def refresh(self) -> None:
        """Re-decide the direction after a label's text changed."""
        t0 = (self._labels[0].text() or "").strip().lower()
        t1 = (self._labels[1].text() or "").strip().lower()
        if t0 and t1 and t0 == t1:
            self._labels[1].setVisible(False)
        elif self._labels[1].text().strip():
            self._labels[1].setVisible(True)
        self.set_horizontal(self._horizontal_for(self.width()))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        if self._horizontal_for(width):
            return max(18, self.fontMetrics().height() + 2)
        return sum(label.heightForWidth(width) for label in self._labels) + self._box.spacing()

    def sizeHint(self):  # noqa: N802 (Qt naming)
        w = self.width() if self.width() > 0 else INSTALL_TEXT_WIDTH
        if self._horizontal_for(w):
            return QSize(self.row_width(), max(18, self.fontMetrics().height() + 2))
        return QSize(w, self.heightForWidth(w))

    def minimumSizeHint(self):  # noqa: N802 (Qt naming)
        w = self.width() if self.width() > 0 else INSTALL_TEXT_WIDTH
        if self._horizontal_for(w):
            return QSize(0, max(18, self.fontMetrics().height() + 2))
        return QSize(0, self.heightForWidth(w))

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        self.set_horizontal(self._horizontal_for(self.width()))
        super().resizeEvent(event)


class _RoundNextButton(QPushButton):
    """One round glyph button: a platform symbol in a filled disc.

    The power-off step used to end with a wide "Continue" button on a row of its
    own, which read like a form to fill in. A single round chip reads as the next
    step of a software-update assistant, and the same chip carries a cancel or a
    back arrow.

    On macOS the chip *is* an AppKit control (Liquid Glass on macOS 26, the
    system circular bezel before it — see ``native_controls``), sitting in this
    widget's NSView and driven by ``clicked`` exactly like the painted disc. The
    paint below is then the fallback and what Windows/Linux get, where no
    native round bezel is exposed to Qt widgets.
    """

    def __init__(self, parent=None, *, symbol: str = "next", accent: bool = True):
        super().__init__("", parent)
        self._symbol = symbol
        # Neutral chips (cancel, back) are a muted disc: only the step that
        # moves the install forward wears the accent colour.
        self._accent = bool(accent)
        self.setObjectName("roundNextButton" if accent else "roundGlyphButton")
        self.setCursor(Qt.ArrowCursor)
        # The widget keeps a few pixels of room around the disc for its shadow.
        self.setFixedSize(ROUND_NEXT_BUTTON_PX, ROUND_NEXT_BUTTON_PX)
        self.setFlat(True)
        self.setAutoDefault(False)
        self.setDefault(False)
        self.setAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect, False)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._keyboard_focused = False
        TabFocusManager.ensure_installed()
        self._hovered = False
        self._pressed = False
        self._arrow = QPixmap()
        self._companion = None
        self._is_leading = False
        self._segmented_native = None
        # AppKit draws the chip on macOS; None until the widget has a window.
        self._native = None
        self._native_attempted = False
        if symbol == "cancel":
            self.setToolTip(tr("btn_cancel"))
            self.setAccessibleName(tr("btn_cancel"))
        elif symbol == "back":
            self.setToolTip(tr("btn_back"))
            self.setAccessibleName(tr("btn_back"))
        elif symbol == "next":
            self.setToolTip(tr("btn_next"))
            self.setAccessibleName(tr("btn_next"))
        elif symbol == "retry":
            self.setToolTip(tr("flash_btn_retry"))
            self.setAccessibleName(tr("flash_btn_retry"))
        elif symbol == "folder":
            self.setToolTip(tr("rockbox_step_select_player_folder"))
            self.setAccessibleName(tr("rockbox_step_select_player_folder"))

    def set_symbol(self, symbol: str) -> None:
        if self._symbol == symbol:
            return
        self._symbol = symbol
        self._arrow = QPixmap()
        self.rebuild_arrow()
        if self._segmented_native is not None:
            self._segmented_native._sync_content()
        elif self._companion is not None:
            comp_seg = getattr(self._companion, "_segmented_native", None)
            if comp_seg is not None:
                comp_seg._sync_content()
        self.update()

    def setText(self, text: str) -> None:  # noqa: N802 (Qt naming)
        super().setText(text)
        if text:
            self.setToolTip(text)
            self.setAccessibleName(text)

    def setToolTip(self, text: str) -> None:  # noqa: N802 (Qt naming)
        super().setToolTip(text)
        native = getattr(self, "_native", None)
        if native is not None:
            native.set_tooltip(text)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(ROUND_NEXT_BUTTON_PX, ROUND_NEXT_BUTTON_PX)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt naming)
        return QSize(ROUND_NEXT_BUTTON_PX, ROUND_NEXT_BUTTON_PX)

    def dismiss_keyboard_focus(self) -> None:
        self._keyboard_focused = False
        self.update()

    def focusInEvent(self, event):  # noqa: N802 (Qt naming)
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
            if reason == Qt.FocusReason.MouseFocusReason:
                self.clearFocus()
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event):  # noqa: N802 (Qt naming)
        self._keyboard_focused = False
        TabFocusManager.unregister_focused_widget(self)
        self.update()
        super().focusOutEvent(event)

    def _colors(self) -> tuple[QColor, str]:
        """Disc and the glyph's contrast colour, from the live theme."""
        from .dark import T, is_dark

        if is_dark():
            text = "#ffffff"
            if self._accent:
                disc = QColor(getattr(T(), "accent", "") or "#52b036")
                disc.setAlpha(_ACCENT_DISC_ALPHA)
            else:
                disc = QColor(getattr(T(), "bg_elev", "") or "#3a3f4b")
                disc.setAlpha(_NEUTRAL_DISC_ALPHA)
        elif self._accent:
            disc = QColor(getattr(T(), "accent", "") or "#52b036")
            disc.setAlpha(_ACCENT_DISC_ALPHA)
            lum = (0.299 * disc.red() + 0.587 * disc.green() + 0.114 * disc.blue()) / 255.0
            text = "#000000" if lum > 0.65 else "#ffffff"
        else:
            disc = QColor(getattr(T(), "bg_elev", "") or "#3a3f4b")
            disc.setAlpha(_NEUTRAL_DISC_ALPHA)
            dark = disc.lightness() < 140
            text = "#ffffff" if dark else "#111827"
        return disc, text

    def set_segmented_pair(self, companion, is_leading: bool = False) -> None:
        prev_companion = getattr(self, "_companion", None)
        prev_leading = getattr(self, "_is_leading", False)
        if prev_companion is companion and prev_leading == bool(is_leading):
            return
        self._companion = companion
        self._is_leading = bool(is_leading)

        if self._segmented_native is not None:
            self._segmented_native.detach()
            self._segmented_native = None

        if self._companion is not None:
            # We are entering segmented mode: detach single round chips immediately so no stray button appears
            if self._native is not None:
                self._native.detach()
                self._native = None
                self._native_attempted = False
            comp_native = getattr(self._companion, "_native", None)
            if comp_native is not None:
                comp_native.detach()
                self._companion._native = None
                self._companion._native_attempted = False

            if self._is_leading:
                self._attach_segmented_native()
        else:
            if self._native is None and native_round_buttons_available():
                self._native_attempted = False
                self._attach_native()
            elif self._native is not None and getattr(self._native, "_button", None) is not None:
                self._native._button.setHidden_(not self.isVisible())

        self.rebuild_arrow()
        self.update()

    def _attach_segmented_native(self) -> None:
        if not native_round_buttons_available() or self._companion is None or not self._is_leading:
            return
        win = self.window()
        if win is None or not win.isVisible():
            return
        from .native_controls import NativeSegmentedPair
        if self._segmented_native is None:
            # Detach any standalone chips from both buttons before attaching the pair
            if self._native is not None:
                self._native.detach()
                self._native = None
                self._native_attempted = False
            comp_native = getattr(self._companion, "_native", None)
            if comp_native is not None:
                comp_native.detach()
                self._companion._native = None
                self._companion._native_attempted = False

            pair = NativeSegmentedPair(self, self._companion)
            if pair.attach():
                self._segmented_native = pair
            else:
                self._segmented_native = None

    def is_segmented_active(self) -> bool:
        if self._segmented_native is not None and self._segmented_native.live:
            return True
        if self._companion is not None:
            comp_seg = getattr(self._companion, "_segmented_native", None)
            if comp_seg is not None and comp_seg.live:
                return True
        return False

    def rebuild_arrow(self) -> None:
        """Re-render the platform glyph for the current theme colours.

        In a right-to-left language the row mirrors — the back chip sits at
        the row's right edge — but a glyph does not mirror itself, so the
        left-pointing back arrow would aim inwards at the next chip (both
        arrows pointing at each other, actions reading the wrong way round).
        Mirror the *directional* glyphs there so each points away from the
        other, the way the row reads; the label, tooltip and accessible name
        keep their real meaning.
        """
        _disc, text = self._colors()
        symbol = self._glyph_symbol()
        # "cancel" is a real symbol in every platform map (SF xmark, Segoe
        # ChromeClose, the SVG cross); "close" is not, and drew a stray glyph.
        self._arrow = get_symbol_pixmap(symbol, _ROUND_NEXT_ARROW_PX, text)
        if self._arrow.isNull() and self._symbol == "cancel":
            self._arrow = _explicit_cancel_cross(_ROUND_NEXT_ARROW_PX, text)
        if self._native is not None:
            self._native.set_symbol(symbol)
            disc, glyph = self._native_colors()
            self._native.set_style(accent=self._accent, disc=disc, content=glyph)
        self.update()

    def _glyph_symbol(self) -> str:
        """The glyph to draw, mirrored in a right-to-left row."""
        symbol = self._symbol
        if self._companion is not None:
            if symbol == "back":
                symbol = "chevron.left"
            elif symbol == "next":
                symbol = "chevron.right"
        if (
            symbol in ("back", "next", "chevron.left", "chevron.right")
            and self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        ):
            if symbol in ("back", "chevron.left"):
                symbol = "chevron.right" if self._companion is not None else "next"
            else:
                symbol = "chevron.left" if self._companion is not None else "back"
        return symbol

    def refresh_theme(self) -> None:
        """Update colours and re-render glyph when host theme or accent changes."""
        self._arrow = QPixmap()
        self.rebuild_arrow()
        if self._segmented_native is not None:
            self._segmented_native._sync_content()
        elif self._companion is not None:
            comp_seg = getattr(self._companion, "_segmented_native", None)
            if comp_seg is not None:
                comp_seg._sync_content()
        self.update()

    def changeEvent(self, event):  # noqa: N802 (Qt naming)
        if event.type() == QEvent.Type.LayoutDirectionChange:
            # The row itself mirrors when the language changes; the glyph has
            # to be re-rendered with it, or the two arrows stay aimed at each
            # other because nothing told them the row flipped.
            self.rebuild_arrow()
        elif event.type() in (QEvent.Type.PaletteChange, QEvent.Type.ApplicationPaletteChange):
            self.refresh_theme()
        elif event.type() == QEvent.Type.EnabledChange:
            if self._native is not None:
                self._native.set_enabled(self.isEnabled())
            if self._segmented_native is not None:
                self._segmented_native._sync_content()
            elif self._companion is not None and getattr(self._companion, "_segmented_native", None) is not None:
                self._companion._segmented_native._sync_content()
            self.update()
        super().changeEvent(event)

    def _native_colors(self) -> tuple[QColor, QColor]:
        """The fill AppKit should bezel with, and the glyph colour.

        The painted disc is translucent so the card's material shows through
        it; a filled AppKit bezel is opaque, so it gets the same colour with
        alpha cleared rather than a grey wash.
        """
        disc, text = self._colors()
        if self._accent:
            disc.setAlpha(255)
        return disc, QColor(text)

    def showEvent(self, event):  # noqa: N802 (Qt naming)
        super().showEvent(event)
        if self._companion is not None and self._is_leading:
            self._attach_segmented_native()
        elif self._companion is None:
            self._attach_native()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        if self._native is not None:
            self._native.sync_frame()

    def _attach_native(self) -> None:
        """Hand the chip to AppKit once the widget has its own NSView."""
        if self._companion is not None or self.is_segmented_active():
            return
        if not native_round_buttons_available():
            return
        if self._native is not None and self._native.live:
            return
        win = self.window()
        if win is None or not win.isVisible():
            return
        if self._native_attempted:
            return
        chip = NativeRoundChip(self)
        if not chip.attach():
            if getattr(chip, "_failed", False):
                self._native_attempted = True
            self._native = None
            return
        self._native_attempted = True
        chip.set_symbol(self._glyph_symbol())
        disc, glyph = self._native_colors()
        chip.set_style(accent=self._accent, disc=disc, content=glyph)
        chip.set_enabled(self.isEnabled())
        chip.set_tooltip(self.toolTip())
        self._native = chip
        self.update()

    def _native_chip_clicked(self) -> None:
        """A real AppKit press, delivered as the button's Qt signal."""
        self._keyboard_focused = False
        self._hovered = False
        self._pressed = False
        if self.isEnabled():
            self.click()


    def _disc_rect(self) -> QRect:
        """The disc itself: the widget minus the room its shadow needs."""
        return self.rect().adjusted(
            _CHIP_SHADOW_PX, _CHIP_SHADOW_PX, -_CHIP_SHADOW_PX, -_CHIP_SHADOW_PX
        )

    def _shadow_strength(self) -> float:
        """How heavy the seat of the shadow is right now."""
        if not self.isEnabled():
            return 0.4
        if self._pressed:
            return 0.55
        if self._hovered:
            return 1.2
        return 1.0

    def _paint_shadow(self, painter: QPainter, disc: QRect) -> None:
        """Soft shade under the disc, the lightest ring outermost.

        Concentric ellipses, each drawn a little larger than the last; the disc
        is painted over them, so what is left is the soft edge around it that
        reads as the chip sitting above the card. Light comes from above, so the
        rings grow a pixel more at the bottom than at the top.
        """
        strength = self._shadow_strength()
        steps = _CHIP_SHADOW_PX
        budget = max(1.0, steps * steps)
        from .glass import is_liquid_glass_supported

        is_legacy = sys.platform == "darwin" and not is_liquid_glass_supported()
        for step in range(steps, 0, -1):
            alpha = int(_CHIP_SHADOW_ALPHA * strength * (steps - step + 1) / budget)
            if alpha <= 0:
                continue
            painter.setBrush(QColor(0, 0, 0, min(alpha, 255)))
            r = disc.adjusted(-step, -step + 1, step, step + 1)
            if is_legacy:
                painter.drawRoundedRect(r, 5.0, 5.0)
            else:
                painter.drawEllipse(r)

    def paint_into(self, painter: QPainter) -> None:
        """Draw the chip — shadow, disc, glyph — into ``painter``.

        ``paintEvent`` is only a wrapper around this, so a test can paint the
        chip onto a canvas of its own and read the pixels. ``QWidget.render()``
        would not do: it fills an opaque background first, which hides both the
        translucency and the shadow.
        """
        if self._arrow.isNull():
            self.rebuild_arrow()
        disc, _text = self._colors()
        if not self.isEnabled():
            disc.setAlpha(min(disc.alpha(), 110))
        elif self._pressed:
            disc = disc.darker(118)
        elif self._hovered:
            disc = disc.lighter(112)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        self._paint_shadow(painter, self._disc_rect())
        disc_rect = self._disc_rect()
        painter.setBrush(disc)
        from .glass import is_liquid_glass_supported

        is_legacy = sys.platform == "darwin" and not is_liquid_glass_supported()
        if is_legacy:
            painter.drawRoundedRect(disc_rect, 5.0, 5.0)
        else:
            painter.drawEllipse(disc_rect)
        # A raised chip catches the light along its top edge.
        highlight = disc.lighter(150)
        highlight.setAlpha(min(110, max(35, int(disc.alpha() * 0.45))))
        painter.setPen(QPen(highlight, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(disc_rect.adjusted(1, 1, -1, -1), 45 * 16, 90 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        if not self._arrow.isNull():
            size = _ROUND_NEXT_ARROW_PX
            painter.drawPixmap(
                QRect(
                    disc_rect.x() + (disc_rect.width() - size) // 2,
                    disc_rect.y() + (disc_rect.height() - size) // 2,
                    size,
                    size,
                ),
                self._arrow,
                self._arrow.rect(),
            )
        if self.hasFocus() and self._keyboard_focused and TabFocusManager.is_tab_active():
            from .dark import get_native_accent_color

            accent_col, _ = get_native_accent_color()
            focus_col = QColor(accent_col or "#007aff")
            focus_pen = QPen(focus_col, 2)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(disc_rect.adjusted(1, 1, -1, -1))

    def _paint_segmented_into(self, painter: QPainter) -> None:
        """Draw this segment half of a paired capsule into painter."""
        if self._arrow.isNull():
            self.rebuild_arrow()
        from .dark import is_dark
        disc, _text = self._colors()
        if not self.isEnabled():
            disc.setAlpha(min(disc.alpha(), 110))
        elif self._pressed:
            disc = disc.darker(118)
        elif self._hovered:
            disc = disc.lighter(112)

        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)

        disc_rect = self._disc_rect()
        use_regular = sys.platform == "darwin"
        h = float(disc_rect.height())
        y = float(disc_rect.y())
        w = float(self.width())
        x0 = float(disc_rect.x())

        from PySide6.QtGui import QPainterPath
        path = QPainterPath()
        if not use_regular:
            r = h / 2.0
            if self._is_leading:
                path.moveTo(w, y)
                path.lineTo(x0 + r, y)
                path.arcTo(x0, y, h, h, 90, 180)
                path.lineTo(w, y + h)
                path.closeSubpath()
                center_x = x0 + (w - x0) / 2.0
            else:
                right_x = float(disc_rect.right() + 1)
                path.moveTo(0, y + h)
                path.lineTo(right_x - r, y + h)
                path.arcTo(right_x - h, y, h, h, 270, 180)
                path.lineTo(0, y)
                path.closeSubpath()
                center_x = right_x / 2.0
        else:
            r = 5.0
            if self._is_leading:
                path.moveTo(w, y)
                path.lineTo(x0 + r, y)
                path.arcTo(x0, y, r * 2, r * 2, 90, 90)
                path.lineTo(x0, y + h - r)
                path.arcTo(x0, y + h - r * 2, r * 2, r * 2, 180, 90)
                path.lineTo(w, y + h)
                path.closeSubpath()
                center_x = x0 + (w - x0) / 2.0
            else:
                right_x = float(disc_rect.right() + 1)
                path.moveTo(0, y + h)
                path.lineTo(right_x - r, y + h)
                path.arcTo(right_x - r * 2, y + h - r * 2, r * 2, r * 2, 270, 90)
                path.lineTo(right_x, y + r)
                path.arcTo(right_x - r * 2, y, r * 2, r * 2, 0, 90)
                path.lineTo(0, y)
                path.closeSubpath()
                center_x = right_x / 2.0

        painter.setBrush(disc)
        painter.drawPath(path)

        # Subtle highlight along the top arc
        highlight = disc.lighter(150)
        highlight.setAlpha(min(110, max(35, int(disc.alpha() * 0.45))))
        painter.setPen(QPen(highlight, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._is_leading:
            painter.drawArc(disc_rect.adjusted(1, 1, -1, -1), 90 * 16, 90 * 16)
            # Divider between the segments
            div_col = QColor(255, 255, 255, 38) if is_dark() else QColor(0, 0, 0, 38)
            painter.setPen(QPen(div_col, 1))
            painter.drawLine(int(w) - 1, int(y) + 4, int(w) - 1, int(y + h) - 4)
        else:
            painter.drawArc(
                QRect(int(disc_rect.right() + 1 - h), int(y), int(h), int(h)).adjusted(1, 1, -1, -1),
                0,
                90 * 16,
            )

        painter.setPen(Qt.PenStyle.NoPen)
        if not self._arrow.isNull():
            size = _ROUND_NEXT_ARROW_PX
            center_y = y + h / 2.0
            if not self.isEnabled():
                painter.setOpacity(0.4)
            painter.drawPixmap(
                int(round(center_x - size / 2.0)),
                int(round(center_y - size / 2.0)),
                self._arrow,
            )
            if not self.isEnabled():
                painter.setOpacity(1.0)

        if self.hasFocus() and self._keyboard_focused and TabFocusManager.is_tab_active():
            from .dark import get_native_accent_color

            accent_col, _ = get_native_accent_color()
            focus_col = QColor(accent_col or "#007aff")
            focus_pen = QPen(focus_col, 2)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        # AppKit owns the chip's look when it is live; Qt keeps the slot.
        if self.is_segmented_active():
            if self._native is not None:
                self._native.detach()
                self._native = None
                self._native_attempted = False
            return
        if self._companion is not None:
            if self._native is not None:
                self._native.detach()
                self._native = None
                self._native_attempted = False
        if self._native is not None and self._native.live and self._companion is None:
            return
        if self._native is None and not self._native_attempted and native_round_buttons_available():
            win = self.window()
            if win is not None and win.isVisible():
                if self._companion is not None and self._is_leading:
                    self._attach_segmented_native()
                elif self._companion is None:
                    self._attach_native()
                if self.is_segmented_active() or (self._native is not None and self._native.live and self._companion is None):
                    return
        painter = QPainter(self)
        try:
            if self._companion is not None:
                self._paint_segmented_into(painter)
            else:
                self.paint_into(painter)
        finally:
            painter.end()

    def enterEvent(self, event):  # noqa: N802 (Qt naming)
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802 (Qt naming)
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802 (Qt naming)
        self._keyboard_focused = False
        TabFocusManager.dismiss_all()
        self.clearFocus()
        self._pressed = True
        self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt naming)
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)




def _explicit_cancel_cross(size: int, color: str) -> QPixmap:
    """A standalone cross for the cancel chip when the platform glyph is missing or too subtle.

    The cancel chip is a muted neutral disc, so platform cancel pictograms (a
    square stop button, a tiny X in a capsule, etc.) can read as almost
    nothing at that small size. This draws a simple + over a filled disc so the
    cancel affordance is visible on every host, and it is only used when the
    normal ``get_symbol_pixmap("close")`` path returned an empty pixmap.
    """
    disc_size = max(int(size), 1) * 2
    pm = QPixmap(disc_size, disc_size)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    center = disc_size // 2
    half = max(2, disc_size // 5)
    painter.setPen(pen)
    painter.drawLine(center - half, center - half, center + half, center + half)
    painter.drawLine(center + half, center - half, center - half, center + half)
    painter.end()
    out = pm.scaled(size * 2, size * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    out.setDevicePixelRatio(2.0)
    return out


def _configure_progress(bar: QProgressBar) -> None:
    """A platform progress bar. No stylesheet, so Aqua / WinUI / Breeze paint it.

    The percent is visible in the native groove. A fixed 6px stylesheet bar
    was what disappeared on acrylic and never moved on screen.
    """
    from .dark import install_progress_readability

    bar.setObjectName("softwareUpdateProgress")
    install_progress_readability(bar)
    bar.setStyleSheet("background: transparent; border: none; padding: 0px;")
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(True)
    bar.setFormat("%p%")
    bar.setMinimumHeight(18)
    bar.setMaximumHeight(24)
    bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


def _make_squircle_icon(symbol: str = "", asset_name: str = "") -> QLabel:
    """Accent settings squircle. A release image replaces it when one loads."""
    del symbol, asset_name
    lbl = QLabel()
    lbl.setFixedSize(40, 40)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setAutoFillBackground(False)
    lbl.setStyleSheet("")
    from .release_icon import squircle_pixmap

    lbl.setPixmap(squircle_pixmap(QPixmap(), 40, complete=False))
    return lbl


class _SvgVectorWidget(QWidget):
    """Native vector renderer for SVG illustrations.

    Renders directly via QSvgRenderer during paintEvent, ensuring
    razor-sharp antialiasing at the host screen's native resolution without
    any bitmap scaling or pixel manipulation.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtSvg import QSvgRenderer

        self._renderer = QSvgRenderer(self)
        self._svg_raw = ""
        self._color = QColor("#ffffff")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent; border: none;")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def load_svg(self, path: Path | str, color: QColor | str) -> bool:
        try:
            self._svg_raw = Path(path).read_text(encoding="utf-8")
        except Exception:
            self._svg_raw = ""
            return False
        return self.set_color(color)

    def set_color(self, color: QColor | str) -> bool:
        self._color = QColor(color) if isinstance(color, str) else color
        if not self._svg_raw or not self._color.isValid():
            return False
        from .icons import vector_recolor_svg_data

        recolored_svg = vector_recolor_svg_data(self._svg_raw, self._color)
        from PySide6.QtCore import QByteArray

        ok = self._renderer.load(QByteArray(recolored_svg.encode("utf-8")))
        if ok and self._renderer.isValid():
            box = self._renderer.viewBoxF()
            if box.height() > 0:
                h = _PAPERCLIP_HINT_PX
                w = max(1, int(round(h * box.width() / box.height())))
                self.setFixedSize(w, h)
        self.update()
        return ok

    def paintEvent(self, event):  # noqa: N802 (Qt naming)
        if not self._renderer.isValid():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        from PySide6.QtCore import QRectF

        self._renderer.render(painter, QRectF(self.rect()))
        painter.end()

    def pixmap(self):
        if not self._svg_raw or not self._renderer.isValid():
            return QPixmap()
        dpr = max(2.0, float(self.devicePixelRatioF() or 1.0))
        box = self._renderer.viewBoxF()
        h = self.height() if self.height() > 0 else _PAPERCLIP_HINT_PX
        w = max(1, int(round(h * box.width() / box.height()))) if box.height() > 0 else 100
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QRectF

        img = QImage(
            int(round(w * dpr)),
            int(round(h * dpr)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        self._renderer.render(p, QRectF(0, 0, img.width(), img.height()))
        p.end()
        img.setDevicePixelRatio(dpr)
        return QPixmap.fromImage(img)

    def setPixmap(self, pm):
        pass


class FlashPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_callback = None
        self._cancel_wait_callback = None
        self._cancel_download_callback = None
        self._model = ""
        self._package_name = ""
        self._wait_banner_key = ""
        self._step_key = ""
        self._prep_step_key = ""
        self._conn_value_key = ""
        self._dev_value_key = ""
        # Install method is chosen in Settings; this page only reports it.
        self._method = default_flash_method()
        # 0 = no step emphasised. Set by highlight_guide_step() when the
        # backend tells us the device is not in flash mode yet.
        self._guide_step = 0
        self._failed = False
        self._complete = False
        self._prompt_only = False
        self._wait_copy = ""
        # True while the card is asking the user to bring a player that just
        # dropped out of the run back into flash mode (a USB disconnect, or the
        # automated switch away from a failed install method). It selects the
        # recovery wording instead of the plain power-off line.
        self._power_off_recovery = False
        # Which recovery wording, and the fields it is formatted with: a tool
        # that never reached the player gets its own line (naming the engine
        # taking over) rather than the "got disconnected" one.
        self._power_off_recovery_key = ""
        self._power_off_recovery_kwargs = {}
        self._install_retry_cb = None
        self._power_off_continue_cb = None
        self._build_ui()

    def _build_ui(self):
        self._headings = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        self._stack = CurrentPageStack()
        self._preparing_view = self._build_preparing_view()
        self._waiting_view = self._build_waiting_view()
        self._flashing_view = self._build_flashing_view()
        self._downloading_view = self._build_downloading_view()
        for w in (self._preparing_view, self._waiting_view, self._flashing_view, self._downloading_view):
            self._stack.addWidget(w)
        layout.addWidget(self._stack, 1)
        self._seal_status_labels()

    def _wrap_centered(
        self,
        card_widget: QWidget,
        extra_below: QWidget | None = None,
        *,
        heading_key: str = "flash_install_in_progress",
    ) -> QWidget:
        """Fill the content width with the progress card.

        Every progress screen — preparing, waiting, installing, downloading —
        comes through here: one width, one set of margins and one gap under the
        card, so the screens do not shift their padding as the app moves
        between them.
        """
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(8)

        heading = QLabel(tr(heading_key))
        _style_card_heading(heading)
        self._headings.append((heading, heading_key))
        # The window header already says "Install in Progress" / "Install
        # Complete". This in-card copy is kept for tests but not shown.
        heading.hide()

        card_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        v.addWidget(card_widget)
        if extra_below is not None:
            extra_below.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            v.addWidget(extra_below)
        v.addStretch(1)
        return container

    def _new_progress_card(self, icon, title, band, chips):
        """The one skeleton the four progress cards are built from.

        Returns ``(card, head, copy)``. ``head`` carries the icon, the software
        title, the progress band and the round chips on a single row; ``copy``
        is where that screen's own words go — below the row, indented to line up
        with the title, so the icon stays the card's leading edge. Building all
        four here is what keeps them the same card on every host: Aqua, WinUI,
        Breeze and Adwaita paint the controls, this decides their places.
        """
        card = Card()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        body = QVBoxLayout()
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(0)
        head = _CardHead(icon, title)
        head.set_band(band)
        for chip in chips:
            head.add_chip(chip)
        body.addWidget(head)
        copy = QVBoxLayout()
        copy.setContentsMargins(_CARD_COPY_INDENT, 0, 0, 0)
        copy.setSpacing(6)
        body.addLayout(copy)
        card.set_layout(body)
        return card, head, copy

    def _build_preparing_view(self):
        self._prep_icon = _make_squircle_icon("📦", "icon.png")
        self._prep_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        self._prep_banner = Banner()
        self._prep_banner.setVisible(False)
        self._prep_progress = InstallProgress()
        _configure_progress(self._prep_progress)

        self._prep_cancel_btn = _RoundNextButton(symbol="back", accent=False)
        self._prep_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._prep_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._prep_cancel_btn.clicked.connect(self._on_cancel_wait)

        self._prep_next_btn = _RoundNextButton(symbol="next", accent=True)
        self._prep_next_btn.setObjectName("prepNextPlaceholderBtn")
        self._prep_next_btn.setEnabled(False)
        self._prep_next_btn.setVisible(True)
        chips = [self._prep_cancel_btn, self._prep_next_btn]

        self._prep_card, self._prep_head, copy = self._new_progress_card(
            self._prep_icon,
            self._prep_pkg_label,
            self._prep_progress,
            chips,
        )
        self._prep_row = self._prep_head.box

        self._prep_step = _status_label(tr("step_extract"))
        copy.addWidget(self._prep_step)
        self._prep_col = copy

        # Guidance images are removed; keep dummy attribute for backward compatibility
        self._prep_img = QLabel()
        self._prep_img.setVisible(False)

        return self._wrap_centered(self._prep_card)

    def _build_waiting_view(self):
        self._wait_icon = _make_squircle_icon("🔌", "icon.png")
        self._wait_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        self._wait_progress_bar = InstallProgress()
        _configure_progress(self._wait_progress_bar)

        # The next step is one round arrow on the card's head row, not a wide
        # button on a row of its own.
        self._wait_continue_btn = _RoundNextButton()
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(False)
        self._wait_continue_btn.setToolTip(tr("btn_next"))
        self._wait_continue_btn.setAccessibleName(tr("btn_next"))
        self._wait_continue_btn.clicked.connect(self._on_power_off_continue)

        self._wait_cancel_btn = _RoundNextButton(symbol="back", accent=False)
        self._wait_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._wait_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._wait_cancel_btn.clicked.connect(self._on_cancel_wait)

        self._wait_card, self._wait_head, copy = self._new_progress_card(
            self._wait_icon,
            self._wait_pkg_label,
            self._wait_progress_bar,
            [self._wait_cancel_btn, self._wait_continue_btn],
        )
        self._wait_row = self._wait_head.box

        self._wait_prompt_label = _status_label(
            tr("flash_connect_device_prompt").format(model=self._connect_model_text())
        )
        self._wait_prompt_label.setWordWrap(True)
        copy.addWidget(self._wait_prompt_label)
        self._wait_col = copy

        # Retained for attribute compatibility, but kept hidden and not added to layout
        self._attempt_lbl = QLabel("")
        self._attempt_lbl.setVisible(False)
        self._attempt_remaining = 0

        self._wait_mode_hint = QLabel("")
        self._wait_mode_hint.setVisible(False)

        self._wait_banner = Banner()
        self._wait_banner.setVisible(False)
        self._wait_status = StatusTag("idle")
        self._wait_status.setVisible(False)

        # SP Flash Tool GUI is a sidebar action. This page does not grow a second launcher.

        # Guidance images are removed; keep dummy attributes for backward compatibility
        self._status_img = QLabel()
        self._status_img.setVisible(False)
        self._guide_title = QLabel(tr("flash_guide_title"))
        self._guide_texts = []
        for key in ("flash_guide_1", "flash_guide_2", "flash_guide_3", "flash_guide_4"):
            text = QLabel(tr(key))
            text.setWordWrap(True)
            self._guide_texts.append((key, text))

        # Paperclip hint with diagram image and model-specific wording
        self._paperclip_hint = _status_label("")
        hint_font = self._paperclip_hint.font()
        hint_font.setPixelSize(12)
        self._paperclip_hint.setFont(hint_font)
        self._paperclip_hint.set_column_width(196)
        self._paperclip_hint.setWordWrap(True)
        self._paperclip_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._paperclip_img = _SvgVectorWidget()
        self._paperclip_img.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._paperclip_hint_host = QWidget()
        hint_layout = QHBoxLayout(self._paperclip_hint_host)
        hint_layout.setContentsMargins(0, 10, 0, 8)
        hint_layout.setSpacing(14)
        hint_layout.addWidget(self._paperclip_img, 0, Qt.AlignmentFlag.AlignTop)
        hint_layout.addWidget(self._paperclip_hint, 1, Qt.AlignmentFlag.AlignTop)
        self._paperclip_hint_host.setVisible(False)
        copy.addWidget(self._paperclip_hint_host)
        self._update_paperclip_hint()

        return self._wrap_centered(self._wait_card)

    def _build_flashing_view(self):
        self._flash_icon = _make_squircle_icon("⚙️", "icon.png")
        self._flash_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        self._flash_banner = Banner()
        self._flash_banner.setVisible(False)
        self._progress_bar = InstallProgress()
        _configure_progress(self._progress_bar)

        # Action chips stay on the head row — right of the title and the progress
        # band — while this card is running, has failed, or has finished. A chip
        # in the middle of a taller card reads as belonging to the guidance copy
        # under it instead of to the software it is installing.
        self._flash_retry_btn = _RoundNextButton(symbol="retry", accent=True)
        self._flash_retry_btn.setObjectName("flashRetryBtn")
        self._flash_retry_btn.setToolTip(tr("flash_btn_retry"))
        self._flash_retry_btn.setAccessibleName(tr("flash_btn_retry"))
        self._flash_retry_btn.setVisible(True)
        self._flash_retry_btn.setEnabled(False)
        self._flash_retry_btn.clicked.connect(self._on_install_retry)

        self._cancel_btn = _RoundNextButton(symbol="back", accent=False)
        self._cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._cancel_btn.clicked.connect(self._on_cancel)

        self._done_mark = _RoundNextButton(symbol="complete", accent=True)
        self._done_mark.setObjectName("installDoneButton")
        self._done_mark.setCursor(Qt.ArrowCursor)
        self._style_done_button()
        self._done_mark.setVisible(False)
        self._done_mark.clicked.connect(self._on_install_done)
        self._install_done_cb = None

        # Round wizard navigation chips for the Rockbox post-install steps.
        self._theme_step1_next_btn = _RoundNextButton(symbol="next", accent=True)
        self._theme_step1_next_btn.setText(tr("dialog_pre_install_continue"))
        self._theme_step1_next_btn.setVisible(False)
        self._theme_step1_next_btn.clicked.connect(self._on_theme_step1_next_clicked)

        self._theme_fallback_next_btn = _RoundNextButton(symbol="folder", accent=True)
        self._theme_fallback_next_btn.setText(tr("rockbox_step_select_player_folder"))
        self._theme_fallback_next_btn.setToolTip(tr("rockbox_step_select_player_folder"))
        self._theme_fallback_next_btn.setVisible(False)
        self._theme_fallback_next_btn.clicked.connect(self._on_theme_fallback_next_clicked)

        self._theme_trouble_recover_btn = _RoundNextButton(symbol="retry", accent=True)
        self._theme_trouble_recover_btn.setText(tr("rockbox_recover_troubleshoot"))
        self._theme_trouble_recover_btn.setVisible(False)
        self._theme_trouble_recover_btn.clicked.connect(self._on_theme_recover_clicked)

        self._theme_back_btn = _RoundNextButton(symbol="back", accent=False)
        self._theme_back_btn.setText(tr("rockbox_wizard_back"))
        self._theme_back_btn.setVisible(False)
        self._theme_back_btn.clicked.connect(self._show_theme_fallback_usb_mode)

        self._progress_card, self._flash_head, copy = self._new_progress_card(
            self._flash_icon,
            self._flash_pkg_label,
            self._progress_bar,
            [
                self._cancel_btn,
                self._flash_retry_btn,
                self._done_mark,
                self._theme_back_btn,
                self._theme_step1_next_btn,
                self._theme_fallback_next_btn,
                self._theme_trouble_recover_btn,
            ],
        )
        # The chips' row, for the stop check to slot in beside them.
        self._actions_row = self._flash_row = self._flash_head.box

        self._step_label = _status_label(tr("step_write"))
        self._eta_label = _status_label("")
        self._partition_progress_label = _status_label("")
        self._partition_progress_label.setObjectName("partitionWriteProgress")
        self._partition_progress_label.setWordWrap(True)
        self._partition_progress_label.setVisible(False)
        # Step and ETA share one row on a wide card, stack on a narrow one.
        self._status_flow = _FlowRow(self._step_label, self._eta_label)
        copy.addWidget(self._status_flow)
        copy.addWidget(self._partition_progress_label)

        self._theme_row = QWidget()
        theme_vbox = QVBoxLayout(self._theme_row)
        theme_vbox.setContentsMargins(0, 4, 0, 0)
        theme_vbox.setSpacing(6)

        # Step 1: Disconnect and power on -> Next chip on the head row
        self._theme_step1_widget = QWidget()
        self._theme_step1_widget.setFixedHeight(0)
        step1_box = QHBoxLayout(self._theme_step1_widget)
        step1_box.setContentsMargins(0, 0, 0, 0)
        step1_box.setSpacing(0)
        theme_vbox.addWidget(self._theme_step1_widget)

        # Fallback: USB storage mode -> Next chip on the head row + recover link below
        self._theme_fallback_widget = QWidget()
        fallback_box = QHBoxLayout(self._theme_fallback_widget)
        fallback_box.setContentsMargins(0, 0, 0, 0)
        fallback_box.setSpacing(10)

        self._theme_recover_link = QLabel(f'<a href="recover">{tr("rockbox_recover_troubleshoot")}</a>')
        self._theme_recover_link.setTextFormat(Qt.TextFormat.RichText)
        self._theme_recover_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self._theme_recover_link.setOpenExternalLinks(False)
        self._theme_recover_link.linkActivated.connect(self._on_theme_recover_clicked)
        fallback_box.addWidget(self._theme_recover_link)
        fallback_box.addStretch(1)
        theme_vbox.addWidget(self._theme_fallback_widget)

        # Backward compatibility aliases
        self._theme_ask_widget = self._theme_step1_widget
        self._theme_yes_btn = self._theme_step1_next_btn
        self._theme_manual_widget = self._theme_fallback_widget
        self._theme_browse_btn = self._theme_fallback_next_btn

        # Trouble recovery section -> chips on the head row
        self._theme_trouble_widget = QWidget()
        self._theme_trouble_widget.setFixedHeight(0)
        trouble_box = QHBoxLayout(self._theme_trouble_widget)
        trouble_box.setContentsMargins(0, 0, 0, 0)
        trouble_box.setSpacing(0)
        theme_vbox.addWidget(self._theme_trouble_widget)

        self._rockbox_site_widget = QWidget()
        self._rockbox_site_widget.setVisible(False)
        self._rockbox_site_btn = QPushButton(tr("rockbox_site_themes_open"))
        self._rockbox_site_btn.setVisible(False)

        self._theme_row.setVisible(False)
        copy.addWidget(self._theme_row)

        self._driver_action_row = QWidget()
        driver_box = QHBoxLayout(self._driver_action_row)
        driver_box.setContentsMargins(0, 6, 0, 2)
        driver_box.setSpacing(8)
        self._download_drivers_btn = QPushButton(tr("settings_download_drivers_btn"))
        self._download_drivers_btn.setObjectName("flashDownloadDriversBtn")
        self._download_drivers_btn.setCursor(Qt.CursorShape.ArrowCursor)
        self._download_drivers_btn.clicked.connect(self._open_mediatek_drivers)
        driver_box.addWidget(self._download_drivers_btn)
        driver_box.addStretch(1)
        self._driver_action_row.setVisible(False)
        copy.addWidget(self._driver_action_row)
        self._flash_col = copy

        # Warning below the update card
        warning_container = QWidget()
        w_layout = QVBoxLayout(warning_container)
        w_layout.setContentsMargins(0, 0, 0, 0)

        self._warning = QLabel(tr("flash_warning"))
        self._warning.setWordWrap(True)
        self._warning.setAlignment(Qt.AlignCenter)
        warn_font = self._warning.font()
        warn_font.setPixelSize(13)
        self._warning.setFont(warn_font)
        _apply_card_text(self._warning)
        w_layout.addWidget(self._warning)

        # Completion explanation for readers who turned the donations appeal
        # off: the appeal's headline/subtitle usually says what was installed,
        # so this note only appears when that block is hidden.
        self._complete_note = QLabel()
        self._complete_note.setWordWrap(True)
        self._complete_note.setAlignment(Qt.AlignCenter)
        self._complete_note.setProperty("cssClass", "hint")
        self._complete_note.setVisible(False)
        self._complete_note_software = ""
        self._complete_note_model = ""
        w_layout.addWidget(self._complete_note)

        self._appeal = QWidget()
        self._appeal.setAutoFillBackground(False)
        self._appeal.setStyleSheet("")
        self._appeal.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        appeal_layout = QVBoxLayout(self._appeal)
        appeal_layout.setContentsMargins(8, 8, 8, 0)
        appeal_layout.setSpacing(10)
        self._intro = SupportIntro()
        self._appeal_label = self._intro.headline
        appeal_layout.addWidget(self._intro)
        # One plain sentence under the headline: what the app is, and what a
        # coffee actually pays for. The appeal's own words, not the Support
        # page's project pitch.
        self._appeal_body = QLabel()
        self._appeal_body.setWordWrap(True)
        self._appeal_body.setProperty("cssClass", "dimmed")
        self._appeal_body.setAutoFillBackground(False)
        self._appeal_body.setStyleSheet("")
        _apply_card_text(self._appeal_body)
        appeal_layout.addWidget(self._appeal_body)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._coffee_btn = QPushButton(tr("donate_completion_coffee"))
        self._coffee_btn.setCursor(Qt.ArrowCursor)
        self._coffee_btn.setStyleSheet("")
        self._coffee_btn.clicked.connect(self._open_coffee_page)
        self._not_now_btn = QPushButton(tr("donate_not_now"))
        self._not_now_btn.setCursor(Qt.ArrowCursor)
        self._not_now_btn.setStyleSheet("")
        self._not_now_btn.clicked.connect(self._on_not_now)
        actions.addWidget(self._coffee_btn)
        actions.addWidget(self._not_now_btn)
        actions.addStretch(1)
        appeal_layout.addLayout(actions)
        self._appeal_dont = QLabel(self._dont_ask_html())
        self._appeal_dont.setTextFormat(Qt.TextFormat.RichText)
        self._appeal_dont.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._appeal_dont.setOpenExternalLinks(False)
        self._appeal_dont.setCursor(Qt.PointingHandCursor)
        self._appeal_dont.setAutoFillBackground(False)
        self._appeal_dont.setStyleSheet("")
        self._appeal_dont.linkActivated.connect(lambda _href: self._on_appeal_dont_ask())
        appeal_layout.addWidget(self._appeal_dont, 0, Qt.AlignLeft)
        self._appeal.setVisible(False)
        self._appeal_callback = None
        w_layout.addWidget(self._appeal)

        # Dummy attributes for backward compatibility
        self._flash_img = QLabel()
        self._flash_img.setVisible(False)
        self._action_label = QLabel("")
        self._action_label.setVisible(False)
        self._status_card = Card("flash_status_panel")
        self._status_card.setVisible(False)
        self._conn_row = InfoRow("flash_conn_status")
        self._dev_row = InfoRow("flash_device_status")
        self._pkg_row = InfoRow("flash_current_pkg")
        self._elapsed_row = InfoRow("flash_elapsed")
        self._eta_row = InfoRow("flash_eta")
        self._prep_eta_seconds = 0
        self._eta_label.setText("")
        self._partition_progress_label.setVisible(False)
        if getattr(self, "_rockbox_site_themes", False):
            if hasattr(self, "_rockbox_site_widget"):
                self._rockbox_site_widget.setVisible(True)
            if hasattr(self, "_rockbox_site_btn"):
                self._rockbox_site_btn.setVisible(True)

        return self._wrap_centered(self._progress_card, warning_container)

    def _build_downloading_view(self):
        self._download_icon = _make_squircle_icon("📥", "icon.png")
        self._download_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        self._download_progress = InstallProgress()
        _configure_progress(self._download_progress)

        self._download_cancel_btn = _RoundNextButton(symbol="back", accent=False)
        self._download_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._download_cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._download_cancel_btn.clicked.connect(self._on_cancel_download)

        self._download_next_btn = _RoundNextButton(symbol="next", accent=True)
        self._download_next_btn.setObjectName("downloadNextPlaceholderBtn")
        self._download_next_btn.setEnabled(False)
        self._download_next_btn.setVisible(True)
        chips = [self._download_cancel_btn, self._download_next_btn]

        self._download_card, self._download_head, copy = self._new_progress_card(
            self._download_icon,
            self._download_pkg_label,
            self._download_progress,
            chips,
        )
        self._download_row = self._download_head.box

        self._download_status_label = _status_label(tr("sel_download_start"))
        copy.addWidget(self._download_status_label)
        self._download_col = copy

        # Same wrapper as the install screens: the download card used to sit in
        # a narrow centred column of its own, so its padding, width and spacing
        # did not line up with the install card it hands over to.
        container = self._wrap_centered(
            self._download_card, heading_key="flash_download_in_progress"
        )
        self._download_heading = self._headings[-1][0]
        return container

    def _switch_view(self, target_view):
        if getattr(self, "_stop_open", False):
            return
        self._stack.setCurrentWidget(target_view)
        self._update_paperclip_hint()
        self._notify_geometry_change()

    def _update_paperclip_hint(self) -> None:
        """Show how to press the hidden button at power-off or disconnect recovery.

        Displays model-specific wording and the corresponding paperclip
        hardware illustration (A5, Y2, or generic/all) matching the active theme.
        In Updater CE branding, the generic all models paperclip hint image (or model-
        specific image if model is known) is displayed in the disconnect recovery scenario.
        In MediaTek Installer branding, paperclip hints are excluded in disconnect recovery.
        """
        host = getattr(self, "_paperclip_hint_host", None)
        label = getattr(self, "_paperclip_hint", None)
        img_label = getattr(self, "_paperclip_img", None)
        if host is None or label is None:
            return
        model = (self._model or "").strip()
        if not model:
            pkg = (getattr(self, "_package_name", "") or "").upper()
            if "A5" in pkg:
                model = "A5"
            else:
                for k in ("Y2", "Y1", "G5", "G3", "G1", "Q5", "Q3"):
                    if k in pkg:
                        model = k
                        break
        copy = getattr(self, "_wait_copy", "")
        from ..branding import is_generic_mtk_brand
        is_mtk = is_generic_mtk_brand()

        is_power_off = (
            bool(model)
            and copy == "power_off"
            and getattr(self, "_show_pin_tip", True)
            and not getattr(self, "_stop_open", False)
        )
        is_usb_disconnected = (
            copy == "usb_disconnected"
            and not is_mtk
            and not getattr(self, "_stop_open", False)
        )
        wanted = is_power_off or is_usb_disconnected
        if not wanted:
            host.setVisible(False)
            if img_label is not None:
                img_label.setFixedSize(0, 0)
            host.setMinimumHeight(0)
            return

        if is_power_off:
            from ..config import device_label_for_model, is_a5_model
            model_name = device_label_for_model(model) if model else tr("device_fallback_word")
            is_a5 = is_a5_model(model)
            key = "flash_paperclip_hint_a5" if is_a5 else "flash_paperclip_hint"
            label.setText(tr(key).format(model=model_name))
            label.setVisible(True)
        else:
            # In disconnect recovery / failover, prompt label already carries the
            # instructions; show only the paperclip diagram image without duplicate text.
            label.setText("")
            label.setVisible(False)

        if img_label is not None:
            from .icons import recolor_bright_pixels, render_svg_asset
            from .dark import T
            from ..paths import RESOURCES_DIR

            # The illustration is drawn in the live text colour: the vector
            # artwork is white (for a dark card), so on a light desktop it is
            # repainted to the foreground role before it is shown.
            txt = QColor()
            try:
                txt = self.palette().color(QPalette.ColorRole.WindowText)
            except Exception:
                pass
            if not txt.isValid():
                txt = QColor(T().fg)
            if not txt.isValid():
                txt = QApplication.style().standardPalette().color(QPalette.ColorRole.Text)

            # Y2 uses paperclip_y2.svg; A5 uses paperclip_a5.svg;
            # all other models (Y1, G-series, Q-series, generic, unknown) use paperclip.svg.
            assets = Path(__file__).resolve().parent.parent.parent / "assets"
            from ..config import is_a5_model, is_y2_model

            if is_a5_model(model):
                stems = ["paperclip_a5", "paperclip"]
            elif is_y2_model(model):
                stems = ["paperclip_y2", "paperclip"]
            else:
                stems = ["paperclip"]

            img_path = None
            for stem in stems:
                for candidate in (
                    RESOURCES_DIR / f"{stem}.svg",
                    assets / f"{stem}.svg",
                ):
                    if candidate.is_file():
                        img_path = candidate
                        break
                if img_path is not None:
                    break
            if img_path is None:
                img_path = assets / "paperclip.svg"

            from PySide6.QtGui import QGuiApplication

            screen = self.screen() or (self.window().screen() if self.window() else None) or QGuiApplication.primaryScreen()
            screen_dpr = float(screen.devicePixelRatio() if screen else 2.0)
            dpr = max(float(self.devicePixelRatioF() or 1.0), screen_dpr, 2.0 if sys.platform == "darwin" else 1.0)
            source = render_svg_asset(img_path, _PAPERCLIP_HINT_PX, color=txt, device_pixel_ratio=dpr)
            if not source.isNull():
                img_label.setProperty("_hint_source", source)

            if hasattr(img_label, "load_svg"):
                ok = img_label.load_svg(img_path, txt)
                img_label.setVisible(ok)
                if ok and host is not None:
                    host.setMinimumHeight(int(img_label.height()) + 18)
            else:
                if not source.isNull():
                    img_label.setPixmap(source)
                    img_label.setFixedSize(source.deviceIndependentSize().toSize())
                    img_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    img_label.setVisible(True)
                    if host is not None:
                        host.setMinimumHeight(int(source.deviceIndependentSize().height()) + 18)
                else:
                    img_label.setVisible(False)
                    img_label.setFixedSize(0, 0)
                    if host is not None:
                        host.setMinimumHeight(0)

        waiting = getattr(self, "_waiting_view", None)
        host.setVisible(waiting is not None and self._stack.currentWidget() is waiting)

    def show_downloading(self):
        val = self._package_name or "\u2014"
        if hasattr(self, "_download_pkg_label"):
            self._download_pkg_label.setText(val)
        self._download_progress.setValue(0)
        self._download_status_label.setText(tr("sel_download_start"))
        self._switch_view(self._downloading_view)
        self._apply_stop_hold()

    def update_download_progress(self, percent: int, status_text: str = ""):
        self._set_determinate(self._download_progress, percent)
        if status_text and not getattr(self, "_stop_open", False):
            self._download_status_label.setText(status_text)
        self._apply_stop_hold()

    def set_cancel_download_callback(self, cb):
        self._cancel_download_callback = cb

    def _on_cancel_download(self):
        self.prompt_stop(
            tr_brand("download_stop_check"),
            on_continue=self._cancel_download_callback,
        )

    def _load_image(self, label, name, target_height=None):
        """Guidance images during firmware installs are removed in favor of native UI."""
        pass

    def show_preparing(self):
        self._complete = False
        self._switch_view(self._preparing_view)
        self._prep_banner.set_key("flash_preparing")
        self._prep_step_key = "step_extract"
        self._prep_step.setText(tr("step_extract"))
        self._apply_stop_hold()

    def show_drop_check(self, name: str = "") -> None:
        """Indeterminate bar while a dropped package is checked. No tool names."""
        self._complete = False
        if name and hasattr(self, "_prep_pkg_label"):
            self._prep_pkg_label.setText(name)
        self._switch_view(self._preparing_view)
        self._prep_banner.setVisible(False)
        self._prep_step_key = "drop_checking"
        self._prep_step.setText(tr("drop_checking"))
        self._prep_progress.setRange(0, 0)

    def show_drop_error(self, message: str) -> None:
        """Stop the throbber and leave the reason on the progress card."""
        self._prep_progress.setRange(0, 100)
        self._prep_progress.setValue(0)
        self._prep_step_key = ""
        self._prep_step.setText(message)
        self._switch_view(self._preparing_view)

    def show_waiting(self):
        self._complete = False
        if hasattr(self, "_rockbox_site_widget"):
            self._rockbox_site_widget.setVisible(False)
        if hasattr(self, "_rockbox_site_btn"):
            self._rockbox_site_btn.setVisible(False)
        self._switch_view(self._waiting_view)
        if not self._prompt_only:
            # Nothing has been written yet. The bar stays at 0 until DA or
            # partition progress arrives.
            self._set_determinate(self._wait_progress_bar, 0)
        self._apply_stop_hold()

    def _set_indeterminate(self, bar: QProgressBar) -> None:
        """Native busy throbber. Range (0, 0) is the platform indeterminate bar."""
        bar.setRange(0, 0)
        bar.setTextVisible(False)

    def show_please_wait(self, connecting: bool = False) -> None:
        """Indeterminate bar and "Please wait", never the connect sentence.

        Used before the tool prints a USB search line, and again after a later
        line of real progress until a percent or stage takes over.
        """
        self._failed = False
        self._complete = False
        self._prompt_only = False
        self._wait_copy = "please_wait_connecting" if connecting else "please_wait"
        self._switch_view(self._waiting_view)
        if connecting:
            self._wait_prompt_label.setText(self._wait_connecting_prompt_text())
        else:
            self._wait_prompt_label.setText(tr("flash_please_wait"))
        self._set_indeterminate(self._wait_progress_bar)
        self._wait_banner.set_type("info")
        self._wait_banner_key = "please_wait"
        self._wait_banner.set_key("flash_please_wait")
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._apply_stop_hold()

    def show_entering_password(self) -> None:
        """Display clear password prompt message while system auth dialog is open."""
        self._failed = False
        self._complete = False
        self._prompt_only = False
        self._wait_copy = "entering_password"
        self._switch_view(self._waiting_view)
        self._wait_prompt_label.setText(tr("install_linux_entering_password"))
        self._set_indeterminate(self._wait_progress_bar)
        self._wait_banner.set_type("info")
        self._wait_banner_key = "entering_password"
        self._wait_banner.set_key("install_linux_entering_password")
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._apply_stop_hold()

    def show_connect_search(self) -> None:
        """Connect sentence only while the flasher is searching for USB."""
        self._failed = False
        self._complete = False
        self._prompt_only = False
        self._wait_copy = "connect"
        self._switch_view(self._waiting_view)
        self.set_waiting_device()
        self._set_determinate(self._wait_progress_bar, 0)
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._apply_stop_hold()

    def _is_mtk_active(self) -> bool:
        from ..flash_service import is_mtk_method
        raw = getattr(self, "_raw_method", "")
        if raw == "sp":
            return False
        return is_mtk_method(getattr(self, "_method", ""))

    def set_step(self, text: str) -> None:
        """Set step title or status line."""
        if hasattr(self, "_step_label"):
            self._step_label.setText(text)

    def _connect_device_prompt_text(self) -> str:
        prompt = tr("flash_connect_device_prompt").format(model=self._connect_model_text())
        remaining = getattr(self, "_attempt_remaining", 0)
        if remaining > 0 and not getattr(self, "_prompt_only", False):
            return f"{prompt} ({remaining}s)"
        return prompt

    def _wait_connecting_prompt_text(self) -> str:
        prompt = tr("flash_wait_connecting_prompt").format(model=self._connect_model_text())
        remaining = getattr(self, "_attempt_remaining", 0)
        if remaining > 0 and not getattr(self, "_prompt_only", False):
            return f"{prompt} ({remaining}s)"
        return prompt

    def set_attempt_timeout(self, seconds) -> None:
        """Show the app's own no-progress countdown alongside the connect prompt.

        ``0`` clears it: the countdown only applies while waiting for the device
        to connect, and stops the moment the device is detected or progress begins.
        """
        try:
            remaining = int(seconds or 0)
        except (TypeError, ValueError):
            remaining = 0
        self._attempt_remaining = max(0, remaining)
        label = getattr(self, "_attempt_lbl", None)
        if label is not None:
            label.setText("")
            label.setVisible(False)
        if hasattr(self, "_wait_prompt_label"):
            copy = getattr(self, "_wait_copy", "")
            if not getattr(self, "_prompt_only", False) and copy not in ("power_off", "stage"):
                if copy == "please_wait_connecting":
                    text = self._wait_connecting_prompt_text()
                else:
                    if remaining > 0:
                        self._wait_copy = "connect"
                    if self._wait_copy == "connect":
                        text = self._connect_device_prompt_text()
                    else:
                        text = ""
                if text:
                    if getattr(self, "_stop_open", False):
                        saved = getattr(self, "_stop_saved", None)
                        if saved:
                            self._stop_saved = (text, saved[1], saved[2], saved[3], saved[4])
                    else:
                        self._wait_prompt_label.setText(text)

    def show_real_stage(self, text: str) -> None:
        """Drop the connect sentence and the throbber once the tool moves on."""
        self._prompt_only = False
        self.set_attempt_timeout(0)
        self._wait_copy = "stage"
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(text or "")
        if self._wait_progress_bar.maximum() == 0 and self._wait_progress_bar.minimum() == 0:
            self._set_determinate(self._wait_progress_bar, 0)
        self._apply_stop_hold()

    def _hide_theme_wizard_chips(self) -> None:
        for attr in (
            "_cancel_btn",
            "_flash_retry_btn",
            "_done_mark",
            "_theme_step1_next_btn",
            "_theme_fallback_next_btn",
            "_theme_trouble_recover_btn",
            "_theme_back_btn",
        ):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setVisible(False)
                btn.setEnabled(True)

    def show_flashing(self):
        if hasattr(self, "_complete_note"):
            self._complete_note.setVisible(False)
        if getattr(self, "_wait_copy", "") in ("connect", "please_wait", "please_wait_connecting"):
            self._wait_copy = "stage"
            if hasattr(self, "_wait_prompt_label"):
                self._wait_prompt_label.setText(self._step_label.text())
        self._switch_view(self._flashing_view)
        failed = self._failed
        done = bool(getattr(self, "_complete", False)) and not failed
        self._warning.setVisible(self._flash_warning_wanted())
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(not done)
            self._flash_retry_btn.setEnabled(failed)
            self._flash_retry_btn.set_symbol("retry" if failed else "next")
            if failed:
                self._flash_retry_btn.setToolTip(tr("flash_btn_retry"))
                self._flash_retry_btn.setAccessibleName(tr("flash_btn_retry"))
            else:
                self._flash_retry_btn.setToolTip(tr("btn_next"))
                self._flash_retry_btn.setAccessibleName(tr("btn_next"))
        if hasattr(self, "_cancel_btn"):
            if done:
                self._cancel_btn.setVisible(True)
                self._cancel_btn.setEnabled(False)
                self._cancel_btn.set_symbol("back")
                self._cancel_btn.setToolTip("")
                self._cancel_btn.setAccessibleName("")
            else:
                self._cancel_btn.setVisible(True)
                self._cancel_btn.setEnabled(True)
                self._cancel_btn.set_symbol("cancel" if failed else "back")
                self._cancel_btn.setToolTip(tr("flash_btn_cancel"))
                self._cancel_btn.setAccessibleName(tr("flash_btn_cancel"))
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(done)
            if done:
                self._done_mark.setEnabled(True)
        if hasattr(self, "_flash_head") and hasattr(self._flash_head, "_sync_chips_pairing"):
            self._flash_head._sync_chips_pairing()
        if not failed and hasattr(self, "_driver_action_row"):
            self._driver_action_row.setVisible(False)
        self._hide_theme_wizard_chips()
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        self._apply_stop_hold()

    def _open_mediatek_drivers(self) -> None:
        from ..browser import open_browser
        from ..config import MEDIATEK_DRIVERS_URL

        open_browser(MEDIATEK_DRIVERS_URL)

    def _set_determinate(self, bar: QProgressBar, percent) -> None:
        percent = max(0, min(100, int(percent)))
        bar.setRange(0, 100)
        bar.setFormat("%p%")
        bar.setValue(percent)
        bar.setTextVisible(True)

    def show_install_failure(self, message: str, explanation: str = "", percent: int = 0) -> None:
        """Keep the failure on the install progress card, with Retry and Cancel beneath."""
        self.update_partition_progress("")
        self.dismiss_prompt_stop()
        self._failed = True
        self._prompt_only = False
        self.show_flashing()
        self._warning.setVisible(False)
        if hasattr(self, "_appeal"):
            self._appeal.setVisible(False)
        if hasattr(self._progress_bar, "set_failed"):
            self._progress_bar.set_failed(True)
        else:
            self._progress_bar.setFormat(tr("install_failed"))

        from ..flash_service import is_sp_driver_error, parse_flash_error
        driver_missing = is_sp_driver_error(message)
        if not explanation:
            title_text, explanation_text = parse_flash_error(message, self._model)
            step_text = title_text
            if message and message not in step_text and len(message) < 40:
                step_text = f"{step_text} ({message})"
            exp_text = explanation_text
        elif driver_missing:
            title_text, _ = parse_flash_error(message, self._model)
            step_text = f"{title_text} ({message})" if message and message not in title_text else title_text
            exp_text = explanation
        else:
            step_text = message
            exp_text = explanation

        from .dark import T
        t = T()
        self._step_label.setText(step_text)
        self._step_label.setStyleSheet(
            f"color: {t.danger_fg}; font-weight: 700; font-size: 13px;"
        )
        self._eta_label.setText(exp_text)
        self._eta_label.setWordWrap(True)
        if hasattr(self, "_status_flow") and hasattr(self._status_flow, "set_forced_stacked"):
            self._status_flow.set_forced_stacked(True)
        if hasattr(self, "_driver_action_row"):
            self._driver_action_row.setVisible(driver_missing)

        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(True)
            self._flash_retry_btn.setEnabled(True)
            self._flash_retry_btn.set_symbol("retry")
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(True)
            self._cancel_btn.setEnabled(True)
            self._cancel_btn.set_symbol("cancel")
        if message == "CACHE_FILE_MISSING":
            self._flash_retry_btn.setToolTip(tr("sel_cache_retry_tooltip"))
            self._flash_retry_btn.setAccessibleName(tr("sel_cache_retry_button"))
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        self._notify_geometry_change()

    def stop_theme_workers(self) -> None:
        """Cancel and clean up any in-progress Rockbox theme background tasks."""
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        for attr in ("_adb_watch_worker", "_adb_push_worker", "_dir_merge_worker"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if w.isRunning():
                        w.requestInterruption()
                        w.wait(1500)
                except Exception:
                    pass
                setattr(self, attr, None)

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        self.stop_theme_workers()
        super().closeEvent(event)

    def clear_install_failure(self) -> None:
        self.dismiss_prompt_stop()
        self._failed = False
        self._complete = False
        self.stop_theme_workers()
        if hasattr(self._progress_bar, "set_failed"):
            self._progress_bar.set_failed(False)
        else:
            self._progress_bar.setFormat("%p%")
        if hasattr(self._wait_progress_bar, "set_failed"):
            self._wait_progress_bar.set_failed(False)
        else:
            self._wait_progress_bar.setFormat("%p%")
        if hasattr(self, "_step_label"):
            # Back to the card foreground rather than an empty sheet: empty
            # inherits the window's default label colour, which is dimmer than
            # the card's own text and would stand out on the next install.
            _apply_card_text(self._step_label)
        if hasattr(self, "_eta_label"):
            self._eta_label.setText("")
        self._partition_progress_label.setVisible(False)
        if getattr(self, "_rockbox_site_themes", False):
            if hasattr(self, "_rockbox_site_widget"):
                self._rockbox_site_widget.setVisible(True)
            if hasattr(self, "_rockbox_site_btn"):
                self._rockbox_site_btn.setVisible(True)
        if hasattr(self, "_status_flow") and hasattr(self._status_flow, "set_forced_stacked"):
            self._status_flow.set_forced_stacked(False)
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(False)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(True)
            self._flash_retry_btn.setEnabled(False)
            self._flash_retry_btn.set_symbol("next")
            self._flash_retry_btn.setToolTip(tr("btn_next"))
            self._flash_retry_btn.setAccessibleName(tr("btn_next"))
            self._cancel_btn.setVisible(True)
            self._cancel_btn.setEnabled(True)
            self._cancel_btn.set_symbol("back")
            self._cancel_btn.setToolTip(tr("flash_btn_cancel"))
            self._cancel_btn.setAccessibleName(tr("flash_btn_cancel"))
        self._hide_theme_wizard_chips()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_driver_action_row"):
            self._driver_action_row.setVisible(False)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()

    def show_usb_disconnected(
        self, on_retry=None, on_cancel=None, prompt_key="", prompt_kwargs=None
    ) -> None:
        """Prompt in the install card when USB drops, asking user to reset and press Next.

        ``prompt_key`` swaps the recovery line for another wording of the same
        step (today: a backend that could not connect at all, which names the
        engine taking over), and ``prompt_kwargs`` carries that line's fields.
        """
        self._failed = True
        self._prompt_only = True
        self._wait_copy = "usb_disconnected"
        self._show_pin_tip = False
        # The retranslate path re-renders this line through
        # ``_power_off_prompt_key``; it has to keep the recovery wording.
        self._power_off_recovery = True
        self._permission_prompt = False
        self._power_off_recovery_key = str(prompt_key or "")
        self._power_off_recovery_kwargs = dict(prompt_kwargs or {})
        self.clear_install_failure()
        self.show_waiting()
        msg = self._power_off_prompt_text()
        self._wait_prompt_label.setText(msg)
        if hasattr(self._wait_progress_bar, "set_failed"):
            self._wait_progress_bar.set_failed(True)
        else:
            self._wait_progress_bar.setFormat(tr("install_failed"))
        self._wait_cancel_btn.setVisible(True)
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(True)
        if on_retry is not None:
            self._power_off_continue_cb = on_retry
            self._install_retry_cb = on_retry
        if prompt_key == "err_connect_failover_recovery":
            self._step_label.setText(tr("install_failed"))
        else:
            self._step_label.setText(tr("err_usb_lost_title"))
        from .dark import T
        t = T()
        self._step_label.setStyleSheet(
            f"color: {t.danger_fg}; font-weight: 700; font-size: 13px;"
        )
        self._eta_label.setText(msg)
        self._eta_label.setWordWrap(True)
        if hasattr(self, "_status_flow") and hasattr(self._status_flow, "set_forced_stacked"):
            self._status_flow.set_forced_stacked(True)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(True)
            self._flash_retry_btn.setEnabled(True)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(True)
            self._cancel_btn.setEnabled(True)
        self._update_paperclip_hint()
        self._apply_stop_hold()
        self._notify_geometry_change()

    # ------------------------------------------------------------------
    # Rockbox 360p themes & fonts post-install flow
    # ------------------------------------------------------------------
    def _log_theme(self, msg: str) -> None:
        cb = getattr(self, "_theme_log_cb", None)
        if callable(cb):
            cb(msg)
        else:
            logger.info("%s", msg)

    def _on_theme_step1_next_clicked(self) -> None:
        callback = getattr(self, "_rockbox_power_on_continue_cb", None)
        if callable(callback):
            self._rockbox_power_on_continue_cb = None
            callback()
            return
        self._log_theme("[Rockbox Themes] User clicked Next on Step 1 (device powered on). Advancing to Step 2 (connect USB).")
        self._start_theme_step2_connect_usb()

    def start_rockbox_themes_stage(
        self,
        model="Y1",
        on_done=None,
        on_recover=None,
        log_cb=None,
        on_troubleshoot=None,
        already_powered_on=False,
        automatic=False,
    ) -> None:
        """Seamless in-card Rockbox 360p themes & fonts post-install flow."""
        self.dismiss_prompt_stop()
        self._theme_stage_model = model
        self._theme_stage_done_cb = on_done
        self._theme_stage_recover_cb = on_troubleshoot or on_recover
        self._rockbox_power_on_continue_cb = None
        self._theme_log_cb = log_cb
        self._themes_stage_active = True
        self._failed = False
        self._complete = False
        self.show_flashing()
        self._warning.setVisible(False)
        if hasattr(self, "_appeal"):
            self._appeal.setVisible(False)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(False)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(False)
        self._hide_theme_wizard_chips()

        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(True)

        self.set_step(tr("rockbox_stage_installing"))
        self._progress_bar.set_precise_value(90.0)

        model_str = (model or "Y1").upper()
        self._rockbox_already_powered_on = bool(already_powered_on)
        self._theme_automatic = bool(automatic)
        self._log_theme(f"[Rockbox Themes] Starting 360p themes & fonts stage for {model_str}...")

        from ..adb import find_adb_binary, adb_devices
        adb_bin = find_adb_binary()
        if not adb_bin:
            self._log_theme("[Rockbox Themes] ADB binary not found on host/bundle. Guiding user via USB Storage Mode.")
            self._show_theme_fallback_usb_mode()
            return

        self._log_theme(f"[Rockbox Themes] Located ADB binary at: {adb_bin}")

        # Seamless mode: check if an ADB device is already connected
        existing_devices = adb_devices(adb_bin)
        if existing_devices:
            serial = existing_devices[0]
            self._log_theme(f"[Rockbox Themes] Seamless match: ADB device {serial} already detected! Pushing themes silently...")
            self._on_adb_device_found(serial)
            return

        # If MTKClient's post-flash handoff already asked the user to boot the
        # player, go straight to the USB/ADB reconnect step instead of asking
        # them to power it on a second time.
        # SP Flash installs never need the manual disconnect/power-on steps:
        # go straight to the silent ADB watch. MTKClient installs still do.
        if automatic or already_powered_on:
            self._start_theme_step2_connect_usb()
        else:
            self._show_theme_step1_disconnect_power_on()

    def _show_theme_step1_disconnect_power_on(self) -> None:
        model_str = getattr(self, "_theme_stage_model", "Y1").upper()
        button = tr("button_centre" if model_str == "Y1" else "button_power")
        step1_text = tr("rockbox_step1_power_on_button").format(
            model=model_str,
            button=button,
        )
        self._eta_label.setText(step1_text)
        self._log_theme(f"[Rockbox Themes] Step 1: Prompting user to disconnect and power on {model_str}.")
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(True)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(True)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        if hasattr(self, "_theme_back_btn"):
            self._theme_back_btn.setEnabled(False)
            self._theme_back_btn.setVisible(True)
        if hasattr(self, "_theme_step1_next_btn"):
            self._theme_step1_next_btn.setEnabled(True)
            self._theme_step1_next_btn.setVisible(True)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_chips_pairing()
            self._flash_head._sync_to_band()
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _start_theme_step2_connect_usb(self) -> None:
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(True)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(True)
        self._hide_theme_wizard_chips()
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

        model_str = getattr(self, "_theme_stage_model", "Y1").upper()
        from ..adb import ADB_WATCH_TIMEOUT_SECONDS
        self._adb_watch_seconds = ADB_WATCH_TIMEOUT_SECONDS
        usb_prompt = tr("rockbox_step2_connect_usb").format(model=model_str)
        adb_status = tr("rockbox_stage_waiting_adb_fmt").format(remaining=self._adb_watch_seconds)
        self._eta_label.setText("" if getattr(self, "_theme_automatic", False) else f"{usb_prompt}\n{adb_status}")
        self._log_theme(f"[Rockbox Themes] Step 2: Waiting for USB connection and ADB handshake ({self._adb_watch_seconds}s timeout)...")

        from ..adb import find_adb_binary
        from ..theme_pack import AdbWatchWorker
        adb_bin = find_adb_binary()
        if not adb_bin:
            self._show_theme_fallback_usb_mode()
            return

        self._adb_watch_worker = AdbWatchWorker(adb_bin, timeout=float(self._adb_watch_seconds), parent=self)
        self._adb_watch_worker.found.connect(self._on_adb_device_found)
        self._adb_watch_worker.timed_out.connect(self._show_theme_fallback_usb_mode)

        self._adb_watch_timer = QTimer(self)
        self._adb_watch_timer.setInterval(1000)
        self._adb_watch_timer.timeout.connect(self._on_adb_watch_tick)
        self._adb_watch_timer.start()
        self._adb_watch_worker.start()

    def _on_adb_watch_tick(self) -> None:
        if getattr(self, "_adb_watch_seconds", 0) > 0:
            self._adb_watch_seconds -= 1
            model_str = getattr(self, "_theme_stage_model", "Y1").upper()
            usb_prompt = tr("rockbox_step2_connect_usb").format(model=model_str)
            adb_status = tr("rockbox_stage_waiting_adb_fmt").format(remaining=self._adb_watch_seconds)
            self._eta_label.setText("" if getattr(self, "_theme_automatic", False) else f"{usb_prompt}\n{adb_status}")
            if self._adb_watch_seconds in (100, 80, 60, 40, 20, 10, 5):
                self._log_theme(f"[Rockbox Themes] Waiting for ADB device... ({self._adb_watch_seconds}s remaining)")
        else:
            if hasattr(self, "_adb_watch_timer"):
                self._adb_watch_timer.stop()

    def _on_adb_device_found(self, serial: str) -> None:
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        self._hide_theme_wizard_chips()
        self._log_theme(f"[Rockbox Themes] Player detected via ADB (serial: {serial}). Waiting {ROCKBOX_BOOT_WAIT_MS // 1000}s for Rockbox to populate .rockbox...")
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        QTimer.singleShot(ROCKBOX_BOOT_WAIT_MS, lambda: self._start_adb_theme_push(serial))

    def _start_adb_theme_push(self, serial: str) -> None:
        self._log_theme("[Rockbox Themes] Pushing 360p themes & fonts over ADB...")
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        self._theme_last_logged_curr = 0
        from ..adb import find_adb_binary
        from ..theme_pack import AdbPushWorker, ensure_normalized_theme_pack_zip
        adb_bin = find_adb_binary()
        norm_zip = ensure_normalized_theme_pack_zip()

        self._adb_push_worker = AdbPushWorker(
            adb_bin,
            serial,
            local_dir=None,
            parent=self,
            zip_path=norm_zip,
        )
        self._adb_push_worker.progress.connect(self._on_theme_push_progress)
        self._adb_push_worker.finished.connect(self._on_theme_push_finished)
        self._adb_push_worker.start()

    def _on_theme_push_progress(self, curr: int, total: int) -> None:
        from ..progress_model import scale_theme_progress
        if total <= 0:
            return
        pct = (float(curr) / max(1.0, float(total))) * 100.0
        scaled = scale_theme_progress(pct)
        self._progress_bar.set_precise_value(scaled)
        if total == 100:
            if curr < 65:
                self._eta_label.setText(f"{tr('rockbox_stage_pushing')} ({curr}%)")
            elif curr < 95:
                self._eta_label.setText(tr("rockbox_stage_installing"))
            else:
                self._eta_label.setText(tr("rockbox_stage_pushing"))
        else:
            self._eta_label.setText(
                tr("rockbox_stage_pushing_files_fmt").format(curr=curr, total=total)
            )
        last_logged = getattr(self, "_theme_last_logged_curr", 0)
        if curr == 1 or curr == total or (curr - last_logged) >= 25 or (total == 100 and (curr - last_logged) >= 20):
            self._theme_last_logged_curr = curr
            if total == 100:
                self._log_theme(f"[Rockbox Themes] Theme pack transfer progress: {curr}%...")
            else:
                self._log_theme(f"[Rockbox Themes] Pushed {curr}/{total} theme files to player ({int(pct)}%)...")

    def _on_theme_push_finished(self, ok: bool, msg: str) -> None:
        if ok:
            self._log_theme("[Rockbox Themes] Themes and fonts were transferred and verified successfully via ADB.")
            self._finish_rockbox_themes_stage()
        else:
            self._log_theme(f"[Rockbox Themes] ADB theme push encountered an issue: {msg}. Falling back to USB storage guidance.")
            self._show_theme_fallback_usb_mode()

    def _show_theme_fallback_usb_mode(self) -> None:
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(True)
        self._log_theme("[Rockbox Themes] Guiding user: Please enable USB storage mode.")
        self.set_step(tr("rockbox_stage_installing"))
        self._eta_label.setText(tr("rockbox_step_usb_storage_mode"))
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(True)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(True)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        if hasattr(self, "_theme_back_btn"):
            self._theme_back_btn.setEnabled(False)
            self._theme_back_btn.setVisible(True)
        if hasattr(self, "_theme_fallback_next_btn"):
            self._theme_fallback_next_btn.set_symbol("folder")
            self._theme_fallback_next_btn.setToolTip(tr("rockbox_step_select_player_folder"))
            self._theme_fallback_next_btn.setEnabled(True)
            self._theme_fallback_next_btn.setVisible(True)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_chips_pairing()
            self._flash_head._sync_to_band()
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _show_theme_manual_prompt(self) -> None:
        self._show_theme_fallback_usb_mode()

    def _on_theme_fallback_next_clicked(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        model = getattr(self, "_theme_stage_model", "Y1").upper()
        title = tr("rockbox_step_select_player_folder")
        self._eta_label.setText(title)
        self._log_theme(f"[Rockbox Themes] Prompting user to select player drive/folder: {title}")
        chosen = QFileDialog.getExistingDirectory(
            self,
            title,
            "",
            QFileDialog.ShowDirsOnly,
        )
        if not chosen:
            return
        from ..theme_pack import find_or_create_rockbox_dir, DirMergeWorker, bundled_rockbox_dir
        dest_dir = find_or_create_rockbox_dir(chosen)
        src_dir = bundled_rockbox_dir()
        self._log_theme(f"[Rockbox Themes] Selected folder: {chosen}. Resolved target .rockbox: {dest_dir}")
        self._log_theme(f"[Rockbox Themes] Merging bundled themes & fonts from {src_dir} into {dest_dir}...")
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        self._theme_last_logged_curr = 0

        self._dir_merge_worker = DirMergeWorker(src_dir, dest_dir, parent=self)
        self._dir_merge_worker.progress.connect(self._on_theme_push_progress)
        self._dir_merge_worker.finished.connect(
            lambda ok, err, count: self._on_dir_merge_finished(ok, err, count, str(dest_dir))
        )
        self._dir_merge_worker.start()

    def _on_dir_merge_finished(self, ok: bool, err: str, count: int, dest: str) -> None:
        """Only declare the theme stage complete once files were really copied."""
        if ok and count > 0:
            self._finish_rockbox_themes_stage(manual_count=count, dest=dest)
            return
        reason = err or "no theme files were copied"
        self._log_theme(f"[Rockbox Themes] Merge into {dest} did not complete: {reason}. Showing USB storage guidance again.")
        self._show_theme_fallback_usb_mode()

    def _on_theme_yes_clicked(self) -> None:
        self._on_theme_fallback_next_clicked()

    def _on_theme_no_clicked(self) -> None:
        model = getattr(self, "_theme_stage_model", "Y1").upper()
        self._log_theme(f"[Rockbox Themes] User reported Rockbox is not yet running on {model}. Showing power-on troubleshoot help.")
        self._eta_label.setText(tr("rockbox_troubleshoot_desc").format(model=model))
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(True)
        self._hide_theme_wizard_chips()
        if hasattr(self, "_theme_back_btn"):
            self._theme_back_btn.setEnabled(True)
            self._theme_back_btn.setVisible(True)
        if hasattr(self, "_theme_trouble_recover_btn"):
            self._theme_trouble_recover_btn.setEnabled(True)
            self._theme_trouble_recover_btn.setVisible(True)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_chips_pairing()
            self._flash_head._sync_to_band()
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _on_theme_browse_clicked(self) -> None:
        self._on_theme_fallback_next_clicked()

    def _on_theme_recover_clicked(self) -> None:
        self._log_theme("[Rockbox Themes] User requested restore original software.")
        cb = getattr(self, "_theme_stage_recover_cb", None)
        if callable(cb):
            cb()
            return
        win = self.window()
        if win and hasattr(win, "restore_original_software"):
            model = getattr(self, "_theme_stage_model", "Y1")
            win.restore_original_software(model)

    def _finish_rockbox_themes_stage(self, manual_count=None, dest=None) -> None:
        if manual_count is not None:
            self._log_theme(f"[Rockbox Themes] Successfully merged {manual_count} theme & font files into {dest}!")
        self._log_theme("[Rockbox Themes] Rockbox 360p themes & fonts stage completed.")
        self._themes_stage_active = False
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(False)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        self._hide_theme_wizard_chips()
        self._progress_bar.set_precise_value(100.0)
        self.set_device_done()
        cb = getattr(self, "_theme_stage_done_cb", None)
        if callable(cb):
            cb()

    def rockbox_power_on_prompt_key(self, model: str) -> str:
        """One copy for both flash methods: the player may not have been powered
        up by the flash tool, so the user powers it on before Next."""
        is_y1 = (model or "").upper() == "Y1"
        return "rockbox_power_on_prompt_sp_y1" if is_y1 else "rockbox_power_on_prompt_sp_other"

    def show_rockbox_power_on_prompt(self, prompt: str, on_continue=None, on_recover=None) -> None:
        """Keep the install card active while the user boots the flashed player."""
        self._failed = False
        self._complete = False
        self._themes_stage_active = False
        self._theme_stage_done_cb = None
        self._theme_stage_recover_cb = on_recover
        self.stop_theme_workers()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_appeal"):
            self._appeal.setVisible(False)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(False)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
        self.show_flashing()
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(False)
        self.set_step(tr("rockbox_stage_installing"))
        self._progress_bar.set_precise_value(90.0)
        self._eta_label.setText(prompt)
        self._eta_label.setWordWrap(True)
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(True)
        self._rockbox_power_on_continue_cb = on_continue
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(True)
        self._theme_step1_next_btn.setText(tr("dialog_pre_install_continue"))
        self._hide_theme_wizard_chips()
        self._theme_step1_next_btn.setVisible(True)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(True)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(True)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        self._notify_geometry_change()

    def _on_rockbox_power_on_continue(self) -> None:
        callback = getattr(self, "_rockbox_power_on_continue_cb", None)
        if callable(callback):
            callback()

    def _power_off_prompt_key(self) -> str:
        """Prompt to prepare device before flashing.

        A player that dropped out of a run (disconnected, or left behind by the
        method that failed) needs its own line: it says how to get back into
        flash mode -- disconnect USB, press the hidden button -- rather than
        only asking for a power-off and unplug. Keeping this here is what makes
        a language change re-render that same line instead of the plain one.
        """
        if self._power_off_recovery:
            return self._power_off_recovery_key or "err_usb_disconnected_recovery"
        if getattr(self, "_permission_prompt", False):
            return "install_linux_permission_prompt"
        return "flash_connect_prompt"

    def _power_off_prompt_text(self) -> str:
        """The power-off / recovery line, with its own fields applied.

        Both the recovery wording and the plain one take ``{model}``; the
        connect-failure wording also names the engine that failed and the one
        taking over, which only its own call site supplies.
        """
        kwargs = dict(self._power_off_recovery_kwargs or {})
        kwargs.setdefault("model", self._connect_model_text())
        text = tr(self._power_off_prompt_key()).format(**kwargs)
        if self._power_off_recovery_key == "err_connect_failover_recovery":
            code = kwargs.get("error_code")
            if code:
                parts = text.split("\n\n", 1)
                if len(parts) == 2:
                    text = f"{parts[0]} (Error:{code})\n\n{parts[1]}"
                else:
                    text = f"{text} (Error:{code})"
        return text

    def show_power_off_prompt(self, show_pin_tip: bool = True, permission: bool = False) -> None:
        """Prompt to power off and unplug. Does not start a flash by itself.

        ``permission`` shows the one-time Linux access step instead; its Next
        button runs the password prompt, and the connect guidance follows it.
        """
        self._failed = False
        self._prompt_only = True
        self._wait_copy = "power_off"
        self._power_off_recovery = False
        self._permission_prompt = bool(permission)
        self._power_off_recovery_key = ""
        self._power_off_recovery_kwargs = {}
        self._show_pin_tip = show_pin_tip
        self.set_attempt_timeout(0)
        self.clear_install_failure()
        self.show_waiting()
        prompt = self._power_off_prompt_text()
        self._wait_prompt_label.setText(prompt)
        self._set_determinate(self._wait_progress_bar, 0)
        # Nothing has been flashed yet, so stepping back needs no question:
        # the cancel chip stops the run outright.
        self._wait_cancel_btn.setVisible(True)
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._update_paperclip_hint()
        self._apply_stop_hold()

    def show_gui_handoff_prompt(self, hint: str) -> None:
        """Power-off line plus a small download-mode hint. Continue does not flash."""
        self.show_power_off_prompt()
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setText(hint or "")
            self._wait_mode_hint.setVisible(False)

    def leave_power_off_prompt(self) -> None:
        self._prompt_only = False
        self._permission_prompt = False
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self.set_waiting_device()
        # SP Flash Tool's countdown starts on its "search usb" line (the worker
        # sends the seconds), so it is not started here. MTKClient keeps its own.
        if self._is_mtk_active():
            self.set_attempt_timeout(120)
        else:
            self.set_attempt_timeout(0)

    def on_install_retry(self, cb) -> None:
        self._install_retry_cb = cb

    def _on_install_retry(self) -> None:
        if callable(self._install_retry_cb):
            self._install_retry_cb()

    def on_power_off_continue(self, cb) -> None:
        self._power_off_continue_cb = cb

    def _on_power_off_continue(self) -> None:
        if callable(self._power_off_continue_cb):
            self._power_off_continue_cb()

    def set_method(self, method):
        """Record the install method chosen in Settings for this run."""
        self._raw_method = str(method or "").strip().lower()
        self._method = normalise_method(method)

    def set_bundled_run(self, enabled: bool) -> None:
        """Record that this run installs the firmware bundled next to the app."""
        self._bundled_run = bool(enabled)

    def set_rockbox_site_themes(self, enabled: bool) -> None:
        """Record that this run installs a Rockbox build whose themes live on themes.rockbox.org."""
        self._rockbox_site_themes = bool(enabled)

    def _initsteps_image(self):
        if paths.IS_MAC:
            return "initsteps.png"
        if self._method == METHOD_SP:
            return "initsteps_sp.png"
        if self._method == METHOD_MTK:
            return "initsteps_win.png" if paths.IS_WINDOWS else "initsteps.png"
        return "initsteps.png"

    def current_method(self):
        return self._method

    def highlight_guide_step(self, step):
        """Emphasise one numbered step of the connection guide."""
        self._guide_step = int(step or 0)
        self._apply_guide_highlight()

    def _apply_guide_highlight(self):
        t = T()
        for idx, (_key, label) in enumerate(self._guide_texts, start=1):
            if idx == self._guide_step:
                label.setStyleSheet(f"color: {t.fg}; font-weight: 600;")
            else:
                label.setStyleSheet("")

    def set_model(self, model):
        self._model = (model or "").strip() or ""
        if hasattr(self, "_wait_prompt_label") and getattr(self, "_wait_copy", "") == "power_off":
            self._wait_prompt_label.setText(self._power_off_prompt_text())
        self._update_paperclip_hint()

    def set_platform(self, platform):
        self._platform = (platform or "").strip() or ""
        self._update_paperclip_hint()

    def refresh_theme(self):
        """Called when system theme or accent changes."""
        self._update_paperclip_hint()

    def _dont_ask_html(self) -> str:
        return f'<a href="dont-ask">{tr("donate_dont_ask_link")}</a>'

    def _open_coffee_page(self) -> None:
        from ..browser import open_browser
        from ..config import DONATION_LINKS

        open_browser(DONATION_LINKS["kofi"])

    def _render_appeal_copy(self) -> None:
        """(Re)write the appeal's words for this language, install and model.

        Shared by ``show_completion_appeal`` and ``retranslate``, so switching
        language after an install still names the software that just finished
        instead of falling back to the generic project pitch.
        """
        software = (getattr(self, "_package_name", "") or "").strip() or tr("flash_current_pkg")
        model = (getattr(self, "_model", "") or "").strip() or tr("device_fallback_word")
        if hasattr(self, "_intro"):
            self._intro.retranslate(software=software, model=model)
            _apply_card_text(self._intro.headline)
            _apply_card_text(self._intro.subtitle)
        if hasattr(self, "_appeal_body"):
            self._appeal_body.setText(tr("donate_completion_body"))
            _apply_card_text(self._appeal_body)
        if hasattr(self, "_coffee_btn"):
            self._coffee_btn.setText(tr("donate_completion_coffee"))
        if hasattr(self, "_not_now_btn"):
            self._not_now_btn.setText(tr("donate_not_now"))
        if hasattr(self, "_appeal_dont"):
            self._appeal_dont.setText(self._dont_ask_html())

    def _render_completion_note(self) -> None:
        software = self._complete_note_software or tr("flash_current_pkg")
        model = self._complete_note_model or tr("device_fallback_word")
        from ..config import install_power_on_steps

        installed = tr("donate_completion_subtitle").format(
            software=software, model=model
        )
        self._complete_note.setText(
            f"{installed}\n{install_power_on_steps(model)}"
        )

    def show_completion_note(self, visible: bool, software: str = "", model: str = "") -> None:
        """Explain the finished install when the donations appeal is hidden."""
        self._complete_note_software = str(software or "").strip()
        self._complete_note_model = str(model or "").strip()
        self._render_completion_note()
        self._complete_note.setVisible(bool(visible))
        self._notify_geometry_change()

    def show_completion_appeal(self, visible: bool, callback=None, on_not_now=None) -> None:
        """Coffee note under a finished install. Hidden when donations are already off.

        The wording leads with what just happened — the software is installed
        on the player — and only then asks for a coffee. An install the reader
        just watched succeed is what makes the ask land as a thank-you rather
        than as an interruption.
        """
        self._appeal_callback = callback
        self._appeal_not_now = on_not_now
        if not hasattr(self, "_appeal"):
            return
        if hasattr(self, "_intro"):
            self._intro.set_keys(COMPLETION_KEYS)
        self._render_appeal_copy()
        self._appeal.setVisible(bool(visible))
        self._notify_geometry_change()

    def _on_not_now(self) -> None:
        self._notify_geometry_change()
        callback = getattr(self, "_appeal_not_now", None)
        if callable(callback):
            callback()

    def _on_appeal_dont_ask(self) -> None:
        """Turn donations off. The caller returns to the compact completion screen."""
        self._notify_geometry_change()
        callback = getattr(self, "_appeal_callback", None)
        if callable(callback):
            callback()

    def set_release_icon(self, pixmap, complete: bool = False):
        """Show the release squircle on every install card. ``complete`` adds the check badge.

        A missing release icon is the settings squircle, not the application icon.
        """
        from .release_icon import squircle_pixmap

        self._release_complete = bool(complete)
        source = pixmap if pixmap is not None and not pixmap.isNull() else QPixmap()
        self._release_source = source
        painted = squircle_pixmap(source, 40, complete=self._release_complete)
        for name in ("_prep_icon", "_wait_icon", "_flash_icon", "_download_icon"):
            label = getattr(self, name, None)
            if label is not None:
                label.setPixmap(painted)
                label.setText("")

    def _display_package_name(self) -> str:
        """Card title with its leading software name in the current language.

        The name is stored as the English catalogue title, so a language change
        re-localises it here rather than keeping the wording from install start.
        """
        name = getattr(self, "_package_name", "") or ""
        canonical = "Original Software"
        if name == canonical or name.startswith(canonical + " "):
            return tr("software_original") + name[len(canonical):]
        return name

    def set_package_name(self, name):
        self._package_name = name or ""
        self.update_partition_progress("")
        val = self._display_package_name() or "\u2014"
        if hasattr(self, "_prep_pkg_label"):
            self._prep_pkg_label.setText(val)
        if hasattr(self, "_wait_pkg_label"):
            self._wait_pkg_label.setText(val)
        if hasattr(self, "_flash_pkg_label"):
            self._flash_pkg_label.setText(val)
        if hasattr(self, "_download_pkg_label"):
            self._download_pkg_label.setText(val)
        self._pkg_row.set_value(val)
        self._relayout_for_text()

    def _connect_model_text(self):
        from ..config import device_label_for_model
        return device_label_for_model(self._model) if self._model else tr("device_fallback_word")

    def set_waiting_device(self):
        self._wait_copy = "connect"
        self._wait_status.set_status("idle")
        prompt = self._connect_device_prompt_text()
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(prompt)
        self._wait_banner.set_type("info")
        self._wait_banner_key = "connect"
        self._wait_banner.set_key("flash_connect_device_prompt", model=self._connect_model_text())
        self._conn_value_key = "flash_conn_waiting"
        self._conn_row.set_value(tr("flash_conn_waiting"))
        self._set_determinate(self._wait_progress_bar, 0)

    def set_searching(self):
        self._wait_banner.set_type("info")
        self._wait_banner_key = "searching"
        self._wait_status.set_status("idle")

    def set_detected(self):
        self._wait_status.set_status("connected")
        self._wait_banner.set_type("success")
        self._wait_banner_key = "ready"
        self._wait_banner.set_key("flash_banner_ready")

    def set_device_flashing(self):
        self._wait_status.set_status("flashing")
        self._flash_banner.set_type("info")
        self._flash_banner.set_key("flash_banner_flashing")
        self._conn_value_key = "flash_conn_connected"
        self._dev_value_key = "status_connected"
        self._conn_row.set_value(tr("flash_conn_connected"))
        self._dev_row.set_value(tr("status_connected"))
        self._warning.setVisible(self._flash_warning_wanted())

    def _flash_warning_wanted(self) -> bool:
        """'Do not unplug' applies only while the write is running.

        Not once the install has completed, and not during the Rockbox themes
        and fonts stage, which runs after the flash has finished.
        """
        if getattr(self, "_failed", False) or getattr(self, "_complete", False):
            return False
        return not getattr(self, "_themes_stage_active", False)

    def set_device_done(self):
        self.update_partition_progress("")
        self.dismiss_prompt_stop()
        self._complete = True
        self._failed = False
        self._warning.setVisible(False)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(True)
            self._cancel_btn.setEnabled(False)
            self._cancel_btn.set_symbol("back")
            self._cancel_btn.setToolTip("")
            self._cancel_btn.setAccessibleName("")
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(True)
            self._done_mark.setEnabled(True)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
        if hasattr(self, "_flash_head") and hasattr(self._flash_head, "_sync_chips_pairing"):
            self._flash_head._sync_chips_pairing()
        source = getattr(self, "_release_source", None)
        if source is not None:
            self.set_release_icon(source, complete=True)
        self._flash_banner.set_type("success")
        self._flash_banner.set_key("flash_banner_done")
        self._wait_status.set_status("complete")
        self._progress_bar.setValue(100)
        self._eta_label.setText("")
        self._partition_progress_label.setVisible(False)
        if getattr(self, "_rockbox_site_themes", False):
            if hasattr(self, "_rockbox_site_widget"):
                self._rockbox_site_widget.setVisible(True)
            if hasattr(self, "_rockbox_site_btn"):
                self._rockbox_site_btn.setVisible(True)

    def update_prep_progress(self, percent):
        self._set_determinate(self._prep_progress, percent)

    def update_progress(self, percent):
        percent = int(percent or 0)
        if (
            getattr(self, "_wait_copy", "") == "please_wait"
            and percent <= 0
            and self._stack.currentWidget() is self._waiting_view
        ):
            self._set_indeterminate(self._wait_progress_bar)
            return
        if percent > 0 and getattr(self, "_wait_copy", "") == "please_wait":
            self._wait_copy = "stage"
        self._set_determinate(self._progress_bar, percent)
        if self._stack.currentWidget() is self._waiting_view and not self._prompt_only:
            self._set_determinate(self._wait_progress_bar, percent)

    def update_step(self, step_key):
        self._step_key = step_key
        if getattr(self, "_stop_open", False):
            self._apply_stop_hold()
            return
        self._step_label.setText(tr(step_key))
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.refresh()
        self._notify_geometry_change()

    _ACTION_HOLD_MS = 1000

    def update_partition_progress(self, text: str) -> None:
        """Show the backend's current partition percent on the install card."""
        label = getattr(self, "_partition_progress_label", None)
        if label is None:
            return
        text = str(text or "")
        if not text:
            label.clear()
            label.setVisible(False)
            self._notify_geometry_change()
            return
        label.setText(text)
        if getattr(self, "_complete", False) or getattr(self, "_failed", False):
            label.setVisible(False)
            return
        was_hidden = label.isHidden()
        label.setVisible(True)
        if was_hidden:
            self._notify_geometry_change()

    def update_action(self, text):
        """Show a status line without flicker.

        Console tools emit status text many times a second. A new line is held
        on screen for ``_ACTION_HOLD_MS`` before it replaces the current one, and
        a change that only moves digits (a percentage) updates the label in place
        with no layout pass. Without this, the line clears and reappears, the
        window shrinks and grows, and the progress bar gets cropped.
        """
        import re
        import time

        text = text or ""
        shown = getattr(self, "_action_shown_text", None)
        if shown is None:
            self._action_apply_text(text)
            self._action_shown_at = time.monotonic()
            self._action_shown_text = text
            return

        if re.sub(r"\d+", "#", text) == re.sub(r"\d+", "#", shown):
            # Only the numbers moved: no hold, no relayout.
            self._action_shown_text = text
            self._action_label.setText(text)
            self._action_pending_text = None
            return

        if text == shown:
            self._action_pending_text = None
            return

        elapsed = (time.monotonic() - getattr(self, "_action_shown_at", 0.0)) * 1000.0
        if elapsed >= self._ACTION_HOLD_MS and not getattr(self, "_action_pending_text", None):
            self._action_apply_text(text)
            self._action_shown_at = time.monotonic()
            self._action_shown_text = text
            return

        # Hold: remember the newest line and apply it once the hold ends.
        self._action_pending_text = text
        timer = getattr(self, "_action_hold_timer", None)
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._action_flush_pending)
            self._action_hold_timer = timer
        if not timer.isActive():
            remaining = max(0, int(self._ACTION_HOLD_MS - elapsed))
            timer.start(remaining)

    def _action_flush_pending(self):
        import time

        text = getattr(self, "_action_pending_text", None)
        self._action_pending_text = None
        if text is None or text == getattr(self, "_action_shown_text", None):
            return
        self._action_apply_text(text)
        self._action_shown_at = time.monotonic()
        self._action_shown_text = text

    def _action_apply_text(self, text):
        self._action_label.setText(text)
        if text and not getattr(self, "_stop_open", False) and not getattr(self, "_failed", False) and not getattr(self, "_themes_stage_active", False):
            if hasattr(self, "_step_label"):
                self._step_label.setText(text)
            flow = getattr(self, "_status_flow", None)
            if flow is not None:
                flow.refresh()
            self._notify_geometry_change()

    def update_time(self, elapsed, eta):
        self._elapsed_row.set_value(elapsed)
        self._eta_row.set_value(eta)
        self._refresh_eta_label(eta)
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.refresh()
        self._notify_geometry_change()

    def set_prep_eta_seconds(self, seconds: int) -> None:
        """The Device Prep stage's own remaining time, in whole seconds.

        The install half is already published to the card as ``eta`` through
        ``update_time``; this hands the prep half to the same label without
        wiping it. ``0`` means the prep stage has not produced a number yet,
        which is the honest state until it does.
        """
        try:
            self._prep_eta_seconds = max(0, int(seconds))
        except (TypeError, ValueError):
            self._prep_eta_seconds = 0
        self._refresh_eta_label(self._eta_row.value())

    def _refresh_eta_label(self, eta_str: str) -> None:
        friendly = self._format_eta_with_prep(eta_str)
        self._eta_label.setText(f"— {friendly}" if friendly else "")
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.refresh()
        self._notify_geometry_change()

    def _format_eta_with_prep(self, eta_str: str) -> str:
        install_eta = self._parse_eta_seconds(eta_str)
        prep_eta = getattr(self, "_prep_eta_seconds", 0)
        if install_eta is None and prep_eta <= 0:
            return ""
        parts = []
        if install_eta is not None:
            parts.append(format_eta_seconds(install_eta))
        if prep_eta > 0:
            parts.append(format_eta_seconds(prep_eta))
        return "\n".join(part for part in parts if part)

    @staticmethod
    def _parse_eta_seconds(eta_str: str):
        if not eta_str or eta_str in ("--:--", "-"):
            return None
        try:
            minutes, seconds = (int(part) for part in eta_str.split(":", 1))
        except (TypeError, ValueError):
            return None
        total = minutes * 60 + seconds
        return total if total > 0 else None

    @staticmethod
    def _format_friendly_eta(eta_str: str) -> str:
        """Format an ETA like '00:53' into localized 'About 53 seconds remaining'."""
        if not eta_str or eta_str in ("--:--", "-"):
            return ""
        try:
            parts = eta_str.split(":")
            if len(parts) == 2:
                mins, secs = int(parts[0]), int(parts[1])
                total_secs = mins * 60 + secs
                if total_secs <= 0:
                    return ""
                if total_secs < 60:
                    key = "flash_eta_second_one" if total_secs == 1 else "flash_eta_seconds_many"
                    return tr(key).format(n=total_secs)
                m = round(total_secs / 60)
                m = max(m, 1)
                key = "flash_eta_minute_one" if m == 1 else "flash_eta_minutes_many"
                return tr(key).format(n=m)
        except Exception:
            pass
        return tr("flash_eta_remaining_fallback").format(eta=eta_str)

    def retranslate(self):
        for label, key in getattr(self, "_headings", []):
            label.setText(tr(key))
        if hasattr(self, "_package_name"):
            self.set_package_name(self._package_name)
        if hasattr(self, "_progress_bar"):
            self._progress_bar.update()
        if hasattr(self, "_wait_progress_bar"):
            self._wait_progress_bar.update()
        if hasattr(self, "_download_heading"):
            self._download_heading.setText(tr("flash_download_in_progress"))
        if hasattr(self, "_download_cancel_btn"):
            self._download_cancel_btn.setToolTip(tr("flash_btn_cancel"))
        copy = getattr(self, "_wait_copy", "")
        if hasattr(self, "_wait_prompt_label") and (self._prompt_only or copy == "power_off"):
            self._wait_prompt_label.setText(self._power_off_prompt_text())
        elif hasattr(self, "_wait_prompt_label") and copy == "please_wait_connecting":
            self._wait_prompt_label.setText(self._wait_connecting_prompt_text())
        elif hasattr(self, "_wait_prompt_label") and copy == "please_wait":
            self._wait_prompt_label.setText(tr("flash_please_wait"))
        elif hasattr(self, "_wait_prompt_label") and copy == "entering_password":
            self._wait_prompt_label.setText(tr("install_linux_entering_password"))
        elif hasattr(self, "_wait_prompt_label") and copy in ("connect", ""):
            self._wait_prompt_label.setText(self._connect_device_prompt_text())
        elif hasattr(self, "_wait_prompt_label") and copy == "stage" and self._step_key:
            self._wait_prompt_label.setText(tr(self._step_key))
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._update_paperclip_hint()
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setText(tr("flash_btn_retry"))
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setToolTip(tr("btn_next"))
            self._wait_continue_btn.setAccessibleName(tr("btn_next"))
        self._prep_banner.retranslate()
        if self._wait_banner_key == "connect":
            self._wait_banner.set_key("flash_connect_device_prompt", model=self._connect_model_text())
        elif self._wait_banner_key == "please_wait":
            self._wait_banner.set_key("flash_please_wait")
        else:
            self._wait_banner.retranslate()
        if hasattr(self, "_theme_yes_btn"):
            self._theme_yes_btn.setText(tr("rockbox_running_yes"))
        if hasattr(self, "_theme_no_btn"):
            self._theme_no_btn.setText(tr("rockbox_running_no"))
        if hasattr(self, "_theme_browse_btn"):
            self._theme_browse_btn.setText(tr("rockbox_step_select_player_folder"))
            self._theme_browse_btn.setToolTip(tr("rockbox_step_select_player_folder"))
        if hasattr(self, "_theme_fallback_next_btn"):
            self._theme_fallback_next_btn.setText(tr("rockbox_step_select_player_folder"))
            self._theme_fallback_next_btn.setToolTip(tr("rockbox_step_select_player_folder"))
        if hasattr(self, "_theme_finish_btn"):
            self._theme_finish_btn.setText(tr("rockbox_btn_finish"))
        if hasattr(self, "_theme_recover_link"):
            self._theme_recover_link.setText(f'<a href="recover">{tr("rockbox_recover_troubleshoot")}</a>')
        if hasattr(self, "_theme_trouble_recover_btn"):
            self._theme_trouble_recover_btn.setText(tr("rockbox_recover_troubleshoot"))
        if hasattr(self, "_theme_back_btn"):
            self._theme_back_btn.setText(tr("rockbox_wizard_back"))
            self._theme_back_btn.setToolTip(tr("rockbox_wizard_back"))
        if hasattr(self, "_download_drivers_btn"):
            self._download_drivers_btn.setText(tr("settings_download_drivers_btn"))
        self._flash_banner.retranslate()
        self._guide_title.setText(tr("flash_guide_title"))
        for key, lbl in self._guide_texts:
            lbl.setText(tr(key))
        self._warning.setText(tr("flash_warning"))
        if hasattr(self, "_intro"):
            # The block keeps whichever wording it was set to: a language change
            # must not turn a finished install's appeal back into the generic
            # project pitch.
            self._render_appeal_copy()
        if hasattr(self, "_complete_note"):
            self._render_completion_note()
        self._style_done_button()
        self._apply_guide_highlight()
        if self._prep_step_key:
            self._prep_step.setText(tr(self._prep_step_key))
        if self._step_key:
            self._step_label.setText(tr(self._step_key))
        if self._conn_value_key:
            self._conn_row.set_value(tr(self._conn_value_key))
        if self._dev_value_key:
            self._dev_row.set_value(tr(self._dev_value_key))
        if hasattr(self, "_prep_cancel_btn"):
            self._prep_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._update_paperclip_hint()
        self._wait_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._retranslate_stop_buttons()
        self._apply_stop_hold()
        self._wait_status.retranslate()
        self._prep_card.retranslate()
        self._progress_card.retranslate()
        self._status_card.retranslate()
        self._conn_row.retranslate()
        self._dev_row.retranslate()
        self._pkg_row.retranslate()
        self._elapsed_row.retranslate()
        self._eta_row.retranslate()
        if hasattr(self, "_eta_label") and hasattr(self, "_eta_row"):
            self._refresh_eta_label(self._eta_row.value())

    def _seal_status_labels(self):
        """Repaint status lines from the live theme. No opaque backdrop."""
        names = (
            "_prep_pkg_label", "_prep_step", "_wait_pkg_label", "_wait_prompt_label",
            "_flash_pkg_label", "_step_label", "_eta_label", "_warning",
            "_download_pkg_label", "_download_status_label", "_download_heading",
            "_appeal_label", "_appeal_body", "_paperclip_hint",
        )
        for name in names:
            label = getattr(self, name, None)
            if label is None:
                continue
            if name == "_step_label" and bool(getattr(self, "_failed", False)):
                # The failure line carries the danger colour; a theme refresh
                # must not flatten it while the failure is still on screen.
                continue
            if name in ("_download_heading",):
                _style_card_heading(label)
            else:
                _apply_card_text(label)
        for heading, _key in getattr(self, "_headings", []):
            _style_card_heading(heading)

    def refresh_theme(self):
        from .dark import install_progress_readability

        self._seal_status_labels()
        for bar_name in ("_prep_progress", "_wait_progress_bar", "_progress_bar", "_download_progress"):
            bar = getattr(self, bar_name, None)
            if bar is not None:
                install_progress_readability(bar)
        source = getattr(self, "_release_source", None)
        self.set_release_icon(
            source if source is not None else QPixmap(),
            complete=bool(getattr(self, "_release_complete", False)),
        )
        for btn in (
            getattr(self, "_cancel_btn", None),
            getattr(self, "_wait_cancel_btn", None),
            getattr(self, "_prep_cancel_btn", None),
            getattr(self, "_download_cancel_btn", None),
            getattr(self, "_wait_continue_btn", None),
            getattr(self, "_flash_retry_btn", None),
            getattr(self, "_done_mark", None),
            getattr(self, "_theme_step1_next_btn", None),
            getattr(self, "_theme_fallback_next_btn", None),
            getattr(self, "_theme_trouble_recover_btn", None),
            getattr(self, "_theme_back_btn", None),
            getattr(self, "_stop_continue", None),
            getattr(self, "_stop_back", None),
            getattr(self, "_stop_continue", None),
            getattr(self, "_stop_back", None),
        ):
            if btn is not None and hasattr(btn, "rebuild_arrow"):
                btn.rebuild_arrow()
        self._update_paperclip_hint()
        self.sync_text_fit()

    def _style_done_button(self) -> None:
        button = getattr(self, "_done_mark", None)
        if not isinstance(button, QPushButton):
            return
        if hasattr(button, "rebuild_arrow"):
            button.rebuild_arrow()
        button.setToolTip(tr("install_done_dismiss"))
        button.setAccessibleName(tr("install_done_dismiss"))
        button.setCursor(Qt.ArrowCursor)

    def on_install_done(self, callback) -> None:
        self._install_done_cb = callback

    def _on_install_done(self) -> None:
        if callable(getattr(self, "_install_done_cb", None)):
            self._install_done_cb()

    def on_cancel(self, callback):
        self._cancel_callback = callback

    def on_cancel_wait(self, callback):
        self._cancel_wait_callback = callback

    def _on_cancel(self):
        if getattr(self, "_failed", False) or getattr(self, "_prompt_only", False):
            callback = getattr(self, "_cancel_callback", None) or getattr(self, "_cancel_wait_callback", None)
            if callable(callback):
                callback()
            return
        self.prompt_stop(tr_brand("install_stop_check"), on_continue=self._cancel_callback)

    def _on_cancel_wait(self):
        """Stop the run. Before anything is written this needs no confirmation.

        At the power-off step the player has not been touched: the chip goes
        straight back to Select Software. Once a run is under way the same
        chip asks first, because there the target is already mid-write.
        """
        if getattr(self, "_prompt_only", False) or getattr(self, "_failed", False):
            callback = getattr(self, "_cancel_wait_callback", None) or getattr(self, "_cancel_callback", None)
            if callable(callback):
                callback()
            return
        self.prompt_stop(tr_brand("install_stop_check"), on_continue=self._cancel_wait_callback)

    def text_column_width(self) -> int:
        """Wrap width for the progress copy. Buttons on their own row can widen it."""
        width = INSTALL_TEXT_WIDTH
        if getattr(self, "_stop_open", False) and hasattr(self, "_stop_continue"):
            buttons = (
                self._stop_continue.sizeHint().width()
                + self._stop_back.sizeHint().width()
                + 24
            )
            width = max(width, buttons)
        return width

    def _status_row_reserve(self) -> int:
        """Pixels the round next-step chip takes out of the status line.

        The chip sits on the status line, so the copy beside it wraps that much
        earlier. Zero while the chip is hidden, which is most of the run.
        """
        button = getattr(self, "_wait_continue_btn", None)
        if button is None or button.isHidden():
            return 0
        return button.width() + 10

    def sync_text_fit(self) -> None:
        """Give wrapping labels a real width so their size hint includes every line."""
        width = self.text_column_width()
        names = (
            "_prep_pkg_label", "_prep_step",
            "_wait_pkg_label", "_wait_prompt_label", "_wait_mode_hint",
            "_flash_pkg_label", "_step_label", "_eta_label",
            "_partition_progress_label",
            "_download_pkg_label", "_download_status_label", "_paperclip_hint",
        )
        for name in names:
            label = getattr(self, name, None)
            if isinstance(label, _FitLabel):
                label.set_column_width(width)
        row_width = max(140, width - self._status_row_reserve())
        for name in ("_wait_prompt_label", "_wait_mode_hint"):
            label = getattr(self, name, None)
            if isinstance(label, _FitLabel):
                label.set_column_width(row_width)
        warning = getattr(self, "_warning", None)
        if isinstance(warning, QLabel):
            warning.setWordWrap(True)
            target_min_w = min(width, INSTALL_TEXT_WIDTH)
            if warning.minimumWidth() != target_min_w:
                warning.setMinimumWidth(target_min_w)
            wrapped = warning.heightForWidth(width)
            if wrapped > 0 and warning.minimumHeight() != wrapped:
                warning.setMinimumHeight(wrapped)

    def content_height_hint(self, width: int | None = None) -> int:
        """Accurately compute total vertical space needed by the active view inside FlashPage."""
        self.sync_text_fit()
        margins = self.layout().contentsMargins() if self.layout() else None
        top_m = margins.top() if margins else 8
        bottom_m = margins.bottom() if margins else 8
        cur_view = self._stack.currentWidget() if hasattr(self, "_stack") else None
        if cur_view is None:
            return self.sizeHint().height()

        view_w = width if width is not None and width > 0 else self.width()
        if view_w <= 0:
            view_w = INSTALL_TEXT_WIDTH + 140

        h = 0
        if cur_view is self._flashing_view:
            card_h = self._progress_card.sizeHint().height()
            warn_h = 0
            if hasattr(self, "_warning") and self._warning.isVisible():
                warn_h = max(self._warning.sizeHint().height(), self._warning.heightForWidth(self.text_column_width())) + 8
            appeal_h = 0
            if hasattr(self, "_appeal") and self._appeal.isVisible():
                appeal_h = self._appeal.sizeHint().height() + 8
            h = card_h + warn_h + appeal_h
        elif cur_view is self._waiting_view:
            card_h = self._wait_card.sizeHint().height()
            hint_h = 0
            if (
                hasattr(self, "_paperclip_hint_host")
                and self._paperclip_hint_host.isVisible()
            ):
                if not self._wait_card.isAncestorOf(self._paperclip_hint_host):
                    hint_h = self._paperclip_hint_host.sizeHint().height() + 6
                else:
                    head_h = self._wait_head.sizeHint().height() if hasattr(self, "_wait_head") else 48
                    prompt_h = self._wait_prompt_label.sizeHint().height() if hasattr(self, "_wait_prompt_label") else 40
                    min_needed = head_h + prompt_h + self._paperclip_hint_host.minimumHeight() + 48
                    card_h = max(card_h, min_needed)
            h = card_h + hint_h
        elif cur_view is self._preparing_view:
            h = self._prep_card.sizeHint().height()
        elif cur_view is self._downloading_view:
            h = self._download_card.sizeHint().height()
        else:
            h = cur_view.sizeHint().height()

        return h + top_m + bottom_m + 8

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        return self.content_height_hint(width)

    def _notify_geometry_change(self) -> None:
        """Debounced request to MainWindow to adjust window height to fit contents."""
        timer = getattr(self, "_geom_notify_timer", None)
        if timer is None:
            from PySide6.QtCore import QTimer
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(40)
            def _trigger():
                win = self.window()
                adjust = getattr(win, "_adjust_window_geometry", None)
                if callable(adjust) and win is not self:
                    adjust()
            timer.timeout.connect(_trigger)
            self._geom_notify_timer = timer
        if not timer.isActive():
            timer.start()

    def _relayout_for_text(self) -> None:
        self.sync_text_fit()
        self.updateGeometry()
        self._notify_geometry_change()

    def _ensure_stop_buttons(self) -> None:
        if hasattr(self, "_stop_continue"):
            return
        # Circular glyphs, not words: a left arrow for "back to it" and a cross
        # for "stop the install". The accent chip comes first, so the coloured
        # one sits on the left and the two arrows point away from each other
        # instead of at each other. Both carry the wording as the hover tip and
        # as the accessible name.
        self._stop_back = _RoundNextButton(symbol="back", accent=True)
        self._stop_continue = _RoundNextButton(symbol="cancel", accent=False)
        for button, key in (
            (self._stop_continue, "install_interrupt_cancel"),
            (self._stop_back, "install_interrupt_back"),
        ):
            button.setToolTip(tr(key))
            button.setAccessibleName(tr(key))
        self._stop_actions = _GlyphActions([self._stop_back, self._stop_continue])
        self._stop_actions.hide()
        self._stop_host = None
        self._stop_continue.clicked.connect(self._accept_stop)
        self._stop_back.clicked.connect(self._reject_stop)
        self._stop_open = False
        self._stop_loop = None
        self._stop_result = False
        self._stop_callback = None
        self._stop_message = ""

    def _retranslate_stop_buttons(self) -> None:
        if not hasattr(self, "_stop_continue"):
            return
        for button, key in (
            (self._stop_continue, "install_interrupt_cancel"),
            (self._stop_back, "install_interrupt_back"),
        ):
            button.setToolTip(tr(key))
            button.setAccessibleName(tr(key))

    def _place_stop_actions(self, column, row=None) -> None:
        """Buttons sit beside the message while the card is wide enough.

        Right of the copy when there is horizontal room, in the order they were
        given; otherwise the same buttons stack under the copy, in that same
        order, as a traffic-light column.
        """
        card = getattr(self, "_stop_card", None)
        room = card.width() if card is not None and card.width() > 0 else 0
        count = len(getattr(self._stop_actions, "_buttons", ())) or 2
        buttons_w = count * ROUND_NEXT_BUTTON_PX + (count - 1) * 10
        # Copy column, the buttons, and the icon plus card padding beside them.
        beside = row is not None and room >= self.text_column_width() + buttons_w + 80
        target = row if beside else column
        host = getattr(self, "_stop_host", None)
        if host is not None and host is not target:
            host.removeWidget(self._stop_actions)
            host.addStretch(1)
        if self._stop_host is not target:
            if beside:
                # Just left of the card's own cancel chip, which is hidden while
                # the stop check is open.
                target.insertWidget(max(0, target.count() - 1), self._stop_actions)
            else:
                # Under the copy, against the right edge of the column.
                target.addWidget(self._stop_actions, 0, Qt.AlignRight)
            self._stop_host = target
        self._stop_actions.set_stacked(not beside)
        self._stop_actions.show()
        self._stop_continue.show()
        self._stop_back.show()

    def _stop_target(self):
        """The progress card that should host the stop check."""
        view = self._stack.currentWidget()
        if view is self._downloading_view:
            return (
                self._download_card,
                self._download_row,
                self._download_status_label,
                self._download_progress,
                self._download_cancel_btn,
                getattr(self, "_download_next_btn", None),
                self._download_col,
            )
        if view is self._preparing_view:
            return (
                self._prep_card,
                self._prep_row,
                self._prep_step,
                self._prep_progress,
                self._prep_cancel_btn,
                getattr(self, "_prep_next_btn", None),
                self._prep_col,
            )
        if view is self._waiting_view:
            return (
                self._wait_card,
                self._wait_row,
                self._wait_prompt_label,
                self._wait_progress_bar,
                self._wait_cancel_btn,
                self._wait_continue_btn,
                self._wait_col,
            )
        return (
            self._progress_card,
            self._flash_row,
            self._step_label,
            self._progress_bar,
            self._cancel_btn,
            getattr(self, "_flash_retry_btn", None),
            self._flash_col,
        )

    def dismiss_prompt_stop(self) -> None:
        """Dismiss any active cancellation prompt without stopping the install."""
        if getattr(self, "_stop_open", False):
            self._dismiss_stop(False)

    def prompt_stop(self, message: str, on_continue=None, *, wait: bool = False) -> bool:
        """Ask, inside the progress card, before stopping a download or install.

        Cancel Install and Back both stop the run when this is the interruption
        card. ``wait`` blocks until one of those buttons is chosen, for the
        window-close path, where Back leaves the run in place.
        """
        if getattr(self, "_stop_open", False):
            if wait:
                self._finish_stop(True)
                return True
            return False
        self._ensure_stop_buttons()
        card, row, label, bar, cancel, extra, column = self._stop_target()
        self._stop_open = True
        self._update_paperclip_hint()
        self._stop_message = message or ""
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.set_forced_stacked(True)
        self._stop_callback = on_continue
        self._stop_card = card
        self._stop_label = label
        self._stop_bar = bar
        self._stop_cancel = cancel
        self._stop_extra = extra
        self._stop_saved = (
            label.text() if label is not None else "",
            label.wordWrap() if label is not None else False,
            bar.isVisible() if bar is not None else False,
            cancel.isVisible() if cancel is not None else False,
            extra.isVisible() if extra is not None else False,
        )
        if label is not None:
            label.setWordWrap(True)
            label.setText(self._stop_message)
        # The progress bar stays visible during the stop check so the card
        # does not suffer a layout shift while the run is still in progress.
        if cancel is not None:
            cancel.hide()
        if extra is not None:
            extra.hide()
        self._retranslate_stop_buttons()
        self._place_stop_actions(column, row)
        self._relayout_for_text()
        fade_in(card)
        if not wait:
            return False
        loop = QEventLoop(self)
        self._stop_loop = loop
        self._stop_result = False
        loop.exec()
        self._stop_loop = None
        return bool(self._stop_result)

    def _apply_stop_hold(self) -> None:
        """Keep the sanity check in place if a progress update repaints the card."""
        if not getattr(self, "_stop_open", False):
            return
        label = getattr(self, "_stop_label", None)
        if label is not None:
            label.setText(self._stop_message)
        cancel = getattr(self, "_stop_cancel", None)
        if cancel is not None:
            cancel.hide()
        extra = getattr(self, "_stop_extra", None)
        if extra is not None:
            extra.hide()
        actions = getattr(self, "_stop_actions", None)
        if actions is not None:
            actions.show()
        if hasattr(self, "_stop_continue"):
            self._stop_continue.show()
            self._stop_back.show()

    def _finish_stop(self, accepted: bool) -> None:
        """✕ stops the run; ← puts the check away and leaves the run alone.

        Only the cross confirms the stop. The back arrow is the way out of the
        check without touching the install, for a user who opened it by
        accident — it must never stop anything.
        """
        callback = getattr(self, "_stop_callback", None)
        waiting = self._stop_loop is not None and self._stop_loop.isRunning()
        self._dismiss_stop(accepted)
        if not accepted and not waiting:
            return
        # The close confirmation waits for either button; the caller reads the
        # result from ``_stop_result`` rather than the callback.
        if waiting or not callable(callback):
            return
        callback()

    def _accept_stop(self) -> None:
        self._finish_stop(True)

    def _reject_stop(self) -> None:
        self._finish_stop(False)

    def _dismiss_stop(self, accepted: bool) -> None:
        if not getattr(self, "_stop_open", False):
            return
        self._stop_result = accepted
        self._stop_open = False
        self._update_paperclip_hint()
        saved = getattr(self, "_stop_saved", None)
        label = getattr(self, "_stop_label", None)
        bar = getattr(self, "_stop_bar", None)
        cancel = getattr(self, "_stop_cancel", None)
        extra = getattr(self, "_stop_extra", None)
        card = getattr(self, "_stop_card", None)
        if saved:
            text, wrap, bar_visible, cancel_visible, extra_visible = saved
            if label is not None:
                label.setWordWrap(wrap)
                label.setText(text)
            if bar is not None:
                bar.setVisible(bar_visible)
            if cancel is not None:
                cancel.setVisible(cancel_visible)
            if extra is not None:
                extra.setVisible(extra_visible)
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.set_forced_stacked(False)
            flow.refresh()
        host = getattr(self, "_stop_host", None)
        actions = getattr(self, "_stop_actions", None)
        if host is not None and actions is not None:
            host.removeWidget(actions)
        if actions is not None:
            actions.hide()
            actions.setParent(None)
        self._stop_host = None
        loop = getattr(self, "_stop_loop", None)
        if loop is not None and loop.isRunning():
            loop.quit()
        if card is not None:
            fade_in(card)
