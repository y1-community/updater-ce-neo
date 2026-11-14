#!/usr/bin/env python3
"""Scatter-free, read-only archive readback for Innioasis / Timmkoo MTK players.

The existing ``y2_mtk_test.py readback`` needs the ROM's own scatter for addresses. When the
firmware you want to archive is the one *on the device* and you have no scatter for it, this
takes a raw snapshot instead and derives the layout from the device's own MBR/EBR chain:

    python debug/y2_archive_readback.py dump  <out_dir>          # read device, then slice it
    python debug/y2_archive_readback.py slice <out_dir>          # offline: re-slice an existing dump
    python debug/y2_archive_readback.py dump  <out_dir> --print  # show the mtk.py command only

Everything it sends to the device is a read: ``ro`` / ``rf`` only. There is no ``wo``, ``wf``,
``e`` or ``format`` anywhere in this file, so a run cannot write, erase or reflash anything.

Two artefacts come out of a run:
  * ``user_area.raw``      bit-exact EMMC_USER image, plus ``boot1_head.bin`` from EMMC_BOOT_1
  * ``<out>/archive/``     per-partition images, a generated ``MT6582_Android_scatter.txt``,
                           ``fw_package.ini``, ``SHA256SUMS`` and ``manifest.json``

Address note (this is what makes or breaks a Y2 read): the scatter's ``physical_start_addr``
column is *not* a device address. The engine writes at ``physical_start_addr + 0x800000``, so
a scatter's physical 0 is at device 0x800000. Reading at the scatter's linear addresses is how
an earlier capture came back with an all-zero MBR. ``slice`` therefore finds the MBR by trying
every known base and keeps the chain that actually parses.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MTK = REPO / "vendor" / "mtkclient" / "mtk.py"
# pyusb's default search does not find the fork's bundled libusb-1.0.dylib on macOS, so
# mtk.py dies with "NoBackendError" when launched directly. This runner is the app's own
# bootstrap: it installs the bundled backend, then runs the same vendor/mtk.py entry point.
RUNNER = REPO / "src" / "console" / "mtk_runner.py"
DEFAULT_DA = REPO / "vendor" / "mtkclient" / "mtkclient" / "Loader" / "MTK_AllInOne_DA_mt6590.bin"
DEFAULT_PRELOADER = REPO / "firmware" / "rom_y2" / "preloader_eastaeon82_wet_kk.bin"

# Device addresses a Y2 MBR has been seen at / can be at, given the scatter->device bias.
MBR_BASE_CANDIDATES = (0x0, 0x800000, 0xC00000, 0x1400000)
# Proven on a stock 3.2.4 Y2 against a 3.2.1 package's table: device = physical + 0xB80000.
DEFAULT_USER_BIAS = 0xB80000
# The engine's own Y2 policy: write address = physical_start_addr + this.
SCATTER_PHYSICAL_BIAS = 0x800000
# Y2 convention: linear_start_addr = physical_start_addr + this; preloader sits at boot1 0x0.
SCATTER_LINEAR_BIAS = 0x1400000
PRELOADER_DATA_OFFSET = 0x800
BOOT_REGION_SIZE = 0x1400000
TABLE_SLOT = 0x80000
SECTOR = 512

# Canonical Y2 layout, from firmware/rom_y2/MT6582_Android_scatter.txt. Used only to put
# names and scatter metadata on extents measured on the device; sizes are cross-checked.
REFERENCE = [
    ("MBR", 0x0, 0x80000, "NORMAL_ROM", True, "UPDATE"),
    ("EBR1", 0x80000, 0x80000, "NORMAL_ROM", True, "UPDATE"),
    ("PRO_INFO", 0x100000, 0x300000, "NONE", False, "PROTECTED"),
    ("NVRAM", 0x400000, 0x500000, "NONE", False, "BINREGION"),
    ("PROTECT_F", 0x900000, 0xA00000, "NONE", False, "PROTECTED"),
    ("PROTECT_S", 0x1300000, 0xA00000, "NONE", False, "PROTECTED"),
    ("SECCFG", 0x1D00000, 0x20000, "NONE", False, "INVISIBLE"),
    ("UBOOT", 0x1D20000, 0x60000, "NORMAL_ROM", True, "UPDATE"),
    ("BOOTIMG", 0x1D80000, 0x1000000, "NORMAL_ROM", True, "UPDATE"),
    ("RECOVERY", 0x2D80000, 0x1000000, "NORMAL_ROM", True, "UPDATE"),
    ("SEC_RO", 0x3D80000, 0x600000, "YAFFS_IMG", True, "UPDATE"),
    ("MISC", 0x4380000, 0x80000, "NONE", False, "INVISIBLE"),
    ("LOGO", 0x4400000, 0x300000, "NORMAL_ROM", True, "UPDATE"),
    ("EBR2", 0x4700000, 0x80000, "NORMAL_ROM", True, "UPDATE"),
    ("EXPDB", 0x4780000, 0xA00000, "NONE", False, "INVISIBLE"),
    ("ANDROID", 0x5180000, 0x33400000, "YAFFS_IMG", True, "UPDATE"),
    ("CACHE", 0x38580000, 0x7E00000, "YAFFS_IMG", True, "UPDATE"),
    ("USRDATA", 0x40380000, 0x32000000, "YAFFS_IMG", True, "UPDATE"),
    ("FAT", 0x72380000, 0x0, "NONE", False, "INVISIBLE"),
]
REFERENCE_BY_OFFSET = {phys: (name, size, ftype, dl, op) for name, phys, size, ftype, dl, op in REFERENCE}
# Partitions that hold user content rather than firmware; not part of an archive by default.
USER_CONTENT = {"USRDATA", "CACHE", "FAT"}


# ---------------------------------------------------------------- device side
def python_exe() -> str:
    for cand in (REPO / ".venv-build" / "bin" / "python", REPO / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def da_loader(path: Path) -> Path:
    """mtkclient skips the 0x65xx DA entries unless the filename contains 'mt6590'."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"download agent not found: {path}")
    if "mt6590" in path.name.lower():
        return path
    alias = Path(tempfile.gettempdir()) / "y2_archive_da" / "MTK_AllInOne_DA_mt6590.bin"
    alias.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, alias)
    print(f"[readback] {path.name} -> {alias} (filename must contain 'mt6590')")
    return alias


