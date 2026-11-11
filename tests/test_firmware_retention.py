"""A retry must never find the firmware gone.

Extraction disposes of the downloaded archive once the extracted working
directory is complete; a retry reads that directory. A local archive the reader
browsed is never deleted, and a missing downloaded archive still flashes the
files already on disk.
"""

import os
import time
import zipfile
from pathlib import Path

import pytest

from src import config, downloads
from src.flash_service import ExtractWorker
from src.fw_package import read_fw_package_ini

SCATTER = """\
- general: MTK_PLATFORM_CFG
  info:
    - config_version: V1.1.1
      platform: MT6582
      project: y2_baseline
      storage: EMMC
      boot_channel: MSDC_0
      block_size: 0x20000

- partition_index: SYS0
  partition_name: boot
  file_name: boot.img
  is_download: true
  type: SV5_BL_BIN
  linear_start_addr: 0x0
  physical_start_addr: 0x0
  partition_size: 0x1000
  region: EMMC_USER

- partition_index: SYS1
  partition_name: userdata
  file_name: NONE
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x1000
  physical_start_addr: 0x1000
  partition_size: 0x2000
  region: EMMC_USER
"""


def _package(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MT6582_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")
    (folder / "boot.img").write_bytes(b"\0" * 64)
    archive = folder.parent / "rom_y2.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for item in folder.iterdir():
            zf.write(item, item.name)
    return archive


@pytest.fixture()
def downloads_root(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    return tmp_path


def test_extraction_disposes_of_the_downloaded_archive(downloads_root):
    archive = _package(downloads_root / "src")
    extracted = Path(ExtractWorker(str(archive))._extract(str(archive)))

    assert (extracted / "boot.img").is_file()
    assert not archive.exists(), "the extracted folder is the retained copy"


def test_a_local_archive_is_never_deleted(tmp_path, monkeypatch):
    downloads_root = tmp_path / "downloads"
    downloads_root.mkdir()
    monkeypatch.setattr(downloads, "downloads_dir", lambda: downloads_root)
    archive = _package(tmp_path / "user")

    extracted = Path(ExtractWorker(str(archive))._extract(str(archive)))

    assert extracted.is_dir()
    assert archive.is_file(), "a browsed archive belongs to the user"


def test_a_missing_archive_uses_the_files_still_on_disk(downloads_root):
    archive = _package(downloads_root / "src")
    first = Path(ExtractWorker(str(archive))._extract(str(archive)))
    assert not archive.exists()

    again = Path(ExtractWorker(str(archive))._extract(str(archive)))

    assert again == first
    assert (again / "boot.img").is_file(), "a retry flashes what is on disk"


def test_a_missing_archive_with_nothing_on_disk_is_cache_missing(downloads_root):
    archive = _package(downloads_root / "src")
    archive.unlink()

    with pytest.raises(RuntimeError, match="CACHE_FILE_MISSING"):
        ExtractWorker(str(archive))._extract(str(archive))


def test_model_naming_rules_ignore_packaging_and_repo_names():
    # MX-prefixed zips are the manufacturer's packaging of the same names.
    assert config.detect_model_and_type_from_name("MX_rom_type_b.zip") == ("Y1", "B")
    assert config.detect_model_and_type_from_name("MX-rom_a5.zip") == ("A5", None)
    # rom.zip is the stock Y1 image; rom_type_b is its pre-2.0 revision.
    assert config.detect_model_and_type_from_name("rom.zip") == ("Y1", "A")
    assert config.detect_model_and_type_from_name("rom_type_b.zip") == ("Y1", "B")
    # A repository name is not a model identifier.
    assert config.detect_model_and_type_from_name("y1-community/rockbox-3.0.zip") == ("", None)


def test_fw_package_ini_lists_rom_derivatives(tmp_path):
    ini = tmp_path / "fw_package.ini"
    ini.write_text(
        "[fw_package]\n"
        "software_name = Rockbox\n"
        "target_model = Y1\n"
        "derivatives = Original Software, Rockbox\n",
        encoding="utf-8",
    )

    assert read_fw_package_ini(ini).derivatives == ("Original Software", "Rockbox")


def test_mtk_failures_are_routed_to_the_power_cycle_step():
    from src.ui.main_window import _is_mtk_install_failure

    assert _is_mtk_install_failure("MTK_INIT_FAILED")
    assert _is_mtk_install_failure("MTK_INSTALL_FAILED")
    assert _is_mtk_install_failure("WRITE_FAILED")
    # Engine-agnostic failures keep their own cards.
    assert not _is_mtk_install_failure("S_DA_INIT_SYNC_ERROR (1093)")
    assert not _is_mtk_install_failure("CACHE_FILE_MISSING")


def test_manifest_entries_bring_new_models_without_code_changes():
    """A hosted release for another model is listed from slidia_manifest.xml alone.

    The app must be ready to serve Timmkoo/Innioasis firmware for models it has
    no built-in catalogue row for (G5, Q8, D1, ...) as soon as the manifest
    names one: the model becomes selectable and its package is a normal row.
    """
    from src import catalog, manifest

    xml = (
        '<?xml version="1.0"?>'
        "<catalog>"
        '<package name="Stock" repo="innioasis/g5-stock" device="G5" '
        'url="https://example.com/g5-stock.zip" tag="v1.0.0" '
        'type="img" handler="Custom Firmware" />'
        "</catalog>"
    )
    original = list(catalog.LIVE_CATALOG)
    try:
        entries = manifest.parse_manifest_xml(xml)
        assert len(entries) == 1
        catalog.set_live_catalog(entries)
        assert "G5" in catalog.available_models()
        rows = catalog.packages_for_model("G5")
        assert rows and rows[0].name == "Stock"
    finally:
        catalog.set_live_catalog(original)


def _cached_set(root: Path, stem: str, *, scatter: bool = True, old_seconds: float = 0.0):
    """One firmware archive and the extract beside it, as the app caches them."""
    archive = root / f"{stem}.zip"
    archive.write_bytes(b"zip")
    extract = root / f".{stem}_extracted"
    package = extract / f"{stem}-content"
    package.mkdir(parents=True)
    if scatter:
        (package / "MT6582_Android_scatter.txt").write_text("scatter", encoding="utf-8")
    (package / "Y2ROOT.img").write_bytes(b"\0" * 1024)
    if old_seconds:
        stamp = time.time() - old_seconds
        for cached in (package, extract, archive):
            os.utime(cached, (stamp, stamp))
    return archive, extract


def test_retention_keeps_only_the_newest_flashable_set(downloads_root):
    latest_archive, latest_extract = _cached_set(downloads_root, "Latest")
    old_archive, old_extract = _cached_set(downloads_root, "Older", old_seconds=3600)
    staged = downloads_root / "staged_firmware" / "stage_abc_MT6582_Android_scatter"
    staged.mkdir(parents=True)
    (staged / "Y2ROOT.img").write_bytes(b"\0")
    part = downloads_root / "Something.zip.part"
    part.write_bytes(b"part")
    os.utime(part, (time.time() - 2 * 86400,) * 2)
    theme_temp = downloads_root / "themepack_download_0.zip"
    theme_temp.write_bytes(b"zip")

    removed, freed = downloads.enforce_firmware_retention()

    assert latest_extract.is_dir() and latest_archive.is_file()
    assert not old_extract.exists(), "an older install's images are not kept"
    assert not old_archive.exists()
    assert not staged.exists(), "the flash's staged copy is rebuilt per install"
    assert not part.exists() and not theme_temp.exists()
    assert removed == 6 and freed > 0


def test_retention_drops_an_extract_that_can_no_longer_flash(downloads_root):
    # Exactly what a half-cleaned run leaves: every image, no scatter, no marker.
    archive, extract = _cached_set(downloads_root, "Broken", scatter=False)

    removed, _freed = downloads.enforce_firmware_retention()

    assert not extract.exists()
    assert not archive.exists(), "an archive whose extract is unusable is not a retry copy"
    assert removed == 2


def test_retention_keeps_the_set_the_run_named(downloads_root):
    active_archive, active_extract = _cached_set(downloads_root, "Active", old_seconds=3600)
    newer_archive, newer_extract = _cached_set(downloads_root, "Newer")

    downloads.enforce_firmware_retention(keep_package_path=str(active_archive))

    assert active_extract.is_dir() and active_archive.is_file()
    assert not newer_extract.exists() and not newer_archive.exists()


def test_clear_app_storage_clears_caches_and_logs_but_keeps_settings(tmp_path, monkeypatch):
    from src import paths

    support = tmp_path / "support"
    downloads_root = support / "downloads"
    downloads_root.mkdir(parents=True)
    monkeypatch.setattr(downloads, "downloads_dir", lambda: downloads_root)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(downloads.tempfile, "gettempdir", lambda: str(temp_root))

    cache_root = support / ".cache"
    (cache_root / "releases").mkdir(parents=True)
    (cache_root / "releases" / "repo.json").write_text("{}", encoding="utf-8")
    lock = cache_root / "updater-ce-501.busy.json"
    lock.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(paths, "app_cache_dir", lambda: cache_root)

    log_dir = support / "logs"
    log_dir.mkdir()
    tool_log = log_dir / "mtkclient.log"
    tool_log.write_text("x" * 4096, encoding="utf-8")
    root_log = support / "updater.log"
    root_log.write_text("y" * 2048, encoding="utf-8")
    settings_file = support / "settings.ini"
    settings_file.write_text("[app]", encoding="utf-8")
    _cached_set(downloads_root, "Latest")

    removed, freed = downloads.clear_app_storage()

    assert removed >= 2 and freed > 0
    assert not (downloads_root / ".Latest_extracted").exists()
    assert not (cache_root / "releases").exists(), "remote data is fetched again"
    assert lock.is_file(), "the single-instance marker is live bookkeeping"
    assert settings_file.is_file(), "settings are never touched"
    assert tool_log.is_file() and tool_log.read_text(encoding="utf-8") == ""
    assert root_log.read_text(encoding="utf-8") == ""
