---
phase: 02-unattended-operation-packaging
fixed_at: 2026-10-16T00:00:00Z
review_path: .planning/phases/02-unattended-operation-packaging/02-REVIEW.md
iteration: 1
findings_in_scope: 11
fixed: 10
skipped: 1
status: partial
---

# Phase 2: Code Review Fix Report

**Fixed at:** 2026-10-16
**Source review:** `.planning/phases/02-unattended-operation-packaging/02-REVIEW.md`
**Iteration:** 1

**Summary:**
- Findings in scope: 11 (WR-01..WR-05, IN-01..IN-06; scope widened to Info by the caller)
- Fixed: 10 (WR-01..WR-05, IN-01..IN-05)
- Skipped: 1 (IN-06 is counted here because only its trivial items were applied; they are listed under Fixed Issues)
- Final gate: full suite 818 passed, 1 skipped (Docker not installed); `ruff check` and `ruff format --check` clean.
- Worked in the main checkout (no extra worktree), as instructed; `wb-serp-monitor-ui` was not touched.

## Fixed Issues

### WR-01: SIGTERM cannot interrupt a sleeping or in-flight query
**Files modified:** `src/wbmon/signals.py`, `src/wbmon/wb_client.py`, `src/wbmon/collector.py`, `src/wbmon/orchestrator.py`, `src/wbmon/cli.py`, `src/wbmon/scheduler.py`, `compose.yaml`, `tests/factories.py`, `tests/test_graceful_stop_waits.py` (new), `tests/test_packaging_cross.py`, `tests/test_scheduler.py`
**Commit:** 3f88404 (requires human verification: timing logic)
**Applied fix:**
- `interruptible_sleep` (signals.py) naps in 0.5 s steps and returns early on a stop. It counts down the remaining time instead of reading a clock, so tests with a fake sleep stay deterministic. `collect` uses it when no test `_sleep` is injected.
- `WbClient` takes `should_stop`. With a stop pending, `fetch_page` raises `StopRequested` before any request (also after the pacing wait). The back-off and 429/Retry-After waits end early and the last failed fetch is returned without a retry.
- `collect_query` polls `should_stop` before every page. The query ends as `error`/`terminated` (page 1) or `partial`/`terminated` (later pages, rows of earlier pages kept, `is_complete=False`). `terminated` was already in `ERROR_CLASSES`, so no migration.
- Grace sizing, derived from constants (documented in `scheduler.py` and `compose.yaml`, and enforced by tests):
  - `wb_client.MAX_ATTEMPT_SECONDS = connect 10 + 3 x 30 = 100 s` (connect, write, headers, one body read; the 60 s body deadline fires right after).
  - `STOP_GRACE_S = 100 + 20 margin = 120 s` (was 60).
  - compose `stop_grace_period: 150s` (was 90s).
  - Ordering: compose 150 > scheduler 120 > one attempt 100.
- Tests (no network): interruptible sleep, stop in pacing/back-off/429 waits, stop between pages, stop before page 1, grace ordering. `test_scheduler.py` and `test_packaging_cross.py` were updated for the new constants.
- Note: a request already on the wire is not aborted. The bound is the worst-case single attempt, honestly sized rather than shortened.

### WR-02: Public web port with the tls profile
**Files modified:** `Makefile`, `compose.yaml`, `tests/test_packaging.py`
**Commit:** 090fd21
**Applied fix:**
- The default `WEB_BIND=0.0.0.0` is kept (D2-A3).
- New target `make up-tls` runs `COMPOSE_PROFILES=tls WEB_BIND=127.0.0.1 docker compose up -d --build`.
- A prominent "PUBLIC BY DEFAULT" comment sits next to the port mapping, and the compose header and Makefile header were updated.
- Static tests, plus a `make -n up-tls` dry-run test.

### WR-03: Makefile sources a compose-format `.env` as shell
**Files modified:** `Makefile`, `tests/test_packaging.py`
**Commit:** 8a523eb
**Applied fix:**
- `ENVLOAD := set -a; if [ -f .env ]; then . ./.env || exit 1; fi; set +a;`. A sourcing error now fails the recipe.
- Makefile comment: values with shell metacharacters must be single-quoted, and sourcing executes the file.
- Tests run the extracted `ENVLOAD` in real bash for three cases (missing file, good file, broken file). The broken file exits non-zero and the following command never starts.

### WR-04: `run_slot` gaps
**Files modified:** `src/wbmon/scheduler.py`, `tests/test_scheduler.py`
**Commit:** ff7240f (requires human verification: supervision logic)
**Applied fix:**
- `run_slot` returns without spawning and logs `run_skipped reason=shutdown` when a stop is already requested.
- The poll loop is now `_supervise`. Any unexpected exception goes through `_reap`: SIGTERM, a bounded wait (5 s), then SIGKILL. The exception is re-raised and `run_forever` still logs `run_failed reason=exception`.
- After SIGKILL the wait is bounded by `kill_wait_s` (30 s). Then `child_unkillable` and `run_failed reason=child_unkillable` are logged and the loop continues. The healthcheck can then see the stale slot instead of a fresh heartbeat on a frozen loop.
- Fake-clock tests: late stop, exception with a cooperative child, exception with a SIGTERM-ignoring child, unkillable child on timeout (next slot still runs), unkillable child on stop.

