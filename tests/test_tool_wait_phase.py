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


def test_firmware_history_ini_is_not_the_one_the_tool_opens(tmp_path):
    from src import sp_flash_gui

    tool = tmp_path / "SP_Flash_Tool"
    tool.mkdir()
    (tool / sp_flash_gui.DA_FILENAME).write_bytes(b"DA")
    (tool / "flash_tool.exe").write_bytes(b"exe")
    extract = tmp_path / "rom"
    extract.mkdir()
    scatter = extract / "MT6572_Android_scatter.txt"
    scatter.write_text("partition_index: SYS0\n", encoding="utf-8")
    (extract / "history.ini").write_text(
        "[LastDAFilePath]\nlastDir=MTK_AllInOne_DA.bin\n\n"
        "[RecentOpenFile]\nlastDir=\nscatterHistory=MT6572_Android_scatter.txt\n",
        encoding="utf-8",
    )

    assert sp_flash_gui.update_sp_history_ini(
        tool, scatter_path=scatter, extract_dir=extract, model="Y1"
    ) is True
    assert not (extract / "history.ini").is_file()
    da_read, scatter_read, history = sp_flash_gui.read_history_paths(tool / "history.ini")
    assert Path(scatter_read).resolve() == scatter.resolve()
    assert Path(da_read).resolve() == (tool / sp_flash_gui.DA_FILENAME).resolve()
    assert "MT6572_Android_scatter.txt" not in history or str(scatter.resolve()) in history
    assert not (tool / "MT6572_Android_scatter.txt").is_file()


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

    # The power-off sentence names the button that powers this player off, and
    # still says how the run proceeds.
    page.show_power_off_prompt()
    assert page._wait_prompt_label.text().endswith("then click Next")
    assert "centre button" in page._wait_prompt_label.text()
    page.set_model("A5")
    page.show_power_off_prompt()
    assert "power button" in page._wait_prompt_label.text()
    assert "centre" not in page._wait_prompt_label.text()
    page.set_model("Y1")


def test_power_off_step_offers_cancel_without_a_question():
    """Nothing is written yet there, so the cancel chip stops the run outright."""
    from PySide6.QtWidgets import QApplication

    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    page = FlashPage()
    try:
        page.set_model("Y1")
        calls = []
        page.on_cancel_wait(lambda: calls.append("cancelled"))
        page.show_power_off_prompt()
        assert not page._wait_cancel_btn.isHidden()
        assert not page._wait_continue_btn.isHidden()

        page._wait_cancel_btn.click()
        assert calls == ["cancelled"], "the power-off step cancels without asking"
        assert not getattr(page, "_stop_open", False)

        # Mid-run the same chip asks first, because the target is being written.
        page.leave_power_off_prompt()
        page._prompt_only = False
        page._wait_cancel_btn.click()
        assert getattr(page, "_stop_open", False)
        assert calls == ["cancelled"]
    finally:
        page.close()
        app.processEvents()


def test_paperclip_hint_names_the_hidden_button_per_model():
    """Below the card: where the hidden button is, once the model is known."""
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, translator
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    original_lang = translator().lang
    translator().set_language("en")
    page = FlashPage()
    try:
        # No model yet: the hint stays out of the way.
        page.set_model("")
        page.show_power_off_prompt()
        assert page._paperclip_hint_host.isHidden()

        # Every known model but the A5 hides the button beside the headphone
        # jack, and the copy says what the button is for: powering the player
        # off — with a paperclip or a pin.
        for model in ("Y1", "Y2"):
            page.set_model(model)
            page.show_power_off_prompt()
            assert not page._paperclip_hint_host.isHidden(), model
            assert page._paperclip_hint.text() == tr("flash_paperclip_hint"), model
            assert "headphone jack" in page._paperclip_hint.text(), model
            assert "paperclip or pin" in page._paperclip_hint.text(), model
            assert page._paperclip_hint.text().startswith("Tip: Power off the device"), model

        page.set_model("A5")
        page.show_power_off_prompt()
        assert page._paperclip_hint.text() == tr("flash_paperclip_hint_a5")
        assert "power button" in page._paperclip_hint.text()
        assert "headphone" not in page._paperclip_hint.text()
        assert "paperclip or pin" in page._paperclip_hint.text()

        # Not at the connect step: the player is already powered off by then.
        page.set_model("Y1")
        page.show_connect_search()
        assert page._paperclip_hint_host.isHidden()

        # Once the install is past the power-off step, the hint goes away.
        page.set_model("Y1")
        page.show_power_off_prompt()
        assert not page._paperclip_hint_host.isHidden()
        page.show_please_wait()
        assert page._paperclip_hint_host.isHidden()
        page.show_flashing()
        assert page._paperclip_hint_host.isHidden()
    finally:
        translator().set_language(original_lang)
        page.deleteLater()
        app.processEvents()

    page.show_please_wait()
    assert page._wait_prompt_label.text() == tr("flash_please_wait")
    assert page._wait_progress_bar.maximum() == 0
    page.deleteLater()
    app.processEvents()


def test_stop_question_hides_the_paperclip_tip_and_asks_to_cancel():
    """The interruption card is a question, and it is not a power-off step."""
    from PySide6.QtWidgets import QApplication

    from src.i18n import tr, tr_brand, translator
    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    original = translator().lang
    translator().set_language("en")
    page = FlashPage()
    try:
        page.set_model("Y1")
        page.show_power_off_prompt()
        assert not page._paperclip_hint_host.isHidden()

        # Asking whether to stop borrows the card: no power-off instruction
        # belongs under a question about cancelling.
        page.prompt_stop(tr("install_stop_check"), on_continue=lambda: None)
        assert page._paperclip_hint_host.isHidden()
        assert tr("install_stop_check") == "Would you like to cancel the install?"
        assert "still running" not in tr("install_stop_check")
        assert "取消" in tr_brand("install_stop_check") or "cancel" in tr_brand(
            "install_stop_check"
        ).lower()

        page._finish_stop(False)
        assert not page._paperclip_hint_host.isHidden()
    finally:
        page.close()
        translator().set_language(original)
        app.processEvents()


def test_next_arrow_centres_on_the_card_not_the_wrapped_prompt():
    """The round arrow sits in the card row, in line with the release icon."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.ui.flash_page import FlashPage

    app = QApplication.instance() or QApplication(sys.argv)
    page = FlashPage()
    try:
        page.resize(900, 420)
        page.show()
        page.set_model("Y1")
        page.show_power_off_prompt()
        app.processEvents()
        row = page._wait_row
        index = row.indexOf(page._wait_continue_btn)
        assert index >= 0, "the arrow belongs to the card row"
        assert row.itemAt(index).alignment() == Qt.AlignVCenter
        arrow_center = page._wait_continue_btn.geometry().center().y()
        icon_center = page._wait_icon.geometry().center().y()
        assert abs(arrow_center - icon_center) <= 6, (arrow_center, icon_center)
    finally:
        page.close()
        app.processEvents()
