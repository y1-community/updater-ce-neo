#!/usr/bin/env python3
"""Sync all language JSON files in assets/i18n with src/i18n.py _STRINGS.
Ensures 1:1 key parity across all supported and catalog languages.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Add repo root to path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import i18n
from src.translate import fetch_google_translation

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sync_i18n")

#: ``{name}`` / ``{target:.0f}`` — a translated format string must keep every
#: one of these, or the app raises on a placeholder it cannot fill.
_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def _same_shape(source: str, translated: str) -> bool:
    """True when the translation keeps the source's placeholders and bold markup.

    Google Translate rewrites ``{raised}`` as translated prose and, in some
    scripts, the ``<b>`` tags as look-alike letters. Such a string would break
    the format call or drop the emphasis, so it is rejected instead of shipped.
    """
    source, translated = str(source), str(translated)
    if sorted(_PLACEHOLDER.findall(source)) != sorted(_PLACEHOLDER.findall(translated)):
        return False
    return source.count("<b>") == translated.count("<b>") and source.count("</b>") == translated.count("</b>")


def sync_language(file_path: Path, all_keys: list[str]) -> int:
    code = file_path.stem
    try:
        data = json.loads(file_path.read_text("utf-8"))
    except Exception as e:
        logger.error("Failed to read %s: %s", file_path, e)
        return 0

    updated = False
    # 1. Update curated keys from _STRINGS if defined for this code
    for k in all_keys:
        table = i18n._STRINGS.get(k, {})
        if code in table and str(table[code]).strip():
            curated = str(table[code]).strip()
            if data.get(k) != curated:
                data[k] = curated
                updated = True

    # 2. Consistent app_name (Innioasis Updater for English, without CE for all)
    if "app_name" in data:
        val = data["app_name"]
        if code in ("en", "en-GB", "en-US"):
            if val != "Innioasis Updater":
                data["app_name"] = "Innioasis Updater"
                updated = True
        elif val.endswith(" CE"):
            data["app_name"] = val[:-3].strip()
            updated = True
        elif val.endswith(" (CE)"):
            data["app_name"] = val[:-5].strip()
            updated = True

    # 3. Short app name (e.g. "Updater" in English, localized "Updater" in others)
    if "app_name_short" in all_keys:
        if code in ("en", "en-GB", "en-US"):
            if data.get("app_name_short") != "Updater":
                data["app_name_short"] = "Updater"
                updated = True
        else:
            short_app = data.get("app_name", "").strip()
            if not data.get("app_name_short") or data.get("app_name_short") != short_app:
                data["app_name_short"] = short_app
                updated = True

    # 4. onboarding_title clean without Innioasis prefix where appropriate
    if "onboarding_title" in data and code in ("en", "en-GB", "en-US"):
        if data["onboarding_title"] != "Let's set up Updater":
            data["onboarding_title"] = "Let's set up Updater"
            updated = True

    missing = [k for k in all_keys if k not in data or not str(data[k]).strip()]
    if not missing:
        if updated:
            sorted_data = {k: data[k] for k in sorted(data.keys())}
            file_path.write_text(json.dumps(sorted_data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        return 1 if updated else 0

    logger.info("Language %s: translating %d missing keys...", code, len(missing))

    def translate_one(key: str) -> tuple[str, str]:
        table = i18n._STRINGS.get(key, {})
        # Check if pre-translated directly in _STRINGS table
        if code in table and str(table[code]).strip():
            return key, str(table[code]).strip()
        en_text = table.get("en", "")
        if not en_text:
            return key, ""
        try:
            trans = fetch_google_translation(en_text, code)
        except Exception as err:
            logger.warning("Translation failed for %s (%s): %s", key, code, err)
            return key, en_text
        if not trans:
            return key, en_text
        if not _same_shape(en_text, trans):
            logger.warning(
                "Translation for %s (%s) damaged its placeholders or markup; keeping English",
                key,
                code,
            )
            return key, en_text
        return key, trans

    translated_count = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(translate_one, k): k for k in missing}
        for future in as_completed(futures):
            key, trans = future.result()
            if trans:
                data[key] = trans
                translated_count += 1

    # Sort keys for consistent diffs
    sorted_data = {k: data[k] for k in sorted(data.keys())}
    file_path.write_text(json.dumps(sorted_data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    logger.info("Language %s: successfully updated %d keys (total: %d)", code, translated_count, len(sorted_data))
    return translated_count

def main():
    cat_dir = ROOT / "assets" / "i18n"
    all_keys = sorted(i18n._STRINGS.keys())
    logger.info("Master string table has %d keys", len(all_keys))

    lang_files = sorted([f for f in cat_dir.glob("*.json") if f.stem != "languages"])
    logger.info("Found %d language catalogs", len(lang_files))

    start_time = time.time()
    total_added = 0
    for idx, f in enumerate(lang_files, 1):
        added = sync_language(f, all_keys)
        total_added += added

    elapsed = time.time() - start_time
    logger.info("Completed i18n sync: added %d translations across %d catalogs in %.2fs", total_added, len(lang_files), elapsed)

if __name__ == "__main__":
    main()
