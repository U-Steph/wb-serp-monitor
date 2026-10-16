"""Schema migrations driven by ``PRAGMA user_version``.

Migration 1 is the frozen schema contract: it already carries every non-backfillable field
(``subject_id``, ``root_id``, ad and price-audit columns, per-query coverage). History tables are
append-only, enforced by triggers; ``runs`` may only be updated while it is still ``running``.
Each migration is a tuple of single statements (triggers contain ``;`` inside ``BEGIN ... END``).
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path

from wbmon.storage.db import connect_rw

_V1_TABLES: tuple[str, ...] = (
    """
    CREATE TABLE queries (
      id INTEGER PRIMARY KEY,
      text TEXT NOT NULL UNIQUE,
      sheet_order INTEGER NOT NULL,
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE runs (
      id INTEGER PRIMARY KEY,
      slot_utc TEXT UNIQUE,
      trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduler','probe')),
      started_at TEXT NOT NULL,
      finished_at TEXT,
      status TEXT NOT NULL
        CHECK (status IN ('running','ok','partial','blocked','error','abandoned')),
      query_source TEXT NOT NULL CHECK (query_source IN ('sheet','bundled')),
      params_json TEXT NOT NULL CHECK (json_valid(params_json)),
      top_n INTEGER NOT NULL CHECK (top_n >= 1),
      n_queries INTEGER NOT NULL DEFAULT 0,
      n_ok INTEGER NOT NULL DEFAULT 0,
      n_partial INTEGER NOT NULL DEFAULT 0,
      n_blocked INTEGER NOT NULL DEFAULT 0,
      n_error INTEGER NOT NULL DEFAULT 0,
      n_items INTEGER NOT NULL DEFAULT 0,
      error TEXT
    )
    """,
    "CREATE INDEX ix_runs_started ON runs(started_at)",
    """
    CREATE TABLE run_queries (
      id INTEGER PRIMARY KEY,
      run_id INTEGER NOT NULL REFERENCES runs(id),
      query_id INTEGER NOT NULL REFERENCES queries(id),
      status TEXT NOT NULL CHECK (status IN ('ok','partial','blocked','rate_limited','error')),
      error_class TEXT,
      http_status INTEGER,
      pages_fetched INTEGER NOT NULL DEFAULT 0,
      items_collected INTEGER NOT NULL DEFAULT 0,
      is_complete INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0,1)),
      normquery TEXT,
      ad_slot_mismatch INTEGER NOT NULL DEFAULT 0,
      duplicates_dropped INTEGER NOT NULL DEFAULT 0,
      error_message TEXT,
      started_at TEXT NOT NULL,
      duration_ms INTEGER,
      UNIQUE (run_id, query_id),
      CHECK (status <> 'ok' OR items_collected > 0),
      CHECK (status IN ('ok','partial') OR items_collected = 0),
      CHECK (status <> 'ok' OR error_class IS NULL)
    )
    """,
    "CREATE INDEX ix_rq_query_run ON run_queries(query_id, run_id)",
    """
    CREATE TABLE observations (
      id INTEGER PRIMARY KEY,
      run_id INTEGER NOT NULL REFERENCES runs(id),
      query_id INTEGER NOT NULL REFERENCES queries(id),
      collected_at TEXT NOT NULL,
      position INTEGER NOT NULL CHECK (position >= 1),
      nm_id INTEGER NOT NULL,
      name TEXT,
      brand TEXT,
      subject_id INTEGER,
      root_id INTEGER,
      price_rub REAL,
      price_basic_rub REAL,
      price_total_rub REAL,
      logistics_rub REAL,
      rating REAL,
      feedbacks INTEGER,
      nm_rating REAL,
      nm_feedbacks INTEGER,
      time1_h INTEGER,
      time2_h INTEGER,
      delivery_hours INTEGER,
      delivery_days INTEGER,
      seller_name TEXT,
      seller_id INTEGER,
      is_promoted INTEGER NOT NULL DEFAULT 0 CHECK (is_promoted IN (0,1)),
      ad_promo_position INTEGER,
      ad_organic_position INTEGER,
      ad_tp TEXT,
      ad_cpm INTEGER,
      total_quantity INTEGER,
      UNIQUE (run_id, query_id, position)
    )
    """,
    "CREATE INDEX ix_obs_query_nm_time ON observations(query_id, nm_id, collected_at)",
    "CREATE INDEX ix_obs_query_time ON observations(query_id, collected_at)",
)


def _abort_trigger(name: str, timing: str, target: str, message: str, when: str = "") -> str:
    """Build a trigger that aborts the statement with ``message``."""
    clause = f" WHEN {when}" if when else ""
    return (
        f"CREATE TRIGGER {name} BEFORE {timing} ON {target}{clause} "
        f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
    )


_V1_TRIGGERS: tuple[str, ...] = (
    _abort_trigger(
        "observations_no_update", "UPDATE", "observations", "observations is append-only"
    ),
    _abort_trigger(
        "observations_no_delete", "DELETE", "observations", "observations is append-only"
    ),
    _abort_trigger(
        "run_queries_no_update", "UPDATE", "run_queries", "run_queries rows are written once"
    ),
    _abort_trigger("run_queries_no_delete", "DELETE", "run_queries", "run_queries is append-only"),
    _abort_trigger("runs_no_delete", "DELETE", "runs", "runs is append-only"),
    _abort_trigger("queries_no_delete", "DELETE", "queries", "queries are never deleted"),
    _abort_trigger(
        "runs_finished_immutable",
        "UPDATE",
        "runs",
        "finished runs are immutable",
        when="OLD.status <> 'running'",
    ),
    _abort_trigger(
        "queries_text_immutable",
        "UPDATE OF text",
        "queries",
        "query text is immutable",
        when="NEW.text IS NOT OLD.text",
    ),
)

MIGRATIONS: tuple[tuple[str, ...], ...] = ((*_V1_TABLES, *_V1_TRIGGERS),)
SCHEMA_VERSION = len(MIGRATIONS)


def user_version(con: sqlite3.Connection) -> int:
    """Return ``PRAGMA user_version``."""
    return int(con.execute("PRAGMA user_version").fetchone()[0])


def migrate(con: sqlite3.Connection) -> int:
    """Apply pending migrations idempotently and return the resulting schema version.

    The version is re-read under the write lock, so concurrent first-time initialisations are safe.
    A database newer than this code is refused.
    """
    if user_version(con) > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{user_version(con)} is newer than this code (v{SCHEMA_VERSION})"
        )
    while user_version(con) < SCHEMA_VERSION:
        con.execute("BEGIN IMMEDIATE")
        try:
            current = user_version(con)
            if current < SCHEMA_VERSION:
                for statement in MIGRATIONS[current]:
                    con.execute(statement)
                con.execute(f"PRAGMA user_version = {current + 1}")  # integer we format ourselves
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
    return user_version(con)


def init_db(path: str | Path) -> int:
    """Create the database file (and parent directory), migrate it, return the schema version."""
    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(connect_rw(db_path)) as con:
        return migrate(con)
