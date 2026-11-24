"""Comprehensive verification suite for 2.5.6.5 Rockbox 240p themes, wizard dropdown, completion screen, Settings links, cleanup & cancel recovery."""

from __future__ import annotations

import os
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _isolated_settings(tmp_path: Path, name: str = "test.ini") -> QSettings:
    qs = QSettings(str(tmp_path / name), QSettings.Format.IniFormat)
    qs.clear()
    return qs


# ---------------------------------------------------------------------------
# 1. Manifest flags (is_240p_themes / is_rb_240p_themes / is_rockbox_240p_themes)
#    and release-level 240 filename detection
# ---------------------------------------------------------------------------


def test_manifest_240p_theme_flag_aliases_and_cache_roundtrip(tmp_path, monkeypatch):
    from src import catalog, manifest

    xml = (
        "<slidia>"
        '<package name="Nugget (Rock8ox 240p + 360p)" brand="Innioasis" '
        'repo="norocoral/nugget-y1" is_240p_themes="true" device="Y1" '
        'type="img" handler="Custom Firmware" '
        'post_install="https://norocoral.github.io/y1/inupdater/ftu/index.html" '
        'description="Based on Linux, running Rockbox" />'
        '<package name="AltRB A" brand="Innioasis" repo="ex/rb-a" '
        'is_rb_240p_themes="true" device="Y1" type="img" handler="Custom Firmware" />'
        '<package name="AltRB B" brand="Innioasis" repo="ex/rb-b" '
        'is_rockbox_240p_themes="true" device="Y2" type="img" handler="Custom Firmware" />'
        "</slidia>"
    )
    pkgs = manifest.parse_manifest_xml(xml)
    assert len(pkgs) == 3
    for pkg in pkgs:
        assert pkg.is_240p_themes is True
        assert pkg.is_240p_rockbox is True
        assert catalog.is_rockbox_firmware_package(pkg) is True

    # Nugget is Linux-based -> mass storage (not Android/ADB)
    assert catalog.is_rockbox_android_port(package=pkgs[0]) is False
    assert catalog.clean_rockbox_software_name(pkgs[0].name) == "Nugget"

    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(pkgs)
    reloaded = manifest.load_cached_manifest()
    assert reloaded is not None and len(reloaded) == 3
    assert all(p.is_240p_themes and p.is_240p_rockbox for p in reloaded)
    assert catalog.is_rockbox_android_port(package=reloaded[0]) is False


def test_release_zip_with_240_in_filename_is_240p_and_hidden_unless_filter_enabled():
    from src import catalog
    from src.fw_package import rockbox_240p_package_name

    assert rockbox_240p_package_name("rockbox-y1-240.zip") is True
    assert rockbox_240p_package_name("Rockbox", "rom_240p.zip") is True
    assert rockbox_240p_package_name("Rockbox", "rom_240.zip") is True
    assert rockbox_240p_package_name("Rockbox", "rom_360p.zip") is False
    assert rockbox_240p_package_name("Rockbox", "rom_2400.zip") is False

    pkg = catalog.FirmwarePackage(
        slug="rockbox-y1",
        name="Rockbox",
        model="Y1",
        repo="rockbox-y1/rockbox",
        package_name="rom.zip",
    )
    asset = {
        "name": "rom_240.zip",
        "browser_download_url": "https://example.invalid/rom_240.zip",
        "size": 1024,
    }
    variant = catalog._parse_rom_asset_variant(asset, "v0.6.0", pkg.repo)
    assert variant is not None and variant["resolution"] == "240p"
    rel = {
        "tag_name": "v0.6.0",
        "name": "v0.6.0",
        "body": "",
        "published_at": "2026-01-01T00:00:00Z",
        "prerelease": False,
        "draft": False,
        "source_repo": pkg.repo,
        "assets": [asset],
        "rom_variants": [variant],
    }

    client = catalog.ReleasesClient.__new__(catalog.ReleasesClient)
    client.get_all_releases = lambda repo, force_refresh=False: [rel]

    # Hidden by default when 240p filter is off
    default_rows = client.releases_for_package(pkg, "Y1", prefer_240p=False)
    assert default_rows == []

    # Visible when hidden 240p filter is enabled, labelled with (240p)
    filtered_rows = client.releases_for_package(pkg, "Y1", prefer_240p=True)
    assert len(filtered_rows) == 1
    assert filtered_rows[0]["selected_resolution"] == "240p"
    label = catalog.parse_clean_release_tag_label(filtered_rows[0], prefer_240p=True)
    assert "(240p)" in label


