"""Smoke test for the Neo updater prototype.

Run:  python tests/smoke_test.py    (from the project root, with deps installed)
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("UPDATER_MTK_INPROCESS", "1")
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
    # Rockbox listing filters are separate keys outside the preferences group.
    from src import device_tracking
    for name in device_tracking.FILTER_NAMES:
        device_tracking.set_rockbox_release_filter(name, False, s)
    # Terminal installs are opt-in; a previous test (or a manual launch) must
    # not silently reroute the guided flow.
    device_tracking.set_terminal_install_enabled(False, s)
    # An SP Flash Tool auth file left behind would change every later flash.
    device_tracking.set_sp_auth_file("", s)
    s.remove("device_tracking")
    s.remove("latest_package")
    # Offline mode lives in the app's own settings scope (settings_page writes
    # it there, not in the scope above): a manually enabled offline mode would
    # otherwise leak into the suite and hide the online catalogue everywhere.
    QSettings("Innioasis", "UpdaterCE").setValue("offline_mode", False)
    from src import config
    config.IS_OFFLINE_MODE = False
    config.IS_MEDIATEK_INSTALLER = False
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

        real_find_sp = fs.paths.find_sp_flash_tool
        fs.paths.find_sp_flash_tool = lambda: Path("fake_sp.exe")
        try:
            calls = []
            make("auto")._dispatch_backend(Path("x"), Path("s"))
            if fs.IS_MAC:
                assert calls == ["mtk"], "auto on macOS -> MTKClient"
            else:
                assert calls == ["sp"], "auto prefers SP Flash Tool on Windows/Linux"
        finally:
            fs.paths.find_sp_flash_tool = real_find_sp
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


def test_rockbox_min_release_gate():
    """rockbox-y1/rockbox hides releases below 0.5: they are not compatible
    with Y1 units sold after April 2026, so Updater must not offer them."""
    from src.catalog import release_version_ok, release_version_tuple

    # Tag shapes the repo actually uses, plus ordinary version tags.
    for tag in ("stable-v0.5", "stable-v0.6", "stable-v1.0.2", "v1.0", "0.5.1"):
        assert release_version_ok("rockbox-y1/rockbox", tag), tag
    assert release_version_tuple("stable-v0.5") == (0, 5)

    for tag in ("stable-v0.4", "stable-v0.3", "v0.4.9", "0.2", "0.4"):
        assert not release_version_ok("rockbox-y1/rockbox", tag), tag

    # No readable version (nightlies, branch names) can't be proven new enough.
    for tag in ("nightly-35e926bd6ea663ab0aac2f138c7f71ae744971f",
                "type-a-base", "stock-menu", ""):
        assert not release_version_ok("rockbox-y1/rockbox", tag), tag

    # Other repos are untouched, and a full GitHub URL still matches the gate.
    assert release_version_ok("y1-community/y1-stock-rom", "v0.1")
    assert not release_version_ok("https://github.com/rockbox-y1/rockbox", "stable-v0.4")


def test_rockbox_min_release_gate_filters_listings():
    """The gate applies to cached and freshly fetched listings alike, so a
    cache written before the gate cannot resurface a hidden release."""
    with tempfile.TemporaryDirectory() as td:
        client = ReleasesClient(cache_root=td)
        rom = {"asset": {"browser_download_url": "u", "name": "rom.zip", "size": 100},
               "model": "dual", "type": "A", "resolution": "native"}
        tags = ["stable-v0.5", "stable-v0.4", "stable-v0.3",
                "nightly-35e926bd6ea663ab0aac2f138c7f71ae744971f"]
        client.cache_releases("rockbox-y1/rockbox", [
            {"tag_name": t, "download_url": "u", "prerelease": False,
             "rom_variants": [rom]}
            for t in tags
        ])
        assert [r["tag_name"] for r in client.get_cached_releases("rockbox-y1/rockbox")] == ["stable-v0.5"]
        assert [r["tag_name"] for r in client.get_all_releases("rockbox-y1/rockbox")] == ["stable-v0.5"]

        # Fresh fetch path: normalization drops the disallowed releases too.
        raw = [{"tag_name": t, "name": t, "published_at": "2026-05-16T00:19:43Z",
                "prerelease": False,
                "assets": [{"name": "rom.zip", "browser_download_url": "u", "size": 100}]}
               for t in ("stable-v0.4", "stable-v0.5")]
        client._get_json = lambda url: raw
        fetched = client.get_all_releases("rockbox-y1/rockbox", force_refresh=True)
        assert [r["tag_name"] for r in fetched] == ["stable-v0.5"]


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
    from src.config import APP_VERSION

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
    assert w.windowTitle().startswith("Updater CE v3.0")
    assert w._brand_label.text() == "Updater CE"
    assert w._icon_label.pixmap() is not None and not w._icon_label.pixmap().isNull()
    # Version badge sits beside the brand with no "v" prefix.
    assert w._brand_version.text() == APP_VERSION, (
        f"Expected bare version, got {w._brand_version.text()!r}"
    )
    assert not w._brand_version.text().lower().startswith("v")
    assert 'by <a href="https://ko-fi.com/teamslide"' in w._version_label.text()
    assert "Ryan Specter" in w._version_label.text()
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
        "name": "Alice", "amount": 25, "method": "Ko-Fi",
        "url": "https://ko-fi.com/alice",
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

    # The goal and donor lines are clickable too: a click on plain text opens
    # the donation dialog (exactly once per click)...
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    import src.donation_dialog as dd

    assert bar._goal_label.cursor().shape() == Qt.PointingHandCursor
    opened.clear()
    QTest.mouseClick(bar._goal_label, Qt.LeftButton, Qt.NoModifier, QPoint(6, 6))
    assert opened == [True]
    opened.clear()
    QTest.mouseClick(bar._donor_label, Qt.LeftButton, Qt.NoModifier, QPoint(6, 6))
    assert opened == [True]

    # ...while a donor name linked to its transaction URL opens that URL and
    # never the dialog.
    opened.clear()
    assert '<a href="https://ko-fi.com/alice"' in bar._donor_lines[0]
    opened_urls = []
    real_open = dd.open_browser
    dd.open_browser = lambda url: opened_urls.append(url)
    try:
        bar._handle_label_click("https://ko-fi.com/alice")
    finally:
        dd.open_browser = real_open
    assert opened_urls == ["https://ko-fi.com/alice"]
    assert opened == [], "a linked donor name must not open the dialog"

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

    # Status update vs donation display alternation (displayed one at a time, no collision)
    bar.show()
    app.processEvents()
    assert bar._donation_container.isVisible() is True
    assert bar._status_container.isVisible() is False

    # Showing status update hides donation container completely
    bar.showMessage("Preparing package...", 0)
    app.processEvents()
    assert bar._donation_container.isVisible() is False
    assert bar._status_container.isVisible() is True
    assert bar._status_label.text() == "Preparing package..."
    assert bar.currentMessage() == "Preparing package..."
    assert bar._status_revert_timer.isActive() is True  # Revert timer active when donations enabled

    # Clearing message restores donation container cleanly
    bar.clearMessage()
    app.processEvents()
    assert bar._donation_container.isVisible() is True
    assert bar._status_container.isVisible() is False
    assert bar.currentMessage() == ""

    # When donations are disabled: persistently show status messages without reverting to donations
    bar.set_donations_enabled(False)
    app.processEvents()
    assert bar._donation_container.isVisible() is False
    bar.showMessage("Flashing firmware...", 0)
    app.processEvents()
    assert bar._status_container.isVisible() is True
    assert bar._donation_container.isVisible() is False
    assert bar._status_revert_timer.isActive() is False  # Persists without auto-revert
    bar.clearMessage()
    app.processEvents()
    assert bar._status_container.isVisible() is False
    assert bar._donation_container.isVisible() is False

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


def test_donation_bar_corner_links():
    """The donation bar is one bar: Credits / Thanks in the left corner, the
    goal or donor display centred between them, Support Us as a text link in
    the right corner — and the whole lot goes with the "show donations"
    setting."""
    import datetime

    from PySide6.QtWidgets import QApplication, QPushButton
    from src import config
    from src.donation_dialog import DonationStatusBar, _LinkButton
    from src.i18n import tr
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    now = datetime.datetime.now()
    donations = [
        {"name": "Alice", "amount": 25, "method": "Ko-Fi", "url": "", "dt": now},
    ]

    credits_clicks = []
    support_clicks = []
    bar = DonationStatusBar(
        donations=donations,
        on_support=lambda: support_clicks.append(True),
        on_credits=lambda: credits_clicks.append(True),
    )
    bar.resize(1000, 44)
    bar.show()
    app.processEvents()

    # Both corner actions are text links, not chrome buttons.
    assert isinstance(bar._credits_link, _LinkButton)
    assert isinstance(bar._support_btn, _LinkButton)
    assert bar._credits_link.isFlat() and bar._support_btn.isFlat()
    assert bar._credits_link.text() == "Credits / Thanks"
    bar._credits_link.click()
    bar._support_btn.click()
    assert credits_clicks == [True]
    assert support_clicks == [True]

    # Credits / Thanks hugs one edge, Support Us the other, by equal margins.
    assert bar._credits_link.x() < bar._goal_group.x()
    assert bar._support_btn.x() + bar._support_btn.width() > bar._goal_group.x() + bar._goal_group.width()
    left_margin = bar._credits_link.x()
    right_margin = bar.width() - (bar._support_btn.x() + bar._support_btn.width())
    assert abs(left_margin - right_margin) <= 2, (left_margin, right_margin)

    def goal_centre():
        return bar._goal_group.x() + bar._goal_group.width() // 2

    # The goal display is on the bar's centre line, and the donor ticker takes
    # its place in the same centred slot when it rotates in.
    assert abs(goal_centre() - bar.width() // 2) <= 2, (goal_centre(), bar.width() // 2)
    bar._rotate()
    app.processEvents()
    assert bar._donor_label.isVisible() and bar._goal_label.isHidden()
    assert abs(goal_centre() - bar.width() // 2) <= 2, (goal_centre(), bar.width() // 2)
    bar.deleteLater()
    app.processEvents()

    # In the window the link lives in the bar, not in the sidebar, so it goes
    # when the bar goes.
    w = MainWindow()
    w.resize(1000, 700)
    w.show()
    app.processEvents()
    sb = w.statusBar()
    try:
        assert w._credits_btn is sb._credits_link
        assert tr("nav_credits") not in [b.text() for b in w._nav_panel.findChildren(QPushButton)]
        assert sb._credits_link.isVisible()

        w._settings_page._cb_hide_donations.setChecked(True)
        app.processEvents()
        assert not w._credits_btn.isVisible()
        assert not sb._support_btn.isVisible()
        assert not sb.isVisible()

        w._settings_page._cb_hide_donations.setChecked(False)
        # A status message takes the bar over while it shows — the donation
        # display (and with it the credits link) comes back once it clears.
        sb.clearMessage()
        app.processEvents()
        assert sb._credits_link.isVisible()

        # The generic installer has no credits page, yet its bar still keeps
        # the goal display centred.
        config.IS_MEDIATEK_INSTALLER = True
        w._apply_generic_mtk_branding()
        w._apply_donation_visibility()
        sb.clearMessage()  # brand changes post a status message too
        app.processEvents()
        assert sb.isVisible() and sb._support_btn.isVisible()
        assert not sb._credits_link.isVisible()
        centre = sb._goal_group.x() + sb._goal_group.width() // 2
        assert abs(centre - sb.width() // 2) <= 2, (centre, sb.width() // 2)
    finally:
        config.IS_MEDIATEK_INSTALLER = False
        w.close()
        app.processEvents()
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
    # Stored history is shown rather than a blank pane (a finished install must
    # still be readable); the empty-state text is only for a truly empty log.
    assert dlg._view.toPlainText().strip()
    assert hasattr(dlg, "_category_combo")
    assert hasattr(dlg, "_goto_btn")
    assert hasattr(dlg, "_save_btn")
    # Only the backends this host can produce are offered as drop-down views.
    from src.diagnostics import available_categories
    offered = [cat for cat, _ in available_categories()]
    present = {dlg._category_combo.itemData(i) for i in range(dlg._category_combo.count())}
    assert present == set(offered), (present, offered)
    assert "all" in present and "gui" in present
    assert hasattr(dlg, "_session_combo")
    assert hasattr(dlg, "_from_edit") and hasattr(dlg, "_to_edit")
    assert hasattr(dlg, "_problems_check")

    # Unique per run so lines persisted by an earlier run cannot satisfy (or
    # inflate) the assertions below.
    gui_line = f"first live line {os.getpid()}"
    mtk_line = f"Waiting for MTK device {os.getpid()}... Power off the device and connect USB."

    # Wire it the same way _show_diagnostics does.
    w.log_line_added.connect(dlg.append_line)
    w._append_log(gui_line)
    w._append_log("second live line")
    w._append_log("[SP] 0% of image data has been sent")
    w._append_log(mtk_line)

    # 1. Unified category shows all lines
    dlg._category_combo.setCurrentIndex(dlg._category_combo.findData("all"))
    text_all = dlg._view.toPlainText()
    assert gui_line in text_all
    assert "image data" in text_all
    assert "Waiting for MTK" in text_all
    # A live line is stored once, not once per recording path.
    assert text_all.count(gui_line) == 1, text_all

    # 2. SP Flash Tool category shows SP output (where the host offers it)
    from src.diagnostics import CAT_SP, CAT_MTK
    if dlg._category_combo.findData(CAT_SP) >= 0:
        dlg._category_combo.setCurrentIndex(dlg._category_combo.findData(CAT_SP))
        text_sp = dlg._view.toPlainText()
        assert "image data" in text_sp
        assert gui_line not in text_sp

    # 3. MTKClient category shows MTK output (where the host offers it)
    if dlg._category_combo.findData(CAT_MTK) >= 0:
        dlg._category_combo.setCurrentIndex(dlg._category_combo.findData(CAT_MTK))
        text_mtk = dlg._view.toPlainText()
        assert "Waiting for MTK" in text_mtk
        assert gui_line not in text_mtk
    dlg._category_combo.setCurrentIndex(dlg._category_combo.findData("all"))

    # 4. Save file test
    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        tmp_save = tf.name
    from PySide6.QtWidgets import QFileDialog
    orig_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = lambda *a, **k: (tmp_save, "Text Files (*.log *.txt)")
    try:
        dlg._on_save_file()
        saved_text = Path(tmp_save).read_text(encoding="utf-8")
        assert "Waiting for MTK" in saved_text
    finally:
        QFileDialog.getSaveFileName = orig_save
        try:
            os.unlink(tmp_save)
        except Exception:
            pass

    # 5. Copy to clipboard test
    dlg._on_copy()
    assert "Waiting for MTK" in QApplication.clipboard().text()

    # 6. Reveal in file manager test
    from unittest.mock import patch
    with patch("subprocess.run") as mock_subproc:
        dlg._on_goto_file()
        assert mock_subproc.called

    w.log_line_added.disconnect(dlg.append_line)
    dlg.close()
    w.close()
    app.processEvents()


def test_tool_output_capture():
    """Output the in-process MTKClient prints itself, plus its logging records,
    reach the diagnostics log — not only a console a GUI build does not have."""
    import logging

    from src.diagnostics import (
        DiagnosticsManager,
        capture_tool_output,
        CAT_ALL,
        CAT_MTK,
    )

    mgr = DiagnosticsManager.instance()
    mgr.clear()
    mgr.start_flash_session("pkg.zip", "mtk", "Y1")
    real_stdout, real_stderr = sys.stdout, sys.stderr
    try:
        recorded = []

        def sink(line):
            # Mirrors MainWindow._on_log_message: the capture sink both
            # forwards to the UI and stores the line.
            recorded.append(line)
            mgr.record_log(line, dedupe=True)

        with capture_tool_output(sink, tag="MTK"):
            print("usblib: [usbread] Overflow with sz=65536, retrying with 4096")
            sys.stderr.write("DaHandler: SLA Signature was accepted.\n")
            logging.getLogger("src.flash_service").warning("DA stage 2 upload failed")

        # Streams are restored afterwards and writes still reach the console.
        assert sys.stdout is real_stdout and sys.stderr is real_stderr
        raw = [line for line in recorded if "stage 2 upload" not in line]
        assert [line for line in raw if "Overflow with sz=65536" in line], recorded
        assert any("SLA Signature was accepted" in line for line in raw), recorded
        assert all(line.startswith("[MTK] ") for line in raw), recorded

        text_all = "\n".join(mgr.get_lines(CAT_ALL))
        assert "Overflow with sz=65536" in text_all, text_all
        assert "SLA Signature was accepted" in text_all, text_all
        assert "DA stage 2 upload failed" in text_all, text_all
        # ...and stored once, despite being written to the console too.
        assert text_all.count("DA stage 2 upload failed") == 1, text_all

        # The backend tab carries the raw tool output as well.
        text_mtk = "\n".join(mgr.get_lines(CAT_MTK))
        assert "Overflow with sz=65536" in text_mtk, text_mtk

        # A tool line that arrives twice is stored once.
        mgr.record_log("[MTK] echoed twice", dedupe=True)
        mgr.record_log("[MTK] echoed twice", dedupe=True)
        assert "\n".join(mgr.get_lines(CAT_ALL)).count("echoed twice") == 1
    finally:
        mgr.end_flash_session(True)
        sys.stdout, sys.stderr = real_stdout, real_stderr
        mgr.clear()


def test_sp_internal_log_streamed():
    """flash_tool's internal QT_FLASH_TOOL.log is forwarded into the SP
    category, skipping lines its console stream already reported."""
    import src.flash_service as fs

    class _StopAfter:
        """Stand-in for the monitor's threading.Event, so the tail loop can be
        driven synchronously and deterministically."""

        def __init__(self, checks):
            self._left = checks

        def is_set(self):
            self._left -= 1
            return self._left <= 0

    with tempfile.TemporaryDirectory() as td:
        log_root = Path(td)
        run_dir = log_root / "20261007_SP_FT"
        run_dir.mkdir()
        (run_dir / "QT_FLASH_TOOL.log").write_text(
            "2026-10-07 12:00:00  0% of image data has been sent\n"
            "DA version: 1.0.0.0\n"
            "S_DA_INIT_SYNC_ERROR (1093)\n",
            encoding="utf-8",
        )

        w = fs.FlashWorker("pkg.zip", method="sp")
        logs = []
        w.log_message.connect(logs.append)
        # This line was already reported on the console stream.
        w._sp_stdout_seen.add(
            fs._normalise_tool_line("2026-10-07 12:00:00  0% of image data has been sent")
        )

        # Two passes over the tail loop (the monitor waits for flash_tool to
        # create its run directory, then follows the file).
        w._monitor_sp_log_file(_StopAfter(3), str(log_root))

        forwarded = [line for line in logs if line.startswith("[QT] ")]
        assert any("DA version: 1.0.0.0" in line for line in forwarded), logs
        assert any("S_DA_INIT_SYNC_ERROR (1093)" in line for line in forwarded), logs
        assert not any("0% of image data has been sent" in line for line in logs), logs


def test_diagnostics_finished_install_readback():
    """A finished install can be read back and exported: the complaint was that
    once the run ends there is no way to see what the tooling did."""
    from PySide6.QtWidgets import QApplication
    from src.diagnostics import DiagnosticsManager, CAT_ALL, CAT_SP, CAT_MTK, CAT_GUI
    from src.ui.dialogs import DiagnosticsDialog

    app = QApplication.instance() or QApplication(sys.argv)
    mgr = DiagnosticsManager.instance()
    mgr.clear()

    mgr.start_flash_session("rom.zip", "sp", "Y1")
    mgr.record_log("Extracting firmware package: rom.zip")
    mgr.record_log("[SP] BROM connected")
    mgr.record_log("[QT] DA version: 1.0.0.0")
    mgr.end_flash_session(False, "S_DA_INIT_SYNC_ERROR (1093)")

    dlg = DiagnosticsDialog(parent=None, lines=[])
    try:
        dlg._category_combo.setCurrentIndex(dlg._category_combo.findData(CAT_ALL))
        text_all = dlg._view.toPlainText()
        assert "INSTALL SESSION STARTED" in text_all, text_all
        assert "INSTALL SESSION COMPLETED: FAILED" in text_all, text_all
        assert "S_DA_INIT_SYNC_ERROR (1093)" in text_all, text_all

        # Category routing is a data-level guarantee, independent of which
        # backends this host shows in the drop-down.
        from src.diagnostics import CAT_SP, CAT_MTK

        sp_lines = "\n".join(mgr.get_lines(CAT_SP))
        assert "BROM connected" in sp_lines, sp_lines
        assert "Extracting firmware package" not in sp_lines, sp_lines
        assert "BROM connected" not in "\n".join(mgr.get_lines(CAT_MTK))
        gui_lines = "\n".join(mgr.get_lines(CAT_GUI))
        assert "Extracting firmware package" in gui_lines, gui_lines
        assert "BROM connected" not in gui_lines, gui_lines

        # ... and the drop-down only offers the backends this host can produce.
        for cat in (CAT_SP, CAT_MTK, CAT_GUI):
            if dlg._category_combo.findData(cat) < 0:
                continue
            dlg._category_combo.setCurrentIndex(dlg._category_combo.findData(cat))
            text_cat = dlg._view.toPlainText()
            assert mgr.get_lines(cat)[-1] in text_cat, text_cat
            if cat == CAT_GUI:
                assert "Extracting firmware package" in text_cat, text_cat
                assert "BROM connected" not in text_cat, text_cat

        # Export whatever the user is looking at.
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tf:
            tmp_save = tf.name
        from PySide6.QtWidgets import QFileDialog
        orig_save = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = lambda *a, **k: (tmp_save, "Text Files (*.log *.txt)")
        try:
            dlg._on_save_file()
            saved = Path(tmp_save).read_text(encoding="utf-8")
            assert "Extracting firmware package" in saved, saved
        finally:
            QFileDialog.getSaveFileName = orig_save
            try:
                os.unlink(tmp_save)
            except Exception:
                pass
    finally:
        dlg.close()
        mgr.clear()
        app.processEvents()


def test_connectivity_check_logging():
    """Supporters are never written to the diagnostics log — reachability is.
    When the feed cannot be loaded the newest local copy is used instead, so the
    app keeps working with innioasis.app unavailable."""
    import logging
    import shutil
    import threading

    from src import donors
    from src.diagnostics import CAT_ALL, DiagnosticsManager, install_log_capture, remove_log_capture

    _reset_app_settings()
    mgr = DiagnosticsManager.instance()
    mgr.clear()
    # The app configures root logging at INFO; a bare test process defaults to
    # WARNING, which would drop the connectivity records before the diagnostics
    # handler ever sees them.
    src_logger = logging.getLogger("src")
    original_level = src_logger.level
    src_logger.setLevel(logging.INFO)

    class _Resp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    csv_text = "Name,Amount,Date,URL,Method\nAlice,50,15/07/2026,https://x,PayPal\n"
    tmp_dir = Path(tempfile.mkdtemp(prefix="updater-donors-"))
    cache_file = tmp_dir / "donors.csv"
    original_get = donors.requests.get
    original_cache_path = donors.cached_donors_path
    donors.cached_donors_path = lambda: cache_file
    install_log_capture()
    try:
        # 1. Reachable feed: a successful connectivity check is logged, the
        #    supporters in the payload are not.
        donors.requests.get = lambda *a, **k: _Resp(200, csv_text)
        done = threading.Event()
        seen = []

        def on_result(parsed):
            seen.append(parsed)
            done.set()

        donors.fetch_remote_donors_async(on_result)
        assert done.wait(10), "donor fetch worker never finished"
        assert seen and seen[0] and seen[0][0]["name"] == "Alice", seen
        assert cache_file.is_file() and "Alice" in cache_file.read_text(encoding="utf-8")

        log_text = "\n".join(mgr.get_lines(CAT_ALL))
        assert "Connectivity check: online" in log_text, log_text
        assert "innioasis.app" in log_text, log_text
        assert "Alice" not in log_text, log_text
        assert "donation records" not in log_text, log_text

        # 2. Unreachable feed: the failure is logged as a connectivity check,
        #    and the most recent local copy is served instead of a blank list.
        mgr.clear()

        def _boom(*_a, **_k):
            raise OSError("network is unreachable")

        donors.requests.get = _boom
        done2 = threading.Event()
        seen2 = []

        def on_result2(parsed):
            seen2.append(parsed)
            done2.set()

        donors.fetch_remote_donors_async(on_result2)
        assert done2.wait(10), "donor fetch worker never finished"
        assert seen2 and seen2[0], "the locally cached supporters must still be usable"
        assert seen2[0][0]["name"] == "Alice", seen2

        log_text2 = "\n".join(mgr.get_lines(CAT_ALL))
        assert "Connectivity check: offline" in log_text2, log_text2
        assert "network is unreachable" in log_text2, log_text2
        assert "Alice" not in log_text2, log_text2

        # 3. No cache at all: nothing is invented.
        cache_file.unlink()
        done3 = threading.Event()
        seen3 = []
        donors.fetch_remote_donors_async(lambda parsed: (seen3.append(parsed), done3.set()))
        assert done3.wait(10), "donor fetch worker never finished"
        assert seen3 == [None], seen3
    finally:
        donors.requests.get = original_get
        donors.cached_donors_path = original_cache_path
        src_logger.setLevel(original_level)
        remove_log_capture()
        shutil.rmtree(tmp_dir, ignore_errors=True)
        mgr.clear()
    _reset_app_settings()


def test_diagnostics_time_filter():
    """A failed install must be findable by date/time: the diagnostics view can
    jump to a recorded install session or to an explicit window, keep the
    traceback of the lines it selects, and show how much of the log it is
    hiding — plus only offer the backends this host can actually produce."""
    from datetime import datetime

    from PySide6.QtWidgets import QApplication
    from src import diagnostics as diag
    from src.ui.dialogs import DiagnosticsDialog, _to_qdatetime

    # 1. Platform-scoped drop-downs: no SP Flash Tool on macOS, no MTKClient on
    #    Windows until the hidden options are revealed with the M key.
    mac_cats = [c for c, _ in diag.available_categories(is_mac=True, is_windows=False, hidden_mtk=False)]
    assert diag.CAT_SP not in mac_cats and diag.CAT_MTK in mac_cats, mac_cats
    win_cats = [c for c, _ in diag.available_categories(is_mac=False, is_windows=True, hidden_mtk=False)]
    assert diag.CAT_SP in win_cats and diag.CAT_MTK not in win_cats, win_cats
    win_hidden = [c for c, _ in diag.available_categories(is_mac=False, is_windows=True, hidden_mtk=True)]
    assert diag.CAT_MTK in win_hidden, win_hidden
    linux_cats = [c for c, _ in diag.available_categories(is_mac=False, is_windows=False, hidden_mtk=False)]
    assert diag.CAT_SP in linux_cats and diag.CAT_MTK in linux_cats, linux_cats
    for cats in (mac_cats, win_cats, linux_cats):
        assert diag.CAT_ALL in cats and diag.CAT_GUI in cats

    # The revealed-options flag is remembered, not just a per-session toggle.
    from src.device_tracking import hidden_mtk_options_enabled, set_hidden_mtk_options
    set_hidden_mtk_options(True)
    assert hidden_mtk_options_enabled()
    assert diag.CAT_MTK in [c for c, _ in diag.available_categories()]
    set_hidden_mtk_options(False)
    assert not hidden_mtk_options_enabled()

    # 2. Two installs, one of which failed with a traceback that carries no
    #    timestamp of its own.
    lines = [
        "[2026-10-07 10:00:00] ======================================================================",
        "[2026-10-07 10:00:00] === INSTALL SESSION STARTED: 2026-10-07 10:00:00",
        "[2026-10-07 10:00:00] === Software Package: rom.zip",
        "[2026-10-07 10:00:00] === Device Model:     Y1",
        "[2026-10-07 10:00:00] === Flashing Method:  sp",
        "[2026-10-07 10:00:05] Download OK",
        "[2026-10-07 10:00:06] === INSTALL SESSION COMPLETED: SUCCESS at 2026-10-07 10:00:06",
        "[2026-10-07 10:10:00] === INSTALL SESSION STARTED: 2026-10-07 10:10:00",
        "[2026-10-07 10:10:00] === Software Package: rockbox.zip",
        "[2026-10-07 10:10:00] === Device Model:     Y1",
        "[2026-10-07 10:10:00] === Flashing Method:  mtk",
        "[2026-10-07 10:10:05] Download failed: S_DA_INIT_SYNC_ERROR (1093)",
        "Traceback (most recent call last):",
        "  SystemExit: 1",
        "[2026-10-07 10:10:06] === INSTALL SESSION COMPLETED: FAILED (S_DA_INIT_SYNC_ERROR (1093)) at 2026-10-07 10:10:06",
        "[2026-10-07 11:00:00] Fetched donors",
    ]
    window = diag.filter_log_lines(
        lines, start=datetime(2026, 10, 7, 10, 9), end=datetime(2026, 10, 7, 10, 11)
    )
    assert any("rockbox.zip" in ln for ln in window), window
    assert not any("rom.zip" in ln for ln in window), window
    assert not any("Fetched donors" in ln for ln in window), window
    # The traceback has no timestamp: it belongs to the failing line above it.
    assert any("Traceback" in ln for ln in window), window
    problems = diag.filter_log_lines(
        lines,
        start=datetime(2026, 10, 7, 10, 9),
        end=datetime(2026, 10, 7, 10, 11),
        problems_only=True,
    )
    assert any("Download failed" in ln for ln in problems), problems
    assert any("Traceback" in ln for ln in problems), problems
    assert not any("Download OK" in ln for ln in problems), problems

    # 3. Sessions come from the banners the flash pipeline writes.
    sessions = diag.collect_install_sessions(lines)
    assert len(sessions) == 2, sessions
    good, bad = sessions
    assert good.package == "rom.zip" and good.success is True and not good.running
    assert good.started == datetime(2026, 10, 7, 10, 0, 0)
    assert good.ended == datetime(2026, 10, 7, 10, 0, 6)
    assert bad.package == "rockbox.zip" and bad.model == "Y1" and bad.method == "mtk"
    assert bad.success is False and bad.detail == "S_DA_INIT_SYNC_ERROR (1093)"
    assert bad.problem_lines >= 3, bad.problem_lines
    assert diag.parse_line_timestamp("Traceback (most recent call last):") is None
    oldest, newest = diag.log_time_range(lines)
    assert oldest == datetime(2026, 10, 7, 10, 0, 0) and newest == datetime(2026, 10, 7, 11, 0, 0)

    # 4. Through the dialog: pick the failed install, then a custom window.
    app = QApplication.instance() or QApplication(sys.argv)
    mgr = diag.DiagnosticsManager.instance()
    mgr.clear()
    with diag._LOCK:  # deterministic content, independent of this host's logs
        mgr._buffers[diag.CAT_ALL] = list(lines)
    dlg = DiagnosticsDialog(parent=None, lines=[])
    try:
        dlg._category_combo.setCurrentIndex(dlg._category_combo.findData(diag.CAT_ALL))
        assert dlg._session_combo.count() == 3, dlg._session_combo.count()
        assert not dlg._range_active, "the default view must not hide anything"
        assert "Fetched donors" in dlg._view.toPlainText()
        from src.i18n import tr

        assert dlg._count_label.text() == tr("log_lines_total").format(
            total=len(dlg.raw_lines())
        ), dlg._count_label.text()

        failed_index = dlg._session_combo.findData(bad.number)
        assert failed_index > 0, failed_index
        dlg._session_combo.setCurrentIndex(failed_index)
        text_session = dlg._view.toPlainText()
        assert "rockbox.zip" in text_session, text_session
        assert "Traceback" in text_session, text_session
        assert "rom.zip" not in text_session, text_session
        assert "Fetched donors" not in text_session, text_session
        assert "of" in dlg._count_label.text(), dlg._count_label.text()

        # Problems only narrows to the failure and its traceback.
        dlg._problems_check.setChecked(True)
        text_problems = dlg._view.toPlainText()
        assert "Download failed" in text_problems, text_problems
        assert "Traceback" in text_problems, text_problems
        assert "Software Package: rockbox.zip" not in text_problems, text_problems

        # Picking the install that succeeded shows it, not the failed one.
        dlg._problems_check.setChecked(False)
        dlg._session_combo.setCurrentIndex(dlg._session_combo.findData(good.number))
        text_good = dlg._view.toPlainText()
        assert "Download OK" in text_good, text_good
        assert "rockbox.zip" not in text_good, text_good

        # An explicit window is honoured even with no session selected.
        dlg._session_combo.setCurrentIndex(0)
        dlg._from_edit.setDateTime(_to_qdatetime(datetime(2026, 10, 7, 10, 10, 0)))
        dlg._to_edit.setDateTime(_to_qdatetime(datetime(2026, 10, 7, 10, 10, 30)))
        text_window = dlg._view.toPlainText()
        assert "Download failed" in text_window, text_window
        assert "rom.zip" not in text_window, text_window
        assert "Fetched donors" not in text_window, text_window

        # "All installs" puts the whole log back.
        dlg._session_combo.setCurrentIndex(1)
        dlg._session_combo.setCurrentIndex(0)
        text_back = dlg._view.toPlainText()
        assert "rom.zip" in text_back and "Fetched donors" in text_back, text_back
        assert not dlg._range_active

        # An export records the filter that produced it.
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tf:
            tmp_filtered = tf.name
        from PySide6.QtWidgets import QFileDialog
        orig_save = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = lambda *a, **k: (tmp_filtered, "Text Files (*.log *.txt)")
        try:
            dlg._session_combo.setCurrentIndex(dlg._session_combo.findData(bad.number))
            dlg._on_save_file()
            saved = Path(tmp_filtered).read_text(encoding="utf-8")
            assert saved.startswith("# "), saved[:200]
            assert "rockbox.zip" in saved and "Fetched donors" not in saved, saved
        finally:
            QFileDialog.getSaveFileName = orig_save
            try:
                os.unlink(tmp_filtered)
            except Exception:
                pass
    finally:
        dlg.close()
        mgr.clear()
        app.processEvents()
    _reset_app_settings()


def test_titlebar_spacing():
    """The sidebar and page content must start just below the window title bar.

    The sidebar used to begin 42px down on macOS (a wasted band under the
    traffic lights) while page content began at 12px, so the first entry floated
    well below the title bar."""
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication
    from src.ui import dark
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()  # layouts only have real geometry once shown
    try:
        first_widget = getattr(w, "_brand_container", w._nav_buttons["nav_select_package"][0])
        nav_top = first_widget.mapTo(w, QPoint(0, 0)).y()
        title_top = w._select_page._title.mapTo(w, QPoint(0, 0)).y()

        assert nav_top in (dark.content_top_margin(), 5, 6), (nav_top, dark.content_top_margin())
        # Only the window chrome may sit above the first sidebar entry.
        assert nav_top <= 32, f"sidebar starts {nav_top}px down: wasted space under the title bar"
        # The sidebar must not start below the page content.
        assert nav_top - title_top <= 20, (nav_top, title_top)
        # Page content itself hugs the top of the page area.
        assert title_top <= 16, title_top

        # A non-glass host (native title bar: client area already below it)
        # needs no traffic-light clearance at all.
        assert dark.content_top_margin() <= dark.MACOS_TRAFFIC_LIGHT_CLEARANCE
        assert dark.page_top_margin() <= 16

        # macOS with an extended content view: the first entry is pulled up into
        # the title-bar band (-12px) so it lines up with the window chrome.
        if dark.IS_MACOS:
            from src.ui import glass

            orig_glass = glass.is_glass_supported
            glass.is_glass_supported = lambda: True
            try:
                assert dark.content_top_margin() == (
                    dark.MACOS_TRAFFIC_LIGHT_CLEARANCE - dark.MACOS_SIDEBAR_TITLEBAR_TRIM
                ), dark.content_top_margin()
                assert dark.content_top_margin() >= 0
            finally:
                glass.is_glass_supported = orig_glass
    finally:
        w.close()
        app.processEvents()


def test_theme_refresh_live():
    """A host appearance change (dark/light switch, desktop accent recolour) must
    restyle the running app on the fly: palette, stylesheet and the inline styles
    owned by custom widgets — no restart, no residual light-mode colours."""
    import time

    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QWidget
    from src.ui import dark
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    dark.apply_theme(app, force_dark=False)
    w = MainWindow()
    w.show()
    app.processEvents()

    original_dark_probe = dark.is_system_dark_mode
    original_accent_probe = dark.get_native_accent_color
    original_fingerprint = dark.theme_fingerprint
    applied = []
    watcher = None
    hostile = None
    try:
        light_qss = app.styleSheet()
        light_bg = dark.T().bg
        light_fg = dark.T().fg
        assert light_bg in light_qss and light_fg  # baseline really is the light theme
        nav_btn = w._nav_buttons["nav_select_package"][0]
        assert dark.T().accent in nav_btn.styleSheet()

        # A widget whose refresher is broken must not stop the app-wide refresh.
        class _Hostile(QWidget):
            def refresh_theme(self):
                raise RuntimeError("widget refresh exploded")

        hostile = _Hostile()
        hostile.show()
        assert hostile in app.allWidgets()

        watcher = dark.ThemeWatcher(app, on_apply=lambda: applied.append(True))
        watcher.install()
        assert watcher._poll_timer.isActive(), "watcher must poll for changes Qt never signals"

        # The host switches to dark mode and to a new accent colour. Neither Qt
        # palette signal is emitted in this synthetic change, so this exercises
        # the fingerprint poll — the catch-all path.
        dark.is_system_dark_mode = lambda: True
        dark.get_native_accent_color = lambda palette=None: ("#ff375f", "#ffffff")
        dark.theme_fingerprint = lambda app=None, **kw: ("dark", True, "#ff375f")
        watcher.poll()
        assert watcher._debounce.isActive(), "a detected change must schedule a refresh"
        deadline = time.time() + 3.0
        while time.time() < deadline and not applied:
            app.processEvents()
            time.sleep(0.02)
        assert applied, "the debounced refresh never ran"

        # Theme state, palette, stylesheet and custom widgets all moved over.
        assert dark.is_dark()
        dark_tokens = dark.T()
        assert dark_tokens.accent == "#ff375f", dark_tokens.accent
        assert app.palette().color(QPalette.Window).name() == dark_tokens.bg
        assert dark_tokens.bg in app.styleSheet() and light_bg not in app.styleSheet()
        assert dark_tokens.accent in nav_btn.styleSheet(), nav_btn.styleSheet()
        assert f"color: {dark_tokens.fg}" in w._brand_label.styleSheet()
        # The window-level chrome (native title bar, glass) is refreshed too.
        assert applied, "the window chrome hook never ran"

        # A refresh must not re-trigger itself through the events it emits.
        settle_until = time.time() + 0.8
        while time.time() < settle_until:
            app.processEvents()
            time.sleep(0.02)
        assert len(applied) == 1, f"theme refresh re-triggered itself: {len(applied)} passes"

        # An unchanged host must not trigger any refresh at all.
        dark.is_system_dark_mode = lambda: True
        dark.get_native_accent_color = lambda palette=None: ("#ff375f", "#ffffff")
        dark.theme_fingerprint = original_fingerprint
        watcher._last = original_fingerprint(app, platform_dark=False)
        watcher._last_full = original_fingerprint(app)
        for _ in range(20):
            watcher.poll()
            app.processEvents()
            time.sleep(0.02)
        assert len(applied) == 1, f"unchanged host triggered {len(applied) - 1} extra refresh(es)"
        assert not watcher._debounce.isActive()

        # Dark -> light again, this time through a plain refresh (the hostile
        # widget's broken hook must be swallowed, not propagated).
        applied.clear()
        dark.is_system_dark_mode = lambda: False
        dark.refresh_theme(app)
        assert not dark.is_dark()
        assert light_bg in app.styleSheet()
        assert not applied  # chrome updates belong to the watcher, not refresh_theme
    finally:
        dark.is_system_dark_mode = original_dark_probe
        dark.get_native_accent_color = original_accent_probe
        dark.theme_fingerprint = original_fingerprint
        if watcher is not None:
            watcher.stop()
            assert not watcher._poll_timer.isActive()
        if hostile is not None:
            hostile.close()
        w.close()
        dark._state.detect()
        dark.apply_theme(app)
        app.processEvents()
    _reset_app_settings()


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
    assert "connect your Y1" in banner or "make sure your Y1 is powered off" in banner, banner
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

    # When done, warning is hidden and the top banner flips to completion
    # (the completion dialog is shown over this page).
    w.service.step_changed.emit(STEP_DONE)
    assert not w._flash_page._warning.isVisible()
    assert w._flash_page._flash_banner.text() == "Install complete"
    assert w._flash_page._wait_status.text() == "Complete"
    w.close()
    app.processEvents()


def test_install_nav_entry_during_run():
    """While a flash run is live the sidebar's first entry becomes "Install
    Software" and holds the accent highlight on every page, so the sidebar
    keeps showing the run instead of going blank; clicking it reopens the
    run's view. Ending the run restores the package browser entry."""
    from PySide6.QtWidgets import QApplication
    from src.i18n import translator, tr
    from src.state import FlashState
    from src.ui.main_window import (
        MainWindow,
        _PAGE_ERROR,
        _PAGE_FLASH,
        _PAGE_RETRY,
        _PAGE_SELECT,
        _PAGE_SETTINGS,
    )

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    w.service.start_flash = lambda pkg, pre="", method="auto", **kw: None
    w.service.start_device_monitor = lambda: None
    w.service.stop_device_monitor = lambda: None

    btn, _idx = w._nav_buttons["nav_select_package"]
    settings_btn, _sidx = w._nav_buttons["nav_settings"]
    assert btn.text() == "Select Software", btn.text()
    assert btn.isChecked() and not settings_btn.isChecked()
    assert not w._install_run_active()

    # Only the in-progress states count as a live install.
    for state in (
        FlashState.S2_WAIT_CONNECTION,
        FlashState.S3_DEVICE_DETECTED,
        FlashState.S4_FLASHING,
        FlashState.S6_RETRYING,
    ):
        w.sm.force_state(state)
        assert w._install_run_active(), state
    for state in (
        FlashState.S1_SELECT_FILE,
        FlashState.S5_COMPLETE,
        FlashState.S5_FAILED,
        FlashState.S5_USB_DISCONNECTED,
    ):
        w.sm.force_state(state)
        assert not w._install_run_active(), state
    w.sm.reset_full()

    w._on_package_selected("C:/fake/rom.zip", "Rockbox (Y1)", "Y1")
    assert w.sm.state is FlashState.S2_WAIT_CONNECTION, w.sm.state
    assert w._stack.currentIndex() == _PAGE_FLASH
    assert btn.text() == "Install Software", btn.text()
    assert btn.isChecked(), "the running install must hold the accent highlight"

    # During an active install, navigating away to other pages like Settings is guarded and blocked
    for page in (_PAGE_ERROR, _PAGE_SETTINGS, _PAGE_SELECT):
        w._nav_to_page(page)
        assert w._stack.currentIndex() == _PAGE_FLASH, "navigating away during active run must be blocked"
        assert btn.text() == "Install Software", (page, btn.text())
        assert btn.isChecked(), f"highlight lost on page index {page}"

    # Clicking the first entry from another page reopens the run view.
    w._stack.setCurrentIndex(_PAGE_SELECT)
    btn.click()
    app.processEvents()
    assert w._stack.currentIndex() == _PAGE_FLASH, "first entry must reopen the run"

    # A mid-run language switch keeps the running label, translated.
    translator().set_language("zh-CN")
    w._retranslate_all()
    assert btn.text() == tr("nav_install_package"), btn.text()
    translator().set_language("en")
    w._retranslate_all()
    assert btn.text() == "Install Software", btn.text()

    # A retry attempt is still part of the run.
    w.sm.force_state(FlashState.S6_RETRYING)
    w._nav_to_page(_PAGE_RETRY)
    assert btn.text() == "Install Software" and btn.isChecked()

    # Ending the run restores the browser entry and its normal highlight.
    w._reset_after_run()
    assert w.sm.state is FlashState.S1_SELECT_FILE, w.sm.state
    assert w._stack.currentIndex() == _PAGE_SELECT
    assert btn.text() == "Select Software", btn.text()
    assert btn.isChecked(), "on the Select page the browser entry is highlighted"
    w._nav_to_page(_PAGE_SETTINGS)
    assert not btn.isChecked(), "browser entry must not stay highlighted after the run"
    assert settings_btn.isChecked()

    w.close()
    app.processEvents()
    _reset_app_settings()


