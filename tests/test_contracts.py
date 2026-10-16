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
    assert len(ERROR_CLASSES) == 20
    assert {"circuit_open", "deadline", "db_write_failed", "terminated"} <= ERROR_CLASSES


# --- normalizer, bundled loader, purity ----------------------------------------------------------


def test_extract_products_both_envelopes_and_misses():
    from wbmon.normalize import extract_products

    items = [{"id": 1}]
    assert extract_products({"data": {"products": items}}) == items
    assert extract_products({"products": items}) == items
    assert extract_products({"data": {"products": None}}) is None
    assert extract_products({"data": {}}) is None
    assert extract_products([items]) is None
    assert extract_products(None) is None


def test_payload_shape_and_normquery():
    from wbmon.normalize import extract_normquery, payload_shape

    assert payload_shape({"data": {"products": []}}) == "data.products"
    assert payload_shape({"products": []}) == "products"
    assert payload_shape({"data": {"items": []}}) == "unknown"
    assert extract_normquery({"metadata": {"normquery": "a b"}}) == "a b"
    assert extract_normquery({"data": {"metadata": {"normquery": "c"}}}) == "c"
    assert extract_normquery({"metadata": {}}) is None
    assert extract_normquery([]) is None


def test_normalize_product_unknown_stays_null():
    from zoneinfo import ZoneInfo

    from wbmon.normalize import normalize_product

    tz = ZoneInfo("Europe/Moscow")
    assert normalize_product({"name": "no id"}, position=1, collected_at="t", tz=tz) is None
    assert normalize_product({"id": True}, position=1, collected_at="t", tz=tz) is None
    assert normalize_product("junk", position=1, collected_at="t", tz=tz) is None
    row = normalize_product(
        {"id": "900000001", "name": "ТЕСТ", "brand": 5, "log": {"cpm": 0}},
        position=3,
        collected_at="2026-10-16T07:00:00Z",
        tz=tz,
    )
    assert row is not None
    assert (row.nm_id, row.position, row.name, row.brand, row.is_promoted) == (
        900000001,
        3,
        "ТЕСТ",
        None,
        1,
    )
    unmapped = [
        n
        for n in OBSERVATION_COLUMNS
        if n not in {"collected_at", "position", "nm_id", "name", "is_promoted", "ad_cpm"}
    ]
    assert all(getattr(row, name) is None for name in unmapped)
    assert row.ad_cpm == 0
    empty_log = normalize_product(
        {"id": 1, "log": {}}, position=1, collected_at="2026-10-16T07:00:00Z", tz=tz
    )
    assert empty_log is not None and empty_log.is_promoted == 0


def test_load_bundled_matches_resource_lines():
    from importlib import resources

    from wbmon.queries import load_bundled

    raw = resources.files("wbmon").joinpath("data/queries_bundled.txt").read_bytes()
    queries = load_bundled()
    assert len(queries) == 70
    assert queries == tuple(raw.decode("utf-8").split("\n")[:-1])


def test_pure_modules_have_no_io_imports():
    from factories import PURE_FORBIDDEN_IMPORTS, SRC_DIR, module_imports

    for name in ("clock.py", "models.py", "normalize.py"):
        assert not (module_imports(SRC_DIR / name) & PURE_FORBIDDEN_IMPORTS), name


# --- HTTP client hygiene -------------------------------------------------------------------------


def test_http_client_hygiene_and_no_cookie_replay():
    import httpx

    from wbmon.config import Settings
    from wbmon.wb_client import make_http_client

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={}, headers={"set-cookie": "sid=abc; Path=/"})

    client = make_http_client(Settings.from_env({}), transport=httpx.MockTransport(handler))
    assert client.trust_env is False
    assert client.follow_redirects is False
    client.get("https://search.wb.ru/x")
    client.get("https://search.wb.ru/x")
    assert all("cookie" not in r.headers for r in seen)
    assert client.headers["accept"] == "application/json"
    assert client.headers["accept-language"] == "ru-RU,ru;q=0.9"
    client.close()


