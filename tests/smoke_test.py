"""Smoke test for the Neo updater prototype.

Run:  python tests/smoke_test.py    (from the project root, with deps installed)
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Import the application as the ``src`` package.
import src  # noqa: F401
from src import catalog, donors  # noqa: E402
from src.flash_service import (  # noqa: E402
    _find_scatter,
    _list_expected_images,
    _missing_images,
    _parse_scatter,
    _parse_scatter_platform,
)
from src.catalog import (  # noqa: E402
    ReleasesClient,
    packages_for_model,
    packages_for_model_software,
    select_preferred_rom_asset,
    software_names_for_model,
    _parse_rom_asset_variant,
)

failures = []


def _reset_app_settings():
    """Reset persisted app settings so tests are deterministic regardless of
    what previous runs (or manual launches) left in QSettings."""
    from PySide6.QtCore import QSettings
    from src.i18n import translator

    s = QSettings("innioasis", "updater")
    s.setValue("language", "en")
    s.setValue("flash_method", "auto")
    s.setValue("update_skipped_version", "")
    translator().set_language("en")


def check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as e:
        failures.append((name, e))
        print(f"  FAIL  {name}: {e!r}")


def test_catalog():
    assert "Rockbox" in software_names_for_model("Y1")
    assert "Rockbox" in software_names_for_model("Y2")
    assert "Inniclassic" in software_names_for_model("Y1")
    assert "Inniclassic" not in software_names_for_model("Y2")
    assert "Y2Player" in software_names_for_model("Y2")
    pkgs = packages_for_model_software("Y1", "Original Software")
    assert len(pkgs) == 1 and pkgs[0].package_name == "rom.zip"
    pkgs = packages_for_model_software("Y2", "Original Software")
    assert pkgs[0].package_name == "rom_y2.zip"
    assert len(packages_for_model("Y1")) >= 6


def test_rom_variant_parsing():
    def asset(name):
        return {"name": name, "browser_download_url": f"https://x/{name}", "size": 10}

    y1 = _parse_rom_asset_variant(asset("rom_360p.zip"), "v1.0", "rockbox-y1/rockbox")
    assert y1["resolution"] == "360p" and y1["type"] == "A"
    y2 = _parse_rom_asset_variant(asset("rom_y2.zip"), "v1.0", "rockbox-y1/rockbox")
    assert y2["model"] == "Y2"
    tb = _parse_rom_asset_variant(asset("rom_360p_type_b.zip"), "v1.0", "rockbox-y1/rockbox")
    assert tb["type"] == "B"
    variants = [
        _parse_rom_asset_variant(asset("rom_240p.zip"), "v1", "r/r"),
        _parse_rom_asset_variant(asset("rom_360p.zip"), "v1", "r/r"),
        _parse_rom_asset_variant(asset("rom.zip"), "v1", "r/r"),
    ]
    preferred = select_preferred_rom_asset([v for v in variants if v])
    assert preferred["resolution"] == "360p"
    # Model-aware: when Y2 is selected, rom_y2.zip must win over rom.zip.
    y2_variants = [
        {"asset": {"name": "rom.zip", "browser_download_url": "x/rom.zip", "size": 1},
         "type": "A", "resolution": "360p", "model": "dual"},
        {"asset": {"name": "rom_y2.zip", "browser_download_url": "x/rom_y2.zip", "size": 1},
         "type": "A", "resolution": "native", "model": "Y2"},
    ]
    pref_y2 = select_preferred_rom_asset(y2_variants, model="Y2")
    assert pref_y2["asset"]["name"] == "rom_y2.zip", (
        f"Y2 should pick rom_y2.zip, got {pref_y2['asset']['name']}"
    )
    pref_y1 = select_preferred_rom_asset(y2_variants, model="Y1")
    assert pref_y1["asset"]["name"] == "rom.zip", (
        f"Y1 should pick rom.zip (dual), got {pref_y1['asset']['name']}"
    )


def test_i18n_languages():
    """French and Spanish are fully translated and switchable."""
    from src import i18n
    from src.i18n import tr, translator

    assert set(i18n._SUPPORTED) >= {"zh-CN", "en", "fr", "es"}
    # Every key has a French and Spanish entry.
    missing = {
        lang: [k for k, table in i18n._STRINGS.items() if not table.get(lang)]
        for lang in ("fr", "es")
    }
    assert not missing["fr"], f"fr missing: {missing['fr']}"
    assert not missing["es"], f"es missing: {missing['es']}"
    # Language switching actually changes strings.
    translator().set_language("fr")
    assert tr("nav_log") == "Diagnostics"
    assert tr("sel_title") == "Choisir un logiciel"
    translator().set_language("es")
    assert tr("nav_log") == "Diagnóstico"
    assert tr("sel_title") == "Seleccionar software"
    translator().set_language("en")
    assert tr("nav_log") == "Diagnostics"


def test_donors():
    from src.donors import get_monthly_goal_stats, parse_donors_csv_text

    csv_text = (
        "Name,Amount,Date,URL,Method\n"
        "Alice,50,15/07/2026,https://x,PayPal\n"
        "Bob,-10,18/08/2026,,Ko-Fi\n"
        "Carol,25,18/08/2026,,PayPal\n"
    )
    donations = parse_donors_csv_text(csv_text)
    assert len(donations) == 3
    assert donations[1]["amount"] == -10
    from datetime import datetime

    now = datetime(2026, 8, 19)
    raised, remaining, pct, target = get_monthly_goal_stats(donations, now=now)
    assert target == 200.0
    assert raised == 25.0  # only Carol in August
    assert 0 < pct < 100
    assert parse_donors_csv_text("") == []


def test_relative_date():
    """Donation dates are shown as friendly relative timestamps (Today, Yesterday,
    On Monday, 3 days ago, etc.) in all supported languages."""
    from datetime import datetime, timedelta
    from src.donors import relative_date
    from src.i18n import translator

    now = datetime(2026, 8, 20, 12, 0, 0)
    today = relative_date(now, now)
    yesterday = relative_date(now - timedelta(days=1), now)
    three_days = relative_date(now - timedelta(days=3), now)
    week_ago = relative_date(now - timedelta(days=7), now)
    two_weeks = relative_date(now - timedelta(days=14), now)
    month_ago = relative_date(now - timedelta(days=35), now)
    five_months = relative_date(now - timedelta(days=150), now)
    year_ago = relative_date(now - timedelta(days=400), now)
    no_date = relative_date(None, now)

    translator().set_language("en")
    assert today == "Today"
    assert yesterday == "Yesterday"
    assert "on " in three_days.lower()
    assert three_days.lower() != today.lower()
    assert week_ago == "a week ago"
    assert two_weeks == "2 weeks ago"
    assert month_ago == "a month ago"
    assert five_months == "5 months ago"
    assert year_ago == "over a year ago"
    assert no_date == ""

    translator().set_language("fr")
    assert relative_date(now, now) == "Aujourd\u2019hui"
    assert relative_date(now - timedelta(days=1), now) == "Hier"

    translator().set_language("es")
    assert relative_date(now, now) == "Hoy"
    assert relative_date(now - timedelta(days=1), now) == "Ayer"

    translator().set_language("en")


def test_donation_dialog_translated():
    """The donation modal's intro (Ryan's message), donor ticker lines,
    goal text, and close button follow the selected language."""
    from PySide6.QtWidgets import QApplication
    from src.donation_dialog import DonationDialog
    from src.i18n import translator
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    donations = [
        {"name": "Alice", "amount": 10, "method": "Ko-Fi", "url": "", "date": "18/08/2026"}
    ]

    translator().set_language("fr")
    dlg = DonationDialog(parent=w, context="install_success", model="Y1",
                         software_name="Rockbox", donations=donations)
    assert "Nous avons installé" in dlg._intro_label.text(), dlg._intro_label.text()
    assert "Rockbox" in dlg._intro_label.text()
    assert "a donné $10 via Ko-Fi" in dlg._donor_lines()[0]
    assert "couvrir" in dlg._goal_label.text(), dlg._goal_label.text()
    assert dlg._close_btn.text() == "Fermer"
    dlg.close()

    translator().set_language("en")
    dlg2 = DonationDialog(parent=w, context="general", model="Y1",
                          software_name="Rockbox", donations=donations)
    assert "I'm Ryan" in dlg2._intro_label.text()
    assert "donated $10 by Ko-Fi" in dlg2._donor_lines()[0]
    assert dlg2._close_btn.text() == "Close"
    dlg2.close()

    _reset_app_settings()
    w.close()
    app.processEvents()


def test_scatter_parsing():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "preloader.bin").write_bytes(b"x")
        (d / "boot.img").write_bytes(b"y")
        (d / "system.img").write_bytes(b"z")
        scatter = d / "MT6572_Android_scatter.txt"
        scatter.write_text(
            "- general: MTK_PLATFORM_CFG\n"
            "  info:\n"
            "    - platform: MT6572\n"
            "      storage: EMMC\n"
            "- partition_index: SYS0\n"
            "  partition_name: preloader\n"
            "  file_name: preloader.bin\n"
            "  is_download: true\n"
            "- partition_index: SYS1\n"
            "  partition_name: boot\n"
            "  file_name: boot.img\n"
            "  is_download: true\n"
            "- partition_index: SYS2\n"
            "  partition_name: system\n"
            "  file_name: system.img\n"
            "  is_download: false\n"
            "- partition_index: SYS3\n"
            "  partition_name: missing\n"
            "  file_name: nope.img\n"
            "  is_download: true\n",
            encoding="utf-8",
        )
        assert _find_scatter(d) == scatter
        assert _parse_scatter_platform(scatter) == "MT6572"
        pairs = _list_expected_images(scatter)
        names = [n for n, _ in pairs]
        assert names == ["preloader", "boot", "missing"]
        assert [n for n, p in pairs if p.exists()] == ["preloader", "boot"]
        parsed = _parse_scatter(scatter)
        assert [n for n, _ in parsed] == ["preloader", "boot"]
        assert _missing_images(scatter) == ["missing"]
        # -verified fallback
        (d / "boot-verified.img").write_bytes(b"vv")
        _missing_images(scatter)  # no crash


def test_manifest_parse_and_merge():
    """Live slidia_manifest.xml parsing, cache, and catalog merge."""
    from src import catalog, manifest

    xml = """<slidia>
      <package name="Original Software" repo="y1-community/y1-stock-rom" device="Y1" type="img" handler="Custom Firmware" />
      <package name="Rockbox" repo="rockbox-y1/rockbox" device="Y2" type="img" handler="Custom Firmware" />
      <package name="Please Update App To Continue" repo="Please-Click/Updates-Available" device="Y1" type="img" handler="Custom Firmware" />
      <package name="An App" repo="x/y" device="Y1" type="apk" handler="App" />
    </slidia>"""
    entries = manifest.parse_manifest_xml(xml)
    assert len(entries) == 2, entries
    by = {(e.name, e.model): e for e in entries}
    assert by[("Original Software", "Y1")].package_name == "rom.zip"
    assert by[("Rockbox", "Y2")].package_name == "rom_y2.zip"
    assert by[("Rockbox", "Y2")].repo == "rockbox-y1/rockbox"

    catalog.set_live_catalog(entries)
    assert "Original Software" in catalog.software_names_for_model("Y1")
    catalog.set_live_catalog([])  # back to the static fallback
    assert "Inniclassic" in catalog.software_names_for_model("Y1")

    # Cache round-trip.
    with tempfile.TemporaryDirectory() as td:
        import json

        cache_dir = Path(td) / "catalog"
        cache_dir.mkdir()
        cache_dir.joinpath("packages.json").write_text(
            json.dumps([{"name": "Rockbox", "repo": "rockbox-y1/rockbox",
                         "device": "Y1", "package_name": "rom.zip", "slug": "rockbox-y1"}]),
            encoding="utf-8",
        )
        manifest._cache_root = lambda: cache_dir  # type: ignore[assignment]
        cached = manifest.load_cached_manifest()
        assert cached and cached[0].name == "Rockbox"


def test_update_version_and_assets():
    """Version comparison + per-OS asset picking."""
    from src import updates

    assert updates.parse_version("2.0.4") == (2, 0, 4)
    assert updates.parse_version("v1.2") == (1, 2, 0)
    assert updates.parse_version("2.0.4-beta1") == (2, 0, 4)
    assert updates.parse_version("release-notes") is None
    assert updates.is_newer("2.0.4", "1.1.2")
    assert not updates.is_newer("1.0.0", "1.1.2")
    assert not updates.is_newer("v1.1.2", "1.1.2")

    assets = [
        {"name": "installer.exe", "browser_download_url": "u1", "size": 10},
        {"name": "Innioasis.Updater.for.Mac.dmg", "browser_download_url": "u2", "size": 20},
        {"name": "run_linux.sh", "browser_download_url": "u3", "size": 5},
    ]
    assert updates.pick_platform_asset(assets, "win32")["name"] == "installer.exe"
    assert updates.pick_platform_asset(assets, "darwin")["name"] == "Innioasis.Updater.for.Mac.dmg"
    assert updates.pick_platform_asset(assets, "linux")["name"] == "run_linux.sh"
    assert updates.pick_platform_asset(assets, "win32")["name"] != "Innioasis.Updater.for.Mac.dmg"
    # Arch preference within the same extension.
    arch_assets = [
        {"name": "InnioasisUpdater-x86_64.AppImage", "browser_download_url": "a", "size": 1},
        {"name": "InnioasisUpdater-arm64.AppImage", "browser_download_url": "b", "size": 1},
    ]
    assert updates.pick_platform_asset(arch_assets, "linux")["name"] == "InnioasisUpdater-x86_64.AppImage"


def test_update_checker():
    """UpdateChecker honors newer/older releases and failure states."""
    from src import updates

    class Fake:
        def __init__(self, data):
            self.data = data

        def get_latest_release_info(self, repo):
            return self.data

    info = updates.UpdateChecker("a/b", client=Fake({"tag_name": "2.0.4", "assets": []}), current="1.1.2").check()
    assert info.tag == "2.0.4" and not info.failed
    assert updates.UpdateChecker("a/b", client=Fake({"tag_name": "1.0.0"}), current="1.1.2").check().tag == ""
    assert updates.UpdateChecker("a/b", client=Fake({}), current="1.1.2").check().tag == ""

    class Boom:
        def get_latest_release_info(self, repo):
            raise RuntimeError("offline")

    failed = updates.UpdateChecker("a/b", client=Boom(), current="1.1.2").check()
    assert failed.failed


def test_update_dialog_and_wiring():
    """Update dialog renders (translated) and main-window wiring shows it
    once per session, honoring the skipped-version setting."""
    from PySide6.QtWidgets import QApplication
    from src.i18n import translator
    from src.ui.dialogs import UpdateAvailableDialog
    from src.updates import UpdateInfo
    import src.ui.main_window as mw

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    assert w._check_updates_btn.text() == "Check for Updates"

    translator().set_language("fr")
    info = UpdateInfo(
        tag="2.0.4", name="n", body="notes", html_url="https://github.com/x/y/releases",
        assets=[{"name": "installer.exe", "browser_download_url": "https://x/installer.exe", "size": 10}],
    )
    dlg = UpdateAvailableDialog(w, info=info, current_version="1.1.2")
    assert dlg._download_btn.text() == "Télécharger la mise à jour"
    assert "2.0.4" in dlg._notes.toPlainText() or "notes" in dlg._notes.toPlainText()
    dlg.close()

    shown = []
    real_dlg = mw.UpdateAvailableDialog

    class _StubUpdateDialog:
        def __init__(self, parent=None, info=None, current_version="", on_skip=None):
            shown.append(info)

        def exec(self):
            return 1

    mw.UpdateAvailableDialog = _StubUpdateDialog
    real_mb = mw.QMessageBox
    mw.QMessageBox.information = staticmethod(lambda *a, **k: None)  # type: ignore[assignment]
    try:
        translator().set_language("en")
        w._on_update_check_done(info, manual=False)
        assert shown and shown[0].tag == "2.0.4", "auto-check should offer the update"

        shown.clear()
        w._on_update_check_done(info, manual=False)
        assert not shown, "only offered once per session"

        w.settings.setValue("update_skipped_version", "2.0.4")
        w2 = mw.MainWindow()  # fresh session with the skip remembered
        w2._on_update_check_done(info, manual=False)
        assert not shown, "skipped version is not re-offered automatically"
        w2.close()

        # Manual check with no update → info box (no dialog).
        shown.clear()
        w._on_update_check_done(UpdateInfo(), manual=True)
        assert not shown
    finally:
        mw.UpdateAvailableDialog = real_dlg
        mw.QMessageBox = real_mb
        _reset_app_settings()
        w.close()
        app.processEvents()


def test_flash_service_import():
    assert catalog is not None
    assert _find_scatter is not None
    assert _parse_scatter is not None
    import src.flash_service as fs

    assert fs.IS_WINDOWS is not None
    assert fs.compute_extract_dir("C:/fw/My.zip") == Path("C:/fw/.My_extracted")


def test_backend_method_dispatch():
    """FlashWorker honors the chosen backend method (sp / mtk / auto)."""
    import src.flash_service as fs
    import src.linux_sp_flash as lsf

    real_ensure = lsf.ensure_linux_sp_flash_tool
    lsf.ensure_linux_sp_flash_tool = lambda progress_cb=None, force_download=False: (True, "ok")
    try:
        def make(method):
            w = fs.FlashWorker("pkg.zip", method=method)
            w._log = lambda *a, **k: None
            w._flash_via_sp_flash_tool = lambda s: calls.append("sp")
            w._flash_via_mtkclient = lambda e, s: calls.append("mtk")
            return w

        calls = []
        make("mtk")._dispatch_backend(Path("x"), Path("s"))
        assert calls == ["mtk"], "explicit mtk always uses MTKClient"

        calls = []
        make("sp")._dispatch_backend(Path("x"), Path("s"))
        if fs.IS_WINDOWS:
            assert calls == ["sp"], "explicit sp uses SP Flash Tool on Windows"
        elif fs.IS_MAC:
            assert calls == ["mtk"], "sp is impossible on macOS -> MTKClient"
        else:
            assert calls == ["sp"], "explicit sp stages + uses SP Flash Tool on Linux"

        calls = []
        make("auto")._dispatch_backend(Path("x"), Path("s"))
        if fs.IS_MAC:
            assert calls == ["mtk"], "auto on macOS -> MTKClient"
        else:
            assert calls == ["sp"], "auto prefers SP Flash Tool on Windows/Linux"
    finally:
        lsf.ensure_linux_sp_flash_tool = real_ensure


def test_releases_client_cached():
    with tempfile.TemporaryDirectory() as td:
        client = ReleasesClient(cache_root=td)
        fake = [{"tag_name": "v1", "download_url": "u", "rom_variants": []}]
        client.cache_releases("a/b", fake)
        assert client.get_cached_releases("a/b") == fake


def test_releases_client_network():
    """Best-effort live GitHub check (cache-first; may be skipped offline)."""
    client = ReleasesClient()
    cached = client.get_cached_releases("rockbox-y1/rockbox")
    if cached:
        print("  (used cached releases)")
        return
    releases = client.get_all_releases("rockbox-y1/rockbox")
    assert isinstance(releases, list)
    print(f"  (fetched {len(releases)} releases)")


def test_ui_construction():
    from PySide6.QtWidgets import QApplication

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    from src.ui.main_window import MainWindow
    from src.donation_dialog import DonationDialog
    from src.ui.dialogs import DiagnosticsDialog
    from src.donors import load_donors_file, parse_donors_csv_text
    from src.ui.select_page import SelectPackagePage

    w = MainWindow()
    w.show()
    donations = parse_donors_csv_text(
        load_donors_file([str(ROOT / "assets" / "donors.csv")]) or ""
    )
    assert donations, "assets/donors.csv should parse"
    dlg = DonationDialog(parent=w, context="install_success", model="Y1",
                         software_name="Rockbox", donations=donations)
    assert dlg is not None
    log = DiagnosticsDialog(parent=w, lines=["hello", "world"])
    assert log is not None
    # Exercise the select page offline path (no network dependency).
    page = SelectPackagePage()
    assert page.current_model() == "Y1"
    assert page.current_package() is not None
    # Select Package is the start page (no separate Home tab).
    assert w._stack.currentIndex() == 0
    assert w.statusBar().objectName() == "donation_status_bar"
    assert w.statusBar()._goal_bar.value() >= 0
    w.close()
    app.processEvents()


def test_donation_status_bar():
    """The main window's bottom status area is the compact Support goal
    display, and it retranslates in place with the rest of the window."""
    from PySide6.QtWidgets import QApplication
    from src.donation_dialog import DonationStatusBar
    from src.i18n import translator

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    donations = [{
        "name": "Alice", "amount": 25, "method": "Ko-Fi", "url": "",
        "dt": __import__("datetime").datetime.now(),
    }]
    opened = []
    refreshed = []
    bar = DonationStatusBar(
        donations=donations,
        on_support=lambda: opened.append(True),
        on_donations_updated=lambda value: refreshed.append(value),
    )
    assert bar.objectName() == "donation_status_bar"
    assert bar._remote_refresh_timer.isActive()
    assert bar._remote_refresh_timer.interval() == 5 * 60 * 1000
    assert bar._goal_bar.value() == 125
    assert "$25" in bar._goal_label.text()
    bar._support_btn.click()
    assert opened == [True]

    live = [{
        "name": "Bob", "amount": 40, "method": "PayPal", "url": "",
        "dt": __import__("datetime").datetime.now(),
    }]
    bar._apply_fresh_donations(live)
    assert bar.donations is live
    assert refreshed == [live]
    assert "$40" in bar._goal_label.text()

    translator().set_language("fr")
    bar.retranslate()
    assert "couvrir" in bar._goal_label.text()
    assert bar._support_btn.text() == "Nous soutenir"

    bar.deleteLater()
    app.processEvents()
    _reset_app_settings()


def test_goal_reached_hides_goal_line():
    """Once the monthly $200 goal is met, the ticker shows donor shout-outs
    only — no goal line — in both the footer bar and the Support modal."""
    import datetime

    from PySide6.QtWidgets import QApplication
    from src.donation_dialog import DonationDialog, DonationStatusBar
    from src.i18n import translator
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    now = datetime.datetime.now()
    full = [
        {"name": "Alice", "amount": 120, "method": "PayPal", "url": "", "dt": now},
        {"name": "Bob", "amount": 80, "method": "Ko-Fi", "url": "", "dt": now},
    ]

    # Stub the live donor fetch so the real innioasis.app data cannot land
    # mid-test and flip these synthetic fixtures back below the goal.
    import src.donation_dialog as dd
    orig_fetch = dd.fetch_remote_donors_async
    dd.fetch_remote_donors_async = lambda callback: None
    try:
        bar = DonationStatusBar(donations=full)
        assert getattr(bar, "_goal_reached", False) is True
        assert bar._showing_goal is False
        assert bar._goal_label.isHidden()
        assert not bar._donor_label.isHidden()
        # Rotation keeps cycling donors instead of flipping back to the goal.
        bar._rotate()
        assert bar._showing_goal is False
        assert bar._goal_label.isHidden()
        assert not bar._donor_label.isHidden()
        bar.deleteLater()

        w = MainWindow()
        dlg = DonationDialog(parent=w, context="general", model="Y1",
                             software_name="Test", donations=full)
        assert getattr(dlg, "_goal_reached", False) is True
        assert dlg._showing_goal is False
        assert dlg._goal_view.isHidden()
        assert not dlg._donor_view.isHidden()
        dlg._next_ticker_step()
        dlg._next_ticker_step()
        dlg._next_ticker_step()
        dlg._next_ticker_step()
        # After a full cycle it must still be on donors, never on the goal.
        assert dlg._showing_goal is False
        assert not dlg._donor_view.isHidden()
        dlg.close()
        w.close()
        app.processEvents()
    finally:
        dd.fetch_remote_donors_async = orig_fetch
    _reset_app_settings()


def test_diagnostics_live_update():
    """The diagnostics dialog streams new log lines while open (no need to
    close and reopen it to see progress)."""
    from PySide6.QtWidgets import QApplication
    from src.ui.dialogs import DiagnosticsDialog
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()

    dlg = DiagnosticsDialog(parent=w, lines=[])
    assert dlg._view.toPlainText() == "No diagnostics entries"

    # Wire it the same way _show_diagnostics does.
    w.log_line_added.connect(dlg.append_line)
    w._append_log("first live line")
    w._append_log("second live line")
    text = dlg._view.toPlainText()
    assert "No diagnostics entries" not in text
    assert "first live line" in text and "second live line" in text, text

    w.log_line_added.disconnect(dlg.append_line)
    dlg.close()
    w.close()
    app.processEvents()


def test_flash_flow_launch():
    """The backend launches immediately on package select (search-usb
    paradigm); steps drive the waiting / flashing views and state machine."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    from src.state import FlashState
    from src.flash_service import STEP_WAITING, STEP_WRITE, STEP_DETECT

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    calls = []
    w.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append((pkg, method))
    w.settings.setValue("flash_method", "auto")
    w._flash_method = "auto"

    w._on_package_selected("C:/fake/rom.zip", "Rockbox (Y1)", "Y1")
    assert calls == [("C:/fake/rom.zip", "auto")], "flash must start immediately after selection"
    assert w.sm.state is FlashState.S2_WAIT_CONNECTION, w.sm.state

    # Backend reports it is searching USB -> guidance view with model prompt.
    w.service.step_changed.emit(STEP_WAITING)
    assert w.sm.state is FlashState.S2_WAIT_CONNECTION
    banner = w._flash_page._wait_banner.text()
    assert "connect your Y1" in banner, banner

    # Device detected -> write step -> flashing view + S4.
    w.service.step_changed.emit(STEP_DETECT)
    w.service.step_changed.emit(STEP_WRITE)
    assert w.sm.state is FlashState.S4_FLASHING, w.sm.state
    assert w._flash_page._stack.currentWidget() is w._flash_page._flashing_view
    # The method selector is disabled once flashing starts.
    assert not w._flash_page._method_combo.isEnabled()
    # In-progress copy: banner and status tag both read "Install in Progress".
    assert w._flash_page._flash_banner.text() == "Install in Progress"
    assert w._flash_page._wait_status.text() == "Install in Progress"
    w.close()
    app.processEvents()


