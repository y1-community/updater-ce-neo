"""Tests for Rockbox Y2 theme patching, drag-and-drop theme flow, and completion notes."""

import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_patch_theme_content_for_y2():
    from src.theme_pack import patch_theme_content_for_y2

    raw_str = (
        "wps: /sdcard/.rockbox/wps/MacClassic.wps\n"
        "sbs: /sdcard0/.rockbox/wps/MacClassic.sbs\n"
        "backdrop: /storage/sdcard0/.rockbox/backdrops/MacClassic.bmp\n"
        "font: /.rockbox/fonts/30-DejaVuSans.fnt\n"
    )
    patched_str = patch_theme_content_for_y2(raw_str)
    assert "/sdcard/.rockbox" not in patched_str
    assert "/sdcard0/.rockbox" not in patched_str
    assert "/storage/sdcard0/.rockbox" not in patched_str
    assert "wps: /.rockbox/wps/MacClassic.wps\n" in patched_str
    assert "sbs: /.rockbox/wps/MacClassic.sbs\n" in patched_str
    assert "backdrop: /.rockbox/backdrops/MacClassic.bmp\n" in patched_str
    assert "font: /.rockbox/fonts/30-DejaVuSans.fnt\n" in patched_str

    raw_bytes = raw_str.encode("utf-8")
    patched_bytes = patch_theme_content_for_y2(raw_bytes)
    assert isinstance(patched_bytes, bytes)
    assert b"/sdcard/.rockbox" not in patched_bytes
    assert b"wps: /.rockbox/wps/MacClassic.wps\n" in patched_bytes


def test_should_apply_theme_path_fix():
    from src.catalog import FirmwarePackage
    from src.theme_pack import should_apply_theme_path_fix

    # Model defaults
    assert should_apply_theme_path_fix(model="Y2") is True
    assert should_apply_theme_path_fix(model="y2") is True
    assert should_apply_theme_path_fix(model="Y1") is False
    assert should_apply_theme_path_fix(model="Timmkoo") is False

    # Package rb_themes_path_fix overrides
    pkg_true = FirmwarePackage(slug="test", name="Test", model="Y1", repo=".", package_name="pkg", rb_themes_path_fix=True)
    assert should_apply_theme_path_fix(model="Y1", pkg=pkg_true) is True

    pkg_false = FirmwarePackage(slug="test", name="Test", model="Y2", repo=".", package_name="pkg", rb_themes_path_fix=False)
    assert should_apply_theme_path_fix(model="Y2", pkg=pkg_false) is False

    # Explicit argument overrides
    assert should_apply_theme_path_fix(model="Y2", rb_themes_path_fix=False) is False
    assert should_apply_theme_path_fix(model="Y1", rb_themes_path_fix=True) is True


def test_is_rockbox_theme_path_and_classify_drop(tmp_path):
    from src.drop_install import classify_drop, is_rockbox_theme_path

    # Scatter is firmware, not theme
    scatter = tmp_path / "MT6765_Android_scatter.txt"
    scatter.write_text("file_name: boot.img\n")
    assert not is_rockbox_theme_path(scatter)
    assert classify_drop(scatter) == "scatter"

    # Single .cfg file
    theme_cfg = tmp_path / "BunnyPod.cfg"
    theme_cfg.write_text("wps: /sdcard/.rockbox/wps/BunnyPod.wps\n")
    assert is_rockbox_theme_path(theme_cfg)
    assert classify_drop(theme_cfg) == "rockbox_theme"

    # Zip containing .rockbox
    theme_zip = tmp_path / "bunnypod.zip"
    with zipfile.ZipFile(theme_zip, "w") as zf:
        zf.writestr(".rockbox/themes/BunnyPod.cfg", "wps: /sdcard/.rockbox/wps/BunnyPod.wps\n")
        zf.writestr(".rockbox/wps/BunnyPod.wps", "wps content")
    assert is_rockbox_theme_path(theme_zip)
    assert classify_drop(theme_zip) == "rockbox_theme"

    # Folder containing .rockbox
    theme_folder = tmp_path / "theme_pack_dir"
    (theme_folder / ".rockbox" / "themes").mkdir(parents=True)
    (theme_folder / ".rockbox" / "themes" / "test.cfg").write_text("test")
    assert is_rockbox_theme_path(theme_folder)
    assert classify_drop(theme_folder) == "rockbox_theme"


