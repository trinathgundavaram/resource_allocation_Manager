"""
working_days.py
---------------
Calendar utilities: working-day counts, month boundaries and label helpers.

A "working day" is Monday-Friday minus any holiday that falls on a weekday.
Holidays are passed in as a set/iterable of ISO date strings ("YYYY-MM-DD")
so this module stays free of any DB dependency and is trivially testable.
"""

import calendar
import datetime as _dt

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
MONTH_ABBR = [
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def last_day_of_month(year, month):
    """Return a date for the last calendar day of the month."""
    last = calendar.monthrange(year, month)[1]
    return _dt.date(year, month, last)


def first_day_of_month(year, month):
    return _dt.date(year, month, 1)


def month_label(year, month):
    return f"{MONTH_ABBR[month]} {year}"


def month_label_full(year, month):
    return f"{MONTH_NAMES[month]} {year}"


def working_days(month, year, holidays=None):
    """Count Mon-Fri days in the month, excluding weekday holidays.

    Parameters
    ----------
    month, year : int
    holidays    : iterable of ISO date strings ("YYYY-MM-DD"), optional.

    Returns
    -------
    int : number of working days.
    """
    holiday_set = set(holidays or [])
    days_in_month = calendar.monthrange(year, month)[1]
    count = 0
    for day in range(1, days_in_month + 1):
        d = _dt.date(year, month, day)
        if d.weekday() >= 5:  # Sat=5, Sun=6
            continue
        if d.isoformat() in holiday_set:
            continue
        count += 1
    return count


def working_hours(month, year, hours_per_day, days_per_week, holidays=None):
    """Working hours for a resource in a given month.

        working_days(month, year, holidays) * hours_per_day * (days_per_week / 5)
    """
    wd = working_days(month, year, holidays)
    return wd * float(hours_per_day) * (float(days_per_week) / 5.0)


def month_weeks(year, month, holidays=None):
    """Split a month into calendar weeks that never cross the month boundary.

    A "week" is a maximal run of days that fall in the same Mon-Sun calendar
    week *and* the same month. So the first stretch of the month (even if it
    starts mid-week) is Week 1, and the final stretch (even if it ends mid-week)
    is its own last week. The next month restarts at Week 1.

    Returns a list of dicts:
        {"n", "label", "start" (date), "end" (date), "working_days" (int)}
    where working_days counts Mon-Fri days that are not holidays.
    """
    holiday_set = set(holidays or [])
    last = calendar.monthrange(year, month)[1]
    weeks = []
    start = 1
    n = 0
    while start <= last:
        wd = _dt.date(year, month, start).weekday()  # Mon=0 ... Sun=6
        end = min(last, start + (6 - wd))            # through the coming Sunday
        cnt = 0
        for day in range(start, end + 1):
            d = _dt.date(year, month, day)
            if d.weekday() < 5 and d.isoformat() not in holiday_set:
                cnt += 1
        n += 1
        weeks.append({
            "n": n,
            "label": f"Week {n} of {MONTH_ABBR[month]} {year}",
            "start": _dt.date(year, month, start),
            "end": _dt.date(year, month, end),
            "working_days": cnt,
        })
        start = end + 1
    return weeks


def iter_months(start_year, start_month, end_year, end_month):
    """Yield (year, month) tuples inclusive from start to end."""
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        yield (y, m)
        m += 1
        if m > 12:
            m = 1
            y += 1


def months_between(start_year, start_month, end_year, end_month):
    """List form of iter_months."""
    return list(iter_months(start_year, start_month, end_year, end_month))


def month_index(year, month):
    """Absolute month index used for ordering / comparisons."""
    return year * 12 + month


def add_months(year, month, delta):
    """Return (year, month) shifted by delta months."""
    idx = month_index(year, month) - 1 + delta
    return idx // 12, idx % 12 + 1


if __name__ == "__main__":
    # Phase 2 smoke test.
    print("Working days Jul 2026 (no holidays):", working_days(7, 2026))
    print("Working days Jul 2026 (Jul 4 holiday):",
          working_days(7, 2026, ["2026-07-03"]))
    print("Working hours Jul 2026 @8h/5d:",
          working_hours(7, 2026, 8, 5))
    print("Months Jan-Apr 2026:",
          [month_label(y, m) for y, m in months_between(2026, 1, 2026, 4)])
