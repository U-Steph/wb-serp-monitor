"""``run_once``: lock, abandon, slot, breaker, deadline, isolation, writer fallback and logs."""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3

import httpx
import pytest

from factories import (
    FakeWB,
    RecordingSleep,
    SteppingClock,
    html_response,
    json_response,
    make_page,
    make_product,
    make_settings,
)

SLOT = "2026-10-16T09:00:00Z"


def bundled() -> list[str]:
    from wbmon.queries import load_bundled

    return list(load_bundled())


def ok_page(first_id: int = 900000001, n: int = 5) -> httpx.Response:
    return json_response(make_page(first_id, n))


def forbidden() -> httpx.Response:
    return html_response(403, headers={"server": "wbaas"})


def limited() -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": "1"}, text="slow down")


def script_ok(queries: list[str], *, n: int = 5) -> dict:
    """One ``n``-item page 1 per query (pair with ``top_n=n`` so no page 2 is requested)."""
    return {(text, 1): [ok_page(900000001 + 1000 * i, n)] for i, text in enumerate(queries)}


def run(fake: FakeWB, settings=None, **kwargs):
    from wbmon.log import configure_logging
    from wbmon.orchestrator import run_once

    configure_logging("INFO")  # bind the handler to the stdout capsys is capturing right now
    sleep = kwargs.pop("sleep", None) or RecordingSleep()
    settings = settings if settings is not None else make_settings(top_n=5)
    run_id = run_once(settings, transport=httpx.MockTransport(fake), sleep=sleep, **kwargs)
    return run_id, sleep


def rows(sql: str, *params):
    path = os.environ["WBMON_DB_PATH"]
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params)]


def run_row(run_id: int) -> dict:
    return rows("SELECT * FROM runs WHERE id = ?", run_id)[0]


def query_rows(run_id: int) -> list[dict]:
    return rows(
        "SELECT rq.*, q.text, q.sheet_order FROM run_queries rq"
        " JOIN queries q ON q.id = rq.query_id WHERE rq.run_id = ? ORDER BY q.sheet_order",
        run_id,
    )


def events(out: str, name: str) -> list[str]:
    return [line for line in out.splitlines() if f" event={name} " in line + " "]


def new_running_run() -> int:
    """Create a 'running' run the way a crashed process would have left it."""
    from wbmon.storage.db import connect_rw
    from wbmon.storage.migrations import init_db
    from wbmon.storage.writer import Writer

    settings = make_settings()
    init_db(settings.db_path)
    with contextlib.closing(connect_rw(settings.db_path)) as con:
        return Writer(con).start_run(
            trigger="manual",
            started_at="2026-10-16T07:00:00Z",
            query_source="bundled",
            params_json="{}",
            top_n=5,
            n_queries=70,
        )


# ------------------------------------------------------------------------------------ breaker


def test_three_blocked_queries_open_the_circuit_for_the_remaining_67(capsys):
    qs = bundled()
    fake = FakeWB({(q, 1): [forbidden()] for q in qs[:3]})

    run_id, sleep = run(fake)

    assert len(fake.wb_requests()) == 3
    assert sleep.calls == [1.5, 1.5]
    summary = run_row(run_id)
    assert (summary["status"], summary["n_blocked"], summary["n_queries"], summary["n_items"]) == (
        "blocked",
        70,
        70,
        0,
    )
    stored = query_rows(run_id)
    assert len(stored) == 70
    assert [r["error_class"] for r in stored[:3]] == ["http_403"] * 3
    open_rows = stored[3:]
    assert len(open_rows) == 67
    for row in open_rows:
        assert (row["status"], row["error_class"]) == ("blocked", "circuit_open")
        assert row["pages_fetched"] == 0 and row["items_collected"] == 0
        assert row["duration_ms"] == 0 and row["started_at"]
        assert "no request sent" in row["error_message"]
    out = capsys.readouterr().out
    assert len(events(out, "breaker_open")) == 1
    assert len(events(out, "query_done")) == 70
    assert len(events(out, "run_done")) == 1
    assert "status=blocked" in events(out, "run_done")[0]


