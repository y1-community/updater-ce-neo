"""Menu bar (macOS) / system tray icon for running in the background.

The icon only exists while the app is kept running on purpose: the user opts in
with "Run in background for notifications". Clicking it brings the main window
back.
"""

from __future__ import annotations

import logging
import sys
from typing import Callable, Optional

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

logger = logging.getLogger(__name__)

RUN_IN_BACKGROUND_KEY = "run_in_background"
SF_SYMBOL = "arrow.triangle.2.circlepath"


def run_in_background_enabled(settings: Optional[QSettings] = None) -> bool:
    """The opt-in switch. Off unless the user turned it on."""
    settings = settings or QSettings("innioasis", "updater")
    value = settings.value(RUN_IN_BACKGROUND_KEY, False)
    return str(value).lower() in ("1", "true", "yes")


def _macos_template_icon(fallback: QIcon) -> QIcon:
    """A system-tinted SF Symbol, so macOS draws it black or white with the menu bar."""
    try:
        from AppKit import NSImage

        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(SF_SYMBOL, None)
        if image is None:
            return fallback
        image.setTemplate_(True)
        from AppKit import NSBitmapImageRep, NSPNGFileType
        from PySide6.QtGui import QImage, QPixmap

        rep = NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        png = bytes(rep.representationUsingType_properties_(NSPNGFileType, None))
        pixmap = QPixmap.fromImage(QImage.fromData(png))
        icon = QIcon(pixmap)
        icon.setIsMask(True)
        return icon
    except Exception:
        logger.debug("SF Symbol tray icon unavailable", exc_info=True)
        return fallback


class BackgroundTray:
    """Owns the tray icon and its menu for one app session."""

    def __init__(self, app: QApplication, show_window: Callable[[], None], quit_app: Callable[[], None], fallback_icon: QIcon):
        self._app = app
        self._show_window = show_window
        self._quit_app = quit_app
        icon = _macos_template_icon(fallback_icon) if sys.platform == "darwin" else fallback_icon
        self._tray = QSystemTrayIcon(icon, app)
        self._tray.setToolTip(app.applicationName())
        menu = QMenu()
        menu.addAction(app.applicationName(), self._on_show)
        menu.addSeparator()
        menu.addAction("Quit", self._on_quit)
        self._menu = menu
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_activated)

    def show(self) -> None:
        self._tray.show()

    def hide(self) -> None:
        self._tray.hide()

    def is_visible(self) -> bool:
        return self._tray.isVisible()

    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._on_show()

    def _on_show(self) -> None:
        self._show_window()

    def _on_quit(self) -> None:
        self._quit_app()