def test_flash_method_switch():
    """Switching the backend while waiting restarts with the new method;
    the selector is populated from the persisted choice."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    from src.state import FlashState

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    calls = []
    w.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append((pkg, method))
    w.settings.setValue("flash_method", "auto")
    w._flash_method = "auto"

    w._on_package_selected("C:/fake/rom.zip", "Rockbox (Y1)", "Y1")
    assert w._flash_page.current_method() == "auto"

    # User switches to MTKClient while the backend searches.
    w._flash_page._method_combo.setCurrentIndex(
        w._flash_page._method_combo.findData("mtk")
    )
    assert w._flash_page.current_method() == "mtk"
    assert calls[-1] == ("C:/fake/rom.zip", "mtk"), "backend restarted with new method"
    assert w.settings.value("flash_method") == "mtk"
    w.settings.setValue("flash_method", "auto")  # leave machine state clean
    w.close()
    app.processEvents()


def test_language_switch_keeps_screen():
    """Changing language mid-activity retranslates in place: the user stays
    on the current page/view and progress state is preserved (regression:
    the old code rebuilt the UI and navigated back to Select)."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    from src.flash_service import STEP_WAITING, STEP_WRITE

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    calls = []
    w.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(pkg)
    w._on_package_selected("C:/fake/rom.zip", "Rockbox (Y1)", "Y1")
    w.service.step_changed.emit(STEP_WAITING)
    app.processEvents()
    assert w._stack.currentIndex() == 1, "should be on the flash page"
    assert w._flash_page._stack.currentWidget() is w._flash_page._waiting_view

    # Switch to French while waiting: same page, same view, retranslated text.
    combo = w._lang_combo
    combo.setCurrentIndex(combo.findData("fr"))
    app.processEvents()
    assert w._stack.currentIndex() == 1
    assert w._flash_page._stack.currentWidget() is w._flash_page._waiting_view
    assert "connecter votre Y1" in w._flash_page._wait_banner.text()

    # Advance to the flashing view with real progress, then switch to Spanish.
    w.service.step_changed.emit(STEP_WRITE)
    w.service.progress.emit(57)
    app.processEvents()
    assert w._flash_page._stack.currentWidget() is w._flash_page._flashing_view
    combo.setCurrentIndex(combo.findData("es"))
    app.processEvents()
    assert w._flash_page._stack.currentWidget() is w._flash_page._flashing_view, \
        "flashing view must survive a language switch"
    assert w._flash_page._progress_bar.value() == 57, "progress must survive"
    assert w._flash_page._step_label.text() == "Escribiendo imagen"
    assert w._flash_page._method_combo.itemText(0) == "Auto (recomendado)"

    _reset_app_settings()
    w.close()
    app.processEvents()


