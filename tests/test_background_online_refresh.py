"""A background copy keeps the manifest and notices fresh, never donors."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication

from src import background_agent, catalog, online_data


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def test_background_file_policy_leaves_donors_and_feeds_out():
    assert "slidia_manifest.xml" in online_data.BACKGROUND_FILES
    assert "donors.csv" not in online_data.BACKGROUND_FILES
    assert "releases_backup.json" not in online_data.BACKGROUND_FILES
    assert "release_blocklist.csv" not in online_data.BACKGROUND_FILES


def test_refresh_files_only_touches_the_named_files(monkeypatch):
    calls = []
    monkeypatch.setattr(online_data, "seed_cache", lambda names=None: [])
    monkeypatch.setattr(
        online_data,
        "refresh",
        lambda name, **kwargs: calls.append(name) or True,
    )

    result = online_data.refresh_files(("slidia_manifest.xml",), force=True)

    assert calls == ["slidia_manifest.xml"]
    assert result == {"slidia_manifest.xml": True}


def test_the_background_agent_checks_notices_and_never_refreshes_everything(monkeypatch):
    app = _app()
    monkeypatch.setattr(
        online_data,
        "refresh_all_async",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("background refreshed the donors feed")
        ),
    )
    fetched = []
    monkeypatch.setattr(
        background_agent.BackgroundAgent,
        "_refresh_manifest",
        lambda self: fetched.append("manifest"),
    )
    monkeypatch.setattr(
        background_agent.BackgroundAgent,
        "_refresh_all_releases",
        lambda self: fetched.append("releases"),
    )
    from src import notice_service

    monkeypatch.setattr(
        notice_service.NoticeService,
        "fetch_async",
        lambda self, force=True: fetched.append("notices"),
    )

    agent = background_agent.BackgroundAgent(app, lambda: None)
    agent._refresh_online()

    assert fetched == ["manifest", "releases", "notices"]
    agent.shutdown()


def test_the_online_timer_is_between_two_and_eight_minutes():
    from src import online_refresh

    assert online_refresh.MIN_INTERVAL_MS == 2 * 60 * 1000
    assert online_refresh.MAX_INTERVAL_MS == 8 * 60 * 1000
    assert online_refresh.MIN_INTERVAL_MS <= online_refresh.next_interval_ms() <= online_refresh.MAX_INTERVAL_MS
    assert background_agent.BACKGROUND_ONLINE_TICK_MS == online_refresh.MIN_INTERVAL_MS


def test_a_new_manifest_is_loaded_and_forces_a_device_check(monkeypatch):
    app = _app()
    agent = background_agent.BackgroundAgent(app, lambda: None)
    forced = []
    monkeypatch.setattr(
        agent,
        "_check_device_updates",
        lambda force_refresh=False: forced.append(force_refresh),
    )
    original = list(catalog.LIVE_CATALOG)
    try:
        entries = [
            catalog.FirmwarePackage(
                "g1-stock", "Original Software", "G1", "owner/g1-rom", "rom.zip"
            )
        ]
        agent._on_manifest_refreshed(entries)

        assert catalog.LIVE_CATALOG == entries
        assert forced == [True]
    finally:
        catalog.set_live_catalog(original)
        agent.shutdown()


def test_device_updates_can_bypass_the_release_cache(monkeypatch):
    from src import player_update_check

    seen = {}

    class FakeClient:
        def releases_for_package(
            self, package, model, show_nightly=False, force_refresh=False
        ):
            seen["force_refresh"] = force_refresh
            return []

    monkeypatch.setattr(player_update_check, "DEVICE_MODELS", ("G1",))
    monkeypatch.setattr(
        player_update_check,
        "is_device_reminder_enabled",
        lambda model, settings=None: True,
    )
    monkeypatch.setattr(
        player_update_check,
        "get_device_install",
        lambda model, settings=None: {
            "tag_name": "v1",
            "package_slug": "g1-stock",
            "software_name": "Original Software",
        },
    )
    monkeypatch.setattr(
        catalog,
        "packages_for_model",
        lambda model: [
            catalog.FirmwarePackage(
                "g1-stock", "Original Software", "G1", "owner/g1-rom", "rom.zip"
            )
        ],
    )

    player_update_check.check_device_updates(
        releases_client=FakeClient(), force_refresh=True
    )

    assert seen["force_refresh"] is True
