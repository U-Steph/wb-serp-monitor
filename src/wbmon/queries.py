"""Query list sources. Query wording is never stripped or normalised (byte-for-byte, QRY-02)."""

from __future__ import annotations

import csv
import io
import time
from importlib import resources
from typing import TYPE_CHECKING

import httpx

from wbmon.models import QuerySet
from wbmon.wb_client import NoCookiesPolicy, read_capped

if TYPE_CHECKING:
    from wbmon.config import Settings

BUNDLED_RESOURCE = "data/queries_bundled.txt"
SHEET_MIN_ROWS = 50  # a sheet outside 50..500 queries is treated as broken, not as the truth
SHEET_MAX_ROWS = 500
SHEET_MAX_BYTES = 1_048_576
SHEET_TOTAL_SECONDS = 30.0  # wall-clock cap for the whole export download


def load_bundled() -> tuple[str, ...]:
    """Return the 70 bundled queries, one per line, exactly as stored in the package data."""
    raw = resources.files("wbmon").joinpath(BUNDLED_RESOURCE).read_bytes()
    return split_bundled(raw.decode("utf-8"))


def split_bundled(text: str) -> tuple[str, ...]:
    """Split the bundled file into queries, accepting LF or CRLF line endings.

    Only the line terminator is removed: a ``\\r`` before ``\\n`` belongs to a Windows checkout,
    not to the query. Everything else stays byte-for-byte (QRY-02).
    """
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return tuple(line.removesuffix("\r") for line in lines)


def parse_csv(text: str) -> tuple[str, ...]:
    """Return the first-column cells of a headerless CSV as query texts.

    Blank and whitespace-only cells are skipped and duplicates are dropped (first one wins).
    Kept cells are never stripped or normalised: the wording is the data (QRY-02).
    """
    seen: set[str] = set()
    queries: list[str] = []
    for row in csv.reader(io.StringIO(text, newline="")):
        if not row:
            continue
        cell = row[0]
        if not cell.strip() or cell in seen:
            continue
        seen.add(cell)
        queries.append(cell)
    return tuple(queries)


def make_sheet_client(
    settings: Settings, *, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    """Build the short-lived client for the Google Sheet export (separate from the WB client).

    Redirects are followed here only (docs.google.com answers with a redirect to
    googleusercontent.com); cookies are rejected and the environment is ignored.
    """
    client = httpx.Client(
        headers={"User-Agent": settings.user_agent, "Accept": "text/csv"},
        timeout=httpx.Timeout(15.0, connect=10.0),
        follow_redirects=True,
        max_redirects=5,
        trust_env=False,
        transport=transport,
    )
    client.cookies.jar.set_policy(NoCookiesPolicy())
    return client


def _fallback(reason: str) -> QuerySet:
    return QuerySet(load_bundled(), "bundled", reason)


def load_queries(settings: Settings, *, transport: httpx.BaseTransport | None = None) -> QuerySet:
    """Return the queries for a run: the Google Sheet when plausible, else the bundled copy.

    Never raises: a sheet problem must not abort a run. The fallback reason is returned in
    ``QuerySet.reason`` (``http_<status>``, ``too_large``, ``decode_error``, ``csv_error``,
    ``implausible_count=<n>`` or ``fetch_failed:<ExceptionClass>``).
    """
    try:
        with (
            make_sheet_client(settings, transport=transport) as client,
            client.stream("GET", settings.sheet_csv_url) as response,
        ):
            if response.status_code != 200:
                return _fallback(f"http_{response.status_code}")
            content = read_capped(
                response,
                max_bytes=SHEET_MAX_BYTES,
                max_seconds=SHEET_TOTAL_SECONDS,
                monotonic=time.monotonic,
                started=time.monotonic(),
            )
        if len(content) > SHEET_MAX_BYTES:
            return _fallback("too_large")
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return _fallback("decode_error")
        try:
            texts = parse_csv(text)
        except csv.Error:
            return _fallback("csv_error")
        if not SHEET_MIN_ROWS <= len(texts) <= SHEET_MAX_ROWS:
            return _fallback(f"implausible_count={len(texts)}")
        return QuerySet(texts, "sheet", None)
    except Exception as exc:  # network, TLS, redirect loops, anything: fall back and log why
        return _fallback(f"fetch_failed:{type(exc).__name__}")
