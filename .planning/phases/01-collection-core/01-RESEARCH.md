# Phase 1: Collection Core - Research

**Researched:** 2026-10-16
**Domain:** Offline-verifiable Python collector (plain httpx) + append-only SQLite history for Wildberries search results
**Confidence:** MEDIUM-HIGH (SQLite / httpx / delivery math / pagination: HIGH, verified by running code on Python 3.11, 3.12 and 3.14; WB payload field semantics: MEDIUM, from public recorded payloads; nothing could be checked against live WB and none was attempted)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Mode:** Smart discuss (autonomous). The user asked for a fully autonomous solution, so the recommended answer for every grey area was auto-accepted by the orchestrator; alternatives are recorded for traceability.

#### Area 1 - SERP semantics (positions, pagination, endpoint)
| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | What is "position"? | 1-based index in the response array order, continuous across pages (page 2 starts at len(page1)+1); promoted/ad items included because they occupy displayed slots; flagged with `is_promoted` | organic-only rank (rejected: TZ says "в том порядке, в котором они представлены в выдаче") |
| 2 | Page size / stop rule | Derive page size from `len(page1)` (expected 100); stop at `top_n`, an empty page, or wrap-around (first-10-ids fingerprint of page N equals page 1 -> stop, mark complete); hard cap `ceil(top_n/len(page1))+1` pages; drop duplicate nm_ids within one run-query keeping the first occurrence (count recorded) | trust `total` (rejected: unreliable) |
| 3 | Endpoint | Default `https://search.wb.ru/exactmatch/ru/common/v18/search`, overridable by env `WBMON_SEARCH_URL`; the full request param set is pinned in config and stored on every run (`runs.params_json`) | `u-search.wb.ru` host / site-internal `/__internal/u-search/...` path (documented alternatives, same params) |
| 4 | Fixed params | `ab_testing=false, appType=1, curr=rub, dest=-1257786 (Moscow), lang=ru, locale=ru, resultset=catalog, sort=popular, spp=30, suppressSpellcheck=false, page=N, query=<original text>`; `dest` overridable by `WBMON_DEST` | other Moscow dest ids (documented only) |

#### Area 2 - Field semantics (normalizer)
| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | Buyer price | `price_rub` = min over sizes with a price of `sizes[].price.product / 100` (Decimal-safe rounding to 2 dp, stored as REAL/NUMERIC); audit columns `price_basic_rub`, `price_total_rub`, `logistics_rub` from the same size; NULL when no priced size; legacy top-level `salePriceU/priceU` used only as fallback if `sizes` has no prices | first size only |
| 2 | Rating & reviews | `rating` = `reviewRating` (float; 0 or missing -> NULL); `feedbacks` = `feedbacks` (int; missing -> NULL; 0 is a real value); extras `nm_rating` = `nmReviewRating`, `nm_feedbacks` = `nmFeedbacks`; legacy integer `rating` ignored | nm-level values as primary |
| 3 | Delivery days | Store raw product-level `time1`, `time2` (hours; fallback: the size with min time1+time2 if product-level missing); `delivery_hours = time1 + time2`; `delivery_days = (local_date(collected_at + delivery_hours) - local_date(collected_at)).days` in Europe/Moscow -> today = 0, tomorrow = 1; NULL if either input missing; implemented in one pure function `delivery_days(collected_at_utc, hours)` with unit tests at midnight/DST-free MSK boundaries; documented as "derived from WB time1+time2 hours, approximate" | ceil(hours/24) (rejected: ignores time-of-day, breaks "today=0") |
| 4 | Seller, name, misc | `seller_name` = `supplier`, `seller_id` = `supplierId`; also `name`, `brand`, `subject_id`, `total_quantity`; ads: `is_promoted` = `log` object present & non-empty, plus `ad_promo_position` (`log.promoPosition`), `ad_organic_position` (`log.position`), `ad_tp` (`log.tp`), `ad_cpm` (`log.cpm`); count `ad_slot_mismatch` when `promoPosition` != computed position | - |

#### Area 3 - HTTP behaviour & failure taxonomy (no circumvention)
| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | Client identity & headers | Plain `httpx.Client` (HTTP/1.1, default TLS), headers: `User-Agent: Mozilla/5.0 (compatible; wbmon/<version>; +<repo url>)` (honest bot-style UA, overridable `WBMON_USER_AGENT`), `Accept: application/json`, `Accept-Language: ru-RU,ru;q=0.9`. No Origin/Referer spoofing, no cookies, no browser fingerprints, no proxies | browser UA string (rejected: disguise) |
| 2 | Pacing | Sequential requests; fixed polite delay `WBMON_REQUEST_DELAY` (default 1.5 s) between requests; timeouts connect 10 s / read 30 s | random jitter (rejected: looks like evasion), concurrency (rejected: load) |
| 3 | Classification | Before parsing: `ok` (200 + JSON + >=1 product), `blocked` (403, 498, or HTML/non-JSON body at 200), `rate_limited` (429; honour `Retry-After` once, capped 60 s, then record), `error` (5xx/timeout/connection after <=2 bounded retries with 2 s/5 s backoff; invalid JSON; schema drift; `products: null`; 200 with zero products on page 1 = `error/empty_suspect`). Page >=2 failing after page 1 succeeded -> query status `partial` (observations from earlier pages are kept, coverage recorded) | - |
| 4 | Circuit breaker | After 3 consecutive `blocked` queries, stop sending requests for the rest of the run and record remaining queries as `blocked` with `error_class='circuit_open'` (no request made); run status = `blocked` if no query was ok, `partial` if some were, `ok` if all ok | 5 (rejected: more requests into a wall) |

#### Area 4 - Storage, CLI, project layout
| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | Layout & tooling | src layout `src/wbmon/` (`config.py`, `clock.py`, `models.py`, `queries.py`, `wb_client.py`, `classify.py`, `normalize.py`, `delivery.py`, `storage/` (`schema.sql`/`migrations.py`, `writer.py`, `reader.py`), `orchestrator.py`, `cli.py`, `__main__.py`, `data/queries_bundled.txt`), `pyproject.toml` (hatchling or setuptools) with deps `httpx>=0.28.1,<1`, `tzdata`; optional groups `web` (streamlit, plotly, pandas) and `dev` (pytest, ruff); Python >=3.11 | pydantic models (rejected: extra deps) |
| 2 | Config | Frozen dataclass `Settings.from_env()` with `WBMON_` env vars: `DB_PATH` (default `./data/wbmon.sqlite3`), `TOP_N` (200), `DEST`, `SEARCH_URL`, `REQUEST_DELAY`, `SHEET_CSV_URL` (`https://docs.google.com/spreadsheets/d/1K0m4229w_1LONgrNzWpx8lkkDcAlbdZH5ifHenZV3x8/export?format=csv`), `USER_AGENT`, `TIMEZONE` (Europe/Moscow), `LOG_LEVEL`; CLI flags override (`--top-n`, `--limit-queries`, `--db`) | - |
| 3 | Schema | Tables: `queries(id PK, text UNIQUE (BINARY), sheet_order, first_seen_at, last_seen_at)`, `runs(id PK, slot_utc TEXT NULL UNIQUE, trigger ('manual','scheduler','probe'), started_at, finished_at, status ('running','ok','partial','blocked','error','abandoned'), query_source ('sheet','bundled'), params_json, top_n, n_queries, n_ok, n_partial, n_blocked, n_error, n_items, error)`, `run_queries(id PK, run_id FK, query_id FK, UNIQUE(run_id,query_id), status ('ok','partial','blocked','rate_limited','error'), error_class, http_status, pages_fetched, items_collected, is_complete INT, normquery, ad_slot_mismatch, duplicates_dropped, error_message, started_at, duration_ms, CHECK(status!='ok' OR items_collected>0))`, `observations(id PK, run_id FK, query_id FK, collected_at, position, nm_id, name, brand, price_rub, price_basic_rub, price_total_rub, logistics_rub, rating, feedbacks, nm_rating, nm_feedbacks, time1_h, time2_h, delivery_hours, delivery_days, seller_name, seller_id, is_promoted, ad_promo_position, ad_organic_position, ad_tp, ad_cpm, total_quantity, UNIQUE(run_id, query_id, position))`; triggers forbid UPDATE/DELETE on `observations` (and DELETE on runs/run_queries); indexes `(query_id, nm_id, collected_at)`, `(run_id, query_id, position)`, `(query_id, collected_at)`, `run_queries(query_id, run_id)`; `PRAGMA user_version` migrations; WAL, `synchronous=NORMAL`, `busy_timeout=30000`, `foreign_keys=ON`; timestamps = UTC ISO-8601 `YYYY-MM-DDTHH:MM:SSZ` | products table (deferred: name/brand denormalized on observations keeps writes simple) |
| 4 | CLI & logs | `python -m wbmon` / console script `wbmon` with argparse subcommands: `init-db`, `collect [--top-n N] [--limit-queries N] [--trigger manual\|scheduler] [--slot ISO]`, `probe [--query TEXT]` (one request, prints classification, records nothing or a `probe` run - decision: records nothing, prints JSON summary); at start of `collect`: mark stale `running` runs as `abandoned`; each query = one `BEGIN IMMEDIATE` transaction writing its `run_queries` row + observations; logs = one line per event `key=value` (ts, level, event, run_id, query, status, http, items, ms) to stdout; exit code 0 when the run completed (even if blocked), non-zero only on crash/config error | click/typer (rejected: extra deps) |

#### Testing strategy
- Fully offline: `httpx.MockTransport` serving hand-written JSON fixtures in `tests/fixtures/` that mirror the real WB response shape (field names from public recorded payloads; values made up and clearly test-only). Cases: `data.products` shape, top-level `products` shape, multi-size prices, missing fields, ads with `log`, page 2, page-2-equals-page-1 wrap, empty page, `products: null`, 403/498/429/HTML-at-200/5xx/timeout, Cyrillic/ё/`%`/`*`/`,` query encoding.
- Every DB test uses `tmp_path` databases. No code path writes fixtures into the configured real DB.
- Pure-function unit tests: delivery days, price selection, classification, wrap detection, position numbering.

### Claude's Discretion
- Exact module boundaries inside `src/wbmon/` may be merged/split for clarity as long as the pure core (classify/normalize/delivery) stays I/O-free.
- Build backend choice (hatchling vs setuptools), ruff rule set, exact log field names.

