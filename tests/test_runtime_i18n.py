"""Runtime translation fills English gaps without disturbing the UI on failure."""

import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from src import i18n
from src.runtime_i18n import RuntimeTranslator
from src.ui.widgets import Card


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _patch_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(i18n, "english_sources", lambda: {"Hello": ("hello",)})
    monkeypatch.setattr(i18n, "translation_for_english", lambda text, lang: "")
    # Learned translations go to the user's catalog; keep that out of the real
    # one while the suite runs.
    monkeypatch.setattr(i18n, "user_catalog_dir", lambda: tmp_path / "i18n")
    # The catalog cache outlives a single test: start each one from clean copy.
    i18n._CATALOGS.pop("ru", None)


def test_english_source_lookup_is_the_runtime_allow_list():
    assert "Home" in i18n.english_sources()
    assert i18n.translation_for_english("Home", "ru")
    assert i18n.translation_for_english("Home", "ru") != "Home"
    assert "Firmware Thing" not in i18n.english_sources()


def test_runtime_translator_fills_hidden_widgets_and_caches(app, tmp_path, monkeypatch):
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def fetch(text, lang):
        calls.append((text, lang))
        return "Привет"

    box = QWidget()
    label = QLabel("Hello", box)
    button = QPushButton("Hello", box)
    edit = QLineEdit(box)
    edit.setPlaceholderText("Hello")
    combo = QComboBox(box)
    combo.addItem("Hello")

    translator = RuntimeTranslator(
        cache_path=tmp_path / "runtime.json",
        fetch=fetch,
        scan_interval_ms=600_000,
    )
    translator.start("ru")
    translator.scan(box)

    assert label.text().startswith("Hello .")
    assert button.text().startswith("Hello .")
    QTest.qWait(700)

    assert label.text() == "Привет"
    assert button.text() == "Привет"
    assert edit.placeholderText() == "Привет"
    assert combo.itemText(0) == "Привет"
    assert calls == [("Hello", "ru")]

    cached = json.loads((tmp_path / "runtime.json").read_text(encoding="utf-8"))
    assert cached["entries"]["ru"]["Hello"] == "Привет"
    translator.stop()

    second_calls = []
    second = RuntimeTranslator(
        cache_path=tmp_path / "runtime.json",
        fetch=lambda text, lang: second_calls.append(text) or "Привет",
        scan_interval_ms=600_000,
    )
    second_box = QWidget()
    second_label = QLabel("Hello", second_box)
    second.start("ru")
    second.scan(second_box)
    assert second_label.text() == "Привет"
    assert second_calls == []
    second.stop()


def test_runtime_translation_failure_restores_english(app, tmp_path, monkeypatch):
    _patch_sources(monkeypatch, tmp_path)

    def refuse(text, lang):
        raise RuntimeError("translation refused")

    box = QWidget()
    label = QLabel("Hello", box)
    translator = RuntimeTranslator(
        cache_path=tmp_path / "failed.json",
        fetch=refuse,
        scan_interval_ms=600_000,
    )
    translator.start("ru")
    translator.scan(box)
    QTest.qWait(700)

    assert label.text() == "Hello"
    assert not label.text().startswith("Hello .")
    assert not (tmp_path / "failed.json").exists()
    translator.stop()


def test_english_scan_never_asks_a_translation_service(app, tmp_path, monkeypatch):
    """English is the source language: no timer, scan or provider call."""
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def fetch(text, lang):
        calls.append((text, lang))
        return "unreachable"

    box = QWidget()
    label = QLabel("Hello", box)
    translator = RuntimeTranslator(
        cache_path=tmp_path / "english.json",
        fetch=fetch,
        scan_interval_ms=600_000,
    )
    translator.start("en")
    translator.scan(box)
    QTest.qWait(400)

    assert calls == []
    assert label.text() == "Hello"
    assert not (tmp_path / "english.json").exists()
    assert not translator._scan_timer.isActive()
    translator.stop()


def test_rich_text_is_translated_run_by_run_and_keeps_its_markup(app, tmp_path, monkeypatch):
    """The donations appeal is ``<b>lead</b> followup``: translate text, not tags."""
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def fetch(text, lang):
        calls.append((text, lang))
        return "Привет"

    box = QWidget()
    label = QLabel("<b>Hello</b> Hello", box)
    label.setTextFormat(Qt.TextFormat.RichText)
    translator = RuntimeTranslator(
        cache_path=tmp_path / "rich.json",
        fetch=fetch,
        scan_interval_ms=600_000,
    )
    translator.start("ru")
    translator.scan(box)
    QTest.qWait(700)

    assert label.text() == "<b>Привет</b> Привет"
    assert calls == [("Hello", "ru")]
    translator.stop()


