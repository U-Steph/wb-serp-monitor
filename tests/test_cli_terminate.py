"""`wbmon collect` turns SIGTERM into a closed run and exit 0 (in-process, offline).

The signal is raised synchronously from inside the FakeWB callable, i.e. while the first query's
request is in flight, so the handler has set the stop flag before the request returns.
"""

from __future__ import annotations

import contextlib
import os
import signal
import sqlite3

import httpx

from factories import FakeWB, RecordingSleep, json_response, make_page

SLOT = "2026-10-16T09:00:00Z"


def bundled() -> list[str]:
    from wbmon.queries import load_bundled

    return list(load_bundled())


def db_rows(sql: str, *params):
    with contextlib.closing(sqlite3.connect(os.environ["WBMON_DB_PATH"])) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params)]


def event_lines(out: str, name: str) -> list[str]:
    return [line for line in out.splitlines() if f" event={name} " in f"{line} "]


def term_during_first_query(seen: dict | None = None):
    """A FakeWB script item that delivers SIGTERM to this process, then answers with 5 products."""

    def item(_request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["sigterm_inside"] = signal.getsignal(signal.SIGTERM)
            seen["sigint_inside"] = signal.getsignal(signal.SIGINT)
        signal.raise_signal(signal.SIGTERM)
        return json_response(make_page(900000001, 5))

    return item


def collect(fake: FakeWB, *argv: str) -> int:
    from wbmon import cli

    return cli.main(list(argv), _transport=httpx.MockTransport(fake), _sleep=RecordingSleep())


def test_sigterm_during_a_query_closes_the_run_partial_and_exits_zero(capsys):
    q1, q2, q3 = bundled()[:3]
    before = signal.getsignal(signal.SIGTERM)
    fake = FakeWB({(q1, 1): [term_during_first_query()]})

    code = collect(fake, "collect", "--limit-queries", "3", "--top-n", "5")

    assert code == 0
    assert len(fake.wb_requests()) == 1
    (run,) = db_rows("SELECT * FROM runs")
    assert run["status"] == "partial"
    assert run["finished_at"] is not None
    first, second, third = db_rows(
        "SELECT rq.*, q.text FROM run_queries rq JOIN queries q ON q.id = rq.query_id"
        " ORDER BY q.sheet_order"
    )
    assert (first["text"], first["status"], first["items_collected"]) == (q1, "ok", 5)
    assert [
        (r["text"], r["status"], r["error_class"], r["items_collected"]) for r in (second, third)
    ] == [
        (q2, "error", "terminated", 0),
        (q3, "error", "terminated", 0),
    ]
    out = capsys.readouterr().out
    assert len(event_lines(out, "terminated")) == 1
    (summary,) = event_lines(out, "run_summary")
    assert "status=partial" in summary
    assert signal.getsignal(signal.SIGTERM) == before  # handler restored after main()


def test_collect_handles_sigterm_only_and_leaves_sigint_alone():
    (q1,) = bundled()[:1]
    seen: dict = {}
    before = signal.getsignal(signal.SIGTERM)
    fake = FakeWB({(q1, 1): [term_during_first_query(seen)]})

    assert collect(fake, "collect", "--limit-queries", "1", "--top-n", "5") == 0

    assert seen["sigterm_inside"] not in (signal.SIG_DFL, signal.SIG_IGN, before)
    assert seen["sigint_inside"] is signal.default_int_handler  # Phase 1 KeyboardInterrupt path
    assert signal.getsignal(signal.SIGTERM) == before


def test_an_interrupted_slot_keeps_its_row_and_is_not_rerun(capsys):
    q1 = bundled()[0]
    argv = ("collect", "--trigger", "scheduler", "--slot", SLOT, "--limit-queries", "2")
    fake = FakeWB({(q1, 1): [term_during_first_query()]})

    assert collect(fake, *argv, "--top-n", "5") == 0

    (run,) = db_rows("SELECT * FROM runs")
    assert run["slot_utc"] == SLOT
    assert run["status"] == "partial"
    capsys.readouterr()

    again = FakeWB()  # any WB request would raise AssertionError: the slot must not be re-run
    assert collect(again, *argv, "--top-n", "5") == 0

    assert again.requests == []
    assert len(db_rows("SELECT id FROM runs")) == 1
    assert len(event_lines(capsys.readouterr().out, "slot_exists")) == 1