### Deferred Ideas (OUT OF SCOPE)
- Organic-rank (ads excluded) series - v2.
- Raw response archival (compressed per run) - v2, storage cost.
- Postgres backend - v2.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| QRY-01 | Load query list from the Google Sheet CSV (redirects, `utf-8-sig`, `csv.reader`, no header) every run | Sec. "httpx specifics": redirect chain works under MockTransport with `follow_redirects=True`; `.text` keeps the BOM, so decode `resp.content.decode("utf-8-sig")`; blank rows must be skipped |
| QRY-02 | Byte-for-byte query wording; `params=` only, no pre-quoting | Wire-encoding table (verified): `ё`->`%D1%91`, `%`->`%25`, `*`->`%2A`, `,`->`%2C`, space->`+`; `request.url.params["query"]` round-trips to the exact original for all 12 probe strings incl. NFD `ё` and the 70 real queries' special characters |
| QRY-03 | Bundled copy of the 70 queries; source recorded on run | PDF appendix extracted: 70 distinct queries, no leading/trailing/double spaces, no NFD; special-character query numbers listed in "Query test vectors"; wheel build verified to include `data/queries_bundled.txt` |
| QRY-04 | Queries registered with stable id + sheet order; vanished query keeps history | `queries` UPSERT (`ON CONFLICT(text) DO UPDATE`) is legal because `queries` is not append-only; add `BEFORE DELETE` trigger on `queries`; verified UPSERT on `observations` is rejected by the UPDATE trigger |
| COL-01 | Plain httpx client, ordinary headers, polite sequential rate | Client construction recipe (explicit Timeout, `trust_env=False`, no-op cookie policy, injectable `sleep`) |
| COL-02 | Paginate to `top_n`/empty/wrap; 1-based positions in array order | Pager prototype validated on 10 scenarios (table in "Pagination semantics"); slot-index positions; cap formula; dedupe leaves position gaps |
| COL-03 | Classify every outcome before parsing | Full classification matrix incl. 3xx, 4xx, HTML sniffing, Retry-After parser (verified on 12 inputs) |
| COL-04 | Per-query isolation + breaker after 3 blocked | Orchestrator rules, breaker counting, run-status derivation table, deadline recommendation |
| COL-05 | No anti-bot circumvention, enforced by dependency list + review | Allowlist test (deps == {httpx, tzdata}; imports in `src/` subset of stdlib + httpx), `trust_env=False`, verified no-op cookie policy so Set-Cookie is never replayed |
| COL-06 | Per-(run,query) attempt logged | `run_queries` columns + `key=value` log event list; `normquery` extraction paths |
| DATA-01 | query id, run id, UTC timestamp, position, nm_id per observation | Per-page `collected_at` (seconds precision `Z`) |
| DATA-02 | Price = min over sizes of `product/100`; audit columns; NULL not 0 | Reference selector validated on 7 fixtures (expected-values table); `product: 0` treated as unpriced |
| DATA-03 | Rating/review rules | Expected-values table: `reviewRating 0 -> NULL`, `feedbacks 0 -> 0`, nm_* raw |
| DATA-04 | Pure unit-tested delivery function | 21 test vectors executed on 3.11/3.12/3.14, plus guard rails (naive datetime, negative, overflow) |
| DATA-05 | Seller + name + brand | Field names confirmed in 4 public payloads |
| DATA-06 | Ad flags and fields | `log` variants (`{}` / `null` / `cpm: 0`) -> expected flags; `promoPosition == array index` in 100% of 93 recorded ads |
| DATA-07 | Never invent; pure normalizer with fixture tests | Fixture skeletons for 7 payload variants + factory for pagination pages |
| STOR-01 | SQLite WAL, busy_timeout, FK; idempotent `init-db` | Verified DDL (3 Python versions), migration runner with re-check under write lock, concurrent-init test |
| STOR-02 | Append-only; triggers; no `INSERT OR REPLACE` | **Verified: `INSERT OR REPLACE` bypasses the DELETE trigger unless `PRAGMA recursive_triggers=ON`** - set it per connection + source-grep test |
| STOR-03 | One tx per query with its run_queries row; crashed run visible | `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` via SQL, py3.11-3.14 connect recipe; advisory `flock` so "abandon stale running" is safe |
| STOR-04 | Indexes for five access paths < 1 s on ~2.4M rows | Measured on a generated 2 352 000-row DB: slowest access path 24 ms; locked index set is sufficient; one index in the locked list is redundant |
| SCHED-01 | `wbmon collect` = one full run, exits | CLI contract incl. `--slot` idempotency and exit codes |
| SCHED-04 | Structured logs to stdout | `key=value` formatter with JSON-escaped values, line-buffered stdout |
| OPS-04 | Offline pytest, tmp DBs only | Autouse guards (cwd + env isolation, DNS/socket block, fixture `_test_only` marker), `tmp_path_retention_policy`, `slow` marker |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **No anti-bot circumvention of any kind** (no TLS-fingerprint impersonation, no headless/stealth browsers, no token/cookie harvesting or replay, no CAPTCHA/PoW solving, no proxy rotation). Plain `httpx` with ordinary headers at a modest sequential rate; 403/498/429 are recorded as `blocked`/`rate_limited`.
- **Data integrity (HARD):** never write synthetic observations to a real database; fixtures live in `tests/` and temp DBs only.
- **Stack:** Python >=3.11 (target 3.12), stdlib `sqlite3` (WAL), `httpx`, pytest; no pydantic / pydantic-settings; no APScheduler / supercronic / systemd in code (documented alternatives only).
- **Orchestrator overrides (win over STACK.md text):** honest bot-style UA, `Accept: application/json`, `Accept-Language: ru-RU,ru;q=0.9`; no Origin/Referer spoofing, no cookies, no proxies; fixed polite delay (default 1.5 s), **no random jitter**; circuit breaker after **3** consecutive `blocked`; config = frozen dataclass from `WBMON_*` env vars.
- **Phase CONTEXT decisions win over CLAUDE.md** on conflict.
- Code identifiers in English; README/write-up in Russian (Phase 4).
- GSD workflow enforcement: file edits go through GSD commands (planner/executor), not ad-hoc.
- STACK.md mentions `respx`, `pydantic`, jittered pacing and a 5-failure breaker: all **superseded** by CONTEXT.md (use `httpx.MockTransport`, no pydantic, fixed delay, breaker = 3).

## Summary

Phase 1 is a greenfield build of the whole collection core, and the project-level research already settled almost all product decisions. What was left open was concrete engineering detail, and every item below was verified by running code (scratch experiments, never in the project tree and never against Wildberries) on Python 3.11.15, 3.12.13 and 3.14.6.

Four findings change how the plans should be written. (1) **`INSERT OR REPLACE` silently bypasses the `BEFORE DELETE` trigger** unless `PRAGMA recursive_triggers=ON` is set on the connection, so the append-only guarantee needs that pragma on every writer connection plus a source-grep test; `ON CONFLICT DO UPDATE` is already blocked by the UPDATE trigger. (2) The recommended storage layout is fast: on a generated 2 352 000-row database (70 queries x 168 runs x 200 positions, 860 MB) every STOR-04 access path takes 0.1-24 ms, so the locked indexes are sufficient, but the locked list contains one redundant index (`(run_id, query_id, position)` duplicates the implicit index of `UNIQUE(run_id, query_id, position)`). (3) httpx by default **stores and replays `Set-Cookie`** inside one `Client` and honours `HTTP(S)_PROXY` from the environment; CONTEXT says "no cookies, no proxies", so the client must install a no-op cookie policy and use `trust_env=False` (both verified). (4) Python 3.12+ has an `autocommit` connection attribute whose announced future default (`False`) would break manual `BEGIN IMMEDIATE`; pass `autocommit=True` explicitly on 3.12+ and `isolation_level=None` on 3.11, and always issue `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` as SQL.

The payload fixtures can be modelled exactly on real recorded payloads found in public GitHub repositories (field names and nesting confirmed in five of them: `data.products` shape, legacy `priceU/salePriceU` shape, ads with `log`, multi-size products). The top-level-`products` (v18 on `u-search.wb.ru`) shape is confirmed only by parser code, not by a recorded payload, so its fixture is a documented reconstruction. The 70 real queries (extracted from the PDF appendix) contain exactly the characters that break naive encoding (`ё`, `%`, `*`, `,`, `.`, Cyrillic `х` vs Latin `x`, a typo), so the wire-encoding test should be parametrised over the real list.

**Primary recommendation:** Build in 4 plans over 3 waves: (01) foundation + frozen contracts (`models.py`, config, clock, log, delivery, bundled queries, test harness) -> (02 storage || 03 WB protocol core) in parallel -> (04) orchestrator + CLI + end-to-end. Freeze the dataclass contracts in plan 01 so plans 02 and 03 touch disjoint files.

## Architectural Responsibility Map

The "tiers" for this phase are code layers inside one Python process (there is no browser/CDN tier).

| Capability | Primary Layer | Secondary Layer | Rationale |
|------------|--------------|-----------------|-----------|
| Query list loading (sheet CSV, bundled fallback, plausibility check) | Imperative shell (`queries.py`) | Pure helper `parse_csv(text)` | Network + file I/O at the edge; parsing is a pure, testable function |
| HTTP request, pacing, bounded retry, Retry-After sleep | Imperative shell (`wb_client.py`) | - | Only module that imports `httpx`, owns `sleep`/clock injection |
| Response classification (status/body sniffing -> enum) | Pure core (`classify.py`) | - | Takes `(status, content_type, body_prefix, headers)`; no I/O so it is table-testable |
| Pagination state machine (stop rules, wrap, dedupe, cap, slot numbering) | Pure core (`pager.py`) | `wb_client` supplies pages | The error-prone logic must be testable without HTTP |
| Product normalization (price, rating, delivery, ads) | Pure core (`normalize.py`, `delivery.py`) | - | Only module that knows WB field names; DATA-07 |
| Atomic persistence, schema, migrations, abandon-stale | Storage (`storage/`) | `lock.py` | Single writer; one transaction per query |
| Read access paths for UI + perf test | Storage (`storage/reader.py`) | - | Minimal in Phase 1; Phase 3 extends |
| Run orchestration (isolation, breaker, status derivation, deadline) | Orchestrator (`orchestrator.py`) | - | Wires pure core + client + writer; never raises out of the query loop |
| CLI, exit codes, log formatting | Shell (`cli.py`, `log.py`) | - | argparse only |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | >=3.11 (dev 3.14.6 venv; 3.11.15 and 3.12.13 available at `~/.local/bin`) | Runtime | Locked by CONTEXT; every recipe below verified on 3.11, 3.12 and 3.14 [VERIFIED: local experiments] |
| httpx | 0.28.1 (installed in `.venv`; PyPI latest 0.28.1) | HTTP client for WB + Google Sheet | Locked (`httpx>=0.28.1,<1`) [VERIFIED: PyPI JSON] |
| stdlib `sqlite3` | SQLite 3.50.4 in the 3.14 venv, 3.53.1 in the 3.11/3.12 builds, 3.46 in the Docker image | Storage | Locked; WAL, triggers, partial/UPSERT features all present |
| tzdata | 2026.5 (installed) | `ZoneInfo("Europe/Moscow")` on systems without a tz database | Locked; macOS also has `/usr/share/zoneinfo/Europe/Moscow` [VERIFIED: PyPI JSON, local ls] |
| stdlib: `zoneinfo`, `csv`, `argparse`, `logging`, `fcntl`, `decimal`, `email.utils`, `dataclasses`, `importlib.resources` | - | Everything else | No extra dependency needed |

### Supporting (dev)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest | 9.1.1 (installed) | Test runner | All tests; `tmp_path_retention_policy = "failed"` accepted by this version [VERIFIED: local run] |
| ruff | 0.17.1 (not installed; latest on PyPI) | Lint/format | `target-version = "py311"` |
| hatchling | 1.32.4 (build backend, fetched on demand) | Build backend | Editable install + wheel verified to work from a Cyrillic/space/trailing-dot path on Python 3.11 and to include `data/*.txt` [VERIFIED: local build] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| hatchling | setuptools | setuptools is not in the venv either; hatchling needs no `MANIFEST.in` for package data (verified). Use hatchling. |
| `respx` (STACK.md) | `httpx.MockTransport` | CONTEXT locks MockTransport: zero extra dependency |
| `freezegun` | injected `now()` / `sleep()` callables | Simpler, no monkeypatching |

**Installation:**
```bash
# from repo root, inside the existing .venv (Python 3.14)
.venv/bin/pip install -e ".[dev]"      # httpx, tzdata, pytest, ruff; hatchling is fetched for the build
# optional cross-version check
~/.local/bin/python3.11 -m venv /tmp/wbmon-311 && /tmp/wbmon-311/bin/pip install -e ".[dev]"
```

**Version verification:** `pip index versions` run 2026-10-16: httpx 0.28.1, tzdata 2026.5, pytest 9.1.1, ruff 0.17.1, hatchling 1.32.4. First-release dates from the PyPI JSON API: httpx 2019-07-19, tzdata 2016-08-20, pytest 2010-11-25, hatchling 2022-01-09, ruff 2022-08-27. [VERIFIED: PyPI JSON API]

### `pyproject.toml` skeleton (verified to build and install editable)
```toml
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[project]
name = "wbmon"
dynamic = ["version"]                     # single source: src/wbmon/__init__.py
requires-python = ">=3.11"
dependencies = ["httpx>=0.28.1,<1", "tzdata"]

[project.optional-dependencies]           # declared up front so Phases 2 and 3 never edit this file concurrently
web = ["streamlit>=1.66,<2", "plotly>=7.1,<8", "pandas>=3,<4"]
dev = ["pytest>=9,<10", "ruff>=0.17,<0.18"]

[project.scripts]
wbmon = "wbmon.cli:main"

[tool.hatch.version]
path = "src/wbmon/__init__.py"

[tool.hatch.build.targets.wheel]
packages = ["src/wbmon"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = ["slow: generated 2.4M-row performance test (about 10 s, about 0.9 GB temp disk)"]
tmp_path_retention_policy = "failed"      # otherwise pytest keeps the last 3 runs of tmp dirs (3 x 0.9 GB for the perf DB)

[tool.ruff]
target-version = "py311"
line-length = 100
src = ["src", "tests"]
```
The project also needs a **`.gitignore`** (none exists today): `.venv/`, `data/`, `*.sqlite3*`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `*.egg-info/`, `.env`, `dist/`.

## Package Legitimacy Audit

`gsd-tools query package-legitimacy check --ecosystem pypi ...` was run (via `node $HOME/.claude/gsd-core/bin/gsd-tools.cjs`). **Every package came back `SUS`**, but only for two heuristic reasons: `unknown-downloads` (the PyPI JSON API exposes no download counts, so the seam cannot evaluate them) and `too-new` (it appears to look at the *latest release* date, which is days to weeks old for actively maintained projects). I cross-checked with the PyPI JSON API: all five have first releases between 2010 and 2022, official source repositories, and no install-time scripts. The verdicts are reported as returned; the disposition below follows the protocol (keep + flag).

| Package | Registry | Age (first release) | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|---------------------|-----------|-------------|---------|-------------|
| httpx | PyPI | 7 yrs (2019-07) | n/a from seam | github.com/encode/httpx | SUS (unknown-downloads) | Already installed in `.venv` and named in the locked CONTEXT decision; no new install |
| tzdata | PyPI | 10 yrs (2016-08) | n/a | github.com/python/tzdata | SUS (too-new, unknown-downloads) | Already installed; named in the locked CONTEXT decision |
| pytest | PyPI | 15 yrs (2010-11) | n/a | github.com/pytest-dev/pytest | SUS (unknown-downloads) | Already installed |
| ruff | PyPI | 4 yrs (2022-08) | n/a | docs.astral.sh/ruff (Astral) | SUS (too-new, unknown-downloads) | **Not installed** - flagged: planner adds one `checkpoint:human-verify` before the first `pip install -e ".[dev]"` |
| hatchling | PyPI | 4 yrs (2022-01) | n/a | github.com/pypa/hatch (PyPA) | SUS (too-new, unknown-downloads) | **Not installed** (build backend, fetched by pip) - same single checkpoint |
| streamlit / plotly / pandas | PyPI | - | n/a | - | SUS (too-new / unknown-downloads / no-repository for pandas) | Only *declared* as the optional `web` extra (already installed in `.venv` by the orchestrator); Phase 1 never imports them; no Phase 1 install |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** all of the above by heuristic only; the planner should insert **one batched** `checkpoint:human-verify` ("ruff, hatchling: confirm install") rather than one per package. httpx/tzdata are user-locked; pytest is already installed.

