"""Test the 8 goals from the 11th October sprint."""

from pathlib import Path
import tempfile
import zipfile
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import src.catalog as catalog
import src.config as config
from src.flash_service import ExtractWorker
from src.release_icons import load_local_package_icon_bytes
from src.i18n import tr, translator
from src.ui.settings_page import SettingsPage


def _get_qapp():
    return QApplication.instance() or QApplication([])


def test_rockbox_y2_prereleases_bypassed():
    """Verify that Rockbox Y2 prereleases are NOT filtered out when show_nightly is False."""
    pkg = catalog.FirmwarePackage("rockbox-y2", "Rockbox", "Y2", "spycemagic/y2-rockbox", "rom_y2.zip", "")
    assert config.resolve_firmware_repo("y1-community/rockbox-y2-rom") == "spycemagic/y2-rockbox"

    mock_releases = [
        {"tag_name": "v0.2.5-beta", "prerelease": True, "rom_variants": [{"model": "Y2", "resolution": "360p", "type": "A", "asset": {"browser_download_url": "http://example.com/1.zip", "name": "rom_y2.zip"}}]},
        {"tag_name": "v0.2.0-beta", "prerelease": True, "rom_variants": [{"model": "Y2", "resolution": "360p", "type": "A", "asset": {"browser_download_url": "http://example.com/2.zip", "name": "rom_y2.zip"}}]},
        {"tag_name": "v0.1.0-base", "prerelease": False, "rom_variants": [{"model": "Y2", "resolution": "360p", "type": "A", "asset": {"browser_download_url": "http://example.com/3.zip", "name": "rom_y2.zip"}}]},
    ]
    client = catalog.ReleasesClient()
    client.get_all_releases = lambda repo, **kwargs: mock_releases

    out = client.releases_for_package(pkg, "Y2", show_nightly=False)
    assert len(out) == 3, f"Expected 3 releases including prereleases, got {len(out)}"
    tags = [r["tag_name"] for r in out]
    assert "v0.2.5-beta" in tags
    assert "v0.2.0-beta" in tags


