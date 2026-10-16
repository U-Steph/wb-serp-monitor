---
phase: 03-web-ui
reviewed: 2026-10-16T18:30:00Z
depth: standard
files_reviewed: 12
files_reviewed_list:
  - src/wbmon/ui/__init__.py
  - src/wbmon/ui/app.py
  - src/wbmon/ui/data.py
  - src/wbmon/ui/series.py
  - src/wbmon/ui/charts.py
  - src/wbmon/ui/texts.py
  - .streamlit/config.toml
  - tests/test_guardrails.py
  - tests/ui/dbbuilder.py
  - src/wbmon/storage/db.py
  - CLAUDE.md
  - .planning/phases/03-web-ui/03-CONTEXT.md
findings:
  critical: 1
  warning: 6
  info: 6
  total: 13
status: issues_found
---

# Phase 3: Code Review Report

**Reviewed:** 2026-10-16
**Depth:** standard
**Files Reviewed:** 12
**Status:** issues_found

## Summary

The read-only guarantees hold up. All reads go through `connect_ro` (`mode=ro` plus `query_only`) in short-lived `contextlib.closing` connections. No write path exists in `src/wbmon/ui`. SQL is constant strings with bound parameters. The only dynamic piece is the `?` placeholder list, which is capped at `MAX_IN_IDS`. Untrusted strings never reach `st.markdown`. Plotly names, hover text and tick labels go through `html.escape`. Timezone handling is correct: UTC strings are parsed strictly, then converted to naive MSK. The FOUND/ABSENT/UNKNOWN classification and the dedupe logic are sound. `ruff check`, `ruff format --check` and `pytest tests/ui tests/test_guardrails.py` all pass (about 410 tests). The code parses with `feature_version=(3,11)`.

One defect breaks the "never invent anything" rule. The run-grid SQL predicate that decides which runs "should have looked at" a query draws failure bands and UNKNOWN points for runs that predate a query. I reproduced it on a temporary DB: after one query is inserted at the top of the sheet, the new query gets 6 false "сбор не удался" bands. The same happens for a query that left the sheet and kept its old `sheet_order`. The remaining findings are robustness and UX issues in the banner, heartbeat, widget state and CSV paths.

## Critical Issues

### CR-01: `RUN_GRID_SQL` fabricates failed-run bands for runs that predate or postdate a query

**File:** `src/wbmon/ui/data.py:77-86` (also `src/wbmon/ui/series.py:231-243`)
**Issue:** A run without an attempt row for the query is included in the grid when `r.status <> 'running' AND :sheet_order <= r.n_queries`. `sheet_order` is not a stable identity. `Writer.upsert_queries` rewrites it on every run, and a query that vanished from the sheet keeps its last `sheet_order`. Two consequences:

1. A query inserted in the middle or at the top of the sheet gets a low `sheet_order`. Every historical run has `n_queries >= sheet_order` and no row for it, so every old run becomes a grid point with `q_status='missing'`, `complete=False`, state UNKNOWN, plus a grey band. The chart then claims the collection failed on days when the query did not exist, and `CHART_SUMMARY` counts these as "без данных (сбор не удался)".
2. A query removed from the sheet keeps its old `sheet_order`. Every new run (`n_queries` about 70) has no row for it, so each one adds a false band forever.

Reproduction on a temp DB (scratchpad `exp1.py`): `scenario_mixed`, then a manual run with the new query first. `load_run_grid_raw(new_query)` returned 7 runs, 6 with `q_status <NA>`, and `band_times` returned 6 bands for a query that had one attempt.

Missing rows are legitimate only when the query was part of the run's plan, which is not stored. A finished run with fewer rows than planned (writer `missing > 0`) and an abandoned run are the real cases. Bound the "missing" rows to the span in which the query was actually being collected.
**Fix:**
```sql
WHERE r.started_at >= :since
  AND (
    rq.id IS NOT NULL
    OR (
      r.status <> 'running'
      AND :sheet_order <= r.n_queries
      -- only runs between the first and the last attempt ever recorded for this query
      AND r.id >= (SELECT MIN(run_id) FROM run_queries WHERE query_id = :q)
      AND r.id <= (SELECT MAX(run_id) FROM run_queries WHERE query_id = :q)
    )
  )
```
Trailing abandoned runs after the last attempt are then not drawn, which is the conservative direction. Add a test: insert a query at sheet position 1 after N runs and assert the old runs are absent from the grid. Add the same for a query dropped from the sheet.

## Warnings

### WR-01: Status banner says "Идёт сбор" forever for a dead `running` run

