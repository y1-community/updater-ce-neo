"""Linux caption controls use the desktop's own symbols, not drawn ones.

The header keeps the app's title and icon, but the minimize / maximize / close
marks are the ones the session's decoration theme provides, under the
freedesktop names (``window-close``, ``window-minimize-symbolic``, …). A
temporary icon theme stands in for whatever the developer's desktop installs.
"""

import os
import struct
import sys
import zlib
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="the host-symbol branch is the Linux one",
)


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _png(path: Path, size: int = 16) -> None:
    """Write a solid black RGBA PNG (no painter needed for the fixture)."""
    colour = b"\x00\x00\x00\xff"
    raw = b"".join(b"\x00" + colour * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _theme(root: Path, name: str, icons: tuple[str, ...]) -> Path:
    """An icon theme with the named caption icons at 16x16."""
    theme = root / name
    actions = theme / "16x16" / "actions"
    actions.mkdir(parents=True)
    (theme / "index.theme").write_text(
        f"[Icon Theme]\nName={name}\nDirectories=16x16/actions\n"
        f"[16x16/actions]\nSize=16\nType=Fixed\nContext=Actions\n",
        encoding="utf-8",
    )
    for icon in icons:
        _png(actions / f"{icon}.png")
    return theme


@pytest.fixture()
def themed(tmp_path):
    """Point Qt at a temporary theme root and restore it afterwards.

    The real search paths are set rather than patching ``QIcon.themeSearchPaths``:
    replacing a method on the wrapped Qt class aborts inside the icon loader.
    """
    from PySide6.QtGui import QIcon

    # The icon loader reads the application's palette and font database.
    _app()
    saved_paths = QIcon.themeSearchPaths()
    saved_theme = QIcon.themeName()

    root = tmp_path / "icons"
    root.mkdir()
    suffix = tmp_path.name.rsplit("_", 1)[-1]
    plain = f"CaptionPlain{suffix}"
    symbolic = f"CaptionSymbolic{suffix}"
    _theme(root, plain, ("window-minimize", "window-close"))
    _theme(root, symbolic, ("window-close-symbolic",))
    QIcon.setThemeSearchPaths([str(root), *saved_paths])
    try:
        yield {"root": root, "plain": plain, "symbolic": symbolic}
    finally:
        QIcon.setThemeName(saved_theme)
        QIcon.setThemeSearchPaths(saved_paths)


def test_the_session_theme_supplies_the_close_symbol(themed):
    from PySide6.QtGui import QIcon

    from src.ui.window_controls import ROLE_CLOSE, host_caption_icon

    QIcon.setThemeName(themed["plain"])
    icon, symbolic = host_caption_icon(ROLE_CLOSE)
    assert not icon.isNull(), "the desktop's window-close must be used"
    assert symbolic is False
    assert icon.pixmap(16, 16).size().width() > 0


def test_a_symbolic_theme_is_used_and_tinted(themed):
    from PySide6.QtGui import QColor, QIcon

    from src.ui.window_controls import ROLE_CLOSE, host_caption_icon, tinted_pixmap

    QIcon.setThemeName(themed["symbolic"])
    icon, symbolic = host_caption_icon(ROLE_CLOSE)
    assert not icon.isNull()
    assert symbolic is True, "a -symbolic icon has to be tinted with the palette"

    tinted = tinted_pixmap(icon.pixmap(16, 16), QColor("#ff0000"))
    image = tinted.toImage()
    assert image.pixelColor(8, 8).red() > 200
    assert image.pixelColor(8, 8).green() < 60


class _StubIcons:
    """Stands in for ``QIcon``'s theme lookup to record what is asked for."""

    def __init__(self, available: dict[str, str]):
        self._available = available
        self.theme = "SessionTheme"
        self.queried: list[tuple[str, str]] = []
        self.themes: list[str] = []

    def themeName(self):
        return self.theme

    def setThemeName(self, name):
        self.theme = name
        self.themes.append(name)

    def fromTheme(self, name, fallback=None):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon, QPixmap

        self.queried.append((self.theme, name))
        if self._available.get(name) == self.theme:
            pixmap = QPixmap(16, 16)
            pixmap.fill(Qt.GlobalColor.black)
            return QIcon(pixmap)
        return QIcon()


def test_the_session_theme_is_tried_before_the_installed_ones(monkeypatch):
    """A Qt platform theme that names no theme still finds one installed."""
    from src.ui import window_controls

    stub = _StubIcons({"window-close": "Adwaita"})
    stub.theme = ""
    monkeypatch.setattr(window_controls, "QIcon", stub)
    monkeypatch.setattr(window_controls, "FALLBACK_ICON_THEMES", ("Breeze", "Adwaita"))

    icon, _symbolic = window_controls.host_caption_icon(window_controls.ROLE_CLOSE)
    assert not icon.isNull()
    queried = [name for _theme, name in stub.queried]
    assert queried and queried[0] == "window-close", queried
    assert ("Adwaita", "window-close") in stub.queried
    # The process-wide theme name is put back however the search ends.
    assert stub.themes[-1] == "", stub.themes


def test_a_session_theme_wins_over_the_fallback_list(monkeypatch):
    from src.ui import window_controls

    stub = _StubIcons({"window-close": "SessionTheme"})
    monkeypatch.setattr(window_controls, "QIcon", stub)
    monkeypatch.setattr(window_controls, "FALLBACK_ICON_THEMES", ("Breeze",))

    icon, _symbolic = window_controls.host_caption_icon(window_controls.ROLE_CLOSE)
    assert not icon.isNull()
    assert stub.queried[0] == ("SessionTheme", "window-close")
    assert "Breeze" not in stub.themes


def test_the_button_paints_the_host_symbol(themed):
    from PySide6.QtGui import QIcon, QImage, QPainter

    from src.ui import window_controls

    QIcon.setThemeName(themed["plain"])
    app = _app()
    button = window_controls.CaptionControlButton(window_controls.ROLE_CLOSE)
    try:
        button._ensure_host_icon()
        assert not button._host_icon.isNull()

        image = QImage(button.width(), button.height(), QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        painted = button._paint_host_symbol(painter, window_controls.QColor("#ffffff"))
        painter.end()
        assert painted is True
        assert any(
            image.pixelColor(x, y).alpha() > 0
            for y in range(image.height())
            for x in range(image.width())
        )
    finally:
        button.deleteLater()
        app.processEvents()


def test_the_maximise_button_asks_for_the_restore_symbol(themed, monkeypatch):
    from PySide6.QtGui import QIcon

    from src.ui import window_controls

    QIcon.setThemeName(themed["plain"])
    asked = []
    real = window_controls.host_caption_icon

    def recording(role, *, maximized=False):
        asked.append((role, maximized))
        return real(role, maximized=maximized)

    monkeypatch.setattr(window_controls, "host_caption_icon", recording)
    app = _app()
    button = window_controls.CaptionControlButton(window_controls.ROLE_MAXIMIZE)
    try:
        button._ensure_host_icon()
        button.set_maximized(True)  # re-resolves for the restore variant
        button._ensure_host_icon()
        assert asked == [("maximize", False), ("maximize", True)], asked
    finally:
        button.deleteLater()
        app.processEvents()


def test_the_windows_red_hover_belongs_to_the_windows_glyphs():
    from src.ui.window_controls import CLOSE_HOT, ROLE_CLOSE, caption_hot_fill

    windows = caption_hot_fill(ROLE_CLOSE, dark=True, pressed=False, host_symbols=False)
    desktop = caption_hot_fill(ROLE_CLOSE, dark=True, pressed=False, host_symbols=True)
    assert windows == CLOSE_HOT
    assert desktop != CLOSE_HOT, "a host symbol is not highlighted with Windows red"
    assert desktop.alpha() < 255


def test_windows_and_macos_keep_their_own_glyphs(monkeypatch):
    from src.ui import window_controls

    monkeypatch.setattr(sys, "platform", "win32")
    assert window_controls.host_caption_symbols_apply() is False
    monkeypatch.setattr(sys, "platform", "darwin")
    assert window_controls.host_caption_symbols_apply() is False
    monkeypatch.setattr(sys, "platform", "linux")
    assert window_controls.host_caption_symbols_apply() is True
