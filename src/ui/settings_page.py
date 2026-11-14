"""Settings page, install method, release reminders and donation preferences."""

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, QSize, Qt, QThread, QTimer, Signal
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

from .. import catalog, device_tracking, fx, paths, shortcut_manager
from ..config import (
    DEVICE_MODELS,
    device_label_for_model,
    hardware_types_for_model,
    is_mediatek_installer,
    is_offline_mode,
)
from ..flash_service import (
    METHOD_MTK,
    METHOD_SP,
    default_flash_method,
    normalise_method,
    windows_only_method,
)
from ..i18n import tr, tr_brand
from .dark import T, page_margins
from .icons import get_symbol_icon
from .scrollbars import configure_scroll_area
from .widgets import Card, close_layout_gap, layout_gap_closing


def terminal_install_desc() -> str:
    """Terminal install description, worded for this platform's console app."""
    key = "settings_terminal_desc_windows" if paths.IS_WINDOWS else "settings_terminal_desc"
    return tr(key)


def run_in_background_desc(platform: str | None = None) -> str:
    """Run-in-background copy, worded for the surface this host actually has.

    macOS keeps the app in the menu bar; Windows and Linux keep it in the
    system tray. Naming both told every reader about a place their desktop
    does not have.
    """
    host = (platform or sys.platform).lower()
    key = (
        "settings_run_in_background_desc_menu_bar"
        if host == "darwin"
        else "settings_run_in_background_desc_system_tray"
    )
    return tr(key)

logger = logging.getLogger(__name__)

# Where Windows users get the MediaTek USB driver before their first flash.
MEDIATEK_DRIVERS_URL = "https://innioasis.app/guide.html"
# ARM64 Windows (native or x86-64 under Prism) needs a different driver guide.
MEDIATEK_DRIVERS_ARM64_URL = "https://armsdr.com/drivers.html"

# Install-method identifiers are owned by flash_service; platform routing
# chooses the available flash engine without exposing platform-specific aliases.


class _AddDeviceReleasesWorker(QThread):
    """Fetches the live release list for one package off the UI thread."""

    loaded = Signal(object)

    def __init__(self, package, model, selected_type):
        super().__init__()
        self._package = package
        self._model = model
        self._selected_type = selected_type

    def run(self):
        try:
            releases = catalog.ReleasesClient().releases_for_package(
                self._package,
                self._model,
                selected_type=self._selected_type,
            )
        except Exception:
            logging.exception("Add device: could not load releases")
            releases = []
        self.loaded.emit(releases)


def _stop_add_device_workers(workers) -> None:
    """Interrupt and wait for any release fetch still running when the page goes."""
    for worker in list(workers):
        worker.requestInterruption()
        worker.wait(1000)