def test_terminal_install_handoff():
    """Terminal installs: the Settings toggle keeps the user on Select Software
    and hands the console command (SP Flash Tool, else MTKClient) to a launcher
    script opened in their own terminal, so the tools' output can be read. The
    guided backend must not start, and failures must be reported."""
    import tempfile
    from pathlib import Path

    from PySide6.QtWidgets import QApplication
    from src import device_tracking, i18n, paths, terminal_install
    from src.flash_service import METHOD_MTK, METHOD_SP
    from src.i18n import tr
    from src.state import FlashState
    from src.ui import settings_page
    from src.ui.main_window import MainWindow, _PAGE_SELECT

    _reset_app_settings()

    for key in (
        "nav_install_package",
        "settings_terminal_group",
        "settings_terminal_install",
        "settings_terminal_desc",
        "settings_terminal_desc_windows",
        "terminal_install_title",
        "terminal_install_no_scatter",
        "terminal_install_no_tool",
        "terminal_install_failed",
        "status_terminal_install",
    ):
        table = i18n._STRINGS.get(key)
        assert table, f"{key} missing from i18n"
        assert set(table) == {"zh-CN", "en", "fr", "es"}, (key, sorted(table))
    # Wording names the console app the user will actually see.
    desc_terminal = i18n._STRINGS["settings_terminal_desc"]
    desc_windows = i18n._STRINGS["settings_terminal_desc_windows"]
    assert "Terminal" in desc_terminal["en"], desc_terminal["en"]
    assert "Command Prompt" in desc_windows["en"], desc_windows["en"]
    for loc in ("zh-CN", "en", "fr", "es"):
        assert desc_terminal[loc] != desc_windows[loc], loc
        assert "Select Software screen" not in desc_terminal[loc], loc
    lang = i18n.translator().lang
    orig_windows = paths.IS_WINDOWS
    try:
        paths.IS_WINDOWS = False
        assert settings_page.terminal_install_desc() == desc_terminal[lang]
        paths.IS_WINDOWS = True
        assert settings_page.terminal_install_desc() == desc_windows[lang]
    finally:
        paths.IS_WINDOWS = orig_windows

    orig_win, orig_mac = paths.IS_WINDOWS, paths.IS_MAC
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        extract = root / "rom_a5"
        extract.mkdir()
        scatter = extract / "MT6572_Android_scatter.txt"
        scatter.write_text("platform: MT6572\npartition_name: boot\nfile_name: boot.img\n")
        pkg = str(root / "rom_a5.zip")

        # SP Flash Tool reuses the guided flow's console arguments.
        sp_dirs, sp_cmds = {}, {}
        try:
            for is_windows, exe_name in ((True, "flash_tool.exe"), (False, "flash_tool")):
                paths.IS_WINDOWS, paths.IS_MAC = is_windows, False
                sp_dir = root / f"sp_{exe_name}"
                sp_dir.mkdir()
                (sp_dir / exe_name).write_bytes(b"")
                da = sp_dir / "MTK_AllInOne_DA.bin"
                da.write_bytes(b"")
                cmd = terminal_install.sp_flash_tool_command(scatter, sp_dir=sp_dir)
                assert cmd is not None
                assert cmd[0].endswith(exe_name), cmd
                assert cmd[1:] == [
                    "-c", "format-download", "-s", str(scatter),
                    "-d", str(da), "-t", "without", "-r",
                ], cmd
                sp_dirs[is_windows], sp_cmds[is_windows] = sp_dir, cmd

            # The guided flow's own build lookup drives the SP command, so the
            # terminal gets exactly what the app would have executed.
            from src import linux_sp_flash

            orig_find = paths.find_sp_flash_tool
            orig_stage = linux_sp_flash.stage_dir
            try:
                paths.IS_WINDOWS, paths.IS_MAC = True, False
                paths.find_sp_flash_tool = lambda: sp_dirs[True]
                assert terminal_install.build_install_command(
                    METHOD_SP, extract, scatter
                ) == sp_cmds[True]

                paths.IS_WINDOWS, paths.IS_MAC = False, False
                linux_sp_flash.stage_dir = lambda: sp_dirs[False]
                assert terminal_install.build_install_command(
                    METHOD_SP, extract, scatter
                ) == sp_cmds[False]
            finally:
                paths.find_sp_flash_tool = orig_find
                linux_sp_flash.stage_dir = orig_stage
        finally:
            paths.IS_WINDOWS, paths.IS_MAC = orig_win, orig_mac

        # macOS has no SP build: the command falls back to MTKClient's CLI.
        if paths.IS_MAC:
            assert terminal_install.sp_flash_tool_command(scatter) is None
        mtk = terminal_install.mtkclient_command(extract, scatter, "MT6572", pkg)
        assert mtk[0] == sys.executable, mtk
        assert mtk[-5:] == [
            "--flash-cli", str(extract), str(scatter), "MT6572", pkg
        ], mtk
        if paths.IS_MAC:
            # The SP method is requested but only MTKClient exists on macOS.
            assert terminal_install.build_install_command(
                METHOD_SP, extract, scatter, package_path=pkg, platform_name="MT6572"
            ) == mtk, "no SP build -> MTKClient fallback"
        assert terminal_install.build_install_command(
            METHOD_MTK, extract, scatter, package_path=pkg, platform_name="MT6572"
        ) == mtk

        # The launcher script carries the exact command plus a closing pause.
        script_root = root / "scripts"
        script_root.mkdir()
        orig_script_dir = terminal_install.script_dir
        terminal_install.script_dir = lambda: script_root
        try:
            posix = terminal_install.build_script(mtk, title="Terminal Install", cwd=extract, is_windows=False)
            assert posix.name.startswith("install_from_terminal"), posix
            ptext = posix.read_text(encoding="utf-8")
            assert ptext.startswith("#!/bin/bash"), ptext
            assert "--flash-cli" in ptext and str(scatter) in ptext, ptext
            assert str(extract) in ptext, ptext
            assert terminal_install.POSIX_DONE_MSG in ptext, ptext
            assert os.access(posix, os.X_OK), "launcher must be executable"

            win = terminal_install.build_script(
                ["C:\\sp\\flash_tool.exe", "-c", "format-download"],
                title="Terminal Install",
                cwd=str(extract),
                is_windows=True,
            )
            assert win.suffix == ".bat", win
            wtext = win.read_text(encoding="utf-8")
            assert wtext.startswith("@echo off"), wtext
            assert "pause" in wtext and "flash_tool.exe" in wtext, wtext
        finally:
            terminal_install.script_dir = orig_script_dir

        # Opening goes through the platform terminal, never through a shell.
        assert terminal_install.terminal_argv("/tmp/x.command", platform="darwin") == [
            "open", "-a", "Terminal", "/tmp/x.command"
        ]
        win_argv = terminal_install.terminal_argv(r"C:\x.bat", platform="win32")
        assert win_argv[:4] == ["cmd", "/c", "start", "Terminal install"], win_argv
        assert win_argv[-2:] == ["/k", r"C:\x.bat"], win_argv

        # --- app integration: the toggle reroutes package selection ----------
        app = QApplication.instance() or QApplication(sys.argv)
        w = MainWindow()
        w.show()
        started = []
        w.service.start_flash = lambda *a, **kw: started.append(a)
        w.service.start_device_monitor = lambda: None
        w.service.stop_device_monitor = lambda: None

        cb = w._settings_page._cb_terminal_install
        assert not cb.isChecked(), "terminal install must be opt-in by default"
        assert cb.text() == tr("settings_terminal_install")
        assert cb.toolTip() == settings_page.terminal_install_desc()
        assert w._settings_page._terminal_desc.text() == settings_page.terminal_install_desc()

        import src.flash_service as flash_service
        import src.ui.main_window as mw

        opened, warned = [], []
        orig_completed = mw.completed_extract_dir
        orig_compute = flash_service.compute_extract_dir
        orig_open = terminal_install.open_in_terminal
        orig_warning = mw.QMessageBox.warning
        mw.completed_extract_dir = lambda p: str(extract)
        flash_service.compute_extract_dir = lambda p: str(extract)
        terminal_install.script_dir = lambda: script_root
        terminal_install.open_in_terminal = lambda script, platform=None: (
            opened.append(Path(script)) or True
        )
        mw.QMessageBox.warning = staticmethod(lambda *a, **kw: warned.append(a))
        try:
            cb.setChecked(True)
            assert device_tracking.terminal_install_enabled(w.settings)

            w._on_package_selected(pkg, "Rockbox (Y1)", "Y1")
            app.processEvents()

            assert not started, "terminal install must not launch the guided backend"
            assert not w._install_run_active()
            assert w._stack.currentIndex() == _PAGE_SELECT, "stays on Select Software"
            assert w._nav_buttons["nav_select_package"][0].text() == "Select Software"
            assert len(opened) == 1, opened
            text = opened[0].read_text(encoding="utf-8")
            assert "--flash-cli" in text and str(scatter) in text, text
            assert w.statusBar().currentMessage() == tr(
                "status_terminal_install"
            ).format(path=str(opened[0]))
            assert any(
                line.startswith("Terminal install command:") for line in w._log_lines
            ), w._log_lines[-3:]

            # No scatter in the package -> warn, stay put, open nothing.
            scatter.unlink()
            warned.clear()
            opened.clear()
            w._on_package_selected(pkg, "Rockbox (Y1)", "Y1")
            assert warned and warned[-1][2] == tr("terminal_install_no_scatter"), warned
            assert not opened and w._stack.currentIndex() == _PAGE_SELECT

            # No console entry point at all -> warn instead of a silent no-op.
            scatter.write_text("platform: MT6572\n")
            orig_build = terminal_install.build_install_command
            terminal_install.build_install_command = lambda *a, **kw: None
            warned.clear()
            try:
                w._on_package_selected(pkg, "Rockbox (Y1)", "Y1")
            finally:
                terminal_install.build_install_command = orig_build
            assert warned and warned[-1][2] == tr("terminal_install_no_tool"), warned

            # Terminal refuses to open -> the script path is still reported.
            terminal_install.open_in_terminal = lambda script, platform=None: False
            warned.clear()
            w._on_package_selected(pkg, "Rockbox (Y1)", "Y1")
            assert warned, "a failed terminal launch must be reported"
            expected_script = script_root / (
                "install_from_terminal.bat"
                if os.name == "nt"
                else "install_from_terminal" + (".command" if sys.platform == "darwin" else ".sh")
            )
            assert warned[-1][2] == tr("terminal_install_failed").format(
                path=str(expected_script)
            ), warned
            assert expected_script.is_file(), "the command must be saved for manual runs"

            # Unticking the box returns to the guided install flow.
            terminal_install.open_in_terminal = orig_open
            cb.setChecked(False)
            assert not device_tracking.terminal_install_enabled(w.settings)
            w._on_package_selected(pkg, "Rockbox (Y1)", "Y1")
            assert started, "guided flow resumes when terminal install is off"
            assert w.sm.state is FlashState.S2_WAIT_CONNECTION, w.sm.state
        finally:
            mw.completed_extract_dir = orig_completed
            flash_service.compute_extract_dir = orig_compute
            terminal_install.open_in_terminal = orig_open
            terminal_install.script_dir = orig_script_dir
            mw.QMessageBox.warning = orig_warning

        w.close()
        app.processEvents()

    _reset_app_settings()


