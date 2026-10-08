"""Settings page — install method, release reminders and donation preferences."""

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, device_tracking, paths
from ..config import (
    DEVICE_MODELS,
    device_label_for_model,
    is_mediatek_installer,
    is_offline_mode,
)
from ..flash_service import (
    METHOD_MTK,
    METHOD_MTK_MAC,
    METHOD_SP,
    default_flash_method,
    normalise_method,
)
from ..i18n import tr
from .dark import page_top_margin
from .widgets import Card


def terminal_install_desc() -> str:
    """Terminal install description, worded for this platform's console app."""
    key = "settings_terminal_desc_windows" if paths.IS_WINDOWS else "settings_terminal_desc"
    return tr(key)

logger = logging.getLogger(__name__)

# Where Windows users get the MediaTek USB driver before their first flash.
MEDIATEK_DRIVERS_URL = "https://innioasis.app/guide.html"

# Install-method identifiers are owned by flash_service ("mtk_mac" is the
# hidden simulated-macOS flow: MTKClient as the only backend with mac-centric
# prompts, revealed by reveal_advanced_methods()).


class SettingsPage(QWidget):
    """Settings page for install method, release reminders and donations."""

    donation_visibility_changed = Signal(bool)  # is_disabled
    check_updates_requested = Signal()
    flash_method_changed = Signal(str)
    simulated_mac_requested = Signal()
    offline_mode_changed = Signal(bool)
    release_filters_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._advanced_revealed = False
        # Guards the filter checkboxes while they are synced from settings, so
        # syncing never re-enters a handler or reopens a confirmation dialog.
        self._filters_loading = False
        # model id -> install-status QLabel, filled in by _build_ui.
        self._install_status_labels = {}
        self._build_ui()
        self.refresh_settings()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, page_top_margin(), 24, 20)
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

        # --- Authentication file (SP Flash Tool, generic MediaTek build) ---
        # Console mode has no --auth switch, so a chosen file is handed over
        # through a generated console configuration file. Optional: most
        # targets are not secure-booted and flash without one.
        self._sp_auth_box = QWidget()
        auth_layout = QVBoxLayout(self._sp_auth_box)
        auth_layout.setContentsMargins(0, 0, 0, 0)
        auth_layout.setSpacing(6)

        auth_row = QHBoxLayout()
        self._sp_auth_label = QLabel(tr("settings_sp_auth"))
        self._sp_auth_label.setProperty("cssClass", "field-label")
        auth_row.addWidget(self._sp_auth_label)

        self._sp_auth_value = QLineEdit()
        self._sp_auth_value.setReadOnly(True)
        self._sp_auth_value.setMinimumWidth(240)
        self._sp_auth_value.setPlaceholderText(tr("settings_sp_auth_none"))
        auth_row.addWidget(self._sp_auth_value, 1)

        self._sp_auth_browse = QPushButton(tr("settings_sp_auth_browse"))
        self._sp_auth_browse.clicked.connect(self._on_sp_auth_browse)
        auth_row.addWidget(self._sp_auth_browse)

        self._sp_auth_clear = QPushButton(tr("settings_sp_auth_clear"))
        self._sp_auth_clear.clicked.connect(self._on_sp_auth_clear)
        auth_row.addWidget(self._sp_auth_clear)
        auth_layout.addLayout(auth_row)

        self._sp_auth_desc = QLabel(tr("settings_sp_auth_desc"))
        self._sp_auth_desc.setWordWrap(True)
        self._sp_auth_desc.setProperty("cssClass", "dimmed")
        auth_layout.addWidget(self._sp_auth_desc)

        method_layout.addWidget(self._sp_auth_box)

        self._method_card.set_layout(method_layout)
        layout.addWidget(self._method_card)

        # --- Card 2: Installation mode (terminal installs) ---
        self._terminal_card = Card("settings_terminal_group")
        term_layout = QVBoxLayout()
        term_layout.setSpacing(8)

        self._cb_terminal_install = QCheckBox(tr("settings_terminal_install"))
        self._cb_terminal_install.setToolTip(terminal_install_desc())
        self._cb_terminal_install.toggled.connect(self._on_terminal_install_toggled)
        term_layout.addWidget(self._cb_terminal_install)

        self._terminal_desc = QLabel(terminal_install_desc())
        self._terminal_desc.setWordWrap(True)
        self._terminal_desc.setProperty("cssClass", "dimmed")
        self._terminal_desc.setContentsMargins(24, 0, 0, 0)
        term_layout.addWidget(self._terminal_desc)

        self._terminal_card.set_layout(term_layout)
        layout.addWidget(self._terminal_card)

        # --- Card 2: Firmware Release Reminders ---
        self._reminders_card = Card("settings_reminders_group")
        rem_layout = QVBoxLayout()
        rem_layout.setSpacing(8)

        self._cb_reminders = QCheckBox(tr("settings_reminders_enable"))
        self._cb_reminders.toggled.connect(self._on_reminders_toggled)
        rem_layout.addWidget(self._cb_reminders)

        self._reminders_card.set_layout(rem_layout)
        layout.addWidget(self._reminders_card)

        # --- Card 3: Rockbox release filters (always visible) ---
        # These are applied by the online browser only when Rockbox releases
        # for Y1 are being listed.
        self._rockbox_card = Card("settings_rockbox_group")
        rock_layout = QVBoxLayout()
        rock_layout.setSpacing(8)

        self._rockbox_desc = QLabel(tr("settings_rockbox_desc"))
        self._rockbox_desc.setWordWrap(True)
        self._rockbox_desc.setProperty("cssClass", "subtitle")
        rock_layout.addWidget(self._rockbox_desc)

        self._cb_old_rockbox = QCheckBox(tr("settings_filter_old_rockbox"))
        self._cb_old_rockbox.setToolTip(tr("settings_old_rockbox_warn_body"))
        self._cb_old_rockbox.toggled.connect(self._on_old_rockbox_toggled)
        rock_layout.addWidget(self._cb_old_rockbox)

        self._cb_nightly = QCheckBox(tr("settings_filter_nightly"))
        self._cb_nightly.toggled.connect(self._on_nightly_toggled)
        rock_layout.addWidget(self._cb_nightly)

        self._cb_240p = QCheckBox(tr("settings_filter_240p"))
        self._cb_240p.setToolTip(tr("settings_240p_tip"))
        self._cb_240p.toggled.connect(self._on_240p_toggled)
        rock_layout.addWidget(self._cb_240p)

        self._rockbox_card.set_layout(rock_layout)
        layout.addWidget(self._rockbox_card)

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

        # --- Card 4: Platform preparation ---
        # Linux gets the SP Flash Tool system checker (udev rules, kernel
        # modules); Windows instead needs the MediaTek USB driver installed
        # before the first flash.
        if paths.IS_WINDOWS:
            self._prep_card = self._build_driver_card()
        else:
            self._prep_card = self._build_checker_card()
        layout.addWidget(self._prep_card)

        # --- Card 5: Offline Mode ---
        # The generic MediaTek Installer build is offline-only, so the toggle
        # that would re-enable online firmware is not shown there at all.
        self._offline_mode_card = Card("settings_offline_mode_group")
        off_layout = QVBoxLayout()
        off_layout.setSpacing(8)

        self._cb_offline_mode = QCheckBox(tr("settings_offline_mode"))
        self._cb_offline_mode.setToolTip(tr("settings_offline_mode_desc"))
        self._cb_offline_mode.toggled.connect(self._on_offline_mode_toggled)
        off_layout.addWidget(self._cb_offline_mode)

        self._offline_mode_desc = QLabel(tr("settings_offline_mode_desc"))
        self._offline_mode_desc.setWordWrap(True)
        self._offline_mode_desc.setProperty("cssClass", "dimmed")
        self._offline_mode_desc.setContentsMargins(24, 0, 0, 0)
        off_layout.addWidget(self._offline_mode_desc)

        self._offline_mode_card.set_layout(off_layout)
        self._offline_mode_card.setVisible(not is_mediatek_installer())
        layout.addWidget(self._offline_mode_card)

        # SP Flash Tool is not supported on macOS (MTKClient only).
        # Hide backend selection and diagnostics cards on macOS.
        self._method_card.setVisible(not paths.IS_MAC)
        self._prep_card.setVisible(not paths.IS_MAC)

        layout.addStretch()
        scroll.setWidget(container)
        root_layout.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Rockbox release filters
    # ------------------------------------------------------------------
    def _confirm_old_rockbox(self) -> bool:
        """Ask before unlocking pre-0.5 Rockbox builds (they brick newer Y1s)."""
        reply = QMessageBox.warning(
            self,
            tr("settings_old_rockbox_warn_title"),
            tr("settings_old_rockbox_warn_body"),
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return reply == QMessageBox.Yes

    def _apply_filter_flags(self, filters=None):
        """Sync the checkboxes with the persisted Rockbox filters."""
        if filters is None:
            filters = device_tracking.rockbox_release_filters()
        flags = filters.as_dict() if hasattr(filters, "as_dict") else dict(filters)
        self._filters_loading = True
        try:
            self._cb_old_rockbox.setChecked(flags[device_tracking.FILTER_OLD_ROCKBOX])
            self._cb_nightly.setChecked(flags[device_tracking.FILTER_NIGHTLY])
            self._cb_240p.setChecked(flags[device_tracking.FILTER_240P])
        finally:
            self._filters_loading = False

    def release_filters(self) -> dict:
        """Current Rockbox release filters (read from settings)."""
        return device_tracking.rockbox_release_filters().as_dict()

    def _on_old_rockbox_toggled(self, checked: bool):
        if self._filters_loading:
            return
        if checked and not self._confirm_old_rockbox():
            self._apply_filter_flags()
            return
        filters = device_tracking.set_rockbox_release_filter(
            device_tracking.FILTER_OLD_ROCKBOX, bool(checked)
        )
        self._apply_filter_flags(filters)
        self.release_filters_changed.emit()

    def _on_nightly_toggled(self, checked: bool):
        if self._filters_loading:
            return
        filters = device_tracking.set_rockbox_release_filter(
            device_tracking.FILTER_NIGHTLY, bool(checked)
        )
        self._apply_filter_flags(filters)
        self.release_filters_changed.emit()

    def _on_240p_toggled(self, checked: bool):
        if self._filters_loading:
            return
        if checked and not self._cb_old_rockbox.isChecked():
            # 240p builds cannot run on Y1 units older than OS 3.0.7, so the
            # older-builds option comes with them — confirmation included.
            self._cb_old_rockbox.setChecked(True)
            if not self._cb_old_rockbox.isChecked():
                self._apply_filter_flags()
                return
        filters = device_tracking.set_rockbox_release_filter(
            device_tracking.FILTER_240P, bool(checked)
        )
        self._apply_filter_flags(filters)
        self.release_filters_changed.emit()

    # ------------------------------------------------------------------
    # Platform preparation cards
    # ------------------------------------------------------------------
    def _build_checker_card(self) -> Card:
        """Linux SP Flash Tool preparation: system checker and GUI launch."""
        card = Card("settings_checker_group")
        layout = QVBoxLayout()
        layout.setSpacing(12)

        self._checker_desc = QLabel(tr("settings_checker_desc"))
        self._checker_desc.setWordWrap(True)
        self._checker_desc.setProperty("cssClass", "subtitle")
        layout.addWidget(self._checker_desc)

        btn_row = QHBoxLayout()
        self._btn_run_checker = QPushButton(tr("system_checker_run_btn"))
        self._btn_run_checker.setProperty("cssClass", "primary")
        self._btn_run_checker.clicked.connect(self._on_run_checker)
        btn_row.addWidget(self._btn_run_checker)

        self._btn_launch_sp = QPushButton(tr("system_checker_launch_gui_btn"))
        self._btn_launch_sp.setProperty("cssClass", "ghost")
        self._btn_launch_sp.clicked.connect(self._on_launch_sp)
        btn_row.addWidget(self._btn_launch_sp)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        card.set_layout(layout)
        return card

    def _build_driver_card(self) -> Card:
        """Windows preparation: install the MediaTek USB driver, then reboot."""
        card = Card("settings_driver_group")
        layout = QVBoxLayout()
        layout.setSpacing(12)

        self._driver_desc = QLabel(tr("settings_driver_desc"))
        self._driver_desc.setWordWrap(True)
        self._driver_desc.setProperty("cssClass", "subtitle")
        layout.addWidget(self._driver_desc)

        btn_row = QHBoxLayout()
        self._btn_download_drivers = QPushButton(tr("settings_download_drivers_btn"))
        self._btn_download_drivers.setProperty("cssClass", "primary")
        self._btn_download_drivers.clicked.connect(self._on_download_drivers)
        btn_row.addWidget(self._btn_download_drivers)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        card.set_layout(layout)
        return card

    def _on_download_drivers(self):
        from ..browser import open_browser

        open_browser(MEDIATEK_DRIVERS_URL)

    # ------------------------------------------------------------------
    # Install method
    # ------------------------------------------------------------------
    def _available_methods(self):
        """Method ids selectable on this platform, in display order."""
        if paths.IS_MAC:
            # No SP Flash Tool build exists for macOS.
            return (METHOD_MTK,)
        if paths.IS_WINDOWS:
            # On Windows, strictly default to SP Flash Tool unless unlocked via 'M' key
            if self._advanced_revealed:
                return (METHOD_SP, METHOD_MTK, METHOD_MTK_MAC)
            return (METHOD_SP,)
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
        self._update_sp_auth_visibility()

    def set_method_enabled(self, enabled: bool):
        """Lock the selector while a run is in progress."""
        self._method_combo.setEnabled(bool(enabled))

    def _on_method_changed(self):
        method = self.current_method()
        self._update_method_note()
        self._update_sp_auth_visibility()
        self.flash_method_changed.emit(method)

    # ------------------------------------------------------------------
    # SP Flash Tool authentication file
    # ------------------------------------------------------------------
    def _update_sp_auth_visibility(self):
        """Offer the auth file only where it can actually be used.

        It is an SP Flash Tool input, so it belongs beside the method selector
        and only for that backend, in the generic MediaTek Installer build that
        offers the option at all (macOS has no SP Flash Tool).
        """
        visible = (
            is_mediatek_installer()
            and not paths.IS_MAC
            and normalise_method(self.current_method()) == METHOD_SP
        )
        self._sp_auth_box.setVisible(visible)

    def _refresh_sp_auth_value(self):
        """Show the stored auth file, or the "not set" placeholder."""
        path = device_tracking.sp_auth_file()
        self._sp_auth_value.setText(path)
        self._sp_auth_value.setToolTip(path or tr("settings_sp_auth_none"))

    def _on_sp_auth_browse(self):
        current = device_tracking.sp_auth_file()
        start_dir = str(Path(current).parent) if current else str(Path.home())
        chosen, _selected = QFileDialog.getOpenFileName(
            self,
            tr("settings_sp_auth_dialog"),
            start_dir,
            tr("settings_sp_auth_filter"),
        )
        if chosen:
            device_tracking.set_sp_auth_file(chosen)
            self._refresh_sp_auth_value()

    def _on_sp_auth_clear(self):
        device_tracking.set_sp_auth_file("")
        self._refresh_sp_auth_value()

    def _update_method_note(self):
        method = self.current_method()
        if method == METHOD_MTK_MAC:
            note = tr("flash_method_note_mtk_mac")
        elif method == METHOD_SP:
            note = tr("flash_method_note_sp")
        else:
            # MTKClient needs no sales pitch; the prep card covers drivers.
            note = ""
        self._method_note.setText(note)
        self._method_note.setVisible(bool(note))

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
        self._prep_card.setVisible(not paths.IS_MAC)

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

        # Rockbox release filters
        self._apply_filter_flags()

        # Terminal install mode
        self._cb_terminal_install.blockSignals(True)
        self._cb_terminal_install.setChecked(
            device_tracking.terminal_install_enabled()
        )
        self._cb_terminal_install.blockSignals(False)

        # Offline mode toggle
        self._cb_offline_mode.blockSignals(True)
        self._cb_offline_mode.setChecked(is_offline_mode())
        self._cb_offline_mode.blockSignals(False)

        # SP Flash Tool authentication file
        self._refresh_sp_auth_value()
        self._update_sp_auth_visibility()

    def apply_brand_mode(self, mediatek_installer: bool):
        """Show or hide the pieces that only make sense for one brand."""
        self._offline_mode_card.setVisible(not mediatek_installer)
        self._update_sp_auth_visibility()

    # ------------------------------------------------------------------
    # Misc actions
    # ------------------------------------------------------------------
    def _on_offline_mode_toggled(self, checked: bool):
        from PySide6.QtCore import QSettings
        QSettings("Innioasis", "UpdaterCE").setValue("offline_mode", checked)
        from .. import config
        # Only the offline switch moves here: the MediaTek Installer identity is
        # fixed at build time and never toggled by a settings checkbox.
        config.IS_OFFLINE_MODE = checked
        self.offline_mode_changed.emit(checked)

    def _on_terminal_install_toggled(self, checked: bool):
        device_tracking.set_terminal_install_enabled(bool(checked))

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
        self._terminal_card.retranslate()
        self._cb_terminal_install.setText(tr("settings_terminal_install"))
        self._cb_terminal_install.setToolTip(terminal_install_desc())
        self._terminal_desc.setText(terminal_install_desc())
        self._reminders_card.retranslate()
        self._cb_reminders.setText(tr("settings_reminders_enable"))
        self._rockbox_card.retranslate()
        self._rockbox_desc.setText(tr("settings_rockbox_desc"))
        self._cb_old_rockbox.setText(tr("settings_filter_old_rockbox"))
        self._cb_old_rockbox.setToolTip(tr("settings_old_rockbox_warn_body"))
        self._cb_nightly.setText(tr("settings_filter_nightly"))
        self._cb_240p.setText(tr("settings_filter_240p"))
        self._cb_240p.setToolTip(tr("settings_240p_tip"))
        self._donations_card.retranslate()
        self._cb_hide_donations.setText(tr("settings_hide_donations"))
        self._cb_skip_install_donations.setText(tr("settings_skip_install_donations"))
        self._prep_card.retranslate()
        if hasattr(self, "_checker_desc"):
            self._checker_desc.setText(tr("settings_checker_desc"))
            self._btn_run_checker.setText(tr("system_checker_run_btn"))
            self._btn_launch_sp.setText(tr("system_checker_launch_gui_btn"))
        if hasattr(self, "_driver_desc"):
            self._driver_desc.setText(tr("settings_driver_desc"))
            self._btn_download_drivers.setText(tr("settings_download_drivers_btn"))
        self._offline_mode_card.retranslate()
        self._cb_offline_mode.setText(tr("settings_offline_mode"))
        self._offline_mode_desc.setText(tr("settings_offline_mode_desc"))
        self._sp_auth_label.setText(tr("settings_sp_auth"))
        self._sp_auth_browse.setText(tr("settings_sp_auth_browse"))
        self._sp_auth_clear.setText(tr("settings_sp_auth_clear"))
        self._sp_auth_desc.setText(tr("settings_sp_auth_desc"))
        self._sp_auth_value.setPlaceholderText(tr("settings_sp_auth_none"))
        self.refresh_settings()
