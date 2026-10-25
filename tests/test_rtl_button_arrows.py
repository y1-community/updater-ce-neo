"""Directional chip glyphs mirror in right-to-left layouts.

Qt mirrors a button *row* in RTL (the back chip lands at the row's right
edge), but a rendered arrow does not mirror itself: a left-pointing back
arrow then aims inwards at the next chip, so both arrows point at each other
and the actions read the wrong way round. The directional glyphs must flip
with the layout direction; tooltips and accessibility names keep their real
meaning.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.ui.flash_page import _RoundNextButton


def _img(pixmap):
    return pixmap.toImage()


def _chip(symbol, direction):
    btn = _RoundNextButton(symbol=symbol, accent=True)
    btn.setLayoutDirection(direction)
    btn.rebuild_arrow()
    return btn


def test_back_arrow_mirrors_in_rtl_and_keeps_its_glyph_in_ltr():
    app = QApplication.instance() or QApplication(sys.argv)

    ltr_back = _chip("back", Qt.LayoutDirection.LeftToRight)
    rtl_back = _chip("back", Qt.LayoutDirection.RightToLeft)
    rtl_next = _chip("next", Qt.LayoutDirection.RightToLeft)
    ltr_next = _chip("next", Qt.LayoutDirection.LeftToRight)

    assert not ltr_back._arrow.isNull()
    # RTL flips the directional glyph: a back chip points the way "next"
    # draws in LTR, i.e. away from the mirrored row instead of inwards.
    assert _img(rtl_back._arrow) == _img(ltr_next._arrow)
    assert _img(rtl_next._arrow) == _img(ltr_back._arrow)
    # LTR is untouched.
    assert _img(ltr_back._arrow) != _img(ltr_next._arrow)

    # The meaning of the button never flips with its glyph.
    assert rtl_back.toolTip() == ltr_back.toolTip()
    assert rtl_back.accessibleName() == ltr_back.accessibleName()
