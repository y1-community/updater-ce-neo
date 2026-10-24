"""The language picker offers every catalog, with a flag where one draws."""

import os
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["--platform", "offscreen"])


def _codes(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def test_every_catalog_is_offered_with_its_code_in_item_data():
    from src.i18n import catalog_codes, curated_codes, languages

    app = _app()
    from src.ui.main_window import MainWindow

    w = MainWindow()
    try:
        combo = w._lang_combo
        offered = _codes(combo)
        assert len(offered) == len(set(offered))
        for code in catalog_codes():
            assert code in offered, code
        # The languages translated by hand lead the list.
        assert list(curated_codes()) == offered[: len(curated_codes())]
        # Item text is never the lookup key: every row is named in its own
        # language, and the code rides in the item data.
        from PySide6.QtCore import Qt

        for index in range(combo.count()):
            code = combo.itemData(index)
            assert code
            entry = languages().get(code) or {}
            native = entry.get("native") or entry.get("english") or code
            assert native in combo.itemText(index), (code, combo.itemText(index))
            assert code in (combo.itemData(index, Qt.ToolTipRole) or ""), code
    finally:
        w.close()
        app.processEvents()


def test_flags_appear_only_where_the_desktop_draws_them(monkeypatch):
    from PySide6.QtCore import Qt

    from src.i18n import flag, flag_supported

    app = _app()
    from src.ui.main_window import MainWindow

    # A Windows-style desktop cannot draw regional-indicator pairs: no flag
    # text, and every row keeps the translate glyph instead.
    monkeypatch.setenv("UPDATER_LANGUAGE_FLAGS", "0")
    assert flag("de") == ""
    w = MainWindow()
    try:
        combo = w._lang_combo
        for index in range(combo.count()):
            assert not combo.itemData(index, Qt.UserRole + 1)
            assert not combo.itemIcon(index).isNull()
    finally:
        w.close()
        app.processEvents()

    # Where the platform draws them, the flag replaces the second glyph.
    monkeypatch.setenv("UPDATER_LANGUAGE_FLAGS", "1")
    assert flag_supported() is True
    assert flag("de") == "\U0001f1e9\U0001f1ea"
    w = MainWindow()
    try:
        combo = w._lang_combo
        index = combo.findData("de")
        assert index >= 0
        assert combo.itemData(index, Qt.UserRole + 1) is True
        assert "\U0001f1e9\U0001f1ea" in combo.itemText(index)
        assert combo.itemIcon(index).isNull(), "the flag is the only mark"
    finally:
        w.close()
        app.processEvents()


def test_a_stored_choice_wins_else_the_system_language_is_used():
    from src.i18n import (
        catalog_codes,
        initial_language,
        language_for_locale,
        system_language,
    )

    codes = set(catalog_codes())
    assert language_for_locale("de_AT") == "de"
    assert language_for_locale("zh_Hans_CN") == "zh-CN"
    assert language_for_locale("") == "en"
    assert system_language() in codes
    assert initial_language("fr") == "fr"
    assert initial_language("") in codes
    assert initial_language("not-a-language") == system_language()


def test_english_keeps_the_dialect_the_system_asks_for(monkeypatch):
    """en-GB and en-US are their own entries; other English locales are not.

    The flag follows the dialect, so a reader in the United States is shown a
    USA flag rather than the UK one, and international English claims neither.
    """
    from src.i18n import (
        flag,
        is_english_language,
        language_for_locale,
        tr,
        translator,
    )

    monkeypatch.setenv("UPDATER_LANGUAGE_FLAGS", "1")
    assert language_for_locale("en_US") == "en-US"
    assert language_for_locale("en_GB") == "en-GB"
    # An English locale with no table of its own stays international rather
    # than borrowing either dialect's spelling.
    assert language_for_locale("en_AU") == "en"
    assert flag("en-US") == "\U0001f1fa\U0001f1f8"
    assert flag("en-GB") == "\U0001f1ec\U0001f1e7"
    assert flag("en") == ""

    # English of any region is still English: the app must not translate its
    # release notes into English.
    for code in ("en", "en-GB", "en-US"):
        assert is_english_language(code) is True
    assert is_english_language("de") is False

    old = translator().lang
    try:
        translator().set_language("en-US")
        assert tr("button_centre") == "center button"
        assert tr("app_name") == "Updater CE"
        translator().set_language("en-GB")
        assert tr("button_centre") == "centre button"
        assert tr("settings_offline_mode_desc").startswith(
            "Hide the online community catalogue"
        )
    finally:
        translator().set_language(old)


def test_switching_language_retranslates_the_window_without_a_brand_label():
    """Picking a language re-labels the chrome, even when no sidebar brand exists.

    On Linux with native titlebars the sidebar brand header is never built, so
    ``_brand_label`` is absent; the retranslation must not depend on it.
    """
    from src.i18n import translator

    app = _app()
    from src.ui.main_window import MainWindow

    previous = translator().lang
    w = MainWindow()
    try:
        combo = w._lang_combo
        for code in ("de", "fr", "en"):
            combo.setCurrentIndex(combo.findData(code))
            app.processEvents()
            assert translator().lang == code
            assert w._lang_label.text() == translator().t("nav_language")
            assert w._nav_buttons, "the sidebar nav is built"
            for key, (btn, _idx) in w._nav_buttons.items():
                assert btn.text() == translator().t(key), key
    finally:
        w.close()
        app.processEvents()
        translator().set_language(previous)
