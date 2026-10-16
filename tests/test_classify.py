"""Response classification: pure matrix, Retry-After vectors, collector wiring and a CLI slice."""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import unicodedata
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from factories import (
    PURE_FORBIDDEN_IMPORTS,
    SRC_DIR,
    FakeWB,
    RecordingSleep,
    build_wb_client,
    html_response,
    json_response,
    make_page,
    make_settings,
    module_imports,
)

JSON = "application/json; charset=utf-8"
HTML = "text/html; charset=utf-8"


def make_fetch(
    status: int | None = 200,
    body: bytes | str = b"",
    *,
    ctype: str | None = JSON,
    server: str | None = None,
    page: int = 1,
    transport_error: str | None = None,
    attempts: int = 1,
):
    from wbmon.models import PageFetch

    content = body.encode("utf-8") if isinstance(body, str) else body
    return PageFetch(
        query="q",
        page=page,
        status_code=status,
        content_type=ctype,
        server=server,
        retry_after=None,
        content=content,
        received_at="2026-01-01T00:00:00Z",
        elapsed_ms=1,
        attempts=attempts,
        transport_error=transport_error,
    )


def dump(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False)


# --------------------------------------------------------------------------- pure matrix

STATUS_CASES = [
    (dict(status=None, transport_error="timeout"), "error", "timeout"),
    (dict(status=None, transport_error="connection"), "error", "connection"),
    (dict(status=403), "blocked", "http_403"),
    (dict(status=498), "blocked", "http_498"),
    (dict(status=301), "blocked", "redirect"),
    (dict(status=302), "blocked", "redirect"),
    (dict(status=307), "blocked", "redirect"),
    (dict(status=308), "blocked", "redirect"),
    (dict(status=429), "rate_limited", "http_429"),
    (dict(status=500), "error", "http_5xx"),
    (dict(status=502), "error", "http_5xx"),
    (dict(status=503), "error", "http_5xx"),
    (dict(status=400), "error", "http_4xx"),
    (dict(status=401), "error", "http_4xx"),
    (dict(status=404), "error", "http_4xx"),
    (dict(status=204), "error", "http_other"),
]


@pytest.mark.parametrize(("kwargs", "outcome", "error_class"), STATUS_CASES)
def test_status_matrix(kwargs, outcome, error_class):
    from wbmon.classify import classify_page

    verdict = classify_page(make_fetch(**kwargs))
    assert (verdict.outcome, verdict.error_class) == (outcome, error_class)
    assert verdict.products is None
    assert verdict.message


def test_oversized_body_is_body_too_large():
    from wbmon.classify import MAX_BODY_BYTES, classify_page

    verdict = classify_page(make_fetch(200, b"x" * (MAX_BODY_BYTES + 1)))
    assert (verdict.outcome, verdict.error_class) == ("error", "body_too_large")


BODY_CASES = [
    ("html content type", dict(body="<html></html>", ctype=HTML), "blocked", "html_at_200"),
    (
        "html content type with json-looking body",
        dict(body="{}", ctype=HTML),
        "blocked",
        "html_at_200",
    ),
    (
        "html sniffed under json type",
        dict(body="  \n<!DOCTYPE html>", ctype=JSON),
        "blocked",
        "html_at_200",
    ),
    ("html sniffed without type", dict(body="<html>", ctype=None), "blocked", "html_at_200"),
    ("bad json", dict(body="{bad"), "error", "bad_json"),
    ("empty body", dict(body=""), "error", "bad_json"),
    ("json array", dict(body="[1, 2]"), "error", "bad_json"),
    ("json scalar", dict(body="7"), "error", "bad_json"),
    ("non-utf8 bytes", dict(body=b"\xff\xfe\x00{}"), "error", "bad_json"),
    ("data without products", dict(body=dump({"data": {"items": []}})), "error", "schema_drift"),
    ("data.products object", dict(body=dump({"data": {"products": {}}})), "error", "schema_drift"),
    ("top-level products string", dict(body=dump({"products": "x"})), "error", "schema_drift"),
    ("empty object", dict(body="{}"), "error", "schema_drift"),
    ("data.products null", dict(body=dump({"data": {"products": None}})), "error", "products_null"),
    ("products null", dict(body=dump({"products": None})), "error", "products_null"),
    ("page 1 empty list", dict(body=dump({"data": {"products": []}})), "error", "empty_suspect"),
    ("page 1 empty top-level", dict(body=dump({"products": []})), "error", "empty_suspect"),
    ("page 2 empty list", dict(body=dump({"data": {"products": []}}), page=2), "end", None),
    ("page 3 empty top-level", dict(body=dump({"products": []}), page=3), "end", None),
]


