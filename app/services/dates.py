"""Working-day arithmetic.

Lives in the app rather than in scripts/ because the leave rules need it -
a seed script importing from the app is fine, the reverse is not.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

# Fixed-date Ugandan public holidays. Movable feasts (Easter, Eid) are not
# handled - a real deployment would need a holidays table rather than this.
FIXED_HOLIDAYS = [
    (1, 1),  # New Year
    (1, 26),  # Liberation Day
    (2, 16),  # Archbishop Janani Luwum Day
    (3, 8),  # International Women's Day
    (5, 1),  # Labour Day
    (6, 3),  # Martyrs' Day
    (6, 9),  # National Heroes Day
    (10, 9),  # Independence Day
    (12, 25),
    (12, 26),
]


def working_days(start: date, end: date) -> Iterator[date]:
    """Weekdays from start to end inclusive. Does not exclude holidays."""
    day = start
    while day <= end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def public_holidays(start: date, end: date) -> set[date]:
    """Weekday public holidays falling in the range."""
    out: set[date] = set()
    for year in range(start.year, end.year + 1):
        for month, dom in FIXED_HOLIDAYS:
            try:
                d = date(year, month, dom)
            except ValueError:
                continue
            if start <= d <= end and d.weekday() < 5:
                out.add(d)
    return out


def chargeable_days(start: date, end: date) -> list[date]:
    """Working days excluding public holidays - what leave actually costs.

    Booking a week that contains Christmas should not spend five days of
    someone's entitlement.
    """
    holidays = public_holidays(start, end)
    return [d for d in working_days(start, end) if d not in holidays]
