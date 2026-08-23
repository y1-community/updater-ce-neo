"""Linux SP Flash Tool staging (ported from Updater CE's ``ensure_linux_sp_flash_tool``).

SP Flash Tool is MediaTek-proprietary and only ships as a Windows exe in the
original InniUpdaterChin payload, but the community publishes a Linux console
build. This module downloads and stages that package so the utility can use
SP Flash Tool as the install method on Linux (x86/x86_64) — with mtkclient as
the automatic fallback when staging is impossible (unsupported arch, offline).
"""

import logging
import os
import platform
import shutil
import zipfile
from pathlib import Path

import requests

from .config import GITHUB_API

logger = logging.getLogger(__name__)

FLASH_TOOL_LINUX_URL = (
    "https://github.com/y1-community/Innioasis-Updater/releases/download/flash_tool/flash_tool_linux.zip"
)
FLASH_TOOL_LINUX_ZIP_NAME = "flash_tool_linux.zip"
FLASH_TOOL_LINUX_BIN = "flash_tool"
FLASH_TOOL_LINUX_ZIP_MIN_BYTES = 10 * 1024 * 1024  # ~67 MB full package

# Required members of flash_tool_linux.zip (relative to the stage dir). The
# binary alone is not enough: Qt4 and DA libs ship under lib/ and the package
# root. Keep in sync with the release layout (unzip -l flash_tool_linux.zip).
FLASH_TOOL_LINUX_REQUIRED_FILES = (
    "flash_tool",
    "flash_tool.sh",
    "libflashtool.so",
    "libflashtool.v1.so",
    "libflashtoolEx.so",
    "libsla_challenge.so",
    "MTK_AllInOne_DA.bin",
    "lib/libQtCore.so.4",
    "lib/libQtGui.so.4",
    "lib/libQtNetwork.so.4",
    "lib/libQtWebKit.so.4",
    "lib/libQtXml.so.4",
    "lib/libQtXmlPatterns.so.4",
    "lib/libQtSql.so.4",
    "lib/libQtHelp.so.4",
    "lib/libQtCLucene.so.4",
    "lib/libphonon.so.4",
    "plugins/imageformats/libqjpeg.so",
    "plugins/sqldrivers/libqsqlite.so",
)

LOG_DIR_NAME = "SP_FT_Logs"

# Where the staged package lives (per-user cache, like CE's app dir pattern).
STAGE_SUBDIR = "linux_flash_tool"


def _app_cache_dir():
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys_platform := platform.system() == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "innioasis-updater"


def stage_dir() -> Path:
    d = _app_cache_dir() / STAGE_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def zip_cache_path() -> Path:
    return _app_cache_dir() / FLASH_TOOL_LINUX_ZIP_NAME


def arch_supported() -> bool:
    """SP Flash Tool Linux builds are x86/x86_64 only (like CE)."""
    machine = (platform.machine() or "").lower()
    if machine in ("x86_64", "amd64", "x64", "i386", "i486", "i586", "i686", "x86"):
        return True
    return False


def unsupported_reason() -> str:
    machine = platform.machine() or "unknown"
    return (
        f"SP Flash Tool is only available on x86 / x86_64 Linux "
        f"(this system is {machine}). Installs use MTKClient instead."
    )


def missing_files(stage: Path) -> list:
    missing = []
    for rel in FLASH_TOOL_LINUX_REQUIRED_FILES:
        path = stage / rel
        try:
            if not path.is_file() or path.stat().st_size <= 0:
                missing.append(rel)
        except OSError:
            missing.append(rel)
    return missing


def files_ready(stage: Path) -> bool:
    return not missing_files(stage)


def zip_has_required_members(zip_path: Path) -> bool:
    try:
        if not zip_path.is_file() or zip_path.stat().st_size < FLASH_TOOL_LINUX_ZIP_MIN_BYTES:
            return False
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = set()
            for name in zf.namelist():
                n = name.lstrip("./")
                if n.endswith("/"):
                    continue
                names.add(n)
        return all(rel in names for rel in FLASH_TOOL_LINUX_REQUIRED_FILES)
    except Exception as e:
        logger.debug("flash_tool_linux.zip check failed: %s", e)
        return False


