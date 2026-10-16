# Phase 2: Unattended Operation & Packaging - Research

**Researched:** 2026-10-16
**Domain:** Python long-running scheduler (signals, subprocess supervision, heartbeat) + Docker/Compose packaging of a SQLite-WAL two-container stack, validated statically (no Docker on the dev machine)
**Confidence:** HIGH for scheduler/heartbeat/signal design (prototype run: 33 tests green on a fake clock and with real child processes); MEDIUM for Docker/Compose runtime behaviour (docs + official JSON schema only, no Docker daemon available; real `docker compose up` is Phase 4)

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**Area 1 - Scheduler semantics**

| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | Trigger model | `wbmon scheduler` = long-running Python loop; computes the next hourly slot (top of hour, Europe/Moscow == UTC+3, no DST) as a UTC ISO `YYYY-MM-DDTHH:00:00Z`; sleeps in <= 30 s chunks (prompt SIGTERM handling, robust to clock jumps); at slot time runs `[sys.executable, "-m", "wbmon", "collect", "--trigger", "scheduler", "--slot", <slot>]` via `subprocess.run` (fresh process per run, inherits env), waits with timeout = `run_deadline + 600 s`, logs `event=run_finished slot=... exit=... duration_s=...` | supercronic / systemd timer / APScheduler (documented alternatives only) |
| 2 | Startup / catch-up | On start: if the current slot (floor of now) has no run in DB and `now - slot <= 45 min` -> run immediately for the current slot; never back-fill older slots (honest gaps); if > 45 min -> wait for next slot | back-fill missed hours (rejected: fabricates timing) |
| 3 | Overlap / double run | Relies on Phase 1 guarantees (non-blocking `flock` on `<db>.lock` -> `lock_busy` exit 0; existing `--slot` -> `slot_exists` exit 0). Scheduler itself is single-threaded and never starts a run while one is in flight; if a run finishes after the next slot's grace window, that slot is logged `slot_missed` (not back-filled) | - |
| 4 | Failure policy | Any child exit code != 0, timeout or exception is logged (`event=run_failed`) and the loop continues to the next slot; scheduler never exits on run failure; `restart: unless-stopped` covers scheduler crashes; on child timeout the child gets SIGTERM, then SIGKILL after 30 s | - |

**Area 2 - Liveness & signals**

| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | Heartbeat | Scheduler writes `<db_dir>/scheduler_heartbeat.json` atomically (write tmp + `os.replace`) at start, every loop tick (<= 30 s) and after each run: `{"ts": utc_iso, "pid", "next_slot", "last_slot", "last_exit", "version"}` | DB heartbeat table (rejected: extra writes, schema change) |
| 2 | Healthcheck | `wbmon healthcheck [--max-age 180]` exits 0 iff heartbeat file exists and is younger than max-age seconds (and parses); exits 1 otherwise with a one-line reason; does NOT depend on WB reachability (all-`blocked` runs are healthy) | latest-run age check (kept as extra info in output only) |
| 3 | SIGTERM/SIGINT | Scheduler: sets a stop flag, forwards SIGTERM to an in-flight child, waits up to 60 s, exits 0. `collect` (Phase 1 orchestrator): on SIGTERM finishes the current query transaction, records remaining queries as `error` with `error_class='terminated'` (no requests), sets run status `partial`, exits 0 - so an interrupted run is never left `running` (the next start's `abandon_stale_runs` covers SIGKILL) | - |
| 4 | Logs | Same `key=value` structured stdout lines as Phase 1 (`log.py`), `PYTHONUNBUFFERED=1`; scheduler events: `scheduler_started`, `slot_wait`, `run_started`, `run_finished`, `run_failed`, `slot_missed`, `catch_up`, `shutdown` | JSON logs |

**Area 3 - Packaging (Docker / compose)**

| # | Question | Accepted | Alternatives |
|---|----------|----------|--------------|
| 1 | Image | One `Dockerfile`: `python:3.12-slim`, `PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Moscow`, non-root user `app` uid/gid 10001, `/data` created and chowned (named volume inherits ownership), `pip install --no-cache-dir ".[web]"` (one image for both roles - simpler; size acceptable), `WBMON_DB_PATH=/data/wbmon.sqlite3`, `EXPOSE 8501`, `.dockerignore` excludes `.venv`, `.git`, `.planning`, `data/`, caches | two-target image (rejected: complexity for little gain) |
| 2 | Compose | `compose.yaml` (no `version:` key): `init-db` (one-shot `wbmon init-db`, `restart: "no"`), `collector` (`wbmon scheduler`, `depends_on: init-db: condition: service_completed_successfully`, `restart: unless-stopped`, healthcheck `wbmon healthcheck` interval 60 s, start_period 30 s), `web` (`streamlit run src/wbmon/ui/app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false`, depends_on init-db, `restart: unless-stopped`, healthcheck via `python -c` urllib GET `http://127.0.0.1:8501/_stcore/health`, `ports: "${WEB_PORT:-8501}:8501"`), optional `caddy` service under compose profile `tls` (`caddy:2.11-alpine`, Caddyfile `{$SITE_ADDRESS} { reverse_proxy web:8501 }`, ports 80/443, volumes caddy_data/caddy_config); named volume `wbdata` mounted rw at `/data` in collector, init-db and web (no `:ro` - WAL readers need `-shm`; read-only is enforced by the UI's `mode=ro` connection); logging `json-file` max-size 10m max-file 3; `env_file: .env` optional (`required: false`) | nginx (rejected: manual WS config) |
| 3 | Web entrypoint path | `src/wbmon/ui/app.py` is created by Phase 3; the image installs the package non-editable, so compose runs `streamlit run /app/src/wbmon/ui/app.py` from the copied source tree (WORKDIR `/app`, source copied) | `python -m streamlit run -m wbmon.ui` |
| 4 | Local path & tooling | `Makefile` targets: `install` (venv + `pip install -e ".[dev,web]"`), `test`, `lint`, `init-db`, `collect-once`, `scheduler`, `web`, `up`, `down`, `logs`, `ps`; `.env.example` documents every `WBMON_*` var with defaults and comments (DB_PATH, TOP_N, DEST, SEARCH_URL, REQUEST_DELAY, RUN_DEADLINE, SHEET_CSV_URL, USER_AGENT, TIMEZONE, LOG_LEVEL) plus `WEB_PORT`, `SITE_ADDRESS` | - |

**Testing strategy (locked)**
- Pure functions with injected clock: `current_slot(now)`, `next_slot(now)`, `should_catch_up(now, slot, has_run, grace=45min)`; vectors around :00, :44:59, :45:00, :59:59, day/month/year rollovers (MSK = UTC+3).
- Scheduler loop tested with fake clock/sleep and a fake runner (no real subprocess, no network): catch-up once, no back-fill, failure continues, timeout path, SIGTERM stop flag, heartbeat file content/atomicity, `slot_missed`.
- One real-subprocess smoke test allowed only against a tmp DB with `collect` pointed at a MockTransport-free path that makes no network calls (e.g. run `wbmon healthcheck` / `wbmon --help`) - never live WB/Google.
- Static checks: `compose.yaml` parses as YAML and contains the services/healthchecks/volume above; `Dockerfile` contains `USER app`, `HEALTHCHECK`-free (healthchecks live in compose), `python:3.12-slim`. Docker is NOT installed on the dev machine - the real `docker compose up` is verified in Phase 4 on the host.

### Claude's Discretion
- Exact module name for the scheduler (`scheduler.py`) and healthcheck (`health.py`), Makefile style, Caddyfile location (`deploy/Caddyfile`).

### Deferred Ideas (OUT OF SCOPE)
- Automatic SQLite backups / retention - v2.
- Autoheal sidecar - not needed.

**Scope guards from the orchestrator brief (also binding):** Phase 2 must not create or edit `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**` (Phase 3 owns them, runs in parallel). No anti-bot circumvention; no live requests to Wildberries/Google from any test, Makefile check or executor command; never synthetic data in a real DB.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| SCHED-02 | `wbmon scheduler` triggers a run every hour at :00 Europe/Moscow as a fresh subprocess, runs once at start if the current slot has no run (45-min grace), never back-fills, never overlaps (file lock + UNIQUE slot) | Slot math + vectors (Architecture Patterns 1), Popen-based loop with fake-clock tests (Pattern 2, Code Examples 1-2), Phase 1 `run_lock` / `run_id_for_slot` / `--slot` idempotency reused |
| SCHED-03 | Scheduler survives failed runs and restarts; heartbeat/latest-run signal exposed for healthchecks and the UI | Heartbeat atomic write + ticking during child wait (Pattern 3), `wbmon healthcheck` semantics (Pattern 4), graceful SIGTERM in `collect` (Pattern 5), restart policy + `stop_grace_period` (Pattern 7) |
| OPS-01 | One Docker image; `docker compose up -d` starts init-db, collector, web on one named volume; restart policy; healthchecks; log rotation; Moscow scheduling with UTC storage; tzdata | Dockerfile + compose.yaml reference (Code Examples 6-7), WAL-across-containers spike (Pattern 6), static validation against the vendored official compose-spec schema (Pattern 8) |
| OPS-02 | Same project runs without Docker (venv + CLI); `.env.example` covers every setting | Makefile + `.env.example` reference (Code Examples 8-9), coverage test that every `WBMON_*` name in `config.py` is documented |
</phase_requirements>

## Project Constraints (from CLAUDE.md)

- **Orchestrator Decisions override the stack research text.** `wbmon scheduler` is a small Python loop (sleep until next :00 Europe/Moscow -> `python -m wbmon collect` as a fresh subprocess; file lock + UNIQUE hourly slot; 45-min catch-up for the current slot only; never back-fill). supercronic / systemd timer / APScheduler are documented alternatives only - **do not add them** (so ignore the supercronic Dockerfile/entrypoint sketches in `research/STACK.md`).
- Config is a frozen dataclass from `WBMON_*` env vars - no pydantic / pydantic-settings / python-dotenv.
- Hard constraints: no anti-bot circumvention; no synthetic observations in a real DB (fixtures only in `tests/` and tmp DBs); plain httpx at a polite fixed rate.
- Python >= 3.11 (target 3.12, dev venv is 3.14): scheduler code must be 3.11-compatible (no PEP 695 `type` statements etc.).
- Phase decisions in `.planning/phases/*/NN-CONTEXT.md` win over CLAUDE.md on conflict.
- README / write-up are Russian and belong to Phase 4; code identifiers English. GSD rule: edits happen through GSD execution, not ad hoc.
- Phase 1 guardrail tests apply to every new `src/` file: no third-party imports except `httpx`; no `OR REPLACE`, `proxy=`, `proxies=`, `verify=False`, `x_wbaas`, `_test_only`, `factories` strings anywhere under `src/`; runtime dependencies stay exactly `httpx` + `tzdata`.

## Summary

Phase 2 is two largely independent halves. **(1) Scheduler engine:** a single-threaded Python loop that derives "which hourly slot are we in" from an injected clock on every 1-second tick (so wall-clock jumps cannot skip or duplicate a slot), decides run / already-exists / too-late with three pure functions, supervises one `collect` child at a time with a `Popen`-poll loop (heartbeat keeps ticking during the 5-40 minute run, SIGTERM is forwarded, timeouts escalate SIGTERM -> SIGKILL), and writes an atomic JSON heartbeat that `wbmon healthcheck` and (in Phase 3) the UI read. I prototyped the complete engine plus tests outside the repo: 30 fake-clock tests (slot vectors, startup catch-up, no back-fill, failure continues, overrun -> catch-up or `slot_missed`, timeout and stop escalation, heartbeat gap <= 30 s during a long child, clock step-back without duplicate) and 3 real-process tests (real `Popen` child that exits on SIGTERM, one that ignores SIGTERM and gets SIGKILLed, and a real scheduler process that exits 0 on SIGTERM) all pass; no WB or network involved.

**(2) Packaging:** one `python:3.12-slim` image run as three compose roles on one named volume. The non-obvious parts are the traps, not the YAML: `env_file`/`environment` override the image's `WBMON_DB_PATH` (pin it in compose or the DB silently leaves the volume), exec-form commands (PID 1 must be the Python process to receive SIGTERM), `stop_grace_period` must exceed the scheduler's 60 s child wait, Docker reserves healthcheck exit code 2, the Compose project name decides the volume name, and PyPI / Docker Hub reachability from a Russian VPS is a real Phase 4 risk (third-party reports of pypi.org/Fastly outages in June and the May-2024 Docker Hub block), so the Dockerfile/compose expose mirror hooks (`PYTHON_IMAGE`, `CADDY_IMAGE`, `PIP_INDEX_URL`) and Phase 4 should prefer building the image where those registries are reachable. Without Docker locally, packaging is validated statically: PyYAML parse, a **vendored copy of the official compose-spec JSON schema** (validated with `jsonschema`; negative controls confirmed it rejects typos in `env_file`, `healthcheck`, `depends_on`), a small Dockerfile instruction parser, and `make` guard tests. I confirmed the Streamlit 1.66 `/_stcore/health` endpoint returns `200 ok` and that the planned urllib one-liner exits 0/1 correctly, and that a `mode=ro` + `query_only` reader and a writer process share a WAL directory with zero reader errors (the reader creates `-shm`/`-wal` itself when absent), which is the two-container topology minus the container boundary.

Phase 1 interfaces are consumed read-only except four small additive edits in the final integration plan: `orchestrator.run_once(..., should_stop=...)`, `models.ERROR_CLASSES += "terminated"`, `cli.py` (`scheduler`, `healthcheck`, SIGTERM wiring in `collect`) and the one Phase 1 test that pins `ERROR_CLASSES` (grep before editing).

**Primary recommendation:** Build `schedule.py` (pure) + `health.py` + `signals.py` + `scheduler.py` (Popen-poll loop, 1 s ticks, heartbeat every 30 s) as new files in wave 1, package Docker/compose/Makefile/.env.example with static pytest validation in parallel in wave 1, then wire `scheduler`/`healthcheck`/graceful-SIGTERM into `cli.py` and `orchestrator.py` in wave 2 - and implement the child supervision with `subprocess.Popen` (the engine behind `subprocess.run`), because `run()` cannot keep a heartbeat, forward SIGTERM, or do SIGTERM-then-SIGKILL.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Hourly trigger, catch-up decision, slot_missed | Scheduler process (`wbmon scheduler`) | - | Pure decision functions + single-threaded loop; clock injected |
| Single-flight guarantee (no overlap, no double slot) | Collect process (`run_lock`, UNIQUE `slot_utc`) | Scheduler (never spawns while a child runs) | Defence in depth already in Phase 1; scheduler adds a third layer cheaply |
| Run supervision (timeout, SIGTERM forward, SIGKILL) | Scheduler process | Container runtime (`stop_grace_period`) | Only the parent can talk to its child |
| Graceful interruption of a run | Collect process (orchestrator) | Scheduler (sends SIGTERM) | Only the run knows a query-transaction boundary |
| Liveness signal | Shared volume file `scheduler_heartbeat.json` | Compose healthcheck (collector), UI (Phase 3 reads it) | No schema change; readable from any container mounting `/data` |
| Persistence / history survival across `down`/`up` | Docker named volume `wbdata` (SQLite WAL) | - | Same-host local filesystem, rw mount in all roles |
| Read-only enforcement for the UI | DB connection (`mode=ro` + `query_only`) | - | Volume stays rw so WAL `-shm` works |
| TLS termination / public entry | Caddy (profile `tls`) | Direct published port (fallback) | Optional edge tier; Streamlit stays plain HTTP behind it |
| Static validation of packaging | pytest (PyYAML + compose-spec schema + Dockerfile parser) | `docker compose config -q` when Docker exists | Docker absent on the dev machine |

## Standard Stack

### Core
| Library / image | Version | Purpose | Why Standard |
|-----------------|---------|---------|--------------|
| Python stdlib (`subprocess`, `signal`, `os.replace`, `datetime`, `json`) | 3.11-3.14 | Scheduler, heartbeat, signals | No new runtime dependency (Phase 1 guardrail: runtime deps stay exactly httpx + tzdata). `[VERIFIED: local prototype run]` |
| `python:3.12-slim` | tag pushed 2026-10-09, multi-arch (amd64/arm64/...) | Base image | `[VERIFIED: hub.docker.com API 2026-10-16]` |
| `caddy:2.11-alpine` | tag pushed 2026-10-06 | Optional TLS proxy (`tls` profile) | `[VERIFIED: hub.docker.com API 2026-10-16]` |
| Docker Compose v2 plugin | **>= 2.24.0 required** for `env_file` long syntax with `required: false` | Orchestration | `[CITED: docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/ via search result; docs.docker.com/reference/compose-file/services/#env_file]` |

### Supporting (dev/test only - added to the `dev` extra in pyproject.toml)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| PyYAML | 6.0.3 | Parse `compose.yaml` in pytest (YAML anchors/merge keys resolve under `safe_load`) | Static packaging tests. `[VERIFIED: PyPI JSON API 2026-10-16]` - see Package Legitimacy Audit (seam verdict SUS) |
| jsonschema | 4.26.0 (already in `.venv` transitively via streamlit/altair, but NOT in a bare `[dev]` install used by CI) | Validate `compose.yaml` against the vendored official compose-spec schema | Declare in `dev` so CI does not silently skip the schema test |
| compose-spec JSON schema | main @ 2026-10-16, 89,808 bytes, sha256 `d61cc3df8c6e6a727043e84f3405c69ffd63d341f629db8480f1d2ee5405b10c`, Apache-2.0 | Vendored at `tests/data/compose-spec.json` | Offline structural validation (typo'd keys, wrong shapes). `[VERIFIED: raw.githubusercontent.com/compose-spec/compose-spec/main/schema/compose-spec.json; GitHub API license = Apache-2.0]` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `subprocess.Popen` poll loop | `subprocess.run(timeout=...)` (as worded in CONTEXT) | `run()` blocks (no heartbeat, healthcheck goes red after 180 s of a normal 5-40 min run), cannot SIGTERM-then-SIGKILL (kills with SIGKILL on timeout), cannot forward a stop request. `run()` is itself `Popen` + `communicate`; the intent of the decision is preserved with `Popen`. **Flag to the planner as a deliberate reading of D-Area1-#1.** |
| 1 s sleep ticks | `threading.Event.wait(30)` woken by the handler | `Event.set()` inside a signal handler can self-deadlock if the handler interrupts the same thread inside the Event's lock; plain-bool flag + short ticks has no such window and trivially fakes in tests |
| Vendored compose-spec schema + `jsonschema` | `docker compose config -q` | Needs Docker; keep it as an additional `skipif(no docker)` test (GitHub runners have it) but do not depend on it |
| PyYAML | hand-rolled YAML-ish text assertions | Brittle on anchors/merge keys; PyYAML is the standard |

**Installation (dev only; no runtime change):**
```bash
# pyproject.toml [project.optional-dependencies] dev  ->  add "pyyaml>=6,<7", "jsonschema>=4,<5"
.venv/bin/pip install -e ".[dev,web]"
```

**Version verification:** `pip index versions pyyaml` -> 6.0.3 (PyPI release list since 2011, project URLs `github.com/yaml/pyyaml`, `pyyaml.org`); `jsonschema` 4.26.0 (`github.com/python-jsonschema/jsonschema`). Docker tags checked via the Docker Hub API (see table).

## Package Legitimacy Audit

| Package | Registry | Age | Downloads | Source Repo | Verdict | Disposition |
|---------|----------|-----|-----------|-------------|---------|-------------|
| pyyaml | PyPI | first PyPI release 2011-07-01, 40 releases, latest 6.0.3 (2025-09-25) | n/a (PyPI API exposes no counts; seam reported `unknown-downloads`) | github.com/yaml/pyyaml | **SUS** (seam) - sole reason `unknown-downloads`; no postinstall concept on PyPI | Flagged. Assessment: the canonical, long-established YAML library. Follow the Phase 1 D-A8 precedent (orchestrator decision: automated metadata check, no human checkpoint): the plan's install task first asserts via the PyPI JSON API that `project_urls` contain `github.com/yaml/pyyaml`. **If the orchestrator wants the protocol default, insert a `checkpoint:human-verify` before the install.** |
| jsonschema | PyPI | latest 4.26.0 (2026-01-07), long-established | n/a (`unknown-downloads`) | github.com/python-jsonschema/jsonschema | **SUS** (seam) - same reason; already installed in the dev `.venv` as a dependency of altair/streamlit | Flagged; same disposition as pyyaml (`project_urls` must contain `github.com/python-jsonschema/jsonschema`) |

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** pyyaml, jsonschema (both only for "unknown downloads"; see Disposition - orchestrator to confirm the D-A8 precedent applies)
No runtime packages are added by this phase. Docker images (`python`, `caddy`) are Docker Official Images verified on Docker Hub. `[ASSUMED]` none of the above is discovered via non-authoritative sources: PyYAML/jsonschema are named in official pytest/compose-spec tooling practice and confirmed on PyPI with their GitHub project URLs.

## Architecture Patterns

### System Architecture Diagram

```
                      (host / docker compose up -d)
 init-db (one-shot) ──exit 0──► collector ───────────────► web (Streamlit, Phase 3)
   wbmon init-db     depends_on:   wbmon scheduler    depends_on init-db   │ mode=ro reader
   (idempotent)      completed     PID 1, exec-form                         │  + reads heartbeat
                                      │                                      │
 ┌────────────────────────────────────▼───────────┐                          │
 │ Scheduler loop (1 s ticks, injected clock)      │                          │
 │  now ─► cur = current_slot(now)                 │                          │
 │  cur > handled ?  ─no─► beat(≤30 s) ─► sleep(1)─┘                          │
 │      │yes                                                                   │
 │      ├─ has_run(cur)? ─yes─► log slot_exists                                │
 │      ├─ should_catch_up (late ≤ 45 min)? ─yes─► run_slot(cur)               │
 │      └─ else ─► log slot_missed (never back-fill)                           │
 │  run_slot: Popen(python -m wbmon collect --trigger scheduler --slot cur)    │
 │      poll every tick; heartbeat every ≤30 s; stop/timeout ─► SIGTERM ─►     │
 │      (+30 s timeout / +60 s stop) ─► SIGKILL; log run_finished/run_failed   │
 └───────────────┬─────────────────────────────────┘                          │
                 │ writes tmp + os.replace                                    │
                 ▼                                                            │
        /data/scheduler_heartbeat.json ◄──── wbmon healthcheck (docker, 60 s) ┘
                 ▲
 collect child (fresh process): lock(<db>.lock, LOCK_NB) ─► abandon_stale_runs ─►
   slot exists? ─► exit 0 │ per query: should_stop? ─► record error/terminated
   │ else BEGIN IMMEDIATE… COMMIT ─► finish_run (partial if ≥1 ok) ─► exit 0
                 │
                 ▼
 /data/wbmon.sqlite3 (+ -wal, -shm, .lock) on named volume wbdata (rw in all 3 roles)
 optional:  caddy (profile tls) :80/:443 ─► web:8501
```

### Recommended Project Structure (new/changed files only)
```
src/wbmon/
├── schedule.py        # NEW pure: current_slot, next_slot, should_catch_up (imports datetime + wbmon.clock only)
├── signals.py         # NEW StopFlag + install_stop_handlers() context manager (main-thread guard, restores handlers)
├── health.py          # NEW heartbeat_path, write_heartbeat (atomic), check_heartbeat, run_healthcheck
├── scheduler.py       # NEW Scheduler (injected clock/sleep/spawn/has_run), spawn_collect, build_scheduler, run_scheduler
├── orchestrator.py    # EDIT (wave 2): should_stop kwarg, terminated records
├── models.py          # EDIT (wave 2): ERROR_CLASSES += "terminated"
└── cli.py             # EDIT (wave 2): scheduler, healthcheck subcommands; SIGTERM wiring in collect
Dockerfile  .dockerignore  compose.yaml  Makefile  .env.example  deploy/Caddyfile      # NEW (wave 1)
pyproject.toml                                                                          # EDIT: dev extra only
tests/
├── data/compose-spec.json                    # NEW vendored schema (pinned sha256 asserted)
├── test_schedule.py  test_health.py  test_signals.py  test_scheduler.py  test_scheduler_process.py   # wave 1
├── test_packaging.py                         # wave 1
└── test_orchestrator_terminate.py  test_cli_phase2.py  test_packaging_cross.py                      # wave 2
```
Do not touch `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**`. Do not create README (Phase 4).

### Pattern 1: Slot arithmetic as three pure functions (verified)
**What:** Hourly slots are the top of the **UTC** hour. Because Europe/Moscow is a fixed UTC+3 (no DST since 2014), the top of the MSK hour is the same instant, so no `zoneinfo` math is needed; normalise any aware input to UTC first (a half-hour-offset zone would otherwise floor differently). Reuse Phase 1 `clock.floor_hour/iso_z/parse_iso_z`.
**Rules:** `current_slot(now)` = floor to hour; `next_slot(now)` = floor + 1 h (exactly on `:00:00` the *next* hour is returned, because the current slot is "now"); `should_catch_up(now, slot, has_run, grace=45min)` = `not has_run and 0 <= now - slot <= grace` (inclusive at exactly 45:00; a slot in the future is never run). Naive datetimes raise `ValueError`.
**Verified vectors** (all pass in the prototype; `now` -> `current_slot` / `next_slot`; MSK = +3 h):

| now (UTC) | current_slot | next_slot | note |
|-----------|--------------|-----------|------|
| 2026-10-16T09:00:00Z | 2026-10-16T09:00:00Z | 2026-10-16T10:00:00Z | exactly on :00 |
| 2026-10-16T09:00:01Z | 2026-10-16T09:00:00Z | 2026-10-16T10:00:00Z | |
| 2026-10-16T09:44:59Z | 2026-10-16T09:00:00Z | 2026-10-16T10:00:00Z | catch-up still true |
| 2026-10-16T09:45:00Z | 2026-10-16T09:00:00Z | 2026-10-16T10:00:00Z | catch-up true (inclusive) |
| 2026-10-16T09:45:01Z | same | same | catch-up false (-> `slot_missed` unless it is the start of the process: still false) |
| 2026-10-16T09:59:59Z | 2026-10-16T09:00:00Z | 2026-10-16T10:00:00Z | |
| 2026-10-16T23:59:59Z | 2026-10-16T23:00:00Z | 2026-10-17T00:00:00Z | UTC day rollover (= 02:59:59 MSK, MSK day 17 starts at 21:00Z) |
| 2026-12-31T23:30:00Z | 2026-12-31T23:00:00Z | 2027-01-01T00:00:00Z | year rollover |
| 2026-02-28T23:30:00Z | 2026-02-28T23:00:00Z | 2026-03-01T00:00:00Z | month rollover, non-leap |
| 2028-02-28T23:30:00Z | 2028-02-28T23:00:00Z | 2028-02-29T00:00:00Z | leap day |
| 2028-02-29T23:00:00Z | 2028-02-29T23:00:00Z | 2028-03-01T00:00:00Z | |
| 2026-03-29T00:59:59Z | 2026-03-29T00:00:00Z | 2026-03-29T01:00:00Z | EU DST day: no effect |
| MSK-aware `2026-10-16T12:34:56+03:00` | 2026-10-16T09:00:00Z | | aware non-UTC input |

Also assert `ZoneInfo("Europe/Moscow").utcoffset(...) == timedelta(hours=3)` on 2026-03-29, 2026-07-01, 2026-10-25, 2027-01-01 (guards the "no DST" premise; needs the `tzdata` dependency already present).

### Pattern 2: Scheduler loop = state machine over an injected clock
**What:** One loop, no precomputed sleep durations: every tick recompute `cur = current_slot(now())`. `handled` (in memory) remembers the last slot processed; act only when `cur > handled` (string compare works for `...Z` ISO). This one rule yields: startup catch-up, on-time hourly runs, late start after an overrun, `slot_missed`, immunity to NTP step-back (no duplicate) and to VM suspend/resume (only the *current* slot is considered - never back-fill).
**Decision table at a new slot:** `has_run(cur)` -> log `slot_exists`; elif `should_catch_up` -> (log `catch_up` if first iteration or > 60 s late) `run_slot`; else log `slot_missed`. Always followed by `slot_wait`.
**Injection points** (all required constructor args so a test can never spawn a real `collect`): `spawn(slot) -> Child`, `now()`, `sleep(s)`, `monotonic()`, `has_run(slot)`. Timing knobs as dataclass fields with module defaults (`tick_s=1.0`, `heartbeat_every_s=30.0`, `term_grace_s=30.0`, `stop_grace_s=60.0`) instead of monkeypatching module globals.
**Production wiring:** `build_scheduler(settings)`: `init_db(settings.db_path)`; `has_run = lambda slot: db lookup via connect_ro + reader.run_id_for_slot, any sqlite3.Error -> False` (the child is idempotent via `--slot`, so a false negative costs one 1-second process); `child_timeout_s = settings.run_deadline + 600`; `heartbeat_file = health.heartbeat_path(settings.db_path)`.
**Overrun behaviour (verified):** run for 10:00 lasting 100 min -> ends 11:40 -> slot 11:00 is 40 min late -> catch-up run; lasting 110 min -> ends 11:50 -> `slot_missed`, next run 12:00.
**Interrupted slots stay gaps:** a run killed or SIGTERM-interrupted at 10:20 leaves a `partial`/`abandoned` row *with* `slot_utc` = 10:00 (UNIQUE); a restart at 10:25 sees `slot_exists` and does not re-run it. This is the honest-gap rule, not a bug - state it in the README (Phase 4).

### Pattern 3: Heartbeat (atomic) that keeps ticking during a run
`write_heartbeat`: create the tmp file **in the same directory** (`os.replace` is atomic only within one filesystem; a tmp in `/tmp` would not be), `os.replace(tmp, path)`, `finally: tmp.unlink(missing_ok=True)`. No `fsync` needed (a torn/missing file only means "unhealthy until the next beat"). Payload exactly the CONTEXT keys: `ts` (iso_z of `now()`), `pid`, `next_slot`, `last_slot`, `last_exit`, `version`. Beat at start (before the loop), at most every 30 s inside the loop **and inside the child-wait loop**, and forced after each run. A heartbeat write `OSError` is logged (`heartbeat_failed`) and swallowed. **Contract for Phase 3:** path `<dirname(WBMON_DB_PATH)>/scheduler_heartbeat.json`, `ts` in `YYYY-MM-DDTHH:MM:SSZ`.

### Pattern 4: `wbmon healthcheck [--db PATH] [--max-age 180]`
Exit 0 iff the file exists, is a JSON object, has a parseable ISO-Z `ts`, and `0 <= age <= max_age` (a `ts` more than 60 s in the future is unhealthy: clock skew/garbage). Otherwise exit 1 with one line on **stdout** (Docker stores healthcheck stdout/stderr for `docker inspect`). **Never exit 2**: Docker reserves exit code 2 for healthchecks `[CITED: docs.docker.com/reference/dockerfile/#healthcheck]`, so map `ConfigError`/usage failures to 1 for this subcommand. Do not look at WB, run statuses or the DB for the verdict (all-`blocked` is healthy). Optional extra info only: best-effort `last_run=<status>@<started_at>` appended to the OK line, wrapped so any DB error is ignored. Compose: `interval 60s`, `timeout 10s`, `retries 3`, `start_period 30s`. A stale heartbeat file surviving `down`/`up` is correct: the new scheduler overwrites it within its first second and `start_period` absorbs the gap.

### Pattern 5: Graceful SIGTERM in `collect` (and how to test without network)
- `signals.py`: `StopFlag` (plain `requested: bool`, `signum`) and `install_stop_handlers(flag, signums)` - no-op outside the main thread; saves previous handlers and **restores them in `finally`** (treat a `None` previous handler as `SIG_DFL`).
- `cli.py collect`: `with install_stop_handlers(flag, (signal.SIGTERM,)): run_once(..., should_stop=lambda: flag.requested)`. **SIGTERM only** - keep SIGINT default so the existing Phase 1 behaviour/test (`KeyboardInterrupt` -> run marked `error`) is unchanged.
- `orchestrator.run_once(..., should_stop: Callable[[], bool] = lambda: False)`: (a) check after acquiring the lock/abandon/slot checks and again right after `load_queries` (the sheet fetch can take seconds) and **before `upsert_queries`/`start_run`**: if set, log `event=terminated phase=before_start`, return `None`, create no run; (b) at the top of each per-query iteration, **before** the deadline and breaker branches: if set, record `QueryResult(status="error", error_class="terminated", error_message="SIGTERM received; no request sent", started_at=iso_z(now()), duration_ms=0)` for every remaining query (full attempt log, no requests) and log `event=terminated run_id=… remaining=N` once. `finish_run` derivation then yields `partial` when >= 1 query was ok/partial; with **zero** ok queries it yields `error` (not `partial`) - see Open Question 1. Exit code 0.
- `models.ERROR_CLASSES` gains `"terminated"` (no DDL CHECK exists on `error_class` - verified in the frozen migration-1 DDL - so no migration). Grep `tests/` for tests pinning the 19 names before editing.
- **Offline tests (no subprocess needed):** call `cli.main(["collect", "--limit-queries", "3", "--top-n", "5"], _transport=MockTransport(fake), _sleep=RecordingSleep())` where a `FakeWB` script item for query 1 is a **callable that does `signal.raise_signal(signal.SIGTERM)` and returns the page** (verified: with the handler installed the flag is set synchronously; handler restored afterwards). Assert: exit 0; run `partial`; Q1 `ok` with 5 items; Q2/Q3 `error`/`terminated` with 0 items; exactly 1 WB request; `signal.getsignal(SIGTERM)` restored; stdout has `event=terminated`. A second test passes `should_stop=lambda: True` directly -> `None`, no run row, 0 requests. A third: `should_stop` flips true after N `query_done` (orchestrator-level, no signals).
- Worst-case time to honour SIGTERM = the in-flight query (<= 2 pages x 3 attempts x 30 s timeouts + pacing ~ 200 s). The scheduler waits only 60 s then SIGKILLs; the run is then left `running` and the next start's `abandon_stale_runs` fixes it. Acceptable and documented; do not add cancellation inside `WbClient`.

### Pattern 6: SQLite WAL on a shared named volume (two containers)
Verified locally (processes, not containers): a `mode=ro` + `query_only` reader (`connect_ro`) opened while **no** `-wal`/`-shm` existed succeeded and created them (so the volume/directory must be writable by the reader's uid), rejected writes ("attempt to write a readonly database"), and read 300 rows appearing from a concurrent writer process with 0 errors. SQLite docs: a read-only WAL database can be read if the `-shm`/`-wal` files exist, **or the directory is writable so they can be created**, or the DB is immutable `[CITED: sqlite.org/wal.html, section 5 "Read-Only Databases"]`; all processes must be on the same host, no network filesystems `[CITED: sqlite.org/wal.html]`. Consequences for compose: named volume (never bind-mount a network/macOS path), **rw in `web`** (no `:ro`), **same uid 10001 in every image role**, `/data` pre-created and chowned in the image so the first mount inherits it. A long-lived reader can stall WAL checkpoints `[CITED: sqlite.org/wal.html section 6]` - Phase 3 already mandates short-lived connections; Phase 1 does a best-effort `wal_checkpoint(TRUNCATE)` at run end.
Not provable here (Phase 4 on the host): that Docker's first-mount copy-up preserves `app:app` ownership (docs say contents are copied but are silent on ownership `[CITED: docs.docker.com/engine/storage/volumes/]`), and cross-container `-shm` mmap sharing. Make failure loud: `init-db` runs as uid 10001 first, so a wrong-owner volume fails `init-db` and (via `service_completed_successfully`) blocks the other two instead of corrupting anything.

### Pattern 7: Compose semantics that matter here
- `stop_grace_period` default is 10 s `[CITED: docs.docker.com/reference/compose-file/services/]`; the scheduler waits up to 60 s for its child -> set `collector.stop_grace_period: 90s` (CONTEXT does not fix it; Claude's discretion).
- **Exec-form `command:` lists only** (a string command runs under `sh -c`, which becomes PID 1 and swallows SIGTERM). Same for Dockerfile `CMD`. Static test asserts `isinstance(command, list)`.
- `environment:` beats `env_file:` beats image `ENV` `[CITED: docs.docker.com/reference/compose-file/services/#env_file]`. Pin `WBMON_DB_PATH=/data/wbmon.sqlite3` (and `TZ`) in compose `environment` for init-db/collector/web so a copied `.env` cannot move the database off the volume.
- `env_file: [{path: .env, required: false}]` needs Compose >= 2.24.0 (older: parse error). `make up` also does `test -f .env || cp .env.example .env`. Phase 4 host check: `docker compose version`.
- `.env` in the project directory is *also* read automatically for `${VAR}` interpolation (`WEB_PORT`, `SITE_ADDRESS`, `PYTHON_IMAGE`, `COMPOSE_PROFILES`); the `env_file` entry is what injects `WBMON_*` into containers. One file serves both.
- Top-level `name: wb-serp-monitor` pins the project name; otherwise it derives from the directory name, which determines the volume name (`<project>_wbdata`) - a renamed checkout directory on the host would silently start with an empty volume. The dev directory name is Cyrillic with spaces and a trailing dot, which Compose would have to sanitise.
- `depends_on.condition: service_completed_successfully` for init-db; Caddy uses plain `depends_on: [web]` (not `service_healthy`, so a Streamlit problem cannot also take down the proxy).
- One shared `build:` + `image: wb-serp-monitor:local` via a YAML anchor in all three roles (a service with only `image:` and no `build:` triggers a pull attempt if the tag does not exist yet). BuildKit caching makes the repeated build instant.
- `down` must never get `-v`/`--volumes` (history is the product). `COMPOSE_PROFILES=tls` in `.env` or `docker compose --profile tls up -d` enables Caddy.

### Pattern 8: Static validation without Docker
Layered, all in `tests/test_packaging.py` (verified on drafts: 7 tests pass; negative controls caught typo'd `restart_polcy`, bad `env_file` shapes, misspelled healthcheck keys, invalid `depends_on.condition`):
1. `yaml.safe_load(compose.yaml)` + `Draft202012Validator(vendored compose-spec).iter_errors` -> no errors (structure/typos). Values that the schema leaves free (e.g. `restart` strings) are asserted explicitly.
2. Hand assertions on topology (services, volumes, `required: false`, pinned `WBMON_DB_PATH`, rw mounts, healthcheck shapes, `stop_grace_period >= 75 s`, `command` is a list, no `:latest`, no `version:` key, `name:` present) - `${VAR:-default}` resolved by a tiny regex helper.
3. Dockerfile: tiny instruction parser (join `\` continuations, drop comments) and assertions (first instruction `ARG PYTHON_IMAGE=python:3.12-slim`, single `FROM ${PYTHON_IMAGE}`, ENV set, `useradd --uid 10001`, `chown app:app /data`, `pip install --no-cache-dir ".[web]"`, last `USER app` after all `RUN`s, `EXPOSE 8501`, no `HEALTHCHECK`, exec-form `CMD`, no `# syntax=` line, build-time smoke `RUN` using `load_bundled`).
4. `.dockerignore` allowlist shape, and a cross-check that `pyproject.toml` has no `readme`/`license-files` key that the allowlist would exclude (hatch would fail the image build).
5. `.env.example` coverage: every `WBMON_[A-Z_]+` literal found in `src/wbmon/config.py` appears as `NAME=` (commented or not); no live `WBMON_DB_PATH` line; no `$` in live lines.
6. Makefile: `down` recipe has no `-v`; `make collect-once` and `make scheduler` **without** `CONFIRM=yes` exit non-zero with the refusal text (executed for real - safe, the guard runs first); `make -n up` shows `docker compose up -d --build`. `skipif(shutil.which("make") is None)`.
7. Optional `skipif(shutil.which("docker") is None)`: `docker compose config -q` (runs automatically on GitHub runners). hadolint is unavailable locally; do not depend on it.
8. Cross-check (wave 2, needs the CLI): every `wbmon <sub>` in compose `command`/`healthcheck` is a registered subcommand of `build_parser()` and the flags parse.

### Anti-Patterns to Avoid
- **`subprocess.run` for the run supervision** (no heartbeat/forwarding/escalation) - use Popen + poll.
- **`time.sleep(until_next_slot)` or one long sleep** - a SIGTERM handler returns and PEP 475 resumes the sleep, delaying shutdown by up to the whole sleep; clock steps break it. Tick at 1 s.
- **`Event.set()`/locks/logging inside the signal handler** - set a bool only.
- **String `command:`/`CMD` (shell form)** - PID 1 becomes `sh`.
- **`COPY .streamlit ./.streamlit` by name** - fails the whole build if Phase 3 has not created the folder yet; `COPY . /app` over an allowlist `.dockerignore` copies only what exists.
- **`# syntax=docker/dockerfile:1`** - makes BuildKit pull the `docker/dockerfile` frontend image from Docker Hub (an extra registry dependency on a restricted network); no feature used here needs it.
- **Running the real `spawn_collect` or `make collect-once CONFIRM=yes` from any test or executor step** - that is a live WB request. The only places that name `wbmon collect` are the `spawn_collect` argv (asserted by monkeypatching `subprocess.Popen`, never invoked) and the guarded Makefile targets.
- **Healthcheck that depends on WB / latest run status** - blocked WB is a normal steady state.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic file replace | custom locking/rename dance | `os.replace(tmp_in_same_dir, final)` | Atomic on POSIX; readers never see a torn heartbeat |
| Single-flight / overlap protection | scheduler-side PID files or DB flags | Phase 1 `run_lock` (flock) + `runs.slot_utc UNIQUE` + `--slot` | Kernel releases on death; already tested in Phase 1 |
| Stale-run cleanup | scheduler-side `UPDATE runs` | Phase 1 `abandon_stale_runs` (runs under the lock in `collect`) | Unsafe without the lock (Phase 1 Pitfall 6) |
| Compose validation | regex over YAML text | PyYAML + official compose-spec JSON schema (`jsonschema`) | Catches structural typos Docker would only report at deploy |
| Log format / line safety | new formatter | `wbmon.log.log_event` / `fmt_value` | Quotes values; already the Phase 1 contract |
| Timezone math for slots | `zoneinfo` conversions | UTC floor (MSK = UTC+3, no DST), with a test asserting the offset | Fewer moving parts; slots are stored UTC anyway |
| TLS/ACME | nginx + certbot | Caddy `{$SITE_ADDRESS} { reverse_proxy web:8501 }` | Decided in CONTEXT; WebSocket upgrade is automatic |
| Cron/scheduling frameworks | APScheduler, supercronic | the ~120-line loop | Locked by CLAUDE.md (documented alternatives only) |

**Key insight:** every hard correctness property here (no overlap, no duplicate slot, no lost history, crash recovery) is already enforced by Phase 1 at the database/lock level, so the scheduler may be a thin, dumb, restartable supervisor - which is exactly what makes it testable on a fake clock.

## Common Pitfalls

### Pitfall 1: Heartbeat goes stale during a normal run
**What goes wrong:** blocking on `subprocess.run` for 5-40 minutes; healthcheck max-age is 180 s -> collector flagged unhealthy mid-run.
**How to avoid:** Popen + poll loop; beat every <= 30 s inside it; a test with a 30-minute fake child asserts max gap between beats <= 31 s (prototype: passes).
**Warning signs:** `docker compose ps` shows `unhealthy` only while a run is in progress.

### Pitfall 2: SIGTERM arrives but shutdown takes 30 s (or Docker SIGKILLs at 10 s)
**What goes wrong:** (a) long `time.sleep` resumed after the handler (PEP 475); (b) default `stop_grace_period` 10 s < scheduler's 60 s child wait.
**How to avoid:** 1 s ticks; `collector.stop_grace_period: 90s`; test asserts the loop exits within a couple of ticks of `stop.request()`.

### Pitfall 3: Healthcheck exit code 2
**What goes wrong:** `wbmon` returns 2 on config/usage errors; Docker reserves 2 for healthchecks.
**How to avoid:** map to 1 inside `healthcheck`; unit test with a broken `WBMON_TOP_N`.

### Pitfall 4: `.env` silently moves the database off the volume
**What goes wrong:** user copies `.env.example` with `WBMON_DB_PATH=data/wbmon.sqlite3` uncommented; `env_file` overrides the image `ENV`; DB lands in the container layer; history is lost on recreate.
**How to avoid:** compose `environment: WBMON_DB_PATH: /data/wbmon.sqlite3` (wins over env_file) for all three roles; `.env.example` ships `WBMON_DB_PATH` commented with a DOCKER warning; tests for both.

### Pitfall 5: Volume name depends on the checkout directory
**How to avoid:** top-level `name: wb-serp-monitor`; asserted by test. `docker compose down` keeps volumes; `-v` destroys history - Makefile `down` and docs say never.

### Pitfall 6: Package data missing from the wheel in the image
**What goes wrong:** hatchling honours `.gitignore`; a bare `data/` rule excludes `src/wbmon/data/queries_bundled.txt`, the bundled-queries fallback (QRY-03) then fails only inside the container.
**Current state:** the repo `.gitignore` already contains `!src/wbmon/data/`; I built the wheel from a copy and the file is included `[VERIFIED: pip wheel on a scratch copy, 2026-10-16]`. The `.dockerignore` allowlist does not copy `.gitignore`, so hatch includes everything in the package anyway.
**How to avoid:** keep the Dockerfile build-time smoke (`python -c "from wbmon.queries import load_bundled; assert len(load_bundled()) == 70"`) - it fails the image build instead of the 3 a.m. run - plus the static test that asserts it exists. (Phase 1 owns `load_bundled`; if its name changes, update the smoke.)

### Pitfall 7: Streamlit cannot find its config / cannot write HOME
**What goes wrong:** Streamlit reads `./.streamlit/config.toml` relative to the **working directory**; as non-root with no home it may fail to write `~/.streamlit`.
**How to avoid:** `WORKDIR /app` with `.streamlit` copied there (allowlist `.dockerignore`); `useradd --create-home --home-dir /home/app`. Compose runs the script as `/app/src/wbmon/ui/app.py`; `import wbmon` resolves to the **site-packages** copy (cwd `/app` does not contain a `wbmon/` directory), so both copies being present is harmless - but the app must use package imports (`from wbmon.ui import data`), not sibling-module imports.

### Pitfall 8: `web` crash-loops until Phase 3 exists
`restart: unless-stopped` + missing `/app/src/wbmon/ui/app.py` = restart loop. Phase 2 acceptance must not require `web` to be healthy; static tests only. The full three-service `compose up` is Phase 4.

### Pitfall 9: Registries unreachable from a Russian VPS (Phase 4 risk)
- Docker Hub: blocked for Russian users on 2024-05-30 (GeoIP, US export controls); reports of restoration within days are conflicting; anonymous pulls are rate-limited per IP (100 per 6 h) `[CITED: habr.com/ru/companies/sberbank/news/819057; tproger.ru article; unverified for 2026 -> check on the host]`.
- PyPI: third-party reports (June 2026) that pypi.org/files.pythonhosted.org (Fastly CDN, no Russian PoP) dropped connections for hours; community mirrors named (e.g. pypi.depkit.ru) are single-author claims `[CITED: habr.com/ru/articles/1043976; LOW confidence]`.
- Mitigation baked into the artifacts: `ARG PYTHON_IMAGE`, `ARG PIP_INDEX_URL`, compose `${CADDY_IMAGE:-caddy:2.11-alpine}`; no GitHub downloads at build (supercronic dropped); no `# syntax=` pull. Phase 4 plan: probe `docker pull python:3.12-slim` and `pip download pyyaml` on the host first; if either fails, **build the image on a machine that can reach them** and ship it: `docker save wb-serp-monitor:local | gzip | ssh host 'gunzip | docker load'` (and the same for `caddy`), then `docker compose up -d --no-build`. Consider a Makefile target `up-prebuilt` for that.

### Pitfall 10: Direct port stays public next to Caddy
`ports: "${WEB_PORT:-8501}:8501"` publishes Streamlit on all interfaces even with the `tls` profile (Docker bypasses ufw). For a TLS deployment set `WEB_PORT` to a loopback-only mapping or accept it; see Open Question 3.

### Pitfall 11: Test hazards specific to this phase
- A scheduler test that uses the default `spawn` would start a real `collect` (live WB). Make `spawn` a required constructor argument; test `build_scheduler(...).spawn is spawn_collect` without calling it, and assert `spawn_collect`'s argv by monkeypatching `subprocess.Popen`.
- Phase 1's autouse `_isolated` fixture patches `socket` **in-process only**; child processes are not protected. Real-process tests may only run `python -c` helper children (no `wbmon collect`), and a scheduler harness whose `spawn` is a sleeping `python -c`.
- Real-process tests need readiness signals (child prints/touches a file) and generous `wait(timeout=…)`, not bare `sleep`s.
- A Phase 1 test may monkeypatch `wbmon.cli.run_once` with a strict signature; adding `should_stop=` to the call can break it - grep `tests/` for `run_once` monkeypatches first.

### Pitfall 12: Shell-compat of `.env`
The same file is parsed by Compose and `. ./.env` in the Makefile. Rules: `KEY=value` lines, no inline comments, no `$`, double-quote values with spaces/`;`. Ship every setting commented-out so an untouched copy equals the defaults (the User-Agent default is derived in code - leave `WBMON_USER_AGENT` unset).

## Code Examples

> All code below was executed in a scratch prototype (outside the repo) unless marked "sketch". It is behaviour-verified but **not ruff-formatted** to the repo's 100-column rule - the executor must run `ruff check` / fix long lines. Names follow the Phase 1 interfaces (`wbmon.clock`, `wbmon.log`).

### 1. `src/wbmon/schedule.py` (pure)
```python
"""Pure hourly-slot arithmetic (no I/O). MSK = UTC+3 with no DST, so MSK top-of-hour == UTC top-of-hour."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from wbmon.clock import floor_hour, iso_z, parse_iso_z

SLOT_LENGTH = timedelta(hours=1)
CATCH_UP_GRACE = timedelta(minutes=45)

def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("naive datetime is not allowed")
    return now.astimezone(timezone.utc)

def current_slot(now: datetime) -> str:
    return iso_z(floor_hour(_utc(now)))

def next_slot(now: datetime) -> str:            # strictly after now: on :00:00 returns the next hour
    return iso_z(floor_hour(_utc(now)) + SLOT_LENGTH)

def should_catch_up(now: datetime, slot: str, has_run: bool, grace: timedelta = CATCH_UP_GRACE) -> bool:
    late = _utc(now) - parse_iso_z(slot)
    return (not has_run) and timedelta(0) <= late <= grace
```

### 2. `src/wbmon/health.py`
```python
from __future__ import annotations
import json, os
from datetime import datetime
from pathlib import Path
from wbmon.clock import parse_iso_z

HEARTBEAT_FILENAME = "scheduler_heartbeat.json"
DEFAULT_MAX_AGE_S = 180.0
MAX_FUTURE_SKEW_S = 60.0

def heartbeat_path(db_path: str | Path) -> Path:
    return Path(db_path).parent / HEARTBEAT_FILENAME

def write_heartbeat(path: Path, payload: dict) -> None:
    """tmp file in the SAME directory + os.replace: readers see the old or the new file, never a torn one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def check_heartbeat(path: Path, now: datetime, max_age_s: float = DEFAULT_MAX_AGE_S) -> tuple[bool, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, f"heartbeat file missing: {path}"
    except OSError as exc:
        return False, f"heartbeat unreadable: {exc.__class__.__name__}"
    try:
        data = json.loads(raw)
        ts = parse_iso_z(data["ts"])
    except (ValueError, KeyError, TypeError):
        return False, "heartbeat is not valid JSON with an ISO-Z 'ts'"
    age = (now - ts).total_seconds()
    if age < -MAX_FUTURE_SKEW_S:
        return False, f"heartbeat timestamp is {-age:.0f}s in the future"
    if age > max_age_s:
        return False, f"heartbeat is stale: age={age:.0f}s max={max_age_s:g}s"
    return True, (f"healthy age_s={max(age, 0):.0f} pid={data.get('pid')} "
                  f"last_slot={data.get('last_slot')} last_exit={data.get('last_exit')}")
```
`run_healthcheck(settings, max_age, now) -> int` (sketch): `ok, msg = check_heartbeat(...)`; `print(msg)`; return `0 if ok else 1`. In `cli.main`, any non-0/1 return for the `healthcheck` command (e.g. 2 from `ConfigError`) is converted to 1.

### 3. `src/wbmon/signals.py` (sketch, handler behaviour verified)
```python
import contextlib, signal, threading

class StopFlag:
    def __init__(self) -> None:
        self.requested = False
        self.signum: int | None = None
    def request(self, signum: int | None = None) -> None:
        self.requested = True
        self.signum = signum

@contextlib.contextmanager
def install_stop_handlers(flag: StopFlag, signums=(signal.SIGTERM,)):
    if threading.current_thread() is not threading.main_thread():
        yield flag                                   # signal.signal() only works in the main thread
        return
    previous = {s: signal.getsignal(s) for s in signums}
    for s in signums:
        signal.signal(s, lambda n, _frame: flag.request(n))
    try:
        yield flag
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler if handler is not None else signal.SIG_DFL)
```

### 4. `src/wbmon/scheduler.py` (core; `StopFlag` moves to `signals.py`)
```python
TICK_S, HEARTBEAT_EVERY_S, CHILD_TERM_GRACE_S, STOP_GRACE_S, CHILD_TIMEOUT_MARGIN_S = 1.0, 30.0, 30.0, 60.0, 600.0

class Child(Protocol):
    pid: int
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...

def spawn_collect(slot: str) -> Child:
    """Fresh process per run (inherits env + stdout). Own session: a terminal Ctrl-C reaches only the scheduler."""
    return subprocess.Popen(
        [sys.executable, "-m", "wbmon", "collect", "--trigger", "scheduler", "--slot", slot],
        stdin=subprocess.DEVNULL, start_new_session=True)

@dataclass
class Scheduler:
    child_timeout_s: float
    heartbeat_file: Path
    spawn: Callable[[str], Child]            # REQUIRED: tests can never spawn a real collect by accident
    now: Callable[[], datetime]
    sleep: Callable[[float], None]
    monotonic: Callable[[], float]
    has_run: Callable[[str], bool]
    stop: StopFlag = field(default_factory=StopFlag)
    grace: timedelta = CATCH_UP_GRACE
    last_slot: str | None = None
    last_exit: int | None = None
    _last_beat: float = float("-inf")

    def beat(self, *, force: bool = False) -> None:
        t = self.monotonic()
        if not force and t - self._last_beat < HEARTBEAT_EVERY_S:
            return
        self._last_beat = t
        now = self.now()
        payload = {"ts": iso_z(now), "pid": os.getpid(), "next_slot": next_slot(now),
                   "last_slot": self.last_slot, "last_exit": self.last_exit, "version": __version__}
        try:
            write_heartbeat(self.heartbeat_file, payload)
        except OSError as exc:               # a full disk must not kill the scheduler
            log_event("heartbeat_failed", level=logging.WARNING, error=f"{type(exc).__name__}: {exc}")

    def run_slot(self, slot: str) -> None:
        t0 = self.monotonic()
        try:
            child = self.spawn(slot)
        except OSError as exc:
            self.last_slot, self.last_exit = slot, None
            log_event("run_failed", level=logging.ERROR, slot=slot, reason="spawn_error", error=str(exc))
            return
        log_event("run_started", slot=slot, pid=child.pid)
        term_at: float | None = None
        reason: str | None = None
        while (rc := child.poll()) is None:
            t = self.monotonic()
            if term_at is None:
                if self.stop.requested:
                    reason = "shutdown"
                elif t - t0 > self.child_timeout_s:
                    reason = "timeout"
                if reason:
                    child.terminate()
                    term_at = t
            elif t - term_at > (STOP_GRACE_S if reason == "shutdown" else CHILD_TERM_GRACE_S):
                child.kill()
                term_at = float("inf")       # escalate exactly once
            self.beat()                      # heartbeat keeps ticking while the child runs
            self.sleep(TICK_S)
        self.last_slot, self.last_exit = slot, rc
        log_event("run_finished", slot=slot, exit=rc, duration_s=round(self.monotonic() - t0, 1))
        if rc != 0 or reason == "timeout":
            log_event("run_failed", level=logging.ERROR, slot=slot, exit=rc, reason=reason or "exit_code")
        self.beat(force=True)

    def run_forever(self) -> int:
        log_event("scheduler_started", version=__version__, pid=os.getpid(),
                  grace_min=int(self.grace.total_seconds() // 60), child_timeout_s=self.child_timeout_s)
        self.beat(force=True)
        handled: str | None = None
        while not self.stop.requested:
            now = self.now()
            cur = current_slot(now)
            if handled is None or cur > handled:   # ISO-Z sorts chronologically; a clock step back never re-runs
                first, handled = handled is None, cur
                try:
                    late = int((now - parse_iso_z(cur)).total_seconds())
                    if self.has_run(cur):
                        log_event("slot_exists", slot=cur)
                    elif should_catch_up(now, cur, False, self.grace):
                        if first or late > 60:
                            log_event("catch_up", slot=cur, late_s=late)
                        self.run_slot(cur)
                    else:
                        log_event("slot_missed", level=logging.WARNING, slot=cur, late_s=late)
                except Exception as exc:           # noqa: BLE001 - the loop must outlive any failure
                    log_event("run_failed", level=logging.ERROR, slot=cur, reason="exception",
                              error=f"{type(exc).__name__}: {exc}")
                if self.stop.requested:
                    break
                log_event("slot_wait", next_slot=next_slot(self.now()))
            self.beat()
            self.sleep(TICK_S)
        log_event("shutdown", signum=self.stop.signum, last_slot=self.last_slot)
        return 0
```
Production entry (sketch): `run_scheduler(settings)`: `configure`d logging already; `init_db(settings.db_path)`; `sched = build_scheduler(settings)`; `with install_stop_handlers(sched.stop, (signal.SIGTERM, signal.SIGINT)): return sched.run_forever()`. The handler lives only in the CLI/entry layer, never inside `Scheduler`.

### 5. Fake-clock test harness (sketch of the verified approach)
```python
class Clock:                                   # sleep() advances both wall and monotonic time
    def __init__(self, start): self.t = datetime.fromisoformat(start); self.mono = 0.0
    def now(self): return self.t
    def sleep(self, s): self.t += timedelta(seconds=s); self.mono += s
    def monotonic(self): return self.mono

class FakeChild:                               # finishes after `runtime` fake seconds; can ignore SIGTERM
    def __init__(self, clock, runtime, rc=0, ignore_term=False, on_term_rc=0): ...
    def poll(self): ...                        # None until runtime elapsed / terminated / killed (-9)
    def terminate(self): self.signals.append("term")
    def kill(self): self.signals.append("kill")
```
Tests that passed: startup at 09:20 -> catch-up 09:00 then 10:00, 11:00, 12:00 each started exactly on the slot; start 09:50 waits for 10:00; `has_run` slot skipped; rc 1 then 3 then 0 -> loop continues; 100-min overrun -> catch-up, 110-min -> `slot_missed`; child timeout -> `["term", "kill"]` for a child that ignores SIGTERM; stop with SIGTERM-ignoring child -> `["term", "kill"]` after >= 60 s; graceful child -> `["term"]` only; heartbeat gaps <= 31 s over a 30-minute child and `check_heartbeat` OK at the end; clock stepped back 1 h mid-run -> no duplicate slot.
Real-process tests (also passed): child `python -c` that exits 0 on SIGTERM -> `last_exit == 0`; child ignoring SIGTERM with `stop_grace_s=0.5` -> `last_exit == -9`; a harness process running the scheduler (fake pinned `now`, sleeping child) exits 0 within 10 s of `os.kill(pid, SIGTERM)` and has written the heartbeat with its pid.

### 6. `Dockerfile` (validated by the static tests)
```dockerfile
# WB SERP Monitor - one image, three roles (init-db / collector / web); the role is the compose `command`.
# No "# syntax=" line on purpose: it would pull docker/dockerfile from Docker Hub at build time.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow \
    WBMON_DB_PATH=/data/wbmon.sqlite3

# Same uid in every container: SQLite WAL needs all processes to create/open -wal/-shm in /data.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown app:app /data

WORKDIR /app
# .dockerignore is an allowlist (pyproject.toml, src, .streamlit), so this copies only what the image needs.
COPY . /app

# Mirror hook for hosts that cannot reach pypi.org (pip reads PIP_INDEX_URL from the environment).
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir ".[web]" \
 && wbmon --help > /dev/null \
 && python -c "from wbmon.queries import load_bundled; assert len(load_bundled()) == 70"

USER app
EXPOSE 8501
CMD ["wbmon", "scheduler"]
```
`.dockerignore`:
```
# Allowlist: everything is excluded unless re-included below.
*
!pyproject.toml
!src
!.streamlit
# Never ship caches, even from inside allowed directories.
**/__pycache__
**/*.pyc
**/*.sqlite3*
```
(CONTEXT lists `.venv`, `.git`, `.planning`, `data/`, caches as excluded - the allowlist excludes all of them by construction and is robust to new junk. If Phase 4 adds `readme`/`license-files` to `pyproject.toml`, add them to the allowlist - the static test enforces this.)

### 7. `compose.yaml` (validated: 0 schema errors, negative controls reject typos)
```yaml
# WB SERP Monitor - one `docker compose up -d` starts init-db, collector and web on one named volume.
# Optional TLS front: `docker compose --profile tls up -d` (set SITE_ADDRESS in .env).
name: wb-serp-monitor

x-logging: &default-logging
  driver: json-file
  options:
    max-size: "10m"
    max-file: "3"

x-wbmon: &wbmon
  build:
    context: .
    args:
      PYTHON_IMAGE: ${PYTHON_IMAGE:-python:3.12-slim}
      PIP_INDEX_URL: ${PIP_INDEX_URL:-https://pypi.org/simple}
  image: wb-serp-monitor:local
  # Optional: Compose >= 2.24 skips a missing file; `make up` also creates it from .env.example.
  env_file:
    - path: .env
      required: false
  # `environment` wins over env_file: the database can never leave the named volume by accident.
  environment:
    WBMON_DB_PATH: /data/wbmon.sqlite3
    TZ: Europe/Moscow
  volumes:
    - wbdata:/data
  logging: *default-logging

services:
  init-db:
    <<: *wbmon
    command: ["wbmon", "init-db"]
    restart: "no"

  collector:
    <<: *wbmon
    command: ["wbmon", "scheduler"]
    restart: unless-stopped
    stop_grace_period: 90s
    depends_on:
      init-db:
        condition: service_completed_successfully
    healthcheck:
      test: ["CMD", "wbmon", "healthcheck", "--max-age", "180"]
      interval: 60s
      timeout: 10s
      retries: 3
      start_period: 30s

  web:
    <<: *wbmon
    command:
      - streamlit
      - run
      - /app/src/wbmon/ui/app.py
      - --server.address=0.0.0.0
      - --server.port=8501
      - --server.headless=true
      - --browser.gatherUsageStats=false
    restart: unless-stopped
    depends_on:
      init-db:
        condition: service_completed_successfully
    ports:
      - "${WEB_PORT:-8501}:8501"
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5).status == 200 else 1)"
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  caddy:
    image: ${CADDY_IMAGE:-caddy:2.11-alpine}
    profiles: ["tls"]
    restart: unless-stopped
    depends_on:
      - web
    environment:
      SITE_ADDRESS: ${SITE_ADDRESS:-:80}
    ports:
      - "80:80"
      - "443:443"
      - "443:443/udp"
    volumes:
      - ./deploy/Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    logging: *default-logging

volumes:
  wbdata:
  caddy_data:
  caddy_config:
```
`deploy/Caddyfile`:
```
{$SITE_ADDRESS} {
	reverse_proxy web:8501
}
```
(Caddy substitutes `{$VAR}` before parsing `[CITED: caddyserver.com/docs/caddyfile/concepts#environment-variables]`; an address of only a port like `:80` is plain HTTP with no auto-HTTPS - the page does not say `:80` explicitly, so confirm in Phase 4.) The web healthcheck snippet was run against a real Streamlit 1.66 process: `/_stcore/health` -> `200 ok`; urllib one-liner exits 0 when up and 1 (traceback) when down `[VERIFIED: local run]`.

### 8. `Makefile` (validated with `make -n`, `make help`, and the guard)
```make
SHELL := /bin/bash
VENV  ?= .venv
PY    := $(VENV)/bin/python
# Load .env into the environment of one recipe line (values must be shell-safe: see .env.example).
ENVLOAD := set -a; [ -f .env ] && . ./.env; set +a;

.DEFAULT_GOAL := help
.PHONY: help install test lint init-db collect-once scheduler web up down logs ps guard-live

help:  ## list targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/'

install:  ## venv + editable install with dev and web extras
	python3 -m venv $(VENV)
	$(PY) -m pip install -e ".[dev,web]"

test:  ## offline test suite
	$(PY) -m pytest -q

lint:  ## ruff
	$(VENV)/bin/ruff check src tests

init-db:  ## create/migrate the local SQLite DB
	@$(ENVLOAD) $(VENV)/bin/wbmon init-db

guard-live:
	@test "$(CONFIRM)" = "yes" || { echo "Refusing: this sends REAL requests to Wildberries/Google. Run it on the deploy host with CONFIRM=yes."; exit 1; }

collect-once: guard-live  ## ONE real collection run (needs CONFIRM=yes)
	@$(ENVLOAD) $(VENV)/bin/wbmon collect

scheduler: guard-live  ## hourly scheduler in the foreground (needs CONFIRM=yes)
	@$(ENVLOAD) $(VENV)/bin/wbmon scheduler

web:  ## Streamlit UI on http://localhost:8501
	@$(ENVLOAD) $(VENV)/bin/streamlit run src/wbmon/ui/app.py

up:  ## build and start the whole stack
	@test -f .env || cp .env.example .env
	docker compose up -d --build

down:  ## stop the stack, KEEP the data volume (never add -v)
	docker compose down

logs:  ## follow collector and web logs
	docker compose logs -f --tail=200 collector web

ps:  ## container status and health
	docker compose ps
```
macOS ships GNU Make 3.81 and bash 3.2: the file avoids `.ONESHELL` and bash-4 features. `guard-live` is the one deviation from CONTEXT's target list: the dev machine is blocked by WB and the hard constraint forbids live requests during execution, so the two live targets refuse without `CONFIRM=yes`. Optional extra target for Phase 4: `up-prebuilt: docker compose up -d --no-build`.

### 9. `.env.example` (validated; every setting commented = defaults)
```
# WB SERP Monitor configuration. Copy to .env (`make up` does it for you). Every line is a default,
# so an untouched copy behaves exactly like having no .env at all. Rules for values: no inline
# comments, no `$`, double-quote anything with spaces or `;` (the file is read by Compose AND by
# POSIX sh in the Makefile).

# ---- Collector (read by Settings.from_env) ----
# Database file. Local default shown. DOCKER: leave unset - compose pins /data/wbmon.sqlite3 (the volume).
# WBMON_DB_PATH=data/wbmon.sqlite3
# Products stored per query (1..1000).
# WBMON_TOP_N=200
# WB delivery region (Moscow).
# WBMON_DEST=-1257786
# WB search endpoint.
# WBMON_SEARCH_URL=https://search.wb.ru/exactmatch/ru/common/v18/search
# Seconds between sequential requests (minimum 0.5; polite default).
# WBMON_REQUEST_DELAY=1.5
# Hard budget of one run in seconds; remaining queries are recorded as error/deadline.
# WBMON_RUN_DEADLINE=2400
# Google Sheet CSV with the 70 queries (a bundled copy is used when it is unreachable).
# WBMON_SHEET_CSV_URL=https://docs.google.com/spreadsheets/d/1K0m4229w_1LONgrNzWpx8lkkDcAlbdZH5ifHenZV3x8/export?format=csv
# Leave unset to send the honest default "Mozilla/5.0 (compatible; wbmon/<version>; +<repo>)".
# WBMON_USER_AGENT=
# Zone for delivery-day math and display. Slots stay at the top of the hour (Moscow == UTC+3).
# WBMON_TIMEZONE=Europe/Moscow
# DEBUG, INFO, WARNING or ERROR.
# WBMON_LOG_LEVEL=INFO

# ---- Docker Compose ----
# Host port of the Streamlit UI.
# WEB_PORT=8501
# Enable the TLS reverse proxy profile (`caddy`): COMPOSE_PROFILES=tls
# COMPOSE_PROFILES=
# Caddy site address: a domain (automatic HTTPS), <dashed-ip>.sslip.io, or :80 (plain HTTP).
# SITE_ADDRESS=:80
# Build-time mirrors for hosts that cannot reach Docker Hub / pypi.org.
# PYTHON_IMAGE=python:3.12-slim
# CADDY_IMAGE=caddy:2.11-alpine
# PIP_INDEX_URL=https://pypi.org/simple
```
Defaults above mirror the frozen `config.py` read this session (`DEFAULT_DEST`, `DEFAULT_SEARCH_URL`, `request_delay` 1.5 with floor 0.5, `run_deadline` 2400, `top_n` 1..1000). The executor must re-derive them from `config.py`, and the coverage test enforces the name list.

### 10. Packaging test skeleton (key assertions; full draft ran green: 7 tests)
```python
ROOT = Path(__file__).resolve().parents[1]          # conftest chdir()s into tmp_path: never use cwd
SCHEMA = ROOT / "tests" / "data" / "compose-spec.json"
def resolve(v): return re.sub(r"\$\{[A-Za-z_]+:-([^}]*)\}", r"\1", v) if isinstance(v, str) else v

def test_compose_matches_official_schema(compose):
    jsonschema = pytest.importorskip("jsonschema")   # declared in [dev]; importorskip only guards odd envs
    assert hashlib.sha256(SCHEMA.read_bytes()).hexdigest() == PINNED_SHA256
    assert not list(jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(compose))

def test_compose_topology(compose):
    assert "version" not in compose and compose["name"] == "wb-serp-monitor"
    svc = compose["services"]
    for name in ("init-db", "collector", "web"):
        s = svc[name]
        assert "wbdata:/data" in s["volumes"]                                   # rw, no ':ro'
        assert s["environment"]["WBMON_DB_PATH"] == "/data/wbmon.sqlite3"
        assert s["env_file"] == [{"path": ".env", "required": False}]
        assert s["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
        assert isinstance(s["command"], list)                                   # exec form: PID 1 gets SIGTERM
    assert svc["collector"]["healthcheck"]["test"][:3] == ["CMD", "wbmon", "healthcheck"]
    assert int(svc["collector"]["stop_grace_period"].rstrip("s")) >= 75
    assert svc["caddy"]["profiles"] == ["tls"] and resolve(svc["caddy"]["image"]) == "caddy:2.11-alpine"
```
(`tests/data/compose-spec.json` must not be edited; to refresh, re-download and update the pinned hash deliberately.)

### 11. Orchestrator and CLI deltas (sketch)
```python
# orchestrator.run_once(..., should_stop: Callable[[], bool] = lambda: False)
if should_stop():                                   # after lock/abandon/slot checks
    log_event("terminated", phase="before_start"); return None
qs = load_queries(settings, transport=transport)
if should_stop():                                   # the sheet fetch can take seconds
    log_event("terminated", phase="before_start"); return None
...                                                  # upsert_queries / start_run only after both checks
for text in texts:
    if should_stop():                               # BEFORE the deadline and breaker branches
        if not announced:
            log_event("terminated", run_id=run_id, remaining=len(texts) - done); announced = True
        result = QueryResult(status="error", error_class="terminated", started_at=iso_z(now()),
                             duration_ms=0, error_message="SIGTERM received; no request sent")
    elif ...:                                       # existing deadline / breaker / collect_query branches
        ...
    stored = writer.record_query(run_id, ids[text], result)

# cli.py collect branch
flag = StopFlag()
with install_stop_handlers(flag, (signal.SIGTERM,)):
    run_id = run_once(settings, ..., should_stop=lambda: flag.requested)
# cli.py new subcommands: scheduler (no flags beyond --db), healthcheck [--db] [--max-age FLOAT, default 180]
# healthcheck: return 1 for ConfigError (never 2); scheduler: ConfigError -> 2 like the other commands
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `version: "3.x"` key in compose files | no `version:` (ignored by the Compose Spec; schema marks it deprecated) | Compose v2 | Locked in CONTEXT; static test asserts absence |
| `env_file: .env` required to exist | long syntax `{path, required: false}` | Compose 2.24.0 | Optional `.env`; need `docker compose version` >= 2.24 on the host |
| Tornado-based Streamlit | Starlette/uvicorn (1.57+), `/_stcore/health` unchanged | Streamlit 1.57 | Verified health endpoint on 1.66.0 |
| supercronic / cron sidecar (STACK.md) | in-image Python scheduler loop | Orchestrator decision | No GitHub download in the Docker build; one fewer registry dependency |
| `docker-compose` v1 binary | `docker compose` plugin | v1 EOL 2023 | Makefile uses the plugin form |

**Deprecated/outdated:** the supercronic/entrypoint/`--if-stale` sketches and `heartbeat` as a bare file mtime in `research/STACK.md` and `ARCHITECTURE.md` are superseded by CONTEXT (JSON heartbeat with `ts`, scheduler loop, `service_completed_successfully` init-db).

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `python:3.12-slim` (Debian) ships `groupadd`/`useradd` (shadow utilities) | Code Examples 6 | Image build fails at the first `RUN`; fix by `adduser` or installing `passwd` - caught on the first Phase 4 build |
| A2 | Docker's first-mount copy-up of an image directory into an empty named volume preserves `app:app` ownership (docs describe copying, are silent on ownership) | Pattern 6 | `init-db` fails with a permission error (loud, blocks the other roles); fix `docker run --rm -v wb-serp-monitor_wbdata:/data alpine chown -R 10001:10001 /data` |
| A3 | WAL `-shm` memory sharing works across two containers on the same host through a local named volume | Pattern 6 | UI sees stale/locked data; Phase 4 two-container check; fallback would be a single-container topology (out of scope now) |
| A4 | Docker Hub and PyPI are (un)reachable from the future Russian VPS - status in 2026 unverified; only 2024 (Docker) and June 2026 (PyPI, blog-level) reports found | Pitfall 9 | Build fails on the host; mitigated by mirror hooks and the build-elsewhere + `docker save | ssh | docker load` path |
| A5 | Caddy treats a site address of only `:80` as plain HTTP without automatic HTTPS (docs imply it, do not state it for `:80`) | Code Examples 7 | `SITE_ADDRESS=:80` fallback would try ACME; fix by `http://:80` form; verify in Phase 4 |
| A6 | Compose merge keys (`<<: *anchor`) and `x-` extension anchors are accepted by Compose v2 exactly as PyYAML resolves them (widely used; schema validated the resolved document) | Pattern 7/8 | `docker compose config` fails in Phase 4/CI - mitigated by the optional `docker compose config -q` test on CI runners |
| A7 | pyyaml / jsonschema "SUS" verdicts are purely the missing download statistics and the orchestrator's D-A8 precedent (automated metadata check, no human checkpoint) applies to them | Package Legitimacy Audit | If not accepted, planner must insert a human-verify checkpoint before the dev-extra install |
| A8 | 1-second ticks are acceptable against CONTEXT's "sleeps in <= 30 s chunks" (a 1 s chunk satisfies the upper bound; chosen for prompt SIGTERM) | Pattern 2 | Negligible; tests run in 0.5 s for 3 simulated hours |
| A9 | Reading Streamlit's `.streamlit/config.toml` from the working directory (not the script directory) | Pitfall 7 | UI theme missing in the container; verify in Phase 4/Phase 3 AppTest |
| A10 | Third-party blog reports (habr/tproger) about registry blocks are accurate | Pitfall 9 | Risk estimate only |

## Open Questions (RESOLVED — see 02-CONTEXT.md addendum D2-A1..D2-A6; Q5 by the "re-read final Phase 1 files" executor rules)

1. **Run status when SIGTERM hits before any query succeeded.**
   - Known: CONTEXT says the interrupted run ends `partial`; Phase 1 `finish_run` derives `partial` only when >= 1 query is ok/partial; with only `error/terminated` rows it derives `error`, and `finish_run(error=...)` forces `error` too.
   - Unclear: whether `partial` is required even with zero successes.
   - Recommendation: keep the Phase 1 derivation (an interrupted run with no data is honestly `error`, each row says `terminated`); document "partial whenever at least one query completed". Do not change `Writer` (frozen) just for this edge.
2. **`subprocess.run` vs `subprocess.Popen`.** CONTEXT names `subprocess.run`; the required behaviours (heartbeat during the run, SIGTERM forwarding, SIGTERM-then-SIGKILL on timeout) cannot be built on `run()`. Recommendation: implement with Popen and state it in the plan as the interpretation of that decision.
3. **Direct 8501 exposure with the `tls` profile.** Recommendation: leave `ports: "${WEB_PORT:-8501}:8501"` as locked; Phase 4 can set `WEB_PORT=127.0.0.1:8501`-style bindings only if the compose port string is changed to `"${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8501}:8501"`. Cheap, but it adds a variable CONTEXT does not list - decide in planning (default recommendation: add `WEB_BIND`, document it in `.env.example`).
4. **Who adds PyYAML/jsonschema to `pyproject.toml`?** Only plan 02-02 edits it (one `dev` line each); Phase 3 must not. Confirm no Phase 3 plan touches `pyproject.toml`.
5. **Phase 1 final shape.** The Phase 1 orchestrator/cli were not yet written at research time (only `clock`, `config`, `log`, `models`, `storage/{db,migrations}` existed). The wave 2 plan must re-read `orchestrator.py`, `cli.py`, `tests/test_orchestrator.py`, `tests/test_cli.py` and `tests/test_contracts.py` and adapt the deltas to what Phase 1 actually shipped (`run_once` kwargs, whether `_transport`/`_sleep` seams exist on `main`).

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker / Compose | real `compose up` | no (dev machine) | - | Static validation (PyYAML + schema + parser); real run in Phase 4 on the host |
| hadolint, yamllint | Dockerfile/YAML lint | no | - | Own Dockerfile parser + schema test |
| Python 3.14 `.venv` (dev), 3.11, 3.12 (`~/.local/bin`) | tests, cross-version check | yes | 3.14.6 / 3.11 / 3.12 | - |
| GNU make | Makefile tests | yes | 3.81 (macOS) | tests `skipif(no make)` |
| bash | Makefile `ENVLOAD` | yes | 3.2.57 | avoid bash-4 features (done) |
| PyYAML | packaging tests | not in `.venv` yet | 6.0.3 on PyPI | install via the `dev` extra (needs PyPI access from the dev machine: works) |
| jsonschema | schema test | yes in `.venv` (transitive), not in bare `[dev]` | 4.26.0 | add to `dev` extra |
| Network to PyPI/Docker Hub APIs (dev) | install, tag verification | yes | - | - |

**Missing dependencies with no fallback:** none for Phase 2 execution.
**Missing dependencies with fallback:** Docker (static validation), hadolint (own parser).

## Plan Split (recommended: 3 plans, 2 waves, disjoint `files_modified`)

| Plan | Wave | Requirements | Goal | `files_modified` |
|------|------|--------------|------|------------------|
| 02-01 | 1 | SCHED-02, SCHED-03 | Scheduler engine: pure slot functions, atomic heartbeat + healthcheck logic, stop-flag/handler helper, `Scheduler` (Popen-poll loop) with `build_scheduler`/`run_scheduler`, fake-clock + real-child tests | `src/wbmon/schedule.py`, `src/wbmon/signals.py`, `src/wbmon/health.py`, `src/wbmon/scheduler.py`, `tests/test_schedule.py`, `tests/test_signals.py`, `tests/test_health.py`, `tests/test_scheduler.py`, `tests/test_scheduler_process.py` |
| 02-02 | 1 | OPS-01, OPS-02 | Packaging: Dockerfile, `.dockerignore`, `compose.yaml`, `deploy/Caddyfile`, `.env.example`, `Makefile`, dev-extra deps, vendored compose schema, static tests | `Dockerfile`, `.dockerignore`, `compose.yaml`, `deploy/Caddyfile`, `.env.example`, `Makefile`, `pyproject.toml` (dev extra only), `tests/test_packaging.py`, `tests/data/compose-spec.json` |
| 02-03 | 2 (depends on 01, 02 and finished Phase 1) | SCHED-02, SCHED-03, OPS-01 | Integration: graceful SIGTERM in `collect`, `scheduler` + `healthcheck` subcommands, signal wiring, cross-checks compose<->CLI, end-to-end offline proofs | `src/wbmon/orchestrator.py`, `src/wbmon/models.py`, `src/wbmon/cli.py`, `tests/test_contracts.py` (only the pinned `ERROR_CLASSES` assertion, if any), `tests/test_orchestrator_terminate.py`, `tests/test_cli_phase2.py`, `tests/test_packaging_cross.py` |

Conflicts avoided: 02-01 and 02-02 share no file and touch no Phase 1 file; every Phase 1 file edited by Phase 2 is confined to 02-03, which runs after Phase 1 is complete. Phase 3 owns `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**` and must not edit `pyproject.toml`, `cli.py` or the Phase 2 files (it only reads `scheduler_heartbeat.json`).

**Suggested tasks**
- **02-01**: (T1) `schedule.py` + `test_schedule.py` with the vector table and purity check (`module_imports` allowlist: only `datetime` + `wbmon.clock`); (T2) `health.py` + `signals.py` + tests (atomicity: no `*.tmp` left, corrupted/missing/stale/future cases, handler restore, non-main-thread no-op); (T3) `scheduler.py` + fake-clock tests (list in Code Examples 5) + `test_scheduler_process.py` real-child tests (children are `python -c` snippets only; readiness via child output; `spawn_collect` argv asserted by monkeypatching `subprocess.Popen`, never invoked).
- **02-02**: (T1) dev deps + legitimacy metadata check + vendored schema + Dockerfile/.dockerignore/Caddyfile; (T2) compose.yaml + `.env.example` + Makefile; (T3) `tests/test_packaging.py` (all layers of Pattern 8). Acceptance: `pytest tests/test_packaging.py` green; `grep -c "latest" Dockerfile compose.yaml` = 0; `docker` never invoked.
- **02-03**: (T1) `models.ERROR_CLASSES` + `orchestrator` `should_stop` + `test_orchestrator_terminate.py` (three offline tests of Pattern 5); (T2) `cli.py` subcommands + SIGTERM wiring + `test_cli_phase2.py` (`healthcheck` exit codes incl. config-error -> 1, `scheduler` with injected fake spawn/clock via `main` seams or direct `run_scheduler`, real `signal.raise_signal` test); (T3) `test_packaging_cross.py` (compose commands are registered subcommands) + full-suite + `ruff` + 3.11/3.12 throwaway-venv run (as Phase 1 plan 01-05 does).

Wave-0/test infrastructure: reuse Phase 1 `tests/factories.py` (`FakeWB`, `RecordingSleep`, `make_settings`) and `conftest._isolated`; no new fixtures needed beyond local fake `Clock`/`FakeChild` in the scheduler tests.

## Security Domain

`security_enforcement` is enabled (ASVS level 1, block on high). `nyquist_validation` is `false`, so no Validation Architecture section is included.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | No accounts; public read-only UI by requirement |
| V3 Session Management | no | Streamlit-managed, out of scope here |
| V4 Access Control | partial | Non-root uid 10001; UI read-only enforced at the DB connection, volume stays rw (documented); no Docker socket mounted |
| V5 Input Validation | yes | `parse_slot` (aware ISO only) for `--slot`; argparse typed `--max-age`; `Settings` validation for env; fixed-argv `Popen` with the validated slot as the only variable argument |
| V6 Cryptography | no | TLS by Caddy/ACME only; nothing hand-rolled |
| V7 Error Handling & Logging | yes | `log_event`/`fmt_value` quoting (no log forging from remote data); healthcheck prints one line, no secrets (none exist) |
| V12 Files & Resources | yes | Heartbeat tmp+`os.replace` in the data directory; path derived from operator-set `WBMON_DB_PATH` only |
| V14 Configuration | yes | Non-root user, pinned base/proxy images (no `latest`), no secrets in image or compose, `.env` gitignored, `.dockerignore` allowlist, log rotation, `restart` policies |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Command injection into the spawned `collect` | Tampering | `Popen` with a list argv, no shell; slot string produced by `current_slot` (not from input) |
| Container runs as root / writable image paths | Elevation of Privilege | `USER app` (uid 10001) after all setup `RUN`s; only `/data` and `/home/app` writable |
| DB leaves the volume / is wiped by `down -v` | Tampering / Repudiation | Pinned `WBMON_DB_PATH` in compose `environment`; Makefile `down` without `-v`, asserted by test |
| Two schedulers or a manual run overlap | Tampering | Phase 1 `flock` + UNIQUE slot; `lock_busy`/`slot_exists` exit 0 |
| SIGKILL / host reboot mid-run | Denial of Service / Repudiation | `abandon_stale_runs` at next `collect`; `restart: unless-stopped`; honest `abandoned` rows |
| Scheduler hangs silently | Denial of Service | Heartbeat + healthcheck (visibility; Compose does not auto-restart unhealthy containers - autoheal is out of scope) |
| Hammering WB after a failure | Denial of Service (external) | Scheduler never retries a slot; Phase 1 breaker/deadline; exactly one spawn per slot |
| Plain-HTTP public UI / port exposed beside TLS proxy | Information Disclosure | Caddy profile for TLS; note direct-port exposure (Pitfall 10) |
| Supply chain (base image, pip index mirror) | Tampering | Official images by tag; `PIP_INDEX_URL`/`PYTHON_IMAGE` mirror args are operator-supplied and documented as trust decisions; dev deps limited to PyYAML/jsonschema with metadata check |
| Accidental live WB traffic from tests/Make | Policy violation | `Scheduler.spawn` required arg, Popen monkeypatch tests, `guard-live` Makefile targets, no `CONFIRM=yes` in any automated step |
| Healthcheck output / heartbeat leaking data | Information Disclosure | Heartbeat holds only ts/pid/slot/exit/version |

## Sources

### Primary (HIGH confidence)
- Local prototype (scratch, outside repo), 2026-10-16: scheduler/health/schedule modules with 30 fake-clock tests and 3 real-process tests, all green; signal-handler flag semantics via `signal.raise_signal`; Makefile guard run; `pip wheel` of the repo copy (bundled data included); WAL writer/`mode=ro` reader process spike; Streamlit 1.66.0 `/_stcore/health` -> `200 ok`; compose draft validated against the compose-spec schema with negative controls.
- Frozen Phase 1 code read this session: `src/wbmon/{clock,config,log,models,cli}.py`, `storage/{db,migrations}.py`; Phase 1 plans 01-01/01-03/01-05 and SKELETON.md for the not-yet-written orchestrator/lock/reader interfaces.
- Docker docs: compose-file services reference (env_file `required`, healthcheck fields, stop_grace_period default 10 s, depends_on conditions, init) https://docs.docker.com/reference/compose-file/services/ ; Dockerfile HEALTHCHECK exit codes (2 reserved) https://docs.docker.com/reference/dockerfile/#healthcheck ; volumes copy-up https://docs.docker.com/engine/storage/volumes/
- SQLite WAL docs https://www.sqlite.org/wal.html (read-only WAL conditions, same-host requirement, checkpoint starvation, WAL deletion on last close)
- Caddyfile concepts https://caddyserver.com/docs/caddyfile/concepts#environment-variables (`{$VAR}`)
- compose-spec schema https://raw.githubusercontent.com/compose-spec/compose-spec/main/schema/compose-spec.json (Apache-2.0 per GitHub API)
- PyPI JSON API (pyyaml 6.0.3, jsonschema 4.26.0, project URLs); Docker Hub API (python:3.12-slim 2026-10-09, caddy:2.11-alpine 2026-10-06); `gsd-tools package-legitimacy check` output (SUS: unknown-downloads)

### Secondary (MEDIUM confidence)
- Compose 2.24.0 as the version introducing `env_file.required` (web search summary of docs https://docs.docker.com/compose/how-tos/environment-variables/set-environment-variables/ ; the services reference page itself gives no version)

### Tertiary (LOW confidence - marked for validation on the host)
- Docker Hub block of 2024-05-30 and conflicting restoration reports: https://habr.com/ru/companies/sberbank/news/819057 , https://tproger.ru/articles/docker-hub-v-rossii---vse--gajd--kak-obojti-blokirovku
- pypi.org/Fastly instability in Russia (June, per blog) and community mirror claims: https://habr.com/ru/articles/1043976

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - stdlib only at runtime; image tags and dev package versions verified against registries.
- Architecture (scheduler, heartbeat, signals): HIGH - implemented and tested as a prototype incl. real processes.
- Packaging: MEDIUM - static validation proven (schema + negative controls), but no Docker daemon; copy-up ownership, cross-container WAL and Caddy `:80` behaviour deferred to Phase 4.
- Pitfalls: HIGH for code-level traps (reproduced), MEDIUM for registry reachability (third-party reports).

**Research date:** 2026-10-16
**Valid until:** 2026-11-15 (stable domain); re-check Docker Hub / PyPI reachability and Compose version on the host at deployment time.
