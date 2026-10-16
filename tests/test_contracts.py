"""Frozen contracts: schema, settings, clock and log formatting."""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from wbmon.clock import floor_hour, iso_z, parse_iso_z, parse_slot
from wbmon.config import (
    DEFAULT_DEST,
    DEFAULT_SEARCH_URL,
    DEFAULT_SHEET_CSV_URL,
    FIXED_SEARCH_PARAMS,
    ConfigError,
    Settings,
)
from wbmon.log import KeyValueFormatter, fmt_value
from wbmon.models import ERROR_CLASSES, OBSERVATION_COLUMNS
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import SCHEMA_VERSION, init_db, migrate, user_version

TRIGGER_NAMES = {
    "observations_no_update",
    "observations_no_delete",
    "run_queries_no_update",
    "run_queries_no_delete",
    "runs_no_delete",
    "queries_no_delete",
    "runs_finished_immutable",
    "queries_text_immutable",
}


# --- schema --------------------------------------------------------------------------------------


def _names(con: sqlite3.Connection, kind: str) -> set[str]:
    rows = con.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,)).fetchall()
    return {r[0] for r in rows}


def test_schema_matches_models_and_freeze(tmp_path):
    path = tmp_path / "schema.sqlite3"
    assert init_db(path) == SCHEMA_VERSION == 1
    with contextlib.closing(sqlite3.connect(path)) as con:
        columns = [r[1] for r in con.execute("PRAGMA table_info(observations)")]
        assert columns == ["id", "run_id", "query_id", *OBSERVATION_COLUMNS]
        assert TRIGGER_NAMES <= _names(con, "trigger")
        indexes = _names(con, "index")
        assert {"ix_runs_started", "ix_rq_query_run", "ix_obs_query_nm_time"} <= indexes
        assert "ix_obs_query_time" in indexes
        # D-A1: no explicit index duplicating UNIQUE(run_id, query_id, position)
        explicit = [
            r[0]
            for r in con.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='observations'"
                " AND sql IS NOT NULL"
            )
        ]
        assert not any("run_id, query_id, position" in sql for sql in explicit)
        assert user_version(con) == 1
        assert str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"


def test_migrate_twice_is_a_noop(tmp_path):
    path = tmp_path / "twice.sqlite3"
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        before = con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
        assert migrate(con) == 1
        after = con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()
    assert before == after


def test_database_newer_than_code_is_refused(tmp_path):
    path = tmp_path / "future.sqlite3"
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        con.execute("PRAGMA user_version = 99")
        with pytest.raises(RuntimeError, match="newer than this code"):
            migrate(con)


