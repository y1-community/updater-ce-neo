"""App update checking.

Compares the latest release of the app's GitHub repo against the installed
``APP_VERSION`` and, when a newer version exists *for this OS*, picks the right
download asset so the UI can walk the user through download and installation.
A release with no asset for the running platform is not offered at all: the
rewrite goes out one platform at a time (see ``config.RELEASE_TARGETS``), so a
Windows-only release must be invisible to a macOS or Linux build.

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
    UPDATE_TARGET_ASSETS,
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

    Target release asset names per platform:
    - macOS (darwin): Innioasis.Updater.for.Mac.dmg
    - Windows (win32): installer.exe
    - Linux (linux): UpdaterCE.appimage

    If these specific target files exist, returns that asset. If not found, falls back
    to matching extensions/architecture priority. If no suitable asset exists for the
    platform in the release, returns None (assuming the user is running the latest build).
    """
    platform = platform or sys.platform
    if not assets:
        return None

    target_name = UPDATE_TARGET_ASSETS.get(platform)
    if target_name:
        target_lower = target_name.lower()
        for a in assets:
            if (a.get("name") or "").lower() == target_lower:
                return a

    # Fallback to extension & arch scoring if target name not found or for legacy/test assets
    exts = UPDATE_ASSET_EXTENSIONS.get(platform)
    if not exts:
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
        "win32": "Windows installer (installer.exe)",
        "darwin": "macOS disk image (Innioasis.Updater.for.Mac.dmg)",
        "linux": "Linux AppImage (UpdaterCE.appimage)",
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
            try:
                data = self.client.get_latest_release_info(self.repo, force=force)
            except TypeError:
                data = self.client.get_latest_release_info(self.repo)
        except Exception as e:
            logger.debug("Update check failed for %s: %s", self.repo, e)
            return UpdateInfo(failed=True)
        if not data or not data.get("tag_name"):
            return UpdateInfo()
        if not is_newer(data["tag_name"], self.current):
            return UpdateInfo()
        assets = data.get("assets") or []
        # Releases are staggered by platform: a release only exists for an OS
        # once it carries an asset for it. Without one there is nothing this
        # build could install, so offering the update would only produce a
        # notice with no download behind it.
        if not pick_platform_asset(assets):
            logger.info(
                "Release %s is not published for %s; staying on %s",
                data["tag_name"],
                asset_hint(),
                self.current,
            )
            return UpdateInfo()
        return UpdateInfo(
            tag=data.get("tag_name", ""),
            name=data.get("name", ""),
            body=data.get("body", ""),
            published_at=data.get("published_at", ""),
            html_url=data.get("html_url", ""),
            assets=assets,
        )


class UpdateCheckWorker(QThread):
    """Runs an update check off the UI thread."""

    finished = Signal(object)  # UpdateInfo (tag="" when up to date/failed)

    def __init__(self, repo=UPDATE_REPO, current=APP_VERSION, parent=None, force: bool = False):
        super().__init__(parent)
        self.repo = repo
        self.current = current
        self.force = force

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            info = UpdateChecker(self.repo, current=self.current).check(force=self.force)
            if self.isInterruptionRequested():
                return
        except Exception:
            logger.exception("Update check worker failed")
            info = UpdateInfo()
        if not self.isInterruptionRequested():
            self.finished.emit(info)
