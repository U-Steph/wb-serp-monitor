# Phase 1: Collection Core - Context

**Gathered:** 2026-10-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous). The user asked for a fully autonomous solution, so the recommended
answer for every grey area was auto-accepted by the orchestrator; alternatives are recorded for traceability.

<domain>
## Phase Boundary

Deliver the `wbmon` Python package that performs one complete, honest collection run from the command
line: load the 70 queries (Google Sheet CSV → bundled fallback), request the Wildberries public search
JSON for each query with a plain `httpx` client, classify every HTTP outcome, paginate to `top_n`
(default 200) in SERP order, normalize each product into an observation row (position, nm_id, buyer
price, rating, review count, delivery days, seller, ad flags), and append everything — including
blocked/failed attempts — to an append-only SQLite history. Includes CLI `init-db` / `collect` /
`probe`, structured logs, and a fully offline pytest suite. Out of this phase: the hourly scheduler,
Docker (Phase 2), the Streamlit UI (Phase 3), deployment and docs (Phase 4).

</domain>

<decisions>
## Implementation Decisions

### Area 1 — SERP semantics (positions, pagination, endpoint)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | What is "position"? | 1-based index in the response array order, continuous across pages (page 2 starts at len(page1)+1); promoted/ad items included because they occupy displayed slots; flagged with `is_promoted` | organic-only rank (rejected: TZ says "в том порядке, в котором они представлены в выдаче") |
| 2 | Page size / stop rule | Derive page size from `len(page1)` (expected 100); stop at `top_n`, an empty page, or wrap-around (first-10-ids fingerprint of page N equals page 1 → stop, mark complete); hard cap `ceil(top_n/len(page1))+1` pages; drop duplicate nm_ids within one run-query keeping the first occurrence (count recorded) | trust `total` (rejected: unreliable) |
| 3 | Endpoint | Default `https://search.wb.ru/exactmatch/ru/common/v18/search`, overridable by env `WBMON_SEARCH_URL`; the full request param set is pinned in config and stored on every run (`runs.params_json`) | `u-search.wb.ru` host / site-internal `/__internal/u-search/…` path (documented alternatives, same params) |
| 4 | Fixed params | `ab_testing=false, appType=1, curr=rub, dest=-1257786 (Moscow), lang=ru, locale=ru, resultset=catalog, sort=popular, spp=30, suppressSpellcheck=false, page=N, query=<original text>`; `dest` overridable by `WBMON_DEST` | other Moscow dest ids (documented only) |

### Area 2 — Field semantics (normalizer)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Buyer price | `price_rub` = min over sizes with a price of `sizes[].price.product / 100` (Decimal-safe rounding to 2 dp, stored as REAL/NUMERIC); audit columns `price_basic_rub`, `price_total_rub`, `logistics_rub` from the same size; NULL when no priced size; legacy top-level `salePriceU/priceU` used only as fallback if `sizes` has no prices | first size only |
| 2 | Rating & reviews | `rating` = `reviewRating` (float; 0 or missing → NULL); `feedbacks` = `feedbacks` (int; missing → NULL; 0 is a real value); extras `nm_rating` = `nmReviewRating`, `nm_feedbacks` = `nmFeedbacks`; legacy integer `rating` ignored | nm-level values as primary |
| 3 | Delivery days | Store raw product-level `time1`, `time2` (hours; fallback: the size with min time1+time2 if product-level missing); `delivery_hours = time1 + time2`; `delivery_days = (local_date(collected_at + delivery_hours) − local_date(collected_at)).days` in Europe/Moscow → today = 0, tomorrow = 1; NULL if either input missing; implemented in one pure function `delivery_days(collected_at_utc, hours)` with unit tests at midnight/DST-free MSK boundaries; documented as "derived from WB time1+time2 hours, approximate" | ceil(hours/24) (rejected: ignores time-of-day, breaks "today=0") |
| 4 | Seller, name, misc | `seller_name` = `supplier`, `seller_id` = `supplierId`; also `name`, `brand`, `subject_id`, `total_quantity`; ads: `is_promoted` = `log` object present & non-empty, plus `ad_promo_position` (`log.promoPosition`), `ad_organic_position` (`log.position`), `ad_tp` (`log.tp`), `ad_cpm` (`log.cpm`); count `ad_slot_mismatch` when `promoPosition` ≠ computed position | — |