**File:** `src/wbmon/ui/series.py:136-138`
**Issue:** `heartbeat_line` guards with `_young_running`, so a run older than 60 min no longer counts as live (D3-A4). `banner()` has no such check. If the collector dies mid-run, the row stays `running` until the next collector start calls `abandon_stale_runs`. The banner reads "⏳ Идёт сбор (начат HH:MM МСК)" indefinitely, while the collector line may say "нет сигнала". The two disagree, and the banner is wrong.
**Fix:** In `banner`, treat a `running` run as live only when `_young_running(latest_run, now)` is true. Otherwise return a warning such as "последний сбор начат HH:MM и не завершился — возможно, сборщик остановлен". Add a test with `started_at` older than 60 min.

### WR-02: `read_heartbeat` can raise and crash the header; a future timestamp reads as "на связи"

**File:** `src/wbmon/ui/data.py:215-225`
**Issue:** The `except` tuple misses `OverflowError`. `datetime.fromisoformat("0001-01-01T00:00:00+05:00").astimezone(UTC)` raises `OverflowError: date value out of range` (reproduced). `render_header` is not wrapped in `_guarded`, so a corrupt or odd heartbeat file takes the whole page down with an uncaught exception. Separately, a timestamp in the future (clock skew, bad file) gives `max(0.0, now - hb)` = 0, so `heartbeat_line` shows "🟢 на связи, сигнал 0 с назад" forever (reproduced with `2999-01-01T00:00:00Z`). That is a false liveness signal.
**Fix:** Add `OverflowError` to the caught exceptions. In `read_heartbeat` or `heartbeat_line`, treat `hb_ts > now + small_tolerance` as unusable and return `None`/stale. Consider wrapping `render_header` in `_guarded` so a header failure cannot hide the tabs.

### WR-03: `st.dataframe`'s built-in CSV download bypasses the formula-injection guard

**File:** `.streamlit/config.toml` (no `client.disableDataExport`), `src/wbmon/ui/app.py:175-205`
**Issue:** D3-A6 requires CSV export to neutralise `= + - @`. That is done only in `series.csv_bytes` for the custom download button. Every `st.dataframe` (characteristics, history, snapshot, run log) also exposes Streamlit's own toolbar "download as CSV" and clipboard copy, which export raw cell text such as seller names. `toolbarMode = "viewer"` does not remove them. The hardened path is therefore bypassable from the same screen, and the page's own caption promises escaping. I could not confirm from the installed source whether the frontend sanitises formulas on its own export, so treat this as unverified, with low-to-medium confidence.
**Fix:** Either set `[client] disableDataExport = true` in `config.toml` (the app already offers its own safe CSV button), or confirm in the browser that the built-in export escapes formulas and note that in the tests/docs.

### WR-04: Key-pinned widgets never follow new data (snapshot slider stuck on an older run)

**File:** `src/wbmon/ui/app.py:296-302` (also the multiselect at `239-249`)
**Issue:** `select_slider(..., value=run_ids[-1], key=f"snap_{query_id}")`. With a `key`, the widget identity and its stored value are stable. I reproduced this with `AppTest`: render, append a new run, rerun. The slider keeps the previous latest run (id 6) while the options now end at id 7. The "default = latest" promise holds only on first render per query. A session left open across the hour, or any rerun triggered by another widget, shows an hour-old snapshot as if current. The multiselect default top-N has the same property, though there it is arguably desirable.
**Fix:** Include the newest snapshot run id in the slider key, e.g. `key=f"snap_{query_id}_{run_ids[-1]}"`, so a new run resets it to "latest". Keep the multiselect key as is, to preserve the user's manual choice.

### WR-05: Status heatmap reads runs and matrix in two separate statements

**File:** `src/wbmon/ui/data.py:323-329`
**Issue:** `load_status_matrix_raw` executes `HEATMAP_RUNS_SQL` and `STATUS_MATRIX_SQL` as two autocommit statements on one connection. If the collector commits a run between them, the second statement's window shifts by one. The oldest run in `runs` then has no cells in `matrix`, and `status_heatmap` paints that whole column white, whose legend is "запрос не выполнялся". This is a false "not executed" claim, and the window is narrow. Rows from the new run are silently ignored.
**Fix:** Run both statements inside one read transaction (`con.execute("BEGIN")` ... `con.rollback()` on the `query_only` connection). Alternatively select the matrix with `WHERE run_id IN (...)` using ids returned by the first query, passed as bound parameters.

### WR-06: Sidebar totals full-scan `observations` every minute and block first paint

