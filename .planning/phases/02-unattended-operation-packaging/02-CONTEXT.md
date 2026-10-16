# Phase 2: Unattended Operation & Packaging - Context

**Gathered:** 2026-10-16
**Status:** Ready for planning
**Mode:** Smart discuss (autonomous). The user asked for a fully autonomous solution, so the recommended
answer for every grey area was auto-accepted by the orchestrator; alternatives are recorded for traceability.

<domain>
## Phase Boundary

Make the Phase 1 collector run by itself every hour on the Moscow clock without manual intervention and
package the whole stack: `wbmon scheduler` (hourly loop launching `wbmon collect` subprocesses),
heartbeat + `wbmon healthcheck`, graceful SIGTERM handling, one Dockerfile, `compose.yaml`
(init-db / collector / web / optional caddy profile) on one named volume, `.env.example`, a Makefile and
a documented no-Docker local path. Out of scope: the Streamlit UI itself (Phase 3 — compose only points
at its entrypoint `src/wbmon/ui/app.py`), real deployment and README/write-up (Phase 4).

</domain>

<decisions>
## Implementation Decisions

### Area 1 — Scheduler semantics
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Trigger model | `wbmon scheduler` = long-running Python loop; computes the next hourly slot (top of hour, Europe/Moscow == UTC+3, no DST) as a UTC ISO `YYYY-MM-DDTHH:00:00Z`; sleeps in ≤ 30 s chunks (prompt SIGTERM handling, robust to clock jumps); at slot time runs `[sys.executable, "-m", "wbmon", "collect", "--trigger", "scheduler", "--slot", <slot>]` via `subprocess.run` (fresh process per run, inherits env), waits with timeout = `run_deadline + 600 s`, logs `event=run_finished slot=… exit=… duration_s=…` | supercronic / systemd timer / APScheduler (documented alternatives only) |
| 2 | Startup / catch-up | On start: if the current slot (floor of now) has no run in DB and `now − slot ≤ 45 min` → run immediately for the current slot; never back-fill older slots (honest gaps); if > 45 min → wait for next slot | back-fill missed hours (rejected: fabricates timing) |
| 3 | Overlap / double run | Relies on Phase 1 guarantees (non-blocking `flock` on `<db>.lock` → `lock_busy` exit 0; existing `--slot` → `slot_exists` exit 0). Scheduler itself is single-threaded and never starts a run while one is in flight; if a run finishes after the next slot's grace window, that slot is logged `slot_missed` (not back-filled) | — |
| 4 | Failure policy | Any child exit code ≠ 0, timeout or exception is logged (`event=run_failed`) and the loop continues to the next slot; scheduler never exits on run failure; `restart: unless-stopped` covers scheduler crashes; on child timeout the child gets SIGTERM, then SIGKILL after 30 s | — |

