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

    from src.i18n import tr, translator
    from src.ui.flash_page import FlashPage

    translator().set_language("en")
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
    if sys.platform == "darwin":
        assert not page._wait_continue_btn.isEnabled()
    else:
        assert page._wait_continue_btn.isHidden()
    assert "SP Flash Tool" not in page._wait_prompt_label.text()
    assert "MTKClient" not in page._wait_prompt_label.text()

    page.show_connect_search()
    assert "Y1" in page._wait_prompt_label.text()
    assert tr("flash_connect_device_prompt").split("{")[0].strip() in page._wait_prompt_label.text()
    assert page._wait_progress_bar.maximum() == 100
    assert page._wait_progress_bar.value() == 0

    # The power-off sentence tells the user to make sure the player is powered-off
    # and disconnected, then press Next.
    page.show_power_off_prompt()
    assert page._wait_prompt_label.text().endswith("then press Next.")
    assert "powered-off and disconnected" in page._wait_prompt_label.text()
    assert "centre button" not in page._wait_prompt_label.text()
    page.set_model("A5")
    page.show_power_off_prompt()
    assert "A5" in page._wait_prompt_label.text()
    assert "powered-off and disconnected" in page._wait_prompt_label.text()
    assert "power button" not in page._wait_prompt_label.text()
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
            assert page._paperclip_hint.text() == tr("flash_paperclip_hint").format(model=model), model
            assert "headphone" in page._paperclip_hint.text(), model
            assert "paperclip or pin" in page._paperclip_hint.text(), model
            assert page._paperclip_hint.text().startswith("Completely power off your"), model
            assert page._paperclip_img.pixmap() is not None and not page._paperclip_img.pixmap().isNull(), model

        page.set_model("A5")
        page.show_power_off_prompt()
        assert page._paperclip_hint.text() == tr("flash_paperclip_hint_a5").format(model="A5")
        assert "power button" in page._paperclip_hint.text()
        assert "headphone" not in page._paperclip_hint.text()
        assert "paperclip or pin" in page._paperclip_hint.text()
        assert page._paperclip_hint.text().startswith("Completely power off your A5")
        assert page._paperclip_img.pixmap() is not None and not page._paperclip_img.pixmap().isNull()

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


def test_paperclip_hint_is_drawn_from_the_bundled_vector_asset(monkeypatch):
    """The power-off illustration comes from paperclip*.svg, not a PNG.

    The bitmaps were replaced by the vector artwork so the diagram stays sharp
    at any window scale and follows the desktop foreground colour. This pins
    the asset the card actually renders — six PNGs could stay on disk and still
    be what the page opened.
    """
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication

    from src.ui import icons as icons_module
    from src.ui.dark import T, apply_theme
    from src.ui.flash_page import FlashPage, _PAPERCLIP_HINT_PX

    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, force_dark=True)

    rendered = []
    real_render = icons_module.render_svg_asset

    def spy(path, height, color=None, device_pixel_ratio=2.0):
        rendered.append((Path(path), int(height)))
        return real_render(path, height, color=color, device_pixel_ratio=device_pixel_ratio)

    monkeypatch.setattr(icons_module, "render_svg_asset", spy)

    page = FlashPage()
    try:
        models_and_stems = (
            ("Y1", "paperclip"),
            ("Y2", "paperclip_y2"),
            ("A5", "paperclip_a5"),
            ("G1", "paperclip"),
            ("G5", "paperclip"),
            ("Q3", "paperclip"),
            ("Generic", "paperclip"),
        )
        for model, stem in models_and_stems:
            rendered.clear()
            page.set_model(model)
            page.show_power_off_prompt()
            app.processEvents()
            assert rendered, model
            asset, height = rendered[-1]
            assert asset.name == f"{stem}.svg", (model, asset)
            assert height == _PAPERCLIP_HINT_PX
            source = page._paperclip_img.property("_hint_source")
            assert source is not None and not source.isNull()
            logical = source.deviceIndependentSize()
            assert round(logical.height()) == _PAPERCLIP_HINT_PX, logical
            # The drawing keeps the asset's own proportions.
            assert 1.45 < logical.width() / logical.height() < 1.62, logical
            visible = page._paperclip_img.pixmap()
            assert not visible.isNull()
            # Every stroke is the live foreground colour, never the white
            # artwork that would be invisible on a light card.
            coloured = [
                visible.toImage().pixelColor(x, y)
                for y in range(visible.toImage().height())
                for x in range(0, visible.toImage().width(), 3)
                if visible.toImage().pixelColor(x, y).alpha() > 200
            ]
            assert coloured, model
            # The ink is the colour the card's own text is drawn in, so the
            # diagram never turns into invisible white artwork.
            foreground = page._paperclip_hint.palette().color(
                page._paperclip_hint.palette().ColorRole.WindowText
            )
            assert max(abs(c.lightness() - foreground.lightness()) for c in coloured) <= 24, model
    finally:
        page.close()
        app.processEvents()
