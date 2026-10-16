"""Fake-clock behaviour matrix of the scheduler loop (no real process, no network).

``Clock.sleep`` advances wall and monotonic time together. ``FakeChild`` finishes after a number
of fake seconds, can ignore SIGTERM and records the signals it received. An autouse fixture bans
``subprocess.Popen`` so nothing here can ever start a real ``wbmon collect``.
"""

from __future__ import annotations

import contextlib
import json
import re
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from factories import make_settings
from wbmon import cli, clock, scheduler
from wbmon.health import HEARTBEAT_KEYS, check_heartbeat
from wbmon.log import configure_logging
from wbmon.schedule import next_slot
from wbmon.scheduler import (
    CHILD_TIMEOUT_MARGIN_S,
    Scheduler,
    build_scheduler,
    has_run_in_db,
    run_scheduler,
    spawn_collect,
)
from wbmon.storage.db import connect_rw
from wbmon.storage.migrations import init_db
from wbmon.storage.writer import Writer

INF = float("inf")
KV = re.compile(r'(\w+)=("(?:[^"\\]|\\.)*"|\S+)')


def at(text: str) -> datetime:
    return clock.parse_iso_z(text)


def hhmm(value: datetime) -> str:
    return value.strftime("%H:%M:%S")


@pytest.fixture(autouse=True)
def _no_real_processes(monkeypatch):
    def banned(*_args, **_kwargs):
        raise AssertionError("subprocess.Popen must never run in the fake-clock tests")

    monkeypatch.setattr(subprocess, "Popen", banned)


class Clock:
    """Injected time source; ``sleep`` advances both clocks and then runs the hooks."""

    def __init__(self, start: str) -> None:
        self.t = at(start)
        self.mono = 0.0
        self.hooks: list[Callable[[], None]] = []
        self.sleeps = 0

    def now(self) -> datetime:
        return self.t

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)
        self.mono += seconds
        self.sleeps += 1
        for hook in list(self.hooks):
            hook()


class FakeChild:
    """A child that finishes after ``runtime_s`` fake seconds (monotonic)."""

    def __init__(
        self,
        clock_: Clock,
        runtime_s: float,
        rc: int = 0,
        *,
        ignore_term: bool = False,
        term_rc: int = 0,
        pid: int = 5000,
    ) -> None:
        self.clock = clock_
        self.runtime_s = runtime_s
        self.rc = rc
        self.ignore_term = ignore_term
        self.term_rc = term_rc
        self.pid = pid
        self.started = clock_.mono
        self.signals: list[str] = []
        self.signal_at: dict[str, float] = {}
        self.finished_at: datetime | None = None
        self._result: int | None = None

    def _done(self, code: int) -> int:
        if self._result is None:
            self._result = code
            self.finished_at = self.clock.t
        return self._result

    def poll(self) -> int | None:
        if self._result is not None:
            return self._result
        if "kill" in self.signals:
            return self._done(-9)
        if "term" in self.signals and not self.ignore_term:
            return self._done(self.term_rc)
        if self.clock.mono - self.started >= self.runtime_s:
            return self._done(self.rc)
        return None

    def terminate(self) -> None:
        self.signals.append("term")
        self.signal_at["term"] = self.clock.mono

    def kill(self) -> None:
        self.signals.append("kill")
        self.signal_at["kill"] = self.clock.mono


@dataclass
class Spawn:
    slot: str
    at: datetime
    child: FakeChild


@dataclass
class Harness:
    clock: Clock
    sched: Scheduler
    spawns: list[Spawn] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)

    @property
    def slots(self) -> list[str]:
        return [s.slot for s in self.spawns]


