"""Writer atomicity, run lifecycle, crash recovery and the run lock (STOR-03, QRY-04)."""

from __future__ import annotations

import contextlib
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from wbmon.models import ObservationRow, QueryResult
from wbmon.storage.db import connect_rw
from wbmon.storage.lock import LockBusy, run_lock
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import ABANDONED_ERROR, Writer

T0 = "2026-10-01T00:00:00Z"
T1 = "2026-10-01T00:05:00Z"
T2 = "2026-10-01T00:10:00Z"


@pytest.fixture
def con(tmp_path: Path):
    path = tmp_path / "writer.sqlite3"
    init_db(path)
    with contextlib.closing(connect_rw(path)) as connection:
        yield connection


@pytest.fixture
def writer(con) -> Writer:
    return Writer(con)


def obs(position: int, nm_id: int | None = None, **overrides) -> ObservationRow:
    """An ObservationRow with only the required fields set (everything else unknown)."""
    values: dict = {name: None for name in ObservationRow.__dataclass_fields__}
    values.update(
        collected_at=T0,
        position=position,
        nm_id=nm_id if nm_id is not None else 900000000 + position,
        is_promoted=0,
    )
    values.update(overrides)
    return ObservationRow(**values)


def result(status: str = "ok", n: int = 3, **overrides) -> QueryResult:
    observations = tuple(obs(i) for i in range(1, n + 1)) if status in ("ok", "partial") else ()
    kwargs: dict = {
        "status": status,
        "started_at": T0,
        "duration_ms": 120,
        "observations": observations,
        "pages_fetched": 1,
        "is_complete": status == "ok",
        "http_status": 200 if status in ("ok", "partial") else 403,
    }
    if status not in ("ok", "partial"):
        kwargs["error_class"] = "http_403"
    kwargs.update(overrides)
    return QueryResult(**kwargs)


def new_run(writer: Writer, **overrides) -> int:
    kwargs: dict = {
        "trigger": "manual",
        "started_at": T0,
        "query_source": "bundled",
        "params_json": "{}",
        "top_n": 200,
        "n_queries": 3,
    }
    kwargs.update(overrides)
    return writer.start_run(**kwargs)


def count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------------------------
# upsert_queries (QRY-04)
# ---------------------------------------------------------------------------------------------


def test_upsert_queries_keeps_ids_order_and_history(con, writer):
    first = writer.upsert_queries(["alpha", "beta", "gamma"], T0)
    assert [first[t] for t in ("alpha", "beta", "gamma")] == sorted(first.values())
    orders = dict(con.execute("SELECT text, sheet_order FROM queries"))
    assert orders == {"alpha": 1, "beta": 2, "gamma": 3}

    run_id = new_run(writer)
    writer.record_query(run_id, first["beta"], result("ok", 2))

    second = writer.upsert_queries(["gamma", "alpha", "delta"], T1)  # beta removed, delta new
    assert second["gamma"] == first["gamma"]
    assert second["alpha"] == first["alpha"]
    assert second["delta"] not in first.values()
    rows = {
        text: (order, first_seen, last_seen)
        for text, order, first_seen, last_seen in con.execute(
            "SELECT text, sheet_order, first_seen_at, last_seen_at FROM queries"
        )
    }
    assert rows["gamma"] == (1, T0, T1)
    assert rows["alpha"] == (2, T0, T1)
    assert rows["delta"] == (3, T1, T1)
    assert rows["beta"] == (2, T0, T0)  # vanished from the list: row and timestamps untouched
    assert con.execute(
        "SELECT COUNT(*) FROM observations WHERE query_id = ?", (first["beta"],)
    ).fetchone() == (2,)


# ---------------------------------------------------------------------------------------------
# record_query
# ---------------------------------------------------------------------------------------------


def test_record_query_ok_stores_row_and_observations(con, writer):
    query_id = writer.upsert_queries(["alpha"], T0)["alpha"]
    run_id = new_run(writer)
    rows = tuple(
        obs(i, price_rub=100.5 + i, name=f"ТЕСТ {i}", is_promoted=i % 2) for i in (1, 2, 3)
    )
    status = writer.record_query(
        run_id, query_id, result("ok", 3, observations=rows, normquery="alpha")
    )
    assert status == "ok"
    assert con.execute(
        "SELECT status, items_collected, is_complete, pages_fetched, normquery, http_status"
        " FROM run_queries"
    ).fetchall() == [("ok", 3, 1, 1, "alpha", 200)]

    names = ["run_id", "query_id", *ObservationRow.__dataclass_fields__]
    stored = con.execute(f"SELECT {', '.join(names)} FROM observations ORDER BY position")
    expected = [tuple(row.as_params(run_id, query_id)[n] for n in names) for row in rows]
    assert stored.fetchall() == expected