def test_success_dialog_flow():
    """After a successful install the donation modal is shown directly (no
    separate completion screen); the completion dialog only appears when the
    user has opted out of the donation modal."""
    from PySide6.QtWidgets import QApplication
    import src.ui.main_window as mw

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    shown = []
    w._show_donation_dialog = lambda context="general": shown.append(("donation", context))
    real_complete = mw.FlashCompleteDialog

    class _StubDialog:
        def __init__(self, *a, **k):
            name = a[1] if len(a) > 1 else k.get("package_name", "")
            shown.append(("complete", name))

        def exec(self):
            return 1

    mw.FlashCompleteDialog = _StubDialog
    try:
        # Donation prompt enabled (default): donation modal only.
        w.settings.setValue("donation_install_prompt_disabled", False)
        w._package_name = "Rockbox (Y1)"
        w._handle_flash_success()
        assert shown == [("donation", "install_success")], shown

        # Opted out: completion dialog only, no donation modal.
        shown.clear()
        w.settings.setValue("donation_install_prompt_disabled", True)
        w._package_name = "Rockbox (Y1)"  # _reset_after_run cleared it above
        w._handle_flash_success()
        assert shown == [("complete", "Rockbox (Y1)")], shown
    finally:
        mw.FlashCompleteDialog = real_complete
        w.close()
        app.processEvents()


