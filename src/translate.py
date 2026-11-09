"""Release notes and notice translation with multi-provider free no-auth fallbacks.

Provides Google Translate URLs for GitHub release notes in the user's chosen language,
in-app translation via a chain of free no-auth translation services (Google clients5,
Google GTX, MyMemory, Lingva, SimplyTranslate) with per-provider cooldowns and graceful
failure, and in-memory caching.
"""

from __future__ import annotations

import json
import logging
import ssl
import time
import urllib.parse
import urllib.request
from typing import Callable, Optional, Tuple

from PySide6.QtCore import QThread, Signal

from .i18n import is_english_language
from .markdown_render import without_github_only

logger = logging.getLogger(__name__)


class TranslationUnavailableError(RuntimeError):
    """Raised when every configured translation service is unreachable or rate-limited."""


# Cache for in-memory translated text: (text_hash_or_key, target_lang) -> translated_text
_TRANSLATION_CACHE: dict[Tuple[str, str], str] = {}

# Cooldown tracking so failed providers are skipped without spamming networks
_PROVIDER_COOLDOWN_SECONDS = 120.0
_GLOBAL_COOLDOWN_SECONDS = 60.0
_PROVIDER_FAILED_AT: dict[str, float] = {}
_ALL_PROVIDERS_FAILED_AT: float = 0.0
_LAST_ACTIVE_PROVIDER: str = ""

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


def reset_translation_cooldowns() -> None:
    """Clear provider and global failure cooldowns (e.g. when connectivity changes or in tests)."""
    global _ALL_PROVIDERS_FAILED_AT, _LAST_ACTIVE_PROVIDER
    _PROVIDER_FAILED_AT.clear()
    _ALL_PROVIDERS_FAILED_AT = 0.0
    _LAST_ACTIVE_PROVIDER = ""


def mark_translation_unavailable(now: Optional[float] = None) -> None:
    """Explicitly mark translation services as temporarily unavailable."""
    global _ALL_PROVIDERS_FAILED_AT
    _ALL_PROVIDERS_FAILED_AT = time.monotonic() if now is None else float(now)


def is_translation_available(now: Optional[float] = None) -> bool:
    """Return False when translation is disabled or all providers recently failed."""
    try:
        from .remote_flags import is_remote_flag_active

        if is_remote_flag_active("null-translate"):
            return False
    except Exception:
        pass
    if _ALL_PROVIDERS_FAILED_AT <= 0.0:
        return True
    ts = time.monotonic() if now is None else float(now)
    return (ts - _ALL_PROVIDERS_FAILED_AT) >= _GLOBAL_COOLDOWN_SECONDS


def last_active_provider() -> str:
    """Return the name of the provider that served the most recent translation."""
    return _LAST_ACTIVE_PROVIDER


def _provider_on_cooldown(name: str, now: Optional[float] = None) -> bool:
    failed_at = _PROVIDER_FAILED_AT.get(name, 0.0)
    if failed_at <= 0.0:
        return False
    ts = time.monotonic() if now is None else float(now)
    if (ts - failed_at) < _PROVIDER_COOLDOWN_SECONDS:
        return True
    _PROVIDER_FAILED_AT.pop(name, None)
    return False


def _get_ssl_context(verify: bool = True) -> ssl.SSLContext:
    """Return an SSL context with certifi root CAs, falling back gracefully."""
    if not verify:
        return ssl._create_unverified_context()
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def _urlopen(req: urllib.request.Request, timeout: float = 4.5):
    """Execute an HTTP request with automatic SSL certificate error recovery."""
    try:
        ctx = _get_ssl_context(verify=True)
        return urllib.request.urlopen(req, context=ctx, timeout=timeout)
    except Exception as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e) or isinstance(
            e, ssl.SSLCertVerificationError
        ):
            logger.debug("SSL verify failed; retrying with unverified context: %s", e)
            ctx = _get_ssl_context(verify=False)
            return urllib.request.urlopen(req, context=ctx, timeout=timeout)
        raise


