---
phase: 03-web-ui
plan: 02
subsystem: ui
tags: [streamlit, plotly, honest-gaps, chart, pandas, apptest, cache]

requires:
  - phase: 03-web-ui
    provides: 03-01 read-only shell (data.py loaders and cache pattern, series/texts, AppTest harness, scenario DB builder)
provides:
  - cached read-only chart loaders (queries, run grid, product options, latest data run, top ids, observations)
  - FOUND / ABSENT / UNKNOWN honest-gap state machine and picker helpers (pure series.py)
  - pure Plotly position chart builder (charts.py) with a single failed-run shapes list
  - sidebar filters (query, period, default product count) and the «📈 Позиции» tab
affects: [03-03, 03-04]

tech-stack:
  added: []
  patterns:
    - "ABSENT is inferred only from an attempt that is ok AND complete; every other missing point is UNKNOWN (gap plus grey band)"
    - "Runs that never attempted the query are excluded in SQL (never-attempted predicate), so they add neither a point nor a band"
    - "Plotly figure built from plain lists with None gaps; failed-run bands assigned once as layout.shapes"
    - "Untrusted text only through format_func labels and html.escape'd trace names; prices pre-formatted as strings"

key-files:
  created:
    - src/wbmon/ui/charts.py
    - tests/ui/test_charts.py
  modified:
    - src/wbmon/ui/data.py
    - src/wbmon/ui/series.py
    - src/wbmon/ui/texts.py
    - src/wbmon/ui/app.py
    - tests/ui/test_data.py
    - tests/ui/test_series.py
    - tests/ui/test_app.py

key-decisions:
  - "Picker key is products_{query_id}_{N}: a change of query or N resets the selection to the new default; default clamped to min(N, 15) so N=20 never raises"
  - "band_times / x_range accept either the raw loader grid or an already prepared one (prepare_grid is applied only when needed)"
  - "Added series.series_names(options) so app.py gets 'ID {nm_id} · {brand or —}' names from a pure, tested helper"
  - "Summary line count 'без данных' = number of band runs (incomplete or missing attempts), matching the drawn bands"
  - "An in-latest-run product is ordered by its latest position; products not in the latest run follow ordered by best position, then ID"

patterns-established:
  - "Loader frames keep numpy_nullable dtypes; series converts to plain int64/bool before logic and charts convert to Python lists"
  - "AppTest chart assertions read at.get('plotly_chart')[0].proto.spec (empty layout.shapes is omitted from the JSON)"

requirements-completed: [UI-03, UI-04, UI-05, UI-06, UI-09, UI-12]

duration: 55min
completed: 2026-10-16
---

# Phase 3 Plan 02: Honest position chart Summary

**Query/period/product pickers and a Plotly position chart that draws only observed positions: position 1 on top, Moscow time, gaps where a product was absent from a complete result, grey bands for failed or partial attempts, diamonds for ads, all untrusted text escaped.**

## Performance

- **Tasks:** 3 of 3
- **Files:** 2 created, 7 modified
- **Tests:** full offline suite 931 passed (was 838), 16 slow deselected; 137 tests in test_data + test_series, 28 in test_charts, 29 in test_app

## Accomplishments