def build(
    tmp_path: Path,
    start: str,
    end: str,
    *,
    child: Callable[[int, str], dict] | None = None,
    has_run: Callable[[str], bool] | None = None,
    spawn_error: Callable[[str], bool] | None = None,
    **sched_kwargs: object,
) -> Harness:
    """Wire a Scheduler to a fake clock; the fake sleep requests a stop at ``end``."""
    configure_logging("INFO")  # inside the test body, so the handler binds capsys' stdout
    clk = Clock(start)
    child = child or (lambda _i, _slot: {"runtime_s": 300})
    harness = Harness(clk, None)  # type: ignore[arg-type]

    def spawn(slot: str) -> FakeChild:
        harness.attempts.append(slot)
        if spawn_error is not None and spawn_error(slot):
            raise OSError("cannot spawn (test)")
        index = len(harness.spawns)
        fake = FakeChild(clk, pid=5000 + index, **child(index, slot))
        harness.spawns.append(Spawn(slot, clk.t, fake))
        return fake

    sched_kwargs.setdefault("child_timeout_s", 3 * 3600.0)
    harness.sched = Scheduler(
        heartbeat_file=tmp_path / "hb" / "scheduler_heartbeat.json",
        spawn=spawn,
        now=clk.now,
        sleep=clk.sleep,
        monotonic=clk.monotonic,
        has_run=has_run or (lambda _slot: False),
        **sched_kwargs,  # type: ignore[arg-type]
    )
    end_at = at(end)

    def stop_at_end() -> None:
        if clk.t >= end_at:
            harness.sched.stop.request(signal.SIGTERM)

    clk.hooks.append(stop_at_end)
    return harness


def events(out: str, name: str | None = None) -> list[dict[str, str]]:
    parsed = []
    for line in out.splitlines():
        fields = {}
        for key, raw in KV.findall(line):
            fields[key] = json.loads(raw) if raw.startswith('"') else raw
        if "event" in fields and (name is None or fields["event"] == name):
            parsed.append(fields)
    return parsed


# ------------------------------------------------------------------------------ cadence


def test_late_start_catches_up_once_then_runs_each_slot_on_the_hour(tmp_path, capsys):
    h = build(tmp_path, "2026-10-16T09:20:00Z", "2026-10-16T12:30:00Z")
    assert h.sched.run_forever() == 0
    out = capsys.readouterr().out
    assert h.slots == [
        "2026-10-16T09:00:00Z",
        "2026-10-16T10:00:00Z",
        "2026-10-16T11:00:00Z",
        "2026-10-16T12:00:00Z",
    ]
    assert [hhmm(s.at) for s in h.spawns] == ["09:20:00", "10:00:00", "11:00:00", "12:00:00"]
    assert len(events(out, "catch_up")) == 1
    assert events(out, "slot_missed") == []
    assert len(events(out, "run_started")) == 4
    assert len(events(out, "run_finished")) == 4
    assert events(out, "run_failed") == []


def test_start_50_minutes_late_skips_the_slot_and_waits_for_the_next(tmp_path, capsys):
    h = build(tmp_path, "2026-10-16T09:50:00Z", "2026-10-16T10:30:00Z")
    h.sched.run_forever()
    missed = events(capsys.readouterr().out, "slot_missed")
    assert [m["slot"] for m in missed] == ["2026-10-16T09:00:00Z"]
    assert h.slots == ["2026-10-16T10:00:00Z"]
    assert hhmm(h.spawns[0].at) == "10:00:00"


def test_start_exactly_45_minutes_late_still_catches_up(tmp_path):
    h = build(tmp_path, "2026-10-16T09:45:00Z", "2026-10-16T09:58:00Z")
    h.sched.run_forever()
    assert h.slots == ["2026-10-16T09:00:00Z"]


def test_start_45_minutes_and_1_second_late_is_missed(tmp_path, capsys):
    h = build(tmp_path, "2026-10-16T09:45:01Z", "2026-10-16T09:58:00Z")
    h.sched.run_forever()
    assert h.slots == []
    assert len(events(capsys.readouterr().out, "slot_missed")) == 1


def test_start_exactly_on_the_hour_runs_without_a_catch_up_complaint(tmp_path, capsys):
    h = build(tmp_path, "2026-10-16T09:00:00Z", "2026-10-16T09:10:00Z")
    h.sched.run_forever()
    assert h.slots == ["2026-10-16T09:00:00Z"]
    assert hhmm(h.spawns[0].at) == "09:00:00"


def test_slot_with_a_run_is_logged_and_not_rerun(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T09:20:00Z",
        "2026-10-16T11:30:00Z",
        has_run=lambda slot: slot == "2026-10-16T10:00:00Z",
    )
    h.sched.run_forever()
    out = capsys.readouterr().out
    assert h.slots == ["2026-10-16T09:00:00Z", "2026-10-16T11:00:00Z"]
    assert [e["slot"] for e in events(out, "slot_exists")] == ["2026-10-16T10:00:00Z"]


