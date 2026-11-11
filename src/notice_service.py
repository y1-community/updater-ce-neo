"""Remote notice fetching and caching service (innioasis.app/notice.xml).

Fetches announcements and public notices from https://innioasis.app/notice.xml,
caches them locally, and determines which notices should be shown to the user.

Notices support:
- title: headline text
- content: notice description (also supports body)
- persistent: bool - whether the notice persists across app runs even after
  dismissal (persistent=true returns on every app launch; persistent=false
  can be dismissed permanently).
- type: optional "player_update" or "app_update" marker, used only to decide
  which notification category a notice belongs to.
- Dismissals are per notice, not per file: a changed notice.xml shows its new
  notices and keeps the ones the user already dismissed out of the list.

Targeting (every attribute set must match; the Notices list, the pop-out list
and the notification tray apply the date window and the platform/architecture
filters, while the contextual axes below only narrow the Select Software card):
- models: comma-separated target models (``Y1,Y2``).
- device_type: comma-separated hardware revisions (``A`` / ``B``).
- platform / os: the operating system(s) the notice is about (``mac``,
  ``windows``, ``linux``). ``os=`` is the same axis under a friendlier name;
  ``macos``/``osx``/``darwin`` and ``win``/``win32`` are normalised.
- arch: the CPU architecture(s) the *app* runs on (``arm64``, ``x86_64``,
  ``x86``, ``armv7``; ``aarch64``/``amd64``/``x64`` accepted, ``universal``
  matches every machine). Together with ``os`` this names one platform build:
  a Type B Y1 running Rockbox 0.1 on macOS arm64 is
  ``models="Y1" device_type="B" firmware_repo="rockbox-y1/rockbox"
  firmware_version="0.1" os="macos" arch="arm64"``.
- firmware_repo: the GitHub repository a notice is about, matching ``repo`` in
  ``slidia_manifest.xml`` (``rockbox-y1/rockbox``). Wildcards are allowed.
- firmware_version: release tag(s) the notice is about (``v3.2.0``, ``v3.2.*``),
  matching the release the reader has selected or the one they last installed.
A notice with none of them is for everyone. A target only narrows the card:
notice for a model the reader has no interest in is not offered there, while a
notice for a model they logged for update reminders (in Settings or by an
install) is. Several applicable notices alternate on the card, one at a time.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import platform as _platform
import re
import sys
import threading
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QSettings, Qt, Signal

from .paths import app_cache_dir

logger = logging.getLogger(__name__)

NOTICE_URL = "https://innioasis.app/notice.xml"
# One notice exists on the card at a time; several applicable notices take
# turns on that card so a reader does not have to open the list to see them.
NOTICE_ROTATION_MS = 12_000
_UNSET = object()
_SETTINGS_DISMISSED_KEY = "dismissed_notice_ids"
_SETTINGS_DISMISSED_FINGERPRINTS_KEY = "dismissed_notice_fingerprints"
_SETTINGS_CACHED_HASH_KEY = "notice_xml_content_hash"


@dataclass
class Notice:
    id: str
    title: str
    content: str
    persistent: bool = False
    severity: str = "info"
    # Feed marker for release notices; blank means an ordinary announcement.
    kind: str = ""
    # ``content`` is the banner/card text; ``full_content`` (falls back to
    # ``content``) is the Markdown body shown in the full notice view. ``url``
    # opens the notice-specific page when one exists.
    full_content: str = ""
    url: str = ""
    # ``from`` is when the notice was posted (a date or a timestamp); the app
    # shows only this. ``until`` is the date the notice stops being shown.
    from_date: Optional[datetime] = None
    until_date: Optional[date] = None
    models: tuple[str, ...] = ()
    # CSV of platforms this notice targets (``mac,windows``); empty means all.
    # ``os=`` in the feed is the same axis and is merged in here.
    platforms: tuple[str, ...] = ()
    # CSV of CPU architectures (``arm64``, ``x86_64``); empty means all. This
    # narrows to the machine the app runs on, e.g. Apple silicon Macs only.
    arches: tuple[str, ...] = ()
    # Context targets beyond the model: the hardware revision(s) (``A``/``B``),
    # the firmware repository the notice is about (the same ``repo`` value
    # ``slidia_manifest.xml`` uses), and the release tag(s) it applies to.
    # Empty means "no narrowing on this axis"; all of them empty means the
    # notice is for everyone.
    device_types: tuple[str, ...] = ()
    firmware_repo: str = ""
    firmware_versions: tuple[str, ...] = ()
    # Digest of the source wording. It survives translation so switching
    # language cannot make a dismissed notice look newly edited.
    source_fingerprint: str = ""
    # True for local (personal) notices generated by the app on this machine
    # (e.g., when translation or release polling is temporarily unavailable).
    local: bool = False

    @property
    def is_contextual(self) -> bool:
        """True when the notice targets anything more specific than everyone."""
        return bool(
            self.models
            or self.device_types
            or self.firmware_repo
            or self.firmware_versions
            or self.platforms
            or self.arches
        )

    @property
    def web_url(self) -> str:
        """The canonical URL for viewing this notice in a web browser."""
        from urllib.parse import quote, urlsplit

        url = (self.url or "").strip()
        if self.local and not url:
            return ""
        if url:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            on_innioasis = host == "innioasis.app" or host.endswith(".innioasis.app")
            path = parts.path.strip("/")
            is_bare_root = not path and not parts.query and not parts.fragment
            is_generic_notices = path == "notices.html" and "notice=" not in parts.query
            if not on_innioasis or (not is_bare_root and not is_generic_notices):
                return url
        if self.id:
            return f"https://innioasis.app/notices.html?notice={quote(self.id.strip())}"
        return url

    def is_live(self, platform: str = "") -> bool:
        """Whether the notice is published right now for ``platform``.

        This is the only filter the Notices list, the pop-out list and the
        notification tray apply: they show an announcement for everyone even
        when its contextual targets do not fit the current selection.
        """
        today = date.today()
        if self.from_date and today < self.from_date.date():
            return False
        if self.until_date and today > self.until_date:
            return False
        if self.platforms:
            wanted = {_normalise_os(p) for p in self.platforms if str(p).strip()}
            wanted.discard("")
            if "none" in wanted:
                return False
            if platform and _normalise_os(platform) not in wanted:
                return False
        if self.arches:
            wanted_arches = {normalise_arch(a) for a in self.arches if str(a).strip()}
            wanted_arches.discard("")
            # ``universal`` covers both sides of a universal binary; an
            # unreadable machine field must not hide the notice from anyone.
            if wanted_arches and "universal" not in wanted_arches:
                here = current_arch()
                if here and here not in wanted_arches:
                    return False
        return True

    def is_active_for(
        self,
        model: str = "",
        platform: str = "",
        device_type: str = "",
        firmware_repo: str = "",
        firmware_version: str = "",
    ) -> bool:
        """Check the date window, platform and the notice's context targets.

        ``model``/``device_type``/``firmware_repo``/``firmware_version``
        describe one reader context (what they selected, or a device they
        logged). Every target the notice sets must match it; a target the
        notice leaves blank is not narrowed. Callers that only have a model
        (as older call sites do) keep the original model-only behaviour.
        """
        if not self.is_live(platform):
            return False
        if self.models and model:
            target = model.strip().upper()
            if not any(m.strip().upper() == target for m in self.models):
                return False
        if self.device_types:
            wanted_types = {t.strip().upper() for t in self.device_types if t.strip()}
            if device_type:
                if device_type.strip().upper() not in wanted_types:
                    return False
            elif model:
                # No revision is known for this context. A model sold in one
                # revision is evidence enough that the notice does not apply;
                # a model with revisions could still match, so leave it be.
                available = _hardware_types_for(model)
                if not available or not (wanted_types & {t.upper() for t in available}):
                    return False
        if self.firmware_repo:
            if not firmware_repo or not _repo_matches(self.firmware_repo, firmware_repo):
                return False
        if self.firmware_versions:
            if not _version_matches(self.firmware_versions, firmware_version):
                return False
        return True

    def matches(self, context: "NoticeContext", platform: str = "") -> bool:
        """True when this notice applies to one reader context."""
        ctx = context or NoticeContext()
        return self.is_active_for(
            model=ctx.model,
            platform=platform,
            device_type=ctx.device_type,
            firmware_repo=ctx.firmware_repo,
            firmware_version=ctx.firmware_version,
        )

    def context_score(self, context: "NoticeContext", platform: str = "") -> int:
        """How specifically this notice answers ``context`` (-1 when it does not).

        The Select Software card orders by this, so the firmware the reader is
        looking at is talked about before a general announcement. A notice
        with no targets scores 0 and stays in the rotation for everyone.
        """
        ctx = context or NoticeContext()
        if not self.matches(ctx, platform):
            return -1
        score = 0
        if self.firmware_repo:
            score += 8
        if self.firmware_versions:
            score += 4
        if self.device_types:
            score += 2
        if self.models:
            score += 1
        if self.arches:
            # A notice naming the reader's exact build is more specific than one
            # that only names their model, so it takes the card first.
            score += 1
        return score


@dataclass(frozen=True)
class NoticeContext:
    """One reader's situation, used to pick contextual notices.

    The Select Software screen contributes the model, revision, software repo
    and selected release. Every device logged for update reminders
    contributes its own model, hardware type, software repo and last installed
    tag, so a notice about a player the reader actually owns is offered even
    while another model is selected in the drop-down.
    """

    model: str = ""
    device_type: str = ""
    firmware_repo: str = ""
    firmware_version: str = ""

    def is_empty(self) -> bool:
        return not (
            self.model
            or self.device_type
            or self.firmware_repo
            or self.firmware_version
        )


def _notice_content_hash(text: str) -> str:
    """Digest of one notice.xml document, used to skip unchanged refreshes."""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()


def notice_fingerprint(notice: Notice) -> str:
    """Stable digest of the words a reader sees in one notice.

    Dismissal is remembered against this digest, not just the id, so editing a
    notice's title or body makes it visible again while an untouched notice
    stays dismissed. ``source_fingerprint`` is used when present so a language
    change does not count as an edit.
    """
    if notice.source_fingerprint:
        return notice.source_fingerprint
    text = "\n".join(
        str(part or "").strip()
        for part in (notice.title, notice.content, notice.full_content)
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _hardware_types_for(model: str) -> tuple[str, ...]:
    """Hardware revisions ``model`` was sold in (empty for single-revision models)."""
    try:
        from .config import hardware_types_for_model

        return hardware_types_for_model(model)
    except Exception:
        return ()


def _normalise_repo(repo: str) -> str:
    """A repository name comparable with ``firmware_repo=``.

    Accepts ``rockbox-y1/rockbox`` as well as a GitHub URL, ignores a trailing
    ``.git`` and case, so the feed and ``slidia_manifest.xml`` can be written
    the way each is normally written.
    """
    text = str(repo or "").strip()
    text = re.sub(r"^https?://(?:www\.)?github\.com/", "", text, flags=re.IGNORECASE)
    text = text.strip().rstrip("/")
    if text.lower().endswith(".git"):
        text = text[:-4]
    return text.casefold()


def _repo_matches(targets: str, repo: str) -> bool:
    """True when ``repo`` is one of the (possibly wildcard) notice repos."""
    wanted = _normalise_repo(repo)
    if not wanted:
        return False
    for part in str(targets or "").split(","):
        pattern = _normalise_repo(part)
        if pattern and fnmatch.fnmatchcase(wanted, pattern):
            return True
    return False


def _version_matches(patterns: tuple[str, ...], version: str) -> bool:
    """Match a release tag against ``firmware_version`` patterns.

    ``*`` matches any release (a repo-wide notice), ``v3.2.*`` matches that
    series, and a bare ``3.2.0`` matches a ``v3.2.0`` tag: the leading ``v`` is
    the feed's business, not the reader's.
    """
    tag = str(version or "").strip()
    if not tag:
        return any(str(p).strip() == "*" for p in patterns)
    candidates = {tag.casefold(), tag.lstrip("vV").casefold()}
    for pattern in patterns:
        wanted = str(pattern or "").strip().casefold()
        if not wanted:
            continue
        for candidate in candidates:
            if fnmatch.fnmatchcase(candidate, wanted):
                return True
            if wanted.startswith("v") and fnmatch.fnmatchcase(candidate, wanted[1:]):
                return True
    return False


#: Moveable spellings of the machine field, resolved to the two names the
#: feed is written in (plus any architecture the reader's machine reports).
_ARCH_ALIASES = {
    "aarch64": "arm64",
    "arm64": "arm64",
    "arm64e": "arm64",
    "apple silicon": "arm64",
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "x86-64": "x86_64",
    "i386": "x86",
    "i686": "x86",
    "x86": "x86",
    "win32": "x86",
    "armv7l": "armv7",
    "armv7": "armv7",
    "armhf": "armv7",
    "universal": "universal",
    "universal2": "universal",
    "fat": "universal",
    "any": "universal",
    "all": "universal",
    "*": "universal",
}

#: The same for operating systems, so ``platform`` and ``os`` agree.
_OS_ALIASES = {
    "mac": "mac",
    "macos": "mac",
    "macosx": "mac",
    "osx": "mac",
    "os x": "mac",
    "darwin": "mac",
    "apple": "mac",
    "windows": "windows",
    "win": "windows",
    "win32": "windows",
    "win64": "windows",
    "linux": "linux",
    "gnu/linux": "linux",
    "none": "none",
}


def normalise_arch(value: str) -> str:
    """Canonical architecture name for ``arch=`` and for this machine.

    Unknown names are passed through unchanged, so a future architecture can be
    targeted by naming it the way ``platform.machine()`` reports it.
    """
    text = str(value or "").strip().casefold().replace(" ", "")
    if not text:
        return ""
    return _ARCH_ALIASES.get(text, text)


def current_arch() -> str:
    """This build's CPU architecture, canonicalised for ``arch=``."""
    try:
        return normalise_arch(_platform.machine())
    except Exception:
        return ""