### Area 2 — Liveness & signals
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Heartbeat | Scheduler writes `<db_dir>/scheduler_heartbeat.json` atomically (write tmp + `os.replace`) at start, every loop tick (≤ 30 s) and after each run: `{"ts": utc_iso, "pid", "next_slot", "last_slot", "last_exit", "version"}` | DB heartbeat table (rejected: extra writes, schema change) |
| 2 | Healthcheck | `wbmon healthcheck [--max-age 180]` exits 0 iff heartbeat file exists and is younger than max-age seconds (and parses); exits 1 otherwise with a one-line reason; does NOT depend on WB reachability (all-`blocked` runs are healthy) | latest-run age check (kept as extra info in output only) |
| 3 | SIGTERM/SIGINT | Scheduler: sets a stop flag, forwards SIGTERM to an in-flight child, waits up to 60 s, exits 0. `collect` (Phase 1 orchestrator): on SIGTERM finishes the current query transaction, records remaining queries as `error` with `error_class='terminated'` (no requests), sets run status `partial`, exits 0 — so an interrupted run is never left `running` (the next start's `abandon_stale_runs` covers SIGKILL) | — |
| 4 | Logs | Same `key=value` structured stdout lines as Phase 1 (`log.py`), `PYTHONUNBUFFERED=1`; scheduler events: `scheduler_started`, `slot_wait`, `run_started`, `run_finished`, `run_failed`, `slot_missed`, `catch_up`, `shutdown` | JSON logs |

### Area 3 — Packaging (Docker / compose)
| # | Question | ✅ Accepted | Alternatives |
|---|----------|------------|--------------|
| 1 | Image | One `Dockerfile`: `python:3.12-slim`, `PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Moscow`, non-root user `app` uid/gid 10001, `/data` created and chowned (named volume inherits ownership), `pip install --no-cache-dir ".[web]"` (one image for both roles — simpler; size acceptable), `WBMON_DB_PATH=/data/wbmon.sqlite3`, `EXPOSE 8501`, `.dockerignore` excludes `.venv`, `.git`, `.planning`, `data/`, caches | two-target image (rejected: complexity for little gain) |
| 2 | Compose | `compose.yaml` (no `version:` key): `init-db` (one-shot `wbmon init-db`, `restart: "no"`), `collector` (`wbmon scheduler`, `depends_on: init-db: condition: service_completed_successfully`, `restart: unless-stopped`, healthcheck `wbmon healthcheck` interval 60 s, start_period 30 s), `web` (`streamlit run src/wbmon/ui/app.py --server.address=0.0.0.0 --server.port=8501 --server.headless=true --browser.gatherUsageStats=false`, depends_on init-db, `restart: unless-stopped`, healthcheck via `python -c` urllib GET `http://127.0.0.1:8501/_stcore/health`, `ports: "${WEB_PORT:-8501}:8501"`), optional `caddy` service under compose profile `tls` (`caddy:2.11-alpine`, Caddyfile `{$SITE_ADDRESS} { reverse_proxy web:8501 }`, ports 80/443, volumes caddy_data/caddy_config); named volume `wbdata` mounted rw at `/data` in collector, init-db and web (no `:ro` — WAL readers need `-shm`; read-only is enforced by the UI's `mode=ro` connection); logging `json-file` max-size 10m max-file 3; `env_file: .env` optional (`required: false`) | nginx (rejected: manual WS config) |
| 3 | Web entrypoint path | `src/wbmon/ui/app.py` is created by Phase 3; the image installs the package non-editable, so compose runs `streamlit run /app/src/wbmon/ui/app.py` from the copied source tree (WORKDIR `/app`, source copied) | `python -m streamlit run -m wbmon.ui` |
| 4 | Local path & tooling | `Makefile` targets: `install` (venv + `pip install -e ".[dev,web]"`), `test`, `lint`, `init-db`, `collect-once`, `scheduler`, `web`, `up`, `down`, `logs`, `ps`; `.env.example` documents every `WBMON_*` var with defaults and comments (DB_PATH, TOP_N, DEST, SEARCH_URL, REQUEST_DELAY, RUN_DEADLINE, SHEET_CSV_URL, USER_AGENT, TIMEZONE, LOG_LEVEL) plus `WEB_PORT`, `SITE_ADDRESS` | — |

### Testing strategy
- Pure functions with injected clock: `current_slot(now)`, `next_slot(now)`, `should_catch_up(now, slot, has_run, grace=45min)`; vectors around :00, :44:59, :45:00, :59:59, day/month/year rollovers (MSK = UTC+3).
- Scheduler loop tested with fake clock/sleep and a fake runner (no real subprocess, no network): catch-up once, no back-fill, failure continues, timeout path, SIGTERM stop flag, heartbeat file content/atomicity, `slot_missed`.
- One real-subprocess smoke test allowed only against a tmp DB with `collect` pointed at a MockTransport-free path that makes no network calls (e.g. run `wbmon healthcheck` / `wbmon --help`) — never live WB/Google.
- Static checks: `compose.yaml` parses as YAML and contains the services/healthchecks/volume above; `Dockerfile` contains `USER app`, `HEALTHCHECK`-free (healthchecks live in compose), `python:3.12-slim`. Docker is NOT installed on the dev machine — the real `docker compose up` is verified in Phase 4 on the host.

### Claude's Discretion
- Exact module name for the scheduler (`scheduler.py`) and healthcheck (`health.py`), Makefile style, Caddyfile location (`deploy/Caddyfile`).

</decisions>

<code_context>
## Existing Code Insights

Phase 1 delivers `src/wbmon/` (config `Settings.from_env()`, `clock.py` with `parse_slot`, `log.py` key=value logger, `lock.py` flock, `orchestrator.py` run loop, `cli.py` argparse with `init-db` / `collect` / `probe`, `storage/reader.py` incl. `run_id_for_slot`). Read the Phase 1 SUMMARY files and code before planning; extend `cli.py` with `scheduler` and `healthcheck` subcommands and `orchestrator.py` with SIGTERM handling — do not duplicate Phase 1 logic.

</code_context>

<specifics>
## Specific Ideas

- Docker is not installed on the dev machine (no Homebrew either); planning must not require building images locally. Phase 4 builds and runs the stack on the host.
- Phase 3 runs in parallel with this phase and owns `src/wbmon/ui/**` and `.streamlit/**`; this phase must not create or edit those paths (compose/Dockerfile only reference them).

</specifics>

<deferred>
## Deferred Ideas

- Automatic SQLite backups / retention — v2.
- Autoheal sidecar — not needed.

</deferred>

<addendum date="2026-10-16" source="orchestrator, after 02-RESEARCH.md">
## Addendum — decisions on open questions raised by phase research (auto-accepted)

- **D2-A1 Status on SIGTERM:** keep the Phase 1 `finish_run` derivation — `partial` if ≥1 query succeeded before termination, `error` if none did; remaining queries recorded as `error` / `error_class='terminated'` without requests (additive `terminated` in `ERROR_CLASSES`, no migration).
- **D2-A2 Child process API:** `subprocess.Popen` + poll loop (not `subprocess.run`) so the scheduler keeps writing the heartbeat during a run, forwards SIGTERM and escalates SIGTERM→SIGKILL after 30 s; child started with `start_new_session=True`; scheduler tick 1 s (within the ≤30 s bound), heartbeat written at least every 30 s.
- **D2-A3 Web exposure:** add `WEB_BIND` (default `0.0.0.0` so the UI is reachable by a public link without Caddy; set `127.0.0.1` when the `tls` profile/Caddy is used) → compose `ports: "${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8501}:8501"`.
- **D2-A4 Dev dependencies:** `pyyaml` and `jsonschema` may be added to the `dev` extra in `pyproject.toml` (Phase 2 is the only phase editing it now); D-A8 precedent applies — automated PyPI metadata check, no human checkpoint. Vendoring the compose-spec JSON schema under `tests/data/` (license noted) is accepted.
- **D2-A5 Makefile:** NO `CONFIRM=yes` guard — the Makefile is a human-facing tool and live collection is the product's purpose. The no-live-WB rule binds the agents/tests, not the shipped commands (agents simply must not run `make collect-once` / `make scheduler`).
- **D2-A6 Packaging details accepted from research:** pin `WBMON_DB_PATH=/data/wbmon.sqlite3` in compose `environment` (wins over env_file), healthcheck exit codes only 0/1, `stop_grace_period: 90s` for collector, exec-form commands, top-level volume `name: wbmon_data`, allowlist `.dockerignore`, no `# syntax=` line, `PYTHON_IMAGE`/`CADDY_IMAGE` build args + `PIP_INDEX_URL` hook for registries unreachable from a Russian VPS, build-time smoke test that the bundled queries file is packaged, Compose ≥ 2.24 noted for `env_file: required: false`.
</addendum>
