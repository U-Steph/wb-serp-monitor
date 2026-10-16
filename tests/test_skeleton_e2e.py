"""Walking skeleton: init-db and a one-query collect replayed offline through MockTransport."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3

import httpx

from factories import FakeWB, RecordingSleep, json_response, make_page

ISO_Z = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
TRIGGERS = {
    "observations_no_update",
    "observations_no_delete",
    "run_queries_no_update",
    "run_queries_no_delete",
    "runs_no_delete",
    "queries_no_delete",
    "runs_finished_immutable",
    "queries_text_immutable",
}


def _db() -> contextlib.closing:
    return contextlib.closing(sqlite3.connect(os.environ["WBMON_DB_PATH"]))


def _collect(fake: FakeWB) -> int:
    from wbmon import cli

    return cli.main(
        ["collect", "--limit-queries", "1", "--top-n", "5"],
        _transport=httpx.MockTransport(fake),
        _sleep=RecordingSleep(),
    )


def _first_query() -> str:
    from wbmon.queries import load_bundled

    return load_bundled()[0]


def _page_one() -> httpx.Response:
    return json_response(make_page(900000001, 5, ads={2: 2}))


def test_init_db_creates_schema_and_is_idempotent():
    from wbmon import cli

    assert cli.main(["init-db"]) == 0
    with _db() as con:
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        objects = con.execute("SELECT type, name FROM sqlite_master").fetchall()
        tables = {name for kind, name in objects if kind == "table"}
        triggers = {name for kind, name in objects if kind == "trigger"}
    assert {"queries", "runs", "run_queries", "observations"} <= tables
    assert TRIGGERS <= triggers

    assert cli.main(["init-db"]) == 0
    with _db() as con:
        assert sorted(con.execute("SELECT type, name FROM sqlite_master").fetchall()) == sorted(
            objects
        )
        assert con.execute("PRAGMA user_version").fetchone()[0] == 1


def test_collect_one_query_end_to_end(capsys):
    query = _first_query()
    assert query == "постельное белье 2 спальное"
    fake = FakeWB({(query, 1): [_page_one()]})

    assert _collect(fake) == 0

    with _db() as con:
        run = con.execute(
            "SELECT trigger, query_source, status, n_queries, n_ok, n_items, finished_at,"
            " params_json FROM runs"
        ).fetchall()
        assert len(run) == 1
        trigger, source, status, n_queries, n_ok, n_items, finished_at, params_json = run[0]
        assert (trigger, source, status, n_queries, n_ok, n_items) == (
            "manual",
            "bundled",
            "ok",
            1,
            1,
            5,
        )
        assert finished_at is not None
        assert "-1257786" in json.dumps(json.loads(params_json), ensure_ascii=False)

        run_queries = con.execute(
            "SELECT status, http_status, pages_fetched, items_collected, is_complete, error_class"
            " FROM run_queries"
        ).fetchall()
        assert run_queries == [("ok", 200, 1, 5, 1, None)]

        rows = con.execute(
            "SELECT position, nm_id, is_promoted, collected_at FROM observations ORDER BY position"
        ).fetchall()
        assert [r[0] for r in rows] == [1, 2, 3, 4, 5]
        assert [r[1] for r in rows] == [900000001 + i for i in range(5)]
        assert [r[0] for r in rows if r[2] == 1] == [2]
        assert all(ISO_Z.match(r[3]) for r in rows)

        queries = con.execute("SELECT sheet_order FROM queries ORDER BY sheet_order").fetchall()
        assert [q[0] for q in queries] == list(range(1, 71))

    out = capsys.readouterr().out
    assert "event=run_summary" in out
    assert "status=ok" in out
    assert "items=5" in out

    requests = fake.wb_requests()
    assert len(requests) == 1
    params = requests[0].url.params
    assert params["query"] == query
    assert params["page"] == "1"
    assert params["dest"] == "-1257786"
    assert params["sort"] == "popular"
    assert params["resultset"] == "catalog"
    assert params["curr"] == "rub"
    assert requests[0].headers["user-agent"].startswith("Mozilla/5.0 (compatible; wbmon/")
    assert "cookie" not in requests[0].headers


def test_second_collect_appends_without_rewriting():
    query = _first_query()
    fake = FakeWB({(query, 1): [_page_one(), _page_one()]})
    select_first = "SELECT * FROM observations WHERE run_id = 1 ORDER BY position"

    assert _collect(fake) == 0
    with _db() as con:
        first_before = con.execute(select_first).fetchall()
    assert len(first_before) == 5

    assert _collect(fake) == 0
    with _db() as con:
        assert con.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 10
        assert con.execute(select_first).fetchall() == first_before
