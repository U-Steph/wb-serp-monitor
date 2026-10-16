---
phase: 03-web-ui
plan: 01
subsystem: ui
tags: [streamlit, read-only, sqlite, pandas, apptest, cache, freshness, run-log]

requires:
  - phase: 01-collection-core
    provides: connect_ro, Writer, schema v1, Settings, clock
  - phase: 02-scheduler-and-ops
    provides: scheduler_heartbeat.json contract (wbmon.health.HEARTBEAT_FILENAME)
provides:
  - read-only Streamlit shell (header, freshness badge, collector line, single status banner, sidebar totals, run-log table)
  - wbmon.ui data layer (connect_ro loaders, DataVersion-keyed cached twins, db_status, heartbeat reader)
  - pure header logic and Russian copy (series.py, texts.py)
  - test infrastructure for later UI plans (temp-only scenario DB builder, AppTest harness, D3-A1 guardrail amendment)
affects: [03-02, 03-03, 03-04]

tech-stack:
  added: []
  patterns:
    - "Raw loader + st.cache_data twin keyed on (path, DataVersion), one contextlib.closing(connect_ro) per call"
    - "Pure series/texts modules (no streamlit, sqlite3, clock): every function takes now explicitly"
    - "Scenario DBs built through the real Phase 1 Writer, refused unless under the system temp dir and outside the repo"

key-files:
  created:
    - src/wbmon/ui/__init__.py
    - src/wbmon/ui/data.py
    - src/wbmon/ui/texts.py
    - src/wbmon/ui/series.py
    - src/wbmon/ui/app.py
    - .streamlit/config.toml
    - tests/ui/__init__.py
    - tests/ui/conftest.py
    - tests/ui/dbbuilder.py
    - tests/ui/test_dbbuilder.py
    - tests/ui/test_data.py
    - tests/ui/test_series.py
    - tests/ui/test_app.py
  modified:
    - tests/test_guardrails.py

key-decisions:
  - "Banners are rendered with icon=\"\" so the UI-SPEC leading emoji stays in the alert text (Streamlit would otherwise lift it into the icon and strip it from the body)"
  - "Phase 1 tests/factories.py has a pre-existing ruff I001 that was left untouched (outside the Phase 3 ownership rule)"
  - "Cache key extends CONTEXT's (db_path, latest_run_id) with last_rq_id and latest_status so in-flight query rows and status changes also invalidate"
  - "'после из' query counts use the genitive (queries_genitive: 1 запроса, 3 запросов) while the UI-SPEC wording is kept"

patterns-established:
  - "Tests import the harness as ui.dbbuilder (tests/ui is a package; tests/ is on sys.path via the root conftest)"
  - "tests/ui/conftest.py ignores the whole UI suite when streamlit/pandas/plotly are not installed (dev-only CI)"

requirements-completed: [STOR-05, UI-01, UI-02, UI-10, UI-12]

duration: 40min
completed: 2026-10-16
---

# Phase 3 Plan 01: Read-only app shell Summary

**Read-only Streamlit shell that already answers "is this data fresh and real": freshness badge, collector line, one status banner, sidebar totals and the «🩺 Журнал сборов» table, all through short-lived `connect_ro` loaders cached on a `DataVersion` key.**

## Performance

- **Tasks:** 3 of 3
- **Files:** 13 created, 1 modified
- **Tests:** full offline suite 838 passed (was 720), 16 slow deselected

## Accomplishments

- `streamlit run src/wbmon/ui/app.py` opens a Russian page in every header state of the UI-SPEC matrix: no DB, 0-byte file, non-SQLite file, zero runs, all-blocked, mixed history, latest run running / partial / blocked / error / abandoned. Each shows the UI-SPEC copy; no traceback and no DB path reach the page (`showErrorDetails = "none"`, generic Russian errors, details to the server log).
- Freshness badge (green < 90 min, yellow < 3 h, red otherwise, gray «Успешных сборов пока нет»; successful = `ok|partial`), collector line from `scheduler_heartbeat.json` (live / stale / absent / «идёт сбор» for a latest `running` run younger than 60 min, D3-A4) and at most one banner by the first-match rules.
- Sidebar totals with U+202F thousands separators; run table (latest 200 runs, newest first) with МСК start, duration, type, emoji status, query counts, products stored; NULLs shown as «—», running rows as «идёт».
- Read-only proven: INSERT through the UI connection raises `OperationalError`; row counts and `user_version` are unchanged after an AppTest run; a missing DB file is never created; each loader opens exactly one connection and closes it.
- Cache invalidation (UI-12): appending a run through the Phase 1 Writer and re-running the same AppTest moves the totals from 6 to 7 without a restart.
- D3-A1 guardrail amendment: everything outside `src/wbmon/ui` stays httpx-only; `ui` may import only streamlit/pandas/plotly; the forbidden-text scan still covers `ui`.
- Smoke check: Streamlit started headless on 127.0.0.1 against a scratch all-blocked DB (health `ok`, no config warning), then stopped. No network beyond loopback.

