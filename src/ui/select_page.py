"""Select package page — online firmware listing + local file picker."""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, downloads, device_tracking
from ..network import get_connectivity_monitor
from ..worker_lifecycle import retire_worker
from ..flash_service import (
    ExtractWorker,
    compute_extract_dir,
    _is_extract_complete,
    _find_scatter,
    prune_extracted_cache,
)
from ..sp_flash_gui import update_sp_history_ini
from ..config import (
    DEVICE_MODELS,
    detect_model_and_type_from_name,
    device_label_for_model,
    install_disconnect_guidance,
    is_generic_mtk,
    is_mediatek_installer,
    is_offline_mode,
)
from ..i18n import tr, translator
from ..browser import open_browser
from ..translate import (
    get_google_translate_release_url,
    ReleaseTranslateWorker,
)
from .widgets import Banner, BatteryToast, Card, CurrentPageStack, InstallProgress, UpdateToast
from .dark import T, link_html, page_margins
from .scrollbars import configure_scroll_area, make_transparent
from .icons import HELP_ICON_PX, get_help_pixmap, get_symbol_icon, help_icon_source

logger = logging.getLogger(__name__)

# How long a clicked Type tip stays on screen. Long enough to read the whole
# Type A / Type B explanation without the pointer having to stay put.
TYPE_TIP_VISIBLE_MS = 12000

# Release list sizing. The list hugs its contents so Install / Restore and
# the file link sit directly below the items; past MAX rows it scrolls
# internally instead of growing the window without bound. An empty list
# keeps a small placeholder height instead of collapsing to nothing.
MAX_VISIBLE_RELEASE_ROWS = 8
EMPTY_RELEASE_LIST_ROWS = 3


def _network_polling_available() -> bool:
    """False in offscreen runs (the smoke suite, headless CI).

    Those builds construct this page many times over and must never reach the
    network; on a real desktop the connectivity check is what decides between
    the online listing and the local-file tab.
    """
    return os.environ.get("QT_QPA_PLATFORM") != "offscreen"


class ReleasesWorker(QThread):
    finished = Signal(list, str)

    def __init__(
        self,
        client,
        package,
        model,
        show_nightly=False,
        selected_type=None,
        show_old_rockbox=False,
        prefer_240p=False,
        force_refresh=False,
        parent=None,
    ):
        super().__init__(parent)
        self.client = client
        self.package = package
        self.model = model
        self.show_nightly = show_nightly
        self.selected_type = selected_type
        self.show_old_rockbox = show_old_rockbox
        self.prefer_240p = prefer_240p
        self.force_refresh = force_refresh

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            releases = self.client.releases_for_package(
                self.package,
                self.model,
                show_nightly=self.show_nightly,
                selected_type=self.selected_type,
                show_old_rockbox=self.show_old_rockbox,
                prefer_240p=self.prefer_240p,
                force_refresh=self.force_refresh,
            )
            if self.isInterruptionRequested():
                return
            self.finished.emit(releases, "")
        except Exception as e:
            if not self.isInterruptionRequested():
                logger.exception("Releases fetch failed")
                self.finished.emit([], str(e))


def display_software_name(name):
    """User-facing software name with preset i18n replacements.

    Catalogue and tracking names stay canonical English (they are lookup
    keys); only the displayed name is localised. "Original Software" has
    a preset translation per language (``software_original``).
    """
    if (name or "").strip() == "Original Software":
        return tr("software_original")
    return name


class _TabsAdapter:

    """Compatibility adapter presenting QStackedWidget as a QTabWidget interface for tests."""

    def __init__(self, page):
        self._page = page

    def count(self) -> int:
        if is_generic_mtk() or is_offline_mode() or not getattr(self._page, "_online_available", False):
            return 1
        return 2

    def currentWidget(self):
        return self._page._views.currentWidget()

    def widget(self, idx: int):
        if idx == 0 and not (is_generic_mtk() or is_offline_mode()):
            return self._page._online_tab
        return self._page._local_tab

    def tabBar(self):
        adapter = self

        class _TabBar:
            def isVisible(self):
                return not (is_generic_mtk() or is_offline_mode() or not getattr(adapter._page, "_online_available", False))

        return _TabBar()


class _TypeInfoMark(QLabel):
    """Small information mark beside Type. Hover waits; a click shows the tip now."""

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            # One event-loop turn later, on purpose: Qt tears a tooltip down on
            # the very mouse release that asked for it, so showing it inline
            # made the tip vanish again before it could be read.
            QTimer.singleShot(0, self._show_tip_now)
        super().mouseReleaseEvent(event)

    def _show_tip_now(self) -> None:
        tip = self.toolTip()
        if not tip:
            return
        QToolTip.showText(
            self.mapToGlobal(QPoint(0, self.height())),
            tip,
            self,
            self.rect(),
            TYPE_TIP_VISIBLE_MS,
        )


