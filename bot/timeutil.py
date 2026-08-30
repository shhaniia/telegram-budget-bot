"""Timezone-aware date helpers. All "now" in the bot goes through here so that
budget periods, due dates, and reminders line up with the user's local day,
not UTC."""
from __future__ import annotations

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from bot import config

try:
    TZ = ZoneInfo(config.TIMEZONE)
except Exception:
    TZ = ZoneInfo("UTC")


def now() -> datetime:
    return datetime.now(TZ)


def today() -> date:
    return now().date()


def today_iso() -> str:
    return today().isoformat()


def yesterday() -> date:
    return today() - timedelta(days=1)


def month_key(d: date | None = None) -> str:
    d = d or today()
    return f"{d.year:04d}-{d.month:02d}"


def week_key(d: date | None = None) -> str:
    d = d or today()
    iso = d.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def period_key(period: str, d: date | None = None) -> str:
    return week_key(d) if period == "weekly" else month_key(d)


def period_bounds(period: str, d: date | None = None) -> tuple[date, date]:
    """Returns (start_date, end_date_inclusive) for the period containing d."""
    d = d or today()
    if period == "weekly":
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=6)
        return start, end
    start = d.replace(day=1)
    if d.month == 12:
        next_month = date(d.year + 1, 1, 1)
    else:
        next_month = date(d.year, d.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end


def parse_date(s: str) -> date:
    """Parses YYYY-MM-DD (raises ValueError on bad input)."""
    return datetime.strptime(s.strip(), "%Y-%m-%d").date()


def add_frequency(d: date, frequency: str) -> date:
    """Advances a date by one billing/recurrence cycle."""
    frequency = frequency.lower()
    if frequency == "daily":
        return d + timedelta(days=1)
    if frequency == "weekly":
        return d + timedelta(weeks=1)
    if frequency == "yearly":
        try:
            return d.replace(year=d.year + 1)
        except ValueError:  # Feb 29
            return d.replace(month=3, day=1, year=d.year + 1)
    # default: monthly
    month = d.month + 1
    year = d.year
    if month > 12:
        month = 1
        year += 1
    day = d.day
    while True:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1  # clamp e.g. Jan 31 -> Feb 28
