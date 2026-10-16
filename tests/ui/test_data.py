"""The read-only data layer: statuses, versions, loaders, connection hygiene, caching."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from ui.dbbuilder import (
    NM,
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
from wbmon.storage.reader import MAX_IN_IDS
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
        lambda: data.load_queries_raw(path),
        lambda: data.load_run_grid_raw(path, built.query_ids[Q1], 1, ""),
        lambda: data.load_product_options_raw(path, built.query_ids[Q1]),
        lambda: data.load_latest_data_run_raw(path, built.query_ids[Q1]),
        lambda: data.load_top_ids_raw(path, built.run_ids[0], built.query_ids[Q1], 3),
        lambda: data.load_observations_raw(path, built.query_ids[Q1], (NM["A"],), ""),
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


# ------------------------------------------------------------------- chart loaders (03-02)

A, B, C, D, E, F = (NM[k] for k in "ABCDEF")
GRID_COLUMNS = [
    "run_id",
    "started_at",
    "run_status",
    "trigger",
    "q_status",
    "error_class",
    "http_status",
    "items_collected",
    "is_complete",
    "pages_fetched",
]
OBS_COLUMNS = [
    "run_id",
    "collected_at",
    "nm_id",
    "position",
    "is_promoted",
    "price_rub",
    "rating",
    "feedbacks",
    "delivery_days",
    "seller_name",
    "name",
    "brand",
]


def test_load_queries_keeps_sheet_order_and_exact_wording(db_path):
    scenario_mixed(db_path)
    frame = data.load_queries_raw(str(db_path))
    assert list(frame.columns) == ["query_id", "text", "sheet_order"]
    assert list(frame["text"]) == [Q1, Q2, Q3]
    assert list(frame["sheet_order"]) == [1, 2, 3]
    assert "ё" in frame["text"][0]
    assert "х" in frame["text"][1] and "x" not in frame["text"][1]  # Cyrillic, not Latin
    assert "x" in frame["text"][2]


def test_run_grid_of_the_first_query(db_path):
    built = scenario_mixed(db_path)
    grid = data.load_run_grid_raw(str(db_path), built.query_ids[Q1], 1, "")
    assert list(grid.columns) == GRID_COLUMNS
    assert list(grid["run_id"]) == built.run_ids
    assert list(grid["q_status"]) == ["ok", "blocked", "ok", "partial", "ok", "ok"]
    assert grid["http_status"][1] == 403
    assert grid["is_complete"][3] == 0
    assert list(grid["started_at"]) == sorted(grid["started_at"])


def test_run_grid_since_keeps_runs_from_that_start(db_path):
    built = scenario_mixed(db_path)
    since = scalar(db_path, "SELECT started_at FROM runs WHERE id = ?", (built.run_ids[3],))
    grid = data.load_run_grid_raw(str(db_path), built.query_ids[Q1], 1, since)
    assert list(grid["run_id"]) == built.run_ids[3:]


def test_run_grid_leaves_out_runs_that_never_reached_the_query(db_path):
    """A run in flight has attempt rows only for the queries it reached so far."""
    built = scenario_latest(db_path, "running")
    r7 = built.run_ids[-1]
    q = built.query_ids
    assert r7 in set(data.load_run_grid_raw(str(db_path), q[Q1], 1, "")["run_id"])
    assert r7 not in set(data.load_run_grid_raw(str(db_path), q[Q2], 2, "")["run_id"])
    assert r7 not in set(data.load_run_grid_raw(str(db_path), q[Q3], 3, "")["run_id"])


def test_run_grid_keeps_an_abandoned_run_inside_the_span_of_the_query(db_path):
    """An abandoned run that planned the query and sits between two attempts is a real gap."""
    built = scenario_latest(db_path, "abandoned")
    r7 = built.run_ids[-1]
    started = iso_z(utc_now() - timedelta(minutes=1))
    full = RunSpec(
        started,
        {text: ok_result(started, Q2_IDS) for text in (Q1, Q2, Q3)},
        trigger="manual",
        n_queries=3,
    )
    r8 = append_run(db_path, full)
    grid = data.load_run_grid_raw(str(db_path), built.query_ids[Q2], 2, "")
    assert list(grid["run_id"])[-2:] == [r7, r8]
    row = grid[grid["run_id"] == r7].iloc[0]
    assert row["run_status"] == "abandoned"
    assert row["q_status"] is None or row["q_status"] is pd.NA or pd.isna(row["q_status"])


def test_run_grid_drops_a_trailing_abandoned_run_after_the_last_attempt(db_path):
    """Nothing proves the query was still being collected after its last attempt (CR-01)."""
    built = scenario_latest(db_path, "abandoned")
    grid = data.load_run_grid_raw(str(db_path), built.query_ids[Q2], 2, "")
    assert built.run_ids[-1] not in set(grid["run_id"])
    assert list(grid["run_id"]) == built.run_ids[:-1]


def test_run_grid_of_a_query_inserted_at_the_top_has_no_false_failure_bands(db_path):
    """CR-01: a new first sheet row gets sheet_order 1 and must not claim the old runs failed."""
    built = scenario_mixed(db_path)
    started = iso_z(utc_now() - timedelta(minutes=1))
    fresh = "новый запрос в начале таблицы"
    spec = RunSpec(
        started,
        {fresh: ok_result(started, (A, B)), Q1: ok_result(started, (A,))},
        trigger="manual",
        n_queries=4,
    )
    new_run = append_run(db_path, spec, (fresh, Q1, Q2, Q3))
    fresh_id = scalar(db_path, "SELECT id FROM queries WHERE text = ?", (fresh,))
    grid = data.load_run_grid_raw(str(db_path), fresh_id, 1, "")
    assert list(grid["run_id"]) == [new_run]
    assert list(grid["q_status"]) == ["ok"]
    # the old queries keep their full history (their span is unchanged)
    old = data.load_run_grid_raw(str(db_path), built.query_ids[Q1], 2, "")
    assert list(old["run_id"]) == [*built.run_ids, new_run]


def test_run_grid_of_a_query_dropped_from_the_sheet_stops_at_its_last_attempt(db_path):
    """CR-01: a removed query keeps its old sheet_order; new runs must not paint bands for it."""
    built = scenario_mixed(db_path)
    started = iso_z(utc_now() - timedelta(minutes=1))
    other, more = "другой запрос", "ещё один запрос"
    spec = RunSpec(
        started,
        {text: ok_result(started, (A, B)) for text in (Q1, Q2, other, more)},
        trigger="manual",
        n_queries=4,
    )
    append_run(db_path, spec, (Q1, Q2, other, more))
    q3 = built.query_ids[Q3]
    assert scalar(db_path, "SELECT sheet_order FROM queries WHERE id = ?", (q3,)) == 3
    grid = data.load_run_grid_raw(str(db_path), q3, 3, "")
    assert list(grid["run_id"]) == built.run_ids
    assert not grid["q_status"].isna().any()


def test_run_grid_leaves_out_a_limited_manual_run(db_path):
    started = iso_z(utc_now() - timedelta(minutes=20))
    limited = RunSpec(started, {Q1: ok_result(started, (A, B))}, trigger="manual", n_queries=1)
    built = build_db(db_path, [limited])
    grid1 = data.load_run_grid_raw(str(db_path), built.query_ids[Q1], 1, "")
    grid2 = data.load_run_grid_raw(str(db_path), built.query_ids[Q2], 2, "")
    assert list(grid1["run_id"]) == built.run_ids
    assert grid2.empty


def test_product_options_one_row_per_product(db_path):
    built = scenario_mixed(db_path)
    options = data.load_product_options_raw(str(db_path), built.query_ids[Q1])
    assert list(options.columns) == [
        "nm_id",
        "n_seen",
        "best_position",
        "worst_position",
        "last_at",
        "last_run_id",
        "last_position",
        "last_promoted",
        "name",
        "brand",
    ]
    assert sorted(options["nm_id"]) == sorted([A, B, C, D, E, F])
    a = options[options["nm_id"] == A].iloc[0]
    assert (a["best_position"], a["worst_position"]) == (1, 2)
    assert (a["last_run_id"], a["last_position"]) == (built.run_ids[-1], 1)
    assert options[options["nm_id"] == F].iloc[0]["last_run_id"] == built.run_ids[4]


def test_product_options_count_a_repeated_product_once(db_path):
    started = iso_z(utc_now() - timedelta(minutes=20))
    result = ok_result(started, (A, B, C, D, E, F, 900000107))
    rows = list(result.observations)
    rows[6] = replace(rows[6], nm_id=A)
    dup = replace(result, observations=tuple(rows))
    built = build_db(db_path, [RunSpec(started, {Q1: dup}, n_queries=3)])
    options = data.load_product_options_raw(str(db_path), built.query_ids[Q1])
    row = options[options["nm_id"] == A]
    assert len(row) == 1
    assert row.iloc[0]["n_seen"] == 2
    assert (row.iloc[0]["best_position"], row.iloc[0]["worst_position"]) == (1, 7)
    assert row.iloc[0]["last_position"] == 1


def test_latest_data_run(db_path):
    built = scenario_mixed(db_path)
    assert data.load_latest_data_run_raw(str(db_path), built.query_ids[Q1]) == built.run_ids[-1]


def test_latest_data_run_is_none_when_everything_was_blocked(db_path):
    built = scenario_all_blocked(db_path)
    assert data.load_latest_data_run_raw(str(db_path), built.query_ids[Q1]) is None


def test_latest_data_run_counts_a_partial_attempt(db_path):
    built = scenario_latest(db_path, "partial")
    q = built.query_ids
    assert data.load_latest_data_run_raw(str(db_path), q[Q2]) == built.run_ids[-1]
    # Q1 failed in r7, so its newest run with data stays r6.
    assert data.load_latest_data_run_raw(str(db_path), q[Q1]) == built.run_ids[5]


def test_latest_data_run_with_only_a_partial_attempt(db_path):
    started = iso_z(utc_now() - timedelta(minutes=20))
    partial = ok_result(
        started, (A,), complete=False, status="partial", http_status=503, error_class="http_5xx"
    )
    built = build_db(db_path, [RunSpec(started, {Q1: partial}, n_queries=3)])
    assert data.load_latest_data_run_raw(str(db_path), built.query_ids[Q1]) == built.run_ids[0]


def test_top_ids_follow_position_order(db_path):
    built = scenario_mixed(db_path)
    q1, r6 = built.query_ids[Q1], built.run_ids[-1]
    assert data.load_top_ids_raw(str(db_path), r6, q1, 5) == (A, B, D, C, E)
    assert data.load_top_ids_raw(str(db_path), r6, q1, 2) == (A, B)


def test_observations_of_selected_products_only(db_path):
    built = scenario_mixed(db_path)
    obs = data.load_observations_raw(str(db_path), built.query_ids[Q1], (B, A), "")
    assert list(obs.columns) == OBS_COLUMNS
    assert set(obs["nm_id"]) == {A, B}
    keys = list(zip(obs["collected_at"], obs["nm_id"], strict=True))
    assert keys == sorted(keys)
    assert len(obs) == 5 + 3  # A in r1, r3, r4, r5, r6; B in r1, r5, r6 (absent r3, r4)


def test_observations_of_nobody_is_an_empty_frame_with_the_columns(db_path):
    built = scenario_mixed(db_path)
    obs = data.load_observations_raw(str(db_path), built.query_ids[Q1], (), "")
    assert obs.empty
    assert list(obs.columns) == OBS_COLUMNS


def test_observations_refuse_too_many_ids(db_path):
    built = scenario_mixed(db_path)
    with pytest.raises(ValueError):
        data.load_observations_raw(
            str(db_path), built.query_ids[Q1], tuple(range(MAX_IN_IDS + 1)), ""
        )


def test_observations_bind_ids_as_parameters():
    sql = data.OBSERVATIONS_SQL_TEMPLATE.format(placeholders="?, ?")
    assert sql.count("?") == 4  # query, two ids, since
    assert "{" not in sql
    hostile = "1); DROP TABLE runs; --"
    assert hostile not in sql


def test_observations_since_cuts_the_history(db_path):
    built = scenario_mixed(db_path)
    since = scalar(db_path, "SELECT started_at FROM runs WHERE id = ?", (built.run_ids[4],))
    obs = data.load_observations_raw(str(db_path), built.query_ids[Q1], (A,), since)
    assert set(obs["run_id"]) == {built.run_ids[4], built.run_ids[5]}


def test_cached_chart_twins_recompute_when_the_data_version_changes(db_path, monkeypatch):
    built = scenario_mixed(db_path)
    q1 = built.query_ids[Q1]
    path = str(db_path)
    calls: list[str] = []
    for name in ("queries", "run_grid", "product_options", "latest_data_run", "top_ids"):
        raw = getattr(data, f"load_{name}_raw")

        def counting(*args, _raw=raw, _name=name):
            calls.append(_name)
            return _raw(*args)

        monkeypatch.setattr(data, f"load_{name}_raw", counting)
    obs_raw = data.load_observations_raw

    def counting_obs(*args):
        calls.append("observations")
        return obs_raw(*args)

    monkeypatch.setattr(data, "load_observations_raw", counting_obs)

    def run_all(ver):
        data.load_queries(path, ver)
        data.load_run_grid(path, ver, q1, 1, "")
        data.load_product_options(path, ver, q1)
        data.load_latest_data_run(path, ver, q1)
        data.load_top_ids(path, ver, built.run_ids[-1], q1, 3)
        data.load_observations(path, ver, q1, (A,), "")

    run_all(data.DataVersion(1, 1, "ok"))
    run_all(data.DataVersion(1, 1, "ok"))
    assert len(calls) == 6
    run_all(data.DataVersion(2, 9, "ok"))
    assert len(calls) == 12


# ------------------------------------------------------------------- attempts and snapshots

ATTEMPT_COLUMNS = [
    "started_at",
    "status",
    "http_status",
    "pages_fetched",
    "items_collected",
    "error_class",
    "duration_ms",
]


def test_query_attempts_of_an_all_blocked_history(db_path):
    built = scenario_all_blocked(db_path)
    frame = data.load_query_attempts_raw(str(db_path), built.query_ids[Q1])
    assert list(frame.columns) == ATTEMPT_COLUMNS
    assert len(frame) == 3
    assert list(frame["status"]) == ["blocked"] * 3
    assert list(frame["http_status"]) == [403] * 3
    assert list(frame["items_collected"]) == [0] * 3
    assert frame["started_at"].is_monotonic_decreasing


def test_query_attempts_limit_keeps_the_newest(db_path):
    built = scenario_mixed(db_path)
    frame = data.load_query_attempts_raw(str(db_path), built.query_ids[Q1], 2)
    assert len(frame) == 2
    assert list(frame["started_at"]) == sorted(
        [
            scalar(str(db_path), "SELECT started_at FROM runs WHERE id = ?", (rid,))
            for rid in built.run_ids[-2:]
        ],
        reverse=True,
    )
    assert data.ATTEMPTS_LIMIT == 10


def test_query_attempts_of_an_unknown_query_are_empty_with_columns(db_path):
    scenario_mixed(db_path)
    frame = data.load_query_attempts_raw(str(db_path), 999)
    assert frame.empty
    assert list(frame.columns) == ATTEMPT_COLUMNS


def test_attempt_and_snapshot_loaders_open_one_connection_and_close_it(db_path, monkeypatch):
    built = scenario_mixed(db_path)
    opened: list[sqlite3.Connection] = []
    real = data.connect_ro

    def spy(path):
        con = real(path)
        opened.append(con)
        return con

    monkeypatch.setattr(data, "connect_ro", spy)
    path, q1 = str(db_path), built.query_ids[Q1]
    for call in (
        lambda: data.load_query_attempts_raw(path, q1),
        lambda: data.load_snapshot_runs_raw(path, q1),
        lambda: data.load_snapshot_raw(path, built.run_ids[0], q1),
    ):
        opened.clear()
        call()
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError):
            opened[0].execute("SELECT 1")


SNAPSHOT_COLUMNS = [
    "position",
    "is_promoted",
    "nm_id",
    "name",
    "brand",
    "price_rub",
    "rating",
    "feedbacks",
    "delivery_days",
    "seller_name",
]


def test_snapshot_runs_are_the_runs_with_stored_observations_newest_first(db_path):
    built = scenario_mixed(db_path)
    frame = data.load_snapshot_runs_raw(str(db_path), built.query_ids[Q1])
    r1, r2, r3, r4, r5, r6 = built.run_ids
    assert list(frame.columns) == ["run_id", "started_at", "status", "items_collected"]
    assert list(frame["run_id"]) == [r6, r5, r4, r3, r1]
    assert list(frame["status"]) == ["ok", "ok", "partial", "ok", "ok"]
    assert data.SNAPSHOT_RUNS_LIMIT == 500


def test_snapshot_of_a_run_is_in_position_order(db_path):
    built = scenario_mixed(db_path)
    q1 = built.query_ids[Q1]
    frame = data.load_snapshot_raw(str(db_path), built.run_ids[-1], q1)
    assert list(frame.columns) == SNAPSHOT_COLUMNS
    assert list(frame["nm_id"]) == [NM[k] for k in "ABDCE"]
    assert list(frame["position"]) == [1, 2, 3, 4, 5]
    partial = data.load_snapshot_raw(str(db_path), built.run_ids[3], q1)
    assert list(partial["nm_id"]) == [NM["C"], NM["A"]]


def test_snapshot_of_a_blocked_history_is_empty_with_the_contract_columns(db_path):
    built = scenario_all_blocked(db_path)
    q1 = built.query_ids[Q1]
    assert data.load_snapshot_runs_raw(str(db_path), q1).empty
    snap = data.load_snapshot_raw(str(db_path), built.run_ids[0], q1)
    assert snap.empty
    assert list(snap.columns) == SNAPSHOT_COLUMNS


def test_snapshot_never_mixes_other_queries(db_path):
    built = scenario_mixed(db_path)
    frame = data.load_snapshot_raw(str(db_path), built.run_ids[-1], built.query_ids[Q2])
    assert set(frame["nm_id"]) == set(Q2_IDS)


# ------------------------------------------------------------------------ status matrix


def test_status_matrix_of_the_mixed_history(db_path):
    built = scenario_mixed(db_path)
    runs, matrix = data.load_status_matrix_raw(str(db_path))
    assert list(runs.columns) == ["run_id", "started_at", "status"]
    assert list(runs["run_id"]) == built.run_ids[::-1]
    assert list(matrix.columns) == [
        "run_id",
        "query_id",
        "status",
        "items_collected",
        "is_complete",
        "http_status",
        "error_class",
    ]
    assert len(matrix) == 18
    assert data.HEATMAP_RUNS == 48


@pytest.mark.parametrize("final", ["running", "abandoned"])
def test_status_matrix_leaves_out_unfinished_runs(db_path, final):
    built = scenario_latest(db_path, final)
    runs, matrix = data.load_status_matrix_raw(str(db_path))
    r7 = built.run_ids[-1]
    assert len(runs) == 6
    assert r7 not in set(runs["run_id"])
    assert r7 not in set(matrix["run_id"])


def test_status_matrix_keeps_a_limited_manual_run_with_its_one_row(db_path):
    started = iso_z(utc_now() - timedelta(minutes=20))
    limited = RunSpec(started, {Q1: ok_result(started, (NM["A"],))}, trigger="manual", n_queries=1)
    built = build_db(db_path, [limited])
    runs, matrix = data.load_status_matrix_raw(str(db_path))
    assert list(runs["run_id"]) == built.run_ids
    assert len(matrix) == 1


def test_status_matrix_n_runs_caps_the_newest_runs(db_path):
    built = scenario_mixed(db_path)
    runs, matrix = data.load_status_matrix_raw(str(db_path), n_runs=2)
    assert list(runs["run_id"]) == built.run_ids[:-3:-1]
    assert len(matrix) == 6
    assert set(matrix["run_id"]) == set(built.run_ids[-2:])


def test_status_matrix_sql_excludes_unfinished_runs_in_both_statements():
    for sql in (data.HEATMAP_RUNS_SQL, data.STATUS_MATRIX_SQL):
        assert "NOT IN ('running', 'abandoned')" in sql


def test_status_matrix_of_an_empty_database_has_the_columns(db_path):
    init_db(db_path)
    runs, matrix = data.load_status_matrix_raw(str(db_path))
    assert runs.empty and matrix.empty
    assert list(runs.columns) == ["run_id", "started_at", "status"]
    assert "query_id" in matrix.columns


def test_status_matrix_opens_one_connection_and_closes_it(db_path, monkeypatch):
    scenario_mixed(db_path)
    opened: list[sqlite3.Connection] = []
    real = data.connect_ro

    def spy(path):
        con = real(path)
        opened.append(con)
        return con

    monkeypatch.setattr(data, "connect_ro", spy)
    data.load_status_matrix_raw(str(db_path))
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_status_matrix_twin_is_keyed_on_the_data_version(db_path, monkeypatch):
    scenario_mixed(db_path)
    calls: list[int] = []
    raw = data.load_status_matrix_raw

    def counting(path, n_runs=data.HEATMAP_RUNS):
        calls.append(n_runs)
        return raw(path, n_runs)

    monkeypatch.setattr(data, "load_status_matrix_raw", counting)
    path = str(db_path)
    data.load_status_matrix(path, data.DataVersion(1, 1, "ok"))
    data.load_status_matrix(path, data.DataVersion(1, 1, "ok"))
    assert len(calls) == 1
    data.load_status_matrix(path, data.DataVersion(2, 9, "ok"))
    assert len(calls) == 2
