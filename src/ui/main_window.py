"""Main window — left navigation + right content area.

Select Software is the application's start page (no separate Home tab). After
a package is chosen, the flash backend launches immediately — SP Flash Tool
(Windows / staged Linux) or mtkclient (all platforms) — and the UI guides the
user to power off and connect their device while the backend searches USB.

The install method is chosen in Settings (Windows/Linux default to SP Flash
Tool's console-mode XML flow; macOS has MTKClient only) and can be switched
while waiting for a device, which restarts the run with the new backend.
"""

import logging
import os
import platform
import sys
import time

from PySide6.QtCore import (
    QEvent,
    QProcess,
    QProcessEnvironment,
    QSettings,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QIcon, QPixmap
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

from .. import device_tracking
from .. import paths
from ..config import (
    APP_NAME,
    APP_VERSION,
    UPDATE_CHECK_STARTUP_DELAY_MS,
    UPDATE_REPO,
    install_power_on_steps,
    is_generic_mtk,
    get_app_name,
)
from ..manifest import ManifestWorker
from ..updates import UpdateCheckWorker, UpdateInfo
from ..donation_dialog import DonationDialog, DonationStatusBar
from ..donors import cached_donors_path, load_donors_file, parse_donors_csv_text
from ..flash_service import (
    LINE_CONNECT_HINT,
    LINE_RETRY,
    METHOD_MTK_MAC,
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_WAITING,
    STEP_WRITE,
    FlashService,
    classify_backend_line,
    completed_extract_dir,
    normalise_method,
)
from ..i18n import tr, translator
from ..state import FlashState, StateMachine
from .dialogs import (
    DiagnosticsDialog,
    FlashCompleteDialog,
    ReleaseReminderDialog,
    RetryGuidanceDialog,
    UpdateAvailableDialog,
)
from .dark import T, apply_theme, content_top_margin, is_dark
from .error_page import ErrorPage
from .flash_page import FlashPage
from .retry_page import RetryPage
from .select_page import SelectPackagePage
from .settings_page import SettingsPage

logger = logging.getLogger(__name__)

_PAGE_SELECT = 0
_PAGE_FLASH = 1
_PAGE_ERROR = 2
_PAGE_RETRY = 3
_PAGE_SETTINGS = 4


class DeviceUpdateCheckWorker(QThread):
    finished = Signal(list)

    def __init__(self, settings=None, ignore_last_notified=False, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.ignore_last_notified = ignore_last_notified

    def run(self):
        try:
            updates = device_tracking.check_device_updates(
                settings=self.settings,
                ignore_last_notified=self.ignore_last_notified,
            )
            self.finished.emit(updates or [])
        except Exception as e:
            logger.debug("Device update check failed: %s", e)
            self.finished.emit([])

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

# Failure codes whose only real fix is the hardware reset described by
# RetryGuidanceDialog: the device stopped answering the handshake.
_RETRY_GUIDANCE_CODES = ("CONNECTION_FAILED", "MTK_INIT_FAILED")


class MainWindow(QMainWindow):
    log_line_added = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(900, 520)
        self.setMinimumSize(900, 500)

        self.sm = StateMachine(self)
        self.service = FlashService(self)
        self.settings = QSettings("innioasis", "updater")
        translator().set_language(str(self.settings.value("language", "en")))

        self._package_path = ""
        self._package_name = ""
        self._package_model = ""
        self._log_lines = []
        # Legacy "auto" values resolve to the platform default so users are
        # steered to SP Flash Tool's console-mode XML flow (MTKClient on macOS).
        self._flash_method = normalise_method(self.settings.value("flash_method", ""))
        self._last_progress = 0
        self._flash_start_ts = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._step_now = ""
        self._update_offered_once = False
        self._update_manual_pending = False
        # A stalled connection makes the backend repeat the same errno line
        # many times over; this keeps the guidance dialog to once per attempt.
        self._retry_guidance_shown = False

        self._build_ui()
        self._connect_signals()
        self._nav_to_page(_PAGE_SELECT)

        self._manifest_worker = ManifestWorker(self)
        self._manifest_worker.finished.connect(self._on_manifest_loaded)
        self._manifest_worker.start()
        QTimer.singleShot(UPDATE_CHECK_STARTUP_DELAY_MS, self._start_auto_update_check)
        QTimer.singleShot(
            UPDATE_CHECK_STARTUP_DELAY_MS + 1000,
            lambda: self._check_device_firmware_updates(manual=False),
        )

        if platform.system() == "Linux" and not paths.IS_MAC:
            # macOS has no SP Flash Tool or Linux setup wizard.
            QTimer.singleShot(600, self._check_linux_first_run)

    def _build_ui(self):
        central = QWidget()
        central.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setCentralWidget(central)
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_nav())

        self._stack = QStackedWidget()
        self._stack.setAttribute(Qt.WA_TranslucentBackground, True)
        self._select_page = SelectPackagePage()
        self._flash_page = FlashPage()
        self._error_page = ErrorPage()
        self._retry_page = RetryPage()
        self._settings_page = SettingsPage()
        for w in (
            self._select_page,
            self._flash_page,
            self._error_page,
            self._retry_page,
            self._settings_page,
        ):
            w.setAttribute(Qt.WA_TranslucentBackground, True)
            self._stack.addWidget(w)
        outer.addWidget(self._stack, 1)

        self._settings_page.donation_visibility_changed.connect(
            self._apply_donation_visibility
        )
        self._settings_page.offline_mode_changed.connect(
            self._on_offline_mode_changed
        )
        self._settings_page.check_updates_requested.connect(
            lambda: self._check_device_firmware_updates(manual=True)
        )

        self._donations = parse_donors_csv_text(load_donors_file([
            cached_donors_path(),
            paths.RESOURCES_DIR / "donors.csv",
        ]) or "")
        self.setStatusBar(DonationStatusBar(
            parent=self,
            donations=self._donations,
            on_support=self._on_support_clicked,
            on_donations_updated=self._on_donations_updated,
        ))
        self.statusBar().messageChanged.connect(self._on_status_message_changed)
        self._select_page.status_message.connect(self._show_status)
        self._apply_donation_visibility()
        self._apply_generic_mtk_branding()

    def _build_nav(self):
        t = T()
        nav = QWidget()
        nav.setObjectName("navPanel")
        nav.setAttribute(Qt.WA_TranslucentBackground, True)
        is_mac = sys.platform == "darwin"
        nav_width = 185 if is_mac else 175
        nav.setFixedWidth(nav_width)

        # Adapt nav sidebar to host system theme and glass transparency
        if is_mac:
            nav_bg = "transparent"
            nav_border = "none"
        else:
            nav_bg = t.bg_nav
            nav_border = f"1px solid {t.border}"
        # Just clear the window title bar (traffic lights on macOS); a larger
        # inset only pushes the first sidebar entry down into empty space.
        top_margin = content_top_margin()

        nav.setStyleSheet(
            f"QWidget#navPanel {{ background-color: {nav_bg}; border-right: {nav_border};"
            f" border-radius: 0; }}"
        )

        layout = QVBoxLayout(nav)
        layout.setContentsMargins(12, top_margin, 12, 14)
        layout.setSpacing(4)

        self._nav_buttons = {}
        btn = QPushButton(tr("nav_select_package"))
        btn.setCheckable(True)
        btn.clicked.connect(lambda: self._nav_to_page(_PAGE_SELECT))
        layout.addWidget(btn)
        self._nav_buttons["nav_select_package"] = (btn, _PAGE_SELECT)

        self._settings_btn = QPushButton(tr("nav_settings"))
        self._settings_btn.setCheckable(True)
        self._settings_btn.clicked.connect(lambda: self._nav_to_page(_PAGE_SETTINGS))
        layout.addWidget(self._settings_btn)
        self._nav_buttons["nav_settings"] = (self._settings_btn, _PAGE_SETTINGS)

        layout.addStretch()

        self._support_btn = QPushButton(tr("nav_donate"))
        self._support_btn.clicked.connect(self._on_support_clicked)
        layout.addWidget(self._support_btn)

        self._log_btn = QPushButton(tr("nav_log"))
        self._log_btn.clicked.connect(self._show_diagnostics)
        layout.addWidget(self._log_btn)

        self._credits_btn = QPushButton(tr("nav_credits"))
        self._credits_btn.clicked.connect(self._open_credits)
        layout.addWidget(self._credits_btn)

        self._check_updates_btn = QPushButton(tr("nav_check_updates"))
        self._check_updates_btn.clicked.connect(self._on_check_updates_clicked)
        layout.addWidget(self._check_updates_btn)

        if platform.system() == "Linux" and not paths.IS_MAC:
            self._linux_setup_btn = QPushButton(tr("nav_linux_setup"))
            self._linux_setup_btn.clicked.connect(self._show_linux_setup)
            layout.addWidget(self._linux_setup_btn)

        from ..sp_flash_gui import is_sp_flash_gui_supported
        if is_sp_flash_gui_supported():
            self._sp_flash_tool_btn = QPushButton(tr("nav_sp_flash_tool_gui"))
            self._sp_flash_tool_btn.clicked.connect(self._open_sp_flash_tool_gui)
            layout.addWidget(self._sp_flash_tool_btn)

        layout.addSpacing(10)

        # Brand header container & version at bottom left, above language dropdown
        brand_container = QWidget()
        brand_row = QHBoxLayout(brand_container)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(6)

        self._icon_label = QLabel()
        icon_path = paths.RESOURCES_DIR / "icon.png"
        icon_size = 30  # 50% larger than 20px
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                icon_size, icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._icon_label.setPixmap(pix)
        self._icon_label.setStyleSheet("background: transparent; border: none;")
        brand_row.addWidget(self._icon_label)

        self._brand_label = QLabel(get_app_name())
        brand_font_size = "14px"
        self._brand_label.setStyleSheet(
            f"font-size: {brand_font_size}; font-weight: 800; color: {t.fg}; letter-spacing: -0.02em;"
            f" background: transparent; border: none;"
        )
        brand_row.addWidget(self._brand_label)

        self._brand_version = QLabel(f"v{APP_VERSION}")
        self._brand_version.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {t.fg_dim};"
            f" background: transparent; border: none; margin-top: 2px;"
        )
        brand_row.addWidget(self._brand_version)
        brand_row.addStretch()
        layout.addWidget(brand_container)

        from .. import browser
        self._version_label = QLabel(
            f'by <a href="https://ko-fi.com/teamslide" style="color: {t.accent}; text-decoration: underline;">Ryan Specter</a>'
        )
        self._version_label.setStyleSheet(
            f"font-size: 11px; color: {t.fg_dim}; background: transparent; border: none; margin-left: 2px;"
        )
        self._version_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._version_label.setOpenExternalLinks(False)
        self._version_label.linkActivated.connect(lambda url: browser.open_browser(url))
        layout.addWidget(self._version_label)

        self._lang_label = QLabel(tr("nav_language"))
        self._lang_label.setStyleSheet(
            f"font-size: 11px; color: {t.fg_dim}; margin-top: 6px; background: transparent; border: none;"
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

        # Nav button styling — follows native OS desktop environment accent color with focus states
        for btn, _ in self._nav_buttons.values():
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {t.fg}; text-align: left;"
                f" padding: 8px 12px; border-radius: 5px; border: 1px solid transparent; font-size: 13px; font-weight: 500; min-height: 34px; }}"
                f"QPushButton:hover {{ background-color: {t.bg_hover}; color: {t.fg}; }}"
                f"QPushButton:focus {{ border: 1px solid {t.border_focus}; outline: none; }}"
                f"QPushButton:checked {{ background-color: {t.nav_active}; color: {t.nav_active_text}; font-weight: 600; border: 1px solid transparent; }}"
                f"QPushButton:checked:focus {{ background-color: {t.nav_active}; color: {t.nav_active_text}; font-weight: 600; border: 1px solid {t.border_strong}; outline: none; }}"
            )
        aux_btns = [self._support_btn, self._log_btn, self._credits_btn, self._check_updates_btn]
        if hasattr(self, "_linux_setup_btn"):
            aux_btns.append(self._linux_setup_btn)
        if hasattr(self, "_sp_flash_tool_btn"):
            aux_btns.append(self._sp_flash_tool_btn)
        for btn in aux_btns:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {t.fg_dim}; text-align: left;"
                f" padding: 7px 12px; border-radius: 5px; border: 1px solid transparent; font-size: 12px; font-weight: 500; min-height: 30px; }}"
                f"QPushButton:hover {{ background-color: {t.bg_hover}; color: {t.fg}; }}"
                f"QPushButton:focus {{ border: 1px solid {t.border_focus}; outline: none; }}"
            )
        self._nav_panel = nav
        self._aux_nav_buttons = aux_btns
        return nav

    def refresh_theme(self):
        """Update window components to match active OS theme tokens."""
        t = T()
        is_mac = sys.platform == "darwin"
        use_glass = False
        if is_mac:
            try:
                from .glass import is_glass_supported
                use_glass = is_glass_supported()
            except ImportError:
                use_glass = False

        nav_bg = "transparent" if (is_mac or use_glass) else t.bg_nav
        nav_border = "none" if (is_mac or use_glass) else f"1px solid {t.border}"

        if hasattr(self, "_nav_panel"):
            self._nav_panel.setStyleSheet(
                f"QWidget#navPanel {{ background-color: {nav_bg}; border-right: {nav_border}; border-radius: 0; }}"
            )

        if hasattr(self, "_brand_label"):
            self._brand_label.setStyleSheet(
                f"font-size: 14px; font-weight: 800; color: {t.fg}; letter-spacing: -0.02em; background: transparent; border: none;"
            )

        if hasattr(self, "_version_label"):
            from ..config import APP_VERSION
            self._version_label.setText(
                f'v{APP_VERSION} by <a href="https://ko-fi.com/teamslide" style="color: {t.accent}; text-decoration: underline;">Ryan Specter</a>'
            )
            self._version_label.setStyleSheet(
                f"font-size: 11px; color: {t.fg_dim}; background: transparent; border: none; margin-left: 2px;"
            )

        if hasattr(self, "_lang_label"):
            self._lang_label.setStyleSheet(
                f"font-size: 11px; color: {t.fg_dim}; margin-top: 6px; background: transparent; border: none;"
            )

        if hasattr(self, "_nav_buttons"):
            for btn, _ in self._nav_buttons.values():
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {t.fg}; text-align: left;"
                    f" padding: 8px 12px; border-radius: 5px; border: 1px solid transparent; font-size: 13px; font-weight: 500; min-height: 34px; }}"
                    f"QPushButton:hover {{ background-color: {t.bg_hover}; color: {t.fg}; }}"
                    f"QPushButton:focus {{ border: 1px solid {t.border_focus}; outline: none; }}"
                    f"QPushButton:checked {{ background-color: {t.nav_active}; color: {t.nav_active_text}; font-weight: 600; border: 1px solid transparent; }}"
                    f"QPushButton:checked:focus {{ background-color: {t.nav_active}; color: {t.nav_active_text}; font-weight: 600; border: 1px solid {t.border_strong}; outline: none; }}"
                )

        if hasattr(self, "_aux_nav_buttons"):
            for btn in self._aux_nav_buttons:
                btn.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {t.fg_dim}; text-align: left;"
                    f" padding: 7px 12px; border-radius: 5px; border: 1px solid transparent; font-size: 12px; font-weight: 500; min-height: 30px; }}"
                    f"QPushButton:hover {{ background-color: {t.bg_hover}; color: {t.fg}; }}"
                    f"QPushButton:focus {{ border: 1px solid {t.border_focus}; outline: none; }}"
                )

        sb = self.statusBar()
        if sb and hasattr(sb, "refresh_theme"):
            sb.refresh_theme()

        for page in (self._select_page, self._flash_page, self._error_page, self._retry_page, self._settings_page):
            if hasattr(page, "refresh_theme") and callable(page.refresh_theme):
                page.refresh_theme()


    def _connect_signals(self):
        self._select_page.package_selected.connect(self._on_package_selected)
        self._settings_page.flash_method_changed.connect(self._on_method_changed)
        self._settings_page.simulated_mac_requested.connect(
            self._restart_in_simulated_macos
        )
        self._flash_page.on_cancel(self._on_cancel_flash)
        self._flash_page.on_cancel_wait(self._on_cancel_wait)
        self._flash_page.on_open_sp_gui(self._open_sp_flash_tool_gui)
        self._error_page.on_retry(self._on_retry_flash)
        self._error_page.on_reconnect(self._on_reconnect)
        self._error_page.on_reselect(lambda: self._nav_to_page(_PAGE_SELECT))
        self._error_page.on_view_log(self._show_diagnostics)
        self._error_page.on_open_sp_gui(self._open_sp_flash_tool_gui)
        self._retry_page.on_cancel(self._on_cancel_flash)
        self.service.step_changed.connect(self._on_step_changed)
        self.service.progress.connect(self._on_progress)
        self.service.action_changed.connect(self._flash_page.update_action)
        self.service.log_message.connect(self._on_log_message)
        self.service.flash_finished.connect(self._on_flash_finished)
        self.service.device_found.connect(self._on_device_found)
        self.service.device_lost.connect(self._on_device_lost)
        self.service.monitor_error.connect(self._on_monitor_error)

    def _reassert_mac_glass(self, *_args):
        if sys.platform == "darwin":
            if getattr(self, "_reasserting_mac_glass", False):
                return
            self._reasserting_mac_glass = True
            try:
                from .glass import _ensure_seamless_titlebar, apply_glass
                _ensure_seamless_titlebar(self)
                apply_glass(self)
            finally:
                self._reasserting_mac_glass = False

    def changeEvent(self, event):
        super().changeEvent(event)
        if sys.platform == "darwin" and event.type() in (
            QEvent.WindowStateChange,
            QEvent.ActivationChange,
        ):
            self._reassert_mac_glass()

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "darwin":
            from .glass import apply_glass, configure_traffic_lights
            apply_glass(self)
            configure_traffic_lights(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if sys.platform == "darwin":
            self._reassert_mac_glass()

    def keyPressEvent(self, event):
        # M or D reveal the hidden install-method entry. "MTKClient (Mac)" runs
        # the macOS code path on Linux/Windows so the Mac flow (MTKClient as the
        # only backend, mac-centric prompts) can be tested without a Mac.
        # NB: PySide6 6.11 returns a Flag here, so int(modifiers()) raises.
        if event.key() in (Qt.Key_M, Qt.Key_D) and event.modifiers() == Qt.NoModifier:
            self._settings_page.reveal_advanced_methods()
            if self._settings_page.advanced_methods_revealed():
                # Remember the reveal across launches: it also unlocks the
                # MTKClient diagnostics view on Windows, which is otherwise
                # hidden because SP Flash Tool is the supported backend there.
                try:
                    from ..device_tracking import set_hidden_mtk_options

                    set_hidden_mtk_options(True)
                except Exception:
                    pass
                self._nav_to_page(_PAGE_SETTINGS)
                self._append_log("Advanced install methods revealed (M/D).")
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
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().start_flash_session(
                package_name=self._package_name or Path(self._package_path).name,
                method=self._flash_method,
                model=self._package_model,
            )
        except Exception:
            pass
        self._retry_guidance_shown = False
        try:
            self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        except ValueError:
            self.sm.reset_full()
            self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._flash_page.set_method(self._flash_method)
        self._flash_page.show_preparing()
        self._nav_to_page(_PAGE_FLASH)
        self._append_log(
            f"Starting flash for {self._package_name} "
            f"(method: {_method_label(self._flash_method)})"
        )
        pre_extracted = completed_extract_dir(self._package_path)
        try:
            from ..flash_service import compute_extract_dir, _find_scatter
            from ..sp_flash_gui import update_sp_history_ini

            ed = pre_extracted or compute_extract_dir(self._package_path)
            sc = _find_scatter(Path(ed)) if Path(ed).is_dir() else None
            update_sp_history_ini(
                scatter_path=sc,
                extract_dir=Path(ed) if Path(ed).is_dir() else None,
                model=self._package_model,
            )
        except Exception as e:
            logger.debug("Could not pre-update SP history.ini in _begin_flash_flow: %s", e)
        self.service.start_flash(
            self._package_path,
            pre_extracted_dir=pre_extracted,
            method=self._flash_method,
            model=self._package_model,
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
            self._flash_page.highlight_guide_step(1)
            # Still waiting for the device: the method may be switched.
            self._settings_page.set_method_enabled(True)
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step == STEP_DETECT:
            self._flash_page.set_detected()
            self._set_state(FlashState.S3_DEVICE_DETECTED)
        elif step in _WRITE_STEPS:
            self._flash_page.show_flashing()
            self._flash_page.set_device_flashing()
            self._settings_page.set_method_enabled(False)
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
        # Backend/tool channel: a tool that prints a line and also reports it
        # through its callback would otherwise store the same event twice.
        self._append_log(msg, dedupe=True)

    def _append_log(self, msg, dedupe=False):
        self._log_lines.append(msg)
        if len(self._log_lines) > 2000:
            self._log_lines = self._log_lines[-2000:]
        try:
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().record_log(msg, dedupe=dedupe)
        except Exception:
            pass
        self.log_line_added.emit(msg)
        self._handle_backend_line(msg)

    def _handle_backend_line(self, line):
        """React to raw backend output with the right amount of UI.

        Only two kinds of line ever need a reaction (see
        :func:`flash_service.classify_backend_line`):

        * mtkclient's ``Hint:`` block is not an error — it restates step 1 of
          the connection guide, so it just re-arms the waiting stage.
        * errno 2 / 5 mean the player is wedged and must be reset by hand, so
          they raise the retry-guidance dialog.

        Everything else stays Diagnostics-only, which is where the raw output
        belongs.
        """
        kind = classify_backend_line(line)
        if not kind or not self._package_path:
            return  # No run in progress — log only.
        if kind == LINE_CONNECT_HINT:
            self._show_connect_guide_stage_one()
        elif kind == LINE_RETRY:
            self._show_retry_guidance(
                tr("retry_guidance_detail_errno").format(code=str(line).strip())
            )

    def _show_connect_guide_stage_one(self):
        """Return the UI to "power off the device and connect USB".

        Guarded to the waiting states: a hint that arrives once writing has
        started must not yank the user back to step 1 mid-flash.
        """
        if self.sm.state not in (
            FlashState.S2_WAIT_CONNECTION,
            FlashState.S3_DEVICE_DETECTED,
        ):
            return
        self._flash_page.set_waiting_device()
        self._flash_page.highlight_guide_step(1)
        self._set_state(FlashState.S2_WAIT_CONNECTION)
        if self._stack.currentIndex() != _PAGE_FLASH:
            self._nav_to_page(_PAGE_FLASH)

    def _show_retry_guidance(self, detail=""):
        """Explain the manual reset, at most once per flash attempt."""
        if self._retry_guidance_shown:
            return
        self._retry_guidance_shown = True
        dialog = RetryGuidanceDialog(self, detail=detail)
        dialog.exec()
        if dialog.want_retry():
            self._on_retry_flash()

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
        try:
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().end_flash_session(ok, str(error_code or ""))
        except Exception:
            pass
        if ok:
            self._handle_flash_success()
        else:
            self._handle_flash_failure(error_code)

    def _handle_flash_success(self):
        try:
            self.sm.transition_to(FlashState.S5_COMPLETE)
        except ValueError:
            self.sm.force_state(FlashState.S5_COMPLETE)

        model = self._package_model or ""
        software = self._package_name or "Firmware"
        steps = install_power_on_steps(model)
        self._show_status(
            tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
        )

        # Record install for device tracking & future release reminders
        rel_info = getattr(self._select_page, "current_installed_release_info", lambda: None)()
        if rel_info:
            device_tracking.record_device_install(
                model=rel_info.get("model") or model,
                software_name=rel_info.get("software_name") or software,
                tag_name=rel_info.get("tag_name") or "",
                release_label=rel_info.get("release_label") or "",
                package_slug=rel_info.get("package_slug") or "",
                published_at=rel_info.get("published_at") or "",
                settings=self.settings,
            )
        elif self._package_name:
            device_tracking.record_device_install(
                model=model,
                software_name=software,
                tag_name="local",
                release_label=software,
                settings=self.settings,
            )
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()

        donation_disabled = device_tracking.is_donation_install_prompt_disabled(self.settings)
        eff_model = (model or self._package_model or "").upper()
        is_y_target = eff_model in ("Y1", "Y2")
        pkg_low = (self._package_name or "").lower()
        path_low = (self._package_path or "").lower()
        is_rockbox = "rockbox" in pkg_low or "rockbox" in path_low
        is_240p = "_240p" in path_low or "240p" in pkg_low
        is_360p_rockbox = bool(is_y_target and is_rockbox and not is_240p)

        self._is_360p_rockbox = is_360p_rockbox

        if donation_disabled:
            dialog = FlashCompleteDialog(
                self, software, self._elapsed_text(), model=model, is_360p_rockbox=is_360p_rockbox
            )
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
        if error_code in _RETRY_GUIDANCE_CODES:
            self._show_retry_guidance(
                tr("retry_guidance_detail_failed").format(code=error_code)
            )

    def _on_retry_flash(self):
        self.sm.reset_for_retry()
        try:
            from ..sp_flash_gui import update_sp_history_ini
            update_sp_history_ini(model=self._package_model)
        except Exception as e:
            logger.debug("Could not update SP history.ini on retry: %s", e)
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
        """Apply an install method chosen in Settings.

        The choice is always persisted. A run that is waiting for the device is
        restarted so the new backend takes over immediately; otherwise the new
        method applies from the next run.
        """
        if method == self._flash_method:
            return
        self._flash_method = method
        self.settings.setValue("flash_method", method)
        if method == METHOD_MTK_MAC:
            # Simulated macOS: most of that behaviour is decided at startup,
            # so offer the restart that actually switches the app over.
            self._append_log("Install method set to MTKClient (Mac) simulation.")
            self._restart_in_simulated_macos()
            return
        if self.sm.state not in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            self._append_log(f"Install method set to {_method_label(method)}.")
            return
        if method in ("sp", "auto"):
            try:
                from ..sp_flash_gui import update_sp_history_ini
                update_sp_history_ini(model=self._package_model)
            except Exception as e:
                logger.debug("Could not update SP history.ini on method change: %s", e)
        self._append_log(f"Flash method changed to {_method_label(method)}; restarting search...")
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._flash_page.set_method(method)
        pre_extracted = completed_extract_dir(self._package_path)
        self.service.start_flash(
            self._package_path, pre_extracted_dir=pre_extracted, method=method
        )
        self.service.start_device_monitor()

    def _restart_in_simulated_macos(self):
        """Relaunch in simulated macOS mode so the Mac flow genuinely applies.

        paths.SIMULATE_MACOS is read at import time and drives the backend
        matrix, the mac-centric prompts and the nav entries, so switching at
        runtime would leave a half-Mac UI. Restarting is the honest way in.
        """
        box = QMessageBox(self)
        box.setWindowTitle(tr("simulated_mac_title"))
        box.setText(tr("simulated_mac_body"))
        restart_btn = box.addButton(
            tr("simulated_mac_restart_now"), QMessageBox.AcceptRole
        )
        box.addButton(tr("simulated_mac_later"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not restart_btn:
            return

        if getattr(sys, "frozen", False):
            program, args = sys.executable, sys.argv[1:]
        else:
            program, args = sys.executable, [sys.argv[0], *sys.argv[1:]]
        env = QProcessEnvironment.systemEnvironment()
        env.insert("INNIOASIS_SIMULATE_MACOS", "1")
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(os.getcwd())
        proc.setProcessEnvironment(env)
        if not proc.startDetached():
            self._append_log(
                "Could not restart automatically; relaunch with --simulate-macos."
            )
            return
        QApplication.quit()

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
        self._settings_page.set_method_enabled(True)
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

    def _open_sp_flash_tool_gui(self):
        from ..config import device_label_for_model
        from ..sp_flash_gui import is_sp_flash_gui_supported, launch_sp_flash_tool_gui

        if not is_sp_flash_gui_supported():
            QMessageBox.information(
                self,
                tr("sp_gui_title"),
                tr("sp_gui_not_supported"),
            )
            return

        model = self._package_model or ""

        from .. import device_tracking
        from ..flash_service import completed_extract_dir, compute_extract_dir, _find_scatter
        from pathlib import Path

        scatter_path = None
        extract_dir = None
        pkg_path = getattr(self, "_package_path", None)
        if not pkg_path and hasattr(self, "_select_page"):
            pkg_path = getattr(self._select_page, "_current_package_path", None)
        if pkg_path:
            ed = completed_extract_dir(pkg_path) or compute_extract_dir(pkg_path)
            if Path(ed).is_dir():
                extract_dir = Path(ed)
                sc = _find_scatter(extract_dir)
                if sc:
                    scatter_path = sc
        if not scatter_path:
            latest = device_tracking.get_latest_package(self.settings)
            if latest:
                if latest.get("scatter_path") and Path(latest["scatter_path"]).is_file():
                    scatter_path = Path(latest["scatter_path"])
                if latest.get("extract_dir") and Path(latest["extract_dir"]).is_dir():
                    extract_dir = Path(latest["extract_dir"])
                if not self._package_model and latest.get("model"):
                    model = latest["model"]

        label = device_label_for_model(model)

        prompt_msg = (
            f"{tr('sp_gui_launch_intro')}\n\n"
            f"• If it isn't already off, power off your {label}.\n"
            f"• If it is connected to USB, disconnect it first.\n"
            f"• In SP Flash Tool, click Download (or Format All + Download as needed).\n\n"
            f"Then connect the USB cable to begin flashing."
        )
        reply = QMessageBox.question(
            self,
            tr("sp_gui_title"),
            prompt_msg,
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok,
        )
        if reply != QMessageBox.Ok:
            return

        if hasattr(self, "service") and self.service:
            self.service.cancel_flash()

        ok, msg = launch_sp_flash_tool_gui(model=model, scatter_path=scatter_path, extract_dir=extract_dir)
        if not ok:
            QMessageBox.warning(
                self,
                tr("sp_gui_error_title"),
                f"{tr('sp_gui_error_desc')}\n\n{msg}",
            )
        else:
            self._show_status(tr("sp_gui_launched_status"), 10000)

    def _check_linux_first_run(self):
        if paths.IS_MAC or os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("CI"):
            return
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
        from ..browser import open_browser
        open_browser("https://innioasis.app/credits.html?thank-you=1")

    def _on_language_changed(self, index):
        lang = self._lang_combo.itemData(index)
        translator().set_language(lang)
        self.settings.setValue("language", lang)
        self._retranslate_all()

    def _retranslate_all(self):
        app_name = get_app_name()
        self._brand_label.setText(app_name)
        self.setWindowTitle(f"{app_name} v{APP_VERSION}")
        for key, (btn, _idx) in self._nav_buttons.items():
            btn.setText(tr(key))
        self._support_btn.setText(tr("nav_donate"))
        self._log_btn.setText(tr("nav_log"))
        self._credits_btn.setText(tr("nav_credits"))
        self._check_updates_btn.setText(tr("nav_check_updates"))
        if hasattr(self, "_linux_setup_btn"):
            self._linux_setup_btn.setText(tr("nav_linux_setup"))
        if hasattr(self, "_sp_flash_tool_btn"):
            self._sp_flash_tool_btn.setText(tr("nav_sp_flash_tool_gui"))
        self._lang_label.setText(tr("nav_language"))
        self._select_page.retranslate()
        self._flash_page.retranslate()
        self._error_page.retranslate()
        self._retry_page.retranslate()
        if hasattr(self, "_settings_page"):
            self._settings_page.retranslate()
        if self.statusBar() and hasattr(self.statusBar(), "retranslate"):
            self.statusBar().retranslate()
        self._apply_generic_mtk_branding()

    def _on_manifest_loaded(self, entries):
        if entries:
            # The live catalogue defines which models are offered; re-filter the
            # drop-down before repopulating the software list for the current
            # model.
            self._select_page.refresh_models()
            self._select_page._on_model_changed()
            # Check for device firmware updates when new releases/manifest is loaded
            self._check_device_firmware_updates(manual=False)

    def _start_auto_update_check(self):
        if self._update_offered_once:
            return
        self._update_manual_pending = False
        self._run_update_check()

    def _on_check_updates_clicked(self):
        self._update_manual_pending = True
        self._run_update_check()

    def _run_update_check(self):
        # Stop any in-flight check before replacing the reference; overwriting a
        # running QThread causes "Destroyed while thread is still running"/SIGABRT.
        old = getattr(self, "_update_worker", None)
        if old is not None and old.isRunning():
            old.requestInterruption()
            old.wait(800)
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

    def _check_device_firmware_updates(self, manual=False):
        old = getattr(self, "_device_update_worker", None)
        if old is not None and old.isRunning():
            old.requestInterruption()
            old.wait(800)
        worker = DeviceUpdateCheckWorker(self.settings, ignore_last_notified=manual, parent=self)
        worker.finished.connect(lambda updates: self._on_device_updates_checked(updates, manual))
        self._device_update_worker = worker
        worker.start()

    def _on_device_updates_checked(self, updates, manual=False):
        if updates:
            for upd in updates:
                def on_start(info):
                    self._nav_to_page(_PAGE_SELECT)
                    self._select_page.start_install_for_release(
                        model=info.get("model", "Y1"),
                        software_name=info.get("software_name", ""),
                        tag_name=info.get("latest_tag"),
                        release=info.get("latest_release"),
                        package=info.get("package"),
                    )

                def on_view(info):
                    self._nav_to_page(_PAGE_SELECT)
                    self._select_page.navigate_to_package(
                        info.get("model", "Y1"),
                        info.get("software_name", ""),
                        tag_name=info.get("latest_tag"),
                    )

                def on_disable(model):
                    device_tracking.set_device_reminder_enabled(model, False, settings=self.settings)
                    if hasattr(self, "_settings_page"):
                        self._settings_page.refresh_settings()

                dlg = ReleaseReminderDialog(
                    parent=self,
                    update_info=upd,
                    on_view_release=on_view,
                    on_disable_reminders=on_disable,
                    on_start_install=on_start,
                    flash_method=self._flash_method,
                )
                dlg.exec()
                device_tracking.set_last_notified_tag(upd.get("model"), upd.get("latest_tag"), settings=self.settings)
                if getattr(dlg, "_install_started", False):
                    break
        elif manual:
            tracked = device_tracking.get_all_device_installs(self.settings)
            if tracked:
                QMessageBox.information(
                    self,
                    tr("reminder_new_release_title"),
                    tr("settings_firmware_up_to_date"),
                )
            else:
                QMessageBox.information(
                    self,
                    tr("reminder_new_release_title"),
                    tr("settings_no_devices_tracked"),
                )

    def _show_status(self, text, timeout_ms=0):
        """Show a transient message in the status bar; it reverts to the
        donations/goal display once cleared or timed out."""
        sb = self.statusBar()
        if sb is None:
            return
        sb.showMessage(text, int(timeout_ms or 0))
        if not sb.isVisible():
            sb.setVisible(True)

    def _on_status_message_changed(self, text):
        if not text:
            self._apply_donation_visibility()

    def _apply_donation_visibility(self, is_disabled=None):
        if is_generic_mtk():
            is_disabled = True
        elif is_disabled is None:
            is_disabled = device_tracking.is_donation_ui_disabled(self.settings)
        sb = self.statusBar()
        if sb is not None:
            if hasattr(sb, "set_donations_enabled"):
                sb.set_donations_enabled(not is_disabled)
            else:
                sb.setVisible(not is_disabled)
        if hasattr(self, "_support_btn") and self._support_btn is not None:
            self._support_btn.setVisible(not is_disabled and not is_generic_mtk())

    def _apply_generic_mtk_branding(self):
        generic = is_generic_mtk()
        app_name = get_app_name()
        self.setWindowTitle(f"{app_name} v{APP_VERSION}")
        if hasattr(self, "_brand_label"):
            self._brand_label.setText(app_name)
        if hasattr(self, "_credits_btn"):
            self._credits_btn.setVisible(not generic)
        if hasattr(self, "_check_updates_btn"):
            self._check_updates_btn.setVisible(not generic)
        if hasattr(self, "_version_label"):
            self._version_label.setVisible(not generic)
        if hasattr(self, "_select_page"):
            self._select_page.apply_generic_mode(generic)
        self._apply_donation_visibility()

    def _on_offline_mode_changed(self, enabled: bool):
        self._apply_generic_mtk_branding()

    def _on_donations_updated(self, donations):
        if donations:
            self._donations = donations

    def _on_support_clicked(self):
        self._show_donation_dialog(context="general")

    def _show_donation_dialog(self, context="general", *args, **kwargs):
        eff_model = self._package_model or ""
        eff_name = self._package_name or ""
        is_360p_rockbox = kwargs.get("is_360p_rockbox")
        if is_360p_rockbox is None:
            is_360p_rockbox = getattr(self, "_is_360p_rockbox", None)
        if is_360p_rockbox is None:
            is_y = eff_model.upper() in ("Y1", "Y2")
            pkg_low = eff_name.lower()
            path_low = (self._package_path or "").lower()
            is_rb = "rockbox" in pkg_low or "rockbox" in path_low
            is_240 = "_240p" in path_low or "240p" in pkg_low
            is_360p_rockbox = bool(is_y and is_rb and not is_240) if context == "install_success" else False
        dialog = DonationDialog(
            parent=self,
            context=context,
            model=eff_model,
            software_name=eff_name,
            donations=self._donations,
            on_dont_ask_again=self._on_donation_dont_ask_again,
            is_360p_rockbox=is_360p_rockbox,
        )
        dialog.exec()

    def _on_donation_dont_ask_again(self):
        """"Don't ask me again" on the Support dialog: the user is not
        interested in the donations model at all, so also turn off the
        donor / donation info shown at the bottom of the window (the
        Settings → hide-donations option) and reflect both immediately."""
        device_tracking.set_donation_install_prompt_disabled(True, settings=self.settings)
        device_tracking.set_donation_ui_disabled(True, settings=self.settings)
        self._apply_donation_visibility(True)
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()

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
            mon = getattr(select_page, "_monitor", None)
            if mon is not None:
                mon.stop_monitoring()
        for w in (
            getattr(self, "_manifest_worker", None),
            getattr(self, "_update_worker", None),
            getattr(self, "_device_update_worker", None),
            getattr(select_page, "_manifest_worker", None) if select_page else None,
        ):
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(1500)

    def close(self):
        self.cleanup_workers()
        return super().close()

    def closeEvent(self, event):
        self.cleanup_workers()
        super().closeEvent(event)


_METHOD_LABELS = {
    "auto": "Auto",
    "sp": "SP Flash Tool",
    "mtk": "MTKClient",
    "mtk_mac": "MTKClient (Mac)",
}


def _method_label(method: str) -> str:
    if paths.IS_MAC:
        return "MTKClient"
    return _METHOD_LABELS.get(method, method)