### Area 3 — HTTP behaviour & failure taxonomy (no circumvention)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Client identity & headers | Plain `httpx.Client` (HTTP/1.1, default TLS), headers: `User-Agent: Mozilla/5.0 (compatible; wbmon/<version>; +<repo url>)` (honest bot-style UA, overridable `WBMON_USER_AGENT`), `Accept: application/json`, `Accept-Language: ru-RU,ru;q=0.9`. No Origin/Referer spoofing, no cookies, no browser fingerprints, no proxies | browser UA string (rejected: disguise) |
| 2 | Pacing | Sequential requests; fixed polite delay `WBMON_REQUEST_DELAY` (default 1.5 s) between requests; timeouts connect 10 s / read 30 s | random jitter (rejected: looks like evasion), concurrency (rejected: load) |
| 3 | Classification | Before parsing: `ok` (200 + JSON + ≥1 product), `blocked` (403, 498, or HTML/non-JSON body at 200), `rate_limited` (429; honour `Retry-After` once, capped 60 s, then record), `error` (5xx/timeout/connection after ≤2 bounded retries with 2 s/5 s backoff; invalid JSON; schema drift; `products: null`; 200 with zero products on page 1 = `error/empty_suspect`). Page ≥2 failing after page 1 succeeded → query status `partial` (observations from earlier pages are kept, coverage recorded) | — |
| 4 | Circuit breaker | After 3 consecutive `blocked` queries, stop sending requests for the rest of the run and record remaining queries as `blocked` with `error_class='circuit_open'` (no request made); run status = `blocked` if no query was ok, `partial` if some were, `ok` if all ok | 5 (rejected: more requests into a wall) |

