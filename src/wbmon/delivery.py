"""Delivery-day calculation. Pure: no I/O.

``delivery_days`` is the number of calendar days (in the delivery region's time zone) between the
moment a page was collected and the moment WB promises delivery: today is 0, tomorrow is 1.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

MSK = ZoneInfo("Europe/Moscow")
MAX_HOURS = 24 * 365


def delivery_days(collected_at_utc: datetime, hours: int | None, tz: tzinfo = MSK) -> int | None:
    """Return the calendar-day difference in ``tz`` after adding ``hours`` to ``collected_at_utc``.

    ``None`` is returned for missing, boolean, negative or absurd (> ``MAX_HOURS``) hours so that no
    value is invented. A naive or non-UTC ``collected_at_utc`` raises ``ValueError``.
    """
    if hours is None or isinstance(hours, bool) or not isinstance(hours, int):
        return None
    if hours < 0 or hours > MAX_HOURS:
        return None
    if collected_at_utc.tzinfo is None or collected_at_utc.utcoffset() != timedelta(0):
        raise ValueError("collected_at_utc must be a timezone-aware UTC datetime")
    start = collected_at_utc.astimezone(tz).date()
    end = (collected_at_utc + timedelta(hours=hours)).astimezone(tz).date()
    return (end - start).days
