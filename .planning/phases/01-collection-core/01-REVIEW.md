---
phase: 01-collection-core
reviewed: 2026-10-16T00:00:00Z
depth: standard
files_reviewed: 23
files_reviewed_list:
  - src/wbmon/__init__.py
  - src/wbmon/__main__.py
  - src/wbmon/classify.py
  - src/wbmon/cli.py
  - src/wbmon/clock.py
  - src/wbmon/collector.py
  - src/wbmon/config.py
  - src/wbmon/delivery.py
  - src/wbmon/log.py
  - src/wbmon/models.py
  - src/wbmon/normalize.py
  - src/wbmon/orchestrator.py
  - src/wbmon/pager.py
  - src/wbmon/queries.py
  - src/wbmon/wb_client.py
  - src/wbmon/storage/__init__.py
  - src/wbmon/storage/db.py
  - src/wbmon/storage/lock.py
  - src/wbmon/storage/migrations.py
  - src/wbmon/storage/reader.py
  - src/wbmon/storage/writer.py
  - pyproject.toml
  - .github/workflows/ci.yml
findings:
  critical: 1
  warning: 5
  info: 9
  total: 15
status: issues_found
---

# Phase 1: Code Review Report

**Reviewed:** 2026-10-16
**Depth:** standard
**Files Reviewed:** 23
**Status:** issues_found

## Summary

The collection core is in good shape overall. SQL is fully parameterised, and the only dynamic SQL
is the capped `IN (?,?,...)` list and constant column names. The append-only triggers,
`recursive_triggers`, per-query `BEGIN IMMEDIATE` transactions, flock-based run lock and log
quoting are sound. There is no anti-bot circumvention: `trust_env=False`, a cookie-rejecting policy
and `follow_redirects=False` for WB all hold. The offline suite (538 tests) passes and
`ruff check` is clean.

The main defect is a robustness hole between the normalizer and the writer. One out-of-range
number in a WB payload escapes both the per-query exception guard and the writer's fallback and
aborts the whole run. I reproduced this with a MockTransport run: the run was closed as `error`,
0 `run_queries` rows were written and the remaining queries were lost. A few smaller input-hardening
gaps sit on the same boundary. Nothing was found that violates the "NULL instead of invented
values" or append-only invariants.

## Critical Issues

### CR-01: One out-of-range number in a WB payload aborts the entire run (OverflowError / InvalidOperation escape the guards)

**File:** `src/wbmon/storage/writer.py:139` (also `src/wbmon/normalize.py:18-28,92-97`, `src/wbmon/orchestrator.py:238`)
**Issue:** `to_int` returns any Python int, including values above 2^63. A product `id`, `feedbacks`, `supplierId` etc. of that size gets through `normalize_product`. The INSERT then raises `OverflowError: Python int too large to convert to SQLite INTEGER`.

`OverflowError` is not a `sqlite3.Error` (verified), so:

1. `record_query` only catches `sqlite3.Error`, so its `db_write_failed` fallback never runs.
2. `_run_queries` calls `writer.record_query(...)` outside any try/except. Only `collect_query` is guarded.
3. The exception propagates to `run_once`, which calls `_mark_run_failed` and re-raises.

Net effect: every remaining query in the run is silently lost, the run is closed as `error`, and the CLI exits 1. Reproduced with a MockTransport run (`limit_queries=3`, first query returns `id: 10**30`): one run with status `error`, `OverflowError: ...` and 0 `run_queries` rows.

The same class of problem exists in `rub()`. For a kopeck amount of 10**30, `Decimal(k) / 100` followed by `quantize` raises `decimal.InvalidOperation` (28-digit context). That one is at least caught by the per-query guard (see WR-01). It still turns a bad price into a lost query, not a NULL.

This is a data-loss path triggered by a single remote value. The project rule is that unknown or malformed values become NULL.

**Fix:**
```python
# normalize.py: bound ints to what SQLite can store, otherwise "unknown"
_INT64_MAX = 2**63 - 1

def to_int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        result = v
    elif isinstance(v, float):
        if not v.is_integer():
            return None
        result = int(v)
    elif isinstance(v, str):
        try:
            result = int(v.strip())   # no isdigit() pre-check, see WR-01
        except ValueError:
            return None
    else:
        return None
    return result if -_INT64_MAX <= result <= _INT64_MAX else None
```
`rub()` should also guard `decimal.InvalidOperation` (or rely on the bounded `to_int` above, since an int64 divided by 100 fits the Decimal context).

