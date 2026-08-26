"""Flash page — preparing / waiting-for-device / flashing views."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..flash_service import (
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_WAITING,
    STEP_WRITE,
)
from ..i18n import tr
from .widgets import Banner, Card, InfoRow, StatusTag
from .dark import T

_STEP_KEY = {
    STEP_EXTRACTING: "step_extract",
    STEP_WAITING: "step_wait",
    STEP_DETECT: "step_detect",
    STEP_DOWNLOAD_DA: "step_download_da",
    STEP_DOWNLOAD_BL: "step_download_bl",
    STEP_WRITE: "step_write",
    STEP_DONE: "step_done",
}


class FlashPage(QWidget):
    method_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_callback = None
        self._cancel_wait_callback = None
        self._model = ""
        self._wait_banner_key = ""
        self._step_key = ""
        self._prep_step_key = ""
        self._conn_value_key = ""
        self._dev_value_key = ""
        self._method_available = ("auto", "sp", "mtk")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        self._stack = QStackedWidget()
        self._preparing_view = self._build_preparing_view()
        self._waiting_view = self._build_waiting_view()
        self._flashing_view = self._build_flashing_view()
        for w in (self._preparing_view, self._waiting_view, self._flashing_view):
            self._stack.addWidget(w)
        layout.addWidget(self._stack, 1)

    def _build_preparing_view(self):
        t = T()
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._prep_banner = Banner()
        self._prep_banner.set_type("info")
        layout.addWidget(self._prep_banner)

        self._prep_img = QLabel()
        self._prep_img.setFixedSize(460, 222)
        self._prep_img.setScaledContents(True)
        self._prep_img.setAlignment(Qt.AlignCenter)
        self._load_image(self._prep_img, "presteps.png")
        layout.addWidget(self._prep_img, 0, Qt.AlignCenter)

        self._prep_card = Card("flash_progress_title")
        self._prep_progress = QProgressBar()
        self._prep_progress.setRange(0, 100)
        self._prep_progress.setFixedHeight(16)
        self._prep_card.add_widget(self._prep_progress)
        self._prep_step = QLabel("")
        self._prep_step.setAlignment(Qt.AlignCenter)
        self._prep_step.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {t.fg_dim};"
            f" border: none; background: transparent;"
        )
        self._prep_card.add_widget(self._prep_step)
        layout.addWidget(self._prep_card)
        layout.addStretch()
        return view

    def _build_waiting_view(self):
        t = T()
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._wait_banner = Banner()
        self._wait_banner.set_type("info")
        layout.addWidget(self._wait_banner)

        row = QHBoxLayout()
        row.setSpacing(20)

        self._status_img = QLabel()
        self._status_img.setFixedSize(460, 222)
        self._status_img.setScaledContents(True)
        self._status_img.setAlignment(Qt.AlignCenter)
        self._load_image(self._status_img, "initsteps.png")
        row.addWidget(self._status_img, 0, Qt.AlignTop)

        guide_box = QVBoxLayout()
        self._guide_title = QLabel(tr("flash_guide_title"))
        self._guide_title.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {t.fg};"
            f" border: none; background: transparent;"
        )
        guide_box.addWidget(self._guide_title)

        self._guide_texts = []
        for key in ("flash_guide_1", "flash_guide_2", "flash_guide_3", "flash_guide_4"):
            text = QLabel(tr(key))
            text.setStyleSheet(
                f"font-size: 13px; color: {t.fg_dim}; border: none; background: transparent;"
            )
            self._guide_texts.append((key, text))
            guide_box.addWidget(text)
        row.addLayout(guide_box, 1)
        layout.addLayout(row)

        self._warning = QLabel(tr("flash_warning"))
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(
            f"font-size: 12px; color: {t.warn_fg};"
            f" background-color: {t.warn_bg};"
            f" border-radius: 10px; padding: 10px 14px;"
        )
        layout.addWidget(self._warning)

        method_row = QHBoxLayout()
        self._method_label = QLabel(tr("flash_method"))
        self._method_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {t.fg_dim};"
            f" border: none; background: transparent;"
        )
        method_row.addWidget(self._method_label)
        self._method_combo = QComboBox()
        self._method_combo.setCursor(Qt.PointingHandCursor)
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        method_row.addWidget(self._method_combo)
        method_row.addStretch()
        layout.addLayout(method_row)

        self._method_note = QLabel("")
        self._method_note.setWordWrap(True)
        self._method_note.setStyleSheet(
            f"font-size: 11px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        layout.addWidget(self._method_note)

        self._method_revealed = False
        self._method_label.setVisible(False)
        self._method_combo.setVisible(False)
        if not paths.IS_MAC:
            self._method_note.setVisible(False)

        self._wait_status = StatusTag("idle")
        layout.addWidget(self._wait_status)

        self._wait_cancel_btn = QPushButton(tr("flash_btn_cancel_wait"))
        self._wait_cancel_btn.setProperty("cssClass", "ghost")
        self._wait_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._wait_cancel_btn.clicked.connect(self._on_cancel_wait)
        layout.addWidget(self._wait_cancel_btn, 0, Qt.AlignLeft)
        layout.addStretch()
        return view

    def _build_flashing_view(self):
        t = T()
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._flash_banner = Banner()
        self._flash_banner.set_type("info")
        layout.addWidget(self._flash_banner)

        self._flash_img = QLabel()
        self._flash_img.setFixedSize(320, 155)
        self._flash_img.setScaledContents(True)
        self._flash_img.setAlignment(Qt.AlignCenter)
        self._load_image(self._flash_img, "installing.png")
        layout.addWidget(self._flash_img, 0, Qt.AlignCenter)

        self._progress_card = Card("flash_progress_title")
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(16)
        self._progress_card.add_widget(self._progress_bar)

        self._step_label = QLabel("")
        self._step_label.setAlignment(Qt.AlignCenter)
        self._step_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {t.fg_dim};"
            f" border: none; background: transparent;"
        )
        self._progress_card.add_widget(self._step_label)

        self._action_label = QLabel("")
        self._action_label.setWordWrap(True)
        self._action_label.setStyleSheet(
            f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        self._progress_card.add_widget(self._action_label)
        layout.addWidget(self._progress_card)

        self._status_card = Card("flash_status_panel")
        self._conn_row = InfoRow("flash_conn_status")
        self._dev_row = InfoRow("flash_device_status")
        self._pkg_row = InfoRow("flash_current_pkg")
        self._elapsed_row = InfoRow("flash_elapsed")
        self._eta_row = InfoRow("flash_eta")
        for r in (self._conn_row, self._dev_row, self._pkg_row, self._elapsed_row, self._eta_row):
            self._status_card.add_widget(r)
        layout.addWidget(self._status_card)

        btn_row = QHBoxLayout()
        self._cancel_btn = QPushButton(tr("flash_btn_cancel"))
        self._cancel_btn.setProperty("cssClass", "ghost")
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return view

    def _load_image(self, label, name):
        for base in (paths.RESOURCES_DIR, paths.REPO_ROOT / "assets"):
            p = base / name
            if p.exists():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    label.setPixmap(pm)
                    return

    def show_preparing(self):
        self._stack.setCurrentWidget(self._preparing_view)
        self._prep_banner.set_key("flash_preparing")
        self._prep_step_key = "step_extract"
        self._prep_step.setText(tr("step_extract"))
        self._load_image(self._prep_img, "presteps.png")

    def show_waiting(self):
        self._stack.setCurrentWidget(self._waiting_view)
        self._method_combo.setEnabled(True)

    def show_flashing(self):
        self._stack.setCurrentWidget(self._flashing_view)
        self._method_combo.setEnabled(False)
        self._load_image(self._flash_img, "installing.png")

    def set_method(self, method, available=("auto", "sp", "mtk")):
        self._method_available = tuple(available)
        self._method_combo.blockSignals(True)
        self._method_combo.clear()
        labels = {
            "auto": tr("flash_method_auto"),
            "sp": tr("flash_method_sp"),
            "mtk": tr("flash_method_mtk"),
        }
        for m in self._method_available:
            if m in labels:
                self._method_combo.addItem(labels[m], m)
        idx = self._method_combo.findData(method)
        self._method_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._method_combo.blockSignals(False)
        if not self._method_revealed:
            self._method_combo.setVisible(False)
            self._method_label.setVisible(False)
            if not paths.IS_MAC:
                self._method_note.setVisible(False)
        self._update_method_note()

    def _initsteps_image(self):
        method = self.current_method()
        if method == "sp":
            return "initsteps_sp.png"
        if method == "mtk":
            return "initsteps_win.png" if paths.IS_WINDOWS else "initsteps.png"
        if paths.IS_MAC:
            return "initsteps.png"
        return "initsteps_sp.png"

    def _on_method_changed(self):
        self._update_method_note()
        if self._stack.currentWidget() is self._waiting_view:
            self._load_image(self._status_img, self._initsteps_image())
        self.method_changed.emit(self.current_method())

    def current_method(self):
        return self._method_combo.currentData() or "auto"

    def _update_method_note(self):
        method = self.current_method()
        if method == "sp":
            self._method_note.setText(tr("flash_method_note_sp"))
        elif method == "mtk":
            self._method_note.setText(tr("flash_method_note_mtk"))
        else:
            self._method_note.setText(tr("flash_method_note_auto"))

    def reveal_method_selector(self):
        if paths.IS_MAC:
            return
        if self._method_revealed:
            return
        self._method_revealed = True
        self._method_label.setVisible(True)
        self._method_combo.setVisible(True)
        self._method_note.setVisible(True)
        self._update_method_note()

    def set_model(self, model):
        self._model = (model or "").strip() or ""

    def set_package_name(self, name):
        self._pkg_row.set_value(name or "\u2014")

    def _connect_model_text(self):
        return self._model if self._model else tr("flash_model_generic")

    def set_waiting_device(self):
        self._wait_status.set_status("idle")
        self._wait_banner.set_type("info")
        self._wait_banner_key = "connect"
        self._wait_banner.set_key("flash_connect_prompt", model=self._connect_model_text())
        self._conn_value_key = "flash_conn_waiting"
        self._conn_row.set_value(tr("flash_conn_waiting"))
        self._load_image(self._status_img, self._initsteps_image())

    def set_searching(self):
        self._wait_banner.set_type("info")
        self._wait_banner_key = "searching"
        self._wait_status.set_status("idle")
        self._load_image(self._status_img, self._initsteps_image())

    def set_detected(self):
        self._wait_status.set_status("connected")
        self._wait_banner.set_type("success")
        self._wait_banner_key = "ready"
        self._wait_banner.set_key("flash_banner_ready")
        self._load_image(self._status_img, "please_wait.png")

    def set_device_flashing(self):
        self._wait_status.set_status("flashing")
        self._flash_banner.set_type("info")
        self._flash_banner.set_key("flash_banner_flashing")
        self._conn_value_key = "flash_conn_connected"
        self._dev_value_key = "status_connected"
        self._conn_row.set_value(tr("flash_conn_connected"))
        self._dev_row.set_value(tr("status_connected"))
        self._load_image(self._status_img, "installing.png")

    def set_device_done(self):
        self._load_image(self._status_img, "installed.png")
        self._load_image(self._flash_img, "installed.png")

    def update_prep_progress(self, percent):
        self._prep_progress.setValue(percent)

    def update_progress(self, percent):
        self._progress_bar.setValue(percent)

    def update_step(self, step_key):
        self._step_key = step_key
        self._step_label.setText(tr(step_key))

    def update_action(self, text):
        self._action_label.setText(text)

    def update_time(self, elapsed, eta):
        self._elapsed_row.set_value(elapsed)
        self._eta_row.set_value(eta)

    def retranslate(self):
        self._prep_banner.retranslate()
        if self._wait_banner_key == "connect":
            self._wait_banner.set_key("flash_connect_prompt", model=self._connect_model_text())
        else:
            self._wait_banner.retranslate()
        self._flash_banner.retranslate()
        self._guide_title.setText(tr("flash_guide_title"))
        for key, lbl in self._guide_texts:
            lbl.setText(tr(key))
        self._warning.setText(tr("flash_warning"))
        self._method_label.setText(tr("flash_method"))
        self._method_note.setText(self._method_note_text())
        if self._prep_step_key:
            self._prep_step.setText(tr(self._prep_step_key))
        if self._step_key:
            self._step_label.setText(tr(self._step_key))
        if self._conn_value_key:
            self._conn_row.set_value(tr(self._conn_value_key))
        if self._dev_value_key:
            self._dev_row.set_value(tr(self._dev_value_key))
        self._wait_cancel_btn.setText(tr("flash_btn_cancel_wait"))
        self._cancel_btn.setText(tr("flash_btn_cancel"))
        self._wait_status.retranslate()
        self._prep_card.retranslate()
        self._progress_card.retranslate()
        self._status_card.retranslate()
        self._conn_row.retranslate()
        self._dev_row.retranslate()
        self._pkg_row.retranslate()
        self._elapsed_row.retranslate()
        self._eta_row.retranslate()
        current = self.current_method()
        self._method_combo.blockSignals(True)
        self._method_combo.clear()
        labels = {
            "auto": tr("flash_method_auto"),
            "sp": tr("flash_method_sp"),
            "mtk": tr("flash_method_mtk"),
        }
        for m in self._method_available:
            if m in labels:
                self._method_combo.addItem(labels[m], m)
        self._method_combo.setCurrentIndex(
            self._method_combo.findData(current) if self._method_combo.findData(current) >= 0 else 0
        )
        self._method_combo.blockSignals(False)

    def _method_note_text(self):
        method = self.current_method()
        if method == "sp":
            return tr("flash_method_note_sp")
        if method == "mtk":
            return tr("flash_method_note_mtk")
        return tr("flash_method_note_auto")

    def on_cancel(self, callback):
        self._cancel_callback = callback

    def on_cancel_wait(self, callback):
        self._cancel_wait_callback = callback

    def _on_cancel(self):
        if self._cancel_callback:
            self._cancel_callback()

    def _on_cancel_wait(self):
        if self._cancel_wait_callback:
            self._cancel_wait_callback()