def read_commands(out: Path, length: int | None) -> list[str]:
    cmds = [
        f"ro 0x0 0x100000 {out / 'boot1_head.bin'} --parttype=boot1",
        f"ro 0x0 0x100000 {out / 'user_head.bin'} --parttype=user",
    ]
    if length:
        cmds.append(f"ro 0x0 {hex(length)} {out / 'user_area.raw'} --parttype=user")
    else:
        cmds.append(f"rf {out / 'user_area.raw'} --parttype=user")
    return cmds


def run_mtk(cmds: list[str], loader: Path, preloader: Path, dry: bool) -> int:
    argv = [python_exe(), str(RUNNER), f"--loader={loader}", f"--preloader={preloader}",
            "multi", ";".join(cmds)]
    if dry:
        print(" ".join(f"'{a}'" if " " in a else a for a in argv))
        return 0
    env = dict(os.environ, PYTHONPATH=str(REPO / "vendor" / "mtkclient"))
    print("[readback] read-only session: ro/rf only, nothing is written or erased.", flush=True)
    print("[readback] waiting for the device: power it OFF, then hold Vol Up + Vol Down and plug in USB.",
          flush=True)
    return subprocess.call(argv, env=env, cwd=str(REPO))


# ---------------------------------------------------------------- offline slicing
def plausible_entries(data: bytes) -> int:
    count = 0
    for index in range(4):
        entry = data[446 + index * 16:462 + index * 16]
        if len(entry) < 16:
            continue
        ptype = entry[4]
        lba = int.from_bytes(entry[8:12], "little")
        size = int.from_bytes(entry[12:16], "little")
        if entry[0] not in (0, 0x80) or not ptype or not lba or not size:
            continue
        count += 1
    return count


def is_table(data: bytes) -> bool:
    return len(data) >= SECTOR and data[510:512] == b"\x55\xaa" and plausible_entries(data[:SECTOR]) > 0


def walk_chain(dump: bytes, base: int, max_tables: int = 8) -> list[tuple[int, int, int]]:
    """(table_offset, partition_offset, size) for every extent an MBR/EBR chain describes.

    Offsets are absolute in the dump. Entries in an EBR are relative to the extended
    partition's base, which is the first extended entry's own offset.
    """
    if base + SECTOR > len(dump) or not is_table(dump[base:base + SECTOR]):
        return []
    found: list[tuple[int, int, int]] = []
    extended_base = None
    queue = [base]
    seen = set()
    while queue and len(seen) < max_tables:
        table = queue.pop(0)
        if table in seen or table + SECTOR > len(dump):
            continue
        seen.add(table)
        head = dump[table:table + SECTOR]
        if not is_table(head):
            continue
        for index in range(4):
            entry = head[446 + index * 16:462 + index * 16]
            ptype = entry[4]
            lba = int.from_bytes(entry[8:12], "little")
            size = int.from_bytes(entry[12:16], "little")
            if not ptype or not lba:
                continue
            if ptype in (0x05, 0x0F, 0x85):
                # An extended entry's own size is a placeholder (the players carry
                # 0xFFFFFFFF), so it must be followed regardless of that field --
                # filtering on it dropped every logical partition.
                target = table + lba * SECTOR
                if extended_base is None:
                    extended_base = target
                if target not in seen:
                    queue.append(target)
                continue
            if not size or size >= 0xFFFFFFF0:
                continue
            start = (table + lba * SECTOR) if table == base else (extended_base or base) + lba * SECTOR
            found.append((table, start, size * SECTOR))
    # Drop the extents that are just the table sectors themselves; they get their own slots.
    table_offsets = {t for t in seen}
    return [item for item in found if item[1] not in table_offsets]


