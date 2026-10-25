"""The MTKClient write plan must match the shipped 1.1.7 build.

These are the differences that damage players: writing the preloader first,
overwriting the device's MBR/EBR with the package's empty stubs, asking the
player for its partition table (mtkclient reads GPT only; these players are
MBR-partitioned, so the lookup returns nothing and the install dies), and
writing an image past the partition the scatter describes.
"""

import shutil
from pathlib import Path

from src import flash_service as fs

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "compat"


def _fixture(tmp_path, scatter_name, stub_tables=True):
    # A private folder: _resolve_image_file() also scans the parent, and pytest
    # tmp dirs share one parent across tests.
    tmp_path = tmp_path / "fw"
    tmp_path.mkdir(parents=True, exist_ok=True)
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


def test_y1_writes_the_preloader_first_then_every_downloadable_image(tmp_path):
    plan = fs.build_mtk_write_plan(_fixture(tmp_path, "MT6572_Android_scatter.txt"), "Y1")

    # Preloader first so boot1 is in place, then every image in scatter order.
    assert [step["name"] for step in plan] == [
        "PRELOADER",
        "UBOOT",
        "BOOTIMG",
        "RECOVERY",
        "SEC_RO",
        "LOGO",
        "ANDROID",
        "CACHE",
        "USRDATA",
    ]
    # The stock scatter reports physical_start_addr 0x0 for every EMMC_USER
    # entry, so the address is linear - 0x1400000 — the layout the package's
    # own MBR/EBR images describe (ANDROID at 0x3740000, USRDATA at
    # 0x4C140000). Every step is an address write; the device's table is
    # never consulted.
    by_name = {step["name"]: step for step in plan}
    assert {name: step["offset"] for name, step in by_name.items() if name != "PRELOADER"} == {
        "UBOOT": 0x1D20000,
        "BOOTIMG": 0x1D80000,
        "RECOVERY": 0x2380000,
        "SEC_RO": 0x2980000,
        "LOGO": 0x2A40000,
        "ANDROID": 0x3740000,
        "CACHE": 0x2C140000,
        "USRDATA": 0x4C140000,
    }
    assert by_name["PRELOADER"]["parttype"] == "boot1"
    assert by_name["PRELOADER"]["offset"] == 0
    # The scatter's own sizes travel with each step so an image that does not
    # fit its partition is refused before the DA is asked to write it.
    assert by_name["USRDATA"]["partition_size"] == 0x80000000
    assert by_name["ANDROID"]["partition_size"] == 0x28A00000
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


def test_preloader_first_can_be_switched_off(tmp_path):
    scatter = _fixture(tmp_path, "MT6582_Android_scatter.txt")
    first = fs.build_mtk_write_plan(scatter, "Y2")
    unsorted = fs.build_mtk_write_plan(scatter, "Y2", preloader_first=False)

    # Switching the reorder off changes only the order, never which images are written.
    assert first[0]["name"] == "PRELOADER"
    assert sorted(step["name"] for step in unsorted) == sorted(step["name"] for step in first)


def test_partition_table_images_are_never_written_back(tmp_path):
    """Neither the empty stubs nor a genuine table image is flashed.

    The device's live table is the layout the player boots from; a table from
    the package describes one vendor's layout and leaves a flashed player that
    cannot find a single partition.
    """
    for stub in (True, False):
        scatter = _fixture(tmp_path / ("stub" if stub else "real"),
                           "MT6582_Android_scatter.txt", stub_tables=stub)
        plan = fs.build_mtk_write_plan(scatter, "Y2")
        names = [step["name"].upper() for step in plan]
        assert not any(fs._is_device_table_partition(n) for n in names)


def test_tail_relative_addresses_are_not_flashed_at_a_guessed_offset(tmp_path):
    """A scatter address at/above 0xFFFF0000 is relative to the end of the
    user area (BMTPOOL: 0xFFFF00a8). It only resolves against the live
    device's size, so it must never become a linear-minus-base offset."""
    scatter = _fixture(tmp_path, "MT6582_Android_scatter.txt")
    text = scatter.read_text()
    text += (
        "\n- partition_index: SYS99\n"
        "  partition_name: TAILPART\n"
        "  file_name: tailpart.img\n"
        "  is_download: true\n"
        "  type: NORMAL_ROM\n"
        "  linear_start_addr: 0xFFFF00a8\n"
        "  physical_start_addr: 0x0\n"
        "  partition_size: 0x100000\n"
        "  region: EMMC_USER\n"
    )
    scatter.write_text(text)
    (scatter.parent / "tailpart.img").write_bytes(b"payload" * 512)

    plan = fs.build_mtk_write_plan(scatter, "Y2")

    assert "TAILPART" not in [step["name"] for step in plan]
    # Nothing else in the plan moved.
    assert "UBOOT" in [step["name"] for step in plan]


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


class _FakeDaLoader:
    def __init__(self, image_result=True):
        self.calls = []
        self.image_result = image_result

    def sdmmc_write_image(self, **kwargs):
        self.calls.append(("write_image", kwargs))
        return self.image_result

    def writeflash(self, **kwargs):
        self.calls.append(("writeflash", kwargs))
        return True


class _FakeConfig:
    def __init__(self, cpu):
        self.cpu = cpu
        self.last_error = None


class _FakeMtk:
    def __init__(self, daloader, cpu="MT6572"):
        self.daloader = daloader
        self.config = _FakeConfig(cpu)


