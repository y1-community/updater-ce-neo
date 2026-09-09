"""Reusable widgets — port of InniUpdaterChin's ``app.ui.widgets``

Widgets use CSS class properties for static styling (handled by the global
QSS) and ``dark.T()`` colour tokens only for dynamically computed styles
(status tag colours, banner variants, etc.).
"""

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
from .dark import T


class StatusTag(QLabel):
    """Coloured pill indicating the current flash-phase status."""

    _COLORS = {
        "idle":         "status_idle",
        "selected":     "status_selected",
        "connected":    "status_connected",
        "disconnected": "status_disconnected",
        "flashing":     "status_flashing",
        "complete":     "status_complete",
        "failed":       "status_failed",
        "retrying":     "status_retrying",
    }

    def __init__(self, status="idle", parent=None):
        super().__init__(parent)
        self._status = status
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(28)
        self.setMinimumWidth(84)
        self._apply()

    def set_status(self, status):
        self._status = status
        self._apply()

    def retranslate(self):
        self._apply()

    def _apply(self):
        t = T()
        key = self._COLORS.get(self._status, "status_idle")
        fg, bg = getattr(t, key)
        self.setText(tr(f"status_{self._status}"))
        self.setStyleSheet(
            f"background-color: {bg}; color: {fg};"
            f" border-radius: 14px; font-size: 11px; font-weight: 700;"
            f" padding: 4px 14px; letter-spacing: 0.02em;"
        )


class StepIndicator(QWidget):
    """Horizontal step dots/labels for the S1-S6 flow."""

    _STEP_KEYS = [
        "home_step_1", "home_step_2", "home_step_3",
        "home_step_4", "home_step_5", "home_step_6",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = []
        self._build_ui()
        self.set_active_step(0)

    def _build_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for _ in range(6):
            lbl = QLabel()
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setMinimumWidth(68)
            layout.addWidget(lbl, 1)
            self._labels.append(lbl)
        self.setFixedHeight(34)

    def set_active_step(self, index):
        self._active = index
        t = T()
        for i, lbl in enumerate(self._labels):
            text = self._STEP_KEYS[i] if i < len(self._STEP_KEYS) else ""
            if i < index:
                lbl.setText(f"&#10003; {tr(text)}")
                lbl.setStyleSheet(f"color: {t.ok_fg}; font-size: 11px; font-weight: 700;")
            elif i == index:
                lbl.setText(f"&#9679; {tr(text)}")
                lbl.setStyleSheet(f"color: {t.fg_primary}; font-size: 11px; font-weight: 700;")
            else:
                lbl.setText(f"&#9675; {tr(text)}")
                lbl.setStyleSheet(f"color: {t.fg_dim}; font-size: 11px; font-weight: 500;")

    def retranslate(self):
        self.set_active_step(getattr(self, "_active", 0))


class InfoRow(QWidget):
    """Key-value row: label on the left, value on the right."""

    def __init__(self, label_key="", parent=None):
        super().__init__(parent)
        self._label_key = ""
        self._label = QLabel()
        self._label.setProperty("cssClass", "dimmed")
        self._value = QLabel()
        self._value.setProperty("cssClass", "infoValue")
        self._value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.addWidget(self._label)
        layout.addWidget(self._value, 1)
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
    """Rounded panel with an optional title, styled via QSS."""

    def __init__(self, title_key="", parent=None):
        super().__init__(parent)
        self.setProperty("cssClass", "card")
        self._title_key = title_key
        self._title = None
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(18, 14, 18, 14)
        self._outer.setSpacing(8)
        if title_key:
            self._title = QLabel(tr(title_key))
            self._title.setProperty("cssClass", "cardTitle")
            self._outer.addWidget(self._title)

    def retranslate(self):
        if self._title is not None and self._title_key:
            self._title.setText(tr(self._title_key))

    def set_layout(self, layout):
        self._outer.addLayout(layout)

    def add_widget(self, widget):
        self._outer.addWidget(widget)


class Banner(QLabel):
    """Status banner (info / success / warning / danger).

    Uses CSS class for base styling; colours computed from tokens.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(42)
        self._key = ""
        self._fmt = {}
        self.set_type("info")

    def set_key(self, key, **fmt):
        self._key = key
        self._fmt = fmt
        self.setText(tr(key).format(**fmt) if fmt else tr(key))

    def retranslate(self):
        if self._key:
            self.setText(tr(self._key).format(**self._fmt) if self._fmt else tr(self._key))

    def set_type(self, banner_type):
        t = T()
        colors = {
            "info":    (t.info_bg, t.info_fg),
            "success": (t.ok_bg, t.ok_fg),
            "warning": (t.warn_bg, t.warn_fg),
            "danger":  (t.danger_bg, t.danger_fg),
        }
        bg, fg = colors.get(banner_type, colors["info"])
        self.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 10px;"
            f" font-size: 13px; padding: 10px 16px; font-weight: 500;"
        )
