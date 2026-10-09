"""Flash page — modern OS software update in-progress display (macOS / iOS / Windows Fluent style)."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame,
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
    METHOD_MTK,
    METHOD_MTK_MAC,
    METHOD_SP,
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_WAITING,
    STEP_WRITE,
    default_flash_method,
    normalise_method,
)
from ..i18n import tr
from .widgets import Banner, Card, InfoRow, StatusTag
from .dark import T, page_top_margin
from .icons import get_symbol_icon

_STEP_KEY = {
    STEP_EXTRACTING: "step_extract",
    STEP_WAITING: "step_wait",
    STEP_DETECT: "step_detect",
    STEP_DOWNLOAD_DA: "step_download_da",
    STEP_DOWNLOAD_BL: "step_download_bl",
    STEP_WRITE: "step_write",
    STEP_DONE: "step_done",
}


def _make_squircle_icon(symbol: str, asset_name: str = "") -> QLabel:
    """Create a macOS / iOS Settings-style rounded squircle icon container."""
    lbl = QLabel()
    lbl.setFixedSize(40, 40)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet(
        "background-color: rgba(128, 128, 128, 0.16);"
        "border-radius: 10px;"
        "font-size: 20px;"
    )
    if asset_name:
        for base in (paths.RESOURCES_DIR, paths.REPO_ROOT / "assets"):
            p = base / asset_name
            if p.exists():
                pm = QPixmap(str(p))
                if not pm.isNull():
                    scaled = pm.scaled(26, 26, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    lbl.setPixmap(scaled)
                    return lbl
    lbl.setText(symbol)
    return lbl


class FlashPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_callback = None
        self._cancel_wait_callback = None
        self._cancel_download_callback = None
        self._open_sp_gui_callback = None
        self._model = ""
        self._package_name = ""
        self._wait_banner_key = ""
        self._step_key = ""
        self._prep_step_key = ""
        self._conn_value_key = ""
        self._dev_value_key = ""
        # Install method is chosen in Settings; this page only reports it.
        self._method = default_flash_method()
        # 0 = no step emphasised. Set by highlight_guide_step() when the
        # backend tells us the device is not in flash mode yet.
        self._guide_step = 0
        self._build_ui()

    def _build_ui(self):
        self._headings = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        self._stack = QStackedWidget()
        self._preparing_view = self._build_preparing_view()
        self._waiting_view = self._build_waiting_view()
        self._flashing_view = self._build_flashing_view()
        self._downloading_view = self._build_downloading_view()
        for w in (self._preparing_view, self._waiting_view, self._flashing_view, self._downloading_view):
            self._stack.addWidget(w)
        layout.addWidget(self._stack, 1)

    def _style_cancel_button(self, btn: QPushButton):
        t = T()
        btn.setFixedSize(24, 24)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setIcon(get_symbol_icon("cancel", 12))
        btn.setIconSize(QSize(12, 12))
        btn.setText("")
        btn.setStyleSheet(
            f"QPushButton#softwareUpdateCancelBtn {{"
            f"  background: {t.bg_hover}; color: {t.fg}; border: none; border-radius: 12px;"
            f"}}"
            f"QPushButton#softwareUpdateCancelBtn:hover {{"
            f"  background: {t.border};"
            f"}}"
        )

    def _wrap_centered(self, card_widget: QWidget, extra_below: QWidget | None = None) -> QWidget:
        """Center the software update card with heading and generous spacing."""
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.addStretch(1)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)

        card_col = QVBoxLayout()
        card_col.setContentsMargins(0, 0, 0, 0)
        card_col.setSpacing(8)

        heading = QLabel(tr("flash_install_in_progress"))
        t = T()
        heading.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {t.fg}; background: transparent; border: none; margin: 0; padding: 0;"
        )
        self._headings.append(heading)
        card_col.addWidget(heading, 0, Qt.AlignLeft)

        card_col.addWidget(card_widget, 0, Qt.AlignCenter)
        if extra_below is not None:
            card_col.addWidget(extra_below, 0, Qt.AlignCenter)

        h.addLayout(card_col)
        h.addStretch(1)
        v.addLayout(h)
        v.addStretch(1)
        return container

    def _build_preparing_view(self):
        self._prep_card = Card()
        self._prep_card.setFixedWidth(470)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._prep_icon = _make_squircle_icon("📦", "icon.png")
        card_layout.addWidget(self._prep_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._prep_pkg_label = QLabel(self._package_name or "\u2014")
        self._prep_pkg_label.setStyleSheet("font-size: 13px; font-weight: 600; background: transparent; border: none;")
        col.addWidget(self._prep_pkg_label)

        self._prep_banner = Banner()
        self._prep_banner.setVisible(False)

        self._prep_progress = QProgressBar()
        self._prep_progress.setObjectName("softwareUpdateProgress")
        self._prep_progress.setRange(0, 100)
        self._prep_progress.setFixedHeight(6)
        self._prep_progress.setTextVisible(False)
        col.addWidget(self._prep_progress)

        self._prep_step = QLabel(tr("step_extract"))
        self._prep_step.setProperty("cssClass", "field-label")
        self._prep_step.setStyleSheet("font-size: 12px; color: palette(placeholder-text); background: transparent;")
        col.addWidget(self._prep_step)

        card_layout.addLayout(col, 1)

        self._prep_cancel_btn = QPushButton("✕")
        self._prep_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._style_cancel_button(self._prep_cancel_btn)
        self._prep_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._prep_cancel_btn.clicked.connect(self._on_cancel_wait)
        card_layout.addWidget(self._prep_cancel_btn, 0, Qt.AlignVCenter)

        self._prep_card.set_layout(card_layout)

        # Guidance images are removed; keep dummy attribute for backward compatibility
        self._prep_img = QLabel()
        self._prep_img.setVisible(False)

        return self._wrap_centered(self._prep_card)

    def _build_waiting_view(self):
        self._wait_card = Card()
        self._wait_card.setFixedWidth(470)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._wait_icon = _make_squircle_icon("🔌", "icon.png")
        card_layout.addWidget(self._wait_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._wait_pkg_label = QLabel(self._package_name or "\u2014")
        self._wait_pkg_label.setStyleSheet("font-size: 13px; font-weight: 600; background: transparent; border: none;")
        col.addWidget(self._wait_pkg_label)

        self._wait_progress_bar = QProgressBar()
        self._wait_progress_bar.setObjectName("softwareUpdateProgress")
        self._wait_progress_bar.setRange(0, 100)
        self._wait_progress_bar.setValue(0)
        self._wait_progress_bar.setFixedHeight(6)
        self._wait_progress_bar.setTextVisible(False)
        col.addWidget(self._wait_progress_bar)

        self._wait_prompt_label = QLabel(tr("flash_connect_device_prompt").format(model=self._connect_model_text()))
        self._wait_prompt_label.setStyleSheet("font-size: 12px; color: palette(placeholder-text); background: transparent;")
        col.addWidget(self._wait_prompt_label)

        self._wait_banner = Banner()
        self._wait_banner.setVisible(False)
        self._wait_status = StatusTag("idle")
        self._wait_status.setVisible(False)
        self._method_note = QLabel("")
        self._method_note.setVisible(False)

        card_layout.addLayout(col, 1)

        self._wait_cancel_btn = QPushButton("✕")
        self._wait_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._style_cancel_button(self._wait_cancel_btn)
        self._wait_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._wait_cancel_btn.clicked.connect(self._on_cancel_wait)
        card_layout.addWidget(self._wait_cancel_btn, 0, Qt.AlignVCenter)

        self._wait_card.set_layout(card_layout)

        below_widget = QWidget()
        below_layout = QVBoxLayout(below_widget)
        below_layout.setContentsMargins(0, 0, 0, 0)
        below_layout.setSpacing(6)

        from ..sp_flash_gui import is_sp_flash_gui_supported
        if is_sp_flash_gui_supported():
            self._open_sp_gui_btn = QPushButton(tr("flash_btn_open_sp_gui"))
            self._open_sp_gui_btn.setToolTip(tr("flash_sp_gui_tooltip"))
            self._open_sp_gui_btn.clicked.connect(self._on_open_sp_gui_clicked)
            below_layout.addWidget(self._open_sp_gui_btn, 0, Qt.AlignCenter)

        # Guidance images are removed; keep dummy attributes for backward compatibility
        self._status_img = QLabel()
        self._status_img.setVisible(False)
        self._guide_title = QLabel(tr("flash_guide_title"))
        self._guide_texts = []
        for key in ("flash_guide_1", "flash_guide_2", "flash_guide_3", "flash_guide_4"):
            text = QLabel(tr(key))
            text.setWordWrap(True)
            self._guide_texts.append((key, text))

        return self._wrap_centered(self._wait_card, below_widget if is_sp_flash_gui_supported() else None)

    def _build_flashing_view(self):
        self._progress_card = Card()
        self._progress_card.setFixedWidth(470)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._flash_icon = _make_squircle_icon("⚙️", "icon.png")
        card_layout.addWidget(self._flash_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._flash_pkg_label = QLabel(self._package_name or "\u2014")
        self._flash_pkg_label.setStyleSheet("font-size: 13px; font-weight: 600; background: transparent; border: none;")
        col.addWidget(self._flash_pkg_label)

        self._flash_banner = Banner()
        self._flash_banner.setVisible(False)

        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("softwareUpdateProgress")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        col.addWidget(self._progress_bar)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(6)

        self._step_label = QLabel(tr("step_write"))
        self._step_label.setProperty("cssClass", "field-label")
        self._step_label.setStyleSheet("font-size: 12px; color: palette(placeholder-text); background: transparent;")
        sub_row.addWidget(self._step_label)

        self._eta_label = QLabel("")
        self._eta_label.setStyleSheet("font-size: 12px; color: palette(placeholder-text); background: transparent;")
        sub_row.addWidget(self._eta_label)
        sub_row.addStretch(1)

        col.addLayout(sub_row)
        card_layout.addLayout(col, 1)

        self._cancel_btn = QPushButton("✕")
        self._cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._style_cancel_button(self._cancel_btn)
        self._cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._cancel_btn.clicked.connect(self._on_cancel)
        card_layout.addWidget(self._cancel_btn, 0, Qt.AlignVCenter)

        self._progress_card.set_layout(card_layout)

        # Warning below the update card
        warning_container = QWidget()
        w_layout = QVBoxLayout(warning_container)
        w_layout.setContentsMargins(0, 0, 0, 0)
        self._warning = QLabel(tr("flash_warning"))
        self._warning.setWordWrap(True)
        self._warning.setAlignment(Qt.AlignCenter)
        self._warning.setProperty("cssClass", "warning-banner")
        self._warning.setStyleSheet("font-size: 12px; color: palette(placeholder-text); background: transparent;")
        w_layout.addWidget(self._warning, 0, Qt.AlignCenter)

        # Dummy attributes for backward compatibility
        self._flash_img = QLabel()
        self._flash_img.setVisible(False)
        self._action_label = QLabel("")
        self._action_label.setVisible(False)
        self._status_card = Card("flash_status_panel")
        self._status_card.setVisible(False)
        self._conn_row = InfoRow("flash_conn_status")
        self._dev_row = InfoRow("flash_device_status")
        self._pkg_row = InfoRow("flash_current_pkg")
        self._elapsed_row = InfoRow("flash_elapsed")
        self._eta_row = InfoRow("flash_eta")

        return self._wrap_centered(self._progress_card, warning_container)

    def _build_downloading_view(self):
        self._download_card = Card()
        self._download_card.setFixedWidth(470)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._download_icon = _make_squircle_icon("📥", "icon.png")
        card_layout.addWidget(self._download_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._download_pkg_label = QLabel(self._package_name or "\u2014")
        self._download_pkg_label.setStyleSheet("font-size: 13px; font-weight: 600; background: transparent; border: none;")
        col.addWidget(self._download_pkg_label)

        self._download_progress = QProgressBar()
        self._download_progress.setObjectName("softwareUpdateProgress")
        self._download_progress.setRange(0, 100)
        self._download_progress.setValue(0)
        self._download_progress.setFixedHeight(6)
        self._download_progress.setTextVisible(False)
        col.addWidget(self._download_progress)

        self._download_status_label = QLabel(tr("sel_download_start"))
        self._download_status_label.setProperty("cssClass", "field-label")
        self._download_status_label.setStyleSheet("font-size: 12px; color: palette(placeholder-text); background: transparent;")
        col.addWidget(self._download_status_label)

        card_layout.addLayout(col, 1)

        self._download_cancel_btn = QPushButton("✕")
        self._download_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._style_cancel_button(self._download_cancel_btn)
        self._download_cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._download_cancel_btn.clicked.connect(self._on_cancel_download)
        card_layout.addWidget(self._download_cancel_btn, 0, Qt.AlignVCenter)

        self._download_card.set_layout(card_layout)

        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.addStretch(1)
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)

        card_col = QVBoxLayout()
        card_col.setContentsMargins(0, 0, 0, 0)
        card_col.setSpacing(8)

        self._download_heading = QLabel(tr("flash_download_in_progress"))
        t = T()
        self._download_heading.setStyleSheet(
            f"font-size: 14px; font-weight: 700; color: {t.fg}; background: transparent; border: none; margin: 0; padding: 0;"
        )
        card_col.addWidget(self._download_heading, 0, Qt.AlignLeft)
        card_col.addWidget(self._download_card, 0, Qt.AlignCenter)

        h.addLayout(card_col)
        h.addStretch(1)
        v.addLayout(h)
        v.addStretch(1)
        return container

    def _switch_view(self, target_view):
        for v in (self._preparing_view, self._waiting_view, self._flashing_view, self._downloading_view):
            if v is not target_view:
                v.hide()
        target_view.show()
        self._stack.setCurrentWidget(target_view)
        self.update()

    def show_downloading(self):
        val = self._package_name or "\u2014"
        if hasattr(self, "_download_pkg_label"):
            self._download_pkg_label.setText(val)
        self._download_progress.setValue(0)
        self._download_status_label.setText(tr("sel_download_start"))
        self._switch_view(self._downloading_view)

    def update_download_progress(self, percent: int, status_text: str = ""):
        self._download_progress.setValue(max(0, min(100, int(percent))))
        if status_text:
            self._download_status_label.setText(status_text)

    def set_cancel_download_callback(self, cb):
        self._cancel_download_callback = cb

    def _on_cancel_download(self):
        if callable(self._cancel_download_callback):
            self._cancel_download_callback()

    def _load_image(self, label, name, target_height=None):
        """Guidance images during firmware installs are removed in favor of native UI."""
        pass

    def show_preparing(self):
        self._switch_view(self._preparing_view)
        self._prep_banner.set_key("flash_preparing")
        self._prep_step_key = "step_extract"
        self._prep_step.setText(tr("step_extract"))

    def show_waiting(self):
        self._switch_view(self._waiting_view)

    def show_flashing(self):
        self._switch_view(self._flashing_view)
        self._warning.setVisible(True)

    def set_method(self, method):
        """Record the install method chosen in Settings for this run."""
        self._method = normalise_method(method)
        self._update_method_note()

    def _initsteps_image(self):
        if paths.IS_MAC:
            return "initsteps.png"
        if self._method == METHOD_SP:
            return "initsteps_sp.png"
        if self._method == METHOD_MTK:
            return "initsteps_win.png" if paths.IS_WINDOWS else "initsteps.png"
        return "initsteps.png"

    def current_method(self):
        return self._method

    def _update_method_note(self):
        if self._method == METHOD_SP:
            note = tr("flash_method_note_sp")
        elif self._method == METHOD_MTK_MAC:
            note = tr("flash_method_note_mtk_mac")
        else:
            note = ""
        self._method_note.setText(note)
        self._method_note.setVisible(bool(note))

    def highlight_guide_step(self, step):
        """Emphasise one numbered step of the connection guide."""
        self._guide_step = int(step or 0)
        self._apply_guide_highlight()

    def _apply_guide_highlight(self):
        t = T()
        for idx, (_key, label) in enumerate(self._guide_texts, start=1):
            if idx == self._guide_step:
                label.setStyleSheet(f"color: {t.fg}; font-weight: 600;")
            else:
                label.setStyleSheet("")

    def set_model(self, model):
        self._model = (model or "").strip() or ""

    def set_package_name(self, name):
        self._package_name = name or ""
        val = self._package_name or "\u2014"
        if hasattr(self, "_prep_pkg_label"):
            self._prep_pkg_label.setText(val)
        if hasattr(self, "_wait_pkg_label"):
            self._wait_pkg_label.setText(val)
        if hasattr(self, "_flash_pkg_label"):
            self._flash_pkg_label.setText(val)
        if hasattr(self, "_download_pkg_label"):
            self._download_pkg_label.setText(val)
        self._pkg_row.set_value(val)

    def _connect_model_text(self):
        from ..config import device_label_for_model
        return device_label_for_model(self._model) if self._model else tr("device_fallback_word")

    def set_waiting_device(self):
        self._wait_status.set_status("idle")
        prompt = tr("flash_connect_device_prompt").format(model=self._connect_model_text())
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(prompt)
        self._wait_banner.set_type("info")
        self._wait_banner_key = "connect"
        self._wait_banner.set_key("flash_connect_device_prompt", model=self._connect_model_text())
        self._conn_value_key = "flash_conn_waiting"
        self._conn_row.set_value(tr("flash_conn_waiting"))

    def set_searching(self):
        self._wait_banner.set_type("info")
        self._wait_banner_key = "searching"
        self._wait_status.set_status("idle")

    def set_detected(self):
        self._wait_status.set_status("connected")
        self._wait_banner.set_type("success")
        self._wait_banner_key = "ready"
        self._wait_banner.set_key("flash_banner_ready")

    def set_device_flashing(self):
        self._wait_status.set_status("flashing")
        self._flash_banner.set_type("info")
        self._flash_banner.set_key("flash_banner_flashing")
        self._conn_value_key = "flash_conn_connected"
        self._dev_value_key = "status_connected"
        self._conn_row.set_value(tr("flash_conn_connected"))
        self._dev_row.set_value(tr("status_connected"))
        self._warning.setVisible(True)

    def set_device_done(self):
        self._warning.setVisible(False)
        self._flash_banner.set_type("success")
        self._flash_banner.set_key("flash_banner_done")
        self._wait_status.set_status("complete")
        self._progress_bar.setValue(100)
        self._eta_label.setText("")

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
        friendly = self._format_friendly_eta(eta)
        self._eta_label.setText(f"— {friendly}" if friendly else "")

    @staticmethod
    def _format_friendly_eta(eta_str: str) -> str:
        """Format an ETA like '00:53' into localized 'About 53 seconds remaining'."""
        if not eta_str or eta_str in ("--:--", "-"):
            return ""
        try:
            parts = eta_str.split(":")
            if len(parts) == 2:
                mins, secs = int(parts[0]), int(parts[1])
                total_secs = mins * 60 + secs
                if total_secs <= 0:
                    return ""
                if total_secs < 60:
                    key = "flash_eta_second_one" if total_secs == 1 else "flash_eta_seconds_many"
                    return tr(key).format(n=total_secs)
                m = round(total_secs / 60)
                m = max(m, 1)
                key = "flash_eta_minute_one" if m == 1 else "flash_eta_minutes_many"
                return tr(key).format(n=m)
        except Exception:
            pass
        return tr("flash_eta_remaining_fallback").format(eta=eta_str)

    def retranslate(self):
        for h in getattr(self, "_headings", []):
            h.setText(tr("flash_install_in_progress"))
        if hasattr(self, "_download_heading"):
            self._download_heading.setText(tr("flash_download_in_progress"))
        if hasattr(self, "_download_cancel_btn"):
            self._download_cancel_btn.setToolTip(tr("flash_btn_cancel"))
        if hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(
                tr("flash_connect_device_prompt").format(model=self._connect_model_text())
            )
        self._prep_banner.retranslate()
        if self._wait_banner_key == "connect":
            self._wait_banner.set_key("flash_connect_device_prompt", model=self._connect_model_text())
        else:
            self._wait_banner.retranslate()
        self._flash_banner.retranslate()
        self._guide_title.setText(tr("flash_guide_title"))
        for key, lbl in self._guide_texts:
            lbl.setText(tr(key))
        self._warning.setText(tr("flash_warning"))
        self._update_method_note()
        self._apply_guide_highlight()
        if self._prep_step_key:
            self._prep_step.setText(tr(self._prep_step_key))
        if self._step_key:
            self._step_label.setText(tr(self._step_key))
        if self._conn_value_key:
            self._conn_row.set_value(tr(self._conn_value_key))
        if self._dev_value_key:
            self._dev_row.set_value(tr(self._dev_value_key))
        if hasattr(self, "_prep_cancel_btn"):
            self._prep_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._wait_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._cancel_btn.setToolTip(tr("flash_btn_cancel"))
        self._wait_status.retranslate()
        self._prep_card.retranslate()
        self._progress_card.retranslate()
        self._status_card.retranslate()
        self._conn_row.retranslate()
        self._dev_row.retranslate()
        self._pkg_row.retranslate()
        self._elapsed_row.retranslate()
        self._eta_row.retranslate()
        if hasattr(self, "_open_sp_gui_btn"):
            self._open_sp_gui_btn.setText(tr("flash_btn_open_sp_gui"))
            self._open_sp_gui_btn.setToolTip(tr("flash_sp_gui_tooltip"))

    def refresh_theme(self):
        t = T()
        for h in getattr(self, "_headings", []):
            h.setStyleSheet(
                f"font-size: 14px; font-weight: 700; color: {t.fg}; background: transparent; border: none; margin: 0; padding: 0;"
            )
        for btn in (
            getattr(self, "_cancel_btn", None),
            getattr(self, "_wait_cancel_btn", None),
            getattr(self, "_prep_cancel_btn", None),
        ):
            if btn is not None:
                self._style_cancel_button(btn)

    def on_cancel(self, callback):
        self._cancel_callback = callback

    def on_cancel_wait(self, callback):
        self._cancel_wait_callback = callback

    def on_open_sp_gui(self, callback):
        self._open_sp_gui_callback = callback

    def _on_cancel(self):
        if self._cancel_callback:
            self._cancel_callback()

    def _on_cancel_wait(self):
        if self._cancel_wait_callback:
            self._cancel_wait_callback()

    def _on_open_sp_gui_clicked(self):
        if self._open_sp_gui_callback:
            self._open_sp_gui_callback()
