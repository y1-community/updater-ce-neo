"""The release list hugs its rows and the window follows its height."""

import os
import sys
import time
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.ui.select_page import (
    EMPTY_RELEASE_LIST_ROWS,
    MAX_VISIBLE_RELEASE_ROWS,
    SelectPackagePage,
)


def _fake_releases(n):
    return [
        {
            "tag_name": f"v{i}", "name": f"v{i}", "body": "",
            "published_at": "", "download_url": "https://x/rom.zip",
            "asset_name": "rom.zip", "rom_variants": [],
            "prerelease": False,
        }
        for i in range(n)
    ]


def _expected_list_height(lst, rows):
    rows = min(rows, MAX_VISIBLE_RELEASE_ROWS)
    return (
        sum(max(1, lst.sizeHintForRow(i)) for i in range(rows))
        + 2 * lst.frameWidth()
        + 2
    )


def test_release_list_hugs_contents_and_caps():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    page = SelectPackagePage()
    app.processEvents()
    lst = page._release_list
    emitted = []
    page.content_size_changed.connect(lambda: emitted.append(1))

    # Empty list keeps a small placeholder instead of collapsing.
    page._on_releases_loaded([], "")
    assert lst.count() == 0
    assert lst.minimumHeight() == lst.maximumHeight() > 0

    for n in (1, 2, 5, MAX_VISIBLE_RELEASE_ROWS):
        page._on_releases_loaded(_fake_releases(n), "")
        assert lst.count() == n
        assert lst.minimumHeight() == lst.maximumHeight()
        assert lst.minimumHeight() == _expected_list_height(lst, n), n

    # Past the cap the list stops growing and scrolls internally.
    page._on_releases_loaded(_fake_releases(MAX_VISIBLE_RELEASE_ROWS + 12), "")
    assert lst.count() == MAX_VISIBLE_RELEASE_ROWS + 12
    assert lst.minimumHeight() == lst.maximumHeight()
    assert lst.minimumHeight() == _expected_list_height(lst, MAX_VISIBLE_RELEASE_ROWS)

    assert emitted, "refills must notify the window"


def _freeze_background_listing(w, sp):
    """Stop background refresh workers so test fills are stable.

    Finished worker callbacks are identity-guarded (``worker is
    self._releases_worker`` / ``worker is self._manifest_worker``), so the
    slots are parked on a sentinel: nulling them is not enough, because an
    already-queued completion reads the slot at fire time and ``None``
    would pass the guard. Returns a ``restore`` callable that puts the
    real (now idle) workers back so window teardown behaves normally.
    """
    sentinel = object()
    originals = []
    for owner, attr in (
        (sp, "_releases_worker"),
        (sp, "_manifest_worker"),
        (w, "_manifest_worker"),
    ):
        worker = getattr(owner, attr, None)
        originals.append((owner, attr, worker))
        if worker is None:
            continue
        try:
            worker.finished.disconnect()
        except Exception:
            pass
        for meth in ("requestInterruption", "quit"):
            try:
                getattr(worker, meth)()
            except Exception:
                pass
        try:
            setattr(owner, attr, sentinel)
        except Exception:
            pass

    def _restore():
        for owner, attr, worker in originals:
            try:
                if getattr(owner, attr, None) is sentinel:
                    setattr(owner, attr, worker)
            except Exception:
                pass

    return _restore


def _fill_big_rows(sp, n, point_size=28):
    """Fill the list and enlarge rows to desktop scale.

    Real row heights come from each item's font, so the big font must go
    on the items (plus the list, for the empty placeholder path). Re-fits
    and notifies after enlarging.
    """
    from PySide6.QtGui import QFont

    big = QFont()
    big.setPointSize(point_size)
    sp._release_list.setFont(big)
    sp._on_releases_loaded(_fake_releases(n), "")
    for row in range(sp._release_list.count()):
        item = sp._release_list.item(row)
        if item is not None:
            item.setFont(big)
    sp._fit_release_list_height()
    sp.content_size_changed.emit()


def _pump_until(app, pred, timeout_s=4.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
        if pred():
            return True
    app.processEvents()
    return bool(pred())


def _quiesce(app, window, timeout_s=10.0):
    """Wait for startup background refreshes (manifest, releases)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
        mw = getattr(window, "_manifest_worker", None)
        rw = getattr(window._select_page, "_releases_worker", None)
        if (mw is None or not mw.isRunning()) and (rw is None or not rw.isRunning()):
            app.processEvents()
            time.sleep(0.05)
            app.processEvents()
            return True
    return False


def test_window_grows_with_list_but_never_shrinks():
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow, MINIMUM_WINDOW_HEIGHT

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    w = MainWindow()
    w.show()
    assert _quiesce(app, w)
    sp = w._select_page
    restore_workers = _freeze_background_listing(w, sp)
    try:
        # Offscreen fonts are tiny, so rows would never outgrow the
        # default window: use desktop-scale rows for the growth path.
        _fill_big_rows(sp, 2)
        assert _pump_until(app, lambda: True)
        base_h = w.height()
        assert base_h >= MINIMUM_WINDOW_HEIGHT

        _fill_big_rows(sp, MAX_VISIBLE_RELEASE_ROWS + 10)
        assert _pump_until(app, lambda: w.height() > base_h), (base_h, w.height())
        grown_h = w.height()

        # A shorter list keeps the size the window already has.
        _fill_big_rows(sp, 1)
        assert _pump_until(app, lambda: True)
        assert w.height() == grown_h
    finally:
        restore_workers()
    w.close()
    app.processEvents()


def test_fit_skipped_while_install_owns_window():
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    w = MainWindow()
    w.show()
    assert _quiesce(app, w)
    sp = w._select_page
    restore_workers = _freeze_background_listing(w, sp)
    try:
        _fill_big_rows(sp, MAX_VISIBLE_RELEASE_ROWS + 10)
        assert _pump_until(app, lambda: True)
        settled_h = w.height()

        w._power_off_prompt = True
        try:
            _fill_big_rows(sp, 1)
            app.processEvents()
            time.sleep(0.3)
            app.processEvents()
            assert w.height() == settled_h
        finally:
            w._power_off_prompt = False
    finally:
        restore_workers()
    w.close()
    app.processEvents()
