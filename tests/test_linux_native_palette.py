"""Linux tokens follow the desktop's own palette, whatever its colours."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

import src.ui.dark as dark


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    return instance


def _palette(window: str, text: str) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(window))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(text))
    palette.setColor(QPalette.ColorRole.Base, QColor(window))
    palette.setColor(QPalette.ColorRole.Button, QColor(window))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    return palette


def test_palette_darkness_follows_window_colour_not_a_setting(app):
    assert dark._palette_is_dark(_palette("#1e2227", "#ffffff")) is True
    assert dark._palette_is_dark(_palette("#f0ead6", "#222222")) is False


def test_light_and_dark_palettes_produce_their_own_window_surface(app):
    for window in ("#f0ead6", "#1e2227", "#3b5b3a"):
        pal = _palette(window, "#101010" if QColor(window).lightness() > 128 else "#f0f0f0")
        tokens = dark._Tokens(
            dark._palette_is_dark(pal), palette=pal, use_native_linux_colors=True,
        )
        assert tokens.bg == QColor(window).name()


def test_status_banners_are_mixed_into_the_desktop_window(app):
    pal = _palette("#3b5b3a", "#f0f0f0")
    tokens = dark._Tokens(True, palette=pal, use_native_linux_colors=True)
    # The banner background is the status hue mixed onto the window colour, so
    # it is neither the fixed light-theme plate nor the fixed dark-theme plate.
    assert tokens.ok_bg not in ("#d1fae5", "#064e3b")
    assert tokens.danger_bg not in ("#fee2e2", "#7f1d1d")
    assert tokens.progress_ok == "#10b981"
