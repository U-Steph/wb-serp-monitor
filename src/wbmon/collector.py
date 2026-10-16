"""Collect one query into a :class:`QueryResult` (never writes to the database)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime, tzinfo

from wbmon import clock
from wbmon.models import PageFetch, QueryResult
from wbmon.normalize import extract_normquery, extract_products, normalize_product
from wbmon.wb_client import WbClient


def _parse_json(fetch: PageFetch) -> object:
    """Decode the body as JSON, returning ``None`` when it is not valid JSON."""
    try:
        return json.loads(fetch.content)
    except ValueError:
        return None


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

    Skeleton slice: a single page, and every unusable page is reported as a plain ``error``
    result; the full classifier and pagination replace this later.
    """
    started_at = clock.iso_z(now())
    started = monotonic()
    fetch = client.fetch_page(query, 1)
    payload = _parse_json(fetch) if fetch.status_code == 200 else None
    products = extract_products(payload)
    rows = []
    if products:
        for position, raw in enumerate(products[:top_n], start=1):
            row = normalize_product(raw, position=position, collected_at=fetch.received_at, tz=tz)
            if row is not None:
                rows.append(row)
    duration_ms = int((monotonic() - started) * 1000)
    if rows and products is not None:
        return QueryResult(
            status="ok",
            started_at=started_at,
            duration_ms=duration_ms,
            observations=tuple(rows),
            http_status=fetch.status_code,
            pages_fetched=1,
            is_complete=len(products) >= top_n,
            normquery=extract_normquery(payload),
        )
    return QueryResult(
        status="error",
        started_at=started_at,
        duration_ms=duration_ms,
        http_status=fetch.status_code,
        pages_fetched=0,
        error_message=f"http={fetch.status_code} transport={fetch.transport_error}",
    )