def test_failed_write_rolls_back_and_leaves_error_row(con, writer):
    query_id = writer.upsert_queries(["alpha"], T0)["alpha"]
    run_id = new_run(writer)
    duplicate_positions = (obs(1), obs(2), obs(2, nm_id=900000777))
    bad = result("ok", observations=duplicate_positions, normquery="alpha")

    assert writer.record_query(run_id, query_id, bad) == "error"

    assert con.in_transaction is False
    assert count(con, "observations") == 0
    row = con.execute(
        "SELECT status, error_class, items_collected, is_complete, error_message, http_status,"
        " pages_fetched, started_at, duration_ms FROM run_queries"
    ).fetchall()
    assert len(row) == 1
    status, error_class, items, complete, message, http, pages, started, duration = row[0]
    assert (status, error_class, items, complete) == ("error", "db_write_failed", 0, 0)
    assert "UNIQUE" in message
    assert len(message) <= 200
    assert (http, pages, started, duration) == (200, 1, T0, 120)


def test_second_record_for_same_pair_raises_and_keeps_first(con, writer):
    query_id = writer.upsert_queries(["alpha"], T0)["alpha"]
    run_id = new_run(writer)
    writer.record_query(run_id, query_id, result("ok", 2))
    before = con.execute("SELECT * FROM run_queries").fetchall()

    with pytest.raises(sqlite3.IntegrityError):
        writer.record_query(run_id, query_id, result("ok", 4))

    assert con.in_transaction is False
    assert con.execute("SELECT * FROM run_queries").fetchall() == before
    assert count(con, "observations") == 2


def test_blocked_result_cannot_carry_observations(con, writer):
    query_id = writer.upsert_queries(["alpha"], T0)["alpha"]
    run_id = new_run(writer)
    bad = result("blocked", observations=(obs(1),))
    with pytest.raises(ValueError):
        writer.record_query(run_id, query_id, bad)
    assert count(con, "run_queries") == 0
    assert count(con, "observations") == 0


def test_blocked_result_is_stored_without_observations(con, writer):
    query_id = writer.upsert_queries(["alpha"], T0)["alpha"]
    run_id = new_run(writer)
    assert writer.record_query(run_id, query_id, result("blocked")) == "blocked"
    assert con.execute(
        "SELECT status, error_class, items_collected FROM run_queries"
    ).fetchall() == [("blocked", "http_403", 0)]


# ---------------------------------------------------------------------------------------------
# finish_run
# ---------------------------------------------------------------------------------------------

DERIVATION = [
    pytest.param(["ok", "ok", "ok"], "ok", id="all-ok"),
    pytest.param(["ok", "blocked", "ok"], "partial", id="ok-and-blocked"),
    pytest.param(["partial", "blocked", "error"], "partial", id="only-partial"),
    pytest.param(["blocked", "rate_limited", "blocked"], "blocked", id="only-blocked"),
    pytest.param(["error", "error", "error"], "error", id="all-error"),
    pytest.param(["blocked", "error", "error"], "blocked", id="blocked-beats-error"),
]


