"""App update checking.

Compares the latest release of the app's GitHub repo against the installed
``APP_VERSION`` and, when a newer version exists, picks the right download
asset for the current OS (Windows / macOS / Linux) so the UI can walk the user
through download and installation.

The GitHub fetch reuses ``catalog.ReleasesClient`` (token + rate-limit aware,
24 h per-user cache) and never raises: any failure simply reports \"no update\".
"""

import logging
import re
import sys
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from .config import (
    APP_VERSION,
    UPDATE_ASSET_ARCH_PRIORITY,
    UPDATE_ASSET_EXTENSIONS,
    UPDATE_REPO,
)
from .catalog import ReleasesClient

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"^\D*(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(tag: str):
    """Normalize a release tag (``v2.0.4``, ``2.0.4``, ``2.0``) to
    ``(major, minor, patch)``; returns None for non-version tags."""
    m = _VERSION_RE.match((tag or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def is_newer(tag: str, current: str) -> bool:
    """True when ``tag`` is a version strictly newer than ``current``."""
    new = parse_version(tag)
    cur = parse_version(current)
    if new is None or cur is None:
        return False
    return new > cur


def _match_ext(name: str, exts) -> str:
    low = name.lower()
    for ext in exts:
        if low.endswith(ext.lower()):
            return ext
    return ""


def pick_platform_asset(assets, platform=None):
    """Best download asset for ``platform`` (sys.platform when omitted).

    Scores by: matching extension (per UPDATE_ASSET_EXTENSIONS) → architecture
    token rank (universal/x86_64/amd64 > arm64/aarch64 > unmarked) → size.
    Returns the asset dict or None.
    """
    platform = platform or sys.platform
    exts = UPDATE_ASSET_EXTENSIONS.get(platform)
    if not exts or not assets:
        return None
    scored = []
    for a in assets:
        name = (a.get("name") or "").lower()
        if not _match_ext(name, exts):
            continue
        arch_rank = len(UPDATE_ASSET_ARCH_PRIORITY)
        for i, token in enumerate(UPDATE_ASSET_ARCH_PRIORITY):
            if token in name:
                arch_rank = i
                break
        scored.append((arch_rank, -int(a.get("size", 0) or 0), a))
    if not scored:
        return None
    scored.sort(key=lambda t: (t[0], t[1]))
    return scored[0][2]


def asset_hint(platform=None) -> str:
    """Human label for the expected asset on this platform (for guidance)."""
    platform = platform or sys.platform
    return {
        "win32": "Windows installer (.exe)",
        "darwin": "macOS disk image (.dmg)",
        "linux": "AppImage / run script (.AppImage, .sh)",
    }.get(platform, "release asset")


@dataclass
class UpdateInfo:
    tag: str = ""
    name: str = ""
    body: str = ""
    published_at: str = ""
    html_url: str = ""
    assets: list = field(default_factory=list)
    failed: bool = False  # True when the check itself errored

    @property
    def version(self) -> str:
        return self.tag.lstrip("vV") if self.tag else ""


class UpdateChecker:
    def __init__(self, repo=UPDATE_REPO, client=None, current=APP_VERSION):
        self.repo = repo
        self.client = client or ReleasesClient()
        self.current = current

    def check(self, force=False) -> UpdateInfo:
        """Latest newer release for this app, or ``UpdateInfo`` with tag=\"\"
        when up to date / unreachable (never raises)."""
        try:
            data = self.client.get_latest_release_info(self.repo)
        except Exception as e:
            logger.debug("Update check failed for %s: %s", self.repo, e)
            return UpdateInfo(failed=True)
        if not data or not data.get("tag_name"):
            return UpdateInfo()
        if not is_newer(data["tag_name"], self.current):
            return UpdateInfo()
        return UpdateInfo(
            tag=data.get("tag_name", ""),
            name=data.get("name", ""),
            body=data.get("body", ""),
            published_at=data.get("published_at", ""),
            html_url=data.get("html_url", ""),
            assets=data.get("assets") or [],
        )


class UpdateCheckWorker(QThread):
    """Runs an update check off the UI thread."""

    finished = Signal(object)  # UpdateInfo (tag="" when up to date/failed)

    def __init__(self, repo=UPDATE_REPO, current=APP_VERSION, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.current = current

    def run(self):
        try:
            info = UpdateChecker(self.repo, current=self.current).check()
        except Exception:
            logger.exception("Update check worker failed")
            info = UpdateInfo()
        self.finished.emit(info)
