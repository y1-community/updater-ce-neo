"""Test focus rectangle behaviour on SidebarButton:
Focus rings must appear ONLY on keyboard navigation (Tab / Backtab),
and must be dismissed whenever the mouse is in use.
"""

from __future__ import annotations

import sys
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from src.ui.sidebar import SidebarButton


def _render_btn(btn: SidebarButton) -> QImage:
    img = QImage(btn.size(), QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    btn.render(img)
    return img


def _images_different(img1: QImage, img2: QImage) -> bool:
    if img1.size() != img2.size():
        return True
    for y in range(img1.height()):
        for x in range(img1.width()):
            if img1.pixelColor(x, y) != img2.pixelColor(x, y):
                return True
    return False


def test_sidebar_focus_rectangle_keyboard_only_and_mouse_dismiss():
    app = QApplication.instance() or QApplication(sys.argv)

    win = QWidget()
    layout = QVBoxLayout(win)

    other = QPushButton("Other")
    layout.addWidget(other)

    btn = SidebarButton("Install Software")
    layout.addWidget(btn)

    win.resize(200, 100)
    win.show()
    win.activateWindow()
    app.processEvents()

    # Move focus to 'other' so 'btn' is idle and unfocused
    other.setFocus()
    app.processEvents()
    assert not btn.hasFocus()
    assert not btn._keyboard_focused

    idle_img = _render_btn(btn)

    # 1. Focus via mouse click (MouseFocusReason) -> should NOT be keyboard focused
    btn.setFocus(Qt.FocusReason.MouseFocusReason)
    assert btn.hasFocus()
    assert not btn._keyboard_focused
    mouse_focused_img = _render_btn(btn)
    assert not _images_different(idle_img, mouse_focused_img)

    # 2. Focus via programmatic setFocus (OtherFocusReason) -> should NOT be keyboard focused
    other.setFocus()
    btn.setFocus(Qt.FocusReason.OtherFocusReason)
    assert btn.hasFocus()
    assert not btn._keyboard_focused

    # 3. Focus via Tab key (TabFocusReason) -> MUST be keyboard focused
    other.setFocus()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    assert btn.hasFocus()
    assert btn._keyboard_focused
    tab_focused_img = _render_btn(btn)
    # The tab selector rectangle must be drawn, producing a visual difference
    assert _images_different(idle_img, tab_focused_img)

    # 4. Mouse press event dismisses keyboard focus
    press_event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(10, 10),
        QPointF(10, 10),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    app.sendEvent(btn, press_event)
    assert not btn._keyboard_focused
    dismissed_img = _render_btn(btn)
    assert not _images_different(idle_img, dismissed_img)

    # 5. Tab again to focus, then verify mouse enter dismisses it
    other.setFocus()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    assert btn._keyboard_focused
    try:
        from PySide6.QtGui import QEnterEvent
        enter_event = QEnterEvent(QPointF(10, 10), QPointF(10, 10), QPointF(10, 10))
    except Exception:
        enter_event = QEvent(QEvent.Type.Enter)
    app.sendEvent(btn, enter_event)
    assert not btn._keyboard_focused

    # 6. Tab again to focus, then verify global dismissal filter via mouse move
    other.setFocus()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    assert btn._keyboard_focused
    move_event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(50, 50),
        QPointF(50, 50),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    app.sendEvent(win, move_event)
    assert not btn._keyboard_focused

    # 7. Tab again to focus, then verify SidebarButton.dismiss_focus_rings()
    other.setFocus()
    btn.setFocus(Qt.FocusReason.TabFocusReason)
    assert btn._keyboard_focused
    SidebarButton.dismiss_focus_rings()
    assert not btn._keyboard_focused
