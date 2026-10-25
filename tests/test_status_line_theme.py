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

from src.ui.dark import theme_foreground
from src.ui.flash_page import FlashPage


@pytest.fixture()
def page():
    app = QApplication.instance() or QApplication(sys.argv)
    yield FlashPage()


def _fg_hex() -> str:
    return theme_foreground().name()


def test_step_line_has_the_card_foreground_after_a_cleared_failure(page):
    page._failed = True
    page._step_label.setStyleSheet("color: #dc2626; font-weight: 700;")

    page.clear_install_failure()

    assert page._step_label.styleSheet().strip(), "must not fall back to inheriting"
    assert _fg_hex() in page._step_label.styleSheet()


def test_status_lines_follow_a_live_theme_change(page):
    app = QApplication.instance()
    before = theme_foreground()
    page.update_action("Writing main system software (13%)")
    page._step_label.setStyleSheet(f"color: {before.name()};")

    pal = app.palette()
    original = pal.color(QPalette.ColorRole.WindowText)
    try:
        pal.setColor(QPalette.ColorRole.WindowText, QColor("#123456"))
        app.setPalette(pal)
        assert theme_foreground().name() == "#123456"
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
