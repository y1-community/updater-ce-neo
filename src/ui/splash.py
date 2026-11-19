"""The launch card a Windows start shows before the app window exists.

Creating the main window builds every page, loads the catalogue and prepares
the host chrome, which on Windows is long enough to look like a launch that did
nothing. A splash is the one piece of UI that can be painted before any of that
work starts, so it is the honest place to say what the app is doing.

The card is drawn, not assembled from widgets: it has no window frame to
inherit, must be correct on its very first frame, and has to survive every
display scale. It reads the app's own colour tokens (``dark.T``), so a light
desktop gets a light card and an accent-coloured desktop gets its own accent.
The text changes when the app was started from a portable firmware package:
that launch is already an install, and "Getting your firmware install ready"
says so.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QSplashScreen

logger = logging.getLogger(__name__)

# The card plus a transparent margin the drop shadow is painted into. The
# window is frameless and translucent, so only the rounded card is visible.
# Deliberately compact: the launch card is a status line, not a second app.
_CARD_W = 400.0
_CARD_H = 166.0
_MARGIN = 12.0
_WIDTH = int(_CARD_W + _MARGIN * 2)
_HEIGHT = int(_CARD_H + _MARGIN * 2)
_RADIUS = 14.0
_PAD = 24.0
_ICON = 44.0
_CREDIT = "Made by Ryan Specter"
_DEFAULT_TITLE = "Innioasis Updater"


def _tokens():
    """The active theme's tokens, with a dark fallback for an unthemed host."""
    try:
        from .dark import T

        return T()
    except Exception:
        return SimpleNamespace(
            bg="#1e2227",
            bg_card="#252932",
            bg_elev="#2b303c",
            fg="#ffffff",
            fg_muted="#e5e7eb",
            border="#3e4451",
            accent="#2563eb",
        )


def _app_name() -> str:
    try:
        from .. import config

        return str(config.get_app_name() or "Innioasis Updater")
    except Exception:
        return "Innioasis Updater"


def _version() -> str:
    try:
        from .. import config

        return str(getattr(config, "APP_VERSION", "") or "")
    except Exception:
        return ""


def _splash_title(portable_fw: bool, firmware_name: str = "") -> str:
    """The name on the launch card.

    A portable firmware run is already an install, so the card names the
    software from ``fw_package.ini``; a package without a name says what the
    run is about to do. An ordinary launch shows the product's public name, and
    the generic MediaTek Installer build keeps its own name as well.
    """
    if portable_fw:
        name = (firmware_name or "").strip()
        if name:
            return name
        try:
            from ..i18n import tr

            label = tr("startup_splash_install_firmware")
            if label and label != "startup_splash_install_firmware":
                return label
        except Exception:
            pass
        from ..i18n import software_word

        return f"Install {software_word()}"
    try:
        from .. import config

        if config.is_mediatek_installer():
            return _app_name()
    except Exception:
        pass
    return _DEFAULT_TITLE


def _splash_subtitle(portable_fw: bool, firmware_version: str = "") -> str:
    """The line under the title: the package's version during a portable run.

    The app's own version would read as the firmware's, so a portable launch
    shows only the version its package declares (and nothing when it declares
    none). An ordinary launch still shows the app version.
    """
    if portable_fw:
        version = (firmware_version or "").strip().lstrip("vV")
        return f"v{version}" if version else ""
    version = _version()
    return f"v{version}" if version else ""


def _app_icon() -> QPixmap | None:
    try:
        from .. import paths

        path = paths.RESOURCES_DIR / "icon.png"
        if path.is_file():
            icon = QPixmap(str(path))
            if not icon.isNull():
                return icon
    except Exception:
        logger.debug("Splash icon unavailable", exc_info=True)
    return None


def _mix(color: QColor, alpha: int) -> QColor:
    """*color* with a different alpha, without touching the token itself."""
    out = QColor(color)
    out.setAlpha(alpha)
    return out


