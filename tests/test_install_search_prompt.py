"""Continue launches the console flasher. The connect sentence follows USB search."""

import os
import queue
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

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


# The console launch path this exercises is the Windows flash_tool command
# line, and the test asserts ``paths.IS_WINDOWS`` before it gets there.
@pytest.mark.skipif(sys.platform != "win32", reason="console flash_tool command is Windows-only")
def test_continue_launches_console_and_search_lines_drive_connect():
    """Continue starts a Download Only run. Search lines reveal the connect sentence."""
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
    # The matcher is deliberately case-insensitive: the tool prints "Hint:" but
    # the same search line lowercased means the same thing to the reader.
    assert is_mtk_usb_search_line("hint: power off the phone before connecting")
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

            with patch("src.downloads.downloads_dir", lambda: root), \
                 patch("src.sp_flash_gui.update_sp_history_ini", lambda *a, **k: None), \
                 patch("src.device_tracking.record_latest_package", lambda *a, **k: None), \
                 patch("src.device_tracking.sp_auth_file", lambda *a, **k: ""), \
                 patch("src.paths.find_sp_flash_tool", lambda: sp_dir), \
                 patch("src.flash_service.subprocess.Popen", side_effect=_popen):
                window._on_package_selected(str(package), "Original Software 3.0.2", "Y1")
                app.processEvents()
                page = window._flash_page
                assert popen_calls == []
                assert page._wait_continue_btn.isVisible()
                assert "please connect your" not in page._wait_prompt_label.text().lower()

                page._wait_continue_btn.click()
                assert _pump(
                    app,
                    lambda: bool([c for c in popen_calls if "taskkill" not in str(c[0]).lower()]),
                ), "Continue must start the console command"
                sp_calls = [c for c in popen_calls if "taskkill" not in str(c[0]).lower()]
                args = sp_calls[0]
                assert args[1:3] == ["-c", "format-download"], args
                assert args[3] == "-s", args
                assert "-d" in args and "-t" in args and args[-1] == "-r", args
                assert args[args.index("-t") + 1] == "without", args
                assert "please connect your" not in page._wait_prompt_label.text().lower()
                assert "30s" not in page._wait_prompt_label.text()
                assert page._wait_progress_bar.minimum() == 0
                assert page._wait_progress_bar.maximum() == 0
                assert window._last_progress == 0

                stream.put("  search usb, timeout 3600000 ms...\n")
                assert _pump(
                    app,
                    lambda: "connect your" in page._wait_prompt_label.text().lower(),
                ), page._wait_prompt_label.text()
                assert "Y1" in page._wait_prompt_label.text()
                assert page._wait_progress_bar.maximum() == 100
                assert window._last_progress == 0

                stream.put("12% of DA has been sent\n")
                assert _pump(
                    app,
                    lambda: page._stack.currentWidget() is page._flashing_view,
                ), page._wait_prompt_label.text()
                assert "please connect your" not in page._step_label.text().lower()
                assert "please connect your" not in page._wait_prompt_label.text().lower()

                stream.put("Search USB, timeout 3600000 ms...\n")
                _pump(app, lambda: False, seconds=0.3)
                assert page._stack.currentWidget() is page._flashing_view
                assert "please connect your" not in page._step_label.text().lower()
                assert "please connect your" not in page._wait_prompt_label.text().lower()

                window._past_usb_search = False
                window._usb_search_active = False
                window._step_now = ""
                page.set_model("Y1")
                page.show_please_wait()
                mtk = FlashWorker(str(package), method="mtk", model="Y1")
                mtk.step_changed.connect(window._on_step_changed)
                mtk.progress.connect(window._on_progress)
                assert "please connect your" not in page._wait_prompt_label.text().lower()

                steps = []
                mtk.step_changed.connect(steps.append)
                mtk._classify_mtk_output("[LOG] ...")
                app.processEvents()
                assert mtk._usb_search_active
                assert STEP_WAITING in steps
                assert "connect your" in page._wait_prompt_label.text().lower()
                assert page._wait_progress_bar.maximum() == 100
                assert window._last_progress == 0

                mtk._classify_mtk_output("Hint: Power off the phone before connecting.")
                app.processEvents()
                assert "connect your" in page._wait_prompt_label.text().lower()

                mtk._classify_mtk_output("Port - Device detected")
                app.processEvents()
                assert mtk._past_usb_search
                assert STEP_DETECT in steps
                assert "please connect your" not in page._wait_prompt_label.text().lower()

                mtk._classify_mtk_output("...")
                mtk._classify_mtk_output("[LOG] Hint: Power off the phone before connecting.")
                app.processEvents()
                assert "please connect your" not in page._wait_prompt_label.text().lower()

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


