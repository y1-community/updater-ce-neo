"""Desktop notification and window focus management across macOS, Windows, and Linux.

Provides window focus-grabbing when background tasks finish and surfaces native
OS system notifications so users are alerted when firmware installation or theme
transfers complete.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QWidget

logger = logging.getLogger(__name__)


def bring_window_to_front(window: Optional[QWidget]) -> None:
    """Un-minimize, raise, and activate the given window, grabbing focus across OSes."""
    if window is None or not isinstance(window, QWidget):
        return

    try:
        # Standard Qt window state restoration
        state = window.windowState()
        if state & Qt.WindowMinimized:
            window.setWindowState((state & ~Qt.WindowMinimized) | Qt.WindowActive)
        else:
            window.setWindowState(state | Qt.WindowActive)

        window.show()
        window.raise_()
        window.activateWindow()
    except Exception as e:
        logger.debug("Failed basic Qt window raise: %s", e)

    # OS-specific window activation
    if sys.platform == "darwin":
        try:
            from AppKit import NSApplication
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


def show_completion_notification(
    title: str,
    message: str,
    window: Optional[QWidget] = None,
) -> None:
    """Deliver a desktop notification and alert the user's desktop environment."""
    if window is not None:
        bring_window_to_front(window)

    delivered = False
    app = QApplication.instance()

    # 1. Qt native QSystemTrayIcon notification
    try:
        if QSystemTrayIcon.isSystemTrayAvailable() and app is not None:
            tray = getattr(app, "_completion_tray_icon", None)
            if tray is None:
                icon = window.windowIcon() if window is not None else app.windowIcon()
                tray = QSystemTrayIcon(icon, app)
                setattr(app, "_completion_tray_icon", tray)
            tray.show()
            tray.showMessage(title, message, QSystemTrayIcon.Information, 7000)
            delivered = True
    except Exception as e:
        logger.debug("QSystemTrayIcon notification failed: %s", e)

    # 2. Linux notify-send fallback
    if not delivered and sys.platform.startswith("linux"):
        notify_send = shutil.which("notify-send")
        if notify_send:
            try:
                subprocess.Popen(
                    [notify_send, "-a", "Innioasis Updater CE", title, message],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                delivered = True
            except Exception as e:
                logger.debug("notify-send failed: %s", e)

    # 3. macOS native notification fallback
    if not delivered and sys.platform == "darwin":
        try:
            from Foundation import NSUserNotification, NSUserNotificationCenter
            notif = NSUserNotification.alloc().init()
            notif.setTitle_(title)
            notif.setInformativeText_(message)
            NSUserNotificationCenter.defaultUserNotificationCenter().deliverNotification_(notif)
            delivered = True
        except Exception:
            try:
                escaped_title = title.replace('"', '\\"')
                escaped_msg = message.replace('"', '\\"')
                subprocess.Popen(
                    ["osascript", "-e", f'display notification "{escaped_msg}" with title "{escaped_title}"'],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                delivered = True
            except Exception as e:
                logger.debug("macOS notification failed: %s", e)

    # Flash window / taskbar icon
    if window is not None:
        try:
            QApplication.alert(window, 0)
        except Exception:
            pass
