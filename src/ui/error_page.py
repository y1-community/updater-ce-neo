"""Error page — device connection, USB mid-flash disconnect, flash failure."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .flash_page import _STEP_KEY
from .widgets import Banner, Card, InfoRow
from .dark import T, page_top_margin

# FlashWorker.finished error codes shown in the "Error Code" row. Known codes
# get a translated label (the raw code stays visible in parentheses for
# support); unknown codes / raw exception text are shown as-is.
_ERROR_CODE_KEYS = {
    "USER_CANCELLED": "err_code_user_cancelled",
    "CONNECTION_FAILED": "err_code_connection_failed",
    "MTK_INIT_FAILED": "err_code_mtk_init_failed",
    "MTK_IMPORT_FAILED": "err_code_mtk_import_failed",
    "SP_FLASH_TOOL_NOT_FOUND": "err_code_sp_not_found",
    "NO_SCATTER_FILE": "err_code_no_scatter",
    "MISSING_IMAGES": "err_code_missing_images",
    "INTERNAL_ERROR": "err_code_internal",
    "NO_DEVICE": "flash_no_device",
    "USB_DISCONNECTED": "err_usb_title",
}


def _format_error_code(code) -> str:
    """Human-readable error code for the error page (keeps the raw code)."""
    code = str(code or "\u2014")
    key = _ERROR_CODE_KEYS.get(code)
    if key:
        return f"{tr(key)} ({code})"
    return code


class ErrorPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._retry_cb = None
        self._reconnect_cb = None
        self._reselect_cb = None
        self._log_cb = None
        self._sp_gui_cb = None
        self._mode = ""
        self._mode_args = {}
        self._build_ui()

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, page_top_margin(), 24, 20)
        layout.setSpacing(14)

        self._banner = Banner()
        layout.addWidget(self._banner)

        self._detail_card = Card("")
        self._title = QLabel("")
        self._title.setProperty("cssClass", "sectionTitle")
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
        self._hint.setProperty("cssClass", "hint")
        layout.addWidget(self._hint)

        btn_row = QHBoxLayout()
        self._retry_btn = QPushButton(tr("err_btn_retry"))
        self._retry_btn.setProperty("cssClass", "primary")
        self._retry_btn.setDefault(True)
        self._reconnect_btn = QPushButton(tr("err_btn_reconnect"))
        self._reconnect_btn.setProperty("cssClass", "primary")
        self._reconnect_btn.setDefault(True)
        self._reselect_btn = QPushButton(tr("err_btn_reselect"))
        self._reselect_btn.setProperty("cssClass", "ghost")
        self._log_btn = QPushButton(tr("err_view_log"))
        self._log_btn.setProperty("cssClass", "ghost")
        self._retry_btn.clicked.connect(lambda: self._retry_cb and self._retry_cb())
        self._reconnect_btn.clicked.connect(lambda: self._reconnect_cb and self._reconnect_cb())
        self._reselect_btn.clicked.connect(lambda: self._reselect_cb and self._reselect_cb())
        self._log_btn.clicked.connect(lambda: self._log_cb and self._log_cb())
        btn_row.addWidget(self._retry_btn)
        btn_row.addWidget(self._reconnect_btn)
        btn_row.addWidget(self._reselect_btn)
        btn_row.addWidget(self._log_btn)

        from ..sp_flash_gui import is_sp_flash_gui_supported
        if is_sp_flash_gui_supported():
            self._sp_gui_btn = QPushButton(tr("flash_btn_open_sp_gui"))
            self._sp_gui_btn.setProperty("cssClass", "ghost")
            self._sp_gui_btn.clicked.connect(lambda: self._sp_gui_cb and self._sp_gui_cb())
            btn_row.addWidget(self._sp_gui_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()

    def on_retry(self, cb):
        self._retry_cb = cb

    def on_reconnect(self, cb):
        self._reconnect_cb = cb

    def on_reselect(self, cb):
        self._reselect_cb = cb

    def on_view_log(self, cb):
        self._log_cb = cb

    def on_open_sp_gui(self, cb):
        self._sp_gui_cb = cb

    def _render(self):
        mode = self._mode
        if mode == "device":
            self._banner.set_type("danger")
            self._banner.set_key("err_device_title")
            self._title.setText(tr("err_device_title"))
            self._error_row.set_value(_format_error_code("NO_DEVICE"))
            self._step_row.set_value("\u2014")
            self._retry_row.set_value("0")
            self._hint.setText(tr("flash_wait_desc"))
        elif mode == "usb":
            self._banner.set_type("danger")
            self._banner.set_key("err_usb_title")
            self._title.setText(tr("err_usb_title"))
            self._error_row.set_value(_format_error_code("USB_DISCONNECTED"))
            self._step_row.set_value(f"{self._mode_args.get('percent', 0)}%")
            self._retry_row.set_value("\u2014")
            self._hint.setText(tr("flash_warning"))
        elif mode == "failed":
            self._banner.set_type("danger")
            self._banner.set_key("flash_failed")
            self._title.setText(tr("err_flash_title"))
            self._error_row.set_value(_format_error_code(self._mode_args.get("error_code")))
            step = self._mode_args.get("step") or "\u2014"
            percent = self._mode_args.get("percent", 0)
            step_label = tr(_STEP_KEY[step]) if step in _STEP_KEY else str(step)
            self._step_row.set_value(f"{step_label} @ {percent}%")
            self._retry_row.set_value(str(self._mode_args.get("retry_count", 0)))
            ndash = '\u2014'
            self._hint.setText(
                f"{tr('flash_current_pkg')}: {self._mode_args.get('package_name') or ndash}"
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
        if hasattr(self, "_sp_gui_btn"):
            self._sp_gui_btn.setText(tr("flash_btn_open_sp_gui"))
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
