"""Read-only data layer of the UI.

Every loader opens one short-lived connection through the Phase 1 ``connect_ro`` (``mode=ro`` URI
plus ``query_only``) and closes it before returning; connections are never cached. Cached twins
(``st.cache_data``) are keyed on ``(path, DataVersion)``: a new run, a new query attempt or a status
change moves the version, so fresh data shows up without a restart. SQL lives in module-level
constants that start with SELECT/WITH, values are always bound parameters.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import streamlit as st

from wbmon.storage.db import connect_ro
from wbmon.storage.reader import MAX_IN_IDS

CACHE_TTL_S = 60
CACHE_MAX_ENTRIES = 256
RUN_LOG_LIMIT = 200
ATTEMPTS_LIMIT = 10
SNAPSHOT_RUNS_LIMIT = 500
HEATMAP_RUNS = 48
HEARTBEAT_FILE = "scheduler_heartbeat.json"
HEARTBEAT_STALE_S = 600  # at least twice the scheduler's write interval (it ticks every 30 s)
READ_ERRORS = (sqlite3.Error, pd.errors.DatabaseError)

DATA_VERSION_SQL = """
SELECT (SELECT MAX(id) FROM runs) AS latest_run_id,
       (SELECT MAX(id) FROM run_queries) AS last_rq_id,
       (SELECT status FROM runs ORDER BY id DESC LIMIT 1) AS latest_status
"""
OVERVIEW_RUNS_SQL = """
SELECT COUNT(*),
       COALESCE(SUM(status IN ('ok', 'partial')), 0),
       MIN(started_at),
       MAX(started_at)
FROM runs
"""
LAST_OK_FINISHED_SQL = (
    "SELECT MAX(COALESCE(finished_at, started_at)) FROM runs WHERE status IN ('ok', 'partial')"
)
TOP_N_LATEST_SQL = "SELECT top_n FROM runs ORDER BY id DESC LIMIT 1"
N_QUERIES_SQL = "SELECT COUNT(*) FROM queries"
# The two observation counts stay separate statements: combined they force a full table scan.
N_OBS_SQL = "SELECT COUNT(*) FROM observations"
N_PRODUCTS_SQL = "SELECT COUNT(DISTINCT nm_id) FROM observations"
RUN_LOG_SQL = """
SELECT id AS run_id, trigger, started_at, finished_at, status, query_source, top_n, n_queries,
       n_ok, n_partial, n_blocked, n_error, n_items, error
FROM runs
ORDER BY started_at DESC, id DESC
LIMIT :limit
"""
# The NOT NULL filter keeps circuit-open rows (no code) from hiding the real HTTP status.
LATEST_RUN_DETAIL_SQL = """
SELECT http_status, COUNT(*) AS n
FROM run_queries
WHERE run_id = ? AND status IN ('blocked', 'rate_limited') AND http_status IS NOT NULL
GROUP BY http_status
ORDER BY n DESC
LIMIT 1
"""
_HAS_RUNS_SQL = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"

QUERIES_SQL = "SELECT id AS query_id, text, sheet_order FROM queries ORDER BY sheet_order, id"
# The last predicates are the honest-gap rule for runs that never looked at this query. A run in
# flight that has not reached it, or a limited manual run, adds neither a point nor a band. A run
# without an attempt row counts as "should have looked" (an abandoned run that planned the query,
# sheet_order <= n_queries) only INSIDE the span of run ids in which the query has attempt rows:
# sheet_order is not a stable identity (the writer rewrites it every run), so outside that span a
# missing row proves nothing (the query was not on the sheet yet, or has left it).
RUN_GRID_SQL = """
SELECT r.id AS run_id, r.started_at, r.status AS run_status, r.trigger,
       rq.status AS q_status, rq.error_class, rq.http_status, rq.items_collected,
       rq.is_complete, rq.pages_fetched
FROM runs r
LEFT JOIN run_queries rq ON rq.run_id = r.id AND rq.query_id = :q
WHERE r.started_at >= :since
  AND (
    rq.id IS NOT NULL
    OR (
      r.status <> 'running'
      AND :sheet_order <= r.n_queries
      AND r.id >= (SELECT MIN(run_id) FROM run_queries WHERE query_id = :q)
      AND r.id <= (SELECT MAX(run_id) FROM run_queries WHERE query_id = :q)
    )
  )