def test_preloader_is_wrapped_and_written_through_write_image(tmp_path):
    """A raw MMM preloader goes to boot1 with a BRLYT header, via WRITE_IMAGE."""
    preloader = tmp_path / "preloader.bin"
    preloader.write_bytes(b"MMM\x01" + b"\xAA" * 4096)
    da = _FakeDaLoader()
    mtk = _FakeMtk(da)
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None

    worker._write_mtk_preloader(None, mtk, str(preloader))

    assert len(da.calls) == 1
    kind, kwargs = da.calls[0]
    assert kind == "write_image"
    assert kwargs["addr"] == 0
    assert kwargs["parttype"] == 1  # MTK_DA_EMMC_PART_BOOT1
    written = Path(kwargs["filename"])
    assert not written.exists(), "the wrapped temp image must be cleaned up"
    assert kwargs["length"] > preloader.stat().st_size


def test_preloader_already_carrying_a_boot_header_is_written_asis(tmp_path):
    preloader = tmp_path / "preloader.bin"
    payload = b"EMMC_BOOT\x00\x00\x00\x01\x00\x00\x00" + b"\xBB" * 4096
    preloader.write_bytes(payload)
    da = _FakeDaLoader()
    mtk = _FakeMtk(da)
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None

    worker._write_mtk_preloader(None, mtk, str(preloader))

    assert da.calls[0][1]["length"] == len(payload)


def test_sparse_regions_are_written_at_their_own_offsets(tmp_path):
    raw = tmp_path / "userdata.raw.img"
    raw.write_bytes(b"\x11" * 4096)
    daloader = _FakeDaLoader()
    mtk = _FakeMtk(daloader)
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None
    seen = []

    worker._write_mtk_sparse_regions(
        mtk,
        "USRDATA",
        str(raw),
        [(0, 2048), (4096, 2048)],
        0x4C140000,
        "user",
        bytes_progress_cb=seen.append,
    )

    assert [call[0] for call in daloader.calls] == ["writeflash", "writeflash"]
    assert [call[1]["addr"] for call in daloader.calls] == [
        0x4C140000,
        0x4C140000 + 4096,
    ]
    assert [call[1]["offset"] for call in daloader.calls] == [0, 4096]
    assert seen == [2048, 2048]


def _table_sector(entries, signature=True):
    """One MBR/EBR sector from ``[(type, lba, sectors), ...]``."""
    import struct

    sector = bytearray(512)
    for index, (ptype, lba, count) in enumerate(entries[:4]):
        offset = 446 + 16 * index
        sector[offset + 4] = ptype
        sector[offset + 8 : offset + 16] = struct.pack("<II", lba, count)
    if signature:
        sector[510:512] = b"\x55\xaa"
    return bytes(sector)


def test_a_live_mbr_chain_is_read_back_with_its_real_partition_sizes():
    """The device's own table bounds every write, as in the shipped build.

    The Y1 table keeps its primary partitions in the MBR and every logical one
    in EBR1 (relative to the extended partition's base). Reading it back is how
    a package built for a 2 GB userdata is kept out of a player whose userdata
    is smaller.
    """
    head = bytearray(2 * 1024 * 1024)
    head[0:512] = _table_sector(
        [
            (0x05, 0x400, 0xFFFFFFFF),   # extended container at 0x80000
            (0x83, 0x4800, 0x5000),      # 0x900000, 10 MB
            (0x83, 0x1BA00, 0x145000),   # 0x3740000, 650 MB
            (0x00, 0, 0),
        ]
    )
    head[0x80000 : 0x80000 + 512] = _table_sector(
        [
            (0x83, 0x160600, 0x100000),  # 0x2C140000, 512 MB
            (0x83, 0x260600, 0x411000),  # 0x4C140000, smaller userdata
            (0x0C, 0x660600, 0xFFFFFFF0),  # "rest of device" placeholder
            (0x00, 0, 0),
        ]
    )

    parts = fs._parse_mbr_partitions(bytes(head))

    assert parts == [
        (0x900000, 0xA00000),
        (0x3740000, 0x145000 * 512),
        (0x2C140000, 0x100000 * 512),
        (0x4C140000, 0x411000 * 512),
    ]


def test_a_sector_without_the_mbr_signature_yields_no_layout():
    head = _table_sector([(0x83, 0x4800, 0x5000)], signature=False)
    assert fs._parse_mbr_partitions(head) == []


def test_sparse_data_end_is_the_last_data_extent():
    assert fs._sparse_data_end([(0, 2048), (4096, 2048)]) == 6144
    assert fs._sparse_data_end([]) == 0


def test_sparse_regions_are_clipped_to_the_devices_partition():
    """A package userdata bigger than the player's stops at the edge."""
    kept, dropped = fs._clip_sparse_regions([(0, 2048), (4096, 8192)], 8192)
    assert dropped is True
    assert kept == [(0, 2048), (4096, 4096)]

    kept, dropped = fs._clip_sparse_regions([(0, 2048)], 8192)
    assert (kept, dropped) == ([(0, 2048)], False)

    # Nothing inside the partition is a package/layout mismatch, not a write.
    assert fs._clip_sparse_regions([(8192, 2048)], 4096) == ([], True)


def test_device_and_package_platform_must_agree():
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None
    mtk = _FakeMtk(_FakeDaLoader(), cpu="MT6582")

    assert worker._check_package_platform(mtk, "MT6582")
    assert worker._check_package_platform(mtk, "mt6582")
    assert not worker._check_package_platform(mtk, "MT6572")
    assert not worker._check_package_platform(mtk, "MT6572/MT6582 Modem")
    # Unknown on either side never blocks the run.
    assert worker._check_package_platform(_FakeMtk(_FakeDaLoader(), cpu=""), "MT6572")
    assert worker._check_package_platform(mtk, "")


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
