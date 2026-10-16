# Requirements: WB SERP Monitor

**Defined:** 2026-10-16
**Core Value:** Real Wildberries search observations are collected on schedule, appended to history without ever being lost or fabricated, and the UI shows each product's position history honestly (gaps where a product was not found — never invented positions).

Sources: TZ (`TZ_Wildberries_short_2026.pdf`), `.planning/PROJECT.md`, `.planning/research/*.md`.

## v1 Requirements

### Query source (QRY)

- [ ] **QRY-01**: The collector loads the query list from the Google Sheet «Запросы WB» CSV export (follows redirects, decodes `utf-8-sig`, `csv.reader`, no header row) on every run.
- [ ] **QRY-02**: Query wording is preserved byte-for-byte end to end (no strip/lowercase/Unicode normalization; `ё`, typos, Cyrillic «х» vs Latin «x» stay distinct) and is passed to WB via httpx `params=` (no manual pre-quoting).
- [ ] **QRY-03**: A bundled copy of the 70 queries (identical to the PDF appendix and the sheet as of 2026-10-16) is used when the sheet is unreachable or returns an implausible list; the source used (`sheet` / `bundled`) is recorded on the run.
- [ ] **QRY-04**: Queries are registered in the DB with a stable id and their sheet order; a query that disappears from the sheet keeps its history (never deleted).

### Collection (COL)

- [ ] **COL-01**: For each query the collector requests the WB public search JSON (`exactmatch/ru/common/v{N}/search`, `sort=popular`, `resultset=catalog`, fixed `dest` for Moscow, `curr=rub`) with a plain `httpx` client, ordinary headers and a polite sequential rate (configurable delay).
- [ ] **COL-02**: It paginates until `top_n` products are collected (default 200, configurable via env/CLI), a page is empty, or a page wrap-around (page N repeats page 1, detected by an id fingerprint) occurs; positions are 1-based across pages in the exact array order (ads included).
- [ ] **COL-03**: Every HTTP outcome is classified before parsing: `ok`, `blocked` (403, 498, HTML body at 200), `rate_limited` (429, `Retry-After` honoured at most once), `error` (5xx/timeouts after ≤2 bounded retries; schema drift; `products: null`); 200-with-zero-products is recorded as an error, never as "no results".
- [ ] **COL-04**: A failing query never aborts the run (per-query isolation); a circuit breaker stops sending requests after 3 consecutive `blocked` queries and records the remaining queries as `blocked` (`circuit_open`) without requests.
- [ ] **COL-05**: The collector never attempts anti-bot circumvention (no TLS impersonation, no stealth/headless browser, no token/cookie harvesting or replay, no CAPTCHA/PoW solving, no proxy rotation) — enforced by dependency list and code review.
- [ ] **COL-06**: Each (run, query) attempt is logged with status, HTTP status, pages fetched, items collected, completeness (`items == top_n` or end of results), WB-normalized query (`metadata.normquery` when present), error text and duration.

### Normalized fields (DATA)

- [ ] **DATA-01**: Each observation stores: query id, run id, collection timestamp (UTC), position, `nm_id`.
- [ ] **DATA-02**: Buyer price in rubles = minimum over sizes of `sizes[].price.product / 100`; `basic`, `total`, `logistics` kept as audit columns; NULL (not 0) when no priced size exists.
- [ ] **DATA-03**: Rating = `reviewRating` (0/missing → NULL); review count = `feedbacks` (missing → NULL); `nmReviewRating`/`nmFeedbacks` stored as extra columns.
- [ ] **DATA-04**: Delivery: raw `time1`/`time2` (hours) stored; `delivery_days` = calendar-day difference in Europe/Moscow between collection time and collection time + (time1 + time2) hours (today = 0, tomorrow = 1), computed by one pure, unit-tested function so it can be recalibrated; NULL when the inputs are missing.
- [ ] **DATA-05**: Seller name = `supplier` (+ `supplierId`); product name and brand stored for search/labels.
- [ ] **DATA-06**: Promoted slots flagged (`is_promoted` from the `log` object) with ad fields (`promoPosition`, organic `log.position`, `tp`, `cpm`) kept for audit.
- [ ] **DATA-07**: No field is ever invented: anything unavailable is NULL; normalizer is a pure function covered by fixture tests (both `data.products` and top-level `products` shapes, multi-size prices, missing fields, ads).

### Storage (STOR)

