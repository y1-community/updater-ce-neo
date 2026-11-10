"""Resumable downloads and archive extraction without third-party modules.

Slow links are the point of this module: a download that dies at 80 % on a
Raspberry Pi's SD card must continue with a Range request, not start again.
Progress is reported through a callback so both the plain CLI and the TUI can
render it their own way.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, Optional

USER_AGENT = "UpdaterCE-lite"
CHUNK = 1 << 20

Progress = Callable[[int, Optional[int]], None]


def human_bytes(value: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def sha256_file(path: Path, progress: Progress | None = None) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(CHUNK)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress:
                progress(done, total)
    return digest.hexdigest()


def download(
    url: str,
    dest: Path,
    progress: Progress | None = None,
    resume: bool = True,
    timeout: float = 60.0,
    expected_sha256: str | None = None,
) -> Path:
    """Download ``url`` to ``dest``, continuing a partial ``.part`` file."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    offset = 0
    if resume and part.exists():
        offset = part.stat().st_size

    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and offset:
            part.unlink(missing_ok=True)
            return download(url, dest, progress, resume=False, timeout=timeout,
                            expected_sha256=expected_sha256)
        raise

    status = getattr(response, "status", 200)
    if status != 206:
        offset = 0
    total = None
    content_range = response.headers.get("Content-Range") or ""
    if "/" in content_range:
        tail = content_range.rsplit("/", 1)[-1].strip()
        if tail.isdigit():
            total = int(tail)
    elif response.headers.get("Content-Length", "").isdigit():
        total = int(response.headers["Content-Length"]) + offset

    mode = "ab" if offset else "wb"
    if progress:
        progress(offset, total)
    with part.open(mode) as handle:
        while True:
            block = response.read(CHUNK)
            if not block:
                break
            handle.write(block)
            offset += len(block)
            if progress:
                progress(offset, total)
    part.replace(dest)

    if expected_sha256:
        actual = sha256_file(dest)
        if actual.lower() != expected_sha256.lower().strip():
            dest.unlink(missing_ok=True)
            raise RuntimeError(
                f"checksum mismatch for {dest.name}: expected {expected_sha256}, got {actual}"
            )
    return dest


def safe_extract_zip(archive: Path, dest: Path) -> list[Path]:
    written: list[Path] = []
    root = dest.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            target = (dest / member.filename).resolve()
            if not str(target).startswith(str(root)):
                raise RuntimeError(f"unsafe path in {archive.name}: {member.filename}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(member) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink)
            written.append(target)
    return written


def safe_extract_tar(archive: Path, dest: Path) -> list[Path]:
    written: list[Path] = []
    root = dest.resolve()
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(root)):
                raise RuntimeError(f"unsafe path in {archive.name}: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = bundle.extractfile(member)
            if extracted is None:
                continue
            with extracted, target.open("wb") as sink:
                shutil.copyfileobj(extracted, sink)
            written.append(target)
    return written


def _rar_tool() -> list[str] | None:
    for name, args in (("unrar", ["x", "-o+"]), ("unar", ["-o"]), ("7z", ["x", "-y"]), ("7za", ["x", "-y"])):
        path = shutil.which(name)
        if path:
            return [path] + args
    return None


def extract(archive: Path, dest: Path) -> list[Path]:
    """Unpack a ROM archive. Returns the files written."""
    archive = Path(archive)
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        return safe_extract_zip(archive, dest)
    if name.endswith((".tar.gz", ".tgz", ".tar")):
        return safe_extract_tar(archive, dest)
    if name.endswith(".rar"):
        tool = _rar_tool()
        if not tool:
            raise RuntimeError(
                "this firmware is a .rar archive and no extractor is installed; "
                "install unrar or 7z (the lite installer lists the command for your distro)"
            )
        result = subprocess.run(tool + [str(archive), str(dest) + os.sep])
        if result.returncode != 0:
            raise RuntimeError(f"{Path(tool[0]).name} could not unpack {archive.name}")
        return [path for path in dest.rglob("*") if path.is_file()]
    raise RuntimeError(f"unsupported archive: {archive.name}")


def find_scatter(folder: Path) -> Path | None:
    """The scatter file a MediaTek flash needs, or None."""
    for pattern in ("*scatter*.txt", "*scatter*", "MT*.txt"):
        for candidate in sorted(folder.rglob(pattern)):
            if candidate.is_file():
                return candidate
    return None


def report_progress(prefix: str = "downloading") -> Progress:
    last = {"pct": -1}

    def render(done: int, total: int | None) -> None:
        if total:
            pct = int(done * 100 / total)
            if pct == last["pct"]:
                return
            last["pct"] = pct
            line = f"\r{prefix}: {pct:3d}%  {human_bytes(done)} / {human_bytes(total)}"
        else:
            line = f"\r{prefix}: {human_bytes(done)}"
        sys.stderr.write(line)
        sys.stderr.flush()
        if total and done >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return render