def _release_repo(rel: Optional[dict]) -> str:
    """``owner/repo`` for a release dict, from its field or its page URL."""
    from .docs_source import normalize_repo, repo_from_url

    repo = normalize_repo((rel or {}).get("source_repo"))
    return repo or repo_from_url(str((rel or {}).get("html_url") or ""))


def _release_tag(rel: Optional[dict]) -> str:
    """The release's tag, from its field or its ``/releases/tag/<tag>`` page."""
    tag = str((rel or {}).get("tag_name") or "").strip()
    if tag:
        return tag
    parts = urllib.parse.urlsplit(str((rel or {}).get("html_url") or ""))
    segments = [segment for segment in parts.path.split("/") if segment]
    if "tag" in segments:
        index = segments.index("tag")
        if index + 1 < len(segments):
            return segments[index + 1]
    return ""


def _translated_page_url(page: str, lang: str) -> str:
    """Google's proxy view of ``page`` in ``lang`` (``""`` without a host)."""
    parts = urllib.parse.urlsplit(str(page or ""))
    host = parts.netloc.lower()
    if not host:
        return ""
    params = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    params += [
        ("_x_tr_sl", "auto"),
        ("_x_tr_tl", lang),
        ("_x_tr_hl", lang),
        ("_x_tr_pto", "wapp"),
    ]
    return urllib.parse.urlunsplit(
        (
            parts.scheme or "https",
            f"{host.replace('.', '-')}.translate.goog",
            parts.path,
            urllib.parse.urlencode(params),
            parts.fragment,
        )
    )


def get_google_translate_release_url(
    rel: Optional[dict] = None, target_lang: Optional[str] = None
) -> str:
    """The Google Translate view of a release's notes on our own site.

    The page a reader lands on is ``innioasis.app/release_notes.html``, named
    by ``repo`` and ``tag``: the site reads that release's title and notes and
    renders them with our styling and its own download buttons. Google's proxy
    serves that same page translated, so "View in browser" never opens a
    github.com page either.
    """
    try:
        from .remote_flags import is_remote_flag_active

        if is_remote_flag_active("null-translate"):
            return ""
    except Exception:
        pass
    lang = (target_lang or "").strip()
    if not lang:
        try:
            from .i18n import translator

            lang = translator().lang or "en"
        except Exception:
            lang = "en"

    from .docs_source import release_notes_url

    page = release_notes_url(
        _release_repo(rel),
        _release_tag(rel),
        name=str((rel or {}).get("name") or ""),
    )
    # A release the site cannot name keeps its own page as the translated view.
    return _translated_page_url(page or str((rel or {}).get("html_url") or ""), lang)


# --- Individual free no-auth translation providers --------------------------


def _translate_via_google_clients5(text: str, target_lang: str, timeout: float) -> Optional[str]:
    url = (
        "https://clients5.google.com/translate_a/t?client=dict-chrome-ex&sl=auto&tl="
        + urllib.parse.quote(target_lang)
    )
    post_data = urllib.parse.urlencode({"q": text}).encode("utf-8")
    req = urllib.request.Request(url, data=post_data, headers=_HEADERS)
    with _urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list) and data:
            if isinstance(data[0], list) and data[0]:
                return str(data[0][0])
            if isinstance(data[0], str):
                return str(data[0])
    return None


def _translate_via_google_gtx(text: str, target_lang: str, timeout: float) -> Optional[str]:
    url = (
        "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl="
        + urllib.parse.quote(target_lang)
        + "&dt=t"
    )
    post_data = urllib.parse.urlencode({"q": text}).encode("utf-8")
    req = urllib.request.Request(url, data=post_data, headers=_HEADERS)
    with _urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, list) and data and isinstance(data[0], list):
            parts = [
                str(p[0])
                for p in data[0]
                if isinstance(p, list) and p and isinstance(p[0], str)
            ]
            if parts:
                return "".join(parts)
    return None