def detect_base(dump: bytes) -> tuple[int, list[tuple[int, int, int]]]:
    """Pick the sector whose chain describes the most partitions.

    The Y2's table is not at any address the scatter implies: this player puts the MBR at
    0xB80000 (linear_start_addr - 0x880000). So the candidates are the known biases *and*
    every 55AA sector in the first 32 MiB -- an EBR sector also starts with 55AA, which is
    why the base must be the one with the longest chain rather than the first hit.
    """
    candidates = list(MBR_BASE_CANDIDATES)
    scan_end = min(len(dump), 0x2000000)
    for offset in range(0, scan_end - SECTOR, SECTOR):
        if dump[offset + 510:offset + 512] == b"\x55\xaa" and offset not in candidates:
            candidates.append(offset)
    best = (0, [])
    for base in candidates:
        extents = walk_chain(dump, base)
        if len(extents) > len(best[1]):
            best = (base, extents)
    return best


def complete_from_reference(entries: list[dict], base: int, dump: bytes) -> list[dict]:
    """Add the partitions an MBR/EBR chain does not describe.

    An MTK scatter covers more partitions than the MBR does: on this player the chain names
    PROTECT_F/PROTECT_S/SEC_RO, EBR1's ANDROID/CACHE/USRDATA and EBR2's FAT, while UBOOT,
    BOOTIMG, RECOVERY and LOGO are only ever written by address from the scatter. Those four
    are present in the snapshot at the reference offsets (verified by their own magic), so an
    archive that stopped at the chain would be missing every boot image.

    Guards: never overlaps an extent the device described, never runs past the dump, and an
    entirely blank region is skipped rather than shipped as an empty partition.
    """
    covered = [(e["offset"], e["size"]) for e in entries]
    names = {e["name"] for e in entries}
    added = []
    for name, rel, size, ftype, download, operation in REFERENCE:
        if name in names or not size:
            continue
        offset = base + rel
        if offset + size > len(dump):
            print(f"[slice] {name}: reference offset 0x{offset:X} is past the end of the dump, skipped")
            continue
        if any(start < offset + size and offset < start + length for start, length in covered):
            continue
        if not any(dump[offset:offset + 4096]):
            print(f"[slice] {name}: blank on this device, skipped")
            continue
        added.append({"name": name, "offset": offset, "size": size, "type": ftype,
                      "download": download, "operation": operation, "source": "reference-layout"})
        covered.append((offset, size))
    return added


def load_scatter(path: Path) -> list[dict]:
    """Partition blocks of an MTK scatter, in file order."""
    blocks: list[dict] = []
    cur: dict = {}
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"\s*-?\s*(\w+):\s*(.+?)\s*$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if key == "partition_index" and cur.get("partition_name"):
            blocks.append(cur)
            cur = {}
        cur[key] = value
    if cur.get("partition_name"):
        blocks.append(cur)

    table = []
    for block in blocks:
        name = block.get("partition_name", "").strip()
        if not name:
            continue
        table.append({
            "name": name,
            "region": block.get("region", "EMMC_USER").strip().upper(),
            "physical": int(block.get("physical_start_addr", "0"), 16),
            "size": int(block.get("partition_size", "0"), 16),
            "type": block.get("type", "NORMAL_ROM").strip(),
            "download": block.get("is_download", "false").strip().lower() == "true",
            "operation": block.get("operation_type", "UPDATE").strip(),
            "file": block.get("file_name", "NONE").strip(),
        })
    return table


