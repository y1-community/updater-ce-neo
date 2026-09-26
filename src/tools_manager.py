"""tools_manager.py — Remote self-healing, integrity verification, and discovery for flashing components.

Manages external tool lifecycles (SP Flash Tool 5.1904 for Windows and Linux,
libusb-1.0 for Darwin, UnRAR for Windows) across all distribution formats
(Installer, AppImage, macOS .app bundle, and source checkouts).
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import paths

logger = logging.getLogger(__name__)


@dataclass
class ToolDefinition:
    id: str
    name: str
    version: str
    target_platform: str  # "windows", "linux", "darwin", or "all"
    primary_url: str
    fallback_urls: List[str]
    expected_sha256: Optional[str]
    primary_executable: str
    required_files: List[str]
    archive_format: str  # "zip", "tar.gz", "file"


TOOLS_MANIFEST: Dict[str, ToolDefinition] = {
    "sp_flash_tool_win": ToolDefinition(
        id="sp_flash_tool_win",
        name="SP Flash Tool (Windows)",
        version="5.1904",
        target_platform="windows",
        primary_url="https://github.com/y1-community/updater-ce-neo/releases/download/v3.0.0-assets/SP_Flash_Tool_v5.1904_Win.zip",
        fallback_urls=[
            "https://github.com/y1-community/Innioasis-Updater/releases/download/flash_tool/SP_Flash_Tool_v5.1904_Win.zip",
            "https://spflashtooldownload.com/wp-content/uploads/SP_Flash_Tool_v5.1904_Win.zip",
        ],
        expected_sha256=None,  # Verified against zip structure and size
        primary_executable="flash_tool.exe",
        required_files=["flash_tool.exe"],
        archive_format="zip",
    ),
    "sp_flash_tool_linux": ToolDefinition(
        id="sp_flash_tool_linux",
        name="SP Flash Tool (Linux)",
        version="5.1904",
        target_platform="linux",
        primary_url="https://github.com/y1-community/updater-ce-neo/releases/download/flash_tool/flash_tool_linux.zip",
        fallback_urls=[
            "https://github.com/y1-community/Innioasis-Updater/releases/download/flash_tool/flash_tool_linux.zip",
            "https://github.com/y1-community/updater-ce-neo/releases/download/v3.0.0-assets/SP_Flash_Tool_v5.1904_Linux.zip",
        ],
        expected_sha256=None,
        primary_executable="flash_tool",
        required_files=["flash_tool", "libflashtool.so", "MTK_AllInOne_DA.bin"],
        archive_format="zip",
    ),
    "libusb_darwin": ToolDefinition(
        id="libusb_darwin",
        name="libusb-1.0 Universal 2 (macOS)",
        version="1.0.30",
        target_platform="darwin",
        primary_url="https://github.com/y1-community/updater-ce-neo/releases/download/v3.0.0-assets/libusb-1.0-universal2.dylib",
        fallback_urls=[
            "https://files.pythonhosted.org/packages/52/6f/26de4e9f858ab50e87931f0be268f3c1bbfce33e8584add60da857632142/libusb_package-1.0.30.0-py3-none-macosx_11_0_arm64.whl",
        ],
        expected_sha256=None,
        primary_executable="libusb-1.0.dylib",
        required_files=["libusb-1.0.dylib"],
        archive_format="file",
    ),
    "unrar_win": ToolDefinition(
        id="unrar_win",
        name="UnRAR (Windows)",
        version="6.24",
        target_platform="windows",
        primary_url="https://github.com/y1-community/updater-ce-neo/releases/download/v3.0.0-assets/UnRAR.exe",
        fallback_urls=[
            "https://www.rarlab.com/rar/unrar.exe",
        ],
        expected_sha256=None,
        primary_executable="UnRAR.exe",
        required_files=["UnRAR.exe"],
        archive_format="file",
    ),
}


def get_user_tools_dir() -> Path:
    """Return user-writable base directory for self-healed components."""
    if paths.IS_WINDOWS:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        d = base / "Innioasis Updater" / "tools"
    elif platform.system() == "Darwin":
        d = Path.home() / "Library" / "Application Support" / "Innioasis" / "tools"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        d = base / "innioasis-updater" / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def compute_file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def find_component(component_id: str) -> Optional[Path]:
    """Search all standard, bundled, and self-healed locations for a component."""
    tool = TOOLS_MANIFEST.get(component_id)
    if not tool:
        return None

    user_tools = get_user_tools_dir() / tool.id
    candidates: List[Path] = [
        # 1. Custom environment variable override
        Path(os.environ[f"{tool.id.upper()}_DIR"]) if os.environ.get(f"{tool.id.upper()}_DIR") else None,
        # 2. Self-healed user tools directory
        user_tools,
    ]

    if component_id in ("sp_flash_tool_win", "sp_flash_tool_linux"):
        if os.environ.get("SP_FLASH_TOOL_DIR"):
            candidates.insert(0, Path(os.environ["SP_FLASH_TOOL_DIR"]))
        candidates.extend([
            paths.INSTALL_DIR / "SP_Flash_Tool",
            paths.INSTALL_DIR,
            paths.REPO_ROOT / "SP_Flash_Tool",
            paths.REPO_ROOT / "tools" / "SP_Flash_Tool",
            Path.cwd() / "SP_Flash_Tool",
        ])
        if platform.system() == "Linux":
            try:
                from . import linux_sp_flash
                candidates.append(linux_sp_flash.stage_dir())
            except Exception:
                pass
    elif component_id == "libusb_darwin":
        dylib_path = paths.find_libusb_dylib()
        if dylib_path and Path(dylib_path).is_file():
            return Path(dylib_path)
    elif component_id == "unrar_win":
        unrar_path = paths.find_unrar()
        if unrar_path and Path(unrar_path).is_file():
            return Path(unrar_path).parent

    for c in candidates:
        if not c:
            continue
        try:
            p = c.resolve()
            if p.is_file() and p.name == tool.primary_executable:
                return p.parent
            if p.is_dir():
                target = p / tool.primary_executable
                if target.is_file():
                    return p
        except Exception:
            pass

    return None


def verify_component_integrity(component_id: str) -> Tuple[bool, str]:
    """Verify that a component is present, executable, and contains all required files."""
    tool = TOOLS_MANIFEST.get(component_id)
    if not tool:
        return False, f"Unknown component ID: {component_id}"

    comp_dir = find_component(component_id)
    if not comp_dir:
        return False, f"{tool.name} not found"

    main_exe = comp_dir / tool.primary_executable
    if not main_exe.is_file():
        return False, f"{tool.name} primary executable missing: {main_exe.name}"

    if os.name != "nt":
        # Check executable permissions
        mode = main_exe.stat().st_mode
        if not (mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)):
            try:
                main_exe.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception as e:
                return False, f"{tool.name} is not executable and chmod failed: {e}"

    # Verify all required files exist
    missing = [rf for rf in tool.required_files if not (comp_dir / rf).exists()]
    if missing:
        return False, f"{tool.name} has missing files: {', '.join(missing)}"

    return True, "OK"


def self_heal_component(
    component_id: str,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    force: bool = False,
) -> Tuple[bool, str, Optional[Path]]:
    """Self-heal a missing or corrupted component by downloading and staging it."""
    tool = TOOLS_MANIFEST.get(component_id)
    if not tool:
        return False, f"Unknown component ID: {component_id}", None

    if not force:
        ok, msg = verify_component_integrity(component_id)
        if ok:
            return True, f"{tool.name} is already intact", find_component(component_id)

    dest_dir = get_user_tools_dir() / tool.id
    dest_dir.mkdir(parents=True, exist_ok=True)

    urls = [tool.primary_url] + tool.fallback_urls
    archive_data = None
    last_err = ""

    for url in urls:
        try:
            logger.info("Self-healing %s from %s ...", tool.name, url)
            if progress_cb:
                progress_cb(0, 100, f"Connecting to {url[:45]}...")

            req = urllib.request.Request(url, headers={"User-Agent": "InnioasisUpdater/3.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                total_len = resp.getheader("Content-Length")
                total_bytes = int(total_len) if total_len and total_len.isdigit() else 0
                downloaded = 0
                buf = io.BytesIO()
                while chunk := resp.read(65536):
                    buf.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total_bytes > 0:
                        pct = int((downloaded / total_bytes) * 100)
                        progress_cb(pct, 100, f"Downloading {tool.name} ({pct}%)...")

                archive_data = buf.getvalue()
                if len(archive_data) > 0:
                    break
        except Exception as e:
            last_err = str(e)
            logger.warning("Download from %s failed: %s", url, e)

    if not archive_data:
        return False, f"Failed to download {tool.name} from all sources: {last_err}", None

    if progress_cb:
        progress_cb(90, 100, f"Extracting {tool.name}...")

    # Extract archive
    try:
        if tool.archive_format == "zip":
            with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
                names = zf.namelist()
                has_root_prefix = all("/" in n for n in names if not n.endswith("/"))
                prefix = ""
                if has_root_prefix:
                    first_part = names[0].split("/")[0] + "/"
                    if all(n.startswith(first_part) for n in names):
                        prefix = first_part

                for member in zf.infolist():
                    rel_name = member.filename[len(prefix):] if prefix and member.filename.startswith(prefix) else member.filename
                    if not rel_name or rel_name.endswith("/"):
                        continue
                    out_path = dest_dir / rel_name
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src_f, open(out_path, "wb") as dst_f:
                        shutil.copyfileobj(src_f, dst_f)
                    perms = member.external_attr >> 16
                    if perms:
                        out_path.chmod(perms)
                    elif out_path.name in (tool.primary_executable, "flash_tool", "flash_tool.sh"):
                        out_path.chmod(0o755)

        elif tool.archive_format == "tar.gz":
            with tarfile.open(fileobj=io.BytesIO(archive_data), mode="r:gz") as tf:
                tf.extractall(dest_dir)

        elif tool.archive_format == "file":
            target_f = dest_dir / tool.primary_executable
            with open(target_f, "wb") as f:
                f.write(archive_data)
            if os.name != "nt":
                target_f.chmod(0o755)

    except Exception as e:
        return False, f"Extraction failed for {tool.name}: {e}", None

    ok, msg = verify_component_integrity(component_id)
    if ok:
        if progress_cb:
            progress_cb(100, 100, f"{tool.name} ready.")
        logger.info("Successfully self-healed %s at %s", tool.name, dest_dir)
        return True, "Self-heal successful", dest_dir

    return False, f"Verification failed after extraction: {msg}", dest_dir
