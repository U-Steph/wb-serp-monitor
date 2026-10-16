"""STOR-04 read paths on a small deterministic database (index proof lives in test_storage_perf)."""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from wbmon.models import ObservationRow, QueryResult
from wbmon.storage import reader
from wbmon.storage.db import connect_ro, connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer

T = ["2026-10-01T00:00:00Z", "2026-10-01T01:00:00Z", "2026-10-01T02:00:00Z"]


def obs(position: int, nm_id: int, at: str, **overrides) -> ObservationRow:
    values: dict = {name: None for name in ObservationRow.__dataclass_fields__}
    values.update(collected_at=at, position=position, nm_id=nm_id, is_promoted=0)
    values.update(overrides)
    return ObservationRow(**values)


def ok(at: str, *rows: ObservationRow, status: str = "ok") -> QueryResult:
    return QueryResult(
        status=status,
        started_at=at,
        duration_ms=10,
        observations=rows,
        pages_fetched=1,
        is_complete=status == "ok",
        http_status=200,
    )


def blocked(at: str) -> QueryResult:
    return QueryResult(
        status="blocked", started_at=at, duration_ms=5, http_status=403, error_class="http_403"
    )


@pytest.fixture(scope="module")
def db_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("reader") / "reader.sqlite3"
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        w = Writer(con)
        ids = w.upsert_queries(["alpha", "beta", "gamma"], T[0])
        a, b, c = ids["alpha"], ids["beta"], ids["gamma"]
        assert (a, b, c) == (1, 2, 3)

        run1 = w.start_run(
            trigger="scheduler",
            started_at=T[0],
            query_source="bundled",
            params_json="{}",
            top_n=3,
            n_queries=3,
            slot_utc=T[0],
        )
        w.record_query(
            run1,
            a,
            ok(
                T[0],
                obs(1, 11, T[0], name="old 11", brand="B1", price_rub=100.0),
                obs(2, 12, T[0], name="old 12", brand="B2", price_rub=200.0),
                obs(3, 13, T[0], name="old 13", brand="B3", price_rub=300.0),
            ),
        )
        w.record_query(run1, b, ok(T[0], obs(1, 21, T[0], name="b21", brand="BB")))
        w.record_query(run1, c, blocked(T[0]))
        w.finish_run(run1, T[0])

        run2 = w.start_run(
            trigger="scheduler",
            started_at=T[1],
            query_source="bundled",
            params_json="{}",
            top_n=3,
            n_queries=3,
            slot_utc=T[1],
        )
        w.record_query(
            run2,
            a,
            ok(
                T[1],
                obs(1, 12, T[1], name="new 12", brand="B2", price_rub=190.0, is_promoted=1),
                obs(2, 11, T[1], name="mid 11", brand="B1", price_rub=110.0),
                obs(3, 14, T[1], name="new 14", brand="B4"),
            ),
        )
        w.record_query(run2, b, blocked(T[1]))
        w.record_query(run2, c, blocked(T[1]))
        w.finish_run(run2, T[1])

        run3 = w.start_run(
            trigger="manual",
            started_at=T[2],
            query_source="bundled",
            params_json="{}",
            top_n=3,
            n_queries=3,
        )
        w.record_query(
            run3,
            a,
            ok(
                T[2],
                obs(1, 11, T[2], name="new 11", brand="B1x", price_rub=120.0),
                status="partial",
            ),
        )
        w.record_query(
            run3,
            b,
            ok(T[2], obs(1, 21, T[2], name="b21", brand="BB"), obs(2, 22, T[2], name="b22")),
        )
        w.finish_run(run3, T[2])
    return path


@pytest.fixture
def ro(db_path):
    with contextlib.closing(connect_ro(db_path)) as con:
        yield con


def test_list_queries_follow_sheet_order(ro):
    rows = reader.list_queries(ro)
    assert [(r["id"], r["text"], r["sheet_order"]) for r in rows] == [
        (1, "alpha", 1),
        (2, "beta", 2),
        (3, "gamma", 3),
    ]
    assert set(rows[0]) == {"id", "text", "sheet_order", "first_seen_at", "last_seen_at"}


