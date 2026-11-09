import csv
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import fx
from .config import DONORS_CSV_URL, MONTHLY_GOAL_USD, site_source_urls
from .network import requests_module

logger = logging.getLogger(__name__)

_DONORS_HOST = "innioasis.app"

_ANONYMOUS_DONOR_NAMES = {
    "", "supporter", "supporters", "a supporter", "top supporter",
    "a top supporter", "someone", "anonymous", "anon", "none", "n/a",
    "unknown", "null",
}


def coffee_method(method) -> str:
    low = " ".join(str(method or "").split()).lower()
    if "bmac" in low or "buymeacoffee" in low or "buy me a coffee" in low:
        return "bmac"
    if "ko-fi" in low or "kofi" in low or "ko fi" in low:
        return "kofi"
    return ""


def method_label(method) -> str:
    kind = coffee_method(method)
    if kind == "bmac":
        return "Buy Me a Coffee"
    if kind == "kofi":
        return "Ko-Fi"
    return ""


def is_anonymous_donor(name) -> bool:
    low = " ".join(str(name or "").split()).lower()
    if low in _ANONYMOUS_DONOR_NAMES:
        return True
    return low.startswith(("supporter", "anonymous", "someone"))


def parse_donation_datetime(value):
    """Parse a donors.csv date, with an optional clock time.

    The feed has always used day-first dates (``08/11/2026``). A time may be
    appended (``08/11/2026 14:32`` or with seconds), and ISO 8601 is accepted
    too. Returns ``(datetime, has_time)``; ``has_time`` is True only when the
    source actually carried a clock, so a date-only row keeps day-based
    wording rather than pretending midnight was the moment of the gift.
    """
    raw = str(value or "").strip()
    if not raw:
        return None, False

    has_time = bool(re.search(r"[T ]\d{1,2}:\d{2}", raw))
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed, has_time
    except ValueError:
        pass

    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(raw, fmt), "%H" in fmt
        except ValueError:
            continue
    return None, False


def parse_donors_csv_text(csv_text):
    if not csv_text:
        return []
    donations = []
    try:
        reader = csv.reader(csv_text.splitlines())
        for i, row in enumerate(reader):
            if not row or not any(row):
                continue
            if i == 0 and row[0].strip().lower().startswith("name"):
                continue
            name = row[0].strip() or "Supporter"
            if name.lower().startswith("total"):
                continue
            if name.lower().strip() == "innioasis":
                continue
            try:
                amt = float(row[1].strip().replace("$", ""))
            except (ValueError, IndexError):
                amt = 0.0
            date_str = row[2].strip() if len(row) > 2 else ""
            dt, has_time = parse_donation_datetime(date_str)
            url = row[3].strip() if len(row) > 3 else ""
            method = row[4].strip() if len(row) > 4 else "Donation"
            msg = row[5].strip() if len(row) > 5 else ""
            donations.append({
                "name": name,
                "amount": amt,
                "date_str": date_str,
                "dt": dt,
                "has_time": has_time,
                "url": url,
                "method": method,
                "message": msg,
            })
    except Exception as e:
        logger.warning("Error parsing donors CSV: %s", e)
    return donations


