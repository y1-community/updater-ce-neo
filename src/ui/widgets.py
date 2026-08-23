"""Reusable widgets — port of InniUpdaterChin's ``app.ui.widgets``
(StatusTag, StepIndicator, InfoRow, Card, Banner)."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr

_STATUS_COLORS = {
    "idle": ("#9ca3af", "#f3f4f6"),
    "selected": ("#2563eb", "#dbeafe"),
    "connected": ("#059669", "#d1fae5"),
    "disconnected": ("#dc2626", "#fee2e2"),
    "flashing": ("#d97706", "#fef3c7"),
    "complete": ("#059669", "#d1fae5"),
    "failed": ("#dc2626", "#fee2e2"),
    "retrying": ("#d97706", "#fef3c7"),
}


class StatusTag(QLabel):
    def __init__(self, status="idle", parent=None):
        super().__init__(parent)
        self._status = status
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(24)
        self.setMinimumWidth(72)
        self._apply()

    def set_status(self, status):
        self._status = status
        self._apply()

    def retranslate(self):
        self._apply()

    def _apply(self):
        fg, bg = _STATUS_COLORS.get(self._status, _STATUS_COLORS["idle"])
        self.setText(tr(f"status_{self._status}"))
        self.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg}; border-radius: 12px;"
            f" font-size: 11px; font-weight: 600; padding: 2px 10px; }}"
        )


class StepIndicator(QWidget):
    """Horizontal step dots/labels for the S1–S6 flow."""

    _STEP_KEYS = ["home_step_1", "home_step_2", "home_step_3", "home_step_4", "home_step_5", "home_step_6"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = []
        self._build_ui()
        self.set_active_step(0)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        for i in range(6):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumWidth(64)
            layout.addWidget(lbl, 1)
            self._labels.append(lbl)
        self.setFixedHeight(32)

    def set_active_step(self, index):
        self._active = index
        for i, lbl in enumerate(self._labels):
            text = self._STEP_KEYS[i] if i < len(self._STEP_KEYS) else ""
            if i < index:
                lbl.setText(f"✓ {tr(text)}")
                lbl.setStyleSheet("color: #059669; font-size: 11px; font-weight: 600;")
            elif i == index:
                lbl.setText(f"● {tr(text)}")
                lbl.setStyleSheet("color: #2563eb; font-size: 11px; font-weight: 700;")
            else:
                lbl.setText(f"○ {tr(text)}")
                lbl.setStyleSheet("color: #9ca3af; font-size: 11px;")

    def retranslate(self):
        self.set_active_step(getattr(self, "_active", 0))


class InfoRow(QWidget):
    def __init__(self, label_key="", parent=None):
        super().__init__(parent)
        self._label_key = ""
        self._label = QLabel()
        self._value = QLabel()
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)
        self._label.setStyleSheet("color: #6b7280; font-size: 12px;")
        self._value.setStyleSheet("color: #111827; font-size: 12px; font-weight: 600;")
        if label_key:
            self.set_label(label_key)

    def set_label(self, key):
        self._label_key = key
        self._label.setText(tr(key))

    def retranslate(self):
        if self._label_key:
            self._label.setText(tr(self._label_key))

    def set_value(self, value):
        self._value.setText(str(value))

    def value(self):
        return self._value.text()


class Card(QFrame):
    """Rounded panel with an optional title."""

    def __init__(self, title_key="", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._title_key = title_key
        self._title = None
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(16, 14, 16, 14)
        self._outer.setSpacing(8)
        if title_key:
            self._title = QLabel(tr(title_key))
            self._title.setStyleSheet("font-size: 13px; font-weight: 700; color: #111827;")
            self._outer.addWidget(self._title)

    def retranslate(self):
        if self._title is not None and self._title_key:
            self._title.setText(tr(self._title_key))

    def set_layout(self, layout):
        self._outer.addLayout(layout)

    def add_widget(self, widget):
        self._outer.addWidget(widget)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1), 10, 10)
        painter.fillPath(path, QColor("#ffffff"))
        painter.setPen(QColor("#e5e7eb"))
        painter.drawPath(path)


class Banner(QLabel):
    """Status banner (info / success / warning / danger).

    ``set_key`` marks the text as translatable; ``retranslate`` re-applies it
    so language switches don't lose or stale the message.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(40)
        self._key = ""
        self._fmt = {}
        self.set_type("info")

    def set_key(self, key, **fmt):
        """Set text from an i18n key (optionally formatted), remembering it."""
        self._key = key
        self._fmt = fmt
        self.setText(tr(key).format(**fmt) if fmt else tr(key))

    def retranslate(self):
        if self._key:
            self.setText(tr(self._key).format(**self._fmt) if self._fmt else tr(self._key))

    def set_type(self, banner_type):
        styles = {
            "info": ("#eff6ff", "#1e40af"),
            "success": ("#d1fae5", "#065f46"),
            "warning": ("#fef3c7", "#92400e"),
            "danger": ("#fee2e2", "#991b1b"),
        }
        bg, fg = styles.get(banner_type, styles["info"])
        self.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg}; border-radius: 8px;"
            f" font-size: 12px; padding: 10px 14px; }}"
        )