def test_240p_manifest_firmware_skips_360p_warning_and_360p_themes_stage():
    from src.catalog import FirmwarePackage
    from src.ui.flash_page import ROCKBOX_240P_THEMES_URL
    from src.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    nugget = FirmwarePackage(
        slug="nugget-y1",
        name="Nugget (Rock8ox 240p + 360p)",
        model="Y1",
        repo="norocoral/nugget-y1",
        package_name="rom.zip",
        is_240p_themes=True,
        post_install="https://norocoral.github.io/y1/inupdater/ftu/index.html",
        description="Based on Linux, running Rockbox",
    )
    try:
        win._package_name = nugget.name
        win._package_model = "Y1"
        win._keep_data = False
        win._keep_data_pkg = nugget
        win._select_page._details_package = nugget

        assert win._is_rockbox_install() is True
        assert win._rockbox_240p_detected() is True
        assert win._rockbox_themes_required() == (False, "Y1")
        assert win._rockbox_360p_themes_notice_needed() is False
        assert win._start_rockbox_cleanup_if_needed() is False
        assert win._completion_themes_spec(themes_failed=False) == (
            ROCKBOX_240P_THEMES_URL,
            "240p",
        )
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 2. Completion screen: Donations & Non-Donations (Get Themes + View Guide)
#    + stay-on-screen / two-button dismissal + install_first routing
# ---------------------------------------------------------------------------


def test_completion_non_donations_two_button_segmented_palette_and_stay_until_both_clicked():
    from src.ui.flash_page import FlashPage, ROCKBOX_240P_THEMES_URL

    app = _app()
    page = FlashPage()
    page.show()
    app.processEvents()
    dismissed = []
    opened = []
    try:
        page.on_install_done(lambda: dismissed.append(True))
        page.set_model("Y1")
        page.set_package_name("Nugget (Rock8ox 240p + 360p)")
        page.set_post_install_urls("https://norocoral.github.io/y1/inupdater/ftu/index.html", "")
        page.set_completion_themes(ROCKBOX_240P_THEMES_URL, kind="240p")
        page.set_device_done(donations_shown=False)
        page.show_completion_note(False, software="Nugget", model="Y1")
        app.processEvents()

        # Left chip = active Get Themes palette chip; right chip = View Guide globe chip
        assert page._cancel_btn.isHidden()
        assert not page._done_themes_btn.isHidden()
        assert page._done_themes_btn.isEnabled()
        assert page._done_themes_btn._symbol == "palette"
        assert not page._done_mark.isHidden()
        assert page._done_mark._symbol == "www"
        assert page._done_themes_btn._companion is page._done_mark
        assert page._done_themes_btn._is_leading is True

        with patch("src.browser.open_browser", side_effect=lambda u: opened.append(u) or True):
            # Click View Guide first -> stays on screen!
            page._done_mark.click()
            app.processEvents()
            assert len(opened) == 1
            assert "norocoral.github.io" in opened[0]
            assert dismissed == []

            # Click Get Themes second -> BOTH clicked -> dismisses!
            page._done_themes_btn.click()
            app.processEvents()
            assert len(opened) == 2
            assert opened[1] == ROCKBOX_240P_THEMES_URL
            assert dismissed == [True]

        # Reverse order (Get Themes first, View Guide second) also stays until both clicked
        dismissed.clear()
        opened.clear()
        page.set_completion_themes(ROCKBOX_240P_THEMES_URL, kind="240p")
        with patch("src.browser.open_browser", side_effect=lambda u: opened.append(u) or True):
            page._done_themes_btn.click()
            app.processEvents()
            assert dismissed == []
            page._done_mark.click()
            app.processEvents()
            assert dismissed == [True]
    finally:
        page.close()
        app.processEvents()