def test_nm_ids_for_query_aggregates_history(ro):
    rows = reader.nm_ids_for_query(ro, 1)
    assert [r["nm_id"] for r in rows] == [11, 12, 13, 14]
    by_id = {r["nm_id"]: r for r in rows}
    assert by_id[11] == {
        "nm_id": 11,
        "n_seen": 3,
        "best_position": 1,
        "worst_position": 2,
        "last_seen_at": T[2],
        "name": "new 11",
        "brand": "B1x",
    }
    assert (by_id[12]["n_seen"], by_id[12]["best_position"], by_id[12]["worst_position"]) == (
        2,
        1,
        2,
    )
    assert (by_id[12]["name"], by_id[12]["last_seen_at"]) == ("new 12", T[1])
    assert (by_id[13]["n_seen"], by_id[13]["name"]) == (1, "old 13")
    assert by_id[14]["last_seen_at"] == T[1]
    assert reader.nm_ids_for_query(ro, 3) == []


def test_positions_for_orders_by_time_then_nm_id(ro):
    rows = reader.positions_for(ro, 1, [12, 11])
    assert [(r["collected_at"], r["nm_id"], r["position"]) for r in rows] == [
        (T[0], 11, 1),
        (T[0], 12, 2),
        (T[1], 11, 2),
        (T[1], 12, 1),
        (T[2], 11, 1),
    ]
    assert set(rows[0]) == {
        "run_id",
        "collected_at",
        "nm_id",
        "position",
        "price_rub",
        "is_promoted",
    }
    assert [r["is_promoted"] for r in rows] == [0, 0, 0, 1, 0]
    assert [r["run_id"] for r in rows] == [1, 1, 2, 2, 3]


def test_positions_for_empty_list_does_not_query():
    class Exploding:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("no query expected")

    assert reader.positions_for(Exploding(), 1, []) == []  # type: ignore[arg-type]
    assert reader.latest_characteristics(Exploding(), 1, []) == []  # type: ignore[arg-type]


def test_too_many_ids_rejected(ro):
    too_many = list(range(reader.MAX_IN_IDS + 1))
    with pytest.raises(ValueError):
        reader.positions_for(ro, 1, too_many)
    with pytest.raises(ValueError):
        reader.latest_characteristics(ro, 1, too_many)
    at_limit = [1_000_000 + i for i in range(reader.MAX_IN_IDS)]
    assert reader.positions_for(ro, 1, at_limit) == []


def test_ids_are_bound_not_interpolated(ro):
    assert reader.positions_for(ro, 1, ["11 OR 1=1"]) == []  # type: ignore[list-item]


def test_latest_characteristics_one_latest_row_per_id(ro):
    rows = reader.latest_characteristics(ro, 1, [11, 12, 99])
    assert [r["nm_id"] for r in rows] == [11, 12]
    by_id = {r["nm_id"]: r for r in rows}
    assert (by_id[11]["name"], by_id[11]["brand"], by_id[11]["collected_at"]) == (
        "new 11",
        "B1x",
        T[2],
    )
    assert (by_id[12]["name"], by_id[12]["price_rub"], by_id[12]["is_promoted"]) == (
        "new 12",
        190.0,
        1,
    )
    assert set(by_id[11]) >= {"id", "run_id", "query_id", *ObservationRow.__dataclass_fields__}


def test_run_log_is_newest_first_and_honours_limit(ro):
    rows = reader.run_log(ro)
    assert [r["id"] for r in rows] == [3, 2, 1]
    assert [r["status"] for r in rows] == ["partial", "partial", "partial"]
    assert [r["id"] for r in reader.run_log(ro, limit=2)] == [3, 2]
    assert "slot_utc" in rows[0]


def test_snapshot_is_ordered_by_position(ro):
    rows = reader.snapshot(ro, 2, 1)
    assert [(r["position"], r["nm_id"]) for r in rows] == [(1, 12), (2, 11), (3, 14)]
    assert reader.snapshot(ro, 2, 3) == []


def test_latest_ok_run_id_ignores_partial_and_blocked(ro):
    assert reader.latest_ok_run_id(ro, 1) == 2  # run 3 is partial for alpha
    assert reader.latest_ok_run_id(ro, 2) == 3
    assert reader.latest_ok_run_id(ro, 3) is None  # only blocked attempts
    assert reader.latest_ok_run_id(ro, 999) is None


def test_run_id_for_slot(ro):
    assert reader.run_id_for_slot(ro, T[1]) == 2
    assert reader.run_id_for_slot(ro, "2030-01-01T00:00:00Z") is None


def test_existing_summary_helpers_still_work(ro):
    assert reader.latest_run_id(ro) == 3
    summary = reader.run_summary(ro, 1)
    assert summary is not None
    assert (summary["status"], summary["n_ok"], summary["n_blocked"]) == ("partial", 2, 1)
    assert reader.run_summary(ro, 999) is None
