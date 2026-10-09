"""Main window — left navigation + right content area.

Select Software is the application's start page (no separate Home tab). After
a package is chosen, the flash backend launches immediately — SP Flash Tool
(Windows / staged Linux) or mtkclient (all platforms) — and the UI guides the
user to power off and connect their device while the backend searches USB.

The install method is chosen in Settings (Windows/Linux default to SP Flash
Tool's console-mode XML flow; macOS has MTKClient only) and can be switched
while waiting for a device, which restarts the run with the new backend.
"""

import logging
import os
import platform
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QEventLoop,
    QProcess,
    QProcessEnvironment,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import device_tracking
from .. import paths
from ..config import (
    APP_NAME,
    APP_VERSION,
    UPDATE_CHECK_STARTUP_DELAY_MS,
    UPDATE_REPO,
    install_power_on_steps,
    is_generic_mtk,
    is_mediatek_installer,
    is_offline_mode,
    get_app_name,
    get_brand_name,
)
from ..manifest import ManifestWorker
from ..updates import UpdateCheckWorker, UpdateInfo
from ..donation_dialog import DonationDialog, DonationStatusBar
from ..donors import cached_donors_path, load_donors_file, parse_donors_csv_text
from ..flash_service import (
    LINE_CONNECT_HINT,
    LINE_RETRY,
    METHOD_MTK_MAC,
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_WAITING,
    STEP_WRITE,
    FlashService,
    classify_backend_line,
    completed_extract_dir,
    normalise_method,
)
from ..i18n import tr, tr_brand, translator
from ..state import FlashState, StateMachine
from .dialogs import (
    DiagnosticsView,
    FlashCompleteDialog,
    ReleaseReminderDialog,
    RetryGuidanceDialog,
    UpdateAvailableDialog,
)
from .dark import T, ThemeWatcher, is_dark
from .glass import (
    HEADER_CONTENT_HEIGHT,
    apply_glass,
    apply_windows_acrylic,
    apply_windows_dark_titlebar,
    disable_window_maximize,
    ensure_window_maximize_enabled,
    set_window_close_button_enabled,
)
from .error_page import ErrorPage
from .flash_page import FlashPage
from .retry_page import RetryPage
from .select_page import SelectPackagePage
from .settings_page import SettingsPage
from .icons import get_symbol_icon
from .widgets import CurrentPageStack
from .sidebar import SidebarButton, sidebar_row_min_height, sidebar_row_spacing
from .widgets import CurrentPageStack

logger = logging.getLogger(__name__)

_PAGE_SELECT = 0
_PAGE_FLASH = 1
_PAGE_ERROR = 2
_PAGE_RETRY = 3
_PAGE_SETTINGS = 4
_PAGE_DIAGNOSTICS = 5
_PAGE_NOTICE = 6

# Choose Software, and the app's ordinary size. Measured from the running
# window on this desktop: 680×480. A desktop that draws its own title bar
# adds that bar outside this size. When the window controls share the brand
# row, this size already includes it. The floor is a little smaller so the
# window can still shrink.
DEFAULT_WINDOW_WIDTH = 680
DEFAULT_WINDOW_HEIGHT = 480
MINIMUM_WINDOW_WIDTH = 640
MINIMUM_WINDOW_HEIGHT = 440


def default_window_size() -> tuple[int, int]:
    """Client size for Choose Software and the rest of the ordinary UI."""
    return DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT

# The sidebar brand title and the version badge beside it share one size.
_BRAND_FONT_SIZE = "14px"


