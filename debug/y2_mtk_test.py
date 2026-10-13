#!/usr/bin/env python3
"""Standalone mtkclient flash/readback harness for Innioasis / Timmkoo (MT6582) devices.

Drives the *vendored upstream* ``vendor/mtkclient/mtk.py`` directly, with none of the Updater CE
middle-man (no flash_service, no mtk_api runtime patches), so a failure here is a failure of plain
mtkclient. Everything is derived from the ROM's own scatter file and its own preloader.

    python debug/y2_mtk_test.py readback <rom_dir> --out good      # read-only snapshot
    python debug/y2_mtk_test.py write    <rom_dir> --only MBR,EBR1  # write + verify by readback
    python debug/y2_mtk_test.py compare  <rom_dir> good            # diff a snapshot vs ROM files
    python debug/y2_mtk_test.py cmd      <rom_dir> --print          # print the raw mtk.py command

See debug/README.md. Power the device OFF, run the command, THEN hold Vol Up + Vol Down and plug in.
"""
from __future__ import annotations

import argparse
import hashlib
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
DEFAULT_DA = REPO / "vendor" / "mtkclient" / "mtkclient" / "Loader" / "MTK_AllInOne_DA_mt6590.bin"
WORK = HERE / "work"

# Regions the scatter lists with no file (calibration / IMEI / security): read-only by default.
BOOT_REGION = "EMMC_BOOT_1"
SKIP_WRITE = {"PRELOADER"}  # needs the EMMC_BOOT/BRLYT wrap; written separately, see README


class Part:
    def __init__(self, d: dict):
        self.name = d["partition_name"]
        self.file = d.get("file_name", "NONE")
        self.addr = int(d["linear_start_addr"], 16)
        self.size = int(d["partition_size"], 16)
        self.region = d.get("region", "EMMC_USER")
        self.download = d.get("is_download", "false").lower() == "true"

    @property
    def parttype(self) -> str:
        return "boot1" if self.region == BOOT_REGION else "user"

    @property
    def has_file(self) -> bool:
        return self.file not in ("NONE", "", None)


def parse_scatter(path: Path) -> list[Part]:
    parts, cur = [], {}
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"\s*-?\s*(\w+):\s*(.+?)\s*$", line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if k == "partition_index" and cur.get("partition_name"):
            parts.append(Part(cur))
            cur = {}
        cur[k] = v
    if cur.get("partition_name"):
        parts.append(Part(cur))
    return parts


def find_scatter(rom: Path) -> Path:
    for pat in ("*scatter*.txt", "*Scatter*.txt"):
        hit = sorted(rom.glob(pat))
        if hit:
            return hit[0]
    sys.exit(f"no scatter file in {rom}")


def find_preloader(rom: Path, parts: list[Part]) -> Path:
    for p in parts:
        if p.name.upper() == "PRELOADER" and p.has_file and (rom / p.file).exists():
            return rom / p.file
    hit = sorted(rom.glob("preloader*.bin"))
    if hit:
        return hit[0]
    sys.exit(f"no preloader in {rom} - the ROM's own preloader is required for DRAM init")


def stage(rom: Path) -> Path:
    """multi splits on spaces/';', so run from a space-free copy of the small files."""
    if " " not in str(rom) and ";" not in str(rom):
        return rom
    dst = Path(tempfile.gettempdir()) / "y2test_rom"
    shutil.rmtree(dst, ignore_errors=True)
    dst.mkdir(parents=True)
    for f in rom.iterdir():
        # skip the multi-hundred-MB images unless a write needs them (handled by symlink)
        try:
            os.symlink(f, dst / f.name)
        except OSError:
            shutil.copy2(f, dst / f.name)
    return dst


def python_exe() -> str:
    for cand in (REPO / ".venv-build" / "bin" / "python", REPO / ".venv" / "bin" / "python"):
        if cand.exists():
            return str(cand)
    return sys.executable


def run_multi(cmds: list[str], loader: Path, preloader: Path, dry: bool) -> int:
    argv = [python_exe(), str(MTK), f"--loader={loader}", f"--preloader={preloader}",
            "multi", ";".join(cmds)]
    if dry:
        print(" ".join(f"'{a}'" if " " in a else a for a in argv))
        return 0
    for c in cmds:
        # `multi` does cmd.split(";") then split(" "), so every path inside must be space-free.
        if len(c.split(" ")) != 5:
            sys.exit(f"path with a space/semicolon would break mtk.py multi: {c}\n"
                     f"use --out/--verify under a space-free directory (default debug/work needs a "
                     f"space-free repo path)")
    env = dict(os.environ, PYTHONPATH=str(REPO / "vendor" / "mtkclient"))
    print("[debug] waiting for device: power OFF, then hold Vol Up + Vol Down and plug in USB",
          flush=True)
    return subprocess.call(argv, env=env, cwd=str(REPO))


