"""Provision the console tools a terminal session needs, only when it needs them.

A streamed lite session on a Pi Zero must not pull 200 MB before it can show a
menu, while an AppImage already contains SP Flash Tool, adb and unrar. The
resolution order is therefore:

1. a copy inside this session (AppImage payload, extracted tarball/zip, source
   checkout, or an earlier download) — nothing is fetched;
2. otherwise the component's published archive, with progress and resumable
   download, unpacked into the per-user component folder;
3. if the link is faster than 60 Mbps, the TUI prefetches the session's tools
   instead of waiting for the first task that needs one.

The URLs are the same ones the window build already uses (its release assets
and Google's platform-tools), so a terminal session does not depend on a new
hosting arrangement that has not been published yet.

Only the standard library is used: this module runs in the lite payload and on
Windows/macOS as well as Linux.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .download import download, human_bytes

PREFETCH_MBPS = 60.0
SPEED_SAMPLE_URL = "https://dl.google.com/android/repository/repository2-1.xml"

SPFLASH_LINUX_URLS = (
    "https://github.com/y1-community/updater-ce-neo/releases/download/flash_tool/flash_tool_linux.zip",
    "https://github.com/y1-community/Innioasis-Updater/releases/download/flash_tool/flash_tool_linux.zip",
    "https://github.com/y1-community/updater-ce-neo/releases/download/v3.0.0-assets/SP_Flash_Tool_v5.1904_Linux.zip",
)


def platform_key() -> str:
    system = {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(sys.platform, sys.platform)
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    elif machine in ("armv7l", "armv6l", "arm"):
        arch = "armhf"
    elif machine in ("i386", "i686", "x86"):
        arch = "i686"
    else:
        arch = machine or "unknown"
    return f"{system}-{arch}"


def component_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "innioasis-updater" / "components"


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    description: str
    names: tuple[str, ...]
    urls: dict[str, tuple[str, ...]] = field(default_factory=dict)
    archive: str = "zip"
    executable: str = ""
    manual_hint: str = ""

    def platform_urls(self) -> tuple[str, ...]:
        return self.urls.get(platform_key(), ())


COMPONENTS: dict[str, Component] = {
    "adb": Component(
        key="adb",
        label="adb",
        description="Android platform tools, used to sideload and inspect the player.",
        names=("adb", "adb.exe"),
        urls={
            "linux-x86_64": ("https://dl.google.com/android/repository/platform-tools-latest-linux.zip",),
            "linux-i686": ("https://dl.google.com/android/repository/platform-tools-latest-linux.zip",),
            "macos-x86_64": ("https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",),
            "macos-arm64": ("https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",),
            "windows-x86_64": ("https://dl.google.com/android/repository/platform-tools-latest-windows.zip",),
        },
        executable="adb",
        manual_hint="Install android-tools with your package manager.",
    ),
    "spflash": Component(
        key="spflash",
        label="SP Flash Tool",
        description="MediaTek's flashing tool (GUI); MTKClient is the console engine.",
        names=("flash_tool", "SP_Flash_Tool", "flash_tool.exe"),
        urls={"linux-x86_64": SPFLASH_LINUX_URLS},
        executable="flash_tool",
        manual_hint="SP Flash Tool is x86_64 Linux/Windows only; use MTKClient here.",
    ),
    "unrar": Component(
        key="unrar",
        label="unrar",
        description="Extracts firmware published as .rar archives.",
        names=("unrar", "unrar.exe"),
        manual_hint=(
            "Install unrar or 7z: Debian/Ubuntu/Mint sudo apt install unrar-free, "
            "Fedora sudo dnf install unrar, Arch sudo pacman -S unrar, "
            "openSUSE sudo zypper install unrar."
        ),
    ),
}


def _payload_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parents[2]]
    env = os.environ.get("INNIOASIS_TOOL_DIR")
    if env:
        roots.append(Path(env))
    roots.append(Path(sys.executable).resolve().parent)
    appdir = os.environ.get("APPDIR")
    if appdir:
        roots += [Path(appdir) / "usr" / "bin", Path(appdir) / "usr" / "lib"]
    roots.append(Path(__file__).resolve().parents[2] / "tools")
    return roots


def find_bundled(component: Component) -> Path | None:
    for root in _payload_roots():
        for name in component.names:
            for candidate in (root / name, root / "tools" / name, root / "SP_Flash_Tool" / name):
                if candidate.exists():
                    return candidate
    for name in component.names:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def find_cached(component: Component) -> Path | None:
    base = component_dir()
    for name in component.names:
        candidate = base / name
        if candidate.exists():
            return candidate
    for sub in base.glob(f"{component.key}*/**/" + (component.executable or component.names[0])):
        if sub.exists():
            return sub
    return None


def resolve(component: Component) -> Path | None:
    return find_bundled(component) or find_cached(component)


def _extract(component: Component, archive: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    root = target.resolve()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                if not str((target / member.filename).resolve()).startswith(str(root)):
                    raise RuntimeError(f"unsafe path in {archive.name}: {member.filename}")
            bundle.extractall(target)
    elif name.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                if not str((target / member.name).resolve()).startswith(str(root)):
                    raise RuntimeError(f"unsafe path in {archive.name}: {member.name}")
            bundle.extractall(target)
    else:
        destination = component_dir() / (component.names[0])
        shutil.copy2(archive, destination)
        destination.chmod(0o755)
        return destination

    wanted = component.executable or component.names[0]
    candidates = sorted(target.rglob(wanted)) + sorted(target.rglob(wanted + ".exe"))
    for candidate in candidates:
        if candidate.is_file():
            candidate.chmod(candidate.stat().st_mode | 0o111)
            return candidate
    raise RuntimeError(f"{archive.name} did not contain {wanted}")


def ensure(
    key: str,
    progress: Callable[[int, int | None], None] | None = None,
    verbose: bool = True,
) -> Path:
    component = COMPONENTS[key]
    existing = resolve(component)
    if existing:
        if verbose:
            print(f"{component.label}: {existing}")
        return existing

    urls = component.platform_urls()
    if not urls:
        raise RuntimeError(
            f"{component.label} is not available for {platform_key()}. {component.manual_hint}"
        )

    workdir = Path(tempfile.mkdtemp(prefix=f"innioasis-{key}-"))
    errors: list[str] = []
    try:
        for url in urls:
            asset = url.rsplit("/", 1)[-1] or f"{key}.zip"
            target = workdir / asset
            if verbose:
                print(f"{component.label}: fetching {asset}")
            try:
                download(url, target, progress=progress or _simple_progress(component.label),
                         timeout=120)
                return _extract(component, target, component_dir() / key)
            except (urllib.error.URLError, OSError, RuntimeError, zipfile.BadZipFile) as exc:
                errors.append(f"{url}: {exc}")
                target.unlink(missing_ok=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    raise RuntimeError(
        f"could not fetch {component.label}. {component.manual_hint}\n  " + "\n  ".join(errors)
    )


def _simple_progress(label: str):
    def render(done: int, total: int | None) -> None:
        if total:
            sys.stderr.write(f"\r{label}: {done * 100 // max(total, 1)}%  {human_bytes(done)} / {human_bytes(total)}")
        else:
            sys.stderr.write(f"\r{label}: {human_bytes(done)}")
        if total and done >= total:
            sys.stderr.write("\n")
        sys.stderr.flush()

    return render


def status() -> list[dict]:
    rows = []
    for key, component in COMPONENTS.items():
        path = resolve(component)
        rows.append({
            "key": key,
            "label": component.label,
            "path": str(path) if path else "",
            "available": bool(path),
            "description": component.description,
            "for_platform": bool(component.platform_urls()) or bool(path),
        })
    return rows


def measure_mbps(timeout: float = 8.0, sample_bytes: int = 1_000_000) -> float:
    """Rough link speed so prefetching is only done when it is cheap."""
    request = urllib.request.Request(
        SPEED_SAMPLE_URL, headers={"User-Agent": "UpdaterCE-lite", "Range": f"bytes=0-{sample_bytes - 1}"}
    )
    start = time.monotonic()
    got = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            while got < sample_bytes:
                block = response.read(262144)
                if not block:
                    break
                got += len(block)
    except (urllib.error.URLError, OSError):
        return 0.0
    elapsed = max(time.monotonic() - start, 0.001)
    return got * 8 / elapsed / 1_000_000


def prefetch(keys: Iterable[str] | None = None, threshold_mbps: float = PREFETCH_MBPS) -> dict[str, str]:
    estimate = measure_mbps()
    if estimate < threshold_mbps:
        return {"skipped": f"{estimate:.1f} Mbps < {threshold_mbps:.0f} Mbps"}
    fetched: dict[str, str] = {}
    for key in keys or tuple(COMPONENTS):
        try:
            fetched[key] = str(ensure(key))
        except RuntimeError as exc:
            fetched[key] = f"unavailable: {exc}"
    return fetched


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Show or fetch the terminal tools.")
    parser.add_argument("action", nargs="?", choices=("list", "fetch", "prefetch"), default="list")
    parser.add_argument("keys", nargs="*")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.action == "list":
        rows = status()
        if args.json:
            import json

            print(json.dumps(rows, indent=2))
        else:
            for row in rows:
                print(f"{row['label']:<14} {row['path'] or 'not installed'}")
        return 0
    if args.action == "prefetch":
        print(prefetch(args.keys or None))
        return 0
    for key in args.keys or tuple(COMPONENTS):
        print(ensure(key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
