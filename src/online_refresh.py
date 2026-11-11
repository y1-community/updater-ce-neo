"""Randomized online refresh cadence and all-repository release refresh.

The window keeps a long-lived catalogue, notices and per-repository release
caches. A fixed timer is easy to predict but also easy to miss: a release
published one second after a tick can wait almost the whole interval. This
module supplies the shared 2-8 minute random delay and the worker that forces
every catalogue repository's release cache, so the Choose Software list can be
re-read without the user pressing Refresh or the R key.
"""

from __future__ import annotations

import logging
import random

from PySide6.QtCore import QThread, Signal

from . import catalog

logger = logging.getLogger(__name__)

#: The user-visible refresh window. Every tick picks a new delay in this range,
#: so repeated checks cannot settle into a predictable minute.
MIN_INTERVAL_MS = 2 * 60 * 1000
MAX_INTERVAL_MS = 8 * 60 * 1000


def next_interval_ms(rng=None) -> int:
    """A fresh delay in ``[MIN_INTERVAL_MS, MAX_INTERVAL_MS]``.

    ``rng`` is injectable so tests can prove the bounds without sleeping.
    """
    source = rng or random
    return int(source.randint(MIN_INTERVAL_MS, MAX_INTERVAL_MS))


def catalogue_repos() -> list[str]:
    """Every repository the catalogue can show, in first-seen order.

    Firmware repositories are the main set, but an APK/info package and the
    Updater's own release repository also publish releases users can be told
    about, so they participate in the same sweep.
    """
    repos: list[str] = []

    def add(repo) -> None:
        value = str(repo or "").strip()
        if value and value not in repos:
            repos.append(value)

    try:
        entries = catalog._catalog_packages()
    except Exception:
        entries = list(getattr(catalog, "CATALOG", ()))
    for package in entries:
        for attribute in ("repo", "info_repo", "apk_repo"):
            add(getattr(package, attribute, ""))
    try:
        from .config import UPDATE_REPO

        add(UPDATE_REPO)
    except Exception:
        pass
    return repos


def refresh_pat_pool() -> None:
    """Refresh ``config.ini``'s PAT pool before spending release requests."""
    try:
        from .pat_pool import get_pat_pool

        get_pat_pool().refresh_from_network(force=True)
    except Exception:
        logger.debug("PAT pool refresh failed", exc_info=True)


class AllReposRefreshWorker(QThread):
    """Force every catalogue repository's release cache in the background."""

    refreshed = Signal(dict)

    def __init__(self, repos=None, client=None, parent=None):
        super().__init__(parent)
        self._repos = list(repos) if repos is not None else None
        self._client = client

    def run(self) -> None:
        results: dict[str, int] = {}
        refresh_pat_pool()
        client = self._client or catalog.ReleasesClient()
        repos = self._repos if self._repos is not None else catalogue_repos()
        for repo in repos:
            if self.isInterruptionRequested():
                return
            try:
                rows = client.get_all_releases(repo, force_refresh=True)
                results[repo] = len(rows or [])
            except Exception as exc:
                logger.debug("Release refresh failed for %s: %s", repo, exc)
                results[repo] = -1
        if not self.isInterruptionRequested():
            self.refreshed.emit(results)
