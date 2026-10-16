"""Pure slot arithmetic: vectors around the hour edges, rollovers, grace boundary, MSK premise."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from factories import SRC_DIR, module_imports
from wbmon.clock import parse_iso_z
from wbmon.schedule import CATCH_UP_GRACE, current_slot, next_slot, should_catch_up

VECTORS = [
    ("2026-10-16T09:00:00Z", "2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"),
    ("2026-10-16T09:00:01Z", "2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"),
    ("2026-10-16T09:44:59Z", "2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"),
    ("2026-10-16T09:45:00Z", "2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"),
    ("2026-10-16T09:45:01Z", "2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"),
    ("2026-10-16T09:59:59Z", "2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"),
    ("2026-10-16T23:59:59Z", "2026-10-16T23:00:00Z", "2026-10-17T00:00:00Z"),
    ("2026-12-31T23:30:00Z", "2026-12-31T23:00:00Z", "2027-01-01T00:00:00Z"),
    ("2026-02-28T23:30:00Z", "2026-02-28T23:00:00Z", "2026-03-01T00:00:00Z"),
    ("2028-02-28T23:30:00Z", "2028-02-28T23:00:00Z", "2028-02-29T00:00:00Z"),
    ("2028-02-29T23:00:00Z", "2028-02-29T23:00:00Z", "2028-03-01T00:00:00Z"),
    ("2026-03-29T00:59:59Z", "2026-03-29T00:00:00Z", "2026-03-29T01:00:00Z"),
]


def at(text: str) -> datetime:
    return parse_iso_z(text)


@pytest.mark.parametrize(("now", "current", "following"), VECTORS)
def test_current_and_next_slot_vectors(now, current, following):
    assert current_slot(at(now)) == current
    assert next_slot(at(now)) == following


def test_aware_non_utc_input_is_normalised():
    moscow = datetime.fromisoformat("2026-10-16T12:34:56+03:00")
    assert current_slot(moscow) == "2026-10-16T09:00:00Z"
    assert next_slot(moscow) == "2026-10-16T10:00:00Z"


def test_half_hour_offset_zone_floors_in_utc():
    india = datetime(2026, 10, 16, 14, 40, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert current_slot(india) == "2026-10-16T09:00:00Z"


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        ("2026-10-16T09:00:00Z", True),
        ("2026-10-16T09:44:59Z", True),
        ("2026-10-16T09:45:00Z", True),
        ("2026-10-16T09:45:01Z", False),
        ("2026-10-16T09:59:59Z", False),
        ("2026-10-16T08:59:59Z", False),
    ],
)
def test_should_catch_up_grace_window(now, expected):
    assert should_catch_up(at(now), "2026-10-16T09:00:00Z", False) is expected


def test_should_catch_up_false_when_the_slot_already_ran():
    assert should_catch_up(at("2026-10-16T09:10:00Z"), "2026-10-16T09:00:00Z", True) is False


def test_should_catch_up_never_runs_a_future_slot():
    assert should_catch_up(at("2026-10-16T09:59:59Z"), "2026-10-16T10:00:00Z", False) is False


def test_should_catch_up_custom_grace():
    now = at("2026-10-16T09:20:00Z")
    assert should_catch_up(now, "2026-10-16T09:00:00Z", False, timedelta(minutes=10)) is False
    assert should_catch_up(now, "2026-10-16T09:00:00Z", False, timedelta(minutes=20)) is True


def test_default_grace_is_45_minutes():
    assert CATCH_UP_GRACE == timedelta(minutes=45)


@pytest.mark.parametrize("func", [current_slot, next_slot])
def test_naive_datetime_is_rejected(func):
    with pytest.raises(ValueError, match="naive"):
        func(datetime(2026, 10, 16, 9, 0, 0))


def test_naive_datetime_is_rejected_by_should_catch_up():
    with pytest.raises(ValueError, match="naive"):
        should_catch_up(datetime(2026, 10, 16, 9, 0, 0), "2026-10-16T09:00:00Z", False)


@pytest.mark.parametrize(
    "when",
    [
        datetime(2026, 3, 29, 12, tzinfo=UTC),
        datetime(2026, 7, 1, 12, tzinfo=UTC),
        datetime(2026, 10, 25, 12, tzinfo=UTC),
        datetime(2027, 1, 1, 12, tzinfo=UTC),
    ],
)
def test_moscow_is_a_fixed_utc_plus_3(when):
    assert ZoneInfo("Europe/Moscow").utcoffset(when) == timedelta(hours=3)


def test_schedule_module_is_pure():
    assert module_imports(SRC_DIR / "schedule.py") <= {"__future__", "datetime", "wbmon"}
