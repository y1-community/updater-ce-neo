"""Tests for the GitHub PAT pool, update checker, and assisted installer dialog."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QApplication

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.pat_pool import PatPoolManager, normalize_pat, get_random_pats, report_pat_status
from src.updates import UpdateChecker, UpdateInfo, is_newer, parse_version, pick_platform_asset, asset_hint
from src.ui.dialogs import UpdateAvailableDialog


def _app():
    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def test_pat_normalization():
    # Token without prefix
    raw = "11BUHMFQQ0bLMGKHwuj2h8_C6857artTYRi3u6lmPHz3JXatyH1DPAj3ypRQllUmmERZIMUT3NcfUqUXnV"
    norm = normalize_pat(raw)
    assert norm.startswith("github_pat_")
    assert norm == f"github_pat_{raw}"

    # Token with existing prefix
    prefixed = "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"
    assert normalize_pat(prefixed) == prefixed

    # Empty string
    assert normalize_pat("") == ""
    assert normalize_pat(None) == ""


def test_pat_pool_selection_and_failure_reporting():
    pool = PatPoolManager()
    pats = pool.get_candidate_pats(count=3)
    assert len(pats) == 3
    for p in pats:
        assert p.startswith("github_pat_")

    # Reporting 401 on a token marks it as bad and excludes it
    bad_pat = pats[0]
    pool.report_token_status(bad_pat, 401)
    new_pats = pool.get_candidate_pats(count=3)
    assert bad_pat not in new_pats


def test_pick_platform_asset_targets():
    assets = [
        {"name": "Innioasis.Updater.for.Mac.dmg", "browser_download_url": "https://example.com/mac.dmg", "size": 100},
        {"name": "installer.exe", "browser_download_url": "https://example.com/installer.exe", "size": 200},
        {"name": "UpdaterCE.appimage", "browser_download_url": "https://example.com/linux.appimage", "size": 300},
        {"name": "updater_ce_linux_portable.tar.gz", "browser_download_url": "https://example.com/linux.tar.gz", "size": 310},
        {"name": "updater_ce_linux_portable.zip", "browser_download_url": "https://example.com/linux.zip", "size": 320},
    ]

    win_asset = pick_platform_asset(assets, platform="win32")
    assert win_asset is not None
    assert win_asset["name"] == "installer.exe"

    mac_asset = pick_platform_asset(assets, platform="darwin")
    assert mac_asset is not None
    assert mac_asset["name"] == "Innioasis.Updater.for.Mac.dmg"

    linux_asset = pick_platform_asset(assets, platform="linux")
    assert linux_asset is not None
    assert linux_asset["name"] == "UpdaterCE.appimage"

    # Test Linux portable fallback when appimage is absent
    linux_no_appimage = [a for a in assets if not a["name"].endswith(".appimage")]
    linux_tar = pick_platform_asset(linux_no_appimage, platform="linux")
    assert linux_tar is not None
    assert linux_tar["name"] == "updater_ce_linux_portable.tar.gz"


def test_pick_platform_asset_isolation():
    """Ensure Linux zip is never picked for Windows."""
    linux_assets = [
        {"name": "updater_ce_linux_portable.zip", "browser_download_url": "https://example.com/linux.zip", "size": 100},
    ]
    assert pick_platform_asset(linux_assets, platform="win32") is None


def test_linux_picks_portable_package_not_installer_script():
    """Choose an installable app package, never the helper shell script."""
    windows_only = [
        {"name": "installer.exe", "browser_download_url": "https://example.com/w.exe", "size": 100},
        {"name": "UpdaterCE-2.5-windows-portable.zip", "browser_download_url": "https://example.com/w.zip", "size": 100},
    ]
    assert pick_platform_asset(windows_only, platform="linux") is None
    assert pick_platform_asset(windows_only, platform="darwin") is None

    linux_packages = [
        {"name": "run_linux.sh", "browser_download_url": "https://example.com/run.sh", "size": 50},
        {"name": "UpdaterCE.tar.gz", "browser_download_url": "https://example.com/updater.tar.gz", "size": 100},
    ]
    picked = pick_platform_asset(linux_packages, platform="linux")
    assert picked is not None
    assert picked["name"] == "UpdaterCE.tar.gz"


def test_update_checker_multiple_releases():
    """UpdateChecker evaluates all releases to find the newest that has assets for the OS."""
    mock_client = MagicMock()
    mock_client.get_all_update_releases.return_value = [
        {
            "tag_name": "3.1.0",
            "name": "3.1.0 (macOS only)",
            "body": "macOS update",
            "assets": [{"name": "Innioasis.Updater.for.Mac.dmg", "browser_download_url": "http://example.com/mac.dmg"}],
        },
        {
            "tag_name": "3.0.5",
            "name": "3.0.5 (Windows & Linux)",
            "body": "Cross-platform update",
            "assets": [
                {"name": "installer.exe", "browser_download_url": "http://example.com/installer.exe"},
                {"name": "UpdaterCE.appimage", "browser_download_url": "http://example.com/appimage"},
            ],
        },
        {
            "tag_name": "flash_tool",
            "name": "SP Flash Tool",
            "body": "Tools",
            "assets": [],
        },
    ]

    # Windows checker: should skip 3.1.0 (no win asset) and pick 3.0.5
    win_checker = UpdateChecker(client=mock_client, current="2.5")
    win_info = win_checker.check(force=True)
    assert win_info.tag == "3.0.5"

    # If current is 3.0.5, Windows checker sees no newer Windows release
    win_checker_current = UpdateChecker(client=mock_client, current="3.0.5")
    assert win_checker_current.check(force=True).tag == ""


def test_update_available_dialog_ui():
    app = _app()
    info = UpdateInfo(
        tag="3.0.1",
        name="Version 3.0.1",
        body="Fixes and improvements",
        assets=[{"name": "installer.exe", "browser_download_url": "https://example.com/installer.exe", "size": 1024}],
    )
    dlg = UpdateAvailableDialog(info=info, current_version="2.5")
    dlg.show()
    app.processEvents()

    try:
        assert dlg._action_btn is not None
        assert dlg._cancel_btn is not None
        assert dlg._notes.toPlainText() == "Fixes and improvements"
        assert dlg._progress_bar.isHidden()
    finally:
        dlg.close()
        app.processEvents()


def test_linux_update_click_opens_guide():
    app = _app()
    info = UpdateInfo(
        tag="3.0.1",
        name="Version 3.0.1",
        body="Linux build",
        assets=[{"name": "UpdaterCE.appimage", "browser_download_url": "https://example.com/appimage", "size": 1024}],
    )
    dlg = UpdateAvailableDialog(info=info, current_version="2.5")
    dlg.show()
    app.processEvents()

    opened_urls = []
    with patch("src.browser.open_browser", lambda url: opened_urls.append(url)):
        with patch("sys.platform", "linux"):
            dlg._start_update_download()

    assert "https://innioasis.app/guide.html" in opened_urls
    dlg.close()
    app.processEvents()


def test_app_update_download_progress_advances_past_connecting():
    """The app-update bar must follow the download, not sit on "Connecting...".

    DownloadWorker.progress emits a single 0-100 int (and .status the byte /
    speed line). The dialog used to connect a three-argument slot, so every
    emission raised TypeError inside the slot and the label never changed.
    """
    from PySide6.QtCore import QObject, Signal

    app = _app()
    info = UpdateInfo(
        tag="3.0.1",
        name="Version 3.0.1",
        body="Fixes and improvements",
        assets=[
            {
                "name": "UpdaterCE-Setup-3.0.1.exe",
                "browser_download_url": "https://example.com/UpdaterCE-Setup-3.0.1.exe",
                "size": 25 * 1024 * 1024,
            }
        ],
    )
    dlg = UpdateAvailableDialog(info=info, current_version="2.5")
    dlg.show()
    app.processEvents()

    class FakeWorker(QObject):
        progress = Signal(int)
        status = Signal(str)
        finished = Signal(bool, str)

        def __init__(self, url, destination, token="", parent=None):
            super().__init__(parent)
            self.destination = destination

        def start(self):
            self.progress.emit(42)
            self.status.emit("10.0 MB / 25.0 MB (42%) — 2.1 MB/s")

    try:
        with patch("src.downloads.DownloadWorker", FakeWorker), patch("sys.platform", "win32"):
            dlg._start_update_download()
            app.processEvents()

        assert dlg._progress_bar.value() == 42
        assert dlg._progress_label.text() == "10.0 MB / 25.0 MB (42%) — 2.1 MB/s"
        assert "Connecting" not in dlg._progress_label.text()
    finally:
        dlg.close()
        app.processEvents()
