---
phase: 03-web-ui
verified: 2026-10-16T18:30:00Z
status: human_needed
score: 5/5 must-haves verified
overrides_applied: 0
deferred:
  - truth: "UI-01 clause: Streamlit app reachable by a public link"
    addressed_in: "Phase 4"
    evidence: "Phase 4 success criterion 1 / OPS-03: 'A public link opens the running Russian UI on the user's host with no login'"
human_verification:
  - test: "Visual check in a real browser (visual_check and all_blocked temp DBs, streamlit run from repo root)"
    expected: "Russian UI looks readable: position 1 at top, grey bands and diamonds visible, heatmap glyphs legible, banner and badge colours as in 03-UI-SPEC"
    why_human: "Visual appearance cannot be asserted by grep/AppTest. Owned by the orchestrator (already in progress: a streamlit on port 8599 belongs to it, not to this verification)."
---

# Phase 3: Web UI Verification Report

**Phase Goal:** A Russian-language Streamlit app lets the user pick a query and products and see an honest, readable history of positions - gaps where a product was not found, clear markers where we could not look - plus the collected characteristics and the run log.
**Verified:** 2026-10-16
**Status:** human_needed (all automated checks pass; only the visual browser check remains, owned by the orchestrator)
**Re-verification:** No - initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Russian UI with freshness header, banner for blocked/partial, sidebar totals; sensible empty/blocked states (zero, one, many, all-blocked); app cannot write to the DB | VERIFIED | `app.py` `render_header` / `render_sidebar_totals`; `texts.FRESHNESS_TEMPLATE` = «Последний успешный сбор: … МСК (…)»; `series.banner`. AppTest on a `visual_check` DB: no exceptions, header caption/collector line rendered. Test names present: `test_zero_runs_render_the_empty_header`, `test_all_blocked_history_is_complete_and_informative`, `test_one_ok_run_...`, `test_state_matrix_renders_without_an_exception[...]`. Read-only: every loader uses `contextlib.closing(connect_ro(...))` (`mode=ro` URI + `PRAGMA query_only=ON`, `storage/db.py:60`); I opened the same DB `mode=ro` and an INSERT raised "attempt to write a readonly database"; run/observation counts unchanged after headless server + AppTest (49 runs / 90 510 obs). Grep of `src/wbmon/ui` for `connect_rw`, `INSERT`, `UPDATE `, `DELETE `, `unsafe_allow_html`, `use_container_width`: no matches. |
| 2 | «Позиции»: pick one of 70 queries (original wording, sheet order), pick nm_ids (searchable `nm_id · brand · name`, default current top-5, reset on query change), time range, interactive chart with position 1 on top, МСК on X, hover with date/time, nm_id, position, price | VERIFIED | AppTest: selectbox has 70 options («01. постельное белье 2 спальное», «02. ...»), multiselect default = 5 ids; selecting query #11 re-ran without exception and the default changed to 5 different ids (key `products_{query_id}_{n}`). Chart spec: `yaxis.range = [30, -0.5]` (reversed, position 1 at top), `mode=lines+markers`, `connectgaps=False`, X is naive МСК (`series.to_msk`), hover template in `charts.py`. Period control «24 ч / 7 дней / Всё время» (`series.period_since`, anchored on latest run). |
| 3 | Honest gaps: absent from a complete run = gap; blocked/partial/error runs marked and not "absent"; ad slots marked | VERIFIED | `series.prepare_grid`: `complete = status=='ok' AND is_complete==1`; `build_points`: ABSENT only if complete, else UNKNOWN, nothing interpolated (only FOUND rows carry a position). `charts.band_shapes` draws grey bands for non-complete runs (4 shapes on the visual_check chart; trace y-values contain `None` gaps, e.g. `[16, 1, 2, 4, None, 13, ...]`); ad points use `symbol: diamond`, legend entry «рекламное размещение». Runs that never attempted the query are excluded in `RUN_GRID_SQL` (D3-A5). |
| 4 | Characteristics table (latest position, price, rating, reviews, delivery, seller, ad, last seen, best/worst, WB link) + full history + Excel-safe CSV | VERIFIED | `app.py::render_product_tables`; `series.characteristics_frame` / `history_frame`; link column `https://www.wildberries.ru/catalog/{int}/detail.aspx` built from the integer id; `series.csv_bytes`: `sep=";"`, `decimal=","`, CRLF, `.encode("utf-8-sig")`, formula-prefix guard `csv_safe`. Tables confirmed in AppTest (`ID, Название, Бренд, Продавец, Позиция (текущая) ...`, history `Время (МСК), ID, Позиция, Цена, Рейтинг ...`). |
| 5 | «Журнал сборов» (start МСК, duration, status, counts, products stored) + query x run heatmap; «Выдача» full top-N snapshot at chosen run; new run appears without restart | VERIFIED | Run-log dataframe columns `Начало (МСК), Длительность, Тип, Статус, Успешных ...`; `charts.status_heatmap` (colour + glyph, 48 runs, 70 queries); «Выдача» `st.select_slider` over runs + 4 metrics + `st.dataframe` (AppTest: columns `Позиция, Реклама, ID, Название, Бренд`). Cache: `st.cache_data(ttl=60)` keyed on `(path, DataVersion(latest_run_id, last_rq_id, latest_status))`; `test_new_run_appears_without_restart` passes. |

