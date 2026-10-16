---
phase: 02-unattended-operation-packaging
plan: 03
subsystem: scheduler
tags: [sigterm, graceful-shutdown, abandoned, terminated, integration, cross-check, ci]

requires:
  - phase: 02-unattended-operation-packaging/01
    provides: "Scheduler (Popen supervision, SIGTERM forwarding, stop/timeout escalation), StopFlag + install_stop_handlers, heartbeat and healthcheck"
  - phase: 02-unattended-operation-packaging/02
    provides: "Dockerfile, compose.yaml, Makefile and the static packaging tests"
provides:
  - "run_once(..., should_stop=...): a stop request records every remaining query as error/terminated without a request and closes the run (D2-A1)"
  - "`wbmon collect` handles SIGTERM only (finishes the in-flight query, closes the run, exit 0); SIGINT keeps the Phase 1 path"
  - "ERROR_CLASSES gains 'terminated' (20 classes; no DDL CHECK on error_class, so no migration)"
  - "proof that a SIGKILLed run becomes 'abandoned' on the next collect and its slot stays a gap (slot_exists)"
  - "real-process supervision tests (python -c children only) and compose/Dockerfile/Makefile <-> CLI cross-checks"
affects: [phase-03, phase-04]

tech-stack:
  added: []
  patterns:
    - "stop checked first in every query iteration (before deadline and breaker), so a stop outranks both"
    - "real-process tests use marker files for readiness and shorten grace periods through Scheduler fields, never module constants"

key-files:
  created:
    - tests/test_orchestrator_terminate.py
    - tests/test_cli_terminate.py
    - tests/test_scheduler_process.py
    - tests/test_packaging_cross.py
  modified:
    - src/wbmon/models.py
    - src/wbmon/orchestrator.py
    - src/wbmon/cli.py
    - tests/test_contracts.py

key-decisions:
  - "terminated queries do not touch the breaker counter and are not counted as collected; the run status comes from the unchanged Phase 1 derivation (partial if any query succeeded, otherwise error)"
  - "SIGTERM before the run row exists (lock/slot checks or query-list fetch) creates no run: log event terminated phase=before_start, return None"
  - "no cancellation inside WbClient: the in-flight query finishes within its own timeouts; the scheduler's stop grace then SIGKILLs and abandon_stale_runs recovers"

patterns-established:
  - "log event terminated: phase=before_start, or run_id + remaining=N (logged once)"

requirements-completed: [SCHED-02, SCHED-03, OPS-01]

duration: 30min
completed: 2026-10-16
---

# Phase 2 Plan 03: Graceful stop, real-process proofs and packaging cross-checks Summary

**`wbmon collect` now turns SIGTERM into honest `terminated` attempt rows, a closed run and exit 0; SIGKILL leaves an `abandoned` run whose slot stays a gap; the scheduler's SIGTERM forwarding and SIGKILL escalation are proven with real processes; and compose/Dockerfile/Makefile are cross-checked against the CLI. The offline suite passes on Python 3.14, 3.12 and 3.11.**

## Performance

- **Duration:** about 30 min
- **Tasks:** 3 of 3
- **Tests:** 755 passed before this plan; 785 passed and 1 skipped after (the skip is the `docker compose config` test, Docker is absent) on 3.14, 3.12 and 3.11; ruff check and format clean

## Accomplishments

- `run_once(should_stop=...)`: three `should_stop()` checks. Two run before the run row exists, after the slot check and again after the query list is fetched, and return `None` with `terminated phase=before_start`. The third sits at the top of each query iteration, before the deadline and breaker branches.
- Terminated attempts: status `error`, error_class `terminated`, no pages, no items, NULL http_status, `duration_ms` 0, message `SIGTERM received; no request sent`. They still go through `record_query` and `query_done`, so the attempt log is complete. One `terminated` event with `remaining=N` is logged per run.
- Precedence is tested: three 403s followed by a stop give blocked, blocked, blocked, then terminated for the rest, exactly three WB requests and run status `blocked`.
- Run status after a stop follows D2-A1: `partial` when at least one query succeeded, `error` when none did (the run is never left `running`).
- `cli.py`: the collect branch wraps `run_once` in `install_stop_handlers(StopFlag, (SIGTERM,))`. SIGINT is not touched, and the previous SIGTERM handler is restored after `main()`.
- SIGKILL simulation: a run left `running` is marked `abandoned` with `finished_at` by the next collect. The same slot is answered `slot_exists` with 0 WB requests, and the following hourly slot runs normally.
- Real-process tests (`tests/test_scheduler_process.py`, 4 tests, about 3 s):
  - a graceful `python -c` child receives the forwarded SIGTERM and exits 0 in under 5 s with no `run_failed`;
  - a SIGTERM-ignoring child is SIGKILLed after the stop grace (`last_exit == -SIGKILL`, `reason=shutdown`);
  - a child past its timeout is terminated, then killed (`reason=timeout`);
  - a real `python -c` harness process running `run_scheduler` exits 0 on SIGTERM, forwards it to the child, logs `catch_up`, `run_started`, `run_finished` and `shutdown`, and leaves a heartbeat with its own pid, `last_slot` `2026-10-16T09:00:00Z` and `last_exit` 0.
