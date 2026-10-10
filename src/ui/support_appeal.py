"""Short support headline: a round portrait beside "It takes you."

The portrait is circle-cropped. A bundled copy is shown immediately, and the
network image replaces it when it can be loaded. Nothing here paints a solid
plate; the window material shows through the square outside the circle.
"""

from __future__ import annotations

import os
import urllib.request

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QFont, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .. import paths
from ..i18n import tr

PORTRAIT_URL = "https://innioasis.app/mtkclient/gui/images/developer.png"
PORTRAIT_DIAMETER = 56


def bundled_portrait_path():
    """Shipped portrait, used when the network image cannot be loaded."""
    return paths.RESOURCES_DIR / "developer.png"


def circle_pixmap(source: QPixmap, diameter: int = PORTRAIT_DIAMETER) -> QPixmap:
    """Scale, center-crop, and clip to a circle on a transparent square."""
    diameter = max(1, int(diameter))
    image = QImage(diameter, diameter, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    if source is not None and not source.isNull():
        scaled = source.scaled(
            diameter,
            diameter,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = max(0, (scaled.width() - diameter) // 2)
        y = max(0, (scaled.height() - diameter) // 2)
        cropped = scaled.copy(x, y, diameter, diameter)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        clip = QPainterPath()
        clip.addEllipse(0, 0, diameter, diameter)
        painter.setClipPath(clip)
        painter.drawPixmap(0, 0, cropped)
        painter.end()
    return QPixmap.fromImage(image)


class _PortraitFetch(QThread):
    finished_bytes = Signal(bytes)

    def run(self) -> None:
        if self.isInterruptionRequested():
            self.finished_bytes.emit(b"")
            return
        try:
            request = urllib.request.Request(
                PORTRAIT_URL,
                headers={"User-Agent": "UpdaterCE"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read()
        except Exception:
            data = b""
        if self.isInterruptionRequested():
            data = b""
        self.finished_bytes.emit(data)


class DeveloperPortrait(QLabel):
    """Circular developer photo. Bundled file first, network image when it arrives."""

    def __init__(self, diameter: int = PORTRAIT_DIAMETER, parent=None):
        super().__init__(parent)
        self._diameter = int(diameter)
        self._thread = None
        self.setFixedSize(self._diameter, self._diameter)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        self.setPixmap(circle_pixmap(QPixmap(str(bundled_portrait_path())), self._diameter))
        self.destroyed.connect(self._abandon)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._stop_fetch)
        self._maybe_fetch()

    def apply_bytes(self, data: bytes) -> bool:
        """Replace the portrait when ``data`` is an image. Keeps the bundled one otherwise."""
        if not data:
            return False
        pix = QPixmap()
        if not pix.loadFromData(data) or pix.isNull():
            return False
        self.setPixmap(circle_pixmap(pix, self._diameter))
        return True

    def _maybe_fetch(self) -> None:
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        thread = _PortraitFetch()
        thread.finished_bytes.connect(self.apply_bytes)
        self._thread = thread
        thread.start()

    def _abandon(self, *_args) -> None:
        thread = self._thread
        if thread is None:
            return
        try:
            thread.finished_bytes.disconnect(self.apply_bytes)
        except Exception:
            pass
        thread.requestInterruption()

    def _stop_fetch(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is None:
            return
        thread.requestInterruption()
        if thread.isRunning():
            thread.wait(2000)


class SupportIntro(QWidget):
    """Portrait aligned with the headline block. No card behind it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAutoFillBackground(False)
        self.setStyleSheet("")
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.portrait = DeveloperPortrait(PORTRAIT_DIAMETER)
        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.headline = QLabel()
        self.headline.setTextFormat(Qt.TextFormat.RichText)
        self.headline.setWordWrap(True)
        self.headline.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        font = self.headline.font()
        font.setPixelSize(18)
        font.setWeight(QFont.Weight.DemiBold)
        self.headline.setFont(font)
        self.headline.setAutoFillBackground(False)
        self.headline.setStyleSheet("")
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setProperty("cssClass", "dimmed")
        self.subtitle.setAutoFillBackground(False)
        column.addWidget(self.headline)
        column.addWidget(self.subtitle)
        row.addWidget(self.portrait, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addLayout(column, 1)
        self.retranslate()

    def retranslate(self) -> None:
        self.headline.setText(tr("donate_headline"))
        self.subtitle.setText(tr("donate_subtitle"))
