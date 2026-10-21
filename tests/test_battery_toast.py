"""Test for the first-run battery charge toast hint on Select Package page."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_app = QApplication.instance() or QApplication(["--platform", "offscreen"])

from src.ui.select_page import SelectPackagePage  # noqa: E402
from src.ui.widgets import BatteryToast  # noqa: E402


def test_battery_toast_first_run_and_dismiss(tmp_path, monkeypatch):
    test_ini = tmp_path / "test_settings.ini"
    settings = QSettings(str(test_ini), QSettings.Format.IniFormat)
    settings.setValue("battery_hint_dismissed", False)

    page = SelectPackagePage()
    page.settings = settings
    page._check_battery_toast_visibility()
    page.show()
    _app.processEvents()

    assert hasattr(page, "_battery_toast")
    assert isinstance(page._battery_toast, BatteryToast)
    assert not page._battery_toast.isHidden()
    assert "make sure the battery is charged" in page._battery_toast._label.text()

    # Click dismiss button
    page._battery_toast._dismiss_btn.click()
    _app.processEvents()

    assert page._battery_toast.isHidden()
    assert settings.value("battery_hint_dismissed", False, type=bool) is True

    # Subsequent run: toast is not shown
    page2 = SelectPackagePage()
    page2.settings = settings
    page2._check_battery_toast_visibility()
    assert page2._battery_toast.isHidden()

    page.close()
    page2.close()


def test_battery_toast_generic_mtk_device_wording(monkeypatch):
    monkeypatch.setenv("BUILD_BRAND", "mediatek_installer")
    toast = BatteryToast()
    assert "device" in toast._label.text().lower()
    assert "player" not in toast._label.text().lower()
    assert toast.property("cssClass") == "card"
    assert toast.minimumHeight() >= 38

