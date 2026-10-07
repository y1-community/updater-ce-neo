"""Modal dialogs — flash complete / failed / diagnostics / update available."""

from datetime import datetime, timedelta
import sys

from PySide6.QtCore import QDate, QDateTime, QTime, Qt, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..config import install_power_on_steps, device_label_for_model
from ..diagnostics import (
    CAT_ALL,
    CAT_SP,
    CAT_MTK,
    CAT_GUI,
    DiagnosticsManager,
    available_categories,
    collect_install_sessions,
    filter_log_lines,
    get_log_file,
    log_time_range,
    reveal_in_file_manager,
)
from ..i18n import tr
from ..updates import asset_hint, pick_platform_asset
from .dark import T


class FlashCompleteDialog(QDialog):
    def __init__(self, parent=None, package_name="", elapsed="00:00", model="Y1", is_360p_rockbox=False):
        super().__init__(parent)
        self.model = model
        self.is_360p_rockbox = is_360p_rockbox
        t = T()
        self.setWindowTitle(tr("flash_complete"))
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(f"<h2 style='color:{t.ok_fg};'>{tr('flash_complete')} &#x2705;</h2>")
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        ndash = '\u2014'
        steps = install_power_on_steps(model)
        body = QLabel(
            f"{tr('flash_current_pkg')}: <b>{package_name or ndash}</b><br>"
            f"{tr('flash_elapsed')}: {elapsed}<br><br>"
            f"<b>{steps}</b>"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)

        if self.is_360p_rockbox:
            self._theme_box = QWidget()
            self._theme_box.setStyleSheet(
                f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border};"
                f" border-radius: 8px; }}"
            )
            tb_layout = QVBoxLayout(self._theme_box)
            tb_layout.setContentsMargins(14, 10, 14, 10)
            tb_layout.setSpacing(4)
            self._theme_title = QLabel(f"<b>{tr('themepack_card_title')}</b>")
            self._theme_title.setStyleSheet(f"font-size: 13px; color: {t.accent}; border: none; background: transparent;")
            tb_layout.addWidget(self._theme_title)
            self._theme_desc = QLabel(tr("themepack_card_desc"))
            self._theme_desc.setWordWrap(True)
            self._theme_desc.setStyleSheet(f"font-size: 11px; color: {t.fg_dim}; border: none; background: transparent;")
            tb_layout.addWidget(self._theme_desc)

            tb_row = QHBoxLayout()
            tb_row.setContentsMargins(0, 4, 0, 0)
            self._theme_btn = QPushButton(tr("themepack_install_btn"))
            self._theme_btn.clicked.connect(self._open_theme_pack_flow)
            tb_row.addWidget(self._theme_btn)
            tb_row.addStretch()
            tb_layout.addLayout(tb_row)
            layout.addWidget(self._theme_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _open_theme_pack_flow(self):
        from ..theme_pack import ThemePackGuidanceDialog
        dlg = ThemePackGuidanceDialog(parent=self, model=self.model)
        dlg.installed_success.connect(self._on_theme_pack_installed)
        dlg.exec()

    def _on_theme_pack_installed(self):
        t = T()
        if hasattr(self, "_theme_title"):
            self._theme_title.setText(f"<span style='color:{t.ok_fg}; font-weight:700;'>{tr('themepack_installed_success')}</span>")
        if hasattr(self, "_theme_desc"):
            self._theme_desc.setText(tr("themepack_complete"))
        if hasattr(self, "_theme_btn"):
            self._theme_btn.setText(tr("themepack_installed_success"))
            self._theme_btn.setEnabled(False)
        self.adjustSize()


class FlashFailedDialog(QDialog):
    def __init__(self, parent=None, error_code="", step="", package_name="", retry_count=0):
        super().__init__(parent)
        self.setWindowTitle(tr("flash_failed"))
        self.setMinimumWidth(440)
        self._want_retry = False
        self._want_log = False
        t = T()

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        title = QLabel(
            f"<h2 style='color:{t.danger_fg};'>{tr('flash_failed')} &#x2715;</h2>"
        )
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        ndash = '\u2014'
        body = QLabel(
            f"{tr('err_error_code')}: <b>{error_code or ndash}</b><br>"
            f"{tr('err_failed_at')}: {step or ndash}<br>"
            f"{tr('flash_current_pkg')}: {package_name or ndash}<br>"
            f"{tr('err_retry_count')}: {retry_count}"
        )
        body.setTextFormat(Qt.RichText)
        body.setWordWrap(True)
        layout.addWidget(body)

        row = QHBoxLayout()
        retry_btn = QPushButton(tr("err_btn_retry"))
        retry_btn.setProperty("cssClass", "primary")
        retry_btn.clicked.connect(self._on_retry)
        log_btn = QPushButton(tr("err_view_log"))
        log_btn.setProperty("cssClass", "ghost")
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


class RetryGuidanceDialog(QDialog):
    """Hardware reset instructions shown when the target stops answering.

    A player left in a half-initialised state answers the BROM handshake with
    errno 5 / errno 2 rather than a handshake, and retrying in place cannot
    recover it: the cable has to come out and the hidden reset button has to
    be pressed first. This dialog is that instruction, and it doubles as the
    retry entry point so the next attempt starts from a clean device state.
    """

    def __init__(self, parent=None, detail=""):
        super().__init__(parent)
        t = T()
        self.setWindowTitle(tr("retry_guidance_title"))
        self.setMinimumWidth(470)
        self._want_retry = False

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(
            f"<h2 style='color:{t.warn_fg};'>{tr('retry_guidance_title')}</h2>"
        )
        title.setTextFormat(Qt.RichText)
        layout.addWidget(title)

        intro = QLabel(tr("retry_guidance_intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)

        steps = QLabel(
            f"<b>{tr('retry_guidance_step_1')}</b><br>"
            f"<b>{tr('retry_guidance_step_2')}</b><br>"
            f"<b>{tr('retry_guidance_step_3')}</b>"
        )
        steps.setTextFormat(Qt.RichText)
        steps.setWordWrap(True)
        layout.addWidget(steps)

        if detail:
            detail_label = QLabel(detail)
            detail_label.setWordWrap(True)
            detail_label.setProperty("cssClass", "dimmed")
            layout.addWidget(detail_label)

        row = QHBoxLayout()
        retry_btn = QPushButton(tr("retry_guidance_btn_retry"))
        retry_btn.setProperty("cssClass", "primary")
        retry_btn.setDefault(True)
        retry_btn.clicked.connect(self._on_retry)
        close_btn = QPushButton(tr("close"))
        close_btn.setProperty("cssClass", "ghost")
        close_btn.clicked.connect(self.reject)
        row.addWidget(retry_btn)
        row.addWidget(close_btn)
        row.addStretch()
        layout.addLayout(row)

    def _on_retry(self):
        self._want_retry = True
        self.accept()

    def want_retry(self):
        return self._want_retry


def _to_qdatetime(value: datetime) -> QDateTime:
    """QDateTime for a Python datetime (log timestamps are local time)."""
    return QDateTime(
        QDate(value.year, value.month, value.day),
        QTime(value.hour, value.minute, value.second),
    )


def _from_python(edit) -> datetime | None:
    """Read a QDateTimeEdit back as a Python datetime, or None if unreadable."""
    try:
        value = edit.dateTime()
    except Exception:
        return None
    try:
        return value.toPython()
    except Exception:
        d, tm = value.date(), value.time()
        return datetime(d.year(), d.month(), d.day(), tm.hour(), tm.minute(), tm.second())


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None, lines=None, initial_category=CAT_ALL):
        super().__init__(parent)
        t = T()
        self.setWindowTitle(tr("log_center"))
        self.resize(740, 500)
        self.setMinimumSize(540, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ── Header Bar: Dropdown for log categories + path indicator ──
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        lbl = QLabel(f"{tr('log_center')}:")
        lbl.setStyleSheet(f"font-weight: 600; color: {t.fg};")
        header_row.addWidget(lbl)

        self._category_combo = QComboBox()
        # Only the backends this host can actually produce (macOS has no SP
        # Flash Tool; Windows hides MTKClient unless it was revealed with M).
        for cat, label_key in available_categories():
            self._category_combo.addItem(tr(label_key), cat)

        idx = self._category_combo.findData(initial_category)
        if idx < 0:
            idx = self._category_combo.findData(CAT_ALL)
        if idx >= 0:
            self._category_combo.setCurrentIndex(idx)
        self._category_combo.currentIndexChanged.connect(self._on_category_changed)
        header_row.addWidget(self._category_combo, 1)

        self._file_badge = QLabel()
        self._file_badge.setStyleSheet(
            f"font-size: 11px; color: {t.fg_dim}; background: {t.bg_card}; padding: 3px 8px;"
            f" border-radius: 4px; border: 1px solid {t.border};"
        )
        header_row.addWidget(self._file_badge)
        layout.addLayout(header_row)

        # ── Time filter row: jump to an install attempt, or pick a window ──
        self._updating_filters = False
        # A range is only applied once the user asks for one, so the default
        # view never hides lines behind a window the user did not choose.
        self._range_active = False
        self._open_end = False
        self._sessions = []
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        session_lbl = QLabel(tr("log_session_label"))
        session_lbl.setStyleSheet(f"color: {t.fg};")
        filter_row.addWidget(session_lbl)

        self._session_combo = QComboBox()
        self._session_combo.setToolTip(tr("log_session_tip"))
        self._session_combo.currentIndexChanged.connect(self._on_session_changed)
        filter_row.addWidget(self._session_combo, 1)

        from_lbl = QLabel(tr("log_range_from"))
        from_lbl.setStyleSheet(f"color: {t.fg};")
        filter_row.addWidget(from_lbl)

        self._from_edit = QDateTimeEdit()
        self._from_edit.setCalendarPopup(True)
        self._from_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._from_edit.setToolTip(tr("log_range_from_tip"))
        self._from_edit.dateTimeChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._from_edit)

        to_lbl = QLabel(tr("log_range_to"))
        to_lbl.setStyleSheet(f"color: {t.fg};")
        filter_row.addWidget(to_lbl)

        self._to_edit = QDateTimeEdit()
        self._to_edit.setCalendarPopup(True)
        self._to_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self._to_edit.setToolTip(tr("log_range_to_tip"))
        self._to_edit.dateTimeChanged.connect(self._on_filter_changed)
        filter_row.addWidget(self._to_edit)

        self._problems_check = QCheckBox(tr("log_problems_only"))
        self._problems_check.setToolTip(tr("log_problems_tip"))
        self._problems_check.toggled.connect(self._on_filter_changed)
        filter_row.addWidget(self._problems_check)

        layout.addLayout(filter_row)

        # ── Log View Area ──
        self._view = QTextEdit()
        self._view.setObjectName("logView")
        self._view.setReadOnly(True)
        font_family = "SF Mono, Menlo, Consolas, 'Courier New', monospace"
        self._view.setStyleSheet(
            f"QTextEdit#logView {{ font-family: {font_family}; font-size: 12px; line-height: 1.4;"
            f" background-color: {t.bg_input}; color: {t.fg}; border: 1px solid {t.border};"
            f" border-radius: 6px; padding: 6px; }}"
        )
        layout.addWidget(self._view, 1)

        # ── Action Buttons Bar: Go To File, Save File, Copy, Close ──
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        self._goto_btn = QPushButton(tr("log_btn_goto_file"))
        self._goto_btn.setToolTip("Reveal log file in Finder / Explorer")
        self._goto_btn.clicked.connect(self._on_goto_file)
        action_row.addWidget(self._goto_btn)

        self._save_btn = QPushButton(tr("log_btn_save_file"))
        self._save_btn.setToolTip("Export the displayed log file to disk")
        self._save_btn.clicked.connect(self._on_save_file)
        action_row.addWidget(self._save_btn)

        self._copy_btn = QPushButton(tr("log_btn_copy"))
        self._copy_btn.setToolTip("Copy displayed log to clipboard")
        self._copy_btn.clicked.connect(self._on_copy)
        action_row.addWidget(self._copy_btn)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"font-size: 11px; color: {t.ok_fg};")
        action_row.addWidget(self._status_label)

        # How much of the log the filter is showing — silent filtering is how a
        # user concludes the tool "lost" an install.
        self._count_label = QLabel("")
        self._count_label.setStyleSheet(f"font-size: 11px; color: {t.fg_dim};")
        action_row.addWidget(self._count_label)
        action_row.addStretch()

        self._close_btn = QPushButton(tr("close"))
        self._close_btn.setDefault(True)
        self._close_btn.clicked.connect(self.accept)
        action_row.addWidget(self._close_btn)

        layout.addLayout(action_row)

        self._empty = True
        self._custom_lines = list(lines) if lines is not None else None
        self._rebuild_session_combo()
        self._refresh_content()

    def current_category(self) -> str:
        return self._category_combo.currentData() or CAT_ALL

    def _on_category_changed(self, _index: int = 0):
        self._rebuild_session_combo()
        self._refresh_content()

    # -- date/time + install session filtering ------------------------------
    def raw_lines(self, cat: str | None = None) -> list[str]:
        """Unfiltered lines for a category, as the view would show them."""
        cat = cat or self.current_category()
        mgr = DiagnosticsManager.instance()
        # The caller may pass the lines it already has in memory, but an empty
        # list must not hide the persisted diagnostics: reopening the window
        # after an install is exactly how a finished run gets inspected.
        custom = list(self._custom_lines or [])
        if custom and cat == CAT_ALL:
            return custom
        lines = mgr.get_display_lines(cat)
        if not lines and custom:
            lines = [ln for ln in custom if mgr.classify_line(ln) == cat]
        return lines

    def _selected_session(self):
        data = self._session_combo.currentData()
        sessions = self._sessions or []
        for session in sessions:
            if session.number == data:
                return session
        return None

    def _rebuild_session_combo(self):
        """List the install attempts found in the current category's log."""
        self._sessions = collect_install_sessions(self.raw_lines())
        previous = self._session_combo.currentData()
        self._updating_filters = True
        try:
            self._session_combo.clear()
            self._session_combo.addItem(tr("log_session_all"), None)
            for session in reversed(self._sessions):  # newest first
                self._session_combo.addItem(self._session_label(session), session.number)
            idx = self._session_combo.findData(previous)
            self._session_combo.setCurrentIndex(idx if idx >= 0 else 0)
        finally:
            self._updating_filters = False
        self._sync_range_edits()

    def _session_label(self, session) -> str:
        stamp = session.started.strftime("%Y-%m-%d %H:%M:%S") if session.started else "?"
        status = {
            "ok": tr("log_session_ok"),
            "failed": tr("log_session_failed"),
        }.get(session.status, tr("log_session_running"))
        if session.success is False and session.detail:
            status = f"{status}: {session.detail}"
        target = session.package or tr("log_session_unknown_package")
        return f"{stamp} · {target} · {status}"

    def _sync_range_edits(self):
        """Point the From/To pickers at the selected install, or the whole log.

        An install still in progress (no completion banner yet) keeps an open
        end, so lines written while the user watches stay visible instead of
        being clipped the moment the picker's value goes stale.
        """
        log_start, log_end = log_time_range(self.raw_lines())
        now = datetime.now()
        session = self._selected_session()
        if session is not None:
            start = session.started or log_start or (now - timedelta(hours=1))
            end = session.ended
            self._open_end = session.running
        else:
            start = log_start or (now - timedelta(hours=1))
            end = log_end or now
            self._open_end = False
        if end is None:
            end = max(now, log_end or now)
        to_value = max(end, start)

        self._updating_filters = True
        try:
            self._from_edit.setDateTime(_to_qdatetime(start))
            self._to_edit.setDateTime(_to_qdatetime(to_value))
        finally:
            self._updating_filters = False

    def _on_session_changed(self, _index: int = 0):
        if self._updating_filters:
            return
        self._sync_range_edits()
        # "All installs" clears the window; a specific install narrows it.
        self._range_active = self._selected_session() is not None
        self._refresh_content(preserve_scroll=True)

    def _on_filter_changed(self, *_args):
        if self._updating_filters:
            return
        self._range_active = True
        self._refresh_content(preserve_scroll=True)

    def _filter_active(self) -> bool:
        try:
            return bool(self._problems_check.isChecked()) or self._range_active
        except AttributeError:
            return False

    def _filter_description(self) -> str:
        """Human-readable summary of the active filter, for exports."""
        parts = []
        session = self._selected_session()
        if session is not None:
            parts.append(self._session_label(session))
        else:
            parts.append(
                f"{_from_python(self._from_edit).strftime('%Y-%m-%d %H:%M:%S')} → "
                f"{_from_python(self._to_edit).strftime('%Y-%m-%d %H:%M:%S')}"
            )
        if self._problems_check.isChecked():
            parts.append(tr("log_problems_only"))
        return " · ".join(parts)

    def _refresh_content(self, preserve_scroll: bool = False):
        cat = self.current_category()
        self._file_badge.setText(get_log_file(cat).name)

        raw = self.raw_lines(cat)
        lines = self._apply_filters(raw)
        content = "\n".join(lines)

        scroll = 0
        if preserve_scroll:
            scroll = self._view.verticalScrollBar().value()

        if content.strip():
            self._view.setPlainText(content)
            self._empty = False
            if preserve_scroll:
                self._view.verticalScrollBar().setValue(scroll)
            else:
                cursor = self._view.textCursor()
                cursor.movePosition(QTextCursor.End)
                self._view.setTextCursor(cursor)
        else:
            self._view.setPlainText(tr("log_no_entries"))
            self._empty = True

        self._count_label.setText(
            tr("log_lines_count").format(shown=len(lines), total=len(raw))
            if self._filter_active()
            else tr("log_lines_total").format(total=len(raw))
        )

    def _apply_filters(self, lines: list[str]) -> list[str]:
        """Apply the date/time window and the problems-only switch."""
        try:
            problems = self._problems_check.isChecked()
        except AttributeError:
            return lines
        if not problems and not self._range_active:
            return lines
        start = _from_python(self._from_edit) if self._range_active else None
        end = None if self._open_end else (_from_python(self._to_edit) if self._range_active else None)
        return filter_log_lines(lines, start=start, end=end, problems_only=problems)

    def set_lines(self, lines):
        self._custom_lines = list(lines) if lines is not None else None
        self._rebuild_session_combo()
        self._refresh_content()

    def append_line(self, line):
        mgr = DiagnosticsManager.instance()
        line_str = str(line)
        # The line is normally recorded by the caller that emitted it; dedupe
        # keeps this display sink from storing the same event twice.
        mgr.record_log(line_str, dedupe=True)
        cat = self.current_category()
        line_cat = mgr.classify_line(line_str)

        if self._filter_active():
            # A filtered view must stay consistent: a line the filter excludes
            # cannot simply be appended, and the counts have to move.
            self._refresh_content()
            return

        if cat == CAT_ALL or cat == line_cat:
            if self._empty:
                # First live line: show the stored history first so an install
                # does not lose everything logged before the dialog opened.
                self._refresh_content()
                if not self._empty:
                    self._view.append(line_str)
                else:
                    self._view.setPlainText(line_str)
                    self._empty = False
            else:
                self._view.append(line_str)
            cursor = self._view.textCursor()
            cursor.movePosition(QTextCursor.End)
            self._view.setTextCursor(cursor)

    def _on_goto_file(self):
        cat = self.current_category()
        target_file = get_log_file(cat)
        if not target_file.is_file():
            try:
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(self._view.toPlainText(), encoding="utf-8")
            except Exception:
                pass
        reveal_in_file_manager(target_file)
        self._show_temp_status(f"Revealed in file manager: {target_file.name}")

    def _on_save_file(self):
        from pathlib import Path
        cat = self.current_category()
        default_name = f"updater_ce_{cat}_log.txt"
        default_path = str(Path.home() / default_name)

        path, _ = QFileDialog.getSaveFileName(
            self,
            tr("log_save_title"),
            default_path,
            "Text Files (*.log *.txt);;All Files (*)",
        )
        if not path:
            return

        try:
            content = self._view.toPlainText()
            if self._filter_active():
                # Record what the view was filtered to: a developer reading the
                # export must not mistake a time window for the whole log.
                header = tr("log_export_filter").format(
                    desc=self._filter_description(), category=tr("log_center")
                )
                content = f"# {header}\n{content}"
            Path(path).write_text(content, encoding="utf-8")
            self._show_temp_status(tr("log_save_success").format(path=Path(path).name))
        except Exception as e:
            self._show_temp_status(f"Error saving log: {e}")

    def _on_copy(self):
        from PySide6.QtWidgets import QApplication
        text = self._view.toPlainText()
        if text:
            clipboard = QApplication.clipboard()
            if clipboard:
                clipboard.setText(text)
            self._show_temp_status(tr("log_copied_hint"))

    def _show_temp_status(self, msg: str, timeout_ms: int = 3500):
        self._status_label.setText(msg)
        QTimer.singleShot(timeout_ms, lambda: self._status_label.setText(""))


class UpdateAvailableDialog(QDialog):
    def __init__(self, parent=None, info=None, current_version="", on_skip=None):
        super().__init__(parent)
        self._info = info
        self._on_skip = on_skip
        t = T()
        self.setWindowTitle(tr("update_available"))
        self.setMinimumWidth(520)
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(
            f"<h2 style='color:{t.fg_primary};'>{tr('update_available')} &#127881;</h2>"
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
            f"font-size: 12px; color: {t.fg_dim};"
            f" background-color: {t.info_bg};"
            f" border-radius: 8px; padding: 10px 14px;"
        )
        layout.addWidget(guide)

        self._asset = asset
        btn_row = QHBoxLayout()
        self._download_btn = QPushButton(tr("update_btn_download"))
        self._download_btn.setProperty("cssClass", "primary")
        self._download_btn.setDefault(True)
        self._download_btn.clicked.connect(self._on_download)
        self._later_btn = QPushButton(tr("update_btn_later"))
        self._later_btn.setProperty("cssClass", "ghost")
        self._later_btn.clicked.connect(self.reject)
        self._skip_btn = QPushButton(tr("update_btn_skip"))
        self._skip_btn.setProperty("cssClass", "ghost")
        self._skip_btn.clicked.connect(self._on_skip_version)
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
            from ..browser import open_browser
            open_browser(url)
        self.accept()

    def _on_skip_version(self):
        if self._on_skip:
            self._on_skip(self._info.version)
        self.reject()


class LinuxSetupDialog(QDialog):
    """User-friendly Linux environment onboarding and SP Flash Tool setup."""

    def __init__(self, parent=None, auto_start: bool = False):
        super().__init__(parent)
        from .. import linux_sp_flash
        self._linux = linux_sp_flash
        self._worker = None
        self._report = {}

        t = T()
        self.setWindowTitle(tr("linux_setup_title"))
        self.resize(700, 580)
        self.setMinimumWidth(580)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 20)
        main_layout.setSpacing(16)

        # Header
        header_row = QHBoxLayout()
        header_row.setSpacing(14)
        icon_badge = QLabel("⚡")
        icon_badge.setStyleSheet(
            f"font-size: 26px; background-color: {t.accent_bg}; color: {t.accent_text}; "
            f"border-radius: 16px; padding: 6px 12px;"
        )
        header_row.addWidget(icon_badge)

        header_text_layout = QVBoxLayout()
        header_text_layout.setSpacing(2)
        self._title_lbl = QLabel(tr("linux_setup_welcome"))
        self._title_lbl.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {t.fg};")
        self._desc_lbl = QLabel(tr("linux_setup_welcome_desc"))
        self._desc_lbl.setWordWrap(True)
        self._desc_lbl.setStyleSheet(f"font-size: 12px; color: {t.fg_dim};")
        header_text_layout.addWidget(self._title_lbl)
        header_text_layout.addWidget(self._desc_lbl)
        header_row.addLayout(header_text_layout)
        main_layout.addLayout(header_row)

        # Download / Staging Progress Bar
        self._progress_box = QWidget()
        prog_layout = QVBoxLayout(self._progress_box)
        prog_layout.setContentsMargins(0, 0, 0, 0)
        prog_layout.setSpacing(6)

        self._progress_msg = QLabel(tr("linux_card_downloading_badge"))
        self._progress_msg.setStyleSheet(f"font-size: 12px; color: {t.fg_dim}; font-weight: 500;")
        prog_layout.addWidget(self._progress_msg)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(False)
        # Left to the native QStyle: styling a progress bar with QSS replaces
        # the platform's own bar/track rendering.
        prog_layout.addWidget(self._progress_bar)
        main_layout.addWidget(self._progress_box)

        # Cards Container
        cards_layout = QVBoxLayout()
        cards_layout.setSpacing(10)

        # Card 1: Flashing Engine
        self._engine_card = QWidget()
        self._engine_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border}; "
            f"border-radius: 12px; padding: 12px; }}"
        )
        ec_layout = QHBoxLayout(self._engine_card)
        ec_layout.setContentsMargins(14, 12, 14, 12)
        ec_icon = QLabel("⚙️")
        ec_icon.setStyleSheet("font-size: 20px; border: none; background: transparent;")
        ec_layout.addWidget(ec_icon)

        ec_text = QVBoxLayout()
        ec_text.setSpacing(2)
        ec_title = QLabel(tr("linux_card_engine_title"))
        ec_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {t.fg}; border: none; background: transparent;")
        self._ec_desc = QLabel(tr("linux_card_engine_desc"))
        self._ec_desc.setStyleSheet(f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;")
        ec_text.addWidget(ec_title)
        ec_text.addWidget(self._ec_desc)
        ec_layout.addLayout(ec_text)
        ec_layout.addStretch()

        self._engine_badge = QLabel(tr("linux_card_downloading_badge"))
        self._engine_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.accent_bg}; color: {t.accent_text}; border: none;"
        )
        ec_layout.addWidget(self._engine_badge)
        cards_layout.addWidget(self._engine_card)

        # Card 2: USB Device Access (udev)
        self._usb_card = QWidget()
        self._usb_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border}; "
            f"border-radius: 12px; padding: 12px; }}"
        )
        uc_layout = QHBoxLayout(self._usb_card)
        uc_layout.setContentsMargins(14, 12, 14, 12)
        uc_icon = QLabel("🔌")
        uc_icon.setStyleSheet("font-size: 20px; border: none; background: transparent;")
        uc_layout.addWidget(uc_icon)

        uc_text = QVBoxLayout()
        uc_text.setSpacing(2)
        uc_title = QLabel(tr("linux_card_usb_title"))
        uc_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {t.fg}; border: none; background: transparent;")
        self._uc_desc = QLabel(tr("linux_card_usb_desc"))
        self._uc_desc.setStyleSheet(f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;")
        uc_text.addWidget(uc_title)
        uc_text.addWidget(self._uc_desc)
        uc_layout.addLayout(uc_text)
        uc_layout.addStretch()

        self._grant_btn = QPushButton(tr("linux_card_grant_btn"))
        self._grant_btn.setProperty("cssClass", "primary")
        self._grant_btn.clicked.connect(self._on_auto_configure)
        uc_layout.addWidget(self._grant_btn)

        self._usb_badge = QLabel(tr("linux_card_granted_badge"))
        self._usb_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;"
        )
        uc_layout.addWidget(self._usb_badge)
        cards_layout.addWidget(self._usb_card)

        # Card 3: System Environment
        self._sys_card = QWidget()
        self._sys_card.setStyleSheet(
            f"QWidget {{ background-color: {t.bg_card}; border: 1px solid {t.border}; "
            f"border-radius: 12px; padding: 12px; }}"
        )
        sc_layout = QHBoxLayout(self._sys_card)
        sc_layout.setContentsMargins(14, 12, 14, 12)
        sc_icon = QLabel("🐧")
        sc_icon.setStyleSheet("font-size: 20px; border: none; background: transparent;")
        sc_layout.addWidget(sc_icon)

        sc_text = QVBoxLayout()
        sc_text.setSpacing(2)
        sc_title = QLabel(tr("linux_card_system_title"))
        sc_title.setStyleSheet(f"font-size: 14px; font-weight: 700; color: {t.fg}; border: none; background: transparent;")
        self._sys_desc = QLabel("Linux x86_64")
        self._sys_desc.setStyleSheet(f"font-size: 12px; color: {t.fg_dim}; border: none; background: transparent;")
        sc_text.addWidget(sc_title)
        sc_text.addWidget(self._sys_desc)
        sc_layout.addLayout(sc_text)
        sc_layout.addStretch()

        self._sys_badge = QLabel(tr("linux_card_ready_badge"))
        self._sys_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;"
        )
        sc_layout.addWidget(self._sys_badge)
        cards_layout.addWidget(self._sys_card)

        main_layout.addLayout(cards_layout)

        # Technical diagnostics toggle & view (hidden by default)
        diag_toggle_row = QHBoxLayout()
        self._diag_toggle_btn = QPushButton(f"▸ {tr('linux_setup_view_log')}")
        self._diag_toggle_btn.setStyleSheet(
            f"color: {t.fg_dim}; border: none; background: transparent; font-size: 11px; text-align: left;"
        )
        self._diag_toggle_btn.clicked.connect(self._toggle_diagnostics)
        diag_toggle_row.addWidget(self._diag_toggle_btn)
        diag_toggle_row.addStretch()
        main_layout.addLayout(diag_toggle_row)

        self._status_view = QTextEdit()
        self._status_view.setReadOnly(True)
        self._status_view.setVisible(False)
        self._status_view.setFixedHeight(120)
        self._status_view.setStyleSheet(
            f"background-color: {t.bg}; color: {t.fg}; border: 1px solid {t.border}; "
            f"border-radius: 8px; font-family: monospace; font-size: 11px; padding: 8px;"
        )
        main_layout.addWidget(self._status_view)

        # Footer Actions
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._recheck_btn = QPushButton(tr("linux_setup_recheck"))
        self._recheck_btn.setProperty("cssClass", "ghost")
        self._recheck_btn.clicked.connect(lambda: self._start_staging(force_download=False))

        self._copy_btn = QPushButton(tr("linux_setup_copy_cmd"))
        self._copy_btn.setProperty("cssClass", "ghost")
        self._copy_btn.clicked.connect(self._on_copy_command)

        self._sp_gui_btn = QPushButton(tr("system_checker_launch_gui_btn"))
        self._sp_gui_btn.setProperty("cssClass", "ghost")
        self._sp_gui_btn.clicked.connect(self._on_launch_sp_gui)

        self._continue_btn = QPushButton(tr("linux_setup_done_btn"))
        self._continue_btn.setProperty("cssClass", "primary")
        self._continue_btn.setDefault(True)
        self._continue_btn.clicked.connect(self.accept)

        btn_row.addWidget(self._recheck_btn)
        btn_row.addWidget(self._copy_btn)
        btn_row.addWidget(self._sp_gui_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._continue_btn)
        main_layout.addLayout(btn_row)

        # Initial refresh or auto-start
        self.refresh_status()
        stage = self._linux.stage_dir()
        if auto_start and not self._linux.files_ready(stage):
            self._start_staging(force_download=False)
        else:
            self._progress_box.setVisible(not self._linux.files_ready(stage))

    def _stop_worker(self):
        if self._worker and self._worker.isRunning():
            try:
                self._worker.requestInterruption()
                if not self._worker.wait(800):
                    self._worker.terminate()
                    self._worker.wait(500)
            except Exception:
                pass

    def _start_staging(self, force_download: bool = False):
        t = T()
        self._progress_box.setVisible(True)
        self._progress_bar.setValue(10)
        self._progress_msg.setText(tr("linux_connecting_github"))
        self._engine_badge.setText(tr("linux_card_downloading_badge"))
        self._engine_badge.setStyleSheet(
            f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; "
            f"background-color: {t.accent_bg}; color: {t.accent_text}; border: none;"
        )

        self._worker = self._linux.LinuxStagingWorker(self, force_download=force_download)
        self._worker.progress.connect(self._on_staging_progress)
        self._worker.finished.connect(self._on_staging_finished)
        self._worker.start()

    def _on_staging_progress(self, pct: int, msg: str):
        self._progress_bar.setValue(pct)
        if msg:
            self._progress_msg.setText(msg)

    def _on_staging_finished(self, ok: bool, msg: str, report: dict):
        self._progress_bar.setValue(100)
        if ok:
            self._progress_msg.setText(tr("linux_engine_ready_msg"))
            QTimer.singleShot(1200, lambda: self._progress_box.setVisible(False))
        else:
            self._progress_msg.setText(tr("linux_setup_issue_fmt").format(msg=msg))
        self.refresh_status()

    def _toggle_diagnostics(self):
        is_visible = self._status_view.isVisible()
        self._status_view.setVisible(not is_visible)
        text_key = "linux_setup_view_log" if is_visible else "linux_setup_hide_log"
        arrow = "▸" if is_visible else "▾"
        self._diag_toggle_btn.setText(f"{arrow} {tr(text_key)}")

    def refresh_status(self):
        t = T()
        report = self._linux.run_system_checker()
        distro = report.get("distro", {})

        # System card
        self._sys_desc.setText(f"{distro.get('pretty_name', 'Linux')} ({distro.get('family', 'generic')}) • x86_64")
        if report.get("arch_ok"):
            self._sys_badge.setText(tr("linux_card_ready_badge"))
            self._sys_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
        else:
            self._sys_badge.setText(tr("linux_unsupported_badge"))
            self._sys_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.danger_bg}; color: {t.danger_fg}; border: none;")

        # Engine card
        if report.get("sp_exec_ok"):
            self._engine_badge.setText(tr("linux_card_ready_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
            self._ec_desc.setText(tr("linux_engine_staged_desc"))
        elif self._worker and self._worker.isRunning():
            self._engine_badge.setText(tr("linux_card_downloading_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.accent_bg}; color: {t.accent_text}; border: none;")
        else:
            self._engine_badge.setText(tr("linux_card_action_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.warn_bg}; color: {t.warn_fg}; border: none;")
            self._ec_desc.setText(tr("linux_engine_recheck_desc"))

        # USB permissions card
        if report.get("udev_ok"):
            self._usb_badge.setVisible(True)
            self._grant_btn.setVisible(False)
            self._usb_badge.setText(tr("linux_card_granted_badge"))
            self._usb_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
            self._uc_desc.setText(tr("linux_usb_rules_active_desc"))
        else:
            self._usb_badge.setVisible(False)
            self._grant_btn.setVisible(True)
            self._uc_desc.setText(tr("linux_usb_grant_desc"))

        # Bottom Continue button
        if report.get("overall_ready"):
            self._continue_btn.setEnabled(True)
            self._continue_btn.setProperty("cssClass", "primary")
        else:
            self._continue_btn.setEnabled(True)

        # Technical logs & full system checker checklist
        lines = []
        lines.append("=== SP Flash Tool Verification Report ===")
        lines.append(f"Target Distribution: {distro.get('pretty_name')} ({distro.get('family')})")
        lines.append(f"Overall Flashing Status: {'READY TO FLASH' if report.get('overall_ready') else 'ACTION NEEDED'}")
        lines.append("")
        lines.append("Diagnostic Checklist:")
        for item in report.get("items", []):
            mark = "[PASS]" if item["status"] == "ok" else ("[WARN]" if item["status"] in ("warn", "info") else "[FAIL]")
            lines.append(f"  {mark:<7} {item['title']}: {item['badge']}")
            lines.append(f"          Detail: {item['detail']}")
        lines.append("")
        lines.append(f"Stage Directory: {report.get('stage_dir')}")
        lines.append(f"Setup Script: {report.get('setup_script_path')}")
        self._status_view.setPlainText("\n".join(lines))
        self._report = report

    def _on_auto_configure(self):
        from PySide6.QtWidgets import QMessageBox
        ok, msg = self._linux.auto_fix_permissions()
        if ok:
            QMessageBox.information(
                self, tr("linux_perms_ok_title"), tr("linux_perms_ok_msg")
            )
        else:
            QMessageBox.warning(self, tr("perm_setup_title"), msg)
        self.refresh_status()

    def _on_copy_command(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        script_path = self._report.get("setup_script_path", "")
        cmd = f"sudo bash {script_path}"
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(cmd)
            QMessageBox.information(
                self, tr("linux_copied_title"),
                tr("linux_copied_msg_fmt").format(cmd=cmd)
            )

    def _on_launch_sp_gui(self):
        from .. import sp_flash_gui
        from PySide6.QtWidgets import QMessageBox
        ok, msg = sp_flash_gui.open_sp_flash_tool_gui()
        if not ok:
            QMessageBox.warning(self, tr("sp_gui_error_title"), msg)

    def closeEvent(self, event):
        self._stop_worker()
        super().closeEvent(event)

    def reject(self):
        self._stop_worker()
        super().reject()

    def accept(self):
        self._stop_worker()
        super().accept()


class ReleaseReminderDialog(QDialog):
    """Dialog alerting the user that a newer firmware release is available for their device."""

    def __init__(
        self,
        parent=None,
        update_info=None,
        on_view_release=None,
        on_disable_reminders=None,
        on_start_install=None,
        flash_method="",
    ):
        super().__init__(parent)
        self.update_info = update_info or {}
        self.on_view_release = on_view_release
        self.on_disable_reminders = on_disable_reminders
        self.on_start_install = on_start_install
        self.flash_method = flash_method
        self._install_started = False

        model = self.update_info.get("model", "Y1")
        device_label = device_label_for_model(model)
        sw_name = self.update_info.get("software_name", "Firmware")
        installed_lbl = (
            self.update_info.get("installed_label")
            or self.update_info.get("installed_tag")
            or ""
        )
        latest_lbl = (
            self.update_info.get("latest_label")
            or self.update_info.get("latest_tag")
            or ""
        )

        self.setWindowTitle(tr("reminder_new_release_title"))
        self.setMinimumWidth(480)
        t = T()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # Header with title and device badge
        hdr_row = QHBoxLayout()
        title_lbl = QLabel(
            f"<h3 style='margin:0; color:{t.fg_primary};'>{tr('reminder_new_release_title')}</h3>"
        )
        title_lbl.setTextFormat(Qt.RichText)
        hdr_row.addWidget(title_lbl, 1)

        badge = QLabel(device_label)
        badge.setStyleSheet(
            f"background-color: {t.accent}; color: {t.accent_text};"
            f" border-radius: 10px; font-size: 11px; font-weight: 700; padding: 3px 10px;"
        )
        hdr_row.addWidget(badge)
        layout.addLayout(hdr_row)

        # Message
        msg = QLabel(
            tr("reminder_new_release_msg").format(
                device=device_label, software=sw_name
            )
        )
        msg.setTextFormat(Qt.RichText)
        msg.setWordWrap(True)
        msg.setStyleSheet(f"font-size: 13px; color: {t.fg_dim}; line-height: 1.4;")
        layout.addWidget(msg)

        # Comparison Card
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background-color: {t.bg_card};"
            f" border: 1px solid {t.border};"
            f" border-radius: 10px; padding: 12px; }}"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(8)

        inst_row = QHBoxLayout()
        inst_title = QLabel(tr("reminder_installed_version"))
        inst_title.setStyleSheet(f"color: {t.fg_dim}; font-size: 12px;")
        inst_val = QLabel(installed_lbl)
        inst_val.setStyleSheet(f"color: {t.fg}; font-size: 12px; font-weight: 600;")
        inst_row.addWidget(inst_title)
        inst_row.addStretch()
        inst_row.addWidget(inst_val)
        card_layout.addLayout(inst_row)

        latest_row = QHBoxLayout()
        latest_title = QLabel(tr("reminder_latest_version"))
        latest_title.setStyleSheet(f"color: {t.fg_primary}; font-size: 12px; font-weight: 600;")
        latest_val = QLabel(latest_lbl)
        latest_val.setStyleSheet(f"color: {t.ok_fg}; font-size: 12px; font-weight: 700;")
        latest_row.addWidget(latest_title)
        latest_row.addStretch()
        latest_row.addWidget(latest_val)
        card_layout.addLayout(latest_row)

        layout.addWidget(card)

        # Method explanation & install prompt
        if paths.IS_MAC:
            method_str = tr("flash_method_mtk")
        else:
            m_lower = (self.flash_method or "").lower()
            if m_lower == "sp":
                method_str = tr("flash_method_sp")
            elif m_lower in ("mtk", "mtk_mac"):
                method_str = tr("flash_method_mtk")
            elif self.flash_method:
                method_str = self.flash_method
            else:
                method_str = ""

        if method_str:
            prompt_text = tr("reminder_prompt_install").format(method=method_str)
        else:
            prompt_text = tr("reminder_prompt_install_generic")

        self._prompt_lbl = QLabel(prompt_text)
        self._prompt_lbl.setWordWrap(True)
        self._prompt_lbl.setStyleSheet(f"font-size: 13px; color: {t.fg}; font-weight: 500; line-height: 1.4;")
        layout.addWidget(self._prompt_lbl)

        # Don't remind checkbox
        self.cb_dont_remind = QCheckBox(tr("reminder_dont_remind_device"))
        layout.addWidget(self.cb_dont_remind)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_dismiss = QPushButton(tr("reminder_dismiss"))
        btn_dismiss.setProperty("cssClass", "ghost")
        btn_dismiss.clicked.connect(self._on_dismiss)
        btn_layout.addWidget(btn_dismiss)

        self._btn_install = QPushButton(tr("reminder_start_install"))
        self._btn_install.setProperty("cssClass", "primary")
        self._btn_install.setDefault(True)
        self._btn_install.clicked.connect(self._on_start_install)
        btn_layout.addWidget(self._btn_install)

        layout.addLayout(btn_layout)

    def _check_disable_opt_out(self):
        if self.cb_dont_remind.isChecked():
            model = self.update_info.get("model", "")
            if callable(self.on_disable_reminders) and model:
                self.on_disable_reminders(model)

    def _on_dismiss(self):
        self._check_disable_opt_out()
        self.reject()

    def _on_start_install(self):
        self._install_started = True
        self._check_disable_opt_out()
        self.accept()
        if callable(self.on_start_install):
            self.on_start_install(self.update_info)
        elif callable(self.on_view_release):
            self.on_view_release(self.update_info)

    def _on_view(self):
        self._on_start_install()


# Alias for cross-platform and explicit system checking invocations
SystemCheckerDialog = LinuxSetupDialog

