"""One end-to-end collection run: queries -> WB pages -> append-only SQLite rows.

A run is safe to start unattended: it holds the non-blocking run lock for its whole duration
(D-A7), recovers crashed runs as ``abandoned`` only while holding it, honours scheduler slots,
isolates every query from the others, stops hammering a blocking WB after
``BREAKER_THRESHOLD`` consecutive refusals (D-A5) and gives up on the remaining queries when the
run deadline passes (D-A6). Queries are always processed in sheet order, one request at a time.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import time
from collections.abc import Callable
from datetime import datetime

import httpx

from wbmon import clock
from wbmon.collector import collect_query
from wbmon.config import Settings
from wbmon.log import log_event
from wbmon.models import QueryResult
from wbmon.queries import load_queries
from wbmon.storage.db import connect_rw
from wbmon.storage.lock import LockBusy, run_lock
from wbmon.storage.migrations import init_db
from wbmon.storage.reader import run_id_for_slot, run_summary
from wbmon.storage.writer import Writer, enforce_result_invariants
from wbmon.wb_client import WbClient, make_http_client

BREAKER_THRESHOLD = 3
MAX_CONSECUTIVE_WRITE_FAILURES = 3  # a single failed write is isolated; a dead database is not
_EXCEPTION_MESSAGE_LIMIT = 200
_RUN_ERROR_LIMIT = 500
_REFUSED = frozenset({"blocked", "rate_limited"})


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
    should_stop: Callable[[], bool] = lambda: False,
) -> int | None:
    """Perform one run and return its run id, or ``None`` when no run was created.

    ``None`` means the run lock was busy (``lock_busy``), the scheduler slot already has a run
    (``slot_exists``) or a stop was requested before the run row existed (``terminated``); all
    are normal, logged outcomes and create nothing.

    ``should_stop`` is polled before the run is created and at the top of every query iteration.
    Once it returns true, every remaining query is recorded as ``error`` / ``terminated`` without
    a request and the run is closed by the unchanged status derivation (D2-A1), so a SIGTERM
    never leaves a run ``running``.
    """
    init_db(settings.db_path)
    with contextlib.ExitStack() as stack:
        try:
            lock_path = stack.enter_context(run_lock(settings.db_path))
        except LockBusy as busy:
            log_event("lock_busy", level=logging.WARNING, lock=str(busy))
            return None
        con = stack.enter_context(contextlib.closing(connect_rw(settings.db_path)))
        writer = Writer(con)
        abandoned = writer.abandon_stale_runs(clock.iso_z(now()))
        if abandoned:
            log_event("abandoned", count=abandoned, lock=str(lock_path))
        if slot_utc is not None:
            existing = run_id_for_slot(con, slot_utc)
            if existing is not None:
                log_event("slot_exists", slot=slot_utc, run_id=existing)
                return None

        if should_stop():
            log_event("terminated", phase="before_start")
            return None
        query_set = load_queries(settings, transport=transport)
        log_event(
            "queries_loaded",
            source=query_set.source,
            count=len(query_set.texts),
            reason=query_set.reason,
        )
        if should_stop():  # flipped while the query list was being fetched
            log_event("terminated", phase="before_start")
            return None
        texts = query_set.texts[:limit_queries] if limit_queries else query_set.texts
        query_ids = writer.upsert_queries(query_set.texts, clock.iso_z(now()))
        try:
            run_id = writer.start_run(
                trigger=trigger,
                started_at=clock.iso_z(now()),
                query_source=query_set.source,
                params_json=settings.params_json(),
                top_n=settings.top_n,
                n_queries=len(texts),
                slot_utc=slot_utc,
            )
        except sqlite3.IntegrityError:
            if slot_utc is None:
                raise
            # lost a slot race that the lock should already prevent: still a normal skip
            log_event("slot_exists", slot=slot_utc, run_id=run_id_for_slot(con, slot_utc))
            return None
        log_event(
            "run_start",
            run_id=run_id,
            trigger=trigger,
            slot=slot_utc,
            source=query_set.source,
            queries=len(texts),
            top_n=settings.top_n,
        )
        try:
            _run_queries(
                settings,
                writer,
                run_id,
                texts,
                query_ids,
                transport=transport,
                sleep=sleep,
                now=now,
                monotonic=monotonic,
                should_stop=should_stop,
            )
            status = writer.finish_run(run_id, clock.iso_z(now()))
        except BaseException as exc:
            _mark_run_failed(writer, run_id, exc, now)
            raise
        summary = run_summary(con, run_id) or {}
        log_event(
            "run_done",
            run_id=run_id,
            status=status,
            ok=summary.get("n_ok"),
            partial=summary.get("n_partial"),
            blocked=summary.get("n_blocked"),
            error=summary.get("n_error"),
            items=summary.get("n_items"),
        )
        with contextlib.suppress(sqlite3.OperationalError):
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return run_id


def _mark_run_failed(
    writer: Writer, run_id: int, exc: BaseException, now: Callable[[], datetime]
) -> None:
    """Close a still-running run as ``error`` with the failure text; never masks ``exc``."""
    with contextlib.suppress(Exception):
        writer.finish_run(
            run_id,
            clock.iso_z(now()),
            error=f"{type(exc).__name__}: {exc}"[:_RUN_ERROR_LIMIT],
        )


def _synthetic_result(
    now: Callable[[], datetime], status: str, error_class: str, message: str
) -> QueryResult:
    """A query that was recorded without sending any request."""
    return QueryResult(
        status=status,  # type: ignore[arg-type]
        started_at=clock.iso_z(now()),
        duration_ms=0,
        error_class=error_class,
        error_message=message,
    )


def _run_queries(
    settings: Settings,
    writer: Writer,
    run_id: int,
    texts: tuple[str, ...] | list[str],
    query_ids: dict[str, int],
    *,
    transport: httpx.BaseTransport | None,
    sleep: Callable[[float], None],
    now: Callable[[], datetime],
    monotonic: Callable[[], float],
    should_stop: Callable[[], bool],
) -> None:
    """Process every query in order: collect, apply stop/breaker/deadline, record, log."""
    tz = settings.tzinfo()
    with make_http_client(settings, transport=transport) as http:
        client = WbClient(http, settings, sleep=sleep, monotonic=monotonic, now=now)
        deadline_at = monotonic() + settings.run_deadline
        consecutive = 0
        breaker_open = False
        deadline_logged = False
        consecutive_write_failures = 0
        terminated_logged = False
        for index, text in enumerate(texts):
            collected = False
            if should_stop():  # checked first: a stop request outranks deadline and breaker
                result = _synthetic_result(
                    now, "error", "terminated", "SIGTERM received; no request sent"
                )
                if not terminated_logged:
                    terminated_logged = True
                    log_event(
                        "terminated",
                        level=logging.WARNING,
                        run_id=run_id,
                        remaining=len(texts) - index,
                    )
            elif monotonic() >= deadline_at:
                result = _synthetic_result(
                    now,
                    "error",
                    "deadline",
                    f"run deadline of {settings.run_deadline:g} s exceeded; no request sent",
                )
                if not deadline_logged:
                    deadline_logged = True
                    log_event(
                        "deadline",
                        level=logging.WARNING,
                        run_id=run_id,
                        after=text,
                        limit_s=settings.run_deadline,
                    )
            elif breaker_open:
                result = _synthetic_result(
                    now,
                    "blocked",
                    "circuit_open",
                    f"circuit open after {BREAKER_THRESHOLD} consecutive blocked/rate_limited "
                    "queries; no request sent",
                )
            else:
                collected = True
                started_at = clock.iso_z(now())
                started = monotonic()
                try:
                    result = collect_query(
                        client, text, top_n=settings.top_n, tz=tz, now=now, monotonic=monotonic
                    )
                except Exception as exc:  # one bad query never aborts the run
                    result = QueryResult(
                        status="error",
                        started_at=started_at,
                        duration_ms=max(0, int((monotonic() - started) * 1000)),
                        error_class="exception",
                        error_message=f"{type(exc).__name__}: {exc}"[:_EXCEPTION_MESSAGE_LIMIT],
                    )
            result = enforce_result_invariants(result)
            if collected:
                consecutive = consecutive + 1 if result.status in _REFUSED else 0
                if consecutive >= BREAKER_THRESHOLD and not breaker_open:
                    breaker_open = True
                    log_event(
                        "breaker_open",
                        level=logging.WARNING,
                        run_id=run_id,
                        after=text,
                        consecutive=consecutive,
                    )
            write_error: Exception | None = None
            try:
                stored = writer.record_query(run_id, query_ids[text], result)
            except Exception as exc:  # even the db_write_failed fallback row could not be written
                write_error = exc
                stored = "error"
            if write_error is None:
                consecutive_write_failures = 0
            else:
                consecutive_write_failures += 1
                log_event(
                    "query_write_failed",
                    level=logging.ERROR,
                    run_id=run_id,
                    query=text,
                    error=f"{type(write_error).__name__}: {write_error}"[:_EXCEPTION_MESSAGE_LIMIT],
                    consecutive=consecutive_write_failures,
                )
                if consecutive_write_failures >= MAX_CONSECUTIVE_WRITE_FAILURES:
                    # the database itself is unusable (locked, full, I/O error): stop the run
                    raise write_error
            failed_write = write_error is not None or stored != result.status
            log_event(
                "query_done",
                run_id=run_id,
                query=text,
                status=stored,
                error_class="db_write_failed" if failed_write else result.error_class,
                http=result.http_status,
                pages=result.pages_fetched,
                items=0 if failed_write else result.items_collected,
                complete=0 if failed_write else int(result.is_complete),
                ms=result.duration_ms,
            )
