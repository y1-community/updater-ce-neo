"""Catalogue reachability: can this machine actually list firmware?

The question the UI has to answer is not "is there internet" but "can the
firmware listings be loaded from the catalogue's repositories". Some networks
— entire regions, corporate filters, hotel and carrier captive portals — answer
yes to the first and no to the second, and an app that trusts a generic probe
offers an Online catalog that can never fill. So the probe here loads a real
releases listing from the repos ``slidia_manifest.xml`` names, and only a
downloaded listing counts as online. ``is_network_available`` keeps its name for
callers that simply want that answer, and the connectvity monitor turns it into
a UI signal off the Qt event loop.
"""

from __future__ import annotations

import logging
import socket
import time
import urllib.request
import ssl
from typing import Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

logger = logging.getLogger(__name__)

PROBE_ENDPOINTS = [
    "https://api.github.com",
    "https://raw.githubusercontent.com",
    "https://innioasis.app",
]


def _get_ssl_context() -> ssl.SSLContext:
    """Return an SSL context with certifi root CAs, falling back gracefully."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


def check_socket_connectivity(host: str = "1.1.1.1", port: int = 53, timeout: float = 1.0) -> bool:
    """Quickly check if internet routing is alive via raw TCP socket."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.close()
        return True
    except OSError:
        return False


