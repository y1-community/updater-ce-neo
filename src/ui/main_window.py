"""Main window — left navigation + right content area.

Select Software is the application's start page (no separate Home tab). After
a package is chosen, the flash backend launches immediately — SP Flash Tool
(Windows / staged Linux) or mtkclient (all platforms) — and the UI guides the
user to power off and connect their device while the backend searches USB.

The install method is chosen in Settings (Windows/Linux default to SP Flash
Tool's console-mode XML flow; macOS has MTKClient only) and can be switched
while waiting for a device, which restarts the run with the new backend.
"""

# PEP 810 (Python 3.15+): module-level imports listed here are resolved on first
# use instead of at import time. The interpreter consults this *as each import
# executes*, so it has to be assigned before the import block below -- placed
# after it, the same list is inert (verified on 3.15.0). Interpreters before
# 3.15 read nothing from it, so nothing changes on 3.11-3.14; there, deferrals
# are the function-local imports instead (see src/network.py).
# Listed: modules nothing touches until the window has been up for a while
# (the update check and the donor fetch run seconds later). Every module that
# imports one of them carries the same list -- one eager importer anywhere
# defeats the deferral (verified on 3.15.0).
__lazy_modules__ = ["src.updates", "src.donors"]

import logging
import os
import platform
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QEventLoop,
    QPoint,
    QPropertyAnimation,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QGuiApplication, QIcon, QPalette, QPixmap
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
from ..worker_lifecycle import retire_worker
from ..updates import UpdateCheckWorker, UpdateInfo
from ..donation_dialog import DonationStatusBar
from ..donors import cached_donors_path, load_donors_file, parse_donors_csv_text
from ..flash_service import (
    LINE_CONNECT_HINT,
    LINE_RETRY,
    METHOD_MTK,
    METHOD_SP,
    STEP_DETECT,
    STEP_DONE,
    STEP_DOWNLOAD_BL,
    STEP_DOWNLOAD_DA,
    STEP_EXTRACTING,
    STEP_PLEASE_WAIT,
    STEP_WAITING,
    STEP_WRITE,
    METHOD_SP,
    FlashService,
    backend_method,
    classify_backend_line,
    completed_extract_dir,
    is_com_port_error,
    is_connect_failure,
    is_sp_driver_error,
    is_wrong_model_error,
    normalise_method,
    sp_error_code,
)
from ..i18n import initial_language, is_english_language, tr, tr_brand, translator
from ..state import FlashState, StateMachine
from .dialogs import (
    DiagnosticsView,
    FlashCompleteDialog,
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
from .support_page import SupportPage
from .icons import get_symbol_icon
from .widgets import CurrentPageStack
from .surfaces import paint_window_surface
from .sidebar import (
    SidebarButton,
    SidebarWidth,
    sidebar_row_min_height,
    sidebar_row_spacing,
)

logger = logging.getLogger(__name__)

_PAGE_SELECT = 0
_PAGE_FLASH = 1
_PAGE_ERROR = 2
_PAGE_RETRY = 3
_PAGE_SETTINGS = 4
_PAGE_DIAGNOSTICS = 5
_PAGE_NOTICE = 6
_PAGE_SUPPORT = 7

# Choose Software, and the app's starting size: the compact window the app has
# always fitted on small screens. It is also the floor, and the ceiling is the
# widest catalogue layout — the window grows to the content in between, so a
# page that needs room takes it and a short one gives it back.
DEFAULT_WINDOW_WIDTH = 640
DEFAULT_WINDOW_HEIGHT = 440
MINIMUM_WINDOW_WIDTH = 640
MINIMUM_WINDOW_HEIGHT = 340
MAXIMUM_WINDOW_WIDTH = 1240
MAXIMUM_SELECT_WINDOW_HEIGHT = 720

# Slack added to a page's own content height when the window hugs it, so the
# last card's shadow and the status bar are never clipped.
CONTENT_FIT_SLACK = 16


def default_window_size(screen=None) -> tuple[int, int]:
    """Client size for Choose Software and the rest of the ordinary UI.

    Clamped to the screen the window lives on. The catalogue size is wide
    enough that it does not fit every display, and a window larger than the
    screen would open with its edges — and the page title — off-screen.
    """
    width, height = DEFAULT_WINDOW_WIDTH, DEFAULT_WINDOW_HEIGHT
    try:
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            width = max(MINIMUM_WINDOW_WIDTH, min(width, int(available.width())))
            height = max(MINIMUM_WINDOW_HEIGHT, min(height, int(available.height())))
    except Exception:
        logger.debug("Screen clamp failed", exc_info=True)
    return width, height


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

# The worker's way of saying "no honest estimate yet": the card then shows the
# elapsed time instead of inventing a number.
ETA_UNKNOWN = -1

_STEP_KEY = {
    STEP_EXTRACTING: "step_extract",
    STEP_PLEASE_WAIT: "flash_please_wait",
    STEP_WAITING: "step_wait",
    STEP_DETECT: "step_detect",
    STEP_DOWNLOAD_DA: "step_download_da",
    STEP_DOWNLOAD_BL: "step_download_bl",
    STEP_WRITE: "step_write",
    STEP_DONE: "step_done",
}

_WRITE_STEPS = (STEP_DOWNLOAD_DA, STEP_DOWNLOAD_BL, STEP_WRITE)

# Failure codes whose only real fix is a hardware reset. Filesystem/package
# failures must stay in the ordinary install card, not this device guidance.
_RETRY_GUIDANCE_CODES = ("CONNECTION_FAILED", "MTK_INIT_FAILED")
_CACHE_MISSING_MARKERS = ("filenotfounderror", "no such file or directory", "cannot find the file")

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
        if event.button() == Qt.LeftButton and not caption_drag_blocked(
            self.childAt(event.position().toPoint())
        ):
            win = self.window()
            bounds = getattr(win, "_caption_button_bounds", None)
            local = event.position().toPoint()
            if caption_button_hit(local.x(), local.y(), bounds) is not None:
                super().mousePressEvent(event)
                return
            if begin_unified_caption_drag(win):
                event.accept()
                return
            if win is not None:
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

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not caption_drag_blocked(
            self.childAt(event.position().toPoint())
        ):
            win = self.window()
            if win is not None:
                if win.isMaximized():
                    win.showNormal()
                else:
                    win.showMaximized()
                controls = getattr(win, "_window_controls", None)
                if controls is not None:
                    controls.set_state(bool(win.isMaximized()))
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


def caption_drag_blocked(widget) -> bool:
    """True when a title-band control must keep the click for itself."""
    from PySide6.QtWidgets import QAbstractSlider, QAbstractSpinBox, QLineEdit

    probe = widget
    while probe is not None:
        if isinstance(probe, (QPushButton, QComboBox, QLineEdit, QAbstractSpinBox, QAbstractSlider)):
            return True
        if isinstance(probe, QLabel) and (
            probe.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByMouse
        ):
            return True
        probe = probe.parentWidget()
    return False


def pass_caption_presses(widget) -> None:
    """Let a title icon or label start a window move instead of eating the press."""
    if widget is None:
        return
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)


def begin_unified_caption_drag(window) -> bool:
    """Move the window the way its native title bar does.

    Windows sends WM_NCLBUTTONDOWN / HTCAPTION, so double-click maximize stays
    the system's gesture. macOS uses the AppKit window drag. Other desktops
    use Qt startSystemMove.
    """
    if window is None:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = int(window.winId())
            user32 = ctypes.windll.user32
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, 0x00A1, 2, 0)
            return True
        except Exception:
            return False
    if sys.platform == "darwin":
        try:
            import AppKit

            from .glass import _get_nsview

            ns_app = AppKit.NSApplication.sharedApplication()
            curr_evt = ns_app.currentEvent()
            if curr_evt:
                view = _get_nsview(window)
                ns_win = view.window() if view else None
                if ns_win is not None:
                    ns_win.performWindowDragWithEvent_(curr_evt)
                    return True
        except Exception:
            pass
    handle = window.windowHandle() if hasattr(window, "windowHandle") else None
    if handle is not None:
        try:
            return bool(handle.startSystemMove())
        except Exception:
            return False
    return False


def client_rect_for_unified_caption(rect, *, maximized: bool, frame_x: int, frame_y: int, padded: int):
    """Client edges when the caption text band is part of the window.

    Restored windows keep the side and bottom frame and pull the top edge up
    to the window top, so the in-app title sits where the native caption was.
    Maximized windows inset every side by the frame thickness.
    """
    left, top, right, bottom = rect
    inset_x = int(frame_x) + int(padded)
    inset_y = int(frame_y) + int(padded)
    if maximized:
        return (left + inset_x, top + inset_y, right - inset_x, bottom - inset_y)
    # Restored windows drop the caption but keep the resize frame exactly as
    # Windows measures it. Qt sizes the window as client + (SM_CXSIZEFRAME +
    # SM_CXPADDEDBORDER) per side, so a thinner edge here hands the app a
    # client *wider* than the width Qt asked for and painted: the extra
    # columns stay unpainted and show as an opaque black bar down the right
    # edge. Matching the frame keeps Qt's own arithmetic true; the band that
    # remains belongs to the frame and is drawn by DWM, not left blank.
    return (left + inset_x, top, right - inset_x, bottom - inset_y)


HTCAPTION = 2
HTMINBUTTON = 8
HTMAXBUTTON = 9
HTCLOSE = 20


def caption_button_hit(x: int, y: int, bounds) -> int | None:
    """Hit-test the min / max / close strip. None when the point is outside it."""
    if not bounds:
        return None
    left, top, right, bottom = bounds
    if right <= left or bottom <= top:
        return None
    if x < left or x >= right or y < top or y >= bottom:
        return None
    span = right - left
    slot = min(2, max(0, int((x - left) * 3 / span)))
    return (HTMINBUTTON, HTMAXBUTTON, HTCLOSE)[slot]


def title_content_right_margin(window_width: int, bounds, *, fallback: int = 138) -> int:
    """Keep the heading clear of the caption buttons."""
    if not bounds or bounds[2] <= bounds[0]:
        return fallback
    reserved = max(0, int(window_width) - int(bounds[0])) + 8
    return max(int(fallback), reserved)


def can_inline_title_with_window_controls() -> bool:
    """Return True if the current platform/DE supports inlining custom title/controls seamlessly."""
    if os.environ.get("UPDATER_TRADITIONAL_TITLE") == "1":
        return False
    if sys.platform == "darwin":
        return True
    if sys.platform == "win32" or platform.system() == "Windows":
        return True
    if sys.platform.startswith("linux"):
        # On Linux, prefer native window decorations for better theme integration
        # like Konsole - show app name and window controls in the system titlebar
        return False
    if os.environ.get("UPDATER_INLINE_TITLE") == "1":
        return True
    return False


# Floor for the install window's height: the progress card plus whatever
# chrome (header, status line) the current scenario shows must fit. The window
# keeps the app's full width while it installs — only the height hugs the card,
# and it grows again for on-screen prompts, the completion card and the
# donation appeal.
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
    if donations_disabled:
        return max(COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS, page_h + prompt_h + header_h)
    return max(COMPACT_INSTALL_MIN_HEIGHT, page_h + prompt_h + status_h + header_h)


def page_title_alignment() -> Qt.AlignmentFlag:
    """Where the large page title sits in the header.

    Controls on the left (macOS, and desktops that put close/minimize there)
    leave the trailing edge of the window free, so the title's last glyph
    lines up with that edge. Controls on the right keep the title at the start,
    beside the app name.
    """
    if interface_is_rtl():
        # A mirrored interface starts at the right edge.
        return Qt.AlignRight | Qt.AlignVCenter
    if are_window_controls_on_left():
        return Qt.AlignRight | Qt.AlignVCenter
    return Qt.AlignLeft | Qt.AlignVCenter


def interface_is_rtl() -> bool:
    """True when the active language reads right to left."""
    try:
        from ..i18n import is_rtl_language

        return is_rtl_language()
    except Exception:
        return False


def apply_interface_direction(app=None) -> None:
    """Mirror the whole interface for right-to-left languages.

    Qt mirrors widget layouts with ``RightToLeft``: the navigation rail moves
    to the right, rows, buttons and dialogs re-order themselves, and text
    aligns to the right. Images are not flipped — the artwork keeps its own
    pixels, only its position in the mirrored layout changes.
    """
    from PySide6.QtWidgets import QApplication

    app = app or QApplication.instance()
    if app is None:
        return
    direction = (
        Qt.LayoutDirection.RightToLeft
        if interface_is_rtl()
        else Qt.LayoutDirection.LeftToRight
    )
    if app.layoutDirection() != direction:
        app.setLayoutDirection(direction)


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


class _DropCheckThread(QThread):
    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.result = (False, "", "missing_scatter")

    def run(self) -> None:
        from ..drop_install import inspect_dropped_path

        self.result = inspect_dropped_path(Path(self.path))


