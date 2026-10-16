---
phase: 02-unattended-operation-packaging
plan: 02
subsystem: packaging
tags: [docker, compose, caddy, makefile, packaging, static-validation, compose-spec]

requires:
  - phase: 02-unattended-operation-packaging/01
    provides: "`wbmon scheduler`, `wbmon healthcheck --max-age`, heartbeat file next to the DB"
provides:
  - "Dockerfile: one non-root (uid/gid 10001) python:3.12-slim image for init-db / collector / web; build fails if the 70 bundled queries or Europe/Moscow tz data are missing"
  - "allowlist .dockerignore (pyproject.toml, src, .streamlit)"
  - "compose.yaml: init-db (one-shot), collector (scheduler, healthcheck, 90 s grace), web (Streamlit from /app), optional caddy under profile tls, named volume wbmon_data, json-file log rotation 10m x 3"
  - "deploy/Caddyfile reverse proxy to web:8501"
  - "Makefile: help install test lint init-db collect-once scheduler web up up-prebuilt down logs ps (no CONFIRM guard, down never removes volumes)"
  - "vendored compose-spec JSON schema (Apache-2.0, sha256 pinned) and tests/test_packaging.py (35 tests)"
affects: [phase-03, phase-04]

tech-stack:
  added: ["pyyaml>=6,<7 (dev)", "jsonschema>=4,<5 (dev)"]
  patterns:
    - "static packaging validation without Docker: official compose schema + negative control + topology assertions + Dockerfile instruction parser"
    - "compose `environment` pins WBMON_DB_PATH and TZ so no .env can move the DB off the volume"
    - "every compose variable and every WBMON_* setting has a built-in default; the stack runs with no env file"

key-files:
  created:
    - Dockerfile
    - .dockerignore
    - compose.yaml
    - deploy/Caddyfile
    - Makefile
    - tests/test_packaging.py
    - tests/data/compose-spec.json
  modified:
    - pyproject.toml
    - .gitignore

key-decisions:
  - "Web healthcheck uses the urllib one-liner against 127.0.0.1:8501/_stcore/health; collector uses `wbmon healthcheck --max-age 180`"
  - "web published as ${WEB_BIND:-0.0.0.0}:${WEB_PORT:-8501}:8501 (public by default; WEB_BIND=127.0.0.1 with the tls profile)"
  - "Makefile `up` / `up-prebuilt` seed .env from .env.example only when that file exists and .env does not"

patterns-established:
  - "tests resolve paths from factories.REPO_ROOT and run only `make help`, `make -n up`, `make -n down`"

requirements-completed: [OPS-01]

duration: 25min
completed: 2026-10-16
---

# Phase 2 Plan 02: Docker, compose and Makefile packaging Summary

**One non-root image and one `docker compose up -d` (init-db, collector with scheduler healthcheck, Streamlit web, optional Caddy `tls` profile) on the fixed-name volume `wbmon_data`, plus a Makefile for the no-Docker path, all validated offline against the official compose-spec schema.**

## Blocked / deferred

- **`.env.example` was NOT created.** The user's permission settings deny writes to `.env*` files, and the orchestrator decided not to bypass the rule. The intended content is ready at `scratchpad/env_example.txt`, with its six tests in `scratchpad/env_tests.py`. The scratchpad directory is `/private/tmp/claude-501/-Users-fvst-Desktop------------------/367724d7-4acd-475a-9a99-420e26b1a56b/scratchpad`. The user decides later whether to add them. The six tests were run against a copy of the file and passed.
- **OPS-02 is not marked complete.** Documentation of every setting will be covered by the README configuration table in Phase 4. The no-Docker Makefile path itself is implemented and tested.
- **Substitute for the missing file:** the stack runs with no env file at all. compose uses `env_file: required: false` and `${VAR:-default}` everywhere, `config.py` has a default for every `WBMON_*` name, and one test (`test_every_setting_has_a_default_so_the_stack_runs_without_an_env_file`) enforces that.
- **Makefile `up` / `up-prebuilt`** copy `.env.example` to `.env` only if the example exists and `.env` does not, so a fresh checkout works.

## Performance

- **Duration:** about 25 min
- **Tasks:** 3 of 3 executed; Task 3 deliverable `.env.example` deferred (see above)
- **Tests:** 754 before; 755 passed and 1 skipped after (the `docker compose config -q` test, because Docker is absent); ruff check and format clean

