"""The install card's step line matches the rest of the card, on every theme.

The step line ("Writing main system software (13%)") bakes the theme
foreground when it is built and is also reset with an empty stylesheet after a
failed attempt. Either way it can drift from the ETA and copy around it, which
inherit the window rule, and it never followed a theme change made while the
app is running. It must take the live card foreground instead — except while
it is carrying the failure colour.
"""

import os
import sys
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.ui import dark
from src.ui.dark import theme_text_color
from src.ui.flash_page import FlashPage


@pytest.fixture()
def page():
    app = QApplication.instance() or QApplication(sys.argv)
    yield FlashPage()


def _fg_hex() -> str:
    return theme_text_color().name()


def test_step_line_has_the_card_foreground_after_a_cleared_failure(page):
    page._failed = True
    page._step_label.setStyleSheet("color: #dc2626; font-weight: 700;")

    page.clear_install_failure()

    assert page._step_label.styleSheet().strip(), "must not fall back to inheriting"
    assert _fg_hex() in page._step_label.styleSheet()


def test_status_lines_follow_a_live_theme_change(page):
    app = QApplication.instance()
    before = theme_text_color()
    page.update_action("Writing main system software (13%)")
    page._step_label.setStyleSheet(f"color: {before.name()};")

    pal = app.palette()
    original = pal.color(QPalette.ColorRole.WindowText)
    try:
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#123456"))
        app.setPalette(pal)
        assert theme_text_color().name() == "#123456"
        page.refresh_theme()
        assert "#123456" in page._step_label.styleSheet()
        assert "#123456" in page._eta_label.styleSheet()
        # The failure line is left alone while a failure is on screen.
        page._failed = True
        page._step_label.setStyleSheet("color: #dc2626; font-weight: 700;")
        page.refresh_theme()
        assert "#123456" not in page._step_label.styleSheet()
    finally:
        pal.setColor(QPalette.ColorRole.WindowText, original)
        app.setPalette(pal)
        page._failed = False
        page.refresh_theme()


def test_completion_letter_and_prompt_links_follow_a_live_switch(page):
    """The install-complete letter and its links take the new theme's colour.

    Their copy bakes a colour when it is written (rich text cannot use a QSS
    palette role), and the page's refresh hook used to be shadowed by a second
    ``refresh_theme`` definition, so a live switch left the letter and the
    "Don't ask me again" link in the previous theme's colour.
    """
    app = QApplication.instance()
    pal = app.palette()
    original = QColor(pal.color(QPalette.ColorRole.WindowText))
    original_placeholder = QColor(pal.color(QPalette.ColorRole.PlaceholderText))
    try:
        page.show_completion_appeal(True, callback=lambda: None)
        page.show_keep_data_erase_confirmation(
            "Install and erase?",
            'Settings reset. <a href="try-anyway">Try anyway</a>',
        )
        for colour in ("#1f6feb", "#f5f7fa"):
            pal.setColor(QPalette.ColorRole.WindowText, QColor(colour))
            pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(colour))
            app.setPalette(pal)
            dark._restyle_existing_widgets(app)

            for label in (
                page._appeal_body,
                page._appeal_signature,
                page._intro.headline,
                page._intro.subtitle,
            ):
                assert colour in label.styleSheet(), label.styleSheet()
            assert colour in page._appeal_dont.text(), page._appeal_dont.text()
            assert colour in page._keep_data_hint.text(), page._keep_data_hint.text()
    finally:
        pal.setColor(QPalette.ColorRole.WindowText, original)
        pal.setColor(QPalette.ColorRole.PlaceholderText, original_placeholder)
        app.setPalette(pal)
        page._failed = False
        dark._restyle_existing_widgets(app)


def test_prompt_and_keep_data_questions_take_the_host_text_colour(page):
    """The card's action lines keep a desktop colour, not a derived black/white."""
    app = QApplication.instance()
    pal = app.palette()
    original = QColor(pal.color(QPalette.ColorRole.WindowText))
    try:
        # A Plasma theme may use its accent as ordinary text. This colour is
        # darker than the usual light-foreground threshold, so the old
        # theme_foreground() normalisation replaced it with white.
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#1f6feb"))
        app.setPalette(pal)
        page.show_power_off_prompt()
        page.show_keep_data_confirmation(
            "Which firmware is running on your Y1?", choices=("Rockbox", "Other")
        )
        page.refresh_theme()

        for label in (
            page._wait_prompt_label,
            page._wait_question_label,
            page._wait_question_heading,
        ):
            assert "#1f6feb" in label.styleSheet(), label.styleSheet()

        # The same labels follow a live light/dark switch after they were built.
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#f5f7fa"))
        app.setPalette(pal)
        page.refresh_theme()
        for label in (
            page._wait_prompt_label,
            page._wait_question_label,
            page._wait_question_heading,
        ):
            assert "#f5f7fa" in label.styleSheet(), label.styleSheet()
    finally:
        pal.setColor(QPalette.ColorRole.WindowText, original)
        app.setPalette(pal)
        page.refresh_theme()
