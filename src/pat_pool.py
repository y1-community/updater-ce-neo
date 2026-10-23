"""GitHub Personal Access Token (PAT) pool for rate-limit mitigation.

Fetches read-only PATs from https://innioasis.app/config.ini under [api_keys].
All except one token in config.ini have the 'github_pat_' prefix omitted, so
this module normalizes them by adding 'github_pat_' where missing.

The client selects up to 3 random PATs from the pool before falling back
to unauthenticated requests, allowing read-only access to GitHub releases
without hitting the unauthenticated 60 requests/hour cap.
"""

from __future__ import annotations

import configparser
import json
import logging
import os
import random
import sys
import time
import urllib.request
from pathlib import Path
from typing import List, Optional, Set

from .paths import app_cache_dir

logger = logging.getLogger(__name__)

CONFIG_INI_URL = "https://innioasis.app/config.ini"
CONFIG_CACHE_TTL_SECONDS = 3600  # 1 hour

# Hardcoded initial fallback tokens parsed from config.ini to guarantee
# immediate availability even before the first network fetch succeeds.
_FALLBACK_RAW_KEYS = [
    "11BUHMFQQ0bLMGKHwuj2h8_C6857artTYRi3u6lmPHz3JXatyH1DPAj3ypRQllUmmERZIMUT3NcfUqUXnV",
    "11BXAHALQ0h4tr2QfbJNAi_f1sg5MoFM8yluxp7dpXbDQl9hkdKsQWumqPlDhiRtcT77G6Q22LOpzdPW3u",
    "11BUHMFQQ0DSaC7nmNrtGh_oi7lorrmvGJ5QS6kUqmcASHqUsCMf8UaCTcwaBbyLHcJUYV3WUXRl2v6dhU",
    "11BXAHALQ0GXEX2PvaMzNg_Xj5MAoslUXTlBsEMxksqKe7o08smGeOg9JGIsEzuxnfOGCHTZYWxJTKVbH5",
    "11BUHMFQQ0aGWvi6KXOPIe_XyIWGh6uzKDcp9i7PqTBhVkvzbSb945joISvUVK5xguFYF3MWE5O1AJG0Rw",
    "11BUHMFQQ00MlHlOSwNMxs_liWIFlnFUeyuN9xakl6YBt1iNtc22VEyvVHKgWr4y5QVXF62JDLUyRJxxTO",
    "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",
]


def normalize_pat(raw_token: str) -> str:
    """Normalize a GitHub Personal Access Token string.

    Prepends 'github_pat_' if the prefix is missing from fine-grained tokens.
    """
    token = (raw_token or "").strip()
    if not token:
        return ""
    if not token.startswith("github_pat_") and not token.startswith("ghp_"):
        token = f"github_pat_{token}"
    return token


class PatPoolManager:
    """Manages cached GitHub PATs fetched from config.ini."""

    def __init__(self, cache_file: Optional[Path] = None):
        self._cache_file = cache_file or (app_cache_dir() / "config_pats.json")
        self._tokens: List[str] = [normalize_pat(k) for k in _FALLBACK_RAW_KEYS if k.strip()]
        self._bad_tokens: Set[str] = set()
        self._last_fetch_ts: float = 0.0
        self._load_cached_tokens()

    def _load_cached_tokens(self) -> None:
        try:
            if self._cache_file.is_file():
                data = json.loads(self._cache_file.read_text(encoding="utf-8"))
                ts = data.get("timestamp", 0)
                toks = data.get("tokens", [])
                if isinstance(toks, list) and toks:
                    self._tokens = [normalize_pat(t) for t in toks if t]
                    self._last_fetch_ts = float(ts)
        except Exception as e:
            logger.debug("Failed to read cached PATs: %s", e)

    def _save_cached_tokens(self) -> None:
        try:
            self._cache_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": self._last_fetch_ts,
                "tokens": self._tokens,
            }
            self._cache_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to write cached PATs: %s", e)

    def refresh_from_network(self, force: bool = False) -> bool:
        """Fetch config.ini from innioasis.app and update the pool."""
        now = time.time()
        if not force and (now - self._last_fetch_ts) < CONFIG_CACHE_TTL_SECONDS:
            return False

        try:
            req = urllib.request.Request(
                CONFIG_INI_URL,
                headers={"User-Agent": "Mozilla/5.0 (compatible; InnioasisUpdater/3.0)"},
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                text = resp.read().decode("utf-8", errors="replace")

            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(text)
            if "api_keys" in parser:
                parsed_tokens = []
                for _, val in parser["api_keys"].items():
                    tok = normalize_pat(val)
                    if tok and len(tok) > 20 and tok not in parsed_tokens:
                        parsed_tokens.append(tok)
                if parsed_tokens:
                    self._tokens = parsed_tokens
                    self._last_fetch_ts = now
                    self._save_cached_tokens()
                    logger.info("Loaded %d PATs from %s", len(parsed_tokens), CONFIG_INI_URL)
                    return True
        except Exception as e:
            logger.debug("Could not refresh PAT pool from network: %s", e)
        return False

    def report_token_status(self, token: str, status_code: int) -> None:
        """Record token failures (401 unauthorized / 403 / 429 rate limited)."""
        if status_code in (401, 403, 429):
            norm = normalize_pat(token)
            if norm:
                self._bad_tokens.add(norm)
                logger.debug("Marked PAT %s... as bad (HTTP %d)", norm[:15], status_code)

    def get_candidate_pats(self, count: int = 3) -> List[str]:
        """Select up to `count` random valid PATs to try.

        Attempts background refresh if cache is stale.
        """
        # If cache expired and we have fewer than 2 good tokens, refresh
        active = [t for t in self._tokens if t not in self._bad_tokens]
        if len(active) < 2 or (time.time() - self._last_fetch_ts) > CONFIG_CACHE_TTL_SECONDS:
            self.refresh_from_network()
            active = [t for t in self._tokens if t not in self._bad_tokens]

        if not active:
            # If all are marked bad, reset bad list as a fallback
            self._bad_tokens.clear()
            active = list(self._tokens)

        sample_size = min(count, len(active))
        return random.sample(active, sample_size) if sample_size > 0 else []


_GLOBAL_POOL: Optional[PatPoolManager] = None


def get_pat_pool() -> PatPoolManager:
    global _GLOBAL_POOL
    if _GLOBAL_POOL is None:
        _GLOBAL_POOL = PatPoolManager()
    return _GLOBAL_POOL


def get_random_pats(count: int = 3) -> List[str]:
    """Select up to 3 random normalized PATs from the pool."""
    return get_pat_pool().get_candidate_pats(count=count)


def report_pat_status(token: str, status_code: int) -> None:
    """Report a failed PAT status to avoid retrying it in the current session."""
    get_pat_pool().report_token_status(token, status_code)
