"""Tests for segmented guidance buttons, folder icon, and Settings themes card."""

import io
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from PySide6.QtWidgets import QApplication

from src.theme_pack import (
    normalize_rockbox_rel_path,
    merge_remote_theme_pack_into_zip,
    download_and_merge_theme_pack,
)


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def test_normalize_rockbox_rel_path():
    assert normalize_rockbox_rel_path("fonts/05-Tiny.fnt") == "fonts/05-Tiny.fnt"
    assert normalize_rockbox_rel_path("themes-themepack/fonts/05-Tiny.fnt") == "fonts/05-Tiny.fnt"
    assert normalize_rockbox_rel_path("themes-themepack/.rockbox/wps/sample.wps") == "wps/sample.wps"
    assert normalize_rockbox_rel_path(".rockbox/backdrops/bg.bmp") == "backdrops/bg.bmp"
    assert normalize_rockbox_rel_path("themes-themepack/themes/test.cfg") == "themes/test.cfg"


def test_merge_remote_theme_pack_into_zip(tmp_path):
    base_zip = tmp_path / "base.zip"
    overlay_zip = tmp_path / "overlay.zip"
    target_zip = tmp_path / "merged.zip"

    # Base zip has fonts and an old theme file
    with zipfile.ZipFile(base_zip, "w") as bz:
        bz.writestr("fonts/test.fnt", b"FONT_DATA_PRESERVED")
        bz.writestr("themes/test.cfg", b"OLD_THEME")

    # Overlay zip has a newer theme file and a new backdrop
    with zipfile.ZipFile(overlay_zip, "w") as oz:
        oz.writestr("themes-themepack/themes/test.cfg", b"NEW_THEME")
        oz.writestr("themes-themepack/backdrops/bg.bmp", b"NEW_BG")

    count = merge_remote_theme_pack_into_zip(overlay_zip, target_zip, base_zip_path=base_zip)
    assert count == 3
    assert target_zip.is_file()

    with zipfile.ZipFile(target_zip, "r") as mz:
        entries = set(mz.namelist())
        assert "fonts/test.fnt" in entries
        assert "themes/test.cfg" in entries
        assert "backdrops/bg.bmp" in entries
        assert mz.read("fonts/test.fnt") == b"FONT_DATA_PRESERVED"
        assert mz.read("themes/test.cfg") == b"NEW_THEME"
        assert mz.read("backdrops/bg.bmp") == b"NEW_BG"


def test_theme_pack_url_is_the_release_asset():
    from src.theme_pack import THEME_PACK_URL

    assert THEME_PACK_URL == (
        "https://github.com/rockbox-y1/themes/releases/download/"
        "for_the_gays_and_the_theys/themepack.zip"
    )


def test_combined_payload_uses_fonts_pack_when_themes_zip_is_absent(tmp_path):
    """Packaged builds ship fonts only; downloaded themes overlay them."""
    from src import theme_pack

    remote_zip = tmp_path / "remote.zip"
    with zipfile.ZipFile(remote_zip, "w") as zf:
        zf.writestr("themes-themepack/.rockbox/themes/new.cfg", b"theme")
    fonts_zip = tmp_path / "fonts.zip"
    with zipfile.ZipFile(fonts_zip, "w") as zf:
        zf.writestr(".rockbox/fonts/pack.fnt", b"font")

    with patch.object(theme_pack, "bundled_rockbox_dir", return_value=tmp_path / "missing"), \
         patch.object(theme_pack, "bundled_themepack_zip_path", return_value=tmp_path / "missing.zip"), \
         patch.object(theme_pack, "bundled_fonts_pack_zip_path", return_value=fonts_zip):
        dest = tmp_path / "combined"
        count = theme_pack.build_combined_rockbox_dir(remote_zip, dest)

    assert count == 1
    assert (dest / "fonts" / "pack.fnt").read_bytes() == b"font"
    assert (dest / "themes" / "new.cfg").read_bytes() == b"theme"
    assert not (dest / "_from_bundled_zip").exists()


