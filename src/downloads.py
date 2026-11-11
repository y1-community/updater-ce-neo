"""Streaming firmware download worker with automatic resume, retry, and progress tracking."""

import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from .i18n import tr
from .network import requests_module

logger = logging.getLogger(__name__)

CHUNK_SIZE = 128 * 1024  # 128 KB chunks
CONNECT_TIMEOUT = 15.0   # seconds
READ_TIMEOUT = 25.0      # seconds per chunk read
MAX_RETRIES = 5
RETRY_BACKOFF = 2.0      # seconds


def downloads_dir():
    """Per-user directory where downloaded firmware packages are stored."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        app_dir = "Updater CE"
    elif sys.platform == "darwin":
        # macOS stores the app's own files in the shared Application Support
        # folder — the pre-3.0 app's folder, kept as the app's home.
        base = Path.home() / "Library" / "Application Support"
        app_dir = "Innioasis Updater"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        app_dir = "updater-ce"
    d = base / app_dir / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_picker_dir() -> str:
    """A user-friendly starting folder for a file picker.

    macOS asks for Documents access when an app opens its file dialog in the
    home folder, because the panel enumerates every child. Downloads is where
    firmware archives normally arrive, so start there (and fall back to the
    app's own download folder) instead of touching Documents.
    """
    try:
        app_downloads = downloads_dir()
    except OSError:
        app_downloads = None
    for candidate in (Path.home() / "Downloads", app_downloads):
        if candidate is None:
            continue
        try:
            if candidate.is_dir():
                return str(candidate)
        except OSError:
            continue
    # Never hand a native panel the home folder: that is what asks macOS for
    # Documents access. The temp folder is an accessible neutral fallback.
    return tempfile.gettempdir()


def firmware_cache_size() -> tuple[int, int]:
    """Calculate the number of files and total byte size of cached firmwares.

    Returns (file_count, total_bytes).
    """
    d = downloads_dir()
    if not d.is_dir():
        return 0, 0
    count = 0
    total_bytes = 0
    for root, _, files in os.walk(d):
        for f in files:
            try:
                fp = Path(root) / f
                if fp.is_file():
                    count += 1
                    total_bytes += fp.stat().st_size
            except OSError:
                pass
    return count, total_bytes


def clear_firmware_cache(exclude=None) -> tuple[int, int]:
    """Delete all downloaded firmware archives (*.zip, *.rar, *.part) and extracted ROM
    staging directories in downloads_dir(). Leaves device tracking records, update reminder
    preferences, and install history completely intact.

    Returns (items_removed_count, total_bytes_freed).
    """
    import shutil

    d = downloads_dir()
    if not d.is_dir():
        return 0, 0
    excluded = set()
    if exclude:
        for p in exclude:
            try:
                excluded.add(Path(p).resolve())
            except Exception:
                pass
    count = 0
    bytes_freed = 0
    for item in list(d.iterdir()):
        try:
            if item.resolve() in excluded:
                continue
            if item.is_file():
                ext = item.suffix.lower()
                if ext in (".zip", ".rar", ".part", ".tar", ".gz", ".7z") or item.name.endswith(".part"):
                    size = item.stat().st_size
                    item.unlink()
                    count += 1
                    bytes_freed += size
            elif item.is_dir():
                dir_size = 0
                for root, _, files in os.walk(item):
                    for f in files:
                        try:
                            dir_size += (Path(root) / f).stat().st_size
                        except OSError:
                            pass
                shutil.rmtree(item, ignore_errors=True)
                count += 1
                bytes_freed += dir_size
        except Exception as e:
            logger.warning("Failed to remove cached firmware item %s: %s", item, e)
    return count, bytes_freed


#: Archive suffixes a firmware package arrives in. Its working copy is
#: ``.<stem>_extracted`` beside the archive (see flash_service.compute_extract_dir).
FIRMWARE_ARCHIVE_SUFFIXES = (".zip", ".rar")
THEME_TEMP_PREFIX = "themepack_download_"
#: An interrupted download is worth resuming for a day; after that it is junk.
INTERRUPTED_PART_MAX_AGE = 24 * 60 * 60
#: A temp staging folder younger than this may belong to a run in progress.
TEMP_LEFTOVER_MAX_AGE = 2 * 60 * 60


def _tree_size(path: Path) -> int:
    """Bytes a file or directory tree occupies. Never raises."""
    try:
        if path.is_file() or path.is_symlink():
            return path.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def _holds_a_scatter(folder: Path) -> bool:
    """True when an extracted package still names its partitions.

    Every layout the app extracts puts the scatter at the package folder or
    one level below it. An extract without one can never be flashed again, so
    it is garbage however new it is.
    """
    try:
        candidates = [folder, *(entry for entry in folder.iterdir() if entry.is_dir())]
    except OSError:
        return False
    for candidate in candidates:
        try:
            entries = list(candidate.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name.casefold()
            if name.endswith(".txt") and "scatter" in name and entry.is_file():
                return True
    return False


def _discard(path: Path, *, truncate: bool = False) -> int:
    """Remove one file or tree, returning the bytes freed (0 when it failed)."""
    size = _tree_size(path)
    try:
        if truncate and path.is_file() and not path.is_symlink():
            # Log files are held open by a logging handler. O_APPEND writes at
            # the new end of the same file, so emptying it frees the space and
            # keeps later lines visible instead of pointing at an unlinked inode.
            with open(path, "w", encoding="utf-8"):
                pass
        elif path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as exc:
        logger.debug("Could not remove %s: %s", path, exc)
        return 0
    return size


def _extract_archive_name(directory: Path, extract_name: str) -> str:
    """The archive beside ``.<stem>_extracted``, when it is still there."""
    stem = extract_name[1 : -len("_extracted")]
    for ext in FIRMWARE_ARCHIVE_SUFFIXES:
        if (directory / f"{stem}{ext}").is_file():
            return f"{stem}{ext}"
    return ""


def enforce_firmware_retention(keep_package_path=None) -> tuple[int, int]:
    """Bound how much firmware the app keeps between runs.

    Settings and cached remote data live outside the downloads folder; what is
    bounded here is the heavy working copy of a firmware package. At most the
    most recent set survives: the extract a retry can still flash and its
    archive. Everything else is what earlier runs left behind -- older
    extracts, an extract that no longer holds a scatter, the staged copy a
    flash made, interrupted ``.part`` files and theme-pack temp downloads.

    With no ``keep_package_path`` (startup, after a finished run) the app keeps
    the newest usable set, so restarting right after a failed install still
    leaves something to retry. A caller that knows the run is over (a
    successful install) clears the folder with :func:`clear_firmware_cache`
    instead. Returns ``(items_removed, bytes_freed)``.
    """
    directory = downloads_dir()
    if not directory.is_dir():
        return 0, 0

    keep_dir = ""
    keep_archive = ""
    keep_stage = None
    if keep_package_path:
        package = Path(str(keep_package_path))
        try:
            chain = (package, *package.parents)
        except OSError:
            chain = (package,)
        for ancestor in chain:
            name = ancestor.name
            if ancestor.parent == directory and name.startswith(".") and name.endswith("_extracted"):
                keep_dir = name
                keep_archive = _extract_archive_name(directory, name)
                break
            if ancestor.parent == directory and ancestor.suffix.lower() in FIRMWARE_ARCHIVE_SUFFIXES:
                keep_archive = name
                keep_dir = f".{ancestor.stem}_extracted"
                break
            if (
                ancestor.parent.name == "staged_firmware"
                and ancestor.parent.parent == directory
                and name.startswith("stage_")
            ):
                keep_stage = ancestor
                break

    extracts: list[Path] = []
    archives: list[Path] = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return 0, 0
    for entry in entries:
        try:
            if entry.is_dir() and entry.name.startswith(".") and entry.name.endswith("_extracted"):
                extracts.append(entry)
            elif entry.is_file() and entry.suffix.lower() in FIRMWARE_ARCHIVE_SUFFIXES:
                archives.append(entry)
        except OSError:
            continue

    if not keep_dir:
        # The most recent extract that can still flash is the one worth
        # keeping; a folder without a scatter is never a candidate.
        usable = []
        for extract in extracts:
            if not _holds_a_scatter(extract):
                continue
            try:
                usable.append((extract.stat().st_mtime, extract))
            except OSError:
                continue
        if usable:
            keep_dir = max(usable, key=lambda item: item[0])[1].name
            keep_archive = _extract_archive_name(directory, keep_dir)

    removed = 0
    freed = 0
    keep_dir_key = keep_dir.casefold()
    keep_archive_key = keep_archive.casefold()

    for extract in extracts:
        if extract.name.casefold() == keep_dir_key and _holds_a_scatter(extract):
            continue
        logger.info("Pruning cached firmware extraction: %s", extract.name)
        freed += _discard(extract)
        removed += 1

    for archive in archives:
        if archive.name.casefold() == keep_archive_key:
            continue
        logger.info("Pruning cached firmware package: %s", archive.name)
        freed += _discard(archive)
        removed += 1

    staged_root = directory / "staged_firmware"
    if staged_root.is_dir():
        try:
            staged_items = list(staged_root.iterdir())
        except OSError:
            staged_items = []
        keep_stage_key = ""
        if keep_stage is not None:
            try:
                keep_stage_key = str(keep_stage.resolve()).casefold()
            except OSError:
                keep_stage_key = ""
        for item in staged_items:
            try:
                if keep_stage_key and str(item.resolve()).casefold() == keep_stage_key:
                    continue
            except OSError:
                pass
            freed += _discard(item)
            removed += 1
        if not keep_stage_key:
            freed += _discard(staged_root)
            removed += 1

    now = time.time()
    try:
        leftovers = [entry for entry in directory.iterdir() if entry.is_file()]
    except OSError:
        leftovers = []
    for item in leftovers:
        try:
            age = now - item.stat().st_mtime
        except OSError:
            continue
        low = item.name.casefold()
        is_stale_part = low.endswith(".part") and age > INTERRUPTED_PART_MAX_AGE
        is_theme_temp = low.startswith(THEME_TEMP_PREFIX)
        is_stray_scatter = low.endswith(".txt") and "scatter" in low
        if is_stale_part or is_theme_temp or is_stray_scatter:
            logger.info("Pruning stale download-cache file: %s", item.name)
            freed += _discard(item)
            removed += 1

    if removed:
        logger.info(
            "Firmware retention removed %d item(s) and freed %.1f MB",
            removed,
            freed / (1024 * 1024),
        )
    return removed, freed


#: Names the single-instance and busy markers use. They are live bookkeeping,
#: not cache data, so clearing storage must never remove them.
_LIVE_BOOKKEEPING_HINTS = ("busy", "lock", "instance")


def clear_app_storage() -> tuple[int, int]:
    """Clear everything the app can download or rebuild.

    Firmware working copies, the cached catalogue/notice data, cached icons and
    docs, leftover staging folders and the logs are all removed. QSettings,
    install records and update-reminder preferences are untouched: the caches
    are fetched again from the online sources on next use. Returns
    ``(items_removed, bytes_freed)``.
    """
    removed = 0
    freed = 0

    count, cache_freed = clear_firmware_cache()
    removed += count
    freed += cache_freed

    directory = downloads_dir()
    if directory.is_dir():
        try:
            leftovers = list(directory.iterdir())
        except OSError:
            leftovers = []
        for item in leftovers:
            freed += _discard(item)
            removed += 1

    try:
        from .paths import app_cache_dir

        cache_root = app_cache_dir()
        if cache_root.is_dir():
            for item in list(cache_root.iterdir()):
                name = item.name.casefold()
                if any(hint in name for hint in _LIVE_BOOKKEEPING_HINTS):
                    continue
                freed += _discard(item)
                removed += 1
    except Exception as exc:
        logger.debug("Could not clear the app cache: %s", exc)

    # Logs sit beside the downloads folder: the per-tool files in logs/ and the
    # app's own rolling trace at the root.
    support_dir = directory.parent
    for log_dir in (support_dir / "logs", support_dir):
        if not log_dir.is_dir():
            continue
        try:
            log_files = [entry for entry in log_dir.iterdir() if entry.is_file() and "log" in entry.name.casefold()]
        except OSError:
            log_files = []
        for log_file in log_files:
            size = _discard(log_file, truncate=True)
            if size:
                freed += size
                removed += 1

    # Staging folders an interrupted or crashed run left in the temp directory.
    temp_root = Path(tempfile.gettempdir())
    now = time.time()
    for pattern in ("innioasis-*", "innioasis-fw-*", "rockbox_*", "rockbox-*", ".innioasis-keep-*"):
        try:
            candidates = list(temp_root.glob(pattern))
        except OSError:
            continue
        for item in candidates:
            try:
                if now - item.stat().st_mtime < TEMP_LEFTOVER_MAX_AGE:
                    continue
            except OSError:
                continue
            freed += _discard(item)
            removed += 1

    logger.info(
        "Cleared app storage: %d item(s), %.1f MB freed",
        removed,
        freed / (1024 * 1024),
    )
    return removed, freed


def ensure_disk_space_available(target_path=None, needed_bytes: int = 0) -> bool:
    """Check if host has enough disk space. Proactively cleans firmware cache if low.
    Returns True if sufficient space is available, False if out of storage.
    """
    import shutil

    d = Path(target_path).parent if target_path else downloads_dir()
    try:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(d)
        threshold = max(needed_bytes + 50 * 1024 * 1024, 100 * 1024 * 1024)
        if usage.free < threshold:
            exclude = None
            if target_path:
                tp = Path(target_path)
                exclude = {tp, Path(f"{tp}.part")}
            clear_firmware_cache(exclude=exclude)
            usage = shutil.disk_usage(d)
            if usage.free < threshold:
                return False
        return True
    except Exception as e:
        logger.debug("Disk usage check error: %s", e)
        return True


class DownloadWorker(QThread):
    """Download a GitHub release asset to a local file, reporting progress."""

    progress = Signal(int)          # 0-100
    status = Signal(str)            # human-readable status line
    finished = Signal(bool, str)    # ok, error_or_path

    def __init__(self, url, destination, token="", parent=None, fallback_urls=None):
        super().__init__(parent)
        self.url = url
        self.destination = Path(destination)
        self.token = token
        self._cancelled = False
        self._response = None
        # Mirror copies of the same release, tried in order when this source
        # stops answering (a deleted repository, a blocked host).
        self._fallbacks = [
            str(candidate).strip() for candidate in (fallback_urls or [])
            if str(candidate or "").strip() and str(candidate).strip() != self.url
        ]

    def _mirror_host(self) -> str:
        try:
            from urllib.parse import urlsplit

            return urlsplit(self.url).hostname or self.url
        except Exception:
            return self.url

    def cancel(self):
        self._cancelled = True
        # Closing the response unblocks a read that is already waiting on the
        # socket, so iter_content() raises at once. Without this the flag was
        # only seen after READ_TIMEOUT, which is what made a cancelled or
        # closing download look stalled for up to 25 seconds.
        response = self._response
        self._response = None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def run(self):
        part_file = Path(f"{self.destination}.part")
        try:
            self.destination.parent.mkdir(parents=True, exist_ok=True)

            # If destination already exists and is non-empty, check if it's already complete
            if self.destination.is_file() and self.destination.stat().st_size > 0:
                try:
                    head_headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Innioasis-Updater-CE",
                    }
                    if self.token:
                        head_headers["Authorization"] = f"token {self.token}"
                    r_head = requests_module().head(self.url, allow_redirects=True, timeout=10, headers=head_headers)
                    expected_size = int(r_head.headers.get("content-length", 0))
                    if expected_size > 0 and self.destination.stat().st_size == expected_size:
                        logger.info("Destination file %s already complete (%d bytes)", self.destination, expected_size)
                        self.status.emit(tr("dl_complete"))
                        self.progress.emit(100)
                        self.finished.emit(True, str(self.destination))
                        return
                except Exception:
                    pass

            # Check for existing partial download to resume
            # If destination exists as an incomplete file, rename to .part
            if not part_file.is_file() and self.destination.is_file():
                try:
                    self.destination.rename(part_file)
                except Exception:
                    pass

            downloaded = part_file.stat().st_size if part_file.is_file() else 0
            total_size = 0
            last_percent = -1
            last_status_time = 0.0
            if downloaded == 0:
                self.progress.emit(0)
            session = requests_module().Session()

            attempts: list[int] = list(range(1, MAX_RETRIES + 1))
            while attempts:
                attempt = attempts.pop(0)
                if self._cancelled:
                    self.finished.emit(False, "USER_CANCELLED")
                    return

                try:
                    headers = {
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Innioasis-Updater-CE",
                    }
                    if self.token:
                        headers["Authorization"] = f"token {self.token}"

                    if downloaded > 0:
                        headers["Range"] = f"bytes={downloaded}-"
                        logger.info("Resuming download from byte %d (attempt %d/%d)", downloaded, attempt, MAX_RETRIES)
                        self.status.emit(tr("dl_resuming_fmt").format(mb=f"{downloaded / (1024 * 1024):.1f}"))
                    else:
                        self.status.emit(tr("dl_starting"))

                    response = session.get(
                        self.url,
                        stream=True,
                        timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                        headers=headers,
                        allow_redirects=True,
                    )

                    # Handle 206 Partial Content, 200 OK, or 416 Range Not Satisfiable
                    if response.status_code == 206:
                        content_range = response.headers.get("content-range", "")
                        if "/" in content_range:
                            try:
                                total_size = int(content_range.split("/")[-1])
                            except ValueError:
                                total_size = downloaded + int(response.headers.get("content-length", 0))
                        else:
                            total_size = downloaded + int(response.headers.get("content-length", 0))
                        file_mode = "ab"
                    elif response.status_code == 200:
                        total_size = int(response.headers.get("content-length", 0))
                        downloaded = 0
                        file_mode = "wb"
                    elif response.status_code == 416:
                        if total_size > 0 and downloaded >= total_size:
                            break
                        downloaded = 0
                        file_mode = "wb"
                        continue
                    else:
                        response.raise_for_status()
                        file_mode = "wb"
                        total_size = int(response.headers.get("content-length", 0))

                    self._response = response
                    logger.info("Streaming download: downloaded=%d, total=%d, mode=%s", downloaded, total_size, file_mode)

                    if total_size > 0:
                        needed = max(0, total_size - downloaded)
                        if not ensure_disk_space_available(self.destination, needed):
                            logger.error("Insufficient disk space to download %s (needed %d bytes)", self.destination, needed)
                            self.finished.emit(False, "OUT_OF_STORAGE")
                            return

                    speed_bytes_sec = 0.0
                    last_speed_time = time.time()
                    bytes_since_speed = 0

                    with open(part_file, file_mode) as f:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if self._cancelled:
                                self.finished.emit(False, "USER_CANCELLED")
                                return
                            if not chunk:
                                continue

                            try:
                                f.write(chunk)
                            except OSError as write_err:
                                from .flash_service import is_storage_full_error
                                if is_storage_full_error(str(write_err), write_err):
                                    clear_firmware_cache(exclude={self.destination, part_file})
                                    try:
                                        f.write(chunk)
                                    except OSError:
                                        logger.error("Disk full while writing download: %s", write_err)
                                        self.finished.emit(False, "OUT_OF_STORAGE")
                                        return
                                else:
                                    raise
                            chunk_len = len(chunk)
                            downloaded += chunk_len
                            bytes_since_speed += chunk_len

                            now = time.time()
                            speed_dt = now - last_speed_time
                            if speed_dt >= 0.5:
                                speed_bytes_sec = bytes_since_speed / speed_dt
                                last_speed_time = now
                                bytes_since_speed = 0

                            if total_size > 0:
                                percent = min(99, int(downloaded * 100 / total_size))
                                if percent != last_percent:
                                    last_percent = percent
                                    self.progress.emit(percent)

                            if now - last_status_time >= 0.3:
                                last_status_time = now
                                speed_mb = speed_bytes_sec / (1024 * 1024)
                                dl_mb = downloaded / (1024 * 1024)
                                if total_size > 0:
                                    tot_mb = total_size / (1024 * 1024)
                                    remaining = total_size - downloaded
                                    eta_text = ""
                                    if speed_bytes_sec > 1024:
                                        from .progress_model import format_eta_seconds

                                        eta_text = format_eta_seconds(remaining / speed_bytes_sec)
                                    status_str = tr("dl_progress_fmt").format(
                                        dl=f"{dl_mb:.1f}", tot=f"{tot_mb:.1f}", pct=percent, speed=f"{speed_mb:.1f}"
                                    )
                                    if eta_text:
                                        status_str = f"{status_str} · {eta_text}"
                                else:
                                    status_str = tr("dl_progress_unknown_fmt").format(
                                        dl=f"{dl_mb:.1f}", speed=f"{speed_mb:.1f}"
                                    )
                                self.status.emit(status_str)

                    self._response = None
                    if total_size > 0 and downloaded < total_size:
                        raise requests_module().exceptions.RequestException(
                            f"Connection closed prematurely ({downloaded}/{total_size} bytes received)"
                        )

                    # Successfully finished reading all data
                    break

                except Exception as exc:
                    if self._cancelled:
                        self.finished.emit(False, "USER_CANCELLED")
                        return

                    from .flash_service import is_storage_full_error
                    if is_storage_full_error(str(exc), exc):
                        logger.error("Download aborted due to out-of-storage: %s", exc)
                        self.finished.emit(False, "OUT_OF_STORAGE")
                        return

                    logger.warning("Download error on attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
                    if "certificate" in str(exc).lower() or "cacert" in str(exc).lower():
                        # CA bundle issue: reset SSL_CERT_FILE and recreate session
                        for k in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
                            os.environ.pop(k, None)
                        try:
                            import certifi
                            os.environ["SSL_CERT_FILE"] = certifi.where()
                        except Exception:
                            pass
                        session = requests_module().Session()
                    if attempt < MAX_RETRIES:
                        self.status.emit(tr("dl_retry_fmt").format(attempt=attempt, max=MAX_RETRIES))
                        # Interruptible backoff: a cancel during the pause must
                        # not have to wait it out.
                        resume_at = time.time() + RETRY_BACKOFF
                        while time.time() < resume_at:
                            if self._cancelled:
                                self.finished.emit(False, "USER_CANCELLED")
                                return
                            time.sleep(0.1)
                        if part_file.is_file():
                            downloaded = part_file.stat().st_size
                    else:
                        # This source stopped answering permanently. A mirror
                        # carries the same release, so continue from there
                        # rather than ending the install.
                        if self._fallbacks:
                            self.url = self._fallbacks.pop(0)
                            try:
                                if part_file.is_file():
                                    part_file.unlink()
                            except OSError:
                                pass
                            downloaded = 0
                            total_size = 0
                            self.progress.emit(0)
                            self.status.emit(
                                tr("dl_mirror_fmt").format(host=self._mirror_host())
                            )
                            attempts = list(range(1, MAX_RETRIES + 1))
                            continue
                        raise

            # Atomic rename from .part to final destination
            if self.destination.exists():
                self.destination.unlink()
            part_file.replace(self.destination)

            # Status first: the card paints the bar on progress and the byte
            # line on status, so the final status must not arrive after the bar
            # has already been filled to 100% (which reads as "stalled at "
            # whatever the last percentage line said).
            self.status.emit(tr("dl_complete"))
            self.progress.emit(100)
            self.finished.emit(True, str(self.destination))

        except Exception as e:
            logger.error("Download failed permanently: %s", e, exc_info=True)
            self.finished.emit(False, str(e))
