import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QWidget, QHBoxLayout

from src.ui.flash_page import (
    _RoundNextButton,
    _CardHead,
    _GlyphActions,
    ROUND_NEXT_BUTTON_PX,
)
from src.flash_service import build_mtk_write_plan
from src.ui.main_window import MainWindow


@pytest.fixture(autouse=True)
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


def test_action_buttons_use_arrow_cursor():
    btn_next = _RoundNextButton(symbol="next", accent=True)
    btn_back = _RoundNextButton(symbol="back", accent=False)
    btn_cancel = _RoundNextButton(symbol="cancel", accent=False)

    assert btn_next.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert btn_back.cursor().shape() == Qt.CursorShape.ArrowCursor
    assert btn_cancel.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_cardhead_and_glyph_actions_pair_buttons():
    b0 = _RoundNextButton(symbol="back", accent=False)
    b1 = _RoundNextButton(symbol="next", accent=True)
    actions = _GlyphActions([b0, b1])

    b0.show()
    b1.show()
    actions._sync_chips_pairing()

    assert b0._companion is b1
    assert b0._is_leading is True
    assert b1._companion is b0
    assert b1._is_leading is False

    # Hide one button -> pairing breaks
    b0.hide()
    actions._sync_chips_pairing()
    assert b0._companion is None
    assert b1._companion is None


def test_segmented_capsule_painter_fallback():
    b0 = _RoundNextButton(symbol="back", accent=False)
    b1 = _RoundNextButton(symbol="next", accent=True)
    b0.set_segmented_pair(b1, is_leading=True)
    b1.set_segmented_pair(b0, is_leading=False)

    img = QImage(ROUND_NEXT_BUTTON_PX * 2, ROUND_NEXT_BUTTON_PX, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    try:
        b0._paint_segmented_into(p)
        p.translate(ROUND_NEXT_BUTTON_PX, 0)
        b1._paint_segmented_into(p)
    finally:
        p.end()

    # Verify pixels are painted into both halves
    left_pixel = img.pixelColor(18, 18)
    right_pixel = img.pixelColor(ROUND_NEXT_BUTTON_PX + 18, 18)
    assert left_pixel.alpha() > 0
    assert right_pixel.alpha() > 0


def test_segmented_pair_detaches_standalone_chips():
    from src.ui.native_controls import NativeRoundChip

    b0 = _RoundNextButton(symbol="back", accent=False)
    b1 = _RoundNextButton(symbol="next", accent=True)

    # Simulate existing native chips on both buttons
    chip0 = NativeRoundChip(b0)
    chip1 = NativeRoundChip(b1)
    b0._native = chip0
    b1._native = chip1

    # Setting segmented pair must detach standalone native chips
    b0.set_segmented_pair(b1, is_leading=True)
    b1.set_segmented_pair(b0, is_leading=False)

    assert b0._native is None
    assert b1._native is None
    assert chip0._button is None
    assert chip1._button is None


def test_native_round_chip_respects_companion():
    from src.ui.native_controls import NativeRoundChip

    b0 = _RoundNextButton(symbol="back", accent=False)
    b1 = _RoundNextButton(symbol="next", accent=True)
    b0._companion = b1

    chip = NativeRoundChip(b0)
    assert chip.attach() is False

    # Also sync_frame on an attached chip must keep button hidden if companion is present
    dummy_btn = type("DummyButton", (), {"setHidden_": lambda self, h: setattr(self, "_hidden", h)})()
    dummy_btn._hidden = False
    chip._button = dummy_btn
    chip.sync_frame()
    assert dummy_btn._hidden is True


def test_native_segmented_pair_structure():
    from src.ui.native_controls import NativeSegmentedPair

    b0 = _RoundNextButton(symbol="back", accent=False)
    b1 = _RoundNextButton(symbol="next", accent=True)
    b0.set_segmented_pair(b1, is_leading=True)

    pair = NativeSegmentedPair(b0, b1)
    assert pair._control is None
    # Segmented controls use regular macOS styling without liquid glass backdrop
    assert getattr(pair, "_glass_backdrop", None) is None


def test_build_mtk_write_plan_includes_mbr_ebr(tmp_path):
    scatter_file = tmp_path / "MT6582_Android_scatter.txt"
    scatter_content = """
- partition_index: SYS0
  partition_name: PRELOADER
  file_name: preloader.bin
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
  linear_start_addr: 0x0
  physical_start_addr: 0x0
  partition_size: 0x80000
  region: EMMC_USER

- partition_index: SYS2
  partition_name: EBR1
  file_name: EBR1
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x80000
  physical_start_addr: 0x80000
  partition_size: 0x80000
  region: EMMC_USER

- partition_index: SYS3
  partition_name: BOOTIMG
  file_name: boot.img
  is_download: true
  type: NORMAL_ROM
  linear_start_addr: 0x100000
  physical_start_addr: 0x100000
  partition_size: 0x600000
  region: EMMC_USER
"""
    scatter_file.write_text(scatter_content.strip())
    (tmp_path / "preloader.bin").write_bytes(b"MTK_BIN" + b"\x00" * 1024)
    # Non-empty MBR / EBR with partition tables
    mbr_bytes = b"\x00" * 446 + b"\x80" + b"\x01" * 15 + b"\x00" * 48 + b"\x55\xaa"
    (tmp_path / "MBR").write_bytes(mbr_bytes)
    ebr_bytes = b"\x00" * 446 + b"\x00" + b"\x02" * 15 + b"\x00" * 48 + b"\x55\xaa"
    (tmp_path / "EBR1").write_bytes(ebr_bytes)
    (tmp_path / "boot.img").write_bytes(b"ANDROID!" + b"\x00" * 1000)

    plan = build_mtk_write_plan(scatter_file)
    names = [s["name"] for s in plan]
    assert "MBR" in names
    assert "EBR1" in names
    assert "BOOTIMG" in names
    assert names[0] == "PRELOADER"  # preloader_first=True


def test_is_sp_complete_override_only_for_sp():
    from unittest.mock import patch

    with patch("src.ui.select_page.ReleasesWorker"), patch("src.manifest.ManifestWorker"):
        win = MainWindow()
        try:
            # When method is mtk, even if _saw_all_command_exec is True, override must be False
            win._used_sp_backend = False
            win._current_method = "mtk"
            win._flash_method = "mtk"
            win._saw_all_command_exec = True
            assert win._is_sp_complete_override() is False

            # When method is sp, override must be True
            win._used_sp_backend = True
            win._current_method = "sp"
            win._flash_method = "sp"
            win._saw_all_command_exec = True
            assert win._is_sp_complete_override() is True
        finally:
            win.close()
            app = QApplication.instance()
            if app:
                app.processEvents()