## Task Commits

1. **Task 1: Guardrail amendment, temp-only scenario DB builder, shell AppTests** - `feed284` (test)
2. **Task 2: Read-only data layer, Russian copy, pure header logic** - `bc33640` (feat)
3. **Task 3: Streamlit entrypoint and theme file** - `edd898a` (feat)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Leading emoji vanished from banner text**
- **Found during:** Task 3 (AppTest run)
- **Issue:** Streamlit 1.66 `st.info/warning/error` lifts a leading emoji into the alert icon and strips it from the body, so the UI-SPEC strings («⏳ Идёт сбор…») no longer appeared in the element value.
- **Fix:** banners are called with `icon=""`, which keeps the exact UI-SPEC copy (emoji inline) in the text.
- **Files modified:** `src/wbmon/ui/app.py`
- **Commit:** `edd898a`

**2. [Rule 1 - Bug] Config test could not see the file's values**
- **Found during:** Task 3
- **Issue:** `streamlit.config.get_option` reads `.streamlit/config.toml` from the process cwd, and the root conftest chdirs to `tmp_path`, so `client.showErrorDetails` returned the default `full`.
- **Fix:** the key-resolution test asserts the file content; a second test runs `get_option` in a subprocess with cwd = repo root and asserts `none` and `#7B2CBF`.
- **Files modified:** `tests/ui/test_app.py`
- **Commit:** `edd898a`

**3. [Rule 1 - Bug] Test expectation of the next attempt hour**
- **Found during:** Task 2 — my own test assumed 15:00 for a fixed `now` of 12:00Z (15:00 МСК); the correct next full Moscow hour is 16:00. Test corrected, code was right.

### Minor choices within the plan

- Banner wording uses the genitive after «из» (`1 запроса`, `3 запросов`) through `texts.queries_genitive`; the 3-queries text is identical to the UI-SPEC copy.
- The guardrail edit was written with a small shared helper `_third_party()` instead of duplicating the loop; test names and assertions are as specified (`<= {"httpx"}` for core, `<= UI_THIRD_PARTY` for ui), one test added (8 -> 9 `def test_`).
- `heartbeat_line` takes `stale_s` as a keyword (series stays pure); `app.py` passes `data.HEARTBEAT_STALE_S`.

## Deferred Issues

- `ruff check src tests` still reports one **pre-existing** I001 in the Phase 1 file `tests/factories.py:256` (import order inside `build_wb_client`). It reproduces on the unmodified phase-planning commit `6880ff2` (verified in a scratch `git archive` copy) and the file is outside the Phase 3 ownership rule, so it was left untouched. Everything this plan touches is ruff-clean (`ruff check src/wbmon/ui tests/ui tests/test_guardrails.py`, `ruff format --check src tests`). Fix is a one-line reorder (`from wbmon import clock` before `from wbmon.wb_client import ...`); the orchestrator can apply it when merging.

## Known Stubs

- `texts.subtitle(overview["n_queries"] or 70, ...)` falls back to 70 queries only when the `queries` table is empty (initialised DB with no runs yet). Intentional per UI-SPEC copy; with data the real query count is shown.
- Tab `ℹ️ О сервисе`, `🗂 Выдача`, `📈 Позиции` constants exist in `texts` but the tabs are added by plans 03-02..03-04 (planned, not a defect).

## Threat Flags

None. No new network endpoint, auth path or schema change; the only new file read is the heartbeat JSON (already in the plan's threat model, T-03-06), parsed defensively.

## Self-Check: PASSED

- Files exist: `src/wbmon/ui/{__init__,data,texts,series,app}.py`, `.streamlit/config.toml`, `tests/ui/{__init__,conftest,dbbuilder,test_dbbuilder,test_data,test_series,test_app}.py`
- Commits present: `feed284`, `bc33640`, `edd898a`
- `PYTHONPATH=src pytest -q`: 838 passed; `ruff check src/wbmon/ui tests/ui tests/test_guardrails.py` and `ruff format --check src tests` clean
- Acceptance greps: no `connect_rw`/writer/migrations/lock in `ui`; 5 `closing(connect_ro(` and 3 `cache_data(ttl=CACHE_TTL_S` lines; `COUNT(DISTINCT nm_id)` on exactly one line; no streamlit/sqlite3/os/time/socket/httpx/subprocess/numpy imports in `series.py`/`texts.py`; no `unsafe_allow_html`, `use_container_width`, `st.write(`, relative imports or write keywords in `src/wbmon/ui`
