"""Remote notice fetching and caching service (innioasis.app/notice.xml).

Fetches announcements and public notices from https://innioasis.app/notice.xml,
caches them locally, and determines which notices should be shown to the user.

Notices support:
- title: headline text
- content: notice description (also supports body)
- persistent: bool - whether the notice persists across app runs even after
  dismissal (persistent=true returns on every app launch; persistent=false
  can be dismissed permanently).
- If the remote notice.xml contents have changed from the locally cached file,
  the new notice will be loaded and shown.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

from PySide6.QtCore import QObject, QSettings, Signal

from .paths import app_cache_dir

logger = logging.getLogger(__name__)

NOTICE_URL = "https://innioasis.app/notice.xml"
_SETTINGS_DISMISSED_KEY = "dismissed_notice_ids"
_SETTINGS_CACHED_HASH_KEY = "notice_xml_content_hash"


@dataclass
class Notice:
    id: str
    title: str
    content: str
    persistent: bool = False
    severity: str = "info"
    link: str = ""
    from_date: Optional[date] = None
    until_date: Optional[date] = None
    models: tuple[str, ...] = ()

    def is_active_for(self, model: str = "") -> bool:
        """Check if notice is within its date window and matches the given model."""
        today = date.today()
        if self.from_date and today < self.from_date:
            return False
        if self.until_date and today > self.until_date:
            return False
        if self.models and model:
            target = model.strip().upper()
            if not any(m.strip().upper() == target for m in self.models):
                return False
        return True


def _parse_bool(val: str | None) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes", "persistent")


def _parse_date(val: str | None) -> Optional[date]:
    if not val:
        return None
    try:
        return datetime.strptime(val.strip(), "%Y-%m-%d").date()
    except Exception:
        return None


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
    # Find all <notice> elements (under root or nested)
    elements = root.findall(".//notice") if root.tag != "notice" else [root]
    for el in elements:
        notice_id = el.get("id", "").strip()
        if not notice_id:
            continue

        # Title
        title_el = el.find("title")
        title = title_el.text.strip() if title_el is not None and title_el.text else ""

        # Content (supports <content> or <body>)
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

        link_el = el.find("link")
        link = link_el.text.strip() if link_el is not None and link_el.text else ""

        from_d = _parse_date(el.get("from"))
        until_d = _parse_date(el.get("until"))

        models_raw = el.get("models", "")
        models = tuple(m.strip() for m in models_raw.split(",") if m.strip())

        notices.append(
            Notice(
                id=notice_id,
                title=title,
                content=content,
                persistent=persistent,
                severity=severity,
                link=link,
                from_date=from_d,
                until_date=until_d,
                models=models,
            )
        )
    return notices


class NoticeService(QObject):
    """Background service to fetch, cache, and manage notice.xml announcements."""

    notice_ready = Signal(object)  # Emits Notice or None

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
        self._active_notices: list[Notice] = []
        self._current_model: str = ""

    def get_dismissed_ids(self) -> set[str]:
        """IDs of non-persistent notices that were dismissed in past runs."""
        s = QSettings("Innioasis", "Innioasis Updater")
        val = s.value(_SETTINGS_DISMISSED_KEY, [])
        if isinstance(val, str):
            return set(val.split(",")) if val else set()
        if isinstance(val, (list, tuple, set)):
            return set(str(x) for x in val if x)
        return set()

    def set_dismissed_ids(self, ids: set[str]) -> None:
        s = QSettings("Innioasis", "Innioasis Updater")
        s.setValue(_SETTINGS_DISMISSED_KEY, list(ids))

    def dismiss_notice(self, notice: Notice) -> None:
        """Dismiss a notice.

        If persistent=False, it is recorded in QSettings so it never returns
        on subsequent app runs (unless remote XML changes).
        If persistent=True, it is dismissed only for the current app session.
        """
        self._session_dismissed.add(notice.id)
        if not notice.persistent:
            dismissed = self.get_dismissed_ids()
            dismissed.add(notice.id)
            self.set_dismissed_ids(dismissed)
        self._emit_next_notice()

    def set_model(self, model: str) -> None:
        """Update target device model and re-evaluate active notice."""
        self._current_model = model or ""
        self._emit_next_notice()

    def fetch_async(self) -> None:
        """Fetch remote notice.xml in a background thread."""
        thread = threading.Thread(target=self._fetch_worker, daemon=True)
        thread.start()

    def _fetch_worker(self) -> None:
        # 1. Load local cached XML first if available
        cached_content = ""
        if self._cache_file.exists():
            try:
                cached_content = self._cache_file.read_text(encoding="utf-8", errors="ignore")
                self._active_notices = parse_notice_xml(cached_content)
                self._emit_next_notice()
            except Exception as e:
                logger.debug("Failed reading cached notice.xml: %s", e)

        # 2. Fetch remote notice.xml
        try:
            import urllib.request
            req = urllib.request.Request(
                NOTICE_URL,
                headers={"User-Agent": "InnioasisUpdater/2.5 (+https://innioasis.app)"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                remote_bytes = resp.read()
                remote_content = remote_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            logger.debug("Remote notice.xml fetch failed (offline or unavailable): %s", e)
            return

        # 3. Check if remote XML changed from locally cached file
        content_changed = remote_content.strip() != cached_content.strip()
        if content_changed:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                self._cache_file.write_text(remote_content, encoding="utf-8")
            except Exception as e:
                logger.debug("Failed writing notice.xml to cache: %s", e)

            # "if remote xml contents have changed from locally cached file then
            # it must be assumed that the new one will be loaded and shown"
            # -> Reset dismissed records so changed notices are shown again
            self.set_dismissed_ids(set())
            self._session_dismissed.clear()

            self._active_notices = parse_notice_xml(remote_content)
            self._emit_next_notice()

    def _emit_next_notice(self) -> None:
        dismissed_past = self.get_dismissed_ids()
        candidate: Notice | None = None
        for n in self._active_notices:
            if n.id in self._session_dismissed:
                continue
            if not n.persistent and n.id in dismissed_past:
                continue
            if not n.is_active_for(self._current_model):
                continue
            candidate = n
            break

        self.notice_ready.emit(candidate)
