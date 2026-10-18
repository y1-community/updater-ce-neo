"""Remote blocklist of unsafe firmware releases (``release_blocklist.csv``).

The CSV is served from ``https://innioasis.app/release_blocklist.csv`` so a
release can be blocked without shipping an app update. Columns:

    software,model,type,version,except,reason

* ``software`` - firmware name as in ``slidia_manifest.xml`` (case-insensitive).
* ``model``    - ``Y1`` / ``Y2`` / ``A5`` (case-insensitive); blank matches any.
* ``type``     - hardware type ``A`` or ``B`` (``Type A`` also accepted); blank any.
* ``version``  - ``<X``, ``<=X``, ``>X``, ``>=X``, ``X-Y`` (inclusive range) or a
                 bare ``X`` (exact). Blank matches every version.
* ``except``   - ``nightly`` leaves untagged/nightly builds unblocked.
* ``reason``   - free text, for maintainers.

Every failure degrades to "no rules" (never raises): the app keeps listing
releases, the same as before the blocklist existed.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .network import requests_module

logger = logging.getLogger(__name__)

BLOCKLIST_URL = "https://innioasis.app/release_blocklist.csv"
REQUEST_TIMEOUT = 12
CACHE_FILE_NAME = "release_blocklist.csv"
REQUIRED_COLUMNS = ("software", "model", "type", "version")

_VERSION_RE = re.compile(
    r"^\s*(?P<op><=|>=|<|>)?\s*v?(?P<a>\d+(?:\.\d+)*)"
    r"(?:\s*-\s*v?(?P<b>\d+(?:\.\d+)*))?\s*$"
)


def _version_tuple(text: str) -> Optional[tuple]:
    m = re.search(r"\d+(?:\.\d+)*", str(text or "").lstrip("vV"))
    if not m:
        return None
    return tuple(int(p) for p in m.group(0).split("."))


def _pad(a: tuple, b: tuple) -> tuple:
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)), b + (0,) * (width - len(b))


def _norm_type(value: str) -> str:
    return re.sub(r"(?i)^\s*type\s*", "", str(value or "")).strip().upper()


@dataclass(frozen=True)
class BlockRule:
    software: str
    model: str
    hw_type: str
    op: str  # "", "<", "<=", ">", ">=", "range", "eq"
    low: Optional[tuple]
    high: Optional[tuple]
    exempt_nightly: bool
    reason: str = ""

    def matches(self, software: str, model: str, hw_type: str, tag: str, is_nightly: bool) -> bool:
        if self.software and self.software.lower() != str(software or "").strip().lower():
            return False
        if self.model and self.model.upper() != str(model or "").strip().upper():
            return False
        if self.hw_type and self.hw_type != _norm_type(hw_type):
            return False
        if self.exempt_nightly and is_nightly:
            return False
        if not self.op:
            return True
        if self.op == "tag":
            return str(tag or "").strip().lower() == str(self.low).strip().lower()
        version = _version_tuple(tag)
        if version is None:
            return False
        if self.op == "range":
            width = max(len(version), len(self.low), len(self.high))
            pad = lambda t: t + (0,) * (width - len(t))
            return pad(self.low) <= pad(version) <= pad(self.high)
        v, bound = _pad(version, self.low)
        return {
            "<": v < bound,
            "<=": v <= bound,
            ">": v > bound,
            ">=": v >= bound,
            "eq": v == bound,
        }[self.op]


def _parse_version_field(text: str):
    """Return (op, low, high) for a version qualifier, or None when invalid."""
    text = (text or "").strip()
    if not text:
        return ("", None, None)
    # A quoted value is an exact tag name, for releases without semver tags.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        tag = text[1:-1].strip()
        return ("tag", tag, None) if tag else None
    m = _VERSION_RE.match(text)
    if not m:
        return None
    a = tuple(int(p) for p in m.group("a").split("."))
    if m.group("b"):
        if m.group("op"):
            return None
        b = tuple(int(p) for p in m.group("b").split("."))
        return ("range", a, b)
    if m.group("op"):
        return (m.group("op"), a, None)
    return ("eq", a, None)


def parse_blocklist_csv(text: str) -> list:
    """Parse the CSV into rules. Bad rows are skipped; never raises."""
    rules: list = []
    try:
        lines = [ln for ln in (text or "").splitlines() if ln.strip()]
        if not lines:
            return rules
        # Quotes are kept verbatim (the version column may be a quoted tag), and
        # ``reason`` is the last column so it may contain commas.
        fields = [f.strip().lower() for f in lines[0].split(",")]
        if not all(col in fields for col in REQUIRED_COLUMNS):
            logger.warning("release_blocklist.csv is missing required columns; ignoring it")
            return rules
        width = len(fields)
        for line in lines[1:]:
            if line.lstrip().startswith("#"):
                continue
            try:
                parts = [p.strip() for p in line.split(",", width - 1)]
                parts += [""] * (width - len(parts))
                row = dict(zip(fields, parts))
                parsed = _parse_version_field(row.get("version") or "")
                if parsed is None:
                    logger.warning("release_blocklist.csv: bad version %r; row skipped", row.get("version"))
                    continue
                op, low, high = parsed
                rules.append(
                    BlockRule(
                        software=(row.get("software") or "").strip(),
                        model=(row.get("model") or "").strip().upper(),
                        hw_type=_norm_type(row.get("type") or ""),
                        op=op,
                        low=low,
                        high=high,
                        exempt_nightly=(row.get("except") or "").strip().lower() == "nightly",
                        reason=(row.get("reason") or "").strip(),
                    )
                )
            except Exception:
                logger.debug("release_blocklist.csv row skipped", exc_info=True)
    except Exception:
        logger.warning("release_blocklist.csv could not be parsed", exc_info=True)
    return rules


def _cache_path() -> Path:
    from .manifest import _cache_root

    return _cache_root() / CACHE_FILE_NAME


def fetch_blocklist_text() -> Optional[str]:
    """Download the CSV. Returns None when the server cannot give a usable file."""
    try:
        resp = requests_module().get(BLOCKLIST_URL, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        text = resp.text
        # An HTML 404 page or a captive portal is not a blocklist.
        if "software" not in text[:200].lower():
            logger.info("release_blocklist.csv at innioasis.app is not a CSV; ignoring")
            return None
        return text
    except Exception as e:
        logger.info("Could not fetch release_blocklist.csv: %s", e)
        return None


def load_blocklist() -> list:
    """Fetch the live blocklist, falling back to the last good copy, then to none."""
    text = fetch_blocklist_text()
    if text is not None:
        try:
            _cache_path().write_text(text, encoding="utf-8")
        except Exception:
            logger.debug("Could not cache release_blocklist.csv", exc_info=True)
        return parse_blocklist_csv(text)
    try:
        cached = _cache_path()
        if cached.exists():
            return parse_blocklist_csv(cached.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("Could not read cached release_blocklist.csv", exc_info=True)
    return []


_RULES: Optional[list] = None


def current_rules() -> list:
    """Process-wide rules, loaded once. A failed load yields [] (nothing blocked)."""
    global _RULES
    if _RULES is None:
        try:
            _RULES = load_blocklist()
        except Exception:
            logger.warning("release blocklist load failed; nothing blocked", exc_info=True)
            _RULES = []
    return _RULES


def blocking_reason(rules: list, software: str, model: str, hw_type: str, tag: str, is_nightly: bool = False) -> Optional[str]:
    """The CSV ``reason`` of the first rule that blocks this release (None when not blocked)."""
    for rule in rules:
        if rule.matches(software, model, hw_type, tag, is_nightly):
            return rule.reason or ""
    return None


GOOGLE_TRANSLATE_URL = "https://translation.googleapis.com/language/translate/v2"
TRANSLATE_KEY_ENV = "GOOGLE_TRANSLATE_API_KEY"
_TRANSLATION_CACHE: dict = {}


def localize_text(text: str, lang: str) -> str:
    """Translate ``text`` into ``lang`` with Google Cloud Translation.

    English, a missing API key, or any network/API failure returns ``text``
    unchanged, so the warning always shows.
    """
    import os

    text = (text or "").strip()
    lang = (lang or "en").strip()
    if not text or lang.lower() in ("", "en"):
        return text
    cache_key = (text, lang)
    if cache_key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[cache_key]
    api_key = os.environ.get(TRANSLATE_KEY_ENV, "").strip()
    if not api_key:
        return text
    try:
        resp = requests_module().post(
            GOOGLE_TRANSLATE_URL,
            params={"key": api_key},
            json={"q": text, "target": lang, "format": "text"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        translated = resp.json()["data"]["translations"][0]["translatedText"]
    except Exception as e:
        logger.info("Translation of firmware warning failed: %s", e)
        return text
    _TRANSLATION_CACHE[cache_key] = translated
    return translated


WARNING_PREFIX = "This firmware is usually hidden due to reports from that community that: "


def warning_message(reason: str, lang: str = "en") -> str:
    """The user-facing warning for a hidden release, in ``lang`` when possible."""
    reason = (reason or "").strip() or "the release is reported as incompatible"
    return localize_text(WARNING_PREFIX + reason, lang)


def is_blocked(rules: list, software: str, model: str, hw_type: str, tag: str, is_nightly: bool = False) -> bool:
    """True when any rule blocks this release."""
    return any(r.matches(software, model, hw_type, tag, is_nightly) for r in rules)
