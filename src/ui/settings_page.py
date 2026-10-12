"""Settings page — install method, release reminders and donation preferences."""

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
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
from ..i18n import tr, tr_brand
from .dark import page_margins
from .icons import get_symbol_icon
from .scrollbars import configure_scroll_area
from .widgets import Card, close_layout_gap, layout_gap_closing


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
        self._method_before = ""
        # Guards the filter checkboxes while they are synced from settings, so
        # syncing never re-enters a handler or reopens a confirmation dialog.
        self._filters_loading = False
        self._install_mode_guard = False
        # checkbox -> (row widget, caption label). The switch sits on the right.
        self._switch_rows = {}
        # model id -> install-status QLabel, filled in by _build_ui.
        self._install_status_labels = {}
        self._build_ui()
        self.refresh_settings()

    def _add_switch(self, layout, text: str) -> QCheckBox:
        """Label on the left, switch on the right, as on macOS and Windows."""
        row = QWidget()
        box = QHBoxLayout(row)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(12)
        caption = QLabel(text)
        caption.setWordWrap(True)
        caption.setCursor(Qt.ArrowCursor)
        switch = QCheckBox()
        switch.setCursor(Qt.ArrowCursor)
        box.addWidget(caption, 1)
        box.addWidget(switch, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(row)
        self._switch_rows[switch] = (row, caption)
        return switch

    def switch_caption(self, switch: QCheckBox) -> str:
        """The words beside a settings switch. The checkbox itself has no text."""
        row = self._switch_rows.get(switch)
        return row[1].text() if row else switch.text()

    def _set_switch_caption(self, switch: QCheckBox, text: str) -> None:
        row = self._switch_rows.get(switch)
        if row is not None:
            row[1].setText(text)
        else:
            switch.setText(text)

    def _add_title_row_switch(self, card, caption_key: str, *, tooltip: str = "") -> QCheckBox:
        """A switch on the card's own title line.

        Used where the title already says it all ("Offline Mode", "Device
        Firmware Release Reminders"), so the words are not printed twice. The
        caption label still exists, hidden, because it is the single place the
        caption wording lives: ``switch_caption()`` and the translations keep
        reading it.
        """
        switch = QCheckBox()
        switch.setCursor(Qt.ArrowCursor)
        caption = QLabel(tr(caption_key))
        caption.setWordWrap(True)
        caption.setVisible(False)
        if tooltip:
            switch.setToolTip(tooltip)
        self._switch_rows[switch] = (caption, caption)
        card.put_widget_on_title_row(switch)
        return switch

    def _set_switch_row_visible(self, switch: QCheckBox, visible: bool) -> None:
        row = self._switch_rows.get(switch)
        if row is not None:
            row[0].setVisible(visible)
        else:
            switch.setVisible(visible)

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(*page_margins())
        root_layout.setSpacing(16)

        # Header
        self._header = QLabel(tr("settings_title"))
        self._header.setProperty("cssClass", "pageTitle")
        self._header.setVisible(False)
        root_layout.addWidget(self._header)

        # Scroll area for clean overflow handling. Transparency comes from the
        # palette, never a stylesheet: a sheet set on a scroll area makes Qt
        # answer SH_ScrollBar_Transient with 0 and the host's floating overlay
        # bars silently become classic ones (see src/ui/scrollbars.py).
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._settings_scroll = scroll

        container = QWidget()
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

        # --- Card 2: Installation mode (terminal and SP GUI installs) ---
        self._terminal_card = Card("settings_terminal_group")
        term_layout = QVBoxLayout()
        term_layout.setSpacing(8)

        self._cb_guided_install = self._add_switch(term_layout, tr("settings_guided_install"))
        self._cb_guided_install.setToolTip(tr("settings_guided_install_desc"))
        self._cb_guided_install.toggled.connect(self._on_guided_install_toggled)
        self._guided_desc = QLabel(tr("settings_guided_install_desc"))
        self._guided_desc.setWordWrap(True)
        self._guided_desc.setProperty("cssClass", "dimmed")
        term_layout.addWidget(self._guided_desc)

        term_text = tr("settings_terminal_install_windows" if paths.IS_WINDOWS else "settings_terminal_install")
        self._cb_terminal_install = self._add_switch(term_layout, term_text)
        self._cb_terminal_install.setToolTip(terminal_install_desc())
        self._cb_terminal_install.toggled.connect(self._on_terminal_install_toggled)

        self._terminal_desc = QLabel(terminal_install_desc())
        self._terminal_desc.setWordWrap(True)
        self._terminal_desc.setProperty("cssClass", "dimmed")
        term_layout.addWidget(self._terminal_desc)

        self._cb_sp_gui_install = self._add_switch(term_layout, tr("settings_sp_gui_install"))
        self._cb_sp_gui_install.setToolTip(tr("settings_sp_gui_desc"))
        self._cb_sp_gui_install.toggled.connect(self._on_sp_gui_install_toggled)

        self._sp_gui_desc = QLabel(tr("settings_sp_gui_desc"))
        self._sp_gui_desc.setWordWrap(True)
        self._sp_gui_desc.setProperty("cssClass", "dimmed")
        term_layout.addWidget(self._sp_gui_desc)

        # Superuser rights for the install. The switch is only a preference:
        # the platform's own question grants the rights, and this app has to
        # restart before its children — every flash tool it spawns, whichever
        # install method is chosen — inherit them. Hence the restart line.
        from .. import elevation

        self._cb_run_as_admin = self._add_switch(term_layout, elevation.label_text())
        self._cb_run_as_admin.setToolTip(elevation.description_text())
        self._cb_run_as_admin.toggled.connect(self._on_run_as_admin_toggled)

        self._run_as_admin_desc = QLabel(elevation.description_text())
        self._run_as_admin_desc.setWordWrap(True)
        self._run_as_admin_desc.setProperty("cssClass", "dimmed")
        term_layout.addWidget(self._run_as_admin_desc)

        self._run_as_admin_status = QLabel("")
        self._run_as_admin_status.setWordWrap(True)
        self._run_as_admin_status.setProperty("cssClass", "subtitle")
        self._run_as_admin_status.setVisible(False)
        term_layout.addWidget(self._run_as_admin_status)

        self._run_as_admin_restart_btn = QPushButton(tr("settings_run_as_admin_restart"))
        self._run_as_admin_restart_btn.clicked.connect(self._on_run_as_admin_restart)
        self._run_as_admin_restart_btn.setVisible(False)
        term_layout.addWidget(self._run_as_admin_restart_btn, alignment=Qt.AlignLeft)

        self._terminal_card.set_layout(term_layout)

        # --- Card 2: Firmware Release Reminders ---
        self._reminders_card = Card("settings_reminders_group")
        rem_layout = QVBoxLayout()
        rem_layout.setSpacing(8)

        # The card title already names the setting, so the switch rides on that
        # line instead of a second, identical caption under it.
        self._cb_reminders = self._add_title_row_switch(
            self._reminders_card, "settings_reminders_enable",
        )
        self._cb_reminders.toggled.connect(self._on_reminders_toggled)

        self._reminders_card.set_layout(rem_layout)
        layout.addWidget(self._reminders_card)

        # --- Rockbox release filters ---
        # These choose between the online Y1/Y2 Rockbox release builds, so they
        # are offered only when there is a catalogue to filter. The card is
        # built here but added to the layout below Firmware Storage & Cache.
        self._rockbox_card = Card("settings_rockbox_group")
        rock_layout = QVBoxLayout()
        rock_layout.setSpacing(8)

        self._rockbox_desc = QLabel(tr("settings_rockbox_desc"))
        self._rockbox_desc.setWordWrap(True)
        self._rockbox_desc.setProperty("cssClass", "subtitle")
        rock_layout.addWidget(self._rockbox_desc)

        self._cb_old_rockbox = self._add_switch(rock_layout, tr("settings_filter_old_rockbox"))
        self._cb_old_rockbox.setToolTip(tr("settings_old_rockbox_warn_body"))
        self._cb_old_rockbox.toggled.connect(self._on_old_rockbox_toggled)

        self._cb_nightly = self._add_switch(rock_layout, tr("settings_filter_nightly"))
        self._cb_nightly.toggled.connect(self._on_nightly_toggled)

        self._cb_240p = self._add_switch(rock_layout, tr("settings_filter_240p"))
        self._cb_240p.setToolTip(tr("settings_240p_tip"))
        self._cb_240p.toggled.connect(self._on_240p_toggled)

        self._rockbox_card.set_layout(rock_layout)

        # --- Card 3: Show App Supporter Credits and Donations ---
        self._donations_card = Card("settings_donations_group")
        don_layout = QVBoxLayout()
        don_layout.setSpacing(12)

        # The card's title *is* the switch's label, so the words are printed
        # once and the switch sits on the title line. On means the supporter
        # credits and the donation prompts are shown.
        self._cb_hide_donations = self._add_title_row_switch(
            self._donations_card, "settings_show_donations",
            tooltip=tr("settings_hide_donations_tip"),
        )
        self._cb_hide_donations.toggled.connect(self._on_hide_donations_toggled)

        # No explainer paragraph: the title already says what the switch does.
        # The label still exists — hidden — because it is where that wording
        # lives for the translations and for switch_caption().
        self._lbl_hide_tip = QLabel(tr("settings_hide_donations_tip"), self._donations_card)
        self._lbl_hide_tip.setWordWrap(True)
        self._lbl_hide_tip.setProperty("cssClass", "dimmed")
        self._lbl_hide_tip.setVisible(False)

        # Backward compatibility aliases
        self._cb_skip_install_donations = self._cb_hide_donations
        self._lbl_skip_tip = self._lbl_hide_tip

        self._donations_actions = QWidget()
        actions = QHBoxLayout(self._donations_actions)
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        self._settings_coffee_btn = QPushButton(tr("donate_coffee_btn"))
        self._settings_coffee_btn.setCursor(Qt.ArrowCursor)
        self._settings_coffee_btn.setStyleSheet("")
        self._settings_coffee_btn.clicked.connect(self._open_settings_coffee)
        self._dismiss_donations_link = QLabel(self._donations_dismiss_html())
        self._dismiss_donations_link.setTextFormat(Qt.TextFormat.RichText)
        self._dismiss_donations_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._dismiss_donations_link.setOpenExternalLinks(False)
        self._dismiss_donations_link.setCursor(Qt.PointingHandCursor)
        self._dismiss_donations_link.setAutoFillBackground(False)
        self._dismiss_donations_link.setStyleSheet("")
        self._dismiss_donations_link.linkActivated.connect(
            lambda _href: self._on_dismiss_donations_section()
        )
        actions.addWidget(self._settings_coffee_btn, 0, Qt.AlignVCenter)
        actions.addWidget(self._dismiss_donations_link, 0, Qt.AlignVCenter)
        actions.addStretch(1)
        don_layout.addWidget(self._donations_actions, alignment=Qt.AlignLeft)

        self._donations_card.set_layout(don_layout)
        layout.addWidget(self._donations_card)

        # --- Firmware Storage & Cache Card ---
        self._cache_card = Card("settings_cache_group")
        cache_layout = QVBoxLayout()
        cache_layout.setSpacing(8)

        self._cache_desc = QLabel(tr("settings_cache_desc"))
        self._cache_desc.setWordWrap(True)
        self._cache_desc.setProperty("cssClass", "dimmed")
        cache_layout.addWidget(self._cache_desc)

        cache_btn_row = QHBoxLayout()
        cache_btn_row.setSpacing(10)
        self._clear_cache_btn = QPushButton(tr("settings_clear_cache_btn"))
        self._clear_cache_btn.setIcon(get_symbol_icon("cancel", 12))
        self._clear_cache_btn.clicked.connect(self._on_clear_firmware_cache)
        cache_btn_row.addWidget(self._clear_cache_btn)

        self._cache_status_lbl = QLabel("")
        cache_btn_row.addWidget(self._cache_status_lbl, 1)
        cache_layout.addLayout(cache_btn_row)

        self._cache_card.set_layout(cache_layout)
        layout.addWidget(self._cache_card)

        # Rockbox filters sit directly below Firmware Storage & Cache.
        layout.addWidget(self._rockbox_card)
        self._update_rockbox_visibility()

        # --- Card 4: Platform preparation ---
        # Linux gets the SP Flash Tool system checker (udev rules, kernel
        # modules); Windows instead needs the MediaTek USB driver installed
        # before the first flash.
        if paths.IS_WINDOWS:
            self._driver_card = self._build_driver_card()
            self._prep_card = self._driver_card
            self._checker_card = self._driver_card
            layout.addWidget(self._driver_card)
        else:
            self._checker_card = self._build_checker_card()
            self._driver_card = self._checker_card
            self._prep_card = self._checker_card
            layout.addWidget(self._checker_card)

        # --- Card 5: Offline Mode ---
        # The generic MediaTek Installer build is offline-only, so the toggle
        # that would re-enable online firmware is not shown there at all.
        self._offline_mode_card = Card("settings_offline_mode_group")
        off_layout = QVBoxLayout()
        off_layout.setSpacing(8)

        self._cb_offline_mode = self._add_title_row_switch(
            self._offline_mode_card, "settings_offline_mode",
            tooltip=tr("settings_offline_mode_desc"),
        )
        self._cb_offline_mode.toggled.connect(self._on_offline_mode_toggled)

        self._offline_mode_desc = QLabel(tr("settings_offline_mode_desc"))
        self._offline_mode_desc.setWordWrap(True)
        self._offline_mode_desc.setProperty("cssClass", "dimmed")
        off_layout.addWidget(self._offline_mode_desc)

        self._offline_mode_card.set_layout(off_layout)
        self._offline_mode_card.setVisible(not is_mediatek_installer())
        layout.addWidget(self._offline_mode_card)

        # --- Card 6: Remove old app (shown only for a pre-3.0 install) ---
        self._legacy_cleanup_card = Card("legacy_cleanup_card_title")
        leg_layout = QVBoxLayout()
        leg_layout.setSpacing(8)
        self._legacy_cleanup_desc = QLabel(tr("legacy_cleanup_card_desc"))
        self._legacy_cleanup_desc.setWordWrap(True)
        self._legacy_cleanup_desc.setProperty("cssClass", "dimmed")
        leg_layout.addWidget(self._legacy_cleanup_desc)

        self._legacy_uninstall_btn = QPushButton(tr("legacy_cleanup_scan_btn"))
        self._legacy_uninstall_btn.clicked.connect(self._on_scan_legacy_installations)
        leg_layout.addWidget(self._legacy_uninstall_btn, alignment=Qt.AlignLeft)

        # One line that carries the result of the journey, in place. The card
        # is not taller for it: the label stays hidden until there is news.
        self._legacy_status_lbl = QLabel("")
        self._legacy_status_lbl.setWordWrap(True)
        self._legacy_status_lbl.setProperty("cssClass", "subtitle")
        self._legacy_status_lbl.setVisible(False)
        leg_layout.addWidget(self._legacy_status_lbl)

        self._legacy_cleanup_card.set_layout(leg_layout)
        self._update_legacy_cleanup_visibility()
        layout.addWidget(self._legacy_cleanup_card)
        layout.addWidget(self._terminal_card)
        layout.addWidget(self._method_card)
        self._card_layout = layout

        # The desktop installer is not offered on macOS.
        # Hide backend selection and diagnostics cards on macOS.
        self._method_card.setVisible(not paths.IS_MAC)
        self._prep_card.setVisible(not paths.IS_MAC)

        layout.addStretch()
        scroll.setWidget(container)
        # Transparent *after* the content widget is in place: QScrollArea turns
        # that widget's auto-fill back on inside setWidget(), and the solid
        # Base it then painted was the dark band between the cards.
        configure_scroll_area(scroll, transparent=True)
        root_layout.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Rockbox release filters
    # ------------------------------------------------------------------
    def _confirm_old_rockbox(self) -> bool:
        """Ask, inside the filter card, before unlocking pre-0.5 Rockbox builds."""
        return self._rockbox_card.run_confirmation(
            tr("settings_old_rockbox_warn_body"),
            tr("dialog_pre_install_continue"),
            tr("flash_btn_cancel"),
        )

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
        self._btn_run_checker.clicked.connect(self._on_run_checker)
        btn_row.addWidget(self._btn_run_checker)

        self._btn_launch_sp = QPushButton(tr("system_checker_launch_gui_btn"))
        self._btn_launch_sp.clicked.connect(self._on_launch_sp)
        btn_row.addWidget(self._btn_launch_sp)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # The preparation reports here instead of in a window of its own: the
        # engine download, the readiness verdict, and anything that needs the
        # user's say-so all land on this one line. Hidden while there is
        # nothing to say, so the card is no taller for it.
        self._checker_status_lbl = QLabel("")
        self._checker_status_lbl.setWordWrap(True)
        self._checker_status_lbl.setProperty("cssClass", "subtitle")
        self._checker_status_lbl.setVisible(False)
        layout.addWidget(self._checker_status_lbl)

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
        if method in (METHOD_MTK, METHOD_MTK_MAC) and not paths.IS_MAC:
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
        self._method_before = self.current_method()

    def set_method_enabled(self, enabled: bool):
        """Lock the selector while a run is in progress."""
        self._method_combo.setEnabled(bool(enabled))

    def _on_method_changed(self):
        method = self.current_method()
        previous = getattr(self, "_method_before", "") or self._persisted_method()
        if method == METHOD_MTK_MAC and previous != METHOD_MTK_MAC:
            # The restart question replaces this card. Later puts the previous
            # method back. Restart Now is what actually relaunches.
            accepted = self._method_card.run_confirmation(
                tr_brand("simulated_mac_body"),
                tr("simulated_mac_restart_now"),
                tr("simulated_mac_later"),
                note=tr("flash_method_note_mtk_mac"),
            )
            if not accepted:
                self.set_method(previous)
                return
            self._method_before = method
            self._update_method_note()
            self._update_sp_auth_visibility()
            self.flash_method_changed.emit(method)
            self.simulated_mac_requested.emit()
            return
        self._method_before = method
        self._update_method_note()
        self._update_sp_auth_visibility()
        self.flash_method_changed.emit(method)

    # ------------------------------------------------------------------
    # SP Flash Tool authentication file
    # ------------------------------------------------------------------
    def _update_rockbox_visibility(self):
        """Offer the Rockbox release filters only with a catalogue to filter.

        They filter the online Y1/Y2 Rockbox release list, so with no online
        catalogue — offline mode, or a build of the generic MediaTek Installer,
        which is offline by definition — there is nothing for them to act on.
        """
        self._rockbox_card.setVisible(not is_offline_mode())

    def _update_legacy_cleanup_visibility(self):
        """Show the removal card only while a pre-3.0 install is on disk.

        The card is an offer to uninstall the old pre-3.0 app (its .app, its
        Application Support folder, or the Linux leftovers). It is not a
        general cleaner: stray Android platform tools alone do not raise it,
        and the card disappears again once the old app is gone.
        """
        if is_mediatek_installer() or paths.IS_WINDOWS:
            self._legacy_cleanup_card.setVisible(False)
            return
        try:
            from ..legacy_cleanup import detect_legacy_installations

            info = detect_legacy_installations()
            visible = bool(info.get("has_legacy"))
        except Exception:
            visible = False
        self._legacy_cleanup_card.setVisible(visible)
        if not visible and hasattr(self, "_legacy_status_lbl"):
            self._legacy_status_lbl.clear()
            self._legacy_status_lbl.setVisible(False)

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
        self._update_rockbox_visibility()
        self._update_legacy_cleanup_visibility()

        # Install method selector
        self.set_method(self._persisted_method())

        # Single reminder opt-in: on if any tracked device is still enabled.
        any_enabled = any(
            device_tracking.is_device_reminder_enabled(m) for m in DEVICE_MODELS
        )
        self._cb_reminders.blockSignals(True)
        self._cb_reminders.setChecked(any_enabled)
        self._cb_reminders.blockSignals(False)

        # Single donation opt-out switch
        opted_out = (
            device_tracking.is_donation_ui_disabled()
            or device_tracking.is_donation_install_prompt_disabled()
        )
        self._cb_hide_donations.blockSignals(True)
        # The switch reads "shown"; the stored flags read "disabled".
        self._cb_hide_donations.setChecked(not opted_out)
        self._cb_hide_donations.blockSignals(False)
        self._sync_donation_opt_out()
        self._apply_donations_section_visibility()

        # Rockbox release filters
        self._apply_filter_flags()

        term_text = tr("settings_terminal_install_windows" if paths.IS_WINDOWS else "settings_terminal_install")
        self._set_switch_caption(self._cb_terminal_install, term_text)
        self._set_switch_caption(self._cb_guided_install, tr("settings_guided_install"))
        self._set_switch_caption(self._cb_sp_gui_install, tr("settings_sp_gui_install"))
        self._sync_install_mode_switches()

        # Superuser preference. It is independent of the install-mode choice:
        # it applies to whichever of them runs.
        from .. import elevation

        if self._run_as_admin_switch() is not None:
            self._cb_run_as_admin.blockSignals(True)
            self._cb_run_as_admin.setChecked(
                elevation.run_as_admin_enabled(QSettings("innioasis", "updater"))
            )
            self._cb_run_as_admin.blockSignals(False)
            self._sync_run_as_admin_controls()

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
        self._update_rockbox_visibility()
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

    def _sync_install_mode_switches(self) -> None:
        """One install choice at a time. Guided is the one that is on by default."""
        from ..sp_flash_gui import is_sp_flash_gui_supported

        sp_supported = is_sp_flash_gui_supported()
        terminal = device_tracking.terminal_install_enabled()
        sp_gui = device_tracking.sp_gui_install_enabled() and sp_supported
        # The install path checks the graphical tool first, so that choice wins
        # when an older build left both flags on.
        if terminal and sp_gui:
            device_tracking.set_terminal_install_enabled(False)
            terminal = False
        guided = not terminal and not sp_gui
        self._install_mode_guard = True
        try:
            for box, checked in (
                (self._cb_guided_install, guided),
                (self._cb_terminal_install, terminal),
                (self._cb_sp_gui_install, sp_gui),
            ):
                box.blockSignals(True)
                box.setChecked(checked)
                box.blockSignals(False)
        finally:
            self._install_mode_guard = False
        self._set_switch_row_visible(self._cb_sp_gui_install, sp_supported)
        self._sp_gui_desc.setVisible(sp_supported)

    def _on_guided_install_toggled(self, checked: bool):
        if self._install_mode_guard:
            return
        if checked:
            device_tracking.set_terminal_install_enabled(False)
            device_tracking.set_sp_gui_install_enabled(False)
        self._sync_install_mode_switches()

    def _on_terminal_install_toggled(self, checked: bool):
        if self._install_mode_guard:
            return
        device_tracking.set_terminal_install_enabled(bool(checked))
        if checked:
            device_tracking.set_sp_gui_install_enabled(False)
        self._sync_install_mode_switches()

    def _on_sp_gui_install_toggled(self, checked: bool):
        if self._install_mode_guard:
            return
        device_tracking.set_sp_gui_install_enabled(bool(checked))
        if checked:
            device_tracking.set_terminal_install_enabled(False)
        self._sync_install_mode_switches()

    # ------------------------------------------------------------------
    # Superuser rights
    # ------------------------------------------------------------------
    def _run_as_admin_switch(self) -> QCheckBox:
        return getattr(self, "_cb_run_as_admin", None)

    def run_as_admin(self) -> bool:
        """The persisted preference, as this page shows it."""
        from .. import elevation

        switch = self._run_as_admin_switch()
        if switch is not None:
            return bool(switch.isChecked())
        return elevation.run_as_admin_enabled(QSettings("innioasis", "updater"))

    def _on_run_as_admin_toggled(self, checked: bool):
        from .. import elevation

        elevation.set_run_as_admin(QSettings("innioasis", "updater"), bool(checked))
        self._sync_run_as_admin_controls()

    def _on_run_as_admin_restart(self):
        """Ask the platform to start an elevated copy and hand over to it."""
        from .. import elevation

        ok, message = elevation.restart_as_admin()
        if ok:
            # The elevated copy is starting; this one steps aside so there is
            # never two apps driving the same ports.
            QApplication.quit()
            return
        self._run_as_admin_status.setText(
            message or tr("settings_run_as_admin_unavailable")
        )
        self._run_as_admin_status.setVisible(True)

    def _sync_run_as_admin_controls(self):
        """Say whether the next install really will run with the rights.

        Three states, one line: the switch is unusable because this desktop has
        no elevation helper; the switch is on but this copy is not elevated
        yet, so a restart is offered; or everything already matches and there
        is nothing to say.
        """
        from .. import elevation

        switch = self._run_as_admin_switch()
        if switch is None or not hasattr(self, "_run_as_admin_status"):
            return
        available = elevation.can_elevate()
        switch.setEnabled(available)
        needs_restart = bool(switch.isChecked() and available and not elevation.is_elevated())
        if not available:
            self._run_as_admin_status.setText(tr("settings_run_as_admin_unavailable"))
            self._run_as_admin_status.setVisible(True)
        elif needs_restart:
            self._run_as_admin_status.setText(tr("settings_run_as_admin_restart_needed"))
            self._run_as_admin_status.setVisible(True)
        else:
            self._run_as_admin_status.clear()
            self._run_as_admin_status.setVisible(False)
        self._run_as_admin_restart_btn.setVisible(needs_restart)

    def _donations_dismiss_html(self) -> str:
        return f'<a href="dismiss">{tr("settings_donations_dismiss")}</a>'

    def _open_settings_coffee(self) -> None:
        from ..browser import open_browser
        open_browser("https://ko-fi.com/teamslide")

    def _sync_donation_opt_out(self) -> None:
        """The coffee button stays. Other donation prompts in this card do not."""
        if hasattr(self, "_settings_coffee_btn"):
            self._settings_coffee_btn.setVisible(True)
        if hasattr(self, "_dismiss_donations_link"):
            self._dismiss_donations_link.setVisible(True)

    def _apply_donations_section_visibility(self) -> None:
        """The dismiss link removes this card. The donation switch does not."""
        if layout_gap_closing(self._donations_card):
            return
        dismissed = device_tracking.is_settings_donations_section_dismissed()
        if not dismissed:
            self._donations_card.setMaximumHeight((1 << 24) - 1)
            self._donations_card.setMinimumHeight(0)
            self._donations_card.clearMask()
        self._donations_card.setVisible(not dismissed)

    def _on_dismiss_donations_section(self) -> None:
        """Drop the donations card for good. Prompts elsewhere are unchanged."""
        if layout_gap_closing(self._donations_card):
            return
        if not device_tracking.is_settings_donations_section_dismissed():
            device_tracking.set_settings_donations_section_dismissed(True)
        close_layout_gap(self._donations_card)

    def _on_hide_donations_toggled(self, checked: bool):
        """The card's switch moved.

        The switch reads "Show App Supporter Credits and Donations", so
        ``checked`` is the *shown* state: the persisted flags are its opposite,
        and so is the emitted value (``donation_visibility_changed`` has always
        carried "disabled").
        """
        self._set_donations_shown(checked)

    def _set_donations_shown(self, shown: bool) -> None:
        """Persist the choice and tell the rest of the app about it."""
        disabled = not bool(shown)
        device_tracking.set_donation_ui_disabled(disabled)
        device_tracking.set_donation_install_prompt_disabled(disabled)
        self._sync_donation_opt_out()
        self.donation_visibility_changed.emit(disabled)

    def _on_skip_install_donations_toggled(self, checked: bool):
        """Legacy alias handler: here ``checked`` means "prompts disabled"."""
        device_tracking.set_donation_install_prompt_disabled(checked)
        device_tracking.set_donation_ui_disabled(checked)
        self._cb_hide_donations.setChecked(not checked)

    def _on_clear_firmware_cache(self):
        from ..downloads import clear_firmware_cache
        count, freed = clear_firmware_cache()
        if freed >= 1024 * 1024:
            freed_str = f"{freed / (1024 * 1024):.1f} MB"
        elif freed >= 1024:
            freed_str = f"{freed / 1024:.1f} KB"
        else:
            freed_str = f"{freed} B"
        msg = tr("settings_cache_cleared_msg").format(count=count, size=freed_str)
        self._cache_status_lbl.setText(msg)

    def _on_run_checker(self):
        """Prepare SP Flash Tool from this card, not from a window of its own.

        The old journey opened a setup window with a header, a download box,
        three status cards and a log — a second Settings page for one button.
        The same worker runs here instead and the card carries its progress:
        stage the engine, verify the host, and ask once on the card before
        anything that needs root.
        """
        if getattr(self, "_sp_prep_worker", None) is not None:
            return
        try:
            from .. import linux_sp_flash
        except Exception as exc:
            logger.debug("SP Flash Tool prep unavailable", exc_info=True)
            self._show_checker_status(str(exc), warn=True)
            return
        self._btn_run_checker.setEnabled(False)
        self._show_checker_status(tr("linux_prep_starting"))
        worker = linux_sp_flash.LinuxStagingWorker(self)
        self._sp_prep_worker = worker
        worker.progress.connect(self._on_sp_prep_progress)
        worker.finished.connect(self._on_sp_prep_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def start_sp_flash_prep(self):
        """Prepare SP Flash Tool on this card, for the Linux entry points.

        The Linux preparation used to be a window of its own opened from the
        navigation rail and on first run. Both now land here, so there is one
        place that prepares the engine and says how it went.
        """
        if paths.IS_MAC or is_mediatek_installer() or not hasattr(self, "_checker_status_lbl"):
            # macOS has no SP Flash Tool, and the Windows card is the driver
            # card rather than the preparation one.
            return
        self._checker_card.setVisible(True)
        self._reveal_checker_card()
        if getattr(self, "_sp_prep_worker", None) is None:
            self._on_run_checker()

    def _on_sp_prep_progress(self, percent: int, message: str):
        line = f"{int(percent)}%"
        if message:
            line = f"{line} — {message}"
        self._show_checker_status(line)

    def _on_sp_prep_finished(self, ok: bool, message: str, report: dict):
        self._sp_prep_worker = None
        self._btn_run_checker.setEnabled(True)
        if not ok:
            self._show_checker_status(
                tr("linux_setup_issue_fmt").format(msg=message), warn=True
            )
            return
        self._render_sp_report(report or {})

    def _render_sp_report(self, report: dict, *, ask_again: bool = True):
        """Say the verdict in one line, and offer the fix on the card."""
        items = [it for it in report.get("items", []) if it.get("status") != "ok"]
        blocking = [it for it in items if not it.get("can_fix")]
        if blocking:
            first = blocking[0]
            self._show_checker_status(
                first.get("detail") or first.get("badge") or tr("linux_prep_blocked"),
                warn=True,
            )
            return
        if not items:
            self._show_checker_status(tr("linux_prep_ready"))
            return
        if not report.get("can_auto_fix", True):
            self._show_checker_status(tr("linux_prep_ready"))
            return
        if not ask_again:
            # One root attempt per press: asking for the same password twice
            # is noise, and a second prompt would not fix anything more.
            first = items[0]
            self._show_checker_status(
                first.get("badge") or first.get("detail") or tr("linux_prep_needs_access"),
                warn=True,
            )
            return
        agreed = self._checker_card.run_confirmation(
            tr("linux_prep_fix_body"),
            tr("linux_prep_fix_btn"),
            tr("flash_btn_cancel"),
        )
        if not agreed:
            self._show_checker_status(tr("linux_prep_needs_access"), warn=True)
            return
        self._show_checker_status(tr("linux_prep_fixing"))
        QApplication.processEvents()
        try:
            from .. import linux_sp_flash

            linux_sp_flash.auto_fix_permissions()
            verdict = linux_sp_flash.run_system_checker()
        except Exception as exc:
            logger.debug("SP Flash Tool permission fix failed", exc_info=True)
            self._show_checker_status(str(exc), warn=True)
            return
        if verdict.get("overall_ready"):
            self._show_checker_status(tr("linux_prep_fixed"))
        else:
            self._render_sp_report(verdict, ask_again=False)

    def _show_checker_status(self, text: str, *, warn: bool = False):
        label = self._checker_status_lbl
        label.setText(text)
        label.setProperty("cssClass", "warning-banner" if warn else "subtitle")
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.setVisible(True)
        self._reveal_checker_card()

    def _reveal_checker_card(self):
        """Scroll the preparation card fully into view as it grows."""
        card = getattr(self, "_checker_card", None)
        scroll = getattr(self, "_settings_scroll", None)
        if card is None or scroll is None:
            return
        card.adjustSize()
        QTimer.singleShot(0, card, lambda: scroll.ensureWidgetVisible(card, 0, 12))

    def _on_launch_sp(self):
        from .. import sp_flash_gui

        ok, msg = sp_flash_gui.open_sp_flash_tool_gui()
        if not ok:
            self._show_checker_status(msg, warn=True)

    def retranslate(self):
        """Retranslate UI elements upon language switch."""
        self._header.setText(tr("settings_title"))
        self._method_card.retranslate()
        self._method_desc.setText(tr("settings_method_desc"))
        self._method_label.setText(tr("flash_method"))
        self._reload_method_options()
        self._terminal_card.retranslate()
        term_text = tr("settings_terminal_install_windows" if paths.IS_WINDOWS else "settings_terminal_install")
        self._set_switch_caption(self._cb_guided_install, tr("settings_guided_install"))
        self._cb_guided_install.setToolTip(tr("settings_guided_install_desc"))
        if hasattr(self, "_guided_desc"):
            self._guided_desc.setText(tr("settings_guided_install_desc"))
        self._set_switch_caption(self._cb_terminal_install, term_text)
        self._cb_terminal_install.setToolTip(terminal_install_desc())
        self._terminal_desc.setText(terminal_install_desc())
        self._set_switch_caption(self._cb_sp_gui_install, tr("settings_sp_gui_install"))
        self._cb_sp_gui_install.setToolTip(tr("settings_sp_gui_desc"))
        self._sp_gui_desc.setText(tr("settings_sp_gui_desc"))
        if hasattr(self, "_cb_run_as_admin"):
            from .. import elevation

            self._set_switch_caption(self._cb_run_as_admin, elevation.label_text())
            self._cb_run_as_admin.setToolTip(elevation.description_text())
            self._run_as_admin_desc.setText(elevation.description_text())
            self._run_as_admin_restart_btn.setText(tr("settings_run_as_admin_restart"))
            self._sync_run_as_admin_controls()
        self._reminders_card.retranslate()
        self._set_switch_caption(self._cb_reminders, tr("settings_reminders_enable"))
        self._rockbox_card.retranslate()
        self._rockbox_desc.setText(tr("settings_rockbox_desc"))
        self._set_switch_caption(self._cb_old_rockbox, tr("settings_filter_old_rockbox"))
        self._cb_old_rockbox.setToolTip(tr("settings_old_rockbox_warn_body"))
        self._set_switch_caption(self._cb_nightly, tr("settings_filter_nightly"))
        self._set_switch_caption(self._cb_240p, tr("settings_filter_240p"))
        self._cb_240p.setToolTip(tr("settings_240p_tip"))
        self._donations_card.retranslate()
        self._set_switch_caption(self._cb_hide_donations, tr("settings_show_donations"))
        self._cb_hide_donations.setToolTip(tr("settings_hide_donations_tip"))
        if hasattr(self, "_lbl_hide_tip"):
            self._lbl_hide_tip.setText(tr("settings_hide_donations_tip"))
        if hasattr(self, "_settings_coffee_btn"):
            self._settings_coffee_btn.setText(tr("donate_coffee_btn"))
        if hasattr(self, "_dismiss_donations_link"):
            self._dismiss_donations_link.setText(self._donations_dismiss_html())
        self._sync_donation_opt_out()
        if hasattr(self, "_cache_card"):
            self._cache_card.retranslate()
        if hasattr(self, "_cache_desc"):
            self._cache_desc.setText(tr("settings_cache_desc"))
        if hasattr(self, "_clear_cache_btn"):
            self._clear_cache_btn.setText(tr("settings_clear_cache_btn"))
        if hasattr(self, "_checker_card"):
            self._checker_card.retranslate()
        if hasattr(self, "_driver_card"):
            self._driver_card.retranslate()
        if hasattr(self, "_checker_desc"):
            self._checker_desc.setText(tr("settings_checker_desc"))
            self._btn_run_checker.setText(tr("system_checker_run_btn"))
            self._btn_launch_sp.setText(tr("system_checker_launch_gui_btn"))
        if hasattr(self, "_driver_desc"):
            self._driver_desc.setText(tr("settings_driver_desc"))
            self._btn_download_drivers.setText(tr("settings_download_drivers_btn"))
        self._offline_mode_card.retranslate()
        self._set_switch_caption(self._cb_offline_mode, tr("settings_offline_mode"))
        self._cb_offline_mode.setToolTip(tr("settings_offline_mode_desc"))
        self._offline_mode_desc.setText(tr("settings_offline_mode_desc"))
        self._sp_auth_label.setText(tr("settings_sp_auth"))
        self._sp_auth_browse.setText(tr("settings_sp_auth_browse"))
        self._sp_auth_clear.setText(tr("settings_sp_auth_clear"))
        self._sp_auth_desc.setText(tr("settings_sp_auth_desc"))
        self._sp_auth_value.setPlaceholderText(tr("settings_sp_auth_none"))
        if hasattr(self, "_legacy_cleanup_card"):
            self._legacy_cleanup_card.retranslate()
        if hasattr(self, "_legacy_cleanup_desc"):
            self._legacy_cleanup_desc.setText(tr("legacy_cleanup_card_desc"))
        if hasattr(self, "_legacy_uninstall_btn"):
            self._legacy_uninstall_btn.setText(tr("legacy_cleanup_scan_btn"))
        self.refresh_settings()

    def refresh_theme(self):
        """Re-fetch theme-colored icons after a system appearance change."""
        if hasattr(self, "_clear_cache_btn"):
            self._clear_cache_btn.setIcon(get_symbol_icon("cancel", 12))
        self.update()

    def _on_scan_legacy_installations(self):
        """The removal journey, compact and inside this card.

        The old app used to be removed from a modal of its own: a title, a
        paragraph, a boxed component list, two checkboxes and a button pair
        that took over the screen for one decision. The card now says what
        goes, asks once in place, and reports the space it freed before
        hiding itself — no dialog, no window, just the card changing.
        """
        from ..legacy_cleanup import detect_legacy_installations, format_bytes

        info = detect_legacy_installations()
        if not (info.get("has_legacy") or info.get("has_platform_tools")):
            self._show_legacy_status(tr("legacy_cleanup_none_found"))
            return

        reclaim = format_bytes(int(info.get("estimated_bytes", 0) or 0))
        agreed = self._legacy_cleanup_card.run_confirmation(
            tr("legacy_cleanup_confirm_body"),
            tr("legacy_cleanup_btn_clean"),
            tr("legacy_cleanup_btn_later"),
            note=tr("legacy_cleanup_reclaim").format(size=reclaim),
        )
        if not agreed:
            self._reveal_legacy_card()
            return

        # Android platform tools are the app's own ADB backend, but they are
        # also the user's: ask before taking them, and put the answer that
        # keeps a working ADB first. Asked only once the removal is agreed to,
        # so walking away asks nothing.
        remove_tools = False
        if info.get("has_platform_tools"):
            keep_tools = self._legacy_cleanup_card.run_confirmation(
                tr("legacy_cleanup_tools_body"),
                tr("legacy_cleanup_keep_btn"),
                tr("legacy_cleanup_remove_btn"),
            )
            remove_tools = not keep_tools

        errors = self._run_legacy_removal(uninstall_platform_tools=remove_tools)
        if errors:
            self._show_legacy_status("\n".join(errors), warn=True)
            return
        self._show_legacy_status(
            tr("legacy_cleanup_done_inline").format(size=reclaim),
            hide_after_ms=4000,
        )

    def _run_legacy_removal(self, *, uninstall_platform_tools: bool = False):
        """Delete the pre-3.0 leftovers. Returns the errors, if any.

        ``uninstall_platform_tools`` is the user's answer to this card's own
        question; it defaults to keeping them, since a flash run needs ADB.
        """
        from ..legacy_cleanup import remove_linux_legacy, remove_macos_legacy

        try:
            if paths.IS_MAC:
                _ok, _removed, errors = remove_macos_legacy(
                    remove_apps=True,
                    remove_app_support=True,
                    uninstall_platform_tools=bool(uninstall_platform_tools),
                )
            else:
                _ok, _removed, errors = remove_linux_legacy(
                    remove_install_dir=True,
                    uninstall_platform_tools=bool(uninstall_platform_tools),
                )
        except Exception as exc:  # a stubborn path must not take the page down
            logger.debug("Legacy removal failed", exc_info=True)
            return [str(exc)]
        return [str(e) for e in (errors or [])]

    def _show_legacy_status(self, text: str, *, warn: bool = False, hide_after_ms: int = 0):
        """Write the journey's result on the card and scroll it into view."""
        label = self._legacy_status_lbl
        label.setText(text)
        label.setProperty("cssClass", "warning-banner" if warn else "subtitle")
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        label.setVisible(True)
        self._reveal_legacy_card()
        if hide_after_ms:
            QTimer.singleShot(int(hide_after_ms), self, self._update_legacy_cleanup_visibility)

    def _reveal_legacy_card(self):
        """Grow the card and move the scroll so the whole card is on screen.

        The card sits low in the Settings column, so a taller card can grow
        past the bottom edge — the scroll follows it instead of leaving the
        new line half cut off.
        """
        card = self._legacy_cleanup_card
        card.adjustSize()
        scroll = getattr(self, "_settings_scroll", None)
        if scroll is None:
            return
        QTimer.singleShot(0, card, lambda: scroll.ensureWidgetVisible(card, 0, 12))
