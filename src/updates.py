"""App update checking.

Compares the latest release of the app's GitHub repo against the installed
``APP_VERSION`` and, when a newer version exists *for this OS*, picks the right
download asset so the UI can walk the user through download and installation.
A release with no asset for the running platform is not offered at all: the
rewrite goes out one platform at a time, so a Windows-only release must be
invisible to a macOS or Linux build. That is decided by asset name here, not by
``config.RELEASE_TARGETS``.

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


def _is_linux_asset(name: str) -> bool:
    """False for names that belong to another OS (a Windows portable zip must
    never be offered to Linux, even when it contains the word "updater")."""
    return not any(tok in name for tok in ("darwin", "mac", "win32", "windows")) and not name.endswith(".exe")


def pick_platform_asset(assets, platform=None):
    """Best download asset for ``platform`` (sys.platform when omitted).

    Target release asset names per platform:
    - macOS (darwin): Innioasis.Updater.for.Mac.dmg
    - Windows (win32): installer.exe
    - Linux (linux): UpdaterCE.appimage, UpdaterCE.tar.gz, UpdaterCE-Linux-Portable.zip, run_linux.sh, updater_ce_linux_portable.tar.gz/.zip

    If these specific target files exist, returns that asset. If not found, falls back
    to matching extensions/architecture priority with strict platform exclusions.
    """
    platform = platform or sys.platform
    if not assets:
        return None

    targets = UPDATE_TARGET_ASSETS.get(platform) or ()
    if isinstance(targets, str):
        targets = (targets,)

    # 1. Exact or canonical name matches in priority order
    for target in targets:
        target_lower = target.lower()
        for a in assets:
            name = (a.get("name") or "").lower()
            if name == target_lower:
                return a

    # 2. Relaxed target prefix/suffix match (e.g. installer*.exe or updaterce*.appimage)
    for a in assets:
        name = (a.get("name") or "").lower()
        if platform == "win32":
            if (name.startswith("installer") or "setup" in name) and name.endswith(".exe"):
                return a
        elif platform == "darwin":
            if "updater" in name and name.endswith(".dmg"):
                return a
        elif platform == "linux":
            if _is_linux_asset(name) and "updater" in name and (name.endswith(".appimage") or name.endswith(".tar.gz") or name.endswith(".zip")):
                return a
            # Filenames with spaces (the branded AppImage/portable package) are
            # valid release assets too, but need not contain the slug "updater".
            if _is_linux_asset(name) and name in ("mediatek installer.appimage", "mediatek installer.tar.gz"):
                return a

    # 3. Fallback to extension & arch scoring with strict OS isolation
    exts = UPDATE_ASSET_EXTENSIONS.get(platform)
    if not exts:
        return None
    scored = []
    for a in assets:
        name = (a.get("name") or "").lower()
        if not _match_ext(name, exts):
            continue
        # Strict platform exclusion guards
        if platform == "win32" and ("linux" in name or "darwin" in name or "mac" in name):
            continue
        if platform == "darwin" and ("linux" in name or "win32" in name or "windows" in name or name.endswith(".exe")):
            continue
        if platform == "linux" and not _is_linux_asset(name):
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
        "linux": "Linux package (UpdaterCE.appimage / UpdaterCE.tar.gz)",
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
        releases = []
        try:
            if hasattr(self.client, "get_all_update_releases"):
                releases = self.client.get_all_update_releases(self.repo, force_refresh=force)
            if not releases:
                try:
                    single = self.client.get_latest_release_info(self.repo, force=force)
                except TypeError:
                    single = self.client.get_latest_release_info(self.repo)
                if single and isinstance(single, dict) and single.get("tag_name"):
                    releases = [single]
        except Exception as e:
            logger.debug("Update check failed for %s: %s", self.repo, e)
            return UpdateInfo(failed=True)

        if not releases:
            return UpdateInfo()

        # Filter candidate releases strictly newer than current version
        candidate_releases = [r for r in releases if is_newer(r.get("tag_name", ""), self.current)]
        candidate_releases.sort(
            key=lambda r: parse_version(r.get("tag_name", "")) or (0, 0, 0),
            reverse=True,
        )

        for rel in candidate_releases:
            assets = rel.get("assets") or []
            if pick_platform_asset(assets):
                logger.info(
                    "Selected update %s for %s (current: %s)",
                    rel.get("tag_name"),
                    asset_hint(),
                    self.current,
                )
                return UpdateInfo(
                    tag=rel.get("tag_name", ""),
                    name=rel.get("name", ""),
                    body=rel.get("body", ""),
                    published_at=rel.get("published_at", ""),
                    html_url=rel.get("html_url", ""),
                    assets=assets,
                )

        logger.info("No newer releases contain assets for %s; staying on %s", asset_hint(), self.current)
        return UpdateInfo()


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
