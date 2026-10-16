"""Heartbeat file and ``wbmon healthcheck``: atomic write, verdict rules, exit codes 0/1 only."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from wbmon import cli, clock, health
from wbmon.config import Settings
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer

NOW = clock.parse_iso_z("2026-10-16T09:30:00Z")


def payload(ts: str = "2026-10-16T09:30:00Z", **overrides: object) -> dict:
    data = {
        "ts": ts,
        "pid": 4242,
        "next_slot": "2026-10-16T10:00:00Z",
        "last_slot": "2026-10-16T09:00:00Z",
        "last_exit": 0,
        "version": "0.1.0",
    }
    data.update(overrides)
    return data


def ago(seconds: float) -> str:
    return clock.iso_z(NOW - timedelta(seconds=seconds))


def db_with_run(path: Path, *, started_at: str = "2026-10-16T09:00:05Z", finish: str | None = None):
    init_db(path)
    with contextlib.closing(connect_rw(path)) as con:
        writer = Writer(con)
        run_id = writer.start_run(
            trigger="scheduler",
            started_at=started_at,
            query_source="bundled",
            params_json=Settings.from_env().params_json(),
            top_n=5,
            n_queries=0,
            slot_utc="2026-10-16T09:00:00Z",
        )
        if finish:
            con.execute(
                "UPDATE runs SET status = ?, finished_at = ? WHERE id = ?",
                (finish, started_at, run_id),
            )
    return run_id


# ------------------------------------------------------------------------------ paths + writing


def test_heartbeat_path_is_next_to_the_database():
    assert health.heartbeat_path(Path("/x/y/wbmon.sqlite3")) == Path(
        "/x/y/scheduler_heartbeat.json"
    )
    assert health.heartbeat_path("data/wbmon.sqlite3") == Path("data/scheduler_heartbeat.json")


def test_contract_constants():
    assert health.HEARTBEAT_FILENAME == "scheduler_heartbeat.json"
    assert health.HEARTBEAT_KEYS == ("ts", "pid", "next_slot", "last_slot", "last_exit", "version")


def test_write_heartbeat_creates_parent_and_roundtrips(tmp_path):
    target = tmp_path / "sub" / "dir" / health.HEARTBEAT_FILENAME
    health.write_heartbeat(target, payload())
    assert json.loads(target.read_text(encoding="utf-8")) == payload()
    assert [p.name for p in target.parent.iterdir()] == [health.HEARTBEAT_FILENAME]


def test_write_heartbeat_overwrites_without_leftovers(tmp_path):
    target = tmp_path / health.HEARTBEAT_FILENAME
    health.write_heartbeat(target, payload(pid=1))
    health.write_heartbeat(target, payload(pid=2))
    assert json.loads(target.read_text(encoding="utf-8"))["pid"] == 2
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_heartbeat_failure_removes_tmp_and_propagates(tmp_path, monkeypatch):
    target = tmp_path / health.HEARTBEAT_FILENAME

    def boom(*_args, **_kwargs):
        raise OSError("disk full (test)")

    monkeypatch.setattr(health.os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        health.write_heartbeat(target, payload())
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------- check_heartbeat


def test_check_missing_file(tmp_path):
    ok, message = health.check_heartbeat(tmp_path / "nope.json", NOW)
    assert not ok
    assert message.startswith("heartbeat file missing")


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[1, 2, 3]",
        '"just a string"',
        "{}",
        '{"ts": 12345}',
        '{"ts": null}',
        '{"ts": "2026-10-16 09:30:00"}',
        '{"ts": "2026-10-16T09:30:00+00:00"}',
        '{"ts": "2026-10-16T09:30:00.5Z"}',
    ],
)
def test_check_invalid_content(tmp_path, raw):
    target = tmp_path / "hb.json"
    target.write_text(raw, encoding="utf-8")
    ok, message = health.check_heartbeat(target, NOW)
    assert not ok
    assert "not valid JSON with an ISO-Z 'ts'" in message


def test_check_non_utf8_content_is_unreadable_not_a_crash(tmp_path):
    target = tmp_path / "hb.json"
    target.write_bytes(b"\xff\xfe\x00garbage")
    ok, message = health.check_heartbeat(target, NOW)
    assert not ok
    assert message.startswith("heartbeat unreadable")


@pytest.mark.parametrize(
    ("age_s", "healthy"), [(0, True), (1, True), (180, True), (181, False), (3600, False)]
)
def test_check_age_boundary(tmp_path, age_s, healthy):
    target = tmp_path / "hb.json"
    health.write_heartbeat(target, payload(ts=ago(age_s)))
    ok, message = health.check_heartbeat(target, NOW, 180)
    assert ok is healthy
    if not healthy:
        assert "stale" in message


@pytest.mark.parametrize(("ahead_s", "healthy"), [(30, True), (60, True), (61, False)])
def test_check_future_skew(tmp_path, ahead_s, healthy):
    target = tmp_path / "hb.json"
    health.write_heartbeat(target, payload(ts=ago(-ahead_s)))
    ok, message = health.check_heartbeat(target, NOW)
    assert ok is healthy
    if not healthy:
        assert "future" in message


def test_check_healthy_message_fields(tmp_path):
    target = tmp_path / "hb.json"
    health.write_heartbeat(target, payload(ts=ago(12)))
    ok, message = health.check_heartbeat(target, NOW)
    assert ok
    assert message.startswith("healthy ")
    for fragment in ("age_s=12", "pid=4242", "last_slot=2026-10-16T09:00:00Z", "last_exit=0"):
        assert fragment in message


# ------------------------------------------------------------------------------ latest_run_info


def test_latest_run_info_missing_db_is_no_db_and_not_created(tmp_path):
    path = tmp_path / "nowhere" / "wbmon.sqlite3"
    assert health.latest_run_info(path) == "no_db"
    assert not path.exists()
    assert not path.parent.exists()


def test_latest_run_info_empty_history(tmp_path):
    path = tmp_path / "wbmon.sqlite3"
    init_db(path)
    assert health.latest_run_info(path) == "none"


def test_latest_run_info_reports_status_and_start(tmp_path):
    path = tmp_path / "wbmon.sqlite3"
    db_with_run(path)
    assert health.latest_run_info(path) == "running@2026-10-16T09:00:05Z"


def test_latest_run_info_garbage_file_is_unknown(tmp_path):
    path = tmp_path / "wbmon.sqlite3"
    path.write_bytes(b"this is not a sqlite database" * 50)
    assert health.latest_run_info(path) == "unknown"


# ------------------------------------------------------------------------------ run_healthcheck


def test_run_healthcheck_healthy_line(tmp_path, capsys):
    db = tmp_path / "wbmon.sqlite3"
    db_with_run(db)
    health.write_heartbeat(health.heartbeat_path(db), payload(ts=ago(5)))
    assert health.run_healthcheck(db, now=lambda: NOW) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("healthy ")
    assert lines[0].endswith(" last_run=running@2026-10-16T09:00:05Z")


def test_run_healthcheck_unhealthy_line(tmp_path, capsys):
    db = tmp_path / "wbmon.sqlite3"
    assert health.run_healthcheck(db, now=lambda: NOW) == 1
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("unhealthy: heartbeat file missing")
    assert lines[0].endswith(" last_run=no_db")


def test_blocked_history_with_fresh_heartbeat_is_healthy(tmp_path, capsys):
    db = tmp_path / "wbmon.sqlite3"
    db_with_run(db, finish="blocked")
    health.write_heartbeat(health.heartbeat_path(db), payload(ts=ago(3), last_exit=1))
    assert health.run_healthcheck(db, now=lambda: NOW) == 0
    assert "last_run=blocked@" in capsys.readouterr().out


def test_max_age_is_honoured(tmp_path):
    db = tmp_path / "wbmon.sqlite3"
    health.write_heartbeat(health.heartbeat_path(db), payload(ts=ago(100)))
    assert health.run_healthcheck(db, max_age_s=200, now=lambda: NOW) == 0
    assert health.run_healthcheck(db, max_age_s=50, now=lambda: NOW) == 1


# ------------------------------------------------------------------------------------- CLI


def test_cli_healthcheck_without_heartbeat_is_1(capsys):
    assert cli.main(["healthcheck"]) == 1
    assert capsys.readouterr().out.startswith("unhealthy: heartbeat file missing")


def test_cli_healthcheck_fresh_heartbeat_is_0(capsys):
    db = Path(os.environ["WBMON_DB_PATH"])
    health.write_heartbeat(health.heartbeat_path(db), payload(ts=clock.iso_z(clock.utc_now())))
    assert cli.main(["healthcheck"]) == 0
    assert capsys.readouterr().out.startswith("healthy ")


def test_cli_healthcheck_max_age(capsys):
    db = Path(os.environ["WBMON_DB_PATH"])
    ts = clock.iso_z(clock.utc_now() - timedelta(seconds=10))
    health.write_heartbeat(health.heartbeat_path(db), payload(ts=ts))
    assert cli.main(["healthcheck", "--max-age", "5"]) == 1
    assert "stale" in capsys.readouterr().out
    assert cli.main(["healthcheck", "--max-age", "60"]) == 0


def test_cli_healthcheck_db_flag_reads_that_directory(tmp_path, capsys):
    other = tmp_path / "other" / "x.sqlite3"
    health.write_heartbeat(health.heartbeat_path(other), payload(ts=clock.iso_z(clock.utc_now())))
    assert cli.main(["healthcheck", "--db", str(other)]) == 0
    assert cli.main(["healthcheck"]) == 1  # the default directory has no heartbeat
    capsys.readouterr()


def test_cli_healthcheck_never_creates_the_database(tmp_path):
    cli.main(["healthcheck"])
    assert list(tmp_path.iterdir()) == []


def test_cli_healthcheck_config_error_is_1_not_2(monkeypatch, capsys):
    monkeypatch.setenv("WBMON_TOP_N", "abc")
    assert cli.main(["healthcheck"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("unhealthy: configuration error")


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "nan", "inf", ""])
def test_cli_healthcheck_bad_max_age_is_1(bad, capsys):
    assert cli.main(["healthcheck", "--max-age", bad]) == 1
    capsys.readouterr()


def test_cli_healthcheck_unknown_option_is_1(capsys):
    assert cli.main(["healthcheck", "--bogus"]) == 1
    capsys.readouterr()


def test_cli_healthcheck_help_is_0(capsys):
    assert cli.main(["healthcheck", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--max-age" in out
    assert "--db" in out


def test_cli_other_commands_keep_exit_code_2_on_usage_errors(capsys):
    assert cli.main(["collect", "--top-n", "abc"]) == 2
    assert cli.main(["bogus"]) == 2
    capsys.readouterr()


def test_cli_healthcheck_unexpected_error_is_1(monkeypatch, capsys):
    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom (test)")

    monkeypatch.setattr(cli, "run_healthcheck", boom)
    assert cli.main(["healthcheck"]) == 1
    assert capsys.readouterr().out.startswith("unhealthy: RuntimeError: kaboom")


# ------------------------------------------------------------------------------ real process


def test_real_process_healthcheck_without_heartbeat(tmp_path):
    db = tmp_path / "db" / "wbmon.sqlite3"
    env = {**os.environ, "WBMON_DB_PATH": str(db)}
    proc = subprocess.run(
        [sys.executable, "-m", "wbmon", "healthcheck"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1
    assert proc.stdout.startswith("unhealthy: heartbeat file missing")
    assert not db.exists()
    assert not db.parent.exists()