@pytest.mark.parametrize(
    ("label", "kwargs", "outcome", "error_class"), BODY_CASES, ids=[c[0] for c in BODY_CASES]
)
def test_body_matrix(label, kwargs, outcome, error_class):
    from wbmon.classify import classify_page

    verdict = classify_page(make_fetch(200, **kwargs))
    assert (verdict.outcome, verdict.error_class) == (outcome, error_class)
    if outcome == "end":
        assert verdict.message is None
    else:
        assert verdict.message


@pytest.mark.parametrize("shape", ["data.products", "products"])
def test_ok_page_keeps_products_and_payload(shape):
    from wbmon.classify import classify_page

    payload = make_page(900000001, 3, shape=shape)
    verdict = classify_page(make_fetch(200, dump(payload)))
    assert verdict.outcome == "ok"
    assert verdict.error_class is None
    assert verdict.message is None
    assert isinstance(verdict.products, tuple)
    assert len(verdict.products) == 3
    assert verdict.payload == payload


def test_bom_prefixed_json_is_accepted():
    from wbmon.classify import classify_page

    body = b"\xef\xbb\xbf" + dump(make_page(900000001, 2)).encode("utf-8")
    assert classify_page(make_fetch(200, body)).outcome == "ok"


# --------------------------------------------------------------------------- messages


def test_message_carries_status_server_ctype_and_body_prefix():
    from wbmon.classify import classify_page

    verdict = classify_page(make_fetch(403, "<html>blocked</html>", ctype=HTML, server="wbaas"))
    assert "http=403" in verdict.message
    assert "server=wbaas" in verdict.message
    assert "ctype=text/html" in verdict.message
    assert "<html>blocked</html>" in verdict.message


def test_message_body_prefix_is_200_chars_without_control_characters():
    from wbmon.classify import describe

    body = "A\nB\r\nC\tD\x00E\x7fF" + "x" * 500
    message = describe(make_fetch(403, body, ctype=HTML))
    body_part = message.split(" body=", 1)[1]
    assert len(body_part) == 200
    assert all(ord(ch) >= 0x20 and ord(ch) != 0x7F for ch in message)
    assert body_part.startswith("A B  C D E F")


def test_message_marks_missing_fields_and_transport_error():
    from wbmon.classify import describe

    message = describe(make_fetch(None, b"", ctype=None, transport_error="timeout", attempts=3))
    assert "http=-" in message
    assert "server=-" in message
    assert "ctype=-" in message
    assert "attempts=3" in message
    assert "transport=timeout" in message


def test_message_tolerates_undecodable_body():
    from wbmon.classify import describe

    assert "body=" in describe(make_fetch(502, b"\xff\xfe", ctype=None))


# --------------------------------------------------------------------------- retry-after

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


def _http_date(delta: float) -> str:
    from email.utils import format_datetime

    return format_datetime(NOW + timedelta(seconds=delta), usegmt=True)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("7", 7.0),
        ("0", 0.0),
        ("-5", 0.0),
        ("120", 60.0),
        ("1.5", 1.5),
        ("inf", 60.0),
        ("nan", None),
        ("abc", None),
        ("", None),
        (None, None),
        (" 7 ", 7.0),
    ],
)
def test_parse_retry_after_vectors(value, expected):
    from wbmon.classify import parse_retry_after

    assert parse_retry_after(value, NOW) == expected


def test_parse_retry_after_http_dates():
    from wbmon.classify import parse_retry_after

    assert parse_retry_after(_http_date(30), NOW) == 30.0
    assert parse_retry_after(_http_date(-3600), NOW) == 0.0
    assert parse_retry_after(_http_date(3600), NOW) == 60.0