def test_an_ok_query_resets_the_consecutive_counter():
    qs = bundled()[:5]
    fake = FakeWB(
        {
            (qs[0], 1): [forbidden()],
            (qs[1], 1): [forbidden()],
            (qs[2], 1): [ok_page()],
            (qs[3], 1): [forbidden()],
            (qs[4], 1): [forbidden()],
        }
    )

    run_id, _ = run(fake, limit_queries=5)

    assert [r["status"] for r in query_rows(run_id)] == [
        "blocked",
        "blocked",
        "ok",
        "blocked",
        "blocked",
    ]
    assert all(r["error_class"] != "circuit_open" for r in query_rows(run_id))
    assert run_row(run_id)["status"] == "partial"
    assert len(fake.wb_requests()) == 5


def test_rate_limited_and_blocked_queries_both_count_towards_the_breaker():
    qs = bundled()[:6]
    fake = FakeWB(
        {
            (qs[0], 1): [limited(), limited()],
            (qs[1], 1): [forbidden()],
            (qs[2], 1): [limited(), limited()],
        }
    )

    run_id, _ = run(fake, limit_queries=6)

    stored = query_rows(run_id)
    assert [r["status"] for r in stored] == ["rate_limited", "blocked", "rate_limited"] + [
        "blocked"
    ] * 3
    assert [r["error_class"] for r in stored[3:]] == ["circuit_open"] * 3
    assert len(fake.wb_requests()) == 5  # 2 + 1 + 2, nothing for Q4..Q6
    assert run_row(run_id)["status"] == "blocked"


def test_errors_do_not_open_the_circuit():
    qs = bundled()[:4]
    bad = httpx.Response(502, text="bad gateway")
    fake = FakeWB({(q, 1): [bad, bad, bad] for q in qs})

    run_id, _ = run(fake, limit_queries=4)

    stored = query_rows(run_id)
    assert [r["status"] for r in stored] == ["error"] * 4
    assert all(r["error_class"] == "http_5xx" for r in stored)
    assert run_row(run_id)["status"] == "error"


# ------------------------------------------------------------------------------- isolation


