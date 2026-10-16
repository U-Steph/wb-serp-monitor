# WB SERP Monitor

## What This Is

A Python service that collects Wildberries search results (SERP) every hour for 70 fixed
bedding-related search queries (top-200 products per query, fixed Moscow delivery region),
stores the full history of observations in a database, and shows how product positions change
over time in a Streamlit web UI. It is a test assignment for an AI-developer vacancy at a team that
builds AI/data products for Ozon/Wildberries sellers; the result is sent to the employer as a repo
link, a link to the running web UI with real data, and a short write-up.

## Core Value

Real Wildberries search observations are collected on schedule, appended to history without ever
being lost or fabricated, and the UI shows each product's position history honestly (gaps where a
product was not found — never invented positions).

## Requirements

### Validated

- ✓ Collection core: sheet/bundled query source with byte-exact wording, plain-httpx WB client with honest outcome classification, multi-page top-N in SERP order, normalizer (price/rating/reviews/delivery/seller/ads, NULL for unknown), append-only SQLite with run log, lock/slot/abandon, breaker and deadline, CLI `init-db`/`collect`/`probe`, 598 offline tests on Python 3.11–3.14 — Phase 1 (QRY-01..04, COL-01..06, DATA-01..07, STOR-01..04, SCHED-01, SCHED-04, OPS-04)

### Active

**Data collection (TZ §2)**
- [ ] Python parser of Wildberries search results for the 70 queries from the Google Sheet
      «Запросы WB» (CSV export of sheet `1K0m4229w_1LONgrNzWpx8lkkDcAlbdZH5ifHenZV3x8`), with a bundled
      copy of the list as offline fallback. Original query wording is preserved byte-for-byte
      (e.g. «постельное бельё» with ё, «пастельный комплект 2-спальный» typo, Cyrillic «х» vs Latin «x»
      in «160х200» / «160x200»). If the sheet and the bundled list differ, the sheet wins.
- [ ] For each query collect the first `top_n` products (default 200, configurable) in exactly the
      order the SERP presents them (promoted/ad slots included, flagged).
- [ ] Collection runs automatically every hour and stores results with date/time of collection;
      history is never overwritten or deleted.
- [ ] One fixed delivery city (Moscow) for comparability (`dest` region parameter).
- [ ] Per found product store: search query, collection date/time, SERP position, product ID
      (`nm_id`), buyer price, rating, review count, delivery time in calendar days (today = 0,
      tomorrow = 1), seller name. Unavailable fields are stored as NULL — never invented.

**Storage (TZ §3)**
- [ ] Database (SQLite by default; Postgres acceptable) keeps the observation history: positions of one
      product for one query at different moments. Re-running collection only appends.

**Web UI (TZ §4)**
- [ ] Simple web service reachable by link (Streamlit) where the user can:
      pick a search query from the list; pick one or more product IDs ever found for that query;
      see an interactive chart of position dynamics over time (position 1 at the TOP; hover shows
      date/time, ID, position); see a table of the collected product characteristics.
- [ ] If a product was not found in a given run, the chart shows a gap — no fabricated position.
- [ ] The interface is neat and understandable (Russian UI language).

**Deliverables (TZ §5)**
- [ ] Repository link (or source archive) with the project code.
- [ ] Link to the running web UI with real data (a few real hourly runs with saved history are
      enough; artificially creating observation history is forbidden).
- [ ] Short run instructions + a brief comment: time spent, difficulties and how they were solved,
      which AI tools were used (Claude Code + GSD workflow).

**Evaluation criteria (TZ §6)**: works (real data collected → stored → shown), stable (repeated runs
work, history is not lost, no constant manual intervention), visual quality (convenient UI, readable
charts).

### Out of Scope

- Anti-bot circumvention of any kind — see Constraints; this is a hard boundary, not a deferral.
- Multiple cities / regions — TZ requires one fixed city for comparability.
- User accounts / auth in the UI — the employer just needs a link.
- Personalized prices (logged-in buyer discounts, WB Wallet) — we record the price shown to an
  anonymous buyer in the Moscow region.
- Collecting product cards beyond what the search response contains (no per-product detail
  requests) — 70 queries × 200 items hourly must stay light.
- Synthetic / demo observation data in the production database — forbidden by TZ §5.

## Context

**Source assignment:** `TZ_Wildberries_short_2026.pdf` (outside the repo, in the parent folder) —
«Python-парсер поисковой выдачи Wildberries + веб-интерфейс: 70 поисковых запросов • топ-200 • сбор
каждый час». The 70 queries are all bedding («постельное белье …») variations. The Google Sheet was
downloaded on 2026-10-16: 70 rows, no header row, identical to the PDF appendix.

**Wildberries search API (public JSON used by the website), as known on 2026-10-16:**
- Endpoint family: `https://search.wb.ru/exactmatch/ru/common/v{13..18}/search`; the website itself
  (2026) calls `https://www.wildberries.ru/__internal/u-search/exactmatch/ru/common/v18/search` and
  `https://u-search.wb.ru/exactmatch/ru/common/v18/search`.
