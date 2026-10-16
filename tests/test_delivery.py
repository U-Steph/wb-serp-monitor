"""delivery_days: the 21 research vectors plus purity."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from factories import PURE_FORBIDDEN_IMPORTS, SRC_DIR, module_imports
from wbmon.delivery import MAX_HOURS, MSK, delivery_days


def _utc(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


VECTORS = [
    ("2026-10-16T07:00:00Z", 0, 0),
    ("2026-10-16T07:00:00Z", 5, 0),
    ("2026-10-16T07:00:00Z", 13, 0),
    ("2026-10-16T07:00:00Z", 14, 1),
    ("2026-10-16T07:00:00Z", 24, 1),
    ("2026-10-16T07:00:00Z", 37, 1),
    ("2026-10-16T07:00:00Z", 38, 2),
    ("2026-10-16T20:30:00Z", 1, 1),
    ("2026-10-16T20:59:59Z", 0, 0),
    ("2026-10-16T20:59:59Z", 1, 1),
    ("2026-10-16T21:00:00Z", 0, 0),
    ("2026-10-16T21:00:00Z", 23, 0),
    ("2026-10-16T21:00:00Z", 24, 1),
    ("2026-12-31T20:30:00Z", 2, 1),
    ("2028-02-28T20:00:00Z", 26, 2),
    ("2026-03-28T20:00:00Z", 10, 1),
    ("2026-10-24T20:00:00Z", 10, 1),
    ("2026-10-16T07:00:00Z", 218, 9),
]


@pytest.mark.parametrize(("collected_at", "hours", "expected"), VECTORS)
def test_research_vectors(collected_at, hours, expected):
    assert delivery_days(_utc(collected_at), hours) == expected


@pytest.mark.parametrize("hours", [None, -1, 10**9, MAX_HOURS + 1, True, 2.5, "5"])
def test_invalid_hours_give_none_without_exception(hours):
    assert delivery_days(_utc("2026-10-16T07:00:00Z"), hours) is None


def test_max_hours_boundary_is_accepted():
    assert delivery_days(_utc("2026-10-16T07:00:00Z"), MAX_HOURS) == 365


def test_none_hours_does_not_even_look_at_the_datetime():
    assert delivery_days(datetime(2026, 10, 16), None) is None


@pytest.mark.parametrize(
    "moment",
    [
        datetime(2026, 10, 16, 7),
        datetime(2026, 10, 16, 10, tzinfo=timezone(timedelta(hours=3))),
        datetime(2026, 10, 16, 10, tzinfo=MSK),
    ],
)
def test_naive_or_non_utc_datetime_is_rejected(moment):
    with pytest.raises(ValueError, match="UTC"):
        delivery_days(moment, 5)


def test_msk_date_is_used_not_the_utc_date():
    late = _utc("2026-10-16T21:00:00Z")  # already the 17th in Moscow
    assert delivery_days(late, 0) == 0
    assert delivery_days(late, 24) == 1


def test_custom_timezone_changes_the_boundary():
    utc_tz = UTC
    assert delivery_days(_utc("2026-10-16T07:00:00Z"), 14, utc_tz) == 0
    assert delivery_days(_utc("2026-10-16T07:00:00Z"), 14) == 1


def test_delivery_module_is_pure():
    assert not module_imports(SRC_DIR / "delivery.py") & PURE_FORBIDDEN_IMPORTS
