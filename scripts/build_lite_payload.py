#!/usr/bin/env python3
"""Package the terminal-only payload that ``run_linux_lite.sh`` installs.

The Python is architecture independent, so one archive runs on x86-64, i686,
aarch64, armv7l and armv6l. It also carries the flashing pieces the window
already ships: the vendored MTKclient tree (the console flasher, including its
Download Agents), adb for each platform that has a binary, and the Linux
x86-64 SP Flash Tool. A 32-bit board uses MTKclient and does not run the
x86-64 binaries.

    python3 scripts/build_lite_payload.py
    python3 scripts/build_lite_payload.py --out dist/linux-lite --site-dir ../Innioasis-Updater

Writes ``UpdaterCE-Lite.tar.gz`` and its ``.sha256`` beside it, and optionally
copies ``run_linux_lite.sh`` into the website repository so it is served at
https://innioasis.app/run_linux_lite.sh.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import re
import shutil
import tarfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PAYLOAD = [
    "lite.py",
    "run_linux_lite.sh",
    "src/__init__.py",
    "src/console/__init__.py",
    "src/console/catalog.py",
    "src/console/cli.py",
    "src/console/components.py",
    "src/console/controls.py",
    "src/console/download.py",
    "src/console/flash.py",
    "src/console/progress.py",
    "src/console/releases.py",
    "src/console/session.py",
    "src/console/simulate.py",
    "src/console/tui.py",
    "src/console/wsl.py",
    "src/tui_session.py",
]
OPTIONAL = ["LICENSE", "LICENSE.md"]


def version() -> str:
    text = (REPO / "src" / "config.py").read_text(encoding="utf-8")
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else "0.0.0"


def _add(tar: tarfile.TarFile, path: Path, arcname: str, mode: int = 0o644) -> None:
    data = path.read_bytes()
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = mode
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


# Trees the window already uses to flash. The Python session is useless for an
# install without them. Binaries stay beside the source so a Pi still gets
# MTKclient, and an x86-64 Linux machine also gets adb and SP Flash Tool.
TOOL_TREES = (
    ("vendor/mtkclient", "vendor/mtkclient"),
    ("tools/adb", "tools/adb"),
    ("tools/linux/SP_Flash_Tool_v5.1904_Linux", "tools/linux/SP_Flash_Tool_v5.1904_Linux"),
    ("tools/linux/unrar", "tools/linux/unrar"),
)


def _reset_meta(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    name = info.name
    base = name.rsplit("/", 1)[-1]
    if base in {".DS_Store", ".gitkeep"} or base.endswith((".pyc", ".pyo")):
        return None
    if "/__pycache__/" in f"/{name}/" or "/.git/" in f"/{name}/":
        return None
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    return info


def build(out_dir: Path, site_dir: Path | None) -> tuple[Path, Path]:
    missing = [name for name in PAYLOAD if not (REPO / name).is_file()]
    if missing:
        raise SystemExit("missing payload files: " + ", ".join(missing))
    for source, _arc in TOOL_TREES:
        if not (REPO / source).exists():
            raise SystemExit(f"missing flashing tool: {source}")

    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / "UpdaterCE-Lite.tar.gz"
    raw_path = out_dir / ".UpdaterCE-Lite.tar"

    with tarfile.open(raw_path, "w") as tar:
        for name in PAYLOAD + [name for name in OPTIONAL if (REPO / name).is_file()]:
            mode = 0o755 if name.endswith((".sh", ".py")) and name != "lite.py" else 0o644
            _add(tar, REPO / name, name, mode)
        readme = REPO / "docs" / "terminal-mode.md"
        if readme.is_file():
            _add(tar, readme, "docs/terminal-mode.md")
        _add_bytes(tar, "VERSION", (version() + "\n").encode())
        for source, arc in TOOL_TREES:
            tar.add(REPO / source, arcname=arc, filter=_reset_meta)

    # gzip with mtime=0 so two builds of the same source are byte-identical.
    with archive.open("wb") as handle, raw_path.open("rb") as src:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as gz:
            shutil.copyfileobj(src, gz, length=1024 * 1024)
    raw_path.unlink(missing_ok=True)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = out_dir / "UpdaterCE-Lite.tar.gz.sha256"
    checksum.write_text(f"{digest}  UpdaterCE-Lite.tar.gz\n", encoding="utf-8")

    if site_dir is not None:
        installer = REPO / "run_linux_lite.sh"
        shutil.copy2(installer, site_dir / "run_linux_lite.sh")

    return archive, checksum


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mode = 0o644
    info.mtime = 0
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=REPO / "dist" / "linux-lite")
    parser.add_argument("--site-dir", type=Path, default=None,
                        help="website checkout to copy run_linux_lite.sh into")
    args = parser.parse_args(argv)

    archive, checksum = build(args.out.resolve(), args.site_dir.resolve() if args.site_dir else None)
    size = archive.stat().st_size / 1024
    print(f"lite payload: {archive} ({size:.0f} KB, {version()})")
    print(f"checksum:     {checksum.read_text(encoding='utf-8').split()[0]}")
    if args.site_dir:
        print(f"installer:    {args.site_dir / 'run_linux_lite.sh'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
