# debug/ — plain-mtkclient test harness (no Updater CE in the loop)

Portable (macOS / Linux / Windows-with-Python) scripts for flashing and reading back
Innioasis / Timmkoo MT6582 devices through the **vendored upstream** `vendor/mtkclient/mtk.py`
only. Nothing from `src/flash_service.py` or `src/mtk_api.py` is involved, so a failure here is a
failure of stock mtkclient. Copy the whole `debug/` folder (plus `vendor/mtkclient/`) to another
machine to reproduce.

```
python debug/y2_mtk_test.py readback <rom_dir> --out debug/work/good          # read-only snapshot
python debug/y2_mtk_test.py compare  <rom_dir> debug/work/good                # snapshot vs ROM files
python debug/y2_mtk_test.py write    <rom_dir> --only MBR,EBR1,LOGO,UBOOT     # write + readback-verify
python debug/y2_mtk_test.py write    <rom_dir>                                # every download partition
python debug/y2_mtk_test.py cmd      <rom_dir> --print                        # just show the mtk.py command
```

**Order of operations:** run the command first (it waits for the device), *then* power the device
off, hold Vol Up + Vol Down and plug in USB. If it exits at once with
`Please disconnect, start mtkclient and reconnect`, the device is still attached from a previous
session: unplug it, power it off, and start over.

## What it does for you
- Addresses, sizes and regions come from the ROM's own `*scatter*.txt` (`linear_start_addr`,
  `partition_size`, `region`), written with `wo` / read with `ro` — **not** `wl`/`w`, which map
  file names onto GPT partition names and cannot work on this MBR/EBR (no GPT) legacy layout.
- The **ROM's own preloader** (`preloader_*.bin` next to the scatter) is passed as `--preloader`.
- Every write is followed by a readback in the same session and compared byte-for-byte.
- `PRELOADER` is read but never written: boot1 holds an `EMMC_BOOT` header + `BRLYT` at 0x200 +
  the preloader at 0x800, so a raw write needs the wrap done by `_wrap_preloader_for_raw_boot`.
- Use `--da` to try a different download agent (default: repo `MTK_AllInOne_DA_mt6590.bin`).

## Findings so far (Innioasis Y2, MT6582, hwcode 0x6582, BROM, no SBC/SLA/DAA)
1. **The ROM's own preloader is mandatory for DRAM init.** mtkclient asks the DA for the DRAM id
   (`150100463732324d42003809dedf73dd`) and, when `--preloader` is absent, scans
   `Loader/Preloader/*` and picks the *first* match: `preloader_wt98999_lterow.bin` (EMI version
   0x14). That EMI blob is wrong for the Y2 and the session dies with `USBError(32, 'Pipe error')`
   / `struct.error: unpack requires a buffer of 4 bytes`. The ROM's `preloader_eastaeon82_wet_kk.bin`
   parses to EMI version 0x0C (2068 bytes) and contains that DRAM id; with it, `M_EXT_RAM_RET: 0`,
   1 GB RAM, stage 2 uploads and the eMMC is identified.
2. **Option order matters.** For every subcommand built from the shared parent parser
   (`r`, `w`, `wl`, `ro`, `wo`, ...), `--loader/--preloader` must come **after** the subcommand;
   before it, the subparser's defaults silently overwrite them. `multi` is the opposite: options go
   **before** `multi`, and each inner command string is split on `;` and spaces (space-free paths).
3. **The Innioasis `MTK_AllInOne_DA.bin` (innioasis.app, SHA-256 `4729b779…`) is byte-identical to
   `tools/*/SP_Flash_Tool_*/MTK_AllInOne_DA.bin`**, but upstream `daconfig.parse_da_loader` skips
   every `0x65xx` entry (except 0x6580) unless the loader *filename contains `mt6590`* — so it
   fails with `No da_loader config set up` under its real name. Copy it to
   `..._mt6590.bin` to use it. The Chinese app's bundled `MTK_AllInOne_DA_mt6590.bin` is
   byte-identical to the vendored one (`49a14137…`), so the DA is not what differs.
4. **Chinese-build mtkclient fork differences** (`/Volumes/InnioasisUpdater/.../mtkclient`, 1546 vs
   1269 lines in `dalegacy_lib.py`): `sdmmc_write_data` uses a configurable chunk size, does *not*
   issue the extra `switch_part` before writes ("MT6582 older DAs reject later write commands"),
   reports NACK status/offsets, and the `emiver in (0x0A, 0x0B)` DRAM branch does a blocking
   `usbread(4)` for the RAM length where upstream polls the CDC queue and may skip it.
5. **State after a previous full flash that "completed" but did not boot** (via Updater CE): boot1
   preloader byte-exact; **MBR, EBR1, EBR2 and LOGO were all zeros**, and LK / BOOTIMG / RECOVERY /
   SEC_RO matched neither ROM at offset 0. The partition table was blank, not a preloader problem.
6. Known-good reference: the same Rockbox ROM flashed with SP Flash Tool (Windows) boots; capture it
   with `readback` for comparison.

## Layout
- `y2_mtk_test.py` — the harness. `--da tools/linux/SP_Flash_Tool_v5.1904_Linux/MTK_AllInOne_DA.bin`
  (the Innioasis DA) works as-is; it is copied to an `mt6590`-named temp file automatically.
- `evidence/` — small, committed artefacts from the live Y2 session (blank MBR/EBR readbacks, boot1
  readback, scatter, hashes, notes).
- `work/` — created at run time (snapshots, verify readbacks); git-ignored, safe to delete.
- **Not in the repo:** the ROM folders (proprietary, hundreds of MB). Pass their path as `<rom_dir>`;
  it only needs the scatter file, the preloader and the images the scatter references.
