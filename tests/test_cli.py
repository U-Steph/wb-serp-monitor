"""End-to-end `wbmon collect` via `cli.main`: pages, full runs, breaker, slots, exit codes."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from factories import (
    FakeWB,
    RecordingSleep,
    html_response,
    json_response,
    make_page,
)


def bundled() -> list[str]:
    from wbmon.queries import load_bundled

    return list(load_bundled())


def collect(fake: FakeWB, *argv: str, sleep: RecordingSleep | None = None):
    """Run ``wbmon <argv>`` offline; returns ``(exit_code, sleep)``."""
    from wbmon import cli

    sleep = sleep if sleep is not None else RecordingSleep()
    code = cli.main(list(argv), _transport=httpx.MockTransport(fake), _sleep=sleep)
    return code, sleep


def db_rows(sql: str, *params):
    path = os.environ["WBMON_DB_PATH"]
    with contextlib.closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, params)]


def scalar(sql: str, *params):
    return next(iter(db_rows(sql, *params)[0].values()))


def one_page(first_id: int, n: int = 5) -> httpx.Response:
    return json_response(make_page(first_id, n))


def event_lines(out: str, name: str) -> list[str]:
    return [line for line in out.splitlines() if f" event={name} " in f"{line} "]


# ------------------------------------------------------------------------------ multi-page


def test_two_page_queries_give_top_200_with_exact_positions(capsys):
    q1, q2 = bundled()[:2]
    fake = FakeWB(
        {
            (q1, 1): [one_page(900001001, 100)],
            (q1, 2): [one_page(900001101, 100)],
            (q2, 1): [one_page(900002001, 100)],
            (q2, 2): [one_page(900002101, 100)],
        }
    )

    code, sleep = collect(fake, "collect", "--limit-queries", "2")

    assert code == 0
    (run,) = db_rows("SELECT * FROM runs")
    assert (run["status"], run["trigger"], run["n_queries"], run["n_ok"]) == ("ok", "manual", 2, 2)
    assert run["slot_utc"] is None and run["top_n"] == 200 and run["finished_at"]
    attempts = db_rows("SELECT * FROM run_queries ORDER BY query_id")
    assert len(attempts) == 2
    for attempt in attempts:
        assert attempt["status"] == "ok"
        assert attempt["pages_fetched"] == 2
        assert attempt["items_collected"] == 200
        assert attempt["is_complete"] == 1
        assert attempt["normquery"] == "тест запрос"
        assert attempt["http_status"] == 200
        assert attempt["duration_ms"] is not None and attempt["started_at"]
    assert scalar("SELECT COUNT(*) FROM observations") == 400
    for query_id in (attempts[0]["query_id"], attempts[1]["query_id"]):
        positions = [
            r["position"]
            for r in db_rows(
                "SELECT position FROM observations WHERE query_id = ? ORDER BY position", query_id
            )
        ]
        assert positions == list(range(1, 201))
    assert scalar("SELECT COUNT(*) FROM observations WHERE price_rub IS NULL") == 0
    assert scalar("SELECT COUNT(*) FROM observations WHERE delivery_days IS NULL") == 0
    assert len(fake.wb_requests()) == 4
    assert sleep.calls == [1.5, 1.5, 1.5]
    out = capsys.readouterr().out
    (summary,) = event_lines(out, "run_summary")
    assert "status=ok" in summary and "items=400" in summary


def test_stdout_is_one_key_value_line_per_event(capsys):
    q1 = bundled()[0]
    fake = FakeWB({(q1, 1): [one_page(900000001)]})

    code, _ = collect(fake, "collect", "--limit-queries", "1", "--top-n", "5")

    assert code == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(line.startswith("ts=") and " level=" in line and " event=" in line for line in lines)
    events = [line.split(" event=")[1].split(" ")[0] for line in lines]
    assert events == ["queries_loaded", "run_start", "query_done", "run_done", "run_summary"]


# --------------------------------------------------------------------- full 70-query runs


def test_full_run_covers_all_70_queries(capsys):
    queries = bundled()
    assert len(queries) == 70
    fake = FakeWB({(q, 1): [one_page(900000001 + 100 * i)] for i, q in enumerate(queries)})

    code, sleep = collect(fake, "collect", "--top-n", "5")

    assert code == 0
    (run,) = db_rows("SELECT * FROM runs")
    assert (run["status"], run["n_queries"], run["n_ok"], run["n_items"]) == ("ok", 70, 70, 350)
    assert scalar("SELECT COUNT(*) FROM run_queries WHERE status = 'ok'") == 70
    assert scalar("SELECT COUNT(*) FROM observations") == 350
    assert scalar("SELECT COUNT(*) FROM queries") == 70
    assert len(fake.wb_requests()) == 70
    assert sleep.calls == [1.5] * 69
    assert run["query_source"] == "bundled"
    assert "status=ok" in event_lines(capsys.readouterr().out, "run_summary")[0]


def test_all_blocked_run_stops_after_three_requests_and_still_exits_zero(capsys):
    q1, q2, q3 = bundled()[:3]
    fake = FakeWB({(q, 1): [html_response(403, headers={"server": "wbaas"})] for q in (q1, q2, q3)})

    code, _ = collect(fake, "collect")

    assert code == 0
    (run,) = db_rows("SELECT * FROM runs")
    assert (run["status"], run["n_blocked"], run["n_items"]) == ("blocked", 70, 0)
    assert len(fake.wb_requests()) == 3
    assert scalar("SELECT COUNT(*) FROM run_queries WHERE error_class = 'circuit_open'") == 67
    assert scalar("SELECT COUNT(*) FROM run_queries WHERE error_class = 'http_403'") == 3
    assert scalar("SELECT COUNT(*) FROM observations") == 0
    out = capsys.readouterr().out
    assert len(event_lines(out, "breaker_open")) == 1
    (summary,) = event_lines(out, "run_summary")
    assert "status=blocked" in summary


# ------------------------------------------------------------- recovery and append-only


def test_crashed_running_run_is_marked_abandoned_by_the_next_collect(capsys):
    from wbmon import cli
    from wbmon.storage.db import connect_rw
    from wbmon.storage.writer import Writer

    assert cli.main(["init-db"]) == 0
    with contextlib.closing(connect_rw(Path(os.environ["WBMON_DB_PATH"]))) as con:
        stale = Writer(con).start_run(
            trigger="manual",
            started_at="2026-10-16T07:00:00Z",
            query_source="bundled",
            params_json="{}",
            top_n=5,
            n_queries=70,
        )
    capsys.readouterr()
    fake = FakeWB({(bundled()[0], 1): [one_page(900000001)]})

    code, _ = collect(fake, "collect", "--limit-queries", "1", "--top-n", "5")

    assert code == 0
    statuses = {r["id"]: r["status"] for r in db_rows("SELECT id, status FROM runs")}
    assert statuses[stale] == "abandoned"
    assert [s for run_id, s in statuses.items() if run_id != stale] == ["ok"]
    assert "event=abandoned" in capsys.readouterr().out


def test_second_collect_only_appends(capsys):
    q1 = bundled()[0]
    fake = FakeWB({(q1, 1): [one_page(900000001), one_page(900000001)]})
    first_run = "SELECT * FROM observations WHERE run_id = 1 ORDER BY position"

    assert collect(fake, "collect", "--limit-queries", "1", "--top-n", "5")[0] == 0
    before = db_rows(first_run)
    assert len(before) == 5

    assert collect(fake, "collect", "--limit-queries", "1", "--top-n", "5")[0] == 0

    assert scalar("SELECT COUNT(*) FROM runs") == 2
    assert scalar("SELECT COUNT(*) FROM observations") == 10
    assert db_rows(first_run) == before
    assert scalar("SELECT COUNT(*) FROM run_queries") == 2


# ------------------------------------------------------------------------ scheduler contract


def test_scheduler_trigger_requires_a_slot(capsys):
    fake = FakeWB()

    code, _ = collect(fake, "collect", "--trigger", "scheduler")

    assert code == 2
    assert "--slot" in capsys.readouterr().err
    assert fake.requests == []
    assert not Path(os.environ["WBMON_DB_PATH"]).exists()


def test_scheduler_slot_is_normalised_and_idempotent(capsys):
    q1 = bundled()[0]
    fake = FakeWB({(q1, 1): [one_page(900000001)]})
    argv = (
        "collect",
        "--trigger",
        "scheduler",
        "--slot",
        "2026-10-16T12:34:56+03:00",
        "--limit-queries",
        "1",
        "--top-n",
        "5",
    )

    assert collect(fake, *argv)[0] == 0
    (run,) = db_rows("SELECT * FROM runs")
    assert run["slot_utc"] == "2026-10-16T09:00:00Z"
    assert run["trigger"] == "scheduler"
    capsys.readouterr()

    code, sleep = collect(fake, *argv)

    assert code == 0
    assert scalar("SELECT COUNT(*) FROM runs") == 1
    assert len(fake.wb_requests()) == 1
    assert sleep.calls == []
    out = capsys.readouterr().out
    assert "event=slot_exists" in out
    assert "event=run_summary" not in out


@pytest.mark.parametrize(
    "argv",
    [
        ("collect", "--slot", "yesterday"),
        ("collect", "--trigger", "scheduler", "--slot", "2026-10-16T12:00:00"),
        ("collect", "--trigger", "probe"),
        ("collect", "--trigger", "scheduler", "--slot", ""),
        # WR-03: no UTC equivalent -> used to escape argparse as an OverflowError traceback
        ("collect", "--slot", "0001-01-01T00:00:00+05:00"),
        ("collect", "--trigger", "scheduler", "--slot", "9999-12-31T23:00:00-05:00"),
    ],
)
def test_bad_trigger_or_slot_is_a_usage_error(argv):
    fake = FakeWB()

    code, _ = collect(fake, *argv)

    assert code == 2
    assert fake.requests == []
    assert not Path(os.environ["WBMON_DB_PATH"]).exists()


def test_busy_lock_exits_zero_without_a_run(capsys):
    from wbmon.config import Settings
    from wbmon.storage.lock import run_lock
    from wbmon.storage.migrations import init_db

    settings = Settings.from_env()
    init_db(settings.db_path)
    fake = FakeWB()

    with run_lock(settings.db_path):
        code, sleep = collect(fake, "collect", "--limit-queries", "1")

    assert code == 0
    assert scalar("SELECT COUNT(*) FROM runs") == 0
    assert fake.requests == []
    assert sleep.calls == []
    out = capsys.readouterr().out
    assert "event=lock_busy" in out
    assert "event=run_summary" not in out


# --------------------------------------------------------------------------- exit codes


def test_invalid_env_is_a_configuration_error(monkeypatch, capsys):
    monkeypatch.setenv("WBMON_TOP_N", "abc")
    fake = FakeWB()

    code, _ = collect(fake, "collect")

    assert code == 2
    assert "WBMON_TOP_N" in capsys.readouterr().err
    assert fake.requests == []


@pytest.mark.parametrize("flag", ["--top-n", "--limit-queries"])
def test_non_positive_numbers_are_usage_errors(flag, capsys):
    fake = FakeWB()

    code, _ = collect(fake, "collect", flag, "0")

    assert code == 2
    assert fake.requests == []
    assert not Path(os.environ["WBMON_DB_PATH"]).exists()


def test_unexpected_exception_is_a_crash_with_exit_one(monkeypatch, capsys):
    from wbmon import cli

    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli, "run_once", explode)

    code, _ = collect(FakeWB(), "collect")

    assert code == 1
    (line,) = event_lines(capsys.readouterr().out, "crash")
    assert "boom" in line and "level=ERROR" in line


def test_crash_inside_a_run_marks_the_run_error_and_exits_one(monkeypatch, capsys):
    from wbmon import orchestrator

    def explode(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(orchestrator, "collect_query", explode)
    # collect_query errors are isolated per query, so break something outside the query loop
    monkeypatch.setattr(orchestrator, "WbClient", explode)

    code, _ = collect(FakeWB(), "collect", "--limit-queries", "1")

    assert code == 1
    (run,) = db_rows("SELECT * FROM runs")
    assert run["status"] == "error"
    assert "kaboom" in run["error"]
    assert "event=crash" in capsys.readouterr().out


# ------------------------------------------------------------------------- help and hygiene


def test_module_help_lists_the_three_commands(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "wbmon", "--help"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        timeout=60,
    )

    assert result.returncode == 0
    for command in ("init-db", "collect", "probe"):
        assert command in result.stdout


def test_collect_help_documents_the_contract(capsys):
    from wbmon import cli

    assert cli.main(["collect", "--help"]) == 0

    out = capsys.readouterr().out
    for flag in ("--db", "--top-n", "--limit-queries", "--trigger", "--slot"):
        assert flag in out


def test_no_database_is_created_outside_the_isolated_path(tmp_path):
    q1 = bundled()[0]
    fake = FakeWB({(q1, 1): [one_page(900000001)]})

    assert collect(fake, "collect", "--limit-queries", "1", "--top-n", "5")[0] == 0

    assert not (Path.cwd() / "data").exists()
    names = sorted(p.name for p in tmp_path.iterdir())
    assert "test.sqlite3" in names
    assert all(name.startswith("test.sqlite3") for name in names), names
