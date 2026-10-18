from src import release_blocklist as rb

CSV = (
    "software,model,type,version,except,reason\n"
    "Original Software,Y1,A,<3.0.7,,old builds\n"
    "Rockbox,Y1,A,<0.5,nightly,pre-0.5 builds\n"
    "Rockbox,Y2,,0.1-0.4,,range rule\n"
    "Rockbox,Y1,A,not-a-version,,bad row\n"
)


def _rules():
    return rb.parse_blocklist_csv(CSV)


def test_bad_version_row_is_skipped_and_others_kept():
    assert len(_rules()) == 3


def test_original_software_below_307_is_blocked_for_type_a():
    rules = _rules()
    assert rb.is_blocked(rules, "Original Software", "Y1", "Type A", "3.0.6")
    assert rb.is_blocked(rules, "Original Software", "Y1", "A", "3.0.6")
    assert not rb.is_blocked(rules, "Original Software", "Y1", "A", "3.0.7")
    assert not rb.is_blocked(rules, "Original Software", "Y1", "B", "3.0.6")


def test_rockbox_below_05_blocked_but_nightly_exempt():
    rules = _rules()
    assert rb.is_blocked(rules, "Rockbox", "Y1", "A", "0.4.9")
    assert not rb.is_blocked(rules, "Rockbox", "Y1", "A", "0.5")
    assert not rb.is_blocked(rules, "Rockbox", "Y1", "A", "nightly-abc", is_nightly=True)


def test_range_is_inclusive_on_both_ends():
    rules = _rules()
    assert rb.is_blocked(rules, "Rockbox", "Y2", "", "0.1")
    assert rb.is_blocked(rules, "Rockbox", "Y2", "", "0.4")
    assert not rb.is_blocked(rules, "Rockbox", "Y2", "", "0.5")
    assert not rb.is_blocked(rules, "Rockbox", "Y2", "", "0.0.9.9")


def test_no_rules_blocks_nothing():
    assert rb.is_blocked([], "Rockbox", "Y1", "A", "0.1") is False


def test_html_page_is_not_treated_as_a_blocklist(monkeypatch):
    class Resp:
        text = "<html><body>Not found</body></html>"

        def raise_for_status(self):
            pass

    class Req:
        def get(self, *a, **k):
            return Resp()

    monkeypatch.setattr(rb, "requests_module", lambda: Req())
    assert rb.fetch_blocklist_text() is None


def test_network_failure_returns_none_and_load_gives_empty(monkeypatch, tmp_path):
    class Req:
        def get(self, *a, **k):
            raise OSError("offline")

    monkeypatch.setattr(rb, "requests_module", lambda: Req())
    monkeypatch.setattr(rb, "_cache_path", lambda: tmp_path / "missing.csv")
    assert rb.fetch_blocklist_text() is None
    assert rb.load_blocklist() == []


def test_load_falls_back_to_cached_copy_when_offline(monkeypatch, tmp_path):
    cached = tmp_path / "release_blocklist.csv"
    cached.write_text(CSV, encoding="utf-8")

    class Req:
        def get(self, *a, **k):
            raise OSError("offline")

    monkeypatch.setattr(rb, "requests_module", lambda: Req())
    monkeypatch.setattr(rb, "_cache_path", lambda: cached)
    assert len(rb.load_blocklist()) == 3
