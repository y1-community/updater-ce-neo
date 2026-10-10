"""A downgrade moves the installed circle and quiets update notices."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from src import device_tracking
from src.catalog import classify_release_list
from src.ui.select_page import SelectPackagePage


def _releases():
    return [
        {"tag_name": "v3.0.2", "published_at": "2024-01-01T00:00:00Z"},
        {"tag_name": "v3.1.2", "published_at": "2024-06-01T00:00:00Z"},
        {"tag_name": "v3.2.0", "published_at": "2025-01-01T00:00:00Z"},
    ]


def test_downgrade_moves_circle_and_quiets_notices():
    path = Path(__file__).resolve().parent / "_release_marks_downgrade.ini"
    path.unlink(missing_ok=True)
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        device_tracking.record_device_install(
            "Y1", "Original Software", "v3.1.2", release_label="3.1.2", settings=settings,
        )
        device_tracking.record_device_install(
            "Y1", "Original Software", "v3.0.2", release_label="3.0.2",
            settings=settings, catalogue_latest="v3.2.0",
        )
        rec = device_tracking.get_device_install("Y1", settings=settings)
        assert rec["tag_name"] == "v3.0.2"
        assert rec["notify_ceiling"] == "v3.2.0"
        assert device_tracking.update_notification_due("v3.0.2", "v3.2.0", "v3.2.0") is False
        assert device_tracking.update_notification_due("v3.0.2", "v3.2.1", "v3.2.0") is True

        marks = classify_release_list(_releases(), "v3.0.2", notify_ceiling="v3.2.0")
        assert marks["rows"]["v3.0.2"]["installed"] is True
        assert marks["rows"]["v3.2.0"]["newer"] is True
        assert marks["prompt"] is None
        resumed = classify_release_list(
            _releases() + [{"tag_name": "v3.2.1", "published_at": "2025-06-01T00:00:00Z"}],
            "v3.0.2",
            notify_ceiling="v3.2.0",
        )
        assert resumed["prompt"]["newer_tag"] == "v3.2.1"

        page = SelectPackagePage()
        page.settings = settings
        page._model_combo.clear()
        page._model_combo.addItem("Y1")
        page._software_combo.clear()
        page._software_combo.addItem("Original Software")
        page._on_releases_loaded(_releases(), "")
        circled = []
        for i in range(page._release_list.count()):
            item = page._release_list.item(i)
            rel = item.data(Qt.ItemDataRole.UserRole)
            if item.text().startswith("●"):
                circled.append(rel["tag_name"])
        assert circled == ["v3.0.2"]
        assert page._update_prompt.isHidden()

        device_tracking.record_device_install(
            "Y1", "Rockbox", "v1.0.0", release_label="1.0.0", settings=settings,
        )
        switched = device_tracking.get_device_install("Y1", settings=settings)
        assert switched["software_name"] == "Rockbox"
        assert switched["tag_name"] == "v1.0.0"
        assert not switched.get("notify_ceiling")
        assert device_tracking.get_software_install(
            "Y1", "Original Software", settings=settings,
        ) is None
        page.deleteLater()
        app.processEvents()
    finally:
        settings.sync()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_downgrade_moves_circle_and_quiets_notices()
    print("downgrade notice tests passed")
