---
phase: 01-collection-core
verified: 2026-10-16T18:00:00Z
status: passed
score: 5/5 must-haves verified
overrides_applied: 0
re_verification: false
gaps: []
deferred: []
human_verification: []
---

# Phase 1: Collection Core Verification Report

**Phase Goal:** One command performs a complete, honest collection run - 70 queries x top-200 from the public WB search JSON, in SERP order - and appends every result (including blocked/failed attempts) to an append-only SQLite history, verifiable entirely offline.
**Verified:** 2026-10-16
**Status:** passed
**Re-verification:** No - initial verification
**Mode:** mvp (the goal is a CLI pipeline, not a user story, so the user-story guard does not apply. The walking-skeleton adaptation D-A10 was honored.)

## Goal Achievement

### Observable Truths (ROADMAP success criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `init-db` creates a WAL SQLite DB with foreign keys and schema version and is re-runnable. The DB itself rejects UPDATE/DELETE on observations. Re-running `collect` only appends. | VERIFIED | `storage/migrations.py` has migration 1 (4 tables, 8 triggers) and `PRAGMA user_version`. `storage/db.py:connect_rw` sets WAL, `foreign_keys=ON`, `busy_timeout`, `recursive_triggers=ON`. In my own run (scratch script, tmp DB) `init-db` ran twice and returned 0 both times. Through `connect_rw`, UPDATE, DELETE and `INSERT OR REPLACE` on observations each raised `IntegrityError: observations is append-only`. A second `collect` left the first run's 600 rows intact and only added rows. `grep -rniE "OR REPLACE\|REPLACE INTO" src` is empty. |
| 2 | `collect` loads the 70 queries from the sheet or the bundled copy, records the source on the run, and query text reaches WB byte-for-byte. | VERIFIED | `queries.py:load_queries` does the sheet fetch with `utf-8-sig`, `csv.reader`, a 50..500 plausibility check, and a bundled fallback with a reason. `runs.query_source` is stored (my run: `bundled`, with the sheet answering 500). The bundled file has exactly 70 unique lines with the right special cases: `ё` x2, the «пастельный комплект 2-спальный» typo, Cyrillic «х» in 160х200 and 70х70, Latin «x» in 160x200. Text is never stripped and goes through httpx `params=`. `test_queries.py::test_query_reaches_wb_byte_for_byte` covers all 70 queries plus probe strings. `test_bundled_queries.py` pins the file byte-for-byte. |
| 3 | Offline replay of both envelope shapes, multi-page, ads and missing fields stores position (1-based across pages, ads flagged), nm_id, price, rating, reviews, delivery days and seller. Unknown fields are NULL. Collection stops at top_n, an empty page, or a wrap-around. | VERIFIED | `normalize.py` (pure) and `pager.py` (pure state machine, stop reasons top_n/empty/wrap/wrap_subset/cap). In my mocked run, page 1 used `data.products` and page 2 used top-level `products`. 200 rows per query, positions 1..200, an unpriced item gave `price_rub` NULL, rating 4.5, feedbacks, delivery_days 1 and seller stored. Fixtures cover 4 shapes. 26 normalize tests, 26 pager tests and 21 collector tests pass. Only `ad` flagging was not exercised in my script because of a bug in my scratch script. It is covered by the committed tests (`test_normalize.py`, `test_collector.py`). |
| 4 | On 403/498/429/HTML-at-200/5xx/null/empty the run completes, each query has an attempt row, failed queries write no observations, the breaker opens after 3 consecutive blocked queries, and an unfinished run becomes `abandoned` on the next start. | VERIFIED | `classify.py` is a decision table that runs before parsing. `orchestrator.py` has `BREAKER_THRESHOLD = 3`, a deadline and per-query isolation. `writer.record_query` writes the attempt row and observations in one `BEGIN IMMEDIATE` transaction. In my all-403 run: exit 0, run `blocked`, 3 requests sent, rows `http_403` x3 then `circuit_open` x3, 0 new observations. A manually inserted `running` run became `abandoned` on the next `collect`. 26 classify tests, 18 orchestrator tests and 24 wb_client tests pass. |
| 5 | `pytest` passes fully offline using fixtures and temp DBs only. `wbmon probe` prints the classified outcome of one real request. | VERIFIED | Run by me: `pytest -q` gives 538 passed, 16 deselected. `pytest -q -m slow` gives 16 passed in 8.8 s (the 2.35M-row STOR-04 proof on a temp DB). `ruff check .` is clean and `ruff format --check src tests` is clean. `tests/test_guardrails.py` blocks the network inside tests and asserts the runtime deps are exactly httpx + tzdata. `cli._probe` sends one request with `retry=False`, writes nothing, and prints a JSON summary. `test_probe.py` has 7 tests. The live probe was not run by me, as instructed. Per `01-05-SUMMARY.md`, the orchestrator recorded `blocked / http_403 / server wbaas`, exit 0. |

