---
phase: 03-web-ui
plan: 04
subsystem: ui
tags: [streamlit, plotly, heatmap, guardrails, performance, apptest, gate]

requires:
  - phase: 03-web-ui
    provides: 03-01 shell and data layer; 03-02 honest chart; 03-03 tables, CSV and the «Выдача» tab
provides:
  - query x run status heatmap (colour + glyph + hover) with summary badges and legend in «Журнал сборов»
  - «ℹ️ О сервисе» methodology tab and the final tab order
  - full UI-SPEC State Matrix in AppTest
  - source-level read-only / injection guard (tests/ui/test_ui_guardrails.py)
  - slow week-scale performance proof (tests/ui/test_perf.py)
  - scenario_visual_check temp-only builder scenario for the end-of-phase visual check
affects: [Phase 4 (deploy: repo-root cwd, .streamlit must ship, CI extras)]

tech-stack:
  added: []
  patterns:
    - "Heatmap axes are integer positions with tickmode=array + ticktext, so duplicate labels can never merge rows or columns"
    - "Guard checks are plain functions over source text, each proven on a deliberately bad snippet"
    - "Perf DB lives in tmp_path_factory; the finalizer deletes DB, -wal, -shm and asserts they are gone"

key-files:
  created:
    - tests/ui/test_ui_guardrails.py
    - tests/ui/test_perf.py
  modified:
    - src/wbmon/ui/data.py
    - src/wbmon/ui/charts.py
    - src/wbmon/ui/series.py
    - src/wbmon/ui/texts.py
    - src/wbmon/ui/app.py
    - tests/ui/dbbuilder.py
    - tests/ui/test_data.py
    - tests/ui/test_charts.py
    - tests/ui/test_series.py
    - tests/ui/test_app.py
    - tests/ui/test_dbbuilder.py

key-decisions:
  - "render_runlog_tab now takes (path, ver, run_log, latest) because the heatmap block loads its own cached frames"
  - "summary_badges uses «Последний сбор:» for n == 1 and «Последние N сбора/сборов:» otherwise; a zero total shows a single green zero badge"
  - "A matrix status outside the five known ones is drawn with the error style (grey E) rather than dropped"
  - "Heatmap hover for a missing cell still prints «Товаров: —» and «HTTP: —» (unknown, never a fabricated number)"
  - "AppTest helpers split plotly charts into position charts and the heatmap, because both tabs render in one script run"

patterns-established:
  - "texts.ABOUT_* are the only multi-line Markdown constants; they contain our own copy and wbmon.REPO_URL only"
  - "Guard: WBMON allow-list per module (clock, config, storage.db:connect_ro, storage.reader:MAX_IN_IDS, ui.*)"

requirements-completed: [UI-01, UI-10, UI-12, STOR-05]

duration: 75min
completed: 2026-10-16
---

# Phase 3 Plan 04: Heatmap, About, Guardrails and Week-Scale Gate Summary

**Query x run status heatmap with a glyph channel, the methodology tab, the full State Matrix in AppTest, an AST/text guard that makes any future write path or Markdown/HTML sink fail CI, and a generated-week performance proof where the slowest loader takes 59 ms.**

## Performance

- **Tasks:** 3 of 3 (the visual human check is deferred to the orchestrator, see below)
- **Files:** 2 created, 11 modified
- **Tests:** full offline suite 1112 passed, 24 slow deselected (was 1003 after 03-03); slow set (`tests/ui/test_perf.py` + `tests/test_storage_perf.py`) 24 passed in 15 s

## Accomplishments