def _normalise_os(value: str) -> str:
    """Canonical operating-system name for ``os=`` / ``platform=``."""
    text = str(value or "").strip().casefold()
    return _OS_ALIASES.get(text, text)


def _parse_device_types(value: str | None) -> tuple[str, ...]:
    """``device_type="A"`` / ``"Type A, B"`` to ``("A", "B")``."""
    types: list[str] = []
    for part in str(value or "").replace(";", ",").split(","):
        text = part.strip()
        if not text:
            continue
        if text.lower().startswith("type"):
            pieces = text.split()
            text = pieces[-1] if pieces else ""
        if text:
            types.append(text.upper())
    return tuple(types)


def current_platform() -> str:
    """Platform name used by ``platform=`` in notice.xml."""
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return ""


def _parse_bool(val: str | None) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes", "persistent")


_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _parse_timestamp(val: str | None) -> Optional[datetime]:
    """Parse a posting time: ``YYYY-MM-DD`` or ``YYYY-MM-DDTHH:MM[:SS]``."""
    if not val:
        return None
    text = val.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _parse_date(val: str | None) -> Optional[date]:
    parsed = _parse_timestamp(val)
    return parsed.date() if parsed else None


def is_battery_reminder(notice: Notice) -> bool:
    """True for a "charge the battery" notice.

    Select Software already carries the app's own battery reminder toast, so a
    feed notice that repeats it does not need to appear in the Notices list or
    arrive as a system notification. Any notice that both names the battery and
    asks for a charge is treated as that reminder.
    """
    text = f"{notice.title} {notice.content} {notice.full_content}".lower()
    return "battery" in text and "charg" in text