def _split_chunks(text: str, max_chars: int = 450) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines(keepends=True):
        if len(line) > max_chars:
            if current:
                chunks.append("".join(current))
                current = []
                current_len = 0
            for idx in range(0, len(line), max_chars):
                chunks.append(line[idx : idx + max_chars])
            continue
        if current_len + len(line) > max_chars and current:
            chunks.append("".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)
    if current:
        chunks.append("".join(current))
    return chunks


def _translate_via_mymemory(text: str, target_lang: str, timeout: float) -> Optional[str]:
    """MyMemory free anonymous translation API (https://api.mymemory.translated.net)."""
    lang = target_lang.strip()
    chunks = _split_chunks(text, max_chars=450)
    if len(chunks) > 8:
        # Avoid making many sequential HTTP calls on huge changelogs
        return None
    translated_parts: list[str] = []
    for chunk in chunks:
        if not chunk.strip():
            translated_parts.append(chunk)
            continue
        params = urllib.parse.urlencode({"q": chunk, "langpair": f"en|{lang}"})
        url = f"https://api.mymemory.translated.net/get?{params}"
        req = urllib.request.Request(
            url, headers={"User-Agent": _HEADERS["User-Agent"], "Accept": "application/json"}
        )
        with _urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, dict):
            return None
        status = int(data.get("responseStatus") or 0)
        if status and status != 200:
            return None
        resp_data = data.get("responseData")
        if not isinstance(resp_data, dict):
            return None
        piece = str(resp_data.get("translatedText") or "").strip()
        if not piece or "MYMEMORY WARNING" in piece.upper() or "QUERY LENGTH LIMIT" in piece.upper():
            return None
        translated_parts.append(piece)
    return "\n".join(translated_parts) if len(chunks) > 1 else (translated_parts[0] if translated_parts else None)


def _make_lingva_provider(base_url: str) -> Callable[[str, str, float], Optional[str]]:
    def _provider(text: str, target_lang: str, timeout: float) -> Optional[str]:
        if len(text) > 1800:
            return None
        lang = target_lang.strip().replace("-", "_")
        encoded_q = urllib.parse.quote(text, safe="")
        url = f"{base_url.rstrip('/')}/api/v1/en/{urllib.parse.quote(lang, safe='')}/{encoded_q}"
        req = urllib.request.Request(
            url, headers={"User-Agent": _HEADERS["User-Agent"], "Accept": "application/json"}
        )
        with _urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and data.get("translation"):
            return str(data["translation"])
        return None

    return _provider


def _translate_via_simplytranslate(text: str, target_lang: str, timeout: float) -> Optional[str]:
    if len(text) > 1800:
        return None
    params = urllib.parse.urlencode(
        {"engine": "google", "from": "en", "to": target_lang.strip(), "text": text}
    )
    url = f"https://simplytranslate.org/api/translate/?{params}"
    req = urllib.request.Request(
        url, headers={"User-Agent": _HEADERS["User-Agent"], "Accept": "application/json"}
    )
    with _urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if isinstance(data, dict):
        result = data.get("translated-text") or data.get("translated_text")
        if result:
            return str(result)
    return None


TRANSLATION_PROVIDERS: tuple[tuple[str, Callable[[str, str, float], Optional[str]]], ...] = (
    ("google_clients5", _translate_via_google_clients5),
    ("google_gtx", _translate_via_google_gtx),
    ("mymemory", _translate_via_mymemory),
    ("lingva_ml", _make_lingva_provider("https://lingva.ml")),
    ("lingva_plausibility", _make_lingva_provider("https://translate.plausibility.cloud")),
    ("simplytranslate", _translate_via_simplytranslate),
)


