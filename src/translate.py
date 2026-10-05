"""Release notes translation using Google Translate.

Provides Google Translate URLs for GitHub release notes in the user's chosen language,
in-app release notes translation via Google Translate, and caching.
"""

import json
import logging
import urllib.parse
import urllib.request
from typing import Optional, Tuple

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

# Cache for in-memory translated text: (text_hash_or_key, target_lang) -> translated_text
_TRANSLATION_CACHE: dict[Tuple[str, str], str] = {}


def get_google_translate_release_url(
    rel: Optional[dict] = None, target_lang: Optional[str] = None
) -> str:
    """Build the Google Translate URL for a given GitHub release.

    Format matches:
    https://github-com.translate.goog/{repo}/releases/{tag}?_x_tr_sl=auto&_x_tr_tl={target_lang}&_x_tr_hl={target_lang}&_x_tr_pto=wapp
    """
    lang = (target_lang or "").strip()
    if not lang:
        try:
            from .i18n import translator

            lang = translator().lang or "en"
        except Exception:
            lang = "en"

    repo = (rel or {}).get("source_repo") or "y1-community/y1-stock-rom"
    tag = (rel or {}).get("tag_name") or ""

    if tag:
        path = f"/{repo}/releases/{tag}"
    else:
        path = f"/{repo}/releases"

    params = urllib.parse.urlencode(
        {
            "_x_tr_sl": "auto",
            "_x_tr_tl": lang,
            "_x_tr_hl": lang,
            "_x_tr_pto": "wapp",
        }
    )
    return f"https://github-com.translate.goog{path}?{params}"


def fetch_google_translation(text: str, target_lang: str, timeout: float = 6.0) -> str:
    """Fetch translated text using Google Translate endpoints.

    Tries clients5.google.com first, falling back to translate.googleapis.com.
    Returns original text if offline or on network failure.
    """
    if not text or not text.strip() or target_lang in ("en", ""):
        return text

    cache_key = (text.strip(), target_lang)
    if cache_key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[cache_key]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # 1. Primary: clients5.google.com/translate_a/t
    try:
        params = {
            "client": "dict-chrome-ex",
            "sl": "auto",
            "tl": target_lang,
            "q": text,
        }
        url = "https://clients5.google.com/translate_a/t?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data:
                if isinstance(data[0], list) and data[0]:
                    result = str(data[0][0])
                    _TRANSLATION_CACHE[cache_key] = result
                    return result
                if isinstance(data[0], str):
                    result = str(data[0])
                    _TRANSLATION_CACHE[cache_key] = result
                    return result
    except Exception as e:
        logger.debug("Primary Google translation failed (%s): %s", url, e)

    # 2. Secondary fallback: translate.googleapis.com/translate_a/single
    try:
        params = {
            "client": "dict-chrome-ex",
            "sl": "auto",
            "tl": target_lang,
            "dt": "t",
            "q": text,
        }
        url = "https://translate.googleapis.com/translate_a/single?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, list) and data and isinstance(data[0], list):
                parts = [p[0] for p in data[0] if isinstance(p, list) and p and p[0]]
                if parts:
                    result = "".join(parts)
                    _TRANSLATION_CACHE[cache_key] = result
                    return result
    except Exception as e:
        logger.debug("Secondary Google translation failed (%s): %s", url, e)

    return text


def translate_release_notes(rel: dict, target_lang: str) -> Tuple[str, str]:
    """Translate release name and body to the target language."""
    name = (rel or {}).get("name", "")
    body = (rel or {}).get("body", "")

    translated_name = fetch_google_translation(name, target_lang) if name else ""
    translated_body = fetch_google_translation(body, target_lang) if body else ""
    return translated_name, translated_body


class ReleaseTranslateWorker(QThread):
    """Asynchronous worker that fetches Google translation for a release."""

    translation_ready = Signal(dict, str, str, str)  # (rel, target_lang, name, body)
    translation_failed = Signal(dict, str, str)       # (rel, target_lang, error)

    def __init__(self, rel: dict, target_lang: str, parent=None):
        super().__init__(parent)
        self.rel = rel
        self.target_lang = target_lang

    def run(self):
        try:
            name, body = translate_release_notes(self.rel, self.target_lang)
            self.translation_ready.emit(self.rel, self.target_lang, name, body)
        except Exception as e:
            logger.warning("Release notes translation error: %s", e)
            self.translation_failed.emit(self.rel, self.target_lang, str(e))
