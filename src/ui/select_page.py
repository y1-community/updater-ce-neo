"""Select package page, online firmware listing + local file picker."""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QScrollArea,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSizePolicy,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .. import catalog, downloads, device_tracking
from ..markdown_render import rewrite_legacy_discord_invite, without_github_only
from ..network import get_connectivity_monitor
from ..worker_lifecycle import retire_worker
from ..flash_service import (
    ExtractWorker,
    ScatterDiscoveryError,
    compute_extract_dir,
    _is_extract_complete,
    _find_scatter,
)
from ..sp_flash_gui import update_sp_history_ini
from ..fw_package import (
    infer_release_name,
    locate_package as locate_fw_package,
    read_package as read_fw_package,
    release_install_heading,
)
from ..paths import INSTALL_DIR
from ..config import (
    DEVICE_MODELS,
    detect_model_and_type_from_name,
    device_label_for_model,
    install_disconnect_guidance,
    is_generic_mtk,
    is_mediatek_installer,
    is_offline_mode,
)
from ..i18n import is_english_language, tr, translator
from ..browser import open_browser
from ..translate import (
    get_google_translate_release_url,
    ReleaseTranslateWorker,
)
from .widgets import (
    AppUpdateToast,
    Banner,
    BatteryToast,
    Card,
    CurrentPageStack,
    InstallProgress,
    NoticeToast,
    UpdateModelToast,
)
from .dark import T, link_html, page_margins
from .scrollbars import configure_scroll_area, make_transparent
from .icons import HELP_ICON_PX, get_help_pixmap, get_symbol_icon, get_symbol_pixmap, help_icon_source, tint_pixmap

logger = logging.getLogger(__name__)

# How long a clicked Type tip stays on screen. Long enough to read the whole
# Type A / Type B explanation without the pointer having to stay put.
TYPE_TIP_VISIBLE_MS = 12000

# Release list sizing. The list hugs its contents so Install / Restore and
# the file link sit directly below the items; past MAX rows it scrolls
# internally instead of growing the window without bound. An empty list
# keeps a small placeholder height instead of collapsing to nothing.
MAX_VISIBLE_RELEASE_ROWS = 4
EMPTY_RELEASE_LIST_ROWS = 3


def _network_polling_available() -> bool:
    """False in offscreen runs (the smoke suite, headless CI).

    Those builds construct this page many times over and must never reach the
    network; on a real desktop the connectivity check is what decides between
    the online listing and the local-file tab.
    """
    return os.environ.get("QT_QPA_PLATFORM") != "offscreen"


class ReleasesWorker(QThread):
    finished = Signal(list, str)

    def __init__(
        self,
        client,
        package,
        model,
        show_nightly=False,
        selected_type=None,
        show_old_rockbox=False,
        prefer_240p=False,
        force_refresh=False,
        parent=None,
        show_original_pre_307=False,
        show_safety_blocked=False,
    ):
        super().__init__(parent)
        self.client = client
        self.package = package
        self.model = model
        self.show_nightly = show_nightly
        self.selected_type = selected_type
        self.show_old_rockbox = show_old_rockbox
        self.prefer_240p = prefer_240p
        self.force_refresh = force_refresh
        self.show_original_pre_307 = show_original_pre_307
        self.show_safety_blocked = show_safety_blocked

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            releases = self.client.releases_for_package(
                self.package,
                self.model,
                show_nightly=self.show_nightly,
                selected_type=self.selected_type,
                show_old_rockbox=self.show_old_rockbox,
                prefer_240p=self.prefer_240p,
                force_refresh=self.force_refresh,
                show_original_pre_307=self.show_original_pre_307,
                show_safety_blocked=self.show_safety_blocked,
            )
            if self.isInterruptionRequested():
                return
            self.finished.emit(releases, "")
        except Exception as e:
            if not self.isInterruptionRequested():
                logger.exception("Releases fetch failed")
                self.finished.emit([], str(e))


def display_software_name(name):
    """User-facing software name with preset i18n replacements.

    Catalogue and tracking names stay canonical English (they are lookup
    keys); only the displayed name is localised. "Original Software" has
    a preset translation per language (``software_original``).
    """
    if (name or "").strip() == "Original Software":
        return tr("software_original")
    return name


class _TabsAdapter:

    """Compatibility adapter presenting QStackedWidget as a QTabWidget interface for tests."""

    def __init__(self, page):
        self._page = page

    def count(self) -> int:
        if is_generic_mtk() or is_offline_mode() or not getattr(self._page, "_online_available", False):
            return 1
        return 2

    def currentWidget(self):
        return self._page._views.currentWidget()

    def widget(self, idx: int):
        if idx == 0 and not (is_generic_mtk() or is_offline_mode()):
            return self._page._online_tab
        return self._page._local_tab

    def tabBar(self):
        adapter = self

        class _TabBar:
            def isVisible(self):
                return not (is_generic_mtk() or is_offline_mode() or not getattr(adapter._page, "_online_available", False))

        return _TabBar()


class _TypeInfoMark(QLabel):
    """Small information mark beside Type. Hover waits; a click shows the tip now."""

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            # One event-loop turn later, on purpose: Qt tears a tooltip down on
            # the very mouse release that asked for it, so showing it inline
            # made the tip vanish again before it could be read.
            QTimer.singleShot(0, self._show_tip_now)
        super().mouseReleaseEvent(event)

    def _show_tip_now(self) -> None:
        tip = self.toolTip()
        if not tip:
            return
        QToolTip.showText(
            self.mapToGlobal(QPoint(0, self.height())),
            tip,
            self,
            self.rect(),
            TYPE_TIP_VISIBLE_MS,
        )


# Release notes show at most this many lines before they scroll inside their
# card. Longer copy must not make the app window grow to fit it.
RELEASE_NOTES_MAX_LINES = 5
# The notes are 13 px copy with a 1.45 line height (see the body CSS).
_RELEASE_NOTES_LINE_PX = 19
_RELEASE_NOTES_MAX_PX = RELEASE_NOTES_MAX_LINES * _RELEASE_NOTES_LINE_PX + 8


