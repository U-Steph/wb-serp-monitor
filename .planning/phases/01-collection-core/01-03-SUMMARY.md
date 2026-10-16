---
phase: 01-collection-core
plan: 03
subsystem: storage
tags: [sqlite, append-only, durability, run-lock, performance, flock]

requires:
  - phase: 01-collection-core/01
    provides: frozen schema v1 with append-only triggers, connect_rw/connect_ro, Writer skeleton, models
provides:
  - "append-only, CHECK/FK/UNIQUE, migration and read-only guarantees proven through connect_rw/connect_ro and the real collect path"
  - "Writer.record_query with atomic per-query transaction and visible error/db_write_failed fallback row"
  - "Writer.abandon_stale_runs and ABANDONED_ERROR; finish_run RuntimeError guard for non-running runs"
  - "wbmon.storage.lock.run_lock / LockBusy (non-blocking flock on {db_path}.lock)"
  - "eight STOR-04 reader paths plus a slow 2,352,000-row performance proof"
affects: [01-05, phase-02, phase-03]

tech-stack:
  added: []
  patterns:
    - "a failed per-query write is rolled back completely and replaced by a single error/db_write_failed run_queries row"
    - "dynamic SQL limited to the IN (?, ...) placeholder list (cap 500); readers return dicts from cursor.description"
    - "perf test captures the SQL actually executed with set_trace_callback and runs EXPLAIN QUERY PLAN on it"

key-files:
  created:
    - src/wbmon/storage/lock.py
    - tests/test_storage_schema.py
    - tests/test_storage_writer.py
    - tests/test_storage_reader.py
    - tests/test_storage_perf.py
  modified:
    - src/wbmon/storage/writer.py
    - src/wbmon/storage/reader.py

key-decisions:
  - "db.py needed no change: the 01-01 recipe already matched RESEARCH (recursive_triggers ON, autocommit/isolation_level split, as_uri() + mode=ro); the mode=ro fallback was not needed because the awkward-path read-only test passes on macOS/3.14"
  - "latest_characteristics selects the row whose id equals the newest observation per (query_id, nm_id) (collected_at DESC, id DESC) instead of comparing collected_at to MAX(), so exactly one row per id is guaranteed even on timestamp ties"
  - "the perf test asserts a USING [COVERING] INDEX plan and no SCAN of observations/run_queries for every path except list_queries (70-row table scan by design)"

patterns-established:
  - "Subprocess-based tests (init race, lock holder) use sys.executable and never touch the network"
  - "Module-scoped tmp_path_factory DB for read-only tests; perf DB removed in a finally block"

requirements-completed: [STOR-01, STOR-02, STOR-03, STOR-04, QRY-04]

duration: 25min
completed: 2026-10-16
---

# Phase 1 Plan 03: Durable Append-Only Storage Summary

**The history store is now provably append-only and crash-safe: the database rejects every rewrite through the collector's own connection, each query is written atomically with a visible `db_write_failed` fallback, crashed runs become `abandoned`, a flock-based run lock exists, and eight index-backed read paths answer in at most 26 ms on 2,352,000 generated rows.**

## Performance

- **Duration:** about 25 min
- **Completed:** 2026-10-16
- **Tasks:** 3 of 3
- **Files:** 5 created, 2 modified (`db.py` and `migrations.py` untouched)

## Accomplishments

- **Schema guarantees (45 tests).** Through `connect_rw`: UPDATE/DELETE of observations, `ON CONFLICT DO UPDATE`, `INSERT OR REPLACE` with a conflicting key, DELETE of runs/run_queries/queries, UPDATE of run_queries, UPDATE of a finished run and renaming a query all raise `IntegrityError` and leave every table unchanged; `sheet_order` updates still work. A control test documents the REPLACE hazard on a bare connection (D-A2). CHECK/FK/UNIQUE matrix (17 cases, each asserting the constraint type), Cyrillic vs Latin / yo vs ye / NFC vs NFD query texts stay distinct, the injection string is stored verbatim, migration idempotency, refusal of a newer DB, rollback of a failing migration, nested parent directories and 6 concurrent `init_db` processes all pass. `connect_ro` reads from a `Тест папка #?.` directory and rejects writes. Rows written by the real `cli.main(["collect", ...])` path cannot be updated or deleted.
- **Writer (20 tests).** `record_query` runs in one `BEGIN IMMEDIATE`; on any `sqlite3.Error` it rolls back and writes a single `error` / `db_write_failed` row (message at most 200 chars, http_status/pages/started_at/duration copied). A duplicate (run, query) pair raises and keeps the first row. `finish_run` derivation covers ok/partial/blocked/error/empty and raises `RuntimeError("run N is not running")` on a finished run. `abandon_stale_runs(now)` converts running runs to `abandoned` with `ABANDONED_ERROR`, `finished_at`, and counters from recorded queries; finished runs are untouched and a second call returns 0.
- **Run lock.** `run_lock` takes a non-blocking exclusive `flock` on `{db_path}.lock` (parent dir created, file never deleted). A second holder gets `LockBusy`; a child process holding the lock blocks the parent until the child is killed with SIGKILL, after which the parent acquires it.
- **STOR-04 readers (12 functional tests + 16 slow tests).** `list_queries`, `nm_ids_for_query`, `positions_for`, `latest_characteristics`, `run_log`, `snapshot`, `latest_ok_run_id`, `run_id_for_slot`, all working on a `connect_ro` connection, all values bound, `MAX_IN_IDS = 500`.

