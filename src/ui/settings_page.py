"""Settings page — manage device release reminders and donation preferences."""

import logging
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import device_tracking
from ..i18n import tr
from .widgets import Card, Banner

logger = logging.getLogger(__name__)


class SettingsPage(QWidget):
    """Settings page allowing configuration of release reminders and donation preferences."""

    donation_visibility_changed = Signal(bool)  # is_disabled
    check_updates_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()
        self.refresh_settings()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 20, 24, 20)
        root_layout.setSpacing(16)

        # Header
        self._header = QLabel(tr("settings_title"))
        self._header.setStyleSheet("font-size: 20px; font-weight: 800; color: #f8fafc;")
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

        # --- Card 1: Firmware Release Reminders ---
        self._reminders_card = Card("settings_reminders_group")
        rem_layout = QVBoxLayout()
        rem_layout.setSpacing(12)

        self._reminders_desc = QLabel(tr("settings_reminders_desc"))
        self._reminders_desc.setWordWrap(True)
        self._reminders_desc.setStyleSheet("color: #94a3b8; font-size: 13px; line-height: 1.4;")
        rem_layout.addWidget(self._reminders_desc)

        # Y1 Row
        y1_box = QVBoxLayout()
        y1_box.setSpacing(4)
        self._cb_y1 = QCheckBox(tr("settings_enable_y1"))
        self._cb_y1.setCursor(Qt.PointingHandCursor)
        self._cb_y1.toggled.connect(self._on_y1_toggled)
        y1_box.addWidget(self._cb_y1)

        y1_status_row = QHBoxLayout()
        y1_status_row.setContentsMargins(24, 0, 0, 0)
        self._lbl_y1_status = QLabel()
        self._lbl_y1_status.setWordWrap(True)
        self._lbl_y1_status.setStyleSheet("color: #64748b; font-size: 12px;")
        y1_status_row.addWidget(self._lbl_y1_status, 1)

        self._btn_clear_y1 = QPushButton(tr("settings_clear_install"))
        self._btn_clear_y1.setProperty("cssClass", "ghost")
        self._btn_clear_y1.setCursor(Qt.PointingHandCursor)
        self._btn_clear_y1.setFixedHeight(26)
        self._btn_clear_y1.clicked.connect(self._on_clear_y1)
        y1_status_row.addWidget(self._btn_clear_y1)
        y1_box.addLayout(y1_status_row)
        rem_layout.addLayout(y1_box)

        # Separator line
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background-color: rgba(255, 255, 255, 0.08);")
        rem_layout.addWidget(sep)

        # Y2 Row
        y2_box = QVBoxLayout()
        y2_box.setSpacing(4)
        self._cb_y2 = QCheckBox(tr("settings_enable_y2"))
        self._cb_y2.setCursor(Qt.PointingHandCursor)
        self._cb_y2.toggled.connect(self._on_y2_toggled)
        y2_box.addWidget(self._cb_y2)

        y2_status_row = QHBoxLayout()
        y2_status_row.setContentsMargins(24, 0, 0, 0)
        self._lbl_y2_status = QLabel()
        self._lbl_y2_status.setWordWrap(True)
        self._lbl_y2_status.setStyleSheet("color: #64748b; font-size: 12px;")
        y2_status_row.addWidget(self._lbl_y2_status, 1)

        self._btn_clear_y2 = QPushButton(tr("settings_clear_install"))
        self._btn_clear_y2.setProperty("cssClass", "ghost")
        self._btn_clear_y2.setCursor(Qt.PointingHandCursor)
        self._btn_clear_y2.setFixedHeight(26)
        self._btn_clear_y2.clicked.connect(self._on_clear_y2)
        y2_status_row.addWidget(self._btn_clear_y2)
        y2_box.addLayout(y2_status_row)
        rem_layout.addLayout(y2_box)

        rem_layout.addSpacing(6)
        check_row = QHBoxLayout()
        self._btn_check_updates = QPushButton(tr("settings_check_firmware_updates"))
        self._btn_check_updates.setProperty("cssClass", "primary")
        self._btn_check_updates.setCursor(Qt.PointingHandCursor)
        self._btn_check_updates.clicked.connect(self.check_updates_requested.emit)
        check_row.addWidget(self._btn_check_updates)
        check_row.addStretch()
        rem_layout.addLayout(check_row)

        self._reminders_card.set_layout(rem_layout)
        layout.addWidget(self._reminders_card)

        # --- Card 2: Community Acknowledgements & Donations ---
        self._donations_card = Card("settings_donations_group")
        don_layout = QVBoxLayout()
        don_layout.setSpacing(12)

        # Hide donations checkbox
        self._cb_hide_donations = QCheckBox(tr("settings_hide_donations"))
        self._cb_hide_donations.setCursor(Qt.PointingHandCursor)
        self._cb_hide_donations.setToolTip(tr("settings_hide_donations_tip"))
        self._cb_hide_donations.toggled.connect(self._on_hide_donations_toggled)
        don_layout.addWidget(self._cb_hide_donations)

        lbl_hide_tip = QLabel(tr("settings_hide_donations_tip"))
        lbl_hide_tip.setWordWrap(True)
        lbl_hide_tip.setStyleSheet("color: #64748b; font-size: 12px; margin-left: 24px;")
        don_layout.addWidget(lbl_hide_tip)

        # Skip install donation prompt checkbox
        self._cb_skip_install_donations = QCheckBox(tr("settings_skip_install_donations"))
        self._cb_skip_install_donations.setCursor(Qt.PointingHandCursor)
        self._cb_skip_install_donations.setToolTip(tr("settings_skip_install_donations_tip"))
        self._cb_skip_install_donations.toggled.connect(self._on_skip_install_donations_toggled)
        don_layout.addWidget(self._cb_skip_install_donations)

        lbl_skip_tip = QLabel(tr("settings_skip_install_donations_tip"))
        lbl_skip_tip.setWordWrap(True)
        lbl_skip_tip.setStyleSheet("color: #64748b; font-size: 12px; margin-left: 24px;")
        don_layout.addWidget(lbl_skip_tip)

        self._donations_card.set_layout(don_layout)
        layout.addWidget(self._donations_card)

        # --- Card 3: SP Flash Tool Diagnostics & Hardware ---
        self._checker_card = Card("settings_checker_group")
        chk_layout = QVBoxLayout()
        chk_layout.setSpacing(12)

        self._checker_desc = QLabel(tr("settings_checker_desc"))
        self._checker_desc.setWordWrap(True)
        self._checker_desc.setStyleSheet("color: #94a3b8; font-size: 13px; line-height: 1.4;")
        chk_layout.addWidget(self._checker_desc)

        chk_btn_row = QHBoxLayout()
        self._btn_run_checker = QPushButton(tr("system_checker_run_btn"))
        self._btn_run_checker.setProperty("cssClass", "primary")
        self._btn_run_checker.setCursor(Qt.PointingHandCursor)
        self._btn_run_checker.clicked.connect(self._on_run_checker)
        chk_btn_row.addWidget(self._btn_run_checker)

        self._btn_launch_sp = QPushButton(tr("system_checker_launch_gui_btn"))
        self._btn_launch_sp.setProperty("cssClass", "ghost")
        self._btn_launch_sp.setCursor(Qt.PointingHandCursor)
        self._btn_launch_sp.clicked.connect(self._on_launch_sp)
        chk_btn_row.addWidget(self._btn_launch_sp)
        chk_btn_row.addStretch()
        chk_layout.addLayout(chk_btn_row)

        self._checker_card.set_layout(chk_layout)
        layout.addWidget(self._checker_card)

        layout.addStretch()
        scroll.setWidget(container)
        root_layout.addWidget(scroll, 1)

    def refresh_settings(self):
        """Reload and update all controls to reflect current settings."""
        # Y1 reminder toggle & install status
        self._cb_y1.blockSignals(True)
        self._cb_y1.setChecked(device_tracking.is_device_reminder_enabled("Y1"))
        self._cb_y1.blockSignals(False)

        y1_rec = device_tracking.get_device_install("Y1")
        if y1_rec:
            dt_str = y1_rec.get("installed_at", "")[:10]
            self._lbl_y1_status.setText(
                tr("settings_installed_status").format(
                    software=y1_rec.get("software_name") or "Firmware",
                    version=y1_rec.get("tag_name") or "Unknown",
                    date=dt_str or "Recorded",
                )
            )
            self._btn_clear_y1.setVisible(True)
        else:
            self._lbl_y1_status.setText(tr("settings_not_installed"))
            self._btn_clear_y1.setVisible(False)

        # Y2 reminder toggle & install status
        self._cb_y2.blockSignals(True)
        self._cb_y2.setChecked(device_tracking.is_device_reminder_enabled("Y2"))
        self._cb_y2.blockSignals(False)

        y2_rec = device_tracking.get_device_install("Y2")
        if y2_rec:
            dt_str = y2_rec.get("installed_at", "")[:10]
            self._lbl_y2_status.setText(
                tr("settings_installed_status").format(
                    software=y2_rec.get("software_name") or "Firmware",
                    version=y2_rec.get("tag_name") or "Unknown",
                    date=dt_str or "Recorded",
                )
            )
            self._btn_clear_y2.setVisible(True)
        else:
            self._lbl_y2_status.setText(tr("settings_not_installed"))
            self._btn_clear_y2.setVisible(False)

        # Donation checkboxes
        self._cb_hide_donations.blockSignals(True)
        self._cb_hide_donations.setChecked(device_tracking.is_donation_ui_disabled())
        self._cb_hide_donations.blockSignals(False)

        self._cb_skip_install_donations.blockSignals(True)
        self._cb_skip_install_donations.setChecked(
            device_tracking.is_donation_install_prompt_disabled()
        )
        self._cb_skip_install_donations.blockSignals(False)

    def _on_y1_toggled(self, checked: bool):
        device_tracking.set_device_reminder_enabled("Y1", checked)

    def _on_y2_toggled(self, checked: bool):
        device_tracking.set_device_reminder_enabled("Y2", checked)

    def _on_clear_y1(self):
        device_tracking.clear_device_install("Y1")
        self.refresh_settings()

    def _on_clear_y2(self):
        device_tracking.clear_device_install("Y2")
        self.refresh_settings()

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
        self._reminders_card.retranslate()
        self._reminders_desc.setText(tr("settings_reminders_desc"))
        self._cb_y1.setText(tr("settings_enable_y1"))
        self._cb_y2.setText(tr("settings_enable_y2"))
        self._btn_clear_y1.setText(tr("settings_clear_install"))
        self._btn_clear_y2.setText(tr("settings_clear_install"))
        self._btn_check_updates.setText(tr("settings_check_firmware_updates"))
        self._donations_card.retranslate()
        self._cb_hide_donations.setText(tr("settings_hide_donations"))
        self._cb_skip_install_donations.setText(tr("settings_skip_install_donations"))
        self._checker_card.retranslate()
        self._checker_desc.setText(tr("settings_checker_desc"))
        self._btn_run_checker.setText(tr("system_checker_run_btn"))
        self._btn_launch_sp.setText(tr("system_checker_launch_gui_btn"))
        self.refresh_settings()