ORDER BY r.started_at, r.id
"""
PRODUCT_OPTIONS_SQL = """
WITH ranked AS (
  SELECT nm_id, run_id, position, is_promoted, name, brand, collected_at,
         ROW_NUMBER() OVER (PARTITION BY nm_id ORDER BY collected_at DESC, position ASC) AS rn,
         COUNT(*)      OVER (PARTITION BY nm_id) AS n_seen,
         MIN(position) OVER (PARTITION BY nm_id) AS best_position,
         MAX(position) OVER (PARTITION BY nm_id) AS worst_position
  FROM observations
  WHERE query_id = :q)
SELECT nm_id, n_seen, best_position, worst_position, collected_at AS last_at,
       run_id AS last_run_id, position AS last_position, is_promoted AS last_promoted, name, brand
FROM ranked
WHERE rn = 1
"""
LATEST_DATA_RUN_SQL = (
    "SELECT MAX(run_id) FROM run_queries WHERE query_id = ? AND items_collected > 0"
)
TOP_OF_RUN_SQL = """
SELECT nm_id FROM observations
WHERE run_id = :r AND query_id = :q
ORDER BY position
LIMIT :n
"""
# The only dynamic part is the list of "?" placeholders; ids themselves are bound parameters.
OBSERVATIONS_SQL_TEMPLATE = """
SELECT run_id, collected_at, nm_id, position, is_promoted, price_rub, rating, feedbacks,
       delivery_days, seller_name, name, brand
FROM observations
WHERE query_id = ? AND nm_id IN ({placeholders}) AND collected_at >= ?
ORDER BY collected_at, nm_id
"""
OBSERVATION_COLUMNS = (
    "run_id",
    "collected_at",
    "nm_id",
    "position",
    "is_promoted",
    "price_rub",
    "rating",
    "feedbacks",
    "delivery_days",
    "seller_name",
    "name",
    "brand",
)


QUERY_ATTEMPTS_SQL = """
SELECT r.started_at, rq.status, rq.http_status, rq.pages_fetched, rq.items_collected,
       rq.error_class, rq.duration_ms
FROM run_queries rq
JOIN runs r ON r.id = rq.run_id
WHERE rq.query_id = ?
ORDER BY rq.run_id DESC
LIMIT ?
"""
SNAPSHOT_RUNS_SQL = """
SELECT r.id AS run_id, r.started_at, rq.status, rq.items_collected
FROM run_queries rq
JOIN runs r ON r.id = rq.run_id
WHERE rq.query_id = ? AND rq.items_collected > 0
ORDER BY r.started_at DESC, r.id DESC
LIMIT ?
"""
SNAPSHOT_SQL = """
SELECT position, is_promoted, nm_id, name, brand, price_rub, rating, feedbacks, delivery_days,
       seller_name
FROM observations
WHERE run_id = ? AND query_id = ?
ORDER BY position
"""
# Running and abandoned runs have no complete per-query rows, so they never become heatmap columns.
# Both statements pick the same runs, newest first; the figure builder flips them to oldest first.
HEATMAP_RUNS_SQL = """
SELECT id AS run_id, started_at, status
FROM runs
WHERE status NOT IN ('running', 'abandoned')
ORDER BY started_at DESC, id DESC
LIMIT :n
"""
STATUS_MATRIX_SQL = """
SELECT rq.run_id, rq.query_id, rq.status, rq.items_collected, rq.is_complete, rq.http_status,
       rq.error_class
FROM run_queries rq
WHERE rq.run_id IN (
  SELECT id FROM runs
  WHERE status NOT IN ('running', 'abandoned')
  ORDER BY started_at DESC, id DESC
  LIMIT :n)
