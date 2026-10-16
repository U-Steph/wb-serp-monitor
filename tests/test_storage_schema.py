"""Schema guarantees proven through the connections the collector and the UI really use.

Covers STOR-01 (append-only history), STOR-02 (idempotent migrations, WAL, read-only path) and
QRY-04 (stable query ids, verbatim query text). Every database lives in ``tmp_path``.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys
import unicodedata
from pathlib import Path

import httpx
import pytest

from factories import FakeWB, RecordingSleep, json_response, make_page
from wbmon.storage import migrations
from wbmon.storage.db import connect_ro, connect_rw
from wbmon.storage.migrations import init_db, migrate, user_version
from wbmon.storage.writer import Writer

NOW = "2026-10-01T00:00:00Z"
RUN_COLUMNS = "(slot_utc, trigger, started_at, status, query_source, params_json, top_n)"


def _rw(path: Path) -> contextlib.closing:
    return contextlib.closing(connect_rw(path))


def _insert_run(
    con: sqlite3.Connection,
    *,
    slot_utc: str | None = None,
    trigger: str = "manual",
    status: str = "running",
    query_source: str = "bundled",
    params_json: str = "{}",
    top_n: int = 200,
) -> int:
    cursor = con.execute(
        f"INSERT INTO runs {RUN_COLUMNS} VALUES (?, ?, ?, ?, ?, ?, ?)",
        (slot_utc, trigger, NOW, status, query_source, params_json, top_n),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _insert_query(con: sqlite3.Connection, text: str, order: int) -> int:
    cursor = con.execute(
        "INSERT INTO queries(text, sheet_order, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?)",
        (text, order, NOW, NOW),
    )
    assert cursor.lastrowid is not None
    return cursor.lastrowid


def _insert_run_query(con, run_id, query_id, status="ok", items=1) -> None:
    con.execute(
        "INSERT INTO run_queries(run_id, query_id, status, items_collected, started_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (run_id, query_id, status, items, NOW),
    )


def _insert_observation(con, run_id, query_id, position, nm_id=900000001) -> None:
    con.execute(
        "INSERT INTO observations(run_id, query_id, collected_at, position, nm_id)"
        " VALUES (?, ?, ?, ?, ?)",
        (run_id, query_id, NOW, position, nm_id),
    )


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    """A migrated DB: run 1 (running) with query 1 (ok, 1 item, position 1), run 2 finished 'ok'.

    Query 2 has no run_queries row, so tests can insert new rows without tripping UNIQUE.
    """
    path = tmp_path / "schema.sqlite3"
    init_db(path)
    with _rw(path) as con:
        run_id = _insert_run(con)
        finished = _insert_run(con, status="ok", slot_utc="2026-10-01T01:00:00Z")
        assert (run_id, finished) == (1, 2)
        assert _insert_query(con, "query one", 1) == 1
        assert _insert_query(con, "query two", 2) == 2
        _insert_run_query(con, 1, 1)
        _insert_observation(con, 1, 1, 1)
    return path


def _dump(con: sqlite3.Connection) -> dict[str, list[tuple]]:
    return {
        table: con.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
        for table in ("queries", "runs", "run_queries", "observations")
    }


# ---------------------------------------------------------------------------------------------
# connect_rw recipe
# ---------------------------------------------------------------------------------------------


def test_connect_rw_pragmas(tmp_path):
    with _rw(tmp_path / "p.sqlite3") as con:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert con.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert con.execute("PRAGMA recursive_triggers").fetchone()[0] == 1
        assert con.in_transaction is False
        if sys.version_info >= (3, 12):
            assert con.autocommit is True


def test_explicit_begin_immediate_commit_persists_without_con_commit(tmp_path):
    path = tmp_path / "tx.sqlite3"
    init_db(path)
    with _rw(path) as con:
        con.execute("BEGIN IMMEDIATE")
        _insert_query(con, "persisted", 1)
        assert con.in_transaction is True
        con.execute("COMMIT")
        assert con.in_transaction is False
    with _rw(path) as other:
        assert other.execute("SELECT text FROM queries").fetchall() == [("persisted",)]


def test_rollback_discards_the_transaction(tmp_path):
    path = tmp_path / "rb.sqlite3"
    init_db(path)
    with _rw(path) as con:
        con.execute("BEGIN IMMEDIATE")
        _insert_query(con, "gone", 1)
        con.execute("ROLLBACK")
        assert con.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 0


# ---------------------------------------------------------------------------------------------
# Append-only matrix (STOR-01, T-01-16)
# ---------------------------------------------------------------------------------------------

APPEND_ONLY_REJECTED = [
    pytest.param("UPDATE observations SET nm_id = 1", (), "append-only", id="update-observation"),
    pytest.param("DELETE FROM observations", (), "append-only", id="delete-observation"),
    pytest.param(
        "INSERT INTO observations(run_id, query_id, collected_at, position, nm_id)"
        " VALUES (1, 1, 't', 1, 7)"
        " ON CONFLICT(run_id, query_id, position) DO UPDATE SET nm_id = excluded.nm_id",
        (),
        "append-only",
        id="observation-on-conflict-do-update",
    ),
    pytest.param(
        "INSERT OR REPLACE INTO observations(run_id, query_id, collected_at, position, nm_id)"
        " VALUES (1, 1, 't', 1, 7)",
        (),
        "append-only",
        id="observation-insert-or-replace",
    ),
    pytest.param("DELETE FROM runs", (), "append-only", id="delete-run"),
    pytest.param("DELETE FROM run_queries", (), "append-only", id="delete-run-query"),
    pytest.param("DELETE FROM queries", (), "never deleted", id="delete-query"),
    pytest.param(
        "UPDATE run_queries SET status = 'error', items_collected = 0",
        (),
        "written once",
        id="update-run-query",
    ),
    pytest.param(
        "UPDATE runs SET error = 'rewritten' WHERE id = 2",
        (),
        "finished runs are immutable",
        id="update-finished-run",
    ),
    pytest.param(
        "UPDATE runs SET status = 'running' WHERE id = 2",
        (),
        "finished runs are immutable",
        id="reopen-finished-run",
    ),
    pytest.param(
        "UPDATE queries SET text = 'renamed' WHERE id = 1",
        (),
        "query text is immutable",
        id="rename-query",
    ),
]


@pytest.mark.parametrize(("sql", "params", "message"), APPEND_ONLY_REJECTED)
def test_database_rejects_history_rewrites(seeded, sql, params, message):
    with _rw(seeded) as con:
        before = _dump(con)
        with pytest.raises(sqlite3.IntegrityError, match=message):
            con.execute(sql, params)
        assert con.in_transaction is False
        assert _dump(con) == before


def test_sheet_order_may_still_be_updated(seeded):
    with _rw(seeded) as con:
        con.execute("UPDATE queries SET sheet_order = 9 WHERE id = 1")
        assert con.execute("SELECT sheet_order FROM queries WHERE id = 1").fetchone() == (9,)


def test_running_run_may_be_finished_once(seeded):
    with _rw(seeded) as con:
        con.execute("UPDATE runs SET status = 'ok', finished_at = ? WHERE id = 1", (NOW,))
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE runs SET status = 'error' WHERE id = 1")


def test_replace_hazard_without_recursive_triggers_is_documented(seeded):
    """D-A2 control: a bare connection lets INSERT OR REPLACE bypass the DELETE trigger."""
    con = sqlite3.connect(str(seeded), isolation_level=None)
    try:
        assert con.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        con.execute(
            "INSERT OR REPLACE INTO observations(run_id, query_id, collected_at, position, nm_id)"
            " VALUES (1, 1, 't', 1, 7)"
        )
        assert con.execute("SELECT nm_id FROM observations").fetchall() == [(7,)]
    finally:
        con.close()


# ---------------------------------------------------------------------------------------------
# CHECK / FK / UNIQUE
# ---------------------------------------------------------------------------------------------

_OBS = (
    "INSERT INTO observations(run_id, query_id, collected_at, position, nm_id{extra})"
    " VALUES (?, ?, 't', ?, 5{values})"
)
_RQ = (
    "INSERT INTO run_queries(run_id, query_id, status, items_collected, started_at{extra})"
    " VALUES (?, ?, ?, ?, 't'{values})"
)
_RUN = f"INSERT INTO runs {RUN_COLUMNS} VALUES (NULL, ?, 't', ?, ?, ?, ?)"

CONSTRAINT_VIOLATIONS = [
    pytest.param(_OBS.format(extra="", values=""), (1, 1, 0), "CHECK", id="observation-position-0"),
    pytest.param(
        _OBS.format(extra=", is_promoted", values=", 2"), (1, 1, 5), "CHECK", id="is-promoted-2"
    ),
    pytest.param(
        _OBS.format(extra="", values=""), (999, 1, 5), "FOREIGN KEY", id="obs-unknown-run"
    ),
    pytest.param(
        _OBS.format(extra="", values=""), (1, 999, 5), "FOREIGN KEY", id="obs-unknown-query"
    ),
    pytest.param(_OBS.format(extra="", values=""), (1, 1, 1), "UNIQUE", id="duplicate-position"),
    pytest.param(_RQ.format(extra="", values=""), (1, 2, "ok", 0), "CHECK", id="ok-with-0-items"),
    pytest.param(
        _RQ.format(extra="", values=""), (1, 2, "blocked", 5), "CHECK", id="blocked-with-items"
    ),
    pytest.param(
        _RQ.format(extra=", error_class", values=", 'http_403'"),
        (1, 2, "ok", 1),
        "CHECK",
        id="ok-with-error-class",
    ),
    pytest.param(_RQ.format(extra="", values=""), (1, 2, "weird", 0), "CHECK", id="unknown-status"),
    pytest.param(_RQ.format(extra="", values=""), (1, 1, "ok", 1), "UNIQUE", id="duplicate-rq"),
    pytest.param(_RQ.format(extra="", values=""), (999, 2, "error", 0), "FOREIGN", id="rq-bad-run"),
    pytest.param(_RUN, ("cron", "running", "bundled", "{}", 200), "CHECK", id="trigger-cron"),
    pytest.param(_RUN, ("manual", "done", "bundled", "{}", 200), "CHECK", id="status-done"),
    pytest.param(_RUN, ("manual", "running", "web", "{}", 200), "CHECK", id="source-web"),
    pytest.param(
        _RUN, ("manual", "running", "bundled", "not json", 200), "CHECK", id="params-not-json"
    ),
    pytest.param(_RUN, ("manual", "running", "bundled", "{}", 0), "CHECK", id="top-n-0"),
    pytest.param(
        "INSERT INTO runs " + RUN_COLUMNS + " VALUES ('2026-10-01T01:00:00Z', ?, 't', ?, ?, ?, ?)",
        ("manual", "running", "bundled", "{}", 200),
        "UNIQUE",
        id="duplicate-slot",
    ),
]


@pytest.mark.parametrize(("sql", "params", "message"), CONSTRAINT_VIOLATIONS)
def test_constraint_violations(seeded, sql, params, message):
    with _rw(seeded) as con:
        before = _dump(con)
        with pytest.raises(sqlite3.IntegrityError, match=message):
            con.execute(sql, params)
        assert _dump(con) == before


def test_many_runs_without_slot_are_allowed(seeded):
    with _rw(seeded) as con:
        for _ in range(5):
            _insert_run(con, slot_utc=None, trigger="probe")
        assert con.execute("SELECT COUNT(*) FROM runs WHERE slot_utc IS NULL").fetchone()[0] == 6


# ---------------------------------------------------------------------------------------------
# Query text: exact, case/script/normalisation sensitive, never interpreted as SQL
# ---------------------------------------------------------------------------------------------


def test_lookalike_query_texts_stay_distinct(tmp_path):
    texts = [
        "комплект 160х200",  # Cyrillic "х"
        "комплект 160x200",  # Latin "x"
        "постельное бельё",
        "постельное белье",
        unicodedata.normalize("NFC", "бельё"),
        unicodedata.normalize("NFD", "бельё"),
    ]
    assert len(set(texts)) == len(texts)
    path = tmp_path / "text.sqlite3"
    init_db(path)
    with _rw(path) as con:
        ids = Writer(con).upsert_queries(texts, NOW)
        assert len(set(ids.values())) == len(texts)
        stored = [row[0] for row in con.execute("SELECT text FROM queries ORDER BY id")]
    assert stored == texts


def test_query_text_is_bound_never_executed(tmp_path):
    evil = "'; DROP TABLE observations;--"
    path = tmp_path / "inject.sqlite3"
    init_db(path)
    with _rw(path) as con:
        Writer(con).upsert_queries([evil], NOW)
        assert con.execute("SELECT text FROM queries").fetchall() == [(evil,)]
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone() == (0,)


# ---------------------------------------------------------------------------------------------
# Migrations (STOR-02)
# ---------------------------------------------------------------------------------------------


def test_migrate_twice_is_a_noop(tmp_path):
    path = tmp_path / "m.sqlite3"
    assert init_db(path) == migrations.SCHEMA_VERSION
    with _rw(path) as con:
        before = sorted(con.execute("SELECT type, name, sql FROM sqlite_master").fetchall())
        assert migrate(con) == migrations.SCHEMA_VERSION
        assert sorted(con.execute("SELECT type, name, sql FROM sqlite_master")) == before


def test_database_newer_than_code_is_refused(tmp_path):
    path = tmp_path / "newer.sqlite3"
    init_db(path)
    with _rw(path) as con:
        con.execute("PRAGMA user_version = 99")
        with pytest.raises(RuntimeError, match="newer"):
            migrate(con)


def test_failed_migration_rolls_back_completely(tmp_path, monkeypatch):
    broken = (("CREATE TABLE t_first (a INTEGER)", "THIS IS NOT SQL"),)
    monkeypatch.setattr(migrations, "MIGRATIONS", broken)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 1)
    with _rw(tmp_path / "broken.sqlite3") as con:
        with pytest.raises(sqlite3.OperationalError):
            migrate(con)
        assert con.in_transaction is False
        assert user_version(con) == 0
        assert con.execute("SELECT name FROM sqlite_master WHERE name = 't_first'").fetchall() == []


def test_init_db_creates_nested_parent_directories(tmp_path):
    path = tmp_path / "a" / "b c" / "wbmon.sqlite3"
    assert init_db(path) == migrations.SCHEMA_VERSION
    assert path.is_file()


def test_six_processes_initialise_the_same_fresh_file(tmp_path):
    path = tmp_path / "race" / "race.sqlite3"
    code = "import sys; from wbmon.storage.migrations import init_db; print(init_db(sys.argv[1]))"
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    results = [(p.communicate(timeout=120), p.returncode) for p in procs]
    for (out, err), returncode in results:
        assert returncode == 0, err
        assert out.strip() == "1"
    with _rw(path) as con:
        assert user_version(con) == 1


# ---------------------------------------------------------------------------------------------
# Read-only path (T-01-21)
# ---------------------------------------------------------------------------------------------


def test_connect_ro_on_awkward_path_reads_and_rejects_writes(tmp_path):
    folder = tmp_path / "Тест папка #?."
    path = folder / "wbmon.sqlite3"
    init_db(path)
    with _rw(path) as con:
        _insert_query(con, "readable", 1)
    with contextlib.closing(connect_ro(path)) as ro:
        assert ro.execute("SELECT text FROM queries").fetchall() == [("readable",)]
        assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.execute(
                "INSERT INTO queries(text, sheet_order, first_seen_at, last_seen_at)"
                " VALUES ('x', 2, 't', 't')"
            )


def test_connect_ro_does_not_create_a_missing_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        connect_ro(tmp_path / "missing.sqlite3")
    assert not (tmp_path / "missing.sqlite3").exists()


# ---------------------------------------------------------------------------------------------
# The real collect path
# ---------------------------------------------------------------------------------------------


def test_rows_written_by_collect_are_immutable():
    from wbmon import cli
    from wbmon.queries import load_bundled

    query = load_bundled()[0]
    fake = FakeWB({(query, 1): [json_response(make_page(900000001, 5, ads={2: 2}))]})
    assert (
        cli.main(
            ["collect", "--limit-queries", "1", "--top-n", "5"],
            _transport=httpx.MockTransport(fake),
            _sleep=RecordingSleep(),
        )
        == 0
    )
    with _rw(Path(os.environ["WBMON_DB_PATH"])) as con:
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 5
        for sql in (
            "UPDATE observations SET position = position + 100",
            "UPDATE observations SET nm_id = 1 WHERE position = 1",
            "DELETE FROM observations WHERE position = 5",
            "UPDATE runs SET error = 'tampered'",
        ):
            with pytest.raises(sqlite3.IntegrityError):
                con.execute(sql)
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 5


# ------------------------------------------------------------- IN-06: ensure_wal diagnostics


class _StubCursor:
    def __init__(self, value):
        self._value = value

    def fetchone(self):
        return (self._value,)


class _LockedConnection:
    """Every journal_mode statement fails the way a locked database does."""

    def execute(self, sql):
        raise sqlite3.OperationalError("database is locked")


class _NoWalConnection:
    """A filesystem that cannot do WAL: the pragma answers, but never with 'wal'."""

    def execute(self, sql):
        return _StubCursor("delete")


def test_ensure_wal_chains_the_underlying_error(monkeypatch):
    from wbmon.storage import db

    sleeps: list[float] = []
    monkeypatch.setattr(db.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match="after 3 attempts") as caught:
        db.ensure_wal(_LockedConnection(), attempts=3, pause=0.1)  # type: ignore[arg-type]

    assert isinstance(caught.value.__cause__, sqlite3.OperationalError)
    assert "database is locked" in str(caught.value.__cause__)
    assert sleeps == [0.1] * 3


def test_ensure_wal_pauses_between_non_wal_answers_and_reports_the_mode(monkeypatch):
    from wbmon.storage import db

    sleeps: list[float] = []
    monkeypatch.setattr(db.time, "sleep", sleeps.append)

    with pytest.raises(RuntimeError, match=r"journal_mode=delete"):
        db.ensure_wal(_NoWalConnection(), attempts=4, pause=0.25)  # type: ignore[arg-type]

    assert sleeps == [0.25] * 4
