"""fw_package.ini: detection beside the app, metadata, updater image and pruning of an extracted firmware zip."""

import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from src import fw_package
from src.ui.select_page import SelectPackagePage
from src.fw_package import (
    find_installed_package,
    locate_package,
    prune_to_firmware_package,
    read_fw_package_ini,
    read_package,
)

SCATTER = """\
- partition_index: SYS0
  partition_name: preloader
  file_name: preloader.bin
  is_download: true
- partition_index: SYS1
  partition_name: boot
  file_name: boot.img
  is_download: true
- partition_index: SYS2
  partition_name: unused
  file_name: NONE
  is_download: false
"""


def _firmware(folder: Path, ini: str = "true\n", files=("preloader.bin", "boot.img")) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MT6572_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")
    for name in files:
        (folder / name).write_bytes(b"x")
    if ini is not None:
        (folder / fw_package.FW_PACKAGE_INI).write_text(ini, encoding="utf-8")
    return folder


def _ini(tmp_path: Path, text: str) -> Path:
    path = tmp_path / fw_package.FW_PACKAGE_INI
    path.write_text(text, encoding="utf-8")
    return path


def _png(path: Path) -> None:
    img = QImage(64, 64, QImage.Format_ARGB32)
    img.fill(QColor("red"))
    assert img.save(str(path), "PNG")


def _app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture(autouse=True)
def _no_release_fetch(monkeypatch):
    # Package pages do not need a release listing. Skipping the network fetch
    # keeps a ReleasesWorker thread from outliving the test and aborting Qt.
    monkeypatch.setattr(SelectPackagePage, "_refresh_releases", lambda self, *a, **k: None)


def test_bare_true_and_false_ini(tmp_path):
    assert read_fw_package_ini(_ini(tmp_path, "true\n")).enabled is True
    assert read_fw_package_ini(_ini(tmp_path, "false")).enabled is False


def test_ini_with_metadata(tmp_path):
    text = (
        "[fw_package]\nenabled = true\nname = Rockbox\nversion = 2.1\n"
        "model = Y1\ntype = Stock\n"
    )
    meta = read_fw_package_ini(_ini(tmp_path, text))
    assert (meta.enabled, meta.name, meta.version, meta.model, meta.type) == (
        True, "Rockbox", "2.1", "Y1", "Stock",
    )


def test_unreadable_ini_counts_as_disabled(tmp_path):
    assert read_fw_package_ini(_ini(tmp_path, "[fw_package\nbroken")).enabled is False
    assert read_fw_package_ini(tmp_path / "missing.ini").enabled is False


def test_read_package_with_complete_files(tmp_path):
    fw = _firmware(tmp_path / "fw", ini="[fw_package]\nname = Stock\nversion = 1.0\nmodel = Y2\n")
    pkg = read_package(fw)
    assert pkg is not None
    assert pkg.scatter.name == "MT6572_Android_scatter.txt"
    assert pkg.display_name == "Stock 1.0"
    assert pkg.model == "Y2"
    assert pkg.image is None


def test_read_package_finds_updater_image(tmp_path):
    fw = _firmware(tmp_path / "fw")
    _png(fw / "updater.png")
    assert read_package(fw).image == (fw / "updater.png").resolve()


def test_read_package_rejects_missing_listed_file(tmp_path):
    fw = _firmware(tmp_path / "fw", files=("preloader.bin",))
    assert read_package(fw) is None


def test_read_package_rejects_disabled_or_absent_ini(tmp_path):
    assert read_package(_firmware(tmp_path / "off", ini="false\n")) is None
    assert read_package(_firmware(tmp_path / "none", ini=None)) is None


def test_read_package_rejects_two_scatters(tmp_path):
    fw = _firmware(tmp_path / "fw")
    (fw / "other_scatter.txt").write_text(SCATTER, encoding="utf-8")
    assert read_package(fw) is None


def test_read_package_takes_release_info_from_app_folder_ini(tmp_path):
    app = tmp_path / "InnioasisUpdater"
    app.mkdir()
    (app / fw_package.FW_PACKAGE_INI).write_text(
        "[Firmware]\nsoftware_name = Rockbox\nversion = 2.5\ntarget_model = Y2\n", encoding="utf-8"
    )
    sp = _firmware(app / "SP_Flash_Tool", ini=None)
    pkg = read_package(sp, require_ini=True, app_dir=app)
    assert pkg is not None
    assert pkg.display_name == "Rockbox 2.5"
    assert pkg.model == "Y2"
    assert pkg.has_ini


