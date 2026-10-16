# Roadmap: WB SERP Monitor

## Overview

From an empty repository to a publicly reachable Streamlit service backed by real hourly Wildberries
search history. Phase 1 builds the whole collection-and-storage core (query source, plain-httpx WB
client with honest response classification, pure normalizer, append-only SQLite, run orchestrator,
CLI, offline test suite) so that every outcome of a run - ok, blocked, error, partial - is recorded and
nothing is ever invented. Phases 2 and 3 both depend only on Phase 1 and can be executed in
parallel: one makes the collector run unattended in Docker (scheduler, packaging), the other builds the
Russian read-only UI on top of the Phase 1 schema. Phase 4 deploys to the user's host, runs the go/no-go
WB reachability probe there, lets real hourly runs accumulate, and ships the README, the employer
write-up and the source archive.

## Cross-Cutting Constraints (apply to every phase)

- **No anti-bot circumvention of any kind**: no TLS impersonation (curl_cffi, tls-client), no
  headless/stealth browsers, no `x_wbaas_token` / cookie harvesting or replay, no CAPTCHA/PoW solving,
  no proxy rotation. Plain `httpx`, ordinary headers, polite sequential rate. 403 / 498 / 429 are
  recorded as `blocked` / `rate_limited`, never worked around.
- **No synthetic observations in a real database**: test fixtures live in `tests/` and temporary
  databases only; there is no seed, demo or back-fill path to the production DB.
- **Unknown is explicit**: anything unavailable is NULL, a failed query is `blocked` / `error` /
  `abandoned`, a gap in the UI is a gap - positions are never invented.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Collection Core** - Package, append-only SQLite, query source, WB client with response classification, normalizer, run orchestrator, CLI (`init-db` / `collect` / `probe`), offline test suite
- [ ] **Phase 2: Unattended Operation & Packaging** - Hourly MSK scheduler with lock/catch-up/heartbeat, Dockerfile, Docker Compose, `.env.example`, local no-Docker path
- [ ] **Phase 3: Web UI** - Russian read-only Streamlit app: position chart with honest gaps, characteristics/history tables, CSV, run log with heatmap, SERP snapshot
- [ ] **Phase 4: Publish & Deliverables** - Deploy to the user's host with a public link, go/no-go probe, real hourly runs, Russian README, employer write-up, source zip

## Phase Details

### Phase 1: Collection Core
**Goal**: One command performs a complete, honest collection run - 70 queries x top-200 from the public WB search JSON, in SERP order - and appends every result (including blocked/failed attempts) to an append-only SQLite history, verifiable entirely offline.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: QRY-01, QRY-02, QRY-03, QRY-04, COL-01, COL-02, COL-03, COL-04, COL-05, COL-06, DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06, DATA-07, STOR-01, STOR-02, STOR-03, STOR-04, SCHED-01, SCHED-04, OPS-04
**Success Criteria** (what must be TRUE):
  1. `wbmon init-db` creates the SQLite database (WAL, foreign keys, schema version) and is safe to re-run; an attempt to UPDATE or DELETE a stored observation is rejected by the database itself, and re-running `wbmon collect` only appends new rows.
  2. `wbmon collect` loads the 70 queries from the Google Sheet (or from the bundled copy when the sheet is unreachable or implausible), records which source was used on the run, and the query text reaches the WB request byte-for-byte (`ё`, the «2-спальный» typo, Cyrillic «х» vs Latin «x» all stay distinct).
  3. Replaying recorded WB responses offline (both `data.products` and top-level `products` shapes, multi-page, ads, missing fields), every product is stored with position (1-based across pages, ads flagged), `nm_id`, buyer price, rating, review count, delivery days and seller; every unavailable field is NULL, never a guess; collection stops at `top_n`, an empty page, or a page-1 wrap-around.
  4. When WB answers 403 / 498 / 429 / HTML-at-200 / 5xx / `products: null` / 200-with-zero-products, the run still completes: each query has a logged attempt with status, HTTP code, pages, items, completeness and duration, failed queries write no observations, a circuit breaker records the remaining queries as `blocked` after 3 consecutive blocked queries, and an unfinished run is marked `abandoned` on the next start.
  5. `pytest` passes fully offline using fixtures and temporary databases only, and `wbmon probe` prints the classified outcome of one real request (a `blocked` result from the dev machine is an accepted, honestly reported outcome).
**Plans**: TBD

Plans:
- [ ] 01-01: TBD (to be created by /gsd-plan-phase 1)