def test_offline_banner_and_generic_model():
    """Select page advertises offline installs; unknown-model (local)
    packages get a generic 'your device' connect prompt."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    from src.flash_service import STEP_WAITING

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    banner = w._select_page._offline_banner.text()
    assert "Innioasis" in banner and "Timmkoo" in banner, banner
    # The offline-install note belongs on the Local file tab, not the Online tab.
    assert w._select_page._offline_banner.parent() is w._select_page._local_tab

    calls = []
    w.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(pkg)
    w._on_package_selected("C:/fake/rom.rar", "rom.rar", "")  # local package: no model
    w.service.step_changed.emit(STEP_WAITING)
    banner = w._flash_page._wait_banner.text()
    assert "connect your device" in banner, banner
    w.close()
    app.processEvents()


def test_release_list_and_notes():
    """Online release list shows names only (no tag), and the changelog
    pane renders Markdown with the tag retained in the detail view."""
    from PySide6.QtWidgets import QApplication
    from src.ui.select_page import SelectPackagePage

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()

    releases = [
        {
            "tag_name": "v1.0.0",
            "name": "Solar (adds YouTube)",
            "published_at": "2024-05-01T12:00:00Z",
            "prerelease": False,
            "body": "## Highlights\n- **Faster** boot\n- fix crash",
            "rom_variants": [{"asset": {"name": "rom.zip"}}],
        },
        {
            "tag_name": "v0.9.0",
            "name": "",
            "published_at": "2024-04-01T12:00:00Z",
            "prerelease": True,
            "body": "",
            "rom_variants": [],
        },
    ]
    page._on_releases_loaded(releases, "")
    labels = [page._release_list.item(i).text() for i in range(page._release_list.count())]
    assert labels[0] == "Solar (adds YouTube)", labels
    assert "v1.0.0" not in labels[0]
    # Empty name falls back to the tag (with the preview marker kept).
    assert labels[1] == "v0.9.0  [preview]", labels

    md = page._render_release_notes(releases[0])
    assert md.startswith("## Solar (adds YouTube)"), md
    assert "**" in md and "---" in md
    assert "<pre" not in md
    assert "v1.0.0" in md  # tag stays visible in the changelog detail

    # Empty body falls back to the translated no-notes message.
    md2 = page._render_release_notes(releases[1])
    assert "No release notes" in md2
    app.processEvents()


def test_release_model_filtering():
    """Y2 must not see releases that only carry a plain rom.zip (Y1-only)
    from a repo without a Y2 marker, while still listing shared/dual repos.
    Mirrors firmware_downloader.py's filter_rom_variants_for_model."""
    from src.catalog import _parse_rom_asset_variant, _release_matches_model
    from src.catalog import FirmwarePackage

    def asset(name):
        return {"name": name, "browser_download_url": f"https://x/{name}", "size": 10}

    def release(names, repo):
        return {
            "rom_variants": [
                v for v in (
                    _parse_rom_asset_variant(asset(n), "v1.0", repo or "y1-community/x")
                    for n in names
                ) if v
            ],
            "source_repo": repo or "y1-community/x",
        }

    y1 = FirmwarePackage("original-y1", "Original Software", "Y1",
                         "y1-community/y1-stock-rom", "rom.zip")
    y2 = FirmwarePackage("original-y2", "Original Software", "Y2",
                         "y1-community/y1-stock-rom", "rom_y2.zip")
    rb_y2 = FirmwarePackage("rockbox-y2", "Rockbox", "Y2",
                            "y1-community/rockbox-y2-rom", "rom_y2.zip")

    # The reported bug: a release with only rom.zip must be hidden for Y2.
    only_rom = release(["rom.zip"], "y1-community/y1-stock-rom")
    assert not _release_matches_model("Y2", only_rom, y2)
    # …but it is a valid Y1 release.
    assert _release_matches_model("Y1", only_rom, y1)

    # A release carrying rom_y2.zip shows for Y2 (even alongside a plain rom.zip).
    both = release(["rom_y2.zip", "rom.zip"], "y1-community/y1-stock-rom")
    assert _release_matches_model("Y2", both, y2)

    # A rom_y2-only release never shows for Y1.
    only_y2 = release(["rom_y2.zip"], "y1-community/y1-stock-rom")
    assert not _release_matches_model("Y1", only_y2, y1)
    assert _release_matches_model("Y2", only_y2, y2)

    # A shared dual rom on a Y2-named repo still shows for both models.
    shared = release(["rom_360p.zip"], "y1-community/rockbox-y2-rom")
    assert _release_matches_model("Y2", shared, rb_y2)