- Typical params: `ab_testing=false&appType=1&curr=rub&dest=-1257786&lang=ru&page={1..}&query={q}&
  resultset=catalog&sort=popular&spp=30&suppressSpellcheck=false` (`dest=-1257786` = Moscow).
  ~100 products per page → top-200 = pages 1–2.
- Response: products under `data.products` (older) or top-level `products` (some v18 clients) —
  parser must accept both. Per product: `id` (nm_id), `root`, `name`, `brand`, `supplier` (seller
  name), `supplierId`, `supplierRating`, `reviewRating` (float rating), `nmReviewRating`, `feedbacks`
  (review count), `nmFeedbacks`, `sizes[]` with `price.{basic,product,total,logistics,return}` in
  kopecks (buyer price = `product`/100), `time1`/`time2` (hours, at product and size level, plus
  `sizes[].stocks[]`), `dtype`, `wh`, `totalQuantity`, and `log` (present on promoted/ad slots:
  `position`, `promoPosition`, `cpm`, `tp`).
- Delivery days must be derived: hypothesis = calendar-day difference (Europe/Moscow) between
  collection time and collection time + (time1 + time2) hours; to be confirmed against how the WB
  front-end renders the delivery date.

**Access situation (critical):** since ~late September 2026 Wildberries puts its search/card APIs
behind an anti-bot layer ("wbaas"). From the dev machine (egress via a German VPN IP) every search
API request returns `403` (`server: wbaas`, header `status-no-id`) and the main page returns HTTP 498
with a JS challenge. Public GitHub reports (ru-marketplace-mcp #93/#101, Sep 2026) show the same 403
from a Moscow residential IP for non-browser clients. Whether plain requests succeed from a Russian
server IP is unknown and must be tested on the deployment host. The system must therefore detect and
report blocking honestly and keep working (retry next hour) without losing history.

**Environment:** macOS dev machine, Python 3.14 locally (deploy target: Python 3.12 in Docker),
Node 22, `gh` authenticated as `U-Steph` (repo + workflow scopes). Hosting is not decided yet —
likely the user's Russian VPS via Docker Compose; the UI must be reachable by a public link.

## Constraints

- **Ethics / platform rules (HARD)**: no anti-bot circumvention — no TLS-fingerprint impersonation
  (curl_cffi, tls-client, etc.), no headless/stealth browsers used to pass WB checks
  (Playwright/Selenium/undetected-chromedriver), no harvesting or replaying `x_wbaas_token` or any
  other anti-bot token/cookie, no CAPTCHA or proof-of-work solving, no proxy rotation to evade blocks.
  The collector uses a plain HTTP client (httpx) with ordinary headers at a modest, polite rate.
  Anti-bot responses (403 / 498 / 429) are recorded as `blocked` for that query/run, with backoff;
  nothing is fabricated.
- **Data integrity (HARD)**: never write synthetic observations to the real database. Test fixtures
  live only in `tests/` and in temporary databases.
- **Tech stack**: Python ≥ 3.11 (target 3.12), httpx, SQLite (WAL) via stdlib `sqlite3` or
  SQLAlchemy Core, Streamlit + Plotly for UI, pytest for tests, Docker Compose for deployment.
- **Stability**: unattended hourly operation (scheduler + restart policy), idempotent per run,
  per-query error isolation (one failing query never aborts the run), run log persisted.
- **Scope/time**: this is a test assignment — keep scope tight, code clean and well-documented;
  README and write-up in Russian (the employer is Russian-speaking), code identifiers in English.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Use WB public search JSON endpoint with plain httpx (no browser automation) | Same data the site renders; respects the anti-bot boundary | ⚠️ Revisit — implemented; `wbmon probe` from the dev machine (DE VPN egress) returns 403 `server: wbaas`; reachability from a Russian host still to be tested in Phase 4 |
| SQLite in WAL mode as default DB | Single node, ~336k rows/day is fine, zero ops; Postgres optional later | ✓ Good — every UI read path < 30 ms on a 2.35M-row DB (Phase 1 perf test) |
| Streamlit + Plotly UI, reversed Y axis, gaps for missing runs | TZ suggests Streamlit; Plotly gives hover + reversed axis + gaps | — Pending |
| Position = 1-based index across pages in displayed order, promoted slots included and flagged | TZ: "в том порядке, в котором они представлены в выдаче" | — Pending |
| Delivery days = calendar-day diff (Europe/Moscow) of collected_at vs collected_at + time1 + time2 h | TZ: today = 0, tomorrow = 1; WB exposes hours | — Pending (verify on live data) |
| Queries loaded from Google Sheet CSV export each run, bundled fallback | TZ: Sheet wording wins | — Pending |
| Every run and every query attempt is logged (status ok / blocked / error) | Honest stability reporting in UI | ✓ Good — run_queries log + breaker/deadline/abandoned states implemented (Phase 1) |
| GSD config chosen autonomously: YOLO, coarse granularity, parallel, research + plan-check + verifier on, balanced models | User asked for a fully autonomous solution | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-10-16 after Phase 1 (Collection Core)*