**Notes**:
- Largest phase; suggested internal order for planning: package skeleton + schema/migrations + writer/reader -> pure normalizer + response classifier (fixture tests) -> query source + client + pagination + orchestrator + breaker -> CLI.
- Freeze the schema early with the fields that cannot be back-filled: name, brand, `is_promoted` and ad fields, raw `time1`/`time2`, price audit columns, per-query status/coverage, and the request parameter set used per run. Later schema changes go through a numbered migration (`PRAGMA user_version`).
- `pyproject.toml` declares the optional dependency groups (`web`, `dev`) up front so Phases 2 and 3 do not conflict on it when run in parallel.
- STOR-03 includes marking stale `running` runs as `abandoned` at the start of `collect`; Phase 2 only exercises this through scheduler restarts.
- STOR-04 (< 1 s on ~2.4M rows) is verified on a generated temporary database, never the real one.
- Research flags (resolve with recorded fixtures; live confirmation happens in Phase 4): endpoint host/version, delivery-days formula, price semantics, page-2 ad numbering.
- The dev machine's egress is blocked by WB (403): do not attempt to get around it; fixtures prove the pipeline.

### Phase 2: Unattended Operation & Packaging
**Goal**: The collector runs by itself every hour on the Moscow clock, survives failures and restarts without manual intervention, and the whole stack starts with one `docker compose up -d` (or without Docker for local development).
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: SCHED-02, SCHED-03, OPS-01, OPS-02
**Success Criteria** (what must be TRUE):
  1. `wbmon scheduler` starts a run at every :00 Europe/Moscow as a fresh `wbmon collect` subprocess; on start it runs once if the current hourly slot has no run (45-minute grace), never back-fills past hours, and two overlapping triggers cannot produce two runs for the same slot (file lock + UNIQUE hourly slot).
  2. A failed run, a killed run, a SIGTERM or a container restart does not stop the hourly cadence: the scheduler keeps going, the interrupted run shows as `abandoned`, and no manual step is needed.
  3. A heartbeat/latest-run signal exists and the collector healthcheck reports healthy while the scheduler is alive even if every run is `blocked`, and unhealthy when the heartbeat goes stale.
  4. `docker compose up -d` brings up `init-db` (one-shot), `collector` and `web` against one named volume with `restart: unless-stopped`, healthchecks and log rotation; scheduling is in Europe/Moscow while storage stays UTC; history survives `down` / `up`.
  5. The project runs without Docker (venv + CLI), and `.env.example` documents every setting (top_n, dest, delay, DB path, sheet URL, timezone).
**Plans**: TBD

Plans:
- [ ] 02-01: TBD (to be created by /gsd-plan-phase 2)

**Notes**:
- Runs in parallel with Phase 3 (both depend only on Phase 1). Touch disjoint files: scheduler/Docker/compose/env files here, `ui/` there.
- The compose `web` service targets the Phase 3 entrypoint (Streamlit app under `src/wbmon/ui/`); it becomes runnable once Phase 3 lands. The full three-service `compose up` check is completed in Phase 4 at the latest.
- Slot calculation and catch-up decision are pure functions tested with a fake clock. Missed hours stay missing (no back-fill) - honest history.
- Research flags: registry reachability (Docker Hub / PyPI) from a Russian VPS; two-container WAL check (writer in `collector`, read-only reader in `web`, shared read-write named volume, no `:ro` mount).
- Scheduler logs reuse the Phase 1 structured log format (SCHED-04).

### Phase 3: Web UI
**Goal**: A Russian-language Streamlit app lets the user pick a query and products and see an honest, readable history of positions - gaps where a product was not found, clear markers where we could not look - plus the collected characteristics and the run log.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: STOR-05, UI-01, UI-02, UI-03, UI-04, UI-05, UI-06, UI-07, UI-08, UI-09, UI-10, UI-11, UI-12
**Success Criteria** (what must be TRUE):
  1. Opening the app shows the Russian UI with a freshness header («Последний успешный сбор: … МСК, N мин назад»), a banner when the latest run was blocked/partial, and sidebar totals (runs, successful runs, first collection, observations, distinct products); it renders sensible empty/blocked states with zero, one, many and all-blocked runs, and the app cannot write to the database (read-only connection).
  2. On the «Позиции» tab the user picks a query from all 70 (original wording, sheet order), picks one or more `nm_id`s ever found for it (searchable `nm_id · brand · name`, defaulting to the current top-5 and resetting when the query changes), narrows a time range, and sees an interactive chart with position 1 at the top, Moscow time on X, and hover showing date/time, `nm_id`, position and price.
  3. Gaps are honest: a product absent from a complete run shows as a gap (never an invented position), runs where the query was blocked/partial/error are visibly marked and are not treated as "absent", and promoted (ad) slots are visibly marked on the chart.
  4. Below the chart the user sees a table of the selected products' characteristics (latest position, price, rating, reviews, delivery days, seller, ad flag, last seen, best/worst position, WB link) and the full observation history, and can download the shown history as a CSV that opens correctly in Excel with Cyrillic text.
  5. The «Журнал сборов» tab shows the run history (start МСК, duration, status, ok/partial/blocked/error counts, products stored) with a query x run status heatmap, the «Выдача» tab shows the full top-N snapshot of one query at a chosen run, and a newly completed run appears without restarting the app.