def detect_scatter_bias(table: list[dict], dump: bytes, scatter_dir: Path) -> tuple[int, str]:
    """Device offset minus the scatter's ``physical_start_addr`` for this player.

    The package can answer this itself: its MBR/EBR images are the table sectors, so a bias
    is right when those bytes appear at ``physical + bias`` in the snapshot. Verified on a
    stock 3.2.4 Y2 with a 3.2.1 package's table: all three sectors match at +0xB80000.
    """
    checks = []
    for name in ("MBR", "EBR1", "EBR2"):
        part = next((p for p in table if p["name"] == name and p["region"] == "EMMC_USER"), None)
        source = scatter_dir / name
        if part is not None and source.is_file():
            checks.append((part["physical"], source.read_bytes()[:SECTOR]))
    mbr_part = next((p for p in table if p["name"] == "MBR" and p["region"] == "EMMC_USER"), None)
    candidates = [0xB80000, 0x800000, 0x880000, 0xC00000, 0x400000, 0x0]
    if mbr_part is not None:
        for bias in candidates:
            offset = mbr_part["physical"] + bias
            if offset + SECTOR <= len(dump) and is_table(dump[offset:offset + SECTOR]):
                candidates.remove(bias)
                candidates.insert(0, bias)
                break
    best = (0, "")
    for bias in candidates:
        hits = 0
        for physical, want in checks:
            offset = physical + bias
            if offset + len(want) <= len(dump) and dump[offset:offset + len(want)] == want:
                hits += 1
        if hits > best[0]:
            best = (hits, bias)
        elif hits == best[0] and hits and bias == 0xB80000:
            best = (hits, bias)
    if best[0]:
        return best[1], f"{best[0]}/{len(checks)} scatter table images matched"
    if mbr_part is not None:
        offset = mbr_part["physical"] + 0xB80000
        if offset + SECTOR <= len(dump) and is_table(dump[offset:offset + SECTOR]):
            return 0xB80000, "MBR sector 55AA at physical+0xB80000"
    return 0, "no bias proven"


def slice_from_scatter(out: Path, scatter_path: Path, version: str) -> int:
    """Slice a snapshot into a flashable package using a package scatter's own layout."""
    raw = out / "user_area.raw"
    if not raw.is_file():
        sys.exit(f"no dump at {raw} - run the 'dump' action first (device needed)")
    dump = raw.read_bytes()
    table = load_scatter(scatter_path)
    if not table:
        sys.exit(f"no partitions parsed from {scatter_path}")
    bias, why = detect_scatter_bias(table, dump, scatter_path.parent)
    print(f"[scatter] {scatter_path.name}: {len(table)} entries, bias 0x{bias:X} ({why})")
    if not bias and any(p["region"] == "EMMC_USER" and p["size"] for p in table):
        print("[scatter] WARNING: no bias proven - addresses are the scatter's physical column verbatim")

    package = out / "package"
    package.mkdir(parents=True, exist_ok=True)
    images = []
    for part in table:
        if part["region"] != "EMMC_USER" or part["size"] <= 0:
            continue
        if part["name"].upper() in USER_CONTENT:
            continue
        offset = part["physical"] + bias
        if offset + part["size"] > len(dump):
            print(f"[scatter] {part['name']}: past the end of the snapshot, skipped")
            continue
        data = dump[offset:offset + part["size"]]
        if not any(data[:4096]) and part["name"].upper() not in ("MBR", "EBR1", "EBR2"):
            print(f"[scatter] {part['name']}: blank on this device, skipped")
            continue
        path = package / f"{part['name'].lower()}.img"
        path.write_bytes(data)
        images.append({"name": part["name"], "offset": offset, "size": len(data),
                       "file": path.name, "sha256": sha256(path)})
        print(f"[scatter] {part['name']:<10} 0x{offset:08X} {len(data) / 1048576:7.1f} MiB -> {path.name}")

    preloader, preloader_size = extract_preloader(out / "boot1_head.bin", package)
    if preloader is None:
        sys.exit("[scatter] no boot1_head.bin in the dump; the package cannot be complete without a preloader")
    (package / "preloader_eastaeon82_wet_kk.bin").write_bytes(preloader.read_bytes())
    preloader.unlink()
    print(f"[scatter] PRELOADER  {preloader_size} bytes -> preloader_eastaeon82_wet_kk.bin")
    images.insert(0, {"name": "PRELOADER", "offset": 0, "size": preloader_size,
                      "file": "preloader_eastaeon82_wet_kk.bin",
                      "sha256": sha256(package / "preloader_eastaeon82_wet_kk.bin")})
    return write_package_meta(package, table, bias, why, images, preloader_size, version, scatter_path)


