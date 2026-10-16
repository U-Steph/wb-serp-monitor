---
phase: 01-collection-core
plan: 04
subsystem: wb-access
tags: [httpx, classification, retry-after, google-sheet, probe, no-circumvention]

requires:
  - phase: 01-collection-core/01
    provides: frozen models/config contracts, NoCookiesPolicy, make_http_client, WbClient skeleton, FakeWB harness, bundled queries
  - phase: 01-collection-core/02
    provides: normalize.extract_products / payload_shape / extract_normquery
provides:
  - "wbmon.classify: pure classify_page(), parse_retry_after(), describe(), MAX_BODY_BYTES, DEFAULT_RETRY_AFTER, RETRY_AFTER_CAP"
  - "collect_query classifies page 1 before parsing; blocked / rate_limited / error attempts are recorded with error_class, http_status and a diagnostic error_message and no observations"
  - "WbClient.fetch_page(..., retry=True): fixed pacing, RETRY_BACKOFF (2.0, 5.0) for transport errors and 5xx, one capped Retry-After retry for 429, no retry for 403/498/3xx/4xx"
  - "wbmon probe [--query TEXT]: one page-1 request, one JSON line, no database, exit 0 whatever the outcome"
  - "wbmon.queries: Google Sheet CSV source (parse_csv, make_sheet_client, load_queries) with plausibility check 50..500 rows and bundled fallback with a recorded reason"
affects: [01-05, phase-02, phase-03]

tech-stack:
  added: []
  patterns:
    - "classify first, parse second: every HTTP outcome becomes a PageVerdict (outcome + error_class + message) in a pure module before any JSON is trusted"
    - "describe() is the only source of error_message: status, server, content type, attempts, transport and a 200-char control-character-free body prefix; never headers wholesale or cookies"
    - "retries are an explicit status-specific loop in the client (no tenacity); probe reuses the client with retry=False"
    - "load_queries never raises; every sheet failure maps to a fallback reason string (http_<n>, too_large, decode_error, csv_error, implausible_count=<n>, fetch_failed:<Exc>)"

key-files:
  created:
    - src/wbmon/classify.py
    - tests/test_classify.py
    - tests/test_wb_client.py
    - tests/test_probe.py
    - tests/test_queries.py
  modified:
    - src/wbmon/collector.py
    - src/wbmon/wb_client.py
    - src/wbmon/cli.py
    - src/wbmon/queries.py

key-decisions:
  - "PageFetch.elapsed_ms and received_at describe the final attempt only; attempts carries the number of requests sent for the page"
  - "429 wait uses an explicit None check so Retry-After: 0 sleeps 0 s instead of falling back to the 30 s default"
  - "collector maps a defensive 'end' verdict on page 1 to status error (cannot occur because page 1 empty is empty_suspect), so QueryResult never carries an invalid status"
  - "empty/duplicate sheet rows are removed before the 50..500 plausibility count, so a sheet of 80 identical rows is implausible_count=1"
  - "wb_client.py now imports the retry constants and parser from wbmon.classify (pure module), keeping a single definition of the Retry-After rules"

patterns-established:
  - "Test seams: build_wb_client(FakeWB, settings, sleep=RecordingSleep()) proves pacing and retries by request counts and recorded sleeps, with no real waiting"
  - "Wire-encoding test parametrised over the real 70 queries plus 12 hostile strings, asserting both url.params round-trip and percent-decoded UTF-8 bytes"

requirements-completed: [QRY-01, QRY-02, QRY-03, COL-01, COL-03, COL-05]

duration: 25min
completed: 2026-10-16
---

# Phase 1 Plan 04: Honest WB Access Summary

**Pure response classifier, polite bounded-retry WB client, Google Sheet query source with bundled fallback, and a no-write `wbmon probe`, so a 403 from WB is recorded as `blocked/http_403` with diagnostics instead of an anonymous error.**

## Performance

- **Duration:** about 25 min
- **Completed:** 2026-10-16
- **Tasks:** 3 of 3
- **Files modified:** 9 (5 created, 4 modified)

## Accomplishments