def test_never_overlaps_and_never_spawns_a_slot_twice(tmp_path):
    h = build(tmp_path, "2026-10-16T09:00:00Z", "2026-10-16T13:00:00Z")
    running: list[FakeChild] = []
    original = h.sched.spawn

    def checked(slot: str) -> FakeChild:
        assert all(c.poll() is not None for c in running), "spawned while a child was running"
        fake = original(slot)
        running.append(fake)
        return fake

    h.sched.spawn = checked  # type: ignore[assignment]
    h.sched.run_forever()
    assert len(h.slots) == len(set(h.slots)) == 4


# ----------------------------------------------------------------------------- failures


def test_failing_children_do_not_stop_the_loop(tmp_path, capsys):
    codes = {0: 1, 1: 3, 2: 0}
    h = build(
        tmp_path,
        "2026-10-16T09:20:00Z",
        "2026-10-16T11:30:00Z",
        child=lambda i, _slot: {"runtime_s": 120, "rc": codes[i]},
    )
    assert h.sched.run_forever() == 0
    out = capsys.readouterr().out
    assert len(events(out, "run_finished")) == 3
    failed = events(out, "run_failed")
    assert [f["reason"] for f in failed] == ["exit_code", "exit_code"]
    assert [f["exit"] for f in failed] == ["1", "3"]
    assert h.sched.last_exit == 0
    assert h.sched.last_slot == "2026-10-16T11:00:00Z"


def test_spawn_error_is_logged_and_the_next_slot_is_still_attempted(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T09:00:00Z",
        "2026-10-16T10:30:00Z",
        spawn_error=lambda slot: slot == "2026-10-16T09:00:00Z",
    )
    assert h.sched.run_forever() == 0
    failed = events(capsys.readouterr().out, "run_failed")
    assert [f["reason"] for f in failed] == ["spawn_error"]
    assert h.attempts == ["2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"]
    assert h.slots == ["2026-10-16T10:00:00Z"]


def test_spawn_error_leaves_last_exit_unknown(tmp_path):
    h = build(tmp_path, "2026-10-16T09:00:00Z", "2026-10-16T09:10:00Z", spawn_error=lambda _s: True)
    h.sched.run_forever()
    assert h.sched.last_slot == "2026-10-16T09:00:00Z"
    assert h.sched.last_exit is None


def test_exception_in_a_slot_is_logged_and_the_loop_continues(tmp_path, capsys):
    def has_run(slot: str) -> bool:
        if slot == "2026-10-16T09:00:00Z":
            raise RuntimeError("database exploded (test)")
        return False

    h = build(tmp_path, "2026-10-16T09:00:00Z", "2026-10-16T10:30:00Z", has_run=has_run)
    assert h.sched.run_forever() == 0
    failed = events(capsys.readouterr().out, "run_failed")
    assert [f["reason"] for f in failed] == ["exception"]
    assert h.slots == ["2026-10-16T10:00:00Z"]


# ----------------------------------------------------------------------------- overrun


def test_overrun_of_100_minutes_catches_up_the_late_slot(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T10:00:00Z",
        "2026-10-16T12:30:00Z",
        child=lambda i, _slot: {"runtime_s": 6000 if i == 0 else 300},
    )
    h.sched.run_forever()
    out = capsys.readouterr().out
    assert h.slots[:2] == ["2026-10-16T10:00:00Z", "2026-10-16T11:00:00Z"]
    assert abs((h.spawns[1].at - at("2026-10-16T11:40:00Z")).total_seconds()) <= 2
    catch_ups = events(out, "catch_up")
    assert [e["slot"] for e in catch_ups][-1] == "2026-10-16T11:00:00Z"
    assert events(out, "slot_missed") == []