def test_read_package_app_folder_ini_takes_precedence_over_package_ini(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / fw_package.FW_PACKAGE_INI).write_text("[Firmware]\nsoftware_name = App\nversion = 1\n", encoding="utf-8")
    sp = _firmware(tmp_path / "sp", ini="[Firmware]\nsoftware_name = Package\nversion = 9\n")
    assert read_package(sp, app_dir=app).display_name == "App 1"


def test_read_package_app_folder_ini_can_disable_package(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / fw_package.FW_PACKAGE_INI).write_text("false\n", encoding="utf-8")
    sp = _firmware(tmp_path / "sp", ini="[Firmware]\nsoftware_name = Stock\n")
    assert read_package(sp, app_dir=app) is None


def test_find_bundled_firmware_reads_app_folder_ini(tmp_path, monkeypatch):
    app = tmp_path / "InnioasisUpdater"
    app.mkdir()
    (app / fw_package.FW_PACKAGE_INI).write_text(
        "[Firmware]\nsoftware_name = Rockbox\nversion = 2.5\ntarget_model = Y2\n", encoding="utf-8"
    )
    sp = _firmware(app / "SP_Flash_Tool", ini=None)
    monkeypatch.setattr(fw_package, "find_sp_flash_tool", lambda: sp)
    pkg = fw_package.find_bundled_firmware(app_dir=app)
    assert pkg is not None
    assert pkg.display_name == "Rockbox 2.5"


def test_find_installed_package_checks_app_folder_then_parent(tmp_path):
    fw = _firmware(tmp_path / "zip_root")
    # App inside an 'installer' subfolder: its parent (the firmware root) holds the package.
    app_in_installer = fw / "installer"
    app_in_installer.mkdir(parents=True)
    assert find_installed_package(app_in_installer).folder == fw.resolve()

    # App beside the firmware in the root.
    assert find_installed_package(fw).folder == fw.resolve()


def test_find_installed_package_returns_none_without_ini(tmp_path):
    fw = _firmware(tmp_path / "plain", ini=None)
    assert find_installed_package(fw / "app") is None


def test_locate_package_finds_nested_zip_extraction(tmp_path):
    root = tmp_path / "extract"
    fw = _firmware(root / "Y2 Firmware", ini="[fw_package]\nname = Stock\n")
    assert locate_package(root).folder == fw.resolve()


def test_prune_keeps_only_scatter_listed_files_and_ini(tmp_path):
    root = tmp_path / "extract"
    fw = _firmware(root / "firmware")
    (fw / "unrelated.bin").write_bytes(b"x")
    (root / "installer" / "InnioasisUpdater").mkdir(parents=True)
    (root / "installer" / "InnioasisUpdater" / "InnioasisUpdater.exe").write_bytes(b"x")
    (root / "installer" / "Readme.txt").write_text("x", encoding="utf-8")

    removed = prune_to_firmware_package(root)

    remaining = sorted(p.name for p in root.rglob("*") if p.is_file())
    assert remaining == [
        "MT6572_Android_scatter.txt",
        "boot.img",
        "fw_package.ini",
        "preloader.bin",
    ]
    assert not (root / "installer").exists()
    assert len(removed) == 3


def test_prune_keeps_updater_image(tmp_path):
    root = tmp_path / "extract"
    fw = _firmware(root / "firmware")
    _png(fw / "updater.png")
    (fw / "unrelated.bin").write_bytes(b"x")

    prune_to_firmware_package(root)

    assert (fw / "updater.png").exists()
    assert not (fw / "unrelated.bin").exists()