No other third-party package is recommended. Everything else is stdlib, which is also what makes the COL-05 allowlist test trivial.

## Architecture Patterns

### System Architecture Diagram

```
                       wbmon collect [--top-n --limit-queries --trigger --slot]
                                        |
                             Settings.from_env()  --> params_json (pinned, stored on the run)
                                        |
                  advisory flock(<db>.lock)  --held--> exit 0 "another run active"
                                        |
                          storage.migrate()  (idempotent, user_version)
                                        |
                          abandon_stale_runs()  (safe: lock proves no live run)
                                        |
        slot already in runs? --yes--> log event=slot_exists, exit 0 (no run created)
                                        |
             QuerySource.load() ---- sheet OK + 50..500 rows? ----> query_source='sheet'
                                        \---- else ---------------> query_source='bundled' (+ reason logged)
                                        |
                     writer.start_run() -> run_id (status 'running')
                                        |
        +-------------- for each query, in sheet order (no shuffle) ---------------+
        |   deadline passed? --yes--> record error/deadline (no request)           |
        |   breaker open?    --yes--> record blocked/circuit_open (no request)     |
        |                                                                          |
        |   WbClient.fetch_page(q, page) --sleep(delay) before every request--> WB |
        |        |  classify(status, ctype, body prefix, headers)                  |
        |        |    ok --> extract_products -> Pager (slot numbering, wrap,      |
        |        |           dedupe, cap, top_n) -> normalize() per product        |
        |        |    blocked / rate_limited / error --> QueryResult(no rows)      |
        |        |    page>=2 failure --> QueryResult('partial', rows of pages <k) |
        |        v                                                                 |
        |   writer.record_query()  = BEGIN IMMEDIATE; run_queries row +            |
        |        executemany(observations); COMMIT   (ROLLBACK -> error row)       |
        |   breaker.update(status)                                                 |
        +--------------------------------------------------------------------------+
                                        |
                  writer.finish_run(): counters via SQL aggregate, derive status
                                        |
                  wal_checkpoint(TRUNCATE); log event=run_done; exit 0
```

### Recommended Project Structure
```
wb-serp-monitor/
├── pyproject.toml  .gitignore
├── src/wbmon/
│   ├── __init__.py            # __version__, REPO_URL constant (used in the UA)
│   ├── __main__.py            # sys.exit(main())
│   ├── config.py              # frozen Settings + from_env(environ=None) + validation
│   ├── clock.py               # utc_now(), iso_z(), parse_iso_z(), floor_hour()   (pure)
│   ├── models.py              # frozen dataclasses + Literal enums: THE shared contract
│   ├── log.py                 # key=value formatter, line-buffered stdout
│   ├── delivery.py            # delivery_days(), pure
│   ├── normalize.py           # extract_products, normalize_product, shape, normquery (pure)
│   ├── classify.py            # classify_response (pure), parse_retry_after (pure)
│   ├── pager.py               # PageAccumulator state machine (pure)
│   ├── wb_client.py           # httpx.Client factory + fetch_page (only httpx importer besides queries.py)
│   ├── queries.py             # parse_csv (pure) + load_queries(client, settings)
│   ├── orchestrator.py        # run_once(): isolation, breaker, deadline, status derivation
│   ├── cli.py                 # argparse: init-db | collect | probe
│   ├── storage/
│   │   ├── db.py              # connect_rw(), connect_ro(), ensure_wal(), pragmas
│   │   ├── migrations.py      # MIGRATIONS: list[tuple[str, ...]] (one statement per string)
│   │   ├── writer.py          # Writer: start_run, record_query, finish_run, abandon_stale_runs, upsert_queries
│   │   ├── reader.py          # five STOR-04 access paths (+ connect_ro); Phase 3 extends
│   │   └── lock.py            # flock context manager (Phase 2 scheduler reuses it)
│   └── data/queries_bundled.txt
└── tests/
    ├── conftest.py            # autouse guards (see "Test harness")
    ├── factories.py           # make_product(), make_page(), FakeWB transport
    ├── fixtures/*.json        # 7 shape fixtures, each with "_test_only": true
    └── test_*.py  (+ test_storage_perf.py marked slow, test_import_boundaries.py, test_no_circumvention.py)
```

### Pattern 1: Functional core, imperative shell
Pure modules (`normalize`, `classify`, `pager`, `delivery`, `clock`, `queries.parse_csv`) import neither `httpx`, `sqlite3`, `os`, `time` nor `socket`. Enforce with an `ast` import scan (code in "Code Examples").

### Pattern 2: Per-query atomic write, sleep outside the transaction
All network I/O and `sleep()` happen *before* `BEGIN IMMEDIATE`; the transaction contains only SQL (a few ms). A failure inside the transaction -> `ROLLBACK`, then a *separate* tiny transaction writes an `error` `run_queries` row (`error_class='db_write_failed'`), so the attempt is still visible.

### Pattern 3: Injectable time and sleep
`WbClient(http, settings, *, sleep=time.sleep, monotonic=time.monotonic)`, `run_once(..., now=clock.utc_now)`. Tests pass a recording `sleep` and a stepping clock; no test ever sleeps and `freezegun` is unnecessary.

### Pattern 4: Per-page `collected_at`
`collected_at` = `now()` taken immediately after a page response is received, truncated to whole seconds. Every observation of that page stores it, and `delivery_days` is computed from the *truncated* value, so `delivery_days == delivery_days(parse(collected_at), delivery_hours)` can be re-derived from stored columns at any time (this is what makes later recalibration honest; add a test).

### Anti-Patterns to Avoid
- **`INSERT OR REPLACE` / `REPLACE INTO` anywhere in `src/`** (bypasses the delete trigger - see STOR-02 below). Add a source-grep test.
- **Trusting `total`** for the page count; **stopping on a short page** (WB page sizes can vary slightly, see Pagination semantics).
- **Sleeping or doing HTTP inside a write transaction.**
- **`con.commit()` with `autocommit=True`** (no-op) - use SQL `COMMIT`.
- **Building SQL with f-strings from external text.** The only dynamic SQL allowed is an `IN (?,?,?)` placeholder list and the integer in `PRAGMA user_version = N`.
- **Randomising query order or adding jitter** (CONTEXT rejects both).

## Fixture Design (research item 1)

Field names and nesting below come from real recorded WB payloads in public repositories: `Unti1/T-skRAZRAB0T/products.json` (search with `normquery`, 3 ads), `mishannn/wb-goods-feed/.../products_response_fixture.json` (90 ads, 37 multi-size), `ibrohim-Fazliddinov/WB-Analyze/json.json` (multi-size with differing per-size delivery), `gldkizz/programmingPetProject/.../wb_catalogs_data.json` (search metadata), `ReptiloidAnunak/wildberries_pars_app/test.json` (legacy `priceU/salePriceU`). [CITED: github.com repos listed in Sources; counts recomputed locally] **All values below are invented and test-only; do not copy real payload contents (names, sellers, tokens) into the repo.** Use obviously fake ids (`9000000xx`), names prefixed `ТЕСТ`, and opaque strings `TEST-OPAQUE-*` for `payload`/`logs`/`qv` (they must be ignored and never stored).

Every fixture gets a top-level `"_test_only": true` key (harmless to the parser, which ignores unknown keys). A conftest check asserts every `tests/fixtures/*.json` has it, so a real payload can never be pasted in by accident.

### Observed envelope variants

| Variant | Where seen | Envelope keys | Products at | `total` at | `normquery` at |
|---------|-----------|---------------|-------------|------------|----------------|
| A: `data.products` (v13-v18 on `search.wb.ru`) | 5 recorded payloads [CITED] | `metadata, state:0, version:2, payloadVersion:2, data` (+ optional `params`) | `data.products` | `data.total` (absent in 2 of 5) | `metadata.normquery` (top-level `metadata`) |
| B: top-level `products` (v18 on `u-search.wb.ru`) | parser code only: `maxexplorer/ParserProject` reads `json_data.get('total')` and `json_data.get('products')` [CITED]; no recorded payload found | `metadata, total, products` (other keys unknown) | `products` | `total` | `metadata.normquery` (assumed same as A) |
| Legacy | `ReptiloidAnunak/.../test.json` | as A plus `params:{version,curr,dest,payloadVersion}` | `data.products` | `data.total` | `metadata.normquery` |

`metadata` (real): `{"name":"<raw query>","catalog_type":"preset","catalog_value":"preset=<id>","normquery":"<WB-normalised>","search_result":{},"rmi":"1","title":"<raw query>","rs":100,"qv":"<opaque base64>"}`. `rs` = 100 (page size). `normquery` differs from the raw query in practice (`чехол на айфон` -> `чехол apple iphone`). In older payloads `metadata` has only `name, catalog_type, catalog_value`, so `normquery` must be optional. [CITED: Unti1, gldkizz payloads]

### Product skeleton (variant A, full real key order; ad item shown)

```json
{
  "_test_only": true,
  "metadata": {"name": "тест-запрос", "catalog_type": "preset", "catalog_value": "preset=1",
               "normquery": "тест запрос", "search_result": {}, "rmi": "1", "title": "тест-запрос",
               "rs": 100, "qv": "TEST-OPAQUE-QV"},
  "state": 0, "version": 2, "payloadVersion": 2,
  "data": {
    "total": 2122,
    "products": [
      {
        "time1": 3, "time2": 26, "wh": 900001, "dtype": 4, "dist": 120,
        "id": 900000002, "root": 800000002, "kindId": 0,
        "brand": "ТЕСТ-БРЕНД", "brandId": 700001, "siteBrandId": 0,
        "colors": [{"name": "белый", "id": 16777215}],
        "subjectId": 2300, "subjectParentId": 6260,
        "name": "ТЕСТ товар 900000002", "entity": "", "matchId": 600001,
        "supplier": "ТЕСТ-Продавец", "supplierId": 500001, "supplierRating": 4.8, "supplierFlags": 0,
        "pics": 5, "rating": 5, "reviewRating": 4.7, "nmReviewRating": 4.6,
        "feedbacks": 120, "nmFeedbacks": 40, "volume": 10, "viewFlags": 0,
        "sizes": [
          {"name": "", "origName": "0", "rank": 0, "optionId": 900000021, "wh": 900001,
           "time1": 3, "time2": 26, "dtype": 4,
           "price": {"basic": 300000, "product": 150000, "total": 150000, "logistics": 0, "return": 0},
           "saleConditions": 134217728, "payload": "TEST-OPAQUE-PAYLOAD"}
        ],
        "totalQuantity": 25,
        "log": {"cpm": 1025, "promotion": 1, "promoPosition": 2, "position": 7, "advertId": 99000001, "tp": "c"},
        "logs": "TEST-OPAQUE-LOGS",
        "meta": {"tokens": []}
      }
    ]
  }
}
```
Facts behind it [CITED + recomputed locally]: prices are integer **kopecks**; `price` keys are exactly `basic, product, total, logistics, return`; `total == product + logistics` in every recorded size; `log` keys are exactly `cpm, promotion, promoPosition, position, advertId, tp`; `log` is absent (not null) on organic items; `log.promoPosition` equalled the 1-based array index of the item in **93 of 93** ads in the two multi-ad payloads (page 1 only - page 2 unverified); `time1/time2` live at product level and usually also on each size (the mishannn payload has them only at product level); product-level `time1+time2` equalled the minimum over sizes in the multi-size examples; `rating` (int) differs from `reviewRating` (float); `nmReviewRating/nmFeedbacks` are absent in the mishannn payload (so must be NULL-able); 55 of 100 items in the `m1 pro` payload had `reviewRating 0 / feedbacks 0`.

### Fixture set (7 JSON files + factory) and expected normalization

Build the files with a tiny helper (kept in `tests/factories.py`, not a script that writes anywhere else). Expected values below were produced by a throwaway reference normalizer run on exactly these fixtures with `collected_at = 2026-10-16T07:00:00Z` (10:00 MSK). [VERIFIED: local run]

