<!-- GSD:project-start source:PROJECT.md -->

## Project

**WB SERP Monitor**

A Python service that collects Wildberries search results (SERP) every hour for 70 fixed
bedding-related search queries (top-200 products per query, fixed Moscow delivery region),
stores the full history of observations in a database, and shows how product positions change
over time in a Streamlit web UI. It is a test assignment for an AI-developer vacancy at a team that
builds AI/data products for Ozon/Wildberries sellers; the result is sent to the employer as a repo
link, a link to the running web UI with real data, and a short write-up.

**Core Value:** Real Wildberries search observations are collected on schedule, appended to history without ever
being lost or fabricated, and the UI shows each product's position history honestly (gaps where a
product was not found — never invented positions).

### Constraints

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
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->

## Technology Stack

## Recommended Stack

### Core Technologies

| Technology | Version (verified 2026-10-16) | Purpose | Why Recommended |
|------------|-------------------------------|---------|-----------------|
| Python | 3.12 (image `python:3.12-slim`, currently Debian 13/trixie, pushed 2026-10-09); `requires-python = ">=3.11"` | Runtime for both processes | Streamlit 1.66 and pandas 3.0 both require >=3.11, so 3.12 is the safe floor. Dev machine runs 3.14 - keep code 3.11-compatible and run the test suite inside the 3.12 image (CI/Docker) before deploy. **HIGH** |
| httpx | 0.28.1 (stable; 1.0 exists only as `1.0.dev*`) - pin `httpx>=0.28.1,<1` | HTTP client for WB search JSON and Google Sheet CSV | Sync `httpx.Client` is enough: 70 queries x 2 pages = 140 requests/hour at a polite pace is I/O-wait, not concurrency-bound. Explicit `Timeout`, connection reuse, and clean status handling. **Gotcha:** `follow_redirects` defaults to `False` - the Google Sheets `export?format=csv` URL redirects, so set `follow_redirects=True` for that call only. **HIGH** |
| SQLite (stdlib `sqlite3`) | Library bundled with Debian trixie python image (3.46.x - WAL, STRICT tables, UPSERT, `RETURNING` all available) | Observation history store | Zero-ops, ~14k rows/hour (336k/day, roughly 40-60 MB/day with indexes) is trivial. WAL gives one writer + many concurrent readers, which is exactly collector-writes / Streamlit-reads. Stdlib `sqlite3` + a ~100-line repository module beats SQLAlchemy here (no ORM mapping, no extra dependency, full control over `BEGIN IMMEDIATE`). **HIGH** |
| Streamlit | 1.66.0 (2026-10-14, requires Python >=3.11) | Web UI | Required by the TZ. Since 1.57 the server is Starlette/Uvicorn (Tornado removed) - old Tornado-era config advice on blogs/forums is partly stale. 1.66 drops Python 3.10. Health endpoint `/_stcore/health` unchanged. **HIGH** |
| Plotly (Python) | 7.1.0 (2026-09-15) | Interactive position-over-time chart | Streamlit 1.66's frontend bundles **plotly.js v4** (verified by grepping the 1.66 wheel's static assets), and plotly.py 7.x targets plotly.js 4.x - versions match, so no schema skew. Gives hover templates, reversed axis, and native gaps. **HIGH** |
| pandas | 3.0.6 (2026-09-17, requires >=3.11) | Shaping query results for chart/table | Already a hard Streamlit dependency (`pandas<4`), so it costs nothing in the web image. `pd.read_sql_query` works with plain `sqlite3` connections without warnings. Install only in the web image/extra, not the collector. Pandas 3 uses the new string dtype by default - don't write code relying on `object` dtype for text columns. **HIGH** |
| supercronic | v0.2.49 (2026-08-14), static Go binary | In-container cron for the collector | See "Scheduling decision" below. **MEDIUM-HIGH** |
| Docker Compose | Compose v2 plugin (`docker compose`, no `version:` key) | Orchestrates collector + web + proxy | Required by constraints. Provides restart policies, healthchecks, shared volume. **HIGH** |
| Caddy | `caddy:2.11-alpine` (latest tag 2.11.7, 2026-10-03) | TLS-terminating reverse proxy in front of Streamlit | Automatic Let's Encrypt, WebSocket upgrade is automatic (Streamlit needs `/_stcore/stream` WS), 4-line Caddyfile. Strictly better than hand-rolled nginx (needs manual `Upgrade`/`Connection` headers, certbot sidecar). **HIGH** |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | 2.14.1 (requires >=3.10) | Defensive parsing of WB JSON (all fields `Optional`, accept both `data.products` and top-level `products`) | Collector only. Turns "field missing/odd type" into `None` -> SQL `NULL` instead of crashes or invented values. Use `model_validate` per product, skip + count a product that fails validation. **HIGH** |
| pydantic-settings | 2.16.0 | Env-driven config (`TOP_N`, `DB_PATH`, `WB_DEST`, `REQUEST_DELAY_S`, `SHEET_URL`) | Collector + web share one `Settings` class; `.env` for Compose. Skip if you prefer 10 lines of `os.environ`; either is fine. **MEDIUM** |
| tzdata | 2026.5 (PyPI) | IANA tz database for `zoneinfo.ZoneInfo("Europe/Moscow")` | Put in dependencies explicitly. Do not rely on system tzdata (absent on some slim/Windows/macOS setups) - delivery-day math (calendar-day diff in MSK) and UI display depend on it. **HIGH** |
| respx | 0.23.1 | Mock `httpx` in tests (200 / 403 / 498 / 429 / timeouts / malformed JSON) | Dev. Fixtures live only under `tests/`; never touch the real DB (data-integrity constraint). **HIGH** |
| pytest | 9.1.1 | Test runner | Dev. Use `tmp_path` SQLite DBs and `streamlit.testing.v1.AppTest` (ships with Streamlit) for a UI smoke test. **HIGH** |
| ruff | 0.17.1 | Lint + format | Dev; replaces black/flake8/isort. **HIGH** |
| uv | 0.13.1 | Dependency resolution, lockfile (`uv.lock`), Docker installs | `COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /bin/uv`; `uv sync --frozen --no-dev`. Reproducible builds in the image. Plain `pip` + `requirements.txt` is an acceptable fallback. **MEDIUM-HIGH** |
| freezegun | 1.5.5 | Freeze time in tests | Optional - prefer passing a `now` parameter / injectable clock into collector functions (simpler, no monkeypatching). **LOW priority** |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| `uv` + `pyproject.toml` with extras | Single package `wbmon` with `[project.optional-dependencies] web = [streamlit, plotly, pandas]`; collector image installs base deps only | Keeps collector image small (no Streamlit/pandas/pyarrow, saves ~300 MB). |
| `pytest` + `respx` | Collector unit tests incl. blocked-status taxonomy | Test matrix: 200 ok, empty list, `data.products` vs `products`, 403/498/429 -> `blocked` (and no retry storm), 5xx/timeout -> `error`, Sheet unreachable -> bundled fallback. |
| `ruff check` / `ruff format` | Lint/format | Configure in `pyproject.toml`. |
| `docker compose config` / `supercronic -test crontab` | Validate compose and crontab syntax before deploy | Cheap CI/local gate. |

