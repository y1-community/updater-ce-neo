"""Tests verifying MTKClient write plan and sparse partition tracking parity with Chinese 1.1.7 build."""

import shutil
from pathlib import Path
from unittest.mock import MagicMock

from src import flash_service as fs

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "compat"


def _create_pkg_fixture(tmp_path, scatter_name="MT6572_Android_scatter.txt", real_tables=True):
    pkg_dir = tmp_path / "firmware"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    scatter = pkg_dir / "MT6572_Android_scatter.txt"
    shutil.copy(ASSETS / scatter_name, scatter)

    # Read expected files from scatter
    for line in scatter.read_text().splitlines():
        line = line.strip()
        if line.startswith("file_name:"):
            fname = line.split(":", 1)[1].strip()
            if fname and fname.upper() != "NONE":
                target = pkg_dir / fname
                if fname in {"MBR", "EBR1", "EBR2"} and not real_tables:
                    target.write_bytes(b"\x00" * 510 + b"\x55\xaa")
                elif fname in {"MBR", "EBR1", "EBR2"}:
                    # Genuine partition table: non-zero table entries with 0x55AA signature
                    data = bytearray(512)
                    data[446] = 0x80  # bootable
                    data[450] = 0x83  # Linux native
                    data[510:512] = b"\x55\xaa"
                    target.write_bytes(bytes(data))
                else:
                    target.write_bytes(b"DATA" * 1024)
    return scatter


def test_genuine_mbr_and_ebr_included_in_plan(tmp_path):
    scatter = _create_pkg_fixture(tmp_path, real_tables=True)
    plan = fs.build_mtk_write_plan(scatter, "Y1", preloader_first=False)
    names = [step["name"].upper() for step in plan]

    assert "MBR" in names
    assert "EBR1" in names
    assert "UBOOT" in names
    assert "BOOTIMG" in names
    assert "RECOVERY" in names
    assert "SEC_RO" in names
    assert "LOGO" in names
    assert "ANDROID" in names
    assert "CACHE" in names
    assert "USRDATA" in names


def test_preloader_last_in_actual_flash_plan(tmp_path):
    scatter = _create_pkg_fixture(tmp_path, real_tables=True)
    plan = fs.build_mtk_write_plan(scatter, "Y1", preloader_first=False)

    # Preloader must be at the very end
    assert plan[-1]["name"].upper() == "PRELOADER"
    # Preloader is parttype boot1
    assert plan[-1]["parttype"] == "boot1"
    assert plan[-1]["offset"] == 0


def test_sparse_partition_progress_and_completion_tracking(tmp_path, monkeypatch):
    """Sparse partitions track actual data extents (sum of region lengths) to reach 100%."""
    worker = fs.FlashWorker("dummy.zip", method="mtk")
    logged = []
    worker._log = lambda msg: logged.append(str(msg))

    # Sparse regions: 3 extents totalling 30MB in a 1920MB container
    regions = [
        (0x0, 10 * 1024 * 1024),
        (0x10000000, 10 * 1024 * 1024),
        (0x70000000, 10 * 1024 * 1024),
    ]
    raw_path = tmp_path / "raw_userdata.bin"
    # Container raw file is 2GB
    raw_path.write_bytes(b"\x00" * 1024)  # Small mock file for getsize mock
    monkeypatch.setattr(fs.os.path, "getsize", lambda p: 2000 * 1024 * 1024)

    mock_daloader = MagicMock()
    mock_daloader.writeflash.return_value = True
    mock_mtk = MagicMock()
    mock_mtk.daloader = mock_daloader

    progress_calls = []
    def on_seg_done(bytes_done):
        progress_calls.append(bytes_done)

    worker._write_mtk_sparse_regions(
        mtk=mock_mtk,
        part_name="USRDATA",
        raw_path=str(raw_path),
        regions=regions,
        offset=0x4C140000,
        parttype="user",
        bytes_progress_cb=on_seg_done,
        segment_bytes=0,
    )

    # Exactly 3 segments written
    assert mock_daloader.writeflash.call_count == 3
    assert sum(progress_calls) == 30 * 1024 * 1024

    # Verify Chinese 1.1.7 log output parity: Region write complete
    summary_logs = [l for l in logged if "Region write complete:" in l]
    assert len(summary_logs) == 1
    assert "data=30.0MB" in summary_logs[0]
    assert "regions=3" in summary_logs[0]
