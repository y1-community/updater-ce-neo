"""Error page — device connection, USB mid-flash disconnect, flash failure
(port of the Chin ``app.ui.page_error``)."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .widgets import Banner, Card, InfoRow


class ErrorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._retry_cb = None
        self._reconnect_cb = None
        self._reselect_cb = None
        self._log_cb = None
        self._mode = ""
        self._mode_args = {}
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self._banner = Banner()
        layout.addWidget(self._banner)

        self._detail_card = Card("")
        self._title = QLabel("")
        self._title.setStyleSheet("font-size: 16px; font-weight: 700; color: #111827;")
        self._detail_card.add_widget(self._title)
        self._error_row = InfoRow("err_error_code")
        self._step_row = InfoRow("err_failed_at")
        self._retry_row = InfoRow("err_retry_count")
        self._detail_card.add_widget(self._error_row)
        self._detail_card.add_widget(self._step_row)
        self._detail_card.add_widget(self._retry_row)
        layout.addWidget(self._detail_card)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("font-size: 13px; color: #6b7280;")
        layout.addWidget(self._hint)

        btn_row = QHBoxLayout()
        self._retry_btn = QPushButton(tr("err_btn_retry"))
        self._reconnect_btn = QPushButton(tr("err_btn_reconnect"))
        self._reselect_btn = QPushButton(tr("err_btn_reselect"))
        self._log_btn = QPushButton(tr("err_view_log"))
        self._retry_btn.clicked.connect(lambda: self._retry_cb and self._retry_cb())
        self._reconnect_btn.clicked.connect(lambda: self._reconnect_cb and self._reconnect_cb())
        self._reselect_btn.clicked.connect(lambda: self._reselect_cb and self._reselect_cb())
        self._log_btn.clicked.connect(lambda: self._log_cb and self._log_cb())
        for b in (self._retry_btn, self._reconnect_btn, self._reselect_btn, self._log_btn):
            b.setCursor(b.cursor())
        btn_row.addWidget(self._retry_btn)
        btn_row.addWidget(self._reconnect_btn)
        btn_row.addWidget(self._reselect_btn)
        btn_row.addWidget(self._log_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

    # -- hooks ---------------------------------------------------------------
    def on_retry(self, cb):
        self._retry_cb = cb

    def on_reconnect(self, cb):
        self._reconnect_cb = cb

    def on_reselect(self, cb):
        self._reselect_cb = cb

    def on_view_log(self, cb):
        self._log_cb = cb

    # -- view modes -----------------------------------------------------------
    def _render(self):
        mode = self._mode
        if mode == "device":
            self._banner.set_type("danger")
            self._banner.set_key("err_device_title")
            self._title.setText(tr("err_device_title"))
            self._error_row.set_value("NO_DEVICE")
            self._step_row.set_value("—")
            self._retry_row.set_value("0")
            self._hint.setText(tr("flash_wait_desc"))
        elif mode == "usb":
            self._banner.set_type("danger")
            self._banner.set_key("err_usb_title")
            self._title.setText(tr("err_usb_title"))
            self._error_row.set_value("USB_DISCONNECTED")
            self._step_row.set_value(f"{self._mode_args.get('percent', 0)}%")
            self._retry_row.set_value("—")
            self._hint.setText(tr("flash_warning"))
        elif mode == "failed":
            self._banner.set_type("danger")
            self._banner.set_key("flash_failed")
            self._title.setText(tr("err_flash_title"))
            self._error_row.set_value(self._mode_args.get("error_code") or "—")
            step = self._mode_args.get("step") or "—"
            percent = self._mode_args.get("percent", 0)
            self._step_row.set_value(f"{step} @ {percent}%")
            self._retry_row.set_value(str(self._mode_args.get("retry_count", 0)))
            self._hint.setText(
                f"{tr('flash_current_pkg')}: {self._mode_args.get('package_name') or '—'}"
            )
        else:
            self._banner.set_type("info")
            self._banner.setText("")

    def retranslate(self):
        self._banner.retranslate()
        self._render()
        self._retry_btn.setText(tr("err_btn_retry"))
        self._reconnect_btn.setText(tr("err_btn_reconnect"))
        self._reselect_btn.setText(tr("err_btn_reselect"))
        self._log_btn.setText(tr("err_view_log"))
        self._error_row.retranslate()
        self._step_row.retranslate()
        self._retry_row.retranslate()

    def show_device_error(self):
        self._mode = "device"
        self._mode_args = {}
        self._render()

    def show_usb_disconnected(self, percent):
        self._mode = "usb"
        self._mode_args = {"percent": percent}
        self._render()

    def show_flash_failed(self, percent, step, error_code, package_name, retry_count):
        self._mode = "failed"
        self._mode_args = {
            "percent": percent,
            "step": step,
            "error_code": error_code,
            "package_name": package_name,
            "retry_count": retry_count,
        }
        self._render()
