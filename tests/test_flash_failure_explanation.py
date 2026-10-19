"""Tests for flash failure parsing, localized explanations, progress bar failed state,
and visible danger styling on the app heading in all locales.
"""

import os
import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

from src.flash_service import parse_flash_error, describe_backend_error
from src.i18n import tr, translator
from src.ui.widgets import InstallProgress
from src.ui.flash_page import FlashPage
from src.ui.main_window import MainWindow, _PAGE_FLASH, _PAGE_SELECT
from src.state import FlashState


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication(sys.argv)


def test_parse_flash_error_all_locales(app):
    """Verify that parse_flash_error parses all error categories and returns localized explanations."""
    test_cases = [
        # (raw_error, expected_code_or_token, expected_en_keyword)
        ("USB_DISCONNECTED", "USB", "disconnected"),
        ("S_BROM_CMD_STARTCMD_FAIL (2005)", "2005", "too early"),
        ("S_CHIP_TYPE_NOT_MATCH (3184)", "3184", "different model"),
        ("S_TIMEOUT (1042)", "1042", "never saw"),
        ("ERR_ATTEMPT_TIMEOUT", "TIMEOUT", "never saw"),
        ("CONNECTION_FAILED", "CONNECTION_FAILED", "stable connection"),
        ("MTK_INIT_FAILED", "MTK_INIT_FAILED", "stable connection"),
        ("S_FT_ENABLE_DRAM_FAIL (4032)", "4032", "memory"),
        ("WRITE_FAILED", "WRITE_FAIL", "interrupted"),
        ("S_FT_DOWNLOAD_FAIL (4008)", "4008", "interrupted"),
        ("NO_SCATTER_FILE", "", "scatter file"),
        ("MISSING_IMAGES", "", "missing"),
        ("SP_FLASH_TOOL_NOT_FOUND", "", "tool"),
        ("Unknown hardware glitch 0xdeadbeef", "0xdeadbeef", "unexpected error"),
    ]

    for loc in ("en", "zh-CN", "fr", "es", "de", "ja"):
        translator().set_language(loc)
        for raw_err, token, _en_kw in test_cases:
            title, explanation = parse_flash_error(raw_err, model="Y1")
            assert title, f"Title empty for {raw_err} in {loc}"
            assert explanation, f"Explanation empty for {raw_err} in {loc}"
            assert len(title) > 0
            assert len(explanation) > 10
            # Ensure "Flash" is not used in user-facing failure strings; should be "Install"
            if loc == "en" and not raw_err.startswith("SP_"):
                assert "Flash Failed" not in title, f"'Flash Failed' used instead of 'Install Failed': {title}"
            if token and token in raw_err:
                assert token in title or token in explanation

    translator().set_language("en")


def test_install_progress_failed_state(app):
    """The progress bar in the failed state replaces percentage with 'Install Failed' and halts filling."""
    bar = InstallProgress()
    bar.setValue(45)
    assert bar.text() == "45%"
    assert bar.fraction() > 0.4

    # Switch to failed state
    bar.set_failed(True)
    assert bar.is_failed()
    assert bar.text() == "Install Failed"
    # Percentage chunk is suppressed so user is not confused by stalled %
    assert bar.fraction() == 0.0

    # Retranslate / locale check
    translator().set_language("zh-CN")
    bar.set_failed(True, tr("install_failed"))
    assert bar.text() == "安装失败"

    # Reset back to normal
    translator().set_language("en")
    bar.set_failed(False)
    assert not bar.is_failed()
    assert bar.text() == "45%"


def test_flash_page_failure_card_display(app):
    """FlashPage.show_install_failure updates step label, explanation, and hides % on progress bar."""
    page = FlashPage()
    page.set_model("Y1")
    page.show_install_failure("S_BROM_CMD_STARTCMD_FAIL (2005)")

    assert page._failed
    assert page._progress_bar.is_failed()
    assert page._progress_bar.text() == "Install Failed"
    assert "2005" in page._step_label.text()
    assert "too early" in page._eta_label.text().lower() or "power" in page._eta_label.text().lower()
    assert page._status_flow._forced_stacked
    assert not page._flash_retry_btn.isHidden()
    assert not page._cancel_btn.isHidden()

    # Clear failure resets labels and styling
    page.clear_install_failure()
    assert not page._failed
    assert not page._progress_bar.is_failed()
    assert page._eta_label.text() == ""


def test_main_window_heading_visibility_on_failure(app):
    """MainWindow title in header bar visibly changes to Install Failed with danger color when flash fails."""
    from src.ui.dark import T
    t = T()

    win = MainWindow()
    try:
        app.processEvents()

        # In normal select page: normal title and palette color
        title = win._select_page._title
        assert "Install Failed" not in title.text()
        assert t.danger_fg not in title.styleSheet()

        # Trigger failure on flash page
        win._fallback_attempted = True
        win._handle_flash_failure("WRITE_FAILED")
        app.processEvents()

        assert win._install_failed
        assert win._stack.currentIndex() == _PAGE_FLASH
        assert "Install Failed" in title.text() or "Write Interrupted" in title.text()
        # Heading style must visibly use danger foreground color
        assert t.danger_fg in title.styleSheet()

        # Retry resets heading back to normal
        win._on_progress_retry()
        app.processEvents()
        assert not win._install_failed
        assert t.danger_fg not in title.styleSheet()
    finally:
        win.close()
        win.deleteLater()
        app.processEvents()