def test_exception_in_one_query_does_not_abort_the_run(monkeypatch):
    from wbmon import orchestrator

    qs = bundled()[:3]
    real = orchestrator.collect_query

    def flaky(client, query, **kwargs):
        if query == qs[1]:
            raise RuntimeError("boom")
        return real(client, query, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_query", flaky)
    fake = FakeWB({(qs[0], 1): [ok_page()], (qs[2], 1): [ok_page(900100001)]})

    run_id, _ = run(fake, limit_queries=3)

    assert run_id is not None
    stored = query_rows(run_id)
    assert [r["status"] for r in stored] == ["ok", "error", "ok"]
    assert stored[1]["error_class"] == "exception"
    assert "RuntimeError: boom" in stored[1]["error_message"]
    assert stored[1]["started_at"] and stored[1]["duration_ms"] is not None
    assert run_row(run_id)["status"] == "partial"


def test_long_exception_text_is_truncated(monkeypatch):
    from wbmon import orchestrator

    def broken(client, query, **kwargs):
        raise ValueError("x" * 1000)

    monkeypatch.setattr(orchestrator, "collect_query", broken)

    run_id, _ = run(FakeWB(), limit_queries=1)

    message = query_rows(run_id)[0]["error_message"]
    assert message.startswith("ValueError: xxx")
    assert len(message) <= 200


def test_writer_failure_is_recorded_and_the_next_query_still_runs(monkeypatch):
    from wbmon import clock, orchestrator
    from wbmon.models import QueryResult
    from wbmon.normalize import normalize_product

    qs = bundled()[:2]
    settings = make_settings(top_n=5)
    stamp = "2026-10-16T09:00:00Z"
    twins = tuple(
        normalize_product(
            make_product(900000001 + i), position=1, collected_at=stamp, tz=settings.tzinfo()
        )
        for i in range(2)
    )
    real = orchestrator.collect_query

    def collect(client, query, **kwargs):
        if query == qs[0]:
            return QueryResult(
                status="ok",
                started_at=clock.iso_z(clock.utc_now()),
                duration_ms=5,
                observations=twins,
                http_status=200,
                pages_fetched=1,
                is_complete=True,
                normquery="x",
            )
        return real(client, query, **kwargs)

    monkeypatch.setattr(orchestrator, "collect_query", collect)
    fake = FakeWB({(qs[1], 1): [ok_page()]})

    run_id, _ = run(fake, settings, limit_queries=2)

    stored = query_rows(run_id)
    assert (stored[0]["status"], stored[0]["error_class"]) == ("error", "db_write_failed")
    assert stored[0]["items_collected"] == 0
    assert stored[1]["status"] == "ok"
    assert (
        rows("SELECT COUNT(*) AS n FROM observations WHERE query_id = ?", stored[0]["query_id"])[0][
            "n"
        ]
        == 0
    )
    assert run_row(run_id)["status"] == "partial"


def test_keyboard_interrupt_marks_the_run_error_and_propagates(monkeypatch):
    from wbmon import orchestrator

    def interrupt(client, query, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(orchestrator, "collect_query", interrupt)

    with pytest.raises(KeyboardInterrupt):
        run(FakeWB(), limit_queries=2)

    (only,) = rows("SELECT * FROM runs")
    assert only["status"] == "error"
    assert "KeyboardInterrupt" in only["error"]
    assert only["finished_at"]


# --------------------------------------------------------------------------------- deadline


def test_deadline_records_remaining_queries_without_requests(capsys):
    qs = bundled()
    clock_ = SteppingClock()

    def slow_page(request: httpx.Request) -> httpx.Response:
        clock_.advance(1000)
        return ok_page()

    fake = FakeWB({(qs[0], 1): [slow_page]})
    settings = make_settings(top_n=5, run_deadline=60)

    run_id, _ = run(fake, settings, monotonic=clock_)

    stored = query_rows(run_id)
    assert stored[0]["status"] == "ok"
    late = stored[1:]
    assert len(late) == 69
    for row in late:
        assert (row["status"], row["error_class"]) == ("error", "deadline")
        assert "run deadline of 60 s exceeded" in row["error_message"]
        assert row["duration_ms"] == 0
    assert len(fake.wb_requests()) == 1
    assert run_row(run_id)["status"] == "partial"
    out = capsys.readouterr().out
    assert len(events(out, "deadline")) == 1
    assert len(events(out, "query_done")) == 70


# ------------------------------------------------------------------------- lock and abandon


def test_busy_lock_creates_no_run_and_leaves_running_runs_alone(capsys):
    from wbmon.storage.lock import run_lock

    stale_id = new_running_run()
    settings = make_settings(top_n=5)
    fake = FakeWB()

    with run_lock(settings.db_path):
        run_id, sleep = run(fake, settings, limit_queries=1)

    assert run_id is None
    assert fake.requests == []
    assert sleep.calls == []
    assert [r["id"] for r in rows("SELECT id FROM runs")] == [stale_id]
    assert run_row(stale_id)["status"] == "running"
    out = capsys.readouterr().out
    assert len(events(out, "lock_busy")) == 1
    assert "level=WARNING" in events(out, "lock_busy")[0]
    assert events(out, "abandoned") == []


def test_lock_is_released_after_the_run():
    from wbmon.storage.lock import run_lock

    q1 = bundled()[0]
    run_id, _ = run(FakeWB(script_ok([q1])), limit_queries=1)
    assert run_id is not None

    with run_lock(make_settings().db_path):
        pass


def test_stale_running_run_is_abandoned_under_the_lock(capsys):
    stale_id = new_running_run()
    q1 = bundled()[0]

    run_id, _ = run(FakeWB(script_ok([q1])), limit_queries=1)

    stale = run_row(stale_id)
    assert stale["status"] == "abandoned"
    assert stale["finished_at"]
    assert run_id != stale_id
    assert run_row(run_id)["status"] == "ok"
    out = capsys.readouterr().out
    (line,) = events(out, "abandoned")
    assert "count=1" in line


# ---------------------------------------------------------------------------------- slot


def test_existing_slot_returns_none_and_creates_nothing(capsys):
    q1 = bundled()[0]
    fake = FakeWB(script_ok([q1]))

    first, _ = run(fake, limit_queries=1, trigger="scheduler", slot_utc=SLOT)
    second, sleep = run(fake, limit_queries=1, trigger="scheduler", slot_utc=SLOT)

    assert first is not None
    assert second is None
    assert sleep.calls == []
    assert len(fake.wb_requests()) == 1
    (only,) = rows("SELECT * FROM runs")
    assert (only["slot_utc"], only["trigger"], only["id"]) == (SLOT, "scheduler", first)
    out = capsys.readouterr().out
    (line,) = events(out, "slot_exists")
    assert f"slot={SLOT}" in line and f"run_id={first}" in line


def test_different_slots_each_get_a_run():
    q1 = bundled()[0]
    fake = FakeWB({(q1, 1): [ok_page(), ok_page()]})

    first, _ = run(fake, limit_queries=1, trigger="scheduler", slot_utc=SLOT)
    second, _ = run(fake, limit_queries=1, trigger="scheduler", slot_utc="2026-10-16T10:00:00Z")

    assert first is not None and second is not None and first != second


def test_slot_integrity_race_is_a_normal_skip(monkeypatch, capsys):
    from wbmon import orchestrator

    q1 = bundled()[0]
    run(FakeWB(script_ok([q1])), limit_queries=1, trigger="scheduler", slot_utc=SLOT)
    # simulate a racer that created the slot between the check and the insert
    monkeypatch.setattr(orchestrator, "run_id_for_slot", lambda con, slot: None)

    again, _ = run(FakeWB(), limit_queries=1, trigger="scheduler", slot_utc=SLOT)

    assert again is None
    assert len(rows("SELECT id FROM runs")) == 1
    assert "event=slot_exists" in capsys.readouterr().out


# ------------------------------------------------------------------------------ audit trail


def test_every_attempt_row_is_complete_and_honest(capsys):
    qs = bundled()[:4]
    fake = FakeWB(
        {
            (qs[0], 1): [ok_page()],
            (qs[1], 1): [forbidden()],
            (qs[2], 1): [ok_page(900200001)],
            (qs[3], 1): [httpx.Response(502, text="bad")] * 3,
        }
    )

    run_id, _ = run(fake, limit_queries=4)

    stored = query_rows(run_id)
    assert [r["status"] for r in stored] == ["ok", "blocked", "ok", "error"]
    iso = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
    for row in stored:
        assert iso.match(row["started_at"])
        assert row["duration_ms"] is not None
    for row in (stored[0], stored[2]):
        assert row["http_status"] == 200
        assert row["pages_fetched"] >= 1
        assert row["normquery"] == "тест запрос"
        assert row["items_collected"] == 5
        assert row["is_complete"] == 1
    assert stored[1]["error_message"] and stored[1]["http_status"] == 403
    assert stored[3]["error_class"] == "http_5xx"
    out = capsys.readouterr().out
    done = events(out, "query_done")
    assert len(done) == 4
    assert "status=ok" in done[0] and "items=5" in done[0] and "pages=1" in done[0]
    assert "status=blocked" in done[1] and "error_class=http_403" in done[1]
    (start,) = events(out, "run_start")
    assert "queries=4" in start and "trigger=manual" in start
    assert len(events(out, "queries_loaded")) == 1


def test_queries_register_in_sheet_order_and_run_follows_it():
    qs = bundled()
    fake = FakeWB(script_ok(qs[:3]))

    run_id, _ = run(fake, limit_queries=3)

    order = rows("SELECT text, sheet_order FROM queries ORDER BY sheet_order")
    assert [r["text"] for r in order] == qs
    assert [r["text"] for r in query_rows(run_id)] == qs[:3]
    assert [r.url.params["query"] for r in fake.wb_requests()] == qs[:3]
    assert run_row(run_id)["n_queries"] == 3


def test_orchestrator_has_no_shuffle_or_jitter():
    from factories import SRC_DIR

    source = (SRC_DIR / "orchestrator.py").read_text(encoding="utf-8")
    assert "shuffle" not in source
    assert "random" not in source
    assert "BREAKER_THRESHOLD = 3" in source