def check_endpoint(url: str, timeout: float = 2.0) -> bool:
    """Quickly check if an HTTP(S) endpoint is reachable."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "InnioasisUpdater/3.0 (+connectivity-check)"},
        method="HEAD",
    )
    # First attempt with proper CA bundle
    try:
        ctx = _get_ssl_context()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status < 500
    except urllib.error.HTTPError as e:
        # 403 (e.g. rate limit) still indicates network connectivity to GitHub
        return e.code < 500
    except (urllib.error.URLError, ssl.SSLError):
        # Fall back to unverified context to confirm reachability even if CA store is broken
        try:
            unverified_ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=timeout, context=unverified_ctx) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as e:
            return e.code < 500
        except Exception:
            return False
    except (OSError, socket.timeout):
        return False
    except Exception:
        return False


# The releases listing of a firmware repository. Loading it is the only thing
# that proves the catalogue can actually be filled: a region, corporate filter or
# carrier that cannot reach GitHub passes every generic connectivity probe and
# then fails to list firmware, which would leave the app offering an online
# catalogue that can never show a build.
GITHUB_RELEASES_URL = "https://api.github.com/repos/{repo}/releases"
LISTING_TIMEOUT = 4.0
LISTING_TOTAL_BUDGET = 12.0


def fetch_releases_listing(repo: str, timeout: float = LISTING_TIMEOUT):
    """One repository's releases listing (a JSON list), or ``None`` on failure.

    Uses the certifi CA bundle with the project's unverified fallback (see the
    HTTPS invariants in the repo conventions) so a broken CA store does not look
    like an unreachable GitHub.
    """
    import json

    request = urllib.request.Request(
        GITHUB_RELEASES_URL.format(repo=repo),
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; InnioasisUpdater/3.0)",
            "Accept": "application/vnd.github+json",
        },
    )
    for ctx in (_get_ssl_context(), ssl._create_unverified_context()):
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=ctx) as resp:
                if resp.status != 200:
                    continue
                data = json.loads(resp.read().decode("utf-8", "replace"))
                if isinstance(data, list):
                    return data
        except urllib.error.HTTPError as e:
            if e.code < 500:
                # HTTP response from GitHub (e.g. 403 rate limit) proves server is reachable
                return []
        except Exception:
            continue
    return None


def listings_reachable(
    repos=None,
    timeout: float = LISTING_TIMEOUT,
    fetch=fetch_releases_listing,
    budget: float = LISTING_TOTAL_BUDGET,
) -> bool:
    """True as soon as any catalogue repository's listings can be loaded.

    All of them failing means GitHub is not usable from here, whatever the
    generic probes say. *budget* caps the whole check so a long list of
    unreachable repositories cannot stall the caller.
    """
    if repos is None:
        repos = catalogue_repos()
    deadline = time.monotonic() + budget
    for repo in repos:
        # An empty listing is still a listing: the repository answered, which is
        # the question being asked (a repo with no releases yet must not read as
        # "GitHub unreachable").
        if fetch(repo, timeout) is not None:
            return True
        if time.monotonic() >= deadline:
            logger.info("Catalogue listing check stopped after %.0fs (no repo answered)", budget)
            break
    return False


def catalogue_repos() -> list[str]:
    """The repositories the catalogue lists (imported lazily: no import cycle)."""
    try:
        from .manifest import catalogue_repos as _repos

        return list(_repos())
    except Exception as e:
        logger.debug("Could not read the catalogue's repositories: %s", e)
        return []


def is_network_available(timeout: float = LISTING_TIMEOUT) -> bool:
    """True when the online catalogue can actually be used from this machine.

    That is the catalogue's own listings (see :func:`listings_reachable`). Where
    no repository is known at all, a plain endpoint probe is the fallback.
    """
    repos = catalogue_repos()
    if repos:
        return listings_reachable(repos, timeout=timeout)
    return any(check_endpoint(url, timeout=timeout) for url in PROBE_ENDPOINTS)


class ConnectivityCheckWorker(QThread):
    result = Signal(bool)

    def __init__(self, timeout: float = 2.0, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.timeout = timeout

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            online = is_network_available(self.timeout)
            if not self.isInterruptionRequested():
                self.result.emit(online)
        except (RuntimeError, Exception):
            pass


class ConnectivityMonitor(QObject):
    """Periodic non-blocking network monitor for the UI.

    A probe is a real HTTPS request, so the cadence is deliberately unhurried:
    each check costs a TLS handshake plus DNS on the calling thread, and a
    monitor that reconnects every few seconds keeps a laptop's radio and CPU
    out of idle for no benefit. While the network looks healthy the state only
    has to be confirmed now and then (:data:`ONLINE_INTERVAL_MS`); once a check
    fails, a shorter interval is used so a returning connection is noticed
    promptly (:data:`OFFLINE_INTERVAL_MS`).

    Passing an explicit ``interval_ms`` pins the cadence (tests do this) and
    disables the adaptive behaviour.
    """

    connectivity_changed = Signal(bool)

    ONLINE_INTERVAL_MS = 60_000
    OFFLINE_INTERVAL_MS = 15_000
    # The probe loads a real releases listing, which is slower than a HEAD
    # request (and slower still on the networks this check exists for).
    PROBE_TIMEOUT_S = 4.0

    def __init__(
        self,
        interval_ms: Optional[int] = None,
        initial_check: bool = True,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        # An explicit interval means "keep this cadence", so adaptation is off.
        self._pinned_interval_ms = int(interval_ms) if interval_ms else None
        self._interval_ms = self._pinned_interval_ms or self.ONLINE_INTERVAL_MS
        self._is_online: Optional[bool] = None
        self._worker: Optional[ConnectivityCheckWorker] = None
        self._timeout = self.PROBE_TIMEOUT_S
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_now)

        if initial_check:
            self.check_now()

    @property
    def is_online(self) -> Optional[bool]:
        return self._is_online

    @property
    def is_monitoring(self) -> bool:
        return self._timer.isActive()

    def start_monitoring(self, interval_ms: Optional[int] = None):
        if interval_ms:
            self._pinned_interval_ms = int(interval_ms)
            self._interval_ms = self._pinned_interval_ms
        if self._is_online is False and self._pinned_interval_ms is None:
            self._interval_ms = self.OFFLINE_INTERVAL_MS
        self.check_now()
        self._timer.start(self._interval_ms)

    def stop_monitoring(self):
        self._timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)

    def _reschedule(self):
        """Pick the next interval from the state the last probe found."""
        if self._pinned_interval_ms is not None or not self._timer.isActive():
            return
        self._interval_ms = (
            self.OFFLINE_INTERVAL_MS
            if self._is_online is False
            else self.ONLINE_INTERVAL_MS
        )
        # start() restarts the countdown from now, which is what we want: the
        # interval that was just chosen applies from this probe onwards.
        self._timer.start(self._interval_ms)

    def check_now(self):
        """Start a probe unless one is already in flight.

        The finished thread is released by ``_on_worker_finished`` before Qt
        deletes it: letting ``finished`` drop straight into ``deleteLater`` left
        ``self._worker`` pointing at a dead C++ object, so every later tick
        raised ``RuntimeError: Internal C++ object ... already deleted`` (and the
        monitor never probed again).
        """
        worker = self._worker
        if worker is not None and worker.isRunning():
            return
        worker = ConnectivityCheckWorker(timeout=self._timeout)
        self._worker = worker
        worker.result.connect(self._on_check_completed)
        worker.finished.connect(self._on_worker_finished)
        worker.start()

    def _on_worker_finished(self):
        """Forget the finished probe thread, then let Qt delete it."""
        worker = self.sender()
        if worker is self._worker:
            self._worker = None
        if worker is not None:
            worker.deleteLater()

    def _on_check_completed(self, online: bool):
        prev = self._is_online
        self._is_online = online
        self._reschedule()
        if prev is None or prev != online:
            logger.info("Network connectivity transitioned: online=%s", online)
            self.connectivity_changed.emit(online)


_shared_monitor: Optional[ConnectivityMonitor] = None


def get_connectivity_monitor() -> ConnectivityMonitor:
    global _shared_monitor
    if _shared_monitor is None:
        _shared_monitor = ConnectivityMonitor(initial_check=False)
    return _shared_monitor
