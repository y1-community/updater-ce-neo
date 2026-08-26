"""Retry page."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .widgets import Card, InfoRow
from .dark import T


class RetryPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_cb = None
        self._retry_count = 0
        self._conn_key = ""
        self._step_key = ""
        self._build_ui()

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self._title = QLabel(tr("retry_title"))
        self._title.setStyleSheet(
            f"font-size: 20px; font-weight: 800; color: {t.fg};"
            f" letter-spacing: -0.02em; border: none; background: transparent;"
        )
        layout.addWidget(self._title)

        self._card = Card("retry_info_title")
        self._pkg_row = InfoRow("flash_current_pkg")
        self._count_row = InfoRow("err_retry_count")
        self._conn_row = InfoRow("flash_conn_status")
        self._card.add_widget(self._pkg_row)
        self._card.add_widget(self._count_row)
        self._card.add_widget(self._conn_row)
        layout.addWidget(self._card)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setFixedHeight(14)
        layout.addWidget(self._progress)

        self._step_label = QLabel("")
        self._step_label.setAlignment(Qt.AlignCenter)
        self._step_label.setStyleSheet(
            f"font-size: 13px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        layout.addWidget(self._step_label)

        self._cancel_btn = QPushButton(tr("retry_btn_cancel"))
        self._cancel_btn.setProperty("cssClass", "ghost")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(lambda: self._cancel_cb and self._cancel_cb())
        layout.addWidget(self._cancel_btn)
        layout.addStretch()

    def update_info(self, package_name, retry_count, conn_status_key):
        self._pkg_row.set_value(package_name or "\u2014")
        self._retry_count = retry_count
        self._conn_key = conn_status_key
        self._count_row.set_value(tr("retry_count_fmt").format(n=retry_count))
        self._conn_row.set_value(tr(conn_status_key) if conn_status_key else "\u2014")

    def update_progress(self, percent):
        self._progress.setValue(percent)

    def update_step(self, step_key):
        self._step_key = step_key
        self._step_label.setText(tr(step_key))

    def retranslate(self):
        self._title.setText(tr("retry_title"))
        self._card.retranslate()
        self._pkg_row.retranslate()
        self._count_row.retranslate()
        self._conn_row.retranslate()
        self._cancel_btn.setText(tr("retry_btn_cancel"))
        if self._retry_count:
            self._count_row.set_value(tr("retry_count_fmt").format(n=self._retry_count))
        if self._conn_key:
            self._conn_row.set_value(tr(self._conn_key))
        if self._step_key:
            self._step_label.setText(tr(self._step_key))

    def on_cancel(self, cb):
        self._cancel_cb = cb