def parse_notice_xml(xml_content: str) -> list[Notice]:
    """Parse notice.xml string into a list of Notice objects."""
    if not xml_content or not xml_content.strip():
        return []
    try:
        root = ET.fromstring(xml_content)
    except Exception as e:
        logger.debug("Failed to parse notice XML: %s", e)
        return []

    notices: list[Notice] = []
    seen_ids: dict[str, int] = {}
    # Find all <notice> elements (under root or nested)
    elements = root.findall(".//notice") if root.tag != "notice" else [root]
    for el in elements:
        notice_id = el.get("id", "").strip()
        if not notice_id:
            continue
        count = seen_ids.get(notice_id, 0)
        seen_ids[notice_id] = count + 1
        if count > 0:
            notice_id = f"{notice_id}_{count}"

        # Title and card copy are normally child elements, but accepting the
        # attributes too keeps compact one-line notices valid.
        title = (el.get("title") or "").strip()
        if not title:
            title_el = el.find("title")
            title = title_el.text.strip() if title_el is not None and title_el.text else ""

        # Content (supports <content>, <body>, or a content="..." attribute)
        content = (el.get("content") or "").strip()
        if not content:
            content_el = el.find("content")
            if content_el is None:
                content_el = el.find("body")
            content = content_el.text.strip() if content_el is not None and content_el.text else ""

        if not title and not content:
            continue

        # Persistence boolean (attribute on <notice> or child element)
        persistent = _parse_bool(el.get("persistent"))
        persistent_el = el.find("persistent")
        if persistent_el is not None and persistent_el.text:
            persistent = _parse_bool(persistent_el.text)

        severity = el.get("severity", "info").strip().lower()
        if severity not in ("info", "warning", "critical"):
            severity = "info"

        # Release notices can say what they are, so the notification settings
        # can keep player releases and app releases apart from announcements.
        kind_raw = (el.get("type") or "").strip().lower().replace("-", "_")
        kind = kind_raw if kind_raw in ("player_update", "app_update") else ""

        # The feed historically used child elements, but a hand-written notice
        # can carry the same field as an attribute. Accept both spellings; the
        # Markdown still belongs to full_content rather than the card preview.
        full_content = (el.get("full_content") or "").strip()
        if not full_content:
            full_el = el.find("full_content")
            full_content = full_el.text.strip() if full_el is not None and full_el.text else ""
        if not full_content:
            full_content = content

        url = (el.get("url") or "").strip()
        if not url:
            url_el = el.find("url")
            if url_el is None:
                url_el = el.find("link")
            url = url_el.text.strip() if url_el is not None and url_el.text else ""

        if url:
            from urllib.parse import quote, urlsplit

            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower()
            if host == "innioasis.app" or host.endswith(".innioasis.app"):
                path = parsed.path.strip("/")
                is_bare_root = not path and not parsed.query and not parsed.fragment
                is_generic_notices = path == "notices.html" and "notice=" not in parsed.query
                if (is_bare_root or is_generic_notices) and notice_id:
                    url = f"https://innioasis.app/notices.html?notice={quote(notice_id)}"

        from_d = _parse_timestamp(el.get("from"))
        until_d = _parse_date(el.get("until"))

        models_raw = el.get("models", "")
        models = tuple(m.strip() for m in models_raw.split(",") if m.strip())
        # ``os=`` and ``platform=`` are one axis: merge them so a feed can use
        # whichever spelling reads better, or both.
        platforms_raw = ",".join(
            part for part in (el.get("platform"), el.get("os")) if part
        )
        platforms = tuple(
            dict.fromkeys(
                _normalise_os(p) for p in platforms_raw.split(",") if p.strip()
            )
        )
        arches = tuple(
            dict.fromkeys(
                normalise_arch(a)
                for a in (el.get("arch") or "").replace(";", ",").split(",")
                if a.strip()
            )
        )
        # Contextual targeting: hardware revision, firmware repository and
        # release tag(s). They only narrow the Select Software card; the
        # Notices list keeps showing every active notice.
        device_types = _parse_device_types(el.get("device_type"))
        firmware_repo = (el.get("firmware_repo") or "").strip()
        firmware_versions = tuple(
            part.strip()
            for part in (el.get("firmware_version") or "").split(",")
            if part.strip()
        )

        notice = Notice(
            id=notice_id,
            title=title,
            content=content,
            persistent=persistent,
            severity=severity,
            kind=kind,
            full_content=full_content,
            url=url,
            from_date=from_d,
            until_date=until_d,
            models=models,
            platforms=platforms,
            arches=arches,
            device_types=device_types,
            firmware_repo=firmware_repo,
            firmware_versions=firmware_versions,
        )
        notice.source_fingerprint = notice_fingerprint(notice)
        # The app already carries the one-time battery toast on Select
        # Software. A feed notice repeating it is a separate thing and never
        # enters the notice system at all.
        if is_battery_reminder(notice):
            continue
        notices.append(notice)
    return notices