def test_mtk_api_init():
    """MTKClient init builds an Mtk handle (regression: the ported mtk_api
    passed ``logLevel=`` — MtkConfig expects ``loglevel=`` — so every MTKClient
    install died with a TypeError before the backend even started)."""
    from src import mtk_api

    mtk = mtk_api.init(None, None)
    assert mtk is not None
    assert type(mtk).__name__ == "Mtk"


def test_cancel_kills_sp_process():
    """Cancelling a running SP Flash Tool worker terminates the backend so a
    switch to MTKClient really frees the port/device."""
    import src.flash_service as fs

    w = fs.FlashWorker("pkg.zip", method="sp")
    killed = []

    class FakeProc:
        pid = 4242

        def terminate(self):
            killed.append(True)

        def wait(self, timeout=None):
            return 0

    w._process = FakeProc()
    w.cancel()
    assert w._cancelled
    assert killed, "SP Flash Tool subprocess must be terminated on cancel"


def test_worker_switch_guard():
    """A late ``finished`` from a previously cancelled worker must not orphan
    the newer worker reference (regression: ``_on_flash_done`` cleared the slot
    unconditionally, so switching SP -> MTKClient left the new worker undetachable)."""
    import src.flash_service as fs

    service = fs.FlashService()
    old = fs.FlashWorker("pkg.zip", method="sp")
    new = fs.FlashWorker("pkg.zip", method="mtk")
    service._flash_worker = new

    # Old run finishes (USER_CANCELLED) after the new run started.
    service._on_flash_done(old, False, "USER_CANCELLED")
    assert service._flash_worker is new, "stale worker must not clear the new one"

    service._on_flash_done(new, True, "")
    assert service._flash_worker is None


