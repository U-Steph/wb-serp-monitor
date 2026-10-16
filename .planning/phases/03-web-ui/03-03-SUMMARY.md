---
phase: 03-web-ui
plan: 03
subsystem: ui
tags: [streamlit, pandas, tables, csv, excel, formula-injection, serp-snapshot, apptest]

requires:
  - phase: 03-web-ui
    provides: 03-01 shell and data layer; 03-02 honest-gap points, picker, chart and positions tab
provides:
  - characteristics table (one row per selected product) with Δ against the previous complete run
  - observation history table (expander) and the Excel-safe CSV export with formula-injection guard
  - recent-attempts table for queries without stored observations (positions and snapshot tabs)
  - third tab «🗂 Выдача» with a run slider, four metrics and the full top-N table
affects: [03-04]

tech-stack:
  added: []
  patterns:
    - "Pure shaping in series.py returns display-ready frames with nullable dtypes; app.py only wires st.dataframe, column_config and the download button"
    - "Unknown stays explicit: text NULL -> dash in series, numeric NA kept as NA and shown through placeholder=«—»"
    - "CSV bytes = DataFrame.to_csv(None, sep=';', decimal=',', lineterminator CRLF).encode('utf-8-sig'); csv_safe applied to the two untrusted text columns"
    - "Untrusted text only in st.dataframe cells, widget labels (format_func) and st.text"

key-files:
  created: []
  modified:
    - src/wbmon/ui/series.py
    - src/wbmon/ui/texts.py
    - src/wbmon/ui/data.py
    - src/wbmon/ui/app.py
    - tests/ui/test_series.py
    - tests/ui/test_data.py
    - tests/ui/test_app.py

key-decisions:
  - "The sidebar query mapping now also carries the sheet text, so the CSV «Запрос» column and the «Выдача» label need no extra lookup"
  - "render_snapshot_tab takes (path, ver, overview, query): the overview is needed for the zero-runs info"
  - "Tables and the CSV block render whenever the query has any picker option; with an empty selection the characteristics frame is empty and the CSV button is disabled with a hint"
  - "Median price rounds half up (floor(x + 0.5)) before fmt_int"
  - "New st.dataframe calls use the literal placeholder=\"—\" so the plan's acceptance grep matches; the run-log table keeps texts.MISSING (same value)"

patterns-established:
  - "Column titles live once in texts.COL_*; series builds frames from them and app.py keys column_config on the same constants"
  - "find_df in AppTests is ambiguous when two tables share a column: the run-log tests now locate their table by «Тип», new tests by an exact column list"

requirements-completed: [UI-07, UI-08, UI-09, UI-11]

duration: 50min
completed: 2026-10-16
---

# Phase 3 Plan 03: Characteristics, history, CSV and «Выдача» Summary

**Characteristics and history tables, an Excel-safe CSV export (BOM, `;`, decimal comma, CRLF, formula-prefix guard), a recent-attempts fallback and a «🗂 Выдача» snapshot tab, all built from pure tested shaping functions over read-only cached loaders.**

## Performance

- **Tasks:** 3 of 3
- **Files:** 7 modified, none created
- **Tests:** full offline suite 1003 passed (was 931 after 03-02), 16 slow deselected; UI suite 282 passed

## Accomplishments

- «Характеристики» under the chart: ID link showing only the number (regex `catalog/(\d+)/detail`, URL built from the integer id), name, brand, seller, current position, Δ к прошлому сбору (▲ n / ▼ n / = 0 / новый / —), best, worst, share of complete runs where found, price, rating, reviews, delivery days, ad mark and last seen (МСК, sortable datetime). Δ compares with the newest EARLIER complete run, so a partial run never defines it; the share counts only FOUND points of complete runs (never above 100 %, NA when the period has no complete run). Rows sorted by current position, products out of the latest run last; a never-found product still gets a row with name and brand from the picker options.
- «История наблюдений» expander: every FOUND point of the selection, newest first then by position, same period as the chart.
- «Скачать CSV»: history rows plus a leading «Запрос» column, `;` separator, decimal comma, CRLF, UTF-8 with BOM, ID as the bare integer, time `DD.MM.YYYY HH:MM`, ad flag 1/0, NULL as an empty field. `csv_safe` prefixes an apostrophe to «Запрос» and «Продавец» cells starting with `= + - @ TAB CR`. File name `wb_serp_q01_20261016_1430.csv` (sheet number, current МСК time). Disabled with «Нет данных для выгрузки» when nothing is selected or found. Round trip through `read_csv` returns a seller containing `;`, a quote and a newline unchanged.
- Recent-attempts table (time МСК, status label, HTTP, pages, items, reason, duration; newest 10) under the «нет успешных сборов» info on «Позиции» and «Выдача», so an all-blocked database explains itself (3 rows, «⛔ Заблокировано», HTTP 403, `http_403`).
- «🗂 Выдача»: `select_slider` over runs that stored observations (labels `DD.MM HH:MM`, default newest, key per query so a query change resets it), four metrics (товаров собрано, из них рекламных, медианная цена, статус сбора), full top-N dataframe (height 640) in position order; the query label is rendered with `st.text`.
- Safety: marketplace and sheet text appears only inside dataframe cells, picker labels and `st.text`; an injection AppTest proves the literal `$x^2$ [ссылка](http://e.x) **b**` shows in both tables and in no markdown, caption or info element.

