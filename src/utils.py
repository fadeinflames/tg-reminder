from __future__ import annotations

from datetime import datetime, timedelta


def format_dt(dt: datetime | None, fmt: str = "%d.%m %H:%M") -> str:
    if not dt:
        return "—"
    return dt.strftime(fmt)


def next_due_date(due_at: datetime | None, repeat_rule: str | None) -> datetime | None:
    if not due_at or not repeat_rule:
        return None
    rule = repeat_rule.lower().strip()
    if rule == "daily":
        return due_at + timedelta(days=1)
    if rule == "weekly":
        return due_at + timedelta(weeks=1)
    if rule == "monthly":
        return _add_months(due_at, 1)
    if rule == "yearly":
        return _add_months(due_at, 12)
    if rule.startswith("every "):
        parts = rule.split()
        if len(parts) >= 3 and parts[1].isdigit():
            amount = int(parts[1])
            unit = parts[2]
            if unit.startswith("week"):
                return due_at + timedelta(weeks=amount)
            if unit.startswith("day"):
                return due_at + timedelta(days=amount)
    return None


def _add_months(dt: datetime, months: int) -> datetime:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - timedelta(days=1)).day
