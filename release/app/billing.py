from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class BillingPeriod:
    start: date
    end: date
    statement_date: date
    due_date: date


def shift_month(value: date, months: int, day: int = None) -> date:
    """Return a date shifted by whole months, clamping its day to month end."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    target_day = value.day if day is None else day
    return date(year, month, min(target_day, monthrange(year, month)[1]))


def billing_period_for(
    anchor: date, statement_day: int, due_day: int, due_month_offset: int
) -> BillingPeriod:
    """Calculate the billing cycle that contains ``anchor``."""
    _validate_day("statement_day", statement_day)
    _validate_day("due_day", due_day)
    if (
        not isinstance(due_month_offset, int)
        or isinstance(due_month_offset, bool)
        or due_month_offset not in (0, 1)
    ):
        raise ValueError("due_month_offset must be 0 or 1")

    statement_date = date(anchor.year, anchor.month, statement_day)
    if anchor > statement_date:
        statement_date = shift_month(statement_date, 1, statement_day)

    start = shift_month(statement_date, -1, statement_day) + timedelta(days=1)
    due_date = shift_month(statement_date, due_month_offset, due_day)
    return BillingPeriod(start, statement_date, statement_date, due_date)


def _validate_day(name: str, value: int) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= 28
    ):
        raise ValueError(f"{name} must be an integer from 1 to 28")
