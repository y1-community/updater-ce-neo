"""Tests for ADB model auto-detection and manual Rockbox theme device selection."""

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
from PySide6.QtWidgets import QApplication, QInputDialog


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _isolated_settings(tmp_path: Path, name: str = "test.ini") -> QSettings:
    qs = QSettings(str(tmp_path / name), QSettings.Format.IniFormat)
    qs.clear()
    return qs


# ---------------------------------------------------------------------------
# 1. ADB property classification & device detection
# ---------------------------------------------------------------------------

def test_classify_adb_model_identifies_y2_and_y1_and_guards_other_devices():
    from src.adb import STANDARD_Y_ADB_SERIAL, classify_adb_model

    # Real Innioasis Y2 hardware capture properties
    assert classify_adb_model({
        "ro.product.model": "Y2",
        "ro.product.device": "Y2",
        "ro.product.board": "Y2",
        "ro.build.fingerprint": "Y2/Y2/Y2:4.4.2/KOT49H/1786599642:user/test-keys",
        "ro.hardware": "mt6582",
    }) == "Y2"

    # Y2 board codename without explicit Y2 model token
    assert classify_adb_model({
        "ro.product.device": "eastaeon82_wet_kk",
        "ro.hardware": "mt6582",
    }) == "Y2"

    # Real Innioasis Y1 identifiers
    assert classify_adb_model({"ro.product.model": "Y1"}) == "Y1"
    assert classify_adb_model({"ro.product.model": "Innioasis Y1"}) == "Y1"
    assert classify_adb_model({"ro.product.device": "g368_nyx", "ro.hardware": "mt6572"}) == "Y1"

    # Generic MTK SoC fallback with standard serial / alps brand
    assert classify_adb_model(
        {"ro.product.brand": "alps", "ro.hardware": "mt6582"},
        serial=STANDARD_Y_ADB_SERIAL,
    ) == "Y2"
    assert classify_adb_model(
        {"ro.product.brand": "alps", "ro.board.platform": "mt6572"},
        serial=STANDARD_Y_ADB_SERIAL,
    ) == "Y1"

    # Must NOT misclassify unrelated Android phones or non-Rockbox-theme players
    assert classify_adb_model({
        "ro.product.model": "Pixel 8",
        "ro.product.brand": "google",
        "ro.product.manufacturer": "Google",
    }) is None
    assert classify_adb_model({
        "ro.product.model": "Galaxy S24",
        "ro.product.brand": "samsung",
        "ro.hardware": "mt6582",
    }) is None
    assert classify_adb_model({
        "ro.product.model": "Q3",
        "ro.product.brand": "alps",
        "ro.hardware": "mt6582",
    }) is None
    assert classify_adb_model({
        "ro.product.model": "G3",
        "ro.product.brand": "alps",
        "ro.hardware": "mt6572",
    }) is None
    # Substring false-positive guard
    assert classify_adb_model({"ro.product.model": "GALAXY2"}) is None
    assert classify_adb_model({"ro.product.model": "Y100"}) is None
    assert classify_adb_model({"ro.product.model": "MY20"}) is None


