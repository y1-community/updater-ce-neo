"""Cache recovery: a firmware whose files went missing is fetched again.

A missing image inside the extracted package used to surface as
CACHE_FILE_MISSING on the install card, while the retry kept reusing the same
"complete" extract. These tests pin the three rules that make the recovery
work: a cached extract is only complete while its images are present, pruning
never deletes a declared image over letter case, and a purge touches only the
release being reinstalled.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src import downloads
from src.catalog import FirmwarePackage
from src.flash_service import (
    EXTRACT_COMPLETE_MARKER,
    _is_extract_complete,
    prune_extracted_cache,
)
from src.fw_package import prune_to_firmware_package
from src.ui.select_page import SelectPackagePage

SCATTER = """\
- partition_index: SYS0
  partition_name: PRELOADER
  file_name: preloader_k47.bin
  is_download: true
- partition_index: SYS1
  partition_name: BOOTIMG
  file_name: boot.img
  is_download: true
"""


def _cached_package(tmp_path, *, with_images=True):
    folder = tmp_path / ".rom_extracted"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "MT6582_Android_scatter.txt").write_text(SCATTER, encoding="utf-8")
    if with_images:
        (folder / "preloader_k47.bin").write_bytes(b"preloader")
        (folder / "boot.img").write_bytes(b"boot")
    (folder / EXTRACT_COMPLETE_MARKER).write_text("ok", encoding="utf-8")
    return folder


def test_a_marked_extract_missing_an_image_is_not_reusable(tmp_path):
    folder = _cached_package(tmp_path, with_images=False)
    assert _is_extract_complete(folder) is False

    (folder / "preloader_k47.bin").write_bytes(b"preloader")
    (folder / "boot.img").write_bytes(b"boot")
    assert _is_extract_complete(folder) is True


def test_pruning_keeps_an_image_whose_name_differs_only_in_case(tmp_path):
    folder = _cached_package(tmp_path)
    # The player's storage is case-insensitive; a Linux host is not, so the
    # scatter's lowercase name must not delete the uppercase image.
    (folder / "PRELOADER_K47.BIN").write_bytes(b"preloader")
    (folder / "notes.txt").write_text("extra", encoding="utf-8")

    prune_to_firmware_package(folder)

    assert (folder / "PRELOADER_K47.BIN").exists()
    assert not (folder / "notes.txt").exists()


def test_a_missing_archive_reaches_the_window_as_a_cache_failure():
    """The re-download only runs for CACHE_FILE_MISSING, never INTERNAL_ERROR.

    ``_do_flash`` raises the cache code when the archive and its extract are
    both gone. Reporting that as INTERNAL_ERROR bypassed the recovery and left
    the user on a card whose only retry used the same missing files.
    """
    from src.flash_service import FlashWorker, failure_code_for_exception

    codes = []
    worker = FlashWorker("/tmp/downloads/rondo_v1_rom.zip")

    def _missing_cache():
        raise RuntimeError("CACHE_FILE_MISSING")

    worker._do_flash = _missing_cache
    worker._start_usb_watchdog = lambda: None
    worker.finished.connect(lambda ok, code: codes.append((ok, code)))
    worker.run()

    assert codes == [(False, "CACHE_FILE_MISSING")]
    assert failure_code_for_exception(RuntimeError("boom")) == "INTERNAL_ERROR"


def test_a_cache_failure_downloads_the_package_before_reporting_it():
    """The install card is not shown while the release is fetched again."""
    from src.ui.main_window import MainWindow

    prompts = []
    logs = []

    class _Page:
        _current_selected_rel = {
            "tag_name": "v1",
            "download_url": "https://example.test/rom.zip",
        }

        def retry_missing_cached_firmware(self, *, prompt=True):
            prompts.append(prompt)

    class _Window:
        _package_path = "/tmp/downloads/rondo_v1_rom.zip"
        _cache_recovery_needed = False
        _cache_recovered_package = ""
        _select_page = _Page()

        def _append_log(self, text):
            logs.append(text)

        def _on_progress_retry(self, *, prompt=True):
            prompts.append(prompt)

    window = _Window()
    assert MainWindow._recover_missing_firmware(window, "FileNotFoundError") is True
    assert prompts == [False], "a run under way must not prompt again"
    assert any("downloading the package" in line for line in logs)

    # Once per package: a second failure after a fresh download is real.
    assert MainWindow._recover_missing_firmware(window, "FileNotFoundError") is False


def test_a_local_firmware_is_not_fetched_over_the_network():
    from src.ui.main_window import MainWindow

    class _Page:
        _current_selected_rel = None

    class _Window:
        _package_path = "/tmp/local/rom.zip"
        _cache_recovered_package = ""
        _select_page = _Page()

        def _append_log(self, text):
            pass

    assert MainWindow._recover_missing_firmware(_Window(), "missing") is False


def test_pruning_keeps_the_active_archive_and_extract_regardless_of_case(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    archive = tmp_path / "Rockbox-Y1.zip"
    archive.write_bytes(b"zip")
    extract = tmp_path / ".Rockbox-Y1_extracted"
    extract.mkdir()

    removed = prune_extracted_cache(keep_package_path=tmp_path / "rockbox-y1.zip")

    assert removed == []
    assert archive.exists()
    assert extract.exists()


def test_retry_keeps_the_extracted_cache_and_does_not_download_again(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    archive = tmp_path / "rondo_v1_rom.zip"
    archive.write_bytes(b"zip")
    extract = _cached_package(tmp_path)
    calls = []

    class _Page:
        _current_selected_rel = {"tag_name": "v1", "asset_name": "rom.zip"}
        _current_package_path = str(archive)
        _current_installed_release_info = {"tag_name": "v1"}
        _pending_install_release_info = None
        _details_package = object()

        def current_package(self):
            return self._details_package

        def _release_cache_paths(self, rel, package):
            return archive, extract

        def purge_release_cache(self, *args, **kwargs):
            raise AssertionError("a retry must not purge the extracted cache")

        def _trigger_release_install(self, *args, **kwargs):
            raise AssertionError("a complete extract must be reused, not downloaded")

        def _on_online_prep_done(self, ok, extract_dir, err):
            calls.append((ok, extract_dir, err))

    SelectPackagePage.retry_missing_cached_firmware(_Page())  # type: ignore[arg-type]

    assert calls == [(True, str(extract), "")]
    assert archive.exists()
    assert extract.exists()


def test_leaving_the_journey_releases_only_its_own_cache():
    from src.ui.main_window import MainWindow

    calls = []
    logs = []
    release = {"tag_name": "v1", "asset_name": "rondo_v1_rom.zip"}
    package = object()

    class _Page:
        _current_selected_rel = release
        _details_package = package

        def purge_release_cache(self, rel=None, pkg=None):
            calls.append((rel, pkg))
            return ["/tmp/downloads/rom.zip"]

    class _Window:
        _package_path = "/tmp/downloads/rondo_v1_rom.zip"
        _select_page = _Page()

        def _append_log(self, text):
            logs.append(text)

    MainWindow._release_install_cache(_Window())
    _Window._package_path = "/tmp/downloads/.rondo_v1_rom_extracted"
    MainWindow._release_install_cache(_Window())

    assert calls == [(release, package), (release, package)]
    assert any("after leaving the install" in line for line in logs)


def test_purging_one_release_keeps_another_release_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "downloads_dir", lambda: tmp_path)
    package = FirmwarePackage(
        slug="rondo",
        name="Rondo",
        model="Y1",
        repo="jarvissax/rondo",
        package_name="rom.zip",
    )
    archive = tmp_path / "rondo_v1_rom.zip"
    archive.write_bytes(b"zip")
    extract = tmp_path / ".rondo_v1_rom_extracted"
    extract.mkdir()
    (extract / "boot.img").write_bytes(b"boot")
    other = tmp_path / "solar_v1_rom.zip"
    other.write_bytes(b"zip")

    class _Page:
        _current_selected_rel = None
        _details_package = None
        _release_cache_paths = SelectPackagePage._release_cache_paths
        purge_release_cache = SelectPackagePage.purge_release_cache

        def current_package(self):
            return None

    removed = _Page().purge_release_cache(
        {"tag_name": "v1", "asset_name": "rom.zip"}, package=package
    )

    assert not archive.exists()
    assert not extract.exists()
    assert other.exists(), "another release's firmware must be left alone"
    assert len(removed) == 2
