"""Headless background mode: the menu bar item without the window.

Running in the background exists so the app can tell the user about a new
notice, a newer player release or a newer Updater build while its window is
out of the way. None of that needs the catalogue, the install card or the
settings page, yet a ``--tray`` launch used to build the whole window and
hide it, and a window closed to the tray kept all of it alive forever.

This object is what the process is reduced to: the tray item, the notification
routing, and the same three checks the window runs. The window is created the
first time it is actually needed (a click, a notification, a second launch)
and handed back here when it has been idle in the tray for long enough. The
handover is deliberately explicit -- :meth:`adopt_window` and
:meth:`release_window` -- because a live window must be the only thing posting
checks and notifications while it exists, or the user would see each of them
twice.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QThread, QTimer, Signal

logger = logging.getLogger(__name__)

#: How often the headless agent re-checks app/device releases while alone.
BACKGROUND_TICK_MS = 5 * 60 * 1000
#: The online/manifest/all-repository sweep uses the shared randomized 2-8
#: minute delay (``online_refresh.next_interval_ms``). This lower bound is kept
#: for callers that describe the cadence.
BACKGROUND_ONLINE_TICK_MS = 2 * 60 * 1000
#: First check after a tray launch; late enough not to compete with startup.
BACKGROUND_STARTUP_DELAY_MS = 8 * 1000


class _DeviceUpdateWorker(QThread):
    """Runs the recorded-device release check off the UI thread."""

    finished = Signal(list)

    def __init__(self, settings, force_refresh=False, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._force_refresh = force_refresh

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            from . import player_update_check

            updates = player_update_check.check_device_updates(
                settings=self._settings,
                force_refresh=self._force_refresh,
            )
            if self.isInterruptionRequested():
                return
        except Exception:
            logger.debug("Background device update check failed", exc_info=True)
            updates = []
        self.finished.emit(updates or [])


class _ManifestRefreshWorker(QThread):
    """Fetch ``slidia_manifest.xml`` and return its parsed packages."""

    finished = Signal(list)

    def run(self):
        entries = []
        try:
            if self.isInterruptionRequested():
                return
            from . import catalog, manifest, online_data

            # Force the fetch: the point of the background timer is to find a
            # newly published manifest promptly, not wait out the 5 min TTL.
            updated = online_data.refresh("slidia_manifest.xml", force=True)
            # A failed download must not reinstate the older cached copy (or
            # the build's two-model snapshot) over a live catalogue.
            if updated or not catalog.LIVE_CATALOG:
                text = online_data.cached_text("slidia_manifest.xml")
                if text.strip():
                    entries = manifest.parse_manifest_xml(text) or []
            if self.isInterruptionRequested():
                return
        except Exception:
            logger.debug("Background manifest refresh failed", exc_info=True)
        self.finished.emit(entries)


class BackgroundAgent(QObject):
    """Owns the tray and the background checks while no window exists."""

    def __init__(self, app, build_window, on_window_released=None):
        super().__init__(app)
        self._app = app
        self._build_window = build_window
        self._on_window_released = on_window_released
        self._window = None
        self._tray = None
        self._timers: list[QTimer] = []
        self._notices_connected = False
        # Keep the exact bound callable: PySide matches a disconnect against
        # the object it was given, and a fresh self._on_notices_ready is a new
        # object each time.
        self._notices_slot = self._on_notices_ready
        self._update_worker = None
        self._device_worker = None
        self._manifest_worker = None
        self._release_worker = None
        self._manifest_signature = None
        self._online_timer = None

    # -- tray ---------------------------------------------------------------
    def ensure_tray(self):
        """Create the host's tray/menu bar item once and return it."""
        if self._tray is not None:
            return self._tray
        from PySide6.QtGui import QIcon

        from .paths import RESOURCES_DIR
        from .tray import create_background_tray

        icon_path = RESOURCES_DIR / "icon.png"
        fallback = QIcon(str(icon_path)) if icon_path.exists() else QIcon()
        self._tray = create_background_tray(
            self._app,
            self.show_window,
            self.quit,
            fallback,
            check_updates=self.check_updates,
        )
        try:
            self._tray.message_clicked.connect(self._on_tray_message_clicked)
        except Exception:
            logger.debug("Tray message click could not be connected", exc_info=True)
        return self._tray

    def start(self, *, show_tray: bool = True) -> None:
        """Become the background app: tray up, notifications and checks on."""
        from .tray import hold_off_automatic_termination

        tray = self.ensure_tray()
        if show_tray:
            tray.show()
        # AppKit reaps a windowless app that lives only in the menu bar.
        hold_off_automatic_termination(True)
        self._app._notification_click_handler = self._on_notification_click
        self._connect_notices()
        try:
            from .notice_service import NoticeService

            service = NoticeService.instance()
            service.start_passive_refresh()
            QTimer.singleShot(700, service.fetch_async)
            QTimer.singleShot(900, service.replay)
        except Exception:
            logger.debug("Notice service could not start", exc_info=True)
        self._start_timers()
        # Do not wait two minutes for the first manifest: the catalogue that a
        # tray launch starts with may already be older than the repo.
        self._refresh_online()
        QTimer.singleShot(BACKGROUND_STARTUP_DELAY_MS, self._startup_tick)

    def shutdown(self) -> None:
        """Stop the timers and retire a check that is still in flight."""
        self._stop_timers()
        for worker in (
            self._update_worker,
            self._device_worker,
            self._manifest_worker,
            self._release_worker,
        ):
            try:
                if worker is not None and worker.isRunning():
                    worker.requestInterruption()
                    worker.wait(300)
            except Exception:
                pass

    # -- window handover ----------------------------------------------------
    def adopt_window(self, window) -> None:
        """A window now exists: it owns checks, notices and notification clicks."""
        self._window = window
        window._background_agent = self
        if self._tray is not None:
            window._background_tray = self._tray
        self._stop_timers()
        self._disconnect_notices()
        self._app._notification_click_handler = window._on_notification_clicked

    def release_window(self, window=None) -> None:
        """The window has been destroyed; the agent takes the background back."""
        self._window = None
        if callable(self._on_window_released):
            try:
                self._on_window_released()
            except Exception:
                logger.debug("Window holder was not cleared", exc_info=True)
        self._app._notification_click_handler = self._on_notification_click
        self._connect_notices()
        self._start_timers()
        self._refresh_online()

    def show_window(self, route=None):
        """Build the window if necessary, show it, then route a click."""
        window = self._window
        if window is None:
            try:
                window = self._build_window()
            except Exception:
                logger.exception("The app window could not be built")
                return None
            self.adopt_window(window)
        try:
            window._show_from_tray()
        except Exception:
            logger.debug("Showing the window failed", exc_info=True)
        if route:
            try:
                window._on_notification_clicked(route)
            except Exception:
                logger.debug("Notification route failed", exc_info=True)
        return window

    def check_updates(self) -> None:
        """The tray's update action: open the window, then check as it does."""
        window = self.show_window()
        if window is None:
            return
        try:
            window._on_check_updates_clicked()
        except Exception:
            logger.debug("Update check from the tray failed", exc_info=True)

    def quit(self) -> None:
        """Quit from the menu bar, letting a live window keep its own guards."""
        if self._window is not None:
            try:
                self._window._quit_from_tray()
                return
            except Exception:
                logger.debug("Window quit failed; quitting directly", exc_info=True)
        self._stop_timers()
        tray = self._tray
        if tray is not None:
            try:
                tray.hide()
            except Exception:
                pass
        self._app.setQuitOnLastWindowClosed(True)
        self._app.quit()

    # -- notifications ------------------------------------------------------
    def _on_notification_click(self, route=None) -> None:
        route = str(route or getattr(self._app, "_last_notification_route", "") or "")
        self.show_window(route or None)

    def _on_tray_message_clicked(self) -> None:
        route = str(getattr(self._app, "_last_notification_route", "") or "")
        self.show_window(route or None)

    def _on_notices_ready(self, notices) -> None:
        """Deliver a new-notice summary while there is no window to show one."""
        if self._window is not None:
            return
        try:
            from . import notice_alerts

            fresh = notice_alerts.unseen_notices(notices)
            if not fresh:
                return
            notice_alerts.mark_notices_seen(n.id for n in fresh)
            notice_alerts.notify_new_notices(
                fresh, tray=self._tray, route="notices"
            )
        except Exception:
            logger.debug("Background notice notification failed", exc_info=True)

    def _connect_notices(self) -> None:
        if self._notices_connected:
            return
        try:
            from .notice_service import NoticeService

            NoticeService.instance().notices_ready.connect(self._notices_slot)
            self._notices_connected = True
        except Exception:
            logger.debug("Could not listen for notices", exc_info=True)

    def _disconnect_notices(self) -> None:
        if not self._notices_connected:
            return
        try:
            from .notice_service import NoticeService

            NoticeService.instance().notices_ready.disconnect(self._notices_slot)
        except Exception:
            logger.debug("Could not stop listening for notices", exc_info=True)
        self._notices_connected = False

    # -- background checks --------------------------------------------------
    def _start_timers(self) -> None:
        if not self._timers:
            updates = QTimer(self)
            updates.setInterval(BACKGROUND_TICK_MS)
            updates.timeout.connect(self._tick)
            online = QTimer(self)
            online.setSingleShot(True)
            online.timeout.connect(self._online_tick)
            self._timers = [updates, online]
            self._online_timer = online
        for timer in self._timers:
            if timer is not self._online_timer:
                timer.start()
        self._schedule_online_tick()

    def _stop_timers(self) -> None:
        for timer in self._timers:
            try:
                timer.stop()
            except Exception:
                pass

    def _startup_tick(self) -> None:
        # The manifest refresh that runs at launch starts the device check as
        # soon as it has new catalogue entries. This covers only the app's own
        # release, so a slow manifest fetch cannot hide an Updater update.
        if self._window is None:
            self._check_app_update()

    def _tick(self) -> None:
        self._check_app_update()
        self._check_device_updates()

    def _schedule_online_tick(self) -> None:
        """Arm the next randomized online sweep."""
        timer = getattr(self, "_online_timer", None)
        if timer is None:
            return
        from .online_refresh import next_interval_ms

        timer.start(next_interval_ms())

    def _online_tick(self) -> None:
        self._refresh_online()
        self._schedule_online_tick()

    def _refresh_online(self) -> None:
        """Keep the files that can become a notification current.

        A windowless copy has no donation bar and no release feed on screen, so
        donors.csv and the feed snapshots are not fetched at all. The manifest,
        every catalogue repository's release cache and notice.xml are refreshed;
        NoticeService parses the notice feed and posts the notification itself.
        """
        self._refresh_manifest()
        self._refresh_all_releases()
        try:
            from .notice_service import NoticeService

            NoticeService.instance().fetch_async()
        except Exception:
            logger.debug("Background notice refresh failed", exc_info=True)

    def _refresh_all_releases(self) -> None:
        """Warm every repository's release cache while no window is open."""
        worker = self._release_worker
        if worker is not None and worker.isRunning():
            return
        try:
            from .online_refresh import AllReposRefreshWorker

            worker = AllReposRefreshWorker(parent=self)
            self._release_worker = worker
            worker.refreshed.connect(self._on_all_releases_refreshed)
            worker.start()
        except Exception:
            logger.debug("Background release refresh failed", exc_info=True)

    def _on_all_releases_refreshed(self, results) -> None:
        if self._window is None or not results:
            return
        # A window may have been created while the sweep was in flight. It owns
        # the list now; the next window refresh uses the cache this sweep wrote.
        logger.debug("Background release refresh finished for %d repositories", len(results))

    def _refresh_manifest(self) -> None:
        worker = self._manifest_worker
        if worker is not None and worker.isRunning():
            return
        try:
            worker = _ManifestRefreshWorker(self)
            worker.finished.connect(self._on_manifest_refreshed)
            self._manifest_worker = worker
            worker.start()
        except Exception:
            logger.debug("Background manifest refresh could not start", exc_info=True)

    def _on_manifest_refreshed(self, entries) -> None:
        """Adopt a freshly downloaded manifest, then check recorded devices.

        Refreshing the file alone changes nothing: the catalogue is an in-memory
        list. A new manifest can add a model or a software line, so the parsed
        entries replace it and the device check runs immediately, bypassing the
        release cache that the manifest just invalidated.
        """
        if self._window is not None or not entries:
            return
        signature = tuple(
            (
                str(getattr(package, "model", "") or ""),
                str(getattr(package, "repo", "") or ""),
                str(getattr(package, "slug", "") or ""),
                str(getattr(package, "name", "") or ""),
            )
            for package in entries
        )
        changed = signature != self._manifest_signature
        self._manifest_signature = signature
        try:
            from . import catalog

            catalog.set_live_catalog(entries)
        except Exception:
            logger.debug("Live catalogue could not be updated", exc_info=True)
            return
        if changed:
            self._check_device_updates(force_refresh=True)

    def _check_app_update(self) -> None:
        worker = self._update_worker
        if worker is not None and worker.isRunning():
            return
        try:
            from .config import APP_VERSION, UPDATE_REPO
            from .updates import UpdateCheckWorker

            worker = UpdateCheckWorker(UPDATE_REPO, APP_VERSION, self)
            worker.finished.connect(self._on_app_update_checked)
            self._update_worker = worker
            worker.start()
        except Exception:
            logger.debug("Background app update check could not start", exc_info=True)

    def _on_app_update_checked(self, info) -> None:
        if self._window is not None:
            return
        try:
            from . import update_alerts

            update_alerts.notify_app_update(info, tray=self._tray)
            if self._tray is not None:
                self._tray.set_update_available(
                    bool(getattr(info, "tag", ""))
                )
        except Exception:
            logger.debug("Background app update notification failed", exc_info=True)

    def _check_device_updates(self, force_refresh: bool = False) -> None:
        worker = self._device_worker
        if worker is not None and worker.isRunning():
            return
        try:
            from PySide6.QtCore import QSettings

            worker = _DeviceUpdateWorker(
                QSettings("innioasis", "updater"),
                force_refresh=force_refresh,
                parent=self,
            )
            worker.finished.connect(self._on_device_updates_checked)
            self._device_worker = worker
            worker.start()
        except Exception:
            logger.debug("Background device update check could not start", exc_info=True)

    def _on_device_updates_checked(self, updates) -> None:
        if self._window is not None:
            return
        try:
            from . import update_alerts

            update_alerts.notify_device_updates(updates, tray=self._tray)
        except Exception:
            logger.debug("Background device notification failed", exc_info=True)