def test_overrun_of_110_minutes_misses_the_slot_and_resumes_on_the_hour(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T10:00:00Z",
        "2026-10-16T12:30:00Z",
        child=lambda i, _slot: {"runtime_s": 6600 if i == 0 else 300},
    )
    h.sched.run_forever()
    out = capsys.readouterr().out
    assert [m["slot"] for m in events(out, "slot_missed")] == ["2026-10-16T11:00:00Z"]
    assert h.slots == ["2026-10-16T10:00:00Z", "2026-10-16T12:00:00Z"]
    assert hhmm(h.spawns[1].at) == "12:00:00"


# ------------------------------------------------------------- timeout and stop escalation


def test_timeout_escalates_term_then_kill_and_the_next_slot_still_runs(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T09:00:00Z",
        "2026-10-16T10:30:00Z",
        child_timeout_s=600.0,
        child=lambda i, _slot: (
            {"runtime_s": INF, "ignore_term": True} if i == 0 else {"runtime_s": 60}
        ),
    )
    h.sched.run_forever()
    out = capsys.readouterr().out
    stuck = h.spawns[0].child
    assert stuck.signals == ["term", "kill"]
    assert 600 <= stuck.signal_at["term"] - stuck.started <= 602
    assert stuck.signal_at["kill"] - stuck.signal_at["term"] >= h.sched.term_grace_s
    failed = events(out, "run_failed")
    assert [f["reason"] for f in failed] == ["timeout"]
    assert failed[0]["exit"] == "-9"
    assert h.slots == ["2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"]


def test_timeout_with_a_child_that_exits_on_sigterm_sends_no_kill(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T09:00:00Z",
        "2026-10-16T09:30:00Z",
        child_timeout_s=600.0,
        child=lambda _i, _slot: {"runtime_s": INF},
    )
    h.sched.run_forever()
    assert h.spawns[0].child.signals == ["term"]
    failed = events(capsys.readouterr().out, "run_failed")
    assert [f["reason"] for f in failed] == ["timeout"]
    assert failed[0]["exit"] == "0"


def test_stop_with_a_sigterm_ignoring_child_kills_after_the_stop_grace(tmp_path, capsys):
    h = build(
        tmp_path,
        "2026-10-16T09:00:00Z",
        "2026-10-16T09:05:00Z",
        child=lambda _i, _slot: {"runtime_s": INF, "ignore_term": True},
    )
    assert h.sched.run_forever() == 0
    out = capsys.readouterr().out
    child = h.spawns[0].child
    assert child.signals == ["term", "kill"]
    assert (
        child.signal_at["kill"] - child.signal_at["term"]
        >= h.sched.stop_grace_s
        == scheduler.STOP_GRACE_S
    )
    assert len(h.spawns) == 1
    assert events(out, "shutdown")[0]["last_slot"] == "2026-10-16T09:00:00Z"
    assert [f["reason"] for f in events(out, "run_failed")] == ["shutdown"]


def test_stop_with_a_graceful_child_sends_only_term(tmp_path):
    h = build(
        tmp_path,
        "2026-10-16T09:00:00Z",
        "2026-10-16T09:05:00Z",
        child=lambda _i, _slot: {"runtime_s": INF},
    )
    assert h.sched.run_forever() == 0
    assert h.spawns[0].child.signals == ["term"]
    assert len(h.spawns) == 1


def test_stop_while_idle_ends_the_loop_within_two_ticks(tmp_path, capsys):
    h = build(tmp_path, "2026-10-16T09:50:00Z", "2026-10-16T09:55:00Z")
    assert h.sched.run_forever() == 0
    assert h.clock.t <= at("2026-10-16T09:55:00Z") + timedelta(seconds=2 * h.sched.tick_s)
    shutdown = events(capsys.readouterr().out, "shutdown")
    assert len(shutdown) == 1
    assert shutdown[0]["signum"] == str(int(signal.SIGTERM))


def test_stop_requested_before_start_runs_nothing(tmp_path, capsys):
    h = build(tmp_path, "2026-10-16T09:00:00Z", "2026-10-16T09:00:00Z")
    h.sched.stop.request(signal.SIGTERM)
    assert h.sched.run_forever() == 0
    assert h.spawns == []
    assert len(events(capsys.readouterr().out, "shutdown")) == 1


# ----------------------------------------------------------------------------- heartbeat


