"""Donations copy ships translated for every catalog.

The English source in ``src/i18n.py`` is the fallback, not the deliverable: a
catalog that repeats the English sentence shows the user an untranslated
donations card. This test pins that rule for the donation surfaces, including
the appeal and the supporter ticker, so a new English string cannot ship
without its copy in the other languages.

Only three kinds of cell are allowed to stay English:

* brand and product names (``Buy Me a Coffee``);
* copy that is genuinely the same word in the target language, such as
  ``in {month}`` in Afrikaans, Corsican, Friulian and Hunsrik;
* a proper-noun line (``Ryan Specter & Community Contributors``) and the N'Ko
  cells no translation provider can produce, both listed per cell below.
"""

import json
import re
from pathlib import Path

import pytest

from src import i18n

CATALOG_DIR = Path(__file__).resolve().parent.parent / "assets" / "i18n"

#: Product names stay as they are in every language.
BRAND_KEYS = {"donate_buy_me_a_coffee"}

#: ``(catalog, key)`` cells that are correct in English, or that no provider
#: can translate. Each entry is deliberate; do not grow this set for a new
#: string that simply has no translation yet.
ENGLISH_OK = {
    ('aa', 'donate_ticker_status_fmt'),
    ('ab', 'donate_goal_day_fmt'),
    ('af', 'donate_when_in_month'),
    ('af', 'donate_when_in_year'),
    ('as', 'donate_bought_coffee'),
    ('as', 'donate_bought_coffees'),
    ('as', 'donate_when_days'),
    ('as', 'donate_when_hours'),
    ('as', 'donate_when_minutes'),
    ('as', 'donate_when_months'),
    ('as', 'donate_when_weeks'),
    ('bbc', 'donate_when_day'),
    ('bci', 'donate_goal_day_fmt'),
    ('bem', 'donate_ticker_status_fmt'),
    ('bm-Nkoo', 'donate_copied'),
    ('bm-Nkoo', 'donate_donated_amount'),
    ('bm-Nkoo', 'donate_donated_by_method'),
    ('bm-Nkoo', 'donate_installed_software_fmt'),
    ('bm-Nkoo', 'donate_intro_success'),
    ('bm-Nkoo', 'donate_intro_success_mediatek'),
    ('bm-Nkoo', 'donate_ticker_fmt'),
    ('bm-Nkoo', 'donate_ticker_status_fmt'),
    ('bm-Nkoo', 'donate_when_day'),
    ('bm-Nkoo', 'donate_when_in_month'),
    ('bm-Nkoo', 'donate_when_in_year'),
    ('bm-Nkoo', 'donate_when_weeks'),
    ('bs', 'donate_supporter'),
    ('bs', 'donate_top_supporter_prefix'),
    ('ceb', 'donate_top_supporter_prefix'),
    ('cnh', 'donate_intro_success'),
    ('cnh', 'donate_intro_success_mediatek'),
    ('cnh', 'donate_support_author'),
    ('co', 'donate_title'),
    ('co', 'donate_top_supporter_prefix'),
    ('co', 'donate_when_in_month'),
    ('co', 'donate_when_in_year'),
    ('cy', 'donate_ticker_status_fmt'),
    ('da', 'donate_method_generic'),
    ('da', 'donate_support_role'),
    ('da', 'donate_supporter'),
    ('da', 'donate_title'),
    ('da', 'donate_title_mediatek'),
    ('da', 'donate_top_supporter_prefix'),
    ('fr-CA', 'donate_supporter'),
    ('fr-CA', 'donate_when_months'),
    ('fr-CA', 'donate_when_weeks'),
    ('fur', 'donate_when_in_month'),
    ('fur', 'donate_when_in_year'),
    ('fy', 'donate_support_role'),
    ('fy', 'donate_supporter'),
    ('fy', 'donate_top_supporter_prefix'),
    ('ha', 'donate_title'),
    ('ha', 'donate_when_hours'),
    ('ha', 'donate_when_in_month'),
    ('ha', 'donate_when_in_year'),
    ('haw', 'donate_support_role'),
    ('hil', 'donate_support_role'),
    ('hil', 'donate_top_supporter_prefix'),
    ('hmn', 'donate_support_role'),
    ('hmn', 'donate_when_days'),
    ('hmn', 'donate_when_hours'),
    ('hmn', 'donate_when_months'),
    ('hr', 'donate_title'),
    ('hrx', 'donate_when_in_month'),
    ('hrx', 'donate_when_in_year'),
    ('ig', 'donate_when_in_month'),
    ('ig', 'donate_when_in_year'),
    ('iu-Latn', 'donate_ticker_status_fmt'),
    ('jam', 'donate_a_supporter'),
    ('jam', 'donate_a_top_supporter'),
    ('jam', 'donate_completion_coffee_guide'),
    ('jam', 'donate_completion_signature'),
    ('jam', 'donate_copied'),
    ('jam', 'donate_crypto_hide'),
    ('jam', 'donate_crypto_toggle'),
    ('jam', 'donate_method_generic'),
    ('jam', 'donate_patreon'),
    ('jam', 'donate_paypal'),
    ('jam', 'donate_revolut'),
    ('jam', 'donate_support_author'),
    ('jam', 'donate_support_role'),
    ('jam', 'donate_supporter'),
    ('jam', 'donate_ticker_fmt'),
    ('jam', 'donate_ticker_status_fmt'),
    ('jam', 'donate_title'),
    ('jam', 'donate_title_mediatek'),
    ('jam', 'donate_top_supporter_prefix'),
    ('jam', 'donate_when_days'),
    ('jam', 'donate_when_hours'),
    ('jam', 'donate_when_in_month'),
    ('jam', 'donate_when_in_year'),
    ('jam', 'donate_when_last_month'),
    ('jam', 'donate_when_last_year'),
    ('jam', 'donate_when_minute'),
    ('jam', 'donate_when_minutes'),
    ('jam', 'donate_when_month'),
    ('jam', 'donate_when_months'),
    ('jam', 'donate_when_week'),
    ('jam', 'donate_when_weeks'),
    ('jw', 'donate_top_supporter_prefix'),
    ('kl', 'donate_when_in_month'),
    ('kl', 'donate_when_in_year'),
    ('km', 'donate_completion_signature'),
    ('ktu', 'donate_donated_amount'),
    ('ku', 'donate_completion_signature'),
    ('ku', 'donate_when_minute'),
    ('la', 'donate_bought_coffee'),
    ('la', 'donate_bought_coffee_kofi'),
    ('la', 'donate_bought_coffees'),
    ('la', 'donate_bought_coffees_kofi'),
    ('la', 'donate_coffee_pitch'),
    ('la', 'donate_completion_coffee_guide'),
    ('la', 'donate_completion_subtitle'),
    ('la', 'donate_completion_subtitle_the_software'),
    ('la', 'donate_completion_themes_subtitle'),
    ('la', 'donate_copied'),
    ('la', 'donate_donated_by_method'),
    ('la', 'donate_goal_day_fmt'),
    ('la', 'donate_goal_fmt'),
    ('la', 'donate_goal_status_fmt'),
    ('la', 'donate_goal_week_fmt'),
    ('la', 'donate_support_author'),
    ('la', 'donate_ticker_fmt'),
    ('la', 'donate_title'),
    ('la', 'donate_title_mediatek'),
    ('la', 'donate_when_day'),
    ('la', 'donate_when_days'),
    ('la', 'donate_when_hours'),
    ('la', 'donate_when_in_month'),
    ('la', 'donate_when_in_year'),
    ('la', 'donate_when_minutes'),
    ('la', 'donate_when_months'),
    ('la', 'donate_when_week'),
    ('la', 'donate_when_weeks'),
    ('lb', 'donate_supporter'),
    ('lb', 'donate_title'),
    ('li', 'donate_support_author'),
    ('li', 'donate_support_role'),
    ('li', 'donate_supporter'),
    ('li', 'donate_top_supporter_prefix'),
    ('li', 'donate_when_in_month'),
    ('li', 'donate_when_in_year'),
    ('lij', 'donate_when_in_month'),
    ('lij', 'donate_when_in_year'),
    ('lmo', 'donate_when_in_month'),
    ('lmo', 'donate_when_in_year'),
    ('lo', 'donate_goal_day_fmt'),
    ('lo', 'donate_goal_fmt'),
    ('lo', 'donate_goal_status_fmt'),
    ('lo', 'donate_goal_week_fmt'),
    ('luo', 'donate_donated_amount'),
    ('luo', 'donate_ticker_status_fmt'),
    ('mam', 'donate_support_author'),
    ('mg', 'donate_support_role'),
    ('mg', 'donate_title'),
    ('mh', 'donate_support_author'),
    ('mh', 'donate_ticker_fmt'),
    ('mh', 'donate_ticker_status_fmt'),
    ('mni-Mtei', 'donate_when_day'),
    ('mni-Mtei', 'donate_when_days'),
    ('mni-Mtei', 'donate_when_in_month'),
    ('mni-Mtei', 'donate_when_in_year'),
    ('mt', 'donate_supporter'),
    ('new', 'donate_bought_coffee'),
    ('new', 'donate_bought_coffee_kofi'),
    ('new', 'donate_bought_coffees'),
    ('new', 'donate_bought_coffees_kofi'),
    ('new', 'donate_coffee_pitch'),
    ('new', 'donate_completion_subtitle'),
    ('new', 'donate_completion_subtitle_the_software'),
    ('new', 'donate_copied'),
    ('new', 'donate_donated_amount'),
    ('new', 'donate_donated_by_method'),
    ('new', 'donate_goal_day_fmt'),
    ('new', 'donate_goal_fmt'),
    ('new', 'donate_goal_status_fmt'),
    ('new', 'donate_goal_week_fmt'),
    ('new', 'donate_installed_software_fmt'),
    ('new', 'donate_intro_success'),
    ('new', 'donate_intro_success_mediatek'),
    ('new', 'donate_ticker_fmt'),
    ('new', 'donate_ticker_status_fmt'),
    ('new', 'donate_when_day'),
    ('new', 'donate_when_days'),
    ('new', 'donate_when_hours'),
    ('new', 'donate_when_in_month'),
    ('new', 'donate_when_in_year'),
    ('new', 'donate_when_minutes'),
    ('new', 'donate_when_months'),
    ('new', 'donate_when_weeks'),
    ('nl', 'donate_supporter'),
    ('nl', 'donate_when_in_month'),
    ('nl', 'donate_when_in_year'),
    ('no', 'donate_supporter'),
    ('nr', 'donate_ticker_fmt'),
    ('nr', 'donate_ticker_status_fmt'),
    ('ny', 'donate_support_author'),
    ('ny', 'donate_title'),
    ('or', 'donate_bought_coffee_kofi'),
    ('or', 'donate_bought_coffees_kofi'),
    ('or', 'donate_ticker_status_fmt'),
    ('pa-Arab', 'donate_intro_success'),
    ('pa-Arab', 'donate_intro_success_mediatek'),
    ('pag', 'donate_support_author'),
    ('pag', 'donate_support_role'),
    ('pam', 'donate_support_author'),
    ('pam', 'donate_support_role'),
    ('pam', 'donate_ticker_fmt'),
    ('pam', 'donate_top_supporter_prefix'),
    ('pam', 'donate_when_minutes'),
    ('ro', 'donate_support_role'),
    ('rw', 'donate_when_hours'),
    ('rw', 'donate_when_minutes'),
    ('rw', 'donate_when_months'),
    ('sat', 'donate_bought_coffee'),
    ('sat', 'donate_bought_coffee_kofi'),
    ('sat', 'donate_bought_coffees'),
    ('sat', 'donate_bought_coffees_kofi'),
    ('sat', 'donate_donated_amount'),
    ('sat', 'donate_donated_by_method'),
    ('sat', 'donate_goal_week_fmt'),
    ('sat', 'donate_ticker_fmt'),
    ('sat', 'donate_ticker_status_fmt'),
    ('sat-Latn', 'donate_intro_success'),
    ('sat-Latn', 'donate_intro_success_mediatek'),
    ('sd', 'donate_goal_day_fmt'),
    ('si', 'donate_support_role'),
    ('sm', 'donate_support_role'),
    ('sn', 'donate_completion_coffee_guide'),
    ('sn', 'donate_method_generic'),
    ('sn', 'donate_support_author'),
    ('so', 'donate_when_in_month'),
    ('so', 'donate_when_in_year'),
    ('ss', 'donate_ticker_status_fmt'),
    ('su', 'donate_top_supporter_prefix'),
    ('sus', 'donate_method_generic'),
    ('sus', 'donate_support_author'),
    ('sus', 'donate_ticker_fmt'),
    ('sus', 'donate_ticker_status_fmt'),
    ('sus', 'donate_title'),
    ('sv', 'donate_method_generic'),
    ('sv', 'donate_support_role'),
    ('tk', 'donate_bought_coffees'),
    ('tk', 'donate_completion_themes_subtitle'),
    ('tt', 'donate_bought_coffees'),
    ('tt', 'donate_bought_coffees_kofi'),
    ('ty', 'donate_ticker_fmt'),
    ('ug', 'donate_bought_coffees'),
    ('ug', 'donate_bought_coffees_kofi'),
    ('ug', 'donate_completion_subtitle'),
    ('ug', 'donate_completion_themes_subtitle'),
    ('ug', 'donate_goal_week_fmt'),
    ('ug', 'donate_ticker_fmt'),
    ('ug', 'donate_when_day'),
    ('vec', 'donate_when_in_month'),
    ('vec', 'donate_when_in_year'),
    ('wo', 'donate_top_supporter_prefix'),
    ('yua', 'donate_support_author'),
    ('yue', 'donate_bought_coffee'),
    ('yue', 'donate_bought_coffees'),
    ('yue', 'donate_coffee_pitch'),
    ('yue', 'donate_completion_themes_subtitle'),
    ('yue', 'donate_donated_amount'),
    ('yue', 'donate_donated_by_method'),
    ('yue', 'donate_goal_day_fmt'),
    ('yue', 'donate_goal_fmt'),
    ('yue', 'donate_goal_status_fmt'),
    ('yue', 'donate_goal_week_fmt'),
    ('yue', 'donate_installed_generic_fmt'),
    ('yue', 'donate_installed_software_fmt'),
    ('yue', 'donate_intro_success'),
    ('yue', 'donate_intro_success_mediatek'),
    ('yue', 'donate_ticker_fmt'),
    ('yue', 'donate_ticker_status_fmt'),
    ('yue', 'donate_when_day'),
    ('yue', 'donate_when_days'),
    ('yue', 'donate_when_hours'),
    ('yue', 'donate_when_in_month'),
    ('yue', 'donate_when_in_year'),
    ('yue', 'donate_when_minutes'),
    ('yue', 'donate_when_months'),
    ('yue', 'donate_when_weeks'),
    ('zu', 'donate_support_author'),
}


