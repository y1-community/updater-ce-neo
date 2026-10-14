"""Keep QThread workers referenced until their threads have actually stopped.

Qt aborts the process — ``SIGABRT`` in ``QThread::~QThread`` — when a QThread
object is destroyed while its thread is still running, and a worker parented to
a widget is destroyed with that widget. Cancellation is always asynchronous
(``DownloadWorker`` sees the flag between chunks, ``ExtractWorker`` between
entries, the release workers between requests), so every place that cancels,
replaces or clears a worker has to keep the object alive for a moment longer
than the code that used to hold it.

``FlashService`` already works this way (``_retired_workers`` /
``_retired_monitors``); this module is the same idea for the UI workers, so the
pages and the main window share one implementation instead of three.
"""

import logging

from PySide6.QtCore import QTimer

logger = logging.getLogger(__name__)


def retire_worker(worker, registry):
    """Hold ``worker`` in ``registry`` until its thread has stopped.

    Returns the worker, so callers can write
    ``self._worker = retire_worker(self._worker, self._active_workers)``.
    """
    if worker is None:
        return worker
    registry.add(worker)
    try:
        # A worker parented to a widget is deleted with that widget's children,
        # which is how a quit during a download aborted the app.
        if worker.isRunning() and worker.parent() is not None:
            worker.setParent(None)
    except Exception:
        pass

    def _forget(*_args, _worker=worker, _registry=registry):
        try:
            running = _worker.isRunning()
        except Exception:
            running = False
        if running:
            QTimer.singleShot(500, _forget)
            return
        _registry.discard(_worker)

    try:
        worker.finished.connect(_forget)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("Could not watch %r for retirement: %s", worker, exc)
        _forget()
    return worker