**File:** `src/wbmon/ui/data.py:53-54, 228-242, 332` (`CACHE_TTL_S = 60`)
**Issue:** `COUNT(*)` and `COUNT(DISTINCT nm_id)` over `observations` run inside `load_overview`, which `main()` calls before anything renders. The design adds about 336k rows per day (about 10M per month, 100M+ per year). The TTL of 60 s means every active visitor triggers a rescan each minute once the entry expires. At scale this blocks the entire page for seconds and is not hidden by the cache. The module comment itself notes the scan. `test_perf` covers a week-sized DB only.
**Fix:** Cache the two totals separately with `ttl=None` (keyed only on `ver`, which already moves on every new run). Alternatively use `SELECT MAX(id) FROM observations` as the count (the table is append-only, so ids are dense) and drop the exact distinct-product count in favour of a per-query figure.

## Info

### IN-01: "Позиция (текущая)" shows a stale last-known position while the latest runs are UNKNOWN

**File:** `src/wbmon/ui/series.py:499-513`
**Issue:** The current position and Δ come from the newest known (FOUND or ABSENT) point. If the last N runs were blocked, the column still shows the position from days ago under a "текущая" heading. The "Последний раз в выдаче" column partly compensates. This is consistent with D3-A7 but misleading during the all-blocked state the context file expects.
**Fix:** Rename the column to "Последняя известная позиция", or blank it when the newest run of the grid is UNKNOWN.

### IN-02: "Успешных сборов" and the freshness badge count `partial` runs as successful

**File:** `src/wbmon/ui/data.py:40-49`
**Issue:** `n_ok_runs` and `LAST_OK_FINISHED_SQL` use `status IN ('ok','partial')`, but the labels read "Успешных сборов" and "Последний успешный сбор". A run where half the queries were blocked counts as a success and keeps the badge green.
**Fix:** Keep the metric but label it "Сборов с данными", or restrict it to `ok`.

### IN-03: Hardcoded `70` and the all-time query count in the header

**File:** `src/wbmon/ui/app.py:44, 390`; `src/wbmon/ui/texts.py:203, 207`
**Issue:** `overview["n_queries"] or 70` and `texts.subtitle(70, ...)` hardcode 70. The ABOUT copy also hardcodes 70 and 200. `N_QUERIES_SQL` counts every query ever stored, including ones that left the sheet, so the header can read 71 or more.
**Fix:** Use the latest run's `n_queries`, with one named constant for the fallback.

### IN-04: `sheet_order` is not unique, so CSV filenames and labels can be ambiguous

**File:** `src/wbmon/ui/series.py:627-631`; `src/wbmon/ui/texts.py:68-70`
**Issue:** The writer keeps the old `sheet_order` for removed queries, so two queries can both read "05". The filename `wb_serp_q05_...csv` and the "05. text" label then do not identify the query. Selectbox options are keyed by `query_id`, so selection itself is safe.
**Fix:** Use `query_id` (or a slug of `text`) in the filename.

### IN-05: `dbbuilder.assert_temporary` trusts TMPDIR and does not verify the target is synthetic

**File:** `tests/ui/dbbuilder.py:50-58, 223-235`
**Issue:** The guard allows any path under `tempfile.gettempdir()` or `/tmp`. If `TMPDIR` points at a data volume, or a real DB lives under `/tmp`, `append_run` writes into it with no further check (`build_db` does refuse an existing file). The data-integrity rule is HARD in CLAUDE.md.
**Fix:** In `append_run`, refuse unless the DB carries a synthetic marker. For example, require that every row of `queries.text` is in the test set, or write a marker row or file in `build_db` and check it. Also refuse a path equal to `WBMON_DB_PATH` from the environment unless the root conftest set it to a tmp path.

### IN-06: Minor quality items

**File:** `src/wbmon/ui/app.py:23, 346, 342`; `src/wbmon/ui/series.py:101, 193`; `src/wbmon/ui/charts.py:188`; `src/wbmon/ui/app.py:381-384`
**Issue:**
- Run-log column labels are repeated as string literals in `app.py` (`_COUNT_COLUMNS`, "Начало (МСК)"), although `series.RUNLOG_COLUMNS` and `_COUNT_SOURCES` already hold them.
- The values 600 (`stale_s`) and 24 (`last`, `min(24, ...)`) are duplicated magic numbers. `data.HEARTBEAT_STALE_S` exists and is not used as the default.
- Series colours are assigned by selection order, so deselecting an earlier product recolours the remaining lines.
- `Settings.from_env()` validates collector-only variables (search URL, delays), so a bad unrelated `WBMON_*` value shows "Не удалось прочитать базу данных" and stops the UI.
- The chart caption's `top-{top_n}` comes from the newest run, which may be a small manual run.

**Fix:** Import the labels from `series`. Add named constants. Assign colours by `nm_id` hash or sorted order. Read only `WBMON_DB_PATH` and `WBMON_TOP_N` for the UI, or show a config-specific message.

---

_Reviewed: 2026-10-16_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
