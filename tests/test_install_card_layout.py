"""Progress-card fit: status row reflow, chip centring, translucent discs.

The install card's copy is one wrapped block inside a fixed column. These
tests pin the behaviour a wide window is supposed to buy: the step and its
ETA share a row instead of holding two lines, the round chips sit centred
on the card rather than floating above the progress band, and the chip discs
let the card's glass through instead of painting an opaque blob.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.ui.flash_page import (  # noqa: E402
    _ACCENT_DISC_ALPHA,
    _NEUTRAL_DISC_ALPHA,
    FlashPage,
    _RoundNextButton,
)


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _flashing_page(width: int) -> FlashPage:
    app = _app()
    page = FlashPage()
    page.set_package_name("Original Software 3.1.2 for Y1")
    page.show_flashing()
    page.update_step("step_download_da")
    page.update_time("00:00", "03:00")
    page.resize(width, 420)
    page.show()
    app.processEvents()
    return page


def test_step_and_eta_share_one_row_when_the_card_is_wide():
    page = _flashing_page(900)
    try:
        flow = page._status_flow
        assert flow._box.direction() == flow._box.Direction.LeftToRight
        assert not page._step_label.wordWrap()
        assert page._step_label.y() == page._eta_label.y()
        assert page._step_label.parentWidget() is flow
    finally:
        page.close()


def test_step_and_eta_stack_again_when_the_card_is_narrow():
    page = _flashing_page(300)
    try:
        flow = page._status_flow
        assert flow._box.direction() == flow._box.Direction.TopToBottom
        assert page._step_label.wordWrap()
        assert page._step_label.y() != page._eta_label.y()
    finally:
        page.close()


def test_stop_prompt_keeps_the_status_line_wrapping():
    """The sanity check borrows the step line, so it must wrap, not run off."""
    page = _flashing_page(900)
    try:
        page.prompt_stop("Stop installing this software?", on_continue=lambda: None)
        assert page._status_flow._forced_stacked
        assert page._step_label.wordWrap()
        page._finish_stop(False)
        assert not page._status_flow._forced_stacked
    finally:
        page.close()


def test_round_chips_are_centred_on_the_card():
    page = _flashing_page(900)
    try:
        row = page._flash_row
        index = row.indexOf(page._cancel_btn)
        assert index >= 0
        assert row.itemAt(index).alignment() == Qt.AlignVCenter
    finally:
        page.close()


def _disc_fill(brush, background):
    """Paint the chip's disc over ``background`` the way paintEvent does."""
    from PySide6.QtGui import QPainter

    size = 30
    canvas = QPixmap(size, size)
    canvas.fill(background)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(brush)
    painter.drawEllipse(QRect(0, 0, size, size))
    painter.end()
    return canvas.toImage().pixelColor(size // 2, size // 2)


def test_round_discs_are_translucent():
    app = _app()
    consts = (_ACCENT_DISC_ALPHA, _NEUTRAL_DISC_ALPHA)
    assert all(0 < alpha < 255 for alpha in consts), consts
    # Only the lead (accent) chip may be near-opaque; a neutral chip is a wash.
    assert _NEUTRAL_DISC_ALPHA < _ACCENT_DISC_ALPHA < 255, consts
    for symbol, accent, alpha in (
        ("next", True, _ACCENT_DISC_ALPHA),
        ("cancel", False, _NEUTRAL_DISC_ALPHA),
    ):
        button = _RoundNextButton(symbol=symbol, accent=accent)
        button.rebuild_arrow()
        disc = button._colors()[0]
        assert disc.alpha() == alpha, (symbol, disc.alpha())
        over_nothing = _disc_fill(disc, QColor(0, 0, 0, 0))
        assert over_nothing.alpha() == alpha, (symbol, over_nothing.alpha())
        # The card behind it shows through: same disc, two different results.
        over_black = _disc_fill(disc, QColor("#000000"))
        over_white = _disc_fill(disc, QColor("#ffffff"))
        assert over_black.getRgb() != over_white.getRgb()
        assert over_black.lightness() < over_white.lightness()
    del app
