"""Desktop notification and window focus management across macOS, Windows, and Linux.

Provides window focus-grabbing when background tasks finish and surfaces native
OS system notifications so users are alerted when firmware installation or theme
transfers complete.

On macOS the notice goes through ``UNUserNotificationCenter``: that is the only
channel that takes part in the host's permission and presentation settings, so
the app can appear on screen as a banner. The older ``NSUserNotification`` API
still exists but macOS files its notices silently, which is why they used to
land in Notification Center without ever being shown.

Both need a bundle identifier on the process, which the build gives the bundled
interpreter (see ``write_interpreter_identity`` in
``scripts/build_universal_app.py``).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

logger = logging.getLogger(__name__)

# An AppleScript program with no interpolation in it: the title and body arrive
# as arguments, so a quote, backslash or "$(...)" in a notice can never become
# code. Only a source checkout ever reaches this channel.
_SCRIPT_NOTIFICATION_PROGRAM = (
    "display notification (item 2 of argv) with title (item 1 of argv)"
)


def bring_window_to_front(window: Optional[QWidget]) -> None:
    """Un-minimize, raise, and activate the given window, grabbing focus across OSes."""
    if window is None or not isinstance(window, QWidget):
        return

    try:
        # Standard Qt window state restoration, including a copy that was
        # closed to the menu bar/tray (hidden rather than minimised).
        if window.isMinimized():
            window.showNormal()
        window.setWindowState(window.windowState() | Qt.WindowActive)
        window.show()
        window.raise_()
        window.activateWindow()
        handle = window.windowHandle()
        if handle is not None:
            handle.requestActivate()
    except Exception as e:
        logger.debug("Failed basic Qt window raise: %s", e)

    # OS-specific window activation
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication

            # The window is the app again: give it back its Dock icon and menu
            # bar after a stay as a menu-bar-only (accessory) process.
            from .tray import set_menu_bar_only

            set_menu_bar_only(False)
            app = NSApplication.sharedApplication()
            app.activateIgnoringOtherApps_(True)
        except Exception as e:
            logger.debug("macOS activateIgnoringOtherApps failed: %s", e)
    elif os.name == "nt":
        try:
            import ctypes
            hwnd = int(window.winId())
            # SW_RESTORE = 9
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception as e:
            logger.debug("Windows SetForegroundWindow failed: %s", e)
    else:
        # Linux (X11 / Wayland alert)
        try:
            QApplication.alert(window, 0)
        except Exception as e:
            logger.debug("Linux QApplication.alert failed: %s", e)


def _macos_bundle_identifier() -> str:
    """This process's bundle identifier, or "" when it has none.

    The build gives the bundled interpreter the app's identity, because the
    launcher hands over to it and the app's code then runs in that process. A
    source checkout has no bundle at all.
    """
    try:
        from Foundation import NSBundle

        bundle = NSBundle.mainBundle()
        identifier = bundle.bundleIdentifier() if bundle is not None else None
        return str(identifier or "")
    except Exception:
        return ""


def system_notifications_available() -> bool:
    """Whether this computer has a channel that can show a system notification."""
    try:
        if sys.platform == "darwin":
            try:
                from UserNotifications import UNUserNotificationCenter  # noqa: F401

                return True
            except Exception:
                pass
            try:
                from AppKit import NSUserNotificationCenter  # noqa: F401

                return True
            except Exception:
                return bool(shutil.which("osascript"))
        if os.name == "nt":
            return True
        if shutil.which("notify-send"):
            return True
        return QSystemTrayIcon.isSystemTrayAvailable()
    except Exception:
        return False


# -- macOS: UNUserNotificationCenter ---------------------------------------
#
# State for the one centre this process can have. ``authorized`` stays None
# until the user answers the permission question; notices posted before the
# answer are held, because a request added to an unauthorized centre is filed
# without ever being shown.
_macos_un_state = {
    "center": None,
    "resolved": False,
    "requested": False,
    "authorized": None,
    "pending": [],
}
_macos_un_delegate = None


def _macos_presentation_options() -> int:
    """Banner *and* list *and* sound: a notice in the foreground still shows."""
    try:
        from UserNotifications import (
            UNNotificationPresentationOptionBanner,
            UNNotificationPresentationOptionList,
            UNNotificationPresentationOptionSound,
        )

        return int(
            UNNotificationPresentationOptionBanner
            | UNNotificationPresentationOptionList
            | UNNotificationPresentationOptionSound
        )
    except Exception:
        # Badge=1, Sound=2, Alert=4, List=8, Banner=16 on every macOS that has
        # the framework at all.
        return 16 | 8 | 2


def _route_notification_click(route: str = "") -> None:
    try:
        app = QApplication.instance()
        handler = getattr(app, "_notification_click_handler", None)
        if callable(handler):
            handler(route or getattr(app, "_last_notification_route", ""))
    except Exception:
        logger.debug("Notification click handler failed", exc_info=True)


def _macos_un_delegate_object():
    """The centre's delegate: it decides presentation and routes a click."""
    global _macos_un_delegate
    if _macos_un_delegate is None:
        from Foundation import NSObject

        class _UserNotificationDelegate(NSObject):
            # The selectors are the framework's own
            # (``userNotificationCenter:willPresentNotification:…``): PyObjC
            # rejects a shortened name with BadPrototypeError, and a delegate
            # that took that path silently lost every notice.
            def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, center, notification, handler):  # noqa: N802
                # Without this macOS silences a notice while the app is active,
                # which is most of the time an install finishes.
                try:
                    handler(_macos_presentation_options())
                except Exception:
                    logger.debug("Presentation options failed", exc_info=True)
                    handler(0)

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, center, response, handler):  # noqa: N802
                route = ""
                try:
                    content = response.notification().request().content()
                    route = str((content.userInfo() or {}).get("route") or "")
                except Exception:
                    logger.debug("Notification response had no route", exc_info=True)
                _route_notification_click(route)
                handler()

        _macos_un_delegate = _UserNotificationDelegate.alloc().init()
    return _macos_un_delegate