**Score:** 5/5 truths verified

### Deferred Items

| # | Item | Addressed In | Evidence |
|---|------|-------------|----------|
| 1 | UI-01 "reachable by a public link" | Phase 4 (OPS-03) | ROADMAP Phase 4 SC1: "A public link opens the running Russian UI on the user's host with no login" |

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/wbmon/ui/app.py` (429 lines) | Streamlit entrypoint, 4 tabs | VERIFIED | Tabs in order «📈 Позиции», «🗂 Выдача», «🩺 Журнал сборов», «ℹ️ О сервисе» (confirmed by AppTest) |
| `src/wbmon/ui/data.py` (430) | read-only cached loaders | VERIFIED | all SQL constants SELECT/WITH; short-lived connections |
| `src/wbmon/ui/series.py` (690) | pure state machine, tables, CSV | VERIFIED | FOUND/ABSENT/UNKNOWN |
| `src/wbmon/ui/charts.py` (355) | position chart + heatmap | VERIFIED | single shapes list, escaped text |
| `src/wbmon/ui/texts.py` (349) | Russian copy | VERIFIED | |
| `.streamlit/config.toml` | theme, `showErrorDetails="none"` | VERIFIED | |
| `tests/ui/*` (7 files + dbbuilder) | AppTest, series, charts, guardrails, perf | VERIFIED | see below |

### Key Link Verification

| From | To | Via | Status |
|------|----|-----|--------|
| `app.py` | `data.py` loaders | `data.load_*` cached twins keyed on `DataVersion` | WIRED |
| `app.py` | `series.build_points` -> `charts.position_figure` -> `st.plotly_chart(width="stretch")` | positions tab | WIRED (chart rendered with real values in AppTest) |
| `app.py` | `charts.status_heatmap` | `st.container(height=480, border=True)` | WIRED (2 plotly charts present) |
| `data.py` | `wbmon.storage.db.connect_ro` | every raw loader | WIRED |
| `series.csv_bytes` | `st.download_button` | `render_product_tables` | WIRED |

### Data-Flow Trace (Level 4)

| Artifact | Data | Source | Real data | Status |
|----------|------|--------|-----------|--------|
| position chart | `points` | `load_run_grid` + `load_observations` (SQL over runs/run_queries/observations) | yes (visual_check temp DB: positions with None gaps) | FLOWING |
| characteristics/history | `points`, `options` | same loaders | yes | FLOWING |
| snapshot | `load_snapshot` | `observations` by run/query | yes | FLOWING |
| run log + heatmap | `load_run_log`, `load_status_matrix` | `runs`, `run_queries` | yes | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full offline suite | `.venv/bin/python -m pytest -q` | 1209 passed, 1 skipped (docker not installed), 24 deselected, 19 s | PASS |
| Slow UI perf (UI-12) | `pytest -q -m slow tests/ui/test_perf.py` | 8 passed in 7.96 s | PASS |
| Lint | `ruff check src tests`; `ruff format --check src tests` | "All checks passed!"; 72 files already formatted (the Phase 1 I001 in `tests/factories.py` noted in the SUMMARYs no longer reproduces) | PASS |
| AppTest on `visual_check` temp DB (WBMON_DB_PATH, built by `dbbuilder.py`) | custom script | no exceptions, 4 tabs, 70 queries, default 5, query switch OK, reversed Y `[30,-0.5]`, 4 band shapes, `connectgaps False` | PASS |
| Headless Streamlit on 127.0.0.1:8613 from repo root | `streamlit run src/wbmon/ui/app.py` | `/_stcore/health` -> `ok`, root HTTP 200, no config warnings in log; server stopped (port down afterwards); tmp dir deleted | PASS |
| UI cannot write | `mode=ro` connection to the same DB | INSERT -> "attempt to write a readonly database"; counts unchanged | PASS |

### Probe Execution

Step 7c: SKIPPED (no probes declared for this phase).

### Requirements Coverage

All 13 IDs appear in plan frontmatter (03-01: STOR-05, UI-01, UI-02, UI-10, UI-12; 03-02: UI-03, UI-04, UI-05, UI-06, UI-09, UI-12; 03-03: UI-07, UI-08, UI-09, UI-11; 03-04: UI-01, UI-10, UI-12, STOR-05) and in REQUIREMENTS.md (all marked Complete, Phase 3). No orphaned Phase 3 requirements.

| Requirement | Source Plan | Status | Evidence |
|-------------|-------------|--------|----------|
| STOR-05 | 03-01, 03-04 | SATISFIED | `connect_ro` (`mode=ro` + `query_only`), `closing()` per call, source guard test `tests/ui/test_ui_guardrails.py`, live INSERT refusal |
| UI-01 | 03-01, 03-04 | SATISFIED (in-app); public link deferred to Phase 4 / OPS-03 | read-only open; zero/one/many/all-blocked states in State Matrix AppTests |
| UI-02 | 03-01 | SATISFIED | freshness badge, banner, sidebar totals (`series.totals_lines`) |
| UI-03 | 03-02 | SATISFIED | 70 options «NN. text», sheet order |
| UI-04 | 03-02 | SATISFIED | multiselect `nm_id · brand · name`, default top-5 of latest data run, key resets |
| UI-05 | 03-02 | SATISFIED | reversed Y, МСК X, lines+markers, hover, legend |
| UI-06 | 03-02 | SATISFIED | ABSENT only on ok AND complete; grey bands; None gaps |
| UI-07 | 03-03 | SATISFIED | characteristics + history frames |
| UI-08 | 03-03 | SATISFIED | `utf-8-sig`, `;`, decimal comma |
| UI-09 | 03-02, 03-03 | SATISFIED | period segmented control, applied to chart, tables and CSV |
| UI-10 | 03-01, 03-04 | SATISFIED | run log table + heatmap |
| UI-11 | 03-03 | SATISFIED | «Выдача» tab |
| UI-12 | 03-01, 03-02, 03-04 | SATISFIED | cache keyed on DataVersion (+ ttl 60); slow perf test on 2.0M observations passes (<1 s per loader) |

### Anti-Patterns Found

None. `grep` for TBD/FIXME/XXX/TODO/HACK in `src/wbmon/ui`, `tests/ui`, `.streamlit`: no matches. No `unsafe_allow_html`, `use_container_width`, `connect_rw` or write SQL keywords in `src/wbmon/ui`. No `data/` directory in the repo (no stray DB). Known "stub" `n_queries or 70` fallback is for an initialised DB with no queries and is intentional UI-SPEC copy.

### Human Verification Required

#### 1. Visual browser check (owned by the orchestrator)

**Test:** `python tests/ui/dbbuilder.py --scenario visual_check <tmp>/v.sqlite3` and `--scenario all_blocked`, then `streamlit run src/wbmon/ui/app.py` from the repo root and inspect all four tabs.
**Expected:** position 1 on top, grey bands and diamonds clearly visible, heatmap glyphs legible, banner/badge colours match 03-UI-SPEC, Excel opens the CSV with correct Cyrillic.
**Why human:** visual appearance and Excel behaviour are not machine-verifiable.

### Gaps Summary

No gaps. All five ROADMAP success criteria and all 13 requirement IDs are backed by code that exists, is wired and carries real data (checked independently of the SUMMARYs: full test suite, slow perf test, ruff, AppTest and headless-server smoke on a throwaway DB). The only open items are the visual human check (orchestrator) and the UI-01 public link (Phase 4 / OPS-03), neither of which fails this phase. Housekeeping: temp DB directory deleted; the streamlit process on port 8599 seen in `ps` is not mine (orchestrator's visual check).

---

_Verified: 2026-10-16_
_Verifier: Claude (gsd-verifier)_
