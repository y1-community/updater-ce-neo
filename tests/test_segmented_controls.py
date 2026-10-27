import os
import sys
from pathlib import Path
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ui.flash_page import FlashPage
from src.i18n import tr, tr_brand


@pytest.fixture
def app():
    return QApplication.instance() or QApplication(sys.argv)


def test_segmented_controls_across_all_install_views(app):
    page = FlashPage()
    page.set_model("Y1")
    page.set_package_name("Original Software 3.1.2 for Y1")
    page.show()
    app.processEvents()

    try:
        # 1. Downloading view
        page._switch_view(page._downloading_view)
        app.processEvents()
        assert not page._download_cancel_btn.isHidden()
        assert not page._download_next_btn.isHidden()
        assert page._download_cancel_btn.isEnabled()
        assert not page._download_next_btn.isEnabled()
        assert page._download_cancel_btn._companion is page._download_next_btn
        assert page._download_next_btn._companion is page._download_cancel_btn
        assert page._download_cancel_btn._glyph_symbol() == "chevron.left"
        assert page._download_next_btn._glyph_symbol() == "chevron.right"

        # 2. Preparing view
        page._switch_view(page._preparing_view)
        app.processEvents()
        assert not page._prep_cancel_btn.isHidden()
        assert not page._prep_next_btn.isHidden()
        assert page._prep_cancel_btn.isEnabled()
        assert not page._prep_next_btn.isEnabled()
        assert page._prep_cancel_btn._companion is page._prep_next_btn
        assert page._prep_next_btn._companion is page._prep_cancel_btn
        assert page._prep_cancel_btn._glyph_symbol() == "chevron.left"
        assert page._prep_next_btn._glyph_symbol() == "chevron.right"

        # 3. Waiting view (device connect / search)
        page.show_connect_search()
        app.processEvents()
        assert not page._wait_cancel_btn.isHidden()
        assert not page._wait_continue_btn.isHidden()
        assert page._wait_cancel_btn.isEnabled()
        assert not page._wait_continue_btn.isEnabled()
        assert page._wait_cancel_btn._companion is page._wait_continue_btn
        assert page._wait_continue_btn._companion is page._wait_cancel_btn
        assert page._wait_cancel_btn._glyph_symbol() == "chevron.left"
        assert page._wait_continue_btn._glyph_symbol() == "chevron.right"

        # 4. Flashing view (in progress)
        page.show_flashing()
        app.processEvents()
        assert not page._cancel_btn.isHidden()
        assert not page._flash_retry_btn.isHidden()
        assert page._cancel_btn.isEnabled()
        assert not page._flash_retry_btn.isEnabled()
        assert page._cancel_btn._symbol == "back"
        assert page._flash_retry_btn._symbol == "next"
        assert page._cancel_btn._companion is page._flash_retry_btn
        assert page._flash_retry_btn._companion is page._cancel_btn
        assert page._cancel_btn._glyph_symbol() == "chevron.left"
        assert page._flash_retry_btn._glyph_symbol() == "chevron.right"

        # 5. Flashing view (failure)
        page.show_install_failure("ERROR_TEST")
        app.processEvents()
        assert not page._cancel_btn.isHidden()
        assert not page._flash_retry_btn.isHidden()
        assert page._cancel_btn.isEnabled()
        assert page._flash_retry_btn.isEnabled()
        assert page._cancel_btn._symbol == "cancel"
        assert page._flash_retry_btn._symbol == "retry"

        # Return to flashing (clear failure)
        page.clear_install_failure()
        page.show_flashing()
        app.processEvents()
        assert page._cancel_btn._symbol == "back"
        assert page._flash_retry_btn._symbol == "next"
        assert page._cancel_btn.isEnabled()
        assert not page._flash_retry_btn.isEnabled()

        # 6. Flashing view (completion state: segmented pair with locked back + active checkmark)
        page.set_device_done()
        app.processEvents()
        assert not page._cancel_btn.isHidden()
        assert not page._done_mark.isHidden()
        assert not page._cancel_btn.isEnabled()
        assert page._done_mark.isEnabled()
        assert page._cancel_btn._companion is page._done_mark
        assert page._done_mark._companion is page._cancel_btn
        assert page._cancel_btn._glyph_symbol() == "chevron.left"
        assert page._done_mark._symbol == "complete"
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_cancel_revealed_on_back_confirmation(app):
    page = FlashPage()
    page.set_model("Y1")
    page.set_package_name("Original Software 3.1.2 for Y1")
    page.show()
    app.processEvents()

    try:
        page.show_connect_search()
        page.set_attempt_timeout(35)
        app.processEvents()
        assert "35s" in page._wait_prompt_label.text()

        # Before back is selected: back button (<) and greyed out next (>)
        assert not page._wait_cancel_btn.isHidden()
        assert not page._wait_continue_btn.isHidden()
        assert not page._wait_continue_btn.isEnabled()

        cancelled = []
        page.on_cancel_wait(lambda: cancelled.append(True))

        # Select back -> confirmation asked
        page._wait_cancel_btn.click()
        app.processEvents()

        assert getattr(page, "_stop_open", False) is True
        assert page._wait_prompt_label.text() == tr_brand("install_stop_check")

        # In confirmation state: cancel button (X) is revealed in _stop_actions
        assert not page._stop_actions.isHidden()
        assert not page._stop_continue.isHidden()
        assert not page._stop_back.isHidden()
        assert page._stop_continue._symbol == "cancel"
        assert page._stop_back._symbol == "back"
        assert page._stop_back._companion is page._stop_continue
        assert page._stop_continue._companion is page._stop_back

        # Timer countdown ticks do not clobber the confirmation question
        page.set_attempt_timeout(22)
        app.processEvents()
        assert page._wait_prompt_label.text() == tr_brand("install_stop_check")

        # Reject confirmation -> returns to install view with [ < | > ]
        page._stop_back.click()
        app.processEvents()
        assert getattr(page, "_stop_open", False) is False
        assert not page._wait_cancel_btn.isHidden()
        assert not page._wait_continue_btn.isHidden()
        assert not page._wait_continue_btn.isEnabled()
        assert "22s" in page._wait_prompt_label.text()
        assert cancelled == []

        # Click back again and this time accept cancel
        page._wait_cancel_btn.click()
        app.processEvents()
        assert getattr(page, "_stop_open", False) is True

        page._stop_continue.click()
        app.processEvents()
        assert cancelled == [True]
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()