def test_completion_donations_view_guide_leading_and_get_themes_beside_stays_on_screen():
    from src.ui.flash_page import FlashPage, ROCKBOX_240P_THEMES_URL

    app = _app()
    page = FlashPage()
    page.show()
    app.processEvents()
    dismissed = []
    opened = []
    try:
        page.set_model("Y1")
        page.set_package_name("Nugget (Rock8ox 240p + 360p)")
        page.show_flashing()
        page.set_post_install_urls("https://norocoral.github.io/y1/inupdater/ftu/index.html", "")
        page.set_completion_themes(ROCKBOX_240P_THEMES_URL, kind="240p")
        page.set_device_done(donations_shown=True)
        page.show_completion_appeal(
            True,
            callback=lambda: dismissed.append("dont"),
            on_not_now=lambda: dismissed.append("not_now"),
        )
        app.processEvents()

        assert page._appeal.isVisible()
        assert not page._completion_guide_row.isHidden()
        assert not page._view_guide_btn.isHidden()
        assert not page._get_themes_btn.isHidden()
        # View Guide is leading (index 0: left on LTR, right on RTL); Get Themes is index 1
        lay = page._completion_guide_row.layout()
        assert lay.itemAt(0).widget() is page._view_guide_btn
        assert lay.itemAt(1).widget() is page._get_themes_btn

        # Clicking BOTH buttons on donations screen still keeps user on screen
        with patch("src.browser.open_browser", side_effect=lambda u: opened.append(u) or True):
            page._view_guide_btn.click()
            page._get_themes_btn.click()
            app.processEvents()
        assert len(opened) == 2
        assert opened[1] == ROCKBOX_240P_THEMES_URL
        assert dismissed == []
    finally:
        page.close()
        app.processEvents()


def test_completion_themes_url_routing_for_install_first_and_failed_360p():
    from src.catalog import FirmwarePackage
    from src.ui.flash_page import ROCKBOX_240P_THEMES_URL, ROCKBOX_360P_THEMES_GUIDE_URL
    from src.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    try:
        # 1. install_first + 240p themes -> 240p themes site
        pkg_240p_if = FirmwarePackage(
            slug="nugget-if",
            name="Nugget",
            model="Y1",
            repo="norocoral/nugget-y1",
            package_name="rom.zip",
            is_240p_themes=True,
            install_first="y1-community/y1-stock-rom",
        )
        win._package_name = pkg_240p_if.name
        win._package_model = "Y1"
        win._select_page._details_package = pkg_240p_if
        assert win._completion_themes_spec(themes_failed=False) == (
            ROCKBOX_240P_THEMES_URL,
            "240p",
        )

        # 2. install_first + 360p Rockbox -> THEMES.md via innioasis.app/post_install.html
        pkg_360p_if = FirmwarePackage(
            slug="rb-if",
            name="Rockbox",
            model="Y1",
            repo="rockbox-y1/rockbox",
            package_name="rom.zip",
            install_first="y1-community/y1-stock-rom",
        )
        win._package_name = "Rockbox"
        win._select_page._details_package = pkg_360p_if
        assert win._completion_themes_spec(themes_failed=False) == (
            ROCKBOX_360P_THEMES_GUIDE_URL,
            "360p_fallback",
        )

        # 3. Regular 360p Rockbox: NO Get Themes on success, THEMES.md on failure
        pkg_360p = FirmwarePackage(
            slug="rb-y1",
            name="Rockbox",
            model="Y1",
            repo="rockbox-y1/rockbox",
            package_name="rom.zip",
        )
        win._select_page._details_package = pkg_360p
        assert win._completion_themes_spec(themes_failed=False) == ("", "")
        assert win._completion_themes_spec(themes_failed=True) == (
            ROCKBOX_360P_THEMES_GUIDE_URL,
            "360p_fallback",
        )
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_innioasis_url_preferences_added_to_both_themes_destinations():
    from src.browser import _with_site_preferences, interstitial_url
    from src.ui.flash_page import ROCKBOX_240P_THEMES_URL, ROCKBOX_360P_THEMES_GUIDE_URL

    u240 = _with_site_preferences(interstitial_url(ROCKBOX_240P_THEMES_URL))
    assert u240.startswith("https://innioasis.app/leaving.html?")
    assert "themes.rockbox.org" in u240
    assert "donations=" in u240
    assert "lang=" in u240
    assert "ce=true" in u240

    u360 = _with_site_preferences(interstitial_url(ROCKBOX_360P_THEMES_GUIDE_URL))
    assert u360.startswith("https://innioasis.app/post_install.html?")
    assert "THEMES.md" in u360
    assert "donations=" in u360
    assert "lang=" in u360
    assert "ce=true" in u360


# ---------------------------------------------------------------------------
# 3. Manual 240p guidance screen + "Install 360p themes anyway" (mass storage)
#    + Passive ADB auto-skip while dropdown is open
# ---------------------------------------------------------------------------


