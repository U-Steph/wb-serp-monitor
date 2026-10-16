"""Pure hourly-slot arithmetic (no I/O, no clock reads).

Europe/Moscow is a fixed UTC+3 without daylight saving time, so the top of the Moscow hour is
the same instant as the top of the UTC hour. Slots are therefore plain UTC top-of-hour
timestamps (``YYYY-MM-DDTHH:00:00Z``), the same form the ``runs.slot_utc`` column stores.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wbmon.clock import floor_hour, iso_z, parse_iso_z

SLOT_LENGTH = timedelta(hours=1)
CATCH_UP_GRACE = timedelta(minutes=45)


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime is not allowed; use an aware datetime")
    return now.astimezone(UTC)


def current_slot(now: datetime) -> str:
    """The slot ``now`` falls into: its hour floored, as ``YYYY-MM-DDTHH:00:00Z``."""
    return iso_z(floor_hour(_utc(now)))


def next_slot(now: datetime) -> str:
    """The first slot strictly after ``now`` (exactly on ``:00:00`` that is the following hour)."""
    return iso_z(floor_hour(_utc(now)) + SLOT_LENGTH)


def should_catch_up(
    now: datetime, slot: str, has_run: bool, grace: timedelta = CATCH_UP_GRACE
) -> bool:
    """True when ``slot`` has no run yet and is at most ``grace`` old (inclusive).

    A slot in the future is never run, and an older slot is never back-filled: a gap stays a gap.
    """
    late = _utc(now) - parse_iso_z(slot)
    return (not has_run) and timedelta(0) <= late <= grace
