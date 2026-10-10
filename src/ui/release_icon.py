"""Squircle release icons and the on-disk cache used by the install UI."""

from __future__ import annotations

import math
import random
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, Qt, QThread, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from .. import paths
from ..release_icons import (
    default_icon_cache_dir,
    fetch_icon_bytes,
    icon_candidate_urls,
    icon_known_miss,
    load_first_icon_bytes,
    read_cached_icon,
    release_asset_urls,
    software_icon_urls,
    write_cached_icon,
)

CROSS_FADE_MS = 280

# Catalogue and install cards use a 40px squircle. The settings glyph fills
# 62% of that, which leaves even padding on every side.
SQUIRCLE_ICON_SIDE = 40
GLYPH_OF_SIDE = 0.62


def app_icon_path() -> Path | None:
    for base in (paths.RESOURCES_DIR, paths.REPO_ROOT / "assets"):
        candidate = base / "icon.png"
        if candidate.is_file():
            return candidate
    return None


def app_icon_pixmap() -> QPixmap:
    path = app_icon_path()
    if path is None:
        return QPixmap()
    pixmap = QPixmap(str(path))
    return pixmap if not pixmap.isNull() else QPixmap()


def _squircle_path(side: float) -> QPainterPath:
    """iOS-style superellipse. A plain rounded square is not this shape."""
    path = QPainterPath()
    steps = 64
    exponent = 4.0
    points = []
    for index in range(steps):
        angle = 2 * math.pi * index / steps
        cosine = math.cos(angle)
        sine = math.sin(angle)
        x = math.copysign(abs(cosine) ** (2.0 / exponent), cosine)
        y = math.copysign(abs(sine) ** (2.0 / exponent), sine)
        points.append((side * (0.5 + 0.48 * x), side * (0.5 + 0.48 * y)))
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.closeSubpath()
    return path


def glyph_box(side: int) -> tuple[int, int, int, int]:
    """Centered square for the settings glyph: x, y, width, height."""
    side = max(int(side), 1)
    glyph = max(16, int(round(side * GLYPH_OF_SIDE)))
    glyph = min(glyph, max(1, side - 4))
    origin = (side - glyph) // 2
    return origin, origin, glyph, glyph


