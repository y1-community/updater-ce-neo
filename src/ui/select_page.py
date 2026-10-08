"""Select package page — online firmware listing + local file picker."""

import logging
import os
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, downloads, device_tracking
from ..network import get_connectivity_monitor
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
)
from ..i18n import tr, translator
from ..browser import open_browser
from ..translate import (
    get_google_translate_release_url,
    ReleaseTranslateWorker,
)
from .widgets import Banner, Card
from .dark import T, page_top_margin

logger = logging.getLogger(__name__)


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


class SelectPackagePage(QWidget):
    package_selected = Signal(str, str, str)
    # (message, timeout_ms) -> shown in the main window's status bar.
    status_message = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = catalog.ReleasesClient()
        self._active_workers = set()
        self._releases_worker = None
        self._download_worker = None
        self._current_package_path = ""
        self._current_package_name = ""
        self._current_package_model = ""
        self._online_banner_key = ""
        self._online_banner_count = 0
        self._local_banner_key = ""
        self._local_banner_arg = ""
        self._download_status_key = ""
        self._local_status_key = ""
        self._prep_worker = None
        self._selected_type = None  # None = all types; 'A' or 'B' for filtered
        self._current_selected_rel = None
        self._is_translated = False
        self._is_translating = False
        self._translated_notes_cache = {}
        self._translate_worker = None
        self._build_ui()
        self._on_model_changed()

        self._monitor = get_connectivity_monitor()
        self._monitor.connectivity_changed.connect(self._on_connectivity_changed)
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            self._monitor.start_monitoring(interval_ms=5000)
            if self._monitor.is_online is False:
                self.set_online_mode(False)

        if is_generic_mtk():
            self.apply_generic_mode(True)

    def apply_generic_mode(self, generic: bool):
        if generic:
            self._tabs.tabBar().setVisible(False)
            self._tabs.setCurrentWidget(self._local_tab)
            self._hint.setText(tr("sel_only_local_generic"))
            self._offline_banner.setText(tr("sel_offline_install_generic"))
        else:
            self._tabs.tabBar().setVisible(True)
            self._hint.setText(tr("sel_only_local"))
            self._offline_banner.setText(tr("sel_offline_install"))

    def _on_connectivity_changed(self, is_online: bool):
        if is_generic_mtk():
            return
        self.set_online_mode(is_online)

    def set_online_mode(self, is_online: bool):
        if is_generic_mtk() and is_online:
            return
        online_idx = self._tabs.indexOf(self._online_tab)
        if not is_online:
            if online_idx >= 0:
                self._tabs.removeTab(online_idx)
                self._tabs.setCurrentWidget(self._local_tab)
                self._say(tr("sel_offline"), timeout_ms=3000)
        else:
            if online_idx < 0:
                self._tabs.insertTab(0, self._online_tab, tr("sel_online"))
                self._tabs.setCurrentWidget(self._online_tab)
                self._on_model_changed()

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, page_top_margin(), 24, 20)
        layout.setSpacing(8)

        self._title = QLabel(tr("sel_title"))
        self._title.setProperty("cssClass", "pageTitle")
        layout.addWidget(self._title)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._online_tab = self._build_online_tab()
        self._local_tab = self._build_local_tab()
        self._tabs.addTab(self._online_tab, tr("sel_online"))
        self._tabs.addTab(self._local_tab, tr("sel_local"))
        self._tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self._tabs, 1)

    def _on_tab_changed(self, _idx: int):
        pass

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

        # Row 0: Device Model & Device Type
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

        type_row = QHBoxLayout()
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(6)
        self._type_combo = QComboBox()
        self._type_combo.addItem(tr("sel_type_a"), "A")
        self._type_combo.addItem(tr("sel_type_b"), "B")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo)

        self._type_help_btn = QPushButton(tr("sel_type_help_btn"))
        self._type_help_btn.setProperty("cssClass", "ghost")
        self._type_help_btn.setToolTip(tr("sel_type_help_body").replace("\n\n", " "))
        self._type_help_btn.clicked.connect(self._show_device_type_help)
        type_row.addWidget(self._type_help_btn)
        filter_layout.addLayout(type_row, 0, 3)

        # Row 1: Software & Refresh
        self._software_label = QLabel(f"{tr('sel_software')}:")
        self._software_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._software_label, 1, 0)

        self._software_combo = QComboBox()
        self._software_combo.setMinimumWidth(120)
        self._software_combo.currentTextChanged.connect(self._on_software_changed)
        filter_layout.addWidget(self._software_combo, 1, 1, 1, 2)

        self._refresh_btn = QPushButton(tr("sel_refresh"))
        self._refresh_btn.setProperty("cssClass", "ghost")
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

        # ── Split Content: Left = Packages + Install; Right = Status + Notes ──
        split = QHBoxLayout()
        split.setSpacing(10)

        # Left panel: Available System Software
        self._pkg_group = Card("sel_available_software")
        pkg_layout = QVBoxLayout()
        pkg_layout.setContentsMargins(0, 0, 0, 0)
        pkg_layout.setSpacing(6)

        self._release_list = QListWidget()
        self._release_list.currentItemChanged.connect(self._on_release_selected)
        pkg_layout.addWidget(self._release_list)

        # Slim progress bar, only visible while downloading / preparing.
        self._download_bar = QProgressBar()
        self._download_bar.setTextVisible(False)
        self._download_bar.setFixedHeight(8)
        self._download_bar.setVisible(False)
        pkg_layout.addWidget(self._download_bar)

        self._install_btn = QPushButton(tr("sel_install"))
        self._install_btn.setProperty("cssClass", "primary")
        self._install_btn.setDefault(True)
        self._install_btn.setAutoDefault(True)
        self._install_btn.setMinimumHeight(36)
        self._install_btn.setEnabled(False)
        self._install_btn.setToolTip(tr("sel_install_tooltip_generic"))
        self._install_btn.clicked.connect(self._on_install)
        pkg_layout.addWidget(self._install_btn)

        self._pkg_group.set_layout(pkg_layout)
        split.addWidget(self._pkg_group, 5)

        # Right panel: Status and Notes
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._notes_group = Card("sel_about_update")
        notes_l = QVBoxLayout()
        notes_l.setContentsMargins(0, 0, 0, 0)
        notes_l.setSpacing(4)

        self._notes = QTextBrowser()
        self._notes.setObjectName("releaseNotes")
        self._notes.setReadOnly(True)
        self._notes.setOpenLinks(False)
        self._notes.setOpenExternalLinks(False)
        self._notes.viewport().setAutoFillBackground(False)
        self._notes.setFrameShape(QFrame.NoFrame)
        self._notes.setLineWrapMode(QTextBrowser.WidgetWidth)
        self._notes.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._notes.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        from .dark import apply_native_scrollbar_policy
        apply_native_scrollbar_policy(self._notes)
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        self._notes.setStyleSheet(
            "QTextBrowser#releaseNotes { background: transparent; border: none; padding: 0px; }"
        )
        self._notes.anchorClicked.connect(self._on_notes_link_clicked)
        notes_l.addWidget(self._notes)

        self._translate_label = QLabel()
        self._translate_label.setObjectName("translateReleaseNotes")
        self._translate_label.setWordWrap(True)
        self._translate_label.setAlignment(Qt.AlignCenter)
        t = T()
        self._translate_label.setStyleSheet(
            f"color: {t.fg_dim}; font-size: 11px; margin-top: 4px;"
        )
        self._translate_label.setTextFormat(Qt.RichText)
        self._translate_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._translate_label.linkActivated.connect(self._on_translate_link_clicked)
        self._translate_label.setVisible(False)
        notes_l.addWidget(self._translate_label)

        self._notes_group.set_layout(notes_l)
        right_layout.addWidget(self._notes_group, 1)

        split.addWidget(right_panel, 6)
        layout.addLayout(split, 1)
        return page

    def _build_local_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
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
        self._browse_btn.setProperty("cssClass", "ghost")
        self._browse_btn.clicked.connect(self._on_choose_file)
        self._browse_folder_btn = QPushButton(tr("sel_browse_folder"))
        self._browse_folder_btn.setProperty("cssClass", "ghost")
        self._browse_folder_btn.clicked.connect(self._on_choose_folder)
        row.addWidget(self._path_edit, 1)
        row.addWidget(self._browse_btn)
        row.addWidget(self._browse_folder_btn)
        grp_l.addLayout(row)

        self._local_banner = Banner()
        grp_l.addWidget(self._local_banner)

        self._local_bar = QProgressBar()
        self._local_bar.setVisible(False)
        grp_l.addWidget(self._local_bar)

        self._local_status = QLabel("")
        self._local_status.setStyleSheet(
            f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        grp_l.addWidget(self._local_status)

        self._start_btn = QPushButton(tr("sel_btn_start"))
        self._start_btn.setProperty("cssClass", "primary")
        self._start_btn.setDefault(True)
        self._start_btn.setAutoDefault(True)
        self._start_btn.setMinimumHeight(36)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_flash)
        grp_l.addWidget(self._start_btn)

        local_group.set_layout(grp_l)
        layout.addWidget(local_group)
        layout.addStretch()
        return page

    def _show_device_type_help(self):
        QMessageBox.information(
            self,
            tr("sel_type_help_title"),
            tr("sel_type_help_body"),
        )

    def _should_prompt_pre_install(self) -> bool:
        """On Linux with SP Flash Tool methods, step 1 (ensuring USB cable is removed
        and device powered off) is naturally presented after silent system prep and privilege
        escalation, when flash_tool fires the line beginning with 'search usb'.
        On Windows or with MTKClient, prompt upfront before starting.
        """
        import sys
        from PySide6.QtCore import QSettings
        from ..paths import IS_MAC, IS_WINDOWS
        if IS_WINDOWS or IS_MAC:
            return True
        if not sys.platform.startswith("linux"):
            return True
        settings = QSettings("Innioasis", "UpdaterCE")
        method = settings.value("flash_method", "auto")
        if method == "mtk":
            return True
        return False

    def _prompt_pre_install(self, model="", type_variant=None):
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("INNIOASIS_HEADLESS"):
            return True
        reply = QMessageBox.question(
            self,
            tr("sel_pre_install_title"),
            install_disconnect_guidance(model, type_variant),
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Ok,
        )
        return reply == QMessageBox.Ok

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

    def retranslate(self):
        self._title.setText(tr("sel_title"))
        online_idx = self._tabs.indexOf(self._online_tab)
        if online_idx >= 0:
            self._tabs.setTabText(online_idx, tr("sel_online"))
        local_idx = self._tabs.indexOf(self._local_tab)
        if local_idx >= 0:
            self._tabs.setTabText(local_idx, tr("sel_local"))
        self._model_label.setText(f"{tr('sel_model')}:")
        self._type_label.setText(f"{tr('sel_type')}:")
        self._software_label.setText(f"{tr('sel_software')}:")
        self._refresh_btn.setText(tr("sel_refresh"))
        self._install_btn.setText(tr("sel_install"))
        if is_generic_mtk():
            self._hint.setText(tr("sel_only_local_generic"))
            self._offline_banner.setText(tr("sel_offline_install_generic"))
        else:
            self._hint.setText(tr("sel_only_local"))
            self._offline_banner.retranslate()
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        self._browse_btn.setText(tr("sel_browse"))
        if hasattr(self, "_browse_folder_btn"):
            self._browse_folder_btn.setText(tr("sel_browse_folder"))
        self._start_btn.setText(tr("sel_btn_start"))
        self._apply_online_banner()
        self._apply_local_banner()
        self._type_combo.setItemText(0, tr("sel_type_a"))
        self._type_combo.setItemText(1, tr("sel_type_b"))
        self._type_help_btn.setText(tr("sel_type_help_btn"))
        self._type_help_btn.setToolTip(tr("sel_type_help_body").replace("\n\n", " "))
        self._refresh_btn.setToolTip(tr("sel_refresh_tooltip"))
        self._pkg_group.setTitle(tr("sel_available_software"))
        self._local_group.setTitle(tr("sel_choose_firmware_pkg"))
        self._update_device_status_copy()
        if self._download_status_key:
            self._say(tr(self._download_status_key))
        if self._local_status_key:
            self._local_status.setText(tr(self._local_status_key))
        self._notes_group.setTitle(tr("sel_about_update"))
        if self._current_selected_rel:
            if self._is_translated:
                self._translate_current_release_in_app()
            else:
                self._notes.setHtml(self._render_release_notes(self._current_selected_rel, as_html=True))
        self._update_translate_link()

    def refresh_theme(self):
        """Update components and release notes typography to match active theme tokens."""
        if hasattr(self, "_notes") and self._current_selected_rel:
            self._notes.setHtml(self._render_release_notes(self._current_selected_rel, as_html=True))
        if hasattr(self, "_translate_label"):
            self._update_translate_link()

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
        if hasattr(self, "_type_help_btn"):
            self._type_help_btn.setVisible(has_types)
        if not has_types:
            self._selected_type = "A"
        else:
            self._selected_type = self._type_combo.currentData() or "A"
        names = catalog.software_names_for_model(model)
        self._software_combo.blockSignals(True)
        self._software_combo.clear()
        self._software_combo.addItems(names)
        self._software_combo.blockSignals(False)
        self._update_device_status_copy()
        self._on_software_changed()

    def _update_device_status_copy(self):
        model = self.current_model()
        eff_type = self._selected_type if model.upper() == "Y1" else None
        lbl = device_label_for_model(model, eff_type)
        if hasattr(self, "_install_btn"):
            self._install_btn.setToolTip(tr("sel_install_tooltip").format(lbl=lbl))

    def is_rockbox_y1_browsing(self) -> bool:
        """True when the Rockbox package for Y1 is the one being browsed."""
        software = (self.current_software() or self._software_combo.currentText()).lower()
        return self.current_model().upper() == "Y1" and "rockbox" in software

    def release_listing_filters(self) -> tuple:
        """``(show_old, show_nightly, prefer_240p)`` for the current browse.

        The Settings toggles only take effect for Rockbox releases on Y1.
        """
        if not self.is_rockbox_y1_browsing():
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
            current_sw = self._software_combo.currentText()
            names = catalog.software_names_for_model(self.current_model())
            existing = [self._software_combo.itemText(i) for i in range(self._software_combo.count())]
            if names != existing:
                self._software_combo.blockSignals(True)
                self._software_combo.clear()
                self._software_combo.addItems(names)
                idx = self._software_combo.findText(current_sw)
                if idx >= 0:
                    self._software_combo.setCurrentIndex(idx)
                self._software_combo.blockSignals(False)

    def _on_releases_loaded(self, releases, error):
        if error:
            self._set_online_banner("sel_offline")
            return
        self._release_list.clear()
        releases = sorted(releases or [], key=catalog.release_sort_key, reverse=True)
        prefer_240p = self.release_listing_filters()[2]
        install_rec = None
        try:
            from .. import device_tracking
            curr_model = self.current_model() or "Y1"
            curr_settings = getattr(self, "settings", None)
            install_rec = device_tracking.get_device_install(curr_model, settings=curr_settings)
        except Exception:
            pass
        installed_tag = (install_rec.get("tag_name") or "") if install_rec else ""
        installed_sw = (install_rec.get("software_name") or "").lower() if install_rec else ""
        curr_sw = (self.current_software() or "").lower()
        is_same_sw = (not curr_sw) or (not installed_sw) or (installed_sw == curr_sw) or (installed_sw in curr_sw) or (curr_sw in installed_sw)

        for rel in releases:
            label = catalog.format_release_display_label(rel, prefer_240p=prefer_240p)
            tag = rel.get("tag_name", "")
            is_installed = bool(installed_tag and is_same_sw and tag == installed_tag)

            if is_installed:
                display_label = f"● {label}  ({tr('installed_badge')})"
            else:
                display_label = label

            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, rel)
            if is_installed:
                f = item.font()
                f.setBold(True)
                item.setFont(f)

            tooltip = self._asset_line(rel)
            if tag:
                tooltip = f"{tag}\n{tooltip}".strip()
            if is_installed:
                tooltip = f"[{tr('installed_badge')}] {tooltip}"
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

    def _asset_line(self, rel):
        assets = ", ".join(
            v["asset"].get("name", "") for v in (rel.get("rom_variants") or [])
        )
        return assets or ""

    def _on_release_selected(self, current, _prev):
        self._install_btn.setEnabled(current is not None)
        if current is None:
            self._current_selected_rel = None
            self._is_translated = False
            self._is_translating = False
            self._notes.clear()
            self._update_translate_link()
            return
        rel = current.data(Qt.UserRole)
        if rel:
            self._current_selected_rel = rel
            self._is_translated = False
            self._is_translating = False
            self._notes.setHtml(self._render_release_notes(rel, as_html=True))
            self._update_translate_link()

    def _render_release_notes(self, rel, translated_body=None, translated_name=None, as_html: bool = False) -> str:
        tag = rel.get("tag_name", "")
        name = translated_name or rel.get("name", "") or tag
        date = (rel.get("published_at") or "")[:10]
        body = translated_body or (rel.get("body") or "").strip() or tr("update_no_notes")
        assets = self._asset_line(rel)
        meta = []
        if tag:
            meta.append(f"`{tag}`")
        if date:
            meta.append(date)
        if assets:
            meta.append(f"{tr('sel_release_assets')}: {assets}")
        if translated_body:
            meta.append(f"*{tr('translated_with_google')}*")
        lines = [f"## {name}"]
        if meta:
            lines.append("")
            lines.append(" \u00b7 ".join(meta))
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(body[:4000])
        raw_md = "\n".join(lines)

        # 1. Parse markdown & remove images per user request:
        # Strip all markdown images ![alt](url)
        cleaned_md = re.sub(r"!\[.*?\]\(.*?\)", "", raw_md)
        # Strip HTML images <img ...>
        cleaned_md = re.sub(r"<img[^>]*>", "", cleaned_md, flags=re.IGNORECASE)
        # Strip empty link anchors left over from image-only links: [ ](url)
        cleaned_md = re.sub(r"\[\s*\]\(.*?\)", "", cleaned_md)

        if not as_html:
            return cleaned_md

        doc = QTextDocument()
        doc.setMarkdown(cleaned_md)
        html = doc.toHtml()

        t = T()
        # 2. Re-style links: clickable, bold, same color as text (not blue #0000ff):
        html = re.sub(
            r'<span style="[^"]*color:#0000ff;[^"]*">',
            f'<span style="color: {t.fg}; font-weight: bold; text-decoration: underline;">',
            html,
        )
        html = re.sub(
            r'<a\s+href="([^"]+)">',
            f'<a href="\\1" style="color: {t.fg}; font-weight: bold; text-decoration: underline;">',
            html,
        )
        html = re.sub(r"<img[^>]*>", "", html, flags=re.IGNORECASE)

        # 3. Inject native rich typography CSS:
        css = f"""
        <style type="text/css">
        body {{
            color: {t.fg};
            font-size: 13px;
            line-height: 1.45;
            background: transparent;
            margin: 0;
            padding: 0;
        }}
        h1, h2, h3, h4 {{
            color: {t.fg};
            font-weight: 700;
            margin-top: 4px;
            margin-bottom: 4px;
        }}
        h1 {{ font-size: 16px; }}
        h2 {{ font-size: 15px; }}
        h3 {{ font-size: 13px; }}
        p {{
            margin-top: 4px;
            margin-bottom: 6px;
            color: {t.fg};
        }}
        ul, ol {{
            margin-top: 4px;
            margin-bottom: 6px;
            padding-left: 18px;
        }}
        li {{
            margin-bottom: 2px;
            color: {t.fg};
        }}
        hr {{
            border: none;
            border-top: 1px solid {t.border};
            margin: 8px 0;
        }}
        code {{
            font-family: Menlo, Monaco, Consolas, "Cascadia Code", "Courier New", monospace;
            font-size: 12px;
            background-color: {t.bg_hover};
            color: {t.fg};
            padding: 1px 4px;
            border-radius: 3px;
        }}
        pre {{
            font-family: Menlo, Monaco, Consolas, "Cascadia Code", "Courier New", monospace;
            font-size: 12px;
            background-color: {t.bg_hover};
            color: {t.fg};
            padding: 6px 8px;
            border-radius: 4px;
            margin: 4px 0;
        }}
        a {{
            color: {t.fg};
            font-weight: bold;
            text-decoration: underline;
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
            return

        t = T()
        url = get_google_translate_release_url(rel, lang)
        self._translate_label.setVisible(True)

        if self._is_translating:
            msg = tr("translating_notes")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'<span style="color:{t.fg_dim};">⏳ {msg} &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.accent}; text-decoration:underline;">{open_txt} ↗</a></span>'
            )
        elif self._is_translated:
            badge = tr("translated_with_google")
            show_orig = tr("show_original")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'✓ <span style="color:{t.fg_dim};">{badge}</span> &nbsp;·&nbsp; '
                f'<a href="action:show_original" style="color:{t.accent}; text-decoration:underline;">{show_orig}</a> &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg_dim}; text-decoration:underline;">{open_txt} ↗</a>'
            )
        else:
            trans_txt = tr("translate_notes")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'🌐 <a href="action:translate_in_app" style="color:{t.accent}; text-decoration:underline; font-weight:600;">{trans_txt}</a> &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg_dim}; text-decoration:underline;">{open_txt} ↗</a>'
            )

    def _on_translate_link_clicked(self, link: str):
        if link == "action:translate_in_app":
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
            self._translate_current_release_in_app()
        elif url_str == "action:show_original":
            self._show_original_release_notes()
        elif url_str.startswith(("http://", "https://", "mailto:")):
            open_browser(url_str)

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
            self._notes.setHtml(
                self._render_release_notes(rel, translated_body=trans_body, translated_name=trans_name, as_html=True)
            )
            self._update_translate_link()
            return

        self._is_translating = True
        self._update_translate_link()
        if self._translate_worker is not None and self._translate_worker.isRunning():
            self._translate_worker.wait(500)
        self._translate_worker = ReleaseTranslateWorker(rel, lang, self)
        self._translate_worker.translation_ready.connect(self._on_translation_ready)
        self._translate_worker.translation_failed.connect(self._on_translation_failed)
        self._translate_worker.start()

    def _on_translation_ready(self, rel, lang, trans_name, trans_body):
        self._translated_notes_cache[(rel.get("tag_name", ""), lang)] = (trans_name, trans_body)
        if self._current_selected_rel == rel:
            self._is_translating = False
            self._is_translated = True
            self._notes.setHtml(
                self._render_release_notes(rel, translated_body=trans_body, translated_name=trans_name, as_html=True)
            )
            self._update_translate_link()

    def _on_translation_failed(self, rel, lang, error):
        if self._current_selected_rel == rel:
            self._is_translating = False
            self._update_translate_link()

    def _show_original_release_notes(self):
        self._is_translated = False
        self._is_translating = False
        if self._current_selected_rel:
            self._notes.setHtml(self._render_release_notes(self._current_selected_rel, as_html=True))
        self._update_translate_link()

    def _trigger_release_install(self, rel, package=None):
        if not rel or not rel.get("download_url"):
            return
        pkg = package or self.current_package()
        if pkg is None:
            return

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
            self._current_package_name = f"{pkg.name} ({eff_model})"
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._download_status_key = "sel_prepare_done"
            self._say(tr("sel_prepare_done"), 8000)
            self._on_online_prep_done(True, str(extract_dir), "")
            return

        # 2. Check if package is already downloaded but needs extraction:
        if dest.is_file() and dest.stat().st_size > 0:
            logger.info("Package %s already downloaded, extracting directly", dest.name)
            self._current_package_path = str(dest)
            self._current_package_name = f"{pkg.name} ({eff_model})"
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._prepare_package(str(dest), self._on_online_prep_done)
            return

        # 3. Otherwise, start download worker
        self._download_status_key = "sel_download_start"
        self._say(tr("sel_download_start"))
        self._download_bar.setValue(0)
        self._download_bar.setVisible(True)
        self._install_btn.setEnabled(False)
        self._download_worker = downloads.DownloadWorker(rel["download_url"], str(dest))
        self._download_worker.progress.connect(self._download_bar.setValue)
        self._download_worker.status.connect(self._on_download_status)
        self._download_worker.finished.connect(self._on_download_done)
        self._download_worker.start()

    def _on_install(self):
        item = self._release_list.currentItem()
        if item is None:
            return
        rel = item.data(Qt.UserRole)
        self._trigger_release_install(rel)

    def _on_download_status(self, text: str):
        self._download_status_key = ""
        self._say(text)

    def _on_download_done(self, ok, result):
        self._download_bar.setVisible(False)
        if not ok:
            self._download_status_key = ""
            self._say(f"{tr('sel_download_failed')} \u2014 {result}", 15000)
            self._install_btn.setEnabled(True)
            return
        eff_model = getattr(self, "_current_package_model", "") or self.current_model()
        self._current_package_path = result
        self._current_package_name = f"{self.current_software()} ({eff_model})"
        self._current_package_model = eff_model
        self._current_installed_release_info = getattr(self, "_pending_install_release_info", None)
        self._prepare_package(result, self._on_online_prep_done)

    def _prepare_package(self, path, done_cb):
        if self._prep_worker is not None:
            self._prep_worker.cancel()
        self._download_status_key = "sel_preparing"
        self._say(tr("sel_preparing"))
        self._local_status_key = "sel_preparing"
        self._local_status.setText(tr("sel_preparing"))
        bar = (
            self._local_bar
            if self._tabs.currentWidget() is self._local_tab
            else self._download_bar
        )
        bar.setValue(0)
        bar.setVisible(True)
        self._prep_worker = ExtractWorker(path)
        self._prep_worker.progress.connect(bar.setValue)
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
        self._say(tr("sel_prepare_done"), 8000)

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

        scatter_file = _find_scatter(Path(extract_dir))
        scatter_abs = scatter_file.resolve() if scatter_file else None
        extract_abs = Path(extract_dir).resolve() if extract_dir else None
        eff_model = getattr(self, "_current_package_model", "")
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
        if p.is_file() and p.suffix.lower() == ".txt":
            package_target = str(p.parent)
            display_name = p.parent.name
        else:
            package_target = path
            display_name = p.name

        self._path_edit.setText(package_target)
        self._current_package_path = package_target
        self._current_package_name = display_name
        self._current_installed_release_info = None

        # Detect model and variant type from original browsed filename:
        det_m, det_t = detect_model_and_type_from_name(display_name)
        if det_m:
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
        else:
            self._current_package_model = ""

        if det_t:
            self._selected_type = det_t
            idx_t = self._type_combo.findData(det_t)
            if idx_t >= 0:
                self._type_combo.setCurrentIndex(idx_t)
        else:
            self._selected_type = None

        self._set_local_banner("sel_current_pkg", display_name)
        self._start_btn.setEnabled(False)
        self._prepare_package(package_target, self._on_local_prep_done)

    def _on_choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            tr("sel_dialog_folder_title"),
            "",
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not folder:
            return
        p = Path(folder)
        display_name = p.name

        self._path_edit.setText(folder)
        self._current_package_path = folder
        self._current_package_name = display_name
        self._current_installed_release_info = None

        det_m, det_t = detect_model_and_type_from_name(display_name)
        if det_m:
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
        else:
            self._current_package_model = ""

        if det_t:
            self._selected_type = det_t
            idx_t = self._type_combo.findData(det_t)
            if idx_t >= 0:
                self._type_combo.setCurrentIndex(idx_t)
        else:
            self._selected_type = None

        self._set_local_banner("sel_current_pkg", display_name)
        self._start_btn.setEnabled(False)
        self._prepare_package(folder, self._on_local_prep_done)

    def _on_start_flash(self):
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

    def current_model(self):
        return self._model_combo.currentText()

    def current_software(self):
        return self._software_combo.currentText()

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

    def navigate_to_package(self, model: str, software_name: str, tag_name=None):
        """Switch to Online tab, select specified model/software, and highlight tag_name."""
        self._tabs.setCurrentWidget(self._online_tab)
        m_idx = self._model_combo.findText(model)
        if m_idx >= 0 and m_idx != self._model_combo.currentIndex():
            self._model_combo.setCurrentIndex(m_idx)
        sw_idx = self._software_combo.findText(software_name)
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