def test_prepare_dragged_theme_payload(tmp_path):
    from src.theme_pack import prepare_dragged_theme_payload

    source_dir = tmp_path / "source_theme"
    (source_dir / "themes").mkdir(parents=True)
    (source_dir / "wps").mkdir(parents=True)
    (source_dir / "themes" / "MyTheme.cfg").write_text("wps: /sdcard/.rockbox/wps/MyTheme.wps\n")
    (source_dir / "wps" / "MyTheme.wps").write_text("wps content")

    target_zip = tmp_path / "target.zip"
    ok, msg = prepare_dragged_theme_payload(source_dir, target_zip, apply_path_fix=True)
    assert ok is True
    assert msg == ""
    assert target_zip.is_file()

    with zipfile.ZipFile(target_zip, "r") as zf:
        names = zf.namelist()
        assert "themes/MyTheme.cfg" in names
        assert "wps/MyTheme.wps" in names
        cfg_content = zf.read("themes/MyTheme.cfg").decode("utf-8")
        assert "/sdcard/.rockbox" not in cfg_content
        assert "wps: /.rockbox/wps/MyTheme.wps" in cfg_content


def test_completion_note_deduplication_and_standalone_copy():
    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = _app()
    page = FlashPage()
    page.show_flashing()
    page.set_device_done(donations_shown=True)
    page.show()
    try:
        # Case 1: Standalone themes install with donations enabled
        page.themes.is_standalone = True
        page.show_completion_appeal(True, themes_installed=True)
        page.show_completion_note(True, model="Y2", themes_installed=True)
        app.processEvents()

        # The duplicate top note container must be HIDDEN when the donation appeal is visible!
        assert not page._completion_note_container.isVisible()
        assert page._appeal.isVisible()
        # The donation appeal subtitle should state standalone theme install
        expected_sub = tr("donate_completion_theme_standalone_subtitle").format(model="Y2")
        assert expected_sub in page._intro.subtitle.text()

        # Case 2: Standalone themes install with donations disabled
        page.themes.is_standalone = True
        page.show_completion_appeal(False, themes_installed=True)
        page.show_completion_note(False, model="Y2", themes_installed=True)
        app.processEvents()

        # The note container is visible, but _complete_note is hidden so text is NOT duplicated!
        assert page._completion_note_container.isVisible()
        assert not page._complete_note.isVisible()
        assert expected_sub in page._completion_title_label.text()
    finally:
        page.close()
        app.processEvents()


def test_themes_journey_standalone_properties():
    from src.themes_journey import ThemesJourney

    journey = ThemesJourney()
    assert journey.is_standalone is False
    assert journey.payload_zip_path is None
    assert journey.rb_themes_path_fix is None

    journey.is_standalone = True
    journey.payload_zip_path = "/tmp/test.zip"
    journey.rb_themes_path_fix = True

    journey.reset_completion()
    assert journey.is_standalone is False
    assert journey.payload_zip_path is None
    assert journey.rb_themes_path_fix is None


def test_rockbox_power_on_prompt_button_for_y2_and_y1():
    from src.ui.main_window import MainWindow

    app = _app()
    w = MainWindow()
    try:
        # Y2 with "(360p Y1 Themes Only) for Y2" release name
        w._package_model = "Y2"
        w._package_name = "Rockbox 2.5 (Beta) (360p Y1 Themes Only) for Y2"
        wanted, model = w._rockbox_themes_required()
        assert wanted is True
        assert model == "Y2"

        prompt_y2 = w._flash_page.rockbox_power_on_question(model)
        assert "power button" in prompt_y2
        assert "Y2" in prompt_y2
        assert "centre button" not in prompt_y2
        assert "your Y1" not in prompt_y2

        # Y1 with Y1 release name
        w._package_model = "Y1"
        w._package_name = "Rockbox 2.5 (Beta) for Y1"
        wanted, model = w._rockbox_themes_required()
        assert wanted is True
        assert model == "Y1"

        prompt_y1 = w._flash_page.rockbox_power_on_question(model)
        assert "centre button" in prompt_y1
        assert "Y1" in prompt_y1
        assert "power button" not in prompt_y1
    finally:
        w.close()
        app.processEvents()