def test_flash_method_switch():
    """Switching the install method in Settings while waiting restarts the run
    with the new backend, and the choice is persisted. Legacy "auto" resolves
    to the platform default (SP Flash Tool) rather than its own mode."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow
    from src import paths

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
    exp_method = "mtk" if paths.IS_MAC else "sp"
    assert w._flash_page.current_method() == exp_method, f"legacy auto -> {exp_method}"

    if not paths.IS_MAC:
        # User switches to MTKClient while the backend searches (Windows/Linux).
        w._settings_page.reveal_advanced_methods()
        combo = w._settings_page._method_combo
        combo.setCurrentIndex(combo.findData("mtk"))
        assert w._settings_page.current_method() == "mtk"
        assert calls[-1] == ("C:/fake/rom.zip", "mtk"), "backend restarted with new method"
        assert w._flash_page.current_method() == "mtk", "flash page reflects the new method"
        assert w.settings.value("flash_method") == "mtk"
    else:
        # On macOS, MTKClient is the only engine; verify it is the active method.
        assert w._settings_page.current_method() == "mtk"
        assert w._flash_page.current_method() == "mtk"

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
    from src import paths

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
    assert "connecter votre Y1" in w._flash_page._wait_banner.text() or "assurer que votre Y1 est éteint" in w._flash_page._wait_banner.text()

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
    exp_method_text = "SP Flash Tool" if not paths.IS_MAC else "MTKClient"
    assert w._settings_page._method_combo.itemText(0) == exp_method_text

    _reset_app_settings()
    w.close()
    app.processEvents()


def test_success_dialog_flow():
    """After a successful install the donation modal is shown directly (no
    separate completion screen); the completion dialog only appears when the
    user has opted out of the donation modal. The flash-page banner flips to
    "Install complete" before either dialog opens."""
    from PySide6.QtWidgets import QApplication
    import src.ui.main_window as mw
    from src.flash_service import STEP_WRITE, STEP_DONE

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = mw.MainWindow()
    w.show()
    shown = []
    banner_during_dialog = []

    def _donation_stub(context="general"):
        banner_during_dialog.append(w._flash_page._flash_banner.text())
        shown.append(("donation", context))

    w._show_donation_dialog = _donation_stub
    real_complete = mw.FlashCompleteDialog

    class _StubDialog:
        def __init__(self, *a, **k):
            name = a[1] if len(a) > 1 else k.get("package_name", "")
            shown.append(("complete", name))

        def exec(self):
            banner_during_dialog.append(w._flash_page._flash_banner.text())
            return 1

    mw.FlashCompleteDialog = _StubDialog
    try:
        # Mirror the real flow: the flashing view is up with the in-progress
        # banner, then the backend reports DONE before the dialog opens.
        w.service.step_changed.emit(STEP_WRITE)
        assert w._flash_page._flash_banner.text() == "Install in Progress"
        w.service.step_changed.emit(STEP_DONE)
        assert w._flash_page._flash_banner.text() == "Install complete"

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

        # The top banner must read "Install complete" *while* the completion
        # dialog is over the page, not keep saying "Install in Progress".
        assert banner_during_dialog == ["Install complete", "Install complete"], banner_during_dialog
    finally:
        mw.FlashCompleteDialog = real_complete
        w.close()
        app.processEvents()


def test_dont_ask_again_disables_donation_ui():
    """Checking \"Don't ask me again\" on the completion Support dialog also
    turns off the donor / donation info: the bottom bar hides and the Settings
    option reflects it (the user is not interested in the donations model)."""
    from PySide6.QtWidgets import QApplication
    from src import device_tracking
    from src.donation_dialog import DonationDialog
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()
    assert device_tracking.is_donation_ui_disabled(w.settings) is False
    assert w.statusBar().isVisible()

    # Drive the real dialog path: tick the opt-out checkbox, then close.
    real_exec = DonationDialog.exec

    def _exec(dlg):
        dlg._dont_ask.setChecked(True)
        dlg._on_close()
        return 1

    DonationDialog.exec = _exec
    try:
        w._show_donation_dialog(context="install_success")
    finally:
        DonationDialog.exec = real_exec

    assert device_tracking.is_donation_install_prompt_disabled(settings=w.settings) is True
    assert device_tracking.is_donation_ui_disabled(settings=w.settings) is True
    assert not w.statusBar().isVisible(), "donor/goal bar must hide"
    assert w._settings_page._cb_hide_donations.isChecked() is True
    assert w._settings_page._cb_skip_install_donations.isChecked() is True

    w.close()
    app.processEvents()
    _reset_app_settings()


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
    assert "connect your device" in banner.lower() or "make sure your device is powered off" in banner.lower(), banner
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
    assert "## Solar" not in md, md
    assert "---" not in md
    assert "<pre" not in md
    assert "Highlights" in md and "Faster" in md

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





def test_rockbox_release_filters():
    """The Rockbox listing filters (old builds / nightly dev / 240p) live on the
    Settings screen and apply only when browsing Rockbox releases for Y1.

    240p builds are identified by their ``rom*_240p.zip`` asset, are hidden
    unless the toggle asks for them, and imply the older-builds flag because they
    cannot run on Y1 units below Innioasis OS 3.0.7."""
    from PySide6.QtWidgets import QApplication
    from src import device_tracking
    from src.catalog import FirmwarePackage, _parse_rom_asset_variant
    from src.ui.select_page import SelectPackagePage
    from src.ui.settings_page import SettingsPage

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    # --- persisted flags and the 240p / older-builds invariant -------------
    filters = device_tracking.rockbox_release_filters()
    assert filters.as_dict() == {
        device_tracking.FILTER_OLD_ROCKBOX: False,
        device_tracking.FILTER_NIGHTLY: False,
        device_tracking.FILTER_240P: False,
    }, filters.as_dict()

    filters = device_tracking.set_rockbox_release_filter(device_tracking.FILTER_240P, True)
    assert filters.rockbox_240p is True
    assert filters.old_rockbox is True, "240p builds must bring the older-builds flag"

    filters = device_tracking.set_rockbox_release_filter(device_tracking.FILTER_OLD_ROCKBOX, False)
    assert filters.old_rockbox is False and filters.rockbox_240p is False, filters.as_dict()

    try:
        device_tracking.set_rockbox_release_filter("not_a_filter", True)
    except ValueError:
        pass
    else:
        raise AssertionError("Unknown filter names must be rejected")

    # --- Settings screen owns the checkboxes -------------------------------
    page = SettingsPage()
    page.show()
    app.processEvents()
    assert page._rockbox_card.isVisible(), "the filters apply to online Updater CE releases"
    for cb in (page._cb_old_rockbox, page._cb_nightly, page._cb_240p):
        assert cb.text() != ""

    emissions = []
    page.release_filters_changed.connect(lambda: emissions.append(True))
    page._confirm_old_rockbox = lambda: True

    page._cb_240p.setChecked(True)
    assert page._cb_old_rockbox.isChecked(), "240p selection enables older builds"
    assert page.release_filters() == {
        device_tracking.FILTER_OLD_ROCKBOX: True,
        device_tracking.FILTER_NIGHTLY: False,
        device_tracking.FILTER_240P: True,
    }, page.release_filters()
    assert emissions, "the browser must be told to re-list releases"

    # Declining the compatibility warning leaves 240p off as well.
    page._cb_240p.setChecked(False)
    page._cb_old_rockbox.setChecked(False)
    page._confirm_old_rockbox = lambda: False
    page._cb_240p.setChecked(True)
    assert not page._cb_240p.isChecked()
    assert not page._cb_old_rockbox.isChecked()
    assert page.release_filters()[device_tracking.FILTER_240P] is False

    # Clearing the older-builds option clears 240p with it.
    page._confirm_old_rockbox = lambda: True
    page._cb_240p.setChecked(True)
    page._cb_old_rockbox.setChecked(False)
    assert not page._cb_240p.isChecked()
    assert page.release_filters()[device_tracking.FILTER_240P] is False

    # Nightly dev releases are independent, and persisted flags survive a reload.
    page._cb_nightly.setChecked(True)
    page._cb_nightly.setChecked(False)
    device_tracking.set_rockbox_release_filter(device_tracking.FILTER_240P, True)
    page.refresh_settings()
    assert page._cb_240p.isChecked() and page._cb_old_rockbox.isChecked()
    assert not page._cb_nightly.isChecked()

    # --- the online browser no longer shows them --------------------------
    select_page = SelectPackagePage()
    for gone in ("_options_bar", "_show_old_rockbox_cb", "_show_nightly_cb", "_show_240p_cb"):
        assert not hasattr(select_page, gone), f"{gone} should not be on the browser anymore"
    assert not hasattr(select_page, "_update_filter_checkboxes_visibility")

    select_page.current_software = lambda: "Original Software"
    select_page.current_model = lambda: "Y1"
    assert select_page.release_listing_filters() == (False, False, False)
    select_page.current_software = lambda: "Rockbox"
    assert select_page.release_listing_filters() == (True, False, True)
    select_page.current_model = lambda: "Y2"
    assert select_page.release_listing_filters() == (False, False, False), "Y1 only"

    # --- 240p builds are only listed when the toggle asks for them --------
    package = FirmwarePackage("rockbox-y1", "Rockbox", "Y1", "rockbox-y1/rockbox", "rom_360p.zip")

    def _asset(name):
        return {"name": name, "browser_download_url": f"https://example.test/{name}", "size": 10}

    def _variant(name, tag):
        return _parse_rom_asset_variant(_asset(name), tag, package.repo)

    only_240p = {
        "tag_name": "stable-v0.5",
        "rom_variants": [_variant("rom_240p.zip", "stable-v0.5")],
        "source_repo": package.repo,
    }
    mixed = {
        "tag_name": "stable-v0.6",
        "rom_variants": [
            _variant("rom_360p.zip", "stable-v0.6"),
            _variant("rom_240p.zip", "stable-v0.6"),
        ],
        "source_repo": package.repo,
    }
    client = catalog.ReleasesClient()
    client.get_all_releases = lambda repo, force_refresh=False: [only_240p, mixed]

    plain = client.releases_for_package(package, "Y1")
    assert [r["tag_name"] for r in plain] == ["stable-v0.6"], [r["tag_name"] for r in plain]
    assert plain[0]["asset_name"] == "rom_360p.zip", plain[0]["asset_name"]

    with_240p = client.releases_for_package(package, "Y1", prefer_240p=True)
    by_tag = {r["tag_name"]: r for r in with_240p}
    assert set(by_tag) == {"stable-v0.5", "stable-v0.6"}, sorted(by_tag)
    assert by_tag["stable-v0.5"]["asset_name"] == "rom_240p.zip"
    assert by_tag["stable-v0.6"]["asset_name"] == "rom_240p.zip"

    select_page.deleteLater()
    page.deleteLater()
    app.processEvents()
    _reset_app_settings()


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
        if fs.MTK_RESTART_IN_BROM:
            # A preloader-mode player is restarted into BROM before the DA upload,
            # and both modes are reported truthfully.
            assert sessions["count"] == 2, sessions
            mode_msgs = [m for m in actions if "detected" in m]
            assert mode_msgs[0] == "Device detected (preloader mode) - configuring download agent...", mode_msgs
            assert mode_msgs[-1] == "Device detected (BROM mode) - configuring download agent...", mode_msgs
            assert any("Restarting the player into BROM mode" in m for m in actions), actions
            # The deliberate restart must not reach the USB monitor as an unplug.
            assert holds and holds[0] >= 30, holds
        else:
            # Direct preloader flashing (MT6572 / Timmkoo A5 parity)
            assert sessions["count"] == 1, sessions
            mode_msgs = [m for m in actions if "detected" in m]
            assert mode_msgs[0] == "Device detected (preloader mode) - configuring download agent...", mode_msgs
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
    assert readiness["arch_ok"] == lsf.arch_supported()
    if lsf.files_ready(lsf.stage_dir()):
        assert readiness["libpng12_staged"] is True
        if sys.platform.startswith("linux"):
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
        with patch.object(Path, "is_file", lambda self: str(self).replace("\\", "/") == "/etc/doas.conf"):
            tools = lsf.find_available_escalation_tools()
            assert tools[0] == "doas", f"doas with /etc/doas.conf must be prioritized, got {tools}"
            assert "pkexec" in tools
            assert "sudo" in tools
            assert "run0" in tools

    # Test doas launcher with mock binary:
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        if sys.platform == "win32":
            mock_script = tdp / "mock_doas.bat"
            mock_script.write_text(
                "@echo off\n"
                'if "%1"=="-n" exit /b 0\n'
                'shift\n'
                'call %*\n'
            )
            runner_dummy = tdp / "dummy_runner.bat"
            runner_dummy.write_text("@echo off\necho Search USB, timeout 3600000 ms...\n")
        else:
            mock_script = tdp / "mock_doas"
            mock_script.write_text(
                "#!/bin/bash\n"
                'if [ "$1" = "-n" ]; then\n'
                "    exit 0\n"
                "fi\n"
                'exec "$@"\n'
            )
            mock_script.chmod(0o755)
            runner_dummy = tdp / "dummy_runner.sh"
            runner_dummy.write_text("#!/bin/bash\necho 'Search USB, timeout 3600000 ms...'\n")
            runner_dummy.chmod(0o755)
            orig_p = os.environ.get("PATH", "")
            doas_link = tdp / "doas"
            shutil.copy2(mock_script, doas_link)
            doas_link.chmod(0o755)
            try:
                os.environ["PATH"] = f"{tdp}{os.pathsep}{orig_p}"
                proc, cancelled = lsf._launch_with_doas(runner_dummy, [], tdp, os.environ.copy())
                assert cancelled is False
                assert proc is not None
                out_line = proc.stdout.readline()
                assert "Search USB" in out_line
                proc.wait()
            finally:
                os.environ["PATH"] = orig_p

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
    import sys
    from PySide6.QtWidgets import QApplication, QWidget

    app = QApplication.instance() or QApplication(sys.argv)
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


# Standard desktop controls that must keep their native rendering. The app
# stylesheet may target them only through an explicit scope (#id, [property]),
# never directly: an unscoped rule replaces Aqua/Win32/Adwaita painting with
# flat web-style widgets (regression: the Online / Local File tabs and every
# button were restyled app-wide).
_NATIVE_CONTROL_SELECTORS = (
    "QTabWidget", "QTabBar", "QPushButton", "QComboBox", "QLineEdit",
    "QTextEdit", "QPlainTextEdit", "QGroupBox", "QCheckBox", "QRadioButton",
    "QScrollBar", "QProgressBar", "QAbstractItemView", "QAbstractButton",
)


def _assert_no_unscoped_control_qss(qss: str):
    """Fail if the application stylesheet restyles a standard control directly."""
    import re

    assert not re.search(r"(^|\})\s*\*\s*\{", qss), "wildcard QSS rule defeats native font cascade"
    assert "QWidget {" not in qss, "QWidget rule styles every control in the app"

    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", qss):
        for selector in (s.strip() for s in match.group(1).split(",")):
            if not selector:
                continue
            if not any(sel in selector for sel in _NATIVE_CONTROL_SELECTORS):
                continue
            scoped = "#" in selector or "[" in selector
            assert scoped, f"unscoped control rule in app stylesheet: {selector!r}"


def _assert_controls_render_natively(app, dark_module):
    """Render standard controls with the app stylesheet and again with only the
    palette applied; identical output proves the stylesheet leaves them alone."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import (
        QCheckBox,
        QComboBox,
        QGroupBox,
        QLineEdit,
        QProgressBar,
        QPushButton,
        QRadioButton,
        QScrollBar,
        QTabWidget,
        QTextEdit,
        QWidget,
    )

    def _make(kind):
        if kind == "tabs":
            tabs = QTabWidget()
            tabs.addTab(QWidget(), "Online")
            tabs.addTab(QWidget(), "Local File")
            return tabs
        if kind == "button":
            return QPushButton("Install")
        if kind == "combo":
            combo = QComboBox()
            combo.addItems(["Y1", "Y2"])
            return combo
        if kind == "line":
            return QLineEdit("firmware.zip")
        if kind == "group":
            return QGroupBox("Device")
        if kind == "progress":
            bar = QProgressBar()
            bar.setValue(40)
            return bar
        if kind == "check":
            return QCheckBox("Verify images")
        if kind == "radio":
            return QRadioButton("Format + download")
        if kind == "scrollbar":
            return QScrollBar(Qt.Horizontal)
        return QTextEdit("log line")

    def _render(widget):
        widget.resize(
            max(widget.sizeHint().width(), 140), max(widget.sizeHint().height(), 26)
        )
        pixmap = QPixmap(widget.size())
        pixmap.fill(Qt.transparent)
        widget.render(pixmap)
        return pixmap.toImage()

    kinds = ("tabs", "button", "combo", "line", "group", "progress",
             "check", "radio", "scrollbar", "textedit")
    stylesheet = dark_module._build_qss()

    app.setStyleSheet("")
    try:
        baseline = {kind: _render(_make(kind)) for kind in kinds}
    finally:
        app.setStyleSheet(stylesheet)

    for kind in kinds:
        themed = _render(_make(kind))
        assert themed == baseline[kind], (
            f"{kind} is still restyled by the app stylesheet instead of the native style"
        )


