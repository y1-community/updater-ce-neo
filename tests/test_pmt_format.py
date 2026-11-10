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
