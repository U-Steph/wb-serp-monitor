---
phase: 01-collection-core
plan: 05
subsystem: orchestration
tags: [pagination, circuit-breaker, deadline, run-lock, slot, cli, e2e, ci, exit-codes]

requires:
  - phase: 01-collection-core/02
    provides: pure Pager state machine, normalize_product, delivery days
  - phase: 01-collection-core/03
    provides: Writer.record_query / finish_run / abandon_stale_runs, run_lock, reader.run_id_for_slot
  - phase: 01-collection-core/04
    provides: classify_page, polite WbClient with bounded retries, query sources, probe
provides:
  - "collect_query: multi-page top-200 collection driven by Pager + classify_page, later-page failure stored as honest 'partial'"
  - "run_once: whole-run flock, abandon_stale_runs under the lock, slot idempotency, circuit breaker (BREAKER_THRESHOLD = 3), run deadline, per-query exception isolation, BaseException run marking, key=value events"
  - "wbmon collect --trigger {manual,scheduler} --slot ISO with the final exit-code contract (0 / 2 / 1)"
  - "21 end-to-end CLI tests incl. a full 70-query run and an all-blocked run; .github/workflows/ci.yml (ruff + offline pytest on 3.11/3.12)"
affects: [phase-02, phase-03, phase-04]

tech-stack:
  added: []
  patterns:
    - "orchestrator keeps the lock, DB connection and HTTP client in one ExitStack/with scope; run_once returns None for every 'nothing to do' outcome (lock_busy, slot_exists)"
    - "synthetic QueryResults (circuit_open, deadline, exception) go through the same writer.record_query path as real ones, so every query of a run has exactly one attempt row"
    - "tests that call run_once directly call configure_logging('INFO') inside the test body (a handler bound in a fixture points at a closed pytest stream)"

key-files:
  created:
    - tests/test_collector.py
    - tests/test_orchestrator.py
    - tests/test_cli.py
    - .github/workflows/ci.yml
  modified:
    - src/wbmon/collector.py
    - src/wbmon/orchestrator.py
    - src/wbmon/cli.py
    - tests/test_classify.py

key-decisions:
  - "pages_fetched is Pager.pages_seen (successful pages incl. the wrap/empty page that ended the SERP); a failed page is never counted"
  - "breaker counts the collected status (not the stored one) and only for queries that actually sent requests; deadline takes priority over an open breaker"
  - "a lost slot UNIQUE race (IntegrityError in start_run) is logged as slot_exists and returns None, like the pre-check"
  - "the breaker/deadline/exception synthetic rows carry started_at = now and duration_ms = 0 so no attempt row has NULLs"

patterns-established:
  - "events: queries_loaded, run_start, query_done (one per query, 70 per full run), breaker_open (once), deadline (once), lock_busy, slot_exists, abandoned, run_done, run_summary, crash, product_skipped"
  - "FakeWB scripts: (query, page) queues; callables can advance an injected SteppingClock to simulate a slow WB for the deadline test"

requirements-completed: [COL-02, COL-03, COL-04, COL-06, STOR-03, SCHED-01, SCHED-04, OPS-04]

duration: 30min
completed: 2026-10-16
---

# Phase 1 Plan 05: Full Honest Run Summary

**`wbmon collect` now performs the complete run of the phase goal: every query is paginated to top-200 through the pure Pager, failures are isolated per query, a blocking WB is left alone after 3 consecutive refusals, the run is bounded by a deadline, protected by a whole-run lock, and recoverable (`abandoned`), with the exact CLI contract Phase 2's scheduler will call.**

## Performance

- **Duration:** about 30 min
- **Completed:** 2026-10-16
- **Tasks:** 3 of 3
- **Files:** 4 created, 4 modified

## Accomplishments

- **Multi-page collection (COL-02).** `collect_query` loops `client.fetch_page` / `classify_page` / `pager.add_page` until `pager.done`. Positions are 1-based across pages with ads included; 2 x 100 gives positions 1..200 with exactly 2 requests and one 1.5 s pacing sleep. Wrap (page 2 == page 1), an empty page 2, a short page followed by another page (97 items then 3), a duplicate id (gap at its position, `duplicates_dropped`), ad `promoPosition` vs global/page-relative slot (`ad_slot_mismatch`), per-page `collected_at` with `delivery_days` recomputed from it (page 1 lands after MSK midnight in the test), `top_n` smaller than a page, both envelope shapes and `product_skipped` are all covered by 22 tests.
- **Honest partial results.** A 403 or double-429 on page 2 keeps page 1's 100 rows as `partial` with `error_class` http_403 / http_429, `http_status`, `pages_fetched = 1`, `is_complete = 0`. A failure on page 1 stores no observations (blocked after 1 request, rate_limited after 2, error/http_5xx after 3 with sleeps [2.0, 5.0], empty_suspect, products_null, schema_drift).
- **Unattended-safe orchestration (COL-04, STOR-03, SCHED-04).** `run_once` takes `run_lock(db_path)` for the whole run (busy: `lock_busy` WARNING, return `None`, no run, no abandon, no network), calls `abandon_stale_runs` only under the lock (`abandoned count=N`), honours `--slot` (`slot_exists`, including the IntegrityError race), opens the circuit after `BREAKER_THRESHOLD = 3` consecutive blocked/rate_limited queries (remaining 67 recorded `blocked/circuit_open`, exactly 3 WB requests, one `breaker_open` event, 70 `query_done` events, run `blocked`), records `error/deadline` for every query after `WBMON_RUN_DEADLINE` without a request, converts a per-query exception into an `error/exception` row ("RuntimeError: boom") and keeps going, keeps going after a `db_write_failed` fallback row, and marks the run `error` with the exception text before re-raising a `KeyboardInterrupt`.
- **Final CLI contract (SCHED-01).** `collect --trigger {manual,scheduler} --slot ISO` (scheduler requires a slot; slots normalised by `clock.parse_slot`, e.g. `2026-10-16T12:34:56+03:00` becomes `2026-10-16T09:00:00Z`); exit 0 for a completed run of any status (even all blocked) or a lock/slot skip, 2 for configuration/usage errors (`WBMON_TOP_N=abc`, `--top-n 0`, `--limit-queries 0`, naive/garbage slot, `--trigger probe`), 1 for a crash with an `event=crash` line.
- **End-to-end proofs.** Offline full 70-query run (70 ok rows, 350 observations, 70 requests, 69 pacing sleeps of 1.5), all-blocked run (exit 0, run `blocked`, 3 requests, 67 `circuit_open`, 0 observations), multi-page run (400 observations, positions 1..200 per query, prices and delivery days populated, `normquery` "тест запрос"), crash recovery (stale `running` run becomes `abandoned`), append-only re-run (first-run rows identical after the second), slot idempotency and lock busy.
- **CI (OPS-04).** `.github/workflows/ci.yml`: job `test` on ubuntu-latest, Python 3.11 and 3.12 matrix, `pip install -e ".[dev]"`, `ruff check src tests`, `pytest -q`; no secrets, slow perf test stays opt-in.

