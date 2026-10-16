---
phase: 03-web-ui
fixed_at: 2026-10-16T19:00:00Z
review_path: .planning/phases/03-web-ui/03-REVIEW.md
iteration: 1
findings_in_scope: 14
fixed: 14
skipped: 0
status: all_fixed
---

# Phase 3: Code Review Fix Report

**Fixed at:** 2026-10-16
**Source reviews:** `03-REVIEW.md` (CR-01, WR-01..WR-06, IN-03), `03-UI-REVIEW.md` (UI-1..UI-4), orchestrator browser check (VIS-1, VIS-2)
**Iteration:** 1

**Summary:**
- Findings in scope: 14 (1 critical, 6 warnings, 1 info, 4 UI-review items, 2 visual-check items)
- Fixed: 14
- Skipped: 0 (IN-01, IN-02, IN-04, IN-05, IN-06 were out of scope by instruction)

Full suite after the last commit: `pytest -q` 1235 passed, 1 skipped (docker not installed); `ruff check src tests` and `ruff format --check src tests` clean. A browser smoke check on port 8615 (synthetic `visual_check` DB, since stopped) confirmed the legend, the heatmap glyph colours, the prices without a comma and the non-monospace «Выдача» label.

## Fixed Issues

### CR-01: `RUN_GRID_SQL` fabricated failed-run bands
**Files modified:** `src/wbmon/ui/data.py`, `tests/ui/test_data.py`
**Commit:** 98e2a1b
**Status:** fixed: requires human verification (SQL logic)
**Applied fix:** A run without an attempt row now counts only when its id lies between the first and the last `run_queries.run_id` of that query (plus the existing `sheet_order <= n_queries`, non-running rules). Reproduced first: a query inserted at sheet position 1 got 6 false rows, a query dropped from the sheet got one false row per new run. New tests cover both, and the old "abandoned run" test was split: an abandoned run inside the query's span is kept; a trailing abandoned run after the last attempt is now dropped (the reviewer's "conservative direction").

### WR-01: banner said «Идёт сбор» for a dead `running` run
**Files modified:** `src/wbmon/ui/series.py`, `src/wbmon/ui/texts.py`, `tests/ui/test_series.py`
**Commit:** 937d6c6
**Status:** fixed: requires human verification (condition)
**Applied fix:** `banner` uses the same `_young_running` (60 min) rule as `heartbeat_line`. An older `running` row gives a warning `BANNER_RUNNING_STALE` («⏸ Последний сбор начат HH:MM МСК и не завершился — возможно, сборщик остановлен…»). Tests at 70 min and 59 min.

### WR-02: heartbeat overflow, future timestamps, unguarded header
**Files modified:** `src/wbmon/ui/data.py`, `src/wbmon/ui/app.py`, `tests/ui/test_data.py`, `tests/ui/test_app.py`
**Commit:** 370ddda
**Status:** fixed: requires human verification (clamp logic)
**Applied fix:** `read_heartbeat` catches `OverflowError` (also around `astimezone`), treats a time more than 300 s in the future as unusable (`None`, shown as «нет данных»), and clamps a smaller skew to now. `render_header` runs through `_guarded`. AppTest: odd timestamps never crash and never read «на связи»; a forced header exception keeps the tabs and shows `ERROR_BLOCK`.

### WR-03: `st.dataframe` built-in export bypassed the formula guard
**Files modified:** `.streamlit/config.toml`, `tests/ui/test_app.py`
**Commit:** 9ab3497
**Applied fix:** `client.disableDataExport` exists in the installed Streamlit 1.66 (`streamlit config show`, `streamlit/config.py`): it hides the CSV download and clipboard copy of read-only tables. Set `disableDataExport = true`; the app's own «Скачать CSV» is the only export. Tests check the file and the value Streamlit resolves from the repo root.

### WR-04: snapshot slider pinned to an old run
**Files modified:** `src/wbmon/ui/app.py`, `tests/ui/test_app.py`
**Commit:** a40726a
**Applied fix:** slider key is `snap_{query_id}_{newest_run_id}`. AppTest appends a run to an open session and expects the slider on the new run (fails without the fix). The multiselect key is unchanged on purpose.

### WR-05: heatmap runs and matrix read in two statements
**Files modified:** `src/wbmon/ui/data.py`, `tests/ui/test_data.py`
**Commit:** b514f85
**Status:** fixed: requires human verification (transaction semantics)
**Applied fix:** `load_status_matrix_raw` does `BEGIN` ... `rollback()` on the read-only connection, so both statements see one WAL snapshot. The test commits a new run from a trace callback between the statements; without the fix the matrix window shifts (fails before, passes after).

### WR-06: totals scanned `observations` every 60 s
**Files modified:** `src/wbmon/ui/data.py`, `src/wbmon/ui/app.py`, `tests/ui/test_data.py`, `tests/ui/test_perf.py`
**Commit:** 5a7eea6
**Applied fix:** `load_overview_raw` no longer counts observations; new `load_totals_raw` and `load_totals`, cached with `ttl=None` and keyed on `(latest_run_id, latest_status)` only, so the scans run when a run starts and when it finishes, not on every attempt, not every minute. Trade-off: the observation totals lag by the run in progress. Tests: call counts per key and an AST check that the cache has no TTL.

