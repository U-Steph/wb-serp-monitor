---
phase: 02-unattended-operation-packaging
plan: 01
subsystem: scheduler
tags: [scheduler, heartbeat, healthcheck, signals, subprocess, popen, catch-up, msk]

requires:
  - phase: 01-collection-core/05
    provides: "`wbmon collect --trigger scheduler --slot` contract (exit 0 on slot_exists/lock_busy), reader.run_id_for_slot, connect_ro, key=value log_event"
provides:
  - "`wbmon scheduler [--db]`: long-running hourly loop; a fresh `python -m wbmon collect --trigger scheduler --slot <slot>` Popen child at every top of the hour, 45 min inclusive catch-up of the current slot only, never back-fills or overlaps, child failures never stop it, exit 0 on SIGTERM/SIGINT"
  - "`wbmon healthcheck [--db] [--max-age 180]`: one stdout line, exit 0 iff the heartbeat parses and is fresh; only ever 0 or 1 (Docker reserves 2)"
  - "heartbeat contract: <dirname(WBMON_DB_PATH)>/scheduler_heartbeat.json with exactly ts, pid, next_slot, last_slot, last_exit, version; atomic write; refreshed at least every 30 s also while a collect child runs"
  - "pure slot arithmetic (schedule.py), StopFlag + install_stop_handlers (signals.py), health.py, scheduler.py"
affects: [02-02, phase-03, phase-04]

tech-stack:
  added: []
  patterns:
    - "Popen + 1 s poll loop instead of subprocess.run: heartbeat keeps ticking, SIGTERM is forwarded, SIGTERM then SIGKILL escalation (D2-A2)"
    - "act only when current_slot > last handled slot (ISO-Z strings sort chronologically): catch-up, no back-fill and immunity to a wall-clock step back in one rule"
    - "Scheduler.spawn is a required dataclass field, so no test can start a real collect by accident"
    - "tests that call library code directly call configure_logging('INFO') inside the test body (a handler bound in a fixture points at a closed pytest stream)"

key-files:
  created:
    - src/wbmon/health.py
    - src/wbmon/schedule.py
    - src/wbmon/signals.py
    - src/wbmon/scheduler.py
    - tests/test_health.py
    - tests/test_scheduler_e2e.py
    - tests/test_schedule.py
    - tests/test_signals.py
    - tests/test_scheduler.py
  modified:
    - src/wbmon/cli.py

key-decisions:
  - "Child supervision uses Popen + poll (D2-A2 supersedes the subprocess.run wording of CONTEXT Area 1 #1): subprocess.run cannot keep the heartbeat ticking, forward SIGTERM or escalate to SIGKILL"
  - "healthcheck maps every failure (ConfigError, argparse usage error, unexpected exception) to a single stdout line and exit 1; --help stays 0"
  - "spawn_collect passes the scheduler's own db path to the child through WBMON_DB_PATH so `wbmon scheduler --db X` and its children share one database"
  - "heartbeat write failures are logged (heartbeat_failed) and swallowed; run_scheduler runs init_db so the no-Docker path needs no separate init-db"

patterns-established:
  - "scheduler events: scheduler_started, slot_wait, run_started, run_finished, run_failed (reason exit_code | timeout | shutdown | spawn_error | exception), slot_missed, slot_exists, catch_up, shutdown, heartbeat_failed"
  - "timing knobs (tick_s, heartbeat_every_s, term_grace_s, stop_grace_s, grace) are Scheduler fields read from the instance, never module globals"

requirements-completed: [SCHED-02, SCHED-03]

duration: 40min
completed: 2026-10-16
---

# Phase 2 Plan 01: Hourly scheduler, heartbeat and healthcheck Summary

**`wbmon scheduler` starts a fresh `collect` child at every top of the hour on a 1 s Popen poll loop (45 min catch-up of the current slot only, no back-fill, no overlap, SIGTERM then SIGKILL escalation, atomic heartbeat every 30 s even during runs, exit 0 on SIGTERM) and `wbmon healthcheck` turns that heartbeat into an honest 0/1 verdict independent of Wildberries.**

## Performance

- **Duration:** about 40 min
- **Tasks:** 3 of 3
- **Files:** 9 created, 1 modified
- **Tests:** 598 before, 720 after (offline suite, about 4 s); ruff check and format clean

## Accomplishments

- `health.py`: atomic `write_heartbeat` (tmp file in the same directory, one atomic rename, cleanup in `finally`), `check_heartbeat` (valid JSON object, strict ISO-Z `ts`, age <= max-age, at most 60 s in the future), `latest_run_info` (information only, never creates a DB, never raises), `run_healthcheck` (one line).
- `schedule.py`: pure `current_slot`, `next_slot`, `should_catch_up` (inclusive 45 min, future slot never run); imports only `__future__`, `datetime`, `wbmon.clock`.
- `signals.py`: `StopFlag` plus `install_stop_handlers` (handler only flips a bool, restores previous handlers in `finally`, no-op off the main thread).
- `scheduler.py`: `Scheduler` loop, `spawn_collect` (list argv, `start_new_session=True`, inherited stdout), `has_run_in_db`, `build_scheduler`, `run_scheduler`.
- `cli.py`: `scheduler` and `healthcheck` subcommands; every other subcommand and exit code is unchanged.
- Real-process proof (`tests/test_scheduler_e2e.py`): a scheduler on a pre-seeded tmp DB writes the heartbeat (exact keys, own pid), `wbmon healthcheck` reports healthy, SIGTERM exits 0 with `scheduler_started`, `slot_exists`, `shutdown` and no `run_started`/`catch_up`.

