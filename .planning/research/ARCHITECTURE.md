# Architecture Research

**Domain:** Scheduled SERP scrape-and-track service (Wildberries search positions): hourly collector -> SQLite history -> Streamlit UI, one VPS, Docker Compose
**Researched:** 2026-10-16
**Confidence:** HIGH for structure, schema and deployment (standard patterns, SQLite WAL facts checked against sqlite.org/wal.html). MEDIUM for WB response-shape details (taken from PROJECT.md, not re-verified live: WB is blocking the dev machine). All WB-field choices below are isolated in one module (`wb/normalize.py`) so they can be corrected without touching anything else.

## Standard Architecture

Scrape-and-track systems almost always collapse to the same shape: a **functional core** (pure parsing/normalising/classifying) inside an **imperative shell** (HTTP, DB, clock, scheduler), a **single writer** to an append-only store, and **read-only consumers** that never call back into the writer. The one non-negotiable property for this project is that every "unknown" is represented explicitly (blocked / error / partial / missing run) so the UI never has to guess and never invents a position.

### System Overview

```
              ┌──────────────────────── collector container ───────────────────────┐
              │                                                                    │
  Google      │  ┌─────────────┐   list[str]   ┌──────────────────────────────┐    │
  Sheet CSV ──┼─▶│ QuerySource │──────────────▶│        RunOrchestrator        │    │
  (+ bundled  │  │ sheet|file  │               │ one run = all queries;        │    │
   fallback)  │  └─────────────┘               │ per-query isolation, breaker  │    │
              │                                └───┬──────────────┬────────────┘    │
              │   ┌───────────────┐  PageResult    │              │ QueryResult     │
  WB search ◀─┼───│ WbSearchClient│◀───────────────┘              ▼ (1 txn/query)   │
  JSON API    │   │ httpx, rate   │                         ┌───────────────┐       │
  (plain)     │   │ limit, retry, │──raw JSON──▶ normalize  │ StorageWriter │       │
              │   │ classify      │          (pure fns)────▶│ (only writer) │       │
              │   └───────────────┘                         └──────┬────────┘       │
              │  ┌───────────┐ calls run_once(slot)                │                │
              │  │ Scheduler │─────────────▶ RunOrchestrator       │                │
              │  │ hourly MSK│  flock: no overlap                  │                │
              │  └───────────┘                                     │                │
              └────────────────────────────────────────────────────┼────────────────┘
                                                                   ▼
                                        ┌───────────────────────────────────────┐
                                        │  /data/wb.db  (SQLite, WAL)           │
                                        │  queries · runs · run_queries ·       │
                                        │  products · observations (append-only)│
                                        │  named Docker volume `wbdata`         │
                                        └───────────────────┬───────────────────┘
                                                            │ mode=ro + query_only
              ┌─────────────────────── web container ───────┼─────────────────────┐
              │  ┌──────────────┐   DataFrames   ┌──────────┴───┐                  │
  Browser ◀───┼──│ Streamlit UI │◀───────────────│ StorageReader│                  │
  (Plotly)    │  │ + series.py  │ st.cache_data  │ (short-lived │                  │
              │  │ (pure gaps)  │ TTL            │  connections)│                  │
              │  └──────────────┘                └──────────────┘                  │
              └────────────────────────────────────────────────────────────────────┘
```

Direction of data: **WB/Sheet -> client -> normalizer -> orchestrator -> writer -> SQLite -> reader -> UI**. Nothing flows backwards; the web container cannot trigger collection and the collector knows nothing about the UI. The only shared thing is the database file (plus `/data/heartbeat`, `/data/collector.lock` for ops).

### Component Responsibilities