def _download_zip(zip_path: Path, progress_cb=None) -> bool:
    try:
        headers = {"Accept": "application/vnd.github+json"}
        resp = requests.get(FLASH_TOOL_LINUX_URL, stream=True, timeout=30, headers=headers)
        if resp.status_code == 302:
            redirect_url = resp.headers.get("Location")
            if redirect_url:
                resp = requests.get(redirect_url, stream=True, timeout=30)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total:
                        progress_cb(min(99, int(downloaded * 100 / total)))
        if progress_cb:
            progress_cb(100)
        return zip_has_required_members(zip_path)
    except Exception as e:
        logger.error("flash_tool_linux.zip download failed: %s", e)
        return False


def _extract_zip(zip_path: Path, stage: Path) -> bool:
    try:
        shutil.rmtree(stage, ignore_errors=True)
        stage.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(stage)
        return files_ready(stage)
    except Exception as e:
        logger.error("flash_tool_linux.zip extract failed: %s", e)
        return False


def make_executable(stage: Path):
    for name in (FLASH_TOOL_LINUX_BIN, "flash_tool.sh"):
        path = stage / name
        if path.is_file():
            try:
                path.chmod(path.stat().st_mode | 0o111)
            except Exception:
                pass


def process_env(stage: Path) -> dict:
    """Environment for launching the Linux flash_tool (bundled Qt4 + plugins)."""
    env = os.environ.copy()
    lib_dirs = [str(stage), str(stage / "lib")]
    existing = env.get("LD_LIBRARY_PATH", "").strip()
    if existing:
        lib_dirs.append(existing)
    env["LD_LIBRARY_PATH"] = ":".join(lib_dirs)
    plugins = stage / "plugins"
    if plugins.is_dir():
        env["QT_PLUGIN_PATH"] = str(plugins)
    env.setdefault("QT_QPA_PLATFORM", "xcb")
    return env


def ensure_linux_sp_flash_tool(progress_cb=None, force_download=False) -> tuple:
    """Ensure the Linux SP Flash Tool package is staged and complete.

    Returns ``(ok, message)``. Package problems are hard failures; the caller
    falls back to mtkclient when ``ok`` is False.
    """
    if os.name == "nt" or platform.system() != "Linux":
        return True, "SP Flash Tool bootstrap is only required on Linux"
    if not arch_supported():
        return False, unsupported_reason()

    stage = stage_dir()
    zip_path = zip_cache_path()
    try:
        missing = missing_files(stage)
        need_fetch = force_download or bool(missing)
        if need_fetch:
            if progress_cb:
                progress_cb(2, f"Preparing SP Flash Tool for Linux ({len(missing)} missing file(s))…")
            if zip_has_required_members(zip_path):
                if progress_cb:
                    progress_cb(30, "Extracting cached flash_tool_linux.zip…")
                if not _extract_zip(zip_path, stage):
                    return False, "Staged flash_tool_linux.zip is incomplete."
            else:
                if progress_cb:
                    progress_cb(5, f"Downloading {FLASH_TOOL_LINUX_ZIP_NAME} (~67 MB)…")
                if not _download_zip(zip_path, progress_cb=lambda p: progress_cb(5 + int(p * 0.5), None) if progress_cb else None):
                    return False, (
                        "Could not download SP Flash Tool for Linux. Check your internet "
                        "connection and try again, or use the MTKClient method."
                    )
                if progress_cb:
                    progress_cb(60, "Extracting SP Flash Tool…")
                if not _extract_zip(zip_path, stage):
                    return False, "Extracted flash_tool_linux.zip is incomplete."
        make_executable(stage)
        still = missing_files(stage)
        if still:
            return False, "SP Flash Tool package is incomplete. Missing: " + ", ".join(still[:10])
        (stage / LOG_DIR_NAME).mkdir(parents=True, exist_ok=True)
        if progress_cb:
            progress_cb(100, "SP Flash Tool is ready.")
        return True, f"SP Flash Tool staged at {stage}"
    except Exception as e:
        logger.exception("Linux SP Flash Tool setup failed")
        return False, str(e)
