"""Command line interface: ``wbmon init-db``, ``wbmon collect`` and ``wbmon probe``.

Exit codes: 0 success (``collect`` also exits 0 when every query was blocked, when another run
holds the lock, and when the requested slot already has a run), 2 usage/config error, 1 crash.

``collect`` is the entry point Phase 2's scheduler calls:
``python -m wbmon collect --trigger scheduler --slot <ISO-8601 time>``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sqlite3
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path

from wbmon.classify import classify_page
from wbmon.clock import parse_slot
from wbmon.config import ConfigError, Settings
from wbmon.log import configure_logging, log_event
from wbmon.normalize import extract_normquery, payload_shape
from wbmon.orchestrator import run_once
from wbmon.queries import load_bundled
from wbmon.storage.db import connect_ro
from wbmon.storage.migrations import init_db
from wbmon.storage.reader import run_summary
from wbmon.wb_client import WbClient, make_http_client


def _positive_int(text: str) -> int:
    """argparse type: an integer of at least 1."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {value}")
    return value


def _slot(text: str) -> str:
    """argparse type: an aware ISO-8601 time normalised to its hourly slot (``...:00:00Z``)."""
    try:
        return parse_slot(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"not an ISO-8601 time with a UTC offset (e.g. 2026-10-16T12:00:00+03:00): {text!r}"
        ) from None


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with its subcommands."""
    parser = argparse.ArgumentParser(
        prog="wbmon", description="Wildberries SERP monitor (append-only SQLite history)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="create or migrate the SQLite database")
    init.add_argument("--db", help="database path (default: WBMON_DB_PATH)")
    collect = sub.add_parser("collect", help="perform one collection run")
    collect.add_argument("--db", help="database path (default: WBMON_DB_PATH)")
    collect.add_argument("--top-n", type=_positive_int, help="positions per query (default 200)")
    collect.add_argument(
        "--limit-queries", type=_positive_int, help="only the first N queries (smoke runs)"
    )
    collect.add_argument(
        "--trigger",
        choices=("manual", "scheduler"),
        default="manual",
        help="what started the run (default manual); 'scheduler' requires --slot",
    )
    collect.add_argument(
        "--slot",
        type=_slot,
        help="hourly slot as an ISO-8601 time with offset; floored to YYYY-MM-DDTHH:00:00Z. "
        "A slot that already has a run is skipped (exit 0)",
    )
    probe = sub.add_parser(
        "probe", help="send one page-1 request, print the classified outcome, write nothing"
    )
    probe.add_argument("--query", help="query text (default: the first bundled query)")
    return parser


def _line_buffer_stdout() -> None:
    """Make stdout line-buffered UTF-8 so logs show up promptly (Docker) and Cyrillic survives."""
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass


def _probe(settings: Settings, query: str | None, *, transport, sleep) -> int:
    """Send exactly one page-1 request (no retry) and print one JSON line; touches no database."""
    text = query if query is not None else load_bundled()[0]
    with make_http_client(settings, transport=transport) as http:
        fetch = WbClient(http, settings, sleep=sleep).fetch_page(text, 1, retry=False)
    verdict = classify_page(fetch)
    summary = {
        "outcome": verdict.outcome,
        "error_class": verdict.error_class,
        "http_status": fetch.status_code,
        "server": fetch.server,
        "content_type": fetch.content_type,
        "products": len(verdict.products) or None if verdict.products is not None else None,
        "normquery": extract_normquery(verdict.payload) if verdict.payload else None,
        "shape": payload_shape(verdict.payload) if verdict.payload else None,
        "elapsed_ms": fetch.elapsed_ms,
        "attempts": fetch.attempts,
        "endpoint": settings.search_url,
        "params": settings.search_params(text, 1),
        "user_agent": settings.user_agent,
        "message": verdict.message,
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0


def _log_run_summary(db_path: Path, run_id: int) -> None:
    """Log the finished run's counters. Purely informational: a failure here is only a warning.

    The run itself is already recorded (and ``run_done`` already logged), so an unreadable
    database at this point must not turn a successful run into exit code 1.
    """
    try:
        with contextlib.closing(connect_ro(db_path)) as con:
            summary = run_summary(con, run_id)
    except sqlite3.Error as exc:
        log_event(
            "run_summary_unavailable",
            level=logging.WARNING,
            run_id=run_id,
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    log_event("run_summary", **(summary or {"run_id": run_id, "status": "missing"}))


def main(argv: Sequence[str] | None = None, *, _transport=None, _sleep=None) -> int:
    """Run the CLI. ``_transport`` and ``_sleep`` are test seams for offline runs."""
    _line_buffer_stdout()
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.command == "collect" and args.trigger == "scheduler" and args.slot is None:
            parser.error("--trigger scheduler requires --slot")
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    try:
        settings = Settings.from_env()
        if getattr(args, "db", None):
            settings = settings.with_overrides(db_path=Path(args.db))
        if getattr(args, "top_n", None):
            settings = settings.with_overrides(top_n=args.top_n)
    except ConfigError as exc:
        print(f"wbmon: configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    try:
        if args.command == "init-db":
            version = init_db(settings.db_path)
            log_event("init_db", db=str(settings.db_path), schema_version=version)
            return 0
        if args.command == "probe":
            return _probe(settings, args.query, transport=_transport, sleep=_sleep or time.sleep)
        if args.command == "collect":
            run_id = run_once(
                settings,
                limit_queries=args.limit_queries,
                trigger=args.trigger,
                slot_utc=args.slot,
                transport=_transport,
                sleep=_sleep or time.sleep,
            )
            if run_id is not None:
                _log_run_summary(settings.db_path, run_id)
            return 0
        raise ConfigError(f"unknown command {args.command!r}")
    except ConfigError as exc:
        print(f"wbmon: configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # last-resort handler: log and exit non-zero
        log_event("crash", level=logging.ERROR, error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stderr)  # the key=value log line has no room for a stack
        return 1
