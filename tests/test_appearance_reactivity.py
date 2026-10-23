"""System appearance changes reach every widget, icon, and text live."""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PySide6.QtGui import QColor, QPalette, QPixmap
from PySide6.QtCore import Qt


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_linux_palette_fingerprint_tracks_same_luminance_theme(monkeypatch):
    """The Linux poll sees full host-palette changes, not only light/dark flips."""
    from src.ui import dark

    app = _app()
    original_linux = dark.IS_LINUX
    original_state = (
        dark._state.native_palette,
        getattr(dark._state, "linux_palette_from_event", False),
        dark._state.tokens,
        dark._state._dark,
    )
    watcher = None
    palette = QPalette(app.style().standardPalette())
    try:
        monkeypatch.setattr(dark, "IS_LINUX", True)
        for role, color in (
            (QPalette.ColorRole.Window, "#343a40"),
            (QPalette.ColorRole.WindowText, "#f4f5f6"),
            (QPalette.ColorRole.Base, "#282d33"),
            (QPalette.ColorRole.Text, "#f0f1f2"),
            (QPalette.ColorRole.Highlight, "#3875a8"),
        ):
            palette.setColor(role, QColor(color))
        dark._state.native_palette = QPalette(palette)
        before = dark.theme_fingerprint(app, platform_dark=False)
        watcher = dark.ThemeWatcher(app)
        watcher._installed = True

        # Same luminance class, but the window, foreground, and accent all vary.
        for role, color in (
            (QPalette.ColorRole.Window, "#3a3434"),
            (QPalette.ColorRole.WindowText, "#f6f0f0"),
            (QPalette.ColorRole.Base, "#302828"),
            (QPalette.ColorRole.Text, "#f2ecec"),
            (QPalette.ColorRole.Highlight, "#a85038"),
        ):
            palette.setColor(role, QColor(color))
        watcher._on_palette_changed(palette)
        after = dark.theme_fingerprint(app, platform_dark=False)
        assert before != after
        watcher.poll()
        assert watcher._debounce.isActive()
        watcher.stop()
    finally:
        if watcher is not None:
            watcher.stop()
        (
            dark._state.native_palette,
            dark._state.linux_palette_from_event,
            dark._state.tokens,
            dark._state._dark,
        ) = original_state
        dark.IS_LINUX = original_linux
        app.processEvents()


def test_fingerprint_tracks_icon_style():
    """The watcher fingerprint includes the macOS icon style and tint."""
    from src.ui import icons
    from src.ui.dark import theme_fingerprint

    _app()
    with patch.object(icons, "macos_icon_style", return_value=("Default", "red")):
        before = theme_fingerprint()
    with patch.object(icons, "macos_icon_style", return_value=("Default", "red")):
        assert theme_fingerprint() == before
    with patch.object(icons, "macos_icon_style", return_value=("Tinted", "orange")):
        assert theme_fingerprint() != before


def test_caption_icon_refetches_on_style_change():
    """A theme-driven icon-style flip redraws the title-bar icon, no restart."""
    from src.ui import icons
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    label = w._icon_label
    first = QPixmap(w._icon_size, w._icon_size)
    first.fill(Qt.red)
    second = QPixmap(w._icon_size, w._icon_size)
    second.fill(Qt.blue)
    try:
        with patch.object(icons, "macos_icon_style", return_value=("Default", "")):
            with patch.object(icons, "macos_app_icon_pixmap", return_value=first):
                w._icon_style = ("Tinted", "")
                w._apply_caption_icon()
                assert label.pixmap().toImage() == first.toImage()
                assert w._icon_style == ("Default", "")
        with patch.object(icons, "macos_icon_style", return_value=("Tinted", "orange")):
            with patch.object(icons, "macos_app_icon_pixmap", return_value=second) as draw:
                w._apply_caption_icon()
                assert draw.call_count == 1
                assert label.pixmap().toImage() == second.toImage()
    finally:
        w.close()
        app.processEvents()


def test_watcher_apply_now_runs_callback_and_restamps():
    """ThemeWatcher.apply_now refreshes the app and notifies window chrome."""
    from src.ui.dark import ThemeWatcher, theme_fingerprint

    app = _app()
    calls = []
    watcher = ThemeWatcher(app, on_apply=lambda: calls.append(True))
    watcher._installed = True
    try:
        watcher.apply_now()
        assert calls == [True]
        assert watcher._last == theme_fingerprint(app, platform_dark=False)
    finally:
        watcher.stop()
        app.processEvents()


def test_page_refresh_hooks_run_clean():
    """Every persistent page exposes refresh_theme and runs it headless."""
    from src.ui.error_page import ErrorPage
    from src.ui.retry_page import RetryPage
    from src.ui.settings_page import SettingsPage
    from src.ui.support_page import SupportPage

    _app()
    for cls in (SettingsPage, SupportPage, ErrorPage, RetryPage):
        page = cls()
        try:
            assert callable(getattr(page, "refresh_theme", None)), cls.__name__
            page.refresh_theme()
        finally:
            page.close()


def test_select_refresh_resets_local_view_icons():
    """Select page refresh re-fetches DA/scatter/auth button icons too."""
    from src.ui import select_page
    from src.ui.select_page import SelectPackagePage

    app = _app()
    page = SelectPackagePage()
    seen = []
    real_icon = select_page.get_symbol_icon
    real_pixmap = select_page.get_symbol_pixmap
    try:
        # Plain glyphs come through ``get_symbol_icon``; the install chip tints
        # a pixmap so a full-colour theme icon survives, so both paths count.
        with patch.object(select_page, "get_symbol_icon") as icons, patch.object(
            select_page, "get_symbol_pixmap"
        ) as pixmaps:
            icons.side_effect = lambda *a, **k: (seen.append(a), real_icon(*a, **k))[1]
            pixmaps.side_effect = lambda *a, **k: (seen.append(a), real_pixmap(*a, **k))[1]
            page.refresh_theme()
        names = [call[0] for call in seen]
        for symbol in ("file", "folder", "install", "cancel"):
            assert symbol in names, names
    finally:
        page.close()
        app.processEvents()


def test_settings_refresh_resets_cache_icon():
    """Settings page refresh re-fetches the clear-cache button icon."""
    from src.ui import settings_page
    from src.ui.settings_page import SettingsPage

    app = _app()
    page = SettingsPage()
    try:
        with patch.object(settings_page, "get_symbol_icon", return_value=QPixmap()) as mock:
            page.refresh_theme()
            # The glyph is re-fetched (and now carries the live text colour,
            # which the icon tests check); the refresh is what this covers.
            # A bin, not the cross: clearing the cache deletes files.
            assert [call.args[:2] for call in mock.call_args_list].count(("trash", 14)) == 1
    finally:
        page.close()
        app.processEvents()