class DeviceUpdateCheckWorker(QThread):
    finished = Signal(list)

    def __init__(self, settings=None, ignore_last_notified=False, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.ignore_last_notified = ignore_last_notified

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            updates = device_tracking.check_device_updates(
                settings=self.settings,
                ignore_last_notified=self.ignore_last_notified,
            )
            if self.isInterruptionRequested():
                return
            self.finished.emit(updates or [])
        except Exception as e:
            logger.debug("Device update check failed: %s", e)
            if not self.isInterruptionRequested():
                self.finished.emit([])

_STEP_KEY = {
    STEP_EXTRACTING: "step_extract",
    STEP_WAITING: "step_wait",
    STEP_DETECT: "step_detect",
    STEP_DOWNLOAD_DA: "step_download_da",
    STEP_DOWNLOAD_BL: "step_download_bl",
    STEP_WRITE: "step_write",
    STEP_DONE: "step_done",
}

_WRITE_STEPS = (STEP_DOWNLOAD_DA, STEP_DOWNLOAD_BL, STEP_WRITE)

# Failure codes whose only real fix is the hardware reset described by
# RetryGuidanceDialog: the device stopped answering the handshake.
_RETRY_GUIDANCE_CODES = ("CONNECTION_FAILED", "MTK_INIT_FAILED")

# States in which a flash run is under way: the sidebar's first entry becomes
# "Install Software" and holds the accent highlight until the run finishes.
_INSTALL_RUN_STATES = (
    FlashState.S2_WAIT_CONNECTION,
    FlashState.S3_DEVICE_DETECTED,
    FlashState.S4_FLASHING,
    FlashState.S6_RETRYING,
)


class DraggableHeaderBar(QWidget):
    """Draggable header row: brand icon, title, and the active page name.

    It does not paint its own background. A custom clear here used to punch
    through the window backing store and leave the previous title on screen.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(int(HEADER_CONTENT_HEIGHT))
        self.setAutoFillBackground(False)
        self._dragging = False
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            is_interactive = False
            w = child
            while w and w is not self:
                if isinstance(w, (QPushButton, QComboBox)) or (
                    isinstance(w, QLabel) and (w.textInteractionFlags() & Qt.LinksAccessibleByMouse)
                ):
                    is_interactive = True
                    break
                w = w.parentWidget()
            if not is_interactive:
                win = self.window()
                if win:
                    dragged = False
                    if sys.platform == "darwin":
                        try:
                            import AppKit
                            from .glass import _get_nsview
                            ns_app = AppKit.NSApplication.sharedApplication()
                            curr_evt = ns_app.currentEvent()
                            if curr_evt:
                                view = _get_nsview(win)
                                ns_win = view.window() if view else None
                                if ns_win:
                                    ns_win.performWindowDragWithEvent_(curr_evt)
                                    dragged = True
                        except Exception:
                            pass
                    if not dragged and hasattr(win, "windowHandle") and win.windowHandle():
                        try:
                            dragged = win.windowHandle().startSystemMove()
                        except Exception:
                            pass
                    if dragged:
                        event.accept()
                        return
                    self._dragging = True
                    self._drag_offset = event.globalPosition().toPoint() - win.pos()
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_dragging", False) and (event.buttons() & Qt.LeftButton):
            win = self.window()
            if win and getattr(self, "_drag_offset", None) is not None:
                win.move(event.globalPosition().toPoint() - self._drag_offset)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)


def client_rect_for_unified_caption(rect, *, maximized: bool, frame_x: int, frame_y: int, padded: int):
    """Client edges when the caption text band is part of the window.

    Restored windows keep the side and bottom frame and pull the top edge up
    to the window top, so the in-app title sits where the native caption was.
    Maximized windows inset every side by the frame thickness.
    """
    left, top, right, bottom = rect
    if maximized:
        inset_x = int(frame_x) + int(padded)
        inset_y = int(frame_y) + int(padded)
        return (left + inset_x, top + inset_y, right - inset_x, bottom - inset_y)
    return (left + int(frame_x), top, right - int(frame_x), bottom - int(frame_y))


def can_inline_title_with_window_controls() -> bool:
    """Return True if the current platform/DE supports inlining custom title/controls seamlessly."""
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32" or platform.system() == "Windows":
        return True
    if os.environ.get("UPDATER_INLINE_TITLE") == "1":
        return True
    if os.environ.get("UPDATER_TRADITIONAL_TITLE") == "1":
        return False
    desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()
    return any(d in desktop for d in ("gnome", "unity", "pantheon"))


# Floor for the compact install window: the progress card plus whatever
# chrome (header, status line) the current scenario shows must fit.
COMPACT_INSTALL_MIN_HEIGHT = 188
COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS = 168


def compact_install_height(
    page_h: int,
    prompt_h: int,
    status_h: int,
    inline_header: bool,
    donations_disabled: bool,
) -> int:
    """Height the window needs to hug the install card, on any desktop.

    The in-app header bar is a real 44px row of the window on every platform
    that draws one: inside the central layout on Windows and Linux, and above
    the body on macOS. It therefore belongs in the required height everywhere.
    Only the macOS build used to count it, which left the Windows/Linux window
    exactly one header short and clipped the bottom status line.
    """
    header_h = int(HEADER_CONTENT_HEIGHT) if inline_header else 0
    height = max(COMPACT_INSTALL_MIN_HEIGHT, page_h + prompt_h + status_h + header_h)
    if donations_disabled:
        # Without the appeal under the card the status bar is the only extra
        # row, so the window can give its height back.
        height = max(COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS, height - status_h)
    return height


def page_title_alignment() -> Qt.AlignmentFlag:
    """Where the large page title sits in the header.

    Controls on the left (macOS, and desktops that put close/minimize there)
    leave the trailing edge of the window free, so the title's last glyph
    lines up with that edge. Controls on the right keep the title at the start,
    beside the app name.
    """
    if are_window_controls_on_left():
        return Qt.AlignRight | Qt.AlignVCenter
    return Qt.AlignLeft | Qt.AlignVCenter


def are_window_controls_on_left() -> bool:
    """Return True if window controls (close/minimize/zoom) are on the left (e.g. macOS)."""
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32" or platform.system() == "Windows":
        return False
    try:
        import subprocess

        res = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.wm.preferences", "button-layout"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        if res.returncode == 0:
            val = res.stdout.strip().strip("'\"")
            if ":" in val:
                left_part, _ = val.split(":", 1)
                return any(x in left_part for x in ("close", "minimize", "maximize"))
    except Exception:
        pass
    return False


class MainWindow(QMainWindow):
    log_line_added = Signal(str)

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        flags = self.windowFlags() | Qt.WindowCloseButtonHint | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
        self.setWindowFlags(flags)
        ensure_window_maximize_enabled(self)
        self.resize(*default_window_size())
        self.setMinimumSize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)

        self.sm = StateMachine(self)
        self.service = FlashService(self)
        self.settings = QSettings("innioasis", "updater")
        translator().set_language(str(self.settings.value("language", "en")))

        self._package_path = ""
        self._package_name = ""
        self._package_model = ""
        self._log_lines = []
        # Legacy "auto" values resolve to the platform default so users are
        # steered to SP Flash Tool's console-mode XML flow (MTKClient on macOS).
        self._flash_method = normalise_method(self.settings.value("flash_method", ""))
        self._last_progress = 0
        self._flash_start_ts = 0.0
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._step_now = ""
        self._update_offered_once = False
        self._update_manual_pending = False
        self._pre_install_guided = False
        self._install_ui_active = False
        self._download_active = False
        self._install_failed = False
        self._power_off_prompt = False
        self._diagnostics_session = False
        # A stalled connection makes the backend repeat the same errno line
        # many times over; this keeps the guidance dialog to once per attempt.
        self._active_workers = set()
        self._build_ui()
        self._connect_signals()
        self._nav_to_page(_PAGE_SELECT)

        self._manifest_worker = ManifestWorker(self)
        self._active_workers.add(self._manifest_worker)
        self._manifest_worker.finished.connect(
            lambda entries: self._on_manifest_worker_finished(self._manifest_worker, entries)
        )
        self._manifest_worker.start()
        QTimer.singleShot(UPDATE_CHECK_STARTUP_DELAY_MS, self._start_auto_update_check)
        QTimer.singleShot(
            UPDATE_CHECK_STARTUP_DELAY_MS + 1000,
            lambda: self._check_device_firmware_updates(manual=False),
        )

        if platform.system() == "Linux" and not paths.IS_MAC:
            # macOS has no SP Flash Tool or Linux setup wizard.
            QTimer.singleShot(600, self._check_linux_first_run)

        if not paths.IS_MAC:
            self._sp_history_timer = QTimer(self)
            self._sp_history_timer.setInterval(30000)
            self._sp_history_timer.timeout.connect(self._sync_sp_history)
            self._sp_history_timer.start()

    def _sync_sp_history(self):
        try:
            from .. import sp_flash_gui
            if sp_flash_gui.is_sp_flash_gui_supported():
                sp_flash_gui.update_sp_history_ini(model=getattr(self, "_package_model", ""))
        except Exception:
            pass

    def _on_manifest_worker_finished(self, worker, entries):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_manifest_worker", None):
            self._on_manifest_loaded(entries)

    def _paint_opaque(self, widget, color: str | None = None):
        """Fill a surface so the next paint replaces the previous frame.

        On Liquid Glass and WinUI acrylic the backdrop stays visible: the
        widget replaces its rect with transparent pixels instead of a solid
        theme color. Linux keeps the solid fill. This does not use
        CompositionMode_Clear.
        """
        if widget is None:
            return
        from .surfaces import glass_surfaces_enabled, show_glass_backdrop
        if glass_surfaces_enabled():
            show_glass_backdrop(widget)
            return
        widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        widget.setAutoFillBackground(True)
        tokens = T()
        fill = QColor(color or tokens.bg)
        fg = QColor(tokens.fg)
        pal = widget.palette()
        pal.setColor(QPalette.ColorRole.Window, fill)
        pal.setColor(QPalette.ColorRole.Base, fill)
        pal.setColor(QPalette.ColorRole.WindowText, fg)
        pal.setColor(QPalette.ColorRole.Text, fg)
        pal.setColor(QPalette.ColorRole.ButtonText, fg)
        widget.setPalette(pal)

    def _build_ui(self):
        central = QWidget(self)
        central.setObjectName("centralWidget")
        # Opaque client area. A translucent central widget keeps the previous
        # page's pixels, so the next screen is painted on top of the last one.
        self._paint_opaque(central)
        self.setCentralWidget(central)
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        # 1. Unified Draggable Title / Header Bar at the top (if supported)
        is_inline = can_inline_title_with_window_controls()
        is_mac = sys.platform == "darwin"
        if is_inline:
            self._title_bar = self._build_header_bar()
            if is_mac:
                self._title_bar.setParent(self)
                self._title_bar.setGeometry(0, 0, self.width(), int(HEADER_CONTENT_HEIGHT))
                self._title_bar.raise_()
                # On macOS, centralWidget is inset by 32px by Cocoa QPA.
                # Setting 12px top margin ensures body begins cleanly at y = 44.
                central_layout.setContentsMargins(0, 12, 0, 0)
            else:
                central_layout.addWidget(self._title_bar)
        else:
            self._title_bar = None
            central_layout.setContentsMargins(0, 0, 0, 0)

        # 2. Body row: Navigation sidebar on left, stacked pages on right
        body = QWidget()
        body.setObjectName("mainBody")
        self._paint_opaque(body)
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_nav())

        self._stack = CurrentPageStack()
        self._paint_opaque(self._stack)
        self._select_page = SelectPackagePage()
        self._flash_page = FlashPage()
        self._error_page = ErrorPage()
        self._retry_page = RetryPage()
        self._settings_page = SettingsPage()
        # The diagnostics console is built the first time it is opened. Until
        # D is pressed it is not constructed, so it cannot tail, relayout, or
        # poll. Log lines still go to DiagnosticsManager.
        self._diagnostics_page = None
        self._diag_placeholder = QWidget()
        self._diag_placeholder.setObjectName("diagnosticsPlaceholder")

        # Mount select_page._title into the unified header bar if inlining
        if is_inline and hasattr(self, "_title_container"):
            self._title_container.layout().addWidget(self._select_page._title)
            from .surfaces import seal_updating_text
            seal_updating_text(self._select_page._title)
            self._align_page_title()

        self._notice_host = QWidget()
        self._notice_host.setObjectName("noticeHost")
        notice_layout = QVBoxLayout(self._notice_host)
        notice_layout.setContentsMargins(16, 12, 16, 12)
        self._paint_opaque(self._notice_host)

        for w in (
            self._select_page,
            self._flash_page,
            self._error_page,
            self._retry_page,
            self._settings_page,
            self._diag_placeholder,
            self._notice_host,
        ):
            self._paint_opaque(w)
            self._stack.addWidget(w)
        content_col = QWidget()
        content_col.setObjectName("contentColumn")
        self._paint_opaque(content_col)
        content_layout = QVBoxLayout(content_col)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._stack, 1)
        self._inline_prompt = self._build_inline_prompt()
        content_layout.addWidget(self._inline_prompt)
        outer.addWidget(content_col, 1)
        central_layout.addWidget(body, 1)

        self._settings_page.donation_visibility_changed.connect(
            self._apply_donation_visibility
        )
        self._settings_page.offline_mode_changed.connect(
            self._on_offline_mode_changed
        )
        self._settings_page.release_filters_changed.connect(
            self._select_page.refresh_release_filters
        )
        self._settings_page.check_updates_requested.connect(
            lambda: self._check_device_firmware_updates(manual=True)
        )

        self._donations = parse_donors_csv_text(load_donors_file([
            cached_donors_path(),
            paths.RESOURCES_DIR / "donors.csv",
        ]) or "")
        self.setStatusBar(DonationStatusBar(
            parent=self,
            donations=self._donations,
            on_support=self._on_support_clicked,
            on_credits=self._open_credits,
            on_donations_updated=self._on_donations_updated,
        ))
        # Credits / Thanks lives at the left of the donation bar (it hides with
        # the rest of the bar, and with the online resources it links to).
        self._credits_btn = self.statusBar().credits_link()
        self.statusBar().messageChanged.connect(self._on_status_message_changed)
        self._select_page.status_message.connect(self._show_status)
        self._apply_donation_visibility()
        self._apply_generic_mtk_branding()

        # Live theme watcher for OS light/dark and accent color changes. One per
        # application: a watcher that is installed twice would refresh every
        # widget twice for the same host change, which is pure repaint churn on
        # a blur-composited window.
        app = QApplication.instance()
        self._theme_watcher = getattr(app, "_theme_watcher", None) if app is not None else None
        if self._theme_watcher is None and app is not None:
            self._theme_watcher = ThemeWatcher(app, parent=self, on_apply=self._on_theme_changed)
            self._theme_watcher.install()
            app._theme_watcher = self._theme_watcher

    def _on_theme_changed(self):
        dark = is_dark()
        if sys.platform == "win32" or platform.system() == "Windows":
            apply_windows_acrylic(self, dark=dark)
            apply_windows_dark_titlebar(self, dark=dark)
        elif sys.platform == "darwin":
            apply_glass(self, dark=dark)
            # Traffic-light geometry follows the title bar, which the refresh
            # above may have redrawn; the window is the only place that knows
            # how to place them.
            try:
                from .glass import configure_traffic_lights

                configure_traffic_lights(self, x_offset=18)
            except Exception:
                pass
        self._refresh_components()

    def refresh_theme(self):
        """Update window components to match active OS theme tokens."""
        self._on_theme_changed()

    def _build_brand_header_widget(self):
        t = T()
        self._brand_container = QWidget()
        self._brand_container.setObjectName("brandContainer")
        self._brand_container.setAutoFillBackground(False)
        brand_row = QHBoxLayout(self._brand_container)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(8)
        brand_row.setAlignment(Qt.AlignVCenter)

        self._icon_label = QLabel()
        icon_path = paths.RESOURCES_DIR / "icon.png"
        icon_size = 28
        if icon_path.exists():
            pix = QPixmap(str(icon_path)).scaled(
                icon_size, icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self._icon_label.setPixmap(pix)
        self._icon_label.setStyleSheet("background: transparent; border: none;")
        brand_row.addWidget(self._icon_label, 0, Qt.AlignVCenter)

        brand_text_col = QVBoxLayout()
        brand_text_col.setContentsMargins(0, 0, 0, 0)
        brand_text_col.setSpacing(1)
        brand_text_col.setAlignment(Qt.AlignVCenter)

        title_version_row = QHBoxLayout()
        title_version_row.setContentsMargins(0, 0, 0, 0)
        title_version_row.setSpacing(5)

        self._brand_label = QLabel(get_app_name())
        self._brand_label.setStyleSheet(
            f"font-size: {_BRAND_FONT_SIZE}; font-weight: 800; color: palette(window-text); letter-spacing: -0.02em; background: transparent; border: none;"
        )
        title_version_row.addWidget(self._brand_label)

        self._brand_version = QLabel(APP_VERSION)
        self._brand_version.setStyleSheet(
            f"font-size: {_BRAND_FONT_SIZE}; font-weight: 800; color: palette(window-text); letter-spacing: -0.02em; background: transparent; border: none;"
        )
        title_version_row.addWidget(self._brand_version)
        title_version_row.addStretch(1)

        brand_text_col.addLayout(title_version_row)

        from .. import browser
        self._version_label = QLabel(
            f'by <a href="https://ko-fi.com/teamslide" style="color: {t.fg}; font-weight: 700; text-decoration: none;">Ryan Specter</a>'
        )
        self._version_label.setStyleSheet(
            "font-size: 11px; color: palette(window-text); background: transparent; border: none; margin: 0; padding: 0;"
        )
        self._version_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._version_label.setOpenExternalLinks(False)
        self._version_label.linkActivated.connect(lambda url: browser.open_browser(url))
        brand_text_col.addWidget(self._version_label)

        brand_row.addLayout(brand_text_col, 1)
        return self._brand_container

    def _build_header_bar(self):
        t = T()
        bar = DraggableHeaderBar(self)
        bar.setFixedHeight(int(HEADER_CONTENT_HEIGHT))
        self._paint_opaque(bar)
        is_mac = sys.platform == "darwin"
        is_win = sys.platform == "win32" or platform.system() == "Windows"
        controls_left = are_window_controls_on_left()

        left_margin = (84 if is_mac else 70) if controls_left else 18
        right_margin = 24 if controls_left else (120 if is_win else 90)

        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(left_margin, 0, right_margin, 0)
        bar_layout.setSpacing(10)
        bar_layout.setAlignment(Qt.AlignVCenter)

        brand = self._build_brand_header_widget()

        # Title container for active page title
        self._title_container = QWidget()
        self._paint_opaque(self._title_container)
        title_cont_layout = QHBoxLayout(self._title_container)
        title_cont_layout.setContentsMargins(0, 0, 0, 0)
        title_cont_layout.setSpacing(0)
        title_cont_layout.setAlignment(Qt.AlignVCenter)

        if controls_left:
            # macOS / left-handed DEs: brand beside the controls, page title
            # against the trailing edge of the window.
            bar_layout.addWidget(brand, 0, Qt.AlignVCenter)
            bar_layout.addStretch(1)
            title_cont_layout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            bar_layout.addWidget(self._title_container, 0, Qt.AlignRight | Qt.AlignVCenter)
        else:
            # Windows / right-handed DEs: Brand on left, page headings beside brand, controls margin on right
            bar_layout.addWidget(brand, 0, Qt.AlignVCenter)
            bar_layout.addSpacing(28)
            bar_layout.addWidget(self._title_container, 0, Qt.AlignVCenter)
            bar_layout.addStretch(1)

        return bar

    def _align_page_title(self):
        """Keep the page title against the window edge opposite the controls."""
        title = getattr(getattr(self, "_select_page", None), "_title", None)
        if title is None:
            return
        title.setAlignment(page_title_alignment())

    def _make_nav_button(self, text: str, icon_name: str, *, checkable: bool = False) -> QPushButton:
        """A sidebar row. Aqua on macOS; a WinUI / Adwaita row on Windows and Linux.

        No local stylesheet. A stylesheet on the button or its parent is what
        painted blank plates and left the previous label behind.
        """
        btn = SidebarButton(text)
        btn.setIcon(get_symbol_icon(icon_name, 16))
        btn.setIconSize(QSize(16, 16))
        btn.setCheckable(checkable)
        btn.setMinimumHeight(max(btn.sizeHint().height(), sidebar_row_min_height()))
        return btn

    def _build_nav(self):
        t = T()
        nav = QWidget()
        nav.setObjectName("navPanel")
        is_mac = sys.platform == "darwin"
        nav_width = 185 if is_mac else 175
        nav.setFixedWidth(nav_width)

        layout = QVBoxLayout(nav)
        layout.setContentsMargins(10, 8, 10, 10)
        # Aqua draws the button about 6px outside the layout cell on each
        # side, so macOS needs the wider gap. Windows and Linux paint inside
        # the row, the way a WinUI or Adwaita sidebar does.
        layout.setSpacing(sidebar_row_spacing())

        if not can_inline_title_with_window_controls():
            brand = self._build_brand_header_widget()
            layout.addWidget(brand)
            layout.addSpacing(8)

        # Exclusive navigation button group to prevent dual focus or dual checked states
        self._nav_btn_group = QButtonGroup(self)
        self._nav_btn_group.setExclusive(True)

        self._nav_buttons = {}
        btn = self._make_nav_button(tr("nav_select_package"), "install", checkable=True)
        btn.clicked.connect(self._on_select_nav_clicked)
        layout.addWidget(btn)
        self._nav_buttons["nav_select_package"] = (btn, _PAGE_SELECT)
        self._nav_btn_group.addButton(btn, _PAGE_SELECT)

        self._settings_btn = self._make_nav_button(tr("nav_settings"), "settings", checkable=True)
        self._settings_btn.clicked.connect(lambda: self._nav_to_page(_PAGE_SETTINGS))
        layout.addWidget(self._settings_btn)
        self._nav_buttons["nav_settings"] = (self._settings_btn, _PAGE_SETTINGS)
        self._nav_btn_group.addButton(self._settings_btn, _PAGE_SETTINGS)

        # Primary pages stay at the top. Everything else sits with the language
        # selector at the bottom of the window.
        layout.addStretch(1)

        self._aux_nav_buttons = []

        if platform.system() == "Linux" and not paths.IS_MAC:
            self._linux_setup_btn = self._make_nav_button(tr("nav_linux_setup"), "tools")
            self._linux_setup_btn.clicked.connect(self._show_linux_setup)
            layout.addWidget(self._linux_setup_btn)
            self._aux_nav_buttons.append(self._linux_setup_btn)

        from ..sp_flash_gui import is_sp_flash_gui_supported
        if is_sp_flash_gui_supported():
            self._sp_flash_tool_btn = self._make_nav_button(tr("nav_sp_flash_tool_gui"), "tools")
            self._sp_flash_tool_btn.clicked.connect(self._open_sp_flash_tool_gui)
            layout.addWidget(self._sp_flash_tool_btn)
            self._aux_nav_buttons.append(self._sp_flash_tool_btn)

        self._support_btn = self._make_nav_button(tr("nav_donate"), "support")
        self._support_btn.clicked.connect(self._on_support_clicked)
        layout.addWidget(self._support_btn)
        self._aux_nav_buttons.append(self._support_btn)

        self._log_btn = self._make_nav_button(tr("nav_log"), "diagnostics", checkable=True)
        self._log_btn.clicked.connect(self._show_diagnostics)
        self._log_btn.setVisible(False)
        layout.addWidget(self._log_btn)
        self._nav_btn_group.addButton(self._log_btn, _PAGE_DIAGNOSTICS)
        self._aux_nav_buttons.append(self._log_btn)

        self._check_updates_btn = self._make_nav_button(tr("nav_check_updates"), "update")
        self._check_updates_btn.clicked.connect(self._on_check_updates_clicked)
        layout.addWidget(self._check_updates_btn)
        self._aux_nav_buttons.append(self._check_updates_btn)

        self._lang_label = QLabel(tr("nav_language"))
        self._lang_label.setStyleSheet(
            "font-size: 11px; color: palette(window-text); margin-top: 6px; background: transparent; border: none;"
        )
        layout.addWidget(self._lang_label)
        self._lang_combo = QComboBox()
        self._lang_combo.setObjectName("langCombo")
        self._lang_combo.addItem("\u4e2d\u6587", "zh-CN")
        self._lang_combo.addItem("English", "en")
        self._lang_combo.addItem("Fran\u00e7ais", "fr")
        self._lang_combo.addItem("Espa\u00f1ol", "es")
        idx = self._lang_combo.findData(translator().lang)
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self._lang_combo)

        self._nav_panel = nav
        self._nav_content = nav
        self._apply_nav_surface()
        self._refresh_icons()
        return nav

    def _apply_nav_surface(self):
        """Color the sidebar from the palette.

        A stylesheet on this panel would also restyle the buttons inside it,
        which is what painted them as blank plates and left old labels behind.
        """
        nav = getattr(self, "_nav_panel", None)
        if nav is None:
            return
        nav.setStyleSheet("")
        # No stylesheet on the rail. On glass the gaps stay clear so the
        # backdrop shows; the buttons repaint their own rows. Elsewhere the
        # rail is a solid pane so a moved highlight cannot linger.
        self._paint_opaque(nav, T().bg_nav)
        nav.update()
        for btn in nav.findChildren(SidebarButton):
            btn.update()

    def _refresh_icons(self):
        """Update navigation and action button icons to match theme tokens and accent."""
        if hasattr(self, "_nav_buttons"):
            for key, (btn, _) in self._nav_buttons.items():
                if key == "nav_select_package":
                    btn.setIcon(get_symbol_icon("install", 16))
                elif key == "nav_settings":
                    btn.setIcon(get_symbol_icon("settings", 16))
        if hasattr(self, "_support_btn"):
            self._support_btn.setIcon(get_symbol_icon("support", 16))
        if hasattr(self, "_log_btn"):
            self._log_btn.setIcon(get_symbol_icon("diagnostics", 16))
        if hasattr(self, "_check_updates_btn"):
            self._check_updates_btn.setIcon(get_symbol_icon("update", 16))
        if hasattr(self, "_linux_setup_btn"):
            self._linux_setup_btn.setIcon(get_symbol_icon("tools", 16))
        if hasattr(self, "_sp_flash_tool_btn"):
            self._sp_flash_tool_btn.setIcon(get_symbol_icon("tools", 16))
        if hasattr(self, "_lang_combo"):
            lang_icon = get_symbol_icon("translate", 14)
            for i in range(self._lang_combo.count()):
                self._lang_combo.setItemIcon(i, lang_icon)

    def _refresh_components(self):
        """Update window components to match active OS theme tokens.

        Navigation buttons are left to the platform style. Repainting them with
        a local stylesheet is what left the previous label on screen.
        """
        t = T()
        self._apply_nav_surface()
        for surface in (
            self.centralWidget(),
            getattr(self, "_title_bar", None),
            getattr(self, "_title_container", None),
            getattr(self, "body", None),
            getattr(self, "_stack", None),
            getattr(self, "_notice_host", None),
            getattr(self, "_select_page", None),
            getattr(self, "_flash_page", None),
            getattr(self, "_error_page", None),
            getattr(self, "_retry_page", None),
            getattr(self, "_settings_page", None),
            getattr(self, "_diagnostics_page", None),
        ):
            if surface is not None:
                self._paint_opaque(surface)

        if hasattr(self, "_brand_label"):
            self._brand_label.setStyleSheet(
                f"font-size: {_BRAND_FONT_SIZE}; font-weight: 800; color: palette(window-text); letter-spacing: -0.02em; background: transparent; border: none;"
            )

        if hasattr(self, "_brand_version"):
            self._brand_version.setText(APP_VERSION)
            self._brand_version.setStyleSheet(
                f"font-size: {_BRAND_FONT_SIZE}; font-weight: 800; color: palette(window-text); letter-spacing: -0.02em; background: transparent; border: none;"
            )

        if hasattr(self, "_version_label"):
            # Attribution only — the version lives beside the brand title, not here.
            self._version_label.setText(
                f'by <a href="https://ko-fi.com/teamslide" style="color: {t.fg}; font-weight: 700; text-decoration: none;">Ryan Specter</a>'
            )
            self._version_label.setStyleSheet(
                "font-size: 11px; color: palette(window-text); background: transparent; border: none; margin: 0; padding: 0;"
            )

        if hasattr(self, "_lang_label"):
            self._lang_label.setStyleSheet(
                "font-size: 11px; color: palette(window-text); margin-top: 6px; background: transparent; border: none;"
            )

        sb = self.statusBar()
        if sb and hasattr(sb, "refresh_theme"):
            sb.refresh_theme()

        self._refresh_icons()

        for page in (
            self._select_page,
            self._flash_page,
            self._error_page,
            self._retry_page,
            self._settings_page,
            getattr(self, "_diagnostics_page", None),
        ):
            if page is not None and hasattr(page, "refresh_theme") and callable(page.refresh_theme):
                page.refresh_theme()


    def _connect_signals(self):
        self._select_page.package_selected.connect(self._on_package_selected)
        self._select_page.download_started.connect(self._on_download_started)
        self._select_page.download_progress.connect(self._on_download_progress)
        self._select_page.download_finished.connect(self._on_download_finished)
        self._select_page.download_cancelled.connect(self._on_download_cancelled)
        self._select_page.prep_progress.connect(self._flash_page.update_prep_progress)
        self._flash_page.set_cancel_download_callback(self._on_cancel_download)
        self._settings_page.flash_method_changed.connect(self._on_method_changed)
        self._settings_page.simulated_mac_requested.connect(
            self._restart_in_simulated_macos
        )
        self._flash_page.on_cancel(self._on_cancel_flash)
        self._flash_page.on_cancel_wait(self._on_cancel_wait)
        self._flash_page.on_install_retry(self._on_progress_retry)
        self._flash_page.on_power_off_continue(self._on_power_off_continue)
        self._error_page.on_retry(self._on_progress_retry)
        self._error_page.on_reconnect(self._on_reconnect)
        self._error_page.on_reselect(lambda: self._nav_to_page(_PAGE_SELECT))
        self._error_page.on_view_log(self._show_diagnostics)
        self._error_page.on_open_sp_gui(self._open_sp_flash_tool_gui)
        self._retry_page.on_cancel(self._on_cancel_flash)
        self.service.step_changed.connect(self._on_step_changed)
        self.service.progress.connect(self._on_progress)
        self.service.action_changed.connect(self._flash_page.update_action)
        self.service.log_message.connect(self._on_log_message)
        self.service.flash_finished.connect(self._on_flash_finished)
        self.service.device_found.connect(self._on_device_found)
        self.service.device_lost.connect(self._on_device_lost)
        self.service.monitor_error.connect(self._on_monitor_error)

    def _on_download_started(self, name, model):
        self._download_active = True
        self._package_name = name
        self._package_model = model or ""
        self._flash_page.set_package_name(name)
        self._flash_page.set_model(model or "")
        self._flash_page.show_downloading()
        self._nav_to_page(_PAGE_FLASH)

    def _on_download_progress(self, percent, status_text):
        if getattr(self, "_download_active", False):
            self._flash_page.update_download_progress(percent, status_text)

    def _on_download_finished(self, ok, result):
        if not ok:
            self._download_active = False
            self._apply_install_ui_state(False)
            self._nav_to_page(_PAGE_SELECT)
            self._show_status(f"{tr('sel_download_failed')} \u2014 {result}", 15000)
        else:
            self._download_active = False
            self._flash_page.show_preparing()
            if hasattr(self, "_select_page") and hasattr(self._select_page, "_title"):
                self._select_page._title.setText(tr("flash_install_in_progress"))

    def _on_cancel_download(self):
        self._download_active = False
        self._select_page.cancel_download()
        self._apply_install_ui_state(False)
        self._nav_to_page(_PAGE_SELECT)

    def _on_download_cancelled(self):
        self._download_active = False
        self._apply_install_ui_state(False)
        self._nav_to_page(_PAGE_SELECT)

    # How often the seamless window chrome is verified on platforms where Qt can
    # silently wipe it (see _verify_native_chrome). The check is a native
    # property read, so this is far cheaper than a repaint.
    CHROME_WATCHDOG_MS = 1000

    def _reassert_mac_glass(self, *_args):
        """Re-apply the native window chrome (macOS glass, Windows DWM)."""
        if getattr(self, "_reasserting_chrome", False):
            return
        self._reasserting_chrome = True
        try:
            if sys.platform == "darwin":
                from .glass import _ensure_seamless_titlebar, apply_glass, configure_traffic_lights
                _ensure_seamless_titlebar(self)
                apply_glass(self)
                configure_traffic_lights(self, x_offset=18)
            elif sys.platform == "win32":
                from .glass import apply_windows_acrylic, apply_windows_dark_titlebar

                apply_windows_dark_titlebar(self, is_dark())
                apply_windows_acrylic(self, dark=is_dark())
        finally:
            self._reasserting_chrome = False

    def _verify_native_chrome(self, *_args):
        """Re-apply the window chrome only when the platform actually lost it.

        Qt's platform plugins re-apply their own window flags whenever a window
        is reconfigured — switching sidebar entries is enough — and that drops
        the seamless title bar (the full-size content view on macOS, the DWM
        attributes on Windows). The native title bar then grows back, the
        content view drops by its height and the sidebar's first entry looks
        pushed down until the user resizes the window by hand. Nothing signals
        this, so the state is verified instead; the read is a native property
        access and the repair only runs when the check fails.
        """
        if not self.isVisible() or self.isMinimized():
            return
        try:
            if self._install_run_active():
                set_window_close_button_enabled(self, False)
        except Exception:
            pass
        try:
            from .glass import native_chrome_intact

            if native_chrome_intact(self, dark=is_dark()):
                return
        except Exception:
            return
        self._reassert_mac_glass()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            is_interactive = False
            w = child
            while w and w is not self:
                if isinstance(w, (QPushButton, QComboBox)) or (
                    isinstance(w, QLabel) and (w.textInteractionFlags() & Qt.LinksAccessibleByMouse)
                ):
                    is_interactive = True
                    break
                w = w.parentWidget()
            if not is_interactive:
                dragged = False
                if sys.platform == "darwin":
                    try:
                        import AppKit
                        from .glass import _get_nsview
                        ns_app = AppKit.NSApplication.sharedApplication()
                        curr_evt = ns_app.currentEvent()
                        if curr_evt:
                            view = _get_nsview(self)
                            ns_win = view.window() if view else None
                            if ns_win:
                                ns_win.performWindowDragWithEvent_(curr_evt)
                                dragged = True
                    except Exception:
                        pass
                if not dragged and hasattr(self, "windowHandle") and self.windowHandle():
                    try:
                        dragged = self.windowHandle().startSystemMove()
                    except Exception:
                        pass
                if dragged:
                    event.accept()
                    return
                self._mw_dragging = True
                self._mw_drag_offset = event.globalPosition().toPoint() - self.pos()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if getattr(self, "_mw_dragging", False) and (event.buttons() & Qt.LeftButton):
            if hasattr(self, "_mw_drag_offset") and self._mw_drag_offset is not None:
                self.move(event.globalPosition().toPoint() - self._mw_drag_offset)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._mw_dragging = False
        self._mw_drag_offset = None
        super().mouseReleaseEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            self._verify_native_chrome()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            if not self._install_run_active() and not self.isMaximized() and not self.isFullScreen():
                self.resize(*default_window_size())
            if not os.environ.get("QT_QPA_PLATFORM") == "offscreen" and not os.environ.get("INNIOASIS_HEADLESS"):
                QTimer.singleShot(1200, self._check_legacy_installations)
        if sys.platform == "darwin":
            from .glass import apply_glass, configure_traffic_lights
            apply_glass(self)
            configure_traffic_lights(self, x_offset=18)
        if event.type() == QEvent.Type.Show and not getattr(self, "_chrome_watchdog", None):
            # Started once: a reset can happen at any point in a session, and the
            # check is a property read rather than a repaint.
            self._chrome_watchdog = QTimer(self)
            self._chrome_watchdog.setInterval(self.CHROME_WATCHDOG_MS)
            self._chrome_watchdog.timeout.connect(self._verify_native_chrome)
            self._chrome_watchdog.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if sys.platform == "darwin" and hasattr(self, "_title_bar"):
            self._title_bar.setGeometry(0, 0, self.width(), int(HEADER_CONTENT_HEIGHT))
            self._title_bar.raise_()
        self._verify_native_chrome()

    def nativeEvent(self, eventType, message):
        """Keep Windows caption buttons and drop the second title string."""
        if sys.platform != "win32" or not can_inline_title_with_window_controls():
            return super().nativeEvent(eventType, message)
        try:
            if bytes(eventType) != b"windows_generic_MSG":
                return super().nativeEvent(eventType, message)
            import ctypes
            from ctypes import wintypes

            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0083 and msg.wParam:
                class _RECT(ctypes.Structure):
                    _fields_ = [
                        ("left", wintypes.LONG),
                        ("top", wintypes.LONG),
                        ("right", wintypes.LONG),
                        ("bottom", wintypes.LONG),
                    ]

                class _NCCALCSIZE_PARAMS(ctypes.Structure):
                    _fields_ = [("rgrc", _RECT * 3), ("lppos", ctypes.c_void_p)]

                params = _NCCALCSIZE_PARAMS.from_address(int(msg.lParam))
                src = params.rgrc[0]
                user32 = ctypes.windll.user32
                left, top, right, bottom = client_rect_for_unified_caption(
                    (src.left, src.top, src.right, src.bottom),
                    maximized=bool(self.windowState() & Qt.WindowMaximized),
                    frame_x=user32.GetSystemMetrics(32),
                    frame_y=user32.GetSystemMetrics(33),
                    padded=user32.GetSystemMetrics(92),
                )
                params.rgrc[0].left = left
                params.rgrc[0].top = top
                params.rgrc[0].right = right
                params.rgrc[0].bottom = bottom
                return True, 0
            if msg.message == 0x0084:
                dwmapi = ctypes.windll.dwmapi
                hit = wintypes.LPARAM()
                if dwmapi.DwmDefWindowProc(
                    wintypes.HWND(int(self.winId())),
                    ctypes.c_uint(msg.message),
                    wintypes.WPARAM(msg.wParam),
                    wintypes.LPARAM(msg.lParam),
                    ctypes.byref(hit),
                ):
                    return True, int(hit.value)
                bar = getattr(self, "_title_bar", None)
                if bar is not None and bar.isVisible():
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    from PySide6.QtCore import QPoint
                    local = bar.mapFromGlobal(QPoint(x, y))
                    if bar.rect().contains(local):
                        child = bar.childAt(local)
                        interactive = False
                        probe = child
                        while probe is not None and probe is not bar:
                            if isinstance(probe, (QPushButton, QComboBox)) or (
                                isinstance(probe, QLabel)
                                and (probe.textInteractionFlags() & Qt.LinksAccessibleByMouse)
                            ):
                                interactive = True
                                break
                            probe = probe.parentWidget()
                        if not interactive:
                            return True, 2
        except Exception:
            logging.getLogger(__name__).debug("unified caption event skipped", exc_info=True)
        return super().nativeEvent(eventType, message)

    def keyPressEvent(self, event):
        # M or D reveal the hidden install-method entry. "MTKClient (Mac)" runs
        # the macOS code path on Linux/Windows so the Mac flow (MTKClient as the
        # only backend, mac-centric prompts) can be tested without a Mac.
        # NB: PySide6 6.11 returns a Flag here, so int(modifiers()) raises.
        if event.key() == Qt.Key_D and event.modifiers() == Qt.NoModifier:
            # Session-only. M still reveals the hidden install backend.
            self._unlock_diagnostics()
            event.accept()
            return
        if event.key() == Qt.Key_M and event.modifiers() == Qt.NoModifier:
            self._settings_page.reveal_advanced_methods()
            if self._settings_page.advanced_methods_revealed():
                # Remember the reveal across launches: it also unlocks the
                # MTKClient diagnostics view on Windows, which is otherwise
                # hidden because SP Flash Tool is the supported backend there.
                try:
                    from ..device_tracking import set_hidden_mtk_options

                    set_hidden_mtk_options(True)
                except Exception:
                    pass
                self._nav_to_page(_PAGE_SETTINGS)
                self._append_log("Advanced install methods revealed (M/D).")
        super().keyPressEvent(event)

    def _install_run_active(self) -> bool:
        """True while a flash run or download run is in progress."""
        if getattr(self, "_download_active", False):
            return True
        return self.sm.state in _INSTALL_RUN_STATES

    def _build_inline_prompt(self) -> QWidget:
        """Install prompts live under the page, in the content column.

        The window grows to fit the prompt and shrinks again when it closes.
        """
        host = QWidget()
        host.setObjectName("installPrompt")
        layout = QVBoxLayout(host)
        layout.setContentsMargins(24, 4, 24, 12)
        layout.setSpacing(8)
        self._prompt_title = QLabel("")
        self._prompt_title.setWordWrap(True)
        self._prompt_title.setStyleSheet(
            "font-size: 15px; font-weight: 700; color: palette(window-text); background: transparent;"
        )
        self._prompt_body = QLabel("")
        self._prompt_body.setWordWrap(True)
        self._prompt_body.setStyleSheet(
            "font-size: 13px; color: palette(window-text); background: transparent;"
        )
        layout.addWidget(self._prompt_title)
        layout.addWidget(self._prompt_body)
        row = QHBoxLayout()
        row.setSpacing(8)
        self._prompt_accept = QPushButton("")
        self._prompt_reject = QPushButton("")
        row.addWidget(self._prompt_accept)
        row.addWidget(self._prompt_reject)
        row.addStretch(1)
        layout.addLayout(row)
        host.hide()
        self._prompt_loop = None
        self._prompt_result = False
        self._prompt_accept.clicked.connect(self._accept_inline_prompt)
        self._prompt_reject.clicked.connect(self._dismiss_inline_prompt)
        return host

    def _show_inline_prompt(self, title: str, body: str, accept: str, reject: str, *, wait: bool) -> bool:
        """Show a prompt in the content column. ``wait`` blocks until a button."""
        self._prompt_title.setText(title)
        self._prompt_body.setText(body)
        self._prompt_accept.setText(accept)
        self._prompt_reject.setText(reject)
        self._prompt_result = False
        self._inline_prompt.show()
        self._adjust_window_geometry()
        if not wait:
            return False
        loop = QEventLoop(self)
        self._prompt_loop = loop
        loop.exec()
        self._prompt_loop = None
        self._inline_prompt.hide()
        self._adjust_window_geometry()
        return self._prompt_result

    def _accept_inline_prompt(self):
        self._prompt_result = True
        self._finish_inline_prompt()

    def _dismiss_inline_prompt(self):
        self._prompt_result = False
        self._finish_inline_prompt()

    def _finish_inline_prompt(self):
        loop = getattr(self, "_prompt_loop", None)
        if loop is not None and loop.isRunning():
            loop.quit()
            return
        prompt = getattr(self, "_inline_prompt", None)
        if prompt is not None:
            prompt.hide()
        self._adjust_window_geometry()

    def _adjust_window_geometry(self):
        """Adjust window size to fit the content of the active page without wasted space."""
        if (
            self.isMinimized()
            or self.isMaximized()
            or self.isFullScreen()
            or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        ):
            return

        curr_idx = self._stack.currentIndex() if hasattr(self, "_stack") else _PAGE_SELECT
        # The log needs the full window, including while an install is running.
        if curr_idx == _PAGE_NOTICE:
            host = getattr(self, "_notice_host", None)
            hint_h = host.sizeHint().height() if host is not None else 220
            width = DEFAULT_WINDOW_WIDTH
            height = max(200, min(520, hint_h + 72))
            self.setMinimumSize(580, 180)
            self.resize(width, height)
            return

        if curr_idx == _PAGE_DIAGNOSTICS:
            width, height = default_window_size()
            self.setMinimumSize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)
            self.resize(width, height)
            return

        is_install = self._install_run_active()
        donations_disabled = device_tracking.is_donation_ui_disabled(self.settings)
        hug_install = is_install or bool(
            getattr(self, "_install_complete", False)
            or getattr(self, "_install_failed", False)
            or getattr(self, "_power_off_prompt", False)
        )

        if hug_install:
            # Hug the progress card. A prompt in the content column is the
            # only reason this grows. The window collapses as soon as the
            # install is pending, including before the flash page is shown.
            self._stack.updateGeometry()
            page_h = self._flash_page.sizeHint().height()
            prompt = getattr(self, "_inline_prompt", None)
            prompt_h = 0
            if prompt is not None and not prompt.isHidden():
                prompt_h = prompt.sizeHint().height()
            bar = self.statusBar()
            status_h = bar.sizeHint().height() if bar is not None and bar.isVisible() else 0
            target_w = DEFAULT_WINDOW_WIDTH
            if curr_idx == _PAGE_FLASH:
                target_h = compact_install_height(
                    page_h=page_h,
                    prompt_h=prompt_h,
                    status_h=status_h,
                    inline_header=getattr(self, "_title_bar", None) is not None,
                    donations_disabled=donations_disabled,
                )
            else:
                target_h = 228
            self.setMinimumSize(580, 168)
            self.resize(target_w, target_h)
        else:
            min_w = MINIMUM_WINDOW_WIDTH
            min_h = MINIMUM_WINDOW_HEIGHT
            self.setMinimumSize(min_w, min_h)

            width, height = default_window_size()
            if curr_idx in (_PAGE_SETTINGS, _PAGE_SELECT):
                self.resize(width, height)
            elif self.width() < min_w or self.height() < min_h:
                self.resize(width, height)

    def _apply_install_ui_state(self, active: bool):
        if getattr(self, "_install_ui_active", None) == active:
            return
        self._install_ui_active = active
        try:
            set_window_close_button_enabled(self, not active)
        except Exception:
            pass

        if active:
            # During active install/download:
            # Keep Settings visible but disabled so both are accommodated as requested
            if hasattr(self, "_settings_btn"):
                # Pending installs show Install Software and Diagnostics only.
                self._settings_btn.setVisible(False)
                self._settings_btn.setEnabled(False)
            if hasattr(self, "_support_btn"):
                self._support_btn.setVisible(False)
            if hasattr(self, "_log_btn"):
                self._sync_diagnostics_button()
            if hasattr(self, "_check_updates_btn"):
                self._check_updates_btn.setVisible(False)
            if hasattr(self, "_linux_setup_btn"):
                self._linux_setup_btn.setVisible(False)
            if hasattr(self, "_sp_flash_tool_btn"):
                # Kept available during a run so it can take over as the fallback.
                self._sp_flash_tool_btn.setVisible(True)
                self._sp_flash_tool_btn.setEnabled(True)
            if hasattr(self, "_lang_label"):
                self._lang_label.setVisible(False)
            if hasattr(self, "_lang_combo"):
                self._lang_combo.setVisible(False)
        else:
            if hasattr(self, "_settings_btn"):
                self._settings_btn.setVisible(True)
                self._settings_btn.setEnabled(True)
            if hasattr(self, "_support_btn"):
                self._support_btn.setVisible(True)
            if hasattr(self, "_log_btn"):
                self._sync_diagnostics_button()
            if hasattr(self, "_check_updates_btn"):
                self._check_updates_btn.setVisible(True)
            if hasattr(self, "_linux_setup_btn"):
                self._linux_setup_btn.setVisible(True)
            if hasattr(self, "_sp_flash_tool_btn"):
                self._sp_flash_tool_btn.setVisible(True)
            if hasattr(self, "_lang_label"):
                self._lang_label.setVisible(True)
            if hasattr(self, "_lang_combo"):
                self._lang_combo.setVisible(True)
            self._apply_donation_visibility()

        nav = getattr(self, "_nav_panel", None)
        if nav is not None:
            nav.update()
        self._adjust_window_geometry()

    def _sync_install_nav_entry(self) -> bool:
        """Rename the first sidebar entry while an install is running.

        The entry stands for the run ("Install Software") instead of the
        package browser, so the sidebar keeps showing what the app is doing.
        """
        entry = getattr(self, "_nav_buttons", {}).get("nav_select_package")
        if not entry:
            return False
        btn, _idx = entry
        active = self._install_run_active() or getattr(self, "_compact_install_screen", False)
        btn.setText(tr("nav_install_package" if active else "nav_select_package"))
        self._apply_install_ui_state(active)
        return active

    def _on_select_nav_clicked(self):
        """First entry: package select. If an install is running:
        - If on another page, reopens the flash run view.
        - If already on the flash page, clicking acts as cancelling the install.
        """
        if self._install_run_active():
            if self._stack.currentIndex() != _PAGE_FLASH:
                self._nav_to_page(_PAGE_FLASH)
            else:
                if getattr(self, "_download_active", False):
                    self._on_cancel_download()
                elif self.sm.state in (FlashState.S4_FLASHING,) or getattr(self, "_step_now", "") in _WRITE_STEPS:
                    self._on_cancel_flash()
                else:
                    self._on_cancel_wait()
        else:
            if getattr(self, "_install_complete", False):
                self._install_complete = False
                self._compact_install_screen = False
            self._nav_to_page(_PAGE_SELECT)

    def _page_title(self, page_idx, install_active: bool) -> str:
        if page_idx == _PAGE_SETTINGS:
            return tr("settings_title")
        if page_idx == _PAGE_DIAGNOSTICS:
            return tr("log_center")
        if page_idx == _PAGE_FLASH:
            if getattr(self, "_install_failed", False):
                return tr("flash_failed_title")
            if getattr(self, "_power_off_prompt", False):
                return tr("dialog_pre_install_title")
            if getattr(self, "_install_complete", False):
                return tr("flash_install_complete")
            if getattr(self, "_download_active", False):
                return tr("flash_download_in_progress")
            return tr("flash_install_in_progress") if install_active else tr("flash_ready_title")
        if page_idx == _PAGE_ERROR:
            return tr("flash_failed_title")
        if page_idx == _PAGE_RETRY:
            return tr("flash_retry_title")
        if page_idx == _PAGE_NOTICE:
            return getattr(self, "_notice_title", "") or tr("sel_title")
        return tr("sel_title")

    def _highlight_nav(self, page_idx, install_active: bool):
        if page_idx == _PAGE_DIAGNOSTICS and hasattr(self, "_log_btn"):
            checked = self._log_btn
        elif install_active and page_idx == _PAGE_FLASH:
            checked = self._nav_buttons["nav_select_package"][0]
        else:
            checked = None
            for _key, (btn, idx) in self._nav_buttons.items():
                if idx == page_idx:
                    checked = btn
                    break
        if checked is not None:
            checked.blockSignals(True)
            checked.setChecked(True)
            checked.clearFocus()
            checked.blockSignals(False)
        for btn in (
            self._nav_buttons.get("nav_select_package", (None,))[0],
            getattr(self, "_settings_btn", None),
            getattr(self, "_log_btn", None),
        ):
            if btn is not None:
                btn.update()

    def _nav_to_page(self, page_idx):
        if self._install_run_active() and page_idx not in (
            _PAGE_FLASH, _PAGE_DIAGNOSTICS, _PAGE_NOTICE, _PAGE_ERROR, _PAGE_RETRY,
        ):
            # A click may already have checked the blocked entry. Put the
            # highlight back on the page that is actually showing.
            self._highlight_nav(self._stack.currentIndex(), True)
            return
        self._stack.setCurrentIndex(page_idx)
        install_active = self._sync_install_nav_entry()
        self._highlight_nav(page_idx, install_active)

        # Update header page title in the unified title bar
        if hasattr(self, "_select_page") and hasattr(self._select_page, "_title"):
            title = self._select_page._title
            title.setText(self._page_title(page_idx, install_active))
            self._align_page_title()
            from .surfaces import seal_updating_text
            seal_updating_text(title)
            title_host = getattr(self, "_title_container", None)
            if title_host is not None:
                self._paint_opaque(title_host)
                title_host.update()

        self._adjust_window_geometry()
        QTimer.singleShot(0, self._verify_native_chrome)

    def _on_package_selected(self, path, name, model):
        self._package_path = path
        self._package_name = name
        self._package_model = model or ""

        self._sync_sp_history()

        if device_tracking.sp_gui_install_enabled(self.settings):
            self._begin_sp_gui_install()
            return

        if device_tracking.terminal_install_enabled(self.settings):
            # Terminal install: the user drives the console tools themselves.
            self._begin_terminal_install()
            return
        self._begin_flash_flow()

    def _begin_terminal_install(self):
        """Open the console install command in the user's terminal window.

        Prompts the user with device connection instructions and connection hints
        (e.g. paperclip pinhole reset or download mode), then opens the terminal
        detached and exits Updater CE.
        """
        from .. import terminal_install
        from ..flash_service import _find_scatter, compute_extract_dir, completed_extract_dir
        from ..config import device_label_for_model, is_mediatek_installer

        extract_dir = completed_extract_dir(self._package_path) or compute_extract_dir(
            self._package_path
        )
        scatter = None
        if Path(extract_dir).is_dir():
            try:
                scatter = _find_scatter(Path(extract_dir))
            except Exception as e:
                logger.debug("Terminal install: scatter discovery failed: %s", e)
        if not scatter:
            self._nav_to_page(_PAGE_SELECT)
            QMessageBox.warning(
                self, tr("terminal_install_title"), tr("terminal_install_no_scatter")
            )
            return

        platform_name = ""
        try:
            from ..flash_service import _parse_scatter_platform

            platform_name = _parse_scatter_platform(Path(scatter)) or ""
        except Exception as e:
            logger.debug("Terminal install: platform detection failed: %s", e)

        command = terminal_install.build_install_command(
            self._flash_method,
            extract_dir,
            scatter,
            package_path=self._package_path,
            platform_name=platform_name,
            auth_file=device_tracking.sp_auth_file(self.settings),
        )
        if command is None:
            self._nav_to_page(_PAGE_SELECT)
            QMessageBox.warning(
                self, tr("terminal_install_title"), tr("terminal_install_no_tool")
            )
            return

        term_name = "Command Prompt" if paths.IS_WINDOWS else "Terminal"
        method_label = "SP Flash Tool" if "flash_tool" in str(command[0]).lower() else "MTKclient"
        dev_label = device_label_for_model(self._package_model) or tr("device_unknown")

        if is_mediatek_installer():
            hint = tr("terminal_hint_generic")
        else:
            hint = tr("terminal_hint_innioasis")

        dialog_title = tr("terminal_install_prompt_title").format(terminal_name=term_name)
        dialog_body = tr("terminal_install_prompt_body").format(
            terminal_name=term_name,
            method=method_label,
            device=dev_label,
            hint=hint,
        )

        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            reply = QMessageBox.information(
                self,
                dialog_title,
                dialog_body,
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Ok,
            )
            if reply != QMessageBox.Ok:
                return

        script = None
        try:
            # The script exports whatever the console tool needs that this
            # machine's environment lacks (Linux SP Flash Tool libraries).
            script = terminal_install.build_script(
                command,
                title=tr("terminal_install_title"),
                cwd=extract_dir,
                env=terminal_install.script_env(command),
            )
        except OSError as e:
            logger.warning("Terminal install: could not write the launcher: %s", e)

        opened = bool(script) and terminal_install.open_in_terminal(script)
        # The app stays on the selection screen; the terminal owns the run.
        self._nav_to_page(_PAGE_SELECT)
        self._append_log(f"Terminal install command: {' '.join(command)}")
        if opened:
            self._show_status(
                tr("status_terminal_install").format(path=str(script)), 20000
            )
            if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
                from PySide6.QtWidgets import QApplication
                QApplication.quit()
        else:
            QMessageBox.warning(
                self,
                tr("terminal_install_title"),
                tr("terminal_install_failed").format(path=str(script or "")),
            )

    def _begin_sp_gui_install(self):
        """Install via SP Flash Tool GUI:
        Validates cached firmware readiness, shows instructions, launches SP Flash Tool GUI detached,
        and quits Updater CE.
        """
        from .. import sp_flash_gui
        from ..config import device_label_for_model, is_mediatek_installer
        from ..flash_service import completed_extract_dir, compute_extract_dir, _find_scatter

        if not sp_flash_gui.is_sp_flash_gui_supported():
            QMessageBox.warning(self, tr("sp_gui_title"), tr("sp_gui_not_supported"))
            return

        extract_dir = completed_extract_dir(self._package_path) or compute_extract_dir(
            self._package_path
        )
        scatter_path = None
        if Path(extract_dir).is_dir():
            try:
                scatter_path = _find_scatter(Path(extract_dir))
            except Exception:
                pass

        ready, sc, ed, reason = sp_flash_gui.check_cached_firmware_readiness(
            scatter_path=scatter_path,
            extract_dir=extract_dir,
            model=self._package_model,
        )
        if not ready:
            QMessageBox.warning(
                self,
                tr("sp_gui_title"),
                tr("sp_gui_no_cached_firmware") + f"\n\n({reason})",
            )
            return

        dev_label = device_label_for_model(self._package_model) or tr("device_unknown")
        if is_mediatek_installer():
            hint = tr("terminal_hint_generic")
        else:
            hint = tr("terminal_hint_innioasis")

        prompt_title = tr("sp_gui_install_prompt_title")
        prompt_body = tr("sp_gui_install_prompt_body").format(
            device=dev_label,
            hint=hint,
        )

        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            reply = QMessageBox.information(
                self,
                prompt_title,
                prompt_body,
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Ok,
            )
            if reply != QMessageBox.Ok:
                return

        sp_flash_gui.update_sp_history_ini(scatter_path=sc, extract_dir=ed, model=self._package_model)
        ok, msg = sp_flash_gui.launch_sp_flash_tool_gui(
            model=self._package_model,
            scatter_path=sc,
            extract_dir=ed,
        )
        if not ok:
            QMessageBox.warning(
                self,
                tr("sp_gui_error_title"),
                f"{tr('sp_gui_error_desc')}\n\n{msg}",
            )
            return

        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            from PySide6.QtWidgets import QApplication
            QApplication.quit()

    def _begin_flash_flow(self):
        if not self._package_path:
            return
        self._install_failed = False
        self._power_off_prompt = False
        if not getattr(self, "_pre_install_guided", False):
            if not (os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("INNIOASIS_HEADLESS")):
                from .dialogs import PreInstallGuidanceDialog
                from ..branding import is_generic_mtk_brand
                dlg = PreInstallGuidanceDialog(
                    self,
                    model=self._package_model,
                    is_mtk_generic=is_generic_mtk_brand(),
                )
                if dlg.exec() != QDialog.Accepted:
                    return
                self._pre_install_guided = True
        try:
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().start_flash_session(
                package_name=self._package_name or Path(self._package_path).name,
                method=self._flash_method,
                model=self._package_model,
            )
        except Exception:
            pass
        self._retry_guidance_shown = False
        try:
            self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        except ValueError:
            self.sm.reset_full()
            self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._flash_page.set_method(self._flash_method)
        self._flash_page.show_completion_appeal(False)
        self._apply_release_icon(complete=False)
        self._flash_page.show_preparing()
        self._nav_to_page(_PAGE_FLASH)
        self._append_log(
            f"Starting flash for {self._package_name} "
            f"(method: {_method_label(self._flash_method)})"
        )
        pre_extracted = completed_extract_dir(self._package_path)
        try:
            from ..flash_service import compute_extract_dir, _find_scatter
            from ..sp_flash_gui import update_sp_history_ini

            ed = pre_extracted or compute_extract_dir(self._package_path)
            sc = _find_scatter(Path(ed)) if Path(ed).is_dir() else None
            update_sp_history_ini(
                scatter_path=sc,
                extract_dir=Path(ed) if Path(ed).is_dir() else None,
                model=self._package_model,
            )
        except Exception as e:
            logger.debug("Could not pre-update SP history.ini in _begin_flash_flow: %s", e)
        self.service.start_flash(
            self._package_path,
            pre_extracted_dir=pre_extracted,
            method=self._flash_method,
            model=self._package_model,
        )
        self.service.start_device_monitor()

    def _on_step_changed(self, step):
        self._step_now = step
        key = _STEP_KEY.get(step, "step_wait")
        self._flash_page.update_step(key)
        self._retry_page.update_step(key)
        self._on_progress(self._last_progress)
        if step == STEP_EXTRACTING:
            self._flash_page.show_preparing()
        elif step == STEP_WAITING:
            self._flash_page.show_waiting()
            self._flash_page.set_waiting_device()
            self._flash_page.highlight_guide_step(1)
            # Still waiting for the device: the method may be switched.
            self._settings_page.set_method_enabled(True)
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step == STEP_DETECT:
            self._flash_page.set_detected()
            self._set_state(FlashState.S3_DEVICE_DETECTED)
        elif step in _WRITE_STEPS:
            self._flash_page.show_flashing()
            self._flash_page.set_device_flashing()
            self._settings_page.set_method_enabled(False)
            self._enter_flashing_state()
        elif step == STEP_DONE:
            self._flash_page.set_device_done()

    def _set_state(self, state):
        if self.sm.state is state:
            return
        try:
            self.sm.transition_to(state)
        except ValueError:
            try:
                self.sm.force_state(state)
            except Exception:
                pass
        self._sync_install_nav_entry()

    def _enter_flashing_state(self):
        if self.sm.state is not FlashState.S4_FLASHING:
            if self.sm.state is FlashState.S2_WAIT_CONNECTION:
                self._set_state(FlashState.S3_DEVICE_DETECTED)
            self._set_state(FlashState.S4_FLASHING)
            if not self._flash_start_ts:
                self._flash_start_ts = time.time()
            self._elapsed_timer.start(1000)

    def _on_progress(self, percent):
        self._last_progress = percent
        if getattr(self, "_step_now", "") == STEP_EXTRACTING:
            self._flash_page.update_prep_progress(percent)
            return
        self._flash_page.update_progress(percent)
        self._retry_page.update_progress(percent)

    def _on_log_message(self, msg):
        # Backend/tool channel: a tool that prints a line and also reports it
        # through its callback would otherwise store the same event twice.
        self._append_log(msg, dedupe=True)

    def _append_log(self, msg, dedupe=False):
        text = str(msg or "")
        lines = text.splitlines() or ([text] if text else [])
        for line in lines:
            if line.strip():
                self._append_log_line(line, dedupe=dedupe)

    def _append_log_line(self, msg, dedupe=False):
        self._log_lines.append(msg)
        from ..diagnostics import LOG_RETAINED_LINES
        if len(self._log_lines) > LOG_RETAINED_LINES:
            self._log_lines = self._log_lines[-LOG_RETAINED_LINES:]
        try:
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().record_log(msg, dedupe=dedupe)
        except Exception:
            pass
        self.log_line_added.emit(msg)
        self._handle_backend_line(msg)

    def _handle_backend_line(self, line):
        """React to raw backend output with the right amount of UI.

        Only two kinds of line ever need a reaction (see
        :func:`flash_service.classify_backend_line`):

        * mtkclient's ``Hint:`` block is not an error — it restates step 1 of
          the connection guide, so it just re-arms the waiting stage.
        * errno 2 / 5 mean the player is wedged and must be reset by hand, so
          they raise the retry-guidance dialog.

        Everything else stays Diagnostics-only, which is where the raw output
        belongs.
        """
        kind = classify_backend_line(line)
        if not kind or not self._package_path:
            return  # No run in progress — log only.
        if kind == LINE_CONNECT_HINT:
            self._show_connect_guide_stage_one()
        elif kind == LINE_RETRY:
            self._show_retry_guidance(
                tr("retry_guidance_detail_errno").format(code=str(line).strip())
            )

    def _show_connect_guide_stage_one(self):
        """Return the UI to "power off the device and connect USB".

        Guarded to the waiting states: a hint that arrives once writing has
        started must not yank the user back to step 1 mid-flash.
        """
        if self.sm.state not in (
            FlashState.S2_WAIT_CONNECTION,
            FlashState.S3_DEVICE_DETECTED,
        ):
            return
        self._flash_page.set_waiting_device()
        self._flash_page.highlight_guide_step(1)
        self._set_state(FlashState.S2_WAIT_CONNECTION)
        if self._stack.currentIndex() != _PAGE_FLASH:
            self._nav_to_page(_PAGE_FLASH)

    def _show_retry_guidance(self, detail=""):
        """Explain the manual reset, at most once per flash attempt."""
        if self._retry_guidance_shown:
            return
        self._retry_guidance_shown = True
        dialog = RetryGuidanceDialog(self, detail=detail)
        dialog.exec()
        if dialog.want_retry():
            self._on_retry_flash()

    def _on_device_found(self, port):
        self._append_log(f"Device detected: {port}")
        self._flash_page.set_detected()

    def _on_device_lost(self):
        if self.sm.state is FlashState.S4_FLASHING:
            try:
                self.sm.transition_to(FlashState.S5_USB_DISCONNECTED)
            except ValueError:
                return
            self._elapsed_timer.stop()
            # The run is over (the target is gone), so the USB monitor has
            # nothing left to watch and must not keep polling in the
            # background; a retry starts a fresh one.
            self.service.stop_device_monitor()
            self.service.cancel_flash()
            self._show_install_failure(tr("err_usb_title"))
        elif self.sm.state in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            try:
                self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
            except ValueError:
                pass
            self._flash_page.set_waiting_device()

    def _on_monitor_error(self, msg):
        self._append_log(msg)

    def _on_flash_finished(self, ok, error_code):
        self._elapsed_timer.stop()
        # Device monitoring belongs to a run in progress and stops with it, on
        # success and failure alike: otherwise a failed attempt (the common
        # case when the device is not powered off properly) left a USB polling
        # thread behind for as long as the window stayed open. Pressing Retry
        # or starting a new install starts a fresh monitor.
        self.service.stop_device_monitor()
        try:
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().end_flash_session(ok, str(error_code or ""))
        except Exception:
            pass
        if ok:
            self._handle_flash_success()
        else:
            self._handle_flash_failure(error_code)

    def _handle_flash_success(self):
        try:
            self.sm.transition_to(FlashState.S5_COMPLETE)
        except ValueError:
            self.sm.force_state(FlashState.S5_COMPLETE)

        model = self._package_model or ""
        software = self._package_name or "Firmware"
        steps = install_power_on_steps(model)
        self._show_status(
            tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
        )

        # Record install for device tracking & future release reminders
        rel_info = getattr(self._select_page, "current_installed_release_info", lambda: None)()
        if rel_info:
            device_tracking.record_device_install(
                model=rel_info.get("model") or model,
                software_name=rel_info.get("software_name") or software,
                tag_name=rel_info.get("tag_name") or "",
                release_label=rel_info.get("release_label") or "",
                package_slug=rel_info.get("package_slug") or "",
                published_at=rel_info.get("published_at") or "",
                settings=self.settings,
            )
        elif self._package_name:
            device_tracking.record_device_install(
                model=model,
                software_name=software,
                tag_name="local",
                release_label=software,
                settings=self.settings,
            )
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()

        donation_disabled = device_tracking.is_donation_install_prompt_disabled(self.settings)
        eff_model = (model or self._package_model or "").upper()
        is_y_target = eff_model in ("Y1", "Y2")
        pkg_low = (self._package_name or "").lower()
        path_low = (self._package_path or "").lower()
        is_rockbox = "rockbox" in pkg_low or "rockbox" in path_low
        is_240p = "_240p" in path_low or "240p" in pkg_low
        is_360p_rockbox = bool(is_y_target and is_rockbox and not is_240p)

        self._is_360p_rockbox = is_360p_rockbox

        elapsed = self._elapsed_text()
        self._is_360p_rockbox = is_360p_rockbox
        self._install_complete = True
        self._compact_install_screen = True
        self._flash_page.show_flashing()
        self._flash_page.set_device_done()
        self._flash_page.show_completion_appeal(not donation_disabled, self._on_completion_dont_ask)
        self._apply_release_icon(complete=True)
        self._nav_to_page(_PAGE_FLASH)
        self._show_status(
            tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
        )
        del elapsed

    def _show_install_failure(self, message: str):
        """Failure stays on the install progress card. Retry is where Cancel was."""
        self._install_failed = True
        self._power_off_prompt = False
        self._install_complete = False
        self._flash_page.show_install_failure(message, self._last_progress or 0)
        self._nav_to_page(_PAGE_FLASH)

    def _handle_flash_failure(self, error_code):
        try:
            self.sm.transition_to(FlashState.S5_FAILED)
        except ValueError:
            self.sm.force_state(FlashState.S5_FAILED)
        self._show_install_failure(str(error_code or tr("flash_failed")))

    def _on_progress_retry(self):
        """Back to the power-off / unplug prompt. Does not start a flash."""
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._elapsed_timer.stop()
        self._install_failed = False
        self._power_off_prompt = True
        self._pre_install_guided = False
        if self.sm.state in (
            FlashState.S5_FAILED,
            FlashState.S5_USB_DISCONNECTED,
            FlashState.S4_FLASHING,
            FlashState.S5_COMPLETE,
        ):
            self.sm.reset_for_retry()
        self._flash_page.set_model(self._package_model)
        self._flash_page.show_power_off_prompt()
        self._nav_to_page(_PAGE_FLASH)

    def _on_power_off_continue(self):
        """The user confirmed the device is off and unplugged. Start the run."""
        self._power_off_prompt = False
        self._pre_install_guided = True
        self._flash_page.leave_power_off_prompt()
        self._begin_flash_flow()

    def _on_retry_flash(self):
        self.sm.reset_for_retry()
        try:
            from ..sp_flash_gui import update_sp_history_ini
            update_sp_history_ini(model=self._package_model)
        except Exception as e:
            logger.debug("Could not update SP history.ini on retry: %s", e)
        self._retry_page.update_info(
            self._package_name, self.sm.context.retry_count, "status_retrying"
        )
        self._retry_page.update_progress(0)
        self._nav_to_page(_PAGE_RETRY)
        QTimer.singleShot(0, self._begin_flash_flow)

    def _on_reconnect(self):
        self._set_state(FlashState.S2_WAIT_CONNECTION)
        self._flash_page.set_waiting_device()
        self._nav_to_page(_PAGE_FLASH)

    def _on_cancel_wait(self):
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self.sm.reset_full()
        self._nav_to_page(_PAGE_SELECT)

    def _on_method_changed(self, method):
        """Apply an install method chosen in Settings.

        The choice is always persisted. A run that is waiting for the device is
        restarted so the new backend takes over immediately; otherwise the new
        method applies from the next run.
        """
        if method == self._flash_method:
            return
        self._flash_method = method
        self.settings.setValue("flash_method", method)
        if method == METHOD_MTK_MAC:
            # Simulated macOS: most of that behaviour is decided at startup,
            # so offer the restart that actually switches the app over.
            self._append_log("Install method set to MTKClient (Mac) simulation.")
            self._restart_in_simulated_macos()
            return
        if self.sm.state not in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            self._append_log(f"Install method set to {_method_label(method)}.")
            return
        if method in ("sp", "auto"):
            try:
                from ..sp_flash_gui import update_sp_history_ini
                update_sp_history_ini(model=self._package_model)
            except Exception as e:
                logger.debug("Could not update SP history.ini on method change: %s", e)
        self._append_log(f"Flash method changed to {_method_label(method)}; restarting search...")
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._flash_page.set_method(method)
        pre_extracted = completed_extract_dir(self._package_path)
        self.service.start_flash(
            self._package_path, pre_extracted_dir=pre_extracted, method=method
        )
        self.service.start_device_monitor()

    def _restart_in_simulated_macos(self):
        """Relaunch in simulated macOS mode so the Mac flow genuinely applies.

        paths.SIMULATE_MACOS is read at import time and drives the backend
        matrix, the mac-centric prompts and the nav entries, so switching at
        runtime would leave a half-Mac UI. Restarting is the honest way in.
        """
        box = QMessageBox(self)
        box.setWindowTitle(tr("simulated_mac_title"))
        box.setText(tr_brand("simulated_mac_body"))
        restart_btn = box.addButton(
            tr("simulated_mac_restart_now"), QMessageBox.AcceptRole
        )
        box.addButton(tr("simulated_mac_later"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not restart_btn:
            return

        if getattr(sys, "frozen", False):
            program, args = sys.executable, sys.argv[1:]
        else:
            program, args = sys.executable, [sys.argv[0], *sys.argv[1:]]
        env = QProcessEnvironment.systemEnvironment()
        env.insert("INNIOASIS_SIMULATE_MACOS", "1")
        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(os.getcwd())
        proc.setProcessEnvironment(env)
        if not proc.startDetached():
            self._append_log(
                "Could not restart automatically; relaunch with --simulate-macos."
            )
            return
        QApplication.quit()

    def _on_cancel_flash(self):
        answer = QMessageBox.question(
            self, tr("flash_cancel_title"), tr("flash_cancel_msg"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.service.cancel_flash()
            self._elapsed_timer.stop()
            self._reset_after_run()

    def _on_completion_dont_ask(self):
        device_tracking.set_donation_ui_disabled(True, self.settings)
        device_tracking.set_donation_install_prompt_disabled(True, self.settings)
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()
        self._apply_donation_visibility()
        self._flash_page.show_completion_appeal(False, self._on_completion_dont_ask)

    def _apply_release_icon(self, complete: bool = False) -> None:
        """Squircle for the install card: cached release icon, else the app icon."""
        from .release_icon import load_release_pixmap
        from ..release_icons import icon_candidate_urls

        page = getattr(self, "_select_page", None)
        release = getattr(page, "_current_selected_rel", None) if page else None
        package = getattr(page, "_details_package", None) if page else None
        pixmap = load_release_pixmap(release, package, allow_network=False)
        self._flash_page.set_release_icon(pixmap, complete=complete)
        if not icon_candidate_urls(release, package):
            return

        class _Loader(QThread):
            loaded = Signal(object)

            def __init__(self, rel, pkg):
                super().__init__()
                self._rel = rel
                self._pkg = pkg

            def run(self):
                self.loaded.emit(load_release_pixmap(self._rel, self._pkg, allow_network=True))

        previous = getattr(self, "_icon_loader", None)
        if previous is not None and previous.isRunning():
            previous.requestInterruption()
        loader = _Loader(release, package)
        loader.loaded.connect(lambda pix, done=complete: self._flash_page.set_release_icon(pix, complete=done or getattr(self, "_install_complete", False)))
        loader.finished.connect(loader.deleteLater)
        self._icon_loader = loader
        loader.start()

    def _reset_after_run(self):
        self._pre_install_guided = False
        self._install_complete = False
        self._install_failed = False
        self._power_off_prompt = False
        self._compact_install_screen = False
        self.sm.reset_full()
        self._package_path = ""
        self._package_name = ""
        self._flash_start_ts = 0.0
        self._settings_page.set_method_enabled(True)
        self.service.stop_device_monitor()
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_SELECT)

    def _tick_elapsed(self):
        self._flash_page.update_time(self._elapsed_text(), self._eta_text())

    def _elapsed_text(self):
        secs = int(time.time() - self._flash_start_ts) if self._flash_start_ts else 0
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _eta_text(self):
        pct = max(self._last_progress or 0, 1)
        secs = int((time.time() - self._flash_start_ts) * (100 - pct) / pct) if self._flash_start_ts else 0
        if secs <= 0:
            return "--:--"
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _diagnostics_unlocked(self) -> bool:
        return bool(getattr(self, "_diagnostics_session", False))

    def _sync_diagnostics_button(self) -> None:
        show = self._diagnostics_unlocked()
        if hasattr(self, "_log_btn"):
            self._log_btn.setVisible(show)
            self._log_btn.setEnabled(show)

    def _unlock_diagnostics(self) -> None:
        """Show Diagnostics for this session only. Logs were already being kept."""
        self._diagnostics_session = True
        self._sync_diagnostics_button()

    def _ensure_diagnostics_page(self):
        page = getattr(self, "_diagnostics_page", None)
        if page is not None:
            return page
        page = DiagnosticsView()
        page.close_requested.connect(self._leave_diagnostics)
        placeholder = getattr(self, "_diag_placeholder", None)
        idx = self._stack.indexOf(placeholder) if placeholder is not None else -1
        if placeholder is not None and idx >= 0:
            self._stack.removeWidget(placeholder)
            placeholder.hide()
        self._stack.insertWidget(idx if idx >= 0 else _PAGE_DIAGNOSTICS, page)
        self._diagnostics_page = page
        return page

    def _detach_diagnostics_tail(self) -> None:
        if not getattr(self, "_diagnostics_tail_connected", False):
            return
        page = getattr(self, "_diagnostics_page", None)
        self._diagnostics_tail_connected = False
        if page is None:
            return
        try:
            self.log_line_added.disconnect(page.append_line)
        except Exception:
            pass

    def _show_diagnostics(self):
        """Open the in-window diagnostics view, including during an install."""
        if not self._diagnostics_unlocked():
            return
        if (
            self._diagnostics_page is not None
            and self._stack.currentIndex() == _PAGE_DIAGNOSTICS
        ):
            self._leave_diagnostics()
            return
        self._page_before_diagnostics = self._stack.currentIndex()
        page = self._ensure_diagnostics_page()
        # The diagnostics manager is the retained log. A copy of the window's
        # short list would freeze the count and drop tool output.
        self._detach_diagnostics_tail()
        page.set_lines(None)
        self.log_line_added.connect(page.append_line)
        self._diagnostics_tail_connected = True
        self._nav_to_page(_PAGE_DIAGNOSTICS)

    def _leave_diagnostics(self):
        self._detach_diagnostics_tail()
        if self._install_run_active():
            self._nav_to_page(_PAGE_FLASH)
            return
        prev = getattr(self, "_page_before_diagnostics", _PAGE_SELECT)
        if prev in (_PAGE_DIAGNOSTICS, None):
            prev = _PAGE_SELECT
        self._nav_to_page(prev)

    def _show_linux_setup(self):
        from .dialogs import LinuxSetupDialog
        dlg = LinuxSetupDialog(self, auto_start=False)
        dlg.exec()

    def _open_sp_flash_tool_gui(self):
        """Open the bundled SP Flash Tool GUI with the best firmware we have.

        A run in this window is cancelled first so the GUI tool can own USB.
        Firmware is chosen in this order: a previous install's extracted cache,
        then a highlighted catalogue or local file (downloaded and extracted
        if it is not cached yet), then the most recent download or cache.
        """
        from ..sp_flash_gui import (
            cached_install_firmware,
            is_sp_flash_gui_supported,
            resolve_cached_firmware,
        )

        if not is_sp_flash_gui_supported():
            QMessageBox.information(
                self,
                tr("sp_gui_title"),
                tr("sp_gui_not_supported"),
            )
            return

        if hasattr(self, "service") and self.service:
            self.service.cancel_flash()
            self.service.stop_device_monitor()
        if getattr(self, "_download_active", False) and hasattr(self, "_select_page"):
            self._select_page.cancel_download()
            self._download_active = False
        if getattr(self, "_elapsed_timer", None) is not None:
            self._elapsed_timer.stop()

        latest = device_tracking.get_latest_package(self.settings)
        scatter, extract = cached_install_firmware(latest)
        if scatter is not None:
            model = (latest or {}).get("model") or self._package_model or ""
            self._launch_sp_gui(scatter, extract, model)
            return

        focused = None
        if hasattr(self, "_select_page"):
            focused = self._select_page.focused_firmware()
        if focused and self._select_page.begin_external_prepare(
            focused, self._on_sp_gui_firmware_ready
        ):
            return

        scatter, extract, model = resolve_cached_firmware(model=self._package_model or "")
        if scatter is not None:
            self._launch_sp_gui(scatter, extract, model or self._package_model or "")
            return
        QMessageBox.warning(
            self,
            tr("sp_gui_title"),
            tr("sp_gui_no_cached_firmware"),
        )

    def _on_sp_gui_firmware_ready(self, ok, extract_dir, err):
        from ..flash_service import _find_scatter

        if not ok or not extract_dir:
            QMessageBox.warning(
                self,
                tr("sp_gui_error_title"),
                f"{tr('sp_gui_error_desc')}\n\n{err or tr('sp_gui_no_cached_firmware')}",
            )
            return
        scatter = _find_scatter(Path(extract_dir), allow_raise=False)
        if not scatter or not Path(scatter).is_file():
            QMessageBox.warning(
                self,
                tr("sp_gui_title"),
                tr("sp_gui_no_cached_firmware"),
            )
            return
        package_path = ""
        name = Path(extract_dir).name
        if hasattr(self, "_select_page"):
            package_path = getattr(self._select_page, "_current_package_path", "") or ""
            name = getattr(self._select_page, "_current_package_name", "") or name
        device_tracking.record_latest_package(
            model=self._package_model or "",
            software_name=name,
            tag_name="",
            package_path=package_path,
            extract_dir=str(Path(extract_dir).resolve()),
            scatter_path=str(Path(scatter).resolve()),
            settings=self.settings,
        )
        self._launch_sp_gui(Path(scatter), Path(extract_dir), self._package_model or "")

    def _launch_sp_gui(self, scatter, extract_dir, model):
        from ..sp_flash_gui import launch_sp_flash_tool_gui

        ok, msg = launch_sp_flash_tool_gui(
            model=model or "",
            scatter_path=scatter,
            extract_dir=extract_dir,
        )
        if not ok:
            QMessageBox.warning(
                self,
                tr("sp_gui_error_title"),
                f"{tr('sp_gui_error_desc')}\n\n{msg}",
            )
            return
        self._show_status(tr("sp_gui_launched_status"), 10000)

    def _check_linux_first_run(self):
        if paths.IS_MAC or os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("CI"):
            return
        from .. import linux_sp_flash
        from .dialogs import LinuxSetupDialog

        stage = linux_sp_flash.stage_dir()
        first_run_done = self.settings.value("linux_first_run_completed", False, type=bool)
        files_ready = linux_sp_flash.files_ready(stage)

        if not files_ready or not first_run_done:
            self.settings.setValue("linux_first_run_completed", True)
            dlg = LinuxSetupDialog(self, auto_start=True)
            dlg.exec()

    def _open_credits(self):
        from ..browser import open_browser
        open_browser("https://innioasis.app/credits.html?thank-you=1")

    def _on_language_changed(self, index):
        lang = self._lang_combo.itemData(index)
        translator().set_language(lang)
        self.settings.setValue("language", lang)
        self._retranslate_all()

    def _retranslate_all(self):
        self._brand_label.setText(get_brand_name())
        self.setWindowTitle(f"{get_app_name()} v{APP_VERSION}")
        for key, (btn, _idx) in self._nav_buttons.items():
            btn.setText(tr(key))
        self._sync_install_nav_entry()
        self._support_btn.setText(tr("nav_donate"))
        self._log_btn.setText(tr("nav_log"))
        self._check_updates_btn.setText(tr("nav_check_updates"))
        if hasattr(self, "_linux_setup_btn"):
            self._linux_setup_btn.setText(tr("nav_linux_setup"))
        if hasattr(self, "_sp_flash_tool_btn"):
            self._sp_flash_tool_btn.setText(tr("nav_sp_flash_tool_gui"))
        if hasattr(self, "_lang_label"):
            self._lang_label.setText(tr("nav_language"))
        if hasattr(self, "_select_page"):
            self._select_page.retranslate()
            if hasattr(self._select_page, "_title"):
                curr_idx = self._stack.currentIndex() if hasattr(self, "_stack") else _PAGE_SELECT
                self._select_page._title.setText(
                    self._page_title(curr_idx, self._install_run_active())
                )
        self._flash_page.retranslate()
        self._error_page.retranslate()
        self._retry_page.retranslate()
        if hasattr(self, "_settings_page"):
            self._settings_page.retranslate()
        if self._diagnostics_page is not None and hasattr(self._diagnostics_page, "retranslate"):
            self._diagnostics_page.retranslate()
        if self.statusBar() and hasattr(self.statusBar(), "retranslate"):
            self.statusBar().retranslate()
        self._apply_generic_mtk_branding()

    def _on_manifest_loaded(self, entries):
        if entries:
            # The live catalogue defines which models are offered; re-filter the
            # drop-down before repopulating the software list for the current
            # model.
            self._select_page.refresh_models()
            self._select_page._on_model_changed()
            # Check for device firmware updates when new releases/manifest is loaded
            self._check_device_firmware_updates(manual=False)

    def _start_auto_update_check(self):
        if self._update_offered_once:
            return
        self._update_manual_pending = False
        self._run_update_check()

    def _on_check_updates_clicked(self):
        self._update_manual_pending = True
        self._run_update_check()

    def _run_update_check(self):
        old = getattr(self, "_update_worker", None)
        if old is not None and old.isRunning():
            try:
                old.finished.disconnect()
            except Exception:
                pass
            old.requestInterruption()
            old.wait(100)
        worker = UpdateCheckWorker(UPDATE_REPO, APP_VERSION, self)
        self._update_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(
            lambda info: self._on_update_worker_finished(worker, info, self._update_manual_pending)
        )
        worker.start()

    def _on_update_worker_finished(self, worker, info, manual):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_update_worker", None):
            self._on_update_check_done(info, manual)

    def _on_update_check_done(self, info, manual):
        if not isinstance(info, UpdateInfo):
            info = UpdateInfo()
        if not manual and self._update_offered_once:
            return
        if info.tag:
            skipped = str(self.settings.value("update_skipped_version", ""))
            if info.version == skipped and not manual:
                return
            self._update_offered_once = True
            dlg = UpdateAvailableDialog(
                self,
                info=info,
                current_version=APP_VERSION,
                on_skip=lambda v: self.settings.setValue("update_skipped_version", v),
            )
            dlg.exec()
        elif manual:
            QMessageBox.information(
                self,
                tr("update_available"),
                tr("update_check_failed")
                if info.failed
                else tr("update_up_to_date").format(version=APP_VERSION),
            )

    def _check_device_firmware_updates(self, manual=False):
        old = getattr(self, "_device_update_worker", None)
        if old is not None and old.isRunning():
            try:
                old.finished.disconnect()
            except Exception:
                pass
            old.requestInterruption()
            old.wait(100)
        worker = DeviceUpdateCheckWorker(self.settings, ignore_last_notified=manual, parent=self)
        self._device_update_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(lambda updates: self._on_device_worker_finished(worker, updates, manual))
        worker.start()

    def _on_device_worker_finished(self, worker, updates, manual):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_device_update_worker", None):
            self._on_device_updates_checked(updates, manual)

    def _on_device_updates_checked(self, updates, manual=False):
        if updates:
            for upd in updates:
                def on_start(info):
                    self._nav_to_page(_PAGE_SELECT)
                    self._select_page.start_install_for_release(
                        model=info.get("model", "Y1"),
                        software_name=info.get("software_name", ""),
                        tag_name=info.get("latest_tag"),
                        release=info.get("latest_release"),
                        package=info.get("package"),
                    )

                def on_view(info):
                    self._nav_to_page(_PAGE_SELECT)
                    self._select_page.navigate_to_package(
                        info.get("model", "Y1"),
                        info.get("software_name", ""),
                        tag_name=info.get("latest_tag"),
                    )

                def on_disable(model):
                    device_tracking.set_device_reminder_enabled(model, False, settings=self.settings)
                    if hasattr(self, "_settings_page"):
                        self._settings_page.refresh_settings()

                dlg = ReleaseReminderDialog(
                    parent=self,
                    update_info=upd,
                    on_view_release=on_view,
                    on_disable_reminders=on_disable,
                    on_start_install=on_start,
                    flash_method=self._flash_method,
                )
                dlg.exec()
                device_tracking.set_last_notified_tag(upd.get("model"), upd.get("latest_tag"), settings=self.settings)
                if getattr(dlg, "_install_started", False):
                    break
        elif manual:
            tracked = device_tracking.get_all_device_installs(self.settings)
            if tracked:
                QMessageBox.information(
                    self,
                    tr("reminder_new_release_title"),
                    tr("settings_firmware_up_to_date"),
                )
            else:
                QMessageBox.information(
                    self,
                    tr("reminder_new_release_title"),
                    tr("settings_no_devices_tracked"),
                )

    def _check_legacy_installations(self):
        try:
            prompted = self.settings.value("legacy_cleanup_prompted", False, type=bool)
            if prompted:
                return
            from ..legacy_cleanup import detect_legacy_installations
            info = detect_legacy_installations()
            if info.get("has_legacy") or info.get("has_platform_tools"):
                from .dialogs import LegacyMigrationDialog
                dlg = LegacyMigrationDialog(self, scan_info=info)
                dlg.exec()
                self.settings.setValue("legacy_cleanup_prompted", True)
        except Exception:
            pass

    def _show_status(self, text, timeout_ms=0):
        """Show a transient message in the status bar; it reverts to the
        donations/goal display once cleared or timed out."""
        sb = self.statusBar()
        if sb is None:
            return
        sb.showMessage(text, int(timeout_ms or 0))
        if not sb.isVisible():
            sb.setVisible(True)

    def _on_status_message_changed(self, text):
        if not text:
            self._apply_donation_visibility()

    def _apply_donation_visibility(self, is_disabled=None):
        if is_disabled is None:
            is_disabled = device_tracking.is_donation_ui_disabled(self.settings)
        sb = self.statusBar()
        if sb is not None:
            if hasattr(sb, "set_donations_enabled"):
                sb.set_donations_enabled(not is_disabled)
            else:
                sb.setVisible(not is_disabled)
        if hasattr(self, "_support_btn") and self._support_btn is not None:
            self._support_btn.setVisible(not is_disabled)
        if hasattr(self, "_version_label") and self._version_label is not None:
            self._version_label.setVisible(not is_disabled)
        if hasattr(self, "_credits_btn") and self._credits_btn is not None:
            self._credits_btn.setVisible(not is_mediatek_installer() and not is_disabled)
        self._adjust_window_geometry()

    def _apply_generic_mtk_branding(self):
        """Apply the identity of whichever build is running.

        Offline mode keeps Updater CE's name; only the generic MediaTek
        Installer build renames the app, and it is the only one that loses the
        online-catalogue affordances it cannot use.
        """
        offline = is_generic_mtk()
        mediatek = is_mediatek_installer()
        self.setWindowTitle(f"{get_app_name()} v{APP_VERSION}")
        if hasattr(self, "_brand_label"):
            # Reads as "Installer 3.0" beside the version badge.
            self._brand_label.setText(get_brand_name())
        # Update checks require an internet connection, so offline/generic hides them.
        # Credits and author attribution remain visible in offline mode for Updater CE,
        # but hidden in generic MediaTek Installer builds.
        if hasattr(self, "_credits_btn"):
            self._credits_btn.setVisible(not mediatek)
        if hasattr(self, "_check_updates_btn"):
            self._check_updates_btn.setVisible(not offline and not is_offline_mode())
        if hasattr(self, "_settings_page"):
            self._settings_page.apply_brand_mode(mediatek)
        if hasattr(self, "_select_page"):
            self._select_page.apply_generic_mode(offline)
        self._apply_donation_visibility()

    def _on_offline_mode_changed(self, enabled: bool):
        # Offline mode hides the online catalogue and its affordances, but it
        # never renames anything: the brand is fixed by the build, not by this
        # checkbox.
        self._apply_generic_mtk_branding()

    def _on_donations_updated(self, donations):
        if donations:
            self._donations = donations

    def _on_support_clicked(self):
        self._show_donation_dialog(context="general")

    def _show_donation_dialog(self, context="general", *args, **kwargs):
        eff_model = self._package_model or ""
        eff_name = self._package_name or ""
        is_360p_rockbox = kwargs.get("is_360p_rockbox")
        if is_360p_rockbox is None:
            is_360p_rockbox = getattr(self, "_is_360p_rockbox", None)
        if is_360p_rockbox is None:
            is_y = eff_model.upper() in ("Y1", "Y2")
            pkg_low = eff_name.lower()
            path_low = (self._package_path or "").lower()
            is_rb = "rockbox" in pkg_low or "rockbox" in path_low
            is_240 = "_240p" in path_low or "240p" in pkg_low
            is_360p_rockbox = bool(is_y and is_rb and not is_240) if context == "install_success" else False
        dialog = DonationDialog(
            parent=self,
            context=context,
            model=eff_model,
            software_name=eff_name,
            donations=self._donations,
            on_dont_ask_again=self._on_donation_dont_ask_again,
            is_360p_rockbox=is_360p_rockbox,
        )
        dialog.exec()

    def _on_donation_dont_ask_again(self):
        """"Don't ask me again" on the Support dialog: the user is not
        interested in the donations model at all, so also turn off the
        donor / donation info shown at the bottom of the window (the
        Settings → hide-donations option) and reflect both immediately."""
        device_tracking.set_donation_install_prompt_disabled(True, settings=self.settings)
        device_tracking.set_donation_ui_disabled(True, settings=self.settings)
        self._apply_donation_visibility(True)
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()

    def cleanup_workers(self):
        """Cleanly terminate and wait for any background workers."""
        self.service.cleanup()
        select_page = getattr(self, "_select_page", None)
        if select_page is not None:
            rw = getattr(select_page, "_releases_worker", None)
            if rw is not None and rw.isRunning():
                rw.requestInterruption()
                rw.wait(1500)
            dw = getattr(select_page, "_download_worker", None)
            if dw is not None and dw.isRunning():
                dw.cancel()
                dw.wait(1500)
            mon = getattr(select_page, "_monitor", None)
            if mon is not None:
                mon.stop_monitoring()
        for w in (
            getattr(self, "_manifest_worker", None),
            getattr(self, "_update_worker", None),
            getattr(self, "_device_update_worker", None),
            getattr(select_page, "_manifest_worker", None) if select_page else None,
        ):
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(1500)

    def close(self):
        return super().close()

    def closeEvent(self, event):
        if self._install_run_active():
            is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or bool(os.environ.get("INNIOASIS_HEADLESS"))
            if not is_headless or getattr(self, "_test_close_prompt", False):
                app_name = get_app_name()
                prompt_pattern = tr("flash_close_confirm")
                if "{app}" in prompt_pattern:
                    prompt_text = prompt_pattern.format(app=app_name)
                else:
                    prompt_text = f"Are you sure you want to stop the install and close {app_name}?"
                ans = QMessageBox.question(
                    self,
                    tr("flash_cancel_title") or f"Stop Install - {app_name}",
                    prompt_text,
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if ans != QMessageBox.Yes:
                    event.ignore()
                    return
            if hasattr(self, "service") and self.service:
                try:
                    self.service.cancel_flash()
                except Exception:
                    pass

        if hasattr(self, "_theme_watcher") and self._theme_watcher:
            try:
                self._theme_watcher.stop()
            except Exception:
                pass
        self.cleanup_workers()
        super().closeEvent(event)


_METHOD_LABELS = {
    "auto": "Auto",
    "sp": "SP Flash Tool",
    "mtk": "MTKClient",
    "mtk_mac": "MTKClient (Mac)",
}


def _method_label(method: str) -> str:
    if paths.IS_MAC:
        return "MTKClient"
    return _METHOD_LABELS.get(method, method)
