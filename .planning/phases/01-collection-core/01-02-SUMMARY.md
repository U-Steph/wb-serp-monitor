---
phase: 01-collection-core
plan: 02
subsystem: normalizer-pager
tags: [normalizer, delivery-days, pagination, pure-core, fixtures, pytest]

requires:
  - phase: 01-collection-core/01
    provides: frozen ObservationRow, clock.parse_iso_z, to_int/to_float/to_str, FakeWB harness
provides:
  - complete WB product -> ObservationRow mapping (all 27 columns) with explicit NULLs
  - pure delivery_days() (Europe/Moscow calendar-day difference)
  - pure Pager state machine and Slot for top-N pagination (COL-02)
  - four test-only JSON fixtures and three test modules
affects: [01-05, phase-02]

tech-stack:
  added: []
  patterns:
    - "price audit columns (basic/total/logistics) always come from the same size as the buyer price"
    - "delivery_days is re-derivable from stored collected_at and delivery_hours"
    - "position = raw slot index; duplicates and id-less items leave gaps instead of renumbering"

key-files:
  created:
    - src/wbmon/delivery.py
    - src/wbmon/pager.py
    - tests/fixtures/search_data_products.json
    - tests/fixtures/search_top_level_products.json
    - tests/fixtures/search_legacy_prices.json
    - tests/fixtures/search_missing_fields.json
    - tests/test_delivery.py
    - tests/test_normalize.py
    - tests/test_pager.py
  modified:
    - src/wbmon/normalize.py
    - tests/test_contracts.py

key-decisions:
  - "collected_at is parsed unconditionally in normalize_product; a malformed timestamp is a caller bug and raises ValueError rather than silently yielding NULL delivery_days"
  - "Ad slot mismatch is counted only for accepted (non-duplicate) slots"
  - "Negative time1/time2 are kept raw in time1_h/time2_h but delivery_hours and delivery_days become NULL"

patterns-established:
  - "Pure modules (normalize, delivery, pager) are import-scanned against PURE_FORBIDDEN_IMPORTS in their own tests"
  - "Fixture rows are compared as full dataclasses.asdict dicts against an expected table"

requirements-completed: [DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07, COL-02]

duration: 25min
completed: 2026-10-16
---

# Phase 1 Plan 02: Normalizer Fields, Delivery Days, Pager Summary

**`wbmon collect` now stores real prices (cheapest priced size, kopecks to rubles), rating, reviews, seller, ad fields and MSK calendar-day delivery for every product with NULL for anything unavailable, and a pure, exhaustively tested top-N pager exists for plan 01-05 to wire in.**

## Performance

- **Duration:** about 25 min
- **Completed:** 2026-10-16
- **Tasks:** 2 of 2
- **Files:** 9 created, 2 modified

## Accomplishments

- `normalize_product` maps all 27 `ObservationRow` columns. Buyer price is the minimum `price.product` over priced sizes (ties: first), with basic/total/logistics from that same size. The legacy `salePriceU`/`priceU` fallback applies only when no size is priced and leaves total/logistics NULL. `reviewRating` 0 or missing becomes NULL while `feedbacks` 0 is kept. Delivery uses product-level `time1`/`time2`, falling back to the fastest complete size. Ad fields are filled only for a non-empty `log` object (cpm 0 stays 0). Opaque WB fields are never read.
- `delivery.py`: `delivery_days(collected_at_utc, hours, tz=MSK)` adds hours in UTC and compares local dates; bool, negative, non-int and over-`MAX_HOURS` hours give `None`; a naive or non-UTC datetime raises `ValueError`. All 18 numeric research vectors plus the None, negative, overflow and misuse rows pass.
- `pager.py`: `Pager` and `Slot` implement the COL-02 rules. Positions are the 1-based slot index across pages in raw order with ads included. Stops are `top_n`, `empty`, `wrap` (first-10-id fingerprint, at least 3 ids), `wrap_subset` (every id already seen) and `cap` (`ceil(top_n/page_size)+1`). A short page never ends the query. Duplicates are dropped with a gap, and ad `promoPosition` is compared against both the global slot and the page-relative index.
- Four test-only fixtures (`"_test_only": true`, fake ids 9000000xx, ТЕСТ names) cover data.products, top-level products, legacy prices and missing fields. Both envelope shapes normalise to identical rows. A CLI vertical slice (`cli.main(["collect", ...])` through `MockTransport` into a tmp DB) confirms stored columns equal the expected table.

## Task Commits

1. **Task 1: fixtures, delivery_days, complete normalizer** - `55e20ee` (feat)
2. **Task 2: pure pagination state machine** - `fde48c1` (feat)

**Plan metadata:** recorded in the docs commit that adds this file.

## Verification

- Full offline suite: 185 passed; `ruff check src tests` and `ruff format --check src tests` clean.
- `normalize.py`, `delivery.py`, `pager.py` import no `httpx`, `sqlite3`, `socket`, `os`, `time` or `subprocess` (asserted by tests).
- No live request to Wildberries or Google was made; `wbmon collect` and `wbmon probe` were never run from a shell.

## Decisions Made

See key-decisions above.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] 01-01 skeleton test asserted the stub behaviour this plan replaces**
- **Found during:** Task 1 (full suite run)
- **Issue:** `tests/test_contracts.py::test_normalize_product_unknown_stays_null` expected every unmapped column to be NULL, including `ad_cpm` for `{"log": {"cpm": 0}}`, and passed `collected_at="t"` to a row-producing call.
- **Fix:** the test now expects `ad_cpm == 0` (cpm 0 stays 0) and uses a valid timestamp for the empty-`log` assertion. The intent (unknown stays NULL) is unchanged.
- **Files modified:** `tests/test_contracts.py` (outside this plan's `files_modified`, required to keep the suite green)
- **Commit:** `55e20ee`

**2. [Process] Tests and implementation were committed together instead of a separate RED commit**
- The plan tasks are `tdd="true"` but the plan type is `execute`; tests were written alongside the implementation and passed on the first run. No separate failing-test commit exists for tasks 1 and 2.

Otherwise the plan was executed as written.

## Known Stubs

None. The `Pager` is not yet wired into `collector.collect_query` (the collector still fetches page 1 only); that wiring is plan 01-05 by design and `collector.py` was intentionally not edited.

## Issues Encountered

None. A pre-existing unstaged edit to `.gitignore` (adds `.gstack/`) is not part of this plan and was left untouched.

## Threat Flags

None. No network code, endpoints or trust boundaries were added; threat mitigations T-01-09 to T-01-13 are covered by the fixture, vector and pager tests.

## Next Phase Readiness

Plan 01-05 can drive `Pager.add_page` from the collector and read `slots`, `stop_reason`, `is_complete`, `duplicates_dropped`, `skipped_no_id` and `ad_slot_mismatch`; each `Slot` carries its page's `received_at`, which the collector passes to `normalize_product` as `collected_at`.

## Self-Check: PASSED