def test_parse_retry_after_custom_cap_and_naive_date():
    from wbmon.classify import parse_retry_after

    assert parse_retry_after("120", NOW, cap=10.0) == 10.0
    naive = "Thu, 01 Jan 2026 12:00:20 -0000"
    assert parse_retry_after(naive, NOW) == 20.0


def test_constants():
    from wbmon.classify import DEFAULT_RETRY_AFTER, MAX_BODY_BYTES, RETRY_AFTER_CAP

    assert (MAX_BODY_BYTES, DEFAULT_RETRY_AFTER, RETRY_AFTER_CAP) == (8 * 1024 * 1024, 30.0, 60.0)


def test_classify_module_is_pure():
    imports = module_imports(SRC_DIR / "classify.py")
    assert not imports & PURE_FORBIDDEN_IMPORTS


# --------------------------------------------------------------------------- collector wiring

QUERY = "тестовый запрос"


def run_collect(response: httpx.Response, *, top_n: int = 5):
    from wbmon.collector import collect_query

    fake = FakeWB({(QUERY, 1): [response]})
    settings = make_settings(top_n=top_n)
    client = build_wb_client(fake, settings)
    result = collect_query(client, QUERY, top_n=top_n, tz=settings.tzinfo())
    return result, fake


def test_collect_403_html_is_blocked_with_diagnostics():
    result, fake = run_collect(html_response(403, headers={"server": "wbaas"}))
    assert (result.status, result.error_class, result.http_status) == ("blocked", "http_403", 403)
    assert result.pages_fetched == 0
    assert result.observations == ()
    assert "server=wbaas" in result.error_message
    assert len(fake.wb_requests()) == 1


def test_collect_498_is_blocked():
    result, _ = run_collect(httpx.Response(498, text="x"))
    assert (result.status, result.error_class, result.http_status) == ("blocked", "http_498", 498)


def test_collect_307_is_blocked_and_not_followed():
    response = httpx.Response(307, headers={"location": "https://search.wb.ru/elsewhere"})
    result, fake = run_collect(response)
    assert (result.status, result.error_class, result.http_status) == ("blocked", "redirect", 307)
    assert len(fake.requests) == 1


def test_collect_html_at_200_is_blocked():
    result, _ = run_collect(html_response(200))
    assert (result.status, result.error_class, result.http_status) == (
        "blocked",
        "html_at_200",
        200,
    )
    assert result.observations == ()


def test_collect_404_is_error():
    result, _ = run_collect(httpx.Response(404, text="nope"))
    assert (result.status, result.error_class, result.http_status) == ("error", "http_4xx", 404)


def test_collect_empty_page_one_is_empty_suspect_not_no_results():
    result, _ = run_collect(json_response(make_page(900000001, 0)))
    assert (result.status, result.error_class) == ("error", "empty_suspect")
    assert result.observations == ()


def test_collect_products_null_is_error():
    result, _ = run_collect(json_response({"data": {"products": None}}))
    assert (result.status, result.error_class) == ("error", "products_null")


def test_collect_ok_page():
    result, _ = run_collect(json_response(make_page(900000001, 5)))
    assert result.status == "ok"
    assert result.error_class is None
    assert result.http_status == 200
    assert result.pages_fetched == 1
    assert result.is_complete is True
    assert result.items_collected == 5
    assert [o.position for o in result.observations] == [1, 2, 3, 4, 5]
    assert result.normquery == "тест запрос"


def test_collect_ok_page_larger_than_top_n_is_truncated():
    result, _ = run_collect(json_response(make_page(900000001, 8)), top_n=3)
    assert result.status == "ok"
    assert [o.nm_id for o in result.observations] == [900000001, 900000002, 900000003]


def test_collect_products_without_integer_ids_is_schema_drift():
    payload = make_page(900000001, 3)
    for product in payload["data"]["products"]:
        product["id"] = "not-an-id"
    result, _ = run_collect(json_response(payload), top_n=3)
    assert (result.status, result.error_class) == ("error", "schema_drift")
    assert result.error_message == "no product with an integer id"
    assert result.observations == ()
    assert result.pages_fetched == 1  # the page itself was fetched fine; it held no usable id


