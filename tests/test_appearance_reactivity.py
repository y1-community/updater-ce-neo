"""System appearance changes reach every widget, icon, and text live."""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PySide6.QtGui import QPixmap
from PySide6.QtCore import Qt


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


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
    real = select_page.get_symbol_icon
    try:
        with patch.object(select_page, "get_symbol_icon") as mock:
            mock.side_effect = lambda *a, **k: (seen.append(a), real(*a, **k))[1]
            page.refresh_theme()
        names = [call.args[0] for call in mock.call_args_list]
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
            mock.assert_called_with("cancel", 12)
    finally:
        page.close()
        app.processEvents()
