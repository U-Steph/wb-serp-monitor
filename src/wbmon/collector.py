"""Collect one query into a :class:`QueryResult` (never writes to the database)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, tzinfo

from wbmon import clock
from wbmon.classify import classify_page
from wbmon.models import QueryResult
from wbmon.normalize import extract_normquery, normalize_product
from wbmon.wb_client import WbClient


def collect_query(
    client: WbClient,
    query: str,
    *,
    top_n: int,
    tz: tzinfo,
    now: Callable[[], datetime] = clock.utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> QueryResult:
    """Fetch page 1 of ``query`` and normalise up to ``top_n`` products in SERP order.

    The page is classified before anything is parsed: blocked / rate_limited / error pages are
    recorded with their error class and a diagnostic message and carry no observations.
    """
    started_at = clock.iso_z(now())
    started = monotonic()
    fetch = client.fetch_page(query, 1)
    verdict = classify_page(fetch)
    if verdict.outcome != "ok" or verdict.products is None:
        return QueryResult(
            status=verdict.outcome if verdict.outcome != "end" else "error",
            started_at=started_at,
            duration_ms=int((monotonic() - started) * 1000),
            error_class=verdict.error_class,
            http_status=fetch.status_code,
            pages_fetched=0,
            error_message=verdict.message,
        )
    products = verdict.products
    rows = []
    for position, raw in enumerate(products[:top_n], start=1):
        row = normalize_product(raw, position=position, collected_at=fetch.received_at, tz=tz)
        if row is not None:
            rows.append(row)
    duration_ms = int((monotonic() - started) * 1000)
    if not rows:
        return QueryResult(
            status="error",
            started_at=started_at,
            duration_ms=duration_ms,
            error_class="schema_drift",
            http_status=fetch.status_code,
            pages_fetched=0,
            error_message="no product with an integer id",
        )
    return QueryResult(
        status="ok",
        started_at=started_at,
        duration_ms=duration_ms,
        observations=tuple(rows),
        http_status=fetch.status_code,
        pages_fetched=1,
        is_complete=len(products) >= top_n,
        normquery=extract_normquery(verdict.payload),
    )
