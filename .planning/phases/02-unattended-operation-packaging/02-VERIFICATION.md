---
phase: 02-unattended-operation-packaging
verified: 2026-10-16T18:10:00Z
status: human_needed
score: 4/5 roadmap success criteria fully verified (SC5 partial: .env.example missing, deferred by environment)
overrides_applied: 0
deferred:
  - truth: "OPS-02 settings documentation: every setting (top_n, dest, delay, DB path, sheet URL, timezone) documented"
    addressed_in: "Phase 4 (DOC-01 README configuration section)"
    evidence: "Phase 4 SC4: README explains 'configuration, operations and limitations'; DOC-01: 'how to run (Docker and local), configuration'. NOTE: this covers the documentation, NOT the literal .env.example file named in Phase 2 SC5 / OPS-02."
human_verification:
  - test: "Decide how to close the .env.example part of Phase 2 SC5 / OPS-02"
    expected: "Either (a) the user creates .env.example themselves (their permission settings deny .env* files for the agent) from the WBMON_* names in src/wbmon/config.py plus WEB_PORT, WEB_BIND, SITE_ADDRESS, COMPOSE_PROFILES, PYTHON_IMAGE, PIP_INDEX_URL, CADDY_IMAGE; or (b) the user accepts a README configuration table (Phase 4) as the OPS-02 documentation and amends the SC5 wording"
    why_human: "Agent cannot write .env* under the user's permission rules; whether a README table satisfies 'documented .env.example' is a scope decision"
  - test: "On a host with Docker: `docker compose config`, `docker compose up -d --build`, `docker compose ps`, then `docker compose down && docker compose up -d`"
    expected: "init-db exits 0, collector becomes healthy, web becomes healthy once Phase 3 lands src/wbmon/ui/app.py, history row count unchanged after down/up"
    why_human: "Docker is not installed on the dev machine; only static validation is possible (test_docker_compose_config_accepts_the_file is skipped). ROADMAP assigns the real run to Phase 4"
  - test: "Two-container WAL check: scheduler/collect writer in `collector`, reader in `web`, shared read-write volume"
    expected: "No 'database is locked' errors; reader sees new rows after each run"
    why_human: "Needs two live containers; ROADMAP research flag, completed in Phase 4"
---

# Phase 2: Unattended Operation & Packaging Verification Report

**Phase Goal:** The collector runs by itself every hour on the Moscow clock, survives failures and restarts without manual intervention, and the whole stack starts with one `docker compose up -d` (or without Docker for local development).
**Verified:** 2026-10-16T18:10:00Z
**Status:** human_needed (no code-level blockers; one scoped-out deliverable and Docker-dependent checks need a human)
**Re-verification:** No - initial verification

## Goal Achievement

