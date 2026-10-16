"""The read-only data layer: statuses, versions, loaders, connection hygiene, caching."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ui.dbbuilder import (
    Q1,
    Q2,
    Q2_IDS,
    Q3,
    RunSpec,
    append_run,
    build_db,
    failed_result,
    ok_result,
    scenario_all_blocked,
    scenario_latest,
    scenario_mixed,
)
from wbmon.clock import iso_z, utc_now
from wbmon.storage.db import connect_ro
from wbmon.storage.migrations import init_db
from wbmon.ui import data

RUN_LOG_COLUMNS = [
    "run_id",
    "trigger",
    "started_at",
    "finished_at",
    "status",
    "query_source",
    "top_n",
    "n_queries",
    "n_ok",
    "n_partial",
    "n_blocked",
    "n_error",
    "n_items",
    "error",
]


def scalar(path, sql, params=()):
    with contextlib.closing(connect_ro(path)) as con:
        return con.execute(sql, params).fetchone()[0]


# ------------------------------------------------------------------------------ db_status


def test_db_status_missing_creates_nothing(db_path):
    assert data.db_status(str(db_path)) == "missing"
    assert not db_path.exists()


def test_db_status_empty_file_is_uninitialized(db_path):
    db_path.write_bytes(b"")
    assert data.db_status(str(db_path)) == "uninitialized"
    assert db_path.stat().st_size == 0


def test_db_status_initialised_file_is_ready(db_path):
    init_db(db_path)
    assert data.db_status(str(db_path)) == "ready"


def test_db_status_garbage_file_is_error(db_path):
    db_path.write_bytes(b"not a database")
    assert data.db_status(str(db_path)) == "error"


# --------------------------------------------------------------------------- data_version


def test_data_version_of_an_empty_database(db_path):
    init_db(db_path)
    assert data.data_version(str(db_path)) == data.DataVersion(None, None, None)


def test_data_version_follows_the_latest_run_and_changes_on_append(db_path):
    built = scenario_mixed(db_path)
    last_rq = scalar(db_path, "SELECT MAX(id) FROM run_queries")
    before = data.data_version(str(db_path))
    assert before == data.DataVersion(built.run_ids[-1], last_rq, "ok")
    started = iso_z(utc_now())
    append_run(db_path, RunSpec(started, {Q2: ok_result(started, Q2_IDS)}, trigger="manual"))
    assert data.data_version(str(db_path)) != before


# ----------------------------------------------------------------------------- overview


def test_overview_of_the_mixed_history(db_path):
    built = scenario_mixed(db_path)
    overview = data.load_overview_raw(str(db_path))
    first_started = scalar(db_path, "SELECT started_at FROM runs WHERE id = ?", (built.run_ids[0],))
    last = built.run_ids[-1]
    assert overview == {
        "n_runs": 6,
        "n_ok_runs": 6,
        "first_started": first_started,
        "last_started": scalar(db_path, "SELECT started_at FROM runs WHERE id = ?", (last,)),
        "last_ok_finished": scalar(db_path, "SELECT finished_at FROM runs WHERE id = ?", (last,)),
        "n_obs": 60,
        "n_products": 12,
        "n_queries": 3,
        "top_n_latest": 200,
    }


def test_overview_of_an_empty_database(db_path):
    init_db(db_path)
    overview = data.load_overview_raw(str(db_path))
    assert overview["n_runs"] == overview["n_ok_runs"] == overview["n_obs"] == 0
    assert overview["n_products"] == overview["n_queries"] == 0
    assert overview["first_started"] is None
    assert overview["last_ok_finished"] is None
    assert overview["top_n_latest"] is None


def test_overview_of_an_all_blocked_history(db_path):
    scenario_all_blocked(db_path)
    overview = data.load_overview_raw(str(db_path))
    assert overview["n_runs"] == 3
    assert overview["n_ok_runs"] == 0
    assert overview["last_ok_finished"] is None
    assert overview["n_obs"] == 0


# ---------------------------------------------------------------------------- run log


def test_run_log_is_newest_first_with_the_contract_columns(db_path):
    built = scenario_mixed(db_path)
    frame = data.load_run_log_raw(str(db_path))
    assert list(frame.columns) == RUN_LOG_COLUMNS
    assert list(frame["run_id"]) == list(reversed(built.run_ids))
    assert len(data.load_run_log_raw(str(db_path), limit=2)) == 2


# -------------------------------------------------------------------- latest run detail


def test_blocked_http_of_an_all_blocked_run(db_path):
    built = scenario_all_blocked(db_path)
    detail = data.load_latest_run_detail_raw(str(db_path), built.run_ids[-1])
    assert detail == {"blocked_http": 403}


def test_circuit_open_rows_without_a_code_do_not_hide_the_real_status(db_path):
    started = iso_z(utc_now() - timedelta(minutes=5))
    spec = RunSpec(
        started,
        {
            Q1: failed_result(started),
            Q2: failed_result(started, http_status=None, error_class="circuit_open"),
            Q3: failed_result(started, http_status=None, error_class="circuit_open"),
        },
        n_queries=3,
    )
    built = build_db(db_path, [spec])
    assert data.load_latest_run_detail_raw(str(db_path), built.run_ids[0]) == {"blocked_http": 403}


def test_blocked_http_is_none_for_a_clean_run(db_path):
    built = scenario_mixed(db_path)
    assert data.load_latest_run_detail_raw(str(db_path), built.run_ids[-1]) == {
        "blocked_http": None
    }


# ------------------------------------------------------------------- connection hygiene


def test_every_loader_opens_one_connection_and_closes_it(db_path, monkeypatch):
    built = scenario_latest(db_path, "blocked")
    opened: list[sqlite3.Connection] = []
    real = data.connect_ro

    def spy(path):
        con = real(path)
        opened.append(con)
        return con

    monkeypatch.setattr(data, "connect_ro", spy)
    path = str(db_path)
    calls = [
        lambda: data.db_status(path),
        lambda: data.data_version(path),
        lambda: data.load_overview_raw(path),
        lambda: data.load_run_log_raw(path),
        lambda: data.load_latest_run_detail_raw(path, built.run_ids[-1]),
    ]
    for call in calls:
        opened.clear()
        call()
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")


def test_a_write_through_the_ui_connection_is_refused(db_path):
    init_db(db_path)
    with contextlib.closing(connect_ro(db_path)) as con, pytest.raises(sqlite3.OperationalError):
        con.execute(
            "INSERT INTO queries(text, sheet_order, first_seen_at, last_seen_at)"
            " VALUES ('x', 9, 'a', 'a')"
        )


# ------------------------------------------------------------------------------ caching


def test_cached_twin_runs_the_raw_loader_once_per_data_version(db_path, monkeypatch):
    init_db(db_path)
    calls: list[str] = []

    def counting(path):
        calls.append(path)
        return {"n_runs": 0}

    monkeypatch.setattr(data, "load_overview_raw", counting)
    path = str(db_path)
    same = data.DataVersion(1, 1, "ok")
    data.load_overview(path, same)
    data.load_overview(path, data.DataVersion(1, 1, "ok"))
    assert len(calls) == 1
    data.load_overview(path, data.DataVersion(2, 5, "ok"))
    assert len(calls) == 2


def test_heartbeat_file_name_matches_the_scheduler_contract():
    health = pytest.importorskip("wbmon.health")
    assert data.HEARTBEAT_FILE == health.HEARTBEAT_FILENAME


# ---------------------------------------------------------------------------- heartbeat


def put(db_path, payload):
    (db_path.parent / data.HEARTBEAT_FILE).write_text(payload, encoding="utf-8")


def test_heartbeat_absent_is_none(db_path):
    assert data.read_heartbeat(db_path) is None


def test_heartbeat_z_timestamp_is_aware_utc(db_path):
    put(db_path, json.dumps({"ts": "2026-10-16T09:00:05Z"}))
    assert data.read_heartbeat(db_path) == datetime(2026, 10, 16, 9, 0, 5, tzinfo=UTC)


def test_heartbeat_offset_is_converted_to_utc(db_path):
    put(db_path, json.dumps({"ts": "2026-10-16T12:00:05+03:00"}))
    assert data.read_heartbeat(db_path) == datetime(2026, 10, 16, 9, 0, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        "{broken",
        "[]",
        "{}",
        json.dumps({"ts": "2026-10-16T09:00:05"}),
        json.dumps({"ts": 12345}),
        json.dumps({"ts": None}),
        json.dumps({"ts": "not a time"}),
        "",
    ],
)
def test_heartbeat_unusable_content_is_none(db_path, payload):
    put(db_path, payload)
    assert data.read_heartbeat(db_path) is None


def test_ui_data_never_uses_the_writer_stack():
    source = Path(data.__file__).read_text(encoding="utf-8")
    for forbidden in ("connect_rw", "storage.writer", "storage.migrations", "storage.lock"):
        assert forbidden not in source
