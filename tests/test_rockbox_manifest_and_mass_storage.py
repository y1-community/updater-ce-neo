"""Tests for Rockbox manifest flags, USB mass-storage discovery, and macOS Desktop TCC safety."""

import json
import os
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_manifest_parses_rockbox_flags_and_roundtrips_cache(tmp_path, monkeypatch):
    from src import catalog, manifest

    xml = (
        '<slidia>'
        '<package name="Nugget (Rockbox 240p + 360p)" brand="Innioasis" '
        'repo="norocoral/nugget-y1" device="Y1" type="img" handler="Custom Firmware" '
        'is_240p_rockbox="true" is_rockbox_android_false="true" '
        'rockbox_folder_path="rockbox_sdcard" rockbox_no_themes="true" />'
        '<package name="Custom Linux RB" brand="Innioasis" '
        'repo="example/linux-rb" device="Y1" type="img" handler="Custom Firmware" '
        'is_rockbox_android_port="false" rockbox_folder_path="/rockbox_sdcard/.rockbox/" />'
        '</slidia>'
    )
    pkgs = manifest.parse_manifest_xml(xml)
    assert len(pkgs) == 2

    nugget, linux_rb = pkgs
    assert nugget.is_240p_rockbox is True
    assert nugget.is_rockbox_android_port is False
    assert catalog.is_rockbox_android_port(package=nugget) is False
    assert nugget.rockbox_folder_path == "rockbox_sdcard"
    assert nugget.rockbox_no_themes is True

    assert linux_rb.is_rockbox_android_port is False
    assert catalog.is_rockbox_android_port(package=linux_rb) is False
    assert linux_rb.rockbox_folder_path == "rockbox_sdcard/.rockbox"
    assert linux_rb.rockbox_no_themes is False

    # Round-trip through cache_manifest / load_cached_manifest
    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(pkgs)
    reloaded = manifest.load_cached_manifest()
    assert reloaded is not None and len(reloaded) == 2
    assert reloaded[0].is_240p_rockbox is True
    assert reloaded[0].is_rockbox_android_port is False
    assert reloaded[0].rockbox_folder_path == "rockbox_sdcard"
    assert reloaded[0].rockbox_no_themes is True
    assert reloaded[1].rockbox_folder_path == "rockbox_sdcard/.rockbox"


def test_normalize_and_find_removable_rockbox_dirs_across_drives(tmp_path):
    from src.theme_sync import find_removable_rockbox_dirs, normalize_rockbox_folder_path

    assert normalize_rockbox_folder_path("") == ""
    assert normalize_rockbox_folder_path("rockbox_sdcard") == "rockbox_sdcard/.rockbox"
    assert normalize_rockbox_folder_path("rockbox_sdcard/.rockbox") == "rockbox_sdcard/.rockbox"
    assert normalize_rockbox_folder_path("/rockbox_sdcard/") == "rockbox_sdcard/.rockbox"
    assert normalize_rockbox_folder_path("nested/sub") == "nested/sub/.rockbox"
    assert normalize_rockbox_folder_path("nested/sub/.rockbox") == "nested/sub/.rockbox"
    assert normalize_rockbox_folder_path("../escape") == ""

    # Drive 1: Nugget layout with >40 sibling artist folders + rockbox_sdcard/.rockbox
    drive1 = tmp_path / "DRIVE_F"
    drive1.mkdir()
    for i in range(45):
        (drive1 / f"Artist_{i:02d}").mkdir()
    nugget_rb = drive1 / "rockbox_sdcard" / ".rockbox"
    nugget_rb.mkdir(parents=True)

    # Drive 2: Standard root .rockbox AND a custom nested path
    drive2 = tmp_path / "DRIVE_G"
    root_rb = drive2 / ".rockbox"
    root_rb.mkdir(parents=True)
    nested_rb = drive2 / "deep" / "custom" / ".rockbox"
    nested_rb.mkdir(parents=True)

    # Drive 3: Unrelated external drive with no .rockbox
    drive3 = tmp_path / "DRIVE_H"
    (drive3 / "Photos").mkdir(parents=True)

    # Automatic discovery without hint finds both rockbox_sdcard/.rockbox and root .rockbox
    found_auto = find_removable_rockbox_dirs(roots=[drive1, drive2, drive3])
    assert nugget_rb in found_auto
    assert root_rb in found_auto
    assert len(found_auto) == 2

    # With manifest hint pointing to deep/custom, all three are found
    found_hinted = find_removable_rockbox_dirs(
        rockbox_folder_path="deep/custom",
        roots=[drive1, drive2, drive3],
    )
    assert nugget_rb in found_hinted
    assert root_rb in found_hinted
    assert nested_rb in found_hinted
    assert len(found_hinted) == 3