| Fixture file | Content | Expected result |
|--------------|---------|-----------------|
| `search_data_products.json` | Variant A, 3 products: #1 organic single-size (`product 150000`, `basic 300000`); #2 ad (`log` as above, `promoPosition 2` = its index); #3 multi-size: `sizes` XXS `product 144600 total 153900 logistics 9300 time 2+211`, M `product 139900 total 147800 logistics 7900 time 2+107`, L `product 144600 time 3+203`; product-level `time1 2, time2 107` | pos1: price 1500.00 / basic 3000.00 / total 1500.00 / logistics 0.00 / rating 4.7 / feedbacks 120 / nm 4.6, 40 / hours 29 / days 1 / promoted 0. pos2: same plus promoted 1, `ad_promo_position 2`, `ad_organic_position 7`, `ad_tp 'c'`, `ad_cpm 1025`. pos3: **price 1399.00 (min size M)**, basic 4750.00, **total 1478.00 and logistics 79.00 from the same size**, hours 109, days 4. `normquery = 'тест запрос'`, shape `data.products` |
| `search_top_level_products.json` | Same three products, envelope `{"metadata":..., "total":2122, "products":[...]}` | Identical rows to the above, shape `products` |
| `search_legacy_prices.json` | Variant A + `params`; product with top-level `priceU 129000, salePriceU 72100, logisticsCost 600` and a `sizes[0]` that has **no `price` key** (`time1 2, time2 42` on the size) | price 721.00, basic 1290.00, **total NULL, logistics NULL** (recommended: only `price_rub`/`price_basic_rub` from the legacy fields), hours from product-level 3+26=29 |
| `search_missing_fields.json` | 6 products: (11) `sizes: []`, `time1/time2: null`, `reviewRating 0`, `feedbacks 0`; (12) `sizes: [null, {price: {basic 0, product 0, total 0, ...}}]`; (13) keys **absent**: `supplier, supplierId, reviewRating, feedbacks, nmReviewRating, nmFeedbacks, totalQuantity, time1, time2`; (14) `log: {}`; (15) `log: null`; (16) `log` with `cpm: 0, promoPosition 6, position 3` | (11) price/basic/total/logistics NULL, **rating NULL, feedbacks 0**, hours/days NULL. (12) price NULL (a `product: 0` is *unpriced*, never "cheapest"). (13) seller NULL, rating/feedbacks/nm_* NULL, total_quantity NULL, but price 1500.00 and hours 29 (**product-level time absent -> fall back to the size with min time1+time2 -> 3+26**). (14) and (15) `is_promoted 0`, all ad_* NULL. (16) `is_promoted 1` (cpm 0 is still an ad), `ad_cpm 0` |
| `search_products_null.json` | `{"data": {"products": null, "total": 0}, "metadata": {...}}` | `extract_products -> None` -> class `products_null`, status `error` |
| `search_zero_products.json` | `{"data": {"products": [], "total": 0}, ...}` | page 1: `error/empty_suspect`; page >=2: end of results |
| `search_schema_drift.json` | `{"data": {"items": []}}` (no products key anywhere) | `extract_products -> None`, shape `unknown` -> `error/schema_drift` |
| `wall_403.html`, `wall_200.html` | Short HTML bodies (`<!DOCTYPE html><html>...`) | 403 -> `blocked/http_403`; 200 + HTML -> `blocked/html_at_200` |

Add two inline cases in unit tests (no file needed): a product with a **non-integer or missing `id`** (skipped, counted in a `product_skipped` log event; its slot index is still consumed) and a **string-typed number** (`"id": "900000099"`, `"feedbacks": "12"`) which the coercion helpers accept.

**Pagination fixtures are generated, not stored:** `tests/factories.make_page(first_id, n, ads={index: promoPosition})` returns a minimal valid payload (`id`, `name`, one priced size, optional `log`) so a 100-item page costs one line. Scenarios: `2x100`, `page2 == page1`, `page2 empty`, `60 then empty`, `60 then wrap`, `page size 48`, `dup id on page2`, `page2 shorter than page1 (97) -> needs page 3`, `page2 fails`, `page2 reshuffled subset of page1`. For the ad-numbering open question add: ad on page 2 at global position 105 with `promoPosition 5` (page-relative) -> **no mismatch**; with `promoPosition 9` -> mismatch +1; ad on page 1 at index 2 with `promoPosition 5` -> mismatch +1.

### MockTransport harness (route by host, never allow an unscripted request)

```python
# tests/factories.py  [VERIFIED pattern: httpx 0.28.1 MockTransport; redirect following works with MockTransport]
import httpx

class FakeWB:
    """Scripted responses keyed by (query, page); records every request; fails on anything unscripted."""
    def __init__(self, scripts, sheet_csv: bytes | None = None):
        self.scripts = {k: list(v) for k, v in scripts.items()}   # each value: list of Response|Exception, popped in order
        self.sheet_csv = sheet_csv
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "docs.google.com":
            if self.sheet_csv is None:
                raise AssertionError("sheet fetched but not scripted")
            return httpx.Response(200, content=self.sheet_csv, headers={"content-type": "text/csv; charset=utf-8"})
        q = request.url.params["query"]; page = int(request.url.params["page"])
        queue = self.scripts.get((q, page))
        if not queue:
            raise AssertionError(f"unscripted WB request: query={q!r} page={page}")
        item = queue.pop(0)
        if isinstance(item, Exception):
            item.request = request           # httpx exceptions carry the request
            raise item
        return item

    def wb_requests(self):
        return [r for r in self.requests if r.url.host != "docs.google.com"]

def build_client(fake: FakeWB, settings):
    from wbmon.wb_client import make_http_client
    return make_http_client(settings, transport=httpx.MockTransport(fake))
```
Status matrix as `pytest.mark.parametrize("response, outcome, error_class", [...])`: `Response(403, html)`, `Response(498)`, `Response(429, headers={"Retry-After": "7"})`, `Response(200, html, ctype text/html)`, `Response(200, "{bad")`, `Response(502)`, `Response(307, headers={"location": url})`, `Response(404)`, `httpx.ReadTimeout("t")`, `httpx.ConnectError("c")`. Assertions that matter most: **number of requests made** (`len(fake.wb_requests())`): 403/498 -> 1 (no retry), 429 -> 2 with exactly one recorded sleep, 5xx/timeouts -> 3 with sleeps `[2, 5]`, circuit-open queries -> 0 additional requests.

## SQLite Specifics (research item 2)

All results below were produced by running the code on Python 3.11.15, 3.12.13 (SQLite 3.53.1) and 3.14.6 (SQLite 3.50.4). [VERIFIED: local experiments]

### Connection recipe (3.11 - 3.14 safe)

```python
# storage/db.py
import sqlite3, sys, time
from pathlib import Path

def connect_rw(path: str | Path) -> sqlite3.Connection:
    # 3.12+: explicit autocommit=True, because the documented future default is autocommit=False,
    # under which a manual BEGIN fails with "cannot start a transaction within a transaction".
    # 3.11 has no such parameter: isolation_level=None gives the same behaviour.
    kw = {"autocommit": True} if sys.version_info >= (3, 12) else {"isolation_level": None}
    con = sqlite3.connect(str(path), timeout=30, **kw)
    con.execute("PRAGMA busy_timeout = 30000")
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA synchronous = NORMAL")
    con.execute("PRAGMA recursive_triggers = ON")   # REQUIRED: makes REPLACE fire the append-only DELETE trigger
    ensure_wal(con)
    return con

def ensure_wal(con, attempts=20, pause=0.25):
    for _ in range(attempts):
        if con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
            return
        try:
            if con.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() == "wal":
                return
        except sqlite3.OperationalError:       # concurrent first-time init can raise 'database is locked'
            time.sleep(pause)
    raise RuntimeError("could not switch database to WAL")

def connect_ro(path: str | Path) -> sqlite3.Connection:
    uri = Path(path).resolve().as_uri() + "?mode=ro"     # NOT f"file:{path}?mode=ro" - see pitfall 7
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.execute("PRAGMA query_only = ON")
    con.execute("PRAGMA busy_timeout = 5000")
    return con
```
Always use `contextlib.closing(connect_rw(...))` (3.13+ emits `ResourceWarning` for an unclosed connection; the verification scripts ran with `warnings.simplefilter("error")` and produced none). Transactions are **always** SQL statements: `con.execute("BEGIN IMMEDIATE")` ... `con.execute("COMMIT")` / `con.execute("ROLLBACK")`. With `autocommit=True`, `con.commit()` is a documented no-op; with `isolation_level=None` it works, so SQL statements are the only form that behaves identically on every version. [CITED: docs.python.org/3/library/sqlite3.html - "autocommit currently defaults to LEGACY_TRANSACTION_CONTROL. The default will change to False in a future Python release"; "If autocommit is True ... commit() and rollback() have no effect"]. Verified: with `autocommit=False`, `BEGIN IMMEDIATE` fails with `OperationalError: cannot start a transaction within a transaction`; with `autocommit=True` it works and `in_transaction` is `True`.

### Migrations with `PRAGMA user_version`

One **statement per string** (triggers contain `;` inside `BEGIN ... END`, so a naive `split(";")` of a script breaks them, and `executescript` cannot re-check the version under the write lock):

```python
# storage/migrations.py
MIGRATIONS: list[tuple[str, ...]] = [
    (   # -> user_version 1
        "CREATE TABLE queries (...)", "CREATE TABLE runs (...)", "CREATE INDEX ix_runs_started ON runs(started_at)",
        "CREATE TABLE run_queries (...)", "CREATE TABLE observations (...)",
        "CREATE INDEX ix_obs_query_nm_time ON observations(query_id, nm_id, collected_at)",
        "CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations "
        "BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END",
        # ... remaining triggers
    ),
]

def migrate(con):
    latest = len(MIGRATIONS)
    if user_version(con) > latest:
        raise RuntimeError(f"database schema v{user_version(con)} is newer than this code (v{latest})")
    while user_version(con) < latest:
        con.execute("BEGIN IMMEDIATE")
        try:
            cur = user_version(con)                 # re-read UNDER the write lock (another process may have migrated)
            if cur < latest:
                for stmt in MIGRATIONS[cur]:
                    con.execute(stmt)
                con.execute(f"PRAGMA user_version = {cur + 1}")   # int we format ourselves
            con.execute("COMMIT")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
```
Verified: DDL and `PRAGMA user_version` are transactional (a failing migration leaves `user_version` and tables unchanged); 6 concurrent processes migrating one fresh file all end at the final version; a rerun is a no-op; a database newer than the code is refused; triggers survive a later `ALTER TABLE ... ADD COLUMN` migration. Concurrency note: in 1 of ~10 trials on 3.11 two of six concurrent first-time initialisations failed with `database is locked` raised by the bare `PRAGMA journal_mode=WAL` even though `busy_timeout` was set; reading the mode first and retrying (`ensure_wal`) gave 0 failures in 8 trials (small sample, but the guard is free). `init-db` creates the parent directory of the DB path.

### Verified DDL (valid on 3.11/3.12/3.14; use as migration 1)

```sql
CREATE TABLE queries (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL UNIQUE,                       -- BINARY collation: Cyrillic 'х' != Latin 'x', NFC != NFD
  sheet_order INTEGER NOT NULL,
  first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);

CREATE TABLE runs (
  id INTEGER PRIMARY KEY,
  slot_utc TEXT UNIQUE,                            -- NULL for manual runs; UNIQUE allows many NULLs
  trigger TEXT NOT NULL CHECK (trigger IN ('manual','scheduler','probe')),
  started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','ok','partial','blocked','error','abandoned')),
  query_source TEXT NOT NULL CHECK (query_source IN ('sheet','bundled')),
  params_json TEXT NOT NULL CHECK (json_valid(params_json)),
  top_n INTEGER NOT NULL CHECK (top_n >= 1),
  n_queries INTEGER NOT NULL DEFAULT 0, n_ok INTEGER NOT NULL DEFAULT 0, n_partial INTEGER NOT NULL DEFAULT 0,
  n_blocked INTEGER NOT NULL DEFAULT 0, n_error INTEGER NOT NULL DEFAULT 0, n_items INTEGER NOT NULL DEFAULT 0,
  error TEXT);
CREATE INDEX ix_runs_started ON runs(started_at);

CREATE TABLE run_queries (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id), query_id INTEGER NOT NULL REFERENCES queries(id),
  status TEXT NOT NULL CHECK (status IN ('ok','partial','blocked','rate_limited','error')),
  error_class TEXT, http_status INTEGER,
  pages_fetched INTEGER NOT NULL DEFAULT 0, items_collected INTEGER NOT NULL DEFAULT 0,
  is_complete INTEGER NOT NULL DEFAULT 0 CHECK (is_complete IN (0,1)),
  normquery TEXT, ad_slot_mismatch INTEGER NOT NULL DEFAULT 0, duplicates_dropped INTEGER NOT NULL DEFAULT 0,
  error_message TEXT, started_at TEXT NOT NULL, duration_ms INTEGER,
  UNIQUE (run_id, query_id),
  CHECK (status <> 'ok' OR items_collected > 0));
CREATE INDEX ix_rq_query_run ON run_queries(query_id, run_id);

CREATE TABLE observations (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id), query_id INTEGER NOT NULL REFERENCES queries(id),
  collected_at TEXT NOT NULL, position INTEGER NOT NULL CHECK (position >= 1), nm_id INTEGER NOT NULL,
  name TEXT, brand TEXT,
  price_rub REAL, price_basic_rub REAL, price_total_rub REAL, logistics_rub REAL,
  rating REAL, feedbacks INTEGER, nm_rating REAL, nm_feedbacks INTEGER,
  time1_h INTEGER, time2_h INTEGER, delivery_hours INTEGER, delivery_days INTEGER,
  seller_name TEXT, seller_id INTEGER,
  is_promoted INTEGER NOT NULL DEFAULT 0 CHECK (is_promoted IN (0,1)),
  ad_promo_position INTEGER, ad_organic_position INTEGER, ad_tp TEXT, ad_cpm INTEGER, total_quantity INTEGER,
  UNIQUE (run_id, query_id, position));
CREATE INDEX ix_obs_query_nm_time ON observations(query_id, nm_id, collected_at);
CREATE INDEX ix_obs_query_time    ON observations(query_id, collected_at);

CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
CREATE TRIGGER run_queries_no_delete BEFORE DELETE ON run_queries
BEGIN SELECT RAISE(ABORT, 'run_queries is append-only'); END;
CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, 'runs is append-only'); END;
-- Recommended additions (consistent with QRY-04 / "written once"): 
CREATE TRIGGER queries_no_delete BEFORE DELETE ON queries
BEGIN SELECT RAISE(ABORT, 'queries are never deleted'); END;
CREATE TRIGGER run_queries_no_update BEFORE UPDATE ON run_queries
BEGIN SELECT RAISE(ABORT, 'run_queries rows are written once'); END;
```
Notes:
- **No separate `(run_id, query_id, position)` index.** `UNIQUE (run_id, query_id, position)` already creates `sqlite_autoindex_observations_1`; the planner used it for the snapshot query. The locked list's third index is therefore redundant (it would only cost ~15% insert time and disk). Keep `ix_obs_query_nm_time`, `ix_obs_query_time`, `ix_rq_query_run`; add `ix_runs_started`.
- `run_queries` rows are inserted once with final values (single transaction), so the UPDATE trigger is safe; only `runs` is updated (`finish_run`, abandon).
- No `STRICT` tables: needs SQLite >= 3.37, an unnecessary portability risk; `CHECK`s plus typed dataclasses cover it.
- Do **not** add `AUTOINCREMENT` (not needed, slower).
- Test the schema/model contract: `[c for c in PRAGMA table_info(observations)]` minus `id` must equal `dataclasses.fields(ObservationRow)` names, so the model and the table cannot drift.