def _request_macos_authorization(center) -> None:
    """Ask the host once for permission, then release what was held."""
    state = _macos_un_state
    if state["requested"]:
        return
    state["requested"] = True
    try:
        from UserNotifications import (
            UNAuthorizationOptionAlert,
            UNAuthorizationOptionSound,
        )

        options = int(UNAuthorizationOptionAlert | UNAuthorizationOptionSound)
    except Exception:
        options = 4 | 2

    def _answered(granted, error):
        state["authorized"] = bool(granted)
        if error is not None:
            logger.debug("Notification authorization error: %s", error)
        pending, state["pending"] = list(state["pending"]), []
        if not granted:
            logger.info("macOS notifications are off for this app (declined)")
            return
        for title, message, route in pending:
            _post_macos_user_notification(center, title, message, route)

    try:
        center.requestAuthorizationWithOptions_completionHandler_(options, _answered)
    except Exception:
        logger.debug("Notification authorization request failed", exc_info=True)
        state["requested"] = False


def _macos_user_notification_center():
    """The app's ``UNUserNotificationCenter``, or None when it cannot exist."""
    state = _macos_un_state
    if state["resolved"]:
        return state["center"]
    state["resolved"] = True
    try:
        from UserNotifications import UNUserNotificationCenter
    except Exception:
        logger.debug("UserNotifications framework unavailable", exc_info=True)
        return None
    if not _macos_bundle_identifier():
        # No bundle, no notification registration: this is a source checkout.
        return None
    try:
        center = UNUserNotificationCenter.currentNotificationCenter()
        if center is None:
            return None
        center.setDelegate_(_macos_un_delegate_object())
        state["center"] = center
        _request_macos_authorization(center)
        return center
    except Exception:
        logger.debug("UserNotifications centre unavailable", exc_info=True)
        return None


def _post_macos_user_notification(center, title: str, message: str, route: str = "") -> bool:
    """Deliver one notice through the app's own Notification Center entry."""
    state = _macos_un_state
    if state["authorized"] is None:
        # The permission question is still open: hold the notice rather than
        # let macOS file it silently.
        if len(state["pending"]) < 8:
            state["pending"].append((title, message, route))
        return True
    if not state["authorized"]:
        return False
    try:
        from UserNotifications import UNMutableNotificationContent, UNNotificationRequest

        content = UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(message)
        if route:
            # Which page the window opens when the notice is clicked.
            content.setUserInfo_({"route": route})
        request = UNNotificationRequest.requestWithIdentifier_content_trigger_(
            f"innioasis-updater-{time.time_ns()}", content, None
        )
        center.addNotificationRequest_withCompletionHandler_(request, None)
        return True
    except Exception:
        logger.debug("UserNotifications delivery failed", exc_info=True)
        return False


def prepare_system_notifications() -> None:
    """Ask the host once for permission, early, so later notices can appear.

    Called while the app starts: macOS shows the permission question then, and
    anything raised before the answer is held instead of being filed silently.
    """
    if sys.platform != "darwin":
        return
    try:
        _macos_user_notification_center()
    except Exception:
        logger.debug("Notification permission could not be requested", exc_info=True)


# -- macOS: the legacy centre, for hosts without the framework --------------

_macos_delegate = None


def _macos_notification_center():
    """Legacy Notification Center with a click delegate installed, or None.

    Only used when ``UserNotifications`` is not available: this API's notices
    are filed without a banner on current macOS.
    """
    global _macos_delegate
    try:
        from AppKit import NSUserNotificationCenter
        from Foundation import NSObject

        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return None
        if _macos_delegate is None:
            class _NotificationClickDelegate(NSObject):
                def userNotificationCenter_didActivateNotification_(self, center, notification):  # noqa: N802
                    _route_notification_click()

            _macos_delegate = _NotificationClickDelegate.alloc().init()
        center.setDelegate_(_macos_delegate)
        return center
    except Exception:
        logger.debug("macOS notification click routing unavailable", exc_info=True)
        return None