def test_nugget_selection_shows_240p_guidance_and_install_anyway_uses_mass_storage(tmp_path):
    from src.player_update_check import RockboxThemeTarget
    from src.ui.flash_page import ROCKBOX_240P_THEMES_URL
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    qs = _isolated_settings(tmp_path)
    targets = [
        RockboxThemeTarget(model="Y1", software_name="Rockbox", full_name="Rockbox", is_android_port=True),
        RockboxThemeTarget(model="Y2", software_name="Rockbox", full_name="Rockbox", is_android_port=True, rb_themes_path_fix=True),
        RockboxThemeTarget(model="Y1", software_name="Nugget", full_name="Nugget (Rock8ox 240p + 360p)", is_240p_themes=True, is_android_port=False),
    ]
    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        with (
            patch("src.player_update_check.get_rockbox_theme_targets", return_value=targets),
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert win.themes.device_prompt_pending is True
            combo = win._flash_page._keep_data_choice
            nugget_idx = next(i for i in range(combo.count()) if "Nugget" in combo.itemText(i))
            combo.setCurrentIndex(nugget_idx)
            win._flash_page._wait_continue_btn.click()
            app.processEvents()

            # Shows 240p completion/guidance screen suggesting Get Themes
            assert win._stack.currentIndex() == _PAGE_FLASH
            assert win._flash_page._completion_themes_url == ROCKBOX_240P_THEMES_URL
            assert win._flash_page._completion_themes_kind == "240p_manual_hint"
            assert callable(win._flash_page._completion_install_anyway_cb)

            # Clicking "Install 360p themes anyway" starts mass-storage transfer (no ADB)
            win._flash_page._on_completion_install_anyway()
            app.processEvents()
            assert win.themes.mass_storage is True
            assert win.themes.model == "Y1"
            assert win.themes.power_on_pending is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_passive_adb_detection_auto_skips_open_software_dropdown(tmp_path):
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    qs = _isolated_settings(tmp_path)
    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert win.themes.device_prompt_pending is True

        # Player is now plugged in while dropdown is open -> auto-skips dropdown!
        with (
            patch("src.adb.find_adb_binary", return_value="/fake/adb"),
            patch("src.adb.adb_detect_player_model", return_value="Y2"),
            patch("src.adb.adb_devices", return_value=[]),
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._flash_page._on_device_prompt_adb_found("0123456789ABCDEF")
            app.processEvents()
            assert win.themes.device_prompt_pending is False
            assert win._stack.currentIndex() == _PAGE_FLASH
            assert win.themes.model == "Y2"
            assert win.themes.rb_themes_path_fix is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 4. Settings card links (Get 240p Themes + Save 360p Themes ZIP)
# ---------------------------------------------------------------------------


def test_settings_card_has_240p_themes_link_and_save_360p_zip_action(tmp_path):
    import time
    from src.ui.flash_page import ROCKBOX_240P_THEMES_URL
    from src.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    src_zip = tmp_path / "cached_360p.zip"
    with zipfile.ZipFile(src_zip, "w") as zf:
        zf.writestr("fonts/test.fnt", b"FONT" * 1024)
    out_zip = tmp_path / "exported.zip"
    opened = []
    progress_seen = []

    def _fake_ensure_ready(*_args, **kwargs):
        cb = kwargs.get("progress_cb")
        if callable(cb):
            cb(500, 1000)
            cb(1000, 1000)
        return src_zip, ""

    try:
        sp = win._settings_page
        assert hasattr(sp, "_get_240p_themes_link")
        assert hasattr(sp, "_save_360p_themes_link")
        assert hasattr(sp, "_themes_progress_bar")

        orig_set_progress = sp.set_themes_progress

        def _spy_progress(done, total, status=""):
            progress_seen.append((done, total, status))
            return orig_set_progress(done, total, status)

        sp.set_themes_progress = _spy_progress

        with patch("src.browser.open_browser", side_effect=lambda u: opened.append(u) or True):
            sp.get_240p_themes_requested.emit()
            app.processEvents()
        assert opened == [ROCKBOX_240P_THEMES_URL]

        with (
            patch.object(QFileDialog, "getSaveFileName", return_value=(str(out_zip), "ZIP Archives (*.zip)")),
            patch("src.theme_pack.ensure_ready_theme_pack_zip", side_effect=_fake_ensure_ready),
            patch("src.notifications.show_completion_notification"),
        ):
            sp.save_360p_themes_zip_requested.emit()
            worker = getattr(win, "_theme_export_worker", None)
            # Must run asynchronously on a background QThread so the GUI never freezes
            assert worker is not None
            # Wait cleanly for background worker to finish and deliver queued Qt signals
            deadline = time.monotonic() + 5.0
            while getattr(win, "_theme_export_worker", None) is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            worker.wait(2000)
            app.processEvents()

        assert out_zip.is_file()
        with zipfile.ZipFile(out_zip) as zf:
            assert "fonts/test.fnt" in zf.namelist()
        # Progress was surfaced in the Settings card and completion status shown afterward
        assert progress_seen, "Expected inline progress updates inside the Install Themes Settings card"
        assert sp._themes_progress_bar.isHidden()
        assert sp._save_360p_themes_link.isEnabled()
        assert str(out_zip) in sp._install_themes_status_lbl.text()
        assert not sp._install_themes_status_lbl.isHidden()
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_no_stray_top_level_popups_on_240p_completion_or_manual_themes_guidance():
    """Completion screens and 240p manual guidance must never spawn stray popup windows."""
    from PySide6.QtWidgets import QApplication
    from src.player_update_check import RockboxThemeTarget
    from src.ui.flash_page import ROCKBOX_240P_THEMES_URL
    from src.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    win.show()
    app.processEvents()
    try:
        nugget_target = RockboxThemeTarget(
            model="Y1",
            software_name="Nugget",
            full_name="Nugget (Rock8ox 240p + 360p)",
            package_slug="nugget-y1",
            is_android_port=False,
            is_240p_themes=True,
            rb_themes_path_fix=None,
            rockbox_folder_path=".rockbox",
        )
        for donations_disabled in (False, True):
            with patch(
                "src.device_tracking.is_donation_install_prompt_disabled",
                return_value=donations_disabled,
            ):
                # 1. Manual Settings -> Install Themes Now for a 240p firmware (Nugget)
                win._show_240p_manual_themes_guidance(nugget_target)
                app.processEvents()
                visible_tops = [
                    w for w in QApplication.topLevelWidgets() if w.isVisible()
                ]
                assert visible_tops == [win], (
                    f"Unexpected stray top-level windows (donations_disabled={donations_disabled}): "
                    f"{[(type(w).__name__, w.windowTitle()) for w in visible_tops]}"
                )

                # 2. Firmware install completion for Nugget (240p + post_install guide)
                fp = win._flash_page
                fp.set_model("Y1")
                fp.set_package_name("Nugget 2.1.3 (240p) for Y1")
                fp.show_flashing()
                fp.set_post_install_urls("https://norocoral.github.io/y1/inupdater/ftu/index.html", "")
                fp.set_completion_themes(ROCKBOX_240P_THEMES_URL, kind="240p")
                fp.set_device_done(donations_shown=not donations_disabled)
                fp.show_completion_note(
                    not donations_disabled,
                    software="Nugget 2.1.3 (240p) for Y1",
                    model="Y1",
                )
                if not donations_disabled:
                    fp.show_completion_appeal(True)
                app.processEvents()
                visible_tops = [
                    w for w in QApplication.topLevelWidgets() if w.isVisible()
                ]
                assert visible_tops == [win], (
                    f"Unexpected stray top-level windows on completion (donations_disabled={donations_disabled}): "
                    f"{[(type(w).__name__, w.windowTitle()) for w in visible_tops]}"
                )
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_completion_view_the_guide_link_clickable_and_redirects_via_innioasis_app_with_prefs():
    """'view the guide' is clickable, routes through innioasis.app with prefs, and keeps balanced (240p)."""
    from PySide6.QtWidgets import QLabel
    from src.browser import _with_donation_preference, _with_site_preferences, interstitial_url
    from src.i18n import translator
    from src.ui.flash_page import FlashPage, ROCKBOX_240P_THEMES_URL, _software_without_model

    app = _app()
    translator().set_language("en")
    assert _software_without_model("Nugget 2.1.3 (240p) for Y1", "Y1") == "Nugget 2.1.3 (240p)"
    assert _software_without_model("Solar (A5)", "A5") == "Solar"

    guide_url = "https://norocoral.github.io/y1/inupdater/ftu/index.html"
    opened = []
    dismissed = []

    page = FlashPage()
    page.show()
    try:
        page.set_model("Y1")
        page.set_package_name("Nugget 2.1.3 (240p) for Y1")
        page.on_install_done(lambda: dismissed.append(True))
        page.show_flashing()
        page.set_post_install_urls(guide_url, "")
        page.set_completion_themes(ROCKBOX_240P_THEMES_URL, kind="240p")

        # --- Non-donations completion screen ---
        page.set_device_done(donations_shown=False)
        page.show_completion_note(
            False, software="Nugget 2.1.3 (240p) for Y1", model="Y1"
        )
        app.processEvents()

        labels = [
            page._completion_hint_layout.itemAt(i).widget()
            for i in range(page._completion_hint_layout.count())
        ]
        hint_lbl = next(w for w in labels if isinstance(w, QLabel) and "get-themes" in w.text())
        assert "Nugget 2.1.3 (240p)" in hint_lbl.text()
        assert "(240p on" not in hint_lbl.text()
        assert 'href="post-install"' in hint_lbl.text()
        assert "view the guide" in hint_lbl.text()

        with patch("src.browser.open_browser", side_effect=lambda u: opened.append(u) or True):
            hint_lbl.linkActivated.emit("post-install")
            app.processEvents()
        assert opened == [guide_url]
        assert dismissed == [], "Two-button non-donations screen stays open until Get Themes is also clicked"

        # Verify the real browser redirect pipeline sends guide_url via innioasis.app with prefs
        with (
            patch("src.browser._native_accent_hex", return_value="#0a5ffe"),
            patch("src.player_update_check.is_donation_ui_disabled", return_value=True),
        ):
            routed = _with_site_preferences(_with_donation_preference(interstitial_url(opened[0])))
        assert routed.startswith("https://innioasis.app/leaving.html?")
        assert "norocoral.github.io" in routed
        assert "donations=false" in routed
        assert "lang=en" in routed
        assert "accent=%230a5ffe" in routed
        assert "ce=true" in routed

        # --- Donations completion screen ---
        opened.clear()
        page.set_device_done(donations_shown=True)
        page.show_completion_note(
            True, software="Nugget 2.1.3 (240p) for Y1", model="Y1"
        )
        page.show_completion_appeal(True)
        app.processEvents()
        assert 'href="post-install"' in page._completion_guide_hint.text()
        assert 'href="get-themes"' in page._completion_guide_hint.text()
        with patch("src.browser.open_browser", side_effect=lambda u: opened.append(u) or True):
            page._completion_guide_hint.linkActivated.emit("post-install")
            app.processEvents()
        assert opened == [guide_url]
    finally:
        page.close()
        app.processEvents()


def test_firmware_retention_preserves_cached_360p_themepack_and_active_part(tmp_path, monkeypatch):
    from src import downloads

    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    pack = tmp_path / "rockbox_360p_themepack.zip"
    pack.write_bytes(b"PK\x03\x04themes")
    active_part = tmp_path / "themepack_download_0.part"
    active_part.write_bytes(b"partial")
    stale_zip = tmp_path / "themepack_download_0.zip"
    stale_zip.write_bytes(b"stale")

    downloads.enforce_firmware_retention()

    assert pack.is_file(), "Cached 360p theme pack must not be pruned as an old firmware archive"
    assert active_part.is_file(), "In-flight theme pack .part file must not be deleted mid-download"
    assert not stale_zip.exists(), "Leftover temp themepack_download_*.zip should still be pruned"


# ---------------------------------------------------------------------------
# 5. Always-cancellable themes stage & disconnect recovery
# ---------------------------------------------------------------------------


def test_themes_stage_cancel_from_post_flash_lands_on_completion_with_get_themes():
    from src.ui.flash_page import ROCKBOX_360P_THEMES_GUIDE_URL
    from src.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    win.show()
    app.processEvents()
    try:
        win._package_model = "Y1"
        win._package_name = "Rockbox"
        with (
            patch("src.adb.find_adb_binary", return_value="/fake/adb"),
            patch("src.adb.adb_devices", return_value=[]),
            patch("src.theme_pack.AdbWatchWorker"),
            patch("src.theme_pack.AdbPresencePoller"),
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._start_themes_stage("Y1", donation_disabled=True, already_powered_on=True, after_firmware=True)
            app.processEvents()
            assert win.themes.active is True
            assert not win._flash_page._theme_back_btn.isHidden()
            assert win._flash_page._theme_back_btn.isEnabled()

            # Clicking Cancel gracefully finishes onto completion screen with THEMES.md
            win._flash_page._theme_back_btn.click()
            app.processEvents()
            assert not win.themes.active
            assert win.themes.placement_failed is True
            assert win._flash_page._completion_themes_url == ROCKBOX_360P_THEMES_GUIDE_URL
            assert not win._flash_page._done_themes_btn.isHidden()
            assert win._flash_page._done_themes_btn._symbol == "palette"
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_adb_push_disconnect_triggers_reconnect_window_with_paperclip_tip_and_dual_watch():
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show()
    page.themes.begin("Y2")
    page.themes.model = "Y2"
    try:
        with (
            patch("src.adb.find_adb_binary", return_value="/fake/adb"),
            patch("src.theme_sync.find_removable_rockbox_dirs", return_value=[]),
            patch("src.theme_pack.AdbWatchWorker"),
        ):
            page._on_theme_push_finished(False, "USB disconnected")
            app.processEvents()
            assert page.themes.adb_reconnect_tried is True
            assert page._adb_watch_seconds == 35
            assert not page._theme_paperclip_tip.isHidden()
            assert "Y2" in page._theme_paperclip_tip.text()
            assert getattr(page, "_removable_watch_timer", None) is not None
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


# ---------------------------------------------------------------------------
# 6. Hardened .rockbox cleanup (force-stop Rockbox + sync + config.cfg check)
# ---------------------------------------------------------------------------


def test_adb_delete_rockbox_stops_processes_syncs_and_verifies_config_removed():
    from src import adb

    calls = []
    ls_checks = []

    def _fake_run(*args, **kwargs):
        calls.append(args)
        joined = " ".join(str(a) for a in args)
        if "rm -rf" in joined:
            return subprocess.CompletedProcess(list(args), 0, stdout="__ADB_RM_OK__\n", stderr="")
        return subprocess.CompletedProcess(list(args), 0, stdout="", stderr="")

    def _fake_ls(_adb, _serial, path):
        ls_checks.append(path)
        return False

    with (
        patch.object(adb, "_run_adb", side_effect=_fake_run),
        patch.object(adb, "adb_find_all_rockbox_dirs", return_value=["/sdcard/.rockbox"]),
        patch.object(adb, "adb_shell_ls", side_effect=_fake_ls),
    ):
        ok, summary = adb.adb_delete_rockbox_dirs("/fake/adb", "SERIAL1")

    assert ok is True
    assert "Removed 1" in summary
    # Must stop Rockbox processes before removing .rockbox so config.cfg is not flushed back
    assert any("force-stop" in " ".join(str(a) for a in c) for c in calls)
    assert any("sync" in " ".join(str(a) for a in c) for c in calls)
    assert "/sdcard/.rockbox/config.cfg" in ls_checks


def test_adb_find_all_rockbox_dirs_skips_vold_staging_bind_mount():
    from src import adb

    # Y2 exposes /mnt/secure/staging/.rockbox alongside /storage/sdcard0/.rockbox;
    # only the real /storage/sdcard0/.rockbox must be returned.
    both = subprocess.CompletedProcess(
        [],
        0,
        stdout="/storage/sdcard0/.rockbox\n/mnt/secure/staging/.rockbox\n/mnt/media_rw/sdcard0/.rockbox\n",
        stderr="",
    )
    with patch.object(adb, "_run_adb", return_value=both):
        assert adb.adb_find_all_rockbox_dirs("/fake/adb", "SERIAL") == ["/storage/sdcard0/.rockbox"]

    # During early boot when only VOLD's staging mount exists, return [] so the
    # boot-wait loop waits cleanly for /storage/sdcard0/.rockbox to mount.
    staging_only = subprocess.CompletedProcess(
        [],
        0,
        stdout="/mnt/secure/staging/.rockbox\n",
        stderr="",
    )
    with patch.object(adb, "_run_adb", return_value=staging_only), patch.object(
        adb, "adb_shell_ls", return_value=False
    ):
        assert adb.adb_find_all_rockbox_dirs("/fake/adb", "SERIAL") == []


def test_adb_push_merge_overwrites_when_able_and_skips_unoverwritable_existing_files(tmp_path):
    from src import adb

    theme_dir = tmp_path / "themes_src"
    theme_dir.mkdir()
    (theme_dir / "a_retry_ok.cfg").write_text("new-a", encoding="utf-8")
    (theme_dir / "b_locked_existing.cfg").write_text("new-b", encoding="utf-8")
    (theme_dir / "c_clean.cfg").write_text("new-c", encoding="utf-8")

    attempts = {"a_retry_ok.cfg": 0, "b_locked_existing.cfg": 0, "c_clean.cfg": 0}
    rm_calls = []
    logs = []

    def fake_run_adb(_adb, *args, **kwargs):
        cmd = str(args[-1]) if args else ""
        if "rm -f" in cmd:
            rm_calls.append(cmd)
        return subprocess.CompletedProcess([], 0, "__ADB_MKDIR_OK__", "")

    def fake_push(_adb, _serial, local_path, remote_path, **_kwargs):
        if isinstance(local_path, list):
            return subprocess.CompletedProcess([], 1, "", "batch failed")
        name = Path(local_path).name
        attempts[name] += 1
        if name == "a_retry_ok.cfg":
            # First overwrite fails; after rm -f retry succeeds!
            rc = 1 if attempts[name] == 1 else 0
            return subprocess.CompletedProcess([], rc, "", "permission denied" if rc else "")
        if name == "b_locked_existing.cfg":
            # Overwrite fails both times, but file already exists on player
            return subprocess.CompletedProcess([], 1, "", "cannot overwrite")
        return subprocess.CompletedProcess([], 0, "", "")

    with (
        patch.object(adb, "_run_adb", side_effect=fake_run_adb),
        patch.object(adb, "_run_adb_push_with_progress", side_effect=fake_push),
        patch.object(adb, "adb_shell_ls", side_effect=lambda _a, _s, p: p.endswith("b_locked_existing.cfg")),
    ):
        ok, msg = adb.adb_push_merge(
            "/fake/adb",
            "SERIAL",
            theme_dir,
            "/storage/sdcard0/.rockbox",
            log_cb=logs.append,
        )

    assert ok is True, msg
    assert attempts["a_retry_ok.cfg"] == 2  # aimed to overwrite via rm -f + retry
    assert attempts["b_locked_existing.cfg"] == 2
    assert any("a_retry_ok.cfg" in c for c in rm_calls)
    assert "Skipped 1 existing file" in msg
    assert any("b_locked_existing.cfg" in line for line in logs)


def test_adb_worker_repairs_only_missing_files_instead_of_repeating_all_files(tmp_path):
    from src import theme_pack as tp

    zip_path = tmp_path / "pack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("themes/a.cfg", b"a")
        zf.writestr("themes/b.cfg", b"b")
        zf.writestr("fonts/f.fnt", b"f")

    worker = tp.AdbPushWorker("/fake/adb", "SERIAL", parent=None, zip_path=zip_path)
    results = []
    worker.finished.connect(lambda ok, msg: results.append((ok, msg)))

    push_calls = []
    verify_calls = [0]

    def fake_push_merge(_adb, _serial, _local, _device_dir, **kwargs):
        push_calls.append(kwargs.get("only_rel_paths"))
        return True, ""

    def fake_verify(_adb, _serial, _device_dir, _zip):
        verify_calls[0] += 1
        if verify_calls[0] == 1:
            return False, "1 of 3 theme files are missing from /storage/sdcard0/.rockbox (first: themes/b.cfg)"
        return True, "3 theme files verified in /storage/sdcard0/.rockbox"

    with (
        patch("src.adb.adb_find_all_rockbox_dirs", return_value=["/storage/sdcard0/.rockbox"]),
        patch("src.adb.adb_push_and_unpack_zip", return_value=(False, "no unzip")),
        patch("src.adb.adb_push_merge", side_effect=fake_push_merge),
        patch("src.adb.adb_verify_theme_payload", side_effect=fake_verify),
        patch("src.adb._payload_missing_from", return_value=["themes/b.cfg"]),
    ):
        worker.run()

    assert results == [(True, "3 theme files verified in /storage/sdcard0/.rockbox")]
    assert push_calls == [None, {"themes/b.cfg"}]


def test_y2_patch_zip_never_double_appends_y2_suffix(tmp_path):
    from src.theme_pack import ensure_theme_zip_patched_for_y2

    src = tmp_path / "rockbox_360p_themepack_y2.zip"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("themes/test.cfg", b"wps: /.rockbox/wps/test.wps\n")
    out = ensure_theme_zip_patched_for_y2(src)
    assert out.name == "rockbox_360p_themepack_y2.zip"
    assert not (tmp_path / "rockbox_360p_themepack_y2_y2.zip").exists()


def test_app_version_is_2_5_7():
    from src.config import APP_VERSION

    assert APP_VERSION == "2.5.7"
