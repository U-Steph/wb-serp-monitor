"""UI-12 proof on a generated week of history (slow: about 10 s, about 0.8 GB temporary disk).

70 queries x 168 hourly runs x 200 positions (every 7th run blocked, so about two million
observations) exist ONLY in a temporary database that is deleted afterwards (data-integrity
constraint: no synthetic rows in a real DB). Run with ``pytest -m slow tests/ui/test_perf.py``.
Every UI loader and both figure builders must answer in under a second.
"""

from __future__ import annotations

import contextlib
import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from ui.dbbuilder import assert_temporary
from wbmon.models import OBSERVATION_COLUMNS
from wbmon.storage.db import connect_ro, connect_rw
from wbmon.storage.migrations import init_db
from wbmon.ui import charts, data, series

pytestmark = pytest.mark.slow

N_QUERIES = 70
N_RUNS = 168
N_POSITIONS = 200
BLOCKED_EVERY = 7
N_BLOCKED_RUNS = N_RUNS // BLOCKED_EVERY
N_OK_RUNS = N_RUNS - N_BLOCKED_RUNS
EXPECTED_OBSERVATIONS = N_QUERIES * N_OK_RUNS * N_POSITIONS
LIMIT_SECONDS = 1.0
QUERY_ID = 35
LATEST_DATA_RUN = N_RUNS - 1  # run 168 is blocked (168 % 7 == 0), run 167 is the newest ok run
N_PICKED = 15

# One SQL expression per observations column; ``r`` is the run, ``q`` the query, ``pos.i`` 1..200.
_OBSERVATION_VALUES: dict[str, str] = {
    "collected_at": "r.started_at",
    "position": "pos.i",
    # each run reshuffles 450 ids, so products enter and leave the top-200 between runs
    "nm_id": "100000000 + q.id * 1000 + ((pos.i * 7 + r.id * 13) % 450)",
    "name": "'ТЕСТ комплект постельного белья, арт. ' || pos.i",
    "brand": "'ТЕСТ-БРЕНД ' || (pos.i % 37)",
    "subject_id": "NULL",
    "root_id": "NULL",
    "price_rub": "CASE WHEN pos.i % 17 = 0 THEN NULL ELSE 1000.5 + pos.i END",
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
    "seller_name": "'ТЕСТ-Продавец ' || (pos.i % 91)",
    "seller_id": "40000 + (pos.i % 91)",
    "is_promoted": "CASE WHEN pos.i % 11 = 0 THEN 1 ELSE 0 END",
    "ad_promo_position": "NULL",
    "ad_organic_position": "NULL",
    "ad_tp": "NULL",
    "ad_cpm": "NULL",
    "total_quantity": "500",
}
_HOUR = "strftime('%Y-%m-%dT%H:%M:%SZ', '2026-10-01 00:00:00', '+' || (i - 1) || ' hours')"


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
            "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < :runs)"
            " INSERT INTO runs(id, slot_utc, trigger, started_at, finished_at, status,"
            " query_source, params_json, top_n, n_queries, n_ok, n_blocked, n_items)"
            f" SELECT i, {_HOUR}, 'scheduler', {_HOUR},"
            " strftime('%Y-%m-%dT%H:%M:%SZ', '2026-10-01 00:20:00', '+' || (i - 1) || ' hours'),"
            " CASE WHEN i % :every = 0 THEN 'blocked' ELSE 'ok' END, 'sheet', '{}', :top,"
            " :nq, CASE WHEN i % :every = 0 THEN 0 ELSE :nq END,"
            " CASE WHEN i % :every = 0 THEN :nq ELSE 0 END,"
            " CASE WHEN i % :every = 0 THEN 0 ELSE :nq * :top END FROM n",
            {"runs": N_RUNS, "every": BLOCKED_EVERY, "top": N_POSITIONS, "nq": N_QUERIES},
        )
        con.execute(
            "INSERT INTO run_queries(run_id, query_id, status, error_class, http_status,"
            " pages_fetched, items_collected, is_complete, started_at)"
            " SELECT r.id, q.id,"
            " CASE WHEN r.status = 'blocked' THEN 'blocked' ELSE 'ok' END,"
            " CASE WHEN r.status = 'blocked' THEN 'http_403' END,"
            " CASE WHEN r.status = 'blocked' THEN 403 END,"
            " CASE WHEN r.status = 'blocked' THEN 1 ELSE 2 END,"
            " CASE WHEN r.status = 'blocked' THEN 0 ELSE ? END,"
            " CASE WHEN r.status = 'blocked' THEN 0 ELSE 1 END, r.started_at"
            " FROM runs r CROSS JOIN queries q",
            (N_POSITIONS,),
        )
        con.execute(
            "WITH RECURSIVE pos(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM pos WHERE i < ?)"
            f" INSERT INTO observations(run_id, query_id, {columns})"
            f" SELECT r.id, q.id, {expressions}"
            " FROM runs r CROSS JOIN queries q CROSS JOIN pos WHERE r.status = 'ok'",
            (N_POSITIONS,),
        )
        con.execute("COMMIT")
        con.execute("ANALYZE")
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")


