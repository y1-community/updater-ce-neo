"""Select package page — online firmware listing + local file picker."""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, downloads
from ..flash_service import ExtractWorker
from ..config import DEVICE_MODELS
from ..i18n import tr
from .widgets import Banner, Card
from .dark import T

logger = logging.getLogger(__name__)


class ReleasesWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, client, package, model, show_nightly, selected_type=None, parent=None):
        super().__init__(parent)
        self.client = client
        self.package = package
        self.model = model
        self.show_nightly = show_nightly
        self.selected_type = selected_type

    def run(self):
        try:
            releases = self.client.releases_for_package(
                self.package, self.model, self.show_nightly, self.selected_type
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
        self._status_tag_label.setStyleSheet(
            f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        self._status_card.add_widget(self._status_tag_label)
        layout.addWidget(self._status_card)

    def _build_online_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        filters = QHBoxLayout()
        self._model_label = QLabel(tr("sel_model"))
        self._model_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {t.fg_dim};"
            f" border: none; background: transparent;"
        )
        filters.addWidget(self._model_label)
        self._model_combo = QComboBox()
        for m in DEVICE_MODELS:
            self._model_combo.addItem(m)
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        filters.addWidget(self._model_combo)

        # Device Type filter: only shown for models with Type A/B variants (e.g. Y1).
        self._type_label = QLabel("Type")
        self._type_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {t.fg_dim};"
            f" border: none; background: transparent;"
        )
        self._type_combo = QComboBox()
        self._type_combo.addItem("Type A", "A")
        self._type_combo.addItem("Type B", "B")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)
        self._type_label.setVisible(False)
        self._type_combo.setVisible(False)
        filters.addWidget(self._type_label)
        filters.addWidget(self._type_combo)

        self._software_label = QLabel(tr("sel_software"))
        self._software_label.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {t.fg_dim};"
            f" border: none; background: transparent;"
        )
        filters.addWidget(self._software_label)
        self._software_combo = QComboBox()
        self._software_combo.currentTextChanged.connect(self._on_software_changed)
        filters.addWidget(self._software_combo)

        self._refresh_btn = QPushButton(tr("sel_refresh"))
        self._refresh_btn.setProperty("cssClass", "ghost")
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.clicked.connect(self._refresh_releases)
        filters.addWidget(self._refresh_btn)
        filters.addStretch()
        layout.addLayout(filters)

        self._online_banner = Banner()
        layout.addWidget(self._online_banner)

        split = QHBoxLayout()
        self._release_list = QListWidget()
        self._release_list.currentItemChanged.connect(self._on_release_selected)
        split.addWidget(self._release_list, 3)
        self._notes = QTextEdit()
        self._notes.setObjectName("releaseNotes")
        self._notes.setReadOnly(True)
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        split.addWidget(self._notes, 2)
        layout.addLayout(split, 1)

        self._download_bar = QProgressBar()
        self._download_bar.setVisible(False)
        layout.addWidget(self._download_bar)

        self._download_status = QLabel("")
        self._download_status.setStyleSheet(
            f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        layout.addWidget(self._download_status)

        install_row = QHBoxLayout()
        self._install_btn = QPushButton(tr("sel_install"))
        self._install_btn.setProperty("cssClass", "primary")
        self._install_btn.setCursor(Qt.PointingHandCursor)
        self._install_btn.setEnabled(False)
        self._install_btn.clicked.connect(self._on_install)
        install_row.addWidget(self._install_btn)
        install_row.addStretch()
        layout.addLayout(install_row)
        return page

    def _build_local_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(12)

        self._hint = QLabel(tr("sel_only_local"))
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            f"font-size: 13px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        layout.addWidget(self._hint)

        self._offline_banner = Banner()
        self._offline_banner.set_type("info")
        self._offline_banner.set_key("sel_offline_install")
        layout.addWidget(self._offline_banner)

        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._browse_btn = QPushButton(tr("sel_browse"))
        self._browse_btn.setProperty("cssClass", "ghost")
        self._browse_btn.setCursor(Qt.PointingHandCursor)
        self._browse_btn.clicked.connect(self._on_choose_file)
        row.addWidget(self._path_edit, 1)
        row.addWidget(self._browse_btn)
        layout.addLayout(row)

        self._local_banner = Banner()
        layout.addWidget(self._local_banner)

        self._local_bar = QProgressBar()
        self._local_bar.setVisible(False)
        layout.addWidget(self._local_bar)

        self._local_status = QLabel("")
        self._local_status.setStyleSheet(
            f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;"
        )
        layout.addWidget(self._local_status)

        self._start_btn = QPushButton(tr("sel_btn_start"))
        self._start_btn.setProperty("cssClass", "primary")
        self._start_btn.setCursor(Qt.PointingHandCursor)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_flash)
        layout.addWidget(self._start_btn)
        layout.addStretch()
        return page

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
        if not has_types:
            self._selected_type = "A"
        else:
            self._selected_type = self._type_combo.currentData() or "A"
        names = catalog.software_names_for_model(model)
        self._software_combo.blockSignals(True)
        self._software_combo.clear()
        self._software_combo.addItems(names)
        self._software_combo.blockSignals(False)
        self._on_software_changed()

    def _on_type_changed(self, index):
        self._selected_type = self._type_combo.currentData()
        self._refresh_releases()

    def _on_software_changed(self):
        self._refresh_releases()

    def _refresh_releases(self):
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
        )
        self._releases_worker.finished.connect(self._on_releases_loaded)
        self._releases_worker.start()

    def _on_releases_loaded(self, releases, error):
        if error:
            self._set_online_banner("sel_offline")
            return
        self._release_list.clear()
        for rel in releases:
            label = rel.get("name") or rel.get("tag_name", "")
            if rel.get("prerelease"):
                label += "  [preview]"
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
            self._release_list.setCurrentRow(0)

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
        dest_dir = downloads.downloads_dir()
        fname = Path(rel.get("asset_name") or "rom.zip").name
        dest = dest_dir / f"{package.slug}_{rel.get('tag_name', 'latest')}_{fname}"
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
        self._current_package_path = result
        self._current_package_name = f"{self.current_software()} ({self.current_model()})"
        self._current_package_model = self.current_model()
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
        self._emit_ready()

    def _on_local_prep_done(self, ok, extract_dir, err):
        self._local_bar.setVisible(False)
        if not ok:
            self._local_status_key = ""
            self._local_status.setText(f"{tr('sel_prepare_failed')} \u2014 {err}")
            return
        self._local_status_key = "sel_prepare_done"
        self._local_status.setText(tr("sel_prepare_done"))
        self._start_btn.setEnabled(True)

    def _on_choose_file(self):
        filter_str = tr("sel_dialog_filter")
        path, _ = QFileDialog.getOpenFileName(self, tr("sel_dialog_title"), "", filter_str)
        if not path:
            return
        self._path_edit.setText(path)
        self._current_package_path = path
        self._current_package_name = Path(path).name
        self._current_package_model = ""
        self._set_local_banner("sel_current_pkg", Path(path).name)
        self._start_btn.setEnabled(False)
        self._prepare_package(path, self._on_local_prep_done)

    def _on_start_flash(self):
        if self._current_package_path:
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