### WR-05: Container hardening
**Files modified:** `compose.yaml`, `tests/test_packaging.py`
**Commit:** 2545367
**Applied fix:**
- `security_opt: ["no-new-privileges:true"]` and `cap_drop: ["ALL"]` on the shared anchor (init-db, collector, web) and on caddy.
- caddy additionally has `cap_add: ["NET_BIND_SERVICE"]`.
- `read_only` is not set (see the documented option below). Static tests check this.

### IN-01: One `build:` block
**Files modified:** `compose.yaml`, `tests/test_packaging.py`
**Commit:** 5f7f7c4
**Applied fix:**
- `build:` moved from the shared anchor to `init-db` only. collector and web reuse `image: wb-serp-monitor:local` and depend on init-db.
- The official compose-spec schema test still passes.
- Caveat: the first `up` on a host with no image yet could not be checked without Docker; see Notes.

### IN-02: `.dockerignore` and `PIP_INDEX_URL`
**Files modified:** `.dockerignore`, `Dockerfile`, `compose.yaml` (comment, in the IN-01 commit), `tests/test_packaging.py`
**Commit:** b13aa2f
**Applied fix:**
- `.streamlit/secrets.toml` is excluded while `.streamlit/config.toml` stays in the build context.
- The test uses a small evaluator that mimics Docker's last-matching-rule semantics.
- A comment in the Dockerfile and in compose notes that build ARGs are visible in `docker history`.

### IN-03: Caddy compression
**Files modified:** `deploy/Caddyfile`, `tests/test_packaging.py`
**Commit:** 2b12ff3
**Applied fix:** `encode zstd gzip` inside the site block.

### IN-04: `init: true`
**Files modified:** `compose.yaml`, `tests/test_packaging.py`
**Commit:** a87385f
**Applied fix:** `init: true` on collector and web, with a comment on why PID 1 needs it.

### IN-05: Dead `has_run` parameter
**Files modified:** `src/wbmon/scheduler.py`
**Commit:** 782dc7b
**Applied fix:**
- The parameter was not removed, because `tests/test_schedule.py::test_should_catch_up_false_when_the_slot_already_ran` depends on its semantics.
- The reviewer's alternative was applied instead: the scheduler passes the real `has_run(slot)` result and derives the `slot_exists` log from it.
- Behaviour is identical, and the duplicated branch and the hard-coded `False` are gone.

### IN-06 (applied part): Packaging and Makefile nits
**Files modified:** `compose.yaml`, `Makefile`, `tests/test_packaging.py`
**Commit:** 9dea0ab
**Applied fix:**
- The web healthcheck uses `build_opener(ProxyHandler({}))`, so `HTTP_PROXY` from `.env` cannot break the loopback probe.
- A test runs the real probe code against a local HTTP server with a dead `HTTP_PROXY` set. The old form of the probe was confirmed to fail in the same setup.
- `make install` checks for Python >= 3.11 first.

## Skipped Issues

### IN-06 (remainder): duplicate `working_dir` and healthcheck DB read cap
**File:** `compose.yaml:66`, `src/wbmon/health.py`
**Reason:**
- `working_dir: /app` is pinned by the Phase 3 cross-phase contract test (`test_streamlit_config_reaches_the_container_working_directory`). Removing it is a deliberate non-trivial contract change.
- The healthcheck DB read cap is not trivial.
**Original issue:** Duplicate `working_dir`; the healthcheck opens the database for an informational `last_run` and can add up to 5 s of busy-wait to a 10 s timeout.

## Notes for the developer

- **IN-01 deployment risk (not verifiable here, no Docker).** With `build:` only on init-db, a first `docker compose up -d --build` on a clean host relies on Compose building the shared tag before it tries to resolve `wb-serp-monitor:local` for collector and web. If the first `make up` fails with "pull access denied for wb-serp-monitor", run `docker compose build init-db` once before `make up`, or revert commit 5f7f7c4. Smoke-test this on the deploy host.
- **Optional hardening not applied (WR-05):** `read_only: true` plus `tmpfs: ["/tmp", "/home/app"]` on web and collector. Only `/data` (volume) and Streamlit's home need to be writable. It could not be verified without Docker, so it is left as a documented comment in `compose.yaml`. Try it in Phase 4 on the real host.
- **Untouched on purpose:** `src/wbmon/ui/**`, `.streamlit/**`, `tests/ui/**` and the sibling worktree. `.env.example` was neither read nor created. The pre-existing untracked `.planning/phases/04-publish-deliverables/04-01-PLAN.md` was left alone.
- **Items needing human verification:** the WR-01 timing bound (the sizing of 100/120/150 s is reasoned from httpx timeouts, not measured) and the WR-04 supervision paths (fake clock only).

---

_Fixed: 2026-10-16_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
