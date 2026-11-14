"""The costs line names the active period and uses the reader's currency."""

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture(autouse=True)
def _english():
    from src.i18n import translator

    translator().set_language("en")
    yield
    translator().set_language("en")


def _pin_gbp(monkeypatch):
    from src import fx

    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 0.5)


def test_day_goal_line_is_warm_and_local(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 17, 12)
    stats = get_goal_stats([{"amount": 5.79, "dt": now}], now=now)

    assert stats["scope"] == "daily"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £2.90 of our £10 costs for today</b>"
    )


def test_week_goal_line_uses_the_week_rung(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    stats = get_goal_stats([{"amount": 20, "dt": now}], now=now)

    assert stats["scope"] == "weekly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £10 of our £50 costs for this week</b>"
    )


def test_month_goal_line_uses_the_month_rung(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 1, 12)
    stats = get_goal_stats([{"amount": 40, "dt": now}], now=now)

    assert stats["scope"] == "monthly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £20 of our £150 costs for this month</b>"
    )


def test_every_ordinary_day_opens_on_today():
    from src.donors import goal_scope

    # The weekday no longer decides the opening rung: today's costs do.
    assert goal_scope(datetime(2026, 11, 9, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 10, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 11, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 12, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 13, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 14, 12)) == "daily"
    assert goal_scope(datetime(2026, 11, 15, 12)) == "daily"


def test_the_month_summary_owns_the_first_and_last_day():
    from src.donors import days_in_month, goal_ladder, goal_scope

    # November 2026 has 30 days, so the closing window is the 28th to the 30th.
    assert days_in_month(datetime(2026, 11, 12, 12)) == 30
    for day in (1, 30):
        when = datetime(2026, 11, day, 12)
        assert goal_scope(when) == "monthly"
        assert goal_ladder(when) == ("monthly", "daily")

    # The closing days can name the month, but never the week.
    for day in (28, 29):
        when = datetime(2026, 11, day, 12)
        assert goal_scope(when) == "daily"
        assert goal_ladder(when) == ("daily", "monthly")

    for day in (2, 3, 4, 27):
        when = datetime(2026, 11, day, 12)
        assert goal_scope(when) == "daily"
        assert goal_ladder(when) == ("daily", "weekly", "monthly")


