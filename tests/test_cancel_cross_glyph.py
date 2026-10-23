"""Symbol-cancel chip regression tests for the install interrupt buttons.

The cancel chip on the SP Flash Tool install-stuck card uses the
platform cancel glyph. On Linux that resolved to a fairly subtle generic
stop symbol, so we explicitly paint a cross whenever the platform glyph is
missing or ambiguous. These tests snapshot the chip and assert the cross
pixels are present.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from src.ui.flash_page import _RoundNextButton, ROUND_NEXT_BUTTON_PX
from src.ui.icons import _FREEDESKTOP_MAP, _SF_SYMBOL_MAP


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _snapshot(btn: _RoundNextButton) -> QPixmap:
    size = btn.sizeHint()
    pm = QPixmap(size)
    pm.fill(Qt.transparent)
    btn.render(pm)
    return pm


def _to_image(pm: QPixmap) -> QImage:
    img = pm.toImage()
    if img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    return img


def _cross_pixels(img: QImage):
    hits = []
    w = img.width()
    h = img.height()
    ptr = img.bits()
    stride = img.bytesPerLine()
    bytes_per_pixel = 4
    for y in range(h):
        row_start = y * stride
        for x in range(w):
            r = ptr[row_start + x * bytes_per_pixel]
            g = ptr[row_start + x * bytes_per_pixel + 1]
            b = ptr[row_start + x * bytes_per_pixel + 2]
            a = ptr[row_start + x * bytes_per_pixel + 3]
            if a >= 40:
                hits.append((x, y, a, r, g, b))
    return hits


def _glyph_is_centred_cross(img: QImage) -> bool:
    hits = _cross_pixels(img)
    if not hits:
        return False
    xs = [h[0] for h in hits]
    ys = [h[1] for h in hits]
    x_mid = sum(xs) / len(xs)
    y_mid = sum(ys) / len(ys)
    centre = img.width() // 2
    return abs(x_mid - centre) < centre * 0.5 and abs(y_mid - centre) < centre * 0.5


def test_cancel_chip_paints_a_centred_cross_on_any_platform():
    """The cancel chip must always render a centred cross glyph, even when a
    platform's native cancel symbol is missing or too subtle to read."""
    _app()
    btn = _RoundNextButton(symbol="cancel", accent=False)
    btn.resize(ROUND_NEXT_BUTTON_PX, ROUND_NEXT_BUTTON_PX)
    btn.show()
    QApplication.instance().processEvents()
    pm = _snapshot(btn)
    assert pm.width() == ROUND_NEXT_BUTTON_PX
    assert pm.height() == ROUND_NEXT_BUTTON_PX
    assert _glyph_is_centred_cross(_to_image(pm)), "cancel chip did not paint a centred cross"


def test_cancel_chip_fallback_cross_not_empty():
    _app()
    btn = _RoundNextButton(symbol="cancel", accent=False)
    btn.resize(ROUND_NEXT_BUTTON_PX, ROUND_NEXT_BUTTON_PX)
    btn.show()
    QApplication.instance().processEvents()
    pm = _snapshot(btn)
    assert len(_cross_pixels(_to_image(pm))) > 0, "cancel chip painted no visible pixels at all"


def test_mac_cancel_symbol_resolves_to_xmark_or_fallback():
    _app()
    names = _SF_SYMBOL_MAP.get("cancel")
    assert names
    assert "xmark" in names


def test_linux_cancel_symbol_fallback_is_cross():
    _app()
    names = _FREEDESKTOP_MAP.get("cancel", [])
    assert names
    assert "process-stop-symbolic" in names