def test_adb_detect_player_model_from_devices_banner_and_getprop_fallback():
    from src import adb

    # Case 1: adb devices -l already carries model:Y2 (no extra shell call needed)
    devices_l_y2 = subprocess.CompletedProcess(
        args=["adb", "devices", "-l"],
        returncode=0,
        stdout=(
            "List of devices attached\n"
            "0123456789ABCDEF       device usb:1-1 product:Y2 model:Y2 device:Y2 transport_id:1\n"
        ),
        stderr="",
    )
    with patch.object(adb, "_run_adb", return_value=devices_l_y2) as mock_run:
        assert adb.adb_detect_player_model(adb="/fake/adb") == "Y2"
        assert mock_run.call_count == 1

    # Case 2: banner omits model -> falls back to single shell getprop probe
    devices_l_bare = subprocess.CompletedProcess(
        args=["adb", "devices", "-l"],
        returncode=0,
        stdout="List of devices attached\n0123456789ABCDEF       device transport_id:2\n",
        stderr="",
    )
    getprop_y1 = subprocess.CompletedProcess(
        args=["adb", "-t", "2", "shell", "..."],
        returncode=0,
        stdout="ro.product.model=Y1\nro.hardware=mt6572\n",
        stderr="",
    )
    with patch.object(adb, "_run_adb", side_effect=[devices_l_bare, getprop_y1]):
        assert adb.adb_detect_player_model(adb="/fake/adb") == "Y1"

    # Case 3: both Y1 and Y2 connected simultaneously -> ambiguous -> None
    devices_l_both = subprocess.CompletedProcess(
        args=["adb", "devices", "-l"],
        returncode=0,
        stdout=(
            "List of devices attached\n"
            "0123456789ABCDEF       device model:Y1 transport_id:1\n"
            "0123456789ABCDEF       device model:Y2 transport_id:2\n"
        ),
        stderr="",
    )
    with patch.object(adb, "_run_adb", return_value=devices_l_both):
        assert adb.adb_detect_player_model(adb="/fake/adb") is None

    # Case 4: one Y2 + one unrelated Pixel -> unambiguous Y2
    devices_l_y2_and_pixel = subprocess.CompletedProcess(
        args=["adb", "devices", "-l"],
        returncode=0,
        stdout=(
            "List of devices attached\n"
            "PIXEL123               device model:Pixel_8 transport_id:1\n"
            "0123456789ABCDEF       device model:Y2 transport_id:2\n"
        ),
        stderr="",
    )
    getprop_pixel = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="ro.product.model=Pixel 8\nro.product.brand=google\n",
        stderr="",
    )
    with patch.object(adb, "_run_adb", side_effect=[devices_l_y2_and_pixel, getprop_pixel]):
        assert adb.adb_detect_player_model(adb="/fake/adb") == "Y2"


# ---------------------------------------------------------------------------
# 2. Pure decision logic in ThemesJourney
# ---------------------------------------------------------------------------

def test_resolve_manual_themes_choice_rules():
    from src.themes_journey import resolve_manual_themes_choice

    # ADB wins over everything (unknown devices OR multiple tracked models)
    assert resolve_manual_themes_choice(
        adb_model="Y2",
        tracked_supported_models=[],
        supported_models=["Y1", "Y2"],
    ) == ("Y2", ["Y2"], "Y2")
    assert resolve_manual_themes_choice(
        adb_model="Y1",
        tracked_supported_models=["Y1", "Y2"],
        supported_models=["Y1", "Y2"],
    ) == ("Y1", ["Y1"], "Y1")

    # Single tracked supported model -> automatic, no prompt
    assert resolve_manual_themes_choice(
        adb_model=None,
        tracked_supported_models=["Y2"],
        supported_models=["Y1", "Y2"],
    ) == ("Y2", ["Y2"], "Y2")

    # Unknown devices (none tracked) -> must prompt with all supported models
    auto, choices, default = resolve_manual_themes_choice(
        adb_model=None,
        tracked_supported_models=[],
        supported_models=["Y1", "Y2"],
        fallback_default="Y2",
    )
    assert auto is None
    assert choices == ["Y1", "Y2"]
    assert default == "Y2"

    # Multiple supported models tracked -> must prompt, pre-selecting Rockbox-installed one
    auto, choices, default = resolve_manual_themes_choice(
        adb_model=None,
        tracked_supported_models=["Y1", "Y2"],
        rockbox_installed_models=["Y2"],
        supported_models=["Y1", "Y2"],
        fallback_default="Y1",
    )
    assert auto is None
    assert choices == ["Y1", "Y2"]
    assert default == "Y2"


# ---------------------------------------------------------------------------
# 3. MainWindow Settings / Ctrl+T / Drag-and-Drop flows
# ---------------------------------------------------------------------------

