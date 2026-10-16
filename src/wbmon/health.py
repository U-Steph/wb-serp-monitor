"""Scheduler heartbeat file and the ``wbmon healthcheck`` verdict.

The scheduler swaps ``<db_dir>/scheduler_heartbeat.json`` in atomically (tmp file in the same
directory, then an atomic rename) so a reader sees the old or the new file, never a torn one.
``wbmon healthcheck`` (Docker, operators) and the Phase 3 UI read that file. The verdict is
about scheduler liveness only: Wildberries reachability and run statuses never change it, so a
history of ``blocked`` runs is healthy.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from wbmon import clock
from wbmon.storage import reader
from wbmon.storage.db import connect_ro

HEARTBEAT_FILENAME = "scheduler_heartbeat.json"
DEFAULT_MAX_AGE_S = 180.0
MAX_FUTURE_SKEW_S = 60.0
HEARTBEAT_KEYS = ("ts", "pid", "next_slot", "last_slot", "last_exit", "version")


def heartbeat_path(db_path: str | Path) -> Path:
    """Heartbeat file location: next to the database file."""
    return Path(db_path).parent / HEARTBEAT_FILENAME


def write_heartbeat(path: Path, payload: Mapping[str, object]) -> None:
    """Write the heartbeat atomically: tmp file in the SAME directory, then an atomic rename.

    The temporary file must live on the same filesystem as the target, otherwise the rename is
    not atomic. No fsync: a lost or torn beat only means "unhealthy until the next beat".
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def check_heartbeat(
    path: Path, now: datetime, max_age_s: float = DEFAULT_MAX_AGE_S
) -> tuple[bool, str]:
    """Return ``(healthy, message)`` for the heartbeat file at ``path``."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, f"heartbeat file missing: {path}"
    except (OSError, ValueError) as exc:
        return False, f"heartbeat unreadable: {type(exc).__name__}"
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("heartbeat is not a JSON object")
        ts = clock.parse_iso_z(data["ts"])
    except (ValueError, KeyError, TypeError):
        return False, "heartbeat is not valid JSON with an ISO-Z 'ts'"
    age = (now - ts).total_seconds()
    if age < -MAX_FUTURE_SKEW_S:
        return False, f"heartbeat timestamp is {-age:.0f}s in the future"
    if age > max_age_s:
        return False, f"heartbeat is stale: age_s={age:.0f} max_s={max_age_s:g}"
    return True, (
        f"healthy age_s={max(age, 0):.0f} pid={data.get('pid')} "
        f"last_slot={data.get('last_slot')} last_exit={data.get('last_exit')}"
    )


def latest_run_info(db_path: str | Path) -> str:
    """Best-effort ``status@started_at`` of the newest run; information only, never raises.

    ``no_db`` when the file does not exist (it is not created), ``none`` for an empty history,
    ``unknown`` when the database cannot be read.
    """
    path = Path(db_path)
    try:
        if not path.exists():
            return "no_db"
        with contextlib.closing(connect_ro(path)) as con:
            rows = reader.run_log(con, 1)
    except (sqlite3.Error, OSError, ValueError):
        return "unknown"
    if not rows:
        return "none"
    return f"{rows[0]['status']}@{rows[0]['started_at']}"


def run_healthcheck(
    db_path: str | Path,
    *,
    max_age_s: float = DEFAULT_MAX_AGE_S,
    now: Callable[[], datetime] = clock.utc_now,
) -> int:
    """Print one verdict line and return 0 (healthy) or 1 (anything else)."""
    ok, message = check_heartbeat(heartbeat_path(db_path), now(), max_age_s)
    info = latest_run_info(db_path)
    if ok:
        print(f"{message} last_run={info}", flush=True)
    else:
        print(f"unhealthy: {message} last_run={info}", flush=True)
    return 0 if ok else 1