## Decision: Scheduling (in-process APScheduler vs cron/systemd vs supercronic)

- **Process-per-run isolation.** Each hourly run is a fresh process: leaked memory/connections, a wedged socket, or an unhandled exception cannot kill the schedule. Exit code and logs per run go to stdout (`docker compose logs collector`).
- **The same CLI is the manual/test entry point** (`docker compose run --rm collector python -m wbmon collect`), so there is exactly one code path - no "scheduler mode" vs "manual mode" divergence.
- **Docker-native:** supercronic is a single static binary built for containers (logs to stdout/stderr, no syslog, no root, no PID-1 zombie issues, signals handled, skips overlapping runs by default - `-overlapping` opts into overlap). Classic `cron` in a container fights all of these.
- **Timezone-safe:** hourly at minute 0 fires at the same instant in every whole-hour zone (Moscow is UTC+3 with no DST since 2014), and supercronic honours `TZ=Europe/Moscow` (needs tzdata in the image) for log timestamps. Data is stored in UTC regardless.
- **Compose-contained:** `docker compose up -d` is the whole deploy; nothing to install on the host.

| Option | Verdict | Reason |
|--------|---------|--------|
| APScheduler 3.11.3 (`BlockingScheduler` + `CronTrigger`) | Acceptable fallback, not primary | Pure-pip and gives `coalesce`/`misfire_grace_time`, but: the 4.x line is still alpha (`4.0.0a6` is the newest release - pin `<4` or you may pull a rewrite), it adds `tzlocal` and an in-process scheduler state that dies with the process, and a hung job inside the scheduler process shares memory with it. Choose it only if you refuse to download a binary in the Dockerfile. |
| Host cron / systemd timer calling `docker compose run --rm collector` | Good alternative | `systemd` timers with `Persistent=true` **catch up missed runs after downtime** (supercronic and cron do not). But it adds host-side setup outside the repo's Compose file, which complicates "run instructions" for a reviewer. Pick this if VPS downtime catch-up matters more than a self-contained `compose up`. |
| `while True: sleep()` loop | Avoid | Hand-rolled drift/overlap/restart edge cases for no gain. |
| Celery / RQ / Airflow / Prefect | Avoid | Brokers and services for one job per hour. Massive over-engineering. |