def test_translation_worker_never_runs_a_batch_for_english():
    from src.runtime_i18n import _TranslationWorker

    calls = []
    worker = _TranslationWorker(
        "en", ["Hello"], lambda text, lang: calls.append(text) or "x"
    )
    worker.run()

    assert calls == []


def test_learned_translations_become_part_of_the_users_catalog(app, tmp_path, monkeypatch):
    """A lookup is stored where the next launch and ``tr()`` can read it."""
    _patch_sources(monkeypatch, tmp_path)
    box = QWidget()
    QLabel("Hello", box)
    translator = RuntimeTranslator(
        cache_path=tmp_path / "cache.json",
        fetch=lambda text, lang: "Привет",
        scan_interval_ms=600_000,
    )
    try:
        translator.start("ru")
        translator.scan(box)
        QTest.qWait(900)

        catalog_file = tmp_path / "i18n" / "ru.json"
        assert catalog_file.is_file()
        stored = json.loads(catalog_file.read_text(encoding="utf-8"))
        assert stored["hello"] == "Привет"
        assert i18n.catalog("ru")["hello"] == "Привет"
    finally:
        translator.stop()
        # The catalog cache is global; leave it as the rest of the suite found it.
        i18n._CATALOGS.pop("ru", None)


def test_regional_variant_reads_its_base_language_before_english():
    german = i18n.catalog("de")
    assert german.get("nav_home")
    assert i18n.translation_for_english("Home", "de-AT") == german["nav_home"]


def test_english_is_never_written_to_a_user_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "user_catalog_dir", lambda: tmp_path / "i18n")
    assert i18n.remember_translations("en", {"Home": "Home"}) == 0
    assert not (tmp_path / "i18n").exists()


_POWER_ON_TEMPLATE = (
    "To turn your {label} on, unplug the USB and hold the {button}."
)


def _patch_template_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(
        i18n,
        "english_sources",
        lambda: {_POWER_ON_TEMPLATE: ("install_power_on_steps_fmt",)},
    )
    monkeypatch.setattr(i18n, "english_templates", lambda: (_POWER_ON_TEMPLATE,))
    monkeypatch.setattr(i18n, "translation_for_english", lambda text, lang: "")
    monkeypatch.setattr(i18n, "user_catalog_dir", lambda: tmp_path / "i18n")
    i18n._CATALOGS.pop("ru", None)


def test_english_templates_include_the_power_on_sentence():
    assert _POWER_ON_TEMPLATE in i18n.english_templates()


def test_runtime_translator_matches_a_formatted_sentence_and_renders_its_values(
    app, tmp_path, monkeypatch
):
    """The completion card shows a formatted template, not the English source."""
    _patch_template_sources(monkeypatch, tmp_path)
    calls = []

    def fetch(text, lang):
        calls.append(text)
        assert "{" not in text  # the provider never sees a placeholder
        return "Отключите ⟦0⟧, затем удерживайте ⟦1⟧, пока он не включится."

    box = QWidget()
    label = QLabel(
        "To turn your Y2 on, unplug the USB and hold the кнопка питания.", box
    )
    translator = RuntimeTranslator(
        cache_path=tmp_path / "template.json",
        fetch=fetch,
        scan_interval_ms=600_000,
    )
    try:
        translator.start("ru")
        translator.scan(box)
        QTest.qWait(700)

        assert label.text() == (
            "Отключите Y2, затем удерживайте кнопка питания, пока он не включится."
        )
        assert calls == ["To turn your ⟦0⟧ on, unplug the USB and hold the ⟦1⟧."]
    finally:
        translator.stop()
        i18n._CATALOGS.pop("ru", None)


def test_a_template_translation_that_loses_a_placeholder_leaves_english(
    app, tmp_path, monkeypatch
):
    _patch_template_sources(monkeypatch, tmp_path)
    original = "To turn your Y2 on, unplug the USB and hold the кнопка питания."

    box = QWidget()
    label = QLabel(original, box)
    translator = RuntimeTranslator(
        cache_path=tmp_path / "damaged.json",
        fetch=lambda text, lang: "Отключите ⟦0⟧ и включите его снова.",
        scan_interval_ms=600_000,
    )
    try:
        translator.start("ru")
        translator.scan(box)
        QTest.qWait(700)

        assert label.text() == original
        assert not (tmp_path / "damaged.json").exists()
    finally:
        translator.stop()
        i18n._CATALOGS.pop("ru", None)