def relative_date(dt, now=None, has_time=False):
    from .i18n import tr

    if not dt:
        return ""
    now = now or datetime.now()
    if has_time:
        seconds = max(0.0, (now - dt).total_seconds())
        minutes = int(seconds // 60)
        if minutes < 1:
            return tr("donate_when_just_now")
        if minutes < 60:
            key = "donate_when_minute" if minutes == 1 else "donate_when_minutes"
            return tr(key).format(count=minutes) if minutes != 1 else tr(key)
        hours = minutes // 60
        if hours < 24:
            key = "donate_when_hour" if hours == 1 else "donate_when_hours"
            return tr(key).format(count=hours) if hours != 1 else tr(key)
    today = now.date()
    dt_date = dt.date() if hasattr(dt, 'date') else dt
    delta = today - dt_date
    days = delta.days

    if days == 0:
        return tr('donate_when_today')
    elif days == 1:
        return tr('donate_when_yesterday')
    elif days <= 6:
        return tr('donate_when_day').format(day=dt.strftime('%A'))
    elif days < 14:
        return tr('donate_when_week')
    elif days < 30:
        weeks = days // 7
        return tr('donate_when_weeks').format(count=weeks)
    elif days < 60:
        return tr('donate_when_month')
    elif days < 365:
        months = days // 30
        return tr('donate_when_months').format(count=months)
    else:
        return tr('donate_when_year')


def format_donation_when(dt, now=None, has_time=False) -> str:
    from .i18n import tr

    if not dt:
        return ""
    now = now or datetime.now()
    if has_time:
        seconds = max(0.0, (now - dt).total_seconds())
        minutes = int(seconds // 60)
        if minutes < 1:
            return tr("donate_when_just_now")
        if minutes < 60:
            key = "donate_when_minute" if minutes == 1 else "donate_when_minutes"
            return tr(key).format(count=minutes) if minutes != 1 else tr(key)
        hours = minutes // 60
        if hours < 24:
            key = "donate_when_hour" if hours == 1 else "donate_when_hours"
            return tr(key).format(count=hours) if hours != 1 else tr(key)
    today = now.date()
    dt_date = dt.date() if hasattr(dt, "date") else dt
    delta = today - dt_date
    days = delta.days

    if days == 0:
        return tr("donate_when_today").lower()
    elif days == 1:
        return tr("donate_when_yesterday").lower()
    elif days < 7:
        return tr("donate_when_days").format(count=days)
    elif days < 14:
        return tr("donate_when_week")

    if dt.year == now.year and dt.month == now.month - 1:
        return tr("donate_when_last_month")
    elif dt.year == now.year - 1 and now.month == 1 and dt.month == 12:
        return tr("donate_when_last_month")

    if dt.year == now.year:
        month_name = tr(f"month_{dt.month:02d}")
        return tr("donate_when_in_month").format(month=month_name)

    if dt.year == now.year - 1:
        return tr("donate_when_last_year")

    return tr("donate_when_in_year").format(year=dt.year)


def load_donors_file(paths):
    for p in paths:
        cand = Path(p)
        if not cand.is_file() and cand.name == "donors.csv" and cand.parent.name == "assets":
            alt = cand.parent / "online" / "donors.csv"
            if alt.is_file():
                cand = alt
        if cand.is_file():
            try:
                text = cand.read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    return text
            except Exception:
                pass
    return None


def get_monthly_goal_stats(donations, now=None):
    now = now or datetime.now()
    target = MONTHLY_GOAL_USD
    cur = [d for d in donations
           if d.get("dt") and d["dt"].month == now.month
           and d["dt"].year == now.year and d.get("amount", 0) > 0]
    raised = sum(d["amount"] for d in cur)
    percent = min(100.0, max(0.0, (raised / target) * 100.0))
    remaining = max(0.0, target - raised)
    return raised, remaining, percent, target


# The daily rung is the everyday ask: it leads on any day whose own costs are
# still short, even when the month target is already met, because it is judged
# on today's gifts alone. The month summary takes over on the first and last
# day of the month, and on the closing days once the day or week has been
# covered, so the end of the month names the whole remaining month rather than
# a rung the community already passed.
GOAL_MONTHLY_OPENING_DAYS = 1
GOAL_MONTHLY_CLOSING_DAYS = 3

WEEKLY_GOAL_USD = MONTHLY_GOAL_USD / 4.0
DAILY_GOAL_USD = MONTHLY_GOAL_USD / 20.0

# Ladder rungs are normally the dollar-equivalent converted into the reader's
# currency. For decimal currencies within an order of magnitude of the dollar,
# a nominal local floor makes more sense: £10/€10 for the day and £50/€50 for
# the week, instead of converting the dollar rungs down to £7.45/€8.93 and
# £37/€45. Highly inflated currencies (RUB, INR, …) and currencies without a
# minor unit (JPY, KRW, …) keep the converted rungs, where the nominal floor
# would be a tiny fraction of the real cost.
GOAL_LOCAL_DAILY = 10.0
GOAL_LOCAL_WEEKLY = 50.0
GOAL_LOCAL_MONTHLY = 150.0
GOAL_LOCAL_RATE_MIN = 0.4
GOAL_LOCAL_RATE_MAX = 2.5

GOAL_COPY_KEYS = {
    "daily": "donate_goal_day_fmt",
    "weekly": "donate_goal_week_fmt",
    "monthly": "donate_goal_fmt",
}


def get_user_currency() -> tuple[str, str]:
    return fx.pair()


def days_in_month(now=None) -> int:
    now = now or datetime.now()
    if now.month == 12:
        first_next = datetime(now.year + 1, 1, 1)
    else:
        first_next = datetime(now.year, now.month + 1, 1)
    return (first_next - datetime(now.year, now.month, 1)).days


def _goal_month_position(now) -> str:
    """Where a day sits in the month: ``"edge"``, ``"closing"`` or ``"open"``.

    The first and last day may lead with the month summary regardless of the
    smaller rungs. The last three days, excluding the last, climb to the month
    once today or this week has been covered. Every other day opens on today.
    """
    total = days_in_month(now)
    if now.day <= GOAL_MONTHLY_OPENING_DAYS or now.day >= total:
        return "edge"
    if now.day > total - GOAL_MONTHLY_CLOSING_DAYS:
        return "closing"
    return "open"


def goal_scope(now=None) -> str:
    """The rung a day would open on before checking what is still short.

    The first and last day of the month open on the month summary; every other
    day opens on today's costs. The ladder always names the first rung still
    short, so a quiet day keeps today's ask and the closing days hand over to
    the month once the day or week is covered (see :func:`get_goal_stats`).
    """
    now = now or datetime.now()
    return "monthly" if _goal_month_position(now) == "edge" else "daily"


def goal_ladder(now=None) -> tuple:
    """Rungs a day's display may name, smallest first.

    The first and last day try the month first and can still fall back to
    today's costs when the month is already covered. The closing days name
    either today or the month, never the week. Every other day climbs from
    today's costs to the week and then the month.
    """
    now = now or datetime.now()
    position = _goal_month_position(now)
    if position == "edge":
        return ("monthly", "daily")
    if position == "closing":
        return ("daily", "monthly")
    return ("daily", "weekly", "monthly")


def goal_period_start(now=None, scope=None) -> datetime:
    """First day of ``scope`` (default: this day's opening rung).

    Weeks are calendar weeks, Monday to Sunday, matching the website's funding
    bar. A day is the calendar day and a month starts on the 1st.
    """
    now = now or datetime.now()
    scope = scope or goal_scope(now)
    if scope == "monthly":
        return datetime(now.year, now.month, 1)
    if scope == "daily":
        return datetime(now.year, now.month, now.day)
    return datetime(now.year, now.month, now.day) - timedelta(days=now.weekday())


def weekly_goal_usd(now=None) -> float:
    return WEEKLY_GOAL_USD


def daily_goal_usd(now=None) -> float:
    return DAILY_GOAL_USD


def goal_targets(code=None) -> dict:
    """Daily, weekly and monthly targets in the reader's currency.

    Decimal currencies near dollar parity keep a local nominal floor, so a UK
    or EU supporter sees the daily goal until £/€10 has arrived and the weekly
    goal until £/€50. Stronger currencies (CAD, AUD) and the dollar retain
    whichever converted target is larger, so no reader gets a smaller ask than
    before. Currencies without a minor unit, or with a highly inflated rate,
    stay on the converted dollar-equivalent targets.
    """
    if not code:
        _symbol, code = fx.pair()
    code = (code or "USD").upper()
    targets = {
        "daily": float(fx.goal(DAILY_GOAL_USD, code)),
        "weekly": float(fx.goal(WEEKLY_GOAL_USD, code)),
        "monthly": float(fx.goal(MONTHLY_GOAL_USD, code)),
    }
    rate = fx.rate(code)
    if fx.decimal_currency(code) and GOAL_LOCAL_RATE_MIN <= rate <= GOAL_LOCAL_RATE_MAX:
        targets["daily"] = max(targets["daily"], GOAL_LOCAL_DAILY)
        targets["weekly"] = max(targets["weekly"], GOAL_LOCAL_WEEKLY)
        targets["monthly"] = max(targets["monthly"], GOAL_LOCAL_MONTHLY)
    return targets


def format_goal_amount(amount) -> str:
    amount = float(amount or 0)
    big = abs(amount) >= 1000
    if amount.is_integer():
        return f"{amount:,.0f}" if big else str(int(amount))
    return f"{amount:,.2f}" if big else f"{amount:.2f}"


def _goal_raised_by_window(donations, now):
    """Money actually raised today, this week, this month and in the last week.

    Only received gifts count, and never a row dated later than ``now``: a
    scheduled donation has not arrived yet. The month counts from the 1st; the
    week is the calendar week (Monday to Sunday) and may begin in the previous
    month; the rolling week is the seven days ending today.
    """
    today = now.date()
    week_start = goal_period_start(now, "weekly").date()
    month_start = today.replace(day=1)
    rolling_start = today - timedelta(days=6)
    day_raised = week_raised = month_raised = rolling_raised = 0.0
    for donation in donations or ():
        dt = donation.get("dt")
        if not dt:
            continue
        amount = float(donation.get("amount", 0) or 0)
        if amount <= 0:
            continue
        day = dt.date() if hasattr(dt, "date") else dt
        if day > today:
            continue
        if day >= month_start:
            month_raised += amount
        if day >= week_start:
            week_raised += amount
        if day >= today:
            day_raised += amount
        if day >= rolling_start:
            rolling_raised += amount
    return day_raised, week_raised, month_raised, rolling_raised


def get_goal_stats(donations, now=None):
    """Raised against the target this day's display should name.

    The ladder follows the website's funding bar: the first and last day lead
    with the month while it is short; otherwise the first rung still short is
    named, from today's costs up through the week to the month. A day still
    short of its own target keeps naming today even after the month is met,
    because it is judged on today's gifts alone. The closing days take the
    month once either today or this week has been covered. A finished month is
    silent.
    """
    now = now or datetime.now()
    day_raised, week_raised, month_raised, rolling_raised = _goal_raised_by_window(donations, now)
    sym, code = fx.pair()
    targets = goal_targets(code)
    # Donations arrive in dollars, but the ladder is judged in the reader's own
    # currency so a nominal £10/€10 day hands over at the same figure shown on
    # screen rather than at the converted value of $10.
    shown_day = fx.local(day_raised, code)
    shown_week = fx.local(week_raised, code)
    shown_month = fx.local(month_raised, code)
    raised_by = {"daily": shown_day, "weekly": shown_week, "monthly": shown_month}
    month_reached = shown_month >= targets["monthly"]
    week_reached = shown_week >= targets["weekly"]
    day_reached = shown_day >= targets["daily"]

    position = _goal_month_position(now)
    scope = None
    if not day_reached:
        # On days where there are less than $10 in donations (or local currency equivalent),
        # always display the daily costs target as it has not been met, even when weekly
        # or monthly costs targets have been met.
        scope = "daily"
    else:
        # When $10 or more has been raised today, users are shown the weekly or monthly goal
        # if they have not been met.
        if position in ("edge", "closing") and not month_reached:
            scope = "monthly"
        elif not week_reached:
            scope = "weekly"
        elif not month_reached:
            scope = "monthly"
        else:
            scope = None

    stats = {
        "scope": scope,
        "raised": 0.0,
        "remaining": 0.0,
        "percent": 0.0,
        "target": 0.0,
        "period_start": None,
        "reached": scope is None,
        "covered": scope is None,
        "day_raised": day_raised,
        "week_raised": week_raised,
        "month_raised": month_raised,
        "last_week_raised": rolling_raised,
        "month_reached": month_reached,
        "currency_symbol": sym,
        "currency_code": code,
        "shown_raised": 0.0,
        "shown_target": 0.0,
        "shown_percent": 0.0,
    }
    if scope is None:
        return stats

    target = targets[scope]
    raised = raised_by[scope]
    stats.update(
        raised=raised,
        remaining=max(0.0, target - raised),
        percent=min(100.0, max(0.0, (raised / target) * 100.0)),
        target=target,
        period_start=goal_period_start(now, scope),
        reached=raised >= target,
        shown_raised=raised,
        shown_target=target,
        shown_percent=fx.share(raised, target),
    )
    return stats


def _fill_goal_template(template, raised_text, target_text):
    """Fill a goal sentence, including catalogs that still hardcode ``$``.

    Older translations wrap the amount in a dollar sign (``${target:.0f}``)
    because the goal used to be shown in USD. The reader's own currency mark is
    passed in already, so the dollar is removed from the template before the
    amounts (which may themselves be dollars) are substituted.
    """
    text = str(template or "").replace("$", "")
    try:
        text = text.format(raised=raised_text, target=target_text)
    except (KeyError, IndexError, ValueError):
        text = re.sub(
            r"\{\s*(raised|target)(?::[^}]*)?\}",
            lambda match: raised_text if match.group(1) == "raised" else target_text,
            text,
        )
    return text.strip()


def format_goal_line(stats) -> str:
    """Warm, period-aware costs line for the status bar and modal.

    ``stats`` comes from :func:`get_goal_stats`, so the sentence names the
    same daily/weekly/monthly rung the support appeal is using. A quiet day
    still names today's smallest ask ("$0 of $10 costs for today"); a zero
    weekly/monthly summary stays silent rather than reading as a cold target.
    An already-covered rung also returns an empty string.
    """
    from .i18n import tr

    stats = stats or {}
    key = GOAL_COPY_KEYS.get(stats.get("scope"))
    if not key or stats.get("reached") or stats.get("covered"):
        return ""
    raised = float(stats.get("shown_raised") or 0)
    target = float(stats.get("shown_target") or 0)
    if target <= 0 or raised >= target:
        return ""
    if raised <= 0 and stats.get("scope") != "daily":
        return ""
    code = str(stats.get("currency_code") or "USD")
    raised_text = fx.money_local(raised, code)
    target_text = fx.money_local(target, code)
    return _fill_goal_template(tr(key), raised_text, target_text)


def cached_donors_path() -> Path:
    from .catalog import _app_cache_dir
    d = _app_cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "donors.csv"


_default_cached_donors_path = cached_donors_path


def _log_connectivity(ok: bool, detail: str) -> None:
    if ok:
        logger.info("Connectivity check: online (%s)", detail)
    else:
        logger.warning("Connectivity check: offline (%s)", detail)


def fetch_remote_donors_async(on_result):
    def _cached():
        text = load_donors_file([cached_donors_path()])
        if not text and cached_donors_path is _default_cached_donors_path:
            try:
                from . import online_data

                text = online_data.read_text("donors.csv")
            except Exception:
                text = ""
        if not text:
            return None
        return parse_donors_csv_text(text) or None

    def _fetch_text(url):
        # Imported here, not at module import: the donation bar is built on
        # every launch, and requests/urllib3 on the first-paint path is ~100 ms
        # and several MB of mapped pages for a fetch that runs seconds later.
        return requests_module().get(
            url,
            headers={
                "User-Agent": "InnioasisUpdater/1.0",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
            timeout=10.0,
        )

    def _worker():
        reason = ""
        for url in site_source_urls("donors.csv"):
            separator = "&" if "?" in url else "?"
            try:
                resp = _fetch_text(f"{url}{separator}_t={int(time.time())}")
                if resp.status_code == 200 and resp.text.strip():
                    text = resp.text
                    parsed = parse_donors_csv_text(text)
                    if parsed:
                        try:
                            cached_donors_path().write_text(text, encoding="utf-8")
                        except Exception as ce:
                            logger.debug("Could not cache donors.csv to disk: %s", ce)
                        _log_connectivity(True, f"{_DONORS_HOST} responded ({len(text)} bytes)")
                        try:
                            on_result(parsed)
                        except RuntimeError:
                            pass
                        return
                    reason = f"{_DONORS_HOST} returned an unusable feed (HTTP {resp.status_code})"
                elif resp.status_code == 200:
                    reason = f"{_DONORS_HOST} returned an empty feed (HTTP {resp.status_code})"
                else:
                    reason = f"{_DONORS_HOST} returned HTTP {resp.status_code}"
            except Exception as e:
                reason = f"{_DONORS_HOST} unreachable ({e})"
                logger.debug("donors.csv source failed: %s", e)

        _log_connectivity(False, reason)
        cached = _cached()
        if cached:
            logger.info("Using the most recent locally cached feed instead")
        try:
            on_result(cached)
        except RuntimeError:
            pass

    threading.Thread(target=_worker, daemon=True).start()