- [ ] **STOR-01**: SQLite database (WAL, `busy_timeout`, `foreign_keys=ON`) with tables for queries, runs, run_queries (attempt log), observations; DDL created idempotently by `init-db`.
- [ ] **STOR-02**: History is append-only: observations are never updated or deleted (DB triggers forbid UPDATE/DELETE); re-running collection only appends; no `INSERT OR REPLACE`.
- [ ] **STOR-03**: Each query's observations are written in one transaction together with its run_queries row; a crashed run stays visible (`running` → `abandoned` on next start).
- [ ] **STOR-04**: Indexes support: list queries; list `nm_id`s ever seen for a query; positions of selected `nm_id`s over time; latest characteristics; run log — each answering in < 1 s on a week of data (~2.4M rows).
- [ ] **STOR-05**: Read-only access path for the UI (`mode=ro` URI + `query_only`), short-lived connections.

### Scheduling & operation (SCHED)

- [ ] **SCHED-01**: `wbmon collect` performs one full run (all queries) and exits; usable manually and by the scheduler.
- [ ] **SCHED-02**: `wbmon scheduler` triggers a run every hour at :00 Europe/Moscow as a fresh subprocess, runs once at start if the current hourly slot has no run (45-min grace), never back-fills past hours, never overlaps runs (file lock + UNIQUE hourly slot).
- [ ] **SCHED-03**: The scheduler survives failed runs and restarts (no manual intervention); a heartbeat/latest-run signal is exposed for healthchecks and the UI.
- [ ] **SCHED-04**: Structured logs for every run and query (stdout), suitable for `docker compose logs`.

### Web UI (UI)

- [ ] **UI-01**: Streamlit app (Russian UI) reachable by a public link; opens read-only on the shared DB; renders correctly with zero, one, or many runs and with all-blocked runs (clear empty/blocked states).
- [ ] **UI-02**: Header shows data freshness («Последний успешный сбор: … МСК, N мин назад») and a banner when the latest run was blocked/partial; sidebar shows totals (runs, successful runs, first collection, observations, distinct products).
- [ ] **UI-03**: User selects a search query from the list of all 70 queries (original wording, sheet order).
- [ ] **UI-04**: User selects one or more `nm_id`s ever found for that query (searchable labels `nm_id · brand · name`), defaulting to the current top-5 of the latest successful run; selection resets sensibly when the query changes.
- [ ] **UI-05**: Interactive Plotly chart of position over time for the selected products: position 1 at the top (reversed Y), Moscow time on X, hover shows date/time, `nm_id`, position (plus price), `lines+markers`, legend per product.
- [ ] **UI-06**: Honest gaps: the chart is built on the full grid of runs for the query; a product absent from a complete run gets a gap (`None`), never an invented value; runs where the query was blocked/partial/error are visually marked (grey band or marker) and do not count as "absent".
- [ ] **UI-07**: Table of collected characteristics for the selected products: latest position, price, rating, reviews, delivery days, seller, ad flag, last-seen time, best/worst position, WB product link; plus a full observation history table for the selection.
- [ ] **UI-08**: CSV export of the shown history (Excel-friendly `utf-8-sig`).
- [ ] **UI-09**: Time-range filter for the chart/table (default: all collected history).
- [ ] **UI-10**: «Журнал сборов» tab: run history (start МСК, duration, status, ok/partial/blocked/error counts, products stored) and a query × run status heatmap.
- [ ] **UI-11**: «Выдача» tab: full top-N snapshot of one query at a chosen run (position, ad flag, nm_id link, name, brand, price, rating, reviews, delivery days, seller).
- [ ] **UI-12**: Cached reads keyed on the latest run id (fresh data appears after each run without restart); the app stays responsive on a week of data.

### Deployment (OPS)

- [ ] **OPS-01**: One Docker image; `docker compose up -d` starts `init-db` (one-shot), `collector` (scheduler) and `web` (Streamlit) sharing one named volume; `restart: unless-stopped`; healthchecks; log rotation; Europe/Moscow scheduling with UTC storage; `tzdata` installed.
- [ ] **OPS-02**: The same project runs without Docker (venv + CLI) for local development; documented `.env.example` covers every setting (top_n, dest, delay, DB path, sheet URL, timezone).
- [ ] **OPS-03**: Deployed on a host where the UI is reachable by a public link; a go/no-go probe (`wbmon probe` / `collect --limit-queries 2`) records whether WB serves plain requests from that host.
- [ ] **OPS-04**: Test suite (pytest) runs offline with fixtures and temp DBs only (no live WB calls, no synthetic data in the real DB); CI-style command documented.

### Deliverables (DOC)

