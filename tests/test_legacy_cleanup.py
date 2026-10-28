"""Unit tests for legacy pre-3.0 cleanup service and safety invariants."""

import os
import sys
import shutil
import tempfile
from pathlib import Path

# Ensure src is importable
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from src.legacy_cleanup import (
    is_protected_app,
    format_bytes,
    _calc_dir_size,
    detect_legacy_installations,
    remove_macos_legacy,
    remove_linux_legacy,
)


def test_protected_app_invariants():
    """Current product bundles are protected — including 'Innioasis Updater.app'.

    The macOS product now carries the pre-3.0 run_mac.sh name, so that path is
    refused by name: the app may be running from it, and a copy sitting in
    /Applications may already *be* the new app. Nothing in this service may
    ever offer to delete it. The no-space 'InnioasisUpdater.app' standalone
    tool is still an old app, and the running bundle is refused whatever it is
    called.
    """
    from src import legacy_cleanup

    assert is_protected_app(Path("/Applications/Innioasis Updater.app")) is True
    assert is_protected_app(Path("~/Applications/Innioasis Updater.app")) is True
    assert is_protected_app(Path("/Applications/Updater CE.app")) is True
    assert is_protected_app(Path("/Applications/UpdaterCE.app")) is True
    assert is_protected_app(Path("/Applications/MediaTek Installer.app")) is True

    # The no-space standalone tool is still an old app.
    assert is_protected_app(Path("/Applications/InnioasisUpdater.app")) is False

    # …but never the bundle this process runs from, even when that name is not
    # in the protected set at all.
    original = legacy_cleanup._running_app_bundle
    legacy_cleanup._running_app_bundle = lambda: Path("/Applications/InnioasisUpdater.app")
    try:
        assert is_protected_app(Path("/Applications/InnioasisUpdater.app")) is True
        assert is_protected_app(Path("/Applications/Innioasis Updater.app")) is True
    finally:
        legacy_cleanup._running_app_bundle = original


def test_detection_offers_only_the_no_space_old_app(tmp_path=None):
    """Only the no-space bundle is offered; 'Innioasis Updater.app' is current."""
    import tempfile

    from src import legacy_cleanup

    original_dirs = legacy_cleanup.LEGACY_APP_DIRS
    old_sim = os.environ.get("INNIOASIS_SIMULATE_MACOS")
    os.environ["INNIOASIS_SIMULATE_MACOS"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "Innioasis Updater.app").mkdir()
        (root / "InnioasisUpdater.app").mkdir()
        (root / "Updater CE.app").mkdir()  # current product: never offered
        legacy_cleanup.LEGACY_APP_DIRS = (root,)
        try:
            info = legacy_cleanup.detect_legacy_installations()
            names = sorted(item.name for item in info["macos_apps"])
            assert names == ["InnioasisUpdater.app"], names
            assert info["has_legacy"] is True
        finally:
            legacy_cleanup.LEGACY_APP_DIRS = original_dirs
            if old_sim is None:
                os.environ.pop("INNIOASIS_SIMULATE_MACOS", None)
            else:
                os.environ["INNIOASIS_SIMULATE_MACOS"] = old_sim


def test_removal_takes_the_no_space_old_app_only():
    """The no-space bundle goes; 'Innioasis Updater.app' and current ones stay."""
    import tempfile

    from src import legacy_cleanup

    original_dirs = legacy_cleanup.LEGACY_APP_DIRS
    old_sim = os.environ.get("INNIOASIS_SIMULATE_MACOS")
    os.environ["INNIOASIS_SIMULATE_MACOS"] = "1"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        spaced = root / "Innioasis Updater.app"  # now the current product name
        nospace = root / "InnioasisUpdater.app"
        current = root / "Updater CE.app"
        for bundle in (spaced, nospace, current):
            bundle.mkdir()
            (bundle / "marker").write_text("x", encoding="utf-8")
        legacy_cleanup.LEGACY_APP_DIRS = (root,)
        try:
            ok, removed, errors = legacy_cleanup.remove_macos_legacy(
                remove_apps=True, remove_app_support=False,
                uninstall_platform_tools=False,
            )
            assert errors == [], errors
            assert ok is True
            assert sorted(Path(item).name for item in removed) == [
                "InnioasisUpdater.app",
            ], removed
            assert spaced.is_dir(), "the current macOS product name must never be removed"
            assert not nospace.exists()
            assert current.is_dir(), "a current product bundle must never be removed"
        finally:
            legacy_cleanup.LEGACY_APP_DIRS = original_dirs
            if old_sim is None:
                os.environ.pop("INNIOASIS_SIMULATE_MACOS", None)
            else:
                os.environ["INNIOASIS_SIMULATE_MACOS"] = old_sim