**Score:** 5/5 truths verified

### Required Artifacts

| Artifact | Status | Details |
|----------|--------|---------|
| `src/wbmon/{config,clock,models,log,queries,wb_client,classify,normalize,delivery,pager,collector,orchestrator,cli,__main__}.py` | VERIFIED | All exist and are substantive (2391 lines in total with storage). No stubs. |
| `src/wbmon/storage/{db,migrations,writer,reader,lock}.py` | VERIFIED | Schema, triggers, atomic per-query writer, `abandon_stale_runs`, 8 STOR-04 read paths and an flock run lock. |
| `src/wbmon/data/queries_bundled.txt` | VERIFIED | 70 unique queries, UTF-8, shipped as package data. |
| `pyproject.toml` | VERIFIED | Deps are httpx + tzdata. `web` and `dev` extras are declared up front. The `wbmon` script entry exists. |
| `.github/workflows/ci.yml` | VERIFIED | Python 3.11 and 3.12 matrix, `ruff check`, offline pytest. |
| `tests/` (19 modules, 4 fixtures) | VERIFIED | 554 tests collected, 538 default + 16 slow. |

### Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `cli.collect` | `orchestrator.run_once` | direct call, passes `trigger`, `slot` and `limit_queries` | WIRED |
| `run_once` | `run_lock`, `abandon_stale_runs`, `load_queries`, `start_run`, `finish_run` | one ExitStack scope | WIRED |
| `_run_queries` | `collect_query` -> `WbClient.fetch_page` -> `classify_page` -> `Pager` -> `normalize_product` | pipeline order is classify before parse | WIRED |
| `_run_queries` | `Writer.record_query` | one transaction per query, including synthetic breaker and deadline rows | WIRED |
| `Settings.search_params` | httpx `params=` | `query` is passed unmodified | WIRED |

### Data-Flow Trace (Level 4)

| Artifact | Data | Source | Real data | Status |
|----------|------|--------|-----------|--------|
| `observations` rows | `ObservationRow` | `normalize_product(slot.raw)` from the classified payload | Yes. My mocked run read back the values from SQLite. | FLOWING |
| `runs` / `run_queries` | counters and status | `finish_run` aggregates the recorded rows | Yes | FLOWING |

### Behavioral Spot-Checks (offline only, tmp DB, no network)

| Behavior | Result | Status |
|----------|--------|--------|
| 3 queries x 2 pages (mixed envelopes) | 600 observations, positions 1..200, run `ok` | PASS |
| DB-level UPDATE / DELETE / REPLACE rejection | `IntegrityError: ... append-only` x3 | PASS |
| All-403 run | 3 requests, 6 `blocked` rows (3 `circuit_open`), 0 observations, exit 0 | PASS |
| Stale `running` run | marked `abandoned` on the next collect | PASS |
| `pytest -q` | 538 passed | PASS |
| `pytest -m slow` | 16 passed (STOR-04 < 1 s on 2.35M rows) | PASS |
| `ruff check .` / `ruff format --check src tests` | clean | PASS |

Step 7c (probes): SKIPPED, because the phase declares no `probe-*.sh` scripts.

### Requirements Coverage

The union of the `requirements:` frontmatter of the five PLANs is 24 IDs. This equals the 24 IDs that ROADMAP and REQUIREMENTS.md map to Phase 1. There are no orphaned IDs.