- **Idempotency by slot:** `runs` table with `slot_utc` (top of the hour, ISO) `UNIQUE`; a run for a slot that already exists is skipped. Prevents double collection after container restarts.
- **Startup catch-up guard:** entrypoint does `init-db`, then runs `collect --if-stale 50m` in the background (so a fresh deploy yields data immediately instead of waiting up to 60 min), then `exec supercronic`. The `--if-stale` check (last run started <50 min ago -> skip) stops a crash-loop from hammering WB.
- **Healthcheck reflects scheduler liveness, not WB reachability:** `python -m wbmon healthcheck --max-age 95m` exits 1 if no run row (ok *or* blocked) started in the last 95 minutes. Blocked runs are still runs.

## Decision: Plotly chart specifics

- **Reversed Y with position 1 on top:** `fig.update_yaxes(autorange="reversed")`, or a fixed `range=[top_n + 5, 0]` (reversed order: larger first) so the scale doesn't jump between selections. Add `title="Позиция в выдаче"`.
- **Gaps for missing runs - build a complete time grid yourself:** Plotly breaks a line only at `None`/`NaN` y values (`connectgaps=False` is the default for scatter - keep it). A product absent from a run has *no row* in the DB, so a plain line would silently join its neighbours. Build for each selected `nm_id` a series over **all successful runs of that query** (`runs`/`query_attempts` with status `ok`), left-joined to observations; absent -> `NaN`. This is the "honest gap" mechanism and must be explicit in code + a test.
- **Isolated points vanish in `mode="lines"`** (a lone observation between two gaps has no neighbour to draw to) -> use `mode="lines+markers"`.
- **Distinguish "not found" from "no data (blocked/error)":** both are gaps in the line, but shade blocked/error runs (`add_vrect` or small grey markers on a baseline) so the reviewer sees *why*. Never interpolate.
- **Timezone display:** plotly.js ignores UTC offsets in ISO strings and shows wall-clock. Convert UTC -> `Europe/Moscow`, drop tzinfo (`.dt.tz_convert("Europe/Moscow").dt.tz_localize(None)`) before plotting and label the axis "время МСК". Store UTC; display MSK.
- **Hover:** `hovertemplate="%{x|%d.%m.%Y %H:%M}<br>ID %{customdata[0]}<br>Позиция %{y}<extra></extra>"` with `customdata=nm_id`. Use `hovermode="closest"` (with `"x unified"` multiple products' hover boxes get crowded).
- **Rendering call:** `st.plotly_chart(fig, width="stretch")`. The `use_container_width` argument was **removed in Streamlit 1.57**; old tutorials using it will throw.
- **Size:** 10 products x 30 days x 24 = 7,200 points - plain SVG `go.Scatter` is fine; `Scattergl` not needed.
- Don't rely on `config={"locale": "ru"}` (locale bundles are separate plotly.js files; unverified whether Streamlit ships them - **LOW confidence**). Hard-code Russian labels and `tickformat="%d.%m %H:%M"`.

## Decision: SQLite shared between two containers

- **One Docker named volume (`wbdata`) mounted at `/data` in both `collector` and `web`.** WAL's `-shm` file is an mmap'd shared-memory index - this works across containers because they share the host kernel and the volume is a local filesystem. It does **not** work on network filesystems (NFS/SMB) and is unreliable on Docker Desktop macOS bind mounts - use a named volume, not `./data:/data`, for the deployed stack.
- **Both containers must be able to write to the directory** (even the reader needs to create/open `-shm`/`-wal`). Do not mount the volume `:ro` in `web`. Instead open the web connection with `PRAGMA query_only = ON` (enforces read-only at the SQLite level while keeping WAL files usable). Don't use `?mode=ro` URI unless `-shm`/`-wal` already exist.
- **Same UID in both containers** (non-root `app`, uid 10001); create and `chown` `/data` in the image so the named volume inherits ownership on first mount.
- **Pragmas (per connection):**
- **Collector write pattern:** `sqlite3.connect(path, isolation_level=None)` (manual transaction control); per query: `BEGIN IMMEDIATE` -> `executemany` the <=200 observation rows + the query-attempt status row -> `COMMIT`. One short transaction per query = per-query isolation (a failing query writes `blocked`/`error` status, never partial rows) and the write lock is held for milliseconds, so the UI never sees `database is locked`.
- **Schema versioning:** `PRAGMA user_version` + an ordered list of migration SQL strings in code; `init-db` runs idempotently at collector start. No Alembic (single table family, one dev).
- **Time storage:** UTC ISO-8601 TEXT with `Z` (`2026-10-16T09:00:00Z`) - sortable, human-readable in `sqlite3` shell; slot/run times as top-of-hour. Keep `collected_at` (actual) separate from `slot_utc` (scheduled).
- **Streamlit side:** open a new connection inside a `@st.cache_data(ttl=300)` function, `pd.read_sql_query`, close. Data changes hourly, so a 5-minute TTL is invisible to users and protects SQLite from rerun storms. Handle "DB file/tables not there yet" with a friendly Russian empty state (first deploy minutes).
- **Backup (cheap, optional):** daily `sqlite3.Connection.backup()` (Python, since `sqlite3` CLI isn't in slim) to a second volume path; history is the product, so one copy of it is worth having.

## Concrete Docker topology

### Dockerfile (one file, two targets) - sketch

# syntax=docker/dockerfile:1

# ---- collector ----

# sha1 from the v0.2.49 release page (verified 2026-10-16)

# ---- web ----

### compose.yaml - sketch

- WebSocket upgrade for `/_stcore/stream` is automatic in Caddy 2 - no extra directives. Keep Streamlit's XSRF/CORS protections **on** (defaults); the proxy and app are same-origin from the browser's view. Optional hardening (Streamlit >=1.61): `--server.allowedHosts=<your-hostname>`.
- `SITE_ADDRESS` choices: real domain `monitor.example.com` -> automatic Let's Encrypt (A record must point at the VPS; ports 80+443 open). No domain -> `<ip-with-dashes>.sslip.io` (e.g. `203-0-113-5.sslip.io`) gives a publicly-trusted Let's Encrypt cert with zero DNS work (**MEDIUM** - works in practice, but depends on a third-party free service). Last resort `:80` -> plain `http://IP/` (works, but browsers show "not secure").
- **Direct port (`ports: 8501:8501`, no proxy)** is the simplest possible fallback and acceptable for a test assignment, but: no TLS, no gzip, port visible in the link, Streamlit exposed to the internet without a hardened front. Keep Caddy as the default; document the one-line fallback in the README.

### Timezone policy (one rule, everywhere)

- **Store UTC** (ISO `...Z`). **Compute** delivery-day = calendar-day diff using `ZoneInfo("Europe/Moscow")` in Python, never the container's local zone. **Display** MSK in UI. `TZ=Europe/Moscow` on containers only affects log timestamps and supercronic's clock, which is deliberately immaterial for an hourly `0 * * * *` job.

### Restart/health semantics

- `restart: unless-stopped` on all services (survives VPS reboot given `systemctl enable docker`). Plain Compose only restarts on process exit; an `unhealthy` container is *not* auto-restarted - the healthchecks serve visibility (`docker compose ps`) and `depends_on: service_healthy` gating. If auto-heal is wanted, add `willfarrell/autoheal` - **not recommended** for scope.
- Collector blocked-by-WB is an expected steady state: the process stays healthy, runs get `blocked` rows, UI surfaces it.

## Collector HTTP behaviour (stack-level rules)

- One `httpx.Client` per run: `timeout=httpx.Timeout(20.0, connect=10.0)`, ordinary browser-like `User-Agent`, `Accept: application/json`, `Accept-Language: ru-RU,ru;q=0.9`. No cookies jar tricks, no special TLS, no http2 extra needed.
- Polite pacing: sequential requests with a configurable delay (e.g. 1-2 s + small jitter) -> a full run of 140 requests takes ~3-5 minutes, far inside the hour.
- Status taxonomy: `200` -> parse; `403`/`498`/`429` -> **`blocked`** (record status + HTTP code, honour `Retry-After` on 429, **no immediate retry**); `5xx`/timeouts/connection errors -> bounded retry (max 2, exponential backoff) then `error`; unparseable JSON -> `error`. Circuit breaker: after N (e.g. 5) consecutive `blocked` queries, mark the rest of the run `skipped_blocked` and end it - do not keep hammering. Next hourly run tries again.
- Retries by hand (a ~20-line loop) rather than `tenacity` (9.2.1): the retry policy is status-specific and "don't retry on blocked" is clearer in explicit code than in decorator config. Add `tenacity` only if you want declarative backoff for the 5xx/timeout path.
- Google Sheet: `follow_redirects=True`, decode `resp.content.decode("utf-8-sig")`, parse with stdlib `csv`, **do not** `strip()`/normalise Unicode (NFC/NFKC would silently change "ё", "х"/"x", double spaces) and do **not** use pandas for this (NaN coercion, dtype guessing). Fall back to the bundled file on any failure; log which source was used.

## Installation

# Dev machine (uv)

# Run

# Deploy (VPS)

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| supercronic + one-shot CLI | APScheduler 3.11.3 in-process | You can't download a binary in the image, or want in-process `misfire_grace_time`/coalesce semantics. Pin `<4`. |
| supercronic | systemd timer (`Persistent=true`) + `docker compose run --rm` | You need catch-up of runs missed during VPS downtime and accept host-side setup. |
| Stdlib `sqlite3` | SQLAlchemy 2.1.4 Core | You expect to switch to Postgres soon. The data layer is ~5 queries; abstraction cost > benefit for this scope. (Streamlit's `st.connection("sql")` also needs SQLAlchemy - skip it.) |
| SQLite WAL | PostgreSQL 16/17 container | Multiple writers, >1 host, or UI hosted elsewhere (see Community Cloud below). TZ allows it, but it adds a service + credentials for no benefit at 336k rows/day. |
| Caddy | nginx (+certbot) | Org standard is nginx. Needs `proxy_http_version 1.1`, `Upgrade`/`Connection` headers, long `proxy_read_timeout` for the WebSocket. |
| Caddy | Direct `8501` | Throwaway demo only. |
| Plotly (via `st.plotly_chart`) | Altair (Streamlit-native `st.line_chart`/`st.altair_chart`) | Never here: reversed axis + true gaps + custom hover is more awkward, and the TZ explicitly wants hover with date/ID/position. |
| `python:3.12-slim` | `python:3.13-slim` / `3.14-slim` | After the assignment; 3.12 is the stated deploy target and the safest wheel coverage. |
| Sync `httpx.Client` | `httpx.AsyncClient` + `asyncio.gather` | Never needed: higher concurrency against an API that is already actively filtering traffic is the wrong direction; sequential-and-polite is the requirement. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Any anti-bot circumvention tooling (TLS-impersonation clients, stealth/headless browsers, token/cookie harvesting or replay, CAPTCHA/PoW solving, evasion proxy rotation) | **Excluded by project constraint** (hard boundary; ethics/platform rules) | Plain `httpx`; record `blocked`, back off, report honestly in UI. |
| `use_container_width=` in `st.plotly_chart` | Removed in Streamlit 1.57 | `width="stretch"` |
| Streamlit on Community Cloud as the only host with a local-file SQLite | No persistent shared filesystem, can't see the VPS volume | VPS + Caddy (see below). |
| Mounting the SQLite volume `:ro` in `web`, or an NFS/SMB/bind-mount-on-Docker-Desktop for the DB | WAL needs writable `-shm`; network FS breaks WAL locking | Named volume, `PRAGMA query_only=ON`. |
| One global long-lived SQLite connection in Streamlit (`@st.cache_resource` connection) | Threading errors (`check_same_thread`), blocks WAL checkpoints | Short-lived connection per cached query. |
| `journal_mode=DELETE` (default) | Readers block the writer and vice versa -> `database is locked` | WAL. |
| Classic `cron`/`crond` in the container | Env vars not inherited, logs go nowhere, PID-1 and root issues | supercronic. |
| APScheduler `4.0.0a*` | Alpha, API differs from 3.x, not for unattended prod | 3.11.3 if in-process is chosen. |
| `pytz` | Legacy; `zoneinfo` + `tzdata` is the standard | `zoneinfo.ZoneInfo`. |
| Celery/Redis/RabbitMQ/Airflow | One job/hour; infra bloat | supercronic. |
| pandas for the query-list CSV | NaN coercion, whitespace/dtype guessing breaks byte-for-byte wording | stdlib `csv`. |
| `st.cache_data` without `ttl` on DB reads | UI never shows new hourly data until restart | `ttl=300`. |
| Unbounded Docker logs | Fill a small VPS disk over weeks | `json-file` `max-size`/`max-file`. |
| `latest` tags (`caddy:latest`, `python:latest`) | Non-reproducible deploys | Pinned `python:3.12-slim`, `caddy:2.11-alpine`, `uv:0.13.1`. |

## Streamlit Community Cloud as UI-only host (DB elsewhere)

- **The core blocker is the DB, not Streamlit.** SQLite is a local file; Community Cloud gives no volume mount and its apps run on separate infra, so the UI cannot open the VPS's `wb.sqlite3`. SQLite over a network is not viable (no WAL across hosts). To make it work you would need one of:
- **Behavioural drawbacks for a hiring-review link:** apps with no traffic for **12 hours go to sleep** and show a "get this app back up" wake-up screen (cold start of tens of seconds) - exactly what an employer opening the link a day later will see. Resource caps (documented approx. 0.078-2 CPU cores, 690 MB-2.7 GB RAM) are shared-tier. Repo must be accessible to the Streamlit GitHub integration (public or authorised private). Secrets via `st.secrets`.
- **What it is good for:** a zero-ops mirror of the UI if the VPS UI is blocked/unreachable from the reviewer's network - and only if option 1 or 2 above is already in place.
- **Recommendation:** primary = VPS + Caddy (UI and collector on one box, one volume, no extra datastore, no sleep). Do not build Cloud support in the initial roadmap.

## Stack Patterns by Variant

- Verify `docker pull python:3.12-slim` and `caddy:2.11-alpine` on the host in the very first deploy step (**LOW confidence** on current RU reachability - not researched). Fallback: build images on the dev machine, `docker save | ssh host docker load`, or configure a registry mirror. The supercronic download from GitHub happens at build time, so build where GitHub is reachable.
- The stack must still be fully functional: runs recorded as `blocked`, UI shows run-health panel and an honest "no data" state. Nothing in the stack changes; no workaround is in scope.
- `SITE_ADDRESS=<ip-dashed>.sslip.io` for a trusted cert, else `:80`.
- Add `ruff` + `pytest` GitHub Actions job running inside `python:3.12-slim` (mirrors deploy runtime; local 3.14 can hide 3.12 incompatibilities).

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| streamlit 1.66.0 | plotly 7.1.0 | 1.66 bundles plotly.js v4; plotly.py 7.1 targets plotly.js 4.1.x - aligned (verified by inspecting wheel). |
| streamlit 1.66.0 | pandas 3.0.6, numpy <3, pyarrow !=25.0.0,<27 | Streamlit's constraints (`pandas<4`, raised minimums in 1.66). Let the resolver pick; do not hand-pin pyarrow. |
| streamlit 1.66.0 | uvicorn >=0.30, websockets <18 | 1.66 logs a startup warning if `websockets>=17` with `uvicorn<0.52`; let `uv.lock` resolve a consistent pair and check the startup log once. |
| streamlit 1.66.0 | Python >=3.11 | 3.10 dropped in 1.66; image is 3.12. |
| pandas 3.0.x | Python >=3.11 | Same floor as Streamlit. New default string dtype. |
| plotly 7.0+ | plotly.js 4 | Breaking vs 6.x: stricter color parsing (no `rgb()` fractions / `hsv()`), mapbox traces removed (irrelevant here); use hex or named colours / `plotly.colors.qualitative.*`. Kaleido <1.0 unsupported (not used). |
| httpx 0.28.1 | Python >=3.8; no h2 needed | `<1` pin guards against the 1.0 pre-releases. |
| pydantic 2.14 / pydantic-settings 2.16 | Python >=3.10/>=3.11 | Consistent with 3.12 image. |
| APScheduler 3.11.3 (fallback only) | Python >=3.8, `tzlocal>=3.0` | Never allow 4.0.0a*. |
| supercronic v0.2.49 | linux/amd64, linux/arm64 (also 386, arm) | Use `TARGETARCH` + per-arch sha1 in Dockerfile (hashes above). Needs `tzdata` for `TZ=`. |
| SQLite (Debian trixie) | WAL across containers on same host volume | Not valid across hosts/NFS. |

## Sources

- PyPI JSON API (queried live 2026-10-16): httpx 0.28.1; streamlit 1.66.0 (+ `requires_dist`); plotly 7.1.0; pandas 3.0.6; apscheduler 3.11.3 (release list shows 4.0.0a1-a6 only as alphas); tenacity 9.2.1; pydantic 2.14.1; pydantic-settings 2.16.0; pytest 9.1.1; respx 0.23.1; ruff 0.17.1; uv 0.13.1; tzdata 2026.5; freezegun 1.5.5; SQLAlchemy 2.1.4 - **HIGH**
- Streamlit 2026 release notes https://docs.streamlit.io/develop/quick-reference/release-notes/2026 - 1.57 Starlette default + removal of `use_container_width` for plotly; 1.60 hardening; 1.61 `server.allowedHosts`; 1.66 drops Python 3.10 (note: only the first ~100k chars of the page were read) - **HIGH**
- Streamlit Docker tutorial https://docs.streamlit.io/deploy/tutorials/docker - `python:3.12-slim`, `HEALTHCHECK` on `/_stcore/health`, `--server.address=0.0.0.0` - **HIGH**
- Streamlit 1.66.0 wheel static assets (downloaded, grepped: "plotly.js v4") - **HIGH**
- Streamlit Community Cloud manage-your-app https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app - 12 h sleep, resource limits (Feb 2024 figures) - **MEDIUM** (limits may have changed)
- Streamlit forum threads on reverse proxies (WebSocket failures are usually base-path/proxy-target issues; Caddy upgrades WS automatically) - **MEDIUM**
- supercronic GitHub README + v0.2.49 release page (https://github.com/aptible/supercronic) - TZ handling, `-overlapping`, `-test`, per-arch sha1 sums - **HIGH**
- GitHub releases API: Caddy v2.11.7 (2026-10-03); Docker Hub tags: `python:3.12-slim` (2026-10-09), `caddy:2.11.x` - **HIGH**
- plotly.py CHANGELOG (7.0.0 / 7.1.0 breaking changes) - **MEDIUM-HIGH** (first 100k chars read)
- SQLite WAL semantics (shared-memory `-shm`, same-host requirement, `query_only`, `BEGIN IMMEDIATE`), Plotly `connectgaps`/NaN gap behaviour, plotly.js tz-offset handling - from established documentation knowledge, not re-fetched today - **MEDIUM** (validate with a 20-line spike: two containers + volume, writer loop + reader loop, and a gap-rendering test)

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
