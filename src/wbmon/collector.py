"""Collect one query into a :class:`QueryResult` (never writes to the database)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, tzinfo

from wbmon import clock
from wbmon.classify import classify_page
from wbmon.log import log_event
from wbmon.models import PageFetch, PageVerdict, QueryResult
from wbmon.normalize import extract_normquery, normalize_product
from wbmon.pager import Pager
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
    """Fetch pages of ``query`` until the top-``top_n`` is covered and normalise them in SERP order.

    Every page is classified before anything is parsed. A failure on page 1 yields a
    blocked / rate_limited / error result without observations. A failure on a later page keeps
    the rows of the earlier pages and is recorded as ``partial`` with the failing page's error
    class, so a half-collected SERP is never presented as complete.
    """
    started_at = clock.iso_z(now())
    started = monotonic()
    pager = Pager(top_n)
    normquery: str | None = None
    failure: tuple[PageVerdict, PageFetch] | None = None
    last_status: int | None = None
    page = 1
    while not pager.done:
        fetch = client.fetch_page(query, page)
        verdict = classify_page(fetch)
        if verdict.outcome not in ("ok", "end"):
            failure = (verdict, fetch)
            break
        if page == 1:
            normquery = extract_normquery(verdict.payload)
        last_status = fetch.status_code
        pager.add_page(verdict.products or (), received_at=fetch.received_at)
        next_page = pager.next_page
        if next_page is None:
            break
        page = next_page

    def elapsed_ms() -> int:
        return max(0, int((monotonic() - started) * 1000))

    if failure is not None and pager.pages_seen == 0:
        verdict, fetch = failure
        return QueryResult(
            status=verdict.outcome,  # type: ignore[arg-type]
            started_at=started_at,
            duration_ms=elapsed_ms(),
            error_class=verdict.error_class,
            http_status=fetch.status_code,
            pages_fetched=0,
            error_message=verdict.message,
        )

    rows = []
    for slot in pager.slots:
        row = normalize_product(
            slot.raw, position=slot.position, collected_at=slot.received_at, tz=tz
        )
        if row is not None:
            rows.append(row)
    if pager.skipped_no_id:
        log_event("product_skipped", query=query, count=pager.skipped_no_id)
    if not rows and failure is not None:
        # Nothing usable came from the pages that did load, but a later page was refused or
        # failed: report that real verdict (it must feed the circuit breaker), not schema drift.
        verdict, fetch = failure
        return QueryResult(
            status=verdict.outcome,  # type: ignore[arg-type]
            started_at=started_at,
            duration_ms=elapsed_ms(),
            error_class=verdict.error_class,
            http_status=fetch.status_code,
            pages_fetched=pager.pages_seen,
            error_message=verdict.message,
        )
    if not rows:
        return QueryResult(
            status="error",
            started_at=started_at,
            duration_ms=elapsed_ms(),
            error_class="schema_drift",
            http_status=last_status,
            pages_fetched=pager.pages_seen,
            error_message="no product with an integer id",
        )
    common = {
        "started_at": started_at,
        "duration_ms": elapsed_ms(),
        "observations": tuple(rows),
        "pages_fetched": pager.pages_seen,
        "normquery": normquery,
        "ad_slot_mismatch": pager.ad_slot_mismatch,
        "duplicates_dropped": pager.duplicates_dropped,
    }
    if failure is not None:
        verdict, fetch = failure
        return QueryResult(
            status="partial",
            error_class=verdict.error_class,
            http_status=fetch.status_code,
            error_message=verdict.message,
            is_complete=False,
            **common,  # type: ignore[arg-type]
        )
    return QueryResult(
        status="ok",
        http_status=last_status,
        is_complete=pager.is_complete,
        **common,  # type: ignore[arg-type]
    )