- Sidebar: «Поисковый запрос» selectbox with every query as «NN. текст» in sheet order (ё, Cyrillic х and Latin x preserved), «Период» segmented control («24 ч» / «7 дней» / «Всё время», a deselected control falls back to all time), «Товаров по умолчанию» 1-20 (default 5).
- «📈 Позиции» tab is first. Product picker offers every nm_id ever seen for the query, labelled «{id} · {brand or —} · {name[:40]}» plus « · не в последней выдаче», ordered by current then best position, default = first min(N, 15) of the latest run with data, max 15, reset when query or N changes; caption when N > 15.
- Honest gaps per run x product: FOUND (an observation exists), ABSENT (no row, attempt is ok AND complete), UNKNOWN (everything else, including partial, ok-but-cut-short, blocked, rate_limited, error and a missing attempt row). FOUND points inside a partial run are still drawn; ABSENT never occurs in a partial run.
- Grid SQL leaves out runs that never attempted the query (in-flight runs not yet at it, limited manual runs) so they add neither a column nor a band; an abandoned run that planned the query paints a band.
- Chart: reversed Y with top bound -0.5 and explicit integer ticks from 1, МСК wall-clock X, `lines+markers` with `connectgaps=False`, y as plain lists with `None`, per-point diamond/circle markers, hover with date/time МСК, ID, position, price («—» when unknown) and a «реклама» line only for ads, horizontal legend with the two legend-only entries, 15-colour palette with dashes from series 10. Band rectangles are one `layout.shapes` list (720 bands in about 0.17 s for the whole month-sized figure test).
- Period filter narrows the grid by run start anchored on the latest run (picker options stay «ever seen»); empty states: «По этому запросу пока нет успешных сборов…» (disabled empty picker plus band-only skeleton), «Выберите хотя бы один товар…» (skeleton stays), «За выбранный период нет сборов…», zero runs shows only the existing banner info and no chart.
- Safety: labels go through `format_func`, trace names are `html.escape`d, prices are pre-formatted strings; an injection AppTest (hostile brand and Markdown/LaTeX name) shows the literal text in the picker and nothing in the chart JSON, markdown, caption or info elements.

## Task Commits

1. **Task 1: Chart loaders and the FOUND / ABSENT / UNKNOWN state machine** - `7963501` (feat)
2. **Task 2: Pure Plotly position chart** - `5eaad07` (feat)
3. **Task 3: Positions tab and sidebar controls with AppTests** - `fd3974b` (feat)

## Deviations from Plan

### Auto-fixed Issues

None needed beyond test-authoring slips found while running the tests (wrong expected ordering of stale products, hand-counted observation totals, a relative path that broke after the root conftest chdir); the code was right in each case and only the test expectations were corrected.

### Minor choices within the plan

- `series.series_names(options)` was added (not in the plan's export list) so the legend names are built in the pure module instead of inside `app.py`.
- `band_times` and `x_range` accept a raw grid as well as a prepared one, which keeps the plan's `band_times(grid)` and `x_range(grid)` signatures while allowing tests to pass loader frames directly.
- Tab body signature is `render_positions_tab(path, ver, overview, query, period, n, top_n)`; `render_sidebar_filters(queries)` returns `(query row or None, period, N)`.
- Because the deliberate deviations (single shapes list, -0.5 top bound, one-fragment hover third line) are the plan's own, they are implemented as specified and not repeated here.

## Deferred Issues

- `ruff check src tests` still reports the **pre-existing** I001 in the Phase 1 file `tests/factories.py:256`; left untouched per the worktree instruction. Everything this plan touches (`src/wbmon/ui`, `tests/ui`) is ruff-clean and `ruff format --check src tests` passes.

## Known Stubs

None. The recent-attempts table under the «нет успешных сборов» info and the characteristics table, history expander and CSV button belong to plan 03-03 (planned, not a defect); the tab constants `TAB_SNAPSHOT` and `TAB_ABOUT` are still unused until 03-03 and 03-04.

## Threat Flags

None. No new network endpoint, auth path or schema change. The new loaders use the same `connect_ro` plus `contextlib.closing` pattern; the only dynamic SQL is the `?` placeholder list, capped at `MAX_IN_IDS`; `since` comes from `clock.iso_z`.

## Self-Check: PASSED

- Files exist: `src/wbmon/ui/charts.py`, `tests/ui/test_charts.py`, updated `data.py`, `series.py`, `texts.py`, `app.py` and the three UI test files
- Commits present: `7963501`, `5eaad07`, `fd3974b`
- `PYTHONPATH=src pytest -q`: 931 passed; `ruff check src/wbmon/ui tests/ui` and `ruff format --check src tests` clean
- Acceptance greps: `sheet_order <= r.n_queries` on one line in `data.py`; no `validate=`, numpy, streamlit or sqlite3 import in `series.py`/`charts.py`; `drop_duplicates` present; no `add_vrect`/`add_shape` in `charts.py`; `shapes=band_shapes(` on one line; `connectgaps=False` and `html.escape` present; `key=f"products_{query_id}_{n}"`, `max_selections=series.MAX_SELECTED`, `required=True` present; the only `st.plotly_chart(` call uses `width="stretch"`; no `use_container_width`, `unsafe_allow_html` or `st.write(` in `app.py`