def test_download_theme_pack_falls_back_and_keeps_bundled_fonts(tmp_path):
    from src import theme_pack as tp

    def _zip_bytes(entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buf.getvalue()

    theme_zip = _zip_bytes({"themes-themepack/.rockbox/themes/release.cfg": b"release"})
    font_zip = _zip_bytes({".rockbox/fonts/pack.fnt": b"font"})

    class _Response:
        def __init__(self, payload):
            self._payload = payload
            self._sent = False
            self.headers = {"Content-Length": str(len(payload))}

        def read(self, size=-1):
            if self._sent:
                return b""
            self._sent = True
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    bad_theme = "https://example.invalid/themepack-old.zip"
    good_theme = "https://example.invalid/themepack.zip"
    fonts = "https://example.invalid/fonts.zip"
    calls = []
    payloads = {good_theme: theme_zip, fonts: font_zip}

    def fake_urlopen(request, timeout=0):
        url = request.full_url
        calls.append(url)
        if url == bad_theme:
            raise OSError("offline")
        return _Response(payloads[url])

    with patch.object(tp, "downloads_dir", return_value=tmp_path), \
         patch.object(tp, "THEME_PACK_THEME_URLS", (bad_theme, good_theme)), \
         patch.object(tp, "THEME_PACK_FONT_URLS", (fonts,)), \
         patch.object(tp, "bundled_themepack_zip_path", return_value=tmp_path / "missing.zip"), \
         patch("src.config.is_offline_mode", return_value=False), \
         patch("urllib.request.urlopen", side_effect=fake_urlopen):
        ok, status, count = tp.download_and_merge_theme_pack(
            target_zip_path=tmp_path / "merged.zip"
        )

    assert ok is True
    assert status == "merged"
    assert count == 2
    assert calls == [bad_theme, good_theme, fonts]
    with zipfile.ZipFile(tmp_path / "merged.zip") as zf:
        names = set(zf.namelist())
        assert "themes/release.cfg" in names
        assert "fonts/pack.fnt" in names


def test_download_and_merge_theme_pack_offline_mode(tmp_path):
    with patch("src.config.is_offline_mode", return_value=True):
        ok, status, count = download_and_merge_theme_pack(target_zip_path=tmp_path / "test.zip")
        assert ok is True
        assert status == "offline"
        assert count == 0


def test_segmented_buttons_on_theme_guidance_screens():
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    app.processEvents()
    try:
        # 1. Fallback USB storage guidance screen
        page._show_theme_fallback_usb_mode()
        assert not page._theme_fallback_next_btn.isHidden()
        assert page._theme_fallback_next_btn.isEnabled()
        assert page._theme_fallback_next_btn._symbol == "folder"

        # Cancel button must be visible and enabled so the user can always exit
        assert not page._theme_back_btn.isHidden()
        assert page._theme_back_btn.isEnabled()
        assert page._theme_back_btn._symbol == "cancel"

        # They must be paired into a segmented capsule
        visible_chips = [c for c in page._flash_head._chips if not c.isHidden()]
        assert len(visible_chips) == 2
        assert page._theme_back_btn in visible_chips
        assert page._theme_fallback_next_btn in visible_chips
        assert page._theme_back_btn._companion == page._theme_fallback_next_btn
        assert page._theme_back_btn._is_leading is True
        assert page._theme_fallback_next_btn._is_leading is False

        # 2. Step 1 guidance screen (disconnect & power on)
        page._show_theme_step1_disconnect_power_on()
        assert not page._theme_step1_next_btn.isHidden()
        assert page._theme_step1_next_btn.isEnabled()
        assert not page._theme_back_btn.isHidden()
        assert page._theme_back_btn.isEnabled()
        assert page._theme_back_btn._symbol == "cancel"

        visible_chips = [c for c in page._flash_head._chips if not c.isHidden()]
        assert len(visible_chips) == 2
        assert page._theme_back_btn in visible_chips
        assert page._theme_step1_next_btn in visible_chips

        # 3. Power-on troubleshooting screen (user answered "No")
        page._on_theme_no_clicked()
        assert not page._theme_trouble_recover_btn.isHidden()
        assert page._theme_trouble_recover_btn.isEnabled()
        assert not page._theme_back_btn.isHidden()
        assert page._theme_back_btn.isEnabled()  # Back is enabled on troubleshooting screen
        assert page._theme_back_btn._symbol == "back"

        visible_chips = [c for c in page._flash_head._chips if not c.isHidden()]
        assert len(visible_chips) == 2
        assert page._theme_back_btn in visible_chips
        assert page._theme_trouble_recover_btn in visible_chips
    finally:
        page.close()


def test_settings_card_install_rockbox_themes():
    from src.ui.settings_page import SettingsPage
    from src.i18n import tr

    app = _app()
    settings = SettingsPage()
    settings.show()
    app.processEvents()
    try:
        assert hasattr(settings, "_install_themes_card")
        assert hasattr(settings, "_install_themes_btn")
        assert not settings._install_themes_card.isHidden()
        assert "Install Rockbox 360p Themes" in settings._install_themes_card.title()
        assert settings._install_themes_btn.text() == tr("settings_install_rockbox_themes_btn")

        # Clicking the button emits install_rockbox_themes_requested
        emitted = []
        settings.install_rockbox_themes_requested.connect(lambda: emitted.append(True))
        settings._install_themes_btn.click()
        assert len(emitted) == 1
    finally:
        settings.close()


def test_main_window_wires_install_themes_card():
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    with patch("src.theme_pack.AdbWatchWorker"):
        win = MainWindow()
    try:
        assert not win._install_run_active()
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch("src.device_tracking.get_all_device_installs", return_value={}),
            patch("src.theme_pack.AdbWatchWorker"),
            patch("src.theme_pack.AdbPresencePoller"),
            patch("src.theme_pack.ThemePackUpdateWorker") as mock_worker,
        ):
            win._settings_page.install_rockbox_themes_requested.emit()
            app.processEvents()
            assert win._stack.currentIndex() == _PAGE_FLASH
            assert win._install_run_active()
            if win.themes.device_prompt_pending:
                win._flash_page._wait_continue_btn.click()
                app.processEvents()
            assert win.themes.active
            assert win.themes.power_on_pending
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_segmented_pair_pushes_a_later_enable_to_the_native_control():
    """A step that gains a back destination must not keep a dead chip.

    The pair is built once; the leading chip is enabled later, when the journey
    moves from "nothing to go back to" to a step that has one.
    """
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    app.processEvents()
    try:
        back, nxt = page._theme_back_btn, page._theme_step1_next_btn

        class _FakeNative:
            def __init__(self):
                self.syncs = 0

            def _sync_content(self):
                self.syncs += 1

        fake = _FakeNative()
        back.set_segmented_pair(companion=nxt, is_leading=True)
        back._segmented_native = fake
        back.setEnabled(False)
        back.setEnabled(True)
        assert fake.syncs == 2
        assert back._companion is nxt
    finally:
        page.close()
        app.processEvents()


