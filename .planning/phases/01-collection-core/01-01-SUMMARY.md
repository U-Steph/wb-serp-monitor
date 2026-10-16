---
phase: 01-collection-core
plan: 01
subsystem: collector-core
tags: [walking-skeleton, python, sqlite, httpx, cli, pytest]

requires: []
provides:
  - installable `wbmon` package (hatchling, src layout, console script `wbmon`, `python -m wbmon`)
  - frozen contracts (models.py, config.py, clock.py, log.py) and frozen schema migration 1
  - "`wbmon init-db` (WAL, user_version 1, 8 append-only/immutability triggers)"
  - "`wbmon collect --limit-queries N --top-n N` thin page-1 pipeline with run_summary read-back"
  - offline test harness (tests/conftest.py, tests/factories.py FakeWB) shared by later plans
  - 70 bundled queries pinned by sha256
affects: [01-02, 01-03, 01-04, 01-05, phase-02, phase-03]

tech-stack:
  added: [httpx>=0.28.1,<1, tzdata, hatchling (build), pytest, ruff (dev)]
  patterns:
    - functional core (clock, models, normalize are I/O-free) with imperative shell
    - one BEGIN IMMEDIATE transaction per query, plain INSERT only (no OR REPLACE)
    - injectable sleep/clock/transport seams for fully offline tests
    - key=value structured log lines with quoted values

key-files:
  created:
    - pyproject.toml
    - .gitignore
    - src/wbmon/{__init__,__main__,config,clock,models,log,queries,wb_client,normalize,collector,orchestrator,cli}.py
    - src/wbmon/storage/{__init__,db,migrations,writer,reader}.py
    - src/wbmon/data/queries_bundled.txt
    - tests/{conftest,factories,test_guardrails,test_bundled_queries,test_skeleton_e2e,test_contracts}.py
  modified: []

key-decisions:
  - "Kept `data/` in .gitignore and re-included the package data with `!src/wbmon/data/`"
  - "finish_run refuses to close a run that is not in the running state (RuntimeError) instead of silently updating nothing"
  - "Orchestrator closes the run with status error and the exception text if the query loop raises, so a run is never left running by a crash inside the loop"
  - "pytest default run excludes the slow marker via addopts `-m \"not slow\"`"

patterns-established:
  - "Unknown is explicit: unmapped observation columns are NULL, never 0"
  - "Tests derive paths from Path(__file__).resolve().parents[1] because the autouse fixture chdirs to tmp_path"
  - "CLI test seams: cli.main(argv, _transport=..., _sleep=...)"

requirements-completed: [QRY-03, QRY-04, DATA-01, STOR-01, STOR-02, SCHED-01, SCHED-04, OPS-04, COL-05]

duration: 30min
completed: 2026-10-16
---

# Phase 1 Plan 01: Walking Skeleton Summary

**Installable `wbmon` package whose `init-db` creates the frozen WAL schema (v1, 8 append-only triggers) and whose `collect --limit-queries 1` replays one WB page through `httpx.MockTransport` into 1 run, 1 run_query and 5 observations, then prints a run_summary read back from SQLite.**

## Performance

- **Duration:** about 30 min
- **Completed:** 2026-10-16
- **Tasks:** 3 of 3
- **Files:** 27 created (src 19 incl. data file, tests 6, pyproject, .gitignore)

## Accomplishments

- Frozen contracts and schema: `ObservationRow` (27 columns, order equals the table after `id/run_id/query_id`, includes `subject_id`/`root_id`), `Settings` with validated `WBMON_*` env vars, migration 1 with `queries/runs/run_queries/observations`, four indexes and eight triggers; `recursive_triggers=ON` so a REPLACE conflict cannot bypass the delete trigger (verified by test).
- Offline walking skeleton: `tests/test_skeleton_e2e.py` proves init-db idempotency, the one-query collect (ad slot at position 2 flagged `is_promoted=1`, pinned params, honest UA, no cookie header) and that a second collect appends without touching the first run's rows.
- Guardrails: runtime dependencies are exactly `httpx` and `tzdata`; circumvention denylist, forbidden source strings (`OR REPLACE`, `verify=False`, `x_wbaas`, ...), network and DNS blocked in tests, tmp cwd and tmp DB path enforced by an autouse fixture.
- Bundled 70 queries match sha256 `2cf16309...ecda5e` (3984 bytes) with special-character lines asserted exactly.
- Verification: 71 tests pass offline on Python 3.14 and on a throwaway Python 3.11 venv; `ruff check` and `ruff format --check` are clean; `wbmon init-db` smoke run against a mktemp path exits 0 with `event=init_db schema_version=1`.

