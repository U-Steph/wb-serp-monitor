"""Command line interface: ``wbmon init-db``.

Exit codes: 0 success, 2 usage/config error, 1 crash.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from wbmon.config import ConfigError, Settings
from wbmon.log import configure_logging, log_event
from wbmon.storage.migrations import init_db


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser with its subcommands."""
    parser = argparse.ArgumentParser(
        prog="wbmon", description="Wildberries SERP monitor (append-only SQLite history)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init-db", help="create or migrate the SQLite database")
    init.add_argument("--db", help="database path (default: WBMON_DB_PATH)")
    return parser


def _line_buffer_stdout() -> None:
    """Make stdout line-buffered UTF-8 so logs show up promptly (Docker) and Cyrillic survives."""
    try:
        sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError, OSError):
        pass


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
    except ConfigError as exc:
        print(f"wbmon: configuration error: {exc}", file=sys.stderr)
        return 2
    configure_logging(settings.log_level)
    try:
        if args.command == "init-db":
            version = init_db(settings.db_path)
            log_event("init_db", db=str(settings.db_path), schema_version=version)
            return 0
        raise ConfigError(f"unknown command {args.command!r}")
    except ConfigError as exc:
        print(f"wbmon: configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # last-resort handler: log and exit non-zero
        log_event("crash", level=logging.ERROR, error=f"{type(exc).__name__}: {exc}")
        return 1