**Plans**: TBD
**UI hint**: yes

Plans:
- [ ] 03-01: TBD (to be created by /gsd-plan-phase 3)

**Notes**:
- Runs in parallel with Phase 2 against the Phase 1 schema (the contract). Develop and test against temporary databases built through the Phase 1 writer in test fixtures; synthetic rows exist only in those temp DBs, never in the real DB path.
- Series building lives in a pure module (`series.py`, point states FOUND / ABSENT / UNKNOWN) with unit tests; an `AppTest` smoke test covers the app; UI-12 responsiveness is checked on a generated week-sized temp DB.
- Implementation conventions from research: naive Moscow datetimes for Plotly, `st.plotly_chart(..., width="stretch")`, short-lived read-only connections under a cache keyed on the latest run id (plus a ttl), `mode=ro` URI + `PRAGMA query_only=ON`.
- UI-01's "reachable by a public link" clause is delivered by Phase 4 (OPS-03); this phase owns all in-app behaviour.

### Phase 4: Publish & Deliverables
**Goal**: The employer can open a public link to the running UI backed by real, honestly recorded hourly history, and has the repository/archive, a Russian README and a write-up.
**Mode:** mvp
**Depends on**: Phase 2, Phase 3
**Requirements**: OPS-03, DOC-01, DOC-02, DOC-03
**Success Criteria** (what must be TRUE):
  1. A public link opens the running Russian UI on the user's host with no login, and the stack survives a restart of the host/containers with its history intact.
  2. The go/no-go WB reachability probe was run from the deployment host and its result (served vs. `blocked`) is recorded; if WB blocks plain requests, the user's decision (another host, another data source, or shipping the honest blocked history) is recorded - no circumvention is attempted.
  3. «Журнал сборов» shows consecutive real hourly runs recorded unattended by the scheduler on the host; every row is genuine - served runs carry real observations, and if WB blocks, the log and the banner say so plainly - and no synthetic or back-filled observation exists in the database.
  4. A Russian README lets a reader run the project (Docker and local) and explains the architecture, data model, field semantics and assumptions (price, rating, delivery days, position, ads), configuration, operations and limitations including the WB anti-bot situation.
  5. A Russian write-up for the employer (time spent, difficulties and how they were solved, AI tools: Claude Code + GSD workflow) links the repository and the live UI, and a source archive (zip) of the repository exists as an alternative to the repo link.
**Plans**: TBD

Plans:
- [ ] 04-01: TBD (to be created by /gsd-plan-phase 4)

**Notes**:
- **Requires human checkpoints**: (a) the user provides the hosting (Russian VPS access, domain or IP) - the plan cannot proceed without it; (b) the user approves publishing (public repository, public UI link); (c) if the probe shows WB blocking, the user decides how to proceed; (d) the user supplies/approves the time-spent figure in the write-up.
- Real data only: if there are not yet enough real runs when the work is otherwise done, wait for more hourly slots - never fabricate history.
- Includes the leftover end-to-end check of the full three-service `docker compose up -d` on the real host, and live spot-checks of price/delivery/`dest` semantics if access allows (otherwise documented as derived/approximate).
- State the ToS exposure of automated collection honestly in README and write-up.

## Execution Order

Phases execute in numeric order, with one parallel branch: **1 -> (2 || 3) -> 4**.
Phases 2 and 3 each depend only on Phase 1 and can be planned and executed in parallel; Phase 4 waits for both.

## Requirement Coverage

| Phase | Requirements | Count |
|-------|--------------|-------|
| 1. Collection Core | QRY-01..04, COL-01..06, DATA-01..07, STOR-01..04, SCHED-01, SCHED-04, OPS-04 | 24 |
| 2. Unattended Operation & Packaging | SCHED-02, SCHED-03, OPS-01, OPS-02 | 4 |
| 3. Web UI | STOR-05, UI-01..12 | 13 |
| 4. Publish & Deliverables | OPS-03, DOC-01..03 | 4 |
| **Total** | | **45 / 45** |

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 / 3 (parallel) -> 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Collection Core | 0/TBD | Not started | - |
| 2. Unattended Operation & Packaging | 0/TBD | Not started | - |
| 3. Web UI | 0/TBD | Not started | - |
| 4. Publish & Deliverables | 0/TBD | Not started | - |
