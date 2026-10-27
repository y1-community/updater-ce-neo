"""Tests for application icon in dialogs, manifest icon fallback precedence, and SP Flash Tool preloader bypass."""

from pathlib import Path
import pytest
from PySide6.QtWidgets import QApplication

from src.catalog import FirmwarePackage
from src.manifest import parse_manifest_xml
from src.release_icons import (
    icon_candidate_urls,
    release_image_fallback_urls,
    software_icon_urls,
)
from src.ui import icons
from src.ui.dialogs import UpdateAvailableDialog
from src.updates import UpdateInfo
import src.flash_service as fs


def _app():
    return QApplication.instance() or QApplication([])


def test_app_icon_pixmap_is_valid():
    app = _app()
    pix = icons.app_icon_pixmap(48)
    assert not pix.isNull()
    assert pix.width() > 0
    assert pix.height() > 0


def test_update_available_dialog_uses_app_icon_not_rocket():
    app = _app()
    info = UpdateInfo(
        tag="v3.0.0",
        name="Update 3.0.0",
        body="Bug fixes and improvements",
    )
    dialog = UpdateAvailableDialog(parent=None, info=info, current_version="2.5.0")
    try:
        assert dialog._icon_label.text() != "🚀"
        pix = dialog._icon_label.pixmap()
        assert pix is not None and not pix.isNull()
    finally:
        dialog.close()


def test_manifest_icon_precedence_over_image():
    xml = """<slidia_manifest>
        <package name="Test Software" repo="owner/repo" device="Y1"
                 icon="https://example.com/icon.png"
                 image="https://example.com/image.png"
                 icon_dark="https://example.com/icon_dark.png" />
    </slidia_manifest>"""
    packages = parse_manifest_xml(xml)
    assert len(packages) == 1
    pkg = packages[0]
    assert pkg.icon == "https://example.com/icon.png"
    assert pkg.image == "https://example.com/image.png"

    # Light mode: icon before image
    light_urls = software_icon_urls(pkg, dark=False)
    assert light_urls == [
        "https://example.com/icon.png",
        "https://example.com/image.png",
    ]

    # Dark mode: icon_dark before icon before image
    dark_urls = software_icon_urls(pkg, dark=True)
    assert dark_urls == [
        "https://example.com/icon_dark.png",
        "https://example.com/icon.png",
        "https://example.com/image.png",
    ]


def test_release_image_fallback_returns_manifest_icons():
    pkg = FirmwarePackage(
        slug="test-y1",
        name="Test",
        model="Y1",
        repo="owner/repo",
        package_name="rom.zip",
        icon="https://example.com/manifest_icon.png",
        image="https://example.com/manifest_image.png",
    )
    rel = {"tag_name": "v1.0.0", "source_repo": "owner/repo"}
    fallbacks = release_image_fallback_urls(rel, pkg, dark=False)
    assert fallbacks == [
        "https://example.com/manifest_icon.png",
        "https://example.com/manifest_image.png",
    ]


def test_write_scatter_without_preloader_disables_download_both_formats(tmp_path):
    # Format 1: With leading dash
    scatter1 = tmp_path / "MT6572_Android_scatter_dash.txt"
    scatter1.write_text(
        "- partition_index: SYS0\n"
        "  partition_name: PRELOADER\n"
        "  file_name: preloader.bin\n"
        "  is_download: true\n"
        "  type: SV5_BL_BIN\n"
        "- partition_index: SYS1\n"
        "  partition_name: MBR\n"
        "  file_name: MBR\n"
        "  is_download: true\n"
        "  type: NORMAL_ROM\n",
        encoding="utf-8",
    )
    staged1 = fs._write_scatter_without_preloader(scatter1)
    assert staged1 is not None and staged1.exists()
    content1 = staged1.read_text(encoding="utf-8")
    assert "partition_name: PRELOADER" in content1
    # PRELOADER must have is_download: false
    lines1 = content1.splitlines()
    preloader_idx = next(i for i, line in enumerate(lines1) if "PRELOADER" in line)
    assert "is_download: false" in lines1[preloader_idx + 2]
    # MBR must retain is_download: true
    mbr_idx = next(i for i, line in enumerate(lines1) if "MBR" in line)
    assert "is_download: true" in lines1[mbr_idx + 2]

    # Format 2: Without leading dash
    scatter2 = tmp_path / "MT6572_Android_scatter_nodash.txt"
    scatter2.write_text(
        "partition_index: SYS0\n"
        "partition_name: PRELOADER\n"
        "file_name: preloader.bin\n"
        "is_download: true\n"
        "type: SV5_BL_BIN\n"
        "partition_index: SYS1\n"
        "partition_name: MBR\n"
        "file_name: MBR\n"
        "is_download: true\n"
        "type: NORMAL_ROM\n",
        encoding="utf-8",
    )
    staged2 = fs._write_scatter_without_preloader(scatter2)
    assert staged2 is not None and staged2.exists()
    content2 = staged2.read_text(encoding="utf-8")
    lines2 = content2.splitlines()
    preloader_idx2 = next(i for i, line in enumerate(lines2) if "PRELOADER" in line)
    assert "is_download: false" in lines2[preloader_idx2 + 2]
    mbr_idx2 = next(i for i, line in enumerate(lines2) if "MBR" in line)
    assert "is_download: true" in lines2[mbr_idx2 + 2]


def test_inno_setup_skips_driver_install_when_already_present():
    iss_path = Path(__file__).parent.parent / "scripts" / "build_windows_installer.iss"
    assert iss_path.exists()
    content = iss_path.read_text(encoding="utf-8")

    # Detection logic must inspect the uninstaller
    assert "C:\\Program Files\\MediaTek\\SP Driver\\unins000.exe" in content
    assert "IsMediaTekDriverAlreadyInstalled" in content
    assert "ShouldInstallDriver" in content

    # Files and Run sections must gate DriverInstall.exe on ShouldInstallDriver
    assert 'Source: "{#MyRepoRoot}tools\\DriverInstall.exe"; DestDir: "{tmp}"; Flags: ignoreversion skipifsourcedoesntexist; Check: ShouldInstallDriver' in content
    assert 'Filename: "{tmp}\\DriverInstall.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART"; StatusMsg: "{cm:StepDriverInstall}"; Flags: runhidden waituntilterminated skipifdoesntexist; Check: ShouldInstallDriver; AfterInstall: MarkDriverInstalled' in content

    # CurStepChanged / InstallBundledDriver must also respect ShouldInstallDriver
    assert "if DriverInstalled or (not ShouldInstallDriver) then" in content

