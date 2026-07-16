from __future__ import annotations

from datetime import timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo


def get_timezone(name: str) -> tzinfo:
    """Return IANA timezone; fall back to fixed UTC offset if tzdata is missing."""
    key = (name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(key)
    except Exception:
        fallbacks: dict[str, timezone] = {
            "Europe/Moscow": timezone(timedelta(hours=3)),
            "Europe/Minsk": timezone(timedelta(hours=3)),
            "Europe/Kyiv": timezone(timedelta(hours=2)),
            "UTC": timezone.utc,
        }
        return fallbacks.get(key, timezone.utc)
