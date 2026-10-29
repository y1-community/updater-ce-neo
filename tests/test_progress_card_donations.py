"""The progress card is hidden only on a finished install with donations on.

Donations replace the card's completion message, so the card hides at the
end of a run. A later run must bring it back: the donations choice belongs to
one run, not to the page.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture()
def page():
    from src.ui.flash_page import FlashPage

    _app()
    p = FlashPage()
    p.show()
    _app().processEvents()
    yield p
    p.close()


def test_completion_with_donations_hides_the_card(page):
    page.set_device_done(donations_shown=True)
    assert page._progress_card.isHidden()


def test_completion_without_donations_keeps_the_card(page):
    page.set_device_done(donations_shown=False)
    assert not page._progress_card.isHidden()


def test_a_new_run_brings_the_card_back(page):
    page.set_device_done(donations_shown=True)
    assert page._progress_card.isHidden()

    page.begin_run()
    page.show_preparing()
    page.show_flashing()

    assert not page._progress_card.isHidden()


def test_a_failed_run_keeps_the_card_up_after_a_donations_success(page):
    page.set_device_done(donations_shown=True)
    page.begin_run()
    page.show_flashing()
    assert not page._progress_card.isHidden()