def test_collector_uses_the_classifier():
    source = (SRC_DIR / "collector.py").read_text(encoding="utf-8")
    assert "classify_page(" in source


# --------------------------------------------------------------------------- CLI slice


def test_cli_collect_records_blocked_and_ok_attempts():
    from wbmon import cli
    from wbmon.queries import load_bundled

    q1, q2, q3 = load_bundled()[:3]
    fake = FakeWB(
        {
            (q1, 1): [html_response(403, headers={"server": "wbaas"})],
            (q2, 1): [json_response(make_page(900000001, 5))],
            (q3, 1): [html_response(200)],
        }
    )
    code = cli.main(
        ["collect", "--limit-queries", "3", "--top-n", "5"],
        _transport=httpx.MockTransport(fake),
        _sleep=RecordingSleep(),
    )
    assert code == 0
    with contextlib.closing(sqlite3.connect(os.environ["WBMON_DB_PATH"])) as con:
        rows = con.execute(
            "SELECT q.sheet_order, rq.status, rq.error_class, rq.http_status, rq.error_message"
            " FROM run_queries rq JOIN queries q ON q.id = rq.query_id ORDER BY q.sheet_order"
        ).fetchall()
        statuses = [(r[1], r[2]) for r in rows]
        assert statuses == [("blocked", "http_403"), ("ok", None), ("blocked", "html_at_200")]
        assert "server=wbaas" in rows[0][4]
        assert rows[1][4] is None
        obs_queries = con.execute(
            "SELECT DISTINCT q.sheet_order FROM observations o JOIN queries q ON q.id = o.query_id"
        ).fetchall()
        assert obs_queries == [(2,)]
        assert con.execute("SELECT status FROM runs").fetchone()[0] == "partial"


# ------------------------------------------------------- IN-07: remote text is sanitised


def test_sanitize_text_strips_control_format_and_separator_characters():
    from wbmon.classify import sanitize_text

    nasty = "a\x1b[31mb‮c d e\x85f​g\x00h\ud800i"
    cleaned = sanitize_text(nasty, 100)
    assert cleaned == "a [31mb c d e f g h i"
    assert (
        sanitize_text("ТЕСТ ё <b>x</b> [link](http://x)", 100) == "ТЕСТ ё <b>x</b> [link](http://x)"
    )
    assert len(sanitize_text("x" * 1000, 50)) == 50


def test_describe_caps_and_cleans_header_values():
    from wbmon.classify import HEADER_VALUE_CHARS, describe

    message = describe(
        make_fetch(403, "x", ctype="text/html\r\nX-Evil: 1" + "y" * 500, server="wb\x1baas" * 100)
    )
    assert all(unicodedata.category(ch)[0] != "C" for ch in message)
    assert "ctype=text/html  X-Evil: 1" in message
    server_part = message.split(" server=", 1)[1].split(" ctype=", 1)[0]
    assert len(server_part) == HEADER_VALUE_CHARS


def test_writer_sanitises_and_caps_any_stored_error_message(tmp_path):
    import contextlib

    from wbmon.models import QueryResult
    from wbmon.storage.db import connect_rw
    from wbmon.storage.migrations import init_db
    from wbmon.storage.writer import ERROR_MESSAGE_MAX_CHARS, Writer

    path = tmp_path / "msg.sqlite3"
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        writer = Writer(con)
        query_id = writer.upsert_queries(["q"], "2026-10-01T00:00:00Z")["q"]
        run_id = writer.start_run(
            trigger="manual",
            started_at="2026-10-01T00:00:00Z",
            query_source="bundled",
            params_json="{}",
            top_n=5,
            n_queries=1,
        )
        hostile = QueryResult(
            status="error",
            started_at="2026-10-01T00:00:00Z",
            duration_ms=1,
            error_class="exception",
            error_message="line1\nline2\x1b[0m" + "z" * 2000,
        )
        writer.record_query(run_id, query_id, hostile)
        (message,) = con.execute("SELECT error_message FROM run_queries").fetchone()
    assert len(message) == ERROR_MESSAGE_MAX_CHARS
    assert message.startswith("line1 line2 [0mzzz")