def test_guided_image_selection():
    """Flash page picks firmware_downloader.py-style guidance images: presteps
    while preparing, method/platform initsteps variant while waiting, and the
    bundled asset files exist."""
    from PySide6.QtWidgets import QApplication
    from src.ui.flash_page import FlashPage
    import src.paths as paths

    app = QApplication.instance() or QApplication(sys.argv)
    page = FlashPage()
    real_win, real_mac = paths.IS_WINDOWS, paths.IS_MAC
    try:
        paths.IS_WINDOWS, paths.IS_MAC = True, False
        page.set_method("sp")
        assert page._initsteps_image() == "initsteps_sp.png"
        page.set_method("mtk")
        assert page._initsteps_image() == "initsteps_win.png"
        page.set_method("auto")
        assert page._initsteps_image() == "initsteps_sp.png", "auto on Windows -> SP"

        paths.IS_WINDOWS, paths.IS_MAC = False, True
        page.set_method("auto")
        assert page._initsteps_image() == "initsteps.png", "auto on macOS -> MTKClient"
        page.set_method("mtk")
        assert page._initsteps_image() == "initsteps.png"
        page.set_method("sp")
        assert page._initsteps_image() == "initsteps_sp.png", "explicit sp keeps SP guide"
    finally:
        paths.IS_WINDOWS, paths.IS_MAC = real_win, real_mac
        page.deleteLater()

    for f in ("presteps.png", "initsteps.png", "initsteps_win.png",
              "initsteps_sp.png", "please_wait.png", "reconnect.png",
              "installing.png", "installed.png"):
        assert (ROOT / "assets" / f).is_file(), f"missing bundled asset {f}"




def test_mtk_system_exit_guarded():
    """In-process mtkclient calls sys.exit() on DA failure paths; SystemExit is
    a BaseException, so the worker must convert it to a clean INTERNAL_ERROR
    instead of letting it kill the app (regression: run() only caught
    Exception, so a mtkclient sys.exit escaped and crashed the process)."""
    import src.flash_service as fs

    w = fs.FlashWorker("pkg.zip", method="mtk")
    results = []
    w.finished.connect(lambda ok, err: results.append((ok, err)))

    def boom():
        raise SystemExit(1)

    w._do_flash = boom
    w.run()
    assert results == [(False, "INTERNAL_ERROR")], results


def test_mtk_connect_system_exit_guarded():
    """A mtkclient SystemExit during device connect becomes CONNECTION_FAILED."""
    import src.flash_service as fs
    import src.mtk_api as mtk_api

    w = fs.FlashWorker("pkg.zip", method="mtk")
    results = []
    w.finished.connect(lambda ok, err: results.append((ok, err)))
    w._log = lambda *a, **k: None

    real_scatter = fs._parse_scatter
    real_connect = mtk_api.connect
    fs._parse_scatter = lambda scatter: [("system", Path("/tmp/system.img"))]

    def boom(mtk, directory):
        raise SystemExit(1)

    mtk_api.connect = boom
    try:
        w._flash_via_mtkclient("/tmp", "/tmp/scatter.txt")
    finally:
        mtk_api.connect = real_connect
        fs._parse_scatter = real_scatter
    assert results == [(False, "CONNECTION_FAILED")], results