def test_prefill_opens_install_from_file_with_name_version_and_type(tmp_path):
    _app()
    fw = _firmware(tmp_path / "fw", ini="[fw_package]\nname = Stock\nversion = 1.0\nmodel = Y2\n")
    _png(fw / "updater.png")
    page = SelectPackagePage()
    pkg = read_package(fw)
    assert pkg is not None

    page.prefill_fw_package(pkg)

    assert page._views.currentWidget() is page._local_tab
    assert page._path_edit.text() == str(fw.resolve())
    assert page._current_package_name == "Get your Y2 ready to install Stock 1.0"
    assert page._details_name.text() == "Stock"
    assert page._details_version.text() == "1.0"
    assert not page._details_header.isHidden()  # page is never shown in tests, so check the widget's own flag
    assert page.release_pixmap().isNull() is False


def test_prefill_selects_software_type_from_ini(tmp_path):
    _app()
    page = SelectPackagePage()
    wanted = page._type_combo.itemData(page._type_combo.count() - 1)
    assert wanted, "type combo should carry data keys"
    fw = _firmware(tmp_path / "fw", ini=f"[fw_package]\nname = Stock\ntype = {wanted}\n")
    page.prefill_fw_package(read_package(fw))
    assert page._type_combo.currentData() == wanted
    assert page._fw_type_index("no-such-type") == -1


def test_prefill_without_image_or_version_leaves_header_plain(tmp_path):
    _app()
    fw = _firmware(tmp_path / "fw", ini="[fw_package]\nname = Stock\n")
    page = SelectPackagePage()
    page.prefill_fw_package(read_package(fw))
    assert page._details_name.text() == "Stock"
    assert page._details_version.text() == ""


def test_browsed_zip_contents_fill_the_package_details(tmp_path, monkeypatch):
    _app()
    # Package details do not need a release listing; skip the network fetch so
    # no ReleasesWorker thread is left running when the test ends.
    monkeypatch.setattr(SelectPackagePage, "_refresh_releases", lambda self, *a, **k: None)
    root = tmp_path / "extract"
    fw = _firmware(root / "Y2 Firmware", ini="[fw_package]\nname = Stock\nversion = 3.0\n")
    page = SelectPackagePage()
    page._fw_package = locate_package(root)
    page._apply_fw_package(page._fw_package)
    assert page._current_package_name == "Get ready to install Stock 3.0"
    assert page._details_version.text() == "3.0"
    assert fw.exists()


def test_install_keeps_latest_archive_and_extract_and_drops_older_sets(tmp_path, monkeypatch):
    from src import downloads
    from src.flash_service import prune_extracted_cache

    dl = tmp_path / "app_downloads"
    dl.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: dl)

    latest = dl / "Y2.zip"
    latest.write_bytes(b"zip")
    (dl / ".Y2_extracted").mkdir()
    old = dl / "Old.zip"
    old.write_bytes(b"zip")
    (dl / ".Old_extracted").mkdir()

    removed = prune_extracted_cache(keep_package_path=str(latest))
    assert latest.exists()
    assert (dl / ".Y2_extracted").exists()
    assert not old.exists()
    assert not (dl / ".Old_extracted").exists()
    assert str(old) in removed


def test_browsing_a_package_does_not_prune_the_saved_set(tmp_path, monkeypatch):
    from src import downloads
    from types import SimpleNamespace
    from src.flash_service import ExtractWorker

    dl = tmp_path / "app_downloads"
    dl.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: dl)
    saved = dl / "Y2.zip"
    saved.write_bytes(b"zip")
    (dl / ".Y2_extracted").mkdir()
    (dl / ".Y2_extracted" / "marker").write_text("x")

    browsed = tmp_path / "user_files" / "Other Firmware"
    _firmware(browsed)
    # A folder package extracts without touching the archive or extract cache.
    # Only the progress signal of the worker is used, so a stub stands in for it
    # (a started-or-not QThread would leave the test process aborting at exit).
    stub = SimpleNamespace(progress=SimpleNamespace(emit=lambda *_: None))
    ExtractWorker._extract(stub, str(browsed))

    assert saved.exists()
    assert (dl / ".Y2_extracted" / "marker").exists()



