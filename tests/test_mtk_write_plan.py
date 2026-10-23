"""The MTKClient write plan must match the shipped firmware_downloader.py.

These are the differences that damaged bootloaders: writing the preloader last,
overwriting the device's MBR/EBR with the package's empty stubs, and writing
Y1 by scatter addresses when its scatter carries none.
"""

import shutil
from pathlib import Path

from src import flash_service as fs

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "compat"


def _fixture(tmp_path, scatter_name, stub_tables=True):
    # A private folder: _resolve_image_file() also scans the parent, and pytest
    # tmp dirs share one parent across tests.
    tmp_path = tmp_path / "fw"
    tmp_path.mkdir()
    scatter = tmp_path / "Android_scatter.txt"
    shutil.copy(ASSETS / scatter_name, scatter)
    files = set()
    for line in scatter.read_text().splitlines():
        line = line.strip()
        if line.startswith("file_name:"):
            name = line.split(":", 1)[1].strip()
            if name and name.upper() != "NONE":
                files.add(name)
    for name in files:
        path = tmp_path / name
        if stub_tables and name in {"MBR", "EBR1", "EBR2"}:
            path.write_bytes(b"\x00" * 510 + b"\x55\xaa")
        else:
            path.write_bytes(b"payload" * 512)
    return scatter


def test_y1_is_written_by_partition_name_in_the_reference_order(tmp_path):
    plan = fs.build_mtk_write_plan(_fixture(tmp_path, "MT6572_Android_scatter.txt"), "Y1")

    assert [step["name"] for step in plan] == [
        "logo",
        "uboot",
        "bootimg",
        "recovery",
        "android",
        "usrdata",
    ]
    assert all(step["offset"] is None for step in plan), "Y1 has no usable scatter addresses"
    assert all(step["parttype"] == "user" for step in plan)
    # The reference Y1 path writes no preloader and no partition table.
    assert "PRELOADER" not in [step["name"].upper() for step in plan]
    assert not any(step["name"].lower().startswith("ebr") for step in plan)


def test_y2_writes_the_preloader_first_at_boot1_and_skips_table_stubs(tmp_path):
    plan = fs.build_mtk_write_plan(_fixture(tmp_path, "MT6582_Android_scatter.txt"), "Y2")

    assert plan[0]["name"] == "PRELOADER"
    assert plan[0]["parttype"] == "boot1"
    assert plan[0]["offset"] == 0
    names = [step["name"] for step in plan]
    assert "MBR" not in names and "EBR1" not in names and "EBR2" not in names
    by_name = {step["name"]: step for step in plan}
    assert by_name["UBOOT"]["offset"] == 0x1D20000
    assert by_name["ANDROID"]["offset"] == 0x5180000
    assert by_name["USRDATA"]["offset"] == 0x40380000
    # Full image lengths: nothing is capped to the scatter partition size and
    # no signed wrapper is stripped.
    assert by_name["ANDROID"]["length"] == (by_name["ANDROID"]["path"]).stat().st_size


def test_a_real_partition_table_image_is_not_silently_dropped(tmp_path):
    """Only the empty stubs are skipped; a genuine table image is still written."""
    scatter = _fixture(tmp_path, "MT6582_Android_scatter.txt", stub_tables=False)
    plan = fs.build_mtk_write_plan(scatter, "Y2")
    names = [step["name"] for step in plan]
    assert "MBR" in names
    mbr = next(step for step in plan if step["name"] == "MBR")
    assert mbr["offset"] == 0


def test_y1_images_missing_from_disk_are_left_out(tmp_path):
    scatter = _fixture(tmp_path, "MT6572_Android_scatter.txt")
    (scatter.parent / "system.img").unlink()
    plan = fs.build_mtk_write_plan(scatter, "Y1")
    assert "android" not in [step["name"] for step in plan]


def test_plan_never_contains_a_preloader_duplicate_for_boot2(tmp_path):
    plan = fs.build_mtk_write_plan(_fixture(tmp_path, "MT6582_Android_scatter.txt"), "Y2")
    boot_parts = [step for step in plan if step["name"].upper() == "PRELOADER"]
    assert len(boot_parts) == 1
    assert boot_parts[0]["parttype"] == "boot1"


class _FakeHandler:
    def __init__(self):
        self.calls = []

    def da_wo(self, **kwargs):
        self.calls.append(("wo", kwargs))
        return True

    def da_write(self, **kwargs):
        self.calls.append(("w", kwargs))


def test_a_scatter_address_is_one_whole_image_da_wo_write(tmp_path):
    image = tmp_path / "lk.bin"
    image.write_bytes(b"x" * 4096)
    handler = _FakeHandler()
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None

    worker._write_mtk_partition(
        handler, object(), "UBOOT", str(image), "user",
        offset=0x1D20000, write_length=4096,
    )

    assert len(handler.calls) == 1, "one partition must be one transfer, never 50 MB slices"
    kind, kwargs = handler.calls[0]
    assert kind == "wo"
    assert kwargs["start"] == 0x1D20000
    assert kwargs["length"] == 4096
    assert kwargs["parttype"] == "user"


def test_no_address_means_a_named_write_resolved_by_the_device_table(tmp_path):
    image = tmp_path / "lk.bin"
    image.write_bytes(b"x" * 4096)
    handler = _FakeHandler()
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None

    worker._write_mtk_partition(
        handler, object(), "uboot", str(image), "user", offset=None
    )

    assert handler.calls == [
        ("w", {"parttype": "user", "filenames": [str(image)], "partitions": ["uboot"]})
    ]