### What the triggers do and do not guarantee (verified)

| Statement | Result |
|-----------|--------|
| `UPDATE observations ...` | `IntegrityError: observations is append-only` |
| `DELETE FROM observations ...` | `IntegrityError: observations is append-only` |
| `INSERT ... ON CONFLICT(run_id,query_id,position) DO UPDATE ...` | rejected by the UPDATE trigger |
| `INSERT OR REPLACE INTO observations ...` with a conflicting key | **succeeds and silently replaces the row** when `recursive_triggers` is OFF (the default); rejected with `recursive_triggers=ON` |
| `INSERT` with duplicate `(run,query,position)` | `IntegrityError: UNIQUE constraint failed` |
| `INSERT` `position = 0`; `run_queries` `status='ok', items_collected=0` | `IntegrityError: CHECK constraint failed` |
| FK violation (unknown run id) | `IntegrityError: FOREIGN KEY constraint failed` (only with `foreign_keys=ON`) |
| `DROP TRIGGER` / `DROP TABLE` by anyone with file access | not preventable at SQL level; document that the guarantee is against application code, not against the file owner |

### Per-query write transaction

```python
def record_query(self, run_id, query_id, result, obs_rows):
    con = self.con
    con.execute("BEGIN IMMEDIATE")
    try:
        con.execute(INSERT_RUN_QUERY_SQL, params)                      # plain INSERT, never OR REPLACE
        if obs_rows:
            con.executemany(INSERT_OBS_SQL, [row.as_params(run_id, query_id) for row in obs_rows])
        con.execute("COMMIT")
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
```
Generate `INSERT_OBS_SQL` once from `dataclasses.fields(ObservationRow)` with named placeholders (`:nm_id`, ...). Verified: after a mid-transaction `IntegrityError` + `ROLLBACK`, zero rows remain and `in_transaction` is `False`.

### Abandon stale runs and the lock

`UPDATE runs SET status='abandoned', finished_at=?, error='process died before finishing' WHERE status='running'` is only correct if no live run exists. CONTEXT puts the file lock in Phase 2, but without it a second `collect` started while the first is still running would abandon a healthy run. Recommendation: Phase 1 ships `storage/lock.py` (`fcntl.flock(fd, LOCK_EX | LOCK_NB)` on `<db_path>.lock`, raising `BlockingIOError` when held; the kernel releases it when the process dies, so it can never be stale) and `collect` holds it for the whole run; "lock held" -> log `event=lock_busy`, exit 0. Phase 2 reuses the same helper. (POSIX-only; the project targets Linux/macOS.)

### STOR-04: measured on a generated 2.4M-row database

Generator (test-only, `tmp_path`, roughly 7 s; one recursive-CTE cross join, no Python loop):
```sql
-- after migrate(); queries 1..70 and runs 1..168 inserted similarly with recursive CTEs, run_queries = runs x queries (11 760 rows)
INSERT INTO observations(run_id, query_id, collected_at, position, nm_id, name, brand, price_rub, ... , total_quantity)
WITH RECURSIVE pos(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM pos WHERE i < 200)
SELECT r.id, q.id, r.started_at, pos.i,
       100000000 + q.id*1000 + ((pos.i*7 + r.id*13) % 450),     -- 450 distinct nm_id per query, unique within a run-query, churning per run
       'Комплект постельного белья 2-спальный, поплин, 100% хлопок, арт. '||pos.i, 'Бренд '||(pos.i % 37),
       1000.5+pos.i, 2000.0+pos.i, 1000.5+pos.i, 0.0, 4.7, 1200+pos.i, 4.7, 300, 3, 26, 29, 1,
       'ООО Продавец '||(pos.i % 91), 40000+(pos.i % 91), (pos.i % 11 = 0), NULL, NULL, NULL, NULL, 500
FROM runs r CROSS JOIN queries q CROSS JOIN pos;
```
For the generator use `PRAGMA synchronous=OFF` and one big transaction (test-only; the production writer keeps `NORMAL`). Then `ANALYZE`, `wal_checkpoint(TRUNCATE)`, open `connect_ro`, time each query once on a fresh connection.

| Access path | SQL shape | Time (3.12 / 3.14, warm cache) | Plan |
|-------------|-----------|---------------------------------|------|
| List queries | `SELECT id,text FROM queries ORDER BY sheet_order` | 0.1-0.3 ms | scan 70 rows |
| nm_ids ever seen (count, best, last) | `SELECT nm_id, COUNT(*), MIN(position), MAX(collected_at) FROM observations WHERE query_id=? GROUP BY nm_id` | **24 ms** (450 groups over ~33.6k rows) | `SEARCH ... USING INDEX ix_obs_query_nm_time (query_id=?)` |
| ... with latest name/brand | correlated `MAX(collected_at)` subquery per nm_id | 8-9 ms | covering index subquery |
| Positions of 5 chosen nm_ids over time | `WHERE query_id=? AND nm_id IN (?,?,?,?,?) GROUP BY run_id, nm_id` | 0.6 ms | `ix_obs_query_nm_time (query_id=? AND nm_id=?)` |
| Latest characteristics (5 ids) | correlated `MAX(collected_at)` | 0.3 ms | same index, covering subquery |
| Run log (last 100) | `SELECT * FROM runs ORDER BY started_at DESC LIMIT 100` | 0.2 ms | `ix_runs_started` |
| Heatmap (all run_queries) | `SELECT run_id, query_id, status FROM run_queries` | 2.8 ms (11 760 rows) | scan |
| Top-200 snapshot of query@run | `WHERE run_id=? AND query_id=? ORDER BY position` | 0.6 ms | `sqlite_autoindex_observations_1` |
| Latest ok run of a query | `SELECT MAX(run_id) FROM run_queries WHERE query_id=? AND status='ok'` | 0.2 ms | `ix_rq_query_run` |
| Sidebar totals | `COUNT(*)` 7.6 ms; `COUNT(DISTINCT nm_id)` over all rows 58 ms; `SUM(runs.n_items)` 0.3 ms | all < 100 ms | covering scans |

Result: **2 352 000 rows, 860 MB file (about 365 bytes/row with three indexes), every STOR-04 path < 25 ms, 40x margin to the 1 s requirement.** Numbers are warm-cache (macOS cannot drop the page cache without sudo); a cold cache on a VPS disk adds I/O but the indexed paths touch only a few thousand pages. The perf test should assert `< 1.0` s per path, assert via `EXPLAIN QUERY PLAN` that the plan text contains `USING INDEX`/`COVERING INDEX` and not `SCAN observations`, mark the test `slow`, and delete the DB in a finalizer (about 0.9 GB temp disk; also set `tmp_path_retention_policy = "failed"`). **Disk planning for Phase 2/4:** about 0.9 GB per week at 70 x 200 hourly with long names.

`reader.py` in Phase 1 must expose these functions (they are what Phase 3 builds on and what the perf test calls): `list_queries`, `nm_ids_for_query`, `positions_for`, `latest_characteristics`, `run_log`, `snapshot`, `latest_ok_run_id`.

## httpx Specifics (research item 3)

All [VERIFIED: httpx 0.28.1 local experiments].

### Client construction
```python
def make_http_client(settings, *, transport=None) -> httpx.Client:
    client = httpx.Client(
        headers={"User-Agent": settings.user_agent, "Accept": "application/json",
                 "Accept-Language": "ru-RU,ru;q=0.9"},
        timeout=httpx.Timeout(30.0, connect=10.0),   # read/write/pool 30, connect 10 (httpx default is 5 s: always set explicitly)
        follow_redirects=False,                      # a 3xx from the WB API is data (classified), never followed
        trust_env=False,                             # ignore ambient HTTP(S)_PROXY / SSL_CERT env: "no proxies" must be literally true
        transport=transport,                         # tests inject httpx.MockTransport
    )
    client.cookies.jar.set_policy(_NoCookies())     # httpx otherwise stores Set-Cookie and replays it on the next request
    return client
```
```python
import http.cookiejar as cj
class _NoCookies(cj.CookiePolicy):
    netscape = True; rfc2965 = False; hide_cookie2 = False
    def set_ok(self, cookie, request): return False
    def return_ok(self, cookie, request): return False
    def domain_return_ok(self, domain, request): return False
    def path_return_ok(self, path, request): return False
```
Verified: with defaults a `Set-Cookie` on response 1 puts `cookie: sid=abc` on request 2; after `client.cookies.jar.set_policy(_NoCookies())` request 2 carries no cookie and the jar stays empty (passing a pre-built jar through `httpx.Cookies(jar)` does **not** work - httpx copies it). Verified: with `HTTPS_PROXY` set in the environment and default `trust_env`, httpx mounts a proxy transport; with `trust_env=False` there are no mounts. The sheet client is a *separate short-lived* `httpx.Client(follow_redirects=True, timeout=Timeout(15.0, connect=10.0), trust_env=False)` with the same UA and the same no-op cookie policy (Google may set consent cookies; they must not be kept either).

### Wire encoding of `params=` (all 12 probe strings round-trip exactly via `request.url.params["query"]`)

| Query text | On the wire (`request.url.query`) |
|------------|-----------------------------------|
| `постельное бельё` | `%D0%BF...%D0%B1%D0%B5%D0%BB%D1%8C%D1%91` (`ё` = `%D1%91`), space = `+` |
| `комплект 160х200` (Cyrillic х) | `...160%D1%85200` |
| `комплект 160x200` (Latin x) | `...160x200` (distinct bytes) |
| `100% хлопок` | `100%25+%D1%85...` |
| `наволочки 70*70` | `...70%2A70` |
| `одеяло, 2 спальное` | `...%2C+2+...` |
| `a&b=c?d#e` | `a%26b%3Dc%3Fd%23e` |
| `  двойной  пробел ` | `++%D0...++...+` (all spaces preserved) |
| NFD `бельё` (`е` + U+0308) | `...%D0%B5%CC%88` (distinct from NFC) |
| `+плюс` | `%2B...` |

Pass non-str values as strings (`"false"`, `"30"`) when pinning the param set - httpx renders `False` as `false` but explicit strings keep `params_json` identical to the wire. The test asserts `request.url.params["query"] == original` for **all 70 real queries** plus the 12 probes, which proves nothing normalised or double-encoded them. Whether WB decodes `+` as a space is not verifiable offline [ASSUMED, see A1]: `requests`-based public WB parsers send exactly this encoding to v18 (`maxexplorer/ParserProject` passes a `params=` dict with Cyrillic category names) [CITED].

### Other verified facts
- `httpx.Response(498)` is valid (`reason_phrase == ""`, `is_error True`).
- Invalid JSON body (HTML or non-UTF-8 bytes) -> `response.json()` raises `json.JSONDecodeError` (a `ValueError`); catch `ValueError`.
- Timeouts: `httpx.ReadTimeout` is a `TimeoutException` and a `TransportError`; `httpx.ConnectError` is a `TransportError` but not a `TimeoutException`. Catch `httpx.TransportError` for the retry path (covers timeouts, connect/read errors) - and in MockTransport handlers raise them with `request=request` or httpx raises `RuntimeError` when the exception has no request.
- Default redirect behaviour: a 307 comes back as `status_code 307, is_redirect True` and is not followed (`follow_redirects=False`).
- `Response.text` keeps a UTF-8 BOM; `csv` parsing must start from `resp.content.decode("utf-8-sig")`; wrap in `io.StringIO(text, newline="")` for `csv.reader`; `csv.reader` yields `[]` for blank lines (skip them) and keeps leading/trailing spaces inside cells.
- Google Sheet redirect chain (`docs.google.com` 307 -> `googleusercontent.com` 200) works under `MockTransport` with `follow_redirects=True`.
- Content negotiation: httpx sends `Accept-Encoding: gzip, deflate` by default; leave it.

