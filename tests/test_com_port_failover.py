"""Tests for SP Flash Tool COM port error (1013) failover and reboot suggestion workflow."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import flash_service as fs
from src import device_tracking, paths
from src.i18n import tr, _STRINGS
from src.state import FlashState
from src.ui.main_window import MainWindow


def _app():
    return QApplication.instance() or QApplication(sys.argv)


def test_is_com_port_error_matches_variations():
    """is_com_port_error detects 1013 / COM port errors in various formats."""
    assert fs.is_com_port_error("S_COM_PORT_OPEN_FAIL (1013)")
    assert fs.is_com_port_error("ERROR : S_COM_PORT_OPEN_FAIL (1013)")
    assert fs.is_com_port_error("[COM] Failed to open COM port.")
    assert fs.is_com_port_error("COM_PORT_OPEN_FAIL")
    assert fs.is_com_port_error("1013")
    assert fs.is_com_port_error("SP_ERR_1013")
    assert fs.is_com_port_error("Exception: err_code[1013]")

    assert not fs.is_com_port_error("")
    assert not fs.is_com_port_error(None)
    assert not fs.is_com_port_error("S_FT_DOWNLOAD_FAIL (4008)")
    assert not fs.is_com_port_error("S_TIMEOUT (1042)")
    assert not fs.is_com_port_error("S_CHIP_TYPE_NOT_MATCH (3184)")


def test_com_port_reboot_suggestion_i18n():
    """err_com_port_reboot_suggestion must be translated across all supported languages."""
    table = _STRINGS["err_com_port_reboot_suggestion"]
    for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
        assert lang in table, f"Missing {lang}"
        assert table[lang].strip(), f"Empty string for {lang}"
        assert "1013" in table[lang]


def test_sp_1013_failover_to_mtk_then_reboot_suggestion(monkeypatch):
    """Default flow on Linux: SP Flash Tool gets 1013 -> attempts MTKclient -> MTKclient fails -> suggests reboot."""
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MAC", False)
    app = _app()
    settings = QSettings("innioasis", "updater")
    orig_pref = settings.value("flash_method")
    settings.setValue("flash_method", "sp")

    window = MainWindow()
    calls = []
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(method)
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True

    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Original Software 3.1.2 for Y1"
        window._package_model = "Y1"

        # 1. Start flash with SP Flash Tool
        window._flash_method = "sp"
        window._flash_page.set_method("sp")
        assert window._flash_method == "sp"

        # 2. SP Flash Tool fails with 1013
        window._handle_flash_failure("ERROR : S_COM_PORT_OPEN_FAIL (1013)")

        # Failover must have switched to MTKclient candidate and marked sp 1013 seen
        assert window._flash_method == "mtk"
        assert window._fallback_attempted is True
        assert window._sp_1013_seen is True
        assert window._power_off_prompt is True

        # 3. User continues to reattempt with MTKclient
        calls.clear()
        window._on_power_off_continue()
        assert calls == ["mtk"]
        assert window._flash_method == "mtk"

        # 4. MTKclient reattempt also fails
        window._handle_flash_failure("CLI_START_FAILED")

        # Now the reboot suggestion must be triggered
        assert window._reboot_suggestion_shown is True
        assert window.sm.state == FlashState.S5_FAILED
        assert "1013" in window._failure_title
        # The failure card explains the locked COM port and reboot suggestion
        desc_text = window._flash_page._eta_label.text()
        assert tr("err_com_port_reboot_suggestion") in desc_text or "1013" in desc_text

        # 5. User clicks retry: preferred method (SP Flash Tool) is restored, failover state cleared
        window._on_retry_flash()
        assert window._fallback_attempted is False
        assert window._sp_1013_seen is False
        assert window._flash_method == "sp"
    finally:
        if orig_pref is None:
            settings.remove("flash_method")
        else:
            settings.setValue("flash_method", orig_pref)
        window.close()
        app.processEvents()


def test_mtk_preference_failover_to_sp_1013_immediate_reboot_suggestion(monkeypatch):
    """User preferred MTKclient -> MTK fails -> SP Flash Tool gets 1013 -> immediate reboot suggestion (Linux)."""
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MAC", False)
    app = _app()
    settings = QSettings("innioasis", "updater")
    orig_pref = settings.value("flash_method")
    orig_adv = settings.value("advanced_settings")
    settings.setValue("advanced_settings", True)
    device_tracking.set_advanced_settings_enabled(True)
    settings.setValue("flash_method", "mtk")

    window = MainWindow()
    calls = []
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(method)
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True

    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Original Software 3.1.2 for Y1"
        window._package_model = "Y1"

        # 1. Preferred method is MTKclient
        assert window._preferred_flash_method() == "mtk"
        window._flash_method = window._preferred_flash_method()
        window._flash_page.set_method("mtk")

        # 2. MTKclient fails first
        window._handle_flash_failure("CONNECTION_FAILED")

        # Must offer failover to SP Flash Tool
        assert window._flash_method == "sp"
        assert window._fallback_attempted is True
        assert window._sp_1013_seen is False

        # 3. User continues to reattempt with SP Flash Tool
        calls.clear()
        window._on_power_off_continue()
        assert calls == ["sp"]
        assert window._flash_method == "sp"

        # 4. SP Flash Tool fails with COM port error 1013
        window._handle_flash_failure("S_COM_PORT_OPEN_FAIL (1013)")

        # Immediately suggested to reboot PC
        assert window._reboot_suggestion_shown is True
        assert window.sm.state == FlashState.S5_FAILED
        assert "1013" in window._failure_title
        desc_text = window._flash_page._eta_label.text()
        assert tr("err_com_port_reboot_suggestion") in desc_text or "1013" in desc_text

        # 5. User preference was NOT changed in settings
        assert settings.value("flash_method") == "mtk"

        # 6. Upon retry or restart, MTKclient is tried first again
        window._on_retry_flash()
        assert window._flash_method == "mtk"
        assert window._fallback_attempted is False
        assert window._sp_1013_seen is False
    finally:
        if orig_pref is None:
            settings.remove("flash_method")
        else:
            settings.setValue("flash_method", orig_pref)
        if orig_adv is None:
            settings.remove("advanced_settings")
        else:
            settings.setValue("advanced_settings", orig_adv)
        device_tracking.set_advanced_settings_enabled(bool(orig_adv))
        window.close()
        app.processEvents()


def test_app_restart_preserves_preference_and_tries_method_b_on_fail(monkeypatch):
    """Simulate app reboot: preferred method is tried first, then method B on fail (Linux)."""
    monkeypatch.setattr(paths, "IS_WINDOWS", False)
    monkeypatch.setattr(paths, "IS_MAC", False)
    app = _app()
    settings = QSettings("innioasis", "updater")
    orig_pref = settings.value("flash_method")
    orig_adv = settings.value("advanced_settings")
    settings.setValue("advanced_settings", True)
    device_tracking.set_advanced_settings_enabled(True)

    try:
        # Case A: Default / SP Flash Tool preference
        settings.setValue("flash_method", "sp")
        w1 = MainWindow()
        try:
            assert w1._preferred_flash_method() == "sp"
            assert w1._other_method("sp") == "mtk"
        finally:
            w1.close()

        # Case B: MTKclient preference
        settings.setValue("flash_method", "mtk")
        w2 = MainWindow()
        try:
            assert w2._preferred_flash_method() == "mtk"
            assert w2._other_method("mtk") == "sp"
        finally:
            w2.close()
    finally:
        if orig_pref is None:
            settings.remove("flash_method")
        else:
            settings.setValue("flash_method", orig_pref)
        if orig_adv is None:
            settings.remove("advanced_settings")
        else:
            settings.setValue("advanced_settings", orig_adv)
        device_tracking.set_advanced_settings_enabled(bool(orig_adv))
        app.processEvents()


def test_windows_sp_failure_shows_error_card_without_mtk_failover(monkeypatch):
    """On Windows, SP Flash Tool failures NEVER fail over to MTKclient or trap the user."""
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr(paths, "IS_MAC", False)
    app = _app()
    settings = QSettings("innioasis", "updater")
    orig_pref = settings.value("flash_method")
    settings.setValue("flash_method", "sp")

    window = MainWindow()
    calls = []
    window.service.start_flash = lambda pkg, pre="", method="auto", **kw: calls.append(method)
    window.service.start_device_monitor = lambda: None
    window.service.cancel_flash = lambda: None
    window.service.stop_device_monitor = lambda: None
    window._ensure_elevated_for_install = lambda: True

    try:
        window.show()
        window._package_path = "C:/fake/rom.zip"
        window._package_name = "Original Software 3.1.2 for Y1"
        window._package_model = "Y1"
        window._flash_method = "sp"
        window._flash_page.set_method("sp")

        # SP Flash Tool fails with an error (e.g. 1013 or 3145)
        window._handle_flash_failure("S_COM_PORT_OPEN_FAIL (1013)")

        # Must NOT fail over to MTKclient on Windows
        assert window._flash_method == "sp"
        assert window._fallback_attempted is False
        assert window._power_off_prompt is False
        assert window.sm.state == FlashState.S5_FAILED
        assert window._install_failed is True

        # Close must NOT be trapped or blocked by an active install confirmation
        closed = window.close()
        assert closed is True
    finally:
        if orig_pref is None:
            settings.remove("flash_method")
        else:
            settings.setValue("flash_method", orig_pref)
        window.close()
        app.processEvents()
