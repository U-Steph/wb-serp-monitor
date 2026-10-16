"""Read-back helpers over a SQLite connection."""

from __future__ import annotations

import sqlite3

_RUN_SUMMARY_SQL = """
SELECT id AS run_id, status, trigger, query_source, started_at, finished_at,
       n_queries, n_ok, n_partial, n_blocked, n_error, n_items
FROM runs WHERE id = ?
"""


def run_summary(con: sqlite3.Connection, run_id: int) -> dict[str, object] | None:
    """Return the run's headline counters, or ``None`` when the run does not exist."""
    cursor = con.execute(_RUN_SUMMARY_SQL, (run_id,))
    row = cursor.fetchone()
    if row is None:
        return None
    return {column[0]: value for column, value in zip(cursor.description, row, strict=True)}


def latest_run_id(con: sqlite3.Connection) -> int | None:
    """Return the highest run id, or ``None`` when there are no runs."""
    value = con.execute("SELECT MAX(id) FROM runs").fetchone()[0]
    return None if value is None else int(value)
