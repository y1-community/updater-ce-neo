"""Every catalog string can be formatted with the fields its English text has.

Machine translation repeatedly ate the braces of a placeholder — ``<b>{model</b>``
— which made ``tr(...)`` fall back to English (the guard in ``i18n`` rejects a
renamed placeholder) or, for a lone brace, raised out of ``str.format`` and took
the screen with it. The donation lines were the visible symptom: they read in
English inside otherwise translated languages.
"""

import os
import re
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import i18n  # noqa: E402

FIELD = re.compile(r"\{([^{}\n]+)\}")


class _Blank:
    """Stands in for a formatted value, whatever the format spec asks for."""

    def __format__(self, spec):
        return ""

    def __str__(self):
        return ""


class _Values(dict):
    def __missing__(self, key):
        return _Blank()


def _fields(text):
    return {name.split(":")[0].strip() for name in FIELD.findall(text)}


def _codes_and_strings():
    for code in i18n.catalog_codes():
        if code == "en":
            continue
        for key, text in i18n.catalog(code).items():
            english = i18n._STRINGS.get(key, {}).get("en")
            if english and isinstance(text, str) and text.strip():
                yield code, key, english, text


def test_no_catalog_invents_a_placeholder():
    """A renamed placeholder is silently swapped for English at runtime."""
    invented = [
        (code, key, sorted(_fields(text) - _fields(english)))
        for code, key, english, text in _codes_and_strings()
        if _fields(text) - _fields(english)
    ]
    assert not invented, invented[:10]


def test_every_catalog_string_can_be_formatted():
    """A lone brace raises ``ValueError`` inside ``str.format``, not a fallback."""
    broken = []
    for code, key, english, text in _codes_and_strings():
        try:
            english.format_map(_Values())
        except Exception:  # pragma: no cover - the source is always formattable
            continue
        try:
            text.format_map(_Values())
        except Exception as exc:
            broken.append((code, key, f"{type(exc).__name__}: {exc}"))
    assert not broken, broken[:10]


def test_the_donation_ticker_keeps_its_values_everywhere():
    """The status bar donor line is the one the user saw untranslated."""
    fields = _fields(i18n._STRINGS["donate_ticker_status_fmt"]["en"])
    assert fields == {"anchor", "amount", "when"}
    for code, key, _english, text in _codes_and_strings():
        if key != "donate_ticker_status_fmt":
            continue
        assert _fields(text) >= {"anchor", "amount"}, (code, text)
