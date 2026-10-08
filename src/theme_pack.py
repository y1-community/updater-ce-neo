"""Rockbox 360p Theme Pack downloader, installer, and guidance workflow.

Provides automatic retrieval of community themes, guidance for USB storage mode,
resilient .rockbox folder location across platforms (accounting for hidden dotfiles),
and extraction / merging into the target player directory.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .downloads import DownloadWorker, downloads_dir
from .i18n import tr
from .ui.dark import T

logger = logging.getLogger(__name__)

THEME_PACK_URL = "https://github.com/rockbox-y1/themes/archive/refs/heads/themepack.zip"
THEME_PACK_ZIP_NAME = "rockbox_360p_themepack.zip"


def get_mount_root(path: Path) -> Path:
    """Return the filesystem mount root or volume root containing path."""
    curr = path.resolve()
    if sys.platform == "darwin":
        for parent in [curr] + list(curr.parents):
            if parent.parent == Path("/Volumes"):
                return parent
    elif sys.platform == "win32":
        return Path(curr.anchor)
    else:
        for parent in [curr] + list(curr.parents):
            if os.path.ismount(str(parent)):
                return parent
    return curr


def find_or_create_rockbox_dir(selected_path: str | Path) -> Path:
    """Resolve or create the .rockbox directory from any folder on the device.

    Because operating systems like macOS and Linux hide dotfiles (.rockbox)
    by default in system file dialogs, users can select ANY directory or the
    root drive of their connected player. This helper traverses the directory
    tree upward and across siblings to locate an existing .rockbox folder, or
    creates one at the volume root.
    """
    p = Path(selected_path).resolve()
    if not p.is_dir():
        p = p.parent

    # 1. User navigated directly into .rockbox
    if p.name.lower() == ".rockbox":
        return p

    # 2. .rockbox directory is directly inside selected directory
    candidate = p / ".rockbox"
    if candidate.is_dir():
        return candidate

    # 3. Check parents up the tree to the volume/filesystem root
    for parent in p.parents:
        cand = parent / ".rockbox"
        if cand.is_dir():
            return cand
        if parent.parent == parent:
            break

    # 4. Check siblings (in case user selected a folder adjacent to .rockbox)
    try:
        if p.parent and p.parent != p:
            for sibling in p.parent.iterdir():
                if sibling.is_dir() and sibling.name.lower() == ".rockbox":
                    return sibling
    except (OSError, PermissionError):
        pass

    # 5. Check immediate subdirectories of p
    try:
        for child in p.iterdir():
            if child.is_dir() and child.name.lower() == ".rockbox":
                return child
    except (OSError, PermissionError):
        pass

    # 6. Fallback: create .rockbox at the volume root, or within p
    mount_root = get_mount_root(p)
    target = mount_root / ".rockbox" if mount_root else p / ".rockbox"
    target.mkdir(parents=True, exist_ok=True)
    return target


def extract_theme_pack_zip(
    zip_path: Path,
    target_rockbox_dir: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> int:
    """Extract and merge theme pack files into the target .rockbox directory."""
    target_rockbox_dir = Path(target_rockbox_dir)
    target_rockbox_dir.mkdir(parents=True, exist_ok=True)

    extracted_count = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        infolist = zf.infolist()
        total = len(infolist)
        for i, info in enumerate(infolist):
            if progress_cb:
                progress_cb(i, total)

            name = info.filename.replace("\\", "/")
            if info.is_dir() or name.endswith("/"):
                continue

            # Strip repo root directory or themes-themepack/.rockbox/ prefix
            if "/.rockbox/" in name:
                rel_part = name.split("/.rockbox/", 1)[1]
            elif name.startswith(".rockbox/"):
                rel_part = name[len(".rockbox/"):]
            else:
                # Strip leading top-level folder name e.g. themes-themepack/...
                parts = name.split("/", 1)
                rel_part = parts[1] if len(parts) > 1 else parts[0]

            if not rel_part:
                continue

            dest_path = target_rockbox_dir / rel_part
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            extracted_count += 1

    if progress_cb:
        progress_cb(total, total)
    return extracted_count


class ThemePackInstallWorker(QThread):
    """Worker thread to extract and install themes without freezing the Qt UI."""

    progress = Signal(int, int)  # current, total
    finished = Signal(bool, str, int)  # success, error_message, count

    def __init__(self, zip_path: Path, target_dir: Path, parent=None):
        super().__init__(parent)
        self.zip_path = zip_path
        self.target_dir = target_dir

    def run(self):
        try:
            count = extract_theme_pack_zip(
                self.zip_path,
                self.target_dir,
                progress_cb=self._on_progress,
            )
            self.finished.emit(True, "", count)
        except Exception as e:
            logger.exception("Theme pack installation failed: %s", e)
            self.finished.emit(False, str(e), 0)

    def _on_progress(self, curr: int, total: int):
        self.progress.emit(curr, total)


class ThemePackGuidanceDialog(QDialog):
    """Interactive dialog guiding the user through USB storage mode and theme installation."""

    installed_success = Signal()

    def __init__(self, parent=None, model="Y1"):
        super().__init__(parent)
        self.model = model
        self._download_worker: Optional[DownloadWorker] = None
        self._install_worker: Optional[ThemePackInstallWorker] = None
        self._zip_path = downloads_dir() / THEME_PACK_ZIP_NAME
        self._target_dir: Optional[Path] = None

        self.setWindowTitle(tr("themepack_dialog_title"))
        self.setModal(True)
        self.setMinimumWidth(540)

        self._build_ui()

    def _build_ui(self):
        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(14)

        # Header Title
        title_lbl = QLabel(f"<h3 style='margin:0; color:{t.fg};'>{tr('themepack_dialog_title')}</h3>")
        title_lbl.setTextFormat(Qt.RichText)
        layout.addWidget(title_lbl)

        # Step 1: USB Storage instructions
        self._step1_card = QWidget()
        self._step1_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border};"
            f" border-radius: 8px; }}"
        )
        s1_layout = QVBoxLayout(self._step1_card)
        s1_layout.setContentsMargins(14, 12, 14, 12)
        s1_layout.setSpacing(6)

        s1_title = QLabel(f"<b>{tr('themepack_step1_title')}</b>")
        s1_title.setStyleSheet(f"font-size: 13px; color: {t.fg}; border: none; background: transparent;")
        s1_layout.addWidget(s1_title)

        s1_desc = QLabel(tr("themepack_step1_desc"))
        s1_desc.setWordWrap(True)
        s1_desc.setStyleSheet(f"font-size: 12px; color: {t.fg}; border: none; background: transparent; line-height: 1.4;")
        s1_layout.addWidget(s1_desc)
        layout.addWidget(self._step1_card)

        # Step 2: Folder selection
        self._step2_card = QWidget()
        self._step2_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border};"
            f" border-radius: 8px; }}"
        )
        s2_layout = QVBoxLayout(self._step2_card)
        s2_layout.setContentsMargins(14, 12, 14, 12)
        s2_layout.setSpacing(8)

        s2_title = QLabel(f"<b>{tr('themepack_step2_title')}</b>")
        s2_title.setStyleSheet(f"font-size: 13px; color: {t.fg}; border: none; background: transparent;")
        s2_layout.addWidget(s2_title)

        s2_desc = QLabel(tr("themepack_step2_desc"))
        s2_desc.setWordWrap(True)
        s2_desc.setStyleSheet(f"font-size: 12px; color: {t.fg}; border: none; background: transparent; line-height: 1.4;")
        s2_layout.addWidget(s2_desc)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(10)

        self._select_btn = QPushButton(tr("themepack_btn_select_folder"))
        self._select_btn.setStyleSheet(f"font-weight: 600; padding: 6px 14px;")
        self._select_btn.clicked.connect(self._on_select_folder)
        action_row.addWidget(self._select_btn)

        self._folder_label = QLabel()
        self._folder_label.setStyleSheet(f"font-size: 11px; color: {t.fg}; border: none; background: transparent;")
        self._folder_label.setWordWrap(True)
        action_row.addWidget(self._folder_label, 1)
        s2_layout.addLayout(action_row)

        layout.addWidget(self._step2_card)

        # Progress bar & status
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFixedHeight(12)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"font-size: 12px; font-weight: 500; color: {t.fg};")
        self._status_label.setVisible(False)
        layout.addWidget(self._status_label)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._close_btn = QPushButton(tr("cancel"))
        self._close_btn.clicked.connect(self._on_close)
        btn_row.addWidget(self._close_btn)
        layout.addLayout(btn_row)

    def _on_select_folder(self):
        """Prompt user for any directory on the device and initiate install flow."""
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("themepack_step2_title"),
            str(Path.home()),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not chosen:
            return

        target_rockbox = find_or_create_rockbox_dir(chosen)
        self._target_dir = target_rockbox
        self._folder_label.setText(f"Target: {target_rockbox}")
        self._select_btn.setEnabled(False)

        # Check if zip is already cached
        if self._zip_path.exists() and self._zip_path.stat().st_size > 1024:
            self._start_install()
        else:
            self._start_download()

    def _start_download(self):
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._status_label.setVisible(True)
        self._status_label.setText(tr("themepack_status_downloading"))

        self._download_worker = DownloadWorker(THEME_PACK_URL, self._zip_path, parent=self)
        self._download_worker.progress.connect(self._progress_bar.setValue)
        self._download_worker.finished.connect(self._on_download_finished)
        self._download_worker.start()

    def _on_download_finished(self, ok: bool, result_or_err: str):
        if not ok:
            t = T()
            self._status_label.setStyleSheet(f"font-size: 12px; color: {t.err_fg};")
            self._status_label.setText(f"Download failed: {result_or_err}")
            self._select_btn.setEnabled(True)
            return

        self._start_install()

    def _start_install(self):
        if not self._target_dir:
            return

        t = T()
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._status_label.setVisible(True)
        self._status_label.setStyleSheet(f"font-size: 12px; color: {t.fg};")
        self._status_label.setText(tr("themepack_status_extracting"))

        self._install_worker = ThemePackInstallWorker(self._zip_path, self._target_dir, parent=self)
        self._install_worker.progress.connect(self._on_install_progress)
        self._install_worker.finished.connect(self._on_install_finished)
        self._install_worker.start()

    def _on_install_progress(self, curr: int, total: int):
        if total > 0:
            pct = int((curr / total) * 100)
            self._progress_bar.setValue(pct)

    def _on_install_finished(self, ok: bool, err_msg: str, count: int):
        t = T()
        if not ok:
            self._status_label.setStyleSheet(f"font-size: 12px; color: {t.err_fg};")
            self._status_label.setText(f"Extraction failed: {err_msg}")
            self._select_btn.setEnabled(True)
            return

        self._progress_bar.setValue(100)
        self._status_label.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {t.ok_fg};")
        self._status_label.setText(tr("themepack_complete"))
        self._close_btn.setText(tr("ok"))
        self.installed_success.emit()

    def _on_close(self):
        if self._download_worker and self._download_worker.isRunning():
            self._download_worker.cancel()
        self.accept()