def test_append_only_triggers_and_replace_guard(tmp_path):
    path = tmp_path / "append.sqlite3"
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        con.execute(
            "INSERT INTO queries(text, sheet_order, first_seen_at, last_seen_at)"
            " VALUES ('q', 1, 't', 't')"
        )
        con.execute(
            "INSERT INTO runs(trigger, started_at, status, query_source, params_json, top_n)"
            " VALUES ('manual', 't', 'running', 'bundled', '{}', 5)"
        )
        con.execute(
            "INSERT INTO observations(run_id, query_id, collected_at, position, nm_id)"
            " VALUES (1, 1, 't', 1, 7)"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("UPDATE observations SET nm_id = 8")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            con.execute("DELETE FROM observations")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT OR REPLACE INTO observations(run_id, query_id, collected_at, position,"
                " nm_id) VALUES (1, 1, 't', 1, 9)"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE queries SET text = 'other'")
        con.execute("UPDATE runs SET status = 'ok'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            con.execute("UPDATE runs SET n_items = 3")
        assert con.execute("SELECT nm_id FROM observations").fetchall() == [(7,)]


# --- config --------------------------------------------------------------------------------------


def test_settings_defaults():
    s = Settings.from_env({})
    assert str(s.db_path) == "data/wbmon.sqlite3"
    assert s.top_n == 200
    assert s.dest == DEFAULT_DEST == "-1257786"
    assert (
        s.search_url == DEFAULT_SEARCH_URL == "https://search.wb.ru/exactmatch/ru/common/v18/search"
    )
    assert s.request_delay == 1.5
    assert s.run_deadline == 2400.0
    assert s.sheet_csv_url == DEFAULT_SHEET_CSV_URL
    assert s.timezone == "Europe/Moscow"
    assert s.log_level == "INFO"
    assert re.match(
        r"^Mozilla/5\.0 \(compatible; wbmon/0\.1\.0; \+https://github\.com/U-Steph/wb-serp-monitor\)$",
        s.user_agent,
    )


def test_each_env_var_overrides_its_field():
    s = Settings.from_env(
        {
            "WBMON_DB_PATH": "/tmp/x.sqlite3",
            "WBMON_TOP_N": "50",
            "WBMON_DEST": "-1",
            "WBMON_SEARCH_URL": "https://example.test/search",
            "WBMON_REQUEST_DELAY": "2.5",
            "WBMON_RUN_DEADLINE": "100",
            "WBMON_SHEET_CSV_URL": "https://example.test/sheet.csv",
            "WBMON_USER_AGENT": "agent/1",
            "WBMON_TIMEZONE": "UTC",
            "WBMON_LOG_LEVEL": "DEBUG",
        }
    )
    assert (str(s.db_path), s.top_n, s.dest) == ("/tmp/x.sqlite3", 50, "-1")
    assert s.search_url == "https://example.test/search"
    assert (s.request_delay, s.run_deadline) == (2.5, 100.0)
    assert s.sheet_csv_url == "https://example.test/sheet.csv"
    assert (s.user_agent, s.timezone, s.log_level) == ("agent/1", "UTC", "DEBUG")


def test_empty_string_means_unset():
    assert Settings.from_env({"WBMON_TOP_N": "", "WBMON_DEST": ""}).top_n == 200


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("WBMON_TOP_N", "abc"),
        ("WBMON_TOP_N", "0"),
        ("WBMON_TOP_N", "1001"),
        ("WBMON_SEARCH_URL", "ftp://x"),
        ("WBMON_SHEET_CSV_URL", "file:///etc/passwd"),
        ("WBMON_REQUEST_DELAY", "0.1"),
        ("WBMON_RUN_DEADLINE", "0"),
        ("WBMON_DEST", "moscow"),
        ("WBMON_TIMEZONE", "Mars/Base"),
        ("WBMON_LOG_LEVEL", "LOUD"),
    ],
)
def test_invalid_env_value_raises_config_error_naming_the_variable(var, value):
    with pytest.raises(ConfigError, match=var):
        Settings.from_env({var: value})


def test_with_overrides_validates():
    s = Settings.from_env({})
    assert s.with_overrides(top_n=5).top_n == 5
    with pytest.raises(ConfigError):
        s.with_overrides(top_n=0)


def test_search_params_pass_query_through_unchanged():
    s = Settings.from_env({})
    params = s.search_params("постельное бельё", 2)
    assert params == {
        **FIXED_SEARCH_PARAMS,
        "dest": "-1257786",
        "page": "2",
        "query": "постельное бельё",
    }
    assert all(isinstance(v, str) for v in params.values())


def test_params_json_is_pinned_and_sorted():
    s = Settings.from_env({})
    text = s.params_json()
    data = json.loads(text)
    assert data["endpoint"] == s.search_url
    assert data["params"] == {**FIXED_SEARCH_PARAMS, "dest": "-1257786"}
    assert "query" not in data["params"] and "page" not in data["params"]
    assert (data["top_n"], data["request_delay"], data["timezone"]) == (200, 1.5, "Europe/Moscow")
    assert data["user_agent"] == s.user_agent
    assert text == json.dumps(data, ensure_ascii=False, sort_keys=True)


# --- clock ---------------------------------------------------------------------------------------


def test_iso_z_round_trip():
    moment = datetime(2026, 10, 16, 7, 0, 5, tzinfo=UTC)
    assert iso_z(moment) == "2026-10-16T07:00:05Z"
    assert parse_iso_z("2026-10-16T07:00:05Z") == moment
    assert iso_z(datetime(2026, 10, 16, 10, 0, 0, tzinfo=timezone(timedelta(hours=3)))) == (
        "2026-10-16T07:00:00Z"
    )


def test_iso_z_and_parse_reject_bad_input():
    with pytest.raises(ValueError):
        iso_z(datetime(2026, 10, 16, 7, 0, 0))
    with pytest.raises(ValueError):
        parse_iso_z("2026-10-16 07:00:00")


def test_floor_hour():
    moment = datetime(2026, 10, 16, 7, 42, 9, tzinfo=UTC)
    assert floor_hour(moment) == datetime(2026, 10, 16, 7, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-10-16T12:34:56+03:00", "2026-10-16T09:00:00Z"),
        ("2026-10-16T09:00:00Z", "2026-10-16T09:00:00Z"),
    ],
)
def test_parse_slot(text, expected):
    assert parse_slot(text) == expected


@pytest.mark.parametrize("text", ["2026-10-16T12:00:00", "yesterday"])
def test_parse_slot_rejects_naive_and_garbage(text):
    with pytest.raises(ValueError):
        parse_slot(text)


# --- log -----------------------------------------------------------------------------------------


def test_fmt_value():
    assert fmt_value(None) == "-"
    assert fmt_value("ok") == "ok"
    assert fmt_value(5) == "5"
    assert fmt_value("постельное бельё") == '"постельное бельё"'
    assert "\n" not in fmt_value("a\nb")
    assert fmt_value("a=b") == '"a=b"'
    assert len(fmt_value("x" * 1000)) <= 303


def test_key_value_formatter_line():
    record = logging.LogRecord("wbmon", logging.INFO, __file__, 1, "init_db", None, None)
    record.fields = {"db": "/tmp/a b.sqlite3", "schema_version": 1}
    line = KeyValueFormatter().format(record)
    assert re.match(r"^ts=\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ level=INFO event=init_db ", line)
    assert line.endswith('db="/tmp/a b.sqlite3" schema_version=1')


def test_error_classes_contract():
    assert len(ERROR_CLASSES) == 19
    assert {"circuit_open", "deadline", "db_write_failed"} <= ERROR_CLASSES