## Task Commits

1. **Task 1: Pure shaping for characteristics, history, CSV, snapshot and attempts** - `a52f14a` (feat)
2. **Task 2: Characteristics table, history expander, CSV button and attempts fallback** - `b6f6ed4` (feat)
3. **Task 3: «🗂 Выдача» tab** - `84252fd` (feat)

## Decisions Made

See `key-decisions` above. Deliberate deviations 1-5 from the plan header (file name slug, `st.text` for the query label, dash placeholder with adjusted null caption, extra CSV caption sentence, datetime columns) are implemented as specified.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Loaders landed in the Task 1 commit instead of Tasks 2 and 3**
- **Found during:** Task 1 (the snapshot and attempts shaping tests build their inputs from the real loaders)
- **Issue:** `test_series.py` needs `load_snapshot_raw` and `load_query_attempts_raw`, which the plan assigns to Tasks 2 and 3, so Task 1 could not be tested and committed on its own.
- **Fix:** Added all three loaders, their SQL constants and cached twins to `data.py` in the Task 1 commit; their loader tests (`test_data.py`) were added in Task 2 as planned.
- **Files modified:** `src/wbmon/ui/data.py`
- **Commit:** `a52f14a`

**2. [Rule 1 - Bug] Run-log AppTests could match the new attempts table**
- **Found during:** Task 2
- **Issue:** `find_df(at, "Статус")` returns the first frame with that column; the attempts table (all-blocked case) now has «Статус» too, so the run-log assertions could silently read the wrong table.
- **Fix:** Run-log tests now locate their table by the run-log-only column «Тип».
- **Files modified:** `tests/ui/test_app.py`
- **Commit:** `b6f6ed4`

**3. [Rule 2 - Robustness] Hand-built point frames without value columns**
- **Found during:** Task 1
- **Issue:** `characteristics_frame`, `history_frame` and `csv_bytes` raised `KeyError` on frames lacking optional value columns such as `name` (hand-built test frames, future callers).
- **Fix:** A small `_full` helper reindexes the points so a missing value column reads as unknown instead of crashing.
- **Files modified:** `src/wbmon/ui/series.py`
- **Commit:** `a52f14a`

### Minor choices within the plan

- Tables and the CSV block appear whenever the query has picker options (`if ids:`), not only when something is selected; with nothing selected the frame is empty and the button disabled, as the checkpoint on the disabled state requires.
- `render_snapshot_tab` signature is `(path, ver, overview, query)` and the sidebar query mapping gained a `text` key.
- In the period test the appended older run has the highest id, so the default selection is its single product (existing 03-02 behaviour: default = top of the run with the highest id); expectations were written accordingly.

## Deferred Issues

- `ruff check src tests` still reports the **pre-existing** I001 in the Phase 1 file `tests/factories.py:256`; left untouched per the worktree instruction. `ruff check src/wbmon/ui tests/ui` and `ruff format --check src tests` are clean.

## Known Stubs

None. `TAB_ABOUT` is still unused until plan 03-04 (planned, not a defect).

## Threat Flags

None. No new network endpoint, auth path or schema change; the three new loaders use `connect_ro` inside `contextlib.closing` with SELECT-only constants and bound parameters (`LIMIT 10` attempts, `LIMIT 500` snapshot runs); product links are built only from the integer `nm_id`.

## Self-Check: PASSED

- Files exist: all seven modified files; this SUMMARY
- Commits present: `a52f14a`, `b6f6ed4`, `84252fd`
- `PYTHONPATH=src pytest -q`: 1003 passed, 16 deselected; `ruff format --check src tests` clean; `ruff check src/wbmon/ui tests/ui` clean
- Acceptance greps: `encode("utf-8-sig")`, `sep=";"`, `decimal=","` once each in `series.py`; no streamlit/numpy/sqlite3/os/time import in `series.py`/`texts.py`; `placeholder="—"` on 3 new tables; `display_text=r"catalog/(\d+)/detail"`; `render_query_attempts(` defined and called twice; `key=f"snap_{`; `height=640`; `st.text(texts.query_label` and no `st.caption(texts.query_label`; no forbidden substrings in `src/wbmon/ui`; no `data/` directory
