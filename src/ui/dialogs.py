"""Modal dialogs — flash complete / failed / diagnostics / update available
(port of the Chin ``app.ui.dialogs``)."""

import sys
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ..i18n import tr
from ..updates import asset_hint, pick_platform_asset


class FlashCompleteDialog(QDialog):
    def __init__(self, parent=None, package_name="", elapsed="00:00"):
        super().__init__(parent)
        self.setWindowTitle(tr("flash_complete"))
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"<h2 style='color:#059669;'>{tr('flash_complete')} ✅</h2>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        body = QLabel(
            f"{tr('flash_current_pkg')}: <b>{package_name or '—'}</b><br>"
            f"{tr('flash_elapsed')}: {elapsed}"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class FlashFailedDialog(QDialog):
    def __init__(self, parent=None, error_code="", step="", package_name="", retry_count=0):
        super().__init__(parent)
        self.setWindowTitle(tr("flash_failed"))
        self.setMinimumWidth(440)
        self._want_retry = False
        self._want_log = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel(f"<h2 style='color:#dc2626;'>{tr('flash_failed')} ✕</h2>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        body = QLabel(
            f"{tr('err_error_code')}: <b>{error_code or '—'}</b><br>"
            f"{tr('err_failed_at')}: {step or '—'}<br>"
            f"{tr('flash_current_pkg')}: {package_name or '—'}<br>"
            f"{tr('err_retry_count')}: {retry_count}"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)

        row = QHBoxLayout()
        retry_btn = QPushButton(tr("err_btn_retry"))
        retry_btn.clicked.connect(self._on_retry)
        log_btn = QPushButton(tr("err_view_log"))
        log_btn.clicked.connect(self._on_log)
        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.reject)
        row.addWidget(retry_btn)
        row.addWidget(log_btn)
        row.addWidget(close_btn)
        row.addStretch()
        layout.addLayout(row)

    def _on_retry(self):
        self._want_retry = True
        self.accept()

    def _on_log(self):
        self._want_log = True
        self.accept()

    def want_retry(self):
        return self._want_retry

    def want_log(self):
        return self._want_log


class DiagnosticsDialog(QDialog):
    """Diagnostics (formerly Log Center) — plain-text view of the session log."""

    def __init__(self, parent=None, lines=None):
        super().__init__(parent)
        self.setWindowTitle(tr("log_center"))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        layout.addWidget(self._view)
        self._empty = True
        self.set_lines(lines or [])

    def set_lines(self, lines):
        if lines:
            self._view.setPlainText("\n".join(lines))
            self._empty = False
        else:
            self._view.setPlainText(tr("log_no_entries"))
            self._empty = True

    def append_line(self, line):
        """Append a live log line (replaces the empty-state placeholder)."""
        if self._empty:
            self._view.setPlainText(str(line))
            self._empty = False
        else:
            self._view.append(str(line))


class UpdateAvailableDialog(QDialog):
    """Tells the user a newer app version exists and walks them through
    downloading + installing it for their OS."""

    def __init__(self, parent=None, info=None, current_version="", on_skip=None):
        super().__init__(parent)
        self._info = info
        self._on_skip = on_skip
        self.setWindowTitle(tr("update_available"))
        self.setMinimumWidth(520)
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(
            f"<h2 style='color:#2563eb;'>{tr('update_available')} 🎉</h2>"
        )
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        version = QLabel(
            f"<b>{tr('update_available_fmt').format(new=self._info.version, current=current_version)}</b>"
        )
        version.setTextFormat(Qt.RichText)
        version.setWordWrap(True)
        layout.addWidget(version)

        notes_label = QLabel(f"<b>{tr('update_notes')}</b>")
        notes_label.setTextFormat(Qt.RichText)
        layout.addWidget(notes_label)
        self._notes = QTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setPlainText(self._info.body.strip() or tr("update_no_notes"))
        self._notes.setFixedHeight(140)
        layout.addWidget(self._notes)

        asset = pick_platform_asset(self._info.assets)
        hint = asset_hint()
        guide_key = {
            "win32": "update_install_win",
            "darwin": "update_install_mac",
            "linux": "update_install_linux",
        }.get(sys.platform, "update_install_generic")
        guide = QLabel(tr(guide_key).format(hint=hint))
        guide.setWordWrap(True)
        guide.setStyleSheet(
            "font-size: 12px; color: #374151; background-color: #eff6ff;"
            " border-radius: 8px; padding: 10px 14px;"
        )
        layout.addWidget(guide)

        self._asset = asset
        btn_row = QHBoxLayout()
        self._download_btn = QPushButton(tr("update_btn_download"))
        self._download_btn.setCursor(Qt.PointingHandCursor)
        self._download_btn.clicked.connect(self._on_download)
        self._later_btn = QPushButton(tr("update_btn_later"))
        self._later_btn.setCursor(Qt.PointingHandCursor)
        self._later_btn.clicked.connect(self.reject)
        self._skip_btn = QPushButton(tr("update_btn_skip"))
        self._skip_btn.setCursor(Qt.PointingHandCursor)
        self._skip_btn.clicked.connect(self._on_skip_version)
        for b in (self._download_btn, self._later_btn, self._skip_btn):
            b.setStyleSheet(
                "QPushButton { border-radius: 8px; padding: 8px 16px; font-weight: 600; }"
            )
        self._download_btn.setStyleSheet(
            "QPushButton { background-color: #2563eb; color: white; border-radius: 8px;"
            " padding: 8px 16px; font-weight: 600; border: none; }"
            "QPushButton:hover { background-color: #1d4ed8; }"
        )
        btn_row.addWidget(self._download_btn)
        btn_row.addWidget(self._later_btn)
        btn_row.addWidget(self._skip_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_download(self):
        url = ""
        if self._asset and self._asset.get("browser_download_url"):
            url = self._asset["browser_download_url"]
        elif self._info.html_url:
            url = self._info.html_url
        if url:
            webbrowser.open(url)
        self.accept()

    def _on_skip_version(self):
        if self._on_skip:
            self._on_skip(self._info.version)
        self.reject()
