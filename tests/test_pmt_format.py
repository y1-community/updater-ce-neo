"""Format All + Download is an opt-in mode for PMT-changed firmware."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("INNIOASIS_HEADLESS", "1")

from pathlib import Path

from PySide6.QtCore import QSettings

from src import keep_data, manifest, pmt_format, sp_console_config
from src.catalog import FirmwarePackage
from src.flash_service import (
    is_pmt_format_required_error,
    parse_flash_error,
    sp_flash_tool_console_args,
)


def _pkg(**overrides):
    values = {
        "slug": "pmt-y1",
        "name": "PMT Test",
        "model": "Y1",
        "repo": "example/pmt",
        "package_name": "rom.zip",
    }
    values.update(overrides)
    return FirmwarePackage(**values)


def test_the_manifest_flag_is_parsed_and_cached(tmp_path, monkeypatch):
    xml = (
        '<slidia_manifest><package name="PMT Test" device="Y1" '
        'repo="example/pmt" PMT_format_needed="true" /></slidia_manifest>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert entries[0].pmt_format_needed is True

    monkeypatch.setattr(manifest, "_cache_root", lambda: tmp_path)
    manifest.cache_manifest(entries)
    restored = manifest.load_cached_manifest()
    assert restored[0].pmt_format_needed is True


def test_an_absent_manifest_flag_keeps_download_mode():
    xml = (
        '<slidia_manifest><package name="Normal" device="Y1" '
        'repo="example/normal" /></slidia_manifest>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert entries[0].pmt_format_needed is False


def test_the_console_command_changes_only_the_mode():
    plain = sp_flash_tool_console_args("scatter.txt", "DA.bin")
    formatted = sp_flash_tool_console_args(
        "scatter.txt", "DA.bin", format_all=True
    )

    assert plain[:2] == ["-c", "download"]
    assert formatted[:2] == ["-c", "format-download"]
    assert plain[2:] == formatted[2:]


def test_an_auth_file_config_uses_format_download(tmp_path, monkeypatch):
    monkeypatch.setattr(sp_console_config, "config_dir", lambda: tmp_path)
    scatter = tmp_path / "scatter.txt"
    scatter.write_text("- platform: MT6572\nstorage: EMMC\n", encoding="utf-8")
    da = tmp_path / "DA.bin"
    da.write_bytes(b"da")
    auth = tmp_path / "custom.auth"
    auth.write_bytes(b"auth")

    args = sp_flash_tool_console_args(
        str(scatter), str(da), str(auth), format_all=True
    )
    config = Path(args[2]).read_text(encoding="utf-8")

    assert "<format-download/>" in config
    assert "<download-only/>" not in config


def test_error_8502_is_recognised_without_catching_other_failures():
    assert is_pmt_format_required_error("S_FT_FORMAT_PMT_FAIL (8502)")
    assert is_pmt_format_required_error(
        "PMT changed for the ROM; it must be downloaded"
    )
    assert not is_pmt_format_required_error("S_FT_DOWNLOAD_FAIL (4008)")

    _title, explanation = parse_flash_error("SP_ERR_8502")
    assert "Format All + Download" in explanation


def test_the_manifest_flag_blocks_keep_data_and_its_question():
    settings = QSettings()
    pkg = _pkg(pmt_format_needed=True)

    assert keep_data.keep_data_available("Y1", pkg, settings) is False
    assert keep_data.needs_confirmation("Y1", pkg, settings) is False


def test_the_no_keep_data_alias_is_parsed():
    xml = (
        '<slidia_manifest><package name="Locked" device="Y1" '
        'repo="example/locked" no_keep_data="true" /></slidia_manifest>'
    )
    entries = manifest.parse_manifest_xml(xml)
    assert entries[0].do_not_keep_data is True


def test_the_terminal_catalogue_reads_both_manifest_vetoes():
    from src.console import catalog as console_catalog

    xml = (
        b'<slidia_manifest><package name="Locked" device="Y1" '
        b'repo="example/locked" no_keep_data="true" '
        b'PMT_format_needed="true" /></slidia_manifest>'
    )
    pkg = console_catalog.parse_manifest(xml)[0]
    assert pkg.do_not_keep_data is True
    assert pkg.pmt_format_needed is True


def test_an_8502_record_blocks_keep_data_for_that_software(tmp_path):
    path = tmp_path / "settings.ini"
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    pkg = _pkg()

    assert keep_data.keep_data_available("Y1", pkg, settings) is True
    assert pmt_format.mark_pmt_format_required(pkg, settings=settings) is True
    assert keep_data.keep_data_available("Y1", pkg, settings) is False

    settings.sync()
    restored = QSettings(str(path), QSettings.Format.IniFormat)
    assert pmt_format.pmt_format_required(pkg, settings=restored) is True


def test_format_all_confirmation_copy_is_clean_across_english_dialects_and_catalogs(monkeypatch):
    """English locales must read updated _STRINGS copy, never stale XML console-mode jargon."""
    import json

    from src import i18n

    old_lang = i18n.translator().lang
    try:
        for code in ("en", "en-GB", "en-US"):
            i18n.translator().set_language(code)
            body = i18n.tr("format_all_confirm_body")
            assert "xml" not in body.lower(), (code, body)
            assert "console-mode" not in body.lower(), (code, body)
            assert "{software}" in body and "{model}" in body and "{data_effect}" in body

        # Even if a flat en-US catalog on disk held stale wording, _STRINGS wins
        # for English while dialect overrides (e.g. center/catalog) still apply.
        monkeypatch.setattr(
            i18n,
            "catalog",
            lambda code: {"format_all_confirm_body": "stale via XML console-mode run"}
            if code == "en-US"
            else {},
        )
        i18n.translator().set_language("en-US")
        assert "xml" not in i18n.tr("format_all_confirm_body").lower()
        assert i18n.tr("button_centre") == "center button"
    finally:
        i18n.translator().set_language(old_lang)

    catalog_dir = Path(__file__).resolve().parents[1] / "assets" / "i18n"
    for path in sorted(catalog_dir.glob("*.json")):
        if path.stem == "languages":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        text = str(data.get("format_all_confirm_body", ""))
        assert "xml" not in text.lower() and "console-mode" not in text.lower(), (
            path.stem,
            text,
        )


def test_confirmed_8502_format_all_survives_apply_pmt_format_state_for_same_software(
    tmp_path, monkeypatch
):
    """After SP Flash Tool error 8502 + confirmation, retrying keeps format_all=True
    even when the same software (e.g. an older Solar build) was already tracked."""
    from src.ui.main_window import MainWindow

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    pkg = _pkg(name="Solar", model="Y2", repo="thesolarproject/solar")

    # Simulate player already tracked as running Solar on Y2: pmt_format_required
    # normally returns False for a same-software reinstall.
    monkeypatch.setattr(pmt_format, "_installed_same_software", lambda *a, **k: True)
    assert pmt_format.pmt_format_required(pkg, model="Y2", settings=settings) is False

    class _FakeFlashPage:
        def __init__(self):
            self.format_all_calls = []

        def set_format_all_required(self, required, model=""):
            self.format_all_calls.append((bool(required), model))

    class _DummyWindow:
        def __init__(self):
            self.settings = settings
            self._package_model = "Y2"
            self._package_name = "Solar October 23 2026 for Y2"
            self._keep_data_pkg = pkg
            self._keep_data = True
            self._format_all_required = True
            self._format_all_confirmed_for_run = True
            self._flash_page = _FakeFlashPage()

        def _current_firmware_package(self):
            return pkg

    dummy = _DummyWindow()
    required = MainWindow._apply_pmt_format_state(dummy, pkg, "Y2")
    assert required is True
    assert dummy._format_all_required is True
    assert dummy._keep_data is False
    assert dummy._flash_page.format_all_calls == [(True, "Y2")]
