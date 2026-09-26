"""Select package page — online firmware listing + local file picker."""

import logging
import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, downloads, device_tracking
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
)
from ..i18n import tr
from .widgets import Banner, Card
from .dark import T

logger = logging.getLogger(__name__)


class ReleasesWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, client, package, model, show_nightly, selected_type=None, force_refresh=False, parent=None):
        super().__init__(parent)
        self.client = client
        self.package = package
        self.model = model
        self.show_nightly = show_nightly
        self.selected_type = selected_type
        self.force_refresh = force_refresh

    def run(self):
        try:
            releases = self.client.releases_for_package(
                self.package, self.model, self.show_nightly, self.selected_type,
                force_refresh=self.force_refresh,
            )
            self.finished.emit(releases, "")
        except Exception as e:
            logger.exception("Releases fetch failed")
            self.finished.emit([], str(e))


class SelectPackagePage(QWidget):
    package_selected = Signal(str, str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.client = catalog.ReleasesClient()
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
        self._build_ui()
        self._on_model_changed()

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        self._title = QLabel(tr("sel_title"))
        self._title.setProperty("cssClass", "pageTitle")
        layout.addWidget(self._title)

        self._tabs = QTabWidget()
        self._online_tab = self._build_online_tab()
        self._local_tab = self._build_local_tab()
        self._tabs.addTab(self._online_tab, tr("sel_online"))
        self._tabs.addTab(self._local_tab, tr("sel_local"))
        layout.addWidget(self._tabs, 1)

        self._status_card = Card("sel_status_title")
        self._status_tag_label = QLabel()
        self._status_tag_label.setProperty("cssClass", "dimmed")
        self._status_card.add_widget(self._status_tag_label)
        layout.addWidget(self._status_card)

    def _build_online_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── Device & Software Filters Group ──────────────────────────────
        self._filter_group = QGroupBox()
        filter_layout = QVBoxLayout(self._filter_group)
        filter_layout.setContentsMargins(8, 6, 8, 6)
        filter_layout.setSpacing(4)

        # Row 1: Device Type filter
        device_type_layout = QHBoxLayout()
        self._type_label = QLabel("Device Type:")
        self._type_label.setProperty("cssClass", "field-label")
        device_type_layout.addWidget(self._type_label)

        self._type_combo = QComboBox()
        self._type_combo.addItem("Type A", "A")
        self._type_combo.addItem("Type B", "B")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        device_type_layout.addWidget(self._type_combo)

        self._type_help_btn = QPushButton("Type?")
        self._type_help_btn.setToolTip(
            "Try Type A System Software first. If your scroll wheel doesn't respond after installation, "
            "install one of the Type B options."
        )
        self._type_help_btn.clicked.connect(self._show_device_type_help)
        device_type_layout.addWidget(self._type_help_btn)

        self._refresh_btn = QPushButton(tr("sel_refresh"))
        self._refresh_btn.setToolTip("Refresh available software catalogue")
        self._refresh_btn.clicked.connect(lambda: self._refresh_releases(force_refresh=True))
        device_type_layout.addWidget(self._refresh_btn)
        device_type_layout.addStretch()

        # Row 2: Device Model filter
        device_model_layout = QHBoxLayout()
        self._model_label = QLabel(tr("sel_model"))
        self._model_label.setProperty("cssClass", "field-label")
        device_model_layout.addWidget(self._model_label)

        self._model_combo = QComboBox()
        for m in DEVICE_MODELS:
            self._model_combo.addItem(m)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        device_model_layout.addWidget(self._model_combo)
        device_model_layout.addStretch()

        # Row 3: Software filter
        software_layout = QHBoxLayout()
        self._software_label = QLabel(tr("sel_software"))
        software_label_key = "sel_software"
        self._software_label.setProperty("cssClass", "field-label")
        software_layout.addWidget(self._software_label)

        self._software_combo = QComboBox()
        self._software_combo.setMinimumWidth(260)
        self._software_combo.currentTextChanged.connect(self._on_software_changed)
        software_layout.addWidget(self._software_combo)
        software_layout.addStretch()

        filter_layout.addLayout(device_type_layout)
        filter_layout.addLayout(device_model_layout)
        filter_layout.addLayout(software_layout)
        layout.addWidget(self._filter_group)

        self._online_banner = Banner()
        self._online_banner.setVisible(False)
        layout.addWidget(self._online_banner)

        # ── Split Content: Left = Packages + Install; Right = Status + Notes ──
        split = QHBoxLayout()
        split.setSpacing(10)

        # Left panel: Available System Software
        self._pkg_group = QGroupBox("Available System Software")
        pkg_layout = QVBoxLayout(self._pkg_group)
        pkg_layout.setContentsMargins(8, 8, 8, 8)
        pkg_layout.setSpacing(6)

        self._release_list = QListWidget()
        self._release_list.currentItemChanged.connect(self._on_release_selected)
        pkg_layout.addWidget(self._release_list)

        self._install_btn = QPushButton(tr("sel_install"))
        self._install_btn.setDefault(True)
        self._install_btn.setAutoDefault(True)
        self._install_btn.setEnabled(False)
        self._install_btn.setToolTip("Install or restore the selected system software to your device")
        self._install_btn.clicked.connect(self._on_install)
        pkg_layout.addWidget(self._install_btn)

        split.addWidget(self._pkg_group, 5)

        # Right panel: Status and Notes
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._status_group = QGroupBox(tr("Status"))
        status_l = QVBoxLayout(self._status_group)
        status_l.setContentsMargins(8, 6, 8, 6)
        status_l.setSpacing(4)

        self._status_label = QLabel(tr("Ready"))
        self._status_label.setWordWrap(True)
        status_l.addWidget(self._status_label)

        self._download_bar = QProgressBar()
        self._download_bar.setVisible(False)
        status_l.addWidget(self._download_bar)

        self._download_status = QLabel("")
        self._download_status.setProperty("cssClass", "dimmed")
        status_l.addWidget(self._download_status)

        right_layout.addWidget(self._status_group)

        self._notes_group = QGroupBox(tr("About this update:"))
        notes_l = QVBoxLayout(self._notes_group)
        notes_l.setContentsMargins(8, 6, 8, 6)
        notes_l.setSpacing(4)

        self._notes = QTextEdit()
        self._notes.setObjectName("releaseNotes")
        self._notes.setReadOnly(True)
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        notes_l.addWidget(self._notes)

        self.smart_drop_hint_label = QLabel(
            "Drop themes, albums, tracks, photos, videos, firmwares here to transfer. "
            "<a href='smart-drop-info'>More Info</a>"
        )
        self.smart_drop_hint_label.setWordWrap(True)
        self.smart_drop_hint_label.setAlignment(Qt.AlignCenter)
        self.smart_drop_hint_label.setStyleSheet("color: #9ca3af; font-size: 11px; margin-top: 4px;")
        self.smart_drop_hint_label.setTextFormat(Qt.RichText)
        self.smart_drop_hint_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.smart_drop_hint_label.linkActivated.connect(self._show_smart_drop_info)
        notes_l.addWidget(self.smart_drop_hint_label)

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

        local_group = QGroupBox("Choose Firmware Package")
        grp_l = QVBoxLayout(local_group)
        grp_l.setContentsMargins(10, 8, 10, 8)
        grp_l.setSpacing(6)

        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._browse_btn = QPushButton(tr("sel_browse"))
        self._browse_btn.clicked.connect(self._on_choose_file)
        row.addWidget(self._path_edit, 1)
        row.addWidget(self._browse_btn)
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
        self._start_btn.setDefault(True)
        self._start_btn.setAutoDefault(True)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_flash)
        grp_l.addWidget(self._start_btn)

        layout.addWidget(local_group)
        layout.addStretch()
        return page

    def _show_device_type_help(self):
        QMessageBox.information(
            self,
            "Device Type Help",
            "Try Type A System Software first.\n\n"
            "If your scroll wheel doesn't respond after installation, "
            "install one of the Type B options.",
        )

    def _show_smart_drop_info(self, _link=""):
        QMessageBox.information(
            self,
            "Smart Drop Info",
            "Smart Drop allows transferring themes, albums, tracks, photos, "
            "videos, and firmware files directly to your device via USB.\n\n"
            "Connect your powered-on Innioasis device via USB with ADB debugging enabled.",
        )

    def _prompt_pre_install(self, model="Y1", type_variant=None):
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("INNIOASIS_HEADLESS"):
            return True
        reply = QMessageBox.question(
            self,
            "Software Install Instructions",
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
                f"{self._online_banner_count} release(s) \u2014 {tr('sel_install')}"
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
        self._tabs.setTabText(0, tr("sel_online"))
        self._tabs.setTabText(1, tr("sel_local"))
        self._model_label.setText(tr("sel_model"))
        self._type_label.setText(tr("sel_type"))
        self._software_label.setText(tr("sel_software"))
        self._refresh_btn.setText(tr("sel_refresh"))
        self._install_btn.setText(tr("sel_install"))
        self._hint.setText(tr("sel_only_local"))
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        self._browse_btn.setText(tr("sel_browse"))
        self._start_btn.setText(tr("sel_btn_start"))
        self._offline_banner.retranslate()
        self._status_card.retranslate()
        self._apply_online_banner()
        self._apply_local_banner()
        if self._download_status_key:
            self._download_status.setText(tr(self._download_status_key))
        if self._local_status_key:
            self._local_status.setText(tr(self._local_status_key))

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
        if hasattr(self, "_status_label") and not self._download_status_key and not getattr(self, "_download_worker", None):
            self._status_label.setText(f"Please follow the instructions below to install this software on your {lbl}")
        if hasattr(self, "_install_btn"):
            self._install_btn.setToolTip(f"Install or restore the selected system software to your {lbl}")

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
        self._set_online_banner("sel_loading")
        self._releases_worker = ReleasesWorker(
            self.client, package, self.current_model(),
            show_nightly=False, selected_type=self._selected_type,
            force_refresh=force_refresh,
        )
        self._releases_worker.finished.connect(self._on_releases_loaded)
        self._releases_worker.start()
        if force_refresh:
            self._refresh_manifest()

    def _refresh_manifest(self):
        try:
            from ..manifest import ManifestWorker
            self._manifest_worker = ManifestWorker(self, force_refresh=True)
            self._manifest_worker.finished.connect(self._on_manifest_refreshed)
            self._manifest_worker.start()
        except Exception as e:
            logger.debug("Background manifest refresh failed: %s", e)

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
        for rel in releases:
            label = catalog.format_release_display_label(rel)
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, rel)
            tooltip = self._asset_line(rel)
            tag = rel.get("tag_name", "")
            if tag:
                tooltip = f"{tag}\n{tooltip}".strip()
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

    def _asset_line(self, rel):
        assets = ", ".join(
            v["asset"].get("name", "") for v in (rel.get("rom_variants") or [])
        )
        return assets or ""

    def _on_release_selected(self, current, _prev):
        self._install_btn.setEnabled(current is not None)
        if current is None:
            self._notes.clear()
            return
        rel = current.data(Qt.UserRole)
        if rel:
            self._notes.setMarkdown(self._render_release_notes(rel))

    def _render_release_notes(self, rel):
        tag = rel.get("tag_name", "")
        name = rel.get("name", "") or tag
        date = (rel.get("published_at") or "")[:10]
        body = (rel.get("body") or "").strip() or tr("update_no_notes")
        assets = self._asset_line(rel)
        meta = []
        if tag:
            meta.append(f"`{tag}`")
        if date:
            meta.append(date)
        if assets:
            meta.append(f"{tr('sel_release_assets')}: {assets}")
        lines = [f"## {name}"]
        if meta:
            lines.append("")
            lines.append(" \u00b7 ".join(meta))
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append(body[:4000])
        return "\n".join(lines)

    def _on_install(self):
        item = self._release_list.currentItem()
        if item is None:
            return
        rel = item.data(Qt.UserRole)
        if not rel or not rel.get("download_url"):
            return
        package = self.current_package()
        if package is None:
            return

        # Determine model and variant from original asset name / download URL:
        orig_name = rel.get("asset_name") or rel.get("download_url") or ""
        det_m, det_t = detect_model_and_type_from_name(orig_name)
        eff_model = det_m or getattr(package, "device", "") or self.current_model()
        eff_type = det_t or self._selected_type

        # Pre-install disconnect / power-off confirmation popup:
        if not self._prompt_pre_install(eff_model, eff_type):
            return

        self._current_package_model = eff_model
        self._pending_install_release_info = {
            "model": eff_model,
            "software_name": package.name,
            "package_slug": package.slug,
            "tag_name": rel.get("tag_name", ""),
            "release_label": catalog.format_release_display_label(rel),
            "type_variant": eff_type,
        }
        dest_dir = downloads.downloads_dir()
        fname = Path(rel.get("asset_name") or "rom.zip").name
        dest = dest_dir / f"{package.slug}_{rel.get('tag_name', 'latest')}_{fname}"
        extract_dir = compute_extract_dir(dest)

        # 1. Check if package is already downloaded and fully extracted:
        if dest.is_file() and dest.stat().st_size > 0 and _is_extract_complete(extract_dir):
            logger.info("Package %s already cached and extracted at %s, reusing immediately", dest.name, extract_dir)
            self._current_package_path = str(dest)
            self._current_package_name = f"{self.current_software()} ({eff_model})"
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._download_status_key = "sel_prepare_done"
            self._download_status.setText(tr("sel_prepare_done"))
            self._on_online_prep_done(True, str(extract_dir), "")
            return

        # 2. Check if package is already downloaded but needs extraction:
        if dest.is_file() and dest.stat().st_size > 0:
            logger.info("Package %s already downloaded, extracting directly", dest.name)
            self._current_package_path = str(dest)
            self._current_package_name = f"{self.current_software()} ({eff_model})"
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._prepare_package(str(dest), self._on_online_prep_done)
            return

        # 3. Otherwise, start download worker
        self._download_status_key = "sel_download_start"
        self._download_status.setText(tr("sel_download_start"))
        self._download_bar.setValue(0)
        self._download_bar.setVisible(True)
        self._install_btn.setEnabled(False)
        self._download_worker = downloads.DownloadWorker(rel["download_url"], str(dest))
        self._download_worker.progress.connect(self._download_bar.setValue)
        self._download_worker.status.connect(self._on_download_status)
        self._download_worker.finished.connect(self._on_download_done)
        self._download_worker.start()

    def _on_download_status(self, text: str):
        self._download_status_key = ""
        self._download_status.setText(text)

    def _on_download_done(self, ok, result):
        self._download_bar.setVisible(False)
        if not ok:
            self._download_status_key = ""
            self._download_status.setText(f"{tr('sel_download_failed')} \u2014 {result}")
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
        self._download_status.setText(tr("sel_preparing"))
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
            self._download_status.setText(f"{tr('sel_prepare_failed')} \u2014 {err}")
            self._install_btn.setEnabled(True)
            return
        self._download_status_key = "sel_prepare_done"
        self._download_status.setText(tr("sel_prepare_done"))

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
            self._local_status.setText(f"{tr('sel_prepare_failed')} \u2014 {err}")
            return
        self._local_status_key = "sel_prepare_done"
        self._local_status.setText(tr("sel_prepare_done"))

        # Retain only this package's extracted files
        prune_extracted_cache(keep_package_path=self._current_package_path)

        scatter_file = _find_scatter(Path(extract_dir))
        scatter_abs = scatter_file.resolve() if scatter_file else None
        extract_abs = Path(extract_dir).resolve() if extract_dir else None
        eff_model = getattr(self, "_current_package_model", "") or self.current_model()
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
        self._path_edit.setText(path)
        self._current_package_path = path
        self._current_package_name = Path(path).name
        self._current_installed_release_info = None

        # Detect model and variant type from original browsed filename:
        det_m, det_t = detect_model_and_type_from_name(path)
        if det_m:
            self._current_package_model = det_m
            idx = self._model_combo.findText(det_m)
            if idx >= 0:
                self._model_combo.setCurrentIndex(idx)
        else:
            self._current_package_model = self.current_model()

        if det_t:
            self._selected_type = det_t
            idx_t = self._type_combo.findData(det_t)
            if idx_t >= 0:
                self._type_combo.setCurrentIndex(idx_t)

        self._set_local_banner("sel_current_pkg", Path(path).name)
        self._start_btn.setEnabled(False)
        self._prepare_package(path, self._on_local_prep_done)

    def _on_start_flash(self):
        if self._current_package_path:
            eff_model = getattr(self, "_current_package_model", "") or self.current_model()
            eff_type = getattr(self, "_selected_type", None)
            if not self._prompt_pre_install(eff_model, eff_type):
                return
            self._emit_ready()

    def _emit_ready(self):
        self.package_selected.emit(
            self._current_package_path,
            self._current_package_name,
            getattr(self, "_current_package_model", "") or self.current_model(),
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