### Observable Truths (ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | `wbmon scheduler` starts a run at every :00 MSK as a fresh `wbmon collect` subprocess; catch-up once if the current slot has no run (45 min); no back-fill; no overlap (file lock + UNIQUE slot) | VERIFIED | `src/wbmon/scheduler.py`: `spawn_collect` = `Popen([sys.executable,"-m","wbmon","collect","--trigger","scheduler","--slot",slot], start_new_session=True)`; `run_forever` acts only when `slot > handled` (no back-fill, immune to clock step back); `_handle_slot` -> `slot_exists` / `should_catch_up` (`schedule.py`, `CATCH_UP_GRACE=45min`, inclusive) / `slot_missed`; one child at a time (poll loop). MSK==UTC+3 fixed, slots are UTC top-of-hour (`schedule.py`). Collect side: `storage/lock.py` `fcntl.flock` NB lock + `migrations.py` `slot_utc TEXT UNIQUE` + `orchestrator.run_once` `slot_exists`/IntegrityError skip. Tests: `test_start_exactly_45_minutes_late_still_catches_up`, `..._45_minutes_and_1_second_late_is_missed`, `test_never_overlaps_and_never_spawns_a_slot_twice`, `test_wall_clock_step_back_never_reruns_a_handled_slot`, 12 slot vectors in `test_schedule.py` |
| 2 | Failed run / killed run / SIGTERM / container restart does not stop the cadence; interrupted run shows `abandoned`; no manual step | VERIFIED | Child non-zero, spawn error, any exception -> `run_failed` logged, loop continues (`run_slot`, `run_forever` try/except). Timeout = `run_deadline+600 s` -> terminate then kill after 30 s. SIGTERM/SIGINT -> `install_stop_handlers`, forwarded SIGTERM, 60 s wait then kill, `shutdown`, exit 0. `collect` handles SIGTERM (`cli.py` `install_stop_handlers`, `orchestrator.py` `should_stop` -> remaining queries `error/terminated`, run closed, exit 0). SIGKILL'd run -> next `collect` `abandon_stale_runs` while holding the lock, slot not re-run. Tests: `test_failing_children_do_not_stop_the_loop`, `test_spawn_error_is_logged_...`, `test_timeout_escalates_term_then_kill_and_the_next_slot_still_runs`, `test_sigterm_to_a_real_scheduler_process_is_forwarded_and_exits_zero`, `test_a_killed_run_is_abandoned_and_its_slot_stays_a_gap`, `test_stop_after_the_first_query_records_the_rest_as_terminated`. Compose: `restart: unless-stopped`, `stop_grace_period: 90s` (> 60 s scheduler wait, cross-checked in `test_packaging_cross.py`) |
| 3 | Heartbeat exists; healthcheck healthy while scheduler alive even if every run is `blocked`; unhealthy when heartbeat stale | VERIFIED | `health.py`: atomic `write_heartbeat` (same-dir tmp + `os.replace`), `check_heartbeat` looks only at `ts` (age <= max-age, <= 60 s future) - never at run status; `scheduler.beat` refreshes every 30 s incl. during a child run and after each run; CLI `healthcheck` only exits 0/1 (also for argparse errors). Spot-check: `WBMON_DB_PATH=<tmp> wbmon healthcheck` -> `unhealthy: heartbeat file missing ... last_run=no_db`, rc=1. Tests: `test_heartbeat_keeps_ticking_during_a_30_minute_run`, `test_real_scheduler_heartbeat_healthcheck_and_clean_sigterm`, `test_the_compose_healthcheck_is_1_without_a_heartbeat_and_0_with_a_fresh_one`, `test_the_healthcheck_never_returns_docker_reserved_code_2`, `tests/test_health.py` |
| 4 | `docker compose up -d` brings up `init-db` (one-shot), `collector`, `web` on one named volume, `restart: unless-stopped`, healthchecks, log rotation; scheduling MSK, storage UTC; history survives down/up | VERIFIED (statically; real run deferred to Phase 4) | `compose.yaml`: init-db `restart: "no"`, collector/web `unless-stopped` + `depends_on init-db: service_completed_successfully`; single volume `wbdata` (name `wbmon_data`) mounted rw at `/data` in all three; `WBMON_DB_PATH: /data/wbmon.sqlite3` pinned in `environment` (beats optional `.env`); json-file `10m x 3`; collector healthcheck `wbmon healthcheck --max-age 180`; web healthcheck `/_stcore/health`; `TZ: Europe/Moscow`; `Makefile down` = `docker compose down` (no `-v`). `Dockerfile`: python:3.12-slim, uid 10001 `USER app`, `tzdata` via dependency + build-time `ZoneInfo('Europe/Moscow')` and `load_bundled()==70` smoke, exec-form CMD. Storage UTC unchanged from Phase 1 (slots/ISO-Z). Validated against vendored official compose-spec schema (`test_compose_matches_the_official_schema`, negative control `test_schema_negative_control_rejects_a_typo`). Not executed: no Docker on this machine (`test_docker_compose_config_accepts_the_file` SKIPPED) |
| 5 | Runs without Docker (venv + CLI) and `.env.example` documents every setting | PARTIAL | Local path VERIFIED: `Makefile` targets install/test/lint/init-db/collect-once/scheduler/web load optional `.env`, `wbmon` entry point in `pyproject.toml`, `Settings.from_env({})` works with every `WBMON_*` defaulted (`test_every_setting_has_a_default_so_the_stack_runs_without_an_env_file`). **`.env.example` does NOT exist** in the repo root (`ls -a`), so the "documents every setting" half is unmet. Known/accepted cause: user permission settings deny `.env*` (02-02-SUMMARY "Blocked / deferred"); its six tests were dropped. Makefile `up` guards on `[ -f .env.example ]` so nothing breaks without it |

