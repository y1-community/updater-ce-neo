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
    QRectF,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QGuiApplication,
    QIcon,
    QPalette,
    QPen,
    QPixmap,
)
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
    NoticesPage,
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
from ..themes_journey import ThemesJourney
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
_PAGE_NOTICES = 8

# Sizing policy: one installer window rather than a document window that keeps
# growing with its contents. Every page is laid out in the same band, and only
# the page's own height moves the window inside it.
#
# 760x520 is the size of an installer pane once the rail is included (the
# macOS Installer's own panes are a little smaller and have no rail): wide
# enough for the catalogue's two cards, short enough to sit beside the
# instructions the reader is following, and the same on every desktop.
DEFAULT_WINDOW_WIDTH = 760
DEFAULT_WINDOW_HEIGHT = 520
# The floor is what the rail plus one readable page column need — the same
# floor the app always had, so a small display still gets a usable window. The
# ceiling is the catalogue's own preferred layout, so no page can turn the
# window into a wide desktop app.
MINIMUM_WINDOW_WIDTH = 640
MINIMUM_WINDOW_HEIGHT = 340
MAXIMUM_WINDOW_WIDTH = 920
# The tallest the app may size itself to. A page with more content than this
# (the Notices list, a long support appeal) scrolls inside the window instead
# of stretching it: a taller window covers the instructions the user is
# following and can hang off a small display. The diagnostics log is the one
# page allowed past it, because there the log is the window.
MAXIMUM_AUTO_FIT_HEIGHT = 620

# Slack added to a page's own content height when the window hugs it, so the
# last card's shadow and the status bar are never clipped.
CONTENT_FIT_SLACK = 16

# A window closed to the tray keeps its whole widget tree: hide() releases no
# pages, so the process stays the size it was with the window open. After this
# long with nothing on screen and nothing running the window is handed back
# entirely, and the process returns to the size of a --tray launch. Coming back
# costs one rebuild, never a flash.
IDLE_TRAY_SHED_SECONDS = 120
IDLE_TRAY_SHED_MS = IDLE_TRAY_SHED_SECONDS * 1000


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
            # The floor and the display are both limits, and the floor wins on
            # a display too small to hold it: below the floor the rail and the
            # page column cannot be drawn at all, so a window that technically
            # fits would be less usable than one that overhangs.
            width = max(MINIMUM_WINDOW_WIDTH, min(width, int(available.width())))
            height = max(MINIMUM_WINDOW_HEIGHT, min(height, int(available.height())))
    except Exception:
        logger.debug("Screen clamp failed", exc_info=True)
    return width, height


# The sidebar brand title and the version badge beside it share one size.
_BRAND_FONT_SIZE = "14px"


class _SimulatedUpdateWorker(QThread):
    """Runs the Cmd/Ctrl+U test lookup of the latest release off the UI thread."""

    finished = Signal(object)

    def __init__(self, checker, parent=None):
        super().__init__(parent)
        self._checker = checker

    def run(self):
        try:
            info = self._checker.simulate_latest(force=True)
        except Exception:
            info = None
        self.finished.emit(info)


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

# Failures the MTKClient engine reports on its own paths. The player is left
# half-initialised by these, so the next attempt needs a real power cycle: the
# card asks for it (with the paperclip reset tip) instead of showing a dead end.
_MTK_FAILURE_CODES = frozenset(
    {
        "MTK_INIT_FAILED",
        "MTK_IMPORT_FAILED",
        "MTK_NO_DEVICE",
        "MTK_DA_FAILED",
        "MTK_INSTALL_FAILED",
        "MTK_STALL",
        "WRITE_FAILED",
        "CONNECTION_FAILED",
    }
)


def _is_mtk_install_failure(error_code) -> bool:
    """True for a failure that leaves the player needing a power cycle."""
    text = str(error_code or "").strip().upper()
    if not text:
        return False
    return text.startswith("MTK_") or text in _MTK_FAILURE_CODES

# States in which a flash run is under way: the sidebar's first entry becomes
# "Install Software" and holds the accent highlight until the run finishes.
_INSTALL_RUN_STATES = (
    FlashState.S2_WAIT_CONNECTION,
    FlashState.S3_DEVICE_DETECTED,
    FlashState.S4_FLASHING,
    FlashState.S6_RETRYING,
)


def _mouse_button_held() -> bool:
    """True while a mouse button is down, including native macOS drags.

    ``QApplication.mouseButtons()`` only knows about presses Qt delivered. A
    drag that starts in AppKit's own title-bar region can own the gesture
    without one, so the window would still accept a programmatic resize. AppKit
    keeps the real button state; ask it first on macOS.
    """
    if sys.platform == "darwin":
        try:
            import AppKit

            if AppKit.NSEvent.pressedMouseButtons():
                return True
        except Exception:
            pass
    from PySide6.QtWidgets import QApplication

    return QApplication.mouseButtons() != Qt.MouseButton.NoButton


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
            if win is not None:
                win._user_dragging = True
                win._stop_size_anim_for_drag()
            if begin_unified_caption_drag(win):
                # The OS owns the gesture now. Leaving the manual mover armed
                # made this widget move the window as well as AppKit, which
                # read as the frame jumping under the cursor.
                self._dragging = False
                self._drag_offset = None
                event.accept()
                return
            if win is not None:
                self._dragging = True
                self._drag_offset = event.globalPosition().toPoint() - win.pos()
            if getattr(self, "_dragging", False):
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
        win = self.window()
        if win is not None and getattr(win, "_user_dragging", False):
            win._user_dragging = False
            QTimer.singleShot(0, win._adjust_window_geometry)
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
    the system's gesture. macOS uses Qt startSystemMove or AppKit window drag.
    Other desktops use Qt startSystemMove.
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

    handle = window.windowHandle() if hasattr(window, "windowHandle") else None
    if handle is not None:
        try:
            if handle.startSystemMove():
                return True
        except Exception:
            pass

    if sys.platform == "darwin":
        try:
            import AppKit

            from .glass import _get_nsview

            ns_app = AppKit.NSApplication.sharedApplication()
            curr_evt = ns_app.currentEvent()
            # In AppKit, performWindowDragWithEvent: strictly requires a mouse-down event (type 1)
            if curr_evt and int(curr_evt.type()) == 1:
                view = _get_nsview(window)
                ns_win = view.window() if view else None
                if ns_win is not None:
                    ns_win.performWindowDragWithEvent_(curr_evt)
                    return True
        except Exception:
            pass
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
        layout.setContentsMargins(40, 40, 40, 40)
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
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # A dim scrim, not a solid plate. The window material stays visible.
        painter.fillRect(self.rect(), QColor(0, 0, 0, 96))

        # The inset the native drop targets use: a translucent tint inside a
        # broken line. It says "drop here" without hiding the page beneath it.
        zone = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        if zone.width() > 80 and zone.height() > 80:
            accent = self.palette().color(QPalette.ColorRole.Highlight)
            if not accent.isValid():
                accent = QColor(255, 255, 255)
            tint = QColor(accent)
            tint.setAlpha(44)
            pen = QPen(accent, 2, Qt.PenStyle.DashLine)
            pen.setDashPattern([7.0, 5.0])
            painter.setPen(pen)
            painter.setBrush(tint)
            painter.drawRoundedRect(zone, 18, 18)
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


def _completion_notification_message(software: str, model: str) -> str:
    """Notification body without repeating the model the package name carries."""
    message = str(software or "").strip()
    if model:
        tokens = message.upper().replace("(", " ").replace(")", " ").split()
        if str(model).upper() not in tokens:
            message = f"{message} {model}".strip()
    return message