def _show_macos_notification(center, title: str, message: str) -> bool:
    """Deliver one notice through the legacy centre."""
    try:
        from Foundation import NSUserNotification

        notification = NSUserNotification.alloc().init()
        notification.setTitle_(title)
        notification.setInformativeText_(message)
        center.deliverNotification_(notification)
        return True
    except Exception:
        logger.debug("macOS notification delivery failed", exc_info=True)
        return False


def _show_macos_script_notification(title: str, message: str) -> bool:
    """Script channel for a source checkout, which has no bundle to register.

    A bundled app never comes here: macOS files every ``osascript`` banner under
    Script Editor, so the notice would appear to come from another app that the
    user can neither see nor allow in Notifications.
    """
    if _macos_bundle_identifier():
        logger.info(
            "macOS could not show a notification for this app; it is still on its page in the app"
        )
        return False
    osascript = shutil.which("osascript")
    if not osascript:
        return False
    try:
        subprocess.Popen(
            [
                osascript,
                "-e", "on run argv",
                "-e", _SCRIPT_NOTIFICATION_PROGRAM,
                "-e", "end run",
                title,
                message,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as e:
        logger.debug("macOS notification script failed: %s", e)
        return False


def _show_tray_message(tray, title: str, message: str, timeout_ms: int) -> bool:
    try:
        tray.showMessage(title, message, QSystemTrayIcon.Information, timeout_ms)
        return True
    except Exception as e:
        logger.debug("Tray notification failed: %s", e)
        return False


def _fallback_tray(app: QApplication, window: Optional[QWidget] = None):
    """A tray icon used only as a balloon-notification channel."""
    tray = getattr(app, "_completion_tray_icon", None)
    if tray is None:
        icon = window.windowIcon() if window is not None else app.windowIcon()
        tray = QSystemTrayIcon(icon, app)
        setattr(app, "_completion_tray_icon", tray)
    if not getattr(tray, "_notice_click_connected", False):
        def _route_click():
            _route_notification_click()

        tray.messageClicked.connect(_route_click)
        tray._notice_click_connected = True
    tray.show()
    return tray


def show_system_notification(
    title: str,
    message: str,
    tray=None,
    timeout_ms: int = 10000,
    route: str = "",
) -> bool:
    """Deliver a notification without raising the window. Returns True if sent.

    ``tray`` is the app's own background tray when one exists. On macOS the
    app's Notification Center entry is tried first: it is what the user sees
    under this app's name, and a click routes back to the window. The tray and
    Linux's ``notify-send`` are the other channels.
    """
    app = QApplication.instance()
    if app is not None:
        # Which page a click should open; the window's click handler reads it.
        setattr(app, "_last_notification_route", route or "")

    if sys.platform == "darwin":
        # 1. The app's own Notification Center entry (banners live here).
        center = _macos_user_notification_center()
        if center is not None:
            if _macos_un_state["authorized"] is False:
                logger.info("macOS notifications are off for this app")
                return False
            if _post_macos_user_notification(center, title, message, route):
                return True
        # 2. Hosts without UserNotifications get the legacy centre.
        legacy = _macos_notification_center()
        if legacy is not None and _show_macos_notification(legacy, title, message):
            return True
        # 3. The background menu bar item, when the user keeps one running.
        if tray is not None and _show_tray_message(tray, title, message, timeout_ms):
            return True
        # 4. A source checkout, which has no bundle to register.
        return _show_macos_script_notification(title, message)

    # 1. The app's background tray, when the user keeps one running.
    if tray is not None and _show_tray_message(tray, title, message, timeout_ms):
        return True

    # 2. Linux notification daemon.
    if sys.platform.startswith("linux"):
        notify_send = shutil.which("notify-send")
        if notify_send:
            try:
                subprocess.Popen(
                    [notify_send, "-a", "Innioasis Updater CE", title, message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except Exception as e:
                logger.debug("notify-send failed: %s", e)

    # 3. Balloon through a tray icon (Windows, and any host left above).
    try:
        if app is not None and QSystemTrayIcon.supportsMessages():
            _fallback_tray(app).showMessage(
                title, message, QSystemTrayIcon.Information, timeout_ms
            )
            return True
    except Exception as e:
        logger.debug("Fallback tray notification failed: %s", e)
    return False


def show_completion_notification(
    title: str,
    message: str,
    window: Optional[QWidget] = None,
    route: str = "install",
) -> None:
    """Deliver a desktop notification and alert the user's desktop environment.

    The default route takes a click back to the install card, where the
    completion message and the next steps are waiting.
    """
    if window is not None:
        bring_window_to_front(window)
    show_system_notification(title, message, route=route)

    # Flash window / taskbar icon
    if window is not None:
        try:
            QApplication.alert(window, 0)
        except Exception:
            pass
