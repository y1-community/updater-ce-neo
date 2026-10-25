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

#: Remote/merged themepack archive name (downloads dir or bundled zip).
THEME_PACK_ZIP_NAME = "rockbox_360p_themepack.zip"


def open_wizards():
    """Currently open setup wizards (lets the passive sync report inline)."""
    import weakref

    registry = getattr(open_wizards, "_registry", None)
    if registry is None:
        registry = weakref.WeakSet()
        open_wizards._registry = registry
    return registry


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
        from .ui.glass import apply_dialog_theme
        apply_dialog_theme(self)
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


# ---------------------------------------------------------------------------
# Directory merge + combined theme-dir builder (360p wizard)
# ---------------------------------------------------------------------------

try:
    from . import paths as _paths
    from .theme_pack_bundled import (
        BUNDLED_THEME_PACK_ZIP_NAME,
    )
    _bundled_zip_path = (getattr(_paths, "RESOURCES_DIR", None) or (_paths.REPO_ROOT / "assets")) / "360p" / BUNDLED_THEME_PACK_ZIP_NAME
except Exception:
    _bundled_zip_path = Path("/tmp/missing_asset") / "rockbox_360p_themepack.zip"


def bundled_themepack_zip_path() -> Path:
    """The bundled 360p theme pack archive shipped inside the app bundle.

    On first call this returns the path the app should treat as the bundled
    theme pack. By default that is the repo/checkout ``assets/360p``
    archive; packaged builds pin it through the suite's resource helper so a
    frozen app never reaches back into the checkout tree.
    """
    return _bundled_zip_path


def bundled_rockbox_dir() -> Path:
    """The app's own ``.rockbox`` content: themes plus the fonts pack.

    The remote themepack zip carries themes only, so the bundled copy (which
    includes the Rockbox fonts pack) is always the merge base: remote files
    overlay it, never the other way round.
    """
    from . import paths as _paths

    base = getattr(_paths, "RESOURCES_DIR", None) or (_paths.REPO_ROOT / "assets")
    return Path(base) / "360p" / "themes-themepack" / ".rockbox"


# Legacy alias preserved for any remaining callers and tests.
def bundled_themepack_dir() -> Path:
    """Backward-compatible accessor for the legacy unbundled tree location."""
    return bundled_rockbox_dir()


def merge_dirs(src_dir: str | Path, dst_dir: str | Path,
               progress_cb: Optional[Callable[[int, int], None]] = None) -> int:
    """Merge ``src_dir`` into ``dst_dir``, overwriting same-name files.

    Only files are copied (directories are created as needed). Returns the
    number of files written.
    """
    src = Path(src_dir)
    dst = Path(dst_dir)
    dst.mkdir(parents=True, exist_ok=True)
    files = [p for p in src.rglob("*") if p.is_file()]
    total = len(files)
    count = 0
    for i, src_file in enumerate(files):
        if progress_cb is not None:
            try:
                progress_cb(i, total)
            except Exception:
                pass
        rel = src_file.relative_to(src)
        dest = dst / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dest)
            count += 1
        except (OSError, PermissionError) as e:
            logger.warning("merge_dirs: skipping %s: %s", src_file, e)
    if progress_cb is not None:
        try:
            progress_cb(total, total)
        except Exception:
            pass
    return count


def build_combined_rockbox_dir(zip_path: str | Path, dest_dir: str | Path,
                               progress_cb: Optional[Callable[[int, int], None]] = None) -> int:
    """Build the full 360p payload: bundled base + remote overlay.

    Copies :func:`bundled_rockbox_dir` (themes + fonts pack) into
    ``dest_dir``, then extracts the latest remote themepack ``zip_path`` over
    it, overwriting same-name files. Bundled-only files (notably fonts) are
    preserved. Returns the number of remote files merged.
    """
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    base = None
    if bundled_rockbox_dir().is_dir():
        base = bundled_rockbox_dir()
    elif bundled_themepack_zip_path().is_file():
        # Prefer a bundled archive when the legacy directory is absent.
        base = dest / "_from_bundled_zip"
        extract_theme_pack_zip(bundled_themepack_zip_path(), base, progress_cb=None)
    if base is not None and base.is_dir():
        merge_dirs(base, dest)
    else:
        logger.warning(
            "build_combined_rockbox_dir: bundled theme source missing: "
            f"{bundled_rockbox_dir()!r} / {bundled_themepack_zip_path()!r}"
        )
    return extract_theme_pack_zip(Path(zip_path), dest, progress_cb=progress_cb)