| Requirement | Source plan(s) | Status | Evidence |
|-------------|----------------|--------|----------|
| QRY-01 | 01-04 | SATISFIED | `queries.py` sheet CSV with `utf-8-sig`, `csv.reader`, redirects followed |
| QRY-02 | 01-04 | SATISFIED | no strip or normalization, httpx `params=`, 70-query wire test |
| QRY-03 | 01-01, 01-04 | SATISFIED | bundled copy, plausibility check, `runs.query_source` |
| QRY-04 | 01-01, 01-03 | SATISFIED | `upsert_queries`, `queries_no_delete` and `queries_text_immutable` triggers |
| COL-01 | 01-04 | SATISFIED | `search.wb.ru` v18, fixed params, plain httpx, 1.5 s delay |
| COL-02 | 01-02, 01-05 | SATISFIED | Pager, 200 positions across 2 pages in my run |
| COL-03 | 01-04, 01-05 | SATISFIED | `classify.py`, bounded retries (2.0 s then 5.0 s), Retry-After at most once |
| COL-04 | 01-05 | SATISFIED | per-query isolation, breaker at 3 (my all-403 run: 3 requests) |
| COL-05 | 01-01, 01-04 | SATISFIED | deps are httpx + tzdata, no cookies, `trust_env=False`, no redirects followed, guardrail tests |
| COL-06 | 01-05 | SATISFIED | `run_queries` columns: status, http_status, pages, items, is_complete, normquery, error, duration |
| DATA-01 | 01-01, 01-02 | SATISFIED | observation columns |
| DATA-02 | 01-02 | SATISFIED | min over sizes of `price.product / 100`, audit columns, NULL when unpriced (verified in my run) |
| DATA-03 | 01-02 | SATISFIED | rating 0 -> NULL, feedbacks, nm_* extras |
| DATA-04 | 01-02 | SATISFIED | raw time1/time2, pure `delivery_days()` in MSK, 8 tests |
| DATA-05 | 01-02 | SATISFIED | supplier, supplierId, name, brand |
| DATA-06 | 01-02 | SATISFIED | `is_promoted` plus 4 ad audit fields |
| DATA-07 | 01-02 | SATISFIED | pure normalizer, all-NULL when unknown, fixtures for both shapes |
| STOR-01 | 01-01, 01-03 | SATISFIED | WAL, busy_timeout, FK, 4 tables, idempotent `init-db` |
| STOR-02 | 01-01, 01-03 | SATISFIED | triggers, `recursive_triggers`, no REPLACE in src |
| STOR-03 | 01-03, 01-05 | SATISFIED | one transaction per query, `abandon_stale_runs` under the lock |
| STOR-04 | 01-03 | SATISFIED | indexes and 8 read paths, slow test passes (< 1 s on 2.35M rows) |
| SCHED-01 | 01-01, 01-05 | SATISFIED | `collect` does one full run and exits 0/1/2 |
| SCHED-04 | 01-01, 01-05 | SATISFIED | key=value log events, quoted, on stdout |
| OPS-04 | 01-01, 01-05 | SATISFIED | offline suite, CI workflow, network blocked in tests |

REQUIREMENTS.md marks all 24 `[x]`. It has no Phase 1 requirement left Pending.

### CONTEXT Decision Compliance (incl. addendum D-A1..D-A10)

All honored in code:
- D-A1: `subject_id` and `root_id` columns exist.
- D-A2: `recursive_triggers=ON`, and REPLACE is rejected (verified).
- D-A3: explicit `BEGIN IMMEDIATE`, autocommit or `isolation_level=None` by Python version.
- D-A4: `trust_env=False`, cookie policy that rejects all cookies, no redirects followed (3xx -> `blocked/redirect`).
- D-A5: the breaker counts blocked or rate_limited.
- D-A6: run deadline, default 2400 s.
- D-A7: flock lock, `lock_busy` and `slot_exists` both exit 0.
- D-A9: User-Agent URL placeholder.
- D-A10: walking skeleton present.
- The honest UA, fixed delay with no jitter, and config via a frozen dataclass with no pydantic were all verified.

### Anti-Patterns Found

None. `grep` for TBD/FIXME/XXX/TODO/HACK in `src`, `tests`, `.github` and `pyproject.toml` is empty. There are no circumvention packages and no `OR REPLACE`.

### Notes (non-blocking, informational)

- The ROADMAP Progress table still shows "1. Collection Core | 0/5 | Planned". The phase checkbox and all five plan checkboxes are `[x]`. This is a stale bookkeeping row and should be updated when the phase is closed.
- `ruff format --check .` at the repo root reports 5 files. They are markdown files under `.planning/`, not code. `ruff format --check src tests` is clean, and CI only runs `ruff check`.
- I did not run `wbmon collect` or `wbmon probe` against the network, as instructed. The live-probe claim (`blocked/http_403`, server `wbaas`, exit 0) rests on the orchestrator's recorded evidence plus the offline probe tests. A `blocked` result is explicitly an accepted outcome in ROADMAP SC5.
- Live confirmation of the endpoint, price semantics and delivery formula is intentionally deferred to Phase 4 (ROADMAP research flags).

### Human Verification Required

None.

### Gaps Summary

No gaps. All 5 success criteria and all 24 Phase 1 requirement IDs are backed by code that exists, is wired, and was exercised offline by me. I ran the full suite (538 passed), the slow STOR-04 test (16 passed), lint, and an independent mocked end-to-end run.

---

_Verified: 2026-10-16_
_Verifier: Claude (gsd-verifier)_
