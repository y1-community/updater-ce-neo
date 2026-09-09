"""Main window — left navigation + right content area.

Select Software is the application's start page (no separate Home tab). After
a package is chosen, the flash backend launches immediately — SP Flash Tool
(Windows / staged Linux) or mtkclient (all platforms) — and the UI guides the
user to power off and connect their device while the backend searches USB.

The user can switch between SP Flash Tool and MTKClient at any time while
waiting for a device (except on macOS, where only MTKClient is available).
"""

import logging
import platform
import sys
import time
import webbrowser

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..config import (
    APP_NAME,
    APP_VERSION,
    UPDATE_CHECK_STARTUP_DELAY_MS,
    UPDATE_REPO,
)
from ..manifest import ManifestWorker
from ..updates import UpdateCheckWorker, UpdateInfo
from ..donation_dialog import DonationDialog, DonationStatusBar
from ..donors import load_donors_file, parse_donors_csv_text
from ..flash_service import (
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_WAITING,
    STEP_WRITE,
    FlashService,
    completed_extract_dir,
)
from ..i18n import tr, translator
from ..state import FlashState, StateMachine
from .dialogs import DiagnosticsDialog, FlashCompleteDialog, UpdateAvailableDialog
from .dark import T, apply_theme
from .error_page import ErrorPage
from .flash_page import FlashPage
from .retry_page import RetryPage
from .select_page import SelectPackagePage

logger = logging.getLogger(__name__)

_PAGE_SELECT = 0
_PAGE_FLASH = 1
_PAGE_ERROR = 2
_PAGE_RETRY = 3

_STEP_KEY = {
    STEP_EXTRACTING: "step_extract",
    STEP_WAITING: "step_wait",
    STEP_DETECT: "step_detect",
    STEP_DOWNLOAD_DA: "step_download_da",
    STEP_DOWNLOAD_BL: "step_download_bl",
    STEP_WRITE: "step_write",
    STEP_DONE: "step_done",
}

_WRITE_STEPS = (STEP_DOWNLOAD_DA, STEP_DOWNLOAD_BL, STEP_WRITE)


