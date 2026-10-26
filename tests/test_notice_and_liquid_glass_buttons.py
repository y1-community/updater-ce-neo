"""Tests for notice.xml handling, liquid glass buttons, and action button cursors."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QPushButton

from src.notice_service import Notice, parse_notice_xml, NoticeService
from src.ui.widgets import AppUpdateToast, BatteryToast, NoticeToast, UpdateToast
from src.ui.flash_page import _RoundNextButton


def test_parse_notice_xml():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <notices updated="2026-10-25">
      <notice id="test-1" persistent="false" severity="warning" models="Y1,Y2">
        <title>Advisory Title</title>
        <content>Important update content</content>
        <link>https://innioasis.app/info</link>
      </notice>
      <notice id="test-2" persistent="true">
        <title>Persistent PSA</title>
        <body>Always charged</body>
      </notice>
    </notices>
    """
    notices = parse_notice_xml(xml)
    assert len(notices) == 2

    n1 = notices[0]
    assert n1.id == "test-1"
    assert n1.title == "Advisory Title"
    assert n1.content == "Important update content"
    assert n1.persistent is False
    assert n1.severity == "warning"
    assert n1.link == "https://innioasis.app/info"
    assert n1.models == ("Y1", "Y2")
    assert n1.is_active_for("Y1") is True
    assert n1.is_active_for("G5") is False

    n2 = notices[1]
    assert n2.id == "test-2"
    assert n2.title == "Persistent PSA"
    assert n2.content == "Always charged"
    assert n2.persistent is True
    assert n2.link == ""
    assert n2.is_active_for("any") is True


def test_notice_service_persistence(tmp_path, monkeypatch):
    service = NoticeService()
    service._cache_file = tmp_path / "notice.xml"
    service._cache_dir = tmp_path

    # Mock settings storage
    stored = {}
    class FakeSettings:
        def __init__(self, *args):
            pass
        def value(self, key, default=None):
            return stored.get(key, default)
        def setValue(self, key, val):
            stored[key] = val

    import src.notice_service as ns
    monkeypatch.setattr(ns, "QSettings", FakeSettings)

    n_non_pers = Notice(id="p-false", title="A", content="B", persistent=False)
    n_pers = Notice(id="p-true", title="C", content="D", persistent=True)

    # Dismiss non-persistent -> saved in settings
    service.dismiss_notice(n_non_pers)
    assert "p-false" in service.get_dismissed_ids()
    assert "p-false" in service._session_dismissed

    # Dismiss persistent -> only in session, not in persistent settings
    service.dismiss_notice(n_pers)
    assert "p-true" in service._session_dismissed
    assert "p-true" not in service.get_dismissed_ids()


def test_action_buttons_cursor_is_arrow_cursor():
    app = QApplication.instance() or QApplication(sys.argv)
    btn = _RoundNextButton(symbol="next", accent=True)
    assert btn.cursor().shape() == Qt.ArrowCursor

    btn_cancel = _RoundNextButton(symbol="cancel", accent=False)
    assert btn_cancel.cursor().shape() == Qt.ArrowCursor


def test_round_next_button_arrow_color_is_light_on_dark_accent():
    app = QApplication.instance() or QApplication(sys.argv)
    btn = _RoundNextButton(symbol="next", accent=True)
    disc, text = btn._colors()
    # For purple, green or any dark accent disc, the glyph colour must be light (#ffffff)
    if (0.299 * disc.red() + 0.587 * disc.green() + 0.114 * disc.blue()) / 255.0 <= 0.65:
        assert text.lower() == "#ffffff"


def test_toast_widgets_use_liquid_glass_round_buttons():
    app = QApplication.instance() or QApplication(sys.argv)

    # UpdateToast
    ut = UpdateToast()
    install_btn = ut.findChild(QPushButton, "updateToastInstall")
    close_btn = ut.findChild(QPushButton, "updateToastClose")
    assert isinstance(install_btn, _RoundNextButton)
    assert isinstance(close_btn, _RoundNextButton)
    assert install_btn.cursor().shape() == Qt.ArrowCursor
    assert close_btn.cursor().shape() == Qt.ArrowCursor

    # AppUpdateToast
    aut = AppUpdateToast()
    action_btn = aut.findChild(QPushButton, "appUpdateToastAction")
    app_close_btn = aut.findChild(QPushButton, "appUpdateToastClose")
    assert isinstance(action_btn, _RoundNextButton)
    assert isinstance(app_close_btn, _RoundNextButton)
    assert action_btn.cursor().shape() == Qt.ArrowCursor
    assert app_close_btn.cursor().shape() == Qt.ArrowCursor

    # BatteryToast
    bt = BatteryToast()
    dismiss_btn = bt.findChild(QPushButton, "batteryToastDismissBtn")
    assert isinstance(dismiss_btn, _RoundNextButton)
    assert dismiss_btn.cursor().shape() == Qt.ArrowCursor

    # NoticeToast
    nt = NoticeToast()
    notice = Notice(id="n1", title="Notice", content="Content", link="https://innioasis.app")
    nt.set_notice(notice)
    assert nt.isVisible()
    n_action = nt.findChild(QPushButton, "noticeToastAction")
    n_close = nt.findChild(QPushButton, "noticeToastClose")
    assert isinstance(n_action, _RoundNextButton)
    assert isinstance(n_close, _RoundNextButton)
    assert n_action.cursor().shape() == Qt.ArrowCursor
    assert n_close.cursor().shape() == Qt.ArrowCursor


def test_retry_buttons_use_arrow_based_retry_glyph():
    app = QApplication.instance() or QApplication(sys.argv)

    from src.ui.icons import get_symbol_pixmap, _get_svg_pixmap, _SF_SYMBOL_MAP, _SEGOE_SYMBOL_MAP
    from src.ui.native_controls import _SF_SYMBOLS, _SEGMENTED_SF_SYMBOLS
    from src.ui.flash_page import FlashPage, _RoundNextButton
    from src.i18n import tr

    # 1. Platform mappings for arrow-based retry glyph
    assert _SF_SYMBOLS["retry"] == "arrow.clockwise"
    assert _SEGMENTED_SF_SYMBOLS["retry"] == "arrow.clockwise"
    assert _SF_SYMBOL_MAP["retry"] == "arrow.clockwise"
    assert _SEGOE_SYMBOL_MAP["retry"] == 0xE72C

    # 2. Universal vector SVG fallback renders non-empty
    svg_pix = _get_svg_pixmap("retry", 16, "#3ecf6a")
    assert not svg_pix.isNull()
    assert svg_pix.width() > 0 and svg_pix.height() > 0

    # 3. get_symbol_pixmap returns valid pixmap
    pix = get_symbol_pixmap("retry", 16, "#ffffff")
    assert not pix.isNull()

    # 4. _RoundNextButton with symbol="retry"
    btn = _RoundNextButton(symbol="retry", accent=True)
    assert btn._symbol == "retry"
    assert btn._glyph_symbol() == "retry"
    assert btn.toolTip() == tr("flash_btn_retry")
    btn.rebuild_arrow()
    assert not btn._arrow.isNull()

    # 5. FlashPage retry button uses symbol="retry"
    page = FlashPage()
    try:
        assert page._flash_retry_btn._symbol == "retry"
        assert page._flash_retry_btn.toolTip() == tr("flash_btn_retry")
    finally:
        page.close()
        app.processEvents()

