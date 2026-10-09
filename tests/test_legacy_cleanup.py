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
    """Verify safety invariants: InnioasisUpdater.app (no space) MUST NEVER be flagged."""
    assert is_protected_app(Path("/Applications/InnioasisUpdater.app")) is True
    assert is_protected_app(Path("~/Applications/InnioasisUpdater.app")) is True
    assert is_protected_app(Path("/Applications/InnioasisUpdater.APP")) is True
    assert is_protected_app(Path("/Applications/Updater CE.app")) is True
    assert is_protected_app(Path("/Applications/UpdaterCE.app")) is True

    # The legacy version WITH A SPACE is not protected:
    assert is_protected_app(Path("/Applications/Innioasis Updater.app")) is False
    assert is_protected_app(Path("~/Applications/Innioasis Updater.app")) is False


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


if __name__ == "__main__":
    test_protected_app_invariants()
    test_format_bytes()
    test_calc_dir_size()
    test_legacy_dialog_instantiation()
    print("All legacy cleanup tests passed!")
