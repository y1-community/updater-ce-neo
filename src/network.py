"""Network connectivity monitor and offline mode detection.

Detects internet availability and reachability of github.com (firmware catalog)
and innioasis.app (donations and updates). Allows UI to dynamically toggle
between Online Firmware listing and offline Local File mode without blocking
the Qt event loop.
"""

from __future__ import annotations

import logging
import socket
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


def is_network_available(timeout: float = 2.0) -> bool:
    """Return True if internet is reachable and GitHub or Innioasis can be contacted."""
    for url in PROBE_ENDPOINTS:
        if check_endpoint(url, timeout=timeout):
            return True
    # If HTTP endpoints failed or timed out, do a quick socket check
    if check_socket_connectivity("1.1.1.1", 53, timeout=1.0) or check_socket_connectivity("8.8.8.8", 53, timeout=1.0):
        return True
    return False


class ConnectivityCheckWorker(QThread):
    result = Signal(bool)

    def __init__(self, timeout: float = 2.0, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.timeout = timeout

    def run(self):
        online = is_network_available(self.timeout)
        self.result.emit(online)


class ConnectivityMonitor(QObject):
    """Periodic non-blocking network monitor for the UI."""

    connectivity_changed = Signal(bool)

    def __init__(
        self,
        interval_ms: int = 6000,
        initial_check: bool = True,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._interval_ms = interval_ms
        self._is_online: Optional[bool] = None
        self._worker: Optional[ConnectivityCheckWorker] = None
        self._timeout = 2.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check_now)

        if initial_check:
            self.check_now()

    @property
    def is_online(self) -> Optional[bool]:
        return self._is_online

    def start_monitoring(self, interval_ms: Optional[int] = None):
        if interval_ms:
            self._interval_ms = interval_ms
        self.check_now()
        self._timer.start(self._interval_ms)

    def stop_monitoring(self):
        self._timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(3000)

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
        if prev is None or prev != online:
            logger.info("Network connectivity transitioned: online=%s", online)
            self.connectivity_changed.emit(online)


_shared_monitor: Optional[ConnectivityMonitor] = None


def get_connectivity_monitor() -> ConnectivityMonitor:
    global _shared_monitor
    if _shared_monitor is None:
        _shared_monitor = ConnectivityMonitor(interval_ms=6000, initial_check=False)
    return _shared_monitor