def test_native_theming():
    """Verify native QStyle detection, typography, dual-theme contrast, and that
    standard controls are left to the native style rather than QSS."""
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
    assert "font-family:" in qss_dark  # monospace diagnostics view only
    assert "#navPanel" in qss_dark
    assert "cssClass=\"cardTitle\"" in qss_dark
    assert "cssClass=\"field-label\"" in qss_dark
    assert "min-height: 36px" in qss_dark, "Primary buttons must meet 36px touch point target"
    assert "min-height: 34px" in qss_dark, "Form controls/nav buttons must meet 34px touch target"
    # Standard controls must not be restyled app-wide (tabs, buttons, combos...).
    _assert_no_unscoped_control_qss(qss_dark)

    # Typography is applied through QFont: a QSS font-family rule defeats the
    # native font cascade and renders CJK text as tofu boxes.
    families = list(app.font().families())
    assert families, families
    if dark.IS_MACOS:
        assert "SF Pro Text" in families, families
    elif dark.IS_WINDOWS:
        assert "Segoe UI" in families, families

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
    _assert_no_unscoped_control_qss(qss_light)

    # The proof: every standard control paints identically with the app
    # stylesheet and with no stylesheet at all, i.e. natively.
    _assert_controls_render_natively(app, dark)

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
        paths.IS_MAC = True
        w_mac = fs.FlashWorker("test.zip", method="sp")
        called = []
        w_mac._flash_via_mtkclient = lambda *a: called.append("mtk")
        w_mac._flash_via_sp_flash_tool = lambda *a: called.append("sp")
        w_mac._dispatch_backend(Path("/tmp"), Path("/tmp/scatter.txt"))
        assert called == ["mtk"]

        # Simulate Windows with auto mode
        fs.IS_MAC = False
        paths.IS_MAC = False
        fs.IS_WINDOWS = True
        paths.IS_WINDOWS = True
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
        paths.IS_MAC = orig_mac
        fs.IS_WINDOWS = orig_win
        paths.IS_WINDOWS = orig_win


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
    assert "Unplug your device" in generic_steps
    assert "power button" in generic_steps

    custom_steps = install_power_on_steps("CustomPlayer")
    assert "Unplug your CustomPlayer" in custom_steps
    assert "power button" in custom_steps


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
    # The model fallback only runs when nothing ambient resolves first, so the
    # latest-package record and the real downloads cache are isolated: a running
    # app instance can otherwise plant an extracted ROM that wins the lookup.
    from src import device_tracking, downloads

    orig_latest = device_tracking.get_latest_package
    orig_downloads_dir = downloads.downloads_dir
    device_tracking.get_latest_package = lambda *a, **k: None
    empty_cache = tempfile.mkdtemp()
    downloads.downloads_dir = lambda: Path(empty_cache)
    try:
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
    finally:
        device_tracking.get_latest_package = orig_latest
        downloads.downloads_dir = orig_downloads_dir

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

    # 6. SettingsPage card and buttons (non-Windows: the Linux prep card)
    from src import paths
    orig_win = paths.IS_WINDOWS
    orig_mac = paths.IS_MAC
    paths.IS_WINDOWS = False
    paths.IS_MAC = False
    try:
        settings_page = SettingsPage()
        assert hasattr(settings_page, "_btn_run_checker")
        assert hasattr(settings_page, "_btn_launch_sp")
        assert settings_page._btn_run_checker.text() != ""
    finally:
        paths.IS_WINDOWS = orig_win
        paths.IS_MAC = orig_mac


