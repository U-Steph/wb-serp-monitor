"""Graceful stop in ``run_once``: ``terminated`` attempts, precedence, and SIGKILL -> abandoned."""

from __future__ import annotations

import contextlib
import os
import sqlite3

import httpx

from factories import (
    FakeWB,
    RecordingSleep,
    html_response,
    json_response,
    make_page,
    make_settings,
)

SLOT_0900 = "2026-10-16T09:00:00Z"
SLOT_1000 = "2026-10-16T10:00:00Z"


def bundled() -> list[str]:
    from wbmon.queries import load_bundled

    return list(load_bundled())


def ok_page(first_id: int = 900000001, n: int = 5) -> httpx.Response:
    return json_response(make_page(first_id, n))


def run(fake: FakeWB, **kwargs):
    from wbmon.log import configure_logging
    from wbmon.orchestrator import run_once

    configure_logging("INFO")  # bind the handler to the stdout capsys is capturing right now
    return run_once(
        make_settings(top_n=5),
        transport=httpx.MockTransport(fake),
        sleep=RecordingSleep(),
        **kwargs,
    )


def rows(sql: str, *params):
    path = os.environ["WBMON_DB_PATH"]
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params)]


def run_rows() -> list[dict]:
    if not os.path.exists(os.environ["WBMON_DB_PATH"]):
        return []
    return rows("SELECT * FROM runs ORDER BY id")


def query_rows(run_id: int) -> list[dict]:
    return rows(
        "SELECT rq.*, q.text FROM run_queries rq JOIN queries q ON q.id = rq.query_id"
        " WHERE rq.run_id = ? ORDER BY q.sheet_order",
        run_id,
    )


def events(out: str, name: str) -> list[str]:
    return [line for line in out.splitlines() if f" event={name} " in line + " "]


# ------------------------------------------------------------------- stop before the run exists


def test_stop_requested_up_front_creates_no_run_and_sends_nothing(capsys):
    fake = FakeWB()

    assert run(fake, should_stop=lambda: True) is None

    assert run_rows() == []
    assert fake.requests == []
    out = capsys.readouterr().out
    (line,) = events(out, "terminated")
    assert "phase=before_start" in line
    assert events(out, "run_start") == []


def test_stop_flipping_during_the_query_list_fetch_creates_no_run(capsys):
    fake = FakeWB()  # the sheet answers 404 -> bundled fallback, but the fetch is still a request

    assert run(fake, should_stop=lambda: len(fake.sheet_requests()) >= 1) is None

    assert run_rows() == []
    assert len(fake.sheet_requests()) >= 1
    assert fake.wb_requests() == []
    out = capsys.readouterr().out
    (line,) = events(out, "terminated")
    assert "phase=before_start" in line


# --------------------------------------------------------------------- stop between queries


def test_stop_after_the_first_query_records_the_rest_as_terminated(capsys):
    q1, q2, q3 = bundled()[:3]
    fake = FakeWB({(q1, 1): [ok_page()]})

    run_id = run(fake, limit_queries=3, should_stop=lambda: len(fake.wb_requests()) >= 1)

    assert run_id is not None
    assert len(fake.wb_requests()) == 1
    first, second, third = query_rows(run_id)
    assert (first["text"], first["status"], first["items_collected"]) == (q1, "ok", 5)
    for later, text in ((second, q2), (third, q3)):
        assert later["text"] == text
        assert later["status"] == "error"
        assert later["error_class"] == "terminated"
        assert later["items_collected"] == 0
        assert later["pages_fetched"] == 0
        assert later["http_status"] is None
        assert later["duration_ms"] == 0
        assert later["error_message"] == "SIGTERM received; no request sent"
        assert later["started_at"]
    run_row = run_rows()[0]
    assert run_row["status"] == "partial"
    assert run_row["finished_at"] is not None
    out = capsys.readouterr().out
    (line,) = events(out, "terminated")
    assert "remaining=2" in line and f"run_id={run_id}" in line
    assert len(events(out, "query_done")) == 3


def test_stop_once_the_run_row_exists_gives_an_error_run_with_no_requests(capsys):
    fake = FakeWB()

    def run_row_exists() -> bool:
        return bool(run_rows())

    run_id = run(fake, limit_queries=3, should_stop=run_row_exists)

    assert run_id is not None
    assert fake.wb_requests() == []
    statuses = [(r["status"], r["error_class"]) for r in query_rows(run_id)]
    assert statuses == [("error", "terminated")] * 3
    (run_row,) = run_rows()
    assert run_row["status"] == "error"  # no success -> the unchanged Phase 1 derivation
    assert run_row["finished_at"] is not None
    assert run_row["status"] != "running"
    assert len(events(capsys.readouterr().out, "terminated")) == 1


def test_stop_outranks_the_circuit_breaker():
    qs = bundled()[:6]
    forbidden = lambda: html_response(403, headers={"server": "wbaas"})  # noqa: E731
    fake = FakeWB({(q, 1): [forbidden()] for q in qs[:3]})

    run_id = run(fake, limit_queries=6, should_stop=lambda: len(fake.wb_requests()) >= 3)

    assert run_id is not None
    assert len(fake.wb_requests()) == 3
    records = query_rows(run_id)
    assert [r["status"] for r in records[:3]] == ["blocked"] * 3
    for later in records[3:]:
        assert (later["status"], later["error_class"]) == ("error", "terminated")
    assert run_rows()[0]["status"] == "blocked"


def test_default_should_stop_changes_nothing(capsys):
    (q1,) = bundled()[:1]
    fake = FakeWB({(q1, 1): [ok_page()]})

    run_id = run(fake, limit_queries=1)

    assert run_id is not None
    assert run_rows()[0]["status"] == "ok"
    assert events(capsys.readouterr().out, "terminated") == []


# ---------------------------------------------------------------- SIGKILL -> abandoned, gap


def test_a_killed_run_is_abandoned_and_its_slot_stays_a_gap(capsys):
    from wbmon.storage.db import connect_rw
    from wbmon.storage.migrations import init_db
    from wbmon.storage.writer import Writer

    settings = make_settings(top_n=5)
    init_db(settings.db_path)
    with contextlib.closing(connect_rw(settings.db_path)) as con:  # what a SIGKILL leaves behind
        killed = Writer(con).start_run(
            trigger="scheduler",
            started_at="2026-10-16T09:00:05Z",
            query_source="bundled",
            params_json="{}",
            top_n=5,
            n_queries=70,
            slot_utc=SLOT_0900,
        )
    fake = FakeWB()

    assert run(fake, trigger="scheduler", slot_utc=SLOT_0900) is None

    (old,) = run_rows()
    assert old["id"] == killed
    assert old["status"] == "abandoned"
    assert old["finished_at"] is not None
    out = capsys.readouterr().out
    (abandoned,) = events(out, "abandoned")
    assert "count=1" in abandoned
    assert len(events(out, "slot_exists")) == 1
    assert fake.requests == []  # the interrupted slot is a gap, never a back-fill

    (q1,) = bundled()[:1]
    fake_next = FakeWB({(q1, 1): [ok_page()]})
    new_id = run(fake_next, trigger="scheduler", slot_utc=SLOT_1000, limit_queries=1)

    assert new_id is not None and new_id != killed
    assert [r["status"] for r in run_rows()] == ["abandoned", "ok"]


def test_terminated_is_a_registered_error_class():
    from wbmon.models import ERROR_CLASSES

    assert len(ERROR_CLASSES) == 20
    assert "terminated" in ERROR_CLASSES
