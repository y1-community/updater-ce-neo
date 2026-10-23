"""The release-icon fetch must outlive the call that replaced it.

``MainWindow._apply_release_icon`` starts a small QThread to fetch the release
image. A path that repaints the card twice in a row — clicking the sidebar SP
Flash Tool button does exactly that: the hand-off begins and the download
started signal repaints — used to drop the running loader on the floor. Python
then collected it, ``QThread::~QThread`` saw a live thread, and Qt aborted the
process: ``QThread: Destroyed while thread is still running`` (SIGABRT).

The window itself is not built here: the loader's lifetime is what matters, and
a real window would start catalogue and update fetches of its own.
"""

import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")


class _Card:
    """Stands in for the install card the icon is painted on."""

    def __init__(self):
        self.icons = []

    def set_release_icon(self, pixmap, complete=False):
        self.icons.append((pixmap, complete))


class _Page:
    """The catalogue state ``_apply_release_icon`` reads."""

    _current_selected_rel = {"tag_name": "v3.1.2", "source_repo": "y1-community/y1-stock-rom"}
    _details_package = None

    def catalogue_section(self):
        return "online"

    def release_pixmap(self):
        return None


@pytest.fixture()
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _host():
    """A MainWindow with only what the icon refresh touches."""
    from src.ui import main_window as mw

    host = mw.MainWindow.__new__(mw.MainWindow)
    host._flash_page = _Card()
    host._select_page = _Page()
    host._retired_workers = set()
    host._install_complete = False
    host._package_path = ""
    return host


def test_a_replaced_icon_loader_is_retired_not_destroyed(app, monkeypatch):
    from PySide6.QtGui import QPixmap

    from src.ui import release_icon

    gate = threading.Event()

    def blocking_load(*args, **kwargs):
        # Only the fetch inside the thread is held; the call on the GUI thread
        # (allow_network=False) must return at once.
        if kwargs.get("allow_network"):
            gate.wait(10)
        return QPixmap()

    monkeypatch.setattr(release_icon, "load_release_pixmap", blocking_load)
    monkeypatch.setattr(
        "src.release_icons.icon_candidate_urls",
        lambda *a, **k: ["https://example.invalid/updater.png"],
    )

    host = _host()
    try:
        host._apply_release_icon()
        first = host._icon_loader
        _wait_until(first.isRunning, 2.0)
        assert first.isRunning(), "the fetch should still be running"

        # The second repaint replaces the first loader while it runs.
        host._apply_release_icon()
        second = host._icon_loader
        assert second is not first
        # Retired, not dropped: the reference keeps the live QThread alive.
        assert first in host._retired_workers
        assert first.isRunning()
    finally:
        gate.set()
        for worker in list(host._retired_workers) + [getattr(host, "_icon_loader", None)]:
            if worker is not None:
                assert worker.wait(5000), "the icon fetch should finish once released"
        app.processEvents()


def test_a_finished_icon_loader_is_not_retired_forever(app, monkeypatch):
    """The retire registry only grows for work that was still running."""
    from PySide6.QtGui import QPixmap

    from src.ui import release_icon

    monkeypatch.setattr(release_icon, "load_release_pixmap", lambda *a, **k: QPixmap())
    monkeypatch.setattr(
        "src.release_icons.icon_candidate_urls",
        lambda *a, **k: ["https://example.invalid/updater.png"],
    )

    host = _host()
    host._apply_release_icon()
    first = host._icon_loader
    assert first.wait(5000)
    app.processEvents()

    host._apply_release_icon()
    assert first not in host._retired_workers
    assert host._icon_loader.wait(5000)


def _wait_until(predicate, timeout):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return False