@pytest.fixture(scope="module")
def perf_db(tmp_path_factory):
    directory = tmp_path_factory.mktemp("uiperf")
    path = directory / "uiperf.sqlite3"
    assert_temporary(path)
    started = time.perf_counter()
    try:
        _generate(path)
        seconds = time.perf_counter() - started
        print(f"\n[ui-perf] generated {EXPECTED_OBSERVATIONS} rows in {seconds:.0f} s")
        yield path
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        shutil.rmtree(directory, ignore_errors=True)
        assert not path.exists()
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()


@pytest.fixture(scope="module")
def options(perf_db):
    return data.load_product_options_raw(str(perf_db), QUERY_ID)


@pytest.fixture(scope="module")
def picked(options) -> tuple[int, ...]:
    return tuple(int(i) for i in options.sort_values("n_seen", ascending=False)["nm_id"][:N_PICKED])


def timed(name: str, call: Callable[[], object]) -> object:
    started = time.perf_counter()
    result = call()
    elapsed = time.perf_counter() - started
    print(f"\n[ui-perf] {name}: {elapsed * 1000:.1f} ms")
    assert elapsed < LIMIT_SECONDS, f"{name} took {elapsed:.3f} s"
    return result


def test_generated_database_has_the_expected_shape(perf_db):
    with contextlib.closing(connect_ro(perf_db)) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == (
            EXPECTED_OBSERVATIONS
        )
        assert con.execute("SELECT COUNT(*) FROM run_queries").fetchone()[0] == N_QUERIES * N_RUNS
        assert con.execute("SELECT COUNT(*) FROM runs WHERE status = 'blocked'").fetchone()[0] == (
            N_BLOCKED_RUNS
        )


def test_header_loaders_are_fast(perf_db):
    path = str(perf_db)
    assert timed("db_status", lambda: data.db_status(path)) == "ready"
    version = timed("data_version", lambda: data.data_version(path))
    assert version.latest_run_id == N_RUNS
    overview = timed("load_overview_raw", lambda: data.load_overview_raw(path))
    assert overview["n_runs"] == N_RUNS
    totals = timed("load_totals_raw", lambda: data.load_totals_raw(path))
    assert totals["n_obs"] == EXPECTED_OBSERVATIONS
    assert len(timed("load_queries_raw", lambda: data.load_queries_raw(path))) == N_QUERIES
    log = timed("load_run_log_raw", lambda: data.load_run_log_raw(path))
    assert len(log) == min(N_RUNS, data.RUN_LOG_LIMIT)
    detail = timed("load_latest_run_detail_raw", lambda: data.load_latest_run_detail_raw(path, 168))
    assert detail["blocked_http"] == 403