class MainWindow(QMainWindow):
    log_line_added = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(960, 660)

        self.sm = StateMachine(self)
        self.service = FlashService(self)
        self.settings = QSettings("innioasis", "updater")
        translator().set_language(str(self.settings.value("language", "en")))

        self._package_path = ""
        self._package_name = ""
        self._package_model = ""
        self._log_lines = []
        self._flash_method = str(
            self.settings.value("flash_method", "auto")
        ).lower()
        if self._flash_method not in ("auto", "sp", "mtk"):
            self._flash_method = "auto"
        self._last_progress = 0
        self._flash_start_ts = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._step_now = ""
        self._update_offered_once = False
        self._update_manual_pending = False

        self._build_ui()
        self._connect_signals()
        self._nav_to_page(_PAGE_SELECT)

        self._manifest_worker = ManifestWorker(self)
        self._manifest_worker.finished.connect(self._on_manifest_loaded)
        self._manifest_worker.start()
        QTimer.singleShot(UPDATE_CHECK_STARTUP_DELAY_MS, self._start_auto_update_check)

        if platform.system() == "Linux":
            QTimer.singleShot(600, self._check_linux_first_run)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_nav())

        self._stack = QStackedWidget()
        self._select_page = SelectPackagePage()
        self._flash_page = FlashPage()
        self._error_page = ErrorPage()
        self._retry_page = RetryPage()
        for w in (self._select_page, self._flash_page, self._error_page, self._retry_page):
            self._stack.addWidget(w)
        outer.addWidget(self._stack, 1)

        self._donations = parse_donors_csv_text(load_donors_file([
            paths.RESOURCES_DIR / "donors.csv",
        ]) or "")
        self.setStatusBar(DonationStatusBar(
            parent=self,
            donations=self._donations,
            on_support=self._on_support_clicked,
            on_donations_updated=self._on_donations_updated,
        ))
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")

    def _build_nav(self):
        t = T()
        nav = QWidget()
        nav.setObjectName("navPanel")
        nav.setFixedWidth(190)
        # Nav sidebar is always dark; use translucent dark styling on macOS glass
        if sys.platform == "darwin":
            nav.setStyleSheet(
                "QWidget#navPanel { background-color: rgba(11, 17, 32, 0.75);"
                " border-right: 1px solid rgba(255, 255, 255, 0.12);"
                " border-radius: 12px 0 0 12px; }"
            )
            top_margin = 38
        else:
            nav.setStyleSheet(
                "QWidget#navPanel { background-color: #0b1120; border-right: 1px solid #1a2538;"
                " border-radius: 12px 0 0 12px; }"
            )
            top_margin = 20

        layout = QVBoxLayout(nav)
        layout.setContentsMargins(12, top_margin, 12, 14)
        layout.setSpacing(4)

        self._brand_label = QLabel(tr("app_name"))
        self._brand_label.setWordWrap(True)
        self._brand_label.setStyleSheet(
            "font-size: 16px; font-weight: 800; color: #f1f5f9; letter-spacing: -0.02em;"
            " background: transparent; border: none;"
        )
        layout.addWidget(self._brand_label)

        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet("font-size: 11px; color: #94a3b8; background: transparent; border: none;")
        layout.addWidget(version)
        layout.addSpacing(16)

        self._nav_buttons = {}
        btn = QPushButton(tr("nav_select_package"))
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._nav_to_page(_PAGE_SELECT))
        layout.addWidget(btn)
        self._nav_buttons["nav_select_package"] = (btn, _PAGE_SELECT)

        layout.addStretch()

        self._support_btn = QPushButton(tr("nav_donate"))
        self._support_btn.setCursor(Qt.PointingHandCursor)
        self._support_btn.clicked.connect(self._on_support_clicked)
        layout.addWidget(self._support_btn)

        self._log_btn = QPushButton(tr("nav_log"))
        self._log_btn.setCursor(Qt.PointingHandCursor)
        self._log_btn.clicked.connect(self._show_diagnostics)
        layout.addWidget(self._log_btn)

        self._credits_btn = QPushButton(tr("nav_credits"))
        self._credits_btn.setCursor(Qt.PointingHandCursor)
        self._credits_btn.clicked.connect(self._open_credits)
        layout.addWidget(self._credits_btn)

        self._check_updates_btn = QPushButton(tr("nav_check_updates"))
        self._check_updates_btn.setCursor(Qt.PointingHandCursor)
        self._check_updates_btn.clicked.connect(self._on_check_updates_clicked)
        layout.addWidget(self._check_updates_btn)

        if platform.system() == "Linux":
            self._linux_setup_btn = QPushButton(tr("nav_linux_setup"))
            self._linux_setup_btn.setCursor(Qt.PointingHandCursor)
            self._linux_setup_btn.clicked.connect(self._show_linux_setup)
            layout.addWidget(self._linux_setup_btn)

        self._lang_label = QLabel(tr("nav_language"))
        self._lang_label.setStyleSheet(
            "font-size: 11px; color: #94a3b8; margin-top: 8px; background: transparent; border: none;"
        )
        layout.addWidget(self._lang_label)
        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("langCombo")
        self._lang_combo.addItem("\u4e2d\u6587", "zh-CN")
        self._lang_combo.addItem("English", "en")
        self._lang_combo.addItem("Fran\u00e7ais", "fr")
        self._lang_combo.addItem("Espa\u00f1ol", "es")
        idx = self._lang_combo.findData(translator().lang)
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self._lang_combo)

        # Nav button styling — high-contrast slate palette with 34px/32px touch targets
        for btn, _ in self._nav_buttons.values():
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #cbd5e1; text-align: left;"
                " padding: 10px 14px; border-radius: 8px; border: none; font-size: 13px; min-height: 34px; }"
                "QPushButton:hover { background-color: #1e293b; color: #f8fafc; }"
                "QPushButton:checked { background-color: #2563eb; color: #ffffff; font-weight: 600; }"
            )
        aux_btns = [self._support_btn, self._log_btn, self._credits_btn, self._check_updates_btn]
        if hasattr(self, "_linux_setup_btn"):
            aux_btns.append(self._linux_setup_btn)
        for btn in aux_btns:
            btn.setStyleSheet(
                "QPushButton { background: transparent; color: #cbd5e1; text-align: left;"
                " padding: 8px 14px; border-radius: 8px; border: none; font-size: 12px; min-height: 32px; }"
                "QPushButton:hover { background-color: #1e293b; color: #f8fafc; }"
            )
        return nav

    def _connect_signals(self):
        self._select_page.package_selected.connect(self._on_package_selected)
        self._flash_page.method_changed.connect(self._on_method_changed)
        self._flash_page.on_cancel(self._on_cancel_flash)
        self._flash_page.on_cancel_wait(self._on_cancel_wait)
        self._error_page.on_retry(self._on_retry_flash)
        self._error_page.on_reconnect(self._on_reconnect)
        self._error_page.on_reselect(lambda: self._nav_to_page(_PAGE_SELECT))
        self._error_page.on_view_log(self._show_diagnostics)
        self._retry_page.on_cancel(self._on_cancel_flash)
        self.service.step_changed.connect(self._on_step_changed)
        self.service.progress.connect(self._on_progress)
        self.service.log_message.connect(self._on_log_message)
        self.service.flash_finished.connect(self._on_flash_finished)
        self.service.device_found.connect(self._on_device_found)
        self.service.device_lost.connect(self._on_device_lost)
        self.service.monitor_error.connect(self._on_monitor_error)

    def keyPressEvent(self, event):
        if event.key() == 0x004D and not int(event.modifiers()):
            if self._stack.currentIndex() == _PAGE_FLASH:
                self._flash_page.reveal_method_selector()
        super().keyPressEvent(event)

    def _nav_to_page(self, page_idx):
        self._stack.setCurrentIndex(page_idx)
        for key, (btn, idx) in self._nav_buttons.items():
            btn.setChecked(idx == page_idx)

    def _on_package_selected(self, path, name, model):
        self._package_path = path
        self._package_name = name
        self._package_model = model or ""
        self._begin_flash_flow()

    def _begin_flash_flow(self):
        if not self._package_path:
            return
        try:
            self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        except ValueError:
            self.sm.reset_full()
            self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._flash_page.set_method(self._flash_method, available=_METHODS_AVAILABLE)
        self._flash_page.show_preparing()
        self._nav_to_page(_PAGE_FLASH)
        self._append_log(
            f"Starting flash for {self._package_name} "
            f"(method: {_method_label(self._flash_method)})"
        )
        pre_extracted = completed_extract_dir(self._package_path)
        self.service.start_flash(
            self._package_path, pre_extracted_dir=pre_extracted, method=self._flash_method
        )
        self.service.start_device_monitor()

    def _on_step_changed(self, step):
        self._step_now = step
        key = _STEP_KEY.get(step, "step_wait")
        self._flash_page.update_step(key)
        self._retry_page.update_step(key)
        self._on_progress(self._last_progress)
        if step == STEP_EXTRACTING:
            self._flash_page.show_preparing()
        elif step == STEP_WAITING:
            self._flash_page.show_waiting()
            self._flash_page.set_waiting_device()
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step == STEP_DETECT:
            self._flash_page.set_detected()
            self._set_state(FlashState.S3_DEVICE_DETECTED)
        elif step in _WRITE_STEPS:
            self._flash_page.show_flashing()
            self._flash_page.set_device_flashing()
            self._enter_flashing_state()
        elif step == STEP_DONE:
            self._flash_page.set_device_done()

    def _set_state(self, state):
        if self.sm.state is state:
            return
        try:
            self.sm.transition_to(state)
        except ValueError:
            try:
                self.sm.force_state(state)
            except Exception:
                pass

    def _enter_flashing_state(self):
        if self.sm.state is not FlashState.S4_FLASHING:
            if self.sm.state is FlashState.S2_WAIT_CONNECTION:
                self._set_state(FlashState.S3_DEVICE_DETECTED)
            self._set_state(FlashState.S4_FLASHING)
            if not self._flash_start_ts:
                self._flash_start_ts = time.time()
            self._elapsed_timer.start(1000)

    def _on_progress(self, percent):
        self._last_progress = percent
        self._flash_page.update_prep_progress(percent)
        self._flash_page.update_progress(percent)
        self._retry_page.update_progress(percent)

    def _on_log_message(self, msg):
        self._append_log(msg)

    def _append_log(self, msg):
        self._log_lines.append(msg)
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-2000:]
        self.log_line_added.emit(msg)

    def _on_device_found(self, port):
        self._append_log(f"Device detected: {port}")
        self._flash_page.set_detected()

    def _on_device_lost(self):
        if self.sm.state is FlashState.S4_FLASHING:
            try:
                self.sm.transition_to(FlashState.S5_USB_DISCONNECTED)
            except ValueError:
                return
            self._elapsed_timer.stop()
            self.service.cancel_flash()
            self._error_page.show_usb_disconnected(self._last_progress or 0)
            self._nav_to_page(_PAGE_ERROR)
        elif self.sm.state in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            try:
                self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
            except ValueError:
                pass
            self._flash_page.set_waiting_device()

    def _on_monitor_error(self, msg):
        self._append_log(msg)

    def _on_flash_finished(self, ok, error_code):
        self._elapsed_timer.stop()
        if ok:
            self._handle_flash_success()
        else:
            self._handle_flash_failure(error_code)

    def _handle_flash_success(self):
        try:
            self.sm.transition_to(FlashState.S5_COMPLETE)
        except ValueError:
            self.sm.force_state(FlashState.S5_COMPLETE)
        donation_disabled = self.settings.value(
            "donation_install_prompt_disabled", False, type=bool
        )
        if donation_disabled:
            dialog = FlashCompleteDialog(self, self._package_name, self._elapsed_text())
            dialog.exec()
        else:
            self._show_donation_dialog(context="install_success")
        self._reset_after_run()

    def _handle_flash_failure(self, error_code):
        try:
            self.sm.transition_to(FlashState.S5_FAILED)
        except ValueError:
            self.sm.force_state(FlashState.S5_FAILED)
        self._error_page.show_flash_failed(
            self._last_progress or 0, self._step_now, error_code,
            self._package_name, self.sm.context.retry_count,
        )
        self._nav_to_page(_PAGE_ERROR)

    def _on_retry_flash(self):
        self.sm.reset_for_retry()
        self._retry_page.update_info(
            self._package_name, self.sm.context.retry_count, "status_retrying"
        )
        self._retry_page.update_progress(0)
        self._nav_to_page(_PAGE_RETRY)
        QTimer.singleShot(0, self._begin_flash_flow)

    def _on_reconnect(self):
        self._set_state(FlashState.S2_WAIT_CONNECTION)
        self._flash_page.set_waiting_device()
        self._nav_to_page(_PAGE_FLASH)

    def _on_cancel_wait(self):
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self.sm.reset_full()
        self._nav_to_page(_PAGE_SELECT)

    def _on_method_changed(self, method):
        if self.sm.state not in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            return
        if method == self._flash_method:
            return
        self._flash_method = method
        self.settings.setValue("flash_method", method)
        self._append_log(f"Flash method changed to {_method_label(method)}; restarting search...")
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        pre_extracted = completed_extract_dir(self._package_path)
        self.service.start_flash(
            self._package_path, pre_extracted_dir=pre_extracted, method=method
        )
        self.service.start_device_monitor()

    def _on_cancel_flash(self):
        answer = QMessageBox.question(
            self, tr("flash_cancel_title"), tr("flash_cancel_msg"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.service.cancel_flash()
            self._elapsed_timer.stop()
            self._reset_after_run()

    def _reset_after_run(self):
        self.sm.reset_full()
        self._package_path = ""
        self._package_name = ""
        self._flash_start_ts = 0.0
        self.service.stop_device_monitor()
        self._nav_to_page(_PAGE_SELECT)

    def _tick_elapsed(self):
        self._flash_page.update_time(self._elapsed_text(), self._eta_text())

    def _elapsed_text(self):
        secs = int(time.time() - self._flash_start_ts) if self._flash_start_ts else 0
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _eta_text(self):
        pct = max(self._last_progress or 0, 1)
        secs = int((time.time() - self._flash_start_ts) * (100 - pct) / pct) if self._flash_start_ts else 0
        if secs <= 0:
            return "--:--"
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _show_diagnostics(self):
        dlg = DiagnosticsDialog(self, self._log_lines)
        self.log_line_added.connect(dlg.append_line)
        try:
            dlg.exec()
        finally:
            self.log_line_added.disconnect(dlg.append_line)

    def _show_linux_setup(self):
        from .dialogs import LinuxSetupDialog
        dlg = LinuxSetupDialog(self, auto_start=False)
        dlg.exec()

    def _check_linux_first_run(self):
        from .. import linux_sp_flash
        from .dialogs import LinuxSetupDialog

        stage = linux_sp_flash.stage_dir()
        first_run_done = self.settings.value("linux_first_run_completed", False, type=bool)
        files_ready = linux_sp_flash.files_ready(stage)

        if not files_ready or not first_run_done:
            self.settings.setValue("linux_first_run_completed", True)
            dlg = LinuxSetupDialog(self, auto_start=True)
            dlg.exec()

    def _open_credits(self):
        webbrowser.open("https://innioasis.app/credits.html?thank-you=1")

    def _on_language_changed(self, index):
        lang = self._lang_combo.itemData(index)
        translator().set_language(lang)
        self.settings.setValue("language", lang)
        self._retranslate_all()

    def _retranslate_all(self):
        self._brand_label.setText(tr("app_name"))
        for key, (btn, _idx) in self._nav_buttons.items():
            btn.setText(tr(key))
        self._support_btn.setText(tr("nav_donate"))
        self._log_btn.setText(tr("nav_log"))
        self._credits_btn.setText(tr("nav_credits"))
        self._check_updates_btn.setText(tr("nav_check_updates"))
        self._lang_label.setText(tr("nav_language"))
        self._select_page.retranslate()
        self._flash_page.retranslate()
        self._error_page.retranslate()
        self._retry_page.retranslate()
        if self.statusBar() and hasattr(self.statusBar(), "retranslate"):
            self.statusBar().retranslate()

    def _on_manifest_loaded(self, entries):
        if entries:
            self._select_page._on_model_changed()

    def _start_auto_update_check(self):
        if self._update_offered_once:
            return
        self._update_manual_pending = False
        self._run_update_check()

    def _on_check_updates_clicked(self):
        self._update_manual_pending = True
        self._run_update_check()

    def _run_update_check(self):
        worker = UpdateCheckWorker(UPDATE_REPO, APP_VERSION, self)
        worker.finished.connect(
            lambda info: self._on_update_check_done(info, self._update_manual_pending)
        )
        self._update_worker = worker
        worker.start()

    def _on_update_check_done(self, info, manual):
        if not isinstance(info, UpdateInfo):
            info = UpdateInfo()
        if not manual and self._update_offered_once:
            return
        if info.tag:
            skipped = str(self.settings.value("update_skipped_version", ""))
            if info.version == skipped and not manual:
                return
            self._update_offered_once = True
            dlg = UpdateAvailableDialog(
                self,
                info=info,
                current_version=APP_VERSION,
                on_skip=lambda v: self.settings.setValue("update_skipped_version", v),
            )
            dlg.exec()
        elif manual:
            QMessageBox.information(
                self,
                tr("update_available"),
                tr("update_check_failed")
                if info.failed
                else tr("update_up_to_date").format(version=APP_VERSION),
            )

    def _on_donations_updated(self, donations):
        if donations:
            self._donations = donations

    def _on_support_clicked(self):
        self._show_donation_dialog(context="general")

    def _show_donation_dialog(self, context="general"):
        dialog = DonationDialog(
            parent=self,
            context=context,
            model=self._package_model,
            software_name=self._package_name,
            donations=self._donations,
            on_dont_ask_again=lambda: self.settings.setValue(
                "donation_install_prompt_disabled", True
            ),
        )
        dialog.exec()

    def cleanup_workers(self):
        """Cleanly terminate and wait for any background workers."""
        self.service.cleanup()
        select_page = getattr(self, "_select_page", None)
        if select_page is not None:
            rw = getattr(select_page, "_releases_worker", None)
            if rw is not None and rw.isRunning():
                rw.requestInterruption()
                rw.wait(1500)
            dw = getattr(select_page, "_download_worker", None)
            if dw is not None and dw.isRunning():
                dw.cancel()
                dw.wait(1500)
        for w in (getattr(self, "_manifest_worker", None), getattr(self, "_update_worker", None)):
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(1500)

    def close(self):
        self.cleanup_workers()
        return super().close()

    def closeEvent(self, event):
        self.cleanup_workers()
        super().closeEvent(event)


def _is_mac():
    import sys
    return sys.platform == "darwin"


_METHODS_AVAILABLE = ("auto", "sp", "mtk") if not _is_mac() else ("auto", "mtk")

_METHOD_LABELS = {
    "auto": "Auto",
    "sp": "SP Flash Tool",
    "mtk": "MTKClient",
}


def _method_label(method: str) -> str:
    return _METHOD_LABELS.get(method, method)