## Task Commits

1. **Task 1: scaffold, offline harness, bundled queries, failing e2e test (RED)** - `f07dddb` (test)
2. **Task 2: init-db slice, contracts, config, clock, log, schema** - `4c553da` (feat)
3. **Task 3: collect slice, page-1 pipeline to append-only rows** - `eef5419` (feat)

**Plan metadata:** recorded in the docs commit that adds this file.

## Files Created/Modified

- `pyproject.toml`, `.gitignore` - packaging, tooling and ignore rules
- `src/wbmon/config.py`, `clock.py`, `models.py`, `log.py` - frozen shared contracts
- `src/wbmon/storage/{db,migrations,writer,reader}.py` - connections, schema v1, atomic writer, read-back
- `src/wbmon/{queries,wb_client,normalize,collector,orchestrator,cli}.py` - thin collection path and CLI
- `src/wbmon/data/queries_bundled.txt` - the 70 queries
- `tests/` - harness (`conftest.py`, `factories.py`) and four test modules

## Decisions Made

See key-decisions above. In addition the settings floor `request_delay >= 0.5 s` and `top_n <= 1000` are enforced in `Settings.__post_init__` so `with_overrides` validates too.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `.gitignore` pattern `data/` also ignored the package data directory**
- **Found during:** Task 1 commit (`git add src/wbmon/data/queries_bundled.txt` was refused)
- **Issue:** the plan-mandated line `data/` matches `src/wbmon/data/` as well as the runtime `./data/` folder
- **Fix:** kept the `data/` line (acceptance criterion) and appended `!src/wbmon/data/`
- **Files modified:** `.gitignore`
- **Commit:** `f07dddb`

**2. [Rule 2 - Missing critical functionality] Run left in `running` state if the query loop raised**
- **Found during:** Task 3 (orchestrator)
- **Fix:** the loop is wrapped so an unexpected exception closes the run as `error` with the exception text and re-raises; `Writer.finish_run` raises `RuntimeError` when the run is not `running` rather than silently updating zero rows
- **Files modified:** `src/wbmon/orchestrator.py`, `src/wbmon/storage/writer.py`
- **Commit:** `eef5419`

**3. [Rule 1 - Bug] Ruff modernisation and a self-tripping guardrail**
- **Found during:** Tasks 2 and 3
- **Issue:** a docstring in `db.py` and `writer.py` contained the text `OR REPLACE`, which the guardrail test (correctly) rejects; ruff `UP` rewrote `timezone.utc` to `datetime.UTC` (valid on 3.11)
- **Fix:** reworded the docstrings; accepted the ruff rewrite
- **Commits:** `4c553da`, `eef5419`

Otherwise the plan was executed as written. The PyPI metadata check passed before install (ruff project URLs reference github.com/astral-sh/ruff, hatchling's reference github.com/pypa/hatch).

## Known Stubs

Intentional skeleton limits, each resolved by a named later plan (they do not block this plan's goal):

- `src/wbmon/normalize.py` `normalize_product`: only identity, name, brand and the promoted flag are mapped; every other observation column is NULL (field mapping: plan 01-02).
- `src/wbmon/queries.py` `load_queries`: bundled list only, reason `skeleton_bundled_only` (Google Sheet source: plan 01-04).
- `src/wbmon/collector.py`: page 1 only, any unusable page is a plain `error` result without `error_class` (pagination and classifier: plans 01-02 and 01-04).
- `src/wbmon/wb_client.py`: single attempt, `retry` flag accepted but unused (plan 01-04).
- `src/wbmon/orchestrator.py`: no breaker, deadline, lock, slot or abandon handling (plan 01-05).

## Issues Encountered

None beyond the deviations above. No live request to Wildberries or Google was made; `wbmon collect` was never run from a shell, and no `data/` directory exists in the repo.

## Threat Flags

None. No new network endpoint, auth path or trust boundary beyond the plan's threat model (T-01-01..T-01-08, T-01-SC mitigations are in place and covered by `test_guardrails.py` and `test_contracts.py`).

## Next Phase Readiness

Plans 01-02, 01-03 and 01-04 can now run in parallel on disjoint files using the frozen `models.py`, `config.py`, `migrations.py` and `tests/factories.py`; plan 01-05 builds on the orchestrator and CLI.

## Self-Check: PASSED
