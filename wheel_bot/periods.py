from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal, Optional

PeriodKey = Literal["all", "prev_year", "cur_year", "prev_month", "cur_month"]


@dataclass(frozen=True)
class PeriodRange:
    start_iso: Optional[str]  # inclusive
    end_iso: Optional[str]  # exclusive


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def resolve_period(key: PeriodKey) -> PeriodRange:
    if key == "all":
        return PeriodRange(None, None)

    today = utc_today()

    if key == "cur_year":
        start = datetime(today.year, 1, 1, tzinfo=timezone.utc)
        end = datetime(today.year + 1, 1, 1, tzinfo=timezone.utc)
        return PeriodRange(_iso(start), _iso(end))

    if key == "prev_year":
        start = datetime(today.year - 1, 1, 1, tzinfo=timezone.utc)
        end = datetime(today.year, 1, 1, tzinfo=timezone.utc)
        return PeriodRange(_iso(start), _iso(end))

    if key == "cur_month":
        start = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
        if today.month == 12:
            end = datetime(today.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(today.year, today.month + 1, 1, tzinfo=timezone.utc)
        return PeriodRange(_iso(start), _iso(end))

    if key == "prev_month":
        first_this = date(today.year, today.month, 1)
        last_prev = first_this - timedelta(days=1)
        start = datetime(last_prev.year, last_prev.month, 1, tzinfo=timezone.utc)
        end = datetime(today.year, today.month, 1, tzinfo=timezone.utc)
        return PeriodRange(_iso(start), _iso(end))

    raise ValueError(f"unknown period {key}")