def write_package_meta(package: Path, table: list[dict], bias: int, evidence: str,
                       images: list[dict], preloader_size: int, version: str,
                       scatter_path: Path, verbatim: bool = False) -> int:
    """Scatter, fw_package.ini, SHA256SUMS and manifest for a finished package.

    ``verbatim`` copies the source package's scatter unchanged instead of regenerating it with
    a baked-in bias. A baked column only works if the engine takes its default address policy;
    the untouched vendor scatter is what the device's own calibration was written against, and
    a package that flashes differently from the vendor's own is not a safe package.
    """
    if verbatim:
        scatter_out = package / scatter_path.name
        scatter_out.write_bytes(scatter_path.read_bytes())
        _write_package_metadata(package, bias, evidence, images, preloader_size, version,
                                scatter_path, scatter_out)
        return 0
    # Regenerated scatter: the same partitions, but the physical column keeps the engine's
    # +0x800000 default landing on the addresses measured here, and the table sectors plus
    # the player's own storage are never written.
    blocks, index = [], 0
    blocks.append(scatter_block("PRELOADER", index, region="EMMC_BOOT_1", linear=0, physical=0,
                                size=BOOT_REGION_SIZE, ftype="SV5_BL_BIN", download=True,
                                operation="BOOTLOADERS", file_name="preloader_eastaeon82_wet_kk.bin"))
    index += 1
    physical_bias = SCATTER_PHYSICAL_BIAS if bias >= SCATTER_PHYSICAL_BIAS else 0
    for part in table:
        if part["region"] != "EMMC_USER":
            continue
        name = part["name"]
        download = part["download"] and name.upper() not in USER_CONTENT | {"MBR", "EBR1", "EBR2", "BMTPOOL"}
        if part["size"] == 0 or part["physical"] >= 0xFFFF0000:
            continue
        physical = part["physical"] + bias - physical_bias
        blocks.append(scatter_block(name, index, region="EMMC_USER",
                                    linear=physical + SCATTER_LINEAR_BIAS, physical=physical,
                                    size=part["size"], ftype=part["type"], download=download,
                                    operation=part["operation"],
                                    file_name=f"{name.lower()}.img" if download else "NONE"))
        index += 1
    scatter_out = package / "MT6582_Android_scatter.txt"
    scatter_out.write_text(
        "############################################################################################################\n"
        "# Generated by debug/y2_archive_readback.py from a read-only snapshot of the device.\n"
        f"# Layout taken from {scatter_path.name}; device offset = physical + 0x{bias:X}.\n"
        "# MBR/EBR and the player's own storage are listed but never written.\n"
        "############################################################################################################\n"
        "- general: MTK_PLATFORM_CFG\n  info:\n    - config_version: V1.1.1\n      platform: MT6582\n"
        "      project: eastaeon82_wet_kk\n      storage: EMMC\n      boot_channel: MSDC_0\n"
        "      block_size: 0x20000\n" + "".join("\n" + block for block in blocks),
        encoding="utf-8",
    )
    _write_package_metadata(package, bias, evidence, images, preloader_size, version,
                            scatter_path, scatter_out)
    return 0


def _write_package_metadata(package: Path, bias: int, evidence: str, images: list[dict],
                            preloader_size: int, version: str, scatter_path: Path,
                            scatter_out: Path) -> None:
    (package / "fw_package.ini").write_text(
        "[package]\n"
        f"software_name = Y2 readback {version}\n"
        f"version = {version}\n"
        "target_model = Y2\n"
        "target_type = \n"
        f"description = Read-only snapshot using the {scatter_path.name} layout verbatim.\n"
        "enabled = true\npost_install = \npost_install2 = \n",
        encoding="utf-8",
    )
    (package / "SHA256SUMS").write_text(
        "".join(f"{item['sha256']}  {item['file']}\n" for item in images), encoding="utf-8")
    (package / "manifest.json").write_text(json.dumps({
        "status": "complete",
        "source": "read-only mtkclient readback (ro/rf), no writes",
        "scatter": str(scatter_path),
        "scatter_written": scatter_out.name,
        "device_bias": bias,
        "bias_evidence": evidence,
        "preloader_size": preloader_size,
        "images": images,
    }, indent=2) + "\n", encoding="utf-8")
    print(f"[package] -> {package} ({len(images)} images, {scatter_out.name})")


def image_name(part: dict, verbatim: bool) -> str:
    """File name for a partition image: the package's own name when reproducing one."""
    if verbatim and part.get("file") and part["file"].upper() != "NONE":
        return part["file"]
    return f"{part['name'].lower()}.img"


