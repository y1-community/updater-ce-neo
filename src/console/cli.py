"""Command line interface for Updater CE on machines without Qt.

    innioasis-updater-lite list --model Y1
    innioasis-updater-lite download Y1 --output ~/firmware
    innioasis-updater-lite flash ~/firmware/rom_Y1 --dry-run
    innioasis-updater-lite adb devices
    innioasis-updater-lite doctor --json
    innioasis-updater-lite tui

Every command supports ``--json`` so an agent or a script can drive it: one
JSON object per line, progress on stderr. That is the same contract the GUI's
``--flash-cli`` already uses for the terminal install script.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import catalog, components, download as dl, flash


def _version() -> str:
    try:
        repo = Path(__file__).resolve().parents[2]
        for line in (repo / "src" / "config.py").read_text(encoding="utf-8").splitlines():
            if line.startswith("APP_VERSION"):
                return line.split("=", 1)[1].strip().strip("'\"")
    except OSError:
        pass
    return "2.5.5"


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        for key, value in payload.items():
            if value not in ("", None):
                print(f"{key}: {value}")


def cmd_list(args: argparse.Namespace) -> int:
    items = catalog.find(catalog.packages(force=args.refresh), args.query or "")
    if args.model:
        want = args.model.lower()
        items = [item for item in items if item.model.lower() == want]
    if args.json:
        print(json.dumps([item.__dict__ for item in items], ensure_ascii=False), flush=True)
        return 0
    if not items:
        print("No firmware matched. Try: list --model Y1")
        return 1
    print(f"{'MODEL':<6} {'BRAND':<12} {'NAME':<34} REPO")
    for item in items:
        print(f"{item.model:<6} {item.brand:<12} {item.name[:34]:<34} {item.repo}")
    print(f"\n{len(items)} entries. Download with: download <model> --output <dir>")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    items = catalog.find(catalog.packages(force=args.refresh), args.query)
    if args.model:
        items = [item for item in items if item.model.lower() == args.model.lower()]
    if not items:
        print(f"No firmware matched '{args.query}'.", file=sys.stderr)
        return 1
    if len(items) > 1 and not args.all:
        print("Several firmwares matched; pick one with a narrower query or --model:", file=sys.stderr)
        for item in items:
            print(f"  {item.model:<5} {item.brand:<12} {item.name}", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser()
    output.mkdir(parents=True, exist_ok=True)
    failures = 0
    for item in items:
        try:
            url, asset_name = catalog.resolve_download(item)
        except RuntimeError as exc:
            print(f"{item.model} {item.name}: {exc}", file=sys.stderr)
            failures += 1
            continue
        target = output / asset_name
        if target.exists() and not args.force:
            print(f"{target} already exists (use --force to download again)")
            if args.json:
                print(json.dumps({"model": item.model, "asset": asset_name, "path": str(target), "cached": True}), flush=True)
            continue
        if args.json:
            print(json.dumps({"model": item.model, "asset": asset_name, "url": url}), flush=True)
        try:
            dl.download(url, target, progress=dl.report_progress(f"{item.model} {asset_name}"))
        except (RuntimeError, OSError) as exc:
            print(f"download failed: {exc}", file=sys.stderr)
            failures += 1
            continue
        if args.extract:
            folder = output / target.stem
            written = dl.extract(target, folder)
            print(f"extracted {len(written)} files into {folder}")
    return 1 if failures else 0


def cmd_flash(args: argparse.Namespace) -> int:
    folder = Path(args.path).expanduser()
    if not folder.is_dir():
        print(f"{folder} is not a directory. Flash takes an unpacked firmware folder.", file=sys.stderr)
        return 2
    scatter = dl.find_scatter(folder)
    if args.engine == "mtk" and not scatter and not args.preloader:
        print(
            f"No scatter file in {folder}. A MediaTek flash needs the scatter that ships with the firmware.",
            file=sys.stderr,
        )
        return 2
    if args.engine == "adb":
        if args.sideload:
            return flash.adb(["sideload", str(folder) if folder.is_file() else args.sideload], dry_run=args.dry_run)
        return flash.adb(["devices"], dry_run=args.dry_run)
    return flash.flash_directory(
        folder,
        preloader=Path(args.preloader).expanduser() if args.preloader else None,
        da=Path(args.da).expanduser() if args.da else None,
        auth=Path(args.auth).expanduser() if args.auth else None,
        dry_run=args.dry_run,
    )


def cmd_adb(args: argparse.Namespace) -> int:
    if not args.adb_args:
        return flash.adb(["devices"], dry_run=args.dry_run)
    return flash.adb(args.adb_args, dry_run=args.dry_run)


def cmd_components(args: argparse.Namespace) -> int:
    if args.action == "list":
        rows = components.status()
        if args.json:
            print(json.dumps(rows, ensure_ascii=False), flush=True)
        else:
            for row in rows:
                print(f"{row['label']:<14} {row['path'] or 'not installed'}")
            print("\nFetch with: components fetch adb spflash")
        return 0
    if args.action == "prefetch":
        result = components.prefetch(args.keys or None)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    failures = 0
    for key in args.keys or tuple(components.COMPONENTS):
        try:
            print(components.ensure(key))
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            failures += 1
    return 1 if failures else 0


def cmd_doctor(args: argparse.Namespace) -> int:
    info = flash.doctor()
    info["components"] = {
        row["key"]: row["path"] for row in components.status() if row["available"]
    }
    if args.json:
        print(json.dumps(info, ensure_ascii=False), flush=True)
        return 0
    print("Updater CE terminal check")
    for key in ("python", "platform", "machine", "tty", "mtkclient", "adb", "unrar", "mount"):
        value = info.get(key) or "(not found)"
        print(f"  {key:<10} {value}")
    if info.get("wsl"):
        print("\nWindows Subsystem for Linux")
        if info.get("wsl_distro"):
            print(f"  distro     {info['wsl_distro']}")
        print(f"  usbipd     {info.get('usbipd') or 'usbipd.exe not on PATH'}")
        print("  USB passthrough:")
        for step in info.get("wsl_steps") or ():
            print(f"    - {step}")
    missing = [name for name in ("mtkclient", "adb") if not info.get(name)]
    if missing:
        print("\nMissing: " + ", ".join(missing))
        print("  Debian/Ubuntu/Mint: sudo apt install android-tools-adb")
        print("  Fedora:             sudo dnf install android-tools")
        print("  Arch/Manjaro:       sudo pacman -S android-tools")
        print("  MTKClient:          pip install --user mtkclient")
        return 1
    print("\nAll console tools are present.")
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    from . import tui

    return tui.run(refresh=args.refresh)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="innioasis-updater-lite",
        description="Updater CE for headless and low-power Linux machines.",
    )
    parser.add_argument("--version", action="version", version=f"Updater CE {_version()} (terminal)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command")

    listing = sub.add_parser("list", help="list firmware in the catalogue")
    listing.add_argument("query", nargs="?", default="")
    listing.add_argument("--model", default="")
    listing.add_argument("--refresh", action="store_true")
    listing.set_defaults(func=cmd_list)

    get = sub.add_parser("download", help="download a firmware zip")
    get.add_argument("query")
    get.add_argument("--model", default="")
    get.add_argument("--output", default=".")
    get.add_argument("--extract", action="store_true")
    get.add_argument("--all", action="store_true")
    get.add_argument("--force", action="store_true")
    get.add_argument("--refresh", action="store_true")
    get.set_defaults(func=cmd_download)

    take = sub.add_parser("flash", help="write an unpacked firmware to the player")
    take.add_argument("path")
    take.add_argument("--engine", choices=("mtk", "adb"), default="mtk")
    take.add_argument("--preloader", default="")
    take.add_argument("--da", default="")
    take.add_argument("--auth", default="")
    take.add_argument("--sideload", default="")
    take.add_argument("--dry-run", action="store_true")
    take.set_defaults(func=cmd_flash)

    adb_parser = sub.add_parser("adb", help="run an adb command")
    adb_parser.add_argument("adb_args", nargs=argparse.REMAINDER)
    adb_parser.add_argument("--dry-run", action="store_true")
    adb_parser.set_defaults(func=cmd_adb)

    tools = sub.add_parser("components", help="show or fetch adb, SP Flash Tool, unrar")
    tools.add_argument("action", nargs="?", choices=("list", "fetch", "prefetch"), default="list")
    tools.add_argument("keys", nargs="*")
    tools.set_defaults(func=cmd_components)

    doctor = sub.add_parser("doctor", help="report the tools this machine has")
    doctor.set_defaults(func=cmd_doctor)

    ui = sub.add_parser("tui", help="open the terminal interface")
    ui.add_argument("--refresh", action="store_true")
    ui.set_defaults(func=cmd_tui)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-") and argv[0] not in ("--version",):
        argv = ["tui"] + argv if sys.stdin.isatty() else ["--help"] + argv
    parser = build_parser()
    # --json is documented as usable after the command too, so accept it
    # anywhere instead of only before the subcommand.
    as_json = "--json" in argv
    argv = [arg for arg in argv if arg != "--json"]
    args = parser.parse_args(argv)
    args.json = as_json or getattr(args, "json", False)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