## Accomplishments

- **Dockerfile:**
  - `ARG PYTHON_IMAGE`, single `FROM`.
  - Group and user `app` 10001, `/data` owned by `app`.
  - `pip install ".[web]"` followed by smoke checks: `wbmon --help`, `load_bundled() == 70`, `ZoneInfo("Europe/Moscow")`.
  - `USER app` last, exec-form `CMD ["wbmon","scheduler"]`.
  - No `HEALTHCHECK`, no `# syntax=`, no `ADD`, no `:latest`.
- **compose.yaml:**
  - Validates with zero errors against the vendored schema, and a negative control (`restart_polcy`) is rejected.
  - Topology assertions cover the three roles, the pinned DB path, rw volume, log rotation, `service_completed_successfully`, `stop_grace_period 90s`, `working_dir /app`, the Streamlit command, and the caddy `tls` profile with mounts.
- **Cross-phase contract:** web `working_dir` = Dockerfile `WORKDIR` = `/app`, the build context is copied to `/app`, and `.dockerignore` re-includes `.streamlit`.
- **Makefile:** parsed and checked per target, with `make help` and `make -n up/down` run for real. Nothing else is executed, and no `.env` is created.

## Task Commits

1. **Task 1: Dockerfile + .dockerignore** - `f838d22` (feat)
2. **Task 2: compose.yaml, Caddyfile, schema, dev extras** - `ae485fd` (feat)
3. **Task 3: Makefile** - `049c2ec` (feat)
4. **Task 3 follow-up: stack runs without an env file** - `51cfc96` (feat)

## Decisions Made

See frontmatter. The PyPI legitimacy gate (D2-A4) passed before installing:
- PyYAML 6.0.3 lists `https://github.com/yaml/pyyaml` in `project_urls`.
- jsonschema 4.26.0 lists `https://github.com/python-jsonschema/jsonschema`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] `.gitignore` hid the vendored schema**
- **Found during:** Task 2
- **Issue:** the bare `data/` rule ignored `tests/data/compose-spec.json`.
- **Fix:** added `!tests/data/` beside `!src/wbmon/data/`.
- **Files modified:** .gitignore
- **Committed in:** ae485fd

### Scope changes directed by the coordinator

**2. `.env.example` and its six tests dropped** because of the permission rule (see Blocked / deferred). The following plan items were adjusted, not implemented as written:
- The Makefile `up` seeding step is conditional.
- The compose comment no longer claims that `make up` creates `.env`.
- One "runs with no env file" test replaces the six `.env.example` tests.

**3. [Process]** Each TDD task was committed once, tests and implementation together, not as separate RED and GREEN commits.

**Total deviations:** 1 auto-fix, 2 directed or process changes. No behaviour of the committed artifacts was weakened.

## Issues Encountered

- The schema hash matched the pinned value (`d61cc3df...b10c`), so there was no upstream drift.
- `.github/workflows/ci.yml` was not touched, because the plan does not list it. It still installs only `.[dev]`. `tests/test_packaging.py` needs only dev extras, so CI is fine. Phase 3's UI tests will need `.[web,dev]` there.

## Known Stubs

None. The `web` service points at `src/wbmon/ui/app.py`, which Phase 3 creates, so it restarts in a loop until the phases are merged (documented in compose.yaml, RESEARCH Pitfall 8).

## Threat Flags

None beyond the plan's threat model. WEB_BIND is documented in compose.yaml and tested through the port mapping, but its explanation in `.env.example` is deferred.

## Next Phase Readiness

- Phase 3 merge needs `src/wbmon/ui/app.py` and `.streamlit/config.toml`; the image already copies `.streamlit`.
- Phase 4:
  - Run the real `docker compose up -d` on the host.
  - Check the volume's first-mount ownership and that WAL works across containers.
  - Check registry reachability and use `make up-prebuilt` if needed.
  - Check `docker compose version` >= 2.24.
  - Write the README configuration table covering every setting (OPS-02).
- No Docker build, no live Wildberries/Google request and no `wbmon collect` or scheduler run happened.

## Self-Check: PASSED

- Files exist: Dockerfile, .dockerignore, compose.yaml, deploy/Caddyfile, Makefile, tests/test_packaging.py, tests/data/compose-spec.json
- Commits exist: f838d22, ae485fd, 049c2ec, 51cfc96
- Full offline suite: 755 passed, 1 skipped; ruff clean
