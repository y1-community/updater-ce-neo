"""Squircle release icons and the on-disk cache used by the install UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap

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


def squircle_pixmap(source: QPixmap, size: int = 40, complete: bool = False) -> QPixmap:
    """Clip ``source`` to a rounded square. A check badge is drawn when complete."""
    side = max(int(size), 1)
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    rect = QRectF(0, 0, side, side)
    path = QPainterPath()
    path.addRoundedRect(rect, side * 0.22, side * 0.22)
    painter.setClipPath(path)
    if source is not None and not source.isNull():
        scaled = source.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        x = (side - scaled.width()) // 2
        y = (side - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)
    else:
        painter.fillRect(rect, QColor("#888888"))
    painter.setClipping(False)
    if complete:
        _paint_complete_badge(painter, side)
    painter.end()
    return pixmap


def _paint_complete_badge(painter: QPainter, side: int) -> None:
    from .dark import T
    from .icons import get_symbol_pixmap

    badge = max(14, int(side * 0.42))
    glyph = get_symbol_pixmap("complete", size=badge, color="#ffffff")
    origin = side - badge
    painter.setBrush(QColor(getattr(T(), "ok_fg", None) or getattr(T(), "accent", "#34c759")))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(origin, origin, badge, badge)
    if glyph is not None and not glyph.isNull():
        painter.drawPixmap(origin, origin, glyph.scaled(badge, badge, Qt.KeepAspectRatio, Qt.SmoothTransformation))


def load_release_pixmap(release, package=None, *, allow_network: bool = True, cache_dir: Path | None = None) -> QPixmap:
    """First icon that loads, else the app icon. Network failures fall through."""
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
    return app_icon_pixmap()