| Component | Responsibility (owns) | Does NOT do | Implementation |
|-----------|----------------------|-------------|----------------|
| `config` | Typed settings from env (DB path, top_n, dest, URLs, intervals, TZ). One frozen object passed down. | I/O | frozen `dataclass` + `Settings.from_env(mapping)`; no pydantic needed (keeps the web image light, trivially testable by passing a dict) |
| `clock` | `utc_now()`, `to_iso(dt)` (`YYYY-MM-DDTHH:MM:SSZ`), `floor_to_hour(dt, tz)`, `next_slot(now, tz)`, MSK display helpers | anything stateful | pure functions + injectable `now` |
| `QuerySource` | Produce the ordered list of query strings, byte-exact; report `source = sheet|fallback`; log diff vs bundled | persist queries | `httpx.get(sheet_csv_url)` -> `csv.reader` on text decoded `utf-8-sig`; fallback = package-data file `wbmon/data/queries_fallback.txt` |
| `WbSearchClient` | One `fetch_page(query, page) -> PageResult`: global rate limit, retry/backoff, timeouts, **classification** of outcome. Pagination loop lives in `fetch_query()` | parse product fields; write DB; decide run status | `httpx.Client` (HTTP/1.1 default, ordinary headers), injectable `transport`, `sleep`, `monotonic` |
| `classify` | `classify(status_code, headers, body_ok) -> ok / blocked / retryable / error` | I/O | pure; 403, 498, 429 -> `blocked`; 5xx, timeout, transport error -> `retryable`; other 4xx, bad JSON -> `error` |
| `normalize` | raw product dict -> `ObservationRow`; `extract_products(payload)` (accepts `data.products` and top-level `products`); `price_rub(kopecks)`; `delivery_days(collected_at, hours, tz)`; nullable everywhere | I/O, time-of-day (receives `collected_at`) | pure functions + frozen dataclasses; the only module that knows WB field names |
| `StorageWriter` | Schema migrate, `start_run`, `record_query_result` (single transaction: run_queries + products upsert + observations), `finish_run`, `abandon_stale_runs` | HTTP, parsing | stdlib `sqlite3`, explicit `BEGIN IMMEDIATE`, `executemany` |
| `StorageReader` | Query functions the UI needs, returning DataFrames/rows; opens `file:...?mode=ro` + `PRAGMA query_only=ON` | writes | one short-lived connection per call |
| `RunOrchestrator` | `run_once(slot, trigger)`: load queries, upsert them, loop queries with isolation, circuit breaker, derive run status, never raise out of the loop | scheduling, overlap locking (caller's job) | plain class taking `client`, `writer`, `query_source`, `clock`, `settings` (dependency injection by constructor, no framework) |
| `Scheduler` | Stateless hourly loop: "is there a run for the current slot? if not and still within grace, run"; heartbeat file; SIGTERM handling | collecting | ~40 lines + `fcntl.flock` guard; no APScheduler (see Alternatives) |
| `ui` | Streamlit page, Plotly figures, Russian copy; `series.py` is a pure module that builds gap-aware series | DB writes, WB calls, scheduling | Streamlit + Plotly; only imports `storage.reader`, `series`, `clock` |

## Recommended Project Structure

```
wb-serp-monitor/
├── pyproject.toml                  # [project.scripts] wbmon = "wbmon.cli:main"; extras: ui, dev
├── Dockerfile                      # one image, python:3.12-slim, two commands
├── compose.yaml
├── .env.example
├── README.md                       # Russian: run instructions + write-up
├── src/wbmon/
│   ├── __init__.py                 # __version__ (goes into runs.app_version)
│   ├── config.py                   # Settings dataclass
│   ├── clock.py                    # UTC/ISO/MSK helpers, slot math (pure)
│   ├── models.py                   # ObservationRow, PageResult, QueryResult, enums (no I/O)
│   ├── cli.py                      # init-db | collect once | scheduler | healthcheck | backup
│   ├── data/queries_fallback.txt   # the 70 queries, byte-exact, package data
│   ├── queries/source.py           # QuerySource (sheet + fallback + diff log)
│   ├── wb/
│   │   ├── client.py               # WbSearchClient: pacing, retry, pagination
│   │   ├── classify.py             # status -> ok/blocked/retryable/error (pure)
│   │   └── normalize.py            # raw JSON -> ObservationRow (pure)
│   ├── storage/
│   │   ├── db.py                   # connect_rw(), connect_ro(), PRAGMAs
│   │   ├── migrations/0001_init.sql
│   │   ├── migrate.py              # PRAGMA user_version runner
│   │   ├── writer.py
│   │   └── reader.py
│   ├── collector/
│   │   ├── runner.py               # RunOrchestrator
│   │   ├── scheduler.py            # slot loop, heartbeat, signals
│   │   └── lock.py                 # flock context manager
│   └── ui/
│       ├── app.py                  # Streamlit entry (thin)
│       ├── series.py               # axis + state classification (pure, no streamlit import)
│       └── charts.py               # Plotly figure builders (pure given DataFrames)
└── tests/
    ├── fixtures/                   # hand-made WB-shaped JSON for TESTS ONLY
    ├── test_normalize.py  test_classify.py  test_client.py
    ├── test_storage.py    test_runner.py    test_scheduler.py
    ├── test_series.py     test_ui_smoke.py  # streamlit.testing.v1.AppTest
    └── test_import_boundaries.py            # ui !-> collector/wb; wb.normalize !-> httpx/sqlite3
```

### Structure Rationale

- **src layout + one installable package:** `pip install -e .` in dev, `pip install .` in Docker; both containers use the *same image* and differ only in `command`, so the schema and the reader/writer code can never drift apart.
- **`wb/` vs `storage/` vs `collector/` vs `ui/`:** dependency arrows go `ui -> storage.reader`, `collector -> wb, storage.writer, queries`, `wb/normalize -> models`. Nothing imports `ui`; `ui` never imports `wb` or `collector`. `test_import_boundaries.py` makes this a CI-checked rule rather than a convention.
- **`models.py` has no I/O:** it is the vocabulary shared by normalizer, orchestrator and writer.
- **`ui/series.py` free of Streamlit:** the gap logic (the most "judged" behaviour) is unit-testable without a browser.

## Database Schema (DDL)

`storage/migrations/0001_init.sql`. Applied by `migrate.py` when `PRAGMA user_version = 0`, then `user_version = 1`. Migrations are forward-only and never destructive.

```sql
-- Connection PRAGMAs (set in db.py, not in the migration):
--   writer: journal_mode=WAL (persistent), synchronous=NORMAL, foreign_keys=ON, busy_timeout=5000
--   reader: mode=ro URI, query_only=ON, busy_timeout=5000
-- Timestamps: TEXT, UTC, 'YYYY-MM-DDTHH:MM:SSZ' (lexicographic order == chronological order).

CREATE TABLE queries (
  id            INTEGER PRIMARY KEY,
  text          TEXT    NOT NULL UNIQUE,      -- exact Sheet wording; BINARY collation => Cyrillic 'х' != Latin 'x'
  sort_order    INTEGER NOT NULL,             -- row order in the Sheet
  is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),  -- dropped from Sheet => 0, history kept
  first_seen_at TEXT    NOT NULL,
  last_seen_at  TEXT    NOT NULL
);

CREATE TABLE runs (
  id            INTEGER PRIMARY KEY,
  scheduled_for TEXT    NOT NULL,             -- hour slot this run represents (== started_at for manual)
  trigger       TEXT    NOT NULL CHECK (trigger IN ('scheduler','catchup','manual')),
  started_at    TEXT    NOT NULL,
  finished_at   TEXT,                         -- NULL while running OR if the process died
  status        TEXT    NOT NULL CHECK (status IN
                  ('running','ok','partial','blocked','failed','abandoned')),
  query_source  TEXT    NOT NULL CHECK (query_source IN ('sheet','fallback')),
  top_n         INTEGER NOT NULL,
  dest          INTEGER NOT NULL,             -- WB region param (Moscow)
  queries_total   INTEGER NOT NULL,
  queries_ok      INTEGER NOT NULL DEFAULT 0,
  queries_partial INTEGER NOT NULL DEFAULT 0,
  queries_blocked INTEGER NOT NULL DEFAULT 0,
  queries_error   INTEGER NOT NULL DEFAULT 0,
  app_version   TEXT,
  note          TEXT
);
-- Idempotency of scheduling: at most ONE scheduler/catch-up run per hour slot. Manual runs always append.
CREATE UNIQUE INDEX ux_runs_slot    ON runs(scheduled_for) WHERE trigger IN ('scheduler','catchup');
CREATE INDEX        ix_runs_started ON runs(started_at);

-- Exactly one final outcome per (run, query). Written in the same transaction as that query's observations.
CREATE TABLE run_queries (
  run_id            INTEGER NOT NULL REFERENCES runs(id),
  query_id          INTEGER NOT NULL REFERENCES queries(id),
  status            TEXT    NOT NULL CHECK (status IN ('ok','partial','blocked','error')),
  collected_at      TEXT    NOT NULL,         -- when the first page of this query was received/attempted
  finished_at       TEXT    NOT NULL,
  http_status       INTEGER,                  -- last HTTP status seen (NULL: transport error / breaker skip)
  pages_fetched     INTEGER NOT NULL DEFAULT 0,
  items_count       INTEGER NOT NULL DEFAULT 0,
  covered_positions INTEGER NOT NULL DEFAULT 0, -- positions 1..N were fully observed (top_n if complete)
  attempts          INTEGER NOT NULL DEFAULT 1, -- HTTP attempts incl. retries (diagnostics)
  error_class       TEXT,                     -- 'blocked_403','blocked_498','blocked_429','http_5xx','timeout',
                                              -- 'bad_json','empty_result','circuit_open', ...
  error_message     TEXT,
  PRIMARY KEY (run_id, query_id),
  CHECK (status <> 'ok' OR items_count > 0)   -- a 200 with zero products is an 'error: empty_result', never 'ok'
) WITHOUT ROWID;
CREATE INDEX ix_rq_query ON run_queries(query_id, collected_at);

-- Descriptive, slowly-changing attributes (last known). NOT a time series; the time series lives in observations.
CREATE TABLE products (
  nm_id         INTEGER PRIMARY KEY,
  name          TEXT,
  brand         TEXT,
  root_id       INTEGER,
  seller_id     INTEGER,
  first_seen_at TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

CREATE TABLE observations (
  run_id         INTEGER NOT NULL,
  query_id       INTEGER NOT NULL,
  position       INTEGER NOT NULL CHECK (position >= 1),   -- 1-based running index in displayed order
  nm_id          INTEGER NOT NULL REFERENCES products(nm_id),
  collected_at   TEXT    NOT NULL,                         -- == run_queries.collected_at (denormalised for export)
  price_rub      REAL,                                     -- buyer price, kopecks/100; NULL if absent
  rating         REAL,
  feedbacks      INTEGER,
  delivery_hours INTEGER,                                  -- time1+time2 as received; lets delivery_days be recomputed
  delivery_days  INTEGER,                                  -- calendar days (MSK): today=0, tomorrow=1; NULL if unknown
  seller_name    TEXT,
  is_promoted    INTEGER CHECK (is_promoted IN (0,1)),     -- 1 when the item carries WB's `log` ad block
  extras         TEXT CHECK (extras IS NULL OR json_valid(extras)),  -- small JSON: log{...}, supplierRating, time1/time2, wh, dtype, ...
  PRIMARY KEY (run_id, query_id, position),
  FOREIGN KEY (run_id, query_id) REFERENCES run_queries(run_id, query_id)
) WITHOUT ROWID;

-- (a) nm_ids ever seen for a query, (b) positions of chosen nm_ids over time: both are covered by this index
CREATE INDEX ix_obs_query_nm ON observations(query_id, nm_id, run_id, position);

-- History is immutable at the database level (not just by convention):
CREATE TRIGGER observations_no_update BEFORE UPDATE ON observations
BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;
CREATE TRIGGER observations_no_delete BEFORE DELETE ON observations
BEGIN SELECT RAISE(ABORT, 'observations is append-only'); END;

-- Convenience for reviewers / CSV export (TZ lists "query text" per row)
CREATE VIEW v_observations AS
SELECT o.collected_at, q.text AS query, o.position, o.nm_id, o.price_rub, o.rating, o.feedbacks,
       o.delivery_days, o.seller_name, o.is_promoted, p.name, p.brand, o.run_id
FROM observations o JOIN queries q ON q.id = o.query_id JOIN products p ON p.nm_id = o.nm_id;
```

### The five access paths and what serves them

| Need | SQL shape | Served by |
|------|-----------|-----------|
| List queries | `SELECT id, text FROM queries WHERE is_active=1 ORDER BY sort_order` (+ inactive with history in a toggle) | table scan, 70 rows |
| nm_ids ever seen for a query | `SELECT nm_id, COUNT(*) n, MIN(position) best, MAX(collected_at) last_seen FROM observations WHERE query_id=? GROUP BY nm_id` | covering `ix_obs_query_nm` |
| Positions of chosen nm_ids over time | `SELECT run_id, nm_id, MIN(position) AS position FROM observations WHERE query_id=? AND nm_id IN (...) GROUP BY run_id, nm_id` (MIN handles the rare nm_id appearing as both ad and organic slot in one SERP) | covering `ix_obs_query_nm` |
| Time axis for a query (see "Gaps") | `SELECT r.id run_id, COALESCE(rq.collected_at, r.started_at) ts, r.status run_status, rq.status q_status, rq.covered_positions, rq.error_class, rq.http_status FROM runs r LEFT JOIN run_queries rq ON rq.run_id=r.id AND rq.query_id=? ORDER BY ts` | `runs` has ~720 rows/month; trivial |
| Latest characteristics | `SELECT o.* FROM observations o JOIN (SELECT nm_id, MAX(run_id) run_id FROM observations WHERE query_id=? AND nm_id IN (...) GROUP BY nm_id) l ON o.run_id=l.run_id AND o.query_id=? AND o.nm_id=l.nm_id JOIN products ...` | `ix_obs_query_nm` + PK lookup |
| Run log | `SELECT * FROM runs ORDER BY started_at DESC LIMIT 100`; per-run detail from `run_queries` by PK prefix | `ix_runs_started`, PK |

Volume check: 70 x 200 x 24 = 336 000 rows/day, roughly 80-100 MB/day including the index, a few GB per month. Fine for SQLite on one VPS; no retention job needed for the assignment.

### Uniqueness / idempotency rules (the "append-only, crash-visible" contract)

1. **`observations` is insert-only** (triggers forbid UPDATE/DELETE). Re-running collection can only add rows with a new `run_id`.
2. **`(run_id, query_id)` is unique in `run_queries`; `(run_id, query_id, position)` unique in `observations`.** Retries inside a run happen *before* the write (in memory), so a query is written exactly once per run.
3. **One transaction per query** (`BEGIN IMMEDIATE`: upsert products -> insert run_queries -> insert observations -> COMMIT). A crash mid-query leaves *no* partial observations; queries finished before the crash stay committed. Do **not** wrap the whole run in one transaction (a crash would lose 60 good queries).
4. **A run row is inserted first with `status='running'`, `finished_at=NULL`.** `finish_run` sets the final status. If the process dies, the row stays `running`. At scheduler/`collect once` startup (after acquiring the lock, so no live run can exist) `abandon_stale_runs()` flips every leftover `running` row to `abandoned` with a note. So "crashed run" is always visible in the run log and never masquerades as success.
5. **At most one scheduler/catch-up run per hour slot** (partial unique index). Manual runs (`collect once`) always append a new run.
6. **Queries are upserted by exact text.** A query removed from the Sheet gets `is_active=0`, never deleted. Never `.strip()`, `NFC`-normalise or lower-case query text; only strip the line terminator and a leading BOM.
7. **`products` is the only mutable table** (last-known name/brand/seller). Everything the TZ wants as history (price, rating, feedbacks, delivery, seller name, promoted flag) is on the immutable `observations` row.

### Run and query status semantics

| Level | Status | Meaning |
|-------|--------|---------|
| `run_queries` | `ok` | all needed pages fetched (or end-of-results reached) and `items_count > 0`; `covered_positions = top_n` |
| | `partial` | page 1.. n-1 ok, a later page blocked/failed; observations for fetched pages ARE stored; `covered_positions = items_count` |
| | `blocked` | 403 / 498 / 429 (after the single 429 wait-retry), or `circuit_open` skip |
| | `error` | timeout/5xx after retries, bad JSON, unexpected shape, `empty_result` (HTTP 200 but zero products on page 1: treated as suspicious, never as "ok with nothing") |
| `runs` | `ok` | every query `ok` |
| | `blocked` | no query `ok` and at least one `blocked`, none `error` |
| | `failed` | no query `ok`, otherwise |
| | `partial` | mixed outcomes (also: graceful SIGTERM mid-run, with `note='interrupted'`) |
| | `abandoned` | set by recovery, process died mid-run |

## Architectural Patterns

### Pattern 1: Functional core, imperative shell

**What:** All WB-specific knowledge (field names, kopecks, delivery hours, ad flag) lives in pure functions that take plain dicts + a `collected_at` and return frozen dataclasses. The shell (client, writer, orchestrator) just moves data.
**When to use:** Always here: WB's payload shape has already changed across v13..v18 and will again.
**Trade-offs:** A few more small types; in exchange the riskiest part (field mapping, delivery rule) is table-testable offline and fixable in one place.

```python
# wb/normalize.py (shape, not final code)
def extract_products(payload: dict) -> list[dict]:
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("products"), list):
        return data["products"]
    if isinstance(payload.get("products"), list):
        return payload["products"]
    raise UnexpectedShape("no products list")          # -> run_queries.error_class='bad_json'

def price_rub(kopecks: int | None) -> float | None:
    return None if kopecks is None else round(kopecks / 100, 2)

def delivery_days(collected_at_utc: datetime, hours: int | None, tz=MSK) -> int | None:
    if hours is None:
        return None
    start = collected_at_utc.astimezone(tz).date()
    end = (collected_at_utc + timedelta(hours=hours)).astimezone(tz).date()
    return (end - start).days                           # hypothesis; verify against the site on live data

def normalize_product(raw: dict, position: int, collected_at: datetime) -> ObservationRow: ...
```

Normalizer decisions to pin in tests: buyer price = **lowest `sizes[].price.product`** among sizes that have a price (fallback to legacy `salePriceU`); rating = `reviewRating` -> `rating`; feedbacks = `feedbacks` -> `nmFeedbacks`; delivery hours = product-level `time1+time2`, fallback to first size/stock with values, else NULL; `is_promoted = 1` iff the item has a non-empty `log` object. Every miss is NULL, never 0. Keep `time1/time2` in `extras` and `delivery_hours` as a column so `delivery_days` can be recomputed (via SQL) if the live check disproves the hypothesis.

### Pattern 2: Honest classification at the HTTP boundary (no circumvention)

**What:** The client's only intelligence is *pacing, bounded retry and truthful labelling*. It never changes identity, never rotates anything, never retries a block.
**When to use:** Always.
**Trade-offs:** If WB blocks plain httpx from the deploy host, the system records `blocked` every hour and shows that truthfully; that is the intended behaviour, not a bug to engineer around.

| Response | Action within the run | Recorded |
|----------|----------------------|----------|
| 200 + products | continue paging until `top_n` reached or an empty page | `ok` |
| 200 + zero products on page 1 / unparsable | no retry | `error` (`empty_result` / `bad_json`) |
| 5xx, timeout, connection error | up to 3 attempts, exponential backoff 1s/2s/4s + jitter, cap 30s | `error` after exhaustion (`partial` if page >= 2) |
| 429 | honour `Retry-After` (cap 60s), **one** retry | `blocked` if still 429 |
| 403, 498 | **no retry in this run** (the next hourly run is the retry) | `blocked` |
| other 4xx | no retry | `error` |

Additional rules: a global minimum interval between requests (default ~2 s plus small jitter; ~140 requests -> about 5 minutes per run, comfortably inside the hour); a **circuit breaker** in the orchestrator: after K consecutive `blocked` results (default 5) the remaining queries are written as `blocked/circuit_open` without sending requests, so a blocked hour costs ~10 requests instead of 140; ordinary descriptive `User-Agent` and `Accept: application/json` (configurable), one fixed endpoint URL from settings (no probing across API versions: version drift is handled by changing config, not by the client guessing).

### Pattern 3: Pagination by loop, not arithmetic

**What:** Request `page=1,2,...` and accumulate until `len(items) >= top_n` or a page returns zero products (with a hard safety cap, e.g. 6 pages). Position = running 1-based counter across the concatenated pages in the order received; truncate at `top_n`.
**When to use:** Always; page size has been 100 but has varied (48/100) across endpoint versions, and a short first page would make `(page-1)*100 + i` skip positions.
**Trade-offs:** Position is "index in what the API returned", which matches the TZ wording ("in the order presented"); a page-size change cannot silently corrupt positions.

### Pattern 4: Stateless scheduler, DB as the source of truth

**What:** The scheduler holds no schedule state. Every ~30 s tick it writes the heartbeat, computes the current hour slot in Europe/Moscow, asks the DB whether a run exists for that slot, and runs if not.

```python
while not stop.is_set():
    heartbeat.touch()
    now = clock.utc_now(); slot = clock.floor_to_hour(now, MSK)
    if not writer.slot_taken(slot) and (now - slot) < GRACE:        # GRACE = 45 min
        with flock(LOCK_PATH, blocking=False) as got:
            if got:
                trigger = "scheduler" if (now - slot) < timedelta(minutes=2) else "catchup"
                writer.abandon_stale_runs(); orchestrator.run_once(slot, trigger)
    stop.wait(timeout=30)
```

Policy consequences, all intentional:
- **Hourly at :00 MSK.** Moscow is UTC+3 with no DST, so slots coincide with UTC hours too, but the code does the math in `Europe/Moscow` explicitly (ship `tzdata` as a pip dependency; `python:3.12-slim` may lack system tzdata).
- **No overlap:** single-threaded loop *and* a non-blocking `flock` on `/data/collector.lock`, which also guards a manual `docker compose exec collector wbmon collect once`. `flock` is released by the kernel if the process dies, so "row says `running` but lock is free" == crashed.
- **Catch-up = at most the current slot, never the past.** A SERP is a point-in-time snapshot; back-filling hour 03:00 at 07:00 would stamp false data on the axis. Missed slots stay honest gaps (and the chart shows them, see below). On restart inside the first 45 minutes of a slot with no run, collect immediately (`trigger='catchup'`); otherwise wait for the next :00.
- **Overrun coalescing:** a run that exceeds an hour just causes the next tick to evaluate the new current slot; missed slots are skipped, not queued.
- `SIGTERM`: set the stop flag, finish the in-flight query, finalise the run as `partial` (`note='interrupted'`), exit. Compose `stop_grace_period: 60s`.

### Pattern 5: Read-only, short-lived, cached reads

**What:** Every UI data function opens a fresh `file:/data/wb.db?mode=ro` connection (`PRAGMA query_only=ON`), runs one query, closes it, and is wrapped in `@st.cache_data(ttl=...)`: queries list 10 min, nm_id list 5 min, series/characteristics/run log 60-120 s (collection is hourly, so 60 s staleness is invisible).
**Why not one shared connection:** Streamlit runs sessions on different threads (sqlite3 connections are thread-bound by default) and long-lived readers can block WAL checkpoints (sqlite.org/wal.html "checkpoint starvation"). Short connections give the writer its "reader gaps"; the collector also runs `PRAGMA wal_checkpoint(TRUNCATE)` after each run.
**Volume mount gotcha (verified in sqlite.org WAL docs):** a read-only WAL database can be opened only if `-shm`/`-wal` exist or the directory is writable (or `immutable=1`, which is wrong here since the DB changes). So mount `wbdata` **read-write** in `web` and enforce read-only at the connection (`mode=ro` + `query_only`), not with a `:ro` volume. WAL also requires all processes on the same host (fine: one VPS, two containers, named volume; never NFS/bind-mount from a network FS).

## Data Flow

### Collection flow (hourly)

```
Scheduler tick (slot S, MSK)
  └─ flock acquired ─▶ abandon_stale_runs() ─▶ run_id = start_run(S, trigger, top_n, dest, source)
       QuerySource.load()  ─ sheet CSV ok & sane? ─▶ 70 strings (source='sheet')
                           └ else bundled file    ─▶ 70 strings (source='fallback'); log diff either way
       writer.upsert_queries(strings) ─▶ [(query_id, text)]
       for each query (sequential, paced):
           try:
             pages = client.fetch_query(text)         # PageResult per page: ok|blocked|error + http_status
             rows  = [normalize_product(p, pos, collected_at) for pos, p in enumerate(items, 1)]
             writer.record_query_result(run_id, query_id, outcome, rows)   # ONE txn
           except Exception as e:                      # per-query isolation: bug in one query never aborts run
             writer.record_query_result(run_id, query_id, error_outcome(e), [])
           if breaker.tripped: write remaining as blocked/circuit_open without HTTP
       writer.finish_run(run_id, derive_status(counts)); wal_checkpoint(TRUNCATE)
```

### Display flow

```
Browser ─▶ Streamlit script rerun
   sidebar: query selectbox ◀─ reader.list_queries()            (cache 10 min)
            nm_id multiselect ◀─ reader.nm_ids_for_query(q)      (cache 5 min)
   axis   = reader.axis_for_query(q)                             (runs LEFT JOIN run_queries)
   points = reader.positions(q, nm_ids)
   series = series.build(axis, points, nm_ids, top_n, max_gap)   (pure: adds state per point)
   charts.position_figure(series)  ─▶ st.plotly_chart
   table  ◀─ reader.latest_characteristics(q, nm_ids) (+ history expander)
   footer/sidebar status ◀─ reader.last_runs(n)  ("Последний сбор: 14:00 МСК, ok / заблокирован")
```

### Key Data Flows

1. **Query list:** Sheet CSV -> `QuerySource` -> `queries` table (upsert by exact text) -> UI selectbox. Sheet wins; fallback only when the sheet is unreachable, not CSV (e.g. HTML login page), or empty.
2. **Observation:** WB JSON -> `extract_products` -> `normalize_product` -> `observations` row (one transaction with its `run_queries` row).
3. **Failure:** classified at the client -> `run_queries.status/http_status/error_class` -> shown in the UI as a failure band (never as a position).

## Gaps in the UI: three kinds of "no point"

The UI must distinguish them because they mean different things to a seller:

| State | Cause | Chart treatment |
|-------|-------|-----------------|
| `FOUND` | observation row exists | marker + line, y = position |
| `ABSENT` | `run_queries.status='ok'` (complete) and nm_id not among rows | **gap** (NaN). Optional toggle: small hollow marker on a separate "вне топ-N" trace at the bottom edge, legend-labelled; it is an annotation, not a fabricated position |
| `UNKNOWN` | run_query is `blocked`/`error`, `partial` and nm_id not in the covered part, no `run_queries` row (abandoned run / crash), or a stretch with no run at all | **gap** (NaN) + grey vertical band (`add_vrect`) labelled "сбой/блокировка сбора" with hover text (status, HTTP code) |

Algorithm (`ui/series.py`, pure, unit-tested):

1. Axis = `runs LEFT JOIN run_queries` for the query, ordered by timestamp, so failed and abandoned runs are on the axis even though they have no observations. `ts = COALESCE(rq.collected_at, r.started_at)`.
2. For each selected nm_id, reindex the observed positions onto the axis (`pandas.Series.reindex`); missing -> NaN; assign the state per the table above.
3. **Hour holes with no run at all** (collector down, VPS rebooted): insert one NaN break row between consecutive axis points when `Δt > max_gap` (default 1.5 x interval = 90 min). Otherwise Plotly would happily draw a line across five missing hours, which implies a continuity nobody observed.
4. Figure: `go.Scatter(x=ts_msk, y=position, mode="lines+markers", connectgaps=False)` per nm_id; `yaxis.autorange="reversed"` (position 1 on top); `hovertemplate` with `%{x|%d.%m.%Y %H:%M}` МСК, nm_id, position. Use `lines+markers`: a product seen in exactly one run between two gaps would be invisible as a bare line. Use stable colours per nm_id.
5. Timezone: DB holds UTC ISO. Convert once in the reader/series step (`tz_convert("Europe/Moscow").tz_localize(None)`), pass naive MSK datetimes to Plotly (plotly.js does not reliably apply tz offsets), and label the axis "время, МСК". Never store local time.

## Package interface: CLI and config

`wbmon` (argparse is enough; no CLI framework dependency):

| Command | Behaviour | Exit codes |
|---------|-----------|-----------|
| `wbmon init-db` | create dirs, run migrations, set WAL; idempotent | 0 |
| `wbmon collect once [--top-n N] [--limit-queries K]` | acquire lock, abandon stale runs, one run, `trigger='manual'`; prints a one-line summary | 0 ok/partial, 2 blocked/failed, 3 lock held |
| `wbmon scheduler` | the loop above | runs until SIGTERM |
| `wbmon healthcheck` | exit 0 iff `/data/heartbeat` mtime < 3 min | 0 / 1 |
| `wbmon backup [--to PATH]` (optional, small) | `sqlite3.Connection.backup()` consistent copy of the live DB | 0 |

`--limit-queries` is for smoke tests against the real API with low volume (writes real data only). There is deliberately **no** seed/demo command in the package; test data builders exist only under `tests/` and write to `tmp_path`.

Settings (env, prefix `WBMON_`): `DB_PATH=/data/wb.db`, `TOP_N=200`, `DEST=-1257786`, `SEARCH_URL`, `SHEET_CSV_URL` (`https://docs.google.com/spreadsheets/d/<id>/export?format=csv&gid=<gid>`, follow redirects; confirm the gid), `REQUEST_INTERVAL_S=2.0`, `REQUEST_TIMEOUT_S=20`, `MAX_RETRIES=3`, `BREAKER_THRESHOLD=5`, `TZ_NAME=Europe/Moscow`, `CATCHUP_GRACE_MIN=45`, `USER_AGENT`, `LOG_LEVEL`. Logging: stdlib `logging` to stdout, one line per query and per run (JSON not needed); Docker's json-file driver with `max-size`/`max-file`.

## Deployment Topology (Docker Compose, one VPS)

```yaml
# compose.yaml (shape)
services:
  init-db:                      # one-shot, idempotent migrations
    build: .
    command: ["wbmon", "init-db"]
    environment: &env { WBMON_DB_PATH: /data/wb.db }
    volumes: ["wbdata:/data"]
    restart: "no"

  collector:
    build: .
    command: ["wbmon", "scheduler"]
    init: true                                   # reaps zombies, forwards SIGTERM
    restart: unless-stopped
    stop_grace_period: 60s
    depends_on: { init-db: { condition: service_completed_successfully } }
    environment: *env
    volumes: ["wbdata:/data"]
    healthcheck:
      test: ["CMD", "wbmon", "healthcheck"]      # heartbeat file fresh; blocked runs are NOT unhealthy
      interval: 60s
      timeout: 10s
      retries: 3
    logging: { driver: json-file, options: { max-size: "10m", max-file: "5" } }

  web:
    build: .
    command: ["streamlit", "run", "src/wbmon/ui/app.py",
              "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true",
              "--browser.gatherUsageStats=false"]
    restart: unless-stopped
    depends_on: { init-db: { condition: service_completed_successfully } }
    environment: *env
    volumes: ["wbdata:/data"]                    # rw volume, read-only connection (see Pattern 5)
    ports: ["8501:8501"]                         # or put an optional Caddy service in front for TLS on 443
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=5).status==200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  wbdata:
```

- **One image, two roles.** `python:3.12-slim` has no `curl`; use the Python one-liner for the web healthcheck. `/_stcore/health` is the Streamlit health route; it breaks if `--server.baseUrlPath` is set (reported on the Streamlit forum), so do not set a base path.
- **Collector healthcheck means "the scheduler loop is alive"**, not "WB is reachable". A blocked WB must not put the container into restart loops; blocked is a data-level condition shown in the UI and run log.
- **Web handles an empty/missing DB** (first minutes after deploy): friendly "Данных пока нет, первый сбор в HH:00 МСК" state, and a run-status strip so a reviewer immediately sees real runs/blocks.
- `/data` holds `wb.db(-wal,-shm)`, `collector.lock`, `heartbeat`. Back up with `wbmon backup` (volume loss = history loss).
- Logs: `docker compose logs -f collector`.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| This assignment (70 queries, hourly, 1 UI audience) | As above. ~140 requests/run, ~5 min/run, ~100 MB/day. |
| 5x queries or 15-min cadence | Same design. Watch run duration vs. interval (coalescing handles overrun); consider a bounded concurrency of 2 only if WB tolerates it, otherwise keep sequential. |
| Multiple writers / multiple regions / many viewers | Move `storage/` behind the same writer/reader interfaces to Postgres (the only module that imports `sqlite3`), partition `observations` by month. The rest of the code is unaffected. |

### Scaling Priorities

1. **First bottleneck:** WB politeness/blocking, not compute. Fix by lowering rate or cadence, never by evasion.
2. **Second bottleneck:** `observations` growth and the "nm_ids ever seen" aggregation over weeks of data. The covering index handles it; if it slows, add a materialised `query_nm_stats` table updated in the same per-query transaction.

## Anti-Patterns

### Anti-Pattern 1: Scheduler or collection inside the Streamlit process

**What people do:** `threading.Thread` / `st.cache_resource` background job inside the web app.
**Why it's wrong:** Streamlit reruns/restarts kill it; multiple sessions can double-run; a UI crash stops data collection.
**Do this instead:** separate `collector` container; web is read-only.

### Anti-Pattern 2: Fabricating positions for absent products

**What people do:** fill absent with 201 / 0 / forward-fill so lines are continuous.
**Why it's wrong:** breaks the project's core value and the TZ ("gap, no fabricated position").
**Do this instead:** NaN + `connectgaps=False`; failures are bands, not values.

### Anti-Pattern 3: Treating "200 with zero items" or "short page" as success

**What people do:** accept any 200 as `ok` and store 0 rows.
**Why it's wrong:** soft blocks/format changes then look like "everything left the top-200" for a whole hour.
**Do this instead:** `empty_result` -> `error`; `ok` requires `items_count > 0`, enforced by a CHECK constraint.

### Anti-Pattern 4: Retrying blocks / escalating when blocked

**What people do:** retry 403 in a tight loop, add header tricks, rotate identities.
**Why it's wrong:** violates the project's hard boundary and worsens the block.
**Do this instead:** classify `blocked`, record it, open the circuit breaker, try again next hour.

### Anti-Pattern 5: Whole-run transaction, or per-row autocommit

**What people do:** one giant transaction (crash loses everything) or 200 commits per query (slow, torn queries).
**Do this instead:** one transaction per query result.

### Anti-Pattern 6: Local-time storage or tz-aware datetimes into Plotly

**What people do:** store MSK strings; send tz-aware timestamps to Plotly and get shifted axes.
**Do this instead:** UTC ISO in DB; convert to naive MSK at the display edge; label axis as МСК.

### Anti-Pattern 7: Normalising query text or de-duplicating Sheet rows

**What people do:** `.strip().lower()`, NFC, replace `ё`/`х`.
**Why it's wrong:** the TZ requires byte-exact wording, and Cyrillic "х" vs Latin "x" are intentionally distinct queries.
**Do this instead:** exact text; `UNIQUE(text)` with BINARY collation; log (don't fix) suspicious entries.

### Anti-Pattern 8: Read-only volume + WAL; shared long-lived sqlite connection in Streamlit

**Do this instead:** see Pattern 5.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Google Sheets CSV export | `httpx.get`, redirects on, 10-20 s timeout, validate (not HTML, >= 1 row) before trusting; no auth | Sheet wins on difference; bundled list is the offline fallback; log `+added/-removed` vs bundled; record `runs.query_source` |
| WB search JSON | one configured endpoint, params `query,page,dest,curr=rub,lang=ru,resultset=catalog,sort=popular,spp,appType,...` | Plain httpx, ordinary headers, paced. May be blocked from the deploy host: the first deploy-host probe (a `collect once --limit-queries 2`) is a go/no-go data point for the whole deliverable and must happen early |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| `queries.source` -> `collector.runner` | `list[str]` + `source` tag | no DB access in the source |
| `wb.client` -> `collector.runner` | `PageResult(status, http_status, products, error_class, attempts)` | client never raises for HTTP-level problems; only for programmer errors |
| `wb.normalize` <- `collector.runner` | `normalize_product(raw, position, collected_at)` | pure; unit-tested with fixtures |
| `collector.runner` -> `storage.writer` | `QueryOutcome` + `list[ObservationRow]`, one call per query | writer owns all SQL for writes and transactions |
| `storage.reader` -> `ui` | DataFrames / plain rows | UI never builds SQL |
| `collector` <-> `ui` | none at code level; shared SQLite file only | enforced by `test_import_boundaries.py` |

## Suggested Build Order (dependencies drive phase structure)

| # | Build | Depends on | Testable how | Notes for roadmap |
|---|-------|-----------|--------------|-------------------|
| 1 | Skeleton: `pyproject`, `config`, `clock`, `models`, bundled `queries_fallback.txt`, CI-able pytest | none | unit | pin Python 3.12 target while dev on 3.14 |
| 2 | `wb/normalize` + `wb/classify` with fixtures | 1 | table-driven unit tests | can proceed without live WB; fixtures are hand-built test data under `tests/` |
| 3 | `storage` (DDL, migrate, writer, reader) | 1 (models) | `tmp_path` DB: append-only triggers, idempotency, crash -> `abandoned`, per-query atomicity | schema is the contract for UI and collector, freeze early |
| 4 | `queries/source` | 1 | `httpx.MockTransport`: sheet ok / HTML / empty / offline -> fallback; byte-exactness of `ё`, `х` vs `x` | |
| 5 | `wb/client` | 1, 2 | `MockTransport`: pages, 5xx->200, timeout, 429+Retry-After, 403/498 no retry; fake clock for pacing | |
| 6 | `collector/runner` + `cli collect once` | 2-5 | fake client: isolation, breaker, partial, status derivation; one real `--limit-queries 2` smoke | **Go/no-go checkpoint:** try plain httpx from the deploy host here; outcome (data or honest `blocked`) shapes the write-up |
| 7 | `scheduler` + `lock` + `healthcheck` | 3, 6 | pure `decide(now, slot_taken)` + flock test with two processes | |
| 8 | `ui/series` + `ui/charts` + `ui/app` | 3 (reader), real or test DB | `series` unit tests (3 states, break rule); `AppTest` smoke; build against a **test-only** temp DB, never the production DB | can start in parallel with 4-7 once schema is frozen |
| 9 | Docker image + `compose.yaml` + healthchecks, deploy to VPS | 6-8 | `docker compose up`, `ps` healthy, 2-3 real hourly runs observed | start collecting real history as early as possible (needs several real runs) |
| 10 | README (RU), write-up, optional backup/Caddy | 9 | | |

Suggested roadmap grouping (coarse): (A) core + data contract (steps 1-3); (B) collection pipeline incl. first real probe (4-6); (C) unattended operation + deploy (7, 9) so history accumulates; (D) UI (8) in parallel with C; (E) polish/docs (10).

## Sources

- sqlite.org/wal.html (fetched 2026-10-16): same-host requirement, read-only WAL conditions (`-shm`/`-wal` exist or writable directory or `immutable`), checkpoint starvation with long-lived readers. HIGH.
- Streamlit Docker templates and forum thread: `/_stcore/health` as health route, breakage with `baseUrlPath` (discuss.streamlit.io "BaseUrlPath and Health check for Streamlit app running on ECS / Fargate"). MEDIUM.
- `/Users/fvst/Desktop/Тестовое задание./wb-serp-monitor/.planning/PROJECT.md`: WB endpoint/fields/anti-bot situation, constraints, decisions. Primary project context; WB field details unverified live (MEDIUM).
- Established patterns (functional core / imperative shell, single-writer append-only store, stateless DB-backed scheduler, circuit breaker, `PRAGMA user_version` migrations, `sqlite3.Connection.backup`): standard practice, no external citation needed. HIGH.
- Plotly `connectgaps=False`, `yaxis.autorange="reversed"`, Streamlit `st.cache_data(ttl=...)`, `streamlit.testing.v1.AppTest`: library behaviour from prior knowledge, not re-fetched in this pass; confirm in the UI phase. MEDIUM.

---
*Architecture research for: Wildberries SERP position tracker (collector + SQLite + Streamlit)*
*Researched: 2026-10-16*