class _DropOverlay(QWidget):
    """Dim the window and name the drop while a firmware file is dragged over it."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window
        self.setAcceptDrops(True)
        self.hide()
        layout = QVBoxLayout(self)
        self._label = QLabel(tr("drop_firmware_here"))
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setAutoFillBackground(False)
        self._label.setStyleSheet("")
        font = self._label.font()
        font.setPixelSize(18)
        font.setWeight(QFont.Weight.Bold)
        self._label.setFont(font)
        pal = self._label.palette()
        pal.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        self._label.setPalette(pal)
        self._label.setForegroundRole(QPalette.ColorRole.WindowText)
        layout.addWidget(self._label)

    def paintEvent(self, event):  # noqa: N802
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        # A dim scrim, not a solid plate. The window material stays visible.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 64))
        painter.end()

    def dragEnterEvent(self, event):  # noqa: N802
        self._window._drag_enter_firmware(event)

    def dragMoveEvent(self, event):  # noqa: N802
        self._window._drag_enter_firmware(event)

    def dragLeaveEvent(self, event):  # noqa: N802
        self.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self.hide()
        self._window._drop_firmware(event)


class MainWindow(QMainWindow):
    # Report from a background flash-tool sweep; ``list`` of what it stopped.
    sweep_report = Signal(list)
    # Result of the post-disconnect USB recovery (a UsbRecoveryReport).
    usb_recovery_report = Signal(object)

    log_line_added = Signal(str)

    def _window_title_for_activity(self, _activity: str = "") -> str:
        return f"{get_app_name()} v{APP_VERSION}"

    def __init__(self):
        super().__init__()
        self._gui_current_identity = None
        # How wide the rail is lives in one place; this holds its last answer.
        self._rail_width = SidebarWidth()
        self.settings = QSettings("innioasis", "updater")
        translator().set_language(
            initial_language(str(self.settings.value("language", "") or ""))
        )
        if sys.platform != "darwin":
            icon = paths.RESOURCES_DIR / "icon.ico"
            if not icon.exists():
                icon = paths.RESOURCES_DIR / "icon.png"
            if icon.exists():
                self.setWindowIcon(QIcon(str(icon)))
        self.setWindowTitle(self._window_title_for_activity(tr("sel_title")))
        
        # On Linux with custom window controls, hide the system frame for a
        # seamless appearance. The app draws its own min/max/close buttons and
        # handles dragging via the header bar.
        is_linux_inline = (
            sys.platform.startswith("linux") or platform.system() == "Linux"
        ) and can_inline_title_with_window_controls()
        if is_linux_inline:
            flags = Qt.FramelessWindowHint | Qt.WindowCloseButtonHint
        else:
            flags = (
                self.windowFlags()
                | Qt.WindowCloseButtonHint
                | Qt.WindowMinimizeButtonHint
            )
        self.setWindowFlags(flags)
        
        # On Linux with frameless windows, install an event filter to handle
        # edge-based resizing (since there's no system frame to handle it).
        if is_linux_inline:
            self._install_frameless_resize_handler()
        
        self.resize(*default_window_size())
        self.setMinimumSize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)

        self.sm = StateMachine(self)
        self.service = FlashService(self)

        self._package_path = ""
        self._package_name = ""
        self._package_model = ""
        # Workers whose cancellation is still being observed, so Qt cannot
        # destroy a running QThread when the window goes away (see
        # _retire_worker).
        self._retired_workers = set()
        self._log_lines = []
        self._run_output = []
        self._run_output_open = False
        self._catalogue_section = "online"
        # Legacy "auto" values resolve to the platform default so users are
        # steered to SP Flash Tool's console-mode XML flow (MTKClient on macOS).
        self._flash_method = normalise_method(self.settings.value("flash_method", ""))
        self._last_progress = 0
        self._flash_start_ts = 0.0
        # Seconds left as measured by the running backend (EtaUnknown until it
        # has watched the write long enough to have a rate).
        self._worker_eta = ETA_UNKNOWN
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._step_now = ""
        self._update_offered_once = False
        self._update_manual_pending = False
        self._pre_install_guided = False
        self._usb_search_active = False
        self._past_usb_search = False
        self._install_ui_active = False
        self._download_active = False
        # The build a finished update check is offering, if any: the rail row
        # only exists while this is set (see _sync_update_button).
        self._pending_app_update = None
        self._install_failed = False
        self._failure_title = ""
        self._cache_recovery_needed = False
        self._legacy_migration_checked = False
        # True once this install has already fallen back to the other backend:
        # one automatic switch per run, so a bad cable cannot ping-pong.
        self._fallback_attempted = False
        self._sp_1013_seen = False
        self._reboot_suggestion_shown = False
        self._power_off_prompt = False
        self._rockbox_themes_flow_active = False
        self._gui_handoff_prepare = False
        self._gui_handoff_ready = False
        self._gui_launch_when_ready = False
        self._gui_after_prepare = ""
        self._gui_ready = None
        # True from the moment the desktop tool is asked to open until its
        # process is up (or the poll gives up). A second request in that window
        # would start a second SP Flash Tool fighting for the same device.
        self._gui_launch_in_flight = False
        self._linux_setup_flow_active = False
        self._diagnostics_session = False
        # A stalled connection makes the backend repeat the same errno line
        # many times over; this keeps the guidance dialog to once per attempt.
        self._active_workers = set()
        self._build_ui()
        self.setAcceptDrops(True)
        self._drop_overlay = _DropOverlay(self)
        self._drop_check_active = False
        self._connect_signals()
        self._nav_to_page(_PAGE_SELECT)
        from .surfaces import harmonise_window_plates
        harmonise_window_plates(self)

        self._manifest_worker = None
        is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or bool(os.environ.get("INNIOASIS_HEADLESS"))
        if not is_headless:
            QTimer.singleShot(250, self._start_manifest_worker)
            QTimer.singleShot(UPDATE_CHECK_STARTUP_DELAY_MS, self._start_auto_update_check)
            QTimer.singleShot(
                UPDATE_CHECK_STARTUP_DELAY_MS + 1000,
                lambda: self._check_device_firmware_updates(manual=False),
            )

        if platform.system() == "Linux" and not paths.IS_MAC:
            # macOS has no SP Flash Tool or Linux setup wizard.
            QTimer.singleShot(600, self._check_linux_first_run)
        self._sync_sp_gui_button_visibility()
        self._sync_settings_sp_gui_visibility()

        if not paths.IS_MAC:
            self._sp_history_timer = QTimer(self)
            self._sp_history_timer.setInterval(30000)
            self._sp_history_timer.timeout.connect(self._sync_sp_history)
            self._sp_history_timer.start()

    def _sync_sp_history(self):
        """Refresh history.ini when the scatter path changes. Once is enough."""
        try:
            from .. import sp_flash_gui
            if not sp_flash_gui.is_sp_flash_gui_supported():
                return
            model = getattr(self, "_package_model", "") or ""
            scatter, _extract, resolved_model = sp_flash_gui.resolve_cached_firmware(model=model)
            key = os.path.normcase(str(scatter)) if scatter else ""
            token = (key, model or resolved_model or "")
            if token == getattr(self, "_sp_history_sync_token", None):
                return
            if sp_flash_gui.update_sp_history_ini(model=model):
                self._sp_history_sync_token = token
        except Exception:
            pass

    def _on_gui_target_identity_changed(self) -> None:
        """Downstream reaction to the selection settling on a different package."""
        pass

    def _local_paths_from_mime(self, mime) -> list[Path]:
        paths = []
        if mime is None or not mime.hasUrls():
            return paths
        for url in mime.urls():
            local = url.toLocalFile() if isinstance(url, QUrl) else ""
            if local:
                paths.append(Path(local))
        return paths

    def _drag_enter_firmware(self, event) -> None:
        from ..drop_install import first_accepted_drop

        paths = self._local_paths_from_mime(event.mimeData())
        if first_accepted_drop(paths) is None:
            event.ignore()
            return
        event.acceptProposedAction()
        overlay = getattr(self, "_drop_overlay", None)
        if overlay is None:
            return
        overlay._label.setText(tr("drop_firmware_here"))
        overlay.setGeometry(self.rect())
        overlay.raise_()
        overlay.show()

    def _drop_firmware(self, event) -> None:
        from ..drop_install import first_accepted_drop

        overlay = getattr(self, "_drop_overlay", None)
        if overlay is not None:
            overlay.hide()
        path = first_accepted_drop(self._local_paths_from_mime(event.mimeData()))
        event.acceptProposedAction()
        if path is None or self._install_run_active():
            return
        self._start_drop_check(path)

    def dragEnterEvent(self, event):  # noqa: N802
        self._drag_enter_firmware(event)

    def dragMoveEvent(self, event):  # noqa: N802
        self._drag_enter_firmware(event)

    def dragLeaveEvent(self, event):  # noqa: N802
        overlay = getattr(self, "_drop_overlay", None)
        if overlay is not None and not overlay.underMouse():
            overlay.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self._drop_firmware(event)

    def _start_drop_check(self, path: Path) -> None:
        """Show a throbber, then install only if the scatter and its images exist.

        The drop is a local package, so it does not become an installed-version record.
        """
        self._drop_check_active = True
        self._package_path = str(path)
        self._package_name = path.name
        self._package_model = ""
        page = getattr(self, "_select_page", None)
        if page is not None:
            page._current_installed_release_info = None
            page._current_package_path = str(path)
            page._current_package_name = path.name
            page._current_package_model = ""
        self._flash_page.set_package_name(path.name)
        self._flash_page.set_model("")
        self._flash_page.show_drop_check(path.name)
        self._nav_to_page(_PAGE_FLASH)

        worker = _DropCheckThread(path, self)
        worker.finished.connect(lambda w=worker: self._on_drop_check_finished(w))
        self._drop_worker = worker
        self._active_workers.add(worker)
        worker.start()

    def _on_drop_check_finished(self, worker) -> None:
        self._active_workers.discard(worker)
        self._drop_check_active = False
        ok, flash_path, error = getattr(worker, "result", (False, "", "missing_scatter"))
        if not ok:
            key = "drop_missing_images" if error == "missing_images" else "drop_missing_scatter"
            self._flash_page.show_drop_error(tr(key))
            self._nav_to_page(_PAGE_FLASH)
            return
        page = getattr(self, "_select_page", None)
        if page is not None:
            page._current_installed_release_info = None
            page._current_package_path = flash_path
            page._current_package_name = Path(flash_path).name
            page._current_selected_rel = None
            page._details_package = None
            page._raw_release_pixmap = None
        self._on_package_selected(flash_path, Path(flash_path).name, "")
        if page is not None:
            page._remember_gui_local(flash_path, "", Path(flash_path).name)
        self._sync_sp_gui_button_visibility()
        self._sync_settings_sp_gui_visibility()

    def _start_manifest_worker(self):
        if getattr(self, "_manifest_worker", None) is not None:
            return
        worker = ManifestWorker(self)
        self._manifest_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(
            lambda entries: self._on_manifest_worker_finished(worker, entries)
        )
        worker.start()

    def _on_manifest_worker_finished(self, worker, entries):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_manifest_worker", None):
            self._on_manifest_loaded(entries)


    def _build_ui(self):
        central = QWidget(self)
        central.setObjectName("centralWidget")
        # Opaque client area. A translucent central widget keeps the previous
        # page's pixels, so the next screen is painted on top of the last one.
        paint_window_surface(central)
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
            self._window_controls = None
            central_layout.setContentsMargins(0, 0, 0, 0)

        # 2. Body row: Navigation sidebar on left, stacked pages on right
        body = QWidget()
        body.setObjectName("mainBody")
        paint_window_surface(body)
        outer = QHBoxLayout(body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_nav())

        self._stack = CurrentPageStack()
        paint_window_surface(self._stack)
        self._select_page = SelectPackagePage()
        QTimer.singleShot(0, self._prefill_fw_package)
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
            if sys.platform == "darwin":
                self._select_page._title.setStyleSheet(
                    "font-size: 13px; font-weight: 600; color: palette(window-text); letter-spacing: normal; background: transparent; border: none;"
                )
            self._title_container.layout().addWidget(self._select_page._title)
            from .surfaces import seal_updating_text
            seal_updating_text(self._select_page._title)
            self._align_page_title()
            self._arm_caption_drag()

        self._notice_host = QWidget()
        self._notice_host.setObjectName("noticeHost")
        notice_layout = QVBoxLayout(self._notice_host)
        notice_layout.setContentsMargins(16, 12, 16, 12)
        paint_window_surface(self._notice_host)
        self._support_page = SupportPage()
        for w in (
            self._select_page,
            self._flash_page,
            self._error_page,
            self._retry_page,
            self._settings_page,
            self._diag_placeholder,
            self._notice_host,
            self._support_page,
        ):
            paint_window_surface(w)
            self._stack.addWidget(w)
        content_col = QWidget()
        content_col.setObjectName("contentColumn")
        paint_window_surface(content_col)
        content_layout = QVBoxLayout(content_col)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._stack, 1)
        self._inline_prompt = self._build_inline_prompt()
        content_layout.addWidget(self._inline_prompt)
        outer.addWidget(content_col, 1)
        central_layout.addWidget(body, 1)

        self._restore_notes_language()
        self._settings_page.donation_visibility_changed.connect(
            self._apply_donation_visibility
        )
        self._settings_page.offline_mode_changed.connect(
            self._on_offline_mode_changed
        )
        self._settings_page.release_filters_changed.connect(
            self._select_page.refresh_release_filters
        )
        self._settings_page.advanced_settings_changed.connect(
            lambda _enabled: self._select_page.refresh_release_filters()
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
        from .scrollbars import apply_native_scrolling
        apply_native_scrolling(self)

    def _arm_caption_drag(self) -> None:
        """App icon, app name, and the page heading start a window move."""
        title = getattr(getattr(self, "_select_page", None), "_title", None)
        for widget in (
            getattr(self, "_icon_label", None),
            getattr(self, "_brand_label", None),
            getattr(self, "_brand_version", None),
            getattr(self, "_title_container", None),
            title,
        ):
            pass_caption_presses(widget)


    def _on_theme_changed(self):
        self.update()
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

    def _apply_caption_icon(self) -> None:
        """Draw the app icon in the caption the way this Mac draws app icons.

        On macOS the icon comes from IconServices, which applies the user's
        "Icon & widget style" (Dark, Clear, Tinted) and tint colour, so the
        caption matches the Dock, Finder and Spotlight art exactly — including
        the tinted style, which no bundled PNG can imitate. Anywhere else (or
        if the system draw fails) the bundled artwork is used, and the styled
        icon is re-fetched only when the style actually changes.
        """
        from . import icons

        label = getattr(self, "_icon_label", None)
        if label is None:
            return
        style = icons.macos_icon_style()
        if style != getattr(self, "_icon_style", None) or label.pixmap().isNull():
            pix = icons.macos_app_icon_pixmap(self._icon_size)
            self._icon_style = style
            self._caption_icon = pix
        pix = getattr(self, "_caption_icon", None)
        if pix is not None and not pix.isNull():
            label.setPixmap(pix)
            return
        icon_path = paths.RESOURCES_DIR / "icon.png"
        if icon_path.exists():
            label.setPixmap(
                QPixmap(str(icon_path)).scaled(
                    self._icon_size, self._icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            )

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
        self._icon_size = 28
        self._icon_style = None
        self._apply_caption_icon()
        self._icon_label.setStyleSheet("background: transparent; border: none;")
        brand_row.addWidget(self._icon_label, 0, Qt.AlignVCenter)

        brand_text_col = QVBoxLayout()
        brand_text_col.setContentsMargins(0, 0, 0, 0)
        brand_text_col.setSpacing(1)
        brand_text_col.setAlignment(Qt.AlignVCenter)

        title_version_row = QHBoxLayout()
        title_version_row.setContentsMargins(0, 0, 0, 0)
        title_version_row.setSpacing(5)

        is_mac = sys.platform == "darwin"
        brand_font_size = "13px" if is_mac else _BRAND_FONT_SIZE
        brand_font_weight = "600" if is_mac else "800"
        brand_letter_spacing = "normal" if is_mac else "-0.02em"

        self._brand_label = QLabel(get_app_name())
        self._brand_label.setStyleSheet(
            f"font-size: {brand_font_size}; font-weight: {brand_font_weight}; color: palette(window-text); letter-spacing: {brand_letter_spacing}; background: transparent; border: none;"
        )
        title_version_row.addWidget(self._brand_label)

        self._brand_version = QLabel(APP_VERSION)
        self._brand_version.setStyleSheet(
            f"font-size: {brand_font_size}; font-weight: {brand_font_weight}; color: palette(window-text); letter-spacing: {brand_letter_spacing}; background: transparent; border: none;"
        )
        title_version_row.addWidget(self._brand_version)
        title_version_row.addStretch(1)

        brand_text_col.addLayout(title_version_row)

        from .. import browser
        self._version_label = QLabel()
        self._version_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self._version_label.setOpenExternalLinks(False)
        self._version_label.linkActivated.connect(lambda url: browser.open_browser(url))
        self._version_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._version_label.setCursor(Qt.PointingHandCursor)
        self._update_author_label()
        brand_text_col.addWidget(self._version_label)

        brand_row.addLayout(brand_text_col, 1)
        return self._brand_container

    def _update_author_label(self):
        """Update author credit label text and styling according to current locale and theme."""
        if not hasattr(self, "_version_label") or self._version_label is None:
            return
        t = T()
        by_word = tr("author_by")
        author_color = t.fg or "#e5e7eb"
        author_link = f'<a href="https://ko-fi.com/teamslide" style="color: {author_color}; font-weight: 600; text-decoration: none;">Ryan Specter</a>'
        self._version_label.setText(
            tr("brand_author_by").format(by=by_word, author=author_link)
        )
        self._version_label.setStyleSheet(
            "font-size: 11px; color: palette(window-text); background: transparent; border: none; margin: 0; padding: 0;"
        )
        is_disabled = (
            device_tracking.is_donation_ui_disabled(self.settings)
            or device_tracking.is_donation_install_prompt_disabled(self.settings)
            or device_tracking.is_settings_donations_section_dismissed(self.settings)
        )
        self._version_label.setVisible(
            not is_disabled
            and not is_mediatek_installer()
        )

    def _build_header_bar(self):
        t = T()
        bar = DraggableHeaderBar(self)
        bar.setFixedHeight(int(HEADER_CONTENT_HEIGHT))
        paint_window_surface(bar)
        is_mac = sys.platform == "darwin"
        is_win = sys.platform == "win32" or platform.system() == "Windows"
        is_linux = sys.platform.startswith("linux") or platform.system() == "Linux"
        controls_left = are_window_controls_on_left()

        # The window is all client area, so the caption never draws the system
        # min/max/close buttons over it: DWM still reports their rectangle and
        # paints nothing there. On Windows and Linux the header draws them
        # itself, flush with the trailing edge, and reserves no margin for
        # buttons that would never appear. macOS keeps its native traffic lights.
        #
        # The maximise button is disabled everywhere: maximising wastes screen
        # real estate — the sidebar, dropdown, button and icon copy is laid out
        # for the compact window, not a full-screen plate. Show the button only
        # when the host forces its own title bar (system decorations on Linux
        # that we did not opt into, or traditional title mode), where hiding the
        # glyph would contradict the frame the user asked for; otherwise leave
        # only min + close so the user cannot throw the app full-screen.
        from .window_controls import CaptionControlButton
        from .window_controls import WindowControls

        self._window_controls = (
            WindowControls(self)
            if (is_win or is_linux) and not controls_left and can_inline_title_with_window_controls()
            else None
        )
        _max_allowed = not (is_mac or (is_linux and self._window_controls is not None))
        if self._window_controls is not None:
            CaptionControlButton.maximise_allowed = _max_allowed
            self._window_controls.set_maximise_allowed(_max_allowed)

        left_margin = (84 if is_mac else 70) if controls_left else 18
        right_margin = 24 if controls_left else (120 if is_win else (0 if self._window_controls is not None else 90))
        if self._window_controls is not None:
            right_margin = 0
        if interface_is_rtl() and self._window_controls is not None:
            # The mirrored row puts our own caption buttons on the left and the
            # app icon on the right, where the margin reserved for the buttons
            # was zero: the icon was pushed against (and past) the rounded
            # corner. The trailing edge gets the same inset the leading one had.
            left_margin, right_margin = right_margin, max(left_margin, 18)

        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(left_margin, 0, right_margin, 0)
        bar_layout.setSpacing(10)
        bar_layout.setAlignment(Qt.AlignVCenter)

        brand = self._build_brand_header_widget()

        # Title container for active page title
        self._title_container = QWidget()
        paint_window_surface(self._title_container)
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
            bar_layout.addWidget(self._title_container, 1)
        else:
            # Windows / right-handed DEs: Brand on left, page headings beside brand, controls margin on right
            bar_layout.addWidget(brand, 0, Qt.AlignVCenter)
            bar_layout.addSpacing(28)
            title_cont_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            bar_layout.addWidget(self._title_container, 1)
            if self._window_controls is not None:
                bar_layout.addWidget(self._window_controls, 0, Qt.AlignVCenter)

        self._arm_caption_drag()
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
        # Every row is 16px, and the glyphs are fitted so a theme's chunky
        # application icon and its thin symbolic marks read as one set.
        btn.setIcon(get_symbol_icon(icon_name, 16, fit_box=True))
        btn.setIconSize(QSize(16, 16))
        btn.setCheckable(checkable)
        btn.setMinimumHeight(max(btn.sizeHint().height(), sidebar_row_min_height()))
        return btn

    def _build_nav(self):
        t = T()
        nav = QWidget()
        nav.setObjectName("navPanel")
        # Sized from its labels by _reapply_rail_width; this is only the width it
        # opens at, before a language's copy has been measured.
        nav.setFixedWidth(self._rail_width.base)

        layout = QVBoxLayout(nav)
        layout.setContentsMargins(10, 8, 10, 10)
        # Aqua draws the button about 6px outside the layout cell on each
        # side, so macOS needs the wider gap. Windows and Linux paint inside
        # the row, the way a WinUI or Adwaita sidebar does.
        layout.setSpacing(sidebar_row_spacing())

        if not can_inline_title_with_window_controls():
            # On Linux with native titlebars, don't show bespoke header in sidebar
            if sys.platform.startswith("linux"):
                pass
            else:
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
        self._settings_btn.clicked.connect(self._show_full_settings)
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
            self._sp_flash_tool_btn.setVisible(False)
            layout.addWidget(self._sp_flash_tool_btn)
            self._aux_nav_buttons.append(self._sp_flash_tool_btn)

        # Same slot as SP Flash Tool GUI. Visible only while an install runs.
        # It is not the D-key diagnostics page.
        self._install_output_btn = self._make_nav_button(tr("nav_install_output"), "diagnostics")
        self._install_output_btn.clicked.connect(self._show_install_output)
        self._install_output_btn.setVisible(False)
        layout.addWidget(self._install_output_btn)
        self._aux_nav_buttons.append(self._install_output_btn)

        self._support_btn = self._make_nav_button(tr("nav_donate"), "support", checkable=True)
        self._support_btn.clicked.connect(self._on_support_clicked)
        layout.addWidget(self._support_btn)
        self._nav_btn_group.addButton(self._support_btn, _PAGE_SUPPORT)
        self._aux_nav_buttons.append(self._support_btn)

        self._log_btn = self._make_nav_button(tr("nav_log"), "diagnostics", checkable=True)
        self._log_btn.clicked.connect(self._show_diagnostics)
        self._log_btn.setVisible(False)
        layout.addWidget(self._log_btn)
        self._nav_btn_group.addButton(self._log_btn, _PAGE_DIAGNOSTICS)
        self._aux_nav_buttons.append(self._log_btn)

        # Only shown while a newer build is actually waiting: a permanent "App
        # Updates" row is a dead end for someone who is already up to date.
        self._check_updates_btn = self._make_nav_button(tr("nav_check_updates"), "update")
        self._check_updates_btn.clicked.connect(self._on_check_updates_clicked)
        self._check_updates_btn.setVisible(False)
        layout.addWidget(self._check_updates_btn)
        self._aux_nav_buttons.append(self._check_updates_btn)

        # Community and help: three marks, no label. They sit under the last
        # navigation row and above the language picker, which keeps the rail a
        # list of words with one quiet row of icons at the end. Each mark is its
        # own button with the wording in its tooltip and accessible name, and
        # the row mirrors with the interface direction.
        self._help_row = QWidget()
        self._help_row.setObjectName("helpRow")
        help_layout = QHBoxLayout(self._help_row)
        help_layout.setContentsMargins(8, 4, 8, 0)
        help_layout.setSpacing(2)
        self._help_icon_buttons = {}
        for name, key, tip_key in (
            ("discord", "_discord_btn", "help_discord_tip"),
            ("reddit", "_reddit_btn", "help_reddit_tip"),
            ("github", "_github_btn", "help_github_tip"),
        ):
            button = SidebarButton("")
            button.setIconSize(QSize(16, 16))
            button.setFixedSize(QSize(26, sidebar_row_min_height()))
            button.setCursor(Qt.ArrowCursor)
            button.setToolTip(tr(tip_key))
            button.setAccessibleName(tr(tip_key))
            button.clicked.connect(lambda _checked=False, n=name: self._open_community_link(n))
            help_layout.addWidget(button, 0, Qt.AlignVCenter)
            self._help_icon_buttons[name] = button
            setattr(self, key, button)
            self._aux_nav_buttons.append(button)
        help_layout.addStretch(1)
        layout.addWidget(self._help_row)
        self._aux_nav_buttons.append(self._help_row)

        self._lang_label = QLabel(tr("nav_language"))
        self._lang_label.setStyleSheet(
            "font-size: 11px; color: palette(window-text); margin-top: 6px; background: transparent; border: none;"
        )
        layout.addWidget(self._lang_label)
        self._lang_combo = QComboBox()
        from .dark import configure_native_combobox
        configure_native_combobox(self._lang_combo, max_visible_items=12)
        self._lang_combo.setObjectName("langCombo")
        self._populate_languages()
        idx = self._lang_combo.findData(translator().lang)
        self._lang_combo.setCurrentIndex(idx if idx >= 0 else 1)
        self._lang_combo.currentIndexChanged.connect(self._on_language_changed)
        layout.addWidget(self._lang_combo)

        self._nav_panel = nav
        self._nav_content = nav
        self._apply_nav_surface()
        self._refresh_icons()
        self._reapply_rail_width()
        return nav

    def _populate_languages(self):
        """Fill the picker: hand-translated languages, then every catalog.

        The text is the language's own name, with its flag in front where this
        desktop's emoji font has one (Windows' Segoe UI Emoji does not, and two
        letters there read worse than no flag). The code itself always rides in
        the item data, so a label is never the lookup key.
        """
        from ..i18n import curated_codes, flag, language_choices

        combo = self._lang_combo
        combo.clear()
        rows = {code: (code, native, english) for code, native, english in language_choices()}
        curated = list(curated_codes())
        # The hand-translated languages keep their own order first; everything
        # the catalogs added follows, in each language's own name.
        ordered = [rows[code] for code in curated if code in rows]
        ordered += [
            rows[code] for code in sorted(rows, key=lambda c: rows[c][1].casefold())
            if code not in set(curated)
        ]
        for code, native, english in ordered:
            glyph = flag(code)
            label = f"{glyph} {native}" if glyph else native
            combo.addItem(label, code)
            index = combo.count() - 1
            tip = english if english and english != native else native
            combo.setItemData(index, f"{tip} ({code})", Qt.ToolTipRole)
            combo.setItemData(index, bool(glyph), Qt.UserRole + 1)
        combo.setToolTip(tr("nav_language"))

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
        # window's material shows; the buttons repaint their own rows.
        # Elsewhere the rail is a solid pane so a moved highlight cannot linger.
        # The rail carries no material of its own: the whole window is one glass
        # surface, so a second effect view here would read as a darker panel.
        paint_window_surface(nav)
        nav.update()
        for btn in nav.findChildren(SidebarButton):
            # The rows belong over the rail's own plate. Raising them is what
            # keeps them visible after a window state change re-stacks the
            # window's native view.
            btn.raise_()
            btn.update()

    def _refresh_icons(self):
        """Update navigation and action button icons to match theme tokens and accent."""
        if hasattr(self, "_nav_buttons"):
            for key, (btn, _) in self._nav_buttons.items():
                if key == "nav_select_package":
                    btn.setIcon(get_symbol_icon("install", 16, fit_box=True))
                elif key == "nav_settings":
                    btn.setIcon(get_symbol_icon("settings", 16, fit_box=True))
        if hasattr(self, "_support_btn"):
            self._support_btn.setIcon(get_symbol_icon("support", 16, fit_box=True))
        for attr, name in (
            ("_discord_btn", "discord"),
            ("_reddit_btn", "reddit"),
            ("_github_btn", "github"),
        ):
            button = getattr(self, attr, None)
            if button is not None:
                button.setIcon(self._community_icon(name))
                button.setIconSize(QSize(16, 16))
        if hasattr(self, "_log_btn"):
            self._log_btn.setIcon(get_symbol_icon("diagnostics", 16, fit_box=True))
        if hasattr(self, "_check_updates_btn"):
            self._check_updates_btn.setIcon(get_symbol_icon("update", 16, fit_box=True))
        if hasattr(self, "_linux_setup_btn"):
            self._linux_setup_btn.setIcon(get_symbol_icon("tools", 16, fit_box=True))
        if hasattr(self, "_sp_flash_tool_btn"):
            self._sp_flash_tool_btn.setIcon(get_symbol_icon("tools", 16, fit_box=True))
        if hasattr(self, "_install_output_btn"):
            self._install_output_btn.setIcon(get_symbol_icon("diagnostics", 16, fit_box=True))
        if hasattr(self, "_lang_combo"):
            from PySide6.QtGui import QIcon

            # Same live foreground as the rest of the sidebar; the provider's
            # default is plain white in dark mode.
            lang_icon = get_symbol_icon("translate", 14, color=T().fg)
            for i in range(self._lang_combo.count()):
                # A row that carries its language's flag needs no second mark;
                # the ones without a flag keep the translate glyph.
                if self._lang_combo.itemData(i, Qt.UserRole + 1):
                    self._lang_combo.setItemIcon(i, QIcon())
                    continue
                self._lang_combo.setItemIcon(i, lang_icon)

    def _reapply_rail_width(self) -> None:
        """Labels, theme or display may have changed: re-apply the rail width.

        The rail's width, its caps, the affordability rule and the report of an
        over-long label all live in ``SidebarWidth``. This feeds it the window's
        facts, and when the rail actually changed raises the window floor to
        match — nothing here measures a label or decides a ceiling.
        """
        nav = getattr(self, "_nav_panel", None)
        if nav is None:
            return
        changed = self._rail_width.apply(
            nav,
            getattr(self, "_lang_combo", None),
            display_width=self._display_width(),
            content_min=self._content_column_minimum(),
            language=getattr(translator(), "lang", ""),
            picker_tooltip=tr("nav_language"),
        )
        if not changed:
            return
        # A wider rail raises the window's own floor: dragging below it would
        # crush the page column under its own cards.
        if hasattr(self, "_select_page"):
            self.setMinimumWidth(self._minimum_window_width())
        if hasattr(self, "_stack") and not self._install_run_active():
            self._fit_window_to_page()

    def _display_width(self) -> int:
        """Width the window may grow to: the display it is on."""
        screen = self.screen()
        if screen is None:
            return 0
        return int(screen.availableGeometry().width())

    def _content_column_minimum(self) -> int:
        """Narrowest the page column can be drawn: the catalogue's floor."""
        page = getattr(self, "_select_page", None)
        if page is None:
            return 0
        try:
            return int(page.minimumSizeHint().width())
        except Exception:
            return 0

    def _minimum_window_width(self) -> int:
        """Least width that keeps the rail and the page column whole."""
        return self._rail_width.window_floor(
            self._content_column_minimum(),
            self._display_width(),
            MINIMUM_WINDOW_WIDTH,
        )

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
            getattr(self, "_support_page", None),
        ):
            if surface is not None:
                paint_window_surface(surface)

        is_mac = sys.platform == "darwin"
        brand_font_size = "13px" if is_mac else _BRAND_FONT_SIZE
        brand_font_weight = "600" if is_mac else "800"
        brand_letter_spacing = "normal" if is_mac else "-0.02em"

        if hasattr(self, "_brand_label"):
            self._brand_label.setStyleSheet(
                f"font-size: {brand_font_size}; font-weight: {brand_font_weight}; color: palette(window-text); letter-spacing: {brand_letter_spacing}; background: transparent; border: none;"
            )

        if hasattr(self, "_icon_label"):
            # The icon style is a system setting of its own (Appearance → Icon &
            # widget style): follow it here as well as the light/dark switch.
            self._apply_caption_icon()

        if hasattr(self, "_brand_version"):
            self._brand_version.setText(APP_VERSION)
            self._brand_version.setStyleSheet(
                f"font-size: {brand_font_size}; font-weight: {brand_font_weight}; color: palette(window-text); letter-spacing: {brand_letter_spacing}; background: transparent; border: none;"
            )

        self._update_author_label()
        self._update_header_title()

        if hasattr(self, "_lang_label"):
            self._lang_label.setStyleSheet(
                "font-size: 11px; color: palette(window-text); margin-top: 6px; background: transparent; border: none;"
            )

        sb = self.statusBar()
        if sb and hasattr(sb, "refresh_theme"):
            sb.refresh_theme()

        self._refresh_icons()
        from .dark import configure_native_combobox
        for combo in self.findChildren(QComboBox):
            configure_native_combobox(combo)

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

        # The caption controls are painted from the live palette, and on Linux
        # the symbols themselves come from the desktop's icon theme, so a theme
        # switch has to re-read and repaint them: their hover wash and glyph
        # colour follow a light or dark surface and would otherwise keep the
        # old theme's.
        controls = getattr(self, "_window_controls", None)
        if controls is not None and hasattr(controls, "refresh_theme"):
            controls.refresh_theme()
        # A theme switch re-derives the palette, so the plates have to follow it.
        from .surfaces import harmonise_window_plates
        harmonise_window_plates(self)
        # The theme's font is what the rows are drawn in: re-measure the rail
        # from the copy at that size.
        self._reapply_rail_width()


    def _install_frameless_resize_handler(self):
        """Install an event filter to handle edge-based resizing for frameless windows on Linux.
        
        Frameless windows don't have a system frame to handle resizing, so we need to
        implement it ourselves by detecting mouse position near window edges and
        adjusting the cursor accordingly.
        """
        self._resize_edge_size = 8  # pixels from edge that triggers resize
        self._resize_edge = None  # current resize edge being dragged
        self._resize_start_pos = None
        self._resize_start_size = None
        
        # Install event filter on the window to handle mouse events for resizing
        self.installEventFilter(self)
    
    def eventFilter(self, obj, event):
        """Handle mouse events for edge-based resizing on frameless windows."""
        if not (sys.platform.startswith("linux") or platform.system() == "Linux"):
            return super().eventFilter(obj, event)
        
        # Only handle events for our own window
        if obj is not self:
            return super().eventFilter(obj, event)
        
        # Check if we have custom window controls (frameless mode)
        if not (hasattr(self, "_window_controls") and self._window_controls is not None):
            return super().eventFilter(obj, event)
        
        if event.type() == QEvent.Type.MouseMove:
            self._handle_frameless_mouse_move(event)
            return True
        elif event.type() == QEvent.Type.MouseButtonPress:
            self._handle_frameless_mouse_press(event)
            return True
        elif event.type() == QEvent.Type.MouseButtonRelease:
            self._handle_frameless_mouse_release(event)
            return True
        
        return super().eventFilter(obj, event)
    
    def _get_frameless_resize_edge(self, pos: QPoint) -> int | None:
        """Determine which edge/corner the mouse is near for resizing.
        
        Returns a bitmask of resize directions or None if not near an edge.
        Uses simple integer flags for compatibility.
        """
        rect = self.rect()
        edge_size = self._resize_edge_size
        x, y = pos.x(), pos.y()
        
        near_left = x < edge_size
        near_right = x >= rect.width() - edge_size
        near_top = y < edge_size
        near_bottom = y >= rect.height() - edge_size
        
        # Skip resize edges in the header area (top 44px) to allow dragging
        if y < int(HEADER_CONTENT_HEIGHT):
            near_top = False
        
        # Use bitmask flags for resize edges
        edge = 0
        if near_left:
            edge |= 1  # Left
        if near_right:
            edge |= 2  # Right
        if near_top:
            edge |= 4  # Top
        if near_bottom:
            edge |= 8  # Bottom
        
        return edge if edge else None
    
    def _is_maximized(self) -> bool:
        """Check if the window is currently maximized."""
        try:
            return self.isMaximized()
        except Exception:
            return False
    
    def _handle_frameless_mouse_move(self, event):
        """Handle mouse move for edge-based resizing."""
        pos = event.position().toPoint() if hasattr(event, "position") else event.globalPos()
        
        # Don't resize when maximized
        if self._is_maximized():
            self.unsetCursor()
            return
        
        if self._resize_edge is not None and event.buttons() & Qt.LeftButton:
            # Resizing
            self._resize_from_edge(pos)
            return
        
        # Update cursor based on position
        edge = self._get_frameless_resize_edge(pos)
        if edge is not None:
            self._set_resize_cursor(edge)
        else:
            self.unsetCursor()
    
    def _handle_frameless_mouse_press(self, event):
        """Handle mouse press for starting edge-based resizing."""
        if event.button() != Qt.LeftButton:
            return
        
        # Don't resize when maximized
        if self._is_maximized():
            return
        
        pos = event.position().toPoint() if hasattr(event, "position") else event.globalPos()
        
        edge = self._get_frameless_resize_edge(pos)
        if edge is not None:
            self._resize_edge = edge
            self._resize_start_pos = pos
            self._resize_start_size = self.size()
            self.setCursor(self._get_resize_cursor_for_edge(edge))
            event.accept()
    
    def _handle_frameless_mouse_release(self, event):
        """Handle mouse release for ending edge-based resizing."""
        if self._resize_edge is not None:
            self._resize_edge = None
            self._resize_start_pos = None
            self._resize_start_size = None
            self.unsetCursor()
            event.accept()
    
    def _resize_from_edge(self, current_pos: QPoint):
        """Perform the actual resize based on the current edge being dragged."""
        if self._resize_edge is None or self._resize_start_size is None:
            return
        
        dx = current_pos.x() - self._resize_start_pos.x()
        dy = current_pos.y() - self._resize_start_pos.y()
        
        new_width = self._resize_start_size.width()
        new_height = self._resize_start_size.height()
        
        edge = self._resize_edge
        
        # Calculate new dimensions based on which edge(s) are being dragged
        # edge bitmask: 1=Left, 2=Right, 4=Top, 8=Bottom
        if edge & 1:  # Left
            new_width = max(self.minimumWidth(), self._resize_start_size.width() - dx)
        if edge & 2:  # Right
            new_width = max(self.minimumWidth(), self._resize_start_size.width() + dx)
        if edge & 4:  # Top
            new_height = max(self.minimumHeight(), self._resize_start_size.height() - dy)
        if edge & 8:  # Bottom
            new_height = max(self.minimumHeight(), self._resize_start_size.height() + dy)
        
        # Also adjust position for top/left edges
        if edge & 1:  # Left
            new_x = self.x() + dx
            if new_x < 0:
                new_x = 0
            self.move(new_x, self.y())
        if edge & 4:  # Top
            new_y = self.y() + dy
            if new_y < 0:
                new_y = 0
            self.move(self.x(), new_y)
        
        self.resize(new_width, new_height)
    
    def _set_resize_cursor(self, edge: int):
        """Set the appropriate cursor for the given resize edge."""
        cursor = self._get_resize_cursor_for_edge(edge)
        self.setCursor(cursor)
    
    def _get_resize_cursor_for_edge(self, edge: int) -> Qt.CursorShape:
        """Get the cursor shape for a given resize edge.
        
        edge bitmask: 1=Left, 2=Right, 4=Top, 8=Bottom
        """
        # Check for corners first (combinations)
        if edge == (1 | 4):  # Top-Left
            return Qt.CursorShape.SizeFDiagCursor
        elif edge == (2 | 4):  # Top-Right
            return Qt.CursorShape.SizeBDiagCursor
        elif edge == (1 | 8):  # Bottom-Left
            return Qt.CursorShape.SizeBDiagCursor
        elif edge == (2 | 8):  # Bottom-Right
            return Qt.CursorShape.SizeFDiagCursor
        elif edge == 1 or edge == 2:  # Left or Right
            return Qt.CursorShape.SizeHorCursor
        elif edge == 4 or edge == 8:  # Top or Bottom
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor
    
    def _connect_signals(self):
        self.sweep_report.connect(self._on_sweep_report)
        self.usb_recovery_report.connect(self._on_usb_recovery_report)
        self._select_page.package_selected.connect(self._on_package_selected)
        if hasattr(self._settings_page, "content_size_changed"):
            self._settings_page.content_size_changed.connect(self._on_settings_content_changed)
        self._select_page.gui_release_clicked.connect(self._on_gui_release_clicked)
        if hasattr(self._select_page, "gui_firmware_changed"):
            self._select_page.gui_firmware_changed.connect(self._sync_sp_gui_button_visibility)
            self._select_page.gui_firmware_changed.connect(self._sync_settings_sp_gui_visibility)
        self._settings_page.set_sp_gui_available(bool(self._select_page.gui_firmware_target()))
        self._select_page.release_icon_ready.connect(self._on_release_icon_ready)
        self._select_page.gui_handoff_abandoned.connect(self._on_gui_handoff_abandoned)
        self._select_page.content_size_changed.connect(self._on_select_content_changed)
        self._support_page.content_size_changed.connect(self._on_support_content_changed)
        self._select_page.app_update_clicked.connect(self._on_app_update_toast_action)
        self._flash_page.on_install_retry(self._on_progress_retry)
        self._select_page.download_started.connect(self._on_download_started)
        self._select_page.download_progress.connect(self._on_download_progress)
        self._select_page.download_finished.connect(self._on_download_finished)
        self._select_page.download_cancelled.connect(self._on_download_cancelled)
        self._select_page.prep_progress.connect(self._flash_page.update_prep_progress)
        self._select_page.prep_started.connect(self._on_prep_started)
        self._select_page.prep_finished.connect(self._on_prep_finished)
        self._flash_page.set_cancel_download_callback(self._on_cancel_download)
        self._settings_page.flash_method_changed.connect(self._on_method_changed)
        self._settings_page.card_focus_finished.connect(self._on_card_focus_finished)
        self._settings_page.linux_prep_completed.connect(self._on_linux_prep_completed)
        # The app's own no-progress countdown, shown on the install card.
        self.service.attempt_timeout.connect(self._flash_page.set_attempt_timeout)
        # Time remaining, measured from the install's own write rate (the
        # worker knows the byte totals; the card only knows the clock).
        self.service.eta_changed.connect(self._on_worker_eta)
        self._flash_page.on_cancel(self._on_cancel_flash)
        self._flash_page.on_cancel_wait(self._on_cancel_wait)
        self._flash_page.on_install_retry(self._on_progress_retry)
        self._flash_page.on_power_off_continue(self._on_power_off_continue)
        self._flash_page.on_install_done(self._on_install_done)
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
        if hasattr(self.service, "usb_disconnected"):
            self.service.usb_disconnected.connect(self._on_device_lost)
        self.service.monitor_error.connect(self._on_monitor_error)

    def _on_download_started(self, name, model):
        self._cache_recovery_needed = False
        self._install_complete = False
        self._install_failed = False
        self._remember_catalogue_section()
        self._begin_run_output()
        self._download_active = True
        self._package_name = name
        self._package_model = model or ""
        self._flash_page.set_package_name(name)
        self._flash_page.set_model(model or "")
        self._flash_page.show_downloading()
        self._apply_release_icon(complete=False)
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_FLASH)
        self._release_status_to_donors()

    def _on_prep_finished(self, ok):
        """A local archive finished extracting: return to Select so Start is reachable."""
        self._prep_active = False
        if getattr(self, "_download_active", False) or self._install_run_active():
            return
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_SELECT)

    def _on_prep_started(self, name, model):
        """Zip or archive extraction belongs on the install card, not the catalogue."""
        self._download_active = False
        self._prep_active = True
        if name:
            self._package_name = name
        if model:
            self._package_model = model
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model or "")
        self._worker_eta = ETA_UNKNOWN
        self._flash_page.set_prep_eta_seconds(0)
        self._flash_page.show_preparing()
        self._apply_release_icon(complete=False)
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_FLASH)
        self._release_status_to_donors()

    def _on_download_progress(self, percent, status_text):
        if getattr(self, "_download_active", False):
            self._flash_page.update_download_progress(percent, status_text)

    def _on_download_finished(self, ok, result):
        if getattr(self, "_gui_handoff_prepare", False):
            if ok:
                # The archive is on disk; extraction starts next. The prep
                # view and its title come from the extractor (prep_started).
                self._download_active = False
                if hasattr(self, "_select_page") and hasattr(self._select_page, "_title"):
                    self._select_page._title.setText(tr("flash_install_in_progress"))
            return
        if not ok:
            self._download_active = False
            self._prep_active = False
            self._sync_install_nav_entry()
            self._nav_to_page(_PAGE_SELECT)
            self._show_status(f"{tr('sel_download_failed')} \u2014 {result}", 15000)
        else:
            self._download_active = False
            self._prep_active = True
            self._sync_install_nav_entry()
            self._flash_page.show_preparing()
            if hasattr(self, "_select_page") and hasattr(self._select_page, "_title"):
                self._select_page._title.setText(tr("flash_install_in_progress"))

    def _on_cancel_download(self):
        self._return_to_select_software()

    def _on_download_cancelled(self):
        if getattr(self, "_returning_to_select", False):
            self._download_active = False
            return
        self._return_to_select_software()

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
            active = self._install_run_active()
            if active:
                set_window_close_button_enabled(self, False)
            controls = getattr(self, "_window_controls", None)
            if controls is not None:
                # Our own control mirrors the system caption button's state.
                controls.set_close_enabled(not active)
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
            bar = getattr(self, "_title_bar", None)
            local = None
            if bar is not None and bar.isVisible():
                local = bar.mapFromGlobal(event.globalPosition().toPoint())
            in_bar = local is not None and bar.rect().contains(local)
            if in_bar and not caption_drag_blocked(bar.childAt(local)) and caption_button_hit(
                local.x(), local.y(), getattr(self, "_caption_button_bounds", None),
            ) is None:
                if begin_unified_caption_drag(self):
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
        if event.type() == QEvent.ApplicationPaletteChange:
            # A theme refresh re-derives the app palette; the page plates must
            # follow it, or they keep the native grey it leaves behind.
            from .surfaces import harmonise_window_plates
            harmonise_window_plates(self)
        if event.type() in (QEvent.WindowStateChange, QEvent.ActivationChange):
            self._verify_native_chrome()
            self._schedule_caption_button_refresh()
            if event.type() == QEvent.WindowStateChange:
                controls = getattr(self, "_window_controls", None)
                if controls is not None:
                    controls.set_state(self.isMaximized())
                self._schedule_window_state_resync()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, "_shown_once", False):
            self._shown_once = True
            if not self._install_run_active() and not self.isMaximized() and not self.isFullScreen():
                self.resize(*default_window_size())
            # The starting size is the compact one; the page on screen may need
            # more, so it gets the chance to take it once the layout is live.
            # The floor is content-derived: two release rows plus the
            # Install / Restore button and the Install-from-file link must fit
            # at every window size.
            # Measure the rail now that the pages exist: its affordable width
            # depends on the page column's own minimum, which is only known
            # once the catalogue has been built. Then the rail's width is part
            # of the window floor, so a language switch raises that floor.
            self._reapply_rail_width()
            self.setMinimumSize(self._minimum_window_width(), self._content_floor_height())
            QTimer.singleShot(0, self._fit_window_to_page)
            if not os.environ.get("QT_QPA_PLATFORM") == "offscreen" and not os.environ.get("INNIOASIS_HEADLESS"):
                QTimer.singleShot(1200, self._check_legacy_installations)
            self._ensure_theme_sync()
        if getattr(self, "_pending_select_fit", False):
            self._pending_select_fit = False
            QTimer.singleShot(0, self._on_select_content_changed)
        if sys.platform == "darwin":
            from .glass import apply_glass, configure_traffic_lights

            # One liquid-glass surface for the whole window: the rail, the pages
            # and their cards are translucent plates over it. Deferred one turn
            # because the window's native view only exists once show() returns;
            # applying it here found no host and left the window unblurred.
            def _apply_native_chrome():
                apply_glass(self)
                configure_traffic_lights(self, x_offset=18)

            QTimer.singleShot(0, _apply_native_chrome)
        if event.type() == QEvent.Type.Show and not getattr(self, "_chrome_watchdog", None):
            # Started once: a reset can happen at any point in a session, and the
            # check is a property read rather than a repaint.
            self._chrome_watchdog = QTimer(self)
            self._chrome_watchdog.setInterval(self.CHROME_WATCHDOG_MS)
            self._chrome_watchdog.timeout.connect(self._verify_native_chrome)
            self._chrome_watchdog.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # The inline header is a manually placed child of the window on macOS,
        # so it must follow every width change *including* an animated resize.
        # Skipping it mid-animation left the right-anchored page heading at the
        # width the animation started from — the heading visibly slid left or
        # right instead of holding its place against the window edge.
        bar = getattr(self, "_title_bar", None)
        if sys.platform == "darwin" and bar is not None:
            bar.setGeometry(0, 0, self.width(), int(HEADER_CONTENT_HEIGHT))
            bar.raise_()
        if getattr(self, "_size_animating", False):
            return
        self._verify_native_chrome()
        self._schedule_caption_button_refresh()
        overlay = getattr(self, "_drop_overlay", None)
        if overlay is not None and overlay.isVisible():
            overlay.setGeometry(self.rect())

    def _schedule_window_state_resync(self) -> None:
        """Re-assemble the window after a full-screen round trip.

        macOS re-lays out a full-screen window natively: the app's header is a
        manually placed child, the sidebar rows paint their own plate, and the
        seamless chrome is re-applied outside the transition. One pass once the
        animation has settled is what keeps the rows and the header in place.
        """
        if getattr(self, "_state_resync_pending", False):
            return
        self._state_resync_pending = True
        QTimer.singleShot(280, self._resync_window_state)

    def _resync_window_state(self) -> None:
        self._state_resync_pending = False
        try:
            if not self.isFullScreen() and not self.isMinimized():
                self._reassert_mac_glass()
            bar = getattr(self, "_title_bar", None)
            if sys.platform == "darwin" and bar is not None:
                bar.setGeometry(0, 0, self.width(), int(HEADER_CONTENT_HEIGHT))
                bar.show()
                bar.raise_()
            self._apply_nav_surface()
            nav = getattr(self, "_nav_panel", None)
            if nav is not None:
                nav.update()
                for btn in nav.findChildren(SidebarButton):
                    btn.update()
            controls = getattr(self, "_window_controls", None)
            if controls is not None:
                controls.set_state(self.isMaximized())
            central = self.centralWidget()
            layout = central.layout() if central is not None else None
            if layout is not None:
                layout.activate()
            self.update()
        except Exception:
            logger.debug("Window state resync failed", exc_info=True)

    def _schedule_caption_button_refresh(self) -> None:
        """Re-run the frame change a manual resize ends with.

        Qt's resize() calls SetWindowPos without SWP_FRAMECHANGED, so DWM keeps
        the previous caption-button rectangle. After a compact install height
        or a settings grow, those buttons sit past the new edge until the user
        drags a border. A deferred frame change is that same update.
        """
        if sys.platform != "win32" or getattr(self, "_caption_refreshing", False):
            return
        if getattr(self, "_caption_refresh_pending", False):
            return
        self._caption_refresh_pending = True
        QTimer.singleShot(0, self._refresh_caption_buttons)

    def _refresh_caption_buttons(self) -> None:
        self._caption_refresh_pending = False
        if sys.platform != "win32" or getattr(self, "_caption_refreshing", False):
            return
        # Qt reconfigures the window behind our back (a page switch is enough)
        # and that can drop its native maximize style again. This pass already
        # applies a frame change, so re-assert it here.
        ensure_window_maximize_enabled(self)
        if getattr(self, "_window_controls", None) is not None:
            # The header draws the controls at its trailing edge: there is no
            # native caption-button rectangle to re-measure or reserve room for.
            self._caption_button_bounds = None
            return
        self._caption_refreshing = True
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)
            user32.RedrawWindow(hwnd, None, None, 0x0001 | 0x0100 | 0x0400)

            class _RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            rect = _RECT()
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, 5, ctypes.byref(rect), ctypes.sizeof(rect),
            )
            bounds = None
            if hr == 0 and rect.right > rect.left:
                left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
                if left > self.width() + 8:
                    origin = self.frameGeometry().topLeft()
                    left -= origin.x()
                    right -= origin.x()
                    top -= origin.y()
                    bottom -= origin.y()
                bounds = (left, top, right, bottom)
            self._caption_button_bounds = bounds
            bar = getattr(self, "_title_bar", None)
            if bar is not None and bar.layout() is not None and bounds:
                lay = bar.layout()
                margins = lay.contentsMargins()
                reserve = title_content_right_margin(self.width(), bounds)
                if margins.right() != reserve:
                    lay.setContentsMargins(margins.left(), margins.top(), reserve, margins.bottom())
        except Exception:
            logging.getLogger(__name__).debug("caption button refresh skipped", exc_info=True)
        finally:
            self._caption_refreshing = False

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
            if msg.message == 0x001A:  # WM_SETTINGCHANGE
                setting = ""
                if msg.lParam:
                    try:
                        setting = ctypes.cast(msg.lParam, ctypes.c_wchar_p).value or ""
                    except Exception:
                        setting = ""
                if "ImmersiveColor" in setting or "WindowsThemeElement" in setting:
                    watcher = getattr(self, "_theme_watcher", None)
                    if watcher is not None:
                        watcher.schedule()
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
                from PySide6.QtGui import QCursor
                local_win = self.mapFromGlobal(QCursor.pos())
                button_hit = caption_button_hit(
                    local_win.x(), local_win.y(), getattr(self, "_caption_button_bounds", None),
                )
                if button_hit is not None:
                    return True, button_hit
                bar = getattr(self, "_title_bar", None)
                if bar is not None and bar.isVisible():
                    from PySide6.QtGui import QCursor
                    local = bar.mapFromGlobal(QCursor.pos())
                    if bar.rect().contains(local):
                        if not caption_drag_blocked(bar.childAt(local)):
                            return True, 2
        except Exception:
            logging.getLogger(__name__).debug("unified caption event skipped", exc_info=True)
        return super().nativeEvent(eventType, message)

    def keyPressEvent(self, event):
        # M reveals advanced install settings; D toggles those settings and
        # diagnostics. R restores the default method and hides both reveals.
        # NB: PySide6 6.11 returns a Flag here, so int(modifiers()) raises.
        if event.key() == Qt.Key_D and event.modifiers() == Qt.NoModifier:
            self.toggle_diagnostics_and_advanced()
            event.accept()
            return
        if event.key() == Qt.Key_M and event.modifiers() == Qt.NoModifier:
            self._settings_page.reveal_advanced_methods()
            if self._settings_page.advanced_methods_revealed():
                try:
                    from ..device_tracking import set_hidden_mtk_options

                    set_hidden_mtk_options(True)
                except Exception:
                    pass
                self._nav_to_page(_PAGE_SETTINGS)
                self._append_log("Advanced install methods revealed (M).")
            event.accept()
            return
        if event.key() == Qt.Key_R and event.modifiers() == Qt.NoModifier:
            self.reset_all_configuration_and_caches()
            event.accept()
            return
        super().keyPressEvent(event)

    def reset_all_configuration_and_caches(self):
        """Completely reset app config back to default, opt credits/donations back in, and clear caches."""
        from .. import device_tracking, downloads, config
        from ..i18n import translator

        # 1. Clear QSettings across all namespaces
        s = QSettings("innioasis", "updater")
        s.clear()
        s.remove("battery_hint_dismissed")
        s_caps = QSettings("Innioasis", "UpdaterCE")
        s_caps.clear()
        s_caps.remove("battery_hint_dismissed")
        s_ref = QSettings("Innioasis", "Innioasis Updater")
        s_ref.clear()
        s_ref.remove("battery_hint_dismissed")

        # 2. Reset device_tracking and ensure credits/donations are opted in
        device_tracking.set_donation_ui_disabled(False, s)
        device_tracking.set_donation_install_prompt_disabled(False, s)
        device_tracking.set_settings_donations_section_dismissed(False, s)
        device_tracking.set_donation_ui_disabled(False, s_caps)
        device_tracking.set_donation_install_prompt_disabled(False, s_caps)
        device_tracking.set_settings_donations_section_dismissed(False, s_caps)

        device_tracking.set_hidden_mtk_options(False, s)
        device_tracking.set_terminal_install_enabled(False, s)
        device_tracking.set_sp_gui_install_enabled(False, s)
        device_tracking.set_sp_auth_file("", s)
        for name in getattr(device_tracking, "FILTER_NAMES", ()):
            device_tracking.set_rockbox_release_filter(name, False, s)

        # 3. Clear firmware downloads cache and completely wipe downloads folder
        try:
            downloads.clear_firmware_cache()
            d = downloads.downloads_dir()
            if d.is_dir():
                import shutil
                for item in list(d.iterdir()):
                    try:
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                    except Exception as e:
                        logger.debug("Failed removing %s during reset: %s", item, e)
        except Exception as e:
            logger.debug("clear_firmware_cache error during reset: %s", e)

        # 4. Clear application cache directory
        try:
            from ..catalog import _app_cache_dir
            app_cache = _app_cache_dir()
            if app_cache.exists():
                import shutil
                for item in app_cache.iterdir():
                    try:
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("app_cache_dir clearing error during reset: %s", e)

        # 5. Clear manifest cache directory
        try:
            from ..manifest import _cache_root
            manifest_cache = _cache_root()
            if manifest_cache.exists():
                import shutil
                for item in manifest_cache.iterdir():
                    try:
                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item, ignore_errors=True)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug("manifest cache clearing error during reset: %s", e)

        # 6. Reset in-memory reveal and method state as well as its settings.
        # D unlocks diagnostics only for this session; R must close it if it is
        # open, and clear both D and M's persisted advanced-options flag.
        self._lock_diagnostics()
        from ..flash_service import default_flash_method
        self._flash_method = default_flash_method()
        if hasattr(self, "_settings_page"):
            self._settings_page.hide_advanced_methods()
            self._settings_page.refresh_settings()
        if hasattr(self, "_flash_page"):
            self._flash_page.set_method(self._flash_method)

        self._apply_donation_visibility(False)
        if hasattr(self, "_select_page"):
            if hasattr(self._select_page, "reset_selection_and_filters"):
                self._select_page.reset_selection_and_filters()
            elif hasattr(self._select_page, "_check_battery_toast_visibility"):
                self._select_page._check_battery_toast_visibility()

        self._nav_to_page(_PAGE_SELECT)
        self.statusBar().showMessage(tr("status_app_reset_defaults"), 4000)
        self._append_log(tr("log_app_reset_defaults"))

    def _rockbox_themes_required(self) -> tuple[bool, str]:
        """Whether the selected Y1/Y2 Rockbox build needs bundled 360p assets.

        Y2 catalogue assets use ``rom_y2.zip`` and commonly have no ``360p``
        suffix; they are the native 360p Rockbox build. Explicit 240p assets
        must remain excluded for either model.
        """
        model = (getattr(self, "_package_model", "") or "").upper()
        name = (getattr(self, "_package_name", "") or "").lower()
        path = (getattr(self, "_package_path", "") or "").lower()
        page = getattr(self, "_select_page", None)
        info = getattr(page, "current_installed_release_info", lambda: None)() or {}
        slug = str(info.get("package_slug", "") or "").lower()
        asset_name = str(info.get("asset_name", "") or "").lower()
        release_label = str(info.get("release_label", "") or "").lower()
        selected_rel = getattr(page, "_current_selected_rel", None) if page else None
        if selected_rel:
            asset_name = str(selected_rel.get("asset_name", asset_name) or asset_name).lower()
            release_label = str(selected_rel.get("release_label", release_label) or release_label).lower()
        if "Y1" in model or "y1" in name or "y1" in path or "y1" in slug:
            model = "Y1"
        elif "Y2" in model or "y2" in name or "y2" in path or "y2" in slug:
            model = "Y2"
        else:
            return False, model
        is_rockbox = "rockbox" in name or "rockbox" in path or "rockbox" in slug
        is_240p = any("240p" in value for value in (name, path, asset_name, release_label))
        is_240p = is_240p or str(info.get("selected_resolution", "")).lower() == "240p"
        local_checked = bool(
            getattr(page, "is_local_rockbox_themes_checked", lambda: False)()
        )
        bundled_rockbox = bool(getattr(self, "_bundled_rockbox_themes", False))
        if not bundled_rockbox and getattr(self, "_package_path", None):
            try:
                from ..fw_package import has_rockbox_marker
                p = Path(self._package_path)
                bundled_rockbox = has_rockbox_marker(p.parent)
            except Exception:
                bundled_rockbox = False
        return bool(bundled_rockbox or (is_rockbox and not is_240p) or (local_checked and model == "Y1")), model

    def _rockbox_240p_detected(self) -> bool:
        """Whether this run installs a 240p Rockbox ROM (theme-site note, no bundled push)."""
        from ..fw_package import rockbox_240p_package_name

        name = str(getattr(self, "_package_name", "") or "")
        path = str(getattr(self, "_package_path", "") or "")
        page = getattr(self, "_select_page", None)
        info = getattr(page, "current_installed_release_info", lambda: None)() or {}
        slug = str(info.get("package_slug", "") or "")
        asset_name = str(info.get("asset_name", "") or "")
        release_label = str(info.get("release_label", "") or "")
        selected_rel = getattr(page, "_current_selected_rel", None) if page else None
        if selected_rel:
            asset_name = str(selected_rel.get("asset_name", asset_name) or asset_name)
            release_label = str(selected_rel.get("release_label", release_label) or release_label)
        fw_names: list[str] = []
        fw_pkg = getattr(page, "_fw_package", None)
        if fw_pkg is not None:
            fw_names.append(str(getattr(fw_pkg, "name", "") or ""))
            try:
                folder = getattr(fw_pkg, "folder", None)
                if folder is not None:
                    fw_names.append(Path(folder).name)
                scatter = getattr(fw_pkg, "scatter", None)
                if scatter is not None:
                    fw_names.append(Path(scatter).name)
            except Exception:
                pass
        if str(info.get("selected_resolution", "")).lower() == "240p" and any(
            "rockbox" in str(v).lower() for v in (name, path, slug, asset_name, release_label, *fw_names)
        ):
            return True
        return rockbox_240p_package_name(
            name, path, slug, asset_name, release_label, *fw_names
        )

    def _show_rockbox_power_on_prompt(self, model: str) -> None:
        """Ask the user to boot the flashed player before ADB/theme setup."""
        self._rockbox_power_on_pending = True
        self._rockbox_power_on_model = model
        prompt = tr(self._flash_page.rockbox_power_on_prompt_key(model)).format(model=model)
        self._flash_page.show_rockbox_power_on_prompt(
            prompt,
            on_continue=self._continue_rockbox_power_on,
        )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()
        self._nav_to_page(_PAGE_FLASH)
        self._adjust_window_geometry()

    def _continue_rockbox_power_on(self) -> None:
        """Start the assets stage once the user has powered on the Rockbox player."""
        if not getattr(self, "_rockbox_power_on_pending", False):
            return
        model = getattr(self, "_rockbox_power_on_model", "") or self._package_model
        self._rockbox_power_on_pending = False
        donation_disabled = device_tracking.is_donation_install_prompt_disabled(self.settings)
        self._start_themes_stage(model, donation_disabled, already_powered_on=True)

    def _install_run_active(self) -> bool:
        """True while an install flow owns the window, including prompts."""
        # A stage that is still running outranks the flash's own completion
        # flag: the Rockbox themes/fonts stage runs after the flash reports
        # success, and must keep Settings and Select locked just the same.
        if getattr(self, "_download_active", False):
            return True
        if getattr(self, "_prep_active", False):
            return True
        if getattr(self, "_gui_handoff_prepare", False):
            return True
        if getattr(self, "_rockbox_themes_flow_active", False):
            return True
        if getattr(self, "_rockbox_power_on_pending", False):
            return True
        if getattr(self, "_power_off_prompt", False):
            return True
        if getattr(self, "_install_complete", False) or getattr(self, "_install_failed", False):
            return False
        return hasattr(self, "sm") and self.sm.state in _INSTALL_RUN_STATES

    def _build_inline_prompt(self) -> QWidget:
        """Install prompts live under the page, in the content column.

        The window grows to fit the prompt and shrinks again when it closes.
        """
        from .flash_page import _RoundNextButton

        host = QWidget()
        host.setObjectName("installPrompt")
        layout = QHBoxLayout(host)
        layout.setContentsMargins(24, 4, 24, 12)
        layout.setSpacing(12)
        copy_col = QVBoxLayout()
        copy_col.setContentsMargins(0, 0, 0, 0)
        copy_col.setSpacing(4)
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
        copy_col.addWidget(self._prompt_title)
        copy_col.addWidget(self._prompt_body)
        layout.addLayout(copy_col, 1)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self._prompt_accept = _RoundNextButton(symbol="next", accent=True)
        self._prompt_reject = _RoundNextButton(symbol="back", accent=False)
        row.addWidget(self._prompt_accept, 0, Qt.AlignVCenter)
        row.addWidget(self._prompt_reject, 0, Qt.AlignVCenter)
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

    def _window_chrome(self) -> tuple[int, int]:
        """``(width, height)`` outside the page stack: rail, header, status bar."""
        stack = getattr(self, "_stack", None)
        if stack is None or stack.width() <= 0 or stack.height() <= 0:
            return (0, 0)
        return (
            max(0, self.width() - stack.width()),
            max(0, self.height() - stack.height()),
        )

    def _page_content_height(self, page) -> int:
        """The height a page wants, with nothing clipped.

        The catalogue measures itself (its list hugs its rows), a settings-style
        page measures the widget inside its scroll area, and anything else falls
        back to its own size hint.
        """
        hint = getattr(page, "content_height_hint", None)
        if callable(hint):
            try:
                value = int(hint())
                if value > 0:
                    return value
            except Exception:
                logger.debug("Content height hint failed", exc_info=True)
        if page is not None:
            from PySide6.QtWidgets import QAbstractScrollArea

            for area in page.findChildren(QAbstractScrollArea):
                try:
                    if not area.widgetResizable():
                        continue
                    inner = area.widget()
                except Exception:
                    continue
                if inner is not None:
                    value = int(inner.sizeHint().height()) + 2 * area.frameWidth()
                    if value > 0:
                        return value
            try:
                return max(int(page.sizeHint().height()), int(page.minimumSizeHint().height()))
            except Exception:
                logger.debug("Page size hint failed", exc_info=True)
        return self.height()

    def _content_floor_height(self) -> int:
        """Least window height that still shows two releases and both actions.

        The catalogue keeps its first two release rows and the Install /
        Restore button plus the Install-from-file link beneath them on screen
        at every window size, so the window can never be dragged shorter than
        that card needs. Clamped to the display, since a floor taller than the
        screen would only push the window off it.
        """
        floor = MINIMUM_WINDOW_HEIGHT
        page = getattr(self, "_select_page", None)
        measure = getattr(page, "minimum_install_height", None)
        if callable(measure):
            try:
                card_h = int(measure())
            except Exception:
                card_h = 0
            if card_h > 0:
                _, chrome_h = self._window_chrome()
                floor = max(floor, card_h + chrome_h + CONTENT_FIT_SLACK)
        screen = self.screen()
        if screen is not None:
            floor = min(floor, int(screen.availableGeometry().height()))
        return int(floor)

    def _apply_window_size_limits(self, content_height: int | None = None, fill_height: bool = False) -> None:
        """Cap resizing at the sizes this page can fill.

        Width stops at the widest catalogue layout; height at the page's own
        content, so dragging a border past either only spreads the two columns
        apart or leaves an empty band under the last card. Full screen still
        gives the log every pixel (``fill_height``).
        """
        stack = getattr(self, "_stack", None)
        page = stack.currentWidget() if stack is not None else None
        if content_height is None:
            content_height = self._page_content_height(page) if page is not None else 0
        _, chrome_h = self._window_chrome()
        max_w = MAXIMUM_WINDOW_WIDTH
        is_hugging = getattr(self, "_hugging_install", False) or (
            stack is not None and stack.currentIndex() == _PAGE_FLASH
        )
        min_floor = (
            COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS
            if is_hugging
            else self._content_floor_height()
        )
        max_h = max(min_floor, int(content_height) + chrome_h + CONTENT_FIT_SLACK)
        if is_hugging:
            # The rail's community row must stay on screen during an install too.
            nav = getattr(self, "_nav_panel", None)
            if nav is not None:
                max_h = max(max_h, nav.sizeHint().height() + chrome_h)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            # Never cap below the window's own floor: the rail plus the page
            # column's minimum is what has to fit, even on a small display.
            max_w = max(self.minimumWidth(), MINIMUM_WINDOW_WIDTH, min(max_w, int(available.width())))
            if fill_height:
                max_h = int(available.height())
            else:
                max_h = max(min_floor, min(max_h, int(available.height())))
        if stack is not None and stack.currentIndex() == _PAGE_SELECT and not fill_height:
            max_h = min(max_h, MAXIMUM_SELECT_WINDOW_HEIGHT)
        if (max_w, max_h) != (self.maximumWidth(), self.maximumHeight()):
            self.setMaximumSize(max_w, max_h)

    def _fit_window_to_page(self, *_args) -> None:
        """Size the window to the page on screen — shrinking as well as growing.

        Every page switch and every content change ends here: a release picked
        from the list, a refilled list, retranslated copy, a finished download.
        A page that needs room takes it and a short one gives it back, so the
        empty band a taller window would add is never left behind. The install
        progress card keeps its own hug, and the log keeps the size the user
        gave it.
        """
        if (
            self.isMinimized()
            or self.isMaximized()
            or self.isFullScreen()
            or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        ):
            return
        stack = getattr(self, "_stack", None)
        page = stack.currentWidget() if stack is not None else None
        if page is None:
            return
        if stack.currentIndex() == _PAGE_DIAGNOSTICS:
            self._apply_window_size_limits(fill_height=True)
            return
        chrome_w, chrome_h = self._window_chrome()
        # The rail's width is the window's width chrome. Take it from the policy
        # too: a rail resized this turn has not been laid out yet, so the
        # geometry can still describe the previous width.
        chrome_w = max(int(chrome_w), int(self._rail_width.width))
        content_h = self._page_content_height(page)
        target_h = int(content_h) + chrome_h + CONTENT_FIT_SLACK
        if stack.currentIndex() == _PAGE_SELECT:
            target_h = min(target_h, MAXIMUM_SELECT_WINDOW_HEIGHT)
        try:
            target_h = max(target_h, int(page.minimumSizeHint().height()) + chrome_h)
            need_w = int(page.minimumSizeHint().width()) + chrome_w
        except Exception:
            need_w = MINIMUM_WINDOW_WIDTH
        # A page whose column cannot be squeezed (the Settings cards, measured
        # inside their scroll area) says how much room it really needs. The
        # page's own minimum is the scroll area's, which is tiny, so without
        # this the cards were cut off at the right edge in a narrow window.
        # The rail's own width is part of the floor: it is sized from its
        # labels, so it grows with the language.
        floor_w = self._minimum_window_width()
        width_hint = getattr(page, "content_width_hint", None)
        if callable(width_hint):
            try:
                hinted = int(width_hint()) + chrome_w
            except Exception:
                hinted = 0
            if hinted > 0:
                need_w = max(need_w, hinted)
                floor_w = hinted
        target_h = max(self._content_floor_height(), target_h)
        # The width the user chose is kept: only a page that cannot be drawn in
        # it (long translated words, a wide form) makes the window wider.
        target_w = max(self.width(), MINIMUM_WINDOW_WIDTH, need_w)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            target_w = min(target_w, int(available.width()), MAXIMUM_WINDOW_WIDTH)
            target_h = min(target_h, int(available.height()))
            floor_w = min(floor_w, int(available.width()), MAXIMUM_WINDOW_WIDTH)
        if stack.currentIndex() == _PAGE_SETTINGS:
            max_settings_h = min(int(available.height()) - 60, 560) if screen is not None else 560
            target_h = min(target_h, max(DEFAULT_WINDOW_HEIGHT, max_settings_h))
        self._apply_window_size_limits(target_h)
        floor_w = max(MINIMUM_WINDOW_WIDTH, min(floor_w, target_w))
        if target_w != self.width() or target_h != self.height():
            self._animate_window_size(
                target_w, target_h, minimum=(floor_w, self._content_floor_height())
            )
        elif floor_w > self.minimumWidth():
            # Nothing to resize, but the page still must not be dragged
            # narrower than its cards.
            self.setMinimumWidth(floor_w)

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
        # The log needs the full window, including while an install is running:
        # it is the one page that is allowed to keep growing past its content.
        if curr_idx == _PAGE_DIAGNOSTICS:
            self._apply_window_size_limits(fill_height=True)
            width, height = default_window_size(self.screen())
            self._animate_window_size(
                width, height, minimum=(self._minimum_window_width(), self._content_floor_height()),
            )
            return

        is_install = self._install_run_active()
        donations_disabled = (
            device_tracking.is_donation_ui_disabled(self.settings)
            or device_tracking.is_donation_install_prompt_disabled(self.settings)
            or not (hasattr(self._flash_page, "_appeal") and self._flash_page._appeal.isVisible())
        )
        install_flags = is_install or bool(
            getattr(self, "_install_complete", False)
            or getattr(self, "_install_failed", False)
            or getattr(self, "_power_off_prompt", False)
            or getattr(self, "_drop_check_active", False)
        )
        # The flags outlive the card (a failed or finished install keeps its
        # state for when the user comes back), so they only decide the size
        # while the card is the page on screen. Any other page owns the window
        # and grows it back. The catalogue is the one exception: it is still
        # showing for a moment after Install starts.
        hug_install = install_flags and (
            curr_idx == _PAGE_FLASH or (curr_idx == _PAGE_SELECT and is_install)
        )

        if hug_install:
            # Hug the progress card. The minimum comes from the wrapped copy
            # (package name, prompt, buttons), not a fixed height that clips it.
            page = self._flash_page
            if hasattr(page, "sync_text_fit"):
                page.sync_text_fit()
            if page.layout() is not None:
                page.layout().activate()
            self._stack.updateGeometry()
            nav = getattr(self, "_nav_panel", None)
            sidebar = nav.width() if nav is not None and nav.width() > 0 else 175
            from .flash_page import install_horizontal_chrome

            text_w = page.text_column_width() if hasattr(page, "text_column_width") else 260
            content_w = max(1, int(text_w) + install_horizontal_chrome(0))

            if curr_idx == _PAGE_FLASH and hasattr(page, "content_height_hint"):
                page_h = page.content_height_hint(content_w)
            else:
                page_h = page.sizeHint().height()
                if page.hasHeightForWidth():
                    fitted = page.heightForWidth(content_w)
                    if 0 < fitted < 1600:
                        page_h = max(page_h, fitted)
            prompt = getattr(self, "_inline_prompt", None)
            prompt_h = 0
            if prompt is not None and not prompt.isHidden():
                prompt_h = prompt.sizeHint().height()
                if prompt.hasHeightForWidth():
                    body_w = max(1, int(text_w))
                    fitted_prompt = prompt.heightForWidth(body_w)
                    if 0 < fitted_prompt < 800:
                        prompt_h = max(prompt_h, fitted_prompt)
            bar = self.statusBar()
            status_h = bar.sizeHint().height() if bar is not None and bar.isVisible() else 0
            # Only the height hugs the progress card: the window keeps the width
            # it already has (the app's ordinary width), widened if the wrapped
            # copy needs more room. Clamped to the screen so a wide window does
            # not push the card off the display.
            target_w = max(self.width(), install_horizontal_chrome(sidebar) + int(text_w))
            scr = self.screen()
            if scr is not None:
                target_w = min(target_w, int(scr.availableGeometry().width()))
            if curr_idx == _PAGE_FLASH:
                actual_chrome = (
                    status_h + (int(HEADER_CONTENT_HEIGHT) if getattr(self, "_title_bar", None) is not None else 0)
                )
                target_h = max(
                    compact_install_height(
                        page_h=page_h,
                        prompt_h=prompt_h,
                        status_h=status_h,
                        inline_header=getattr(self, "_title_bar", None) is not None,
                        donations_disabled=donations_disabled,
                    ),
                    page_h + prompt_h + actual_chrome + 8,
                )
                # The rail counts during an install too: its community row
                # (Discord, Reddit, GitHub) sits at the bottom and was clipped
                # when only the progress card was measured.
                if nav is not None:
                    target_h = max(target_h, nav.sizeHint().height() + actual_chrome)
            else:
                # The catalogue is still showing. The short install size is
                # applied once the progress card is the page on screen. The
                # rail still needs its full height, community row included.
                target_h = 228
                if nav is not None:
                    target_h = max(target_h, nav.sizeHint().height() + int(HEADER_CONTENT_HEIGHT))
            self._hugging_install = True
            self._apply_window_size_limits(target_h)
            self._animate_window_size(target_w, target_h, minimum=(target_w, target_h))
        else:
            min_w = self._minimum_window_width()
            min_h = self._content_floor_height()
            self._hugging_install = False
            self.setMinimumSize(min_w, min_h)
            # Whatever page is on screen now owns the window size: leaving the
            # install card grows it back, a short page gives the room up.
            self._fit_window_to_page()

    def _on_support_content_changed(self) -> None:
        """The appeal rewrapped: give the window the height the card now needs.

        The donated card is sized from its content, and that content is a
        word-wrapped paragraph: a narrower window means more lines. Without
        this the card kept the height of the width it was built at and cut the
        last lines off.
        """
        stack = getattr(self, "_stack", None)
        page = getattr(self, "_support_page", None)
        if stack is None or page is None or stack.currentWidget() is not page:
            return
        if (
            self.isMinimized()
            or self.isMaximized()
            or self.isFullScreen()
            or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        ):
            return
        self._fit_window_to_page()

    def _on_settings_content_changed(self):
        """A Settings card changed size: keep the window wide enough for it.

        The freed-space line under Clear Firmware Cache appears after the page
        is already on screen, and it widens that card. The window takes the new
        width instead of the scroll area clipping it.
        """
        stack = getattr(self, "_stack", None)
        if stack is None or stack.currentIndex() != _PAGE_SETTINGS:
            return
        if self.isMinimized() or self.isMaximized() or self.isFullScreen():
            return
        if getattr(self, "_settings_fit_queued", False):
            return
        self._settings_fit_queued = True
        # One event-loop turn later: a label that has just been given a longer
        # text only reports its new minimum after the relayout pass, so
        # measuring now still reads the previous width.
        QTimer.singleShot(0, self._fit_settings_page_width)

    def _fit_settings_page_width(self):
        """Deferred half of _on_settings_content_changed: measure, then grow."""
        self._settings_fit_queued = False
        stack = getattr(self, "_stack", None)
        page = getattr(self, "_settings_page", None)
        if stack is None or page is None or stack.currentIndex() != _PAGE_SETTINGS:
            return
        if self.isMinimized() or self.isMaximized() or self.isFullScreen():
            return
        scroll = getattr(page, "_settings_scroll", None)
        for _ in range(3):
            QApplication.sendPostedEvents()
            layout = page.layout()
            if layout is not None:
                layout.activate()
            if scroll is not None:
                inner = scroll.widget()
                if inner is not None:
                    inner.updateGeometry()
                scroll.viewport().updateGeometry()
        self._fit_window_to_page()

    def _on_select_content_changed(self):
        """Resize the window for the software list contents.

        The release list hugs its rows, so the window grows to show more items
        and shrinks back when the list is shorter — clamped to the screen and
        to the page's own content. The install flow keeps owning the window
        size while it runs. Skipped on other pages, or when not yet visible
        (retried on show).
        """
        try:
            if (
                self.isMinimized()
                or self.isMaximized()
                or self.isFullScreen()
                or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
                or self._install_run_active()
                or getattr(self, "_install_complete", False)
                or getattr(self, "_install_failed", False)
                or getattr(self, "_power_off_prompt", False)
            ):
                return
            stack = getattr(self, "_stack", None)
            page = getattr(self, "_select_page", None)
            if stack is None or page is None or stack.currentWidget() is not page:
                return
            if not self.isVisible() or stack.height() <= 0:
                self._pending_select_fit = True
                return
            self._pending_select_fit = False
            # The fit signal fires synchronously from the list refill, while
            # the layout item caches still describe the previous rows — Qt
            # only refreshes them on the next relayout pass. Defer the
            # measurement a tick (coalescing rapid refills) so the hint
            # below measures the new rows instead of the old ones.
            if getattr(self, "_select_fit_queued", False):
                return
            self._select_fit_queued = True
            QTimer.singleShot(0, self._fit_window_to_select_content)
        except Exception:
            logger.debug("Select content fit failed", exc_info=True)

    def _fit_window_to_select_content(self):
        """Deferred half of _on_select_content_changed: measure and grow."""
        self._select_fit_queued = False
        try:
            if (
                self.isMinimized()
                or self.isMaximized()
                or self.isFullScreen()
                or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
                or self._install_run_active()
                or getattr(self, "_install_complete", False)
                or getattr(self, "_install_failed", False)
                or getattr(self, "_power_off_prompt", False)
            ):
                return
            stack = getattr(self, "_stack", None)
            page = getattr(self, "_select_page", None)
            if stack is None or page is None or stack.currentWidget() is not page:
                return
            if not self.isVisible() or stack.height() <= 0:
                self._pending_select_fit = True
                return
            # The relayout cascade crosses several nesting levels (and a
            # stacked widget), so one pass is not enough: flush posted events
            # until the measured height settles, with a small cap.
            need = -1
            for _ in range(5):
                QApplication.sendPostedEvents()
                settled = page.content_height_hint()
                if settled == need:
                    break
                need = settled
            stack.updateGeometry()
            self._fit_window_to_page()
        except Exception:
            logger.debug("Select content fit failed", exc_info=True)

    def _animate_window_size(self, width: int, height: int, minimum=None) -> None:
        """Ease a programmatic resize. Reduced motion and a maximized window snap."""
        from .widgets import prefers_reduced_motion

        width = int(width)
        height = int(height)
        anim = getattr(self, "_size_anim", None)
        if anim is not None:
            if anim.state() == QPropertyAnimation.State.Running and anim.endValue() == QSize(width, height):
                return
            self._size_anim = None
            try:
                anim.finished.disconnect()
            except Exception:
                pass
            anim.stop()
        if minimum is not None:
            self._size_anim_minimum = (int(minimum[0]), int(minimum[1]))
        else:
            self._size_anim_minimum = None
        # Drop the floor before the snap so a shorter install card can land.
        # The final minimum is put back in _finish_size_anim.
        floor = getattr(self, "_size_anim_minimum", None)
        if floor is not None:
            self.setMinimumSize(
                min(self.minimumWidth(), floor[0], width),
                min(self.minimumHeight(), floor[1], height),
            )
        if (
            prefers_reduced_motion()
            or self.isMaximized()
            or self.isFullScreen()
            or (self.width() == width and self.height() == height)
        ):
            self._size_animating = False
            self.resize(width, height)
            self._finish_size_anim()
            return
        self._size_animating = True
        anim = QPropertyAnimation(self, b"size", self)
        anim.setDuration(200)
        anim.setStartValue(self.size())
        anim.setEndValue(QSize(width, height))
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._finish_size_anim)
        self._size_anim = anim
        anim.start()

    def _finish_size_anim(self) -> None:
        self._size_anim = None
        self._size_animating = False
        floor = getattr(self, "_size_anim_minimum", None)
        self._size_anim_minimum = None
        if floor is not None:
            self.setMinimumSize(floor[0], floor[1])
        if hasattr(self, "_verify_native_chrome"):
            self._verify_native_chrome()
        if hasattr(self, "_schedule_caption_button_refresh"):
            self._schedule_caption_button_refresh()

    def _handoff_chrome(self) -> bool:
        """The disconnect hint and the download that leads to it."""
        return bool(
            getattr(self, "_gui_handoff_prepare", False)
            or getattr(self, "_gui_handoff_ready", False)
            or getattr(self, "_power_off_prompt", False)
        )

    def _apply_install_ui_state(self, active: bool):
        handoff = self._handoff_chrome()
        key = (
            bool(active),
            bool(handoff),
        )
        if getattr(self, "_install_ui_key", None) == key:
            if not active:
                self._reveal_sp_gui_button()
            return
        self._install_ui_key = key
        self._install_ui_active = active
        try:
            set_window_close_button_enabled(self, not active)
        except Exception:
            pass
        controls = getattr(self, "_window_controls", None)
        if controls is not None:
            controls.set_close_enabled(not active)

        if active:
            # During install / extraction / prep stages: only show "Install Software"
            # and Diagnostics (simplified user facing install output).
            if hasattr(self, "_settings_btn"):
                self._settings_btn.setVisible(False)
                self._settings_btn.setEnabled(False)
            if hasattr(self, "_support_btn"):
                self._support_btn.setVisible(False)
            # The community marks follow the Donations setting even mid-install,
            # so users can find help while a run is in progress.
            self._set_help_row_visible(
                not device_tracking.is_donation_ui_disabled(self.settings)
            )
            if hasattr(self, "_log_btn"):
                self._sync_diagnostics_button()
            self._sync_update_button()
            if hasattr(self, "_linux_setup_btn"):
                self._linux_setup_btn.setVisible(False)
            if hasattr(self, "_sp_flash_tool_btn"):
                self._sp_flash_tool_btn.setVisible(False)
                self._sp_flash_tool_btn.setEnabled(False)
            if hasattr(self, "_install_output_btn"):
                self._install_output_btn.setVisible(True)
                self._install_output_btn.setEnabled(True)
            # The language selector stays put during install / prep / themes:
            # a reader who started in one language can switch to their own and
            # follow the prompts and instructions in it. Every screen in the
            # flow retranslates live through ``_retranslate_all``.
            if hasattr(self, "_lang_label"):
                self._lang_label.setVisible(True)
            if hasattr(self, "_lang_combo"):
                self._lang_combo.setVisible(True)
        else:
            if hasattr(self, "_settings_btn"):
                self._settings_btn.setVisible(True)
                self._settings_btn.setEnabled(True)
            if hasattr(self, "_support_btn"):
                self._support_btn.setVisible(True)
            self._set_help_row_visible(True)
            if hasattr(self, "_log_btn"):
                self._sync_diagnostics_button()
            self._sync_update_button()
            if hasattr(self, "_linux_setup_btn"):
                setup_done = self.settings.value(
                    "linux_setup_completed", False, type=bool
                ) or getattr(self, "_linux_setup_flow_active", False)
                self._linux_setup_btn.setVisible(not setup_done or self._linux_setup_flow_active)
                self._linux_setup_btn.setEnabled(not setup_done or self._linux_setup_flow_active)
            if hasattr(self, "_sp_flash_tool_btn"):
                self._sync_sp_gui_button_visibility()
            if hasattr(self, "_install_output_btn"):
                self._install_output_btn.setVisible(False)
            if hasattr(self, "_lang_label"):
                self._lang_label.setVisible(True)
            if hasattr(self, "_lang_combo"):
                self._lang_combo.setVisible(True)
            self._apply_donation_visibility()
            self._reveal_sp_gui_button()

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
        active = self._install_run_active()
        btn.setText(tr("nav_install_package" if active else "nav_select_package"))
        self._apply_install_ui_state(active)
        self._reapply_rail_width()
        return active

    def _on_select_nav_clicked(self):
        """First entry: package select. While a run owns the window this entry
        only reopens the install card.

        It must never cancel the run or open Select Software: cancelling is
        the install card's own ✕, which asks before stopping. A click on this
        entry during an install is a request to see the run, nothing more.
        """
        if self._install_run_active():
            if self._stack.currentIndex() != _PAGE_FLASH:
                self._nav_to_page(_PAGE_FLASH)
            else:
                self._highlight_nav(_PAGE_FLASH, True)
            return
        if getattr(self, "_gui_handoff_ready", False):
            self._power_off_prompt = False
            self._flash_page.leave_power_off_prompt()
            self._nav_to_page(_PAGE_SELECT)
            return
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
            if getattr(self, "_install_failed", False) or (
                hasattr(self, "sm") and self.sm.state in (FlashState.S5_FAILED, FlashState.S5_USB_DISCONNECTED)
            ):
                return getattr(self, "_failure_title", None) or tr("install_failed_title")
            if getattr(self, "_power_off_prompt", False):
                return tr("dialog_pre_install_title")
            if getattr(self, "_install_complete", False):
                return tr("flash_install_complete")
            if getattr(self, "_download_active", False):
                return tr("flash_download_in_progress")
            return tr("flash_install_in_progress") if install_active else tr("flash_ready_title")
        if page_idx == _PAGE_ERROR:
            return getattr(self, "_failure_title", None) or tr("install_failed_title")
        if page_idx == _PAGE_RETRY:
            return tr("flash_retry_title")
        if page_idx == _PAGE_NOTICE:
            return getattr(self, "_notice_title", "") or tr("sel_title")
        if page_idx == _PAGE_SUPPORT:
            return tr("nav_donate")
        return tr("sel_title")

    def _update_header_title(self):
        """Update header page title text and visual style in the unified title bar."""
        if not (hasattr(self, "_select_page") and hasattr(self._select_page, "_title")):
            return
        title = self._select_page._title
        curr_idx = self._stack.currentIndex() if hasattr(self, "_stack") else _PAGE_SELECT
        install_active = self._install_run_active() or getattr(self, "_compact_install_screen", False)
        failed = getattr(self, "_install_failed", False) or (
            hasattr(self, "sm") and self.sm.state in (FlashState.S5_FAILED, FlashState.S5_USB_DISCONNECTED)
        )
        text = self._page_title(curr_idx, install_active)
        title.setText(text)
        self._align_page_title()

        is_mac = sys.platform == "darwin"
        font_size = "13px" if is_mac else "14px"
        from .dark import T
        t = T()
        if failed and curr_idx in (_PAGE_FLASH, _PAGE_ERROR):
            title.setStyleSheet(
                f"font-size: {font_size}; font-weight: 700; color: {t.danger_fg}; background: transparent; border: none;"
            )
        else:
            title.setStyleSheet(
                f"font-size: {font_size}; font-weight: 600; color: palette(window-text); letter-spacing: normal; background: transparent; border: none;"
            )
        from .surfaces import seal_updating_text
        seal_updating_text(title)
        title.updateGeometry()
        title_host = getattr(self, "_title_container", None)
        if title_host is not None:
            if title_host.layout() is not None:
                title_host.layout().activate()
            bar = getattr(self, "_title_bar", None)
            if bar is not None and bar.layout() is not None:
                bar.layout().activate()
            paint_window_surface(title_host)
            title_host.update()

    def _highlight_nav(self, page_idx, install_active: bool):
        SidebarButton.dismiss_focus_rings()
        if page_idx == _PAGE_DIAGNOSTICS and hasattr(self, "_log_btn"):
            checked = self._log_btn
        elif page_idx == _PAGE_SUPPORT:
            checked = getattr(self, "_support_btn", None)
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
            getattr(self, "_support_btn", None),
        ):
            if btn is not None:
                btn.update()

    def _nav_to_page(self, page_idx):
        SidebarButton.dismiss_focus_rings()
        # Settings and Select Software are closed while a run owns the window:
        # the user finishes, fails or cancels the install on its card first.
        # The guard runs before anything else so a blocked call can never
        # disturb the run's processes or its session on the way out.
        if self._install_run_active() and page_idx not in (
            _PAGE_FLASH, _PAGE_DIAGNOSTICS, _PAGE_NOTICE, _PAGE_RETRY,
        ):
            # A click may already have checked the blocked entry. Put the
            # highlight back on the page that is actually showing.
            self._highlight_nav(self._stack.currentIndex(), True)
            return
        self._stack.setCurrentIndex(page_idx)
        from .surfaces import harmonise_window_plates
        harmonise_window_plates(self)
        # A card shown on its own owns the Settings page only while that page is
        # the one on screen; leaving hands the column back.
        settings_page = getattr(self, "_settings_page", None)
        left_linux_setup = bool(
            settings_page is not None
            and page_idx != _PAGE_SETTINGS
            and settings_page.focused_card() is getattr(settings_page, "_checker_card", None)
            and getattr(settings_page, "_checker_card", None) is not None
        )
        if (
            settings_page is not None
            and page_idx != _PAGE_SETTINGS
            and settings_page.focused_card() is not None
        ):
            self._legacy_focus_pending = False
            settings_page.clear_card_focus()
            if left_linux_setup:
                self._linux_setup_flow_active = False
                self._sync_sp_gui_button_visibility()
        if self._progress_owns_status():
            self._release_status_to_donors()
        install_active = self._sync_install_nav_entry()
        self._highlight_nav(page_idx, install_active)

        # Update header page title in the unified title bar
        self._update_header_title()

        self._adjust_window_geometry()
        QTimer.singleShot(0, self._verify_native_chrome)

    def _flash_inputs(self):
        """Explicit Download Agent / auth file from Select Software.

        The MediaTek Installer carries a DA (and optionally an auth file) beside
        the scatter; Updater CE leaves the DA empty so the bundled
        ``MTK_AllInOne_DA.bin`` applies and falls back to the saved auth file.
        """
        da = ""
        auth = ""
        page = getattr(self, "_select_page", None)
        if page is not None:
            getter = getattr(page, "installer_da_file", None)
            if callable(getter):
                try:
                    da = getter() or ""
                except Exception:
                    da = ""
            getter = getattr(page, "installer_auth_file", None)
            if callable(getter):
                try:
                    auth = getter() or ""
                except Exception:
                    auth = ""
        if not auth:
            auth = device_tracking.sp_auth_file(self.settings)
        return da, auth

    def _on_package_selected(self, path, name, model):
        self._remember_catalogue_section()
        self._download_active = False
        self._prep_active = False
        self._package_path = path
        self._package_name = name
        self._package_model = model or ""
        # Only the auto-started ROM-bundled run ends on Retry or Quit.
        self._bundled_firmware_run = bool(path) and path == getattr(self, "_bundled_package_path", None)
        if hasattr(self, "_flash_page"):
            self._flash_page.set_bundled_run(self._bundled_firmware_run)
        self._is_240p_rockbox = False
        if hasattr(self, "_flash_page"):
            self._flash_page.set_rockbox_site_themes(False)
        self._pre_install_guided = False
        self._cache_recovery_needed = False
        self._fallback_attempted = False
        self._sp_1013_seen = False
        self._flash_method = self._preferred_flash_method()
        if hasattr(self, "_flash_page"):
            self._flash_page.set_method(self._flash_method)

        self._sync_sp_history()

        from ..sp_flash_gui import is_sp_flash_gui_supported

        # Same effective choice the Settings page shows: a stored flag only
        # means "SP Flash Tool GUI" where the GUI is actually supported.
        if device_tracking.sp_gui_install_enabled(self.settings) and is_sp_flash_gui_supported():
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

        if not self._ensure_elevated_for_install():
            return
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

        da_file, auth_file = self._flash_inputs()
        command = terminal_install.build_install_command(
            self._flash_method,
            extract_dir,
            scatter,
            package_path=self._package_path,
            platform_name=platform_name,
            auth_file=auth_file,
            da_file=da_file,
            model=self._package_model,
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
            # machine's environment lacks (Linux SP Flash Tool libraries), and
            # carries the app's other copies as fallbacks: the bundle it is
            # running from is a build output, so a rebuild between generating
            # this launcher and running it must not break a hand-run install.
            script = terminal_install.build_script(
                command,
                title=tr("terminal_install_title"),
                cwd=extract_dir,
                env=terminal_install.script_env(command),
                fallbacks=terminal_install.launcher_candidates(command[0]),
                identity=terminal_install.launcher_identity(command[0]),
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
        """Install with the SP Flash Tool GUI: prepare the firmware, then hand it over.

        The selected package's extract is checked first, because a run that was
        interrupted leaves a scatter with images missing from beside it. When it
        is not usable the package is prepared again in this window — the usual
        download/extract progress screen — and only once a complete scatter and
        its images are on disk is history.ini written, every session this app
        started is stopped, and the tool's own window opened. The app is then
        back on its main screen while SP Flash Tool does the flashing.
        """
        from .. import sp_flash_gui
        from ..flash_service import completed_extract_dir, compute_extract_dir, _find_scatter

        if getattr(self, "_gui_launch_in_flight", False):
            # The tool is already opening (the privilege prompt on Linux can
            # stay up for minutes). Selecting another package now must not start
            # a second copy against the same device.
            return
        page = getattr(self, "_select_page", None)
        target = page.gui_firmware_target() if page is not None else None
        if target is None:
            self._sync_sp_gui_button_visibility()
            return
        if not sp_flash_gui.is_sp_flash_gui_supported():
            QMessageBox.warning(self, tr("sp_gui_title"), tr("sp_gui_not_supported"))
            return
        if not self._ensure_elevated_for_install():
            return

        package_path = target.get("path") or target.get("dest") or self._package_path
        model = target.get("model") or self._package_model or ""
        if target.get("kind") == "cache":
            self._gui_ready = (
                Path(target["scatter"]),
                Path(target["extract"]),
                model,
            )
            self._complete_gui_handoff()
            return

        extract_dir = completed_extract_dir(package_path) or compute_extract_dir(
            package_path
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
            model=model,
        )
        if ready:
            self._gui_ready = (Path(sc), Path(ed), model)
            self._complete_gui_handoff()
            return

        # The on-screen eligible target has either a not-yet-downloaded
        # catalogue release or a partial local archive. Do not replace it with
        # the previous install's cache: that would silently select a different
        # release than the one the user focused/browsed.
        if target.get("kind") == "cache":
            return

        # A local package whose scatter or images are missing must never be
        # flashed: refuse it rather than attempting to repair it.
        if target.get("kind") == "local":
            logger.warning("The selected local firmware is incomplete, refusing: %s", reason)
            QMessageBox.warning(self, tr("sp_gui_title"), tr("sp_gui_no_cached_firmware"))
            return

        # Not usable yet: prepare it here instead of turning it into an error.
        logger.info("The selected firmware is not complete, preparing it again: %s", reason)
        self._append_log(tr("sp_gui_preparing_firmware"))
        if target is None:
            QMessageBox.warning(
                self,
                tr("sp_gui_title"),
                tr_brand("gui_handoff_no_package"),
            )
            return
        self._gui_after_prepare = "launch"
        self._gui_launch_when_ready = True
        if not self._begin_gui_prepare(target):
            QMessageBox.warning(
                self,
                tr("sp_gui_title"),
                tr_brand("gui_handoff_no_package"),
            )

    def _package_prepare_target(self):
        """The package the user selected, as something the prepare worker can take.

        An archive that is already on disk only needs extracting again, which is
        exactly what repairs an extract an interrupted run left half-written.
        """
        path = str(getattr(self, "_package_path", "") or "")
        if not path or not Path(path).is_file():
            return None
        return {
            "kind": "local",
            "path": path,
            "model": self._package_model or "",
            "name": self._package_name or Path(path).name,
        }

    def _needs_linux_device_prep(self) -> bool:
        return bool(
            platform.system() == "Linux"
            and not paths.IS_MAC
            and not is_mediatek_installer()
            and os.environ.get("QT_QPA_PLATFORM") != "offscreen"
            and not os.environ.get("INNIOASIS_HEADLESS")
        )

    def _start_linux_install_prep(self) -> bool:
        """Make one safe, device-aware Linux prep attempt before starting flash."""
        from .. import linux_sp_flash

        prep_copy = (
            "install_linux_prep_pending_friendly"
            if is_mediatek_installer()
            else "install_linux_prep_pending"
        )
        self._flash_page.update_prep_progress(1)
        self._flash_page.set_prep_eta_seconds(0)
        self._append_log(tr(prep_copy))
        if backend_method(self._flash_method) == METHOD_MTK:
            # MTKClient talks to the player through libusb as the normal user:
            # there is nothing to elevate and no system change to make, so no
            # password prompt is shown for an MTKClient install. SP Flash Tool
            # still gets its one-time setup when the run falls back to it.
            try:
                linux_sp_flash.silent_system_prep()
            except Exception as exc:
                logger.info("Optional Linux user-space prep did not complete: %s", exc)
            self._linux_permission_pending = False
            return True
        try:
            # Ordinary user-space pieces never need elevation.
            linux_sp_flash.silent_system_prep()
            # No device probing here: SP Flash Tool and mtk.py report their own
            # connection problems. Prep only makes the system ready for them.
            report = linux_sp_flash.run_system_checker()
            needs_access = any(
                item.get("key") == "udev_rules" and item.get("status") != "ok"
                for item in (report.get("items") or [])
            ) or any(
                item.get("key") == "user_groups" and item.get("status") != "ok"
                for item in (report.get("items") or [])
            )
            if needs_access and not getattr(self, "_linux_permission_confirmed", False):
                # Wizard stage: nothing is prompted until the user clicks Next.
                # The password prompt and the connect guidance come after it.
                self._linux_permission_pending = True
                self._show_device_prep(permission=True)
                return False
            if not needs_access:
                self._linux_permission_confirmed = False
        except Exception as exc:
            logger.info("Optional Linux device preparation did not complete: %s", exc)
            self._append_log(tr("install_without_admin"))
        return True

    def _on_linux_permission_next(self) -> None:
        """The user clicked Next on the Linux access step: ask for the password now."""
        from .. import linux_sp_flash

        self._linux_permission_pending = False
        self._linux_permission_confirmed = True
        # The system password dialog opens over this card, so say what it is
        # for instead of leaving the generic "please wait" line up while the
        # user is being asked to authenticate. This is the SP Flash Tool setup
        # step only; MTKClient installs never reach it.
        self._flash_page.show_entering_password()
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
        except Exception:
            pass
        try:
            fixed, detail = linux_sp_flash.auto_fix_permissions()
        except Exception as exc:
            logger.info("Linux access setup failed: %s", exc)
            fixed, detail = False, str(exc)
        self._append_log(
            tr("install_linux_prep_pending_friendly") if fixed else (detail or tr("install_without_admin"))
        )
        self._begin_flash_flow()

    def _ensure_elevated_for_install(self) -> bool:
        """Honour the Settings "run as administrator" switch before an install.

        The flash tools are children of this process, so they inherit its
        rights: asking Windows to restart Updater CE elevated once is what makes
        every install mode elevated, and keeps the live progress stream and the
        cancel path working (elevating one tool at a time would lose both).
        Returns False when the elevated copy is taking over and this run must
        not start.
        """
        from .. import elevation

        if not elevation.run_as_admin_enabled(self.settings) or elevation.is_elevated():
            return True
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("CI"):
            return True
        reply = QMessageBox.warning(
            self,
            tr("install_needs_admin_title"),
            tr("install_needs_admin_body"),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if reply != QMessageBox.Yes:
            self._append_log(tr("install_without_admin"))
            return True
        ok, msg = elevation.restart_as_admin()
        if not ok:
            self._append_log(msg or tr("install_without_admin"))
            return True
        from PySide6.QtWidgets import QApplication

        self._append_log(tr("install_restarting_as_admin"))
        QApplication.quit()
        return False

    def _remember_catalogue_section(self) -> None:
        page = getattr(self, "_select_page", None)
        if page is not None and hasattr(page, "catalogue_section"):
            self._catalogue_section = page.catalogue_section()

    def _begin_run_output(self) -> None:
        if not getattr(self, "_run_output_open", False):
            self._run_output = []
            self._run_output_open = True

    def _needs_device_prep(self) -> bool:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("INNIOASIS_HEADLESS"):
            return False
        page = getattr(self, "_select_page", None)
        if page is not None and hasattr(page, "_should_prompt_pre_install"):
            return bool(page._should_prompt_pre_install())
        return True

    def _show_device_prep(self, permission: bool = False) -> None:
        """Device preparation lives on the install page, not in a dialog."""
        self._install_failed = False
        self._install_complete = False
        self._power_off_prompt = True
        self.service.stop_device_monitor()
        try:
            self.service.take_primed_mtk()
        except Exception:
            pass
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._flash_page.set_method(self._flash_method)
        try:
            from ..flash_service import completed_extract_dir, _find_scatter, _parse_scatter_platform
            ed = completed_extract_dir(self._package_path)
            if ed:
                sc = _find_scatter(Path(ed))
                if sc:
                    platform = _parse_scatter_platform(sc) or ""
                    if hasattr(self._flash_page, "set_platform"):
                        self._flash_page.set_platform(platform)
        except Exception:
            pass
        # This screen shows the release's own image, not only the settings
        # stand-in: it is the first thing the user sees when the install starts,
        # and the catalogue already has the image by then.
        self._apply_release_icon(complete=False)
        self._flash_page.show_power_off_prompt(permission=permission)
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_FLASH)

    def _on_install_done(self) -> None:
        """Leave the completed install and reopen the catalogue the run started from."""
        if getattr(self, "_bundled_firmware_run", False):
            if getattr(self, "_is_240p_rockbox", False):
                # Pre-packaged 240p Rockbox: the check button finishes by quitting the app.
                self._flash_page.show_completion_appeal(False)
                self._quit_after_bundled_run()
                return
            # ROM-bundled portable run: stay on the run's card (Retry or Quit), never Select.
            self._flash_page.stop_theme_workers()
            self._rockbox_themes_flow_active = False
            self._run_output_open = False
            self._download_active = False
            self._flash_page.show_completion_appeal(False)
            self._flash_page.show_flashing()
            return
        section = getattr(self, "_catalogue_section", "online") or "online"
        self._flash_page.stop_theme_workers()
        self._rockbox_power_on_pending = False
        self._rockbox_power_on_model = ""
        self._rockbox_themes_flow_active = False
        self._run_output_open = False
        self._download_active = False
        self._reset_after_run()
        page = getattr(self, "_select_page", None)
        if page is not None and hasattr(page, "show_catalogue_section"):
            page.show_catalogue_section(section)
        self._sync_sp_gui_button_visibility()
        self._nav_to_page(_PAGE_SELECT)

    def _show_install_output(self) -> None:
        """Small live terminal for the install tool. Closing it does not cancel."""
        from .dialogs import InstallConsoleDialog

        dialog = getattr(self, "_install_output_dialog", None)
        if dialog is None:
            dialog = InstallConsoleDialog(self)
            self._install_output_dialog = dialog
            self.log_line_added.connect(dialog.append_line)
        dialog.set_lines(list(getattr(self, "_run_output", [])))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _present_message(self, title: str, body: str) -> None:
        """A short notice in the main window, not a modal dialog."""
        from PySide6.QtWidgets import QLabel, QWidget

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(24, 16, 24, 16)
        # The window header already shows the page title. A second heading
        # here repeated "Check for Updates" above the status line.
        text = QLabel(body)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addStretch(1)
        close = QPushButton(tr("close"))
        close.setCursor(Qt.ArrowCursor)
        close.clicked.connect(self._leave_notice)
        layout.addWidget(close, 0, Qt.AlignLeft)
        self._present_inline(host, title)

    def _present_inline(self, widget, title: str) -> None:
        """Show a former dialog inside the main window."""
        host = getattr(self, "_notice_host", None)
        if host is None or widget is None:
            return
        layout = host.layout()
        while layout.count():
            item = layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.hide()
                child.setParent(None)
        if isinstance(widget, QDialog):
            widget.setWindowFlags(Qt.WindowType.Widget)
            widget.setModal(False)
            try:
                widget.finished.connect(lambda *_: self._leave_notice())
            except Exception:
                pass
        widget.setParent(host)
        layout.addWidget(widget)
        widget.show()
        self._notice_title = title
        self._notice_return = self._stack.currentIndex()
        self._nav_to_page(_PAGE_NOTICE)

    def _leave_notice(self) -> None:
        back = getattr(self, "_notice_return", _PAGE_SELECT)
        if back in (_PAGE_NOTICE, None):
            back = _PAGE_SELECT
        self._nav_to_page(back)

    def _begin_flash_flow(self):
        if not self._package_path:
            return
        if not self._ensure_elevated_for_install():
            return
        if self._needs_linux_device_prep():
            if not self._start_linux_install_prep():
                return
        if not getattr(self, "_fallback_attempted", False):
            self._flash_method = self._preferred_flash_method()
            self._flash_page.set_method(self._flash_method)
            self._sp_1013_seen = False
        self._install_failed = False
        self._power_off_prompt = False
        # The window in which a flash backend may run starts here, with the
        # guidance that asks for the device, and nothing may sweep the tools
        # away until the run reports its result.
        from .. import process_guard

        process_guard.begin_install_session(reason="guided install")
        if self._needs_device_prep() and not getattr(self, "_pre_install_guided", False):
            self._show_device_prep()
            return
        self._begin_run_output()
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

        eff_model = (self._package_model or "").upper()
        pkg_low = (self._package_name or "").lower()
        path_low = (self._package_path or "").lower()
        if "Y1" in eff_model:
            eff_model = "Y1"
        elif "Y2" in eff_model:
            eff_model = "Y2"
        elif "y1" in path_low or "y1" in pkg_low:
            eff_model = "Y1"
        elif "y2" in path_low or "y2" in pkg_low:
            eff_model = "Y2"

        is_360p_rockbox, eff_model = self._rockbox_themes_required()
        self._is_360p_rockbox = is_360p_rockbox

        try:
            from ..sleep_inhibit import acquire_sleep_inhibition
            acquire_sleep_inhibition("Firmware installation in progress")
        except Exception as e:
            logger.debug("Could not acquire sleep inhibition: %s", e)

        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._flash_page.set_method(self._flash_method)
        self._flash_page.show_completion_appeal(False)
        self._apply_release_icon(complete=False)
        self._past_usb_search = False
        self._usb_search_active = False
        self._last_progress = 0
        pre_extracted = completed_extract_dir(self._package_path)
        if pre_extracted:
            # The connect sentence waits for the tool's own search line (see
            # _on_step_changed STEP_WAITING); until then it is only "please wait".
            self._flash_page.show_please_wait()
        else:
            self._flash_page.show_preparing()
            self._flash_page.set_attempt_timeout(0)
        self._nav_to_page(_PAGE_FLASH)
        self._append_log(
            f"Starting flash for {self._package_name} "
            f"(method: {_method_label(self._flash_method)})"
        )
        try:
            from ..flash_service import compute_extract_dir, _find_scatter, _parse_scatter_platform
            from ..sp_flash_gui import update_sp_history_ini

            ed = pre_extracted or compute_extract_dir(self._package_path)
            sc = _find_scatter(Path(ed)) if Path(ed).is_dir() else None
            if sc and hasattr(self._flash_page, "set_platform"):
                platform = _parse_scatter_platform(sc) or ""
                if platform:
                    self._flash_page.set_platform(platform)
            update_sp_history_ini(
                scatter_path=sc,
                extract_dir=Path(ed) if Path(ed).is_dir() else None,
                model=self._package_model,
            )
        except Exception as e:
            logger.debug("Could not pre-update SP history.ini in _begin_flash_flow: %s", e)
        da_file, auth_file = self._flash_inputs()
        self._saw_all_command_exec = False
        self._used_sp_backend = (backend_method(self._flash_method) == METHOD_SP)
        if hasattr(self, "_failure_grace_timer") and self._failure_grace_timer.isActive():
            self._failure_grace_timer.stop()
        self.service.start_flash(
            self._package_path,
            pre_extracted_dir=pre_extracted,
            method=self._flash_method,
            model=self._package_model,
            da_file=da_file,
            auth_file=auth_file,
            is_rockbox=self._is_360p_rockbox,
        )
        self.service.stop_device_monitor()

    def _on_step_changed(self, step):
        if step in (STEP_DETECT, STEP_DONE) or step in _WRITE_STEPS:
            self._past_usb_search = True
            self._usb_search_active = False
        self._step_now = step
        key = _STEP_KEY.get(step, "step_wait")
        self._flash_page.update_step(key)
        self._retry_page.update_step(key)
        self._on_progress(self._last_progress)
        if step == STEP_EXTRACTING:
            self._flash_page.show_preparing()
        elif step == STEP_DETECT:
            self._flash_page.show_please_wait(connecting=True)
        elif step == STEP_PLEASE_WAIT:
            self._usb_search_active = False
            self._last_progress = 0
            if self._past_usb_search or not getattr(self, "_pre_install_guided", False):
                self._flash_page.show_please_wait()
            self._settings_page.set_method_enabled(True)
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step == STEP_WAITING:
            # A handshake retry prints search or hint again after please-wait.
            self._past_usb_search = False
            self._usb_search_active = True
            self._last_progress = 0
            self._flash_page.show_connect_search()
            self._flash_page.highlight_guide_step(1)
            # Still waiting for the device: the method may be switched.
            self._settings_page.set_method_enabled(True)
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step == STEP_DETECT:
            self._flash_page.show_real_stage(tr("step_detect"))
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
        if (
            getattr(self, "_step_now", "") in (STEP_PLEASE_WAIT, STEP_WAITING)
            and not getattr(self, "_past_usb_search", False)
        ):
            # Connect and the pre-search wait stay at 0. Do not paint 10%.
            self._last_progress = 0
            # No ETA either: nothing has been written yet in this run.
            self._worker_eta = ETA_UNKNOWN
            self._flash_page.set_prep_eta_seconds(0)
            return
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
        raw = str(msg or "").strip()
        if not raw or set(raw) <= {'.'}:
            return
        if any(w in raw for w in ("RuntimeWarning", "UserWarning", "DeprecationWarning", "innioasis.startup")):
            return
        if raw.startswith("."):
            stripped = raw.lstrip(". ")
            if not stripped or stripped == "Preloader":
                return
            msg = stripped
        self._log_lines.append(msg)
        if getattr(self, "_run_output_open", False):
            self._run_output.append(msg)
            if len(self._run_output) > 4000:
                self._run_output = self._run_output[-4000:]
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
        text = str(line or "")
        low = text.lower()
        is_sp = bool(getattr(self, "_used_sp_backend", False))
        if is_sp and (
            "all command exec" in low
            or ("all command" in low and "exec" in low)
            or "flash complete" in low
        ):
            self._saw_all_command_exec = True
            if hasattr(self, "_failure_grace_timer") and self._failure_grace_timer.isActive():
                self._failure_grace_timer.stop()
            # If the app has incorrectly declared a failure or disconnection:
            if getattr(self, "_install_failed", False) or self.sm.state in (
                FlashState.S5_USB_DISCONNECTED,
                FlashState.S5_FAILED,
            ):
                logger.info(
                    "SP Flash Tool 'all command exec' / completion detected; "
                    "correcting premature failure/disconnection state to success stage."
                )
                self._install_failed = False
                self._handle_flash_success()
                return

        if any(marker in low for marker in _CACHE_MISSING_MARKERS):
            # A file disappearing from the extracted cache is not a player
            # connection problem. Keep it on the install card so the recovery
            # can re-download/re-extract firmware rather than resetting USB.
            self._set_cache_recovery_package(text)
            return
        kind = classify_backend_line(text)
        if not kind or not self._package_path:
            return  # No run in progress — log only.
        if kind == LINE_CONNECT_HINT:
            self._show_connect_guide_stage_one()
        elif kind == LINE_RETRY:
            if getattr(self, "_past_usb_search", False):
                # mtkclient reopens the port to move the DA to high speed and
                # logs "[Errno 2] Entity not found" between the close and the
                # reopen. The stock Y2 run printed exactly that and then wrote
                # every partition, so a transient errno after the player was
                # seen must not raise the manual-reset dialog over a running
                # flash (Retry there could also start a second run). The
                # worker's own failure path owns recovery from this point.
                return
            self._show_retry_guidance(
                tr("retry_guidance_detail_errno").format(code=text.strip())
            )

    def _set_cache_recovery_package(self, detail=""):
        """Keep the selected release and route the user to local cache recovery."""
        package = Path(getattr(self, "_package_path", "") or "")
        first_report = not getattr(self, "_cache_recovery_needed", False)
        self._cache_recovery_needed = True
        if first_report:
            # Clear the firmware cache now, so the next install re-downloads and
            # re-extracts the package instead of reusing the broken cache.
            try:
                from .. import downloads
                count, _freed = downloads.clear_firmware_cache()
                self._append_log(f"Cleared firmware cache ({count} item(s)) so the next install re-downloads the firmware.")
            except Exception:
                logger.warning("Could not clear firmware cache after a missing-file error", exc_info=True)
        if package.is_dir():
            self._append_log(
                "Selected local firmware directory is incomplete; choose Select Software and "
                "select it again after restoring the missing file."
            )
        else:
            self._append_log("Cached firmware archive is missing; download the firmware again from Select Software.")
        if self._stack.currentIndex() == _PAGE_FLASH:
            self._install_failed = True
            self._power_off_prompt = False
            self._install_complete = False
            from ..flash_service import parse_flash_error
            title, explanation = parse_flash_error("CACHE_FILE_MISSING", self._package_model)
            self._failure_title = title
            self._flash_page.show_install_failure("CACHE_FILE_MISSING", explanation, self._last_progress or 0)
            self._nav_to_page(_PAGE_FLASH)

    def _show_connect_guide_stage_one(self):
        """Return the UI to "power off the device and connect USB".

        Guarded to the waiting states: a hint that arrives once writing has
        started must not yank the user back to step 1 mid-flash.
        """
        if getattr(self, "_past_usb_search", False):
            return
        if self.sm.state not in (
            FlashState.S2_WAIT_CONNECTION,
            FlashState.S3_DEVICE_DETECTED,
        ):
            return
        self._usb_search_active = True
        self._last_progress = 0
        self._step_now = STEP_WAITING
        self._flash_page.show_connect_search()
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

    def _is_sp_complete_override(self) -> bool:
        """True only if an SP Flash Tool run confirmed completion via console."""
        is_sp = bool(getattr(self, "_used_sp_backend", False))
        return bool(is_sp and getattr(self, "_saw_all_command_exec", False))

    def _on_flash_disconnected(self):
        """Handle abrupt USB disconnection during flash, wait 5s safety window before killing tasks."""
        if (
            self.sm.state is FlashState.S5_COMPLETE
            or getattr(self, "_install_complete", False)
            or self._is_sp_complete_override()
        ):
            return
        try:
            self.sm.transition_to(FlashState.S5_USB_DISCONNECTED)
        except ValueError:
            self.sm.force_state(FlashState.S5_USB_DISCONNECTED)
        self._elapsed_timer.stop()
        self.service.stop_device_monitor()

        # 5-second safety window before killing the flashing process
        if not hasattr(self, "_kill_process_timer"):
            self._kill_process_timer = QTimer(self)
            self._kill_process_timer.setSingleShot(True)
            self._kill_process_timer.timeout.connect(lambda: self._kill_flashing_process("usb disconnected"))
        self._kill_process_timer.start(5000)

        try:
            from ..sleep_inhibit import release_sleep_inhibition
            release_sleep_inhibition()
        except Exception:
            pass
        self._install_failed = True
        self._power_off_prompt = False
        self._install_complete = False
        self._failure_title = tr("err_usb_lost_title")
        if hasattr(self, "_package_model") and self._package_model:
            self._flash_page.set_model(self._package_model)
        self._flash_page.show_usb_disconnected(
            on_retry=self._on_power_off_continue,
            on_cancel=self._on_cancel_flash,
        )
        self._nav_to_page(_PAGE_FLASH)
        self._update_header_title()
        self._adjust_window_geometry()

    def _kill_flashing_process(self, reason: str = "usb disconnected"):
        """Terminate the flashing process after the safety window has elapsed."""
        if self._is_sp_complete_override() or getattr(self, "_install_complete", False):
            return
        self.service.cancel_flash()
        from .. import process_guard
        process_guard.end_install_session(reason=reason, sweep=False)
        process_guard.sweep_async(reason=reason, on_done=self.sweep_report.emit)
        self._recover_usb_after_disconnect()

    def _recover_usb_after_disconnect(self):
        """Release the app's USB claims and report what the host can still see.

        A run that dies can leave the player claimed, and on macOS a wedged
        run can take the host's USB controller down with it. Recovery runs off
        the GUI thread; when nothing enumerates at all the install card swaps
        to the power-cycle wording, because replugging the player alone cannot
        bring a dead controller back.
        """
        import threading

        from .. import usb_recovery

        def _run():
            try:
                report = usb_recovery.recover_usb_connection(reap_helpers=False)
            except Exception as e:  # pragma: no cover - recovery never breaks the failure screen
                logger.debug("USB recovery failed: %s", e)
                return
            try:
                self.usb_recovery_report.emit(report)
            except RuntimeError:  # pragma: no cover - window closed mid-recovery
                pass

        threading.Thread(target=_run, name="usb-recovery", daemon=True).start()

    def _on_usb_recovery_report(self, report) -> None:
        """Log what the host sees after a disconnect; ask for a power cycle if dead."""
        if report is None:
            return
        self._append_log(
            f"USB recovery: {report.state} "
            f"({report.total_devices} USB device(s), {report.mtk_devices} MediaTek)"
        )
        if getattr(report, "bus_down", False):
            self._show_usb_bus_down()

    def _show_usb_bus_down(self):
        """The host has no USB devices at all: ask for a power cycle, not a replug."""
        if self.sm.state is not FlashState.S5_USB_DISCONNECTED:
            return
        self._flash_page.show_usb_disconnected(
            on_retry=self._on_power_off_continue,
            on_cancel=self._on_cancel_flash,
            prompt_key="err_usb_bus_down_recovery",
        )

    def _on_device_lost(self):
        # A player that vanishes *after* the app has declared the flash
        # successful is expected behaviour — it reboots into the software just
        # written, and it disappears again while the guided themes/fonts
        # install talks to it over ADB. Detection belongs to a run in
        # progress: once success is recorded, the completion screen, the
        # donation prompt and the guided themes install must not be
        # interrupted by it. Starting the next run arms detection again.
        if (
            self.sm.state is FlashState.S5_COMPLETE
            or getattr(self, "_install_complete", False)
            or getattr(self, "_rockbox_themes_flow_active", False)
            or self._is_sp_complete_override()
            or getattr(self, "_themes_stage_done", False)
        ):
            return
        if self.sm.state is FlashState.S4_FLASHING or getattr(self, "_past_usb_search", False):
            self._on_flash_disconnected()
        elif self.sm.state in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            try:
                self.sm.transition_to(FlashState.S2_WAIT_CONNECTION)
            except ValueError:
                pass
            if getattr(self, "_usb_search_active", False) and not getattr(self, "_past_usb_search", False):
                self._flash_page.show_connect_search()

    def _on_monitor_error(self, msg):
        self._append_log(msg)

    def _on_flash_finished(self, ok, error_code):
        self._elapsed_timer.stop()
        if hasattr(self, "_kill_process_timer") and self._kill_process_timer.isActive():
            self._kill_process_timer.stop()

        if self._is_sp_complete_override():
            self._append_log("Notice: Flash finished handler confirmed 'all command exec' — classing as success.")
            ok = True
            error_code = ""

        if not ok:
            self._rockbox_themes_flow_active = False
            self._rockbox_power_on_pending = False
            self._rockbox_power_on_model = ""
        # Success and failure end the install window the same way: every
        # backend this app started goes now, in the background so the result
        # (or a retry) is never held up by process cleanup.
        from .. import process_guard

        process_guard.end_install_session(reason="run finished", sweep=False)
        # The sweep runs on its own thread, so its report comes back through a
        # signal: appending to the log touches widgets and belongs on the GUI
        # thread.
        process_guard.sweep_async(
            reason="install finished" if ok else "install failed",
            on_done=self.sweep_report.emit,
        )
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
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.dismiss_prompt_stop()
        try:
            self.sm.transition_to(FlashState.S5_COMPLETE)
        except ValueError:
            self.sm.force_state(FlashState.S5_COMPLETE)

        model = self._package_model or ""
        software = self._package_name or "Firmware"
        steps = install_power_on_steps(model)

        # Record install for device tracking & future release reminders
        rel_info = getattr(self._select_page, "current_installed_release_info", lambda: None)()
        # Online catalogue installs only. A local file has no release tag, so
        # it is not tracked and cannot drive an update prompt.
        if rel_info and (rel_info.get("tag_name") or "").strip():
            latest_tag = ""
            if hasattr(self._select_page, "catalogue_latest_tag"):
                latest_tag = self._select_page.catalogue_latest_tag()
            device_tracking.record_device_install(
                model=rel_info.get("model") or model,
                software_name=rel_info.get("software_name") or software,
                tag_name=rel_info.get("tag_name") or "",
                release_label=rel_info.get("release_label") or "",
                package_slug=rel_info.get("package_slug") or "",
                published_at=rel_info.get("published_at") or "",
                settings=self.settings,
                catalogue_latest=latest_tag,
            )
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()

        donation_disabled = device_tracking.is_donation_install_prompt_disabled(self.settings)
        eff_model = (model or self._package_model or "").upper()
        pkg_low = (self._package_name or "").lower()
        path_low = (self._package_path or "").lower()
        if "Y1" in eff_model:
            eff_model = "Y1"
        elif "Y2" in eff_model:
            eff_model = "Y2"
        elif "y1" in path_low or "y1" in pkg_low:
            eff_model = "Y1"
        elif "y2" in path_low or "y2" in pkg_low:
            eff_model = "Y2"

        is_360p_rockbox, eff_model = self._rockbox_themes_required()
        pkg_low = (self._package_name or "").lower()
        path_low = (self._package_path or "").lower()
        is_target_rockbox = "rockbox" in pkg_low or "rockbox" in path_low
        is_240p_rockbox = self._rockbox_240p_detected()

        self._is_360p_rockbox = is_360p_rockbox
        self._is_240p_rockbox = is_240p_rockbox
        self._rockbox_power_on_pending = False
        self._rockbox_power_on_model = ""
        self._download_active = False

        if is_360p_rockbox or (is_target_rockbox and not is_240p_rockbox and eff_model in ("Y1", "Y2")):
            self._nav_to_page(_PAGE_FLASH)
            self._rockbox_themes_flow_active = True
            is_sp_win = paths.IS_WINDOWS and (
                getattr(self, "_used_sp_backend", False)
                or backend_method(getattr(self, "_flash_method", "")) == METHOD_SP
            )
            if is_sp_win:
                self._append_log(
                    "Notice: SP Flash Tool on Windows detected — starting automated Rockbox themes flow "
                    "(waiting 90-120s for ADB connection, no user interaction required)..."
                )
                self._start_themes_stage(eff_model, donation_disabled, already_powered_on=True, automatic=True)
            else:
                self._show_rockbox_power_on_prompt(eff_model)
        else:
            try:
                from ..sleep_inhibit import release_sleep_inhibition
                release_sleep_inhibition()
            except Exception:
                pass
            self._install_complete = True
            self._compact_install_screen = False
            self._power_off_prompt = False
            self._prep_active = False
            self._download_active = False
            self._sync_install_nav_entry()
            self._flash_page.show_flashing()
            self._flash_page.set_device_done()
            self._flash_page.set_rockbox_site_themes(is_240p_rockbox)
            self._flash_page.show_completion_appeal(False)
            self._apply_release_icon(complete=True)
            self._nav_to_page(_PAGE_FLASH)
            if not donation_disabled:
                self._flash_page.show_completion_appeal(
                    True, self._on_completion_dont_ask, self._on_completion_not_now,
                )
            self._adjust_window_geometry()
            self._show_status(
                tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
            )

    def _create_themes_wizard(self, model: str):
        """Factory seam for the 360p themes stage (tests stub the dialog)."""
        from ..theme_pack import Rockbox360pSetupWizard

        return Rockbox360pSetupWizard(parent=self, model=model)

    def _start_themes_stage(
        self,
        model: str,
        donation_disabled: bool,
        *,
        already_powered_on: bool = False,
        automatic: bool = False,
    ) -> None:
        """Open the themes install stage after a 360p Rockbox install."""
        self._themes_stage_done = False
        self._rockbox_power_on_pending = False
        self._rockbox_power_on_model = ""
        self._rockbox_themes_flow_active = True
        self._sync_install_nav_entry()
        self._append_log(f"Starting Rockbox 360p themes & fonts post-install stage for {model}...")
        # Check if caller or test suite stubbed _create_themes_wizard
        try:
            is_custom = self._create_themes_wizard.__code__ != MainWindow._create_themes_wizard.__code__
        except Exception:
            is_custom = False

        if is_custom:
            try:
                dlg = self._create_themes_wizard(model)
                if dlg is not None:
                    dlg.installed_success.connect(lambda: self._finish_themes_stage(donation_disabled))
                    dlg.finished.connect(lambda _result: self._finish_themes_stage(donation_disabled))
                    dlg.show()
                    try:
                        dlg.raise_()
                        dlg.activateWindow()
                    except Exception:
                        pass
                    return
            except Exception:
                logger.debug("Custom themes wizard could not open", exc_info=True)

        self._flash_page.start_rockbox_themes_stage(
            model=model,
            on_done=lambda: self._finish_themes_stage(donation_disabled),
            on_recover=self._on_progress_retry,
            log_cb=self._append_log,
            already_powered_on=already_powered_on,
            automatic=automatic,
        )
        self._adjust_window_geometry()

    def _finish_themes_stage(self, donation_disabled: bool) -> None:
        if getattr(self, "_themes_stage_done", False):
            return
        self._themes_stage_done = True
        self._rockbox_themes_flow_active = False
        self._rockbox_power_on_pending = False
        self._rockbox_power_on_model = ""
        self._sync_install_nav_entry()
        try:
            from ..sleep_inhibit import release_sleep_inhibition
            release_sleep_inhibition()
        except Exception:
            pass
        self._install_complete = True
        self._compact_install_screen = False
        self._power_off_prompt = False
        self._prep_active = False
        self._download_active = False
        self._sync_install_nav_entry()
        self._flash_page.show_flashing()
        self._flash_page.set_device_done()
        self._apply_release_icon(complete=True)
        self._nav_to_page(_PAGE_FLASH)
        if not donation_disabled:
            self._flash_page.show_completion_appeal(
                True, self._on_completion_dont_ask, self._on_completion_not_now,
            )
        self._adjust_window_geometry()
        model = self._package_model or ""
        software = self._package_name or "Firmware"
        steps = install_power_on_steps(model)
        self._show_status(
            tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
        )

    def _ensure_theme_sync(self) -> None:
        """Start the passive 360p theme sync (self-gating; idle when untracked)."""
        if getattr(self, "_theme_sync_service", None) is not None:
            return
        try:
            from ..theme_sync import ThemeSyncService

            service = ThemeSyncService(self.settings, parent=self)
            self._theme_sync_service = service
            service.start()
        except Exception:
            logger.debug("Passive theme sync could not start", exc_info=True)

    def _show_install_failure(self, message: str):
        """Failure stays on the install progress card. Retry is where Cancel was."""
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.dismiss_prompt_stop()
        self._rockbox_themes_flow_active = False
        self._rockbox_power_on_pending = False
        raw = str(message or "")
        low = raw.lower()
        if message == "CACHE_FILE_MISSING" or any(marker in low for marker in _CACHE_MISSING_MARKERS):
            self._set_cache_recovery_package(raw)
            message = "CACHE_FILE_MISSING"
        self._install_failed = True
        self._power_off_prompt = False
        self._install_complete = False
        self._prep_active = False
        self._download_active = False
        self._compact_install_screen = False
        from ..flash_service import parse_flash_error
        parsed_title, explanation = parse_flash_error(message, getattr(self, "_package_model", ""))
        self._failure_title = parsed_title
        self._flash_page.show_install_failure(message, explanation, self._last_progress or 0)
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_FLASH)
        self._update_header_title()

    def _handle_flash_failure(self, error_code):
        try:
            from ..sleep_inhibit import release_sleep_inhibition
            release_sleep_inhibition()
        except Exception:
            pass
        self._download_active = False
        from ..flash_service import is_connect_failure, is_disconnection_error, is_mtk_device_connected
        cache_failure = bool(getattr(self, "_cache_recovery_needed", False)) or any(
            marker in str(error_code or "").lower() for marker in _CACHE_MISSING_MARKERS
        ) or str(error_code or "") == "CACHE_FILE_MISSING"
        is_sp_tool_error = str(error_code or "").startswith("SP_") or "S_" in str(error_code or "")
        if not cache_failure and not is_sp_tool_error and (
            error_code == "USB_DISCONNECTED"
            or is_disconnection_error(error_code)
            or (
                getattr(self, "_past_usb_search", False)
                and not is_mtk_device_connected()
                and not is_connect_failure(error_code)
            )
        ):
            self._append_log(f"Flash failed due to device disconnection ({error_code}).")
            self._on_flash_disconnected()
            return
        # A backend that never got going is not the end of the install on a
        # host that has the other engine: hand the user back to the power-off
        # step and start the other one from there.
        if not cache_failure and self._offer_backend_failover(error_code):
            return
        if not cache_failure and getattr(self.service, "_mtk_stall_prompt_text", lambda: None)():
            self._show_mtk_stall_restart_prompt()
            return

        need_reboot_suggestion = not cache_failure and (
            getattr(self, "_sp_1013_seen", False)
            or (getattr(self, "_fallback_attempted", False) and is_com_port_error(error_code))
        )
        if need_reboot_suggestion:
            self._show_com_port_reboot_suggestion(error_code)
            return

        try:
            self.sm.transition_to(FlashState.S5_FAILED)
        except ValueError:
            self.sm.force_state(FlashState.S5_FAILED)
        self._show_install_failure(
            "CACHE_FILE_MISSING" if cache_failure else str(error_code or tr("install_failed"))
        )

    # ------------------------------------------------------------------
    # Backend failover
    # ------------------------------------------------------------------
    def _preferred_flash_method(self) -> str:
        """The user's preferred flash method from settings, or default."""
        pref = normalise_method(self.settings.value("flash_method", ""))
        return backend_method(pref)

    def _other_method(self, method) -> str:
        """The other backend for ``method``, or "" when there is not one.

        Only hosts that ship both engines can switch: macOS has no SP Flash
        Tool at all. This is about the guided in-app flow only — the terminal
        launcher and the SP Flash Tool GUI handoff are deliberately left to
        run the tool themselves.
        """
        if paths.IS_MAC:
            return ""
        current = backend_method(method)
        if current == METHOD_SP:
            # MTKClient on Windows is hidden until the M key reveals it, so
            # SP Flash Tool has no second engine there and retries itself.
            if paths.IS_WINDOWS and not device_tracking.hidden_mtk_options_enabled():
                return ""
            return METHOD_MTK
        if current == METHOD_MTK:
            return METHOD_SP
        return ""

    def _return_to_power_off_stage(
        self, method="", recovery=False, prompt_key="", prompt_kwargs=None
    ) -> None:
        """Re-arm the disconnect / power-off step; Continue starts ``method``.

        A backend's command must not start hunting for the player until the
        user has confirmed it is unplugged and powered off. SP Flash Tool's
        console search expires on its own two-minute window, so a command
        started too early reports S_TIMEOUT while nothing was actually wrong.

        ``recovery`` is set for the automated switch to the other engine: that
        attempt left the player mid-download, so the step spells out how to
        put it back into flash mode instead of the plain power-off line.
        """
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
        if method:
            # Session-scoped: the saved preference stays whatever the user
            # chose, so the next install starts from their pick again.
            self._flash_method = backend_method(method)
            self._flash_page.set_method(self._flash_method)
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._apply_release_icon(complete=False)
        if recovery:
            # The step that follows the automatic switch shows the failed card
            # rather than a plain prompt: the band keeps saying the install
            # failed, and the line says how to get the player back into flash
            # mode. It is the same card the USB-drop path shows, so both read
            # alike, and the pin tip stays off because that line names the
            # hidden button itself.
            self._flash_page.show_usb_disconnected(
                on_retry=self._on_power_off_continue,
                on_cancel=self._on_cancel_flash,
                prompt_key=prompt_key,
                prompt_kwargs=prompt_kwargs,
            )
        else:
            self._flash_page.show_power_off_prompt()
        self._nav_to_page(_PAGE_FLASH)

    def _offer_backend_failover(self, error_code) -> bool:
        """Send the user back to the power-off step to try the other backend.

        Applies to guided in-app installs on hosts with both engines. A
        wrong-model result is never retried (no backend can fix it), and only
        one switch is made per install, so an exhausted pair of engines ends
        as an explained failure instead of a loop.
        """
        if getattr(self, "_fallback_attempted", False):
            return False
        if is_wrong_model_error(error_code) or is_sp_driver_error(error_code):
            return False
        candidate = self._other_method(self._flash_method)
        if not candidate:
            return False
        self._fallback_attempted = True
        if is_com_port_error(error_code):
            self._sp_1013_seen = True
        # Diagnostics only: the user should not have to read backend names.
        logger.info(
            "Install attempt with %s did not start (%s); offering %s",
            backend_method(self._flash_method),
            error_code,
            candidate,
        )
        # The attempt never reached the player, so the step asks for the
        # paperclip reset and names both engines rather than a power-cycle.
        self._return_to_power_off_stage(
            candidate,
            recovery=True,
            prompt_key="err_connect_failover_recovery",
            prompt_kwargs={
                "failed": _method_label(backend_method(self._flash_method)),
                "next": _method_label(candidate),
            },
        )
        return True

    def _on_progress_retry(self):
        """Retry an install failure, using firmware recovery rather than USB reset when needed."""
        if getattr(self, "_cache_recovery_needed", False):
            self.service.cancel_flash()
            self.service.stop_device_monitor()
            self._elapsed_timer.stop()
            self._install_failed = False
            self._failure_title = ""
            self._flash_page.clear_install_failure()
            self._power_off_prompt = False
            self._pre_install_guided = False
            if self.sm.state in (
                FlashState.S5_FAILED,
                FlashState.S5_USB_DISCONNECTED,
                FlashState.S4_FLASHING,
                FlashState.S5_COMPLETE,
            ):
                self.sm.reset_for_retry()
            self._cache_recovery_needed = False
            if getattr(self._select_page, "_current_selected_rel", None) is None:
                self._return_to_select_software()
            else:
                self._select_page.retry_missing_cached_firmware()
            return
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._elapsed_timer.stop()
        self._install_failed = False
        self._failure_title = ""
        self._flash_page.clear_install_failure()
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
        self._apply_release_icon(complete=False)
        self._flash_page.show_power_off_prompt(show_pin_tip=True)
        self._nav_to_page(_PAGE_FLASH)

    def _on_power_off_continue(self):
        """The user confirmed the device is off and unplugged. Start the run."""
        if getattr(self, "_linux_permission_pending", False):
            self._on_linux_permission_next()
            return
        if getattr(self, "_gui_handoff_ready", False):
            self._complete_gui_handoff()
            return
        if self.sm.state in (
            FlashState.S5_FAILED,
            FlashState.S5_USB_DISCONNECTED,
            FlashState.S4_FLASHING,
            FlashState.S5_COMPLETE,
        ):
            self.sm.reset_for_retry()
        self._install_failed = False
        self._failure_title = ""
        self._flash_page.clear_install_failure()
        self._power_off_prompt = False
        self._pre_install_guided = True
        self._flash_page.leave_power_off_prompt()
        self._begin_flash_flow()

    def _on_retry_flash(self):
        self.sm.reset_for_retry()
        self._fallback_attempted = False
        self._sp_1013_seen = False
        self._flash_method = self._preferred_flash_method()
        self._flash_page.set_method(self._flash_method)
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
        self._return_to_select_software()

    def _show_mtk_stall_restart_prompt(self):
        """Show the restart-and-quit prompt after an MTKClient output stall and quit on OK."""
        text = getattr(self.service, "_mtk_stall_prompt_text", lambda: None)()
        if not text:
            return
        title = text.get("title", tr("prompt_restart_and_quit_title"))
        desc = text.get("desc", tr("prompt_restart_and_quit_desc"))
        prompt = tr("prompt_restart_and_quit_ok")
        from PySide6.QtWidgets import QMessageBox
        dialog = QMessageBox(
            QMessageBox.Information,
            title,
            desc,
            QMessageBox.Ok,
            self,
        )
        dialog.setWindowTitle(title)
        dialog.setText(desc)
        dialog.setInformativeText("")
        dialog.addButton(prompt, QMessageBox.AcceptRole)
        dialog.setDefaultButton(QMessageBox.Ok)
        if dialog.exec() == QMessageBox.Ok:
            self.service.cancel_flash()
            self.service.stop_device_monitor()
            from .. import process_guard
            for _ in process_guard.end_install_session(reason="restart prompt accepted"):
                pass
            try:
                from ..sleep_inhibit import release_sleep_inhibition
                release_sleep_inhibition()
            except Exception:
                pass
            QApplication.quit()

    def _show_com_port_reboot_suggestion(self, error_code):
        """Ask the user to restart their computer after a COM port (1013) failure."""
        self._reboot_suggestion_shown = True
        try:
            self.sm.transition_to(FlashState.S5_FAILED)
        except ValueError:
            self.sm.force_state(FlashState.S5_FAILED)
        display_code = (
            "S_COM_PORT_OPEN_FAIL (1013)"
            if (is_com_port_error(error_code) or getattr(self, "_sp_1013_seen", False))
            else str(error_code or tr("install_failed"))
        )
        self._show_install_failure(display_code)
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("INNIOASIS_HEADLESS"):
            return
        title = tr("prompt_restart_and_quit_title")
        desc = tr("err_com_port_reboot_suggestion")
        from PySide6.QtWidgets import QMessageBox

        dialog = QMessageBox(
            QMessageBox.Icon.Information,
            title,
            desc,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            self,
        )
        dialog.setWindowTitle(title)
        dialog.setText(desc)
        dialog.setInformativeText("")
        dialog.setButtonText(QMessageBox.StandardButton.Ok, tr("prompt_restart_and_quit_ok"))
        dialog.setButtonText(QMessageBox.StandardButton.Cancel, tr("cancel"))
        dialog.setDefaultButton(QMessageBox.StandardButton.Ok)
        if dialog.exec() == QMessageBox.StandardButton.Ok:
            self.service.cancel_flash()
            self.service.stop_device_monitor()
            from .. import process_guard

            for _ in process_guard.end_install_session(reason="restart computer suggestion accepted"):
                pass
            try:
                from ..sleep_inhibit import release_sleep_inhibition

                release_sleep_inhibition()
            except Exception:
                pass
            QApplication.quit()

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
        if self.sm.state not in (FlashState.S2_WAIT_CONNECTION, FlashState.S3_DEVICE_DETECTED):
            logger.info("Install method set to %s for the next run", backend_method(method))
            return
        if method in ("sp", "auto"):
            try:
                from ..sp_flash_gui import update_sp_history_ini
                update_sp_history_ini(model=self._package_model)
            except Exception as e:
                logger.debug("Could not update SP history.ini on method change: %s", e)
        # Kept silent for the user: the app is a front end for both engines and
        # the install flow should read the same either way. The restart is a
        # diagnostic detail, and the new command must not start until the user
        # has again confirmed the player is unplugged and powered off.
        logger.info("Install method changed to %s", backend_method(method))
        self._return_to_power_off_stage(method)

    def _stop_running_install(self) -> None:
        """Stop a download or flash so it does not keep running in the background."""
        self._download_active = False
        try:
            from ..sleep_inhibit import release_sleep_inhibition
            release_sleep_inhibition()
        except Exception:
            pass
        if hasattr(self, "_flash_page") and hasattr(self._flash_page, "stop_theme_workers"):
            self._flash_page.stop_theme_workers()
        page = getattr(self, "_select_page", None)
        if page is not None:
            # Hand the worker to the page's retirement set rather than clearing
            # the reference: cancellation is asynchronous, and a QThread object
            # destroyed while its thread still runs aborts the process.
            cancel = getattr(page, "cancel_download", None)
            if callable(cancel):
                cancel()
            worker = getattr(page, "_download_worker", None)
            if worker is not None:
                try:
                    worker.cancel()
                except Exception:
                    pass
                self._retire_worker(worker)
                page._download_worker = None
            bar = getattr(page, "_download_bar", None)
            if bar is not None:
                bar.setVisible(False)
            button = getattr(page, "_install_btn", None)
            if button is not None:
                button.setEnabled(True)
        service = getattr(self, "service", None)
        if service is not None:
            for name in ("cancel_extract", "cancel_flash", "stop_device_monitor"):
                fn = getattr(service, name, None)
                if not callable(fn):
                    continue
                try:
                    fn()
                except Exception:
                    pass
        timer = getattr(self, "_elapsed_timer", None)
        if timer is not None:
            timer.stop()

    def _return_to_select_software(self) -> None:
        """Stop the run and select Select Software. Install Software does not stay selected."""
        if getattr(self, "_bundled_firmware_run", False):
            # ROM-bundled portable run: X / cancel quits the app instead.
            self._quit_after_bundled_run()
            return
        if getattr(self, "_returning_to_select", False):
            return
        self._returning_to_select = True
        try:
            self._stop_running_install()
            self._reset_after_run()
        finally:
            self._returning_to_select = False

    def _quit_after_bundled_run(self) -> None:
        """Stop the ROM-bundled run and close the app. The stop prompt has already been answered."""
        self._stop_running_install()
        self._force_quit = True
        self.close()

    def _on_cancel_flash(self):
        # Only the ✕ (confirm stop) button lands here, via the stop-check
        # callback. The ← (back) button only dismisses the check inside the
        # progress card so the run keeps going — it must never navigate away.
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.dismiss_prompt_stop()
        self._return_to_select_software()

    def _on_completion_not_now(self):
        """Dismiss the appeal and return to Select Software. Donations stay on."""
        self._on_install_done()

    def _on_completion_dont_ask(self):
        """Hide donations, then the compact completion screen without the appeal."""
        device_tracking.set_donation_ui_disabled(True, self.settings)
        device_tracking.set_donation_install_prompt_disabled(True, self.settings)
        self._flash_page.show_completion_appeal(False)
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()
        self._apply_donation_visibility(True)
        self._highlight_nav(_PAGE_FLASH, True)
        self._adjust_window_geometry()

    def _apply_release_icon(self, complete: bool = False) -> None:
        """Squircle until a release icon is cached, then that image. The check overlays either."""
        from .release_icon import load_release_pixmap
        from ..release_icons import icon_candidate_urls
        from ..config import is_mediatek_installer

        page = getattr(self, "_select_page", None)
        release = getattr(page, "_current_selected_rel", None) if page else None
        package = getattr(page, "_details_package", None) if page else None
        # A downloaded release lands in the working folder as an extracted
        # tree containing a scatter file, which is also what a hand-browsed
        # zip looks like. Only the *absence* of a catalogue release (or a
        # deliberately local browse) means the generic squircle: otherwise an
        # online release would lose its own updater.jpg the moment it was
        # unpacked, and the install card showed a placeholder instead.
        is_local = (
            release is None
            or is_mediatek_installer()
            or (page is not None and getattr(page, "catalogue_section", lambda: "")() == "local")
        )
        dark = bool(is_dark())
        if is_local:
            from ..release_icons import load_local_package_icon_bytes
            from .release_icon import pixmap_from_icon_bytes

            pkg_path = getattr(self, "_package_path", None)
            local_bytes = load_local_package_icon_bytes(pkg_path, dark=dark) if pkg_path else None
            pixmap = pixmap_from_icon_bytes(local_bytes) if local_bytes else QPixmap()
            self._flash_page.set_release_icon(pixmap, complete=complete)
            return

        pixmap = page.release_pixmap() if page is not None and hasattr(page, "release_pixmap") else None
        if pixmap is None or pixmap.isNull():
            pixmap = load_release_pixmap(release, package, allow_network=False, dark=dark, package_path=getattr(self, "_package_path", None))
        self._flash_page.set_release_icon(pixmap, complete=complete)
        if not icon_candidate_urls(release, package, dark=dark):
            return

        class _Loader(QThread):
            loaded = Signal(object)

            def __init__(self, rel, pkg, use_dark):
                super().__init__()
                self._rel = rel
                self._pkg = pkg
                self._dark = use_dark

            def run(self):
                self.loaded.emit(load_release_pixmap(
                    self._rel, self._pkg, allow_network=True, dark=self._dark,
                ))

        previous = getattr(self, "_icon_loader", None)
        if self._worker_running(previous):
            previous.requestInterruption()
            # The loader cannot stop until its network fetch returns, and
            # replacing the reference below used to drop the last one while the
            # thread was still running. Qt aborts the process for that
            # ("QThread: Destroyed while thread is still running"), which is
            # what a click that repaints the icon twice in a row — the SP Flash
            # Tool launcher, an install start — crashed on.
            self._retire_worker(previous)
        loader = _Loader(release, package, dark)
        loader.loaded.connect(lambda pix, done=complete: self._flash_page.set_release_icon(pix, complete=done or getattr(self, "_install_complete", False)))
        loader.finished.connect(loader.deleteLater)
        self._icon_loader = loader
        loader.start()

    def _reset_after_run(self):
        self._pre_install_guided = False
        self._rockbox_power_on_pending = False
        self._rockbox_power_on_model = ""
        self._rockbox_themes_flow_active = False
        self._install_complete = False
        self._install_failed = False
        self._power_off_prompt = False
        self._compact_install_screen = False
        self._download_active = False
        self._prep_active = False
        self._cache_recovery_needed = False
        self._fallback_attempted = False
        self._sp_1013_seen = False
        self._flash_method = self._preferred_flash_method()
        self._flash_page.set_method(self._flash_method)
        self._flash_page.set_release_icon(QPixmap(), complete=False)
        self.sm.reset_full()
        self._package_path = ""
        self._package_name = ""
        self._flash_start_ts = 0.0
        self._settings_page.set_method_enabled(True)
        self._rockbox_power_on_pending = False
        self._rockbox_power_on_model = ""
        self.service.stop_device_monitor()
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_SELECT)

    def _tick_elapsed(self):
        self._flash_page.update_time(self._elapsed_text(), self._eta_text())

    def _elapsed_text(self):
        secs = int(time.time() - self._flash_start_ts) if self._flash_start_ts else 0
        return f"{secs // 60:02d}:{secs % 60:02d}"

    def _on_worker_eta(self, seconds):
        """The write ETA is measured in seconds; -1 while it is not known."""
        try:
            value = int(seconds)
            self._worker_eta = value if value > 0 else ETA_UNKNOWN
        except (TypeError, ValueError):
            self._worker_eta = ETA_UNKNOWN
        self._tick_elapsed()

    def _eta_text(self):
        """Show only the ETA measured from this run's actual write throughput.

        Progress percentage is not a reliable clock: a single large partition
        can dominate the transfer. Until the backend has enough byte samples to
        measure a rate, keep ETA unknown rather than extrapolating from percent.
        """
        measured = getattr(self, "_worker_eta", ETA_UNKNOWN)
        if measured <= 0:
            return "--:--"
        return f"{measured // 60:02d}:{measured % 60:02d}"

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

    def _lock_diagnostics(self) -> None:
        """Hide Diagnostics button and leave the page if open."""
        self._diagnostics_session = False
        self._sync_diagnostics_button()
        if hasattr(self, "_stack") and self._stack.currentIndex() == _PAGE_DIAGNOSTICS:
            self._leave_diagnostics()

    def toggle_diagnostics_and_advanced(self) -> None:
        """Toggle advanced settings and diagnostics view on or off (shortcut D)."""
        is_active = self._diagnostics_unlocked() or self._settings_page.advanced_methods_revealed()
        if is_active:
            self._lock_diagnostics()
            self._settings_page.hide_advanced_methods()
            try:
                from ..device_tracking import set_hidden_mtk_options

                set_hidden_mtk_options(False)
            except Exception:
                pass
            self._append_log("Advanced install methods and diagnostics hidden (D).")
        else:
            self._unlock_diagnostics()
            self._settings_page.reveal_advanced_methods()
            try:
                from ..device_tracking import set_hidden_mtk_options

                set_hidden_mtk_options(True)
            except Exception:
                pass
            self._nav_to_page(_PAGE_SETTINGS)
            self._append_log("Advanced install methods and diagnostics revealed (D).")

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
        from .scrollbars import apply_native_scrolling
        apply_native_scrolling(page)
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

    def _sync_settings_nav_label(self) -> None:
        """The Settings row names the setup stage while that stage is open."""
        button = getattr(self, "_settings_btn", None)
        if button is None:
            return
        button.setText(
            tr("nav_linux_setup")
            if getattr(self, "_linux_setup_flow_active", False)
            else tr("nav_settings")
        )
        self._reapply_rail_width()

    def _show_full_settings(self):
        """Open the complete Settings column and highlight its navigation row."""
        if self._install_run_active():
            # Settings stays closed while an install, device prep or themes
            # stage owns the window; the install card is the only screen.
            self._nav_to_page(_PAGE_FLASH)
            return
        page = getattr(self, "_settings_page", None)
        self._linux_setup_flow_active = False
        self._sync_settings_nav_label()
        if page is not None and page.focused_card() is not None:
            page.clear_card_focus()
        self._nav_to_page(_PAGE_SETTINGS)
        if hasattr(self, "_linux_setup_btn"):
            setup_done = self.settings.value("linux_setup_completed", False, type=bool)
            self._linux_setup_btn.setVisible(not setup_done)
            self._linux_setup_btn.setEnabled(not setup_done)
        self._sync_sp_gui_button_visibility()

    def _show_linux_setup(self):
        """Show the Linux preparation card inside Settings, by itself."""
        if self._install_run_active():
            # A preparation card is Settings: not reachable mid-run.
            self._nav_to_page(_PAGE_FLASH)
            return
        self._nav_to_page(_PAGE_SETTINGS)
        page = getattr(self, "_settings_page", None)
        if page is None or not hasattr(page, "start_sp_flash_prep"):
            return
        self._linux_setup_flow_active = True
        self._sync_settings_nav_label()
        page.show_card_alone(page._checker_card)
        self._sync_sp_gui_button_visibility()
        if not page.start_sp_flash_prep():
            self._linux_setup_flow_active = False
            page.clear_card_focus()
            self._highlight_nav(_PAGE_SETTINGS, self._install_run_active())
            self._sync_sp_gui_button_visibility()
            return
        # Completion can be synchronous in tests or on a warm, local stage.
        if self._stack.currentIndex() == _PAGE_SETTINGS:
            self._highlight_nav(_PAGE_SETTINGS, self._install_run_active())
            self._adjust_window_geometry()

    def _on_linux_prep_completed(self) -> None:
        """Return to software selection and retire the one-time setup shortcut."""
        if not getattr(self, "_linux_setup_flow_active", False) or self._install_run_active():
            return
        self._linux_setup_flow_active = False
        self._sync_settings_nav_label()
        self.settings.setValue("linux_setup_completed", True)
        setup_btn = getattr(self, "_linux_setup_btn", None)
        if setup_btn is not None:
            setup_btn.setVisible(False)
            setup_btn.setEnabled(False)
        if self._settings_page.focused_card() is not None:
            self._settings_page.clear_card_focus()
        self._nav_to_page(_PAGE_SELECT)

    def _sync_sp_gui_button_visibility(self) -> bool:
        """Expose the GUI launcher only for a selected or retained usable package."""
        btn = getattr(self, "_sp_flash_tool_btn", None)
        if btn is None:
            return False
        if self._install_run_active():
            btn.setVisible(False)
            btn.setEnabled(False)
            return False
        handoff = bool(
            getattr(self, "_gui_handoff_prepare", False)
            or getattr(self, "_gui_handoff_ready", False)
        )
        page = getattr(self, "_select_page", None)
        target = page.gui_firmware_target() if page is not None and hasattr(page, "gui_firmware_target") else None
        available = bool(handoff or target)
        if getattr(self, "_gui_launch_in_flight", False):
            # One request is already in flight: nothing to offer until the tool
            # is running, and re-offering it here would launch it twice.
            btn.setVisible(False)
            btn.setEnabled(False)
            self._sync_settings_sp_gui_visibility(available=False)
            return False
        if target and not handoff:
            page._gui_previous_identity = page._release_identity(target)
            self._gui_current_identity = page._gui_previous_identity
        elif not target:
            self._gui_current_identity = None
        settings_page = getattr(self, "_settings_page", None)
        setup_flow = bool(
            getattr(self, "_linux_setup_flow_active", False)
            and settings_page is not None
            and getattr(self, "_stack", None) is not None
            and self._stack.currentIndex() == _PAGE_SETTINGS
        )
        btn.setVisible(available and not setup_flow)
        btn.setEnabled(available and not setup_flow)
        self._sync_settings_sp_gui_visibility(available=available)
        return available

    def _sync_settings_sp_gui_visibility(self, *, available=None) -> None:
        page = getattr(self, "_select_page", None)
        if available is None:
            available = bool(page is not None and hasattr(page, "gui_firmware_target") and page.gui_firmware_target())
        settings = getattr(self, "_settings_page", None)
        if settings is not None and hasattr(settings, "set_sp_gui_available"):
            settings.set_sp_gui_available(bool(available))

    def _reveal_sp_gui_button(self) -> None:
        """Keep the sidebar launcher available only for a ready handoff."""
        if self._install_run_active():
            return
        if self._sync_sp_gui_button_visibility() and (
            getattr(self, "_gui_handoff_prepare", False)
            or getattr(self, "_gui_handoff_ready", False)
        ):
            output = getattr(self, "_install_output_btn", None)
            if output is not None:
                output.setVisible(False)

    def _on_gui_handoff_abandoned(self):
        self._gui_handoff_prepare = False
        self._gui_handoff_ready = False
        self._gui_launch_when_ready = False
        self._gui_after_prepare = ""
        self._gui_ready = None

    def _on_gui_release_clicked(self, target):
        """A catalogue click downloads that package, then shows the hint.

        It does not start an in-app install. Continue, or the sidebar button,
        opens the desktop tool afterwards.
        """
        if not isinstance(target, dict):
            return
        # The click is the whole request: download that package and hand it to
        # the desktop tool, instead of stopping on a preparation prompt.
        self._gui_after_prepare = "launch"
        self._gui_launch_when_ready = True
        self._begin_gui_prepare(target)

    def _cached_gui_package(self):
        """The cached extract, but only when the desktop tool can open it.

        An extract from an interrupted run still has its scatter and some of
        its images, so it looks like firmware and then fails inside the tool
        ("images referenced in scatter file were not found"). Readiness is
        checked here instead, and an unusable cache is reported as no cache at
        all: the release on screen is downloaded as a fresh one.
        """
        from ..sp_flash_gui import cached_install_firmware, check_cached_firmware_readiness

        latest = device_tracking.get_latest_package(self.settings)
        scatter, extract = cached_install_firmware(latest)
        if scatter is None:
            return None
        model = (latest or {}).get("model") or self._package_model or ""
        ready, sc, ed, reason = check_cached_firmware_readiness(
            scatter_path=scatter,
            extract_dir=extract,
            model=model,
        )
        if not ready:
            self._gui_cache_reason = reason
            logger.info("Cached firmware cannot be opened, preparing a fresh one: %s", reason)
            return None
        self._gui_cache_reason = ""
        return {
            "kind": "cache",
            "scatter": sc or scatter,
            "extract": ed or extract,
            "model": model,
            "name": (latest or {}).get("software_name") or "",
        }

    def _open_sp_flash_tool_gui(self):
        """Open the desktop tool with the chosen firmware and return here.

        While the disconnect hint is up, this is the same as Continue.
        Otherwise firmware is: a clicked release or browsed package, else the
        cached extract, else the release focused on screen.
        """
        from ..sp_flash_gui import choose_sp_gui_package, is_sp_flash_gui_supported

        if not is_sp_flash_gui_supported():
            QMessageBox.information(
                self,
                tr("sp_gui_title"),
                tr("sp_gui_not_supported"),
            )
            return
        if getattr(self, "_gui_launch_in_flight", False):
            # The tool is already being opened (on Linux it waits behind the
            # privilege prompt). Opening it again would run a second copy that
            # competes for the same USB port and the same history.ini.
            return
        if not self._sync_sp_gui_button_visibility():
            return

        if getattr(self, "_gui_handoff_ready", False):
            self._complete_gui_handoff()
            return
        if getattr(self, "_gui_handoff_prepare", False):
            return

        # Read the choice *before* anything is cancelled. Stopping the install
        # service and the device monitor resets the flash state, and the page
        # drops the explicit release selection when its catalogue refreshes —
        # reading the target afterwards found nothing and the button did
        # nothing at all for a focused release that was not cached yet.
        page = getattr(self, "_select_page", None)
        chosen = page.gui_firmware_target() if page is not None and hasattr(page, "gui_firmware_target") else None
        target = None
        if chosen and chosen.get("kind") != "cache":
            if chosen.get("kind") == "online" and not Path(chosen.get("dest") or "").is_file():
                target = chosen
            else:
                target = page.explicit_firmware() if page is not None else None
                target = target or (page.focused_firmware() if page is not None else None)
                target = target or chosen

        if hasattr(self, "service") and self.service:
            self.service.cancel_flash()
            self.service.stop_device_monitor()
        if getattr(self, "_download_active", False) and hasattr(self, "_select_page"):
            self._select_page.cancel_download()
            self._download_active = False
        if getattr(self, "_elapsed_timer", None) is not None:
            self._elapsed_timer.stop()

        if chosen and chosen.get("kind") == "cache":
            self._package_model = chosen.get("model") or self._package_model or ""
            self._gui_ready = (chosen["scatter"], chosen["extract"], self._package_model)
            self._complete_gui_handoff()
            return
        if not chosen or not target:
            return
        # A clicked but not-yet-downloaded release is prepared in the usual
        # progress view; it becomes eligible only after its extract is complete.
        self._gui_after_prepare = "launch"
        self._gui_launch_when_ready = True
        self._begin_gui_prepare(target)

    def _begin_gui_prepare(self, target) -> bool:
        page = getattr(self, "_select_page", None)
        if page is None or not hasattr(page, "begin_external_prepare"):
            return False
        self._gui_handoff_ready = False
        self._gui_handoff_prepare = True
        self._package_model = target.get("model") or self._package_model or ""
        self._package_name = target.get("name") or self._package_name or ""
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._apply_release_icon(complete=False)
        started = page.begin_external_prepare(target, self._on_gui_prepare_done)
        if not started:
            self._gui_handoff_prepare = False
            return False
        if self._gui_handoff_prepare:
            # The card follows what the worker is doing, not the hand-off as a
            # whole: the download signals switch to the download view, and the
            # extractor's own prep_started switches to the extraction view.
            # Showing "Extracting" up front used to hide a multi-hundred
            # megabyte download behind a 0% extraction card.
            self._download_active = (target.get("kind") == "online")
            self._nav_to_page(_PAGE_FLASH)
            if hasattr(self, "_select_page") and hasattr(self._select_page, "_title"):
                title_key = "flash_download_in_progress" if self._download_active else "flash_install_in_progress"
                self._select_page._title.setText(tr(title_key))
        self._reveal_sp_gui_button()
        return True

    def _on_gui_prepare_done(self, ok, extract_dir, err):
        from ..flash_service import _find_scatter

        self._gui_handoff_prepare = False
        if not ok or not extract_dir:
            self._download_active = False
            self._gui_ready = None
            self._sync_sp_gui_button_visibility()
            self._nav_to_page(_PAGE_SELECT)
            logger.warning("Desktop tool package was not ready: %s", err)
            QMessageBox.warning(
                self,
                tr("gui_handoff_failed_title"),
                tr("gui_handoff_failed"),
            )
            return
        try:
            scatter = _find_scatter(Path(extract_dir), allow_raise=False)
        except Exception as e:
            logger.debug("Could not find scatter in extract dir: %s", e, exc_info=True)
            scatter = None
        if not scatter or not Path(scatter).is_file():
            self._download_active = False
            self._gui_ready = None
            self._sync_sp_gui_button_visibility()
            self._nav_to_page(_PAGE_SELECT)
            QMessageBox.warning(
                self,
                tr("gui_handoff_failed_title"),
                tr("gui_handoff_failed"),
            )
            return
        package_path = ""
        name = Path(extract_dir).name
        page = getattr(self, "_select_page", None)
        if page is not None:
            package_path = getattr(page, "_current_package_path", "") or ""
            name = getattr(page, "_current_package_name", "") or name
        model = self._package_model or ""
        try:
            device_tracking.record_latest_package(
                model=model,
                software_name=name,
                tag_name="",
                package_path=package_path,
                extract_dir=str(Path(extract_dir).resolve()),
                scatter_path=str(Path(scatter).resolve()),
                settings=self.settings,
            )
        except Exception as e:
            logger.debug("Could not record latest package: %s", e, exc_info=True)
        self._gui_ready = (Path(scatter), Path(extract_dir), model)
        try:
            from ..sp_flash_gui import find_sp_flash_tool_dirs, stage_history_before_launch, update_sp_history_ini

            update_sp_history_ini(
                scatter_path=Path(scatter),
                extract_dir=Path(extract_dir),
                model=model,
            )
            history_ready = any(
                stage_history_before_launch(tool_dir, scatter)[0]
                for tool_dir in find_sp_flash_tool_dirs()
            )
        except Exception:
            logger.debug("Could not prepare history.ini before the desktop tool", exc_info=True)
            history_ready = False
        if not history_ready:
            logger.warning("SP Flash Tool history.ini could not be prepared for the selected firmware")
            self._gui_ready = None
            self._gui_handoff_prepare = False
            self._download_active = False
            self._sync_sp_gui_button_visibility()
            QMessageBox.warning(
                self,
                tr("gui_handoff_failed_title"),
                tr("gui_handoff_failed"),
            )
            return
        launch_now = (
            getattr(self, "_gui_launch_when_ready", False)
            or getattr(self, "_gui_after_prepare", "") == "launch"
        )
        if launch_now:
            self._complete_gui_handoff()
        else:
            self._show_gui_handoff_hint()

    def _show_gui_handoff_hint(self):
        from ..config import download_mode_hint

        ready = getattr(self, "_gui_ready", None) or (None, None, "")
        model = ready[2] or self._package_model or ""
        self._package_model = model
        self._gui_handoff_prepare = False
        self._gui_handoff_ready = True
        self._download_active = False
        self._install_failed = False
        self._install_complete = False
        self._compact_install_screen = False
        self._power_off_prompt = True
        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(model)
        self._flash_page.show_gui_handoff_prompt(download_mode_hint(model))
        self._apply_release_icon(complete=False)
        self._nav_to_page(_PAGE_FLASH)
        self._release_status_to_donors()
        self._reveal_sp_gui_button()

    def _complete_gui_handoff(self):
        """Leave the progress screen and open the desktop tool with this package."""
        ready = getattr(self, "_gui_ready", None)
        self._gui_handoff_ready = False
        self._gui_handoff_prepare = False
        self._gui_launch_when_ready = False
        self._gui_after_prepare = ""
        # Consumed: the tool is opened from this tuple once, and a second
        # request must go through the hand-off again rather than reuse it.
        self._gui_ready = None
        self._download_active = False
        self._power_off_prompt = False
        self._compact_install_screen = False
        self._install_failed = False
        self._install_complete = False
        if getattr(self, "_elapsed_timer", None) is not None:
            self._elapsed_timer.stop()
        try:
            self.sm.reset_full()
        except Exception:
            pass
        if hasattr(self, "_flash_page"):
            self._flash_page.leave_power_off_prompt()
        self._nav_to_page(_PAGE_SELECT)
        if not ready or not ready[0]:
            QMessageBox.warning(
                self,
                tr("gui_handoff_failed_title"),
                tr("gui_handoff_failed"),
            )
            return
        scatter, extract, model = ready
        # The desktop tool opens the same USB port and the same history.ini the
        # guided backends use, so nothing this app started may still be running:
        # a console-mode flash_tool searching for the device (or an MTKClient
        # session unwinding on its own thread) would fight it for both.
        self._terminate_install_sessions()
        self._launch_sp_gui(scatter, extract, model or "")

    def _terminate_install_sessions(self) -> None:
        """Stop every install session this app started before handing over."""
        service = getattr(self, "service", None)
        if service is None or not hasattr(service, "terminate_install_sessions"):
            return
        try:
            report = service.terminate_install_sessions()
        except Exception:
            logger.debug("Could not stop the previous install session", exc_info=True)
            return
        self._append_log(report)

    def _prefill_fw_package(self):
        """When a bundled firmware package (scatter in SP_Flash_Tool or next to macOS .app)
        is detected, stage files to the app's working directory, prime SP Flash Tool history.ini,
        and automatically transition into the Install Software screen."""
        from ..fw_package import find_bundled_firmware, package_install_heading, stage_bundled_firmware

        try:
            pkg = find_bundled_firmware()
        except Exception as e:
            logger.warning("Bundled firmware check failed: %s", e)
            return

        if pkg is None:
            return

        try:
            # 1. Parse scatter, stage firmware files into working directory
            staged_scatter = stage_bundled_firmware(pkg)

            # 2. Configure package details in Select page and Main window
            package_path = str(staged_scatter)
            package_name = package_install_heading(pkg)
            package_model = pkg.model or ""

            if hasattr(self, "_select_page") and self._select_page is not None:
                self._select_page._current_installed_release_info = None
                self._select_page._current_package_path = package_path
                self._select_page._current_package_name = package_name
                self._select_page._current_package_model = package_model
                if pkg.type:
                    self._select_page._selected_type = pkg.type
                if pkg.has_rockbox and hasattr(self._select_page, "_local_rockbox_themes_cb"):
                    self._select_page._local_rockbox_themes_cb.setChecked(True)
                if hasattr(self._select_page, "_path_edit"):
                    self._select_page._path_edit.setText(str(pkg.folder))
                if hasattr(self._select_page, "_details_package"):
                    from .. import catalog
                    self._select_page._details_package = catalog.FirmwarePackage(
                        slug="local-" + pkg.folder.name,
                        name=package_name,
                        model=package_model,
                        repo="",
                        package_name="",
                    )
                    self._select_page._set_details_header(None, self._select_page._details_package)

            if pkg.has_rockbox:
                self._bundled_rockbox_themes = True
                self._is_360p_rockbox = True
                self._rockbox_themes_flow_active = True
            else:
                self._bundled_rockbox_themes = False

            self._bundled_package_path = package_path

            # 3. Automatically launch into the Install Software screen (_PAGE_FLASH)
            logger.info("Auto-triggering install for bundled firmware: %s (%s)", package_name, package_model)
            self._on_package_selected(package_path, package_name, package_model)
        except Exception as e:
            logger.error("Failed to auto-start bundled firmware: %s", e, exc_info=True)

    def _launch_sp_gui(self, scatter, extract_dir, model):
        from ..sp_flash_gui import launch_sp_flash_tool_gui
        from .. import process_guard

        # Tool processes already running (leftovers from an earlier session)
        # must not count as this launch having started.
        self._tool_pids_before_launch = process_guard.flash_tool_pids()
        ok, msg = launch_sp_flash_tool_gui(
            model=model or "",
            scatter_path=scatter,
            extract_dir=extract_dir,
        )
        if not ok:
            logger.warning("Desktop tool did not open: %s", msg)
            QMessageBox.warning(
                self,
                tr("gui_handoff_failed_title"),
                tr("gui_handoff_failed"),
            )
            self._nav_to_page(_PAGE_SELECT)
            return
        self._gui_launch_in_flight = True
        self._append_log(msg or tr("sp_gui_launched_status"))
        if paths.IS_LINUX:
            # The launch goes through pkexec/sudo, whose password prompt belongs
            # to the desktop and outlives this window. Leaving now closed the app
            # under the prompt and the authorisation died with it, so the tool
            # never started. Wait for the tool's own process, then go.
            self._quit_when_tool_runs()
            return
        # The desktop tool owns the device from here: this app is only holding
        # history.ini and a window open, so it leaves.
        self._quit_after_gui_handoff()

    def _quit_when_tool_runs(self, timeout_seconds: float = 180.0) -> None:
        """Leave once SP Flash Tool is actually running, not when it was asked.

        On Linux the tool starts behind a privilege prompt, so the request
        returning only means the prompt is up. Polling for the tool's process
        keeps the updater (and the prompt) alive until the password is accepted,
        and leaves it open — with the launch still offered — if it never is.
        """
        from .. import process_guard

        timer = getattr(self, "_tool_launch_timer", None)
        if timer is not None:
            timer.stop()
        timer = QTimer(self)
        self._tool_launch_timer = timer
        start = time.monotonic()

        def _check() -> None:
            if getattr(self, "_gui_quit_started", False):
                timer.stop()
                return
            if process_guard.flash_tool_running(
                ignore_pids=getattr(self, "_tool_pids_before_launch", frozenset())
            ):
                timer.stop()
                self._quit_after_gui_handoff()
                return
            if time.monotonic() - start >= timeout_seconds:
                timer.stop()
                logger.warning("SP Flash Tool never started; keeping the updater open")
                # The request went nowhere (refused or dismissed prompt): the
                # launch is offered again instead of staying hidden forever.
                self._gui_launch_in_flight = False
                self._sync_sp_gui_button_visibility()

        timer.timeout.connect(_check)
        timer.start(700)
        # The prompt may already be answered, or the tool may start at once.
        QTimer.singleShot(1200, _check)

    def _quit_after_gui_handoff(self) -> None:
        """Leave the app once SP Flash Tool's own window is up.

        Not ``close()``: closing the window ends the install window and
        sweeps stray flash tools, which would kill the very tool this run just
        handed over to. Quitting the event loop instead leaves every process
        alone; ``app._leave_cleanly`` keeps the interpreter from tearing down a
        worker thread that is still finishing.
        """
        if getattr(self, "_gui_quit_started", False):
            return
        self._gui_quit_started = True
        self._force_quit = True
        logger.info("SP Flash Tool GUI is running; closing the updater")
        QApplication.quit()

    def _check_linux_first_run(self):
        """First run on Linux: prepare on the Settings card, not in a window.

        This used to open the setup window on top of the app. The card holds
        the same preparation, so the first run simply lands on it — a flash
        run stages the engine on its own either way.
        """
        if paths.IS_MAC or os.environ.get("QT_QPA_PLATFORM") == "offscreen" or os.environ.get("CI"):
            return
        from .. import linux_sp_flash

        stage = linux_sp_flash.stage_dir()
        first_run_done = self.settings.value("linux_first_run_completed", False, type=bool)
        files_ready = linux_sp_flash.files_ready(stage)

        if not files_ready or not first_run_done:
            self.settings.setValue("linux_first_run_completed", True)
            self._show_linux_setup()

    def _open_credits(self):
        from ..browser import open_browser
        open_browser("https://innioasis.app/credits.html?thank-you=1")

    def _on_language_changed(self, index):
        lang = self._lang_combo.itemData(index)
        translator().set_language(lang)
        self.settings.setValue("language", lang)
        # A right-to-left language mirrors the interface. This runs before the
        # retranslation so the re-laid-out copy is measured in the direction it
        # is finally drawn in.
        apply_interface_direction(QApplication.instance())
        self._align_page_title()
        self._retranslate_all()
        self._sync_notes_language()
        # German labels are longer than English ones: refit the window to the
        # copy it now holds.
        QTimer.singleShot(0, self._fit_window_to_page)

    def _restore_notes_language(self):
        """Apply the saved release-notes choice once the pages exist.

        A reader who left the app on German (or Spanish, or Japanese) and had
        the notes translated sees them translated again next time, without
        asking twice.
        """
        if not hasattr(self, "_select_page"):
            return
        if is_english_language():
            return
        stored = self.settings.value("translate_release_notes", None)
        if stored is None or str(stored).strip().lower() not in ("false", "0", "no", "off"):
            self._select_page.set_auto_translate_notes(True)

    def _sync_notes_language(self):
        """Keep the release notes in the language the reader just chose.

        Switching to any language but English turns the in-app translation on
        and translates what is already on screen, so no second click is needed.
        Switching back to English restores the published text. The choice is
        remembered, so the next release selected is shown translated too.
        """
        if not hasattr(self, "_select_page"):
            return
        wanted = not is_english_language()
        self.settings.setValue("translate_release_notes", wanted)
        self._select_page.set_auto_translate_notes(wanted)

    def _retranslate_all(self):
        # The sidebar brand header is not built on Linux with native titlebars.
        if hasattr(self, "_brand_label"):
            self._brand_label.setText(get_brand_name())
        self.setWindowTitle(f"{get_app_name()} v{APP_VERSION}")
        self._update_author_label()
        for key, (btn, _idx) in self._nav_buttons.items():
            btn.setText(tr(key))
        self._sync_settings_nav_label()
        self._sync_install_nav_entry()
        self._support_btn.setText(tr("nav_donate"))
        for attr, tip_key in (
            ("_discord_btn", "help_discord_tip"),
            ("_reddit_btn", "help_reddit_tip"),
            ("_github_btn", "help_github_tip"),
        ):
            button = getattr(self, attr, None)
            if button is not None:
                button.setToolTip(tr(tip_key))
                button.setAccessibleName(tr(tip_key))
        self._log_btn.setText(tr("nav_log"))
        self._sync_update_button()
        if hasattr(self, "_linux_setup_btn"):
            self._linux_setup_btn.setText(tr("nav_linux_setup"))
        if hasattr(self, "_sp_flash_tool_btn"):
            self._sp_flash_tool_btn.setText(tr("nav_sp_flash_tool_gui"))
        if hasattr(self, "_install_output_btn"):
            self._install_output_btn.setText(tr("nav_install_output"))
        if hasattr(self, "_lang_label"):
            self._lang_label.setText(tr("nav_language"))
        if hasattr(self, "_select_page"):
            self._select_page.retranslate()
            self._update_header_title()
        self._flash_page.retranslate()
        if hasattr(self, "_support_page"):
            self._support_page.retranslate()
        self._error_page.retranslate()
        self._retry_page.retranslate()
        if hasattr(self, "_settings_page"):
            self._settings_page.retranslate()
        if self._diagnostics_page is not None and hasattr(self._diagnostics_page, "retranslate"):
            self._diagnostics_page.retranslate()
        if self.statusBar() and hasattr(self.statusBar(), "retranslate"):
            self.statusBar().retranslate()
        self._apply_generic_mtk_branding()
        # Every row has its new language's text now: the rail takes the width
        # that copy needs instead of eliding it
        self._reapply_rail_width()

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
        info = getattr(self, "_pending_app_update", None)
        if info is not None and getattr(info, "tag", ""):
            # The rail row names the waiting build; opening it is the update.
            self._offer_app_update(info)
            return
        self._update_manual_pending = True
        self._show_update_check_busy()
        self._run_update_check()

    def _sync_update_button(self) -> None:
        """Show the update row only for a build that is waiting to be installed.

        The label carries the action and the version ("Get 2.7", "Obtenir 3.0"),
        so the row reads as what the click does rather than as a place.
        """
        button = getattr(self, "_check_updates_btn", None)
        if button is None:
            return
        info = getattr(self, "_pending_app_update", None)
        version = str(getattr(info, "version", "") or "")
        waiting = bool(version) and bool(getattr(info, "tag", ""))
        if waiting:
            button.setText(tr("nav_get_update").format(version=version))
        else:
            button.setText(tr("nav_check_updates"))
        available = (
            waiting
            and not is_generic_mtk()
            and not is_offline_mode()
            and not self._install_run_active()
        )
        button.setVisible(available)
        button.setEnabled(available)
        self._reapply_rail_width()

    def _show_update_check_busy(self):
        """Native indeterminate bar while the app-update check runs off-thread."""
        from PySide6.QtWidgets import QProgressBar, QVBoxLayout, QWidget

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(24, 16, 24, 16)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        layout.addWidget(bar)
        layout.addStretch(1)
        self._present_inline(host, tr("nav_check_updates"))

    def _run_update_check(self):
        old = getattr(self, "_update_worker", None)
        if old is not None and old.isRunning():
            try:
                old.finished.disconnect()
            except Exception:
                pass
            old.requestInterruption()
            old.wait(100)
        worker = UpdateCheckWorker(
            UPDATE_REPO, APP_VERSION, self, force=bool(self._update_manual_pending)
        )
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

    def _on_app_update_toast_action(self, info):
        """User clicked action button on app update toast."""
        if self._install_run_active():
            # If an install/download is currently running, don't interrupt with modal
            # Instead, open release page in browser
            from ..browser import open_browser
            release_url = getattr(info, "html_url", "") or f"https://github.com/{UPDATE_REPO}/releases"
            open_browser(release_url)
            return

        self._offer_app_update(info)

    def _on_update_check_done(self, info, manual):
        if not isinstance(info, UpdateInfo):
            info = UpdateInfo()
        if not manual and self._update_offered_once:
            return
        if info.tag:
            skipped = str(self.settings.value("update_skipped_version", ""))
            if info.version == skipped and not manual:
                self._pending_app_update = None
                self._sync_update_button()
                return
            self._update_offered_once = True
            self._pending_app_update = info
            self._sync_update_button()
            self._offer_app_update(info)
        else:
            self._pending_app_update = None
            self._sync_update_button()
            if manual:
                self._present_message(
                    tr("nav_check_updates"),
                    tr("update_check_failed")
                    if info.failed
                    else tr("update_up_to_date").format(version=APP_VERSION),
                )

    def _offer_app_update(self, info) -> None:
        """Present the waiting build: a toast while a firmware run owns the window."""
        self._pending_app_update = info
        self._sync_update_button()
        # If user has started a firmware download/install, do not interrupt with a
        # full window view! Present a toast in the Select Software view instead.
        if self._install_run_active():
            self._select_page.show_app_update(info)
            return
        dlg = UpdateAvailableDialog(
            self,
            info=info,
            current_version=APP_VERSION,
            on_skip=lambda v: self.settings.setValue("update_skipped_version", v),
        )
        self._present_inline(dlg, tr("nav_check_updates"))

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
            # The note stays on Select Software. It does not change the
            # catalogue or remember a ceiling; the close button is session-only.
            self._select_page.show_update_offers(updates)
        elif manual:
            tracked = device_tracking.get_all_device_installs(self.settings)
            if tracked:
                self._present_message(
                    tr("reminder_new_release_title"),
                    tr("settings_firmware_up_to_date"),
                )
            else:
                self._present_message(
                    tr("reminder_new_release_title"),
                    tr("settings_no_devices_tracked"),
                )

    def _check_legacy_installations(self):
        try:
            from ..legacy_cleanup import (
                detect_legacy_installations,
                migrate_legacy_application_support,
            )
            if not getattr(self, "_legacy_migration_checked", False):
                self._legacy_migration_checked = True
                migration_ok, migration_note = migrate_legacy_application_support()
                if migration_ok and migration_note not in ("already migrated", "legacy app marker not found"):
                    logger.info("Legacy application data migration: %s", migration_note)
            prompted = self.settings.value("legacy_cleanup_prompted", False, type=bool)
            if prompted:
                return
            info = detect_legacy_installations()
            if info.get("has_legacy"):
                # The offer is the Settings card, shown on its own in the app's
                # own surface. Nothing is popped out over the window.
                self._show_legacy_removal_card()
                self.settings.setValue("legacy_cleanup_prompted", True)
        except Exception:
            logger.debug("Legacy installation migration/check failed", exc_info=True)

    def _show_legacy_removal_card(self) -> None:
        """Offer the removal on its own card, in the window, not as a modal.

        The Settings card is the offer's one home. Showing it alone keeps the
        question on the app's own surface, and the window is then sized to fit
        that single card.
        """
        self._nav_to_page(_PAGE_SETTINGS)
        page = getattr(self, "_settings_page", None)
        card = getattr(page, "_legacy_cleanup_card", None) if page is not None else None
        if card is None:
            return
        page.show_card_alone(card)
        page._reveal_legacy_card()
        self._legacy_focus_pending = True
        self._adjust_window_geometry()

    def _on_card_focus_finished(self):
        """A card shown on its own is done: hand the page and the window back."""
        self._adjust_window_geometry()
        if getattr(self, "_legacy_focus_pending", False):
            self._legacy_focus_pending = False
            self._return_to_select_software()

    def _progress_owns_status(self) -> bool:
        """The progress card already shows download and install text."""
        return bool(
            getattr(self, "_download_active", False)
            or self._install_run_active()
            or getattr(self, "_power_off_prompt", False)
            or getattr(self, "_gui_handoff_prepare", False)
            or getattr(self, "_gui_handoff_ready", False)
            or getattr(self, "_drop_check_active", False)
            or getattr(self, "_install_complete", False)
            or getattr(self, "_install_failed", False)
        )

    def _on_sweep_report(self, killed) -> None:
        """Log what a background flash-tool sweep cleaned up."""
        from .. import process_guard

        text = process_guard.describe(killed)
        if text:
            self._append_log(text)

    def _release_status_to_donors(self) -> None:
        sb = self.statusBar()
        if sb is not None:
            sb.clearMessage()

    def _show_status(self, text, timeout_ms=0):
        """Show a transient message in the status bar; it reverts to the
        donations/goal display once cleared or timed out.

        Download and install progress stay on the card, so the bar can keep
        the donor and goal lines.
        """
        sb = self.statusBar()
        if sb is None:
            return
        if self._progress_owns_status():
            sb.clearMessage()
            return
        sb.showMessage(text, int(timeout_ms or 0))
        if not sb.isVisible():
            sb.setVisible(True)

    def _on_release_icon_ready(self, pixmap) -> None:
        if pixmap is None or getattr(pixmap, "isNull", lambda: True)():
            return
        self._flash_page.set_release_icon(
            pixmap, complete=bool(getattr(self, "_install_complete", False)),
        )

    def _on_status_message_changed(self, text):
        if not text:
            self._apply_donation_visibility()

    def _set_help_row_visible(self, visible: bool) -> None:
        """Show or hide the community marks as one row.

        Each mark is its own focusable button, so hiding only the row would
        leave the buttons reachable by Tab; they hide with it.
        """
        row = getattr(self, "_help_row", None)
        if row is not None:
            row.setVisible(bool(visible))
        for attr in ("_discord_btn", "_reddit_btn", "_github_btn"):
            button = getattr(self, attr, None)
            if button is not None:
                button.setVisible(bool(visible))
                button.setEnabled(bool(visible))

    def _apply_donation_visibility(self, is_disabled=None):
        if is_disabled is None:
            is_disabled = (
                device_tracking.is_donation_ui_disabled(self.settings)
                or device_tracking.is_donation_install_prompt_disabled(self.settings)
                or device_tracking.is_settings_donations_section_dismissed(self.settings)
            )
        sb = self.statusBar()
        if sb is not None:
            if hasattr(sb, "set_donations_enabled"):
                sb.set_donations_enabled(not is_disabled)
            else:
                sb.setVisible(not is_disabled)
        quiet = self._install_run_active() or self._handoff_chrome()
        if hasattr(self, "_support_btn") and self._support_btn is not None:
            self._support_btn.setVisible((not is_disabled) and not quiet)
        self._set_help_row_visible(not is_disabled)
        if hasattr(self, "_version_label") and self._version_label is not None:
            self._version_label.setVisible(not is_disabled and not is_mediatek_installer())
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
            # Reads as "Installer 2.5" beside the version badge.
            self._brand_label.setText(get_brand_name())
        # Update checks require an internet connection, so offline/generic hides them.
        # Credits and author attribution remain visible in offline mode for Updater CE,
        # but hidden in generic MediaTek Installer builds.
        if hasattr(self, "_credits_btn"):
            self._credits_btn.setVisible(not mediatek)
        self._sync_update_button()
        if hasattr(self, "_settings_page"):
            self._settings_page.apply_brand_mode(mediatek)
        if hasattr(self, "_select_page"):
            self._select_page.apply_generic_mode(offline)
            # The MediaTek Installer takes the DA + scatter SP Flash Tool's own
            # setup takes instead of a firmware package.
            if hasattr(self._select_page, "apply_installer_mode"):
                self._select_page.apply_installer_mode(mediatek)
        self._apply_donation_visibility()

    def _community_icon(self, name: str, icon_size: int = 16) -> QIcon:
        """Load a community brand SVG and tint it with the live sidebar foreground."""
        from PySide6.QtCore import QByteArray, QRectF
        from PySide6.QtSvg import QSvgRenderer

        path = paths.RESOURCES_DIR / f"{name}.svg"
        color = T().fg
        if not path.is_file():
            return get_symbol_icon("support", icon_size, color=color)
        try:
            source = path.read_text(encoding="utf-8")
            import re

            source = re.sub(r"fill=([\"'])[^\"']+\1", f'fill="{color}"', source)
            source = source.replace("currentColor", color)
            renderer = QSvgRenderer(QByteArray(source.encode("utf-8")))
            if renderer.isValid():
                from PySide6.QtGui import QPixmap, QPainter

                pixmap = QPixmap(icon_size * 2, icon_size * 2)
                pixmap.setDevicePixelRatio(2.0)
                pixmap.fill(Qt.transparent)
                view_box = renderer.viewBoxF()
                if view_box.width() <= 0 or view_box.height() <= 0:
                    raise ValueError(f"Invalid {name} icon viewBox")
                scale = min(icon_size / view_box.width(), icon_size / view_box.height())
                width = view_box.width() * scale
                height = view_box.height() * scale
                target = QRectF((icon_size - width) / 2, (icon_size - height) / 2, width, height)
                painter = QPainter(pixmap)
                renderer.render(painter, target)
                painter.end()
                return QIcon(pixmap)
        except Exception:
            logger.debug("Could not load %s community icon", name, exc_info=True)
        return get_symbol_icon("support", icon_size, color=color)

    def _open_community_link(self, community: str) -> None:
        from ..browser import open_browser
        from ..config import DONATION_LINKS

        url = DONATION_LINKS.get(community)
        if url:
            open_browser(url)

    def _on_offline_mode_changed(self, enabled: bool):
        # Offline mode hides the online catalogue and its affordances, but it
        # never renames anything: the brand is fixed by the build, not by this
        # checkbox.
        self._apply_generic_mtk_branding()

    def _on_donations_updated(self, donations):
        if donations:
            self._donations = donations

    def _on_support_clicked(self):
        """Open Support Us in the window. A finished install keeps Install Software selected.

        During an install run the page opens as a pop-out modal instead, so the
        install progress stays on screen underneath.
        """
        if self._install_run_active() or getattr(self, "_compact_install_screen", False):
            self._open_support_modal()
            self._highlight_nav(
                self._stack.currentIndex(),
                self._sync_install_nav_entry(),
            )
            return
        self._nav_to_page(_PAGE_SUPPORT)

    def _open_support_modal(self):
        """Show the Support Us page in a pop-out dialog above the install run."""
        existing = getattr(self, "_support_modal", None)
        if existing is not None:
            try:
                existing.raise_()
                existing.activateWindow()
                return
            except Exception:
                pass
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("nav_donate"))
        dlg.setModal(True)
        page = SupportPage(dlg)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(page)
        dlg.resize(560, page.content_height_hint(560 - 24) + 24)
        dlg.finished.connect(lambda _r: setattr(self, "_support_modal", None))
        self._support_modal = dlg
        dlg.show()
        # The hint above is measured before the page has its width; one turn
        # later the wrapped copy knows its real line count, so the dialog takes
        # the height it actually needs instead of clipping the appeal.
        def _fit_support_modal() -> None:
            if not dlg.isVisible():
                return
            wanted = page.content_height_hint() + 24
            if wanted > dlg.height():
                dlg.resize(dlg.width(), wanted)

        QTimer.singleShot(0, _fit_support_modal)

    def _show_donation_dialog(self, context="general", *args, **kwargs):
        """Support lives in the window. Install completion does not open a dialog."""
        del context, args, kwargs
        self._on_support_clicked()

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

    def _worker_running(self, worker) -> bool:
        """True while ``worker``'s thread runs; a deleted worker is not running."""
        if worker is None:
            return False
        try:
            return bool(worker.isRunning())
        except RuntimeError:
            # The C++ object is gone (finished, then deleteLater): nothing to
            # keep alive, and asking again would raise.
            return False

    def _retire_worker(self, worker):
        """Keep a worker alive until its thread has stopped.

        The window owns workers parented to itself, so quitting while one is
        still running used to destroy a live QThread — Qt aborts the process for
        that (``QThread: Destroyed while thread is still running``), which is
        what the crash reports for this app showed.
        """
        return retire_worker(worker, self._retired_workers)

    def cleanup_workers(self):
        """Cleanly terminate and wait for any background workers."""
        self.service.cleanup()
        select_page = getattr(self, "_select_page", None)
        if select_page is not None:
            rw = getattr(select_page, "_releases_worker", None)
            if rw is not None and rw.isRunning():
                rw.requestInterruption()
                rw.wait(1500)
                self._retire_worker(rw)
            dw = getattr(select_page, "_download_worker", None)
            if dw is not None and dw.isRunning():
                dw.cancel()
                dw.wait(1500)
                self._retire_worker(dw)
            mon = getattr(select_page, "_monitor", None)
            if mon is not None:
                mon.stop_monitoring()
        icon_loader = getattr(self, "_icon_loader", None)
        if self._worker_running(icon_loader):
            # The icon fetch has its own network timeout, so it is retired
            # rather than waited for: dropping the reference here destroyed a
            # running QThread, which aborts the process on the way out.
            icon_loader.requestInterruption()
            self._retire_worker(icon_loader)
        flash_page = getattr(self, "_flash_page", None)
        if flash_page is not None and hasattr(flash_page, "stop_theme_workers"):
            flash_page.stop_theme_workers()
        for w in (
            getattr(self, "_manifest_worker", None),
            getattr(self, "_update_worker", None),
            getattr(self, "_device_update_worker", None),
            getattr(select_page, "_manifest_worker", None) if select_page else None,
            getattr(select_page, "_prep_worker", None) if select_page else None,
            getattr(select_page, "_translate_worker", None) if select_page else None,
        ):
            if w is not None and w.isRunning():
                w.requestInterruption()
                w.wait(1500)
                self._retire_worker(w)

    def close(self):
        return super().close()

    def closeEvent(self, event):
        if self._install_run_active() and not getattr(self, "_force_quit", False):
            is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or bool(os.environ.get("INNIOASIS_HEADLESS"))
            if not is_headless or getattr(self, "_test_close_prompt", False):
                app_name = get_app_name()
                prompt_pattern = tr("flash_close_confirm")
                if "{app}" in prompt_pattern:
                    prompt_text = prompt_pattern.format(app=app_name)
                else:
                    prompt_text = f"Are you sure you want to stop the install and close {app_name}?"
                self._nav_to_page(_PAGE_FLASH)
                page = self._flash_page
                if (
                    getattr(self, "_download_active", False)
                    and page._stack.currentWidget() is not page._downloading_view
                ):
                    page.show_downloading()
                if not page.prompt_stop(prompt_text, wait=True):
                    event.ignore()
                    return
            if hasattr(self, "service") and self.service:
                try:
                    self.service.cancel_flash()
                except Exception:
                    pass
            try:
                from ..sleep_inhibit import release_sleep_inhibition
                release_sleep_inhibition()
            except Exception:
                pass

        # Closing the window ends any install window: a backend left behind
        # would hold the USB port after the app is gone.
        from .. import process_guard

        for item in process_guard.end_install_session(reason="app closed"):
            logger.info("Stopped leftover %s (pid %s) on close", item.get("tool"), item.get("pid"))

        if hasattr(self, "_theme_watcher") and self._theme_watcher:
            try:
                self._theme_watcher.stop()
            except Exception:
                pass
        service = getattr(self, "_theme_sync_service", None)
        if service is not None:
            try:
                service.stop()
            except Exception:
                pass
        self.cleanup_workers()
        super().closeEvent(event)


_METHOD_LABELS = {
    "auto": "Auto",
    "sp": "SP Flash Tool",
    "mtk": "MTKClient",
}


def _method_label(method: str) -> str:
    if paths.IS_MAC:
        return "MTKClient"
    return _METHOD_LABELS.get(method, method)
