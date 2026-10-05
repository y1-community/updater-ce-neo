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
    _is_android_sparse,
    _convert_sparse_to_raw,
    _signed_image_payload_range,
    _detect_legacy_user_addr_bias,
    LEGACY_MBR_USER_ADDR_BIAS,
    LEGACY_SEGMENTED_WRITE_BYTES,
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
    s.setValue("donation_ui_disabled", False)
    s.setValue("donation_install_prompt_disabled", False)
    s.setValue("preferences/donation_ui_disabled", False)
    s.setValue("preferences/donation_install_prompt_disabled", False)
    s.setValue("linux_first_run_completed", True)
    s.remove("preferences")
    s.remove("device_installs")
    s.remove("device_tracking")
    s.remove("latest_package")
    translator().set_language("en")


def check(name, fn):
    try:
        fn()
        print(f"  PASS  {name}")
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        failures.append((name, e))
        print(f"  FAIL  {name}: {e!r}\n{tb}")


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


def test_simulate_macos_flag_parsing():
    """launcher.py --simulate-macos flips INNIOASIS_SIMULATE_MACOS before any
    src import, so paths.SIMULATE_MACOS / paths.IS_MAC are effective at import
    time (exactly how a frozen build consumes the flag)."""
    import subprocess

    env = {k: v for k, v in os.environ.items() if k != "INNIOASIS_SIMULATE_MACOS"}
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    probe = (
        "import os, sys;"
        "extra_argv = sys.argv[1:];"
        "sys.argv = ['launcher.py'] + extra_argv;"
        "import runpy;"
        "runpy.run_path('launcher.py', run_name='sim_probe');"
        "from src import paths;"
        "print('SIM=' + str(paths.SIMULATE_MACOS));"
        "print('ISMAC=' + str(paths.IS_MAC));"
        "print('ENV=' + os.environ.get('INNIOASIS_SIMULATE_MACOS', ''))"
    )

    # Without the flag: no simulation.
    r = subprocess.run(
        [sys.executable, "-c", probe, ""],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    assert r.returncode == 0, f"probe failed: {r.stderr[-500:]}"
    assert "SIM=False" in r.stdout, r.stdout
    assert "ENV=" in r.stdout, r.stdout

    # With --simulate-macos: simulation active at import time.
    r = subprocess.run(
        [sys.executable, "-c", probe, "--simulate-macos"],
        capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=120,
    )
    assert r.returncode == 0, f"probe failed: {r.stderr[-500:]}"
    assert "SIM=True" in r.stdout, r.stdout
    assert "ISMAC=True" in r.stdout, r.stdout
    assert "ENV=1" in r.stdout, r.stdout


def test_simulate_macos_backend_parity():
    """With --simulate-macos active on a Linux host, the backend matrix must
    be identical to a real macOS build: SP Flash Tool is unreachable and every
    dispatch path (auto/sp/mtk) lands on MTKClient."""
    import src.flash_service as fs
    from src import paths as _paths
    from src.sp_flash_gui import is_sp_flash_gui_supported, launch_sp_flash_tool_gui

    if sys.platform == "darwin":
        return  # already a real Mac; nothing to simulate

    # Save/restore every platform flag the sim mode touches.
    saved = {
        "paths.SIMULATE_MACOS": _paths.SIMULATE_MACOS,
        "paths.IS_MAC": _paths.IS_MAC,
        "fs.SIMULATE_MACOS": getattr(fs, "SIMULATE_MACOS", None),
        "fs.IS_MAC": fs.IS_MAC,
    }

    def set_sim(on):
        _paths.SIMULATE_MACOS = on
        _paths.IS_MAC = on or sys.platform == "darwin"
        fs.SIMULATE_MACOS = on
        fs.IS_MAC = _paths.IS_MAC

    try:
        set_sim(True)

        def dispatch(method):
            calls = []
            w = fs.FlashWorker("pkg.zip", method=method)
            w._log = lambda *a, **k: None
            w._flash_via_sp_flash_tool = lambda s: calls.append("sp")
            w._flash_via_mtkclient = lambda e, s: calls.append("mtk")
            w._dispatch_backend(Path("x"), Path("s"))
            return calls

        # macOS parity contract (mirrors test_backend_method_dispatch's macOS
        # branch, exercised here on a simulated Mac):
        assert dispatch("mtk") == ["mtk"]
        assert dispatch("sp") == ["mtk"], "sp must be unreachable in macOS mode"
        assert dispatch("auto") == ["mtk"], "auto must be MTKClient in macOS mode"

        # SP Flash Tool GUI surfaces behave like on a real Mac.
        assert is_sp_flash_gui_supported() is False
        ok, msg = launch_sp_flash_tool_gui()
        assert ok is False and msg, "simulated macOS must refuse SP Flash Tool GUI"

        # Method matrix: the Settings selector owns it now, and macOS offers no
        # SP Flash Tool entry (MTKClient is the only backend there).
        from PySide6.QtWidgets import QApplication
        from src.ui.settings_page import METHOD_MTK, SettingsPage
        from src.ui.dialogs import ReleaseReminderDialog
        from src.ui.main_window import _method_label
        from src.sp_flash_gui import find_sp_flash_tool_dirs, update_sp_history_ini

        assert _paths.find_sp_flash_tool() is None
        assert find_sp_flash_tool_dirs() == []
        assert update_sp_history_ini("/dummy") is False
        assert _method_label("sp") == "MTKClient"

        app = QApplication.instance() or QApplication(sys.argv)
        page = SettingsPage()
        try:
            assert page._available_methods() == (METHOD_MTK,), page._available_methods()
            # On macOS, cards relating to SP Flash Tool / backend options must be hidden
            assert page._checker_card.isHidden() is True
            assert page._method_card.isHidden() is True
            page.refresh_settings()
            assert page._checker_card.isHidden() is True
            assert page._method_card.isHidden() is True
            page.retranslate()
            assert page._checker_card.isHidden() is True
            assert page._method_card.isHidden() is True
            # "MTKClient (Mac)" is meaningless on a Mac, so the M/D reveal is a
            # no-op there rather than adding a redundant duplicate.
            page.reveal_advanced_methods()
            assert page._available_methods() == (METHOD_MTK,), page._available_methods()
        finally:
            page.deleteLater()

        from src.ui.flash_page import FlashPage
        fp = FlashPage()
        try:
            assert hasattr(fp, "_open_sp_gui_btn") is False
            assert fp._initsteps_image() == "initsteps.png"
        finally:
            fp.deleteLater()

        from src.ui.error_page import ErrorPage
        ep = ErrorPage()
        try:
            assert hasattr(ep, "_sp_gui_btn") is False
        finally:
            ep.deleteLater()

        # Dialog prompt on macOS never refers to SP Flash Tool even if passed "sp"
        dlg = ReleaseReminderDialog(
            update_info={
                "model": "y1",
                "software_name": "Inniclassic",
                "installed_tag": "0.4",
                "latest_tag": "1.0",
            },
            flash_method="sp",
        )
        try:
            assert "SP Flash" not in dlg._prompt_lbl.text()
            assert "MTKClient" in dlg._prompt_lbl.text()
        finally:
            dlg.deleteLater()
    finally:
        _paths.SIMULATE_MACOS = saved["paths.SIMULATE_MACOS"]
        _paths.IS_MAC = saved["paths.IS_MAC"]
        fs.IS_MAC = saved["fs.IS_MAC"]
        if saved["fs.SIMULATE_MACOS"] is None:
            if hasattr(fs, "SIMULATE_MACOS"):
                del fs.SIMULATE_MACOS
        else:
            fs.SIMULATE_MACOS = saved["fs.SIMULATE_MACOS"]


def test_releases_client_cached():
    with tempfile.TemporaryDirectory() as td:
        client = ReleasesClient(cache_root=td)
        fake = [{"tag_name": "v1", "download_url": "u", "rom_variants": []}]
        client.cache_releases("a/b", fake)
        assert client.get_cached_releases("a/b") == fake


def test_releases_client_force_refresh():
    """Verify force_refresh=True bypasses cache and calls network."""
    with tempfile.TemporaryDirectory() as td:
        client = ReleasesClient(cache_root=td)
        old_fake = [{"tag_name": "3.1.7", "download_url": "u1", "rom_variants": [{"asset": {"browser_download_url": "u1", "name": "rom_y2.zip", "size": 100}, "model": "Y2", "type": "A", "resolution": "native"}]}]
        client.cache_releases("test/repo", old_fake)
        # Without force_refresh: returns cached
        assert client.get_all_releases("test/repo", force_refresh=False) == old_fake

        # Mock _get_json to return fresh GitHub release data
        new_data = [
            {"tag_name": "3.2.1", "name": "System Software 3.2.1", "published_at": "2026-09-13T12:00:00Z", "assets": [{"name": "rom_y2.zip", "browser_download_url": "u2", "size": 200}]},
            {"tag_name": "3.1.7", "name": "Original System Software 3.1.7", "published_at": "2025-01-01T10:00:00Z", "assets": [{"name": "rom_y2.zip", "browser_download_url": "u1", "size": 100}]},
        ]
        client._get_json = lambda url: new_data
        refreshed = client.get_all_releases("test/repo", force_refresh=True)
        assert len(refreshed) == 2
        assert refreshed[0]["tag_name"] == "3.2.1"
        assert refreshed[1]["tag_name"] == "3.1.7"
        # Cache should now be updated on disk
        cached_now = client.get_cached_releases("test/repo")
        assert len(cached_now) == 2
        assert cached_now[0]["tag_name"] == "3.2.1"


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
    from src.flash_service import STEP_WAITING, STEP_WRITE, STEP_DETECT, STEP_DONE

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
    # Warning must NOT be visible while waiting / searching USB before handshake
    assert not w._flash_page._warning.isVisible()

    # Device detected -> write step -> flashing view + S4.
    w.service.step_changed.emit(STEP_DETECT)
    assert not w._flash_page._warning.isVisible()
    w.service.step_changed.emit(STEP_WRITE)
    assert w.sm.state is FlashState.S4_FLASHING, w.sm.state
    assert w._flash_page._stack.currentWidget() is w._flash_page._flashing_view
    # The install-method selector (on Settings) is locked once flashing starts.
    assert not w._settings_page._method_combo.isEnabled()
    # In-progress copy: banner and status tag both read "Install in Progress".
    assert w._flash_page._flash_banner.text() == "Install in Progress"
    assert w._flash_page._wait_status.text() == "Install in Progress"
    # Warning must be visible only during active write / flash installation
    assert w._flash_page._warning.isVisible()
    assert "Do not unplug" in w._flash_page._warning.text()

    # When done, warning is hidden
    w.service.step_changed.emit(STEP_DONE)
    assert not w._flash_page._warning.isVisible()
    w.close()
    app.processEvents()


def test_flash_method_switch():
    """Switching the install method in Settings while waiting restarts the run
    with the new backend, and the choice is persisted. Legacy "auto" resolves
    to the platform default (SP Flash Tool) rather than its own mode."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    calls = []
    w.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append((pkg, method))
    w.service.start_device_monitor = lambda: None
    w.settings.setValue("flash_method", "auto")
    w._flash_method = "auto"

    w._on_package_selected("C:/fake/rom.zip", "Rockbox (Y1)", "Y1")
    assert w._flash_page.current_method() == "sp", "legacy auto -> SP Flash Tool"

    # User switches to MTKClient while the backend searches.
    combo = w._settings_page._method_combo
    combo.setCurrentIndex(combo.findData("mtk"))
    assert w._settings_page.current_method() == "mtk"
    assert calls[-1] == ("C:/fake/rom.zip", "mtk"), "backend restarted with new method"
    assert w._flash_page.current_method() == "mtk", "flash page reflects the new method"
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
    w.service.start_device_monitor = lambda: None
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
    # New Settings strings translate in place too.
    assert w._settings_page._cb_reminders.text() == "Envíame recordatorios de nuevas versiones"
    assert w._settings_page._method_combo.itemText(0) == "SP Flash Tool"

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
        # Legacy "auto" now resolves to the platform default (the SP Flash Tool
        # console flow) instead of being a method of its own.
        page.set_method("auto")
        assert page.current_method() == "sp"
        assert page._initsteps_image() == "initsteps_sp.png", "auto on Windows -> SP"

        paths.IS_WINDOWS, paths.IS_MAC = False, True
        page.set_method("auto")
        assert page.current_method() == "mtk"
        assert page._initsteps_image() == "initsteps.png", "auto on macOS -> MTKClient"
        page.set_method("mtk")
        assert page._initsteps_image() == "initsteps.png"
        page.set_method("sp")
        assert page.current_method() == "mtk", "macOS has no SP Flash Tool backend"
        assert page._initsteps_image() == "initsteps.png"
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
    """A mtkclient SystemExit during the handshake becomes CONNECTION_FAILED."""
    import src.flash_service as fs
    import src.mtk_api as mtk_api

    w = fs.FlashWorker("pkg.zip", method="mtk")
    results = []
    w.finished.connect(lambda ok, err: results.append((ok, err)))
    w._log = lambda *a, **k: None

    real_scatter = fs._parse_scatter
    real_init, real_handshake = mtk_api.init, mtk_api.handshake
    fs._parse_scatter = lambda scatter: [("system", Path("/tmp/system.img"))]

    mtk_api.init = lambda loader=None, preloader=None, **kw: object()

    def boom(session, directory="."):
        raise SystemExit(1)

    mtk_api.handshake = boom
    try:
        w._flash_via_mtkclient("/tmp", "/tmp/scatter.txt")
    finally:
        mtk_api.init, mtk_api.handshake = real_init, real_handshake
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
    real_init, real_handshake, real_attach = (
        mtk_api.init, mtk_api.handshake, mtk_api.attach
    )
    fs._parse_scatter = lambda scatter: [("system", Path("/tmp/system.img"))]

    class FakeConfig:
        is_brom = True
        gui = None

    class FakeSession:
        config = FakeConfig()

    def fake_attach(session, directory="."):
        class FakeHandler:
            def handle_da_cmds(self, *a, **k):
                raise SystemExit(1)

        return (FakeSession(), FakeHandler())

    mtk_api.init = lambda loader=None, preloader=None, **kw: FakeSession()
    mtk_api.handshake = lambda session, directory=".": (session, object())
    mtk_api.attach = fake_attach
    try:
        w._flash_via_mtkclient("/tmp", "/tmp/scatter.txt")
    finally:
        mtk_api.init, mtk_api.handshake, mtk_api.attach = (
            real_init, real_handshake, real_attach
        )
        fs._parse_scatter = real_scatter
    assert results == [(False, "WRITE_FAILED")], results


def test_usblib_reads_are_bounded():
    """libusb treats a timeout of 0 as *wait forever*, so every bulk read in
    mtkclient's usblib must carry a real timeout. Otherwise a target that stops
    answering after the stage-2 DA upload parks the worker thread forever and
    the app can never fail, cancel or retry (the reported mac/linux hang)."""
    import inspect
    import re as _re
    from mtkclient.Library.Connection import usblib

    for name in ("usbread", "usbxmlread"):
        src = inspect.getsource(getattr(usblib.UsbClass, name))
        # \b keeps "repr(e)" from being mistaken for a read call.
        calls = _re.findall(r"\bepr\(([^)]*)\)", src)
        assert calls, f"{name}: no endpoint reads found"
        for args in calls:
            assert "," in args, f"{name}: unbounded read epr({args}) — timeout=0 waits forever"
            assert "self.timeout" in args, f"{name}: read without self.timeout: epr({args})"


def test_mtk_stall_watcher():
    """A silent device is reported once, and the warning re-arms once the
    backend hears from the device again."""
    import time as _time

    from src.flash_service import _StallWatcher, _MtkMessageBridge

    stalled = []
    w = _StallWatcher(lambda idle: stalled.append(round(idle, 1)), timeout=0.2, interval=0.02)
    w.start()
    deadline = _time.time() + 5
    while not stalled and _time.time() < deadline:
        _time.sleep(0.02)
    assert len(stalled) == 1, stalled

    # Still silent: no repeated spam from the watcher.
    _time.sleep(0.3)
    assert len(stalled) == 1, stalled

    # Device answers again -> a later silence warns again.
    w.activity()
    deadline = _time.time() + 5
    while len(stalled) < 2 and _time.time() < deadline:
        _time.sleep(0.02)
    assert len(stalled) == 2, stalled
    w.stop()
    assert w.daemon is True

    forwarded = []
    _MtkMessageBridge(forwarded.append).emit("Successfully uploaded stage 2")
    assert forwarded == ["Successfully uploaded stage 2"]


def test_mtkclient_da_progress_surface():
    """The MTKClient path must (a) relay mtkclient's own phase messages,
    (b) restart a preloader-mode player into BROM before the DA is uploaded
    (the stage-2 stall), reporting each mode truthfully, and (c) mark the
    install as started from the first image write — the reference updater
    derives install progress from the writes, not the handshake."""
    from src import flash_service as fs
    import src.mtk_api

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "preloader_g368_nyx.bin").write_bytes(b"p" * 64)
        (d / "boot.img").write_bytes(b"b" * 64)
        scatter = d / "MT6572_Android_scatter.txt"
        scatter.write_text(
            "- general: MTK_PLATFORM_CFG\n"
            "  info:\n"
            "    - platform: MT6572\n"
            "- partition_index: SYS0\n"
            "  partition_name: preloader\n"
            "  file_name: preloader_g368_nyx.bin\n"
            "  is_download: true\n"
            "- partition_index: SYS1\n"
            "  partition_name: boot\n"
            "  file_name: boot.img\n"
            "  is_download: true\n",
            encoding="utf-8",
        )

        class FakeConfig:
            def __init__(self):
                # PID 0x2000 + a preloader BL version: preloader mode, NOT BROM.
                self.is_brom = False
                self.gui = None
                self.guiprogress = None

        class FakeMtk:
            def __init__(self):
                self.config = FakeConfig()

        class FakeDaloader:
            flashmode = None

        mtk = FakeMtk()
        mtk.daloader = FakeDaloader()

        saved = {
            name: getattr(src.mtk_api, name)
            for name in ("init", "handshake", "attach", "arm_brom_boot",
                        "trigger_reset", "close_port")
        }
        saved["_wait"] = fs._wait_for_mtk_mode
        sessions = {"count": 0}

        src.mtk_api.init = lambda loader=None, preloader=None, **kw: mtk
        src.mtk_api.arm_brom_boot = lambda handle: True
        src.mtk_api.trigger_reset = lambda handle: True
        src.mtk_api.close_port = lambda handle: None
        # No hardware on the test host: the restart is deemed to have landed.
        fs._wait_for_mtk_mode = lambda pids, timeout: True

        def fake_handshake(handle, directory="."):
            sessions["count"] += 1
            # mtkclient routes its phase chatter here once config.gui is set.
            handle.config.gui.emit("DaHandler - Device is in Preloader-Mode.")
            # Preloader mode on the first look; the restart lands us in BROM.
            handle.config.is_brom = sessions["count"] > 1
            return handle, object()

        def fake_attach(handle, directory="."):
            handle.config.gui.emit("Successfully uploaded stage 2")
            return handle, object()

        src.mtk_api.handshake = fake_handshake
        src.mtk_api.attach = fake_attach

        holds = []
        w = fs.FlashWorker(
            str(d / "rom.zip"), method="mtk", device_loss_hold=holds.append
        )
        steps, logs, actions, results = [], [], [], []
        w.step_changed.connect(steps.append)
        w.log_message.connect(logs.append)
        w.action_changed.connect(actions.append)
        w.finished.connect(lambda ok, err: results.append((ok, err)))
        writes = []
        w._write_mtk_partition = lambda *a, **k: writes.append(k.get("part_type", a[4] if len(a) > 4 else ""))

        try:
            w._flash_via_mtkclient(str(d), str(scatter))
        finally:
            for name, fn in saved.items():
                if name == "_wait":
                    fs._wait_for_mtk_mode = fn
                else:
                    setattr(src.mtk_api, name, fn)

        assert results == [(True, "")], results
        # mtkclient's phase messages reached the diagnostics log.
        assert "Successfully uploaded stage 2" in logs, logs
        assert "DaHandler - Device is in Preloader-Mode." in logs, logs
        # A preloader-mode player is restarted into BROM before the DA upload,
        # and both modes are reported truthfully.
        assert sessions["count"] == 2, sessions
        mode_msgs = [m for m in actions if "detected" in m]
        assert mode_msgs[0] == "Device detected (preloader mode) - configuring download agent...", mode_msgs
        assert mode_msgs[-1] == "Device detected (BROM mode) - configuring download agent...", mode_msgs
        assert any("Restarting the player into BROM mode" in m for m in actions), actions
        # The deliberate restart must not reach the USB monitor as an unplug.
        assert holds and holds[0] >= 30, holds
        # Install-started comes from the first image write, not the handshake.
        assert steps.index(fs.STEP_WRITE) > steps.index(fs.STEP_DETECT), steps
        first_write_log = next(i for i, m in enumerate(logs) if m.startswith("Writing "))
        write_step_log = next(i for i, m in enumerate(logs) if m.startswith("Install in Progress"))
        assert write_step_log < first_write_log, logs
        assert steps.count(fs.STEP_WRITE) == 1, steps
        assert writes, "partitions must actually be written"
        # The gui hook is released after the session.
        assert mtk.config.gui is None


def _mtk_flash_fixture(td):
    """Minimal MT6572 package used by the MTKClient session tests."""
    d = Path(td)
    (d / "preloader_g368_nyx.bin").write_bytes(b"p" * 64)
    (d / "boot.img").write_bytes(b"b" * 64)
    scatter = d / "MT6572_Android_scatter.txt"
    scatter.write_text(
        "- general: MTK_PLATFORM_CFG\n"
        "  info:\n"
        "    - platform: MT6572\n"
        "- partition_index: SYS0\n"
        "  partition_name: preloader\n"
        "  file_name: preloader_g368_nyx.bin\n"
        "  is_download: true\n"
        "- partition_index: SYS1\n"
        "  partition_name: boot\n"
        "  file_name: boot.img\n"
        "  is_download: true\n",
        encoding="utf-8",
    )
    return d, scatter


def test_mtk_da_failure_auto_recovers():
    """A failed DA upload must not be the end of the run: the worker waits for
    the player to come back and retries once before falling back to the retry
    dialog. When the player never returns, the run fails cleanly with the
    reconnect instructions."""
    from src import flash_service as fs
    import src.mtk_api

    class FakeConfig:
        def __init__(self):
            self.is_brom = True
            self.gui = None
            self.guiprogress = None

    class FakeMtk:
        def __init__(self):
            self.config = FakeConfig()

    saved = {n: getattr(src.mtk_api, n) for n in ("init", "handshake", "attach")}
    saved["_wait"] = fs._wait_for_mtk_mode
    try:
        for attempt_outcome in (["fail", "ok"], ["fail", "fail"]):
            with tempfile.TemporaryDirectory() as td:
                d, scatter = _mtk_flash_fixture(td)
                mtk = FakeMtk()
                calls = {"attach": 0}

                src.mtk_api.init = lambda loader=None, preloader=None, **kw: mtk
                src.mtk_api.handshake = lambda handle, directory=".": (handle, object())

                def fake_attach(handle, directory=".", _outcome=attempt_outcome):
                    calls["attach"] += 1
                    if _outcome[min(calls["attach"], 2) - 1] == "fail":
                        return None, None
                    return handle, object()

                src.mtk_api.attach = fake_attach
                # Device is already in BROM: no restart involved here.
                fs._wait_for_mtk_mode = lambda pids, timeout: True

                w = fs.FlashWorker(str(d / "rom.zip"), method="mtk")
                logs, actions, results = [], [], []
                w.log_message.connect(logs.append)
                w.action_changed.connect(actions.append)
                w.finished.connect(lambda ok, err: results.append((ok, err)))
                w._write_mtk_partition = lambda *a, **k: True
                w._flash_via_mtkclient(str(d), str(scatter))

                if attempt_outcome == ["fail", "ok"]:
                    assert calls["attach"] == 2, calls
                    assert results == [(True, "")], results
                    assert any("Attempt 1 failed" in m for m in logs), logs
                    assert any("retrying" in m for m in actions), actions
                else:
                    # Exactly one automatic retry, then the retry dialog's
                    # CONNECTION_FAILED takes over.
                    assert calls["attach"] == fs.MTK_DA_ATTEMPTS, calls
                    assert results == [(False, "CONNECTION_FAILED")], results
                    assert logs.count("The download agent did not come up.") == 1, logs
                    assert any("Attempt 1 failed" in m for m in logs), logs
    finally:
        for name, fn in saved.items():
            if name == "_wait":
                fs._wait_for_mtk_mode = fn
            else:
                setattr(src.mtk_api, name, fn)


def test_mtk_retry_gives_up_when_device_stays_away():
    """The recovery wait must not loop forever: a player that never returns
    ends the run instead of parking the worker."""
    from src import flash_service as fs
    import src.mtk_api

    class FakeConfig:
        def __init__(self):
            self.is_brom = True
            self.gui = None

    class FakeMtk:
        def __init__(self):
            self.config = FakeConfig()

    saved = {n: getattr(src.mtk_api, n) for n in ("init", "handshake", "attach")}
    real_wait = fs._wait_for_mtk_mode
    try:
        mtk = FakeMtk()
        src.mtk_api.init = lambda loader=None, preloader=None, **kw: mtk
        src.mtk_api.handshake = lambda handle, directory=".": (handle, object())
        src.mtk_api.attach = lambda handle, directory=".": (None, None)
        fs._wait_for_mtk_mode = lambda pids, timeout: False
        with tempfile.TemporaryDirectory() as td:
            d, scatter = _mtk_flash_fixture(td)
            w = fs.FlashWorker(str(d / "rom.zip"), method="mtk")
            logs, results = [], []
            w.log_message.connect(logs.append)
            w.finished.connect(lambda ok, err: results.append((ok, err)))
            w._flash_via_mtkclient(str(d), str(scatter))
        assert results == [(False, "CONNECTION_FAILED")], results
        assert logs.count("The download agent did not come up.") == 1, logs
        assert any("start the flash again" in m for m in logs), logs
    finally:
        for name, fn in saved.items():
            setattr(src.mtk_api, name, fn)
        fs._wait_for_mtk_mode = real_wait


def test_device_monitor_lost_hold():
    """Restarting the player into BROM makes it re-enumerate; the monitor must
    be able to stay quiet during that window or it aborts the flash."""
    from src.flash_service import DeviceMonitor

    m = DeviceMonitor()
    assert m._lost_is_held() is False
    m.hold_lost(60)
    assert m._lost_is_held() is True
    # A shorter later hold must not shorten the window already granted.
    m.hold_lost(1)
    assert m._lost_is_held() is True
    m.release_lost_hold()
    assert m._lost_is_held() is False


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
    if lsf.files_ready(lsf.stage_dir()):
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


def test_linux_sp_flash_askpass_and_step1_deferral():
    """Verify Linux askpass helper, runner wrapper, silent prep, and deferred Step 1."""
    from unittest.mock import patch
    import io
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from src import linux_sp_flash as lsf
    from src.flash_service import FlashWorker, STEP_WAITING
    from src.ui.select_page import SelectPackagePage

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        runner = lsf.create_flash_tool_runner(tdp)
        assert runner.is_file()
        assert os.access(runner, os.X_OK)
        text = runner.read_text(encoding="utf-8")
        assert "LD_LIBRARY_PATH" in text
        assert "QT_PLUGIN_PATH" in text
        assert "run_flash_tool.sh" in str(runner)

        prep_ok = lsf.silent_system_prep(tdp)
        assert prep_ok is True
        assert (tdp / lsf.LOG_DIR_NAME).is_dir()

    askpass = lsf.get_askpass_helper()
    assert askpass is not None and askpass.is_file()
    assert os.access(askpass, os.X_OK)
    ap_text = askpass.read_text(encoding="utf-8")
    assert "zenity" in ap_text or "kdialog" in ap_text or "SUDO_ASKPASS" in ap_text

    # _PrependedStream preserves pre-read lines
    stream = lsf._PrependedStream("header_line\n", io.StringIO("second_line\nthird_line\n"))
    lines = list(stream)
    assert lines == ["header_line\n", "second_line\n", "third_line\n"]

    # SelectPackagePage: Step 1 modal is skipped on Linux for SP Flash Tool (deferred until "search usb")
    settings = QSettings("Innioasis", "UpdaterCE")
    settings.setValue("flash_method", "sp")
    page = SelectPackagePage()
    with patch("sys.platform", "linux"):
        with patch("src.paths.IS_WINDOWS", False):
            with patch("src.paths.IS_MAC", False):
                assert page._should_prompt_pre_install() is False, "Linux SP flow must not prompt upfront"

        with patch("src.paths.IS_WINDOWS", True):
            assert page._should_prompt_pre_install() is True, "Windows must prompt upfront"

    # Line classification: "search usb" triggers STEP_WAITING (step 1 displayed naturally)
    worker = FlashWorker("dummy.zip", "sp", "scatter.txt")
    emitted = []
    worker.step_changed.connect(emitted.append)
    worker._classify_sp_stdout("Search USB, timeout 3600000 ms...")
    assert STEP_WAITING in emitted
    page.deleteLater()

    # Escalation tool discovery & doas support tests
    with patch("shutil.which") as mock_which:
        # Scenario A: Alpine/Void Linux where only doas is installed
        mock_which.side_effect = lambda cmd: "/usr/bin/doas" if cmd == "doas" else None
        tools = lsf.find_available_escalation_tools()
        assert tools == ["doas"], f"Expected ['doas'], got {tools}"

        # Scenario B: System where /etc/doas.conf exists alongside sudo and pkexec
        def _which_all(cmd):
            if cmd in ("doas", "sudo", "pkexec", "run0"):
                return f"/usr/bin/{cmd}"
            return None
        mock_which.side_effect = _which_all
        with patch.object(Path, "is_file", lambda self: str(self) == "/etc/doas.conf"):
            tools = lsf.find_available_escalation_tools()
            assert tools[0] == "doas", f"doas with /etc/doas.conf must be prioritized, got {tools}"
            assert "pkexec" in tools
            assert "sudo" in tools
            assert "run0" in tools

    # Test doas launcher with mock binary:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        # Mock doas binary
        mock_script = tdp / "mock_doas"
        mock_script.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-n" ]; then\n'
            "    exit 0\n"
            "fi\n"
            'exec "$@"\n'
        )
        mock_script.chmod(0o755)

        with patch("shutil.which", lambda cmd: str(mock_script) if cmd == "doas" else None):
            runner_dummy = tdp / "dummy_runner.sh"
            runner_dummy.write_text("#!/bin/bash\necho 'Search USB, timeout 3600000 ms...'\n")
            runner_dummy.chmod(0o755)
            proc, cancelled = lsf._launch_with_doas(runner_dummy, [], tdp, os.environ.copy())
            assert cancelled is False
            assert proc is not None
            out_line = proc.stdout.readline()
            assert "Search USB" in out_line
            proc.wait()

    app.processEvents()


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


def test_glass_module():
    """Verify macOS Liquid Glass bridge, Ventura to Golden Gate compatibility, and safe no-ops."""
    import platform
    from src.ui import glass
    from PySide6.QtWidgets import QWidget

    w = QWidget()
    if not glass.IS_MACOS:
        assert not glass.is_glass_supported()
        assert glass.prepare_window_for_glass(w) is False
        assert glass.apply_glass(w) is False
        assert glass.configure_traffic_lights(w) is False

    assert glass.VENTURA_VERSION == (13, 0, 0)
    assert glass.GOLDEN_GATE_VERSION == (26, 0, 0)
    assert glass.ARCH in ("x86_64", "arm64", "aarch64", "amd64", platform.machine().lower())
    w.close()


def _relative_luminance(hex_color: str) -> float:
    """Calculate relative luminance for WCAG contrast ratio."""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 8:  # ignore alpha if present
        hex_color = hex_color[:6]
    r, g, b = [int(hex_color[i:i+2], 16) / 255.0 for i in (0, 2, 4)]
    r = r / 12.92 if r <= 0.04045 else ((r + 0.055) / 1.055) ** 2.4
    g = g / 12.92 if g <= 0.04045 else ((g + 0.055) / 1.055) ** 2.4
    b = b / 12.92 if b <= 0.04045 else ((b + 0.055) / 1.055) ** 2.4
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast_ratio(c1: str, c2: str) -> float:
    """Compute WCAG 2.1 contrast ratio between two hex colors."""
    l1 = _relative_luminance(c1)
    l2 = _relative_luminance(c2)
    lighter = max(l1, l2)
    darker = min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def test_native_theming():
    """Verify native QStyle detection, typography stack, dual-theme contrast, and semantic classes."""
    from PySide6.QtWidgets import QApplication
    from src.ui import dark

    app = QApplication.instance() or QApplication(sys.argv)
    style_name = dark.setup_native_app_style(app)
    assert style_name is not None
    assert len(style_name) > 0

    # Test Dark Mode Tokens
    dark_tokens = dark._Tokens(dark=True)
    assert _contrast_ratio(dark_tokens.fg, dark_tokens.bg) >= 7.0, "Dark mode text must meet AAA"
    assert _contrast_ratio(dark_tokens.fg_dim, dark_tokens.bg_card) >= 4.5, "Dark mode dimmed text must meet AA"
    assert _contrast_ratio(dark_tokens.accent_text, dark_tokens.accent_bg) >= 4.5, "Dark mode accent text must meet AA"

    for badge_name in ("status_idle", "status_connected", "status_disconn", "status_flashing", "status_complete"):
        fg, bg = getattr(dark_tokens, badge_name)
        ratio = _contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"Dark badge {badge_name} contrast {ratio:.2f} < 4.5"

    dark.apply_theme(app, force_dark=True)
    qss_dark = dark._build_qss()
    assert "font-family:" in qss_dark
    assert "#navPanel" in qss_dark
    assert "cssClass=\"cardTitle\"" in qss_dark
    assert "cssClass=\"field-label\"" in qss_dark
    assert "min-height: 36px" in qss_dark, "Primary buttons must meet 36px touch point target"
    assert "min-height: 34px" in qss_dark, "Form controls/nav buttons must meet 34px touch target"

    # Test Light Mode Tokens
    light_tokens = dark._Tokens(dark=False)
    assert _contrast_ratio(light_tokens.fg, light_tokens.bg) >= 7.0, "Light mode text must meet AAA"
    assert _contrast_ratio(light_tokens.fg_dim, light_tokens.bg_card) >= 4.5, "Light mode dimmed text must meet AA"
    assert _contrast_ratio(light_tokens.accent_text, light_tokens.accent_bg) >= 4.5, "Light mode accent text must meet AA"

    for badge_name in ("status_idle", "status_connected", "status_disconn", "status_flashing", "status_complete"):
        fg, bg = getattr(light_tokens, badge_name)
        ratio = _contrast_ratio(fg, bg)
        assert ratio >= 4.5, f"Light badge {badge_name} contrast {ratio:.2f} < 4.5"

    # Test Banner Colors (info, ok, warn, danger) in both modes
    for t_set, mode in ((dark_tokens, "dark"), (light_tokens, "light")):
        assert _contrast_ratio(t_set.ok_fg, t_set.ok_bg) >= 4.5, f"{mode} ok banner contrast < 4.5"
        assert _contrast_ratio(t_set.warn_fg, t_set.warn_bg) >= 4.5, f"{mode} warn banner contrast < 4.5"
        assert _contrast_ratio(t_set.danger_fg, t_set.danger_bg) >= 4.5, f"{mode} danger banner contrast < 4.5"
        assert _contrast_ratio(t_set.info_fg, t_set.info_bg) >= 4.5, f"{mode} info banner contrast < 4.5"

    # Test Nav rail contrast (dark background)
    nav_bg = "#0b1120"
    assert _contrast_ratio("#f1f5f9", nav_bg) >= 7.0, "Nav brand text must meet AAA (> 7:1)"
    assert _contrast_ratio("#cbd5e1", nav_bg) >= 7.0, "Nav button text must meet AAA (> 7:1)"
    assert _contrast_ratio("#94a3b8", nav_bg) >= 7.0, "Nav secondary label text must meet AAA (> 7:1)"

    dark.apply_theme(app, force_dark=False)
    qss_light = dark._build_qss()
    assert "font-family:" in qss_light
    assert "#navPanel" in qss_light

    if dark.IS_MACOS:
        assert "SF Pro Text" in qss_light
    elif dark.IS_WINDOWS:
        assert "Segoe UI" in qss_light

    # Reset back to default detection
    dark.apply_theme(app)


def test_preloader_raw_wrapping_and_routing():
    """Verify raw MMM\\x01 preloader binaries receive valid BRLYT/EMMC_BOOT headers."""
    import src.flash_service as fs

    # 1. Create a synthetic raw preloader binary
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
        raw_preloader_path = Path(tf.name)
        # Magic MMM\x01 preceded by some bytes and followed by payload
        tf.write(b"XYZ" + fs._PRELOADER_MAGIC + b"\x00" * 1024)

    try:
        wrapped = fs._wrap_preloader_for_raw_boot(raw_preloader_path, storage_type="emmc")
        assert wrapped != raw_preloader_path
        assert wrapped.exists()
        with open(wrapped, "rb") as f:
            data = f.read()
        # Verify headers
        assert data[:9] == b"EMMC_BOOT"
        assert data[fs._BRLYT_OFFSET : fs._BRLYT_OFFSET + 5] == b"BRLYT"
        assert len(data) == fs._BOOT_HEADER_SIZE + len(b"\x00" * 1024) + len(fs._PRELOADER_MAGIC)

        # An already-wrapped preloader should not be rewrapped
        rewrapped = fs._wrap_preloader_for_raw_boot(wrapped, storage_type="emmc")
        assert rewrapped == wrapped

        # Clean up wrapped file
        wrapped.unlink(missing_ok=True)
    finally:
        raw_preloader_path.unlink(missing_ok=True)

    # 2. Test preloader routing targets for legacy vs modern
    w = fs.FlashWorker("dummy.zip", method="mtk")

    class FakeMtkLegacy:
        class daloader:
            flashmode = "LEGACY"

    class FakeMtkModern:
        class daloader:
            flashmode = "XFLASH"

    assert w._resolve_preloader_target_parts(FakeMtkLegacy()) == ("boot1",)
    assert w._resolve_preloader_target_parts(FakeMtkModern()) == ("boot1", "boot2")
    assert w._resolve_preloader_target_parts(FakeMtkLegacy(), "EMMC_BOOT_1") == ("boot1",)
    assert w._resolve_preloader_target_parts(FakeMtkModern(), "EMMC_BOOT_2") == ("boot2",)


def test_cross_platform_mtk_payloads_and_backend_dispatch():
    """Verify vendor DA payloads are present for modern SoCs and backend routing is correct."""
    from src import paths

    # Verify payloads in vendor directory
    vendor_payloads = paths.BASE_DIR / "vendor" / "mtkclient" / "mtkclient" / "payloads"
    assert (vendor_payloads / "da_x.bin").is_file()
    assert (vendor_payloads / "da_xml_64.bin").is_file()
    assert (vendor_payloads / "da_xml.bin").is_file()
    assert (vendor_payloads / "da_x.bin").stat().st_size > 10000

    exploit_dir = paths.BASE_DIR / "vendor" / "mtkclient" / "mtkclient" / "Library" / "Exploit" / "test"
    assert (exploit_dir / "DA_BR.bin").is_file()

    import src.flash_service as fs

    # Verify backend dispatch logic
    # On macOS, sp should redirect to mtk
    orig_mac = fs.IS_MAC
    orig_win = fs.IS_WINDOWS
    try:
        # Simulate macOS
        fs.IS_MAC = True
        w_mac = fs.FlashWorker("test.zip", method="sp")
        called = []
        w_mac._flash_via_mtkclient = lambda *a: called.append("mtk")
        w_mac._flash_via_sp_flash_tool = lambda *a: called.append("sp")
        w_mac._dispatch_backend(Path("/tmp"), Path("/tmp/scatter.txt"))
        assert called == ["mtk"]

        # Simulate Windows with auto mode
        fs.IS_MAC = False
        fs.IS_WINDOWS = True
        w_win = fs.FlashWorker("test.zip", method="auto")
        called = []
        w_win._flash_via_mtkclient = lambda *a: called.append("mtk")
        w_win._flash_via_sp_flash_tool = lambda *a: called.append("sp")
        # When SP Flash Tool is present:
        orig_find = paths.find_sp_flash_tool
        paths.find_sp_flash_tool = lambda: Path("/fake/flash_tool.exe")
        try:
            w_win._dispatch_backend(Path("/tmp"), Path("/tmp/scatter.txt"))
            assert called == ["sp"]
        finally:
            paths.find_sp_flash_tool = orig_find
    finally:
        fs.IS_MAC = orig_mac
        fs.IS_WINDOWS = orig_win


def test_release_version_parsing_and_sorting():
    """Verify that version tags are parsed according to legacy conventions and
    releases are sorted chronologically from newest to oldest."""
    from src import catalog
    from datetime import datetime
    import re

    # 1. Tag timestamp extraction
    assert catalog._extract_tag_timestamp("Solar 20260630-0550") == 202606300550
    assert catalog._extract_tag_timestamp("20260819-0956") == 202608190956
    assert catalog._extract_tag_timestamp("Latest-3.1.2") == 0

    # 2. Semver parsing
    assert catalog._parse_semver("3.1.2") == (3, 1, 2)
    assert catalog._parse_semver("0.9.0") == (0, 9, 0)
    assert catalog._parse_semver("invalid") is None

    # 3. Version designations
    v1 = catalog.parse_version_designations("Latest-3.1.2")
    assert v1["clean_version"] == "3.1.2"

    v2 = catalog.parse_version_designations("Stable-v0.3-ipod-theme-compatible")
    assert v2["clean_version"] == "0.3"
    assert "iPod Classic/Video Rockbox Theme Compatible" in v2["designations"]

    v3 = catalog.parse_version_designations("ADB-2.1.9")
    assert v3["clean_version"] == "2.1.9"
    assert "ADB" in v3["designations"]

    v4 = catalog.parse_version_designations("type-b-1.7.6-13057e75dc29a1a7!")
    assert v4["clean_version"] == "1.7.6"

    v5 = catalog.parse_version_designations("3.2.0-fm")
    assert v5["clean_version"] == "3.2.0"
    assert "FM" in v5["designations"]

    # Test label formatting suppressing redundant designation
    r_fm = {
        "tag_name": "3.2.0-fm",
        "name": "System Software 3.2.0 for Innioasis Y2 with FM Radio",
        "published_at": "2026-09-13T11:00:00Z",
    }
    assert catalog.format_release_display_label(r_fm) == "System Software 3.2.0 for Innioasis Y2 with FM Radio"

    # 4. Datestamp formatting
    m = re.search(r'(\d{8})-(\d{4})\b', "20260819-0956")
    now_same_day = datetime(2026, 8, 19, 12, 0)
    assert catalog.format_datestamp_version(m, now_dt=now_same_day) == "Today at 09:56"

    now_next_day = datetime(2026, 8, 20, 12, 0)
    assert catalog.format_datestamp_version(m, now_dt=now_next_day) == "Yesterday at 09:56"

    # 5. Full sorting: newest releases must appear first
    releases = [
        {"tag_name": "3.0.2", "published_at": "2025-12-12T15:15:38Z"},
        {"tag_name": "y2-base", "published_at": "2026-07-26T17:43:35Z"},
        {"tag_name": "3.0.7", "published_at": "2026-04-23T09:52:31Z"},
        {"tag_name": "20260819-0956", "published_at": "2026-08-19T10:06:38Z"},
        {"tag_name": "3.2.1", "published_at": "2026-09-13T12:00:00Z"},
        {"tag_name": "3.2.0-fm", "published_at": "2026-09-13T11:00:00Z"},
        {"tag_name": "3.1.7", "published_at": "2026-08-01T10:00:00Z"},
        {"tag_name": "Latest-3.1.2", "published_at": "2026-07-16T02:03:48Z"},
        {"tag_name": "type-b-1.7.6", "published_at": "2025-10-08T00:22:11Z"},
        {"tag_name": "ADB-2.1.9", "published_at": "2025-07-18T23:09:12Z"},
    ]
    sorted_rels = sorted(releases, key=catalog.release_sort_key, reverse=True)
    tags = [r["tag_name"] for r in sorted_rels]
    assert tags == [
        "20260819-0956",
        "3.2.1",
        "3.2.0-fm",
        "3.1.7",
        "Latest-3.1.2",
        "3.0.7",
        "3.0.2",
        "ADB-2.1.9",
        "type-b-1.7.6",
        "y2-base",
    ], f"Incorrect release sort order: {tags}"


def test_install_power_on_steps():
    """Verify device-specific post-install power-on instructions."""
    from src.config import install_power_on_steps

    y1_steps = install_power_on_steps("Y1")
    assert "Unplug your Y1" in y1_steps
    assert "centre button" in y1_steps

    y2_steps = install_power_on_steps("Y2")
    assert "Unplug your Y2" in y2_steps
    assert "power/lock button" in y2_steps

    generic_steps = install_power_on_steps("")
    assert "Unplug your Y1" in generic_steps
    assert "centre button" in generic_steps

    custom_steps = install_power_on_steps("CustomPlayer")
    assert "Unplug your CustomPlayer" in custom_steps
    assert "centre button" in custom_steps


def test_donation_dialog_install_completion():
    """Verify DonationDialog renders installation success banner and post-install steps
    when opened with context='install_success'."""
    from PySide6.QtWidgets import QApplication
    from src.donation_dialog import DonationDialog
    import src.donation_dialog as dd

    app = QApplication.instance() or QApplication(sys.argv)
    orig_fetch = dd.fetch_remote_donors_async
    dd.fetch_remote_donors_async = lambda cb: None
    try:
        dlg = DonationDialog(
            context="install_success",
            model="Y1",
            software_name="Rockbox (Y1)",
            donations=[],
        )
        text_content = []
        for child in dlg.findChildren(object):
            if hasattr(child, "text") and callable(child.text):
                text_content.append(child.text())
        joined = " ".join(text_content)
        assert "We've installed" in joined
        assert "Rockbox (Y1)" in joined
        assert "Unplug your Y1" in joined
        assert "centre button" in joined
        dlg.close()

        dlg_gen = DonationDialog(
            context="general",
            model="Y1",
            software_name="Rockbox (Y1)",
            donations=[],
        )
        text_gen = []
        for child in dlg_gen.findChildren(object):
            if hasattr(child, "text") and callable(child.text):
                text_gen.append(child.text())
        joined_gen = " ".join(text_gen)
        assert "We've installed" not in joined_gen
        dlg_gen.close()
    finally:
        dd.fetch_remote_donors_async = orig_fetch
    app.processEvents()


def test_flash_service_action_changed():
    """Verify FlashService forwards action_changed and FlashPage updates action label."""
    from PySide6.QtWidgets import QApplication
    from src.flash_service import FlashService, FlashWorker
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    service = FlashService()
    assert hasattr(service, "action_changed")

    page = FlashPage()
    service.action_changed.connect(page.update_action)

    worker = FlashWorker("dummy.zip", method="mtk")
    assert hasattr(worker, "action_changed")
    worker.action_changed.connect(service.action_changed)

    worker.action_changed.emit("Writing system (1/5): system.img")
    assert page._action_label.text() == "Writing system (1/5): system.img"

    worker.action_changed.emit("Flash complete! Disconnect USB and reboot.")
    assert page._action_label.text() == "Flash complete! Disconnect USB and reboot."

    worker.action_changed.disconnect(service.action_changed)
    page.deleteLater()
    app.processEvents()


def test_sp_flash_tool_gui():
    """Verify SP Flash Tool GUI support detection, history.ini scatter pinning,
    and UI button presence on supported platforms."""
    from PySide6.QtWidgets import QApplication
    from src import sp_flash_gui
    from src import paths
    from src.ui.main_window import MainWindow
    from src.ui.flash_page import FlashPage

    # 1. Platform support detection
    orig_mac = paths.IS_MAC
    orig_win = paths.IS_WINDOWS
    try:
        paths.IS_MAC = True
        assert sp_flash_gui.is_sp_flash_gui_supported() is False
        ok, msg = sp_flash_gui.launch_sp_flash_tool_gui()
        assert ok is False
        assert "not available on macOS" in msg

        paths.IS_MAC = False
        paths.IS_WINDOWS = True
        assert sp_flash_gui.is_sp_flash_gui_supported() is True
    finally:
        paths.IS_MAC = orig_mac
        paths.IS_WINDOWS = orig_win

    # 2. History.ini generation and update
    with tempfile.TemporaryDirectory() as td:
        sp_dir = Path(td)
        assert sp_flash_gui.update_sp_history_ini(sp_dir, model="Y1") is True
        ini_file = sp_dir / "history.ini"
        assert ini_file.is_file()
        content = ini_file.read_text(encoding="utf-8")
        exp_y1 = str((sp_dir / "MT6572_Android_scatter.txt").resolve())
        assert f"scatterHistory={exp_y1}" in content
        assert f"lastDir={exp_y1}" in content
        assert os.path.isabs(exp_y1)

        # Update for Y2
        assert sp_flash_gui.update_sp_history_ini(sp_dir, model="Y2") is True
        content2 = ini_file.read_text(encoding="utf-8")
        exp_y2 = str((sp_dir / "MT6582_Android_scatter.txt").resolve())
        assert f"scatterHistory={exp_y2},{exp_y1}" in content2
        assert f"lastDir={exp_y2}" in content2
        assert os.path.isabs(exp_y2)

    # 3. UI presence
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    if sp_flash_gui.is_sp_flash_gui_supported():
        assert hasattr(w, "_sp_flash_tool_btn")
        assert hasattr(w._flash_page, "_open_sp_gui_btn")
        assert w._sp_flash_tool_btn.text() != ""
        assert w._flash_page._open_sp_gui_btn.text() != ""
    w.close()
    app.processEvents()


def test_open_browser_focused_new_window():
    """Verify open_browser opens URLs in a new focused window and handles all platforms."""
    from unittest.mock import patch, MagicMock
    from src import browser
    from src.ui.main_window import MainWindow

    # Empty URL rejection
    assert browser.open_browser("") is False
    assert browser.open_browser(None) is False

    # Linux direct browser invocation with --new-window
    calls = []
    def fake_popen(cmd, *a, **kw):
        calls.append(cmd)
        mock = MagicMock()
        return mock

    with patch("subprocess.Popen", side_effect=fake_popen):
        with patch.object(browser, "_find_linux_browser", return_value="/usr/bin/firefox"):
            with patch("platform.system", return_value="Linux"):
                ok = browser.open_browser("https://innioasis.app/credits.html?thank-you=1")
                assert ok is True
                assert len(calls) == 1
                assert calls[0] == ["/usr/bin/firefox", "--new-window", "https://innioasis.app/credits.html?thank-you=1"]

    # Fallback to QDesktopServices
    calls.clear()
    with patch("subprocess.Popen", side_effect=fake_popen):
        with patch.object(browser, "_find_linux_browser", return_value=None):
            with patch("platform.system", return_value="Linux"):
                with patch("PySide6.QtGui.QDesktopServices.openUrl", return_value=True) as mock_qds:
                    ok = browser.open_browser("https://innioasis.app/credits.html?thank-you=1")
                    assert ok is True
                    assert mock_qds.called

    # MainWindow._open_credits calls open_browser
    credits_urls = []
    with patch("src.browser.open_browser", side_effect=lambda u, **k: credits_urls.append(u)):
        w = MainWindow()
        w._open_credits()
        assert credits_urls == ["https://innioasis.app/credits.html?thank-you=1"]
        w.close()


def test_device_tracking():
    """Verify recording and querying installs, reminders, and donation preferences."""
    from PySide6.QtCore import QSettings
    from src import device_tracking

    with tempfile.TemporaryDirectory() as td:
        settings = QSettings(f"{td}/test_settings.ini", QSettings.IniFormat)

        # 1. Recording installs
        device_tracking.record_device_install(
            "Y1", "Original Software", "3.1.2",
            release_label="System 3.1.2", package_slug="original-y1",
            settings=settings,
        )
        device_tracking.record_device_install(
            "Y2", "Original Software", "3.1.7",
            release_label="Original System Software 3.1.7 for Innioasis Y2", package_slug="original-y2",
            settings=settings,
        )

        y1_rec = device_tracking.get_device_install("Y1", settings=settings)
        assert y1_rec is not None
        assert y1_rec["tag_name"] == "3.1.2"
        assert y1_rec["package_slug"] == "original-y1"

        y2_rec = device_tracking.get_device_install("Y2", settings=settings)
        assert y2_rec is not None
        assert y2_rec["tag_name"] == "3.1.7"
        assert y2_rec["release_label"] == "Original System Software 3.1.7 for Innioasis Y2"

        all_installs = device_tracking.get_all_device_installs(settings=settings)
        assert "Y1" in all_installs and "Y2" in all_installs

        # 2. Clearing installs
        device_tracking.clear_device_install("Y1", settings=settings)
        assert device_tracking.get_device_install("Y1", settings=settings) is None
        assert device_tracking.get_device_install("Y2", settings=settings) is not None

        # 3. Reminder preferences
        assert device_tracking.is_device_reminder_enabled("Y1", settings=settings) is True
        device_tracking.set_device_reminder_enabled("Y1", False, settings=settings)
        assert device_tracking.is_device_reminder_enabled("Y1", settings=settings) is False
        device_tracking.set_device_reminder_enabled("Y1", True, settings=settings)
        assert device_tracking.is_device_reminder_enabled("Y1", settings=settings) is True

        # 4. Donation preferences
        assert device_tracking.is_donation_ui_disabled(settings=settings) is False
        device_tracking.set_donation_ui_disabled(True, settings=settings)
        assert device_tracking.is_donation_ui_disabled(settings=settings) is True
        assert device_tracking.is_donation_install_prompt_disabled(settings=settings) is True

        device_tracking.set_donation_ui_disabled(False, settings=settings)
        assert device_tracking.is_donation_ui_disabled(settings=settings) is False
        device_tracking.set_donation_install_prompt_disabled(True, settings=settings)
        assert device_tracking.is_donation_install_prompt_disabled(settings=settings) is True


def test_check_device_updates():
    """Verify release updates detection for tracked devices."""
    from PySide6.QtCore import QSettings
    from src import device_tracking, catalog

    with tempfile.TemporaryDirectory() as td:
        settings = QSettings(f"{td}/test_settings.ini", QSettings.IniFormat)
        device_tracking.record_device_install("Y2", "Original Software", "3.1.7", settings=settings)

        # Mock releases client returning 3.2.1 and 3.1.7
        mock_client = catalog.ReleasesClient(cache_root=td)
        mock_releases = [
            {"tag_name": "3.2.1", "name": "System Software 3.2.1 for Innioasis Y2", "rom_variants": [{"asset": {"browser_download_url": "u", "name": "rom_y2.zip"}}]},
            {"tag_name": "3.1.7", "name": "Original System Software 3.1.7 for Innioasis Y2", "rom_variants": [{"asset": {"browser_download_url": "u", "name": "rom_y2.zip"}}]},
        ]
        mock_client.releases_for_package = lambda pkg, model, show_nightly=False: mock_releases

        # Check updates: 3.2.1 > 3.1.7
        updates = device_tracking.check_device_updates(releases_client=mock_client, settings=settings)
        assert len(updates) == 1
        assert updates[0]["model"] == "Y2"
        assert updates[0]["latest_tag"] == "3.2.1"
        assert updates[0]["installed_tag"] == "3.1.7"

        # After saving last_notified_tag, should suppress duplicate alert
        device_tracking.set_last_notified_tag("Y2", "3.2.1", settings=settings)
        assert len(device_tracking.check_device_updates(releases_client=mock_client, settings=settings)) == 0

        # Manual check ignores last_notified_tag
        assert len(device_tracking.check_device_updates(releases_client=mock_client, settings=settings, ignore_last_notified=True)) == 1

        # Opting out of Y2 reminders disables check
        device_tracking.set_device_reminder_enabled("Y2", False, settings=settings)
        assert len(device_tracking.check_device_updates(releases_client=mock_client, settings=settings, ignore_last_notified=True)) == 0


def test_settings_page_and_dialogs():
    """Verify SettingsPage and ReleaseReminderDialog interaction."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from src.ui.settings_page import SettingsPage
    from src.ui.dialogs import ReleaseReminderDialog
    from src.ui.main_window import MainWindow, _PAGE_SETTINGS
    from src import device_tracking

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as td:
        settings = QSettings(f"{td}/test_settings.ini", QSettings.IniFormat)
        device_tracking.record_device_install("Y1", "Original Software", "3.1.2", settings=settings)

        sp = SettingsPage()
        sp.refresh_settings()

        # Test donation visibility toggle
        emitted_vis = []
        sp.donation_visibility_changed.connect(lambda v: emitted_vis.append(v))
        sp._cb_hide_donations.setChecked(True)
        assert emitted_vis == [True]
        assert device_tracking.is_donation_ui_disabled() is True

        # Test ReleaseReminderDialog with opt-out
        disabled_models = []
        viewed_releases = []
        upd = {
            "model": "Y2",
            "software_name": "Original Software",
            "installed_tag": "3.1.7",
            "latest_tag": "3.2.1",
        }
        dlg = ReleaseReminderDialog(
            update_info=upd,
            on_view_release=lambda u: viewed_releases.append(u),
            on_disable_reminders=lambda m: disabled_models.append(m),
        )
        dlg.cb_dont_remind.setChecked(True)
        dlg._on_view()
        assert disabled_models == ["Y2"]
        assert len(viewed_releases) == 1

        # Test MainWindow settings navigation and donation visibility
        mw = MainWindow()
        mw._nav_to_page(_PAGE_SETTINGS)
        assert mw._stack.currentIndex() == _PAGE_SETTINGS
        mw._apply_donation_visibility(is_disabled=True)
        assert mw.statusBar().isHidden() is True
        assert mw._support_btn.isHidden() is True
        mw._apply_donation_visibility(is_disabled=False)
        assert mw.statusBar().isHidden() is False
        assert mw._support_btn.isHidden() is False
        mw.close()


def test_firmware_release_reminder_install_flow():
    """Verify single software tracking per model and start-install prompt flow."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from src import device_tracking, catalog
    from src.ui.dialogs import ReleaseReminderDialog
    from src.ui.select_page import SelectPackagePage

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    with tempfile.TemporaryDirectory() as td:
        settings = QSettings(f"{td}/test_settings.ini", QSettings.IniFormat)

        # 1. User installed Inniclassic 0.4 on Y1
        device_tracking.record_device_install(
            "Y1",
            "Inniclassic",
            "0.4",
            release_label="Inniclassic 0.4",
            package_slug="inniclassic",
            settings=settings,
        )

        # Verify only one software is tracked for Y1
        rec = device_tracking.get_device_install("Y1", settings=settings)
        assert rec is not None
        assert rec["software_name"] == "Inniclassic"
        assert rec["tag_name"] == "0.4"
        # Y2 and A5 have no recorded install
        assert device_tracking.get_device_install("Y2", settings=settings) is None
        assert device_tracking.get_device_install("A5", settings=settings) is None

        # 2. Inniclassic 1.0 is released
        mock_client = catalog.ReleasesClient(cache_root=td)
        mock_releases = [
            {
                "tag_name": "1.0",
                "name": "Inniclassic 1.0 for Innioasis Y1",
                "download_url": "https://example.com/inniclassic_1.0_rom.zip",
                "asset_name": "rom.zip",
                "rom_variants": [{"asset": {"browser_download_url": "https://example.com/inniclassic_1.0_rom.zip", "name": "rom.zip"}}],
            },
            {
                "tag_name": "0.4",
                "name": "Inniclassic 0.4 for Innioasis Y1",
                "download_url": "https://example.com/inniclassic_0.4_rom.zip",
                "asset_name": "rom.zip",
                "rom_variants": [{"asset": {"browser_download_url": "https://example.com/inniclassic_0.4_rom.zip", "name": "rom.zip"}}],
            },
        ]
        mock_client.releases_for_package = lambda pkg, model, show_nightly=False: mock_releases

        # Check updates finds Inniclassic 1.0 > 0.4
        updates = device_tracking.check_device_updates(releases_client=mock_client, settings=settings)
        assert len(updates) == 1
        upd = updates[0]
        assert upd["model"] == "Y1"
        assert upd["software_name"].lower() == "inniclassic"
        assert upd["installed_tag"] == "0.4"
        assert upd["latest_tag"] == "1.0"

        # 3. Popup dialog asks user if they would like to start an install with selected method
        started_installs = []
        dlg = ReleaseReminderDialog(
            update_info=upd,
            on_start_install=lambda info: started_installs.append(info),
            flash_method="sp",
        )
        # Check that button text is Start Install
        assert dlg._btn_install.text() == "Start Install"
        # Trigger start install
        dlg._on_start_install()
        assert len(started_installs) == 1
        assert started_installs[0]["latest_tag"] == "1.0"

        # 4. Verify SelectPackagePage handles start_install_for_release
        sel = SelectPackagePage()
        sel.start_install_for_release(
            model=upd["model"],
            software_name=upd["software_name"],
            tag_name=upd["latest_tag"],
            release=upd["latest_release"],
        )
        assert sel.current_model() == "Y1"
        assert sel.current_software().lower() == "inniclassic"
        if getattr(sel, "_download_worker", None) is not None:
            sel._download_worker.cancel()
            sel._download_worker.wait(500)


def test_latest_package_tracking_and_history_ini():
    """Verify recording latest package and generating prepopulated history.ini."""
    from PySide6.QtCore import QSettings
    from src import device_tracking, sp_flash_gui

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        settings = QSettings(f"{td}/test_settings.ini", QSettings.IniFormat)

        # 1. Create simulated extracted firmware folder with scatter and images
        extract_dir = tdp / "firmware_extracted"
        extract_dir.mkdir()
        scatter = extract_dir / "MT6582_Android_scatter.txt"
        scatter.write_text("platform: MT6582\npartition_name: boot\nfile_name: boot.img\n")
        boot_img = extract_dir / "boot.img"
        boot_img.write_bytes(b"dummy boot")

        # 2. Record latest package
        device_tracking.record_latest_package(
            model="Y2",
            software_name="Original Software",
            tag_name="3.2.0-fm",
            package_path=str(tdp / "firmware.zip"),
            extract_dir=str(extract_dir),
            scatter_path=str(scatter),
            settings=settings,
        )

        rec = device_tracking.get_latest_package(settings=settings)
        assert rec is not None
        assert rec["model"] == "Y2"
        assert rec["software_name"] == "Original Software"
        assert rec["tag_name"] == "3.2.0-fm"
        assert rec["scatter_path"] == str(scatter)
        assert rec["extract_dir"] == str(extract_dir)

        # 3. Test SP Flash Tool history.ini creation
        sp_bin_dir = tdp / "sp_flash_tool"
        sp_bin_dir.mkdir()
        ok = sp_flash_gui.update_sp_history_ini(
            sp_dir=sp_bin_dir,
            scatter_path=scatter,
            extract_dir=extract_dir,
            model="Y2",
        )
        assert ok is True
        hist_ini = sp_bin_dir / "history.ini"
        assert hist_ini.is_file()
        text = hist_ini.read_text(encoding="utf-8")
        assert f"scatterHistory={scatter.resolve()}" in text
        assert f"lastDir={extract_dir.resolve()}" in text
        # Verify scatter file was copied to sp_bin_dir
        assert (sp_bin_dir / "MT6582_Android_scatter.txt").is_file()

        # 4. Clear latest package
        device_tracking.clear_latest_package(settings=settings)
        assert device_tracking.get_latest_package(settings=settings) is None


def test_prune_extracted_cache_and_reusing_download():
    """Verify only the most recently downloaded package remains extracted in cache,
    and selecting the same release reuses the cached extraction without redownload."""
    from src.flash_service import prune_extracted_cache, EXTRACT_COMPLETE_MARKER
    from src import downloads

    with tempfile.TemporaryDirectory() as td:
        orig_downloads_dir = downloads.downloads_dir
        tdp = Path(td)
        downloads.downloads_dir = lambda: tdp

        try:
            # Create two extracted folders
            old_pkg = tdp / "pkg1.zip"
            old_pkg.write_bytes(b"1")
            old_extract = tdp / ".pkg1_extracted"
            old_extract.mkdir()
            (old_extract / EXTRACT_COMPLETE_MARKER).write_text("ok")

            new_pkg = tdp / "pkg2.zip"
            new_pkg.write_bytes(b"2")
            new_extract = tdp / ".pkg2_extracted"
            new_extract.mkdir()
            (new_extract / EXTRACT_COMPLETE_MARKER).write_text("ok")

            # Prune cache keeping new_pkg
            removed = prune_extracted_cache(keep_package_path=str(new_pkg))
            assert str(old_extract) in removed
            assert not old_extract.exists()
            assert new_extract.exists()
            assert (new_extract / EXTRACT_COMPLETE_MARKER).is_file()
        finally:
            downloads.downloads_dir = orig_downloads_dir


def test_sp_flash_system_checker_and_diagnostics():
    """Verify run_system_checker, option.ini normalization, TtyAccessGuardian, and Settings checker card."""
    from src import linux_sp_flash as lsf
    from src.ui.dialogs import SystemCheckerDialog, LinuxSetupDialog
    from src.ui.settings_page import SettingsPage
    from PySide6.QtWidgets import QApplication

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    # 1. System checker returns structured report
    report = lsf.run_system_checker()
    assert "items" in report
    assert len(report["items"]) >= 10
    keys = {item["key"] for item in report["items"]}
    assert "os_arch" in keys
    assert "engine_files" in keys
    assert "libpng12" in keys
    assert "udev_rules" in keys
    assert "user_groups" in keys
    assert "service_conflicts" in keys
    assert "kernel_driver" in keys
    assert "mount_permissions" in keys
    assert "option_ini" in keys
    assert "connected_device" in keys
    assert "cdc_acm_ok" in report
    assert "mount_ok" in report

    # 2. Companion ttyACMs rule content
    tty_rules = lsf.generate_ttyacms_rule_content()
    assert "ttyACM" in tty_rules
    assert "0666" in tty_rules
    assert "RUN+=" in tty_rules

    # 3. Option.ini fixing
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        opt = tdp / "option.ini"
        opt.write_text("[Trace]\nLogPath=C:\\ProgramData\\SP_FT_Logs\nEnable=1\n")
        fixed = lsf.fix_option_ini(stage=tdp)
        assert fixed is True
        content = opt.read_text()
        assert "C:\\ProgramData" not in content
        assert str(tdp / lsf.LOG_DIR_NAME) in content

    # 4. TtyAccessGuardian
    guardian = lsf.TtyAccessGuardian(interval=0.01)
    guardian.start()
    assert guardian.is_alive()
    guardian.stop()
    guardian.join(timeout=1.0)
    assert not guardian.is_alive()

    # 5. SystemCheckerDialog alias and controls
    assert SystemCheckerDialog is LinuxSetupDialog
    dlg = SystemCheckerDialog()
    assert dlg._sp_gui_btn is not None
    assert dlg._sp_gui_btn.text() != ""

    # 6. SettingsPage card and buttons
    settings_page = SettingsPage()
    assert hasattr(settings_page, "_btn_run_checker")
    assert hasattr(settings_page, "_btn_launch_sp")
    assert settings_page._btn_run_checker.text() != ""
def test_sp_history_ini_subsequent_attempts_and_absolute_paths():
    """Verify that update_sp_history_ini always writes valid absolute paths
    across subsequent install attempts, retries, and fixes any legacy relative paths."""
    from src import sp_flash_gui, device_tracking
    from PySide6.QtCore import QSettings

    with tempfile.TemporaryDirectory() as td:
        sp_dir = Path(td) / "sp_tool"
        sp_dir.mkdir(parents=True)
        da_file = sp_dir / "MTK_AllInOne_DA.bin"
        da_file.write_bytes(b"DA_BIN")

        # Fake packages and scatter files
        fw1_dir = Path(td) / "fw1_extracted"
        fw1_dir.mkdir(parents=True)
        sc1 = fw1_dir / "MT6572_Android_scatter.txt"
        sc1.write_text("platform: MT6572\n", encoding="utf-8")

        fw2_dir = Path(td) / "fw2_extracted"
        fw2_dir.mkdir(parents=True)
        sc2 = fw2_dir / "MT6582_Android_scatter.txt"
        sc2.write_text("platform: MT6582\n", encoding="utf-8")

        # --- Attempt 1: First install attempt (Y1) ---
        ok1 = sp_flash_gui.update_sp_history_ini(
            sp_dir=sp_dir,
            scatter_path=sc1,
            extract_dir=fw1_dir,
            model="Y1",
        )
        assert ok1 is True
        ini_file = sp_dir / "history.ini"
        assert ini_file.is_file()

        # Check with QSettings
        qs1 = QSettings(str(ini_file), QSettings.IniFormat)
        assert qs1.value("LastDAFilePath/lastDir") == str(da_file.resolve())
        assert qs1.value("RecentOpenFile/lastDir") == str(sc1.resolve())
        assert qs1.value("RecentOpenFile/scatterHistory") == str(sc1.resolve())
        assert os.path.isabs(qs1.value("LastDAFilePath/lastDir"))
        assert os.path.isabs(qs1.value("RecentOpenFile/lastDir"))

        # --- Attempt 2: Subsequent install attempt (Y2) ---
        ok2 = sp_flash_gui.update_sp_history_ini(
            sp_dir=sp_dir,
            scatter_path=sc2,
            extract_dir=fw2_dir,
            model="Y2",
        )
        assert ok2 is True

        qs2 = QSettings(str(ini_file), QSettings.IniFormat)
        assert qs2.value("LastDAFilePath/lastDir") == str(da_file.resolve())
        assert qs2.value("RecentOpenFile/lastDir") == str(sc2.resolve())
        expected_hist = f"{sc2.resolve()},{sc1.resolve()}"
        assert f"scatterHistory={expected_hist}" in ini_file.read_text(encoding="utf-8")
        raw_val = qs2.value("RecentOpenFile/scatterHistory")
        items = raw_val if isinstance(raw_val, list) else [raw_val]
        assert items == [str(sc2.resolve()), str(sc1.resolve())]
        assert os.path.isabs(qs2.value("RecentOpenFile/lastDir"))

        # --- Test upgrade of legacy/malformed history.ini with relative paths ---
        malformed_text = (
            "[LastDAFilePath]\n"
            "lastDir=MTK_AllInOne_DA.bin\n\n"
            "[RecentOpenFile]lastDir=\n"
            "scatterHistory=MT6572_Android_scatter.txt\n"
            "authHistory=\n"
        )
        ini_file.write_text(malformed_text, encoding="utf-8")

        ok3 = sp_flash_gui.update_sp_history_ini(
            sp_dir=sp_dir,
            model="Y2",
        )
        assert ok3 is True
        qs3 = QSettings(str(ini_file), QSettings.IniFormat)
        assert qs3.value("LastDAFilePath/lastDir") == str(da_file.resolve())
        assert os.path.isabs(qs3.value("LastDAFilePath/lastDir"))
        assert os.path.isabs(qs3.value("RecentOpenFile/lastDir"))
        raw3 = qs3.value("RecentOpenFile/scatterHistory")
        items3 = raw3 if isinstance(raw3, list) else [raw3]
        for item in items3:
            item_str = str(item).strip()
            assert os.path.isabs(item_str), f"Expected absolute path, got {item_str}"


def test_model_detection_and_install_guidance():
    from src.config import (
        detect_model_and_type_from_name,
        device_label_for_model,
        install_disconnect_guidance,
        install_power_on_steps,
        DEVICE_MODELS,
    )
    from src import catalog, manifest
    from src.ui.settings_page import SettingsPage
    from src.ui.select_page import SelectPackagePage

    # 1. Device models list
    assert "A5" in DEVICE_MODELS
    assert "Y1" in DEVICE_MODELS
    assert "Y2" in DEVICE_MODELS

    # 2. Model & type detection from filenames & URLs
    m1, t1 = detect_model_and_type_from_name("rom_a5.zip")
    assert m1 == "A5" and t1 is None

    m2, t2 = detect_model_and_type_from_name("https://github.com/y1-community/stock/releases/download/v1.0/rom_a5.zip")
    assert m2 == "A5" and t2 is None

    m3, t3 = detect_model_and_type_from_name("rom_y2.zip")
    assert m3 == "Y2" and t3 is None

    m4, t4 = detect_model_and_type_from_name("https://github.com/y1-community/rockbox/releases/download/v1.0/rom_y2.zip")
    assert m4 == "Y2" and t4 is None

    m5, t5 = detect_model_and_type_from_name("rom_type_b.zip")
    assert m5 == "Y1" and t5 == "B"

    m6, t6 = detect_model_and_type_from_name("rom_type_a.zip")
    assert m6 == "Y1" and t6 == "A"

    m7, t7 = detect_model_and_type_from_name("rom.zip")
    assert m7 == "Y1" and t7 == "A"

    # 3. Device label formatting
    assert "A5" in device_label_for_model("A5")
    assert "Y2" in device_label_for_model("Y2")
    assert "Type B" in device_label_for_model("Y1", "B")
    assert "Type A" in device_label_for_model("Y1", "A")

    # 4. Disconnect & paperclip guidance
    guide_y1 = install_disconnect_guidance("Y1", "B")
    assert "paperclip" in guide_y1.lower() or "pin" in guide_y1.lower()
    assert "Type B" in guide_y1

    guide_a5 = install_disconnect_guidance("A5")
    assert "A5" in guide_a5
    assert "paperclip" in guide_a5.lower() or "pin" in guide_a5.lower()

    guide_y2 = install_disconnect_guidance("Y2")
    assert "Y2" in guide_y2
    assert "paperclip" in guide_y2.lower() or "pin" in guide_y2.lower()

    # 5. Catalog A5 packages
    a5_pkgs = catalog.packages_for_model("A5")
    assert len(a5_pkgs) > 0, "Expected A5 packages in catalog"

    # 6. Manifest parsing A5
    sample_xml = '''<slidia_manifest>
        <package name="Test A5 Firmware" repo="y1-community/a5-test" device="A5" type="img" handler="Custom Firmware" />
    </slidia_manifest>'''
    parsed = manifest.parse_manifest_xml(sample_xml)
    assert len(parsed) == 1
    assert parsed[0].model == "A5"
    assert parsed[0].package_name == "rom_a5.zip"

    # 7. UI components have A5 support
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    settings_page = SettingsPage()
    # One opt-in covers every tracked device, plus the install-method selector.
    assert hasattr(settings_page, "_cb_reminders")
    assert settings_page._method_combo.count() >= 1

    select_page = SelectPackagePage()
    assert select_page._prompt_pre_install("A5") is True  # Non-blocking offscreen


def test_android_sparse_handling():
    import struct
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        raw_dummy = tdp / "raw.img"
        raw_dummy.write_bytes(b"HELLO_RAW_DATA_1234567890")
        assert not _is_android_sparse(raw_dummy)

        sparse_file = tdp / "test_sparse.img"
        hdr = struct.pack("<I4H4I", 0xED26FF3A, 1, 0, 28, 12, 4096, 4, 3, 0)
        chunk1_hdr = struct.pack("<2H2I", 0xCAC1, 0, 1, 12 + 4096)
        chunk1_data = b"A" * 4096
        chunk2_hdr = struct.pack("<2H2I", 0xCAC2, 0, 2, 12 + 4)
        chunk2_fill = b"\xEF\xBE\xAD\xDE"
        chunk3_hdr = struct.pack("<2H2I", 0xCAC3, 0, 1, 12)

        sparse_file.write_bytes(hdr + chunk1_hdr + chunk1_data + chunk2_hdr + chunk2_fill + chunk3_hdr)
        assert _is_android_sparse(sparse_file)

        out_raw, regions = _convert_sparse_to_raw(sparse_file, tdp)
        assert out_raw.exists()
        raw_bytes = out_raw.read_bytes()
        assert len(raw_bytes) == 4 * 4096

        assert raw_bytes[:4096] == b"A" * 4096
        expected_fill = b"\xEF\xBE\xAD\xDE" * (2 * 4096 // 4)
        assert raw_bytes[4096:3 * 4096] == expected_fill
        assert raw_bytes[3 * 4096:] == b"\x00" * 4096


def test_signed_image_stripping():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        plain = tdp / "plain.bin"
        plain.write_bytes(b"PLAIN_CONTENT_NO_SIGNING" * 10)
        assert _signed_image_payload_range(plain, plain.stat().st_size) is None

        signed = tdp / "signed.bin"
        header = b"SSSS" + b"\x00" * 60
        payload = b"ACTUAL_BOOT_OR_RECOVERY_IMAGE_PAYLOAD_DATA" * 5
        footer = b"\x00" * 232 + b"EEEE"
        signed.write_bytes(header + payload + footer)
        total_sz = len(header) + len(payload) + len(footer)
        res = _signed_image_payload_range(signed, total_sz)
        assert res == (64, len(payload))

        hdr_only = tdp / "header_only.bin"
        hdr_only.write_bytes(header + payload)
        assert _signed_image_payload_range(hdr_only, len(header) + len(payload)) is None


def test_legacy_mbr_user_addr_bias():
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        scatter_mt6582 = tdp / "MT6582_scatter.txt"
        scatter_mt6582.write_text("""- platform: MT6582
  project: y2
  storage: EMMC
- partition_index: SYS0
  partition_name: PRELOADER
  file_name: preloader_y2.bin
  is_download: true
  type: SV5_BL_BIN
  linear_start_addr: 0x0
  physical_start_addr: 0x0
  partition_size: 0x40000
  region: EMMC_BOOT_1
- partition_index: SYS1
  partition_name: MBR
  file_name: MBR
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x1400000
  physical_start_addr: 0x0
  partition_size: 0x80000
  region: EMMC_USER
- partition_index: SYS2
  partition_name: EBR1
  file_name: EBR1
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x1480000
  physical_start_addr: 0x80000
  partition_size: 0x80000
  region: EMMC_USER
""")
        bias = _detect_legacy_user_addr_bias(scatter_mt6582)
        assert bias == LEGACY_MBR_USER_ADDR_BIAS
        assert LEGACY_MBR_USER_ADDR_BIAS == 0x800000
        assert LEGACY_SEGMENTED_WRITE_BYTES == 50 * 1024 * 1024

        scatter_mt6765 = tdp / "MT6765_scatter.txt"
        scatter_mt6765.write_text("""- platform: MT6765
  project: modern
  storage: EMMC
- partition_index: SYS0
  partition_name: boot
  file_name: boot.img
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x2000000
  physical_start_addr: 0x2000000
  partition_size: 0x2000000
  region: EMMC_USER
""")
        assert _detect_legacy_user_addr_bias(scatter_mt6765) == 0


def test_macos_universal_libusb():
    dylib_path = ROOT / "vendor" / "mtkclient" / "mtkclient" / "Darwin" / "libusb-1.0.dylib"
    assert dylib_path.exists(), f"libusb dylib missing at {dylib_path}"

    with open(dylib_path, "rb") as f:
        header = f.read(8)
    import struct
    magic = struct.unpack(">I", header[:4])[0]
    assert magic in (0xCAFEBABE, 0xBEBAFECA), f"Not a universal Mach-O binary: magic={hex(magic)}"
    nfat_arch = struct.unpack(">I", header[4:8])[0]
    assert nfat_arch >= 2, f"Expected at least 2 architectures, found {nfat_arch}"

    with open(dylib_path, "rb") as f:
        f.seek(8)
        cputypes = []
        for _ in range(nfat_arch):
            arch_data = f.read(20)
            cputype = struct.unpack(">i", arch_data[:4])[0]
            cputypes.append(cputype)

    CPU_TYPE_X86_64 = 0x01000007
    CPU_TYPE_ARM64 = 0x0100000C
    assert CPU_TYPE_X86_64 in cputypes, f"x86_64 slice missing in {dylib_path}: {cputypes}"
    assert CPU_TYPE_ARM64 in cputypes, f"arm64 slice missing in {dylib_path}: {cputypes}"


def test_macos_universal_app_bundle():
    app_dir = ROOT / "dist" / "Innioasis Updater CE.app"
    if not app_dir.exists():
        return

    import struct

    def _check_universal_slices(path: Path):
        assert path.exists(), f"Binary missing at {path}"
        with open(path, "rb") as f:
            header = f.read(8)
        magic = struct.unpack(">I", header[:4])[0]
        assert magic in (0xCAFEBABE, 0xBEBAFECA), f"{path.name} is not universal Mach-O: magic={hex(magic)}"
        nfat_arch = struct.unpack(">I", header[4:8])[0]
        assert nfat_arch >= 2, f"Expected at least 2 architectures in {path.name}, found {nfat_arch}"

        with open(path, "rb") as f:
            f.seek(8)
            cputypes = []
            for _ in range(nfat_arch):
                arch_data = f.read(20)
                cputype = struct.unpack(">i", arch_data[:4])[0]
                cputypes.append(cputype)

        CPU_TYPE_X86_64 = 0x01000007
        CPU_TYPE_ARM64 = 0x0100000C
        assert CPU_TYPE_X86_64 in cputypes, f"x86_64 slice missing in {path.name}: {cputypes}"
        assert CPU_TYPE_ARM64 in cputypes, f"arm64 slice missing in {path.name}: {cputypes}"

    # 1. Launcher executable
    exe_path = app_dir / "Contents" / "MacOS" / "Innioasis Updater CE"
    _check_universal_slices(exe_path)

    # 2. Bundled libusb-1.0.dylib
    libusb_path = app_dir / "Contents" / "Frameworks" / "libusb-1.0.dylib"
    _check_universal_slices(libusb_path)

    # 3. Bundled Python 3.11 runtime executable
    python_bin = app_dir / "Contents" / "Resources" / "python" / "bin" / "python3.11"
    _check_universal_slices(python_bin)

    # 4. Bundled PySide6 Cocoa platform plugin and Qt bindings
    site_packages = app_dir / "Contents" / "Resources" / "python" / "lib" / "python3.11" / "site-packages"
    cocoa_plugin = site_packages / "PySide6" / "Qt" / "plugins" / "platforms" / "libqcocoa.dylib"
    _check_universal_slices(cocoa_plugin)
    qtwidgets = site_packages / "PySide6" / "QtWidgets.abi3.so"
    _check_universal_slices(qtwidgets)

    # 5. Application source and assets
    app_code = app_dir / "Contents" / "Resources" / "app"
    assert (app_code / "launcher.py").exists(), "app/launcher.py missing"
    assert (app_code / "src" / "app.py").exists(), "app/src/app.py missing"
    assert (app_dir / "Contents" / "Resources" / "icon.icns").exists(), "icon.icns missing"

    # 6. No static libraries (which crash rcodesign)
    static_libs = list(app_dir.rglob("*.a"))
    assert len(static_libs) == 0, f"Found unexpected static libraries: {static_libs}"

    # 7. Self-contained bundle size verification (>= 200MB)
    total_size_mb = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / (1024 * 1024)
    assert total_size_mb >= 200.0, f"Bundle size {total_size_mb:.1f}MB is too small; missing standalone runtime or PySide6"

    # 8. Info.plist metadata
    info_plist_path = app_dir / "Contents" / "Info.plist"
    assert info_plist_path.exists(), "Info.plist missing"
    import plistlib
    with open(info_plist_path, "rb") as f:
        info = plistlib.load(f)
    assert info.get("LSMinimumSystemVersion") == "13.0"
    assert info.get("CFBundleExecutable") == "Innioasis Updater CE"


def test_tools_manager_and_self_healing():
    from src import tools_manager

    # 1. Manifest definitions
    assert "sp_flash_tool_win" in tools_manager.TOOLS_MANIFEST
    assert "sp_flash_tool_linux" in tools_manager.TOOLS_MANIFEST
    assert "libusb_darwin" in tools_manager.TOOLS_MANIFEST
    assert "unrar_win" in tools_manager.TOOLS_MANIFEST

    user_tools = tools_manager.get_user_tools_dir()
    assert isinstance(user_tools, Path)

    # 2. Check integrity returns boolean and status string
    ok, msg = tools_manager.verify_component_integrity("sp_flash_tool_win")
    assert isinstance(ok, bool)
    assert isinstance(msg, str)

    # 3. Test self-healing extraction with synthetic zip
    import tempfile
    import zipfile
    import io
    import urllib.request

    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        orig_user_tools = tools_manager.get_user_tools_dir
        tools_manager.get_user_tools_dir = lambda: tdp / "tools"

        try:
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w") as zf:
                zf.writestr("flash_tool", b"fake-elf-binary")
                zf.writestr("libflashtool.so", b"fake-so")
                zf.writestr("MTK_AllInOne_DA.bin", b"fake-da")

            zip_bytes = zip_buf.getvalue()

            class MockResponse:
                def __init__(self, data):
                    self.data = data
                    self.fp = io.BytesIO(data)

                def read(self, amt=65536):
                    return self.fp.read(amt)

                def getheader(self, name):
                    if name.lower() == "content-length":
                        return str(len(self.data))
                    return None

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            orig_urlopen = urllib.request.urlopen
            urllib.request.urlopen = lambda req, timeout=30: MockResponse(zip_bytes)

            try:
                progress_events = []

                def on_progress(cur, total, msg):
                    progress_events.append((cur, total, msg))

                ok, msg, comp_dir = tools_manager.self_heal_component(
                    "sp_flash_tool_linux", progress_cb=on_progress, force=True
                )
                assert ok is True, f"self_heal_component failed: {msg}"
                assert comp_dir is not None
                assert (comp_dir / "flash_tool").exists()
                assert len(progress_events) > 0

                ok_int, msg_int = tools_manager.verify_component_integrity("sp_flash_tool_linux")
                assert ok_int is True, f"Integrity check failed: {msg_int}"

                found = tools_manager.find_component("sp_flash_tool_linux")
                assert found == comp_dir
            finally:
                urllib.request.urlopen = orig_urlopen
        finally:
            tools_manager.get_user_tools_dir = orig_user_tools


def test_backend_line_classification():
    """Raw backend output is triaged: errno 2/5 mean the player must be reset
    by hand, mtkclient's Hint block just re-arms connection-guide step 1, and
    everything else stays Diagnostics-only."""
    from src.flash_service import (
        LINE_CONNECT_HINT,
        LINE_RETRY,
        classify_backend_line,
    )

    # Exactly the lines seen in a stalled connection's Diagnostics output.
    assert classify_backend_line("[Errno 5] Input/Output Error") == LINE_RETRY
    assert classify_backend_line("[Errno 2] Entity not found") == LINE_RETRY
    assert classify_backend_line("errno=5 I/O error") == LINE_RETRY
    assert classify_backend_line("USBError: [Errno 2] No such file") == LINE_RETRY

    hint = (
        "Hint: Power off the phone before connecting. For brom mode, press and "
        "hold vol up, vol dwn, or all hw buttons and connect usb."
    )
    assert classify_backend_line(hint) == LINE_CONNECT_HINT

    # Unrelated noise must not trigger either reaction.
    assert classify_backend_line("Device detected: USB 0E8D:2000") == ""
    assert classify_backend_line("[Errno 13] Permission denied") == ""
    assert classify_backend_line("") == ""


def test_retry_guidance_and_connect_hint():
    """A stalled target raises the retry-guidance dialog once per attempt (not
    once per repeated line), while Hint lines return the UI to guide step 1
    without any dialog."""
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication
    from src.flash_service import STEP_WAITING
    from src.state import FlashState
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    w.service.start_flash = lambda *a, **k: None
    w.service.start_device_monitor = lambda: None
    w.service.cancel_flash = lambda: None
    w.service.stop_device_monitor = lambda: None

    shown = []

    class _FakeDialog:
        def __init__(self, parent=None, detail=""):
            shown.append(detail)

        def exec(self):
            return 1

        def want_retry(self):
            return False

    w._on_package_selected("C:/fake/rom.zip", "Rockbox (Y1)", "Y1")
    w.service.step_changed.emit(STEP_WAITING)
    assert w.sm.state is FlashState.S2_WAIT_CONNECTION

    with patch("src.ui.main_window.RetryGuidanceDialog", _FakeDialog):
        for _ in range(18):
            w._append_log("[Errno 5] Input/Output Error")
        assert len(shown) == 1, f"one dialog per attempt, got {len(shown)}"
        assert "Errno 5" in shown[0], shown[0]

        # Hint lines are not errors: no dialog, and the connection guide is
        # re-armed at step 1 ("power off the device, then connect USB").
        w._append_log(
            "Hint: Power off the phone before connecting. For brom mode, press "
            "and hold vol up, vol dwn, or all hw buttons and connect usb."
        )
        assert len(shown) == 1, "hint lines must never raise a dialog"

    assert w._flash_page._guide_step == 1, "hint must highlight guide step 1"
    assert w.sm.state is FlashState.S2_WAIT_CONNECTION
    w.close()
    app.processEvents()


def test_device_model_registry():
    """The registry knows the wider lineup (so hand-imported firmware gets
    correct guidance) while only catalogue-backed models are selectable."""
    from src import device_models
    from src.config import detect_model_and_type_from_name, device_label_for_model

    ids = device_models.model_ids()
    assert ids, "assets/device_models.xml must parse"
    for expected in ("Y1", "Y2", "A5", "G5", "Q5", "Q3E", "Q8"):
        assert expected in ids, ids

    # Detection from the user-facing examples.
    assert device_models.model_from_filename("timmkoo_a5_stock.zip")[0] == "A5"
    assert device_models.model_from_filename("innioasis_g5_v1.zip")[0] == "G5"
    assert detect_model_and_type_from_name("innioasis_g5_v1.zip")[0] == "G5"

    # Legacy detection/formatting is untouched by the registry.
    assert detect_model_and_type_from_name("rom_a5.zip") == ("A5", None)
    assert detect_model_and_type_from_name("rom_y2.zip") == ("Y2", None)
    assert detect_model_and_type_from_name("rom.zip") == ("Y1", "A")
    assert device_label_for_model("Y1", "B") == "Y1 (Type B)"
    assert device_label_for_model("A5") == "A5"
    # Non-legacy models are named in full so prompts read correctly.
    assert device_label_for_model("G5") == "Innioasis G5"
    assert device_models.guidance_name("Q5") == "Timmkoo Q5"

    # Flashability distinguishes real targets from guidance-only models.
    assert device_models.is_flashable("Y1") is True
    assert device_models.is_flashable("A5") is True
    assert device_models.is_flashable("G5") is False

    # Selection order follows the registry, and an unknown catalogue model is
    # appended rather than hidden.
    assert device_models.selectable_models(["A5", "Y1"]) == ["Y1", "A5"]
    assert device_models.selectable_models(["ZZ9"]) == ["ZZ9"]
    assert device_models.selectable_models([], fallback=("Y1", "Y2")) == ["Y1", "Y2"]


def test_model_dropdown_manifest_filtered():
    """The device drop-down offers only models the manifest lists a release
    for, but a hand-imported model stays selectable so guidance names it."""
    from PySide6.QtWidgets import QApplication
    from src import catalog
    from src.ui.select_page import SelectPackagePage

    app = QApplication.instance() or QApplication(sys.argv)
    saved = list(catalog.LIVE_CATALOG)
    try:
        y2_only = [p for p in catalog.CATALOG if p.model == "Y2"][:1]
        assert y2_only, "static catalogue must carry a Y2 package"
        catalog.set_live_catalog(y2_only)
        assert catalog.available_models() == ["Y2"]

        page = SelectPackagePage()
        items = [page._model_combo.itemText(i) for i in range(page._model_combo.count())]
        assert items == ["Y2"], f"only manifest models may be offered, got {items}"

        # Firmware dropped in by hand for a model with no release yet must
        # still be selectable so the prompts name the right player.
        page._select_model_for_manual("G5")
        assert page.current_model() == "G5"
        page.refresh_models()
        items = [page._model_combo.itemText(i) for i in range(page._model_combo.count())]
        assert "G5" in items, items
        assert page.current_model() == "G5"
        page.deleteLater()
    finally:
        catalog.set_live_catalog(saved)
def test_release_notes_translation():
    """Verify release notes translation URL generation, in-app translation,
    and UI toggle under the release notes."""
    from unittest.mock import patch
    from PySide6.QtWidgets import QApplication, QListWidgetItem
    from PySide6.QtCore import Qt
    from src import translate
    from src.i18n import translator
    from src.ui.select_page import SelectPackagePage

    # 1. URL construction tests
    rel_sample = {
        "source_repo": "y1-community/y1-stock-rom",
        "tag_name": "3.2.1",
        "name": "3.2.1",
        "body": "General performance and stability improvements",
    }
    fi_url = translate.get_google_translate_release_url(rel_sample, "fi")
    assert fi_url == "https://github-com.translate.goog/y1-community/y1-stock-rom/releases/3.2.1?_x_tr_sl=auto&_x_tr_tl=fi&_x_tr_hl=fi&_x_tr_pto=wapp"

    en_url = translate.get_google_translate_release_url(rel_sample, "en")
    assert en_url == "https://github-com.translate.goog/y1-community/y1-stock-rom/releases/3.2.1?_x_tr_sl=auto&_x_tr_tl=en&_x_tr_hl=en&_x_tr_pto=wapp"

    es_url = translate.get_google_translate_release_url(rel_sample, "es")
    assert "tl=es" in es_url and "hl=es" in es_url and "github-com.translate.goog" in es_url

    # 2. In-memory translation helper
    cached_val = translate.fetch_google_translation("General performance and stability improvements", "es")
    assert isinstance(cached_val, str)
    assert len(cached_val) > 0
    # Cache hit returns immediately
    cached_again = translate.fetch_google_translation("General performance and stability improvements", "es")
    assert cached_again == cached_val

    # 3. UI behavior
    app = QApplication.instance() or QApplication(sys.argv)
    orig_lang = translator().lang
    opened_urls = []

    try:
        page = SelectPackagePage()

        # Verify 'Drop themes' text is not present anywhere in the page
        all_texts = []
        for child in page.findChildren(object):
            if hasattr(child, "text") and callable(child.text):
                all_texts.append(child.text())
        joined = " ".join(all_texts)
        assert "Drop themes" not in joined

        # When language is English and release selected, translate label is hidden
        translator().set_language("en")
        item = QListWidgetItem("3.2.1")
        item.setData(Qt.UserRole, rel_sample)
        page._on_release_selected(item, None)
        assert page._translate_label.isHidden()

        # When language is Spanish, translate label is visible and contains URL
        translator().set_language("es")
        page._update_translate_link()
        assert not page._translate_label.isHidden()
        assert "https://github-com.translate.goog" in page._translate_label.text()
        assert "3.2.1" in page._translate_label.text()

        # Clicking translate link opens browser and loads translated notes
        with patch("src.ui.select_page.open_browser", side_effect=lambda u: opened_urls.append(u)):
            page._on_translate_link_clicked(es_url)
            assert len(opened_urls) == 1
            assert opened_urls[0] == es_url
            app.processEvents()

            # Now test in-app translation applied
            page._on_translation_ready(rel_sample, "es", "3.2.1", "Mejoras generales de rendimiento")
            assert "Mejoras generales" in page._notes.toPlainText()
            assert "Google" in page._notes.toPlainText()

            # Clicking Show original reverts to English notes
            page._on_translate_link_clicked("action:show_original")
            assert "General performance" in page._notes.toPlainText()

        page.deleteLater()
    finally:
        translator().set_language(orig_lang)
    app.processEvents()


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
    check("simulate-macos flag parsing", test_simulate_macos_flag_parsing)
    check("simulate-macos backend parity", test_simulate_macos_backend_parity)
    check("releases client (cached)", test_releases_client_cached)
    check("releases client (force refresh)", test_releases_client_force_refresh)
    check("releases client (network)", test_releases_client_network)
    check("release version parsing and sorting", test_release_version_parsing_and_sorting)
    check("install power on steps", test_install_power_on_steps)
    check("donation dialog install completion", test_donation_dialog_install_completion)
    check("flash service action changed", test_flash_service_action_changed)
    check("sp flash tool gui", test_sp_flash_tool_gui)
    check("open browser focused new window", test_open_browser_focused_new_window)
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
    check("usblib reads bounded (no infinite DA hang)", test_usblib_reads_are_bounded)
    check("mtk stall watcher", test_mtk_stall_watcher)
    check("mtkclient DA progress surface", test_mtkclient_da_progress_surface)
    check("mtk DA failure auto recovers", test_mtk_da_failure_auto_recovers)
    check("mtk retry gives up when device stays away", test_mtk_retry_gives_up_when_device_stays_away)
    check("device monitor lost hold", test_device_monitor_lost_hold)
    check("linux sp flash validation", test_linux_sp_flash_validation)
    check("linux sp flash distro detection", test_linux_sp_flash_distro_detection)
    check("linux sp flash rules and readiness", test_linux_sp_flash_rules_and_readiness)
    check("linux setup dialog", test_linux_setup_dialog)
    check("linux sp flash askpass and step1 deferral", test_linux_sp_flash_askpass_and_step1_deferral)
    check("package prep gates flash start", test_package_prep_gates_flash_start)
    check("auto falls back when SP missing", test_auto_falls_back_when_sp_missing)
    check("download worker resume and cancel", test_download_worker)
    check("glass module and Ventura-GoldenGate compatibility", test_glass_module)
    check("native OS theming and widgets", test_native_theming)
    check("preloader raw wrapping and routing", test_preloader_raw_wrapping_and_routing)
    check("cross platform mtk payloads and backend dispatch", test_cross_platform_mtk_payloads_and_backend_dispatch)
    check("device tracking", test_device_tracking)
    check("check device updates", test_check_device_updates)
    check("settings page and dialogs", test_settings_page_and_dialogs)
    check("firmware release reminder install flow", test_firmware_release_reminder_install_flow)
    check("latest package tracking and history ini", test_latest_package_tracking_and_history_ini)
    check("prune extracted cache and reusing download", test_prune_extracted_cache_and_reusing_download)
    check("sp flash system checker and diagnostics", test_sp_flash_system_checker_and_diagnostics)
    check("sp history ini subsequent attempts and absolute paths", test_sp_history_ini_subsequent_attempts_and_absolute_paths)
    check("model detection and install guidance", test_model_detection_and_install_guidance)
    check("android sparse handling", test_android_sparse_handling)
    check("signed image stripping", test_signed_image_stripping)
    check("legacy mbr user addr bias", test_legacy_mbr_user_addr_bias)
    check("macos universal libusb fat binary", test_macos_universal_libusb)
    check("macos universal app bundle", test_macos_universal_app_bundle)
    check("tools manager and self healing", test_tools_manager_and_self_healing)
    check("backend line classification", test_backend_line_classification)
    check("retry guidance and connect hint", test_retry_guidance_and_connect_hint)
    check("device model registry", test_device_model_registry)
    check("model dropdown manifest filter", test_model_dropdown_manifest_filtered)
    check("release notes translation", test_release_notes_translation)
    if failures:
        print(f"\n{len(failures)} FAILURES:")
        for name, err in failures:
            print(f"  - {name}: {err!r}")
        sys.stdout.flush()
        os._exit(1)
    print("\nAll smoke tests passed.")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