def test_find_or_create_rockbox_dir_respects_manifest_folder_path(tmp_path):
    from src.theme_pack import find_or_create_rockbox_dir

    # Existing rockbox_sdcard/.rockbox is resolved even when drive root is picked
    drive = tmp_path / "PLAYER"
    existing = drive / "rockbox_sdcard" / ".rockbox"
    existing.mkdir(parents=True)
    assert find_or_create_rockbox_dir(drive, rockbox_folder_path="rockbox_sdcard") == existing.resolve()

    # When absent and rockbox_folder_path is set, creates under that relative path
    fresh = tmp_path / "FRESH_PLAYER"
    fresh.mkdir()
    with patch("src.theme_pack.get_mount_root", return_value=fresh):
        created = find_or_create_rockbox_dir(fresh, rockbox_folder_path="rockbox_sdcard")
    assert created == (fresh / "rockbox_sdcard" / ".rockbox").resolve()
    assert created.is_dir()


def test_dir_merge_worker_installs_to_all_detected_rockbox_dirs(tmp_path):
    from src.theme_pack import DirMergeWorker

    _app()
    payload = tmp_path / "themes.zip"
    with zipfile.ZipFile(payload, "w") as zf:
        zf.writestr("themes/TestTheme.cfg", "wps: /sdcard/.rockbox/wps/TestTheme.wps\n")
        zf.writestr("wps/TestTheme.wps", "%s%?it<%it|%fn>\n")
        zf.writestr("fonts/20-Test.fnt", b"FONT")

    rb1 = tmp_path / "drive1" / "rockbox_sdcard" / ".rockbox"
    rb2 = tmp_path / "drive2" / ".rockbox"
    rb1.mkdir(parents=True)
    rb2.mkdir(parents=True)

    results = []
    worker = DirMergeWorker(dst_dirs=[rb1, rb2], zip_path=payload)
    worker.finished.connect(lambda ok, err, count: results.append((ok, err, count)))
    worker.run()

    assert results and results[0][0] is True
    assert results[0][2] == 6  # 3 files x 2 destinations
    assert (rb1 / "themes" / "TestTheme.cfg").is_file()
    assert (rb2 / "themes" / "TestTheme.cfg").is_file()
    assert (rb1 / "fonts" / "20-Test.fnt").is_file()
    assert (rb2 / "fonts" / "20-Test.fnt").is_file()


def test_non_android_rockbox_skips_adb_and_prompts_usb_transfer_mode(tmp_path, monkeypatch):
    from src.catalog import FirmwarePackage
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    pkg = FirmwarePackage(
        slug="linux-rb-y1",
        name="Linux Rockbox",
        model="Y1",
        repo="example/linux-rb",
        package_name="rom.zip",
        is_rockbox_android_port=False,
        rockbox_folder_path="rockbox_sdcard",
    )
    try:
        monkeypatch.setattr(page, "_active_firmware_package", lambda: pkg)
        adb_called = []
        monkeypatch.setattr(
            "src.adb.find_adb_binary",
            lambda: adb_called.append("called") or "/usr/bin/adb",
        )
        # No drive mounted yet -> must prompt USB transfer mode (not ADB)
        monkeypatch.setattr("src.theme_sync.find_removable_rockbox_dirs", lambda **kw: [])
        page.start_rockbox_themes_stage(model="Y1", already_powered_on=True, automatic=False)
        app.processEvents()

        assert adb_called == []
        assert page.themes.mass_storage is True
        assert page.themes.rockbox_folder_path == "rockbox_sdcard"
        expected_prompt = tr("rockbox_step2_connect_usb_storage").format(model="Y1")
        assert page._flash_question_label.text() == expected_prompt

        # Now mount two drives with .rockbox and click Next -> transfers to ALL of them
        d1 = tmp_path / "ext1" / "rockbox_sdcard" / ".rockbox"
        d2 = tmp_path / "ext2" / ".rockbox"
        d1.mkdir(parents=True)
        d2.mkdir(parents=True)
        merged_targets = []
        monkeypatch.setattr(
            "src.theme_sync.find_removable_rockbox_dirs",
            lambda rockbox_folder_path="": [d1, d2] if rockbox_folder_path == "rockbox_sdcard" else [],
        )
        monkeypatch.setattr(page, "_start_mass_storage_merge", lambda dirs: merged_targets.extend(dirs))
        page._on_theme_step1_next_clicked()
        app.processEvents()

        assert adb_called == []
        assert merged_targets == [d1, d2]
    finally:
        page.stop_theme_workers()
        page.close()
        app.processEvents()


