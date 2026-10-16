"""Real-process supervision proofs for the scheduler (SIGTERM forwarding, SIGKILL escalation).

Every spawned program is a ``python -c`` snippet (the supervised children) or a ``python -c``
harness that imports ``wbmon`` and runs the scheduler against a temporary database. Nothing here
starts a Wildberries-facing process and nothing touches the network. Readiness is signalled with
marker files in ``tmp_path``; every child is killed in a ``finally`` block.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wbmon.log import configure_logging
from wbmon.scheduler import Scheduler

WAIT_S = 15.0
SLOT = "2026-10-16T09:00:00Z"
PINNED_NOW = datetime(2026, 10, 16, 9, 20, tzinfo=UTC)

# Installs a SIGTERM handler that leaves a marker and exits 0, then reports ready and waits.
GRACEFUL_CHILD = """
import pathlib, signal, sys, time
marks = pathlib.Path(sys.argv[1])
def on_term(signum, frame):
    (marks / "got-term").write_text("1")
    sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
(marks / "ready").write_text("1")
time.sleep(60)
"""

# Reports ready, then ignores SIGTERM for as long as it lives.
STUBBORN_CHILD = """
import pathlib, signal, sys, time
marks = pathlib.Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
(marks / "ready").write_text("1")
time.sleep(60)
"""

HARNESS = """
import subprocess, sys, time
from datetime import datetime, timezone
from wbmon.config import Settings
from wbmon.health import heartbeat_path
from wbmon.log import configure_logging
from wbmon.scheduler import Scheduler, run_scheduler

marks, child_code = sys.argv[1], sys.argv[2]
settings = Settings.from_env()
configure_logging("INFO")
sched = Scheduler(
    child_timeout_s=120.0,
    heartbeat_file=heartbeat_path(settings.db_path),
    spawn=lambda slot: subprocess.Popen(
        [sys.executable, "-c", child_code, marks], start_new_session=True
    ),
    now=lambda: datetime(2026, 10, 16, 9, 20, tzinfo=timezone.utc),
    sleep=time.sleep,
    monotonic=time.monotonic,
    has_run=lambda slot: False,
    tick_s=0.05,
    stop_grace_s=10.0,
)
sys.exit(run_scheduler(settings, scheduler=sched))
"""


def wait_for(path: Path, what: str) -> None:
    deadline = time.monotonic() + WAIT_S
    while not path.exists():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what} ({path})")
        time.sleep(0.02)


class Harness:
    """Builds a Scheduler whose children are ``python -c`` snippets; reaps them afterwards."""

    def __init__(self, tmp_path: Path) -> None:
        self.marks = tmp_path / "marks"
        self.marks.mkdir()
        self.children: list[subprocess.Popen] = []
        self.heartbeat = tmp_path / "hb" / "scheduler_heartbeat.json"
        self.scheduler: Scheduler | None = None

    def build(self, child_code: str, *, auto_stop: bool = True, **fields) -> Scheduler:
        def spawn(_slot: str) -> subprocess.Popen:
            child = subprocess.Popen(
                [sys.executable, "-c", child_code, str(self.marks)],
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            self.children.append(child)
            return child

        ready = self.marks / "ready"

        def sleep(seconds: float) -> None:  # stop once the child reports ready, like a SIGTERM
            if auto_stop and ready.exists() and not sched.stop.requested:
                sched.stop.request(signal.SIGTERM)
            time.sleep(seconds)

        sched = Scheduler(
            child_timeout_s=fields.pop("child_timeout_s", 120.0),
            heartbeat_file=self.heartbeat,
            spawn=spawn,
            now=lambda: PINNED_NOW,
            sleep=sleep,
            monotonic=time.monotonic,
            has_run=lambda _slot: False,
            tick_s=0.05,
            **fields,
        )
        self.scheduler = sched
        return sched

    def reap(self) -> None:
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=WAIT_S)


@pytest.fixture
def harness(tmp_path):
    h = Harness(tmp_path)
    try:
        yield h
    finally:
        h.reap()


def test_a_child_that_handles_sigterm_is_stopped_gracefully(harness, capsys):
    configure_logging("INFO")
    sched = harness.build(GRACEFUL_CHILD, stop_grace_s=5.0)

    started = time.monotonic()
    sched.run_slot(SLOT)
    elapsed = time.monotonic() - started

    assert sched.last_exit == 0
    assert (harness.marks / "got-term").exists()
    assert elapsed < 5.0
    out = capsys.readouterr().out
    assert " event=run_finished " in out
    assert " event=run_failed " not in out


def test_a_child_that_ignores_sigterm_is_sigkilled_after_the_stop_grace(harness, capsys):
    configure_logging("INFO")
    sched = harness.build(STUBBORN_CHILD, stop_grace_s=0.5)

    started = time.monotonic()
    sched.run_slot(SLOT)
    elapsed = time.monotonic() - started

    assert sched.last_exit == -signal.SIGKILL
    assert 0.4 <= elapsed < 5.0
    out = capsys.readouterr().out
    (failed,) = [line for line in out.splitlines() if " event=run_failed " in line]
    assert "reason=shutdown" in failed


def test_a_child_over_its_timeout_is_terminated_then_killed(harness, capsys):
    configure_logging("INFO")
    # the timeout is generous enough for interpreter start-up, so the handler is installed first
    sched = harness.build(STUBBORN_CHILD, auto_stop=False, child_timeout_s=1.5, term_grace_s=0.3)

    started = time.monotonic()
    sched.run_slot(SLOT)
    elapsed = time.monotonic() - started

    assert sched.last_exit == -signal.SIGKILL
    assert elapsed < 10.0
    out = capsys.readouterr().out
    (failed,) = [line for line in out.splitlines() if " event=run_failed " in line]
    assert "reason=timeout" in failed


def test_sigterm_to_a_real_scheduler_process_is_forwarded_and_exits_zero(tmp_path):
    marks = tmp_path / "harness-marks"
    marks.mkdir()
    db_path = tmp_path / "db" / "wbmon.sqlite3"
    env = {
        **os.environ,
        "WBMON_DB_PATH": str(db_path),
        "WBMON_SEARCH_URL": "http://127.0.0.1:9/search",
        "WBMON_SHEET_CSV_URL": "http://127.0.0.1:9/sheet.csv",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", HARNESS, str(marks), GRACEFUL_CHILD],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for(marks / "ready", "the supervised child to be ready")
        proc.send_signal(signal.SIGTERM)
        output, _ = proc.communicate(timeout=WAIT_S)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    assert proc.returncode == 0, output
    assert (marks / "got-term").exists()  # the scheduler forwarded SIGTERM to its child
    for event in ("catch_up", "run_started", "run_finished", "shutdown"):
        assert f" event={event} " in output, output
    heartbeat = json.loads((db_path.parent / "scheduler_heartbeat.json").read_text("utf-8"))
    assert heartbeat["pid"] == proc.pid
    assert heartbeat["last_slot"] == SLOT
    assert heartbeat["last_exit"] == 0
