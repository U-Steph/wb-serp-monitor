# Project Research Summary

**Project:** WB SERP Monitor
**Domain:** Wildberries search-position tracker — hourly collector → append-only SQLite history → Streamlit/Plotly UI
**Researched:** 2026-10-16
**Confidence:** MEDIUM (stack/architecture HIGH; WB payload semantics & host reachability MEDIUM/LOW)

## Executive Summary

A small scrape-and-track system: one sequential, polite collector requests the public WB search JSON
(70 queries × 2 pages ≈ 140 requests/hour), appends observations to SQLite (WAL), and a separate
read-only Streamlit app draws position-over-time charts. Design = functional core (pure
parse/normalize/classify) inside an imperative shell (httpx, sqlite3, clock, scheduler), single writer,
one transaction per query, and every "unknown" made explicit (blocked / error / partial / abandoned) so
the UI never invents a position and never confuses "left the top-200" with "we didn't see it".

Recommended build: one package `wbmon` (src layout), one Docker image with several commands, stdlib
sqlite3, plain httpx, Streamlit 1.66 + Plotly 7.1, Docker Compose with a named volume. Freeze the schema
early with non-backfillable fields: name, brand, is_promoted, raw delivery hours, price audit columns,
per-query status/coverage.

The dominant risk is not technical: since late September 2026 WB's anti-bot layer returns 403/498 to
non-browser clients, and reachability from the deploy host is unknown. Circumvention is forbidden by
project constraint, so mitigation = go/no-go probe from the real host early, honest `blocked`
classification with a circuit breaker, and a run-log UI where "blocked" is a first-class state.
Secondary risks: payload drift (`data.products` vs top-level `products`), past-the-end pagination
wrapping back to page 1, and unverified semantics (delivery days, price field, `dest`) — handled by
tolerant parsing, raw-field storage, and one-time browser spot-checks.

## Orchestrator decisions (conflict resolutions)

- **Scheduler:** `wbmon scheduler` sleeps until the next :00 Europe/Moscow and launches each run as a
  fresh subprocess (`python -m wbmon collect`); guarded by a file lock + UNIQUE hourly slot in `runs`;
  45-minute catch-up grace for the current slot only (no back-fill). Rationale: per-run isolation like
  supercronic, no extra binary, identical locally and in Docker. supercronic / systemd timer are
  documented alternatives only; APScheduler rejected.
- **Package/config:** package `wbmon`, src layout; config = frozen dataclass from env vars (prefix
  `WBMON_`), no pydantic-settings.
- **Price:** min over sizes of `sizes[].price.product` / 100; also store `basic`, `total`, `logistics`.
- **Rating/reviews:** rating = `reviewRating` (0/missing → NULL); review count = `feedbacks`; also
  `nmReviewRating`, `nmFeedbacks`.
- **Seller:** `supplier` + `supplierId`.
- **Delivery:** store raw `time1`/`time2` and `delivery_hours`; `delivery_days` from one pure function
  (Europe/Moscow calendar-day diff) so it can be recalibrated without data loss.
- **Position:** 1-based array index across pages, ads included; store `is_promoted` + ad fields; detect
  page wrap-around by a first-ids fingerprint and stop.
- **Anti-bot:** plain httpx only; 403/498/HTML-at-200 → `blocked`; 429 → `rate_limited` (honour
  Retry-After once); circuit breaker after **3** consecutive blocked queries (research said 3–5; 3
  adopted); no circumvention of any kind.
- **Deployment:** Docker Compose (init-db one-shot, collector, web, optional caddy) on a Russian VPS if
  the user provides one; WB reachability from that host is an explicit go/no-go probe.
- **Healthcheck:** collector health = heartbeat freshness (blocked runs are not "unhealthy").
- **Pacing:** sequential requests, ~1.5–3 s apart.

## Key Findings

### Recommended Stack (STACK.md — HIGH)

