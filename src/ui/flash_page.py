"""Flash page, modern OS software update in-progress display (macOS / iOS / Windows Fluent style)."""

import logging
from pathlib import Path
import re
import sys

logger = logging.getLogger(__name__)

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QEventLoop,
    QPropertyAnimation,
    QRect,
    QSettings,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..themes_journey import ThemesJourney
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
from .support_appeal import (
    COMPLETION_BODY_KEYS,
    COMPLETION_KEYS,
    ONE_MORE_THING_KEYS,
    THEMES_COMPLETION_KEYS,
    THEME_STANDALONE_COMPLETION_KEYS,
    DeveloperPortrait,
    SupportIntro,
)
from .widgets import (
    Banner,
    Card,
    CollapsibleWidget,
    CurrentPageStack,
    InfoRow,
    InstallProgress,
    StatusTag,
    fade_in,
)
from .dark import T, apply_explicit_foreground, link_html, page_top_margin


#: The pre-install heading — ``Get your <model> ready to install
#: <software>`` or ``Get ready to install <software>``. It is the install
#: card's opening copy, never a title to read back from a card while the
#: package is being downloaded or written.
_READY_HEADING = re.compile(
    r"^get\s+your\s+(?P<model>.+?)\s+ready\s+to\s+install\s+(?P<software>.+)$"
    r"|^get\s+ready\s+to\s+install\s+(?P<software_alt>.+)$",
    re.IGNORECASE,
)


def _themed_link(href: str, text: str, *, underline: bool = False) -> str:
    """An in-app link in the theme foreground, with no visited or clicked tint.

    ``underline`` is for a link that sits on its own line rather than inside a
    sentence: the completion letter's "What to do if it doesn't start up" reads
    as an action there, and the platform's link tint is already suppressed.
    """
    html = link_html(text, bold=False).replace('href="#"', f'href="{href}"', 1)
    if underline:
        html = html.replace("text-decoration: none;", "text-decoration: underline;", 1)
    return html


def _styled_prompt_links(html: str) -> str:
    """Give anchors in translated prompt copy the live host text colour.

    A catalog string may carry a plain anchor (the Keep Data explainer's "Try
    anyway"). Qt would paint that in the document's own link colour, which is
    not the colour the sentence around it follows; an anchor that already has
    a style keeps it.
    """
    from .dark import theme_text_color

    if "<a " not in (html or ""):
        return html or ""
    style = (
        f"color: {theme_text_color().name()}; font-weight: 700;"
        " text-decoration: none; background: transparent;"
    )
    return re.sub(
        r"<a\s+(?![^>]*\bstyle=)([^>]*)>",
        lambda match: f'<a {match.group(1)} style="{style}">',
        html,
    )


def _hint_text_link(href: str, text: str) -> str:
    """An action written into hint copy, dimmer than it and underlined.

    Used for the acknowledgement that puts a read-once hint away and for the
    link that opens it: both are actions on the hint rather than more copy, and
    an affordance painted in the sentence's own colour and weight reads as the
    last words of the sentence.
    """
    from .dark import theme_placeholder_text_color

    try:
        colour = theme_placeholder_text_color().name()
    except Exception:
        colour = ""
    style = "text-decoration: underline; border: none; background: transparent;"
    if colour:
        style = f"color: {colour}; {style}"
    return f'<a href="{href}" style="{style}">{text}</a>'


#: Settings group for the install card's "read it, put it away" hints. One key
#: per hint, so dismissing the paperclip tip never hides the microSD line.
#: A dismissal lasts until the user resets the app with R: that clears this
#: group with the rest of the settings, so the tips come back for a fresh start.
_HINT_DISMISS_GROUP = "dismissed_install_hints"


def _hint_dismissed(name: str, settings=None) -> bool:
    """True when the user dismissed ``name`` with its own Got it link."""
    store = settings if settings is not None else QSettings("innioasis", "updater")
    return bool(store.value(f"{_HINT_DISMISS_GROUP}/{name}", False, type=bool))


def _set_hint_dismissed(name: str, dismissed: bool = True, settings=None) -> None:
    """Remember, across launches, that ``name`` has been read."""
    store = settings if settings is not None else QSettings("innioasis", "updater")
    store.setValue(f"{_HINT_DISMISS_GROUP}/{name}", bool(dismissed))


#: Settings group for hints that opened once without being asked for. It is
#: kept apart from the dismissal key so a past Got it never re-triggers the
#: one automatic showing, and the automatic showing never counts as a dismissal.
_HINT_SEEN_GROUP = "seen_install_hints"


def _hint_seen(name: str, settings=None) -> bool:
    """True when ``name`` has already had its one automatic showing."""
    store = settings if settings is not None else QSettings("innioasis", "updater")
    return bool(store.value(f"{_HINT_SEEN_GROUP}/{name}", False, type=bool))


def _set_hint_seen(name: str, seen: bool = True, settings=None) -> None:
    """Remember, across launches, that ``name`` has been shown on its own once."""
    store = settings if settings is not None else QSettings("innioasis", "updater")
    store.setValue(f"{_HINT_SEEN_GROUP}/{name}", bool(seen))
from .icons import get_symbol_data_uri, get_symbol_icon, get_symbol_pixmap
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
ROCKBOX_BOOT_WAIT_MS = 120_000

#: Height of the paperclip diagram. Drawn 8% larger than the original 60 px so
#: the pin and the button it points at stay legible on a phone-sized photo.
_PAPERCLIP_HINT_PX = round(60 * 1.08)

#: The progress card's type scale. One rank per job, so a prompt and the hint
#: under it never read as two different kinds of text: the software title is
#: the small bold line, a question in the band is the only 14, and every
#: sentence of body copy — prompt, hint, explainer, reassurance — is 12.
_TITLE_PX = 13
_QUESTION_PX = 14
_BODY_PX = 12


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
        # Height is measured at this width. The column (and the minimum width
        # that sizes the window) stays ``_wrap_width``; a label the host has
        # already given its real width can tighten its reserved height without
        # moving anything. See :meth:`set_measure_width`.
        self._measure_width = INSTALL_TEXT_WIDTH
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
        self._measure_width = target_w
        if not self._single_line:
            self.setMinimumWidth(self._wrap_width)
        self._refresh_mode()
        self.updateGeometry()

    def set_measure_width(self, width: int) -> None:
        """Reserve the height this label needs at ``width``.

        A word-wrapped label inside a fixed column reserves the lines the
        column would need, which is far more than it draws once the card is
        wider than that column — the leftover becomes a gap under the text.
        Only the height follows ``width`` here: the column and its minimum
        width are untouched, so window sizing does not move.
        """
        target = max(1, int(width))
        if self._measure_width == target:
            return
        self._measure_width = target
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
            return QSize(self._wrap_width, self.heightForWidth(self._measures()))
        return QSize(self._wrap_width, self._wrapped_height(self._measures()))

    def minimumSizeHint(self):  # noqa: N802 (Qt naming)
        if self._single_line:
            return QSize(0, self.heightForWidth(self._wrap_width))
        return QSize(
            min(self._wrap_width, max(40, self._unbroken_width())),
            self._wrapped_height(self._measures()),
        )

    def _measures(self) -> int:
        return max(self._wrap_width, int(self._measure_width))

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
            self.setMinimumHeight(self._wrapped_height(self._measures()))
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


def _software_without_model(software: str, model: str) -> str:
    """The software name with a standalone model token removed.

    Completion copy names the player ("… is installed on your {model}"), and a
    release name can carry it too ("Rockbox for Y2"); filling both placeholders
    read as "Rockbox for Y2 is installed on your Y2.". Only a standalone token
    is dropped, so names like "Y2Player" are left alone, and a name that is
    nothing but the model is kept rather than blanked.
    """
    text = str(software or "").strip()
    token = str(model or "").strip()
    if not text or not token:
        return text
    word = rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])"
    if not re.search(word, text, re.IGNORECASE):
        return text
    # Drop the model together with the punctuation that framed it, so the
    # remaining words keep their spacing: "(Y2)", "– Y2", "for Y2", "Y2 –".
    stripped = text
    for pattern, repl in (
        (rf"[([\s]*{word}[)\]]\s*", " "),
        (rf"\bfor\s+{word}", " "),
        (rf"\s*[-–, ·,:/]\s*{word}", " "),
        (rf"{word}\s*[-–, ·,:/]?\s*", " "),
    ):
        stripped = re.sub(pattern, repl, stripped, flags=re.IGNORECASE)
    stripped = re.sub(r"\s{2,}", " ", stripped).strip(" -–, ·,:()[]/")
    return stripped or text


def _completion_software_name(name: str, model: str) -> str:
    """The release name a completion screen may name, from a title or heading.

    ``_package_name`` holds the install heading (``Get your Y2 ready to
    install Rockbox 2.5``) or a plain release name. Unwrapping the heading has
    to come first: stripping the model alone left ``Get your ready to install
    Rockbox 2.5``, which matched the generic-heading check and made the screen
    say "The software has been installed on your Y2" about a release it knew.
    """
    text = str(name or "").strip()
    if not text:
        return ""
    from .. import catalog

    heading_name = catalog.software_from_install_heading(text, model)
    if heading_name:
        return heading_name
    return _software_without_model(text, model)


def _style_card_heading(label) -> None:
    if label is None:
        return
    font = label.font()
    font.setPixelSize(_QUESTION_PX)
    font.setWeight(QFont.Weight.Bold)
    label.setFont(font)
    _apply_card_text(label)


def _status_label(text: str, *, strong: bool = False) -> _FitLabel:
    label = _FitLabel(text)
    font = label.font()
    font.setPixelSize(_TITLE_PX if strong else _BODY_PX)
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
# How long the chips take to travel from the row's edge to the question they
# answer, and back. Long enough to read as one control moving, short enough
# that the answer never waits on the animation.
_CHIP_INLINE_MS = 220
# QWIDGETSIZE_MAX: a maximum width that means "let the layout decide".
_UNLIMITED_WIDTH = 16777215
# Digits the no-progress countdown reserves in the connect sentence. Meeting a
# ticking number at its widest means the line is measured once, not every
# second, so the chips beside it settle instead of sliding on every tick.
_BAND_COUNTDOWN_DIGITS = 3
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
        # Absorbs whatever room the identity column does not need, so a chips
        # row that has slid in beside a question never shares that room out
        # around the title and pushes the words off the card's leading edge.
        self._tail_space = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )
        self.box.addItem(self._tail_space)
        self.band = None
        self._chips: list[_RoundNextButton] = []
        # While a yes/no question stands in the band's slot the chips belong
        # beside its words, not at the row's far edge. The identity column's
        # width carries them there and back (see set_band_inline).
        self._inline_content = None
        self._inline_target = None
        # The one-line width the card measured for the inline copy, kept so a
        # resize re-measures the same sentence instead of its live countdown.
        self._inline_line_width = None
        self._inline_anim = QPropertyAnimation(self.identity, b"maximumWidth", self)
        self._inline_anim.setDuration(_CHIP_INLINE_MS)
        self._inline_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._inline_anim.finished.connect(self._finish_inline_travel)

    def set_band(self, band: QWidget) -> None:
        """The progress band, directly under the title it reports on."""
        self.band = band
        self.identity_box.addWidget(band)

    def add_chip(self, chip: QWidget) -> None:
        """A round action chip, right of the title and the band.

        Centred on the progress band so the release icon, progress bar and
        round action buttons share one horizontal axis.
        """
        self.box.insertWidget(max(0, self.box.count() - 1), chip, 0, Qt.AlignVCenter)
        if isinstance(chip, _RoundNextButton):
            self._chips.append(chip)
            chip.installEventFilter(self)
        self._sync_chips_pairing()

    def set_band_inline(
        self,
        inline: bool,
        content: QWidget | None = None,
        *,
        line_width: int | None = None,
    ) -> None:
        """Slide the chips from the row's edge to just past the band's words.

        A single yes/no question and its two chips are one control, so while
        the card only asks something the chips sit immediately after the
        question instead of at the far edge of a wide card. The identity
        column's width is animated rather than swapped, so the pair travels
        there and back instead of jumping, and the band gets its room again
        once the question has been answered.

        ``line_width`` lets the card supply the one-line width it wants the
        chips held at, instead of the head measuring ``content``'s own text:
        a sentence with a ticking countdown is measured at the countdown's
        widest so the number can change inside a slot that never moves.
        """
        content = content if inline else None
        self._inline_line_width = line_width if content is not None else None
        target = (
            self._inline_band_width(content, line_width=self._inline_line_width)
            if content is not None
            else None
        )
        if target == self._inline_target and content is self._inline_content:
            return
        self._inline_content = content
        self._inline_target = target
        if target is None:
            self._travel_chips_to(self.width(), restore_unlimited=True)
        else:
            self._travel_chips_to(target, restore_unlimited=False)

    def _inline_band_width(self, content: QWidget, *, line_width: int | None = None) -> int:
        """Room the identity column needs for the chips to sit past its words."""
        text = content.text() if hasattr(content, "text") else ""
        if line_width:
            wanted = int(line_width)
        else:
            wanted = int(content.fontMetrics().horizontalAdvance(text)) if text else 0
        title = int(self.title.minimumSizeHint().width()) if self.title is not None else 0
        visible_chips = sum(1 for chip in self._chips if not chip.isHidden())
        available = (
            int(self.width())
            - _CARD_ICON_PX
            - _CARD_HEAD_GAP * 2
            - visible_chips * ROUND_NEXT_BUTTON_PX
        )
        target = max(wanted, title)
        if available > 0:
            target = min(target, available)
        return max(1, int(target))

    def _travel_chips_to(self, maximum: int, restore_unlimited: bool) -> None:
        """Animate the identity column's ceiling, and with it the chips' place."""
        maximum = max(1, int(maximum))
        start = int(self.identity.width())
        self._inline_anim.stop()
        # A column narrower than its words wraps them, so an inline column that
        # has to grow steps over instead of sliding: the sentence the chips are
        # moving aside for is never re-wrapped on the way.
        growing = maximum > start and not restore_unlimited
        if start <= 0 or growing or abs(start - maximum) <= 2 or not self.isVisible():
            self.identity.setMaximumWidth(_UNLIMITED_WIDTH if restore_unlimited else maximum)
            self.identity.updateGeometry()
            return
        self._inline_anim.setStartValue(start)
        self._inline_anim.setEndValue(maximum)
        self._inline_anim.start()

    def _finish_inline_travel(self) -> None:
        if self._inline_target is None:
            self.identity.setMaximumWidth(_UNLIMITED_WIDTH)
            self.identity.updateGeometry()

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
        if ev.type() == QEvent.Type.Resize and self._inline_content is not None:
            # A narrower card may no longer have room for the chips beside the
            # question, so re-measure the ceiling instead of leaving a stale one.
            target = self._inline_band_width(
                self._inline_content, line_width=self._inline_line_width
            )
            if target != self._inline_target:
                self._inline_target = target
                self._inline_anim.stop()
                self.identity.setMaximumWidth(target)
                self.identity.updateGeometry()
        return res

    def band_span(self) -> tuple[int, int]:
        """Vertical extent of the row's action line, in this row's coordinates.

        The band leads the head now, with the software title beneath it, so the
        line the icon and chips are centred on is the band's own row; the title
        is only the fallback when no band is showing.
        """
        self._sync_to_band()
        if self.band is not None and not self.band.isHidden():
            return (
                self.identity.y() + self.band.geometry().top(),
                self.identity.y() + self.band.geometry().bottom(),
            )
        return (
            self.identity.y() + self.title.geometry().top(),
            self.identity.y() + self.title.geometry().bottom(),
        )


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
        self.setCursor(Qt.PointingHandCursor)
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
            # The accent disc always takes a white glyph: the dark glyph read as
            # a hole punched in a light-coloured disc on the confirm/next/tick chips.
            disc = QColor(getattr(T(), "accent", "") or "#52b036")
            disc.setAlpha(_ACCENT_DISC_ALPHA)
            text = "#ffffff"
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

    def detach_native(self) -> None:
        """Hand the AppKit overlay back before this widget goes away.

        The AppKit button is a subview of the *window*, not part of Qt's widget
        tree, so destroying the widget does not remove it. A chip thrown away
        that way kept its button painted at the last frame it had — the notices
        list rebuilds its cards on every refresh, which left a stray ✕ over
        whatever replaced them.
        """
        native = getattr(self, "_native", None)
        if native is not None:
            try:
                native.detach()
            except Exception:
                logger.debug("could not detach the native chip", exc_info=True)
            self._native = None
        # A reused widget may attach again the next time it is shown.
        self._native_attempted = False

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


class _PaperclipHintHost(QWidget):
    """Host container for the paperclip instruction, diagram and tips.

    Implements heightForWidth and sizeHint dynamically across its child
    labels so the card and window expand to fit all lines and buttons without
    clipping on any platform.
    """

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt naming)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt naming)
        layout = self.layout()
        if layout is None:
            return self.sizeHint().height()
        w = max(1, int(width))
        h = 0
        visible_count = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            widget = item.widget() if item else None
            if widget is not None and not widget.isHidden():
                if widget.hasHeightForWidth():
                    item_h = widget.heightForWidth(w)
                else:
                    item_h = widget.sizeHint().height()
                h += item_h
                visible_count += 1
        if visible_count > 1:
            h += layout.spacing() * (visible_count - 1)
        m = layout.contentsMargins()
        h += m.top() + m.bottom()
        return max(h, self.minimumHeight())

    def sizeHint(self):  # noqa: N802 (Qt naming)
        w = self.width() if self.width() > 0 else INSTALL_TEXT_WIDTH
        return QSize(w, self.heightForWidth(w))