# --- writer --------------------------------------------------------------------------------------


def _writer(tmp_path):
    from wbmon.storage.writer import Writer

    path = tmp_path / "w.sqlite3"
    init_db(path)
    con = connect_rw(path)
    return con, Writer(con)


def test_writer_run_status_derivation_and_append_only(tmp_path):
    from wbmon.models import QueryResult
    from wbmon.storage.reader import latest_run_id, run_summary
    from wbmon.storage.writer import Writer  # noqa: F401

    con, writer = _writer(tmp_path)
    with contextlib.closing(con):
        ids = writer.upsert_queries(["a", "b"], "2026-10-16T07:00:00Z")
        assert sorted(ids) == ["a", "b"]
        assert latest_run_id(con) is None
        run = writer.start_run(
            trigger="manual",
            started_at="2026-10-16T07:00:00Z",
            query_source="bundled",
            params_json="{}",
            top_n=5,
            n_queries=2,
        )
        blocked = QueryResult(
            status="blocked", started_at="t", duration_ms=1, error_class="http_403"
        )
        writer.record_query(run, ids["a"], blocked)
        writer.record_query(run, ids["b"], blocked)
        with pytest.raises(sqlite3.IntegrityError):
            writer.record_query(run, ids["b"], blocked)  # a query is written once per run
        assert writer.finish_run(run, "2026-10-16T07:01:00Z") == "blocked"
        summary = run_summary(con, run)
        assert summary is not None
        assert (summary["status"], summary["n_blocked"], summary["n_items"]) == ("blocked", 2, 0)
        assert latest_run_id(con) == run
        assert run_summary(con, 999) is None
        with pytest.raises(RuntimeError):
            writer.finish_run(run, "2026-10-16T07:02:00Z")  # finished runs are not reopened


def test_record_query_rejects_rows_on_failed_status(tmp_path):
    from wbmon.models import ObservationRow, QueryResult

    con, writer = _writer(tmp_path)
    with contextlib.closing(con):
        ids = writer.upsert_queries(["a"], "t")
        run = writer.start_run(
            trigger="manual",
            started_at="t",
            query_source="bundled",
            params_json="{}",
            top_n=5,
            n_queries=1,
        )
        row = ObservationRow(
            **{name: None for name in OBSERVATION_COLUMNS}
            | {"collected_at": "t", "position": 1, "nm_id": 1, "is_promoted": 0}
        )
        with pytest.raises(ValueError):
            writer.record_query(
                run,
                ids["a"],
                QueryResult(status="error", started_at="t", duration_ms=0, observations=(row,)),
            )
        assert con.execute("SELECT COUNT(*) FROM run_queries").fetchone()[0] == 0


@pytest.mark.parametrize("text", ["0001-01-01T00:00:00+05:00", "9999-12-31T23:00:00-05:00"])
def test_parse_slot_out_of_range_is_a_value_error(text):
    with pytest.raises(ValueError, match="UTC range"):
        parse_slot(text)


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [(" 5 ", "5"), ("+5", "5"), ("1_0", "10"), ("-1257786", "-1257786"), ("-0", "0")],
)
def test_dest_is_normalised_to_a_canonical_integer_string(raw, canonical):
    """IN-08: int() accepts these, but the raw text used to be sent and stored as given."""
    s = Settings.from_env({"WBMON_DEST": raw})
    assert s.dest == canonical
    assert s.search_params("q", 1)["dest"] == canonical
    assert f'"dest": "{canonical}"' in s.params_json()
    assert s.with_overrides(dest=f" {canonical} ").dest == canonical


def test_non_integer_dest_is_still_rejected():
    with pytest.raises(ConfigError, match="WBMON_DEST"):
        Settings.from_env({"WBMON_DEST": "moscow"})
