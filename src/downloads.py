"""Streaming firmware download worker (ported from CE's ``_download_release_zip``)."""

import logging
import os
import sys
from pathlib import Path

import requests
from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

CHUNK_SIZE = 8192


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
        self.destination = destination
        self.token = token
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            headers = {"Accept": "application/vnd.github+json"}
            if self.token:
                headers["Authorization"] = f"token {self.token}"
            response = requests.get(self.url, stream=True, timeout=20, headers=headers)
            if response.status_code == 302:
                redirect_url = response.headers.get("Location")
                if redirect_url:
                    response = requests.get(redirect_url, stream=True, timeout=20, headers=headers)
            response.raise_for_status()

            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            os.makedirs(os.path.dirname(self.destination) or ".", exist_ok=True)
            with open(self.destination, "wb") as f:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if self._cancelled:
                        self.finished.emit(False, "USER_CANCELLED")
                        return
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(min(99, int(downloaded * 100 / total)))
            self.progress.emit(100)
            self.finished.emit(True, self.destination)
        except Exception as e:
            logger.error("Download failed: %s", e, exc_info=True)
            self.finished.emit(False, str(e))