class _PortableCleanupWorker(QThread):
    """Remove a portable run's working files off the UI thread."""

    progress = Signal(int, str)
    finished = Signal(list, str)

    def __init__(self, session, parent=None):
        super().__init__(parent)
        self._session = session

    def run(self):
        try:
            removed = self._session.cleanup(
                lambda percent, key: self.progress.emit(int(percent), str(key))
            )
            self.finished.emit(removed, "")
        except Exception as exc:
            logger.exception("Portable cleanup failed")
            self.finished.emit([], str(exc))


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
        self.sm.state_changed.connect(self._on_state_changed)
        self.service = FlashService(self)

        # Initial window geometry adjustment after full setup
        self._adjust_window_geometry()

        self._package_path = ""
        self._package_name = ""
        self._package_model = ""
        # A bundled-firmware (portable) run leaves only temporary working files;
        # closing it runs the cleanup pass and quits.
        self._bundled_firmware_run = False
        self._portable_session = None
        self._portable_cleanup_started = False
        self._portable_cleanup_done = False
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
        # Catalogue, notices, donors and blocklist are re-checked while the app
        # stays open, so a long-running window never shows last week's data.
        # The refresh is ttl-gated and runs off the UI thread: free when the
        # cached copies are current, silent when the network is not there.
        self._online_refresh_timer = QTimer(self)
        self._online_refresh_timer.setSingleShot(True)
        self._online_refresh_timer.timeout.connect(self._online_refresh_tick)
        from ..online_refresh import next_interval_ms

        self._online_refresh_timer.start(next_interval_ms())
        # Only one all-repository sweep runs at a time; its result reloads the
        # visible release list without another manifest fetch.
        self._release_refresh_worker = None
        # A sweep may finish while another page is open; the catalogue reloads
        # when the user returns instead of waiting for the next random tick.
        self._release_refresh_pending = False
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
        self._saw_all_command_exec = False
        # flash_tool's own completion line already finished this run's UI, so
        # a late worker result must not repaint that completion screen.
        self._sp_console_completion_done = False
        self._failure_title = ""
        self._cache_recovery_needed = False
        self._legacy_migration_checked = False
        # True once this install has already fallen back to the other backend:
        # one automatic switch per run, so a bad cable cannot ping-pong.
        self._fallback_attempted = False
        self._sp_flash_tool_tried = False
        self._sp_1013_seen = False
        self._reboot_suggestion_shown = False
        self._power_off_prompt = False
        # "Keep my data", as read from the install card when the user
        # confirmed the player was off. Off means the firmware is written whole.
        self._keep_data = False
        # An accepted update notification asks for this explicitly; a reinstall
        # of the exact tracked firmware is treated the same way. This is read
        # when the prep card is built and stays with the run through retries.
        self._keep_settings_default = False
        # A package that needs Format All + Download (manifest flag or a
        # previous 8502) runs the same SP backend with only the command mode
        # changed. Keep Data is unavailable while this is set.
        self._format_all_required = False
        self._format_all_confirm_pending = False
        self._keep_data_pkg = None
        # Install Firmware First: a manifest ``install_first`` package must be
        # put on the player before this overlay. The journey owns the answer
        # and what comes next; the stage names the prompt on screen.
        self._install_first_journey = None
        self._install_first_stage = ""
        self._install_first_after = None
        self._install_first_prerequisite_run = False
        # MTKclient leaves the player off after the base run, so the target's
        # prep card only asks for a power-off: the player has just been
        # powered on and disconnected by the preceding card.
        self._install_first_power_off_only = False
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
        # The headless tray owner, when this session has one. It takes the
        # window back after a long idle and builds a new one on demand.
        self._background_agent = None
        self._idle_shed_timer = None
        # The exact bound callable: PySide matches a disconnect against the
        # object it was given, and a fresh bound method is a new object.
        self._notices_slot = self._on_notices_ready
        self._build_ui()
        self._install_themes_shortcut()
        self.setAcceptDrops(True)
        self._drop_overlay = _DropOverlay(self)
        self._drop_check_active = False
        # Drag events go to the child under the cursor, not to the window. The
        # app-level filter lets a package dropped anywhere -- the catalogue,
        # Settings, a donation card, the scroll bars -- take the same path as
        # one dropped on the window frame.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._connect_signals()
        self._nav_to_page(_PAGE_SELECT)
        from .surfaces import harmonise_window_plates
        harmonise_window_plates(self)

        self._manifest_worker = None
        is_headless = os.environ.get("QT_QPA_PLATFORM") == "offscreen" or bool(os.environ.get("INNIOASIS_HEADLESS"))
        if not is_headless:
            QTimer.singleShot(250, self._start_manifest_worker)
            QTimer.singleShot(3000, self._check_remote_flags_startup)
            QTimer.singleShot(UPDATE_CHECK_STARTUP_DELAY_MS, self._start_auto_update_check)
            QTimer.singleShot(
                UPDATE_CHECK_STARTUP_DELAY_MS + 1000,
                lambda: self._check_device_firmware_updates(manual=False),
            )

        self._sync_sp_gui_button_visibility()
        self._sync_settings_sp_gui_visibility()

        self._runtime_i18n = None
        if not is_headless:
            try:
                from ..i18n import current_language
                from ..runtime_i18n import RuntimeTranslator

                self._runtime_i18n = RuntimeTranslator(self)
                self._runtime_i18n.start(current_language())
                app = QApplication.instance()
                if app is not None:
                    app.aboutToQuit.connect(self._runtime_i18n.stop)
                if hasattr(self, "_stack"):
                    self._stack.currentChanged.connect(
                        lambda _index: QTimer.singleShot(
                            0, self._runtime_i18n.scan
                        )
                    )
            except Exception:
                logger.debug("Runtime translation unavailable", exc_info=True)
                self._runtime_i18n = None

        if not paths.IS_MAC:
            self._sp_history_timer = QTimer(self)
            self._sp_history_timer.setInterval(30000)
            self._sp_history_timer.timeout.connect(self._sync_sp_history)
            self._sp_history_timer.start()
            if not is_headless:
                QTimer.singleShot(800, self._sync_sp_history)

    def _sync_sp_history(self):
        """Passively keep history.ini pointed at the last cached firmware's scatter and DA.

        Runs on launch, on the background timer while idle, and after a firmware
        install finishes — never during an active firmware install or while
        SP Flash Tool is running.
        """
        try:
            if (
                self._install_run_active()
                or getattr(self, "_gui_handoff_prepare", False)
                or getattr(self, "_gui_launch_in_flight", False)
                or getattr(self, "_gui_quit_started", False)
            ):
                return
            from .. import process_guard, sp_flash_gui
            if not sp_flash_gui.is_sp_flash_gui_supported():
                return
            if process_guard.install_session_active() or process_guard.is_flash_tool_process_running():
                return
            model = getattr(self, "_package_model", "") or ""
            sp_flash_gui.update_sp_history_ini(model=model)
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

    def _drag_enter_firmware(self, event) -> bool:
        from ..drop_install import first_accepted_drop

        if self._install_run_active() or getattr(self, "_drop_check_active", False):
            event.ignore()
            return False
        paths = self._local_paths_from_mime(event.mimeData())
        if first_accepted_drop(paths) is None:
            event.ignore()
            return False
        event.acceptProposedAction()
        overlay = getattr(self, "_drop_overlay", None)
        if overlay is None:
            return True
        overlay._label.setText(tr("drop_firmware_here"))
        overlay.setGeometry(self.rect())
        overlay.raise_()
        overlay.show()
        return True

    def _hide_drop_overlay_if_outside(self) -> None:
        overlay = getattr(self, "_drop_overlay", None)
        if overlay is None:
            return
        # Moving between child widgets sends a drag-leave to each one; only a
        # cursor that has actually left the window retires the overlay.
        if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
            overlay.hide()

    def _drop_firmware(self, event) -> None:
        from ..drop_install import first_accepted_drop

        overlay = getattr(self, "_drop_overlay", None)
        if overlay is not None:
            overlay.hide()
        path = first_accepted_drop(self._local_paths_from_mime(event.mimeData()))
        if path is None:
            event.ignore()
            return
        event.acceptProposedAction()
        if self._install_run_active() or getattr(self, "_drop_check_active", False):
            return
        self._start_drop_check(path)

    def accept_local_package(self, path) -> bool:
        """Install a package opened from outside the window.

        Taskbar/dock drops and file-open events arrive after the window exists;
        this routes them through the same check as a drop onto the window.
        """
        from ..drop_install import classify_drop

        candidate = Path(path)
        if classify_drop(candidate) is None:
            return False
        self._show_from_tray()
        if self._install_run_active() or getattr(self, "_drop_check_active", False):
            return False
        self._start_drop_check(candidate)
        return True

    def eventFilter(self, watched, event):  # noqa: N802
        """Catch a firmware drag over any child of the window.

        Qt delivers drag events to the widget under the cursor. Without this
        filter, Settings, a donation card or a scroll viewport would swallow a
        drop the window itself accepts. Other drags are left to the widget.
        """
        if isinstance(watched, QWidget) and watched.window() is self:
            kind = event.type()
            if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
                return self._drag_enter_firmware(event)
            if kind == QEvent.Type.DragLeave:
                self._hide_drop_overlay_if_outside()
                return False
            if kind == QEvent.Type.Drop:
                from ..drop_install import first_accepted_drop

                if first_accepted_drop(self._local_paths_from_mime(event.mimeData())) is None:
                    return False
                self._drop_firmware(event)
                return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):  # noqa: N802
        self._drag_enter_firmware(event)

    def dragMoveEvent(self, event):  # noqa: N802
        self._drag_enter_firmware(event)

    def dragLeaveEvent(self, event):  # noqa: N802
        self._hide_drop_overlay_if_outside()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        self._drop_firmware(event)

    def _start_drop_check(self, path: Path) -> None:
        """Show a throbber, then install only if the scatter and its images exist.

        The drop is a local package, so it does not become an installed-version record.
        """
        if getattr(self, "_drop_check_active", False):
            return
        self._drop_check_active = True
        from .. import catalog

        name = catalog.format_install_heading("", "")
        self._package_path = str(path)
        self._package_name = name
        self._package_model = ""
        page = getattr(self, "_select_page", None)
        if page is not None:
            page._current_installed_release_info = None
            page._current_package_path = str(path)
            page._current_package_name = name
            page._current_package_model = ""
        self._flash_page.set_package_name(name)
        self._flash_page.set_model("")
        self._flash_page.show_drop_check(name)
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
        from .. import catalog
        from ..fw_package import locate_package, package_install_heading

        source = Path(getattr(worker, "path", "") or flash_path)
        model = ""
        name = catalog.format_install_heading("", "")
        try:
            # An archive's extracted files are not at flash_path (which is the
            # archive itself), so only inspect a folder/scatter drop here. Do
            # not scan the archive's parent: it may hold an unrelated package.
            folder = Path(flash_path)
            pkg = None
            if folder.is_dir():
                pkg = locate_package(
                    folder,
                    require_ini=False,
                    source_names=(source.name,) if source.name else (),
                )
            if pkg is not None:
                model = pkg.model or ""
                name = package_install_heading(pkg)
        except Exception:
            logger.debug("Could not read dropped package metadata", exc_info=True)

        page = getattr(self, "_select_page", None)
        if page is not None:
            page._current_installed_release_info = None
            page._current_package_path = flash_path
            page._current_package_name = name
            page._current_package_model = model
            page._current_selected_rel = None
            page._details_package = None
            page._raw_release_pixmap = None
        self._on_package_selected(flash_path, name, model)
        if page is not None:
            page._remember_gui_local(flash_path, model, name)
        self._sync_sp_gui_button_visibility()
        self._sync_settings_sp_gui_visibility()

    def _start_manifest_worker(self, force_refresh: bool = False):
        if getattr(self, "_manifest_worker", None) is not None:
            return
        worker = ManifestWorker(self, force_refresh=force_refresh)
        self._manifest_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(
            lambda entries: self._on_manifest_worker_finished(worker, entries)
        )
        worker.start()

    def _on_manifest_worker_finished(self, worker, entries):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_manifest_worker", None):
            # The window lives for days: a finished worker must not latch this
            # slot shut, or the catalogue could only ever be fetched once.
            self._manifest_worker = None
            self._on_manifest_loaded(entries)

    def _check_remote_flags_startup(self):
        try:
            from ..remote_flags import RemoteFlagsWorker, is_remote_flag_active
            if is_remote_flag_active("reset_all"):
                self.reset_all_configuration_and_caches()
                return
            worker = RemoteFlagsWorker(self)
            worker.flags_updated.connect(self._on_remote_flags_updated)
            self._active_workers.add(worker)
            worker.start()
        except Exception:
            pass

    def _on_remote_flags_updated(self, flags: dict):
        if flags.get("reset_all"):
            self.reset_all_configuration_and_caches()
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()


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
                self._sync_mac_header_margins()
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
        self._select_page.set_install_guard(self._guard_install_first)
        QTimer.singleShot(0, self._prefill_fw_package)
        # The themes/fonts stage spans this window and the install card, so the
        # same journey object owns its state for both.
        self.themes = ThemesJourney()
        self._flash_page = FlashPage(journey=self.themes)
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
        self._notices_page = NoticesPage()
        self._notices_page.title_changed.connect(self._update_header_title)
        paint_window_surface(self._notices_page)
        self._stack.addWidget(self._notices_page)
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
        self._settings_page.install_rockbox_themes_requested.connect(
            self._on_settings_install_rockbox_themes
        )
        self._settings_page.refresh_themes_requested.connect(self._on_refresh_themes)
        self._settings_page.themes_auto_install_changed.connect(
            self._on_themes_auto_install_changed
        )

        # The Notices rail row exists only while there is something to read:
        # dismissing the last notice removes it, and a new notice id in the
        # feed brings it back.
        try:
            from ..notice_service import NoticeService

            _notices = NoticeService.instance()
            self._notices_available = bool(_notices.active_notices)
            # Clicks on a system notification land on this window, so they
            # must be routable before the first one can be delivered below.
            app = QApplication.instance()
            if app is not None:
                app._notification_click_handler = self._on_notification_clicked
            _notices.notices_ready.connect(self._notices_slot)
            # Select Software starts a fetch while it is being built, before
            # this signal could be heard: take its current answer now, then
            # refetch once after the first paint so a changed feed is noticed.
            _notices.replay()
            # Notices must arrive while the window is out of the way too: the
            # timer lives with the service, not with the notices page, and a
            # force-refresh checks the tiny XML file rather than waiting hours.
            _notices.start_passive_refresh()
            QTimer.singleShot(700, _notices.fetch_async)
        except Exception:
            logger.debug("Notice notifications could not be connected", exc_info=True)

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
        self._settings_btn.clicked.connect(self._on_settings_nav_clicked)
        layout.addWidget(self._settings_btn)
        self._nav_buttons["nav_settings"] = (self._settings_btn, _PAGE_SETTINGS)
        self._nav_btn_group.addButton(self._settings_btn, _PAGE_SETTINGS)

        # Notices sits under Settings and follows the same hide rules.
        self._notices_btn = self._make_nav_button(tr("nav_notices"), "bell", checkable=True)
        self._notices_btn.clicked.connect(lambda: self._nav_to_page(_PAGE_NOTICES))
        layout.addWidget(self._notices_btn)
        self._nav_buttons["nav_notices"] = (self._notices_btn, _PAGE_NOTICES)
        self._nav_btn_group.addButton(self._notices_btn, _PAGE_NOTICES)

        # Primary pages stay at the top. Everything else sits with the language
        # selector at the bottom of the window.
        layout.addStretch(1)

        self._aux_nav_buttons = []

        if platform.system() == "Linux" and not paths.IS_MAC:
            self._linux_setup_btn = self._make_nav_button(tr("nav_linux_setup"), "tools")
            self._linux_setup_btn.clicked.connect(self._show_linux_setup)
            self._linux_setup_btn.setVisible(False)
            self._linux_setup_btn.setEnabled(False)
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
            getattr(self, "_notices_page", None),
        ):
            if page is not None and hasattr(page, "refresh_theme") and callable(page.refresh_theme):
                page.refresh_theme()

        # The inline update card paints from the live palette and renders its
        # release notes to HTML once. Rebuild that document when the OS
        # appearance changes so its text cannot keep the old theme's colour.
        notice_host = getattr(self, "_notice_host", None)
        if notice_host is not None:
            for child in notice_host.findChildren(QWidget):
                refresh = getattr(child, "refresh_theme", None)
                if callable(refresh):
                    refresh()

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
        if getattr(self, "_select_size_hold", None) is not None:
            # The catalogue pins the size: an edge drag cannot change it, so it
            # must not offer the resize cursor or start a resize either.
            return None
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
        self._settings_page.install_app_requested.connect(self._offer_portable_app_install)
        # The app's own no-progress countdown, shown on the install card.
        self.service.attempt_timeout.connect(self._flash_page.set_attempt_timeout)
        # Time remaining, measured from the install's own write rate (the
        # worker knows the byte totals; the card only knows the clock).
        self.service.eta_changed.connect(self._on_worker_eta)
        self._flash_page.on_cancel(self._on_cancel_flash)
        self._flash_page.on_cancel_wait(self._on_cancel_wait)
        self._flash_page.on_install_retry(self._on_progress_retry)
        self._flash_page.on_power_off_continue(self._on_power_off_continue)
        self._flash_page.on_try_anyway(self._on_keep_data_try_anyway)
        self._flash_page.on_install_done(self._on_install_done)
        self._flash_page.install_app_requested.connect(self._install_portable_app)
        self._error_page.on_retry(self._on_progress_retry)
        self._error_page.on_reconnect(self._on_reconnect)
        self._error_page.on_reselect(lambda: self._nav_to_page(_PAGE_SELECT))
        self._error_page.on_view_log(self._show_diagnostics)
        self._error_page.on_open_sp_gui(self._open_sp_flash_tool_gui)
        self._retry_page.on_cancel(self._on_cancel_flash)
        self.service.step_changed.connect(self._on_step_changed)
        # Coalesced, not direct: a running tool can emit several progress lines
        # per percent and every queued signal is an event-loop round trip.
        self.service.progress.connect(self._queue_progress)
        self.service.action_changed.connect(self._flash_page.update_action)
        if hasattr(self.service, "partition_progress_changed"):
            self.service.partition_progress_changed.connect(
                self._queue_partition_progress
            )
        self.service.log_message.connect(self._on_log_message)
        self.service.flash_finished.connect(self._on_flash_finished)
        self.service.device_found.connect(self._on_device_found)
        self.service.device_lost.connect(self._on_device_lost)
        if hasattr(self.service, "usb_disconnected"):
            self.service.usb_disconnected.connect(self._on_device_lost)
        if hasattr(self.service, "getting_ready"):
            self.service.getting_ready.connect(self._on_getting_ready)
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
        QTimer.singleShot(0, self._maybe_offer_deferred_app_update)

    def _on_prep_started(self, name, model):
        """Zip or archive extraction belongs on the install card, not the catalogue."""
        self._download_active = False
        self._prep_active = True
        self._install_failed = False
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
            from ..flash_service import is_storage_full_error
            if is_storage_full_error(result):
                self._show_install_failure("OUT_OF_STORAGE")
                return
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
                self._sync_mac_header_margins()
            elif sys.platform == "win32":
                from .glass import apply_windows_acrylic, apply_windows_dark_titlebar

                apply_windows_dark_titlebar(self, is_dark())
                apply_windows_acrylic(self, dark=is_dark())
        finally:
            self._reasserting_chrome = False

    def _sync_mac_header_margins(self) -> None:
        """Align body layout cleanly under the 44px title bar across macOS appearance states."""
        if sys.platform != "darwin" or not can_inline_title_with_window_controls():
            return
        central = self.centralWidget()
        if central is None:
            return
        layout = central.layout()
        if layout is None:
            return
        cw_y = central.geometry().y()
        needed_top = max(0, int(HEADER_CONTENT_HEIGHT) - cw_y)
        m = layout.contentsMargins()
        if m.top() != needed_top:
            layout.setContentsMargins(m.left(), needed_top, m.right(), m.bottom())

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
                # No programmatic resize may land mid-gesture: it makes the
                # window stutter and can cancel the drag outright.
                self._user_dragging = True
                self._stop_size_anim_for_drag()
                if begin_unified_caption_drag(self):
                    # Native drag: do not also run the manual mover.
                    self._mw_dragging = False
                    self._mw_drag_offset = None
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
        if getattr(self, "_user_dragging", False):
            self._user_dragging = False
            QTimer.singleShot(0, self._adjust_window_geometry)
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
        elif event.type() == QEvent.Type.Show:
            # Coming back from the tray: the hidden window had paused these.
            self._set_window_polling(True)

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
            self._sync_mac_header_margins()
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
                self._sync_mac_header_margins()
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
        # Ctrl/⌘+G: hardware stages complete themselves so the interface can
        # be walked by hand without a player. Cmd arrives as ControlModifier on
        # macOS, so both are accepted; no other chord is, leaving text-field
        # shortcuts alone.
        mods = event.modifiers()
        if (
            event.key() == Qt.Key_G
            and bool(mods & (Qt.ControlModifier | Qt.MetaModifier))
            and not (mods & (Qt.AltModifier | Qt.ShiftModifier))
        ):
            self.toggle_install_simulation()
            event.accept()
            return
        super().keyPressEvent(event)

    def toggle_install_simulation(self) -> bool:
        """Ctrl/⌘+G: skip the hardware waits so UI routes can be walked.

        Off at every launch. The run output records the switch, because a
        firmware or themes stage that finishes in seconds with no player
        attached should never be mistaken for a real install.
        """
        from .. import simulation

        on = simulation.toggle()
        self._append_log(
            "Install simulation on: firmware and themes stages complete themselves "
            "(Ctrl/Cmd+G turns it off)."
            if on
            else "Install simulation off: installs use the player again."
        )
        return on

    def reset_all_configuration_and_caches(self):
        """Completely reset app config back to default, opt credits/donations back in, and clear caches."""
        from .. import device_tracking, downloads, config
        from ..i18n import translator

        # 1. Clear QSettings across all namespaces. The battery reminder keeps
        # its dismissal (closing that toast is permanent), but the install
        # card tips do not: R is how a user who wants the How? tip or the
        # keep-data explanation back gets them, so their dismissals go with the
        # rest of the settings and the card offers them again.
        def _dismissals(store):
            return {
                key: store.value(key)
                for key in store.allKeys()
                if key == "battery_hint_dismissed"
            }

        s = QSettings("innioasis", "updater")
        s_caps = QSettings("Innioasis", "UpdaterCE")
        s_ref = QSettings("Innioasis", "Innioasis Updater")
        kept = [
            (store, _dismissals(store)) for store in (s, s_caps, s_ref)
        ]
        for store in (s, s_caps, s_ref):
            store.clear()
        for store, dismissals in kept:
            for key, value in dismissals.items():
                store.setValue(key, value)

        # Notice read-state is cleared with the settings: dismissed notices
        # come back, including ones dismissed earlier in this session.
        try:
            from ..notice_service import NoticeService

            notices = NoticeService.instance()
            notices.reset_all_read_state()
            self._notices_available = bool(notices.active_notices)
            if hasattr(self, "_notices_page") and hasattr(self._notices_page, "refresh_from_service"):
                self._notices_page.refresh_from_service()
            self._apply_notices_nav_visibility()
        except Exception as e:
            logger.debug("Notice read-state reset failed: %s", e)

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

        # Refresh notices and read-state after cache clearing so the cache is re-seeded.
        try:
            from ..notice_service import NoticeService

            notices = NoticeService.instance()
            notices.reset_all_read_state()
            self._notices_available = bool(notices.active_notices)
            if hasattr(self, "_notices_page") and hasattr(self._notices_page, "refresh_from_service"):
                self._notices_page.refresh_from_service()
            self._apply_notices_nav_visibility()
        except Exception as e:
            logger.debug("Notice read-state reset after cache clear failed: %s", e)

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
            # The tips were just un-dismissed, and a reveal made in this session
            # is memory too: a card already on screen must offer its How? link
            # and keep-data explanation again, not keep the reset-time state.
            try:
                if hasattr(self._flash_page, "_paperclip_revealed"):
                    self._flash_page._paperclip_revealed = False
                if hasattr(self._flash_page, "_keep_data_warning_revealed"):
                    self._flash_page._keep_data_warning_revealed = False
                # Only a card that is actually showing needs redrawing; the
                # next visit refreshes its hints on the way in.
                stack = getattr(self, "_stack", None)
                if stack is None or stack.currentWidget() is self._flash_page:
                    for name in ("_update_paperclip_hint", "_update_keep_data_warning"):
                        refresh = getattr(self._flash_page, name, None)
                        if callable(refresh):
                            refresh()
                    sync_band = getattr(self._flash_page, "_sync_wait_band_page", None)
                    if callable(sync_band):
                        sync_band()
            except Exception:
                logger.debug("Could not restore the install hints after reset", exc_info=True)

        self._apply_donation_visibility(False)
        if hasattr(self, "_select_page"):
            if hasattr(self._select_page, "reset_selection_and_filters"):
                self._select_page.reset_selection_and_filters()
            elif hasattr(self._select_page, "_check_battery_toast_visibility"):
                self._select_page._check_battery_toast_visibility()

        self._nav_to_page(_PAGE_SELECT)
        self.statusBar().showMessage(tr("status_app_reset_defaults"), 4000)
        self._append_log(tr("log_app_reset_defaults"))

    def _install_themes_shortcut(self) -> None:
        """Ctrl+T (Cmd+T on macOS) starts the Rockbox themes & fonts stage on demand."""
        from PySide6.QtGui import QKeySequence, QShortcut

        # Qt maps "Ctrl" to the Command key on macOS, so one sequence covers both.
        shortcut = QShortcut(QKeySequence("Ctrl+T"), self)
        shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        shortcut.activated.connect(self.start_rockbox_themes_manually)
        self._themes_shortcut = shortcut

        # Cmd+U / Cmd+W (Ctrl+U / Ctrl+W elsewhere) opens the App updates card on demand, for testing.
        update_test = QShortcut(QKeySequence("Ctrl+U"), self)
        update_test.setContext(Qt.ShortcutContext.WindowShortcut)
        update_test.activated.connect(self._show_update_test)
        self._update_test_shortcut = update_test

        update_test_w = QShortcut(QKeySequence("Ctrl+W"), self)
        update_test_w.setContext(Qt.ShortcutContext.WindowShortcut)
        update_test_w.activated.connect(self._show_update_test)
        self._update_test_shortcut_w = update_test_w

    def _show_update_test(self) -> None:
        """Present the App updates card from the latest release, even if not newer."""
        from ..updates import UpdateChecker
        from ..config import UPDATE_REPO

        worker = getattr(self, "_update_test_worker", None)
        if worker is not None and worker.isRunning():
            return
        worker = _SimulatedUpdateWorker(UpdateChecker(UPDATE_REPO), self)
        self._update_test_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(lambda info: self._on_update_test_done(worker, info))
        worker.start()

    def _on_update_test_done(self, worker, info) -> None:
        self._active_workers.discard(worker)
        if info is None or not getattr(info, "tag", ""):
            self._present_message(
                tr("nav_check_updates"),
                tr("update_check_failed"),
            )
            return
        self._notify_app_update(info, force=True)
        self._offer_app_update(info)

    def _manual_themes_model(self) -> str:
        """Y1 or Y2 for a manually started themes stage."""
        candidates = []
        page = getattr(self, "_select_page", None)
        if page is not None:
            try:
                candidates.append(page.current_model())
            except Exception:
                pass
        candidates.append(getattr(self, "_package_model", "") or "")
        for value in candidates:
            upper = str(value or "").upper()
            if "Y2" in upper:
                return "Y2"
            if "Y1" in upper:
                return "Y1"
        return "Y1"

    def _on_settings_install_rockbox_themes(self) -> None:
        """Triggered from Settings card: install Rockbox 360p themes."""
        if self._install_run_active():
            self._append_log("Rockbox themes install ignored: an install is already in progress.")
            return

        from ..config import is_offline_mode
        offline = is_offline_mode()
        model = self._manual_themes_model()
        self._append_log(f"Starting Rockbox 360p themes installation for {model}...")

        if not offline:
            self._append_log("[Rockbox Themes] Checking for latest 360p themes from GitHub...")
            # The download runs alongside the stage. Connecting the player and
            # booting into Rockbox takes longer than the pack fetch, so the
            # install never waits on the network; the status line reports it.
            self._start_theme_pack_refresh()
        else:
            self._append_log("[Rockbox Themes] Offline mode active: using cached 360p themes and the bundled fonts pack.")
        self._theme_install_pending = False
        self.start_rockbox_themes_manually()

    def _on_refresh_themes(self) -> None:
        """Manual refresh from the Rockbox themes settings card."""
        if self._install_run_active():
            self._append_log("Theme refresh ignored: an install is already in progress.")
            return
        self._theme_install_pending = False
        self._start_theme_pack_refresh()

    def _start_theme_pack_refresh(self) -> None:
        from ..theme_pack import ThemePackUpdateWorker

        active = getattr(self, "_theme_update_worker", None)
        try:
            if active is not None and active.isRunning():
                return
        except RuntimeError:
            active = None
        from time import monotonic

        worker = ThemePackUpdateWorker(parent=self)
        self._theme_update_worker = worker
        self._theme_update_started = monotonic()
        self._theme_update_last_bytes = 0
        self._theme_update_last_time = self._theme_update_started
        worker.progress.connect(self._on_theme_update_progress)
        worker.finished.connect(self._on_theme_update_finished)
        worker.finished.connect(worker.deleteLater)
        if hasattr(self, "_settings_page"):
            self._settings_page.set_themes_refreshing(True, tr("settings_themes_refreshing"))
        worker.start()

    def _on_theme_update_progress(self, downloaded: int, total: int) -> None:
        from time import monotonic
        from ..progress_model import format_size_bytes

        now = monotonic()
        elapsed = max(0.001, now - getattr(self, "_theme_update_last_time", now))
        delta = max(0, int(downloaded) - getattr(self, "_theme_update_last_bytes", 0))
        self._theme_update_last_time = now
        self._theme_update_last_bytes = int(downloaded)
        speed = format_size_bytes(delta / elapsed)
        if total > 0:
            progress = tr("settings_themes_progress_fmt").format(
                percent=int(round(100 * downloaded / total)),
                done=format_size_bytes(downloaded),
                total=format_size_bytes(total),
            )
        else:
            progress = format_size_bytes(downloaded)
        status_text = tr("settings_themes_refreshing_progress").format(
            progress=progress, speed=speed
        )
        if hasattr(self, "_settings_page"):
            self._settings_page.set_themes_refreshing(True, status_text)
        try:
            if self.themes.active and hasattr(self, "_flash_page"):
                self._flash_page.set_theme_download_status(status_text)
        except Exception:
            pass

    def _on_theme_update_finished(self, ok: bool, status: str, count: int) -> None:
        if hasattr(self, "_settings_page"):
            self._settings_page.set_themes_refreshing(False)
        try:
            if hasattr(self, "_flash_page"):
                self._flash_page.set_theme_download_status("")
        except Exception:
            pass
        pending = bool(getattr(self, "_theme_install_pending", False))
        self._theme_install_pending = False
        if ok and status == "merged":
            self._append_log(f"[Rockbox Themes] Successfully downloaded and merged {count} latest themes into 360p pack.")
            if hasattr(self, "_settings_page"):
                self._settings_page.set_themes_status(tr("settings_themes_refresh_done"))
        elif status == "offline_fallback":
            self._append_log("[Rockbox Themes] Offline or download unreachable; using stored 360p theme pack.")
            if hasattr(self, "_settings_page"):
                self._settings_page.set_themes_status(tr("settings_themes_refresh_failed"))
        if pending:
            self.start_rockbox_themes_manually()

    def start_rockbox_themes_manually(self) -> bool:
        """Begin the Rockbox themes & fonts stage without a flash having just run.

        Does nothing while an install is in progress. Otherwise it opens the same
        guided stage a Rockbox install hands off to: power the player on, connect
        USB, then the themes and fonts are pushed over ADB.
        """
        if self._install_run_active():
            self._append_log("Rockbox themes shortcut ignored: an install is already running.")
            return False
        model = self._manual_themes_model()
        if model != "Y1":
            self._append_log("Rockbox themes are not installed for Y2; the themes stage was not started.")
            return False
        self._append_log(f"Rockbox themes & fonts stage started manually for {model}.")
        self._package_model = model
        self._install_complete = False
        self._install_failed = False
        self.themes.reset_completion()
        self.themes.set_apk("", "")
        self._compact_install_screen = False
        self._is_360p_rockbox = True
        self._flash_page.set_model(model)
        self._nav_to_page(_PAGE_FLASH)
        self._show_rockbox_power_on_prompt(model)
        return True

    def _apk_install_info(self) -> tuple[str, str]:
        """Return ``(apk_path, apk_url)`` when the selected release comes from an ``apk_repo``."""
        page = getattr(self, "_select_page", None)
        if page is None:
            return "", ""
        info = getattr(page, "current_installed_release_info", lambda: None)() or {}
        apk_repo = str(info.get("apk_repo") or "")
        apk_url = str(info.get("apk_url") or "")
        apk_path = str(info.get("apk_path") or "")
        if not apk_repo:
            pkg = getattr(page, "_details_package", None)
            rel = getattr(page, "_current_selected_rel", None)
            apk_repo = str(
                (rel.get("apk_repo") if isinstance(rel, dict) else "")
                or getattr(pkg, "apk_repo", "")
                or ""
            )
            if apk_repo and isinstance(rel, dict):
                apk_url = str(rel.get("apk_url") or "")
                apk_name = Path(str(rel.get("apk_name") or "app.apk")).name
                if apk_url and not apk_path:
                    from .. import downloads

                    slug = str(getattr(pkg, "slug", "") or "app")
                    tag = str(rel.get("tag_name") or "latest")
                    apk_path = str(downloads.downloads_dir() / f"{slug}_{tag}_{apk_name}")
        if not (apk_repo and (apk_url or apk_path)):
            return "", ""
        return apk_path, apk_url

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
        # Themes are Y1-only, and only for a Rockbox-named, non-240p release.
        wanted = bool(model == "Y1" and is_rockbox and not is_240p)
        # The per-install tickbox is an opt-out: checked by default, cleared by
        # anyone who would rather install the firmware on its own. The Settings
        # auto-install toggle still has the final say on the automatic stage.
        opt_out = getattr(page, "_local_rockbox_themes_cb", None) if page else None
        if wanted and opt_out is not None and not opt_out.isChecked():
            wanted = False
        return wanted, model

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

    def _show_rockbox_power_on_prompt(
        self, model: str, *, after_firmware: bool = False, on_continue=None
    ) -> None:
        """Ask the user to boot the flashed player before ADB/theme setup."""
        self._install_complete = True
        self._install_failed = False
        self.themes.ask_power_on(model, after_firmware=after_firmware)
        prompt = tr(self._flash_page.rockbox_power_on_prompt_key(model)).format(model=model)
        self._flash_page.show_rockbox_power_on_prompt(
            prompt,
            on_continue=on_continue or self._continue_rockbox_power_on,
            on_recover=lambda: self.restore_original_software(model),
            after_firmware=after_firmware,
        )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()
        self._nav_to_page(_PAGE_FLASH)
        self._adjust_window_geometry()

    def _continue_rockbox_power_on(self) -> None:
        """Start the assets stage once the user has powered on the Rockbox player."""
        if not self.themes.power_on_pending:
            return
        model = self.themes.power_on_model or self._package_model
        after_firmware = self.themes.power_on_after_firmware
        self.themes.clear_power_on()
        donation_disabled = device_tracking.is_donation_install_prompt_disabled(self.settings)
        self._start_themes_stage(
            model, donation_disabled, already_powered_on=True, after_firmware=after_firmware
        )

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
        if self.themes.active:
            return True
        if self.themes.power_on_pending:
            return True
        if getattr(self, "_power_off_prompt", False):
            return True
        if getattr(self, "_keep_data_confirm_pending", False):
            return True
        if getattr(self, "_keep_data_erase_confirm_pending", False):
            return True
        if getattr(self, "_install_first_stage", ""):
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

    def _select_page_on_screen(self) -> bool:
        """True while the software catalogue owns the window."""
        stack = getattr(self, "_stack", None)
        return (
            self.isVisible()
            and stack is not None
            and stack.currentIndex() == _PAGE_SELECT
        )

    def _hold_select_window_size(self) -> None:
        """Pin the window to the size the catalogue fitted itself to.

        The catalogue measures its own content and the window follows it, so a
        border drag can only spread the two columns apart or leave an empty
        band under the last card. While that page is on screen its current
        size is the only one on offer. The app's own fits still run: each one
        releases the hold, sizes the window, and takes it again.

        A frame too short to show the catalogue is never pinned. The install
        card's own size animation can still be running when a cancel navigates
        back, and its end value is the card's compact height: freezing that
        would leave Select Software as a strip with the release rows and the
        Install button cut off. Such a frame stays free and is re-measured
        instead, so it can never become the catalogue's size.
        """
        if not self._select_page_on_screen():
            return
        floor_h = self._content_floor_height()
        if self.height() < floor_h:
            attempts = int(getattr(self, "_select_frame_refit_attempts", 0))
            if attempts < 4:
                self._select_frame_refit_attempts = attempts + 1
                QTimer.singleShot(0, self._refit_short_select_frame)
            return
        self._select_frame_refit_attempts = 0
        size = QSize(self.width(), self.height())
        if getattr(self, "_select_size_hold", None) is None:
            self._select_size_hold = (self.minimumSize(), self.maximumSize())
        self.setMinimumSize(size)
        self.setMaximumSize(size)

    def _refit_short_select_frame(self) -> None:
        """Re-measure the catalogue after a frame landed below its own floor.

        The height to reach is the page's, measured once the widgets are back
        on screen, so the window grows to the release rows rather than to a
        fixed number.
        """
        if not self._select_page_on_screen():
            return
        self._fit_window_to_page()

    def _release_select_window_size(self) -> None:
        """Lift the catalogue's fixed-size hold so a page can be fitted."""
        hold = getattr(self, "_select_size_hold", None)
        if hold is None:
            return
        self._select_size_hold = None
        previous_min, previous_max = hold
        self.setMinimumSize(previous_min)
        self.setMaximumSize(previous_max)

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
        solo_settings_card = bool(
            stack is not None
            and stack.currentIndex() == _PAGE_SETTINGS
            and getattr(self, "_settings_page", None) is not None
            and self._settings_page.focused_card() is not None
        )
        is_hugging = bool(
            getattr(self, "_hugging_install", False)
            or (stack is not None and stack.currentIndex() in (_PAGE_FLASH, _PAGE_SUPPORT))
            or solo_settings_card
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
                max_h = max(max_h, nav.minimumSizeHint().height() + chrome_h)
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
        if not fill_height:
            # Nothing may be resized past the auto-fit ceiling, including by a
            # border drag: the page's overflow scrolls, so extra height would
            # only leave an empty band. A page's own floor still wins.
            max_h = max(min_floor, min(max_h, MAXIMUM_AUTO_FIT_HEIGHT))
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
            or self.isFullScreen()
            or bool(self.windowState() & Qt.WindowFullScreen)
        ):
            return
        stack = getattr(self, "_stack", None)
        page = stack.currentWidget() if stack is not None else None
        if page is None:
            return
        if stack.currentIndex() == _PAGE_FLASH or getattr(self, "_hugging_install", False):
            # The install card gives a maximized frame back, so the hug runs
            # even then; it decides for itself whether to restore the frame.
            self._adjust_window_geometry()
            return
        if self.isMaximized() or bool(self.windowState() & Qt.WindowMaximized):
            return
        self._release_select_window_size()
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
        solo_settings_card = bool(
            stack.currentIndex() == _PAGE_SETTINGS
            and getattr(self, "_settings_page", None) is not None
            and self._settings_page.focused_card() is not None
        )
        is_support = bool(stack.currentIndex() == _PAGE_SUPPORT)
        # One installer window: the support page and a solo settings card keep
        # the window's own floor instead of hugging their card down to a strip.
        # Only a running install may take a shorter frame, because it is the
        # one screen that owns the whole window while the run is under way.
        floor_h = (
            MINIMUM_WINDOW_HEIGHT
            if (solo_settings_card or is_support)
            else self._content_floor_height()
        )
        if solo_settings_card or is_support:
            nav = getattr(self, "_nav_panel", None)
            if nav is not None:
                floor_h = max(floor_h, nav.minimumSizeHint().height() + chrome_h)
        target_h = max(floor_h, target_h)
        # The width the user chose is kept: only a page that cannot be drawn in
        # it (long translated words, a wide form) makes the window wider.
        target_w = max(self.width(), MINIMUM_WINDOW_WIDTH, need_w)
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            target_w = min(target_w, int(available.width()), MAXIMUM_WINDOW_WIDTH)
            target_h = min(target_h, int(available.height()))
            floor_w = min(floor_w, int(available.width()), MAXIMUM_WINDOW_WIDTH)
        if stack.currentIndex() == _PAGE_SETTINGS and not solo_settings_card:
            max_settings_h = min(int(available.height()) - 60, 560) if screen is not None else 560
            target_h = min(target_h, max(DEFAULT_WINDOW_HEIGHT, max_settings_h))
        # No page grows the window past the ceiling on its own. Content that
        # needs more (the Notices list, a long appeal) scrolls instead; the
        # page's floor still wins where it cannot be drawn any shorter.
        target_h = max(floor_h, min(target_h, MAXIMUM_AUTO_FIT_HEIGHT))
        if stack.currentIndex() == _PAGE_SELECT and getattr(self, "_select_frame_guard", False):
            # The frame was just handed back from an install card. This fit may
            # have measured the page before it laid out at that size, so it can
            # grow the window but never shrink it below the restored frame.
            self._select_frame_guard = False
            target_h = max(target_h, self.height())
        self._apply_window_size_limits(target_h)
        floor_w = max(MINIMUM_WINDOW_WIDTH, min(floor_w, target_w))
        if target_w != self.width() or target_h != self.height():
            self._animate_window_size(
                target_w, target_h, minimum=(floor_w, floor_h)
            )
        elif floor_w > self.minimumWidth():
            # Nothing to resize, but the page still must not be dragged
            # narrower than its cards.
            self.setMinimumWidth(floor_w)
        if not getattr(self, "_size_animating", False):
            # An animation re-takes the hold in _finish_size_anim; taking it
            # now would freeze the window at the size it is about to leave.
            self._hold_select_window_size()

    def _visible_rail_height(self) -> int:
        nav = getattr(self, "_nav_panel", None)
        if nav is None:
            return 0
        layout = nav.layout()
        if layout is None:
            return nav.minimumSizeHint().height()
        margins = layout.contentsMargins()
        total = margins.top() + margins.bottom()
        spacing = layout.spacing()
        visible_count = 0
        for i in range(layout.count()):
            item = layout.itemAt(i)
            w = item.widget()
            if w is not None and w.isVisible():
                total += max(w.sizeHint().height(), w.minimumSizeHint().height(), 24)
                visible_count += 1
        if visible_count > 1:
            total += spacing * (visible_count - 1)
        return max(total, 120)

    def _adjust_window_geometry(self):
        """Adjust window size to fit the content of the active page without wasted space."""
        if (
            self.isMinimized()
            or self.isFullScreen()
            or bool(self.windowState() & Qt.WindowFullScreen)
        ):
            return

        held = _mouse_button_held()
        if getattr(self, "_user_dragging", False) or held:
            # The user owns the frame while they move it, including a native
            # title-bar drag Qt never reports to our header. Resizing here
            # makes AppKit shift the window under the cursor, so retry after
            # the gesture instead.
            timer = getattr(self, "_drag_geometry_retry", None)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(250)
                timer.timeout.connect(self._adjust_window_geometry)
                self._drag_geometry_retry = timer
            if not timer.isActive():
                timer.start()
            # A native drag can swallow the release event: no button held means
            # the gesture is over, so a stale flag may clear.
            if not held:
                self._user_dragging = False
            return

        # If flash page is not yet set up, use minimum size to avoid crashes
        if not hasattr(self, "_flash_page"):
            self.setMinimumSize(MINIMUM_WINDOW_WIDTH, MINIMUM_WINDOW_HEIGHT)
            self._release_select_window_size()
            return

        self._release_select_window_size()
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
            or getattr(self, "_keep_data_confirm_pending", False)
            or getattr(self, "_keep_data_erase_confirm_pending", False)
        )
        # The flags outlive the card (a failed or finished install keeps its
        # state for when the user comes back), so they only decide the size
        # while the card is the page on screen. Any other page owns the window
        # and grows it back.
        hug_install = install_flags and curr_idx == _PAGE_FLASH

        if not hug_install and (
            self.isMaximized() or bool(self.windowState() & Qt.WindowMaximized)
        ):
            # A maximized frame belongs to the document pages the user is
            # reading. Nothing here resizes it.
            return
        if hug_install and (
            self.isMaximized() or bool(self.windowState() & Qt.WindowMaximized)
        ):
            # A maximized frame is a document window; the install card is a
            # compact wizard. One short card at the top of a full-height
            # empty column looked broken, so the card gives the frame back
            # before it is measured.
            self.showNormal()
            controls = getattr(self, "_window_controls", None)
            if controls is not None and hasattr(controls, "set_state"):
                controls.set_state(False)

        if hug_install:
            # Hug the progress card. The minimum comes from the wrapped copy
            # (package name, prompt, buttons), not a fixed height that clips it.
            page = self._flash_page
            generation = getattr(page, "view_generation", None)
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
                rail_min = (
                    self._visible_rail_height() + actual_chrome
                    if nav is not None
                    else 0
                )
                content_h = compact_install_height(
                    page_h=page_h,
                    prompt_h=prompt_h,
                    status_h=status_h,
                    inline_header=getattr(self, "_title_bar", None) is not None,
                    donations_disabled=donations_disabled,
                )
                # Every install stage hugs its card. A running card used to be
                # padded out to a width-derived "wizard" frame (52% of the
                # window width, 380-520px), so a short progress card sat above
                # a band of empty window. The card plus the small fit slack is
                # the whole height now; the rail's visible rows are the only
                # content that can need more.
                target_h = max(
                    content_h,
                    page_h + prompt_h + actual_chrome + 8,
                    rail_min,
                )
                # The progress card is capped like every other page: its own
                # overflow scrolls rather than pushing the window taller.
                target_h = min(target_h, MAXIMUM_AUTO_FIT_HEIGHT)
                # A running install only needs a new geometry when the target
                # actually changes. Do not skip solely because the view
                # generation is unchanged: a native/manual resize or a stale
                # pre-layout measurement can leave the frame taller than this
                # stage needs, and that is exactly what the compact fit fixes.
                if (
                    self._install_run_active()
                    and generation is not None
                    and generation == getattr(self, "_install_fit_generation", None)
                    and self.width() == target_w
                    and self.height() == target_h
                ):
                    return
                if generation is not None:
                    self._install_fit_generation = generation
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
            solo_settings_card = bool(
                curr_idx == _PAGE_SETTINGS
                and getattr(self, "_settings_page", None) is not None
                and self._settings_page.focused_card() is not None
            )
            min_w = self._minimum_window_width()
            min_h = (
                COMPACT_INSTALL_MIN_HEIGHT_NO_DONATIONS
                if solo_settings_card
                else self._content_floor_height()
            )
            self._hugging_install = False
            self.setMinimumSize(min_w, min_h)
            # Whatever page is on screen now owns the window size: leaving the
            # install card grows it back, a short page gives the room up.
            if curr_idx != _PAGE_FLASH:
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
            ):
                # Only a *running* install owns the frame. The finished/failed/
                # power-off flags outlive their card, and honouring them here
                # is why a large window stayed large long after the install
                # page (and its card) had been left behind.
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

    def _refit_catalogue_after_install(self) -> None:
        """Give Select Software its own height back after an install card.

        The install card hugs itself with a fixed minimum, and the catalogue
        measures itself from a layout that only runs once it is on screen. A
        fit made while the card is being torn down therefore measures the
        hidden page and keeps the card's compact frame — the catalogue came
        back as a strip too short to read after a cancel or a run that ended
        without a completion card. The floor is applied now, so the frame can
        never be shorter than the catalogue needs, and the content fit runs
        again once the page has been laid out at that size.
        """
        stack = getattr(self, "_stack", None)
        page = getattr(self, "_select_page", None)
        if stack is None or page is None or stack.currentWidget() is not page:
            return
        self._hugging_install = False
        self._release_select_window_size()
        # A size animation started for the install card is aimed at that card's
        # compact frame. Allowed to finish after this navigation, it re-applies
        # the frame (and its minimum) over the catalogue, which is how the page
        # came back as an unreadable strip after a cancel. Drop the run's own
        # animation here; the fits scheduled below measure the catalogue.
        self._stop_size_anim_for_drag()
        self._size_anim_minimum = None
        if not (
            self.isMinimized()
            or self.isMaximized()
            or self.isFullScreen()
            or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        ):
            self.setMinimumSize(
                self._minimum_window_width(), self._content_floor_height()
            )
        frame = getattr(self, "_pre_install_frame", None)
        self._pre_install_frame = None
        if frame is not None and not (
            self.isMinimized()
            or self.isMaximized()
            or self.isFullScreen()
            or bool(self.windowState() & (Qt.WindowMaximized | Qt.WindowFullScreen))
        ):
            # Give back the frame the run took, instead of measuring the page.
            # The card's compact height (and the caps the hug applied) are
            # lifted first: a resize is clamped by the maximum, so restoring
            # while the install cap is still in force would land short again.
            screen = self.screen()
            max_w, max_h = MAXIMUM_WINDOW_WIDTH, MAXIMUM_AUTO_FIT_HEIGHT
            if screen is not None:
                available = screen.availableGeometry()
                max_w = min(max_w, int(available.width()))
                max_h = min(max_h, int(available.height()))
            self.setMaximumSize(max_w, max_h)
            width = max(int(frame[0]), self._minimum_window_width())
            height = max(int(frame[1]), self._content_floor_height())
            self.setMinimumSize(self._minimum_window_width(), self._content_floor_height())
            self.resize(min(width, max_w), min(height, max_h))
            # A fit already queued from the catalogue's refill measures the page
            # before it has laid out at this size, so it may grow the window but
            # must not squeeze it back under the frame the run returned.
            self._select_frame_guard = True
            self._hold_select_window_size()
            return
        # One turn later: the page's rows and cards report their real heights
        # only after the layout that the navigation itself triggered.
        QTimer.singleShot(0, self._fit_window_to_select_content)
        QTimer.singleShot(0, self._fit_window_to_page)

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
            ):
                # Only a *running* install owns the frame. The finished/failed/
                # power-off flags outlive their card, and honouring them here
                # is why a large window stayed large long after the install
                # page (and its card) had been left behind.
                return
            stack = getattr(self, "_stack", None)
            page = getattr(self, "_select_page", None)
            if stack is None or page is None or stack.currentWidget() is not page:
                return
            if not self.isVisible() or stack.height() <= 0:
                self._pending_select_fit = True
                return
            # One measurement after the deferred tick: the refill has already
            # asked the layout to re-run. Flushing posted events here ran the
            # whole page relayout synchronously — up to five nested passes —
            # inside the click that changed the selection, which is what made
            # picking a release feel like the app had stalled.
            need = page.content_height_hint()
            stack.updateGeometry()
            self._fit_window_to_page()
        except Exception:
            logger.debug("Select content fit failed", exc_info=True)

    def _stop_size_anim_for_drag(self) -> None:
        """Cancel an in-flight fit animation before the user takes the frame."""
        anim = getattr(self, "_size_anim", None)
        if anim is not None:
            try:
                anim.stop()
            except Exception:
                pass
            self._size_anim = None
        self._size_animating = False

    def _animate_window_size(self, width: int, height: int, minimum=None) -> None:
        """Ease a programmatic resize. Reduced motion and a maximized window snap."""
        from .widgets import prefers_reduced_motion

        if getattr(self, "_user_dragging", False) or _mouse_button_held():
            # Never move the frame out from under a caption drag, including a
            # native title-bar drag Qt does not report to our header.
            self._install_fit_generation = None
            timer = getattr(self, "_drag_geometry_retry", None)
            if timer is None:
                timer = QTimer(self)
                timer.setSingleShot(True)
                timer.setInterval(250)
                timer.timeout.connect(self._adjust_window_geometry)
                self._drag_geometry_retry = timer
            if not timer.isActive():
                timer.start()
            return
        self._release_select_window_size()
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
        # A large change is a different window, not a nudge: easing 200px of
        # travel read as the old, larger window refusing to go away for the
        # whole animation. Snap those and keep the easing for small fits.
        jump = abs(self.width() - width) > 240 or abs(self.height() - height) > 240
        if (
            prefers_reduced_motion()
            or jump
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
        # Giving room back should not feel slower than taking it.
        shrinking = width < self.width() or height < self.height()
        anim.setDuration(150 if shrinking else 200)
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
        # The last animated resize leaves the newly exposed strip to the page
        # beneath the card. Only the card repaints itself on each percent, so
        # that strip could stay unpainted; ask the client area for one full
        # paint once the frame has settled.
        central = self.centralWidget()
        if central is not None:
            central.update()
        self._hold_select_window_size()
        # The stage change hides sidebar entries while the window is still
        # resizing, so a focused entry can lose its widget after the UI state
        # was applied. Re-check once the new size has settled.
        QTimer.singleShot(0, self._drop_stale_focus_frames)

    def _handoff_chrome(self) -> bool:
        """The disconnect hint and the download that leads to it."""
        return bool(
            getattr(self, "_gui_handoff_prepare", False)
            or getattr(self, "_gui_handoff_ready", False)
            or getattr(self, "_power_off_prompt", False)
        )

    def _apply_install_ui_state(self, active: bool):
        handoff = self._handoff_chrome()
        linux_setup = bool(getattr(self, "_linux_setup_flow_active", False))
        key = (
            bool(active),
            bool(handoff),
            bool(linux_setup),
        )
        if getattr(self, "_install_ui_key", None) == key:
            if not active and not linux_setup:
                self._reveal_sp_gui_button()
            self._drop_stale_focus_frames()
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

        select_entry = getattr(self, "_nav_buttons", {}).get("nav_select_package")
        select_btn = select_entry[0] if select_entry else None

        if active:
            # During install / extraction / prep stages: only show "Install Software"
            # and Diagnostics (simplified user facing install output).
            if select_btn is not None:
                select_btn.setVisible(True)
                select_btn.setEnabled(True)
            if hasattr(self, "_settings_btn"):
                self._settings_btn.setVisible(False)
                self._settings_btn.setEnabled(False)
            self._apply_notices_nav_visibility()
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
                self._linux_setup_btn.setEnabled(False)
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
        elif linux_setup:
            # During initial Linux Setup: lock the sidebar onto the renamed
            # "Linux Setup" button (_settings_btn) and hide all other navigation
            # buttons so nothing can take the user out of the setup screen.
            if select_btn is not None:
                select_btn.setVisible(False)
                select_btn.setEnabled(False)
            if hasattr(self, "_settings_btn"):
                self._settings_btn.setVisible(True)
                self._settings_btn.setEnabled(True)
                self._settings_btn.blockSignals(True)
                self._settings_btn.setChecked(True)
                self._settings_btn.blockSignals(False)
            self._apply_notices_nav_visibility()
            if hasattr(self, "_support_btn"):
                self._support_btn.setVisible(False)
                self._support_btn.setEnabled(False)
            self._set_help_row_visible(
                not device_tracking.is_donation_ui_disabled(self.settings)
            )
            if hasattr(self, "_log_btn"):
                self._log_btn.setVisible(False)
                self._log_btn.setEnabled(False)
            if hasattr(self, "_check_updates_btn"):
                self._check_updates_btn.setVisible(False)
                self._check_updates_btn.setEnabled(False)
            if hasattr(self, "_linux_setup_btn"):
                self._linux_setup_btn.setVisible(False)
                self._linux_setup_btn.setEnabled(False)
            if hasattr(self, "_sp_flash_tool_btn"):
                self._sp_flash_tool_btn.setVisible(False)
                self._sp_flash_tool_btn.setEnabled(False)
            if hasattr(self, "_install_output_btn"):
                self._install_output_btn.setVisible(False)
                self._install_output_btn.setEnabled(False)
            if hasattr(self, "_lang_label"):
                self._lang_label.setVisible(True)
            if hasattr(self, "_lang_combo"):
                self._lang_combo.setVisible(True)
        else:
            if select_btn is not None:
                select_btn.setVisible(True)
                select_btn.setEnabled(True)
            if hasattr(self, "_settings_btn"):
                self._settings_btn.setVisible(True)
                self._settings_btn.setEnabled(True)
            self._apply_notices_nav_visibility()
            if hasattr(self, "_support_btn"):
                self._support_btn.setVisible(True)
                self._support_btn.setEnabled(True)
            self._set_help_row_visible(True)
            if hasattr(self, "_log_btn"):
                self._sync_diagnostics_button()
            self._sync_update_button()
            if hasattr(self, "_linux_setup_btn"):
                self._linux_setup_btn.setVisible(False)
                self._linux_setup_btn.setEnabled(False)
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
        self._drop_stale_focus_frames()
        self._adjust_window_geometry()

    def _drop_stale_focus_frames(self) -> None:
        """Hide Qt's focus frame once the widget it follows is no longer shown.

        A sidebar entry that disappears while an install owns the window can
        still hold keyboard focus, and Qt keeps that widget's ``QFocusFrame``
        visible after the widget itself is hidden. The frame then paints its
        accent-blue outline at the last position it had — on top of otherwise
        empty space — which read as a stray blue dot on the install and
        completion screens. A frame with no visible widget under it indicates
        nothing, so it is hidden until focus moves somewhere real again.
        """
        from PySide6.QtWidgets import QFocusFrame

        for frame in self.findChildren(QFocusFrame):
            tracked = frame.widget()
            if tracked is None or not tracked.isVisibleTo(self):
                frame.hide()

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
            # Only the card's own flag titles the page. The state machine keeps
            # its terminal states across a return to Select Software, so keying
            # off them captioned the next attempt's download/prep/power-off
            # card with the previous attempt's lost-USB failure.
            if getattr(self, "_install_failed", False):
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
        if page_idx == _PAGE_NOTICES:
            notices_page = getattr(self, "_notices_page", None)
            if notices_page is not None:
                return notices_page.display_title()
            return tr("nav_notices")
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
        failed = getattr(self, "_install_failed", False)
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
        elif page_idx in (_PAGE_NOTICES, _PAGE_NOTICE):
            # One notice's detail is a child view of the Notices page, so that
            # row stays lit rather than leaving the previously open tab's row.
            checked = getattr(self, "_notices_btn", None)
        elif page_idx == _PAGE_FLASH:
            checked = self._nav_buttons.get("nav_select_package", (None,))[0]
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
            getattr(self, "_notices_btn", None),
            getattr(self, "_log_btn", None),
            getattr(self, "_support_btn", None),
        ):
            if btn is not None:
                btn.update()

    def _nav_to_page(self, page_idx):
        SidebarButton.dismiss_focus_rings()
        previous_page = self._stack.currentIndex() if hasattr(self, "_stack") else page_idx
        if previous_page == _PAGE_SELECT and page_idx != _PAGE_SELECT:
            # Remember the catalogue's own frame before anything can re-measure
            # it. The size the page pinned for itself is that frame; the current
            # geometry is the fallback. This is exactly what the user must get
            # back if the run they are entering is cancelled: once the install
            # card has taken the window, measuring the catalogue describes the
            # squeezed page rather than the one it was.
            hold = getattr(self, "_select_size_hold", None)
            if hold is not None:
                self._pre_install_frame = (int(hold[0].width()), int(hold[0].height()))
            else:
                self._pre_install_frame = (self.width(), self.height())
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
        if getattr(self, "_linux_setup_flow_active", False) and page_idx != _PAGE_SETTINGS:
            self._highlight_nav(_PAGE_SETTINGS, False)
            return
        # The Notices row is hidden once every notice has been dismissed; a
        # direct call (a stale toast, a shortcut) must not open a page the
        # sidebar no longer offers.
        if page_idx == _PAGE_NOTICES and not getattr(self, "_notices_available", False):
            self._highlight_nav(self._stack.currentIndex(), self._install_run_active())
            return
        self._stack.setCurrentIndex(page_idx)
        if page_idx == _PAGE_SELECT:
            QTimer.singleShot(0, self._reload_refreshed_releases)
        if page_idx == _PAGE_FLASH:
            self._install_fit_generation = None
        # A row highlights itself when clicked, but navigation started anywhere
        # else -- a notice toast, a shortcut, a hand-off from the flash page --
        # has to move the selection too, or the old row stays lit. This is the
        # helper the blocked-navigation paths already use: it sets the row
        # without signalling and without leaving a focus ring behind.
        if hasattr(self, "_nav_buttons"):
            # Early startup navigation runs before the rail exists.
            self._highlight_nav(page_idx, self._install_run_active())
        # The battery reminder belongs to Select Software alone: the Notices
        # page is for reading announcements, not for setup reminders.
        select_page = getattr(self, "_select_page", None)
        if select_page is not None and hasattr(select_page, "_battery_toast"):
            if page_idx == _PAGE_SELECT:
                select_page._check_battery_toast_visibility()
            else:
                select_page._battery_toast.setVisible(False)
        if page_idx == _PAGE_NOTICES:
            # Opening the page is the refresh gesture: cached notices appear at
            # once, and a throbber covers the background fetch that follows.
            notices_page = getattr(self, "_notices_page", None)
            if notices_page is not None and hasattr(notices_page, "refresh"):
                notices_page.refresh()
        if page_idx != _PAGE_SELECT:
            # The catalogue's fixed-size hold belongs to that page alone.
            self._release_select_window_size()
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

    def _current_firmware_package(self):
        """The selected catalogue package, when one is known.

        A local package has no catalogue row; its install still gets the
        manifest/learned checks, but nothing can be looked up from the page.
        """
        page = getattr(self, "_select_page", None)
        pkg = getattr(page, "_details_package", None) if page is not None else None
        if pkg is None and page is not None and hasattr(page, "current_package"):
            try:
                pkg = page.current_package()
            except Exception:
                pkg = None
        if pkg is None and self._package_model:
            from .. import catalog

            for candidate in catalog.packages_for_model(self._package_model):
                name = str(getattr(candidate, "name", "") or "").casefold()
                if name and name in (self._package_name or "").casefold():
                    pkg = candidate
                    break
        return pkg

    def _apply_pmt_format_state(self, pkg=None, model: str = "") -> bool:
        """Apply the manifest/learned Format All + Download requirement.

        This only changes the SP console command's mode and the Keep Data
        choice; extraction, validation, retries and failover remain untouched.
        """
        from .. import pmt_format

        pkg = pkg or self._current_firmware_package()
        if pkg is not None:
            self._keep_data_pkg = pkg
        model = model or self._package_model or getattr(pkg, "model", "") or ""
        required = pmt_format.pmt_format_required(
            pkg,
            model=model,
            software=self._package_name,
            settings=self.settings,
        )
        if pkg is None and getattr(self, "_format_all_required", False):
            required = True
        self._format_all_required = bool(required)
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.set_format_all_required(required, model)
        if required:
            self._keep_data = False
        return bool(required)

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
        # A local package can declare an install_first prerequisite just like a
        # catalogue release. The portability of the package must not bypass the
        # question that keeps an overlay off an unprepared player.
        if not self._guard_local_install_first(path):
            return
        self._remember_catalogue_section()
        self._download_active = False
        self._prep_active = False
        self._install_failed = False
        self._package_path = path
        self._package_name = name
        self._package_model = model or ""
        page = getattr(self, "_select_page", None)
        taker = getattr(page, "take_keep_settings_default", None)
        self._keep_settings_default = bool(taker()) if callable(taker) else False
        # Only the auto-started ROM-bundled run ends on Retry or Quit.
        self._bundled_firmware_run = bool(path) and path == getattr(self, "_bundled_package_path", None)
        if not self._bundled_firmware_run and not getattr(
            self, "_install_first_prerequisite_run", False
        ):
            self._bundled_rockbox = False
        if hasattr(self, "_flash_page"):
            self._flash_page.set_bundled_run(self._bundled_firmware_run)
        self._is_240p_rockbox = False
        if hasattr(self, "_flash_page"):
            self._flash_page.set_rockbox_site_themes(False)
        self._pre_install_guided = False
        self._cache_recovery_needed = False
        self._fallback_attempted = False
        self._sp_1013_seen = False
        # A new install starts with the tick off; the card can turn it on again.
        self._keep_data = False
        self._format_all_required = False
        self._format_all_confirm_pending = False
        self._keep_data_pkg = None
        self._rockbox_cleanup_started = False
        self._flash_method = self._preferred_flash_method()
        if hasattr(self, "_flash_page"):
            self._flash_page.set_method(self._flash_method)

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

        format_all = self._apply_pmt_format_state(
            getattr(self, "_keep_data_pkg", None), self._package_model
        )
        da_file, auth_file = self._flash_inputs()
        from ..flash_service import SpAuthFileError

        try:
            command = terminal_install.build_install_command(
                self._flash_method,
                extract_dir,
                scatter,
                package_path=self._package_path,
                platform_name=platform_name,
                auth_file=auth_file,
                da_file=da_file,
                model=self._package_model,
                format_all=format_all,
            )
        except SpAuthFileError as e:
            QMessageBox.warning(
                self, tr("terminal_install_title"), tr("err_auth_file_unusable_desc")
            )
            logger.debug("Terminal install: auth file unusable: %s", e)
            return
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
        self._linux_permission_pending = False
        if backend_method(self._flash_method) == METHOD_MTK:
            # MTKClient talks to the player through libusb as the normal user,
            # but raw USB device nodes are not writable without a MediaTek
            # udev rule. Install that rule once here instead of starting the
            # flash and failing with "Access denied". The rule is persistent
            # and is the same one SP Flash Tool uses. A copy launched with
            # root already has access and needs no rule.
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                return True
            try:
                access_ok, access_msg = linux_sp_flash.check_udev_rules()
            except Exception as exc:
                logger.info("Could not check MediaTek USB access: %s", exc)
                access_ok, access_msg = True, ""
            if not access_ok and not getattr(self, "_linux_permission_confirmed", False):
                logger.info("MediaTek USB access is not configured: %s", access_msg)
                self._linux_permission_pending = True
                self._show_device_prep(permission=True)
                return False
            if access_ok:
                self._linux_permission_confirmed = False
            return True
        try:
            # Ordinary user-space pieces never need elevation.
            linux_sp_flash.silent_system_prep()
            # No device probing here: SP Flash Tool and mtk.py report their own
            # connection problems. Prep only makes the system ready for them.
            report = linux_sp_flash.run_system_checker()
            # Only the udev rules need root, so only they justify a password
            # prompt. The rules carry TAG+="uaccess", which is what actually
            # opens the player to the logged-in seat; group membership is a
            # fallback the setup script adds for free. Asking again because
            # the user is not in dialout/plugdev (Arch and Fedora use neither)
            # made a prepared machine prompt for every install.
            needs_access = any(
                item.get("key") == "udev_rules" and item.get("status") != "ok"
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
        # user is being asked to authenticate. This is the USB-access setup
        # step; MTKClient installs reach it only when the rules are missing.
        self._flash_page.show_entering_password()
        try:
            from PySide6.QtWidgets import QApplication

            QApplication.processEvents()
        except Exception:
            pass
        try:
            if backend_method(self._flash_method) == METHOD_MTK:
                # The udev rule alone is enough for MTKClient; do not stage
                # or verify the SP Flash Tool payload for an MTK install.
                fixed, detail = linux_sp_flash.install_udev_rules()
            else:
                fixed, detail = linux_sp_flash.auto_fix_permissions()
        except Exception as exc:
            logger.info("Linux access setup failed: %s", exc)
            fixed, detail = False, str(exc)
        if not fixed:
            # Let a later retry offer the access step again; a cancelled or
            # failed password prompt must not become a permanent "confirmed".
            self._linux_permission_confirmed = False
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

        # Linux never restarts the whole app: the SP Flash Tool elevates its
        # own launch when the switch is on, and MTKClient uses a persistent
        # udev rule instead of root. A whole-app restart would prompt for
        # every MTKClient install.
        if platform.system() == "Linux":
            return True
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
        self._flash_page.set_power_off_only(self._install_first_power_off_only)
        keep_data_default = self._keep_data_default_for_install()
        try:
            from ..flash_service import completed_extract_dir, compute_extract_dir, _find_scatter, _parse_scatter_platform
            from ..fw_package import scatter_has_userdata, scatter_has_preserve_data_variant
            ed = completed_extract_dir(self._package_path) or compute_extract_dir(self._package_path)
            sc = None
            if ed and Path(ed).is_dir():
                sc = _find_scatter(Path(ed), allow_raise=False)
            elif self._package_path and Path(self._package_path).is_file():
                p = Path(self._package_path)
                if p.suffix.lower() == ".txt" or "scatter" in p.name.lower():
                    sc = p
            if sc:
                platform = _parse_scatter_platform(sc) or ""
                if hasattr(self._flash_page, "set_platform"):
                    self._flash_page.set_platform(platform)
                has_ud = scatter_has_userdata(sc)
                has_preserve = scatter_has_preserve_data_variant(sc.parent)
                self._flash_page.set_has_userdata(
                    has_ud, preserve_data_default=has_preserve or keep_data_default
                )
        except Exception as e:
            logger.debug("Could not inspect scatter in _show_device_prep: %s", e)
        # This screen shows the release's own image, not only the settings
        # stand-in: it is the first thing the user sees when the install starts,
        # and the catalogue already has the image by then.
        self._apply_release_icon(complete=False)
        self._keep_data_confirm_pending = False
        self._keep_data_erase_confirm_pending = False
        self._flash_page.clear_keep_data_confirmation()
        self._flash_page.show_power_off_prompt(permission=permission)
        try:
            from .. import keep_data, catalog

            pkg = None
            page = getattr(self, "_select_page", None)
            if page is not None:
                pkg = getattr(page, "_details_package", None) or page.current_package()
            model = self._package_model or getattr(pkg, "model", "") or ""
            if pkg is None and model:
                for candidate in catalog.packages_for_model(model):
                    if candidate.name and candidate.name.casefold() in (self._package_name or "").casefold():
                        pkg = candidate
                        break
            self._keep_data_pkg = pkg
            self._apply_pmt_format_state(pkg, model)
            # The tracked install is recorded per model and hardware type: a
            # Y1 install must never be judged compatible from a Y2 record.
            hardware = (
                self._select_page.current_hardware_type()
                if hasattr(self, "_select_page")
                else ""
            )
            available = keep_data.keep_data_available(
                model, pkg, self.settings, hardware_type=hardware
            )
            if self._install_first_already_on_target():
                available = True
            elif self._install_first_forces_no_keep_data():
                available = False
            self._flash_page.set_keep_data_available(
                available and not self._format_all_required,
                keep_data.keep_data_label(model),
            )
        except Exception:
            logger.debug("Could not evaluate Keep Data compatibility", exc_info=True)
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
            self.themes.finish()
            self._run_output_open = False
            self._download_active = False
            self._flash_page.show_completion_appeal(False)
            self._flash_page.show_flashing()
            return
        section = getattr(self, "_catalogue_section", "online") or "online"
        self._flash_page.stop_theme_workers()
        self.themes.clear_power_on()
        self.themes.finish()
        self._run_output_open = False
        self._download_active = False
        self._release_install_cache()
        self._drop_finished_install_firmware()
        self._reset_after_run()
        page = getattr(self, "_select_page", None)
        if page is not None and hasattr(page, "show_catalogue_section"):
            page.show_catalogue_section(section)
        self._sync_sp_gui_button_visibility()
        self._nav_to_page(_PAGE_SELECT)

    def _drop_finished_install_firmware(self) -> None:
        """A completed install needs no firmware copy on the host any more.

        The extract exists so a failed run can retry without downloading
        again. Once the install has finished nothing reads it (the SP Flash
        Tool history is only written where that tool exists), so leaving it
        behind just parks a multi-gigabyte image set on the user's disk.
        """
        try:
            from ..downloads import clear_firmware_cache

            count, freed = clear_firmware_cache()
        except Exception:
            logger.debug("Could not clear the finished install's firmware", exc_info=True)
            return
        if count:
            self._append_log(
                "Removed the finished install's firmware files "
                f"({freed / (1024 * 1024):.1f} MB freed)."
            )

    def _refresh_online_data_after_storage_clear(self) -> None:
        """Fetch the remote data again after Clear Firmware Cache removed it.

        Notices and the catalogue listing live in the app cache, which the
        clear button empties, so they are re-read from their online sources.
        A run in progress owns the catalogue and is left alone.
        """
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().fetch_async()
        except Exception:
            logger.debug("Could not refetch notices after clearing storage", exc_info=True)
        if self._install_run_active():
            return
        page = getattr(self, "_select_page", None)
        refresh = getattr(page, "_refresh_releases", None)
        if not callable(refresh):
            return
        try:
            refresh(force_refresh=True)
        except Exception:
            logger.debug("Could not refetch the catalogue after clearing storage", exc_info=True)

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
        else:
            self._linux_permission_pending = False
        if not getattr(self, "_fallback_attempted", False):
            self._flash_method = self._preferred_flash_method()
            self._flash_page.set_method(self._flash_method)
            self._sp_1013_seen = False
        self._install_failed = False
        self._power_off_prompt = False
        # The target's prep card has been answered by the time the flash starts;
        # later retries use the ordinary power-off-and-disconnect wording.
        self._install_first_power_off_only = False
        self._flash_page.set_power_off_only(False)
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
        apk_path, apk_url = self._apk_install_info()
        self.themes.set_apk(apk_path, apk_url)
        self._is_360p_rockbox = (
            is_360p_rockbox
            or bool(apk_path or apk_url)
            or bool(getattr(self, "_bundled_rockbox", False))
        )


        self._flash_page.set_package_name(self._package_name)
        self._flash_page.set_model(self._package_model)
        self._flash_page.set_method(self._flash_method)
        keep_data_default = self._keep_data_default_for_install()
        self._flash_page.show_completion_appeal(False)
        self._apply_release_icon(complete=False)
        self._past_usb_search = False
        self._usb_search_active = False
        self._last_progress = 0
        pre_extracted = completed_extract_dir(self._package_path)
        self._flash_page.begin_run()
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
            from ..manifest import refresh_catalog
            refresh_catalog(force_refresh=True)
            self._apply_post_install_urls()
        except Exception as e:
            logger.debug("Live manifest check during install flow: %s", e)
        try:
            from ..flash_service import compute_extract_dir, _find_scatter, _parse_scatter_platform
            from ..fw_package import scatter_has_userdata, scatter_has_preserve_data_variant

            ed = pre_extracted or compute_extract_dir(self._package_path)
            sc = _find_scatter(Path(ed), allow_raise=False) if Path(ed).is_dir() else None
            if not sc and self._package_path and Path(self._package_path).is_file():
                p = Path(self._package_path)
                if p.suffix.lower() == ".txt" or "scatter" in p.name.lower():
                    sc = p
            if sc:
                if hasattr(self._flash_page, "set_platform"):
                    platform = _parse_scatter_platform(sc) or ""
                    if platform:
                        self._flash_page.set_platform(platform)
                has_ud = scatter_has_userdata(sc)
                has_preserve = scatter_has_preserve_data_variant(sc.parent)
                self._flash_page.set_has_userdata(
                    has_ud, preserve_data_default=has_preserve or keep_data_default
                )
        except Exception as e:
            logger.debug("Could not inspect scatter platform in _begin_flash_flow: %s", e)
        self._apply_pmt_format_state(
            getattr(self, "_keep_data_pkg", None), self._package_model
        )
        da_file, auth_file = self._flash_inputs()
        self._saw_all_command_exec = False
        self._sp_console_completion_done = False
        self._completion_notified = False
        self._used_sp_backend = (backend_method(self._flash_method) == METHOD_SP)
        if self._used_sp_backend:
            self._sp_flash_tool_tried = True
        if hasattr(self, "_failure_grace_timer") and self._failure_grace_timer.isActive():
            self._failure_grace_timer.stop()
        from .. import simulation

        if simulation.enabled():
            # No worker is started: the driver emits the service's own signals,
            # so the card, the state machine and the completion routing all take
            # their real paths. Nothing touches USB or a backend process.
            self.service.stop_device_monitor()
            previous = getattr(self, "_sim_flash_run", None)
            if previous is not None:
                previous.stop()
            self._sim_flash_run = simulation.FlashRun(self.service, self)
            self._sim_flash_run.start()
            return
        self.service.start_flash(
            self._package_path,
            pre_extracted_dir=pre_extracted,
            method=self._flash_method,
            model=self._package_model,
            da_file=da_file,
            auth_file=auth_file,
            is_rockbox=self._is_360p_rockbox,
            keep_data=getattr(self, "_keep_data", False),
            format_all=getattr(self, "_format_all_required", False),
        )
        self.service.stop_device_monitor()

    def _on_step_changed(self, step):
        # A finished run is final. The SP Flash Tool log monitor can flush the
        # tail of QT_FLASH_TOOL.log after the worker has already reported
        # success, and a late STEP_WRITE used to put the write card back up at
        # its 95% ceiling, hiding the completion screen and its donations
        # prompt. Nothing may reopen the card in that state.
        if getattr(self, "_install_complete", False) or self.sm.state is FlashState.S5_COMPLETE:
            return
        if step in (STEP_DETECT, STEP_DONE) or step in _WRITE_STEPS:
            self._past_usb_search = True
            self._usb_search_active = False
        previous_step = getattr(self, "_step_now", None)
        self._step_now = step
        key = _STEP_KEY.get(step, "step_wait")
        self._flash_page.update_step(key)
        self._retry_page.update_step(key)
        self._on_progress(self._last_progress)
        if step == STEP_EXTRACTING:
            self._flash_page.show_preparing()
        elif step == STEP_DETECT:
            self._past_usb_search = True
            self._usb_search_active = False
            self._flash_page.show_real_stage(tr("step_detect"))
            self._flash_page.set_detected()
            self._flash_page.set_device_flashing()
            self._flash_page.show_flashing()
            self._flash_page._warning.setVisible(False)
            self._settings_page.set_method_enabled(False)
            self._enter_flashing_state()
        elif step == STEP_PLEASE_WAIT:
            self._usb_search_active = False
            self._last_progress = 0
            if self._past_usb_search or not getattr(self, "_pre_install_guided", False):
                self._flash_page.show_please_wait()
            self._settings_page.set_method_enabled(True)
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step == STEP_WAITING:
            if getattr(self, "_past_usb_search", False):
                return
            # A handshake retry prints search or hint again after please-wait.
            self._past_usb_search = False
            self._usb_search_active = True
            self._last_progress = 0
            self._flash_page.show_connect_search()
            self._flash_page.highlight_guide_step(1)
            # Still waiting for the device: the method may be switched.
            self._settings_page.set_method_enabled(True)
            self._set_state(FlashState.S2_WAIT_CONNECTION)
        elif step in _WRITE_STEPS:
            # SP Flash Tool reports a write step for every percent. The first
            # one already put the card up; rebuilding it and refitting the
            # window a hundred times during a slow write left the page surface
            # behind the card unpainted, which read as a white band under it.
            if previous_step not in _WRITE_STEPS:
                self._flash_page.show_flashing()
            self._flash_page.set_device_flashing()
            self._settings_page.set_method_enabled(False)
            self._enter_flashing_state()
        elif step == STEP_DONE:
            # Every partition is written, but the worker has not finished: it
            # still closes USB (MTKclient) or waits for SP Flash Tool. The
            # completion screen arrives with _handle_flash_success; until then
            # the card stays up saying it is finishing.
            self._flash_page.show_finishing()

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

    def _on_state_changed(self):
        """Called when the state machine transitions, resize the window to fit."""
        self._adjust_window_geometry()

    def _enter_flashing_state(self):
        if self.sm.state is not FlashState.S4_FLASHING:
            if self.sm.state is FlashState.S2_WAIT_CONNECTION:
                self._set_state(FlashState.S3_DEVICE_DETECTED)
            self._set_state(FlashState.S4_FLASHING)
            if not self._flash_start_ts:
                self._flash_start_ts = time.time()
            self._elapsed_timer.start(1000)

    # One paint per tick, not per signal. 10 fps is more than a percentage bar
    # can show, and it keeps the event loop free while a backend floods it with
    # per-partition ticks; 25 fps was measurable UI load during a flash.
    _PROGRESS_COALESCE_MS = 100
    _PARTITION_COALESCE_MS = 80

    def _queue_progress(self, percent):
        """Keep only the newest backend position for the next UI tick."""
        self._pending_progress = percent
        timer = getattr(self, "_progress_coalesce", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self._PROGRESS_COALESCE_MS)
            timer.timeout.connect(self._flush_progress)
            self._progress_coalesce = timer
        if not timer.isActive():
            timer.start()

    def _flush_progress(self):
        value = getattr(self, "_pending_progress", None)
        self._pending_progress = None
        if value is not None:
            self._on_progress(value)

    def _drop_pending_progress(self):
        """Forget a queued tick: a result card must not be repainted from it."""
        timer = getattr(self, "_progress_coalesce", None)
        if timer is not None and timer.isActive():
            timer.stop()
        self._pending_progress = None

    def _queue_partition_progress(self, text):
        """Keep only the newest partition line for the next UI tick."""
        self._pending_partition_progress = text
        timer = getattr(self, "_partition_coalesce", None)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval(self._PARTITION_COALESCE_MS)
            timer.timeout.connect(self._flush_partition_progress)
            self._partition_coalesce = timer
        if not timer.isActive():
            timer.start()

    def _flush_partition_progress(self):
        text = getattr(self, "_pending_partition_progress", None)
        self._pending_partition_progress = None
        if text is not None:
            self._flash_page.update_partition_progress(text)

    def _on_progress(self, percent):
        # Same rule as _on_step_changed: a coalesced or replayed tick from a
        # finished run must not repaint the card behind the completion screen.
        if getattr(self, "_install_complete", False) or self.sm.state is FlashState.S5_COMPLETE:
            return
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
        if getattr(self, "_step_now", "") == STEP_EXTRACTING:
            self._last_progress = percent
            self._flash_page.update_prep_progress(percent)
            return
        # Rockbox installs reserve the final 10% for themes and fonts. The
        # backend (FlashWorker) already maps the write progress to 0-90% for
        # Rockbox and 0-100% for standard releases, so percent is passed through
        # directly without double-scaling.
        self._last_progress = percent
        if percent is not None and percent > 12.5:
            if hasattr(self, "_flash_page") and getattr(self._flash_page, "_getting_ready", False):
                self._flash_page.set_getting_ready(False)
        self._flash_page.update_progress(percent)
        self._retry_page.update_progress(percent)

    def _on_getting_ready(self, active: bool) -> None:
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.set_getting_ready(active)

    def _on_log_message(self, msg):
        from ..flash_service import is_mtk_checksum_line
        if is_mtk_checksum_line(msg):
            if hasattr(self, "_flash_page") and self._flash_page is not None:
                self._flash_page.set_getting_ready(True)
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
            if len(self._run_output) > 4000 + 512:
                # Trim in blocks: a per-line slice of the retained output was
                # another full copy on the GUI thread for every tool line.
                del self._run_output[: len(self._run_output) - 4000]
        from ..diagnostics import LOG_RETAINED_LINES
        # Trim in blocks: slicing the retained list on every line copied 80k
        # entries per tool line, which starved the event loop during a flash.
        if len(self._log_lines) > LOG_RETAINED_LINES + 2048:
            del self._log_lines[: len(self._log_lines) - LOG_RETAINED_LINES]
        try:
            from ..diagnostics import DiagnosticsManager
            DiagnosticsManager.instance().record_log(msg, dedupe=dedupe)
        except Exception:
            pass
        self.log_line_added.emit(msg)
        self._handle_backend_line(msg)

    def _handle_backend_line(self, line):
        """React to raw backend output with the right amount of UI.

        Three kinds of line need a reaction:

        * mtkclient's ``Hint:`` block is not an error — it restates step 1 of
          the connection guide, so it just re-arms the waiting stage.
        * errno 2 / 5 mean the player is wedged and must be reset by hand, so
          they raise the retry-guidance dialog.
        * SP Flash Tool's own completion line finishes a run whose console
          process never exits (see :meth:`_maybe_finish_sp_console_run`).

        Everything else stays Diagnostics-only, which is where the raw output
        belongs.
        """
        text = str(line or "")
        low = text.lower()
        is_sp = bool(getattr(self, "_used_sp_backend", False)) or text.startswith("[SP]")
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
                self._failure_title = ""
                if hasattr(self, "_flash_page"):
                    # A failure card raised too early must not stay under the
                    # completion screen the line is about to show.
                    self._flash_page.clear_install_failure()
            # The tool has finished its command. Some builds keep the console
            # process alive after this line, so finish the install here rather
            # than leaving the card at the last write percentage.
            self._maybe_finish_sp_console_run()

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

    def _recover_missing_firmware(self, detail="") -> bool:
        """Fetch and extract the package again before reporting a cache failure.

        A file missing from the extracted package is nearly always a cache
        problem, and the archive is deleted once it has been extracted — so the
        way back is a fresh download of the same release, which is what the
        Retry button does by hand. Doing it here means CACHE_FILE_MISSING only
        reaches the user when the re-download itself fails, or when the
        firmware came from a local file that cannot be fetched again.

        Once per package: a second failure after a fresh download is real.
        """
        package = str(getattr(self, "_package_path", "") or "")
        if not package or getattr(self, "_cache_recovered_package", "") == package:
            return False
        # A retry keeps this journey's extract even when the archive is gone:
        # retry_missing_cached_firmware reuses it rather than purging it.
        page = getattr(self, "_select_page", None)
        rel = getattr(page, "_current_selected_rel", None) if page is not None else None
        if not rel or not rel.get("download_url"):
            return False
        # The release has to be the one whose archive this run is using: a
        # local-file install can leave a catalogue selection behind, and
        # fetching that instead would replace the wrong firmware.
        asset = Path(str(rel.get("asset_name") or "")).name
        if asset and asset not in Path(package).name:
            return False
        retry = getattr(page, "retry_missing_cached_firmware", None)
        if not callable(retry):
            return False
        self._cache_recovered_package = package
        self._cache_recovery_needed = True
        self._append_log(
            "Firmware files were missing from the cache; downloading the package "
            "again before reporting a failure."
        )
        if detail:
            self._append_log(f"  ({detail})")
        # The retry path owns the reset (cancel the backend, clear the failure
        # card). ``prompt=False``: the run is already past the pre-install step,
        # so the power-off confirmation must not interrupt the recovery.
        self._on_progress_retry(prompt=False)
        return True

    def _set_cache_recovery_package(self, detail="") -> bool:
        """Recover a cache failure, or keep the release for a manual retry.

        Returns True when a recovery download was started, so the caller does
        not paint the failure card on top of it.
        """
        if self._recover_missing_firmware(detail):
            return True
        package = Path(getattr(self, "_package_path", "") or "")
        self._cache_recovery_needed = True
        # Keep this journey's extracted cache. A failure is not permission to
        # delete it: the Retry button may be pressed any number of times, and
        # the extract is the source once the downloaded archive is gone. It is
        # released only when the user leaves the install journey.
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
        return False

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

    def _sp_follow_up_stage_pending(self) -> bool:
        """True when a post-flash guide stage still owns the end of this run.

        An APK install (``apk_repo``) or the Rockbox themes/fonts transfer is a
        follow-up stage the success path starts by itself. While one is
        configured, flash_tool's own completion line is not the end of the
        journey: the card must hand over to that stage instead.
        """
        apk_path, apk_url = self._apk_install_info()
        if apk_path or apk_url:
            return True
        wants_themes, _model = self._rockbox_themes_required()
        if not wants_themes:
            return False
        from ..theme_sync import themes_auto_install_enabled

        return bool(themes_auto_install_enabled(self.settings))

    def _maybe_finish_sp_console_run(self) -> None:
        """Finish the journey on flash_tool's own completion line.

        Some SP Flash Tool builds keep their console process alive after
        "All command exec done!": the command is finished, but the process
        blocks instead of exiting, so the worker never reports a result and the
        card stays at the last write percentage (95% for a standard release)
        with no completion screen. The worker treats that same line as success
        when it does exit, so with no follow-up stage configured the run is
        finished here: 100%, the cleanup a completed worker would have run, and
        the ordinary completion screen (donations appeal or completion note).
        The flash itself is untouched; only its end is recognised earlier.
        """
        if getattr(self, "_sp_console_completion_done", False):
            return
        if getattr(self, "_install_complete", False) or getattr(self, "_install_failed", False):
            return
        if not getattr(self, "_package_path", ""):
            return
        if self.themes.active or self.themes.power_on_pending:
            return
        if self._sp_follow_up_stage_pending():
            return
        self._sp_console_completion_done = True
        self._append_log(
            "SP Flash Tool reported 'All command exec done'; the install is complete."
        )
        self._last_progress = 100
        self._drop_pending_progress()
        self._flash_page.update_progress(100)
        timer = getattr(self, "_elapsed_timer", None)
        if timer is not None:
            timer.stop()
        if hasattr(self, "_kill_process_timer") and self._kill_process_timer.isActive():
            self._kill_process_timer.stop()
        # The tool has finished its command; close the console process that is
        # still holding the port, and release the host-awake hold with it.
        service = getattr(self, "service", None)
        if service is not None:
            try:
                service.cancel_flash()
                service.stop_device_monitor()
            except Exception:
                logger.debug("Could not close the SP Flash Tool session", exc_info=True)
        self.themes.finish()
        self.themes.clear_power_on()
        from .. import process_guard

        process_guard.end_install_session(reason="install complete", sweep=False)
        process_guard.sweep_async(
            reason="install complete", on_done=self.sweep_report.emit
        )
        try:
            from ..diagnostics import DiagnosticsManager

            DiagnosticsManager.instance().end_flash_session(True, "")
        except Exception:
            pass
        self._handle_flash_success()

    def _on_flash_disconnected(self):
        """Handle abrupt USB disconnection during flash, wait 5s safety window before killing tasks."""
        service = getattr(self, "service", None)
        worker = getattr(service, "_flash_worker", None)
        if (
            worker is not None
            and getattr(worker, "_tool_tag", "") == "MTK"
            and not getattr(worker, "_finished", False)
            and getattr(worker, "_past_usb_search", False)
            and time.time() < getattr(worker, "_device_loss_held_until", 0.0)
        ):
            self._append_log("Notice: USB device re-enumeration while MTKClient is writing; keeping the install active.")
            return
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
            or self.themes.active
            or self._is_sp_complete_override()
            or self.themes.completion_shown
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
        # A coalesced earlier tick must not repaint the bar behind the result.
        self._drop_pending_progress()
        run = getattr(self, "_sim_flash_run", None)
        if run is not None:
            run.stop()
            self._sim_flash_run = None
        self._elapsed_timer.stop()
        if hasattr(self, "_kill_process_timer") and self._kill_process_timer.isActive():
            self._kill_process_timer.stop()

        if not ok:
            self.themes.finish()
            self.themes.clear_power_on()
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
            if getattr(self, "_sp_console_completion_done", False):
                # flash_tool's completion line already showed the completion
                # screen; this result is only the run closing.
                return
            self._handle_flash_success()
        else:
            if getattr(self, "_sp_console_completion_done", False):
                # A completed flash must not be reported as failed because of
                # what the tool did after its own completion line (its exit
                # code, a USB disconnect, or a console that never exits).
                self._append_log(
                    "Notice: ignoring a late SP Flash Tool failure after its "
                    f"completion line: {error_code}"
                )
                return
            self._handle_flash_failure(error_code)

    def _record_completed_install(self):
        """Log the firmware that just finished writing as this model's firmware.

        Both halves of a two-step install call this: the base is the model's
        firmware as soon as it is on the player, and the target then replaces
        it with the version it wrote.
        """
        model = self._package_model or ""
        software = self._package_name or "Firmware"
        rel_info = getattr(self._select_page, "current_installed_release_info", lambda: None)()
        # Online catalogue installs only. A local file has no release tag, so
        # it is not tracked and cannot drive an update prompt.
        if not (rel_info and (rel_info.get("tag_name") or "").strip()):
            return
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
            hardware_type=rel_info.get("hardware_type") or rel_info.get("type_variant") or (self._select_page.current_hardware_type() if hasattr(self, "_select_page") else ""),
        )
        # The player has just joined the update-reminder list, so its model,
        # firmware and repository can now bring notices to the card.
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().refresh_context()
        except Exception as e:
            logger.debug("Could not refresh the notice context after install: %s", e)

    def _handle_flash_success(self):
        if (
            getattr(self, "_install_first_prerequisite_run", False)
            and self._install_first_after
        ):
            # The base is on the player now, so it is this model's firmware
            # from this moment: log it before the run moves on, or a user who
            # stops here would be asked which firmware is running all over
            # again and Keep Data could not tell what the data belongs to.
            self._record_completed_install()
            self._finish_install_first_prerequisite()
            return
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
        self._record_completed_install()
        if hasattr(self, "_settings_page"):
            self._settings_page.mark_firmware_installed()
            self._settings_page.refresh_settings()

        try:
            from ..sp_flash_gui import update_sp_history_ini
            update_sp_history_ini(model=model)
        except Exception as e:
            logger.debug("Could not update SP history.ini on install complete: %s", e)

        # Keep my settings off on a Rockbox install is a request for a clean
        # start, and the old .rockbox folder is where Rockbox keeps its themes,
        # fonts and presets. That step runs before the themes stage and before
        # the completion screen; it re-enters this method when it is done.
        if self._start_rockbox_cleanup_if_needed():
            return

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
        is_240p_rockbox = self._rockbox_240p_detected()
        apk_path, apk_url = self._apk_install_info()
        wants_apk = bool(apk_path or apk_url)
        self.themes.set_apk(apk_path, apk_url)

        self._is_360p_rockbox = is_360p_rockbox
        self._is_240p_rockbox = is_240p_rockbox
        self.themes.clear_power_on()
        self._download_active = False

        # The in-beta Settings switch is the single owner of the automatic
        # themes decision: off means the install finishes like any other
        # firmware and the player is left alone. The Settings "Install Themes
        # Now" button does not come through here.
        from ..theme_sync import themes_auto_install_enabled

        themes_auto = themes_auto_install_enabled(self.settings)
        wants_themes = is_360p_rockbox
        if wants_apk or (wants_themes and themes_auto):
            self._nav_to_page(_PAGE_FLASH)
            # Keep the completion screen's recorded success state while the
            # post-flash theme wizard runs. USB enumeration is expected to
            # change during the ADB handoff and is not a firmware failure.
            self._install_complete = True
            self._install_failed = False
            is_sp_win = paths.IS_WINDOWS and (
                getattr(self, "_used_sp_backend", False)
                or backend_method(getattr(self, "_flash_method", "")) == METHOD_SP
            )
            if is_sp_win:
                self._append_log(
                    "Notice: SP Flash Tool on Windows detected, starting automated Rockbox themes flow "
                    "(waiting 90-120s for ADB connection, no user interaction required)..."
                )
                self._start_themes_stage(
                    eff_model,
                    donation_disabled,
                    already_powered_on=True,
                    automatic=True,
                    after_firmware=True,
                )
            else:
                self._show_rockbox_power_on_prompt(eff_model, after_firmware=True)
        else:
            # Includes a Rockbox install with the themes switch off: it ends on
            # the ordinary completion screen, exactly like any other firmware.
            if wants_themes:
                self._append_log(
                    "[Rockbox Themes] Themes and fonts auto-install is off in Settings; "
                    "the install finishes without the themes stage."
                )
            self._install_complete = True
            self._compact_install_screen = False
            self._power_off_prompt = False
            self._prep_active = False
            self._download_active = False
            self._sync_install_nav_entry()
            self._flash_page.show_flashing()
            self._flash_page.set_device_done(donations_shown=not donation_disabled)
            self._apply_post_install_urls()
            self._flash_page.set_rockbox_site_themes(is_240p_rockbox)
            self._flash_page.show_completion_appeal(False)
            self._flash_page.show_completion_note(
                donation_disabled, software=software, model=model
            )
            self._apply_release_icon(complete=donation_disabled)
            self._nav_to_page(_PAGE_FLASH)
            self._sync_install_app_button()
            if not donation_disabled:
                self._flash_page.show_completion_appeal(
                    True, self._on_completion_dont_ask, self._on_completion_not_now,
                )
            self._adjust_window_geometry()
            self._sync_sp_history()
            self._show_status(
                tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
            )
            self._notify_task_complete(software, model)

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
        after_firmware: bool = False,
    ) -> None:
        """Open the themes install stage after a 360p Rockbox install.

        ``automatic`` marks a stage the app decided to run by itself; it is
        refused while the in-beta Settings switch is off. The Settings button
        starts the same stage by hand and is never gated. ``after_firmware``
        is True when a Rockbox firmware install came first in this run.
        """
        allowed, reason = self.themes.should_start(automatic=automatic, settings=self.settings)
        if not allowed:
            self._append_log(reason)
            return
        self.themes.reset_completion()
        # The window owns the entry to the stage, and a custom wizard never
        # reaches the card's own begin(): mark the run from here.
        self.themes.begin(model, automatic=automatic)
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
            on_recover=lambda: self.restore_original_software(model),
            log_cb=self._append_log,
            already_powered_on=already_powered_on,
            automatic=automatic,
            after_firmware=after_firmware,
        )
        self._adjust_window_geometry()

    def _is_rockbox_install(self) -> bool:
        """Whether the software being installed is a Rockbox build.

        Detection cannot use the 360p themes helper: that one is Y1-only and
        obeys the themes Settings switch, while the cleanup step applies to any
        Rockbox install on any model.
        """
        values = [getattr(self, "_package_name", ""), getattr(self, "_package_path", "")]
        page = getattr(self, "_select_page", None)
        if page is not None:
            info = {}
            getter = getattr(page, "current_installed_release_info", None)
            if callable(getter):
                try:
                    info = getter() or {}
                except Exception:
                    info = {}
            for key in ("software_name", "package_slug", "release_label", "asset_name"):
                values.append(info.get(key))
            pkg = getattr(page, "_details_package", None)
            if pkg is not None:
                values.extend((getattr(pkg, "name", ""), getattr(pkg, "repo", "")))
        return any("rockbox" in str(value or "").casefold() for value in values)

    def _start_rockbox_cleanup_if_needed(self) -> bool:
        """Run the old-.rockbox removal step, returning True when it started.

        A Rockbox install with Keep my settings unticked asks for a fresh
        start, so the stage removes the folders the flash step cannot reach.
        Ticked means the user's themes and presets are kept and this is
        skipped entirely.
        """
        from ..rockbox_cleanup import rockbox_install_needs_cleanup

        if getattr(self, "_rockbox_cleanup_started", False):
            return False
        if not rockbox_install_needs_cleanup(
            is_rockbox=self._is_rockbox_install(),
            keep_data=bool(getattr(self, "_keep_data", False)),
        ):
            return False
        apk_path, apk_url = self._apk_install_info()
        if apk_path or apk_url:
            return False

        stage_model = (self._package_model or "").upper() or "Y1"
        if "Y1" in stage_model:
            stage_model = "Y1"
        elif "Y2" in stage_model:
            stage_model = "Y2"
        self._rockbox_cleanup_started = True
        self._nav_to_page(_PAGE_FLASH)
        self._install_complete = True
        self._install_failed = False
        self._append_log(
            "[Rockbox Cleanup] Keep my settings was not selected: removing the old "
            f".rockbox folders from {stage_model} before finishing the install."
        )
        is_sp_win = paths.IS_WINDOWS and (
            getattr(self, "_used_sp_backend", False)
            or backend_method(getattr(self, "_flash_method", "")) == METHOD_SP
        )
        on_done = lambda: self._finish_rockbox_cleanup()  # noqa: E731
        on_recover = lambda: self.restore_original_software(stage_model)  # noqa: E731
        if is_sp_win:
            # SP Flash reboots the player into the software just written, so
            # the ADB watch finds it without a disconnect or power cycle.
            self._flash_page.start_rockbox_cleanup_stage(
                model=stage_model,
                on_done=on_done,
                on_recover=on_recover,
                log_cb=self._append_log,
                already_powered_on=True,
                automatic=True,
                after_firmware=True,
            )
        else:
            # MTKclient hands back a powered-off player, so the cleanup waits
            # for the user to boot it exactly like the themes stage does.
            callback = lambda: self._flash_page.start_rockbox_cleanup_stage(  # noqa: E731
                model=stage_model,
                on_done=on_done,
                on_recover=on_recover,
                log_cb=self._append_log,
                already_powered_on=True,
                automatic=True,
                after_firmware=True,
            )
            self._show_rockbox_power_on_prompt(
                stage_model, after_firmware=True, on_continue=callback
            )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()
        return True

    def _finish_rockbox_cleanup(self) -> None:
        """Continue the install journey after the cleanup stage ends."""
        failed = bool(getattr(self.themes, "placement_failed", False))
        self.themes.finish()
        self._sync_install_nav_entry()
        if failed:
            self._append_log(
                "[Rockbox Cleanup] The old .rockbox folders were not removed; the install "
                "itself finished. The player's .rockbox folder can be deleted by hand."
            )
            self._show_status(tr("rockbox_cleanup_failed_note"), 60000)
        # Re-enters the completion path with the cleanup step already marked as
        # taken, so it runs the themes stage or the completion screen next.
        self._handle_flash_success()

    def refresh_everything(self) -> None:
        """Refresh every remote source the app reads, as the Refresh button asks.

        One click must not re-list only the software on screen: the manifest,
        notices, donors, the release blocklist, ``config.ini``'s token pool and
        every catalogue repository's releases are all forced, so the app behaves
        as if its caches had been reset.         Nothing is cleared first -- each file
        replaces its cache only after a usable download, and a failed release
        fetch falls back to the cache it already has -- so an offline moment
        can never blank the app.
        """
        self._refresh_online_data(force_update_check=True)

    def _refresh_online_data(self, force_update_check: bool = False) -> None:
        """Replace stale online files and re-read notices without blocking the UI.

        Every file is refreshed whether or not the window is in front: the
        donors CSV feeds the bar and the Support page, the feed snapshots drive
        notifications, and a hidden window is exactly when a passive sweep has
        nothing else to do. Notice.xml keeps its own poll (NoticeService owns
        that file and posts the notification).
        """
        if self._install_run_active():
            # Never compete with a running download or flash for bandwidth.
            return
        try:
            from ..online_data import refresh_all_async

            refresh_all_async(
                force=True,
                on_done=getattr(self, "_on_online_files_refreshed", None),
            )
        except Exception:
            logger.debug("Online data refresh could not start", exc_info=True)
        # Downloading slidia_manifest.xml changes nothing by itself: the
        # catalogue is the parsed list in memory. Fetch and install it here so
        # brands, models and software added since launch appear without a click.
        try:
            self._start_manifest_worker(force_refresh=True)
        except Exception:
            logger.debug("Catalogue refresh could not start", exc_info=True)
        try:
            from ..release_blocklist import refresh_blocklist_async

            # The blocklist keeps its own cache and rules, so the copy that
            # online_data downloads never reaches it; refresh it here as well.
            refresh_blocklist_async()
        except Exception:
            logger.debug("Release blocklist refresh could not start", exc_info=True)
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().fetch_async()
        except Exception:
            logger.debug("Notice refresh could not start", exc_info=True)
        if getattr(self, "_select_page", None) is not None:
            try:
                self._start_all_release_refresh()
            except Exception:
                logger.debug("Release cache refresh could not start", exc_info=True)
        if force_update_check:
            # A click asks "is anything new?" about the app itself too. The
            # check is the ordinary (non-manual) one, so a waiting build updates
            # the update row and notifies without interrupting with a dialog.
            try:
                self._update_manual_pending = False
                self._run_update_check()
            except Exception:
                logger.debug("App update check could not start", exc_info=True)

    def _on_online_files_refreshed(self, updated) -> None:
        """Pick up a freshly downloaded donors feed (this runs off the UI thread)."""
        if not isinstance(updated, dict) or not updated.get("donors.csv"):
            return
        QTimer.singleShot(0, self._reload_donations)

    def _reload_donations(self) -> None:
        """Re-read the cached donors feed so the bar shows a new total."""
        try:
            from ..online_data import read_text

            donations = parse_donors_csv_text(read_text("donors.csv") or "")
        except Exception:
            logger.debug("Donations reload failed", exc_info=True)
            return
        if donations:
            self._on_donations_updated(donations)

    def _online_refresh_tick(self) -> None:
        """Refresh once, then pick a fresh random delay for the next pass."""
        try:
            self._refresh_online_data()
        finally:
            if not getattr(self, "_force_quit", False):
                from ..online_refresh import next_interval_ms

                timer = getattr(self, "_online_refresh_timer", None)
                if timer is not None:
                    timer.start(next_interval_ms())

    def _start_all_release_refresh(self) -> None:
        """Force every catalogue repository's release cache off the UI thread."""
        worker = getattr(self, "_release_refresh_worker", None)
        if worker is not None and worker.isRunning():
            return
        try:
            from ..online_refresh import AllReposRefreshWorker

            worker = AllReposRefreshWorker(parent=self)
            self._release_refresh_worker = worker
            self._active_workers.add(worker)
            worker.refreshed.connect(
                lambda results: self._on_all_releases_refreshed(worker, results)
            )
            worker.start()
        except Exception:
            logger.debug("All-repository release refresh failed to start", exc_info=True)

    def _on_all_releases_refreshed(self, worker, results) -> None:
        """Re-read the visible Choose Software list from the refreshed cache."""
        self._active_workers.discard(worker)
        if worker is getattr(self, "_release_refresh_worker", None):
            self._release_refresh_worker = None
        if not results or self._install_run_active():
            return
        self._release_refresh_pending = True
        self._reload_refreshed_releases()
        # The release cache is the freshest it can be right now: this is where a
        # newly published build for a tracked player becomes a notification.
        try:
            self._check_device_firmware_updates(manual=False)
        except Exception:
            logger.debug("Device firmware check could not start", exc_info=True)

    def _reload_refreshed_releases(self) -> None:
        """Apply a finished sweep when the catalogue can accept it.

        A sweep that finishes while Settings or an install card is open must
        not be lost: the flag survives until Select Software is visible and
        idle, so the release rows show the refreshed cache on return.
        """
        if not getattr(self, "_release_refresh_pending", False):
            return
        if self._install_run_active():
            return
        page = getattr(self, "_select_page", None)
        if page is None or not page.isVisible():
            return
        try:
            if hasattr(page, "_can_relist_quietly") and not page._can_relist_quietly():
                return
            self._release_refresh_pending = False
            page._refresh_releases_keep_selection(force_refresh=False)
        except Exception:
            logger.debug("Could not re-read the release list", exc_info=True)

    def _apply_post_install_urls(self) -> None:
        """Hand the selected firmware's post-install URLs to the completion screen.

        A local package's ``fw_package.ini`` or the catalogue entry's
        ``slidia_manifest.xml`` attributes supply them. Only completion routing
        uses these; flashing and theme prep are unaffected.
        """
        page = getattr(self, "_select_page", None)
        post = post_2 = ""
        if page is not None and hasattr(page, "post_install_urls"):
            try:
                post, post_2 = page.post_install_urls()
            except Exception:
                logger.debug("Could not read post-install URLs", exc_info=True)
                post = post_2 = ""
        elif page is not None:
            fw_pkg = getattr(page, "_fw_package", None)
            post = str(getattr(fw_pkg, "post_install", "") or "")
            post_2 = str(getattr(fw_pkg, "post_install_2", "") or "")
        self._flash_page.set_post_install_urls(post, post_2)

    def _finish_themes_stage(self, donation_disabled: bool) -> None:
        if self.themes.completion_shown:
            return
        was_apk_stage = self.themes.is_apk_stage
        self.themes.mark_completion_shown()
        self.themes.finish()
        self._sync_install_nav_entry()
        self._install_complete = True
        self._compact_install_screen = False
        self._power_off_prompt = False
        self._prep_active = False
        self._download_active = False
        self._sync_install_nav_entry()
        self._flash_page.show_flashing()
        self._flash_page.set_device_done(donations_shown=not donation_disabled)
        self._apply_post_install_urls()
        self._apply_release_icon(complete=donation_disabled)
        self._nav_to_page(_PAGE_FLASH)
        # The themes stage only reaches this screen after its pack is placed,
        # unless it gave up; a give-up keeps the power-on steps and try-again note.
        themes_failed = self.themes.placement_failed
        themes_installed = not themes_failed
        if not donation_disabled:
            self._flash_page.show_completion_appeal(
                True, self._on_completion_dont_ask, self._on_completion_not_now,
                themes_installed=themes_installed,
            )
        model = self._package_model or ""
        software = self._package_name or "Firmware"
        # A themes stage that gave up still lands on this screen, so its note
        # must be shown even where the donations appeal would otherwise be the
        # only block — the try-again copy is why the user is reading.
        self._flash_page.show_completion_note(
            donation_disabled or themes_failed, software=software, model=model,
            themes_installed=themes_installed and not was_apk_stage,
        )
        self._sync_install_app_button()
        self._adjust_window_geometry()
        self._sync_sp_history()
        if themes_failed:
            self._show_status(tr("themes_placement_failed_note"), 60000)
        else:
            steps = install_power_on_steps(model)
            self._show_status(
                tr("status_install_ok_fmt").format(software=software, steps=steps), 60000
            )
        self._notify_task_complete(software, model)

    def _notify_task_complete(self, software: str, model: str) -> None:
        """Bring the window forward and post a native notification when a task finishes."""
        if getattr(self, "_completion_notified", False):
            return
        self._completion_notified = True
        try:
            from ..notifications import bring_window_to_front, show_completion_notification
            title = tr("notify_install_complete_title")
            message = _completion_notification_message(software, model)
            hidden = self._window_hidden_in_tray()
            show_completion_notification(title, message, window=self)
            if hidden:
                # The notification's bring-to-front shows the window but does
                # not release the menu-bar hold or restart the paused timers.
                self._show_from_tray()
            else:
                bring_window_to_front(self)
        except Exception:
            logger.debug("Completion notification failed", exc_info=True)

    def restore_original_software(self, model: str = "") -> None:
        """Trigger a download and install of the latest original software for model."""
        eff_model = (
            model
            or getattr(self, "_package_model", "")
            or self.themes.model
            or getattr(getattr(self, "_flash_page", None), "_model", "")
            or self._select_page.current_model()
            or "Y1"
        ).upper()
        if "Y2" in eff_model:
            eff_model = "Y2"
        elif "A5" in eff_model:
            eff_model = "A5"
        else:
            eff_model = "Y1"

        self._append_log(f"Restoring original software for {eff_model}...")
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._elapsed_timer.stop()
        if hasattr(self, "_flash_page"):
            self._flash_page.stop_theme_workers()
            self._flash_page.clear_install_failure()
        self.themes.mark_completion_shown()
        self.themes.finish()
        self._install_failed = False
        self._failure_title = ""
        self._power_off_prompt = False
        self._pre_install_guided = False
        self._saw_all_command_exec = False
        self._fallback_attempted = False
        self._cache_recovery_needed = False
        if self.sm.state in (
            FlashState.S5_FAILED,
            FlashState.S5_USB_DISCONNECTED,
            FlashState.S4_FLASHING,
            FlashState.S5_COMPLETE,
        ):
            self.sm.reset_for_retry()

        self._flash_method = self._preferred_flash_method()
        if hasattr(self, "_flash_page"):
            self._flash_page.set_method(self._flash_method)

        self._select_page.start_install_for_release(eff_model, "Original Software", tag_name="")

    def _ensure_theme_sync(self) -> None:
        """Start the passive 360p theme sync (self-gating; idle when untracked)."""
        if getattr(self, "_theme_sync_service", None) is not None:
            return
        from ..theme_sync import themes_auto_install_enabled

        if not themes_auto_install_enabled(self.settings):
            # The in-beta toggle is off: no background sync is created at all.
            # Turning it on later runs _on_themes_auto_install_changed.
            return
        try:
            from ..theme_sync import ThemeSyncService

            service = ThemeSyncService(self.settings, parent=self)
            self._theme_sync_service = service
            service.start()
        except Exception:
            logger.debug("Passive theme sync could not start", exc_info=True)

    def _on_themes_auto_install_changed(self, enabled: bool) -> None:
        """The in-beta Settings toggle owns the passive sync's run state."""
        if enabled:
            if getattr(self, "_theme_sync_service", None) is None:
                self._ensure_theme_sync()
            else:
                self._theme_sync_service.start()
        else:
            service = getattr(self, "_theme_sync_service", None)
            if service is not None:
                service.stop()

    def _handle_pmt_format_required(self, error_code):
        """Offer the one destructive retry that can clear SP Flash error 8502.

        Download Only cannot replace the changed partition table. The user is
        told what will be erased and must confirm before the app goes back to
        the power-off/pin-reset stage; the next run only swaps the console
        command to Format All + Download.
        """
        from .. import pmt_format

        pkg = getattr(self, "_keep_data_pkg", None) or self._current_firmware_package()
        if pkg is not None:
            self._keep_data_pkg = pkg
        newly = pmt_format.mark_pmt_format_required(
            pkg,
            model=self._package_model,
            software=self._package_name,
            settings=self.settings,
        )
        self._format_all_required = True
        self._format_all_confirm_pending = True
        self._keep_data = False
        self._install_failed = False
        self._power_off_prompt = True
        self._pre_install_guided = False
        self.service.stop_device_monitor()
        self._elapsed_timer.stop()
        self._flash_page.set_format_all_required(True, self._package_model)
        self._flash_page.clear_install_failure()
        self._flash_page.clear_keep_data_confirmation()
        self._flash_page.show_format_all_confirmation(
            self._package_name or Path(self._package_path).name,
            self._package_model,
        )
        detail = (
            f"SP Flash Tool error 8502 (PMT changed) while installing "
            f"{self._package_name or self._package_path} on "
            f"{self._package_model or 'the player'}: Format All + Download "
            f"required; Keep Data is unavailable for this software."
        )
        logger.warning(detail)
        self._append_log(detail)
        if newly:
            self._append_log(
                "Recorded this software as requiring Format All + Download."
            )
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_FLASH)
        self._update_header_title()

    def _show_install_failure(self, message: str):
        """Failure stays on the install progress card. Retry is where Cancel was."""
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.dismiss_prompt_stop()
        self.themes.finish()
        self.themes.clear_power_on()
        raw = str(message or "")
        low = raw.lower()
        if message == "CACHE_FILE_MISSING" or any(marker in low for marker in _CACHE_MISSING_MARKERS):
            # A missing cached file is recovered by downloading the package
            # again. Only when that is impossible, or has already been tried,
            # does the failure reach the user.
            if self._set_cache_recovery_package(raw):
                return
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
        self._flash_page.show_install_failure(
            message,
            explanation,
            self._last_progress or 0,
            wrong_model=is_wrong_model_error(message),
        )
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_FLASH)
        self._update_header_title()

    def _handle_flash_failure(self, error_code):
        self._download_active = False
        from ..flash_service import (
            is_attempt_timeout_error,
            is_connect_failure,
            is_disconnection_error,
            is_mtk_device_connected,
            is_pmt_format_required_error,
            is_storage_full_error,
        )
        if is_storage_full_error(error_code):
            try:
                from ..downloads import clear_firmware_cache, downloads_dir
                import shutil
                dd = downloads_dir()
                if dd.is_dir() and shutil.disk_usage(dd).free < 50 * 1024 * 1024:
                    clear_firmware_cache()
            except Exception:
                pass
            self._append_log(f"Flash failed due to insufficient host storage space ({error_code}).")
            self._show_install_failure("OUT_OF_STORAGE")
            return

        try:
            from ..downloads import clear_firmware_cache, downloads_dir
            import shutil
            dd = downloads_dir()
            free_bytes = shutil.disk_usage(dd).free if dd.is_dir() else 100 * 1024 * 1024
            if free_bytes < 50 * 1024 * 1024:
                # Free the *other* cached firmwares, never the one this run is
                # flashing: deleting it here guaranteed the next retry had to
                # download the whole package again.
                keep = set()
                current = str(getattr(self, "_package_path", "") or "")
                if current:
                    keep.add(current)
                    try:
                        from ..flash_service import compute_extract_dir

                        keep.add(str(compute_extract_dir(current)))
                    except Exception:
                        pass
                _cnt, _freed = clear_firmware_cache(exclude=keep)
                free_bytes = shutil.disk_usage(dd).free if dd.is_dir() else 100 * 1024 * 1024
                if free_bytes < 50 * 1024 * 1024:
                    self._append_log(f"Flash failed due to critically low host storage ({free_bytes} bytes free; original error: {error_code}).")
                    self._show_install_failure("OUT_OF_STORAGE")
                    return
        except Exception:
            pass
        cache_failure = bool(getattr(self, "_cache_recovery_needed", False)) or any(
            marker in str(error_code or "").lower() for marker in _CACHE_MISSING_MARKERS
        ) or str(error_code or "") == "CACHE_FILE_MISSING"
        if cache_failure and self._recover_missing_firmware(error_code):
            # A download is running in place of the failure card.
            return
        if not cache_failure and is_pmt_format_required_error(error_code):
            self._handle_pmt_format_required(error_code)
            return
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
        if is_attempt_timeout_error(error_code):
            # The connect window ended with nothing attached. That is not a
            # failed method, so the same engine is offered again: the user is
            # sent back to the disconnect / power-off step, the killed run is
            # already stopped, and Continue starts the same method afresh.
            # Timeouts may repeat as often as needed; only a real install
            # failure switches engines.
            self._append_log(
                f"No device inside the connect window ({error_code}); "
                f"returning to the power-off step with {_method_label(self._flash_method)}."
            )
            self._return_to_power_off_stage(self._flash_method)
            return
        if not cache_failure and error_code == "MTK_CHECKSUM_STALLED":
            if hasattr(self, "_flash_page") and self._flash_page is not None:
                self._flash_page.set_getting_ready(False)
            from ..flash_service import is_sp_flash_tool_supported_for_host, backend_method, METHOD_SP, METHOD_MTK
            sp_already_tried = (
                getattr(self, "_fallback_attempted", False)
                or getattr(self, "_sp_flash_tool_tried", False)
                or getattr(self, "_used_sp_backend", False)
                or (backend_method(self._flash_method) == METHOD_SP)
            )
            if not sp_already_tried and is_sp_flash_tool_supported_for_host():
                self._fallback_attempted = True
                self._sp_flash_tool_tried = True
                self._append_log("Checksum stall persisted for 2 minutes; switching to SP Flash Tool to recover the player.")
                self._return_to_power_off_stage(
                    METHOD_SP,
                    recovery=True,
                    prompt_key="err_checksum_stall_sp_failover",
                    prompt_kwargs={
                        "failed": _method_label(METHOD_MTK),
                        "next": _method_label(METHOD_SP),
                    },
                )
                return
            try:
                self.sm.transition_to(FlashState.S5_FAILED)
            except ValueError:
                self.sm.force_state(FlashState.S5_FAILED)
            self._show_install_failure("MTK_CHECKSUM_STALLED")
            return
        # A backend that never got going is not the end of the install on a
        # host that has the other engine: hand the user back to the power-off
        # step and start the other one from there.
        if not cache_failure and self._offer_backend_failover(error_code):
            return
        from ..flash_service import is_mtk_method

        if (
            not cache_failure
            and is_mtk_method(self._flash_method)
            and _is_mtk_install_failure(error_code)
        ):
            from ..config import device_label_for_model

            # The engine stopped with the player mid-download. Ask for the
            # unplug / power-off (paperclip reset if needed) confirmation, then
            # Continue starts a fresh run of the same method.
            self._append_log(
                f"MTKClient install failed ({error_code}); asking for a power cycle "
                f"with {_method_label(self._flash_method)}."
            )
            self._return_to_power_off_stage(
                self._flash_method,
                recovery=True,
                prompt_key="install_disconnect_guidance_fmt",
                prompt_kwargs={"label": device_label_for_model(self._package_model)},
            )
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
        Tool at all. On Windows, SP Flash Tool is the native flashing backend
        and does not automatically fail over to MTKclient (which requires
        different USB driver filters). Auto-failover from SP Flash Tool to
        MTKclient is a Linux feature.
        """
        if paths.IS_MAC or paths.IS_WINDOWS:
            return ""
        current = backend_method(method)
        if current == METHOD_SP:
            return METHOD_MTK
        if current == METHOD_MTK:
            # SP Flash Tool ships for x86/x86_64 Linux only.
            from .. import linux_sp_flash

            return METHOD_SP if linux_sp_flash.arch_supported() else ""
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
        # The killed command's children must be gone too: a leftover mtk.py /
        # console SP Flash Tool run holds the USB port, and the retry the user
        # confirms next would then fail on a busy device.
        try:
            from ..flash_service import terminate_sp_sessions

            terminate_sp_sessions()
        except Exception:
            logger.debug("Could not stop leftover flashing sessions", exc_info=True)
        self._elapsed_timer.stop()
        self._install_failed = False
        self._format_all_confirm_pending = False
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
        from ..flash_service import is_attempt_timeout_error

        if is_attempt_timeout_error(error_code):
            # Nothing was there to flash: retry the same method, never switch.
            return False
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

    def _on_progress_retry(self, *, prompt: bool = True):
        """Retry an install failure, using firmware recovery rather than USB reset when needed.

        ``prompt`` keeps the pre-install power-off confirmation for a retry the
        user asked for; an automatic recovery passes False.
        """
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
                self._select_page.retry_missing_cached_firmware(prompt=prompt)
            return
        self.service.cancel_flash()
        self.service.stop_device_monitor()
        self._elapsed_timer.stop()
        self._install_failed = False
        self._saw_all_command_exec = False
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
        if getattr(self, "_install_first_stage", ""):
            self._on_install_first_continue()
            return
        if getattr(self, "_format_all_confirm_pending", False):
            # The destructive retry was accepted. Go back to the ordinary
            # power-off/pin-reset stage; Continue now starts the same SP
            # backend with only its console mode changed.
            self._format_all_confirm_pending = False
            self._format_all_required = True
            self._keep_data = False
            self._flash_page.clear_keep_data_confirmation()
            self._return_to_power_off_stage(self._flash_method)
            self._flash_page.show_format_all_power_off_prompt()
            return
        # Read the card's "Keep my data" tick here: this is the moment
        # the user confirmed the player is off and unplugged, so the run that
        # follows has to obey what the card showed then.
        self._keep_data = self._flash_page.keep_data_checked()
        if getattr(self, "_keep_data_confirm_pending", False):
            choice = self._flash_page.keep_data_confirm_choice()
            if choice and choice == tr("keep_data_choice_other"):
                self._keep_data_confirm_pending = False
                self._keep_data_confirm_known_name = ""
                # "Other" is the user saying the track is wrong: forget it so
                # the next run asks instead of assuming.
                self._forget_keep_data_software()
                self._begin_keep_data_erase_confirmation()
                return
            self._keep_data_confirm_pending = False
            self._keep_data = True
            self._remember_keep_data_answer(choice)
            self._flash_page.clear_keep_data_confirmation()
        elif getattr(self, "_keep_data_erase_confirm_pending", False):
            # The user confirmed continuing without Keep Data with Yes (checkmark)
            self._keep_data_erase_confirm_pending = False
            self._keep_data = False
            self._flash_page.clear_keep_data_confirmation()
        elif self._keep_data and self._keep_data_needs_confirmation():
            self._begin_keep_data_confirmation()
            return
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
        self._saw_all_command_exec = False
        self._failure_title = ""
        self._flash_page.clear_install_failure()
        self._power_off_prompt = False
        self._pre_install_guided = True
        self._flash_page.leave_power_off_prompt()
        self._begin_flash_flow()

    # ------------------------------------------------------------------
    # Install Firmware First (manifest ``install_first``)
    # ------------------------------------------------------------------
    def _install_first_model(self) -> str:
        journey = getattr(self, "_install_first_journey", None)
        target = getattr(journey, "target", None)
        return str(
            self._package_model or getattr(target, "model", "") or ""
        ).upper()

    def _install_first_label(self) -> str:
        from ..config import device_label_for_model

        model = self._install_first_model()
        return device_label_for_model(model) if model else tr("device_fallback_word")

    def _install_first_record_version(self) -> str:
        """The version the tracked install knows, used to skip the version ask."""
        try:
            from ..player_update_check import get_software_install

            journey = getattr(self, "_install_first_journey", None)
            requirement = getattr(journey, "requirement", None)
            if requirement is None:
                return ""
            hardware = (
                self._select_page.current_hardware_type()
                if hasattr(self, "_select_page")
                else ""
            )
            record = get_software_install(
                self._install_first_model(),
                requirement.software_name,
                hardware_type=hardware,
                settings=self.settings,
            ) or {}
            return str(
                record.get("release_label") or record.get("tag_name") or ""
            ).strip()
        except Exception:
            return ""

    def _install_first_selected_answer(self) -> str:
        from .. import install_first

        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            return ""
        choice = ""
        page = getattr(self, "_flash_page", None)
        if page is not None:
            choice = page.keep_data_confirm_choice()
        if choice and choice == journey.requirement.label():
            return install_first.ANSWER_PREREQUISITE
        if choice and choice.casefold() == journey.target_name.casefold():
            return install_first.ANSWER_TARGET
        return install_first.ANSWER_OTHER

    def _guard_install_first(self, pkg, rel) -> bool:
        """Run the prerequisite sanity check before any download starts.

        This is the Select page's install guard: returning ``False`` stops the
        normal install because the card is now asking which firmware the player
        runs. Nothing has been downloaded or extracted at this point, so an
        answer that needs the base cannot waste bandwidth or disk.
        """
        try:
            from .. import install_first
        except Exception:
            return True
        model = str(getattr(pkg, "model", "") or self._package_model or "")
        # No packages list: the resolver reads the catalogue itself, which is
        # what lets a base firmware listed under a sibling model (one build
        # serving Q3 and Q5) still satisfy this model's declaration.
        requirement = install_first.requirement_for(pkg, model)
        if requirement is None:
            return True
        existing = getattr(self, "_install_first_journey", None)
        if (
            existing is not None
            and existing.target is pkg
            and existing.answer
            and not getattr(self, "_install_first_prerequisite_run", False)
        ):
            # A retry of this same target (a failed write, a missing cache)
            # already has its answer; do not ask the same question again. A
            # failed prerequisite leaves the flag set, so its retry does ask
            # (and starts the base again) rather than trusting an answer about
            # a base that was never written.
            return True
        if requirement.package is None:
            self._show_status(
                tr("install_first_missing").format(software=requirement.software_name),
                15000,
            )
            return False
        journey = install_first.InstallFirstJourney(
            target=pkg,
            target_rel=dict(rel or {}),
            requirement=requirement,
        )
        self._install_first_journey = journey
        self._install_first_after = None
        self._install_first_prerequisite_run = False
        self._package_name = str(getattr(pkg, "name", "") or self._package_name)
        self._package_model = model
        page = getattr(self, "_flash_page", None)
        if page is not None:
            page.set_package_name(self._package_name)
            page.set_model(self._package_model)
            page.set_method(self._flash_method)
        self._ask_install_first_software(journey)
        self._nav_to_page(_PAGE_FLASH)
        return False

    def _local_fw_package_for_path(self, path):
        """The parsed local package behind a selection, when it has one."""
        try:
            from pathlib import Path
            from ..fw_package import locate_package
            from ..paths import INSTALL_DIR
        except Exception:
            return None
        if not path:
            return None
        p = Path(str(path))
        page = getattr(self, "_select_page", None)
        current = getattr(page, "_fw_package", None)
        bundled = getattr(self, "_bundled_fw_package", None)
        try:
            if bundled is not None:
                bundled_path = str(getattr(self, "_bundled_package_path", "") or "")
                if str(p) == bundled_path or p.resolve() == Path(bundled.folder).resolve():
                    return bundled
            if current is not None:
                target = p.parent if p.is_file() else p
                if current.folder.resolve() == target.resolve():
                    return current
        except OSError:
            pass
        root = p if p.is_dir() else p.parent
        try:
            return locate_package(
                root,
                require_ini=False,
                app_dir=INSTALL_DIR,
                source_names=(p.name,),
            )
        except Exception:
            return None

    def _guard_local_install_first(self, path) -> bool:
        """Ask the install-first question for a locally browsed package.

        This mirrors :meth:`_guard_install_first`, but the target and its
        prerequisite are local ``FwPackage`` objects. When the portable zip
        carries ``pre_stage``, the journey uses it directly; otherwise the
        ordinary catalogue release lookup remains available for the download.
        """
        from dataclasses import replace

        try:
            from .. import fw_package, install_first
        except Exception:
            return True
        page = getattr(self, "_select_page", None)
        if page is None or getattr(page, "_current_installed_release_info", None):
            return True
        pkg = self._local_fw_package_for_path(path)
        if pkg is None or not str(getattr(pkg, "install_first", "") or "").strip():
            return True
        model = str(getattr(pkg, "model", "") or self._package_model or "")
        target = fw_package.package_as_catalogue(pkg)
        requirement = install_first.requirement_for(target, model)
        if requirement is None:
            return True
        local_base = getattr(pkg, "install_first_package", None)
        if local_base is not None:
            requirement = replace(
                requirement,
                package=fw_package.package_as_catalogue(local_base),
            )

        existing = getattr(self, "_install_first_journey", None)
        existing_target = getattr(existing, "target", None) if existing is not None else None
        same_target = existing_target is target or bool(
            getattr(existing_target, "slug", "")
            and getattr(existing_target, "slug", "") == getattr(target, "slug", "")
        )
        if (
            existing is not None
            and same_target
            and existing.answer
            and not getattr(self, "_install_first_prerequisite_run", False)
        ):
            return True
        if requirement.package is None:
            self._show_status(
                tr("install_first_missing").format(software=requirement.software_name),
                15000,
            )
            return False

        rel = fw_package.local_install_release(pkg)
        if pkg is getattr(self, "_bundled_fw_package", None):
            staged = str(getattr(self, "_bundled_package_path", "") or "")
            if staged:
                rel["local_install_path"] = staged
        journey = install_first.InstallFirstJourney(
            target=target,
            target_rel=rel,
            requirement=requirement,
            local_prerequisite=local_base,
        )
        self._install_first_journey = journey
        self._install_first_after = None
        self._install_first_prerequisite_run = False
        self._package_name = target.name or self._package_name
        self._package_model = model
        flash = getattr(self, "_flash_page", None)
        if flash is not None:
            flash.set_package_name(self._package_name)
            flash.set_model(self._package_model)
            flash.set_method(self._flash_method)
        self._ask_install_first_software(journey)
        self._nav_to_page(_PAGE_FLASH)
        return False

    @staticmethod
    def _same_software_name(left, right) -> bool:
        """Loose name match: a tracked ``Reborn`` fits ``Reborn (Y2Linux)``."""
        a = str(left or "").strip().casefold()
        b = str(right or "").strip().casefold()
        if not a or not b:
            return False
        return a == b or a in b or b in a

    def _install_first_hardware_type(self) -> str:
        return (
            self._select_page.current_hardware_type()
            if hasattr(self, "_select_page")
            else ""
        )

    def _install_first_known_answer(self, journey, known) -> str:
        """The answer a tracked software implies, or ``""`` when it implies none."""
        from .. import install_first

        name = str((known or {}).get("software_name") or "").strip()
        if not name or journey.requirement is None:
            return ""
        if self._same_software_name(name, journey.requirement.software_name):
            return install_first.ANSWER_PREREQUISITE
        if self._same_software_name(name, journey.target_name):
            return install_first.ANSWER_TARGET
        return ""

    def _ask_install_first_software(self, journey) -> None:
        """Ask what the player runs — a confirmation when that is already known.

        A tracked install or an earlier answer often says what this model runs.
        The menu then becomes one question, "is it still running X?":
        confirming the base goes straight to the target, and confirming the
        target is what lets Keep Data carry the player's settings over. Saying
        no forgets the answer and asks the full question instead.
        """
        from .. import keep_data

        requirement = journey.requirement
        page = getattr(self, "_flash_page", None)
        model = self._install_first_model()
        known = keep_data.known_running_software(
            model, self.settings, self._install_first_hardware_type()
        )
        name = str((known or {}).get("software_name") or "").strip()
        answer = self._install_first_known_answer(journey, known)
        self._install_first_running_answer = answer
        self._install_first_running_name = name if answer else ""
        if answer:
            self._install_first_stage = "running"
            if page is not None:
                page.show_install_first_prompt(
                    tr("keep_data_running_fmt").format(
                        software=name, model=self._install_first_label()
                    ),
                    tr("install_first_hint").format(
                        software=requirement.label(),
                        model=self._install_first_label(),
                        target=journey.target_name,
                    ),
                    (),
                    on_no=self._on_install_first_running_no,
                )
            self._sync_install_nav_entry()
            self._adjust_window_geometry()
            return
        selected = journey.target_name
        if name and self._same_software_name(name, requirement.software_name):
            selected = requirement.label()
        self._install_first_stage = "software"
        if page is not None:
            page.show_install_first_prompt(
                tr("keep_data_which_firmware_fmt").format(model=self._install_first_label()),
                tr("install_first_hint").format(
                    software=requirement.label(),
                    model=self._install_first_label(),
                    target=journey.target_name,
                ),
                (journey.target_name, requirement.label(), tr("keep_data_choice_other")),
                selected=selected,
                on_no=self._cancel_install_first,
            )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()

    def _on_install_first_running_no(self) -> None:
        """The player is not running what was tracked; ask the full question."""
        from .. import keep_data

        journey = getattr(self, "_install_first_journey", None)
        self._install_first_running_answer = ""
        self._install_first_running_name = ""
        self._install_first_stage = ""
        page = getattr(self, "_flash_page", None)
        if page is not None:
            page.clear_keep_data_confirmation()
        if journey is None:
            self._cancel_install_first()
            return
        keep_data.forget_running_software(
            self._install_first_model(),
            self.settings,
            self._install_first_hardware_type(),
        )
        self._ask_install_first_software(journey)

    def _answer_install_first_running(self, confirmed: bool) -> None:
        """Answer the confirmation: keep the tracked software, or forget it."""
        from .. import install_first, keep_data

        journey = getattr(self, "_install_first_journey", None)
        answer = getattr(self, "_install_first_running_answer", "")
        name = getattr(self, "_install_first_running_name", "")
        if journey is None or not answer:
            self._cancel_install_first()
            return
        if not confirmed:
            self._on_install_first_running_no()
            return
        model = self._install_first_model()
        hardware = self._install_first_hardware_type()
        known = keep_data.known_running_software(model, self.settings, hardware)
        version = str(
            (known or {}).get("release_label") or (known or {}).get("tag_name") or ""
        ).strip()
        keep_data.note_running_software(model, name, self.settings, hardware)
        step = journey.choose(answer, version)
        if step == install_first.ASK_VERSION:
            # The confirmation is the answer: the player runs the base (or a
            # build nothing ever versioned), so do not ask the same thing twice.
            step = journey.confirm_version(True)
        self._install_first_stage = ""
        self._install_first_running_answer = ""
        if step == install_first.PROCEED:
            self._proceed_with_install_first_target()
        else:
            self._confirm_install_first_prerequisite()

    def _remember_install_first_answer(self, journey, answer: str) -> None:
        """Store what an answer said this model runs, without a version.

        A working player is the evidence, not firmware sitting in flash: this
        answer is what lets the next run ask "is it still running X?" instead
        of the full menu, while a finished install is what records the build.
        """
        from .. import install_first, keep_data

        model = self._install_first_model()
        hardware = self._install_first_hardware_type()
        if answer == install_first.ANSWER_PREREQUISITE:
            keep_data.note_running_software(
                model, journey.requirement.software_name, self.settings, hardware
            )
        elif answer == install_first.ANSWER_TARGET:
            keep_data.note_running_software(
                model, journey.target_name, self.settings, hardware
            )
        else:
            keep_data.forget_running_software(model, self.settings, hardware)

    def _on_install_first_continue(self) -> None:
        """The card's Continue/tick: answer the stage that is on screen."""
        from .. import install_first

        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            self._cancel_install_first()
            return
        stage = getattr(self, "_install_first_stage", "")
        self._install_first_stage = ""
        page = getattr(self, "_flash_page", None)
        if page is not None:
            page.clear_keep_data_confirmation()
        if stage == "power_on":
            self._resume_install_first_target_after_power_on()
            return
        if stage == "running":
            self._answer_install_first_running(True)
            return
        if stage == "version":
            step = journey.confirm_version(True)
        elif stage == "confirm":
            self._start_install_first_prerequisite()
            return
        else:
            answer = self._install_first_selected_answer()
            step = journey.choose(answer, self._install_first_record_version())
            self._remember_install_first_answer(journey, answer)
        if step == install_first.PROCEED:
            self._proceed_with_install_first_target()
        elif step == install_first.ASK_VERSION:
            self._ask_install_first_version()
        else:
            self._confirm_install_first_prerequisite()

    def _resume_install_first_target_after_power_on(self) -> None:
        """The MTK base has been powered on once; start the target install."""
        previous, rel = self._install_first_after or (None, None)
        self._install_first_after = None
        if previous is None or not rel:
            self._cancel_install_first()
            return
        self._select_page.start_release_install(rel, previous, prompt=False)

    def _ask_install_first_version(self) -> None:
        from .. import install_first

        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            self._cancel_install_first()
            return
        requirement = journey.requirement
        version = (
            requirement.version_range.label()
            if requirement.version_range is not None
            else requirement.version_text
        )
        self._install_first_stage = "version"
        self._flash_page.show_install_first_prompt(
            tr("install_first_version_fmt").format(
                model=self._install_first_label(),
                software=requirement.software_name,
                version=version,
            ),
            tr("install_first_hint").format(
                software=requirement.label(),
                model=self._install_first_label(),
                target=journey.target_name,
            ),
            (),
            on_no=self._on_install_first_version_no,
        )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()

    def _on_install_first_version_no(self) -> None:
        from .. import install_first

        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            self._cancel_install_first()
            return
        self._install_first_stage = ""
        self._flash_page.clear_keep_data_confirmation()
        if journey.confirm_version(False) == install_first.PROCEED:
            self._proceed_with_install_first_target()
        else:
            self._confirm_install_first_prerequisite()

    def _confirm_install_first_prerequisite(self) -> None:
        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            self._cancel_install_first()
            return
        requirement = journey.requirement
        self._install_first_stage = "confirm"
        self._flash_page.show_install_first_prompt(
            tr("install_first_need_title").format(
                software=requirement.software_name, target=journey.target_name
            ),
            tr("install_first_need_body").format(
                software=requirement.label(),
                target=journey.target_name,
                model=self._install_first_label(),
            ),
            (),
            on_no=self._cancel_install_first,
        )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()

    def _proceed_with_install_first_target(self) -> None:
        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            return
        self._install_first_stage = ""
        self._install_first_power_off_only = False
        self._flash_page.set_power_off_only(False)
        self._flash_page.clear_keep_data_confirmation()
        self._select_page.start_release_install(
            journey.target_rel, journey.target, prompt=False
        )

    def _install_first_filter_rows(self, rows, requirement) -> list:
        """Rows of the prerequisite release, newest first, honouring its range."""
        from .. import catalog, versioning

        usable = []
        for rel in rows or []:
            if requirement.version_range is None:
                usable.append(rel)
                continue
            version = versioning.release_version_tuple(
                rel.get("tag_name", ""), rel.get("name", "")
            )
            # A tag that cannot be read cannot be shown to satisfy the
            # declared version, so it is not a candidate for the base. This
            # matters most for an exact "=3.2.0": a newer build is not a
            # substitute, and the old all-rows fallback silently installed one.
            if version is not None and requirement.allows(version):
                usable.append(rel)
        return sorted(usable, key=catalog.release_sort_key, reverse=True)

    def _start_install_first_prerequisite(self) -> None:
        journey = getattr(self, "_install_first_journey", None)
        if journey is None:
            self._cancel_install_first()
            return
        requirement = journey.requirement
        page = self._select_page
        local_base = getattr(journey, "local_prerequisite", None)
        if local_base is not None:
            # The portable zip carries the base in pre_stage: use its own
            # scatter and images instead of downloading and extracting a
            # second copy of the same firmware.
            self._install_first_after = (journey.target, journey.target_rel)
            self._install_first_prerequisite_run = True
            self._install_first_stage = ""
            self._install_first_power_off_only = False
            self._flash_page.set_power_off_only(False)
            self._flash_page.clear_keep_data_confirmation()
            self._keep_data = False
            self._append_log(
                tr("install_first_starting").format(
                    software=requirement.software_name, target=journey.target_name
                )
            )
            page.start_local_install(local_base, prompt=False)
            return
        try:
            rows = page.client.releases_for_package(requirement.package, self._install_first_model())
        except Exception as exc:
            logger.debug("Install First: prerequisite releases failed: %s", exc)
            rows = []
        fetched_rows = list(rows or [])
        rows = self._install_first_filter_rows(fetched_rows, requirement)
        if not rows:
            if fetched_rows:
                version = (
                    requirement.version_range.label()
                    if requirement.version_range is not None
                    else requirement.version_text
                )
                message = tr("install_first_version_unavailable").format(
                    software=requirement.software_name, version=version
                )
            else:
                message = tr("install_first_missing").format(
                    software=requirement.software_name
                )
            self._show_status(message, 15000)
            self._cancel_install_first()
            return
        self._install_first_after = (journey.target, journey.target_rel)
        self._install_first_prerequisite_run = True
        self._install_first_stage = ""
        self._install_first_power_off_only = False
        self._flash_page.set_power_off_only(False)
        self._flash_page.clear_keep_data_confirmation()
        self._keep_data = False
        self._append_log(
            tr("install_first_starting").format(
                software=requirement.software_name, target=journey.target_name
            )
        )
        page.start_release_install(rows[0], requirement.package, prompt=False)

    def _finish_install_first_prerequisite(self) -> None:
        """The base is installed: continue straight into the requested overlay."""
        journey = getattr(self, "_install_first_journey", None)
        previous, rel = self._install_first_after or (None, None)
        self._install_first_prerequisite_run = False
        self._install_first_after = None
        # The prerequisite's own extraction is released with the journey when
        # the target finishes; purging here would compare the target's selected
        # release with the base's working directory and could remove the wrong
        # cache entry.
        self._flash_page.clear_keep_data_confirmation()
        self._install_complete = False
        self._install_failed = False
        self._pre_install_guided = False
        self._keep_data = False
        self._format_all_required = False
        if journey is not None:
            self._append_log(
                tr("install_first_base_done").format(
                    software=journey.requirement.software_name,
                    target=journey.target_name,
                )
            )
        if previous is None or not rel:
            return
        if self._install_first_used_mtk():
            # MTKclient finishes with the player off and disconnected. The
            # first-time base needs one normal power-on before the overlay is
            # flashed; SP Flash Tool powers the player on itself, so it keeps
            # the ordinary target prep instead.
            self._install_first_after = (previous, rel)
            self._install_first_power_off_only = True
            self._install_first_stage = "power_on"
            self._flash_page.set_power_off_only(False)
            self._flash_page.show_install_first_prompt(
                tr("install_first_power_on_fmt").format(
                    model=self._install_first_label()
                ),
                tr("install_first_power_on_hint"),
                (),
                on_no=self._cancel_install_first,
            )
            self._sync_install_nav_entry()
            self._nav_to_page(_PAGE_FLASH)
            self._adjust_window_geometry()
            return
        self._select_page.start_release_install(rel, previous, prompt=False)

    def _install_first_used_mtk(self) -> bool:
        """True when the firmware just written went through MTKclient."""
        try:
            method = str(self._flash_page.current_method() or "").strip().lower()
        except Exception:
            return False
        return "mtk" in method

    def _cancel_install_first(self) -> None:
        self._install_first_journey = None
        self._install_first_stage = ""
        self._install_first_after = None
        self._install_first_prerequisite_run = False
        self._install_first_power_off_only = False
        page = getattr(self, "_flash_page", None)
        if page is not None:
            page.set_power_off_only(False)
            page.clear_keep_data_confirmation()
        self._return_to_select_software()

    def _install_first_already_on_target(self) -> bool:
        from .. import install_first

        journey = getattr(self, "_install_first_journey", None)
        return bool(journey is not None and journey.answer == install_first.ANSWER_TARGET)

    def _install_first_forces_no_keep_data(self) -> bool:
        """True when the answer means this target starts on a fresh partition."""
        from .. import catalog, install_first

        journey = getattr(self, "_install_first_journey", None)
        if getattr(self, "_install_first_prerequisite_run", False):
            return True
        if journey is None or journey.answer in ("", install_first.ANSWER_TARGET):
            return False
        try:
            if install_first.related_by_derivative(
                journey.target,
                journey.requirement,
                catalog.packages_for_model(self._install_first_model()),
            ):
                return False
        except Exception:
            pass
        return True

    def _keep_data_default_for_install(self) -> bool:
        """True when this run should start with Keep my settings ticked.

        Accepting an update notification is the user saying they want the
        upgrade without starting over. Reinstalling the exact firmware that is
        already tracked is the same intent. A first install or a change to a
        different firmware still asks the user to opt in.
        """
        if self._install_first_already_on_target():
            # Reinstalling the firmware already on the player: its data is
            # already on the right partition, even when the catalogue forbids
            # Keep Data for a first install of this overlay.
            return True
        if self._install_first_forces_no_keep_data():
            return False
        try:
            from .. import keep_data

            pkg = getattr(self, "_keep_data_pkg", None) or self._current_firmware_package()
            model = self._package_model or getattr(pkg, "model", "") or ""
            hw = (
                self._select_page.current_hardware_type()
                if hasattr(self, "_select_page")
                else ""
            )
            # Never pre-tick a package that forbids Keep Data or needs a
            # destructive format: the checkbox would be hidden but still read
            # as checked when the user continues.
            if not keep_data.keep_data_available(
                model, pkg, self.settings, hardware_type=hw
            ):
                return False
            if getattr(self, "_keep_settings_default", False):
                return True
            return keep_data.tracked_is_same(
                model, pkg, self.settings, hardware_type=hw
            )
        except Exception:
            logger.debug("Could not evaluate the Keep Data default", exc_info=True)
            return False

    def _keep_data_needs_confirmation(self) -> bool:
        if self._install_first_already_on_target() or self._install_first_forces_no_keep_data():
            return False
        if self._keep_data_default_for_install():
            # An update carries the player's data forward by definition; there
            # is no "which firmware is running?" question to answer.
            return False
        try:
            from .. import keep_data

            pkg = getattr(self, "_keep_data_pkg", None)
            model = self._package_model or getattr(pkg, "model", "") or ""
            hw = self._select_page.current_hardware_type() if hasattr(self, "_select_page") else ""
            return keep_data.needs_confirmation(model, pkg, self.settings, hardware_type=hw)
        except Exception:
            return False

    def _begin_keep_data_confirmation(self) -> None:
        """Ask whether the player is currently running compatible firmware, in-card."""
        from .. import keep_data
        from ..config import device_label_for_model

        pkg = getattr(self, "_keep_data_pkg", None)
        model = self._package_model or getattr(pkg, "model", "") or ""
        model_name = device_label_for_model(model) if model else tr("device_fallback_word")
        software = str(getattr(pkg, "name", "") or self._package_name or "").strip()
        names = keep_data.compatible_software_names(pkg, model) if pkg is not None else []
        hw = self._select_page.current_hardware_type() if hasattr(self, "_select_page") else ""
        known = keep_data.known_running_software(model, self.settings, hw)
        known_name = str((known or {}).get("software_name") or "").strip()
        self._keep_data_confirm_known_name = ""
        if known_name and any(
            known_name.casefold() == str(candidate).casefold() for candidate in names
        ):
            # The player's software is already tracked, so the sanity check is
            # a confirmation of that one name rather than a menu: it is the
            # same yes/no the single-compatible case asks, with the answer
            # already filled in.
            self._keep_data_confirm_known_name = known_name
            question = tr("keep_data_running_fmt").format(
                model=model_name, software=known_name
            )
            choices = ()
            selected = known_name
        elif len(names) > 1:
            question = tr("keep_data_which_firmware_fmt").format(model=model_name)
            choices = tuple(names) + (tr("keep_data_choice_other"),)
            selected = names[0]
        else:
            software_label = names[0] if names else (software or tr("software_original"))
            question = tr("keep_data_running_fmt").format(
                model=model_name, software=software_label
            )
            choices = ()
            selected = names[0] if names else ""
        self._keep_data_confirm_pending = True
        y1_like = "Y1" in model.upper() or "A5" in model.upper()
        if y1_like:
            hint = tr("keep_data_only_compatible_hint_y1")
        else:
            hint = tr("keep_data_only_compatible_hint_other")
        self._flash_page.show_keep_data_confirmation(
            question,
            hint,
            choices,
            selected=selected,
            on_no=self._on_keep_data_not_compatible,
        )
        self._sync_install_nav_entry()
        self._adjust_window_geometry()

    def _on_keep_data_not_compatible(self) -> None:
        """The player is not running the one compatible software item.

        Answering No to the sanity check means Keep Data is not safe for this
        run, so the install explains that the player's settings cannot carry
        over instead of silently keeping a partition the firmware may not read.
        """
        self._keep_data_confirm_pending = False
        self._keep_data_confirm_known_name = ""
        self._forget_keep_data_software()
        self._keep_data = False
        self._begin_keep_data_erase_confirmation()

    def _keep_data_tracking_context(self):
        """The model and hardware type the Keep Data answer is about."""
        pkg = getattr(self, "_keep_data_pkg", None)
        model = self._package_model or getattr(pkg, "model", "") or ""
        hw = (
            self._select_page.current_hardware_type()
            if hasattr(self, "_select_page")
            else ""
        )
        return model, hw

    def _remember_keep_data_answer(self, choice: str) -> None:
        """Store the software the user just confirmed as running, no version."""
        from .. import keep_data

        picked = str(choice or "").strip()
        name = str(getattr(self, "_keep_data_confirm_known_name", "") or "").strip()
        if picked and picked != tr("keep_data_choice_other"):
            name = picked
        self._keep_data_confirm_known_name = ""
        if not name:
            return
        model, hw = self._keep_data_tracking_context()
        try:
            keep_data.note_running_software(model, name, self.settings, hw)
        except Exception:
            logger.debug("Could not record the confirmed software", exc_info=True)

    def _forget_keep_data_software(self) -> None:
        """Clear the tracked software: the answer said it is something else."""
        from .. import keep_data

        model, hw = self._keep_data_tracking_context()
        try:
            keep_data.forget_running_software(model, self.settings, hw)
        except Exception:
            logger.debug("Could not clear the tracked software", exc_info=True)

    def _begin_keep_data_erase_confirmation(self) -> None:
        """Explain that data cannot be kept with the current firmware, and ask whether to proceed."""
        from ..config import device_label_for_model

        model = self._package_model or ""
        y1_like = "Y1" in model.upper() or "A5" in model.upper()
        model_name = device_label_for_model(model) if model else tr("device_fallback_word")
        if y1_like:
            question = tr("keep_data_erase_prompt_y1").format(model=model_name)
            explainer = tr("keep_data_erase_explainer_y1")
        else:
            question = tr("keep_data_erase_prompt_other").format(model=model_name)
            explainer = tr("keep_data_erase_explainer_other")
        pkg = getattr(self, "_keep_data_pkg", None)
        if getattr(pkg, "do_not_keep_data", False):
            import re
            explainer = re.sub(r'<a\s+href=["\']try-anyway["\'].*?</a>', '', explainer, flags=re.IGNORECASE).strip()
        self._keep_data_erase_confirm_pending = True
        self._flash_page.show_keep_data_erase_confirmation(question, explainer)
        self._sync_install_nav_entry()
        self._adjust_window_geometry()

    def _on_keep_data_try_anyway(self) -> None:
        """Subvert Keep Data restriction for this particular install."""
        self._keep_data = True
        self._keep_data_confirm_pending = False
        self._keep_data_erase_confirm_pending = False
        self._flash_page.clear_keep_data_confirmation()
        self._install_failed = False
        self._saw_all_command_exec = False
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
        self._retry_page.update_info(
            self._package_name, self.sm.context.retry_count, "status_retrying"
        )
        self._retry_page.update_progress(0)
        self._nav_to_page(_PAGE_RETRY)
        QTimer.singleShot(0, self._begin_flash_flow)

    def _on_reconnect(self):
        self._set_state(FlashState.S2_WAIT_CONNECTION)
        # The same connect card as every other path into the wait: the retry
        # page used to raise only the sentence, leaving whatever card the
        # failed run ended on (or the software-titled one) on screen.
        self._flash_page.show_connect_search()
        self._nav_to_page(_PAGE_FLASH)

    def _on_cancel_wait(self):
        if getattr(self, "_install_first_stage", ""):
            self._cancel_install_first()
            return
        if getattr(self, "_keep_data_confirm_pending", False):
            self._keep_data_confirm_pending = False
            self._begin_keep_data_erase_confirmation()
            return
        if getattr(self, "_keep_data_erase_confirm_pending", False):
            self._keep_data_erase_confirm_pending = False
            self._flash_page.clear_keep_data_confirmation()
            self._show_device_prep()
            return
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
        # Kept silent for the user: the app is a front end for both engines and
        # the install flow should read the same either way. The restart is a
        # diagnostic detail, and the new command must not start until the user
        # has again confirmed the player is unplugged and powered off.
        logger.info("Install method changed to %s", backend_method(method))
        self._return_to_power_off_stage(method)

    def _stop_running_install(self) -> None:
        """Stop a download or flash so it does not keep running in the background."""
        self._download_active = False
        # Abandoning a run ends the window in which a backend may hold the USB
        # port, and with it the system-awake hold: nothing is touching the
        # player from here. Cancel first so the sweep cannot take a live writer
        # with it, and let the sweep run off the GUI thread.
        self.themes.finish()
        try:
            self.service.cancel_flash()
            self.service.stop_device_monitor()
        except Exception:
            logger.debug("Stopping the install run failed", exc_info=True)
        from .. import process_guard

        process_guard.end_install_session(reason="install stopped", sweep=False)
        process_guard.sweep_async(reason="install stopped", on_done=self.sweep_report.emit)
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
            self._release_install_cache()
            self._reset_after_run()
        finally:
            self._returning_to_select = False

    def _release_install_cache(self) -> None:
        """Drop this journey's firmware after the user has left the install.

        A failure and every retry keep the extracted working directory. This is
        the one place that releases it, called when the journey is exited, not
        while the failure card is still offering Retry.
        """
        page = getattr(self, "_select_page", None)
        purge = getattr(page, "purge_release_cache", None)
        if not callable(purge):
            return
        package_path = str(getattr(self, "_package_path", "") or "")
        if not package_path:
            # Nothing was opened for install, so there is no journey to release.
            return
        release = getattr(page, "_current_selected_rel", None)
        package = getattr(page, "_details_package", None)
        if release is None and package is None:
            return
        if isinstance(release, dict):
            asset = Path(str(release.get("asset_name") or "")).name.casefold()
            package_name = Path(package_path).name.casefold()
            asset_stem = Path(asset).stem.casefold()
            matches_archive = bool(asset) and asset in package_name
            matches_extract = bool(asset_stem) and package_name.startswith(
                f".{asset_stem}_"
            )
            if asset and not (matches_archive or matches_extract):
                # A different release was selected after the run; its cache is
                # not this journey's to remove.
                return
        try:
            removed = purge(release, package)
        except Exception:
            logger.debug("Could not release the install cache", exc_info=True)
            return
        if removed:
            self._append_log(
                "Released this install's cached firmware after leaving the install."
            )

    def _quit_after_bundled_run(self) -> None:
        """Stop the ROM-bundled run, clean its files up, then close the app.

        The stop prompt has already been answered. A portable run must not
        leave staged firmware, extractions or caches behind, so quitting shows
        the same progress card as the install while that work is done.
        """
        if self._portable_cleanup_required():
            self._start_portable_cleanup()
            return
        self._stop_running_install()
        self._force_quit = True
        self.close()

    def _on_cancel_flash(self):
        # Only the ✕ (confirm stop) button lands here, via the stop-check
        # callback. The ← (back) button only dismisses the check inside the
        # progress card so the run keeps going — it must never navigate away.
        run = getattr(self, "_sim_flash_run", None)
        if run is not None:
            run.stop()
            self._sim_flash_run = None
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.dismiss_prompt_stop()
        self._return_to_select_software()

    def _on_completion_not_now(self):
        """Dismiss the appeal. In a portable run this is the quit gesture.

        The machine keeps nothing from the run, so Not now starts the cleanup
        pass and leaves; a catalogue install still returns to Select Software.
        """
        if getattr(self, "_bundled_firmware_run", False):
            self._start_portable_cleanup()
            return
        self._on_install_done()

    def _on_completion_dont_ask(self):
        """Stop the appeal for good, then leave the install journey.

        Turning the prompt off is a preference, not a request to keep looking
        at the install card: the run is already finished, so this ends the same
        way Not now does and the catalogue is back where the user started.
        """
        if getattr(self, "_bundled_firmware_run", False) and not self._portable_app_installed():
            # The card already explains why a portable run cannot keep this
            # preference; nothing may be written for a one-off run.
            return
        device_tracking.set_donation_ui_disabled(True, self.settings)
        device_tracking.set_donation_install_prompt_disabled(True, self.settings)
        self._flash_page.show_completion_appeal(False)
        if hasattr(self, "_settings_page"):
            self._settings_page.refresh_settings()
        self._apply_donation_visibility(True)
        if getattr(self, "_bundled_firmware_run", False):
            # A ROM-bundled portable run has no catalogue to return to; it
            # keeps the compact completion card.
            self._compact_install_screen = True
            self._flash_page.show_completion_note(
                True, software=self._package_name, model=self._package_model
            )
            self._apply_release_icon(complete=True)
            self._adjust_window_geometry()
            return
        self._on_install_done()

    def _sync_install_app_button(self) -> None:
        """Offer the real install on a portable run's completion card."""
        page = getattr(self, "_flash_page", None)
        if page is None or not hasattr(page, "show_install_app"):
            return
        available = bool(getattr(self, "_bundled_firmware_run", False)) and bool(
            getattr(self, "_install_complete", False)
        )
        if not available:
            page.show_install_app(False)
            return
        page.show_install_app(
            True, tr("portable_keep_app") if paths.IS_LINUX else tr("portable_install_app")
        )

    def _install_portable_app(self) -> None:
        """Install or keep the app from a portable run's completion card.

        Windows only has the guide to point at. macOS and Linux reuse the
        normal update download: macOS reveals the disk image so the app can be
        dragged into Applications, Linux keeps the matching AppImage.
        """
        if not getattr(self, "_bundled_firmware_run", False):
            return
        if paths.IS_WINDOWS:
            from ..browser import open_browser

            open_browser("https://innioasis.app/guide.html")
            return

        from .dialogs import UpdateAvailableDialog
        from ..updates import UpdateChecker

        info = getattr(self, "_pending_app_update", None)
        if info is None or not str(getattr(info, "tag", "") or ""):
            try:
                info = UpdateChecker(UPDATE_REPO).simulate_latest(force=True)
            except Exception:
                logger.debug("Could not look up the app build to install", exc_info=True)
                info = None
        if info is None or not str(getattr(info, "tag", "") or ""):
            from ..browser import open_browser

            open_browser(f"https://github.com/{UPDATE_REPO}/releases")
            return

        label = tr("portable_keep_app") if paths.IS_LINUX else tr("portable_install_app")
        if paths.IS_MAC:
            body = tr("portable_install_app_mac_fmt")
        elif paths.IS_LINUX:
            body = tr("portable_keep_app_fmt")
        else:
            body = tr("portable_install_app_fmt")
        dialog = UpdateAvailableDialog(
            self,
            info=info,
            current_version=APP_VERSION,
            on_skip=lambda v: self.settings.setValue("update_skipped_version", v),
            install_mode=True,
            install_label=label,
            install_body=body,
            on_finished=self._quit_after_portable_app,
        )
        self._present_inline(dialog, label)

    def _offer_portable_app_install(self) -> None:
        """A portable switch that needs the app installed opens its install screen.

        Run in Background and Notifications cannot be kept by a run that
        removes its own files when it quits. The attempt opens the app
        install (or update) screen instead; while a flash owns the window
        its card is the install screen, and its completion offers the same
        install.
        """
        if self._install_run_active():
            self._nav_to_page(_PAGE_FLASH)
            return
        self._install_portable_app()

    def _quit_after_portable_app(self) -> None:
        """The install package is on disk: clean the portable run up and leave."""
        if self._portable_cleanup_required():
            self._start_portable_cleanup()
            return
        self._force_quit = True
        self.close()

    def _portable_app_installed(self) -> bool:
        """True when a real Updater CE install can read what this run stores.

        A portable run shares the installed app's settings store, so a
        preference saved here is the one the app reads on its next launch.
        Without an install the run is a one-off and must write nothing.
        """
        try:
            return bool(paths.installed_app_present())
        except Exception:
            return False

    def _portable_cleanup_required(self) -> bool:
        return bool(
            getattr(self, "_bundled_firmware_run", False)
            and getattr(self, "_portable_session", None) is not None
            and not getattr(self, "_portable_cleanup_done", False)
        )

    def _start_portable_cleanup(self) -> None:
        """Stop the run, show the cleanup card, then quit when it is empty."""
        if getattr(self, "_portable_cleanup_started", False) or getattr(
            self, "_portable_cleanup_done", False
        ):
            return
        session = getattr(self, "_portable_session", None)
        if session is None:
            self._portable_cleanup_done = True
            self._force_quit = True
            self.close()
            return
        self._portable_cleanup_started = True
        self._stop_running_install()
        try:
            self.service.stop_device_monitor()
        except Exception:
            pass
        page = getattr(self, "_flash_page", None)
        if page is not None and hasattr(page, "show_cleanup"):
            page.show_cleanup()
        self._nav_to_page(_PAGE_FLASH)
        self._adjust_window_geometry()
        worker = _PortableCleanupWorker(session, self)
        self._portable_cleanup_worker = worker
        worker.progress.connect(self._on_portable_cleanup_progress)
        worker.finished.connect(self._on_portable_cleanup_finished)
        worker.start()

    def _on_portable_cleanup_progress(self, percent, key) -> None:
        page = getattr(self, "_flash_page", None)
        if page is not None and hasattr(page, "set_cleanup_progress"):
            page.set_cleanup_progress(int(percent), tr(str(key)))

    def _on_portable_cleanup_finished(self, removed, error) -> None:
        if error:
            logger.warning("Portable cleanup did not finish cleanly: %s", error)
        self._portable_cleanup_done = True
        page = getattr(self, "_flash_page", None)
        if page is not None and hasattr(page, "finish_cleanup"):
            page.finish_cleanup()
        QTimer.singleShot(650, self._quit_after_portable_cleanup)

    def _quit_after_portable_cleanup(self) -> None:
        self._force_quit = True
        self.close()

    def _apply_release_icon(self, complete: bool = False) -> None:
        """Squircle until a release icon is cached, then that image. The check overlays either."""
        from .release_icon import load_release_pixmap
        from ..release_icons import icon_candidate_urls, release_image_fallback_urls
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
        if not icon_candidate_urls(release, package, dark=dark) and not release_image_fallback_urls(release, package, dark=dark):
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
        self.themes.clear_power_on()
        self.themes.finish()
        self._install_complete = False
        self._install_failed = False
        self._power_off_prompt = False
        # A keep-data question that was on screen when the run was abandoned is
        # part of the run. Left set, it keeps ``_install_run_active`` true, which
        # blocks the navigation back to Select Software and leaves the window
        # holding the progress card's compact height — the catalogue came back
        # as a strip too short to read.
        self._keep_data_confirm_pending = False
        self._keep_data_erase_confirm_pending = False
        self._format_all_confirm_pending = False
        self._install_first_journey = None
        self._install_first_stage = ""
        self._install_first_after = None
        self._install_first_prerequisite_run = False
        self._install_first_power_off_only = False
        self._keep_data = False
        if hasattr(self, "_flash_page") and self._flash_page is not None:
            self._flash_page.set_power_off_only(False)
            self._flash_page.clear_keep_data_confirmation()
        self._compact_install_screen = False
        self._download_active = False
        self._prep_active = False
        self._cache_recovery_needed = False
        self._fallback_attempted = False
        self._sp_1013_seen = False
        self._flash_method = self._preferred_flash_method()
        self._flash_page.set_method(self._flash_method)
        self._flash_page.set_release_icon(QPixmap(), complete=False)
        self._flash_page.show_install_app(False)
        self.sm.reset_full()
        self._package_path = ""
        self._package_name = ""
        self._flash_start_ts = 0.0
        self._settings_page.set_method_enabled(True)
        self.themes.clear_power_on()
        self.service.stop_device_monitor()
        self._sync_install_nav_entry()
        self._nav_to_page(_PAGE_SELECT)
        self._refit_catalogue_after_install()
        QTimer.singleShot(0, self._maybe_offer_deferred_app_update)

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

    def _on_settings_nav_clicked(self):
        """Clicking the sidebar Settings button while it is temporarily renamed
        to 'Linux Setup' keeps the user on the setup screen."""
        if getattr(self, "_linux_setup_flow_active", False):
            self._highlight_nav(_PAGE_SETTINGS, False)
            return
        self._show_full_settings()

    def _show_full_settings(self):
        """Open the complete Settings column and highlight its navigation row."""
        if self._install_run_active():
            # Settings stays closed while an install, device prep or themes
            # stage owns the window; the install card is the only screen.
            self._nav_to_page(_PAGE_FLASH)
            return
        page = getattr(self, "_settings_page", None)
        self._linux_setup_flow_active = False
        # Opening Settings by hand is the reader leaving any focused card: a
        # stale removal journey must not pull them back to Select later.
        self._legacy_focus_pending = False
        self._sync_settings_nav_label()
        if page is not None and page.focused_card() is not None:
            page.clear_card_focus()
        self._nav_to_page(_PAGE_SETTINGS)
        if hasattr(self, "_linux_setup_btn"):
            self._linux_setup_btn.setVisible(False)
            self._linux_setup_btn.setEnabled(False)
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
        self._sync_install_nav_entry()
        page.show_card_alone(page._checker_card)
        self._sync_sp_gui_button_visibility()
        if not page.start_sp_flash_prep():
            self._linux_setup_flow_active = False
            self._sync_settings_nav_label()
            self._sync_install_nav_entry()
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
        self.settings.setValue("linux_first_run_completed", True)
        self.settings.setValue("linux_setup_completed", True)
        setup_btn = getattr(self, "_linux_setup_btn", None)
        if setup_btn is not None:
            setup_btn.setVisible(False)
            setup_btn.setEnabled(False)
        if self._settings_page.focused_card() is not None:
            self._settings_page.clear_card_focus()
        self._sync_install_nav_entry()
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
                on_screen = page is not None and not page.isHidden()
                target = page.explicit_firmware() if on_screen else None
                target = target or (page.focused_firmware() if on_screen else None)
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
        self._install_failed = False
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
            # Snapshot the user's folders before this run writes a single file;
            # cleanup later removes exactly what appeared after this point.
            from .. import portable_run

            self._portable_session = portable_run.PortableSession()
            self._bundled_firmware_run = True
            self._bundled_fw_package = pkg
            if hasattr(self, "_settings_page"):
                self._settings_page.set_portable_run(True)
            # 1. Parse scatter, stage firmware files into working directory
            staged_scatter = stage_bundled_firmware(pkg)

            # 2. Configure package details in Select page and Main window
            package_path = str(staged_scatter)
            package_name = package_install_heading(pkg)
            package_model = pkg.model or ""

            if hasattr(self, "_select_page") and self._select_page is not None:
                self._select_page._fw_package = pkg
                self._select_page._current_installed_release_info = None
                self._select_page._current_package_path = package_path
                self._select_page._current_package_name = package_name
                self._select_page._current_package_model = package_model
                if pkg.type:
                    self._select_page._selected_type = pkg.type
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

            self._bundled_rockbox = bool(pkg.has_rockbox)
            if self._bundled_rockbox:
                self._is_360p_rockbox = True

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
            snap = process_guard.process_snapshot()
            current_pids = frozenset(
                pid for pid, _ppid, cmd in snap
                if process_guard._tool_invocation(cmd) == "flash_tool"
            )
            ignore_pids = getattr(self, "_tool_pids_before_launch", frozenset())
            new_pids = current_pids - ignore_pids
            if new_pids:
                timer.stop()
                for pid in new_pids:
                    process_guard.register_handoff_pid(pid)
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

    def _open_credits(self):
        from ..browser import open_browser
        open_browser("https://innioasis.app/credits.html?thank-you=1")

    def _on_language_changed(self, index):
        lang = self._lang_combo.itemData(index)
        runtime = getattr(self, "_runtime_i18n", None)
        if runtime is not None:
            # Restore anything the fallback had changed before the normal
            # retranslation writes the new language over these widgets.
            runtime.set_language(lang)
        translator().set_language(lang)
        self.settings.setValue("language", lang)
        # Existing app shortcuts follow the interface language. Missing ones
        # stay missing; renamed ones keep the name the person gave them.
        try:
            from ..shortcut_manager import reconcile_shortcut_names

            reconcile_shortcut_names()
        except Exception:
            logger.debug(
                "Could not update shortcut names after language change", exc_info=True
            )
        # A right-to-left language mirrors the interface. This runs before the
        # retranslation so the re-laid-out copy is measured in the direction it
        # is finally drawn in.
        apply_interface_direction(QApplication.instance())
        self._align_page_title()
        self._retranslate_all()
        self._sync_notes_language()
        try:
            from ..notice_service import NoticeService

            # The feed keeps the published wording in the cache; re-render it
            # in the new language without restarting the app.
            NoticeService.instance().reload_translations()
        except Exception:
            logger.debug("Could not reload notice translations", exc_info=True)
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
        notices_page = getattr(self, "_notices_page", None)
        if notices_page is not None and hasattr(notices_page, "retranslate"):
            notices_page.retranslate()
        if self._diagnostics_page is not None and hasattr(self._diagnostics_page, "retranslate"):
            self._diagnostics_page.retranslate()
        if self.statusBar() and hasattr(self.statusBar(), "retranslate"):
            self.statusBar().retranslate()
        from ..notice_service import NoticeService
        NoticeService.instance().reload_translations()
        self._apply_generic_mtk_branding()
        # Every row has its new language's text now: the rail takes the width
        # that copy needs instead of eliding it
        self._reapply_rail_width()
        runtime = getattr(self, "_runtime_i18n", None)
        if runtime is not None:
            QTimer.singleShot(0, runtime.scan)

    def _on_manifest_loaded(self, entries):
        if entries:
            # The live catalogue defines which models are offered; re-filter the
            # drop-down before repopulating the software list for the current
            # model.
            self._select_page.refresh_models()
            self._select_page._on_manifest_refreshed(entries)
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
        self._sync_tray_update_state(waiting)
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
            from ..docs_source import release_notes_url

            release_url = (
                release_notes_url(
                    UPDATE_REPO,
                    getattr(info, "tag", "") or getattr(info, "version", ""),
                    name=getattr(info, "name", "") or "",
                )
                or getattr(info, "html_url", "")
                or f"https://github.com/{UPDATE_REPO}/releases"
            )
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
            if self._window_hidden_in_tray():
                self._notify_app_update(info)
            else:
                self._offer_app_update(info)
                # The system notification is the second channel for a waiting
                # build, not a replacement for the update view. It is skipped
                # when the user turned app-update notifications off.
                self._notify_app_update(info)
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
        # An install, a completion screen and a donation ask all own the
        # window. The update waits for that run to finish instead of replacing
        # it; the rail row still names the waiting build in the meantime.
        if self._install_run_active() or getattr(self, "_install_complete", False):
            if self._install_run_active():
                # A toast does not replace the run or the page the user is
                # looking at; the full update view waits for the run to end.
                self._select_page.show_app_update(info)
            self._deferred_app_update = info
            return
        dlg = UpdateAvailableDialog(
            self,
            info=info,
            current_version=APP_VERSION,
            on_skip=lambda v: self.settings.setValue("update_skipped_version", v),
        )
        self._present_inline(dlg, tr("nav_check_updates"))

    def _maybe_offer_deferred_app_update(self) -> None:
        info = getattr(self, "_deferred_app_update", None)
        if info is None:
            return
        if self._install_run_active() or getattr(self, "_install_complete", False):
            return
        self._deferred_app_update = None
        if info is getattr(self, "_pending_app_update", None):
            self._offer_app_update(info)

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
            self._notify_device_updates(updates)
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

    def _window_hidden_in_tray(self) -> bool:
        tray = getattr(self, "_background_tray", None)
        return tray is not None and tray.is_visible() and not self.isVisible()

    def _notification_tray(self):
        """The background tray, when one is up, so a click can be routed."""
        tray = getattr(self, "_background_tray", None)
        if tray is not None and tray.is_visible():
            return tray
        return None

    def _notify_app_update(self, info, force: bool = False) -> None:
        """Announce a waiting app build natively; clicking opens its update view."""
        import re

        from .. import notice_alerts

        if not force and not notice_alerts.app_updates_enabled():
            return
        version = str(getattr(info, "version", "") or "")
        if not version:
            return
        if not force:
            if getattr(self, "_notified_app_update_version", "") == version:
                return
            # Persisted too, so a restart does not announce the same build again.
            from PySide6.QtCore import QSettings

            settings = QSettings("innioasis", "updater")
            if settings.value("notified_app_update_version", "", type=str) == version:
                self._notified_app_update_version = version
                return
        body = tr("update_available_fmt").format(new=version, current=APP_VERSION)
        body = re.sub(r"<[^>]+>", "", body)
        self._notified_app_update_version = version
        if not force:
            from PySide6.QtCore import QSettings

            settings = QSettings("innioasis", "updater")
            settings.setValue("notified_app_update_version", version)
        notice_alerts.deliver(
            tr("update_available"),
            body,
            tray=self._notification_tray(),
            route="app_update",
        )

    def _notify_device_updates(self, updates) -> None:
        """Post one system notification per device with a newer release.

        No window is passed, so the app is not brought to the front.
        """
        from .. import notice_alerts

        if not notice_alerts.player_updates_enabled():
            return
        from .. import player_update_check

        title = tr("reminder_new_release_title")
        for upd in updates or []:
            software = str((upd or {}).get("software_name") or "")
            model = str((upd or {}).get("model") or "")
            latest_tag = str((upd or {}).get("latest_tag") or "")
            if not software or not model:
                continue
            # Each release is announced once: remember the tag so the next
            # periodic check does not repeat the same system notification.
            if latest_tag and player_update_check.get_last_notified_tag(model) == latest_tag:
                continue
            if latest_tag:
                player_update_check.set_last_notified_tag(model, latest_tag)
            import json

            # The click must land on this release, not just on Select Software.
            route = "player_update:" + json.dumps(
                {"model": model, "software": software, "tag": latest_tag},
                separators=(",", ":"),
            )
            notice_alerts.deliver(
                title,
                tr("sel_update_available").format(software=software, model=model),
                tray=self._notification_tray(),
                route=route,
            )

    def _on_notices_ready(self, notices) -> None:
        """Follow the live notice list: rail row, notification, and routing."""
        self._notices_available = bool(notices)
        logger.info(
            "Notices: %d available (%s)",
            len(notices or []),
            "; ".join(str(getattr(notice, "title", "") or "") for notice in notices or []),
        )
        self._apply_notices_nav_visibility()
        self._notify_about_notices(notices)
        if (
            not self._notices_available
            and hasattr(self, "_stack")
            and self._stack.currentIndex() == _PAGE_NOTICES
        ):
            # Nothing is left to read on the page the user is looking at.
            self._nav_to_page(_PAGE_SELECT)

    def _apply_notices_nav_visibility(self) -> None:
        """Show the Notices row only when a notice survives dismissal."""
        btn = getattr(self, "_notices_btn", None)
        if btn is None:
            return
        visible = bool(
            getattr(self, "_notices_available", False)
            and not self._install_run_active()
            and not getattr(self, "_linux_setup_flow_active", False)
        )
        btn.setVisible(visible)
        btn.setEnabled(visible)

    def _notify_about_notices(self, notices) -> None:
        """Announce the new notices as one counted notification.

        One banner for the batch, never one per notice: a feed that publishes
        three items must not raise three alerts. Notices are marked seen whether
        or not a notification was sent, so turning a category on later never
        replays old announcements.
        """
        from .. import notice_alerts

        fresh = notice_alerts.unseen_notices(notices)
        if not fresh:
            return
        logger.info("Notices: %d new since the last run", len(fresh))
        notice_alerts.mark_notices_seen(n.id for n in fresh)
        notice_alerts.notify_new_notices(
            fresh, tray=self._notification_tray(), route="notices"
        )

    def _bring_notification_window_forward(self) -> None:
        """Restore the window like a tray click before opening a notification's page."""
        if self._window_hidden_in_tray() or not self.isVisible():
            self._show_from_tray()
            return
        from ..notifications import bring_window_to_front

        bring_window_to_front(self)

    def _on_notification_clicked(self, route=None) -> None:
        """Route a clicked system notification to the view it announces."""
        if route is None:
            app = QApplication.instance()
            route = getattr(app, "_last_notification_route", "")
        route = str(route or "")
        kind, _, target = route.partition(":")
        if kind == "app_update":
            self._bring_notification_window_forward()
            self._on_check_updates_clicked()
        elif kind == "player_update":
            self._bring_notification_window_forward()
            self._nav_to_page(_PAGE_SELECT)
            self._focus_notified_release(target)
        elif kind == "install":
            self._bring_notification_window_forward()
            self._nav_to_page(_PAGE_FLASH)
        elif kind == "notices":
            self._bring_notification_window_forward()
            self._nav_to_page(_PAGE_NOTICES)
            self._open_notified_notice(target)

    def _focus_notified_release(self, target: str) -> None:
        """Select the model, software and tag named by a notification."""
        if not target:
            return
        stack = getattr(self, "_stack", None)
        if stack is not None and stack.currentIndex() != _PAGE_SELECT:
            # The install run owns the window; do not change the hidden page.
            return
        import json

        try:
            payload = json.loads(target)
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        model = str(payload.get("model") or "").strip()
        software = str(payload.get("software") or "").strip()
        tag = str(payload.get("tag") or "").strip()
        page = getattr(self, "_select_page", None)
        if page is None or not model or not software:
            return
        if hasattr(page, "navigate_to_package"):
            page.navigate_to_package(
                model, software, tag_name=tag or None, refresh_if_missing=True
            )

    def _open_notified_notice(self, target: str) -> None:
        """Show the notice named by a notification, falling back to the list."""
        stack = getattr(self, "_stack", None)
        if stack is not None and stack.currentIndex() != _PAGE_NOTICES:
            return
        page = getattr(self, "_notices_page", None)
        if page is None:
            return
        if hasattr(page, "refresh_from_service"):
            try:
                page.refresh_from_service()
            except Exception:
                pass
        if target and hasattr(page, "open_notice_by_id"):
            try:
                if page.open_notice_by_id(target):
                    return
            except Exception:
                pass
        if hasattr(page, "show_list"):
            page.show_list()

    def _check_legacy_installations(self):
        try:
            if paths.IS_WINDOWS or is_mediatek_installer():
                # The removal offer is a macOS/Linux Settings card. On these
                # hosts it can never be shown, so launch neither walks the old
                # folders nor risks focusing a card that is not there.
                return
            if sys.platform == "darwin":
                # macOS, first launch: the pre-3.0 app's footprint is the
                # Application Support folder marked by firmware_downloader.py.
                # Clear it silently and be done — no removal offer is ever put
                # on screen. The .app itself is never touched (the current
                # macOS product carries the old app's name), and without the
                # marker the folder is the app's own and stays as it is.
                if getattr(self, "_legacy_migration_checked", False):
                    return
                self._legacy_migration_checked = True
                from ..legacy_cleanup import clear_legacy_support_now

                cleared, note = clear_legacy_support_now()
                if cleared:
                    logger.info("Legacy application support cleanup: %s", note)
                self.settings.setValue("legacy_cleanup_prompted", True)
                return
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
                # own surface. Nothing is popped out over the window. A host
                # whose Settings column does not carry the card is left alone,
                # and is not recorded as having been asked.
                if self._show_legacy_removal_card():
                    self.settings.setValue("legacy_cleanup_prompted", True)
        except Exception:
            logger.debug("Legacy installation migration/check failed", exc_info=True)

    def _show_legacy_removal_card(self) -> bool:
        """Offer the removal on its own card, in the window, not as a modal.

        The Settings card is the offer's one home. Showing it alone keeps the
        question on the app's own surface, and the window is then sized to fit
        that single card. Returns False when this host's Settings column does
        not offer the card, so the launch check cannot navigate into a focused
        card that has no journey behind it.
        """
        page = getattr(self, "_settings_page", None)
        card = getattr(page, "_legacy_cleanup_card", None) if page is not None else None
        if card is None:
            return False
        available = getattr(page, "legacy_offer_available", None)
        if not callable(available) or not available():
            return False
        self._nav_to_page(_PAGE_SETTINGS)
        if not page.show_card_alone(card):
            return False
        page._reveal_legacy_card()
        self._legacy_focus_pending = True
        self._adjust_window_geometry()
        return True

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
            page = getattr(self, "_support_page", None)
            # The shipped Support Us page is static; a future page that renders
            # the donation total opts in by providing the setter.
            setter = getattr(page, "set_donations", None)
            if callable(setter):
                setter(donations)

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
        # SupportPage takes a parent only. The in-window page is built the same
        # way; a page that can show the running total opts in with set_donations.
        page = SupportPage(dlg)
        donations = getattr(self, "_donations", None)
        setter = getattr(page, "set_donations", None)
        if donations is not None and callable(setter):
            setter(donations)
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

    def cleanup_workers(self, sweep: bool = True, wait: bool = True):
        """Cleanly terminate background workers.

        ``wait=False`` is for a close/quit: blocking the GUI thread on a worker
        blocked in a socket read is what made the window beachball before it
        could hide or quit. Retired workers stay referenced and ``_leave_cleanly``
        ends the process if any is still running.
        """
        if getattr(self, "_gui_quit_started", False):
            sweep = False
        self.service.cleanup(sweep=sweep)
        select_page = getattr(self, "_select_page", None)
        if select_page is not None:
            rw = getattr(select_page, "_releases_worker", None)
            if rw is not None and rw.isRunning():
                rw.requestInterruption()
                if wait:
                    rw.wait(1500)
                self._retire_worker(rw)
            dw = getattr(select_page, "_download_worker", None)
            if dw is not None and dw.isRunning():
                dw.cancel()
                if wait:
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
            getattr(self, "_release_refresh_worker", None),
            getattr(select_page, "_manifest_worker", None) if select_page else None,
            getattr(select_page, "_prep_worker", None) if select_page else None,
            getattr(select_page, "_translate_worker", None) if select_page else None,
        ):
            if w is not None and w.isRunning():
                w.requestInterruption()
                if wait:
                    w.wait(1500)
                self._retire_worker(w)

    def close(self):
        return super().close()

    def _schedule_app_quit(self) -> None:
        """Leave after the current close handler has unwound.

        The single-instance lock marks a real app session. A window created by
        a test or an embedding host has no lock, and must not quit that host's
        event loop or destroy its threads on the way out.
        """
        app = QApplication.instance()
        if app is None or getattr(app, "_instance_lock", None) is None:
            return
        app.setQuitOnLastWindowClosed(True)
        QTimer.singleShot(0, app.quit)

    def closeEvent(self, event):
        app = QApplication.instance()
        if app is not None and getattr(app, "_instance_lock", None) is not None:
            # Do not let Qt quit in the middle of this handler. The close path
            # retires workers, and quitting there destroyed the process while
            # they were still winding up (SIGABRT on exit). A window built by a
            # test or embedding host has no lock and keeps the host's setting.
            app.setQuitOnLastWindowClosed(False)
        flashing_in_flight = (
            getattr(self, "_download_active", False)
            or getattr(self, "_prep_active", False)
            or (hasattr(self, "sm") and self.sm.state in (FlashState.S4_FLASHING, FlashState.S3_DEVICE_DETECTED))
            or self.themes.active
        ) and not (
            getattr(self, "_install_failed", False)
            or getattr(self, "_install_complete", False)
            or getattr(self, "_power_off_prompt", False)
            or (hasattr(self, "sm") and self.sm.state in (FlashState.S5_FAILED, FlashState.S5_USB_DISCONNECTED, FlashState.S5_COMPLETE))
        )
        if flashing_in_flight and not getattr(self, "_force_quit", False):
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
        elif hasattr(self, "service") and self.service:
            try:
                self.service.cancel_flash()
            except Exception:
                pass

        # A portable run owns files in the user's folders: closing finishes as
        # a visible cleanup pass, never as a hidden tray session.
        if self._portable_cleanup_required() and not getattr(self, "_force_quit", False):
            event.ignore()
            self._start_portable_cleanup()
            return

        # Opt-in: with "Run in background" on, hide at once. Waiting for
        # worker/process cleanup here made a menu-bar close look like a hang,
        # and the hidden app does not need the window to finish that work.
        from ..tray import run_in_background_enabled

        if (
            run_in_background_enabled(self.settings)
            and not getattr(self, "_force_quit", False)
            and not getattr(self, "_tray_quit_pending", False)
        ):
            self._hide_to_tray(event)
            return

        # Closing the window ends any install window: a backend left behind
        # would hold the USB port after the app is gone.
        from .. import process_guard

        if getattr(self, "_gui_quit_started", False):
            process_guard.end_install_session(reason="app closed after gui handoff", sweep=False)
        else:
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
                service.stop(wait=False)
            except Exception:
                pass
        self.cleanup_workers(sweep=False, wait=False)

        self._closing_accepted = True
        super().closeEvent(event)
        self._schedule_app_quit()

    def _hide_to_tray(self, event) -> None:
        """Close the window but keep the process and its tray icon alive."""
        from ..tray import hold_off_automatic_termination, set_menu_bar_only

        self.hide()
        event.ignore()
        tray = self._ensure_background_tray()
        tray.show()
        if not tray.is_available():
            # No menu bar/tray on this host: an invisible process with no
            # window and no icon is unreachable, so leave cleanly instead.
            self._schedule_app_quit()
            return
        # AppKit reaps an idle, windowless app within seconds and takes the
        # menu bar item with it; the hold stays until the window comes back.
        hold_off_automatic_termination(True)
        # macOS: an accessory app is the one that lives in the menu bar alone,
        # with no Dock icon for a window that is not there.
        set_menu_bar_only(True)
        self._start_background_update_timer()
        self._set_window_polling(False)
        self._arm_idle_shed()

    def _set_window_polling(self, running: bool) -> None:
        """Pause the window-only timers while it is hidden in the tray.

        Nothing on screen reads the chrome watchdog or the SP history, so a
        hidden window stops them. The online refresh keeps running: it is what
        fetches new notices, and a notice that arrives while the app is in the
        tray still has to reach the system notification channel.
        """
        for name in ("_chrome_watchdog", "_sp_history_timer"):
            timer = getattr(self, name, None)
            if timer is None:
                continue
            if running:
                timer.start()
            else:
                timer.stop()

    def _arm_idle_shed(self) -> None:
        """Schedule the window's release after a long, idle stay in the tray."""
        if getattr(self, "_background_agent", None) is None:
            return
        timer = self._idle_shed_timer
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._shed_to_background)
            self._idle_shed_timer = timer
        timer.start(IDLE_TRAY_SHED_MS)

    def _cancel_idle_shed(self) -> None:
        timer = getattr(self, "_idle_shed_timer", None)
        if timer is not None:
            timer.stop()

    def _shed_to_background(self) -> None:
        """Give the widget tree back while the app lives in the tray.

        "Hidden" is not "released": a hidden window still owns every page, its
        styles, its caches and its Python state. After the idle window this
        hands the lot back, so the process returns to the size of a --tray
        launch. The next open rebuilds it; nothing is lost in between, because
        a running install (or any prompt, dialog or worker) refuses the shed.
        """
        agent = getattr(self, "_background_agent", None)
        if agent is None or not self._window_hidden_in_tray():
            return
        if not self._shed_allowed():
            # Something still owns the window: try again after another full
            # idle period rather than on every timer tick.
            self._arm_idle_shed()
            return
        logger.info(
            "Releasing the window after %s s in the tray", IDLE_TRAY_SHED_SECONDS
        )
        self._release_for_shed(agent)

    def _shed_allowed(self) -> bool:
        """True only when nothing is using the window and no dialog is up."""
        if not self._window_hidden_in_tray():
            return False
        if self._install_run_active() or getattr(self, "_download_active", False):
            return False
        if getattr(self, "_prep_active", False) or getattr(self, "_drop_check_active", False):
            return False
        if getattr(self, "_gui_handoff_prepare", False) or getattr(
            self, "_gui_launch_in_flight", False
        ):
            return False
        if getattr(self, "_closing_accepted", False) or getattr(
            self, "_tray_quit_pending", False
        ):
            return False
        if self.themes.active or self.themes.transfer_running:
            return False
        if getattr(self, "_portable_session", None) is not None and not getattr(
            self, "_portable_cleanup_done", False
        ):
            return False
        for worker in list(getattr(self, "_active_workers", ())):
            if self._worker_running(worker):
                return False
        app = QApplication.instance()
        if app is not None:
            if app.activeModalWidget() is not None:
                return False
            from PySide6.QtWidgets import QDialog

            if any(
                isinstance(w, QDialog) and w.isVisible()
                for w in app.topLevelWidgets()
            ):
                return False
        return True

    def _release_for_shed(self, agent) -> None:
        """Drop every global reference to this window, then delete it.

        A signal connection, a pending timer or the app's notification handler
        is enough to keep the Python object -- and with it every page's state
        -- alive after the C++ widget tree is gone. Each one is released here
        before deleteLater(), and the allocator is then asked to return the
        pages to the OS.
        """
        import gc

        from PySide6.QtCore import QCoreApplication, QEvent

        from ..memory_relief import release_memory_to_os
        from ..notice_service import NoticeService

        app = QApplication.instance()
        self._cancel_idle_shed()
        timer = getattr(self, "_background_update_timer", None)
        if timer is not None:
            timer.stop()
        self._set_window_polling(False)
        try:
            NoticeService.instance().notices_ready.disconnect(self._notices_slot)
        except Exception:
            logger.debug("Notice listener was already disconnected", exc_info=True)
        page = getattr(self, "_select_page", None)
        monitor = getattr(page, "_monitor", None)
        if monitor is not None and page is not None:
            try:
                monitor.connectivity_changed.disconnect(page._on_connectivity_changed)
            except Exception:
                pass
            try:
                monitor.stop_monitoring()
            except Exception:
                pass
        runtime = getattr(self, "_runtime_i18n", None)
        if runtime is not None:
            try:
                runtime.stop()
            except Exception:
                pass
            try:
                app.aboutToQuit.disconnect(runtime.stop)
            except Exception:
                pass
            self._runtime_i18n = None
        watcher = getattr(self, "_theme_watcher", None)
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass
            if app is not None and getattr(app, "_theme_watcher", None) is watcher:
                app._theme_watcher = None
        self._theme_watcher = None
        self._background_tray = None
        self._log_lines.clear()
        self._run_output.clear()
        try:
            self.cleanup_workers(sweep=False, wait=False)
        except Exception:
            logger.debug("Workers could not be cleaned before the shed", exc_info=True)
        for child in self.findChildren(QTimer):
            try:
                child.stop()
            except Exception:
                pass
        service = getattr(self, "_theme_sync_service", None)
        if service is not None:
            try:
                service.stop(wait=False)
            except Exception:
                pass
        # Hand the background duties back before the window stops existing.
        agent.release_window(self)
        self.hide()
        self.setParent(None)
        self.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        release_memory_to_os()

    def _hide_to_tray_at_launch(self) -> None:
        """Start-at-login: the tray is up and the window is never shown."""
        from ..tray import hold_off_automatic_termination, set_menu_bar_only

        self._ensure_background_tray().show()
        hold_off_automatic_termination(True)
        set_menu_bar_only(True)
        self._start_background_update_timer()

    def _start_background_update_timer(self) -> None:
        """While the tray keeps the app alive, re-check for updates every few hours."""
        if getattr(self, "_background_update_timer", None) is not None:
            return
        timer = QTimer(self)
        timer.setInterval(5 * 60 * 1000)
        timer.timeout.connect(self._background_update_tick)
        timer.start()
        self._background_update_timer = timer

    def _background_update_tick(self) -> None:
        self._update_manual_pending = False
        self._run_update_check()
        self._check_device_firmware_updates(manual=False)
        # Notices have their own passive timer, but a copy that just came back
        # from sleep also re-reads the feed here, so the next announcement is
        # not left waiting for the next rotation.
        self._refresh_online_data()

    def _sync_tray_update_state(self, available: bool) -> None:
        """Point the menu bar item at a waiting build, when the host has one."""
        tray = getattr(self, "_background_tray", None)
        setter = getattr(tray, "set_update_available", None)
        if callable(setter):
            try:
                setter(bool(available))
            except Exception:
                logger.debug("Could not update the menu bar item", exc_info=True)

    def _on_tray_check_updates(self) -> None:
        """The menu bar's update action: show the window before checking."""
        self._show_from_tray()
        self._on_check_updates_clicked()

    def _ensure_background_tray(self):
        agent = getattr(self, "_background_agent", None)
        if agent is not None:
            # The agent owns the one tray item: handing it around instead of
            # creating a second one also keeps it up while no window exists.
            tray = agent.ensure_tray()
            self._background_tray = tray
            return tray
        tray = getattr(self, "_background_tray", None)
        if tray is None:
            from PySide6.QtGui import QIcon
            from ..tray import create_background_tray

            fallback = QIcon(str(paths.RESOURCES_DIR / "icon.png"))
            tray = create_background_tray(
                QApplication.instance(),
                self._show_from_tray,
                self._quit_from_tray,
                fallback,
                check_updates=self._on_tray_check_updates,
            )
            tray.message_clicked.connect(self._on_notification_clicked)
            self._background_tray = tray
            info = getattr(self, "_pending_app_update", None)
            self._sync_tray_update_state(
                bool(getattr(info, "version", "")) and bool(getattr(info, "tag", ""))
            )
        return tray

    def _show_from_tray(self) -> None:
        from ..notifications import bring_window_to_front
        from ..tray import hold_off_automatic_termination

        self._cancel_idle_shed()
        agent = getattr(self, "_background_agent", None)
        if agent is not None:
            # The window is back: it is the one thing posting checks and
            # notices again, so the agent stops its own.
            agent.adopt_window(self)
        hold_off_automatic_termination(False)
        bring_window_to_front(self)
        self._set_window_polling(True)
        self._apply_host_chrome()

    def _apply_host_chrome(self) -> None:
        """Reapply the native window chrome for a window shown from the tray.

        A window built by the background agent never went through the launch
        path in app.main; every later show repeats these harmlessly.
        """
        try:
            from .dark import is_dark
            from .glass import (
                apply_glass,
                apply_windows_dark_titlebar,
                configure_traffic_lights,
            )

            apply_windows_dark_titlebar(self, is_dark())
            apply_glass(self)
            configure_traffic_lights(self, x_offset=18)
        except Exception:
            logger.debug("Host chrome could not be applied", exc_info=True)

    def _quit_from_tray(self) -> None:
        # Not _force_quit: that skips the install prompt. Quit goes through the
        # same close guard as the window's close button, so an install in
        # progress still asks first and can be declined.
        self._closing_accepted = False
        self._tray_quit_pending = True
        self.close()
        self._tray_quit_pending = False
        if self._closing_accepted:
            tray = getattr(self, "_background_tray", None)
            if tray is not None:
                tray.hide()
            self._schedule_app_quit()


_METHOD_LABELS = {
    "auto": "Auto",
    "sp": "SP Flash Tool",
    "mtk": "MTKClient",
}


def _method_label(method: str) -> str:
    if paths.IS_MAC:
        return "MTKClient"
    return _METHOD_LABELS.get(method, method)
