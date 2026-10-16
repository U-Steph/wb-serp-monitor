"""One end-to-end collection run: queries -> WB pages -> append-only SQLite rows."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from wbmon import clock
from wbmon.collector import collect_query
from wbmon.config import Settings
from wbmon.log import log_event
from wbmon.queries import load_queries
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer
from wbmon.wb_client import WbClient, make_http_client


def run_once(
    settings: Settings,
    *,
    limit_queries: int | None = None,
    trigger: str = "manual",
    slot_utc: str | None = None,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = clock.utc_now,
    monotonic: Callable[[], float] = time.monotonic,
) -> int | None:
    """Perform one run and return its run id.

    Skeleton slice: queries are processed sequentially with one atomic write each. Isolation of
    unexpected failures, the circuit breaker, the deadline and locking arrive in later slices.
    """
    init_db(settings.db_path)
    query_set = load_queries(settings, transport=transport)
    log_event(
        "queries_loaded",
        source=query_set.source,
        count=len(query_set.texts),
        reason=query_set.reason,
    )
    texts = query_set.texts[:limit_queries] if limit_queries else query_set.texts
    tz = settings.tzinfo()
    with (
        contextlib.closing(connect_rw(settings.db_path)) as con,
        make_http_client(settings, transport=transport) as http,
    ):
        writer = Writer(con)
        query_ids = writer.upsert_queries(query_set.texts, clock.iso_z(now()))
        run_id = writer.start_run(
            trigger=trigger,
            started_at=clock.iso_z(now()),
            query_source=query_set.source,
            params_json=settings.params_json(),
            top_n=settings.top_n,
            n_queries=len(texts),
            slot_utc=slot_utc,
        )
        log_event(
            "run_start",
            run_id=run_id,
            trigger=trigger,
            source=query_set.source,
            queries=len(texts),
            top_n=settings.top_n,
        )
        client = WbClient(http, settings, sleep=sleep, monotonic=monotonic, now=now)
        try:
            for text in texts:
                result = collect_query(
                    client, text, top_n=settings.top_n, tz=tz, now=now, monotonic=monotonic
                )
                writer.record_query(run_id, query_ids[text], result)
                log_event(
                    "query_done",
                    run_id=run_id,
                    query=text,
                    status=result.status,
                    error_class=result.error_class,
                    http=result.http_status,
                    pages=result.pages_fetched,
                    items=result.items_collected,
                    ms=result.duration_ms,
                )
        except BaseException as exc:
            writer.finish_run(run_id, clock.iso_z(now()), error=f"{type(exc).__name__}: {exc}")
            raise
        status = writer.finish_run(run_id, clock.iso_z(now()))
        log_event("run_done", run_id=run_id, status=status)
    return run_id
