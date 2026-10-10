"""Sticky release-notes translation + localised Original Software display name."""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _rel(tag, body="General performance and stability improvements"):
    return {
        "source_repo": "y1-community/y1-stock-rom",
        "tag_name": tag,
        "name": tag,
        "body": body,
    }


def _item(rel):
    from PySide6.QtWidgets import QListWidgetItem
    from PySide6.QtCore import Qt

    item = QListWidgetItem(rel["tag_name"])
    item.setData(Qt.UserRole, rel)
    return item


def test_translate_click_enables_auto_translate_on_focus():
    from src.i18n import translator
    from src.ui.select_page import SelectPackagePage

    app = _app()
    orig_lang = translator().lang
    page = SelectPackagePage()
    try:
        translator().set_language("es")
        calls = []
        page._translate_current_release_in_app = lambda: calls.append(
            (page._current_selected_rel or {}).get("tag_name")
        )
        assert page._auto_translate_notes is False

        page._on_release_selected(_item(_rel("3.2.1")), None)
        assert calls == [], "no auto-translate before the user ever clicks translate"

        page._on_translate_link_clicked("action:translate_in_app")
        assert page._auto_translate_notes is True
        assert calls == ["3.2.1"]

        # Focusing another release translates automatically, no second click.
        page._on_release_selected(_item(_rel("3.2.2")), None)
        assert calls == ["3.2.1", "3.2.2"]
        assert page._auto_translate_notes is True
    finally:
        translator().set_language(orig_lang)
        page.deleteLater()
        app.processEvents()


def test_show_original_disables_auto_translate():
    from src.i18n import translator
    from src.ui.select_page import SelectPackagePage

    app = _app()
    orig_lang = translator().lang
    page = SelectPackagePage()
    try:
        translator().set_language("es")
        calls = []
        page._translate_current_release_in_app = lambda: calls.append(
            (page._current_selected_rel or {}).get("tag_name")
        )
        page._on_release_selected(_item(_rel("3.2.1")), None)
        page._on_translate_link_clicked("action:translate_in_app")
        assert page._auto_translate_notes is True

        page._on_translate_link_clicked("action:show_original")
        assert page._auto_translate_notes is False
        assert "General performance" in page._notes.toPlainText()

        calls.clear()
        page._on_release_selected(_item(_rel("3.2.2")), None)
        assert calls == [], "original requested: further focuses stay untranslated"
    finally:
        translator().set_language(orig_lang)
        page.deleteLater()
        app.processEvents()


def test_original_software_display_name_presets():
    from src.i18n import translator
    from src.ui.select_page import display_software_name

    orig_lang = translator().lang
    try:
        expected = {
            "zh-CN": "原厂固件",
            "en": "Original Software",
            "fr": "Logiciel d'origine",
            "es": "Software original",
        }
        for lang, text in expected.items():
            translator().set_language(lang)
            assert display_software_name("Original Software") == text, lang
            assert display_software_name("Rockbox") == "Rockbox", lang
            assert display_software_name("JJ Launcher") == "JJ Launcher", lang
    finally:
        translator().set_language(orig_lang)


def test_software_dropdown_shows_preset_translation():
    """The Software dropdown localises the label; the lookup key stays English."""
    from src.i18n import translator
    from src.ui.select_page import SelectPackagePage

    app = _app()
    orig_lang = translator().lang
    page = SelectPackagePage()
    try:
        page._model_combo.setCurrentText("Y1")
        translator().set_language("zh-CN")
        page.retranslate()
        combo = page._software_combo
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert "原厂固件" in texts, texts
        assert "Original Software" not in texts, texts
        assert "Rockbox" in texts

        combo.blockSignals(True)
        combo.setCurrentIndex(texts.index("原厂固件"))
        combo.blockSignals(False)
        assert page.current_software() == "Original Software"
        assert page.current_package() is not None

        translator().set_language("en")
        page.retranslate()
        texts = [combo.itemText(i) for i in range(combo.count())]
        assert "Original Software" in texts, texts
        assert page.current_software() == "Original Software"
    finally:
        translator().set_language(orig_lang)
        page.deleteLater()
        app.processEvents()


def test_details_header_shows_translated_original_software():
    from src.i18n import translator
    from src.ui.select_page import SelectPackagePage

    app = _app()
    orig_lang = translator().lang
    page = SelectPackagePage()
    try:
        translator().set_language("zh-CN")
        pkg = SimpleNamespace(name="Original Software", slug="original-y1")
        page._set_details_header(_rel("3.2.1"), pkg)
        assert page._details_name.text() == "原厂固件"
        translator().set_language("en")
        page._set_details_header(_rel("3.2.1"), pkg)
        assert page._details_name.text() == "Original Software"
    finally:
        translator().set_language(orig_lang)
        page.deleteLater()
        app.processEvents()