def test_settings_platform_prep_cards():
    """The settings prep card is platform-specific: Linux users get the SP
    Flash Tool system checker, Windows users are told to install the MediaTek
    USB driver (Download Drivers -> innioasis.app/guide.html) and reboot. The
    MTKClient sales blurb is gone from the method notes."""
    from PySide6.QtWidgets import QApplication
    from src import browser, i18n, paths
    from src.flash_service import METHOD_MTK, METHOD_MTK_MAC, METHOD_SP
    from src.i18n import tr
    from src.ui.flash_page import FlashPage
    from src.ui.settings_page import MEDIATEK_DRIVERS_URL, SettingsPage

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    # The "MTKClient — the open-source MediaTek flasher…" line is removed.
    assert "flash_method_note_mtk" not in i18n._STRINGS
    for key in ("settings_driver_group", "settings_driver_desc", "settings_download_drivers_btn"):
        table = i18n._STRINGS.get(key)
        assert table, f"{key} missing from i18n"
        assert set(table) == {"zh-CN", "en", "fr", "es"}, (key, sorted(table))
    assert "MediaTek USB driver" in tr("settings_driver_desc")
    assert "reboot" in tr("settings_driver_desc")
    assert MEDIATEK_DRIVERS_URL == "https://innioasis.app/guide.html"

    # Windows: the driver card replaces the Linux SP Flash Tool prep card.
    orig_windows = paths.IS_WINDOWS
    paths.IS_WINDOWS = True
    try:
        page = SettingsPage()
        assert hasattr(page, "_btn_download_drivers")
        assert hasattr(page, "_driver_desc")
        assert not hasattr(page, "_btn_run_checker")
        assert not hasattr(page, "_btn_launch_sp")
        assert page._btn_download_drivers.text() == tr("settings_download_drivers_btn")
        assert tr("settings_driver_desc") in page._driver_desc.text()

        opened = []
        orig_open = browser.open_browser
        browser.open_browser = lambda url: opened.append(url)
        try:
            page._btn_download_drivers.click()
        finally:
            browser.open_browser = orig_open
        assert opened == [MEDIATEK_DRIVERS_URL], opened

        # Language switching retranslates the Windows card without touching
        # the Linux-only widgets that do not exist here.
        page.retranslate()
        assert page._btn_download_drivers.text() == tr("settings_download_drivers_btn")
        assert page._driver_desc.text() == tr("settings_driver_desc")
    finally:
        paths.IS_WINDOWS = orig_windows

    # MTKClient selections carry no explanatory note; SP Flash Tool still does.
    # macOS collapses every method to MTKClient, so check the multi-method
    # platforms explicitly.
    orig_mac = paths.IS_MAC
    orig_win_for_linux = paths.IS_WINDOWS
    paths.IS_MAC = False
    paths.IS_WINDOWS = False
    try:
        page = SettingsPage()
        assert hasattr(page, "_btn_run_checker")  # Linux prep card on this host
        page.set_method(METHOD_MTK)
        assert page._method_note.text() == "", page._method_note.text()
        assert page._method_note.isHidden()
        page.set_method(METHOD_MTK_MAC)
        assert page._method_note.text() == tr("flash_method_note_mtk_mac")
        assert not page._method_note.isHidden()
        page.set_method(METHOD_SP)
        assert page._method_note.text() == tr("flash_method_note_sp")
        assert not page._method_note.isHidden()

        flash_page = FlashPage()
        flash_page.set_method(METHOD_MTK)
        assert flash_page._method_note.text() == ""
        assert flash_page._method_note.isHidden()
        flash_page.set_method(METHOD_SP)
        assert flash_page._method_note.text() == tr("flash_method_note_sp")
        assert not flash_page._method_note.isHidden()
        flash_page.deleteLater()
        app.processEvents()
    finally:
        paths.IS_MAC = orig_mac
        paths.IS_WINDOWS = orig_win_for_linux

    # On macOS (MTKClient only) the blurb is gone: nothing to explain.
    paths.IS_MAC = True
    paths.IS_WINDOWS = False
    try:
        mac_page = SettingsPage()
        mac_page.set_method(METHOD_MTK)
        assert mac_page._method_note.text() == ""
        assert mac_page._method_note.isHidden()
        mac_flash_page = FlashPage()
        mac_flash_page.set_method(METHOD_MTK)
        assert mac_flash_page._method_note.text() == ""
        assert mac_flash_page._method_note.isHidden()
        mac_flash_page.deleteLater()
        app.processEvents()
    finally:
        paths.IS_MAC = orig_mac
        paths.IS_WINDOWS = orig_windows


