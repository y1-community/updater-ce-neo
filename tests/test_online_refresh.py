"""The randomized all-source refresh used while the window stays open."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from src import catalog, config, online_refresh


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def test_the_delay_is_between_two_and_eight_minutes():
    for _ in range(50):
        delay = online_refresh.next_interval_ms()
        assert online_refresh.MIN_INTERVAL_MS <= delay <= online_refresh.MAX_INTERVAL_MS

    class _Fixed:
        def randint(self, low, high):
            assert (low, high) == (online_refresh.MIN_INTERVAL_MS, online_refresh.MAX_INTERVAL_MS)
            return low + 1

    assert online_refresh.next_interval_ms(_Fixed()) == online_refresh.MIN_INTERVAL_MS + 1


def test_every_catalogue_repository_is_included_once(monkeypatch):
    packages = [
        catalog.FirmwarePackage(
            "rockbox-y1", "Rockbox", "Y1", "rockbox-y1/rockbox", "rom.zip",
            info_repo="rockbox-y1/info", apk_repo="rockbox-y1/apk",
        ),
        catalog.FirmwarePackage(
            "rockbox-y2", "Rockbox", "Y2", "rockbox-y1/rockbox", "rom_y2.zip",
        ),
    ]
    monkeypatch.setattr(catalog, "_catalog_packages", lambda: packages)
    monkeypatch.setattr(config, "UPDATE_REPO", "owner/updater")

    repos = online_refresh.catalogue_repos()

    assert repos == [
        "rockbox-y1/rockbox",
        "rockbox-y1/info",
        "rockbox-y1/apk",
        "owner/updater",
    ]


def test_worker_forces_every_repository_and_refreshes_config(monkeypatch):
    _app()
    from src import pat_pool

    calls = []
    monkeypatch.setattr(online_refresh, "refresh_pat_pool", lambda: calls.append("config"))

    class _Client:
        def get_all_releases(self, repo, force_refresh=False):
            calls.append((repo, force_refresh))
            return [{"tag_name": "stable-v0.6"}]

    worker = online_refresh.AllReposRefreshWorker(
        repos=["owner/one", "owner/two"], client=_Client()
    )
    results = []
    worker.refreshed.connect(results.append)
    worker.run()

    assert results == [{"owner/one": 1, "owner/two": 1}]
    assert calls == [
        "config",
        ("owner/one", True),
        ("owner/two", True),
    ]


def test_a_finished_sweep_reloads_the_visible_release_list(monkeypatch):
    from src.ui.main_window import MainWindow

    calls = []

    class _Page:
        def isVisible(self):
            return True

        def _can_relist_quietly(self):
            return True

        def _refresh_releases_keep_selection(self, force_refresh=False):
            calls.append(force_refresh)

    page = _Page()
    worker = object()

    class _Host:
        _active_workers = {worker}
        _release_refresh_worker = worker
        _select_page = page

        def _install_run_active(self):
            return False

    MainWindow._on_all_releases_refreshed(_Host(), worker, {"owner/one": 2})

    assert calls == [False]
    assert worker not in _Host._active_workers


def test_a_window_refresh_forces_every_source(monkeypatch):
    from src import notice_service, online_data, release_blocklist
    from src.ui.main_window import MainWindow

    calls = []
    monkeypatch.setattr(
        online_data,
        "refresh_all_async",
        lambda **kwargs: calls.append(("online", kwargs.get("force"))),
    )
    monkeypatch.setattr(
        release_blocklist,
        "refresh_blocklist_async",
        lambda: calls.append(("blocklist", None)),
    )

    class _Notices:
        def fetch_async(self, *args, **kwargs):
            calls.append(("notices", True))

    monkeypatch.setattr(
        notice_service.NoticeService, "instance", staticmethod(lambda: _Notices())
    )

    class _Host:
        _select_page = object()

        def _install_run_active(self):
            return False

        def _window_hidden_in_tray(self):
            return False

        def isVisible(self):
            return True

        def isMinimized(self):
            return False

        def _start_manifest_worker(self, force_refresh=False):
            calls.append(("manifest", force_refresh))

        def _start_all_release_refresh(self):
            calls.append(("releases", None))

    MainWindow._refresh_online_data(_Host())

    assert calls == [
        ("online", True),
        ("manifest", True),
        ("blocklist", None),
        ("notices", True),
        ("releases", None),
    ]


def test_a_window_tick_rearms_with_a_new_random_delay():
    from src.ui.main_window import MainWindow

    calls = []

    class _Timer:
        value = 0

        def start(self, value):
            self.value = value

    timer = _Timer()

    class _Host:
        _online_refresh_timer = timer
        _force_quit = False

        def _refresh_online_data(self):
            calls.append(True)

    MainWindow._online_refresh_tick(_Host())

    assert calls == [True]
    assert online_refresh.MIN_INTERVAL_MS <= timer.value <= online_refresh.MAX_INTERVAL_MS
