"""Installed releases are marked with a circle. Newer ones are the emphasis."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.catalog import classify_release_list


def _releases():
    return [
        {"tag_name": "v3.0.2", "published_at": "2024-01-01T00:00:00Z"},
        {"tag_name": "v3.1.2", "published_at": "2024-06-01T00:00:00Z"},
        {"tag_name": "v3.2.0", "published_at": "2025-01-01T00:00:00Z"},
    ]


def test_newer_rows_and_prompt_use_version_order():
    marks = classify_release_list(_releases(), "v3.1.2")
    assert marks["rows"]["v3.1.2"]["installed"] is True
    assert marks["rows"]["v3.1.2"]["newer"] is False
    assert marks["rows"]["v3.2.0"]["newer"] is True
    assert marks["rows"]["v3.0.2"]["newer"] is False
    assert marks["prompt"]["newer_tag"] == "v3.2.0"
    assert marks["prompt"]["installed_tag"] == "v3.1.2"


def test_no_prompt_when_installed_is_newest():
    marks = classify_release_list(_releases(), "v3.2.0")
    assert marks["prompt"] is None
    assert marks["rows"]["v3.2.0"]["installed"] is True
    assert not any(row["newer"] for row in marks["rows"].values())


def test_software_install_is_remembered_separately():
    from PySide6.QtCore import QSettings

    from src import device_tracking

    path = Path(__file__).resolve().parent / "_release_marks_settings.ini"
    if path.exists():
        path.unlink()
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    try:
        device_tracking.record_device_install(
            "Y1", "Original Software", "v3.1.2", release_label="3.1.2", settings=settings,
        )
        device_tracking.record_device_install(
            "Y1", "Rockbox", "v1.0.0", release_label="1.0.0", settings=settings,
        )
        original = device_tracking.get_software_install("Y1", "Original Software", settings=settings)
        rockbox = device_tracking.get_software_install("Y1", "Rockbox", settings=settings)
        assert original["tag_name"] == "v3.1.2"
        assert rockbox["tag_name"] == "v1.0.0"
        # The model still remembers the latest install for older callers.
        assert device_tracking.get_device_install("Y1", settings=settings)["tag_name"] == "v1.0.0"
    finally:
        settings.sync()
        path.unlink(missing_ok=True)


def test_installed_row_is_a_circle_without_the_word():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QListWidgetItem

    from src.i18n import tr
    from src.ui.select_page import SelectPackagePage

    app = QApplication.instance() or QApplication(sys.argv)
    from PySide6.QtCore import QSettings
    path = Path(__file__).resolve().parent / "_release_marks_ui.ini"
    if path.exists():
        path.unlink()
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    page = SelectPackagePage()
    page.settings = settings
    page._model_combo.clear()
    page._model_combo.addItem("Y1")
    page._software_combo.clear()
    page._software_combo.addItem("Original Software")
    page._on_releases_loaded(_releases(), "")
    # No install recorded: no circle, no prompt, nothing bold.
    assert page._update_prompt.isHidden()
    labels = [page._release_list.item(i).text() for i in range(page._release_list.count())]
    assert all("Installed" not in text and "（" not in text for text in labels)
    assert all(not text.startswith("●") for text in labels)

    from src import device_tracking

    page.settings = settings
    device_tracking.record_device_install(
        "Y1", "Original Software", "v3.1.2", release_label="3.1.2", settings=settings,
    )
    page._on_releases_loaded(_releases(), "")
    installed = None
    newer_bold = False
    for i in range(page._release_list.count()):
        item: QListWidgetItem = page._release_list.item(i)
        assert "(Installed)" not in item.text()
        assert tr("installed_badge") not in item.text()
        rel = item.data(Qt.ItemDataRole.UserRole)
        if rel["tag_name"] == "v3.1.2":
            installed = item
            assert item.text().startswith("●")
            assert "3.1.2" in item.text()
            assert not item.font().bold()
            assert tr("sel_installed_tip") in item.toolTip()
        if rel["tag_name"] == "v3.2.0":
            newer_bold = item.font().bold()
            assert not item.text().startswith("●")
        if rel["tag_name"] == "v3.0.2":
            assert not item.font().bold()
    assert installed is not None
    assert newer_bold is True
    assert not page._update_prompt.isHidden()
    text = page._update_prompt.text()
    assert "3.2.0" in text or "v3.2.0" in text
    assert "3.1.2" in text
    page._on_releases_loaded(
        [rel for rel in _releases() if rel["tag_name"] != "v3.2.0"],
        "",
    )
    # Installed 3.1.2 is now the newest of what is listed... 3.2.0 removed,
    # so 3.1.2 is newest. No prompt.
    device_tracking.record_device_install(
        "Y1", "Original Software", "v3.2.0", release_label="3.2.0", settings=settings,
    )
    page._on_releases_loaded(_releases(), "")
    assert page._update_prompt.isHidden()
    page.deleteLater()
    path.unlink(missing_ok=True)


if __name__ == "__main__":
    test_newer_rows_and_prompt_use_version_order()
    test_no_prompt_when_installed_is_newest()
    test_software_install_is_remembered_separately()
    test_installed_row_is_a_circle_without_the_word()
    print("release mark tests passed")