def recording_heartbeats(monkeypatch, clk: Clock) -> list[tuple[datetime, dict]]:
    writes: list[tuple[datetime, dict]] = []
    original = scheduler.health.write_heartbeat

    def recording(path, payload):
        original(path, payload)
        writes.append((clk.t, dict(payload)))

    monkeypatch.setattr(scheduler.health, "write_heartbeat", recording)
    return writes


def test_heartbeat_keeps_ticking_during_a_30_minute_run(tmp_path, monkeypatch):
    h = build(
        tmp_path,
        "2026-10-16T09:00:00Z",
        "2026-10-16T09:40:00Z",
        child=lambda _i, _slot: {"runtime_s": 1800},
    )
    writes = recording_heartbeats(monkeypatch, h.clock)
    h.sched.run_forever()
    times = [when for when, _ in writes]
    gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
    assert max(gaps) <= 31
    finished = h.spawns[0].child.finished_at
    assert finished in times  # forced beat at the tick the child finished
    payload = dict(next(p for when, p in writes if when == finished))
    assert payload["last_slot"] == "2026-10-16T09:00:00Z"
    assert payload["last_exit"] == 0


def test_heartbeat_payload_keys_and_next_slot(tmp_path, monkeypatch):
    h = build(tmp_path, "2026-10-16T09:50:00Z", "2026-10-16T10:05:00Z")
    writes = recording_heartbeats(monkeypatch, h.clock)
    h.sched.run_forever()
    assert writes
    for when, payload in writes:
        assert tuple(sorted(payload)) == tuple(sorted(HEARTBEAT_KEYS))
        assert payload["ts"] == clock.iso_z(when)
        assert payload["next_slot"] == next_slot(when)
        assert isinstance(payload["pid"], int)
    assert writes[0][1]["last_slot"] is None
    assert writes[0][1]["last_exit"] is None


def test_heartbeat_file_is_healthy_at_the_end_of_a_run(tmp_path):
    h = build(tmp_path, "2026-10-16T09:00:00Z", "2026-10-16T09:20:00Z")
    h.sched.run_forever()
    ok, message = check_heartbeat(h.sched.heartbeat_file, h.clock.now())
    assert ok, message
    assert "last_slot=2026-10-16T09:00:00Z" in message


def test_heartbeat_write_failure_is_logged_and_runs_continue(tmp_path, monkeypatch, capsys):
    def denied(_path, _payload):
        raise PermissionError("read-only volume (test)")

    monkeypatch.setattr(scheduler.health, "write_heartbeat", denied)
    h = build(tmp_path, "2026-10-16T09:20:00Z", "2026-10-16T10:30:00Z")
    assert h.sched.run_forever() == 0
    out = capsys.readouterr().out
    assert events(out, "heartbeat_failed")
    assert h.slots == ["2026-10-16T09:00:00Z", "2026-10-16T10:00:00Z"]


# ---------------------------------------------------------------------------- clock steps


def test_wall_clock_step_back_never_reruns_a_handled_slot(tmp_path):
    h = build(
        tmp_path,
        "2026-10-16T10:00:00Z",
        "2026-10-16T11:30:00Z",
        child=lambda _i, _slot: {"runtime_s": 1800},
    )
    stepped = []

    def step_back() -> None:
        if not stepped and h.clock.t >= at("2026-10-16T10:10:00Z"):
            stepped.append(True)
            h.clock.t -= timedelta(hours=1)

    h.clock.hooks.append(step_back)
    h.sched.run_forever()
    assert stepped
    assert len(h.slots) == len(set(h.slots))
    assert h.slots[0] == "2026-10-16T10:00:00Z"


# ----------------------------------------------------------------------- wiring helpers


def test_build_scheduler_wires_production_collaborators(tmp_path):
    settings = make_settings(db_path=tmp_path / "data" / "wbmon.sqlite3")
    sched = build_scheduler(settings)
    assert sched.spawn.func is spawn_collect  # type: ignore[attr-defined]
    assert sched.spawn.keywords == {"db_path": settings.db_path}  # type: ignore[attr-defined]
    assert sched.has_run.func is has_run_in_db  # type: ignore[attr-defined]
    assert (
        sched.child_timeout_s
        == settings.run_deadline + CHILD_TIMEOUT_MARGIN_S
        == (settings.run_deadline + 600)
    )
    assert sched.heartbeat_file == tmp_path / "data" / "scheduler_heartbeat.json"
    assert sched.tick_s == 1.0
    assert sched.heartbeat_every_s == 30.0


