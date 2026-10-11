#!/usr/bin/env python3
"""Generate the app's language catalogs from the English strings.

`src/i18n.py` holds the English source of truth plus the curated translations.
Everything else the app can display comes from ``assets/i18n/<code>.json`` —
one flat ``{string_id: text}`` file per language, generated here with Google
Translate and shipped inside the app bundle.

    # every language Google can translate English to (resumable)
    .venv-build/bin/python scripts/i18n_fill.py --all

    # a few languages, or one the app never shipped before
    .venv-build/bin/python scripts/i18n_fill.py --lang de ja nl

    # refresh the language list and their native names
    .venv-build/bin/python scripts/i18n_fill.py --labels

    # is every language complete? (offline, exit 1 when not)
    .venv-build/bin/python scripts/i18n_fill.py --check

English is the source: every string id is translated from its ``en`` entry.
Placeholders (``{size}``) and line breaks are verified after each batch; a
damaged string is retried on its own, and one whose line breaks moved has its
lines translated separately. Already-complete languages are skipped, so an
interrupted run is resumed by simply running it again.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOGS = REPO / "assets" / "i18n"
LANGUAGES_FILE = CATALOGS / "languages.json"
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.translate import _urlopen  # noqa: E402

BATCH_SIZE = 40
PAUSE_SECONDS = 0.15
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
TRANSLATE_URL = "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=en&tl={lang}"
LANGUAGE_LIST_URL = "https://translate.googleapis.com/translate_a/l?client=gtx&alpha=true&hl=en"


# ---------------------------------------------------------------------------
# Google Translate
# ---------------------------------------------------------------------------
def _post(url: str, texts, timeout: float):
    data = urllib.parse.urlencode([("q", text) for text in texts]).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=HEADERS)
    with _urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _one(text: str, lang: str, timeout: float) -> str:
    from src.translate import fetch_google_translation

    return fetch_google_translation(text, lang, timeout=timeout)


def _placeholders(text: str):
    return sorted(re.findall(r"\{[^}]+\}", text or ""))


def _repair(source: str, translated: str, lang: str, timeout: float) -> str:
    """Keep placeholders and line breaks; retry alone if the batch damaged it."""
    if not translated or translated == source:
        return _one(source, lang, timeout)
    if _placeholders(source) != _placeholders(translated):
        return _one(source, lang, timeout)
    if source.count("\n") != translated.count("\n"):
        lines = source.split("\n")
        done = [_one(line, lang, timeout) if line.strip() else line for line in lines]
        joined = "\n".join(done)
        if joined.strip() and _placeholders(source) == _placeholders(joined):
            return joined
        return _one(source, lang, timeout)
    return translated


def translate(texts, lang: str, *, timeout: float = 15.0, pause: float = PAUSE_SECONDS):
    """Translate English ``texts`` into ``lang``, one batched request per chunk."""
    out = []
    url = TRANSLATE_URL.format(lang=urllib.parse.quote(lang))
    for start in range(0, len(texts), BATCH_SIZE):
        chunk = texts[start : start + BATCH_SIZE]
        got = None
        try:
            data = _post(url, chunk, timeout)
            if (
                isinstance(data, list)
                and len(data) == len(chunk)
                and all(isinstance(item, str) for item in data)
            ):
                got = [_repair(src, dst, lang, timeout) for src, dst in zip(chunk, data)]
        except Exception as exc:  # batching is an optimisation, never a blocker
            print(f"    batch failed ({exc}); retrying one at a time")
        if got is None:
            got = [_one(text, lang, timeout) for text in chunk]
        out.extend(got)
        if pause:
            time.sleep(pause)
    return out


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------
def source_strings() -> dict:
    from src.i18n import _STRINGS

    return {
        key: (table.get("en") or key)
        for key, table in _STRINGS.items()
        if (table.get("en") or "").strip()
    }


def read_catalog(code: str) -> dict:
    path = CATALOGS / f"{code}.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {key: value for key, value in data.items() if isinstance(value, str)}
    except Exception as exc:
        print(f"  {code}: unreadable catalog ({exc}); regenerating")
        return {}


def write_catalog(code: str, mapping: dict, order) -> Path:
    CATALOGS.mkdir(parents=True, exist_ok=True)
    path = CATALOGS / f"{code}.json"
    ordered = {key: mapping[key] for key in order if key in mapping}
    path.write_text(
        json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return path


def read_languages() -> dict:
    if not LANGUAGES_FILE.is_file():
        return {}
    try:
        return json.loads(LANGUAGES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_languages(languages: dict) -> None:
    CATALOGS.mkdir(parents=True, exist_ok=True)
    LANGUAGES_FILE.write_text(
        json.dumps(languages, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def fetch_languages(timeout: float = 15.0) -> dict:
    """Every language Google Translate accepts, with its native name."""
    url = LANGUAGE_LIST_URL
    request = urllib.request.Request(url, headers={"User-Agent": HEADERS["User-Agent"]})
    with _urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    english = payload.get("tl") or payload.get("sl") or {}
    known = read_languages()
    languages = {}
    names = sorted(english)
    for index, code in enumerate(names, 1):
        name = english[code]
        cached = (known.get(code) or {}).get("native")
        native = cached or (name if code == "en" else "")
        if not native:
            try:
                native = fetch_native_name(code, name, timeout)
            except Exception as exc:
                print(f"  {code}: native name failed ({exc}); using the English one")
                native = name
        languages[code] = {"english": name, "native": native}
        if index % 20 == 0:
            print(f"  …{index}/{len(names)} language names")
            write_languages(languages)
        time.sleep(PAUSE_SECONDS)
    write_languages(languages)
    return languages


def fetch_native_name(code: str, english_name: str, timeout: float) -> str:
    from src.translate import fetch_google_translation

    value = fetch_google_translation(english_name, code, timeout=timeout)
    return (value or english_name).strip()


# ---------------------------------------------------------------------------
# Coverage and generation
# ---------------------------------------------------------------------------
def coverage(order) -> dict:
    report = {}
    for code in ["en"] + sorted(read_languages()):
        if code in report:
            continue
        catalog = read_catalog(code)
        missing = [key for key in order if not (catalog.get(key) or "").strip()]
        report[code] = missing
    return report


def curated_strings(code: str) -> dict:
    """Hand-checked copy for a language, when src/i18n.py has any.

    The four shipped languages are translated by hand; German and Japanese have
    partial tables. Those strings seed the catalog so machine output never
    replaces reviewed wording — Google only fills the gaps.
    """
    from src.i18n import _STRINGS

    return {
        key: table[code]
        for key, table in _STRINGS.items()
        if (table.get(code) or "").strip()
    }


def fill_language(code: str, source: dict, *, force: bool, timeout: float) -> dict:
    """Translate one language, reusing whatever its catalog already has."""
    existing = read_catalog(code)
    for key, value in curated_strings(code).items():
        if force or not (existing.get(key) or "").strip():
            existing[key] = value
    if force:
        wanted = list(source)
    else:
        wanted = [key for key in source if not (existing.get(key) or "").strip()]
    if not wanted:
        print(f"{code}: complete ({len(source)} strings)")
        return existing
    print(f"{code}: {len(wanted)} of {len(source)} strings to translate")
    values = translate([source[key] for key in wanted], code, timeout=timeout)
    for key, value in zip(wanted, values):
        if value and value.strip():
            existing[key] = value
    write_catalog(code, existing, source)
    return existing


def refresh_keys(keys, source: dict, order, args) -> int:
    """Retranslate just ``keys`` in every language, keeping the rest as it is.

    Copy that changed in ``src/i18n.py`` cannot reach the hundred catalogs a
    long run already wrote — they are complete, so a normal run skips them.
    This is the targeted pass that updates those few strings.
    """
    targets = sorted(read_languages())
    wanted = [code for code in targets if code != "en"]
    done = 0
    for code in wanted:
        existing = read_catalog(code)
        hand = curated_strings(code)
        for key in keys:
            if key in hand:
                existing[key] = hand[key]
        todo = [key for key in keys if key not in hand]
        if todo:
            values = translate([source[key] for key in todo], code, timeout=args.timeout)
            for key, value in zip(todo, values):
                if value and value.strip():
                    existing[key] = value
        write_catalog(code, existing, source)
        done += 1
        if done % 20 == 0:
            print(f"  …{done}/{len(wanted)} languages refreshed")
    print(f"{done} language(s) refreshed for {len(keys)} string(s)")
    return 0


def main(argv=None) -> int:
    global PAUSE_SECONDS

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report coverage and stop")
    parser.add_argument("--labels", action="store_true", help="refresh languages.json")
    parser.add_argument("--lang", nargs="+", default=[], help="language codes to fill")
    parser.add_argument("--all", action="store_true", help="every known language")
    parser.add_argument("--force", action="store_true", help="retranslate everything")
    parser.add_argument("--dry-run", action="store_true", help="count only")
    parser.add_argument("--limit", type=int, default=0, help="stop after N languages")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="languages to translate at the same time (default 1)",
    )
    parser.add_argument(
        "--keys",
        nargs="+",
        default=[],
        help="refresh only these string ids in every language",
    )
    args = parser.parse_args(argv)
    PAUSE_SECONDS = args.pause

    if args.labels:
        languages = fetch_languages(timeout=args.timeout)
        print(f"languages.json: {len(languages)} languages")
        if not (args.lang or args.all or args.check):
            return 0

    source = source_strings()
    order = list(source)
    print(f"{len(order)} English strings to translate")

    if args.check:
        languages = read_languages()
        bad = []
        for code in ["en"] + sorted(languages):
            catalog = read_catalog(code)
            missing = [key for key in order if not (catalog.get(key) or "").strip()]
            state = "complete" if not missing else f"{len(missing)} missing"
            print(f"  {code:>7}: {len(order) - len(missing):>4}/{len(order)}  {state}")
            if missing and code != "en":
                bad.append(code)
        print(f"{len(languages) + 1} languages, {len(bad)} incomplete")
        return 1 if bad else 0

    if not args.all and not args.lang:
        print("nothing to do: pass --lang, --all, --labels or --check")
        return 0

    if args.keys:
        unknown = [key for key in args.keys if key not in source]
        if unknown:
            print(f"unknown string ids: {unknown}")
            return 2
        return refresh_keys(args.keys, source, order, args)

    known = read_languages()
    targets = sorted(known) if args.all else [code for code in args.lang]
    if not targets and not known:
        targets = sorted(args.lang)
    if "en" in targets and not args.force:
        targets.remove("en")  # English is the source, not a translation
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("no languages to fill")
        return 0

    if args.dry_run:
        for code in targets:
            existing = read_catalog(code)
            missing = [key for key in order if not (existing.get(key) or "").strip()]
            print(f"{code}: {len(missing)} of {len(order)} strings missing")
        return 0

    def run(code: str) -> bool:
        try:
            fill_language(code, source, force=args.force, timeout=args.timeout)
            return True
        except Exception as exc:
            print(f"{code}: failed ({exc}); continuing")
            return False

    done = 0
    workers = max(1, int(args.workers))
    if workers == 1:
        for code in targets:
            if run(code):
                done += 1
    else:
        # Each language is a handful of batched requests; several at once turn
        # a two-hour run into minutes. Every language is written on its own, so
        # an interrupted run is still resumed by simply running it again.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for ok in pool.map(run, targets):
                if ok:
                    done += 1
    print(f"{done} language(s) written to assets/i18n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