def _ink_pixmap(source: QPixmap, target: int) -> QPixmap:
    """Crop to the painted glyph and scale that box, so font padding cannot shove it into a corner."""
    if source is None or source.isNull() or target < 1:
        return QPixmap()
    image = source.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    width, height = image.width(), image.height()
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            if image.pixelColor(x, y).alpha() > 24:
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
    if max_x < min_x:
        return QPixmap()
    crop = image.copy(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)
    pix = QPixmap.fromImage(crop)
    pix.setDevicePixelRatio(1.0)
    return pix.scaled(target, target, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def placeholder_settings_pixmap(size: int = SQUIRCLE_ICON_SIDE) -> QPixmap:
    """Accent squircle with a centered host settings glyph. Not the application icon.

    The fill is the system accent (a pink accent is a pink squircle). The glyph
    is white or black from the same luminance rule as other accent text.
    Windows uses Segoe Fluent Icons / MDL2 Settings (E713). macOS uses the
    SF Symbol gearshape. Linux and BSD use ``preferences-system``.
    """
    from .dark import get_native_accent_color
    from .icons import get_symbol_pixmap

    side = max(int(size), 1)
    accent, glyph_color = get_native_accent_color()
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(accent))
    painter.drawPath(_squircle_path(side))
    _x, _y, glyph_side, _h = glyph_box(side)
    raw = get_symbol_pixmap("settings", size=glyph_side, color=glyph_color)
    glyph = _ink_pixmap(raw, glyph_side)
    if glyph is not None and not glyph.isNull():
        # width() is device pixels. The ink pixmap is DPR 1, so this is the
        # logical size and the glyph sits in the middle of the squircle.
        painter.drawPixmap((side - glyph.width()) // 2, (side - glyph.height()) // 2, glyph)
    painter.end()
    return pixmap


def squircle_pixmap(source: QPixmap, size: int = 40, complete: bool = False) -> QPixmap:
    """Clip a real release icon to a squircle, or draw the settings placeholder.

    ``complete`` adds the green check badge on top of whichever image is shown.
    """
    side = max(int(size), 1)
    if source is None or source.isNull():
        pixmap = placeholder_settings_pixmap(side)
    else:
        pixmap = QPixmap(side, side)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setClipPath(_squircle_path(side))
        scaled = source.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter.drawPixmap((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
        painter.end()
    if complete:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        _paint_complete_badge(painter, side)
        painter.end()
    return pixmap


def _paint_complete_badge(painter: QPainter, side: int) -> None:
    """Green circle and a light check, drawn on the package icon itself."""
    badge = max(14, int(side * 0.42))
    origin = side - badge - 1
    painter.setPen(QPen(QColor("#ffffff"), max(1.0, badge / 12.0)))
    painter.setBrush(QColor("#22c55e"))
    painter.drawEllipse(origin, origin, badge, badge)
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor("#ffffff"), max(1.6, badge / 7.0), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    mark = QPainterPath()
    mark.moveTo(origin + badge * 0.28, origin + badge * 0.54)
    mark.lineTo(origin + badge * 0.44, origin + badge * 0.70)
    mark.lineTo(origin + badge * 0.74, origin + badge * 0.34)
    painter.drawPath(mark)


def cached_icon_pixmap(urls, cache_dir: Path | None = None) -> QPixmap:
    """First image already on disk for these URLs. Does not use the network."""
    directory = cache_dir or default_icon_cache_dir()
    for url in urls or []:
        pixmap = pixmap_from_icon_bytes(read_cached_icon(url, directory) or b"")
        if not pixmap.isNull():
            return pixmap
    return QPixmap()


class SoftwareIconJob(QThread):
    """Fetch the manifest software logo off the UI thread."""

    ready = Signal(bytes)

    def __init__(self, package, *, dark: bool = False, parent=None):
        super().__init__(parent)
        self._package = package
        self._dark = bool(dark)

    def run(self) -> None:
        from ..release_icons import load_first_icon_bytes, software_icon_urls

        data = load_first_icon_bytes(
            software_icon_urls(self._package, dark=self._dark),
            default_icon_cache_dir(),
            allow_network=True,
        )
        self.ready.emit(data or b"")


def pixmap_from_icon_bytes(data: bytes) -> QPixmap:
    """Decode icon bytes. A failed decode is a null pixmap, never a broken image."""
    if not data:
        return QPixmap()
    image = QImage.fromData(data)
    if image.isNull():
        return QPixmap()
    pixmap = QPixmap.fromImage(image)
    return pixmap if not pixmap.isNull() else QPixmap()


def load_release_pixmap(release, package=None, *, allow_network: bool = True, cache_dir: Path | None = None, dark: bool | None = None) -> QPixmap:
    """First real release icon, or a null pixmap so the caller draws the squircle.

    URLs are built from owner, repo, and tag. A null result is the settings
    squircle, not the application icon.
    """
    if dark is None:
        try:
            from .dark import is_dark

            dark = bool(is_dark())
        except Exception:
            dark = False
    directory = cache_dir or default_icon_cache_dir()
    for url in icon_candidate_urls(release, package, dark=dark):
        data = read_cached_icon(url, directory)
        if data is None and allow_network:
            data = fetch_icon_bytes(url)
            if data:
                write_cached_icon(url, data, directory)
        pixmap = pixmap_from_icon_bytes(data or b"")
        if not pixmap.isNull():
            return pixmap
    return QPixmap()


def blend_pixmaps(base: QPixmap, incoming: QPixmap, amount: float) -> QPixmap:
    """Cross-fade. ``amount`` 0 is the stand-in; 1 is the release image."""
    amount = max(0.0, min(1.0, float(amount)))
    if base is None or base.isNull():
        return incoming if incoming is not None else QPixmap()
    if incoming is None or incoming.isNull() or amount <= 0.0:
        return base
    if amount >= 1.0:
        return incoming
    canvas = QPixmap(base.size())
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setOpacity(1.0 - amount)
    painter.drawPixmap(0, 0, base)
    painter.setOpacity(amount)
    painter.drawPixmap(0, 0, incoming)
    painter.end()
    return canvas


class FadingIcon(QWidget):
    """Shows a stand-in, then cross-fades to the release image. No hard cut."""

    def __init__(self, size: int = SQUIRCLE_ICON_SIDE, parent=None):
        super().__init__(parent)
        self._size = int(size)
        self.setFixedSize(self._size, self._size)
        self._shown = QPixmap()
        self._base = QPixmap()
        self._over = QPixmap()
        self._amount = 1.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(CROSS_FADE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._anim.valueChanged.connect(self._on_amount)
        self._anim.finished.connect(self._on_finished)

    def show_stand_in(self, pixmap: QPixmap) -> None:
        self._anim.stop()
        self._shown = pixmap if pixmap is not None else QPixmap()
        self._base = self._shown
        self._over = QPixmap()
        self._amount = 1.0
        self.update()

    def setPixmap(self, pixmap: QPixmap) -> None:
        self.show_stand_in(pixmap)

    def cross_fade_to(self, pixmap: QPixmap) -> None:
        if pixmap is None or pixmap.isNull():
            return
        if self._shown.isNull():
            self.show_stand_in(pixmap)
            return
        if not self._over.isNull() and self._amount >= 1.0 and pixmap.cacheKey() == self._shown.cacheKey():
            return
        if pixmap.cacheKey() == self._shown.cacheKey() and self._over.isNull():
            return
        self._base = self.pixmap()
        self._over = pixmap
        self._amount = 0.0
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self.update()

    def blend_amount(self) -> float:
        return float(self._amount)

    def finish_cross_fade(self) -> None:
        self._on_amount(1.0)
        self._on_finished()

    def pixmap(self) -> QPixmap:
        if self._over.isNull() or self._amount >= 1.0:
            return self._shown if not self._shown.isNull() else self._base
        return blend_pixmaps(self._base, self._over, self._amount)

    def _on_amount(self, value) -> None:
        self._amount = float(value)
        self.update()

    def _on_finished(self) -> None:
        if not self._over.isNull():
            self._shown = self._over
            self._base = self._over
            self._over = QPixmap()
            self._amount = 1.0
            self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        frame = self.pixmap()
        if not frame.isNull():
            painter.drawPixmap(0, 0, frame)
        painter.end()


class ReleaseIconJob(QThread):
    """Download release icons off the UI thread, one at a time, with jitter.

    Emits the tag and the release-file bytes. The software logo is not treated
    as a release icon. A cached file is emitted immediately. A known 404 is skipped.
    """

    ready = Signal(str, bytes)

    def __init__(self, releases, package=None, *, dark: bool = False, priority_tag: str = "", cache_dir: Path | None = None, rng=None, parent=None):
        super().__init__(parent)
        self._releases = list(releases or [])
        self._package = package
        self._dark = bool(dark)
        self._priority = str(priority_tag or "")
        self._cache_dir = cache_dir
        self._rng = rng
        self.gaps: list[float] = []

    def promote(self, tag: str) -> None:
        """Move a selected release to the front of the remaining queue."""
        self._priority = str(tag or "")

    def _sleep_gap(self, delay: float, current_tag: str) -> str:
        remaining = float(delay)
        while remaining > 0:
            if self.isInterruptionRequested():
                return "stop"
            if self._priority and self._priority != current_tag:
                return "jump"
            step = min(0.05, remaining)
            time.sleep(step)
            remaining -= step
        return "ok"

    def run(self):
        import random

        from .. import release_icons as icons

        pending = [rel for rel in self._releases if rel]
        rng = self._rng or random.Random()
        directory = self._cache_dir or icons.default_icon_cache_dir()
        network_started = False
        while pending:
            if self.isInterruptionRequested():
                return
            priority = self._priority
            if priority:
                pending.sort(
                    key=lambda rel: 0 if str((rel or {}).get("tag_name") or "") == priority else 1,
                )
            rel = pending.pop(0)
            tag = str((rel or {}).get("tag_name") or "")
            urls = icons.release_asset_urls(rel, self._package, dark=self._dark)
            data = b""
            for url in urls:
                cached = icons.read_cached_icon(url, directory)
                if cached:
                    data = cached
                    break
            if data:
                self.ready.emit(tag, data)
                continue
            jumped = False
            for url in urls:
                if self.isInterruptionRequested():
                    return
                if icons.icon_known_miss(url, directory):
                    continue
                if network_started:
                    gap = icons.next_preload_gap(rng)
                    self.gaps.append(gap)
                    paused = self._sleep_gap(gap, tag)
                    if paused == "stop":
                        return
                    if paused == "jump":
                        pending.insert(0, rel)
                        jumped = True
                        break
                network_started = True
                fetched, code = icons.fetch_icon_status(url)
                if fetched:
                    icons.write_cached_icon(url, fetched, directory)
                    data = fetched
                    break
                if code == 404:
                    icons.remember_icon_miss(url, directory)
            if not jumped:
                self.ready.emit(tag, data)