**Score:** SC1-SC4 verified, SC5 partial (4 of 5 fully verified).

### Deferred Items

| # | Item | Addressed In | Evidence |
|---|------|--------------|----------|
| 1 | Written documentation of every setting (OPS-02, config half) | Phase 4 | DOC-01 / Phase 4 SC4: README "configuration" section. This delivers documentation, not the literal `.env.example` file; flagged for a human decision rather than silently treated as equivalent |

### Required Artifacts (PLAN must_haves)

| Artifact | Status | Details |
|----------|--------|---------|
| `src/wbmon/health.py` | VERIFIED | contains `os.replace(`; heartbeat_path/write/check/latest_run_info/run_healthcheck; wired from `cli.py` and `scheduler.py` |
| `src/wbmon/schedule.py` | VERIFIED | pure functions, `CATCH_UP_GRACE`; used by scheduler; `test_schedule_module_is_pure` |
| `src/wbmon/signals.py` | VERIFIED | `StopFlag`, `install_stop_handlers`; used by scheduler and collect |
| `src/wbmon/scheduler.py` | VERIFIED | `start_new_session=True`; `Scheduler` has no default `spawn` (tests cannot start real collect) |
| `src/wbmon/cli.py` | VERIFIED | `scheduler`, `healthcheck` subcommands, collect SIGTERM handler |
| `src/wbmon/orchestrator.py`, `models.py` | VERIFIED | `should_stop`, `terminated` error class |
| `Dockerfile`, `.dockerignore`, `compose.yaml`, `deploy/Caddyfile`, `Makefile`, `tests/data/compose-spec.json` | VERIFIED | all present, substantive, cross-checked by `test_packaging*.py` |
| `.env.example` | MISSING | see SC5; planned artifact in 02-02, deferred (permissions) |
| `.streamlit/` (referenced by `.dockerignore` allowlist, `working_dir: /app`) | ABSENT (expected) | Not in any Phase 2 `files_modified`; `COPY . /app` works without it; Phase 3 delivers it. Info only |
| `src/wbmon/ui/app.py` (compose `web` command) | ABSENT (expected) | Phase 3 on a separate branch; `compose.yaml` header documents the crash-loop until it lands |

### Key Link Verification

| From | To | Status | Details |
|------|----|--------|---------|
| scheduler.py -> reader.run_id_for_slot | `has_run_in_db` via `connect_ro` | WIRED | |
| scheduler.py -> health.write_heartbeat | `Scheduler.beat`, called in the child poll loop | WIRED | |
| scheduler.py -> `collect --trigger scheduler --slot` | `spawn_collect` argv | WIRED | matches CLI contract (`test_every_wbmon_service_command_parses_with_the_real_cli`) |
| cli.py -> run_scheduler / run_healthcheck | subcommand dispatch | WIRED | |
| cli.py -> install_stop_handlers -> orchestrator `should_stop` | collect branch | WIRED | |
| compose.yaml -> wbmon healthcheck | collector healthcheck | WIRED | `["CMD","wbmon","healthcheck","--max-age","180"]` |
| compose.yaml/Dockerfile -> `/data` volume + DB path pin | env + mounts | WIRED | |
| Dockerfile -> `load_bundled()` | build-time smoke | WIRED | |
| `.env.example` -> config.py | coverage test | NOT_WIRED | file missing (deferred) |

### Data-Flow Trace (Level 4)

Not applicable: no UI/data-rendering artifacts in this phase. Heartbeat data is produced from the injected clock and live scheduler state (not static); the healthcheck reads it from the file the scheduler writes (proved by the real-process e2e test).

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full offline suite | `.venv/bin/python -m pytest -q` | 785 passed, 1 skipped (docker not installed), 16 deselected (`slow`) in 6.9 s | PASS |
| Lint | `.venv/bin/python -m ruff check src tests` | All checks passed | PASS |
| Format | `ruff format --check src tests` | 55 files already formatted (root-level `ruff format --check .` also lists `.planning/*.md` code blocks - not source) | PASS |
| Healthcheck without a heartbeat | `WBMON_DB_PATH=<scratch> wbmon healthcheck` | `unhealthy: heartbeat file missing ... last_run=no_db`, exit 1 | PASS |
| Phase tests exist | `pytest --collect-only` on scheduler/schedule/process/e2e/terminate/packaging files | 128 tests collected | PASS |