class _ReleaseNotesBrowser(QTextBrowser):
    """Release notes view that dynamically sizes vertically to its document content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(80)
        self.setMaximumHeight(260)

    def sizeHint(self) -> QSize:
        doc = self.document()
        if doc is None:
            return super().sizeHint()
        vp = self.viewport()
        w = vp.width() if vp and vp.width() > 0 else self.width()
        if w > 0:
            doc.setTextWidth(w)
        doc_h = int(doc.documentLayout().documentSize().height())
        target_h = max(28, doc_h + 8)
        # Notes scroll inside their card; they must not drive the app window
        # taller just because a release has unusually long copy.
        target_h = min(target_h, 260)
        from PySide6.QtCore import QSize
        return QSize(super().sizeHint().width(), target_h)

    def minimumSizeHint(self) -> QSize:
        from PySide6.QtCore import QSize
        return QSize(50, 28)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateGeometry()


class SelectPackagePage(QWidget):
    package_selected = Signal(str, str, str)
    gui_release_clicked = Signal(object)
    release_icon_ready = Signal(object)
    gui_handoff_abandoned = Signal()
    download_started = Signal(str, str)
    download_progress = Signal(int, str)
    download_finished = Signal(bool, str)
    download_cancelled = Signal()
    prep_progress = Signal(int)
    prep_started = Signal(str, str)
    # (message, timeout_ms) -> shown in the main window's status bar.
    status_message = Signal(str, int)
    # Emitted whenever the release list is (re)filled, so the window can
    # grow or shrink its height to fit the items.
    content_size_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = None
        self.client = catalog.ReleasesClient()
        self._active_workers = set()
        self._releases_worker = None
        self._download_worker = None
        self._current_package_path = ""
        self._current_package_name = ""
        self._current_package_model = ""
        # MediaTek Installer inputs (Download Agent + scatter are required, the
        # auth file optional). Updater CE leaves the DA empty so the bundled
        # MTK_AllInOne_DA.bin applies.
        self._installer_da = ""
        self._installer_mode = False
        self._online_banner_key = ""
        self._online_banner_count = 0
        self._local_banner_key = ""
        self._local_banner_arg = ""
        self._download_status_key = ""
        self._local_status_key = ""
        self._prep_worker = None
        self._selected_type = None  # None = all types; 'A' or 'B' for filtered
        self._current_selected_rel = None
        self._gui_explicit = None
        self._gui_handoff_worker = False
        self._is_translated = False
        self._is_translating = False
        # Sticky choice: once the user translates once, newly focused
        # releases translate automatically. "Show original" opts back out.
        self._auto_translate_notes = False
        self._translated_notes_cache = {}
        self._translate_worker = None
        # Whether the online catalogue is on offer. It starts hidden and is only
        # offered once the catalogue's own firmware listings load: on a network
        # that cannot reach GitHub (some regions, corporate filters) a generic
        # connectivity probe would happily promise an online catalogue that can
        # never list a build, and the local-file screen is the honest offer.
        self._online_available = False
        self._build_ui()
        self._tabs = _TabsAdapter(self)
        self._update_view_links()
        self._on_model_changed()

        self._monitor = get_connectivity_monitor()
        self._monitor.connectivity_changed.connect(self._on_connectivity_changed)
        if self._connectivity_monitor_applicable():
            self._monitor.start_monitoring()
            if self._monitor.is_online is False:
                self.set_online_mode(False)
            elif self._monitor.is_online is True:
                self.set_online_mode(True)
            # While the first check is in flight nothing is offered yet; the
            # monitor's answer decides (see _on_connectivity_changed).
        elif not is_generic_mtk():
            # Nothing checks listings here (headless runs, and builds that carry
            # no catalogue at all): the catalogue's own state decides instead.
            self.set_online_mode(True)

        if is_generic_mtk():
            self.apply_generic_mode(True)

        self.apply_installer_mode(is_mediatek_installer())
        self._check_and_load_cached_firmware()

    def _check_and_load_cached_firmware(self):
        """Restore last installed/attempted firmware from downloads_dir cache if still on disk."""
        from .. import device_tracking
        from ..sp_flash_gui import cached_install_firmware

        latest = device_tracking.get_latest_package(getattr(self, "settings", None))
        scatter, extract = cached_install_firmware(latest)
        if scatter and extract and extract.is_dir():
            target_path = str(extract)
            self._path_edit.setText(target_path)
            self._current_package_path = target_path
            self._current_package_model = (latest or {}).get("model") or ""
            self._current_package_name = (latest or {}).get("software_name") or extract.name
            self._remember_gui_local(target_path, self._current_package_model, self._current_package_name)
            self._set_local_banner("sel_current_pkg", extract.name)
            self._start_btn.setEnabled(True)
            self._auto_detect_auth_file(target_path)

    def _connectivity_monitor_applicable(self) -> bool:
        """True only when an online catalogue is actually on offer.

        The probe exists to decide between the online and local listings, so
        with the catalogue switched off (offline mode, MediaTek Installer build)
        there is nothing to decide and the periodic HTTPS check would be pure
        cost.
        """
        if is_generic_mtk():
            return False
        return _network_polling_available()

    def apply_generic_mode(self, generic: bool):
        # Network polling follows the catalogue: no catalogue, no probes.
        if not generic and self._connectivity_monitor_applicable():
            if not self._monitor.is_monitoring:
                self._monitor.start_monitoring()
        elif generic and self._monitor.is_monitoring:
            self._monitor.stop_monitoring()

        if generic:
            self._views.setCurrentWidget(self._local_tab)
            self._hint.setText(tr("sel_only_local_generic"))
            self._offline_banner.setText(tr("sel_offline_install_generic"))
        else:
            self._show_online_view()
            self._hint.setText(tr("sel_only_local"))
            self._offline_banner.setText(tr("sel_offline_install"))
        self._update_view_links()

    def _on_connectivity_changed(self, is_online: bool):
        if is_generic_mtk():
            return
        self.set_online_mode(is_online)

    def set_online_mode(self, is_online: bool):
        if is_generic_mtk() and is_online:
            return
        was_available = self._online_available
        self._online_available = bool(is_online)
        self._update_view_links()
        if is_online:
            self._show_online_view(refresh=not was_available)
        else:
            self._views.setCurrentWidget(self._local_tab)
            if was_available:
                self._say(tr("sel_offline"), timeout_ms=3000)

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*page_margins())
        layout.setSpacing(8)

        self._title = QLabel(tr("sel_title"))
        self._title.setProperty("cssClass", "pageTitle")
        if sys.platform == "darwin":
            self._title.setStyleSheet(
                "font-size: 13px; font-weight: 600; color: palette(window-text); letter-spacing: normal; background: transparent; border: none;"
            )
        layout.addWidget(self._title)

        # First-run reminder to charge the player/device battery before use
        self._toast_container = QWidget(self)
        toast_layout = QHBoxLayout(self._toast_container)
        toast_layout.setContentsMargins(4, 0, 4, 0)
        self._battery_toast = BatteryToast(self._toast_container)
        self._battery_toast.dismissed.connect(self._on_dismiss_battery_toast)
        toast_layout.addWidget(self._battery_toast)
        layout.addWidget(self._toast_container)
        self._check_battery_toast_visibility()

        # No tabs: the catalogue *is* the Select Software screen, and the
        # local-file picker is reached from the "Install from File" link below
        # the primary action (and left again from its own link). One screen at
        # a time keeps the layout identical between them, leaves every control
        # a native widget, and gives the file flow the full page instead of a
        # tab-sized strip.
        self._views = CurrentPageStack()
        self._online_tab = self._build_online_tab()
        self._local_tab = self._build_local_tab()
        self._views.addWidget(self._online_tab)
        self._views.addWidget(self._local_tab)
        layout.addWidget(self._views, 1)

    def _link_html(self, key: str, *, bold: bool = True) -> str:
        t = T()
        weight = " font-weight: 700;" if bold else ""
        return (
            f'<a href="#" style="color: {t.fg}; text-decoration: none;'
            f' border: none; background: transparent;{weight}">{tr(key)}</a>'
        )

    def _make_link(self, key: str, on_click, *, bold: bool = True) -> QLabel:
        """A plain text link: hyperlinks carry the pointing cursor, buttons do not.

        Same colour as the surrounding text either way; *bold* marks the link
        that leads somewhere primary. Browsing for a local file is the
        secondary path while the catalogue is on offer (and the MediaTek
        Installer, which is offline-first, does not show it at all), so that
        one stays at the text weight.
        """
        label = QLabel(self._link_html(key, bold=bold))
        label.setAlignment(Qt.AlignCenter)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        label.setCursor(Qt.PointingHandCursor)
        label.linkActivated.connect(lambda *_: on_click())
        if not hasattr(self, "_registered_links"):
            self._registered_links = []
        self._registered_links.append((label, key, bold))
        return label

    def _show_local_view(self):
        """Show the local-file screen ("Install from a file")."""
        self._current_selected_rel = None
        self._raw_release_pixmap = None
        self._views.setCurrentWidget(self._local_tab)

    def catalogue_section(self) -> str:
        """Which catalogue the current install was started from."""
        views = getattr(self, "_views", None)
        if views is not None and views.currentWidget() is getattr(self, "_local_tab", None):
            return "local"
        return "online"

    def show_catalogue_section(self, section: str) -> None:
        if section == "local":
            self._show_local_view()
        else:
            self._show_online_view()

    def _show_online_view(self, refresh: bool = False):
        """Show the catalogue ("Install from the catalogue")."""
        if is_generic_mtk():
            return
        self._views.setCurrentWidget(self._online_tab)
        if refresh or not self._release_list.count():
            self._on_model_changed()

    def _update_view_links(self):
        """Both links are catalogue affordances: no catalogue, no links.

        Offline Mode (and the MediaTek Installer, which is offline-first) has no
        list to choose from and no catalogue to return to, so neither link is
        shown there.
        """
        available = (
            bool(self._online_available)
            and not is_generic_mtk()
            and not is_offline_mode()
        )
        self._install_from_file_link.setVisible(available)
        self._install_online_link.setVisible(available)

    def _say(self, text: str, timeout_ms: int = 0):
        """Show a status message in the main status bar (0 = until replaced)."""
        self.status_message.emit(text, timeout_ms)

    def _build_online_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── Device & Software Filters (Compact 2-Row Responsive Grid) ────
        self._filter_group = Card()
        filter_layout = QGridLayout()
        filter_layout.setContentsMargins(4, 2, 4, 2)
        filter_layout.setHorizontalSpacing(8)
        filter_layout.setVerticalSpacing(6)

        # Row 0: Model and Type. The help mark sits in the type cell, beside the combo.
        self._model_label = QLabel(f"{tr('sel_model')}:")
        self._model_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._model_label, 0, 0)

        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(80)
        self.refresh_models()
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        filter_layout.addWidget(self._model_combo, 0, 1)

        self._type_label = QLabel(f"{tr('sel_type')}:")
        self._type_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._type_label, 0, 2)

        self._type_combo = QComboBox()
        self._type_combo.addItem(tr("sel_type_a"), "A")
        self._type_combo.addItem(tr("sel_type_b"), "B")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)

        self._type_field = QWidget()
        type_row = QHBoxLayout(self._type_field)
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(4)
        type_row.addWidget(self._type_combo, 1)
        self._type_help_icon = _TypeInfoMark(self._type_field)
        self._type_help_icon.setObjectName("typeHelpIcon")
        self._type_help_icon.setCursor(Qt.ArrowCursor)
        self._type_help_icon.setFocusPolicy(Qt.NoFocus)
        self._type_help_icon.setAlignment(Qt.AlignCenter)
        self._type_help_icon.setFixedSize(HELP_ICON_PX, HELP_ICON_PX)
        self._type_help_icon.setContentsMargins(0, 0, 0, 0)
        type_row.addWidget(self._type_help_icon, 0, Qt.AlignVCenter)
        self._apply_type_help_hint()
        filter_layout.addWidget(self._type_field, 0, 3)

        # Row 1: Software & Refresh
        self._software_label = QLabel(f"{tr('sel_software')}:")
        self._software_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._software_label, 1, 0)

        self._software_combo = QComboBox()
        self._software_combo.setMinimumWidth(120)
        self._software_combo.currentTextChanged.connect(self._on_software_changed)
        filter_layout.addWidget(self._software_combo, 1, 1, 1, 2)

        self._refresh_btn = QPushButton(tr("sel_refresh"))
        self._refresh_btn.setToolTip(tr("sel_refresh_tooltip"))
        self._refresh_btn.clicked.connect(lambda: self._refresh_releases(force_refresh=True))
        filter_layout.addWidget(self._refresh_btn, 1, 3)

        filter_layout.setColumnStretch(1, 1)
        filter_layout.setColumnStretch(3, 1)
        self._filter_group.set_layout(filter_layout)

        layout.addWidget(self._filter_group)

        # Rockbox listing filters (old builds / nightly / 240p) live on the
        # Settings screen; they are applied here only for Y1 Rockbox browsing.
        self._online_banner = Banner()
        self._online_banner.setVisible(False)
        layout.addWidget(self._online_banner)
        self._update_prompt = UpdateToast()
        self._update_prompt.install_clicked.connect(self._on_toast_install)
        self._update_prompt.close_clicked.connect(self._on_toast_close)
        self._update_prompt.model_clicked.connect(self._on_toast_model)
        self._update_offers = []
        self._toast_session_mute = set()
        layout.addWidget(self._update_prompt)

        # ── Split Content: Left = Packages + Install; Right = Status + Notes ──
        split = QHBoxLayout()
        split.setSpacing(10)

        # Left panel: Available System Software
        self._pkg_group = Card("sel_available_software")
        pkg_layout = QVBoxLayout()
        pkg_layout.setContentsMargins(0, 0, 0, 0)
        pkg_layout.setSpacing(6)

        self._release_list = QListWidget()
        self._release_list.setObjectName("releaseList")
        self._release_list.setFrameShape(QFrame.NoFrame)
        self._release_list.viewport().setAutoFillBackground(False)
        configure_scroll_area(
            self._release_list,
            horizontal=Qt.ScrollBarAlwaysOff,
            vertical=Qt.ScrollBarAsNeeded,
            transparent=True,
        )
        self._release_list.currentItemChanged.connect(self._on_release_selected)
        self._release_list.itemClicked.connect(self._on_release_clicked)
        self._release_list.itemActivated.connect(self._on_install)
        self._release_list.itemDoubleClicked.connect(self._on_release_double_clicked)
        pkg_layout.addWidget(self._release_list)
        self._fit_release_list_height()

        # Slim progress bar, only visible while downloading / preparing.
        self._download_bar = InstallProgress()
        self._download_bar.setTextVisible(True)
        self._download_bar.setFormat("%p%")
        self._download_bar.setMinimumHeight(18)
        from .dark import install_progress_readability
        install_progress_readability(self._download_bar)
        self._download_bar.setVisible(False)
        pkg_layout.addWidget(self._download_bar)

        self._install_btn = QPushButton(tr("sel_install"))
        self._install_btn.setIcon(get_symbol_icon("install", 16))
        self._install_btn.setDefault(True)
        self._install_btn.setAutoDefault(True)
        self._install_btn.setMinimumHeight(32)
        self._install_btn.setEnabled(False)
        self._install_btn.setToolTip(tr("sel_install_tooltip_generic"))
        self._install_btn.clicked.connect(self._on_install)
        pkg_layout.addWidget(self._install_btn)

        # The way into the local-file screen. A link, not a button: it is a
        # navigation affordance, and the primary action above stays the only
        # button competing for attention.
        self._install_from_file_link = self._make_link(
            "sel_install_from_file", self._show_local_view, bold=False
        )
        pkg_layout.addWidget(self._install_from_file_link)
        # The card is as tall as the notes column beside it, so the slack has
        # to go *after* the link: a plain label would otherwise grow to take
        # it and centre its text half a card below the button.
        self._install_from_file_link.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        pkg_layout.addStretch(1)

        self._pkg_group.set_layout(pkg_layout)
        self._pkg_group.set_glass_clear(True)
        split.addWidget(self._pkg_group, 5)

        # Right panel: Status and Notes
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._notes_group = Card()
        notes_l = QVBoxLayout()
        notes_l.setContentsMargins(0, 0, 0, 0)
        notes_l.setSpacing(4)

        details_row = QHBoxLayout()
        details_row.setContentsMargins(0, 0, 0, 0)
        details_row.setSpacing(8)
        from .release_icon import FadingIcon

        self._details_icon = FadingIcon(40)
        details_row.addWidget(self._details_icon, 0, Qt.AlignTop)
        details_text = QVBoxLayout()
        details_text.setContentsMargins(0, 0, 0, 0)
        details_text.setSpacing(0)
        self._details_name = QLabel("")
        self._details_name.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: palette(window-text); border: none;"
        )
        self._details_version = QLabel("")
        self._details_version.setStyleSheet(
            "font-size: 12px; color: palette(window-text); border: none;"
        )
        details_text.addWidget(self._details_name)
        details_text.addWidget(self._details_version)
        details_row.addLayout(details_text, 1)
        self._details_header = QWidget()
        self._details_header.setLayout(details_row)
        self._details_header.setAutoFillBackground(False)
        self._details_header.setVisible(False)
        from .surfaces import clear_glass_plate
        clear_glass_plate(self._details_header)
        clear_glass_plate(self._details_name)
        clear_glass_plate(self._details_version)
        clear_glass_plate(self._details_icon)
        notes_l.addWidget(self._details_header)

        self._notes = _ReleaseNotesBrowser()
        self._notes.setObjectName("releaseNotes")
        self._notes.setReadOnly(True)
        self._notes.setOpenLinks(False)
        self._notes.setOpenExternalLinks(False)
        self._notes.viewport().setAutoFillBackground(False)
        self._notes.setFrameShape(QFrame.NoFrame)
        self._notes.setLineWrapMode(QTextBrowser.WidgetWidth)
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        # Anchors inside the notes are the text colour and bold, never the
        # document default blue-with-underline (document stylesheet, not a
        # widget stylesheet: the platform keeps the scrollbars).
        self._notes.document().setDefaultStyleSheet(
            f"a {{ color: {T().fg}; font-weight: 700; text-decoration: none; }}"
        )
        # Host policies plus the live palette. No stylesheet on this view:
        # a matching rule would cost the host's overlay scrollbars
        # (see src/ui/scrollbars.py).
        configure_scroll_area(
            self._notes,
            horizontal=Qt.ScrollBarAlwaysOff,
            vertical=Qt.ScrollBarAsNeeded,
            transparent=True,
        )
        from .surfaces import clear_glass_plate
        clear_glass_plate(self._release_list)
        clear_glass_plate(self._notes)
        self._notes.anchorClicked.connect(self._on_notes_link_clicked)
        notes_l.addWidget(self._notes)

        self._translate_label = QLabel()
        self._translate_label.setObjectName("translateReleaseNotes")
        self._translate_label.setWordWrap(True)
        self._translate_label.setAlignment(Qt.AlignCenter)
        self._translate_label.setStyleSheet(
            "color: palette(window-text); font-size: 11px; margin-top: 4px; background: transparent;"
        )
        self._translate_label.setTextFormat(Qt.RichText)
        self._translate_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._translate_label.linkActivated.connect(self._on_translate_link_clicked)
        self._translate_label.setVisible(False)
        notes_l.addWidget(self._translate_label)
        notes_l.addStretch(1)

        self._notes_group.set_layout(notes_l)
        self._notes_group.set_glass_clear(True)
        right_layout.addWidget(self._notes_group, 1)

        split.addWidget(right_panel, 6)
        layout.addLayout(split, 1)
        return page

    def _set_notes_content(self, html_text: str):
        self._notes.setHtml(html_text)
        from .surfaces import clear_glass_plate
        clear_glass_plate(self._notes)
        self._notes.updateGeometry()
        if hasattr(self, "_notes_group") and self._notes_group:
            self._notes_group.updateGeometry()
            self._notes_group.repaint()
        if hasattr(self, "_notes") and self._notes and self._notes.viewport():
            self._notes.viewport().repaint()
        self.content_size_changed.emit()

    def _build_local_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        self._hint = QLabel(tr("sel_only_local"))
        self._hint.setWordWrap(True)
        self._hint.setProperty("cssClass", "hint")
        layout.addWidget(self._hint)

        self._offline_banner = Banner()
        self._offline_banner.set_type("info")
        self._offline_banner.set_key("sel_offline_install")
        layout.addWidget(self._offline_banner)

        self._local_group = local_group = Card("sel_choose_firmware_pkg")
        grp_l = QVBoxLayout()
        grp_l.setContentsMargins(0, 0, 0, 0)
        grp_l.setSpacing(6)

        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._browse_btn = QPushButton(tr("sel_browse"))
        self._browse_btn.setIcon(get_symbol_icon("file", 16))
        self._browse_btn.clicked.connect(self._on_choose_file)
        self._browse_folder_btn = QPushButton(tr("sel_browse_folder"))
        self._browse_folder_btn.setIcon(get_symbol_icon("folder", 16))
        self._browse_folder_btn.clicked.connect(self._on_choose_folder)
        row.addWidget(self._path_edit, 1)
        row.addWidget(self._browse_btn)
        row.addWidget(self._browse_folder_btn)
        grp_l.addLayout(row)

        # MediaTek Installer: the three files SP Flash Tool's own setup asks
        # for. The Download Agent and scatter are required; an auth file is
        # only needed by some secure-booted firmwares. Updater CE hides both
        # rows — there the firmware package (and its bundled DA) is the whole
        # input.
        self._da_row_widget = QWidget()
        da_row = QHBoxLayout(self._da_row_widget)
        da_row.setContentsMargins(0, 0, 0, 0)
        da_row.setSpacing(8)
        self._da_label = QLabel(tr("mtk_da_label") + ":")
        self._da_edit = QLineEdit()
        self._da_edit.setReadOnly(True)
        self._da_edit.setPlaceholderText(tr("mtk_da_placeholder"))
        self._browse_da_btn = QPushButton(tr("sel_browse"))
        self._browse_da_btn.setIcon(get_symbol_icon("file", 16))
        self._browse_da_btn.clicked.connect(self._on_choose_da_file)
        self._clear_da_btn = QPushButton(tr("settings_sp_auth_clear"))
        self._clear_da_btn.setIcon(get_symbol_icon("cancel", 12))
        self._clear_da_btn.clicked.connect(self._on_clear_da_file)
        da_row.addWidget(self._da_label)
        da_row.addWidget(self._da_edit, 1)
        da_row.addWidget(self._browse_da_btn)
        da_row.addWidget(self._clear_da_btn)
        grp_l.addWidget(self._da_row_widget)
        self._da_row_widget.setVisible(False)

        self._scatter_row_widget = QWidget()
        scatter_row = QHBoxLayout(self._scatter_row_widget)
        scatter_row.setContentsMargins(0, 0, 0, 0)
        scatter_row.setSpacing(8)
        self._scatter_label = QLabel(tr("mtk_scatter_label") + ":")
        self._scatter_edit = QLineEdit()
        self._scatter_edit.setReadOnly(True)
        self._scatter_edit.setPlaceholderText(tr("mtk_scatter_placeholder"))
        self._browse_scatter_btn = QPushButton(tr("sel_browse"))
        self._browse_scatter_btn.setIcon(get_symbol_icon("file", 16))
        self._browse_scatter_btn.clicked.connect(self._on_choose_scatter_file)
        scatter_row.addWidget(self._scatter_label)
        scatter_row.addWidget(self._scatter_edit, 1)
        scatter_row.addWidget(self._browse_scatter_btn)
        grp_l.addWidget(self._scatter_row_widget)
        self._scatter_row_widget.setVisible(False)

        # Auth file row: MediaTek Installer only. The Innioasis targets Updater
        # CE serves have no secure boot to answer — they flash with the bundled
        # MTK_AllInOne_DA.bin, the same way the Chinese build does — so Updater
        # CE never offers an auth file at all.
        self._auth_row_widget = QWidget()
        self._auth_row = QHBoxLayout(self._auth_row_widget)
        self._auth_row.setContentsMargins(0, 0, 0, 0)
        self._auth_row.setSpacing(8)
        self._auth_label = QLabel(tr("settings_sp_auth") + ":")
        self._auth_edit = QLineEdit()
        self._auth_edit.setReadOnly(True)
        self._auth_edit.setPlaceholderText(tr("settings_sp_auth_none"))
        self._browse_auth_btn = QPushButton(tr("settings_sp_auth_browse"))
        self._browse_auth_btn.setIcon(get_symbol_icon("file", 16))
        self._browse_auth_btn.clicked.connect(self._on_choose_auth_file)
        self._clear_auth_btn = QPushButton(tr("settings_sp_auth_clear"))
        self._clear_auth_btn.setIcon(get_symbol_icon("cancel", 12))
        self._clear_auth_btn.clicked.connect(self._on_clear_auth_file)
        self._auth_row.addWidget(self._auth_label)
        self._auth_row.addWidget(self._auth_edit, 1)
        self._auth_row.addWidget(self._browse_auth_btn)
        self._auth_row.addWidget(self._clear_auth_btn)
        grp_l.addWidget(self._auth_row_widget)
        self._auth_row_widget.setVisible(False)
        self._sync_auth_file_ui()

        self._local_banner = Banner()
        grp_l.addWidget(self._local_banner)

        self._local_bar = InstallProgress()
        self._local_bar.setTextVisible(True)
        self._local_bar.setFormat("%p%")
        self._local_bar.setMinimumHeight(18)
        from .dark import install_progress_readability
        install_progress_readability(self._local_bar)
        self._local_bar.setVisible(False)
        grp_l.addWidget(self._local_bar)

        self._local_status = QLabel("")
        from .dark import apply_explicit_foreground
        apply_explicit_foreground(self._local_status, " font-size: 12px; border: none;")
        grp_l.addWidget(self._local_status)

        self._local_rockbox_themes_cb = QCheckBox(tr("sel_local_rockbox_themes_option"))
        self._local_rockbox_themes_cb.setChecked(False)
        grp_l.addWidget(self._local_rockbox_themes_cb)

        self._start_btn = QPushButton(tr("sel_btn_start"))
        self._start_btn.setIcon(get_symbol_icon("install", 16))
        self._start_btn.setDefault(True)
        self._start_btn.setAutoDefault(True)
        self._start_btn.setMinimumHeight(32)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_flash)
        grp_l.addWidget(self._start_btn)

        # ...and the way back to the catalogue list. Same weight as the file
        # link above: both are secondary navigation, and the primary action is
        # the button.
        self._install_online_link = self._make_link(
            "sel_install_online", self._show_online_view, bold=False
        )
        self._install_online_link.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        grp_l.addWidget(self._install_online_link)
        grp_l.addStretch(1)

        local_group.set_layout(grp_l)
        layout.addWidget(local_group)
        layout.addStretch()
        return page

    def _sync_auth_file_ui(self):
        from .. import device_tracking
        current = device_tracking.sp_auth_file(getattr(self, "settings", None))
        if hasattr(self, "_auth_edit"):
            self._auth_edit.setText(current or "")
            if hasattr(self, "_clear_auth_btn"):
                self._clear_auth_btn.setVisible(bool(current))

    def _on_choose_auth_file(self):
        from .. import device_tracking
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("settings_sp_auth"),
            "",
            "Authentication Files (*.auth);;All Files (*.*)",
        )
        if path:
            device_tracking.set_sp_auth_file(path, getattr(self, "settings", None))
            self._sync_auth_file_ui()

    def _on_clear_auth_file(self):
        from .. import device_tracking
        device_tracking.set_sp_auth_file("", getattr(self, "settings", None))
        self._sync_auth_file_ui()

    def _auto_detect_auth_file(self, target_path: str) -> None:
        """Scan package directory or archive location for an .auth file (e.g. authsv.auth, *.auth)."""
        from .. import device_tracking
        p = Path(target_path)
        candidates = []
        if p.is_dir():
            candidates.append(p)
        elif p.is_file():
            candidates.append(p.parent)
        try:
            from ..flash_service import compute_extract_dir
            ext = compute_extract_dir(target_path)
            if ext.is_dir() and ext not in candidates:
                candidates.append(ext)
        except Exception:
            pass

        for c in candidates:
            try:
                for f in c.rglob("*.auth"):
                    if f.is_file() and f.stat().st_size > 0:
                        logger.info("Auto-detected auth file: %s", f)
                        device_tracking.set_sp_auth_file(str(f.resolve()), getattr(self, "settings", None))
                        self._sync_auth_file_ui()
                        return
            except Exception:
                pass

    # ── MediaTek Installer: Download Agent / scatter ──────────────────────
    def apply_installer_mode(self, enabled: bool) -> None:
        """Show the DA + scatter + auth fields the generic installer flashes with.

        The installer is a front end for the same two backends Updater CE uses;
        it just takes the three files SP Flash Tool's own setup takes (Download
        Agent, scatter, optional auth) instead of a firmware package. Updater CE
        keeps its single package row and its bundled DA — and no auth row, since
        none of its targets use one.
        """
        self._installer_mode = bool(enabled)
        for widget in (
            getattr(self, "_da_row_widget", None),
            getattr(self, "_scatter_row_widget", None),
            getattr(self, "_auth_row_widget", None),
        ):
            if widget is not None:
                widget.setVisible(bool(enabled))
        for name in ("_path_edit", "_browse_btn", "_browse_folder_btn"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not enabled)
        if enabled:
            self._prefill_da()
        self._sync_auth_file_ui()
        self._update_local_labels()

    def _update_local_labels(self) -> None:
        if hasattr(self, "_da_label"):
            self._da_label.setText(tr("mtk_da_label") + ":")
            self._da_edit.setPlaceholderText(tr("mtk_da_placeholder"))
            self._browse_da_btn.setText(tr("sel_browse"))
            self._scatter_label.setText(tr("mtk_scatter_label") + ":")
            self._scatter_edit.setPlaceholderText(tr("mtk_scatter_placeholder"))
            self._browse_scatter_btn.setText(tr("sel_browse"))
        if self._installer_mode and hasattr(self, "_local_group"):
            self._local_group.setTitle(tr("mtk_choose_flash_files"))

    def _prefill_da(self) -> None:
        """Offer the Download Agent the app already ships, when there is one."""
        if not self._installer_da:
            try:
                from ..sp_flash_gui import bundled_da_path

                da = bundled_da_path()
            except Exception:
                logger.debug("Could not locate a bundled download agent", exc_info=True)
                da = None
            if da:
                self._installer_da = str(da)
        self._sync_da_ui()

    def _sync_da_ui(self) -> None:
        if not hasattr(self, "_da_edit"):
            return
        self._da_edit.setText(self._installer_da or "")
        self._clear_da_btn.setVisible(bool(self._installer_da))

    def _on_choose_da_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("mtk_da_label"), "", tr("mtk_da_filter")
        )
        if path:
            self._installer_da = path
            self._sync_da_ui()

    def _on_clear_da_file(self) -> None:
        self._installer_da = ""
        self._sync_da_ui()

    def _on_choose_scatter_file(self) -> None:
        """Point the installer at a scatter file (its images live beside it)."""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("mtk_scatter_label"), "", tr("mtk_scatter_filter")
        )
        if not path:
            return
        p = Path(path)
        if not p.is_file():
            return
        self._scatter_edit.setText(str(p))
        # SP Flash Tool's own layout: the partition images sit in the folder
        # beside the scatter file, so that folder is the package for this run.
        package_target = str(p.parent)
        self._path_edit.setText(package_target)
        self._current_package_path = package_target
        self._current_installed_release_info = None
        self._current_selected_rel = None
        self._details_package = None
        self._raw_release_pixmap = None
        self._auto_detect_auth_file(package_target)
        mfr_info = catalog.parse_manufacturer_rom_info(p.parent.name) or catalog.parse_manufacturer_rom_info(p.name)
        if mfr_info:
            det_m = mfr_info["model"]
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
            self._selected_type = None
            self._current_package_name = mfr_info["title"]
        else:
            det_m, det_t = detect_model_and_type_from_name(p.parent.name)
            if not det_m:
                det_m, det_t = detect_model_and_type_from_name(p.name)
            self._current_package_model = det_m or ""
            if det_m:
                self._select_model_for_manual(det_m)
                self._current_package_name = f"Installing software on {det_m}"
            else:
                self._current_package_name = "Installing software"
            self._selected_type = det_t or None
        self._remember_gui_local(package_target, self._current_package_model, self._current_package_name)
        self._set_local_banner("sel_current_pkg", p.name)
        self._details_package = catalog.FirmwarePackage(
            slug="local-" + str(Path(package_target).name),
            name=self._current_package_name or Path(package_target).name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._set_details_header(None, self._details_package)
        self._on_local_prep_done(True, str(p.parent), "")

    def installer_da_file(self) -> str:
        """The Download Agent chosen for a MediaTek Installer flash (or "")."""
        return self._installer_da if self._installer_mode else ""

    def installer_auth_file(self) -> str:
        """The auth file chosen on this screen (falls back to the saved one)."""
        return device_tracking.sp_auth_file(getattr(self, "settings", None)) or ""

    def _type_help_tip(self) -> str:
        """Full device-type help as a wrapping tooltip. No dialog.

        Plain text, not rich text: the tip is drawn by the platform, and a
        rich-text document would need its own colours written into it — which
        is what made these tips look nothing like the host's own tooltips.
        """
        body = tr("sel_type_help_body")
        if not body or body == "sel_type_help_body":
            body = tr("device_type_help_msg") or tr("sel_type_help_tooltip")
        return (body or "").strip()

    def _refresh_type_help_icon(self) -> None:
        """Same hover tip as the Type combo, on the platform information glyph beside it.

        Tinted with the accent, matching the information badge the host desktop
        draws in its own settings panes.
        """
        icon = getattr(self, "_type_help_icon", None)
        if icon is None:
            return
        icon.setCursor(Qt.ArrowCursor)
        theme = T()
        color = getattr(theme, "accent", "") or getattr(theme, "fg_muted", None)
        pix = get_help_pixmap(HELP_ICON_PX, color=color)
        icon.setText("")
        if pix is not None and not pix.isNull():
            icon.setPixmap(pix)
        else:
            icon.setPixmap(QPixmap())
        icon.setProperty("helpSource", help_icon_source())

    def _apply_type_help_hint(self) -> None:
        tip = self._type_help_tip()
        if hasattr(self, "_type_combo"):
            self._type_combo.setToolTip(tip)
        if hasattr(self, "_type_label"):
            self._type_label.setToolTip(tip)
        if hasattr(self, "_type_help_icon"):
            self._type_help_icon.setToolTip(tip)
            self._refresh_type_help_icon()

    def _should_prompt_pre_install(self) -> bool:
        """On Linux with SP Flash Tool methods, step 1 (ensuring USB cable is removed
        and device powered off) is naturally presented after silent system prep and privilege
        escalation, when flash_tool fires the line beginning with 'search usb'.
        On Windows or with MTKClient, prompt upfront before starting.
        """
        import sys
        from PySide6.QtCore import QSettings
        from ..flash_service import METHOD_MTK, backend_method
        from ..paths import IS_MAC, IS_WINDOWS
        if IS_WINDOWS or IS_MAC:
            return True
        if not sys.platform.startswith("linux"):
            return True
        # On Linux the SP Flash Tool flow defers step 1 until the tool prints
        # its "search usb" line (the app escalates and sets up udev first).
        # MTKClient has no such line to hang it on, so its run is gated on the
        # power-off screen. NB: the same settings handle the rest of the app
        # uses — reading a differently-cased QSettings path silently returned
        # the default, so an MTKClient run on Linux never prompted at all.
        settings = QSettings("innioasis", "updater")
        return backend_method(settings.value("flash_method", "")) == METHOD_MTK

    def _prompt_pre_install(self, model="", type_variant=None):
        """Device preparation is a page in the install flow, not a dialog."""
        return True

    def _set_update_prompt(self, software: str, model: str, offer=None) -> None:
        """Keep the catalogue's current model in the update note.

        Other models already on the note stay. An empty offer removes only
        this model. Session dismiss hides a release until the next launch.
        """
        software = (software or "").strip()
        model = (model or "").strip()
        offers = [
            item for item in (getattr(self, "_update_offers", None) or [])
            if not (item.get("model") == model and item.get("software") == software)
        ]
        if software and model and offer:
            offers.append({
                "model": model,
                "software": software,
                "tag": str(offer.get("tag") or ""),
                "release": offer.get("release"),
                "package": offer.get("package"),
            })
        self._update_offers = offers
        self._present_update_offers()

    def show_update_offers(self, updates) -> None:
        """Show every device that has a newer release. Does not start an install."""
        offers = []
        for upd in updates or []:
            model = str((upd or {}).get("model") or "").strip()
            software = str((upd or {}).get("software_name") or "").strip()
            if not model or not software:
                continue
            offers.append({
                "model": model,
                "software": software,
                "tag": str((upd or {}).get("latest_tag") or ""),
                "release": (upd or {}).get("latest_release"),
                "package": (upd or {}).get("package"),
            })
        self._update_offers = offers
        self._present_update_offers()

    def _present_update_offers(self) -> None:
        prompt = getattr(self, "_update_prompt", None)
        if prompt is None:
            return
        muted = getattr(self, "_toast_session_mute", set())
        visible = [
            offer for offer in (getattr(self, "_update_offers", None) or [])
            if (offer.get("model"), offer.get("software"), str(offer.get("tag") or "")) not in muted
        ]
        prompt.set_offers(visible)

    def _on_toast_install(self) -> None:
        offers = []
        prompt = getattr(self, "_update_prompt", None)
        if prompt is not None and hasattr(prompt, "offers"):
            offers = prompt.offers()
        offer = offers[0] if len(offers) == 1 else {}
        release = offer.get("release")
        if not release:
            return
        self.start_install_for_release(
            model=str(offer.get("model") or self.current_model()),
            software_name=str(offer.get("software") or self.current_software()),
            tag_name=str(offer.get("tag") or ""),
            release=release,
            package=offer.get("package") or self._package_for_selection(),
        )

    def _on_toast_close(self) -> None:
        """Hide every offer on the note until the next launch.

        This does not store a notify ceiling or a skipped release.
        """
        self._toast_session_mute = set(getattr(self, "_toast_session_mute", set()))
        for offer in list(getattr(self, "_update_offers", None) or []):
            self._toast_session_mute.add((
                offer.get("model"),
                offer.get("software"),
                str(offer.get("tag") or ""),
            ))
        self._present_update_offers()

    def _on_toast_model(self, offer) -> None:
        """Select that model and focus its release. Does not start an install."""
        if not isinstance(offer, dict):
            return
        self.navigate_to_package(
            str(offer.get("model") or ""),
            str(offer.get("software") or ""),
            tag_name=str(offer.get("tag") or "") or None,
        )
        release = offer.get("release")
        if not release:
            return
        target = self._firmware_target_for_release(release)
        if target:
            self._gui_explicit = target

    def _set_online_banner(self, key, count=0):
        self._online_banner_key = key
        self._online_banner_count = count
        self._apply_online_banner()

    def _apply_online_banner(self):
        key = self._online_banner_key
        if key == "releases":
            self._online_banner.set_type("info")
            self._online_banner.setText(
                tr("sel_n_releases_fmt").format(
                    n=self._online_banner_count, action=tr("sel_install")
                )
            )
        elif key:
            self._online_banner.set_type("warning" if key in ("sel_offline", "sel_no_release") else "info")
            self._online_banner.setText(tr(key))
        else:
            self._online_banner.setText("")

    def _set_local_banner(self, key, arg=""):
        self._local_banner_key = key
        self._local_banner_arg = arg
        self._apply_local_banner()

    def _apply_local_banner(self):
        if self._local_banner_key == "sel_current_pkg":
            self._local_banner.set_type("success")
            self._local_banner.setText(f"{tr('sel_current_pkg')}: {self._local_banner_arg}")
        elif self._local_banner_key:
            self._local_banner.setText(tr(self._local_banner_key))
        else:
            self._local_banner.setText("")

    def _check_battery_toast_visibility(self) -> None:
        from PySide6.QtCore import QSettings

        settings = self.settings or QSettings("innioasis", "updater")
        dismissed = bool(settings.value("battery_hint_dismissed", False, type=bool))
        self._battery_toast.setVisible(not dismissed)
        if hasattr(self, "_toast_container"):
            self._toast_container.setVisible(not dismissed)

    def _on_dismiss_battery_toast(self) -> None:
        from PySide6.QtCore import QSettings

        settings = self.settings or QSettings("innioasis", "updater")
        settings.setValue("battery_hint_dismissed", True)
        self._battery_toast.setVisible(False)
        if hasattr(self, "_toast_container"):
            self._toast_container.setVisible(False)
    def reset_selection_and_filters(self) -> None:
        """Reset filters, selections, cached firmware display, and battery reminder toast back to initial defaults."""
        from PySide6.QtCore import QSettings

        settings = self.settings or QSettings("innioasis", "updater")
        settings.remove("battery_hint_dismissed")
        self._check_battery_toast_visibility()
        if hasattr(self, "_model_combo") and self._model_combo.count() > 0:
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentIndex(0)
            self._model_combo.blockSignals(False)
        if hasattr(self, "_type_combo") and self._type_combo.count() > 0:
            self._type_combo.blockSignals(True)
            self._type_combo.setCurrentIndex(0)
            self._type_combo.blockSignals(False)
        if hasattr(self, "_software_combo") and self._software_combo.count() > 0:
            self._software_combo.blockSignals(True)
            self._software_combo.setCurrentIndex(0)
            self._software_combo.blockSignals(False)
        if hasattr(self, "_show_online_view"):
            self._show_online_view()
        if hasattr(self, "_check_and_load_cached_firmware"):
            self._check_and_load_cached_firmware()
        self.content_size_changed.emit()

    def retranslate(self):
        self._title.setText(tr("sel_title"))
        if hasattr(self, "_battery_toast"):
            self._battery_toast.retranslate()
        self._install_from_file_link.setText(
            self._link_html("sel_install_from_file", bold=False)
        )
        self._install_online_link.setText(
            self._link_html("sel_install_online", bold=False)
        )
        self._model_label.setText(f"{tr('sel_model')}:")
        self._type_label.setText(f"{tr('sel_type')}:")
        self._software_label.setText(f"{tr('sel_software')}:")
        self._refresh_btn.setText(tr("sel_refresh"))
        self._install_btn.setText(tr("sel_install"))
        if self._current_selected_rel:
            lbl = self._current_selected_rel.get("name") or self._current_selected_rel.get("tag_name") or ""
            self._install_btn.setToolTip(tr("sel_install_tooltip").format(lbl=lbl))
        else:
            self._install_btn.setToolTip(tr("sel_install_tooltip_generic"))
        if is_generic_mtk():
            self._hint.setText(tr("sel_only_local_generic"))
            self._offline_banner.setText(tr("sel_offline_install_generic"))
        else:
            self._hint.setText(tr("sel_only_local"))
            self._offline_banner.retranslate()
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        # Anchors inside the notes are the text colour and bold, never the
        # document default blue-with-underline (document stylesheet, not a
        # widget stylesheet: the platform keeps the scrollbars).
        self._notes.document().setDefaultStyleSheet(
            f"a {{ color: {T().fg}; font-weight: 700; text-decoration: none; }}"
        )
        self._browse_btn.setText(tr("sel_browse"))
        if hasattr(self, "_browse_folder_btn"):
            self._browse_folder_btn.setText(tr("sel_browse_folder"))
        if hasattr(self, "_auth_label"):
            self._auth_label.setText(tr("settings_sp_auth") + ":")
        if hasattr(self, "_browse_auth_btn"):
            self._browse_auth_btn.setText(tr("settings_sp_auth_browse"))
        if hasattr(self, "_clear_auth_btn"):
            self._clear_auth_btn.setText(tr("settings_sp_auth_clear"))
        if hasattr(self, "_auth_edit"):
            self._auth_edit.setPlaceholderText(tr("settings_sp_auth_none"))
        self._update_local_labels()
        if hasattr(self, "_local_rockbox_themes_cb"):
            self._local_rockbox_themes_cb.setText(tr("sel_local_rockbox_themes_option"))
        self._start_btn.setText(tr("sel_btn_start"))
        self._apply_online_banner()
        if hasattr(self, "_update_prompt"):
            self._update_prompt.retranslate()
        self._apply_local_banner()
        self._type_combo.setItemText(0, tr("sel_type_a"))
        self._type_combo.setItemText(1, tr("sel_type_b"))
        # Software names carry preset translations: re-label the dropdown rows
        # in place, keeping the canonical English name as each row's data.
        self._software_combo.blockSignals(True)
        for i in range(self._software_combo.count()):
            canonical = str(
                self._software_combo.itemData(i) or self._software_combo.itemText(i)
            )
            self._software_combo.setItemData(i, canonical)
            self._software_combo.setItemText(i, display_software_name(canonical))
        self._software_combo.blockSignals(False)
        self._apply_type_help_hint()
        self._refresh_btn.setToolTip(tr("sel_refresh_tooltip"))
        self._pkg_group.setTitle(tr("sel_available_software"))
        self._local_group.setTitle(
            tr("mtk_choose_flash_files") if self._installer_mode else tr("sel_choose_firmware_pkg")
        )
        self._update_device_status_copy()
        if self._download_status_key:
            self._say(tr(self._download_status_key))
        if self._local_status_key:
            self._local_status.setText(tr(self._local_status_key))
        if self._current_selected_rel:
            if self._is_translated or (
                self._auto_translate_notes and (translator().lang or "en").strip() != "en"
            ):
                self._translate_current_release_in_app()
            else:
                self._set_notes_content(self._render_release_notes(self._current_selected_rel, as_html=True))
            self._set_details_header(
                self._current_selected_rel,
                getattr(self, "_details_package", None) or self._package_for_selection(),
            )
        self._update_translate_link()
        if hasattr(self, "_registered_links"):
            for label, key, bold in self._registered_links:
                label.setText(self._link_html(key, bold=bold))

    def refresh_theme(self):
        """Update components and release notes typography to match active theme tokens."""
        self._apply_type_help_hint()
        if hasattr(self, "_notes"):
            self._notes.document().setDefaultStyleSheet(
                f"a {{ color: {T().fg}; font-weight: 700; text-decoration: none; }}"
            )
        if hasattr(self, "_notes") and self._current_selected_rel:
            self._set_notes_content(self._render_release_notes(self._current_selected_rel, as_html=True))
        if hasattr(self, "_translate_label"):
            self._update_translate_link()
        if hasattr(self, "_release_list"):
            make_transparent(self._release_list)
            from .surfaces import clear_glass_plate
            clear_glass_plate(self._release_list)
        if hasattr(self, "_notes"):
            from .surfaces import clear_glass_plate
            clear_glass_plate(self._notes)
        if hasattr(self, "_details_header"):
            from .surfaces import clear_glass_plate
            clear_glass_plate(self._details_header)
            clear_glass_plate(self._details_name)
            clear_glass_plate(self._details_version)
        if hasattr(self, "_registered_links"):
            for label, key, bold in self._registered_links:
                label.setText(self._link_html(key, bold=bold))
        if hasattr(self, "_install_btn"):
            self._install_btn.setIcon(get_symbol_icon("install", 16))
        if hasattr(self, "_update_prompt") and hasattr(self._update_prompt, "refresh_theme"):
            self._update_prompt.refresh_theme()
        rel = getattr(self, "_current_selected_rel", None)
        if rel and hasattr(self, "_details_icon"):
            self._set_details_header(rel, getattr(self, "_details_package", None) or self._package_for_selection())
        listed = list(getattr(self, "_listed_releases", None) or [])
        if listed:
            self._prefetch_release_icons(listed)
        elif rel:
            self._ensure_release_icon(rel)
        if hasattr(self, "_browse_btn"):
            self._browse_btn.setIcon(get_symbol_icon("file", 16))
        if hasattr(self, "_browse_folder_btn"):
            self._browse_folder_btn.setIcon(get_symbol_icon("folder", 16))
        if hasattr(self, "_start_btn"):
            self._start_btn.setIcon(get_symbol_icon("install", 16))
        for attr, symbol, size in (
            ("_browse_da_btn", "file", 16),
            ("_clear_da_btn", "cancel", 12),
            ("_browse_scatter_btn", "file", 16),
            ("_browse_auth_btn", "file", 16),
            ("_clear_auth_btn", "cancel", 12),
        ):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setIcon(get_symbol_icon(symbol, size))
        from .dark import apply_explicit_foreground, install_progress_readability
        status = getattr(self, "_local_status", None)
        if status is not None:
            apply_explicit_foreground(status, " font-size: 12px; border: none;")
        for bar_name in ("_download_bar", "_local_bar"):
            bar = getattr(self, bar_name, None)
            if bar is not None:
                install_progress_readability(bar)

    def refresh_models(self):
        """Repopulate the device drop-down from what the catalogue offers.

        Only models with a release in the live catalogue are listed — there is
        no point offering a device with nothing to install. The wider lineup in
        src/device_models.py exists so that firmware imported by hand still
        gets guidance naming the right player; such a model is appended here
        while it is selected, so the drop-down never contradicts the prompt.
        """
        from .. import device_models

        model_ids = device_models.selectable_models(
            available_ids=catalog.available_models(),
            fallback=DEVICE_MODELS,
        )
        current = self._model_combo.currentText().strip()
        if current and current not in model_ids:
            # Keep a manually imported model visible/selected.
            model_ids.append(current)

        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(model_ids)
        if current:
            idx = self._model_combo.findText(current)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        self._model_combo.blockSignals(False)

    def _select_model_for_manual(self, model: str):
        """Select ``model`` for a hand-imported package, adding it if needed."""
        if not model:
            return
        idx = self._model_combo.findText(model)
        if idx < 0:
            # Not catalogue-backed (e.g. a G5): add it so the connection
            # prompts name the right player, and let refresh_models() drop it
            # again once another model is chosen.
            self._model_combo.addItem(model)
            idx = self._model_combo.count() - 1
        self._model_combo.setCurrentIndex(idx)

    def _on_model_changed(self):
        model = self.current_model()
        # Show the type filter only for models that have Type A/B variants (Y1).
        has_types = model.upper() == "Y1"
        self._type_label.setVisible(has_types)
        self._type_combo.setVisible(has_types)
        if hasattr(self, "_type_help_icon"):
            self._type_help_icon.setVisible(has_types)
        if hasattr(self, "_type_field"):
            self._type_field.setVisible(has_types)
        if not has_types:
            self._selected_type = "A"
        else:
            self._selected_type = self._type_combo.currentData() or "A"
        names = catalog.software_names_for_model(model)
        self._populate_software_combo(names)
        self._update_device_status_copy()
        self._on_software_changed()

    def _populate_software_combo(self, names, select=None) -> None:
        """Fill the Software dropdown with localised labels.

        The canonical English name stays as the item's ``Qt.UserRole`` data:
        catalogue lookups and device tracking key off it, and only the label
        is translated (see :func:`display_software_name`). ``select`` is a
        canonical name to restore after the rebuild.
        """
        combo = self._software_combo
        combo.blockSignals(True)
        combo.clear()
        for name in names:
            combo.addItem(display_software_name(name), name)
        if select is not None:
            idx = self._find_software_index(select)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _find_software_index(self, name) -> int:
        """Row for a canonical or displayed software name, or -1."""
        combo = self._software_combo
        target = (name or "").strip()
        for i in range(combo.count()):
            if str(combo.itemData(i) or "") == target or combo.itemText(i) == target:
                return i
        localized = display_software_name(target)
        for i in range(combo.count()):
            if combo.itemText(i) == localized:
                return i
        return -1

    def _update_device_status_copy(self):
        model = self.current_model()
        eff_type = self._selected_type if model.upper() == "Y1" else None
        lbl = device_label_for_model(model, eff_type)
        if hasattr(self, "_install_btn"):
            self._install_btn.setToolTip(tr("sel_install_tooltip").format(lbl=lbl))

    def is_rockbox_y1_browsing(self) -> bool:
        """True when the Rockbox package for Y1 is the one being browsed."""
        software = (self.current_software() or "").lower()
        return self.current_model().upper() == "Y1" and "rockbox" in software

    def is_local_rockbox_themes_checked(self) -> bool:
        """True if the user chose to install Rockbox 360p themes & fonts for local file."""
        if hasattr(self, "_local_rockbox_themes_cb"):
            return self._local_rockbox_themes_cb.isChecked()
        return False

    def is_rockbox_y2_browsing(self) -> bool:
        """True when the Rockbox package for Y2 is the one being browsed."""
        software = (self.current_software() or "").lower()
        return self.current_model().upper() == "Y2" and "rockbox" in software

    def release_listing_filters(self) -> tuple:
        """``(show_old, show_nightly, prefer_240p)`` for the current browse.

        The Settings toggles only take effect for Rockbox releases on Y1.
        Gated behind Advanced Settings to prevent installer errors for standard users.
        """
        if not device_tracking.advanced_settings_enabled():
            return (False, False, False)
        if not self.is_rockbox_y1_browsing():
            return (False, False, False)
        if self.is_rockbox_y2_browsing():
            return (False, False, False)
        filters = device_tracking.rockbox_release_filters()
        return (
            bool(filters.old_rockbox),
            bool(filters.nightly),
            bool(filters.rockbox_240p),
        )

    def refresh_release_filters(self):
        """Re-list releases after the Settings filters changed."""
        self._refresh_releases()

    def _on_type_changed(self, index):
        self._selected_type = self._type_combo.currentData()
        self._refresh_releases()

    def _on_software_changed(self):
        self._refresh_releases()

    def _refresh_releases(self, force_refresh=False):
        package = self.current_package()
        self._release_list.clear()
        self._install_btn.setEnabled(False)
        if package is None:
            self._set_online_banner("sel_no_release")
            return
        old = self._releases_worker
        if old is not None and old.isRunning():
            try:
                old.finished.disconnect()
            except Exception:
                pass
            old.requestInterruption()
            old.wait(100)

        self._set_online_banner("sel_loading")
        show_old, show_nightly, prefer_240p = self.release_listing_filters()
        worker = ReleasesWorker(
            self.client,
            package,
            self.current_model(),
            show_nightly=show_nightly,
            selected_type=self._selected_type,
            show_old_rockbox=show_old,
            prefer_240p=prefer_240p,
            force_refresh=force_refresh,
            parent=self,
        )
        self._releases_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(lambda rels, err: self._on_releases_worker_finished(worker, rels, err))
        worker.start()
        if force_refresh:
            self._refresh_manifest()

    def _on_releases_worker_finished(self, worker, releases, error):
        self._active_workers.discard(worker)
        if worker is self._releases_worker:
            self._on_releases_loaded(releases, error)

    def _refresh_manifest(self):
        try:
            from ..manifest import ManifestWorker
            old = getattr(self, "_manifest_worker", None)
            if old is not None and old.isRunning():
                try:
                    old.finished.disconnect()
                except Exception:
                    pass
                old.requestInterruption()
                old.wait(100)
            worker = ManifestWorker(self, force_refresh=True)
            self._manifest_worker = worker
            self._active_workers.add(worker)
            worker.finished.connect(lambda entries: self._on_manifest_worker_finished(worker, entries))
            worker.start()
        except Exception as e:
            logger.debug("Background manifest refresh failed: %s", e)

    def _on_manifest_worker_finished(self, worker, entries):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_manifest_worker", None):
            self._on_manifest_refreshed(entries)

    def _on_manifest_refreshed(self, entries):
        if entries:
            current_sw = self.current_software()
            names = catalog.software_names_for_model(self.current_model())
            combo = self._software_combo
            existing = [
                str(combo.itemData(i) or combo.itemText(i)) for i in range(combo.count())
            ]
            if names != existing:
                self._populate_software_combo(names, select=current_sw)

    def _release_row_height(self) -> int:
        """Height of one release row, from a real row or the list font."""
        if self._release_list.count():
            return max(1, self._release_list.sizeHintForRow(0))
        return self._release_list.fontMetrics().lineSpacing() + 8

    def _fit_release_list_height(self):
        """Size the release list to its contents (capped).

        With few items the list is exactly as tall as its rows, so the
        Install / Restore button and the Install-from-file link sit directly
        below them instead of floating under list whitespace. With many
        items the list stops at MAX_VISIBLE_RELEASE_ROWS and scrolls.
        """
        count = self._release_list.count()
        if count:
            rows = min(count, MAX_VISIBLE_RELEASE_ROWS)
            height = sum(
                max(1, self._release_list.sizeHintForRow(i)) for i in range(rows)
            )
        else:
            rows = EMPTY_RELEASE_LIST_ROWS
            height = rows * self._release_row_height()
        height += 2 * self._release_list.frameWidth() + 2
        self._release_list.setMinimumHeight(height)
        self._release_list.setMaximumHeight(height)
        self._release_list.updateGeometry()

    def content_height_hint(self) -> int:
        """Height the page needs to show its contents without compression.

        The list sits several layouts deep (card, split, view stack), and a
        refill leaves stale hint caches in those nested layouts — measuring
        the page directly would report the previous rows. So invalidate
        every layout from the list up to the page, then take the larger of
        the page's size and minimum hints (the list enforces its height via
        min/max, which sizeHint() alone ignores).
        """
        page_layout = self.layout()
        if page_layout is not None:
            self._invalidate_layout_tree(page_layout)
            page_layout.activate()
        return max(self.sizeHint().height(), self.minimumSizeHint().height())

    @staticmethod
    def _invalidate_layout_tree(layout) -> None:
        """Recursively clear cached hint/size data for a layout subtree."""
        try:
            layout.invalidate()
        except Exception:
            return
        try:
            count = layout.count()
        except Exception:
            return
        for i in range(count):
            try:
                item = layout.itemAt(i)
            except Exception:
                continue
            if item is None:
                continue
            try:
                child = item.layout()
            except Exception:
                continue
            if child is None:
                try:
                    widget = item.widget()
                except Exception:
                    continue
                if widget is not None:
                    try:
                        child = widget.layout()
                    except Exception:
                        continue
            if child is not None:
                SelectPackagePage._invalidate_layout_tree(child)

    def _on_releases_loaded(self, releases, error):
        if error:
            self._set_online_banner("sel_offline")
            self._set_update_prompt(self.current_software(), self.current_model(), None)
            self._fit_release_list_height()
            self.content_size_changed.emit()
            return
        self._release_list.clear()
        releases = sorted(releases or [], key=catalog.release_sort_key, reverse=True)
        prefer_240p = self.release_listing_filters()[2]
        installed_tag = ""
        installed_published = ""
        install_rec = None
        curr_model = self.current_model() or "Y1"
        software_name = self.current_software() or ""
        try:
            from .. import device_tracking
            install_rec = device_tracking.get_software_install(
                curr_model, software_name, settings=getattr(self, "settings", None),
            )
            if install_rec:
                installed_tag = install_rec.get("tag_name") or ""
                installed_published = install_rec.get("published_at") or ""
        except Exception:
            pass

        self._listed_releases = list(releases)
        ceiling = (install_rec or {}).get("notify_ceiling") or ""
        marks = catalog.classify_release_list(
            releases, installed_tag, installed_published, ceiling,
        )
        prompt = marks.get("prompt")
        if prompt and installed_tag:
            package = None
            try:
                pkgs = catalog.packages_for_model_software(curr_model, software_name)
                package = pkgs[0] if pkgs else None
            except Exception:
                package = None
            self._set_update_prompt(software_name, curr_model, {
                "tag": prompt.get("newer_tag") or "",
                "release": prompt.get("newer_release"),
                "package": package,
            })
        else:
            self._set_update_prompt(software_name, curr_model, None)

        for rel in releases:
            label = catalog.parse_clean_release_tag_label(rel, prefer_240p=prefer_240p)
            tag = rel.get("tag_name", "")
            row = marks["rows"].get(tag) or {}
            is_installed = bool(row.get("installed"))
            is_newer = bool(row.get("newer"))

            display_label = f"● {label}" if is_installed else label

            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, rel)
            font = item.font()
            font.setBold(is_newer and not is_installed)
            item.setFont(font)

            tooltip = self._asset_line(rel)
            if tag:
                tooltip = f"{tag}\n{tooltip}".strip()
            if is_installed:
                tip = tr("sel_installed_tip")
                tooltip = f"{tip}\n{tooltip}".strip()
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, tip)
            item.setToolTip(tooltip)
            self._release_list.addItem(item)
        if not releases:
            self._set_online_banner("sel_no_release")
        else:
            self._set_online_banner("releases", len(releases))
            target_tag = getattr(self, "_target_tag_to_select", None)
            selected_row = 0
            if target_tag:
                self._target_tag_to_select = None
                for row in range(self._release_list.count()):
                    it = self._release_list.item(row)
                    r = it.data(Qt.UserRole)
                    if r and r.get("tag_name") == target_tag:
                        selected_row = row
                        break
            self._release_list.setCurrentRow(selected_row)
            self._prefetch_release_icons(releases)
            if self._install_btn.isEnabled():
                self._install_btn.setDefault(True)
                self._install_btn.setFocus()
            if getattr(self, "_auto_install_tag", None):
                target_auto = self._auto_install_tag
                self._auto_install_tag = None
                for row in range(self._release_list.count()):
                    it = self._release_list.item(row)
                    r = it.data(Qt.UserRole) if it else None
                    if r and r.get("tag_name") == target_auto:
                        self._release_list.setCurrentRow(row)
                        self._trigger_release_install(r)
                        break
            self._fit_release_list_height()
            self.updateGeometry()
            self.content_size_changed.emit()

    def _asset_line(self, rel):
        assets = ", ".join(
            v["asset"].get("name", "") for v in (rel.get("rom_variants") or [])
        )
        return assets or ""

    def _on_release_selected(self, current, _prev):
        self._install_btn.setEnabled(current is not None)
        if current is None:
            self._current_selected_rel = None
        if current is not None:
            self._install_btn.setDefault(True)
        if current is None:
            self._current_selected_rel = None
            self._details_package = None
            self._is_translated = False
            self._is_translating = False
            self._notes.clear()
            self._notes.updateGeometry()
            self._set_details_header(None, None)
            self._update_translate_link()
            self.content_size_changed.emit()
            return
        rel = current.data(Qt.UserRole)
        if rel:
            self._current_selected_rel = rel
            self._details_package = self._package_for_selection()
            self._is_translated = False
            self._is_translating = False
            self._set_notes_content(self._render_release_notes(rel, as_html=True))
            self._set_details_header(rel, self._details_package)
            self._start_icon_job(self._listed_releases or [rel], priority_tag=str(rel.get("tag_name") or ""))
            if self._auto_translate_notes and (translator().lang or "en").strip() != "en":
                self._translate_current_release_in_app()
            self._update_translate_link()

    def _render_release_notes(self, rel, translated_body=None, translated_name=None, as_html: bool = False) -> str:
        body = translated_body or (rel.get("body") or "").strip() or tr("update_no_notes")
        # The badge is not repeated here: the line under the notes already says
        # the copy was translated, and the reader should see the release notes
        # themselves, not the provenance twice.

        # 1. Normalise markdown: turn any markdown headings #{1,6} into bold text
        # so all text is rendered at uniform body text size.
        cleaned_md = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"**\1**\n", body[:4000])

        # Strip all markdown images ![alt](url)
        cleaned_md = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned_md)
        # Strip HTML images <img ...>
        cleaned_md = re.sub(r"<img[^>]*>", "", cleaned_md, flags=re.IGNORECASE)
        # Strip empty link anchors left over from image-only links: [ ](url)
        cleaned_md = re.sub(r"\[\s*\]\(.*?\)", "", cleaned_md)
        # Normalise HTML headings <h1>-<h6> to bold paragraphs
        cleaned_md = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"<p><b>\1</b></p>", cleaned_md, flags=re.IGNORECASE)

        if not as_html:
            return cleaned_md

        doc = QTextDocument()
        doc.setMarkdown(cleaned_md)
        html = doc.toHtml()
        # Qt's markdown export paints a solid paper colour on the body. The
        # details pane sits on the card, which sits on the system material.
        html = re.sub(
            r"background(?:-color)?:\s*[^;\"']+;?",
            "",
            html,
            flags=re.IGNORECASE,
        )

        t = T()
        # 2. Re-style links: clickable, bold, same color as text (not blue #0000ff):
        html = re.sub(
            r'<span style="[^"]*color:#0000ff;[^"]*">',
            f'<span style="color: {t.fg}; font-weight: bold; text-decoration: none;">',
            html,
        )
        html = re.sub(
            r'<a\s+href="([^"]+)">',
            f'<a href="\\1" style="color: {t.fg}; font-weight: bold; text-decoration: none;">',
            html,
        )
        html = re.sub(r"<img[^>]*>", "", html, flags=re.IGNORECASE)
        # Normalise all inline font-size declarations so no differences in text size exist
        html = re.sub(r"font-size:[^;\"'>]+;?", "font-size: 13px;", html)

        # 3. Inject CSS ensuring uniform 13px font size across all elements:
        css = f"""
        <style type="text/css">
        body, p, span, div, li, b, strong, em, i, a, code, pre, h1, h2, h3, h4, h5, h6 {{
            color: {t.fg};
            font-size: 13px;
            line-height: 1.45;
        }}
        body {{
            margin: 0;
            padding: 0;
        }}
        h1, h2, h3, h4, h5, h6 {{
            font-weight: 700;
            margin-top: 4px;
            margin-bottom: 4px;
            font-size: 13px;
        }}
        p {{
            margin-top: 4px;
            margin-bottom: 6px;
            color: {t.fg};
            font-size: 13px;
        }}
        ul, ol {{
            margin-top: 4px;
            margin-bottom: 6px;
            padding-left: 18px;
        }}
        li {{
            margin-bottom: 2px;
            color: {t.fg};
            font-size: 13px;
        }}
        code, pre {{
            font-family: Menlo, Monaco, Consolas, "Cascadia Code", "Courier New", monospace;
            font-size: 13px;
            background-color: {t.bg_hover};
            color: {t.fg};
            padding: 2px 4px;
            border-radius: 3px;
        }}
        a {{
            color: {t.fg};
            font-weight: bold;
            text-decoration: none;
            font-size: 13px;
        }}
        </style>
        """
        if "<head>" in html:
            html = html.replace("<head>", f"<head>{css}")
        else:
            html = f"<html><head>{css}</head><body>{html}</body></html>"
        return html

    def _update_translate_link(self):
        rel = self._current_selected_rel
        lang = (translator().lang or "en").strip()
        if not rel or lang == "en":
            self._translate_label.setVisible(False)
            self._translate_label.setText("")
            self._translate_label.updateGeometry()
            self.content_size_changed.emit()
            return

        t = T()
        url = get_google_translate_release_url(rel, lang)
        self._translate_label.setVisible(True)

        if self._is_translating:
            msg = tr("translating_notes")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'<span style="color:{t.fg};">⏳ {msg} &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg}; font-weight:700; text-decoration:none;">{open_txt} ↗</a></span>'
            )
        elif self._is_translated:
            badge = tr("translated_with_google")
            show_orig = tr("show_original")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'✓ <span style="color:{t.fg};">{badge}</span> &nbsp;·&nbsp; '
                f'<a href="action:show_original" style="color:{t.fg}; font-weight:700; text-decoration:none;">{show_orig}</a> &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg}; font-weight:700; text-decoration:none;">{open_txt} ↗</a>'
            )
        else:
            trans_txt = tr("translate_notes")
            open_txt = tr("open_in_google_translate")
            try:
                from .icons import get_symbol_data_uri
                globe_uri = get_symbol_data_uri("translate", 12, match_accent=False)
                globe_html = f'<img src="{globe_uri}" width="12" height="12" style="vertical-align: -1px;"> '
            except Exception:
                globe_html = ""
            self._translate_label.setText(
                f'{globe_html}<a href="action:translate_in_app" style="color:{t.fg}; text-decoration:none; font-weight:700;">{trans_txt}</a> &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg}; text-decoration:none; font-weight:700;">{open_txt} ↗</a>'
            )
        self._translate_label.updateGeometry()
        self.content_size_changed.emit()

    def _on_translate_link_clicked(self, link: str):
        if link == "action:translate_in_app":
            self._auto_translate_notes = True
            self._translate_current_release_in_app()
        elif link == "action:show_original":
            self._show_original_release_notes()
        elif link.startswith(("http://", "https://")):
            open_browser(link)

    def _on_notes_link_clicked(self, qurl_or_str):
        url_str = qurl_or_str.toString() if hasattr(qurl_or_str, "toString") else str(qurl_or_str)
        if not url_str:
            return
        if url_str == "action:translate_in_app":
            self._auto_translate_notes = True
            self._translate_current_release_in_app()
        elif url_str == "action:show_original":
            self._show_original_release_notes()
        elif url_str.startswith(("http://", "https://", "mailto:")):
            open_browser(url_str)

    def set_auto_translate_notes(self, enabled: bool):
        """Translate release notes from now on, starting with what is on screen.

        Called when the app language changes, so a reader who switches to
        another language sees the visible release notes in that language
        immediately — no extra click — and every release they select afterwards
        is translated too.
        """
        self._auto_translate_notes = bool(enabled)
        lang = (translator().lang or "en").strip()
        if not enabled or lang == "en":
            # Switching back to English (or turning translation off) puts the
            # published notes back, so the very same change that turned it on
            # can turn it off.
            if self._is_translated or self._is_translating:
                self._show_original_release_notes()
            return
        self._translate_current_release_in_app()

    def _translate_current_release_in_app(self):
        rel = self._current_selected_rel
        if not rel:
            return
        lang = (translator().lang or "en").strip()
        if lang == "en":
            return
        tag = rel.get("tag_name", "")
        cached = self._translated_notes_cache.get((tag, lang))
        if cached:
            trans_name, trans_body = cached
            self._is_translated = True
            self._is_translating = False
            self._set_notes_content(
                self._render_release_notes(rel, translated_body=trans_body, translated_name=trans_name, as_html=True)
            )
            self._update_translate_link()
            return

        self._is_translating = True
        self._update_translate_link()
        if self._translate_worker is not None and self._translate_worker.isRunning():
            self._translate_worker.wait(500)
            # A wait that times out leaves the thread running; keeping the
            # reference stops Qt destroying it (which aborts the process).
            retire_worker(self._translate_worker, self._active_workers)
        self._translate_worker = ReleaseTranslateWorker(rel, lang, self)
        self._translate_worker.translation_ready.connect(self._on_translation_ready)
        self._translate_worker.translation_failed.connect(self._on_translation_failed)
        self._translate_worker.start()

    def _on_translation_ready(self, rel, lang, trans_name, trans_body):
        self._translated_notes_cache[(rel.get("tag_name", ""), lang)] = (trans_name, trans_body)
        cur = self._current_selected_rel
        if cur == rel or (cur and cur.get("tag_name") == rel.get("tag_name")):
            self._is_translating = False
            self._is_translated = True
            self._set_notes_content(
                self._render_release_notes(rel, translated_body=trans_body, translated_name=trans_name, as_html=True)
            )
            self._update_translate_link()

    def _on_translation_failed(self, rel, lang, error):
        cur = self._current_selected_rel
        if cur == rel or (cur and cur.get("tag_name") == rel.get("tag_name")):
            self._is_translating = False
            self._update_translate_link()

    def _show_original_release_notes(self):
        self._is_translated = False
        self._is_translating = False
        self._auto_translate_notes = False
        if self._current_selected_rel:
            self._set_notes_content(self._render_release_notes(self._current_selected_rel, as_html=True))
        self._update_translate_link()

    def _trigger_release_install(self, rel, package=None):
        if not rel or not rel.get("download_url"):
            return
        pkg = package or self.current_package()
        if pkg is None:
            return
        self._details_package = pkg

        # Determine model and variant from original asset name / download URL:
        orig_name = rel.get("asset_name") or rel.get("download_url") or ""
        det_m, det_t = detect_model_and_type_from_name(orig_name)
        eff_model = det_m or getattr(pkg, "device", "") or self.current_model()
        eff_type = det_t or self._selected_type

        # Pre-install disconnect / power-off confirmation popup (skipped for SP Flash Tool on Linux):
        if self._should_prompt_pre_install():
            if not self._prompt_pre_install(eff_model, eff_type):
                return

        self._current_package_model = eff_model
        self._pending_install_release_info = {
            "model": eff_model,
            "software_name": pkg.name,
            "package_slug": pkg.slug,
            "tag_name": rel.get("tag_name", ""),
            "release_label": catalog.format_release_display_label(rel),
            "type_variant": eff_type,
            "published_at": rel.get("published_at", ""),
        }
        dest_dir = downloads.downloads_dir()
        fname = Path(rel.get("asset_name") or "rom.zip").name
        dest = dest_dir / f"{pkg.slug}_{rel.get('tag_name', 'latest')}_{fname}"
        extract_dir = compute_extract_dir(dest)

        # 1. Check if package is already downloaded and fully extracted:
        if dest.is_file() and dest.stat().st_size > 0 and _is_extract_complete(extract_dir):
            logger.info("Package %s already cached and extracted at %s, reusing immediately", dest.name, extract_dir)
            self._current_package_path = str(dest)
            self._current_package_name = self._install_card_title(pkg.name, rel, eff_model)
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._download_status_key = "sel_prepare_done"
            self._on_online_prep_done(True, str(extract_dir), "")
            return

        # 2. Check if package is already downloaded but needs extraction:
        if dest.is_file() and dest.stat().st_size > 0:
            logger.info("Package %s already downloaded, extracting directly", dest.name)
            self._current_package_path = str(dest)
            self._current_package_name = self._install_card_title(pkg.name, rel, eff_model)
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._prepare_package(str(dest), self._on_online_prep_done)
            return

        # 3. Otherwise, start download worker. Package progress is on the install card.
        self._current_package_path = str(dest)
        self._current_package_name = self._install_card_title(pkg.name, rel, eff_model)
        self._current_package_model = eff_model
        self._current_installed_release_info = self._pending_install_release_info
        self._current_selected_rel = rel
        self._download_status_key = "sel_download_start"
        self._install_btn.setEnabled(False)
        display_name = self._install_card_title(pkg.name, rel, eff_model)
        self.download_started.emit(display_name, eff_model)
        self._gui_handoff_worker = False
        if self._download_worker is not None and self._download_worker.isRunning():
            self._download_worker.cancel()
            retire_worker(self._download_worker, self._active_workers)
        self._download_worker = downloads.DownloadWorker(rel["download_url"], str(dest))
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.status.connect(self._on_download_status)
        self._download_worker.finished.connect(self._on_download_done)
        self._download_worker.start()

    def retry_missing_cached_firmware(self):
        """Retry the selected release after its archive or extracted cache vanished."""
        rel = getattr(self, "_current_selected_rel", None)
        if rel is None:
            self._say(tr("sel_cache_retry_select_release"), 12000)
            return
        self._trigger_release_install(rel, package=getattr(self, "_details_package", None))

    def cancel_download(self):
        worker = self._download_worker
        if worker is not None:
            worker.cancel()
            # The thread only notices between chunks, so it outlives this call:
            # dropping the last reference here destroyed a running QThread and
            # aborted the app.
            retire_worker(worker, self._active_workers)
            self._download_worker = None
        self._download_bar.setVisible(False)
        self._install_btn.setEnabled(True)
        self.download_cancelled.emit()

    def _on_install(self):
        item = self._release_list.currentItem()
        if item is None:
            return
        self._stop_gui_handoff_workers()
        self.gui_handoff_abandoned.emit()
        rel = item.data(Qt.UserRole)
        self._trigger_release_install(rel)
        self._current_selected_rel = rel

    def _on_download_progress(self, val: int):
        self._last_download_percent = int(val)
        self.download_progress.emit(val, getattr(self, "_last_download_status_text", ""))

    def _on_download_status(self, text: str):
        self._last_download_status_text = text
        self._download_status_key = ""
        self.download_progress.emit(int(getattr(self, "_last_download_percent", 0)), text)

    def _on_download_done(self, ok, result):
        self._download_bar.setVisible(False)
        self.download_finished.emit(ok, str(result))
        if not ok:
            self._download_status_key = ""
            self._say(f"{tr('sel_download_failed')} \u2014 {result}", 15000)
            self._install_btn.setEnabled(True)
            return
        eff_model = getattr(self, "_current_package_model", "") or self.current_model()
        self._current_package_path = result
        self._current_package_name = self._install_card_title(
            self.current_software(),
            getattr(self, "_current_selected_rel", None),
            eff_model,
        )
        self._current_package_model = eff_model
        self._current_installed_release_info = getattr(self, "_pending_install_release_info", None)
        self._details_package = self.current_package()
        self._set_details_header(self._current_selected_rel, self._details_package)
        self._prepare_package(result, self._on_online_prep_done)

    def _prepare_package(self, path, done_cb):
        if self._prep_worker is not None:
            self._prep_worker.cancel()
            retire_worker(self._prep_worker, self._active_workers)
        self._download_status_key = "sel_preparing"
        self._local_status_key = "sel_preparing"
        self._download_bar.setVisible(False)
        self._local_bar.setVisible(False)
        self.prep_started.emit(
            getattr(self, "_current_package_name", "") or "",
            getattr(self, "_current_package_model", "") or "",
        )
        self._prep_worker = ExtractWorker(path)
        self._prep_worker.progress.connect(self.prep_progress)
        self._prep_worker.finished.connect(done_cb)
        self._prep_worker.finished.connect(self._on_prep_finished)
        self._prep_worker.start()

    def _on_prep_finished(self, *_args):
        self._prep_worker = None

    def _on_online_prep_done(self, ok, extract_dir, err):
        self._download_bar.setVisible(False)
        if not ok:
            self._download_status_key = ""
            if "MULTIPLE_SCATTERS" in str(err):
                self._say(tr("sel_multiple_scatters_title"), 15000)
                QMessageBox.warning(
                    self,
                    tr("sel_multiple_scatters_title"),
                    tr("sel_multiple_scatters_error"),
                )
            else:
                self._say(f"{tr('sel_prepare_failed')} \u2014 {err}", 15000)
            self._install_btn.setEnabled(True)
            return
        self._download_status_key = "sel_prepare_done"

        # Retain only the most recently downloaded software package!
        prune_extracted_cache(keep_package_path=self._current_package_path)

        # Track latest package in device_tracking and prepopulate SP history.ini
        scatter_file = _find_scatter(Path(extract_dir))
        scatter_abs = scatter_file.resolve() if scatter_file else None
        extract_abs = Path(extract_dir).resolve() if extract_dir else None
        info = getattr(self, "_current_installed_release_info", {}) or {}
        eff_model = info.get("model") or getattr(self, "_current_package_model", "") or self.current_model()
        device_tracking.record_latest_package(
            model=eff_model,
            software_name=info.get("software_name") or self.current_software(),
            tag_name=info.get("tag_name", ""),
            package_path=str(self._current_package_path),
            extract_dir=str(extract_abs) if extract_abs else "",
            scatter_path=str(scatter_abs) if scatter_abs else "",
        )
        update_sp_history_ini(
            scatter_path=scatter_abs,
            extract_dir=extract_abs,
            model=eff_model,
        )

        self._emit_ready()

    def _on_local_prep_done(self, ok, extract_dir, err):
        self._local_bar.setVisible(False)
        if not ok:
            self._local_status_key = ""
            if "MULTIPLE_SCATTERS" in str(err):
                self._local_status.setText(tr("sel_multiple_scatters_title"))
                QMessageBox.warning(
                    self,
                    tr("sel_multiple_scatters_title"),
                    tr("sel_multiple_scatters_error"),
                )
            else:
                self._local_status.setText(f"{tr('sel_prepare_failed')} \u2014 {err}")
            return
        self._local_status_key = "sel_prepare_done"
        self._local_status.setText(tr("sel_prepare_done"))

        # Retain only this package's extracted files
        prune_extracted_cache(keep_package_path=self._current_package_path)

        # Show the browsed package in the details header and start its icon path.
        self._details_package = self._details_package or catalog.FirmwarePackage(
            slug="local-" + str(Path(self._current_package_path).name),
            name=self._current_package_name or Path(self._current_package_path).name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._set_details_header(self._current_selected_rel, self._details_package)

        # Auto-detect .auth file in same folder/zip/rar
        self._auto_detect_auth_file(extract_dir)

        scatter_file = _find_scatter(Path(extract_dir))
        scatter_abs = scatter_file.resolve() if scatter_file else None
        extract_abs = Path(extract_dir).resolve() if extract_dir else None
        eff_model = getattr(self, "_current_package_model", "")
        if not eff_model and scatter_abs:
            plat, proj = catalog.parse_scatter_header(scatter_abs)
            inferred = catalog.infer_model_from_scatter(plat, proj)
            if inferred:
                eff_model = inferred
                self._current_package_model = eff_model
                self._select_model_for_manual(eff_model)
                if not self._current_package_name or self._current_package_name == "Installing software":
                    self._current_package_name = f"Installing software on {eff_model}"
                if self._details_package:
                    self._details_package.model = eff_model
                    self._details_package.name = self._current_package_name
                    self._set_details_header(self._current_selected_rel, self._details_package)
        device_tracking.record_latest_package(
            model=eff_model,
            software_name=self._current_package_name,
            tag_name="",
            package_path=str(self._current_package_path),
            extract_dir=str(extract_abs) if extract_abs else "",
            scatter_path=str(scatter_abs) if scatter_abs else "",
        )
        update_sp_history_ini(
            scatter_path=scatter_abs,
            extract_dir=extract_abs,
            model=eff_model,
        )

        self._start_btn.setEnabled(True)

    def _on_choose_file(self):
        filter_str = tr("sel_dialog_filter")
        path, _ = QFileDialog.getOpenFileName(self, tr("sel_dialog_title"), "", filter_str)
        if not path:
            return
        p = Path(path)
        is_scatter = p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower())
        if is_scatter:
            package_target = str(p.parent)
            display_name = p.parent.name
        else:
            package_target = path
            display_name = p.name

        self._path_edit.setText(package_target)
        self._current_package_path = package_target
        self._current_installed_release_info = None
        self._current_selected_rel = None
        self._details_package = None
        self._raw_release_pixmap = None

        # Auto-detect .auth file in the same folder as the chosen file
        self._auto_detect_auth_file(package_target)

        mfr_info = catalog.parse_manufacturer_rom_info(display_name)
        if mfr_info:
            det_m = mfr_info["model"]
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
            self._selected_type = None
            self._current_package_name = mfr_info["title"]
        else:
            # Detect model and variant type from original browsed filename:
            det_m, det_t = detect_model_and_type_from_name(display_name)
            if det_m:
                self._current_package_model = det_m
                self._select_model_for_manual(det_m)
                self._current_package_name = f"Installing software on {det_m}"
            else:
                self._current_package_model = ""
                self._current_package_name = "Installing software"

            if det_t:
                self._selected_type = det_t
                idx_t = self._type_combo.findData(det_t)
                if idx_t >= 0:
                    self._type_combo.setCurrentIndex(idx_t)
            else:
                self._selected_type = None

        self._details_package = catalog.FirmwarePackage(
            slug="local-" + str(Path(package_target).name),
            name=self._current_package_name or Path(package_target).name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._remember_gui_local(
            package_target, self._current_package_model, self._current_package_name
        )
        self._set_local_banner("sel_current_pkg", display_name)
        self._set_details_header(None, self._details_package)
        if is_scatter:
            self._on_local_prep_done(True, str(p.parent), "")
            return
        self._start_btn.setEnabled(False)
        self._prepare_package(package_target, self._on_local_prep_done)

    def _on_choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            tr("sel_dialog_folder_title"),
            "",
        )
        if not folder:
            return
        p = Path(folder)
        display_name = p.name

        self._path_edit.setText(folder)
        self._current_package_path = folder
        self._current_installed_release_info = None
        self._current_selected_rel = None
        self._details_package = None
        self._raw_release_pixmap = None

        # Auto-detect .auth file in the selected folder
        self._auto_detect_auth_file(folder)

        mfr_info = catalog.parse_manufacturer_rom_info(display_name)
        if mfr_info:
            det_m = mfr_info["model"]
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
            self._selected_type = None
            self._current_package_name = mfr_info["title"]
        else:
            det_m, det_t = detect_model_and_type_from_name(display_name)
            if det_m:
                self._current_package_model = det_m
                self._select_model_for_manual(det_m)
                self._current_package_name = f"Installing software on {det_m}"
            else:
                self._current_package_model = ""
                self._current_package_name = "Installing software"

            if det_t:
                self._selected_type = det_t
                idx_t = self._type_combo.findData(det_t)
                if idx_t >= 0:
                    self._type_combo.setCurrentIndex(idx_t)
            else:
                self._selected_type = None

        self._remember_gui_local(folder, self._current_package_model, self._current_package_name)
        self._set_local_banner("sel_current_pkg", display_name)
        self._details_package = catalog.FirmwarePackage(
            slug="local-" + display_name,
            name=self._current_package_name or display_name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._set_details_header(None, self._details_package)
        try:
            scatter_file = _find_scatter(p, allow_raise=True)
        except ScatterDiscoveryError:
            self._local_status_key = ""
            self._local_status.setText(tr("sel_multiple_scatters_title"))
            QMessageBox.warning(
                self,
                tr("sel_multiple_scatters_title"),
                tr("sel_multiple_scatters_error"),
            )
            self._start_btn.setEnabled(False)
            return

        if scatter_file is not None:
            self._on_local_prep_done(True, str(scatter_file.parent), "")
            return

        self._start_btn.setEnabled(False)
        self._on_local_prep_done(False, "", "NO_SCATTER_FILE")

    def _on_start_flash(self):
        if self._installer_mode and not self._installer_da:
            QMessageBox.warning(self, tr("mtk_da_label"), tr("mtk_da_required"))
            return
        if self._current_package_path:
            eff_model = getattr(self, "_current_package_model", "")
            eff_type = getattr(self, "_selected_type", None)
            if self._should_prompt_pre_install():
                if not self._prompt_pre_install(eff_model, eff_type):
                    return
            self._emit_ready()

    def _emit_ready(self):
        self.package_selected.emit(
            self._current_package_path,
            self._current_package_name,
            getattr(self, "_current_package_model", ""),
        )

    def _on_release_clicked(self, item):
        """A click only records which package the desktop tool should use.

        It does not download or start an install. Install / Restore starts the
        guided flow. The sidebar tool button downloads this package later.
        """
        from ..sp_flash_gui import is_sp_flash_gui_supported

        if not is_sp_flash_gui_supported() or item is None:
            return
        rel = item.data(Qt.UserRole)
        target = self._firmware_target_for_release(rel)
        if not target:
            return
        self._gui_explicit = target

    def _on_release_double_clicked(self, _item):
        from ..sp_flash_gui import is_sp_flash_gui_supported

        if is_sp_flash_gui_supported():
            return
        self._on_install()

    def _remember_gui_local(self, path: str, model: str, name: str) -> None:
        from ..sp_flash_gui import is_gui_local_package, is_sp_flash_gui_supported

        if not is_sp_flash_gui_supported() or not is_gui_local_package(path):
            return
        self._gui_explicit = {
            "kind": "local",
            "path": path,
            "model": model or "",
            "name": name or Path(path).name,
        }

    def _stop_gui_handoff_workers(self) -> None:
        """Drop a handoff download so Install / Restore can run on its own."""
        self._external_tool_callback = None
        if not getattr(self, "_gui_handoff_worker", False):
            return
        worker = self._download_worker
        if worker is not None:
            try:
                worker.finished.disconnect()
            except Exception:
                pass
            try:
                worker.progress.disconnect()
            except Exception:
                pass
            try:
                worker.status.disconnect()
            except Exception:
                pass
            worker.cancel()
            retire_worker(worker, self._active_workers)
            self._download_worker = None
        prep = self._prep_worker
        if prep is not None:
            prep.cancel()
            retire_worker(prep, self._active_workers)
        self._gui_handoff_worker = False

    def explicit_firmware(self) -> Optional[dict]:
        """A release the user clicked, or a qualifying file they browsed to."""
        target = getattr(self, "_gui_explicit", None)
        if not target:
            return None
        if target.get("kind") == "local":
            from ..sp_flash_gui import is_gui_local_package

            if not is_gui_local_package(target.get("path") or ""):
                return None
        return target

    def _firmware_target_for_release(self, rel) -> Optional[dict]:
        if not rel or not rel.get("download_url"):
            return None
        pkg = self.current_package()
        dest_dir = downloads.downloads_dir()
        fname = Path(rel.get("asset_name") or "rom.zip").name
        slug = getattr(pkg, "slug", None) or "firmware"
        dest = dest_dir / f"{slug}_{rel.get('tag_name', 'latest')}_{fname}"
        orig_name = rel.get("asset_name") or rel.get("download_url") or ""
        det_m, _det_t = detect_model_and_type_from_name(orig_name)
        model = det_m or (getattr(pkg, "device", "") if pkg is not None else "") or self.current_model()
        return {
            "kind": "online",
            "path": str(dest),
            "dest": dest,
            "url": rel["download_url"],
            "model": model or "",
            "name": self._install_card_title(
                pkg.name if pkg is not None and getattr(pkg, "name", "") else "",
                rel,
                model,
            ),
            "release": rel,
        }

    def focused_firmware(self) -> Optional[dict]:
        """The catalogue release highlighted on screen.

        That is the default row (usually the latest Original Software) until
        the user moves the highlight. A clicked release and a browsed local
        package are tracked separately.
        """
        if is_generic_mtk() or self.catalogue_section() == "local":
            return None
        return self._firmware_target_for_release(getattr(self, "_current_selected_rel", None))

    def begin_external_prepare(self, target: dict, callback) -> bool:
        """Download or extract ``target``, then ``callback(ok, extract_dir, err)``.

        The work runs on a worker thread. Progress uses the same signals as an
        install download so the progress card updates and the window stays live.
        """
        if not target:
            return False
        self._external_tool_callback = callback
        self._gui_handoff_worker = True
        kind = target.get("kind")
        if kind == "local":
            path = target.get("path") or ""
            if not path:
                self._external_tool_callback = None
                self._gui_handoff_worker = False
                return False
            self._current_package_path = path
            self._current_package_model = target.get("model") or ""
            self._current_package_name = target.get("name") or Path(path).name
            p = Path(path)
            is_scatter = p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower())
            sc = p if is_scatter else (_find_scatter(p, allow_raise=False) if p.is_dir() else None)
            if sc:
                cb = self._external_tool_callback
                self._external_tool_callback = None
                self._gui_handoff_worker = False
                if cb:
                    cb(True, str(sc.parent), "")
                return True
            self._prepare_package(path, self._on_external_prep_done)
            return True
        if kind != "online":
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            return False
        dest = Path(target["dest"])
        extract_dir = compute_extract_dir(dest)
        from ..flash_service import completed_extract_dir

        already = completed_extract_dir(dest)
        if already and _is_extract_complete(already):
            cb = self._external_tool_callback
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            cb(True, str(already), "")
            return True
        if dest.is_file() and dest.stat().st_size > 0 and _is_extract_complete(extract_dir):
            cb = self._external_tool_callback
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            cb(True, str(extract_dir), "")
            return True
        self._current_package_model = target.get("model") or ""
        self._current_package_name = target.get("name") or dest.name
        if dest.is_file() and dest.stat().st_size > 0:
            self._current_package_path = str(dest)
            self._prepare_package(str(dest), self._on_external_prep_done)
            return True
        self.download_started.emit(self._current_package_name, self._current_package_model)
        if self._download_worker is not None and self._download_worker.isRunning():
            self._download_worker.cancel()
            retire_worker(self._download_worker, self._active_workers)
        self._download_worker = downloads.DownloadWorker(target["url"], str(dest))
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.status.connect(self._on_download_status)
        self._download_worker.finished.connect(self._on_external_download_done)
        self._download_worker.start()
        return True

    def _on_external_download_done(self, ok, result):
        self._download_bar.setVisible(False)
        self.download_finished.emit(ok, str(result))
        if not ok:
            cb = getattr(self, "_external_tool_callback", None)
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            if cb:
                cb(False, "", str(result))
            return
        self._current_package_path = str(result)
        self._prepare_package(str(result), self._on_external_prep_done)

    def _on_external_prep_done(self, ok, extract_dir, err):
        cb = getattr(self, "_external_tool_callback", None)
        self._external_tool_callback = None
        self._gui_handoff_worker = False
        if cb:
            cb(bool(ok), str(extract_dir or ""), str(err or ""))

    def _package_for_selection(self):
        try:
            pkgs = catalog.packages_for_model_software(
                self.current_model(), self.current_software()
            )
        except Exception:
            return None
        return pkgs[0] if pkgs else None

    def _set_details_header(self, rel, package) -> None:
        """Icon, software name, and the same version string as the release list."""
        self._details_package = package
        if not rel:
            self._details_header.setVisible(False)
            self._details_name.setText("")
            self._details_version.setText("")
            return
        raw_name = package.name if package is not None and getattr(package, "name", "") else self.current_software()
        name = display_software_name(raw_name)
        version = catalog.parse_clean_release_tag_label(
            rel,
            prefer_240p=self.release_listing_filters()[2],
            version_only=True,
        )
        self._details_name.setText(name or "")
        self._details_version.setText(version or "")
        from .surfaces import seal_updating_text
        seal_updating_text(self._details_name)
        seal_updating_text(self._details_version)
        from .dark import is_dark
        from .release_icon import cached_icon_pixmap
        from ..release_icons import release_asset_urls, software_icon_urls

        dark = bool(is_dark())
        release_pm = cached_icon_pixmap(release_asset_urls(rel, package, dark=dark))
        if not release_pm.isNull():
            self._present_details_icon(release_pm, immediate=not getattr(self, "_icon_presented", False))
            self._icon_phase = "release"
        else:
            software_pm = cached_icon_pixmap(software_icon_urls(package, dark=dark))
            self._present_details_icon(
                software_pm, immediate=not getattr(self, "_icon_presented", False),
            )
            self._icon_phase = "software" if not software_pm.isNull() else "placeholder"
            if software_pm.isNull() and software_icon_urls(package, dark=dark):
                self._fetch_software_icon(package, dark)
        self._details_header.setVisible(True)
        self._ensure_release_icon(rel)

    def release_pixmap(self):
        """The release or software image already on screen, before the squircle paint."""
        from PySide6.QtGui import QPixmap

        if self.catalogue_section() == "local" or getattr(self, "_current_selected_rel", None) is None:
            pkg_path = getattr(self, "_current_package_path", None)
            if pkg_path:
                from ..release_icons import load_local_package_icon_bytes
                from .release_icon import pixmap_from_icon_bytes
                from .dark import is_dark

                data = load_local_package_icon_bytes(pkg_path, dark=bool(is_dark()))
                if data:
                    pix = pixmap_from_icon_bytes(data)
                    if not pix.isNull():
                        return pix
            return QPixmap()
        pix = getattr(self, "_raw_release_pixmap", None)
        return pix if pix is not None else QPixmap()

    def _present_details_icon(self, raw, *, immediate: bool) -> None:
        from .release_icon import squircle_pixmap
        from PySide6.QtGui import QPixmap

        source = raw if raw is not None and not getattr(raw, "isNull", lambda: True)() else QPixmap()
        if not source.isNull() and getattr(self, "_current_selected_rel", None) is not None:
            self._raw_release_pixmap = source
        else:
            self._raw_release_pixmap = None
        painted = squircle_pixmap(source, 40, complete=False)
        if immediate or not getattr(self, "_icon_presented", False):
            self._details_icon.show_stand_in(painted)
            self._icon_presented = True
        else:
            self._details_icon.cross_fade_to(painted)

    def _fetch_software_icon(self, package, dark: bool) -> None:
        from ..release_icons import software_icon_urls
        from .release_icon import SoftwareIconJob

        urls = tuple(software_icon_urls(package, dark=dark))
        if not urls or getattr(self, "_software_icon_urls", None) == urls:
            return
        self._software_icon_urls = urls
        previous = getattr(self, "_software_job", None)
        if previous is not None:
            previous.requestInterruption()
        job = SoftwareIconJob(package, dark=dark)
        job.ready.connect(self._on_software_icon_ready)
        job.finished.connect(job.deleteLater)
        self._software_job = job
        job.start()

    def _on_software_icon_ready(self, data: bytes) -> None:
        if getattr(self, "_icon_phase", "") == "release":
            return
        from .release_icon import pixmap_from_icon_bytes

        pixmap = pixmap_from_icon_bytes(data)
        if pixmap.isNull():
            return
        self._present_details_icon(pixmap, immediate=False)
        self._icon_phase = "software"
        self.release_icon_ready.emit(pixmap)

    def _prefetch_release_icons(self, releases) -> None:
        """Start icon downloads for the visible list. The selected tag goes first."""
        rel = getattr(self, "_current_selected_rel", None) or (releases[0] if releases else None)
        tag = str((rel or {}).get("tag_name") or "")
        self._start_icon_job(releases, priority_tag=tag)

    def _ensure_release_icon(self, rel) -> None:
        """Keep the selected release at the front of the paced preload."""
        if not rel:
            return
        tag = str(rel.get("tag_name") or "")
        running = getattr(self, "_icon_job", None)
        if running is not None and running.isRunning():
            running.promote(tag)
            return
        listed = list(getattr(self, "_listed_releases", None) or [])
        self._start_icon_job(listed or [rel], priority_tag=tag)

    def _start_icon_job(self, releases, priority_tag: str = "") -> None:
        from .dark import is_dark
        from .release_icon import ReleaseIconJob

        previous = getattr(self, "_icon_job", None)
        if previous is not None:
            previous.requestInterruption()
            try:
                previous.ready.disconnect()
            except Exception:
                pass
            previous.finished.connect(previous.deleteLater)
        self._icon_gen = int(getattr(self, "_icon_gen", 0)) + 1
        generation = self._icon_gen
        package = getattr(self, "_details_package", None) or self._package_for_selection()
        repo = getattr(package, "repo", "") or ""
        if repo:
            for rel in releases or []:
                if isinstance(rel, dict) and not rel.get("source_repo"):
                    rel["source_repo"] = repo
        job = ReleaseIconJob(
            releases,
            package,
            dark=bool(is_dark()),
            priority_tag=priority_tag,
            cache_dir=None,
            parent=self,
        )
        job._generation = generation
        # A page method queues onto the UI thread. A lambda can run in the
        # worker and crash when it touches widgets.
        job.ready.connect(self._on_icon_bytes)
        job.finished.connect(lambda: self._clear_icon_job(job))
        job.finished.connect(job.deleteLater)
        self._icon_job = job
        job.start()

    def _clear_icon_job(self, job) -> None:
        """Drop the job reference once it finishes (before deleteLater)."""
        if getattr(self, "_icon_job", None) is job:
            self._icon_job = None

    def _on_icon_bytes(self, tag: str, data: bytes) -> None:
        job = self.sender()
        generation = getattr(job, "_generation", None)
        self._on_release_icon_ready(tag, data, generation)

    def _on_release_icon_ready(self, tag: str, data: bytes, generation: int) -> None:
        if generation != getattr(self, "_icon_gen", None):
            return
        rel = getattr(self, "_current_selected_rel", None)
        if not rel or str(rel.get("tag_name") or "") != str(tag or ""):
            return
        if not data:
            return
        from .release_icon import pixmap_from_icon_bytes

        pixmap = pixmap_from_icon_bytes(data)
        if pixmap.isNull():
            return
        self._present_details_icon(pixmap, immediate=False)
        self._icon_phase = "release"
        self.release_icon_ready.emit(pixmap)

    def _install_card_title(self, software, rel, model) -> str:
        """Progress-card title: software, the list's version string, and model."""
        version = ""
        if rel:
            version = catalog.parse_clean_release_tag_label(
                rel,
                prefer_240p=self.release_listing_filters()[2],
                version_only=True,
            )
        return catalog.format_install_card_title(software, version, model)

    def current_model(self):
        return self._model_combo.currentText()

    def current_software(self):
        """Canonical English software name behind the selected row.

        The dropdown shows preset translations, so a row's text is not a
        lookup key; the canonical name lives in the item data instead.
        """
        idx = self._software_combo.currentIndex()
        if idx < 0:
            return self._software_combo.currentText()
        return str(self._software_combo.itemData(idx) or self._software_combo.itemText(idx))

    def current_package(self):
        model = self.current_model()
        software = self.current_software()
        pkgs = catalog.packages_for_model_software(model, software)
        return pkgs[0] if pkgs else None

    def get_package_path(self):
        return self._current_package_path

    def set_package_path(self, path):
        self._current_package_path = path
        if path:
            self._current_package_name = Path(path).name

    def current_installed_release_info(self):
        """Return release details dict if an online release was prepared for installation."""
        return getattr(self, "_current_installed_release_info", None)

    def catalogue_latest_tag(self) -> str:
        """Newest tag in the list that was last shown, used as a downgrade ceiling."""
        releases = list(getattr(self, "_listed_releases", None) or [])
        if not releases:
            return ""
        newest = max(releases, key=catalog.release_sort_key)
        return newest.get("tag_name") or ""

    def navigate_to_package(self, model: str, software_name: str, tag_name=None):
        """Switch to the catalogue, select specified model/software, highlight tag_name."""
        self._show_online_view()
        m_idx = self._model_combo.findText(model)
        if m_idx >= 0 and m_idx != self._model_combo.currentIndex():
            self._model_combo.setCurrentIndex(m_idx)
        sw_idx = self._find_software_index(software_name)
        if sw_idx >= 0 and sw_idx != self._software_combo.currentIndex():
            self._software_combo.setCurrentIndex(sw_idx)
        if tag_name:
            self._target_tag_to_select = tag_name
            # If releases already loaded, select right now
            for row in range(self._release_list.count()):
                it = self._release_list.item(row)
                r = it.data(Qt.UserRole)
                if r and r.get("tag_name") == tag_name:
                    self._release_list.setCurrentRow(row)
                    self._target_tag_to_select = None
                    break

    def start_install_for_release(
        self,
        model: str,
        software_name: str,
        tag_name: str = "",
        release: Optional[dict] = None,
        package: Optional[catalog.FirmwarePackage] = None,
    ):
        """Navigate to package/release and initiate installation with current method."""
        self.navigate_to_package(model, software_name, tag_name=tag_name)
        if release:
            self._trigger_release_install(release, package=package)
        else:
            item = self._release_list.currentItem()
            if item:
                r = item.data(Qt.UserRole)
                if r and (not tag_name or r.get("tag_name") == tag_name):
                    self._trigger_release_install(r, package=package)
                    return
            self._auto_install_tag = tag_name