- `classify_page` implements the full matrix: 403/498/any 3xx/HTML-at-200 are `blocked`, 429 is `rate_limited`, 5xx/timeout/connection/4xx/bad JSON/schema drift/products null/oversized body are `error`, and an empty page 1 is `empty_suspect` (never "no results"); an empty page 2+ is `end`. `parse_retry_after` passes all 12 RESEARCH vectors.
- `wbmon collect` now stores blocked / rate_limited / error attempts with `error_class`, `http_status` and `error_message` (`http=403 server=wbaas ctype=... attempts=1 transport=- body=...`) and writes no observations for them; a CLI-level test shows a run with one ok, one 403 and one HTML-at-200 query ending as `partial`.
- The WB client stays honest and polite: bot UA, no Origin/Referer/cookies, `trust_env=False` (proven with `HTTPS_PROXY` set), redirects returned not followed, fixed delay before every request except the first, no jitter, at most 2 transient retries (sleeps 2 s then 5 s), one 429 retry after `min(Retry-After or 30, 60)` s, and no retry of 403/498/3xx/4xx. All proven by request counts and recorded sleeps.
- Queries load from the Google Sheet CSV (redirects followed, max 5, utf-8-sig, `csv.reader`, no strip/normalise, de-duplicated), with fallback to the bundled 70 queries for HTTP errors, oversize, bad encoding, implausible counts and any exception; `runs.query_source` records `sheet` or `bundled`.
- Query wording reaches WB byte-for-byte: the wire test runs over 82 strings (70 real queries + 12 hostile strings incl. NFD, Cyrillic x vs Latin x, `%`, `*`, `&=?#`), asserting no `%25D` double encoding.
- `wbmon probe [--query TEXT]` sends exactly one page-1 request with `retry=False`, prints one JSON line with the classified outcome, creates no database file or `data/` directory, and exits 0 for a blocked result (2 on configuration error).

## Task Commits

1. **Task 1: Honest outcome classification wired into `wbmon collect`** - `34fbda7` (feat)
2. **Task 2: Polite client with bounded retries, identity tests and `wbmon probe`** - `8cbdc34` (feat)
3. **Task 3: Google Sheet query source with bundled fallback and byte-exact wording** - `7d1b074` (feat)

Each TDD task was committed as one feat commit (tests and implementation together, tests written against the specified behaviour); there are no separate RED commits.

## Files Created/Modified

- `src/wbmon/classify.py` - pure classifier, Retry-After parser, one-line diagnostic builder
- `src/wbmon/collector.py` - page-1 verdict from `classify_page`, `schema_drift` when no product has an integer id
- `src/wbmon/wb_client.py` - `RETRY_BACKOFF`, pacing, bounded status-specific retry loop, `_send`
- `src/wbmon/cli.py` - `probe` subcommand
- `src/wbmon/queries.py` - `parse_csv`, `make_sheet_client`, sheet-backed `load_queries`, `SHEET_*` limits
- `tests/test_classify.py` (73 cases incl. skeleton e2e rerun), `tests/test_wb_client.py`, `tests/test_probe.py`, `tests/test_queries.py` (111 cases)

## Decisions Made

See `key-decisions` in the frontmatter. Notably: elapsed/received_at describe the final attempt; `Retry-After: 0` is honoured as 0 s; sheet rows are de-duplicated before the plausibility count.

## Deviations from Plan

None - plan executed exactly as written. (The optional manual `wbmon probe` human-check was skipped by instruction; the orchestrator runs it separately. No live request to WB or Google was made.)

## Issues Encountered

None.

## Known Stubs

None. Collection is still page 1 only (by plan); plan 01-05 adds pages 2..N, the circuit breaker and the deadline.

## Threat Flags

None. The new network surface (Google Sheet fetch, WB retries) is covered by the plan's threat model (T-01-26..T-01-34); `trust_env=False`, `NoCookiesPolicy`, size caps and redirect limits are all enforced and tested.

## Verification

- `.venv/bin/python -m pytest -q`: 477 passed, 16 deselected (slow marker)
- `ruff check src tests` and `ruff format --check src tests`: clean
- `wbmon --help` lists `probe`; `grep random src/wbmon/wb_client.py` is empty; `follow_redirects=True` appears only in `queries.py`

## Next Phase Readiness

Plan 01-05 can add pages 2..N on top of `collect_query` using `classify_page(fetch)` per page (`end` verdict for an empty page 2+), the circuit breaker (3 consecutive `blocked`), the run deadline and the run lock. Pending human check: one `wbmon probe` from the dev machine to record the real WB classification (expected `blocked/http_403`, server wbaas).

## Self-Check: PASSED

- Files: `src/wbmon/classify.py`, `tests/test_classify.py`, `tests/test_wb_client.py`, `tests/test_probe.py`, `tests/test_queries.py` exist
- Commits `34fbda7`, `8cbdc34`, `7d1b074` exist in `git log`