- [ ] **DOC-01**: README in Russian: what it does, architecture diagram, data model, field definitions (price/rating/delivery semantics and assumptions), how to run (Docker and local), configuration, operational notes, limitations (incl. WB anti-bot situation, honestly).
- [ ] **DOC-02**: Write-up for the employer (Russian): time spent, difficulties and how they were solved, AI tools used (Claude Code + GSD multi-agent workflow), links to repo and live UI.
- [ ] **DOC-03**: Source archive (zip) of the repository as an alternative to the repo link.

## v2 Requirements

Deferred — mention in the write-up only.

- **V2-01**: Postgres backend option (SQLAlchemy URL) for multi-host setups.
- **V2-02**: Alerts (Telegram) on blocked runs or big position drops.
- **V2-03**: Organic-rank (excluding ads) chart mode.
- **V2-04**: Multi-region comparison (several `dest` values).
- **V2-05**: Automatic DB backups / retention policy.

## Out of Scope

| Feature | Reason |
|---------|--------|
| Any anti-bot circumvention (TLS impersonation, stealth/headless browsers, token harvesting/replay, CAPTCHA/PoW solving, proxy rotation) | Hard project constraint (ethics/platform rules) |
| Synthetic, demo or back-filled observations in the real DB | TZ §5 forbids artificial history; Core Value |
| Public "run now" button in the UI | Abuse vector; scheduler is the only trigger in production |
| User accounts / auth in UI | Employer only needs a link |
| Per-product card requests (detail API) | 70×200 hourly must stay light; search response has the needed fields |
| Personalized prices (logged-in discounts, WB Wallet) | Anonymous Moscow buyer price is the comparable baseline |
| Editing queries/settings in the UI | Sheet + env are the sources of truth |
| Data pruning | History must never be lost |

## Traceability

Which phases cover which requirements. Updated during roadmap creation (2026-10-16).

| Requirement | Phase | Status |
|-------------|-------|--------|
| QRY-01 | Phase 1 | Pending |
| QRY-02 | Phase 1 | Pending |
| QRY-03 | Phase 1 | Pending |
| QRY-04 | Phase 1 | Pending |
| COL-01 | Phase 1 | Pending |
| COL-02 | Phase 1 | Pending |
| COL-03 | Phase 1 | Pending |
| COL-04 | Phase 1 | Pending |
| COL-05 | Phase 1 | Pending |
| COL-06 | Phase 1 | Pending |
| DATA-01 | Phase 1 | Pending |
| DATA-02 | Phase 1 | Pending |
| DATA-03 | Phase 1 | Pending |
| DATA-04 | Phase 1 | Pending |
| DATA-05 | Phase 1 | Pending |
| DATA-06 | Phase 1 | Pending |
| DATA-07 | Phase 1 | Pending |
| STOR-01 | Phase 1 | Pending |
| STOR-02 | Phase 1 | Pending |
| STOR-03 | Phase 1 | Pending |
| STOR-04 | Phase 1 | Pending |
| STOR-05 | Phase 3 | Pending |
| SCHED-01 | Phase 1 | Pending |
| SCHED-02 | Phase 2 | Pending |
| SCHED-03 | Phase 2 | Pending |
| SCHED-04 | Phase 1 | Pending |
| UI-01 | Phase 3 | Pending |
| UI-02 | Phase 3 | Pending |
| UI-03 | Phase 3 | Pending |
| UI-04 | Phase 3 | Pending |
| UI-05 | Phase 3 | Pending |
| UI-06 | Phase 3 | Pending |
| UI-07 | Phase 3 | Pending |
| UI-08 | Phase 3 | Pending |
| UI-09 | Phase 3 | Pending |
| UI-10 | Phase 3 | Pending |
| UI-11 | Phase 3 | Pending |
| UI-12 | Phase 3 | Pending |
| OPS-01 | Phase 2 | Pending |
| OPS-02 | Phase 2 | Pending |
| OPS-03 | Phase 4 | Pending |
| OPS-04 | Phase 1 | Pending |
| DOC-01 | Phase 4 | Pending |
| DOC-02 | Phase 4 | Pending |
| DOC-03 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 45 total
- Mapped to phases: 45 (Phase 1: 24, Phase 2: 4, Phase 3: 13, Phase 4: 4)
- Unmapped: 0

**Notes:**
- STOR-03 (abandoned-run marking) is delivered in Phase 1 and exercised by scheduler restarts in Phase 2.
- UI-01's "public link" clause is delivered by Phase 4 (OPS-03); Phase 3 owns the in-app behaviour.
- OPS-04 starts in Phase 1 (offline pytest); later phases extend the suite under the same offline rule.

---
*Requirements defined: 2026-10-16*
*Last updated: 2026-10-16 after roadmap creation*