"""


class DataVersion(NamedTuple):
    """Cache key part: moves whenever the collector appends a run, an attempt or a status."""

    latest_run_id: int | None
    last_rq_id: int | None
    latest_status: str | None


def _df(con: sqlite3.Connection, sql: str, params: object = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, con, params=params, dtype_backend="numpy_nullable")  # type: ignore[arg-type]


def _scalar(con: sqlite3.Connection, sql: str) -> object:
    row = con.execute(sql).fetchone()
    return None if row is None else row[0]


def db_status(path: str) -> str:
    """Classify the file without ever creating it: missing, uninitialized, ready or error."""
    if not Path(path).is_file():
        return "missing"
    try:
        with contextlib.closing(connect_ro(path)) as con:
            if int(con.execute("PRAGMA user_version").fetchone()[0]) < 1:
                return "uninitialized"
            return "ready" if con.execute(_HAS_RUNS_SQL).fetchone() else "error"
    except READ_ERRORS:
        return "error"


def data_version(path: str) -> DataVersion:
    """Three index lookups; deliberately uncached so every rerun notices a new run."""
    with contextlib.closing(connect_ro(path)) as con:
        return DataVersion(*con.execute(DATA_VERSION_SQL).fetchone())


def read_heartbeat(db_path: str | Path) -> datetime | None:
    """The scheduler heartbeat timestamp (aware UTC), or None if absent or unusable."""
    file = Path(db_path).parent / HEARTBEAT_FILE
    try:
        stamp = json.loads(file.read_text(encoding="utf-8"))["ts"]
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def load_overview_raw(path: str) -> dict:
    """Totals for the sidebar and the freshness badge."""
    with contextlib.closing(connect_ro(path)) as con:
        n_runs, n_ok_runs, first_started, last_started = con.execute(OVERVIEW_RUNS_SQL).fetchone()
        return {
            "n_runs": int(n_runs),
            "n_ok_runs": int(n_ok_runs),
            "first_started": first_started,
            "last_started": last_started,
            "last_ok_finished": _scalar(con, LAST_OK_FINISHED_SQL),
            "n_obs": int(_scalar(con, N_OBS_SQL)),  # type: ignore[call-overload]
            "n_products": int(_scalar(con, N_PRODUCTS_SQL)),  # type: ignore[call-overload]
            "n_queries": int(_scalar(con, N_QUERIES_SQL)),  # type: ignore[call-overload]
            "top_n_latest": _scalar(con, TOP_N_LATEST_SQL),
        }


def load_run_log_raw(path: str, limit: int = RUN_LOG_LIMIT) -> pd.DataFrame:
    """The newest ``limit`` runs, newest first."""
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, RUN_LOG_SQL, {"limit": int(limit)})


def load_latest_run_detail_raw(path: str, run_id: int) -> dict:
    """Most frequent real HTTP status among the blocked attempts of one run."""
    with contextlib.closing(connect_ro(path)) as con:
        row = con.execute(LATEST_RUN_DETAIL_SQL, (int(run_id),)).fetchone()
    return {"blocked_http": None if row is None else int(row[0])}


def load_queries_raw(path: str) -> pd.DataFrame:
    """Every query in sheet order with its original wording."""
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, QUERIES_SQL)


def load_run_grid_raw(path: str, query_id: int, sheet_order: int, since: str) -> pd.DataFrame:
    """The x grid of one query: its runs from ``since`` (UTC ``Z``, empty = all time) onwards."""
    params = {"q": int(query_id), "since": since, "sheet_order": int(sheet_order)}
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, RUN_GRID_SQL, params)


def load_product_options_raw(path: str, query_id: int) -> pd.DataFrame:
    """One row per nm_id ever found for the query, with its latest observation."""
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, PRODUCT_OPTIONS_SQL, {"q": int(query_id)})


def load_latest_data_run_raw(path: str, query_id: int) -> int | None:
    """Newest run in which the query stored at least one item (ok or partial), else None."""
    with contextlib.closing(connect_ro(path)) as con:
        row = con.execute(LATEST_DATA_RUN_SQL, (int(query_id),)).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def load_top_ids_raw(path: str, run_id: int, query_id: int, n: int) -> tuple[int, ...]:
    """The first ``n`` nm_ids of one run of one query, in position order."""
    params = {"r": int(run_id), "q": int(query_id), "n": int(n)}
    with contextlib.closing(connect_ro(path)) as con:
        return tuple(int(row[0]) for row in con.execute(TOP_OF_RUN_SQL, params))


def load_observations_raw(
    path: str, query_id: int, nm_ids: tuple[int, ...], since: str
) -> pd.DataFrame:
    """Observations of the selected products from ``since`` onwards, oldest first."""
    ids = tuple(int(i) for i in nm_ids)
    if len(ids) > MAX_IN_IDS:
        raise ValueError(f"at most {MAX_IN_IDS} nm_ids per call, got {len(ids)}")
    if not ids:
        return pd.DataFrame({name: pd.Series(dtype="object") for name in OBSERVATION_COLUMNS})
    sql = OBSERVATIONS_SQL_TEMPLATE.format(placeholders=", ".join("?" * len(ids)))
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, sql, (int(query_id), *ids, since))


def load_query_attempts_raw(path: str, query_id: int, limit: int = ATTEMPTS_LIMIT) -> pd.DataFrame:
    """The newest ``limit`` attempts of one query (any status), newest first."""
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, QUERY_ATTEMPTS_SQL, (int(query_id), int(limit)))


def load_snapshot_runs_raw(path: str, query_id: int) -> pd.DataFrame:
    """Runs in which the query stored observations (ok or partial), newest first, at most 500."""
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, SNAPSHOT_RUNS_SQL, (int(query_id), SNAPSHOT_RUNS_LIMIT))


def load_snapshot_raw(path: str, run_id: int, query_id: int) -> pd.DataFrame:
    """The stored top-N of one query in one run, in position order."""
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, SNAPSHOT_SQL, (int(run_id), int(query_id)))


def load_status_matrix_raw(
    path: str, n_runs: int = HEATMAP_RUNS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The newest ``n_runs`` finished runs (newest first) and every query attempt inside them."""
    params = {"n": int(n_runs)}
    with contextlib.closing(connect_ro(path)) as con:
        return _df(con, HEATMAP_RUNS_SQL, params), _df(con, STATUS_MATRIX_SQL, params)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_overview(path: str, ver: DataVersion) -> dict:
    """Cached ``load_overview_raw``; ``ver`` is only part of the cache key."""
    del ver
    return load_overview_raw(path)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_run_log(path: str, ver: DataVersion, limit: int = RUN_LOG_LIMIT) -> pd.DataFrame:
    """Cached ``load_run_log_raw``."""
    del ver
    return load_run_log_raw(path, limit)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_latest_run_detail(path: str, ver: DataVersion, run_id: int) -> dict:
    """Cached ``load_latest_run_detail_raw``."""
    del ver
    return load_latest_run_detail_raw(path, run_id)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_queries(path: str, ver: DataVersion) -> pd.DataFrame:
    """Cached ``load_queries_raw``."""
    del ver
    return load_queries_raw(path)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_run_grid(
    path: str, ver: DataVersion, query_id: int, sheet_order: int, since: str
) -> pd.DataFrame:
    """Cached ``load_run_grid_raw``."""
    del ver
    return load_run_grid_raw(path, query_id, sheet_order, since)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_product_options(path: str, ver: DataVersion, query_id: int) -> pd.DataFrame:
    """Cached ``load_product_options_raw``."""
    del ver
    return load_product_options_raw(path, query_id)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_latest_data_run(path: str, ver: DataVersion, query_id: int) -> int | None:
    """Cached ``load_latest_data_run_raw``."""
    del ver
    return load_latest_data_run_raw(path, query_id)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_top_ids(
    path: str, ver: DataVersion, run_id: int, query_id: int, n: int
) -> tuple[int, ...]:
    """Cached ``load_top_ids_raw``."""
    del ver
    return load_top_ids_raw(path, run_id, query_id, n)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_observations(
    path: str, ver: DataVersion, query_id: int, nm_ids: tuple[int, ...], since: str
) -> pd.DataFrame:
    """Cached ``load_observations_raw``; callers pass ``nm_ids`` sorted so the key is stable."""
    del ver
    return load_observations_raw(path, query_id, nm_ids, since)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_query_attempts(
    path: str, ver: DataVersion, query_id: int, limit: int = ATTEMPTS_LIMIT
) -> pd.DataFrame:
    """Cached ``load_query_attempts_raw``."""
    del ver
    return load_query_attempts_raw(path, query_id, limit)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_snapshot_runs(path: str, ver: DataVersion, query_id: int) -> pd.DataFrame:
    """Cached ``load_snapshot_runs_raw``."""
    del ver
    return load_snapshot_runs_raw(path, query_id)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_snapshot(path: str, ver: DataVersion, run_id: int, query_id: int) -> pd.DataFrame:
    """Cached ``load_snapshot_raw``."""
    del ver
    return load_snapshot_raw(path, run_id, query_id)


@st.cache_data(ttl=CACHE_TTL_S, max_entries=CACHE_MAX_ENTRIES, show_spinner=False)
def load_status_matrix(
    path: str, ver: DataVersion, n_runs: int = HEATMAP_RUNS
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cached ``load_status_matrix_raw``."""
    del ver
    return load_status_matrix_raw(path, n_runs)