def test_a_short_day_asks_for_today(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    # Thursday 5 November 2026: the 5th is outside the closing/opening window.
    now = datetime(2026, 11, 5, 12)
    donations = [
        {"amount": 30, "dt": datetime(2026, 11, 2, 9)},
        {"amount": 5, "dt": now},
    ]
    assert get_goal_stats(donations, now=now)["scope"] == "daily"


def test_today_hands_over_at_ten_local_units(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)

    almost = get_goal_stats([{"amount": 19.98, "dt": now}], now=now)
    assert almost["scope"] == "daily"

    paid = get_goal_stats([{"amount": 20.00, "dt": now}], now=now)
    assert paid["scope"] == "weekly"


def test_the_week_leads_until_its_target_is_met(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)

    weekly = get_goal_stats([{"amount": 24, "dt": now}], now=now)
    assert weekly["scope"] == "weekly"
    assert format_goal_line(weekly) == (
        "<b>You've helped us raise £12 of our £50 costs for this week</b>"
    )

    monthly = get_goal_stats([{"amount": 100, "dt": now}], now=now)
    assert monthly["scope"] == "monthly"
    assert format_goal_line(monthly) == (
        "<b>You've helped us raise £50 of our £150 costs for this month</b>"
    )


def test_the_week_choice_is_stable_across_refreshes(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    donations = [{"amount": 24, "dt": now}]

    first = get_goal_stats(donations, now=now)["scope"]
    assert first == "weekly"
    for _ in range(5):
        assert get_goal_stats(donations, now=now)["scope"] == first


def test_a_completed_week_always_shows_the_month(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    stats = get_goal_stats([{"amount": 100, "dt": now}], now=now)

    assert stats["scope"] == "monthly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £50 of our £150 costs for this month</b>"
    )


def test_a_strong_rolling_week_does_not_skip_the_weekly_rung(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    # Monday 16 November 2026, so the current week starts today.
    now = datetime(2026, 11, 16, 12)
    quiet = [{"amount": 24, "dt": now}]
    strong = [
        {"amount": 120, "dt": datetime(2026, 11, 15, 12)},
        {"amount": 24, "dt": now},
    ]

    assert get_goal_stats(quiet, now=now)["scope"] == "weekly"
    strong_stats = get_goal_stats(strong, now=now)
    assert strong_stats["last_week_raised"] == 144.0
    assert strong_stats["week_raised"] == 24.0
    assert strong_stats["scope"] == "weekly"


def test_a_covered_month_and_week_name_running_costs_instead_of_a_bar(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    stats = get_goal_stats([{"amount": 300, "dt": now}], now=now)

    assert stats["scope"] is None
    assert stats["appeal"] == "running_costs"
    assert format_goal_line(stats) == (
        "Help us with our running costs, donate today."
    )


def test_a_short_day_still_names_today_after_the_month_is_met(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    donations = [
        {"amount": 300, "dt": datetime(2026, 11, 2, 9)},
        {"amount": 5, "dt": now},
    ]
    stats = get_goal_stats(donations, now=now)

    assert stats["month_reached"] is True
    assert stats["scope"] == "daily"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £2.50 of our £10 costs for today</b>"
    )


def test_a_covered_month_keeps_naming_today_until_the_week_is_covered(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    # $200 early in the month: the month is met, but the last seven days are not.
    stats = get_goal_stats(
        [{"amount": 200, "dt": datetime(2026, 11, 2, 9)}], now=now
    )

    assert stats["scope"] == "daily"
    assert stats["reached"] is False
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £0 of our £10 costs for today</b>"
    )


def test_a_covered_month_shows_today_at_its_target_once_it_is_paid(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 19, 12)
    donations = [
        {"amount": 200, "dt": datetime(2026, 11, 2, 9)},
        {"amount": 20, "dt": now},
    ]
    stats = get_goal_stats(donations, now=now)

    assert stats["scope"] == "daily"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £10 of our £10 costs for today</b>"
    )


def test_the_last_stretch_of_the_month_names_the_weekly_shortfall(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 1, 12)
    stats = get_goal_stats([{"amount": 185, "dt": datetime(2026, 11, 1, 9)}], now=now)

    assert stats["scope"] == "weekly"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £7.50 of our £50 costs for this week</b>"
    )


def test_the_closing_days_ask_for_today_while_today_and_the_week_are_short(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 28, 12)
    stats = get_goal_stats([], now=now)

    assert stats["scope"] == "daily"
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £0 of our £10 costs for today</b>"
    )


def test_the_closing_days_hand_over_to_the_month_once_today_is_covered(
    monkeypatch,
):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 28, 12)

    # When today is covered (>= £10), the closing days climb to the month
    covered_today = get_goal_stats([{"amount": 20, "dt": now}], now=now)
    assert covered_today["scope"] == "monthly"

    # Even with a covered week, if today is short (< £10), the daily costs target
    # is not met, so it displays daily.
    short_today_covered_week = get_goal_stats(
        [{"amount": 100, "dt": datetime(2026, 11, 23, 9)}], now=now
    )
    assert short_today_covered_week["scope"] == "daily"


def test_the_first_and_last_day_lead_with_the_month_even_when_today_is_covered(
    monkeypatch,
):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    for now in (datetime(2026, 11, 1, 12), datetime(2026, 11, 30, 12)):
        stats = get_goal_stats([{"amount": 20, "dt": now}], now=now)
        assert stats["scope"] == "monthly"


def test_the_last_day_still_names_today_when_the_month_is_met(monkeypatch):
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 30, 12)
    donations = [
        {"amount": 300, "dt": datetime(2026, 11, 2, 9)},
        {"amount": 5, "dt": now},
    ]
    stats = get_goal_stats(donations, now=now)

    assert stats["month_reached"] is True
    assert stats["scope"] == "daily"


def test_zero_monthly_and_covered_goals_behavior(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 1, 12)

    # With no donations raised today (< £10), the daily target of £10 is shown
    assert format_goal_line(get_goal_stats([], now=now)) == (
        "<b>You've helped us raise £0 of our £10 costs for today</b>"
    )
    covered = get_goal_stats([{"amount": 300, "dt": now}], now=now)
    assert covered["appeal"] == "running_costs"
    assert format_goal_line(covered) == (
        "Help us with our running costs, donate today."
    )