def sync_bundled_themepack_from_remote(zip_path: str | Path,
                                       assets_rockbox_dir: Optional[str | Path] = None,
                                       assets_themepack_zip: Optional[str | Path] = None) -> int:
    """Refresh the in-repo ``assets/360p`` copy from a downloaded remote zip.

    Merges the zip over ``assets/360p/themes-themepack/.rockbox`` (or the
    given dir), overwriting same-name files while keeping bundled-only files
    such as the fonts pack. Used to refresh what ships with the app; the
    per-install merge in :func:`build_combined_rockbox_dir` applies the same
    ordering at install time.
    """
    target = Path(assets_rockbox_dir) if assets_rockbox_dir else bundled_rockbox_dir()
    return extract_theme_pack_zip(Path(zip_path), target)


class AdbWatchWorker(QThread):
    """Watch for an ADB device for up to ``timeout`` seconds."""

    found = Signal(str)  # serial
    timed_out = Signal()
    watch_progress = Signal(float)  # 0.0-1.0

    def __init__(self, adb: str, timeout: float = 30, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._timeout = timeout

    def run(self):
        try:
            from . import adb as _adb

            serial = _adb.wait_for_adb_device(
                self._adb,
                timeout=self._timeout,
                cancel_cb=self.isInterruptionRequested,
                progress_cb=self.watch_progress.emit,
            )
        except Exception as e:
            logger.debug("ADB watch failed: %s", e)
            serial = None
        if self.isInterruptionRequested():
            return
        if serial:
            self.found.emit(serial)
        else:
            self.timed_out.emit()


class AdbPushWorker(QThread):
    """Push the combined theme dir to the player over ADB, then reboot it."""

    progress = Signal(int, int)
    finished = Signal(bool, str)  # success, message

    def __init__(self, adb: str, serial: str, local_dir: Path, parent=None):
        super().__init__(parent)
        self._adb = adb
        self._serial = serial
        self._local_dir = Path(local_dir)

    def run(self):
        try:
            from . import adb as _adb

            try:
                _adb.adb_enable_mass_storage(self._adb, self._serial)
            except Exception:
                pass

            device_dirs = _adb.adb_find_all_rockbox_dirs(self._adb, self._serial)
            if not device_dirs:
                # The player may expose storage without a probed path; fall
                # back to the primary candidate and let push create it.
                device_dirs = [_adb.DEVICE_ROCKBOX_CANDIDATES[0]]

            total_dirs = len(device_dirs)
            all_ok = True
            messages: list[str] = []

            for idx, d_dir in enumerate(device_dirs):
                def _dir_progress(curr: int, total: int, idx=idx, total_dirs=total_dirs):
                    if total > 0:
                        overall_curr = idx * total + curr
                        overall_total = total_dirs * total
                        self.progress.emit(overall_curr, overall_total)
                    else:
                        self.progress.emit(curr, total)

                ok, msg = _adb.adb_push_merge(
                    self._adb,
                    self._serial,
                    self._local_dir,
                    d_dir,
                    progress_cb=_dir_progress,
                )
                if not ok:
                    all_ok = False
                    messages.append(f"{d_dir}: {msg or 'push failed'}")
                elif msg:
                    messages.append(msg)

            if not all_ok:
                self.finished.emit(False, "; ".join(messages) or "ADB push failed")
                return
            _adb.adb_reboot(self._adb, self._serial)
            self.finished.emit(True, "; ".join(messages))
        except Exception as e:
            logger.exception("ADB theme push failed: %s", e)
            self.finished.emit(False, str(e))

    def _on_progress(self, curr: int, total: int):
        self.progress.emit(curr, total)


class DirMergeWorker(QThread):
    """Merge the combined theme dir into a USB-storage ``.rockbox`` folder."""

    progress = Signal(int, int)
    finished = Signal(bool, str, int)  # success, error, count

    def __init__(self, src_dir: Path, dst_dir: Path, parent=None):
        super().__init__(parent)
        self._src = Path(src_dir)
        self._dst = Path(dst_dir)

    def run(self):
        try:
            count = merge_dirs(self._src, self._dst, progress_cb=self._on_progress)
            self.finished.emit(True, "", count)
        except Exception as e:
            logger.exception("Theme merge failed: %s", e)
            self.finished.emit(False, str(e), 0)

    def _on_progress(self, curr: int, total: int):
        self.progress.emit(curr, total)


class Rockbox360pSetupWizard(QDialog):
    """Post-flash wizard for 360p Rockbox installs: themes + fonts on-device.

    Page 0 asks the user to disconnect, boot into Rockbox, and reconnect.
    Page 1 watches for an ADB device (30 s) and pushes automatically,
    rebooting the player when done. When no ADB binary or device is
    available, page 2 falls back to the USB Storage Mode folder picker (any
    folder on the device; the ``.rockbox`` location is resolved from there).
    The closing page mirrors the install-done checkmark style.
    """

    installed_success = Signal()
    finished_via_adb = Signal(bool)

    def __init__(self, parent=None, model="Y1"):
        super().__init__(parent)
        from .ui.glass import apply_dialog_theme
        apply_dialog_theme(self)
        self.model = model
        self._adb_binary: Optional[str] = None
        self._adb_serial: Optional[str] = None
        self._via_adb = False
        self._combined_dir: Optional[Path] = None
        self._work_dir: Optional[Path] = None
        self._zip_path = downloads_dir() / THEME_PACK_ZIP_NAME
        self._download_worker: Optional[DownloadWorker] = None
        self._watch_worker: Optional[AdbWatchWorker] = None
        self._push_worker: Optional[AdbPushWorker] = None
        self._merge_worker: Optional[DirMergeWorker] = None

        self.setWindowTitle(tr("rockbox_wizard_title"))
        self.setModal(True)
        self.setMinimumWidth(540)
        self._build_ui()
        try:
            open_wizards().add(self)
        except Exception:
            pass

    # -- pages ----------------------------------------------------------
    def _build_ui(self):
        from PySide6.QtWidgets import QStackedWidget

        t = T()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(14)

        title_lbl = QLabel(f"<h3 style='margin:0; color:{t.fg};'>{tr('rockbox_wizard_title')}</h3>")
        title_lbl.setTextFormat(Qt.RichText)
        layout.addWidget(title_lbl)

        self._stack = QStackedWidget()
        layout.addWidget(self._stack)

        self._reboot_page = self._card(tr("rockbox_wizard_reboot_title"), tr("rockbox_wizard_reboot_desc"))
        self._stack.addWidget(self._reboot_page)

        self._adb_page = self._build_adb_page()
        self._stack.addWidget(self._adb_page)

        self._usb_page = self._build_usb_page()
        self._stack.addWidget(self._usb_page)

        self._done_page = self._build_done_page()
        self._stack.addWidget(self._done_page)

        # Bottom buttons: Back / primary action, like the install prompts.
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._back_btn = QPushButton(tr("rockbox_wizard_back"))
        self._back_btn.clicked.connect(self._on_back)
        btn_row.addWidget(self._back_btn)
        self._next_btn = QPushButton(tr("rockbox_wizard_next"))
        self._next_btn.setDefault(True)
        self._next_btn.clicked.connect(self._on_next)
        btn_row.addWidget(self._next_btn)
        layout.addLayout(btn_row)
        self._sync_buttons()

    def _card(self, title: str, desc: str) -> QWidget:
        t = T()
        card = QWidget()
        card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border};"
            f" border-radius: 8px; }}"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(6)
        title_lbl = QLabel(f"<b>{title}</b>")
        title_lbl.setStyleSheet("font-size: 13px; border: none; background: transparent;")
        box.addWidget(title_lbl)
        desc_lbl = QLabel(desc)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 12px; border: none; background: transparent; line-height: 1.4;")
        box.addWidget(desc_lbl)
        return card

    def _build_adb_page(self) -> QWidget:
        t = T()
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(self._card(tr("rockbox_wizard_adb_title"), tr("rockbox_wizard_adb_desc")))
        self._adb_bar = QProgressBar()
        self._adb_bar.setRange(0, 100)
        self._adb_bar.setValue(0)
        self._adb_bar.setFixedHeight(12)
        box.addWidget(self._adb_bar)
        self._adb_status = QLabel(tr("rockbox_wizard_adb_searching"))
        self._adb_status.setWordWrap(True)
        self._adb_status.setStyleSheet(f"font-size: 12px; color: {t.fg};")
        box.addWidget(self._adb_status)
        return page

    def _build_usb_page(self) -> QWidget:
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(self._card(tr("rockbox_wizard_usb_title"), tr("rockbox_wizard_usb_desc")))
        row = QHBoxLayout()
        row.setSpacing(10)
        self._usb_select_btn = QPushButton(tr("themepack_btn_select_folder"))
        self._usb_select_btn.setStyleSheet("font-weight: 600; padding: 6px 14px;")
        self._usb_select_btn.clicked.connect(self._on_usb_pick)
        row.addWidget(self._usb_select_btn)
        self._usb_folder_label = QLabel()
        self._usb_folder_label.setWordWrap(True)
        row.addWidget(self._usb_folder_label, 1)
        box.addLayout(row)
        self._usb_bar = QProgressBar()
        self._usb_bar.setRange(0, 100)
        self._usb_bar.setValue(0)
        self._usb_bar.setFixedHeight(12)
        self._usb_bar.setVisible(False)
        box.addWidget(self._usb_bar)
        self._usb_status = QLabel()
        self._usb_status.setWordWrap(True)
        self._usb_status.setVisible(False)
        box.addWidget(self._usb_status)
        return page

    def _build_done_page(self) -> QWidget:
        t = T()
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        done_lbl = QLabel(
            f"<span style='color:{t.ok_fg}; font-weight:700; font-size:14px;'>\u2713 "
            f"{tr('rockbox_wizard_done_title')}</span>"
        )
        done_lbl.setTextFormat(Qt.RichText)
        box.addWidget(done_lbl)
        info = QLabel(tr("rockbox_wizard_done_desc"))
        info.setWordWrap(True)
        box.addWidget(info)
        return page

    # -- navigation -----------------------------------------------------
    def _sync_buttons(self):
        idx = self._stack.currentIndex()
        self._back_btn.setVisible(idx in (1, 2))
        if idx == 0:
            self._next_btn.setText(tr("rockbox_wizard_next"))
            self._next_btn.setVisible(True)
        elif idx == 1:
            self._next_btn.setText(tr("rockbox_wizard_skip_adb"))
            self._next_btn.setVisible(True)
        elif idx == 2:
            self._next_btn.setVisible(False)
        else:
            self._next_btn.setText(tr("rockbox_wizard_done"))
            self._next_btn.setVisible(True)

    def _on_back(self):
        idx = self._stack.currentIndex()
        if idx == 1:
            self._stop_watch()
            self._stack.setCurrentIndex(0)
        elif idx == 2:
            self._stack.setCurrentIndex(1)
            self._start_watch()
        self._sync_buttons()

    def _on_next(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            self._stack.setCurrentIndex(1)
            self._sync_buttons()
            self._start_watch()
        elif idx == 1:
            self._stop_watch()
            self._stack.setCurrentIndex(2)
            self._sync_buttons()
        else:
            self._cleanup_work_dir()
            self.accept()

    # -- ADB watch ------------------------------------------------------
    def _start_watch(self):
        from . import adb as _adb

        self._adb_binary = _adb.find_adb_binary()
        if not self._adb_binary:
            self._adb_status.setText(tr("rockbox_wizard_no_adb"))
            self._stack.setCurrentIndex(2)
            self._sync_buttons()
            return
        self._adb_bar.setValue(0)
        self._adb_status.setText(tr("rockbox_wizard_adb_searching"))
        self._stop_watch()
        self._watch_worker = AdbWatchWorker(self._adb_binary, timeout=_adb.ADB_WATCH_TIMEOUT_SECONDS, parent=self)
        self._watch_worker.watch_progress.connect(lambda f: self._adb_bar.setValue(int(f * 100)))
        self._watch_worker.found.connect(self._on_adb_found)
        self._watch_worker.timed_out.connect(self._on_adb_timeout)
        self._watch_worker.start()

    def _stop_watch(self):
        w = self._watch_worker
        self._watch_worker = None
        if w is not None:
            w.requestInterruption()
            w.wait(1500)

    def maybe_adopt_passive_push(self, serial: str) -> bool:
        """Take over a passive ADB push instead of waiting out the watch.

        Only while the ADB page is still watching (no push of our own yet).
        Returns True when adopted.
        """
        try:
            if self._stack.currentIndex() != 1:
                return False
            push = getattr(self, "_push_worker", None)
            if push is not None and push.isRunning():
                return False
            if getattr(self, "_via_adb", False):
                return False
        except Exception:
            return False
        self._on_adb_found(serial or "")
        return True

    def note_passive_progress(self, done: int, total: int, label: str) -> None:
        """Show background merge progress while the wizard sits idle on it."""
        try:
            idx = self._stack.currentIndex()
            push = getattr(self, "_push_worker", None)
            pushing = push is not None and push.isRunning()
            if idx == 1 and not pushing and not getattr(self, "_via_adb", False):
                text = f"{label}: {done}/{total}" if total else str(label)
                self._adb_status.setText(text)
                if total:
                    self._adb_bar.setValue(int(done / total * 100))
            elif idx == 2 and getattr(self, "_usb_target", None) is None:
                self._usb_status.setVisible(True)
                self._usb_status.setText(f"{label}: {done}/{total}" if total else str(label))
        except Exception:
            pass

    def _on_adb_found(self, serial: str):
        self._stop_watch()
        self._adb_serial = serial
        self._adb_status.setText(tr("rockbox_wizard_adb_found").format(serial=serial))
        self._next_btn.setVisible(False)
        self._back_btn.setVisible(False)
        self._ensure_payload_then(self._start_adb_push)

    def _on_adb_timeout(self):
        # A push of our own may have started (found, or adopted passive push)
        # while the watch was winding down: never yank the page mid-push.
        push = getattr(self, "_push_worker", None)
        if (push is not None and push.isRunning()) or getattr(self, "_via_adb", False):
            return
        try:
            if self._stack.currentIndex() != 1:
                return
        except Exception:
            pass
        self._adb_status.setText(tr("rockbox_wizard_adb_timeout"))
        self._stack.setCurrentIndex(2)
        self._sync_buttons()

    def _start_adb_push(self):
        if not self._adb_binary or not self._adb_serial or not self._combined_dir:
            return
        self._adb_status.setText(tr("rockbox_wizard_adb_pushing"))
        self._push_worker = AdbPushWorker(self._adb_binary, self._adb_serial, self._combined_dir, parent=self)
        self._push_worker.progress.connect(self._on_push_progress)
        self._push_worker.finished.connect(self._on_push_finished)
        self._push_worker.start()

    def _on_push_progress(self, curr: int, total: int):
        if total > 0:
            self._adb_bar.setValue(int(curr / total * 100))

    def _on_push_finished(self, ok: bool, msg: str):
        if not ok:
            self._adb_status.setText(f"{tr('rockbox_wizard_adb_push_failed')}: {msg}")
            self._stack.setCurrentIndex(2)
            self._sync_buttons()
            return
        self._via_adb = True
        self._show_done()

    # -- payload (bundled base + latest remote overlay) ------------------
    def _ensure_payload_then(self, ready_cb):
        """Make sure the combined theme dir exists, downloading first if needed."""
        if self._combined_dir and self._combined_dir.is_dir():
            ready_cb()
            return
        self._pending_ready_cb = ready_cb
        if self._zip_path.exists() and self._zip_path.stat().st_size > 1024:
            self._build_combined()
        else:
            self._adb_status.setText(tr("themepack_status_downloading"))
            self._download_worker = DownloadWorker(THEME_PACK_URL, self._zip_path, parent=self)
            self._download_worker.finished.connect(self._on_payload_downloaded)
            self._download_worker.start()

    def _on_payload_downloaded(self, ok: bool, result_or_err: str):
        if not ok:
            # Offline: fall back to whatever the bundled copy alone provides.
            base = bundled_rockbox_dir()
            if base.is_dir():
                import tempfile

                self._work_dir = Path(tempfile.mkdtemp(prefix="rockbox_360p_"))
                self._combined_dir = self._work_dir / ".rockbox"
                merge_dirs(base, self._combined_dir)
                cb = getattr(self, "_pending_ready_cb", None)
                if cb:
                    cb()
                return
            zip_candidate = bundled_themepack_zip_path()
            if zip_candidate.is_file():
                import tempfile

                self._work_dir = Path(tempfile.mkdtemp(prefix="rockbox_360p_"))
                self._combined_dir = self._work_dir / ".rockbox"
                extract_theme_pack_zip(zip_candidate, self._combined_dir)
                cb = getattr(self, "_pending_ready_cb", None)
                if cb:
                    cb()
                return
            self._adb_status.setText(f"Download failed: {result_or_err}")
            return
        self._build_combined()

    def _build_combined(self):
        import tempfile

        self._cleanup_work_dir()
        self._work_dir = Path(tempfile.mkdtemp(prefix="rockbox_360p_"))
        self._combined_dir = self._work_dir / ".rockbox"
        try:
            build_combined_rockbox_dir(self._zip_path, self._combined_dir)
        except Exception as e:
            logger.exception("Combined theme build failed: %s", e)
        cb = getattr(self, "_pending_ready_cb", None)
        self._pending_ready_cb = None
        if cb:
            cb()

    # -- USB storage fallback --------------------------------------------
    def _on_usb_pick(self):
        chosen = QFileDialog.getExistingDirectory(
            self,
            tr("rockbox_wizard_usb_title"),
            str(Path.home()),
            QFileDialog.ShowDirsOnly | QFileDialog.DontResolveSymlinks,
        )
        if not chosen:
            return
        try:
            target = find_or_create_rockbox_dir(chosen)
        except Exception as e:
            self._usb_status.setVisible(True)
            self._usb_status.setText(f"{tr('themepack_err_not_found')} ({e})")
            return
        self._usb_target = target
        self._usb_folder_label.setText(f"Target: {target}")
        self._usb_select_btn.setEnabled(False)
        self._ensure_payload_then(self._start_usb_merge)

    def _start_usb_merge(self):
        target = getattr(self, "_usb_target", None)
        if not target or not self._combined_dir:
            return
        t = T()
        self._usb_bar.setVisible(True)
        self._usb_bar.setValue(0)
        self._usb_status.setVisible(True)
        self._usb_status.setStyleSheet(f"font-size: 12px; color: {t.fg};")
        self._usb_status.setText(tr("themepack_status_extracting"))
        self._merge_worker = DirMergeWorker(self._combined_dir, target, parent=self)
        self._merge_worker.progress.connect(self._on_usb_progress)
        self._merge_worker.finished.connect(self._on_usb_finished)
        self._merge_worker.start()

    def _on_usb_progress(self, curr: int, total: int):
        if total > 0:
            self._usb_bar.setValue(int(curr / total * 100))

    def _on_usb_finished(self, ok: bool, err: str, count: int):
        t = T()
        if not ok:
            self._usb_status.setStyleSheet(f"font-size: 12px; color: {t.err_fg};")
            self._usb_status.setText(f"{tr('sel_themepack_failed')}: {err}")
            self._usb_select_btn.setEnabled(True)
            return
        del count
        self._usb_bar.setValue(100)
        self._usb_status.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {t.ok_fg};")
        self._usb_status.setText(tr("themepack_complete"))
        self._show_done()

    # -- done -------------------------------------------------------------
    def _show_done(self):
        self._stop_watch()
        self._stack.setCurrentIndex(3)
        self._sync_buttons()
        try:
            from . import theme_sync as _sync

            _sync.record_theme_sync(self.model)
            target = getattr(self, "_usb_target", None)
            if target:
                _sync.remember_theme_target(self.model, str(target))
        except Exception:
            pass
        self.finished_via_adb.emit(self._via_adb)
        self.installed_success.emit()

    def _cleanup_work_dir(self):
        work = self._work_dir
        self._work_dir = None
        self._combined_dir = None
        if work:
            try:
                shutil.rmtree(work, ignore_errors=True)
            except Exception:
                pass

    def closeEvent(self, event):
        try:
            open_wizards().discard(self)
        except Exception:
            pass
        self._stop_watch()
        for w in (self._download_worker, self._push_worker, self._merge_worker):
            try:
                if w is not None and w.isRunning():
                    w.requestInterruption()
                    w.wait(1500)
            except Exception:
                pass
        self._cleanup_work_dir()
        super().closeEvent(event)