### Retry-After parser (verified on 12 inputs)
```python
import email.utils, datetime as dt
def parse_retry_after(value: str | None, now: dt.datetime, cap: float = 60.0) -> float | None:
    if value is None: return None
    v = value.strip()
    try:
        secs = float(v)
    except ValueError:
        try: d = email.utils.parsedate_to_datetime(v)
        except (TypeError, ValueError): return None
        if d.tzinfo is None: d = d.replace(tzinfo=dt.timezone.utc)
        secs = (d - now).total_seconds()
    if secs != secs: return None            # NaN
    return min(max(secs, 0.0), cap)         # negative/past date -> 0, inf -> cap
```
Results: `"7"->7.0`, `"0"->0.0`, `"-5"->0.0`, `"120"->60.0` (capped), `"1.5"->1.5`, `"inf"->60.0`, `"nan"->None`, `"abc"->None`, `""->None`, `None->None`, HTTP-date 30 s ahead `->30.0`, HTTP-date in the past `->0.0`. When absent/unparseable use a fixed default wait (recommend 30 s, A4).

### Detecting HTML at 200 / classification matrix (pure `classify_response`)

Inputs: `status`, `content_type`, `body[:512]`, `headers` (for `server`). Order matters: status first, then body sniffing, then JSON/shape.

| Condition | Outcome | `error_class` | Retry in run? |
|-----------|---------|---------------|---------------|
| 403 | `blocked` | `http_403` | no |
| 498 | `blocked` | `http_498` | no |
| 3xx (any) | `blocked` | `redirect` | no (A2: never followed; other maintainers report a self-referential 307 from the edge on datacenter hosts) |
| 429 | `rate_limited` | `http_429` | once, after `min(Retry-After or 30, 60)` s |
| 200 and first non-space byte is `<` (or content-type contains `html`) | `blocked` | `html_at_200` | no |
| 200, not JSON object after parse | `error` | `bad_json` | no |
| 200, JSON but `extract_products` is `None` and no `data.products`/`products` key at all | `error` | `schema_drift` | no |
| 200, `products` key present but `null` | `error` | `products_null` | no |
| 200, page 1, `products == []` | `error` | `empty_suspect` | no |
| 200, page >= 2, `products == []` | end of results (complete) | - | - |
| 200, products non-empty | `ok` | - | - |
| 5xx | `error` | `http_5xx` | up to 2 retries, sleeps 2 s then 5 s |
| timeout / connection error (`httpx.TransportError`) | `error` | `timeout` / `connection` | same 2 retries |
| other 4xx (400, 401, 404, ...) | `error` | `http_4xx` | no |

Record in `error_message`: `http=<status> server=<server header> ctype=<type> body=<first 200 chars, control chars replaced>` (never the whole body, never cookies). A failing *page >= 2* after page 1 succeeded turns the query into `partial` with the same `error_class`.

## Delivery-Days Function (research item 4)

Verified implementation (pure; Python 3.11-3.14):

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
MSK = ZoneInfo("Europe/Moscow")        # UTC+3 fixed since 2014-10-26; needs the tzdata package on slim images
MAX_HOURS = 24 * 365

def delivery_days(collected_at_utc: datetime, hours: int | None, tz: ZoneInfo = MSK) -> int | None:
    if hours is None or isinstance(hours, bool) or not isinstance(hours, int):
        return None
    if hours < 0 or hours > MAX_HOURS:          # no invented value, and no OverflowError on absurd input
        return None
    if collected_at_utc.tzinfo is None or collected_at_utc.utcoffset() != timedelta(0):
        raise ValueError("collected_at_utc must be timezone-aware UTC")   # a naive datetime would silently use local time
    start = collected_at_utc.astimezone(tz).date()
    end = (collected_at_utc + timedelta(hours=hours)).astimezone(tz).date()   # add hours in UTC, then convert
    return (end - start).days
```

### Test vectors (all executed and passing on 3.11.15, 3.12.13, 3.14.6)

| collected_at (UTC) | MSK wall clock | hours | expected | Why |
|--------------------|----------------|-------|----------|-----|
| 2026-10-16T07:00:00Z | 10:00 | 0 | 0 | today |
| 2026-10-16T07:00:00Z | 10:00 | 5 | 0 | 15:00 same day (`ceil(5/24)` would say 1) |
| 2026-10-16T07:00:00Z | 10:00 | 13 | 0 | 23:00 same day |
| 2026-10-16T07:00:00Z | 10:00 | 14 | 1 | exactly 00:00 next day (boundary) |
| 2026-10-16T07:00:00Z | 10:00 | 24 | 1 | 10:00 next day |
| 2026-10-16T07:00:00Z | 10:00 | 37 | 1 | 23:00 next day |
| 2026-10-16T07:00:00Z | 10:00 | 38 | 2 | 00:00 day after next |
| 2026-10-16T20:30:00Z | 23:30 | 1 | 1 | crosses midnight |
| 2026-10-16T20:59:59Z | 23:59:59 | 0 | 0 | last second of the day |
| 2026-10-16T20:59:59Z | 23:59:59 | 1 | 1 | |
| 2026-10-16T21:00:00Z | 00:00:00 (17th) | 0 | 0 | UTC date is still the 16th but MSK date is the 17th: proves local-date use |
| 2026-10-16T21:00:00Z | 00:00:00 | 23 | 0 | 23:00 same MSK day |
| 2026-10-16T21:00:00Z | 00:00:00 | 24 | 1 | |
| 2026-12-31T20:30:00Z | 23:30 | 2 | 1 | year rollover |
| 2028-02-28T20:00:00Z | 23:00 | 26 | 2 | month rollover over a leap day (result depends only on hour arithmetic) |
| 2026-03-28T20:00:00Z | 23:00 | 10 | 1 | no DST: EU last-Sunday-of-March week behaves like any other |
| 2026-10-24T20:00:00Z | 23:00 | 10 | 1 | no DST: EU last-Sunday-of-October week |
| 2026-10-16T07:00:00Z | 10:00 | 218 | 9 | far warehouse |
| any | any | `None` | `None` | missing input |
| any | any | -1 | `None` | invalid |
| any | any | 10**9 | `None` | overflow guard (no exception) |
| naive datetime, or tz-aware non-UTC datetime | - | 5 | `ValueError` | misuse |

Leap-year vectors cannot discriminate anything (the result depends only on hour arithmetic), so one rollover vector is enough. `delivery_hours` for a product is `time1 + time2`; if either is `None` after the size-level fallback, both `delivery_hours` and `delivery_days` are NULL and the raw `time1_h/time2_h` stay as received.

## Pagination Semantics and Run Rules

Validated by a throwaway state machine on 10 scenarios (`top_n = 200`) [VERIFIED: local run]:

| Scenario | Rows stored | Last position | Pages fetched | `is_complete` | Notes |
|----------|-------------|---------------|---------------|---------------|-------|
| 2 x 100 | 200 | 200 | 2 | yes | stops at `top_n`; **no 3rd request** |
| page 2 == page 1 | 100 | 100 | 2 | yes (end: wrap) | wrapped page discarded |
| page 2 empty | 100 | 100 | 2 | yes (end: empty) | |
| 60 then empty / 60 then wrap | 60 | 60 | 2 | yes | short result set: one extra request is the price of correctness |
| page size 48 | 200 | 200 | 5 | yes | cap = ceil(200/48)+1 = 6 not reached |
| duplicate id on page 2 | **199** | **200** | 2 | yes | `duplicates_dropped = 1`; **position 105 (say) is simply absent** |
| page 2 returns 97 items | 200 | 200 | **3** | yes | stopping at a short page would have wrongly reported 197 as complete |
| page 2 fails (429) | 100 | 100 | 1 | no | -> `partial`; failing request not counted in `pages_fetched` |
| page 2 = page 1 reversed (subset) | 100 | 100 | 2 | yes (end: wrap_subset) | fingerprint differs but all ids already seen |

Rules to implement and test:
1. **Position = running slot index** in the concatenated raw arrays (`slot += 1` for every array element including skipped/duplicate ones), truncated at `top_n`. A dropped duplicate leaves a gap in `position`; this keeps positions equal to what the site displays and keeps `promoPosition` comparable. (A5: CONTEXT does not say whether positions are renumbered after dedupe.)
2. Stop on: `slot >= top_n`; empty page; wrap (first-10-id fingerprint equal to page 1's, needs >= 3 ids; **or** every id on the page is already seen); `pages >= ceil(top_n/len(page1)) + 1`. **A short page alone is not an end signal.**
3. `is_complete = (slots_covered >= top_n) or natural_end`. Cap reached without either -> `status='ok'`, `is_complete=0` (e.g. dedupe drops pushed it under `top_n`).
4. `ad_slot_mismatch`: for each promoted item compare `log.promoPosition` against the global slot **and** the page-relative index; count a mismatch only if it equals neither (Page-2 numbering is unverified; raw `ad_promo_position` is always stored so the analysis can be redone). On page 1 both coincide.
5. `normquery` from page 1: `payload["metadata"]["normquery"]`, else `payload["data"]["metadata"]["normquery"]`, else NULL.
6. Per-product: no integer `id` -> skip, log `event=product_skipped`, slot still consumed.
7. Pace with `sleep(delay)` before **every** request except the very first of the run (page 1 -> page 2 included); the sleep is injected. A full run is about 140 requests x 1.5 s = 3.5 min plus latency.
8. Retry budget per request: 5xx/timeout/connection -> 2 retries (sleeps 2 s, 5 s); 429 -> one retry after `min(Retry-After or 30, 60)` s; never retry 403/498/3xx/other 4xx.

### Run status derivation (`finish_run`, counters via SQL aggregate over `run_queries`)

| Condition on the run's `run_queries` | `runs.status` |
|--------------------------------------|---------------|
| every query `ok` | `ok` |
| at least one `ok` or `partial`, not all `ok` | `partial` |
| none `ok`/`partial`, at least one `blocked` or `rate_limited` | `blocked` |
| none `ok`/`partial`, none blocked (all `error`) | `error` |
| unhandled exception in the loop | `error` with `error` text, then re-raise -> exit non-zero (A12: CONTEXT's wording "partial if some were" is read as ok-or-partial) |
| process killed | stays `running` until the next `collect` marks it `abandoned` |

`n_blocked` counts `blocked` **and** `rate_limited` queries (the run table has no `n_rate_limited` column); `n_items = SUM(items_collected)`.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Query-string encoding | manual `quote()`/f-string URLs | `httpx` `params=` | Double-encoding risk; verified exact round trip |
| CSV parsing | `split(",")` / pandas | stdlib `csv.reader` on `io.StringIO(text, newline="")` | Quoted fields, `\r\n`, no NaN coercion |
| Time zones | fixed `+3` offsets | `zoneinfo.ZoneInfo("Europe/Moscow")` + `tzdata` | Correct history, explicit dependency |
| Decimal rounding of kopecks | float division chains | `Decimal(k) / 100` quantized to 0.01 | Deterministic 2 dp |
| Schema versioning | Alembic | `PRAGMA user_version` + list of statements | One schema family, verified race-safe |
| Cross-process mutual exclusion | PID files | `fcntl.flock` | Released by the kernel on death |
| Retry decorators | tenacity | ~25-line explicit loop | Status-specific policy ("never retry a block") reads clearer in code |
| HTTP mocking | respx / fake servers | `httpx.MockTransport` | Locked by CONTEXT, zero dependencies |
| Argument parsing / settings | click, typer, pydantic-settings | argparse + frozen dataclass | Locked by CONTEXT |
| Structured logging | structlog / JSON logger | 25-line `logging.Formatter` for `key=value` | One dependency-free format |

**Key insight:** the only thing worth writing by hand is the WB-specific knowledge (field mapping, classification, pagination), and it all lives in small pure functions that are cheap to test exhaustively.

## Common Pitfalls

### Pitfall 1: `INSERT OR REPLACE` defeats the append-only trigger
**What goes wrong:** A REPLACE conflict deletes the old row without firing `BEFORE DELETE`; history is silently rewritten. **Why:** SQLite fires delete triggers for REPLACE only when `recursive_triggers` is on (verified). **Avoid:** `PRAGMA recursive_triggers=ON` in `connect_rw`; grep test over `src/` for `OR REPLACE` and `REPLACE INTO`; a unit test that attempts the REPLACE through `connect_rw` and expects `IntegrityError`. **Warning sign:** a test passing only because it used a bare `sqlite3.connect`.

### Pitfall 2: Python 3.12+ transaction-control default
**What goes wrong:** manual `BEGIN IMMEDIATE` raises `cannot start a transaction within a transaction` the day the `autocommit` default flips to `False`; `con.commit()` silently does nothing under `autocommit=True`. **Avoid:** the version-dependent connect recipe above and SQL-only `BEGIN/COMMIT/ROLLBACK`.

### Pitfall 3: httpx keeps and replays cookies and reads proxy env vars
**What goes wrong:** the "no cookies, no proxies" decision is violated by httpx defaults. **Avoid:** no-op cookie policy via `client.cookies.jar.set_policy(...)`, `trust_env=False`; behavioural tests (Set-Cookie response then second request has no `cookie` header; client has no mounts when `HTTPS_PROXY` is set).

### Pitfall 4: Short page treated as the end
**What goes wrong:** page sizes vary slightly (48/100 seen across endpoint versions; a 97-item page 2 is plausible after server-side filtering), so a rule "short page = last page" under-collects. **Avoid:** stop only on `top_n`/empty/wrap/cap.

### Pitfall 5: Timeouts can blow the hourly budget (the breaker only counts `blocked`)
**What goes wrong:** if WB hangs, each query costs about 3 x 30 s read timeout + 7 s backoff = about 97 s; 70 queries is about 113 min, longer than the hour. **Avoid:** a run deadline (A7): `WBMON_RUN_DEADLINE` default 2400 s; when exceeded record remaining queries as `error/deadline` without requests. Similarly 70 consecutive 429s with a 60 s wait each would run 70 min, which the breaker should also bound (A3: count `rate_limited` like `blocked` for the consecutive-refusal counter).

### Pitfall 6: Unsafe "abandon stale runs"
**What goes wrong:** a second `collect` abandons a healthy live run. **Avoid:** hold the advisory lock first (see Storage).

### Pitfall 7: SQLite URI built with an f-string
**What goes wrong:** `f"file:{path}?mode=ro"` breaks on `#` or `?` in the path and, worse, **silently opens an empty database** (`no such table`), as verified; the project directory itself contains Cyrillic, spaces and a trailing dot. **Avoid:** `Path.resolve().as_uri() + "?mode=ro"` (verified on a path with Cyrillic, spaces, `#`, `?`).