def test_a_quiet_day_still_names_today(monkeypatch):
    from src.donors import format_goal_line, get_goal_stats

    _pin_gbp(monkeypatch)
    # Thursday 5 November 2026: no donations today, so the daily rung is $0.
    now = datetime(2026, 11, 5, 12)
    donations = [{"amount": 30, "dt": datetime(2026, 11, 2, 9)}]

    stats = get_goal_stats(donations, now=now)

    assert stats["scope"] == "daily"
    assert stats["day_raised"] == 0
    assert format_goal_line(stats) == (
        "<b>You've helped us raise £0 of our £10 costs for today</b>"
    )


def test_status_bar_shows_a_zero_daily_goal(qapp, monkeypatch):
    import src.donation_dialog as dialog
    from src.donors import get_goal_stats

    _pin_gbp(monkeypatch)
    now = datetime(2026, 11, 5, 12)
    stats = get_goal_stats(
        [{"amount": 30, "dt": datetime(2026, 11, 2, 9)}], now=now
    )
    monkeypatch.setattr(dialog, "get_goal_stats", lambda donations: stats)
    monkeypatch.setattr(dialog, "_resolve_bar_state", lambda widget: (0, 0, 1, 1, True))

    bar = dialog.DonationStatusBar(donations=[])

    assert bar._showing_goal is True
    assert "£0" in bar._goal_label.text()
    assert "for today" in bar._goal_label.text()
    bar.deleteLater()


def test_status_bar_uses_the_period_line_not_the_monthly_dollar_line(qapp, monkeypatch):
    import src.donation_dialog as dialog

    stats = {
        "scope": "daily",
        "reached": False,
        "covered": False,
        "shown_raised": 2.9,
        "shown_target": 10.0,
        "shown_percent": 29.0,
        "currency_code": "GBP",
        "currency_symbol": "£",
    }
    monkeypatch.setattr(dialog, "get_goal_stats", lambda donations: stats)
    monkeypatch.setattr(dialog, "_resolve_bar_state", lambda widget: (0, 0, 1, 1, True))
    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")

    bar = dialog.DonationStatusBar(donations=[])
    assert bar._showing_goal is True
    assert "£2.90" in bar._goal_label.text()
    assert "for today" in bar._goal_label.text()
    assert "$" not in bar._goal_label.text()
    bar.deleteLater()


def test_usd_goal_line_keeps_the_dollar_mark(monkeypatch):
    from src import fx
    from src.donors import format_goal_line, get_goal_stats

    monkeypatch.setenv("UPDATER_CURRENCY", "USD")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 1.0)
    now = datetime(2026, 11, 17, 12)
    stats = get_goal_stats([{"amount": 2.9, "dt": now}], now=now)

    assert format_goal_line(stats) == (
        "<b>You've helped us raise $2.90 of our $10 costs for today</b>"
    )


def test_gbp_and_eur_get_local_daily_and_weekly_floors(monkeypatch):
    from src import fx
    from src.donors import goal_targets

    monkeypatch.setenv("UPDATER_CURRENCY", "GBP")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 0.7454)
    assert goal_targets("GBP") == {
        "daily": 10.0,
        "weekly": 50.0,
        "monthly": 150.0,
    }

    monkeypatch.setenv("UPDATER_CURRENCY", "EUR")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 0.8934)
    assert goal_targets("EUR") == {
        "daily": 10.0,
        "weekly": 50.0,
        "monthly": 179.0,
    }


def test_stronger_decimal_currencies_keep_their_existing_targets(monkeypatch):
    from src import fx
    from src.donors import goal_targets

    monkeypatch.setenv("UPDATER_CURRENCY", "CAD")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 1.399)
    assert goal_targets("CAD") == {
        "daily": 14.0,
        "weekly": 70.0,
        "monthly": 280.0,
    }


def test_inflated_currencies_keep_dollar_equivalent_targets(monkeypatch):
    from src import fx
    from src.donors import goal_targets

    monkeypatch.setenv("UPDATER_CURRENCY", "RUB")
    monkeypatch.setattr(fx, "rate", lambda code="USD": 81.42)
    assert goal_targets("RUB") == {
        "daily": 820.0,
        "weekly": 4100.0,
        "monthly": 16300.0,
    }

    monkeypatch.setattr(fx, "rate", lambda code="USD": 158.4)
    assert goal_targets("JPY") == {
        "daily": 1590.0,
        "weekly": 8000.0,
        "monthly": 32000.0,
    }