## Task Commits

1. **Task 1: multi-page top-200 collect_query with honest partial results** - `92c9401` (feat)
2. **Task 2: unattended-safe run orchestration** - `5e06944` (feat)
3. **Task 3: final wbmon collect CLI contract, e2e proofs and CI workflow** - `b8b919a` (feat)

Each TDD task was committed as one feat commit (tests and implementation together); there are no separate RED commits.

## Verification

- Local `.venv` (Python 3.14.6): `pytest -q` 538 passed, 16 deselected (slow); `pytest -q -m slow tests/test_storage_perf.py` 16 passed in 8.5 s; `ruff check src tests` and `ruff format --check src tests` clean.
- Throwaway venvs outside the repo (`pip install -e ".[dev]"` from PyPI): Python 3.11.15 538 passed, Python 3.12.13 538 passed.
- `wbmon collect --help` lists `--db`, `--top-n`, `--limit-queries`, `--trigger`, `--slot`; `python -m wbmon --help` lists init-db, collect, probe.
- `grep -rn "OR REPLACE\|REPLACE INTO\|x_wbaas\|verify=False" src/` prints nothing; `grep "shuffle\|random" src/wbmon/orchestrator.py` prints nothing; the repo has no `data/` directory.
- No live request to Wildberries or Google was made by this plan; `wbmon collect`/`probe` were never run from a shell; all synthetic rows exist only in `tmp_path` databases.

## Probe Evidence (single manual `wbmon probe`, run by the orchestrator from the dev machine)

`{"outcome": "blocked", "error_class": "http_403", "http_status": 403, "server": "wbaas", "attempts": 1}`, exit 0. The plain, honest-identity client is classified as `blocked/http_403` by WB's `wbaas` edge from this machine, as anticipated. The system records this honestly (`blocked`, `circuit_open` after 3 queries) and does not try to get around it. The go/no-go decision about where the collector can actually run (for example the target VPS) belongs to Phase 4.

## Decisions Made

See key-decisions in the frontmatter.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Earlier 01-04 test assumed page-1-only collection**
- **Found during:** Task 1
- **Issue:** `tests/test_classify.py::test_collect_products_without_integer_ids_is_schema_drift` scripted only page 1 with 3 id-less items and `top_n=5`; with the Pager a short page is never an end signal, so page 2 is (correctly) requested and the unscripted request failed. The test also expected `pages_fetched == 0`, but the page itself was fetched successfully.
- **Fix:** the test now uses `top_n=3` (Pager stops after page 1) and asserts `pages_fetched == 1`. Production behaviour follows the plan (`schema_drift` / "no product with an integer id").
- **Files modified:** `tests/test_classify.py`
- **Commit:** `92c9401`

### Plan-vs-reality notes

- Tasks are `tdd="true"` but the plan type is `execute`; tests and implementation were committed together per task (no separate RED commits), as in plans 01-03 and 01-04.
- `QueryResult.pages_fetched` for a successful run counts the wrap/empty page that ended the SERP (Pager.pages_seen), matching the plan's `pages_fetched 2` for "page 2 identical" and "page 2 empty".
- The cross-version venvs (3.11, 3.12) were created under the session scratchpad directory rather than a `mktemp` dir in `/tmp`; the repo was not touched by them.

## Known Stubs

None.

## Threat Flags

None. Mitigations T-01-35..T-01-43 are implemented and covered by tests: exact request counts for the breaker (3 of 70), deadline without requests, lock/slot idempotency, abandon only under the lock, BaseException marking, append-only re-run, parse_slot/argparse validation, offline-only fixtures, and no new dependency.

## Next Phase Readiness

Phase 2 can call `python -m wbmon collect --trigger scheduler --slot <ISO>` as a fresh subprocess and read the outcome from the DB (exit 0 also for lock_busy / slot_exists / all-blocked). Phase 3 can read the finished schema and the eight reader paths. Phase 4 must decide, with a real `wbmon probe` from the target host, whether the collector can obtain data there; the system records `blocked` honestly either way.

## Self-Check: PASSED

- Files exist: `src/wbmon/collector.py`, `src/wbmon/orchestrator.py`, `src/wbmon/cli.py`, `tests/test_collector.py`, `tests/test_orchestrator.py`, `tests/test_cli.py`, `.github/workflows/ci.yml`
- Commits `92c9401`, `5e06944`, `b8b919a` exist in `git log`