def test_sp_monitor_ignores_stale_prior_session_logs():
    """SP Flash Tool log monitoring ignores directories from prior runs."""
    from PySide6.QtCore import QCoreApplication
    from src.flash_service import FlashWorker, STEP_WRITE

    app = QCoreApplication.instance() or QCoreApplication([])

    class _StopAfter:
        def __init__(self, checks):
            self._left = checks

        def is_set(self):
            self._left -= 1
            return self._left <= 0

    with tempfile.TemporaryDirectory() as td:
        log_root = Path(td)
        # Create an old dump directory with a 85% progress log from a past session
        old_dir = log_root / "SP_FT_Dump_old_session"
        old_dir.mkdir()
        old_log = old_dir / "QT_FLASH_TOOL.log"
        old_log.write_text(
            "10/12/2026 15:02:44.713  85% of image data has been sent (2.67G of 3.15G)\n",
            encoding="utf-8",
        )
        stale_mtime = time.time() - 3600
        os.utime(old_dir, (stale_mtime, stale_mtime))
        os.utime(old_log, (stale_mtime, stale_mtime))

        w = FlashWorker("pkg.zip", method="sp")
        steps = []
        progress_values = []
        w.step_changed.connect(steps.append)
        w.progress.connect(progress_values.append)

        # Launch time is now: the monitor must ignore old_dir
        launch_time = time.time()
        stop_event = _StopAfter(2)
        w._monitor_sp_log_file(stop_event, str(log_root), launch_time=launch_time)
        app.processEvents()

        assert not w._past_usb_search
        assert not w._device_seen
        assert STEP_WRITE not in steps
        assert 85 not in progress_values


def test_sp_monitor_accepts_current_session_logs():
    """SP Flash Tool log monitoring tails new directories matching launch_time."""
    from PySide6.QtCore import QCoreApplication
    from src.flash_service import FlashWorker, STEP_WRITE

    app = QCoreApplication.instance() or QCoreApplication([])

    class _StopAfter:
        def __init__(self, checks):
            self._left = checks

        def is_set(self):
            self._left -= 1
            return self._left <= 0

    with tempfile.TemporaryDirectory() as td:
        log_root = Path(td)
        launch_time = time.time()
        # Create a fresh dump directory matching launch_time
        fresh_dir = log_root / "SP_FT_Dump_current"
        fresh_dir.mkdir()
        fresh_log = fresh_dir / "QT_FLASH_TOOL.log"
        fresh_log.write_text(
            "10/12/2026 15:02:44.713  42% of image data has been sent (1.00G of 2.00G)\n",
            encoding="utf-8",
        )

        w = FlashWorker("pkg.zip", method="sp")
        steps = []
        progress_values = []
        w.step_changed.connect(steps.append)
        w.progress.connect(progress_values.append)

        stop_event = _StopAfter(3)
        w._monitor_sp_log_file(stop_event, str(log_root), launch_time=launch_time)
        app.processEvents()

        assert w._past_usb_search
        assert w._device_seen
        assert STEP_WRITE in steps
        assert len(progress_values) > 0


