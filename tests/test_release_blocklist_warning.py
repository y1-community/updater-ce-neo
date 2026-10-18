import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import release_blocklist as rb  # noqa: E402


def test_quoted_tag_matches_exact_name_only(monkeypatch):
    rules = rb.parse_blocklist_csv(
        'software,model,type,version,except,reason\n'
        'Rockbox,Y1,A,"nightly-abc",,Known bad build, see notes\n'
    )
    assert len(rules) == 1
    assert rules[0].reason == "Known bad build, see notes"
    assert rb.is_blocked(rules, "Rockbox", "Y1", "A", "Nightly-ABC", True)
    assert not rb.is_blocked(rules, "Rockbox", "Y1", "A", "nightly-abd", True)


def test_blocking_reason_returns_csv_reason():
    rules = rb.parse_blocklist_csv(
        "software,model,type,version,except,reason\n"
        "Original Software,Y1,A,<3.0.7,,Will not boot on Type A units\n"
    )
    assert rb.blocking_reason(rules, "Original Software", "Y1", "A", "v3.0.6") == (
        "Will not boot on Type A units"
    )
    assert rb.blocking_reason(rules, "Original Software", "Y1", "B", "v3.0.6") is None


def test_warning_is_english_without_key_or_for_english(monkeypatch):
    monkeypatch.delenv(rb.TRANSLATE_KEY_ENV, raising=False)
    rb._TRANSLATION_CACHE.clear()
    msg = rb.warning_message("Will not boot", "de")
    assert msg == rb.WARNING_PREFIX + "Will not boot"
    assert rb.warning_message("Will not boot", "en") == rb.WARNING_PREFIX + "Will not boot"


def test_warning_translates_with_key(monkeypatch):
    monkeypatch.setenv(rb.TRANSLATE_KEY_ENV, "test-key")
    rb._TRANSLATION_CACHE.clear()
    resp = mock.Mock()
    resp.json.return_value = {"data": {"translations": [{"translatedText": "Übersetzt"}]}}
    fake = SimpleNamespace(post=mock.Mock(return_value=resp))
    with mock.patch.object(rb, "requests_module", return_value=fake):
        assert rb.warning_message("Will not boot", "de") == "Übersetzt"
        # Second call is served from the cache, not the network.
        assert rb.warning_message("Will not boot", "de") == "Übersetzt"
    assert fake.post.call_count == 1


def test_warning_falls_back_to_english_on_api_error(monkeypatch):
    monkeypatch.setenv(rb.TRANSLATE_KEY_ENV, "test-key")
    rb._TRANSLATION_CACHE.clear()
    fake = SimpleNamespace(post=mock.Mock(side_effect=OSError("offline")))
    with mock.patch.object(rb, "requests_module", return_value=fake):
        assert rb.warning_message("Will not boot", "fr") == rb.WARNING_PREFIX + "Will not boot"