def test_format_bytes():
    assert format_bytes(500) == "500 B"
    assert "KB" in format_bytes(2048)
    assert "MB" in format_bytes(10 * 1024 * 1024)
    assert "GB" in format_bytes(2 * 1024 * 1024 * 1024)


def test_calc_dir_size():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        f1 = tdp / "file1.bin"
        f1.write_bytes(b"A" * 1000)
        sub = tdp / "sub"
        sub.mkdir()
        f2 = sub / "file2.bin"
        f2.write_bytes(b"B" * 2000)

        total = _calc_dir_size(tdp)
        assert total >= 3000


def test_legacy_dialog_instantiation():
    """Verify LegacyMigrationDialog can instantiate cleanly headlessly."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(["--platform", "offscreen"])

    from src.ui.dialogs import LegacyMigrationDialog

    mock_info = {
        "has_legacy": True,
        "macos_apps": [Path("/Applications/Innioasis Updater.app")],
        "macos_app_support": Path("/tmp/mock_app_support"),
        "has_platform_tools": True,
        "platform_tools_type": "brew",
        "estimated_bytes": 500 * 1024 * 1024,
        "items_summary": ["Innioasis Updater.app (80 MB)", "Support Files (420 MB)"],
    }
    dlg = LegacyMigrationDialog(scan_info=mock_info)
    assert dlg.windowTitle() != ""
    assert dlg.cb_remove_legacy.isChecked() is True
    assert hasattr(dlg, "cb_keep_platform_tools")
    assert dlg.cb_keep_platform_tools.isChecked() is True
    app.processEvents()


def test_remove_old_app_card_follows_the_detection():
    """The Settings card is "Remove old app" and only for a real pre-3.0 install."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import src.legacy_cleanup as legacy
    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    original_lang = translator().lang
    original_detect = legacy.detect_legacy_installations
    translator().set_language("en")
    page = None
    try:
        # Stray platform tools are not "an old version of the app".
        legacy.detect_legacy_installations = lambda: {
            "has_legacy": False,
            "has_platform_tools": True,
            "platform_tools_paths": [Path("/tmp/mock/adb")],
            "estimated_bytes": 0,
        }
        page = SettingsPage()
        assert page._legacy_cleanup_card.isHidden(), "platform tools alone must not offer removal"

        legacy.detect_legacy_installations = lambda: {
            "has_legacy": True,
            "has_platform_tools": False,
            "macos_apps": [Path("/Applications/Innioasis Updater.app")],
            "estimated_bytes": 1024,
        }
        page._update_legacy_cleanup_visibility()
        assert not page._legacy_cleanup_card.isHidden()
        assert page._legacy_cleanup_card.title() == "Remove old app"
        assert page._legacy_uninstall_btn.text() == "Uninstall"
        assert "older version" in page._legacy_cleanup_desc.text()

        # …and it goes away again once the old app is gone.
        legacy.detect_legacy_installations = lambda: {"has_legacy": False, "estimated_bytes": 0}
        page._update_legacy_cleanup_visibility()
        assert page._legacy_cleanup_card.isHidden()
    finally:
        legacy.detect_legacy_installations = original_detect
        translator().set_language(original_lang)
        if page is not None:
            page.deleteLater()
            app.processEvents()


def _answer_card(page, answers):
    """Answer each in-card confirmation as it opens (True = accept button).

    Returns the accept-button label of every question that was put to the
    user, in order, so a test can prove which questions the card asked.
    """
    from PySide6.QtCore import QTimer

    pending = list(answers)
    asked = []

    def tick():
        card = page._legacy_cleanup_card
        if getattr(card, "_confirm_accept", None) is None:
            QTimer.singleShot(0, tick)
            return
        asked.append(card._confirm_accept.text())
        answer = pending.pop(0) if pending else True
        (card._confirm_accept if answer else card._confirm_reject).click()
        if pending:
            QTimer.singleShot(0, tick)

    QTimer.singleShot(0, tick)
    return asked