def test_cached_extract_is_never_deleted_and_is_restored_in_place(tmp_path, monkeypatch):
    import zipfile
    from types import SimpleNamespace
    from src import downloads
    from src.flash_service import ExtractWorker

    dl = tmp_path / "app_downloads"
    dl.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: dl)

    staging = tmp_path / "staging"
    _firmware(staging, files=("preloader.bin", "boot.img"))
    archive = dl / "Y2.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for f in staging.iterdir():
            zf.write(f, f.name)

    stub = SimpleNamespace(progress=SimpleNamespace(emit=lambda *_: None), _cancelled=False)
    stub._extract_zip = lambda pkg, d: ExtractWorker._extract_zip(stub, pkg, d)
    stub._extract_rar = lambda pkg, d: None
    extract_dir = Path(ExtractWorker._extract(stub, str(archive)))
    (extract_dir / "keep.txt").write_text("cached")
    (extract_dir / "boot.img").unlink()

    # The archive is removed right after extraction; the cached folder is what remains.
    assert not archive.exists()

    # A normal install attempt never removes or re-extracts a cached folder.
    ExtractWorker._extract(stub, str(archive))
    assert not (extract_dir / "boot.img").exists()
    assert (extract_dir / "keep.txt").exists()

    # A forced re-extract over the cache rewrites files in place when the archive is still present.
    with zipfile.ZipFile(archive, "w") as zf:
        for f in staging.iterdir():
            zf.write(f, f.name)
    ExtractWorker._extract(stub, str(archive), force=True)
    assert (extract_dir / "boot.img").exists()
    assert not archive.exists()


def test_prune_does_nothing_without_a_valid_package(tmp_path):
    root = tmp_path / "extract"
    _firmware(root, files=("preloader.bin",))  # boot.img missing -> not a valid package
    (root / "extra.bin").write_bytes(b"x")
    assert prune_to_firmware_package(root) == []
    assert (root / "extra.bin").exists()


def test_stage_firmware_for_flashing(tmp_path, monkeypatch):
    from src.flash_service import stage_firmware_for_flashing
    from src import downloads

    dl = tmp_path / "downloads"
    dl.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: dl)

    fw = _firmware(tmp_path / "app_folder", files=("preloader.bin", "boot.img"))
    (fw / "_internal").mkdir()
    (fw / "extra_file.log").write_text("temporary stuff")
    scatter = fw / "MT6572_Android_scatter.txt"

    staged_scatter = stage_firmware_for_flashing(scatter)
    assert staged_scatter.is_file()
    assert staged_scatter.parent != fw
    assert (staged_scatter.parent / "preloader.bin").is_file()
    assert (staged_scatter.parent / "boot.img").is_file()
    assert (staged_scatter.parent / "fw_package.ini").is_file()
    assert not (staged_scatter.parent / "extra_file.log").exists()
    assert not (staged_scatter.parent / "_internal").exists()


def test_fw_package_example_ini_exists_and_valid():
    from src.paths import REPO_ROOT
    from src.fw_package import read_fw_package_ini

    example_ini = REPO_ROOT / "docs" / "fw_package_example.ini"
    assert example_ini.is_file(), f"Example INI missing at {example_ini}"
    meta = read_fw_package_ini(example_ini)
    assert meta.enabled is True
    assert meta.name == "Rockbox"
    assert meta.version == "2.5"
    assert meta.model == "Y1"
    assert meta.type == "Type A"
    assert "Rockbox build" in meta.description


def test_read_package_optional_ini(tmp_path):
    fw = _firmware(tmp_path / "plain_fw", ini=None)
    pkg = fw_package.read_package(fw, require_ini=False)
    assert pkg is not None
    assert pkg.scatter.name == "MT6572_Android_scatter.txt"
    assert pkg.has_ini is False
    assert "install software" in pkg.display_name.lower()


def test_find_bundled_firmware_in_sp_flash_tool(tmp_path, monkeypatch):
    sp_dir = tmp_path / "tools" / "SP_Flash_Tool"
    _firmware(sp_dir, ini="[Firmware]\nsoftware_name = Rockbox\nversion = 2.5\ntarget_model = Y1\n")
    (sp_dir / "rockbox_themes.txt").write_text("rockbox marker")

    monkeypatch.setattr(fw_package, "find_sp_flash_tool", lambda: sp_dir)
    pkg = fw_package.find_bundled_firmware(app_dir=tmp_path)
    assert pkg is not None
    assert pkg.folder == sp_dir.resolve()
    assert pkg.display_name == "Rockbox 2.5"
    assert pkg.model == "Y1"
    assert pkg.has_rockbox is True


