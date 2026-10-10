"""Continue launches the console flasher. The connect sentence follows USB search."""

import os
import queue
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("UPDATER_MTK_INPROCESS", "1")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _pump(app, predicate, seconds=8.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    app.processEvents()
    return bool(predicate())


def test_continue_launches_console_and_search_lines_drive_connect():
    """Continue starts format-download. Search lines reveal the connect sentence."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from src import i18n, paths
    from src.flash_service import (
        STEP_DETECT,
        STEP_DOWNLOAD_DA,
        STEP_WAITING,
        FlashWorker,
        is_mtk_usb_search_line,
        is_sp_usb_search_line,
    )
    from src.i18n import tr, translator
    from src.ui.main_window import MainWindow

    for lang in ("en", "zh-CN", "fr", "es", "de", "ja"):
        text = i18n._STRINGS["flash_please_wait"].get(lang) or ""
        assert text, lang
        assert "firmware" not in text.lower()
        assert "sp flash" not in text.lower()
        assert "mtk" not in text.lower()

    assert is_sp_usb_search_line("  Search USB, timeout 3600000 ms...")
    assert is_sp_usb_search_line("[SP] search usb port")
    assert not is_sp_usb_search_line("Download failed: search usb timeout")
    assert is_mtk_usb_search_line("   ...")
    assert is_mtk_usb_search_line("[LOG] ...")
    assert is_mtk_usb_search_line("[LOG] Hint: Power off the phone before connecting.")
    assert is_mtk_usb_search_line("[MTK] ......")
    assert not is_mtk_usb_search_line("hint: lower case is not the prefix")
    assert not is_mtk_usb_search_line("Initializing mtkclient...")

    assert paths.IS_WINDOWS, "console launch check runs the Windows flash_tool command"

    settings = QSettings("innioasis", "updater")
    keys = (
        "language",
        "flash_method",
        "sp_gui_install",
        "preferences/sp_gui_install",
        "terminal_install",
        "preferences/terminal_install",
        "sp_auth_file",
        "preferences/sp_auth_file",
    )
    saved = {key: settings.value(key) for key in keys}
    previous_lang = translator().lang
    translator().set_language("en")
    settings.setValue("language", "en")
    settings.setValue("flash_method", "sp")
    settings.setValue("sp_gui_install", False)
    settings.setValue("preferences/sp_gui_install", False)
    settings.setValue("terminal_install", False)
    settings.setValue("preferences/terminal_install", False)
    settings.setValue("sp_auth_file", "")
    settings.setValue("preferences/sp_auth_file", "")

    app = QApplication.instance() or QApplication(sys.argv)
    window = None
    stream = queue.Queue()
    popen_calls = []

    class _Stdout:
        def readline(self):
            return stream.get()

    class _Proc:
        def __init__(self):
            self.stdout = _Stdout()
            self.returncode = 0
            self.pid = 4321

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def poll(self):
            return None

        def terminate(self):
            return None

    def _popen(cmd, **kwargs):
        popen_calls.append(list(cmd))
        return _Proc()

    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            package = root / "rom.zip"
            package.write_bytes(b"PK")
            extract = root / ".rom_extracted"
            extract.mkdir()
            (extract / "MT6572_Android_scatter.txt").write_text(
                "platform: MT6572\n", encoding="utf-8"
            )
            (extract / ".extract_complete").write_text("ok", encoding="utf-8")
            sp_dir = root / "SP_Flash_Tool"
            sp_dir.mkdir()
            (sp_dir / "flash_tool.exe").write_bytes(b"")
            (sp_dir / "MTK_AllInOne_DA.bin").write_bytes(b"DA")

            window = MainWindow()
            window._needs_device_prep = lambda: True
            window._flash_method = "sp"
            window.service.start_device_monitor = lambda: None
            window.service.stop_device_monitor = lambda: None
            window.show()
            app.processEvents()

            with patch("src.sp_flash_gui.update_sp_history_ini", lambda *a, **k: None), \
                 patch("src.device_tracking.record_latest_package", lambda *a, **k: None), \
                 patch("src.device_tracking.sp_auth_file", lambda *a, **k: ""), \
                 patch("src.paths.find_sp_flash_tool", lambda: sp_dir), \
                 patch("src.flash_service.subprocess.Popen", side_effect=_popen):
                window._on_package_selected(str(package), "Original Software 3.0.2", "Y1")
                app.processEvents()
                page = window._flash_page
                assert popen_calls == []
                assert page._wait_continue_btn.isVisible()
                assert "connect your" not in page._wait_prompt_label.text().lower()

                page._wait_continue_btn.click()
                assert _pump(app, lambda: bool(popen_calls)), "Continue must start the console command"
                args = popen_calls[0]
                assert args[1:3] == ["-c", "format-download"], args
                assert args[3] == "-s", args
                assert "-d" in args and "-t" in args and args[-1] == "-r", args
                assert args[args.index("-t") + 1] == "without", args
                assert page._wait_prompt_label.text() == tr("flash_please_wait")
                assert "connect your" not in page._wait_prompt_label.text().lower()
                assert page._wait_progress_bar.minimum() == 0
                assert page._wait_progress_bar.maximum() == 0
                assert window._last_progress == 0

                stream.put("  search usb, timeout 3600000 ms...\n")
                assert _pump(
                    app,
                    lambda: "connect your" in page._wait_prompt_label.text().lower(),
                ), page._wait_prompt_label.text()
                assert "Y1" in page._wait_prompt_label.text()
                assert page._wait_progress_bar.maximum() == 0
                assert window._last_progress == 0

                stream.put("12% of DA has been sent\n")
                assert _pump(
                    app,
                    lambda: page._stack.currentWidget() is page._flashing_view,
                ), page._wait_prompt_label.text()
                assert "connect your" not in page._step_label.text().lower()
                assert "connect your" not in page._wait_prompt_label.text().lower()

                stream.put("Search USB, timeout 3600000 ms...\n")
                _pump(app, lambda: False, seconds=0.3)
                assert page._stack.currentWidget() is page._flashing_view
                assert "connect your" not in page._step_label.text().lower()
                assert "connect your" not in page._wait_prompt_label.text().lower()

                window._past_usb_search = False
                window._usb_search_active = False
                window._step_now = ""
                page.set_model("Y1")
                page.show_please_wait()
                mtk = FlashWorker(str(package), method="mtk", model="Y1")
                mtk.step_changed.connect(window._on_step_changed)
                mtk.progress.connect(window._on_progress)
                assert "connect your" not in page._wait_prompt_label.text().lower()

                steps = []
                mtk.step_changed.connect(steps.append)
                mtk._classify_mtk_output("[LOG] ...")
                app.processEvents()
                assert mtk._usb_search_active
                assert STEP_WAITING in steps
                assert "connect your" in page._wait_prompt_label.text().lower()
                assert page._wait_progress_bar.maximum() == 0
                assert window._last_progress == 0

                mtk._classify_mtk_output("Hint: Power off the phone before connecting.")
                app.processEvents()
                assert "connect your" in page._wait_prompt_label.text().lower()

                mtk._classify_mtk_output("Port - Device detected")
                app.processEvents()
                assert mtk._past_usb_search
                assert STEP_DETECT in steps
                assert "connect your" not in page._wait_prompt_label.text().lower()

                mtk._classify_mtk_output("...")
                mtk._classify_mtk_output("[LOG] Hint: Power off the phone before connecting.")
                app.processEvents()
                assert "connect your" not in page._wait_prompt_label.text().lower()

                quiet = FlashWorker(str(package), method="mtk", model="Y1")
                quiet._classify_mtk_output("hint: lower case is not the prefix")
                assert not quiet._usb_search_active

                later = []
                sp = FlashWorker(str(package), method="sp", model="Y1")
                sp.step_changed.connect(later.append)
                sp._classify_sp_stdout("  Search USB, timeout 3600000 ms...")
                assert STEP_WAITING in later
                sp._classify_sp_stdout("12% of DA has been sent")
                assert STEP_DOWNLOAD_DA in later
                assert sp._past_usb_search
                later.clear()
                sp._classify_sp_stdout("Search USB, timeout 3600000 ms...")
                assert STEP_WAITING not in later
    finally:
        stream.put("")
        if window is not None:
            worker = getattr(window.service, "_flash_worker", None)
            if worker is not None and worker.isRunning():
                worker.wait(3000)
            window.close()
            app.processEvents()
        translator().set_language(previous_lang or "en")
        for key, value in saved.items():
            if value is None:
                settings.remove(key)
            else:
                settings.setValue(key, value)