def test_a_learned_template_replaces_a_catalog_value_still_in_english(
    tmp_path, monkeypatch
):
    """An English cell is a gap, not curated copy: learning may repair it."""
    _patch_template_sources(monkeypatch, tmp_path)
    russian = "Отключите {label}, затем удерживайте {button}, пока он не включится."
    # The shipped catalog is complete now; isolate the rule under test with a
    # catalog cell that is still the English source, as a failed pass leaves it.
    monkeypatch.setattr(
        i18n,
        "catalog",
        lambda code: {"install_power_on_steps_fmt": _POWER_ON_TEMPLATE},
    )
    try:
        assert i18n.remember_translations("ru", {_POWER_ON_TEMPLATE: russian}) == 1
        stored = json.loads(
            (tmp_path / "i18n" / "ru.json").read_text(encoding="utf-8")
        )
        assert stored["install_power_on_steps_fmt"] == russian
    finally:
        i18n._CATALOGS.pop("ru", None)


def test_the_russian_completion_sentence_is_fully_translated():
    """Regression for the card that mixed English into a Russian install."""
    old = i18n.current_language()
    i18n.translator().set_language("ru")
    try:
        from src.config import install_power_on_steps

        text = install_power_on_steps("Y2")
        assert "Unplug" not in text
        assert "Y2" in text
        assert "питания" in text
    finally:
        i18n.translator().set_language(old)


def test_the_mixed_completion_sentence_is_translated_from_the_catalog(app, tmp_path):
    """The screenshot case: English template, Russian button value already in it."""
    calls = []
    box = QWidget()
    label = QLabel(
        "To turn your Y2 on, unplug the USB and hold the кнопка питания.", box
    )
    translator = RuntimeTranslator(
        cache_path=tmp_path / "catalog.json",
        fetch=lambda text, lang: calls.append(text) or "unused",
        scan_interval_ms=600_000,
    )
    old = i18n.current_language()
    i18n.translator().set_language("ru")
    try:
        translator.start("ru")
        translator.scan(box)

        # Values are substituted verbatim; `install_power_on_steps` supplies
        # the accusative `кнопку питания`, while this literal label carries the
        # nominative form it was built with.
        assert label.text() == (
            "Чтобы включить Y2, отсоедините USB и удерживайте кнопка питания."
        )
        assert calls == []  # the bundled catalog already has the template
    finally:
        translator.stop()
        i18n.translator().set_language(old)


def test_shortcut_card_relabels_without_rebuilding_its_layout(app):
    old = i18n.current_language()
    i18n.translator().set_language("ru")
    try:
        from src.ui.settings_page import SettingsPage

        page = SettingsPage.__new__(SettingsPage)
        page._shortcuts_card = Card("settings_shortcuts_title")
        switch = QPushButton()
        caption = QLabel("old caption")
        page._switch_rows = {switch: (QWidget(), caption)}
        page._shortcut_switches = {("updater", "desktop"): switch}
        page._shortcut_label_keys = {
            ("updater", "desktop"): (
                "settings_shortcut_updater",
                "settings_shortcut_desktop",
            )
        }

        SettingsPage._retranslate_shortcut_switches(page)

        assert page._shortcuts_card.title() == "Ярлыки приложений"
        assert caption.text() == i18n.tr(
            "settings_shortcut_updater"
        ) + " — " + i18n.tr("settings_shortcut_desktop")
        assert switch.text() == ""
    finally:
        i18n.translator().set_language(old)


def test_brand_title_excluded_from_runtime_translation(app, tmp_path, monkeypatch):
    """Ensure updater title is not queued for external translation fetching."""
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def fetch(text, lang):
        calls.append((text, lang))
        return "Updated"

    brand_label = QLabel("Updater")
    brand_label.setObjectName("brandLabel")
    brand_label._skip_runtime_i18n = True

    translator = RuntimeTranslator(
        cache_path=tmp_path / "runtime.json",
        fetch=fetch,
        scan_interval_ms=600_000,
    )
    translator.start("de")
    try:
        translator.scan(brand_label)
        assert calls == []
        assert brand_label.text() == "Updater"
        assert translator._source_for("Updater") == ""
        assert translator._source_for("Innioasis Updater") == ""
    finally:
        translator.stop()