def test_connectivity_monitor_reprobing():
    """The connectivity monitor must keep probing after a finished check.

    The probe thread used to be left referenced after Qt deleted it, so every
    later tick raised "Internal C++ object (ConnectivityCheckWorker) already
    deleted" — flooding the MTKClient install log — and monitoring stopped for
    the rest of the session."""
    import time

    from PySide6.QtWidgets import QApplication
    from src import network

    app = QApplication.instance() or QApplication(sys.argv)
    orig_probe = network.is_network_available
    probes = []

    def fake_probe(timeout=2.0):
        probes.append(timeout)
        return True

    network.is_network_available = fake_probe
    monitor = network.ConnectivityMonitor(interval_ms=50, initial_check=False)
    transitions = []
    monitor.connectivity_changed.connect(transitions.append)

    def _drain():
        deadline = time.time() + 5.0
        while monitor._worker is not None and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert monitor._worker is None, "finished probe thread was not released"

    try:
        monitor.check_now()
        _drain()
        assert monitor.is_online is True, monitor.is_online
        assert len(probes) == 1, probes
        assert transitions == [True], transitions

        # A later tick starts a fresh probe instead of poking a dead object.
        monitor.check_now()
        assert monitor._worker is not None
        _drain()
        assert len(probes) == 2, probes
        assert transitions == [True], transitions  # unchanged state: no re-emit

        # Timer-driven ticks stay healthy across several cycles.
        monitor.start_monitoring(interval_ms=50)
        deadline = time.time() + 5.0
        while len(probes) < 5 and time.time() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert len(probes) >= 5, probes
        monitor.stop_monitoring()
        assert not monitor._timer.isActive()
    finally:
        monitor.stop_monitoring()
        network.is_network_available = orig_probe


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
        ini_text1 = ini_file.read_text(encoding="utf-8")
        assert f"lastDir={da_file.resolve()}" in ini_text1
        assert f"lastDir={sc1.resolve()}" in ini_text1
        assert f"scatterHistory={sc1.resolve()}" in ini_text1
        if sys.platform != "win32":
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

        expected_hist = f"{sc2.resolve()},{sc1.resolve()}"
        assert f"scatterHistory={expected_hist}" in ini_file.read_text(encoding="utf-8")
        if sys.platform != "win32":
            qs2 = QSettings(str(ini_file), QSettings.IniFormat)
            assert qs2.value("LastDAFilePath/lastDir") == str(da_file.resolve())
            assert qs2.value("RecentOpenFile/lastDir") == str(sc2.resolve())
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
        if sys.platform != "win32":
            qs3 = QSettings(str(ini_file), QSettings.IniFormat)
            assert qs3.value("LastDAFilePath/lastDir") == str(da_file.resolve())
            assert os.path.isabs(qs3.value("LastDAFilePath/lastDir"))
            assert os.path.isabs(qs3.value("RecentOpenFile/lastDir"))
            raw3 = qs3.value("RecentOpenFile/scatterHistory")
            items3 = raw3 if isinstance(raw3, list) else [raw3]
            assert str((da_file.parent / "MT6572_Android_scatter.txt").resolve()) in items3
        else:
            ini3_text = ini_file.read_text(encoding="utf-8")
            assert f"lastDir={da_file.resolve()}" in ini3_text
            assert "MT6572_Android_scatter.txt" in ini3_text
            items3 = [str((da_file.parent / "MT6572_Android_scatter.txt").resolve())]
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
    assert device_label_for_model("Y1", "B") == "Y1"
    assert device_label_for_model("Y1", "A") == "Y1"
    assert "Type" not in device_label_for_model("Y1", "B")
    assert "Type" not in device_label_for_model("Y1", "A")
    assert device_label_for_model("") == "device"

    # 4. Disconnect & paperclip guidance
    guide_y1 = install_disconnect_guidance("Y1", "B")
    assert "paperclip" in guide_y1.lower() or "pin" in guide_y1.lower()
    assert "Y1" in guide_y1
    assert "Type" not in guide_y1

    guide_generic = install_disconnect_guidance("")
    assert "device" in guide_generic.lower()
    assert "Type" not in guide_generic

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

    # 8. Local files without model suffix resolve to empty model (generic 'device')
    m_generic, t_generic = detect_model_and_type_from_name("update.zip")
    assert m_generic == "" and t_generic is None
    m_rar, t_rar = detect_model_and_type_from_name("custom_firmware.rar")
    assert m_rar == "" and t_rar is None

    # 9. Verify device_label_for_model substitutes 'device' for empty, generic, or unknown
    assert device_label_for_model("") == "device"
    assert device_label_for_model("device") == "device"
    assert device_label_for_model(None) == "device"
    assert device_label_for_model("generic") == "device"

    # 10. Multi-language generic label verification
    from src.i18n import translator
    translator().set_language("zh-CN")
    assert device_label_for_model("") == "设备"
    guide_zh = install_disconnect_guidance("")
    assert "设备" in guide_zh
    assert "Type" not in guide_zh
    translator().set_language("en")


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
    app_dir = ROOT / "dist" / "Updater CE.app"
    if not app_dir.exists():
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
    exe_name = "Updater CE" if (app_dir / "Contents" / "MacOS" / "Updater CE").exists() else "Innioasis Updater CE"
    exe_path = app_dir / "Contents" / "MacOS" / exe_name
    _check_universal_slices(exe_path)

    # 2. Bundled libusb-1.0.dylib
    libusb_path = app_dir / "Contents" / "Frameworks" / "libusb-1.0.dylib"
    if not libusb_path.exists():
        libusb_path = app_dir / "Contents" / "Resources" / "libusb-1.0.dylib"
    if libusb_path.exists():
        _check_universal_slices(libusb_path)

    # 3. Bundled Python runtime executable (if standalone python runtime layout)
    python_bin = app_dir / "Contents" / "Resources" / "python" / "bin" / "python3.11"
    if python_bin.exists():
        _check_universal_slices(python_bin)
        site_packages = app_dir / "Contents" / "Resources" / "python" / "lib" / "python3.11" / "site-packages"
        cocoa_plugin = site_packages / "PySide6" / "Qt" / "plugins" / "platforms" / "libqcocoa.dylib"
        if cocoa_plugin.exists():
            _check_universal_slices(cocoa_plugin)
        qtwidgets = site_packages / "PySide6" / "QtWidgets.abi3.so"
        if qtwidgets.exists():
            _check_universal_slices(qtwidgets)
        app_code = app_dir / "Contents" / "Resources" / "app"
        assert (app_code / "launcher.py").exists(), "app/launcher.py missing"
        assert (app_code / "src" / "app.py").exists(), "app/src/app.py missing"

    # 4. Icon and assets
    assert (app_dir / "Contents" / "Resources" / "icon.icns").exists(), "icon.icns missing"
    assert (app_dir / "Contents" / "Info.plist").exists(), "Info.plist missing"

    # 5. No static libraries (which crash rcodesign)
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
    assert info.get("CFBundleExecutable") in ("Updater CE", "Innioasis Updater CE")


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
        # re-armed at step 1 ("Please make sure your {model} is powered off
        # and disconnected").
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
    assert device_label_for_model("Y1", "B") == "Y1"
    assert device_label_for_model("") == "device"
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

        if hasattr(page, "_releases_worker") and page._releases_worker and page._releases_worker.isRunning():
            page._releases_worker.wait(1000)
        app.processEvents()

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


def test_offline_online_tab_toggling():
    """When offline, only Local File tab is present. When online, Online tab is restored."""
    from PySide6.QtWidgets import QApplication
    from src.ui.select_page import SelectPackagePage

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    page = SelectPackagePage()

    # Force offline mode
    page.set_online_mode(False)
    app.processEvents()
    assert page._tabs.count() == 1, f"Expected 1 tab in offline mode, got {page._tabs.count()}"
    assert page._tabs.currentWidget() is page._local_tab

    # Restore online mode
    page.set_online_mode(True)
    app.processEvents()
    assert page._tabs.count() == 2, f"Expected 2 tabs in online mode, got {page._tabs.count()}"
    assert page._tabs.widget(0) is page._online_tab
    assert page._tabs.currentWidget() is page._online_tab

    page.deleteLater()
    app.processEvents()


def test_windows_m_key_shortcut_and_method_defaults():
    """Windows defaults exclusively to SP Flash Tool; pressing M unlocks MTKClient."""
    from PySide6.QtWidgets import QApplication
    from src.ui.settings_page import SettingsPage
    from src import paths

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    orig_win = paths.IS_WINDOWS
    orig_mac = paths.IS_MAC
    try:
        paths.IS_WINDOWS = True
        paths.IS_MAC = False
        sp = SettingsPage()

        # Windows default: SP Flash Tool only
        assert sp._available_methods() == ("sp",), sp._available_methods()

        # Unlock advanced methods (pressing M key)
        sp.reveal_advanced_methods()
        assert "mtk" in sp._available_methods()
        assert "sp" in sp._available_methods()

        sp.deleteLater()
    finally:
        paths.IS_WINDOWS = orig_win
        paths.IS_MAC = orig_mac
    app.processEvents()