- Python 3.12-slim image (+ `tzdata` pip dependency); dev machine is 3.14 → keep code ≥3.11-compatible.
- `httpx>=0.28.1,<1`; `follow_redirects=True` only for the Google Sheet export.
- SQLite WAL via stdlib `sqlite3`, `BEGIN IMMEDIATE` per query, `synchronous=NORMAL`,
  `busy_timeout` (collector 30 s / web 5 s), `foreign_keys=ON`.
- Streamlit 1.66, Plotly 7.1, pandas 3.0 (web only). `st.plotly_chart(fig, width="stretch")` —
  `use_container_width` was removed in 1.57.
- Named volume `wbdata` mounted read-write in both containers; read-only enforced at the connection
  (`mode=ro` URI + `PRAGMA query_only=ON`), not via a `:ro` mount (WAL readers need `-shm`).
- Optional Caddy 2.11 (sslip.io when no domain). Dev: pytest, httpx `MockTransport`, ruff.
- **Avoid:** any anti-bot tooling, APScheduler 4.x (alpha), cron daemons in containers, pandas for
  the query CSV, a global long-lived SQLite connection in Streamlit, `st.cache_data` without ttl,
  Streamlit Community Cloud as primary host (needs remote DB, sleeps after inactivity).

### Expected Features (FEATURES.md — MEDIUM)

**Table stakes:** hourly 70 × top-200 collection in SERP order, append-only, run on start; run log with
per-query status + coverage; freshness badge, blocked banner, totals, Russian empty states; query
selectbox, searchable multiselect (default current top-5), time-range filter; Plotly chart with
reversed Y, true gaps on the full run grid, lines+markers, МСК hover; characteristics table with WB
links; Excel-safe CSV (`utf-8-sig`); «Журнал сборов» tab.

**Differentiators (in order):** failed-run grey bands, ad markers, best/worst/presence columns + Δ vs
previous run, query × run status heatmap, «Выдача» snapshot tab, collector heartbeat, «О сервисе»
expander.

**Defer:** price-history charts, alerts, multi-region, Postgres, REST API, retention.
**Never:** anti-bot workarounds, synthetic data, "run now" button, auth, per-product detail requests.

### Architecture Approach (ARCHITECTURE.md — HIGH)

One-way data flow: Sheet/WB → client → normalize → orchestrator → writer → SQLite → reader → UI; the
web container cannot trigger collection. Components: QuerySource, WbSearchClient + classify, pure
normalize, RunOrchestrator, StorageWriter / StorageReader, Scheduler + lock, UI (`series.py` /
`charts.py` / `app.py`); an import-boundary test enforces layering. Tables: `queries`, `runs`
(UNIQUE slot for scheduler runs), `run_queries` (status, http, pages, items, coverage, error class),
`products` (the only mutable table), `observations` (immutable via triggers). Migrations via
`PRAGMA user_version`; `wal_checkpoint(TRUNCATE)` after each run; readers use short-lived connections
under `st.cache_data(ttl)`. UI point states: FOUND, ABSENT (gap — only if the run was complete),
UNKNOWN (gap + band).

### Critical Pitfalls (PITFALLS.md — MEDIUM)

1. **Block treated as "no results"** → classify before parsing; `ok` requires items > 0 (CHECK);
   circuit breaker; never reuse old data.
2. **Host reachability discovered too late** → probe from the real deploy host first; if blocked,
   surface the decision to the user (different host / licensed data source / ship honest blocked
   history) — never circumvent.
3. **Past-the-end pagination returns page 1** → fingerprint wrap detection + safety cap; don't trust
   `total`; derive page size from `len(page1)`.
4. **Incomplete run read as "not found"** → per-query coverage status; bands for blocked/partial runs.
5. **Unverified field semantics baked in at ingest** → store raw inputs, derive in one pure function,
   spot-check in a browser with a Moscow address; pin and store the request param set per run.

Also: no seed path to the production DB; naive МСК datetimes for Plotly; WAL on a read-write named
volume; `tzdata` in the slim image; byte-exact query text (ё, Cyrillic «х» vs Latin «x»).