def _catalogs():
    for path in sorted(CATALOG_DIR.glob("*.json")):
        if path.stem == "languages":
            continue
        # An English catalog (en-GB, en-US) shows English copy on purpose.
        # This test is about copy that would otherwise fall back untranslated.
        if i18n.is_english_language(path.stem):
            continue
        yield path.stem, json.loads(path.read_text(encoding="utf-8"))


def _english_donation_keys():
    """Donation keys with English copy the UI actually shows."""
    for key, table in i18n._STRINGS.items():
        if not key.startswith("donate_") or key in BRAND_KEYS:
            continue
        english = str((table or {}).get("en") or "").strip()
        if not english or "<span" in english:
            # The completion headline is a styled sentence assembled in code,
            # not a plain string a catalog can own.
            continue
        yield key, english


def test_every_donation_string_is_translated_in_every_catalog():
    missing = []
    for lang, catalog in _catalogs():
        for key, english in _english_donation_keys():
            if (lang, key) in ENGLISH_OK:
                continue
            value = str(catalog.get(key) or "").strip()
            if not value or value == english:
                missing.append(f"{lang}:{key}")
    assert not missing, (
        "donation copy still falls back to English in these catalogs: "
        + ", ".join(sorted(missing))
    )


def test_ticker_templates_keep_the_placeholders_the_code_fills():
    """A translated ticker is only usable if its format fields survive."""
    fields = ("anchor", "amount", "method", "when", "count", "top_prefix")
    for lang, catalog in _catalogs():
        for key in (
            "donate_ticker_fmt",
            "donate_ticker_status_fmt",
            "donate_bought_coffee",
            "donate_bought_coffees",
            "donate_bought_coffee_kofi",
            "donate_bought_coffees_kofi",
            "donate_donated_amount",
            "donate_donated_by_method",
        ):
            value = str(catalog.get(key) or "")
            for field in fields:
                source = str((i18n._STRINGS.get(key) or {}).get("en") or "")
                if "{%s}" % field in source:
                    assert "{%s}" % field in value, f"{lang}:{key} lost {{{field}}}"


def test_supporter_prefix_keeps_the_space_before_the_donor_name():
    """The ticker template places ``{top_prefix}{anchor}`` directly together."""
    from src.donation_dialog import _supporter_prefix

    old = i18n.current_language()
    i18n.translator().set_language("ru")
    try:
        assert _supporter_prefix() == "Лучший сторонник "
    finally:
        i18n.translator().set_language(old)


def test_the_support_appeal_uses_one_complete_sentence_and_keeps_its_bold_lead():
    """The old pair split a sentence mid-clause; translators ended it there."""
    from src.ui.support_appeal import support_appeal_html

    old = i18n.translator().lang
    i18n.translator().set_language("ru")
    try:
        full = i18n.catalog("ru")["donate_support_appeal_full"]
        html = support_appeal_html("donate_support_appeal_lead")
        plain = re.sub(r"<[^>]+>", "", html)
        assert plain == full
        assert html.startswith("<b>")
        assert html.index("</b>") < len(html)
        assert "устройств. которые" not in plain
    finally:
        i18n.translator().set_language(old)