def test_mtk_write_system_exit_guarded():
    """A mtkclient SystemExit mid-write becomes WRITE_FAILED, not a crash."""
    import src.flash_service as fs
    import src.mtk_api as mtk_api

    w = fs.FlashWorker("pkg.zip", method="mtk")
    results = []
    w.finished.connect(lambda ok, err: results.append((ok, err)))
    w._log = lambda *a, **k: None

    real_scatter = fs._parse_scatter
    real_connect = mtk_api.connect
    fs._parse_scatter = lambda scatter: [("system", Path("/tmp/system.img"))]

    def fake_connect(mtk, directory):
        class FakeHandler:
            def handle_da_cmds(self, *a, **k):
                raise SystemExit(1)

        return (object(), FakeHandler())

    mtk_api.connect = fake_connect
    try:
        w._flash_via_mtkclient("/tmp", "/tmp/scatter.txt")
    finally:
        mtk_api.connect = real_connect
        fs._parse_scatter = real_scatter
    assert results == [(False, "WRITE_FAILED")], results


def test_linux_sp_flash_validation():
    """Linux staging required-files checks work against a synthetic zip."""
    import zipfile
    from src import linux_sp_flash as lsf

    with tempfile.TemporaryDirectory() as td:
        stage = Path(td) / "stage"
        stage.mkdir()
        # A stage with none of the required files is reported missing.
        missing = lsf.missing_files(stage)
        assert len(missing) == len(lsf.FLASH_TOOL_LINUX_REQUIRED_FILES)
        # Build a fake zip containing every required member (empty files).
        zp = Path(td) / "flash_tool_linux.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for rel in lsf.FLASH_TOOL_LINUX_REQUIRED_FILES:
                zf.writestr(rel, b"x")
        # Size gate rejects the small fake zip.
        assert not lsf.zip_has_required_members(zp)
        # A zip with the required members clears the size gate (>10 MB) and
        # passes member-wise validation.
        with zipfile.ZipFile(zp, "w") as zf:
            for rel in lsf.FLASH_TOOL_LINUX_REQUIRED_FILES:
                zf.writestr(rel, b"x" * (700 * 1024))
        assert lsf.zip_has_required_members(zp)


def test_linux_sp_flash_distro_detection():
    """Verify Linux distribution detection and multi-distro family mapping."""
    from unittest.mock import patch
    from src import linux_sp_flash as lsf

    host_info = lsf.detect_linux_distro()
    assert host_info["id"] != "", "Host distro ID should not be empty"
    assert host_info["family"] in (
        "arch", "debian", "fedora", "suse", "gentoo", "void", "alpine", "nixos", "generic"
    )

    distros = [
        ("ubuntu", ["debian"], "Ubuntu 24.04", "debian", "dialout", "apt"),
        ("debian", [], "Debian GNU/Linux 12", "debian", "dialout", "apt"),
        ("fedora", [], "Fedora Linux 41", "fedora", "dialout", "dnf"),
        ("arch", [], "Arch Linux", "arch", "uucp", "pacman"),
        ("cachyos", ["arch"], "CachyOS", "arch", "uucp", "pacman"),
        ("omarchy", ["arch"], "Omarchy Linux", "arch", "uucp", "pacman"),
        ("opensuse-tumbleweed", ["suse"], "openSUSE Tumbleweed", "suse", "dialout", "zypper"),
        ("gentoo", [], "Gentoo Linux", "gentoo", "uucp", "emerge"),
        ("alpine", [], "Alpine Linux", "alpine", "dialout", "apk"),
        ("void", [], "Void Linux", "void", "dialout", "xbps"),
        ("nixos", [], "NixOS", "nixos", "dialout", "nix"),
    ]

    for did, id_like, name, expected_family, expected_grp, expected_pm in distros:
        fake_content = f'ID={did}\nID_LIKE="{" ".join(id_like)}"\nNAME="{name}"\nPRETTY_NAME="{name}"\n'
        with patch.object(Path, "is_file", return_value=True):
            with patch.object(Path, "read_text", return_value=fake_content):
                info = lsf.detect_linux_distro()
                assert info["family"] == expected_family, f"Expected {expected_family} for {did}, got {info['family']}"
                assert info["serial_group"] == expected_grp, f"Expected {expected_grp} for {did}, got {info['serial_group']}"
                assert info["package_manager"] == expected_pm, f"Expected {expected_pm} for {did}, got {info['package_manager']}"


def test_linux_sp_flash_rules_and_readiness():
    """Verify udev rule generation, setup script writing, and readiness report."""
    from src import linux_sp_flash as lsf

    rules = lsf.generate_udev_rule_content()
    assert "0e8d" in rules
    assert "0003" in rules
    assert "0666" in rules
    assert "uaccess" in rules
    assert "ID_MM_DEVICE_IGNORE" in rules
    assert "BRLTTY_DEVICE_IGNORE" in rules

    with tempfile.TemporaryDirectory() as td:
        script = lsf.write_setup_script(cache_dir=Path(td))
        assert script.is_file()
        assert os.access(script, os.X_OK)
        content = script.read_text(encoding="utf-8")
        assert "UDEV_RULE_FILENAME" not in content
        assert "99-innioasis-mediatek.rules" in content

    readiness = lsf.verify_linux_flashing_readiness()
    assert "overall_ready" in readiness
    assert "sp_exec_ok" in readiness
    assert readiness["arch_ok"] is True
    assert readiness["libpng12_staged"] is True
    assert readiness["sp_exec_ok"] is True


def test_linux_setup_dialog():
    """Verify LinuxSetupDialog instantiates and populates status without crashing."""
    from PySide6.QtWidgets import QApplication
    from src.ui.dialogs import LinuxSetupDialog

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    dlg = LinuxSetupDialog()
    assert dlg.windowTitle() != ""
    assert "SP Flash Tool Verification Report" in dlg._status_view.toPlainText()