- `data.load_status_matrix` (raw + cached twin, keyed on `DataVersion`): the newest 48 finished runs and every attempt in them; running and abandoned runs are excluded by both statements (`NOT IN ('running', 'abandoned')`).
- `charts.status_heatmap`: `go.Heatmap` with a stepped 12-stop colour scale, `✓ ~ ✕ ! E` glyphs as the non-colour channel, white empty cells for attempts that never happened, hover with full query text / time МСК / status / items / HTTP, tick labels `NN. text[:24]…`, height `20 * n + 96`, query and run texts escaped with `charts.esc`. 48 runs x 70 queries build in 17 ms.
- «🩺 Журнал сборов»: subheader, badges for the last 24 runs, legend, heatmap in `st.container(height=480, border=True)`, then caption, run table, footer. All-blocked renders a full red field of ✕; zero runs show only the info.
- «ℹ️ О сервисе» with the UI-SPEC copy and `wbmon.REPO_URL`; tabs are now «📈 Позиции», «🗂 Выдача», «🩺 Журнал сборов», «ℹ️ О сервисе».
- AppTest State Matrix (13 parametrized states: no DB, empty file, garbage file, DB without `runs`, zero runs, one ok run, mixed, all blocked, latest partial / running / abandoned / blocked / error, query without data) plus targeted heatmap, badge, About and chart-range checks.
- `tests/ui/test_ui_guardrails.py` (16 test functions, 54 cases): import allow-list, pure modules free of I/O, no `unsafe_allow_html` / `use_container_width` / `connect_rw` / `st.write` / `st.html` / `add_vrect` / `add_shape`, SQL constants SELECT/WITH only, no uppercase write keyword or PRAGMA assignment, every `st.dataframe` has `hide_index=True` + `placeholder`, every `st.plotly_chart` has `width="stretch"`, `showErrorDetails = "none"`. Each check is also run against bad snippets to prove it fires.
- `tests/ui/test_perf.py` (`-m slow`): 2,016,000 observations (70 queries x 168 runs x 200 positions, every 7th run blocked) generated in about 6 s inside `tmp_path_factory`, deleted by the finalizer.
- `scenario_visual_check` in `tests/ui/dbbuilder.py` (real 70 query texts, synthetic «ТЕСТ» products, runs 5 / 20-22 / 30 / 40 and the limited manual run as scripted).

## Gate Results

| Step | Result |
|------|--------|
| a. `pytest -q` (3.14, main venv) | 1112 passed, 24 deselected |
| a. `pytest -q -m slow tests/ui/test_perf.py tests/test_storage_perf.py` | 24 passed |
| b. `ruff format --check src tests` | clean (66 files) |
| b. `ruff check src tests` | only the known pre-existing I001 in `tests/factories.py:256`; `ruff check src/wbmon/ui tests/ui` clean |
| c. Python 3.12.13 venv, `pip install -e ".[web,dev]"` (streamlit 1.66.0, plotly 7.1.0, pandas 3.0.6) | install OK (no pip error); `tests/ui tests/test_guardrails.py` 408 passed; whole suite 1112 passed; ui lint clean; venv deleted |
| d. headless `streamlit run src/wbmon/ui/app.py` from the repo root on a `visual_check` tmp DB, 127.0.0.1:8599 | `/_stcore/health` returned `ok` after 2 s; `grep -ci "not a valid config option\|unrecognized config"` = 0; server stopped, tmp dir removed |

Slowest timings from the perf test (limit 1.0 s each): `load_overview_raw` 59 ms (slowest loader), `load_product_options_raw` 55 ms, position pipeline (`build_points` + `band_times` + `position_figure`, 15 products x 168 runs) 76 ms, `status_heatmap` 17 ms, `load_observations_raw` 3 ms, `load_status_matrix_raw` 3 ms; every other loader below 3 ms. `EXPLAIN QUERY PLAN` of the observation and product-option SQL contains no `SCAN observations` row.

Extra check (not required): the `visual_check` DB opened in an AppTest renders all four tabs without exception, the heatmap is 70 x 48, run 5 and runs 20-22 are all-3 columns, run 30 shows partial (2) for queries 11-20, the manual limited run column is `[1, 1, 0, 0...]`, and the position chart for query 01 has 4 band shapes (run 5 + runs 20-22, none for the abandoned run 40).

## Task Commits

