"""STOR-04 performance proof on a generated week of history (slow: about 10 s, about 0.9 GB tmp).

70 queries x 168 hourly runs x 200 positions = 2,352,000 observations exist ONLY in a temporary
database that is deleted afterwards (data-integrity constraint: no synthetic rows in a real DB).
Run with ``pytest -m slow tests/test_storage_perf.py``.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from wbmon.models import OBSERVATION_COLUMNS
from wbmon.storage import reader
from wbmon.storage.db import connect_ro, connect_rw
from wbmon.storage.migrations import init_db

pytestmark = pytest.mark.slow

N_QUERIES = 70
N_RUNS = 168
N_POSITIONS = 200
EXPECTED_ROWS = N_QUERIES * N_RUNS * N_POSITIONS
LIMIT_SECONDS = 1.0
QUERY_ID = 35
RUN_ID = 100
SLOT = "2026-10-05T03:00:00Z"  # run 100 = start + 99 hours

# One SQL expression per observations column (the frozen ObservationRow column list).
_OBSERVATION_VALUES: dict[str, str] = {
    "collected_at": "r.started_at",
    "position": "pos.i",
    "nm_id": "100000000 + q.id * 1000 + ((pos.i * 7 + r.id * 13) % 450)",
    "name": "'Комплект постельного белья 2-спальный, поплин, 100% хлопок, арт. ' || pos.i",
    "brand": "'Бренд ' || (pos.i % 37)",
    "subject_id": "NULL",
    "root_id": "NULL",
    "price_rub": "1000.5 + pos.i",
    "price_basic_rub": "2000.0 + pos.i",
    "price_total_rub": "1000.5 + pos.i",
    "logistics_rub": "0.0",
    "rating": "4.7",
    "feedbacks": "1200 + pos.i",
    "nm_rating": "4.7",
    "nm_feedbacks": "300",
    "time1_h": "3",
    "time2_h": "26",
    "delivery_hours": "29",
    "delivery_days": "1",
    "seller_name": "'ООО Продавец ' || (pos.i % 91)",
    "seller_id": "40000 + (pos.i % 91)",
    "is_promoted": "CASE WHEN pos.i % 11 = 0 THEN 1 ELSE 0 END",
    "ad_promo_position": "NULL",
    "ad_organic_position": "NULL",
    "ad_tp": "NULL",
    "ad_cpm": "NULL",
    "total_quantity": "500",
}


def _generate(path: Path) -> None:
    assert set(_OBSERVATION_VALUES) == set(OBSERVATION_COLUMNS), "generator drifted from schema"
    columns = ", ".join(OBSERVATION_COLUMNS)
    expressions = ", ".join(_OBSERVATION_VALUES[name] for name in OBSERVATION_COLUMNS)
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        con.execute("PRAGMA synchronous = OFF")  # test-only; the production writer keeps NORMAL
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?)"
            " INSERT INTO queries(id, text, sheet_order, first_seen_at, last_seen_at)"
            " SELECT i, 'perf query ' || i, i, '2026-10-01T00:00:00Z', '2026-10-01T00:00:00Z'"
            " FROM n",
            (N_QUERIES,),
        )
        con.execute(
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < ?)"
            " INSERT INTO runs(id, slot_utc, trigger, started_at, finished_at, status,"
            " query_source, params_json, top_n, n_queries, n_ok, n_items)"
            " SELECT i, strftime('%Y-%m-%dT%H:%M:%SZ', '2026-10-01 00:00:00', '+' || (i - 1)"
            " || ' hours'), 'scheduler', strftime('%Y-%m-%dT%H:%M:%SZ', '2026-10-01 00:00:00',"
            " '+' || (i - 1) || ' hours'), strftime('%Y-%m-%dT%H:%M:%SZ', '2026-10-01 00:20:00',"
            " '+' || (i - 1) || ' hours'), 'ok', 'sheet', '{}', ?, ?, ?, ? FROM n",
            (N_RUNS, N_POSITIONS, N_QUERIES, N_QUERIES, N_QUERIES * N_POSITIONS),
        )
        con.execute(
            "INSERT INTO run_queries(run_id, query_id, status, pages_fetched, items_collected,"
            " is_complete, started_at)"
            " SELECT r.id, q.id, 'ok', 2, ?, 1, r.started_at FROM runs r CROSS JOIN queries q",
            (N_POSITIONS,),
        )
        con.execute(
            "WITH RECURSIVE pos(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM pos WHERE i < ?)"
            f" INSERT INTO observations(run_id, query_id, {columns})"
            f" SELECT r.id, q.id, {expressions}"
            " FROM runs r CROSS JOIN queries q CROSS JOIN pos",
            (N_POSITIONS,),
        )
        con.execute("COMMIT")
        con.execute("ANALYZE")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")


@pytest.fixture(scope="module")
def perf_db(tmp_path_factory):
    directory = tmp_path_factory.mktemp("perf")
    path = directory / "perf.sqlite3"
    started = time.perf_counter()
    try:
        _generate(path)
        print(f"\n[perf] generated {EXPECTED_ROWS} rows in {time.perf_counter() - started:.1f} s")
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        shutil.rmtree(directory, ignore_errors=True)
        assert not path.exists()


@pytest.fixture(scope="module")
def sample_ids(perf_db) -> list[int]:
    with contextlib.closing(connect_ro(perf_db)) as con:
        return [row["nm_id"] for row in reader.nm_ids_for_query(con, QUERY_ID)][:5]


def test_generated_database_has_the_expected_size(perf_db):
    with contextlib.closing(connect_ro(perf_db)) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == EXPECTED_ROWS
        assert con.execute("SELECT COUNT(*) FROM run_queries").fetchone()[0] == N_QUERIES * N_RUNS


CASES: dict[str, Callable] = {
    "list_queries": lambda con, ids: reader.list_queries(con),
    "nm_ids_for_query": lambda con, ids: reader.nm_ids_for_query(con, QUERY_ID),
    "positions_for": lambda con, ids: reader.positions_for(con, QUERY_ID, ids),
    "latest_characteristics": lambda con, ids: reader.latest_characteristics(con, QUERY_ID, ids),
    "run_log": lambda con, ids: reader.run_log(con),
    "snapshot": lambda con, ids: reader.snapshot(con, RUN_ID, QUERY_ID),
    "latest_ok_run_id": lambda con, ids: reader.latest_ok_run_id(con, QUERY_ID),
    "run_id_for_slot": lambda con, ids: reader.run_id_for_slot(con, SLOT),
}


def _check_result(name: str, result) -> None:
    expected_len = {
        "list_queries": N_QUERIES,
        "nm_ids_for_query": 450,
        "positions_for": 5 * N_RUNS,  # every id is present in some runs; at most 5 x 168
        "latest_characteristics": 5,
        "run_log": 100,
        "snapshot": N_POSITIONS,
    }
    if name == "latest_ok_run_id":
        assert result == N_RUNS
    elif name == "run_id_for_slot":
        assert result == RUN_ID
    elif name == "positions_for":
        assert 0 < len(result) <= expected_len[name]
    else:
        assert len(result) == expected_len[name]


@pytest.mark.parametrize("name", list(CASES))
def test_read_path_is_fast(perf_db, sample_ids, name):
    with contextlib.closing(connect_ro(perf_db)) as con:  # fresh connection per path
        started = time.perf_counter()
        result = CASES[name](con, sample_ids)
        elapsed = time.perf_counter() - started
    print(f"\n[perf] {name}: {elapsed * 1000:.1f} ms")
    _check_result(name, result)
    assert elapsed < LIMIT_SECONDS, f"{name} took {elapsed:.3f} s"


@pytest.mark.parametrize("name", [n for n in CASES if n != "list_queries"])
def test_read_path_uses_an_index(perf_db, sample_ids, name):
    statements: list[str] = []
    with contextlib.closing(connect_ro(perf_db)) as con:
        con.set_trace_callback(statements.append)
        CASES[name](con, sample_ids)
        con.set_trace_callback(None)
        selects = [s for s in statements if s.lstrip().upper().startswith(("SELECT", "WITH"))]
        assert selects, f"{name}: no SELECT traced"
        plans: list[str] = []
        for sql in selects:
            plans.extend(row[3] for row in con.execute("EXPLAIN QUERY PLAN " + sql))
    plan_text = "\n".join(plans)
    assert re.search(r"USING (COVERING )?INDEX", plan_text), f"{name}:\n{plan_text}"
    assert not re.search(r"SCAN (observations|o|run_queries)\b(?! USING)", plan_text), (
        f"{name} scans a big table:\n{plan_text}"
    )
