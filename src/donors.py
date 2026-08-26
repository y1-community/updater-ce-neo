"""Donors data — CSV parsing, monthly goal stats, live remote fetch.

Ported from CE's ``load_donors_data`` / ``parse_donors_csv_text`` /
``get_monthly_goal_stats`` / ``fetch_remote_donors_async``.
"""

import csv
import logging
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from .config import DONORS_CSV_URL, MONTHLY_GOAL_USD

logger = logging.getLogger(__name__)


def parse_donors_csv_text(csv_text):
    """Parse raw CSV text into structured donation records (see donors.csv)."""
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
            try:
                amt = float(row[1].strip().replace("$", ""))
            except (ValueError, IndexError):
                amt = 0.0
            date_str = row[2].strip() if len(row) > 2 else ""
            dt = None
            if date_str:
                try:
                    dt = datetime.strptime(date_str.strip(), "%d/%m/%Y")
                except Exception:
                    dt = None
            url = row[3].strip() if len(row) > 3 else ""
            method = row[4].strip() if len(row) > 4 else "Donation"
            msg = row[5].strip() if len(row) > 5 else ""
            donations.append({
                "name": name,
                "amount": amt,
                "date_str": date_str,
                "dt": dt,
                "url": url,
                "method": method,
                "message": msg,
            })
    except Exception as e:
        logger.warning("Error parsing donors CSV: %s", e)
    return donations


def relative_date(dt, now=None):
    """Return a short relative-time string for a donation date.

    Returns a translated string such as 'Today', 'Yesterday', 'On Monday',
    '3 days ago', '2 weeks ago', 'a month ago', etc.
    """
    from .i18n import tr

    if not dt:
        return ""
    now = now or datetime.now()
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


def load_donors_file(paths):
    """Read the first existing non-empty donors.csv among ``paths``."""
    for p in paths:
        if Path(p).is_file():
            try:
                text = Path(p).read_text(encoding="utf-8", errors="ignore")
                if text.strip():
                    return text
            except Exception:
                pass
    return None


def get_monthly_goal_stats(donations, now=None):
    """Amount raised in the current calendar month towards the $200 target."""
    now = now or datetime.now()
    target = MONTHLY_GOAL_USD
    cur = [d for d in donations
           if d.get("dt") and d["dt"].month == now.month
           and d["dt"].year == now.year and d.get("amount", 0) > 0]
    raised = sum(d["amount"] for d in cur)
    percent = min(100.0, max(0.0, (raised / target) * 100.0))
    remaining = max(0.0, target - raised)
    return raised, remaining, percent, target


def fetch_remote_donors_async(on_result):
    """Fetch the latest donors.csv from innioasis.app in a background thread.

    ``on_result(donations_or_None)`` is invoked on success (from the worker
    thread; the caller is expected to marshal to the UI thread).
    """

    def _worker():
        try:
            url = f"{DONORS_CSV_URL}?_t={int(__import__('time').time())}"
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "InnioasisUpdater/1.0",
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                },
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                if resp.status == 200:
                    text = resp.read().decode("utf-8", errors="ignore")
                    parsed = parse_donors_csv_text(text)
                    if parsed:
                        on_result(parsed)
                        return
        except Exception as e:
            logger.debug("Remote donors.csv fetch failed (%s)", e)
        on_result(None)

    threading.Thread(target=_worker, daemon=True).start()