Not run by design: `wbmon collect|probe|scheduler`, `make up/collect-once/scheduler`, any Docker command, any live request.

### Probe Execution

SKIPPED: no `scripts/*/tests/probe-*.sh` and no phase-declared probes.

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| SCHED-02 | 02-01, 02-03 | hourly :00 MSK subprocess, catch-up 45 min, no back-fill, no overlap | SATISFIED | SC1 above |
| SCHED-03 | 02-01, 02-03 | survives failed runs/restarts; heartbeat/latest-run signal for healthchecks | SATISFIED | SC2, SC3 above. ("and the UI" consumer is Phase 3; the signal itself exists) |
| OPS-01 | 02-02, 02-03 | one image, compose with init-db/collector/web, volume, restart, healthchecks, log rotation, MSK scheduling/UTC storage, tzdata | SATISFIED statically | SC4. Real `docker compose up` and the `web` service becoming healthy are Phase 3/4 dependent (human item 2) |
| OPS-02 | 02-02 | runs without Docker; documented `.env.example` for every setting | PARTIALLY SATISFIED / config documentation DEFERRED | Local no-Docker path verified; `.env.example` absent due to user permission rules; REQUIREMENTS.md correctly still shows OPS-02 as Pending |

Orphaned requirements: none. REQUIREMENTS.md maps exactly SCHED-02, SCHED-03, OPS-01, OPS-02 to Phase 2, and the union of PLAN frontmatter `requirements` (02-01: SCHED-02/03; 02-02: OPS-01/02; 02-03: SCHED-02/03, OPS-01) covers all four.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| (Phase 2 source, tests, Docker/Makefile/Caddy files) | - | grep for TODO/FIXME/TBD/XXX | none found | No debt markers; no stubs (`except Exception` in the scheduler loop is deliberate, logged, and tested) |
| `.planning/ROADMAP.md` | 98-102, 190 | Phase 2 plan list says "TBD" and progress table says "0/TBD Not started" while the phase header is `[x]` | Info | Stale bookkeeping only; update when closing the phase |
| `.planning/REQUIREMENTS.md` | 68 | OPS-01 ticked complete although no real `docker compose up` has run | Info | Acceptable per ROADMAP note (static validation now, real check in Phase 4); keep Phase 4 as the confirmation point |

### Human Verification Required

1. **Close the `.env.example` gap (decision).** Test: create `.env.example` by hand (agent is denied `.env*`) or explicitly accept the Phase 4 README configuration table in its place and amend the SC5/OPS-02 wording. Expected: every `WBMON_*` setting (names/defaults in `src/wbmon/config.py`) plus `WEB_PORT`, `WEB_BIND`, `SITE_ADDRESS`, `COMPOSE_PROFILES`, `PYTHON_IMAGE`, `PIP_INDEX_URL`, `CADDY_IMAGE` documented. Why human: permission-bound and a scope decision.
2. **Real compose bring-up** (host with Docker, after Phase 3 lands `ui/app.py`): `docker compose up -d --build`, `ps` shows collector/web healthy, `down` then `up -d` keeps history. Why human: no Docker here.
3. **Two-container WAL check** (writer in `collector`, reader in `web` on the shared rw volume). Why human: needs live containers.

### Gaps Summary

No code defects found. The scheduler, catch-up/no-back-fill logic, failure/SIGTERM/SIGKILL survival, heartbeat and healthcheck are implemented, wired and proved by fake-clock plus real-process tests (785 pass, ruff clean). The Docker packaging is complete and statically validated against the official compose schema with cross-checks against the real CLI and timing constants. The only unmet roadmap item is the literal `.env.example` file (SC5 / OPS-02), blocked by the user's own permission settings and partly covered later by the Phase 4 README; it needs a user decision rather than another code plan. Docker-dependent end-to-end checks are explicitly assigned to Phase 4.

---

_Verified: 2026-10-16T18:10:00Z_
_Verifier: Claude (gsd-verifier)_