## Implications for Roadmap

Suggested coarse phases:

1. **Foundation & data contract** — package skeleton (config, clock, models, bundled 70-query
   fallback), frozen schema + migrations + writer/reader with append-only triggers, pure normalizer and
   classifier with fixture tests. Avoids pitfalls 5, 6, 8, 9, 20.
2. **Collection pipeline + access probe** — QuerySource (sheet + fallback), WbSearchClient (pacing,
   retry, pagination + wrap detection), RunOrchestrator (isolation, breaker, statuses), `wbmon collect`
   / `wbmon probe` CLI; fixture tests for 403/498/429/HTML-at-200/null products/both shapes/page-2 =
   page-1/query encoding. Avoids pitfalls 1, 3, 4, 7, 10, 11, 18.
3. **Unattended operation & packaging** — scheduler (subprocess per run, lock, UNIQUE slot, catch-up,
   heartbeat, SIGTERM), abandoned-run recovery, healthchecks, Dockerfile + compose, log rotation.
   Deploy early so real hourly history accumulates while the UI is built.
4. **Web UI** — can start in parallel with phase 3 against test-only temp DBs; header freshness +
   banner, sidebar totals, «Позиции» tab (selectors, chart, table, CSV), «Журнал сборов» (run table +
   heatmap), «Выдача» snapshot; pure `series.py` with unit tests; AppTest smoke test.
5. **Publish, verification & write-up** — public link, recorded spot-checks, Russian README (run
   instructions, gap rule, assumptions, ToS-risk statement), employer write-up (time, difficulties, AI
   tools: Claude Code + GSD), source zip.

### Research Flags

- Needs phase research: phase 2 (endpoint host/version, delivery formula, price semantics, page-2 ad
  numbering), phase 3 (registry reachability from a RU VPS, two-container WAL check).
- Standard patterns (skip research): phase 4, phase 5.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Versions verified live 2026-10-16; RU registry reachability MEDIUM |
| Features | MEDIUM | Table stakes from TZ; competitor evidence thin |
| Architecture | HIGH | Standard patterns; WAL facts from sqlite.org; WB field details MEDIUM |
| Pitfalls | MEDIUM | From recorded payloads + 2026 parsers; delivery & dest undocumented; no live WB access |

**Overall confidence:** MEDIUM

### Gaps to Address

- **Reachability from the deploy host** — unknown; probe early. If blocked: honest blocked history +
  strong run-log UI; user decides host/source; no circumvention.
- **Delivery-days formula** — hypothesis; keep raw hours; calibrate against browser cards if access
  exists; otherwise document as derived/approximate.
- **Price & rating semantics** — `min(product)/100` and merged `feedbacks` are MEDIUM; spot-check.
- **Moscow `dest`** — `-1257786` common; store the param set per run.
- **Page-2 ad numbering / page size** — unverified; track an `ad_slot_mismatch` counter.
- **ToS exposure** — automated collection may conflict with WB's offer; mitigated by low rate and no
  evasion; stated honestly in the write-up.

## Sources

### Primary (HIGH)
- PyPI / GitHub releases / Docker Hub (queried 2026-10-16); Streamlit 2026 release notes and 1.66
  wheel; sqlite.org/wal.html; recorded WB payload fixtures (mishannn/wb-goods-feed,
  Unti1/T-skRAZRAB0T, ibrohim-Fazliddinov/WB-Analyze).

### Secondary (MEDIUM)
- ru-marketplace-mcp (issues #93/#101 — reachability evidence only; impersonation parts deliberately
  not used); Zaruber/Sheetswbparser, maxexplorer/ParserProject, Duff89/wb_parse_search_phrase;
  Streamlit forum / Plotly timezone threads; supercronic README.

### Tertiary (LOW)
- Habr Q&A on time1/time2 (no answers); marketing pages; Streamlit Community Cloud limits (Feb 2024).

---
*Research completed: 2026-10-16*
*Ready for roadmap: yes*
