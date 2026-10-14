"""Our guided flow must consume the bundled (Chinese-fork) DA's reporting.

The vendored mtkclient is the reference build's fork, and its legacy DA reports
failures through ``config.last_error`` (status code, chunk index, offset) and
takes its transfer size from ``config.write_chunk_size``. Both are the seam
between that library and this app: if either stops being set or read, a failed
write degrades to a bare "WRITE_FAILED" and the device is left half written with
nothing useful to tell the user.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import flash_service  # noqa: E402


class _Daloader:
    flashmode = "LEGACY"

    def __init__(self, ok=True):
        self.ok = ok

    def writeflash(self, **_kwargs):
        return self.ok


class _Config:
    write_chunk_size = None
    last_error = None


class _Mtk:
    def __init__(self, ok=True):
        self.config = _Config()
        self.daloader = _Daloader(ok)


def test_legacy_da_status_reaches_the_failure_message():
    """A DA NACK becomes a message naming the status, not a bare failure."""
    worker = flash_service.FlashWorker(package_path="", method="mtk")
    mtk = _Mtk(ok=False)
    mtk.config.last_error = (
        "device returned NACK(0xA5) at chunk 3/40 offset=0x200000/0x1400000 "
        "status=0x00002301 DA_STORAGE_WRITE_FAIL"
    )

    class _Handler:
        pass

    with pytest.raises(RuntimeError) as err:
        worker._write_mtk_partition(
            _Handler(), mtk, "system", "does-not-need-to-exist.img", "user",
            offset=0x1000, write_length=4096,
        )
    assert "DA_STORAGE_WRITE_FAIL" in str(err.value)
    assert "system" in str(err.value)


def test_legacy_write_chunk_size_matches_the_reference_table(tmp_path):
    """512 KiB normally, 256 KiB once an image is 1.5 GB or larger."""
    small = tmp_path / "boot.img"
    small.write_bytes(b"\0" * 1024)
    assert flash_service.legacy_write_chunk_size([small]) == flash_service.LEGACY_WRITE_CHUNK_BYTES == 524288

    large = tmp_path / "system.img"
    with open(large, "wb") as fh:
        fh.truncate(flash_service.LEGACY_WRITE_CHUNK_LARGE_IMAGE_BYTES)
    assert flash_service.legacy_write_chunk_size([small, large]) == 262144
    # A path that is gone must not break the decision.
    assert flash_service.legacy_write_chunk_size([tmp_path / "missing.img"]) == 524288


def test_worker_hands_the_chunk_size_to_the_bundled_da(tmp_path):
    """The DA reads ``config.write_chunk_size``; our worker has to set it."""
    image = tmp_path / "boot.img"
    image.write_bytes(b"\0" * 512)
    worker = flash_service.FlashWorker(package_path="", method="mtk")
    worker._log = lambda *_a, **_k: None
    mtk = _Mtk()

    applied = worker._apply_mtk_write_strategy(mtk, [("BOOTIMG", image)])

    assert applied == 524288
    assert mtk.config.write_chunk_size == 524288


def test_non_legacy_da_keeps_mtkclients_own_default(tmp_path):
    image = tmp_path / "boot.img"
    image.write_bytes(b"\0" * 512)
    worker = flash_service.FlashWorker(package_path="", method="mtk")
    worker._log = lambda *_a, **_k: None
    mtk = _Mtk()
    mtk.daloader.flashmode = "XFLASH"

    assert worker._apply_mtk_write_strategy(mtk, [("BOOTIMG", image)]) == 0
    assert mtk.config.write_chunk_size is None


def test_bundled_fork_still_exposes_the_hooks_we_depend_on():
    """Guard against a future sync that drops the fork's reporting hooks."""
    source = (
        Path(__file__).resolve().parent.parent
        / "vendor" / "mtkclient" / "mtkclient" / "Library" / "DA" / "legacy" / "dalegacy_lib.py"
    ).read_text(encoding="utf-8", errors="ignore")
    assert "write_chunk_size" in source
    assert "last_error" in source