class _ReleaseNotesBrowser(QTextBrowser):
    """Release notes view that grows to its content with headroom for translation hints."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QTextOption

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(60)
        self.setMaximumHeight(140)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)

    def sizeHint(self) -> QSize:
        doc = self.document()
        if doc is None:
            return super().sizeHint()
        vp = self.viewport()
        w = vp.width() if vp and vp.width() > 0 else self.width()
        if w > 0:
            doc.setTextWidth(w)
        doc_h = int(doc.documentLayout().documentSize().height())
        target_h = max(40, doc_h + 8)
        target_h = min(target_h, 140)
        from PySide6.QtCore import QSize
        return QSize(super().sizeHint().width(), target_h)

    def minimumSizeHint(self) -> QSize:
        from PySide6.QtCore import QSize
        return QSize(50, 48)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.updateGeometry()


class SelectPackagePage(QWidget):
    package_selected = Signal(str, str, str)
    gui_release_clicked = Signal(object)
    gui_firmware_changed = Signal()
    release_icon_ready = Signal(object)
    gui_handoff_abandoned = Signal()
    download_started = Signal(str, str)
    download_progress = Signal(int, str)
    download_finished = Signal(bool, str)
    download_cancelled = Signal()
    prep_progress = Signal(int)
    prep_started = Signal(str, str)
    # Local archive preparation finished (ok). Start stays on this page, so the
    # main window must hand the screen back to it.
    prep_finished = Signal(bool)
    # (message, timeout_ms) -> shown in the main window's status bar.
    status_message = Signal(str, int)
    # Emitted whenever the release list is (re)filled, so the window can
    # grow or shrink its height to fit the items.
    content_size_changed = Signal()
    # Emitted when user clicks 'Update' on the app update toast
    app_update_clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._gui_previous_identity = None
        self.settings = None
        self.client = catalog.ReleasesClient()
        self._active_workers = set()
        self._releases_worker = None
        self._download_worker = None
        self._current_package_path = ""
        self._current_package_name = ""
        self._fw_package = None
        self._current_package_model = ""
        # MediaTek Installer inputs (Download Agent + scatter are required, the
        # auth file optional). Updater CE leaves the DA empty so the bundled
        # MTK_AllInOne_DA.bin applies.
        self._installer_da = ""
        self._installer_mode = False
        self._online_banner_key = ""
        self._online_banner_count = 0
        self._local_banner_key = ""
        self._local_banner_arg = ""
        self._download_status_key = ""
        self._local_status_key = ""
        self._prep_worker = None
        self._selected_type = None  # None = all types; 'A' or 'B' for filtered
        self._current_selected_rel = None
        self._gui_explicit = None
        # Set when an accepted update notification starts the install. The
        # window consumes it for that run so Keep my settings starts ticked.
        self._keep_settings_default = False
        self._gui_handoff_worker = False
        self._is_translated = False
        self._is_translating = False
        # Sticky choice: once the user translates once, newly focused
        # releases translate automatically. "Show original" opts back out.
        self._auto_translate_notes = False
        self._translated_notes_cache = {}
        self._translate_worker = None
        # One local notice per session when the translation services stop
        # answering, so a failed translate explains itself instead of looking
        # like a dead link.
        self._translation_notice_posted = False
        # Whether the online catalogue is on offer. It starts hidden and is only
        # offered once the catalogue's own firmware listings load: on a network
        # that cannot reach GitHub (some regions, corporate filters) a generic
        # connectivity probe would happily promise an online catalogue that can
        # never list a build, and the local-file screen is the honest offer.
        self._online_available = False
        # True once the reader picked the local-file screen themselves. Their
        # choice outranks the connection: coming back online must not drag them
        # off the file they were about to install.
        self._manual_local = False
        # False until the first probe answers: the first answer is a starting
        # state, not a connection change worth a toast.
        self._connectivity_known = False
        # Set when the first probe finds no archive at all, so the explanation
        # can wait until the page is actually on screen (see showEvent).
        self._pending_archive_notice = False
        self._build_ui()
        self._tabs = _TabsAdapter(self)
        self._update_view_links()
        self._on_model_changed()

        self._monitor = get_connectivity_monitor()
        self._monitor.connectivity_changed.connect(self._on_connectivity_changed)
        if self._connectivity_monitor_applicable():
            self._monitor.start_monitoring()
            if self._monitor.is_online is False:
                self.set_online_mode(False)
            elif self._monitor.is_online is True:
                self.set_online_mode(True)
            # While the first check is in flight nothing is offered yet; the
            # monitor's answer decides (see _on_connectivity_changed).
        elif not is_generic_mtk():
            # Nothing checks listings here (headless runs, and builds that carry
            # no catalogue at all): the catalogue's own state decides instead.
            self.set_online_mode(True)

        if is_generic_mtk():
            self.apply_generic_mode(True)

        self.apply_installer_mode(is_mediatek_installer())
        self._check_and_load_cached_firmware()

    def _check_and_load_cached_firmware(self):
        """Restore last attempted firmware as a GUI choice only when complete."""
        from .. import device_tracking
        from ..sp_flash_gui import cached_install_firmware, check_cached_firmware_readiness

        latest = device_tracking.get_latest_package(getattr(self, "settings", None))
        scatter, extract = cached_install_firmware(latest)
        if scatter and extract and extract.is_dir():
            ready, scatter, extract, _reason = check_cached_firmware_readiness(
                scatter_path=scatter,
                extract_dir=extract,
                model=(latest or {}).get("model") or "",
            )
            if not ready:
                return
            target_path = str(extract)
            self._path_edit.setText(target_path)
            self._current_package_path = target_path
            self._current_package_model = (latest or {}).get("model") or ""
            self._current_package_name = (latest or {}).get("software_name") or extract.name
            self._remember_gui_local(target_path, self._current_package_model, self._current_package_name)
            self._set_local_banner("sel_current_pkg", extract.name)
            self._start_btn.setEnabled(True)
            self._auto_detect_auth_file(target_path)

    def _gui_target_if_ready(self, target: Optional[dict]) -> Optional[dict]:
        """Return a target only when its extracted scatter and images are usable."""
        if not target:
            return None
        from ..sp_flash_gui import check_cached_firmware_readiness

        kind = target.get("kind")
        if kind == "online":
            archive = Path(target.get("dest") or target.get("path") or "")
            if not archive.is_file() or archive.stat().st_size <= 0:
                return None
            from ..flash_service import completed_extract_dir, compute_extract_dir

            extract = completed_extract_dir(archive) or compute_extract_dir(archive)
            ready, scatter, extract_dir, _reason = check_cached_firmware_readiness(
                scatter_path=None,
                extract_dir=extract,
                model=target.get("model") or "",
            )
            if not ready:
                return None
            return {**target, "kind": "cache", "scatter": scatter, "extract": extract_dir, "release": target.get("release")}
        if kind == "local":
            path = Path(target.get("path") or "")
            if not path.exists():
                return None
            from ..flash_service import compute_extract_dir, completed_extract_dir

            if path.is_dir():
                extract = path
            elif path.suffix.lower() in (".zip", ".rar"):
                extract = completed_extract_dir(path) or compute_extract_dir(path)
            else:
                extract = path.parent
            ready, scatter, extract_dir, _reason = check_cached_firmware_readiness(
                scatter_path=None,
                extract_dir=extract,
                model=target.get("model") or "",
            )
            if not ready:
                return None
            return {**target, "kind": "cache", "scatter": scatter, "extract": extract_dir, "release": target.get("release")}
        if kind == "cache":
            ready, scatter, extract_dir, _reason = check_cached_firmware_readiness(
                scatter_path=target.get("scatter"),
                extract_dir=target.get("extract"),
                model=target.get("model") or "",
            )
            if ready:
                return {**target, "scatter": scatter, "extract": extract_dir, "release": target.get("release")}
        return None

    def gui_firmware_target(self) -> Optional[dict]:
        """Selected package to prepare, or retained extract when nothing is selected.

        Only a page on screen can choose a release: off screen the focused or
        explicit catalogue item is ignored so the most recent install is used.
        """
        on_screen = not self.isHidden()
        explicit = self.explicit_firmware() if on_screen else None
        focused = self.focused_firmware() if on_screen else None
        for target in (explicit, focused):
            ready = self._gui_target_if_ready(target)
            if ready:
                return ready
            if target and target.get("kind") == "online":
                # The selected catalogue release can be downloaded and
                # extracted by the handoff worker even before it is cached.
                return target
            if target and target.get("kind") == "local":
                path = Path(target.get("path") or "")
                if path.is_file() and path.suffix.lower() in (".zip", ".rar"):
                    # A partial extract is repaired by extracting its original
                    # archive again; a folder with missing images is not.
                    return target
            if target:
                return None
        # A browsed local path takes precedence over the previous-install
        # cache, even when it is not a valid SP package. Never launch the old
        # firmware just because the new local selection cannot be used.
        if on_screen and self.catalogue_section() == "local" and getattr(self, "_current_package_path", ""):
            return None
        from .. import device_tracking
        from ..sp_flash_gui import cached_install_firmware, check_cached_firmware_readiness

        latest = device_tracking.get_latest_package(getattr(self, "settings", None))
        scatter, extract = cached_install_firmware(latest)
        if not scatter or not extract:
            return None
        ok, scatter, extract, _reason = check_cached_firmware_readiness(
            scatter_path=scatter,
            extract_dir=extract,
            model=(latest or {}).get("model") or "",
        )
        if not ok:
            return None
        return {
            "kind": "cache",
            "scatter": scatter,
            "extract": extract,
            "model": (latest or {}).get("model") or "",
            "name": (latest or {}).get("software_name") or "",
            "release": (latest or {}).get("release"),
        }

    def _emit_gui_firmware_changed(self) -> None:
        old = getattr(self, "_gui_previous_identity", None)
        candidate = self.gui_firmware_target()
        new = self._release_identity(candidate)
        if old != new:
            self._gui_previous_identity = new
            self.gui_firmware_changed.emit()
            self.content_size_changed.emit()
        else:
            self._gui_previous_identity = new

    def _invalidate_online_gui_target(self) -> None:
        if getattr(self, "_gui_explicit", None) and self._gui_explicit.get("kind") != "local":
            self._gui_explicit = None

    def _connectivity_monitor_applicable(self) -> bool:
        """True only when an online catalogue is actually on offer.

        The probe exists to decide between the online and local listings, so
        with the catalogue switched off (offline mode, MediaTek Installer build)
        there is nothing to decide and the periodic HTTPS check would be pure
        cost.
        """
        if is_generic_mtk():
            return False
        return _network_polling_available()

    def apply_generic_mode(self, generic: bool):
        # Network polling follows the catalogue: no catalogue, no probes.
        if not generic and self._connectivity_monitor_applicable():
            if not self._monitor.is_monitoring:
                self._monitor.start_monitoring()
        elif generic and self._monitor.is_monitoring:
            self._monitor.stop_monitoring()

        if generic:
            self._views.setCurrentWidget(self._local_tab)
            self._hint.setText(tr("sel_only_local_generic"))
        else:
            self._show_online_view()
            self._hint.setText(tr("sel_only_local"))
        self._update_view_links()

    def _on_connectivity_changed(self, is_online: bool):
        if is_generic_mtk():
            return
        if is_online:
            # A returning connection is the moment to replace stale snapshots:
            # ttl-gated and off the UI thread, so a failure changes nothing.
            try:
                from .. import online_data

                online_data.refresh_all_async()
            except Exception:
                pass
            # The catalogue is the parsed manifest, not the downloaded file:
            # a returning connection must re-read it, or new models keep
            # waiting for the next launch.
            self._refresh_manifest()
            try:
                from ..notice_service import NoticeService

                NoticeService.instance().fetch_async()
            except Exception:
                pass
        self.set_online_mode(is_online, _from_poll=True)

    def set_online_mode(self, is_online: bool, *, _from_poll: bool = False):
        if is_generic_mtk() and is_online:
            return
        was_available = self._online_available
        self._online_available = bool(is_online)
        self._update_view_links()

        win = self.window()
        is_busy = (
            getattr(self, "_prep_worker", None) is not None
            or getattr(self, "_download_worker", None) is not None
            or (hasattr(win, "_install_run_active") and win._install_run_active())
        )
        # Never move the screen under a running task, under a selection the
        # reader made in the catalogue, or after they deliberately opened the
        # local-file screen. The toast reports the change either way.
        page_visible = self.isVisible() or (win is self)
        may_switch = (
            not is_busy
            and page_visible
            and not self._manual_local
            and (not _from_poll or self._current_selected_rel is None)
        )
        if is_online:
            if may_switch:
                self._show_online_view(refresh=not was_available)
        elif may_switch:
            self._views.setCurrentWidget(self._local_tab)
        show_toast = self._connectivity_known and was_available != is_online
        if not self._connectivity_known and not is_online:
            # The first probe of the session found no archive. That is not a
            # "connection change" but it is exactly when the app would otherwise
            # look broken, so queue the explanation for the first visible paint.
            self._pending_archive_notice = True
        self._connectivity_known = True
        if show_toast and self.isVisible():
            self._show_connection_toast(is_online)
        elif not is_online:
            self._maybe_show_archive_notice()

    def _maybe_show_archive_notice(self) -> None:
        """Explain an unreachable archive once, as soon as the page is on screen."""
        if not getattr(self, "_pending_archive_notice", False):
            return
        if self._online_available or not self.isVisible():
            return
        self._pending_archive_notice = False
        self._show_connection_toast(False)

    def _show_connection_toast(self, is_online: bool) -> None:
        """Explain a connection change beside the catalogue, with one way out."""
        toast = getattr(self, "_conn_toast", None)
        if toast is None:
            return
        if is_online:
            toast.show_message(
                tr("conn_online"),
                action_text=tr("conn_action_online"),
                icon="\U0001F310",
            )
        else:
            # The archive is not answering: GitHub refused the connection and the
            # static fallback feed could not be reached either. Point the user at
            # the crawlable firmware list, which needs no GitHub API, so a package
            # can still be fetched from another device or network.
            toast.show_message(
                tr("conn_archive_unavailable"),
                action_text=tr("conn_action_archive_list"),
                icon="\U0001F4E1",
            )

    def _on_conn_toast_action(self) -> None:
        """Go where the toast offered: refresh the catalogue, or open the static list."""
        if self._online_available:
            self._show_online_view(refresh=True)
            return
        from ..browser import open_browser
        from ..feed_fallback import FIRMWARE_LIST_URL

        if not open_browser(FIRMWARE_LIST_URL):
            self._show_local_view()

    def _build_ui(self):
        t = T()
        # The catalogue scrolls as a page. The release list and the notes each
        # scroll inside their own cards, but a model with a long list, a long
        # release note, or a short display can still add up to more than the
        # window is allowed to show — and the Install / Restore button and the
        # file link were then painted below the window's edge, out of reach.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll_content = QWidget()
        layout = QVBoxLayout(self._scroll_content)
        layout.setContentsMargins(*page_margins())
        layout.setSpacing(8)

        self._title = QLabel(tr("sel_title"))
        self._title.setProperty("cssClass", "pageTitle")
        if sys.platform == "darwin":
            self._title.setStyleSheet(
                "font-size: 13px; font-weight: 600; color: palette(window-text); letter-spacing: normal; background: transparent; border: none;"
            )
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(self._title)
        title_row.addStretch(1)
        # Notices live in the sidebar's own page: a second text link beside the
        # title only repeated the navigation and crowded the header.
        layout.addLayout(title_row)

        # First-run reminder to charge the player/device battery before use
        self._toast_container = QWidget(self)
        # Two stacked cards: the battery reminder and, when the connection
        # changes, a status line that explains it without moving the screen.
        toast_layout = QVBoxLayout(self._toast_container)
        toast_layout.setContentsMargins(4, 0, 4, 0)
        toast_layout.setSpacing(6)
        self._battery_toast = BatteryToast(self._toast_container)
        self._battery_toast.dismissed.connect(self._on_dismiss_battery_toast)
        toast_layout.addWidget(self._battery_toast)
        from .widgets import ConnectivityToast

        self._conn_toast = ConnectivityToast(self._toast_container)
        self._conn_toast.action_clicked.connect(self._on_conn_toast_action)
        toast_layout.addWidget(self._conn_toast)
        layout.addWidget(self._toast_container)
        self._check_battery_toast_visibility()

        # No tabs: the catalogue *is* the Select Software screen, and the
        # local-file picker is reached from the "Install from File" link below
        # the primary action (and left again from its own link). One screen at
        # a time keeps the layout identical between them, leaves every control
        # a native widget, and gives the file flow the full page instead of a
        # tab-sized strip.
        self._views = CurrentPageStack()
        self._online_tab = self._build_online_tab()
        self._local_tab = self._build_local_tab()
        self._views.addWidget(self._online_tab)
        self._views.addWidget(self._local_tab)
        layout.addWidget(self._views, 1)

        self._scroll.setWidget(self._scroll_content)
        # Transparent *after* the content widget is in place: QScrollArea turns
        # that widget's auto-fill back on inside setWidget(), and the solid
        # Base it then painted showed as a band behind the cards.
        #
        # No visible bars either: the window is fitted to the cards (see
        # ``content_width_hint`` and ``content_height_hint``), so a bar would
        # only sit in front of the layout it does not need to reveal. Wheel and
        # trackpad scrolling still work — the scroll area moves its viewport
        # whether or not a bar is drawn — which is what keeps a page taller
        # than a small display reachable.
        configure_scroll_area(
            self._scroll,
            horizontal=Qt.ScrollBarAlwaysOff,
            vertical=Qt.ScrollBarAlwaysOff,
            transparent=True,
        )
        root.addWidget(self._scroll, 1)

    def _link_html(self, key: str, *, bold: bool = True) -> str:
        t = T()
        weight = " font-weight: 700;" if bold else ""
        return (
            f'<a href="#" style="color: {t.fg}; text-decoration: none;'
            f' border: none; background: transparent;{weight}">{tr(key)}</a>'
        )

    def _make_link(self, key: str, on_click, *, bold: bool = True) -> QLabel:
        """A plain text link: hyperlinks carry the pointing cursor, buttons do not.

        Same colour as the surrounding text either way; *bold* marks the link
        that leads somewhere primary. Browsing for a local file is the
        secondary path while the catalogue is on offer (and the MediaTek
        Installer, which is offline-first, does not show it at all), so that
        one stays at the text weight.
        """
        label = QLabel(self._link_html(key, bold=bold))
        label.setAlignment(Qt.AlignCenter)
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.LinksAccessibleByMouse)
        label.setCursor(Qt.PointingHandCursor)
        label.linkActivated.connect(lambda *_: on_click())
        if not hasattr(self, "_registered_links"):
            self._registered_links = []
        self._registered_links.append((label, key, bold))
        return label

    def _show_local_view(self):
        """Show the local-file screen ("Install from a file")."""
        # Reachable only from a link (a connection change switches the stack
        # directly), so this records a deliberate choice.
        self._manual_local = True
        self._current_selected_rel = None
        self._raw_release_pixmap = None
        self._views.setCurrentWidget(self._local_tab)

    def catalogue_section(self) -> str:
        """Which catalogue the current install was started from."""
        views = getattr(self, "_views", None)
        if views is not None and views.currentWidget() is getattr(self, "_local_tab", None):
            return "local"
        return "online"

    def show_catalogue_section(self, section: str) -> None:
        if section == "local":
            self._show_local_view()
        else:
            self._show_online_view()

    def _show_online_view(self, refresh: bool = False):
        """Show the catalogue ("Install from the catalogue")."""
        if is_generic_mtk():
            return
        self._manual_local = False
        self._views.setCurrentWidget(self._online_tab)
        if (refresh or not self._release_list.count()) and not (
            self._releases_worker is not None and self._releases_worker.isRunning()
        ):
            self._on_model_changed()

    def _update_view_links(self):
        """Both links are catalogue affordances: no catalogue, no links.

        Offline Mode (and the MediaTek Installer, which is offline-first) has no
        list to choose from and no catalogue to return to, so neither link is
        shown there.
        """
        available = (
            bool(self._online_available)
            and not is_generic_mtk()
            and not is_offline_mode()
        )
        self._install_from_file_link.setVisible(available)
        self._install_online_link.setVisible(available)

    def _say(self, text: str, timeout_ms: int = 0):
        """Show a status message in the main status bar (0 = until replaced)."""
        self.status_message.emit(text, timeout_ms)

    def _install_restore_icon(self):
        """Tint the install glyph like sidebar icons, including full-color theme icons."""
        from PySide6.QtGui import QIcon

        color = T().fg
        pixmap = get_symbol_pixmap("install", 16, color=color)
        return QIcon(tint_pixmap(pixmap, color))

    def _button_icon(self, symbol: str, size: int = 16):
        """A small button glyph in the live text colour.

        The icon provider's default is plain white in dark mode, which sat
        next to the theme's own foreground on every label and button. These
        glyphs follow the interface text colour, like the sidebar rows.
        """
        return get_symbol_icon(symbol, size, color=T().fg)

    def _build_online_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(6)

        # ── Device & Software Filters (Compact 2-Row Responsive Grid) ────
        self._filter_group = Card()
        filter_layout = QGridLayout()
        filter_layout.setContentsMargins(4, 2, 4, 2)
        filter_layout.setHorizontalSpacing(8)
        filter_layout.setVerticalSpacing(6)

        # Row 0: Brand. Only shown when slidia_manifest.xml tags releases with
        # a brand attribute; it scopes the release listing and nothing else.
        self._brand_label = QLabel(f"{tr('sel_brand')}:")
        self._brand_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._brand_label, 0, 0)

        self._brand_combo = QComboBox()
        self._brand_combo.setObjectName("softwareBrandFilter")
        self._brand_combo.setMinimumWidth(80)
        self._brand_combo.currentIndexChanged.connect(self._on_brand_changed)
        filter_layout.addWidget(self._brand_combo, 0, 1)

        # Row 1: Model and Type. The help mark sits in the type cell, beside the combo.
        self._model_label = QLabel(f"{tr('sel_model')}:")
        self._model_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._model_label, 1, 0)

        self._model_combo = QComboBox()
        self._model_combo.setObjectName("softwareModelFilter")
        self._model_combo.setMinimumWidth(80)
        self.refresh_models()
        self._model_combo.currentTextChanged.connect(self._on_model_changed)
        filter_layout.addWidget(self._model_combo, 1, 1)

        self._type_label = QLabel(f"{tr('sel_type')}:")
        self._type_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._type_label, 1, 2)

        self._type_combo = QComboBox()
        self._type_combo.setObjectName("softwareTypeFilter")
        self._type_combo.addItem(tr("sel_type_a"), "A")
        self._type_combo.addItem(tr("sel_type_b"), "B")
        self._type_combo.currentIndexChanged.connect(self._on_type_changed)

        self._type_field = QWidget()
        type_row = QHBoxLayout(self._type_field)
        type_row.setContentsMargins(0, 0, 0, 0)
        type_row.setSpacing(4)
        type_row.addWidget(self._type_combo, 1)
        self._type_help_icon = _TypeInfoMark(self._type_field)
        self._type_help_icon.setObjectName("typeHelpIcon")
        self._type_help_icon.setCursor(Qt.ArrowCursor)
        self._type_help_icon.setFocusPolicy(Qt.NoFocus)
        self._type_help_icon.setAlignment(Qt.AlignCenter)
        self._type_help_icon.setFixedSize(HELP_ICON_PX, HELP_ICON_PX)
        self._type_help_icon.setContentsMargins(0, 0, 0, 0)
        type_row.addWidget(self._type_help_icon, 0, Qt.AlignVCenter)
        self._apply_type_help_hint()
        filter_layout.addWidget(self._type_field, 1, 3)

        # Row 2: Software & Refresh
        self._software_label = QLabel(f"{tr('sel_software')}:")
        self._software_label.setProperty("cssClass", "field-label")
        filter_layout.addWidget(self._software_label, 2, 0)

        self._software_combo = QComboBox()
        self._software_combo.setObjectName("softwareReleaseFilter")
        self._software_combo.setMinimumWidth(120)
        self._software_combo.currentTextChanged.connect(self._on_software_changed)
        filter_layout.addWidget(self._software_combo, 2, 1, 1, 2)

        self._refresh_btn = QPushButton(tr("sel_refresh"))
        self._refresh_btn.setToolTip(tr("sel_refresh_tooltip"))
        self._refresh_btn.clicked.connect(lambda: self._refresh_releases(force_refresh=True))
        filter_layout.addWidget(self._refresh_btn, 2, 3)

        # Why Keep Data will not be offered, said next to the software name it
        # applies to: the install card otherwise only asks once the device is
        # connected, and the answer is already known here.
        self._keep_data_note = QLabel(tr("sel_keep_data_incompatible"))
        self._keep_data_note.setWordWrap(True)
        self._keep_data_note.setProperty("cssClass", "hint")
        self._keep_data_note.setVisible(False)
        filter_layout.addWidget(self._keep_data_note, 3, 0, 1, 4)

        filter_layout.setColumnStretch(1, 1)
        filter_layout.setColumnStretch(3, 1)
        self._filter_group.set_layout(filter_layout)

        layout.addWidget(self._filter_group)

        # Rockbox listing filters (old builds / nightly / 240p) live on the
        # Settings screen; they are applied here only for Y1 Rockbox browsing.
        self._online_banner = Banner()
        self._online_banner.setVisible(False)
        layout.addWidget(self._online_banner)
        self._app_update_prompt = AppUpdateToast()
        self._app_update_prompt.action_clicked.connect(self._on_app_update_action)
        self._app_update_prompt.close_clicked.connect(self._on_app_update_dismiss)
        self._app_update_info = None
        layout.addWidget(self._app_update_prompt)

        self._update_offers = []
        self._toast_session_mute = set()

        # One toast per device with a newer release, stacked in this column.
        # The column is hidden while empty so it takes no height.
        self._model_toasts = []
        self._model_toast_column = QVBoxLayout()
        self._model_toast_column.setSpacing(6)
        self._model_toast_column.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self._model_toast_column)

        self._active_notices = []
        self._notice_prompt = NoticeToast()
        self._notice_prompt.list_requested.connect(self._open_notices_list)
        layout.addWidget(self._notice_prompt)

        from ..notice_service import NoticeService
        NoticeService.instance().notice_ready.connect(self._on_notice_ready)
        NoticeService.instance().notices_ready.connect(self._on_notices_ready)
        NoticeService.instance().fetch_async()

        # ── Split Content: Left = Packages + Install; Right = Status + Notes ──
        split = QHBoxLayout()
        split.setSpacing(10)

        # Left panel: Available System Software
        self._pkg_group = Card("sel_available_software")
        # Busy bar on the title row while the manifest refreshes in the background;
        # the cached list stays usable underneath it.
        self._manifest_throbber = QProgressBar()
        self._manifest_throbber.setRange(0, 0)
        self._manifest_throbber.setTextVisible(False)
        self._manifest_throbber.setFixedSize(60, 6)
        self._manifest_throbber.setVisible(False)
        self._pkg_group.put_widget_on_title_row(self._manifest_throbber)
        pkg_layout = QVBoxLayout()
        pkg_layout.setContentsMargins(0, 0, 0, 0)
        pkg_layout.setSpacing(6)

        self._release_list = QListWidget()
        self._release_list.setObjectName("releaseList")
        self._release_list.setAttribute(Qt.WA_TranslucentBackground, False)
        self._release_list.setFrameShape(QFrame.NoFrame)
        self._release_list.viewport().setAutoFillBackground(False)
        configure_scroll_area(
            self._release_list,
            horizontal=Qt.ScrollBarAlwaysOff,
            vertical=Qt.ScrollBarAsNeeded,
            transparent=False,
        )
        self._release_list.setAutoFillBackground(False)
        self._release_list.setAttribute(Qt.WA_NoSystemBackground, False)
        self._release_list.viewport().setAutoFillBackground(False)
        self._release_list.viewport().setAttribute(Qt.WA_TranslucentBackground, False)
        self._release_list.viewport().setAttribute(Qt.WA_NoSystemBackground, False)
        # Use the desktop style for the list and its scrollbars. Local QSS on a
        # QAbstractScrollArea replaces the Plasma overlay scrollbar with Qt's
        # classic arrow bar.
        self._release_list.setStyleSheet("")
        self._release_list.viewport().setStyleSheet("")
        self._release_list.verticalScrollBar().setStyleSheet("")
        self._release_list.horizontalScrollBar().setStyleSheet("")
        self._release_list.setFrameShape(QFrame.NoFrame)
        self._release_list.setLineWidth(0)
        self._release_list.setMidLineWidth(0)
        self._release_list.setAttribute(Qt.WA_StyledBackground, False)
        self._release_list.viewport().setAttribute(Qt.WA_StyledBackground, False)
        self._release_list.currentItemChanged.connect(self._on_release_selected)
        self._release_list.itemClicked.connect(self._on_release_clicked)
        self._release_list.itemActivated.connect(self._on_install)
        self._release_list.itemDoubleClicked.connect(self._on_release_double_clicked)
        # The list takes the card's spare room first, up to the row cap set by
        # ``_fit_release_list_height``. The plain stretch below still keeps the
        # slack off the link (see above), but with equal shares a card with
        # room for seven rows only ever showed half of them.
        pkg_layout.addWidget(self._release_list, 10)
        self._fit_release_list_height()

        # Slim progress bar, only visible while downloading / preparing.
        self._download_bar = InstallProgress()
        self._download_bar.setTextVisible(True)
        self._download_bar.setFormat("%p%")
        self._download_bar.setMinimumHeight(18)
        from .dark import install_progress_readability
        install_progress_readability(self._download_bar)
        self._download_bar.setVisible(False)
        pkg_layout.addWidget(self._download_bar)

        self._install_btn = QPushButton(tr("sel_install"))
        self._install_btn.setIcon(self._install_restore_icon())
        self._install_btn.setDefault(True)
        self._install_btn.setAutoDefault(True)
        self._install_btn.setMinimumHeight(32)
        self._install_btn.setEnabled(False)
        self._install_btn.setToolTip(tr("sel_install_tooltip_generic"))
        self._install_btn.clicked.connect(self._on_install)
        pkg_layout.addWidget(self._install_btn)

        # The way into the local-file screen. A link, not a button: it is a
        # navigation affordance, and the primary action above stays the only
        # button competing for attention.
        self._install_from_file_link = self._make_link(
            "sel_install_from_file", self._show_local_view, bold=False
        )
        pkg_layout.addWidget(self._install_from_file_link)
        # The card is as tall as the notes column beside it, so the slack has
        # to go *after* the link: a plain label would otherwise grow to take
        # it and centre its text half a card below the button.
        self._install_from_file_link.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        pkg_layout.addStretch(1)

        self._pkg_group.set_layout(pkg_layout)
        # A scroll viewport erases its contents with Base; keep that palette
        # synchronized with the painted card instead of the generic window.
        self._pkg_group.refresh_theme = lambda: clear_glass_plate(self._release_list)
        split.addWidget(self._pkg_group, 5)

        # Right panel: Status and Notes
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._notes_group = Card()
        notes_l = QVBoxLayout()
        notes_l.setContentsMargins(0, 0, 0, 0)
        notes_l.setSpacing(4)

        details_row = QHBoxLayout()
        details_row.setContentsMargins(0, 0, 0, 0)
        details_row.setSpacing(8)
        from .release_icon import FadingIcon

        self._details_icon = FadingIcon(40)
        details_row.addWidget(self._details_icon, 0, Qt.AlignVCenter)
        details_text = QVBoxLayout()
        details_text.setContentsMargins(0, 0, 0, 0)
        details_text.setSpacing(0)
        self._details_name = QLabel("")
        self._details_name.setStyleSheet(
            "font-size: 13px; font-weight: 600; color: palette(window-text); border: none;"
        )
        self._details_version = QLabel("")
        self._details_version.setStyleSheet(
            "font-size: 12px; color: palette(window-text); border: none;"
        )
        details_text.addWidget(self._details_name)
        details_text.addWidget(self._details_version)
        details_row.addLayout(details_text, 1)
        self._details_header = QWidget()
        self._details_header.setLayout(details_row)
        self._details_header.setAutoFillBackground(False)
        self._details_header.setAttribute(Qt.WA_TranslucentBackground, False)
        self._details_header.setAttribute(Qt.WA_NoSystemBackground, True)
        self._details_name.setAutoFillBackground(False)
        self._details_name.setAttribute(Qt.WA_TranslucentBackground, False)
        self._details_name.setAttribute(Qt.WA_NoSystemBackground, True)
        self._details_version.setAutoFillBackground(False)
        self._details_version.setAttribute(Qt.WA_TranslucentBackground, False)
        self._details_version.setAttribute(Qt.WA_NoSystemBackground, True)
        self._details_header.setVisible(False)
        from .surfaces import clear_glass_plate
        clear_glass_plate(self._details_header)
        clear_glass_plate(self._details_name)
        clear_glass_plate(self._details_version)
        clear_glass_plate(self._details_icon)
        notes_l.addWidget(self._details_header)

        self._notes = _ReleaseNotesBrowser()
        self._notes.setObjectName("releaseNotes")
        self._notes.setAttribute(Qt.WA_TranslucentBackground, False)
        self._notes.setReadOnly(True)
        self._notes.setOpenLinks(False)
        self._notes.setOpenExternalLinks(False)
        self._notes.viewport().setAutoFillBackground(False)
        self._notes.viewport().setAttribute(Qt.WA_TranslucentBackground, False)
        self._notes.setFrameShape(QFrame.NoFrame)
        self._notes.setLineWrapMode(QTextBrowser.WidgetWidth)
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        # Anchors inside the notes are the text colour and bold, never the
        # document default blue-with-underline (document stylesheet, not a
        # widget stylesheet: the platform keeps the scrollbars).
        self._notes.document().setDefaultStyleSheet(
            f"body {{ background: transparent; }} a {{ color: {T().fg}; font-weight: 700; text-decoration: none; }}"
        )
        # Host policies plus the live palette. No widget stylesheet on this
        # view: a matching rule would cost the host's overlay scrollbars
        # (see src/ui/scrollbars.py).
        configure_scroll_area(
            self._notes,
            horizontal=Qt.ScrollBarAlwaysOff,
            vertical=Qt.ScrollBarAsNeeded,
            transparent=True,
        )
        self._notes.setAutoFillBackground(False)
        self._notes.setAttribute(Qt.WA_NoSystemBackground, False)
        self._notes.viewport().setAutoFillBackground(False)
        self._notes.viewport().setAttribute(Qt.WA_NoSystemBackground, False)
        self._notes.setStyleSheet("")
        self._notes.viewport().setStyleSheet("")
        self._notes.verticalScrollBar().setStyleSheet("")
        self._notes.horizontalScrollBar().setStyleSheet("")
        self._notes.setAttribute(Qt.WA_StyledBackground, False)
        self._notes.viewport().setAttribute(Qt.WA_StyledBackground, False)
        from .surfaces import clear_glass_plate
        clear_glass_plate(self._release_list)
        clear_glass_plate(self._notes)
        self._notes.anchorClicked.connect(self._on_notes_link_clicked)
        notes_l.addWidget(self._notes)

        self._translate_label = QLabel()
        self._translate_label.setObjectName("translateReleaseNotes")
        self._translate_label.setWordWrap(True)
        self._translate_label.setAlignment(Qt.AlignCenter)
        self._translate_label.setStyleSheet(
            "color: palette(window-text); font-size: 11px; margin-top: 4px; background: transparent;"
        )
        self._translate_label.setTextFormat(Qt.RichText)
        self._translate_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._translate_label.linkActivated.connect(self._on_translate_link_clicked)
        self._translate_label.setVisible(False)
        notes_l.addWidget(self._translate_label)
        # English-only on purpose: the GitHub page itself is untranslated.
        self._see_more_label = QLabel()
        self._see_more_label.setObjectName("seeMoreReleaseNotes")
        self._see_more_label.setAlignment(Qt.AlignCenter)
        self._see_more_label.setStyleSheet("font-size: 11px; margin-top: 2px; background: transparent;")
        self._see_more_label.setTextFormat(Qt.RichText)
        self._see_more_label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._see_more_label.setOpenExternalLinks(False)
        self._see_more_label.linkActivated.connect(self._on_see_more_clicked)
        self._see_more_label.setVisible(False)
        notes_l.addWidget(self._see_more_label)
        notes_l.addStretch(1)

        self._notes_group.set_layout(notes_l)
        right_layout.addWidget(self._notes_group, 1)

        split.addWidget(right_panel, 6)
        layout.addLayout(split, 1)
        return page

    def _set_notes_content(self, html_text: str):
        self._notes.setHtml(html_text)
        from .surfaces import clear_glass_plate
        clear_glass_plate(self._notes)
        self._notes.updateGeometry()
        if hasattr(self, "_notes_group") and self._notes_group:
            self._notes_group.updateGeometry()
            self._notes_group.repaint()
        if hasattr(self, "_notes") and self._notes and self._notes.viewport():
            self._notes.viewport().repaint()
        self.content_size_changed.emit()

    def _build_local_tab(self):
        t = T()
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(10)

        self._hint = QLabel(tr("sel_only_local"))
        self._hint.setWordWrap(True)
        self._hint.setProperty("cssClass", "hint")
        layout.addWidget(self._hint)

        self._local_group = local_group = Card("sel_choose_firmware_pkg")
        grp_l = QVBoxLayout()
        grp_l.setContentsMargins(0, 0, 0, 0)
        grp_l.setSpacing(6)

        row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._browse_btn = QPushButton(tr("sel_browse"))
        self._browse_btn.setIcon(self._button_icon("file", 16))
        self._browse_btn.clicked.connect(self._on_choose_file)
        self._browse_folder_btn = QPushButton(tr("sel_browse_folder"))
        self._browse_folder_btn.setIcon(self._button_icon("folder", 16))
        self._browse_folder_btn.clicked.connect(self._on_choose_folder)
        row.addWidget(self._path_edit, 1)
        row.addWidget(self._browse_btn)
        row.addWidget(self._browse_folder_btn)
        grp_l.addLayout(row)

        # MediaTek Installer: the three files SP Flash Tool's own setup asks
        # for. The Download Agent and scatter are required; an auth file is
        # only needed by some secure-booted firmwares. Updater CE hides both
        # rows — there the firmware package (and its bundled DA) is the whole
        # input.
        self._da_row_widget = QWidget()
        da_row = QHBoxLayout(self._da_row_widget)
        da_row.setContentsMargins(0, 0, 0, 0)
        da_row.setSpacing(8)
        self._da_label = QLabel(tr("mtk_da_label") + ":")
        self._da_edit = QLineEdit()
        self._da_edit.setReadOnly(True)
        self._da_edit.setPlaceholderText(tr("mtk_da_placeholder"))
        self._browse_da_btn = QPushButton(tr("sel_browse"))
        self._browse_da_btn.setIcon(self._button_icon("file", 16))
        self._browse_da_btn.clicked.connect(self._on_choose_da_file)
        self._clear_da_btn = QPushButton(tr("settings_sp_auth_clear"))
        self._clear_da_btn.setIcon(self._button_icon("cancel", 12))
        self._clear_da_btn.clicked.connect(self._on_clear_da_file)
        da_row.addWidget(self._da_label)
        da_row.addWidget(self._da_edit, 1)
        da_row.addWidget(self._browse_da_btn)
        da_row.addWidget(self._clear_da_btn)
        grp_l.addWidget(self._da_row_widget)
        self._da_row_widget.setVisible(False)

        self._scatter_row_widget = QWidget()
        scatter_row = QHBoxLayout(self._scatter_row_widget)
        scatter_row.setContentsMargins(0, 0, 0, 0)
        scatter_row.setSpacing(8)
        self._scatter_label = QLabel(tr("mtk_scatter_label") + ":")
        self._scatter_edit = QLineEdit()
        self._scatter_edit.setReadOnly(True)
        self._scatter_edit.setPlaceholderText(tr("mtk_scatter_placeholder"))
        self._browse_scatter_btn = QPushButton(tr("sel_browse"))
        self._browse_scatter_btn.setIcon(self._button_icon("file", 16))
        self._browse_scatter_btn.clicked.connect(self._on_choose_scatter_file)
        scatter_row.addWidget(self._scatter_label)
        scatter_row.addWidget(self._scatter_edit, 1)
        scatter_row.addWidget(self._browse_scatter_btn)
        grp_l.addWidget(self._scatter_row_widget)
        self._scatter_row_widget.setVisible(False)

        # Auth file row: MediaTek Installer only. The Innioasis targets Updater
        # CE serves have no secure boot to answer — they flash with the bundled
        # MTK_AllInOne_DA.bin, the same way the Chinese build does — so Updater
        # CE never offers an auth file at all.
        self._auth_row_widget = QWidget()
        self._auth_row = QHBoxLayout(self._auth_row_widget)
        self._auth_row.setContentsMargins(0, 0, 0, 0)
        self._auth_row.setSpacing(8)
        self._auth_label = QLabel(tr("settings_sp_auth") + ":")
        self._auth_edit = QLineEdit()
        self._auth_edit.setReadOnly(True)
        self._auth_edit.setPlaceholderText(tr("settings_sp_auth_none"))
        self._browse_auth_btn = QPushButton(tr("settings_sp_auth_browse"))
        self._browse_auth_btn.setIcon(self._button_icon("file", 16))
        self._browse_auth_btn.clicked.connect(self._on_choose_auth_file)
        self._clear_auth_btn = QPushButton(tr("settings_sp_auth_clear"))
        self._clear_auth_btn.setIcon(self._button_icon("cancel", 12))
        self._clear_auth_btn.clicked.connect(self._on_clear_auth_file)
        self._auth_row.addWidget(self._auth_label)
        self._auth_row.addWidget(self._auth_edit, 1)
        self._auth_row.addWidget(self._browse_auth_btn)
        self._auth_row.addWidget(self._clear_auth_btn)
        grp_l.addWidget(self._auth_row_widget)
        self._auth_row_widget.setVisible(False)
        self._sync_auth_file_ui()

        # Retained as hidden attribute for API compatibility; dark banner card is omitted from UI per native design
        self._local_banner = Banner()
        self._local_banner.setVisible(False)

        self._local_bar = InstallProgress()
        self._local_bar.setTextVisible(True)
        self._local_bar.setFormat("%p%")
        self._local_bar.setMinimumHeight(18)
        from .dark import install_progress_readability
        install_progress_readability(self._local_bar)
        self._local_bar.setVisible(False)
        grp_l.addWidget(self._local_bar)

        self._local_status = QLabel("")
        from .dark import apply_explicit_foreground
        apply_explicit_foreground(self._local_status, " font-size: 12px; border: none;")
        grp_l.addWidget(self._local_status)

        self._local_rockbox_themes_cb = QCheckBox(tr("sel_local_rockbox_themes_option"))
        # Opt-out: themes and fonts are pushed after a Y1 Rockbox install unless
        # the user clears this box (or turns the Settings toggle off).
        self._local_rockbox_themes_cb.setChecked(True)
        self._local_rockbox_themes_cb.setVisible(not is_mediatek_installer())
        grp_l.addWidget(self._local_rockbox_themes_cb)

        self._start_btn = QPushButton(tr("sel_btn_start"))
        self._start_btn.setIcon(self._install_restore_icon())
        self._start_btn.setDefault(True)
        self._start_btn.setAutoDefault(True)
        self._start_btn.setMinimumHeight(32)
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._on_start_flash)
        grp_l.addWidget(self._start_btn)

        # ...and the way back to the catalogue list. Same weight as the file
        # link above: both are secondary navigation, and the primary action is
        # the button.
        self._install_online_link = self._make_link(
            "sel_install_online", self._show_online_view, bold=False
        )
        self._install_online_link.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        grp_l.addWidget(self._install_online_link)
        grp_l.addStretch(1)

        local_group.set_layout(grp_l)
        layout.addWidget(local_group)
        layout.addStretch()
        return page

    def _sync_auth_file_ui(self):
        from .. import device_tracking
        current = device_tracking.sp_auth_file(getattr(self, "settings", None))
        if hasattr(self, "_auth_edit"):
            self._auth_edit.setText(current or "")
            if hasattr(self, "_clear_auth_btn"):
                self._clear_auth_btn.setVisible(bool(current))

    def _on_choose_auth_file(self):
        from .. import device_tracking
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr("settings_sp_auth"),
            downloads.default_picker_dir(),
            f"{tr('settings_sp_auth_filter')};;{tr('log_filter_all_files')} (*.*)",
        )
        if path:
            device_tracking.set_sp_auth_file(path, getattr(self, "settings", None))
            self._sync_auth_file_ui()

    def _on_clear_auth_file(self):
        from .. import device_tracking
        device_tracking.set_sp_auth_file("", getattr(self, "settings", None))
        self._sync_auth_file_ui()

    def _auto_detect_auth_file(self, target_path: str) -> None:
        """Scan package directory or archive location for an .auth file (e.g. authsv.auth, *.auth)."""
        from .. import device_tracking
        p = Path(target_path)
        candidates = []
        if p.is_dir():
            candidates.append(p)
        elif p.is_file():
            candidates.append(p.parent)
        try:
            from ..flash_service import compute_extract_dir
            ext = compute_extract_dir(target_path)
            if ext.is_dir() and ext not in candidates:
                candidates.append(ext)
        except Exception:
            pass

        for c in candidates:
            try:
                for f in c.rglob("*.auth"):
                    if f.is_file() and f.stat().st_size > 0:
                        logger.info("Auto-detected auth file: %s", f)
                        device_tracking.set_sp_auth_file(str(f.resolve()), getattr(self, "settings", None))
                        self._sync_auth_file_ui()
                        return
            except Exception:
                pass

    # ── MediaTek Installer: Download Agent / scatter ──────────────────────
    def apply_installer_mode(self, enabled: bool) -> None:
        """Show the DA + scatter + auth fields the generic installer flashes with.

        The installer is a front end for the same two backends Updater CE uses;
        it just takes the three files SP Flash Tool's own setup takes (Download
        Agent, scatter, optional auth) instead of a firmware package. Updater CE
        keeps its single package row and its bundled DA — and no auth row, since
        none of its targets use one.
        """
        self._installer_mode = bool(enabled)
        for widget in (
            getattr(self, "_da_row_widget", None),
            getattr(self, "_scatter_row_widget", None),
            getattr(self, "_auth_row_widget", None),
        ):
            if widget is not None:
                widget.setVisible(bool(enabled))
        for name in ("_path_edit", "_browse_btn", "_browse_folder_btn"):
            widget = getattr(self, name, None)
            if widget is not None:
                widget.setVisible(not enabled)
        if hasattr(self, "_local_rockbox_themes_cb"):
            self._local_rockbox_themes_cb.setVisible(not enabled)
        if enabled:
            self._prefill_da()
        self._sync_auth_file_ui()
        self._update_local_labels()

    def _update_local_labels(self) -> None:
        if hasattr(self, "_da_label"):
            self._da_label.setText(tr("mtk_da_label") + ":")
            self._da_edit.setPlaceholderText(tr("mtk_da_placeholder"))
            self._browse_da_btn.setText(tr("sel_browse"))
            self._scatter_label.setText(tr("mtk_scatter_label") + ":")
            self._scatter_edit.setPlaceholderText(tr("mtk_scatter_placeholder"))
            self._browse_scatter_btn.setText(tr("sel_browse"))
        if self._installer_mode and hasattr(self, "_local_group"):
            self._local_group.setTitle(tr("mtk_choose_flash_files"))

    def _prefill_da(self) -> None:
        """Offer the Download Agent the app already ships, when there is one."""
        if not self._installer_da:
            try:
                from ..sp_flash_gui import bundled_da_path

                da = bundled_da_path()
            except Exception:
                logger.debug("Could not locate a bundled download agent", exc_info=True)
                da = None
            if da:
                self._installer_da = str(da)
        self._sync_da_ui()

    def _sync_da_ui(self) -> None:
        if not hasattr(self, "_da_edit"):
            return
        self._da_edit.setText(self._installer_da or "")
        self._clear_da_btn.setVisible(bool(self._installer_da))

    def _on_choose_da_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("mtk_da_label"), downloads.default_picker_dir(), tr("mtk_da_filter")
        )
        if path:
            self._installer_da = path
            self._sync_da_ui()

    def _on_clear_da_file(self) -> None:
        self._installer_da = ""
        self._sync_da_ui()

    def _on_choose_scatter_file(self) -> None:
        """Point the installer at a scatter file (its images live beside it)."""
        path, _ = QFileDialog.getOpenFileName(
            self, tr("mtk_scatter_label"), downloads.default_picker_dir(), tr("mtk_scatter_filter")
        )
        if not path:
            return
        p = Path(path)
        if not p.is_file():
            return
        self._scatter_edit.setText(str(p))
        # SP Flash Tool's own layout: the partition images sit in the folder
        # beside the scatter file, so that folder is the package for this run.
        package_target = str(p.parent)
        self._path_edit.setText(package_target)
        self._current_package_path = package_target
        self._gui_explicit = None
        self._gui_previous_identity = None
        self._emit_gui_firmware_changed()
        self._current_installed_release_info = None

        self._current_selected_rel = None
        self._details_package = None
        self._raw_release_pixmap = None
        self._auto_detect_auth_file(package_target)
        mfr_info = catalog.parse_manufacturer_rom_info(p.parent.name) or catalog.parse_manufacturer_rom_info(p.name)
        if mfr_info:
            det_m = mfr_info["model"]
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
            self._selected_type = None
            self._current_package_name = mfr_info["title"]
        else:
            det_m, det_t = detect_model_and_type_from_name(p.parent.name)
            if not det_m:
                det_m, det_t = detect_model_and_type_from_name(p.name)
            self._current_package_model = det_m or ""
            if det_m:
                self._select_model_for_manual(det_m)
            inf_name, inf_ver = infer_release_name(det_m, (p.parent.name, p.name))
            self._current_package_name = f"{inf_name} {inf_ver}".strip() if inf_name else ""
            self._selected_type = det_t or None
        self._remember_gui_local(package_target, self._current_package_model, self._current_package_name)
        self._set_local_banner("sel_current_pkg", p.name)
        self._details_package = catalog.FirmwarePackage(
            slug="local-" + str(Path(package_target).name),
            name=self._current_package_name or Path(package_target).name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._set_details_header(None, self._details_package)
        self._on_local_prep_done(True, str(p.parent), "")

    def installer_da_file(self) -> str:
        """The Download Agent chosen for a MediaTek Installer flash (or "")."""
        return self._installer_da if self._installer_mode else ""

    def installer_auth_file(self) -> str:
        """The auth file chosen on this screen (falls back to the saved one)."""
        return device_tracking.sp_auth_file(getattr(self, "settings", None)) or ""

    def _type_help_tip(self) -> str:
        """Full device-type help as a wrapping tooltip. No dialog.

        Plain text, not rich text: the tip is drawn by the platform, and a
        rich-text document would need its own colours written into it — which
        is what made these tips look nothing like the host's own tooltips.
        """
        body = tr("sel_type_help_body")
        if not body or body == "sel_type_help_body":
            body = tr("device_type_help_msg") or tr("sel_type_help_tooltip")
        return (body or "").strip()

    def _refresh_type_help_icon(self) -> None:
        """Same hover tip as the Type combo, on the platform information glyph beside it.

        Tinted with the accent, matching the information badge the host desktop
        draws in its own settings panes.
        """
        icon = getattr(self, "_type_help_icon", None)
        if icon is None:
            return
        icon.setCursor(Qt.ArrowCursor)
        theme = T()
        color = getattr(theme, "accent", "") or getattr(theme, "fg_muted", None)
        pix = get_help_pixmap(HELP_ICON_PX, color=color)
        icon.setText("")
        if pix is not None and not pix.isNull():
            icon.setPixmap(pix)
        else:
            icon.setPixmap(QPixmap())
        icon.setProperty("helpSource", help_icon_source())

    def _apply_type_help_hint(self) -> None:
        tip = self._type_help_tip()
        if hasattr(self, "_type_combo"):
            self._type_combo.setToolTip(tip)
        if hasattr(self, "_type_label"):
            self._type_label.setToolTip(tip)
        if hasattr(self, "_type_help_icon"):
            self._type_help_icon.setToolTip(tip)
            self._refresh_type_help_icon()

    def _should_prompt_pre_install(self) -> bool:
        """Whether the run asks the user to prepare the player before starting.

        Every method is gated on this, on every platform: the install must not
        start hunting for the player until the user has confirmed their model
        is powered off and disconnected. The Linux SP Flash Tool flow used to
        defer the question to the tool's own "search usb" line, which meant an
        install could already be running before the user had been asked at all.
        """
        return True

    def _prompt_pre_install(self, model="", type_variant=None):
        """Device preparation is a page in the install flow, not a dialog."""
        return True

    def _set_update_prompt(self, software: str, model: str, offer=None) -> None:
        """Keep the catalogue's current model in the update note.

        Other models already on the note stay. An empty offer removes only
        this model. Session dismiss hides a release until the next launch.
        """
        software = (software or "").strip()
        model = (model or "").strip()
        offers = [
            item for item in (getattr(self, "_update_offers", None) or [])
            if not (item.get("model") == model and item.get("software") == software)
        ]
        if software and model and offer:
            offers.append({
                "model": model,
                "software": software,
                "tag": str(offer.get("tag") or ""),
                "release": offer.get("release"),
                "package": offer.get("package"),
            })
        self._update_offers = offers
        self._present_update_offers()

    def show_update_offers(self, updates) -> None:
        """Show every device that has a newer release. Does not start an install."""
        offers = []
        for upd in updates or []:
            model = str((upd or {}).get("model") or "").strip()
            software = str((upd or {}).get("software_name") or "").strip()
            if not model or not software:
                continue
            offers.append({
                "model": model,
                "software": software,
                "tag": str((upd or {}).get("latest_tag") or ""),
                "release": (upd or {}).get("latest_release"),
                "package": (upd or {}).get("package"),
            })
        self._update_offers = offers
        self._present_update_offers()

    def show_app_update(self, info) -> None:
        """Display an application update toast in the toast spot.

        Takes precedence over device firmware updates until dismissed.
        """
        self._app_update_info = info
        prompt = getattr(self, "_app_update_prompt", None)
        if prompt is not None:
            prompt.set_update(info)
        self._present_update_offers()

    def _on_app_update_action(self) -> None:
        """User clicked Update on the app update toast."""
        if self._app_update_info is not None:
            self.app_update_clicked.emit(self._app_update_info)

    def _on_app_update_dismiss(self) -> None:
        """User dismissed the app update toast.

        Reveals device firmware update reminder if any are waiting.
        """
        self._app_update_info = None
        prompt = getattr(self, "_app_update_prompt", None)
        if prompt is not None:
            prompt.setVisible(False)
        self._present_update_offers()

    def _present_update_offers(self) -> None:
        # Precedence: if app update toast is currently visible, hide device update prompt
        app_prompt = getattr(self, "_app_update_prompt", None)
        app_active = bool(app_prompt and app_prompt.isVisible() and self._app_update_info and getattr(self._app_update_info, "tag", None))

        # Device offers are one toast per model (see _sync_model_toasts); the
        # single combined note is no longer used for them.
        self._sync_model_toasts(suppressed=app_active)

    def _sync_model_toasts(self, suppressed: bool = False) -> None:
        """Rebuild one toast per device with a newer release that is not muted.

        Stacked toasts change the page height, so the window is told to refit.
        """
        for toast in getattr(self, "_model_toasts", []):
            # Hide and unparent now: deleteLater alone keeps the old toast on
            # screen and in the layout until the event loop runs again.
            toast.hide()
            self._model_toast_column.removeWidget(toast)
            toast.setParent(None)
            toast.deleteLater()
        self._model_toasts = []
        if not suppressed:
            muted = getattr(self, "_toast_session_mute", set())
            for offer in getattr(self, "_update_offers", None) or []:
                key = (offer.get("model"), offer.get("software"), str(offer.get("tag") or ""))
                if key in muted:
                    continue
                toast = UpdateModelToast(offer, self)
                toast.install_clicked.connect(self._on_model_toast_install)
                toast.later_clicked.connect(self._on_model_toast_later)
                self._model_toast_column.addWidget(toast)
                toast.show()
                self._model_toasts.append(toast)
        self.content_size_changed.emit()

    def _on_model_toast_later(self, offer) -> None:
        """X on one device toast: hide that offer until the next launch."""
        if not isinstance(offer, dict):
            return
        self._toast_session_mute = set(getattr(self, "_toast_session_mute", set()))
        self._toast_session_mute.add((
            offer.get("model"),
            offer.get("software"),
            str(offer.get("tag") or ""),
        ))
        self._present_update_offers()

    def _on_model_toast_install(self, offer) -> None:
        """Check on one device toast: install that device's release now."""
        if not isinstance(offer, dict):
            return
        release = offer.get("release")
        if not release:
            return
        self._keep_settings_default = True
        self.start_install_for_release(
            model=str(offer.get("model") or self.current_model()),
            software_name=str(offer.get("software") or self.current_software()),
            tag_name=str(offer.get("tag") or ""),
            release=release,
            package=offer.get("package") or self._package_for_selection(),
            keep_settings=True,
        )

    def _set_online_banner(self, key, count=0):
        self._online_banner_key = key
        self._online_banner_count = count
        self._apply_online_banner()

    def _apply_online_banner(self):
        key = self._online_banner_key
        if key == "releases":
            self._online_banner.set_type("info")
            self._online_banner.setText(
                tr("sel_n_releases_fmt").format(
                    n=self._online_banner_count, action=tr("sel_install")
                )
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
        self._local_banner.setVisible(False)

    def _check_battery_toast_visibility(self) -> None:
        from PySide6.QtCore import QSettings

        settings = self.settings or QSettings("innioasis", "updater")
        dismissed = bool(settings.value("battery_hint_dismissed", False, type=bool))
        self._battery_toast.setVisible(not dismissed)
        if hasattr(self, "_toast_container"):
            self._toast_container.setVisible(not dismissed)

    def _on_dismiss_battery_toast(self) -> None:
        from PySide6.QtCore import QSettings

        settings = self.settings or QSettings("innioasis", "updater")
        settings.setValue("battery_hint_dismissed", True)
        self._battery_toast.setVisible(False)
        if hasattr(self, "_toast_container"):
            self._toast_container.setVisible(False)
        # The toast spot leaves the page's height, so the window must refit.
        self.content_size_changed.emit()
    def reset_selection_and_filters(self) -> None:
        """Reset filters, selections and cached firmware display back to defaults.

        A dismissed battery reminder stays dismissed: the toast is a hint the
        user closed, not a filter this reset owns.
        """
        from PySide6.QtCore import QSettings

        settings = self.settings or QSettings("innioasis", "updater")
        self._check_battery_toast_visibility()
        if hasattr(self, "_brand_combo") and self._brand_combo.count() > 0:
            # Back to the default brand first: the device list is scoped to it.
            self._brand_combo.blockSignals(True)
            self._brand_combo.setCurrentIndex(0)
            self._brand_combo.blockSignals(False)
            self._populate_model_combo()
        if hasattr(self, "_model_combo") and self._model_combo.count() > 0:
            self._model_combo.blockSignals(True)
            self._model_combo.setCurrentIndex(0)
            self._model_combo.blockSignals(False)
        if hasattr(self, "_type_combo") and self._type_combo.count() > 0:
            self._type_combo.blockSignals(True)
            self._type_combo.setCurrentIndex(0)
            self._type_combo.blockSignals(False)
        if hasattr(self, "_software_combo"):
            # Rebuild the names for the reset model instead of leaving the
            # previous model's list in place, and start on Original Software.
            names = catalog.software_names_for_model(
                self.current_model(), brand=self.current_brand()
            )
            self._populate_software_combo(names)
        if hasattr(self, "_software_combo") and not is_generic_mtk():
            # The resets above rebuilt the software choice without signals.
            # Start the matching listing now; it replaces any worker still
            # fetching the pre-reset choice before the catalogue is shown.
            self._on_software_changed()
        if hasattr(self, "_show_online_view"):
            self._show_online_view()
        if hasattr(self, "_check_and_load_cached_firmware"):
            self._check_and_load_cached_firmware()
        self._sync_notice_context()
        self.content_size_changed.emit()

    def retranslate(self):
        self._title.setText(tr("sel_title"))
        if hasattr(self, "_battery_toast"):
            self._battery_toast.retranslate()
        self._install_from_file_link.setText(
            self._link_html("sel_install_from_file", bold=False)
        )
        self._install_online_link.setText(
            self._link_html("sel_install_online", bold=False)
        )
        self._brand_label.setText(f"{tr('sel_brand')}:")
        self._model_label.setText(f"{tr('sel_model')}:")
        self._type_label.setText(f"{tr('sel_type')}:")
        self._software_label.setText(f"{tr('sel_software')}:")
        if hasattr(self, "_keep_data_note"):
            self._keep_data_note.setText(tr("sel_keep_data_incompatible"))
        self._refresh_btn.setText(tr("sel_refresh"))
        self._install_btn.setText(tr("sel_install"))
        if self._current_selected_rel:
            lbl = self._current_selected_rel.get("name") or catalog.release_effective_tag(self._current_selected_rel) or self._current_selected_rel.get("tag_name") or ""
            self._install_btn.setToolTip(tr("sel_install_tooltip").format(lbl=lbl))
        else:
            self._install_btn.setToolTip(tr("sel_install_tooltip_generic"))
        if is_generic_mtk():
            self._hint.setText(tr("sel_only_local_generic"))
        else:
            self._hint.setText(tr("sel_only_local"))
        self._path_edit.setPlaceholderText(tr("sel_placeholder"))
        self._notes.setPlaceholderText(tr("sel_notes_hint"))
        # Anchors inside the notes are the text colour and bold, never the
        # document default blue-with-underline (document stylesheet, not a
        # widget stylesheet: the platform keeps the scrollbars).
        self._notes.document().setDefaultStyleSheet(
            f"body {{ background: transparent; }} a {{ color: {T().fg}; font-weight: 700; text-decoration: none; }}"
        )
        self._browse_btn.setText(tr("sel_browse"))
        if hasattr(self, "_browse_folder_btn"):
            self._browse_folder_btn.setText(tr("sel_browse_folder"))
        if hasattr(self, "_auth_label"):
            self._auth_label.setText(tr("settings_sp_auth") + ":")
        if hasattr(self, "_browse_auth_btn"):
            self._browse_auth_btn.setText(tr("settings_sp_auth_browse"))
        if hasattr(self, "_clear_auth_btn"):
            self._clear_auth_btn.setText(tr("settings_sp_auth_clear"))
        if hasattr(self, "_auth_edit"):
            self._auth_edit.setPlaceholderText(tr("settings_sp_auth_none"))
        self._update_local_labels()
        if hasattr(self, "_local_rockbox_themes_cb"):
            self._local_rockbox_themes_cb.setText(tr("sel_local_rockbox_themes_option"))
        self._start_btn.setText(tr("sel_btn_start"))
        self._apply_online_banner()
        self._apply_local_banner()
        self._type_combo.setItemText(0, tr("sel_type_a"))
        self._type_combo.setItemText(1, tr("sel_type_b"))
        # Software names carry preset translations: re-label the dropdown rows
        # in place, keeping the canonical English name as each row's data.
        self._software_combo.blockSignals(True)
        for i in range(self._software_combo.count()):
            canonical = str(
                self._software_combo.itemData(i) or self._software_combo.itemText(i)
            )
            self._software_combo.setItemData(i, canonical)
            self._software_combo.setItemText(i, display_software_name(canonical))
        self._software_combo.blockSignals(False)
        self._apply_type_help_hint()
        self._refresh_btn.setToolTip(tr("sel_refresh_tooltip"))
        self._pkg_group.setTitle(tr("sel_available_software"))
        self._local_group.setTitle(
            tr("mtk_choose_flash_files") if self._installer_mode else tr("sel_choose_firmware_pkg")
        )
        self._update_device_status_copy()
        if self._download_status_key:
            self._say(tr(self._download_status_key))
        if self._local_status_key:
            self._local_status.setText(tr(self._local_status_key))
        if self._current_selected_rel:
            if self._is_translated or (
                self._auto_translate_notes and not is_english_language()
            ):
                self._translate_current_release_in_app()
            else:
                self._set_notes_content(self._render_release_notes(self._current_selected_rel, as_html=True))
            self._set_details_header(
                self._current_selected_rel,
                getattr(self, "_details_package", None) or self._package_for_selection(),
            )
        self._update_translate_link()
        if hasattr(self, "_registered_links"):
            for label, key, bold in self._registered_links:
                label.setText(self._link_html(key, bold=bold))

    def refresh_theme(self):
        """Update components and release notes typography to match active theme tokens."""
        self._apply_type_help_hint()
        if hasattr(self, "_notes"):
            self._notes.document().setDefaultStyleSheet(
                f"body {{ background: transparent; }} a {{ color: {T().fg}; font-weight: 700; text-decoration: none; }}"
            )
        if hasattr(self, "_notes") and self._current_selected_rel:
            self._set_notes_content(self._render_release_notes(self._current_selected_rel, as_html=True))
        if hasattr(self, "_translate_label"):
            self._update_translate_link()
        if hasattr(self, "_release_list"):
            make_transparent(self._release_list)
            from .surfaces import clear_glass_plate
            clear_glass_plate(self._release_list)
        if hasattr(self, "_notes"):
            from .surfaces import clear_glass_plate
            clear_glass_plate(self._notes)
        if hasattr(self, "_details_header"):
            from .surfaces import clear_glass_plate
            clear_glass_plate(self._details_header)
            clear_glass_plate(self._details_name)
            clear_glass_plate(self._details_version)
        if hasattr(self, "_registered_links"):
            for label, key, bold in self._registered_links:
                label.setText(self._link_html(key, bold=bold))
        if hasattr(self, "_install_btn"):
            self._install_btn.setIcon(self._install_restore_icon())
        if hasattr(self, "_battery_toast") and hasattr(self._battery_toast, "refresh_theme"):
            self._battery_toast.refresh_theme()
        rel = getattr(self, "_current_selected_rel", None)
        if rel and hasattr(self, "_details_icon"):
            self._set_details_header(rel, getattr(self, "_details_package", None) or self._package_for_selection())
        listed = list(getattr(self, "_listed_releases", None) or [])
        if listed:
            self._prefetch_release_icons(listed)
        elif rel:
            self._ensure_release_icon(rel)
        if hasattr(self, "_browse_btn"):
            self._browse_btn.setIcon(self._button_icon("file", 16))
        if hasattr(self, "_browse_folder_btn"):
            self._browse_folder_btn.setIcon(self._button_icon("folder", 16))
        if hasattr(self, "_start_btn"):
            self._start_btn.setIcon(self._install_restore_icon())
        for attr, symbol, size in (
            ("_browse_da_btn", "file", 16),
            ("_clear_da_btn", "cancel", 12),
            ("_browse_scatter_btn", "file", 16),
            ("_browse_auth_btn", "file", 16),
            ("_clear_auth_btn", "cancel", 12),
        ):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setIcon(self._button_icon(symbol, size))
        from .dark import apply_explicit_foreground, install_progress_readability
        status = getattr(self, "_local_status", None)
        if status is not None:
            apply_explicit_foreground(status, " font-size: 12px; border: none;")
        for bar_name in ("_download_bar", "_local_bar"):
            bar = getattr(self, bar_name, None)
            if bar is not None:
                install_progress_readability(bar)

    def refresh_models(self):
        """Repopulate the brand and device drop-downs from the catalogue.

        The brand drop-down only exists when ``slidia_manifest.xml`` tags
        releases with a ``brand`` attribute; it scopes this listing and nothing
        else. Only models with a release in the live catalogue
        (``slidia_manifest.xml``) are listed — there is no point offering a
        device with nothing to install, and a package browsed by hand must
        never add a row here. Such a package keeps its model on
        ``_current_package_model``, which is what the prompts and the install
        use; the drop-down returns to its default row instead.
        """
        self._sync_brand_combo()
        self._populate_model_combo()

    def current_brand(self) -> str:
        """Brand the release listing is scoped to, or "" when none is offered."""
        combo = getattr(self, "_brand_combo", None)
        if combo is None or combo.count() == 0 or combo.currentIndex() < 0:
            return ""
        return str(combo.itemData(combo.currentIndex()) or combo.currentText())

    def _sync_brand_combo(self) -> None:
        """Fill the brand drop-down from the catalogue and the registry."""
        combo = getattr(self, "_brand_combo", None)
        if combo is None:
            return
        from .. import device_models

        brands = catalog.brand_names()
        # A brand the registry knows but no release has been published for yet
        # (Timmkoo's A5, SR1, Q3, Q5, Q8) still needs its row: its models are
        # selectable, and without the brand filter they could not be reached.
        for name in device_models.brand_names():
            if not any(name.casefold() == known.casefold() for known in brands):
                brands.append(name)
        current = self.current_brand()
        combo.blockSignals(True)
        combo.clear()
        for brand in brands:
            combo.addItem(brand, brand)
        if brands:
            idx = combo.findData(current) if current else -1
            # Innioasis is the first brand, so index 0 is the default.
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        combo.setVisible(bool(brands))
        if hasattr(self, "_brand_label"):
            self._brand_label.setVisible(bool(brands))

    def _on_brand_changed(self, *_):
        """Brand scopes the model list, and through it the release listing."""
        self._populate_model_combo(refresh_selection=True)

    def _populate_model_combo(self, refresh_selection: bool = False) -> None:
        from .. import device_models

        brand = self.current_brand()
        model_ids = device_models.selectable_models(
            available_ids=catalog.available_models(brand=brand),
            fallback=DEVICE_MODELS,
            brand=brand,
        )
        current = self._model_combo.currentText().strip()

        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(model_ids)
        idx = self._model_combo.findText(current) if current else -1
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)
        reset = bool(current) and idx < 0 and self._model_combo.count() > 0
        if reset:
            # The selected model is no longer offered (the manifest changed):
            # back to the default row, and the software list follows it below.
            self._model_combo.setCurrentIndex(0)
        self._model_combo.blockSignals(False)
        if (reset or refresh_selection) and hasattr(self, "_software_combo"):
            self._on_model_changed()

    def _select_model_for_manual(self, model: str) -> None:
        """Point the device drop-down at a hand-imported package's model.

        Every flashable registry model has a row (a G5 with no release yet
        included). A model the registry does not know at all still reaches the
        prompts and the install through ``_current_package_model``, so the list
        returns to its default row rather than inventing one.
        """
        if not model:
            return
        idx = self._model_combo.findText(model)
        if idx < 0:
            if self._model_combo.count():
                self._model_combo.setCurrentIndex(0)
            return
        self._model_combo.setCurrentIndex(idx)

    def _on_model_changed(self):
        previous_software = (
            self.current_software() if hasattr(self, "_software_combo") else ""
        )
        model = self.current_model()
        # Show the type filter only for models that have Type A/B variants (Y1).
        has_types = model.upper() == "Y1"
        self._type_label.setVisible(has_types)
        self._type_combo.setVisible(has_types)
        if hasattr(self, "_type_help_icon"):
            self._type_help_icon.setVisible(has_types)
        if hasattr(self, "_type_field"):
            self._type_field.setVisible(has_types)
        if not has_types:
            self._selected_type = "A"
        else:
            self._selected_type = self._type_combo.currentData() or "A"
        names = catalog.software_names_for_model(model, brand=self.current_brand())
        # The same software name means the equivalent firmware for the new
        # model; anything else falls back to Original Software in the combo.
        self._populate_software_combo(
            names,
            select=self._software_name_kept_across_models(previous_software, names),
        )
        self._update_device_status_copy()
        self._on_software_changed()
        self._sync_notice_context()

    def _on_notice_ready(self, notice) -> None:
        if hasattr(self, "_notice_prompt") and self._notice_prompt is not None:
            self._notice_prompt.set_notice(notice)

    def _on_notices_ready(self, notices) -> None:
        self._active_notices = list(notices or [])

    def _open_notices_list(self) -> None:
        """Open the Notices tab in the main window."""
        window = self.window()
        if hasattr(window, "_nav_to_page") and hasattr(window, "_notices_btn"):
            from .main_window import _PAGE_NOTICES

            window._nav_to_page(_PAGE_NOTICES)
            return
        from .dialogs import NoticesDialog

        notices = list(getattr(self, "_active_notices", []) or [])
        dlg = NoticesDialog(self, notices)
        dlg.exec()

    @staticmethod
    def _default_software_name(names) -> str:
        """The stock/original row in a model's software list.

        Every model defaults to its Original Software entry, not whatever the
        manifest happens to list first: switching models must not silently
        browse another firmware family. If a catalogue has no such entry, its
        first row is the only sensible fallback.
        """
        cleaned = [str(name) for name in (names or []) if str(name).strip()]
        for name in cleaned:
            if name.strip().casefold() == "original software":
                return name
        for name in cleaned:
            if "original" in name.casefold():
                return name
        return cleaned[0] if cleaned else ""

    @staticmethod
    def _software_name_kept_across_models(previous, names) -> str:
        """The new model's spelling of ``previous``, when it lists that name.

        Software rows are matched by canonical name: the same name on another
        model is the equivalent choice, and the new model's package is then
        resolved normally. An empty result means the caller should fall back
        to :meth:`_default_software_name`.
        """
        wanted = (previous or "").strip().casefold()
        if not wanted:
            return ""
        for name in names or []:
            if str(name).strip().casefold() == wanted:
                return str(name)
        return ""

    def _populate_software_combo(self, names, select=None) -> None:
        """Fill the Software dropdown with localised labels.

        The canonical English name stays as the item's ``Qt.UserRole`` data:
        catalogue lookups and device tracking key off it, and only the label
        is translated (see :func:`display_software_name`). ``select`` is a
        canonical name to restore after the rebuild; when it is absent the
        model's Original Software row is selected.
        """
        combo = self._software_combo
        combo.blockSignals(True)
        combo.clear()
        for name in names:
            combo.addItem(display_software_name(name), name)
        idx = self._find_software_index(select) if select is not None else -1
        if idx < 0:
            idx = self._find_software_index(self._default_software_name(names))
        if idx >= 0:
            combo.setCurrentIndex(idx)
        combo.blockSignals(False)

    def _find_software_index(self, name) -> int:
        """Row for a canonical or displayed software name, or -1."""
        combo = self._software_combo
        target = (name or "").strip()
        for i in range(combo.count()):
            if str(combo.itemData(i) or "") == target or combo.itemText(i) == target:
                return i
        localized = display_software_name(target)
        for i in range(combo.count()):
            if combo.itemText(i) == localized:
                return i
        return -1

    def _update_device_status_copy(self):
        model = self.current_model()
        eff_type = self._selected_type if model.upper() == "Y1" else None
        lbl = device_label_for_model(model, eff_type)
        if hasattr(self, "_install_btn"):
            self._install_btn.setToolTip(tr("sel_install_tooltip").format(lbl=lbl))

    def is_rockbox_y1_browsing(self) -> bool:
        """True when the Rockbox package for Y1 is the one being browsed."""
        software = (self.current_software() or "").lower()
        return self.current_model().upper() == "Y1" and "rockbox" in software

    def is_rockbox_y2_browsing(self) -> bool:
        """True when the Rockbox package for Y2 is the one being browsed."""
        software = (self.current_software() or "").lower()
        return self.current_model().upper() == "Y2" and "rockbox" in software

    def original_pre_307_listing(self) -> bool:
        """Whether Original Software releases older than 3.0.7 may be listed.

        Unlike the Rockbox filters this is not gated by Advanced Settings: the
        pre-3.0.7 hide is the default, and only the explicit opt-in lifts it.
        """
        if not device_tracking.advanced_settings_enabled():
            return False
        return bool(device_tracking.rockbox_release_filters().original_pre_307)

    def release_listing_filters(self) -> tuple:
        """``(show_old, show_nightly, prefer_240p)`` for the current browse.

        The Settings toggles only take effect for Rockbox releases on Y1.
        Gated behind Advanced Settings to prevent installer errors for standard users.
        """
        if not device_tracking.advanced_settings_enabled():
            return (False, False, False)
        if not self.is_rockbox_y1_browsing():
            return (False, False, False)
        if self.is_rockbox_y2_browsing():
            return (False, False, False)
        filters = device_tracking.rockbox_release_filters()
        return (
            bool(filters.old_rockbox),
            bool(filters.nightly),
            bool(filters.rockbox_240p),
        )

    def refresh_release_filters(self):
        """Re-list releases after the Settings filters changed."""
        self._refresh_releases()

    def _on_type_changed(self, index):
        self._selected_type = self._type_combo.currentData()
        self._refresh_releases()
        self._sync_notice_context()
        self._update_keep_data_note()

    def _on_software_changed(self):
        self._refresh_releases()
        self._sync_notice_context()
        self._update_keep_data_note()

    def _update_keep_data_note(self) -> None:
        """Say in advance when the player's current firmware blocks Keep Data.

        The catalogue already knows both firmware families, so the refusal is
        certain before the device is connected. It belongs beside the Software
        choice, not only on the install card after the player is plugged in.
        """
        note = getattr(self, "_keep_data_note", None)
        if note is None:
            return
        try:
            from .. import keep_data

            blocked = keep_data.incompatible_with_current(
                self.current_model(),
                self.current_package(),
                getattr(self, "settings", None),
                hardware_type=self.current_hardware_type(),
            )
        except Exception:
            logger.debug("Could not evaluate the Keep Data note", exc_info=True)
            blocked = False
        if note.isHidden() == (not blocked):
            return
        note.setVisible(bool(blocked))
        self.content_size_changed.emit()

    def _sync_notice_context(self) -> None:
        """Report what the reader is looking at, so notices can be contextual.

        The model, hardware revision and the selected firmware's repository
        and release tag narrow which notice the card offers. A selection
        change is exactly when a targeted notice becomes worth surfacing.
        """
        try:
            from ..notice_service import NoticeService

            package = self.current_package()
            rel = getattr(self, "_current_selected_rel", None) or {}
            NoticeService.instance().set_context(
                model=self.current_model(),
                device_type=self.current_hardware_type(),
                firmware_repo=str(getattr(package, "repo", "") or ""),
                firmware_version=str(rel.get("tag_name") or ""),
            )
        except Exception:
            logger.debug("Could not report the notice context", exc_info=True)

    def _refresh_releases(self, force_refresh=False, *, refresh_manifest=True):
        # The manifest defines which models and software exist, so a refresh
        # fetches it before anything below can bail out on an empty or stale
        # selection: that is exactly the state this button must repair.
        if force_refresh and refresh_manifest:
            self._refresh_manifest()
        package = self.current_package()
        # A fresh listing has no selected release yet. Drop the old release
        # details so a slow worker can never let the previous model's package
        # reach the install card or a flash while the new list is loading.
        self._current_selected_rel = None
        self._details_package = None
        self._listed_releases = []
        self._release_list.clear()
        self._install_btn.setEnabled(False)
        if package is None:
            self._set_online_banner("sel_no_release")
            return
        old = self._releases_worker
        if old is not None and old.isRunning():
            try:
                old.finished.disconnect()
            except Exception:
                pass
            old.requestInterruption()
            old.wait(100)

        self._set_online_banner("sel_loading")
        loading_item = QListWidgetItem(tr("sel_loading"))
        loading_item.setFlags(loading_item.flags() & ~Qt.ItemIsEnabled)
        self._release_list.addItem(loading_item)
        self._fit_release_list_height()
        self._notes.setHtml(
            f"<p style='color: palette(window-text); opacity: 0.7;'><i>{tr('release_notes_loading')}</i></p>"
        )
        show_old, show_nightly, prefer_240p = self.release_listing_filters()
        worker = ReleasesWorker(
            self.client,
            package,
            self.current_model(),
            show_nightly=show_nightly,
            selected_type=self._selected_type,
            show_old_rockbox=show_old,
            show_original_pre_307=self.original_pre_307_listing(),
            # Selecting old Rockbox or the pre-3.0.7 Original build turns on the
            # listing of safety-blocked releases for that choice.
            show_safety_blocked=bool(show_old or self.original_pre_307_listing()),
            prefer_240p=prefer_240p,
            force_refresh=force_refresh,
            parent=self,
        )
        self._releases_worker = worker
        self._active_workers.add(worker)
        worker.finished.connect(lambda rels, err: self._on_releases_worker_finished(worker, rels, err))
        worker.start()

    def _on_releases_worker_finished(self, worker, releases, error):
        self._active_workers.discard(worker)
        if worker is self._releases_worker:
            self._on_releases_loaded(releases, error)

    def _refresh_manifest(self):
        try:
            from ..manifest import ManifestWorker
            old = getattr(self, "_manifest_worker", None)
            if old is not None and old.isRunning():
                try:
                    old.finished.disconnect()
                except Exception:
                    pass
                old.requestInterruption()
                old.wait(100)
            worker = ManifestWorker(self, force_refresh=True)
            self._manifest_worker = worker
            self._active_workers.add(worker)
            worker.finished.connect(lambda entries: self._on_manifest_worker_finished(worker, entries))
            self._manifest_throbber.setVisible(True)
            worker.start()
        except Exception as e:
            self._manifest_throbber.setVisible(False)
            logger.debug("Background manifest refresh failed: %s", e)

    def _on_manifest_worker_finished(self, worker, entries):
        self._active_workers.discard(worker)
        if worker is getattr(self, "_manifest_worker", None):
            self._manifest_worker = None
            self._manifest_throbber.setVisible(False)
            self._on_manifest_refreshed(entries)

    def _on_manifest_refreshed(self, entries):
        """Adopt a refreshed manifest into the drop-downs and the listing.

        A new manifest can add, retire or newly brand a model, and a release
        published under software that was already listed changes nothing in
        the manifest at all. So the drop-downs are always rebuilt and the
        visible listing is re-fetched (selection and scroll preserved), which
        is what makes a new firmware appear without the reader pressing
        Refresh.
        """
        if not entries:
            return
        # A retired model resets the drop-down to its default row.
        self.refresh_models()
        current_sw = self.current_software()
        names = catalog.software_names_for_model(
            self.current_model(), brand=self.current_brand()
        )
        combo = self._software_combo
        existing = [
            str(combo.itemData(i) or combo.itemText(i)) for i in range(combo.count())
        ]
        if names != existing:
            # Keep the user's software choice when the new list still has it;
            # a name that disappeared falls back to Original Software.
            self._populate_software_combo(names, select=current_sw)
        if self.current_package() is None:
            self._refresh_releases()
        elif self._can_relist_quietly():
            self._refresh_releases_keep_selection(
                force_refresh=bool(self._online_available)
            )

    def _can_relist_quietly(self) -> bool:
        """True when a passive refresh may replace the release list on screen."""
        if not self.isVisible():
            return False
        win = self.window()
        if hasattr(win, "_install_run_active") and win._install_run_active():
            return False
        if getattr(self, "_prep_worker", None) is not None:
            return False
        if getattr(self, "_download_worker", None) is not None:
            return False
        worker = getattr(self, "_releases_worker", None)
        return worker is None or not worker.isRunning()

    def _refresh_releases_keep_selection(self, force_refresh=False):
        """Re-list releases after a refresh, keeping the chosen release and scroll."""
        package = self.current_package()
        if package is None:
            return
        selected = self._release_list.currentItem()
        selected_tag = ""
        if selected is not None:
            rel = selected.data(Qt.UserRole) or {}
            selected_tag = rel.get("tag_name", "") or ""
        scroll = self._release_list.verticalScrollBar().value()
        self._target_tag_to_select = selected_tag or None
        self._scroll_to_restore = scroll
        # The caller already refreshed the manifest; re-fetching it here would
        # start another catalogue worker from every catalogue result.
        self._refresh_releases(force_refresh=force_refresh, refresh_manifest=False)

    def _release_row_height(self) -> int:
        """Height of one release row, from a real row or the list font."""
        if self._release_list.count():
            return max(1, self._release_list.sizeHintForRow(0))
        return self._release_list.fontMetrics().lineSpacing() + 8

    def minimum_install_height(self) -> int:
        """Least card height that still shows two releases and both actions.

        Two release rows, the Install / Restore button, and the
        Install-from-file link below it — the window is never allowed to be
        dragged shorter than this, so the primary actions stay reachable
        whatever else is on the page.
        """
        rows = max(1, self._release_row_height()) * 2
        list_h = rows + 2 * self._release_list.frameWidth() + 2
        btn_h = max(32, int(self._install_btn.minimumHeight() or 32))
        link_h = max(1, self._install_from_file_link.sizeHint().height())
        spacing = self._pkg_group.layout().spacing() if self._pkg_group.layout() is not None else 6
        margins = 0
        layout = self._pkg_group.layout()
        if layout is not None:
            margins = layout.contentsMargins().top() + layout.contentsMargins().bottom()
        return list_h + btn_h + link_h + margins + spacing * 3

    def _max_visible_rows(self) -> int:
        """Gradually scale visible release list items up on larger screens."""
        screen = self.screen()
        if screen is not None:
            avail_h = screen.availableGeometry().height()
            if avail_h >= 1000:
                return 7
            elif avail_h >= 880:
                return 6
            elif avail_h >= 800:
                return 5
        return 4

    def _fit_release_list_height(self):
        """Size the release list to its contents (capped, responsive to display height)."""
        count = self._release_list.count()
        max_rows = self._max_visible_rows()
        if count:
            rows = min(count, max_rows)
            height = sum(
                max(1, self._release_list.sizeHintForRow(i)) for i in range(rows)
            )
        else:
            rows = EMPTY_RELEASE_LIST_ROWS
            height = rows * self._release_row_height()
        frame = 2 * self._release_list.frameWidth() + 2
        height += frame
        # The list may shrink to two rows — the floor ``minimum_install_height``
        # promises, which keeps Install / Restore and the file link on screen —
        # and scroll inside itself for the rest. Pinning min = max forced the
        # page to the tallest list, so a window that could not take that height
        # clipped the card instead of giving the rows a scrollbar.
        floor_rows = min(rows, 2)
        if count:
            floor_h = sum(
                max(1, self._release_list.sizeHintForRow(i)) for i in range(floor_rows)
            )
        else:
            floor_h = floor_rows * self._release_row_height()
        self._release_list.setMinimumHeight(max(1, floor_h + frame))
        self._release_list.setMaximumHeight(height)
        self._release_list.updateGeometry()

    def content_height_hint(self) -> int:
        """Height the page needs to show its contents without compression or text clipping.

        The page itself is a scroll area, so its own hint only describes the
        viewport. The window sizes itself from the *content* inside it, and the
        scroll area only comes into play when that content is taller than the
        window may grow.
        """
        content = getattr(self, "_scroll_content", None)
        page_layout = content.layout() if content is not None else self.layout()
        if page_layout is not None:
            self._invalidate_layout_tree(page_layout)
            page_layout.activate()
        if content is not None:
            hint_h = max(content.sizeHint().height(), content.minimumSizeHint().height())
        else:
            hint_h = max(self.sizeHint().height(), self.minimumSizeHint().height())

        # Ensure release notes card fits header + body + translate notes + see more without overlap
        if hasattr(self, "_notes_group") and self._notes_group.isVisible():
            header_h = self._details_header.sizeHint().height() if hasattr(self, "_details_header") and self._details_header.isVisible() else 0
            notes_h = max(50, self._notes.sizeHint().height()) if hasattr(self, "_notes") else 60
            card_w = max(160, (self._notes_group.width() - 28) if self._notes_group.width() > 28 else 200)
            trans_h = 0
            if hasattr(self, "_translate_label") and self._translate_label.isVisible():
                trans_h = max(self._translate_label.sizeHint().height(), self._translate_label.heightForWidth(card_w), 24)
                self._translate_label.setMinimumHeight(trans_h)
            see_more_h = self._see_more_label.sizeHint().height() if hasattr(self, "_see_more_label") and self._see_more_label.isVisible() else 0
            # Card contents margins (top 12, bottom 12) + spacing between rows
            notes_needed = header_h + notes_h + trans_h + see_more_h + 36
            if notes_needed > 0:
                self._notes_group.setMinimumHeight(notes_needed)
                if hasattr(self, "_pkg_group"):
                    self._pkg_group.setMinimumHeight(notes_needed)
                if page_layout is not None:
                    self._invalidate_layout_tree(page_layout)
                    page_layout.activate()
                hint_h = max(hint_h, notes_needed + 140)
        return hint_h

    def content_width_hint(self) -> int:
        """Width the two cards need side by side, so neither is cut off.

        The page is a scroll area, so its own hint describes the *viewport*
        rather than the cards inside it. Without this the window kept a width
        narrower than the split needs: the notes card ran past the right edge
        and a horizontal bar appeared under the cards.

        The preferred width is asked for, not the minimum: at the minimum the
        two columns are squeezed until the release note wraps in a sliver. A
        note reports an ideal width that grows with its text, so it is capped
        — past that the note wraps instead of widening the window.
        """
        content = getattr(self, "_scroll_content", None)
        if content is None:
            return int(self.minimumSizeHint().width())
        layout = content.layout()
        if layout is not None:
            self._invalidate_layout_tree(layout)
            layout.activate()
        margins = layout.contentsMargins() if layout is not None else None
        padding = (margins.left() + margins.right()) if margins is not None else 0
        frame = 2 * int(self._scroll.frameWidth()) if hasattr(self, "_scroll") else 0
        preferred = max(
            int(content.sizeHint().width()), int(content.minimumSizeHint().width())
        )
        return min(preferred, self.CONTENT_WIDTH_CAP) + padding + frame

    #: Widest the catalogue asks the window to be for its cards.
    CONTENT_WIDTH_CAP = 900

    @staticmethod
    def _invalidate_layout_tree(layout) -> None:
        """Recursively clear cached hint/size data for a layout subtree."""
        try:
            layout.invalidate()
        except Exception:
            return
        try:
            count = layout.count()
        except Exception:
            return
        for i in range(count):
            try:
                item = layout.itemAt(i)
            except Exception:
                continue
            if item is None:
                continue
            try:
                child = item.layout()
            except Exception:
                continue
            if child is None:
                try:
                    widget = item.widget()
                except Exception:
                    continue
                if widget is not None:
                    try:
                        child = widget.layout()
                    except Exception:
                        continue
            if child is not None:
                SelectPackagePage._invalidate_layout_tree(child)

    def _on_releases_loaded(self, releases, error):
        if error:
            self._set_online_banner("sel_offline")
            self._set_update_prompt(self.current_software(), self.current_model(), None)
            self._release_list.clear()
            self._notes.clear()
            self._fit_release_list_height()
            self.content_size_changed.emit()
            # A failed poll is not fatal: the screen already says it is offline,
            # and a status line names what could not be loaded. Nothing here
            # should raise or take the window over.
            toast = getattr(self, "_conn_toast", None)
            try:
                if toast is not None and self.isVisible():
                    toast.show_message(
                        tr("toast_release_notes_unavailable"), icon="\U0001F4E1"
                    )
            except Exception:
                pass
            return
        self._release_list.clear()
        releases = sorted(releases or [], key=catalog.release_sort_key, reverse=True)
        prefer_240p = self.release_listing_filters()[2]
        installed_tag = ""
        installed_published = ""
        install_rec = None
        curr_model = self.current_model() or "Y1"
        software_name = self.current_software() or ""
        try:
            from .. import device_tracking
            install_rec = device_tracking.get_software_install(
                curr_model, software_name, settings=getattr(self, "settings", None),
            )
            if install_rec:
                installed_tag = install_rec.get("tag_name") or ""
                installed_published = install_rec.get("published_at") or ""
        except Exception:
            pass

        self._listed_releases = list(releases)
        ceiling = (install_rec or {}).get("notify_ceiling") or ""
        marks = catalog.classify_release_list(
            releases, installed_tag, installed_published, ceiling,
        )
        prompt = marks.get("prompt")
        if prompt and installed_tag:
            package = None
            try:
                pkgs = catalog.packages_for_model_software(
                    curr_model, software_name, brand=self.current_brand()
                )
                package = pkgs[0] if pkgs else None
            except Exception:
                package = None
            self._set_update_prompt(software_name, curr_model, {
                "tag": prompt.get("newer_tag") or "",
                "release": prompt.get("newer_release"),
                "package": package,
            })
        else:
            self._set_update_prompt(software_name, curr_model, None)

        for rel in releases:
            label = catalog.parse_clean_release_tag_label(rel, prefer_240p=prefer_240p)
            tag = rel.get("tag_name", "")
            row = marks["rows"].get(tag) or {}
            is_installed = bool(row.get("installed"))
            is_newer = bool(row.get("newer"))

            display_label = f"● {label}" if is_installed else label

            item = QListWidgetItem(display_label)
            item.setData(Qt.UserRole, rel)
            font = item.font()
            font.setBold(is_newer and not is_installed)
            item.setFont(font)

            tooltip = self._asset_line(rel)
            eff_tag = catalog.release_effective_tag(rel)
            if eff_tag:
                tooltip = f"{eff_tag}\n{tooltip}".strip()
            elif tag:
                tooltip = f"{tag}\n{tooltip}".strip()
            if is_installed:
                tip = tr("sel_installed_tip")
                tooltip = f"{tip}\n{tooltip}".strip()
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, tip)
            item.setToolTip(tooltip)
            self._release_list.addItem(item)
        if not releases:
            self._set_online_banner("sel_no_release")
            self._notes.clear()
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
            if target_tag:
                selected_item = self._release_list.item(selected_row)
                if selected_item is not None:
                    self._release_list.scrollToItem(selected_item)
            scroll_to = getattr(self, "_scroll_to_restore", None)
            if scroll_to is not None:
                self._scroll_to_restore = None
                self._release_list.verticalScrollBar().setValue(scroll_to)
            self._prefetch_release_icons(releases)
            if self._install_btn.isEnabled():
                self._install_btn.setDefault(True)
                self._install_btn.setFocus()
            if getattr(self, "_auto_install_tag", None):
                target_auto = self._auto_install_tag
                self._auto_install_tag = None
                for row in range(self._release_list.count()):
                    it = self._release_list.item(row)
                    r = it.data(Qt.UserRole) if it else None
                    if r and (target_auto in ("latest", "") or r.get("tag_name") == target_auto):
                        self._release_list.setCurrentRow(row)
                        self._trigger_release_install(r)
                        break
            self._fit_release_list_height()
            self.updateGeometry()
            self.content_size_changed.emit()

    def _asset_line(self, rel):
        assets = ", ".join(
            v["asset"].get("name", "") for v in (rel.get("rom_variants") or [])
        )
        return assets or ""

    def _on_release_selected(self, current, _prev):
        self._install_btn.setEnabled(current is not None)
        if current is None:
            self._current_selected_rel = None
        if current is not None:
            self._install_btn.setDefault(True)
        if current is None:
            self._current_selected_rel = None
            self._details_package = None
            self._is_translated = False
            self._is_translating = False
            self._notes.clear()
            self._notes.updateGeometry()
            self._set_details_header(None, None)
            self._update_translate_link()
            self._sync_notice_context()
            self._emit_gui_firmware_changed()
            return
        rel = current.data(Qt.UserRole)
        if rel:
            if getattr(self, "_gui_explicit", None) and self._gui_explicit.get("kind") != "local":
                self._gui_explicit = None
                self._gui_previous_identity = None
            self._current_selected_rel = rel
            self._details_package = self._package_for_selection()
            self._is_translated = False
            self._is_translating = False
            self._set_notes_content(self._render_release_notes(rel, as_html=True))
            self._set_details_header(rel, self._details_package)
            listed = getattr(self, "_listed_releases", None) or [rel]
            self._start_icon_job(listed, priority_tag=str(rel.get("tag_name") or ""))
            if self._auto_translate_notes and not is_english_language():
                self._translate_current_release_in_app()
            self._update_translate_link()
            # The selected release is now part of the notice context, so a
            # notice about this firmware tag can take the card over.
            self._sync_notice_context()
            self._emit_gui_firmware_changed()

    def _render_release_notes(self, rel, translated_body=None, translated_name=None, as_html: bool = False) -> str:
        from ..translate import strip_changes_header
        raw_body = translated_body or without_github_only(rel.get("body") or "").strip()
        body = strip_changes_header(raw_body) or tr("update_no_notes")
        # No translation badge inside the notes: the control under them already
        # says the text was translated, and where to show the original.

        # 1. Normalise markdown: turn any markdown headings #{1,6} into bold text
        # so all text is rendered at uniform body text size.
        cleaned_md = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"**\1**\n", body[:4000])

        # Strip all markdown images ![alt](url)
        cleaned_md = re.sub(r"!\[.*?\]\(.*?\)", "", cleaned_md)
        # Strip HTML images <img ...>
        cleaned_md = re.sub(r"<img[^>]*>", "", cleaned_md, flags=re.IGNORECASE)
        # Strip empty link anchors left over from image-only links: [ ](url)
        cleaned_md = re.sub(r"\[\s*\]\(.*?\)", "", cleaned_md)
        # Normalise HTML headings <h1>-<h6> to bold paragraphs
        cleaned_md = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"<p><b>\1</b></p>", cleaned_md, flags=re.IGNORECASE)

        if not as_html:
            return cleaned_md

        doc = QTextDocument()
        doc.setMarkdown(rewrite_legacy_discord_invite(cleaned_md))
        html = doc.toHtml()
        # Qt's markdown export paints a solid paper colour on the body. The
        # details pane sits on the card, which sits on the system material.
        html = re.sub(
            r"background(?:-color)?:\s*[^;\"']+;?",
            "",
            html,
            flags=re.IGNORECASE,
        )
        # ...and it writes the document default foreground into every span
        # (``color: #000000``). Inline declarations beat the stylesheet below,
        # which is why the notes looked black in a dark theme and never
        # followed a live theme change. The theme colour is applied by the CSS
        # that is injected next, so the stale inline colours go.
        html = re.sub(r"color:\s*[^;\"']+;?", "", html, flags=re.IGNORECASE)

        t = T()
        # 2. Re-style links: clickable, bold, same color as text (not blue #0000ff):
        html = re.sub(
            r'<span style="[^"]*color:#0000ff;[^"]*">',
            f'<span style="color: {t.fg}; font-weight: bold; text-decoration: none;">',
            html,
        )
        html = re.sub(
            r'<a\s+href="([^"]+)">',
            f'<a href="\\1" style="color: {t.fg}; font-weight: bold; text-decoration: none;">',
            html,
        )
        html = re.sub(r"<img[^>]*>", "", html, flags=re.IGNORECASE)
        # Normalise all inline font-size declarations so no differences in text size exist
        html = re.sub(r"font-size:[^;\"'>]+;?", "font-size: 13px;", html)

        # 3. Inject CSS ensuring uniform 13px font size across all elements:
        css = f"""
        <style type="text/css">
        body, p, span, div, li, b, strong, em, i, a, code, pre, h1, h2, h3, h4, h5, h6 {{
            color: {t.fg};
            font-size: 13px;
            line-height: 1.45;
        }}
        body {{
            margin: 0;
            padding: 0;
        }}
        h1, h2, h3, h4, h5, h6 {{
            font-weight: 700;
            margin-top: 4px;
            margin-bottom: 4px;
            font-size: 13px;
        }}
        p {{
            margin-top: 4px;
            margin-bottom: 6px;
            color: {t.fg};
            font-size: 13px;
        }}
        ul, ol {{
            margin-top: 4px;
            margin-bottom: 6px;
            padding-left: 18px;
        }}
        li {{
            margin-bottom: 2px;
            color: {t.fg};
            font-size: 13px;
        }}
        code, pre {{
            font-family: Menlo, Monaco, Consolas, "Cascadia Code", "Courier New", monospace;
            font-size: 13px;
            background-color: {t.bg_hover};
            color: {t.fg};
            padding: 2px 4px;
            border-radius: 3px;
        }}
        a {{
            color: {t.fg};
            font-weight: bold;
            text-decoration: none;
            font-size: 13px;
        }}
        </style>
        """
        if "<head>" in html:
            html = html.replace("<head>", f"<head>{css}")
        else:
            html = f"<html><head>{css}</head><body>{html}</body></html>"
        return html

    def showEvent(self, event):
        super().showEvent(event)
        # The page content sits in a scroll area, so its children only get real
        # geometry once that content widget has been sized. A page that was
        # built while another stage was showing comes back with placeholder
        # geometry until the first layout pass; force it before painting.
        content = getattr(self, "_scroll_content", None)
        layout = content.layout() if content is not None else None
        if layout is not None:
            layout.activate()
        # A session that started with no reachable archive says so here, once.
        self._maybe_show_archive_notice()
        # The tracked install changes when an install finishes; the note is
        # about that record as much as about the dropdown.
        self._update_keep_data_note()

    def _update_see_more_link(self):
        rel = self._current_selected_rel
        url = str((rel or {}).get("html_url") or "") if rel else ""
        # English only: outside English the line under the notes already opens
        # this same release page in Google Translate ("View in browser"), so a
        # second link to it would offer the identical action twice.
        lang = (translator().lang or "en").strip()
        if not url or not is_english_language(lang):
            self._see_more_label.setVisible(False)
            self._see_more_label.setText("")
            return
        # See More belongs on our own release-notes page: it reads this
        # release's title and notes from GitHub and shows them with the site's
        # styling and its own Get it in Updater / Download Portable Zip
        # buttons. github.com is only the fallback for a release the site
        # cannot name.
        from .. import docs_source

        repo = str((rel or {}).get("source_repo") or "") or docs_source.repo_from_url(url)
        tag = str((rel or {}).get("tag_name") or "")
        model = self.current_model() if hasattr(self, "current_model") else ""
        target = (
            docs_source.release_notes_url(
                repo, tag, name=str((rel or {}).get("name") or ""), model=model
            )
            or url
        )
        t = T()
        # The release page's own words are not translated, but this link label
        # belongs to the app UI and must follow the selected language.
        self._see_more_label.setText(
            f'<a href="{target}" style="color:{t.fg}; text-decoration:none; font-weight:700;">{tr("notices_see_more")}</a>'
        )
        self._see_more_label.setVisible(True)

    def _on_see_more_clicked(self, link: str):
        from ..browser import open_browser
        open_browser(link)

    def _update_translate_link(self):
        rel = self._current_selected_rel
        lang = (translator().lang or "en").strip()
        self._update_see_more_link()
        if not rel or is_english_language(lang):
            self._translate_label.setVisible(False)
            self._translate_label.setText("")
            self._translate_label.updateGeometry()
            self.content_size_changed.emit()
            return

        t = T()
        url = get_google_translate_release_url(rel, lang)
        self._translate_label.setVisible(True)
        # When every provider is on cooldown the control is dimmed and its hint
        # (hover, or a click that pins it) explains why. It is not a dialog:
        # a dead link is a small detail, not a decision the reader must make.
        try:
            from ..translate import is_translation_available

            translation_ready = is_translation_available()
        except Exception:
            translation_ready = True

        if self._is_translating:
            msg = tr("translating_notes")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'<span style="color:{t.fg};">{msg} &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg}; font-weight:700; text-decoration:none;">{open_txt} ↗</a></span>'
            )
        elif self._is_translated:
            badge = tr("translated_with_google")
            show_orig = tr("show_original")
            open_txt = tr("open_in_google_translate")
            self._translate_label.setText(
                f'<span style="color:{t.fg};">{badge}</span> &nbsp;·&nbsp; '
                f'<a href="action:show_original" style="color:{t.fg}; font-weight:700; text-decoration:none;">{show_orig}</a> &nbsp;·&nbsp; '
                f'<a href="{url}" style="color:{t.fg}; font-weight:700; text-decoration:none;">{open_txt} ↗</a>'
            )
        else:
            trans_txt = tr("translate_notes")
            open_txt = tr("open_in_google_translate")
            try:
                from .icons import get_symbol_data_uri
                globe_uri = get_symbol_data_uri("translate", 12, match_accent=False)
                globe_html = f'<img src="{globe_uri}" width="12" height="12" style="vertical-align: -1px;"> '
            except Exception:
                globe_html = ""
            if translation_ready:
                self._translate_label.setText(
                    f'{globe_html}<a href="action:translate_in_app" style="color:{t.fg}; text-decoration:none; font-weight:700;">{trans_txt}</a> &nbsp;·&nbsp; '
                    f'<a href="{url}" style="color:{t.fg}; text-decoration:none; font-weight:700;">{open_txt} ↗</a>'
                )
            else:
                # Same words, no link: disabled by the palette, with the pin hint.
                self._translate_label.setText(
                    f'<span style="color:{t.fg};">{globe_html}{trans_txt}</span> &nbsp;·&nbsp; '
                    f'<a href="{url}" style="color:{t.fg}; text-decoration:none; font-weight:700;">{open_txt} ↗</a>'
                )
        from .widgets import install_pinned_hint

        if translation_ready:
            self._translate_label.setEnabled(True)
            install_pinned_hint(self._translate_label, "")
        else:
            self._translate_label.setEnabled(False)
            install_pinned_hint(self._translate_label, tr("translate_unavailable_hint"))
        card_w = max(160, (self._notes_group.width() - 28) if hasattr(self, "_notes_group") and self._notes_group.width() > 28 else 200)
        needed_h = max(self._translate_label.sizeHint().height(), self._translate_label.heightForWidth(card_w), 24)
        self._translate_label.setMinimumHeight(needed_h)
        self._translate_label.updateGeometry()
        self.content_size_changed.emit()

    def _on_translate_link_clicked(self, link: str):
        if link == "action:translate_in_app":
            self._auto_translate_notes = True
            self._translate_current_release_in_app()
        elif link == "action:show_original":
            self._show_original_release_notes()
        elif link.startswith(("http://", "https://")):
            open_browser(link)

    def _on_notes_link_clicked(self, qurl_or_str):
        url_str = qurl_or_str.toString() if hasattr(qurl_or_str, "toString") else str(qurl_or_str)
        if not url_str:
            return
        if url_str == "action:translate_in_app":
            self._auto_translate_notes = True
            self._translate_current_release_in_app()
        elif url_str == "action:show_original":
            self._show_original_release_notes()
        elif url_str.startswith(("http://", "https://", "mailto:")):
            open_browser(url_str)

    def set_auto_translate_notes(self, enabled: bool):
        """Translate release notes from now on, starting with what is on screen.

        Called when the app language changes, so a reader who switches to
        another language sees the visible release notes in that language
        immediately — no extra click — and every release they select afterwards
        is translated too.
        """
        self._auto_translate_notes = bool(enabled)
        lang = (translator().lang or "en").strip()
        if not enabled or is_english_language(lang):
            # Switching back to English (or turning translation off) puts the
            # published notes back, so the very same change that turned it on
            # can turn it off.
            if self._is_translated or self._is_translating:
                self._show_original_release_notes()
            return
        self._translate_current_release_in_app()

    def _note_translation_unavailable(self) -> None:
        """Explain an unavailable translation service once, and on the control."""
        self._is_translating = False
        self._update_translate_link()
        if getattr(self, "_translation_notice_posted", False):
            return
        self._translation_notice_posted = True
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().post_local_notice(
                "translation-unavailable",
                tr("translate_unavailable_notice_title"),
                tr("translate_unavailable_notice_body"),
                severity="warning",
            )
        except Exception:
            pass

    def _translate_current_release_in_app(self):
        rel = self._current_selected_rel
        if not rel:
            return
        lang = (translator().lang or "en").strip()
        if is_english_language(lang):
            return
        try:
            from ..translate import is_translation_available
        except Exception:
            is_translation_available = None
        # Only the import is defensive: if the availability check or the
        # explanation itself fails we still want the error, not a worker that
        # silently sits there and dies.
        if is_translation_available is not None and not is_translation_available():
            # Every provider is on cooldown: do not start a worker that would
            # sit there and fail, just say so and dim the control.
            self._note_translation_unavailable()
            return
        tag = rel.get("tag_name", "")
        cached = self._translated_notes_cache.get((tag, lang))
        if cached:
            trans_name, trans_body = cached
            self._is_translated = True
            self._is_translating = False
            self._set_notes_content(
                self._render_release_notes(rel, translated_body=trans_body, translated_name=trans_name, as_html=True)
            )
            self._update_translate_link()
            return

        self._is_translating = True
        self._update_translate_link()
        if self._translate_worker is not None and self._translate_worker.isRunning():
            self._translate_worker.wait(500)
            # A wait that times out leaves the thread running; keeping the
            # reference stops Qt destroying it (which aborts the process).
            retire_worker(self._translate_worker, self._active_workers)
        self._translate_worker = ReleaseTranslateWorker(rel, lang, self)
        self._translate_worker.translation_ready.connect(self._on_translation_ready)
        self._translate_worker.translation_failed.connect(self._on_translation_failed)
        self._translate_worker.start()

    def _on_translation_ready(self, rel, lang, trans_name, trans_body):
        self._translated_notes_cache[(rel.get("tag_name", ""), lang)] = (trans_name, trans_body)
        # A provider answered again: the local notice has served its purpose.
        try:
            from ..notice_service import NoticeService

            NoticeService.instance().remove_local_notice("translation-unavailable")
            self._translation_notice_posted = False
        except Exception:
            pass
        cur = self._current_selected_rel
        if cur == rel or (cur and cur.get("tag_name") == rel.get("tag_name")):
            self._is_translating = False
            self._is_translated = True
            self._set_notes_content(
                self._render_release_notes(rel, translated_body=trans_body, translated_name=trans_name, as_html=True)
            )
            self._update_translate_link()

    def _on_translation_failed(self, rel, lang, error):
        cur = self._current_selected_rel
        if cur == rel or (cur and cur.get("tag_name") == rel.get("tag_name")):
            self._is_translating = False
            self._note_translation_unavailable()

    def _show_original_release_notes(self):
        self._is_translated = False
        self._is_translating = False
        self._auto_translate_notes = False
        if self._current_selected_rel:
            self._set_notes_content(self._render_release_notes(self._current_selected_rel, as_html=True))
        self._update_translate_link()

    def _trigger_release_install(self, rel, package=None, *, prompt=True):
        if not rel or not rel.get("download_url"):
            return
        pkg = package or self.current_package()
        if pkg is None:
            return
        self._details_package = pkg

        # Determine model and variant from original asset name / download URL:
        orig_name = rel.get("asset_name") or rel.get("download_url") or ""
        det_m, det_t = detect_model_and_type_from_name(orig_name)
        eff_model = det_m or getattr(pkg, "device", "") or self.current_model()
        eff_type = det_t or self._selected_type

        # Pre-install disconnect / power-off confirmation popup (skipped for SP Flash Tool on Linux):
        if prompt and self._should_prompt_pre_install():
            if not self._prompt_pre_install(eff_model, eff_type):
                return

        dest_dir = downloads.downloads_dir()
        apk_repo = str(rel.get("apk_repo") or getattr(pkg, "apk_repo", "") or "")
        apk_url = str(rel.get("apk_url") or "") if apk_repo else ""
        apk_name = Path(str(rel.get("apk_name") or "app.apk")).name
        apk_dest = dest_dir / f"{pkg.slug}_{rel.get('tag_name', 'latest')}_{apk_name}" if apk_url else ""
        self._current_package_model = eff_model
        self._pending_install_release_info = {
            "model": eff_model,
            "software_name": pkg.name,
            "package_slug": pkg.slug,
            "tag_name": rel.get("tag_name", ""),
            "release_label": catalog.format_release_display_label(rel),
            "type_variant": eff_type,
            "hardware_type": eff_type,
            "published_at": rel.get("published_at", ""),
            "apk_repo": apk_repo,
            "apk_url": apk_url,
            "apk_path": str(apk_dest) if apk_dest else "",
        }
        fname = Path(rel.get("asset_name") or "rom.zip").name
        dest = dest_dir / f"{pkg.slug}_{rel.get('tag_name', 'latest')}_{fname}"
        extract_dir = compute_extract_dir(dest)

        # New firmware install confirmed: stop any active download/extraction and
        # remove old scatter files / working directories from previous installs.
        self._stop_transfer_workers()
        from ..flash_service import cleanup_previous_install_scatters

        cleanup_previous_install_scatters(keep_package_path=dest, for_new_install=True)

        # 1. Reuse the extracted firmware. The archive is deleted once it is
        # extracted, so a finished extract counts even when the zip is gone.
        if _is_extract_complete(extract_dir):
            logger.info("Package %s already cached and extracted at %s, reusing immediately", dest.name, extract_dir)
            self._current_package_path = str(dest)
            self._current_package_name = self._install_card_title(pkg.name, rel, eff_model)
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._download_status_key = "sel_prepare_done"
            self._on_online_prep_done(True, str(extract_dir), "")
            return

        # 2. Check if package is already downloaded but needs extraction:
        if dest.is_file() and dest.stat().st_size > 0:
            logger.info("Package %s already downloaded, extracting directly", dest.name)
            self._current_package_path = str(dest)
            self._current_package_name = self._install_card_title(pkg.name, rel, eff_model)
            self._current_package_model = eff_model
            self._current_installed_release_info = self._pending_install_release_info
            self._prepare_package(str(dest), self._on_online_prep_done)
            return

        # 3. Otherwise, start download worker. Package progress is on the install card.
        self._current_package_path = str(dest)
        self._current_package_name = self._install_card_title(pkg.name, rel, eff_model)
        self._current_package_model = eff_model
        self._current_installed_release_info = self._pending_install_release_info
        self._current_selected_rel = rel
        self._last_download_percent = 0
        self._last_download_status_text = ""
        self._download_status_key = "sel_download_start"
        self._install_btn.setEnabled(False)
        display_name = self._install_card_title(pkg.name, rel, eff_model)
        self.download_started.emit(display_name, eff_model)
        self._gui_handoff_worker = False
        from .. import portable

        self._download_worker = downloads.DownloadWorker(
            rel["download_url"],
            str(dest),
            fallback_urls=portable.fallback_urls_for(rel["download_url"]),
        )
        self._active_workers.add(self._download_worker)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.status.connect(self._on_download_status)
        self._download_worker.finished.connect(self._on_download_done)
        self._download_worker.start()

    def _release_cache_paths(self, rel, package=None):
        """The archive and extracted folder one release is installed from."""
        if not rel:
            return None, None
        pkg = package or getattr(self, "_details_package", None) or self.current_package()
        if pkg is None:
            return None, None
        fname = Path(str(rel.get("asset_name") or "rom.zip")).name
        dest = downloads.downloads_dir() / (
            f"{pkg.slug}_{rel.get('tag_name', 'latest')}_{fname}"
        )
        return dest, compute_extract_dir(dest)

    def purge_release_cache(self, rel=None, package=None) -> list:
        """Delete one release's archive and extracted folder.

        Called only when the user leaves the install journey, never from a
        failure or retry: the extract is the install's source once the download
        has been disposed of, and repeated retries must keep it.
        """
        import shutil

        rel = rel if rel is not None else getattr(self, "_current_selected_rel", None)
        dest, extract_dir = self._release_cache_paths(rel, package)
        removed = []
        for path in (dest, Path(f"{dest}.part") if dest else None, extract_dir):
            if path is None or not path.exists():
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
                removed.append(str(path))
            except OSError as error:
                logger.debug("Could not purge cached firmware %s: %s", path, error)
        return removed

    def retry_missing_cached_firmware(self, *, prompt: bool = True):
        """Retry with the firmware already on disk.

        A failure never purges this journey's extract: retries are unlimited,
        and once the downloaded archive has been disposed of the extracted
        working directory is the install's only source. It is released only
        when the user leaves the install journey.
        """
        rel = getattr(self, "_current_selected_rel", None)
        if rel is None:
            self._say(tr("sel_cache_retry_select_release"), 12000)
            return
        package = getattr(self, "_details_package", None) or self.current_package()
        dest, extract_dir = self._release_cache_paths(rel, package)
        if (
            dest is not None
            and extract_dir is not None
            and _is_extract_complete(extract_dir)
        ):
            # The files are still good: do not download, purge or re-extract,
            # just hand the same working directory back to the install.
            logger.info("Reusing extracted firmware for retry at %s", extract_dir)
            self._current_package_path = str(dest)
            self._current_installed_release_info = (
                getattr(self, "_current_installed_release_info", None)
                or getattr(self, "_pending_install_release_info", None)
            )
            self._on_online_prep_done(True, str(extract_dir), "")
            return
        self._trigger_release_install(
            rel,
            package=package,
            prompt=prompt,
        )

    def cancel_download(self):
        worker = self._download_worker
        if worker is not None:
            worker.cancel()
            # The thread only notices between chunks, so it outlives this call:
            # dropping the last reference here destroyed a running QThread and
            # aborted the app.
            retire_worker(worker, self._active_workers)
            self._download_worker = None
        prep = self._prep_worker
        if prep is not None:
            prep.cancel()
            retire_worker(prep, self._active_workers)
            self._prep_worker = None
        self._download_bar.setVisible(False)
        self._install_btn.setEnabled(True)
        self.download_cancelled.emit()

    def _on_install(self):
        item = self._release_list.currentItem()
        if item is None:
            return
        self._stop_gui_handoff_workers()
        self.gui_handoff_abandoned.emit()
        rel = item.data(Qt.UserRole)
        # A manual Install is a fresh choice, not the accepted update prompt.
        self._keep_settings_default = False
        self._trigger_release_install(rel)
        self._current_selected_rel = rel

    def _on_download_progress(self, val: int):
        self._last_download_percent = int(val)
        self.download_progress.emit(val, getattr(self, "_last_download_status_text", ""))

    def _on_download_status(self, text: str):
        self._last_download_status_text = text
        self._download_status_key = ""
        self.download_progress.emit(int(getattr(self, "_last_download_percent", 0)), text)

    def _on_download_done(self, ok, result):
        self._download_bar.setVisible(False)
        self.download_finished.emit(ok, str(result))
        if not ok:
            self._download_status_key = ""
            self._say(f"{tr('sel_download_failed')} \u2014 {result}", 15000)
            self._install_btn.setEnabled(True)
            return
        eff_model = getattr(self, "_current_package_model", "") or self.current_model()
        self._current_package_path = result
        self._current_package_name = self._install_card_title(
            self.current_software(),
            getattr(self, "_current_selected_rel", None),
            eff_model,
        )
        self._current_package_model = eff_model
        self._current_installed_release_info = getattr(self, "_pending_install_release_info", None)
        self._details_package = self.current_package()
        self._set_details_header(self._current_selected_rel, self._details_package)
        self._prepare_package(result, self._on_online_prep_done)

    def _stop_transfer_workers(self) -> None:
        """Ensure at most one download or extraction worker is active at a time."""
        worker = self._download_worker
        if worker is not None:
            for sig in (worker.finished, worker.progress, worker.status):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            worker.cancel()
            retire_worker(worker, self._active_workers)
            self._download_worker = None
        prep = self._prep_worker
        if prep is not None:
            for sig in (prep.finished, prep.progress):
                try:
                    sig.disconnect()
                except Exception:
                    pass
            prep.cancel()
            retire_worker(prep, self._active_workers)
            self._prep_worker = None

    def _prepare_package(self, path, done_cb):
        self._stop_transfer_workers()
        self._download_status_key = "sel_preparing"
        self._local_status_key = "sel_preparing"
        self._download_bar.setVisible(False)
        self._local_bar.setVisible(False)
        self._ensure_install_title()
        self.prep_started.emit(
            getattr(self, "_current_package_name", "") or "",
            getattr(self, "_current_package_model", "") or "",
        )
        self._prep_worker = ExtractWorker(path)
        self._active_workers.add(self._prep_worker)
        self._prep_worker.progress.connect(self.prep_progress)
        self._prep_worker.finished.connect(done_cb)
        self._prep_worker.finished.connect(self._on_prep_finished)
        self._prep_worker.start()

    def _on_prep_finished(self, *_args):
        if self._prep_worker is not None:
            retire_worker(self._prep_worker, self._active_workers)
            self._prep_worker = None

    def _multiple_scatters_message(self) -> str:
        """Explain a release that carries more than one scatter it could flash.

        An online release is somebody else's build: the reader cannot repack
        it, so the sentence names the software whose developer has to publish a
        single scatter. A browsed folder keeps the generic wording, because
        choosing a different folder is something the reader can do.
        """
        info = getattr(self, "_current_installed_release_info", None) or {}
        software = str(info.get("software_name") or "").strip()
        if not software:
            try:
                software = str(self.current_software() or "").strip()
            except Exception:
                software = ""
        if not software:
            software = str(getattr(self, "_current_package_name", "") or "").strip()
        if software:
            return tr("sel_multiple_scatters_release_error").format(software=software)
        return tr("sel_multiple_scatters_error")

    def _on_online_prep_done(self, ok, extract_dir, err):
        self._download_bar.setVisible(False)
        if not ok:
            self._download_status_key = ""
            if "MULTIPLE_SCATTERS" in str(err):
                self._say(tr("sel_multiple_scatters_title"), 15000)
                QMessageBox.warning(
                    self,
                    tr("sel_multiple_scatters_title"),
                    self._multiple_scatters_message(),
                )
            else:
                self._say(f"{tr('sel_prepare_failed')} \u2014 {err}", 15000)
            self._install_btn.setEnabled(True)
            self.prep_finished.emit(False)
            return
        self._download_status_key = "sel_prepare_done"

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

        self._emit_ready()

    def _on_local_prep_done(self, ok, extract_dir, err):
        if ok and self._fw_package is None and extract_dir:
            # A browsed zip or folder that carries fw_package.ini fills in its model, type, name and header.
            self._fw_package = locate_fw_package(
                extract_dir,
                app_dir=INSTALL_DIR,
                source_names=(Path(str(self._current_package_path or "")).name,),
            )
            if self._fw_package is not None:
                self._apply_fw_package(self._fw_package)
        self._local_bar.setVisible(False)
        self.prep_finished.emit(bool(ok))
        if not ok:
            self._local_status_key = ""
            if "MULTIPLE_SCATTERS" in str(err):
                self._local_status.setText(tr("sel_multiple_scatters_title"))
                QMessageBox.warning(
                    self,
                    tr("sel_multiple_scatters_title"),
                    tr("sel_multiple_scatters_error"),
                )
            else:
                self._local_status.setText(f"{tr('sel_prepare_failed')} \u2014 {err}")
            return
        self._local_status_key = "sel_prepare_done"
        self._local_status.setText(tr("sel_prepare_done"))

        # Show the browsed package in the details header and start its icon path.
        self._details_package = self._details_package or catalog.FirmwarePackage(
            slug="local-" + str(Path(self._current_package_path).name),
            name=self._current_package_name or Path(self._current_package_path).name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._set_details_header(self._current_selected_rel, self._details_package)

        # Auto-detect .auth file in same folder/zip/rar
        self._auto_detect_auth_file(extract_dir)

        scatter_file = _find_scatter(Path(extract_dir))
        scatter_abs = scatter_file.resolve() if scatter_file else None
        extract_abs = Path(extract_dir).resolve() if extract_dir else None
        eff_model = getattr(self, "_current_package_model", "")
        if not eff_model and scatter_abs:
            plat, proj = catalog.parse_scatter_header(scatter_abs)
            inferred = catalog.infer_model_from_scatter(plat, proj)
            if inferred:
                eff_model = inferred
                self._current_package_model = eff_model
                self._select_model_for_manual(eff_model)
                if not self._current_package_name or self._current_package_name.startswith(("Installing software", "Get ready to install", "Get your")):
                    inf_name, inf_ver = infer_release_name(
                        eff_model, (Path(str(self._current_package_path or "")).name,)
                    )
                    self._current_package_name = f"{inf_name} {inf_ver}".strip() if inf_name else ""
                if self._details_package:
                    import dataclasses
                    self._details_package = dataclasses.replace(
                        self._details_package,
                        model=eff_model,
                        name=self._current_package_name,
                    )
                    self._set_details_header(self._current_selected_rel, self._details_package)
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
        self._fw_package = None
        filter_str = tr("sel_dialog_filter")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("sel_dialog_title"), downloads.default_picker_dir(), filter_str
        )
        if not path:
            return
        p = Path(path)
        is_scatter = p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower())
        if is_scatter:
            package_target = str(p.parent)
            display_name = p.parent.name
        else:
            package_target = path
            display_name = p.name

        self._path_edit.setText(package_target)
        self._current_package_path = package_target
        self._gui_explicit = None
        self._emit_gui_firmware_changed()
        self._current_installed_release_info = None

        self._current_selected_rel = None
        self._details_package = None
        self._raw_release_pixmap = None

        # Auto-detect .auth file in the same folder as the chosen file
        self._auto_detect_auth_file(package_target)

        mfr_info = catalog.parse_manufacturer_rom_info(display_name)
        if mfr_info:
            det_m = mfr_info["model"]
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
            self._selected_type = None
            self._current_package_name = mfr_info["title"]
        else:
            # Detect model and variant type from original browsed filename:
            det_m, det_t = detect_model_and_type_from_name(display_name)
            self._current_package_model = det_m or ""
            if det_m:
                self._select_model_for_manual(det_m)
            inf_name, inf_ver = infer_release_name(det_m, (display_name,))
            self._current_package_name = f"{inf_name} {inf_ver}".strip() if inf_name else ""

            if det_t:
                self._selected_type = det_t
                idx_t = self._type_combo.findData(det_t)
                if idx_t >= 0:
                    self._type_combo.setCurrentIndex(idx_t)
            else:
                self._selected_type = None

        self._details_package = catalog.FirmwarePackage(
            slug="local-" + str(Path(package_target).name),
            name=self._current_package_name or Path(package_target).name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._remember_gui_local(
            package_target, self._current_package_model, self._current_package_name
        )
        self._set_local_banner("sel_current_pkg", display_name)
        self._set_details_header(None, self._details_package)
        if is_scatter:
            self._on_local_prep_done(True, str(p.parent), "")
            return
        self._start_btn.setEnabled(False)
        self._prepare_package(package_target, self._on_local_prep_done)

    def _on_choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            tr("sel_dialog_folder_title"),
            downloads.default_picker_dir(),
        )
        if not folder:
            return
        self.load_local_folder(folder)

    def prefill_fw_package(self, pkg) -> None:
        """Open the install-from-file view on a firmware package's folder (fw_package.ini)."""
        self._show_local_view()
        self.load_local_folder(str(pkg.folder))

    def _apply_fw_package(self, pkg) -> None:
        """Fill model, type, name and the header from fw_package.ini."""
        if pkg.model:
            self._current_package_model = pkg.model
            self._select_model_for_manual(pkg.model)
        if pkg.type:
            idx = self._fw_type_index(pkg.type)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)
                self._selected_type = self._type_combo.itemData(idx)
        model = self._current_package_model or pkg.model or ""
        if pkg.has_release_name:
            name = pkg.name
            lang = (translator().lang or "en").strip()
            if not is_english_language(lang) and name:
                try:
                    from ..translate import fetch_google_translation
                    trans = fetch_google_translation(name, lang)
                    if trans:
                        name = trans
                except Exception:
                    pass
            self._current_package_name = catalog.format_install_heading(f"{name} {pkg.version}".strip(), model)
        else:
            # No release name in the INI or file names: keep the generic install heading.
            self._current_package_name = catalog.format_install_heading("", model)
        self._details_package = catalog.FirmwarePackage(
            slug="local-" + pkg.folder.name,
            name=self._current_package_name or pkg.folder.name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
            post_install=getattr(pkg, "post_install", "") or "",
            post_install_2=getattr(pkg, "post_install_2", "") or "",
        )
        self._set_details_header(None, self._details_package)

    def _fw_type_index(self, wanted: str) -> int:
        """Index of the software-type entry matching the ini's ``type`` (by data or label, any case)."""
        want = wanted.strip().lower()
        for i in range(self._type_combo.count()):
            data = self._type_combo.itemData(i)
            if (data and str(data).lower() == want) or self._type_combo.itemText(i).lower() == want:
                return i
        return -1

    def _show_fw_package_header(self, pkg) -> None:
        """Name, version and the package's updater image, shown like an online firmware header."""
        from PySide6.QtGui import QPixmap
        from .surfaces import seal_updating_text
        from .dark import is_dark
        from .release_icon import pixmap_from_icon_bytes
        from ..release_icons import load_local_package_icon_bytes

        name = pkg.name or pkg.folder.name
        lang = (translator().lang or "en").strip()
        if not is_english_language(lang) and pkg.name:
            try:
                from ..translate import fetch_google_translation
                trans = fetch_google_translation(pkg.name, lang)
                if trans:
                    name = trans
            except Exception:
                pass
        self._details_name.setText(name)
        self._details_version.setText(pkg.version or "")
        seal_updating_text(self._details_name)
        seal_updating_text(self._details_version)
        pix = QPixmap()
        data = load_local_package_icon_bytes(self._current_package_path, dark=bool(is_dark()))
        if data:
            pix = pixmap_from_icon_bytes(data)
        self._present_details_icon(pix, immediate=True)
        self._icon_phase = "release" if not pix.isNull() else "placeholder"
        self._details_header.setVisible(True)

    def load_local_folder(self, folder: str) -> None:
        p = Path(folder)
        display_name = p.name
        self._fw_package = read_fw_package(p, app_dir=INSTALL_DIR)

        self._path_edit.setText(folder)
        self._current_package_path = folder
        self._gui_explicit = None
        self._emit_gui_firmware_changed()
        self._current_installed_release_info = None

        self._current_selected_rel = None
        self._details_package = None
        self._raw_release_pixmap = None

        # Auto-detect .auth file in the selected folder
        self._auto_detect_auth_file(folder)

        mfr_info = catalog.parse_manufacturer_rom_info(display_name)
        if mfr_info:
            det_m = mfr_info["model"]
            self._current_package_model = det_m
            self._select_model_for_manual(det_m)
            self._selected_type = None
            self._current_package_name = mfr_info["title"]
        else:
            det_m, det_t = detect_model_and_type_from_name(display_name)
            self._current_package_model = det_m or ""
            if det_m:
                self._select_model_for_manual(det_m)
            from ..fw_package import infer_release_name

            inferred_name, inferred_version = infer_release_name(det_m, (display_name,))
            # A title, not an instruction: "Get your Y2 ready…" belongs on the
            # card heading, never in the sentence that reports what installed.
            self._current_package_name = (
                f"{inferred_name} {inferred_version}".strip() or display_name
            )

            if det_t:
                self._selected_type = det_t
                idx_t = self._type_combo.findData(det_t)
                if idx_t >= 0:
                    self._type_combo.setCurrentIndex(idx_t)
            else:
                self._selected_type = None

        if self._fw_package is not None:
            self._apply_fw_package(self._fw_package)
        self._remember_gui_local(folder, self._current_package_model, self._current_package_name)
        self._set_local_banner("sel_current_pkg", display_name)
        self._details_package = catalog.FirmwarePackage(
            slug="local-" + display_name,
            name=self._current_package_name or display_name,
            model=self._current_package_model or "",
            repo="",
            package_name="",
        )
        self._set_details_header(None, self._details_package)
        try:
            scatter_file = _find_scatter(p, allow_raise=True)
        except ScatterDiscoveryError:
            self._local_status_key = ""
            self._local_status.setText(tr("sel_multiple_scatters_title"))
            QMessageBox.warning(
                self,
                tr("sel_multiple_scatters_title"),
                tr("sel_multiple_scatters_error"),
            )
            self._start_btn.setEnabled(False)
            return

        if scatter_file is not None:
            self._on_local_prep_done(True, str(scatter_file.parent), "")
            return

        self._start_btn.setEnabled(False)
        self._on_local_prep_done(False, "", "NO_SCATTER_FILE")

    def _on_start_flash(self):
        if self._installer_mode and not self._installer_da:
            QMessageBox.warning(self, tr("mtk_da_label"), tr("mtk_da_required"))
            return
        if self._current_package_path:
            eff_model = getattr(self, "_current_package_model", "")
            eff_type = getattr(self, "_selected_type", None)
            if self._should_prompt_pre_install():
                if not self._prompt_pre_install(eff_model, eff_type):
                    return
            self._keep_settings_default = False
            self._stop_transfer_workers()
            from ..flash_service import cleanup_previous_install_scatters

            cleanup_previous_install_scatters(
                keep_package_path=self._current_package_path,
                for_new_install=True,
            )
            self._emit_ready()

    def _ensure_install_title(self) -> str:
        """Use a detected release title, or name the device being installed to."""
        name = (getattr(self, "_current_package_name", "") or "").strip()
        model = (getattr(self, "_current_package_model", "") or "").strip()
        lower = name.casefold()
        generic = (
            not name
            or name == "\u2014"
            or lower.startswith("installing software")
            or lower.endswith("ready to install software")
        )
        if generic:
            self._current_package_name = catalog.format_install_heading("", model)
        return self._current_package_name

    def _emit_ready(self):
        name = self._ensure_install_title()
        self.package_selected.emit(
            self._current_package_path,
            name,
            getattr(self, "_current_package_model", ""),
        )

    def _on_release_clicked(self, item):
        """A click only records which package the desktop tool should use.

        It does not download or start an install. Install / Restore starts the
        guided flow. The sidebar tool button downloads this package later.
        """
        from ..sp_flash_gui import is_sp_flash_gui_supported

        if not is_sp_flash_gui_supported() or item is None:
            return
        rel = item.data(Qt.UserRole)
        target = self._firmware_target_for_release(rel)
        if not target:
            return
        self._gui_explicit = target
        self._emit_gui_firmware_changed()

    def _on_release_double_clicked(self, _item):
        from ..sp_flash_gui import is_sp_flash_gui_supported

        if is_sp_flash_gui_supported():
            return
        self._on_install()

    def _remember_gui_local(self, path: str, model: str, name: str) -> None:
        from ..sp_flash_gui import is_gui_local_package, is_sp_flash_gui_supported

        if not is_sp_flash_gui_supported() or not is_gui_local_package(path):
            return
        self._gui_explicit = {
            "kind": "local",
            "path": path,
            "model": model or "",
            "name": name or Path(path).name,
        }
        self._emit_gui_firmware_changed()

    def _stop_gui_handoff_workers(self) -> None:
        """Drop a handoff download so Install / Restore can run on its own."""
        self._external_tool_callback = None
        if not getattr(self, "_gui_handoff_worker", False):
            return
        worker = self._download_worker
        if worker is not None:
            try:
                worker.finished.disconnect()
            except Exception:
                pass
            try:
                worker.progress.disconnect()
            except Exception:
                pass
            try:
                worker.status.disconnect()
            except Exception:
                pass
            worker.cancel()
            retire_worker(worker, self._active_workers)
            self._download_worker = None
        prep = self._prep_worker
        if prep is not None:
            prep.cancel()
            retire_worker(prep, self._active_workers)
        self._gui_handoff_worker = False

    def explicit_firmware(self) -> Optional[dict]:
        """A release the user clicked, or a qualifying file they browsed to."""
        target = getattr(self, "_gui_explicit", None)
        if not target:
            return None
        if target.get("kind") == "local":
            from ..sp_flash_gui import is_gui_local_package

            if not is_gui_local_package(target.get("path") or ""):
                return None
            return target
        if target.get("kind") == "online":
            rel = getattr(self, "_current_selected_rel", None)
            if not rel or rel.get("tag_name") != (target.get("release") or {}).get("tag_name"):
                return None
            return target
        if target.get("kind") == "cache":
            return target if self._gui_target_if_ready(target) else None
        return None

    def _firmware_target_for_release(self, rel) -> Optional[dict]:
        if not rel or not rel.get("download_url"):
            return None
        pkg = self.current_package()
        dest_dir = downloads.downloads_dir()
        fname = Path(rel.get("asset_name") or "rom.zip").name
        slug = getattr(pkg, "slug", None) or "firmware"
        dest = dest_dir / f"{slug}_{rel.get('tag_name', 'latest')}_{fname}"
        orig_name = rel.get("asset_name") or rel.get("download_url") or ""
        det_m, _det_t = detect_model_and_type_from_name(orig_name)
        model = det_m or (getattr(pkg, "device", "") if pkg is not None else "") or self.current_model()
        return {
            "kind": "online",
            "path": str(dest),
            "dest": dest,
            "url": rel["download_url"],
            "model": model or "",
            "name": self._install_card_title(
                pkg.name if pkg is not None and getattr(pkg, "name", "") else "",
                rel,
                model,
            ),
            "release": rel,
            "tag_name": rel.get("tag_name"),
            "software": pkg.name if pkg is not None and getattr(pkg, "name", "") else "",
        }

    def focused_firmware(self) -> Optional[dict]:
        """The catalogue release currently highlighted on screen."""
        if is_generic_mtk() or self.catalogue_section() == "local":
            return None
        target = self._firmware_target_for_release(getattr(self, "_current_selected_rel", None))
        return self._gui_target_if_ready(target) or target

    def _release_identity(self, target):
        """A stable identity key for ``target`` that ignores transient extraction state."""
        if not isinstance(target, dict):
            return None
        rel = target.get("release") or {}
        tag = rel.get("tag_name") or target.get("tag_name") or ""
        if tag:
            return tag
        pkg = target.get("package")
        return (
            target.get("kind"),
            pkg.name if pkg is not None and getattr(pkg, "name", "") else "",
            target.get("model") or "",
        )

    def begin_external_prepare(self, target: dict, callback) -> bool:
        """Download or extract ``target``, then ``callback(ok, extract_dir, err)``.

        The work runs on a worker thread. Progress uses the same signals as an
        install download so the progress card updates and the window stays live.
        """
        if not target:
            return False
        self._external_tool_callback = callback
        self._gui_handoff_worker = True
        kind = target.get("kind")
        if kind == "local":
            path = target.get("path") or ""
            if not path:
                self._external_tool_callback = None
                self._gui_handoff_worker = False
                return False
            self._current_package_path = path
            self._current_package_model = target.get("model") or ""
            self._current_package_name = target.get("name") or Path(path).name
            p = Path(path)
            is_scatter = p.is_file() and (p.suffix.lower() == ".txt" or "scatter" in p.name.lower())
            sc = p if is_scatter else (_find_scatter(p, allow_raise=False) if p.is_dir() else None)
            if sc:
                cb = self._external_tool_callback
                self._external_tool_callback = None
                self._gui_handoff_worker = False
                if cb:
                    cb(True, str(sc.parent), "")
                return True
            self._prepare_package(path, self._on_external_prep_done)
            return True
        if kind != "online":
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            return False
        dest = Path(target["dest"])
        extract_dir = compute_extract_dir(dest)
        from ..flash_service import completed_extract_dir

        already = completed_extract_dir(dest)
        if already and _is_extract_complete(already):
            cb = self._external_tool_callback
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            cb(True, str(already), "")
            return True
        if dest.is_file() and dest.stat().st_size > 0 and _is_extract_complete(extract_dir):
            cb = self._external_tool_callback
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            cb(True, str(extract_dir), "")
            return True
        self._current_package_model = target.get("model") or ""
        self._current_package_name = target.get("name") or dest.name
        if dest.is_file() and dest.stat().st_size > 0:
            self._current_package_path = str(dest)
            self._prepare_package(str(dest), self._on_external_prep_done)
            return True
        self.download_started.emit(self._current_package_name, self._current_package_model)
        self._last_download_percent = 0
        self._last_download_status_text = ""
        self._stop_transfer_workers()
        from ..flash_service import cleanup_previous_install_scatters

        cleanup_previous_install_scatters(keep_package_path=dest, for_new_install=True)
        from .. import portable

        self._download_worker = downloads.DownloadWorker(
            target["url"],
            str(dest),
            fallback_urls=portable.fallback_urls_for(target["url"]),
        )
        self._active_workers.add(self._download_worker)
        self._download_worker.progress.connect(self._on_download_progress)
        self._download_worker.status.connect(self._on_download_status)
        self._download_worker.finished.connect(self._on_external_download_done)
        self._download_worker.start()
        return True

    def _on_external_download_done(self, ok, result):
        if self._download_worker is not None:
            retire_worker(self._download_worker, self._active_workers)
            self._download_worker = None
        self._download_bar.setVisible(False)
        self.download_finished.emit(ok, str(result))
        if not ok:
            cb = getattr(self, "_external_tool_callback", None)
            self._external_tool_callback = None
            self._gui_handoff_worker = False
            if cb:
                cb(False, "", str(result))
            return
        self._current_package_path = str(result)
        self._prepare_package(str(result), self._on_external_prep_done)

    def _on_external_prep_done(self, ok, extract_dir, err):
        cb = getattr(self, "_external_tool_callback", None)
        self._external_tool_callback = None
        self._gui_handoff_worker = False
        if cb:
            cb(bool(ok), str(extract_dir or ""), str(err or ""))

    def _package_for_selection(self):
        try:
            pkgs = catalog.packages_for_model_software(
                self.current_model(),
                self.current_software(),
                brand=self.current_brand(),
            )
        except Exception:
            return None
        return pkgs[0] if pkgs else None

    def post_install_urls(self) -> tuple[str, str]:
        """Post-install page URLs for the firmware being installed.

        A local package's ``fw_package.ini`` wins; otherwise the catalogue entry
        selected in the list supplies them (its ``post_install`` /
        ``post_install_2`` attributes from ``slidia_manifest.xml``).
        """
        candidates = [
            getattr(self, "_fw_package", None),
            getattr(self, "_details_package", None),
        ]
        try:
            candidates.append(self._package_for_selection())
        except Exception:
            pass
        return catalog.post_install_urls_for(*candidates)

    def _set_details_header(self, rel, package) -> None:
        """Icon, software name, and the same version string as the release list."""
        self._details_package = package
        if not rel and package is not None and self._fw_package is not None:
            self._show_fw_package_header(self._fw_package)
            return
        if not rel:
            self._details_header.setVisible(False)
            self._details_name.setText("")
            self._details_version.setText("")
            return
        raw_name = package.name if package is not None and getattr(package, "name", "") else self.current_software()
        name = display_software_name(raw_name)
        version = catalog.parse_clean_release_tag_label(
            rel,
            prefer_240p=self.release_listing_filters()[2],
            version_only=True,
        )
        self._details_name.setText(name or "")
        self._details_version.setText(version or "")
        from .surfaces import seal_updating_text
        seal_updating_text(self._details_name)
        seal_updating_text(self._details_version)
        from .dark import is_dark
        from .release_icon import cached_icon_pixmap
        from ..release_icons import (
            icon_candidate_urls,
            release_image_fallback_urls,
            software_icon_urls,
        )

        dark = bool(is_dark())
        release_urls = icon_candidate_urls(rel, package, dark=dark)
        release_pm = cached_icon_pixmap(
            release_urls,
            release=rel,
            package=package,
        )
        if not release_pm.isNull():
            self._present_details_icon(release_pm, immediate=not getattr(self, "_icon_presented", False))
            self._icon_phase = "release"
        else:
            software_pm = cached_icon_pixmap(
                release_image_fallback_urls(rel, package, dark=dark)
                or software_icon_urls(package, dark=dark)
            )
            self._present_details_icon(
                software_pm, immediate=not getattr(self, "_icon_presented", False),
            )
            self._icon_phase = "software" if not software_pm.isNull() else "placeholder"
            if software_pm.isNull() and software_icon_urls(package, dark=dark):
                self._fetch_software_icon(package, dark)
        self._details_header.setVisible(True)
        self._ensure_release_icon(rel)

    def release_pixmap(self):
        """The release or software image already on screen, before the squircle paint."""
        from PySide6.QtGui import QPixmap

        if self.catalogue_section() == "local" or getattr(self, "_current_selected_rel", None) is None:
            pkg_path = getattr(self, "_current_package_path", None)
            if pkg_path:
                from ..release_icons import load_local_package_icon_bytes
                from .release_icon import pixmap_from_icon_bytes
                from .dark import is_dark

                data = load_local_package_icon_bytes(pkg_path, dark=bool(is_dark()))
                if data:
                    pix = pixmap_from_icon_bytes(data)
                    if not pix.isNull():
                        return pix
            return QPixmap()
        pix = getattr(self, "_raw_release_pixmap", None)
        return pix if pix is not None else QPixmap()

    def _present_details_icon(self, raw, *, immediate: bool) -> None:
        from .release_icon import squircle_pixmap
        from PySide6.QtGui import QPixmap

        source = raw if raw is not None and not getattr(raw, "isNull", lambda: True)() else QPixmap()
        if not source.isNull() and getattr(self, "_current_selected_rel", None) is not None:
            self._raw_release_pixmap = source
        else:
            self._raw_release_pixmap = None
        painted = squircle_pixmap(source, 40, complete=False)
        if immediate or not getattr(self, "_icon_presented", False):
            self._details_icon.show_stand_in(painted)
            self._icon_presented = True
        else:
            self._details_icon.cross_fade_to(painted)

    def _fetch_software_icon(self, package, dark: bool) -> None:
        from ..release_icons import software_icon_urls
        from .release_icon import SoftwareIconJob

        urls = tuple(software_icon_urls(package, dark=dark))
        if not urls or getattr(self, "_software_icon_urls", None) == urls:
            return
        self._software_icon_urls = urls
        previous = getattr(self, "_software_job", None)
        if previous is not None:
            previous.requestInterruption()
        job = SoftwareIconJob(package, dark=dark)
        job.ready.connect(self._on_software_icon_ready)
        job.finished.connect(job.deleteLater)
        self._software_job = job
        job.start()

    def _on_software_icon_ready(self, data: bytes) -> None:
        if getattr(self, "_icon_phase", "") == "release":
            return
        from .release_icon import pixmap_from_icon_bytes

        pixmap = pixmap_from_icon_bytes(data)
        if pixmap.isNull():
            return
        self._present_details_icon(pixmap, immediate=False)
        self._icon_phase = "software"
        self.release_icon_ready.emit(pixmap)

    def _prefetch_release_icons(self, releases) -> None:
        """Start icon downloads for the visible list. The selected tag goes first."""
        rel = getattr(self, "_current_selected_rel", None) or (releases[0] if releases else None)
        tag = str((rel or {}).get("tag_name") or "")
        self._start_icon_job(releases, priority_tag=tag)

    def _ensure_release_icon(self, rel) -> None:
        """Keep the selected release at the front of the paced preload."""
        if not rel:
            return
        tag = str(rel.get("tag_name") or "")
        running = getattr(self, "_icon_job", None)
        if running is not None and running.isRunning():
            running.promote(tag)
            return
        listed = list(getattr(self, "_listed_releases", None) or [])
        self._start_icon_job(listed or [rel], priority_tag=tag)

    def _start_icon_job(self, releases, priority_tag: str = "") -> None:
        from .dark import is_dark
        from .release_icon import ReleaseIconJob

        previous = getattr(self, "_icon_job", None)
        if previous is not None:
            previous.requestInterruption()
            try:
                previous.ready.disconnect()
            except Exception:
                pass
            previous.finished.connect(previous.deleteLater)
        self._icon_gen = int(getattr(self, "_icon_gen", 0)) + 1
        generation = self._icon_gen
        package = getattr(self, "_details_package", None) or self._package_for_selection()
        repo = getattr(package, "repo", "") or ""
        if repo:
            for rel in releases or []:
                if isinstance(rel, dict):
                    rel.setdefault("source_repo", repo)
        job = ReleaseIconJob(
            releases,
            package,
            dark=bool(is_dark()),
            priority_tag=priority_tag,
            cache_dir=None,
            parent=self,
        )
        job._generation = generation
        # A page method queues onto the UI thread. A lambda can run in the
        # worker and crash when it touches widgets.
        job.ready.connect(self._on_icon_bytes)
        job.finished.connect(lambda: self._clear_icon_job(job))
        job.finished.connect(job.deleteLater)
        self._icon_job = job
        job.start()

    def _clear_icon_job(self, job) -> None:
        """Drop the job reference once it finishes (before deleteLater)."""
        if getattr(self, "_icon_job", None) is job:
            self._icon_job = None

    def _on_icon_bytes(self, tag: str, data: bytes) -> None:
        job = self.sender()
        generation = getattr(job, "_generation", None)
        self._on_release_icon_ready(tag, data, generation)

    def _on_release_icon_ready(self, tag: str, data: bytes, generation: int) -> None:
        if generation != getattr(self, "_icon_gen", None):
            return
        rel = getattr(self, "_current_selected_rel", None)
        if not rel or str(rel.get("tag_name") or "") != str(tag or ""):
            return
        if not data:
            package = getattr(self, "_details_package", None)
            if package and getattr(self, "_icon_phase", "") != "software":
                from .release_icon import cached_icon_pixmap
                from ..release_icons import software_icon_urls
                from .dark import is_dark
                sw_pm = cached_icon_pixmap(software_icon_urls(package, dark=bool(is_dark())))
                if not sw_pm.isNull():
                    self._present_details_icon(sw_pm, immediate=False)
                    self._icon_phase = "software"
                    self.release_icon_ready.emit(sw_pm)
            return
        from .release_icon import pixmap_from_icon_bytes

        pixmap = pixmap_from_icon_bytes(data)
        if pixmap.isNull():
            return
        self._present_details_icon(pixmap, immediate=False)
        self._icon_phase = "release"
        self.release_icon_ready.emit(pixmap)

    def _install_card_title(self, software, rel, model) -> str:
        """Progress-card title: software, the list's version string, and model."""
        version = ""
        if rel:
            version = catalog.parse_clean_release_tag_label(
                rel,
                prefer_240p=self.release_listing_filters()[2],
                version_only=True,
            )
        return catalog.format_install_card_title(software, version, model)

    def current_model(self):
        return self._model_combo.currentText()

    def current_hardware_type(self) -> str:
        """The hardware type ('A', 'B', etc.) if model has variants (Y1), else ''."""
        if (self.current_model() or "").upper() == "Y1":
            return getattr(self, "_selected_type", "A") or "A"
        return ""

    def current_software(self):
        """Canonical English software name behind the selected row.

        The dropdown shows preset translations, so a row's text is not a
        lookup key; the canonical name lives in the item data instead.
        """
        idx = self._software_combo.currentIndex()
        if idx < 0:
            return self._software_combo.currentText()
        return str(self._software_combo.itemData(idx) or self._software_combo.itemText(idx))

    def current_package(self):
        model = self.current_model()
        software = self.current_software()
        pkgs = catalog.packages_for_model_software(
            model, software, brand=self.current_brand()
        )
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

    def catalogue_latest_tag(self) -> str:
        """Newest tag in the list that was last shown, used as a downgrade ceiling."""
        releases = list(getattr(self, "_listed_releases", None) or [])
        if not releases:
            return ""
        newest = max(releases, key=catalog.release_sort_key)
        return newest.get("tag_name") or ""

    def navigate_to_package(
        self, model: str, software_name: str, tag_name=None, refresh_if_missing=False
    ):
        """Switch to the catalogue, select specified model/software, highlight tag_name."""

        self._show_online_view()
        m_idx = self._model_combo.findText(model)
        if m_idx >= 0 and m_idx != self._model_combo.currentIndex():
            self._model_combo.setCurrentIndex(m_idx)
        sw_idx = self._find_software_index(software_name)
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
                    self._release_list.scrollToItem(it)
                    self._target_tag_to_select = None
                    break
            else:
                # The tag is not in the current listing (a release outside the
                # active filters, or a stale list). A running worker will apply
                # the pending tag when it lands; otherwise ask for a fresh list.
                worker = getattr(self, "_releases_worker", None)
                if (
                    refresh_if_missing
                    and self.current_package() is not None
                    and (worker is None or not worker.isRunning())
                ):
                    self._refresh_releases()

    def start_install_for_release(
        self,
        model: str,
        software_name: str,
        tag_name: str = "",
        release: Optional[dict] = None,
        package: Optional[catalog.FirmwarePackage] = None,
        keep_settings: bool = False,
    ):
        """Navigate to package/release and initiate installation with current method."""
        self._keep_settings_default = bool(keep_settings)
        self.navigate_to_package(model, software_name, tag_name=tag_name)
        if release:
            self._trigger_release_install(release, package=package)
        else:
            current_pkg = self.current_package()
            pkg_match = current_pkg and getattr(current_pkg, "model", "") == model and (
                getattr(current_pkg, "name", "") == software_name
                or getattr(current_pkg, "slug", "") == software_name
            )
            worker_running = self._releases_worker is not None and self._releases_worker.isRunning()
            if pkg_match and not worker_running and self._release_list.count() > 0:
                for row in range(self._release_list.count()):
                    it = self._release_list.item(row)
                    r = it.data(Qt.UserRole) if it else None
                    if r and (not tag_name or tag_name == "latest" or r.get("tag_name") == tag_name):
                        self._release_list.setCurrentRow(row)
                        self._trigger_release_install(r, package=package)
                        return
            if not self._release_list.count() and not worker_running:
                self._refresh_releases()
            self._auto_install_tag = tag_name or "latest"

    def take_keep_settings_default(self) -> bool:
        """Return and clear the update-install Keep my settings default."""
        wanted = bool(getattr(self, "_keep_settings_default", False))
        self._keep_settings_default = False
        return wanted
