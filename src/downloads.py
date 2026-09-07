"""Streaming firmware download worker with automatic resume, retry, and progress tracking."""

import logging
import os
import sys
import time
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

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
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    d = base / "innioasis-updater" / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


class DownloadWorker(QThread):
    """Download a GitHub release asset to a local file, reporting progress."""

    progress = Signal(int)          # 0-100
    status = Signal(str)            # human-readable status line
    finished = Signal(bool, str)    # ok, error_or_path

    def __init__(self, url, destination, token="", parent=None):
        super().__init__(parent)
        self.url = url
        self.destination = Path(destination)
        self.token = token
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

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
                    r_head = requests.head(self.url, allow_redirects=True, timeout=10, headers=head_headers)
                    expected_size = int(r_head.headers.get("content-length", 0))
                    if expected_size > 0 and self.destination.stat().st_size == expected_size:
                        logger.info("Destination file %s already complete (%d bytes)", self.destination, expected_size)
                        self.progress.emit(100)
                        self.status.emit("Download complete")
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
            session = requests.Session()

            for attempt in range(1, MAX_RETRIES + 1):
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
                        self.status.emit(f"Resuming download from {downloaded / (1024 * 1024):.1f} MB...")
                    else:
                        self.status.emit("Starting download…")

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

                    logger.info("Streaming download: downloaded=%d, total=%d, mode=%s", downloaded, total_size, file_mode)

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

                            f.write(chunk)
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
                                    status_str = f"{dl_mb:.1f} MB / {tot_mb:.1f} MB ({percent}%) \u2014 {speed_mb:.1f} MB/s"
                                else:
                                    status_str = f"{dl_mb:.1f} MB \u2014 {speed_mb:.1f} MB/s"
                                self.status.emit(status_str)

                    if total_size > 0 and downloaded < total_size:
                        raise requests.exceptions.RequestException(
                            f"Connection closed prematurely ({downloaded}/{total_size} bytes received)"
                        )

                    # Successfully finished reading all data
                    break

                except Exception as exc:
                    if self._cancelled:
                        self.finished.emit(False, "USER_CANCELLED")
                        return

                    logger.warning("Download error on attempt %d/%d: %s", attempt, MAX_RETRIES, exc)
                    if attempt < MAX_RETRIES:
                        self.status.emit(f"Connection interrupted. Retrying ({attempt}/{MAX_RETRIES})...")
                        time.sleep(RETRY_BACKOFF)
                        if part_file.is_file():
                            downloaded = part_file.stat().st_size
                    else:
                        raise

            # Atomic rename from .part to final destination
            if self.destination.exists():
                self.destination.unlink()
            part_file.replace(self.destination)

            self.progress.emit(100)
            self.status.emit("Download complete")
            self.finished.emit(True, str(self.destination))

        except Exception as e:
            logger.error("Download failed permanently: %s", e, exc_info=True)
            self.finished.emit(False, str(e))
