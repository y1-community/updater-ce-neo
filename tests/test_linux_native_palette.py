"""Linux tokens follow the desktop's own palette, whatever its colours."""

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QComboBox

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
    palette.setColor(QPalette.ColorRole.Text, QColor(text))
    palette.setColor(QPalette.ColorRole.Button, QColor(window))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#7c3aed"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#f2d72f"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9eaa89"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#777777"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#666666"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#555555"))
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


def test_custom_foreground_roles_reach_tokens_and_combobox(app, monkeypatch):
    palette = _palette("#263626", "#d5e8b2")
    monkeypatch.setattr(dark, "IS_LINUX", True)
    tokens = dark._Tokens(True, palette=palette, use_native_linux_colors=True)
    assert tokens.fg == "#d5e8b2"
    assert tokens.fg_dim == "#d5e8b2"
    assert tokens.fg_muted == "#9eaa89"
    assert tokens.disabled_fg == "#777777"
    assert tokens._linux_combo_button_text == "#d5e8b2"

    state = dark._ThemeState()
    monkeypatch.setattr(dark, "_state", state)
    monkeypatch.setattr(dark, "_palette_is_dark", lambda _palette: True)
    state.set_dark(True, palette=palette)
    combo = QComboBox()
    dark.configure_combo_palette(combo)
    assert combo.palette().color(QPalette.ColorRole.WindowText).name() == "#d5e8b2"
    assert combo.palette().color(QPalette.ColorRole.Text).name() == "#d5e8b2"
    assert combo.palette().color(QPalette.ColorRole.ButtonText).name() == "#d5e8b2"


def test_native_palette_builder_preserves_text_and_selection_roles(app, monkeypatch):
    palette = _palette("#263626", "#d5e8b2")
    monkeypatch.setattr(dark, "IS_LINUX", True)
    monkeypatch.setattr(dark, "_palette_is_dark", lambda _palette: True)
    result = dark._make_palette(True, native_palette=palette)
    assert result.color(QPalette.ColorRole.WindowText) == palette.color(QPalette.ColorRole.WindowText)
    assert result.color(QPalette.ColorRole.Text) == palette.color(QPalette.ColorRole.Text)
    assert result.color(QPalette.ColorRole.ButtonText) == palette.color(QPalette.ColorRole.ButtonText)
    assert result.color(QPalette.ColorRole.HighlightedText) == palette.color(QPalette.ColorRole.HighlightedText)


def test_bright_recolor_preserves_dark_illustration_details(app):
    from src.ui.icons import recolor_bright_pixels

    source = QPixmap(3, 1)
    source.fill(Qt.transparent)
    image = source.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    image.setPixelColor(0, 0, QColor("#ffffff"))
    image.setPixelColor(1, 0, QColor("#f0f0f0"))
    image.setPixelColor(2, 0, QColor("#000000"))
    result = recolor_bright_pixels(QPixmap.fromImage(image), "#d5e8b2").toImage()
    assert result.pixelColor(0, 0).name() == "#d5e8b2"
    assert result.pixelColor(1, 0).red() >= 212
    assert result.pixelColor(1, 0).green() >= 231
    assert result.pixelColor(1, 0).blue() >= 176
    assert result.pixelColor(2, 0).name() == "#000000"
    assert result.pixelColor(2, 0).alpha() == 255

    edge = QPixmap(1, 1)
    edge_image = edge.toImage().convertToFormat(QImage.Format.Format_ARGB32)
    edge_image.setPixelColor(0, 0, QColor(220, 220, 220, 128))
    edge_pixel = recolor_bright_pixels(QPixmap.fromImage(edge_image), "#d5e8b2").toImage().pixelColor(0, 0)
    assert 0 < edge_pixel.alpha() < 255


def test_status_banners_are_mixed_into_the_desktop_window(app):
    pal = _palette("#3b5b3a", "#f0f0f0")
    tokens = dark._Tokens(True, palette=pal, use_native_linux_colors=True)
    # The banner background is the status hue mixed onto the desktop's own window.
    assert tokens.ok_bg not in ("#d1fae5", "#064e3b")
    assert tokens.danger_bg not in ("#fee2e2", "#7f1d1d")
    assert tokens.progress_ok == "#10b981"
