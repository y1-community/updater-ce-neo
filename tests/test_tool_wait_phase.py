"""Connect copy follows USB search lines. Later progress is please-wait."""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.flash_service import (  # noqa: E402
    PHASE_BEFORE,
    PHASE_CONNECT,
    PHASE_PLEASE_WAIT,
    advance_tool_wait_phase,
)


def _phases(lines):
    phase = PHASE_BEFORE
    seen = []
    for line in lines:
        phase = advance_tool_wait_phase(phase, line)
        seen.append(phase)
    return seen


def test_search_then_forward_then_hint_again():
    """SP search, a later download line, MTK dots/hint, then a real MTK line."""
    phases = _phases(
        [
            "search usb port...",
            "12% of DA has been sent",
            ".....................",
            "Port - Hint:",
            "AttributeError: 'NoneType' object has no attribute 'write'",
            "Port - Device detected",
        ]
    )
    assert phases == [
        PHASE_CONNECT,
        PHASE_PLEASE_WAIT,
        PHASE_CONNECT,
        PHASE_CONNECT,
        PHASE_CONNECT,
        PHASE_PLEASE_WAIT,
    ]


def test_search_prefixes_and_pre_search_lines():
    assert advance_tool_wait_phase(PHASE_BEFORE, "  SEARCH USB, timeout 3600000 ms") == PHASE_CONNECT
    assert advance_tool_wait_phase(PHASE_BEFORE, "[SP] search usb port...") == PHASE_CONNECT
    assert advance_tool_wait_phase(PHASE_BEFORE, "[LOG] .....................") == PHASE_CONNECT
    assert advance_tool_wait_phase(PHASE_BEFORE, "  hint: power off the phone") == PHASE_CONNECT
    assert advance_tool_wait_phase(PHASE_BEFORE, "Port - Hint:") == PHASE_CONNECT
    # Before a search line, a download line must not invent the connect sentence.
    assert advance_tool_wait_phase(PHASE_BEFORE, "12% of image data has been sent") == PHASE_BEFORE
    # Logging spam after search stays on the connect sentence.
    phase = advance_tool_wait_phase(PHASE_CONNECT, "Traceback (most recent call last):")
    assert phase == PHASE_CONNECT
    phase = advance_tool_wait_phase(phase, "File \"logging/__init__.py\", line 1100, in emit")
    assert phase == PHASE_CONNECT


def test_dead_stream_handler_does_not_raise(tmp_path, monkeypatch):
    from src.diagnostics import guard_broken_stream_handlers

    root = logging.getLogger()
    dead = logging.StreamHandler()
    dead.stream = None
    root.addHandler(dead)
    kept = logging.FileHandler(tmp_path / "kept.log", encoding="utf-8")
    root.addHandler(kept)
    try:
        removed = guard_broken_stream_handlers()
        assert removed >= 1
        assert dead not in root.handlers
        assert kept in root.handlers
        logging.getLogger("src.sp_flash_gui").info(
            "Updated %s with absolute scatter: %s", tmp_path / "history.ini", tmp_path / "scatter.txt"
        )
    finally:
        root.removeHandler(kept)
        kept.close()
        if dead in root.handlers:
            root.removeHandler(dead)


def test_history_ini_is_not_rewritten_when_scatter_is_unchanged(tmp_path, monkeypatch):
    from src import sp_flash_gui

    tool = tmp_path / "SP_Flash_Tool"
    tool.mkdir()
    (tool / sp_flash_gui.DA_FILENAME).write_bytes(b"DA")
    scatter = tmp_path / "MT6572_Android_scatter.txt"
    scatter.write_text("partition_index: SYS0\n", encoding="utf-8")

    infos = []
    monkeypatch.setattr(sp_flash_gui.logger, "info", lambda *args, **kwargs: infos.append(args))

    assert sp_flash_gui.update_sp_history_ini(tool, scatter_path=scatter, model="Y1") is True
    history = tool / "history.ini"
    _da, scatter_read, _history = sp_flash_gui.read_history_paths(history)
    assert Path(scatter_read).resolve() == scatter.resolve()
    first = history.read_text(encoding="utf-8")
    assert len(infos) == 1

    assert sp_flash_gui.update_sp_history_ini(tool, scatter_path=scatter, model="Y1") is True
    assert history.read_text(encoding="utf-8") == first
    assert len(infos) == 1


def test_connect_sentence_and_please_wait_bar():
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    page = FlashPage()
    page.set_model("Y1")

    page.show_power_off_prompt()
    assert not page._wait_continue_btn.isHidden()
    assert tr("flash_connect_prompt").split("{")[0].strip() in page._wait_prompt_label.text()
    assert "Please connect your" not in page._wait_prompt_label.text()

    page.show_please_wait()
    assert page._wait_prompt_label.text() == tr("flash_please_wait")
    assert page._wait_progress_bar.minimum() == 0
    assert page._wait_progress_bar.maximum() == 0
    assert page._wait_continue_btn.isHidden()
    assert "SP Flash Tool" not in page._wait_prompt_label.text()
    assert "MTKClient" not in page._wait_prompt_label.text()

    page.show_connect_search()
    assert "Y1" in page._wait_prompt_label.text()
    assert tr("flash_connect_device_prompt").split("{")[0].strip() in page._wait_prompt_label.text()
    assert page._wait_progress_bar.maximum() == 100
    assert page._wait_progress_bar.value() == 0

    page.show_please_wait()
    assert page._wait_prompt_label.text() == tr("flash_please_wait")
    assert page._wait_progress_bar.maximum() == 0
    page.deleteLater()
    app.processEvents()
