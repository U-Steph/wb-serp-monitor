"""Query source: Google Sheet CSV, plausibility fallback to the bundled copy, byte-exact wording."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import unicodedata
from urllib.parse import unquote_to_bytes

import httpx
import pytest

from factories import (
    SRC_DIR,
    FakeWB,
    RecordingSleep,
    build_wb_client,
    json_response,
    make_page,
    make_settings,
    sheet_csv_bytes,
)

CSV_HEADERS = {"content-type": "text/csv; charset=utf-8"}

PROBE_STRINGS = [
    "постельное бельё",
    "комплект 160х200",  # Cyrillic х
    "комплект 160x200",  # Latin x
    "100% хлопок",
    "наволочки 70*70",
    "одеяло, 2 спальное",
    "a&b=c?d#e",
    "  двойной  пробел ",
    "бел" + "ь" + "е" + "̈",  # NFD form of "бельё"
    "+плюс",
    "dr. hygge",
    "пастельный комплект 2-спальный",
]


def bundled() -> tuple[str, ...]:
    from wbmon.queries import load_bundled

    return load_bundled()


def csv_response(texts, **kwargs) -> httpx.Response:
    return httpx.Response(200, content=sheet_csv_bytes(texts, **kwargs), headers=CSV_HEADERS)


def load(sheet, **settings_overrides):
    """Run load_queries against a scripted sheet; return (QuerySet, FakeWB)."""
    from wbmon.queries import load_queries

    fake = FakeWB(sheet=sheet)
    result = load_queries(make_settings(**settings_overrides), transport=httpx.MockTransport(fake))
    return result, fake


def numbered(n: int) -> list[str]:
    return [f"тест запрос {i}" for i in range(n)]


# --------------------------------------------------------------------------- parse_csv


def test_parse_csv_handles_bom_crlf_quotes_blanks_duplicates_and_extra_columns():
    from wbmon.queries import parse_csv

    raw = (
        "﻿"
        "постельное белье 2 спальное,lost\r\n"
        '"одеяло, 2 спальное"\r\n'
        "\r\n"
        "   \r\n"
        " с пробелами внутри ,x\r\n"
        "постельное белье 2 спальное\r\n"
        "last\r\n"
    )
    # the BOM is removed by decoding with utf-8-sig, exactly as load_queries does
    text = raw.encode("utf-8").decode("utf-8-sig")
    assert parse_csv(text) == (
        "постельное белье 2 спальное",
        "одеяло, 2 спальное",
        " с пробелами внутри ",
        "last",
    )


def test_parse_csv_does_not_normalise_anything():
    from wbmon.queries import parse_csv

    nfd = "бельё".replace("ё", "ё")
    texts = ["бельё", nfd, "160х200", "160x200", "A", "a"]
    parsed = parse_csv("\n".join(texts) + "\n")
    assert parsed == tuple(texts)
    assert len(set(parsed)) == 6


def test_parse_csv_empty_input():
    from wbmon.queries import parse_csv

    assert parse_csv("") == ()
    assert parse_csv("\n\n  \n") == ()


# --------------------------------------------------------------------------- sheet source


def test_plausible_sheet_equal_to_bundled_is_used():
    texts = bundled()
    assert len(texts) == 70

    result, fake = load([csv_response(texts)])

    assert result.source == "sheet"
    assert result.reason is None
    assert result.texts == texts
    assert len(fake.sheet_requests()) == 1
    assert fake.wb_requests() == []


def test_sheet_wins_when_plausible_and_different():
    texts = numbered(60)

    result, _ = load([csv_response(texts)])

    assert (result.source, result.texts) == ("sheet", tuple(texts))


def test_sheet_request_identity_and_url():
    settings = make_settings()
    _, fake = load([csv_response(numbered(60))])

    (request,) = fake.sheet_requests()
    assert str(request.url) == settings.sheet_csv_url
    assert request.headers["user-agent"] == settings.user_agent
    assert "cookie" not in request.headers


def test_redirect_chain_to_googleusercontent_is_followed():
    redirect = httpx.Response(
        307, headers={"location": "https://doc-0c-test.googleusercontent.com/export.csv"}
    )

    result, fake = load([redirect, csv_response(numbered(60))])

    assert result.source == "sheet"
    assert len(fake.sheet_requests()) == 2
    assert fake.sheet_requests()[1].url.host == "doc-0c-test.googleusercontent.com"


def test_too_many_redirects_falls_back():
    hop = httpx.Response(307, headers={"location": "https://docs.google.com/hop"})

    result, _ = load([hop] * 6 + [csv_response(numbered(60))])

    assert result.source == "bundled"
    assert result.reason == "fetch_failed:TooManyRedirects"
    assert result.texts == bundled()


def _fallback_cases():
    oversized = httpx.Response(200, content=b"a\n" * 600_000, headers=CSV_HEADERS)
    return [
        ("http_500", [httpx.Response(500)], "http_500"),
        ("unscripted-404", [], "http_404"),
        ("connect-error", [httpx.ConnectError("boom")], "fetch_failed:ConnectError"),
        ("timeout", [httpx.ReadTimeout("slow")], "fetch_failed:ReadTimeout"),
        ("49-rows", [csv_response(numbered(49))], "implausible_count=49"),
        ("501-rows", [csv_response(numbered(501))], "implausible_count=501"),
        ("empty", [httpx.Response(200, content=b"", headers=CSV_HEADERS)], "implausible_count=0"),
        ("too-large", [oversized], "too_large"),
        (
            "bad-utf8",
            [httpx.Response(200, content=b"\xff\xfe\x00bad", headers=CSV_HEADERS)],
            "decode_error",
        ),
        (
            "dupes-collapse-below-minimum",
            [csv_response(["same"] * 80)],
            "implausible_count=1",
        ),
    ]


@pytest.mark.parametrize(
    ("sheet", "reason"),
    [pytest.param(sheet, reason, id=name) for name, sheet, reason in _fallback_cases()],
)
def test_fallback_to_bundled_with_reason(sheet, reason):
    result, _ = load(sheet)

    assert result.source == "bundled"
    assert result.reason == reason
    assert result.texts == bundled()
    assert len(result.texts) == 70


def test_boundary_row_counts_are_accepted():
    for n in (50, 500):
        result, _ = load([csv_response(numbered(n))])
        assert (result.source, len(result.texts)) == ("sheet", n)


def test_load_queries_never_raises_on_unexpected_errors():
    result, _ = load([RuntimeError("weird")])
    assert (result.source, result.reason) == ("bundled", "fetch_failed:RuntimeError")


def test_sheet_client_configuration():
    from wbmon.queries import make_sheet_client

    settings = make_settings()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                307,
                headers={"location": "https://doc-0c-test.googleusercontent.com/x.csv"}
                | {"set-cookie": "NID=abc; Path=/"},
            )
        return httpx.Response(200, content=b"x\n")

    with make_sheet_client(settings, transport=httpx.MockTransport(handler)) as client:
        assert client.follow_redirects is True
        assert client.max_redirects == 5
        assert client.trust_env is False
        assert client.headers["user-agent"] == settings.user_agent
        client.get(settings.sheet_csv_url)
        assert len(list(client.cookies.jar)) == 0

    assert len(seen) == 2
    assert "cookie" not in seen[1].headers


def test_follow_redirects_only_for_the_sheet_client():
    queries_source = (SRC_DIR / "queries.py").read_text(encoding="utf-8")
    client_source = (SRC_DIR / "wb_client.py").read_text(encoding="utf-8")
    assert queries_source.count("follow_redirects=True") == 1
    assert "follow_redirects=True" not in client_source


def test_no_stripping_of_kept_query_text():
    lines = [
        line
        for line in (SRC_DIR / "queries.py").read_text(encoding="utf-8").splitlines()
        if ".strip()" in line
    ]
    assert len(lines) == 1
    assert "cell.strip()" in lines[0]


# --------------------------------------------------------------------------- wire encoding

ALL_QUERIES = list(bundled()) + PROBE_STRINGS


def _raw_query(request: httpx.Request) -> str:
    return request.url.query.decode("ascii")


@pytest.mark.parametrize("query", ALL_QUERIES)
def test_query_reaches_wb_byte_for_byte(query):
    fake = FakeWB({(query, 1): [json_response(make_page(900000001, 1))]})
    client = build_wb_client(fake)

    client.fetch_page(query, 1)

    (request,) = fake.wb_requests()
    assert request.url.params["query"] == query
    raw = _raw_query(request)
    assert "%25D" not in raw  # no double encoding of already-encoded UTF-8
    # the encoded query parameter decodes back to the exact UTF-8 bytes of the query
    pairs = dict(part.split("=", 1) for part in raw.split("&"))
    assert unquote_to_bytes(pairs["query"].replace("+", "%20")) == query.encode("utf-8")


def test_wire_test_covers_all_70_queries_and_12_probe_strings():
    assert len(ALL_QUERIES) >= 82
    assert len(bundled()) == 70
    assert len(PROBE_STRINGS) == 12


def _wire(query: str) -> str:
    fake = FakeWB({(query, 1): [json_response(make_page(900000001, 1))]})
    build_wb_client(fake).fetch_page(query, 1)
    return _raw_query(fake.wb_requests()[0])


def test_specific_percent_encodings():
    assert "%D1%91" in _wire("постельное бельё")
    assert "160%D1%85200" in _wire("комплект 160х200")
    assert "160x200" in _wire("комплект 160x200")
    assert "100%25" in _wire("100% хлопок")
    assert "70%2A70" in _wire("наволочки 70*70")
    assert "%2B" in _wire("+плюс")


def test_nfd_and_nfc_stay_distinct_on_the_wire():
    nfc = "бельё"
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    assert _wire(nfc) != _wire(nfd)


def test_bundled_queries_stay_distinct_after_encoding():
    wires = {_wire(q) for q in bundled()}
    assert len(wires) == 70


# --------------------------------------------------------------------------- end to end


def test_run_once_takes_queries_from_the_sheet_and_records_the_source():
    from wbmon.orchestrator import run_once

    texts = bundled()
    reversed_texts = list(reversed(texts))
    r0 = texts[-1]
    fake = FakeWB(
        {(r0, 1): [json_response(make_page(900000001, 5))]},
        sheet=[csv_response(reversed_texts)],
    )

    run_id = run_once(
        make_settings(top_n=5),
        limit_queries=1,
        transport=httpx.MockTransport(fake),
        sleep=RecordingSleep(),
    )

    assert run_id is not None
    (wb_request,) = fake.wb_requests()
    assert wb_request.url.params["query"] == r0
    with contextlib.closing(sqlite3.connect(os.environ["WBMON_DB_PATH"])) as con:
        assert con.execute("SELECT query_source FROM runs").fetchone() == ("sheet",)
        assert con.execute("SELECT sheet_order FROM queries WHERE text = ?", (r0,)).fetchone() == (
            1,
        )
        order = [row[0] for row in con.execute("SELECT text FROM queries ORDER BY sheet_order")]
        assert order == reversed_texts


def test_run_once_falls_back_to_bundled_when_sheet_is_unreachable():
    from wbmon.orchestrator import run_once

    q1 = bundled()[0]
    fake = FakeWB(
        {(q1, 1): [json_response(make_page(900000001, 5))]}, sheet=[httpx.ConnectError("x")]
    )

    run_once(
        make_settings(top_n=5),
        limit_queries=1,
        transport=httpx.MockTransport(fake),
        sleep=RecordingSleep(),
    )

    with contextlib.closing(sqlite3.connect(os.environ["WBMON_DB_PATH"])) as con:
        assert con.execute("SELECT query_source FROM runs").fetchone() == ("bundled",)


def test_oversized_sheet_is_cut_off_while_streaming():
    """WR-02: the cap must stop the download, not just reject a fully buffered body."""
    from wbmon.queries import SHEET_MAX_BYTES

    chunks_read = 0

    def endless():
        nonlocal chunks_read
        for _ in range(100_000):
            chunks_read += 1
            yield b"a\n" * 50_000  # 100 kB per chunk

    result, _ = load([httpx.Response(200, content=endless(), headers=CSV_HEADERS)])

    assert (result.source, result.reason) == ("bundled", "too_large")
    assert chunks_read <= SHEET_MAX_BYTES // 100_000 + 2


@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example.com/export.csv",
        "http://docs.google.com/export.csv",  # downgrade to plain http
        "https://docs.google.com.evil.example/export.csv",
        "https://evilgoogleusercontent.com/export.csv",  # not a *.googleusercontent.com subdomain
        "//evil.example.com/export.csv",
    ],
)
def test_redirect_leaving_google_is_refused_and_not_followed(location):
    """IN-08: sheet redirects may only stay on docs.google.com / *.googleusercontent.com (https)."""
    redirect = httpx.Response(302, headers={"location": location})

    result, fake = load([redirect, csv_response(numbered(60))])

    assert result.source == "bundled"
    assert result.reason == "fetch_failed:DisallowedRedirect"
    assert result.texts == bundled()
    assert len(fake.requests) == 1  # the second hop was never requested