def capture_from_scatter(out: Path, scatter_path: Path, version: str, include_user_content: bool,
                         loader: Path, preloader: Path, dry_run: bool,
                         verbatim: bool = False) -> int:
    """Read every partition of a package layout at its proven address, in one session.

    Sizes are the scatter's own ``partition_size``, so each file is exactly the partition a
    package would carry - including the protected/invisible ones a raw slice skipped. The
    package's MBR/EBR images are read too and compared afterwards, which is what proves the
    bias on this device rather than assuming it.
    """
    table = load_scatter(scatter_path)
    if not table:
        sys.exit(f"no partitions parsed from {scatter_path}")
    package = out / "package"
    package.mkdir(parents=True, exist_ok=True)
    head = out / "boot1_head.bin"

    cmds = [f"ro 0x0 0x100000 {head} --parttype=boot1"]
    wanted = []
    for part in table:
        if part["region"] != "EMMC_USER" or part["size"] <= 0:
            continue
        if part["name"].upper() in USER_CONTENT and not include_user_content:
            continue
        if part["physical"] >= 0xFFFF0000:
            continue  # tail-relative sentinel (BMTPOOL), not an address
        path = package / image_name(part, verbatim)
        cmds.append(f"ro {hex(part['physical'] + DEFAULT_USER_BIAS)} {hex(part['size'])} "
                    f"{path} --parttype=user")
        wanted.append(part)

    rc = run_mtk(cmds, loader, preloader, dry_run)
    if dry_run:
        return rc

    bias = DEFAULT_USER_BIAS
    evidence = f"all partitions read at physical + 0x{bias:X}"
    mbr_part = next((p for p in table if p["name"] == "MBR"), None)
    source = scatter_path.parent / "MBR"
    if mbr_part is not None and source.is_file():
        got = (package / "mbr.img").read_bytes()[:SECTOR] if (package / "mbr.img").is_file() else b""
        if got and got != source.read_bytes()[:SECTOR]:
            sys.exit("[capture] the MBR read back does not match the package's table image - "
                     "the layout does not apply to this device, so no package metadata was written")
        if got:
            print("[capture] MBR matches the package's table image")

    images = []
    for part in wanted:
        path = package / image_name(part, verbatim)
        if not path.is_file():
            continue
        images.append({"name": part["name"], "offset": part["physical"] + bias,
                       "size": path.stat().st_size, "file": path.name, "sha256": sha256(path)})
        print(f"[capture] {part['name']:<10} 0x{part['physical'] + bias:08X} "
              f"{path.stat().st_size / 1048576:7.1f} MiB -> {path.name}")

    pre_src, pre_size = extract_preloader(head, package)
    if pre_src is None:
        sys.exit("[capture] no preloader found in the boot1 head")
    (package / "preloader_eastaeon82_wet_kk.bin").write_bytes(pre_src.read_bytes())
    pre_src.unlink()
    print(f"[capture] PRELOADER  {pre_size} bytes -> preloader_eastaeon82_wet_kk.bin")
    images.insert(0, {"name": "PRELOADER", "offset": 0, "size": pre_size,
                      "file": "preloader_eastaeon82_wet_kk.bin",
                      "sha256": sha256(package / "preloader_eastaeon82_wet_kk.bin")})
    return write_package_meta(package, table, bias, evidence, images, pre_size, version,
                              scatter_path, verbatim)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_preloader(boot1_path: Path, out_dir: Path) -> tuple[Path | None, int]:
    """Slice the raw preloader out of the EMMC_BOOT_1 head (descriptor at 0x200, image at 0x800)."""
    if not boot1_path.is_file():
        return None, 0
    head = boot1_path.read_bytes()[:0x100000]
    if len(head) < PRELOADER_DATA_OFFSET + 16:
        return None, 0
    boundary = int.from_bytes(head[0x200 + 16:0x200 + 20], "little")
    length = boundary - PRELOADER_DATA_OFFSET if 0 < boundary <= len(head) else 0
    data = head[PRELOADER_DATA_OFFSET:PRELOADER_DATA_OFFSET + length] if length > 0 else b""
    data = data.rstrip(b"\xff\x00")  # the boundary field is 512-byte rounded padding
    if not data[:3] == b"MMM":
        magic = head.find(b"MMM\x01", PRELOADER_DATA_OFFSET)
        if magic < 0:
            return None, 0
        data = head[magic:].rstrip(b"\xff\x00") or head[magic:]
    if not data:
        return None, 0
    path = out_dir / "preloader.bin"
    path.write_bytes(data)
    return path, len(data)


def scatter_block(name: str, index: int, *, region: str, linear: int, physical: int, size: int,
                  ftype: str, download: bool, operation: str, file_name: str) -> str:
    return (
        f"- partition_index: SYS{index}\n"
        f"  partition_name: {name}\n"
        f"  file_name: {file_name}\n"
        f"  is_download: {'true' if download else 'false'}\n"
        f"  type: {ftype}\n"
        f"  linear_start_addr: {hex(linear)}\n"
        f"  physical_start_addr: {hex(physical)}\n"
        f"  partition_size: {hex(size)}\n"
        f"  region: {region}\n"
        f"  storage: HW_STORAGE_EMMC\n"
        f"  boundary_check: {'false' if name == 'BMTPOOL' else 'true'}\n"
        f"  is_reserved: {'true' if name == 'BMTPOOL' else 'false'}\n"
        f"  operation_type: {operation}\n"
        f"  reserve: 0x00\n"
    )