class SettingsPage(QWidget):
    """Settings page for install method, release reminders and donations."""

    donation_visibility_changed = Signal(bool)  # is_disabled
    check_updates_requested = Signal()
    flash_method_changed = Signal(str)
    offline_mode_changed = Signal(bool)
    release_filters_changed = Signal()
    # A card grew or shrank; the window sizes itself to the column, so a line
    # that appears at runtime (the space a cache clear freed) can widen the
    # window instead of being cut off at the right edge.
    content_size_changed = Signal()
    # A card that was shown on its own has finished its journey; the window
    # can give the Settings column back. Linux staging has a separate signal
    # because its first-run entry returns to Select Software on success.
    card_focus_finished = Signal()
    linux_prep_completed = Signal()
    advanced_settings_changed = Signal(bool)
    install_rockbox_themes_requested = Signal()
    refresh_themes_requested = Signal()
    # The in-beta themes auto-install toggle flipped; the window starts or
    # stops the passive sync to match.
    themes_auto_install_changed = Signal(bool)
    # A portable run cannot keep background running or notifications; the
    # switch asks the window for the screen that installs the app itself.
    install_app_requested = Signal()

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
        # The card the page is focused on, or None. While one is focused every
        # other card stays hidden whatever its own rule says.
        self._solo_card = None
        self._sp_gui_available = False
        self._build_ui()
        self.refresh_settings()

    def content_width_hint(self) -> int:
        """The narrowest this column can be drawn without clipping a card.

        A widget-resizable ``QScrollArea`` answers the layout with its own
        (tiny) minimum, so the window could be dragged narrow until the cards
        ran off the right edge and the scroll area hid the overflow behind a
        horizontal scroll — the Settings column was cut off in exactly that
        state. The container's minimum is what the cards actually need.
        """
        width = int(self.minimumSizeHint().width())
        scroll = getattr(self, "_settings_scroll", None)
        if scroll is not None:
            inner = scroll.widget()
            if inner is not None:
                width = max(width, int(inner.minimumSizeHint().width()) + 2 * scroll.frameWidth())
            bar = scroll.verticalScrollBar()
            if bar is not None:
                # The bar sits inside the scroll area, so it takes from the
                # viewport the cards are laid out in.
                width += bar.sizeHint().width()
        layout = self.layout()
        if layout is not None:
            margins = layout.contentsMargins()
            width += margins.left() + margins.right()
        return width

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
        switch.setStyleSheet("")  # no host style override
        box.addWidget(caption, 1)
        box.addWidget(switch, 0, Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(row)
        self._switch_rows[switch] = (row, caption)
        switch.text = caption.text
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
        switch.setStyleSheet("")  # no host style override
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

    def _cards_in_column(self):
        """Every card that shares the Settings column, in column order.

        Read from the column layout rather than a hand-kept list of attribute
        names: a card added to Settings later is part of the column and must be
        hidden with the rest while one card is focused. The shortcut card was
        missing from that list, so it stayed on screen behind a focused journey.
        """
        cards = []
        for widget in self._column_widgets():
            if isinstance(widget, Card) and widget not in cards:
                cards.append(widget)
        for name in ("_checker_card", "_prep_card"):
            card = getattr(self, name, None)
            if card is not None and card not in cards:
                cards.append(card)
        return cards

    def _column_widgets(self):
        """The Settings scroll column's top-level widgets, in order."""
        scroll = getattr(self, "_settings_scroll", None)
        container = scroll.widget() if scroll is not None else None
        layout = container.layout() if container is not None else None
        if layout is None:
            return []
        widgets = []
        for index in range(layout.count()):
            item = layout.itemAt(index)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widgets.append(widget)
        return widgets

    def _may_show_card(self, card) -> bool:
        """False for every card but the focused one, while one is focused."""
        return self._solo_card is None or self._solo_card is card

    def _sp_prep_card_available(self) -> bool:
        """Whether the platform preparation card belongs in Settings.

        Windows always offers its MediaTek USB driver card. macOS has no SP
        Flash Tool. On Linux the card prepares the optional SP Flash Tool
        backend only, so it stays out of the way unless Show Advanced Settings
        is on and SP Flash Tool is the selected method; MTKClient is the
        default there and needs no system preparation.
        """
        if paths.IS_MAC:
            return False
        if paths.IS_WINDOWS:
            return True
        if not self._advanced_shown():
            return False
        return normalise_method(self.current_method()) == METHOD_SP

    def _update_prep_card_visibility(self) -> None:
        """Apply the platform preparation card's own visibility rules.

        A focused card belongs to an explicit journey (Linux setup), not to
        the ordinary Settings column, so it stays up until the journey ends.
        """
        card = getattr(self, "_prep_card", None)
        if card is None:
            return
        if self._solo_card is not None:
            card.setVisible(self._solo_card is card)
            return
        card.setVisible(self._sp_prep_card_available())

    def content_height_hint(self) -> int:
        """Height needed by the Settings page, hugging a solo card when focused."""
        card = getattr(self, "_solo_card", None)
        if card is not None:
            card.adjustSize()
            height = max(
                int(card.sizeHint().height()),
                int(card.minimumSizeHint().height()),
            )
            layout = self.layout()
            if layout is not None:
                margins = layout.contentsMargins()
                height += margins.top() + margins.bottom()
            return max(1, height)
        scroll = getattr(self, "_settings_scroll", None)
        if scroll is not None and scroll.widget() is not None:
            inner = scroll.widget()
            height = int(inner.sizeHint().height()) + 2 * scroll.frameWidth()
            layout = self.layout()
            if layout is not None:
                margins = layout.contentsMargins()
                height += margins.top() + margins.bottom()
            return max(1, height)
        return max(1, int(self.sizeHint().height()))

    def show_card_alone(self, card) -> bool:
        """Put one card on screen by itself and hide the rest.

        The Linux entry point and the removal offer both want the user's whole
        attention, and neither is a pop-out window: focusing the page is what
        lets the main window size itself to the single card.

        Refuses a card this host does not offer. The launch check can find a
        pre-3.0 folder on a platform whose Settings column never shows the
        removal card; focusing it anyway hid every other card behind a journey
        that could not finish, with the scroll switched off.
        """
        if card is None:
            return False
        if (
            card is getattr(self, "_legacy_cleanup_card", None)
            and not self.legacy_offer_available()
        ):
            return False
        self._solo_card = card
        for other in self._cards_in_column():
            # One card, nothing else: a focused journey (Linux setup, removal
            # offer) hides the rest of Settings — including the Advanced
            # Settings toggle — until the user leaves it.
            other.setVisible(other is card)
        scroll = getattr(self, "_settings_scroll", None)
        if scroll is not None:
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            if scroll.verticalScrollBar() is not None:
                scroll.verticalScrollBar().setValue(0)
                scroll.verticalScrollBar().setEnabled(False)
        if card is getattr(self, "_checker_card", None):
            self._sync_linux_prep_copy()
        self.content_size_changed.emit()
        win = self.window()
        if win is not None and hasattr(win, "_highlight_nav"):
            from .main_window import _PAGE_SETTINGS

            if win._stack.currentIndex() == _PAGE_SETTINGS:
                win._highlight_nav(_PAGE_SETTINGS, win._install_run_active())
        return True

    def clear_card_focus(self) -> None:
        """Give the column back to the ordinary per-card visibility rules."""
        self._solo_card = None
        scroll = getattr(self, "_settings_scroll", None)
        if scroll is not None:
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            if scroll.verticalScrollBar() is not None:
                scroll.verticalScrollBar().setEnabled(True)
        self.refresh_settings()
        self._sync_linux_prep_copy()
        self.content_size_changed.emit()

    def _finish_card_focus(self) -> None:
        """Leave focus mode and tell the window the card's journey is over.

        Does nothing when no card is focused, so the same exit points can be
        used whether the journey started from the card on its own or from the
        ordinary Settings column.
        """
        if self._solo_card is None:
            return
        self.clear_card_focus()
        self.card_focus_finished.emit()

    def focused_card(self):
        return self._solo_card

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
        self._method_card = Card(self._method_card_title_key())
        method_layout = QVBoxLayout()
        method_layout.setSpacing(12)

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
        self._cb_run_as_admin.setStyleSheet("")  # no host style override
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

        # --- Card 2: Update reminders ---
        self._reminders_card = Card("settings_reminders_group")
        rem_layout = QVBoxLayout()
        rem_layout.setSpacing(8)

        # The title describes the opt-in action; explanatory copy below is
        # specific to any device/software pair already being tracked.
        self._cb_reminders = self._add_title_row_switch(
            self._reminders_card, "settings_reminders_enable",
        )
        self._cb_reminders.setStyleSheet("")  # no host style override
        self._cb_reminders.toggled.connect(self._on_reminders_toggled)
        self._reminders_desc = QLabel("")
        self._reminders_desc.setWordWrap(True)
        self._reminders_desc.setProperty("cssClass", "subtitle")
        rem_layout.addWidget(self._reminders_desc)

        # One switch per model once software has been installed on more than
        # one player: releases are tracked per model, but only one unit of a
        # model, so the reminder can be switched off for a single device
        # without silencing the others.
        self._reminders_list = QWidget()
        self._reminders_list_layout = QVBoxLayout(self._reminders_list)
        self._reminders_list_layout.setContentsMargins(0, 0, 0, 0)
        self._reminders_list_layout.setSpacing(4)
        self._reminder_switches: dict[str, QCheckBox] = {}
        self._reminder_rows: list[QWidget] = []
        rem_layout.addWidget(self._reminders_list)

        # Add Player: records a player's installed software without a flash,
        # so Player Update Reminders can track it. Opens inline, not as a dialog.
        self._add_device_button = QPushButton(tr("settings_add_device"))
        self._add_device_button.setCursor(Qt.PointingHandCursor)
        self._add_device_button.clicked.connect(self._toggle_add_device_panel)
        rem_layout.addWidget(self._add_device_button, 0, Qt.AlignLeft)
        self._add_device_panel = self._build_add_device_panel()
        self._add_device_panel.setVisible(False)
        rem_layout.addWidget(self._add_device_panel)

        self._reminders_card.set_layout(rem_layout)
        self._update_reminders_copy()
        self._reminders_card.setVisible(
            self._may_show_card(self._reminders_card) and not is_mediatek_installer()
        )
        layout.addWidget(self._reminders_card)

        # --- Rockbox release filters ---
        # These choose between the online Y1/Y2 Rockbox release builds, so they
        # are offered only when there is a catalogue to filter. The card is
        # built here but added to the layout below Firmware Storage & Cache.
        self._rockbox_card = Card("settings_rockbox_group")
        # The title line is the disclosure switch: the filter rows stay out of
        # the column until it is on, so the card reads as one quiet option.
        self._cb_rockbox_filters = self._add_title_row_switch(
            self._rockbox_card, "settings_rockbox_group"
        )
        self._cb_rockbox_filters.setStyleSheet("")
        self._cb_rockbox_filters.toggled.connect(self._on_rockbox_filters_toggled)

        rock_layout = QVBoxLayout()
        rock_layout.setSpacing(8)
        self._rockbox_options = QWidget()
        options_layout = QVBoxLayout(self._rockbox_options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(8)

        self._rockbox_desc = QLabel(tr("settings_rockbox_desc"))
        self._rockbox_desc.setWordWrap(True)
        self._rockbox_desc.setProperty("cssClass", "subtitle")
        options_layout.addWidget(self._rockbox_desc)

        self._cb_old_rockbox = self._add_switch(options_layout, tr("settings_filter_old_rockbox"))
        self._cb_old_rockbox.setStyleSheet("")  # no host style override
        self._cb_old_rockbox.setToolTip(tr("settings_old_rockbox_warn_body"))
        self._cb_old_rockbox.toggled.connect(self._on_old_rockbox_toggled)

        self._cb_nightly = self._add_switch(options_layout, tr("settings_filter_nightly"))
        self._cb_nightly.setStyleSheet("")  # no host style override
        self._cb_nightly.toggled.connect(self._on_nightly_toggled)

        self._cb_240p = self._add_switch(options_layout, tr("settings_filter_240p"))
        self._cb_240p.setStyleSheet("")  # no host style override
        self._cb_240p.setToolTip(tr("settings_240p_tip"))
        self._cb_240p.toggled.connect(self._on_240p_toggled)

        self._cb_original_pre307 = self._add_switch(
            options_layout, tr("settings_filter_original_pre307")
        )
        self._cb_original_pre307.setStyleSheet("")  # no host style override
        self._cb_original_pre307.setToolTip(tr("settings_original_pre307_warn_body"))
        self._cb_original_pre307.toggled.connect(self._on_original_pre307_toggled)

        self._rockbox_options.setVisible(False)
        rock_layout.addWidget(self._rockbox_options)
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
        self._cb_hide_donations.setStyleSheet("")  # no host style override
        self._cb_hide_donations.toggled.connect(self._on_hide_donations_toggled)

        # Short subtitle: says the card can be turned off for a cleaner look.
        self._lbl_hide_tip = QLabel(tr("settings_hide_donations_tip"), self._donations_card)
        self._lbl_hide_tip.setWordWrap(True)
        self._lbl_hide_tip.setProperty("cssClass", "dimmed")
        don_layout.addWidget(self._lbl_hide_tip)

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
        self._settings_bmc_btn = QPushButton(tr("donate_buy_me_a_coffee"))
        self._settings_bmc_btn.setCursor(Qt.ArrowCursor)
        self._settings_bmc_btn.setStyleSheet("")
        self._settings_bmc_btn.clicked.connect(self._open_settings_bmc)
        self._settings_discord_btn = QPushButton(tr("community_discord"))
        self._settings_discord_btn.setCursor(Qt.ArrowCursor)
        self._settings_discord_btn.setStyleSheet("")
        self._settings_discord_btn.setIcon(self._community_icon("discord"))
        self._settings_discord_btn.setIconSize(QSize(10, 10))
        self._settings_discord_btn.clicked.connect(lambda: self._open_community_link("discord"))
        self._settings_reddit_btn = QPushButton(tr("community_reddit"))
        self._settings_reddit_btn.setCursor(Qt.ArrowCursor)
        self._settings_reddit_btn.setStyleSheet("")
        self._settings_reddit_btn.setIcon(self._community_icon("reddit"))
        self._settings_reddit_btn.setIconSize(QSize(10, 10))
        self._settings_reddit_btn.clicked.connect(lambda: self._open_community_link("reddit"))
        self._dismiss_donations_link = QLabel(self._donations_dismiss_html())
        self._dismiss_donations_link.setWordWrap(True)
        self._dismiss_donations_link.setTextFormat(Qt.TextFormat.RichText)
        self._dismiss_donations_link.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        self._dismiss_donations_link.setOpenExternalLinks(False)
        self._dismiss_donations_link.setCursor(Qt.PointingHandCursor)
        self._dismiss_donations_link.setAutoFillBackground(False)
        self._dismiss_donations_link.setStyleSheet("")
        self._dismiss_donations_link.setVisible(False)
        self._dismiss_donations_link.linkActivated.connect(
            lambda _href: self._on_dismiss_donations_section()
        )
        actions.addWidget(self._settings_coffee_btn, 0, Qt.AlignVCenter)
        actions.addWidget(self._settings_bmc_btn, 0, Qt.AlignVCenter)
        actions.addWidget(self._settings_discord_btn, 0, Qt.AlignVCenter)
        actions.addWidget(self._settings_reddit_btn, 0, Qt.AlignVCenter)
        actions.addStretch(1)
        don_layout.addWidget(self._donations_actions, alignment=Qt.AlignLeft)
        self._sync_donation_methods()
        # This opt-out link only appears after donations are disabled. Keep it
        # on its own row so it cannot widen the entire Settings scroll area
        # when its (usually longer) localized text becomes visible.
        don_layout.addWidget(self._dismiss_donations_link, alignment=Qt.AlignLeft)

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
        # Follow the interface text colour: the icon provider defaults to plain
        # white in dark mode, which does not match the theme's foreground.
        self._clear_cache_btn.setIcon(get_symbol_icon("trash", 14, color=T().fg))
        self._clear_cache_btn.clicked.connect(self._on_clear_firmware_cache)
        cache_btn_row.addWidget(self._clear_cache_btn)

        self._cache_status_lbl = QLabel("")
        cache_btn_row.addWidget(self._cache_status_lbl, 1)
        cache_layout.addLayout(cache_btn_row)

        self._cache_card.set_layout(cache_layout)
        layout.addWidget(self._cache_card)

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
        self._cb_offline_mode.setStyleSheet("")  # no host style override
        self._cb_offline_mode.toggled.connect(self._on_offline_mode_toggled)

        self._offline_mode_desc = QLabel(tr("settings_offline_mode_desc"))
        self._offline_mode_desc.setWordWrap(True)
        self._offline_mode_desc.setProperty("cssClass", "dimmed")
        off_layout.addWidget(self._offline_mode_desc)

        self._offline_mode_card.set_layout(off_layout)
        self._offline_mode_card.setVisible(
            self._may_show_card(self._offline_mode_card) and not is_mediatek_installer()
        )
        layout.addWidget(self._offline_mode_card)

        # --- Card: Notifications ---
        # One master switch over the notice types, plus the route to the
        # background card for notifications while the window is closed.
        self._notifications_card = Card("settings_notifications_group")
        notif_layout = QVBoxLayout()
        notif_layout.setSpacing(8)
        self._cb_notifications = self._add_title_row_switch(
            self._notifications_card,
            "settings_notifications_group",
            tooltip=tr("settings_notifications_desc"),
        )
        self._cb_notifications.setStyleSheet("")  # no host style override
        self._cb_notifications.toggled.connect(self._on_notifications_toggled)

        self._notifications_desc = QLabel(tr("settings_notifications_desc"))
        self._notifications_desc.setWordWrap(True)
        self._notifications_desc.setProperty("cssClass", "dimmed")
        notif_layout.addWidget(self._notifications_desc)

        from ..notice_alerts import (
            CATEGORY_APP, CATEGORY_GENERAL, CATEGORY_PLAYER, CATEGORY_WARNING,
        )

        self._notification_switches = {}
        for category, caption_key in (
            (CATEGORY_GENERAL, "settings_notifications_general"),
            (CATEGORY_WARNING, "settings_notifications_warning"),
            (CATEGORY_PLAYER, "settings_notifications_player"),
            (CATEGORY_APP, "settings_notifications_app"),
        ):
            switch = self._add_switch(notif_layout, tr(caption_key))
            switch.setStyleSheet("")  # no host style override
            switch.toggled.connect(
                lambda checked, c=category: self._on_notification_category_toggled(c, checked)
            )
            self._notification_switches[category] = switch

        self._notifications_unavailable = QLabel(tr("settings_notifications_unavailable"))
        self._notifications_unavailable.setWordWrap(True)
        self._notifications_unavailable.setProperty("cssClass", "dimmed")
        self._notifications_unavailable.setVisible(False)
        notif_layout.addWidget(self._notifications_unavailable)

        # This switch is linked to "Run in Background": either one turns the
        # other on, and the background card remains the place that explains
        # what staying open means.
        self._cb_notifications_background = self._add_switch(
            notif_layout, tr("settings_notifications_background")
        )
        self._cb_notifications_background.setStyleSheet("")
        self._cb_notifications_background.setToolTip(
            run_in_background_desc()
        )
        self._cb_notifications_background.toggled.connect(
            self._on_notifications_background_toggled
        )

        self._notifications_card.set_layout(notif_layout)
        layout.addWidget(self._notifications_card)

        # --- Card: Run in background (opt-in, off by default) ---
        self._background_card = Card("settings_run_in_background_group")
        bg_layout = QVBoxLayout()
        bg_layout.setSpacing(8)
        self._cb_run_in_background = self._add_title_row_switch(
            self._background_card, "settings_run_in_background_group",
            tooltip=run_in_background_desc(),
        )
        self._cb_run_in_background.setStyleSheet("")  # no host style override
        from ..tray import background_mode_supported, run_in_background_enabled
        # macOS builds temporarily ship without the background mode: closing
        # the window quits and there is no menu bar item or login entry. Hide
        # both switches that would promise it rather than showing dead ones.
        supported = background_mode_supported()
        self._background_card.setVisible(supported)
        # The switch lives in a row (caption + switch), so hiding the checkbox
        # alone left its words on the card. The row goes with it.
        self._set_switch_row_visible(self._cb_notifications_background, supported)
        self._cb_run_in_background.setChecked(run_in_background_enabled())
        self._cb_run_in_background.toggled.connect(self._on_run_in_background_toggled)
        self._background_desc = QLabel(run_in_background_desc())
        self._background_desc.setWordWrap(True)
        self._background_desc.setProperty("cssClass", "dimmed")
        bg_layout.addWidget(self._background_desc)
        self._background_card.set_layout(bg_layout)
        layout.addWidget(self._background_card)

        # --- Card: Install Rockbox 360p Themes ---
        self._install_themes_card = Card("settings_install_themes_group")
        # Beta, one word, on the title line: the feature is a preview and the
        # pill says so without spending a sentence on it.
        self._themes_beta_pill = QLabel(tr("settings_themes_beta_pill"))
        self._themes_beta_pill.setStyleSheet(
            "QLabel { color: palette(placeholder-text); border: 1px solid palette(mid);"
            " border-radius: 9px; padding: 1px 8px; font-size: 10px; font-weight: 600; }"
        )
        self._themes_beta_pill.setAccessibleName(tr("settings_themes_beta_pill"))
        self._install_themes_card.put_widget_on_title_row(self._themes_beta_pill)
        # Corner switch: the in-beta auto-install toggle, off by default.
        self._cb_install_themes = self._add_title_row_switch(
            self._install_themes_card,
            "settings_install_themes_group",
            tooltip=tr("settings_install_themes_desc"),
        )
        self._cb_install_themes.setStyleSheet("")  # no host style override
        self._cb_install_themes.setAccessibleName(tr("settings_install_themes_group"))
        self._cb_install_themes.toggled.connect(self._on_themes_auto_install_toggled)

        themes_layout = QVBoxLayout()
        themes_layout.setSpacing(8)

        self._install_themes_desc = QLabel(tr("settings_install_themes_desc"))
        self._install_themes_desc.setWordWrap(True)
        self._install_themes_desc.setProperty("cssClass", "dimmed")
        themes_layout.addWidget(self._install_themes_desc)

        themes_btn_row = QHBoxLayout()
        themes_btn_row.setSpacing(10)
        self._install_themes_btn = QPushButton(tr("settings_install_rockbox_themes_btn"))
        self._install_themes_btn.setIcon(get_symbol_icon("folder", 14, color=T().fg))
        self._install_themes_btn.clicked.connect(self._on_install_rockbox_themes_clicked)
        themes_btn_row.addWidget(self._install_themes_btn)

        self._refresh_themes_btn = QPushButton(tr("settings_refresh_themes_btn"))
        self._refresh_themes_btn.setIcon(get_symbol_icon("refresh", 14, color=T().fg))
        self._refresh_themes_btn.clicked.connect(self.refresh_themes_requested.emit)
        themes_btn_row.addWidget(self._refresh_themes_btn)

        self._install_themes_status_lbl = QLabel("")
        self._install_themes_status_lbl.setWordWrap(True)
        self._install_themes_status_lbl.setProperty("cssClass", "subtitle")
        self._install_themes_status_lbl.setVisible(False)
        themes_btn_row.addWidget(self._install_themes_status_lbl, 1)
        themes_layout.addLayout(themes_btn_row)

        self._install_themes_card.set_layout(themes_layout)
        self._install_themes_card.setVisible(
            self._may_show_card(self._install_themes_card) and not is_mediatek_installer()
        )
        layout.addWidget(self._install_themes_card)

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
        layout.addWidget(self._legacy_cleanup_card)

        # --- Card: App shortcuts (Windows and Linux only) ---
        self._shortcuts_card = Card("settings_shortcuts_title")
        sc_layout = QVBoxLayout()
        sc_layout.setSpacing(8)
        self._shortcut_switches: dict[tuple[str, str], QCheckBox] = {}
        self._shortcut_label_keys: dict[tuple[str, str], tuple[str, str]] = {}
        for sid, name_key in (
            ("updater", "settings_shortcut_updater"),
            ("sp_flash_tool", "settings_shortcut_sp_flash"),
        ):
            for kind, kind_key in (
                ("desktop", "settings_shortcut_desktop"),
                ("menu", "settings_shortcut_menu"),
            ):
                label = f"{tr(name_key)} — {tr(kind_key)}"
                sw = self._add_switch(sc_layout, label)
                sw.toggled.connect(
                    lambda checked, s=sid, k=kind: self._on_shortcut_toggled(s, k, checked)
                )
                self._shortcut_switches[(sid, kind)] = sw
                self._shortcut_label_keys[(sid, kind)] = (name_key, kind_key)
        self._shortcuts_card.set_layout(sc_layout)
        layout.addWidget(self._shortcuts_card)
        self._shortcuts_card.setVisible(
            shortcut_manager.supported() and self._may_show_card(self._shortcuts_card)
        )
        self._sync_shortcut_row_visibility()
        self.refresh_shortcut_state()

        # --- Card: Advanced Settings ---
        # The card's title is the switch's label: the words are printed once,
        # with the switch on the title line, as on the donations card.
        self._advanced_card = Card("settings_advanced_toggle")
        layout.addWidget(self._advanced_card)
        adv_layout = QVBoxLayout()
        adv_layout.setSpacing(8)

        self._cb_advanced_settings = self._add_title_row_switch(
            self._advanced_card, "settings_advanced_toggle",
            tooltip=tr("settings_advanced_desc"),
        )
        self._cb_advanced_settings.setStyleSheet("")
        self._cb_advanced_settings.toggled.connect(self._on_advanced_settings_toggled)

        self._advanced_desc = QLabel(tr("settings_advanced_desc"))
        self._advanced_desc.setWordWrap(True)
        self._advanced_desc.setProperty("cssClass", "dimmed")
        adv_layout.addWidget(self._advanced_desc)

        self._advanced_card.set_layout(adv_layout)

        # --- Card: Settings and media (revealed by Show Advanced Settings) ---
        # Skipping the compatibility question changes every install, so it is a
        # card of its own rather than a row inside the Advanced Settings card,
        # and it only appears while Show Advanced Settings is on.
        from ..keep_data import allow_keep_data_any, set_allow_keep_data_any

        # The title line is the option itself: the switch sits on it, so the
        # same words are not printed twice.
        self._keep_data_card = Card("settings_allow_keep_data_any")
        keep_layout = QVBoxLayout()
        keep_layout.setSpacing(8)
        self._keep_data_desc = QLabel(tr("settings_allow_keep_data_any_desc"))
        self._keep_data_desc.setWordWrap(True)
        self._keep_data_desc.setProperty("cssClass", "dimmed")
        keep_layout.addWidget(self._keep_data_desc)
        self._cb_allow_keep_data_any = self._add_title_row_switch(
            self._keep_data_card,
            "settings_allow_keep_data_any",
            tooltip=tr("settings_allow_keep_data_any_desc"),
        )
        self._cb_allow_keep_data_any.setStyleSheet("")
        self._cb_allow_keep_data_any.setChecked(allow_keep_data_any())
        self._cb_allow_keep_data_any.toggled.connect(
            lambda checked: set_allow_keep_data_any(bool(checked))
        )
        self._keep_data_card.set_layout(keep_layout)
        layout.addWidget(self._keep_data_card)

        # The Advanced Settings card is a detail switch, not a headline: it
        # sits below the everyday cards (below Offline Mode), after the
        # settings it reveals rather than above them.
        layout.addWidget(self._rockbox_card)
        layout.addWidget(self._terminal_card)
        layout.addWidget(self._method_card)
        self._card_layout = layout

        self._sync_advanced_visibility()
        self._update_prep_card_visibility()

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
            self._cb_original_pre307.setChecked(
                flags[device_tracking.FILTER_ORIGINAL_PRE_307]
            )
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

    def _confirm_original_pre307(self) -> bool:
        """Ask before showing Original Software builds older than 3.0.7 for Y1 Type A."""
        return self._rockbox_card.run_confirmation(
            tr("settings_original_pre307_warn_body"),
            tr("dialog_pre_install_continue"),
            tr("flash_btn_cancel"),
        )

    def _on_original_pre307_toggled(self, checked: bool):
        if self._filters_loading:
            return
        if checked and not self._confirm_original_pre307():
            self._apply_filter_flags()
            return
        filters = device_tracking.set_rockbox_release_filter(
            device_tracking.FILTER_ORIGINAL_PRE_307, bool(checked)
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
        self._btn_launch_sp.setVisible(False)
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

        self._arm64_driver_note = QLabel(tr("settings_arm64_driver_note"))
        self._arm64_driver_note.setWordWrap(True)
        self._arm64_driver_note.setProperty("cssClass", "subtitle")
        self._arm64_driver_note.setVisible(bool(paths.IS_WINDOWS and windows_only_method() == METHOD_MTK))
        layout.addWidget(self._arm64_driver_note)

        card.set_layout(layout)
        return card

    def _on_download_drivers(self):
        from ..browser import open_browser

        is_arm64 = bool(paths.IS_WINDOWS and windows_only_method() == METHOD_MTK)
        open_browser(MEDIATEK_DRIVERS_ARM64_URL if is_arm64 else MEDIATEK_DRIVERS_URL)

    # ------------------------------------------------------------------
    # Install method
    # ------------------------------------------------------------------
    def _available_methods(self):
        """Method ids selectable on this platform, in display order."""
        advanced = bool(getattr(self, "_advanced_revealed", False))
        if paths.IS_MAC:
            # No SP Flash Tool build exists for macOS.
            return (METHOD_MTK,)
        if not advanced:
            locked = windows_only_method()
            if locked:
                # Windows is fixed by CPU by default: Intel/AMD gets SP
                # Flash Tool only, ARM64 (native or emulated) gets MTKClient only.
                return (locked,)
        if not paths.IS_WINDOWS and not paths.IS_MAC:
            # On Linux, MTKClient is the default on every architecture. SP Flash
            # Tool is offered as a backup / retry on x86/x86_64 hosts only.
            from .. import linux_sp_flash
            if sys.platform.startswith("linux") and not linux_sp_flash.arch_supported():
                return (METHOD_MTK,)
            return (METHOD_MTK, METHOD_SP)
        return (METHOD_SP, METHOD_MTK)

    def _on_advanced_settings_toggled(self, checked: bool) -> None:
        was_revealed = bool(getattr(self, "_advanced_revealed", False))
        self._advanced_revealed = bool(checked)
        device_tracking.set_advanced_settings_enabled(bool(checked))
        self._sync_advanced_visibility()
        self._sync_linux_prep_copy()
        if not paths.IS_MAC:
            self._reload_method_options()
        if was_revealed and not checked:
            self._revert_advanced_settings()
        self.advanced_settings_changed.emit(self._advanced_revealed)
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _advanced_shown(self) -> bool:
        """True while Show Advanced Settings is on."""
        if hasattr(self, "_cb_advanced_settings"):
            checked = self._cb_advanced_settings.isChecked()
        else:
            checked = self._advanced_revealed
        return bool(checked)

    def _sync_advanced_visibility(self) -> None:
        show_adv = self._advanced_shown()
        if hasattr(self, "_advanced_card"):
            # The toggle lives on this card, so it must come back when a
            # focused journey (solo card) ends and the column is restored.
            self._advanced_card.setVisible(self._may_show_card(self._advanced_card))
        is_win_x86 = bool(paths.IS_WINDOWS and windows_only_method() == METHOD_SP and not show_adv)
        if hasattr(self, "_terminal_card"):
            self._terminal_card.setVisible(show_adv and self._may_show_card(self._terminal_card))
        if hasattr(self, "_keep_data_card"):
            self._keep_data_card.setVisible(show_adv and self._may_show_card(self._keep_data_card))
        if hasattr(self, "_method_card"):
            self._method_card.setVisible(
                show_adv and not paths.IS_MAC and not is_win_x86 and self._may_show_card(self._method_card)
            )
        if hasattr(self, "_method_combo"):
            self._method_combo.setEnabled(not is_win_x86)
            self._method_combo.setVisible(not is_win_x86)
        if hasattr(self, "_rockbox_card"):
            self._update_rockbox_visibility()
        self._sync_shortcut_row_visibility()
        self._update_prep_card_visibility()

    def _sync_shortcut_row_visibility(self) -> None:
        """Show the tool's shortcut rows only behind Show Advanced Settings.

        The app's own desktop and Start Menu shortcuts are everyday choices;
        SP Flash Tool's are for the guided install's advanced path, so they
        stay out of the way until that switch is on. Nothing changes on disk:
        only the tool's two rows are hidden.
        """
        show_tools = bool(getattr(self, "_advanced_revealed", False))
        if hasattr(self, "_cb_advanced_settings"):
            show_tools = self._cb_advanced_settings.isChecked()
        for (shortcut_id, _kind), switch in getattr(self, "_shortcut_switches", {}).items():
            if shortcut_id == shortcut_manager.TOOL_ID:
                self._set_switch_row_visible(switch, show_tools)
        self.refresh_shortcut_state()

    def reveal_advanced_methods(self):
        """Reveal advanced install methods and cards (M or D while running)."""
        self._advanced_revealed = True
        device_tracking.set_advanced_settings_enabled(True)
        if hasattr(self, "_cb_advanced_settings"):
            self._cb_advanced_settings.blockSignals(True)
            self._cb_advanced_settings.setChecked(True)
            self._cb_advanced_settings.blockSignals(False)
        self._sync_advanced_visibility()
        self._sync_linux_prep_copy()
        if not paths.IS_MAC:
            self._reload_method_options()
        self.advanced_settings_changed.emit(self._advanced_revealed)
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def hide_advanced_methods(self):
        """Hide advanced install methods and cards (D toggle off).

        Hiding them also puts every choice they expose back to the platform
        default, so nothing an advanced pass configured keeps applying out of
        sight.
        """
        was_revealed = bool(self._advanced_revealed)
        self._advanced_revealed = False
        device_tracking.set_advanced_settings_enabled(False)
        if hasattr(self, "_cb_advanced_settings"):
            self._cb_advanced_settings.blockSignals(True)
            self._cb_advanced_settings.setChecked(False)
            self._cb_advanced_settings.blockSignals(False)
        self._sync_advanced_visibility()
        self._sync_linux_prep_copy()
        if not paths.IS_MAC:
            self._reload_method_options()
        if was_revealed:
            self._revert_advanced_settings()
        self.advanced_settings_changed.emit(self._advanced_revealed)
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _revert_advanced_settings(self) -> None:
        """Return every advanced-only choice to the platform default.

        With Show Advanced Settings off the app must behave as it does for a
        first-run user: the guided in-app install, the platform's own flash
        method (Windows gets its CPU's engine — MTKClient on ARM64, SP Flash
        Tool on Intel/AMD), no terminal or SP Flash Tool GUI handoff, no
        superuser restart, no release filters, so old builds, nightlies,
        240p and pre-3.0.7 releases stay hidden, and the compatibility question
        asked again before every install.
        """
        # Installation mode: guided in-app install wins over terminal / GUI.
        device_tracking.set_terminal_install_enabled(False)
        device_tracking.set_sp_gui_install_enabled(False)
        if hasattr(self, "_sync_install_mode_switches"):
            self._sync_install_mode_switches()

        # Superuser / administrator restart: off.
        switch = self._run_as_admin_switch()
        if switch is not None and switch.isChecked():
            switch.setChecked(False)  # persists through its own handler

        # "Always offer to keep my settings and media": off again, so a switch
        # hidden with this card can never skip the compatibility question.
        from ..keep_data import set_allow_keep_data_any

        set_allow_keep_data_any(False)
        keep_switch = getattr(self, "_cb_allow_keep_data_any", None)
        if keep_switch is not None and keep_switch.isChecked():
            keep_switch.setChecked(False)  # persists through its own handler

        # Release filters: all off (no old builds, nightlies, 240p, pre-3.0.7).
        for flag in (
            device_tracking.FILTER_OLD_ROCKBOX,
            device_tracking.FILTER_NIGHTLY,
            device_tracking.FILTER_240P,
            device_tracking.FILTER_ORIGINAL_PRE_307,
        ):
            device_tracking.set_rockbox_release_filter(flag, False)
        self._apply_filter_flags()
        self.release_filters_changed.emit()

        # The filter rows go back behind the card's own title switch.
        if hasattr(self, "_cb_rockbox_filters") and self._cb_rockbox_filters.isChecked():
            self._cb_rockbox_filters.setChecked(False)  # hides the rows via its handler

        # Flash method: the platform default, through the same path a user's
        # own choice takes, so the main window persists and applies it.
        from ..flash_service import default_flash_method

        method = default_flash_method()
        # Persisted here as well as by the main window's handler: the default
        # must stick even with nothing listening to the signal (a page under
        # test, or a window still coming up).
        QSettings("innioasis", "updater").setValue("flash_method", method)
        self.set_method(method)
        self.flash_method_changed.emit(method)

    def advanced_methods_revealed(self) -> bool:
        return self._advanced_revealed

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_M and event.modifiers() == Qt.NoModifier:
            self.reveal_advanced_methods()
            event.accept()
            return
        if event.key() == Qt.Key_D and event.modifiers() == Qt.NoModifier:
            win = self.window()
            if win is not None and hasattr(win, "toggle_diagnostics_and_advanced"):
                win.toggle_diagnostics_and_advanced()
            else:
                self.reveal_advanced_methods()
            event.accept()
            return
        if event.key() == Qt.Key_R and event.modifiers() == Qt.NoModifier:
            win = self.window()
            if win is not None and hasattr(win, "reset_all_configuration_and_caches"):
                win.reset_all_configuration_and_caches()
                event.accept()
                return
        super().keyPressEvent(event)

    def _reload_method_options(self):
        labels = {
            METHOD_SP: tr("flash_method_sp"),
            METHOD_MTK: tr("flash_method_mtk"),
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
        self._update_prep_card_visibility()

    def _persisted_method(self) -> str:
        return normalise_method(QSettings("innioasis", "updater").value("flash_method", ""))

    def current_method(self) -> str:
        return self._method_combo.currentData() or default_flash_method()

    def set_method(self, method):
        """Reflect a method chosen elsewhere (or persisted) in the selector."""
        method = normalise_method(method)
        self._reload_method_options()
        idx = self._method_combo.findData(method)
        if idx >= 0 and idx != self._method_combo.currentIndex():
            self._method_combo.blockSignals(True)
            self._method_combo.setCurrentIndex(idx)
            self._method_combo.blockSignals(False)
        self._update_method_note()
        self._update_sp_auth_visibility()
        self._update_prep_card_visibility()
        self._method_before = self.current_method()

    def set_method_enabled(self, enabled: bool):
        """Lock the selector while a run is in progress."""
        self._method_combo.setEnabled(bool(enabled))

    def _on_method_changed(self):
        method = self.current_method()
        previous = getattr(self, "_method_before", "") or self._persisted_method()
        self._method_before = method
        self._update_method_note()
        self._update_sp_auth_visibility()
        self._update_prep_card_visibility()
        self.flash_method_changed.emit(method)

    # ------------------------------------------------------------------
    # SP Flash Tool authentication file
    # ------------------------------------------------------------------
    def _on_rockbox_filters_toggled(self, checked: bool) -> None:
        """Reveal the release filter rows under the card's title switch."""
        self._rockbox_options.setVisible(bool(checked))
        win = self.window()
        if win is not None and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

    def _update_rockbox_visibility(self):
        """Offer the Rockbox release filters only behind Show Advanced Settings.

        They filter the online Y1/Y2 Rockbox release list, so with no online
        catalogue — offline mode, or a build of the generic MediaTek Installer,
        which is offline by definition — there is nothing for them to act on.
        They are also an advanced choice: turning Show Advanced Settings off
        hides them (and resets them) along with the rest of the advanced cards.
        """
        self._rockbox_card.setVisible(
            self._advanced_shown()
            and not is_offline_mode()
            and self._may_show_card(self._rockbox_card)
        )

    def refresh_shortcut_state(self):
        """Set each checkbox from the real shortcut files on disk.

        The tool's rows are hidden until Show Advanced Settings is on, so their
        files are only read once those rows are on screen: reading unrecognised
        shortcut targets starts a shell, which startup should not pay for.
        """
        if not hasattr(self, "_shortcut_switches"):
            return
        show_tools = bool(getattr(self, "_advanced_revealed", False))
        if hasattr(self, "_cb_advanced_settings"):
            show_tools = self._cb_advanced_settings.isChecked()
        skip = () if show_tools else (shortcut_manager.TOOL_ID,)
        state = shortcut_manager.get_state(skip=skip)
        for (sid, kind), sw in self._shortcut_switches.items():
            if sid in skip:
                continue
            sw.blockSignals(True)
            sw.setChecked(state.get(sid, {}).get(kind, False))
            sw.blockSignals(False)

    def _on_shortcut_toggled(self, shortcut_id: str, kind: str, checked: bool):
        shortcut_manager.set_shortcut(shortcut_id, kind, checked)
        self.refresh_shortcut_state()

    def _retranslate_shortcut_switches(self) -> None:
        """Relabel the shortcut rows in place, without touching their layout."""
        if not hasattr(self, "_shortcuts_card"):
            return
        self._shortcuts_card.retranslate()
        for key, switch in self._shortcut_switches.items():
            labels = self._shortcut_label_keys.get(key)
            if labels is None:
                continue
            name_key, kind_key = labels
            self._set_switch_caption(switch, f"{tr(name_key)} — {tr(kind_key)}")

    def legacy_offer_available(self) -> bool:
        """Whether the pre-3.0 removal offer may be shown at all.

        One rule for the card and for anything that wants to put the reader in
        front of it: not on Windows, not in the generic MediaTek Installer
        build, and only for a real pre-3.0 install. The launch check asks this
        before it opens Settings, so a host without the offer never lands in a
        focused card it cannot see.
        """
        if is_mediatek_installer() or paths.IS_WINDOWS:
            return False
        try:
            from ..legacy_cleanup import detect_legacy_installations

            return bool(detect_legacy_installations().get("has_legacy"))
        except Exception:
            return False

    def _update_legacy_cleanup_visibility(self):
        """Show the removal card only while a pre-3.0 install is on disk.

        The card is an offer to uninstall the old pre-3.0 app (its .app, its
        Application Support folder, or the Linux leftovers). It is not a
        general cleaner: stray Android platform tools alone do not raise it,
        and the card disappears again once the old app is gone.
        """
        visible = self.legacy_offer_available()
        self._legacy_cleanup_card.setVisible(
            visible and self._may_show_card(self._legacy_cleanup_card)
        )
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
        from ..downloads import default_picker_dir

        current = device_tracking.sp_auth_file()
        start_dir = str(Path(current).parent) if current else default_picker_dir()
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
        if method == METHOD_SP:
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
        """Name only installed releases that the reminder checker tracks."""
        parts = []
        for model in DEVICE_MODELS:
            record = device_tracking.get_device_install(model)
            if not record:
                continue
            label = device_tracking.tracked_model_label(record)
            software = str(record.get("software_name") or "").strip()
            parts.append(
                tr("settings_reminders_model_fmt").format(software=software, model=label)
                if software else label
            )
        return " · ".join(parts)

    def _tracked_devices_summary(self) -> str:
        models = [
            model for model in DEVICE_MODELS
            if device_tracking.get_device_install(model)
        ]
        return ", ".join(device_label_for_model(model) for model in models)

    @staticmethod
    def _is_original_software(software: str) -> bool:
        """The player's own firmware, which the copy calls "the software"."""
        return "original" in (software or "").strip().lower()

    def _tracked_installs(self) -> list[tuple[str, dict]]:
        """``(key, install record)`` for every model and model+type permutation still tracked."""
        installs = device_tracking.get_all_device_installs()
        return list(installs.items())

    def _rebuild_reminder_switches(self, tracked: list[tuple[str, dict]]) -> None:
        """One reminder switch per tracked model and model+type permutation."""
        if not hasattr(self, "_reminders_list_layout"):
            return
        for row in getattr(self, "_reminder_rows", []):
            # Keep the row parented while deleteLater waits for the event loop.
            # setParent(None) promotes a visible child to a top-level window,
            # which flashed as a small window over the page on every rebuild.
            self._reminders_list_layout.removeWidget(row)
            row.hide()
            row.deleteLater()
        for switch in self._reminder_switches.values():
            self._switch_rows.pop(switch, None)
            # The switch is a child of its row; deleting the row takes it too.
            switch.hide()
        self._reminder_rows = []
        self._reminder_switches = {}
        show = len(tracked) >= 1
        self._reminders_list.setVisible(show)
        if not show:
            return
        for key, record in tracked:
            caption = device_tracking.tracked_permutation_label(record)
            row = QWidget()
            box = QHBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(12)
            caption_label = QLabel(caption)
            caption_label.setWordWrap(True)
            switch = QCheckBox()
            switch.setCursor(Qt.ArrowCursor)
            switch.setStyleSheet("")
            switch.setChecked(device_tracking.is_device_reminder_enabled(key))
            switch.toggled.connect(
                lambda checked, k=key, rec=record: self._on_model_reminder_toggled(k, rec, checked)
            )
            box.addWidget(caption_label, 1)
            box.addWidget(switch, 0, Qt.AlignRight | Qt.AlignVCenter)
            self._reminders_list_layout.addWidget(row)
            self._reminder_rows.append(row)
            self._reminder_switches[key] = switch
            self._switch_rows[switch] = (row, caption_label)
            switch.text = caption

    def _on_model_reminder_toggled(self, key: str, record: dict, checked: bool) -> None:
        # Unchecking a row removes that model/type permutation tracked install.
        if checked:
            device_tracking.set_device_reminder_enabled(key, True)
            self.refresh_settings()
            return
        model = record.get("model", key.split("_")[0])
        hw_type = record.get("hardware_type", "")
        device_tracking.clear_device_install(model, hardware_type=hw_type)
        self.refresh_settings()

    def _update_reminders_copy(self) -> None:
        """Describe reminder coverage from the installs actually tracked."""
        if not hasattr(self, "_reminders_desc"):
            return
        tracked = self._tracked_installs()
        if not tracked:
            text = tr("settings_reminders_desc")
        elif len(tracked) == 1:
            _key, record = tracked[0]
            label = device_tracking.tracked_model_label(record)
            software = str(record.get("software_name") or "").strip()
            if not software or self._is_original_software(software):
                text = tr("settings_reminders_tracked_original").format(model=label)
            else:
                text = tr("settings_reminders_tracked_named").format(
                    software=software, model=label
                )
        else:
            text = tr("settings_reminders_tracked_multi")
        self._reminders_desc.setText(text)
        self._rebuild_reminder_switches(tracked)

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
    def _build_add_device_panel(self) -> QWidget:
        """Inline form: Model, Type (only variants), Software, Version, Save/Cancel."""
        panel = QWidget()
        form = QVBoxLayout(panel)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(6)

        self._add_device_model = QComboBox()
        self._add_device_type = QComboBox()
        self._add_device_type.setVisible(False)
        self._add_device_software = QComboBox()
        self._add_device_version = QComboBox()
        self._add_device_status = QLabel("")
        self._add_device_status.setWordWrap(True)
        self._add_device_status.setProperty("cssClass", "dimmed")

        for caption_key, combo in (
            ("settings_add_device_model", self._add_device_model),
            ("settings_add_device_type", self._add_device_type),
            ("settings_add_device_software", self._add_device_software),
            ("settings_add_device_version", self._add_device_version),
        ):
            label = QLabel(tr(caption_key))
            form.addWidget(label)
            form.addWidget(combo)
            if combo is self._add_device_type:
                self._add_device_type_label = label
                label.setVisible(False)

        form.addWidget(self._add_device_status)

        buttons = QHBoxLayout()
        self._add_device_save = QPushButton(tr("settings_add_device_save"))
        self._add_device_save.setCursor(Qt.PointingHandCursor)
        self._add_device_save.clicked.connect(self._save_add_device)
        self._add_device_cancel = QPushButton(tr("settings_add_device_cancel"))
        self._add_device_cancel.setCursor(Qt.PointingHandCursor)
        self._add_device_cancel.clicked.connect(self._hide_add_device_panel)
        buttons.addWidget(self._add_device_save)
        buttons.addWidget(self._add_device_cancel)
        buttons.addStretch(1)
        form.addLayout(buttons)

        self._add_device_workers = []
        self.destroyed.connect(lambda *_, ws=self._add_device_workers: _stop_add_device_workers(ws))
        self._add_device_model.currentIndexChanged.connect(self._on_add_device_model_changed)
        self._add_device_type.currentIndexChanged.connect(self._on_add_device_software_changed)
        self._add_device_software.currentIndexChanged.connect(self._on_add_device_software_changed)
        return panel

    def _toggle_add_device_panel(self):
        if self._add_device_panel.isVisible():
            self._hide_add_device_panel()
            return
        self._add_device_model.blockSignals(True)
        self._add_device_model.clear()
        self._add_device_model.addItems(catalog.available_models() or list(DEVICE_MODELS))
        self._add_device_model.blockSignals(False)
        self._on_add_device_model_changed()
        self._add_device_panel.setVisible(True)

    def _hide_add_device_panel(self):
        self._add_device_panel.setVisible(False)
        self._add_device_status.setText("")

    def _on_add_device_model_changed(self, *_):
        model = self._add_device_model.currentText().strip()
        types = hardware_types_for_model(model)
        self._add_device_type.blockSignals(True)
        self._add_device_type.clear()
        for hw_type in types:
            self._add_device_type.addItem(
                device_tracking.hardware_type_label(hw_type), hw_type
            )
        self._add_device_type.blockSignals(False)
        show_type = len(types) > 1
        self._add_device_type_label.setVisible(show_type)
        self._add_device_type.setVisible(show_type)
        self._add_device_software.blockSignals(True)
        self._add_device_software.clear()
        self._add_device_software.addItems(catalog.software_names_for_model(model))
        self._add_device_software.blockSignals(False)
        self._on_add_device_software_changed()

    def _add_device_package(self):
        model = self._add_device_model.currentText().strip()
        software = self._add_device_software.currentText().strip()
        packages = catalog.packages_for_model_software(model, software)
        return packages[0] if packages else None

    def _on_add_device_software_changed(self, *_):
        """Fill Version from the live release list, fetched off the UI thread."""
        package = self._add_device_package()
        self._add_device_version.clear()
        if package is None:
            return
        model = self._add_device_model.currentText().strip()
        selected_type = (
            self._add_device_type.currentData()
            if len(hardware_types_for_model(model)) > 1 else None
        )
        self._add_device_status.setText(tr("settings_add_device_loading"))
        self._add_device_version.addItem(tr("settings_add_device_loading"), "")
        request_id = object()
        self._add_device_request = request_id
        worker = _AddDeviceReleasesWorker(package, model, selected_type)
        worker.loaded.connect(
            lambda releases, rid=request_id: self._on_add_device_releases(rid, releases)
        )
        # Keep every running worker referenced until it finishes, so replacing
        # one with a newer selection never destroys a live QThread.
        for old in list(self._add_device_workers):
            old.requestInterruption()
            old.wait(100)
        self._add_device_workers.append(worker)
        worker.finished.connect(lambda w=worker: self._add_device_workers.remove(w) if w in self._add_device_workers else None)
        worker.start()

    def _on_add_device_releases(self, request_id, releases):
        if request_id is not getattr(self, "_add_device_request", None):
            return  # a newer selection has replaced this request
        self._add_device_version.clear()
        self._add_device_status.setText("")
        for rel in releases:
            label = catalog.parse_clean_release_tag_label(rel, version_only=True)
            self._add_device_version.addItem(label, rel)
        if not releases:
            self._add_device_status.setText(tr("settings_add_device_no_versions"))

    def _save_add_device(self):
        package = self._add_device_package()
        index = self._add_device_version.currentIndex()
        rel = self._add_device_version.itemData(index) if index >= 0 else None
        if package is None or not rel:
            self._add_device_status.setText(tr("settings_add_device_pick_version"))
            return
        model = self._add_device_model.currentText().strip()
        hw_type = (
            self._add_device_type.currentData()
            if len(hardware_types_for_model(model)) > 1 else ""
        )
        tag_name = rel.get("tag_name", "")
        if not tag_name:
            self._add_device_status.setText(tr("settings_add_device_pick_version"))
            return
        device_tracking.record_device_install(
            model=model,
            software_name=package.name,
            tag_name=tag_name,
            release_label=catalog.parse_clean_release_tag_label(rel, version_only=True),
            package_slug=package.slug,
            published_at=rel.get("published_at", "") or "",
            settings=QSettings("innioasis", "updater"),
            hardware_type=hw_type,
        )
        # A device added here is logged for update reminders, so notices about
        # its model, revision and firmware may now be shown on Select Software.
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().refresh_context()
        except Exception:
            logger.debug("Could not refresh the notice context after adding a device", exc_info=True)
        self._hide_add_device_panel()
        self.refresh_settings()

    def _sync_add_device_button_visibility(self):
        """Keep Add Player button available in Player Update Reminders."""
        if hasattr(self, "_add_device_button"):
            self._add_device_button.setVisible(True)

    def mark_firmware_installed(self):
        """Called after a firmware install completes."""
        self._firmware_installed_this_session = True
        self.refresh_settings()
        self._firmware_installed_this_session = True
        self._sync_add_device_button_visibility()

    def refresh_settings(self):
        """Reload and update all controls to reflect current settings."""
        self.refresh_shortcut_state()
        persisted_adv = QSettings("innioasis", "updater").value("advanced_settings", False, type=bool)
        self._advanced_revealed = bool(persisted_adv)
        if hasattr(self, "_cb_advanced_settings"):
            self._cb_advanced_settings.blockSignals(True)
            self._cb_advanced_settings.setChecked(self._advanced_revealed)
            self._cb_advanced_settings.blockSignals(False)
        device_tracking.set_advanced_settings_enabled(self._advanced_revealed)
        if not self._advanced_revealed:
            # Advanced is off: anything an earlier advanced session left
            # behind goes back to the app default, not just on the toggle-off.
            self._revert_advanced_settings()
        self._sync_advanced_visibility()
        self._sync_linux_prep_copy()
        self._update_prep_card_visibility()
        self._reminders_card.setVisible(
            self._may_show_card(self._reminders_card) and not is_mediatek_installer()
        )
        self._offline_mode_card.setVisible(
            self._may_show_card(self._offline_mode_card) and not is_mediatek_installer()
        )
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
        self._update_reminders_copy()

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

        # In-beta themes auto-install toggle: reflects the persisted switch.
        from ..theme_sync import themes_auto_install_enabled

        if hasattr(self, "_cb_install_themes"):
            self._cb_install_themes.blockSignals(True)
            self._cb_install_themes.setChecked(themes_auto_install_enabled())
            self._cb_install_themes.blockSignals(False)

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

        # Notification switches: master, then each notice category.
        self._sync_notification_controls()

        # Offline mode toggle
        self._cb_offline_mode.blockSignals(True)
        self._cb_offline_mode.setChecked(is_offline_mode())
        self._cb_offline_mode.blockSignals(False)

        # SP Flash Tool authentication file
        self._refresh_sp_auth_value()
        self._update_sp_auth_visibility()
        self.content_size_changed.emit()

    def apply_brand_mode(self, mediatek_installer: bool):
        """Show or hide the pieces that only make sense for one brand."""
        self._offline_mode_card.setVisible(
            self._may_show_card(self._offline_mode_card) and not mediatek_installer
        )
        self._reminders_card.setVisible(
            self._may_show_card(self._reminders_card) and not mediatek_installer
        )
        if hasattr(self, "_install_themes_card"):
            self._install_themes_card.setVisible(
                self._may_show_card(self._install_themes_card) and not mediatek_installer
            )
        self._update_rockbox_visibility()
        self._update_sp_auth_visibility()

    # ------------------------------------------------------------------
    # Misc actions
    # ------------------------------------------------------------------
    def set_portable_run(self, enabled: bool) -> None:
        """Record that this run installs bundled firmware and stores nothing."""
        self._portable_run = bool(enabled)

    def _redirect_keep_alive_preference(self, *boxes) -> bool:
        """Send a portable run's keep-the-app switch to the install screen.

        Turning on background running or notifications promises the app will
        still be here tomorrow. A portable run with nothing installed cannot
        keep that promise (it removes its own files when it quits), so the
        switch returns to off and the app opens the screen that installs it.
        With a real install the switches behave as usual.
        """
        if not getattr(self, "_portable_run", False):
            return False
        try:
            if paths.installed_app_present():
                return False
        except Exception:
            pass
        for box in boxes:
            if box is None:
                continue
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)
        self._sync_notification_controls()
        self.install_app_requested.emit()
        return True

    def _on_offline_mode_toggled(self, checked: bool):
        from PySide6.QtCore import QSettings
        QSettings("Innioasis", "UpdaterCE").setValue("offline_mode", checked)
        from .. import config
        # Only the offline switch moves here: the MediaTek Installer identity is
        # fixed at build time and never toggled by a settings checkbox.
        config.IS_OFFLINE_MODE = checked
        self.offline_mode_changed.emit(checked)

    def _on_run_in_background_toggled(self, checked: bool):
        if checked and self._redirect_keep_alive_preference(
            getattr(self, "_cb_run_in_background", None)
        ):
            return
        from ..tray import RUN_IN_BACKGROUND_KEY
        from ..login_item import set_login_item
        QSettings("innioasis", "updater").setValue(RUN_IN_BACKGROUND_KEY, bool(checked))
        set_login_item(bool(checked))
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(not checked)
        # Keep the notifications card honest: the two switches describe the
        # same background process, so neither can be on by itself.
        box = getattr(self, "_cb_notifications_background", None)
        if box is not None:
            box.blockSignals(True)
            box.setChecked(bool(checked))
            box.blockSignals(False)
        if checked:
            from ..notice_alerts import set_notifications_enabled

            set_notifications_enabled(True)
            self._sync_notification_controls()

    def _on_notifications_background_toggled(self, checked: bool):
        if checked and self._redirect_keep_alive_preference(
            getattr(self, "_cb_notifications_background", None)
        ):
            return
        """Link background notifications to the background-running opt-in."""
        from ..tray import RUN_IN_BACKGROUND_KEY
        from ..login_item import set_login_item

        QSettings("innioasis", "updater").setValue(
            RUN_IN_BACKGROUND_KEY, bool(checked)
        )
        set_login_item(bool(checked))
        app = QApplication.instance()
        if app is not None:
            app.setQuitOnLastWindowClosed(not checked)
        if checked:
            from ..notice_alerts import set_notifications_enabled

            set_notifications_enabled(True)
        box = getattr(self, "_cb_run_in_background", None)
        if box is not None:
            box.blockSignals(True)
            box.setChecked(bool(checked))
            box.blockSignals(False)
        self._sync_notification_controls()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def _on_notifications_toggled(self, checked: bool):
        if checked and self._redirect_keep_alive_preference(
            getattr(self, "_cb_notifications", None)
        ):
            return
        from ..notice_alerts import set_notifications_enabled

        set_notifications_enabled(bool(checked))
        if not checked:
            # Turning notifications off also ends the background process that
            # exists to deliver them.
            box = getattr(self, "_cb_notifications_background", None)
            if box is not None and box.isChecked():
                box.setChecked(False)
        self._sync_notification_controls()

    def _on_notification_category_toggled(self, category: str, checked: bool):
        from ..notice_alerts import set_category_enabled

        set_category_enabled(category, bool(checked))

    def _sync_notification_controls(self) -> None:
        """Mirror the stored switches, and follow the host's permission state."""
        from ..notice_alerts import (
            category_preference,
            notifications_enabled,
            system_notifications_available,
        )

        supported = system_notifications_available()
        master_on = supported and bool(notifications_enabled())
        self._cb_notifications.blockSignals(True)
        self._cb_notifications.setChecked(master_on)
        self._cb_notifications.blockSignals(False)
        self._cb_notifications.setEnabled(supported)
        for category, switch in self._notification_switches.items():
            switch.blockSignals(True)
            switch.setChecked(bool(category_preference(category)))
            switch.blockSignals(False)
            switch.setEnabled(master_on)
        self._notifications_unavailable.setVisible(not supported)
        background = getattr(self, "_cb_notifications_background", None)
        if background is not None:
            from ..tray import background_mode_supported, run_in_background_enabled

            background.blockSignals(True)
            background.setChecked(bool(run_in_background_enabled()))
            background.blockSignals(False)
            background.setEnabled(supported)
            # A host without the background mode (macOS for now) never offers
            # this switch, even though it can show notifications while the
            # window is open. Resyncing used to re-show the row the build hid.
            self._set_switch_row_visible(
                background, supported and background_mode_supported()
            )

    def _focus_background_card(self) -> None:
        """Take the reader from Notifications to the card that keeps the app open."""
        card = getattr(self, "_background_card", None)
        if card is None:
            return
        if self._solo_card is not None:
            self.clear_card_focus()
        scroll = getattr(self, "_settings_scroll", None)
        if scroll is not None:
            scroll.ensureWidgetVisible(card, 0, 24)
        card.setFocus(Qt.FocusReason.OtherFocusReason)

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
        from .dark import link_html
        return link_html(tr("settings_donations_dismiss"), bold=False).replace(
            'href="#"', 'href="dismiss"', 1
        )

    def _open_settings_coffee(self) -> None:
        from ..browser import open_browser
        from ..config import DONATION_LINKS

        route = fx.donation_route()
        if route == "boosty":
            open_browser(DONATION_LINKS["boosty"])
        else:
            open_browser(DONATION_LINKS["kofi"])

    def _open_settings_bmc(self) -> None:
        from ..browser import open_browser
        from ..config import DONATION_LINKS

        open_browser(DONATION_LINKS["bmc"])

    def _sync_donation_methods(self) -> None:
        """Show Boosty alone for the Russian market."""
        route = fx.donation_route()
        if hasattr(self, "_settings_coffee_btn"):
            key = {"boosty": "donate_boosty"}.get(route, "donate_coffee_btn")
            self._settings_coffee_btn.setText(tr(key))
        if hasattr(self, "_settings_bmc_btn"):
            self._settings_bmc_btn.setText(tr("donate_buy_me_a_coffee"))
            # Set both ways: a language switch back to English has to restore
            # the second coffee page, not leave the row one button short.
            self._settings_bmc_btn.setVisible(not route)

    def _community_icon(self, name: str):
        """Render a bundled community SVG in the current interface foreground color."""
        from PySide6.QtCore import QByteArray, QRectF
        from PySide6.QtGui import QIcon, QPainter, QPixmap
        from PySide6.QtSvg import QSvgRenderer
        import re

        icon_size = 10
        path = paths.RESOURCES_DIR / f"{name}.svg"
        color = T().fg
        if not path.is_file():
            return get_symbol_icon("support", icon_size, color=color)
        try:
            source = re.sub(r"fill=([\"'])[^\"']+\1", f'fill="{color}"', path.read_text(encoding="utf-8"))
            source = source.replace("currentColor", color)
            renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
            if renderer.isValid():
                pixmap = QPixmap(icon_size * 2, icon_size * 2)
                pixmap.setDevicePixelRatio(2.0)
                pixmap.fill(Qt.transparent)
                view_box = renderer.viewBoxF()
                if view_box.width() <= 0 or view_box.height() <= 0:
                    raise ValueError(f"Invalid {name} icon viewBox")
                scale = min(icon_size / view_box.width(), icon_size / view_box.height())
                width = view_box.width() * scale
                height = view_box.height() * scale
                target = QRectF((icon_size - width) / 2, (icon_size - height) / 2, width, height)
                painter = QPainter(pixmap)
                renderer.render(painter, target)
                painter.end()
                return QIcon(pixmap)
        except Exception:
            logger.debug("Could not load %s community icon", name, exc_info=True)
        return get_symbol_icon("support", icon_size, color=color)

    def _open_community_link(self, community: str) -> None:
        from ..browser import open_browser
        from ..config import DONATION_LINKS

        url = DONATION_LINKS.get(community)
        if url:
            open_browser(url)

    def _sync_donation_opt_out(self) -> None:
        """The dismiss link appears only when Community Credits/Donations has been disabled."""
        disabled = (
            not self._cb_hide_donations.isChecked()
            or device_tracking.is_donation_ui_disabled()
            or device_tracking.is_donation_install_prompt_disabled()
        )
        for name in (
            "_settings_coffee_btn",
            "_settings_bmc_btn",
            "_settings_discord_btn",
            "_settings_reddit_btn",
        ):
            button = getattr(self, name, None)
            if button is not None:
                button.setVisible(True)
        if hasattr(self, "_dismiss_donations_link"):
            self._dismiss_donations_link.setVisible(disabled)
        self._sync_donation_methods()

    def _apply_donations_section_visibility(self) -> None:
        """The dismiss link removes this card. The donation switch does not."""
        if layout_gap_closing(self._donations_card):
            return
        dismissed = device_tracking.is_settings_donations_section_dismissed()
        if not dismissed:
            self._donations_card.setMaximumHeight((1 << 24) - 1)
            self._donations_card.setMinimumHeight(0)
            self._donations_card.clearMask()
        self._donations_card.setVisible(
            self._may_show_card(self._donations_card) and not dismissed
        )

    def _on_dismiss_donations_section(self) -> None:
        """Permanently disable credits/donations functionality and hide the settings card."""
        if layout_gap_closing(self._donations_card):
            return
        device_tracking.set_settings_donations_section_dismissed(True)
        device_tracking.set_donation_ui_disabled(True)
        device_tracking.set_donation_install_prompt_disabled(True)
        self.donation_visibility_changed.emit(True)
        close_layout_gap(self._donations_card)
        win = self.window()
        if win and hasattr(win, "_adjust_window_geometry"):
            win._adjust_window_geometry()

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
        device_tracking.sync_sp_donation_marker(disabled)
        self._sync_donation_opt_out()
        self.donation_visibility_changed.emit(disabled)

    def _on_skip_install_donations_toggled(self, checked: bool):
        """Legacy alias handler: here ``checked`` means "prompts disabled"."""
        device_tracking.set_donation_install_prompt_disabled(checked)
        device_tracking.set_donation_ui_disabled(checked)
        self._cb_hide_donations.setChecked(not checked)

    def _on_clear_firmware_cache(self):
        from ..downloads import clear_app_storage
        count, freed = clear_app_storage()
        if freed >= 1024 * 1024:
            freed_str = f"{freed / (1024 * 1024):.1f} MB"
        elif freed >= 1024:
            freed_str = f"{freed / 1024:.1f} KB"
        else:
            freed_str = f"{freed} B"
        msg = tr("settings_cache_cleared_msg").format(count=count, size=freed_str)
        self._cache_status_lbl.setText(msg)
        # The remote-data caches just went with the firmware, so the window
        # fetches them again instead of leaving the lists empty until the next
        # refresh button press.
        window = self.window()
        refresh = getattr(window, "_refresh_online_data_after_storage_clear", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                logger.debug("Could not refetch online data after clearing storage", exc_info=True)
        self.content_size_changed.emit()

    def set_themes_refreshing(self, refreshing: bool, status_text: str = "") -> None:
        """Swap the themes button for a refresh notice while a background theme pack update runs."""
        if not hasattr(self, "_install_themes_btn"):
            return
        self._install_themes_btn.setVisible(not refreshing)
        if hasattr(self, "_refresh_themes_btn"):
            self._refresh_themes_btn.setVisible(not refreshing)
        self._install_themes_status_lbl.setText(status_text if refreshing else "")
        self._install_themes_status_lbl.setVisible(refreshing)

    def set_themes_status(self, status_text: str) -> None:
        """Show a one-line result under the themes buttons."""
        if not hasattr(self, "_install_themes_status_lbl"):
            return
        self._install_themes_status_lbl.setText(status_text or "")
        self._install_themes_status_lbl.setVisible(bool(status_text))

    def _on_install_rockbox_themes_clicked(self):
        self.install_rockbox_themes_requested.emit()

    def _on_themes_auto_install_toggled(self, checked: bool) -> None:
        """Persist the in-beta toggle and tell the window to start/stop the sync."""
        from ..theme_sync import set_themes_auto_install_enabled

        set_themes_auto_install_enabled(bool(checked))
        self.themes_auto_install_changed.emit(bool(checked))

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
        self._show_checker_status(
            tr("linux_prep_starting_friendly" if is_mediatek_installer() else "linux_prep_starting")
        )
        worker = linux_sp_flash.LinuxStagingWorker(self)
        self._sp_prep_worker = worker
        worker.progress.connect(self._on_sp_prep_progress)
        worker.finished.connect(self._on_sp_prep_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def start_sp_flash_prep(self) -> bool:
        """Prepare SP Flash Tool on this card, for the Linux entry points.

        The Linux preparation used to be a window of its own opened from the
        navigation rail and on first run. Both now land here, so there is one
        place that prepares the engine and says how it went.

        Returns whether this card is the one to show; ``False`` where the
        preparation does not apply, so the caller does not focus a card that
        stays hidden.
        """
        if paths.IS_MAC or is_mediatek_installer() or not hasattr(self, "_checker_status_lbl"):
            # macOS has no SP Flash Tool, and the Windows card is the driver
            # card rather than the preparation one.
            return False
        self._checker_card.setVisible(self._may_show_card(self._checker_card))
        self._reveal_checker_card()
        if getattr(self, "_sp_prep_worker", None) is None:
            self._on_run_checker()
        return True

    def _on_sp_prep_progress(self, percent: int, message: str):
        line = f"{int(percent)}%"
        if message:
            line = f"{line}, {message}"
        self._show_checker_status(line)

    def _on_sp_prep_finished(self, ok: bool, message: str, report: dict):
        self._sp_prep_worker = None
        self._btn_run_checker.setEnabled(True)
        if not ok:
            self._show_checker_status(
                tr("linux_setup_issue_fmt").format(msg=message), warn=True
            )
            return
        report = report or {}
        self._render_sp_report(report)
        issues = [
            item for item in report.get("items", [])
            if item.get("status") != "ok" and item.get("key") != "connected_device"
        ]
        if report.get("overall_ready") or not issues:
            self.linux_prep_completed.emit()

    def _render_sp_report(self, report: dict, *, ask_again: bool = True):
        """Say the verdict in one line, and offer the fix on the card."""
        # Device presence is informational: the user may prepare the computer
        # before connecting their player, and absence never blocks flashing.
        items = [
            it for it in report.get("items", [])
            if it.get("status") != "ok" and it.get("key") != "connected_device"
        ]
        blocking = [it for it in items if not it.get("can_fix")]
        if blocking:
            first = blocking[0]
            self._show_checker_status(
                first.get("detail") or first.get("badge") or tr("linux_prep_blocked"),
                warn=True,
            )
            return
        # Tool names only appear once Advanced Settings is on.
        advanced = bool(
            getattr(self, "_advanced_revealed", False)
            or (hasattr(self, "_cb_advanced_settings") and self._cb_advanced_settings.isChecked())
            or device_tracking.advanced_settings_enabled()
        )
        ready_key = "linux_prep_ready" if advanced else "linux_prep_ready_friendly"
        if not items:
            self._show_checker_status(tr(ready_key))
            return
        if not report.get("can_auto_fix", True):
            self._show_checker_status(tr(ready_key))
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
        win = self.window()
        setup_flow = bool(
            self._solo_card is self._checker_card
            or (win is not None and getattr(win, "_linux_setup_flow_active", False))
        )
        body_key = "linux_prep_fix_body_wizard" if setup_flow else "linux_prep_fix_body"
        agreed = self._checker_card.run_confirmation(
            tr(body_key),
            tr("linux_prep_fix_btn"),
            tr("flash_btn_cancel"),
        )
        if not agreed:
            if setup_flow:
                if win is not None:
                    win._force_quit = True
                    win.close()
                QApplication.quit()
                return
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
            self._show_checker_status(
                tr("linux_prep_fixed_friendly" if is_mediatek_installer() else "linux_prep_fixed")
            )
            if setup_flow:
                if win is not None and hasattr(win, "settings"):
                    win.settings.setValue("linux_first_run_completed", True)
                    win.settings.setValue("linux_setup_completed", True)
                self._checker_card.run_confirmation(
                    tr("linux_prep_reboot_body"),
                    tr("linux_prep_reboot_done_btn"),
                    "",
                    symbol="check",
                )
                if win is not None:
                    win._force_quit = True
                    win.close()
                QApplication.quit()
                return
            self.linux_prep_completed.emit()
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
        if self._solo_card is not None:
            self.content_size_changed.emit()

    def _reveal_checker_card(self):
        """Scroll the preparation card fully into view as it grows."""
        card = getattr(self, "_checker_card", None)
        scroll = getattr(self, "_settings_scroll", None)
        if card is None or scroll is None:
            return
        card.adjustSize()
        QTimer.singleShot(0, card, lambda: scroll.ensureWidgetVisible(card, 0, 12))

    def set_sp_gui_available(self, available: bool) -> None:
        """The Settings shortcut requires a focused or retained full package."""
        self._sp_gui_available = bool(available)
        self._sync_linux_prep_copy()

    def _sync_linux_prep_copy(self) -> None:
        """Keep technical tool names behind Advanced Settings."""
        if not hasattr(self, "_checker_card") or not hasattr(self, "_checker_desc"):
            return
        installer = is_mediatek_installer()
        # Follow the visible Show Advanced Settings switch, not the hidden forced flag.
        advanced = bool(
            (hasattr(self, "_cb_advanced_settings") and self._cb_advanced_settings.isChecked())
            or installer
        )
        if installer:
            title_key = "settings_checker_group_installer"
            desc_key = "settings_checker_desc_installer"
            button_key = "system_checker_run_btn_installer"
        else:
            title_key = "settings_checker_group_advanced" if advanced else "settings_checker_group"
            desc_key = "settings_checker_desc_advanced" if advanced else "settings_checker_desc_friendly"
            button_key = "system_checker_run_btn_advanced" if advanced else "system_checker_run_btn"
        self._checker_card.setTitle(tr(title_key))
        self._checker_desc.setText(tr(desc_key))
        self._btn_run_checker.setText(tr(button_key))
        from ..sp_flash_gui import is_sp_flash_gui_supported
        self._btn_launch_sp.setText(tr("system_checker_launch_gui_btn"))
        self._btn_launch_sp.setEnabled(
            self._sp_gui_available and is_sp_flash_gui_supported()
        )
        setup_flow = self._solo_card is self._checker_card
        self._btn_launch_sp.setVisible(
            self._btn_launch_sp.isEnabled()
            and bool(advanced)
            and not setup_flow
        )

    def _on_launch_sp(self):
        """Hand the selected firmware to the main window's validated route."""
        win = self.window()
        if win is not None and hasattr(win, "_open_sp_flash_tool_gui"):
            win._open_sp_flash_tool_gui()

    @staticmethod
    def _method_card_title_key() -> str:
        """The MediaTek Installer names the engine; the full app says Install Method."""
        return "settings_method_group_mediatek" if is_mediatek_installer() else "settings_method_group"

    def retranslate(self):
        """Retranslate UI elements upon language switch."""
        self._header.setText(tr("settings_title"))
        self._method_card._title_key = self._method_card_title_key()
        self._method_card.retranslate()
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
        if hasattr(self, "_advanced_card"):
            self._advanced_card.retranslate()
            self._set_switch_caption(self._cb_advanced_settings, tr("settings_advanced_toggle"))
            self._cb_advanced_settings.setToolTip(tr("settings_advanced_desc"))
            self._advanced_desc.setText(tr("settings_advanced_desc"))
        if hasattr(self, "_keep_data_card"):
            self._keep_data_card.retranslate()
            self._keep_data_desc.setText(tr("settings_allow_keep_data_any_desc"))
            self._set_switch_caption(
                self._cb_allow_keep_data_any, tr("settings_allow_keep_data_any")
            )
            self._cb_allow_keep_data_any.setToolTip(
                tr("settings_allow_keep_data_any_desc")
            )
        self._reminders_card.retranslate()
        self._set_switch_caption(self._cb_reminders, tr("settings_reminders_enable"))
        if hasattr(self, "_add_device_button"):
            self._add_device_button.setText(tr("settings_add_device"))
        self._update_reminders_copy()
        self._rockbox_card.retranslate()
        self._set_switch_caption(self._cb_rockbox_filters, tr("settings_rockbox_group"))
        self._rockbox_desc.setText(tr("settings_rockbox_desc"))
        self._set_switch_caption(self._cb_old_rockbox, tr("settings_filter_old_rockbox"))
        self._cb_old_rockbox.setToolTip(tr("settings_old_rockbox_warn_body"))
        self._set_switch_caption(self._cb_nightly, tr("settings_filter_nightly"))
        self._set_switch_caption(self._cb_240p, tr("settings_filter_240p"))
        self._set_switch_caption(
            self._cb_original_pre307, tr("settings_filter_original_pre307")
        )
        self._cb_original_pre307.setToolTip(tr("settings_original_pre307_warn_body"))
        self._cb_240p.setToolTip(tr("settings_240p_tip"))
        self._donations_card.retranslate()
        self._set_switch_caption(self._cb_hide_donations, tr("settings_show_donations"))
        self._cb_hide_donations.setToolTip(tr("settings_hide_donations_tip"))
        if hasattr(self, "_lbl_hide_tip"):
            self._lbl_hide_tip.setText(tr("settings_hide_donations_tip"))
        if hasattr(self, "_settings_coffee_btn"):
            self._settings_coffee_btn.setText(tr("donate_coffee_btn"))
        if hasattr(self, "_settings_bmc_btn"):
            self._settings_bmc_btn.setText(tr("donate_buy_me_a_coffee"))
        self._sync_donation_methods()
        if hasattr(self, "_settings_discord_btn"):
            self._settings_discord_btn.setText(tr("community_discord"))
        if hasattr(self, "_settings_reddit_btn"):
            self._settings_reddit_btn.setText(tr("community_reddit"))
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
            self._sync_linux_prep_copy()
        if hasattr(self, "_driver_card"):
            self._driver_card.retranslate()
        if hasattr(self, "_checker_desc"):
            self._sync_linux_prep_copy()
        if hasattr(self, "_driver_desc"):
            self._driver_desc.setText(tr("settings_driver_desc"))
            self._btn_download_drivers.setText(tr("settings_download_drivers_btn"))
        self._offline_mode_card.retranslate()
        self._set_switch_caption(self._cb_offline_mode, tr("settings_offline_mode"))
        self._cb_offline_mode.setToolTip(tr("settings_offline_mode_desc"))
        self._offline_mode_desc.setText(tr("settings_offline_mode_desc"))
        self._notifications_card.retranslate()
        self._set_switch_caption(
            self._cb_notifications, tr("settings_notifications_group")
        )
        self._cb_notifications.setToolTip(tr("settings_notifications_desc"))
        self._notifications_desc.setText(tr("settings_notifications_desc"))
        notification_caption_keys = {
            "general": "settings_notifications_general",
            "warning": "settings_notifications_warning",
            "player_update": "settings_notifications_player",
            "app_update": "settings_notifications_app",
        }
        for category, switch in getattr(self, "_notification_switches", {}).items():
            self._set_switch_caption(switch, tr(notification_caption_keys[category]))
        if hasattr(self, "_cb_notifications_background"):
            self._set_switch_caption(
                self._cb_notifications_background,
                tr("settings_notifications_background"),
            )
            self._cb_notifications_background.setToolTip(
                run_in_background_desc()
            )
        self._notifications_unavailable.setText(
            tr("settings_notifications_unavailable")
        )
        self._background_card.retranslate()
        self._set_switch_caption(
            self._cb_run_in_background, tr("settings_run_in_background_group")
        )
        self._cb_run_in_background.setToolTip(
            run_in_background_desc()
        )
        self._background_desc.setText(run_in_background_desc())
        if hasattr(self, "_install_themes_card"):
            self._install_themes_card.retranslate()
        if hasattr(self, "_install_themes_desc"):
            self._install_themes_desc.setText(tr("settings_install_themes_desc"))
        if hasattr(self, "_themes_beta_pill"):
            self._themes_beta_pill.setText(tr("settings_themes_beta_pill"))
            self._themes_beta_pill.setAccessibleName(tr("settings_themes_beta_pill"))
        if hasattr(self, "_cb_install_themes"):
            self._cb_install_themes.setToolTip(tr("settings_install_themes_desc"))
            self._cb_install_themes.setAccessibleName(tr("settings_install_themes_group"))
        if hasattr(self, "_install_themes_btn"):
            self._install_themes_btn.setText(tr("settings_install_rockbox_themes_btn"))
            self._install_themes_btn.setIcon(get_symbol_icon("folder", 14, color=T().fg))
        if hasattr(self, "_refresh_themes_btn"):
            self._refresh_themes_btn.setText(tr("settings_refresh_themes_btn"))
            self._refresh_themes_btn.setIcon(get_symbol_icon("refresh", 14, color=T().fg))
        self._retranslate_shortcut_switches()
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
            self._clear_cache_btn.setIcon(get_symbol_icon("trash", 14, color=T().fg))
        if hasattr(self, "_dismiss_donations_link"):
            self._dismiss_donations_link.setText(self._donations_dismiss_html())
        for name in ("discord", "reddit"):
            button = getattr(self, f"_settings_{name}_btn", None)
            if button is not None:
                button.setIcon(self._community_icon(name))
                button.setIconSize(QSize(10, 10))
        for widget in (
            getattr(self, "_notifications_card", None),
            getattr(self, "_background_card", None),
            getattr(self, "_notifications_desc", None),
            getattr(self, "_notifications_unavailable", None),
            getattr(self, "_cb_notifications", None),
            getattr(self, "_cb_notifications_background", None),
            getattr(self, "_cb_run_in_background", None),
            getattr(self, "_background_desc", None),
        ):
            if widget is not None:
                widget.update()
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
        if not info.get("has_legacy"):
            # Nothing to remove: there is no pre-3.0 install, so there is no
            # offer. Android platform tools alone never raise this card.
            self._show_legacy_status(tr("legacy_cleanup_none_found"))
            self._finish_card_focus()
            return

        reclaim = format_bytes(int(info.get("estimated_bytes", 0) or 0))
        agreed = self._legacy_cleanup_card.run_confirmation(
            tr("legacy_cleanup_confirm_body"),
            tr("legacy_cleanup_btn_clean"),
            tr("legacy_cleanup_btn_later"),
            note=tr("legacy_cleanup_reclaim").format(size=reclaim),
            symbol="trash",
        )
        if not agreed:
            self._reveal_legacy_card()
            self._finish_card_focus()
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
            self._finish_card_focus()
            return
        self._show_legacy_status(
            tr("legacy_cleanup_done_inline").format(size=reclaim),
            hide_after_ms=4000,
        )
        # The removal is done, so the card has nothing left to ask: the offer
        # ends here and the column comes back.
        self._finish_card_focus()

    def _run_legacy_removal(self, *, uninstall_platform_tools: bool = False):
        """Delete the pre-3.0 leftovers. Returns the errors, if any.

        ``uninstall_platform_tools`` is the user's answer to this card's own
        question; it defaults to keeping them, since a flash run needs ADB.
        """
        from ..legacy_cleanup import (
            remove_linux_legacy,
            remove_macos_legacy,
            remove_windows_legacy,
        )

        try:
            if paths.IS_MAC:
                _ok, _removed, errors = remove_macos_legacy(
                    remove_apps=True,
                    remove_app_support=True,
                    uninstall_platform_tools=bool(uninstall_platform_tools),
                )
            elif sys.platform == "win32":
                _ok, _removed, errors = remove_windows_legacy()
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