def test_rockbox_360p_theme_pack():
    import tempfile
    import zipfile
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from src.theme_pack import (
        find_or_create_rockbox_dir,
        extract_theme_pack_zip,
        ThemePackGuidanceDialog,
    )
    from src.donation_dialog import DonationDialog
    from src.ui.dialogs import FlashCompleteDialog
    from src.i18n import tr

    app = QApplication.instance() or QApplication(sys.argv)

    # 1. Directory resolution with hidden dotfile resilience
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp).resolve()
        rb = root / ".rockbox"
        rb.mkdir()
        assert find_or_create_rockbox_dir(rb).resolve() == rb
        sub = root / "Music" / "Albums"
        sub.mkdir(parents=True)
        assert find_or_create_rockbox_dir(sub).resolve() == rb
        other = root / "Podcasts"
        other.mkdir()
        assert find_or_create_rockbox_dir(other).resolve() == rb

        # 2. Archive extraction and merging into target .rockbox
        zip_path = root / "test_theme.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("themes-themepack/.rockbox/themes/DarkNight.cfg", "cfg_data")
            zf.writestr("themes-themepack/.rockbox/fonts/16-GNU-Unifont.fnt", "fnt_data")
            zf.writestr("themes-themepack/.rockbox/backdrops/dark.bmp", "bmp_data")
        dest_rb = root / "test_dest_rb"
        count = extract_theme_pack_zip(zip_path, dest_rb)
        assert count == 3
        assert (dest_rb / "themes" / "DarkNight.cfg").read_text() == "cfg_data"
        assert (dest_rb / "fonts" / "16-GNU-Unifont.fnt").read_text() == "fnt_data"
        assert (dest_rb / "backdrops" / "dark.bmp").read_text() == "bmp_data"

    # 3. DonationDialog Theme Pack Offer
    d_360 = DonationDialog(
        context="install_success",
        model="Y1",
        software_name="Rockbox (Y1)",
        is_360p_rockbox=True,
    )
    assert hasattr(d_360, "_theme_box")
    assert hasattr(d_360, "_theme_btn")
    assert d_360._theme_btn.isEnabled()
    d_360._on_theme_pack_installed()
    assert not d_360._theme_btn.isEnabled()
    assert tr("themepack_installed_success") in d_360._theme_btn.text()
    d_360.close()

    # DonationDialog with is_360p_rockbox=False (e.g. 240p or non-Rockbox)
    d_240 = DonationDialog(
        context="install_success",
        model="Y1",
        software_name="Rockbox (240p)",
        is_360p_rockbox=False,
    )
    assert not hasattr(d_240, "_theme_box")
    d_240.close()

    # 4. FlashCompleteDialog Theme Pack Offer
    fc_360 = FlashCompleteDialog(
        package_name="Rockbox (Y2)",
        elapsed="01:15",
        model="Y2",
        is_360p_rockbox=True,
    )
    assert hasattr(fc_360, "_theme_box")
    assert hasattr(fc_360, "_theme_btn")
    fc_360._on_theme_pack_installed()
    assert not fc_360._theme_btn.isEnabled()
    fc_360.close()

    fc_other = FlashCompleteDialog(
        package_name="Innioasis Stock",
        elapsed="00:45",
        model="Y1",
        is_360p_rockbox=False,
    )
    assert not hasattr(fc_other, "_theme_box")
    fc_other.close()

    # 5. Guidance Dialog structure
    guide_dlg = ThemePackGuidanceDialog(model="Y1")
    assert hasattr(guide_dlg, "_select_btn")
    assert hasattr(guide_dlg, "_progress_bar")
    assert hasattr(guide_dlg, "_status_label")
    guide_dlg.close()
    app.processEvents()


def test_updater_ce_branding():
    from PySide6.QtWidgets import QApplication
    from src.config import APP_NAME, APP_VERSION
    from src.i18n import translator, tr
    from src.ui.main_window import MainWindow

    assert APP_NAME == "Updater CE"

    # Verify translations across all supported languages
    t = translator()
    for lang in ("zh-CN", "en", "fr", "es"):
        t.set_language(lang)
        assert tr("app_name") == "Updater CE", f"app_name in {lang} should be 'Updater CE', got {tr('app_name')}"
        assert "Updater CE" in tr("donate_title"), f"donate_title in {lang} should mention 'Updater CE'"
    t.set_language("en")

    # Verify MainWindow sidebar and window title
    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()

    assert w.windowTitle().startswith(f"Updater CE v{APP_VERSION}")
    assert w._brand_label.text() == "Updater CE"

    # Verify icon label
    assert w._icon_label is not None
    pm = w._icon_label.pixmap()
    assert pm is not None and not pm.isNull(), "App icon pixmap should be present and non-null"
    assert pm.width() <= 32 and pm.height() <= 32

    # Verify version badge: beside the brand title, no "v", brand-sized, and the
    # line underneath the title is attribution only.
    import re

    def _font_size_px(widget):
        m = re.search(r"font-size:\s*(\d+)px", widget.styleSheet())
        return int(m.group(1)) if m else None

    ver_text = w._version_label.text()
    brand_ver = getattr(w, "_brand_version", None)
    assert brand_ver is not None, "MainWindow should expose the sidebar version badge"
    brand_ver_text = brand_ver.text()
    assert brand_ver_text == APP_VERSION, f"Expected bare version, got {brand_ver_text!r}"
    assert _font_size_px(brand_ver) == _font_size_px(w._brand_label), (
        f"Version font size {_font_size_px(brand_ver)} should match brand "
        f"{_font_size_px(w._brand_label)}"
    )
    assert APP_VERSION not in ver_text, f"Version should not sit under the title: {ver_text!r}"
    assert "by" in ver_text
    assert 'href="https://ko-fi.com/teamslide"' in ver_text
    assert "Ryan Specter" in ver_text

    # No sidebar label may render the version with a "v" prefix.
    from PySide6.QtWidgets import QLabel
    for lbl in w._nav_panel.findChildren(QLabel):
        assert f"v{APP_VERSION}" not in lbl.text(), (
            f"Sidebar label still shows 'v{APP_VERSION}': {lbl.text()!r}"
        )

    # Verify link click dispatches to open_browser
    opened_urls = []
    import src.browser
    orig_open = src.browser.open_browser
    src.browser.open_browser = lambda url: opened_urls.append(url)
    try:
        w._version_label.linkActivated.emit("https://ko-fi.com/teamslide")
        assert opened_urls == ["https://ko-fi.com/teamslide"], f"Expected URL opened, got {opened_urls}"
    finally:
        src.browser.open_browser = orig_open

    w.close()
    app.processEvents()


def test_scatter_discovery_and_folder_packages():
    import tempfile
    from pathlib import Path
    from src.flash_service import find_scatter_files, _find_scatter, ScatterDiscoveryError
    from src.ui.select_page import SelectPackagePage

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Empty directory -> no scatters
        assert find_scatter_files(root) == []
        assert _find_scatter(root) is None

        # Nested single subfolder with scatter
        sub = root / "rom_nested"
        sub.mkdir()
        scat1 = sub / "MT6572_Android_scatter.txt"
        scat1.write_text("platform: MT6572\n")

        found = find_scatter_files(root)
        assert len(found) == 1
        assert found[0] == scat1
        assert _find_scatter(root) == scat1

        # Second scatter file in another folder -> multiple scatters error
        sub2 = root / "rom_nested_2"
        sub2.mkdir()
        scat2 = sub2 / "MT6580_Android_scatter.txt"
        scat2.write_text("platform: MT6580\n")

        found_multi = find_scatter_files(root)
        assert len(found_multi) == 2
        try:
            _find_scatter(root)
            assert False, "Should have raised ScatterDiscoveryError"
        except ScatterDiscoveryError as err:
            assert len(err.scatters) == 2

    # Verify SelectPackagePage handles multiple scatters gracefully
    sp = SelectPackagePage()
    # Trigger _on_package_prep_failed with MULTIPLE_SCATTERS error code
    err_msg = "MULTIPLE_SCATTERS: /tmp/s1.txt\n/tmp/s2.txt"
    # Monkeypatch QMessageBox.warning to intercept without blocking GUI
    from PySide6.QtWidgets import QMessageBox
    orig_warning = QMessageBox.warning
    warned = []
    QMessageBox.warning = lambda parent, title, text: warned.append((title, text))
    try:
        sp._on_local_prep_done(False, "", err_msg)
        assert len(warned) == 1
        assert "Multiple" in warned[0][0] or "Firmware" in warned[0][0] or "Logiciel" in warned[0][0] or "Firmwares" in warned[0][0]
    finally:
        QMessageBox.warning = orig_warning
        sp.close()


def test_generic_mtk_mode_and_offline_branding():
    from PySide6.QtWidgets import QApplication
    from src import config, i18n
    from src.i18n import tr
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    i18n.translator().set_language("en")

    # Initial standard state
    assert not config.is_generic_mtk()
    assert not config.is_offline_mode()
    assert not config.is_mediatek_installer()
    assert config.get_app_name() == "Updater CE"
    assert config.get_brand_name() == "Updater CE"

    w = MainWindow()
    w.show()
    app.processEvents()

    assert w.windowTitle().startswith("Updater CE")
    assert w._brand_label.text() == "Updater CE"
    assert w._select_page._tabs.count() >= 2
    assert w._select_page._tabs.tabBar().isVisible()
    assert w._support_btn.isVisible()
    assert w._credits_btn.isVisible()
    assert not w._settings_page._offline_mode_card.isHidden()
    assert not w._settings_page._rockbox_card.isHidden()

    # Toggle offline mode in Settings Page: this hides the online catalogue
    # only. The Updater CE brand must survive it untouched — offline mode is
    # not the MediaTek Installer.
    w._settings_page._cb_offline_mode.setChecked(True)
    app.processEvents()

    assert config.is_generic_mtk()
    assert config.is_offline_mode()
    assert not config.is_mediatek_installer()
    assert config.get_app_name() == "Updater CE"
    assert w.windowTitle().startswith("Updater CE")
    assert w._brand_label.text() == "Updater CE"
    assert not w._select_page._tabs.tabBar().isVisible()
    assert w._support_btn.isVisible()
    # Offline mode keeps local donations and credits visible (local donors.csv)
    w.statusBar().clearMessage()
    app.processEvents()
    assert w.statusBar().isVisible()
    assert w._credits_btn.isVisible()
    assert not w._settings_page._offline_mode_card.isHidden()
    # ...and nothing to filter, so the Rockbox release filters go too.
    assert w._settings_page._rockbox_card.isHidden()
    assert tr("settings_offline_mode") == "Offline Mode"
    for key in ("settings_offline_mode", "settings_offline_mode_group", "settings_offline_mode_desc"):
        for loc, text in i18n._STRINGS[key].items():
            assert "MediaTek" not in text, (key, loc, text)

    # Toggle off (the status bar shows an 'online listings unavailable'
    # message while offline, which takes the donation display — and its
    # credits link — over until it clears).
    w._settings_page._cb_offline_mode.setChecked(False)
    w.statusBar().clearMessage()
    app.processEvents()

    assert not config.is_generic_mtk()
    assert config.get_app_name() == "Updater CE"
    assert w.windowTitle().startswith("Updater CE")
    assert w._select_page._tabs.tabBar().isVisible()
    assert w._support_btn.isVisible()
    assert w._credits_btn.isVisible()
    assert not w._settings_page._rockbox_card.isHidden()

    w.close()
    app.processEvents()
    _reset_app_settings()


def test_mediatek_installer_mode():
    from PySide6.QtWidgets import QApplication
    from src import config, i18n
    from src.i18n import tr_brand
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    i18n.translator().set_language("en")
    assert not config.is_mediatek_installer()

    # Names: the app is "MediaTek Installer", shown in app as "Installer 3.0" —
    # never "MediaTek Firmware Installer" and never "Updater CE".
    for key in ("app_name_mediatek_installer", "app_name_mediatek_installer_short"):
        table = i18n._STRINGS[key]
        assert set(table) == {"zh-CN", "en", "fr", "es"}, (key, sorted(table))
    assert i18n._STRINGS["app_name_mediatek_installer"]["en"] == "MediaTek Installer"
    assert i18n._STRINGS["app_name_mediatek_installer_short"]["en"] == "Installer"
    assert "app_name_generic_mtk" not in i18n._STRINGS

    # Donation copy names this tool, not the CE catalogue, archive or gallery.
    for key in ("donate_title_mediatek", "donate_intro_general_mediatek", "donate_intro_success_mediatek"):
        table = i18n._STRINGS[key]
        assert set(table) == {"zh-CN", "en", "fr", "es"}, (key, sorted(table))
        for loc, text in table.items():
            assert "Updater CE" not in text, (key, loc)
    for key in ("donate_intro_general_mediatek", "donate_intro_success_mediatek"):
        for loc, text in i18n._STRINGS[key].items():
            for brand in ("Themes Gallery", "Community Firmware", "Galerie de Thèmes", "Galería de Temas"):
                assert brand not in text, (key, loc, brand)
    # CE keeps its own wording.
    assert "Updater CE" in i18n._STRINGS["app_name"]["en"]

    try:
        config.IS_MEDIATEK_INSTALLER = True
        assert config.is_mediatek_installer()
        assert config.is_offline_mode(), "the generic installer is offline-only"
        assert config.get_app_name() == "MediaTek Installer"
        assert config.get_brand_name() == "Installer"
        assert tr_brand("donate_title") == "Support MediaTek Installer"
        assert "MediaTek Installer" in tr_brand("donate_intro_general")

        w = MainWindow()
        w.show()
        app.processEvents()

        assert w.windowTitle() == f"MediaTek Installer v{config.APP_VERSION}", w.windowTitle()
        assert w._brand_label.text() == "Installer"
        assert w._brand_version.text() == config.APP_VERSION

        # Offline tool: no checkbox to re-enable online firmware, and no
        # CE-only affordances it cannot use.
        assert w._settings_page._offline_mode_card.isHidden()
        # An offline-only tool has no online releases to filter either.
        assert w._settings_page._rockbox_card.isHidden()
        assert not w._credits_btn.isVisible()
        assert not w._check_updates_btn.isVisible()
        assert not w._select_page._tabs.tabBar().isVisible()

        # Donations still shown, in this brand's wording.
        assert w._support_btn.isVisible()
        assert w.statusBar()._donations_enabled

        # The user's own donation opt-out still wins.
        w._settings_page._cb_hide_donations.setChecked(True)
        app.processEvents()
        assert not w._support_btn.isVisible()
        assert not w.statusBar()._donations_enabled
        w._settings_page._cb_hide_donations.setChecked(False)
        app.processEvents()
        assert w._support_btn.isVisible()

        # Terminal install remains available.
        assert not w._settings_page._terminal_card.isHidden()
        assert not w._settings_page._cb_terminal_install.isHidden()

        # Flipping the brand off switches the whole identity back.
        config.IS_MEDIATEK_INSTALLER = False
        w._apply_generic_mtk_branding()
        app.processEvents()
        assert w.windowTitle().startswith("Updater CE")
        assert w._brand_label.text() == "Updater CE"
        assert not w._settings_page._offline_mode_card.isHidden()
        assert not w._settings_page._rockbox_card.isHidden()
        assert w._credits_btn.isVisible()

        w.close()
        app.processEvents()
    finally:
        config.IS_MEDIATEK_INSTALLER = False
        _reset_app_settings()


