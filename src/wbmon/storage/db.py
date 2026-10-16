"""SQLite connection recipes (Python 3.11 to 3.14 safe)."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path


def connect_rw(path: str | Path) -> sqlite3.Connection:
    """Open a read-write connection configured for the single collector writer.

    Transactions are always explicit SQL (``BEGIN IMMEDIATE`` / ``COMMIT`` / ``ROLLBACK``): 3.12+
    gets ``autocommit=True`` (its future default would break a manual BEGIN), 3.11 gets
    ``isolation_level=None``. ``recursive_triggers`` makes a REPLACE conflict fire the
    append-only DELETE trigger.
    """
    if sys.version_info >= (3, 12):
        con = sqlite3.connect(str(path), timeout=30, autocommit=True)
    else:
        con = sqlite3.connect(str(path), timeout=30, isolation_level=None)
    try:
        con.execute("PRAGMA busy_timeout = 30000")
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA recursive_triggers = ON")
        ensure_wal(con)
    except BaseException:
        con.close()
        raise
    return con


def ensure_wal(con: sqlite3.Connection, attempts: int = 20, pause: float = 0.25) -> None:
    """Switch the database to WAL, retrying when a concurrent first-time init holds the lock."""
    for _ in range(attempts):
        if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal":
            return
        try:
            if str(con.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower() == "wal":
                return
        except sqlite3.OperationalError:
            time.sleep(pause)
    raise RuntimeError("could not switch database to WAL")


def connect_ro(path: str | Path) -> sqlite3.Connection:
    """Open a read-only connection (URI built from the resolved path, never an f-string)."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA query_only = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    return con
