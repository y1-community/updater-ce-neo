"""Remote release blocklist for the text session, standard library only.

The window parses ``release_blocklist.csv`` in ``src/release_blocklist.py``.
That module imports the Qt-bound network helper, so the terminal payload cannot
import it. This is the same CSV contract and the same match rules, kept free of
Qt so an offline tarball can still honour a shipped blocklist.

Columns: ``software,model,type,version,except,reason``. A bad row is skipped;
every failure degrades to "no rules" rather than hiding the catalogue.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("software", "model", "type", "version")

_VERSION_RE = re.compile(
    r"^\s*(?P<op><=|>=|<|>)?\s*v?(?P<a>\d+(?:\.\d+)*)"
    r"(?:\s*-\s*v?(?P<b>\d+(?:\.\d+)*))?\s*$"
)


def _version_tuple(text: str):
    match = re.search(r"\d+(?:\.\d+)*", str(text or "").lstrip("vV"))
    if not match:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _pad(left: tuple, right: tuple):
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)), right + (0,) * (width - len(right))


def _normalise_type(value: str) -> str:
    return re.sub(r"(?i)^\s*type\s*", "", str(value or "")).strip().upper()


@dataclass(frozen=True)
class BlockRule:
    software: str
    model: str
    hw_type: str
    op: str
    low: tuple | None
    high: tuple | None
    exempt_nightly: bool
    reason: str = ""

    def matches(self, software: str, model: str, hw_type: str, tag: str, is_nightly: bool) -> bool:
        if self.software and self.software.lower() != str(software or "").strip().lower():
            return False
        if self.model and self.model.upper() != str(model or "").strip().upper():
            return False
        if self.hw_type and self.hw_type != _normalise_type(hw_type):
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
            left, low = _pad(version, self.low)
            _left, high = _pad(version, self.high)
            return low <= left <= high
        version, bound = _pad(version, self.low)
        return {
            "<": version < bound,
            "<=": version <= bound,
            ">": version > bound,
            ">=": version >= bound,
            "eq": version == bound,
        }[self.op]


def _parse_version_field(text: str):
    text = (text or "").strip()
    if not text:
        return ("", None, None)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        tag = text[1:-1].strip()
        return ("tag", tag, None) if tag else None
    match = _VERSION_RE.match(text)
    if not match:
        return None
    low = tuple(int(part) for part in match.group("a").split("."))
    if match.group("b"):
        if match.group("op"):
            return None
        high = tuple(int(part) for part in match.group("b").split("."))
        return ("range", low, high)
    if match.group("op"):
        return (match.group("op"), low, None)
    return ("eq", low, None)


def parse_blocklist_csv(text: str) -> list[BlockRule]:
    """Parse the CSV into rules. Bad rows are skipped; never raises."""
    rules: list[BlockRule] = []
    try:
        lines = [line for line in (text or "").splitlines() if line.strip()]
        if not lines:
            return rules
        fields = [field.strip().lower() for field in lines[0].split(",")]
        if not all(column in fields for column in REQUIRED_COLUMNS):
            logger.warning("release_blocklist.csv is missing required columns; ignoring it")
            return rules
        width = len(fields)
        for line in lines[1:]:
            if line.lstrip().startswith("#"):
                continue
            try:
                parts = [part.strip() for part in line.split(",", width - 1)]
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
                        hw_type=_normalise_type(row.get("type") or ""),
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
        logger.debug("release_blocklist.csv could not be parsed", exc_info=True)
    return rules


def is_blocked(rules, software: str, model: str, hw_type: str, tag: str, is_nightly: bool) -> bool:
    if not rules:
        return False
    return any(rule.matches(software, model, hw_type, tag, is_nightly) for rule in rules)
