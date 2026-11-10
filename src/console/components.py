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

    def platform_urls(self, platform: str | None = None) -> tuple[str, ...]:
        return self.urls.get(platform or platform_key(), ())


COMPONENTS: dict[str, Component] = {
    "adb": Component(
        key="adb",
        label="adb",
        description="Android platform tools, used to sideload and inspect the player.",
        names=("adb", "adb.exe"),
        urls={
            # Official platform-tools zips are 64-bit. 32-bit Intel and 32-bit
            # ARM must not be handed the x86_64 binary.
            "linux-x86_64": ("https://dl.google.com/android/repository/platform-tools-latest-linux.zip",),
            "macos-x86_64": ("https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",),
            "macos-arm64": ("https://dl.google.com/android/repository/platform-tools-latest-darwin.zip",),
            "windows-x86_64": ("https://dl.google.com/android/repository/platform-tools-latest-windows.zip",),
        },
        executable="adb",
        manual_hint=(
            "32-bit Linux has no official platform-tools build here. "
            "Debian/Ubuntu/Mint: sudo apt install adb. "
            "Fedora: sudo dnf install android-tools. "
            "Arch: sudo pacman -S android-tools. "
            "Or place a binary built for this architecture on PATH."
        ),
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


# Directory names under tools/adb/. The x86_64 tree is never a match for
# i686 or 32-bit ARM, even when that file happens to sit in the same checkout.
VENDOR_DIRS = {
    "linux-x86_64": "linux-x64",
    "linux-arm64": "linux-arm64",
    "macos-x86_64": "macos-x64",
    "macos-arm64": "macos-arm64",
    "windows-x86_64": "windows-x64",
}
MIN_TOOL_BYTES = 100_000


def _large_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= MIN_TOOL_BYTES
    except OSError:
        return False


def find_bundled(component: Component, roots: Iterable[Path] | None = None, platform: str | None = None) -> Path | None:
    platform = platform or platform_key()
    if component.key == "spflash" and platform != "linux-x86_64":
        return None
    search = list(roots) if roots is not None else _payload_roots()
    if component.key == "adb":
        vendor = VENDOR_DIRS.get(platform)
        exe = "adb.exe" if platform.startswith("windows") else "adb"
        if vendor:
            for root in search:
                for candidate in (
                    root / "tools" / "adb" / vendor / exe,
                    root / "adb" / vendor / exe,
                ):
                    if _large_file(candidate):
                        return candidate
        if roots is None:
            found = shutil.which(exe)
            if found and _large_file(Path(found)):
                return Path(found)
        return None
    if component.key == "spflash":
        for root in search:
            for relative in ("flash_tool", "SP_Flash_Tool/flash_tool", "linux_flash_tool/flash_tool"):
                candidate = root / relative
                if _large_file(candidate):
                    return candidate
        return None
    for root in search:
        for name in component.names:
            for candidate in (root / name, root / "tools" / name, root / "SP_Flash_Tool" / name):
                if candidate.exists() and candidate.is_file():
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


def resolve(component: Component, roots: Iterable[Path] | None = None, platform: str | None = None) -> Path | None:
    platform = platform or platform_key()
    if component.key == "spflash" and platform != "linux-x86_64":
        return None
    return find_bundled(component, roots=roots, platform=platform) or find_cached(component)


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
    roots: Iterable[Path] | None = None,
    platform: str | None = None,
    downloader: Callable[..., Path] | None = None,
) -> Path:
    component = COMPONENTS[key]
    existing = resolve(component, roots=roots, platform=platform)
    if existing:
        if verbose:
            print(f"{component.label}: {existing}")
        return existing

    urls = component.platform_urls(platform)
    if not urls:
        raise RuntimeError(
            f"{component.label} is not available for {platform or platform_key()}. {component.manual_hint}"
        )
    if downloader is not None:
        return downloader(key)

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


def measure_mbps(timeout: float = 8.0, sample_bytes: int = 1_000_000) -> float | None:
    """Rough link speed in decimal megabits (bits/second/1e6).

    ``None`` means the probe failed. Callers treat that the same as a slow
    link: nothing is prefetched.
    """
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
        return None
    if got <= 0:
        return None
    elapsed = max(time.monotonic() - start, 0.001)
    return got * 8 / elapsed / 1_000_000


def session_component_keys(platform: str | None = None, headless: bool = False) -> list[str]:
    """Tools this temporary session may need, and only those this arch can use.

    SP Flash Tool is the Linux x86_64 build. A headless machine and every
    other architecture leave it out and stay on MTKclient. adb is included
    when a matching vendored tree or a same-arch download exists.
    """
    platform = platform or platform_key()
    keys: list[str] = []
    if platform == "linux-x86_64" and not headless:
        keys.append("spflash")
    if platform in COMPONENTS["adb"].urls or platform in VENDOR_DIRS:
        keys.append("adb")
    return keys


def prefetch_plan(mbps: float | None, needed: Iterable[str], present: Iterable[str]) -> list[str]:
    """Components to fetch before the menu.

    Above 60 Mbps the missing session tools are fetched. At 60, below 60, or
    when the probe failed, the list is empty and each tool waits for its step.
    """
    if mbps is None or mbps <= PREFETCH_MBPS:
        return []
    have = set(present)
    return [name for name in needed if name not in have]


def prefetch(keys: Iterable[str] | None = None, threshold_mbps: float = PREFETCH_MBPS) -> dict[str, str]:
    estimate = measure_mbps()
    needed = list(keys) if keys is not None else session_component_keys()
    present = [key for key in needed if key in COMPONENTS and resolve(COMPONENTS[key])]
    plan = prefetch_plan(estimate, needed, present)
    if not plan:
        why = "probe failed" if estimate is None else f"{estimate:.1f} Mbps"
        return {"skipped": why, "plan": []}
    fetched: dict[str, str] = {}
    for key in plan:
        try:
            fetched[key] = str(ensure(key))
        except RuntimeError as exc:
            fetched[key] = f"unavailable: {exc}"
    fetched["plan"] = ",".join(plan)
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
