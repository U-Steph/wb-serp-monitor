"""Append-only writer: one ``BEGIN IMMEDIATE`` transaction per query, plain INSERTs only."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from wbmon.models import OBSERVATION_COLUMNS, QueryResult

# Column names come from the frozen ObservationRow dataclass, never from external text.
INSERT_OBSERVATION_SQL = (
    "INSERT INTO observations (run_id, query_id, "
    + ", ".join(OBSERVATION_COLUMNS)
    + ") VALUES (:run_id, :query_id, "
    + ", ".join(f":{name}" for name in OBSERVATION_COLUMNS)
    + ")"
)

_INSERT_RUN_QUERY_SQL = """
INSERT INTO run_queries (
  run_id, query_id, status, error_class, http_status, pages_fetched, items_collected,
  is_complete, normquery, ad_slot_mismatch, duplicates_dropped, error_message, started_at,
  duration_ms
) VALUES (
  :run_id, :query_id, :status, :error_class, :http_status, :pages_fetched, :items_collected,
  :is_complete, :normquery, :ad_slot_mismatch, :duplicates_dropped, :error_message, :started_at,
  :duration_ms
)
"""

_AGGREGATE_SQL = """
SELECT COUNT(*),
       COALESCE(SUM(status = 'ok'), 0),
       COALESCE(SUM(status = 'partial'), 0),
       COALESCE(SUM(status IN ('blocked', 'rate_limited')), 0),
       COALESCE(SUM(status = 'error'), 0),
       COALESCE(SUM(items_collected), 0)
FROM run_queries WHERE run_id = ?
"""


class Writer:
    """Writes runs, per-query results and observations through a ``connect_rw`` connection."""

    def __init__(self, con: sqlite3.Connection) -> None:
        self._con = con

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run the block inside BEGIN IMMEDIATE ... COMMIT, rolling back on any error."""
        self._con.execute("BEGIN IMMEDIATE")
        try:
            yield self._con
            self._con.execute("COMMIT")
        except BaseException:
            if self._con.in_transaction:
                self._con.execute("ROLLBACK")
            raise

    def upsert_queries(self, texts: Sequence[str], now: str) -> dict[str, int]:
        """Register queries with their sheet order (1-based) and return ``{text: id}``.

        A query that vanished from the sheet later simply keeps its row and history.
        """
        with self._transaction() as con:
            for order, text in enumerate(texts, start=1):
                con.execute(
                    "INSERT INTO queries(text, sheet_order, first_seen_at, last_seen_at)"
                    " VALUES (?, ?, ?, ?)"
                    " ON CONFLICT(text) DO UPDATE SET sheet_order = excluded.sheet_order,"
                    " last_seen_at = excluded.last_seen_at",
                    (text, order, now, now),
                )
        wanted = set(texts)
        return {
            text: query_id
            for query_id, text in self._con.execute("SELECT id, text FROM queries")
            if text in wanted
        }

    def start_run(
        self,
        *,
        trigger: str,
        started_at: str,
        query_source: str,
        params_json: str,
        top_n: int,
        n_queries: int,
        slot_utc: str | None = None,
    ) -> int:
        """Insert a ``running`` run and return its id."""
        cursor = self._con.execute(
            "INSERT INTO runs(slot_utc, trigger, started_at, status, query_source, params_json,"
            " top_n, n_queries) VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
            (slot_utc, trigger, started_at, query_source, params_json, top_n, n_queries),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def record_query(self, run_id: int, query_id: int, result: QueryResult) -> str:
        """Write the ``run_queries`` row and its observations in one transaction."""
        if result.observations and result.status not in ("ok", "partial"):
            raise ValueError(f"status {result.status!r} cannot carry observations")
        params = {
            "run_id": run_id,
            "query_id": query_id,
            "status": result.status,
            "error_class": result.error_class,
            "http_status": result.http_status,
            "pages_fetched": result.pages_fetched,
            "items_collected": result.items_collected,
            "is_complete": 1 if result.is_complete else 0,
            "normquery": result.normquery,
            "ad_slot_mismatch": result.ad_slot_mismatch,
            "duplicates_dropped": result.duplicates_dropped,
            "error_message": result.error_message,
            "started_at": result.started_at,
            "duration_ms": result.duration_ms,
        }
        with self._transaction() as con:
            con.execute(_INSERT_RUN_QUERY_SQL, params)
            if result.observations:
                con.executemany(
                    INSERT_OBSERVATION_SQL,
                    [row.as_params(run_id, query_id) for row in result.observations],
                )
        return result.status

    def finish_run(self, run_id: int, finished_at: str, *, error: str | None = None) -> str:
        """Aggregate the run's query results, derive its status and close it."""
        with self._transaction() as con:
            total, n_ok, n_partial, n_blocked, n_error, n_items = con.execute(
                _AGGREGATE_SQL, (run_id,)
            ).fetchone()
            if error is not None or total == 0:
                status = "error"
            elif n_ok == total:
                status = "ok"
            elif n_ok + n_partial > 0:
                status = "partial"
            elif n_blocked > 0:
                status = "blocked"
            else:
                status = "error"
            cursor = con.execute(
                "UPDATE runs SET status = ?, finished_at = ?, n_ok = ?, n_partial = ?,"
                " n_blocked = ?, n_error = ?, n_items = ?, error = ?"
                " WHERE id = ? AND status = 'running'",
                (status, finished_at, n_ok, n_partial, n_blocked, n_error, n_items, error, run_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"run {run_id} is not in the running state")
        return status
