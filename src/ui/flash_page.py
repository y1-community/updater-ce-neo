"""Flash page — modern OS software update in-progress display (macOS / iOS / Windows Fluent style)."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
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
from .widgets import Banner, Card, CurrentPageStack, InfoRow, StatusTag
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


class _StatusLabel(QLabel):
    """Software title or status line on the progress card.

    A text change asks the card to paint its frost again first, so the new
    glyphs replace the old ones. The label itself stays transparent.
    """

    def setText(self, text):  # noqa: N802 (Qt naming)
        super().setText(text)
        host = self.parentWidget()
        while host is not None and not isinstance(host, Card):
            host = host.parentWidget()
        if host is not None:
            host.update()


def _status_label(text: str, *, strong: bool = False) -> _StatusLabel:
    from .dark import apply_readable_palette

    label = _StatusLabel(text)
    label.setMinimumHeight(18)
    font = label.font()
    font.setPixelSize(13 if strong else 12)
    font.setWeight(QFont.Weight.DemiBold if strong else QFont.Weight.Normal)
    label.setFont(font)
    label.setAutoFillBackground(False)
    label.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
    apply_readable_palette(label)
    label.setForegroundRole(QPalette.ColorRole.WindowText)
    return label


def _configure_progress(bar: QProgressBar) -> None:
    """A platform progress bar. No stylesheet, so Aqua / WinUI / Breeze paint it.

    The percent is visible in the native groove. A fixed 6px stylesheet bar
    was what disappeared on acrylic and never moved on screen.
    """
    from .dark import apply_readable_palette

    bar.setObjectName("softwareUpdateProgress")
    bar.setStyleSheet("")
    bar.setAutoFillBackground(False)
    apply_readable_palette(bar, progress=True)
    bar.setRange(0, 100)
    bar.setValue(0)
    bar.setTextVisible(True)
    bar.setFormat("%p%")
    bar.setMinimumHeight(18)
    bar.setMaximumHeight(24)
    bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


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
        self._failed = False
        self._complete = False
        self._prompt_only = False
        self._install_retry_cb = None
        self._power_off_continue_cb = None
        self._build_ui()

    def _build_ui(self):
        self._headings = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(8)

        self._stack = CurrentPageStack()
        self._preparing_view = self._build_preparing_view()
        self._waiting_view = self._build_waiting_view()
        self._flashing_view = self._build_flashing_view()
        self._downloading_view = self._build_downloading_view()
        for w in (self._preparing_view, self._waiting_view, self._flashing_view, self._downloading_view):
            self._stack.addWidget(w)
        layout.addWidget(self._stack, 1)
        self._seal_status_labels()

    def _style_cancel_button(self, btn: QPushButton):
        btn.setFixedSize(24, 24)
        btn.setCursor(Qt.ArrowCursor)
        btn.setIcon(get_symbol_icon("cancel", 12))
        btn.setIconSize(QSize(12, 12))
        btn.setText("")
        btn.setStyleSheet("")
        btn.setFlat(False)

    def _wrap_centered(self, card_widget: QWidget, extra_below: QWidget | None = None) -> QWidget:
        """Fill the content width with the progress card."""
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(4, 2, 4, 2)
        v.setSpacing(8)

        heading = QLabel(tr("flash_install_in_progress"))
        heading.setStyleSheet(
            "font-size: 14px; font-weight: 700; "
            "background: transparent; border: none; margin: 0; padding: 0;"
        )
        from .dark import apply_readable_palette
        apply_readable_palette(heading)
        self._headings.append(heading)
        # The window header already says "Install in Progress" / "Install
        # Complete". This in-card copy is kept for tests but not shown.
        heading.hide()

        card_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        v.addWidget(card_widget)
        if extra_below is not None:
            extra_below.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
            v.addWidget(extra_below)
        v.addStretch(1)
        return container

    def _build_preparing_view(self):
        self._prep_card = Card()
        self._prep_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._prep_icon = _make_squircle_icon("📦", "icon.png")
        card_layout.addWidget(self._prep_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._prep_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        col.addWidget(self._prep_pkg_label)

        self._prep_banner = Banner()
        self._prep_banner.setVisible(False)

        self._prep_progress = QProgressBar()
        _configure_progress(self._prep_progress)
        col.addWidget(self._prep_progress)

        self._prep_step = _status_label(tr("step_extract"))
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
        self._wait_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._wait_icon = _make_squircle_icon("🔌", "icon.png")
        card_layout.addWidget(self._wait_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._wait_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        col.addWidget(self._wait_pkg_label)

        self._wait_progress_bar = QProgressBar()
        _configure_progress(self._wait_progress_bar)
        col.addWidget(self._wait_progress_bar)

        self._wait_prompt_label = _status_label(
            tr("flash_connect_device_prompt").format(model=self._connect_model_text())
        )
        col.addWidget(self._wait_prompt_label)

        self._wait_banner = Banner()
        self._wait_banner.setVisible(False)
        self._wait_status = StatusTag("idle")
        self._wait_status.setVisible(False)

        card_layout.addLayout(col, 1)

        self._wait_cancel_btn = QPushButton("✕")
        self._wait_cancel_btn.setObjectName("softwareUpdateCancelBtn")
        self._style_cancel_button(self._wait_cancel_btn)
        self._wait_cancel_btn.setToolTip(tr("flash_btn_cancel_wait"))
        self._wait_cancel_btn.clicked.connect(self._on_cancel_wait)
        card_layout.addWidget(self._wait_cancel_btn, 0, Qt.AlignVCenter)

        self._wait_continue_btn = QPushButton(tr("dialog_pre_install_continue"))
        self._wait_continue_btn.setVisible(False)
        self._wait_continue_btn.setCursor(Qt.ArrowCursor)
        self._wait_continue_btn.setMinimumHeight(28)
        self._wait_continue_btn.clicked.connect(self._on_power_off_continue)
        card_layout.addWidget(self._wait_continue_btn, 0, Qt.AlignVCenter)

        self._wait_card.set_layout(card_layout)

        # SP Flash Tool GUI is a sidebar action. This page does not grow a second launcher.

        # Guidance images are removed; keep dummy attributes for backward compatibility
        self._status_img = QLabel()
        self._status_img.setVisible(False)
        self._guide_title = QLabel(tr("flash_guide_title"))
        self._guide_texts = []
        for key in ("flash_guide_1", "flash_guide_2", "flash_guide_3", "flash_guide_4"):
            text = QLabel(tr(key))
            text.setWordWrap(True)
            self._guide_texts.append((key, text))

        return self._wrap_centered(self._wait_card)

    def _build_flashing_view(self):
        self._progress_card = Card()
        self._progress_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._flash_icon = _make_squircle_icon("⚙️", "icon.png")
        card_layout.addWidget(self._flash_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._flash_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        col.addWidget(self._flash_pkg_label)

        self._flash_banner = Banner()
        self._flash_banner.setVisible(False)

        self._progress_bar = QProgressBar()
        _configure_progress(self._progress_bar)
        col.addWidget(self._progress_bar)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(6)

        self._step_label = _status_label(tr("step_write"))
        sub_row.addWidget(self._step_label)

        self._eta_label = _status_label("")
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

        from .icons import get_symbol_icon
        self._done_mark = QPushButton()
        self._done_mark.setObjectName("installDoneButton")
        self._done_mark.setFixedSize(28, 28)
        self._done_mark.setCursor(Qt.ArrowCursor)
        self._done_mark.setIcon(get_symbol_icon("complete", 18))
        self._done_mark.setIconSize(QSize(18, 18))
        self._done_mark.setFocusPolicy(Qt.StrongFocus)
        self._done_mark.setAutoDefault(False)
        self._done_mark.setFlat(False)
        self._style_done_button()
        self._done_mark.setVisible(False)
        self._done_mark.clicked.connect(self._on_install_done)
        self._install_done_cb = None
        card_layout.addWidget(self._done_mark, 0, Qt.AlignVCenter)

        self._flash_retry_btn = QPushButton(tr("flash_btn_retry"))
        self._flash_retry_btn.setVisible(False)
        self._flash_retry_btn.setCursor(Qt.ArrowCursor)
        self._flash_retry_btn.setMinimumHeight(28)
        self._flash_retry_btn.clicked.connect(self._on_install_retry)
        card_layout.addWidget(self._flash_retry_btn, 0, Qt.AlignVCenter)

        self._progress_card.set_layout(card_layout)

        # Warning below the update card
        warning_container = QWidget()
        w_layout = QVBoxLayout(warning_container)
        w_layout.setContentsMargins(0, 0, 0, 0)
        from .dark import apply_readable_palette

        self._warning = QLabel(tr("flash_warning"))
        self._warning.setWordWrap(True)
        self._warning.setAlignment(Qt.AlignCenter)
        warn_font = self._warning.font()
        warn_font.setPixelSize(13)
        self._warning.setFont(warn_font)
        self._warning.setStyleSheet("background: transparent; border: none;")
        apply_readable_palette(self._warning)
        w_layout.addWidget(self._warning)

        self._appeal = QWidget()
        appeal_layout = QVBoxLayout(self._appeal)
        appeal_layout.setContentsMargins(0, 4, 0, 0)
        appeal_layout.setSpacing(8)
        self._appeal_label = QLabel(self._coffee_pitch())
        self._appeal_label.setWordWrap(True)
        self._appeal_label.setAlignment(Qt.AlignCenter)
        pitch_font = self._appeal_label.font()
        pitch_font.setPixelSize(15)
        pitch_font.setWeight(QFont.Weight.DemiBold)
        self._appeal_label.setFont(pitch_font)
        self._appeal_label.setStyleSheet("background: transparent; border: none;")
        apply_readable_palette(self._appeal_label)
        appeal_layout.addWidget(self._appeal_label)
        self._coffee_btn = QPushButton(tr("donate_coffee_btn"))
        self._coffee_btn.setCursor(Qt.ArrowCursor)
        self._coffee_btn.setStyleSheet("")
        self._coffee_btn.clicked.connect(self._open_coffee_page)
        appeal_layout.addWidget(self._coffee_btn, 0, Qt.AlignCenter)
        self._appeal_dont = QCheckBox(tr("donate_dont_ask"))
        self._appeal_dont.toggled.connect(self._on_appeal_dont_ask)
        appeal_layout.addWidget(self._appeal_dont, 0, Qt.AlignCenter)
        self._appeal.setVisible(False)
        self._appeal_callback = None
        w_layout.addWidget(self._appeal, 0, Qt.AlignCenter)

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
        self._download_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card_layout = QHBoxLayout()
        card_layout.setContentsMargins(16, 14, 16, 14)
        card_layout.setSpacing(14)

        self._download_icon = _make_squircle_icon("📥", "icon.png")
        card_layout.addWidget(self._download_icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        self._download_pkg_label = _status_label(self._package_name or "\u2014", strong=True)
        col.addWidget(self._download_pkg_label)

        self._download_progress = QProgressBar()
        _configure_progress(self._download_progress)
        col.addWidget(self._download_progress)

        self._download_status_label = _status_label(tr("sel_download_start"))
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
        h = QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.addStretch(1)

        card_col = QVBoxLayout()
        card_col.setContentsMargins(0, 0, 0, 0)
        card_col.setSpacing(8)

        self._download_heading = QLabel(tr("flash_download_in_progress"))
        self._download_heading.setStyleSheet(
            "font-size: 14px; font-weight: 700; background: transparent; border: none; margin: 0; padding: 0;"
        )
        from .dark import apply_readable_palette
        apply_readable_palette(self._download_heading)
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
        self._set_determinate(self._download_progress, percent)
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
        self._complete = False
        self._switch_view(self._preparing_view)
        self._prep_banner.set_key("flash_preparing")
        self._prep_step_key = "step_extract"
        self._prep_step.setText(tr("step_extract"))

    def show_waiting(self):
        self._complete = False
        self._switch_view(self._waiting_view)
        if not self._prompt_only:
            # Nothing has been written yet. The bar stays at 0 until DA or
            # partition progress arrives.
            self._set_determinate(self._wait_progress_bar, 0)

    def show_flashing(self):
        self._switch_view(self._flashing_view)
        failed = self._failed
        done = bool(getattr(self, "_complete", False)) and not failed
        self._warning.setVisible(not failed and not done)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(failed)
            self._cancel_btn.setVisible(not failed and not done)
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(done)

    def _set_determinate(self, bar: QProgressBar, percent) -> None:
        percent = max(0, min(100, int(percent)))
        bar.setRange(0, 100)
        bar.setFormat("%p%")
        bar.setValue(percent)
        bar.setTextVisible(True)

    def show_install_failure(self, message: str, percent: int = 0) -> None:
        """Keep the failure on the install progress card, with Retry in the cancel slot."""
        self._failed = True
        self._prompt_only = False
        self.show_flashing()
        self._warning.setVisible(False)
        if hasattr(self, "_appeal"):
            self._appeal.setVisible(False)
        self._set_determinate(self._progress_bar, percent)
        self._step_label.setText(message)
        self._eta_label.setText("")
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(False)

    def clear_install_failure(self) -> None:
        self._failed = False
        self._complete = False
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(False)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
            self._cancel_btn.setVisible(True)
            self._cancel_btn.setEnabled(True)

    def show_power_off_prompt(self) -> None:
        """Prompt to power off and unplug. Does not start a flash by itself."""
        self._failed = False
        self._prompt_only = True
        self.clear_install_failure()
        self.show_waiting()
        prompt = tr("flash_connect_prompt").format(model=self._connect_model_text())
        self._wait_prompt_label.setText(prompt)
        self._set_determinate(self._wait_progress_bar, 0)
        self._wait_cancel_btn.setVisible(False)
        self._wait_continue_btn.setVisible(True)

    def leave_power_off_prompt(self) -> None:
        self._prompt_only = False
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setVisible(False)
        if hasattr(self, "_wait_cancel_btn"):
            self._wait_cancel_btn.setVisible(True)

    def on_install_retry(self, cb) -> None:
        self._install_retry_cb = cb

    def _on_install_retry(self) -> None:
        if callable(self._install_retry_cb):
            self._install_retry_cb()

    def on_power_off_continue(self, cb) -> None:
        self._power_off_continue_cb = cb

    def _on_power_off_continue(self) -> None:
        if callable(self._power_off_continue_cb):
            self._power_off_continue_cb()

    def set_method(self, method):
        """Record the install method chosen in Settings for this run."""
        self._method = normalise_method(method)

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

    def _coffee_pitch(self) -> str:
        from ..config import get_app_name

        return tr("donate_coffee_pitch").format(app=get_app_name())

    def _open_coffee_page(self) -> None:
        from ..browser import open_browser

        open_browser("https://ko-fi.com/teamslide")

    def show_completion_appeal(self, visible: bool, callback=None) -> None:
        """Coffee note under a finished install. Hidden only when the user opted out before."""
        self._appeal_callback = callback
        if not hasattr(self, "_appeal"):
            return
        if hasattr(self, "_appeal_label"):
            self._appeal_label.setText(self._coffee_pitch())
        self._appeal_dont.blockSignals(True)
        self._appeal_dont.setChecked(False)
        self._appeal_dont.blockSignals(False)
        self._appeal.setVisible(bool(visible))

    def _on_appeal_dont_ask(self, checked: bool) -> None:
        """Remember the choice for the next install. This success screen stays up."""
        if not checked:
            return
        callback = self._appeal_callback
        if callable(callback):
            callback()

    def set_release_icon(self, pixmap, complete: bool = False):
        """Show the release squircle on every install card. ``complete`` adds the check badge.

        A missing release icon is the settings squircle, not the application icon.
        """
        from .release_icon import squircle_pixmap

        self._release_complete = bool(complete)
        source = pixmap if pixmap is not None and not pixmap.isNull() else QPixmap()
        self._release_source = source
        painted = squircle_pixmap(source, 40, complete=self._release_complete)
        for name in ("_prep_icon", "_wait_icon", "_flash_icon", "_download_icon"):
            label = getattr(self, name, None)
            if label is not None:
                label.setPixmap(painted)
                label.setText("")

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
        self._complete = True
        self._failed = False
        self._warning.setVisible(False)
        if hasattr(self, "_cancel_btn"):
            self._cancel_btn.setVisible(False)
            self._cancel_btn.setEnabled(False)
        if hasattr(self, "_done_mark"):
            self._done_mark.setVisible(True)
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setVisible(False)
        source = getattr(self, "_release_source", None)
        if source is not None:
            self.set_release_icon(source, complete=True)
        self._flash_banner.set_type("success")
        self._flash_banner.set_key("flash_banner_done")
        self._wait_status.set_status("complete")
        self._progress_bar.setValue(100)
        self._eta_label.setText("")

    def update_prep_progress(self, percent):
        self._set_determinate(self._prep_progress, percent)

    def update_progress(self, percent):
        self._set_determinate(self._progress_bar, percent)
        if self._stack.currentWidget() is self._waiting_view and not self._prompt_only:
            self._set_determinate(self._wait_progress_bar, percent)

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
        if hasattr(self, "_wait_prompt_label") and not self._prompt_only:
            self._wait_prompt_label.setText(
                tr("flash_connect_device_prompt").format(model=self._connect_model_text())
            )
        elif self._prompt_only and hasattr(self, "_wait_prompt_label"):
            self._wait_prompt_label.setText(
                tr("flash_connect_prompt").format(model=self._connect_model_text())
            )
        if hasattr(self, "_flash_retry_btn"):
            self._flash_retry_btn.setText(tr("flash_btn_retry"))
        if hasattr(self, "_wait_continue_btn"):
            self._wait_continue_btn.setText(tr("dialog_pre_install_continue"))
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
        if hasattr(self, "_appeal_label"):
            self._appeal_label.setText(self._coffee_pitch())
            self._appeal_dont.setText(tr("donate_dont_ask"))
        if hasattr(self, "_coffee_btn"):
            self._coffee_btn.setText(tr("donate_coffee_btn"))
        self._style_done_button()
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

    def _seal_status_labels(self):
        """Erase the previous status string on the label's own rect."""
        from .surfaces import glass_surfaces_enabled, seal_updating_text
        fill = QColor(T().bg_card)
        names = (
            "_prep_pkg_label", "_prep_step", "_wait_pkg_label", "_wait_prompt_label",
            "_flash_pkg_label", "_step_label", "_eta_label", "_warning",
            "_download_pkg_label", "_download_status_label", "_download_heading",
            "_appeal_label",
        )
        for name in names:
            label = getattr(self, name, None)
            if label is None:
                continue
            from .dark import apply_readable_palette

            apply_readable_palette(label)
            if glass_surfaces_enabled():
                # These lines sit on the card, whose frost fill is only a few
                # percent alpha and so cannot erase the previous glyphs. Without
                # an explicit backdrop erase the old package title / step / ETA
                # smeared across the progress bar and overlapped the new text.
                label.setStyleSheet("background: transparent; border: none;")
                label.setAutoFillBackground(False)
                seal_updating_text(label)
                continue
            label.setAutoFillBackground(True)
            pal = label.palette()
            pal.setColor(QPalette.ColorRole.Window, fill)
            pal.setColor(QPalette.ColorRole.Base, fill)
            label.setPalette(pal)
        for heading in getattr(self, "_headings", []):
            if glass_surfaces_enabled():
                heading.setAutoFillBackground(False)
                seal_updating_text(heading)
                continue
            heading.setAutoFillBackground(True)
            pal = heading.palette()
            pal.setColor(QPalette.ColorRole.Window, fill)
            heading.setPalette(pal)

    def refresh_theme(self):
        from .dark import apply_readable_palette

        self._seal_status_labels()
        for bar_name in ("_prep_progress", "_wait_progress_bar", "_progress_bar", "_download_progress"):
            bar = getattr(self, bar_name, None)
            if bar is not None:
                apply_readable_palette(bar, progress=True)
        for h in getattr(self, "_headings", []):
            h.setStyleSheet(
                "font-size: 14px; font-weight: 700; "
                "background: transparent; border: none; margin: 0; padding: 0;"
            )
            apply_readable_palette(h)
        download_heading = getattr(self, "_download_heading", None)
        if download_heading is not None:
            download_heading.setStyleSheet(
                "font-size: 14px; font-weight: 700; "
                "background: transparent; border: none; margin: 0; padding: 0;"
            )
            apply_readable_palette(download_heading)
        for btn in (
            getattr(self, "_cancel_btn", None),
            getattr(self, "_wait_cancel_btn", None),
            getattr(self, "_prep_cancel_btn", None),
        ):
            if btn is not None:
                self._style_cancel_button(btn)

    def _style_done_button(self) -> None:
        button = getattr(self, "_done_mark", None)
        if not isinstance(button, QPushButton):
            return
        from .icons import get_symbol_icon

        button.setIcon(get_symbol_icon("complete", 18))
        button.setText("")
        button.setToolTip(tr("install_done_dismiss"))
        button.setAccessibleName(tr("install_done_dismiss"))
        button.setCursor(Qt.ArrowCursor)

    def on_install_done(self, callback) -> None:
        self._install_done_cb = callback

    def _on_install_done(self) -> None:
        if callable(getattr(self, "_install_done_cb", None)):
            self._install_done_cb()

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