### Area 4 — Storage, CLI, project layout
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Layout & tooling | src layout `src/wbmon/` (`config.py`, `clock.py`, `models.py`, `queries.py`, `wb_client.py`, `classify.py`, `normalize.py`, `delivery.py`, `storage/` (`schema.sql`/`migrations.py`, `writer.py`, `reader.py`), `orchestrator.py`, `cli.py`, `__main__.py`, `data/queries_bundled.txt`), `pyproject.toml` (hatchling or setuptools) with deps `httpx>=0.28.1,<1`, `tzdata`; optional groups `web` (streamlit, plotly, pandas) and `dev` (pytest, ruff); Python ≥3.11 | pydantic models (rejected: extra deps) |
| 2 | Config | Frozen dataclass `Settings.from_env()` with `WBMON_` env vars: `DB_PATH` (default `./data/wbmon.sqlite3`), `TOP_N` (200), `DEST`, `SEARCH_URL`, `REQUEST_DELAY`, `SHEET_CSV_URL` (`https://docs.google.com/spreadsheets/d/1K0m4229w_1LONgrNzWpx8lkkDcAlbdZH5ifHenZV3x8/export?format=csv`), `USER_AGENT`, `TIMEZONE` (Europe/Moscow), `LOG_LEVEL`; CLI flags override (`--top-n`, `--limit-queries`, `--db`) | — |
| 3 | Schema | Tables: `queries(id PK, text UNIQUE (BINARY), sheet_order, first_seen_at, last_seen_at)`, `runs(id PK, slot_utc TEXT NULL UNIQUE, trigger ('manual','scheduler','probe'), started_at, finished_at, status ('running','ok','partial','blocked','error','abandoned'), query_source ('sheet','bundled'), params_json, top_n, n_queries, n_ok, n_partial, n_blocked, n_error, n_items, error)`, `run_queries(id PK, run_id FK, query_id FK, UNIQUE(run_id,query_id), status ('ok','partial','blocked','rate_limited','error'), error_class, http_status, pages_fetched, items_collected, is_complete INT, normquery, ad_slot_mismatch, duplicates_dropped, error_message, started_at, duration_ms, CHECK(status!='ok' OR items_collected>0))`, `observations(id PK, run_id FK, query_id FK, collected_at, position, nm_id, name, brand, price_rub, price_basic_rub, price_total_rub, logistics_rub, rating, feedbacks, nm_rating, nm_feedbacks, time1_h, time2_h, delivery_hours, delivery_days, seller_name, seller_id, is_promoted, ad_promo_position, ad_organic_position, ad_tp, ad_cpm, total_quantity, UNIQUE(run_id, query_id, position))`; triggers forbid UPDATE/DELETE on `observations` (and DELETE on runs/run_queries); indexes `(query_id, nm_id, collected_at)`, `(run_id, query_id, position)`, `(query_id, collected_at)`, `run_queries(query_id, run_id)`; `PRAGMA user_version` migrations; WAL, `synchronous=NORMAL`, `busy_timeout=30000`, `foreign_keys=ON`; timestamps = UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ` | products table (deferred: name/brand denormalized on observations keeps writes simple) |
| 4 | CLI & logs | `python -m wbmon` / console script `wbmon` with argparse subcommands: `init-db`, `collect [--top-n N] [--limit-queries N] [--trigger manual|scheduler] [--slot ISO]`, `probe [--query TEXT]` (one request, prints classification, records nothing or a `probe` run — decision: records nothing, prints JSON summary); at start of `collect`: mark stale `running` runs as `abandoned`; each query = one `BEGIN IMMEDIATE` transaction writing its `run_queries` row + observations; logs = one line per event `key=value` (ts, level, event, run_id, query, status, http, items, ms) to stdout; exit code 0 when the run completed (even if blocked), non-zero only on crash/config error | click/typer (rejected: extra deps) |

### Testing strategy
- Fully offline: `httpx.MockTransport` serving hand-written JSON fixtures in `tests/fixtures/` that mirror the real WB response shape (field names from public recorded payloads; values made up and clearly test-only). Cases: `data.products` shape, top-level `products` shape, multi-size prices, missing fields, ads with `log`, page 2, page-2-equals-page-1 wrap, empty page, `products: null`, 403/498/429/HTML-at-200/5xx/timeout, Cyrillic/ё/`%`/`*`/`,` query encoding.
- Every DB test uses `tmp_path` databases. No code path writes fixtures into the configured real DB.
- Pure-function unit tests: delivery days, price selection, classification, wrap detection, position numbering.

### Claude's Discretion
- Exact module boundaries inside `src/wbmon/` may be merged/split for clarity as long as the pure core (classify/normalize/delivery) stays I/O-free.
- Build backend choice (hatchling vs setuptools), ruff rule set, exact log field names.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- None — greenfield repository (only `.planning/`, `CLAUDE.md`). A local `.venv` (Python 3.14) already has httpx 0.28.1, streamlit 1.66.0, plotly 7.1.0, pandas 3.0.6, pytest 9.1.1, apscheduler 3.11.3, tzdata, pypdf installed; it must be gitignored.

### Established Patterns
- None yet; this phase establishes them (src layout, frozen-dataclass settings, functional core / imperative shell, key=value logs, offline fixtures).

### Integration Points
- Phase 2 calls `python -m wbmon collect --trigger scheduler --slot <ISO>` as a subprocess and reads run status from the DB.
- Phase 3 reads the schema defined here through a read-only reader (`storage/reader.py` may be extended there).

</code_context>

<specifics>
## Specific Ideas

- The 70 queries (verified identical in the PDF appendix and the Google Sheet on 2026-10-16) go into `src/wbmon/data/queries_bundled.txt`, one per line, UTF-8, exact bytes. Note: one query uses Latin «x» (`…160x200`), three use Cyrillic «х» (`160х200`, `70х70`, `160х200`), one contains «ё» twice across queries (`постельное бельё`, `постельное бельё евро`), one has the typo «пастельный».
- Sheet plausibility check: ≥ 50 non-empty rows and ≤ 500; otherwise fall back to bundled and log `query_source=bundled reason=…`.
- `wbmon probe` from the dev machine is expected to print `blocked` (403 from wbaas) — that is an honest, accepted outcome; never try to get around it.

</specifics>

<deferred>
## Deferred Ideas

- Organic-rank (ads excluded) series — v2.
- Raw response archival (compressed per run) — v2, storage cost.
- Postgres backend — v2.

</deferred>

<addendum date="2026-10-16" source="orchestrator, after 01-RESEARCH.md">
## Addendum — decisions on open questions raised by phase research (auto-accepted)

- **D-A1 Schema freeze:** migration 1 adds `subject_id` and `root_id` (WB `root`) to `observations`; drop the redundant explicit index on `(run_id, query_id, position)` (the UNIQUE constraint already provides it).
- **D-A2 Append-only hardening:** `connect_rw` sets `PRAGMA recursive_triggers=ON` so `INSERT OR REPLACE` cannot silently replace rows; tests assert REPLACE raises `IntegrityError` and a source-grep test forbids `OR REPLACE` / `REPLACE INTO` in `src/`.
- **D-A3 Transactions:** Python 3.12+ `autocommit=True`, 3.11 `isolation_level=None`; always explicit SQL `BEGIN IMMEDIATE` / `COMMIT` / `ROLLBACK`.
- **D-A4 httpx hygiene:** `trust_env=False` (no env proxies) and a cookie policy that rejects all cookies (`client.cookies.jar.set_policy(...)`), `follow_redirects=False` for WB; any 3xx from WB → `blocked` (`error_class='redirect'`). Spaces in `query` are form-encoded as `+` by httpx `params=` (standard; accepted).
- **D-A5 Breaker:** counts consecutive `blocked` **or** `rate_limited` queries (3 → open).
- **D-A6 Run deadline:** `WBMON_RUN_DEADLINE` (default 2400 s); when exceeded, remaining queries are recorded as `error` with `error_class='deadline'` without requests; run status `partial`.
- **D-A7 Lock in Phase 1:** `collect` takes a non-blocking `fcntl.flock` on `<db_path>.lock`; if busy → log `lock_busy`, exit 0, create no run. With `--slot` given and a run for that slot already existing → log `slot_exists`, exit 0, create no run. "Abandon stale runs" only runs while holding the lock (so it never kills a live run).
- **D-A8 Packages:** httpx, tzdata, pytest, ruff, hatchling (and web extras streamlit/plotly/pandas) are standard, long-established PyPI packages — no human-verify checkpoint is needed before installing them.
- **D-A9 User-Agent URL placeholder:** `+https://github.com/U-Steph/wb-serp-monitor` (final repo URL confirmed in Phase 4).
- **D-A10 MVP / walking skeleton adaptation:** this is a CLI data pipeline with no UI in Phase 1. The walking skeleton = scaffold + full frozen schema (migration 1) + `wbmon init-db` + `wbmon collect --limit-queries 1` running end-to-end against an `httpx.MockTransport` fixture (page 1 only) and writing a real run/run_query/observation into a tmp DB + a read-back (`wbmon` prints the run summary). Later plans widen the slice (full fields, pagination, classification/breaker, query source, probe, lock/deadline, perf). Plans may run in parallel where files do not overlap.
</addendum>
