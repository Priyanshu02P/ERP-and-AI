"""
Pure date maths for recurring purchase orders (no database access).

Occurrence k is always computed from the start date (start + k*step), never from the previous
occurrence, so month-end clamping (31 Jan -> 28 Feb -> 31 Mar) does not drift.
"""
import calendar
from datetime import date, timedelta

WEEKLY, MONTHLY, QUARTERLY, YEARLY = "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY"
_STEP = {WEEKLY: ("days", 7), MONTHLY: ("months", 1), QUARTERLY: ("months", 3), YEARLY: ("months", 12)}
MAX_CYCLES = 1000  # safety net against absurd schedules


def add_months(d: date, months: int) -> date:
    year, month0 = divmod(d.month - 1 + months, 12)
    year, month = d.year + year, month0 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def occurrence_date(start: date, end: date, frequency: str, k: int):
    """Date of the k-th occurrence (0-based), or None if it falls after `end`."""
    unit, step = _STEP[frequency]
    d = start + timedelta(days=step * k) if unit == "days" else add_months(start, step * k)
    return d if d <= end else None


def planned_cycles(start, end, frequency) -> int:
    """How many releases the agreement will produce over its term (>=1 for one-off)."""
    if not (start and end and frequency) or end < start:
        return 1
    count = 0
    while count < MAX_CYCLES and occurrence_date(start, end, frequency, count) is not None:
        count += 1
    return max(count, 1)


def upcoming(start, end, frequency, from_index: int, n: int = 6):
    out = []
    for k in range(from_index, from_index + n):
        d = occurrence_date(start, end, frequency, k)
        if d is None:
            break
        out.append(d)
    return out