class FlashPage(QWidget):
    # Bumped whenever the card changes stage or structure; MainWindow fits the
    # window to it.
    view_generation = 0
    # The portable run's completion card offers to install this app properly.
    install_app_requested = Signal()
    # The completion letter's start-up advice: put this model's Original
    # Software back on the player, or open Select Software to browse instead.
    install_original_requested = Signal()
    browse_software_requested = Signal()

    def __init__(self, parent=None, journey=None):
        super().__init__(parent)
        # One owner for the themes/fonts stage. The window passes its own in so
        # both widgets read the same state; a page used on its own gets its own.
        self.themes = journey if journey is not None else ThemesJourney()
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
        # A bundled-firmware run is a portable run: it stores nothing beyond
        # the install and cleans its working files up before quitting.
        self._portable_run = False
        self._cleanup_mode = False
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
        # The flash tool needs a moment to start its USB listener. Hold the
        # connect sentence back for this window, so the first thing the user
        # reads is "Getting ready…" rather than an instruction to plug in
        # before the backend can answer the handshake.
        self._connect_get_ready_active = False
        self._connect_prompt_revealed = False
        self._write_100_start_time: float | None = None
        self._write_exceeded_100: bool = False
        self._write_100_timer = None
        # A new backend search line gets a fresh handshake window even when
        # the power-off card already put the connect sentence on screen and
        # that early window expired while the tool was still starting.
        self._connect_search_seen = False
        self._connect_get_ready_timer = QTimer(self)
        self._connect_get_ready_timer.setSingleShot(True)
        self._connect_get_ready_timer.setInterval(3000)
        self._connect_get_ready_timer.timeout.connect(self._reveal_connect_prompt)
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
        self._prep_icon = _make_squircle_icon()
        self._prep_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        self._prep_banner = Banner()
        self._prep_banner.setVisible(False)
        self._prep_progress = InstallProgress()
        _configure_progress(self._prep_progress)

        self._prep_cancel_btn = _RoundNextButton(symbol="back", accent=False)
        self._prep_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._prep_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._prep_cancel_btn.clicked.connect(self._on_cancel_wait)

        self._prep_next_btn = _RoundNextButton(symbol="arrow-right", accent=True)
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
        self._wait_icon = _make_squircle_icon()
        # Reference copy, not the card's action line: the prompt or question
        # takes the top row and this name sits under it at body size.
        self._wait_pkg_label = _status_label(self._package_name or "\u2014")
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

        # The firmware question the keep-data prompt asks takes the progress
        # band's own place in the card head, right beside the Next/Cancel chips,
        # so the eye runs dropdown -> steps text -> chip. The bar is still there
        # underneath for every state that is working, not asking.
        self._wait_head_stack = QStackedWidget()
        self._wait_head_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._wait_head_stack.addWidget(self._wait_progress_bar)
        # A one-line question the card asks — "Is Rockbox already installed on
        # your Y1?" — takes the band's place instead of sitting under it. A bar
        # frozen at 0% while nothing is being written reports nothing, and in
        # the band's slot the question shares its row and its axis with the X
        # and the tick, so those chips read as the question's two answers.
        self._wait_question_label = QLabel("")
        self._wait_question_label.setWordWrap(True)
        self._wait_question_label.setTextFormat(Qt.TextFormat.RichText)
        self._wait_question_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # The prompt's own line carries the tip's How? link as its last words.
        self._wait_question_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._wait_question_label.setOpenExternalLinks(False)
        self._wait_question_label.linkActivated.connect(lambda href: self._on_paperclip_help(href))
        _style_card_heading(self._wait_question_label)
        self._wait_question_label.setVisible(False)
        self._wait_head_stack.addWidget(self._wait_question_label)
        self._wait_card, self._wait_head, copy = self._new_progress_card(
            self._wait_icon,
            self._wait_pkg_label,
            self._wait_head_stack,
            [self._wait_cancel_btn, self._wait_continue_btn],
        )
        self._wait_row = self._wait_head.box
        # The question in the software title's own row. The card asks it there
        # when the band is busy holding the firmware list instead, so the two
        # lines of the head read question -> list, with the X and the tick on
        # the list's axis.
        self._wait_question_heading = QLabel("")
        self._wait_question_heading.setWordWrap(True)
        self._wait_question_heading.setTextFormat(Qt.PlainText)
        self._wait_question_heading.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _style_card_heading(self._wait_question_heading)
        self._wait_question_heading.setVisible(False)
        # The card's own line — prompt, question or progress band — owns the
        # top row, and the software title drops beneath it in small copy: the
        # line the user has to act on is what the head is for.
        identity = self._wait_head.identity_box
        identity.removeWidget(self._wait_pkg_label)
        identity.removeWidget(self._wait_head_stack)
        identity.insertWidget(0, self._wait_question_heading)
        identity.insertWidget(1, self._wait_head_stack)
        identity.insertWidget(2, self._wait_pkg_label)

        self._wait_prompt_label = _status_label(
            tr("flash_connect_device_prompt").format(model=self._connect_model_text())
        )
        self._wait_prompt_label.setWordWrap(True)
        copy.addWidget(self._wait_prompt_label)

        # "Keep my data": a tick on the connect / power-off card, off
        # unless the user asks for it. The state is read when Next is pressed,
        # so what the flash does is what the card showed at that moment.
        self._keep_data_user_changed = False
        self._keep_data_cb = QCheckBox(tr("flash_keep_data_option"))
        self._keep_data_cb.clicked.connect(lambda _checked=False: setattr(self, "_keep_data_user_changed", True))
        self._keep_data_cb.setCursor(Qt.ArrowCursor)
        self._keep_data_cb.setChecked(False)
        self._keep_data_cb.setToolTip(tr("flash_keep_data_tooltip"))
        self._keep_data_cb.setAccessibleName(tr("flash_keep_data_option"))
        apply_explicit_foreground(
            self._keep_data_cb, f" font-size: {_BODY_PX}px; border: none;"
        )
        self._keep_data_cb.setVisible(False)
        copy.addWidget(self._keep_data_cb)
        self._keep_data_choice = QComboBox()
        self._keep_data_choice.setMinimumWidth(200)
        self._keep_data_choice.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        # The firmware list takes the band's own slot, between the icon and the
        # chips: it is what the question is about, so it belongs on the band's
        # axis, with the round chips reading as the answers to the row it sits
        # on. While it is there the software title gives its row to the
        # question (see ``_sync_wait_band_page``).
        self._keep_data_choice_holder = QWidget()
        choice_row = QHBoxLayout(self._keep_data_choice_holder)
        choice_row.setContentsMargins(0, 0, 0, 0)
        choice_row.setSpacing(0)
        choice_row.addWidget(self._keep_data_choice)
        self._wait_head_stack.addWidget(self._keep_data_choice_holder)
        self._keep_data_choice.setVisible(False)
        self._keep_data_choice_holder.setVisible(False)
        self._wait_head_stack.setCurrentWidget(self._wait_progress_bar)
        self._keep_data_hint = QLabel("")
        self._keep_data_hint.setWordWrap(True)
        self._keep_data_hint.setProperty("cssClass", "hint")
        self._keep_data_hint.setTextFormat(Qt.RichText)
        self._keep_data_hint.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._keep_data_hint.linkActivated.connect(self._on_keep_data_hint_link)
        self._keep_data_hint.setVisible(False)
        copy.addWidget(self._keep_data_hint)
        self._keep_data_warning = QLabel("")
        self._keep_data_warning.setWordWrap(True)
        self._keep_data_warning.setProperty("cssClass", "hint")
        self._keep_data_warning.setTextFormat(Qt.TextFormat.RichText)
        self._keep_data_warning.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._keep_data_warning.setOpenExternalLinks(False)
        self._keep_data_warning.linkActivated.connect(self._on_keep_data_warning_got_it)
        self._keep_data_warning.setVisible(False)
        copy.addWidget(self._keep_data_warning)
        # "If you continue without this…" waits behind a link: it is a
        # paragraph for the reader who is deciding, not a line every card owes
        # the reader who already knows.
        self._keep_data_warning_revealed = False
        self._keep_data_help_link = QLabel(self._keep_data_help_html())
        self._keep_data_help_link.setTextFormat(Qt.TextFormat.RichText)
        self._keep_data_help_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._keep_data_help_link.setOpenExternalLinks(False)
        self._keep_data_help_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self._keep_data_help_link.linkActivated.connect(self._on_keep_data_help)
        self._keep_data_help_link.setVisible(False)
        copy.insertWidget(copy.indexOf(self._keep_data_warning), self._keep_data_help_link)
        self._keep_data_cb.toggled.connect(self._update_keep_data_warning)
        self._keep_data_allowed = True
        # Once the run's Keep Data answer is settled, every retry and backend
        # failover reuses it: the checkbox is not shown again and no second
        # compatibility question is asked. A distinct new install clears it.
        self._keep_data_locked = False
        self._keep_data_locked_value = False
        # Set by the window once it knows the package is Rockbox: the install's
        # cleanup step removes .rockbox itself, so the Keep Data warning has to
        # say that Rockbox settings go with it.
        self._rockbox_install = False
        self._keep_data_confirm_active = False
        self._keep_data_erase_confirm_active = False
        # X on the card in front leaves the install (list answers, erase warning).
        self._keep_data_cancel_leaves = False
        self._keep_data_force_hidden = False
        self._format_all_required = False
        self._format_all_confirm_active = False
        self._wait_col = copy

        self._keep_data_microsd_note = QLabel(self._microsd_note_html())
        self._keep_data_microsd_note.setWordWrap(True)
        self._keep_data_microsd_note.setProperty("cssClass", "dimmed")
        self._keep_data_microsd_note.setStyleSheet(
            f"font-size: {_BODY_PX}px; margin-top: 2px;"
        )
        self._keep_data_microsd_note.setTextFormat(Qt.TextFormat.RichText)
        self._keep_data_microsd_note.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._keep_data_microsd_note.setOpenExternalLinks(False)
        self._keep_data_microsd_note.linkActivated.connect(self._on_microsd_got_it)
        self._keep_data_microsd_note.setVisible(False)
        copy.insertWidget(copy.indexOf(self._keep_data_cb) + 1, self._keep_data_microsd_note)

        # What the tick keeps and what the install replaces, under the tick it
        # describes. The warning below covers the unchecked case, so only one
        # of the two ever speaks at a time.
        self._keep_data_note = QLabel(tr("flash_keep_data_tooltip"))
        self._keep_data_note.setWordWrap(True)
        self._keep_data_note.setProperty("cssClass", "dimmed")
        self._keep_data_note.setStyleSheet(
            f"font-size: {_BODY_PX}px; margin-top: 2px;"
        )
        self._keep_data_note.setVisible(False)
        # Right under the tick, above the microSD reassurance: what keeping data
        # means first, then the one thing that is never touched.
        self._keep_data_note.setVisible(False)
        copy.insertWidget(copy.indexOf(self._keep_data_cb) + 1, self._keep_data_note)

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

        # Paperclip hint with diagram image and model-specific wording in the main card
        self._paperclip_hint = _status_label("")
        hint_font = self._paperclip_hint.font()
        hint_font.setPixelSize(_BODY_PX)
        self._paperclip_hint.setFont(hint_font)
        self._paperclip_hint.setWordWrap(True)
        self._paperclip_hint.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        self._paperclip_img = _SvgVectorWidget()
        self._paperclip_img.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # The acknowledgement is the last words of the tip itself, so it reads
        # as one sentence — "... using a paperclip or pin. Got it." — instead
        # of a second line that costs a whole row of the card.
        self._paperclip_hint.setTextFormat(Qt.TextFormat.RichText)
        self._paperclip_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
        )
        self._paperclip_hint.setOpenExternalLinks(False)
        self._paperclip_hint.setCursor(Qt.CursorShape.PointingHandCursor)
        self._paperclip_hint.linkActivated.connect(self._on_paperclip_got_it)
        self._paperclip_sentence = ""
        # The tip is asked for: the prompt line ends in its How? link, and the
        # diagram and its copy arrive when the link is followed.
        self._paperclip_revealed = False

        self._paperclip_hint_host = _PaperclipHintHost()
        self._paperclip_hint_host.setObjectName("paperclipHintHost")
        self._paperclip_hint_host.setStyleSheet("background: transparent; border: none; margin-top: 2px;")
        hint_layout = QVBoxLayout(self._paperclip_hint_host)
        hint_layout.setContentsMargins(0, 0, 0, 0)
        hint_layout.setSpacing(6)
        hint_layout.addWidget(self._paperclip_hint)
        hint_layout.addWidget(self._paperclip_img, 0, Qt.AlignmentFlag.AlignLeft)
        
        # New advice hints
        self._charger_hint_label = _status_label(tr("flash_hint_wall_charger"))
        self._charger_hint_label.setProperty("cssClass", "dimmed")
        self._charger_hint_label.setStyleSheet(f"font-size: {_BODY_PX}px; margin-top: 2px;")
        self._charger_hint_label.setVisible(False)
        hint_layout.addWidget(self._charger_hint_label)

        import re

        self._driver_hint_widget = QWidget()
        driver_hint_layout = QHBoxLayout(self._driver_hint_widget)
        driver_hint_layout.setContentsMargins(0, 2, 0, 0)
        driver_hint_layout.setSpacing(6)

        clean_driver_hint = re.sub(r"<[^>]+>", "", tr("flash_hint_install_drivers")).strip()
        self._driver_hint_label = QLabel(clean_driver_hint)
        self._driver_hint_label.setProperty("cssClass", "dimmed")
        self._driver_hint_label.setStyleSheet(f"font-size: {_BODY_PX}px;")
        self._driver_hint_label.setWordWrap(True)
        driver_hint_layout.addWidget(self._driver_hint_label)

        self._driver_hint_btn = QPushButton(tr("settings_download_drivers_btn"))
        self._driver_hint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._driver_hint_btn.clicked.connect(self._open_mediatek_drivers)
        driver_hint_layout.addWidget(self._driver_hint_btn)
        driver_hint_layout.addStretch(1)

        self._driver_hint_widget.setVisible(False)
        hint_layout.addWidget(self._driver_hint_widget)

        self._driver_reboot_hint_label = QLabel(tr("flash_hint_windows_reboot_driver"))
        self._driver_reboot_hint_label.setProperty("cssClass", "dimmed")
        self._driver_reboot_hint_label.setStyleSheet(f"font-size: {_BODY_PX}px;")
        self._driver_reboot_hint_label.setWordWrap(True)
        self._driver_reboot_hint_label.setVisible(False)
        hint_layout.addWidget(self._driver_reboot_hint_label)

        self._paperclip_hint_host.setVisible(False)
        # Above the tick, which sits below it: the hint says how to power the
        # player off, then the tick and its copy say what the install keeps.
        copy.insertWidget(copy.indexOf(self._keep_data_cb), self._paperclip_hint_host)
        self._update_paperclip_hint()

        return self._wrap_centered(self._wait_card)

    def _build_flashing_view(self):
        self._flash_icon = _make_squircle_icon()
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
        self._theme_step1_next_btn = _RoundNextButton(symbol="arrow-right", accent=True)
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

        # Shown under the done mark when the software has post-install steps
        # and donations are off: the globe chip and this sentence open them.
        self._post_install_hint = QLabel(self._post_install_hint_html())
        self._post_install_hint.setTextFormat(Qt.TextFormat.RichText)
        self._post_install_hint.setWordWrap(True)
        self._post_install_hint.setProperty("cssClass", "dimmed")
        self._post_install_hint.setVisible(False)
        self._post_install_hint.linkActivated.connect(lambda _href: self._open_post_install_guide())

        # The install card asks its own questions in the band's slot, the way
        # the wait card does: a bar frozen at its last percentage while the
        # user is being asked something reports nothing, and a question standing
        # in the band sits on the row and the axis of the round chips that
        # answer it. The bar returns the moment the stage reports progress.
        self._flash_question_label = QLabel("")
        self._flash_question_label.setWordWrap(True)
        self._flash_question_label.setTextFormat(Qt.PlainText)
        self._flash_question_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        _style_card_heading(self._flash_question_label)
        self._flash_question_label.setVisible(False)
        self._flash_band_stack = QStackedWidget()
        self._flash_band_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._flash_band_stack.addWidget(self._progress_bar)
        self._flash_band_stack.addWidget(self._flash_question_label)
        self._flash_band_stack.setCurrentWidget(self._progress_bar)
        self._progress_card, self._flash_head, copy = self._new_progress_card(
            self._flash_icon,
            self._flash_pkg_label,
            self._flash_band_stack,
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
        copy.addWidget(self._post_install_hint)

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

        self._theme_recover_link = QLabel(
            _themed_link("recover", tr("rockbox_recover_troubleshoot"))
        )
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

        # A portable run finishes by offering the real install: the app is
        # still a folder or zip, not something the machine keeps.
        self._install_app_btn = QPushButton(tr("portable_install_app"))
        self._install_app_btn.setObjectName("portableInstallAppButton")
        self._install_app_btn.setCursor(Qt.PointingHandCursor)
        self._install_app_btn.setStyleSheet("")
        self._install_app_btn.setVisible(False)
        self._install_app_btn.clicked.connect(self.install_app_requested.emit)
        w_layout.addWidget(self._install_app_btn, 0, Qt.AlignCenter)

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
        # The completion card note: installed software and post-install steps.
        self._completion_note_container = QWidget()
        self._completion_note_layout = QVBoxLayout(self._completion_note_container)
        self._completion_note_layout.setContentsMargins(0, 0, 0, 0)
        self._completion_note_layout.setSpacing(10)
        
        self._completion_title_label = QLabel()
        self._completion_title_label.setWordWrap(True)
        self._completion_title_label.setProperty("cssClass", "hint")
        self._completion_note_layout.addWidget(self._completion_title_label)

        # The installed-on sentence on its own. The two-line heading above is
        # the visible form; this label keeps the plain sentence available to
        # callers and tests that read the note's text.
        self._complete_note = QLabel()
        self._complete_note.setWordWrap(True)
        self._complete_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._complete_note.setProperty("cssClass", "hint")
        self._complete_note.setVisible(False)
        self._completion_note_layout.addWidget(self._complete_note)
        
        self._completion_hint_widget = QWidget()
        self._completion_hint_layout = QVBoxLayout(self._completion_hint_widget)
        self._completion_hint_layout.setContentsMargins(0, 0, 0, 0)
        self._completion_hint_layout.setSpacing(5)
        
        self._completion_collapsible = CollapsibleWidget(tr("completion_hints_title"), self._completion_hint_widget)
        self._completion_note_layout.addWidget(self._completion_collapsible)
        
        self._completion_note_container.setVisible(False)
        w_layout.addWidget(self._completion_note_container)
        
        self._complete_note_software = ""
        self._complete_note_model = ""
        self._complete_note_themes = False
        # The simple card's advice label, built by _render_completion_note.
        self._completion_advice_label = None
        self._appeal_themes = False

        self._appeal = QWidget()
        self._appeal.setAutoFillBackground(False)
        self._appeal.setStyleSheet("")
        self._appeal.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        appeal_layout = QVBoxLayout(self._appeal)
        appeal_layout.setContentsMargins(8, 8, 8, 0)
        appeal_layout.setSpacing(10)
        # The completion letter's heading carries the firmware icon instead
        # of the portrait: the picture belongs to the signature below.
        self._intro = SupportIntro(show_portrait=False, show_icon=True)
        self._appeal_label = self._intro.headline
        appeal_layout.addWidget(self._intro)

        # The extra-steps sentence is the guide link itself: the reader is
        # told why the install is not finished, so that sentence is what they
        # click to finish it.
        self._completion_guide_hint = QLabel()
        self._completion_guide_hint.setWordWrap(True)
        self._completion_guide_hint.setTextFormat(Qt.TextFormat.RichText)
        self._completion_guide_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._completion_guide_hint.setOpenExternalLinks(False)
        self._completion_guide_hint.setCursor(Qt.PointingHandCursor)
        self._completion_guide_hint.linkActivated.connect(
            lambda _href: self._open_view_guide()
        )
        self._completion_guide_hint.setProperty("cssClass", "dimmed")
        self._completion_guide_hint.setAutoFillBackground(False)
        self._completion_guide_hint.setStyleSheet("")
        _apply_card_text(self._completion_guide_hint)
        # The sentence is read, not unfolded: it tells the reader why the
        # install is not finished and sits directly above the button that
        # finishes it. Collapsing belongs to the long help, not this line.
        self._completion_guide_hint.setVisible(False)
        appeal_layout.addWidget(self._completion_guide_hint)

        # The guide belongs to the instruction, not the donation ask: it sits
        # directly under the extra-steps sentence and above the appeal copy.
        self._view_guide_btn = QPushButton(tr("post_install_view_guide_btn"))
        self._view_guide_btn.setCursor(Qt.PointingHandCursor)
        self._view_guide_btn.setStyleSheet("")
        self._view_guide_btn.clicked.connect(self._open_view_guide)
        self._view_guide_btn.setVisible(False)
        appeal_layout.addWidget(self._view_guide_btn, 0, Qt.AlignLeft)

        # The one instruction the install still needs, at the size of an
        # instruction rather than a footnote to the headline.
        self._appeal_power_on = QLabel()
        self._appeal_power_on.setWordWrap(True)
        self._appeal_power_on.setTextFormat(Qt.TextFormat.RichText)
        self._appeal_power_on.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._appeal_power_on.setOpenExternalLinks(False)
        self._appeal_power_on.linkActivated.connect(self._on_start_help_link)
        self._appeal_power_on.setAutoFillBackground(False)
        self._appeal_power_on.setStyleSheet("")
        _apply_card_text(self._appeal_power_on)
        self._appeal_power_on.setVisible(False)
        appeal_layout.addWidget(self._appeal_power_on)

        self._start_help_hint = QLabel(
            _themed_link(
                "start-help", tr("completion_not_starting_hint"), underline=True
            )
        )
        self._start_help_hint.setTextFormat(Qt.TextFormat.RichText)
        self._start_help_hint.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._start_help_hint.setOpenExternalLinks(False)
        self._start_help_hint.setCursor(Qt.PointingHandCursor)
        self._start_help_hint.setProperty("cssClass", "dimmed")
        self._start_help_hint.linkActivated.connect(self._on_start_help_link)
        self._start_help_hint.setVisible(False)
        appeal_layout.addWidget(self._start_help_hint)

        self._start_help_body = QWidget()
        help_box = QVBoxLayout(self._start_help_body)
        help_box.setContentsMargins(0, 2, 0, 4)
        help_box.setSpacing(6)
        self._start_help_advice = QLabel(self._start_help_text())
        self._start_help_advice.setWordWrap(True)
        self._start_help_advice.setProperty("cssClass", "dimmed")
        self._start_help_advice.setTextFormat(Qt.TextFormat.RichText)
        self._start_help_advice.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._start_help_advice.setOpenExternalLinks(False)
        help_box.addWidget(self._start_help_advice)
        appeal_layout.addWidget(self._start_help_body)
        self._start_help_body.setVisible(False)

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

        # The picture belongs to the letter, not the banner: it sits below the
        # completion text and directly above the buttons, signed the way a
        # short personal note is. Beside the headline it read as a product
        # shot rather than as a message from the person who wrote the app.
        self._appeal_signature_row = QWidget()
        signature = QHBoxLayout(self._appeal_signature_row)
        signature.setContentsMargins(0, 0, 0, 0)
        signature.setSpacing(10)
        self._appeal_portrait = DeveloperPortrait(44)
        self._appeal_signature = QLabel(tr("donate_completion_signature"))
        self._appeal_signature.setWordWrap(True)
        self._appeal_signature.setProperty("cssClass", "dimmed")
        self._appeal_signature.setAutoFillBackground(False)
        self._appeal_signature.setStyleSheet("")
        signature_font = self._appeal_signature.font()
        signature_font.setItalic(True)
        self._appeal_signature.setFont(signature_font)
        _apply_card_text(self._appeal_signature)
        signature.addWidget(self._appeal_portrait, 0, Qt.AlignVCenter)
        signature.addWidget(self._appeal_signature, 0, Qt.AlignVCenter)
        signature.addStretch(1)
        appeal_layout.addWidget(self._appeal_signature_row)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._coffee_btn = QPushButton(tr("donate_completion_coffee"))
        self._coffee_btn.setCursor(Qt.PointingHandCursor)
        self._coffee_btn.setStyleSheet("")
        self._coffee_btn.clicked.connect(self._open_coffee_page)
        # The second donation destination, right beside the first: a completed
        # install always offers both, guide or not.
        self._bmc_btn = QPushButton(tr("donate_buy_me_a_coffee"))
        self._bmc_btn.setCursor(Qt.PointingHandCursor)
        self._bmc_btn.setStyleSheet("")
        self._bmc_btn.clicked.connect(self._open_bmc_page)
        self._not_now_btn = QPushButton(tr("donate_not_now"))
        self._not_now_btn.setCursor(Qt.PointingHandCursor)
        self._not_now_btn.setStyleSheet("")
        self._not_now_btn.clicked.connect(self._on_not_now)
        actions.addWidget(self._coffee_btn)
        actions.addWidget(self._bmc_btn)
        actions.addWidget(self._not_now_btn)
        actions.addStretch(1)
        appeal_layout.addLayout(actions)
        self._appeal_actions = actions

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

        # "What to do if it doesn't start up": the completion screen's one
        # piece of troubleshooting, kept as a hint so the letter stays short.
        # Opening it names both routes back to a working player — this model's
        # Original Software, or any other firmware in the catalogue — each a
        # link the window handles rather than a page this card opens.
        #
        # Reading order, top to bottom: how to start the player, what to do if
        # it still doesn't start (the advice opens directly under its own
        # hint), why the install is not finished and the guide that finishes
        # it, then the ask, signed. The advice used to be placed after the
        # appeal copy, where it read as part of the donation note.
        self._start_help_body.setVisible(False)
        reading_order = (
            self._appeal_power_on,
            self._start_help_hint,
            self._start_help_body,
            self._completion_guide_hint,
            self._view_guide_btn,
        )
        for widget in reading_order:
            appeal_layout.removeWidget(widget)
        for offset, widget in enumerate(reading_order):
            appeal_layout.insertWidget(1 + offset, widget)

        # Dummy attributes for backward compatibility
        self._flash_img = QLabel()
        self._flash_img.setVisible(False)
        self._action_label = QLabel("", parent=self)
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
        self._download_icon = _make_squircle_icon()
        self._download_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        self._download_progress = InstallProgress()
        _configure_progress(self._download_progress)

        self._download_cancel_btn = _RoundNextButton(symbol="cancel", accent=False)
        self._download_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._download_cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._download_cancel_btn.clicked.connect(self._on_cancel_download)

        self._download_next_btn = _RoundNextButton(symbol="arrow-right", accent=True)
        self._download_next_btn.setObjectName("downloadNextPlaceholderBtn")
        self._download_next_btn.setEnabled(False)
        self._download_next_btn.setVisible(False)
        chips = [self._download_cancel_btn]

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
        if self._stack.currentWidget() is not target_view:
            # The window is fitted to the card when the stage changes, and
            # only then: a live install must not resize the window from its
            # own progress/action lines (see MainWindow._adjust_window_geometry).
            self.view_generation = getattr(self, "view_generation", 0) + 1
        self._stack.setCurrentWidget(target_view)
        self._update_paperclip_hint()
        self._notify_geometry_change()

    def _update_paperclip_hint(self) -> None:
        """Show how to press the hidden button at power-off, connect, or disconnect recovery.

        Displays model-specific wording and the corresponding paperclip
        hardware illustration (A5, Y2, or generic/all) matching the active theme.
        In Updater CE branding, the generic all models paperclip hint image (or model-
        specific image if model is known) is displayed in the disconnect recovery scenario.
        In MediaTek Installer branding, paperclip hints are excluded in disconnect recovery.
        """
        import sys

        host = getattr(self, "_paperclip_hint_host", None)
        label = getattr(self, "_paperclip_hint", None)
        img_label = getattr(self, "_paperclip_img", None)
        if host is None or label is None:
            return
        was_hidden = host.isHidden()
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
            and not getattr(self, "_rockbox_themes_prompt", False)
            and getattr(self, "_show_pin_tip", True)
            and not getattr(self, "_stop_open", False)
        )
        is_usb_disconnected = (
            copy == "usb_disconnected"
            and not getattr(self, "_stop_open", False)
        )
        is_connect = (
            copy == "connect"
            and not getattr(self, "_stop_open", False)
        )
        wanted = (is_power_off or is_usb_disconnected or is_connect)
        # The first install that reaches the power-off or dropped-USB card shows
        # the tip open, because there the diagram *is* the instruction. After
        # that one showing it waits behind its How? link. The connect card never
        # opens by itself: its "not connecting? Try this" link is always on
        # offer for a reader whose player is not being found.
        if (
            (is_power_off or is_usb_disconnected)
            and not (is_mtk and is_usb_disconnected)
            and not _hint_seen("paperclip")
            and not getattr(self, "_stop_open", False)
        ):
            _set_hint_seen("paperclip")
            self._paperclip_revealed = True
        revealed = bool(getattr(self, "_paperclip_revealed", False))
        if _hint_dismissed("paperclip"):
            revealed = False
        # The connect card never opens its tip by itself: the sentence says
        # what to do, and the \"not connecting?\" link below it is the door. A
        # connect card that opened the diagram hid the ask behind it, and it
        # also swallowed the link the reader is meant to follow.
        if not wanted or not revealed or getattr(self, "_rockbox_themes_prompt", False):
            host.setVisible(False)
            self._charger_hint_label.setVisible(False)
            if hasattr(self, "_driver_hint_widget"):
                self._driver_hint_widget.setVisible(False)
            if hasattr(self, "_driver_hint_label"):
                self._driver_hint_label.setVisible(False)
            if hasattr(self, "_driver_reboot_hint_label"):
                self._driver_reboot_hint_label.setVisible(False)
            if img_label is not None:
                img_label.setFixedSize(0, 0)
            host.setMinimumHeight(0)
            self._note_paperclip_visibility_change(was_hidden)
            return

        from .. import paths
        is_windows = bool(paths.IS_WINDOWS)
        drivers_installed = paths.windows_sp_drivers_installed()
        self._charger_hint_label.setVisible(True)
        if hasattr(self, "_driver_hint_widget"):
            self._driver_hint_widget.setVisible(is_windows and not drivers_installed)
        elif hasattr(self, "_driver_hint_label"):
            self._driver_hint_label.setVisible(is_windows and not drivers_installed)
        if hasattr(self, "_driver_reboot_hint_label"):
            self._driver_reboot_hint_label.setVisible(is_windows)

        if is_connect:
            from ..config import device_label_for_model, is_a5_model

            # Show the connect hint. If the user has been here a while (or we want to be proactive), 
            # we can show the "won't connect?" hint instead. 
            # Let's show both or make it conditional.
            # The user request suggests: "*model* won't connect? Try this"
            
            
            key = (
                "flash_connect_paperclip_prompt_a5"
                if is_a5_model(model)
                else "flash_connect_paperclip_prompt"
            )
            model_name = (
                device_label_for_model(model) if model else tr("device_fallback_word")
            )
            self._paperclip_sentence = tr(key).format(model=model_name)
            # One instruction on this card: where the hidden button is and
            # how to press it, with the diagram. "Not connecting? Try this" is
            # already the heading's own link, so repeating that sentence in the
            # tip said the same thing twice.
            label.setText(self._paperclip_hint_html(self._paperclip_sentence))
            label.setVisible(True)
            QTimer.singleShot(0, self._fit_paperclip_hint_height)
        elif is_power_off:
            from ..config import device_label_for_model, is_a5_model
            model_name = device_label_for_model(model) if model else tr("device_fallback_word")
            is_a5 = is_a5_model(model)
            key = "flash_paperclip_hint_a5" if is_a5 else "flash_paperclip_hint"
            self._paperclip_sentence = tr(key).format(model=model_name)
            label.setText(self._paperclip_hint_html(self._paperclip_sentence))
            label.setVisible(True)
            # Measure at the width the card actually gives the tip, once the
            # layout has run: the wrap column is narrower than the card, and
            # reserving for it left a gap between the sentence and the diagram.
            QTimer.singleShot(0, self._fit_paperclip_hint_height)
        elif is_mtk and is_usb_disconnected:
            # The MediaTek Installer build keeps this card free of the tip: its
            # own disconnect copy already carries the instructions.
            self._paperclip_sentence = ""
            label.setText("")
            label.setVisible(False)
        else:
            # Dropped USB before the write: the same hidden button fully powers
            # the player down, and saying so beside the diagram is what makes
            # the picture usable. Its Windows driver hint keeps its own line.
            from ..config import device_label_for_model, is_a5_model

            model_name = (
                device_label_for_model(model) if model else tr("device_fallback_word")
            )
            key = (
                "flash_paperclip_hint_a5"
                if is_a5_model(model)
                else "flash_paperclip_hint"
            )
            self._paperclip_sentence = tr(key).format(model=model_name)
            label.setText(self._paperclip_hint_html(self._paperclip_sentence))
            label.setVisible(True)
            QTimer.singleShot(0, self._fit_paperclip_hint_height)

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

            hint_h = label.sizeHint().height() if label.isVisible() else 0
            if hasattr(img_label, "load_svg"):
                ok = img_label.load_svg(img_path, txt)
                img_label.setVisible(ok)
                if ok and host is not None:
                    # Exactly the tip and its diagram plus the gap between them:
                    # reserving more left a hole under the sentence.
                    host.setMinimumHeight(int(img_label.height()) + hint_h + 4)
            else:
                if not source.isNull():
                    img_label.setPixmap(source)
                    img_label.setFixedSize(source.deviceIndependentSize().toSize())
                    img_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                    img_label.setVisible(True)
                    if host is not None:
                        host.setMinimumHeight(
                            int(source.deviceIndependentSize().height()) + hint_h + 4
                        )
                else:
                    img_label.setVisible(False)
                    img_label.setFixedSize(0, 0)
                    if host is not None:
                        host.setMinimumHeight(0)

        waiting = getattr(self, "_waiting_view", None)
        host.setVisible(
            revealed and waiting is not None and self._stack.currentWidget() is waiting
        )
        self._note_paperclip_visibility_change(was_hidden)

    def _note_paperclip_visibility_change(self, was_hidden: bool) -> None:
        """Refit the window when the guide changes the waiting card's height.

        The power-off prompt and the USB-search prompt are the same stacked
        view, so ``view_generation`` does not change between them. Hiding the
        paperclip guide makes the card several hundred pixels shorter; without
        a new generation MainWindow keeps the window at the prompt's height and
        leaves an empty band under the connect card.
        """
        host = getattr(self, "_paperclip_hint_host", None)
        if host is None or host.isHidden() == was_hidden:
            return
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._relayout_wait_card()
        self._notify_geometry_change()

    def _relayout_wait_card(self) -> None:
        """Grow the wait card when its own content just grew.

        The paperclip tip and the Keep settings rows live in the card's column,
        and the tip's height is only known once it has been measured. Setting
        that height changes the card's size hint after the card was laid out at
        the smaller one, which left the card shorter than its own minimum and
        squeezed the tick, its copy and the tip into each other's pixels. Qt
        does not re-run an outer layout just because an inner child changed, so
        the card asks for one.
        """
        card = getattr(self, "_wait_card", None)
        if card is None:
            return
        layout = card.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        # The card reports a height-for-width, and the column above it sizes the
        # card from that number rather than from its size hint. Once the tip has
        # grown the card, that number is smaller than the rows need, and Qt
        # squeezes them until they paint over each other. A minimum height is
        # the one thing a layout may not go under, so the card holds it.
        needed = card.minimumSizeHint().height()
        if card.minimumHeight() != needed:
            card.setMinimumHeight(needed)
        card.updateGeometry()
        parent = card.parentWidget()
        parent_layout = parent.layout() if parent is not None else None
        if parent_layout is not None:
            parent_layout.invalidate()
            parent_layout.activate()

    def show_downloading(self):
        val = self._package_name or "\u2014"
        if hasattr(self, "_download_pkg_label"):
            self._download_pkg_label.setText(val)
        self._download_progress.setValue(0)
        self._download_status_label.setText(tr("sel_download_start"))
        self._switch_view(self._downloading_view)
        self._apply_stop_hold()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

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

    def begin_run(self) -> None:
        """A new flash starts with the progress card up; donations apply at its end."""
        self._reset_connect_prompt_delay()
        self._donations_shown = False
        # The row starts visible; only a completion render without the ask
        # hides it, and that decision survives a retranslate.
        self._donation_row_visible = True
        self._complete = False
        self._failed = False
        if hasattr(self, "_post_install_hint"):
            self._post_install_hint.setVisible(False)
        # Set per install by set_post_install_urls; the empty default keeps a
        # completion screen reached without one from failing.
        self._post_install_url = ""
        self._post_install_url_2 = ""
        self._post_install_url_2_source = ""
        # A ``post_install_once`` firmware can finish without the guide when
        # the player is already running it (or the user just said so in the
        # Keep Data question). The window decides that per run.
        self._post_install_guide_suppressed = False

    def show_preparing(self):
        self._reset_connect_prompt_delay()
        self._complete = False
        self._wait_copy = "preparing"
        self._sync_keep_data_visibility()
        self._switch_view(self._preparing_view)
        self._prep_banner.set_key("flash_preparing")
        self._prep_step_key = "step_extract"
        self._prep_step.setText(tr("step_extract"))
        self._apply_stop_hold()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_drop_check(self, name: str = "") -> None:
        """Indeterminate bar while a dropped package is checked. No tool names."""
        self._complete = False
        self._donations_shown = False
        if name and hasattr(self, "_prep_pkg_label"):
            self._prep_pkg_label.setText(name)
        self._switch_view(self._preparing_view)
        self._prep_banner.setVisible(False)
        self._prep_step_key = "drop_checking"
        self._prep_step.setText(tr("drop_checking"))
        self._prep_progress.setRange(0, 0)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_drop_error(self, message: str) -> None:
        """Stop the throbber and leave the reason on the progress card."""
        self._prep_progress.setRange(0, 100)
        self._prep_progress.setValue(0)
        self._prep_step_key = ""
        self._prep_step.setText(message)
        self._switch_view(self._preparing_view)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_waiting(self):
        self._complete = False
        self.dismiss_flash_question()
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
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def _set_indeterminate(self, bar: QProgressBar) -> None:
        """Native busy throbber. Range (0, 0) is the platform indeterminate bar."""
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        self._sync_wait_band_page()

    def show_please_wait(self, connecting: bool = False) -> None:
        """Indeterminate bar and "Please wait", never the connect sentence.

        Used before the tool prints a USB search line, and again after a later
        line of real progress until a percent or stage takes over.
        """
        # A handshake retry starts a new search, so its connect card gets its
        # own getting-ready window rather than inheriting the previous one.
        self._reset_connect_prompt_delay()
        self._failed = False
        self._complete = False
        self._prompt_only = False
        self._wait_copy = "please_wait_connecting" if connecting else "please_wait"
        self._sync_keep_data_visibility()
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
        self._sync_keep_data_visibility()
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

    def _arm_connect_prompt_delay(self) -> None:
        """Start the getting-ready window unless this pass already has one.

        The tool needs a moment to start its USB listener, so the connect
        sentence is held back. Repeated search lines from the same pass must
        not extend the window: only a new stage, or the first line after the
        window expired, starts a fresh one.
        """
        timer = getattr(self, "_connect_get_ready_timer", None)
        if timer is None or timer.isActive():
            return
        self._connect_get_ready_active = True
        self._connect_prompt_revealed = False
        timer.start(3000)

    def _reset_connect_prompt_delay(self) -> None:
        """Arm the getting-ready window for the next connect stage."""
        timer = getattr(self, "_connect_get_ready_timer", None)
        if timer is not None:
            timer.stop()
        self._connect_get_ready_active = False
        self._connect_search_seen = False

    def _reset_write_progress(self) -> None:
        self._write_100_start_time = None
        self._write_exceeded_100 = False
        timer = getattr(self, "_write_100_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        for bar in (getattr(self, "_progress_bar", None), getattr(self, "_wait_progress_bar", None)):
            if bar is not None:
                bar.setTextVisible(True)
                if not getattr(bar, "is_getting_ready", lambda: False)():
                    bar.setFormat("%p%")

    def _on_write_100_timeout(self) -> None:
        if getattr(self, "_complete", False) or getattr(self, "_failed", False):
            return
        for bar in (getattr(self, "_progress_bar", None), getattr(self, "_wait_progress_bar", None)):
            if bar is not None:
                bar.setTextVisible(False)
                bar.setFormat("")
        part_lbl = getattr(self, "_partition_progress_label", None)
        if part_lbl is not None and part_lbl.isVisible():
            part_lbl.clear()
            part_lbl.setVisible(False)
            self._notify_geometry_change()

    def reset_connect_session(self) -> None:
        """Reset the connect prompt state for a completely new install session."""
        self._reset_connect_prompt_delay()
        self._connect_prompt_revealed = False
        self._reset_write_progress()

    def _finish_connect_prompt_delay(self) -> None:
        """Stop the window once the device is detected or the stage moved on."""
        timer = getattr(self, "_connect_get_ready_timer", None)
        if timer is not None:
            timer.stop()
        self._connect_get_ready_active = False
        self._connect_prompt_revealed = True

    def _reveal_connect_prompt(self) -> None:
        """Swap "Getting ready…" for the connect sentence after the window."""
        self._connect_get_ready_timer.stop()
        self._connect_get_ready_active = False
        # A reveal means the engine is up, so it also ends the hold for the
        # backend's own search line.
        self._connect_search_seen = True
        if getattr(self, "_wait_copy", "") != "connect":
            return
        if getattr(self, "_complete", False) or getattr(self, "_failed", False):
            return
        if self._stack.currentWidget() is not self._waiting_view:
            return
        self._connect_prompt_revealed = True
        self.set_waiting_device()
        self._sync_wait_band_page()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_connect_search(self) -> None:
        """Connect sentence only while the flasher is searching for USB.

        "Please connect your Y1" is the same kind of ask as "power it off":
        the reader has one thing to do and no percentage to watch yet, so it
        gets the same prompt-only card — no software title above it and no bar
        frozen at 0%. The title returns with the bar once the run is writing.
        """
        self._failed = False
        self._complete = False
        self._prompt_only = True
        # The flasher is actively searching for the device now: reveal the
        # connect prompt immediately and cancel any getting-ready hold.
        timer = getattr(self, "_connect_get_ready_timer", None)
        if timer is not None:
            timer.stop()
        self._connect_get_ready_active = False
        self._connect_prompt_revealed = True
        self._connect_search_seen = True
        self._wait_copy = "connect"
        # Every handshake starts with the tip closed: the card always shows the
        # link below the sentence, and the diagram opens only when it is clicked.
        self._paperclip_revealed = False
        self._switch_view(self._waiting_view)
        self.set_waiting_device()
        self._sync_wait_band_page()
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._apply_stop_hold()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

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

    def set_theme_download_status(self, text: str) -> None:
        """Show the theme-pack download line on the install card itself."""
        if hasattr(self, "_step_label"):
            if text:
                self._step_label.setText(text)
            else:
                default_step = tr("rockbox_cleanup_step") if self._cleanup_stage else tr("rockbox_stage_installing")
                self._step_label.setText(default_step if (getattr(self, "themes", None) and self.themes.active) else "")
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.refresh()
        self._notify_geometry_change()

    def _connect_prompt_held(self) -> bool:
        """True while the card should say "Getting ready…" instead of asking.

        The hold belongs to the engine's own search line: the worker only puts
        this card up once mtkclient has started, so the window is a moment for
        the USB listener to come up, not a wait for the import. Every writer of
        the connect sentence goes through ``_connect_prompt_text``, so a
        countdown tick or a re-layout cannot put the ask back early.
        """
        return bool(getattr(self, "_connect_get_ready_active", False))

    def _connect_prompt_text(self) -> str:
        if self._connect_prompt_held():
            return tr("flash_getting_ready")
        return self._connect_device_prompt_text()

    def _connect_device_prompt_text(self) -> str:
        # No countdown here. A ticking number on the card reads as a deadline
        # the user has to beat, and a connect attempt that runs out is not the
        # end of anything: the power-off and disconnect card comes back with
        # the way to try again.
        return tr("flash_connect_device_prompt").format(model=self._connect_model_text())

    def _wait_connecting_prompt_text(self) -> str:
        return tr("flash_wait_connecting_prompt").format(model=self._connect_model_text())

    def _band_line_width(self) -> int:
        """One-line width of the band's copy, with the countdown at its widest.

        The connect countdown loses a digit every second. Measuring the live
        sentence on every tick slid the chips beside it and, whenever the card
        was tight, left the label narrower than its words — so the countdown
        wrapped onto a line of its own and the card changed height. Counting
        the digits at their widest lets the chips move over once for the whole
        slot; only the numbers change inside it afterwards.
        """
        label = getattr(self, "_wait_question_label", None)
        if label is None:
            return 0
        metrics = label.fontMetrics()
        # The label may carry markup (the paperclip tip's link), so measure the
        # words it draws rather than the tags around them.
        text = re.sub(r"<[^>]+>", "", label.text() or "")
        countdown = re.search(r"\((\d+)s\)", text)
        if countdown is None:
            return int(metrics.horizontalAdvance(text)) + 2
        widest_digit = max(metrics.horizontalAdvance(ch) for ch in "0123456789")
        prefix = text[: countdown.start()]
        suffix = text[countdown.end() :]
        return int(
            metrics.horizontalAdvance(f"{prefix}(")
            + widest_digit * _BAND_COUNTDOWN_DIGITS
            + metrics.horizontalAdvance(f"s){suffix}")
        ) + 2

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
            if copy not in ("power_off", "stage"):
                if copy == "please_wait_connecting":
                    text = self._wait_connecting_prompt_text()
                else:
                    if remaining > 0:
                        self._wait_copy = "connect"
                    if self._wait_copy == "connect":
                        text = self._connect_prompt_text()
                    else:
                        text = ""
                if remaining > 0:
                    # A countdown belongs to the connect prompt: the card that
                    # asks for the player, not one titled by the software. A
                    # tick that arrives before the tool's own search line must
                    # not put the software name back above the sentence.
                    self._prompt_only = True
                if text and not getattr(self, "_connect_get_ready_active", False):
                    if getattr(self, "_stop_open", False):
                        saved = getattr(self, "_stop_saved", None)
                        if saved:
                            saved["label_text"] = text
                            self._stop_saved = saved
                    else:
                        self._wait_prompt_label.setText(text)
                # On a prompt-only card the band carries this sentence, so a
                # ticked-down countdown has to reach it too.
                self._sync_wait_band_page()

    def show_real_stage(self, text: str) -> None:
        """Drop the connect sentence and the throbber once the tool moves on."""
        self._finish_connect_prompt_delay()
        self._prompt_only = False
        self.set_attempt_timeout(0)
        self._wait_copy = "stage"
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(text or "")
        if self._wait_progress_bar.maximum() == 0 and self._wait_progress_bar.minimum() == 0:
            self._set_determinate(self._wait_progress_bar, 0)
        self._sync_wait_band_page()
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
        logger.info("show_flashing called, _complete=%s", getattr(self, "_complete", False))
        self._reset_write_progress()
        self._finish_connect_prompt_delay()
        self._cleanup_mode = False
        self.show_install_app(False)
        if hasattr(self, "_completion_note_container"):
            self._completion_note_container.setVisible(False)
        if hasattr(self, "_progress_card"):
            self._progress_card.setVisible(True)
        if getattr(self, "_wait_copy", "") in ("connect", "please_wait", "please_wait_connecting"):
            if hasattr(self, "_wait_prompt_label"):
                self._wait_prompt_label.setText(self._step_label.text())
        self._wait_copy = "stage"
        self._prompt_only = False
        self._sync_wait_band_page()
        self.dismiss_flash_question()
        self._sync_keep_data_visibility()
        self._switch_view(self._flashing_view)
        failed = self._failed
        done = bool(getattr(self, "_complete", False)) and not failed
        if not done and hasattr(self, "_post_install_hint"):
            self._post_install_hint.setVisible(False)
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
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()
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
            self._done_mark.setVisible(done and not getattr(self, "_donations_shown", False))
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
        # Reaching this screen resets the guide's visibility with the rest of
        # the completion state.
        self._apply_post_install_state()

    def _open_mediatek_drivers(self) -> None:
        import sys
        from ..browser import open_browser
        from ..config import MEDIATEK_DRIVERS_URL, MEDIATEK_DRIVERS_ARM64_URL
        from ..platform_detect import is_windows_on_arm

        is_arm64 = is_windows_on_arm() if sys.platform == "win32" else False
        open_browser(MEDIATEK_DRIVERS_ARM64_URL if is_arm64 else MEDIATEK_DRIVERS_URL)

    def _set_determinate(self, bar: QProgressBar, percent) -> None:
        try:
            val = float(percent)
        except (TypeError, ValueError):
            val = 0.0
        val = max(0.0, min(100.0, val))
        bar.setRange(0, 100)
        if not getattr(bar, "is_getting_ready", lambda: False)():
            bar.setFormat("%p%")
        bar.setValue(val)
        bar.setTextVisible(True)
        self._sync_wait_band_page()

    def set_getting_ready(self, active: bool = True, text: str = "") -> None:
        """Toggle 'Getting ready...' state: hide percentage and display text on and near progress bar."""
        self._getting_ready = bool(active)
        msg = text or tr("flash_getting_ready")
        if hasattr(self, "_progress_bar") and hasattr(self._progress_bar, "set_getting_ready"):
            self._progress_bar.set_getting_ready(active, text=msg)
        if hasattr(self, "_wait_progress_bar") and hasattr(self._wait_progress_bar, "set_getting_ready"):
            self._wait_progress_bar.set_getting_ready(active, text=msg)
        if active:
            if hasattr(self, "_step_label"):
                self._step_label.setText(msg)
            flow = getattr(self, "_status_flow", None)
            if flow is not None:
                flow.refresh()
            self._notify_geometry_change()
        elif hasattr(self, "_step_key") and getattr(self, "_step_key", None):
            if hasattr(self, "_step_label"):
                self._step_label.setText(tr(self._step_key))
            flow = getattr(self, "_status_flow", None)
            if flow is not None:
                flow.refresh()
            self._notify_geometry_change()

    def show_install_failure(
        self,
        message: str,
        explanation: str = "",
        percent: int = 0,
        wrong_model: bool = False,
    ) -> None:
        """Keep the failure on the install progress card.

        An ordinary failure offers Retry and Cancel. A wrong player offers only
        the ×: no retry can turn the connected model into the package's model,
        so the useful next step is to cancel and choose the right software.
        """
        self.set_getting_ready(False)
        self.update_partition_progress("")
        self.dismiss_prompt_stop()
        self._failed = True
        self._prompt_only = False
        # The failure card is not the connect prompt, so the "keep my data"
        # tick does not belong on it.
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_cb.setVisible(False)
        if hasattr(self, "_keep_data_warning"):
            self._keep_data_warning.setVisible(True)
        if hasattr(self, "_keep_data_help_link"):
            self._keep_data_help_link.setVisible(True)
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
        if wrong_model:
            # The only action that can help: stop this install and pick the
            # software for the player that is actually connected.
            if hasattr(self, "_flash_retry_btn"):
                self._flash_retry_btn.setVisible(False)
                self._flash_retry_btn.setEnabled(False)
            if hasattr(self, "_wait_continue_btn"):
                self._wait_continue_btn.setVisible(False)
            if hasattr(self, "_cancel_btn"):
                self._cancel_btn.setVisible(True)
                self._cancel_btn.setEnabled(True)
                self._cancel_btn.set_symbol("cancel")
                self._cancel_btn.setToolTip(tr("btn_cancel"))
                self._cancel_btn.setAccessibleName(tr("btn_cancel"))
        if message == "CACHE_FILE_MISSING":
            self._flash_retry_btn.setToolTip(tr("sel_cache_retry_tooltip"))
            self._flash_retry_btn.setAccessibleName(tr("sel_cache_retry_button"))
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def stop_theme_workers(self) -> None:
        """Cancel and clean up any in-progress Rockbox theme background tasks."""
        self.themes.settle_prompt()
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        self._close_theme_folder_dialog()
        self._stop_removable_drive_watch()
        sim_timer = getattr(self, "_sim_theme_timer", None)
        if sim_timer is not None:
            sim_timer.stop()
        for attr in (
            "_adb_watch_worker",
            "_adb_push_worker",
            "_dir_merge_worker",
            "_rockbox_cleanup_worker",
        ):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    if w.isRunning():
                        w.requestInterruption()
                        w.wait(1500)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._stop_adb_presence_watch()

    def _ensure_adb_presence_watch(self) -> None:
        """Notice a player while a guidance step is waiting on the user.

        Runs for as long as the step does, including behind the modal folder
        picker, which starves Qt timers but not a thread. Starting it again on a
        repeated prompt replaces the old watcher, so only one ever polls.
        Non-Android Rockbox ports never use ADB.
        """
        self._stop_adb_presence_watch()
        if getattr(self.themes, "mass_storage", False):
            return
        from ..adb import find_adb_binary
        from ..theme_pack import AdbPresencePoller

        adb_bin = find_adb_binary()
        if not adb_bin:
            return
        worker = AdbPresencePoller(adb_bin, parent=self)
        worker.found.connect(self._on_adb_device_found)
        worker.multiple.connect(self._on_adb_multiple_devices)
        self.themes.attach_watch(worker)
        worker.start()

    def _start_removable_drive_watch(self) -> None:
        """Poll non-system / external drives for ``.rockbox`` during mass-storage steps."""
        self._stop_removable_drive_watch()
        timer = QTimer(self)
        timer.setInterval(2000)
        timer.timeout.connect(self._poll_removable_rockbox_dirs)
        self._removable_watch_timer = timer
        timer.start()

    def _stop_removable_drive_watch(self) -> None:
        timer = getattr(self, "_removable_watch_timer", None)
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
            self._removable_watch_timer = None

    def _poll_removable_rockbox_dirs(self) -> None:
        if not getattr(self.themes, "active", False) or not getattr(self.themes, "mass_storage", False):
            self._stop_removable_drive_watch()
            return
        if self._try_mass_storage_auto_transfer(fallback_to_picker=False):
            self._stop_removable_drive_watch()

    def _try_mass_storage_auto_transfer(self, *, fallback_to_picker: bool = False) -> bool:
        """Scan external drives for ``.rockbox`` dirs and transfer to every one found."""
        try:
            from ..theme_sync import find_removable_rockbox_dirs

            hint = str(getattr(self.themes, "rockbox_folder_path", "") or "")
            removable = find_removable_rockbox_dirs(rockbox_folder_path=hint)
        except Exception as e:
            logger.debug("Error checking removable rockbox dirs: %s", e)
            removable = []
        if removable:
            self._stop_removable_drive_watch()
            joined = ", ".join(str(p) for p in removable)
            self._log_theme(
                f"[Rockbox Themes] Auto-detected {len(removable)} external .rockbox "
                f"folder(s): {joined}"
            )
            if self._cleanup_stage:
                self._start_local_cleanup(removable)
            else:
                self._start_mass_storage_merge(removable)
            return True
        if fallback_to_picker:
            self._show_theme_fallback_usb_mode()
        return False

    def _active_firmware_package(self):
        """Return the active manifest/local FirmwarePackage from the parent window."""
        win = self.window()
        if win is None:
            return None
        getter = getattr(win, "_active_firmware_package", None)
        if callable(getter):
            try:
                pkg = getter()
                if pkg is not None:
                    return pkg
            except Exception:
                pass
        page = getattr(win, "_select_page", None)
        if page is not None:
            pkg = getattr(page, "_details_package", None)
            if pkg is not None:
                return pkg
            cur = getattr(page, "current_package", None)
            if callable(cur):
                try:
                    pkg = cur()
                    if pkg is not None:
                        return pkg
                except Exception:
                    pass
        return getattr(win, "_details_package", None) or getattr(win, "_keep_data_pkg", None)

    def _stop_adb_presence_watch(self) -> None:
        worker = self.themes.take_watch()
        if worker is None:
            return
        try:
            worker.found.disconnect()
            worker.multiple.disconnect()
        except Exception:
            pass
        worker.requestInterruption()
        if worker.wait(1500):
            return
        # ``adb devices`` can sit in its own subprocess timeout while we ask the
        # poller to stop. Dropping a running QThread makes Qt abort the process,
        # so a slow one is retired and deleted once it exits by itself.
        logger.debug("ADB presence watcher still stopping; retiring it")
        self.themes.retire_watch(worker)
        worker.finished.connect(lambda w=worker: self._retire_presence_worker(w))

    def _retire_presence_worker(self, worker) -> None:
        self.themes.forget_watch(worker)
        worker.deleteLater()

    def _request_user_attention(self) -> None:
        """Bring the window forward: the user has something to do right now."""
        win = self.window()
        if win is None:
            return
        try:
            win.raise_()
            win.activateWindow()
        except Exception:
            logger.debug("Could not raise the window for a guidance step", exc_info=True)

    def closeEvent(self, event):  # noqa: N802 (Qt naming)
        self.stop_theme_workers()
        self._drain_retired_presence_workers()
        super().closeEvent(event)

    def _drain_retired_presence_workers(self, timeout_ms: int = 3000) -> None:
        """Join pollers that were still finishing an ``adb devices`` call.

        Only app shutdown pays this wait: a QThread destroyed while running
        aborts the process, and the poller's own adb call is short.
        """
        for worker in list(self.themes.retired_watches):
            try:
                worker.wait(timeout_ms)
            except Exception:
                logger.debug("Could not join a retired ADB poller", exc_info=True)
            if not worker.isRunning():
                self.themes.forget_watch(worker)

    def clear_install_failure(self) -> None:
        self.set_getting_ready(False)
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
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

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
        # This is the USB-recovery screen, not the power-off confirmation: the
        # Keep Data choice belongs only on that one prompt.
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_cb.setVisible(False)
        if hasattr(self, "_keep_data_warning"):
            self._keep_data_warning.setVisible(True)
        if hasattr(self, "_keep_data_help_link"):
            self._keep_data_help_link.setVisible(True)
        msg = self._power_off_prompt_text()
        self._wait_prompt_label.setText(msg)
        # The recovery sentence is the card's action line here, so it belongs
        # in the band's slot rather than under a bar that can only say 0%.
        self._sync_wait_band_page()
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
        self._update_keep_data_warning()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()
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
        if self.themes.usb_asked:
            if getattr(self.themes, "mass_storage", False):
                self._try_mass_storage_auto_transfer(fallback_to_picker=True)
                return
            # The cable is in: the watch, not the folder picker, is next.
            self._start_theme_step2_connect_usb()
            return
        self._log_theme("[Rockbox Themes] User confirmed the player is powered on. Asking for the USB cable.")
        self._show_theme_connect_usb_question()

    def rockbox_power_on_question(self, model: str = "", standalone: bool = False) -> str:
        """The one power-on question, for every model and both flash methods.

        Only the button differs per player: the Y1 wakes on its centre button,
        every other model on the power button. One sentence means the MTKClient
        handoff, the standalone themes run and the SP Flash Tool run all ask
        the same thing instead of three near-copies of it.
        """
        from ..config import device_label_for_model, is_y1_model, is_y2_model

        eff_model = model or getattr(self, "_model", "") or getattr(self.themes, "model", "")
        pkg_name = getattr(self, "_package_name", "") or ""
        if "for Y2" in pkg_name or "for y2" in pkg_name.lower():
            eff_model = "Y2"
        elif is_y2_model(eff_model) or is_y2_model(getattr(self, "_model", "")):
            eff_model = "Y2"
        elif is_y1_model(eff_model):
            eff_model = "Y1"

        label = device_label_for_model(eff_model)
        if standalone:
            if getattr(self.themes, "mass_storage", False):
                return tr("rockbox_step2_connect_usb_storage").format(model=label)
            return tr("rockbox_step_standalone_power_on_usb").format(model=label)

        is_y1 = (eff_model == "Y1")
        button = tr("button_centre" if is_y1 else "button_power")
        return tr("rockbox_step1_power_on_button").format(model=label, button=button)

    def show_flash_question(self, text: str) -> None:
        """Ask on the install card: the question takes the band's slot.

        The round chips beside the band are the question's answers, so they
        slide in to sit just past its words while it is up (see
        ``_CardHead.set_band_inline``). Every path that reports progress again
        calls :meth:`dismiss_flash_question`, so a question is never left
        standing over a live run.
        """
        label = getattr(self, "_flash_question_label", None)
        stack = getattr(self, "_flash_band_stack", None)
        if label is None or stack is None:
            return
        if label.text() != (text or ""):
            label.setText(text or "")
        label.setVisible(True)
        if stack.currentWidget() is not label:
            stack.setCurrentWidget(label)
        head = getattr(self, "_flash_head", None)
        if head is not None:
            head.set_band_inline(True, label)
        # While the card is asking, the question is the only line it owes the
        # user: the step title ("Installing Rockbox Themes & Fonts...") and any
        # leftover run copy would sit under it saying what the prompt already
        # says. They come back with the bar, once the stage is working again.
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.setVisible(False)
        self._relayout_for_text()

    def dismiss_flash_question(self) -> None:
        """Hand the band's slot back to the progress bar."""
        label = getattr(self, "_flash_question_label", None)
        stack = getattr(self, "_flash_band_stack", None)
        if label is None or stack is None:
            return
        if stack.currentWidget() is not self._progress_bar:
            stack.setCurrentWidget(self._progress_bar)
        label.setVisible(False)
        head = getattr(self, "_flash_head", None)
        if head is not None:
            head.set_band_inline(False)
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.setVisible(True)

    def _show_theme_connect_usb_question(self) -> None:
        """Ask for the USB cable, and start the ADB watch only once it is in.

        ADB is the route that can find every ``.rockbox`` mount and unpack on
        the device itself, so the card asks for the cable as its own step and
        gives the player a whole watch window to answer before a mass-storage
        folder is offered. The presence watch still runs behind the question,
        so a player that is already plugged in answers it for the user.
        """
        self._stop_adb_presence_watch()
        self._stop_removable_drive_watch()
        self.themes.ask_usb()
        model_str = self.themes.model.upper()
        is_mass_storage = bool(getattr(self.themes, "mass_storage", False))
        if is_mass_storage:
            self._log_theme(
                f"[Rockbox Themes] Step 2: Prompting user to connect {model_str} by USB transfer mode."
            )
            self.show_flash_question(
                tr("rockbox_step2_connect_usb_storage").format(model=model_str)
            )
        else:
            self._log_theme(
                f"[Rockbox Themes] Step 2: Prompting user to connect {model_str} by USB."
            )
            self.show_flash_question(
                tr("rockbox_step2_connect_usb").format(model=model_str)
            )
        self._eta_label.setText("")
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(True)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(True)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(True)
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
        if is_mass_storage:
            self._start_removable_drive_watch()
        else:
            self._ensure_adb_presence_watch()
        self._request_user_attention()

    def start_rockbox_themes_stage(
        self,
        model="Y1",
        on_done=None,
        on_recover=None,
        log_cb=None,
        on_troubleshoot=None,
        already_powered_on=False,
        automatic=False,
        after_firmware=False,
        purpose="themes",
    ) -> None:
        """Seamless in-card Rockbox post-install flow.

        ``after_firmware`` is True when a Rockbox firmware install came first;
        the bar then resumes at 90%. A standalone run starts from 0%.
        ``purpose`` is ``"themes"`` for the bundled 360p themes and fonts, or
        ``"cleanup"`` to remove the player's old ``.rockbox`` folders when Keep
        my settings was left unticked; the guidance, watcher and fallbacks are
        the same for both.
        """
        self.dismiss_prompt_stop()
        # A new stage owns detection from here: no watcher from the previous
        # journey may answer for this one before its own step starts watching.
        self._stop_adb_presence_watch()
        self.themes.begin(model, automatic=automatic, purpose=purpose)
        self._theme_stage_done_cb = on_done
        self._theme_stage_recover_cb = on_troubleshoot or on_recover
        self._rockbox_power_on_continue_cb = None
        self._theme_log_cb = log_cb
        self._failed = False
        self._complete = False
        self.show_flashing()
        # The themes guidance lives inside the progress card. Donations hide that
        # card only on the completion screen, so keep it up for this stage.
        if hasattr(self, "_progress_card"):
            self._progress_card.setVisible(True)
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

        if hasattr(self, "_action_hold_timer") and self._action_hold_timer is not None:
            self._action_hold_timer.stop()
        self._action_pending_text = None
        self._action_shown_text = ""
        if hasattr(self, "_action_label"):
            self._action_label.setText("")
            self._action_label.setVisible(False)
        self.update_partition_progress("")
        if hasattr(self, "_partition_progress_label") and self._partition_progress_label is not None:
            self._partition_progress_label.clear()
            self._partition_progress_label.setVisible(False)

        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(True)

        step_text = tr("rockbox_cleanup_step") if self._cleanup_stage else tr("rockbox_stage_installing")
        self.set_step(step_text)
        self._progress_bar.set_precise_value(90.0 if after_firmware else 0.0)

        model_str = (model or "Y1").upper()
        self._rockbox_already_powered_on = bool(already_powered_on)
        if self._cleanup_stage:
            self._log_theme(
                f"[Rockbox Cleanup] Starting .rockbox removal stage for {model_str}..."
            )
        else:
            self._log_theme(f"[Rockbox Themes] Starting 360p themes & fonts stage for {model_str}...")

        from .. import simulation

        if simulation.enabled():
            self._start_simulated_themes_stage()
            return

        from ..catalog import is_rockbox_android_port
        pkg_name = getattr(self, "_package_name", "") or ""
        pkg = self._active_firmware_package()
        android_port = (
            not getattr(self.themes, "mass_storage", False)
            and is_rockbox_android_port(package=pkg, name=pkg_name)
        )
        if pkg is not None and getattr(pkg, "rockbox_folder_path", "") and not getattr(self.themes, "rockbox_folder_path", ""):
            self.themes.rockbox_folder_path = str(pkg.rockbox_folder_path)

        if not android_port:
            self.themes.mass_storage = True
            self._log_theme(
                f"[Rockbox Themes] Non-Android Rockbox port detected for {model_str}: "
                "skipping ADB and using USB storage mode."
            )
            if self._try_mass_storage_auto_transfer(fallback_to_picker=False):
                return
            if not already_powered_on and not automatic:
                self._show_theme_step1_disconnect_power_on()
                return
            self._show_theme_connect_usb_question()
            return

        from ..adb import find_adb_binary, adb_devices
        adb_bin = find_adb_binary()
        if not adb_bin:
            # No ADB client in this build, so the pack has to be placed through
            # USB storage mode. The guidance below still runs first — unplug,
            # boot the player, reconnect the cable — and only the folder picker
            # is left for the end. Opening with the folder would ask the user
            # for a drive the powered-off player cannot show yet.
            self._log_theme(
                "[Rockbox Themes] No ADB client was found on this host; the player "
                "is placed by hand after the power-on and USB steps."
            )
            existing_devices = []
        else:
            self._log_theme(f"[Rockbox Themes] Located ADB binary at: {adb_bin}")

            # Seamless mode: only a single player can be addressed over ADB —
            # with more than one attached they share one serial, so every -s
            # command fails. Fall through to the watch instead, which asks the
            # user to disconnect down to one before the transfer starts.
            existing_devices = adb_devices(adb_bin)
        if len(existing_devices) == 1:
            serial = existing_devices[0]
            self._log_theme(f"[Rockbox Themes] Seamless match: ADB device {serial} already detected! Pushing themes silently...")
            self._on_adb_device_found(serial)
            return
        if len(existing_devices) > 1:
            self._log_theme(
                f"[Rockbox Themes] {len(existing_devices)} players are connected over ADB; "
                "asking the user to disconnect all but the one being installed."
            )

        # If MTKClient's post-flash handoff already asked the user to boot the
        # player, go straight to the USB/ADB reconnect step instead of asking
        # them to power it on a second time.
        # SP Flash installs never need the manual disconnect/power-on steps:
        # go straight to the silent ADB watch. MTKClient installs still do.
        if automatic:
            # Nothing asked the user anything (SP Flash Tool reboots the player
            # itself), so the watch is the whole step.
            self._start_theme_step2_connect_usb()
            return
        if getattr(self.themes, "is_standalone", False):
            # Standalone/drag-and-drop prompt already asked the user to connect
            # USB and power on before clicking Next; go straight to the ADB watch.
            self._start_theme_step2_connect_usb()
            return
        if already_powered_on:
            # The window already asked for the boot; this stage's own question
            # is the cable that brings the player back.
            self._show_theme_connect_usb_question()
            return
        self._show_theme_step1_disconnect_power_on()

    @property
    def _cleanup_stage(self) -> bool:
        """This stage deletes the player's old ``.rockbox`` folders."""
        return bool(getattr(self.themes, "is_cleanup", False))

    def start_rockbox_cleanup_stage(
        self,
        model="Y1",
        on_done=None,
        on_recover=None,
        log_cb=None,
        already_powered_on=False,
        automatic=False,
        after_firmware=True,
    ) -> None:
        """Remove the player's old ``.rockbox`` folders as an install step.

        Keep my settings unticked is the user asking for a fresh start, so the
        Rockbox payload goes with the data partition. The guidance is the
        themes stage's own (power on, USB, ADB, storage-mode fallback), which is
        why this is that stage with a different purpose rather than a second
        wizard.
        """
        self.start_rockbox_themes_stage(
            model=model,
            on_done=on_done,
            on_recover=on_recover,
            log_cb=log_cb,
            already_powered_on=already_powered_on,
            automatic=automatic,
            after_firmware=after_firmware,
            purpose="cleanup",
        )

    def _on_cleanup_progress(self, curr: int, total: int) -> None:
        from ..progress_model import scale_theme_progress

        if total <= 0:
            return
        self._progress_bar.set_precise_value(
            scale_theme_progress((float(curr) / max(1.0, float(total))) * 100.0)
        )
        if curr < total:
            self._eta_label.setText(tr("rockbox_cleanup_deleting"))

    def _start_adb_cleanup(self, serial: str) -> None:
        from ..adb import find_adb_binary
        from ..rockbox_cleanup import AdbRockboxCleanupWorker

        adb_bin = find_adb_binary()
        if not adb_bin:
            self._show_theme_fallback_usb_mode()
            return
        self.dismiss_flash_question()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        self.set_step(tr("rockbox_cleanup_step"))
        self._eta_label.setText(tr("rockbox_cleanup_deleting"))
        self.themes.reset_progress()
        worker = AdbRockboxCleanupWorker(adb_bin, serial, parent=self)
        self._rockbox_cleanup_worker = worker
        worker.progress.connect(self._on_cleanup_progress)
        worker.log.connect(self._log_theme)
        worker.finished.connect(self._on_cleanup_finished)
        worker.start()

    def _on_cleanup_finished(self, ok: bool, msg: str) -> None:
        if ok:
            self._log_theme(f"[Rockbox Cleanup] {msg}.")
            self._finish_rockbox_themes_stage()
            return
        self._log_theme(
            f"[Rockbox Cleanup] {msg}. Falling back to USB storage guidance."
        )
        self._show_theme_fallback_usb_mode()

    def _start_local_cleanup(self, chosen) -> None:
        from ..rockbox_cleanup import LocalRockboxCleanupWorker

        self._stop_adb_presence_watch()
        self._stop_removable_drive_watch()
        self._close_theme_folder_dialog()
        label = (
            ", ".join(str(p) for p in chosen)
            if isinstance(chosen, (list, tuple))
            else str(chosen)
        )
        self._log_theme(
            f"[Rockbox Cleanup] Selected folder(s): {label}. Looking for .rockbox folders..."
        )
        self.dismiss_flash_question()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        self.set_step(tr("rockbox_cleanup_step"))
        self._eta_label.setText(tr("rockbox_cleanup_deleting"))
        self.themes.reset_progress()
        worker = LocalRockboxCleanupWorker(
            chosen,
            parent=self,
            rockbox_folder_path=str(getattr(self.themes, "rockbox_folder_path", "") or ""),
            skip_adb_reboot=bool(getattr(self.themes, "mass_storage", False)),
        )
        self._rockbox_cleanup_worker = worker
        worker.progress.connect(self._on_cleanup_progress)
        worker.log.connect(self._log_theme)
        worker.finished.connect(self._on_local_cleanup_finished)
        worker.start()

    def _on_local_cleanup_finished(self, ok: bool, msg: str, count: int) -> None:
        if ok:
            self._log_theme(f"[Rockbox Cleanup] {msg}.")
            self._finish_rockbox_themes_stage()
            return
        self._log_theme(
            f"[Rockbox Cleanup] {msg}. Falling back to USB storage guidance."
        )
        self._show_theme_fallback_usb_mode()

    def _show_theme_step1_disconnect_power_on(self) -> None:
        model_str = (self.themes.model or self._model or "").upper()
        pkg_name = getattr(self, "_package_name", "") or ""
        if "for Y2" in pkg_name or "for y2" in pkg_name.lower():
            model_str = "Y2"
        elif "Y2" in model_str or "Y2" in getattr(self, "_model", "").upper():
            model_str = "Y2"
        elif "Y1" in model_str:
            model_str = "Y1"
        self.themes.clear_usb()
        self._log_theme(f"[Rockbox Themes] Step 1: Prompting user to disconnect and power on {model_str}.")
        self._eta_label.setText("")
        # Powering the player on is the question's subject, so it takes the
        # band's slot with the Next chip beside it rather than sitting under a
        # bar that has no percentage to report here.
        is_standalone = getattr(self.themes, "is_standalone", False)
        self.show_flash_question(self.rockbox_power_on_question(model_str, standalone=is_standalone))
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
        self._ensure_adb_presence_watch()
        self._request_user_attention()

    def _start_theme_step2_connect_usb(self) -> None:
        from ..adb import find_adb_binary

        if not find_adb_binary():
            # Nothing can be pushed without a client, so a countdown would only
            # report a wait that can never end. The user has already been asked
            # to boot the player and to connect it by cable (or SP Flash Tool
            # rebooted it), so the drive that appears is the remaining route.
            self._log_theme(
                "[Rockbox Themes] No ADB client on this host; continuing with USB "
                "storage mode."
            )
            self._show_theme_fallback_usb_mode()
            return

        # This step runs its own countdown watch; two watchers would both claim
        # the same player and start the push twice.
        self._stop_adb_presence_watch()
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

        model_str = self.themes.model.upper()
        from ..adb import ADB_WATCH_TIMEOUT_SECONDS
        self._adb_watch_seconds = ADB_WATCH_TIMEOUT_SECONDS
        self.themes.clear_multiple()
        self.themes.clear_usb()
        usb_prompt = tr("rockbox_step2_connect_usb").format(model=model_str)
        adb_status = tr("rockbox_stage_waiting_adb_fmt").format(remaining=self._adb_watch_seconds)
        # The question keeps the band while the watch runs, with the countdown
        # as its explanation. Only the elapsed window hands the user a folder
        # to pick: the Back chip has nowhere to go until then. An automatic
        # stage asks nothing — SP Flash Tool already rebooted the player, so
        # the card only reports the wait.
        if self.themes.automatic:
            self.dismiss_flash_question()
        else:
            self.show_flash_question(usb_prompt)
        self._eta_label.setText(adb_status if (self._cleanup_stage or not self.themes.automatic) else "")
        if hasattr(self, "_theme_step1_next_btn"):
            self._theme_step1_next_btn.setVisible(False)
        if hasattr(self, "_theme_fallback_next_btn"):
            self._theme_fallback_next_btn.setVisible(False)
        if hasattr(self, "_theme_back_btn"):
            self._theme_back_btn.setEnabled(False)
            self._theme_back_btn.setVisible(True)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_chips_pairing()
            self._flash_head._sync_to_band()
        self._log_theme(f"[Rockbox Themes] Step 2: Waiting for USB connection and ADB handshake ({self._adb_watch_seconds}s timeout)...")

        from ..theme_pack import AdbWatchWorker
        adb_bin = find_adb_binary()

        self._adb_watch_worker = AdbWatchWorker(adb_bin, timeout=float(self._adb_watch_seconds), parent=self)
        self._adb_watch_worker.found.connect(self._on_adb_device_found)
        self._adb_watch_worker.timed_out.connect(self._show_theme_fallback_usb_mode)
        self._adb_watch_worker.multiple.connect(self._on_adb_multiple_devices)

        self._adb_watch_timer = QTimer(self)
        self._adb_watch_timer.setInterval(1000)
        self._adb_watch_timer.timeout.connect(self._on_adb_watch_tick)
        self._adb_watch_timer.start()
        self._adb_watch_worker.start()

    def _on_adb_watch_tick(self) -> None:
        if getattr(self, "_adb_watch_seconds", 0) > 0:
            self._adb_watch_seconds -= 1
            if self.themes.multiple_players:
                # The disconnect prompt owns the copy until one player remains,
                # and it is the card's question, so it takes the band.
                self.show_flash_question(tr("rockbox_stage_disconnect_extra"))
            else:
                adb_status = tr("rockbox_stage_waiting_adb_fmt").format(remaining=self._adb_watch_seconds)
                self._eta_label.setText(adb_status if (self._cleanup_stage or not self.themes.automatic) else "")
            if self._adb_watch_seconds in (100, 80, 60, 40, 20, 10, 5):
                self._log_theme(f"[Rockbox Themes] Waiting for ADB device... ({self._adb_watch_seconds}s remaining)")
        else:
            if hasattr(self, "_adb_watch_timer"):
                self._adb_watch_timer.stop()

    def _on_adb_multiple_devices(self, count: int) -> None:
        """Two players share one serial: ask the user to disconnect one."""
        self.themes.note_multiple()
        self._log_theme(
            f"[Rockbox Themes] {count} players are connected over ADB. "
            "Waiting for the user to disconnect all but the one being installed."
        )
        self.show_flash_question(tr("rockbox_stage_disconnect_extra"))

    def _on_adb_device_found(self, serial: str) -> None:
        # A player answered: a picker open on a stale folder choice has lost.
        self._close_theme_folder_dialog()
        if self.themes.prompt_pending:
            # The user was asked to power the player on; it answered instead.
            # Pressing Continue for them keeps the stage's own wiring (watch,
            # completion, donation screen) in charge of what happens next.
            self.themes.settle_prompt()
            self._stop_adb_presence_watch()
            self.dismiss_flash_question()
            if hasattr(self, "_theme_row"):
                self._theme_row.setVisible(False)
            if hasattr(self, "_theme_step1_widget"):
                self._theme_step1_widget.setVisible(False)
            if hasattr(self, "_theme_fallback_widget"):
                self._theme_fallback_widget.setVisible(False)
            if hasattr(self, "_theme_trouble_widget"):
                self._theme_trouble_widget.setVisible(False)
            self._hide_theme_wizard_chips()
            callback = getattr(self, "_rockbox_power_on_continue_cb", None)
            self._log_theme(
                f"[Rockbox Themes] Player detected over ADB ({serial}); continuing the stage."
            )
            if callable(callback):
                callback()
                return
        # One player, one transfer: claim the player or take for retry
        self.themes.take_player()
        self._stop_adb_presence_watch()
        # The player answered: the card is writing again, not asking.
        self.dismiss_flash_question()
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        if self.themes.is_apk_stage:
            self._log_theme(f"[APK Install] Player detected via ADB (serial: {serial}). Installing application...")
            self._eta_label.setText(tr("rockbox_stage_installing"))
            self._start_adb_theme_push(serial)
            return
        if self._cleanup_stage:
            self._log_theme(
                f"[Rockbox Cleanup] Player detected via ADB (serial: {serial}). "
                "Removing the old .rockbox folders..."
            )
            self._eta_label.setText(tr("rockbox_cleanup_deleting"))
            self._start_adb_cleanup(serial)
            return
        from ..adb import find_adb_binary, adb_find_all_rockbox_dirs
        adb_bin = find_adb_binary()
        if adb_bin and adb_find_all_rockbox_dirs(adb_bin, serial):
            self._log_theme(f"[Rockbox Themes] Player detected via ADB (serial: {serial}) with .rockbox ready. Starting push...")
            self._eta_label.setText(tr("rockbox_stage_pushing"))
            self._start_adb_theme_push(serial)
            return

        self._log_theme(f"[Rockbox Themes] Player detected via ADB (serial: {serial}). Waiting for Rockbox to populate .rockbox (up to {ROCKBOX_BOOT_WAIT_MS // 1000}s)...")
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        wait_ms = min(1000, ROCKBOX_BOOT_WAIT_MS) if ROCKBOX_BOOT_WAIT_MS > 0 else 0
        QTimer.singleShot(wait_ms, lambda: self._start_adb_theme_push(serial))

    def _start_adb_theme_push(self, serial: str) -> None:
        if self.themes.is_apk_stage:
            self._log_theme("[APK Install] Installing application package over ADB...")
            self._eta_label.setText(tr("rockbox_stage_installing"))
            self.themes.reset_progress()
            from ..adb import find_adb_binary
            from ..theme_pack import AdbApkInstallWorker

            adb_bin = find_adb_binary()
            self._adb_push_worker = AdbApkInstallWorker(
                adb_bin,
                serial,
                apk_path=self.themes.apk_path,
                apk_url=self.themes.apk_url,
                parent=self,
            )
            self._adb_push_worker.progress.connect(self._on_theme_push_progress)
            self._adb_push_worker.log.connect(self._log_theme)
            self._adb_push_worker.finished.connect(self._on_theme_push_finished)
            self._adb_push_worker.start()
            return
        self._log_theme("[Rockbox Themes] Pushing 360p themes & fonts over ADB...")
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        self.themes.reset_progress()
        from ..adb import find_adb_binary
        from ..theme_pack import AdbPushWorker, should_apply_theme_path_fix
        adb_bin = find_adb_binary()
        model = getattr(self.themes, "model", "") or getattr(self, "_model", "")
        rb_themes_path_fix = getattr(self.themes, "rb_themes_path_fix", None)
        apply_fix = should_apply_theme_path_fix(model=model, rb_themes_path_fix=rb_themes_path_fix)

        custom_zip = getattr(self.themes, "payload_zip_path", None)
        if custom_zip and Path(custom_zip).is_file():
            norm_zip = Path(custom_zip)
        else:
            norm_zip = None

        self._adb_push_worker = AdbPushWorker(
            adb_bin,
            serial,
            local_dir=None,
            parent=self,
            zip_path=norm_zip,
            model=model,
            rb_themes_path_fix=rb_themes_path_fix,
        )
        self._adb_push_worker.progress.connect(self._on_theme_push_progress)
        self._adb_push_worker.log.connect(self._log_theme)
        self._adb_push_worker.status.connect(self.set_theme_download_status)
        self._adb_push_worker.finished.connect(self._on_theme_push_finished)
        self._adb_push_worker.start()

    def _start_simulated_themes_stage(self) -> None:
        """The themes stage with no player attached: same widgets, no ADB."""
        self._log_theme(
            "[Simulation] Hardware skipped: the themes and fonts push completes itself."
        )
        self.set_step(tr("rockbox_stage_installing"))
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        for attr in ("_theme_row", "_theme_step1_widget", "_theme_fallback_widget", "_theme_trouble_widget"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setVisible(False)
        self._hide_theme_wizard_chips()
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        self.themes.reset_progress()
        self._sim_theme_percent = 0
        timer = getattr(self, "_sim_theme_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.setInterval(120)
            timer.timeout.connect(self._sim_theme_tick)
            self._sim_theme_timer = timer
        timer.start()

    def _sim_theme_tick(self) -> None:
        self._sim_theme_percent = min(100, self._sim_theme_percent + 5)
        self._on_theme_push_progress(self._sim_theme_percent, 100)
        if self._sim_theme_percent >= 100:
            self._sim_theme_timer.stop()
            # The success path, unchanged: verification, placement, hand-off.
            QTimer.singleShot(250, lambda: self._on_theme_push_finished(True, ""))

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
        if self.themes.progress_log_due(curr, total):
            if total == 100:
                self._log_theme(f"[Rockbox Themes] Theme pack transfer progress: {curr}%...")
            else:
                self._log_theme(f"[Rockbox Themes] Pushed {curr}/{total} theme files to player ({int(pct)}%)...")

    def _on_theme_push_finished(self, ok: bool, msg: str) -> None:
        # A download line must never outlive the transfer it described.
        self.set_theme_download_status("")
        if ok:
            self._log_theme("[Rockbox Themes] Themes and fonts were transferred and verified successfully via ADB.")
            self._finish_rockbox_themes_stage()
        else:
            self.themes.adb_attempted = True
            self._log_theme(f"[Rockbox Themes] ADB theme push encountered an issue: {msg}. Falling back to USB storage guidance.")
            if self._try_mass_storage_auto_transfer(fallback_to_picker=False):
                return
            self._show_theme_fallback_usb_mode()

    def _show_theme_fallback_usb_mode(self) -> None:
        self.themes.settle_prompt()
        self.themes.reset_player()
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(True)
        if self._cleanup_stage:
            self._log_theme(
                "[Rockbox Cleanup] Guiding user: Please enable USB storage mode to remove "
                "the old .rockbox folders."
            )
        else:
            self._log_theme("[Rockbox Themes] Guiding user: Please enable USB storage mode.")
        # A cleanup that has not started removing anything yet has no step to
        # report: the card is asking for the player's drive, and "Removing the
        # old Rockbox settings" would claim work that is not happening.
        self.set_step("" if self._cleanup_stage else tr("rockbox_stage_installing"))
        self._eta_label.setText("")
        # The storage-mode instruction is the card's current question, so it
        # takes the band with the folder chip beside it — and it names the drive
        # as the thing to choose, because that chip is the folder picker.
        model_str = self.themes.model.upper()
        self.show_flash_question(
            tr("rockbox_step_usb_storage_folder").format(model=model_str)
        )
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
        # Keep looking for the player while the user is in USB storage mode and
        # behind the folder picker: ADB (or removable mount) can finish by itself.
        if getattr(self.themes, "mass_storage", False):
            self._start_removable_drive_watch()
        else:
            self._ensure_adb_presence_watch()
        self._request_user_attention()

    def _show_theme_manual_prompt(self) -> None:
        self._show_theme_fallback_usb_mode()

    def _on_theme_fallback_next_clicked(self) -> None:
        """Open the folder picker without blocking the journey.

        A blocking ``getExistingDirectory`` starves the event loop, so a player
        that appears over ADB while the user is browsing would not be noticed
        until the picker closed. Opening the dialog keeps this page live: the
        presence watcher can dismiss it and take the transfer over.
        """
        import sys
        from PySide6.QtWidgets import QFileDialog
        from ..downloads import default_picker_dir

        if self._try_mass_storage_auto_transfer(fallback_to_picker=False):
            return

        title = tr("rockbox_step_select_rockbox_folder") if sys.platform == "win32" else tr("rockbox_step_select_player_folder")
        if sys.platform == "win32":
            chosen = QFileDialog.getExistingDirectory(
                self,
                title,
                default_picker_dir(),
                QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontResolveSymlinks,
            )
            if chosen:
                self._on_theme_folder_chosen(chosen)
            return

        existing = self.themes.folder_dialog
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        self._eta_label.setText(title)
        self._log_theme(f"[Rockbox Themes] Prompting user to select player drive/folder: {title}")
        dialog = QFileDialog(self, title)
        dialog.setFileMode(QFileDialog.FileMode.Directory)
        dialog.setOption(QFileDialog.Option.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.Option.DontResolveSymlinks, True)
        # Without an explicit start folder the native panel can inherit the
        # launch/current directory. If that is inside Documents, macOS treats
        # the picker as Documents access and prompts for it.
        dialog.setDirectory(default_picker_dir())
        dialog.fileSelected.connect(self._on_theme_folder_chosen)
        dialog.finished.connect(self._on_theme_folder_dialog_finished)
        self.themes.attach_folder_dialog(dialog)
        dialog.open()

    def _on_theme_folder_dialog_finished(self, *_args) -> None:
        """Picked, cancelled or dismissed by detection: forget the dialog."""
        dialog = self.themes.take_folder_dialog()
        if dialog is not None:
            dialog.deleteLater()

    def _close_theme_folder_dialog(self) -> None:
        """Dismiss an open picker (a player answered, or the stage ended)."""
        dialog = self.themes.take_folder_dialog()
        if dialog is None:
            return
        try:
            dialog.reject()
        except Exception:
            logger.debug("Could not dismiss the folder picker", exc_info=True)

    def _start_mass_storage_merge(self, target_dirs: list[Path]) -> None:
        """Merge themes & fonts into one or more external-storage ``.rockbox`` dirs."""
        from ..theme_pack import DirMergeWorker, should_apply_theme_path_fix

        self._close_theme_folder_dialog()
        self._stop_adb_presence_watch()
        self._stop_removable_drive_watch()
        self.dismiss_flash_question()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()

        dests = [Path(d) for d in target_dirs if d]
        dest_label = ", ".join(str(d) for d in dests)
        model = getattr(self.themes, "model", "") or getattr(self, "_model", "")
        rb_themes_path_fix = getattr(self.themes, "rb_themes_path_fix", None)
        apply_fix = should_apply_theme_path_fix(model=model, rb_themes_path_fix=rb_themes_path_fix)
        custom_zip = getattr(self.themes, "payload_zip_path", None)
        payload_zip = Path(custom_zip) if custom_zip and Path(custom_zip).is_file() else None
        if payload_zip is None:
            # The worker downloads/validates the full pack itself: doing it here
            # would freeze the page while the user waits in USB storage mode.
            self._log_theme("[Rockbox Themes] Preparing the complete themes & fonts pack...")
        self._log_theme(f"[Rockbox Themes] Merging themes & fonts payload into {dest_label}...")
        self._eta_label.setText(tr("rockbox_stage_pushing"))
        self.themes.reset_progress()

        self._dir_merge_worker = DirMergeWorker(
            dst_dirs=dests,
            parent=self,
            zip_path=payload_zip,
            ensure_full=True,
            apply_path_fix=apply_fix,
        )
        self._dir_merge_worker.progress.connect(self._on_theme_push_progress)
        self._dir_merge_worker.log.connect(self._log_theme)
        self._dir_merge_worker.finished.connect(
            lambda ok, err, count: self._on_dir_merge_finished(ok, err, count, dest_label)
        )
        self._dir_merge_worker.start()

    def _on_theme_folder_chosen(self, chosen: str) -> None:
        if not chosen:
            # Cancelled. The presence watcher keeps running, so the user can
            # retry or simply plug the player in.
            return
        if not getattr(self.themes, "mass_storage", False):
            from ..adb import find_adb_binary, adb_devices
            adb_bin = find_adb_binary()
            if adb_bin:
                try:
                    live_devices = adb_devices(adb_bin)
                except Exception:
                    live_devices = []
                if self.themes.prefer_player(len(live_devices)):
                    self._on_adb_device_found(live_devices[0])
                    return
        self._close_theme_folder_dialog()
        self._stop_adb_presence_watch()
        self._stop_removable_drive_watch()
        self.dismiss_flash_question()
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(False)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(False)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        self._hide_theme_wizard_chips()
        if self._cleanup_stage:
            self._start_local_cleanup(chosen)
            return
        if self.themes.is_apk_stage:
            import shutil
            from ..theme_pack import ensure_apk_file

            self._stop_adb_presence_watch()
            try:
                apk_file = ensure_apk_file(self.themes.apk_path, self.themes.apk_url)
                dest_dir = Path(chosen)
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(apk_file, dest_dir / apk_file.name)
                self._finish_rockbox_themes_stage(manual_count=1, dest=str(dest_dir))
            except Exception as e:
                self._giveup_rockbox_themes_stage(str(e))
            return
        from ..theme_pack import find_or_create_rockbox_dir

        hint = str(getattr(self.themes, "rockbox_folder_path", "") or "")
        dest_dir = find_or_create_rockbox_dir(chosen, rockbox_folder_path=hint)
        self._log_theme(f"[Rockbox Themes] Selected folder: {chosen}. Resolved target .rockbox: {dest_dir}")
        self._start_mass_storage_merge([dest_dir])

    def _on_dir_merge_finished(self, ok: bool, err: str, count: int, dest: str) -> None:
        """Only declare the theme stage complete once files were really copied."""
        if ok and count > 0:
            self._finish_rockbox_themes_stage(manual_count=count, dest=dest)
            return
        reason = err or "no theme files were copied"
        # The automation already retried (zip unpack, then per-file push), the
        # guidance then named this folder pick as the last resort, and it still
        # could not write. Looping the USB guidance would strand the user here:
        # hand back to the completion screen with the try-again copy instead.
        self._giveup_rockbox_themes_stage(reason)

    def _giveup_rockbox_themes_stage(self, reason: str) -> None:
        """Placement failed after the journey's retries: end on the completion
        ``/`` donation screen once, carrying ``themes_placement_failed_note``."""
        if not self.themes.transfer_running:
            return  # already finished or given up — never route twice.
        self._log_theme(
            f"[Rockbox Themes] Unable to place the themes pack ({reason}). "
            "Finishing with the completion screen."
        )
        self._stop_adb_presence_watch()
        self._stop_removable_drive_watch()
        self.themes.mark_placement_failed()
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(False)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        self._hide_theme_wizard_chips()
        self.stop_theme_workers()
        cb = getattr(self, "_theme_stage_done_cb", None)
        self._theme_stage_done_cb = None
        if callable(cb):
            cb()

    def _on_theme_yes_clicked(self) -> None:
        self._on_theme_fallback_next_clicked()

    def _on_theme_no_clicked(self) -> None:
        model = self.themes.model.upper()
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
        # Powering the player on is exactly the moment ADB can answer for it.
        self._ensure_adb_presence_watch()
        self._request_user_attention()

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
            win.restore_original_software(self.themes.model)

    def _finish_rockbox_themes_stage(self, manual_count=None, dest=None) -> None:
        if manual_count is not None:
            self._log_theme(f"[Rockbox Themes] Successfully merged {manual_count} theme & font files into {dest}!")
        if self._cleanup_stage:
            self._log_theme("[Rockbox Cleanup] Removing the old .rockbox folders finished.")
        else:
            self._log_theme("[Rockbox Themes] Rockbox 360p themes & fonts stage completed.")
        self._stop_adb_presence_watch()
        self._stop_removable_drive_watch()
        self.themes.finish()
        if hasattr(self, "_adb_watch_timer"):
            self._adb_watch_timer.stop()
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(False)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(False)
        self._hide_theme_wizard_chips()
        self.dismiss_flash_question()
        self._progress_bar.set_precise_value(100.0)
        self.set_device_done()
        cb = getattr(self, "_theme_stage_done_cb", None)
        if callable(cb):
            cb()

    def show_rockbox_power_on_prompt(
        self, prompt: str, on_continue=None, on_recover=None, after_firmware=False, purpose="themes"
    ) -> None:
        """Keep the install card active while the user boots the flashed player."""
        self._failed = False
        self._complete = False
        self._themes_stage_done_cb = None
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
        if hasattr(self, "_progress_card"):
            self._progress_card.setVisible(True)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(False)
        if hasattr(self, "_action_hold_timer") and self._action_hold_timer is not None:
            self._action_hold_timer.stop()
        self._action_pending_text = None
        self._action_shown_text = ""
        if hasattr(self, "_action_label") and self._action_label is not None:
            self._action_label.clear()
            self._action_label.setVisible(False)
        self.update_partition_progress("")
        if hasattr(self, "_partition_progress_label") and self._partition_progress_label is not None:
            self._partition_progress_label.clear()
            self._partition_progress_label.setVisible(False)
        step_text = tr("rockbox_cleanup_step") if (purpose == "cleanup" or self._cleanup_stage) else tr("rockbox_stage_installing")
        self.set_step(step_text)
        self._progress_bar.set_precise_value(90.0 if after_firmware else 0.0)
        self._eta_label.setText("")
        # The boot question takes the band's slot, with the Next chip that
        # answers it beside the words.
        self.show_flash_question(prompt)
        if hasattr(self, "_status_flow"):
            self._status_flow.set_forced_stacked(True)
        self._rockbox_power_on_continue_cb = on_continue
        self.themes.open_prompt()
        if hasattr(self, "_theme_step1_widget"):
            self._theme_step1_widget.setVisible(True)
        self._theme_step1_next_btn.setText(tr("dialog_pre_install_continue"))
        self._hide_theme_wizard_chips()
        self._theme_step1_next_btn.setVisible(True)
        if hasattr(self, "_theme_back_btn"):
            # Segmented back/next with the back side disabled: the firmware is
            # already installed, so this step has nothing to go back to.
            self._theme_back_btn.setEnabled(False)
            self._theme_back_btn.setVisible(True)
        if hasattr(self, "_theme_row"):
            self._theme_row.setVisible(True)
        if hasattr(self, "_theme_fallback_widget"):
            self._theme_fallback_widget.setVisible(True)
        if hasattr(self, "_theme_trouble_widget"):
            self._theme_trouble_widget.setVisible(False)
        if hasattr(self, "_flash_head"):
            self._flash_head._sync_to_band()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()
        # A player that appears now still wins: the user may already have
        # mirrored the wait by connecting it.
        self._ensure_adb_presence_watch()
        self._request_user_attention()

    def _on_rockbox_power_on_continue(self) -> None:
        self.themes.settle_prompt()
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
        if getattr(self, "_rockbox_themes_prompt", False):
            return "rockbox_y1_theme_warning_prompt"
        if getattr(self, "_permission_prompt", False):
            return "install_linux_permission_prompt"
        if getattr(self, "_power_off_only", False):
            return "flash_power_off_only_fmt"
        return "flash_connect_prompt"

    def _power_off_prompt_text(self) -> str:
        """The power-off / recovery line, with its own fields applied.

        Both the recovery wording and the plain one take ``{model}``; the
        connect-failure wording also names the engine that failed and the one
        taking over, which only its own call site supplies.
        """
        if getattr(self, "_rockbox_themes_prompt", False):
            model_text = self._connect_model_text() or "Y1"
            text = tr("rockbox_y1_theme_warning_prompt")
            if model_text != "Y1":
                text = (
                    text.replace("on Y1", f"on {model_text}")
                    .replace("sur Y1", f"sur {model_text}")
                    .replace("auf dem Y1", f"auf dem {model_text}")
                    .replace("en Y1", f"en {model_text}")
                    .replace("Y1 上的", f"{model_text} 上的")
                    .replace("Y1 の", f"{model_text} の")
                    .replace("Y1의", f"{model_text}의")
                    .replace("на Y1", f"на {model_text}")
                    .replace("na Y1", f"na {model_text}")
                    .replace("su Y1", f"su {model_text}")
                )
            return text
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
        self._complete = False
        self._prompt_only = True
        self._wait_copy = "power_off"
        self._power_off_recovery = False
        self._permission_prompt = bool(permission)
        self._rockbox_themes_prompt = False
        self._power_off_recovery_key = ""
        self._power_off_recovery_kwargs = {}
        self._show_pin_tip = show_pin_tip
        self.set_attempt_timeout(0)
        self.clear_install_failure()
        self.show_waiting()
        self._reset_connect_prompt_delay()
        self.clear_keep_data_confirmation()
        self._set_yes_no_glyphs(False)
        prompt = self._power_off_prompt_text()
        self._wait_prompt_label.setText(prompt)
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_force_hidden = False
        self._sync_keep_data_visibility()
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
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_rockbox_themes_prompt(self) -> None:
        """Prompt explaining the 360p themes requirement inside the wizard modal before power-off."""
        self._failed = False
        self._complete = False
        self._prompt_only = True
        self._wait_copy = "power_off"
        self._power_off_recovery = False
        self._permission_prompt = False
        self._rockbox_themes_prompt = True
        self.set_attempt_timeout(0)
        self.clear_install_failure()
        self.show_waiting()
        self._reset_connect_prompt_delay()
        self.clear_keep_data_confirmation()
        self._set_yes_no_glyphs(False)
        prompt = self._power_off_prompt_text()
        self._wait_prompt_label.setText(prompt)
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_force_hidden = True
        self._sync_keep_data_visibility()
        self._set_determinate(self._wait_progress_bar, 0)
        self._wait_cancel_btn.setVisible(True)
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        self._update_paperclip_hint()
        self._apply_stop_hold()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def set_rockbox_themes_prompt(self, enabled: bool) -> None:
        self._rockbox_themes_prompt = bool(enabled)

    def _keep_data_visible(self) -> bool:
        """The tick is only for the power-off-and-disconnect prompt.

        A release that cannot carry settings omits the tick entirely: offering
        it beside "this install will reset your settings" would promise the
        opposite of what the card says one line below.
        """
        return bool(
            getattr(self, "_has_userdata", True)
            and getattr(self, "_keep_data_allowed", True)
            and not getattr(self, "_format_all_required", False)
            and getattr(self, "_wait_copy", "") == "power_off"
            and not getattr(self, "_permission_prompt", False)
            and not getattr(self, "_rockbox_themes_prompt", False)
            and not getattr(self, "_keep_data_confirm_active", False)
            and not getattr(self, "_keep_data_force_hidden", False)
            and not getattr(self, "_keep_data_locked", False)
            and not getattr(self, "_complete", False)
            and not getattr(self, "_failed", False)
        )

    def _sync_keep_data_visibility(self) -> None:
        cb = getattr(self, "_keep_data_cb", None)
        if cb is None:
            return
        visible = self._keep_data_visible()
        cb.setVisible(visible)
        if not visible:
            self._keep_data_warning.setVisible(False)
        # Warning, explainer and microSD reassurance are set together there,
        # with the tick's own state and the user's dismissed-hint choices.
        self._update_keep_data_warning()

    def set_has_userdata(self, has_userdata: bool, preserve_data_default: bool = False) -> None:
        """Remember whether the package has user data and its default choice."""
        self._has_userdata = bool(has_userdata)
        if hasattr(self, "_keep_data_cb") and not self._keep_data_user_changed:
            self._keep_data_cb.setChecked(self._has_userdata and bool(preserve_data_default))
        self._sync_keep_data_visibility()

    def reset_keep_data_preference(self) -> None:
        self._keep_data_user_changed = False
        self._keep_data_locked = False
        self._keep_data_locked_value = False
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_cb.setChecked(False)

    def lock_keep_data(self, keep: bool) -> None:
        """Freeze this run's Keep Data answer for retries and failovers.

        The question has already been answered on the first attempt. A retry
        must not show the checkbox or ask the compatibility question again,
        whichever flash backend the retry uses; ``keep_data_checked`` keeps
        returning the answer the user gave.
        """
        self._keep_data_locked = True
        self._keep_data_locked_value = bool(keep)
        self._keep_data_user_changed = True
        self._sync_keep_data_visibility()

    def set_rockbox_install(self, rockbox: bool) -> None:
        """Remember that this install lays down Rockbox.

        Rockbox keeps its themes, fonts and presets in ``.rockbox``, which the
        install removes when Keep my settings is unticked. The warning beside
        the tick therefore has to name them, not only the firmware's own data.
        """
        rockbox = bool(rockbox)
        if rockbox == getattr(self, "_rockbox_install", False):
            return
        self._rockbox_install = rockbox
        self._update_keep_data_warning()

    def _keep_data_warning_key(self, *, y1_like: bool) -> str:
        """Which "if you continue without this" sentence fits this install."""
        if getattr(self, "_rockbox_install", False):
            return (
                "flash_keep_data_warning_rockbox_y1"
                if y1_like
                else "flash_keep_data_warning_rockbox"
            )
        return "flash_keep_data_warning_y1" if y1_like else "flash_keep_data_warning"

    def set_keep_data_available(self, available: bool, label: str = "") -> None:
        """Gate the tick on the connect / power-off card.

        The tick says only what it does. Which releases Keep Data can carry
        settings and media from is not part of the prep screen: it belongs to
        the confirmation that asks about it, not to the option itself.
        """
        self._keep_data_allowed = bool(available)
        if hasattr(self, "_keep_data_cb"):
            text = label or tr("flash_keep_data_option")
            self._keep_data_cb.setText(text)
            self._keep_data_cb.setAccessibleName(text)
        self._sync_keep_data_visibility()

    def set_format_all_required(self, required: bool, model: str = "") -> None:
        """Mark this firmware as needing Format All + Download.

        There is no keep-data choice to make in that mode: the format command
        erases the user data partition before the images are written. The
        prompt therefore hides the tick and shows what will be deleted.
        """
        self._format_all_required = bool(required)
        if model:
            self._model = str(model)
        self._sync_keep_data_visibility()
        self._update_keep_data_warning()

    def _keep_data_help_html(self) -> str:
        """The link that opens the "if you continue without this" line."""
        return _hint_text_link("keep-data-help", tr("keep_data_help_link"))

    def _on_keep_data_help(self, _link: str = "") -> None:
        self._keep_data_warning_revealed = True
        self._update_keep_data_warning()
        self._notify_geometry_change()

    def _on_keep_data_warning_got_it(self, _link: str = "") -> None:
        self._keep_data_warning_revealed = False
        _set_hint_dismissed("keep_data_warning")
        self._update_keep_data_warning()
        self._notify_geometry_change()

    def _update_keep_data_warning(self) -> None:
        """Explain what happens without the tick, in model-appropriate terms."""
        warning = getattr(self, "_keep_data_warning", None)
        cb = getattr(self, "_keep_data_cb", None)
        microsd_note = getattr(self, "_keep_data_microsd_note", None)
        note = getattr(self, "_keep_data_note", None)
        if warning is None or cb is None:
            return
        if getattr(self, "_keep_data_locked", False):
            warning.setVisible(False)
            help_link = getattr(self, "_keep_data_help_link", None)
            if help_link is not None:
                help_link.setVisible(False)
            if microsd_note is not None:
                microsd_note.setVisible(False)
            if note is not None:
                note.setVisible(False)
            return
        model = str(getattr(self, "_model", "") or "").upper()
        y1_like = "Y1" in model or "A5" in model
        wait_copy = getattr(self, "_wait_copy", "")
        format_required = bool(getattr(self, "_format_all_required", False))
        on_prompt = wait_copy in ("power_off", "usb_disconnected")
        visible = (
            bool(getattr(self, "_has_userdata", True))
            and wait_copy == "power_off"
            and not getattr(self, "_keep_data_confirm_active", False)
            and not getattr(self, "_permission_prompt", False)
            and not getattr(self, "_keep_data_force_hidden", False)
        )
        format_visible = bool(
            format_required
            and on_prompt
            and not getattr(self, "_keep_data_confirm_active", False)
            and not getattr(self, "_format_all_confirm_active", False)
            and not getattr(self, "_permission_prompt", False)
            and not getattr(self, "_keep_data_force_hidden", False)
        )
        text = ""
        if format_visible:
            key = (
                "format_all_data_warning_y1"
                if y1_like
                else "format_all_data_warning_other"
            )
            text = tr(key)
        elif visible:
            if not getattr(self, "_keep_data_allowed", True):
                key = (
                    "flash_keep_data_unavailable_y1"
                    if y1_like
                    else "flash_keep_data_unavailable"
                )
                text = tr(key)
            elif not cb.isChecked():
                text = tr(self._keep_data_warning_key(y1_like=y1_like))
        # Only the "if you continue without this" explanation waits behind a
        # link: an unavailable or format-all warning is news the reader needs
        # now and stays on the card. The link is the explanation's only door,
        # so once that Got it is used the link goes too: a door with nothing
        # behind it is worse than no door, and the hint is never re-shown.
        self._keep_data_warning_sentence = text
        explanation = bool(
            visible
            and getattr(self, "_keep_data_allowed", True)
            and not cb.isChecked()
            and not format_required
        )
        dismissed = _hint_dismissed("keep_data_warning")
        revealed = bool(getattr(self, "_keep_data_warning_revealed", False)) and not dismissed
        help_link = getattr(self, "_keep_data_help_link", None)
        if help_link is not None:
            wanted_link = self._keep_data_help_html()
            if help_link.text() != wanted_link:
                help_link.setText(wanted_link)
            help_link.setVisible(explanation and not revealed and not dismissed)
        if explanation:
            # Hidden until the link is followed, and hidden again for good once
            # its Got it is used: toggling the tick must not bring it back.
            body = (
                f"{text} {_hint_text_link('keep-data-got-it', tr('hint_got_it'))}"
                if revealed
                else ""
            )
        else:
            body = text
        if warning.text() != body:
            warning.setText(body)
        warning.setVisible(bool(body))
        if note is not None:
            # The tick explains itself only while it is on; the warning above
            # carries the explanation for the unchecked case instead.
            note_text = (
                tr("flash_keep_data_tooltip")
                if (
                    visible
                    and cb.isChecked()
                    and not format_required
                    and getattr(self, "_keep_data_allowed", True)
                )
                else ""
            )
            if note.text() != note_text:
                note.setText(note_text)
            note.setVisible(bool(note_text))
        if microsd_note is not None:
            # The microSD reassurance is part of the "if you continue without
            # this" explanation, not a line the card owes every reader: it is
            # only contextually relevant once that explanation is on screen.
            # The format-all warning already speaks for microSD media, and a
            # reader who put the reassurance away keeps it away.
            microsd_note.setVisible(
                revealed and not format_visible and not _hint_dismissed("microsd")
            )
        self._notify_geometry_change()

    def show_keep_data_confirmation(
        self,
        question: str,
        hint: str = "",
        choices=(),
        selected: str = "",
        on_no=None,
        show_keep_data_explainer: bool = True,
    ) -> None:
        """Ask for the currently installed firmware on the same progress card.

        With no ``choices`` this is a yes/no question about one compatible
        software item; ``on_no`` is the answer to "no", because there the No
        chip means "the player runs something else", not "stop the install".

        The tick never appears here: the user answered it on the power-off
        card, and showing it beside a different question only asks again. The
        explainer follows the same rule (the install-first question has none).
        """
        self._keep_data_confirm_active = True
        self._keep_data_confirm_no_cb = on_no
        self._keep_data_erase_confirm_active = False
        self._wait_copy = "keep_data_confirm"
        self._keep_data_force_hidden = True
        self._prompt_only = True
        self._wait_prompt_label.setText(question or "")
        self._wait_prompt_label.setVisible(False)
        self._keep_data_cb.setVisible(False)
        if hasattr(self, "_keep_data_warning"):
            self._keep_data_warning.setVisible(show_keep_data_explainer)
        if hasattr(self, "_keep_data_help_link"):
            self._keep_data_help_link.setVisible(show_keep_data_explainer)
        names = [str(c) for c in (choices or ()) if str(c).strip()]
        # The X leaves the install when the card offers a list of answers:
        # there it is the cancel chip beside an arrow, not a "no" answer. A
        # single yes/no question keeps its X as "No, that is not what runs".
        self._keep_data_cancel_leaves = len(names) > 1
        self._keep_data_choice.clear()
        self._keep_data_choice.addItems(names)
        if selected and selected in names:
            self._keep_data_choice.setCurrentText(selected)
        self._show_keep_data_choice(len(names) > 1)
        self._show_wait_question(question or "")
        self._keep_data_hint.setText(hint or "")
        self._keep_data_hint.setVisible(bool(hint))
        if hasattr(self, "_wait_pkg_label"):
            self._wait_pkg_label.setVisible(False)
        if hasattr(self, "_keep_data_note"):
            self._keep_data_note.setText("")
            self._keep_data_note.setVisible(False)
        if hasattr(self, "_keep_data_microsd_note"):
            # This question is about which software is on the player, not
            # about what an erase keeps; the reassurance belongs with the
            # explanation behind the power-off card's help link.
            self._keep_data_microsd_note.setVisible(False)
        # If there are dropdown choices, use Cancel (X) and Next (Arrow);
        # if a single Yes/No question, use No (X) and Yes (Checkmark ✓).
        self._set_yes_no_glyphs(len(names) <= 1)
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(True)
        self._wait_cancel_btn.setVisible(True)
        self._wait_cancel_btn.setEnabled(True)
        self._set_determinate(self._wait_progress_bar, 0)
        if hasattr(self, "_paperclip_hint_host"):
            self._paperclip_hint_host.setVisible(False)
        self._stack.setCurrentWidget(self._waiting_view)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_install_first_prompt(
        self,
        question: str,
        hint: str = "",
        choices=(),
        selected: str = "",
        on_no=None,
        next_action: bool = False,
    ) -> None:
        """Ask the Install Firmware First question on the same progress card.

        The prerequisite check is the Keep Data sanity check with a different
        question: one in-card prompt, a drop-down of software names, and the
        same X / tick chips. Sharing the renderer keeps the two journeys
        visually and behaviourally identical.
        """
        self.show_keep_data_confirmation(
            question,
            hint,
            choices,
            selected=selected,
            on_no=on_no,
            show_keep_data_explainer=False,
        )
        # This question's X is an answer ("the player already runs the
        # target"), not the cancel chip: leaving the journey is what the
        # window's own ``_cancel_install_first`` does.
        self._keep_data_cancel_leaves = False
        if next_action:
            # An instruction card ("power it on, then click Next"), not a
            # question: its chip moves the install on, so it takes the Next
            # arrow instead of the Yes tick a yes/no question gets.
            self._set_yes_no_glyphs(False)
        # Keep ``_wait_copy`` as the shared renderer set it: the Keep Data
        # branch is what keeps this question in the wait band across a resize.
        # The window's ``_install_first_stage`` is what makes it a prerequisite
        # question rather than the ordinary "which firmware?" check.

    def show_keep_data_erase_confirmation(self, question: str, explainer: str = "") -> None:
        """Ask whether to install and erase settings (or settings + media)."""
        self._keep_data_erase_confirm_active = True
        # X on the warning leaves the install: the user was asked to accept an
        # erase and said no, so there is nothing left to ask them.
        self._keep_data_cancel_leaves = True
        self._keep_data_confirm_active = False
        self._wait_copy = "keep_data_erase_confirm"
        self._keep_data_force_hidden = True
        self._prompt_only = True
        self._wait_prompt_label.setText(question or "")
        self._wait_prompt_label.setVisible(False)
        self._keep_data_cb.setVisible(False)
        self._show_keep_data_choice(False)
        self._show_wait_question(question or "")
        if hasattr(self, "_keep_data_warning"):
            self._keep_data_warning.setVisible(False)
        if hasattr(self, "_keep_data_note"):
            # Nothing is being kept on this path, so the tick's "keeps your
            # settings and media" line would flatly contradict the question.
            self._keep_data_note.setText("")
            self._keep_data_note.setVisible(False)
        self._keep_data_explainer = explainer or ""
        self._keep_data_hint.setText(_styled_prompt_links(self._keep_data_explainer))
        self._keep_data_hint.setVisible(bool(explainer))
        if hasattr(self, "_keep_data_microsd_note"):
            # The erase explainer below already says media on the microSD is
            # retained, so the standalone reassurance would only repeat it.
            self._keep_data_microsd_note.setVisible(False)
        self._set_yes_no_glyphs(True)
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(True)
        self._wait_cancel_btn.setVisible(True)
        self._wait_cancel_btn.setEnabled(True)
        self._set_determinate(self._wait_progress_bar, 0)
        if hasattr(self, "_paperclip_hint_host"):
            self._paperclip_hint_host.setVisible(False)
        self._stack.setCurrentWidget(self._waiting_view)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_format_all_confirmation(self, software: str, model: str = "") -> None:
        """Ask before retrying an 8502 install with Format All + Download.

        This is a destructive mode change, so it is a question on the same
        progress card rather than an automatic retry. The tick/Next chip is
        the confirmation; the X cancels the install.
        """
        from html import escape

        model_text = str(model or self._model or self._connect_model_text())
        y1_like = "Y1" in model_text.upper() or "A5" in model_text.upper()
        effect = tr(
            "format_all_data_warning_y1"
            if y1_like
            else "format_all_data_warning_other"
        )
        question = tr("format_all_confirm_question").format(model=model_text)
        body = tr("format_all_confirm_body").format(
            software=escape(str(software or self._package_name or "")),
            model=escape(model_text),
            data_effect=escape(effect),
        )
        self._format_all_confirm_active = True
        self._keep_data_confirm_active = False
        self._keep_data_erase_confirm_active = False
        self._wait_copy = "format_all_confirm"
        self._keep_data_force_hidden = True
        self._prompt_only = True
        self._failed = False
        self._complete = False
        self._wait_prompt_label.setVisible(False)
        self._keep_data_cb.setVisible(False)
        self._keep_data_warning.setVisible(False)
        if hasattr(self, "_keep_data_note"):
            self._keep_data_note.setText("")
            self._keep_data_note.setVisible(False)
        self._show_keep_data_choice(False)
        self._show_wait_question(question)
        self._keep_data_hint.setText(body.replace("\n", "<br>"))
        self._keep_data_hint.setVisible(True)
        if hasattr(self, "_keep_data_microsd_note"):
            self._keep_data_microsd_note.setVisible(False)
        self._set_yes_no_glyphs(True)
        self._wait_continue_btn.setVisible(True)
        self._wait_continue_btn.setEnabled(True)
        self._wait_cancel_btn.setVisible(True)
        self._wait_cancel_btn.setEnabled(True)
        self._set_determinate(self._wait_progress_bar, 0)
        if hasattr(self, "_paperclip_hint_host"):
            self._paperclip_hint_host.setVisible(False)
        self._stack.setCurrentWidget(self._waiting_view)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_format_all_power_off_prompt(self) -> None:
        """First-stage retry prompt after the format confirmation.

        It keeps the ordinary power-off card (so the format-only warning is
        visible) but uses the paperclip recovery line the user needs after a
        failed SP Flash Tool run.
        """
        self.show_power_off_prompt()
        self._power_off_recovery = True
        self._power_off_recovery_key = "format_all_recovery"
        self._power_off_recovery_kwargs = {"model": self._connect_model_text()}
        self._wait_prompt_label.setText(self._power_off_prompt_text())
        self._sync_wait_band_page()
        self._update_keep_data_warning()
        self._update_paperclip_hint()

    def on_try_anyway(self, cb) -> None:
        self._try_anyway_callback = cb

    def _on_keep_data_hint_link(self, link: str) -> None:
        if link == "try-anyway":
            cb = getattr(self, "_try_anyway_callback", None)
            if callable(cb):
                cb()

    def _microsd_note_html(self) -> str:
        """The microSD reassurance with its own Got it link at the end."""
        from html import escape

        return (
            f"{escape(tr('keep_data_microsd_reassurance'))} "
            f"{_hint_text_link('got-it', tr('hint_got_it'))}"
        )

    def _fit_paperclip_hint_height(self) -> None:
        """Reserve the paperclip tip's height for the width it is drawn at."""
        host = getattr(self, "_paperclip_hint_host", None)
        if host is None or host.isHidden():
            return
        label = getattr(self, "_paperclip_hint", None)
        w = host.width() if host.width() > 0 else (label.width() if label and label.width() > 0 else INSTALL_TEXT_WIDTH)
        for lbl in (label, getattr(self, "_charger_hint_label", None), getattr(self, "_driver_hint_label", None), getattr(self, "_driver_reboot_hint_label", None)):
            if lbl is not None and hasattr(lbl, "set_measure_width") and w > 0:
                lbl.set_measure_width(w)
        wanted = host.heightForWidth(w)
        if host.minimumHeight() != wanted:
            host.setMinimumHeight(wanted)
            self._relayout_wait_card()
            self._notify_geometry_change()

    def _paperclip_hint_html(self, sentence: str) -> str:
        """The paperclip tip with its Got it link as the end of the sentence."""
        from html import escape

        return f"{escape(sentence)} {_hint_text_link('got-it', tr('hint_got_it'))}"

    def _paperclip_help_html(self) -> str:
        """The link that opens the paperclip tip on a power-off card."""
        return _hint_text_link("show-hint", self._paperclip_help_text())

    def _paperclip_help_text(self) -> str:
        """The link text to open the paperclip tip."""
        copy = getattr(self, "_wait_copy", "")
        logger.debug("Paperclip help text: _wait_copy=%s", copy)
        if copy == "connect":
            from ..config import device_label_for_model

            model = (self._model or "").strip()
            name = (
                device_label_for_model(model)
                if model
                else tr("device_fallback_word")
            )
            return tr("flash_not_connecting_help_fmt").format(model=name)
        return tr("flash_paperclip_help")

    def _paperclip_help_wanted(self) -> bool:
        """Whether the prompt line should end in the tip's How? link."""
        if getattr(self, "_stop_open", False):
            return False
        if getattr(self, "_rockbox_themes_prompt", False):
            return False
        if getattr(self, "_paperclip_revealed", False) and getattr(self, "_wait_copy", "") != "connect":
            # The link already opened the tip: offering it again beside the
            # open tip says the same thing twice. The connect card is the
            # exception: its "won't connect? try this" link is what names the
            # reset sequence for a player the app has not reached.
            return False
        return True

    def _prompt_band_html(self, sentence: str) -> str:
        """The prompt, with the tip's How? link as a separate line below the sentence."""
        from html import escape

        text = escape(sentence)
        if self._paperclip_help_wanted():
            text = f"{text}<br>{self._paperclip_help_html()}"
        return text

    def _on_paperclip_help(self, link: str = "show-hint") -> None:
        """Open the paperclip tip: the diagram is shown when it is asked for.

        The link passes its own ``show-hint`` target; a direct call (the
        card's own tests, or any other entry) means the same thing.
        """
        if link and link != "show-hint":
            return
        # Asking for the tip outranks a dismissal remembered from an earlier
        # install. Without this the click set the reveal flag and the saved
        # "Got it." turned it straight back off, so the link did nothing and
        # then hid itself because the tip counted as already open.
        if _hint_dismissed("paperclip"):
            _set_hint_dismissed("paperclip", False)
        self._paperclip_revealed = True
        self._update_paperclip_hint()
        # The How? link has done its job: the prompt keeps its own words.
        self._sync_wait_band_page()
        self._notify_geometry_change()

    def _on_paperclip_got_it(self, _link: str = "") -> None:
        """Put the paperclip tip away, but keep showing it."""
        _set_hint_dismissed("paperclip")
        self._paperclip_revealed = False
        self._update_paperclip_hint()
        self._sync_wait_band_page()
        self._relayout_for_text()

    def _on_microsd_got_it(self, _link: str = "") -> None:
        """Put the microSD reassurance away and remember that it was read."""
        _set_hint_dismissed("microsd")
        self._update_keep_data_warning()
        self._relayout_for_text()

    def _set_yes_no_glyphs(self, yes_no: bool) -> None:
        """Swap the wait card's chips between Next/Cancel and Yes/No glyphs."""
        if yes_no:
            from ..i18n import current_language

            # Where a circle means yes (Japan), the yes chip is a ring and the
            # no chip is an X; elsewhere the yes chip is a tick.
            yes_symbol = "circle" if str(current_language()).startswith("ja") else "check"
            self._wait_continue_btn.set_symbol(yes_symbol)
            self._wait_cancel_btn.set_symbol("cancel")
            labels = (tr("btn_yes"), tr("btn_no"))
        else:
            self._wait_continue_btn.set_symbol("next")
            self._wait_cancel_btn.set_symbol("cancel")
            labels = (tr("btn_next"), tr("btn_cancel"))
        # Tooltips and accessible names follow the glyphs, so the hover text
        # reads Yes/No on a question rather than the stale Next/Back labels.
        for btn, label in zip((self._wait_continue_btn, self._wait_cancel_btn), labels):
            btn.setToolTip(label)
            btn.setAccessibleName(label)

    def _show_keep_data_choice(self, active: bool) -> None:
        """Show the firmware list in the band's slot, level with the chips."""
        choice = getattr(self, "_keep_data_choice", None)
        if choice is None:
            return
        choice.setVisible(bool(active))
        holder = getattr(self, "_keep_data_choice_holder", None)
        if holder is not None:
            holder.setVisible(bool(active))

    def _show_wait_question(self, text: str) -> None:
        """Ask a one-line question in the band's slot, in bold.

        The question replaces the progress band while the card is only asking
        something: the X and the tick then sit on the row of the words they
        answer, instead of under a bar that is merely showing 0%.
        """
        label = getattr(self, "_wait_question_label", None)
        if label is None:
            return
        label.setText(str(text or ""))
        self._sync_wait_band_page()

    def _style_wait_band_line(self, label, *, pixel: int, weight: QFont.Weight) -> None:
        """A question is the bold 14; the prompt leads the card at 13 semibold.

        The prompt has no software title above it any more, so it is the card's
        headline: it takes the title's rank instead of reading as body copy.
        """
        key = (pixel, weight)
        if getattr(self, "_wait_band_style", None) == key:
            return
        self._wait_band_style = key
        font = label.font()
        font.setPixelSize(pixel)
        font.setWeight(weight)
        label.setFont(font)

    def _sync_wait_band_page(self) -> None:
        """The band's slot belongs to the card's own line while it is asking.

        Every path that moves the card on sets a progress value again, so the
        band comes back by itself once the question has been answered or
        abandoned; a question must never be left standing over a live run.

        A prompt-only stage — power the player off, bring it back after a
        dropped USB — has no percentage to report, so a bar frozen at 0% there
        reports nothing. Its sentence takes the band's slot instead, which puts
        the words of the action on the same row as the X and the tick that
        answer it, and the copy line under the band stops repeating it.
        """
        stack = getattr(self, "_wait_head_stack", None)
        if stack is None:
            return
        question = getattr(self, "_wait_question_label", None)
        prompt = getattr(self, "_wait_prompt_label", None)
        heading = getattr(self, "_wait_question_heading", None)
        title = getattr(self, "_wait_pkg_label", None)
        asking = bool(
            getattr(self, "_keep_data_confirm_active", False)
            or getattr(self, "_keep_data_erase_confirm_active", False)
            or getattr(self, "_format_all_confirm_active", False)
        )
        prompt_line = ""
        if (
            not asking
            and getattr(self, "_prompt_only", False)
            and not getattr(self, "_stop_open", False)
            and prompt is not None
        ):
            prompt_line = prompt.text().strip()
        band_text = question.text().strip() if asking and question is not None else prompt_line
        if not band_text:
            band_text = ""
        prompt_band = bool(band_text) and not asking
        if question is not None and band_text:
            if asking:
                question.setTextFormat(Qt.TextFormat.PlainText)
                if question.text() != band_text:
                    question.setText(band_text)
            else:
                question.setTextFormat(Qt.TextFormat.RichText)
                wanted_text = self._prompt_band_html(band_text)
                if question.text() != wanted_text:
                    question.setText(wanted_text)
            self._style_wait_band_line(
                question,
                pixel=_QUESTION_PX if asking else _TITLE_PX,
                weight=QFont.Weight.Bold if asking else QFont.Weight.DemiBold,
            )
        choice = getattr(self, "_keep_data_choice", None)
        holder = getattr(self, "_keep_data_choice_holder", None)
        # The list needs the band's slot, because that is the row the chips sit
        # on. The question then takes the software title's row, so the card
        # still reads as one question and its answers rather than two headings.
        dropdown = bool(choice is not None and holder is not None and not choice.isHidden())
        if dropdown:
            wanted = holder
        elif band_text:
            wanted = question
        else:
            wanted = self._wait_progress_bar
        if stack.currentWidget() is not wanted:
            stack.setCurrentWidget(wanted)
        question_visible = wanted is question
        if question is not None and question.isVisible() != question_visible:
            question.setVisible(question_visible)
        if heading is not None and title is not None:
            heading_text = band_text if dropdown else ""
            if heading.text() != heading_text:
                heading.setText(heading_text)
            heading.setVisible(bool(heading_text))
            # The head leads with the line the user has to act on. A prompt-only
            # stage has no title: "power off and disconnect" is about the player
            # in hand, and the run's software name costs a row the prompt needs.
            # A question is about the player in hand too, and it already names
            # the software, so the "<software> <version> for <model>" caption
            # would only repeat version and model underneath it.
            title.setVisible(not (asking or dropdown or prompt_band))
        # The band already carries the sentence, so the line under it would
        # repeat it. While the bar is reporting a real run, that line is the
        # card's status copy and stays.
        if prompt is not None:
            prompt.setVisible(not (band_text or dropdown))
        # A single yes/no question keeps its two answers beside its words: the
        # chips slide in to the question and back out when it is answered. The
        # dropdown screen has no chips to move — they stay on the row's edge,
        # level with the list they confirm — so the pair is left alone there.
        head = getattr(self, "_wait_head", None)
        if head is not None:
            inline = bool(band_text and question is not None and not dropdown)
            head.set_band_inline(
                inline,
                question,
                line_width=self._band_line_width() if inline else None,
            )

    def keep_data_confirm_choice(self) -> str:
        return self._keep_data_choice.currentText() if hasattr(self, "_keep_data_choice") else ""

    def clear_keep_data_confirmation(self) -> None:
        self._keep_data_confirm_active = False
        self._keep_data_confirm_no_cb = None
        self._keep_data_erase_confirm_active = False
        self._keep_data_cancel_leaves = False
        self._format_all_confirm_active = False
        self._keep_data_force_hidden = False
        self._set_yes_no_glyphs(False)
        self._show_keep_data_choice(False)
        self._show_wait_question("")
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setVisible(True)
        if hasattr(self, "_keep_data_hint"):
            self._keep_data_hint.setVisible(False)
        if hasattr(self, "_keep_data_microsd_note"):
            self._keep_data_microsd_note.setVisible(False)
        self._sync_keep_data_visibility()
        self._update_paperclip_hint()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_gui_handoff_prompt(self, hint: str) -> None:
        """Power-off line plus a small download-mode hint. Continue does not flash."""
        self.show_power_off_prompt()
        # The user drives SP Flash Tool's own GUI here, so this run's scatter
        # (and therefore the keep-data option) is not ours to set.
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_cb.setVisible(False)
        self._keep_data_force_hidden = True
        self._keep_data_cb.setVisible(False)
        if hasattr(self, "_keep_data_warning"):
            self._keep_data_warning.setVisible(True)
        if hasattr(self, "_keep_data_help_link"):
            self._keep_data_help_link.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setText(hint or "")
            self._wait_mode_hint.setVisible(False)

    def keep_data_checked(self) -> bool:
        """Whether the user asked this run to leave their user data untouched."""
        if not getattr(self, "_has_userdata", True):
            return False
        if getattr(self, "_keep_data_locked", False):
            return bool(getattr(self, "_keep_data_locked_value", False))
        if getattr(self, "_format_all_required", False) or not getattr(
            self, "_keep_data_allowed", True
        ):
            return False
        cb = getattr(self, "_keep_data_cb", None)
        return bool(cb is not None and cb.isChecked())

    def leave_power_off_prompt(self) -> None:
        self._prompt_only = False
        self._permission_prompt = False
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_cb.setVisible(False)
        self.clear_keep_data_confirmation()
        self._update_keep_data_warning()
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(True)
            self._wait_continue_btn.setEnabled(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        # The flasher tool is starting up in the background and not yet searching.
        # Show "Getting ready..." until MTKclient or SP Flash Tool actively searches for the device.
        self._arm_connect_prompt_delay()
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

    def set_power_off_only(self, enabled: bool) -> None:
        """Ask only for a power-off, for a player that was just turned on.

        The install-first path uses this after an MTKclient base run: the
        preceding card has already had the player disconnected and powered on,
        so the next prep must not repeat the disconnect instruction.
        """
        self._power_off_only = bool(enabled)
        if getattr(self, "_wait_copy", "") == "power_off" and hasattr(
            self, "_wait_prompt_label"
        ):
            self._wait_prompt_label.setText(self._power_off_prompt_text())

    def set_bundled_run(self, enabled: bool) -> None:
        """Record that this run installs the firmware bundled next to the app."""
        self._bundled_run = bool(enabled)
        self.set_portable_run(bool(enabled))

    def set_portable_run(self, enabled: bool) -> None:
        """Mark this as a portable run (nothing is stored on this computer)."""
        self._portable_run = bool(enabled)
        if hasattr(self, "_appeal_dont"):
            self._appeal_dont.setText(self._dont_ask_html())
            self._appeal_dont.setToolTip(
                tr("portable_dont_ask_hint") if self._portable_run else ""
            )

    def show_install_app(self, visible: bool, label: str = "") -> None:
        """Show the completion card's Install This App / Keep App button."""
        button = getattr(self, "_install_app_btn", None)
        if button is None:
            return
        if label:
            button.setText(label)
            button.setToolTip(label)
        button.setVisible(bool(visible))

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
        from .dark import theme_text_color

        colour = theme_text_color().name()
        for idx, (_key, label) in enumerate(self._guide_texts, start=1):
            if idx == self._guide_step:
                label.setStyleSheet(f"color: {colour}; font-weight: 600;")
            else:
                label.setStyleSheet("")

    def set_model(self, model):
        self._model = (model or "").strip() or ""
        if hasattr(self, "_wait_prompt_label") and getattr(self, "_wait_copy", "") == "power_off":
            self._wait_prompt_label.setText(self._power_off_prompt_text())
            self._sync_wait_band_page()
        if hasattr(self, "_prep_pkg_label"):
            self._render_package_name()
        self._update_paperclip_hint()
        self._update_keep_data_warning()

    def set_platform(self, platform):
        self._platform = (platform or "").strip() or ""
        self._update_paperclip_hint()

    def _refresh_copy_theme(self):
        """Re-render copy whose markup carries a colour from the host palette.

        Rich-text labels (the completion letter, the keep-data hints and the
        in-app links) cannot use a QSS palette role, so their HTML is rebuilt
        from the live palette whenever the system appearance changes. Called
        from ``refresh_theme`` below.
        """
        self._apply_guide_highlight()
        if getattr(self, "_keep_data_explainer", "") and hasattr(self, "_keep_data_hint"):
            self._keep_data_hint.setText(_styled_prompt_links(self._keep_data_explainer))
        if hasattr(self, "_keep_data_microsd_note"):
            self._keep_data_microsd_note.setText(self._microsd_note_html())
        if hasattr(self, "_charger_hint_label"):
            self._charger_hint_label.setText(tr("flash_hint_wall_charger"))
        if hasattr(self, "_driver_hint_label"):
            import re
            clean_driver = re.sub(r"<[^>]+>", "", tr("flash_hint_install_drivers")).strip()
            self._driver_hint_label.setText(clean_driver)
        if hasattr(self, "_driver_hint_btn"):
            self._driver_hint_btn.setText(tr("settings_download_drivers_btn"))
        if hasattr(self, "_driver_reboot_hint_label"):
            self._driver_reboot_hint_label.setText(tr("flash_hint_windows_reboot_driver"))
        self._update_paperclip_hint()
        self._update_keep_data_warning()
        if hasattr(self, "_post_install_hint"):
            self._post_install_hint.setText(self._post_install_hint_html())
        if hasattr(self, "_completion_guide_hint"):
            self._completion_guide_hint.setText(self._completion_guide_html())
        if hasattr(self, "_appeal_dont"):
            self._appeal_dont.setText(self._dont_ask_html())
            self._appeal_dont.setToolTip(
                tr("portable_dont_ask_hint") if self._portable_storage_blocked() else ""
            )
        if hasattr(self, "_theme_recover_link"):
            self._theme_recover_link.setText(
                _themed_link("recover", tr("rockbox_recover_troubleshoot"))
            )

    def _dont_ask_html(self) -> str:
        if self._portable_storage_blocked():
            # Dimmed, not removed: the coffee and Not now buttons stay. The
            # setting cannot be kept because a portable run stores nothing.
            from html import escape

            from .dark import theme_placeholder_text_color

            muted = theme_placeholder_text_color().name()
            color = f"color:{muted}; "
            return (
                f'<a href="portable-dont-ask" style="{color}text-decoration:none;">'
                f'{escape(tr("donate_dont_ask_link"))}</a>'
            )
        return _themed_link("dont-ask", tr("donate_dont_ask_link"))

    def set_post_install_urls(self, post_install: str = "", post_install_2: str = "") -> None:
        """Set the firmware's post-install steps URLs for the completion screen.

        ``post_install`` is for users without donations: the done mark becomes a
        globe that opens it. ``post_install_2`` is for donations-shown users: View
        Guide and Buy Us A Coffee open it.
        """
        from .. import fx

        self._post_install_url = str(post_install or "").strip()
        self._post_install_url_2_source = str(post_install_2 or "").strip()
        # The second URL is the donation page; the Russian market is sent to
        # Boosty instead.
        self._post_install_url_2 = (
            "" if fx.donation_route() else self._post_install_url_2_source
        )
        self._apply_post_install_state()

    def _post_install_hint_html(self) -> str:
        """The completion note, with the globe glyph inline in its sentence."""
        globe = get_symbol_data_uri("globe", 12, match_accent=False)
        globe_html = (
            f'<img src="{globe}" width="12" height="12" '
            'style="vertical-align: -1px;">'
        )
        link = _themed_link("post-install", tr("post_install_find_out_more"))
        return tr("post_install_explain").format(
            globe=globe_html, find_out_more=link
        )

    def _completion_guide_html(self) -> str:
        """The extra-steps sentence shown above the View Guide button."""
        from html import escape

        return escape(tr("post_install_explain_completion"))

    def _apply_post_install_state(self) -> None:
        donations_shown = bool(getattr(self, "_donations_shown", False))
        post_install_2 = getattr(self, "_post_install_url_2", "")
        is_complete = getattr(self, "_complete", False)
        if hasattr(self, "_done_mark"):
            globe = self._has_post_install() and not donations_shown
            self._done_mark.set_symbol("www" if globe else "complete")
            self._done_mark.setToolTip(tr("post_install_globe_tooltip") if globe else "")
        if hasattr(self, "_post_install_hint"):
            # The compact one-line card is no longer a completion render: the
            # letter carries the guide for both donation states.
            self._post_install_hint.setVisible(False)
        if hasattr(self, "_view_guide_btn"):
            self._view_guide_btn.setText(tr("post_install_view_guide_btn"))
            self._view_guide_btn.setVisible(self._has_post_install() and is_complete)
        from .. import fx

        route = fx.donation_route()
        if hasattr(self, "_coffee_btn"):
            if route == "boosty":
                self._coffee_btn.setText(tr("donate_boosty"))
            elif post_install_2 and self._has_post_install():
                self._coffee_btn.setText(tr("donate_completion_coffee_guide"))
            else:
                self._coffee_btn.setText(tr("donate_completion_coffee"))
        if hasattr(self, "_bmc_btn"):
            self._bmc_btn.setText(tr("donate_buy_me_a_coffee"))
            # Boosty is the only method offered in the Russian market, so the
            # second coffee page is hidden rather than left as a dead end; and
            # a letter without the ask has hidden the whole row already.
            self._bmc_btn.setVisible(
                bool(getattr(self, "_donation_row_visible", True)) and not route
            )
        self._set_donation_row_visible(bool(getattr(self, "_donation_row_visible", True)))
        from .. import donation_order

        donation_order.place(
            self._appeal_actions,
            {"kofi": self._coffee_btn, "bmc": self._bmc_btn},
            donation_order.completion_coffee_order(),
        )
        self._apply_start_help_state()

    def _set_donation_row_visible(self, shown: bool) -> None:
        """Show or retire the ask as one block.

        A post-install completion keeps the letter when donations are off, so
        the coffee buttons and the opt-out link leave together: an empty strip
        under the signature would read as a missing picture, not as a choice.
        """
        from .. import fx

        self._donation_row_visible = bool(shown)
        for name in ("_coffee_btn", "_not_now_btn", "_appeal_dont"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(bool(shown))
        if hasattr(self, "_bmc_btn"):
            self._bmc_btn.setVisible(bool(shown) and not fx.donation_route())

    def _has_post_install(self) -> bool:
        """True when this software still has steps after the install card."""
        if getattr(self, "_post_install_guide_suppressed", False):
            return False
        return bool(
            getattr(self, "_post_install_url", "")
            or getattr(self, "_post_install_url_2", "")
        )

    def _start_help_text(self) -> str:
        """The start-up advice, naming the player this install ran on.

        The two routes back sit inside the sentence itself — underlined links
        sharing the explanation's paragraph, not buttons underneath it. A
        translation that words the sentence without repeating one of the
        action names still gets that route as an underlined tail, so no
        language loses the action.
        """
        from html import escape

        from ..config import device_label_for_model

        model = (getattr(self, "_model", "") or "").strip()
        label = device_label_for_model(model) if model else tr("device_fallback_word")
        body = escape(tr("completion_not_starting_body").format(model=label), quote=False)
        missing: list[tuple[str, str]] = []
        actions = (
            ("install-original", tr("completion_install_original")),
            ("other-software", tr("completion_other_software")),
        )
        for href, action in actions:
            action_html = escape(action, quote=False)
            pattern = re.compile(re.escape(action_html), re.IGNORECASE)
            if pattern.search(body):
                body = pattern.sub(
                    lambda match, href=href: _themed_link(
                        href, match.group(0), underline=True
                    ),
                    body,
                    count=1,
                )
            else:
                missing.append((href, action_html))
        if missing:
            tail = " · ".join(
                _themed_link(href, action, underline=True)
                for href, action in missing
            )
            body = f"{body} {tail}"
        return body

    def _start_help_hint_text(self) -> str:
        """Shorter "What if my {model} doesn't start up?" link text."""
        model = (getattr(self, "_model", "") or "").strip()
        if model:
            from ..config import device_label_for_model

            label = device_label_for_model(model)
            try:
                return tr("completion_not_starting_hint_fmt").format(model=label)
            except Exception:
                pass
        return tr("completion_not_starting_hint")

    def _render_start_help(self) -> None:
        """Re-write the start-up advice for this language and install."""
        from html import escape

        if hasattr(self, "_start_help_hint"):
            self._start_help_hint.setText(
                _themed_link(
                    "start-help",
                    escape(self._start_help_hint_text(), quote=False),
                    underline=True,
                )
            )
        if hasattr(self, "_start_help_advice"):
            self._start_help_advice.setText(self._start_help_text())

    def toggle_start_help(self, expanded=None) -> None:
        """Open or close the "doesn't start up" advice under its hint.

        Two screens carry that hint — the completion letter and the simple
        card without the ask — and each keeps its own advice label, so both
        follow the one toggle.
        """
        targets = [
            widget
            for widget in (
                getattr(self, "_start_help_body", None),
                getattr(self, "_completion_advice_label", None),
            )
            if widget is not None
        ]
        if not targets:
            return
        show = (
            not any(widget.isVisible() for widget in targets)
            if expanded is None
            else bool(expanded)
        )
        for widget in targets:
            widget.setVisible(show)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def _on_start_help_link(self, href: str) -> None:
        if href == "start-help":
            self.toggle_start_help()
        elif href == "install-original":
            self.install_original_requested.emit()
        elif href == "other-software":
            self.browse_software_requested.emit()

    def _apply_start_help_state(self) -> None:
        """The hint belongs to a finished install, never to a live run."""
        complete = bool(getattr(self, "_complete", False)) and not bool(
            getattr(self, "_failed", False)
        )
        hint = getattr(self, "_start_help_hint", None)
        if hint is not None:
            hint.setVisible(False)
        power_on = getattr(self, "_appeal_power_on", None)
        if power_on is not None:
            power_on.setVisible(
                complete and not bool(getattr(self, "_cleanup_mode", False))
            )
        body = getattr(self, "_start_help_body", None)
        if body is not None and not complete:
            body.setVisible(False)

    # -- post-install guide -------------------------------------------------

    def _post_install_open_url(self, url: str) -> str:
        """Where a guide link should open in a browser.

        A Markdown guide opens on the site's own reader page, which renders the
        same file with the site's styling; anything else keeps its own page.
        """
        from .. import docs_source

        page = docs_source.post_install_page_url(
            url,
            repo=docs_source.repo_from_url(url),
            software=getattr(self, "_package_name", "") or "",
            model=getattr(self, "_model", "") or "",
        )
        # A post_install that is a repository or release page (not a Markdown
        # file) still belongs on the site's parser page for that project.
        return page or docs_source.site_page_for_link(url) or url

    def _open_post_install_guide(self) -> None:
        """Globe: the firmware's setup steps, in the browser."""
        from ..browser import open_browser, _with_donation_preference

        url = getattr(self, "_post_install_url", "") or getattr(self, "_post_install_url_2", "")
        if url:
            open_browser(_with_donation_preference(self._post_install_open_url(url)))

    def _open_view_guide(self) -> None:
        """View Guide: opens post_install link."""
        from ..browser import open_browser, _with_donation_preference

        url = getattr(self, "_post_install_url", "") or getattr(self, "_post_install_url_2", "")
        if url:
            open_browser(_with_donation_preference(self._post_install_open_url(url)))

    def _open_coffee_page(self) -> None:
        from .. import fx
        from ..browser import interstitial_url, open_browser
        from ..config import DONATION_LINKS

        route = fx.donation_route()
        if route == "boosty":
            open_browser(DONATION_LINKS["boosty"])
            self._on_not_now()
            return
        post_install_2 = getattr(self, "_post_install_url_2", "")
        if post_install_2 and self._has_post_install():
            open_browser(interstitial_url(post_install_2, thank=True))
        else:
            open_browser(DONATION_LINKS["kofi"])
        self._on_not_now()

    def _open_bmc_page(self) -> None:
        """Buy Me a Coffee: always the project's own page, guide or not."""
        from ..browser import open_browser
        from ..config import DONATION_LINKS

        open_browser(DONATION_LINKS["bmc"])
        self._on_not_now()

    def _render_appeal_copy(self) -> None:
        """(Re)write the appeal's words for this language, install and model.

        Shared by ``show_completion_appeal`` and ``retranslate``, so switching
        language after an install still names the software that just finished
        instead of falling back to the generic project pitch.
        """
        model = (getattr(self, "_model", "") or "").strip() or tr("device_fallback_word")
        software = _completion_software_name(
            (getattr(self, "_package_name", "") or "").strip(),
            (getattr(self, "_model", "") or "").strip(),
        ) or tr("flash_current_pkg")
        from html import escape

        from ..config import install_power_on_steps

        steps = install_power_on_steps(model)
        has_guide = self._has_post_install()
        if hasattr(self, "_intro"):
            self._intro.retranslate(software=software, model=model)
            self._intro.subtitle.setVisible(not has_guide)
            _apply_card_text(self._intro.headline)
            _apply_card_text(self._intro.subtitle)
        start_help_link = _themed_link(
            "start-help",
            escape(self._start_help_hint_text(), quote=False),
            underline=True,
        )
        if hasattr(self, "_appeal_power_on"):
            # How to turn the player on after the write. MTKClient leaves it
            # sitting in download mode, so every MTKClient completion says
            # this, guide or not. SP Flash Tool starts the player itself, so
            # it says nothing rather than explaining a step nobody needs.
            self._appeal_power_on.setTextFormat(Qt.TextFormat.RichText)
            self._appeal_power_on.setOpenExternalLinks(False)
            try:
                self._appeal_power_on.linkActivated.disconnect()
            except Exception:
                pass
            self._appeal_power_on.linkActivated.connect(self._on_start_help_link)
            if self._is_mtk_active():
                clean_steps = escape(steps.rstrip(), quote=False)
                self._appeal_power_on.setText(f"{clean_steps} {start_help_link}")
            else:
                self._appeal_power_on.setText(start_help_link)
            self._appeal_power_on.setVisible(True)
            _apply_card_text(self._appeal_power_on)
        if hasattr(self, "_start_help_hint"):
            self._start_help_hint.setVisible(False)
        if hasattr(self, "_completion_guide_hint"):
            self._completion_guide_hint.setText(self._completion_guide_html())
            self._completion_guide_hint.setVisible(has_guide)
            _apply_card_text(self._completion_guide_hint)
        if self._has_post_install() and not getattr(self, "_appeal_themes", False):
            # The completion letter for an install with a guide reads as the
            # reference note: one fixed paragraph, not the rotating pitch used
            # on the plain completion and support surfaces.
            self._active_appeal_key = "donate_completion_body_small_hours"
        elif getattr(self, "_active_appeal_key", None) not in COMPLETION_BODY_KEYS:
            import random
            self._active_appeal_key = random.choice(COMPLETION_BODY_KEYS)
        appeal_text = tr(self._active_appeal_key)
        if hasattr(self, "_appeal_body"):
            self._appeal_body.setText(appeal_text)
            # One letter layout: headline, extra-steps sentence and its guide
            # button, message, signature, then the donation buttons. The guide
            # never moves below the message, where it read as an afterthought.
            self._appeal_body.setVisible(True)
            _apply_card_text(self._appeal_body)
        if hasattr(self, "_appeal_signature"):
            self._appeal_signature.setText(tr("donate_completion_signature"))
            _apply_card_text(self._appeal_signature)
        self._render_start_help()
        self._apply_post_install_state()
        if hasattr(self, "_not_now_btn"):
            self._not_now_btn.setText(tr("donate_not_now"))
        if hasattr(self, "_appeal_dont"):
            self._appeal_dont.setText(self._dont_ask_html())

    def _render_completion_note(self, post_install_once: bool = False) -> None:
        """The completion card for readers whose donations appeal is off.

        The simple view, with no ask anywhere on it: what finished — or, when
        the firmware still needs its guide, only what is left to do — the
        guide that finishes it, how to start the player when the method needs
        it, and the start-up advice behind its own hint.
        """
        from html import escape

        model = self._complete_note_model or tr("device_fallback_word")
        software = _completion_software_name(
            self._complete_note_software, self._complete_note_model
        ) or tr("flash_current_pkg")
        from ..config import install_power_on_steps

        # Clear hints
        while self._completion_hint_layout.count():
            item = self._completion_hint_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._completion_advice_label = None
        # The card's lines are the whole message, not something to unfold.
        self._completion_collapsible.toggle_button.setVisible(False)
        self._completion_collapsible.toggle(True)

        if self._complete_note_themes and not self.themes.placement_failed:
            if getattr(self.themes, "is_standalone", False):
                count = getattr(self.themes, "custom_theme_count", 1)
                if count > 1:
                    installed = tr("donate_completion_themes_standalone_plural_subtitle").format(count=count, model=model)
                else:
                    installed = tr("donate_completion_theme_standalone_subtitle").format(model=model)
            else:
                installed = tr("donate_completion_themes_subtitle").format(model=model)
            self._completion_title_label.setText(f"<b>{escape(installed)}</b>")
            _apply_card_text(self._completion_title_label)
            self._complete_note.setText(installed)
            self._complete_note.setVisible(False)
            return

        if not software or software.startswith(("Get ready to install", "Installing software", "Get your")):
            installed = tr("donate_completion_subtitle_the_software").format(model=model)
        else:
            installed = tr("donate_completion_subtitle").format(
                software=software, model=model
            )
        if self.themes.placement_failed:
            # The themes stage gave up: the note's job is the try-again copy,
            # which is why the reader is on this screen at all.
            installed = tr("themes_placement_failed_note")

        # A firmware whose guide still has to run is not installed in the sense
        # the sentence claims, so the card leaves the sentence out and says
        # what is left to do instead.
        has_guide = self._has_post_install() and not self.themes.placement_failed
        if has_guide:
            heading = f"<b>{escape(tr('completion_one_more_thing'))}</b>"
        else:
            # Nothing is left to do: the card opens with what finished.
            heading = f"<b>{escape(installed)}</b>"
        self._completion_title_label.setText(heading)
        _apply_card_text(self._completion_title_label)
        self._complete_note.setText(installed)
        self._complete_note.setVisible(bool(self.themes.placement_failed))

        if has_guide:
            lbl = QLabel(self._post_install_hint_html())
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setWordWrap(True)
            lbl.setProperty("cssClass", "dimmed")
            lbl.setOpenExternalLinks(False)
            lbl.linkActivated.connect(lambda _href: self._open_post_install_guide())
            _apply_card_text(lbl)
            self._completion_hint_layout.addWidget(lbl)

        start_help_link = _themed_link(
            "start-help",
            escape(self._start_help_hint_text(), quote=False),
            underline=True,
        )
        if self._is_mtk_active():
            # MTKClient leaves the player in download mode; SP Flash Tool
            # starts it, so only MTKClient completions explain the button.
            clean_steps = escape(install_power_on_steps(model).rstrip(), quote=False)
            power_on = QLabel(f"{clean_steps} {start_help_link}")
            power_on.setWordWrap(True)
            power_on.setTextFormat(Qt.TextFormat.RichText)
            power_on.setOpenExternalLinks(False)
            power_on.linkActivated.connect(self._on_start_help_link)
            _apply_card_text(power_on)
            self._completion_hint_layout.addWidget(power_on)
        else:
            hint_lbl = QLabel(start_help_link)
            hint_lbl.setTextFormat(Qt.TextFormat.RichText)
            hint_lbl.setOpenExternalLinks(False)
            hint_lbl.linkActivated.connect(self._on_start_help_link)
            _apply_card_text(hint_lbl)
            self._completion_hint_layout.addWidget(hint_lbl)

        # The start-up advice belongs under its own hint, closed until asked
        # for, the same way the letter carries it.
        advice = QLabel(self._start_help_text())
        advice.setWordWrap(True)
        advice.setTextFormat(Qt.TextFormat.RichText)
        advice.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        advice.setOpenExternalLinks(False)
        advice.setProperty("cssClass", "dimmed")
        advice.linkActivated.connect(self._on_start_help_link)
        advice.setVisible(False)
        _apply_card_text(advice)
        self._completion_hint_layout.addWidget(advice)
        self._completion_advice_label = advice

    def show_completion_note(
        self,
        donations_enabled: bool,
        software: str = "",
        model: str = "",
        themes_installed: bool = False,
        post_install_once: bool = False,
        post_install_suppressed: bool = False,
    ) -> None:
        """Finish the screen, with the ask only where donations are enabled.

        With donations on and a guide to run, the letter carries the finish:
        what is left, the guide, and the coffee note signed by the maintainer.
        With the ask off, the simple completion card is the whole screen — no
        donation copy, no signature, no empty button row.
        """
        logger.info("show_completion_note called, software=%s, post_install_once=%s", software, post_install_once)
        self._post_install_once = post_install_once
        self._post_install_guide_suppressed = bool(post_install_suppressed)
        self._complete_note_themes = bool(themes_installed)
        self._complete_note_software = str(software or "").strip()
        self._complete_note_model = str(model or "").strip()
        # The letter reads the same two values the donation path uses, so the
        # callers that name the finished install must set them here too.
        if self._complete_note_software:
            self._package_name = self._complete_note_software
        if self._complete_note_model:
            self._model = self._complete_note_model
        self._donations_shown = False
        self._completion_advice_label = None
        themes_failed = bool(self.themes.placement_failed)
        self._render_completion_note(post_install_once=post_install_once)
        if donations_enabled and hasattr(self, "_appeal") and not themes_failed:
            self._appeal_themes = bool(themes_installed)
            self._completion_note_container.setVisible(False)
            if hasattr(self, "_post_install_hint"):
                self._post_install_hint.setVisible(False)
            if hasattr(self, "_progress_card"):
                # The letter already says the install is done; the 100% card
                # above it would repeat the same news in a second shape.
                self._progress_card.setVisible(False)
            if hasattr(self, "_intro"):
                if getattr(self.themes, "is_standalone", False):
                    count = getattr(self.themes, "custom_theme_count", 1)
                    if count > 1:
                        from .support_appeal import THEME_STANDALONE_PLURAL_COMPLETION_KEYS
                        self._intro.set_keys(THEME_STANDALONE_PLURAL_COMPLETION_KEYS, count=count)
                    else:
                        self._intro.set_keys(THEME_STANDALONE_COMPLETION_KEYS)
                elif themes_installed:
                    self._intro.set_keys(THEMES_COMPLETION_KEYS)
                elif self._has_post_install():
                    self._intro.set_keys(ONE_MORE_THING_KEYS)
                else:
                    self._intro.set_keys(COMPLETION_KEYS)
            if self._has_post_install():
                self._active_appeal_key = "donate_completion_body_small_hours"
                self._set_donation_row_visible(False)
            else:
                import random
                self._active_appeal_key = random.choice(COMPLETION_BODY_KEYS)
                self._set_donation_row_visible(True)
            self._render_appeal_copy()
            self._appeal.setVisible(True)
        else:
            self._completion_note_container.setVisible(True)
            if not donations_enabled and hasattr(self, "_appeal"):
                # Without the ask the letter has nothing the simple card does
                # not say, so it leaves the screen entirely: no donation copy,
                # no signature, no empty button row.
                self._appeal.setVisible(False)
        # A suppressed guide changes the done mark (globe vs tick) and hides
        # View Guide; those were rendered before ``show_completion_note`` knew
        # this run's answer.
        self._apply_post_install_state()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def show_completion_appeal(
        self, visible: bool, callback=None, on_not_now=None, themes_installed: bool = False
    ) -> None:
        """Coffee note under a finished install. Hidden when donations are already off.

        The wording leads with what just happened — the software is installed
        on the player — and only then asks for a coffee. An install the reader
        just watched succeed is what makes the ask land as a thank-you rather
        than as an interruption.
        """
        self._appeal_callback = callback
        self._appeal_not_now = on_not_now
        self._appeal_themes = bool(themes_installed)
        self._donations_shown = bool(visible)
        if not hasattr(self, "_appeal"):
            return
        if hasattr(self, "_intro"):
            if getattr(self.themes, "is_standalone", False):
                count = getattr(self.themes, "custom_theme_count", 1)
                if count > 1:
                    from .support_appeal import THEME_STANDALONE_PLURAL_COMPLETION_KEYS
                    self._intro.set_keys(THEME_STANDALONE_PLURAL_COMPLETION_KEYS, count=count)
                else:
                    self._intro.set_keys(THEME_STANDALONE_COMPLETION_KEYS)
            elif themes_installed:
                self._intro.set_keys(THEMES_COMPLETION_KEYS)
            elif self._has_post_install():
                # The write is not the end of this install: the letter leads
                # with the steps the guide still has to walk the user through.
                self._intro.set_keys(ONE_MORE_THING_KEYS)
            else:
                self._intro.set_keys(COMPLETION_KEYS)
        import random
        self._active_appeal_key = random.choice(COMPLETION_BODY_KEYS)
        self._render_appeal_copy()
        self._set_donation_row_visible(bool(visible))
        if visible:
            self._completion_note_container.setVisible(False)
            if hasattr(self, "_post_install_hint"):
                self._post_install_hint.setVisible(False)
            if hasattr(self, "_progress_card"):
                self._progress_card.setVisible(False)
        self._appeal.setVisible(bool(visible))
        self._apply_post_install_state()
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def _on_not_now(self) -> None:
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()
        callback = getattr(self, "_appeal_not_now", None)
        if callable(callback):
            callback()

    def _portable_storage_blocked(self) -> bool:
        """True when this portable run cannot keep a preference at all.

        A run with no installed copy stores nothing the next launch could
        read, so preferences are refused. Once Updater CE is installed the
        run writes to that install's own settings store, and the choice is
        kept for the app the user actually runs.
        """
        if not getattr(self, "_portable_run", False):
            return False
        try:
            return not paths.installed_app_present()
        except Exception:
            return True

    def _on_appeal_dont_ask(self) -> None:
        """Turn donations off; the letter stays and its ask leaves."""
        if self._portable_storage_blocked():
            # A portable run cannot persist the choice, so the link explains
            # that instead of pretending to save it. Shown like the Type tip.
            from PySide6.QtGui import QToolTip

            label = getattr(self, "_appeal_dont", None)
            tip = tr("portable_dont_ask_hint")
            if label is not None:
                label.setToolTip(tip)
                QToolTip.showText(
                    label.mapToGlobal(label.rect().bottomLeft()),
                    tip,
                    label,
                    label.rect(),
                    12000,
                )
            return
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()
        callback = getattr(self, "_appeal_callback", None)
        if callable(callback):
            callback()

    def show_cleanup(self) -> None:
        """Show the portable run's cleanup on the install progress card.

        The same card, progress band and status line as a firmware install or
        the Rockbox themes stage: quitting is another task with visible work,
        not a window that vanishes.
        """
        self.dismiss_prompt_stop()
        self._cleanup_mode = True
        self._complete = False
        self._failed = False
        self.show_install_app(False)
        if hasattr(self, "_completion_note_container"):
            self._completion_note_container.setVisible(False)
        if hasattr(self, "_appeal"):
            self._appeal.setVisible(False)
        if hasattr(self, "_warning"):
            self._warning.setVisible(False)
        if hasattr(self, "_progress_card"):
            self._progress_card.setVisible(True)
        for attr in (
            "_cancel_btn",
            "_flash_retry_btn",
            "_done_mark",
            "_theme_back_btn",
            "_theme_step1_next_btn",
            "_theme_fallback_next_btn",
            "_theme_trouble_recover_btn",
            "_driver_action_row",
            "_post_install_hint",
            "_start_help_hint",
            "_start_help_body",
        ):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.setVisible(False)
        if hasattr(self, "_flash_pkg_label"):
            self._flash_pkg_label.setText(tr("portable_cleanup_heading"))
        if hasattr(self, "_step_label"):
            self._step_label.setText(tr("portable_cleanup_downloads"))
        if hasattr(self, "_eta_label"):
            self._eta_label.setText("")
        if hasattr(self, "_partition_progress_label"):
            self._partition_progress_label.setVisible(False)
        self._set_determinate(self._progress_bar, 0)
        self._switch_view(self._flashing_view)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def set_cleanup_progress(self, percent, text: str) -> None:
        """Advance the cleanup pass shown on the progress card."""
        if hasattr(self, "_progress_bar"):
            self._set_determinate(self._progress_bar, percent)
        if hasattr(self, "_step_label"):
            self._step_label.setText(text or "")

    def finish_cleanup(self) -> None:
        """Leave the cleanup card complete while the app closes."""
        if hasattr(self, "_progress_bar"):
            self._set_determinate(self._progress_bar, 100)
        if hasattr(self, "_step_label"):
            self._step_label.setText(tr("portable_cleanup_done"))

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
        # The completion heading's icon is the same release squircle; a
        # release without artwork keeps the generic placeholder beside it.
        if hasattr(self, "_intro"):
            self._intro.set_icon(source if not source.isNull() else None)

    def _generic_install_title(self) -> str:
        """Name the device a package is installing to when its title is unknown."""
        from ..config import device_label_for_model

        device = device_label_for_model(self._model) if self._model else tr("flash_player_fallback")
        return tr("flash_installing_software_on").format(model=device)

    def _display_package_name(self) -> str:
        """Card title with its leading software name in the current language.

        The name is stored as the English catalogue title, so a language change
        re-localises it here rather than keeping the wording from install start.
        A package with no detected release title names its target device instead.
        """
        name = (getattr(self, "_package_name", "") or "").strip()
        heading = _READY_HEADING.match(name)
        if heading is not None:
            # The heading claimed the install had not started yet, even on
            # a card halfway through the write. Name the software and its
            # device instead, which is true at every stage of the run.
            software = (heading.group("software") or heading.group("software_alt") or "").strip()
            if software.casefold() == "software":
                return self._generic_install_title()
            from ..catalog import format_install_card_title

            model = (heading.group("model") or getattr(self, "_model", "") or "").strip()
            name = format_install_card_title(software, "", model) or name
        lower = name.casefold()
        if (
            not name
            or name == "\u2014"
            or lower.startswith("installing software")
            or lower.endswith("ready to install software")
            or lower.endswith((".zip", ".rar", ".7z", ".tar", ".gz", ".bin", ".img", ".txt"))
        ):
            return self._generic_install_title()
        canonical = "Original Software"
        if name == canonical or name.startswith(canonical + " "):
            return tr("software_original") + name[len(canonical):]
        return name

    def _render_package_name(self) -> None:
        """Put the current display title on every install-view card."""
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

    def set_package_name(self, name):
        self._package_name = name or ""
        self.update_partition_progress("")
        self._render_package_name()
        self._relayout_for_text()

    def _connect_model_text(self):
        from ..config import device_label_for_model
        return device_label_for_model(self._model) if self._model else tr("device_fallback_word")

    def set_waiting_device(self):
        self._wait_copy = "connect"
        # Every connect prompt is the same card, whichever path reached it: the
        # sentence takes the band's line and the software name steps aside.
        # Leaving this to the callers made a reconnect from the retry page, or
        # a countdown that arrived before the tool's search line, put the old
        # title-above-the-prompt card back.
        self._prompt_only = True
        self._wait_status.set_status("idle")
        if hasattr(self, "_wait_pkg_label"):
            self._wait_pkg_label.setVisible(False)
        # The connect prompt is not the power-off prompt: no Keep Data tick,
        # and the paperclip advice belongs to power-off/recovery only.
        self._sync_keep_data_visibility()
        self._update_paperclip_hint()
        # Give the backend a moment to start its USB listener before asking
        # the reader to plug in. The stage owns this window: the first search
        # line re-arms it, and repeated lines in the same stage do not.
        #
        # MTKClient is held back until its own search line prints, not just for
        # the window: loading pyusb, the bundled libusb and the vendored fork
        # takes longer than any fixed countdown, and a player plugged in while
        # that is still running has left its preloader window before the
        # handshake can run. So "Please connect your <model>" waits for proof
        # that the engine is listening.
        getting_ready = self._connect_prompt_held()
        prompt = self._connect_prompt_text()
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(prompt)
        self._wait_banner.set_type("info")
        if getting_ready:
            self._wait_banner_key = "getting_ready"
            self._wait_banner.set_key("flash_getting_ready")
        else:
            self._wait_banner_key = "connect"
            self._wait_banner.set_key(
                "flash_connect_device_prompt", model=self._connect_model_text()
            )
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
        return not self.themes.transfer_running

    def show_finishing(self) -> None:
        """Hold the install card up while the backend closes its session.

        ``STEP_DONE`` means every partition was written, but the worker emits
        ``finished`` only after the backend has closed USB (MTKclient) or the
        SP Flash Tool process has exited. Showing the completion screen at
        that point blanked the window for the wait: completion with donations
        hides this card, and the appeal cannot appear until success routing
        runs. The card stays, reading that it is finishing up.
        """
        self._complete = False
        self._failed = False
        self._donations_shown = False
        self._step_key = "flash_finishing"
        if hasattr(self, "_completion_note_container"):
            self._completion_note_container.setVisible(False)
        if hasattr(self, "_appeal"):
            self._appeal.setVisible(False)
        if hasattr(self, "_progress_card"):
            self._progress_card.setVisible(True)
        if hasattr(self, "_warning"):
            # The session is still open, so "do not unplug" still applies.
            self._warning.setVisible(True)
        if hasattr(self, "_flash_banner"):
            self._flash_banner.set_type("info")
            self._flash_banner.set_key("flash_banner_flashing")
        if hasattr(self, "_step_label"):
            self._step_label.setText(tr("flash_finishing"))
        if hasattr(self, "_eta_label"):
            self._eta_label.setText("")
        if hasattr(self, "_progress_bar"):
            self._set_determinate(self._progress_bar, 100)
        if hasattr(self, "_partition_progress_label"):
            self._partition_progress_label.setVisible(False)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(False)
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(False)
        if hasattr(self, "_status_flow"):
            self._status_flow.refresh()
        self._switch_view(self._flashing_view)
        self.view_generation = getattr(self, "view_generation", 0) + 1
        self._notify_geometry_change()

    def set_device_done(self, donations_shown: bool | None = None):
        """Finish the install card. With donations shown, the donation message says it is complete.

        That message replaces the green check badge and the dismiss checkmark.
        ``None`` keeps the donations state the install already set, so a later
        finish call (e.g. the Rockbox themes stage) cannot bring the card back.
        """
        self.update_partition_progress("")
        self.dismiss_prompt_stop()
        self._complete = True
        self._failed = False
        # show_finishing() renamed the step; completion puts the last real
        # step back so the card never claims it is still finishing.
        self._step_key = "step_done"
        if hasattr(self, "_step_label"):
            self._step_label.setText(tr("step_done"))
        if donations_shown is not None:
            self._donations_shown = bool(donations_shown)
        donations_shown = getattr(self, "_donations_shown", False)
        self._warning.setVisible(False)
        # The donations appeal already says the install is complete, so the
        # progress card (100% and its check) is redundant and hidden.
        if hasattr(self, "_progress_card"):
            self._progress_card.setVisible(not donations_shown)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(True)
            self._cancel_btn.setEnabled(False)
            self._cancel_btn.set_symbol("back")
            self._cancel_btn.setToolTip("")
            self._cancel_btn.setAccessibleName("")
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(not donations_shown)
            self._done_mark.setEnabled(True)
        self._apply_post_install_state()
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
        if hasattr(self, "_flash_head") and hasattr(self._flash_head, "_sync_chips_pairing"):
            self._flash_head._sync_chips_pairing()
        source = getattr(self, "_release_source", None)
        if source is not None:
            self.set_release_icon(source, complete=not donations_shown)
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
        if (
            getattr(self, "_cleanup_stage", False)
            or (getattr(self, "themes", None) is not None and self.themes.active)
            or getattr(self, "_complete", False)
            or getattr(self, "_failed", False)
        ):
            return
        import time

        try:
            raw_val = float(percent or 0)
        except (TypeError, ValueError):
            raw_val = 0.0

        if raw_val > 100.0:
            self._write_exceeded_100 = True

        now = time.monotonic()
        if raw_val >= 100.0:
            if getattr(self, "_write_100_start_time", None) is None:
                self._write_100_start_time = now
                timer = getattr(self, "_write_100_timer", None)
                if timer is None:
                    from PySide6.QtCore import QTimer

                    timer = QTimer(self)
                    timer.setSingleShot(True)
                    timer.timeout.connect(self._on_write_100_timeout)
                    self._write_100_timer = timer
                if not timer.isActive():
                    timer.start(2000)
        else:
            if not getattr(self, "_write_exceeded_100", False):
                self._write_100_start_time = None
                timer = getattr(self, "_write_100_timer", None)
                if timer is not None and timer.isActive():
                    timer.stop()

        hide_percentage = (
            getattr(self, "_write_exceeded_100", False)
            or (
                getattr(self, "_write_100_start_time", None) is not None
                and (now - self._write_100_start_time >= 2.0)
            )
        )

        val = max(0.0, min(100.0, raw_val))
        if getattr(self, "_getting_ready", False) and val > 12.5:
            self.set_getting_ready(False)
        if (
            getattr(self, "_wait_copy", "") == "please_wait"
            and val <= 0
            and self._stack.currentWidget() is self._waiting_view
        ):
            self._set_indeterminate(self._wait_progress_bar)
            return
        if val > 0 and getattr(self, "_prompt_only", False):
            # The run is writing now, so the bar has something to report and the
            # card gets its software title back with it.
            self._prompt_only = False
            self._sync_wait_band_page()
        if val > 0:
            # A stage that reports again has answered its own question.
            self.dismiss_flash_question()
        if val > 0 and getattr(self, "_wait_copy", "") == "please_wait":
            self._wait_copy = "stage"
        self._set_determinate(self._progress_bar, val)
        if self._stack.currentWidget() is self._waiting_view and not self._prompt_only:
            self._set_determinate(self._wait_progress_bar, val)

        for bar in (self._progress_bar, getattr(self, "_wait_progress_bar", None)):
            if bar is not None:
                if hide_percentage:
                    bar.setTextVisible(False)
                    bar.setFormat("")
                else:
                    bar.setTextVisible(True)
                    if not getattr(bar, "is_getting_ready", lambda: False)():
                        bar.setFormat("%p%")

        if hide_percentage:
            part_lbl = getattr(self, "_partition_progress_label", None)
            if part_lbl is not None and part_lbl.isVisible():
                part_lbl.clear()
                part_lbl.setVisible(False)
                self._notify_geometry_change()

    def update_step(self, step_key):
        if (
            getattr(self, "_cleanup_stage", False)
            or (getattr(self, "themes", None) is not None and self.themes.active)
            or getattr(self, "_complete", False)
            or getattr(self, "_failed", False)
        ):
            return
        self._step_key = step_key
        if getattr(self, "_stop_open", False):
            self._apply_stop_hold()
            return
        if getattr(self, "_getting_ready", False):
            if step_key not in ("DETECT", "step_detect", "flash_getting_ready", "GETTING_READY"):
                self.set_getting_ready(False)
        if getattr(self, "_getting_ready", False):
            self._step_label.setText(tr("flash_getting_ready"))
        else:
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
        import time

        text = str(text or "").strip()
        if (
            not text
            or getattr(self, "_complete", False)
            or getattr(self, "_failed", False)
            or getattr(self, "_cleanup_stage", False)
            or getattr(self, "_prompt_only", False)
            or (hasattr(self, "themes") and self.themes.active)
            or getattr(self, "_write_exceeded_100", False)
            or (
                getattr(self, "_write_100_start_time", None) is not None
                and (time.monotonic() - self._write_100_start_time >= 2.0)
            )
        ):
            label.clear()
            label.setVisible(False)
            self._notify_geometry_change()
            return

        import re
        match = re.search(r"(\d+(?:\.\d+)?)%", text)
        if match:
            try:
                pct = float(match.group(1))
                if pct >= 100.0:
                    label.clear()
                    label.setVisible(False)
                    self._notify_geometry_change()
                    return
                text = text.replace(match.group(0), f"{min(100.0, pct):.1f}%")
            except ValueError:
                pass

        label.setText(text)
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
        is_final = any(kw in text.lower() for kw in ("complete", "error", "finish", "fail", "success"))
        if (is_final or not text or (elapsed >= self._ACTION_HOLD_MS and not getattr(self, "_action_pending_text", None))):
            self._action_apply_text(text)
            self._action_shown_at = time.monotonic()
            self._action_shown_text = text
            self._action_pending_text = None
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
        if (
            getattr(self, "themes", None) is not None and (self.themes.active or self._cleanup_stage)
            or getattr(self, "_complete", False)
            or getattr(self, "_failed", False)
        ):
            return
        self._action_label.setText(text)
        if text and not getattr(self, "_stop_open", False) and not getattr(self, "_failed", False) and not self.themes.transfer_running:
            if hasattr(self, "_step_label"):
                self._step_label.setText(text)
            flow = getattr(self, "_status_flow", None)
            if flow is not None:
                flow.refresh()
            self._notify_geometry_change()

    def update_time(self, elapsed, eta):
        if (
            getattr(self, "_cleanup_stage", False)
            or (getattr(self, "themes", None) is not None and self.themes.active)
            or getattr(self, "_complete", False)
            or getattr(self, "_failed", False)
        ):
            return
        self._elapsed_row.set_value(elapsed)
        self._eta_row.set_value(eta)
        self._refresh_eta_label(eta)
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.refresh()

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
        self._eta_label.setText(f",  {friendly}" if friendly else "")
        flow = getattr(self, "_status_flow", None)
        if flow is not None:
            flow.refresh()

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
        if hasattr(self, "_wait_prompt_label") and copy == "power_off":
            self._wait_prompt_label.setText(self._power_off_prompt_text())
        elif hasattr(self, "_wait_prompt_label") and copy == "please_wait_connecting":
            self._wait_prompt_label.setText(self._wait_connecting_prompt_text())
        elif hasattr(self, "_wait_prompt_label") and copy == "please_wait":
            self._wait_prompt_label.setText(tr("flash_please_wait"))
        elif hasattr(self, "_wait_prompt_label") and copy == "entering_password":
            self._wait_prompt_label.setText(tr("install_linux_entering_password"))
        elif hasattr(self, "_wait_prompt_label") and copy in ("connect", ""):
            self._wait_prompt_label.setText(self._connect_prompt_text())
        elif hasattr(self, "_wait_prompt_label") and copy == "stage" and self._step_key:
            self._wait_prompt_label.setText(tr(self._step_key))
        if hasattr(self, "_wait_mode_hint"):
            self._wait_mode_hint.setVisible(False)
        if hasattr(self, "_keep_data_cb"):
            self._keep_data_cb.setText(tr("flash_keep_data_option"))
            self._keep_data_cb.setToolTip(tr("flash_keep_data_tooltip"))
            self._keep_data_cb.setAccessibleName(tr("flash_keep_data_option"))
        if hasattr(self, "_keep_data_microsd_note"):
            self._keep_data_microsd_note.setText(self._microsd_note_html())
        if hasattr(self, "_charger_hint_label"):
            self._charger_hint_label.setText(tr("flash_hint_wall_charger"))
        if hasattr(self, "_driver_hint_label"):
            import re
            clean_driver = re.sub(r"<[^>]+>", "", tr("flash_hint_install_drivers")).strip()
            self._driver_hint_label.setText(clean_driver)
        if hasattr(self, "_driver_hint_btn"):
            self._driver_hint_btn.setText(tr("settings_download_drivers_btn"))
        if hasattr(self, "_driver_reboot_hint_label"):
            self._driver_reboot_hint_label.setText(tr("flash_hint_windows_reboot_driver"))
        self._update_paperclip_hint()
        self._sync_wait_band_page()
        if hasattr(self, "_post_install_hint"):
            self._post_install_hint.setText(self._post_install_hint_html())
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
            self._theme_recover_link.setText(
                _themed_link("recover", tr("rockbox_recover_troubleshoot"))
            )
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
        if hasattr(self, "_post_install_url_2_source"):
            from .. import fx

            # The chosen language can change while a download is running, so
            # the completion card re-derives its route instead of keeping the
            # one from when the firmware was selected.
            self._post_install_url_2 = (
                "" if fx.donation_route() else self._post_install_url_2_source
            )
        if hasattr(self, "_intro"):
            # The block keeps whichever wording it was set to: a language change
            # must not turn a finished install's appeal back into the generic
            # project pitch.
            self._render_appeal_copy()
        if hasattr(self, "_completion_note_container"):
            self._render_completion_note(post_install_once=getattr(self, "_post_install_once", False))
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
            "_wait_question_label", "_wait_question_heading", "_stop_question",
            "_flash_pkg_label", "_step_label", "_eta_label", "_partition_progress_label",
            "_warning", "_download_pkg_label", "_download_status_label", "_download_heading",
            "_appeal_label", "_appeal_body", "_appeal_signature", "_completion_guide_hint",
            "_paperclip_hint", "_keep_data_cb", "_keep_data_microsd_note",
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

        self._refresh_copy_theme()
        # The completion hints bake the link colour into their markup, so they
        # are rebuilt with the palette: a hint built before the theme applied
        # kept a black link on the dark card, which read as missing text.
        if hasattr(self, "_post_install_hint"):
            self._post_install_hint.setText(self._post_install_hint_html())
        if hasattr(self, "_completion_guide_hint"):
            self._completion_guide_hint.setText(self._completion_guide_html())
        if getattr(self, "_appeal", None) is not None and self._appeal.isVisible():
            self._render_appeal_copy()
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
        if getattr(self, "_post_install_url", "") and not getattr(self, "_donations_shown", False):
            from ..browser import open_browser

            open_browser(self._post_install_url)
        if callable(getattr(self, "_install_done_cb", None)):
            self._install_done_cb()

    def on_cancel(self, callback):
        self._cancel_callback = callback

    def on_cancel_wait(self, callback):
        self._cancel_wait_callback = callback

    def _nothing_written_yet(self) -> bool:
        """True on a wait card that has not touched the player.

        The power-off and USB-recovery cards are asking for a player to be
        brought back; nothing has been written on either, so leaving them needs
        no stop confirmation. The same goes for the Keep Data question and its
        erase warning: both are asked before the first write, and a user who
        does not want to answer them does not want this run. The connect card
        is different: the flasher is already running and watching for the port,
        so its cancel chip still asks first. This used to be spelled
        ``_prompt_only``, which made the connect card lose its confirmation once
        it became a prompt-only card.
        """
        return bool(getattr(self, "_prompt_only", False)) and getattr(
            self, "_wait_copy", ""
        ) in (
            "power_off",
            "usb_disconnected",
            "keep_data_confirm",
            "keep_data_erase_confirm",
        )

    def _on_cancel(self):
        if getattr(self, "_failed", False) or self._nothing_written_yet():
            callback = getattr(self, "_cancel_callback", None) or getattr(self, "_cancel_wait_callback", None)
            if callable(callback):
                callback()
            return
        self.prompt_stop(tr_brand("install_stop_check"), on_continue=self._cancel_callback)

    def _on_cancel_wait(self):
        """Stop the run. Before anything is written this needs no confirmation.

        At the power-off step the player has not been touched: the chip goes
        straight back to Select Software. Once a run is under way the same
        chip asks first, because there the target is already mid-write. The
        keep-data sanity check is the exception: its X reads "No", which means
        the player is running different software, not that the install stops.
        """
        if (
            getattr(self, "_keep_data_confirm_active", False)
            and not getattr(self, "_keep_data_cancel_leaves", False)
        ):
            callback = getattr(self, "_keep_data_confirm_no_cb", None)
            if callable(callback):
                self._keep_data_confirm_no_cb = None
                callback()
                return
        if self._nothing_written_yet() or getattr(self, "_failed", False):
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
            card_h = (
                self._progress_card.sizeHint().height()
                if hasattr(self, "_progress_card") and self._progress_card.isVisible()
                else 0
            )
            warn_h = 0
            if hasattr(self, "_warning") and self._warning.isVisible():
                warn_h = max(self._warning.sizeHint().height(), self._warning.heightForWidth(self.text_column_width())) + 8
            # The appeal and the completion note sit below the card at the
            # full content width, not in the card's fixed wrap column. They
            # must be measured at the width they are actually laid out at:
            # measuring them at INSTALL_TEXT_WIDTH wrapped the copy into
            # roughly twice the lines it draws and left an empty band under a
            # finished install.
            page_measure_w = int(self.width()) - 32
            if page_measure_w <= 0:
                page_measure_w = max(1, int(view_w) - install_horizontal_chrome(0))
            appeal_h = 0
            if hasattr(self, "_appeal") and self._appeal.isVisible():
                appeal_layout = self._appeal.layout()
                appeal_w = int(self._appeal.width()) or page_measure_w
                wrapped_h = (
                    appeal_layout.totalHeightForWidth(max(1, appeal_w))
                    if appeal_layout is not None
                    else 0
                )
                appeal_h = max(self._appeal.sizeHint().height(), wrapped_h) + 8
            complete_note_h = 0
            if hasattr(self, "_completion_note_container") and self._completion_note_container.isVisible():
                note_w = int(self._completion_note_container.width()) or page_measure_w
                complete_note_h = max(
                    self._completion_note_container.sizeHint().height(),
                    self._completion_note_container.heightForWidth(max(1, note_w)),
                ) + 8
            h = card_h + warn_h + appeal_h + complete_note_h
        elif cur_view is self._waiting_view:
            card_h = self._wait_card.sizeHint().height()
            head_h = self._wait_head.sizeHint().height() if hasattr(self, "_wait_head") else 48
            wait_col = getattr(self, "_wait_col", None)
            if wait_col is not None:
                # Measure each line at the width it is actually drawn at. The
                # fixed wrap column is only the minimum these labels are held
                # to; on a wider card they draw in fewer lines than that
                # column would need, and the old measurement left the waiting
                # screen taller than its words.
                body_w = wait_col.geometry().width()
                if body_w <= 0:
                    body_w = self.text_column_width() if hasattr(self, "text_column_width") else 260
                copy_h = 0
                for i in range(wait_col.count()):
                    item = wait_col.itemAt(i)
                    if item is not None and item.widget() is not None and not item.widget().isHidden():
                        w = item.widget()
                        if w.hasHeightForWidth():
                            # A label wraps at its own width, which the layout
                            # may size wider than the column's minimum.
                            label_w = int(w.width()) or int(body_w)
                            wh = w.heightForWidth(max(1, label_w))
                        else:
                            wh = w.sizeHint().height()
                        copy_h += wh + wait_col.spacing()
                card_h = max(card_h, head_h + copy_h + 36)
            hint_h = 0
            if (
                hasattr(self, "_paperclip_hint_host")
                and self._paperclip_hint_host.isVisible()
                and not self._wait_card.isAncestorOf(self._paperclip_hint_host)
            ):
                hint_h = self._paperclip_hint_host.sizeHint().height() + 6
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
        # The paperclip tip wraps to the card, not to the design column, so its
        # reserved height follows the width it is actually given.
        hint = getattr(self, "_paperclip_hint", None)
        if hint is not None and not hint.isHidden() and hint.width() > 0:
            hint.set_measure_width(hint.width())
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
        # The stop question takes the band's row rather than borrowing the
        # status line: it is the one thing the card is asking, and the two
        # answers belong on its axis. Created once and reparented to whichever
        # progress card is asking, so every stop check reads the same.
        self._stop_question = QLabel("")
        self._stop_question.setObjectName("installStopQuestion")
        self._stop_question.setWordWrap(True)
        self._stop_question.setTextFormat(Qt.TextFormat.PlainText)
        self._stop_question.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._stop_question.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred
        )
        _style_card_heading(self._stop_question)
        self._stop_question.hide()
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

    def _stop_column_widgets(self, column):
        """The cards' copy widgets, which step aside while the stop question asks."""
        if column is None:
            return []
        widgets = []
        for index in range(column.count()):
            item = column.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widgets.append(widget)
        return widgets

    def _place_stop_actions(self, row=None) -> None:
        """Put the question and its two answers on the card's head row.

        The stop question stands where the progress band is while the run is
        working, and the segmented pair follows it immediately. That is the
        same shape as the keep-data question: one line asks, the two answers
        sit on its axis, and the rest of the running card stays out of the way.
        """
        if row is None:
            row = getattr(self, "_stop_host", None)
        if row is None:
            return
        question = getattr(self, "_stop_question", None)
        actions = getattr(self, "_stop_actions", None)
        if question is not None:
            if row.indexOf(question) < 0:
                row.insertWidget(2, question, 0, Qt.AlignVCenter)
            question.show()
        if actions is not None:
            if row.indexOf(actions) < 0:
                row.insertWidget(3, actions, 0, Qt.AlignVCenter)
            actions.set_stacked(False)
            actions.show()
        self._stop_host = row
        if hasattr(self, "_stop_continue"):
            self._stop_continue.show()
            self._stop_back.show()
        self._fit_stop_question()

    def _fit_stop_question(self) -> None:
        """Give the stop question the room it needs on the head row.

        The head row is a horizontal box, which cannot trade width for height:
        a wrapping label left to its own size hint wraps into a narrow column
        and its last line is clipped by the row's fixed icon height. The
        question is held to one line when the row has the room, and otherwise
        to the widest column that fits beside the icon and chips, with its
        height reserved for the lines that column needs.
        """
        question = getattr(self, "_stop_question", None)
        host = getattr(self, "_stop_host", None)
        if question is None or host is None:
            return
        text = question.text() or " "
        metrics = question.fontMetrics()
        one_line = int(metrics.horizontalAdvance(text)) + 4
        parent = host.parentWidget()
        total = int(parent.width()) if parent is not None else 0
        actions = getattr(self, "_stop_actions", None)
        chips = int(actions.sizeHint().width()) if actions is not None else 0
        if total > 0:
            available = total - _CARD_ICON_PX - _CARD_HEAD_GAP * 3 - chips - 2
            width = min(one_line, max(120, available))
        else:
            width = one_line
        width = max(1, int(width))
        flags = int(Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap)
        rect = metrics.boundingRect(0, 0, width, 10000, flags, text)
        height = max(metrics.height(), rect.height()) + 2
        if question.minimumWidth() != width or question.maximumWidth() != width:
            question.setFixedWidth(width)
        if question.minimumHeight() != height:
            question.setMinimumHeight(height)
            question.updateGeometry()

    def resizeEvent(self, event):  # noqa: N802 (Qt naming)
        super().resizeEvent(event)
        if getattr(self, "_stop_open", False):
            self._fit_stop_question()

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
        identity = (
            row.itemAt(1).widget()
            if row is not None and row.count() > 1
            else None
        )
        column_widgets = self._stop_column_widgets(column)
        self._stop_open = True
        self._update_paperclip_hint()
        self._stop_message = message or ""
        self._stop_callback = on_continue
        self._stop_card = card
        self._stop_label = label
        self._stop_bar = bar
        self._stop_cancel = cancel
        self._stop_extra = extra
        self._stop_identity = identity
        self._stop_column = column
        self._stop_saved = {
            "label_text": label.text() if label is not None else "",
            "label_wrap": label.wordWrap() if label is not None else False,
            "bar_hidden": bar.isHidden() if bar is not None else False,
            "cancel_hidden": cancel.isHidden() if cancel is not None else False,
            "extra_hidden": extra.isHidden() if extra is not None else False,
            "column_widgets": [(w, w.isHidden()) for w in column_widgets],
            "identity_hidden": identity.isHidden() if identity is not None else False,
        }
        # The question takes the head's own row, where the progress band
        # stands while the run is working. The title and the body describe the
        # running install, not the question of whether to stop it.
        if identity is not None:
            identity.hide()
        for widget in column_widgets:
            widget.hide()
        if cancel is not None:
            cancel.hide()
        if extra is not None:
            extra.hide()
        question = getattr(self, "_stop_question", None)
        if question is not None:
            question.setText(self._stop_message)
            question.setAccessibleName(self._stop_message)
        self._retranslate_stop_buttons()
        self._place_stop_actions(row)
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
        """Keep the stop question in the head row if a progress update repaints."""
        if not getattr(self, "_stop_open", False):
            return
        question = getattr(self, "_stop_question", None)
        if question is not None:
            question.setText(self._stop_message)
            question.setAccessibleName(self._stop_message)
        identity = getattr(self, "_stop_identity", None)
        if identity is not None:
            identity.hide()
        column = getattr(self, "_stop_column", None)
        for widget in self._stop_column_widgets(column):
            widget.hide()
        cancel = getattr(self, "_stop_cancel", None)
        if cancel is not None:
            cancel.hide()
        extra = getattr(self, "_stop_extra", None)
        if extra is not None:
            extra.hide()
        self._place_stop_actions(getattr(self, "_stop_host", None))

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
        saved = getattr(self, "_stop_saved", None) or {}
        label = getattr(self, "_stop_label", None)
        bar = getattr(self, "_stop_bar", None)
        cancel = getattr(self, "_stop_cancel", None)
        extra = getattr(self, "_stop_extra", None)
        identity = getattr(self, "_stop_identity", None)
        column = getattr(self, "_stop_column", None)
        card = getattr(self, "_stop_card", None)
        host = getattr(self, "_stop_host", None)
        actions = getattr(self, "_stop_actions", None)
        question = getattr(self, "_stop_question", None)
        for widget in (question, actions):
            if widget is None:
                continue
            if host is not None and host.indexOf(widget) >= 0:
                host.removeWidget(widget)
            widget.hide()
            widget.setParent(None)
        self._stop_host = None
        if saved:
            if label is not None:
                label.setWordWrap(bool(saved.get("label_wrap", False)))
                label.setText(saved.get("label_text", ""))
            if bar is not None:
                bar.setVisible(not saved.get("bar_hidden", False))
            if cancel is not None:
                cancel.setVisible(not saved.get("cancel_hidden", False))
            if extra is not None:
                extra.setVisible(not saved.get("extra_hidden", False))
            if identity is not None:
                identity.setVisible(not saved.get("identity_hidden", False))
            for widget, hidden in saved.get("column_widgets", ()):
                widget.setVisible(not hidden)
        self._stop_identity = None
        self._stop_column = None
        loop = getattr(self, "_stop_loop", None)
        if loop is not None and loop.isRunning():
            loop.quit()
        # The card goes back to the line it was showing before the check, so a
        # dismissed check on the connect screen cannot leave the stop sentence
        # (or the software title with it) on screen until the next tick.
        self._sync_wait_band_page()
        # The paperclip guide lives in the copy column, so refresh it only
        # after that column has been restored; otherwise its own hide-state is
        # put back over the fresh answer and the tip stays gone.
        self._update_paperclip_hint()
        if card is not None:
            fade_in(card)