def test_package_prep_gates_flash_start():
    """Extraction is package preparation: download/local-file flows extract
    BEFORE the flash flow starts (package_selected only fires after prep), and
    the flash worker reuses the extraction instead of emitting EXTRACTING."""
    import zipfile
    from PySide6.QtWidgets import QApplication
    from src.ui.select_page import SelectPackagePage
    from src.flash_service import FlashWorker, STEP_EXTRACTING, STEP_WAITING

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()

    tmp = Path(tempfile.mkdtemp(prefix="neo_prep_"))
    zpath = tmp / "rom.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("MT6572_Android_scatter.txt", "platform: MT6572")

    emitted = []
    page.package_selected.connect(lambda *a: emitted.append(a))

    # Online flow: after download, prep runs; ready must wait for it.
    page._on_download_done(True, str(zpath))
    assert not emitted, "package_selected must not fire before preparation finishes"
    worker = page._prep_worker
    assert worker is not None, "prep worker should be running"
    worker.wait(5000)
    for _ in range(50):
        app.processEvents()
        if emitted:
            break
    assert emitted, "package_selected should fire once preparation completes"

    # Flash worker with the pre-extracted dir never shows EXTRACTING.
    steps = []
    fw = FlashWorker(str(zpath), pre_extracted_dir=str(zpath.parent / ".rom_extracted"))
    fw._dispatch_backend = lambda *a: None
    fw.step_changed.connect(steps.append)
    fw.run()
    assert STEP_EXTRACTING not in steps, steps
    assert STEP_WAITING in steps, steps

    # Without a prepared dir the extraction fallback still exists (safety net).
    steps2 = []
    fw2 = FlashWorker(str(zpath), pre_extracted_dir="")
    fw2._dispatch_backend = lambda *a: None
    fw2.step_changed.connect(steps2.append)
    fw2.run()
    assert STEP_EXTRACTING in steps2, steps2

    # Local flow: start button stays disabled until prep finishes.
    page2 = SelectPackagePage()
    page2._prepare_package(str(zpath), page2._on_local_prep_done)
    assert not page2._start_btn.isEnabled()
    w2 = page2._prep_worker
    w2.wait(5000)
    for _ in range(50):
        app.processEvents()
        if page2._start_btn.isEnabled():
            break
    assert page2._start_btn.isEnabled(), "start button enabled only after prep"

def test_auto_falls_back_when_sp_missing():
    """Auto method on Windows falls back to MTKClient when the SP Flash Tool
    payload is absent, instead of failing with SP_FLASH_TOOL_NOT_FOUND."""
    import src.flash_service as fs

    if not fs.IS_WINDOWS:
        return  # fallback path is Windows-specific

    real_find = fs.paths.find_sp_flash_tool
    fs.paths.find_sp_flash_tool = lambda: None
    try:
        calls = []
        w = fs.FlashWorker("pkg.zip", method="auto")
        w._log = lambda *a, **k: None
        w._flash_via_sp_flash_tool = lambda s: calls.append("sp")
        w._flash_via_mtkclient = lambda e, s: calls.append("mtk")
        w._dispatch_backend(Path("x"), Path("s"))
        assert calls == ["mtk"], f"auto must fall back to mtkclient when SP missing: {calls}"

        # Explicit "sp" still reports the missing tool rather than silently
        # switching backends.
        calls = []
        w2 = fs.FlashWorker("pkg.zip", method="sp")
        w2._log = lambda *a, **k: None
        w2._flash_via_sp_flash_tool = lambda s: calls.append("sp")
        w2._flash_via_mtkclient = lambda e, s: calls.append("mtk")
        w2._dispatch_backend(Path("x"), Path("s"))
        assert calls == ["sp"], f"explicit sp must still attempt SP Flash Tool: {calls}"
    finally:
        fs.paths.find_sp_flash_tool = real_find


def test_download_worker():
    """Verify DownloadWorker streaming, resume with Range headers, and cancellation."""
    from unittest.mock import patch, MagicMock
    from src.downloads import DownloadWorker

    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "test.zip"
        worker = DownloadWorker("https://fake.url/test.zip", str(dest))

        mock_response = MagicMock()
        mock_response.status_code = 206
        mock_response.headers = {
            "content-range": "bytes 100-199/200",
            "content-length": "100",
        }
        mock_response.iter_content.return_value = [b"x" * 100]

        with patch("requests.Session.get", return_value=mock_response):
            part_file = Path(f"{dest}.part")
            part_file.write_bytes(b"x" * 100)

            results = []
            worker.finished.connect(lambda ok, path: results.append((ok, path)))
            worker.run()

            assert results == [(True, str(dest))], f"Expected successful download, got {results}"
            assert dest.is_file()
            assert dest.stat().st_size == 200

        # Test cancellation
        dest2 = Path(td) / "cancel.zip"
        worker2 = DownloadWorker("https://fake.url/cancel.zip", str(dest2))
        worker2.cancel()
        results2 = []
        worker2.finished.connect(lambda ok, path: results2.append((ok, path)))
        worker2.run()
        assert results2 == [(False, "USER_CANCELLED")]


def main():
    print("== Neo updater smoke test ==")
    check("catalog", test_catalog)
    check("i18n languages", test_i18n_languages)
    check("rom variant parsing", test_rom_variant_parsing)
    check("donors", test_donors)
    check("relative date", test_relative_date)
    check("donation dialog translated", test_donation_dialog_translated)
    check("scatter parsing", test_scatter_parsing)
    check("manifest parse + merge", test_manifest_parse_and_merge)
    check("update version + assets", test_update_version_and_assets)
    check("update checker", test_update_checker)
    check("update dialog + wiring", test_update_dialog_and_wiring)
    check("flash_service import", test_flash_service_import)
    check("backend method dispatch", test_backend_method_dispatch)
    check("releases client (cached)", test_releases_client_cached)
    check("releases client (network)", test_releases_client_network)
    check("UI construction", test_ui_construction)
    check("donation status bar", test_donation_status_bar)
    check("goal reached hides goal line", test_goal_reached_hides_goal_line)
    check("diagnostics live update", test_diagnostics_live_update)
    check("flash flow launch", test_flash_flow_launch)
    check("flash method switch", test_flash_method_switch)
    check("language switch keeps screen", test_language_switch_keeps_screen)
    check("success dialog flow", test_success_dialog_flow)
    check("offline banner + generic model", test_offline_banner_and_generic_model)
    check("release list + notes", test_release_list_and_notes)
    check("release model filtering", test_release_model_filtering)
    check("mtk api init", test_mtk_api_init)
    check("cancel kills SP process", test_cancel_kills_sp_process)
    check("worker switch guard", test_worker_switch_guard)
    check("guided image selection", test_guided_image_selection)
    check("mtk system exit guarded", test_mtk_system_exit_guarded)
    check("mtk connect system exit guarded", test_mtk_connect_system_exit_guarded)
    check("mtk write system exit guarded", test_mtk_write_system_exit_guarded)
    check("linux sp flash validation", test_linux_sp_flash_validation)
    check("linux sp flash distro detection", test_linux_sp_flash_distro_detection)
    check("linux sp flash rules and readiness", test_linux_sp_flash_rules_and_readiness)
    check("linux setup dialog", test_linux_setup_dialog)
    check("package prep gates flash start", test_package_prep_gates_flash_start)
    check("auto falls back when SP missing", test_auto_falls_back_when_sp_missing)
    check("download worker resume and cancel", test_download_worker)
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for name, err in failures:
            print(f"  - {name}: {err!r}")
        sys.exit(1)
    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