class NoticeService(QObject):
    """Background service to fetch, cache, and manage notice.xml announcements."""

    notice_ready = Signal(object)  # Emits Notice or None
    notices_ready = Signal(object)  # Emits list[Notice] of every active notice (for the list)
    # A refresh is a background fetch; the pages that show notices use these to
    # run a throbber for exactly as long as one is in flight.
    refresh_started = Signal()
    refresh_finished = Signal()
    # A refresh runs on a worker thread. Emitting the notice signals there is
    # fine (widgets receive them queued), but the card's rotation timer belongs
    # to this service's thread, so its sync is queued separately.
    _rotation_sync_requested = Signal(int)

    _instance: NoticeService | None = None

    @classmethod
    def instance(cls) -> NoticeService:
        if cls._instance is None:
            cls._instance = NoticeService()
        return cls._instance

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._cache_dir = app_cache_dir()
        self._cache_file = self._cache_dir / "notice.xml"
        self._session_dismissed: set[str] = set()
        self._session_dismissed_fingerprints: dict[str, str] = {}
        self._banner_dismissed: set[str] = set()
        self._active_notices: list[Notice] = []
        # Hash of the XML behind ``_active_notices``. A refresh that returns
        # the same document must not translate and re-emit it a second time.
        self._source_hash = ""
        self._local_notices: dict[str, Notice] = {}
        # The Select Software selection: model, hardware revision, and the
        # firmware the reader is looking at. Logged devices are read live from
        # the install records instead of being copied here.
        self._current_model: str = ""
        self._current_type: str = ""
        self._current_repo: str = ""
        self._current_version: str = ""
        self._rotation_index = 0
        self._rotation_timer = None
        self._fetch_lock = threading.Lock()
        self._fetch_inflight = False
        self._rotation_sync_requested.connect(
            self._sync_rotation_timer, Qt.ConnectionType.QueuedConnection
        )
        try:
            snapshot = self._snapshot_text()
            if snapshot:
                from .i18n import current_language

                self._active_notices = self._localised(
                    parse_notice_xml(snapshot), current_language(), cached_only=True
                )
                self._source_hash = _notice_content_hash(snapshot)
        except Exception:
            pass

    @property
    def active_notices(self) -> list[Notice]:
        """Notices currently active and not dismissed, across all models."""
        dismissed_past = self.get_dismissed_ids()
        dismissed_fingerprints = self.get_dismissed_fingerprints()
        platform = current_platform()
        visible: list[Notice] = []
        all_candidates = [
            *getattr(self, "_local_notices", {}).values(),
            *self._active_notices,
        ]
        for notice in all_candidates:
            if is_battery_reminder(notice):
                # The dedicated toast covers this; do not repeat it as a
                # notice or notify about it.
                continue
            fingerprint = notice_fingerprint(notice)
            compound_key = f"{notice.id}::{fingerprint}"
            if notice.id in self._session_dismissed or compound_key in self._session_dismissed:
                # A changed notice is new again even within the same session;
                # an unchanged one (including a translated copy) stays hidden.
                stored = self._session_dismissed_fingerprints.get(notice.id, "") or self._session_dismissed_fingerprints.get(compound_key, "")
                if not stored or stored == fingerprint:
                    continue
                self._session_dismissed.discard(notice.id)
                self._session_dismissed.discard(compound_key)
                self._session_dismissed_fingerprints.pop(notice.id, None)
                self._session_dismissed_fingerprints.pop(compound_key, None)
            if not notice.persistent and (notice.id in dismissed_past or compound_key in dismissed_past):
                # A legacy dismissal has no digest: keep it dismissed. Once a
                # notice has a digest, only the same words stay hidden.
                stored = dismissed_fingerprints.get(notice.id, "") or dismissed_fingerprints.get(compound_key, "")
                if not stored or stored == fingerprint:
                    continue
            # Context is not applied here: the full list is everyone's.
            if not notice.is_live(platform=platform):
                continue
            visible.append(notice)
        return visible

    def _snapshot_text(self) -> str:
        """The newest local notice.xml: this install's cache, then the bundled copy."""
        try:
            if self._cache_file.exists():
                text = self._cache_file.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    return text
        except OSError:
            pass
        try:
            from . import online_data

            return online_data.read_text("notice.xml") or ""
        except Exception:
            return ""

    def get_dismissed_ids(self) -> set[str]:
        """IDs of non-persistent notices that were dismissed in past runs."""
        ids: set[str] = set()
        for org, app in (("Innioasis", "Innioasis Updater"), ("innioasis", "updater")):
            s = QSettings(org, app)
            val = s.value(_SETTINGS_DISMISSED_KEY, [])
            if isinstance(val, str) and val:
                ids.update(val.split(","))
            elif isinstance(val, (list, tuple, set)):
                ids.update(str(x) for x in val if x)
        return ids

    def set_dismissed_ids(self, ids: set[str]) -> None:
        for org, app in (("Innioasis", "Innioasis Updater"), ("innioasis", "updater")):
            s = QSettings(org, app)
            if ids:
                s.setValue(_SETTINGS_DISMISSED_KEY, sorted(ids))
            elif hasattr(s, "remove"):
                # Remove rather than store an empty array: macOS keeps the old
                # value when an empty list is written, which made a cleared
                # dismissal come back on the next launch.
                s.remove(_SETTINGS_DISMISSED_KEY)
            else:
                s.setValue(_SETTINGS_DISMISSED_KEY, [])
            sync = getattr(s, "sync", None)
            if callable(sync):
                sync()

    def get_dismissed_fingerprints(self) -> dict[str, str]:
        """Digest of each dismissed notice's wording, keyed by notice id."""
        fingerprints: dict[str, str] = {}
        for org, app in (("Innioasis", "Innioasis Updater"), ("innioasis", "updater")):
            raw = QSettings(org, app).value(_SETTINGS_DISMISSED_FINGERPRINTS_KEY, "")
            if not raw:
                continue
            try:
                data = json.loads(str(raw))
                if isinstance(data, dict):
                    fingerprints.update({str(k): str(v) for k, v in data.items() if k and v})
            except Exception:
                pass
        return fingerprints

    def set_dismissed_fingerprints(self, values: dict[str, str]) -> None:
        for org, app in (("Innioasis", "Innioasis Updater"), ("innioasis", "updater")):
            s = QSettings(org, app)
            if values:
                s.setValue(_SETTINGS_DISMISSED_FINGERPRINTS_KEY, json.dumps(values, sort_keys=True))
            elif hasattr(s, "remove"):
                s.remove(_SETTINGS_DISMISSED_FINGERPRINTS_KEY)
            else:
                s.setValue(_SETTINGS_DISMISSED_FINGERPRINTS_KEY, "")
            sync = getattr(s, "sync", None)
            if callable(sync):
                sync()

    def reset_all_read_state(self) -> None:
        """Reset all dismissed and read states so every notice can be re-read."""
        self.set_dismissed_ids(set())
        self.set_dismissed_fingerprints({})
        self._session_dismissed.clear()
        self._session_dismissed_fingerprints.clear()
        self._banner_dismissed.clear()
        for org, app in (
            ("innioasis", "updater"),
            ("Innioasis", "UpdaterCE"),
            ("Innioasis", "Innioasis Updater"),
        ):
            st = QSettings(org, app)
            st.remove(_SETTINGS_DISMISSED_KEY)
            st.remove(_SETTINGS_DISMISSED_FINGERPRINTS_KEY)
            st.remove("notified_notice_ids")
            sync = getattr(st, "sync", None)
            if callable(sync):
                sync()
        try:
            snapshot = self._snapshot_text()
            if snapshot:
                from .i18n import current_language

                self._active_notices = self._localised(
                    parse_notice_xml(snapshot), current_language(), cached_only=True
                )
                self._source_hash = _notice_content_hash(snapshot)
        except Exception as e:
            logger.debug("Failed re-parsing snapshot during reset_all_read_state: %s", e)
        self._emit_next_notice()
        self.fetch_async(force=True)

    def dismiss_for_session(self, notice: Notice) -> None:
        """Hide a notice from the Select Software banner only.

        Nothing is written to settings, so the notice stays in the Notices
        list and comes back on the next launch.
        """
        self._banner_dismissed.add(notice_fingerprint(notice))
        self._emit_next_notice()

    def dismiss_notice(self, notice: Notice) -> None:
        """Dismiss a notice for good from the Notices list (its X).

        If persistent=False, it is recorded in QSettings so it never returns
        on subsequent app runs (unless remote XML changes).
        If persistent=True, it is dismissed only for the current app session.
        """
        fp = notice_fingerprint(notice)
        compound_key = f"{notice.id}::{fp}"
        self._session_dismissed.add(notice.id)
        self._session_dismissed.add(compound_key)
        self._session_dismissed_fingerprints[notice.id] = fp
        self._session_dismissed_fingerprints[compound_key] = fp
        if not notice.persistent:
            dismissed = self.get_dismissed_ids()
            dismissed.add(notice.id)
            dismissed.add(compound_key)
            self.set_dismissed_ids(dismissed)
            fingerprints = self.get_dismissed_fingerprints()
            fingerprints[notice.id] = fp
            fingerprints[compound_key] = fp
            self.set_dismissed_fingerprints(fingerprints)
        self._emit_next_notice()

    def post_local_notice(
        self,
        notice_id: str,
        title: str,
        content: str,
        *,
        full_content: str = "",
        severity: str = "info",
        url: str = "",
        persistent: bool = True,
        models: tuple[str, ...] = (),
        firmware_repo: str = "",
    ) -> Notice:
        """Create or update a local (personal) notice shown in this user's Notices."""
        cid = (notice_id or "").strip() or "local-notice"
        body = (content or "").strip()
        full = (full_content or body).strip()
        fp = hashlib.sha256(
            f"{cid}\n{title}\n{body}\n{full}".encode("utf-8")
        ).hexdigest()[:16]
        notice = Notice(
            id=cid,
            title=(title or "").strip(),
            content=body,
            full_content=full,
            severity=severity if severity in ("info", "warning", "critical") else "info",
            persistent=bool(persistent),
            url=(url or "").strip(),
            from_date=datetime.now(),
            models=tuple(m.strip().upper() for m in models if m and m.strip()),
            firmware_repo=(firmware_repo or "").strip(),
            source_fingerprint=fp,
            local=True,
        )
        if not hasattr(self, "_local_notices"):
            self._local_notices = {}
        self._local_notices[cid] = notice
        self._emit_next_notice()
        return notice

    def remove_local_notice(self, notice_id: str) -> None:
        """Remove a local (personal) notice by id if present."""
        cid = (notice_id or "").strip()
        local_map = getattr(self, "_local_notices", None)
        if cid and local_map and cid in local_map:
            local_map.pop(cid, None)
            self._emit_next_notice()

    def clear_local_notices(self) -> None:
        """Remove all local (personal) notices."""
        local_map = getattr(self, "_local_notices", None)
        if local_map:
            local_map.clear()
            self._emit_next_notice()

    @property
    def local_notices(self) -> list[Notice]:
        """All registered local (personal) notices."""
        return list(getattr(self, "_local_notices", {}).values())

    def set_model(self, model: str) -> None:
        """Back-compat: update the target model and re-evaluate the card."""
        self.set_context(model=model)

    def set_context(
        self,
        model=_UNSET,
        *,
        device_type=_UNSET,
        firmware_repo=_UNSET,
        firmware_version=_UNSET,
    ) -> None:
        """Tell the service what the reader is looking at right now.

        Only the axes passed are changed, so a model change can clear the
        firmware context without forgetting the rest. A selection is a good
        moment to surface something relevant: the card jumps to the notice
        that best matches the new context, then resumes its rotation.
        """
        if model is not _UNSET:
            self._current_model = (model or "").strip()
        if device_type is not _UNSET:
            self._current_type = (device_type or "").strip()
        if firmware_repo is not _UNSET:
            self._current_repo = (firmware_repo or "").strip()
        if firmware_version is not _UNSET:
            self._current_version = (firmware_version or "").strip()
        self._rotation_index = 0
        self._emit_next_notice()

    def refresh_context(self) -> None:
        """Re-read logged devices and republish (after adding or installing one)."""
        self._rotation_index = 0
        self._emit_next_notice()

    def tracked_contexts(self) -> list[NoticeContext]:
        """Devices logged for update reminders, manually or by an install.

        A notice about a player the reader owns should reach them even while
        another model is selected in the drop-down. The install record names
        the software and the tag it came from, which supplies the repository
        and version a ``firmware_repo`` / ``firmware_version`` notice needs.
        """
        try:
            from . import player_update_check

            installs = player_update_check.get_all_device_installs()
        except Exception:
            logger.debug("Could not read logged devices for notices", exc_info=True)
            return []
        contexts: list[NoticeContext] = []
        for key, record in (installs or {}).items():
            rec = record or {}
            model = str(rec.get("model") or str(key).split("_")[0] or "").strip().upper()
            if not model:
                continue
            try:
                if not player_update_check.is_device_reminder_enabled(model):
                    continue
            except Exception:
                pass
            contexts.append(
                NoticeContext(
                    model=model,
                    device_type=str(rec.get("hardware_type") or "").strip(),
                    firmware_repo=self._repo_for_software(model, rec.get("software_name") or ""),
                    firmware_version=str(rec.get("tag_name") or "").strip(),
                )
            )
        return contexts

    @staticmethod
    def _repo_for_software(model: str, software: str) -> str:
        """The catalogue repository behind a logged install's software name."""
        if not model or not software:
            return ""
        try:
            from . import catalog

            packages = catalog.packages_for_model_software(model, software)
            return str(getattr(packages[0], "repo", "") or "") if packages else ""
        except Exception:
            return ""

    def fetch_async(self, force: bool = True) -> None:
        """Fetch remote notice.xml in a background thread, never concurrently."""
        with self._fetch_lock:
            if self._fetch_inflight:
                return
            self._fetch_inflight = True
        self.refresh_started.emit()
        try:
            thread = threading.Thread(target=self._fetch_worker, args=(force,), daemon=True)
            thread.start()
        except Exception:
            with self._fetch_lock:
                self._fetch_inflight = False
            self.refresh_finished.emit()
            raise

    def start_passive_refresh(self, interval_ms: int = 60_000) -> None:
        """Re-check the feed on a calm interval.

        Notices change rarely, so this is minutes-scale background work rather
        than polling: the readers sees a throbber only when they are looking at
        the notices page at that moment.
        """
        from PySide6.QtCore import QTimer

        if getattr(self, "_passive_timer", None) is None:
            self._passive_timer = QTimer(self)
            self._passive_timer.timeout.connect(lambda: self.fetch_async(force=True))
        self._passive_timer.start(max(60_000, int(interval_ms)))

    def _fetch_worker(self, force: bool = True) -> None:
        """Run a fetch and always report completion, whatever happened."""
        try:
            self._fetch_worker_inner(force=force)
        except Exception as e:
            logger.debug("Notice fetch worker encountered exception: %s", e)
        finally:
            with self._fetch_lock:
                self._fetch_inflight = False
            try:
                self.refresh_finished.emit()
            except RuntimeError:
                pass

    def _fetch_worker_inner(self, force: bool = True) -> None:
        from .i18n import current_language, is_english_language

        lang = current_language()
        # 1. Show the newest local copy first -- this install's cache, else the
        #    snapshot built into the app -- so launch waits for neither the
        #    network nor Google Translate (translations come from the disk cache).
        local_content = self._snapshot_text()
        if local_content:
            try:
                self._active_notices = self._localised(
                    parse_notice_xml(local_content), lang, cached_only=True
                )
                self._source_hash = _notice_content_hash(local_content)
            except Exception as e:
                logger.debug("Local notice.xml could not be read: %s", e)
            self._emit_next_notice()

        # 2. Replace the local copy only when it is stale. The app-wide cache is
        #    shared with the catalogue, donors and blocklist, so one refresh
        #    keeps every online file current; a failed fetch keeps what we have.
        try:
            from . import online_data

            # A notice is tiny, and the point of the poll is to discover a
            # newly committed notice without waiting out the file's TTL.
            online_data.refresh("notice.xml", force=True)
            remote_content = online_data.cached_text("notice.xml") or ""
        except Exception as e:
            logger.debug("Notice refresh failed: %s", e)
            return
        if not remote_content:
            return

        remote_hash = _notice_content_hash(remote_content)
        if remote_hash == self._source_hash:
            # The feed is byte-for-byte what is already on screen. Do not run
            # translation again or emit a second identical notice list: a
            # periodic poll and the connectivity refresh commonly overlap.
            return

        content_changed = remote_content.strip() != local_content.strip()
        if content_changed:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._cache_file.write_text(remote_content, encoding="utf-8")
            except Exception as e:
                logger.debug("Failed writing notice.xml to cache: %s", e)

            # Dismissals follow notices, not the file: a new notice has a new
            # id and appears, while every notice already dismissed stays out
            # of the list even when the feed around it changes.

        # Translate new or changed notices (cached per notice and language), then show them.
        self._active_notices = self._localised(parse_notice_xml(remote_content), lang)
        self._source_hash = remote_hash
        self._emit_next_notice()

    def reload_translations(self) -> None:
        """Re-render the cached notices in the language just selected.

        Runs off the UI thread because a first-time translation may take a
        moment; the result arrives through ``notices_ready`` like a refresh.
        """
        thread = threading.Thread(target=self._reload_translations_worker, daemon=True)
        thread.start()

    def _reload_translations_worker(self) -> None:
        from .i18n import current_language

        try:
            text = self._snapshot_text()
            if not text.strip():
                return
            translated = self._localised(parse_notice_xml(text), current_language())
            self._active_notices = translated
            self._emit_next_notice()
        except Exception:
            logger.debug("Could not re-render notices for the new language", exc_info=True)
        finally:
            self.refresh_finished.emit()

    def _localised(self, notices: list[Notice], lang: str, cached_only: bool = False) -> list[Notice]:
        """Return notices with title/content in ``lang`` (Google Translate, disk-cached).

        ``cached_only`` uses only the translation cache so launch never waits on the network.
        """
        from .i18n import is_english_language
        from .translate import fetch_google_translation

        if is_english_language(lang):
            return notices
        out: list[Notice] = []
        for n in notices:
            out.append(self._translation_for(n, lang, cached_only, fetch_google_translation))
        return out

    def _translation_for(self, n: Notice, lang: str, cached_only: bool, translate) -> Notice:
        """Return ``n`` with its text in ``lang``, cached on disk per notice and language."""
        key = hashlib.sha256(
            f"{n.id}\n{n.title}\n{n.content}\n{n.full_content}".encode("utf-8")
        ).hexdigest()[:24]
        path = self._cache_dir / "notice_translations" / f"{lang}_{key}.json"
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                return replace(n, title=data["title"], content=data["content"], full_content=data["full_content"])
        except Exception:
            logger.debug("Unreadable notice translation cache", exc_info=True)
        if cached_only:
            return n

        def _call_translate(text: str) -> str:
            if not text:
                return text
            try:
                return translate(text, lang, raise_on_error=True)
            except TypeError:
                return translate(text, lang)

        try:
            title = _call_translate(n.title) if n.title else n.title
            content = _call_translate(n.content) if n.content else n.content
            full = _call_translate(n.full_content) if n.full_content else n.full_content
        except Exception:
            logger.debug("Notice translation unavailable for %s (%s)", n.id, lang)
            return n
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"title": title, "content": content, "full_content": full}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("Could not cache notice translation", exc_info=True)
        return replace(n, title=title, content=content, full_content=full)

    def replay(self) -> None:
        """Emit the current state to a listener that connected late.

        Select Software starts the first fetch while the window is still being
        built, so the initial answer can arrive before the rail connects. A
        replay gives that listener the real list instead of an empty one.
        """
        self._emit_next_notice()

    def selection_context(self) -> NoticeContext:
        """The context the Select Software screen last reported."""
        return NoticeContext(
            model=self._current_model,
            device_type=self._current_type,
            firmware_repo=self._current_repo,
            firmware_version=self._current_version,
        )

    def contextual_notices(self) -> list[Notice]:
        """Active notices that apply to this reader, most specific first.

        This is the Select Software card's set, not the Notices list: the list
        (and the pop-out list, and the tray) keeps showing every active notice
        so an announcement meant for everyone is never hidden by context.
        """
        return self._banner_candidates()

    def _contexts(self) -> list[NoticeContext]:
        """The selected context plus every device logged for reminders."""
        contexts: list[NoticeContext] = []
        selected = self.selection_context()
        if not selected.is_empty():
            contexts.append(selected)
        for context in self.tracked_contexts():
            if context not in contexts:
                contexts.append(context)
        return contexts

    def _banner_candidates(self, visible: list[Notice] | None = None) -> list[Notice]:
        """What the card may show, ordered by how well it fits this reader."""
        notices = self.active_notices if visible is None else list(visible)
        contexts = self._contexts()
        platform = current_platform()
        ranked: list[tuple[int, int, Notice]] = []
        for position, notice in enumerate(notices):
            if notice_fingerprint(notice) in self._banner_dismissed:
                continue
            best = -1
            for context in contexts:
                score = notice.context_score(context, platform)
                if score >= 0:
                    best = max(best, score)
            # Without any context (first launch) the card still has to say
            # something, so every notice is offered until a model is known.
            if contexts and best < 0:
                continue
            ranked.append((-max(best, 0), position, notice))
        ranked.sort(key=lambda row: (row[0], row[1]))
        return [row[2] for row in ranked]

    def _rotate_notice(self) -> None:
        """Show the next applicable notice on the card, one at a time."""
        candidates = self._banner_candidates()
        if not candidates:
            self.notice_ready.emit(None)
            self._sync_rotation_timer(0)
            return
        self._rotation_index = (self._rotation_index + 1) % len(candidates)
        self.notice_ready.emit(candidates[self._rotation_index])

    def _sync_rotation_timer(self, count: int) -> None:
        """Rotate only while more than one notice applies.

        A background refresh reaches this from its worker thread, where Qt
        refuses to create or start the timer (and the card then stayed on one
        notice for the rest of the session). Queueing the sync puts it back on
        the thread that owns the timer.
        """
        from PySide6.QtCore import QCoreApplication, QThread, QTimer

        if QCoreApplication.instance() is None:
            return
        if QThread.currentThread() != self.thread():
            self._rotation_sync_requested.emit(int(count))
            return
        if count < 2:
            if self._rotation_timer is not None:
                self._rotation_timer.stop()
            return
        if self._rotation_timer is None:
            self._rotation_timer = QTimer(self)
            self._rotation_timer.setInterval(NOTICE_ROTATION_MS)
            self._rotation_timer.timeout.connect(self._rotate_notice)
        if not self._rotation_timer.isActive():
            self._rotation_timer.start()

    def listed_notices(self) -> list[Notice]:
        """Every active notice for the Notices list, tray and badge.

        ``active_notices`` already applies the date window plus the platform and
        architecture filters in ``Notice.is_live``. The list must not also apply
        the contextual model/revision/release targets: those narrow only the
        Select Software card. Applying them here hid every compatibility
        warning whenever the selected release was not one the warning named,
        and the Notices page then reported zero even though the feed contained
        live entries.
        """
        return list(self.active_notices)

    def _emit_next_notice(self) -> None:
        try:
            from .remote_flags import is_remote_flag_active
            if is_remote_flag_active("notices_off"):
                self.notices_ready.emit([])
                self.notice_ready.emit(None)
                self._sync_rotation_timer(0)
                return
        except Exception:
            pass
        # The list, pop-out and tray see the notices that fit this reader;
        # the card gets one of them, chosen and rotated through the rest.
        visible = self.active_notices
        self.notices_ready.emit(self.listed_notices())
        candidates = self._banner_candidates(visible)
        if self._rotation_index >= len(candidates):
            self._rotation_index = 0
        self.notice_ready.emit(candidates[self._rotation_index] if candidates else None)
        self._sync_rotation_timer(len(candidates))
