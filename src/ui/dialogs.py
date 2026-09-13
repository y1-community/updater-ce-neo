"""Modal dialogs — flash complete / failed / diagnostics / update available."""

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import install_power_on_steps, device_label_for_model
from ..i18n import tr
from ..updates import asset_hint, pick_platform_asset
from .dark import T


class FlashCompleteDialog(QDialog):
    def __init__(self, parent=None, package_name="", elapsed="00:00", model="Y1"):
        super().__init__(parent)
        t = T()
        self.setWindowTitle(tr("flash_complete"))
        self.setMinimumWidth(420)
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


class DiagnosticsDialog(QDialog):
    def __init__(self, parent=None, lines=None):
        super().__init__(parent)
        self.setWindowTitle(tr("log_center"))
        self.resize(640, 420)
        layout = QVBoxLayout(self)
        self._view = QTextEdit()
        self._view.setObjectName("logView")
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
        if self._empty:
            self._view.setPlainText(str(line))
            self._empty = False
        else:
            self._view.append(str(line))


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
        self._download_btn.setCursor(Qt.PointingHandCursor)
        self._download_btn.clicked.connect(self._on_download)
        self._later_btn = QPushButton(tr("update_btn_later"))
        self._later_btn.setProperty("cssClass", "ghost")
        self._later_btn.setCursor(Qt.PointingHandCursor)
        self._later_btn.clicked.connect(self.reject)
        self._skip_btn = QPushButton(tr("update_btn_skip"))
        self._skip_btn.setProperty("cssClass", "ghost")
        self._skip_btn.setCursor(Qt.PointingHandCursor)
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
        self._progress_bar.setFixedHeight(12)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setStyleSheet(
            f"QProgressBar {{ background-color: {t.bg_input}; border: 1px solid {t.border}; "
            f"border-radius: 6px; }}"
            f"QProgressBar::chunk {{ background-color: {t.accent}; border-radius: 5px; }}"
        )
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
        self._grant_btn.setCursor(Qt.PointingHandCursor)
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
        self._diag_toggle_btn.setCursor(Qt.PointingHandCursor)
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
        self._recheck_btn.setCursor(Qt.PointingHandCursor)
        self._recheck_btn.clicked.connect(lambda: self._start_staging(force_download=False))

        self._copy_btn = QPushButton(tr("linux_setup_copy_cmd"))
        self._copy_btn.setProperty("cssClass", "ghost")
        self._copy_btn.setCursor(Qt.PointingHandCursor)
        self._copy_btn.clicked.connect(self._on_copy_command)

        self._sp_gui_btn = QPushButton(tr("system_checker_launch_gui_btn"))
        self._sp_gui_btn.setProperty("cssClass", "ghost")
        self._sp_gui_btn.setCursor(Qt.PointingHandCursor)
        self._sp_gui_btn.clicked.connect(self._on_launch_sp_gui)

        self._continue_btn = QPushButton(tr("linux_setup_done_btn"))
        self._continue_btn.setProperty("cssClass", "primary")
        self._continue_btn.setCursor(Qt.PointingHandCursor)
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
        needs_staging = not self._linux.files_ready(stage) or auto_start
        if needs_staging:
            self._start_staging(force_download=False)
        else:
            self._progress_box.setVisible(False)

    def _start_staging(self, force_download: bool = False):
        t = T()
        self._progress_box.setVisible(True)
        self._progress_bar.setValue(10)
        self._progress_msg.setText("Connecting to GitHub...")
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
            self._progress_msg.setText("Flashing engine ready.")
            QTimer.singleShot(1200, lambda: self._progress_box.setVisible(False))
        else:
            self._progress_msg.setText(f"Setup issue: {msg}")
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
            self._sys_badge.setText("Unsupported")
            self._sys_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.danger_bg}; color: {t.danger_fg}; border: none;")

        # Engine card
        if report.get("sp_exec_ok"):
            self._engine_badge.setText(tr("linux_card_ready_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
            self._ec_desc.setText("MediaTek SP Flash Tool staged with libpng12 compatibility.")
        elif self._worker and self._worker.isRunning():
            self._engine_badge.setText(tr("linux_card_downloading_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.accent_bg}; color: {t.accent_text}; border: none;")
        else:
            self._engine_badge.setText(tr("linux_card_action_badge"))
            self._engine_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.warn_bg}; color: {t.warn_fg}; border: none;")
            self._ec_desc.setText("Click Re-check or restart setup to download package.")

        # USB permissions card
        if report.get("udev_ok"):
            self._usb_badge.setVisible(True)
            self._grant_btn.setVisible(False)
            self._usb_badge.setText(tr("linux_card_granted_badge"))
            self._usb_badge.setStyleSheet(f"font-size: 11px; font-weight: 700; padding: 4px 10px; border-radius: 10px; background-color: {t.ok_bg}; color: {t.ok_fg}; border: none;")
            self._uc_desc.setText("MediaTek rules active (/etc/udev/rules.d/). Desktop access enabled.")
        else:
            self._usb_badge.setVisible(False)
            self._grant_btn.setVisible(True)
            self._uc_desc.setText("Grant permissions once so the app can communicate with your device.")

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
                self, "Permissions Configured",
                "USB permissions & udev rules installed successfully. Services configured."
            )
        else:
            QMessageBox.warning(self, "Permission Setup", msg)
        self.refresh_status()

    def _on_copy_command(self):
        from PySide6.QtWidgets import QApplication, QMessageBox
        script_path = self._report.get("setup_script_path", "")
        cmd = f"sudo bash {script_path}"
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(cmd)
            QMessageBox.information(
                self, "Copied",
                f"Setup command copied to clipboard:\n\n{cmd}\n\nRun this in a terminal to install rules and reload udev."
            )

    def _on_launch_sp_gui(self):
        from .. import sp_flash_gui
        from PySide6.QtWidgets import QMessageBox
        ok, msg = sp_flash_gui.open_sp_flash_tool_gui()
        if not ok:
            QMessageBox.warning(self, "SP Flash Tool GUI", msg)

    def closeEvent(self, event):
        if self._worker and self._worker.isRunning():
            self._worker.wait(1000)
        super().closeEvent(event)

    def reject(self):
        if self._worker and self._worker.isRunning():
            self._worker.wait(1000)
        super().reject()

    def accept(self):
        if self._worker and self._worker.isRunning():
            self._worker.wait(1000)
        super().accept()


class ReleaseReminderDialog(QDialog):
    """Dialog alerting the user that a newer firmware release is available for their device."""

    def __init__(
        self,
        parent=None,
        update_info=None,
        on_view_release=None,
        on_disable_reminders=None,
    ):
        super().__init__(parent)
        self.update_info = update_info or {}
        self.on_view_release = on_view_release
        self.on_disable_reminders = on_disable_reminders

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
        self.setMinimumWidth(460)
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
            f"background-color: {t.accent}; color: #ffffff;"
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
        msg.setStyleSheet("font-size: 13px; color: #cbd5e1; line-height: 1.4;")
        layout.addWidget(msg)

        # Comparison Card
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: rgba(255, 255, 255, 0.04);"
            " border: 1px solid rgba(255, 255, 255, 0.1);"
            " border-radius: 10px; padding: 12px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(8)

        inst_row = QHBoxLayout()
        inst_title = QLabel(tr("reminder_installed_version"))
        inst_title.setStyleSheet("color: #64748b; font-size: 12px;")
        inst_val = QLabel(installed_lbl)
        inst_val.setStyleSheet("color: #94a3b8; font-size: 12px; font-weight: 600;")
        inst_row.addWidget(inst_title)
        inst_row.addStretch()
        inst_row.addWidget(inst_val)
        card_layout.addLayout(inst_row)

        latest_row = QHBoxLayout()
        latest_title = QLabel(tr("reminder_latest_version"))
        latest_title.setStyleSheet("color: #38bdf8; font-size: 12px; font-weight: 600;")
        latest_val = QLabel(latest_lbl)
        latest_val.setStyleSheet(f"color: {t.ok_fg}; font-size: 12px; font-weight: 700;")
        latest_row.addWidget(latest_title)
        latest_row.addStretch()
        latest_row.addWidget(latest_val)
        card_layout.addLayout(latest_row)

        layout.addWidget(card)

        # Don't remind checkbox
        self.cb_dont_remind = QCheckBox(tr("reminder_dont_remind_device"))
        self.cb_dont_remind.setCursor(Qt.PointingHandCursor)
        self.cb_dont_remind.setStyleSheet("font-size: 12px; color: #94a3b8;")
        layout.addWidget(self.cb_dont_remind)

        # Action Buttons
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_dismiss = QPushButton(tr("reminder_dismiss"))
        btn_dismiss.setProperty("cssClass", "ghost")
        btn_dismiss.setCursor(Qt.PointingHandCursor)
        btn_dismiss.clicked.connect(self._on_dismiss)
        btn_layout.addWidget(btn_dismiss)

        btn_view = QPushButton(tr("reminder_view_release"))
        btn_view.setProperty("cssClass", "primary")
        btn_view.setCursor(Qt.PointingHandCursor)
        btn_view.clicked.connect(self._on_view)
        btn_layout.addWidget(btn_view)

        layout.addLayout(btn_layout)

    def _check_disable_opt_out(self):
        if self.cb_dont_remind.isChecked():
            model = self.update_info.get("model", "")
            if callable(self.on_disable_reminders) and model:
                self.on_disable_reminders(model)

    def _on_dismiss(self):
        self._check_disable_opt_out()
        self.reject()

    def _on_view(self):
        self._check_disable_opt_out()
        self.accept()
        if callable(self.on_view_release):
            self.on_view_release(self.update_info)


# Alias for cross-platform and explicit system checking invocations
SystemCheckerDialog = LinuxSetupDialog

