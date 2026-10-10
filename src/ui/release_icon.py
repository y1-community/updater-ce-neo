"""Squircle release icons and the on-disk cache used by the install UI."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap

from .. import paths
from ..release_icons import (
    default_icon_cache_dir,
    fetch_icon_bytes,
    icon_candidate_urls,
    read_cached_icon,
    write_cached_icon,
)


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


def placeholder_settings_pixmap(size: int = 40) -> QPixmap:
    """Theme squircle with the host settings glyph. Not the application icon.

    Windows uses Segoe Fluent Icons / MDL2 Settings (E713). macOS uses the
    SF Symbol gearshape when the system font has it. Linux and BSD use the
    FreeDesktop name ``preferences-system`` (then ``emblem-system``).
    """
    from .dark import is_dark
    from .icons import get_symbol_pixmap

    side = max(int(size), 1)
    dark = bool(is_dark())
    fill = QColor("#2c3038" if dark else "#e6e8ee")
    glyph_color = "#f5f7fa" if dark else "#1a1d23"
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(fill)
    painter.drawPath(_squircle_path(side))
    glyph_side = max(12, int(side * 0.5))
    glyph = get_symbol_pixmap("settings", size=glyph_side, color=glyph_color)
    if glyph is not None and not glyph.isNull():
        painted = glyph.scaled(
            glyph_side, glyph_side, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        painter.drawPixmap((side - painted.width()) // 2, (side - painted.height()) // 2, painted)
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
    painter.setPen(QPen(QColor("#ffffff"), max(1.6, badge / 7.0), Qt.RoundCap, Qt.RoundJoin))
    mark = QPainterPath()
    mark.moveTo(origin + badge * 0.28, origin + badge * 0.54)
    mark.lineTo(origin + badge * 0.44, origin + badge * 0.70)
    mark.lineTo(origin + badge * 0.74, origin + badge * 0.34)
    painter.drawPath(mark)


def load_release_pixmap(release, package=None, *, allow_network: bool = True, cache_dir: Path | None = None) -> QPixmap:
    """First real release icon, or a null pixmap when none is specified.

    ``updater.png`` and the manifest ``icon`` are the only sources. A null
    result is the settings squircle, not the application icon.
    """
    directory = cache_dir or default_icon_cache_dir()
    for url in icon_candidate_urls(release, package):
        data = read_cached_icon(url, directory)
        if data is None and allow_network:
            data = fetch_icon_bytes(url)
            if data:
                write_cached_icon(url, data, directory)
        if not data:
            continue
        image = QImage.fromData(data)
        if image.isNull():
            continue
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            return pixmap
    return QPixmap()