class _StartupSplash(QSplashScreen):
    """The launch card: icon, name, version and one status line.

    A plain ``QSplashScreen`` only knows how to blit a pixmap and stamp a
    message onto it, which is what made the old splash look like a rectangle
    with two labels. Painting the whole card here keeps the type crisp at every
    scale and lets the rounded corner and shadow follow the host appearance.
    """

    def __init__(
        self,
        message: str,
        title: str | None = None,
        subtitle: str | None = None,
    ) -> None:
        canvas = QPixmap(_WIDTH, _HEIGHT)
        canvas.fill(Qt.GlobalColor.transparent)
        # Set the final window type and hints in the constructor. Re-applying
        # them after construction recreates the native splash window on
        # Windows, which showed up as a small extra window at launch.
        super().__init__(
            canvas,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint,
        )
        # Frameless + translucent is what makes the rounded corners real
        # instead of four black notches on the desktop.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._tokens = _tokens()
        self._message = message
        self._title = title or _app_name()
        # The title already names the product; repeat only the version here.
        self._subtitle = subtitle if subtitle is not None else _splash_subtitle(False)
        self._icon = _app_icon()

        base = QFont(QApplication.font())
        self._title_font = QFont(base)
        self._title_font.setPixelSize(21)
        self._title_font.setWeight(QFont.Weight.DemiBold)
        self._subtitle_font = QFont(base)
        self._subtitle_font.setPixelSize(13)
        self._status_font = QFont(base)
        self._status_font.setPixelSize(14)
        self._credit_font = QFont(base)
        self._credit_font.setPixelSize(12)

    # -- public -------------------------------------------------------------
    def set_status(self, message: str) -> None:
        """Change the status line without rebuilding the card."""
        self._message = message or ""
        self.update()

    # -- painting -----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt naming)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            self._paint(painter)
        except Exception:
            logger.debug("Startup splash could not paint", exc_info=True)
        finally:
            painter.end()

    def _paint(self, painter: QPainter) -> None:
        t = self._tokens
        card = QRectF(_MARGIN, _MARGIN, _CARD_W, _CARD_H)
        accent = QColor(t.accent)
        dark = QColor(t.bg).lightness() < 128

        self._paint_shadow(painter, card)

        path = QPainterPath()
        path.addRoundedRect(card, _RADIUS, _RADIUS)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(path, QColor(t.bg_elev))

        border = QColor(t.border)
        border.setAlpha(200 if dark else 255)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            card.adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS
        )

        icon_rect = QRectF(card.x() + _PAD, card.y() + 22.0, _ICON, _ICON)
        self._paint_icon(painter, icon_rect, accent)

        left = icon_rect.right() + 14.0
        right = card.right() - _PAD
        title_rect = QRectF(left, card.y() + 22.0, right - left, 28.0)
        painter.setFont(self._title_font)
        painter.setPen(QColor(t.fg))
        title = QFontMetrics(self._title_font).elidedText(
            self._title, Qt.TextElideMode.ElideRight, int(title_rect.width())
        )
        painter.drawText(
            title_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title,
        )

        subtitle_rect = QRectF(left, card.y() + 52.0, right - left, 18.0)
        painter.setFont(self._subtitle_font)
        painter.setPen(_mix(QColor(t.fg_muted), 170))
        painter.drawText(
            subtitle_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._subtitle,
        )

        status_rect = QRectF(
            card.x() + _PAD,
            card.y() + 92.0,
            card.width() - _PAD * 2.0,
            24.0,
        )
        painter.setFont(self._status_font)
        painter.setPen(_mix(QColor(t.fg_muted), 215))
        message = QFontMetrics(self._status_font).elidedText(
            self._message, Qt.TextElideMode.ElideRight, int(status_rect.width())
        )
        painter.drawText(
            status_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            message,
        )

        credit_rect = QRectF(
            card.x() + _PAD,
            card.y() + _CARD_H - 36.0,
            card.width() - _PAD * 2.0,
            18.0,
        )
        painter.setFont(self._credit_font)
        painter.setPen(_mix(QColor(t.fg_muted), 170))
        painter.drawText(
            credit_rect,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            _CREDIT,
        )

    def _paint_shadow(self, painter: QPainter, card: QRectF) -> None:
        """A soft shadow painted into the transparent margin.

        Windows gives a translucent frameless window no shadow of its own, and a
        splash with a hard edge looks like a stray rectangle over the desktop.
        Stacked low-alpha rings are enough at this size and cost nothing.
        """
        painter.setPen(Qt.PenStyle.NoPen)
        steps = int(_MARGIN)
        for i in range(steps, 0, -1):
            alpha = int(7 * (1.0 - i / float(steps)) ** 2)
            if alpha <= 0:
                continue
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(
                card.adjusted(-i, -i, i, i), _RADIUS + i, _RADIUS + i
            )

    def _paint_icon(self, painter: QPainter, rect: QRectF, accent: QColor) -> None:
        if self._icon is not None and not self._icon.isNull():
            # Draw the app's own artwork directly; an extra rounded plate around
            # it just adds a second, larger icon shape.
            icon = self._icon.scaled(
                int(rect.width()),
                int(rect.height()),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            target = QRectF(0.0, 0.0, icon.width(), icon.height())
            target.moveCenter(rect.center())
            painter.drawPixmap(target, icon, QRectF(icon.rect()))
            return
        # No artwork installed (a stripped build): the app initial in the accent
        # colour, without inventing a placeholder icon plate.
        font = QFont(self._title_font)
        font.setPixelSize(26)
        painter.setFont(font)
        painter.setPen(accent)
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignCenter), self._title[:1].upper()
        )


def show_startup_splash(
    portable_fw: bool = False,
    firmware_name: str = "",
    firmware_version: str = "",
) -> QSplashScreen:
    """Show the launch splash and return it, so the caller can finish it.

    The caller closes it with ``finish(window)`` once the real window exists.
    Nothing here may raise into the launch: a host without the pieces the
    splash wants is still a host that must start the app.
    """
    from ..i18n import tr

    splash = _StartupSplash(
        tr("startup_splash_firmware" if portable_fw else "startup_splash_ready"),
        title=_splash_title(portable_fw, firmware_name),
        subtitle=_splash_subtitle(portable_fw, firmware_version),
    )

    # Open on the display the user is actually looking at, centred, rather than
    # wherever Qt's default placement lands.
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        splash.move(geometry.center() - splash.rect().center())

    splash.show()
    try:
        QApplication.processEvents()
    except Exception:
        pass
    return splash
