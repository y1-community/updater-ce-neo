"""Flash page — preparing / waiting-for-device / flashing views.

- ``preparing``: firmware package is being readied; keep the device unplugged.
- ``waiting``: the backend is searching USB — CE-style image guidance tells the
  user to power off their {model} and connect the cable.
- ``flashing``: InniUpdaterChin-style progress display (bar, steps, elapsed/ETA).

The page supports in-place ``retranslate()`` so a language switch mid-activity
updates the visible strings without rebuilding or navigating away.
"""

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
    """Emitted when the user switches the flash backend while waiting."""

    method_changed = Signal(str)  # "auto" | "sp" | "mtk"

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

    # ------------------------------------------------------------------ UI
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

    # -- preparing view ------------------------------------------------------
    def _build_preparing_view(self):
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
        self._prep_progress.setStyleSheet(
            "QProgressBar { border: 1px solid #e5e7eb; border-radius: 8px;"
            " background-color: #f3f4f6; text-align: center; }"
            "QProgressBar::chunk { background-color: #2563eb; border-radius: 8px; }"
        )
        self._prep_card.add_widget(self._prep_progress)
        self._prep_step = QLabel("")
        self._prep_step.setAlignment(Qt.AlignCenter)
        self._prep_step.setStyleSheet("font-size: 13px; font-weight: 600; color: #374151;")
        self._prep_card.add_widget(self._prep_step)
        layout.addWidget(self._prep_card)
        layout.addStretch()
        return view

    # -- waiting view (CE-style guidance) ------------------------------------
    def _build_waiting_view(self):
        view = QWidget()
        layout = QVBoxLayout(view)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self._wait_banner = Banner()
        self._wait_banner.set_type("info")
        layout.addWidget(self._wait_banner)

        row = QHBoxLayout()
        row.setSpacing(20)

        # Status image — changes with the flash phase:
        #   waiting  → sleeping.png  (power off your device)
        #   detected → ready.png     (device found)
        #   flashing → installing.png (writing firmware)
        self._status_img = QLabel()
        self._status_img.setFixedSize(460, 222)
        self._status_img.setScaledContents(True)
        self._status_img.setAlignment(Qt.AlignCenter)
        # Default guide; the method-aware variant is applied by
        # set_waiting_device()/set_searching() once the combo exists.
        self._load_image(self._status_img, "initsteps.png")
        row.addWidget(self._status_img, 0, Qt.AlignTop)

        guide_box = QVBoxLayout()
        self._guide_title = QLabel(tr("flash_guide_title"))
        self._guide_title.setStyleSheet("font-size: 15px; font-weight: 700; color: #111827;")
        guide_box.addWidget(self._guide_title)

        self._guide_texts = []
        for key in ("flash_guide_1", "flash_guide_2", "flash_guide_3", "flash_guide_4"):
            text = QLabel(tr(key))
            text.setStyleSheet("font-size: 13px; color: #374151;")
            self._guide_texts.append((key, text))
            guide_box.addWidget(text)
        row.addLayout(guide_box, 1)
        layout.addLayout(row)

        self._warning = QLabel(tr("flash_warning"))
        self._warning.setWordWrap(True)
        self._warning.setStyleSheet(
            "font-size: 12px; color: #92400e; background-color: #fef3c7;"
            " border-radius: 8px; padding: 10px 14px;"
        )
        layout.addWidget(self._warning)

        # Flash backend method selector (Auto / SP Flash Tool / MTKClient).
        # Changing it while the backend is still searching restarts the
        # search with the same package.
        method_row = QHBoxLayout()
        self._method_label = QLabel(tr("flash_method"))
        self._method_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #374151;")
        method_row.addWidget(self._method_label)
        self._method_combo = QComboBox()
        self._method_combo.setCursor(Qt.PointingHandCursor)
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        method_row.addWidget(self._method_combo)
        method_row.addStretch()
        layout.addLayout(method_row)

        self._method_note = QLabel("")
        self._method_note.setWordWrap(True)
        self._method_note.setStyleSheet("font-size: 11px; color: #9ca3af;")
        layout.addWidget(self._method_note)

        # Hide the method picker by default on Windows/Linux; it only
        # appears after the user presses M. On macOS there is only one
        # backend (MTKClient) so the combo stays hidden permanently.
        self._method_revealed = False
        self._method_label.setVisible(False)
        self._method_combo.setVisible(False)
        if not paths.IS_MAC:
            self._method_note.setVisible(False)

        self._wait_status = StatusTag("idle")
        layout.addWidget(self._wait_status)

        self._wait_cancel_btn = QPushButton(tr("flash_btn_cancel_wait"))
        self._wait_cancel_btn.setCursor(Qt.PointingHandCursor)
        self._wait_cancel_btn.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; font-weight: 600;"
            " font-size: 13px; border-radius: 8px; border: none; padding: 8px 18px; }"
            "QPushButton:hover { background-color: #4b5563; }"
        )
        self._wait_cancel_btn.clicked.connect(self._on_cancel_wait)
        layout.addWidget(self._wait_cancel_btn, 0, Qt.AlignLeft)
        layout.addStretch()
        return view

    # -- flashing view (Chin-style progress) ---------------------------------
    def _build_flashing_view(self):
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
        self._progress_bar.setStyleSheet(
            "QProgressBar { border: 1px solid #e5e7eb; border-radius: 8px;"
            " background-color: #f3f4f6; text-align: center; }"
            "QProgressBar::chunk { background-color: #2563eb; border-radius: 8px; }"
        )
        self._progress_card.add_widget(self._progress_bar)

        self._step_label = QLabel("")
        self._step_label.setAlignment(Qt.AlignCenter)
        self._step_label.setStyleSheet("font-size: 13px; font-weight: 600; color: #374151;")
        self._progress_card.add_widget(self._step_label)

        self._action_label = QLabel("")
        self._action_label.setWordWrap(True)
        self._action_label.setStyleSheet("font-size: 12px; color: #6b7280;")
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
        self._cancel_btn.setCursor(Qt.PointingHandCursor)
        self._cancel_btn.setStyleSheet(
            "QPushButton { background-color: #6b7280; color: white; font-weight: 600;"
            " font-size: 13px; border-radius: 8px; border: none; padding: 8px 18px; }"
            "QPushButton:hover { background-color: #4b5563; }"
        )
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

    # -- view switching -------------------------------------------------------
    def show_preparing(self):
        self._stack.setCurrentWidget(self._preparing_view)
        self._prep_banner.set_key("flash_preparing")
        self._prep_step_key = "step_extract"
        self._prep_step.setText(tr("step_extract"))
        # firmware_downloader.py paradigm: presteps guides "unplug the device"
        # before the backend starts searching.
        self._load_image(self._prep_img, "presteps.png")

    def show_waiting(self):
        self._stack.setCurrentWidget(self._waiting_view)
        self._method_combo.setEnabled(True)

    def show_flashing(self):
        self._stack.setCurrentWidget(self._flashing_view)
        self._method_combo.setEnabled(False)
        self._load_image(self._flash_img, "installing.png")

    # -- flash method ---------------------------------------------------------
    def set_method(self, method, available=("auto", "sp", "mtk")):
        """Populate the backend selector; ``available`` limits the options
        (e.g. macOS has no SP Flash Tool). Selects ``method`` without
        re-triggering ``method_changed``."""
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
        """initsteps variant for the active backend/platform.

        Mirrors firmware_downloader.py: SP Flash Tool gets the SP-specific
        guide; MTKClient gets the Windows or generic guide depending on OS.
        """
        method = self.current_method()
        if method == "sp":
            return "initsteps_sp.png"
        if method == "mtk":
            return "initsteps_win.png" if paths.IS_WINDOWS else "initsteps.png"
        # "auto": SP Flash Tool on Windows/Linux, MTKClient on macOS.
        if paths.IS_MAC:
            return "initsteps.png"
        return "initsteps_sp.png"

    def _on_method_changed(self):
        self._update_method_note()
        # Refresh the guidance image for the newly selected backend while the
        # page is still waiting for a device.
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

    # -- method picker reveal (hidden by default on Win/Linux) ----------------
    def reveal_method_selector(self):
        """Show the backend method picker (normally hidden; user presses M)."""
        if paths.IS_MAC:
            return  # only one option — nothing to choose
        if self._method_revealed:
            return
        self._method_revealed = True
        self._method_label.setVisible(True)
        self._method_combo.setVisible(True)
        self._method_note.setVisible(True)
        self._update_method_note()

    # -- data -----------------------------------------------------------------
    def set_model(self, model):
        self._model = (model or "").strip() or ""

    def set_package_name(self, name):
        self._pkg_row.set_value(name or "—")

    def _connect_model_text(self):
        return self._model if self._model else tr("flash_model_generic")

    def set_waiting_device(self):
        self._wait_status.set_status("idle")
        self._wait_banner.set_type("info")
        self._wait_banner_key = "connect"
        self._wait_banner.set_key("flash_connect_prompt", model=self._connect_model_text())
        self._conn_value_key = "flash_conn_waiting"
        self._conn_row.set_value(tr("flash_conn_waiting"))
        # initsteps: "power off your device and connect USB" guidance image,
        # picked for the active backend/platform like firmware_downloader.py.
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
        # please_wait: device detected, DA handshake in progress
        # (firmware_downloader.py shows please_wait on "device detected").
        self._load_image(self._status_img, "please_wait.png")

    def set_device_flashing(self):
        self._wait_status.set_status("flashing")
        self._flash_banner.set_type("info")
        self._flash_banner.set_key("flash_banner_flashing")
        self._conn_value_key = "flash_conn_connected"
        self._dev_value_key = "status_connected"
        self._conn_row.set_value(tr("flash_conn_connected"))
        self._dev_row.set_value(tr("status_connected"))
        # Show installing.png — firmware is being written.
        self._load_image(self._status_img, "installing.png")

    def set_device_done(self):
        # installed.png — flash completed, ready to disconnect/reboot.
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

    # -- retranslation (in place; keeps the current view + activity state) ----
    def retranslate(self):
        self._prep_banner.retranslate()
        if self._wait_banner_key == "connect":
            # Re-derive the model text (the generic fallback is translated).
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
        # Rebuild the method combo items (labels are translatable).
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

    # -- callbacks ------------------------------------------------------------
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