def test_chart_loaders_are_fast(perf_db, picked):
    path = str(perf_db)
    grid = timed("load_run_grid_raw", lambda: data.load_run_grid_raw(path, QUERY_ID, QUERY_ID, ""))
    assert len(grid) == N_RUNS
    options = timed("load_product_options_raw", lambda: data.load_product_options_raw(path, 35))
    assert len(options) > 0
    latest = timed("load_latest_data_run_raw", lambda: data.load_latest_data_run_raw(path, 35))
    assert latest == LATEST_DATA_RUN
    top = timed("load_top_ids_raw", lambda: data.load_top_ids_raw(path, LATEST_DATA_RUN, 35, 15))
    assert len(top) == 15
    obs = timed("load_observations_raw", lambda: data.load_observations_raw(path, 35, picked, ""))
    assert 0 < len(obs) <= N_PICKED * N_OK_RUNS


def test_table_loaders_are_fast(perf_db):
    path = str(perf_db)
    attempts = timed("load_query_attempts_raw", lambda: data.load_query_attempts_raw(path, 35))
    assert len(attempts) == data.ATTEMPTS_LIMIT
    runs = timed("load_snapshot_runs_raw", lambda: data.load_snapshot_runs_raw(path, 35))
    assert len(runs) == N_OK_RUNS
    snapshot = timed("load_snapshot_raw", lambda: data.load_snapshot_raw(path, 167, 35))
    assert len(snapshot) == N_POSITIONS


def test_status_matrix_loader_is_fast(perf_db):
    runs, matrix = timed(
        "load_status_matrix_raw", lambda: data.load_status_matrix_raw(str(perf_db))
    )  # type: ignore[misc]
    assert len(runs) == data.HEATMAP_RUNS
    assert len(matrix) == data.HEATMAP_RUNS * N_QUERIES


def test_position_chart_pipeline_is_fast(perf_db, picked):
    path = str(perf_db)
    grid = data.load_run_grid_raw(path, QUERY_ID, QUERY_ID, "")
    obs = data.load_observations_raw(path, QUERY_ID, picked, "")
    names = {int(i): f"ID {i}" for i in picked}

    def build():
        points = series.build_points(grid, obs, list(picked))
        bands = series.band_times(grid)
        return charts.position_figure(
            points, list(picked), bands, names, series.x_range(grid), N_POSITIONS
        )

    fig = timed("position pipeline", build)
    assert len(fig.data) == N_PICKED + 2  # type: ignore[attr-defined]
    assert len(fig.layout.shapes) == N_BLOCKED_RUNS  # type: ignore[attr-defined]


def test_status_heatmap_is_fast_for_48_runs_by_70_queries(perf_db):
    path = str(perf_db)
    runs, matrix = data.load_status_matrix_raw(path)
    queries = data.load_queries_raw(path)
    fig = timed("status_heatmap", lambda: charts.status_heatmap(runs, matrix, queries))
    z = fig.data[0].z  # type: ignore[attr-defined]
    assert len(z) == N_QUERIES and len(z[0]) == data.HEATMAP_RUNS
    # run 168 is blocked: the newest column is the full red field
    assert {row[-1] for row in z} == {3}


def test_observation_and_option_queries_use_an_index(perf_db, picked):
    plans: dict[str, list[str]] = {}
    with contextlib.closing(connect_ro(perf_db)) as con:
        obs_sql = data.OBSERVATIONS_SQL_TEMPLATE.format(placeholders=", ".join("?" * len(picked)))
        for name, sql, params in (
            ("observations", obs_sql, (QUERY_ID, *picked, "")),
            ("product_options", data.PRODUCT_OPTIONS_SQL, {"q": QUERY_ID}),
        ):
            plans[name] = [row[3] for row in con.execute("EXPLAIN QUERY PLAN " + sql, params)]
    for name, details in plans.items():
        assert details, name
        assert "SCAN observations" not in details, f"{name}: {details}"
        assert any("USING" in d and "INDEX" in d for d in details), f"{name}: {details}"