## Performance Results (slow test, `pytest -m slow`, macOS, Python 3.14.6, SQLite 3.50.4)

Generated DB: 70 queries x 168 hourly runs x 200 positions = 2,352,000 observations in 8.3 s (whole slow module: about 9 s). Each path timed once on a fresh `connect_ro` connection:

| Path | Time |
|------|------|
| list_queries | 0.3 ms |
| nm_ids_for_query (450 groups) | 25.7 ms |
| positions_for (5 ids) | 1.0 ms |
| latest_characteristics (5 ids) | 0.4 ms |
| run_log (100) | 0.4 ms |
| snapshot (200 rows) | 1.1 ms |
| latest_ok_run_id | 0.3 ms |
| run_id_for_slot | 0.2 ms |

Limit is 1.0 s per path (about 40x margin). `EXPLAIN QUERY PLAN` of the SQL actually executed contains `USING INDEX`/`USING COVERING INDEX` and no `SCAN observations`/`SCAN run_queries` for every path except `list_queries`. The tmp DB and its `-wal`/`-shm` files are deleted in a `finally` block; the pytest temp area was 148 KB afterwards.

## Task Commits

1. **Task 1: schema guarantees through connect_rw/connect_ro** - `f5bbb33` (test)
2. **Task 2: atomic writes, abandon_stale_runs, run lock** - `b6e60c7` (feat)
3. **Task 3: STOR-04 read paths and perf proof** - `3c96dc9` (feat)

**Plan metadata:** recorded in the docs commit that adds this file.

## Verification

- Default suite: 262 passed, 16 deselected (the slow module) in about 0.6 s; `pytest -m slow tests/test_storage_perf.py`: 16 passed in 8.5 s (run twice; the first run exposed a wrong slot constant in the test, see below).
- `ruff check src tests` and `ruff format --check src tests` clean.
- `git diff --stat src/wbmon/storage/migrations.py` empty; `db.py` unchanged; `grep -c "ON CONFLICT" writer.py` is 1 (queries upsert only).
- No network access, no live Wildberries/Google request; `wbmon collect`/`probe` were never run from a shell; synthetic rows exist only in tmp DBs.

## Decisions Made

See key-decisions above.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Wrong slot constant in my perf test**
- **Found during:** Task 3 first slow run
- **Issue:** run 100 starts 99 hours after 2026-10-01T00:00:00Z, which is 2026-10-05T03:00:00Z; I had written 2026-10-04T03:00:00Z (run 76)
- **Fix:** corrected the constant; no production code involved
- **Files modified:** `tests/test_storage_perf.py`
- **Commit:** `3c96dc9`

### Plan-vs-reality notes

- Task 1 step 1 (review `db.py`): no drift found and no edit made, so `db.py` is not in a commit of this plan.
- The `finish_run` guard already existed from 01-01; this plan only aligned the message to `run N is not running` and extracted the status derivation into `Writer._derive_status` (shared with `abandon_stale_runs`).
- `latest_characteristics` deviates from the plan's literal "collected_at equals correlated MAX" shape in favour of an id-based correlated subquery (same index, guaranteed single row; see key-decisions).
- Tasks are `tdd="true"` but the plan type is `execute`; tests and implementation were committed together (Task 1 added tests only because the guarantees already existed). No separate RED commits for Tasks 2 and 3.

## Known Stubs

None.

## Issues Encountered

None beyond the test constant above. An unrelated pre-existing unstaged edit to `.gitignore` was left untouched.

## Threat Flags

None. T-01-16..T-01-23 mitigations are implemented and covered by the tests above; no new network surface was added. The lock file `{db_path}.lock` is a new file in the DB directory and is documented in `lock.py`.

## Next Phase Readiness

Plan 01-05 can wrap `collect` in `run_lock(settings.db_path)`, call `abandon_stale_runs` right after acquiring it, and use `reader.run_id_for_slot` for slot idempotency. Phase 3 can build the UI directly on the eight reader functions.

## Self-Check: PASSED
