"""Verify that selecting a folder containing a *scatter*.txt file points directly
to the firmware without extraction or showing download in progress.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.flash_service import (
    ExtractWorker,
    completed_extract_dir,
    compute_extract_dir,
)
from src.ui.main_window import MainWindow, _PAGE_FLASH, _PAGE_SELECT
from src.ui.select_page import SelectPackagePage


def _create_scatter_firmware_dir(tmp_path: Path, name="y2_firmware") -> tuple[Path, Path]:
    fw_dir = tmp_path / name
    fw_dir.mkdir(parents=True, exist_ok=True)
    scatter = fw_dir / "MT6582_Android_scatter.txt"
    scatter.write_text(
        "################################################################################\n"
        "#  General Setting\n"
        "#    MT6582\n"
        "################################################################################\n"
        "- platform: MT6582\n"
        "  project: Y2\n"
        "- partition_index: SYS0\n"
        "  partition_name: boot\n"
        "  file_name: boot.img\n",
        encoding="utf-8",
    )
    (fw_dir / "boot.img").write_bytes(b"bootimgdata")
    return fw_dir, scatter


def test_compute_and_completed_extract_dir_for_scatter_folder(tmp_path):
    fw_dir, scatter = _create_scatter_firmware_dir(tmp_path)

    # For folder containing scatter:
    assert compute_extract_dir(fw_dir) == fw_dir
    assert completed_extract_dir(fw_dir) == str(fw_dir)

    # For scatter file directly:
    assert compute_extract_dir(scatter) == fw_dir
    assert completed_extract_dir(scatter) == str(fw_dir)


def test_extract_worker_does_not_copy_scatter_folder(tmp_path):
    fw_dir, scatter = _create_scatter_firmware_dir(tmp_path)

    worker = ExtractWorker(str(fw_dir))
    result_dir = worker._extract(str(fw_dir))

    # Must return fw_dir directly without copying to downloads
    assert Path(result_dir).resolve() == fw_dir.resolve()


def test_select_page_choose_folder_with_scatter_does_not_prepare(tmp_path):
    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    page = SelectPackagePage()
    app.processEvents()

    fw_dir, scatter = _create_scatter_firmware_dir(tmp_path)

    prep_signals = []
    page.prep_started.connect(lambda n, m: prep_signals.append((n, m)))

    with patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value=str(fw_dir)):
        page._on_choose_folder()

    app.processEvents()

    # prep_started must NOT have been emitted
    assert len(prep_signals) == 0, f"Expected 0 prep_started emissions, got {prep_signals}"
    assert page._prep_worker is None
    assert page._start_btn.isEnabled()
    assert Path(page._current_package_path).resolve() == Path(fw_dir).resolve()
    assert page._details_package is not None
    assert page._details_package.model == "Y2"


def test_select_page_choose_scatter_file_does_not_prepare(tmp_path):
    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    page = SelectPackagePage()
    app.processEvents()

    fw_dir, scatter = _create_scatter_firmware_dir(tmp_path)

    prep_signals = []
    page.prep_started.connect(lambda n, m: prep_signals.append((n, m)))

    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(scatter), "Scatter (*.txt)")):
        page._on_choose_file()

    app.processEvents()

    assert len(prep_signals) == 0
    assert page._prep_worker is None
    assert page._start_btn.isEnabled()
    assert Path(page._current_package_path).resolve() == Path(fw_dir).resolve()


def test_begin_flash_flow_with_scatter_folder_shows_connect_not_extracting(tmp_path):
    app = QApplication.instance() or QApplication(["--platform", "offscreen"])
    win = MainWindow()
    try:
        app.processEvents()

        fw_dir, scatter = _create_scatter_firmware_dir(tmp_path)

        win._package_path = str(fw_dir)
        win._package_name = "Installing software on Y2"
        win._package_model = "Y2"

        with patch.object(win.service, "start_flash") as mock_start_flash, \
             patch.object(win.service, "start_device_monitor"):
            win._begin_flash_flow()
            app.processEvents()

            # Must pass pre_extracted_dir pointing to fw_dir
            mock_start_flash.assert_called_once()
            call_kwargs = mock_start_flash.call_args[1]
            assert Path(call_kwargs["pre_extracted_dir"]).resolve() == fw_dir.resolve()

            # Must not be download_active
            assert not getattr(win, "_download_active", False)

            # Title should NOT say Download in Progress
            title = win._page_title(_PAGE_FLASH, install_active=False)
            assert "Download in Progress" not in title

            assert win._flash_page._stack.currentWidget() == win._flash_page._waiting_view
    finally:
        win.close()
        app.processEvents()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        test_compute_and_completed_extract_dir_for_scatter_folder(t / "1")
        test_extract_worker_does_not_copy_scatter_folder(t / "2")
        test_select_page_choose_folder_with_scatter_does_not_prepare(t / "3")
        test_select_page_choose_scatter_file_does_not_prepare(t / "4")
        test_begin_flash_flow_with_scatter_folder_shows_connect_not_extracting(t / "5")
    print("ALL SCATTER FOLDER SELECTION TESTS PASSED")
    os._exit(0)