def test_sp_flash_auth_file():
    """The generic build can hand SP Flash Tool an optional .auth file.

    Console mode has no --auth switch — the tool's own xsd and help page show
    the file travels in the console configuration file (-i) — so that is what
    the app writes when a user has chosen one, while the plain command line
    stays the default for the (usual) unauthenticated flash.
    """
    import shutil
    import subprocess
    import xml.etree.ElementTree as ET

    from PySide6.QtWidgets import QApplication
    from src import config, device_tracking, paths, sp_console_config, terminal_install
    from src.flash_service import METHOD_MTK, METHOD_SP, sp_flash_tool_console_args
    from src.i18n import tr
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)

    # --- the setting itself --------------------------------------------------
    assert device_tracking.sp_auth_file() == "", "no auth file by default"
    device_tracking.set_sp_auth_file("/tmp/custom.auth")
    assert device_tracking.sp_auth_file() == "/tmp/custom.auth"
    device_tracking.set_sp_auth_file("")
    assert device_tracking.sp_auth_file() == ""

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        extract = root / "rom_a5"
        extract.mkdir()
        scatter = extract / "MT6572_Android_scatter.txt"
        scatter.write_text(
            "########################################################\n"
            "- general: MTK_PLATFORM_CFG\n"
            "  info: \n"
            "    - config_version: V1.1.1\n"
            "      platform: MT6572\n"
            "      project: g368_nyx\n"
            "      storage: EMMC\n"
            "########################################################\n"
            "- partition_index: SYS0\n"
            "  partition_name: preloader\n"
            "  file_name: preloader_g368_nyx.bin\n"
            "  is_download: true\n",
            encoding="utf-8",
        )
        auth = root / "custom.auth"
        auth.write_bytes(b"\x00AUTH")
        da = root / "MTK_AllInOne_DA.bin"
        da.write_bytes(b"da")

        assert sp_console_config.parse_scatter_general(scatter) == {
            "platform": "MT6572",
            "storage": "EMMC",
        }
        assert sp_console_config.auth_file_usable(auth)
        assert not sp_console_config.auth_file_usable(root / "gone.auth")

        # Chip and storage must come from the scatter: guessing either would be
        # worse than flashing the way we always have.
        no_storage = root / "no_storage.txt"
        no_storage.write_text(
            "- general: MTK_PLATFORM_CFG\n  info: \n      platform: MT6572\n",
            encoding="utf-8",
        )
        no_platform = root / "no_platform.txt"
        no_platform.write_text(
            "- general: MTK_PLATFORM_CFG\n  info: \n      storage: EMMC\n",
            encoding="utf-8",
        )
        for bad_scatter, reason in (
            (no_storage, "missing_storage"),
            (no_platform, "missing_platform"),
        ):
            try:
                sp_console_config.build_console_config_xml(bad_scatter, da, auth)
            except sp_console_config.SpConfigError as e:
                assert e.reason == reason, (bad_scatter.name, e.reason)
            else:
                raise AssertionError(f"{bad_scatter.name} should not produce a config")
        try:
            sp_console_config.build_console_config_xml(scatter, da, "")
        except sp_console_config.SpConfigError as e:
            assert e.reason == "no_auth_file", e.reason
        else:
            raise AssertionError("a config without an auth file is pointless")

        # --- the generated console configuration -----------------------------
        config_path = sp_console_config.write_console_config(
            scatter, da, auth, target_dir=root / "sp"
        )
        assert config_path.name == "console_config.xml"
        root_el = ET.parse(config_path).getroot()
        assert root_el.tag == "flashtool-config" and root_el.get("version") == "2.0"
        general = root_el.find("general")
        # Element order is part of the schema's sequence.
        assert [child.tag for child in general] == [
            "chip-name",
            "storage-type",
            "download-agent",
            "scatter",
            "authentication",
            "connection",
        ], [child.tag for child in general]
        assert general.findtext("chip-name") == "MT6572"
        assert general.findtext("storage-type") == "EMMC"
        assert general.findtext("download-agent") == str(da)
        assert general.findtext("scatter") == str(scatter)
        assert general.findtext("authentication") == str(auth)
        connection = general.find("connection")
        assert connection.get("type") == "BromUSB"
        # The documented equivalent of the CLI's -t without.
        assert connection.get("without-battery") == "true"
        assert [child.tag for child in root_el.find("commands")] == ["format-download"]

        # SP Flash Tool's own schema is the real check on that file.
        xsd = (
            Path(__file__).resolve().parent.parent
            / "tools"
            / "linux"
            / "SP_Flash_Tool_v5.1904_Linux"
            / "console_mode.xsd"
        )
        assert xsd.is_file(), "the bundled console_mode.xsd is the contract here"
        if shutil.which("xmllint"):
            res = subprocess.run(
                ["xmllint", "--noout", "--schema", str(xsd), str(config_path)],
                capture_output=True,
                text=True,
            )
            assert res.returncode == 0, res.stderr

        # --- console arguments (guided flow and terminal install agree) -------
        plain = sp_flash_tool_console_args(str(scatter), str(da))
        assert plain == [
            "-c", "format-download",
            "-s", str(scatter),
            "-d", str(da),
            "-t", "without",
            "-r",
        ], plain

        logged = []
        with_auth = sp_flash_tool_console_args(str(scatter), str(da), str(auth), logged.append)
        assert with_auth[:2] == ["-r", "-i"], with_auth
        assert Path(with_auth[2]).is_file(), with_auth
        assert any(str(auth) in line for line in logged), logged

        # A file that vanished is reported, and the flash still goes ahead.
        logged = []
        gone = sp_flash_tool_console_args(str(scatter), str(da), str(root / "gone.auth"), logged.append)
        assert gone == plain, gone
        assert logged and str(root / "gone.auth") in logged[0], logged
        assert logged[0] != "sp_auth_missing_file", "raw i18n keys never reach users"

        # ... and an unusable scatter falls back with an explanation, not a guess.
        logged = []
        fallback = sp_flash_tool_console_args(str(no_platform), str(da), str(auth), logged.append)
        assert fallback == sp_flash_tool_console_args(str(no_platform), str(da)), fallback
        assert logged and "platform" in logged[0], logged
        assert logged[0] != "sp_config_missing_platform", "raw i18n keys never reach users"

        # The terminal install builds the very same command.
        orig_windows, orig_mac = paths.IS_WINDOWS, paths.IS_MAC
        import src.linux_sp_flash as linux_sp_flash

        orig_stage = linux_sp_flash.stage_dir
        try:
            paths.IS_WINDOWS, paths.IS_MAC = False, False
            stage = root / "sp_stage"
            stage.mkdir()
            (stage / linux_sp_flash.FLASH_TOOL_LINUX_BIN).write_text("#!/bin/sh\n")
            linux_sp_flash.stage_dir = lambda: stage
            cmd = terminal_install.sp_flash_tool_command(
                scatter, sp_dir=stage, da_file=da, auth_file=str(auth)
            )
            assert cmd[:3] == [str(stage / linux_sp_flash.FLASH_TOOL_LINUX_BIN), "-r", "-i"], cmd
            built = terminal_install.build_install_command(
                METHOD_SP, str(extract), scatter, auth_file=str(auth)
            )
            assert built[:3] == cmd[:3], (built, cmd)
            bare = terminal_install.sp_flash_tool_command(scatter, sp_dir=stage, da_file=da)
            assert bare[1:] == plain, bare
        finally:
            linux_sp_flash.stage_dir = orig_stage
            paths.IS_WINDOWS, paths.IS_MAC = orig_windows, orig_mac

        # --- the Settings control --------------------------------------------
        import src.ui.settings_page as settings_page

        orig_mtk = config.IS_MEDIATEK_INSTALLER
        orig_dialog = settings_page.QFileDialog.getOpenFileName
        try:
            config.IS_MEDIATEK_INSTALLER = True
            # macOS has no SP Flash Tool, so the option only exists off it.
            paths.IS_MAC = False
            w = MainWindow()
            w.show()
            app.processEvents()
            page = w._settings_page

            page.set_method(METHOD_MTK)
            app.processEvents()
            assert page._sp_auth_box.isHidden(), "MTKClient backend has no auth file"
            page.set_method(METHOD_SP)
            app.processEvents()
            assert not page._sp_auth_box.isHidden(), "SP Flash Tool offers one"
            assert page._sp_auth_label.text() == tr("settings_sp_auth")
            assert page._sp_auth_value.text() == "", "nothing chosen yet"
            assert page._sp_auth_value.placeholderText() == tr("settings_sp_auth_none")

            # Browsing stores the choice; clearing forgets it.
            settings_page.QFileDialog.getOpenFileName = (
                lambda *a, **kw: (str(auth), "")
            )
            page._on_sp_auth_browse()
            assert device_tracking.sp_auth_file() == str(auth)
            assert page._sp_auth_value.text() == str(auth)
            page._on_sp_auth_clear()
            assert device_tracking.sp_auth_file() == ""
            assert page._sp_auth_value.text() == ""

            # Not a CE feature: the checkbox-free generic build is where it lives.
            config.IS_MEDIATEK_INSTALLER = False
            w._apply_generic_mtk_branding()
            app.processEvents()
            assert page._sp_auth_box.isHidden(), "Updater CE hides it again"
            w.close()
            app.processEvents()
        finally:
            settings_page.QFileDialog.getOpenFileName = orig_dialog
            config.IS_MEDIATEK_INSTALLER = orig_mtk
            paths.IS_MAC = orig_mac

    _reset_app_settings()


def test_window_minimum_size_and_titlebar_stability():
    """Verify minimum window size (680x420), 800x500 default, layout compactness, and titlebar stability."""
    from PySide6.QtWidgets import QApplication
    from src.ui.main_window import MainWindow

    _reset_app_settings()
    app = QApplication.instance() or QApplication(sys.argv)
    w = MainWindow()
    w.show()
    app.processEvents()

    # 1. Minimum size constraint is 680 x 420 (allows free resizing above usable floor)
    assert w.minimumSize().width() == 680
    assert w.minimumSize().height() == 420

    # 2. Resizing to 800 x 500 default is allowed and pages fit within bounds
    w.resize(800, 500)
    app.processEvents()
    assert w.size().width() == 800
    assert w.size().height() == 500

    # 3. Switching to Settings expands height smoothly to fit settings content
    w._nav_to_page(4)  # Settings
    app.processEvents()
    assert w.size().width() == 800
    assert w.size().height() >= 500

    w._nav_to_page(0)  # Select
    app.processEvents()
    assert w.size().width() == 800

    # 4. Flashing view fits without issues
    w._nav_to_page(1)  # Flash
    app.processEvents()
    assert w.size().width() == 800

    w._nav_to_page(0)  # Select
    app.processEvents()
    assert w.size().width() == 800

    # 5. Check macOS seamless titlebar properties if running on macOS
    if sys.platform == "darwin":
        from src.ui.glass import _get_nsview
        view = _get_nsview(w)
        if view:
            ns_win = view.window()
            if ns_win:
                assert ns_win.titlebarAppearsTransparent() is True

    w.close()
    app.processEvents()
    _reset_app_settings()


def test_os_standard_iconography():
    """Verify OS-standard iconography for install, cancel, settings, translate, diagnostics, etc."""
    from PySide6.QtWidgets import QApplication
    from src.ui.icons import get_symbol_icon, get_symbol_pixmap
    from src.ui.main_window import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    for sym in ["install", "cancel", "settings", "translate", "diagnostics", "update", "support", "tools", "file", "folder"]:
        icon = get_symbol_icon(sym, 16)
        pix = get_symbol_pixmap(sym, 16)
        assert not icon.isNull(), f"Icon {sym} is null"
        assert not pix.isNull(), f"Pixmap {sym} is null"

    w = MainWindow()
    assert not w._settings_btn.icon().isNull(), "Settings button icon missing"
    assert not w._log_btn.icon().isNull(), "Diagnostics button icon missing"
    assert not w._check_updates_btn.icon().isNull(), "Updates button icon missing"
    w.close()
    app.processEvents()


def main():
    print("== Neo updater smoke test ==")
    check("updater ce branding and navigation header", test_updater_ce_branding)
    check("rockbox 360p theme pack workflow", test_rockbox_360p_theme_pack)
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
    check("donation bar corner links", test_donation_bar_corner_links)
    check("diagnostics live update", test_diagnostics_live_update)
    check("tool output capture", test_tool_output_capture)
    check("sp internal log streamed", test_sp_internal_log_streamed)
    check("diagnostics finished install readback", test_diagnostics_finished_install_readback)
    check("diagnostics date and session filtering", test_diagnostics_time_filter)
    check("connectivity check logging", test_connectivity_check_logging)
    check("connectivity monitor reprobing", test_connectivity_monitor_reprobing)
    check("flash flow launch", test_flash_flow_launch)
    check("install nav entry during run", test_install_nav_entry_during_run)
    check("terminal install handoff", test_terminal_install_handoff)
    check("sp flash auth file", test_sp_flash_auth_file)
    check("flash method switch", test_flash_method_switch)
    check("language switch keeps screen", test_language_switch_keeps_screen)
    check("success dialog flow", test_success_dialog_flow)
    check("offline banner + generic model", test_offline_banner_and_generic_model)
    check("release list + notes", test_release_list_and_notes)
    check("release model filtering", test_release_model_filtering)
    check("rockbox release filters", test_rockbox_release_filters)
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
    check("title bar spacing", test_titlebar_spacing)
    check("live theme and accent refresh", test_theme_refresh_live)
    check("preloader raw wrapping and routing", test_preloader_raw_wrapping_and_routing)
    check("cross platform mtk payloads and backend dispatch", test_cross_platform_mtk_payloads_and_backend_dispatch)
    check("device tracking", test_device_tracking)
    check("check device updates", test_check_device_updates)
    check("settings page and dialogs", test_settings_page_and_dialogs)
    check("firmware release reminder install flow", test_firmware_release_reminder_install_flow)
    check("latest package tracking and history ini", test_latest_package_tracking_and_history_ini)
    check("prune extracted cache and reusing download", test_prune_extracted_cache_and_reusing_download)
    check("sp flash system checker and diagnostics", test_sp_flash_system_checker_and_diagnostics)
    check("settings platform prep cards", test_settings_platform_prep_cards)
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
    check("offline online tab toggling", test_offline_online_tab_toggling)
    check("windows m key shortcut and method defaults", test_windows_m_key_shortcut_and_method_defaults)
    check("scatter discovery and folder packages", test_scatter_discovery_and_folder_packages)
    check("generic MTK mode and offline branding", test_generic_mtk_mode_and_offline_branding)
    check("mediatek installer mode", test_mediatek_installer_mode)
    check("window minimum size and titlebar stability", test_window_minimum_size_and_titlebar_stability)
    check("os standard iconography", test_os_standard_iconography)
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