def fetch_google_translation(
    text: str,
    target_lang: str,
    timeout: float = 4.5,
    *,
    raise_on_error: bool = False,
) -> str:
    """Fetch translated text using Google Translate and free no-auth fallback services.

    Tries providers in order, skipping any provider on cooldown so network issues never
    cause repeated hangs or traffic spikes. When ``raise_on_error=True``, raises
    :class:`TranslationUnavailableError` if all providers fail; otherwise returns the
    original ``text``.
    """
    global _ALL_PROVIDERS_FAILED_AT, _LAST_ACTIVE_PROVIDER

    try:
        from .remote_flags import is_remote_flag_active

        if is_remote_flag_active("null-translate"):
            if raise_on_error:
                raise TranslationUnavailableError("Translation disabled by remote flag")
            return text
    except TranslationUnavailableError:
        raise
    except Exception:
        pass

    if not text or not text.strip() or is_english_language(target_lang):
        return text

    cache_key = (text.strip(), target_lang)
    if cache_key in _TRANSLATION_CACHE:
        return _TRANSLATION_CACHE[cache_key]

    now = time.monotonic()
    if _ALL_PROVIDERS_FAILED_AT > 0.0 and (now - _ALL_PROVIDERS_FAILED_AT) < _GLOBAL_COOLDOWN_SECONDS:
        if raise_on_error:
            raise TranslationUnavailableError("Translation services are temporarily on cooldown")
        return text

    for provider_name, provider_fn in TRANSLATION_PROVIDERS:
        if _provider_on_cooldown(provider_name, now=now):
            continue
        try:
            result = provider_fn(text, target_lang, timeout)
            if isinstance(result, str) and result.strip():
                _TRANSLATION_CACHE[cache_key] = result
                _PROVIDER_FAILED_AT.pop(provider_name, None)
                _ALL_PROVIDERS_FAILED_AT = 0.0
                _LAST_ACTIVE_PROVIDER = provider_name
                return result
            _PROVIDER_FAILED_AT[provider_name] = time.monotonic()
        except Exception as e:
            _PROVIDER_FAILED_AT[provider_name] = time.monotonic()
            logger.debug("Translation provider %s failed: %s", provider_name, e)

    _ALL_PROVIDERS_FAILED_AT = time.monotonic()
    if raise_on_error:
        raise TranslationUnavailableError("All translation services are currently unavailable")
    return text


def strip_changes_header(text: str) -> str:
    """Parse original release notes for lines beginning with Changes (formatted or not)
    and ensure that anything including or above the line beginning with Changes is not shown.
    """
    if not text:
        return ""
    import re

    match = re.search(r"(?im)^\s*(?:[#*_\->\s]*)\bchanges\b.*(?:\n|$)", text)
    if match:
        remainder = text[match.end() :].lstrip("\r\n").strip()
        return remainder if remainder else text.strip()
    return text.strip()


def translate_release_notes(
    rel: dict, target_lang: str, *, raise_on_error: bool = False
) -> Tuple[str, str]:
    """Translate release name and body to the target language."""
    name = (rel or {}).get("name", "")
    body = (rel or {}).get("body", "")
    # Parts marked GitHub-only are never shown, so they are not translated either.
    body = strip_changes_header(without_github_only(body))

    translated_name = (
        fetch_google_translation(name, target_lang, raise_on_error=raise_on_error)
        if name
        else ""
    )
    translated_body = (
        fetch_google_translation(body, target_lang, raise_on_error=raise_on_error)
        if body
        else ""
    )
    return translated_name, translated_body


class ReleaseTranslateWorker(QThread):
    """Asynchronous worker that fetches translation for a release."""

    translation_ready = Signal(dict, str, str, str)  # (rel, target_lang, name, body)
    translation_failed = Signal(dict, str, str)  # (rel, target_lang, error)

    def __init__(self, rel: dict, target_lang: str, parent=None):
        super().__init__(parent)
        self.rel = rel
        self.target_lang = target_lang

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            name, body = translate_release_notes(
                self.rel, self.target_lang, raise_on_error=True
            )
            if not self.isInterruptionRequested():
                self.translation_ready.emit(self.rel, self.target_lang, name, body)
        except Exception as e:
            if not self.isInterruptionRequested():
                logger.warning("Release notes translation error: %s", e)
                self.translation_failed.emit(self.rel, self.target_lang, str(e))