- `tests/test_packaging_cross.py` (15 tests): every `wbmon` command in compose, the Dockerfile CMD and Makefile recipes parses with `build_parser`; the collector healthcheck runs in-process (1 without a heartbeat, 0 with a fresh one, never 2); and the timing and shared-path agreements hold:
  - `stop_grace_period` 90 s is above the scheduler stop wait plus 5 ticks (65 s);
  - healthcheck `--max-age` 180 equals `DEFAULT_MAX_AGE_S` and is at least 3 heartbeat intervals (90 s);
  - the default child timeout is 3000 s, below 3600 s;
  - `/data/wbmon.sqlite3` is pinned identically in Dockerfile and compose, and the heartbeat lands at `/data/scheduler_heartbeat.json` on the `wbmon_data` volume shared by collector and web;
  - the web entrypoint resolves to `/app/src/wbmon/ui/app.py` under WORKDIR `/app`.

## Task Commits

1. **Task 1: terminated attempts in the orchestrator, SIGKILL -> abandoned** - `8943333` (feat)
2. **Task 2: SIGTERM wiring in collect, real-process proofs** - `1ce17ac` (feat)
3. **Task 3: packaging cross-checks and the cross-version gate** - `d1daa33` (test)

## Cross-version gate

| Interpreter | Result |
|-------------|--------|
| 3.14 (`.venv`) | 785 passed, 1 skipped, 16 deselected |
| 3.12 (throwaway venv, `pip install -e ".[dev]"`, `--ignore=tests/ui`) | 785 passed, 1 skipped, 16 deselected |
| 3.11 (same procedure) | 785 passed, 1 skipped, 16 deselected |

The throwaway venvs live in the session scratchpad, not in the repo.

## Decisions Made

See frontmatter.

## Deviations from Plan

None - the plan was executed as written.

- No Phase 1 test monkeypatched `wbmon.cli.run_once` with a signature that rejects `should_stop` (the one monkeypatch uses `*_args, **_kwargs`), so no fake needed widening.
- The real-process tests exposed no defect in `src/wbmon/scheduler.py`, so it was not modified.
- **Process:** each TDD task was committed once (tests and implementation together), as in plans 02-01 and 02-02, not as separate RED and GREEN commits.

## Issues Encountered

- `gsd-tools` is not on PATH here. State updates were done through `node ~/.claude/gsd-core/bin/gsd-tools.cjs` where possible.
- `.env.example` is still absent (the 02-02 deferral). No cross-check test requires it.

## Known Stubs

None.

## Threat Flags

None beyond the plan's threat model. Mitigations T-02-20 to T-02-26 are covered by tests:
- the run is never left `running`;
- no request is sent after a stop request;
- an interrupted slot is never re-run;
- SIGINT and handler restoration are asserted;
- packaging commands are parsed against the CLI;
- only `python -c` children are spawned;
- no frozen contract was touched (`git log` over storage, config and pager shows no Phase 2 commits).

## Next Phase Readiness

- Phase 2 is functionally complete. Remaining Phase 2 follow-ups are for Phase 4: a real `docker compose up -d` on the deploy host, and the README configuration table (OPS-02).
- Phase 3 UI can treat `error_class = 'terminated'` as a normal interrupted-attempt marker (a gap in the product history, not an observation).
- No live Wildberries/Google request, no Docker command and no `wbmon collect` / `wbmon scheduler` run happened. `src/wbmon/ui/`, `.streamlit/` and `tests/ui/` were not touched.

## Self-Check: PASSED

- Files exist: tests/test_orchestrator_terminate.py, tests/test_cli_terminate.py, tests/test_scheduler_process.py, tests/test_packaging_cross.py
- Commits exist: 8943333, 1ce17ac, d1daa33
- Full offline suite: 785 passed, 1 skipped on 3.14, 3.12 and 3.11; ruff check and format clean; guardrail grep clean; no `data/` directory