def readback_cmds(parts: list[Part], out: Path, only: set[str] | None, head: int) -> list[str]:
    cmds = []
    for p in parts:
        if only and p.name not in only:
            continue
        if p.name == "BMTPOOL" or p.addr >= 0xFFFF0000 or p.size == 0:
            continue
        length = p.size if p.size <= 0x1000000 else head
        if p.region == BOOT_REGION:
            length = min(p.size, 0x400000)
        cmds.append(f"ro {hex(p.addr if p.region != BOOT_REGION else 0)} {hex(length)} "
                    f"{out / p.name} --parttype={p.parttype}")
    return cmds


def cmd_readback(a, parts, rom, loader, pre):
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    only = set(a.only.split(",")) if a.only else None
    cmds = readback_cmds(parts, out, only, a.head)
    return run_multi(cmds, loader, pre, a.print)


def cmd_write(a, parts, rom, loader, pre):
    only = set(a.only.split(",")) if a.only else None
    ver = Path(a.verify).resolve()
    ver.mkdir(parents=True, exist_ok=True)
    cmds, todo = [], []
    for p in parts:
        if not (p.download and p.has_file) or p.name in SKIP_WRITE:
            continue
        if only and p.name not in only:
            continue
        f = rom / p.file
        if not f.exists():
            print(f"[debug] skip {p.name}: {f.name} missing")
            continue
        size = f.stat().st_size
        if size > p.size:
            sys.exit(f"{p.name}: image {size:#x} larger than partition {p.size:#x}")
        cmds.append(f"wo {hex(p.addr)} {hex(size)} {f} --parttype={p.parttype}")
        cmds.append(f"ro {hex(p.addr)} {hex(size)} {ver / p.name} --parttype={p.parttype}")
        todo.append((p, f, size))
    if not cmds:
        sys.exit("nothing to write")
    rc = run_multi(cmds, loader, pre, a.print)
    if a.print or rc != 0:
        return rc
    return verify(todo, ver)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:12]


def verify(todo, ver: Path) -> int:
    bad = 0
    for p, f, size in todo:
        got = (ver / p.name)
        if not got.exists():
            print(f"FAIL {p.name:10s} no readback"); bad += 1; continue
        want, have = f.read_bytes(), got.read_bytes()[:size]
        if want == have:
            print(f"OK   {p.name:10s} {size:#x} bytes verified ({sha(want)})")
        else:
            d = next((i for i in range(min(len(want), len(have))) if want[i] != have[i]), len(have))
            print(f"FAIL {p.name:10s} first diff @ {d:#x}"); bad += 1
    return 1 if bad else 0


def cmd_compare(a, parts, rom, loader, pre):
    snap = Path(a.snapshot).resolve()
    bad = 0
    for p in parts:
        f, g = rom / p.file, snap / p.name
        if not (p.has_file and f.exists() and g.exists()):
            continue
        want, have = f.read_bytes(), g.read_bytes()[:f.stat().st_size]
        if want == have:
            print(f"MATCH {p.name}")
        else:
            d = next((i for i in range(min(len(want), len(have))) if want[i] != have[i]), len(have))
            print(f"DIFF  {p.name:10s} first diff @ {d:#x}  dev_zeros={not any(have)}")
            bad += 1
    return 1 if bad else 0


def cmd_cmd(a, parts, rom, loader, pre):
    a.print = True
    return cmd_readback(a, parts, rom, loader, pre)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["readback", "write", "compare", "cmd"])
    ap.add_argument("rom", help="ROM folder (contains the scatter file + preloader)")
    ap.add_argument("snapshot", nargs="?", help="snapshot dir for 'compare'")
    ap.add_argument("--out", default=str(WORK / "readback"))
    ap.add_argument("--verify", default=str(WORK / "verify"))
    ap.add_argument("--only", help="comma list of partition names, e.g. MBR,EBR1,LK")
    ap.add_argument("--head", type=lambda x: int(x, 0), default=0x100000,
                    help="bytes to read from partitions larger than 16 MiB (default 1 MiB)")
    ap.add_argument("--da", default=str(DEFAULT_DA), help="download agent (default: repo mt6590 DA)")
    ap.add_argument("--print", action="store_true", help="print the mtk.py command, don't run")
    a = ap.parse_args()

    rom = Path(a.rom).expanduser().resolve()
    parts = parse_scatter(find_scatter(rom))
    pre = find_preloader(rom, parts)
    srom = stage(rom)
    pre_s = srom / pre.name
    da = Path(a.da).expanduser().resolve()
    if "mt6590" not in da.name.lower():
        # upstream daconfig.parse_da_loader skips 0x65xx entries unless the filename has "mt6590"
        alias = Path(tempfile.gettempdir()) / "y2test_da" / "MTK_AllInOne_DA_mt6590.bin"
        alias.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(da, alias)
        print(f"[debug] {da.name} -> {alias} (filename must contain 'mt6590' for MT65xx entries)")
        da = alias
    handlers = {"readback": cmd_readback, "write": cmd_write, "compare": cmd_compare, "cmd": cmd_cmd}
    print(f"[debug] rom={rom}\n[debug] preloader={pre.name}  da={da.name}  partitions={len(parts)}")
    return handlers[a.action](a, parts, srom if a.action != "compare" else rom, da, pre_s)


if __name__ == "__main__":
    sys.exit(main())
