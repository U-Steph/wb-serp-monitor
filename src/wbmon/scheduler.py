"""``wbmon scheduler``: start a fresh ``wbmon collect`` child at every top of the (Moscow) hour.

A small single-threaded supervisor:

* every 1 s tick it derives the current hourly slot from an injected clock and acts only when a
  slot newer than the last handled one begins (so a clock step back never re-runs a slot);
* a slot without a run that is at most 45 minutes old is run (this also covers a late start);
  an older slot is logged ``slot_missed`` and never back-filled;
* the child is supervised with ``Popen`` and a poll loop, so the heartbeat keeps ticking during
  a 5-40 minute run, a stop request is forwarded as SIGTERM, and a stuck child is escalated to
  SIGKILL;
* a failing child, a spawn error or any other exception is logged and never stops the loop.

Overlap is impossible by construction (one child at a time); the collect process adds its own
file lock and the UNIQUE ``slot_utc`` as defence in depth.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

from wbmon import __version__, clock, health
from wbmon.clock import iso_z, parse_iso_z
from wbmon.config import Settings
from wbmon.log import log_event
from wbmon.schedule import CATCH_UP_GRACE, current_slot, next_slot, should_catch_up
from wbmon.signals import StopFlag, install_stop_handlers
from wbmon.storage import reader
from wbmon.storage.db import connect_ro
from wbmon.storage.migrations import init_db

TICK_S = 1.0
HEARTBEAT_EVERY_S = 30.0
CHILD_TERM_GRACE_S = 30.0
STOP_GRACE_S = 60.0
CHILD_TIMEOUT_MARGIN_S = 600.0
_LATE_START_LOG_S = 60


class Child(Protocol):
    """The slice of ``subprocess.Popen`` the scheduler needs (fakeable in tests)."""

    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


def spawn_collect(slot: str, *, db_path: str | Path) -> Child:
    """Start one fresh ``wbmon collect`` process for ``slot``.

    The argument list is fixed (no shell, the slot comes from ``current_slot``). stdout/stderr
    are inherited so the child's key=value lines reach the container log. The own session keeps a
    terminal Ctrl-C away from the child: only the scheduler gets it and forwards SIGTERM. The
    child uses the scheduler's own database path.
    """
    return subprocess.Popen(
        [sys.executable, "-m", "wbmon", "collect", "--trigger", "scheduler", "--slot", slot],
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "WBMON_DB_PATH": str(db_path)},
    )


def has_run_in_db(db_path: str | Path, slot: str) -> bool:
    """True when the database already has a run for ``slot``.

    Any read problem answers False: the collect child is idempotent per slot, so a false
    negative only costs one child that exits immediately with ``slot_exists``.
    """
    try:
        with contextlib.closing(connect_ro(db_path)) as con:
            return reader.run_id_for_slot(con, slot) is not None
    except (sqlite3.Error, OSError):
        return False


@dataclass
class Scheduler:
    """The hourly loop. ``spawn`` has no default so a test can never start a real collect."""

    child_timeout_s: float
    heartbeat_file: Path
    spawn: Callable[[str], Child]
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]
    has_run: Callable[[str], bool]
    stop: StopFlag = field(default_factory=StopFlag)
    grace: timedelta = CATCH_UP_GRACE
    tick_s: float = TICK_S
    heartbeat_every_s: float = HEARTBEAT_EVERY_S
    term_grace_s: float = CHILD_TERM_GRACE_S
    stop_grace_s: float = STOP_GRACE_S
    last_slot: str | None = None
    last_exit: int | None = None
    _last_beat: float = field(default=float("-inf"), init=False, repr=False)

    def beat(self, *, force: bool = False) -> None:
        """Write the heartbeat when forced or when ``heartbeat_every_s`` have passed."""
        mono = self.monotonic()
        if not force and mono - self._last_beat < self.heartbeat_every_s:
            return
        self._last_beat = mono
        now = self.now()
        payload = {
            "ts": iso_z(now),
            "pid": os.getpid(),
            "next_slot": next_slot(now),
            "last_slot": self.last_slot,
            "last_exit": self.last_exit,
            "version": __version__,
        }
        try:
            health.write_heartbeat(self.heartbeat_file, payload)
        except OSError as exc:  # a full disk must not kill the scheduler
            log_event(
                "heartbeat_failed",
                level=logging.WARNING,
                error=f"{type(exc).__name__}: {exc}",
            )

    def run_slot(self, slot: str) -> None:
        """Run one child to completion, keeping the heartbeat alive; never raises for a child."""
        started = self.monotonic()
        try:
            child = self.spawn(slot)
        except OSError as exc:
            self.last_slot, self.last_exit = slot, None
            log_event(
                "run_failed",
                level=logging.ERROR,
                slot=slot,
                reason="spawn_error",
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        log_event("run_started", slot=slot, pid=child.pid)
        reason: str | None = None
        term_at: float | None = None
        killed = False
        while (rc := child.poll()) is None:
            mono = self.monotonic()
            if term_at is None:
                if self.stop.requested:
                    reason = "shutdown"
                elif mono - started > self.child_timeout_s:
                    reason = "timeout"
                if reason is not None:
                    child.terminate()
                    term_at = mono
            elif not killed:
                grace = self.stop_grace_s if reason == "shutdown" else self.term_grace_s
                if mono - term_at >= grace:
                    child.kill()
                    killed = True
            self.beat()
            self.sleep(self.tick_s)
        self.last_slot, self.last_exit = slot, rc
        log_event(
            "run_finished",
            slot=slot,
            exit=rc,
            duration_s=round(self.monotonic() - started, 1),
        )
        if reason == "timeout":
            log_event("run_failed", level=logging.ERROR, slot=slot, exit=rc, reason="timeout")
        elif rc != 0:
            failure = "shutdown" if reason == "shutdown" else "exit_code"
            log_event("run_failed", level=logging.ERROR, slot=slot, exit=rc, reason=failure)
        self.beat(force=True)

    def run_forever(self) -> int:
        """Loop until a stop is requested; returns the process exit code (always 0)."""
        log_event(
            "scheduler_started",
            version=__version__,
            pid=os.getpid(),
            grace_min=int(self.grace.total_seconds() // 60),
            child_timeout_s=self.child_timeout_s,
            heartbeat=self.heartbeat_file,
        )
        self.beat(force=True)
        handled: str | None = None
        while not self.stop.requested:
            now = self.now()
            slot = current_slot(now)
            # ISO-Z strings sort chronologically, so a wall-clock step back never re-runs a slot.
            if handled is None or slot > handled:
                first = handled is None
                handled = slot
                try:
                    self._handle_slot(now, slot, first=first)
                except Exception as exc:  # noqa: BLE001 - the loop must outlive any failure
                    log_event(
                        "run_failed",
                        level=logging.ERROR,
                        slot=slot,
                        reason="exception",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                if self.stop.requested:
                    break
                log_event("slot_wait", next_slot=next_slot(self.now()))
            self.beat()
            self.sleep(self.tick_s)
        log_event("shutdown", signum=self.stop.signum, last_slot=self.last_slot)
        return 0

    def _handle_slot(self, now: datetime, slot: str, *, first: bool) -> None:
        late_s = int((now - parse_iso_z(slot)).total_seconds())
        if self.has_run(slot):
            log_event("slot_exists", slot=slot)
        elif should_catch_up(now, slot, False, self.grace):
            if first or late_s > _LATE_START_LOG_S:
                log_event("catch_up", slot=slot, late_s=late_s)
            self.run_slot(slot)
        else:
            log_event("slot_missed", level=logging.WARNING, slot=slot, late_s=late_s)


def build_scheduler(
    settings: Settings,
    *,
    now: Callable[[], datetime] = clock.utc_now,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Scheduler:
    """Production wiring: real clock, real ``wbmon collect`` children, the settings' database."""
    return Scheduler(
        child_timeout_s=settings.run_deadline + CHILD_TIMEOUT_MARGIN_S,
        heartbeat_file=health.heartbeat_path(settings.db_path),
        spawn=functools.partial(spawn_collect, db_path=settings.db_path),
        now=now,
        sleep=sleep,
        monotonic=monotonic,
        has_run=functools.partial(has_run_in_db, settings.db_path),
    )


def run_scheduler(settings: Settings, *, scheduler: Scheduler | None = None) -> int:
    """Create the database if needed, install SIGTERM/SIGINT handlers and run until stopped."""
    init_db(settings.db_path)
    sched = scheduler if scheduler is not None else build_scheduler(settings)
    with install_stop_handlers(sched.stop, (signal.SIGTERM, signal.SIGINT)):
        return sched.run_forever()