def test_platform_tools_question_asks_and_is_honoured():
    """The card asks before taking ADB, and the answer decides the removal."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import src.legacy_cleanup as legacy
    from src.i18n import tr, translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    original_lang = translator().lang
    original_detect = legacy.detect_legacy_installations
    original_mac = legacy.remove_macos_legacy
    original_linux = legacy.remove_linux_legacy
    translator().set_language("en")
    calls = []

    def record(name):
        def run(**kwargs):
            calls.append((name, dict(kwargs)))
            return True, ["/tmp/removed"], []

        return run

    legacy.remove_macos_legacy = record("mac")
    legacy.remove_linux_legacy = record("linux")
    page = None
    try:
        legacy.detect_legacy_installations = lambda: {
            "has_legacy": True,
            "has_platform_tools": True,
            "macos_apps": [Path("/Applications/Innioasis Updater.app")],
            "platform_tools_paths": [Path("/tmp/mock/adb")],
            "estimated_bytes": 2 * 1024 * 1024,
        }
        page = SettingsPage()

        # 1. Keep the tools: the app is removed, the tools are left alone.
        asked = _answer_card(page, [True, True])
        page._on_scan_legacy_installations()
        app.processEvents()
        assert asked == ["Uninstall", "Keep"], asked
        assert len(calls) == 1, calls
        assert calls[0][1]["uninstall_platform_tools"] is False
        assert "ADB" in tr("legacy_cleanup_tools_body")

        # 2. Remove the tools as well: the same two questions, the other answer
        # on the second one, and that answer reaches the cleanup service.
        calls.clear()
        asked = _answer_card(page, [True, False])
        page._on_scan_legacy_installations()
        app.processEvents()
        assert asked == ["Uninstall", "Keep"], asked
        assert len(calls) == 1, calls
        assert calls[0][1]["uninstall_platform_tools"] is True

        # 3. Walking away at the app question asks nothing else and removes nothing.
        calls.clear()
        asked = _answer_card(page, [False])
        page._on_scan_legacy_installations()
        app.processEvents()
        assert asked == ["Uninstall"], asked
        assert calls == []
    finally:
        legacy.detect_legacy_installations = original_detect
        legacy.remove_macos_legacy = original_mac
        legacy.remove_linux_legacy = original_linux
        translator().set_language(original_lang)
        if page is not None:
            page.deleteLater()
            app.processEvents()


def test_removal_journey_stays_in_the_card():
    """Uninstall asks, removes and reports inside the card — no modal window."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    import src.legacy_cleanup as legacy
    import src.ui.dialogs as dialogs
    import src.ui.settings_page as settings_page
    from src.i18n import translator
    from src.ui.settings_page import SettingsPage

    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    original_lang = translator().lang
    original_detect = legacy.detect_legacy_installations
    original_mac = legacy.remove_macos_legacy
    original_linux = legacy.remove_linux_legacy
    original_dialog = dialogs.LegacyMigrationDialog
    translator().set_language("en")
    calls = []
    opened = []

    def fake_mac(**kwargs):
        calls.append(("mac", kwargs))
        return True, ["/Applications/Innioasis Updater.app"], []

    def fake_linux(**kwargs):
        calls.append(("linux", kwargs))
        return True, ["~/.local/share/applications/innioasis.desktop"], []

    class NoDialog:
        def __init__(self, *a, **kw):
            opened.append(a)
            raise AssertionError("the removal journey must not open a dialog")

    page = None
    try:
        legacy.detect_legacy_installations = lambda: {
            "has_legacy": True,
            "has_platform_tools": False,
            "macos_apps": [Path("/Applications/Innioasis Updater.app")],
            "estimated_bytes": 428 * 1024 * 1024,
        }
        legacy.remove_macos_legacy = fake_mac
        legacy.remove_linux_legacy = fake_linux
        dialogs.LegacyMigrationDialog = NoDialog

        page = SettingsPage()
        # Accept the in-card confirmation, then let the removal run.
        QTimer.singleShot(0, lambda: page._legacy_cleanup_card._confirm_accept.click())
        page._on_scan_legacy_installations()
        app.processEvents()

        assert opened == [], opened
        assert len(calls) == 1, calls
        platform_id, kwargs = calls[0]
        if platform_id == "mac":
            assert kwargs == {
                "remove_apps": True,
                "remove_app_support": True,
                "uninstall_platform_tools": False,
            }
        else:
            assert kwargs == {
                "remove_install_dir": True,
                "uninstall_platform_tools": False,
            }
        # The result lands on the card itself, without an information box.
        assert page._legacy_status_lbl.isHidden() is False
        assert "428" in page._legacy_status_lbl.text()

        # Declining changes nothing.
        legacy.detect_legacy_installations = lambda: {
            "has_legacy": True,
            "estimated_bytes": 1024,
        }
        QTimer.singleShot(0, lambda: page._legacy_cleanup_card._confirm_reject.click())
        page._on_scan_legacy_installations()
        app.processEvents()
        assert len(calls) == 1, calls

        # Nothing left: the card says so in place and the removal is not run.
        page._legacy_status_lbl.clear()
        legacy.detect_legacy_installations = lambda: {"has_legacy": False, "estimated_bytes": 0}
        page._on_scan_legacy_installations()
        app.processEvents()
        assert len(calls) == 1, calls
        assert page._legacy_status_lbl.text() == "No old version was found."
        assert not page._legacy_status_lbl.isHidden()

        # Once the old app is gone the card goes too, status line included.
        settings_page.QTimer.singleShot(0, lambda: None)
        page._update_legacy_cleanup_visibility()
        assert page._legacy_cleanup_card.isHidden()
        assert page._legacy_status_lbl.text() == ""
    finally:
        legacy.detect_legacy_installations = original_detect
        legacy.remove_macos_legacy = original_mac
        legacy.remove_linux_legacy = original_linux
        dialogs.LegacyMigrationDialog = original_dialog
        translator().set_language(original_lang)
        if page is not None:
            page.deleteLater()
            app.processEvents()


