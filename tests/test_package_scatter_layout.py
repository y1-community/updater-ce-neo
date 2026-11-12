"""Packages whose layout differs from a stock release.

Y2Linux-style releases ship more than one scatter, and portable packages also
carry their own Download Agent (the tool's ``MTK_AllInOne_DA.bin`` stays the one
every install uses). The install flow must follow the package's files rather
than a fixed partition list, and must say what a 6104 rejection means.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import flash_service as fs
from src.i18n import tr


def _block(index, name, file_name, download, linear, physical, size, region="EMMC_USER"):
    return (
        f"- partition_index: SYS{index}\n"
        f"  partition_name: {name}\n"
        f"  file_name: {file_name}\n"
        f"  is_download: {'true' if download else 'false'}\n"
        "  type: NORMAL_ROM\n"
        f"  linear_start_addr: 0x{linear:X}\n"
        f"  physical_start_addr: 0x{physical:X}\n"
        f"  partition_size: 0x{size:X}\n"
        f"  region: {region}\n"
        "  storage: HW_STORAGE_EMMC\n"
        "  boundary_check: true\n"
        "  is_reserved: false\n"
        "  operation_type: UPDATE\n"
        "  reserve: 0x00\n\n"
    )


def _header():
    return (
        "- general: MTK_PLATFORM_CFG\n"
        "  info:\n"
        "    - config_version: V1.1.1\n"
        "      platform: MT6582\n"
        "      project: eastaeon82_wet_kk\n"
        "      storage: EMMC\n"
        "      block_size: 0x20000\n\n"
    )


def _scatter(*rows):
    return _header() + "".join(
        _block(i, name, file_name, download, linear, physical, size, region)
        for i, (name, file_name, download, linear, physical, size, region) in enumerate(rows)
    )


BOOTIMG = ("BOOTIMG", "BOOTIMG.img", True, 0x3180000, 0x1D80000, 0x1000000, "EMMC_USER")
RECOVERY = ("RECOVERY", "RECOVERY.img", True, 0x4180000, 0x2D80000, 0x1000000, "EMMC_USER")
ANDROID = ("ANDROID", "Y2ROOT.img", True, 0x6580000, 0x5180000, 0x33400000, "EMMC_USER")
USRDATA = ("USRDATA", "Y2DATA.img", True, 0x41780000, 0x40380000, 0x32000000, "EMMC_USER")
PRELOADER = ("PRELOADER", "NONE", False, 0x0, 0x0, 0x1400000, "EMMC_BOOT_1")
PRELOADER_DL = ("PRELOADER", "preloader.bin", True, 0x0, 0x0, 0x1400000, "EMMC_BOOT_1")
MBR = ("MBR", "NONE", False, 0x1400000, 0x0, 0x80000, "EMMC_USER")
EBR1 = ("EBR1", "NONE", False, 0x1480000, 0x80000, 0x80000, "EMMC_USER")


def _write_images(folder: Path, *names):
    for name in names:
        (folder / name).write_bytes(b"\x01" * 64)


def _overlay_package(tmp_path: Path):
    """A Y2Linux/Reborn-shaped package with the manifest's native EMMC_USER map."""
    scatter = tmp_path / "MT6582_Android_scatter.txt"
    scatter.write_text(
        _scatter(PRELOADER, MBR, EBR1, BOOTIMG, RECOVERY, ANDROID, USRDATA),
        encoding="utf-8",
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "org.schulzcode.y2linux.release/v1",
                "payloads": [
                    {"target_partition": "BOOTIMG", "absolute_start_bytes": 0x1D80000},
                    {"target_partition": "ANDROID", "absolute_start_bytes": 0x5180000},
                    {"target_partition": "USRDATA", "absolute_start_bytes": 0x40380000},
                ],
                "storage_addressing": {
                    "logical_sector_bytes": 512,
                    "logical_to_native_user_offset_sectors": 23552,
                    "payload_absolute_start_bytes_semantics": (
                        "stock logical disk / scatter physical_start_addr; not native EMMC_USER"
                    ),
                    "targets": {
                        "BOOTIMG": {"native_user_start_bytes": 0x2900000},
                        "ANDROID": {"native_user_start_bytes": 0x5D00000},
                        "USRDATA": {"native_user_start_bytes": 0x40F00000},
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    _write_images(
        tmp_path,
        "BOOTIMG.img",
        "RECOVERY.img",
        "Y2ROOT.img",
        "Y2DATA.img",
        "preloader.bin",
    )
    return scatter


def _package(tmp_path: Path, preserve_rows, preserve_name="MT6582_preserve_data_scatter.txt"):
    (tmp_path / "MT6582_Android_scatter.txt").write_text(
        _scatter(PRELOADER, BOOTIMG, ANDROID, USRDATA), encoding="utf-8"
    )
    (tmp_path / preserve_name).write_text(
        _scatter(PRELOADER, *preserve_rows), encoding="utf-8"
    )
    _write_images(tmp_path, "BOOTIMG.img", "Y2ROOT.img", "Y2DATA.img")
    return tmp_path / "MT6582_Android_scatter.txt"


def test_overlay_package_is_detected_by_its_manifest_and_missing_foundations(tmp_path):
    scatter = _overlay_package(tmp_path)

    assert fs.is_overlay_package(scatter) is True


def test_a_partial_stock_update_with_a_foundation_is_not_an_overlay(tmp_path):
    scatter = tmp_path / "MT6582_Android_scatter.txt"
    scatter.write_text(_scatter(PRELOADER_DL, BOOTIMG), encoding="utf-8")

    assert fs.is_overlay_package(scatter) is False


def test_overlay_package_probes_live_mbr_to_find_native_user_offset(tmp_path):
    """Overlay packages must probe the live MBR/EBR1 to resolve +0xB80000 on Y2."""
    scatter = _overlay_package(tmp_path)
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None
    worker._is_legacy_mode = lambda _mtk: True

    def make_table(boot, part_type):
        table = bytearray(512)
        table[446] = boot
        table[450] = part_type
        table[454:458] = (2048).to_bytes(4, "little")
        table[458:462] = (1000).to_bytes(4, "little")
        table[510:512] = b"\x55\xaa"
        return bytes(table)

    mbr_blob = make_table(0x80, 0x83)
    ebr_blob = make_table(0x00, 0x05)

    def read_blob(_mtk, addr, length):
        if addr == 0xB80000 and length == 512:
            return mbr_blob
        if addr == 0xC00000 and length == 512:
            return ebr_blob
        return b"\x00" * int(length)

    worker._read_user_blob = read_blob
    current = worker._configure_scatter_user_addressing(scatter)

    result = worker._auto_configure_scatter_user_addressing(
        SimpleNamespace(), scatter, current
    )

    assert (result.mode, result.bias) == ("physical", 0xB80000)


def test_overlay_manifest_payload_without_an_address_fails_closed(tmp_path):
    scatter = _overlay_package(tmp_path)
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "org.schulzcode.y2linux.release.1",
                "payloads": [{"target_partition": "BOOTIMG"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(fs.PackageLayoutError, match="absolute_start_bytes"):
        fs._y2linux_manifest_addresses(scatter)


def test_overlay_manifest_addresses_drive_the_write_plan(tmp_path):
    scatter = _overlay_package(tmp_path)

    overrides = fs._y2linux_manifest_addresses(scatter)
    plan = fs.build_mtk_write_plan(
        scatter,
        model="Y2",
        preloader_first=False,
        skip_data_partitions=False,
        address_overrides=overrides,
    )
    by_name = {step["name"].upper(): step for step in plan}

    assert by_name["BOOTIMG"]["offset"] == 0x2900000
    assert by_name["RECOVERY"]["offset"] == 0x3900000
    assert by_name["ANDROID"]["offset"] == 0x5D00000
    assert by_name["USRDATA"]["offset"] == 0x40F00000


def test_preserve_scatter_is_the_packages_own_exclusion_list(tmp_path):
    scatter = _package(tmp_path, (BOOTIMG, ANDROID))

    preserve = fs.find_preserve_data_scatter(scatter)

    assert preserve is not None and preserve.name == "MT6582_preserve_data_scatter.txt"
    assert fs.downloaded_partition_names(scatter) == ["BOOTIMG", "ANDROID", "USRDATA"]
    assert fs.downloaded_partition_names(preserve) == ["BOOTIMG", "ANDROID"]

    plan = fs.build_mtk_write_plan(
        scatter, skip_partition_names={"USRDATA"}
    )
    assert {step["name"] for step in plan} == {"BOOTIMG", "ANDROID"}


def test_preserve_scatter_that_adds_a_partition_is_ignored(tmp_path):
    extra = ("SEC_RO", "secro.img", True, 0x5180000, 0x3D80000, 0x1000000, "EMMC_USER")
    scatter = _package(tmp_path, (BOOTIMG, ANDROID, extra))

    assert fs.find_preserve_data_scatter(scatter) is None


def test_the_tools_own_download_agent_is_always_the_one_used(tmp_path):
    scatter = _package(tmp_path, (BOOTIMG, ANDROID))
    (tmp_path / "MTK_AllInOne_DA.bin").write_bytes(b"\x00" * 32)

    tool = tmp_path / "SP_Flash_Tool"
    tool.mkdir()
    tool_da = tool / "MTK_AllInOne_DA.bin"
    tool_da.write_bytes(b"\x01" * 32)

    worker = fs.FlashWorker("pkg.zip", method="sp")
    assert worker._resolve_da_file(tool, scatter) == tool_da
    assert not hasattr(fs, "_package_download_agent")


def test_6104_is_reported_as_a_secure_da_rejection():
    name = "S_SECURITY_SECURE_USB_DL_DA_RETURN_INVALID_TYPE"
    assert fs.SP_ERROR_CODE_NAMES["6104"] == name
    assert fs.SP_ERROR_GUIDANCE[name] == "err_sp_da_invalid_type"
    guidance = tr("err_sp_da_invalid_type")
    assert guidance and guidance != "err_sp_da_invalid_type"


SECRO = ("SEC_RO", "secro.img", True, 0x5180000, 0x3D80000, 0x600000, "EMMC_USER")


def test_an_image_shipped_under_another_container_suffix_is_still_detected(tmp_path):
    """A scatter that declares ``secro.img`` still ships as ``secro.bin``.

    Lithium (Timmkoo Q3/Q5) packs ``secro.bin`` beside a scatter whose SEC_RO
    entry names ``secro.img``. The name is what says which partition a file
    belongs to, so the container suffix must not make a present image read as
    missing and hold the whole package back.
    """
    scatter = tmp_path / "MT6582_Android_scatter.txt"
    scatter.write_text(_scatter(PRELOADER_DL, BOOTIMG, SECRO), encoding="utf-8")
    _write_images(tmp_path, "preloader.bin", "BOOTIMG.img", "secro.bin")

    assert fs._missing_images(scatter) == []
    plan = {step["name"]: step for step in fs.build_mtk_write_plan(scatter, "")}
    assert plan["SEC_RO"]["path"].name == "secro.bin"
    assert plan["SEC_RO"]["offset"] == 0x3D80000

    # SP Flash Tool reads the scatter itself, so the declared name is aliased
    # beside the shipped image for it.
    fs.align_scatter_disk_images(scatter)
    assert (tmp_path / "secro.img").is_file()


def test_an_image_in_a_subfolder_shipped_under_another_suffix_is_detected(tmp_path):
    # Its own folder: _resolve_image_file() also scans the parent, and the
    # tests in a session share that parent.
    package = tmp_path / "package"
    package.mkdir()
    scatter = package / "MT6582_Android_scatter.txt"
    scatter.write_text(_scatter(PRELOADER_DL, SECRO), encoding="utf-8")
    images = package / "images"
    images.mkdir()
    _write_images(images, "preloader.bin", "secro.bin")
    (package / "__MACOSX").mkdir()
    (package / "__MACOSX" / "secro.img").write_bytes(b"resource fork")

    assert fs._missing_images(scatter) == []
    assert fs._list_expected_images(scatter)[1][1] == (images / "secro.bin").resolve()


def test_a_package_file_named_after_a_partition_is_not_an_image(tmp_path):
    """Only a real image satisfies the scatter, not a readme or checksum list."""
    package = tmp_path / "package"
    package.mkdir()
    scatter = package / "MT6582_Android_scatter.txt"
    scatter.write_text(_scatter(PRELOADER_DL, SECRO), encoding="utf-8")
    _write_images(package, "preloader.bin")
    (package / "secro.txt").write_text("notes about the partition", encoding="utf-8")
    (package / "secro.sha256").write_text("deadbeef  secro.img\n", encoding="utf-8")

    assert fs._missing_images(scatter) == ["SEC_RO"]


def test_verified_boot_variants_take_precedence_over_unsigned_intermediates(tmp_path):
    """When a scatter declares unsigned filenames (e.g. boot.img, lk.img, logo.bin)
    and the package directory ships both the unsigned intermediate and the signed
    *-verified / *-sign image (as on MT6750 Mechen H1 Pro / X55), the signed image
    must be selected to match SP Flash Tool Verified Boot resolution."""
    scatter = tmp_path / "MT6750_Android_scatter.txt"
    scatter.write_text(
        "- general: MTK_PLATFORM_CFG\n"
        "  info:\n"
        "    - config_version: V1.1.2\n"
        "      platform: MT6750\n"
        "      project: k50v1_64_bsp\n"
        "      storage: EMMC\n"
        "      boot_channel: MSDC_0\n"
        "      block_size: 0x20000\n"
        "- partition_index: SYS1\n"
        "  partition_name: pgpt\n"
        "  file_name: NONE\n"
        "  is_download: false\n"
        "  type: NORMAL_ROM\n"
        "  linear_start_addr: 0x0\n"
        "  physical_start_addr: 0x0\n"
        "  partition_size: 0x8000\n"
        "  region: EMMC_USER\n"
        "- partition_index: SYS19\n"
        "  partition_name: lk\n"
        "  file_name: lk.img\n"
        "  is_download: true\n"
        "  type: NORMAL_ROM\n"
        "  linear_start_addr: 0xac00000\n"
        "  physical_start_addr: 0xac00000\n"
        "  partition_size: 0x100000\n"
        "  region: EMMC_USER\n"
        "- partition_index: SYS21\n"
        "  partition_name: boot\n"
        "  file_name: boot.img\n"
        "  is_download: true\n"
        "  type: NORMAL_ROM\n"
        "  linear_start_addr: 0xae00000\n"
        "  physical_start_addr: 0xae00000\n"
        "  partition_size: 0x1000000\n"
        "  region: EMMC_USER\n"
        "- partition_index: SYS22\n"
        "  partition_name: logo\n"
        "  file_name: logo.bin\n"
        "  is_download: true\n"
        "  type: NORMAL_ROM\n"
        "  linear_start_addr: 0xbe00000\n"
        "  physical_start_addr: 0xbe00000\n"
        "  partition_size: 0x800000\n"
        "  region: EMMC_USER\n",
        encoding="utf-8",
    )
    (tmp_path / "lk.img").write_bytes(b"unsigned-lk")
    (tmp_path / "lk-verified.img").write_bytes(b"verified-lk")
    (tmp_path / "boot.img").write_bytes(b"unsigned-boot")
    (tmp_path / "boot-verified.img").write_bytes(b"verified-boot")
    (tmp_path / "logo.bin").write_bytes(b"unsigned-logo")
    (tmp_path / "logo-verified.bin").write_bytes(b"verified-logo")

    plan = fs.build_mtk_write_plan(scatter, "")
    assert [(step["name"], step["path"].name, step["offset"]) for step in plan] == [
        ("lk", "lk-verified.img", 0xAC00000),
        ("boot", "boot-verified.img", 0xAE00000),
        ("logo", "logo-verified.bin", 0xBE00000),
    ]