## Task Commits

1. **Task 1: healthcheck + heartbeat file** - `a1ee21f` (feat)
2. **Task 2: scheduler, schedule, signals, e2e** - `87fdf5a` (feat)
3. **Task 3: slot vectors, signal helper, fake-clock matrix** - `cd92d12` (test)

## Files Created/Modified

- `src/wbmon/health.py`, `schedule.py`, `signals.py`, `scheduler.py` - new runtime modules (stdlib only)
- `src/wbmon/cli.py` - `scheduler` and `healthcheck` subparsers, `_positive_float`, healthcheck error mapping to exit 1
- `tests/test_health.py` (50 cases), `test_schedule.py`, `test_signals.py`, `test_scheduler.py` (31 cases, 0.8 s), `test_scheduler_e2e.py`

## Decisions Made

See frontmatter. The behaviour matrix proven on the fake clock: late start 09:20 gives catch-up of 09:00 then 10:00/11:00/12:00 exactly on the hour; 09:45:00 catches up, 09:45:01 is `slot_missed`; a 100 min run is followed by a catch-up of the late slot at about 11:40, a 110 min run by `slot_missed` and the 12:00 run; exit codes 1, 3, 0 never stop the loop; timeout sends `term` at about 600 s then `kill` after at least 30 s; stop kills a SIGTERM-ignoring child after at least 60 s; heartbeat gaps stay at most 31 s over a 30 min child; a wall-clock step back never re-runs a slot.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Logging handler bound in an autouse fixture wrote to a closed stream**
- **Found during:** Task 3 (first run of tests/test_scheduler.py: 16 failures, "Logging error")
- **Issue:** `configure_logging` called in a fixture bound the handler to the fixture-phase capture stream, closed by the time the test body ran (the same hazard Phase 1 recorded in its patterns).
- **Fix:** `build()` in tests/test_scheduler.py calls `configure_logging("INFO")` inside the test body.
- **Files modified:** tests/test_scheduler.py
- **Verification:** 70 tests in the three Task 3 modules pass.
- **Committed in:** cd92d12

**2. [Process] Task commits are combined RED+GREEN**
- Each TDD task was committed once (tests and implementation together) rather than as separate `test(...)` then `feat(...)` commits. For Task 2 the real-process e2e test was written alongside the implementation; no test was weakened to pass. Strictly this misses the per-task RED commit of the plan-level TDD gate (the plan type is `execute`, not `tdd`).

---

**Total deviations:** 1 auto-fixed (Rule 3), 1 process note. **Impact:** none on behaviour; scheduler.py needed no changes after Task 3 tests (all behaviours held on the first run).

## Issues Encountered

- `gsd-tools` is not on PATH in this environment; state updates were done through `node ~/.claude/gsd-core/bin/gsd-tools.cjs` where possible.

## Known Stubs

None.

## Threat Flags

None. New surface is limited to the heartbeat file (threat T-02-05, mitigated: same-directory atomic rename; strict validation in `check_heartbeat`) and a fixed-argv child process (T-02-01, argv asserted by test, no shell).

## Next Phase Readiness

- Plan 02-02 (Docker/compose/Makefile/.env.example) can reference `wbmon scheduler` (exec-form, `stop_grace_period >= 75 s`; the scheduler exits 0 on SIGTERM) and `wbmon healthcheck --max-age 180` (exit 0/1 only).
- Phase 3 UI can read `<dirname(WBMON_DB_PATH)>/scheduler_heartbeat.json` (keys ts, pid, next_slot, last_slot, last_exit, version; `ts` ISO-Z).
- Not done here by design (later plan): graceful SIGTERM inside `collect` (`should_stop`, `terminated` error class). Until then a SIGTERM forwarded to a running collect kills it with the default action; the scheduler's 60 s wait then returns quickly and the next collect start runs `abandon_stale_runs`.
- No real WB/Google traffic and no `data/` directory were produced by any test; the only real scheduler process ran against a pre-seeded tmp DB.

## Self-Check: PASSED

- Files exist: src/wbmon/health.py, schedule.py, signals.py, scheduler.py; tests/test_health.py, test_scheduler_e2e.py, test_schedule.py, test_signals.py, test_scheduler.py
- Commits exist: a1ee21f, 87fdf5a, cd92d12
- Full offline suite 720 passed; `ruff check src tests` and `ruff format --check src tests` clean
