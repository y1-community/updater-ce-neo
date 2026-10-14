"""Application-wide keyboard focus and tab highlight manager.

Ensures focus rings / tab indicators are:
1. Rendered matching the widget's geometry (e.g. circular for round chips).
2. Dismissed immediately whenever pointer / mouse input occurs.
3. Automatically dismissed if Tab has not been pressed in the last 2 seconds.
"""

from __future__ import annotations

import time
import weakref

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtWidgets import QWidget


class TabFocusManager(QObject):
    """Monitors application events for keyboard navigation (Tab) vs mouse activity."""

    _instance: TabFocusManager | None = None
    _tab_active: bool = False
    _last_tab_time: float = 0.0
    _timer: QTimer | None = None
    _active_widgets: weakref.WeakSet[QWidget] = weakref.WeakSet()
    _last_mouse_pos: QPoint | None = None

    @classmethod
    def ensure_installed(cls) -> None:
        if cls._instance is None:
            app = QCoreApplication.instance()
            if app is not None:
                cls._instance = cls()
                cls._timer = QTimer(cls._instance)
                cls._timer.setSingleShot(True)
                cls._timer.setInterval(2000)
                cls._timer.timeout.connect(cls._on_timeout)
                try:
                    app.installEventFilter(cls._instance)
                except Exception:
                    pass

    @classmethod
    def is_tab_active(cls) -> bool:
        if not cls._tab_active:
            return False
        if time.monotonic() - cls._last_tab_time > 2.0:
            cls._tab_active = False
            return False
        return True

    @classmethod
    def register_focused_widget(cls, widget: QWidget) -> None:
        cls._active_widgets.add(widget)
        cls.ensure_installed()

    @classmethod
    def unregister_focused_widget(cls, widget: QWidget) -> None:
        cls._active_widgets.discard(widget)

    @classmethod
    def dismiss_all(cls) -> None:
        cls._tab_active = False
        if cls._timer is not None and cls._timer.isActive():
            cls._timer.stop()
        if cls._active_widgets:
            widgets = list(cls._active_widgets)
            cls._active_widgets.clear()
            for w in widgets:
                if hasattr(w, "dismiss_keyboard_focus"):
                    w.dismiss_keyboard_focus()
                elif hasattr(w, "_keyboard_focused"):
                    w._keyboard_focused = False
                    w.update()

    @classmethod
    def _on_timeout(cls) -> None:
        cls.dismiss_all()

    def eventFilter(self, watched, event):
        etype = event.type()
        if etype == QEvent.Type.KeyPress:
            key = getattr(event, "key", lambda: None)()
            if key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
                TabFocusManager._last_tab_time = time.monotonic()
                TabFocusManager._tab_active = True
                if self._timer is not None:
                    self._timer.start(2000)
        elif etype in (
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.Wheel,
        ):
            self.dismiss_all()
        elif etype == QEvent.Type.MouseMove:
            pos = getattr(event, "globalPosition", None)
            if pos is not None:
                pt = pos().toPoint()
            else:
                pt = getattr(event, "globalPos", lambda: None)()
            if pt is not None:
                if self._last_mouse_pos is not None and pt == self._last_mouse_pos:
                    return False
                self._last_mouse_pos = pt
            self.dismiss_all()
        return False
