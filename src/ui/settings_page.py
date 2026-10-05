"""Settings page — install method, release reminders and donation preferences."""

import logging

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, device_tracking, paths
from ..config import DEVICE_MODELS, device_label_for_model
from ..flash_service import (
    METHOD_MTK,
    METHOD_MTK_MAC,
    METHOD_SP,
    default_flash_method,
    normalise_method,
)
from ..i18n import tr
from .widgets import Card

logger = logging.getLogger(__name__)

# Install-method identifiers are owned by flash_service ("mtk_mac" is the
# hidden simulated-macOS flow: MTKClient as the only backend with mac-centric
# prompts, revealed by reveal_advanced_methods()).


class SettingsPage(QWidget):
    """Settings page for install method, release reminders and donations."""

    donation_visibility_changed = Signal(bool)  # is_disabled
    check_updates_requested = Signal()
    flash_method_changed = Signal(str)
    simulated_mac_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._advanced_revealed = False
        # model id -> install-status QLabel, filled in by _build_ui.
        self._install_status_labels = {}
        self._build_ui()
        self.refresh_settings()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(16)

        # Header
        self._header = QLabel(tr("settings_title"))
        self._header.setProperty("cssClass", "pageTitle")
        root_layout.addWidget(self._header)

        # Scroll area for clean overflow handling
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setStyleSheet("background: transparent;")

        container = QWidget()
        container.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(18)

        # --- Card 1: Install method ---
        self._method_card = Card("settings_method_group")
        method_layout = QVBoxLayout()
        method_layout.setSpacing(12)

        self._method_desc = QLabel(tr("settings_method_desc"))
        self._method_desc.setWordWrap(True)
        self._method_desc.setProperty("cssClass", "subtitle")
        method_layout.addWidget(self._method_desc)

        method_row = QHBoxLayout()
        self._method_label = QLabel(tr("flash_method"))
        self._method_label.setProperty("cssClass", "field-label")
        method_row.addWidget(self._method_label)

        self._method_combo = QComboBox()
        self._method_combo.setMinimumWidth(240)
        self._method_combo.currentIndexChanged.connect(self._on_method_changed)
        method_row.addWidget(self._method_combo)
        method_row.addStretch()
        method_layout.addLayout(method_row)

        self._method_note = QLabel("")
        self._method_note.setWordWrap(True)
        self._method_note.setProperty("cssClass", "dimmed")
        method_layout.addWidget(self._method_note)

        self._method_card.set_layout(method_layout)
        layout.addWidget(self._method_card)

        # --- Card 2: Firmware Release Reminders ---
        self._reminders_card = Card("settings_reminders_group")
        rem_layout = QVBoxLayout()
        rem_layout.setSpacing(8)

        self._cb_reminders = QCheckBox(tr("settings_reminders_enable"))
        self._cb_reminders.toggled.connect(self._on_reminders_toggled)
        rem_layout.addWidget(self._cb_reminders)

        self._reminders_card.set_layout(rem_layout)
        layout.addWidget(self._reminders_card)

        # --- Card 3: Community Acknowledgements & Donations ---
        self._donations_card = Card("settings_donations_group")
        don_layout = QVBoxLayout()
        don_layout.setSpacing(12)

        # Hide donations checkbox
        self._cb_hide_donations = QCheckBox(tr("settings_hide_donations"))
        self._cb_hide_donations.setToolTip(tr("settings_hide_donations_tip"))
        self._cb_hide_donations.toggled.connect(self._on_hide_donations_toggled)
        don_layout.addWidget(self._cb_hide_donations)

        lbl_hide_tip = QLabel(tr("settings_hide_donations_tip"))
        lbl_hide_tip.setWordWrap(True)
        lbl_hide_tip.setProperty("cssClass", "dimmed")
        lbl_hide_tip.setContentsMargins(24, 0, 0, 0)
        don_layout.addWidget(lbl_hide_tip)

        # Skip install donation prompt checkbox
        self._cb_skip_install_donations = QCheckBox(tr("settings_skip_install_donations"))
        self._cb_skip_install_donations.setToolTip(tr("settings_skip_install_donations_tip"))
        self._cb_skip_install_donations.toggled.connect(self._on_skip_install_donations_toggled)
        don_layout.addWidget(self._cb_skip_install_donations)

        lbl_skip_tip = QLabel(tr("settings_skip_install_donations_tip"))
        lbl_skip_tip.setWordWrap(True)
        lbl_skip_tip.setProperty("cssClass", "dimmed")
        lbl_skip_tip.setContentsMargins(24, 0, 0, 0)
        don_layout.addWidget(lbl_skip_tip)

        self._donations_card.set_layout(don_layout)
        layout.addWidget(self._donations_card)

        # --- Card 4: SP Flash Tool Diagnostics & Hardware ---
        self._checker_card = Card("settings_checker_group")
        chk_layout = QVBoxLayout()
        chk_layout.setSpacing(12)

        self._checker_desc = QLabel(tr("settings_checker_desc"))
        self._checker_desc.setWordWrap(True)
        self._checker_desc.setProperty("cssClass", "subtitle")
        chk_layout.addWidget(self._checker_desc)

        chk_btn_row = QHBoxLayout()
        self._btn_run_checker = QPushButton(tr("system_checker_run_btn"))
        self._btn_run_checker.setProperty("cssClass", "primary")
        self._btn_run_checker.clicked.connect(self._on_run_checker)
        chk_btn_row.addWidget(self._btn_run_checker)

        self._btn_launch_sp = QPushButton(tr("system_checker_launch_gui_btn"))
        self._btn_launch_sp.setProperty("cssClass", "ghost")
        self._btn_launch_sp.clicked.connect(self._on_launch_sp)
        chk_btn_row.addWidget(self._btn_launch_sp)
        chk_btn_row.addStretch()
        chk_layout.addLayout(chk_btn_row)

        self._checker_card.set_layout(chk_layout)
        layout.addWidget(self._checker_card)

        # SP Flash Tool is not supported on macOS (MTKClient only).
        # Hide backend selection and diagnostics cards on macOS.
        self._method_card.setVisible(not paths.IS_MAC)
        self._checker_card.setVisible(not paths.IS_MAC)

        layout.addStretch()
        scroll.setWidget(container)
        root_layout.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Install method
    # ------------------------------------------------------------------
    def _available_methods(self):
        """Method ids selectable on this platform, in display order."""
        if paths.IS_MAC:
            # No SP Flash Tool build exists for macOS.
            return (METHOD_MTK,)
        if self._advanced_revealed:
            return (METHOD_SP, METHOD_MTK, METHOD_MTK_MAC)
        return (METHOD_SP, METHOD_MTK)

    def reveal_advanced_methods(self):
        """Reveal the hidden 'MTKClient (Mac)' entry (M or D while running).

        Used to exercise the macOS flow — MTKClient as the only backend, with
        mac-centric prompts — without Mac hardware. No-op on a real Mac, where
        that is simply the normal behaviour.
        """
        if paths.IS_MAC or self._advanced_revealed:
            return
        self._advanced_revealed = True
        self._reload_method_options()

    def advanced_methods_revealed(self) -> bool:
        return self._advanced_revealed

    def _reload_method_options(self):
        labels = {
            METHOD_SP: tr("flash_method_sp"),
            METHOD_MTK: tr("flash_method_mtk"),
            METHOD_MTK_MAC: tr("flash_method_mtk_mac"),
        }
        current = self._method_combo.currentData() or self._persisted_method()
        self._method_combo.blockSignals(True)
        self._method_combo.clear()
        for method in self._available_methods():
            self._method_combo.addItem(labels.get(method, method), method)
        idx = self._method_combo.findData(current)
        self._method_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._method_combo.blockSignals(False)
        self._update_method_note()

    def _persisted_method(self) -> str:
        return normalise_method(QSettings("innioasis", "updater").value("flash_method", ""))

    def current_method(self) -> str:
        return self._method_combo.currentData() or default_flash_method()

    def set_method(self, method):
        """Reflect a method chosen elsewhere (or persisted) in the selector."""
        method = normalise_method(method)
        if method == METHOD_MTK_MAC and not paths.IS_MAC:
            # Restore the revealed entry for a choice made in a previous run.
            self._advanced_revealed = True
        self._reload_method_options()
        idx = self._method_combo.findData(method)
        if idx >= 0 and idx != self._method_combo.currentIndex():
            self._method_combo.blockSignals(True)
            self._method_combo.setCurrentIndex(idx)
            self._method_combo.blockSignals(False)
        self._update_method_note()

    def set_method_enabled(self, enabled: bool):
        """Lock the selector while a run is in progress."""
        self._method_combo.setEnabled(bool(enabled))

    def _on_method_changed(self):
        method = self.current_method()
        self._update_method_note()
        self.flash_method_changed.emit(method)

    def _update_method_note(self):
        method = self.current_method()
        if method == METHOD_SP:
            self._method_note.setText(tr("flash_method_note_sp"))
        elif method == METHOD_MTK_MAC:
            self._method_note.setText(tr("flash_method_note_mtk_mac"))
        else:
            self._method_note.setText(tr("flash_method_note_mtk"))

    # ------------------------------------------------------------------
    # Release reminders
    # ------------------------------------------------------------------
    def tracked_software_summary(self) -> str:
        """Name the software releases watched per device, e.g. "Y1: Rockbox"."""
        parts = []
        for model in DEVICE_MODELS:
            label = device_label_for_model(model)
            try:
                names = catalog.software_names_for_model(model)
            except Exception:
                logger.debug("Catalogue lookup failed for %s", model, exc_info=True)
                names = []
            parts.append(f"{label}: {', '.join(names)}" if names else label)
        return " \u00b7 ".join(parts)

    def _tracked_devices_summary(self) -> str:
        return ", ".join(device_label_for_model(m) for m in DEVICE_MODELS)

    def _on_reminders_toggled(self, checked: bool):
        for model in DEVICE_MODELS:
            device_tracking.set_device_reminder_enabled(model, bool(checked))
        self.refresh_settings()

    def _on_clear_install(self, model: str):
        device_tracking.clear_device_install(model)
        self.refresh_settings()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------
    def refresh_settings(self):
        """Reload and update all controls to reflect current settings."""
        self._method_card.setVisible(not paths.IS_MAC)
        self._checker_card.setVisible(not paths.IS_MAC)

        # Install method selector
        self.set_method(self._persisted_method())

        # Single reminder opt-in: on if any tracked device is still enabled.
        any_enabled = any(
            device_tracking.is_device_reminder_enabled(m) for m in DEVICE_MODELS
        )
        self._cb_reminders.blockSignals(True)
        self._cb_reminders.setChecked(any_enabled)
        self._cb_reminders.blockSignals(False)

        # Donation checkboxes
        self._cb_hide_donations.blockSignals(True)
        self._cb_hide_donations.setChecked(device_tracking.is_donation_ui_disabled())
        self._cb_hide_donations.blockSignals(False)

        self._cb_skip_install_donations.blockSignals(True)
        self._cb_skip_install_donations.setChecked(
            device_tracking.is_donation_install_prompt_disabled()
        )
        self._cb_skip_install_donations.blockSignals(False)

    # ------------------------------------------------------------------
    # Misc actions
    # ------------------------------------------------------------------
    def _on_hide_donations_toggled(self, checked: bool):
        device_tracking.set_donation_ui_disabled(checked)
        self.donation_visibility_changed.emit(checked)

    def _on_skip_install_donations_toggled(self, checked: bool):
        device_tracking.set_donation_install_prompt_disabled(checked)

    def _on_run_checker(self):
        from .dialogs import LinuxSetupDialog

        dlg = LinuxSetupDialog(self, auto_start=False)
        dlg.exec()

    def _on_launch_sp(self):
        from .. import sp_flash_gui

        ok, msg = sp_flash_gui.open_sp_flash_tool_gui()
        if not ok:
            QMessageBox.warning(self, "SP Flash Tool GUI", msg)

    def retranslate(self):
        """Retranslate UI elements upon language switch."""
        self._header.setText(tr("settings_title"))
        self._method_card.retranslate()
        self._method_desc.setText(tr("settings_method_desc"))
        self._method_label.setText(tr("flash_method"))
        self._reload_method_options()
        self._reminders_card.retranslate()
        self._cb_reminders.setText(tr("settings_reminders_enable"))
        self._donations_card.retranslate()
        self._cb_hide_donations.setText(tr("settings_hide_donations"))
        self._cb_skip_install_donations.setText(tr("settings_skip_install_donations"))
        self._checker_card.retranslate()
        self._checker_desc.setText(tr("settings_checker_desc"))
        self._btn_run_checker.setText(tr("system_checker_run_btn"))
        self._btn_launch_sp.setText(tr("system_checker_launch_gui_btn"))
        self.refresh_settings()