@pytest.mark.parametrize(("statuses", "expected"), DERIVATION)
def test_finish_run_derives_status(con, writer, statuses, expected):
    ids = writer.upsert_queries([f"q{i}" for i in range(len(statuses))], T0)
    run_id = new_run(writer, n_queries=len(statuses))
    for text, status in zip(ids, statuses, strict=True):
        extra = {"error_class": "timeout"} if status == "error" else {}
        writer.record_query(run_id, ids[text], result(status, 2, **extra))

    assert writer.finish_run(run_id, T1) == expected
    run = con.execute(
        "SELECT status, finished_at, n_ok, n_partial, n_blocked, n_error, n_items, error"
        " FROM runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    assert run[0] == expected
    assert run[1] == T1
    assert run[2:6] == (
        statuses.count("ok"),
        statuses.count("partial"),
        statuses.count("blocked") + statuses.count("rate_limited"),
        statuses.count("error"),
    )
    assert run[6] == 2 * (statuses.count("ok") + statuses.count("partial"))
    assert run[7] is None


def test_finish_run_without_queries_is_error(con, writer):
    run_id = new_run(writer)
    assert writer.finish_run(run_id, T1) == "error"


def test_finish_run_with_error_argument(con, writer):
    query_id = writer.upsert_queries(["alpha"], T0)["alpha"]
    run_id = new_run(writer)
    writer.record_query(run_id, query_id, result("ok", 2))
    assert writer.finish_run(run_id, T1, error="loop crashed") == "error"
    assert con.execute("SELECT status, error, n_items FROM runs").fetchall() == [
        ("error", "loop crashed", 2)
    ]


def test_finish_run_twice_raises(con, writer):
    run_id = new_run(writer)
    writer.finish_run(run_id, T1)
    with pytest.raises(RuntimeError, match="not running"):
        writer.finish_run(run_id, T2)
    assert con.in_transaction is False


def test_finish_run_unknown_run_raises(writer):
    with pytest.raises(RuntimeError, match="not running"):
        writer.finish_run(12345, T1)


# ---------------------------------------------------------------------------------------------
# abandon_stale_runs (T-01-19)
# ---------------------------------------------------------------------------------------------


def test_abandon_stale_runs(con, writer):
    ids = writer.upsert_queries(["alpha", "beta"], T0)
    crashed = new_run(writer)
    writer.record_query(crashed, ids["alpha"], result("ok", 3))
    untouched_start = new_run(writer, started_at=T1)
    finished = new_run(writer, started_at=T0, slot_utc="2026-10-01T00:00:00Z")
    writer.record_query(finished, ids["beta"], result("ok", 2))
    writer.finish_run(finished, T1)
    finished_before = con.execute("SELECT * FROM runs WHERE id = ?", (finished,)).fetchone()

    assert writer.abandon_stale_runs(T2) == 2

    rows = {
        r[0]: r[1:]
        for r in con.execute(
            "SELECT id, status, finished_at, error, n_ok, n_items FROM runs ORDER BY id"
        )
    }
    assert rows[crashed] == ("abandoned", T2, ABANDONED_ERROR, 1, 3)
    assert rows[untouched_start] == ("abandoned", T2, ABANDONED_ERROR, 0, 0)
    assert con.execute("SELECT * FROM runs WHERE id = ?", (finished,)).fetchone() == finished_before
    assert writer.abandon_stale_runs(T2) == 0
    assert con.in_transaction is False


# ---------------------------------------------------------------------------------------------
# run_lock (D-A7, T-01-18)
# ---------------------------------------------------------------------------------------------


def test_run_lock_is_exclusive_and_reusable(tmp_path):
    db_path = tmp_path / "nested dir" / "wbmon.sqlite3"
    with run_lock(db_path) as lock_path:
        assert lock_path == Path(str(db_path) + ".lock")
        assert lock_path.is_file()
        with pytest.raises(LockBusy), run_lock(db_path):
            pytest.fail("second holder must not get the lock")
    with run_lock(db_path):
        pass
    assert Path(str(db_path) + ".lock").exists()  # never deleted


def test_run_lock_released_when_body_raises(tmp_path):
    db_path = tmp_path / "wbmon.sqlite3"
    with pytest.raises(KeyError), run_lock(db_path):
        raise KeyError("boom")
    with run_lock(db_path):
        pass


_HOLDER = (
    "import sys\n"
    "from wbmon.storage.lock import run_lock\n"
    "with run_lock(sys.argv[1]):\n"
    "    print('locked', flush=True)\n"
    "    sys.stdin.readline()\n"
)


def test_lock_held_by_another_process_and_freed_by_sigkill(tmp_path):
    db_path = tmp_path / "wbmon.sqlite3"
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, str(db_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "locked"
        with pytest.raises(LockBusy), run_lock(db_path):
            pytest.fail("lock must be held by the child")
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)
        with run_lock(db_path):
            pass
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        for stream in (proc.stdin, proc.stdout):
            if stream is not None:
                stream.close()