### Pitfall 8: Editable install / package data on a Cyrillic path
Verified OK on Python 3.11 with hatchling (editable install, `wbmon` console script, `python -m wbmon`, wheel contains `wbmon/data/queries_bundled.txt`). Still include a test that reads the bundled file via `importlib.resources.files("wbmon")`.

### Pitfall 9: Stdout buffering hides logs in Docker
`print()`/logging to a pipe is block-buffered. `sys.stdout.reconfigure(line_buffering=True, encoding="utf-8")` in `cli.main()` (Cyrillic query text in log lines).

### Pitfall 10: Log injection and unbounded log fields
Query text and WB error bodies go into log lines. Render values with `json.dumps(value, ensure_ascii=False)` when they contain whitespace, `=`, `"` or control characters, and truncate bodies to 200 chars.

### Pitfall 11: pytest temp retention of the 0.9 GB perf DB
Default retention keeps the last 3 runs' tmp dirs. Use `tmp_path_retention_policy = "failed"` and an explicit finalizer.

### Pitfall 12: Tests that touch the real DB path or the network
Default `WBMON_DB_PATH` is `./data/wbmon.sqlite3`. The autouse guard (below) chdirs to `tmp_path`, sets `WBMON_DB_PATH` to a tmp file, clears other `WBMON_*` vars and blocks DNS/sockets.

## Code Examples

### Test harness (`tests/conftest.py`)
```python
# [VERIFIED: pytest 9.1.1 + httpx 0.28.1 local run]
import json, os, pathlib, socket, pytest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"

@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                               # default ./data/wbmon.sqlite3 lands in tmp
    for key in [k for k in os.environ if k.startswith("WBMON_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("WBMON_DB_PATH", str(tmp_path / "test.sqlite3"))
    def _blocked(*a, **k): raise RuntimeError("network access is forbidden in tests")
    monkeypatch.setattr(socket, "getaddrinfo", _blocked)      # httpx without a MockTransport now fails loudly
    monkeypatch.setattr(socket.socket, "connect", _blocked)

def test_all_fixtures_are_marked_test_only():
    for f in FIXTURES.glob("*.json"):
        assert json.loads(f.read_text(encoding="utf-8")).get("_test_only") is True, f.name
```
Verified: after the patch a real `httpx.Client().get(...)` raises (`DNS lookup is forbidden in tests`) while `MockTransport` and `sqlite3` keep working.

### `key=value` log line
```python
def fmt_value(v) -> str:
    if v is None: return "-"
    s = str(v)
    return s if s and all(c.isalnum() or c in "-_.:/@+" for c in s) else json.dumps(s, ensure_ascii=False)
# ts=2026-10-16T12:00:03Z level=INFO event=query_done run_id=5 query="постельное бельё" status=ok http=200 pages=2 items=200 ms=3120
```
Events: `run_start`, `queries_loaded` (`source`, `count`, `reason`), `query_done` (one per query), `breaker_open`, `deadline`, `product_skipped`, `slot_exists`, `lock_busy`, `abandoned`, `run_done`.

### Purity and allowlist tests (COL-05, functional core)
```python
import ast, pathlib, tomllib
SRC = pathlib.Path("src/wbmon")
PURE = {"normalize.py", "classify.py", "pager.py", "delivery.py", "clock.py", "models.py"}
FORBIDDEN = {"httpx", "sqlite3", "socket", "os", "time", "subprocess"}

def imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
            for n in ([a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""])}

def test_pure_modules_have_no_io_imports():
    for name in PURE:
        assert not (imports(SRC / name) & FORBIDDEN), name

def test_runtime_dependencies_are_exactly_httpx_and_tzdata():
    deps = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["dependencies"]
    assert {d.split(">")[0].split("<")[0].split("=")[0].strip() for d in deps} == {"httpx", "tzdata"}

def test_third_party_imports_in_src_are_httpx_only():
    third = set()
    for p in SRC.rglob("*.py"):
        third |= {m for m in imports(p) if m not in __import__("sys").stdlib_module_names and m not in {"wbmon", ""}}
    assert third <= {"httpx"}

def test_no_replace_conflict_resolution_in_sql():
    text = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py")).upper()
    assert "OR REPLACE" not in text and "REPLACE INTO" not in text
```
(`sys.stdlib_module_names` exists on 3.10+.) Add behavioural tests for the client: `client.trust_env is False`, and the Set-Cookie non-replay test.

### Reference selectors (condensed, verified against the fixtures above)
```python
def to_int(v):
    if isinstance(v, bool): return None
    if isinstance(v, int): return v
    if isinstance(v, float): return int(v) if v.is_integer() else None
    if isinstance(v, str) and v.strip().lstrip("-").isdigit(): return int(v)
    return None

def rub(kopecks):                                   # kopecks int -> 2 dp float, Decimal-safe
    k = to_int(kopecks)
    return None if k is None else float((Decimal(k) / 100).quantize(Decimal("0.01"), ROUND_HALF_UP))

def pick_size(product):                             # cheapest size with product > 0; ties -> first in array order
    best = None
    for s in product.get("sizes") or []:
        if not isinstance(s, dict) or not isinstance(s.get("price"), dict): continue
        p = to_int(s["price"].get("product"))
        if p is None or p <= 0: continue
        if best is None or p < best[0]: best = (p, s)
    return best[1] if best else None                # audit columns (basic/total/logistics) come from THIS size

def extract_products(payload):                      # None means "no products list": caller classifies schema_drift / products_null
    if not isinstance(payload, dict): return None
    data = payload.get("data")
    if isinstance(data, dict) and "products" in data:
        return data["products"] if isinstance(data["products"], list) else None
    if "products" in payload:
        return payload["products"] if isinstance(payload["products"], list) else None
    return None
```
Legacy fallback applies **only when `pick_size` returns `None`** and `salePriceU` is a positive int: `price_rub = salePriceU/100`, `price_basic_rub = priceU/100`, `price_total_rub = NULL`, `logistics_rub = NULL`. Delivery fallback: product-level `time1`/`time2` first; if either is missing, the size minimising `time1+time2` among sizes having both.

### CLI contract (for Phase 2 and the plans)
- `wbmon init-db [--db PATH]` -> creates parent dir, WAL, `migrate()`; exit 0.
- `wbmon collect [--top-n N] [--limit-queries N] [--trigger manual|scheduler] [--slot ISO] [--db PATH]`: `--trigger scheduler` requires `--slot` (argparse error -> exit 2); `--slot` is normalised to `YYYY-MM-DDTHH:00:00Z`; if a run with that `slot_utc` exists -> log `slot_exists`, **exit 0, no run created** (Phase 2 reads status from the DB); lock held -> `lock_busy`, exit 0; run completed (any statuses) -> 0; config error -> 2; crash -> 1.
- `wbmon probe [--query TEXT]` (default: first bundled query): one page-1 request through the same client + classifier, prints one JSON line `{"outcome","error_class","http_status","server","content_type","products","normquery","shape","elapsed_ms","endpoint","params"}`, writes nothing to any database, exit 0 even when `blocked`.
- Test seam: `cli.main(argv=None, *, _transport=None, _sleep=None)`; the e2e tests pass a `FakeWB` transport (also serves the sheet host) and a recording sleep.

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `sqlite3` implicit transactions (`isolation_level`) | `autocommit` parameter / attribute (PEP 249); legacy remains default but is slated to change | Python 3.12 | Use the explicit connect recipe |
| Unclosed connections silently GC'd | `ResourceWarning` | Python 3.13 | `contextlib.closing` everywhere |
| `datetime.utcnow()` / default sqlite datetime adapters | Deprecated (adapters deprecated in 3.12) | 3.12 | Store ISO-8601 `Z` strings explicitly |
| `search.wb.ru` `data.products` envelope | v18 on `u-search.wb.ru` returns top-level `products`/`total` | 2025-2026 per parser code | Accept both |
| top-level `priceU/salePriceU` | `sizes[].price{basic,product,total,logistics,return}` | v5+ | Legacy only as fallback |

**Deprecated/outdated:** `respx`, `pydantic`, jittered pacing, 5-failure breaker (STACK.md text) - superseded by CONTEXT.md. v18 endpoints are still in use by repositories pushed as recently as 2026-10-16 (`maxexplorer/ParserProject`) and 2026-10-13 (`ru-marketplace-mcp`), which is only weak evidence that the endpoint is current. [CITED: GitHub repo metadata]

## Plan-Split Suggestion (research item 5)

Four plans, three waves. Plans in the same wave touch **disjoint files**; their only coupling is the contract frozen in plan 01 (`models.py`, `config.py`, `clock.py`, `log.py`).

| Plan | Wave | Title | Files (owned) | Requirements | Depends on |
|------|------|-------|---------------|--------------|------------|
| 01-01 | 1 | Foundation, contracts, delivery, bundled queries, test harness | `pyproject.toml`, `.gitignore`, `src/wbmon/{__init__,__main__,config,clock,models,log,delivery}.py`, `src/wbmon/data/queries_bundled.txt`, `tests/conftest.py`, `tests/test_{config,clock,delivery,log,bundled_queries,no_circumvention,import_boundaries}.py` | DATA-04, QRY-03 (file + count/special-char tests), SCHED-04 (formatter), OPS-04 (harness), COL-05 (allowlist test) | - |
| 01-02 | 2 | Storage: schema, migrations, writer, reader, lock, perf | `src/wbmon/storage/*`, `tests/test_storage_*.py`, `tests/test_storage_perf.py` (slow) | STOR-01, STOR-02, STOR-03, STOR-04, QRY-04, DATA-01 (columns), COL-06 (columns) | 01-01 |
| 01-03 | 2 | WB protocol core: fixtures, normalize, classify, pager, client, query source | `src/wbmon/{normalize,classify,pager,wb_client,queries}.py`, `tests/factories.py`, `tests/fixtures/*`, `tests/test_{normalize,classify,pager,wb_client,queries}.py` | QRY-01, QRY-02, COL-01, COL-02, COL-03, COL-05, DATA-01, DATA-02, DATA-03, DATA-05, DATA-06, DATA-07 | 01-01 |
| 01-04 | 3 | Orchestrator, CLI, end-to-end | `src/wbmon/{orchestrator,cli}.py`, `tests/test_{orchestrator,cli,e2e}.py` | COL-04, COL-06, SCHED-01, SCHED-04, STOR-02/03 end-to-end, QRY-04 end-to-end, OPS-04 | 01-02, 01-03 |

Contract to freeze in 01-01 `models.py` (so 02 and 03 can run in parallel without coordination):
```python
@dataclass(frozen=True, slots=True)
class ObservationRow:   # field names == observations columns minus id/run_id/query_id
    collected_at: str; position: int; nm_id: int; name: str | None; brand: str | None
    price_rub: float | None; price_basic_rub: float | None; price_total_rub: float | None; logistics_rub: float | None
    rating: float | None; feedbacks: int | None; nm_rating: float | None; nm_feedbacks: int | None
    time1_h: int | None; time2_h: int | None; delivery_hours: int | None; delivery_days: int | None
    seller_name: str | None; seller_id: int | None; is_promoted: int
    ad_promo_position: int | None; ad_organic_position: int | None; ad_tp: str | None; ad_cpm: int | None
    total_quantity: int | None

@dataclass(frozen=True, slots=True)
class QueryResult:      # everything run_queries needs + the rows to store
    status: QueryStatus; error_class: str | None; http_status: int | None; pages_fetched: int
    is_complete: bool; normquery: str | None; ad_slot_mismatch: int; duplicates_dropped: int
    error_message: str | None; started_at: str; duration_ms: int; observations: tuple[ObservationRow, ...]
# QueryStatus = Literal["ok","partial","blocked","rate_limited","error"]; Outcome / ErrorClass constants live here too.
```
`Writer` API consumed by 01-04: `upsert_queries(texts, now) -> dict[text, id]`, `start_run(...) -> run_id`, `record_query(run_id, query_id, QueryResult)`, `finish_run(run_id, now)`, `abandon_stale_runs(now) -> int`. `WbClient`/`Pager` API consumed by 01-04: `collect_query(query, top_n) -> QueryResult`.

Task ordering inside plans: 01-01: skeleton -> clock/models/config -> delivery (TDD with the vector table) -> log -> bundled queries + tests -> harness guards. 01-02: DDL + migrate -> connect/ensure_wal/lock -> writer (atomicity, abandon, REPLACE test) -> reader + perf generator. 01-03: fixtures + factories -> normalize (TDD with the expected-values table) -> classify + retry-after -> pager (10 scenarios) -> client (cookie/proxy/pacing/retry matrix) -> queries source. 01-04: orchestrator (breaker, deadline, status derivation, isolation incl. a writer failure) -> CLI -> e2e (2 queries x 2 pages -> DB asserts; all-403 run -> 3 requests then `circuit_open`; stale `running` -> `abandoned`; second collect appends and never rewrites).