def slice_archive(out: Path, version: str, include_user_content: bool) -> int:
    raw = out / "user_area.raw"
    if not raw.is_file():
        sys.exit(f"no dump at {raw} - run the 'dump' action first (device needed)")
    size = raw.stat().st_size
    print(f"[slice] user_area.raw: {size} bytes ({size / 1048576:.1f} MiB)")
    if size < 0x400000:
        sys.exit("[slice] dump is too small to hold an MBR/EBR chain; re-run the readback")

    dump = raw.read_bytes()
    base, extents = detect_base(dump)
    if not extents:
        sys.exit(
            "[slice] no parseable MBR/EBR chain at any known base "
            f"({', '.join(hex(b) for b in MBR_BASE_CANDIDATES)}).\n"
            "        The read probably landed on a blank region; check the mtk.py log, then\n"
            "        try again with the right preloader for the device's DRAM (--preloader)."
        )
    print(f"[slice] MBR base: {hex(base)}; chain entries: {len(extents)}")

    archive = out / "archive"
    archive.mkdir(parents=True, exist_ok=True)

    # Table slots are the chain's own sectors: MBR plus each EBR, in the scatter's convention.
    entries: list[dict] = []
    table_offsets = sorted({base} | {t for t, _, _ in extents})
    for pos, table in enumerate(table_offsets):
        name = "MBR" if table == base else f"EBR{pos}"
        ref = REFERENCE_BY_OFFSET.get(table - base)
        if ref and ref[0].startswith("EBR"):
            name = ref[0]
        entries.append({"name": name, "offset": table, "size": TABLE_SLOT,
                        "type": "NORMAL_ROM", "download": True, "operation": "UPDATE"})

    for _, start, length in extents:
        ref = REFERENCE_BY_OFFSET.get(start - base)
        name = ref[0] if ref else f"PART_{start - base:08X}"
        if not ref and length > len(dump) - start:
            # The chain's last entry claims the rest of the eMMC (FAT holds the player's
            # own storage); it is not firmware and is skipped like the other user data.
            ref = REFERENCE_BY_OFFSET[0x72380000]
            name = ref[0]
        if ref and ref[1] and ref[1] != length:
            print(f"[slice] {name}: device {length:#x} vs reference {ref[1]:#x} (using device)")
        entries.append({
            "name": name,
            "offset": start,
            "size": length,
            "type": ref[2] if ref else "NORMAL_ROM",
            "download": ref[3] if ref else False,
            "operation": ref[4] if ref else "UPDATE",
            "source": "mbr-chain",
        })

    entries.extend(complete_from_reference(entries, base, dump))
    entries.sort(key=lambda item: item["offset"])
    renamed: dict[str, int] = {}
    for entry in entries:
        count = renamed.get(entry["name"], 0) + 1
        renamed[entry["name"]] = count
        if count > 1:
            entry["name"] = f"{entry['name']}_{count}"

    images = []
    for entry in entries:
        if not include_user_content and entry["name"] in USER_CONTENT:
            continue
        offset, length = entry["offset"], entry["size"]
        if offset + length > len(dump):
            length = max(0, len(dump) - offset)
            print(f"[slice] {entry['name']}: truncated to {length:#x} bytes at the end of the dump")
        if not length:
            continue
        path = archive / f"{entry['name']}.bin"
        path.write_bytes(dump[offset:offset + length])
        images.append({"name": entry["name"], "region": "EMMC_USER", "offset": offset,
                       "size": length, "file": path.name, "source": entry.get("source", "mbr-chain"),
                       "sha256": sha256(path)})
        print(f"[slice] {entry['name']:<12} 0x{offset:X} {length / 1048576:8.1f} MiB -> {path.name}")

    preloader, preloader_size = extract_preloader(out / "boot1_head.bin", archive)
    if preloader is not None:
        print(f"[slice] PRELOADER   {preloader_size} bytes -> {preloader.name}")
        images.insert(0, {"name": "PRELOADER", "region": "EMMC_BOOT_1", "offset": 0,
                          "size": preloader_size, "file": preloader.name, "source": "boot1",
                          "sha256": sha256(preloader)})
    else:
        print("[slice] PRELOADER: no boot1_head.bin in the dump; archive has no preloader")

    # Scatter: physical column keeps the engine's +0x800000 round trip to the measured address.
    blocks = []
    physical_bias = SCATTER_PHYSICAL_BIAS if all(e["offset"] >= SCATTER_PHYSICAL_BIAS for e in entries) else 0
    index = 0
    if preloader is not None:
        blocks.append(scatter_block("PRELOADER", index, region="EMMC_BOOT_1", linear=0, physical=0,
                                    size=BOOT_REGION_SIZE, ftype="SV5_BL_BIN", download=True,
                                    operation="BOOTLOADERS", file_name=preloader.name))
        index += 1
    for entry in entries:
        physical = entry["offset"] - physical_bias
        blocks.append(scatter_block(
            entry["name"], index, region="EMMC_USER",
            linear=physical + SCATTER_LINEAR_BIAS, physical=physical, size=entry["size"],
            ftype=entry["type"], download=entry["download"], operation=entry["operation"],
            file_name=f"{entry['name']}.bin" if entry["download"] else "NONE",
        ))
        index += 1
    scatter = archive / "MT6582_Android_scatter.txt"
    scatter.write_text(
        "############################################################################################################\n"
        "# Generated by debug/y2_archive_readback.py from a read-only snapshot of the device's own\n"
        f"# MBR/EBR chain (MBR at device offset 0x{base:X}). Verify before flashing anything.\n"
        "############################################################################################################\n"
        "- general: MTK_PLATFORM_CFG\n"
        "  info:\n"
        "    - config_version: V1.1.1\n"
        "      platform: MT6582\n"
        "      project: eastaeon82_wet_kk\n"
        "      storage: EMMC\n"
        "      boot_channel: MSDC_0\n"
        "      block_size: 0x20000\n"
        + "".join("\n" + block for block in blocks),
        encoding="utf-8",
    )
    print(f"[slice] scatter -> {scatter.name} ({len(blocks)} entries)")

    (archive / "fw_package.ini").write_text(
        "[package]\n"
        f"software_name = Y2 readback {version}\n"
        f"version = {version}\n"
        "target_model = Y2\n"
        "target_type = \n"
        "description = Read-only snapshot of the firmware the player shipped with.\n"
        "enabled = true\n"
        "post_install = \n"
        "post_install2 = \n"
        "derivative = \n",
        encoding="utf-8",
    )

    sums = "".join(f"{item['sha256']}  {item['file']}\n" for item in images)
    (archive / "SHA256SUMS").write_text(sums, encoding="utf-8")
    manifest = {
        "status": "complete",
        "source": "read-only mtkclient readback (ro/rf), no writes",
        "device": "Innioasis Y2 / MT6582",
        "mbr_base": base,
        "physical_bias": physical_bias,
        "user_area_bytes": len(dump),
        "preloader": {"file": preloader.name, "size": preloader_size} if preloader else None,
        "scatter_file": scatter.name,
        "images": images,
    }
    (archive / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"[slice] {len(images)} images, SHA256SUMS and manifest.json written to {archive}")
    return 0