def test_settings_manual_themes_prompts_once_for_unknown_devices_and_cancels_cleanly(tmp_path):
    from src.ui.main_window import MainWindow, _PAGE_FLASH, _PAGE_SETTINGS

    app = _app()
    qs = _isolated_settings(tmp_path)
    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        win._nav_to_page(_PAGE_SETTINGS)
        # Cancel on picker -> must NOT switch to Flash page or start download
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch.object(QInputDialog, "getItem", return_value=("", False)) as mock_dlg,
            patch("src.theme_pack.ThemePackUpdateWorker") as mock_worker,
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert mock_dlg.call_count == 1
            assert mock_worker.call_count == 0
            assert win._stack.currentIndex() == _PAGE_SETTINGS
            assert not win.themes.active

        # Choose Y2 -> prompted ONCE (not twice!), enters Flash page for Y2
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch.object(QInputDialog, "getItem", return_value=("Y2", True)) as mock_dlg,
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert mock_dlg.call_count == 1
            assert win._stack.currentIndex() == _PAGE_FLASH
            assert win.themes.active
            assert win.themes.model == "Y2"
            assert win.themes.rb_themes_path_fix is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_settings_manual_themes_prompts_when_multiple_supported_models_tracked(tmp_path):
    from src import player_update_check as tracking
    from src.config import device_label_for_model
    from src.ui.main_window import MainWindow

    app = _app()
    qs = _isolated_settings(tmp_path)
    # Track Y1 (Original Software) and Y2 (Original Software) — both support Rockbox themes
    tracking.record_device_install("Y1", "Original Software", "3.2.4", settings=qs)
    tracking.record_device_install("Y2", "Original Software", "3.1.7", settings=qs)

    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch.object(QInputDialog, "getItem", return_value=(device_label_for_model("Y2"), True)) as mock_dlg,
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert mock_dlg.call_count == 1
            items_arg = mock_dlg.call_args.args[3]
            assert device_label_for_model("Y1") in items_arg
            assert device_label_for_model("Y2") in items_arg
            assert win.themes.model == "Y2"
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_settings_manual_themes_skips_prompt_when_adb_detects_model(tmp_path):
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    app = _app()
    qs = _isolated_settings(tmp_path)  # No tracked devices at all!
    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        with (
            patch("src.adb.adb_detect_player_model", return_value="Y2"),
            patch.object(QInputDialog, "getItem") as mock_dlg,
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert mock_dlg.call_count == 0
            assert win._stack.currentIndex() == _PAGE_FLASH
            assert win.themes.model == "Y2"
            assert win.themes.rb_themes_path_fix is True
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_settings_manual_themes_skips_prompt_for_single_tracked_model(tmp_path):
    from src import player_update_check as tracking
    from src.ui.main_window import MainWindow

    app = _app()
    qs = _isolated_settings(tmp_path)
    # Only Y1 tracked (even across both Type A and Type B variants -> still one model Y1)
    tracking.record_device_install("Y1", "Rockbox", "0.6.1", hardware_type="A", settings=qs)
    tracking.record_device_install("Y1", "Original Software", "3.2.4", hardware_type="B", settings=qs)
    # A non-Rockbox-theme device (G1) should not cause a Rockbox theme prompt
    tracking.record_device_install("G1", "Original Software", "1.0.0", settings=qs)

    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch.object(QInputDialog, "getItem") as mock_dlg,
            patch("src.theme_pack.ThemePackUpdateWorker"),
        ):
            win._on_settings_install_rockbox_themes()
            app.processEvents()
            assert mock_dlg.call_count == 0
            assert win.themes.model == "Y1"
            assert win.themes.rb_themes_path_fix is False
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_drag_and_drop_theme_skips_prompt_via_adb_and_honours_cancel(tmp_path):
    from src.ui.main_window import MainWindow, _PAGE_FLASH, _PAGE_SELECT

    app = _app()
    qs = _isolated_settings(tmp_path)
    theme_zip = tmp_path / "MyTheme.zip"
    with zipfile.ZipFile(theme_zip, "w") as zf:
        zf.writestr(".rockbox/themes/MyTheme.cfg", "wps: /sdcard/.rockbox/wps/MyTheme.wps\n")
        zf.writestr(".rockbox/wps/MyTheme.wps", "wps content")

    with patch("src.theme_pack.AdbWatchWorker"), patch("src.theme_pack.AdbPresencePoller"):
        win = MainWindow()
    win.settings = qs
    try:
        win._nav_to_page(_PAGE_SELECT)
        # Cancel -> does not start
        with (
            patch("src.adb.adb_detect_player_model", return_value=None),
            patch.object(QInputDialog, "getItem", return_value=("", False)) as mock_dlg,
        ):
            win._start_theme_drop_install(theme_zip)
            app.processEvents()
            assert mock_dlg.call_count == 1
            assert win._stack.currentIndex() == _PAGE_SELECT
            assert not win.themes.active

        # ADB detects Y2 -> skips prompt and applies Y2 path fix
        with (
            patch("src.adb.adb_detect_player_model", return_value="Y2"),
            patch.object(QInputDialog, "getItem") as mock_dlg,
        ):
            win._start_theme_drop_install(theme_zip)
            app.processEvents()
            assert mock_dlg.call_count == 0
            assert win._stack.currentIndex() == _PAGE_FLASH
            assert win.themes.model == "Y2"
            assert win.themes.rb_themes_path_fix is True
            with zipfile.ZipFile(win.themes.payload_zip_path, "r") as zf:
                cfg = zf.read("themes/MyTheme.cfg").decode("utf-8")
                assert "/.rockbox/wps/MyTheme.wps" in cfg
                assert "/sdcard/.rockbox" not in cfg
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_flash_page_standalone_adb_push_refines_model_from_connected_device(tmp_path):
    from src.ui.flash_page import FlashPage

    app = _app()
    theme_dir = tmp_path / "dropped"
    (theme_dir / "themes").mkdir(parents=True)
    (theme_dir / "wps").mkdir(parents=True)
    (theme_dir / "themes" / "Demo.cfg").write_text("wps: /sdcard/.rockbox/wps/Demo.wps\n")
    (theme_dir / "wps" / "Demo.wps").write_text("wps")
    zip_path = tmp_path / "payload.zip"
    from src.theme_pack import prepare_dragged_theme_payload
    prepare_dragged_theme_payload(theme_dir, zip_path, apply_path_fix=False)

    page = FlashPage()
    try:
        page.themes.begin("Y1")
        page.themes.is_standalone = True
        page.themes.is_custom_theme = True
        page.themes.custom_theme_paths = [theme_dir]
        page.themes.payload_zip_path = zip_path
        page.themes.rb_themes_path_fix = False

        captured = {}

        class _FakeWorker:
            progress = MagicMock()
            log = MagicMock()
            status = MagicMock()
            finished = MagicMock()

            def __init__(self, adb, serial, **kwargs):
                captured["model"] = kwargs.get("model")
                captured["rb_themes_path_fix"] = kwargs.get("rb_themes_path_fix")
                captured["zip_path"] = kwargs.get("zip_path")

            def start(self):
                captured["started"] = True

        with (
            patch("src.adb.find_adb_binary", return_value="/fake/adb"),
            patch("src.adb.adb_detect_player_model", return_value="Y2"),
            patch("src.theme_pack.AdbPushWorker", _FakeWorker),
        ):
            page._start_adb_theme_push("0123456789ABCDEF")

        assert captured["started"] is True
        assert captured["model"] == "Y2"
        assert captured["rb_themes_path_fix"] is True
        assert page.themes.model == "Y2"
        # Dropped zip was re-prepared with Y2 path fix!
        with zipfile.ZipFile(zip_path, "r") as zf:
            cfg = zf.read("themes/Demo.cfg").decode("utf-8")
            assert "/.rockbox/wps/Demo.wps" in cfg
            assert "/sdcard/.rockbox" not in cfg
    finally:
        page.close()
        app.processEvents()


def test_flash_page_install_journey_keeps_known_model_without_adb_override():
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    try:
        page.themes.begin("Y1")
        page.themes.is_standalone = False
        page.themes.rb_themes_path_fix = False

        captured = {}

        class _FakeWorker:
            progress = MagicMock()
            log = MagicMock()
            status = MagicMock()
            finished = MagicMock()

            def __init__(self, adb, serial, **kwargs):
                captured["model"] = kwargs.get("model")
                captured["rb_themes_path_fix"] = kwargs.get("rb_themes_path_fix")

            def start(self):
                captured["started"] = True

        with (
            patch("src.adb.find_adb_binary", return_value="/fake/adb"),
            patch("src.adb.adb_detect_player_model") as mock_detect,
            patch("src.theme_pack.AdbPushWorker", _FakeWorker),
        ):
            page._start_adb_theme_push("0123456789ABCDEF")
            assert mock_detect.call_count == 0

        assert captured["started"] is True
        assert captured["model"] == "Y1"
        assert captured["rb_themes_path_fix"] is False
    finally:
        page.close()
        app.processEvents()