If 01-03 is judged too large, split it into 01-03a (fixtures, normalize, classify, pager: pure) and 01-03b (wb_client, queries: depends on 03a) - this adds a wave and pushes 01-04 to wave 4.

Human checkpoint: one batched `checkpoint:human-verify` before the first install (ruff, hatchling; see Package Legitimacy Audit). The bundled-queries task needs no network: the PDF appendix is at `/Users/fvst/Desktop/Тестовое задание./TZ_Wildberries_short_2026.pdf` (pypdf is installed in `.venv`); the 70 queries are the numbered list `1.`-`70.` after the heading «Приложение. Поисковые запросы Wildberries» on pages 2-4 (a naive `^\d+\. ` regex over the whole text also matches the six section headings `1. Цель` ... `6. Критерии оценки`, so restrict it to text after that heading); an optional one-off dev comparison against the Google Sheet CSV is allowed (it is not Wildberries) but must not be part of the test suite.

## Query Test Vectors (from the real list, extracted from the PDF appendix)

70 distinct queries, none with leading/trailing/double spaces, none NFD, max length 60. Characters that exercise encoding: #13 and #67 `ё`; #19 `dr. hygge` (Latin + period); #21 and #41 `100%`; #37 `200*220`; #53, #62, #65, #70 `1,5`; #2, #11, #12, #18, #30, #51 `1.5`; #33, #56, #57 Cyrillic `х`; #66 `160x200` Latin `x`; typo `пастельный` in #14 and #53. The bundled-file test asserts: 70 lines, all distinct, no blank line, file bytes decode as UTF-8 without BOM, and exact equality for these special queries. The wire test iterates all 70.

## Runtime State Inventory

Not applicable: greenfield phase (no rename/refactor/migration).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | WB's search endpoint decodes `+` in `query=` as a space (httpx encodes spaces as `+`) | httpx Specifics | If treated literally, every multi-word query would search a different string. Public `requests`-based parsers send the same encoding to v18, so the risk is low, but it cannot be verified offline; Phase 4's live probe should compare `metadata.normquery` for a two-word query. If wrong, switch to `urllib.parse.urlencode(..., quote_via=quote)` and build the URL once (tests unchanged) |
| A2 | Any 3xx from the WB API is a refusal -> `blocked/redirect` and counts toward the breaker | Classification | A legitimate redirect would be mislabelled blocked; harmless (still recorded honestly), and the next run retries |
| A3 | The consecutive-refusal counter treats `rate_limited` like `blocked` (CONTEXT says only `blocked`) | Pitfall 5 | If rejected, a 429 storm costs up to 70 x 60 s; the deadline (A7) bounds it |
| A4 | Default wait when `Retry-After` is absent/unparseable is 30 s (cap 60) | Retry-After parser | Slightly shorter/longer pause; no data impact |
| A5 | `position` stays the raw slot index; a dropped duplicate leaves a gap | Pagination Semantics | If the planner prefers renumbering, `ad_slot_mismatch` and the UI "position == site position" claim need adjusting |
| A6 | `ad_slot_mismatch` counts only when `promoPosition` equals neither the global slot nor the page-relative index | Pagination Semantics | If page 2 numbering is something else the counter is merely noisy; raw values are stored |
| A7 | A run deadline (`WBMON_RUN_DEADLINE`, default 2400 s) is added although CONTEXT lists no such setting | Pitfall 5 | Without it a hanging WB can push a run past one hour; adding it needs one extra env var and one `error_class='deadline'` |
| A8 | Phase 1 owns `storage/lock.py` (flock) although SCHED-02 assigns the lock to Phase 2 | Storage | If deferred, "abandon stale runs" can mark a live concurrent run abandoned |
| A9 | v18 top-level-`products` envelope contains `metadata` with `normquery` like variant A | Fixture Design | `normquery` would be NULL for that shape; no data loss |
| A10 | Legacy fallback fills only `price_rub` and `price_basic_rub` (total/logistics NULL) | Fixture Design | Audit columns incomplete for legacy-shape responses; avoids mixing field semantics |
| A11 | `+<repo url>` in the User-Agent: the repository URL is not yet known; use a single `REPO_URL` constant (placeholder until Phase 4 publishes) | Standard Stack | UA shows a placeholder; fix in one place |
| A12 | Run-status mapping reads CONTEXT's "partial if some were [ok]" as "ok or partial" | Run status table | A run with only `partial` queries would otherwise be labelled `blocked` |
| A13 | Disk growth is about 0.9 GB/week (measured with synthetic 70-character names) | STOR-04 | Real names are probably similar; Phase 2/4 should size the volume with margin |

## Open Questions

1. **Schema-freeze completeness: `subject_id` is named in Area 2 but missing from the Area 4 schema.**
   - What we know: Area 2 #4 says store `subject_id`; the observations column list does not contain it. Other non-backfillable fields seen in real payloads: `root` (imt id), `wh`, `dtype`, `dist`.
   - What's unclear: whether to add them to migration 1 (cheap now, impossible to backfill later).
   - Recommendation: add `subject_id INTEGER` (explicitly requested) and `root_id INTEGER` to `observations` in migration 1; leave `wh/dtype/dist` out (meaning unknown) - the planner should decide; a later column is a forward-only migration but old rows stay NULL.

2. **Slot-exists and lock-busy exit codes.** Recommended: both exit 0 with a log event, no run created (Phase 2 reads the DB for status). Confirm with the Phase 2 plan.

3. **Should the breaker count `rate_limited`?** (A3) Recommended yes; CONTEXT is silent beyond `blocked`.

4. **Page-2 `promoPosition` numbering** is unverified (no recorded page-2 payload found); handled by storing the raw value and the tolerant mismatch rule; confirm with the first real run (Phase 4).

5. **`run_queries` timestamps:** CONTEXT's `started_at` is the first-request time of the query; `observations.collected_at` is per page. No `finished_at` column exists (duration_ms covers it).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.14 (venv) | dev + tests | yes | 3.14.6 (SQLite 3.50.4) | - |
| Python 3.11 / 3.12 | cross-version check | yes | 3.11.15, 3.12.13 at `~/.local/bin` (SQLite 3.53.1) | - |
| httpx / tzdata / pytest in `.venv` | runtime + tests | yes | 0.28.1 / 2026.5 / 9.1.1 | - |
| ruff | lint | no | latest on PyPI 0.17.1 | install via `pip install -e ".[dev]"` (after the human-verify checkpoint) |
| hatchling | build | no (fetched by pip on demand) | 1.32.4 | setuptools (equally absent) |
| PyPI network access | installs | yes | verified with `pip index versions` | - |
| System tz database | zoneinfo | yes | `/usr/share/zoneinfo/Europe/Moscow` (plus `tzdata` wheel) | `tzdata` wheel |
| uv | - | no | - | not needed (pip) |
| Docker | Phase 2 only | no | - | not needed in Phase 1 |
| git / gh | commits | yes | 2.50.1 / logged in | - |
| Free disk | perf test | yes | about 29 GB | test deletes its DB |

**Missing dependencies with no fallback:** none.
**Missing dependencies with fallback:** ruff and hatchling are installable from PyPI.

*Validation Architecture section intentionally omitted: `workflow.nyquist_validation` is `false` in `.planning/config.json`. Test mapping is given per requirement in the Phase Requirements table and in the plan split.*

## Security Domain

`security_enforcement` is enabled (ASVS level 1, block on high).

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | no accounts; the collector makes anonymous requests |
| V3 Session Management | no | cookies deliberately disabled (no-op policy) |
| V4 Access Control | partial | DB file permissions are the only control; read-only open path for readers (`mode=ro` + `query_only`) |
| V5 Input Validation | **yes** | WB JSON and the sheet CSV are untrusted: type-coercing helpers (`to_int`, `to_float`), skip malformed products, parameterised SQL only, body size sanity, scheme check on `WBMON_SEARCH_URL`/`SHEET_CSV_URL` (`http`/`https` only) |
| V6 Cryptography | no | TLS verification stays at httpx defaults (never `verify=False`); no custom crypto |
| V7 Error handling & logging | **yes** | log only status, `server` header, <= 200-char body prefix; never log cookies/headers wholesale; JSON-escape values (log injection) |
| V10 Malicious code / supply chain | **yes** | runtime dependencies pinned to `httpx`, `tzdata`; allowlist tests; COL-05 enforcement |
| V14 Configuration | yes | all settings from `WBMON_*` env, no secrets in the repo; `.env` gitignored |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| SQL injection via query text / WB strings | Tampering | placeholders for all values; only `IN (?,?,...)` lists and the integer in `PRAGMA user_version` are interpolated; test with a query containing `'; DROP TABLE observations;--` |
| Log injection via newline/control characters in query text or error bodies | Tampering/Repudiation | `json.dumps` quoting, truncation, control-char replacement |
| History tampering by application bug (UPDATE/DELETE/REPLACE) | Tampering | triggers + `recursive_triggers=ON` + source-grep test |
| Fabricated data in the real DB | Tampering | tests only on `tmp_path` DBs, autouse env/cwd isolation, `_test_only` fixture marker, no seed/demo code path |
| Hostile or compromised endpoint returning a huge body | Denial of service | httpx timeouts; reject bodies over a few MB (`len(response.content)`); sheet size/rows plausibility (50-500 rows) |
| SSRF through `WBMON_SEARCH_URL` / `SHEET_CSV_URL` | Spoofing | operator-controlled env only; scheme check; sheet client allows redirects (max 5) but only the sheet fetch |
| Ambient proxy env silently routing traffic | Information disclosure/Spoofing | `trust_env=False` |
| Cookie capture and replay of edge tokens | Spoofing | no-op cookie policy (COL-05) |
| Formula injection when product names are exported to CSV | Tampering | Phase 3 concern (UI-08): prefix cells starting with `= + - @`; noted here because Phase 1 stores names unmodified (correct for the store) |

## Sources

### Primary (HIGH confidence)
- Local experiments (scratch scripts outside the project, Python 3.11.15 / 3.12.13 / 3.14.6, httpx 0.28.1, pytest 9.1.1): SQLite triggers/REPLACE/UPSERT/CHECK/FK behaviour, `autocommit` semantics, `user_version` migration race, 2.35M-row performance, URI encoding, MockTransport patterns, cookie/proxy behaviour, Retry-After parsing, delivery vectors, pagination state machine, editable install and wheel contents from a Cyrillic path.
- https://docs.python.org/3/library/sqlite3.html - `autocommit` parameter/attribute, `isolation_level`, `commit`/`rollback`/`close`, 3.13 `ResourceWarning`, `executescript` behaviour.
- PyPI JSON API (queried 2026-10-16): httpx, tzdata, pytest, ruff, hatchling versions and first-release dates.
- `gsd-tools query package-legitimacy check` (all SUS on heuristics, see audit).
- `/Users/fvst/Desktop/Тестовое задание./TZ_Wildberries_short_2026.pdf` - the 70 queries (appendix).

### Secondary (MEDIUM confidence)
- Recorded WB payloads (GitHub; shape facts recomputed locally): https://github.com/Unti1/T-skRAZRAB0T/blob/HEAD/products.json , https://github.com/mishannn/wb-goods-feed/blob/HEAD/internal/integrations/wildberries/products_response_fixture.json , https://github.com/ibrohim-Fazliddinov/WB-Analyze/blob/HEAD/json.json , https://github.com/gldkizz/programmingPetProject/blob/HEAD/python/Parsing/wb_catalogs_data.json , https://github.com/ReptiloidAnunak/wildberries_pars_app/blob/HEAD/test.json
- v18 top-level `products`/`total` and `params=` encoding usage: https://github.com/maxexplorer/ParserProject/blob/HEAD/wb_prices_analytics/main.py (only the request-parameter and response-key usage was read)
- Pagination wrap evidence, fingerprint idea, self-referential 307 remark: https://github.com/Vladimir-Human/ru-marketplace-mcp (server.py comments; nothing about client-identity workarounds was used)
- Project research: `.planning/research/{SUMMARY,PITFALLS,ARCHITECTURE,STACK}.md`

### Tertiary (LOW confidence)
- Endpoint currency inferred from repo push dates only (no live access).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - versions verified, toolchain exercised on this machine.
- Architecture / SQLite / httpx / delivery / pagination: HIGH - each recommendation was executed on three Python versions.
- Fixture shapes: MEDIUM-HIGH for variant A and legacy (five recorded payloads), MEDIUM for variant B (parser code only).
- WB field semantics (price = `product`, merged `feedbacks`, delivery = `time1+time2`): MEDIUM/LOW - unchanged from project research; raw inputs are stored so they can be recalibrated.
- Pitfalls: HIGH for items found by experiment (REPLACE, cookies, URI, autocommit), MEDIUM for timing estimates.

**Research date:** 2026-10-16
**Valid until:** 2026-11-15 for stack and tooling; WB payload facts should be re-checked at the Phase 4 live probe.

**Housekeeping note:** two empty directories remain in the system temp folder (`.../T/tmpqked_lz_`, `.../T/tmpgxbhcgml`) from the performance experiment; their 860 MB database files were deleted, the empty directories could not be removed by the sandbox safety check.