# ---------------------------------------------------------------- entry point
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["dump", "slice", "capture"])
    ap.add_argument("out", help="directory for the dump and the sliced archive")
    ap.add_argument("--version", default="3.2.4", help="firmware version label for fw_package.ini")
    ap.add_argument("--length", type=lambda x: int(x, 0), default=0,
                    help="bytes of EMMC_USER to read (default: the DA's reported flash size)")
    ap.add_argument("--preloader", default=str(DEFAULT_PRELOADER),
                    help="preloader used for DRAM init; must match the device's DRAM")
    ap.add_argument("--da", default=str(DEFAULT_DA), help="download agent")
    ap.add_argument("--include-user-content", action="store_true",
                    help="also capture USRDATA/CACHE/FAT (personal data, not firmware)")
    ap.add_argument("--verbatim", action="store_true",
                    help="copy the source scatter and image names unchanged (reproduces the vendor package)")
    ap.add_argument("--scatter", help="slice with this package scatter's layout instead of the built-in table")
    ap.add_argument("--print", dest="dry_run", action="store_true", help="print the mtk.py command only")
    a = ap.parse_args()

    out = Path(a.out).expanduser().resolve()
    if a.action == "capture":
        if not a.scatter:
            sys.exit("capture needs --scatter: the layout to read every partition at")
        scatter = Path(a.scatter).expanduser().resolve()
        preloader = Path(a.preloader).expanduser().resolve()
        if not preloader.is_file():
            sys.exit(f"preloader not found: {preloader}")
        out.mkdir(parents=True, exist_ok=True)
        return capture_from_scatter(out, scatter, a.version, a.include_user_content,
                                    da_loader(Path(a.da)), preloader, a.dry_run, a.verbatim)
    if a.action == "slice":
        if a.scatter:
            return slice_from_scatter(out, Path(a.scatter).expanduser().resolve(), a.version)
        return slice_archive(out, a.version, a.include_user_content)
    out.mkdir(parents=True, exist_ok=True)
    if " " in str(out) or ";" in str(out):
        sys.exit(f"output path must not contain a space or ';' (mtk.py multi splits on them): {out}")
    preloader = Path(a.preloader).expanduser().resolve()
    if not preloader.is_file():
        sys.exit(f"preloader not found: {preloader}\n"
                 "pass --preloader with a preloader whose EMI/dram config matches this device")
    rc = run_mtk(read_commands(out, a.length), da_loader(Path(a.da)), preloader, a.dry_run)
    if a.dry_run:
        return rc
    if rc != 0:
        print(f"[readback] mtk.py exited {rc}; slice whatever landed in {out} with: "
              f"python {Path(__file__).name} slice {out}", flush=True)
    return slice_archive(out, a.version, a.include_user_content)


if __name__ == "__main__":
    sys.exit(main())