def test_rockbox_no_themes_skips_themes_cleanup_and_notice():
    from src.catalog import FirmwarePackage
    from src.ui.main_window import MainWindow

    app = _app()
    win = MainWindow()
    pkg = FirmwarePackage(
        slug="nugget-y1",
        name="Nugget (Rockbox 240p + 360p)",
        model="Y1",
        repo="norocoral/nugget-y1",
        package_name="rom.zip",
        is_240p_rockbox=True,
        is_rockbox_android_port=False,
        rockbox_no_themes=True,
        rockbox_folder_path="rockbox_sdcard",
    )
    try:
        win._package_name = pkg.name
        win._package_model = "Y1"
        win._keep_data = False
        win._keep_data_pkg = pkg
        if hasattr(win, "_select_page") and win._select_page is not None:
            win._select_page._details_package = pkg
        assert win._rockbox_themes_required() == (False, "Y1")
        assert win._rockbox_360p_themes_notice_needed() is False
        assert win._start_rockbox_cleanup_if_needed() is False
    finally:
        win._force_quit = True
        win.close()
        app.processEvents()


def test_cached_type_a_only_package_hidden_under_type_b(tmp_path, monkeypatch):
    from src import catalog
    from src.catalog import FirmwarePackage

    releases_dir = tmp_path / "releases"
    releases_dir.mkdir(parents=True)
    (releases_dir / "example_typea-only.json").write_text(
        json.dumps([
            {
                "tag_name": "v1.0.0",
                "name": "v1.0.0",
                "assets": [{"name": "rom.zip", "browser_download_url": "https://example.invalid/rom.zip"}],
            }
        ]),
        encoding="utf-8",
    )
    (releases_dir / "example_dual-type.json").write_text(
        json.dumps([
            {
                "tag_name": "v1.0.0",
                "name": "v1.0.0",
                "assets": [
                    {"name": "rom.zip", "browser_download_url": "https://example.invalid/rom.zip"},
                    {"name": "rom_type_b.zip", "browser_download_url": "https://example.invalid/rom_type_b.zip"},
                ],
            }
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(catalog, "_app_cache_dir", lambda: tmp_path)
    catalog._REPO_CACHE_ROWS.clear()

    pkg_a_only = FirmwarePackage("a-only-y1", "TypeAOnly", "Y1", "example/typea-only", "rom.zip")
    pkg_dual = FirmwarePackage("dual-y1", "DualType", "Y1", "example/dual-type", "rom.zip")

    assert catalog.package_supports_hw_type(pkg_a_only, "A") is True
    assert catalog.package_supports_hw_type(pkg_a_only, "B") is False
    assert catalog.package_supports_hw_type(pkg_dual, "A") is True
    assert catalog.package_supports_hw_type(pkg_dual, "B") is True


def test_macos_shortcut_manager_never_touches_desktop(monkeypatch):
    from src import shortcut_manager as sm

    monkeypatch.setattr(sm.paths, "IS_MAC", True)
    monkeypatch.setattr(sm.paths, "IS_WINDOWS", False)
    monkeypatch.setattr(sm.paths, "IS_LINUX", False)

    assert sm.supported() is False
    assert sm._roots("desktop") == []
    assert sm._roots("menu") == []
    assert sm.get_state() == {}
    # Must not raise or touch ~/Desktop
    sm.reconcile_shortcut_names()


def test_version_bumped_to_2_5_6_3():
    from src import config

    assert config.APP_VERSION == "2.5.6.3"
