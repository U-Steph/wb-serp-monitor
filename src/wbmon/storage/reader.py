"""Read paths over a SQLite connection (STOR-04): every observation lookup is index-backed.

All functions take an open connection (``connect_ro`` in the UI, ``connect_rw`` in the collector),
return plain dicts built from ``cursor.description`` and bind every value as a parameter. The only
dynamic SQL is the ``IN (?, ?, ...)`` placeholder list, capped at ``MAX_IN_IDS``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

MAX_IN_IDS = 500

_RUN_SUMMARY_SQL = """
SELECT id AS run_id, status, trigger, query_source, started_at, finished_at,
       n_queries, n_ok, n_partial, n_blocked, n_error, n_items
FROM runs WHERE id = ?
"""


def _rows(con: sqlite3.Connection, sql: str, params: Sequence[object] = ()) -> list[dict]:
    """Run a SELECT and return its rows as dicts keyed by column name."""
    cursor = con.execute(sql, params)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def _in_clause(nm_ids: Sequence[int]) -> str:
    """Placeholder list for ``nm_id IN (...)``; the ids themselves stay bound parameters."""
    if len(nm_ids) > MAX_IN_IDS:
        raise ValueError(f"at most {MAX_IN_IDS} nm_ids per call, got {len(nm_ids)}")
    return ", ".join("?" for _ in nm_ids)


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


def list_queries(con: sqlite3.Connection) -> list[dict]:
    """All queries ever seen, in sheet order (removed queries keep their row)."""
    return _rows(
        con,
        "SELECT id, text, sheet_order, first_seen_at, last_seen_at FROM queries"
        " ORDER BY sheet_order, id",
    )


def nm_ids_for_query(con: sqlite3.Connection, query_id: int) -> list[dict]:
    """Every nm_id ever seen for a query with coverage stats and its latest name/brand."""
    return _rows(
        con,
        """
        SELECT o.nm_id AS nm_id,
               COUNT(*) AS n_seen,
               MIN(o.position) AS best_position,
               MAX(o.position) AS worst_position,
               MAX(o.collected_at) AS last_seen_at,
               (SELECT x.name FROM observations x
                 WHERE x.query_id = o.query_id AND x.nm_id = o.nm_id
                 ORDER BY x.collected_at DESC, x.id DESC LIMIT 1) AS name,
               (SELECT x.brand FROM observations x
                 WHERE x.query_id = o.query_id AND x.nm_id = o.nm_id
                 ORDER BY x.collected_at DESC, x.id DESC LIMIT 1) AS brand
        FROM observations o
        WHERE o.query_id = ?
        GROUP BY o.nm_id
        ORDER BY o.nm_id
        """,
        (query_id,),
    )


def positions_for(con: sqlite3.Connection, query_id: int, nm_ids: Sequence[int]) -> list[dict]:
    """Observed positions of the chosen nm_ids over time (a missing run means not observed)."""
    if not nm_ids:
        return []
    placeholders = _in_clause(nm_ids)
    return _rows(
        con,
        "SELECT run_id, collected_at, nm_id, position, price_rub, is_promoted"
        " FROM observations"
        f" WHERE query_id = ? AND nm_id IN ({placeholders})"
        " ORDER BY collected_at, nm_id",
        (query_id, *nm_ids),
    )


def latest_characteristics(
    con: sqlite3.Connection, query_id: int, nm_ids: Sequence[int]
) -> list[dict]:
    """The most recent full observation row of each requested nm_id for the query."""
    if not nm_ids:
        return []
    placeholders = _in_clause(nm_ids)
    return _rows(
        con,
        f"""
        SELECT o.* FROM observations o
        WHERE o.query_id = ? AND o.nm_id IN ({placeholders})
          AND o.id = (SELECT m.id FROM observations m
                       WHERE m.query_id = o.query_id AND m.nm_id = o.nm_id
                       ORDER BY m.collected_at DESC, m.id DESC LIMIT 1)
        ORDER BY o.nm_id
        """,
        (query_id, *nm_ids),
    )


def run_log(con: sqlite3.Connection, limit: int = 100) -> list[dict]:
    """The most recent runs, newest first."""
    return _rows(con, "SELECT * FROM runs ORDER BY started_at DESC, id DESC LIMIT ?", (limit,))


def snapshot(con: sqlite3.Connection, run_id: int, query_id: int) -> list[dict]:
    """The stored SERP of one query in one run, in position order."""
    return _rows(
        con,
        "SELECT * FROM observations WHERE run_id = ? AND query_id = ? ORDER BY position",
        (run_id, query_id),
    )


def latest_ok_run_id(con: sqlite3.Connection, query_id: int) -> int | None:
    """Id of the newest run in which the query finished ``ok`` (partial/blocked do not count)."""
    value = con.execute(
        "SELECT MAX(run_id) FROM run_queries WHERE query_id = ? AND status = 'ok'", (query_id,)
    ).fetchone()[0]
    return None if value is None else int(value)


def run_id_for_slot(con: sqlite3.Connection, slot_utc: str) -> int | None:
    """Id of the run that owns the given hourly slot, if any."""
    row = con.execute("SELECT id FROM runs WHERE slot_utc = ?", (slot_utc,)).fetchone()
    return None if row is None else int(row[0])
