"""Progress-card fit: status row reflow, chip placement, raised translucent discs.

The install card's copy is one wrapped block inside a fixed column. These
tests pin the behaviour a wide window is supposed to buy: the step and its
ETA share a row instead of holding two lines, and the round chips sit to the
right of and inline with the software title and the progress band — on every
progress screen, under every host look — instead of drifting to the middle of
a taller card. The discs stay translucent and now sit above a soft shadow, so
the card's glass shows through a chip that reads as raised.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory  # noqa: E402

from src.ui.flash_page import (  # noqa: E402
    _ACCENT_DISC_ALPHA,
    _CARD_COPY_INDENT,
    _CHIP_DISC_PX,
    _NEUTRAL_DISC_ALPHA,
    INSTALL_TEXT_WIDTH,
    FlashPage,
    _RoundNextButton,
    install_horizontal_chrome,
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


def test_eta_uses_measured_install_time_and_optional_prep_time():
    page = FlashPage()
    try:
        page.update_time("00:10", "01:05")
        install = page._format_friendly_eta("01:05")
        assert page._eta_label.text() == f"— {install}"

        page.set_prep_eta_seconds(17)
        assert page._eta_label.text().splitlines() == [
            f"— {install}",
            page._format_friendly_eta("00:17"),
        ]

        page.set_prep_eta_seconds(0)
        assert page._eta_label.text() == f"— {install}"
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


def test_round_chips_are_inline_with_the_title_and_progress_band():
    """The chips belong to the title and its band, not to the middle of the card.

    Centred on a card that is tall because of its guidance copy, the chips read
    as part of that copy. Anchored to the head row they stay the next step of
    the software named above them, which is what the mockup asks for.
    """
    page = _flashing_page(900)
    try:
        page.show_install_failure("USB connection was lost")
        _app().processEvents()
        head = page._flash_head
        row = getattr(page, "_actions_row", page._flash_row)
        assert row is head.box
        ident_right = head.identity.geometry().right()
        top, bottom = head.band_span()
        for chip in (page._flash_retry_btn, page._cancel_btn):
            assert row.indexOf(chip) >= 0, "the chip is not on the head row"
            assert chip.geometry().left() >= ident_right, chip
            centre = chip.geometry().center().y()
            assert top <= centre <= bottom, (centre, top, bottom)
            assert centre < page._progress_card.height() // 2, "chip drifted to the card centre"
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


def test_cancel_and_action_buttons_are_to_the_right_of_progress():
    page = _flashing_page(700)
    try:
        page.show_install_failure("Test error")
        _app().processEvents()
        assert page._cancel_btn.isVisible()
        assert page._flash_retry_btn.isVisible()
        # The band lives inside the title column, so measure it from there: the
        # pair of chips has to start past the right edge of the progress band.
        ident = page._flash_head.identity
        band_right = ident.geometry().left() + page._progress_bar.geometry().right()
        assert page._cancel_btn.x() > page._flash_retry_btn.x()
        assert page._flash_retry_btn.x() >= band_right, (page._flash_retry_btn.x(), band_right)
    finally:
        page.close()


def _paint_chip(button, background) -> QImage:
    """Paint a chip onto its own canvas the way its paintEvent does."""
    image = QImage(button.width(), button.height(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(background)
    painter = QPainter(image)
    try:
        button.paint_into(painter)
    finally:
        painter.end()
    return image


def test_round_chip_reads_as_raised_above_the_card():
    _app()  # the chip reads the live palette before it paints
    button = _RoundNextButton(symbol="next")
    button.rebuild_arrow()
    disc = button._disc_rect()
    assert disc.width() == _CHIP_DISC_PX, disc
    assert button.width() > disc.width(), "no room kept around the disc for a shadow"

    image = _paint_chip(button, QColor(0, 0, 0, 0))
    centre_x = button.width() // 2
    under = image.pixelColor(centre_x, disc.bottom() + 2).alpha()
    far = image.pixelColor(centre_x, button.height() - 1).alpha()
    above = image.pixelColor(centre_x, disc.top() - 2).alpha()
    assert under > 0, "the chip paints no shadow, so it cannot read as raised"
    assert far < under, (far, under)
    # Light from above: the shade gathers under the disc, not over it.
    assert above <= under, (above, under)

    # Raised is not opaque: the card still shows through the disc.
    over_white = _paint_chip(button, QColor("#ffffff")).pixelColor(disc.center())
    over_black = _paint_chip(button, QColor("#000000")).pixelColor(disc.center())
    assert over_white.getRgb() != over_black.getRgb()
    assert over_black.lightness() < over_white.lightness()


def test_every_progress_screen_puts_the_chips_on_the_same_head_row():
    """One card shape for all four screens, whichever host look is in charge.

    Aqua, WinUI, Breeze and Adwaita only paint the controls inside the card; the
    places are decided in one builder. Forcing each style Qt can offer is how
    that promise is checked here, since the layout must not depend on the style.
    """
    app = _app()
    screens = (
        ("show_preparing", "_prep_head", "_prep_col"),
        ("show_power_off_prompt", "_wait_head", "_wait_col"),
        ("show_flashing", "_flash_head", "_flash_col"),
        ("show_downloading", "_download_head", "_download_col"),
    )
    styles = ["Fusion"] + [name for name in QStyleFactory.keys() if name != "Fusion"]
    for style_name in styles:
        style = QStyleFactory.create(style_name)
        if style is None:
            continue
        app.setStyle(style)
        page = FlashPage()
        page.set_package_name("Original Software 3.1.2 for Y1")
        page.resize(install_horizontal_chrome(0) + INSTALL_TEXT_WIDTH, 460)
        page.show()
        try:
            for screen, head_name, copy_name in screens:
                getattr(page, screen)()
                app.processEvents()
                head = getattr(page, head_name)
                assert head.box.itemAt(0).widget() is head.icon
                assert head.box.itemAt(1).widget() is head.identity
                # Only the chips this screen actually shows: a hidden chip has
                # no geometry to check (the retry arrow, the done mark).
                chips = [head.box.itemAt(i).widget() for i in range(2, head.box.count())]
                chips = [chip for chip in chips if chip.isVisible()]
                assert chips, (style_name, head_name)
                ident_right = head.identity.geometry().right()
                top, bottom = head.band_span()
                for chip in chips:
                    assert chip.geometry().left() >= ident_right, (style_name, head_name)
                    centre = chip.geometry().center().y()
                    assert top <= centre <= bottom, (style_name, head_name, centre, top, bottom)
                # The copy sits under the row, lined up with the software title.
                copy_left = getattr(page, copy_name).geometry().left() + _CARD_COPY_INDENT
                title_left = head.geometry().left() + head.identity.geometry().left()
                assert abs(copy_left - title_left) <= 2, (style_name, head_name, copy_left, title_left)
        finally:
            page.close()
            app.processEvents()


def test_the_copy_keeps_its_wrap_width_at_the_recommended_window_size():
    """The head row must not squeeze the copy below the width it wraps into."""
    app = _app()
    page = FlashPage()
    page.set_package_name("Original Software 3.1.2 for Y1")
    page.resize(install_horizontal_chrome(0) + INSTALL_TEXT_WIDTH, 470)
    page.show()
    try:
        page.show_flashing()
        app.processEvents()
        copy_width = page._flash_col.geometry().width() - _CARD_COPY_INDENT
        assert copy_width >= INSTALL_TEXT_WIDTH, copy_width
        assert page._flash_head.identity.width() >= INSTALL_TEXT_WIDTH, page._flash_head.identity.width()
    finally:
        page.close()
        app.processEvents()