def test_linux_offer_needs_the_old_downloader():
    """A bare install directory is not an old install.

    The current release installs into the same folder, so the directory
existing proves nothing. Without the pre-3.0 downloader inside it there is
    nothing for the app to help remove, so the offer is never made.
    """
    from src import legacy_cleanup

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "innioasis-updater"
        folder.mkdir()
        assert legacy_cleanup.looks_like_pre_3_0_linux_install(folder) is False
        (folder / legacy_cleanup.PRE_3_0_LINUX_MARKER).write_text("")
        assert legacy_cleanup.looks_like_pre_3_0_linux_install(folder) is True


def test_detection_follows_the_old_downloader_marker(monkeypatch):
    """The Linux half of the detection follows the marker, not the directory.

    The card is an offer to uninstall a pre-3.0 install, and a folder with no
    downloader in it is not one -- which is also what makes the card disappear
    again once the removal has taken the downloader away.
    """
    from src import legacy_cleanup

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        folder = root / "innioasis-updater"
        folder.mkdir()
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("INNIOASIS_SIMULATE_MACOS", raising=False)
        monkeypatch.setattr(legacy_cleanup, "LEGACY_LINUX_INSTALL_DIR", folder)
        # The shared paths belong to whatever is installed; keep them out of it.
        monkeypatch.setattr(legacy_cleanup, "LEGACY_LINUX_DESKTOP_FILE", root / "none.desktop")
        monkeypatch.setattr(legacy_cleanup, "LEGACY_LINUX_BIN_LAUNCHER", root / "no-bin")
        monkeypatch.setattr(legacy_cleanup, "LEGACY_LINUX_CACHE_DIR", root / "no-cache")
        # Isolate host macOS paths from this Linux marker unit test
        monkeypatch.setattr(legacy_cleanup, "LEGACY_APP_DIRS", ())
        monkeypatch.setattr(legacy_cleanup, "LEGACY_SUPPORT_DIR", root / "no-support")
        monkeypatch.setattr(legacy_cleanup, "LEGACY_SUPPORT_DIRS", ())

        info = detect_legacy_installations()
        assert info["linux_install_dir"] is None
        assert info["has_legacy"] is False

        (folder / legacy_cleanup.PRE_3_0_LINUX_MARKER).write_text("")
        info = detect_legacy_installations()
        assert info["linux_install_dir"] == folder
        assert info["has_legacy"] is True


