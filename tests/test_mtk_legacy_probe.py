"""Device-evidence USER-address resolution for the MTK legacy path.

The CMR build writes the Y2 USER area at ``physical_start_addr + 0xB80000``
(MBR 0xB80000, EBR1 0xC00000, UBOOT 0x28A0000). A fixed 0x800000 bias writes
the same images 0x380000 too low, which leaves the real partition table
untouched. These tests pin the probe, the equivalent-candidate collapse and
the fail-closed rule.
"""

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import flash_service as fs
from src import mtk_legacy_layout as layout

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "compat"


def _package(tmp_path, scatter_name="MT6582_Android_scatter.txt"):
    root = tmp_path / "fw"
    root.mkdir(parents=True, exist_ok=True)
    scatter = root / "Android_scatter.txt"
    shutil.copy(ASSETS / scatter_name, scatter)
    for line in scatter.read_text().splitlines():
        line = line.strip()
        if not line.startswith("file_name:"):
            continue
        name = line.split(":", 1)[1].strip()
        if not name or name.upper() == "NONE":
            continue
        if name in {"MBR", "EBR1", "EBR2"}:
            table = bytearray(512)
            table[446] = 0x80 if name == "MBR" else 0
            table[450] = 0x83 if name == "MBR" else 0x05
            table[454:458] = (2048).to_bytes(4, "little")
            table[458:462] = (1000).to_bytes(4, "little")
            table[510:512] = b"\x55\xaa"
            (root / name).write_bytes(bytes(table))
        else:
            (root / name).write_bytes(b"payload" * 512)
    return scatter


def _tables(root):
    return (root / "MBR").read_bytes(), (root / "EBR1").read_bytes()


class _FakeEmmc:
    def __init__(self, user_size):
        self.m_emmc_ua_size = user_size


class _FakeLegacyStorage:
    def __init__(self, user_size):
        self.emmc = _FakeEmmc(user_size)


class _FakeDaConfig:
    def __init__(self, user_size):
        self.legacy_storage = _FakeLegacyStorage(user_size)
        self.storage = SimpleNamespace(flashtype="emmc")


class _FakeDaLoader:
    flashmode = 3

    def __init__(self, data, user_size):
        self.data = bytearray(data)
        self.daconfig = _FakeDaConfig(user_size)
        self.config = SimpleNamespace(last_error=None)

    def readflash(self, addr, length, filename="", parttype=None, display=True):
        addr = int(addr)
        chunk = bytes(self.data[addr : addr + int(length)])
        return chunk if len(chunk) == int(length) else None

    def formatflash(self, **kwargs):
        return True

    def writeflash(self, **kwargs):
        return True


def _fake_mtk(data, user_size):
    return SimpleNamespace(
        daloader=_FakeDaLoader(data, user_size),
        config=SimpleNamespace(last_error=None),
        port=None,
    )


def _worker():
    worker = fs.FlashWorker("pkg.zip", method="mtk")
    worker._log = lambda *a, **k: None
    return worker


def _resolve(worker, mtk, scatter):
    current = worker._configure_scatter_user_addressing(scatter)
    return worker._auto_configure_scatter_user_addressing(mtk, scatter, current)


def _plan(scatter, candidate):
    return fs.build_mtk_write_plan(
        scatter,
        "Y2",
        preloader_first=False,
        user_addressing=candidate,
        user_size=0x74000000,
        block_size=fs._parse_scatter_block_size(scatter),
    )


def test_y2_probe_chooses_the_cmr_b80000_layout(tmp_path):
    scatter = _package(tmp_path)
    mbr, ebr = _tables(scatter.parent)
    user_size = 0x74000000
    data = bytearray(user_size)
    data[0xB80000 : 0xB80000 + 512] = mbr
    data[0xC00000 : 0xC00000 + 512] = ebr

    candidate = _resolve(_worker(), _fake_mtk(data, user_size), scatter)

    assert (candidate.mode, candidate.bias) == ("physical", 0xB80000)
    by_name = {step["name"].upper(): step for step in _plan(scatter, candidate)}
    assert by_name["MBR"]["offset"] == 0xB80000
    assert by_name["EBR1"]["offset"] == 0xC00000
    assert by_name["UBOOT"]["offset"] == 0x28A0000
    assert by_name["BOOTIMG"]["offset"] == 0x2900000
    assert by_name["ANDROID"]["offset"] == 0x5D00000


def test_probe_honours_a_device_with_tables_at_800000(tmp_path):
    scatter = _package(tmp_path)
    mbr, ebr = _tables(scatter.parent)
    user_size = 0x74000000
    data = bytearray(user_size)
    data[0x800000 : 0x800000 + 512] = mbr
    data[0x880000 : 0x880000 + 512] = ebr

    candidate = _resolve(_worker(), _fake_mtk(data, user_size), scatter)

    assert (candidate.mode, candidate.bias) == ("physical", 0x800000)
    by_name = {step["name"].upper(): step for step in _plan(scatter, candidate)}
    assert by_name["UBOOT"]["offset"] == 0x2520000


def test_probe_aborts_before_writing_when_the_layout_is_unproven(tmp_path):
    scatter = _package(tmp_path)
    user_size = 0x74000000

    with pytest.raises(fs.PackageLayoutError, match="auto-probe"):
        _resolve(_worker(), _fake_mtk(bytearray(user_size), user_size), scatter)


def test_probe_runs_for_a_scatter_without_a_known_chip_name(tmp_path):
    # The probe is evidence-driven, not gated on _LEGACY_MBR_BIAS_PLATFORMS:
    # a future slidia_manifest model must take the same path.
    scatter = _package(tmp_path)
    mbr, ebr = _tables(scatter.parent)
    user_size = 0x74000000
    data = bytearray(user_size)
    data[0xB80000 : 0xB80000 + 512] = mbr
    data[0xC00000 : 0xC00000 + 512] = ebr

    candidate = _resolve(_worker(), _fake_mtk(data, user_size), scatter)

    assert candidate.bias == 0xB80000


def test_equivalent_candidates_are_collapsed(tmp_path):
    scatter = _package(tmp_path)
    mbr, ebr = _tables(scatter.parent)
    user_size = 0x74000000
    data = bytearray(user_size)
    data[0xB80000 : 0xB80000 + 512] = mbr
    data[0xC00000 : 0xC00000 + 512] = ebr

    candidate = _resolve(_worker(), _fake_mtk(data, user_size), scatter)

    # physical+0xB80000 and linear-0x880000 resolve to the same layout; the
    # probe must not treat that equivalence as an ambiguity.
    assert candidate.bias == 0xB80000
    assert candidate.mode == "physical"


def test_relative_layout_must_match_for_a_dual_table_win():
    mbr_part = {
        "partition_name": "MBR",
        "region": "EMMC_USER",
        "linear_start_addr": "0x1400000",
        "physical_start_addr": "0x0",
    }
    ebr_part = {
        "partition_name": "EBR1",
        "region": "EMMC_USER",
        "linear_start_addr": "0x1480000",
        "physical_start_addr": "0x80000",
    }
    assert layout.relative_layout_matches(mbr_part, ebr_part, 0xB80000, 0xC00000)
    assert not layout.relative_layout_matches(mbr_part, ebr_part, 0xB80000, 0xD00000)
