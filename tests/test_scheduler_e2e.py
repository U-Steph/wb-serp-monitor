"""Real-process proof of ``wbmon scheduler`` + ``wbmon healthcheck`` + SIGTERM, with no collect.

Safety: the tmp database is pre-seeded with a run for the current slot and the next two, so the
scheduler can only log ``slot_exists`` and never spawns ``wbmon collect``. Even an unexpected
child could only reach a closed local port (WB and sheet URLs point at 127.0.0.1:9). Seeded rows
are synthetic and live only in this tmp database.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from datetime import timedelta

import pytest

import wbmon
from wbmon import clock
from wbmon.config import Settings
from wbmon.health import HEARTBEAT_FILENAME
from wbmon.schedule import current_slot
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer

READY_TIMEOUT_S = 15.0
EXIT_TIMEOUT_S = 15.0


@pytest.fixture
def scheduler_env(tmp_path):
    db = tmp_path / "db" / "wbmon.sqlite3"
    init_db(db)
    now = clock.utc_now()
    params_json = Settings.from_env().params_json()
    with contextlib.closing(connect_rw(db)) as con:
        writer = Writer(con)
        for hours in (0, 1, 2):
            writer.start_run(
                trigger="scheduler",
                started_at=clock.iso_z(now),
                query_source="bundled",
                params_json=params_json,
                top_n=5,
                n_queries=0,
                slot_utc=current_slot(now + timedelta(hours=hours)),
            )
    env = {
        **os.environ,
        "WBMON_DB_PATH": str(db),
        "WBMON_SEARCH_URL": "http://127.0.0.1:9/search",
        "WBMON_SHEET_CSV_URL": "http://127.0.0.1:9/sheet.csv",
        "PYTHONUNBUFFERED": "1",
    }
    return db, env


def wait_for(path, proc, timeout=READY_TIMEOUT_S):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            pytest.fail(f"scheduler exited early with {proc.returncode}")
        time.sleep(0.05)
    pytest.fail(f"{path} did not appear within {timeout} s")


def run_cli(env, cwd, *args):
    return subprocess.run(
        [sys.executable, "-m", "wbmon", *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_real_scheduler_heartbeat_healthcheck_and_clean_sigterm(tmp_path, scheduler_env):
    db, env = scheduler_env
    heartbeat = db.parent / HEARTBEAT_FILENAME
    proc = subprocess.Popen(
        [sys.executable, "-m", "wbmon", "scheduler"],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for(heartbeat, proc)

        beat = json.loads(heartbeat.read_text(encoding="utf-8"))
        assert set(beat) == {"ts", "pid", "next_slot", "last_slot", "last_exit", "version"}
        assert beat["pid"] == proc.pid
        assert beat["last_slot"] is None
        assert beat["last_exit"] is None
        assert beat["version"] == wbmon.__version__

        check = run_cli(env, tmp_path, "healthcheck")
        assert check.returncode == 0, check.stdout
        assert check.stdout.startswith("healthy ")

        proc.send_signal(signal.SIGTERM)
        output, _ = proc.communicate(timeout=EXIT_TIMEOUT_S)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()

    assert proc.returncode == 0, output
    assert "event=scheduler_started" in output
    assert "event=slot_exists" in output
    assert "event=shutdown" in output
    assert "event=run_started" not in output
    assert "event=catch_up" not in output


def test_top_level_help_lists_every_subcommand(tmp_path):
    proc = run_cli(os.environ.copy(), tmp_path, "--help")
    assert proc.returncode == 0
    for name in ("init-db", "collect", "probe", "scheduler", "healthcheck"):
        assert name in proc.stdout
