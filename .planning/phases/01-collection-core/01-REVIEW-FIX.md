---
phase: 01-collection-core
fixed_at: 2026-10-16T00:00:00Z
review_path: .planning/phases/01-collection-core/01-REVIEW.md
iteration: 1
findings_in_scope: 14
fixed: 14
skipped: 0
status: all_fixed
---

# Phase 1: Code Review Fix Report

**Source review:** `.planning/phases/01-collection-core/01-REVIEW.md`
**Iteration:** 1

**Summary:**
- In scope: CR-01, WR-01..WR-05 (required) plus IN-01, IN-03, IN-04, IN-06, IN-07, IN-08 (listed items), IN-09
- Fixed: 14 findings in 14 commits (13 `fix(01)` and 1 `docs(01)` for IN-02)
- Deliberately not changed: IN-05 (slot retry policy, Phase 2); IN-02 semantics (comment only)
- Tests: 538 before, 598 after (each fix has a regression test); `ruff check src tests` and
  `ruff format --check src tests` clean.
- `models.ERROR_CLASSES` is unchanged (still 19 entries, so Phase 2's 19 to 20 plan is unaffected).
  No new error class was added.

## Fixed Issues

### CR-01: One out-of-range number aborts the entire run
**Commit:** 18b50dc
**Files:** `src/wbmon/normalize.py`, `src/wbmon/storage/writer.py`
**Fix:** `to_int` returns None outside the signed 64-bit range; `to_float` survives huge JSON
integers (OverflowError); `to_str` returns None for unpaired surrogates (the same failure class
at bind time); `delivery_hours` is NULL when `time1 + time2` would overflow. `record_query`'s
`db_write_failed` fallback now also catches `OverflowError` and `ValueError`. The
`_run_queries` guard from the review's suggestion is covered by WR-05.
**Tests:** unit tests on the coercers, a writer test for both failure kinds, and an end-to-end
run with `id: 10**30` and `feedbacks: 10**30` (confirmed to fail without the fix).

### WR-01: `to_int` raised ValueError for isdigit-but-unparseable strings
**Commit:** e58e72d
**Fix:** ASCII `-?[0-9]+` full match plus try/except around `int()`. `"²"`, `"--5"`, `"٣"`, `"1_0"`,
`"+5"` and 5000-digit strings now give NULL; `" 7 "` and `"007"` still parse.

### WR-02: Response size and total time were not really bounded
**Commit:** 187c2a2
**Files:** `src/wbmon/wb_client.py`, `src/wbmon/queries.py`
**Fix:** WB pages and the sheet are streamed through a shared `read_capped()`. It stops at cap + 1
bytes of *decoded* data (so gzip bombs are cut too). A body still arriving after 60 s (WB) or
30 s (sheet) is abandoned. Mapping onto existing classes: oversize gives `body_too_large` (WB) or
`too_large` (sheet fallback reason); the time cap gives `transport_error="timeout"`, which
classifies as `timeout`. Caveat: the time check runs between chunks, so a worst case can exceed
the cap by up to one socket-read timeout (30 s).
**Tests:** an endless/1 GiB stream stops after about 9 chunks, gzip bomb, slow drip (fake
clock), normal body unchanged, oversized sheet stops early.
**Test change:** `test_deadline_records_remaining_queries_without_requests` advanced the fake
clock by 1000 s inside one request; that is now correctly a per-request timeout, so it uses
50 s with `run_deadline=30`. The intent of the test is unchanged.

### WR-03: Extreme `--slot` crashed with a traceback
**Commit:** e852c11
**Fix:** `parse_slot` converts `OverflowError` into `ValueError`, so argparse exits 2.
**Tests:** `clock`-level and CLI-level cases for year 0001 and 9999.

### WR-04: Post-run failure flipped the exit code; crashes had no traceback
**Commit:** b1b8034
**Fix:** the summary re-query uses `connect_ro` and a `sqlite3.Error` degrades to a
`run_summary_unavailable` warning (exit stays 0). The crash handler also prints the traceback to
stderr (the key=value log line is truncated at 300 chars).
**Tests:** locked-database summary keeps exit 0; crash puts `Traceback` on stderr.

### WR-05: `db_write_failed` fallback was fragile
**Commit:** 3fd1c87 (logic change: **requires human verification**, see below)
**Files:** `src/wbmon/orchestrator.py`, `src/wbmon/storage/writer.py`
**Fix:**
- `_run_queries` wraps `record_query`: a failure (including a failing fallback) is logged as
  `query_write_failed` and the run continues. After `MAX_CONSECUTIVE_WRITE_FAILURES = 3`
  consecutive failures the original exception is re-raised so `run_once` marks the run `error`
  (a dead database does not burn 70 x 30 s of busy timeout).
- `finish_run` compares recorded rows with `runs.n_queries`; a run with unwritten queries can no
  longer end as `ok` (it becomes `partial`). Found while testing: without this a lost write was
  invisible in the run status.
- `enforce_result_invariants`: an `ok` result with no observations is recorded as
  `error` / `exception` with an explicit message, instead of being mislabelled `db_write_failed`.
  No new error class.
**Human check:** the threshold of 3 and the "missing rows make the run partial" rule are policy
choices. Confirm they fit Phase 2's expectations.

### IN-01: `nm_rating` 0 stored as 0.0
**Commit:** a5e9f34
**Fix:** 0 becomes NULL, as for `rating`. Two existing tests that encoded the old behaviour were
updated (`nm_rating=0.0` to `None`).

### IN-02: `wrap_subset` semantics (comment only)
**Commit:** 17e7a77
One comment in `pager.py` explains why an all-seen page means the end of results. No behaviour change.

### IN-03: A page-N failure hidden behind `schema_drift`
**Commit:** 709747f
**Fix:** when earlier pages yielded no usable rows and a later page failed, `collect_query` now
returns that page's verdict (for example `blocked` / `http_403`), so it feeds the circuit breaker.

### IN-04: `load_bundled` depended on LF endings
**Commit:** 0ac8f6d
**Fix:** `.gitattributes` (`* text=auto eol=lf` plus an explicit rule for `src/wbmon/data/*.txt`).
`split_bundled()` removes only the CR of a CRLF terminator; the query text stays byte-exact (the
pinned SHA-256 test is unchanged and passes). Tests cover a CRLF copy of the real file and 70
queries without `\r`.

### IN-06: `ensure_wal` hid the cause
**Commit:** 6ba6f0a
**Fix:** keeps the last `OperationalError` (`raise ... from last_exc`), pauses after every failed
attempt (including a non-raising non-WAL answer) and puts the reported `journal_mode` in the message.

### IN-07: Remote text stored unsanitised in `error_message`
**Commit:** e724f74
**Fix:** `classify.sanitize_text` turns every Unicode `C*` character plus U+2028/2029 into a space.
`describe` caps header values at 100 and the body prefix at 200 characters, and the writer enforces
a 500-character cap on every stored `error_message`, including the fallback message. HTML and
markdown are intentionally left as received, so **Phase 3 must still render `error_message`,
`name`, `brand` and `seller_name` as plain text** (no `unsafe_allow_html`).

### IN-08: Smaller hygiene items (only the three requested)
**Commit:** 732bbce
**Fix:**
- Sheet redirects are checked in a response hook before being followed: https only, host
  `docs.google.com` or `*.googleusercontent.com`. Anything else falls back to the bundled list
  with reason `fetch_failed:DisallowedRedirect`. The initial `WBMON_SHEET_CSV_URL` itself is
  operator-configured and not restricted.
- `probe --query ""` (or whitespace only) is a usage error (exit 2) and sends nothing.
- `Settings.dest` is normalised with `str(int(dest))`, so `" 5 "`, `"+5"` and `"1_0"` are sent and
  stored as `5`, `5`, `10`.
Not touched (not requested): duplicate `sheet_order` for removed queries, the `products` summary
expression readability.

### IN-09: CI hardening
**Commit:** e5dabb4
**Fix:** `permissions: contents: read`, a `concurrency` group with `cancel-in-progress`, matrix
3.11 to 3.14. Not done (not requested): pinning actions to SHAs and restricting `push` to the
default branch. A guardrail test checks the workflow text. The 3.13 and 3.14 jobs have not run
yet; they only start once the repo is on GitHub.

## Skipped Issues

None of the in-scope findings were skipped. IN-05 was left as is by instruction (Phase 2 owns the
slot retry policy).

## Process notes

- Work was done directly in the main working tree (no separate git worktree), as the
  instructions require staging only specific files next to other people's uncommitted planning
  files and a `.gitignore` edit. Every commit stages explicit paths only; `.gitignore` and the
  other untracked or modified planning files were not touched.
- The venv is Python 3.14; nothing was run on 3.11. New code uses only 3.11-compatible syntax
  (`removesuffix`, parenthesised `with`, no 3.12-only APIs).

---

_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