Defence in depth in `writer.record_query`: widen the fallback to `except (sqlite3.Error, OverflowError, ValueError)`. In `_run_queries`, wrap `record_query` so that a failed write for one query cannot abort the rest of the run.

## Warnings

### WR-01: `to_int` raises ValueError for strings that pass `isdigit()` but are not parseable

**File:** `src/wbmon/normalize.py:26-27`
**Issue:** `v.strip().lstrip("-").isdigit()` is not a safe pre-check for `int(v)`. `"²".isdigit()` is True but `int("²")` raises `ValueError`. `"--5"` also passes (`lstrip("-")` strips both dashes) and `int("--5")` raises. Both reproduced. The exception escapes `normalize_product` and `collect_query`. The orchestrator then records the whole query as `error/exception`, so every good product of that query is discarded because of one odd field. Conversely, non-ASCII digit strings such as `"٣"` are accepted as 3.
**Fix:** Drop the `isdigit()` pre-check and use try/except around `int(v.strip())`. Optionally restrict to `re.fullmatch(r"-?[0-9]+", s)` so Unicode digits become NULL. See the code in CR-01.

### WR-02: Response size and total request time are not actually bounded

**File:** `src/wbmon/wb_client.py:128-140`, `src/wbmon/classify.py:112`, `src/wbmon/queries.py:86-91`
**Issue:** `MAX_BODY_BYTES` (8 MiB) and `SHEET_MAX_BYTES` (1 MiB) are checked only after `client.get()` has buffered, and gunzipped, the complete body in memory. They therefore do not prevent a memory spike from a huge or compressed response. The httpx `Timeout(30, connect=10)` is per socket operation, not total. A server that drips one byte every 29 s keeps a request open indefinitely, and the run deadline is only checked between queries.
**Fix:** Use `client.stream("GET", ...)`, accumulate `iter_bytes()` and abort once the cap is exceeded, mapping that to `body_too_large` / `too_large`. Add a wall-clock bound per request, for example by checking `monotonic()` inside the streaming loop or passing a total-time deadline.

### WR-03: `--slot` with an extreme date crashes with a traceback instead of a usage error

**File:** `src/wbmon/clock.py:50`, `src/wbmon/cli.py:45-52,126-131`
**Issue:** `parse_slot("0001-01-01T00:00:00+05:00")` raises `OverflowError` at `astimezone(UTC)` (verified; `9999-12-31T23:00:00-05:00` fails too). `_slot` only translates `ValueError`. argparse does not catch `OverflowError`, and `main` only catches `SystemExit` around `parse_args`. The result is an uncaught traceback, not exit 2 with a message.
**Fix:** In `parse_slot` (or `_slot`), catch `(ValueError, OverflowError)` and re-raise as `ValueError`.

### WR-04: A failure after a successful run flips the exit code to 1, and crashes carry no traceback

**File:** `src/wbmon/cli.py:158-169`
**Issue:** After `run_once` returns, the CLI reopens the DB with `connect_rw` just to print the summary. If that fails (locked, FS error), the generic handler logs `crash` and exits 1 even though the run was recorded successfully. `run_once` already logged `run_done`, so this second summary is redundant. The `crash` handler also logs only `Type: message`. For an unattended service, a traceback (stderr or log) is needed to diagnose production failures.
**Fix:** Use `connect_ro` (or drop the re-query) and wrap the summary in its own `try/except sqlite3.Error`. In the crash handler add `traceback.print_exc(file=sys.stderr)` or log `traceback.format_exc()` as a field.

### WR-05: The `db_write_failed` fallback is more fragile than its docstring suggests

**File:** `src/wbmon/storage/writer.py:139-153`
**Issue:** The fallback runs the same INSERT again with only a few fields changed. If the first failure was `database is locked` or `disk I/O error`, the second write very likely fails the same way and the exception propagates, aborting the run (documented only for the duplicate-row case). Separately, the fallback reuses `params["status"]` semantics. If `result.status` was `ok` with 0 observations, the CHECK failure is recorded as `db_write_failed` rather than as the real bug (a collector invariant violation).
**Fix:** In `_run_queries`, wrap `record_query` so an exception from the fallback is logged and the loop continues or exits cleanly with the run marked `error`. Consider validating `ok => items_collected > 0` before the transaction and raising a distinct error class.

## Info

### IN-01: `nm_rating` of 0 is stored as 0.0 while `rating` of 0 becomes NULL

**File:** `src/wbmon/normalize.py:190-192,212`
**Issue:** Per decision 2.2 `reviewRating` 0 means "no rating" and is NULLed. `nmReviewRating` follows the same WB convention, but a literal 0 is stored. If it behaves like `reviewRating`, this is an invented value that will chart as a real zero.
**Fix:** Apply the same `== 0 -> None` rule, or document why `nm_rating` differs.