def test_find_bundled_firmware_macos_app_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(fw_package, "IS_MAC", True)
    root = tmp_path / "macos_bundle"
    _firmware(root, ini="[Firmware]\nsoftware_name = Solar\nversion = 1.0\ntarget_model = Y2\n")

    app_dir = root / "Updater CE.app" / "Contents" / "MacOS"
    app_dir.mkdir(parents=True)
    exe = app_dir / "Updater CE"
    exe.write_bytes(b"dummy")

    monkeypatch.setattr(fw_package.sys, "executable", str(exe))
    pkg = fw_package.find_bundled_firmware(app_dir=app_dir)
    assert pkg is not None
    assert pkg.folder == root.resolve()
    assert pkg.display_name == "Solar 1.0"
    assert pkg.model == "Y2"


def test_find_bundled_firmware_rejects_multiple_scatters(tmp_path, monkeypatch):
    sp_dir = tmp_path / "tools" / "SP_Flash_Tool"
    _firmware(sp_dir)
    (sp_dir / "second_scatter.txt").write_text(SCATTER, encoding="utf-8")

    monkeypatch.setattr(fw_package, "find_sp_flash_tool", lambda: sp_dir)
    pkg = fw_package.find_bundled_firmware(app_dir=tmp_path)
    assert pkg is None


def test_prime_sp_tool_history_ini(tmp_path):
    sp_dir = tmp_path / "SP_Flash_Tool"
    sp_dir.mkdir()
    scatter = sp_dir / "MT6572_Android_scatter.txt"
    scatter.write_text("scatter")

    fw_package.prime_sp_tool_history_ini(sp_dir, scatter)
    hist = sp_dir / "history.ini"
    assert hist.is_file()
    text = hist.read_text(encoding="utf-8")
    assert "scatterHistory=MT6572_Android_scatter.txt" in text
    assert "downloadAgentHistory=MTK_AllInOne_DA.bin" in text
    assert "lastDir=" in text


def test_stage_bundled_firmware(tmp_path, monkeypatch):
    from src import downloads

    dl = tmp_path / "downloads"
    dl.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: dl)

    fw = _firmware(tmp_path / "sp_dir", ini="[Firmware]\nname=Custom\n")
    (fw / "install_rockbox.txt").write_text("rockbox marker")

    pkg = fw_package.read_package(fw, require_ini=False)
    assert pkg is not None
    staged = fw_package.stage_bundled_firmware(pkg)
    assert staged.is_file()
    assert staged.parent != fw
    assert (staged.parent / "boot.img").is_file()
    assert (staged.parent / "install_rockbox.txt").is_file()
    assert (fw / "history.ini").is_file()


def test_auto_start_bundled_firmware_navigates_to_flash_page(tmp_path, monkeypatch):
    from src.ui.main_window import MainWindow, _PAGE_FLASH

    fw = _firmware(tmp_path / "sp_tool_fw", ini="[Firmware]\nsoftware_name = Solar\nversion = 2.0\ntarget_model = Y1\n")
    (fw / "rockbox_pack.txt").write_text("marker")

    monkeypatch.setattr(fw_package, "find_bundled_firmware", lambda *a, **k: fw_package.read_package(fw, require_ini=False))

    app = _app()
    w = MainWindow()
    try:
        w._prefill_fw_package()
        app.processEvents()

        assert w._stack.currentIndex() == _PAGE_FLASH
        assert w._package_name == "Get your Y1 ready to install Solar 2.0"
        assert w._package_model == "Y1"
        assert w._is_360p_rockbox is True
    finally:
        w.close()
        app.processEvents()


def test_cache_missing_failure_clears_firmware_cache(tmp_path, monkeypatch):
    from src import downloads
    from src.ui.main_window import MainWindow

    dl = tmp_path / "downloads"
    dl.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: dl)
    extract = dl / ".Y1_extracted"
    extract.mkdir()
    (extract / "boot.img").write_bytes(b"x")
    archive = dl / "Y1.zip"
    archive.write_bytes(b"zip")

    app = _app()
    w = MainWindow()
    try:
        w._show_install_failure("CACHE_FILE_MISSING")
        app.processEvents()
        assert not extract.exists()
        assert not archive.exists()
        assert w._cache_recovery_needed is True

        # A repeat report during the same run does not clear again.
        (dl / ".Y1_extracted").mkdir()
        w._handle_backend_line("FileNotFoundError: [Errno 2] No such file or directory")
        assert (dl / ".Y1_extracted").exists()
    finally:
        w.close()
        app.processEvents()


