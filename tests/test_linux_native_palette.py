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


def test_a_light_desktop_palette_wins_over_a_dark_colour_scheme_hint(app, monkeypatch):
    """One decision for tokens and palette, taken from what the desktop paints.

    Qt's colour-scheme hint and the theme's own palette come from different
    sources and can disagree. When the hint said dark over a light palette the
    app mixed both: dark cards and light text on a white window, white combo
    text in white fields, unreadable titles. The palette is what the reader is
    looking at, so it decides.
    """
    monkeypatch.setattr(dark, "IS_LINUX", True)
    monkeypatch.setattr(dark, "is_system_dark_mode", lambda: True)
    state = dark._ThemeState()
    light = _palette("#fcfcfc", "#232627")

    assert state.detect(native_palette=light) is False
    assert dark._palette_is_dark(light) is False
    assert QColor(state.tokens.bg).lightness() > 200
    assert QColor(state.tokens.fg).lightness() < 80
    # The palette the app is given agrees with the tokens.
    built = dark._make_palette(state.is_dark, native_palette=light)
    assert QColor(built.color(QPalette.ColorRole.Window)).lightness() > 200
    assert dark.contrast_ratio(
        built.color(QPalette.ColorRole.Text), built.color(QPalette.ColorRole.Base)
    ) >= 4.5

    # And a dark palette under a light hint stays dark, for the same reason.
    monkeypatch.setattr(dark, "is_system_dark_mode", lambda: False)
    state = dark._ThemeState()
    assert state.detect(native_palette=_palette("#1e2227", "#f0f0f0")) is True
    assert QColor(state.tokens.bg).lightness() < 80


def test_breeze_style_palette_never_outranks_the_host_palette(app, monkeypatch):
    """Plasma paints with the palette it handed this app, not the style's default.

    ``QStyle.standardPalette()`` comes from the style's own scheme, which can
    disagree with the scheme the platform theme applied (per-app overrides,
    a kdeglobals left behind by another theme). Preferring the style default
    painted dark cards, dark sidebar rows and dark-on-dark titles on a white
    window whose combo fields stayed white.
    """

    class _Style:
        def objectName(self):
            return "breeze"

        def standardPalette(self):
            return _palette("#24272c", "#f1f1f2")

    class _App:
        def style(self):
            return _Style()

    state = dark._ThemeState()
    monkeypatch.setattr(dark, "_state", state)
    monkeypatch.setattr(dark, "IS_LINUX", True)
    monkeypatch.setattr(dark, "is_system_dark_mode", lambda: True)

    state.platform_palette = _palette("#eff0f1", "#232627")
    chosen = dark._linux_system_palette(_App())
    assert dark._palette_is_dark(chosen) is False

    state.detect(native_palette=chosen)
    assert state.is_dark is False
    assert QColor(state.tokens.bg).lightness() > 200
    assert QColor(state.tokens.fg).lightness() < 80


def test_the_host_palette_is_snapshotted_before_the_app_replaces_it(app, monkeypatch):
    """The app palette is ours after the first apply, so the host one is kept."""
    state = dark._ThemeState()
    monkeypatch.setattr(dark, "_state", state)
    monkeypatch.setattr(dark, "IS_LINUX", True)

    original = QPalette(app.palette())
    try:
        app.setPalette(_palette("#eff0f1", "#232627"))
        dark._capture_platform_palette(app)
        assert state.platform_palette is not None
        assert state.platform_palette.color(QPalette.ColorRole.Window).name() == "#eff0f1"

        # A later call sees the derived palette and must not adopt it.
        app.setPalette(_palette("#24272c", "#f1f1f2"))
        dark._capture_platform_palette(app)
        assert state.platform_palette.color(QPalette.ColorRole.Window).name() == "#eff0f1"
    finally:
        app.setPalette(original)


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


def test_open_combo_rows_use_the_theme_font_colour(app, monkeypatch):
    # Desktop schemes can put a plain white/black value in QPalette::Text (the
    # colour for text inside Base surfaces) while the theme's font colour lives
    # in WindowText/ButtonText. The open menu must read like the closed combo.
    palette = _palette("#263626", "#d5e8b2")
    palette.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    monkeypatch.setattr(dark, "IS_LINUX", True)
    state = dark._ThemeState()
    monkeypatch.setattr(dark, "_state", state)
    state.set_dark(True, palette=palette)

    combo = QComboBox()
    combo.addItems(["one", "two"])
    view = combo.view()
    dark.configure_combo_view_palette(view)
    palette = view.palette()
    assert palette.color(QPalette.ColorRole.Text).name() == "#d5e8b2"
    assert palette.color(QPalette.ColorRole.WindowText).name() == "#d5e8b2"
    # The row surface is the list background, never the text colour.
    assert palette.color(QPalette.ColorRole.Window).name() == "#263626"
    assert palette.color(QPalette.ColorRole.Base).name() == "#263626"
    # The selected row still uses the desktop's own selection pair.
    assert palette.color(QPalette.ColorRole.Highlight) == app.palette().color(QPalette.ColorRole.Highlight)


def test_combo_menu_rows_keep_the_theme_font_colour(app):
    """The open menu reads like every label, not in the scheme's raw Text colour."""
    from src.ui.glass import menu_palette

    dark_host = _palette("#263626", "#d5e8b2")
    dark_host.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    rows = menu_palette(dark_host, frost=False)
    assert rows.color(QPalette.ColorRole.Text).name() == "#d5e8b2"
    assert rows.color(QPalette.ColorRole.WindowText).name() == "#d5e8b2"

    light_host = _palette("#f0ead6", "#3b2f10")
    light_host.setColor(QPalette.ColorRole.Text, QColor("#ffffff"))
    rows = menu_palette(light_host, frost=False)
    assert rows.color(QPalette.ColorRole.Text).name() == "#3b2f10"


def test_combo_menu_rows_stay_readable_when_the_font_colour_is_invisible(app):
    from src.ui.glass import menu_palette

    host = _palette("#263626", "#263626")
    rows = menu_palette(host, frost=False)
    text = rows.color(QPalette.ColorRole.Text)
    assert dark.contrast_ratio(text, rows.color(QPalette.ColorRole.Base)) >= 4.5


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