### IN-02: `wrap_subset` marks a query complete although `top_n` was not reached

**File:** `src/wbmon/pager.py:21,88-91`
**Issue:** A later page whose ids are all already in `_seen` (for example a page of repeated ads) stops paging and counts as complete (`is_complete=1`) even if fewer than `top_n` slots were covered. This could overstate coverage. It is low probability and it is the specified behaviour for wrap.
**Fix:** Consider `is_complete` only for `top_n` / `empty` / `wrap` and log `wrap_subset` separately, or record the stop reason.

### IN-03: A page-N failure after a page-1 batch of id-less products hides the real error

**File:** `src/wbmon/collector.py:80-89`
**Issue:** When `rows` is empty the result is `error/schema_drift` and the `failure` verdict (for example a page-2 403) is dropped. This is an edge case, but it hides a block signal that should feed the breaker.
**Fix:** Prefer the `failure` verdict's `error_class` and outcome when `failure is not None`.

### IN-04: `load_bundled` depends on LF line endings

**File:** `src/wbmon/queries.py:26-31`
**Issue:** The file is split on `"\n"` with no `.gitattributes`. A Windows checkout with `core.autocrlf=true` would leave a trailing `\r` on every query. The query text is the data (QRY-02), and the `\r` would be sent to WB and stored. The 70-count is also not enforced at runtime.
**Fix:** Add `.gitattributes` with `src/wbmon/data/*.txt text eol=lf`, and add a test or assert for 70 non-empty lines without `\r`.

### IN-05: A crash-abandoned scheduler slot is never retried

**File:** `src/wbmon/orchestrator.py:65-72`
**Issue:** `abandon_stale_runs` runs before the slot check. The abandoned run keeps `slot_utc`, so the same `--slot` is then skipped as `slot_exists`. This matches D-A7 as written, but it means a hard crash costs that hour with no retry, even if the process is restarted within the same hour.
**Fix:** Document it, or exclude `abandoned` / `error` runs from the slot check. That would need a partial UNIQUE index or clearing `slot_utc`, which conflicts with immutability.

### IN-06: `ensure_wal` hides the underlying error

**File:** `src/wbmon/storage/db.py:35-48`
**Issue:** If every attempt raises `OperationalError`, it ends with `RuntimeError("could not switch database to WAL")` and loses the cause. After a non-raising non-WAL result (for example a filesystem without shared memory) it loops 20 times without a pause.
**Fix:** Keep the last exception and `raise RuntimeError(...) from last_exc`. Sleep on every failed attempt.

### IN-07: Remote text is stored unsanitised in `error_message`

**File:** `src/wbmon/classify.py:53-60`, `src/wbmon/storage/writer.py:149`
**Issue:** `describe` stores up to 200 characters of the remote body and header values. Control characters are scrubbed, but HTML or markdown is not. Phase 3 must render `error_message`, `name`, `brand` and `seller_name` as plain text only (no `unsafe_allow_html`).
**Fix:** Note the constraint in the Phase 3 plan.

### IN-08: Smaller hygiene items

**Files:** `src/wbmon/config.py:95`, `src/wbmon/storage/reader.py:51-57`, `src/wbmon/cli.py:98,108`, `src/wbmon/queries.py:61-68`
**Issue:**
- `int(self.dest)` accepts `" 5 "`, `"+5"` and `"1_0"`, but the raw string is what gets sent. Normalise with `str(int(dest))`.
- A query removed from the sheet keeps its old `sheet_order`, so `list_queries` can contain duplicate orders (ties broken by id).
- `probe --query ""` sends an empty query, because the check is `is not None`.
- The `products` summary expression relies on operator precedence (`len(x) or None if ... else None`) and is hard to read.
- The sheet client follows up to 5 redirects to any host or scheme (it is env-configurable only; low risk).
**Fix:** Tidy as noted.

### IN-09: CI hardening and coverage

**File:** `.github/workflows/ci.yml`, `src/wbmon/storage/db.py:2`
**Issue:**
- The workflow has no `permissions:` block (default token scope) and uses action tags rather than SHAs.
- `on: push` plus `on: pull_request` runs every PR twice.
- The matrix covers 3.11 and 3.12, but `db.py` claims "3.11 to 3.14 safe" and the local venv is 3.14, which is never tested in CI.
**Fix:** Add `permissions: {contents: read}`, add `"3.13"` and `"3.14"` to the matrix, and restrict `push` to the default branch.

---

_Reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
