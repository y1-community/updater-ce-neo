"""Windows retries: MTKClient is only a failover target once the M key reveals it."""

import os
import sys
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["INNIOASIS_HEADLESS"] = "1"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import device_tracking, paths
from src.ui.main_window import MainWindow


def _window():
    QApplication.instance() or QApplication(sys.argv)
    return MainWindow.__new__(MainWindow)


def test_windows_sp_has_no_mtk_failover_without_m_key():
    window = _window()
    settings = QSettings("innioasis", "updater")
    orig = settings.value("hidden_mtk_options")
    device_tracking.set_hidden_mtk_options(False)
    try:
        with patch.object(paths, "IS_MAC", False), patch.object(paths, "IS_WINDOWS", True):
            assert window._other_method("sp") == ""
    finally:
        if orig is None:
            settings.remove("hidden_mtk_options")
        else:
            device_tracking.set_hidden_mtk_options(bool(orig))


def test_windows_sp_never_fails_over_to_mtk():
    window = _window()
    settings = QSettings("innioasis", "updater")
    orig = settings.value("hidden_mtk_options")
    device_tracking.set_hidden_mtk_options(True)
    try:
        with patch.object(paths, "IS_MAC", False), patch.object(paths, "IS_WINDOWS", True):
            assert window._other_method("sp") == ""
    finally:
        if orig is None:
            settings.remove("hidden_mtk_options")
        else:
            device_tracking.set_hidden_mtk_options(bool(orig))


def test_linux_failover_unchanged_without_m_key():
    window = _window()
    with patch.object(paths, "IS_MAC", False), patch.object(paths, "IS_WINDOWS", False):
        assert window._other_method("sp") == "mtk"
        assert window._other_method("mtk") == "sp"