def test_offline_package_icon_detection():
    """Verify load_local_package_icon_bytes loads updater.png without full archive extraction."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        zip_file = tmp_path / "firmware.zip"
        
        # 1x1 png bytes
        fake_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82"
        with zipfile.ZipFile(zip_file, "w") as z:
            z.writestr("MT6572_Android_scatter.txt", "scatter")
            z.writestr("updater.png", fake_png)
            z.writestr("system.img", b"system" * 1000)

        # Test loading directly from zip
        data = load_local_package_icon_bytes(zip_file)
        assert data == fake_png

        # Test loading from extracted directory
        folder = tmp_path / "unpacked"
        folder.mkdir()
        (folder / "updater.png").write_bytes(fake_png)
        data_dir = load_local_package_icon_bytes(folder)
        assert data_dir == fake_png


def test_author_localization():
    """Verify author attribution is localized across languages."""
    translator().set_language("en")
    assert "by" in tr("brand_author_by").format(author="Ryan")
    translator().set_language("zh-CN")
    assert "作者：" in tr("brand_author_by").format(author="Ryan")
    translator().set_language("fr")
    assert "par" in tr("brand_author_by").format(author="Ryan")
    translator().set_language("es")
    assert "por" in tr("brand_author_by").format(author="Ryan")
    translator().set_language("de")
    assert "von" in tr("brand_author_by").format(author="Ryan")
    translator().set_language("ja")
    assert "作：" in tr("brand_author_by").format(author="Ryan")
    translator().set_language("en")


def test_advanced_settings_gating_and_md_shortcut(monkeypatch):
    """Verify Advanced Settings hides terminal/method cards by default and reveals on M/D or toggle."""
    import src.paths as paths
    monkeypatch.setattr(paths, "IS_MAC", False)
    _app = _get_qapp()
    page = SettingsPage()

    # Initial state: unchecked -> hidden
    page._cb_advanced_settings.setChecked(False)
    page._sync_advanced_visibility()
    assert page._terminal_card.isHidden()
    assert page._method_card.isHidden()
    assert "mtk_mac" not in page._available_methods()

    # Toggle on -> reveal the shared advanced MTKClient method
    page._cb_advanced_settings.setChecked(True)
    assert not page._terminal_card.isHidden()
    assert not page._method_card.isHidden()
    assert "mtk_mac" not in page._available_methods()
    assert "mtk" in page._available_methods()
    assert "sp" in page._available_methods()

    # Toggle off -> hidden
    page._cb_advanced_settings.setChecked(False)
    assert page._terminal_card.isHidden()
    assert page._method_card.isHidden()
    assert "mtk_mac" not in page._available_methods()

    # M/D reveals the same platform-neutral advanced method list.
    page.reveal_advanced_methods()
    assert page._cb_advanced_settings.isChecked()
    assert not page._terminal_card.isHidden()
    assert not page._method_card.isHidden()
    assert "mtk" in page._available_methods()
    assert "mtk_mac" not in page._available_methods()

    # Turning Advanced Settings off and back on keeps the method list unified.
    page._cb_advanced_settings.setChecked(False)
    page._cb_advanced_settings.setChecked(True)
    assert "mtk" in page._available_methods()
    assert "mtk_mac" not in page._available_methods()


def test_extract_worker_emits_100_percent():
    """Verify archive extraction emits 100% upon completion."""
    _app = _get_qapp()
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        zip_file = tmp_path / "firmware.zip"
        dest_dir = tmp_path / "extracted"

        with zipfile.ZipFile(zip_file, "w") as z:
            z.writestr("MT6572_Android_scatter.txt", "scatter")
            z.writestr("boot.img", b"boot" * 500)
            z.writestr("recovery.img", b"recovery" * 500)

        emitted_percentages = []
        worker = ExtractWorker(str(zip_file))
        worker.progress.connect(lambda pct: emitted_percentages.append(pct))
        worker.run()

        assert len(emitted_percentages) > 0
        assert emitted_percentages[-1] == 100, f"Expected final emission 100, got {emitted_percentages[-1]}"


def test_manufacturer_rom_detection_y1_y2():
    """Verify parse_manufacturer_rom_info correctly extracts Y1 and Y2 info."""
    y1_info = catalog.parse_manufacturer_rom_info("Y1_HVGA_V1.0.8_20220606.rar")
    assert y1_info is not None
    assert y1_info["model"] == "Y1"
    assert y1_info["version"] == "1.0.8"
    assert y1_info["date"] == "20220606"
    assert "Original Software 1.0.8 for Y1" in y1_info["title"]

    y2_info = catalog.parse_manufacturer_rom_info("Y2_V3.2.1_20220814.rar")
    assert y2_info is not None
    assert y2_info["model"] == "Y2"
    assert y2_info["version"] == "3.2.1"
    assert y2_info["date"] == "20220814"
    assert "Original Software 3.2.1 for Y2" in y2_info["title"]

    y2_extracted = catalog.parse_manufacturer_rom_info(".original-software-y2_3.2.1_rom_y2_extracted")
    assert y2_extracted is not None
    assert y2_extracted["model"] == "Y2"
    assert y2_extracted["version"] == "3.2.1"

    y1_simple = catalog.parse_manufacturer_rom_info("Y1-3.1.2.zip")
    assert y1_simple is not None
    assert y1_simple["model"] == "Y1"
    assert y1_simple["version"] == "3.1.2"


def test_device_monitor_priming_and_handoff():
    """Verify DeviceMonitor primes session and hands it to FlashService / FlashWorker."""
    from src.flash_service import DeviceMonitor, FlashService

    monitor = DeviceMonitor()
    mock_mtk = object()
    monitor.primed_mtk = mock_mtk

    taken = monitor.take_primed_mtk()
    assert taken is mock_mtk
    assert monitor.primed_mtk is None

    service = FlashService()
    service._device_monitor = monitor
    monitor.primed_mtk = mock_mtk
    assert service.take_primed_mtk() is mock_mtk
    assert service.take_primed_mtk() is None


def test_mtk_darwin_handshake_safe_read(monkeypatch):
    """Verify patched_run_handshake avoids small bulk IN reads on Darwin."""
    import sys
    import logging
    import src.mtk_api as mtk_api
    mtk_api._ensure_imports()
    from mtkclient.Library import Port

    read_calls = []
    class MockEPIn:
        wMaxPacketSize = 64
        def read(self, size, timeout=500):
            read_calls.append(size)
            curr_byte = len(read_calls)
            mapping = {1: b"\x5f", 2: b"\xf5", 3: b"\xaf", 4: b"\xfa"}
            return mapping.get(curr_byte, b"")

    class MockEPOut:
        def write(self, data, timeout=500):
            return len(data)

    class MockCDC:
        pid = 0x2000
        EP_IN = MockEPIn()
        EP_OUT = MockEPOut()
        def set_line_coding(self, *a, **k): pass
        def setcontrollinestate(self, *a, **k): pass

    port = Port.Port.__new__(Port.Port)
    port.info = lambda *a: None
    port.debug = lambda *a: None
    port.cdc = MockCDC()

    monkeypatch.setattr(sys, "platform", "darwin")
    ok = port.run_handshake(retries=1)
    assert ok is True
    assert all(sz >= 64 for sz in read_calls), f"Expected all reads >= 64, got {read_calls}"