def test_release_install_heading_uses_release_name_from_file_name():
    assert fw_package.release_install_heading("Y2", ("Rockbox_2.5.zip",)) == "Get your Y2 ready to install Rockbox 2.5"


def test_release_install_heading_falls_back_to_generic_label():
    assert fw_package.release_install_heading("Y2", ("rom_y2.zip",)) == "Get your Y2 ready to install software"
    assert fw_package.release_install_heading("", ()) == "Get ready to install software"


def test_package_install_heading_prefers_release_name():
    named = _pkg_for(name="Rockbox", version="2.5", model="Y2")
    assert fw_package.package_install_heading(named) == "Get your Y2 ready to install Rockbox 2.5"
    generic = _pkg_for(name="Installing software on Y2", model="Y2")
    assert fw_package.package_install_heading(generic) == "Get your Y2 ready to install software"
    generic_new = _pkg_for(name="Get your Y2 ready to install software", model="Y2")
    assert fw_package.package_install_heading(generic_new) == "Get your Y2 ready to install software"


def test_rockbox_240p_package_name_marks_small_screen_rockbox_rom():
    assert fw_package.rockbox_240p_package_name("Rockbox", "rockbox_y1_240p.zip") is True
    assert fw_package.rockbox_240p_package_name("Rockbox 2.5", "rom_240.zip") is True
    # Plain 360p Rockbox builds are not 240p ROMs.
    assert fw_package.rockbox_240p_package_name("Rockbox", "Rockbox_2.5.zip") is False
    # A 240p tag without Rockbox is not a Rockbox ROM either.
    assert fw_package.rockbox_240p_package_name("Stock", "stock_y1_240p.zip") is False
    assert fw_package.rockbox_240p_package_name("Rockbox") is False


def test_read_package_flags_240p_rockbox_without_bundled_flow(tmp_path):
    folder = _firmware(
        tmp_path / "pkg",
        ini="[Firmware]\nsoftware_name = Rockbox\nversion = 2.5\ntarget_model = Y1\n",
    )
    pkg = read_package(folder, source_names=("rockbox_y1_240p.zip",))
    assert pkg is not None
    assert pkg.has_rockbox_240p is True
    # The 240p build skips the bundled 360p theme push.
    assert pkg.has_rockbox is False


def _pkg_for(**kwargs):
    return fw_package.FwPackage(folder=Path("."), scatter=Path("x_scatter.txt"), **kwargs)


def test_read_package_infers_name_from_source_zip_name(tmp_path):
    folder = _firmware(tmp_path / "pkg", ini=None)
    pkg = read_package(folder, require_ini=False, source_names=("Rockbox_2.5.zip",))
    assert pkg is not None
    assert pkg.name == "Rockbox"
    assert pkg.version == "2.5"


def test_read_package_finds_updater_image_beside_app(tmp_path):
    if fw_package.IS_MAC:
        pytest.skip("macOS only reads fw_package.ini from the package folder")
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    _ini(app_dir, "[Firmware]\nsoftware_name = Rockbox\nversion = 2.5\n")
    _png(app_dir / "updater.png")
    folder = _firmware(tmp_path / "pkg", ini=None)
    pkg = read_package(folder, require_ini=True, app_dir=app_dir)
    assert pkg is not None
    assert pkg.image == (app_dir / "updater.png").resolve()


def test_local_icon_found_beside_scatter_file(tmp_path):
    from src.release_icons import load_local_package_icon_bytes

    folder = _firmware(tmp_path / "pkg")
    _png(folder / "updater.png")
    data = load_local_package_icon_bytes(folder / "MT6572_Android_scatter.txt", dark=False)
    assert data is not None and len(data) > 0
    assert load_local_package_icon_bytes(tmp_path / "missing_scatter.txt", dark=False) is None