1. **Task 1: Status matrix loader and the glyph heatmap builder** - `aa37bda` (feat)
2. **Task 2: Four-tab app, heatmap block, About tab, State Matrix tests, UI guard** - `ebaade1` (feat)
3. **Task 3: Week-scale performance proof and the visual_check scenario** - `f32134c` (test)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Position-chart AppTests would have counted the heatmap**
- **Found during:** Task 2
- **Issue:** Both tabs render in one script run, so `chart_spec(at)` (asserts exactly one plotly chart) and `not at.get("plotly_chart")` in eight existing 03-02/03-03 tests broke the moment the heatmap existed.
- **Fix:** `chart_specs` returns position charts only; new `heatmap_specs` returns the heatmap; the two "no chart" assertions use `chart_specs`.
- **Files modified:** `tests/ui/test_app.py`
- **Commit:** `ebaade1`

**2. [Rule 3 - Scope] Scenario tests added to `tests/ui/test_dbbuilder.py`**
- The plan lists only `tests/ui/dbbuilder.py` for the scenario; five tests for `scenario_visual_check` (documented run statuses, scripted attempts, synthetic/moving products, CLI refusing the repo path and creating nothing, no overwrite) were added to the existing builder test file, which is inside `tests/ui/**`.
- **Commit:** `f32134c`

### Minor choices within the plan

- `render_runlog_tab` signature changed to `(path, ver, run_log, latest)` (the heatmap block loads cached frames itself).
- `TAB_ABOUT` is no longer unused; `texts` now imports `wbmon.REPO_URL` (allowed by the guard).
- The guard's wbmon allow-list also permits `from wbmon import REPO_URL, clock` as the plan's list implies (`wbmon` for REPO_URL, `wbmon.clock`).
- No pip or network errors occurred in the 3.12 step.

## Auth gates

None.

## Deferred Issues

- `ruff check src tests` still reports the pre-existing I001 in the Phase 1 file `tests/factories.py:256`; left untouched per instruction.
- **Visual human check (Task 3 `<human-check>`): deferred to orchestrator.** Automated parts are done (health smoke, data checks through AppTest). The orchestrator should run steps 1-9 of the plan with a browser on temporary DBs: `python tests/ui/dbbuilder.py --scenario visual_check "$T/visual.sqlite3"` and `--scenario all_blocked "$T/blocked.sqlite3"`, `WBMON_DB_PATH=... streamlit run src/wbmon/ui/app.py --server.headless true --server.port 8599` from the repo root, then `rm -rf "$T"`.

## Cross-phase notes

- CI installs only `.[dev]`, so `tests/ui` is skipped there via `collect_ignore_glob` in `tests/ui/conftest.py`. Phase 4 may switch CI to `.[web,dev]`; Phase 1/4 own `ci.yml`.
- Phase 2/4 must start Streamlit with the repo root as cwd (D3-A3: `.streamlit/config.toml` is read from the process cwd) and must not `.dockerignore` `.streamlit/`.
- The slow suite (`-m slow`) now holds the storage perf test (2.35M rows) and the UI perf test (2.0M rows); both need about 0.8 GB of temporary disk each and clean up after themselves.

## Known Stubs

None.

## Threat Flags

None. No new endpoint, auth path or schema change. The only new SQL is two SELECTs over `runs` / `run_queries` with a bound `:n`; sheet-controlled query text reaches Plotly only through `charts.esc` (hostile `<a href>` test asserts no raw `<a ` in the figure JSON).

## Self-Check: PASSED

- Files exist: `tests/ui/test_ui_guardrails.py`, `tests/ui/test_perf.py`, this SUMMARY, and all modified files
- Commits present: `aa37bda`, `ebaade1`, `f32134c`
- `PYTHONPATH=src pytest -q`: 1112 passed, 24 deselected; slow set 24 passed; `ruff format --check src tests` clean
- Acceptance greps: 2 `NOT IN ('running', 'abandoned')` lines in `data.py`; one `texttemplate="%{text}"`, one `showscale=False`; no streamlit/numpy/sqlite3 import in `charts.py` / `series.py` / `texts.py`; `TAB_ABOUT` in the final `st.tabs`; one `st.container(height=480, border=True)`; `REPO_URL` in `texts.py`; guard file has 16 test functions; no `data/` directory in the repo; changed files are all under `src/wbmon/ui/**`, `tests/ui/**` and `.planning/**`