### IN-03: hardcoded 70 in the header and About
**Files modified:** `src/wbmon/ui/app.py`, `src/wbmon/ui/data.py`, `src/wbmon/ui/series.py`, `src/wbmon/ui/texts.py`, `tests/ui/test_app.py`, `tests/ui/test_data.py`
**Commit:** c7fb242
**Applied fix:** `series.sheet_size` takes the planned `n_queries` of the newest scheduler run (a small manual run does not shrink it; any run if there is no scheduler run), then the stored query count, then the named `texts.DEFAULT_N_QUERIES = 70` for a database with no run. About copy takes `{n}` and `{top_n}`; wording changed to «(сейчас их N)» to stay grammatical for any number.

### UI-1: spacing tokens not applied
**Files modified:** `src/wbmon/ui/app.py`, `tests/ui/test_app.py`
**Commit:** bb60962
**Applied fix:** `st.container(gap=24)` around header and tabs; «Позиции» is `gap="medium"` (32 px) between picker, chart group and history, with `gap=24` between the chart block and its table; sidebar period + N and the totals box use `gap="xsmall"` (8 px). `render_product_tables` was split into `render_characteristics` and `render_history` for this. The test walks the element tree and checks the gap of each block.

### UI-2: monospace query label on «Выдача»
**Files modified:** `src/wbmon/ui/app.py`, `src/wbmon/ui/texts.py`, `tests/ui/test_app.py`
**Commit:** e99f063
**Applied fix:** `st.caption(texts.md_literal(label))`. `md_literal` backslash-escapes every ASCII punctuation character and flattens line breaks. Tests: hostile query (`*`, `_`, `[x](url)`, `![img]`, backtick, `<script>`, `#`, `:red[...]`, `:material/..:`, `$..$`, `>`, `&amp;`, `\`, `1.`) reaches no markdown-rendered element unescaped, no `st.text` remains, and the escape round-trips under the CommonMark rule.

### UI-3: unrecorded copy deviations
**Files modified:** `.planning/phases/03-web-ui/03-UI-SPEC.md` (addendum at the end only)
**Commit:** 90a7cd8
**Applied fix:** Addendum with a table of every copy deviation (TABLE_NULL_CAPTION, CSV_CAPTION, banner wording, the two new heartbeat/banner strings, About numbers) and sections for spacing, typography, heatmap, legend, number format and the other fixes.

### UI-4: heatmap glyph colours not explicit
**Files modified:** `src/wbmon/ui/charts.py`, `tests/ui/test_charts.py`, `tests/ui/test_app.py`, `03-UI-SPEC.md` addendum
**Commits:** e6fb93f, 968eb10 (follow-up)
**Applied fix:** A heatmap trace accepts one text colour only, so the glyphs are text-mode scatter overlays, one trace per colour (`GLYPH_COLORS`: white on ok/blocked/rate_limited/error, `#1A1D21` on amber partial). Heatmap `text`/`texttemplate` removed; `fig.data[0]` is still the heatmap and still carries the hover. The follow-up pins both axes (`autorange=False` plus explicit range): the overlays otherwise add autorange padding and leave empty grey rows above the first query (seen in the browser, not in the unit tests).

### VIS-1: legend entries overlap
**Files modified:** `src/wbmon/ui/charts.py`, `tests/ui/test_charts.py`
**Commit:** 796ff44
**Applied fix:** `legend.entrywidthmode="pixels"`, `legend.entrywidth=250`. Trace names longer than 32 characters are cut with an ellipsis (`legend_name`) so a long brand cannot run into the next fixed-width entry (this also shortens the hover header for such brands; the ID is always kept). Test asserts mode and width. Verified in the browser: no overlap.

### VIS-2: «1,073» in an en-US browser
**Files modified:** `src/wbmon/ui/app.py`, `tests/ui/test_app.py`
**Commit:** 2542c26
**Applied fix:** «Цена, ₽» and «Отзывов» use `NumberColumn(format="%d")` instead of `"localized"`. Test walks every dataframe's column config and fails on `localized`. Metrics and hover keep the U+202F separator.

## Skipped Issues

None among the in-scope findings. Out of scope by instruction and not touched: IN-01 (current-position column during UNKNOWN runs), IN-02 («успешных» counts `partial`), IN-04 (CSV filename uses a non-unique `sheet_order`), IN-05 (`dbbuilder.assert_temporary` trusts TMPDIR), IN-06 (minor quality items).

## Notes for the verifier

- Worth a manual look because they are logic changes: CR-01 (a trailing abandoned run is no longer drawn on a query's chart), WR-01, WR-02 (300 s future tolerance), WR-05.
- The UI-review point «spinner covers only the chart» and the missing Plotly alt text were not in scope and remain.
- All work happened in a temporary worktree branch that was fast-forwarded onto `main`; the Streamlit server on port 8599 was not touched (it runs the pre-fix code until restarted).

---

_Fixed: 2026-10-16_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