def test_macos_legacy_is_the_downloader_marker_not_the_app_name(monkeypatch):
    """The shared folder is old only while firmware_downloader.py is at its root.

    'Innioasis Updater.app' is the current product name, so the bundle itself
    never raises the offer. The marker in Application Support is what does —
    and once it is gone there is nothing left to offer, even if the old .app
    still sits in Applications.
    """
    from src import legacy_cleanup

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        app_dir = root / "Applications"
        app_dir.mkdir()
        supp_dir = root / "Application Support" / "Innioasis Updater"
        supp_dir.mkdir(parents=True)

        monkeypatch.setattr(legacy_cleanup, "LEGACY_APP_DIRS", (app_dir,))
        monkeypatch.setattr(legacy_cleanup, "LEGACY_SUPPORT_DIR", supp_dir)
        monkeypatch.setattr(legacy_cleanup, "LEGACY_SUPPORT_DIRS", (supp_dir,))
        monkeypatch.setenv("INNIOASIS_SIMULATE_MACOS", "1")

        # 1. Neither present: has_legacy is False
        info = legacy_cleanup.detect_legacy_installations()
        assert info["has_legacy"] is False
        assert info["macos_apps"] == []
        assert info["macos_app_support"] is None

        # 2. firmware_downloader.py in Application Support raises the offer
        #    (even if the user deleted the .app).
        downloader = supp_dir / "firmware_downloader.py"
        downloader.write_text("# legacy downloader\n", encoding="utf-8")
        info = legacy_cleanup.detect_legacy_installations()
        assert info["has_legacy"] is True
        assert info["macos_apps"] == []
        assert info["macos_app_support"] == supp_dir

        # 3. The .app in Applications is the current product name: never
        #    detected, never offered — only the support-folder marker counts.
        old_app = app_dir / "Innioasis Updater.app"
        old_app.mkdir()
        info = legacy_cleanup.detect_legacy_installations()
        assert info["has_legacy"] is True
        assert old_app not in info["macos_apps"]
        assert info["macos_apps"] == []
        assert info["macos_app_support"] == supp_dir

        # 4. Marker gone: the old app version is already gone — the .app alone
        #    raises nothing, and the folder is left to the current app.
        downloader.unlink()
        info = legacy_cleanup.detect_legacy_installations()
        assert info["has_legacy"] is False
        assert info["macos_apps"] == []
        assert info["macos_app_support"] is None


def test_clear_legacy_support_now_is_marker_gated(monkeypatch):
    """Contents clear only while firmware_downloader.py marks the old app.

    Without the marker the folder is the current app's own home — its downloads
    and cache stay untouched. With it, every entry goes and the folder itself
    survives, because the app keeps storing its files there.
    """
    from src import legacy_cleanup

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "Innioasis Updater"
        (folder / "downloads").mkdir(parents=True)
        own_file = folder / "downloads" / "rom.zip"
        own_file.write_text("x", encoding="utf-8")
        (folder / ".cache").mkdir()
        (folder / ".cache" / "catalog.json").write_text("{}", encoding="utf-8")
        monkeypatch.setattr(legacy_cleanup, "LEGACY_SUPPORT_DIR", folder)

        # No marker: the app's own files are never treated as legacy.
        ok, note = legacy_cleanup.clear_legacy_support_now()
        assert ok is False
        assert "no legacy marker" in note
        assert own_file.is_file()

        # Marker present: every entry goes, the folder stays.
        (folder / "firmware_downloader.py").write_text("# old app", encoding="utf-8")
        ok, note = legacy_cleanup.clear_legacy_support_now()
        assert ok is True, note
        assert folder.is_dir()
        assert list(folder.iterdir()) == []

        # And a second call is a no-op: without the marker, nothing to clear.
        ok, note = legacy_cleanup.clear_legacy_support_now()
        assert ok is False


if __name__ == "__main__":
    test_protected_app_invariants()
    test_format_bytes()
    test_calc_dir_size()
    test_legacy_dialog_instantiation()
    test_remove_old_app_card_follows_the_detection()
    test_detection_offers_only_the_no_space_old_app()
    test_removal_takes_the_no_space_old_app_only()
    test_platform_tools_question_asks_and_is_honoured()
    test_removal_journey_stays_in_the_card()
    print("All legacy cleanup tests passed!")
