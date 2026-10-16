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

CACHE_TTL_S = 60
CACHE_MAX_ENTRIES = 256
RUN_LOG_LIMIT = 200
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
