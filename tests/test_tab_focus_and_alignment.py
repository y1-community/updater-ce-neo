"""Tests for keyboard tab focus management, circular focus outlines, and install card alignment."""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QPushButton

from src.ui.flash_page import FlashPage, _RoundNextButton
from src.ui.tab_focus import TabFocusManager


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_round_next_button_has_mac_focus_rect_disabled():
    _app()
    btn = _RoundNextButton(symbol="cancel", accent=False)
    assert not btn.testAttribute(Qt.WidgetAttribute.WA_MacShowFocusRect)
    assert btn.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_tab_focus_manager_activates_on_tab_and_dismisses_on_timeout():
    _app()
    TabFocusManager.ensure_installed()
    TabFocusManager.dismiss_all()
    assert not TabFocusManager.is_tab_active()

    # Simulate Tab press
    event = QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Tab), Qt.KeyboardModifier.NoModifier)
    TabFocusManager._instance.eventFilter(None, event)
    assert TabFocusManager.is_tab_active()

    # Simulate timeout
    TabFocusManager._on_timeout()
    assert not TabFocusManager.is_tab_active()


def test_tab_focus_manager_dismisses_on_mouse_movement():
    _app()
    TabFocusManager.ensure_installed()
    TabFocusManager.dismiss_all()

    # Simulate Tab press
    event = QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_Tab), Qt.KeyboardModifier.NoModifier)
    TabFocusManager._instance.eventFilter(None, event)
    assert TabFocusManager.is_tab_active()

    # Simulate mouse movement to a new coordinate
    mouse_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPoint(100, 100),
        QPoint(100, 100),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    TabFocusManager._instance.eventFilter(None, mouse_event)
    assert not TabFocusManager.is_tab_active()


def test_round_button_paints_circular_focus_when_tab_active():
    _app()
    from PySide6.QtGui import QFocusEvent

    btn = _RoundNextButton(symbol="cancel", accent=False)
    btn.resize(30, 30)

    # Simulate tab active and focus in with TabFocusReason
    TabFocusManager._tab_active = True
    TabFocusManager._last_tab_time = time.monotonic()
    focus_event = QFocusEvent(QEvent.Type.FocusIn, Qt.FocusReason.TabFocusReason)
    btn.focusInEvent(focus_event)
    assert btn._keyboard_focused

    # Paint button onto a pixmap
    pm = QPixmap(30, 30)
    btn.render(pm)
    assert not pm.isNull()

    # Mouse press clears keyboard focus
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPoint(15, 15),
        QPoint(15, 15),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    btn.mousePressEvent(press_event)
    assert not btn._keyboard_focused
    assert not TabFocusManager.is_tab_active()


def test_all_install_cards_continual_alignment():
    """All four cards are one shape: chips on the head row, copy under it.

    The chips used to be centred on the card as a whole, which put them level
    with the guidance copy on a tall (failed) card. They belong to the software
    title and its progress band, so the row they are added to is the head row
    and they are centred on that, not on the card.
    """
    app = _app()
    page = FlashPage()
    page.set_package_name("Original Software 3.1.2 for Y1")
    page.resize(700, 450)
    page.show()
    app.processEvents()

    # 1. Preparing view
    page.show_preparing()
    app.processEvents()
    assert page._prep_row is page._prep_head.box
    assert page._prep_head.box.indexOf(page._prep_cancel_btn) >= 0
    assert page._prep_head.icon is page._prep_icon
    assert page._prep_icon.width() == 40
    assert page._prep_cancel_btn.y() > 0

    # 2. Waiting view (power off prompt)
    page.show_power_off_prompt()
    app.processEvents()
    assert page._wait_row is page._wait_head.box
    assert page._wait_head.box.indexOf(page._wait_continue_btn) >= 0
    assert page._wait_head.box.indexOf(page._wait_cancel_btn) >= 0
    assert page._wait_continue_btn.isVisible()
    assert page._wait_cancel_btn.isVisible()
    top, bottom = page._wait_head.band_span()
    assert top <= page._wait_continue_btn.geometry().center().y() <= bottom
    # Inside-card mode hint is hidden
    assert not page._wait_mode_hint.isVisible()
    # Under-card paperclip hint is visible for Y1
    assert page._paperclip_hint_host.isVisible()
    assert "headphone" in page._paperclip_hint.text().lower() or "button" in page._paperclip_hint.text().lower()

    # 3. Flashing view
    page.show_flashing()
    app.processEvents()
    assert page._flash_row is page._flash_head.box
    assert page._flash_head.box.indexOf(page._cancel_btn) >= 0
    assert page._flash_head.icon is page._flash_icon
    assert page._cancel_btn.y() > 0

    # 4. Downloading view
    page.show_downloading()
    app.processEvents()
    assert page._download_row is page._download_head.box
    assert page._download_head.box.indexOf(page._download_cancel_btn) >= 0
    assert page._download_head.icon is page._download_icon
    assert page._download_cancel_btn.y() > 0

    page.close()