def test_normalized_payload_keeps_the_fonts_pack_in_step(tmp_path):
    """Fonts travel with the themes: one payload, both packs merged."""
    from src import theme_pack as tp

    fonts_zip = tmp_path / "fonts.zip"
    themes_zip = tmp_path / "themes.zip"
    with zipfile.ZipFile(fonts_zip, "w") as fz:
        fz.writestr(".rockbox/fonts/35-Nimbus.fnt", b"FONT")
        fz.writestr(".rockbox/fonts/COPYING-fonts.txt", b"LICENCE")
    with zipfile.ZipFile(themes_zip, "w") as tz:
        tz.writestr("themes-themepack/.rockbox/themes/cabbie.cfg", b"CFG")
        tz.writestr("themes-themepack/.rockbox/wps/cabbie.wps", b"WPS")

    target = tmp_path / "payload.zip"
    with patch.object(tp, "bundled_fonts_pack_zip_path", return_value=fonts_zip), patch.object(
        tp, "bundled_themepack_zip_path", return_value=themes_zip
    ):
        built = tp.ensure_normalized_theme_pack_zip(target, force_rebuild=True)

    with zipfile.ZipFile(built) as z:
        entries = set(z.namelist())
        assert "fonts/35-Nimbus.fnt" in entries
        assert "fonts/COPYING-fonts.txt" in entries
        assert "themes/cabbie.cfg" in entries
        assert "wps/cabbie.wps" in entries
        # Nothing keeps a wrapper directory: this is unpacked straight into
        # .rockbox on the player.
        assert not any(e.startswith((".rockbox/", "themes-themepack/")) for e in entries)
