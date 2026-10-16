"""Command line interface: ``wbmon init-db``, ``wbmon collect`` and ``wbmon probe``.

Exit codes: 0 success, 2 usage/config error, 1 crash.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from wbmon.classify import classify_page
from wbmon.config import ConfigError, Settings
from wbmon.log import configure_logging, log_event
from wbmon.normalize import extract_normquery, payload_shape
from wbmon.orchestrator import run_once
from wbmon.queries import load_bundled
from wbmon.storage.db import connect_rw
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


def main(argv: Sequence[str] | None = None, *, _transport=None, _sleep=None) -> int:
    """Run the CLI. ``_transport`` and ``_sleep`` are test seams for offline runs."""
    _line_buffer_stdout()
    try:
        args = build_parser().parse_args(argv)
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
                transport=_transport,
                sleep=_sleep or time.sleep,
            )
            if run_id is not None:
                with contextlib.closing(connect_rw(settings.db_path)) as con:
                    summary = run_summary(con, run_id)
                log_event("run_summary", **(summary or {"run_id": run_id, "status": "missing"}))
            return 0
        raise ConfigError(f"unknown command {args.command!r}")
    except ConfigError as exc:
        print(f"wbmon: configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # last-resort handler: log and exit non-zero
        log_event("crash", level=logging.ERROR, error=f"{type(exc).__name__}: {exc}")
        return 1