def test_spawn_collect_argv_env_and_session(tmp_path, monkeypatch):
    seen: dict[str, object] = {}

    def recorder(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(subprocess, "Popen", recorder)
    db = tmp_path / "x" / "wbmon.sqlite3"
    slot = "2026-10-16T09:00:00Z"
    spawn_collect(slot, db_path=db)
    argv = seen["argv"]
    assert argv == [
        sys.executable,
        "-m",
        "wbmon",
        "collect",
        "--trigger",
        "scheduler",
        "--slot",
        slot,
    ]
    kwargs = seen["kwargs"]
    assert kwargs["start_new_session"] is True  # type: ignore[index]
    assert kwargs["stdin"] == subprocess.DEVNULL  # type: ignore[index]
    assert kwargs["env"]["WBMON_DB_PATH"] == str(db)  # type: ignore[index]
    assert "shell" not in kwargs  # type: ignore[operator]
    args = cli.build_parser().parse_args(argv[3:])
    assert args.trigger == "scheduler"
    assert args.slot == slot
    assert clock.parse_slot(slot) == slot


def seed_run(db: Path, slot: str) -> None:
    init_db(db)
    with contextlib.closing(connect_rw(db)) as con:
        Writer(con).start_run(
            trigger="scheduler",
            started_at=slot,
            query_source="bundled",
            params_json=make_settings().params_json(),
            top_n=5,
            n_queries=0,
            slot_utc=slot,
        )


def test_has_run_in_db_missing_database_is_false(tmp_path):
    assert has_run_in_db(tmp_path / "nope" / "wbmon.sqlite3", "2026-10-16T09:00:00Z") is False


def test_has_run_in_db_finds_only_the_seeded_slot(tmp_path):
    db = tmp_path / "wbmon.sqlite3"
    seed_run(db, "2026-10-16T09:00:00Z")
    assert has_run_in_db(db, "2026-10-16T09:00:00Z") is True
    assert has_run_in_db(db, "2026-10-16T10:00:00Z") is False


def test_has_run_in_db_garbage_file_is_false(tmp_path):
    db = tmp_path / "wbmon.sqlite3"
    db.write_bytes(b"definitely not sqlite" * 100)
    assert has_run_in_db(db, "2026-10-16T09:00:00Z") is False


def test_run_scheduler_initialises_db_installs_and_restores_handlers(tmp_path):
    settings = make_settings(db_path=tmp_path / "fresh" / "wbmon.sqlite3")
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    h = build(tmp_path, "2026-10-16T09:50:00Z", "2026-10-16T09:55:00Z")
    during: dict[str, object] = {}

    def observe() -> None:
        during.setdefault("sigterm", signal.getsignal(signal.SIGTERM))
        during.setdefault("sigint", signal.getsignal(signal.SIGINT))

    h.clock.hooks.append(observe)
    assert not settings.db_path.exists()
    assert run_scheduler(settings, scheduler=h.sched) == 0
    assert settings.db_path.exists()
    assert callable(during["sigterm"]) and during["sigterm"] is not before[signal.SIGTERM]
    assert callable(during["sigint"]) and during["sigint"] is not before[signal.SIGINT]
    assert {s: signal.getsignal(s) for s in before} == before


def test_a_real_signal_stops_the_loop_through_run_scheduler(tmp_path, capsys):
    settings = make_settings(db_path=tmp_path / "wbmon.sqlite3")
    h = build(tmp_path, "2026-10-16T09:50:00Z", "2099-01-01T00:00:00Z")
    sent = []

    def send_sigterm() -> None:
        if not sent and h.clock.sleeps >= 3:
            sent.append(True)
            signal.raise_signal(signal.SIGTERM)

    h.clock.hooks.append(send_sigterm)
    assert run_scheduler(settings, scheduler=h.sched) == 0
    out = capsys.readouterr().out
    assert events(out, "shutdown")[0]["signum"] == str(int(signal.SIGTERM))
